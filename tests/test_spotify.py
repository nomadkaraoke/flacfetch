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

    def test_resolve_duration_prefers_release(self):
        """Uses duration_seconds on the release when set, without hitting the API."""
        downloader = SpotifyDownloader(provider=MagicMock())
        sp = MagicMock()
        release = Release(
            title="t", artist="a",
            quality=Quality(AudioFormat.FLAC),
            source_name="Spotify",
            download_url="spotify:track:4PTG3Z6ehGkBFwjybzWkR8",
            duration_seconds=213,
        )

        assert downloader._resolve_duration_secs(sp, "4PTG3Z6ehGkBFwjybzWkR8", release) == 213
        sp.track.assert_not_called()

    def test_resolve_duration_falls_back_to_api(self):
        """When release has no duration, looks it up via the Spotify Web API.

        Regression test: download-by-id doesn't populate duration_seconds on the
        Release, so short tracks used to fail with 'Download timeout' because
        the default 300s expected_size was larger than the actual capture.
        """
        downloader = SpotifyDownloader(provider=MagicMock())
        sp = MagicMock()
        sp.track.return_value = {"duration_ms": 159567}  # 159s, same as the bug report
        release = Release(
            title="t", artist="a",
            quality=Quality(AudioFormat.FLAC),
            source_name="Spotify",
            download_url="spotify:track:0LmMZe0g80Z60zoTqR3cai",
        )

        assert downloader._resolve_duration_secs(sp, "0LmMZe0g80Z60zoTqR3cai", release) == 159
        sp.track.assert_called_once_with("0LmMZe0g80Z60zoTqR3cai")

    def test_resolve_duration_falls_back_to_default_on_api_error(self):
        """If the API lookup fails, use the 300s default rather than raising."""
        downloader = SpotifyDownloader(provider=MagicMock())
        sp = MagicMock()
        sp.track.side_effect = Exception("transient network error")
        release = Release(
            title="t", artist="a",
            quality=Quality(AudioFormat.FLAC),
            source_name="Spotify",
            download_url="spotify:track:4PTG3Z6ehGkBFwjybzWkR8",
        )

        assert downloader._resolve_duration_secs(sp, "4PTG3Z6ehGkBFwjybzWkR8", release) == 300

    def test_resolve_duration_falls_back_when_api_returns_no_duration(self):
        """If the API returns a track without duration_ms, use the default."""
        downloader = SpotifyDownloader(provider=MagicMock())
        sp = MagicMock()
        sp.track.return_value = {"id": "foo"}  # no duration_ms
        release = Release(
            title="t", artist="a",
            quality=Quality(AudioFormat.FLAC),
            source_name="Spotify",
            download_url="spotify:track:4PTG3Z6ehGkBFwjybzWkR8",
        )

        assert downloader._resolve_duration_secs(sp, "4PTG3Z6ehGkBFwjybzWkR8", release) == 300


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


class TestLibrespotCredentialSelection:
    """librespot login: prefer stored credentials, fall back to access token."""

    def _make_release(self):
        return Release(
            title="Test",
            artist="Test",
            quality=Quality(AudioFormat.FLAC),
            source_name="Spotify",
            download_url="spotify:track:4PTG3Z6ehGkBFwjybzWkR8",
            duration_seconds=200,  # avoid a Web API duration lookup
        )

    def test_download_uses_stored_credentials_when_present(self, tmp_path):
        """When credentials.json exists, librespot gets -c <dir> and no token."""
        creds_dir = tmp_path / "librespot"
        creds_dir.mkdir()
        (creds_dir / "credentials.json").write_text("{}")

        provider = MagicMock()
        downloader = SpotifyDownloader(provider=provider)
        downloader._librespot_path = "/fake/librespot"

        with patch(
            "flacfetch.downloaders.spotify.get_librespot_credentials_dir",
            return_value=str(creds_dir),
        ), patch("flacfetch.downloaders.spotify.subprocess.Popen") as mock_popen, patch.object(
            downloader, "_wait_for_device", return_value=None
        ):
            with pytest.raises(SpotifyDownloadError, match="not found in Spotify"):
                downloader.download(self._make_release(), str(tmp_path / "out"))

        cmd = mock_popen.call_args.args[0]
        env = mock_popen.call_args.kwargs["env"]
        assert "-c" in cmd
        assert str(creds_dir) in cmd
        assert "--disable-audio-cache" in cmd
        assert "LIBRESPOT_ACCESS_TOKEN" not in env
        # Stored creds mean we never mint a third-party access token.
        provider.get_access_token.assert_not_called()

    def test_download_falls_back_to_access_token_without_credentials(self, tmp_path):
        """With no stored credentials, librespot gets the access token via env."""
        creds_dir = tmp_path / "librespot"  # does not exist

        provider = MagicMock()
        provider.get_access_token.return_value = "tok-123"
        downloader = SpotifyDownloader(provider=provider)
        downloader._librespot_path = "/fake/librespot"

        with patch(
            "flacfetch.downloaders.spotify.get_librespot_credentials_dir",
            return_value=str(creds_dir),
        ), patch("flacfetch.downloaders.spotify.subprocess.Popen") as mock_popen, patch.object(
            downloader, "_wait_for_device", return_value=None
        ):
            with pytest.raises(SpotifyDownloadError, match="not found in Spotify"):
                downloader.download(self._make_release(), str(tmp_path / "out"))

        cmd = mock_popen.call_args.args[0]
        env = mock_popen.call_args.kwargs["env"]
        assert "-c" not in cmd
        assert env.get("LIBRESPOT_ACCESS_TOKEN") == "tok-123"

    def test_device_not_found_message_detects_invalid_credentials(self, tmp_path):
        """spirc INVALID_CREDENTIALS produces an actionable, keeper-pointing message."""
        log = tmp_path / "x.librespot.log"
        log.write_text(
            "Authenticated as 'x' !\n"
            "could not initialize spirc: Invalid state "
            "{ Login request was denied: INVALID_CREDENTIALS }\n"
        )
        downloader = SpotifyDownloader(provider=MagicMock())
        msg = downloader._device_not_found_message(log)
        assert "INVALID_CREDENTIALS" in msg
        assert "credential keeper" in msg

    def test_device_not_found_message_includes_log_tail(self, tmp_path):
        """A non-credential failure still surfaces the librespot log tail."""
        log = tmp_path / "x.librespot.log"
        log.write_text("some unexpected librespot failure\n")
        downloader = SpotifyDownloader(provider=MagicMock())
        msg = downloader._device_not_found_message(log)
        assert "librespot log:" in msg
        assert "unexpected librespot failure" in msg


class TestSpotifyConcurrencySerialization:
    """Concurrent Spotify captures must not overlap.

    A Spotify account has a single active playback stream, so two overlapping
    captures on the same account clobber each other -- one download's file ends
    up containing another track's audio. Regression test for the 2026-09-01
    incident (job 842948f4 got Keeno - Shelter from the Storm audio while its
    source was Maduk - One Last Picture, because both Spotify downloads ran
    within 2 seconds of each other).
    """

    def _make_release(self, track_id):
        return Release(
            title="Test",
            artist="Test",
            quality=Quality(AudioFormat.FLAC),
            source_name="Spotify",
            download_url=f"spotify:track:{track_id}",
            duration_seconds=200,  # avoid a Web API duration lookup
        )

    def _stub_downloader(self, tmp_path):
        creds_dir = tmp_path / "librespot"
        creds_dir.mkdir()
        (creds_dir / "credentials.json").write_text("{}")

        provider = MagicMock()
        downloader = SpotifyDownloader(provider=provider)
        downloader._librespot_path = "/fake/librespot"
        return downloader, creds_dir

    def test_captures_are_serialized(self, tmp_path):
        """Two concurrent download() calls never run their capture at the same time."""
        import threading
        import time

        downloader, creds_dir = self._stub_downloader(tmp_path)

        active = 0
        max_active = 0
        state_lock = threading.Lock()

        def fake_wait_for_download(pcm_path, *_args, **_kwargs):
            nonlocal active, max_active
            with state_lock:
                active += 1
                max_active = max(max_active, active)
            # Give the other thread a chance to overlap if the lock is broken.
            time.sleep(0.2)
            # Leave some captured PCM behind so download() does not treat this as
            # an empty capture.
            pcm_path.write_bytes(b"\x00" * 4096)
            with state_lock:
                active -= 1

        proc = MagicMock()
        proc.poll.return_value = None

        def run(track_id):
            with patch(
                "flacfetch.downloaders.spotify.get_librespot_credentials_dir",
                return_value=str(creds_dir),
            ), patch(
                "flacfetch.downloaders.spotify.subprocess.Popen", return_value=proc
            ), patch.object(
                downloader, "_wait_for_device", return_value={"id": "dev"}
            ), patch.object(
                downloader, "_wait_for_track_load", return_value=True
            ), patch.object(
                downloader, "_wait_for_download", side_effect=fake_wait_for_download
            ), patch.object(
                downloader, "_stop_librespot"
            ), patch.object(
                downloader, "_convert_pcm_to_flac", return_value=True
            ):
                downloader.download(
                    self._make_release(track_id), str(tmp_path / track_id)
                )

        threads = [
            threading.Thread(target=run, args=("4LXnEERKcz4aRC4NCMQJ0x",)),
            threading.Thread(target=run, args=("0S4fjRqfZP6XOF9j7vzmR1",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert max_active == 1, (
            f"Spotify captures overlapped (max concurrent={max_active}); "
            "serialization lock is not protecting the capture"
        )

    def test_each_capture_uses_a_unique_device_name(self, tmp_path):
        """Each download registers librespot under its own device name."""
        downloader, creds_dir = self._stub_downloader(tmp_path)

        device_names = []

        def capture_name(cmd, *_args, **_kwargs):
            # librespot device name follows the "-n" flag
            idx = cmd.index("-n")
            device_names.append(cmd[idx + 1])
            proc = MagicMock()
            proc.poll.return_value = None
            return proc

        seen_wait_names = []

        def fake_wait_for_device(sp, device_name, timeout=15):
            seen_wait_names.append(device_name)
            return {"id": "dev"}

        for track_id in ("aaaa", "bbbb"):
            with patch(
                "flacfetch.downloaders.spotify.get_librespot_credentials_dir",
                return_value=str(creds_dir),
            ), patch(
                "flacfetch.downloaders.spotify.subprocess.Popen",
                side_effect=capture_name,
            ), patch.object(
                downloader, "_wait_for_device", side_effect=fake_wait_for_device
            ), patch.object(
                downloader, "_wait_for_track_load", return_value=True
            ), patch.object(
                downloader,
                "_wait_for_download",
                side_effect=lambda pcm_path, *a, **k: pcm_path.write_bytes(b"\x00" * 4096),
            ), patch.object(
                downloader, "_stop_librespot"
            ), patch.object(
                downloader, "_convert_pcm_to_flac", return_value=True
            ):
                downloader.download(self._make_release(track_id), str(tmp_path / track_id))

        assert len(set(device_names)) == 2, f"device names not unique: {device_names}"
        assert all(n.startswith("flacfetch-capture-") for n in device_names)
        # The device we wait for must be the one we registered.
        assert seen_wait_names == device_names

    def test_capture_fails_if_requested_track_never_loads(self, tmp_path):
        """If the requested track does not load, fail rather than capture wrong audio."""
        downloader, creds_dir = self._stub_downloader(tmp_path)

        proc = MagicMock()
        proc.poll.return_value = None

        with patch(
            "flacfetch.downloaders.spotify.get_librespot_credentials_dir",
            return_value=str(creds_dir),
        ), patch(
            "flacfetch.downloaders.spotify.subprocess.Popen", return_value=proc
        ), patch.object(
            downloader, "_wait_for_device", return_value={"id": "dev"}
        ), patch.object(
            downloader, "_wait_for_track_load", return_value=False
        ), patch.object(
            downloader, "_stop_librespot"
        ):
            with pytest.raises(SpotifyDownloadError, match="did not load"):
                downloader.download(
                    self._make_release("4LXnEERKcz4aRC4NCMQJ0x"), str(tmp_path / "out")
                )


class TestSpotifyAuthFailFast:
    """Test that Spotify auth fails fast without cached token (for headless servers)."""

    def test_get_client_fails_fast_without_cached_token(self):
        """Should raise SpotifyAuthError immediately when no cached OAuth token exists.

        This prevents blocking browser-based OAuth on headless servers.
        Regression test for: job 2ac68b0f failed because Spotify auth blocked
        indefinitely, preventing valid RED/OPS results from being returned.
        """
        provider = SpotifyProvider(client_id="test_id", client_secret="test_secret")

        mock_auth_manager = MagicMock()
        mock_auth_manager.get_cached_token.return_value = None  # No cached token

        # Create mock modules for spotipy
        mock_spotipy = MagicMock()
        mock_oauth2 = MagicMock()
        mock_oauth2.SpotifyOAuth.return_value = mock_auth_manager

        with patch.dict("sys.modules", {"spotipy": mock_spotipy, "spotipy.oauth2": mock_oauth2}):
            # Reset provider's cached client
            provider._sp = None
            provider._auth_manager = None

            with pytest.raises(SpotifyAuthError) as exc_info:
                provider._get_client()

            assert "No cached OAuth token" in str(exc_info.value)
            # Verify we didn't try to call current_user() which would trigger browser auth
            mock_auth_manager.get_cached_token.assert_called_once()

    def test_get_client_refreshes_expired_token(self):
        """Should refresh expired token without browser interaction."""
        provider = SpotifyProvider(client_id="test_id", client_secret="test_secret")

        mock_auth_manager = MagicMock()
        mock_auth_manager.get_cached_token.return_value = {
            "access_token": "old_token",
            "refresh_token": "refresh_token_123",
        }
        mock_auth_manager.is_token_expired.return_value = True
        mock_auth_manager.refresh_access_token.return_value = {
            "access_token": "new_token",
            "refresh_token": "new_refresh_token",
        }

        mock_sp = MagicMock()
        mock_sp.current_user.return_value = {"display_name": "Test User", "id": "test"}

        # Create mock modules for spotipy
        mock_spotipy = MagicMock()
        mock_spotipy.Spotify.return_value = mock_sp
        mock_oauth2 = MagicMock()
        mock_oauth2.SpotifyOAuth.return_value = mock_auth_manager

        with patch.dict("sys.modules", {"spotipy": mock_spotipy, "spotipy.oauth2": mock_oauth2}):
            # Reset provider's cached client
            provider._sp = None
            provider._auth_manager = None
            client = provider._get_client()

            assert client is mock_sp
            mock_auth_manager.refresh_access_token.assert_called_once_with("refresh_token_123")

    def test_get_client_uses_valid_cached_token(self):
        """Should use valid cached token without refresh."""
        provider = SpotifyProvider(client_id="test_id", client_secret="test_secret")

        mock_auth_manager = MagicMock()
        mock_auth_manager.get_cached_token.return_value = {
            "access_token": "valid_token",
            "refresh_token": "refresh_token",
        }
        mock_auth_manager.is_token_expired.return_value = False

        mock_sp = MagicMock()
        mock_sp.current_user.return_value = {"display_name": "Test User", "id": "test"}

        # Create mock modules for spotipy
        mock_spotipy = MagicMock()
        mock_spotipy.Spotify.return_value = mock_sp
        mock_oauth2 = MagicMock()
        mock_oauth2.SpotifyOAuth.return_value = mock_auth_manager

        with patch.dict("sys.modules", {"spotipy": mock_spotipy, "spotipy.oauth2": mock_oauth2}):
            # Reset provider's cached client
            provider._sp = None
            provider._auth_manager = None
            client = provider._get_client()

            assert client is mock_sp
            mock_auth_manager.refresh_access_token.assert_not_called()


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
