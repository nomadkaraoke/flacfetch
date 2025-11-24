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
        
        # Alert handling for verbose logging
        if logging.getLogger().isEnabledFor(logging.DEBUG):
            self.ses.set_alert_mask(lt.alert.category_t.all_categories)
        
        while True:
            # Poll alerts
            if logging.getLogger().isEnabledFor(logging.DEBUG):
                alerts = self.ses.pop_alerts()
                for a in alerts:
                    # Filter out noisy alerts if needed, or log all
                    # tracker_announce_alert, tracker_reply_alert, tracker_error_alert are useful
                    if isinstance(a, lt.tracker_error_alert) or isinstance(a, lt.tracker_reply_alert) or isinstance(a, lt.tracker_announce_alert):
                        print(f"\n[LibTorrent Alert] {a.message()}")
                    elif isinstance(a, lt.peer_error_alert) or isinstance(a, lt.peer_disconnected_alert):
                         # Often too noisy, maybe only show connection errors if stuck
                         pass
                    elif a.category() & lt.alert.category_t.error_notification:
                         print(f"\n[LibTorrent Error] {a.message()}")
            
            s = handle.status()
            if s.state == lt.torrent_status.seeding or s.state == lt.torrent_status.finished:
                break
                
            # State mapping based on libtorrent 1.2.x enum values
            # 0: queued_for_checking
            # 1: checking_files
            # 2: downloading_metadata
            # 3: downloading
            # 4: finished
            # 5: seeding
            # 6: allocating
            # 7: checking_resume_data
            state_str = ['queued_for_checking', 'checking_files', 'downloading_metadata', \
                         'downloading', 'finished', 'seeding', 'allocating', 'checking_resume_data']
            
            if s.state < len(state_str):
                state = state_str[s.state]
            else:
                state = f"unknown state ({s.state})"

            print(f'\r{s.progress * 100:.2f}% complete (down: {s.download_rate / 1000:.1f} kB/s up: {s.upload_rate / 1000:.1f} kB/s peers: {s.num_peers}) {state}', end='')
            sys.stdout.flush()
            time.sleep(1)
            
        print(f"\nDownload complete: {release.title}")
