import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from ..core.interfaces import Downloader
from ..core.log import get_logger
from ..core.models import Release
from .torrent_coordinator import SHARED_TORRENT_REGISTRY

logger = get_logger("TorrentDownloader")

# Try import transmission_rpc
try:
    import transmission_rpc
except ImportError:
    transmission_rpc = None

class TorrentDownloader(Downloader):
    """
    Downloads torrents using Transmission daemon.
    Requires transmission-daemon to be running locally.
    """

    def __init__(self,
                 host: str = 'localhost',
                 port: int = 9091,
                 username: Optional[str] = None,
                 password: Optional[str] = None,
                 keep_seeding: bool = False):
        """
        Initialize Transmission RPC client.

        Args:
            host: Transmission daemon host (default: localhost)
            port: Transmission RPC port (default: 9091)
            username: Optional RPC username
            password: Optional RPC password
            keep_seeding: If True, keep torrents seeding after download completes.
                          Used in server mode for indefinite seeding.
        """
        if transmission_rpc is None:
            raise ImportError(
                "transmission-rpc not installed. Install with: pip install transmission-rpc"
            )

        # Use environment variables if not specified
        self.host = host or os.environ.get("TRANSMISSION_HOST", "localhost")
        self.port = port or int(os.environ.get("TRANSMISSION_PORT", "9091"))
        self.username = username
        self.password = password
        self.keep_seeding = keep_seeding
        self.client = None

        # Stall handling for the download monitor loop. A torrent that connects
        # to peers but transfers nothing (a stale/choking peer set from a private
        # tracker) used to spin the monitor loop forever, leaking a hung download
        # task on every caller retry until the API stopped responding. We now
        # force a fresh tracker announce after a short stall (to pull a new peer
        # list) and abort the download after a hard stall ceiling so the task
        # exits and the caller can retry cleanly.
        #
        # Values come from the environment but must be sane: a nan/inf/zero/
        # negative threshold could silently disable the safety net (nan never
        # compares true) or abort on the very first monitor tick (<= 0 ceiling).
        self.stall_reannounce_seconds = self._positive_float_env(
            "FLACFETCH_STALL_REANNOUNCE_SECONDS", 90.0
        )
        self.stall_reannounce_interval = self._positive_float_env(
            "FLACFETCH_STALL_REANNOUNCE_INTERVAL", 120.0
        )
        self.max_stall_seconds = self._positive_float_env(
            "FLACFETCH_MAX_STALL_SECONDS", 600.0
        )
        # The hard ceiling must sit strictly above the re-announce threshold, with
        # enough margin for a forced re-announce to actually pull fresh peers and
        # resume — otherwise the abort fires on the same tick as the re-announce
        # and it never gets a chance to help.
        min_ceiling = self.stall_reannounce_seconds + self.stall_reannounce_interval
        if self.max_stall_seconds < min_ceiling:
            logger.warning(
                f"FLACFETCH_MAX_STALL_SECONDS ({self.max_stall_seconds}) leaves no "
                f"room for a re-announce to take effect (threshold "
                f"{self.stall_reannounce_seconds} + interval "
                f"{self.stall_reannounce_interval}); raising it to {min_ceiling}"
            )
            self.max_stall_seconds = min_ceiling

        # Download directory for keep_seeding mode
        self._download_dir = os.environ.get(
            "FLACFETCH_DOWNLOAD_DIR",
            "/var/lib/transmission-daemon/downloads"
        )

    @staticmethod
    def _positive_float_env(name: str, default: float) -> float:
        """Read a strictly-positive, finite float from the environment.

        Falls back to ``default`` (and warns) for missing, unparseable, or
        non-finite/<= 0 values so a misconfiguration can't disable the stall
        safety net or make it abort immediately.
        """
        raw = os.environ.get(name)
        if raw is None:
            return default
        try:
            val = float(raw)
        except (TypeError, ValueError):
            logger.warning(f"Invalid {name}={raw!r}; using default {default}")
            return default
        if not math.isfinite(val) or val <= 0:
            logger.warning(f"Invalid {name}={raw!r} (must be > 0); using default {default}")
            return default
        return val

    def _ensure_daemon_running(self) -> bool:
        """Check if transmission-daemon is running, attempt to start if not."""
        try:
            # Try to connect
            self.client = transmission_rpc.Client(
                host=self.host,
                port=self.port,
                username=self.username,
                password=self.password
            )
            logger.debug(f"Connected to Transmission daemon at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.warning(f"Could not connect to Transmission daemon: {e}")
            logger.info("Attempting to start transmission-daemon...")

            try:
                # Try to start transmission-daemon
                subprocess.Popen(
                    ['transmission-daemon'],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                logger.info("Started transmission-daemon. Waiting for it to initialize...")
                time.sleep(2)

                # Try connecting again
                self.client = transmission_rpc.Client(
                    host=self.host,
                    port=self.port,
                    username=self.username,
                    password=self.password
                )
                logger.info("Successfully connected to Transmission daemon")
                return True
            except Exception as start_error:
                logger.error(f"Failed to start or connect to transmission-daemon: {start_error}")
                logger.error(
                    "Please ensure transmission-daemon is installed and running.\n"
                    "Install: brew install transmission-cli\n"
                    "Start: transmission-daemon"
                )
                return False

    def download(self, release: Release, output_path: str, output_filename: Optional[str] = None) -> str:
        """
        Download a torrent using Transmission.

        Args:
            release: Release object containing download_url (path to .torrent file)
            output_path: Directory to save downloaded files
            output_filename: Optional specific filename for the output file

        Returns:
            Path to the downloaded file
        """
        if not release.download_url:
            raise ValueError("Release has no download URL/Path")

        # Ensure daemon is running
        if not self._ensure_daemon_running():
            raise RuntimeError("Cannot connect to Transmission daemon")

        # Determine download directory
        # In keep_seeding mode, use persistent download dir
        # Otherwise use temp dir (will be cleaned up)
        if self.keep_seeding:
            download_dir = self._download_dir
            use_temp = False
            logger.debug(f"Using persistent download directory: {download_dir}")
        else:
            download_dir = tempfile.mkdtemp(prefix="flacfetch_torrent_")
            use_temp = True
            logger.debug(f"Using temporary download directory: {download_dir}")

        # Prepare final output path
        abs_output_path = os.path.abspath(output_path)
        os.makedirs(abs_output_path, exist_ok=True)
        os.makedirs(download_dir, exist_ok=True)

        # Check if it's a local torrent file
        if not os.path.exists(release.download_url):
            raise ValueError(
                f"TorrentDownloader requires a local .torrent file path. "
                f"Got: {release.download_url}"
            )

        if not os.path.isfile(release.download_url):
            raise ValueError(f"Not a file: {release.download_url}")

        logger.info(f"Adding torrent to Transmission: {release.download_url}")

        target_file_path = None
        downloaded_file_path = None
        torrent = None
        download_succeeded = False
        # Shared-torrent coordination state (see torrent_coordinator.py). Set once
        # the torrent is added and we know its info-hash; used to merge file
        # selection with concurrent downloads of the same torrent and to
        # reference-count cleanup so we never delete a torrent a sibling is using.
        info_hash = None
        target_ids = []  # file ids THIS job wants (empty => whole torrent)
        joined = False   # True once we've registered with the shared registry
        wants_all = False  # True when this job wants the whole torrent

        try:
            # Add torrent to Transmission in PAUSED state first
            # This prevents it from starting download before we set file priorities
            # Read the .torrent and pass the raw bytes so transmission-rpc sends it
            # as base64 `metainfo`. Passing the file *path* string makes the library
            # send it as `filename` for the daemon to read locally, which transmission
            # 4.x rejects with "unrecognized info" (it worked on 3.x). Bytes/metainfo
            # is accepted by both.
            with open(release.download_url, "rb") as _tf:
                _torrent_metainfo = _tf.read()
            torrent = self.client.add_torrent(
                _torrent_metainfo,
                download_dir=download_dir,
                paused=True  # Critical: add in paused state
            )

            logger.info(f"Torrent added (paused): {torrent.name} (ID: {torrent.id})")

            # Re-fetch once to reliably read the info-hash. It's how we coordinate
            # with any concurrent download of the SAME torrent (Transmission
            # dedupes an add by info-hash, so several tracks from one album share
            # a single torrent instance).
            torrent = self.client.get_torrent(torrent.id)
            info_hash = getattr(torrent, "hashString", None) or getattr(torrent, "info_hash", None)

            # Work out which file(s) THIS job wants and the full file list. We
            # fetch the file list even for a whole-torrent download so we can
            # re-assert "want everything" if a selective sibling tries to
            # restrict the shared torrent. get_files() can be a bare Mock in unit
            # tests / empty before metadata parses, so guard the iteration.
            all_ids = []
            if release.target_file:
                logger.info(f"Target file specified: {release.target_file}")

                # Wait for metadata to be parsed (files list is empty until parsed)
                logger.debug("Waiting for torrent metadata to be parsed...")
                max_wait = 10  # seconds
                waited = 0
                all_files = torrent.get_files()
                while len(all_files) == 0 and waited < max_wait:
                    time.sleep(0.5)
                    waited += 0.5
                    torrent = self.client.get_torrent(torrent.id)
                    all_files = torrent.get_files()

                if len(all_files) == 0:
                    logger.warning("Timeout waiting for torrent metadata. Downloading entire torrent.")
                else:
                    logger.debug(f"Metadata parsed: {len(all_files)} files found")
                    all_ids = [f.id for f in all_files]
                    for file_obj in all_files:
                        if release.target_file in file_obj.name:
                            logger.info(f"Found target file: {file_obj.name} (id {file_obj.id})")
                            target_ids.append(file_obj.id)
                    if not target_ids:
                        logger.warning(
                            f"Target file '{release.target_file}' not found in torrent. "
                            f"Downloading entire torrent."
                        )
            else:
                try:
                    all_ids = [f.id for f in torrent.get_files()]
                except Exception:  # unparsed metadata / mock get_files
                    all_ids = []

            # A job with no isolated target file wants the WHOLE torrent.
            wants_all = not target_ids

            # Apply the file selection MERGED with any sibling downloads of this
            # torrent: the daemon is told to want the union of every active job's
            # files (or EVERYTHING when any sibling wants the whole torrent).
            # Done under the registry lock so concurrent joiners can't overwrite
            # each other and starve all-but-one of their target file. ``selection
            # is None`` means "want everything" (no restriction).
            def _apply_selection(selection):
                wanted = list(all_ids) if selection is None else list(selection)
                wanted_set = set(wanted)
                unwanted = [i for i in all_ids if i not in wanted_set]
                logger.info(
                    f"Applying merged file selection for {info_hash}: "
                    f"want {len(wanted)}, unwant {len(unwanted)}"
                )
                self.client.change_torrent(
                    torrent.id,
                    files_wanted=wanted,
                    files_unwanted=unwanted,
                )

            # Only meaningful once we know the full file list.
            apply = _apply_selection if all_ids else None
            if info_hash:
                # Always register (even for a whole-torrent download) so the
                # reference count protects the torrent from a sibling's cleanup.
                refcount, _sel = SHARED_TORRENT_REGISTRY.join(
                    info_hash,
                    target_ids,
                    wants_all=wants_all,
                    apply_fn=apply,
                )
                joined = True
                logger.info(
                    f"Joined shared torrent {info_hash} "
                    f"(active downloads sharing it: {refcount})"
                )
            elif apply:
                # No info-hash to coordinate on (shouldn't happen post-add) —
                # fall back to a direct selection.
                _apply_selection(None if wants_all else set(target_ids))

            # Now start the torrent (idempotent if a sibling already started it).
            logger.info("Starting torrent download...")
            self.client.start_torrent(torrent.id)

            # Monitor download progress
            print(f"Downloading {release.title}...")

            last_progress = -1.0
            stall_counter = 0
            max_stalls = 60  # 60 seconds of no progress before the on-screen warning
            last_progress_time = time.monotonic()
            last_reannounce_time = None  # None => first re-announce allowed as soon as stalled
            missing_polls = 0  # consecutive polls where the torrent had vanished

            while True:
                # Refresh torrent status. A shared torrent should never disappear
                # while we hold a reference-count on it, but tolerate a transient
                # miss (e.g. an uncoordinated external removal / daemon restart)
                # rather than dying with a cryptic KeyError from transmission-rpc.
                try:
                    torrent = self.client.get_torrent(torrent.id)
                    missing_polls = 0
                except KeyError:
                    missing_polls += 1
                    if missing_polls > 5:
                        raise RuntimeError(
                            "Torrent disappeared from Transmission mid-download "
                            "(removed by another process?)"
                        )
                    time.sleep(1)
                    continue

                status = torrent.status
                progress = torrent.progress

                # Complete when THIS job's target file(s) are fully downloaded AND
                # present on disk — independent of sibling downloads sharing the
                # torrent, whose files may still be in flight. Requiring the file
                # on disk (not merely byte-complete) avoids declaring done during
                # the brief window before transmission renames 'X.part' -> 'X'.
                # Fall back to whole-torrent completion (seeding / 100%) when we
                # have no specific target file.
                located = self._locate_completed_target(
                    torrent, download_dir, release, target_ids
                )
                if located or status in ['seeding', 'seed_pending'] or progress >= 100.0:
                    print("\n✓ Download complete")
                    logger.info(f"Torrent finished: {torrent.name}")

                    # Entered via whole-torrent completion before our file settled
                    # on disk? Retry briefly for the rename/flush rather than
                    # returning an empty path (which would break the caller's
                    # upload). Normal case: already located, no delay.
                    if located is None and release.target_file:
                        for _attempt in range(20):
                            located = self._locate_completed_target(
                                self.client.get_torrent(torrent.id),
                                download_dir, release, target_ids,
                            )
                            if located:
                                break
                            time.sleep(0.5)

                    source_path, logical_name = located if located else (None, None)

                    if source_path and source_path.exists():
                        # Determine output filename from the logical (final) name,
                        # so a '.part' source still yields the right extension.
                        if output_filename:
                            original_ext = Path(logical_name).suffix
                            if not output_filename.endswith(original_ext):
                                final_filename = output_filename + original_ext
                            else:
                                final_filename = output_filename
                        else:
                            final_filename = logical_name

                        target_file_path = Path(abs_output_path) / final_filename

                        # Single-file torrents land directly in the output dir, so
                        # source and target can be the same file (the torrent's own
                        # download). Nothing to copy/move — it's already in place and
                        # transmission keeps seeding it.
                        same_file = (
                            target_file_path.exists()
                            and source_path.resolve() == target_file_path.resolve()
                        )

                        if same_file:
                            logger.info(
                                f"Downloaded file already at target path, "
                                f"skipping copy: {target_file_path}"
                            )
                            downloaded_file_path = str(source_path)
                        elif self.keep_seeding:
                            # Copy CONTENT ONLY (copyfile, not copy2): copy2 runs
                            # copystat(), which utime()/chmod()s the destination. When
                            # the download is owned by the transmission daemon and we
                            # run as a non-root, non-owner service user, that fails with
                            # EPERM ("Operation not permitted"). copyfile touches no
                            # ownership-restricted metadata, so the copy succeeds and the
                            # torrent keeps seeding.
                            logger.info(f"Copying file to: {target_file_path}")
                            shutil.copyfile(str(source_path), str(target_file_path))
                            downloaded_file_path = str(target_file_path)
                            logger.info("File copied successfully. Torrent continues seeding.")
                        else:
                            # Move file (original behavior)
                            logger.info(f"Moving file to: {target_file_path}")
                            shutil.move(str(source_path), str(target_file_path))
                            downloaded_file_path = str(target_file_path)
                            logger.info("File moved successfully")
                    else:
                        logger.warning("Could not find downloaded file")

                    download_succeeded = True
                    break

                # Check for errors
                if status == 'stopped' and progress < 100:
                    error_msg = torrent.error_string if hasattr(torrent, 'error_string') else "Unknown error"
                    raise RuntimeError(f"Torrent download stopped with error: {error_msg}")

                # Display progress
                download_rate = getattr(torrent, 'rate_download', 0) / 1000  # KB/s
                upload_rate = getattr(torrent, 'rate_upload', 0) / 1000  # KB/s
                peers = getattr(torrent, 'peers_connected', 0)

                # Detect stalls by wall-clock time since the last real transfer
                # activity (robust even if a monitor iteration takes longer than
                # the 1s tick). A live download counts as active even when its
                # percentage barely moves this tick — reset on *either* a progress
                # bump or a non-zero download rate so a large file trickling in
                # slowly isn't mistaken for a stall and aborted.
                now = time.monotonic()
                if progress > last_progress or download_rate > 0:
                    stall_counter = 0
                    last_progress = max(progress, last_progress)
                    last_progress_time = now
                else:
                    stall_counter += 1

                stalled_for = now - last_progress_time

                stall_warning = ""
                if stall_counter > max_stalls:
                    stall_warning = " [STALLED - no progress]"

                # While stalled, periodically force a fresh tracker announce. The
                # usual cause is a stale/choking peer set from a private tracker;
                # a re-announce pulls a new peer list (DHT/PEX are off for private
                # torrents, so the tracker is the only source of fresh peers).
                if stalled_for >= self.stall_reannounce_seconds and (
                    last_reannounce_time is None
                    or (now - last_reannounce_time) >= self.stall_reannounce_interval
                ):
                    last_reannounce_time = now
                    try:
                        self.client.reannounce_torrent(torrent.id)
                        logger.warning(
                            f"Download stalled {int(stalled_for)}s (peers: {peers}) — "
                            f"forcing tracker re-announce for '{torrent.name}'"
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"Re-announce failed: {e}")

                # Hard ceiling: if nothing transfers for this long, abort so the
                # download task exits instead of looping forever. The unbounded
                # loop previously leaked a hung background task on every caller
                # retry until the API stopped responding.
                if stalled_for >= self.max_stall_seconds:
                    raise RuntimeError(
                        f"Torrent download stalled for {int(stalled_for)}s with no "
                        f"progress at {progress:.2f}% (peers: {peers}); aborting"
                    )

                print(
                    f'\r{progress:.2f}% complete '
                    f'(down: {download_rate:.1f} KB/s up: {upload_rate:.1f} KB/s '
                    f'peers: {peers}) {status}{stall_warning}',
                    end=''
                )
                sys.stdout.flush()

                # Log verbose info
                if logger.isEnabledFor(10):  # DEBUG level
                    logger.debug(
                        f"Torrent {torrent.id}: {progress:.2f}% - "
                        f"D:{download_rate:.1f}KB/s U:{upload_rate:.1f}KB/s - "
                        f"Peers:{peers} - Status:{status}"
                    )

                time.sleep(1)

        except transmission_rpc.error.TransmissionError as e:
            logger.error(f"Transmission error: {e}")
            raise RuntimeError(f"Transmission error: {e}")
        except Exception as e:
            logger.error(f"Download failed: {e}")
            raise
        finally:
            # Final cleanup for the LAST job sharing this torrent: keep it seeding
            # when it holds good data (any sharer succeeded) in keep_seeding mode,
            # otherwise remove it and its data — on total failure (incl. a
            # hard-stall abort) the next retry must re-add fresh rather than
            # re-attach to the same stuck state (transmission dedupes by hash).
            # Runs UNDER the registry lock (see below) so a concurrent join can't
            # attach to a torrent being torn down.
            def _cleanup_last(any_success):
                if self.keep_seeding and any_success:
                    if torrent:
                        logger.info(f"Torrent {torrent.id} ({torrent.name}) will continue seeding")
                    return
                try:
                    if torrent:
                        logger.debug(f"Removing torrent {torrent.id} from Transmission")
                        self.client.remove_torrent(torrent.id, delete_data=True)
                except Exception as e:
                    logger.warning(f"Failed to remove torrent: {e}")
                try:
                    if use_temp and os.path.exists(download_dir):
                        logger.debug(f"Cleaning up temporary directory: {download_dir}")
                        shutil.rmtree(download_dir, ignore_errors=True)
                except Exception as e:
                    logger.warning(f"Failed to clean up temp directory: {e}")

            # Release our hold on the shared torrent. leave() runs the selection
            # re-apply (when siblings remain) and the removal (when we're last)
            # under the registry lock, atomic with the reference-count change.
            if joined and info_hash:
                remaining, _any = SHARED_TORRENT_REGISTRY.leave(
                    info_hash,
                    target_ids,
                    download_succeeded,
                    wants_all=wants_all,
                    apply_fn=_apply_selection if torrent is not None else None,
                    remove_fn=_cleanup_last,
                )
                if remaining > 0:
                    logger.info(
                        f"Torrent {info_hash} still in use by {remaining} other "
                        f"download(s); leaving it in place"
                    )
                    # In non-keep_seeding mode a duplicate add deduped onto a
                    # sibling's directory, leaving OUR private temp dir empty.
                    # Clean it only if empty — never touch live data a sibling
                    # is using.
                    try:
                        if (
                            use_temp
                            and os.path.isdir(download_dir)
                            and not os.listdir(download_dir)
                        ):
                            logger.debug(f"Cleaning up empty temp directory: {download_dir}")
                            shutil.rmtree(download_dir, ignore_errors=True)
                    except Exception as e:
                        logger.warning(f"Failed to clean up temp directory: {e}")
            else:
                # Never joined the registry (e.g. no info-hash) — clean up directly.
                _cleanup_last(download_succeeded)

        return downloaded_file_path or ""

    @staticmethod
    def _locate_completed_target(torrent, download_dir, release, target_ids):
        """Locate this job's completed target file on disk.

        Returns ``(source_path, logical_name)`` when every file in
        ``target_ids`` is byte-complete AND the target file is present on disk,
        otherwise ``None``. Transmission names an incomplete file ``X.part`` and
        renames it to ``X`` on completion, and that rename can lag the byte
        count, so accept whichever of the two names exists — the content is
        complete once ``bytesCompleted == size``. ``logical_name`` is always the
        final name (no ``.part``) so the caller derives the right extension.

        Returns ``None`` when there is no target file (caller falls back to
        whole-torrent completion), the bytes aren't all present, or the file
        isn't on disk yet.
        """
        if not target_ids or not release.target_file:
            return None
        try:
            files = torrent.get_files()
        except Exception:
            return None
        if not files:
            return None
        by_id = {f.id: f for f in files}
        for fid in target_ids:
            f = by_id.get(fid)
            if f is None or f.size <= 0 or f.completed < f.size:
                return None
        td = getattr(torrent, "download_dir", None)
        effective_dir = td if isinstance(td, str) and td else download_dir
        for f in files:
            if f.id in target_ids and release.target_file in f.name:
                final = Path(effective_dir) / f.name
                if final.exists():
                    return final, f.name
                part = Path(effective_dir) / (f.name + ".part")
                if part.exists():
                    return part, f.name
        return None
