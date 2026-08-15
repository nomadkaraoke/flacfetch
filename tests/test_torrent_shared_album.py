"""Regression test for the shared-album-torrent scenario.

Several tracks from one album (a bulk batch) resolve to the SAME torrent. Since
Transmission dedupes an add by info-hash, the concurrent downloads share one
torrent instance. Before coordination, each download set its own file priorities
(starving all-but-one of its target file) and the first to finish removed the
torrent out from under the others -> "Torrent not found in result".

This test drives two concurrent download() calls against a fake shared daemon and
asserts both extract their own file and neither fails.
"""
import os
import tempfile
import threading

import pytest

pytest.importorskip("transmission_rpc")


class _FakeFile:
    __slots__ = ("id", "name", "size", "completed", "selected")

    def __init__(self, fid, name, size):
        self.id = fid
        self.name = name
        self.size = size
        self.completed = 0
        self.selected = True  # transmission wants everything until told otherwise


class _FakeTorrent:
    """Read-only view over the shared daemon state for one poll."""

    def __init__(self, daemon):
        self._d = daemon
        self.id = 1
        self.name = "Whatever People Say I Am"
        self.hashString = daemon.info_hash
        self.rate_download = 100_000
        self.rate_upload = 0
        self.peers_connected = 20
        self.error_string = ""

    @property
    def status(self):
        return "seeding" if self._d.all_files_done() else "downloading"

    @property
    def progress(self):
        return self._d.progress()

    def get_files(self):
        return self._d.snapshot_files()


class _FakeSharedDaemon:
    """Models ONE transmission torrent shared by every add of the same info-hash.

    Honours the real absolute-set semantics of change_torrent(files_wanted/
    files_unwanted), and only lets a file "download" (complete) once every
    concurrent job has started — so the test genuinely exercises the case where a
    later joiner could otherwise un-want an earlier joiner's file.
    """

    def __init__(self, files, expected_starters):
        self.info_hash = "deadbeef" * 5
        self._files = files
        self._lock = threading.Lock()
        self._started = 0
        self._expected = expected_starters
        self._all_started = threading.Event()
        self.removed = False
        self.remove_calls = []

    # --- transmission_rpc.Client surface ---
    def add_torrent(self, metainfo, download_dir=None, paused=True):
        return _FakeTorrent(self)

    def get_torrent(self, tid):
        with self._lock:
            if self.removed:
                raise KeyError("Torrent not found in result")
            # Advance download: wanted files complete once everyone has started.
            if self._all_started.is_set():
                for f in self._files:
                    if f.selected:
                        f.completed = f.size
        return _FakeTorrent(self)

    def change_torrent(self, tid, files_wanted=None, files_unwanted=None):
        with self._lock:
            for f in self._files:
                if files_unwanted and f.id in files_unwanted:
                    f.selected = False
            for f in self._files:
                if files_wanted and f.id in files_wanted:
                    f.selected = True

    def start_torrent(self, tid):
        with self._lock:
            self._started += 1
            if self._started >= self._expected:
                self._all_started.set()

    def reannounce_torrent(self, tid):
        pass

    def remove_torrent(self, tid, delete_data=False):
        with self._lock:
            self.removed = True
            self.remove_calls.append((tid, delete_data))

    # --- test helpers ---
    def snapshot_files(self):
        with self._lock:
            out = []
            for f in self._files:
                g = _FakeFile(f.id, f.name, f.size)
                g.completed = f.completed
                g.selected = f.selected
                out.append(g)
            return out

    def all_files_done(self):
        return all(f.completed >= f.size for f in self._files if f.selected)

    def progress(self):
        wanted = [f for f in self._files if f.selected]
        if not wanted:
            return 0.0
        return 100.0 * sum(f.completed for f in wanted) / sum(f.size for f in wanted)


def _make_release(download_url, target_file):
    class _R:
        pass

    r = _R()
    r.download_url = download_url
    r.title = target_file
    r.target_file = target_file
    return r


def test_two_tracks_same_album_both_succeed(monkeypatch):
    """Two concurrent downloads of different tracks from ONE album torrent must
    both extract their own file — no starvation, no 'Torrent not found in result'.
    """
    from flacfetch.downloaders import torrent_coordinator
    from flacfetch.downloaders.torrent import TorrentDownloader

    # Fresh registry so this test is isolated from module state.
    monkeypatch.setattr(
        torrent_coordinator, "SHARED_TORRENT_REGISTRY",
        torrent_coordinator.SharedTorrentRegistry(),
    )
    import flacfetch.downloaders.torrent as torrent_mod
    monkeypatch.setattr(
        torrent_mod, "SHARED_TORRENT_REGISTRY",
        torrent_coordinator.SHARED_TORRENT_REGISTRY,
    )

    with tempfile.TemporaryDirectory() as download_dir, \
            tempfile.TemporaryDirectory() as out_dir:
        track_a = "01 - The View from the Afternoon.flac"
        track_b = "05 - You Probably Couldn't See.flac"
        for name in (track_a, track_b):
            with open(os.path.join(download_dir, name), "wb") as fh:
                fh.write(b"FLACDATA")

        daemon = _FakeSharedDaemon(
            files=[_FakeFile(0, track_a, 8), _FakeFile(1, track_b, 8)],
            expected_starters=2,
        )

        # A dummy .torrent file on disk (download() reads its bytes).
        torrent_path = os.path.join(download_dir, "album.torrent")
        with open(torrent_path, "wb") as fh:
            fh.write(b"d4:infod4:name4:teste")

        results = {}
        errors = {}

        def run(label, target_file):
            os.environ["FLACFETCH_DOWNLOAD_DIR"] = download_dir
            dl = TorrentDownloader(keep_seeding=True)
            dl.client = daemon
            dl._ensure_daemon_running = lambda: True
            try:
                results[label] = dl.download(_make_release(torrent_path, target_file), out_dir)
            except Exception as e:  # noqa: BLE001
                errors[label] = e

        ta = threading.Thread(target=run, args=("a", track_a))
        tb = threading.Thread(target=run, args=("b", track_b))
        ta.start()
        tb.start()
        ta.join(timeout=15)
        tb.join(timeout=15)

        assert not errors, f"downloads raised: {errors}"
        assert set(results) == {"a", "b"}
        # Each job extracted ITS OWN track.
        assert os.path.basename(results["a"]) == track_a or results["a"].endswith(track_a)
        assert os.path.basename(results["b"]) == track_b or results["b"].endswith(track_b)
        # The torrent is only removed once (by the last leaver) — and since
        # keep_seeding + success, not at all here.
        assert daemon.remove_calls == []
        assert not daemon.removed
