"""Main credential keeper orchestrator.

Runs a scheduling loop that periodically refreshes YouTube cookies and
Spotify OAuth tokens using a persistent browser session logged into Google.
"""
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..core.config import get_librespot_credentials_dir, get_youtube_cookies_path
from .browser import close_browser, launch_browser
from .google_login import ensure_google_logged_in
from .librespot import refresh_librespot_credentials
from .probe import ProbeOutcome, probe_cookies
from .spotify import refresh_spotify_token
from .youtube import refresh_youtube_cookies

logger = logging.getLogger(__name__)

# Refresh intervals (in seconds)
YOUTUBE_REFRESH_INTERVAL = int(os.environ.get("KEEPER_YOUTUBE_INTERVAL", 8 * 3600))  # 8 hours
# How often to validate the exported cookies with a real yt-dlp probe between
# refreshes. YouTube can kill the exported session server-side within hours of
# a refresh while the live browser session stays "logged in" (2026-08-24
# incident: search lost all YouTube results for ~30h with a "healthy" keeper),
# so the probe — not the browser's login state — is the health signal.
YOUTUBE_PROBE_INTERVAL = int(os.environ.get("KEEPER_YOUTUBE_PROBE_INTERVAL", 30 * 60))  # 30 min
# Refresh attempts per cycle; attempts after the first relaunch the browser
# (a fresh launch is the only known way to get a valid export once YouTube has
# invalidated the session server-side).
YOUTUBE_MAX_REFRESH_ATTEMPTS = int(os.environ.get("KEEPER_YOUTUBE_MAX_ATTEMPTS", 3))
SPOTIFY_REFRESH_INTERVAL = int(os.environ.get("KEEPER_SPOTIFY_INTERVAL", 12 * 3600))  # 12 hours
# librespot stored credentials are long-lived (no ~1h access-token expiry), so
# this only needs to run occasionally to re-mint if they are ever invalidated.
LIBRESPOT_REFRESH_INTERVAL = int(os.environ.get("KEEPER_LIBRESPOT_INTERVAL", 24 * 3600))  # 24 hours
# When credentials are missing we want to self-heal quickly, but not retry the
# (browser-driven, rate-limit-prone) OAuth flow every loop iteration. Back off
# to this interval between attempts while credentials are still absent.
LIBRESPOT_MISSING_RETRY_INTERVAL = int(
    os.environ.get("KEEPER_LIBRESPOT_RETRY_INTERVAL", 15 * 60)  # 15 minutes
)

# Whether to send Pushbullet notifications on successful refreshes
NOTIFY_ON_SUCCESS = os.environ.get("KEEPER_NOTIFY_ON_SUCCESS", "false").lower() in ("true", "1", "yes")

# Status file path
STATUS_FILE = os.environ.get("KEEPER_STATUS_FILE", "/mnt/flacfetch-data/browser-profiles/keeper-status.json")


def _setup_logging():
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )
    # Reduce noise from libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _load_status() -> dict:
    try:
        with open(STATUS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_status(status: dict):
    try:
        Path(STATUS_FILE).parent.mkdir(parents=True, exist_ok=True)
        with open(STATUS_FILE, "w") as f:
            json.dump(status, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not save status file: {e}")


async def _send_notification(title: str, body: str):
    """Send a Pushbullet notification."""
    api_key = os.environ.get("PUSHBULLET_API_KEY")
    if not api_key:
        return

    try:
        import httpx

        async with httpx.AsyncClient() as client:
            await client.post(
                "https://api.pushbullet.com/v2/pushes",
                headers={"Access-Token": api_key, "Content-Type": "application/json"},
                json={"type": "note", "title": title, "body": body},
                timeout=30,
            )
    except Exception as e:
        logger.warning(f"Failed to send notification: {e}")


async def _relaunch_browser(browser: dict):
    """Close and relaunch the browser, updating the refs dict in place."""
    logger.info("Relaunching browser for a fresh session...")
    await close_browser(browser["pw"], browser["context"])
    pw, context, page = await launch_browser()
    browser.update(pw=pw, context=context, page=page)


async def refresh_and_validate_youtube(
    browser: dict,
    status: dict,
    *,
    max_attempts: int = None,
    relaunch_first: bool = False,
    relaunch=_relaunch_browser,
    ensure_login=None,
    refresh=None,
    probe=None,
) -> bool:
    """Refresh YouTube cookies and validate the export with a real yt-dlp probe.

    A "logged in" browser session is NOT proof the exported cookies work:
    YouTube can invalidate the export server-side while the live session stays
    active. If the probe rejects the export, relaunch the browser and retry.

    Set ``relaunch_first`` when the current session's export is already known
    dead (periodic probe failed) — re-exporting from that session would be a
    guaranteed no-op, so start from a fresh browser instead.

    The refresh/probe/login callables are injectable for tests; production
    callers use the defaults.
    """
    if max_attempts is None:
        max_attempts = YOUTUBE_MAX_REFRESH_ATTEMPTS
    ensure_login = ensure_login or ensure_google_logged_in
    refresh = refresh or refresh_youtube_cookies
    probe = probe or probe_cookies

    yt_status = status.setdefault("youtube", {})
    failure_reason = "no attempts made"

    for attempt in range(1, max_attempts + 1):
        if attempt > 1 or relaunch_first:
            await relaunch(browser)
        page, context = browser["page"], browser["context"]

        if not await ensure_login(page):
            failure_reason = "Google session lost and re-login failed"
            logger.error(f"{failure_reason} (attempt {attempt}/{max_attempts})")
            continue

        if not await refresh(page, context):
            failure_reason = "Cookie extraction/upload failed"
            logger.error(f"{failure_reason} (attempt {attempt}/{max_attempts})")
            continue

        outcome, msg = await probe(get_youtube_cookies_path())
        yt_status.update({
            "last_probe": datetime.now(timezone.utc).isoformat(),
            "last_probe_status": outcome.value,
            "last_probe_message": msg,
        })

        if outcome is ProbeOutcome.INVALID_COOKIES:
            failure_reason = msg
            logger.warning(
                f"Exported cookies rejected by probe (attempt {attempt}/{max_attempts}): {msg}"
            )
            continue

        if outcome is ProbeOutcome.TRANSIENT:
            # The export uploaded fine and the failure isn't a cookie rejection —
            # don't churn the browser over a network blip. The periodic probe
            # will catch it if the cookies are actually dead.
            logger.warning(f"Probe inconclusive, accepting refresh: {msg}")
        else:
            logger.info(msg)
        return True

    yt_status["last_failure_reason"] = failure_reason
    logger.error(
        f"YouTube cookies still invalid after {max_attempts} attempts: {failure_reason}"
    )
    return False


async def run_keeper():
    """Main keeper loop. Launches browser and periodically refreshes credentials."""
    _setup_logging()
    logger.info("Credential keeper starting...")

    # Validate required environment variables
    required_vars = ["FLACFETCH_ACCOUNT_EMAIL", "FLACFETCH_ACCOUNT_PASSWORD", "FLACFETCH_API_KEY"]
    missing = [v for v in required_vars if not os.environ.get(v)]
    if missing:
        logger.error(f"Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)

    status = _load_status()
    browser = {"pw": None, "context": None, "page": None}

    try:
        pw, context, page = await launch_browser()
        browser.update(pw=pw, context=context, page=page)

        # Initial Google login check
        if not await ensure_google_logged_in(page):
            logger.error("Failed to log into Google - credential keeper cannot proceed")
            await _send_notification(
                "Credential Keeper: Google Login Failed",
                "Could not log into nomadflacfetch@gmail.com. Manual intervention needed.",
            )
            sys.exit(1)

        status["google_session"] = {
            "logged_in": True,
            "last_login": datetime.now(timezone.utc).isoformat(),
        }
        _save_status(status)

        logger.info(
            f"Keeper running. YouTube every {YOUTUBE_REFRESH_INTERVAL // 3600}h, "
            f"Spotify every {SPOTIFY_REFRESH_INTERVAL // 3600}h"
        )

        # Initialize to trigger immediately on first loop iteration
        last_youtube_refresh = float("-inf")
        last_youtube_probe = float("-inf")
        last_spotify_refresh = float("-inf")
        last_librespot_refresh = float("-inf")

        while True:
            now = asyncio.get_event_loop().time()

            # Between refreshes, validate the exported cookies with a real
            # yt-dlp probe — a "logged in" browser is not proof the export
            # still works. A failed probe forces an immediate refresh cycle.
            refresh_due = now - last_youtube_refresh >= YOUTUBE_REFRESH_INTERVAL
            probe_found_dead = False
            if not refresh_due and now - last_youtube_probe >= YOUTUBE_PROBE_INTERVAL:
                outcome, msg = await probe_cookies(get_youtube_cookies_path())
                status.setdefault("youtube", {}).update({
                    "last_probe": datetime.now(timezone.utc).isoformat(),
                    "last_probe_status": outcome.value,
                    "last_probe_message": msg,
                })
                last_youtube_probe = now
                _save_status(status)
                if outcome is ProbeOutcome.INVALID_COOKIES:
                    logger.warning(f"Periodic probe found dead cookies — remediating: {msg}")
                    refresh_due = True
                    probe_found_dead = True
                else:
                    logger.info(f"Periodic cookie probe: {msg}")

            # YouTube cookie refresh (+ probe validation with browser-restart
            # self-heal — see refresh_and_validate_youtube)
            if refresh_due:
                logger.info("--- YouTube cookie refresh ---")

                # If the periodic probe just proved the current session's
                # export dead, re-exporting from it is a guaranteed no-op —
                # start from a fresh browser.
                success = await refresh_and_validate_youtube(
                    browser, status, relaunch_first=probe_found_dead
                )
                status.setdefault("youtube", {}).update({
                    "last_refresh": datetime.now(timezone.utc).isoformat(),
                    "last_refresh_status": "ok" if success else "error",
                })
                if success:
                    if NOTIFY_ON_SUCCESS:
                        probe_ok = status.get("youtube", {}).get("last_probe_status") == "ok"
                        await _send_notification(
                            "✅ YouTube Cookies Refreshed",
                            "YouTube cookies extracted, uploaded, and probe-validated."
                            if probe_ok
                            else "YouTube cookies extracted and uploaded (probe inconclusive).",
                        )
                else:
                    await _send_notification(
                        "❌ YouTube Cookies Invalid After Self-Heal",
                        "Cookie export still failing the yt-dlp probe after "
                        f"{YOUTUBE_MAX_REFRESH_ATTEMPTS} attempts (with browser "
                        "restarts). Manual intervention needed: "
                        f"{status.get('youtube', {}).get('last_failure_reason', 'unknown')}",
                    )

                last_youtube_refresh = now
                last_youtube_probe = now
                _save_status(status)

            # Spotify token refresh
            if now - last_spotify_refresh >= SPOTIFY_REFRESH_INTERVAL:
                logger.info("--- Spotify token refresh ---")

                page = browser["page"]
                if not await ensure_google_logged_in(page):
                    logger.error("Google session lost, could not re-login")
                    status.setdefault("spotify", {}).update({
                        "last_refresh": datetime.now(timezone.utc).isoformat(),
                        "last_refresh_status": "error",
                        "error": "Google session lost",
                    })
                else:
                    success = await refresh_spotify_token(page)
                    status.setdefault("spotify", {}).update({
                        "last_refresh": datetime.now(timezone.utc).isoformat(),
                        "last_refresh_status": "ok" if success else "error",
                        "used_google_login": True,
                    })
                    if success:
                        if NOTIFY_ON_SUCCESS:
                            await _send_notification(
                                "✅ Spotify Token Refreshed",
                                "Spotify OAuth token obtained and uploaded successfully.",
                            )
                    else:
                        await _send_notification(
                            "❌ Spotify Token Refresh Failed",
                            "Could not complete Spotify OAuth flow.",
                        )

                last_spotify_refresh = now
                _save_status(status)

            # librespot stored-credential refresh. Also runs when the credentials
            # file is missing (e.g. first setup / after a rebuild) so the download
            # path self-heals -- but throttled to LIBRESPOT_MISSING_RETRY_INTERVAL
            # so a persistently-failing OAuth flow doesn't retry every loop.
            creds_missing = not os.path.isfile(
                os.path.join(get_librespot_credentials_dir(), "credentials.json")
            )
            librespot_due = now - last_librespot_refresh >= (
                LIBRESPOT_MISSING_RETRY_INTERVAL if creds_missing
                else LIBRESPOT_REFRESH_INTERVAL
            )
            if librespot_due:
                logger.info("--- librespot credential refresh ---")

                page = browser["page"]
                if not await ensure_google_logged_in(page):
                    logger.error("Google session lost, could not re-login")
                    status.setdefault("librespot", {}).update({
                        "last_refresh": datetime.now(timezone.utc).isoformat(),
                        "last_refresh_status": "error",
                        "error": "Google session lost",
                    })
                else:
                    success = await refresh_librespot_credentials(page)
                    status.setdefault("librespot", {}).update({
                        "last_refresh": datetime.now(timezone.utc).isoformat(),
                        "last_refresh_status": "ok" if success else "error",
                    })
                    if success:
                        if NOTIFY_ON_SUCCESS:
                            await _send_notification(
                                "✅ librespot Credentials Refreshed",
                                "librespot stored credentials minted successfully.",
                            )
                    else:
                        await _send_notification(
                            "❌ librespot Credential Refresh Failed",
                            "Could not complete librespot OAuth flow.",
                        )

                last_librespot_refresh = now
                _save_status(status)

            # Sleep before next check (1 minute intervals)
            await asyncio.sleep(60)

    except Exception as e:
        logger.error(f"Credential keeper crashed: {e}", exc_info=True)
        await _send_notification("Credential Keeper Crashed", str(e))
        raise
    finally:
        if browser["context"]:
            try:
                await browser["context"].close()
            except Exception:
                pass
        if browser["pw"]:
            try:
                await browser["pw"].stop()
            except Exception:
                pass
