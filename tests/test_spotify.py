"""Tests for Spotify provider and downloader.

These tests mock all external dependencies (zotify, filesystem) to allow
testing without actual Spotify credentials.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from flacfetch.core.models import AudioFormat, MediaSource, Quality, Release, TrackQuery
from flacfetch.downloaders.spotify import SpotifyDownloader
from flacfetch.providers.spotify import SpotifyProvider

# Sample Spotify track response (from Spotify Web API)
SAMPLE_TRACK = {
    "id": "4PTG3Z6ehGkBFwjybzWkR8",
    "name": "Never Gonna Give You Up",
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
    "id": "abc123def456",
    "name": "Collaboration Track",
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
        provider = SpotifyProvider("/fake/creds.json")
        assert provider.name == "Spotify"

    def test_credentials_path_resolution_explicit(self):
        """Should use explicitly provided path."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"username": "test"}, f)
            temp_path = f.name

        try:
            provider = SpotifyProvider(temp_path)
            assert provider.credentials_path == Path(temp_path)
        finally:
            os.unlink(temp_path)

    def test_credentials_path_resolution_env_var(self):
        """Should use SPOTIFY_CREDENTIALS_PATH env var."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"username": "test"}, f)
            temp_path = f.name

        try:
            with patch.dict(os.environ, {"SPOTIFY_CREDENTIALS_PATH": temp_path}):
                provider = SpotifyProvider()
                assert provider.credentials_path == Path(temp_path)
        finally:
            os.unlink(temp_path)

    def test_credentials_path_resolution_default(self):
        """Should fall back to default path when no credentials found."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SPOTIFY_CREDENTIALS_PATH", None)

            provider = SpotifyProvider()
            expected = Path.home() / ".flacfetch" / "spotify_credentials.json"
            assert provider.credentials_path == expected

    def test_search_returns_empty_when_no_credentials(self):
        """Search should return empty list if credentials don't exist."""
        provider = SpotifyProvider("/nonexistent/path/credentials.json")
        results = provider.search(TrackQuery(artist="Test", title="Song"))
        assert results == []

    def test_search_returns_empty_on_init_failure(self):
        """Search should return empty list if zotify init fails."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"username": "test"}, f)
            temp_path = f.name

        try:
            provider = SpotifyProvider(temp_path)

            with patch.object(provider, "_init_zotify", return_value=False):
                results = provider.search(TrackQuery(artist="Test", title="Song"))
                assert results == []
        finally:
            os.unlink(temp_path)

    def test_search_returns_releases(self):
        """Search should return Release objects from Spotify results."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"username": "test"}, f)
            temp_path = f.name

        try:
            provider = SpotifyProvider(temp_path)

            with patch.object(provider, "_init_zotify", return_value=True):
                with patch.object(provider, "_execute_search") as mock_search:
                    mock_search.return_value = {"tracks": {"items": [SAMPLE_TRACK]}}

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
                    assert release.quality.format == AudioFormat.VORBIS
                    assert release.quality.bitrate == 320
        finally:
            os.unlink(temp_path)

    def test_search_handles_multiple_artists(self):
        """Search should join multiple artists with comma."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"username": "test"}, f)
            temp_path = f.name

        try:
            provider = SpotifyProvider(temp_path)

            with patch.object(provider, "_init_zotify", return_value=True):
                with patch.object(provider, "_execute_search") as mock_search:
                    mock_search.return_value = {"tracks": {"items": [SAMPLE_TRACK_MULTIPLE_ARTISTS]}}

                    results = provider.search(
                        TrackQuery(artist="Artist One", title="Collaboration Track")
                    )

                    assert len(results) == 1
                    assert results[0].artist == "Artist One, Artist Two, Artist Three"
                    assert results[0].release_type == "Single"
        finally:
            os.unlink(temp_path)

    def test_search_handles_missing_fields(self):
        """Search should handle tracks with missing optional fields."""
        minimal_track = {
            "id": "minimal123",
            "name": "Minimal Track",
            "artists": [{"name": "Artist"}],
            "album": {"name": "Album"},
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"username": "test"}, f)
            temp_path = f.name

        try:
            provider = SpotifyProvider(temp_path)

            with patch.object(provider, "_init_zotify", return_value=True):
                with patch.object(provider, "_execute_search") as mock_search:
                    mock_search.return_value = {"tracks": {"items": [minimal_track]}}

                    results = provider.search(TrackQuery(artist="Artist", title="Minimal Track"))

                    assert len(results) == 1
                    assert results[0].year is None
                    assert results[0].duration_seconds is None
        finally:
            os.unlink(temp_path)

    def test_search_calculates_match_score(self):
        """Search should calculate match score based on track title."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"username": "test"}, f)
            temp_path = f.name

        try:
            provider = SpotifyProvider(temp_path)

            with patch.object(provider, "_init_zotify", return_value=True):
                with patch.object(provider, "_execute_search") as mock_search:
                    mock_search.return_value = {"tracks": {"items": [SAMPLE_TRACK]}}

                    results = provider.search(
                        TrackQuery(artist="Rick Astley", title="Never Gonna Give You Up")
                    )

                    assert len(results) == 1
                    assert results[0].match_score > 0.9
        finally:
            os.unlink(temp_path)

    def test_search_estimates_file_size(self):
        """Search should estimate file size from duration and bitrate."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"username": "test"}, f)
            temp_path = f.name

        try:
            provider = SpotifyProvider(temp_path)

            with patch.object(provider, "_init_zotify", return_value=True):
                with patch.object(provider, "_execute_search") as mock_search:
                    mock_search.return_value = {"tracks": {"items": [SAMPLE_TRACK]}}

                    results = provider.search(
                        TrackQuery(artist="Rick Astley", title="Never Gonna Give You Up")
                    )

                    # 213 seconds * 320kbps / 8 = ~8.5MB
                    assert results[0].size_bytes is not None
                    assert results[0].size_bytes > 8_000_000
                    assert results[0].size_bytes < 9_000_000
        finally:
            os.unlink(temp_path)

    def test_search_handles_api_error(self):
        """Search should return empty list on API errors."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"username": "test"}, f)
            temp_path = f.name

        try:
            provider = SpotifyProvider(temp_path)

            with patch.object(provider, "_init_zotify", return_value=True):
                with patch.object(provider, "_execute_search") as mock_search:
                    # Return empty dict to simulate error
                    mock_search.return_value = {}

                    results = provider.search(TrackQuery(artist="Test", title="Track"))
                    assert results == []
        finally:
            os.unlink(temp_path)

    def test_search_handles_exception(self):
        """Search should return empty list on exceptions."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"username": "test"}, f)
            temp_path = f.name

        try:
            provider = SpotifyProvider(temp_path)

            with patch.object(provider, "_init_zotify", return_value=True):
                with patch.object(provider, "_execute_search") as mock_search:
                    mock_search.side_effect = Exception("Network error")

                    results = provider.search(TrackQuery(artist="Test", title="Track"))
                    assert results == []
        finally:
            os.unlink(temp_path)

    def test_track_without_id_is_skipped(self):
        """Tracks without ID should be skipped."""
        track_no_id = {
            "name": "No ID Track",
            "artists": [{"name": "Artist"}],
            "album": {"name": "Album"},
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"username": "test"}, f)
            temp_path = f.name

        try:
            provider = SpotifyProvider(temp_path)

            with patch.object(provider, "_init_zotify", return_value=True):
                with patch.object(provider, "_execute_search") as mock_search:
                    mock_search.return_value = {"tracks": {"items": [track_no_id]}}

                    results = provider.search(TrackQuery(artist="Artist", title="No ID Track"))
                    assert results == []
        finally:
            os.unlink(temp_path)

    def test_validate_credentials_exists(self):
        """validate_credentials should return True for existing file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"username": "test"}, f)
            temp_path = f.name

        try:
            provider = SpotifyProvider(temp_path)
            assert provider._validate_credentials() is True
        finally:
            os.unlink(temp_path)

    def test_validate_credentials_missing_file(self):
        """validate_credentials should return False for missing file."""
        provider = SpotifyProvider("/nonexistent/credentials.json")
        assert provider._validate_credentials() is False


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

    def test_download_invalid_url_raises(self):
        """Should raise ValueError for missing download URL."""
        downloader = SpotifyDownloader()
        release = Release(
            title="Test",
            artist="Test",
            quality=Quality(AudioFormat.VORBIS, bitrate=320),
            source_name="Spotify",
            download_url=None,
        )

        with pytest.raises(ValueError, match="no download URL"):
            downloader.download(release, "/tmp")

    def test_download_invalid_uri_raises(self):
        """Should raise ValueError for invalid Spotify URI."""
        downloader = SpotifyDownloader()
        release = Release(
            title="Test",
            artist="Test",
            quality=Quality(AudioFormat.VORBIS, bitrate=320),
            source_name="Spotify",
            download_url="https://youtube.com/watch?v=123",
        )

        with pytest.raises(ValueError, match="Invalid Spotify URI"):
            downloader.download(release, "/tmp")

    def test_download_creates_output_directory(self):
        """Should create output directory if it doesn't exist."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"username": "test"}, f)
            creds_path = f.name

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "subdir", "nested")

            downloader = SpotifyDownloader(creds_path)
            release = Release(
                title="Album",
                artist="Artist",
                quality=Quality(AudioFormat.VORBIS, bitrate=320),
                source_name="Spotify",
                download_url="spotify:track:4PTG3Z6ehGkBFwjybzWkR8",
                target_file="Track Name",
            )

            def mock_download_api(track_id, out_path, base_name):
                output_file = os.path.join(out_path, f"{base_name}.ogg")
                Path(output_file).touch()
                return output_file

            with patch.object(downloader, "_download_via_api", side_effect=mock_download_api):
                result = downloader.download(release, output_path)

                assert os.path.exists(output_path)
                assert os.path.exists(result)

        os.unlink(creds_path)

    def test_download_uses_custom_filename(self):
        """Should use custom filename when provided."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"username": "test"}, f)
            creds_path = f.name

        with tempfile.TemporaryDirectory() as tmpdir:
            downloader = SpotifyDownloader(creds_path)
            release = Release(
                title="Album",
                artist="Artist",
                quality=Quality(AudioFormat.VORBIS, bitrate=320),
                source_name="Spotify",
                download_url="spotify:track:4PTG3Z6ehGkBFwjybzWkR8",
                target_file="Original Track",
            )

            def mock_download_api(track_id, out_path, base_name):
                output_file = os.path.join(out_path, f"{base_name}.ogg")
                Path(output_file).touch()
                return output_file

            with patch.object(downloader, "_download_via_api", side_effect=mock_download_api):
                result = downloader.download(release, tmpdir, output_filename="Custom Name")

                assert "Custom Name" in result
                assert os.path.exists(result)

        os.unlink(creds_path)

    def test_download_default_filename_format(self):
        """Should use 'Artist - Track.ogg' format by default."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"username": "test"}, f)
            creds_path = f.name

        with tempfile.TemporaryDirectory() as tmpdir:
            downloader = SpotifyDownloader(creds_path)
            release = Release(
                title="Album Name",
                artist="The Artist",
                quality=Quality(AudioFormat.VORBIS, bitrate=320),
                source_name="Spotify",
                download_url="spotify:track:4PTG3Z6ehGkBFwjybzWkR8",
                target_file="Track Title",
            )

            def mock_download_api(track_id, out_path, base_name):
                output_file = os.path.join(out_path, f"{base_name}.ogg")
                Path(output_file).touch()
                return output_file

            with patch.object(downloader, "_download_via_api", side_effect=mock_download_api):
                result = downloader.download(release, tmpdir)

                assert "The Artist - Track Title.ogg" in result

        os.unlink(creds_path)

    def test_download_cli_fallback(self):
        """Should fall back to CLI when API fails."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"username": "test"}, f)
            creds_path = f.name

        with tempfile.TemporaryDirectory() as tmpdir:
            downloader = SpotifyDownloader(creds_path)
            release = Release(
                title="Album",
                artist="Artist",
                quality=Quality(AudioFormat.VORBIS, bitrate=320),
                source_name="Spotify",
                download_url="spotify:track:4PTG3Z6ehGkBFwjybzWkR8",
                target_file="Track",
            )

            def mock_cli_download(track_id, out_path, base_name):
                output_file = os.path.join(out_path, f"{base_name}.ogg")
                Path(output_file).touch()
                return output_file

            with patch.object(downloader, "_download_via_api", side_effect=Exception("API failed")):
                with patch.object(downloader, "_download_via_cli", side_effect=mock_cli_download):
                    result = downloader.download(release, tmpdir)
                    assert os.path.exists(result)

        os.unlink(creds_path)


class TestSpotifyIntegration:
    """Integration tests for provider and downloader working together."""

    def test_provider_release_works_with_downloader(self):
        """Release from provider should be valid for downloader."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"username": "test"}, f)
            creds_path = f.name

        try:
            provider = SpotifyProvider(creds_path)
            downloader = SpotifyDownloader(creds_path)

            with patch.object(provider, "_init_zotify", return_value=True):
                with patch.object(provider, "_execute_search") as mock_search:
                    mock_search.return_value = {"tracks": {"items": [SAMPLE_TRACK]}}

                    results = provider.search(
                        TrackQuery(artist="Rick Astley", title="Never Gonna Give You Up")
                    )

                    assert len(results) == 1
                    release = results[0]

                    assert release.download_url is not None
                    assert release.download_url.startswith("spotify:track:")

                    track_id = downloader._extract_track_id(release.download_url)
                    assert track_id == "4PTG3Z6ehGkBFwjybzWkR8"
        finally:
            os.unlink(creds_path)


class TestAudioFormatVorbis:
    """Test that VORBIS format is properly integrated."""

    def test_vorbis_format_exists(self):
        """VORBIS should be a valid AudioFormat."""
        assert hasattr(AudioFormat, "VORBIS")
        assert AudioFormat.VORBIS.name == "VORBIS"

    def test_vorbis_is_not_lossless(self):
        """VORBIS should not be considered lossless."""
        quality = Quality(format=AudioFormat.VORBIS, bitrate=320)
        assert quality.is_lossless() is False

    def test_vorbis_quality_string(self):
        """VORBIS quality should display bitrate."""
        quality = Quality(format=AudioFormat.VORBIS, bitrate=320, media=MediaSource.WEB)
        quality_str = str(quality)
        assert "VORBIS" in quality_str
        assert "320kbps" in quality_str

    def test_vorbis_comparison(self):
        """VORBIS 320 should be better than lower bitrates."""
        vorbis_320 = Quality(format=AudioFormat.VORBIS, bitrate=320)
        vorbis_160 = Quality(format=AudioFormat.VORBIS, bitrate=160)
        opus_128 = Quality(format=AudioFormat.OPUS, bitrate=128)

        assert vorbis_160 < vorbis_320
        assert opus_128 < vorbis_320

    def test_flac_beats_vorbis(self):
        """FLAC should always beat VORBIS regardless of bitrate."""
        flac = Quality(format=AudioFormat.FLAC, bit_depth=16)
        vorbis_320 = Quality(format=AudioFormat.VORBIS, bitrate=320)

        assert vorbis_320 < flac
