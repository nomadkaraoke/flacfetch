import time
import os
import sys
from typing import Optional, Dict, Any
from ..core.interfaces import Downloader
from ..core.models import Release

# Try import libtorrent
try:
    import libtorrent as lt # type: ignore
except ImportError:
    lt = None

class TorrentDownloader(Downloader):
    def __init__(self, session_settings: Optional[Dict[str, Any]] = None):
        if lt is None:
            # We allow instantiation but download will fail, or raise here?
            # Better to raise here so Manager knows not to register it.
            pass
        else:
            self.ses = lt.session()
            self.ses.listen_on(6881, 6891)
        
    def download(self, release: Release, output_path: str) -> None:
        if lt is None:
            raise ImportError("libtorrent not installed. Cannot download torrent.")
            
        if not release.download_url:
            raise ValueError("Release has no download URL/Path")
            
        params = {
            'save_path': output_path,
            'storage_mode': lt.storage_mode_t(2), # storage_mode_sparse
        }
        
        handle = None
        
        # Check if download_url is a local file path (the .torrent file we fetched)
        # We assume if it's a file on disk, it's a .torrent file
        if os.path.exists(release.download_url) and os.path.isfile(release.download_url):
            info = lt.torrent_info(release.download_url)
            handle = self.ses.add_torrent({'ti': info, 'save_path': output_path})
        elif release.download_url.startswith("magnet:"):
             handle = lt.add_magnet_uri(self.ses, release.download_url, params)
        else:
             raise ValueError(f"TorrentDownloader requires a local .torrent file path or magnet link. Got: {release.download_url}")

        print(f"Downloading {release.title}...")
        
        # Wait for metadata if magnet (not usually needed for .torrent files but good practice)
        if release.download_url.startswith("magnet:"):
            print("Waiting for metadata...")
            while not handle.has_metadata():
                time.sleep(1)
        
        print("Starting download...")
        
        while True:
            s = handle.status()
            if s.state == lt.torrent_status.seeding or s.state == lt.torrent_status.finished:
                break
                
            state_str = ['queued', 'checking', 'downloading metadata', \
                         'downloading', 'finished', 'seeding', 'allocating']
            state = state_str[s.state]
            print(f'\r{s.progress * 100:.2f}% complete (down: {s.download_rate / 1000:.1f} kB/s up: {s.upload_rate / 1000:.1f} kB/s peers: {s.num_peers}) {state}', end='')
            sys.stdout.flush()
            time.sleep(1)
            
        print(f"\nDownload complete: {release.title}")

