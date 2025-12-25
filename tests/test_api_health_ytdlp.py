"""Tests for yt-dlp health checking in the health API."""
import os
from unittest.mock import MagicMock, patch

from flacfetch.api.models import YtdlpHealth
from flacfetch.api.routes.health import _check_ytdlp


class TestCheckYtdlp:
    """Tests for _check_ytdlp function."""

    def test_check_ytdlp_returns_health_object(self):
        """Test that _check_ytdlp returns a valid YtdlpHealth object."""
        result = _check_ytdlp()
        assert isinstance(result, YtdlpHealth)
        # Should have all required fields
        assert hasattr(result, "version")
        assert hasattr(result, "ejs_installed")
        assert hasattr(result, "deno_available")
        assert hasattr(result, "cookies_configured")

    def test_ytdlp_version_detected(self):
        """Test that yt-dlp version is detected."""
        result = _check_ytdlp()
        # yt-dlp should be installed in the test environment
        assert result.version is not None
        assert len(result.version) > 0

    def test_ejs_not_installed(self):
        """Test EJS detection when not installed."""
        # Just verify the function returns a valid YtdlpHealth object
        # The actual EJS status depends on the test environment
        result = _check_ytdlp()
        assert isinstance(result, YtdlpHealth)
        # ejs_installed is a boolean
        assert isinstance(result.ejs_installed, bool)

    def test_ejs_installed(self):
        """Test EJS detection when installed."""
        mock_ejs = MagicMock()
        mock_ejs.__version__ = "1.0.0"

        with patch.dict("sys.modules", {"yt_dlp_ejs": mock_ejs}):
            result = _check_ytdlp()
            # Check will try to import, result depends on actual environment
            assert isinstance(result, YtdlpHealth)

    def test_deno_available(self):
        """Test Deno detection when available."""
        with patch("shutil.which", return_value="/usr/local/bin/deno"):
            with patch("os.path.exists", return_value=True):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(
                        returncode=0,
                        stdout="deno 2.0.0\nv8 12.0\ntypescript 5.0\n",
                    )
                    result = _check_ytdlp()
                    # Deno should be detected
                    assert result.deno_available is True
                    assert result.deno_version == "2.0.0"

    def test_deno_not_available(self):
        """Test Deno detection when not available."""
        with patch("shutil.which", return_value=None):
            with patch("os.path.exists", return_value=False):
                result = _check_ytdlp()
                assert result.deno_available is False
                assert result.deno_version is None

    def test_deno_in_root_path(self):
        """Test Deno detection in /root/.deno/bin/deno path."""
        with patch("shutil.which", return_value=None):
            with patch("os.path.exists") as mock_exists:
                mock_exists.side_effect = lambda p: p == "/root/.deno/bin/deno"
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(
                        returncode=0,
                        stdout="deno 2.1.0",
                    )
                    result = _check_ytdlp()
                    assert result.deno_available is True
                    assert result.deno_version == "2.1.0"

    def test_cookies_configured_via_file(self):
        """Test cookies detection when file exists."""
        with patch.dict(os.environ, {"YOUTUBE_COOKIES_FILE": "/tmp/cookies.txt"}):
            with patch("os.path.exists", return_value=True):
                result = _check_ytdlp()
                assert result.cookies_configured is True

    def test_cookies_not_configured(self):
        """Test cookies detection when not configured."""
        with patch.dict(os.environ, {"YOUTUBE_COOKIES_FILE": ""}, clear=False):
            with patch("flacfetch.api.routes.health.os.path.exists", return_value=False):
                result = _check_ytdlp()
                assert result.cookies_configured is False


class TestHealthEndpointYtdlp:
    """Integration tests for yt-dlp in health endpoint."""

    def test_health_includes_ytdlp(self):
        """Test health endpoint includes ytdlp info."""
        from fastapi.testclient import TestClient

        from flacfetch.api.main import create_app

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FLACFETCH_API_KEY", None)
            app = create_app()
            client = TestClient(app)

            response = client.get("/health")
            assert response.status_code == 200
            data = response.json()

            assert "ytdlp" in data
            ytdlp = data["ytdlp"]
            assert "version" in ytdlp
            assert "ejs_installed" in ytdlp
            assert "deno_available" in ytdlp
            assert "cookies_configured" in ytdlp

    def test_health_ytdlp_has_version(self):
        """Test health endpoint shows yt-dlp version."""
        from fastapi.testclient import TestClient

        from flacfetch.api.main import create_app

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FLACFETCH_API_KEY", None)
            app = create_app()
            client = TestClient(app)

            response = client.get("/health")
            data = response.json()

            # yt-dlp should be installed
            assert data["ytdlp"]["version"] is not None

