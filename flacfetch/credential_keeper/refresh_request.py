"""Signal from the flacfetch API to the credential keeper: "these cookies just failed".

The API and the keeper are separate systemd services with the same user, so a file
next to the keeper's status file is enough. The API writes it when YouTube bot-blocks
a real search or download, the keeper notices within a couple of seconds and
re-exports, and the API watches the cookie file's mtime to know when to retry.

Standard library only: this is imported by the downloader and provider modules.
"""
import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

REQUEST_FILENAME = "youtube-refresh-request"
_DEFAULT_STATUS_FILE = "/mnt/flacfetch-data/browser-profiles/keeper-status.json"

# How long a bot-blocked search or download waits for new cookies. A relaunch and
# re-export takes about 12s on flacup, plus however long the keeper is busy.
WAIT_SECONDS = float(os.environ.get("FLACFETCH_COOKIE_REFRESH_WAIT", 30))
# At most one on-demand re-export per this many seconds, however many requests
# fail. Enforced on both sides: here so callers don't wait for a refresh that
# isn't coming, and in the keeper because it is the one doing the work.
COOLDOWN_SECONDS = float(os.environ.get("KEEPER_YOUTUBE_ON_DEMAND_COOLDOWN", 300))

_lock = threading.Lock()
_last_request_at: Optional[float] = None
_wait_until = 0.0


def _request_path() -> str:
    status_file = os.environ.get("KEEPER_STATUS_FILE", _DEFAULT_STATUS_FILE)
    return os.path.join(os.path.dirname(status_file), REQUEST_FILENAME)


def cookies_mtime(cookies_file: Optional[str]) -> int:
    """The cookie file's mtime in ns, or 0 when there isn't one."""
    if not cookies_file:
        return 0
    try:
        return os.stat(cookies_file).st_mtime_ns
    except OSError:
        return 0


def request_refresh(wait_seconds: Optional[float] = None) -> bool:
    """Ask the keeper for fresh cookies. Returns True if a new request was written.

    Never raises. False means the request was rate limited or couldn't be written
    (for example on a dev machine with no keeper).
    """
    global _last_request_at, _wait_until
    with _lock:
        now = time.monotonic()
        if _last_request_at is not None and now - _last_request_at < COOLDOWN_SECONDS:
            return False
        try:
            with open(_request_path(), "w") as f:
                f.write(f"{time.time()}\n")
        except OSError as e:
            logger.info(f"Could not request a cookie refresh: {e}")
            _wait_until = 0.0
            return False
        _last_request_at = now
        _wait_until = now + (WAIT_SECONDS if wait_seconds is None else wait_seconds)
        return True


def wait_for_fresh_cookies(
    cookies_file: Optional[str],
    baseline_mtime: int,
    timeout: Optional[float] = None,
    poll: float = 0.5,
) -> bool:
    """Block until the cookie file is newer than ``baseline_mtime``, requesting a
    refresh if nobody has. Returns False if no new cookies arrive in time.

    Take the baseline before the attempt that failed, so cookies replaced during
    that attempt count. A caller that arrives while another caller's request is in
    flight shares its deadline; one that arrives later in the cooldown returns at
    once rather than waiting for a refresh that won't happen.

    Blocks the calling thread: run it off the event loop, since the keeper delivers
    new cookies through the API.
    """
    if not cookies_file:
        return False
    if cookies_mtime(cookies_file) > baseline_mtime:
        return True

    request_refresh(timeout)
    while time.monotonic() < _wait_until:
        if cookies_mtime(cookies_file) > baseline_mtime:
            return True
        time.sleep(poll)
    return cookies_mtime(cookies_file) > baseline_mtime


def refresh_requested() -> bool:
    """Whether a request is waiting. For the keeper's idle loop."""
    return os.path.exists(_request_path())


def consume_refresh_request() -> bool:
    """Remove a waiting request. Returns True if there was one. Never raises."""
    try:
        os.unlink(_request_path())
        return True
    except OSError:
        return False
