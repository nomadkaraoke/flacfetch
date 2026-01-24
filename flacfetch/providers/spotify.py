"""Spotify provider for flacfetch.

This module provides search functionality for Spotify using the official Web API
via spotipy. Requires Spotify Premium for downloading (handled by downloader).

Authentication: OAuth2 via spotipy (browser-based login, cached automatically)
Quality: CD-quality FLAC output (44.1kHz/16-bit) via librespot capture
"""

import os
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth

from ..core.config import get_spotify_cache_path
from ..core.interfaces import Provider
from ..core.log import get_logger
from ..core.matching import calculate_match_score
from ..core.models import AudioFormat, MediaSource, Quality, Release, TrackQuery

logger = get_logger("SpotifyProvider")

# Required OAuth scopes for playback control
SPOTIFY_SCOPES = [
    "user-read-playback-state",
    "user-modify-playback-state",
    "streaming",
]


class SpotifyAuthError(Exception):
    """Raised when Spotify authentication fails."""

    pass


class SpotifyProvider(Provider):
    """Provider for Spotify streaming service.

    Uses the official Spotify Web API for search. Requires:
    - Spotify Developer App (Client ID + Secret)
    - OAuth2 authentication (browser login, cached automatically)
    - Spotify Premium account (for downloading via SpotifyDownloader)

    Environment variables:
        SPOTIPY_CLIENT_ID: Spotify app client ID
        SPOTIPY_CLIENT_SECRET: Spotify app client secret
        SPOTIPY_REDIRECT_URI: OAuth redirect URI (e.g. http://127.0.0.1:8888/callback)
    """

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        cache_path: Optional[str] = None,
    ):
        """Initialize the Spotify provider.

        Args:
            client_id: Spotify app client ID (or use SPOTIPY_CLIENT_ID env var)
            client_secret: Spotify app client secret (or use SPOTIPY_CLIENT_SECRET env var)
            cache_path: Path to OAuth token cache file (default: from config)
        """
        self._client_id = client_id
        self._client_secret = client_secret
        self._cache_path = cache_path or get_spotify_cache_path()
        self._sp: Optional["spotipy.Spotify"] = None
        self._auth_manager: Optional["SpotifyOAuth"] = None
        self._cache_mtime: Optional[float] = None  # Track file modification time
        self._search_limit = 10

    def invalidate(self) -> None:
        """Clear cached client to force re-authentication on next use.

        Call this after updating the OAuth token file to pick up new credentials
        without restarting the server.
        """
        self._sp = None
        self._auth_manager = None
        self._cache_mtime = None
        logger.info("SpotifyProvider invalidated, will reload credentials on next use")

    @property
    def name(self) -> str:
        return "Spotify"

    def _get_client(self) -> "spotipy.Spotify":
        """Get or create authenticated Spotify client.

        Raises SpotifyAuthError immediately if no cached token exists,
        preventing blocking browser-based OAuth on headless servers.

        Automatically detects when the cache file has been updated and
        reloads credentials without requiring a server restart.
        """
        # Check if cache file changed since last load (hot-reload support)
        if self._sp is not None and self._cache_mtime is not None:
            try:
                current_mtime = os.path.getmtime(self._cache_path)
                if current_mtime != self._cache_mtime:
                    logger.info("Spotify token file changed, reloading credentials...")
                    self.invalidate()
            except OSError:
                pass  # File may not exist, continue with existing client

        if self._sp is not None:
            return self._sp

        try:
            import spotipy
            from spotipy.oauth2 import SpotifyOAuth
        except ImportError as e:
            raise SpotifyAuthError(
                "spotipy not installed. Install with: pip install spotipy"
            ) from e

        try:
            self._auth_manager = SpotifyOAuth(
                client_id=self._client_id,
                client_secret=self._client_secret,
                scope=" ".join(SPOTIFY_SCOPES),
                cache_path=self._cache_path,
            )

            # Check for cached token BEFORE any API call that might trigger
            # interactive browser-based OAuth (which blocks on headless servers)
            cached_token = self._auth_manager.get_cached_token()
            if not cached_token:
                raise SpotifyAuthError(
                    "No cached OAuth token. Run flacfetch interactively once to "
                    "authenticate with Spotify, or disable Spotify on this server."
                )

            # Refresh if expired (this uses the cached refresh_token, no browser needed)
            if self._auth_manager.is_token_expired(cached_token):
                logger.info("Refreshing expired Spotify token...")
                cached_token = self._auth_manager.refresh_access_token(
                    cached_token["refresh_token"]
                )

            self._sp = spotipy.Spotify(auth_manager=self._auth_manager)

            # Track file mtime for hot-reload detection
            try:
                self._cache_mtime = os.path.getmtime(self._cache_path)
            except OSError:
                self._cache_mtime = None

            # Verify auth works
            user = self._sp.current_user()
            logger.info(f"Authenticated as: {user.get('display_name', user.get('id'))}")

            return self._sp

        except SpotifyAuthError:
            raise  # Re-raise our own errors
        except Exception as e:
            raise SpotifyAuthError(f"Spotify authentication failed: {e}") from e

    def get_access_token(self) -> str:
        """Get current OAuth access token for use by downloader.

        Returns:
            Valid OAuth access token string

        Raises:
            SpotifyAuthError: If token cannot be obtained
        """
        self._get_client()  # Ensure we're authenticated

        if self._auth_manager is None:
            raise SpotifyAuthError("Auth manager not initialized")

        token_info = self._auth_manager.get_cached_token()
        if not token_info:
            raise SpotifyAuthError("No cached token available")

        # Refresh if expired
        if self._auth_manager.is_token_expired(token_info):
            token_info = self._auth_manager.refresh_access_token(token_info["refresh_token"])

        access_token: str = token_info["access_token"]
        return access_token

    def search(self, query: TrackQuery) -> list[Release]:
        """Search Spotify for tracks matching the query.

        Args:
            query: TrackQuery with artist and title

        Returns:
            List of Release objects matching the query
        """
        try:
            sp = self._get_client()
        except SpotifyAuthError as e:
            logger.warning(f"Spotify not available: {e}")
            return []

        search_query = f"{query.artist} {query.title}".strip()
        logger.info(f"Searching Spotify for: {search_query}")

        try:
            results = sp.search(q=search_query, type="track", limit=self._search_limit)
            tracks = results.get("tracks", {}).get("items", [])

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

            # Spotify URI for download
            spotify_uri = f"spotify:track:{track_id}"

            # Quality - librespot captures at 44.1kHz/16-bit, converted to FLAC
            quality = Quality(
                format=AudioFormat.FLAC,  # Output format after conversion
                bitrate=None,  # Lossless
                bit_depth=16,
                sample_rate=44100,
                media=MediaSource.WEB,
            )

            # Duration and file size estimate (FLAC ~900kbps average)
            duration_ms = track.get("duration_ms", 0)
            duration_secs = duration_ms // 1000 if duration_ms else None
            estimated_size = int(duration_secs * 900 * 1000 / 8) if duration_secs else None

            # Match score
            match_score = calculate_match_score(query.title, track_name)

            # Popularity (0-100) - scale for sorting compatibility
            popularity = track.get("popularity", 0)

            # Release type mapping
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
                source_id=track_id,
            )

        except Exception as e:
            logger.warning(f"Failed to parse track: {e}")
            return None


def is_spotify_configured() -> bool:
    """Check if Spotify credentials are configured via environment variables.

    Returns:
        True if SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET are set
    """
    import os

    return bool(
        os.environ.get("SPOTIPY_CLIENT_ID") and os.environ.get("SPOTIPY_CLIENT_SECRET")
    )
