"""
Tests for TorrentDownloader with keep_seeding mode.
"""
import itertools
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


class TestKeepSeedingCopySemantics:
    """Regression tests for how the completed file is placed into the output dir.

    The flacfetch service runs as a non-root user that is only a *group member*
    of the transmission-owned downloads. `shutil.copy2` runs `copystat()`, which
    `utime()`/`chmod()`s the destination — and for single-file torrents the
    destination is the transmission-owned download itself, so a non-owner hits
    EPERM ("Operation not permitted"). We must copy CONTENT ONLY, and skip the
    copy entirely when source and target are the same file.
    """

    def _run_seeding_download(self, download_dir, file_name, output_path,
                              output_filename=None, keep_seeding=True):
        with patch('flacfetch.downloaders.torrent.transmission_rpc') as mock_rpc:
            from flacfetch.downloaders.torrent import TorrentDownloader

            mock_client = Mock()
            mock_rpc.Client.return_value = mock_client

            with tempfile.NamedTemporaryFile(suffix='.torrent', delete=False) as f:
                f.write(b'd8:announce20:http://tracker/annce4:infod4:name4:teste')
                torrent_path = f.name

            try:
                mock_release = Mock()
                mock_release.download_url = torrent_path
                mock_release.title = "Test Single"
                mock_release.target_file = file_name

                mock_file = Mock()
                mock_file.name = file_name
                mock_file.id = 0
                mock_file.selected = True

                mock_torrent = Mock()
                mock_torrent.id = 1
                mock_torrent.name = "Test Single"
                mock_torrent.status = "seeding"
                mock_torrent.progress = 100
                mock_torrent.get_files.return_value = [mock_file]

                mock_client.add_torrent.return_value = mock_torrent
                mock_client.get_torrent.return_value = mock_torrent

                with patch.dict(os.environ,
                                {'FLACFETCH_DOWNLOAD_DIR': download_dir}):
                    downloader = TorrentDownloader(keep_seeding=keep_seeding)
                    downloader.client = mock_client
                    downloader._ensure_daemon_running = Mock(return_value=True)
                    return downloader.download(
                        mock_release, output_path,
                        output_filename=output_filename,
                    )
            finally:
                os.unlink(torrent_path)

    def test_keep_seeding_uses_copyfile_not_copy2(self):
        """A distinct target must be populated via copyfile (content only), never
        copy2 — copy2's copystat() EPERMs on transmission-owned files."""
        with tempfile.TemporaryDirectory() as download_dir, \
                tempfile.TemporaryDirectory() as out_dir:
            # source lives in a subfolder (album-style) so target != source
            sub = os.path.join(download_dir, "album")
            os.makedirs(sub)
            src_rel = "album/04 - dog.flac"
            with open(os.path.join(download_dir, src_rel), 'wb') as fh:
                fh.write(b'FLACDATA')

            with patch('flacfetch.downloaders.torrent.shutil.copy2') as m_copy2, \
                    patch('flacfetch.downloaders.torrent.shutil.copyfile',
                          wraps=__import__('shutil').copyfile) as m_copyfile:
                result = self._run_seeding_download(
                    download_dir, src_rel, out_dir,
                    output_filename="piri - dog",
                )

            assert not m_copy2.called, "copy2 must not be used (copystat EPERMs)"
            assert m_copyfile.called, "copyfile should place the file"
            assert os.path.exists(result)
            with open(result, 'rb') as fh:
                assert fh.read() == b'FLACDATA'

    def test_single_file_same_path_skips_copy(self):
        """Single-file torrent already at the output path: no copy at all, return
        the source path (it's in place and still seeding)."""
        with tempfile.TemporaryDirectory() as download_dir:
            file_name = "04 - dog.flac"
            src = os.path.join(download_dir, file_name)
            with open(src, 'wb') as fh:
                fh.write(b'FLACDATA')

            with patch('flacfetch.downloaders.torrent.shutil.copy2') as m_copy2, \
                    patch('flacfetch.downloaders.torrent.shutil.copyfile') as m_copyfile, \
                    patch('flacfetch.downloaders.torrent.shutil.move') as m_move:
                # output dir == download dir, no rename → target resolves to source
                result = self._run_seeding_download(
                    download_dir, file_name, download_dir,
                    output_filename=None,
                )

            assert not m_copy2.called
            assert not m_copyfile.called
            assert not m_move.called
            assert os.path.realpath(result) == os.path.realpath(src)
            assert os.path.exists(result)


class TestAddTorrentMetainfo:
    """Regression tests for how the torrent is handed to transmission."""

    def test_add_torrent_passes_bytes_not_path(self):
        """The .torrent must be added as raw bytes (base64 `metainfo`), NOT as a
        file-path string. transmission-rpc sends a path string as `filename` for
        the daemon to read locally, which transmission 4.x rejects with
        "unrecognized info" (it worked on 3.x). Bytes/metainfo works on both.
        """
        with patch('flacfetch.downloaders.torrent.transmission_rpc') as mock_rpc:
            from flacfetch.downloaders.torrent import TorrentDownloader

            mock_client = Mock()
            mock_rpc.Client.return_value = mock_client

            torrent_bytes = b'd8:announce20:http://tracker/annce4:infod4:name4:teste'
            with tempfile.NamedTemporaryFile(suffix='.torrent', delete=False) as f:
                f.write(torrent_bytes)
                torrent_path = f.name

            try:
                mock_release = Mock()
                mock_release.download_url = torrent_path
                mock_release.title = "Test Album"
                mock_release.target_file = None  # skip selective-download branch

                mock_torrent = Mock()
                mock_torrent.id = 1
                mock_torrent.name = "Test Album"
                mock_client.add_torrent.return_value = mock_torrent
                mock_client.get_torrent.return_value = mock_torrent

                downloader = TorrentDownloader()
                downloader.client = mock_client
                downloader._ensure_daemon_running = Mock(return_value=True)

                try:
                    downloader.download(mock_release, tempfile.gettempdir())
                except Exception:
                    pass  # may fail later awaiting completion; we only assert the add call

                assert mock_client.add_torrent.called, "add_torrent was never called"
                call = mock_client.add_torrent.call_args
                first_arg = call.args[0] if call.args else call.kwargs.get('torrent')
                assert isinstance(first_arg, (bytes, bytearray)), (
                    f"add_torrent must receive bytes (metainfo), got {type(first_arg).__name__}"
                )
                assert first_arg == torrent_bytes
                assert first_arg != torrent_path
            finally:
                os.unlink(torrent_path)


class _FakeTransmissionError(Exception):
    """Stand-in for transmission_rpc.error.TransmissionError when the whole
    transmission_rpc module is mocked — the downloader has an
    `except transmission_rpc.error.TransmissionError` clause, and Python can only
    catch real exception classes, not Mock attributes."""


class TestStallHandling:
    """The monitor loop must recover from (re-announce) or bail out of a stalled
    download instead of spinning forever — the unbounded loop previously leaked a
    hung background task on every caller retry until the API stopped responding.
    """

    @staticmethod
    def _write_torrent():
        with tempfile.NamedTemporaryFile(suffix='.torrent', delete=False) as f:
            f.write(b'd8:announce20:http://tracker/annce4:infod4:name4:teste')
            return f.name

    def _run_stalled_download(self, keep_seeding, output_dir):
        """Drive download() against a torrent that never transfers (0 B/s at a
        constant percentage) and return the mock client so callers can assert on
        re-announce / removal. Raises the RuntimeError from the hard-stall abort.
        """
        with patch('flacfetch.downloaders.torrent.transmission_rpc') as mock_rpc, \
                patch('flacfetch.downloaders.torrent.time') as mock_time, \
                patch.dict(os.environ, {'FLACFETCH_DOWNLOAD_DIR': output_dir}):
            from flacfetch.downloaders.torrent import TorrentDownloader

            mock_client = Mock()
            mock_rpc.Client.return_value = mock_client
            mock_rpc.error.TransmissionError = _FakeTransmissionError
            # Advance the monotonic clock 100s per call so the default thresholds
            # (re-announce at 90s, abort at 600s) are crossed deterministically,
            # and never actually sleep.
            mock_time.monotonic.side_effect = itertools.count(0, 100)
            mock_time.sleep = Mock()

            stuck = Mock()
            stuck.id = 1
            stuck.name = "Stuck Album"
            stuck.status = "downloading"
            stuck.progress = 43.0  # never changes
            stuck.rate_download = 0  # 0 B/s — the stall we're guarding against
            stuck.rate_upload = 0
            stuck.peers_connected = 50
            mock_client.add_torrent.return_value = stuck
            mock_client.get_torrent.return_value = stuck

            torrent_path = self._write_torrent()
            try:
                mock_release = Mock()
                mock_release.download_url = torrent_path
                mock_release.title = "Stuck Album"
                mock_release.target_file = None  # skip selective-download branch

                downloader = TorrentDownloader(keep_seeding=keep_seeding)
                downloader.client = mock_client
                downloader._ensure_daemon_running = Mock(return_value=True)

                downloader.download(mock_release, output_dir)
            finally:
                os.unlink(torrent_path)

    def test_stall_reannounces_then_aborts(self):
        """A torrent stuck at constant progress gets a forced tracker re-announce
        and is aborted once the hard stall ceiling is crossed."""
        with tempfile.TemporaryDirectory() as out_dir:
            with pytest.raises(RuntimeError, match="stalled"):
                self._run_stalled_download(keep_seeding=False, output_dir=out_dir)

    def test_stall_reannounces_before_aborting(self):
        """The stall path forces at least one tracker re-announce (fresh peer
        list) before giving up."""
        with tempfile.TemporaryDirectory() as out_dir:
            with patch('flacfetch.downloaders.torrent.transmission_rpc') as mock_rpc, \
                    patch('flacfetch.downloaders.torrent.time') as mock_time, \
                    patch.dict(os.environ, {'FLACFETCH_DOWNLOAD_DIR': out_dir}):
                from flacfetch.downloaders.torrent import TorrentDownloader

                mock_client = Mock()
                mock_rpc.Client.return_value = mock_client
                mock_rpc.error.TransmissionError = _FakeTransmissionError
                mock_time.monotonic.side_effect = itertools.count(0, 100)
                mock_time.sleep = Mock()

                stuck = Mock()
                stuck.id = 1
                stuck.name = "Stuck Album"
                stuck.status = "downloading"
                stuck.progress = 43.0
                stuck.rate_download = 0
                stuck.rate_upload = 0
                stuck.peers_connected = 50
                mock_client.add_torrent.return_value = stuck
                mock_client.get_torrent.return_value = stuck

                torrent_path = self._write_torrent()
                try:
                    mock_release = Mock()
                    mock_release.download_url = torrent_path
                    mock_release.title = "Stuck Album"
                    mock_release.target_file = None

                    downloader = TorrentDownloader()
                    downloader.client = mock_client
                    downloader._ensure_daemon_running = Mock(return_value=True)

                    with pytest.raises(RuntimeError, match="stalled"):
                        downloader.download(mock_release, out_dir)

                    assert mock_client.reannounce_torrent.called, (
                        "a stalled download should force a tracker re-announce"
                    )
                    assert mock_client.remove_torrent.called
                finally:
                    os.unlink(torrent_path)

    def test_keep_seeding_stall_removes_torrent(self):
        """A failed (stalled) keep_seeding download must NOT be left seeding — the
        torrent is removed so the next retry re-adds fresh instead of re-attaching
        to the same stuck state (transmission dedupes adds by info-hash)."""
        with tempfile.TemporaryDirectory() as out_dir:
            with patch('flacfetch.downloaders.torrent.transmission_rpc') as mock_rpc, \
                    patch('flacfetch.downloaders.torrent.time') as mock_time, \
                    patch.dict(os.environ, {'FLACFETCH_DOWNLOAD_DIR': out_dir}):
                from flacfetch.downloaders.torrent import TorrentDownloader

                mock_client = Mock()
                mock_rpc.Client.return_value = mock_client
                mock_rpc.error.TransmissionError = _FakeTransmissionError
                mock_time.monotonic.side_effect = itertools.count(0, 100)
                mock_time.sleep = Mock()

                stuck = Mock()
                stuck.id = 7
                stuck.name = "Stuck Album"
                stuck.status = "downloading"
                stuck.progress = 43.0
                stuck.rate_download = 0
                stuck.rate_upload = 0
                stuck.peers_connected = 50
                mock_client.add_torrent.return_value = stuck
                mock_client.get_torrent.return_value = stuck

                torrent_path = self._write_torrent()
                try:
                    mock_release = Mock()
                    mock_release.download_url = torrent_path
                    mock_release.title = "Stuck Album"
                    mock_release.target_file = None

                    downloader = TorrentDownloader(keep_seeding=True)
                    downloader.client = mock_client
                    downloader._ensure_daemon_running = Mock(return_value=True)

                    with pytest.raises(RuntimeError, match="stalled"):
                        downloader.download(mock_release, out_dir)

                    mock_client.remove_torrent.assert_called_once_with(
                        7, delete_data=True
                    )
                finally:
                    os.unlink(torrent_path)

    def test_progress_does_not_trigger_stall_logic(self):
        """A download that keeps making progress must never be re-announced or
        aborted — it should complete normally."""
        with patch('flacfetch.downloaders.torrent.transmission_rpc') as mock_rpc, \
                patch('flacfetch.downloaders.torrent.time') as mock_time:
            from flacfetch.downloaders.torrent import TorrentDownloader

            mock_client = Mock()
            mock_rpc.Client.return_value = mock_client
            mock_rpc.error.TransmissionError = _FakeTransmissionError
            mock_time.monotonic.side_effect = itertools.count(0, 10)
            mock_time.sleep = Mock()

            def make(status, progress):
                t = Mock()
                t.id = 1
                t.name = "Healthy"
                t.status = status
                t.progress = progress
                t.rate_download = 500_000
                t.rate_upload = 0
                t.peers_connected = 20
                t.get_files.return_value = []
                return t

            # progress climbs, then flips to seeding → completes. Extra seeding
            # entry covers the completion branch's re-fetch of the torrent.
            mock_client.get_torrent.side_effect = [
                make("downloading", 20.0),
                make("downloading", 60.0),
                make("downloading", 95.0),
                make("seeding", 100.0),
                make("seeding", 100.0),
            ]
            mock_client.add_torrent.return_value = make("downloading", 0.0)

            torrent_path = self._write_torrent()
            try:
                mock_release = Mock()
                mock_release.download_url = torrent_path
                mock_release.title = "Healthy"
                mock_release.target_file = None

                downloader = TorrentDownloader()
                downloader.client = mock_client
                downloader._ensure_daemon_running = Mock(return_value=True)

                downloader.download(mock_release, tempfile.gettempdir())

                assert not mock_client.reannounce_torrent.called, (
                    "a progressing download must not be re-announced or aborted"
                )
            finally:
                os.unlink(torrent_path)
