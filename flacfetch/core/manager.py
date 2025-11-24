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
        # 3. Seeders (Higher is better - reliability/speed)
        # 4. Year (Oldest first for "original" preference if types match, or Descending?
        #    - If I want "most normal", usually oldest main album.
        #    - If I want "newest remaster", newest.
        #    - Let's go with Oldest as tie-breaker for "Original".
        # 5. Quality
        
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

        return sorted(releases, key=lambda r: (
            r.match_score,          # 1. Name Match (High to Low)
            release_type_score(r),  # 2. Type (Album first)
            (r.seeders or 0),       # 3. Seeders (High to Low)
            -(r.year or 9999),      # 4. Year (Oldest first -> Smallest year -> Largest negative)
            r.quality               # 5. Quality
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
