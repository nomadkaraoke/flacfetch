"""Tests for SharedTorrentRegistry — the coordinator that lets several downloads
share one torrent (same info-hash) without clobbering each other's file
selection or deleting the torrent out from under a sibling."""
import threading

import pytest

from flacfetch.downloaders.torrent_coordinator import SharedTorrentRegistry


class TestFileSelectionMerge:
    def test_join_merges_wanted_into_union(self):
        reg = SharedTorrentRegistry()
        applied = []

        refcount_a, union_a = reg.join("hashA", [0], apply_fn=lambda u: applied.append(set(u)))
        assert refcount_a == 1
        assert union_a == {0}

        # A second job wanting a different file must NOT drop the first job's file.
        refcount_b, union_b = reg.join("hashA", [5], apply_fn=lambda u: applied.append(set(u)))
        assert refcount_b == 2
        assert union_b == {0, 5}

        # Each apply_fn saw the union AS OF its join (monotonically growing).
        assert applied == [{0}, {0, 5}]

    def test_apply_fn_runs_before_join_returns(self):
        """The selection is applied while the registry lock is held, so it is
        serialized with the merge — never a stale, narrower set applied late."""
        reg = SharedTorrentRegistry()
        seen = {}

        def apply(union):
            seen["union"] = set(union)

        _, union = reg.join("h", [1, 2], apply_fn=apply)
        assert seen["union"] == {1, 2} == union

    def test_join_without_apply_fn_still_counts(self):
        reg = SharedTorrentRegistry()
        refcount, union = reg.join("h", [], apply_fn=None)
        assert refcount == 1
        assert union == set()


class TestApplyFailureRollback:
    def test_apply_fn_raising_rolls_back_refcount(self):
        """If pushing the selection to the daemon fails, the join must NOT leak a
        reference (which would stop the torrent ever being cleaned up)."""
        reg = SharedTorrentRegistry()

        def boom(_selection):
            raise RuntimeError("change_torrent RPC failed")

        with pytest.raises(RuntimeError, match="change_torrent"):
            reg.join("h", [0], apply_fn=boom)

        # Entry was fully rolled back: a subsequent leave sees nothing, and a
        # fresh join starts clean at refcount 1.
        remaining, _ = reg.leave("h", success=False)
        assert remaining == 0
        refcount, _ = reg.join("h", [1])
        assert refcount == 1

    def test_rollback_preserves_a_healthy_sibling(self):
        reg = SharedTorrentRegistry()
        reg.join("h", [0])  # healthy first joiner

        def boom(_selection):
            raise RuntimeError("nope")

        with pytest.raises(RuntimeError):
            reg.join("h", [1], apply_fn=boom)

        # The failed joiner rolled back; the healthy sibling is still the only ref.
        remaining, _ = reg.leave("h", success=True)
        assert remaining == 0


class TestWantsAll:
    def test_whole_torrent_join_yields_want_all_selection(self):
        reg = SharedTorrentRegistry()
        applied = []
        _, selection = reg.join("h", [], wants_all=True,
                                apply_fn=lambda s: applied.append(s))
        assert selection is None  # None => "want everything"
        assert applied == [None]

    def test_selective_joiner_after_whole_torrent_does_not_restrict(self):
        """A whole-torrent job needs every file; a later selective joiner must be
        told to want everything (selection=None), not just its own file."""
        reg = SharedTorrentRegistry()
        reg.join("h", [], wants_all=True)

        applied = []
        _, selection = reg.join("h", [3], apply_fn=lambda s: applied.append(s))
        assert selection is None
        assert applied == [None]

    def test_whole_torrent_joiner_overrides_prior_selective_union(self):
        reg = SharedTorrentRegistry()
        # A selective joiner restricts to {2} ...
        _, sel1 = reg.join("h", [2])
        assert sel1 == {2}
        # ... then a whole-torrent joiner arrives: selection flips to want-all.
        applied = []
        _, sel2 = reg.join("h", [], wants_all=True,
                           apply_fn=lambda s: applied.append(s))
        assert sel2 is None
        assert applied == [None]


class TestReferenceCounting:
    def test_torrent_kept_until_last_leaves(self):
        reg = SharedTorrentRegistry()
        reg.join("h", [0])
        reg.join("h", [1])

        remaining, _ = reg.leave("h", success=False)
        assert remaining == 1  # one sibling still active — caller must NOT remove

        remaining, _ = reg.leave("h", success=False)
        assert remaining == 0  # last one out — caller owns cleanup

    def test_any_success_true_if_any_sharer_succeeded(self):
        reg = SharedTorrentRegistry()
        reg.join("h", [0])
        reg.join("h", [1])

        # First job fails, second succeeds.
        remaining, any_success = reg.leave("h", success=False)
        assert remaining == 1
        assert any_success is False

        remaining, any_success = reg.leave("h", success=True)
        assert remaining == 0
        assert any_success is True  # torrent holds good data -> keep seeding

    def test_all_failed_reports_no_success(self):
        reg = SharedTorrentRegistry()
        reg.join("h", [0])
        remaining, any_success = reg.leave("h", success=False)
        assert remaining == 0
        assert any_success is False

    def test_leave_unknown_hash_is_safe(self):
        reg = SharedTorrentRegistry()
        remaining, any_success = reg.leave("never-joined", success=True)
        assert remaining == 0
        assert any_success is True

    def test_entry_recreated_after_fully_drained(self):
        reg = SharedTorrentRegistry()
        reg.join("h", [0])
        reg.leave("h", success=True)
        # A brand-new download of the same torrent starts a fresh entry.
        refcount, union = reg.join("h", [9])
        assert refcount == 1
        assert union == {9}


class TestThreadSafety:
    def test_concurrent_join_leave_balances(self):
        reg = SharedTorrentRegistry()
        start = threading.Event()
        results = []

        def worker(i):
            start.wait()
            reg.join("h", [i])
            reg.leave("h", success=(i % 2 == 0))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        start.set()
        for t in threads:
            t.join()

        # Everyone left -> entry fully drained; a fresh join starts at 1.
        refcount, _ = reg.join("h", [0])
        results.append(refcount)
        assert results == [1]
