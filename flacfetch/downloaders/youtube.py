import os
from typing import Optional

import yt_dlp  # type: ignore

from ..core.interfaces import Downloader
from ..core.models import Release


def get_cookies_file() -> Optional[str]:
    """
    Get the path to YouTube cookies file if configured.

    Checks:
    1. YOUTUBE_COOKIES_FILE environment variable
    2. Default path /opt/flacfetch/youtube_cookies.txt

    Returns:
        Path to cookies file if it exists and is readable, None otherwise
    """
    # Check environment variable first
    cookies_file = os.environ.get("YOUTUBE_COOKIES_FILE")
    if cookies_file and os.path.exists(cookies_file):
        return cookies_file

    # Check default path
    default_path = "/opt/flacfetch/youtube_cookies.txt"
    if os.path.exists(default_path):
        return default_path

    return None


def get_ytdlp_base_opts(cookies_file: Optional[str] = None) -> dict:
    """
    Get base yt-dlp options with common settings including cookies if available.

    Args:
        cookies_file: Optional path to cookies file. If None, will auto-detect.

    Returns:
        Dictionary of yt-dlp options
    """
    opts = {}

    # Add cookies if available
    if cookies_file is None:
        cookies_file = get_cookies_file()

    if cookies_file:
        opts["cookiefile"] = cookies_file

    return opts


class YoutubeDownloader(Downloader):
    """
    YouTube audio downloader using yt-dlp.

    Supports authenticated downloads via cookies when configured.
    """

    def __init__(self, cookies_file: Optional[str] = None):
        """
        Initialize YouTube downloader.

        Args:
            cookies_file: Optional path to cookies file for authenticated downloads.
                         If not provided, will auto-detect from environment.
        """
        self.cookies_file = cookies_file

    def download(self, release: Release, output_path: str, output_filename: Optional[str] = None) -> str:
        """
        Download a YouTube video/audio.

        Args:
            release: Release object to download
            output_path: Directory to save the downloaded file
            output_filename: Optional specific filename (without extension)

        Returns:
            Path to the downloaded file
        """
        print(f"Downloading from YouTube: {release.title}...")

        # Determine output template
        if output_filename:
            # Remove extension if provided
            output_name = os.path.splitext(output_filename)[0]
            outtmpl = f'{output_path}/{output_name}.%(ext)s'
        else:
            outtmpl = f'{output_path}/%(title)s.%(ext)s'

        # Get base options (includes cookies if available)
        ydl_opts = get_ytdlp_base_opts(self.cookies_file)

        # Add download-specific options
        ydl_opts.update({
            'format': 'bestaudio/best',
            'outtmpl': outtmpl,
            'quiet': False,
        })

        # Log if using cookies
        if ydl_opts.get("cookiefile"):
            print(f"Using YouTube cookies from: {ydl_opts['cookiefile']}")

        downloaded_file: str | None = None
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(release.download_url, download=True)
                # Get the actual filename that was used
                if info:
                    downloaded_file = ydl.prepare_filename(info)
            print("YouTube download complete.")
            if not downloaded_file:
                raise RuntimeError("Failed to determine downloaded filename")
            return downloaded_file
        except Exception as e:
            print(f"Error downloading from YouTube: {e}")
            raise

