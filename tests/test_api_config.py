"""Tests for config API routes."""
import json
import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import patch

from flacfetch.api.routes.config import (
    CookiesStatusResponse,
    CookiesUploadRequest,
    CookiesUploadResponse,
    SpotifyTokenStatusResponse,
    SpotifyTokenUploadRequest,
    SpotifyTokenUploadResponse,
    _get_cookies_file_path,
    _validate_cookies_format,
    _validate_spotify_token_format,
    _write_cookies_file,
    _write_spotify_token_file,
)
from flacfetch.core.config import (
    get_spotify_cache_path,
    get_youtube_cookies_path,
)


class TestValidateCookiesFormat:
    """Tests for _validate_cookies_format function."""

    def test_empty_content(self):
        """Test validation fails for empty content."""
        is_valid, message = _validate_cookies_format("")
        assert not is_valid
        # May fail on "Empty" or "No valid cookie lines"
        assert "Empty" in message or "No valid" in message

    def test_no_valid_cookie_lines(self):
        """Test validation fails when no valid cookie lines."""
        content = "# Just a comment\n# Another comment"
        is_valid, message = _validate_cookies_format(content)
        assert not is_valid
        assert "No valid cookie lines" in message

    def test_valid_youtube_cookies(self):
        """Test validation passes for valid YouTube cookies."""
        content = """# Netscape HTTP Cookie File
.youtube.com\tTRUE\t/\tTRUE\t1735689600\tPREF\tvalue1
.youtube.com\tTRUE\t/\tTRUE\t1735689600\tSID\tvalue2
.google.com\tTRUE\t/\tTRUE\t1735689600\tHSID\tvalue3
"""
        is_valid, message = _validate_cookies_format(content)
        assert is_valid
        assert "Valid" in message
        assert "3 cookies" in message
        assert "3 YouTube/Google" in message

    def test_no_youtube_cookies(self):
        """Test validation fails when no YouTube/Google cookies."""
        content = """.example.com\tTRUE\t/\tTRUE\t1735689600\tSESSION\tvalue1
.other.com\tTRUE\t/\tTRUE\t1735689600\tTOKEN\tvalue2
"""
        is_valid, message = _validate_cookies_format(content)
        assert not is_valid
        assert "none for YouTube/Google" in message

    def test_mixed_cookies(self):
        """Test validation passes with some non-YouTube cookies."""
        content = """.youtube.com\tTRUE\t/\tTRUE\t1735689600\tPREF\tvalue1
.example.com\tTRUE\t/\tTRUE\t1735689600\tOTHER\tvalue2
"""
        is_valid, message = _validate_cookies_format(content)
        assert is_valid
        assert "2 cookies" in message
        assert "1 YouTube/Google" in message

    def test_skips_comments(self):
        """Test validation skips comment lines."""
        content = """# This is a comment
# Another comment
.youtube.com\tTRUE\t/\tTRUE\t1735689600\tPREF\tvalue1
"""
        is_valid, message = _validate_cookies_format(content)
        assert is_valid

    def test_invalid_format(self):
        """Test validation fails for invalid format."""
        content = "this is not a valid cookie format"
        is_valid, message = _validate_cookies_format(content)
        assert not is_valid


class TestWriteCookiesFile:
    """Tests for _write_cookies_file function."""

    def test_write_cookies_success(self):
        """Test writing cookies to file."""
        import sys

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "cookies.txt")
            content = "# test cookies"

            result = _write_cookies_file(content, file_path)

            assert result is True
            assert os.path.exists(file_path)
            with open(file_path) as f:
                assert f.read() == content
            # Check permissions on Unix only (Windows doesn't have the same permission model)
            if sys.platform != "win32":
                assert oct(os.stat(file_path).st_mode)[-3:] == "600"

    def test_write_creates_directory(self):
        """Test writing cookies creates parent directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "subdir", "cookies.txt")
            content = "# test cookies"

            result = _write_cookies_file(content, file_path)

            assert result is True
            assert os.path.exists(file_path)

    def test_write_failure(self):
        """Test handling of write failure."""
        # Try to write to a read-only location
        with patch("tempfile.NamedTemporaryFile", side_effect=PermissionError("No permission")):
            result = _write_cookies_file("content", "/some/path")
            assert result is False


class TestGetCookiesFilePath:
    """Tests for _get_cookies_file_path function."""

    def test_default_path(self):
        """Test returns default path when env var not set."""
        with patch.dict(os.environ, {}, clear=True):
            # Clear the env var if it exists
            if "YOUTUBE_COOKIES_FILE" in os.environ:
                del os.environ["YOUTUBE_COOKIES_FILE"]
            result = _get_cookies_file_path()
            assert result == "/opt/flacfetch/youtube_cookies.txt"

    def test_env_var_path(self):
        """Test returns path from env var."""
        with patch.dict(os.environ, {"YOUTUBE_COOKIES_FILE": "/custom/path.txt"}):
            result = _get_cookies_file_path()
            assert result == "/custom/path.txt"


class TestCookiesModels:
    """Tests for Pydantic models in config routes."""

    def test_cookies_upload_request(self):
        """Test CookiesUploadRequest model."""
        request = CookiesUploadRequest(cookies="# test cookies")
        assert request.cookies == "# test cookies"

    def test_cookies_upload_response(self):
        """Test CookiesUploadResponse model."""
        response = CookiesUploadResponse(
            success=True,
            message="Uploaded successfully",
            updated_at=datetime.now(timezone.utc),
        )
        assert response.success is True
        assert "Uploaded" in response.message

    def test_cookies_status_response_configured(self):
        """Test CookiesStatusResponse when configured."""
        response = CookiesStatusResponse(
            configured=True,
            source="file",
            file_path="/opt/flacfetch/youtube_cookies.txt",
            cookies_valid=True,
            validation_message="Valid: 5 cookies",
        )
        assert response.configured is True
        assert response.source == "file"

    def test_cookies_status_response_not_configured(self):
        """Test CookiesStatusResponse when not configured."""
        response = CookiesStatusResponse(
            configured=False,
            validation_message="No cookies configured",
        )
        assert response.configured is False
        assert response.source is None


class TestConfigRoutes:
    """Integration tests for config API routes."""

    def test_upload_cookies_requires_auth(self):
        """Test upload endpoint requires authentication when API key is set."""
        from fastapi.testclient import TestClient

        from flacfetch.api.main import create_app

        # Set API key so auth is required
        with patch.dict(os.environ, {"FLACFETCH_API_KEY": "test-key"}):
            app = create_app()
            client = TestClient(app)

            # Make request without API key header
            response = client.post(
                "/config/youtube-cookies",
                json={"cookies": "# test"},
            )
            # Should return 401/403 without API key in header
            assert response.status_code in [401, 403]

    def test_upload_cookies_invalid_format(self):
        """Test upload rejects invalid cookie format."""
        from fastapi.testclient import TestClient

        from flacfetch.api.main import create_app

        with patch.dict(os.environ, {"FLACFETCH_API_KEY": "test-key"}):
            app = create_app()
            client = TestClient(app)

            response = client.post(
                "/config/youtube-cookies",
                json={"cookies": "invalid format"},
                headers={"X-API-Key": "test-key"},
            )
            assert response.status_code == 400
            assert "No valid cookie lines" in response.json()["detail"]

    def test_upload_cookies_success(self):
        """Test successful cookie upload."""
        from fastapi.testclient import TestClient

        from flacfetch.api.main import create_app

        valid_cookies = """.youtube.com\tTRUE\t/\tTRUE\t1735689600\tPREF\tvalue1
.youtube.com\tTRUE\t/\tTRUE\t1735689600\tSID\tvalue2
"""
        with patch.dict(os.environ, {"FLACFETCH_API_KEY": "test-key"}):
            with patch("flacfetch.api.routes.config._update_secret", return_value=False):
                with patch("flacfetch.api.routes.config._write_cookies_file", return_value=True):
                    with patch("flacfetch.api.routes.config._get_cookies_file_path", return_value="/tmp/cookies.txt"):
                        app = create_app()
                        client = TestClient(app)

                        response = client.post(
                            "/config/youtube-cookies",
                            json={"cookies": valid_cookies},
                            headers={"X-API-Key": "test-key"},
                        )
                        assert response.status_code == 200
                        data = response.json()
                        assert data["success"] is True

    def test_status_requires_auth(self):
        """Test status endpoint requires authentication when API key is set."""
        from fastapi.testclient import TestClient

        from flacfetch.api.main import create_app

        # Set API key so auth is required
        with patch.dict(os.environ, {"FLACFETCH_API_KEY": "test-key"}):
            app = create_app()
            client = TestClient(app)

            # Make request without API key header
            response = client.get("/config/youtube-cookies/status")
            assert response.status_code in [401, 403]

    def test_status_not_configured(self):
        """Test status when cookies not configured."""
        from fastapi.testclient import TestClient

        from flacfetch.api.main import create_app

        with patch.dict(os.environ, {"FLACFETCH_API_KEY": "test-key"}):
            with patch("flacfetch.api.routes.config._get_cookies_file_path", return_value="/nonexistent/path.txt"):
                with patch("flacfetch.api.routes.config.os.path.exists", return_value=False):
                    app = create_app()
                    client = TestClient(app)

                    response = client.get(
                        "/config/youtube-cookies/status",
                        headers={"X-API-Key": "test-key"},
                    )
                    assert response.status_code == 200
                    data = response.json()
                    assert data["configured"] is False

    def test_delete_requires_auth(self):
        """Test delete endpoint requires authentication when API key is set."""
        from fastapi.testclient import TestClient

        from flacfetch.api.main import create_app

        # Set API key so auth is required
        with patch.dict(os.environ, {"FLACFETCH_API_KEY": "test-key"}):
            app = create_app()
            client = TestClient(app)

            # Make request without API key header
            response = client.delete("/config/youtube-cookies")
            assert response.status_code in [401, 403]

    def test_delete_no_cookies(self):
        """Test delete when no cookies exist."""
        from fastapi.testclient import TestClient

        from flacfetch.api.main import create_app

        with patch.dict(os.environ, {"FLACFETCH_API_KEY": "test-key"}):
            with patch("flacfetch.api.routes.config._get_cookies_file_path", return_value="/nonexistent/path.txt"):
                with patch("flacfetch.api.routes.config.os.path.exists", return_value=False):
                    app = create_app()
                    client = TestClient(app)

                    response = client.delete(
                        "/config/youtube-cookies",
                        headers={"X-API-Key": "test-key"},
                    )
                    assert response.status_code == 200
                    data = response.json()
                    assert "No cookies file" in data["message"]


# =============================================================================
# Centralized Config Path Tests
# =============================================================================


class TestCentralizedConfigPaths:
    """Tests for centralized credential path configuration."""

    def test_get_spotify_cache_path_server(self):
        """Test server path for Spotify cache."""
        with patch.dict(os.environ, {}, clear=True):
            # Clear env var if set
            os.environ.pop("SPOTIFY_CACHE_PATH", None)
            result = get_spotify_cache_path(local=False)
            assert result == "/opt/flacfetch/.cache"

    def test_get_spotify_cache_path_local(self):
        """Test local path for Spotify cache."""
        result = get_spotify_cache_path(local=True)
        assert ".cache-spotipy" in result
        assert result.startswith(os.path.expanduser("~"))

    def test_get_spotify_cache_path_env_override(self):
        """Test env var override for Spotify cache."""
        with patch.dict(os.environ, {"SPOTIFY_CACHE_PATH": "/custom/cache"}):
            result = get_spotify_cache_path(local=False)
            assert result == "/custom/cache"

    def test_get_youtube_cookies_path_server(self):
        """Test server path for YouTube cookies."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("YOUTUBE_COOKIES_FILE", None)
            result = get_youtube_cookies_path(local=False)
            assert result == "/opt/flacfetch/youtube_cookies.txt"

    def test_get_youtube_cookies_path_local(self):
        """Test local path for YouTube cookies."""
        result = get_youtube_cookies_path(local=True)
        assert "youtube_cookies.txt" in result
        assert ".flacfetch" in result

    def test_get_youtube_cookies_path_env_override(self):
        """Test env var override for YouTube cookies."""
        with patch.dict(os.environ, {"YOUTUBE_COOKIES_FILE": "/custom/cookies.txt"}):
            result = get_youtube_cookies_path(local=False)
            assert result == "/custom/cookies.txt"


# =============================================================================
# Spotify Token Tests
# =============================================================================


class TestValidateSpotifyTokenFormat:
    """Tests for _validate_spotify_token_format function."""

    def test_invalid_json(self):
        """Test validation fails for invalid JSON."""
        is_valid, message, token = _validate_spotify_token_format("not json")
        assert not is_valid
        assert "Invalid JSON" in message
        assert token is None

    def test_missing_access_token(self):
        """Test validation fails when access_token missing."""
        token_data = {
            "token_type": "Bearer",
            "refresh_token": "refresh123",
        }
        is_valid, message, token = _validate_spotify_token_format(json.dumps(token_data))
        assert not is_valid
        assert "access_token" in message
        assert token is None

    def test_missing_refresh_token(self):
        """Test validation fails when refresh_token missing."""
        token_data = {
            "access_token": "access123",
            "token_type": "Bearer",
        }
        is_valid, message, token = _validate_spotify_token_format(json.dumps(token_data))
        assert not is_valid
        assert "refresh_token" in message

    def test_invalid_token_type(self):
        """Test validation fails for wrong token_type."""
        token_data = {
            "access_token": "access123",
            "token_type": "Basic",
            "refresh_token": "refresh123",
        }
        is_valid, message, token = _validate_spotify_token_format(json.dumps(token_data))
        assert not is_valid
        assert "token_type" in message

    def test_missing_scope(self):
        """Test validation fails when required scopes missing."""
        token_data = {
            "access_token": "access123",
            "token_type": "Bearer",
            "refresh_token": "refresh123",
            "scope": "playlist-read-private",  # Wrong scope
        }
        is_valid, message, token = _validate_spotify_token_format(json.dumps(token_data))
        assert not is_valid
        assert "scope" in message.lower()

    def test_valid_token(self):
        """Test validation passes for valid token."""
        token_data = {
            "access_token": "BQBu9MdE8blMee9v120P7zTWM3CIah6C",
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": "AQCas2njrXeZMe9yXzI_CG-QvdUb6z",
            "scope": "user-read-playback-state user-modify-playback-state streaming",
            "expires_at": 1769286036,
        }
        is_valid, message, token = _validate_spotify_token_format(json.dumps(token_data))
        assert is_valid
        assert "Valid" in message
        assert token is not None
        assert token["access_token"] == token_data["access_token"]


class TestWriteSpotifyTokenFile:
    """Tests for _write_spotify_token_file function."""

    def test_write_token_success(self):
        """Test writing Spotify token to file."""
        import sys

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, ".cache")
            content = '{"access_token": "test"}'

            result = _write_spotify_token_file(content, file_path)

            assert result is True
            assert os.path.exists(file_path)
            with open(file_path) as f:
                assert f.read() == content
            if sys.platform != "win32":
                assert oct(os.stat(file_path).st_mode)[-3:] == "600"

    def test_write_creates_directory(self):
        """Test writing token creates parent directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "subdir", ".cache")
            content = '{"access_token": "test"}'

            result = _write_spotify_token_file(content, file_path)

            assert result is True
            assert os.path.exists(file_path)


class TestSpotifyTokenModels:
    """Tests for Spotify token Pydantic models."""

    def test_spotify_token_upload_request(self):
        """Test SpotifyTokenUploadRequest model."""
        request = SpotifyTokenUploadRequest(token='{"access_token": "test"}')
        assert '{"access_token"' in request.token

    def test_spotify_token_upload_response(self):
        """Test SpotifyTokenUploadResponse model."""
        response = SpotifyTokenUploadResponse(
            success=True,
            message="Token uploaded successfully",
            updated_at=datetime.now(timezone.utc),
        )
        assert response.success is True
        assert "uploaded" in response.message

    def test_spotify_token_status_response_configured(self):
        """Test SpotifyTokenStatusResponse when configured."""
        response = SpotifyTokenStatusResponse(
            configured=True,
            file_path="/opt/flacfetch/.cache",
            token_valid=True,
            expires_at=datetime.now(timezone.utc),
            validation_message="Valid Spotify OAuth token",
        )
        assert response.configured is True
        assert response.token_valid is True

    def test_spotify_token_status_response_not_configured(self):
        """Test SpotifyTokenStatusResponse when not configured."""
        response = SpotifyTokenStatusResponse(
            configured=False,
            validation_message="No Spotify OAuth token configured",
        )
        assert response.configured is False
        assert response.file_path is None


class TestSpotifyTokenRoutes:
    """Integration tests for Spotify token API routes."""

    def test_upload_token_requires_auth(self):
        """Test upload endpoint requires authentication."""
        from fastapi.testclient import TestClient

        from flacfetch.api.main import create_app

        with patch.dict(os.environ, {"FLACFETCH_API_KEY": "test-key"}):
            app = create_app()
            client = TestClient(app)

            response = client.post(
                "/config/spotify-token",
                json={"token": '{"access_token": "test"}'},
            )
            assert response.status_code in [401, 403]

    def test_upload_token_invalid_format(self):
        """Test upload rejects invalid token format."""
        from fastapi.testclient import TestClient

        from flacfetch.api.main import create_app

        with patch.dict(os.environ, {"FLACFETCH_API_KEY": "test-key"}):
            app = create_app()
            client = TestClient(app)

            response = client.post(
                "/config/spotify-token",
                json={"token": "not valid json"},
                headers={"X-API-Key": "test-key"},
            )
            assert response.status_code == 400
            assert "Invalid JSON" in response.json()["detail"]

    def test_upload_token_success(self):
        """Test successful token upload."""
        from fastapi.testclient import TestClient

        from flacfetch.api.main import create_app

        valid_token = json.dumps({
            "access_token": "BQBu9MdE8blMee9v120P7zTWM3CIah6C",
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": "AQCas2njrXeZMe9yXzI_CG-QvdUb6z",
            "scope": "user-read-playback-state user-modify-playback-state streaming",
            "expires_at": 1769286036,
        })

        with patch.dict(os.environ, {"FLACFETCH_API_KEY": "test-key"}):
            with patch("flacfetch.api.routes.config._update_spotify_secret", return_value=False):
                with patch("flacfetch.api.routes.config._write_spotify_token_file", return_value=True):
                    with patch("flacfetch.api.routes.config.get_spotify_cache_path", return_value="/tmp/.cache"):
                        with patch("flacfetch.api.routes.config._invalidate_spotify_provider", return_value=True):
                            app = create_app()
                            client = TestClient(app)

                            response = client.post(
                                "/config/spotify-token",
                                json={"token": valid_token},
                                headers={"X-API-Key": "test-key"},
                            )
                            assert response.status_code == 200
                            data = response.json()
                            assert data["success"] is True

    def test_status_requires_auth(self):
        """Test status endpoint requires authentication."""
        from fastapi.testclient import TestClient

        from flacfetch.api.main import create_app

        with patch.dict(os.environ, {"FLACFETCH_API_KEY": "test-key"}):
            app = create_app()
            client = TestClient(app)

            response = client.get("/config/spotify-token/status")
            assert response.status_code in [401, 403]

    def test_status_not_configured(self):
        """Test status when token not configured."""
        from fastapi.testclient import TestClient

        from flacfetch.api.main import create_app

        with patch.dict(os.environ, {"FLACFETCH_API_KEY": "test-key"}):
            with patch("flacfetch.api.routes.config.get_spotify_cache_path", return_value="/nonexistent/.cache"):
                with patch("flacfetch.api.routes.config.os.path.exists", return_value=False):
                    app = create_app()
                    client = TestClient(app)

                    response = client.get(
                        "/config/spotify-token/status",
                        headers={"X-API-Key": "test-key"},
                    )
                    assert response.status_code == 200
                    data = response.json()
                    assert data["configured"] is False

    def test_delete_requires_auth(self):
        """Test delete endpoint requires authentication."""
        from fastapi.testclient import TestClient

        from flacfetch.api.main import create_app

        with patch.dict(os.environ, {"FLACFETCH_API_KEY": "test-key"}):
            app = create_app()
            client = TestClient(app)

            response = client.delete("/config/spotify-token")
            assert response.status_code in [401, 403]

    def test_delete_no_token(self):
        """Test delete when no token exists."""
        from fastapi.testclient import TestClient

        from flacfetch.api.main import create_app

        with patch.dict(os.environ, {"FLACFETCH_API_KEY": "test-key"}):
            with patch("flacfetch.api.routes.config.get_spotify_cache_path", return_value="/nonexistent/.cache"):
                with patch("flacfetch.api.routes.config.os.path.exists", return_value=False):
                    with patch("flacfetch.api.routes.config._invalidate_spotify_provider", return_value=True):
                        app = create_app()
                        client = TestClient(app)

                        response = client.delete(
                            "/config/spotify-token",
                            headers={"X-API-Key": "test-key"},
                        )
                        assert response.status_code == 200
                        data = response.json()
                        assert "No token file" in data["message"]

