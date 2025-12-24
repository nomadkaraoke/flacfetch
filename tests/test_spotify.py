"""Tests for Spotify provider and downloader.

These tests mock external dependencies (spotipy, librespot subprocess) to allow
testing without actual Spotify credentials.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from flacfetch.core.models import AudioFormat, Quality, Release, TrackQuery
from flacfetch.downloaders.spotify import (
    LibrespotNotFoundError,
    SpotifyDownloader,
    SpotifyDownloadError,
    find_librespot,
    is_librespot_available,
)
from flacfetch.providers.spotify import (
    SpotifyAuthError,
    SpotifyProvider,
    is_spotify_configured,
)

# Sample Spotify track response (from Spotify Web API)
SAMPLE_TRACK = {
    "id": "4PTG3Z6ehGkBFwjybzWkR8",
    "name": "Never Gonna Give You Up",
    "uri": "spotify:track:4PTG3Z6ehGkBFwjybzWkR8",
    "artists": [
        {"name": "Rick Astley"},
    ],
    "album": {
        "name": "Whenever You Need Somebody",
        "album_type": "album",
        "release_date": "1987-11-16",
    },
    "duration_ms": 213573,
    "popularity": 87,
}

SAMPLE_TRACK_MULTIPLE_ARTISTS = {
    "id": "abc123def456789012345",
    "name": "Collaboration Track",
    "uri": "spotify:track:abc123def456789012345",
    "artists": [
        {"name": "Artist One"},
        {"name": "Artist Two"},
        {"name": "Artist Three"},
    ],
    "album": {
        "name": "Collab Album",
        "album_type": "single",
        "release_date": "2023-06-15",
    },
    "duration_ms": 180000,
    "popularity": 65,
}

SAMPLE_SEARCH_RESPONSE = {
    "tracks": {
        "items": [SAMPLE_TRACK, SAMPLE_TRACK_MULTIPLE_ARTISTS],
    }
}


class TestSpotifyProvider:
    """Test Spotify provider functionality."""

    def test_provider_name(self):
        """Provider should return 'Spotify' as name."""
        provider = SpotifyProvider()
        assert provider.name == "Spotify"

    def test_search_returns_empty_when_not_authenticated(self):
        """Search should return empty list if authentication fails."""
        provider = SpotifyProvider()

        with patch.object(provider, "_get_client", side_effect=SpotifyAuthError("No credentials")):
            results = provider.search(TrackQuery(artist="Test", title="Song"))
            assert results == []

    def test_search_returns_releases(self):
        """Search should return Release objects from Spotify results."""
        provider = SpotifyProvider()

        mock_sp = MagicMock()
        mock_sp.search.return_value = {"tracks": {"items": [SAMPLE_TRACK]}}
        mock_sp.current_user.return_value = {"display_name": "Test User", "id": "testuser"}

        with patch.object(provider, "_get_client", return_value=mock_sp):
            results = provider.search(
                TrackQuery(artist="Rick Astley", title="Never Gonna Give You Up")
            )

            assert len(results) == 1
            release = results[0]

            assert release.source_name == "Spotify"
            assert release.artist == "Rick Astley"
            assert release.target_file == "Never Gonna Give You Up"
            assert release.title == "Whenever You Need Somebody"
            assert release.year == 1987
            assert release.release_type == "Album"
            assert release.download_url == "spotify:track:4PTG3Z6ehGkBFwjybzWkR8"
            # New implementation outputs FLAC (after conversion)
            assert release.quality.format == AudioFormat.FLAC

    def test_search_handles_multiple_artists(self):
        """Search should join multiple artists with comma."""
        provider = SpotifyProvider()

        mock_sp = MagicMock()
        mock_sp.search.return_value = {"tracks": {"items": [SAMPLE_TRACK_MULTIPLE_ARTISTS]}}
        mock_sp.current_user.return_value = {"display_name": "Test User"}

        with patch.object(provider, "_get_client", return_value=mock_sp):
            results = provider.search(
                TrackQuery(artist="Artist One", title="Collaboration Track")
            )

            assert len(results) == 1
            assert results[0].artist == "Artist One, Artist Two, Artist Three"
            assert results[0].release_type == "Single"

    def test_search_handles_missing_fields(self):
        """Search should handle tracks with missing optional fields."""
        minimal_track = {
            "id": "minimal123456789012345",
            "name": "Minimal Track",
            "uri": "spotify:track:minimal123456789012345",
            "artists": [{"name": "Artist"}],
            "album": {"name": "Album"},
        }

        provider = SpotifyProvider()

        mock_sp = MagicMock()
        mock_sp.search.return_value = {"tracks": {"items": [minimal_track]}}
        mock_sp.current_user.return_value = {"display_name": "Test User"}

        with patch.object(provider, "_get_client", return_value=mock_sp):
            results = provider.search(TrackQuery(artist="Artist", title="Minimal Track"))

            assert len(results) == 1
            assert results[0].year is None
            assert results[0].duration_seconds is None

    def test_search_calculates_match_score(self):
        """Search should calculate match score based on track title."""
        provider = SpotifyProvider()

        mock_sp = MagicMock()
        mock_sp.search.return_value = {"tracks": {"items": [SAMPLE_TRACK]}}
        mock_sp.current_user.return_value = {"display_name": "Test User"}

        with patch.object(provider, "_get_client", return_value=mock_sp):
            results = provider.search(
                TrackQuery(artist="Rick Astley", title="Never Gonna Give You Up")
            )

            assert len(results) == 1
            assert results[0].match_score > 0.9

    def test_search_handles_api_error(self):
        """Search should return empty list on API errors."""
        provider = SpotifyProvider()

        mock_sp = MagicMock()
        mock_sp.search.return_value = {}
        mock_sp.current_user.return_value = {"display_name": "Test User"}

        with patch.object(provider, "_get_client", return_value=mock_sp):
            results = provider.search(TrackQuery(artist="Test", title="Track"))
            assert results == []

    def test_search_handles_exception(self):
        """Search should return empty list on exceptions."""
        provider = SpotifyProvider()

        mock_sp = MagicMock()
        mock_sp.search.side_effect = Exception("Network error")
        mock_sp.current_user.return_value = {"display_name": "Test User"}

        with patch.object(provider, "_get_client", return_value=mock_sp):
            results = provider.search(TrackQuery(artist="Test", title="Track"))
            assert results == []

    def test_track_without_id_is_skipped(self):
        """Tracks without ID should be skipped."""
        track_no_id = {
            "name": "No ID Track",
            "uri": "spotify:track:",
            "artists": [{"name": "Artist"}],
            "album": {"name": "Album"},
        }

        provider = SpotifyProvider()

        mock_sp = MagicMock()
        mock_sp.search.return_value = {"tracks": {"items": [track_no_id]}}
        mock_sp.current_user.return_value = {"display_name": "Test User"}

        with patch.object(provider, "_get_client", return_value=mock_sp):
            results = provider.search(TrackQuery(artist="Artist", title="No ID Track"))
            assert results == []

    def test_get_access_token(self):
        """Should return access token from auth manager."""
        provider = SpotifyProvider()

        mock_auth = MagicMock()
        mock_auth.get_cached_token.return_value = {"access_token": "test_token_123", "refresh_token": "refresh"}
        mock_auth.is_token_expired.return_value = False

        provider._auth_manager = mock_auth
        provider._sp = MagicMock()  # Simulate being authenticated

        token = provider.get_access_token()
        assert token == "test_token_123"

    def test_get_access_token_refreshes_expired(self):
        """Should refresh expired token."""
        provider = SpotifyProvider()

        mock_auth = MagicMock()
        mock_auth.get_cached_token.return_value = {"access_token": "old_token", "refresh_token": "refresh"}
        mock_auth.is_token_expired.return_value = True
        mock_auth.refresh_access_token.return_value = {"access_token": "new_token", "refresh_token": "refresh"}

        provider._auth_manager = mock_auth
        provider._sp = MagicMock()

        token = provider.get_access_token()
        assert token == "new_token"
        mock_auth.refresh_access_token.assert_called_once_with("refresh")


class TestSpotifyDownloader:
    """Test Spotify downloader functionality."""

    def test_extract_track_id_uri(self):
        """Should extract track ID from Spotify URI."""
        downloader = SpotifyDownloader()
        track_id = downloader._extract_track_id("spotify:track:4PTG3Z6ehGkBFwjybzWkR8")
        assert track_id == "4PTG3Z6ehGkBFwjybzWkR8"

    def test_extract_track_id_url(self):
        """Should extract track ID from Spotify URL."""
        downloader = SpotifyDownloader()

        track_id = downloader._extract_track_id(
            "https://open.spotify.com/track/4PTG3Z6ehGkBFwjybzWkR8"
        )
        assert track_id == "4PTG3Z6ehGkBFwjybzWkR8"

        track_id = downloader._extract_track_id(
            "https://open.spotify.com/track/4PTG3Z6ehGkBFwjybzWkR8?si=abc123"
        )
        assert track_id == "4PTG3Z6ehGkBFwjybzWkR8"

    def test_extract_track_id_raw_id(self):
        """Should accept raw 22-char track ID."""
        downloader = SpotifyDownloader()
        track_id = downloader._extract_track_id("4PTG3Z6ehGkBFwjybzWkR8")
        assert track_id == "4PTG3Z6ehGkBFwjybzWkR8"

    def test_extract_track_id_invalid(self):
        """Should return None for invalid inputs."""
        downloader = SpotifyDownloader()

        assert downloader._extract_track_id("") is None
        assert downloader._extract_track_id("not-a-valid-id") is None
        assert downloader._extract_track_id("https://youtube.com/watch?v=abc") is None

    def test_sanitize_filename(self):
        """Should remove invalid filename characters."""
        downloader = SpotifyDownloader()

        assert downloader._sanitize_filename('Track: "Name"') == "Track_ _Name_"
        assert downloader._sanitize_filename("A/B\\C") == "A_B_C"
        assert downloader._sanitize_filename("Normal Name") == "Normal Name"
        assert downloader._sanitize_filename("  Trimmed  ") == "Trimmed"
        assert downloader._sanitize_filename("") == "Unknown"
        assert downloader._sanitize_filename(None) == "Unknown"

    def test_sanitize_filename_length_limit(self):
        """Should truncate long filenames."""
        downloader = SpotifyDownloader()
        long_name = "A" * 300
        result = downloader._sanitize_filename(long_name)
        assert len(result) <= 200

    def test_download_raises_without_provider(self):
        """Should raise error when provider not configured."""
        downloader = SpotifyDownloader(provider=None)
        # Mock librespot as available so we can test the provider check
        downloader._librespot_path = "/fake/librespot"
        release = Release(
            title="Test",
            artist="Test",
            quality=Quality(AudioFormat.FLAC),
            source_name="Spotify",
            download_url="spotify:track:4PTG3Z6ehGkBFwjybzWkR8",
        )

        with pytest.raises(SpotifyDownloadError, match="SpotifyProvider not configured"):
            downloader.download(release, "/tmp")

    def test_download_raises_without_librespot(self):
        """Should raise error when librespot not found."""
        mock_provider = MagicMock()
        downloader = SpotifyDownloader(provider=mock_provider)
        downloader._librespot_path = None  # Simulate not found

        release = Release(
            title="Test",
            artist="Test",
            quality=Quality(AudioFormat.FLAC),
            source_name="Spotify",
            download_url="spotify:track:4PTG3Z6ehGkBFwjybzWkR8",
        )

        with pytest.raises(LibrespotNotFoundError):
            downloader.download(release, "/tmp")

    def test_download_invalid_url_raises(self):
        """Should raise error for missing download URL."""
        mock_provider = MagicMock()
        downloader = SpotifyDownloader(provider=mock_provider)
        downloader._librespot_path = "/fake/librespot"

        release = Release(
            title="Test",
            artist="Test",
            quality=Quality(AudioFormat.FLAC),
            source_name="Spotify",
            download_url=None,
        )

        with pytest.raises(SpotifyDownloadError, match="no download URL"):
            downloader.download(release, "/tmp")

    def test_download_invalid_uri_raises(self):
        """Should raise error for invalid Spotify URI."""
        mock_provider = MagicMock()
        downloader = SpotifyDownloader(provider=mock_provider)
        downloader._librespot_path = "/fake/librespot"

        release = Release(
            title="Test",
            artist="Test",
            quality=Quality(AudioFormat.FLAC),
            source_name="Spotify",
            download_url="https://youtube.com/watch?v=123",
        )

        with pytest.raises(SpotifyDownloadError, match="Invalid Spotify URI"):
            downloader.download(release, "/tmp")


class TestSpotifyIntegration:
    """Integration tests for provider and downloader working together."""

    def test_provider_release_works_with_downloader(self):
        """Release from provider should be valid for downloader."""
        provider = SpotifyProvider()
        downloader = SpotifyDownloader(provider=provider)

        mock_sp = MagicMock()
        mock_sp.search.return_value = {"tracks": {"items": [SAMPLE_TRACK]}}
        mock_sp.current_user.return_value = {"display_name": "Test User"}

        with patch.object(provider, "_get_client", return_value=mock_sp):
            results = provider.search(
                TrackQuery(artist="Rick Astley", title="Never Gonna Give You Up")
            )

            assert len(results) == 1
            release = results[0]

            assert release.download_url is not None
            assert release.download_url.startswith("spotify:track:")

            track_id = downloader._extract_track_id(release.download_url)
            assert track_id == "4PTG3Z6ehGkBFwjybzWkR8"


class TestLibrespotDetection:
    """Test librespot binary detection."""

    def test_find_librespot_in_path(self):
        """Should find librespot via shutil.which."""
        with patch("shutil.which", return_value="/usr/local/bin/librespot"):
            with patch("os.path.isfile", return_value=True):
                result = find_librespot()
                assert result == "/usr/local/bin/librespot"

    def test_find_librespot_homebrew(self):
        """Should find librespot in Homebrew location."""
        with patch("shutil.which", return_value=None):
            with patch("os.path.isfile", side_effect=lambda p: p == "/opt/homebrew/bin/librespot"):
                result = find_librespot()
                assert result == "/opt/homebrew/bin/librespot"

    def test_find_librespot_cargo(self):
        """Should find librespot in Cargo location."""
        home = os.path.expanduser("~")
        cargo_path = f"{home}/.cargo/bin/librespot"

        with patch("shutil.which", return_value=None):
            with patch("os.path.isfile", side_effect=lambda p: p == cargo_path):
                result = find_librespot()
                assert result == cargo_path

    def test_find_librespot_not_found(self):
        """Should return None when librespot not found."""
        with patch("shutil.which", return_value=None):
            with patch("os.path.isfile", return_value=False):
                result = find_librespot()
                assert result is None

    def test_is_librespot_available(self):
        """Should return True when librespot is found."""
        with patch("flacfetch.downloaders.spotify.find_librespot", return_value="/usr/bin/librespot"):
            assert is_librespot_available() is True

        with patch("flacfetch.downloaders.spotify.find_librespot", return_value=None):
            assert is_librespot_available() is False


class TestSpotifyConfigCheck:
    """Test Spotify configuration detection."""

    def test_is_spotify_configured_with_env_vars(self):
        """Should return True when env vars are set."""
        with patch.dict(os.environ, {
            "SPOTIPY_CLIENT_ID": "test_id",
            "SPOTIPY_CLIENT_SECRET": "test_secret",
        }):
            assert is_spotify_configured() is True

    def test_is_spotify_configured_missing_id(self):
        """Should return False when client ID is missing."""
        with patch.dict(os.environ, {"SPOTIPY_CLIENT_SECRET": "test_secret"}, clear=True):
            os.environ.pop("SPOTIPY_CLIENT_ID", None)
            assert is_spotify_configured() is False

    def test_is_spotify_configured_missing_secret(self):
        """Should return False when client secret is missing."""
        with patch.dict(os.environ, {"SPOTIPY_CLIENT_ID": "test_id"}, clear=True):
            os.environ.pop("SPOTIPY_CLIENT_SECRET", None)
            assert is_spotify_configured() is False

    def test_is_spotify_configured_empty_vars(self):
        """Should return False when env vars are empty."""
        with patch.dict(os.environ, {
            "SPOTIPY_CLIENT_ID": "",
            "SPOTIPY_CLIENT_SECRET": "",
        }):
            assert is_spotify_configured() is False


class TestAudioFormatOutput:
    """Test that output format is properly configured."""

    def test_output_is_flac(self):
        """Provider should report FLAC as output format (after conversion)."""
        provider = SpotifyProvider()

        mock_sp = MagicMock()
        mock_sp.search.return_value = {"tracks": {"items": [SAMPLE_TRACK]}}
        mock_sp.current_user.return_value = {"display_name": "Test User"}

        with patch.object(provider, "_get_client", return_value=mock_sp):
            results = provider.search(
                TrackQuery(artist="Rick Astley", title="Never Gonna Give You Up")
            )

            assert len(results) == 1
            assert results[0].quality.format == AudioFormat.FLAC
            # FLAC is lossless
            assert results[0].quality.is_lossless() is True

    def test_flac_beats_vorbis(self):
        """FLAC should always beat VORBIS regardless of bitrate."""
        flac = Quality(format=AudioFormat.FLAC, bit_depth=16)
        vorbis_320 = Quality(format=AudioFormat.VORBIS, bitrate=320)

        assert vorbis_320 < flac
