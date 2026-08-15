"""Coordinates concurrent downloads that resolve to the SAME torrent.

Transmission dedupes an ``add`` by info-hash, so several jobs that each want a
different file from one album torrent all attach to a *single* torrent instance
in the daemon. flacfetch was originally built assuming every download owned its
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

* file selection is *merged* — the daemon is told to want the union of every
  active job's target files, via per-file reference counts, so a file stays
  wanted exactly while at least one active job wants it;
* every daemon mutation (apply selection, remove torrent) runs **under the
  registry lock**, atomic with the reference-count change, so concurrent joiners
  and leavers can't race each other or tear down a torrent someone still needs.

Residual: a brand-new download calls the daemon's ``add`` *before* it joins the
registry, so it can, in a very narrow window, attach to a torrent another job is
removing. The worst case is a clean, retriable "torrent disappeared" error for
the new job (handled by the monitor loop), never data corruption — and in the
server's keep-seeding mode a *successful* torrent is never removed at all.
"""
import threading
from typing import Callable, Iterable, Optional, Set, Tuple

from ..core.log import get_logger

logger = get_logger("TorrentCoordinator")


class _Entry:
    __slots__ = ("refcount", "file_refs", "wants_all", "any_success")

    def __init__(self) -> None:
        self.refcount = 0
        # file_id -> number of active jobs currently wanting that file. A file is
        # wanted exactly while its count is > 0, so it becomes unwanted again as
        # soon as the last job that wanted it leaves.
        self.file_refs: dict[int, int] = {}
        # Count of active jobs that want the WHOLE torrent (no isolated target).
        # While > 0 the selection is unrestricted (everything wanted), so a
        # selective sibling can't un-want the whole-torrent job's files.
        self.wants_all = 0
        self.any_success = False

    def selection(self) -> Optional[Set[int]]:
        """The file ids to want, or ``None`` meaning "want the whole torrent"."""
        if self.wants_all > 0:
            return None
        return {fid for fid, count in self.file_refs.items() if count > 0}

    def _add(self, file_ids: Iterable[int]) -> None:
        for fid in file_ids:
            fid = int(fid)
            self.file_refs[fid] = self.file_refs.get(fid, 0) + 1

    def _remove(self, file_ids: Iterable[int]) -> None:
        for fid in file_ids:
            fid = int(fid)
            if fid in self.file_refs:
                self.file_refs[fid] -= 1
                if self.file_refs[fid] <= 0:
                    del self.file_refs[fid]


class SharedTorrentRegistry:
    """Process-wide coordinator keyed by torrent info-hash.

    Thread-safe: all mutation, and every caller-supplied daemon callback
    (``apply_fn`` / ``remove_fn``), runs under a single lock so selection changes
    and torrent removal are serialized with the reference-count transitions they
    depend on.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}

    def join(
        self,
        info_hash: str,
        target_ids: Iterable[int],
        wants_all: bool = False,
        apply_fn: Optional[Callable[[Optional[Set[int]]], None]] = None,
    ) -> Tuple[int, Optional[Set[int]]]:
        """Register a job's interest in ``info_hash``.

        Bumps the reference count of each id in ``target_ids`` (and the
        whole-torrent counter when ``wants_all``) and, while still holding the
        lock, invokes ``apply_fn(selection)`` so the merged selection is pushed
        to the daemon atomically with the merge. ``selection`` is the set of
        wanted file ids, or ``None`` meaning "want the whole torrent".

        If ``apply_fn`` raises the join is fully rolled back before the exception
        propagates, so a failed selection RPC leaves no trace — neither a leaked
        reference nor stray wanted ids.

        Returns ``(refcount_after_join, selection)``.
        """
        target_ids = [int(i) for i in target_ids]
        with self._lock:
            entry = self._entries.get(info_hash)
            if entry is None:
                entry = _Entry()
                self._entries[info_hash] = entry
            entry.refcount += 1
            if wants_all:
                entry.wants_all += 1
            entry._add(target_ids)
            selection = entry.selection()
            if apply_fn is not None:
                try:
                    apply_fn(selection)
                except Exception:
                    entry.refcount -= 1
                    if wants_all:
                        entry.wants_all -= 1
                    entry._remove(target_ids)
                    if entry.refcount <= 0:
                        self._entries.pop(info_hash, None)
                    raise
            return entry.refcount, selection

    def leave(
        self,
        info_hash: str,
        target_ids: Iterable[int],
        success: bool,
        wants_all: bool = False,
        apply_fn: Optional[Callable[[Optional[Set[int]]], None]] = None,
        remove_fn: Optional[Callable[[bool], None]] = None,
    ) -> Tuple[int, bool]:
        """Mark a job done with this torrent.

        ``target_ids`` / ``wants_all`` must match what was passed to
        :meth:`join` so the per-file and whole-torrent reference counts are
        released. All daemon work happens under the lock:

        - when other jobs remain, ``apply_fn(selection)`` re-applies the reduced
          selection (files this job alone wanted are dropped), so the torrent
          stops downloading files nobody wants;
        - when this was the LAST job, ``remove_fn(any_success)`` performs cleanup
          (remove the torrent, or leave it seeding) atomically with discarding
          the entry, so a concurrent join can't attach to a torrent being torn
          down.

        Returns ``(remaining_refcount, any_success)``.
        """
        target_ids = [int(i) for i in target_ids]
        with self._lock:
            entry = self._entries.get(info_hash)
            if entry is None:
                if remove_fn is not None:
                    remove_fn(success)
                return 0, success
            if success:
                entry.any_success = True
            entry.refcount -= 1
            if wants_all and entry.wants_all > 0:
                entry.wants_all -= 1
            entry._remove(target_ids)
            remaining = entry.refcount
            any_success = entry.any_success
            if remaining <= 0:
                if remove_fn is not None:
                    remove_fn(any_success)
                self._entries.pop(info_hash, None)
            elif apply_fn is not None:
                # Re-applying the reduced selection is BEST-EFFORT: the leaving
                # job's own download is already finished, and worst case the
                # daemon keeps a few now-unwanted files. Never let a transient
                # change_torrent failure here propagate — leave() runs from the
                # caller's `finally`, so a raise would turn a completed download
                # into an error (and mask any original exception).
                try:
                    apply_fn(entry.selection())
                except Exception as e:
                    logger.warning(
                        f"Best-effort selection re-apply failed for {info_hash}: {e}"
                    )
            return max(remaining, 0), any_success


# One registry shared by every TorrentDownloader instance in the process (RED,
# OPS, ...). They all talk to the same transmission daemon, so coordination must
# be global rather than per-instance.
SHARED_TORRENT_REGISTRY = SharedTorrentRegistry()
