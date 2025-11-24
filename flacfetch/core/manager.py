from typing import List, Optional, Type
from .models import TrackQuery, Release
from .interfaces import Provider, Downloader, InteractionHandler
from .log import get_logger

logger = get_logger("FetchManager")

class FetchManager:
    def __init__(self):
        self.providers: List[Provider] = []
        self._downloader_map: dict[str, Downloader] = {}
        self._default_downloader: Optional[Downloader] = None
        self._provider_priority: Optional[List[str]] = None
        self._use_fallback_search: bool = True  # Search lower priority providers if higher ones fail
    
    def add_provider(self, provider: Provider):
        self.providers.append(provider)
    
    def set_provider_priority(self, priority_list: List[str]):
        """Set provider search priority by name.
        
        Args:
            priority_list: List of provider names in priority order (e.g., ['Redacted', 'OPS', 'YouTube'])
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

    def search(self, query: TrackQuery) -> List[Release]:
        all_releases = []
        
        # Get providers in priority order
        providers = self._get_ordered_providers()
        
        for idx, provider in enumerate(providers):
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
                
        return all_releases
    
    def _get_ordered_providers(self) -> List[Provider]:
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

    def _sort_releases(self, releases: List[Release]) -> List[Release]:
        # Sorting Logic:
        # 1. Match Score (Redacted only, mostly)
        # 2. Channel Match (YouTube artist matching)
        # 3. Official/Topic (YouTube)
        # 4. Release Type (Redacted)
        # 5. Seeders (Redacted) / Views (YouTube)
        # 6. Quality (Bitrate/Lossless)
        # 7. Year (Context dependent)
        
        def release_type_score(r: Release) -> int:
            if not r.release_type: return 0
            priority = {
                "Album": 10,
                "Single": 9,
                "EP": 8,
                "Soundtrack": 7,
                "Compilation": 5,
                "Anthology": 5,
                "Remix": 1
            }
            return priority.get(r.release_type, 0)
        
        def channel_match_score(r: Release) -> int:
            """Score based on how well the channel name matches the artist name (YouTube only)"""
            if r.source_name != "YouTube":
                return 0
            if not r.channel or not r.artist:
                return 0
            
            channel_lower = r.channel.lower()
            artist_lower = r.artist.lower()
            
            # Exact match (highest priority)
            if channel_lower == artist_lower:
                return 100
            # Artist name in channel (e.g., "salute - Topic")
            elif artist_lower in channel_lower:
                return 80
            # Channel in artist name
            elif channel_lower in artist_lower:
                return 60
            # No match
            return 0

        def is_official_score(r: Release) -> int:
            if r.source_name != "YouTube":
                return 0
            score = 0
            # "Topic" channels are auto-generated official audio
            if r.channel and " - Topic" in r.channel:
                score += 20
            # VEVO
            if r.channel and "VEVO" in r.channel.upper():
                score += 15
            # Title keywords
            title_lower = r.title.lower()
            if "official audio" in title_lower:
                score += 10
            elif "official video" in title_lower:
                score += 5
            elif "official music video" in title_lower:
                score += 5
            elif "lyric video" in title_lower:
                score += 2
            
            return score

        def year_score(r: Release) -> int:
            if not r.year: return 0
            if r.source_name == "Redacted":
                # Oldest first -> -Year
                return -r.year
            elif r.source_name == "YouTube":
                # Newest first -> Year
                return r.year
            return -r.year

        return sorted(releases, key=lambda r: (
            r.match_score,             # 1. Name Match
            channel_match_score(r),    # 2. Channel Match (YouTube)
            is_official_score(r),      # 3. Official (YouTube)
            release_type_score(r),     # 4. Type (Redacted)
            (r.seeders or r.view_count or 0),  # 5. Seeders/Views
            r.quality,                 # 6. Quality
            year_score(r)              # 7. Year
        ), reverse=True)

    def select_best(self, releases: List[Release]) -> Optional[Release]:
        if not releases:
            return None
        sorted_releases = self._sort_releases(releases)
        return sorted_releases[0]

    def select_interactive(self, releases: List[Release], handler: InteractionHandler) -> Optional[Release]:
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
                import tempfile
                import os
                fd, path = tempfile.mkstemp(suffix=".torrent")
                with os.fdopen(fd, 'wb') as tmp:
                    tmp.write(artifact)
                
                logger.debug(f"Saved temporary torrent file to {path}")
                release.download_url = path 
            elif provider.name == "Redacted":
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
