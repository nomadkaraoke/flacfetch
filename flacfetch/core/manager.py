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
    
    def add_provider(self, provider: Provider):
        self.providers.append(provider)

    def register_downloader(self, source_name: str, downloader: Downloader):
        self._downloader_map[source_name] = downloader

    def set_default_downloader(self, downloader: Downloader):
        self._default_downloader = downloader

    def search(self, query: TrackQuery) -> List[Release]:
        all_releases = []
        for provider in self.providers:
            try:
                logger.info(f"Searching {provider.name} for '{query.artist} - {query.title}'...") 
                results = provider.search(query)
                logger.info(f"Found {len(results)} results from {provider.name}")
                all_releases.extend(results)
            except Exception as e:
                logger.error(f"Error searching {provider.name}: {e}")
                pass
        return all_releases

    def _sort_releases(self, releases: List[Release]) -> List[Release]:
        # Sorting Logic:
        # 1. Match Score (Exact match > Partial match)
        # 2. Release Type (Album > Single > EP > Other)
        # 3. Year (Oldest first for "original")
        # 4. Quality (Lossless > High Bitrate)
        
        def release_type_score(r: Release) -> int:
            if not r.release_type: return 0
            # Higher is better
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

        # Sort key: tuple comparisons
        return sorted(releases, key=lambda r: (
            r.match_score, # Primary: Name match
            release_type_score(r), # Secondary: Type
            -(r.year or 9999), # Tertiary: Year (Oldest first -> negative of year ensures ascending order? No. 
                               # Python sorts ascending. We want oldest first. So smaller year is better.
                               # But we are sorting `reverse=True`?
                               # Let's stick to one direction.
                               # If we use reverse=True (descending):
                               # Match Score: 1.0 > 0.5 (Correct)
                               # Release Type: 10 > 1 (Correct)
                               # Year: We want 2011 > 2014? No, we want 2011 (Oldest) to appear first?
                               # Actually, usually people want the *Original* release. So oldest.
                               # If sorting descending, we want 2011 to be "bigger" than 2014.
                               # So use -2011 > -2014.
                               -(r.year or 9999),
            r.quality # Quaternary: Quality (Quality implements __lt__)
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

    def download(self, release: Release, output_path: str):
        downloader = self._downloader_map.get(release.source_name, self._default_downloader)
        if not downloader:
            msg = f"No downloader registered for source: {release.source_name}"
            logger.error(msg)
            raise ValueError(msg)
        
        provider = next((p for p in self.providers if p.name == release.source_name), None)
        
        if provider:
            if not release.target_file and release.track_pattern:
                # This shouldn't happen often with RedactedProvider now resolving upfront
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
        
        logger.info(f"Starting download for {release.title}...")
        downloader.download(release, output_path)
