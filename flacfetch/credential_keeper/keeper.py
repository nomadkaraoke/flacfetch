"""Main credential keeper orchestrator.

Runs a scheduling loop that periodically refreshes YouTube cookies and
Spotify OAuth tokens using a persistent browser session logged into Google.
"""
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..core.config import get_librespot_credentials_dir, get_youtube_cookies_path
from .browser import close_browser, launch_browser
from .google_login import ensure_google_logged_in
from .librespot import refresh_librespot_credentials
from .probe import ProbeOutcome, probe_cookies
from .refresh_request import COOLDOWN_SECONDS as YOUTUBE_ON_DEMAND_COOLDOWN
from .refresh_request import consume_refresh_request, refresh_requested
from .spotify import refresh_spotify_token
from .youtube import refresh_youtube_cookies

logger = logging.getLogger(__name__)

# Refresh intervals (in seconds)
# Only applies while probes are inconclusive (for example the cookie file is
# missing). A passing probe never re-exports: the export is working, and the old
# unconditional 8h re-export put the browser back on youtube.com, where the new
# export died within 30 minutes (0 of 41 survived, 2026-10-05 to 11-03).
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
# Cooldown between "self-heal failed" notifications. A persistently-failing
# export would otherwise page on every 30-min probe cycle (~48 pushes/day).
YOUTUBE_FAILURE_ALERT_INTERVAL = int(
    os.environ.get("KEEPER_YOUTUBE_FAILURE_ALERT_INTERVAL", 4 * 3600)  # 4 hours
)
# More dead probes than this in a UTC day sends one notification. Self-heal hides
# these, but cookies are dead for up to a probe interval each time, and real
# searches and downloads fail during that gap.
YOUTUBE_CHURN_ALERT_THRESHOLD = int(os.environ.get("KEEPER_YOUTUBE_CHURN_THRESHOLD", 6))
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


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _record_youtube_event(status: dict, event: str) -> dict:
    """Count a probe event in today's stats, logging yesterday's on rollover."""
    yt_status = status.setdefault("youtube", {})
    stats = yt_status.get("daily_stats")
    today = _today()
    if not stats or stats.get("date") != today:
        if stats:
            logger.info(
                f"YouTube probe summary date={stats['date']} ok={stats['ok_probes']} "
                f"dead={stats['dead_probes']} rejected={stats['rejected_exports']} "
                f"on_demand={stats['on_demand_refreshes']}"
            )
        stats = yt_status["daily_stats"] = {
            "date": today,
            "ok_probes": 0,
            "dead_probes": 0,
            "rejected_exports": 0,
            "on_demand_refreshes": 0,
            "churn_alerted": False,
        }
    stats[event] += 1
    return stats


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
            _record_youtube_event(status, "rejected_exports")
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


@dataclass
class YouTubeTimers:
    """Event-loop timestamps for maintain_youtube. Defaults mean "never"."""

    last_refresh: float = float("-inf")
    last_probe: float = float("-inf")
    last_failure_alert: float = float("-inf")
    last_on_demand: float = float("-inf")


async def maintain_youtube(
    browser: dict,
    status: dict,
    timers: YouTubeTimers,
    now: float,
    *,
    probe=None,
    refresh=None,
    notify=None,
    consume_request=None,
) -> None:
    """One keeper-loop pass for YouTube cookies.

    Re-exports only when something says the cookies aren't working: a dead probe, a
    bot block reported by the API, or (past the max age) an inconclusive probe. The
    first pass after startup always exports, because restarting the keeper is the
    documented way to force fresh cookies.
    """
    probe = probe or probe_cookies
    refresh = refresh or refresh_and_validate_youtube
    notify = notify or _send_notification
    consume_request = consume_request or consume_refresh_request

    refresh_needed = timers.last_refresh == float("-inf")
    relaunch_first = False

    # Always consume, so a request dropped by the cooldown can't fire later.
    if consume_request():
        if now - timers.last_on_demand >= YOUTUBE_ON_DEMAND_COOLDOWN:
            logger.warning("Cookie refresh requested by the API after a YouTube bot block")
            timers.last_on_demand = now
            _record_youtube_event(status, "on_demand_refreshes")
            relaunch_first = not refresh_needed
            refresh_needed = True
        else:
            logger.info("Ignoring cookie refresh request: inside the on-demand cooldown")

    if not refresh_needed and now - timers.last_probe >= YOUTUBE_PROBE_INTERVAL:
        outcome, msg = await probe(get_youtube_cookies_path())
        status.setdefault("youtube", {}).update({
            "last_probe": datetime.now(timezone.utc).isoformat(),
            "last_probe_status": outcome.value,
            "last_probe_message": msg,
        })
        timers.last_probe = now
        if outcome is ProbeOutcome.INVALID_COOKIES:
            _record_youtube_event(status, "dead_probes")
            logger.warning(f"Periodic probe found dead cookies — remediating: {msg}")
            refresh_needed = True
            relaunch_first = True
        else:
            if outcome is ProbeOutcome.OK:
                _record_youtube_event(status, "ok_probes")
            logger.info(f"Periodic cookie probe: {msg}")
            refresh_needed = (
                outcome is ProbeOutcome.TRANSIENT
                and now - timers.last_refresh >= YOUTUBE_REFRESH_INTERVAL
            )
        _save_status(status)

    if refresh_needed:
        logger.info("--- YouTube cookie refresh ---")
        # relaunch_first: this session's export is known dead, so re-exporting from
        # it would be a no-op. Start from a fresh browser.
        success = await refresh(browser, status, relaunch_first=relaunch_first)
        status.setdefault("youtube", {}).update({
            "last_refresh": datetime.now(timezone.utc).isoformat(),
            "last_refresh_status": "ok" if success else "error",
        })
        if success:
            if NOTIFY_ON_SUCCESS:
                probe_ok = status.get("youtube", {}).get("last_probe_status") == "ok"
                await notify(
                    "✅ YouTube Cookies Refreshed",
                    "YouTube cookies extracted, uploaded, and probe-validated."
                    if probe_ok
                    else "YouTube cookies extracted and uploaded (probe inconclusive).",
                )
        elif now - timers.last_failure_alert >= YOUTUBE_FAILURE_ALERT_INTERVAL:
            # Throttled: a persistently-dead export re-remediates every
            # probe cycle; without a cooldown that's ~48 pushes/day.
            timers.last_failure_alert = now
            await notify(
                "❌ YouTube Cookies Invalid After Self-Heal",
                "Cookie export still failing the yt-dlp probe after "
                f"{YOUTUBE_MAX_REFRESH_ATTEMPTS} attempts (with browser "
                "restarts). Manual intervention needed: "
                f"{status.get('youtube', {}).get('last_failure_reason', 'unknown')}",
            )

        timers.last_refresh = now
        timers.last_probe = now
        _save_status(status)

    await _alert_on_churn(status, notify)


async def _alert_on_churn(status: dict, notify) -> None:
    """Notify once a UTC day when dead probes pass the threshold."""
    stats = status.get("youtube", {}).get("daily_stats")
    if not stats or stats["churn_alerted"]:
        return
    dead = stats["dead_probes"] + stats["rejected_exports"]
    if dead <= YOUTUBE_CHURN_ALERT_THRESHOLD:
        return
    stats["churn_alerted"] = True
    _save_status(status)
    await notify(
        "⚠️ YouTube Cookie Churn",
        f"{dead} dead cookie probes so far today (limit {YOUTUBE_CHURN_ALERT_THRESHOLD}). "
        "Self-heal is replacing them, but YouTube searches and downloads can fail until "
        "it does, and searches return no YouTube results. Check "
        "/var/log/flacfetch-credential-keeper.log on flacup and deploy/README.md "
        "§ YouTube cookie health.",
    )


async def _idle(seconds: float, poll: float = 2.0) -> None:
    """Sleep between passes, waking early if the API has requested a refresh."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while (remaining := deadline - loop.time()) > 0:
        if refresh_requested():
            return
        await asyncio.sleep(min(poll, remaining))


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
            f"Keeper running. YouTube probe every {YOUTUBE_PROBE_INTERVAL // 60}m, "
            f"Spotify every {SPOTIFY_REFRESH_INTERVAL // 3600}h"
        )

        # Initialize to trigger immediately on first loop iteration
        youtube_timers = YouTubeTimers()
        last_spotify_refresh = float("-inf")
        last_librespot_refresh = float("-inf")

        while True:
            now = asyncio.get_event_loop().time()

            await maintain_youtube(browser, status, youtube_timers, now)

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
            await _idle(60)

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
