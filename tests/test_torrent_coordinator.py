"""Tests for SharedTorrentRegistry — the coordinator that lets several downloads
share one torrent (same info-hash) without clobbering each other's file
selection or deleting the torrent out from under a sibling.

The registry runs the caller's daemon callbacks (apply_fn / remove_fn) under its
own lock, so selection changes and torrent removal are atomic with the
reference-count transitions they depend on.
"""
import threading

import pytest

from flacfetch.downloaders.torrent_coordinator import SharedTorrentRegistry


class TestFileSelectionMerge:
    def test_join_merges_wanted_into_union(self):
        reg = SharedTorrentRegistry()
        applied = []

        refcount_a, sel_a = reg.join("h", [0], apply_fn=lambda s: applied.append(s))
        assert refcount_a == 1
        assert sel_a == {0}

        # A second job wanting a different file must NOT drop the first job's file.
        refcount_b, sel_b = reg.join("h", [5], apply_fn=lambda s: applied.append(s))
        assert refcount_b == 2
        assert sel_b == {0, 5}
        assert applied == [{0}, {0, 5}]

    def test_join_without_apply_fn_still_counts(self):
        reg = SharedTorrentRegistry()
        refcount, selection = reg.join("h", [], apply_fn=None)
        assert refcount == 1
        assert selection == set()

    def test_selection_shrinks_when_a_selective_job_leaves(self):
        """When a selective job leaves, its file drops out of the wanted set (if no
        other job wants it) and the reduced selection is re-applied under the lock,
        so the torrent stops downloading files nobody wants."""
        reg = SharedTorrentRegistry()
        reg.join("h", [0])
        reg.join("h", [1])

        applied = []
        remaining, _ = reg.leave("h", [0], success=True, apply_fn=lambda s: applied.append(s))
        assert remaining == 1
        assert applied == [{1}]  # file 0 dropped, file 1 still wanted

    def test_shared_file_stays_wanted_until_last_wanter_leaves(self):
        reg = SharedTorrentRegistry()
        reg.join("h", [0])
        reg.join("h", [0, 1])  # both want file 0

        applied = []
        reg.leave("h", [0], success=True, apply_fn=lambda s: applied.append(s))
        # file 0 still wanted by the second job.
        assert applied == [{0, 1}]


class TestApplyFailureRollback:
    def test_apply_fn_raising_rolls_back_refcount(self):
        reg = SharedTorrentRegistry()

        def boom(_selection):
            raise RuntimeError("change_torrent RPC failed")

        with pytest.raises(RuntimeError, match="change_torrent"):
            reg.join("h", [0], apply_fn=boom)

        # Fully rolled back: a fresh join starts clean at refcount 1.
        refcount, _ = reg.join("h", [1])
        assert refcount == 1

    def test_apply_failure_does_not_leak_wanted_ids(self):
        reg = SharedTorrentRegistry()
        reg.join("h", [0])  # healthy sibling wants {0}

        def boom(_selection):
            raise RuntimeError("nope")

        with pytest.raises(RuntimeError):
            reg.join("h", [1], apply_fn=boom)

        # {1} must not linger; the next selective joiner sees only {0, 2}.
        _, selection = reg.join("h", [2])
        assert selection == {0, 2}


class TestWantsAll:
    def test_whole_torrent_join_yields_want_all_selection(self):
        reg = SharedTorrentRegistry()
        applied = []
        _, selection = reg.join("h", [], wants_all=True, apply_fn=lambda s: applied.append(s))
        assert selection is None  # None => "want everything"
        assert applied == [None]

    def test_selective_joiner_after_whole_torrent_does_not_restrict(self):
        reg = SharedTorrentRegistry()
        reg.join("h", [], wants_all=True)
        applied = []
        _, selection = reg.join("h", [3], apply_fn=lambda s: applied.append(s))
        assert selection is None
        assert applied == [None]

    def test_whole_torrent_joiner_overrides_prior_selective_union(self):
        reg = SharedTorrentRegistry()
        _, sel1 = reg.join("h", [2])
        assert sel1 == {2}
        applied = []
        _, sel2 = reg.join("h", [], wants_all=True, apply_fn=lambda s: applied.append(s))
        assert sel2 is None
        assert applied == [None]

    def test_wants_all_released_on_leave_restores_selection(self):
        """When the last whole-torrent job leaves but a selective sibling remains,
        the reduced selection is re-applied under the lock and want-all clears."""
        reg = SharedTorrentRegistry()
        reg.join("h", [3])                 # selective sibling
        reg.join("h", [], wants_all=True)  # whole-torrent job

        applied = []
        remaining, _ = reg.leave("h", [], success=True, wants_all=True,
                                 apply_fn=lambda s: applied.append(s))
        assert remaining == 1
        assert applied == [{3}]  # daemon dropped back to the selective union

        # want-all cleared: a later selective joiner restricts again.
        _, selection = reg.join("h", [4])
        assert selection == {3, 4}

    def test_no_restore_while_another_whole_torrent_job_remains(self):
        reg = SharedTorrentRegistry()
        reg.join("h", [3])
        reg.join("h", [], wants_all=True)
        reg.join("h", [], wants_all=True)

        applied = []
        remaining, _ = reg.leave("h", [], success=True, wants_all=True,
                                 apply_fn=lambda s: applied.append(s))
        assert remaining == 2
        assert applied == [None]  # still want-all


class TestRemovalUnderLock:
    def test_remove_fn_only_when_last_leaves(self):
        reg = SharedTorrentRegistry()
        reg.join("h", [0])
        reg.join("h", [1])

        removed = []
        applied = []
        remaining, _ = reg.leave("h", [0], success=False,
                                 apply_fn=lambda s: applied.append(s),
                                 remove_fn=lambda ok: removed.append(ok))
        assert remaining == 1
        assert removed == []          # sibling remains — do NOT remove
        assert applied == [{1}]       # selection reduced instead

        remaining, _ = reg.leave("h", [1], success=False,
                                 apply_fn=lambda s: applied.append(s),
                                 remove_fn=lambda ok: removed.append(ok))
        assert remaining == 0
        assert removed == [False]     # last one out — remove, no success anywhere

    def test_remove_fn_sees_any_success_across_sharers(self):
        reg = SharedTorrentRegistry()
        reg.join("h", [0])
        reg.join("h", [1])
        reg.leave("h", [0], success=True)          # first succeeds
        removed = []
        reg.leave("h", [1], success=False, remove_fn=lambda ok: removed.append(ok))
        assert removed == [True]  # torrent holds good data -> caller keeps seeding

    def test_leave_unknown_hash_still_cleans_up(self):
        reg = SharedTorrentRegistry()
        removed = []
        remaining, any_success = reg.leave("nope", [], success=True,
                                           remove_fn=lambda ok: removed.append(ok))
        assert remaining == 0
        assert any_success is True
        assert removed == [True]

    def test_entry_recreated_after_fully_drained(self):
        reg = SharedTorrentRegistry()
        reg.join("h", [0])
        reg.leave("h", [0], success=True, remove_fn=lambda ok: None)
        refcount, selection = reg.join("h", [9])
        assert refcount == 1
        assert selection == {9}


class TestThreadSafety:
    def test_concurrent_join_leave_balances(self):
        reg = SharedTorrentRegistry()
        start = threading.Event()

        def worker(i):
            start.wait()
            reg.join("h", [i])
            reg.leave("h", [i], success=(i % 2 == 0))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        start.set()
        for t in threads:
            t.join()

        # Everyone left -> entry fully drained; a fresh join starts at 1.
        refcount, _ = reg.join("h", [0])
        assert refcount == 1
