"""librespot credential keeper.

Mints reusable Spotify credentials for librespot by driving its *native* OAuth
flow (``librespot --enable-oauth``) through the persistent, Google-logged-in
browser. The resulting ``credentials.json`` is what the Spotify downloader hands
to librespot via ``-c``.

Why this exists: Spotify only accepts its own librespot OAuth client
(``65b708073fc0480ea92a077233ca87bd``) for Spotify Connect (spirc) device login.
A third-party access token (our ``SPOTIPY_CLIENT_ID``) authenticates the AP
session but is rejected at spirc with ``INVALID_CREDENTIALS`` -- so the token
path cannot register the capture device. librespot's own OAuth produces stored
credentials that spirc accepts, and those credentials are long-lived (no ~1h
access-token expiry), so this only needs to run occasionally / on first setup.
"""
import asyncio
import logging
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from ..core.config import get_librespot_credentials_dir

logger = logging.getLogger(__name__)

LIBRESPOT_DEVICE_NAME = "flacfetch-capture"
OAUTH_PORT = int(os.environ.get("LIBRESPOT_OAUTH_PORT", "5588"))
# librespot prints the redirect target on this loopback port; the browser must
# be able to reach it so librespot can capture the authorization code.
REDIRECT_HOST = f"127.0.0.1:{OAUTH_PORT}"


def _find_librespot() -> str | None:
    for loc in (shutil.which("librespot"), "/usr/local/bin/librespot"):
        if loc and os.path.isfile(loc):
            return loc
    return None


def _spawn_librespot_oauth(cache_dir: str, log_path: str) -> subprocess.Popen:
    """Start ``librespot --enable-oauth`` writing credentials into cache_dir.

    cache_dir must be empty of a ``credentials.json`` -- if librespot finds a
    cached credential it skips the OAuth flow entirely (and never prints the
    "Browse to:" URL), so callers always point this at a fresh temp directory.
    """
    librespot = _find_librespot()
    if not librespot:
        raise FileNotFoundError("librespot binary not found")

    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "w")  # noqa: SIM115 (closed when proc ends)
    return subprocess.Popen(
        [
            librespot,
            "--enable-oauth",
            "--oauth-port", str(OAUTH_PORT),
            "-n", LIBRESPOT_DEVICE_NAME,
            "--backend", "pipe",
            "--disable-discovery",
            "--disable-audio-cache",
            "-c", cache_dir,
        ],
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )


def _read_browse_url(log_path: str, timeout: int = 20) -> str | None:
    """Read the 'Browse to: <url>' line librespot prints on startup."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            for line in Path(log_path).read_text().splitlines():
                if line.startswith("Browse to:"):
                    return line.split("Browse to:", 1)[1].strip()
        except OSError:
            pass
        time.sleep(0.5)
    return None


async def _drive_browser_approval(page, url: str) -> bool:
    """Navigate the authorize URL and click through Google / Spotify consent.

    The final redirect to ``http://127.0.0.1:PORT/login`` must be allowed to
    load so librespot's local server captures the code (do NOT intercept it).
    Returns True once the browser reaches the librespot redirect server.
    """
    logger.info("Driving librespot OAuth approval in browser...")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:  # noqa: BLE001 - navigation quirks are expected
        logger.debug(f"Navigation note: {e}")
    await page.wait_for_timeout(2000)

    for _ in range(8):
        current = page.url
        if REDIRECT_HOST in current:
            logger.info("librespot OAuth redirect reached local server")
            return True

        # "Continue with Google" (Spotify login page)
        google_button = None
        for selector in (
            'button:has-text("Continue with Google")',
            '[data-testid="google-login-button"]',
            'a:has-text("Continue with Google")',
        ):
            google_button = await page.query_selector(selector)
            if google_button:
                break
        if google_button:
            logger.info("Clicking 'Continue with Google'")
            await google_button.click()
            await page.wait_for_timeout(4000)
            continue

        # Spotify consent / authorize screen
        agree = await page.query_selector('button[data-testid="auth-accept"]')
        if agree:
            logger.info("Clicking Spotify authorize/accept")
            await agree.click()
            await page.wait_for_timeout(4000)
            continue

        # Google account chooser
        account = await page.query_selector("div[data-identifier]")
        if account:
            logger.info("Choosing Google account")
            await account.click()
            await page.wait_for_timeout(4000)
            continue

        await page.wait_for_timeout(2000)

    return REDIRECT_HOST in page.url


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


async def refresh_librespot_credentials(page) -> bool:
    """Mint librespot stored credentials via its native OAuth flow.

    Always mints into a fresh temp directory (so librespot performs the OAuth
    flow instead of reusing an existing credential) and then atomically swaps
    the new ``credentials.json`` into place. This keeps any currently-live
    credentials valid until the new one is fully written -- a concurrent
    download never sees a missing credentials file.

    The browser must already be logged into Google (the SSO identity for the
    Spotify account). Returns True if new credentials were installed.
    """
    creds_dir = get_librespot_credentials_dir()
    Path(creds_dir).mkdir(parents=True, exist_ok=True)
    # credentials.json holds reusable Spotify auth -- keep it private to the
    # (single) service user rather than relying on librespot's umask.
    try:
        os.chmod(creds_dir, 0o700)
    except OSError:
        pass
    creds_file = Path(creds_dir) / "credentials.json"
    # Temp dir on the SAME filesystem as creds_dir so os.replace is atomic.
    tmp_dir = os.path.join(creds_dir, ".oauth-tmp")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    log_path = os.path.join(creds_dir, "oauth.log")

    proc = None
    try:
        proc = _spawn_librespot_oauth(tmp_dir, log_path)
    except FileNotFoundError as e:
        logger.error(f"Cannot refresh librespot credentials: {e}")
        return False

    try:
        url = _read_browse_url(log_path)
        if not url:
            logger.error("librespot did not print an OAuth URL")
            return False

        if not await _drive_browser_approval(page, url):
            logger.error("Browser did not complete librespot OAuth approval")
            return False

        # Give librespot time to exchange the code and write credentials.
        tmp_creds = Path(tmp_dir) / "credentials.json"
        for _ in range(12):
            if tmp_creds.exists():
                break
            await asyncio.sleep(1)

        if not tmp_creds.exists():
            logger.error("librespot OAuth completed but no credentials.json written")
            return False

        os.replace(tmp_creds, creds_file)  # atomic swap into the live location
        try:
            os.chmod(creds_file, 0o600)
        except OSError:
            pass
        logger.info(f"librespot credentials written to {creds_file}")
        return True
    finally:
        _stop(proc)
        shutil.rmtree(tmp_dir, ignore_errors=True)
