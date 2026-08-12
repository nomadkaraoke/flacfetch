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

# librespot stored credentials (reusable Spotify Connect credentials).
# Kept on the persistent data disk so they survive redeploys/re-provisions and
# are shared between the download service and the credential keeper. librespot
# reads/writes ``credentials.json`` inside this directory when passed ``-c``.
SERVER_LIBRESPOT_CREDENTIALS_DIR = "/mnt/flacfetch-data/browser-profiles/librespot"


# =============================================================================
# Local paths (used when running CLI on user's machine)
# =============================================================================

LOCAL_SPOTIFY_CACHE_PATH = os.path.expanduser("~/.cache-spotipy")
LOCAL_YOUTUBE_COOKIES_PATH = os.path.expanduser("~/.flacfetch/youtube_cookies.txt")
LOCAL_LIBRESPOT_CREDENTIALS_DIR = os.path.expanduser("~/.flacfetch/librespot")


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


def get_librespot_credentials_dir(local: bool = False) -> str:
    """Get the directory holding librespot's stored Spotify credentials.

    librespot reads/writes ``credentials.json`` inside this directory. These
    reusable credentials are minted by librespot's own OAuth client, which is
    the only client Spotify accepts for Spotify Connect (spirc) device login --
    third-party access tokens are rejected there with ``INVALID_CREDENTIALS``.

    Args:
        local: If True, return the local machine path (~/.flacfetch/librespot).
               If False, return the server path or env var override.

    Returns:
        Absolute path to the librespot credentials cache directory.
    """
    if local:
        return LOCAL_LIBRESPOT_CREDENTIALS_DIR
    return os.environ.get("LIBRESPOT_CREDENTIALS_DIR", SERVER_LIBRESPOT_CREDENTIALS_DIR)
