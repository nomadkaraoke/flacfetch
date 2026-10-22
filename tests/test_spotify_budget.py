"""Tests for the Spotify listening-budget ledger and batch gate."""
import sqlite3

import pytest

from flacfetch.api.services.spotify_budget import (
    BudgetConfig,
    SpotifyLedger,
    Usage,
    check_batch,
)

DAY = 86400


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def ledger(tmp_path, clock):
    return SpotifyLedger(str(tmp_path / "ledger.sqlite"), now=clock)


@pytest.fixture
def cfg():
    return BudgetConfig()


def _add_done(ledger, clock, download_id, priority, seconds, age=0):
    """Insert a settled entry started `age` seconds ago."""
    saved = clock.t
    clock.t = saved - age
    ledger.reserve(download_id, priority, seconds)
    ledger.settle(download_id, seconds)
    clock.t = saved


class TestCheckBatch:
    def test_batch_allowed_under_budget(self, cfg):
        assert check_batch(Usage(), 240, cfg, now=1000.0) == (True, None, 0)

    def test_batch_refused_over_budget(self, cfg):
        entries = ((500.0, 43000.0, "batch"),)
        usage = Usage(total_s=43000, batch_s=43000, entries=entries)
        ok, code, retry = check_batch(usage, 240, cfg, now=1000.0)
        assert (ok, code) == (False, "spotify_budget")
        # the 43000 s entry ages out at 500 + 86400
        assert retry == 500 + DAY - 1000

    def test_retry_after_waits_only_for_enough_entries(self, cfg):
        entries = ((100.0, 20000.0, "batch"), (200.0, 23000.0, "batch"))
        usage = Usage(total_s=43000, batch_s=43000, entries=entries)
        # excess is 240 - 200 = 40... first entry frees 20000 >= 240+43000-43200=40
        ok, code, retry = check_batch(usage, 240, cfg, now=1000.0)
        assert not ok and retry == 100 + DAY - 1000

    def test_hard_cap_counts_interactive(self, cfg):
        entries = ((100.0, 75000.0, "interactive"), (200.0, 4000.0, "batch"))
        usage = Usage(total_s=79000, batch_s=4000, entries=entries)
        ok, code, retry = check_batch(usage, 240, cfg, now=1000.0)  # 79240 > 79200
        assert (ok, code) == (False, "spotify_budget")
        assert retry == 100 + DAY - 1000

    def test_batch_busy_at_two_in_flight(self, cfg):
        assert check_batch(Usage(batch_in_flight=1), 240, cfg, 1000.0)[0] is True
        ok, code, retry = check_batch(Usage(batch_in_flight=2), 240, cfg, 1000.0)
        assert (ok, code) == (False, "batch_busy") and retry > 0

    def test_interactive_active_blocks_batch(self, cfg):
        ok, code, retry = check_batch(Usage(interactive_in_flight=1), 240, cfg, 1000.0)
        assert (ok, code) == (False, "interactive_active") and retry > 0

    def test_batch_gap_30s(self, cfg):
        usage = Usage(last_batch_start=990.0)
        assert check_batch(usage, 240, cfg, 1000.0) == (False, "batch_gap", 20)
        assert check_batch(usage, 240, cfg, 1020.0)[0] is True


class TestLedger:
    def test_window_boundary(self, ledger, clock):
        _add_done(ledger, clock, "a", "batch", 100, age=DAY)  # exactly at the edge: out
        _add_done(ledger, clock, "b", "batch", 200, age=DAY - 1)
        u = ledger.usage()
        assert u.total_s == 200 and u.batch_s == 200

    def test_ledger_survives_restart(self, tmp_path, clock):
        path = str(tmp_path / "l.sqlite")
        SpotifyLedger(path, now=clock).reserve("a", "batch", 240)
        again = SpotifyLedger(path, now=clock)
        u = again.usage()
        assert u.total_s == 240 and u.batch_in_flight == 1

    def test_failed_before_capture_releases(self, ledger):
        ledger.reserve("a", "batch", 240)
        ledger.release("a")
        u = ledger.usage()
        assert u.total_s == 0 and u.batch_in_flight == 0

    def test_captured_seconds_replace_reservation(self, ledger):
        ledger.reserve("a", "interactive", 300)
        assert ledger.usage().total_s == 300
        ledger.settle("a", 187.5)
        u = ledger.usage()
        assert u.total_s == 187.5 and u.interactive_in_flight == 0

    def test_settle_without_capture_keeps_reservation(self, ledger):
        ledger.reserve("a", "batch", 240)
        ledger.settle("a", None)
        assert ledger.usage().batch_s == 240

    def test_stale_reserved_settles_after_2h(self, ledger, clock):
        ledger.reserve("a", "batch", 240)
        clock.t += 2 * 3600 + 1
        u = ledger.usage()
        assert u.batch_in_flight == 0 and u.batch_s == 240
        with sqlite3.connect(ledger.path) as conn:
            state, captured = conn.execute("select state, captured_s from entries").fetchone()
        assert (state, captured) == ("done", 240)

    def test_in_flight_split_by_priority(self, ledger):
        ledger.reserve("a", "batch", 100)
        ledger.reserve("b", "interactive", 100)
        u = ledger.usage()
        assert (u.batch_in_flight, u.interactive_in_flight) == (1, 1)

    def test_try_reserve_batch_is_atomic_gate(self, ledger, clock, cfg):
        assert ledger.try_reserve_batch("a", 240, cfg)[0] is True
        ok, code, _ = ledger.try_reserve_batch("b", 240, cfg)
        assert (ok, code) == (False, "batch_gap")
        assert ledger.usage().batch_in_flight == 1  # refused one not recorded
        clock.t += 31
        assert ledger.try_reserve_batch("b", 240, cfg)[0] is True
        clock.t += 31
        assert ledger.try_reserve_batch("c", 240, cfg)[1] == "batch_busy"
