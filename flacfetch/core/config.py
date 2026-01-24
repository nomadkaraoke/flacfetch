"""Centralized configuration for credential paths and settings.

This module provides a single source of truth for all credential file paths,
ensuring consistency across the codebase and eliminating duplicate path definitions.
"""
import os

# =============================================================================
# Server paths (used when running as API service on GCE)
# =============================================================================

SPOTIFY_CACHE_PATH = "/opt/flacfetch/.cache"
YOUTUBE_COOKIES_PATH = "/opt/flacfetch/youtube_cookies.txt"


# =============================================================================
# Local paths (used when running CLI on user's machine)
# =============================================================================

LOCAL_SPOTIFY_CACHE_PATH = os.path.expanduser("~/.cache-spotipy")
LOCAL_YOUTUBE_COOKIES_PATH = os.path.expanduser("~/.flacfetch/youtube_cookies.txt")


# =============================================================================
# Path getter functions
# =============================================================================


def get_spotify_cache_path(local: bool = False) -> str:
    """Get Spotify OAuth cache path.

    Args:
        local: If True, return the local machine path (~/.cache-spotipy).
               If False, return the server path or env var override.

    Returns:
        Absolute path to the Spotify OAuth cache file.
    """
    if local:
        return LOCAL_SPOTIFY_CACHE_PATH
    return os.environ.get("SPOTIFY_CACHE_PATH", SPOTIFY_CACHE_PATH)


def get_youtube_cookies_path(local: bool = False) -> str:
    """Get YouTube cookies file path.

    Args:
        local: If True, return the local machine path (~/.flacfetch/youtube_cookies.txt).
               If False, return the server path or env var override.

    Returns:
        Absolute path to the YouTube cookies file.
    """
    if local:
        return LOCAL_YOUTUBE_COOKIES_PATH
    return os.environ.get("YOUTUBE_COOKIES_FILE", YOUTUBE_COOKIES_PATH)
