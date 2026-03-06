"""Tests for credential keeper and related credential check additions."""
import json
import os
from unittest.mock import MagicMock, patch

import pytest

# =============================================================================
# Tests for _persist_spotify_token_to_secret_manager
# =============================================================================


class TestPersistSpotifyToken:
    """Tests for the token writeback to Secret Manager."""

    def test_writeback_reads_cache_file(self, tmp_path):
        """Test that writeback reads the cache file and calls Secret Manager."""
        import sys

        from flacfetch.api.services.credential_check import _persist_spotify_token_to_secret_manager

        cache_file = tmp_path / ".cache"
        token_data = json.dumps({"access_token": "tok", "refresh_token": "ref", "token_type": "Bearer"})
        cache_file.write_text(token_data)

        mock_client = MagicMock()
        mock_sm_module = MagicMock()
        mock_sm_module.SecretManagerServiceClient.return_value = mock_client

        original = sys.modules.get("google.cloud.secretmanager")
        sys.modules["google.cloud.secretmanager"] = mock_sm_module
        try:
            with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}):
                _persist_spotify_token_to_secret_manager(str(cache_file))
            mock_client.add_secret_version.assert_called_once()
            call_args = mock_client.add_secret_version.call_args
            payload = call_args[1]["request"]["payload"]["data"]
            assert payload == token_data.encode("utf-8")
            assert "spotify-oauth-token" in call_args[1]["request"]["parent"]
        finally:
            if original is not None:
                sys.modules["google.cloud.secretmanager"] = original
            else:
                sys.modules.pop("google.cloud.secretmanager", None)

    def test_writeback_skips_empty_file(self, tmp_path):
        """Test that writeback skips if cache file is empty."""
        from flacfetch.api.services.credential_check import _persist_spotify_token_to_secret_manager

        cache_file = tmp_path / ".cache"
        cache_file.write_text("   ")

        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}):
            # Should return without calling Secret Manager
            _persist_spotify_token_to_secret_manager(str(cache_file))

    def test_writeback_handles_missing_file(self):
        """Test that writeback handles missing cache file gracefully."""
        from flacfetch.api.services.credential_check import _persist_spotify_token_to_secret_manager

        # Should not raise
        _persist_spotify_token_to_secret_manager("/nonexistent/path/.cache")

    def test_writeback_handles_no_project_id(self, tmp_path):
        """Test that writeback handles missing project ID gracefully."""
        from flacfetch.api.services.credential_check import _persist_spotify_token_to_secret_manager

        cache_file = tmp_path / ".cache"
        cache_file.write_text('{"access_token": "tok"}')

        with patch.dict(os.environ, {}, clear=True):
            # Mock the metadata request to fail
            with patch("requests.get", side_effect=Exception("no metadata")):
                # Should not raise
                _persist_spotify_token_to_secret_manager(str(cache_file))

    def test_writeback_handles_import_error(self, tmp_path):
        """Test that writeback handles missing google-cloud-secret-manager."""
        from flacfetch.api.services.credential_check import _persist_spotify_token_to_secret_manager

        cache_file = tmp_path / ".cache"
        cache_file.write_text('{"access_token": "tok"}')

        with patch.dict("sys.modules", {"google.cloud": None, "google.cloud.secretmanager": None}):
            # Should not raise
            _persist_spotify_token_to_secret_manager(str(cache_file))


# =============================================================================
# Tests for YouTube cookie extraction (pure logic)
# =============================================================================


class TestYouTubeCookieExtraction:
    """Tests for Netscape cookie format conversion."""

    @pytest.mark.asyncio
    async def test_extract_netscape_cookies_format(self):
        """Test that cookies are converted to proper Netscape format."""
        from flacfetch.credential_keeper.youtube import extract_netscape_cookies

        mock_context = MagicMock()

        async def mock_cookies(*args, **kwargs):
            return [
                {"domain": ".youtube.com", "path": "/", "secure": True, "expires": 1735689600,
                 "name": "PREF", "value": "tz=America.New_York"},
                {"domain": ".google.com", "path": "/", "secure": True, "expires": 1735689600,
                 "name": "SID", "value": "abc123"},
            ]

        mock_context.cookies = mock_cookies

        result = await extract_netscape_cookies(mock_context)

        assert "# Netscape HTTP Cookie File" in result
        assert ".youtube.com\tTRUE\t/\tTRUE\t1735689600\tPREF\ttz=America.New_York" in result
        assert ".google.com\tTRUE\t/\tTRUE\t1735689600\tSID\tabc123" in result

    @pytest.mark.asyncio
    async def test_extract_empty_cookies_raises(self):
        """Test that empty cookies raise ValueError."""
        from flacfetch.credential_keeper.youtube import extract_netscape_cookies

        mock_context = MagicMock()

        async def empty_cookies(*args, **kwargs):
            return []

        mock_context.cookies = empty_cookies

        with pytest.raises(ValueError, match="No cookies found"):
            await extract_netscape_cookies(mock_context)

    @pytest.mark.asyncio
    async def test_extract_non_youtube_cookies_raises(self):
        """Test that cookies without YouTube/Google domains raise ValueError."""
        from flacfetch.credential_keeper.youtube import extract_netscape_cookies

        mock_context = MagicMock()

        async def other_cookies(*args, **kwargs):
            return [{"domain": ".example.com", "path": "/", "secure": False, "expires": 0,
                     "name": "test", "value": "val"}]

        mock_context.cookies = other_cookies

        with pytest.raises(ValueError, match="none for YouTube/Google"):
            await extract_netscape_cookies(mock_context)


# =============================================================================
# Tests for Spotify OAuth URL building and code extraction
# =============================================================================


class TestSpotifyOAuth:
    """Tests for Spotify OAuth helper functions."""

    def test_build_oauth_url(self):
        """Test OAuth URL construction."""
        from flacfetch.credential_keeper.spotify import _build_oauth_url

        with patch.dict(os.environ, {
            "SPOTIPY_CLIENT_ID": "test-client-id",
            "SPOTIPY_REDIRECT_URI": "http://127.0.0.1:8888/callback",
        }):
            url = _build_oauth_url()

        assert "client_id=test-client-id" in url
        assert "response_type=code" in url
        assert "redirect_uri=" in url
        assert "127.0.0.1" in url
        assert "streaming" in url

    def test_build_oauth_url_missing_client_id(self):
        """Test OAuth URL raises without client ID."""
        from flacfetch.credential_keeper.spotify import _build_oauth_url

        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="SPOTIPY_CLIENT_ID"):
                _build_oauth_url()

    def test_extract_code_from_url(self):
        """Test extracting authorization code from redirect URL."""
        from flacfetch.credential_keeper.spotify import _extract_code_from_url

        url = "http://127.0.0.1:8888/callback?code=AQBz123abc&state=xyz"
        assert _extract_code_from_url(url) == "AQBz123abc"

    def test_extract_code_missing(self):
        """Test extracting code from URL without code param."""
        from flacfetch.credential_keeper.spotify import _extract_code_from_url

        url = "http://127.0.0.1:8888/callback?error=access_denied"
        assert _extract_code_from_url(url) is None

    def test_extract_code_invalid_url(self):
        """Test extracting code from invalid URL."""
        from flacfetch.credential_keeper.spotify import _extract_code_from_url

        assert _extract_code_from_url("not-a-url") is None


# =============================================================================
# Tests for keeper status endpoint
# =============================================================================


class TestKeeperStatusEndpoint:
    """Tests for the /credentials/keeper-status API endpoint."""

    @pytest.mark.asyncio
    async def test_status_file_not_found(self):
        """Test endpoint returns not_running when status file doesn't exist."""
        from flacfetch.api.routes.credentials import get_keeper_status

        with patch.dict(os.environ, {"KEEPER_STATUS_FILE": "/nonexistent/status.json"}):
            result = await get_keeper_status(api_key="test")

        assert result["status"] == "not_running"

    @pytest.mark.asyncio
    async def test_status_file_valid(self, tmp_path):
        """Test endpoint returns parsed JSON from status file."""
        from flacfetch.api.routes.credentials import get_keeper_status

        status_file = tmp_path / "keeper-status.json"
        status_data = {
            "google_session": {"logged_in": True},
            "youtube": {"last_refresh_status": "ok"},
        }
        status_file.write_text(json.dumps(status_data))

        with patch.dict(os.environ, {"KEEPER_STATUS_FILE": str(status_file)}):
            result = await get_keeper_status(api_key="test")

        assert result["google_session"]["logged_in"] is True
        assert result["youtube"]["last_refresh_status"] == "ok"

    @pytest.mark.asyncio
    async def test_status_file_corrupted(self, tmp_path):
        """Test endpoint handles corrupted status file."""
        from flacfetch.api.routes.credentials import get_keeper_status

        status_file = tmp_path / "keeper-status.json"
        status_file.write_text("not json {{{")

        with patch.dict(os.environ, {"KEEPER_STATUS_FILE": str(status_file)}):
            result = await get_keeper_status(api_key="test")

        assert result["status"] == "error"
