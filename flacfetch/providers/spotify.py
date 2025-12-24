"""Spotify provider for flacfetch.

This module provides search functionality for Spotify using the librespot protocol
via zotify. Requires Spotify Premium credentials.

Quality: 320kbps OGG Vorbis (Premium) or 160kbps (Free)
"""

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import requests

from ..core.interfaces import Provider
from ..core.log import get_logger
from ..core.matching import calculate_match_score
from ..core.models import AudioFormat, MediaSource, Quality, Release, TrackQuery

logger = get_logger("SpotifyProvider")

# Spotify Web API search endpoint
SEARCH_URL = "https://api.spotify.com/v1/search"


class SpotifyProvider(Provider):
    """Provider for Spotify streaming service.

    Requires Spotify Premium credentials. Credentials can be generated using
    librespot-auth and should be placed at ~/.flacfetch/spotify_credentials.json
    or specified via SPOTIFY_CREDENTIALS_PATH environment variable.

    Quality: 320kbps OGG Vorbis (Premium) or 160kbps (Free)
    """

    def __init__(self, credentials_path: Optional[str] = None):
        """Initialize the Spotify provider.

        Args:
            credentials_path: Path to credentials.json from librespot-auth.
                            Defaults to ~/.flacfetch/spotify_credentials.json
        """
        self.credentials_path = self._resolve_credentials_path(credentials_path)
        self._initialized = False
        self._search_limit = 10

    @property
    def name(self) -> str:
        return "Spotify"

    def _resolve_credentials_path(self, provided_path: Optional[str]) -> Path:
        """Resolve credentials file path with fallback locations."""
        if provided_path:
            path = Path(provided_path).expanduser()
            if path.exists():
                return path
            return Path(provided_path)

        # Check environment variable
        env_path = os.environ.get("SPOTIFY_CREDENTIALS_PATH")
        if env_path:
            path = Path(env_path).expanduser()
            if path.exists():
                return path

        # Default locations in priority order
        search_paths = [
            Path.home() / ".flacfetch" / "spotify_credentials.json",
            Path.home() / ".config" / "zotify" / "credentials.json",
            Path.home() / ".zotify" / "credentials.json",
            Path.home() / "Library" / "Application Support" / "Zotify" / "credentials.json",
        ]

        for path in search_paths:
            if path.exists():
                logger.debug(f"Found Spotify credentials at: {path}")
                return path

        return search_paths[0]

    def _validate_credentials(self) -> bool:
        """Check if credentials file exists."""
        return self.credentials_path.exists()

    def _init_zotify(self) -> bool:
        """Initialize zotify with our credentials.

        Returns:
            True if initialization succeeded, False otherwise.
        """
        if self._initialized:
            return True

        if not self.credentials_path.exists():
            logger.warning(f"Spotify credentials not found at {self.credentials_path}")
            return False

        try:
            from zotify.config import Config
            from zotify.zotify import Zotify

            # Create mock args object that zotify expects
            args = SimpleNamespace(
                config_location=None,
                username=None,
                password=None,
            )

            # Set credentials location in config before loading
            # We need to set this as an environment or config override
            Config.Values = {}
            Config.Values['CREDENTIALS_LOCATION'] = str(self.credentials_path)

            # Load config
            Config.load(args)

            # Override credentials location after load
            Config.Values['CREDENTIALS_LOCATION'] = str(self.credentials_path)

            # Initialize session
            Zotify.login(args)

            self._initialized = True
            logger.info("Spotify session initialized successfully")
            return True

        except ImportError as e:
            logger.warning(f"zotify not installed: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to initialize Spotify session: {e}")
            return False

    def _get_auth_header(self) -> dict:
        """Get auth header from zotify session."""
        from zotify.zotify import Zotify
        return Zotify.get_auth_header()

    def _execute_search(self, search_query: str) -> dict:
        """Execute search against Spotify API."""
        params = {
            "q": search_query,
            "type": "track",
            "limit": str(self._search_limit),
            "offset": "0",
        }
        headers = self._get_auth_header()
        response = requests.get(SEARCH_URL, headers=headers, params=params)

        if response.status_code != 200:
            logger.error(f"Spotify API error: {response.status_code}")
            return {}

        return response.json()

    def search(self, query: TrackQuery) -> list[Release]:
        """Search Spotify for tracks matching the query.

        Args:
            query: TrackQuery with artist and title

        Returns:
            List of Release objects matching the query
        """
        if not self._init_zotify():
            return []

        search_query = f"{query.artist} {query.title}".strip()
        logger.info(f"Searching Spotify for: {search_query}")

        try:
            data = self._execute_search(search_query)
            tracks = data.get("tracks", {}).get("items", [])

            releases = []
            for track in tracks:
                release = self._track_to_release(track, query)
                if release:
                    releases.append(release)

            logger.info(f"Found {len(releases)} tracks from Spotify")
            return releases

        except Exception as e:
            logger.error(f"Spotify search error: {e}")
            return []

    def _track_to_release(self, track: dict, query: TrackQuery) -> Optional[Release]:
        """Convert Spotify track data to Release object."""
        try:
            track_id = track.get("id")
            if not track_id:
                return None

            # Extract artists
            artists = track.get("artists", [])
            artist_name = artists[0]["name"] if artists else "Unknown"
            all_artists = ", ".join(a.get("name", "") for a in artists) if len(artists) > 1 else artist_name

            # Track name
            track_name = track.get("name", "Unknown")

            # Album info
            album = track.get("album", {})
            album_name = album.get("name", "")
            album_type = album.get("album_type", "album")

            # Release year
            release_year = None
            release_date = album.get("release_date", "")
            if release_date and len(release_date) >= 4:
                try:
                    release_year = int(release_date[:4])
                except ValueError:
                    pass

            # Spotify URI
            spotify_uri = f"spotify:track:{track_id}"

            # Quality (320kbps Vorbis for Premium)
            quality = Quality(
                format=AudioFormat.VORBIS,
                bitrate=320,
                media=MediaSource.WEB,
            )

            # Duration and file size estimate
            duration_ms = track.get("duration_ms", 0)
            duration_secs = duration_ms // 1000 if duration_ms else None
            estimated_size = int(duration_secs * 320 * 1000 / 8) if duration_secs else None

            # Match score
            match_score = calculate_match_score(query.title, track_name)

            # Popularity and release type
            popularity = track.get("popularity", 0)
            release_type_map = {
                "album": "Album",
                "single": "Single",
                "compilation": "Compilation",
                "ep": "EP",
            }
            release_type = release_type_map.get(album_type.lower(), "Album")

            return Release(
                title=album_name,
                artist=all_artists,
                quality=quality,
                source_name=self.name,
                download_url=spotify_uri,
                size_bytes=estimated_size,
                year=release_year,
                release_type=release_type,
                target_file=track_name,
                duration_seconds=duration_secs,
                match_score=match_score,
                track_pattern=query.title,
                view_count=popularity * 10000,  # Scale for sorting compatibility
            )

        except Exception as e:
            logger.warning(f"Failed to parse track: {e}")
            return None
