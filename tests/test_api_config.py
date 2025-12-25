"""Tests for config API routes."""
import os
import tempfile
from datetime import datetime
from unittest.mock import patch

from flacfetch.api.routes.config import (
    CookiesStatusResponse,
    CookiesUploadRequest,
    CookiesUploadResponse,
    _get_cookies_file_path,
    _validate_cookies_format,
    _write_cookies_file,
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
            updated_at=datetime.utcnow(),
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

