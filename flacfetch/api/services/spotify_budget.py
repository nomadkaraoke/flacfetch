"""
Spotify listening budget for the flacfetch HTTP API.

Spotify Premium accounts must not "listen" to more than 24 h of audio per
rolling 24 h. Every Spotify download made through the API is recorded in a
small SQLite ledger (survives restarts). Batch (background, bulk) downloads
are gated against the ledger; interactive downloads are never refused, only
counted.
"""
import logging
import math
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator, Optional, Tuple

logger = logging.getLogger(__name__)

# Reserved entries older than this are assumed to have crashed mid-flight and
# are counted at their reserved length.
STALE_RESERVED_S = 2 * 3600

# Capture format used by SpotifyDownloader: 44.1 kHz, 16-bit, stereo PCM.
PCM_BYTES_PER_SECOND = 44100 * 4

# Reservation used for an interactive download that gave no expected duration.
DEFAULT_INTERACTIVE_RESERVE_S = 300

# Typical track length used by GET /spotify/budget to answer "can a batch start?".
PROBE_BATCH_SECONDS = 240

CODE_BUDGET = "spotify_budget"
CODE_BATCH_BUSY = "batch_busy"
CODE_INTERACTIVE_ACTIVE = "interactive_active"
CODE_BATCH_GAP = "batch_gap"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(f"Ignoring non-integer {name}={raw!r}; using {default}")
        return default


@dataclass
class BudgetConfig:
    batch_budget_s: int = 43200
    hard_cap_s: int = 86400
    interactive_reserve_s: int = 7200
    max_batch_in_flight: int = 2
    min_batch_gap_s: int = 30
    window_s: int = 86400

    @classmethod
    def from_env(cls) -> "BudgetConfig":
        d = cls()
        return cls(
            batch_budget_s=_env_int("FLACFETCH_SPOTIFY_BATCH_BUDGET_S", d.batch_budget_s),
            hard_cap_s=_env_int("FLACFETCH_SPOTIFY_HARD_CAP_S", d.hard_cap_s),
            interactive_reserve_s=_env_int("FLACFETCH_SPOTIFY_INTERACTIVE_RESERVE_S", d.interactive_reserve_s),
            max_batch_in_flight=_env_int("FLACFETCH_SPOTIFY_MAX_BATCH_IN_FLIGHT", d.max_batch_in_flight),
            min_batch_gap_s=_env_int("FLACFETCH_SPOTIFY_MIN_BATCH_GAP_S", d.min_batch_gap_s),
            window_s=_env_int("FLACFETCH_SPOTIFY_WINDOW_S", d.window_s),
        )


@dataclass
class Usage:
    total_s: float = 0.0
    batch_s: float = 0.0
    batch_in_flight: int = 0
    interactive_in_flight: int = 0
    last_batch_start: Optional[float] = None
    # (started_at, counted_seconds, priority) of every counted entry; used to
    # work out when enough usage ages out of the window.
    entries: Tuple[Tuple[float, float, str], ...] = ()


def _age_out_delay(
    entries: Tuple[Tuple[float, float, str], ...],
    excess: float,
    only_batch: bool,
    cfg: BudgetConfig,
    now: float,
) -> float:
    """Seconds until entries aging out of the window free up `excess` seconds."""
    freed = 0.0
    for started_at, seconds, priority in sorted(entries):
        if only_batch and priority != "batch":
            continue
        freed += seconds
        if freed >= excess:
            return max(0.0, started_at + cfg.window_s - now)
    return float(cfg.window_s)


def check_batch(
    usage: Usage, seconds: float, cfg: BudgetConfig, now: float
) -> Tuple[bool, Optional[str], int]:
    """Decide whether a batch download of `seconds` may start.

    Returns (ok, code, retry_after_s).
    """
    batch_excess = usage.batch_s + seconds - cfg.batch_budget_s
    soft_cap = cfg.hard_cap_s - cfg.interactive_reserve_s
    total_excess = usage.total_s + seconds - soft_cap
    if batch_excess > 0 or total_excess > 0:
        delay = 0.0
        if batch_excess > 0:
            delay = max(delay, _age_out_delay(usage.entries, batch_excess, True, cfg, now))
        if total_excess > 0:
            delay = max(delay, _age_out_delay(usage.entries, total_excess, False, cfg, now))
        return False, CODE_BUDGET, max(1, math.ceil(delay))

    if usage.batch_in_flight >= cfg.max_batch_in_flight:
        return False, CODE_BATCH_BUSY, cfg.min_batch_gap_s

    if usage.interactive_in_flight > 0:
        return False, CODE_INTERACTIVE_ACTIVE, 60

    if usage.last_batch_start is not None:
        since = now - usage.last_batch_start
        if since < cfg.min_batch_gap_s:
            return False, CODE_BATCH_GAP, max(1, math.ceil(cfg.min_batch_gap_s - since))

    return True, None, 0


class SpotifyLedger:
    """SQLite ledger of Spotify listening time (one connection per call)."""

    def __init__(self, path: str, now: Callable[[], float] = time.time):
        self.path = path
        self._now = now
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        with self._tx() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS entries ("
                "download_id TEXT PRIMARY KEY, priority TEXT NOT NULL, "
                "started_at REAL NOT NULL, reserved_s REAL NOT NULL, "
                "captured_s REAL NULL, state TEXT NOT NULL)"
            )

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")
        finally:
            conn.close()

    def _insert(self, conn: sqlite3.Connection, download_id: str, priority: str, seconds: float) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO entries(download_id, priority, started_at, reserved_s, captured_s, state) "
            "VALUES (?, ?, ?, ?, NULL, 'reserved')",
            (download_id, priority, self._now(), float(seconds)),
        )

    def reserve(self, download_id: str, priority: str, seconds: float) -> None:
        with self._tx() as conn:
            self._insert(conn, download_id, priority, seconds)

    def try_reserve_batch(
        self, download_id: str, seconds: float, cfg: BudgetConfig
    ) -> Tuple[bool, Optional[str], int]:
        """Atomically check the batch gate and, if open, reserve."""
        with self._tx() as conn:
            now = self._now()
            usage = self._usage(conn, now, cfg.window_s)
            ok, code, retry = check_batch(usage, seconds, cfg, now)
            if ok:
                self._insert(conn, download_id, "batch", seconds)
            return ok, code, retry

    def settle(self, download_id: str, captured_s: Optional[float]) -> None:
        """Record the captured length (None keeps the reservation as the count)."""
        with self._tx() as conn:
            conn.execute(
                "UPDATE entries SET state='done', captured_s=COALESCE(?, reserved_s) WHERE download_id=?",
                (None if captured_s is None else float(captured_s), download_id),
            )

    def release(self, download_id: str) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE entries SET state='released' WHERE download_id=?", (download_id,))

    def usage(self, now: Optional[float] = None, window_s: int = 86400) -> Usage:
        with self._tx() as conn:
            return self._usage(conn, self._now() if now is None else now, window_s)

    def _usage(self, conn: sqlite3.Connection, now: float, window_s: int) -> Usage:
        # Crash safety: a reservation that never settled counts at its reserved size.
        conn.execute(
            "UPDATE entries SET state='done', captured_s=reserved_s "
            "WHERE state='reserved' AND started_at < ?",
            (now - STALE_RESERVED_S,),
        )
        rows = conn.execute(
            "SELECT priority, started_at, reserved_s, captured_s, state FROM entries "
            "WHERE state != 'released' AND started_at > ?",
            (now - window_s,),
        ).fetchall()
        usage = Usage()
        entries = []
        for priority, started_at, reserved_s, captured_s, state in rows:
            counted = captured_s if (state == "done" and captured_s is not None) else reserved_s
            usage.total_s += counted
            entries.append((started_at, counted, priority))
            if priority == "batch":
                usage.batch_s += counted
            if state == "reserved":
                if priority == "batch":
                    usage.batch_in_flight += 1
                else:
                    usage.interactive_in_flight += 1
        last = conn.execute(
            "SELECT MAX(started_at) FROM entries WHERE priority='batch' AND state != 'released'"
        ).fetchone()[0]
        usage.last_batch_start = last
        usage.entries = tuple(entries)
        return usage


_ledger: Optional[SpotifyLedger] = None
_ledger_lock = threading.Lock()


def default_ledger_path() -> str:
    return os.environ.get("FLACFETCH_SPOTIFY_LEDGER") or os.path.join(
        os.path.expanduser("~"), ".cache", "flacfetch", "spotify_ledger.sqlite"
    )


def get_spotify_ledger() -> SpotifyLedger:
    global _ledger
    with _ledger_lock:
        if _ledger is None:
            _ledger = SpotifyLedger(default_ledger_path())
        return _ledger


def reset_spotify_ledger() -> None:
    """Drop the cached singleton (tests / config changes)."""
    global _ledger
    with _ledger_lock:
        _ledger = None
