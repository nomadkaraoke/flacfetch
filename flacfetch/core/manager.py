from typing import Optional

from .interfaces import Downloader, InteractionHandler, Provider
from .log import get_logger
from .models import AudioFormat, MediaSource, Quality, Release, TrackQuery

logger = get_logger("FetchManager")

class FetchManager:
    def __init__(self):
        self.providers: list[Provider] = []
        self._downloader_map: dict[str, Downloader] = {}
        self._default_downloader: Optional[Downloader] = None
        self._provider_priority: Optional[list[str]] = None
        self._use_fallback_search: bool = True  # Search lower priority providers if higher ones fail

    def add_provider(self, provider: Provider):
        self.providers.append(provider)

    def set_provider_priority(self, priority_list: list[str]):
        """Set provider search priority by name.

        Args:
            priority_list: List of provider names in priority order (e.g., ['RED', 'OPS', 'YouTube'])
        """
        self._provider_priority = priority_list
        logger.info(f"Provider priority set to: {' > '.join(priority_list)}")

    def enable_fallback_search(self, enabled: bool = True):
        """Enable/disable searching lower priority providers if higher ones return no results.

        Args:
            enabled: If True, search lower priority providers when higher ones fail.
                    If False, only search the highest priority provider.
        """
        self._use_fallback_search = enabled

    def register_downloader(self, source_name: str, downloader: Downloader):
        self._downloader_map[source_name] = downloader

    def set_default_downloader(self, downloader: Downloader):
        self._default_downloader = downloader

    def search(self, query: TrackQuery) -> list[Release]:
        all_releases = []

        # Get providers in priority order
        providers = self._get_ordered_providers()

        for _idx, provider in enumerate(providers):
            try:
                logger.info(f"Searching {provider.name} for '{query.artist} - {query.title}'...")
                results = provider.search(query)
                logger.info(f"Found {len(results)} results from {provider.name}")

                if results:
                    all_releases.extend(results)
                    # If we found results and fallback is disabled, stop searching
                    if not self._use_fallback_search:
                        logger.info(f"Found results from {provider.name}, skipping lower priority providers")
                        break
                else:
                    # No results from this provider
                    if self._use_fallback_search:
                        logger.info(f"No results from {provider.name}, trying next provider...")
                    else:
                        # Fallback disabled and no results - stop here
                        logger.info(f"No results from {provider.name} and fallback disabled, stopping search")
                        break

            except Exception as e:
                logger.error(f"Error searching {provider.name}: {e}")
                # Continue to next provider on error if fallback enabled
                if self._use_fallback_search:
                    logger.info(f"Provider {provider.name} failed, trying next provider...")
                else:
                    logger.info(f"Provider {provider.name} failed and fallback disabled, stopping search")
                    break

        # Sort results by quality and relevance
        if all_releases:
            all_releases = self._sort_releases(all_releases, query)

        return all_releases

    def _get_ordered_providers(self) -> list[Provider]:
        """Get providers ordered by priority (if set), otherwise in registration order."""
        if not self._provider_priority:
            return self.providers

        # Build a map of provider name to provider
        provider_map = {p.name: p for p in self.providers}

        # Order providers by priority list
        ordered = []
        for name in self._provider_priority:
            if name in provider_map:
                ordered.append(provider_map[name])

        # Add any providers not in priority list at the end
        for provider in self.providers:
            if provider not in ordered:
                ordered.append(provider)

        return ordered

    def _sort_releases(self, releases: list[Release], query: Optional[TrackQuery] = None) -> list[Release]:
        """
        Sort releases by quality and relevance.

        Priority order:
        1. Lossless over lossy
        2. Artist match (exact match with searched artist)
        3. Release type (Album > Single > EP > Compilation)
        4. Seeders/availability (higher is better)
        5. Quality details (24bit > 16bit, CD/WEB > VINYL)
        6. YouTube-specific (official channels, Topic releases)
        7. Year (prefer original releases)
        """
        searched_artist = query.artist.lower() if query and query.artist else None

        def quality_tier(r: Release) -> int:
            """
            Quality tier score - higher is better:
            - True lossless (torrent FLAC from CD/WEB): 100
            - Spotify (320kbps Vorbis transcoded to FLAC): 50
            - YouTube/other lossy: 0
            """
            if r.quality and r.quality.format:
                fmt = r.quality.format.name
                if fmt in ("FLAC", "WAV", "ALAC", "APE"):
                    # Check if true lossless or Spotify transcoded
                    if r.source_name.lower() == "spotify":
                        return 50  # Spotify: better than YouTube, worse than true lossless
                    return 100  # True lossless
            return 0  # Lossy

        def artist_match_score(r: Release) -> int:
            """
            Prioritize releases where the artist matches the searched artist.
            Compilations often have a different artist (VA or first artist).
            """
            if not searched_artist or not r.artist:
                return 0

            release_artist = r.artist.lower()

            # Exact match
            if release_artist == searched_artist:
                return 100
            # Searched artist in release artist
            if searched_artist in release_artist:
                return 80
            # Release artist in searched artist
            if release_artist in searched_artist:
                return 60
            # No match - this is likely a compilation appearance
            return 0

        def release_type_score(r: Release) -> int:
            """Albums > Singles > EPs > Compilations."""
            if not r.release_type:
                return 0
            priority = {
                "Album": 100,
                "Single": 80,
                "EP": 70,
                "Soundtrack": 60,
                "Live album": 50,
                "Anthology": 30,
                "Compilation": 20,
                "Remix": 10,
                "Bootleg": 5,
                "Demo": 5,
            }
            return priority.get(r.release_type, 0)

        def seeder_score(r: Release) -> int:
            """More seeders = more reliable download."""
            if r.source_name == "YouTube":
                # YouTube doesn't have seeders, use view count bucketed
                views = r.view_count or 0
                if views > 10_000_000:
                    return 100
                elif views > 1_000_000:
                    return 80
                elif views > 100_000:
                    return 60
                elif views > 10_000:
                    return 40
                return 20

            if r.source_name == "Spotify":
                # Use popularity (stored in view_count, scaled by 10000)
                popularity = (r.view_count or 0) // 10000  # Unscale
                if popularity >= 80:
                    return 95  # Very popular
                elif popularity >= 60:
                    return 80
                elif popularity >= 40:
                    return 60
                elif popularity >= 20:
                    return 40
                return 20

            # Torrent seeders - scale logarithmically
            seeders = r.seeders or 0
            if seeders >= 100:
                return 100
            elif seeders >= 50:
                return 90
            elif seeders >= 20:
                return 80
            elif seeders >= 10:
                return 70
            elif seeders >= 5:
                return 50
            elif seeders >= 1:
                return 30
            return 0

        def quality_detail_score(r: Release) -> int:
            """24bit > 16bit, prefer CD/WEB over VINYL for consistency."""
            score = 0
            if r.quality:
                # Bit depth
                if r.quality.bit_depth:
                    if r.quality.bit_depth >= 24:
                        score += 20
                    elif r.quality.bit_depth >= 16:
                        score += 10
                # Media type
                if r.quality.media:
                    media_name = r.quality.media.name if hasattr(r.quality.media, 'name') else str(r.quality.media)
                    media_priority = {
                        "WEB": 15,  # Consistent quality
                        "CD": 14,
                        "SACD": 13,
                        "DVD": 12,
                        "BLURAY": 11,
                        "VINYL": 5,  # Variable quality
                        "OTHER": 0,
                    }
                    score += media_priority.get(media_name, 0)
            return score

        def youtube_score(r: Release) -> int:
            """Score YouTube results by official-ness."""
            if r.source_name != "YouTube":
                return 0
            score = 0

            if r.channel:
                # Use searched_artist if available, otherwise fall back to release's artist
                artist_to_match = searched_artist or (r.artist.lower() if r.artist else None)

                # Channel matching
                if artist_to_match:
                    channel_lower = r.channel.lower()
                    if channel_lower == artist_to_match:
                        score += 100  # Exact match (likely official channel)
                    elif artist_to_match in channel_lower:
                        score += 30  # Partial match

                # Official channel indicators (independent of artist match)
                if " - Topic" in r.channel:
                    score += 40  # Auto-generated official
                if "VEVO" in r.channel.upper():
                    score += 35

            # Title keywords
            if r.title:
                title_lower = r.title.lower()
                if "official audio" in title_lower:
                    score += 20
                elif "official video" in title_lower:
                    score += 15
                elif "official" in title_lower:
                    score += 10
                elif "lyric" in title_lower:
                    score += 5

            return score

        def year_score(r: Release) -> int:
            """Prefer original/older releases for albums, newer for YouTube."""
            if not r.year:
                return 0
            if r.source_name == "YouTube":
                # Newer is better for YouTube (better quality uploads)
                return min(r.year - 2000, 30)  # Cap at 30
            # For albums, prefer original year (lower year = older = original)
            # But not too old (pre-1980 might be remaster issues)
            if r.year >= 1980:
                return 50 - (r.year - 1980)  # Older within reason
            return 0

        return sorted(releases, key=lambda r: (
            quality_tier(r),          # 1. True lossless > Spotify > YouTube
            artist_match_score(r),    # 2. Artist matches searched artist
            release_type_score(r),    # 3. Album > Single > Compilation
            seeder_score(r),          # 4. More seeders = more reliable
            quality_detail_score(r),  # 5. 24bit > 16bit, CD/WEB > VINYL
            youtube_score(r),         # 6. Official YouTube channels
            year_score(r),            # 7. Original releases preferred
            r.match_score or 0,       # 8. Provider-calculated match score (tie-breaker)
        ), reverse=True)

    def select_best(self, releases: list[Release]) -> Optional[Release]:
        if not releases:
            return None
        sorted_releases = self._sort_releases(releases)
        return sorted_releases[0]

    def select_interactive(self, releases: list[Release], handler: InteractionHandler) -> Optional[Release]:
        if not releases:
            return None
        sorted_releases = self._sort_releases(releases)
        return handler.select_release(sorted_releases)

    def download(self, release: Release, output_path: str, output_filename: Optional[str] = None) -> str:
        downloader = self._downloader_map.get(release.source_name, self._default_downloader)
        if not downloader:
            msg = f"No downloader registered for source: {release.source_name}"
            logger.error(msg)
            raise ValueError(msg)

        provider = next((p for p in self.providers if p.name == release.source_name), None)

        if provider:
            if not release.target_file and release.track_pattern:
                logger.info(f"Resolving target file for {release.title}...")
                provider.populate_details(release)

        if provider:
            logger.info(f"Fetching metadata/artifact for {release.title} from {provider.name}...")
            artifact = provider.fetch_artifact(release)
            if artifact:
                import os
                import tempfile
                fd, path = tempfile.mkstemp(suffix=".torrent")
                with os.fdopen(fd, 'wb') as tmp:
                    tmp.write(artifact)
                # Make file readable by other users (Transmission daemon runs as different user)
                os.chmod(path, 0o644)

                logger.debug(f"Saved temporary torrent file to {path}")
                release.download_url = path
            elif provider.name == "RED":
                 # If we failed to fetch the artifact but still proceed, we likely passed a URL to the downloader
                 # The downloader expects a local path or magnet.
                 # If download_url is http..., TorrentDownloader will fail.
                 if release.download_url and release.download_url.startswith("http"):
                     msg = "Failed to download torrent file from provider. Cannot proceed with download."
                     logger.error(msg)
                     raise ValueError(msg)

        logger.info(f"Starting download for {release.title}...")
        downloaded_file = downloader.download(release, output_path, output_filename=output_filename)
        return downloaded_file

    def download_by_id(
        self,
        source_name: str,
        source_id: str,
        output_path: str,
        output_filename: Optional[str] = None,
        target_file: Optional[str] = None,
        download_url: Optional[str] = None,
    ) -> str:
        """
        Download by source ID without needing a cached Release object.

        This is useful when the original Release was serialized to storage and
        you need to download later without re-searching. For torrent sources,
        this fetches the .torrent file by ID. For YouTube, this uses the URL directly.

        Args:
            source_name: Provider name (e.g., 'RED', 'OPS', 'YouTube')
            source_id: Source-specific ID (torrent ID for RED/OPS, video ID for YouTube)
            output_path: Directory to save the downloaded file
            output_filename: Optional filename for the output
            target_file: For torrents, the specific file to extract from the torrent
            download_url: For YouTube/Spotify, the direct URL to download from

        Returns:
            Path to the downloaded file

        Raises:
            ValueError: If provider not found or download fails
        """
        import os
        import tempfile

        # Get downloader for this source
        downloader = self._downloader_map.get(source_name, self._default_downloader)
        if not downloader:
            msg = f"No downloader registered for source: {source_name}"
            logger.error(msg)
            raise ValueError(msg)

        # Get provider
        provider = next((p for p in self.providers if p.name == source_name), None)

        # For YouTube/Spotify, we can download directly with URL
        if source_name == "YouTube":
            if not download_url:
                # Construct URL from video ID
                download_url = f"https://www.youtube.com/watch?v={source_id}"

            # Create minimal release for YouTube (lossy AAC from YouTube)
            release = Release(
                title=output_filename or source_id,
                artist="",
                quality=Quality(format=AudioFormat.AAC, media=MediaSource.WEB),
                source_name=source_name,
                download_url=download_url,
                source_id=source_id,
            )
            logger.info(f"Starting YouTube download for {download_url}...")
            return downloader.download(release, output_path, output_filename=output_filename)

        if source_name == "Spotify":
            if not download_url:
                download_url = f"spotify:track:{source_id}"

            # Spotify outputs FLAC but source is 320kbps Vorbis (not true lossless)
            release = Release(
                title=output_filename or source_id,
                artist="",
                quality=Quality(format=AudioFormat.FLAC, media=MediaSource.WEB),
                source_name=source_name,
                download_url=download_url,
                source_id=source_id,
                target_file=target_file,
            )
            logger.info(f"Starting Spotify download for {download_url}...")
            return downloader.download(release, output_path, output_filename=output_filename)

        # For torrent sources (RED/OPS), we need to fetch the .torrent file
        if source_name in ("RED", "OPS"):
            if not provider:
                msg = f"Provider {source_name} not registered"
                logger.error(msg)
                raise ValueError(msg)

            logger.info(f"Fetching torrent artifact for {source_name} ID: {source_id}")
            artifact = provider.fetch_artifact_by_id(source_id)

            if not artifact:
                msg = f"Failed to fetch torrent file for {source_name} ID: {source_id}"
                logger.error(msg)
                raise ValueError(msg)

            # Save torrent to temp file
            fd, torrent_path = tempfile.mkstemp(suffix=".torrent")
            with os.fdopen(fd, 'wb') as tmp:
                tmp.write(artifact)
            os.chmod(torrent_path, 0o644)
            logger.debug(f"Saved temporary torrent file to {torrent_path}")

            # Create minimal release for torrent download (assume FLAC from CD for trackers)
            release = Release(
                title=output_filename or source_id,
                artist="",
                quality=Quality(format=AudioFormat.FLAC, media=MediaSource.CD),
                source_name=source_name,
                download_url=torrent_path,  # Local path to .torrent file
                source_id=source_id,
                target_file=target_file,
            )

            logger.info(f"Starting torrent download for {source_name} ID: {source_id}...")
            return downloader.download(release, output_path, output_filename=output_filename)

        # Unknown source
        msg = f"download_by_id not implemented for source: {source_name}"
        logger.error(msg)
        raise ValueError(msg)
