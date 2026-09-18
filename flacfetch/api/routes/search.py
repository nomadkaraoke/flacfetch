"""
Search endpoint for flacfetch HTTP API.
"""
import asyncio
import logging
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException

from ..auth import verify_api_key
from ..models import ProviderSearchStats, SearchRequest, SearchResponse, SearchResultItem
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
_inflight_searches: Dict[str, "asyncio.Task"] = {}


def _inflight_key(artist: str, title: str, exhaustive: bool) -> str:
    normalized_artist = SearchCacheService._normalize_part(artist)
    normalized_title = SearchCacheService._normalize_part(title)
    return f"{normalized_artist}|||{normalized_title}|||{exhaustive}"


def _configure_and_search(fetch_manager, artist: str, title: str, exhaustive: bool):
    """Configure providers and run the sweep. Runs inside _search_executor so the
    provider mutation and the search are atomic with respect to other sweeps."""
    for provider in fetch_manager.providers:
        # Check if provider has early termination settings (RED/OPS)
        if hasattr(provider, 'early_termination'):
            provider.early_termination = not exhaustive
            if exhaustive:
                # Also increase search limit for exhaustive mode
                if hasattr(provider, 'search_limit'):
                    provider.search_limit = 20

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
        # Get quality info
        quality_str = str(release.quality) if release.quality else ""
        quality_data = None
        is_lossless = False
        if release.quality:
            quality_data = {
                "format": release.quality.format.name,
                "bit_depth": release.quality.bit_depth,
                "sample_rate": release.quality.sample_rate,
                "bitrate": release.quality.bitrate,
                "media": release.quality.media.name,
            }
            # Use is_true_lossless - Spotify is transcoded from lossy source
            is_lossless = release.quality.is_true_lossless(release.source_name)

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

