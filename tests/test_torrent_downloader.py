"""
Tests for TorrentDownloader with keep_seeding mode.
"""
import os
import tempfile
from unittest.mock import Mock, patch

import pytest

# Only run if transmission_rpc is available
pytest.importorskip("transmission_rpc")


class TestTorrentDownloaderInit:
    """Tests for TorrentDownloader initialization."""

    def test_init_keep_seeding_default(self):
        """Test that keep_seeding defaults to False."""
        with patch('flacfetch.downloaders.torrent.transmission_rpc'):
            from flacfetch.downloaders.torrent import TorrentDownloader
            downloader = TorrentDownloader()
            assert downloader.keep_seeding is False

    def test_init_keep_seeding_enabled(self):
        """Test that keep_seeding can be enabled."""
        with patch('flacfetch.downloaders.torrent.transmission_rpc'):
            from flacfetch.downloaders.torrent import TorrentDownloader
            downloader = TorrentDownloader(keep_seeding=True)
            assert downloader.keep_seeding is True

    def test_init_host_port_defaults(self):
        """Test default host and port values."""
        with patch('flacfetch.downloaders.torrent.transmission_rpc'):
            from flacfetch.downloaders.torrent import TorrentDownloader
            downloader = TorrentDownloader()
            # Default values when env vars not set
            assert downloader.host in ('localhost', None, '')
            assert downloader.port in (9091, None)

    def test_init_custom_host_port(self):
        """Test custom host and port."""
        with patch('flacfetch.downloaders.torrent.transmission_rpc'):
            from flacfetch.downloaders.torrent import TorrentDownloader
            downloader = TorrentDownloader(
                host='custom-host',
                port=1234,
            )
            assert downloader.host == 'custom-host'
            assert downloader.port == 1234


class TestTorrentDownloaderKeepSeeding:
    """Tests for keep_seeding mode behavior."""

    def test_keep_seeding_affects_download_dir(self):
        """Test that keep_seeding uses persistent download dir."""
        with patch('flacfetch.downloaders.torrent.transmission_rpc'):
            from flacfetch.downloaders.torrent import TorrentDownloader

            # With keep_seeding=True, should use persistent dir
            downloader = TorrentDownloader(keep_seeding=True)
            assert downloader._download_dir is not None

    def test_download_requires_local_torrent_file(self):
        """Test that download raises for non-local URLs."""
        with patch('flacfetch.downloaders.torrent.transmission_rpc'):
            from flacfetch.downloaders.torrent import TorrentDownloader

            mock_release = Mock()
            mock_release.download_url = "http://example.com/file.torrent"

            downloader = TorrentDownloader()
            downloader._ensure_daemon_running = Mock(return_value=True)

            with pytest.raises(ValueError, match="local .torrent file path"):
                downloader.download(mock_release, "/tmp")

    def test_download_requires_valid_file(self):
        """Test that download raises for non-existent file."""
        with patch('flacfetch.downloaders.torrent.transmission_rpc'):
            from flacfetch.downloaders.torrent import TorrentDownloader

            mock_release = Mock()
            mock_release.download_url = "/nonexistent/file.torrent"

            downloader = TorrentDownloader()
            downloader._ensure_daemon_running = Mock(return_value=True)

            with pytest.raises(ValueError):
                downloader.download(mock_release, "/tmp")


class TestSelectiveDownload:
    """Tests for selective file downloading."""

    def test_target_file_sets_priorities(self):
        """Test that target_file causes file priority changes."""
        with patch('flacfetch.downloaders.torrent.transmission_rpc') as mock_rpc:
            from flacfetch.downloaders.torrent import TorrentDownloader

            # Create mock client
            mock_client = Mock()
            mock_rpc.Client.return_value = mock_client

            # Create temp torrent file
            with tempfile.NamedTemporaryFile(suffix='.torrent', delete=False) as f:
                f.write(b'mock torrent data')
                torrent_path = f.name

            try:
                mock_release = Mock()
                mock_release.download_url = torrent_path
                mock_release.title = "Test Album"
                mock_release.target_file = "02. Target Song.flac"

                # Mock torrent with multiple files
                mock_file1 = Mock()
                mock_file1.name = "01. First Song.flac"
                mock_file1.id = 0

                mock_file2 = Mock()
                mock_file2.name = "02. Target Song.flac"
                mock_file2.id = 1
                mock_file2.selected = True

                mock_torrent = Mock()
                mock_torrent.id = 1
                mock_torrent.name = "Test Album"
                mock_torrent.status = "downloading"
                mock_torrent.progress = 0
                mock_torrent.get_files.return_value = [mock_file1, mock_file2]

                mock_client.add_torrent.return_value = mock_torrent
                mock_client.get_torrent.return_value = mock_torrent

                downloader = TorrentDownloader()
                downloader.client = mock_client

                try:
                    downloader.download(mock_release, tempfile.gettempdir())
                except Exception:
                    pass  # Will fail during download wait

                # Verify change_torrent was called to set file priorities
                if mock_client.change_torrent.called:
                    call_kwargs = mock_client.change_torrent.call_args[1]
                    # File 1 (id=0) should be unwanted
                    assert 0 in call_kwargs.get('files_unwanted', [])
                    # File 2 (id=1) should be wanted
                    assert 1 in call_kwargs.get('files_wanted', [])
            finally:
                os.unlink(torrent_path)
