import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from ..core.interfaces import Downloader
from ..core.log import get_logger
from ..core.models import Release

logger = get_logger("TorrentDownloader")

# Try import transmission_rpc
try:
    import transmission_rpc
except ImportError:
    transmission_rpc = None

class TorrentDownloader(Downloader):
    """
    Downloads torrents using Transmission daemon.
    Requires transmission-daemon to be running locally.
    """

    def __init__(self,
                 host: str = 'localhost',
                 port: int = 9091,
                 username: Optional[str] = None,
                 password: Optional[str] = None):
        """
        Initialize Transmission RPC client.

        Args:
            host: Transmission daemon host (default: localhost)
            port: Transmission RPC port (default: 9091)
            username: Optional RPC username
            password: Optional RPC password
        """
        if transmission_rpc is None:
            raise ImportError(
                "transmission-rpc not installed. Install with: pip install transmission-rpc"
            )

        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.client = None

    def _ensure_daemon_running(self) -> bool:
        """Check if transmission-daemon is running, attempt to start if not."""
        try:
            # Try to connect
            self.client = transmission_rpc.Client(
                host=self.host,
                port=self.port,
                username=self.username,
                password=self.password
            )
            logger.debug(f"Connected to Transmission daemon at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.warning(f"Could not connect to Transmission daemon: {e}")
            logger.info("Attempting to start transmission-daemon...")

            try:
                # Try to start transmission-daemon
                subprocess.Popen(
                    ['transmission-daemon'],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                logger.info("Started transmission-daemon. Waiting for it to initialize...")
                time.sleep(2)

                # Try connecting again
                self.client = transmission_rpc.Client(
                    host=self.host,
                    port=self.port,
                    username=self.username,
                    password=self.password
                )
                logger.info("Successfully connected to Transmission daemon")
                return True
            except Exception as start_error:
                logger.error(f"Failed to start or connect to transmission-daemon: {start_error}")
                logger.error(
                    "Please ensure transmission-daemon is installed and running.\n"
                    "Install: brew install transmission-cli\n"
                    "Start: transmission-daemon"
                )
                return False

    def download(self, release: Release, output_path: str, output_filename: Optional[str] = None) -> str:
        """
        Download a torrent using Transmission.

        Args:
            release: Release object containing download_url (path to .torrent file)
            output_path: Directory to save downloaded files
            output_filename: Optional specific filename for the output file

        Returns:
            Path to the downloaded file
        """
        if not release.download_url:
            raise ValueError("Release has no download URL/Path")

        # Ensure daemon is running
        if not self._ensure_daemon_running():
            raise RuntimeError("Cannot connect to Transmission daemon")

        # Use a temp directory for the actual download
        temp_dir = tempfile.mkdtemp(prefix="flacfetch_torrent_")
        logger.debug(f"Using temporary download directory: {temp_dir}")

        # Prepare final output path
        abs_output_path = os.path.abspath(output_path)
        os.makedirs(abs_output_path, exist_ok=True)

        # Check if it's a local torrent file
        if not os.path.exists(release.download_url):
            raise ValueError(
                f"TorrentDownloader requires a local .torrent file path. "
                f"Got: {release.download_url}"
            )

        if not os.path.isfile(release.download_url):
            raise ValueError(f"Not a file: {release.download_url}")

        logger.info(f"Adding torrent to Transmission: {release.download_url}")

        target_file_path = None
        downloaded_file_path = None

        try:
            # Add torrent to Transmission in PAUSED state first
            # This prevents it from starting download before we set file priorities
            torrent = self.client.add_torrent(
                release.download_url,
                download_dir=temp_dir,  # Download to temp directory
                paused=True  # Critical: add in paused state
            )

            logger.info(f"Torrent added (paused): {torrent.name} (ID: {torrent.id})")

            # Handle selective file download
            if release.target_file:
                logger.info(f"Target file specified: {release.target_file}")

                # Wait for metadata to be parsed (files list will be empty until torrent is parsed)
                logger.debug("Waiting for torrent metadata to be parsed...")
                max_wait = 10  # seconds
                waited = 0
                files = []
                while waited < max_wait:
                    torrent = self.client.get_torrent(torrent.id)
                    files = torrent.get_files()
                    if len(files) > 0:
                        logger.debug(f"Metadata parsed: {len(files)} files found")
                        break
                    time.sleep(0.5)
                    waited += 0.5

                if len(files) == 0:
                    logger.warning("Timeout waiting for torrent metadata. Downloading entire torrent.")
                else:
                    # Find target file in the list
                    target_indices = []
                    target_file_name = None

                    for file_obj in files:
                        file_name = file_obj.name
                        file_id = file_obj.id
                        if release.target_file in file_name:
                            logger.info(f"Found target file: {file_name} (id {file_id})")
                            target_indices.append(file_id)
                            if not target_file_name:
                                target_file_name = file_name  # Remember the first match

                    if target_indices:
                        # Set unwanted files
                        all_indices = [f.id for f in files]
                        unwanted_indices = [i for i in all_indices if i not in target_indices]

                        if unwanted_indices:
                            logger.info(f"Setting file priorities: want {len(target_indices)}, unwant {len(unwanted_indices)}")
                            self.client.change_torrent(
                                torrent.id,
                                files_unwanted=unwanted_indices,
                                files_wanted=target_indices
                            )
                            logger.info(f"File priorities set: downloading only {len(target_indices)} file(s)")
                    else:
                        logger.warning(
                            f"Target file '{release.target_file}' not found in torrent. "
                            f"Downloading entire torrent."
                        )

            # Now start the torrent
            logger.info("Starting torrent download...")
            self.client.start_torrent(torrent.id)

            # Monitor download progress
            print(f"Downloading {release.title}...")

            last_progress = -1
            stall_counter = 0
            max_stalls = 60  # 60 seconds of no progress before warning

            while True:
                # Refresh torrent status
                torrent = self.client.get_torrent(torrent.id)

                status = torrent.status
                progress = torrent.progress

                # Check if complete
                if status in ['seeding', 'seed_pending']:
                    print("\n✓ Download complete")
                    logger.info(f"Torrent finished: {torrent.name}")

                    # Stop the torrent (stop seeding)
                    self.client.stop_torrent(torrent.id)
                    logger.info(f"Stopped seeding torrent {torrent.id}")

                    # Find and move the downloaded file
                    torrent = self.client.get_torrent(torrent.id)
                    files = torrent.get_files()

                    # If we have a target file, find it and move it
                    if release.target_file and files:
                        for file_obj in files:
                            if file_obj.selected and release.target_file in file_obj.name:
                                # Construct the full path to the downloaded file
                                source_path = Path(temp_dir) / file_obj.name
                                logger.debug(f"Looking for downloaded file at: {source_path}")

                                if source_path.exists():
                                    # Determine output filename
                                    if output_filename:
                                        # Keep the extension from the original file
                                        original_ext = source_path.suffix
                                        if not output_filename.endswith(original_ext):
                                            final_filename = output_filename + original_ext
                                        else:
                                            final_filename = output_filename
                                    else:
                                        # Use the original filename (just the basename)
                                        final_filename = source_path.name

                                    target_file_path = Path(abs_output_path) / final_filename
                                    logger.info(f"Moving file to: {target_file_path}")

                                    # Move the file
                                    shutil.move(str(source_path), str(target_file_path))
                                    downloaded_file_path = str(target_file_path)
                                    logger.info("File moved successfully")
                                    break

                        if not downloaded_file_path:
                            logger.warning("Could not find downloaded file to move")

                    break

                # Check for errors
                if status == 'stopped' and progress < 100:
                    error_msg = torrent.error_string if hasattr(torrent, 'error_string') else "Unknown error"
                    raise RuntimeError(f"Torrent download stopped with error: {error_msg}")

                # Display progress
                download_rate = getattr(torrent, 'rate_download', 0) / 1000  # KB/s
                upload_rate = getattr(torrent, 'rate_upload', 0) / 1000  # KB/s
                peers = getattr(torrent, 'peers_connected', 0)

                # Detect stalls
                if abs(progress - last_progress) < 0.01:  # Less than 0.01% progress
                    stall_counter += 1
                else:
                    stall_counter = 0
                    last_progress = progress

                stall_warning = ""
                if stall_counter > max_stalls:
                    stall_warning = " [STALLED - no progress]"

                print(
                    f'\r{progress:.2f}% complete '
                    f'(down: {download_rate:.1f} KB/s up: {upload_rate:.1f} KB/s '
                    f'peers: {peers}) {status}{stall_warning}',
                    end=''
                )
                sys.stdout.flush()

                # Log verbose info
                if logger.isEnabledFor(10):  # DEBUG level
                    logger.debug(
                        f"Torrent {torrent.id}: {progress:.2f}% - "
                        f"D:{download_rate:.1f}KB/s U:{upload_rate:.1f}KB/s - "
                        f"Peers:{peers} - Status:{status}"
                    )

                time.sleep(1)

        except transmission_rpc.error.TransmissionError as e:
            logger.error(f"Transmission error: {e}")
            raise RuntimeError(f"Transmission error: {e}")
        except Exception as e:
            logger.error(f"Download failed: {e}")
            raise
        finally:
            # Clean up: remove torrent from Transmission and delete temp directory
            try:
                if 'torrent' in locals():
                    logger.debug(f"Removing torrent {torrent.id} from Transmission")
                    self.client.remove_torrent(torrent.id, delete_data=True)
            except Exception as e:
                logger.warning(f"Failed to remove torrent: {e}")

            try:
                if os.path.exists(temp_dir):
                    logger.debug(f"Cleaning up temporary directory: {temp_dir}")
                    shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception as e:
                logger.warning(f"Failed to clean up temp directory: {e}")

        return downloaded_file_path or ""
