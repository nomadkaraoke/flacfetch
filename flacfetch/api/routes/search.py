"""
Search endpoint for flacfetch HTTP API.
"""
import asyncio
import logging
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from flacfetch.core.models import MediaSource

from ..auth import verify_api_key
from ..models import (
    AlbumSearchRequest,
    AlbumSearchResponse,
    AlbumSearchResultItem,
    AlbumTrackFile,
    ProviderSearchStats,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
)
from ..services import get_download_manager, get_search_cache_service
from ..services.search_cache import SearchCacheService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["search"])

# Provider sweeps are synchronous and can take 40s+. Running them directly in
# the async handler blocks the whole event loop, which stalls health checks,
# cache-hit searches, and download status polls behind every uncached search
# (2026-09-18 incident: a queued search blew the caller's 60s timeout).
# A single-thread executor keeps sweeps serialized (the provider instances are
# shared and mutated per-search, so they must not run concurrently) while the
# event loop stays responsive.
_search_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="provider-search")

# Identical searches that arrive while a sweep is in flight await its result
# instead of queueing another full sweep behind it.
_inflight_searches: Dict[tuple, "asyncio.Task"] = {}

# Group-fetch limit for exhaustive sweeps (normal sweeps use each provider's own default)
EXHAUSTIVE_SEARCH_LIMIT = 20


def _quality_fields(release) -> tuple[str, Optional[dict], bool]:
    """(quality string, quality_data dict, is_lossless) for a release."""
    if not release.quality:
        return "", None, False
    quality_data = {
        "format": release.quality.format.name,
        "bit_depth": release.quality.bit_depth,
        "sample_rate": release.quality.sample_rate,
        "bitrate": release.quality.bitrate,
        "media": release.quality.media.name,
    }
    # Use is_true_lossless - Spotify is transcoded from lossy source
    return str(release.quality), quality_data, release.quality.is_true_lossless(release.source_name)


def _inflight_key(artist: str, title: str, exhaustive: bool) -> tuple:
    normalized_artist = SearchCacheService._normalize_part(artist)
    normalized_title = SearchCacheService._normalize_part(title)
    return (normalized_artist, normalized_title, exhaustive)


def _configure_and_search(fetch_manager, artist: str, title: str, exhaustive: bool):
    """Configure providers and run the sweep. Runs inside _search_executor so the
    provider mutation and the search are atomic with respect to other sweeps."""
    for provider in fetch_manager.providers:
        # Check if provider has early termination settings (RED/OPS)
        if hasattr(provider, 'early_termination'):
            provider.early_termination = not exhaustive
            if hasattr(provider, 'search_limit'):
                # Remember the provider's own default so a non-exhaustive sweep
                # doesn't inherit a previous exhaustive sweep's expanded limit
                if not hasattr(provider, '_default_search_limit'):
                    provider._default_search_limit = provider.search_limit
                provider.search_limit = (
                    EXHAUSTIVE_SEARCH_LIMIT if exhaustive else provider._default_search_limit
                )

    from flacfetch.core.models import TrackQuery
    query = TrackQuery(artist=artist, title=title)
    return fetch_manager.search(query)


async def _search_and_cache(fetch_manager, cache_service, artist: str, title: str, exhaustive: bool):
    """Run a provider sweep off the event loop and cache the results."""
    loop = asyncio.get_running_loop()
    releases = await loop.run_in_executor(
        _search_executor, _configure_and_search, fetch_manager, artist, title, exhaustive
    )

    # Cache results (fire-and-forget, best-effort)
    # Always cache fresh search results, including exhaustive searches
    if releases:
        asyncio.create_task(cache_service.cache_search_results(artist, title, releases))

    return releases


@router.post("/search", response_model=SearchResponse)
async def search_audio(
    request: SearchRequest,
    api_key: str = Depends(verify_api_key),
) -> SearchResponse:
    """
    Search for audio matching artist and title.

    Returns a list of results from all configured providers (RED, OPS, YouTube).
    Results are sorted by quality with lossless sources prioritized.

    The search_id in the response can be used with POST /download to download a result.

    Set `exhaustive=true` for a comprehensive search that disables early termination
    and searches more groups (slower but returns more results).
    """
    manager = get_download_manager()
    cache_service = get_search_cache_service()
    fetch_manager = manager._get_fetch_manager()

    logger.info(f"Searching for: {request.artist} - {request.title} (exhaustive={request.exhaustive})")

    # Check cache first (only for non-exhaustive searches)
    releases = None
    from_cache = False
    if not request.exhaustive:
        cached_results = await cache_service.get_cached_search(request.artist, request.title)
        if cached_results is not None:
            logger.info(f"Cache HIT for: {request.artist} - {request.title}")
            releases = cached_results
            from_cache = True

    if releases is None:
        if not from_cache:
            logger.info(f"Cache MISS for: {request.artist} - {request.title}")

        key = _inflight_key(request.artist, request.title, request.exhaustive)
        task = _inflight_searches.get(key)
        if task is not None:
            logger.info(f"Coalescing with in-flight search for: {request.artist} - {request.title}")
        else:
            task = asyncio.create_task(
                _search_and_cache(
                    fetch_manager, cache_service, request.artist, request.title, request.exhaustive
                )
            )
            _inflight_searches[key] = task
            task.add_done_callback(lambda t: _inflight_searches.pop(key, None))
            # Retrieve the exception so an errored sweep with no remaining
            # waiters doesn't emit "exception was never retrieved" warnings.
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

        try:
            # shield() so one caller disconnecting doesn't cancel the sweep
            # other coalesced callers are awaiting.
            releases = await asyncio.shield(task)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Search failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Search failed: {e}")

    if not releases:
        raise HTTPException(status_code=404, detail=f"No results found for: {request.artist} - {request.title}")

    logger.info(f"Found {len(releases)} results")

    # Generate search ID and cache results
    search_id = f"search_{uuid.uuid4().hex[:12]}"
    manager.cache_search(search_id, request.artist, request.title, releases)

    # Convert to response format
    results: List[SearchResultItem] = []
    for idx, release in enumerate(releases):
        quality_str, quality_data, is_lossless = _quality_fields(release)

        results.append(SearchResultItem(
            index=idx,
            title=release.title,
            artist=release.artist,
            provider=release.source_name,
            quality=quality_str,
            quality_data=quality_data,
            seeders=release.seeders,
            size_bytes=release.size_bytes,
            target_file=release.target_file,
            target_file_size=release.target_file_size,
            year=release.year,
            label=release.label,
            edition_info=release.edition_info,
            release_type=release.release_type,
            channel=release.channel,
            view_count=release.view_count,
            duration_seconds=release.duration_seconds,
            match_score=release.match_score,
            formatted_size=release.formatted_size,
            formatted_duration=release.formatted_duration,
            is_lossless=is_lossless,
            source_id=release.source_id,
        ))

    # Build provider stats
    provider_counts = Counter(r.provider for r in results)
    configured_providers = [p.name for p in fetch_manager.providers]
    provider_stats = [
        ProviderSearchStats(
            provider=provider,
            results_count=provider_counts.get(provider, 0),
            searched=True,
        )
        for provider in configured_providers
    ]

    return SearchResponse(
        search_id=search_id,
        artist=request.artist,
        title=request.title,
        results=results,
        results_count=len(results),
        provider_stats=provider_stats,
    )



# =============================================================================
# Album search
# =============================================================================

# Album searches in flight, keyed like _inflight_key but per album + tracklist.
_inflight_album_searches: Dict[tuple, "asyncio.Task"] = {}


def _album_inflight_key(request: AlbumSearchRequest) -> tuple:
    norm = SearchCacheService._normalize_part
    tracks = tuple((t.key, norm(t.title), t.position) for t in request.tracks)
    return (norm(request.artist), norm(request.album), tracks)


# Release types that are the album as released (vs a live/compilation/remix
# group whose name happens to contain the album's).
_STUDIO_RELEASE_TYPES = {"Album", "EP", "Single", "Soundtrack"}


def _album_sort_key(candidate):
    """Most tracks covered, then studio release types, then non-vinyl, then the
    strongest file matches, then seeders."""
    release = candidate.release
    is_vinyl = bool(release.quality and release.quality.media == MediaSource.VINYL)
    not_studio = release.release_type not in _STUDIO_RELEASE_TYPES
    match_total = sum(f.match_score for f in candidate.track_files.values())
    return (-candidate.matched_count, not_studio, is_vinyl, -round(match_total, 3), -(release.seeders or 0))


class AlbumSearchProvidersFailed(Exception):
    """Every provider that supports album search raised (vs. found nothing)."""


def _run_album_search(fetch_manager, artist: str, album: str, tracks: list) -> list:
    """Album search on every provider that supports it (RED/OPS). Runs inside
    _search_executor so it's serialized with track sweeps on the shared providers."""
    candidates = []
    attempted = failed = 0
    for provider in fetch_manager.providers:
        search_album = getattr(provider, "search_album", None)
        if search_album is None:
            continue
        attempted += 1
        try:
            candidates.extend(search_album(artist, album, tracks))
        except Exception as e:  # noqa: BLE001 - one tracker failing must not sink the other
            failed += 1
            logger.error(f"Album search failed on {provider.name}: {e}", exc_info=True)
    if attempted and failed == attempted:
        raise AlbumSearchProvidersFailed(f"all {attempted} album search providers failed")
    candidates.sort(key=_album_sort_key)
    return candidates


@router.post("/search/album", response_model=AlbumSearchResponse)
async def search_album(
    request: AlbumSearchRequest,
    api_key: str = Depends(verify_api_key),
) -> AlbumSearchResponse:
    """
    Find lossless torrents of one album and map each requested track to its file.

    One tracker browse by artist + album name (RED and OPS) instead of a search per
    track. Each result is a torrent with ``track_files`` ({track key: target_file});
    download a track with POST /download-by-id using the torrent's source_id and that
    target_file. Results are ordered by how many requested tracks they cover, then
    non-vinyl, then seeders. 404 when no torrent covers any requested track.
    """
    from flacfetch.core.album_match import AlbumTrack

    manager = get_download_manager()
    fetch_manager = manager._get_fetch_manager()
    tracks = [AlbumTrack(key=t.key, title=t.title, position=t.position) for t in request.tracks]

    logger.info(f"Album search: {request.artist} - {request.album} ({len(tracks)} tracks)")

    key = _album_inflight_key(request)
    task = _inflight_album_searches.get(key)
    if task is None:
        loop = asyncio.get_running_loop()
        task = asyncio.ensure_future(loop.run_in_executor(
            _search_executor, _run_album_search, fetch_manager, request.artist, request.album, tracks
        ))
        _inflight_album_searches[key] = task
        task.add_done_callback(lambda t: _inflight_album_searches.pop(key, None))
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
    else:
        logger.info(f"Coalescing with in-flight album search: {request.artist} - {request.album}")

    try:
        candidates = await asyncio.shield(task)
    except asyncio.CancelledError:
        raise
    except AlbumSearchProvidersFailed as e:
        # Distinct from 404: the trackers are failing, not the album missing.
        raise HTTPException(status_code=502, detail=f"Album search failed: {e}")
    except Exception as e:
        logger.error(f"Album search failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Album search failed: {e}")

    if not candidates:
        raise HTTPException(
            status_code=404, detail=f"No album torrents found for: {request.artist} - {request.album}"
        )

    results: List[AlbumSearchResultItem] = []
    for candidate in candidates:
        release = candidate.release
        quality_str, quality_data, is_lossless = _quality_fields(release)
        results.append(AlbumSearchResultItem(
            title=release.title,
            artist=release.artist,
            provider=release.source_name,
            source_id=release.source_id,
            quality=quality_str,
            quality_data=quality_data,
            is_lossless=is_lossless,
            seeders=release.seeders,
            size_bytes=release.size_bytes,
            formatted_size=release.formatted_size,
            year=release.year,
            label=release.label,
            edition_info=release.edition_info,
            release_type=release.release_type,
            matched_count=candidate.matched_count,
            track_files={
                k: AlbumTrackFile(
                    target_file=f.target_file, target_file_size=f.target_file_size, match_score=f.match_score
                )
                for k, f in candidate.track_files.items()
            },
        ))

    logger.info(
        f"Album search found {len(results)} torrents; best covers "
        f"{results[0].matched_count}/{len(tracks)} tracks"
    )
    return AlbumSearchResponse(
        artist=request.artist,
        album=request.album,
        tracks_requested=len(tracks),
        results=results,
        results_count=len(results),
    )
