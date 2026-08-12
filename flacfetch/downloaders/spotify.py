"""Spotify downloader for flacfetch.

This module provides download functionality for Spotify tracks using:
- librespot binary (Rust) with pipe backend for audio capture
- Spotify Web API (via spotipy) for playback control
- ffmpeg for PCM to FLAC conversion

Output: FLAC at 44.1kHz/16-bit (CD quality)
"""

import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..core.config import get_librespot_credentials_dir
from ..core.interfaces import Downloader
from ..core.log import get_logger
from ..core.models import Release

if TYPE_CHECKING:
    import spotipy

    from ..providers.spotify import SpotifyProvider

logger = get_logger("SpotifyDownloader")

# librespot configuration
LIBRESPOT_DEVICE_NAME = "flacfetch-capture"
SAMPLE_RATE = 44100
CHANNELS = 2
BIT_DEPTH = 16


class SpotifyDownloadError(Exception):
    """Raised when Spotify download fails."""

    pass


class LibrespotNotFoundError(SpotifyDownloadError):
    """Raised when librespot binary is not found."""

    pass


def find_librespot() -> Optional[str]:
    """Find librespot binary in common locations.

    Returns:
        Path to librespot binary, or None if not found
    """
    locations = [
        shutil.which("librespot"),
        "/opt/homebrew/bin/librespot",
        "/usr/local/bin/librespot",
        os.path.expanduser("~/.cargo/bin/librespot"),
    ]
    for loc in locations:
        if loc and os.path.isfile(loc):
            return loc
    return None


class SpotifyDownloader(Downloader):
    """Downloader for Spotify tracks using librespot + Web API.

    Downloads work by:
    1. Starting librespot with OAuth token and pipe backend
    2. Using Web API to trigger playback on the librespot device
    3. Capturing raw PCM audio from the pipe
    4. Converting PCM to FLAC with ffmpeg

    Requires:
    - librespot binary (brew install librespot or cargo install librespot)
    - ffmpeg for conversion
    - Valid OAuth token from SpotifyProvider
    """

    def __init__(self, provider: Optional["SpotifyProvider"] = None):
        """Initialize Spotify downloader.

        Args:
            provider: SpotifyProvider instance for OAuth token and Web API access.
                     Required for downloading.
        """
        self._provider = provider
        self._librespot_path = find_librespot()

        if not self._librespot_path:
            logger.warning(
                "librespot not found. Install with: brew install librespot"
            )

    def _get_spotify_client(self) -> "spotipy.Spotify":
        """Get Spotify Web API client from provider."""
        if self._provider is None:
            raise SpotifyDownloadError("SpotifyProvider not configured")
        return self._provider._get_client()

    def _get_access_token(self) -> str:
        """Get OAuth access token from provider."""
        if self._provider is None:
            raise SpotifyDownloadError("SpotifyProvider not configured")
        return self._provider.get_access_token()

    def _resolve_duration_secs(
        self, sp: "spotipy.Spotify", track_id: str, release: Release
    ) -> int:
        """Resolve track duration in seconds.

        The PCM capture loop compares captured bytes against an expected size
        derived from duration. A wrong duration silently fails short tracks:
        if the expected-size guess is too large, the real full-track capture
        never reaches the 70% threshold and the download fails with a false
        "Download timeout" (see _wait_for_download).

        Prefer the duration already on the release (from search). Otherwise
        look it up via the Web API, which is reliable and cheap. Only fall
        back to the 300s default if both fail.
        """
        if release.duration_seconds:
            return release.duration_seconds
        try:
            duration_ms = sp.track(track_id).get("duration_ms")
            if duration_ms:
                secs = duration_ms // 1000
                logger.debug(f"Resolved duration from Spotify API: {secs}s")
                return secs
        except Exception as e:
            logger.warning(f"Failed to look up track duration, using default: {e}")
        return 300

    def download(
        self,
        release: Release,
        output_path: str,
        output_filename: Optional[str] = None,
    ) -> str:
        """Download a Spotify track.

        Args:
            release: Release object with spotify:track:ID URL
            output_path: Directory to save the downloaded file
            output_filename: Optional filename (without extension)

        Returns:
            Path to the downloaded FLAC file

        Raises:
            SpotifyDownloadError: If download fails
            LibrespotNotFoundError: If librespot binary not found
        """
        if not self._librespot_path:
            raise LibrespotNotFoundError(
                "librespot not found. Install with:\n"
                "  brew install librespot\n"
                "  # or\n"
                "  cargo install librespot"
            )

        if not release.download_url:
            raise SpotifyDownloadError("Release has no download URL")

        track_id = self._extract_track_id(release.download_url)
        if not track_id:
            raise SpotifyDownloadError(f"Invalid Spotify URI/URL: {release.download_url}")

        track_uri = f"spotify:track:{track_id}"
        track_name = release.target_file or release.title

        logger.info(f"Downloading from Spotify: {release.artist} - {track_name}")

        # Determine output filename
        if output_filename:
            base_name = os.path.splitext(output_filename)[0]
        else:
            safe_artist = self._sanitize_filename(release.artist)
            safe_title = self._sanitize_filename(track_name)
            base_name = f"{safe_artist} - {safe_title}"

        os.makedirs(output_path, exist_ok=True)

        pcm_path = Path(output_path) / f"{base_name}.pcm"
        flac_path = Path(output_path) / f"{base_name}.flac"
        log_path = Path(output_path) / f"{base_name}.librespot.log"

        # The Web API client is always required to control playback on the
        # librespot device (start/pause). Authenticate it up-front.
        try:
            sp = self._get_spotify_client()
        except Exception as e:
            raise SpotifyDownloadError(f"Authentication failed: {e}") from e

        duration_secs = self._resolve_duration_secs(sp, track_id, release)

        # Build the librespot invocation. Preferred: stored reusable credentials
        # minted by librespot's own OAuth client (the only client Spotify accepts
        # for Spotify Connect / spirc device login). Fall back to a third-party
        # access token only if no stored credentials exist -- Spotify now rejects
        # that path at spirc with INVALID_CREDENTIALS, so it is best-effort.
        logger.debug(f"Starting librespot: {self._librespot_path}")
        librespot_cmd = [
            self._librespot_path,
            "-n", LIBRESPOT_DEVICE_NAME,
            "--backend", "pipe",
            "--bitrate", "320",
            "--disable-discovery",
            # Volume settings for full-quality recording:
            "--initial-volume", "100",  # Start at max volume
            "--volume-ctrl", "fixed",  # No dynamic volume adjustments
            # Note: volume normalisation is disabled by default, no flag needed
        ]
        librespot_env = os.environ.copy()

        creds_dir = get_librespot_credentials_dir()
        if os.path.isfile(os.path.join(creds_dir, "credentials.json")):
            # Stored-credential login. Disable the audio cache so librespot does
            # not fill the disk with cached streams (we only want credentials).
            librespot_cmd += ["-c", creds_dir, "--disable-audio-cache"]
            logger.debug(f"Using librespot stored credentials from {creds_dir}")
        else:
            try:
                access_token = self._get_access_token()
            except Exception as e:
                raise SpotifyDownloadError(f"Authentication failed: {e}") from e
            # Use an env var for the token (safer than a command-line arg, which
            # is visible in process listings).
            librespot_env["LIBRESPOT_ACCESS_TOKEN"] = access_token
            logger.warning(
                "No librespot stored credentials at %s; falling back to "
                "access-token login. Spotify Connect (spirc) may reject this "
                "with INVALID_CREDENTIALS. Run the credential keeper to mint "
                "stored credentials.",
                creds_dir,
            )

        with open(pcm_path, "wb") as pcm_file, open(log_path, "w") as log_file:
            librespot_proc = subprocess.Popen(
                librespot_cmd,
                stdout=pcm_file,
                stderr=log_file,
                env=librespot_env,
            )

            try:
                # Wait for device to appear in Spotify
                device = self._wait_for_device(sp, timeout=15)
                if not device:
                    raise SpotifyDownloadError(self._device_not_found_message(log_path))

                logger.debug(f"Device ready: {device['id']}")

                # Start playback
                logger.debug(f"Starting playback: {track_uri}")
                sp.start_playback(device_id=device["id"], uris=[track_uri])

                # Wait for track to load
                self._wait_for_track_load(sp, track_uri, timeout=30)

                # Wait for download to complete
                # librespot downloads faster than real-time, so we monitor file size
                self._wait_for_download(
                    pcm_path, duration_secs, librespot_proc, timeout=duration_secs + 30
                )

                # Pause playback
                try:
                    sp.pause_playback(device_id=device["id"])
                except Exception:
                    pass

            except KeyboardInterrupt:
                logger.info("Download interrupted")
                raise
            except Exception as e:
                # Read log for more info
                log_content = ""
                if log_path.exists():
                    log_content = log_path.read_text()[-500:]  # Last 500 chars
                logger.debug(f"librespot log: {log_content}")
                raise SpotifyDownloadError(f"Download failed: {e}") from e
            finally:
                # Stop librespot gracefully
                self._stop_librespot(librespot_proc)

        # Verify PCM was captured
        if not pcm_path.exists() or pcm_path.stat().st_size == 0:
            log_content = log_path.read_text() if log_path.exists() else "No log"
            raise SpotifyDownloadError(f"No audio captured. Log: {log_content[-500:]}")

        pcm_size = pcm_path.stat().st_size
        logger.debug(f"Captured {pcm_size / (1024*1024):.2f} MB PCM")

        # Convert PCM to FLAC
        if not self._convert_pcm_to_flac(pcm_path, flac_path):
            raise SpotifyDownloadError("FLAC conversion failed")

        # Cleanup temp files
        pcm_path.unlink(missing_ok=True)
        log_path.unlink(missing_ok=True)

        logger.info(f"Download complete: {flac_path}")
        return str(flac_path)

    def _device_not_found_message(self, log_path: Path) -> str:
        """Build a diagnostic message when the librespot device never appears.

        Reads the librespot log to distinguish the common failure modes so the
        error surfaced to callers (and karaoke-gen) is actionable rather than a
        bare "device not found".
        """
        tail = ""
        try:
            if log_path.exists():
                tail = log_path.read_text()[-500:]
        except OSError:
            pass

        base = f"Device '{LIBRESPOT_DEVICE_NAME}' not found in Spotify"
        if "INVALID_CREDENTIALS" in tail:
            return (
                f"{base}: librespot could not register as a Spotify Connect "
                "device (spirc login denied: INVALID_CREDENTIALS). The stored "
                "librespot credentials are missing or stale -- the credential "
                "keeper needs to re-run librespot's OAuth flow. "
                f"librespot log: {tail}"
            )
        if tail:
            return f"{base}. librespot log: {tail}"
        return base

    def _wait_for_device(self, sp: "spotipy.Spotify", timeout: int = 15) -> Optional[dict]:
        """Wait for librespot device to appear in Spotify devices list."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                devices = sp.devices()
                device_list: list = devices.get("devices", [])
                for device in device_list:
                    if device["name"] == LIBRESPOT_DEVICE_NAME:
                        return dict(device)
            except Exception as e:
                logger.debug(f"Device check error: {e}")
            time.sleep(1)
        return None

    def _wait_for_track_load(
        self, sp: "spotipy.Spotify", track_uri: str, timeout: int = 30
    ) -> bool:
        """Wait for track to load on the device."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                pb = sp.current_playback()
                if pb and pb.get("item", {}).get("uri") == track_uri:
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    def _wait_for_download(
        self,
        pcm_path: Path,
        duration_secs: int,
        process: subprocess.Popen,
        timeout: int,
    ) -> None:
        """Wait for PCM download to complete by monitoring file size."""
        expected_size = duration_secs * SAMPLE_RATE * CHANNELS * (BIT_DEPTH // 8)
        start = time.time()
        last_size = 0
        stall_count = 0

        while time.time() - start < timeout:
            # Check if process died
            if process.poll() is not None:
                logger.warning("librespot exited early")
                break

            if not pcm_path.exists():
                time.sleep(0.5)
                continue

            current_size = pcm_path.stat().st_size

            # Progress logging
            if current_size > last_size:
                pct = min(100, (current_size / expected_size) * 100)
                logger.debug(f"Download progress: {pct:.0f}%")
                last_size = current_size
                stall_count = 0

                # If we have enough data, we're done
                if current_size >= expected_size * 0.95:
                    logger.debug("Download complete (95%+ captured)")
                    return
            else:
                stall_count += 1
                # If stalled for a while but we have significant data, consider done
                if stall_count > 5 and current_size > expected_size * 0.8:
                    logger.debug("Download complete (stalled with 80%+ data)")
                    return

            time.sleep(1)

        # Timeout reached, but check if we got enough data
        if pcm_path.exists():
            final_size = pcm_path.stat().st_size
            if final_size >= expected_size * 0.7:
                logger.warning(f"Download may be incomplete: timeout but captured {final_size/expected_size*100:.0f}% of expected data")
                return

        raise SpotifyDownloadError("Download timeout")

    def _stop_librespot(self, process: subprocess.Popen) -> None:
        """Stop librespot process gracefully."""
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def _convert_pcm_to_flac(self, input_path: Path, output_path: Path) -> bool:
        """Convert raw PCM to FLAC using ffmpeg."""
        cmd = [
            "ffmpeg", "-y",
            "-f", "s16le",
            "-ar", str(SAMPLE_RATE),
            "-ac", str(CHANNELS),
            "-i", str(input_path),
            "-c:a", "flac",
            str(output_path),
        ]

        logger.debug(f"Converting to FLAC: {output_path}")
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            logger.error(f"ffmpeg error: {result.stderr}")
            return False

        return output_path.exists()

    def _extract_track_id(self, url_or_uri: str) -> Optional[str]:
        """Extract Spotify track ID from URI or URL."""
        # spotify:track:XXXX format
        if url_or_uri.startswith("spotify:track:"):
            return url_or_uri.split(":")[-1]

        # https://open.spotify.com/track/XXXX format
        url_match = re.search(r"open\.spotify\.com/track/([a-zA-Z0-9]+)", url_or_uri)
        if url_match:
            return url_match.group(1)

        # Raw track ID (22 alphanumeric chars)
        if re.match(r"^[a-zA-Z0-9]{22}$", url_or_uri):
            return url_or_uri

        return None

    def _sanitize_filename(self, name: str) -> str:
        """Remove invalid filename characters."""
        if not name:
            return "Unknown"

        # Remove invalid characters
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, "_")

        # Remove control characters
        name = "".join(c for c in name if ord(c) >= 32)
        name = name.strip(" .")

        # Limit length
        if len(name) > 200:
            name = name[:200]

        return name or "Unknown"


def is_librespot_available() -> bool:
    """Check if librespot binary is available.

    Returns:
        True if librespot is found in PATH or common locations
    """
    return find_librespot() is not None
