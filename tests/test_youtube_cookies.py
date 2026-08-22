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
    get_ytdlp_cache_dir,
    isolated_cookiefile,
    ytdlp_opts_isolated,
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


class TestGetYtdlpCacheDir:
    """Tests for the FLACFETCH_YTDLP_CACHE_DIR override.

    On the server, HOME/.cache is the Spotify token *file*, so yt-dlp's default
    ~/.cache/yt-dlp path raises NotADirectoryError and caching is silently lost.
    """

    def test_returns_none_when_env_unset(self):
        """No override configured -> use yt-dlp's default (None)."""
        with patch.dict(os.environ, {}, clear=True):
            assert get_ytdlp_cache_dir() is None

    def test_creates_and_returns_dir_when_env_set(self):
        """Env set -> directory is created and returned."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = os.path.join(tmpdir, "ytdlp-cache")
            with patch.dict(os.environ, {"FLACFETCH_YTDLP_CACHE_DIR": cache_dir}):
                result = get_ytdlp_cache_dir()
                assert result == cache_dir
                assert os.path.isdir(cache_dir)

    def test_returns_none_when_dir_uncreatable(self):
        """A bad cache path must not break downloads (caching just disabled)."""
        with patch.dict(os.environ, {"FLACFETCH_YTDLP_CACHE_DIR": "/some/cache"}):
            with patch(
                "flacfetch.downloaders.youtube.os.makedirs",
                side_effect=OSError("not a directory"),
            ):
                assert get_ytdlp_cache_dir() is None

    def test_base_opts_include_cachedir_when_set(self):
        """get_ytdlp_base_opts wires cachedir through alongside cookies."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = os.path.join(tmpdir, "ytdlp-cache")
            with patch.dict(os.environ, {"FLACFETCH_YTDLP_CACHE_DIR": cache_dir}):
                with patch(
                    "flacfetch.downloaders.youtube.get_cookies_file",
                    return_value=None,
                ):
                    result = get_ytdlp_base_opts()
                    assert result == {"cachedir": cache_dir}

    def test_base_opts_no_cachedir_when_unset(self):
        """Without the override, base opts carry no cachedir key."""
        with patch.dict(os.environ, {}, clear=True):
            with patch(
                "flacfetch.downloaders.youtube.get_cookies_file",
                return_value=None,
            ):
                result = get_ytdlp_base_opts()
                assert "cachedir" not in result


class TestIsolatedCookiefile:
    """Tests for the cookie write-back isolation (protects keeper's cookies).

    yt-dlp saves the rotated cookie jar back to the cookiefile on every run; on a
    flagged IP those rotations are rejected, poisoning the shared file. Each run
    must therefore operate on a throwaway copy.
    """

    def test_none_passes_through(self):
        with isolated_cookiefile(None) as cf:
            assert cf is None

    def test_missing_file_passes_through(self):
        with isolated_cookiefile("/no/such/cookies.txt") as cf:
            assert cf == "/no/such/cookies.txt"

    def test_yields_distinct_copy_then_cleans_up(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            canonical = os.path.join(tmpdir, "youtube_cookies.txt")
            with open(canonical, "w") as f:
                f.write("# Netscape\noriginal-cookie\n")

            copy_path = None
            with isolated_cookiefile(canonical) as cf:
                copy_path = cf
                assert cf != canonical
                assert os.path.exists(cf)
                with open(cf) as f:
                    assert "original-cookie" in f.read()

            # Temp copy is removed after the context exits.
            assert not os.path.exists(copy_path)

    def test_falls_back_to_canonical_when_copy_fails(self):
        """A copy failure must not break the download — fall back to the original."""
        with tempfile.TemporaryDirectory() as tmpdir:
            canonical = os.path.join(tmpdir, "cookies.txt")
            with open(canonical, "w") as f:
                f.write("# cookies\n")

            with patch(
                "flacfetch.downloaders.youtube.shutil.copyfile",
                side_effect=OSError("disk full"),
            ):
                with isolated_cookiefile(canonical) as cf:
                    assert cf == canonical

    def test_writeback_to_copy_does_not_touch_canonical(self):
        """The core guarantee: simulated yt-dlp write-back hits only the copy."""
        with tempfile.TemporaryDirectory() as tmpdir:
            canonical = os.path.join(tmpdir, "youtube_cookies.txt")
            with open(canonical, "w") as f:
                f.write("GOOD-KEEPER-COOKIES\n")

            with isolated_cookiefile(canonical) as cf:
                # yt-dlp would overwrite the cookiefile with rotated (rejected) data.
                with open(cf, "w") as f:
                    f.write("POISONED-ROTATED-COOKIES\n")

            with open(canonical) as f:
                assert f.read() == "GOOD-KEEPER-COOKIES\n"


class TestYtdlpOptsIsolated:
    """Tests for ytdlp_opts_isolated wrapper."""

    def test_noop_without_cookiefile(self):
        opts = {"quiet": True}
        with ytdlp_opts_isolated(opts) as isolated:
            assert isolated is opts

    def test_swaps_cookiefile_to_copy_and_preserves_original_dict(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            canonical = os.path.join(tmpdir, "cookies.txt")
            with open(canonical, "w") as f:
                f.write("# cookies\n")

            opts = {"cookiefile": canonical, "quiet": True}
            with ytdlp_opts_isolated(opts) as isolated:
                assert isolated["cookiefile"] != canonical
                assert os.path.exists(isolated["cookiefile"])
                assert isolated["quiet"] is True
                # Original dict is left untouched (shallow copy semantics).
                assert opts["cookiefile"] == canonical


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

                # Verify yt_dlp was called with cookies — but an *isolated copy*,
                # not the canonical file, so its cookie write-back can't poison the
                # keeper-managed cookies.
                call_args = mock_yt_dlp.call_args
                opts = call_args[0][0]
                cookiefile_used = opts.get("cookiefile")
                assert cookiefile_used
                assert cookiefile_used != cookies_path

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

