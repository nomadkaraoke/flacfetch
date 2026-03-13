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

from .browser import launch_browser
from .google_login import ensure_google_logged_in
from .spotify import refresh_spotify_token
from .youtube import refresh_youtube_cookies

logger = logging.getLogger(__name__)

# Refresh intervals (in seconds)
YOUTUBE_REFRESH_INTERVAL = int(os.environ.get("KEEPER_YOUTUBE_INTERVAL", 8 * 3600))  # 8 hours
SPOTIFY_REFRESH_INTERVAL = int(os.environ.get("KEEPER_SPOTIFY_INTERVAL", 12 * 3600))  # 12 hours

# Whether to send Pushbullet notifications on successful refreshes
NOTIFY_ON_SUCCESS = os.environ.get("KEEPER_NOTIFY_ON_SUCCESS", "false").lower() in ("true", "1", "yes")

# Status file path
STATUS_FILE = os.environ.get("KEEPER_STATUS_FILE", "/mnt/flacfetch-data/keeper-status.json")


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
    pw = None
    context = None

    try:
        pw, context, page = await launch_browser()

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
        last_spotify_refresh = float("-inf")

        while True:
            now = asyncio.get_event_loop().time()

            # YouTube cookie refresh
            if now - last_youtube_refresh >= YOUTUBE_REFRESH_INTERVAL:
                logger.info("--- YouTube cookie refresh ---")

                # Ensure Google session is still active
                if not await ensure_google_logged_in(page):
                    logger.error("Google session lost, could not re-login")
                    status.setdefault("youtube", {}).update({
                        "last_refresh": datetime.now(timezone.utc).isoformat(),
                        "last_refresh_status": "error",
                        "error": "Google session lost",
                    })
                    await _send_notification(
                        "Credential Keeper: Google Login Lost",
                        "Google session expired and re-login failed.",
                    )
                else:
                    success = await refresh_youtube_cookies(page, context)
                    status.setdefault("youtube", {}).update({
                        "last_refresh": datetime.now(timezone.utc).isoformat(),
                        "last_refresh_status": "ok" if success else "error",
                    })
                    if success:
                        if NOTIFY_ON_SUCCESS:
                            await _send_notification(
                                "✅ YouTube Cookies Refreshed",
                                "YouTube cookies extracted and uploaded successfully.",
                            )
                    else:
                        await _send_notification(
                            "❌ YouTube Cookie Refresh Failed",
                            "Could not extract/upload YouTube cookies.",
                        )

                last_youtube_refresh = now
                _save_status(status)

            # Spotify token refresh
            if now - last_spotify_refresh >= SPOTIFY_REFRESH_INTERVAL:
                logger.info("--- Spotify token refresh ---")

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

            # Sleep before next check (1 minute intervals)
            await asyncio.sleep(60)

    except Exception as e:
        logger.error(f"Credential keeper crashed: {e}", exc_info=True)
        await _send_notification("Credential Keeper Crashed", str(e))
        raise
    finally:
        if context:
            try:
                await context.close()
            except Exception:
                pass
        if pw:
            try:
                await pw.stop()
            except Exception:
                pass
