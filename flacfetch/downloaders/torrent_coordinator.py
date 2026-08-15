"""Coordinates concurrent downloads that resolve to the SAME torrent.

Transmission dedupes an ``add`` by info-hash, so several jobs that each want a
different file from one album torrent all attach to a *single* torrent instance
in the daemon. flacfetch was originally built assuming every download owns its
torrent exclusively, which breaks badly when e.g. a bulk batch requests several
tracks from the same album at once:

* Each job independently sets file priorities
  (``files_wanted=[myTrack], files_unwanted=[everything else]``). The calls
  race and the last writer wins, so every job except one has its target file
  marked *unwanted* and never downloads — it stalls at 0% and eventually aborts.
* When any one job finishes (or aborts) it calls
  ``remove_torrent(delete_data=True)`` in its cleanup, yanking the shared
  torrent and its data out from under the siblings still using it. Their next
  status poll raises ``KeyError("Torrent not found in result")``.

This registry makes the shared-torrent case correct by coordinating on the
info-hash across all downloader instances in the process:

* file selection is *merged* — the daemon is told to want the UNION of every
  active job's target files, and the merge is applied under a lock so concurrent
  joiners can't clobber each other;
* the torrent is *reference-counted* — cleanup only removes it once the LAST
  active job for that info-hash has finished, so no job ever deletes a torrent a
  sibling still needs.
"""
import threading
from typing import Callable, Iterable, Optional, Set, Tuple


class _Entry:
    __slots__ = ("refcount", "wanted", "any_success")

    def __init__(self) -> None:
        self.refcount = 0
        # Union of file ids wanted across all active jobs for this torrent. Only
        # ever grows for the life of the entry (the entry is discarded once the
        # last job leaves), so a file that becomes wanted stays wanted and can't
        # be un-wanted by a later joiner while a sibling still needs it.
        self.wanted: Set[int] = set()
        self.any_success = False


class SharedTorrentRegistry:
    """Process-wide coordinator keyed by torrent info-hash.

    Thread-safe: all mutation happens under a single lock. The lock is also held
    across the caller-supplied ``apply_fn`` in :meth:`join` so the transmission
    file-selection change is serialized with the in-memory merge (otherwise two
    joiners could compute a union, then apply in the reverse order and reinstate
    a stale, narrower selection).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}

    def join(
        self,
        info_hash: str,
        wanted_ids: Iterable[int],
        apply_fn: Optional[Callable[[Set[int]], None]] = None,
    ) -> Tuple[int, Set[int]]:
        """Register a job's interest in ``info_hash``.

        Adds ``wanted_ids`` to the shared wanted-set and, while still holding the
        lock, invokes ``apply_fn(union)`` (if given) so the merged selection is
        pushed to the daemon atomically with the merge.

        Returns ``(refcount_after_join, union_of_wanted_ids)``.
        """
        with self._lock:
            entry = self._entries.get(info_hash)
            if entry is None:
                entry = _Entry()
                self._entries[info_hash] = entry
            entry.refcount += 1
            entry.wanted.update(int(i) for i in wanted_ids)
            union = set(entry.wanted)
            if apply_fn is not None:
                apply_fn(union)
            return entry.refcount, union

    def leave(self, info_hash: str, success: bool) -> Tuple[int, bool]:
        """Mark a job done with this torrent.

        Returns ``(remaining_refcount, any_success)`` where ``any_success`` is
        True if *any* job that shared this torrent succeeded. When the returned
        refcount is 0 the entry has been discarded and the caller owns cleanup
        (remove the torrent, or leave it seeding if it succeeded).
        """
        with self._lock:
            entry = self._entries.get(info_hash)
            if entry is None:
                return 0, success
            if success:
                entry.any_success = True
            entry.refcount -= 1
            remaining = entry.refcount
            any_success = entry.any_success
            if remaining <= 0:
                self._entries.pop(info_hash, None)
            return max(remaining, 0), any_success


# One registry shared by every TorrentDownloader instance in the process (RED,
# OPS, ...). They all talk to the same transmission daemon, so coordination must
# be global rather than per-instance.
SHARED_TORRENT_REGISTRY = SharedTorrentRegistry()
