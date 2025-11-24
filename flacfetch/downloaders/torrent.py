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
        
        # Check if download_url is a local file path
        if os.path.exists(release.download_url) and os.path.isfile(release.download_url):
            info = lt.torrent_info(release.download_url)
            
            # Selective Download Logic
            if release.target_file:
                print(f"Target file specified: {release.target_file}")
                # file_priorities is a list of integers, one per file
                # 0 = do not download, 1-7 = priority
                file_priorities = [0] * info.num_files()
                found = False
                
                for i in range(info.num_files()):
                    f_path = info.files().file_path(i)
                    # Use suffix match to be safe, or exact match if path known
                    if release.target_file in f_path:
                        print(f"Found target file in torrent: {f_path} (Index {i})")
                        file_priorities[i] = 7
                        found = True
                        break
                
                if not found:
                    print(f"Warning: Target file '{release.target_file}' not found in torrent file list. Downloading entire torrent.")
                    file_priorities = [] # Default behavior
            else:
                file_priorities = []

            # Add torrent
            handle = self.ses.add_torrent({'ti': info, 'save_path': output_path})
            
            # Apply priorities if set
            if file_priorities:
                handle.prioritize_files(file_priorities)
                
        elif release.download_url.startswith("magnet:"):
             handle = lt.add_magnet_uri(self.ses, release.download_url, params)
             # Magnet selective download requires metadata first, handled below
        else:
             raise ValueError(f"TorrentDownloader requires a local .torrent file path or magnet link. Got: {release.download_url}")

        print(f"Downloading {release.title}...")
        
        # Wait for metadata if magnet or just check status
        if release.download_url.startswith("magnet:") and not handle.has_metadata():
            print("Waiting for metadata...")
            while not handle.has_metadata():
                time.sleep(1)
            
            # TODO: Implement selective download for magnet links once metadata is retrieved
            # For now, it downloads full magnet

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
