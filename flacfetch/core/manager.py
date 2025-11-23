from typing import List, Optional, Type
from .models import TrackQuery, Release
from .interfaces import Provider, Downloader, InteractionHandler
from .log import get_logger

logger = get_logger("FetchManager")

class FetchManager:
    def __init__(self):
        self.providers: List[Provider] = []
        # Mapping source_name to Downloader instance
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

    def select_best(self, releases: List[Release]) -> Optional[Release]:
        if not releases:
            return None
        # Sort reverse so highest quality is first
        sorted_releases = sorted(releases, key=lambda r: r.quality, reverse=True)
        return sorted_releases[0]

    def select_interactive(self, releases: List[Release], handler: InteractionHandler) -> Optional[Release]:
        if not releases:
            return None
        # Sort anyway for better UX
        sorted_releases = sorted(releases, key=lambda r: r.quality, reverse=True)
        return handler.select_release(sorted_releases)

    def download(self, release: Release, output_path: str):
        downloader = self._downloader_map.get(release.source_name, self._default_downloader)
        if not downloader:
            msg = f"No downloader registered for source: {release.source_name}"
            logger.error(msg)
            raise ValueError(msg)
        
        # Check if we need to fetch artifact first (e.g. .torrent file)
        # Find the provider for this release
        provider = next((p for p in self.providers if p.name == release.source_name), None)
        if provider:
            logger.info(f"Fetching metadata/artifact for {release.title} from {provider.name}...")
            artifact = provider.fetch_artifact(release)
            if artifact:
                # If artifact is bytes (e.g. .torrent), we might need to save it to a temp file 
                # or pass it to downloader. 
                # For simplicity, let's assume downloader can handle raw data if we extended the interface,
                # or we save to temp file here.
                # Let's save to a temp .torrent file if it looks like one.
                import tempfile
                import os
                
                # Heuristic: if artifact starts with 'd8:announce', it's a torrent
                # or just suffix with .torrent
                fd, path = tempfile.mkstemp(suffix=".torrent")
                with os.fdopen(fd, 'wb') as tmp:
                    tmp.write(artifact)
                
                logger.debug(f"Saved temporary torrent file to {path}")
                # Update release or pass path to downloader?
                # Downloader.download takes release and output_path.
                # We can hack release.download_url to be the file path for the torrent downloader
                # strictly for this operation.
                # Better: Downloader interface should take optional 'meta_file_path'
                
                # For now, let's assume the specific TorrentDownloader knows how to handle a local file path in download_url
                # if it starts with file:// or just absolute path.
                release.download_url = path 
        
        logger.info(f"Starting download for {release.title}...")
        downloader.download(release, output_path)
