"""Tests for YouTube cookies support in provider and downloader."""
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from flacfetch.core.models import AudioFormat, Quality, Release
from flacfetch.downloaders.youtube import (
    YoutubeDownloader,
    get_cookies_file,
    get_ytdlp_base_opts,
)
from flacfetch.providers.youtube import YoutubeProvider


class TestGetCookiesFile:
    """Tests for get_cookies_file function."""

    def test_returns_none_when_no_cookies(self):
        """Test returns None when no cookies file exists."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("os.path.exists", return_value=False):
                result = get_cookies_file()
                assert result is None

    def test_returns_env_var_path(self):
        """Test returns path from YOUTUBE_COOKIES_FILE env var."""
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"# cookies")
            temp_path = f.name

        try:
            with patch.dict(os.environ, {"YOUTUBE_COOKIES_FILE": temp_path}):
                result = get_cookies_file()
                assert result == temp_path
        finally:
            os.unlink(temp_path)

    def test_env_var_path_not_exists(self):
        """Test returns None when env var path doesn't exist."""
        with patch.dict(os.environ, {"YOUTUBE_COOKIES_FILE": "/nonexistent/path.txt"}):
            with patch("flacfetch.downloaders.youtube.os.path.exists", return_value=False):
                result = get_cookies_file()
                # Should return None since the env var path doesn't exist
                assert result is None

    def test_returns_default_path_when_exists(self):
        """Test returns default path when it exists."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("os.path.exists") as mock_exists:
                mock_exists.side_effect = lambda p: p == "/opt/flacfetch/youtube_cookies.txt"
                result = get_cookies_file()
                assert result == "/opt/flacfetch/youtube_cookies.txt"


class TestGetYtdlpBaseOpts:
    """Tests for get_ytdlp_base_opts function."""

    def test_returns_empty_dict_when_no_cookies(self):
        """Test returns empty dict when no cookies available."""
        with patch("flacfetch.downloaders.youtube.get_cookies_file", return_value=None):
            result = get_ytdlp_base_opts()
            assert result == {}

    def test_includes_cookiefile_when_available(self):
        """Test includes cookiefile when cookies available."""
        with patch("flacfetch.downloaders.youtube.get_cookies_file", return_value="/path/to/cookies.txt"):
            result = get_ytdlp_base_opts()
            assert result == {"cookiefile": "/path/to/cookies.txt"}

    def test_uses_provided_cookies_file(self):
        """Test uses explicitly provided cookies file."""
        result = get_ytdlp_base_opts("/my/custom/cookies.txt")
        assert result == {"cookiefile": "/my/custom/cookies.txt"}


class TestYoutubeDownloaderCookies:
    """Tests for YoutubeDownloader with cookies support."""

    def test_init_without_cookies(self):
        """Test initialization without cookies file."""
        downloader = YoutubeDownloader()
        assert downloader.cookies_file is None

    def test_init_with_cookies(self):
        """Test initialization with cookies file."""
        downloader = YoutubeDownloader(cookies_file="/path/to/cookies.txt")
        assert downloader.cookies_file == "/path/to/cookies.txt"

    def test_download_uses_cookies(self):
        """Test download uses cookies when configured."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cookies_path = os.path.join(tmpdir, "cookies.txt")
            with open(cookies_path, "w") as f:
                f.write("# Netscape cookies\n")

            downloader = YoutubeDownloader(cookies_file=cookies_path)

            release = Release(
                title="Test Track",
                artist="Test Artist",
                quality=Quality(AudioFormat.OPUS),
                source_name="YouTube",
                download_url="https://youtu.be/test123",
            )

            with patch("yt_dlp.YoutubeDL") as mock_yt_dlp:
                mock_instance = MagicMock()
                mock_yt_dlp.return_value.__enter__.return_value = mock_instance
                mock_instance.extract_info.return_value = {"title": "Test"}
                mock_instance.prepare_filename.return_value = os.path.join(tmpdir, "test.opus")

                # Create the expected output file
                with open(os.path.join(tmpdir, "test.opus"), "w") as f:
                    f.write("fake audio")

                downloader.download(release, tmpdir)

                # Verify yt_dlp was called with cookies
                call_args = mock_yt_dlp.call_args
                opts = call_args[0][0]
                assert opts.get("cookiefile") == cookies_path

    def test_download_without_cookies(self):
        """Test download works without cookies."""
        with tempfile.TemporaryDirectory() as tmpdir:
            downloader = YoutubeDownloader()

            release = Release(
                title="Test Track",
                artist="Test Artist",
                quality=Quality(AudioFormat.OPUS),
                source_name="YouTube",
                download_url="https://youtu.be/test123",
            )

            with patch("yt_dlp.YoutubeDL") as mock_yt_dlp:
                with patch("flacfetch.downloaders.youtube.get_cookies_file", return_value=None):
                    mock_instance = MagicMock()
                    mock_yt_dlp.return_value.__enter__.return_value = mock_instance
                    mock_instance.extract_info.return_value = {"title": "Test"}
                    mock_instance.prepare_filename.return_value = os.path.join(tmpdir, "test.opus")

                    with open(os.path.join(tmpdir, "test.opus"), "w") as f:
                        f.write("fake audio")

                    downloader.download(release, tmpdir)

                    # Verify yt_dlp was called without cookies
                    call_args = mock_yt_dlp.call_args
                    opts = call_args[0][0]
                    assert "cookiefile" not in opts

    def test_download_with_custom_filename(self):
        """Test download with custom filename."""
        with tempfile.TemporaryDirectory() as tmpdir:
            downloader = YoutubeDownloader()

            release = Release(
                title="Test Track",
                artist="Test Artist",
                quality=Quality(AudioFormat.OPUS),
                source_name="YouTube",
                download_url="https://youtu.be/test123",
            )

            with patch("yt_dlp.YoutubeDL") as mock_yt_dlp:
                with patch("flacfetch.downloaders.youtube.get_cookies_file", return_value=None):
                    mock_instance = MagicMock()
                    mock_yt_dlp.return_value.__enter__.return_value = mock_instance
                    mock_instance.extract_info.return_value = {"title": "Test"}
                    output_file = os.path.join(tmpdir, "custom_name.opus")
                    mock_instance.prepare_filename.return_value = output_file

                    with open(output_file, "w") as f:
                        f.write("fake audio")

                    downloader.download(release, tmpdir, output_filename="custom_name")

                    # Verify custom filename template was used
                    call_args = mock_yt_dlp.call_args
                    opts = call_args[0][0]
                    assert "custom_name" in opts["outtmpl"]

    def test_download_error_handling(self):
        """Test download handles errors properly."""
        downloader = YoutubeDownloader()

        release = Release(
            title="Test Track",
            artist="Test Artist",
            quality=Quality(AudioFormat.OPUS),
            source_name="YouTube",
            download_url="https://youtu.be/test123",
        )

        with patch("yt_dlp.YoutubeDL") as mock_yt_dlp:
            with patch("flacfetch.downloaders.youtube.get_cookies_file", return_value=None):
                mock_instance = MagicMock()
                mock_yt_dlp.return_value.__enter__.return_value = mock_instance
                mock_instance.extract_info.side_effect = Exception("Download failed")

                with pytest.raises(Exception, match="Download failed"):
                    downloader.download(release, "/tmp")


class TestYoutubeProviderCookies:
    """Tests for YoutubeProvider with cookies support."""

    def test_init_without_cookies(self):
        """Test initialization without cookies file."""
        provider = YoutubeProvider()
        assert provider.cookies_file is None

    def test_init_with_cookies(self):
        """Test initialization with cookies file."""
        provider = YoutubeProvider(cookies_file="/path/to/cookies.txt")
        assert provider.cookies_file == "/path/to/cookies.txt"

    def test_search_uses_cookies(self):
        """Test search uses cookies when configured."""
        provider = YoutubeProvider(cookies_file="/path/to/cookies.txt")

        with patch("yt_dlp.YoutubeDL") as mock_yt_dlp:
            mock_instance = MagicMock()
            mock_yt_dlp.return_value.__enter__.return_value = mock_instance
            mock_instance.extract_info.return_value = {"entries": []}

            from flacfetch.core.models import TrackQuery

            query = TrackQuery(artist="Test", title="Track")
            provider.search(query)

            # Verify yt_dlp was called with cookies
            call_args = mock_yt_dlp.call_args
            opts = call_args[0][0]
            assert opts.get("cookiefile") == "/path/to/cookies.txt"

