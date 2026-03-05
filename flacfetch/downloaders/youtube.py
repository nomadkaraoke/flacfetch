import logging
import os
from dataclasses import dataclass
from typing import Optional

import yt_dlp  # type: ignore

from ..core.interfaces import Downloader
from ..core.models import Release

logger = logging.getLogger(__name__)


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


@dataclass
class YoutubeAvailability:
    """Result of a YouTube video availability check."""
    available: bool
    video_id: str
    title: Optional[str] = None
    error: Optional[str] = None
    is_geo_restricted: bool = False
    is_age_restricted: bool = False
    is_private: bool = False
    is_removed: bool = False


def extract_video_id(url_or_id: str) -> str:
    """Extract YouTube video ID from a URL or return the ID if already bare."""
    import re

    # Already a bare video ID (11 chars, alphanumeric + hyphen + underscore)
    if re.match(r'^[\w-]{11}$', url_or_id):
        return url_or_id

    # Try to extract from various YouTube URL formats
    patterns = [
        r'(?:youtube\.com/watch\?.*v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/)([\w-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url_or_id)
        if match:
            return match.group(1)

    # Return as-is if we can't parse it (let yt-dlp handle the error)
    return url_or_id


def check_youtube_availability(
    url_or_id: str,
    cookies_file: Optional[str] = None,
) -> YoutubeAvailability:
    """
    Check if a YouTube video is available for download from the current location.

    Uses yt-dlp to extract info without downloading. Detects geo-restrictions,
    age restrictions, private/removed videos, and other unavailability reasons.

    Args:
        url_or_id: YouTube video URL or video ID
        cookies_file: Optional path to cookies file for authenticated access

    Returns:
        YoutubeAvailability with status details
    """
    video_id = extract_video_id(url_or_id)
    url = f"https://www.youtube.com/watch?v={video_id}"

    ydl_opts = get_ytdlp_base_opts(cookies_file)
    ydl_opts.update({
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
    })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info:
                return YoutubeAvailability(
                    available=True,
                    video_id=video_id,
                    title=info.get('title'),
                )
            return YoutubeAvailability(
                available=False,
                video_id=video_id,
                error="No video info returned",
            )

    except yt_dlp.utils.GeoRestrictedError as e:
        logger.info(f"YouTube video {video_id} is geo-restricted: {e}")
        return YoutubeAvailability(
            available=False,
            video_id=video_id,
            error="This video is not available in your server's country/region. "
                  "The video may be available in the uploader's country but restricted elsewhere.",
            is_geo_restricted=True,
        )

    except (yt_dlp.utils.ExtractorError, yt_dlp.utils.DownloadError) as e:
        error_str = str(e).lower()
        result = YoutubeAvailability(
            available=False,
            video_id=video_id,
            error=str(e),
        )

        # Detect specific error types from the error message
        if any(phrase in error_str for phrase in [
            'video unavailable', 'video is unavailable',
            'this video is not available',
            'not available in your country',
        ]):
            # Could be geo-restriction even if not explicitly raised as GeoRestrictedError
            if 'country' in error_str or 'geo' in error_str:
                result.is_geo_restricted = True
                result.error = (
                    "This video is not available in your server's country/region. "
                    "The video may be available in the uploader's country but restricted elsewhere."
                )
            else:
                # Generic "unavailable" - could be geo, removed, or other
                result.error = (
                    "This YouTube video is unavailable. This could be due to geographic "
                    "restrictions (video available in uploader's country but not in the "
                    "server's country), or the video may have been removed/made private."
                )
                result.is_geo_restricted = True  # Flag as possible geo since that's a common cause

        if 'private' in error_str:
            result.is_private = True
            result.is_geo_restricted = False

        if any(phrase in error_str for phrase in ['age', 'sign in']):
            result.is_age_restricted = True
            result.is_geo_restricted = False

        if any(phrase in error_str for phrase in [
            'has been removed', 'deleted', 'terminated',
            'copyright', 'no longer available',
        ]):
            result.is_removed = True
            result.is_geo_restricted = False

        return result

    except Exception as e:
        logger.warning(f"Unexpected error checking YouTube availability for {video_id}: {e}")
        return YoutubeAvailability(
            available=False,
            video_id=video_id,
            error=str(e),
        )


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

