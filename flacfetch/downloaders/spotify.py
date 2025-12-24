"""Spotify downloader for flacfetch.

This module provides download functionality for Spotify tracks using the
librespot protocol via zotify. Downloads are saved as OGG Vorbis at 320kbps.
"""

import os
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

from ..core.interfaces import Downloader
from ..core.log import get_logger
from ..core.models import Release

logger = get_logger("SpotifyDownloader")


class SpotifyDownloader(Downloader):
    """Downloader for Spotify tracks using zotify/librespot.

    Downloads the encrypted stream from Spotify and decrypts it
    using the librespot protocol. Output is OGG Vorbis at 320kbps.
    """

    def __init__(
        self,
        credentials_path: Optional[str] = None,
        output_format: str = "ogg",
    ):
        """Initialize Spotify downloader.

        Args:
            credentials_path: Path to Spotify credentials.json
            output_format: Output format - 'ogg' (native, default)
        """
        self.credentials_path = self._resolve_credentials_path(credentials_path)
        self.output_format = output_format.lower()
        self._initialized = False

    def _resolve_credentials_path(self, provided_path: Optional[str]) -> Path:
        """Resolve credentials file path with fallback locations."""
        if provided_path:
            path = Path(provided_path).expanduser()
            if path.exists():
                return path
            return Path(provided_path)

        env_path = os.environ.get("SPOTIFY_CREDENTIALS_PATH")
        if env_path:
            path = Path(env_path).expanduser()
            if path.exists():
                return path

        search_paths = [
            Path.home() / ".flacfetch" / "spotify_credentials.json",
            Path.home() / ".config" / "zotify" / "credentials.json",
            Path.home() / ".zotify" / "credentials.json",
            Path.home() / "Library" / "Application Support" / "Zotify" / "credentials.json",
        ]

        for path in search_paths:
            if path.exists():
                return path

        return search_paths[0]

    def _init_zotify(self) -> bool:
        """Initialize zotify with credentials."""
        if self._initialized:
            return True

        if not self.credentials_path.exists():
            raise FileNotFoundError(
                f"Spotify credentials not found at {self.credentials_path}"
            )

        try:
            from librespot.audio.decoders import AudioQuality
            from zotify.config import Config
            from zotify.zotify import Zotify

            args = SimpleNamespace(
                config_location=None,
                username=None,
                password=None,
            )

            Config.Values = {}
            Config.Values['CREDENTIALS_LOCATION'] = str(self.credentials_path)
            Config.load(args)
            Config.Values['CREDENTIALS_LOCATION'] = str(self.credentials_path)

            Zotify.login(args)

            # Set download quality
            Zotify.DOWNLOAD_QUALITY = AudioQuality.VERY_HIGH if Zotify.check_premium() else AudioQuality.HIGH

            self._initialized = True
            return True

        except ImportError as e:
            raise ImportError(
                "zotify is not installed. Install with: pip install git+https://github.com/zotify-dev/zotify.git"
            ) from e

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
            Path to the downloaded file
        """
        if not release.download_url:
            raise ValueError("Release has no download URL")

        track_id = self._extract_track_id(release.download_url)
        if not track_id:
            raise ValueError(f"Invalid Spotify URI/URL: {release.download_url}")

        track_name = release.target_file or release.title
        logger.info(f"Downloading from Spotify: {track_name}")
        logger.debug(f"Track ID: {track_id}")

        # Determine output filename
        if output_filename:
            base_name = os.path.splitext(output_filename)[0]
        else:
            safe_artist = self._sanitize_filename(release.artist)
            safe_title = self._sanitize_filename(track_name)
            base_name = f"{safe_artist} - {safe_title}"

        os.makedirs(output_path, exist_ok=True)

        # Try zotify Python API first, fall back to CLI
        try:
            output_file = self._download_via_api(track_id, output_path, base_name)
        except Exception as e:
            logger.debug(f"API download failed: {e}, trying CLI fallback")
            output_file = self._download_via_cli(track_id, output_path, base_name)

        if not os.path.exists(output_file):
            # Search for downloaded file with various extensions
            for ext in [".ogg", ".mp3", ".m4a", ".flac"]:
                alt_file = os.path.join(output_path, f"{base_name}{ext}")
                if os.path.exists(alt_file):
                    output_file = alt_file
                    break
            else:
                # Search for any file containing the base name
                for f in os.listdir(output_path):
                    if base_name in f or track_id in f:
                        output_file = os.path.join(output_path, f)
                        break

        if not os.path.exists(output_file):
            raise RuntimeError(f"Download completed but file not found: {output_file}")

        logger.info(f"Download complete: {output_file}")
        return output_file

    def _download_via_api(self, track_id: str, output_path: str, base_name: str) -> str:
        """Download using zotify Python API."""
        self._init_zotify()

        from zotify.config import Config
        from zotify.track import download_track

        # Configure zotify output settings
        Config.Values['ROOT_PATH'] = output_path
        Config.Values['ROOT_PODCAST_PATH'] = output_path
        Config.Values['DOWNLOAD_FORMAT'] = 'ogg'
        Config.Values['SONG_ARCHIVE'] = ''  # Disable archive
        Config.Values['SKIP_EXISTING'] = False

        # Download the track
        download_track('single', track_id, disable_progressbar=True)

        # Find the downloaded file
        output_file = os.path.join(output_path, f"{base_name}.ogg")
        return output_file

    def _download_via_cli(self, track_id: str, output_path: str, base_name: str) -> str:
        """Download using zotify CLI as fallback."""
        spotify_url = f"https://open.spotify.com/track/{track_id}"

        cmd = [
            "zotify",
            spotify_url,
            "--root-path", output_path,
            "--download-quality", "very_high",
            "--download-format", "ogg",
        ]

        if self.credentials_path.exists():
            cmd.extend(["--credentials-location", str(self.credentials_path)])

        logger.debug(f"Running: {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Unknown error"
            raise RuntimeError(f"Zotify CLI failed: {error_msg}")

        return os.path.join(output_path, f"{base_name}.ogg")

    def _extract_track_id(self, url_or_uri: str) -> Optional[str]:
        """Extract Spotify track ID from URI or URL."""
        if url_or_uri.startswith("spotify:track:"):
            return url_or_uri.split(":")[-1]

        url_match = re.search(r"open\.spotify\.com/track/([a-zA-Z0-9]+)", url_or_uri)
        if url_match:
            return url_match.group(1)

        if re.match(r"^[a-zA-Z0-9]{22}$", url_or_uri):
            return url_or_uri

        return None

    def _sanitize_filename(self, name: str) -> str:
        """Remove invalid filename characters."""
        if not name:
            return "Unknown"

        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, "_")

        name = "".join(c for c in name if ord(c) >= 32)
        name = name.strip(" .")

        if len(name) > 200:
            name = name[:200]

        return name or "Unknown"
