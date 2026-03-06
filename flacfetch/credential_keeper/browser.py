"""Browser lifecycle management using Patchright (anti-detection Playwright fork)."""
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Default profile directory (persistent disk on GCE)
DEFAULT_PROFILE_DIR = "/mnt/flacfetch-data/browser-profiles/google"


def get_profile_dir() -> str:
    base = os.environ.get("BROWSER_PROFILE_DIR", "/mnt/flacfetch-data/browser-profiles")
    return os.path.join(base, "google")


async def launch_browser():
    """Launch a persistent Chromium browser via Patchright.

    Returns (browser, context, page) tuple.
    The browser uses a persistent profile directory so sessions survive restarts.
    """
    from patchright.async_api import async_playwright

    profile_dir = get_profile_dir()
    Path(profile_dir).mkdir(parents=True, exist_ok=True)

    logger.info(f"Launching browser with profile at {profile_dir}")

    # Remove stale lock files from previous crashes to prevent
    # "Opening in existing browser session" errors
    for lock_file in ["SingletonLock", "SingletonCookie", "SingletonSocket"]:
        lock_path = Path(profile_dir) / lock_file
        lock_path.unlink(missing_ok=True)

    pw = await async_playwright().start()

    # Launch persistent context (keeps cookies, localStorage, etc.)
    context = await pw.chromium.launch_persistent_context(
        user_data_dir=profile_dir,
        headless=False,  # Headed mode via Xvfb to avoid headless detection
        viewport={"width": 1280, "height": 720},
        locale="en-US",
        timezone_id="America/New_York",
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    )

    # Use existing page or create one
    if context.pages:
        page = context.pages[0]
    else:
        page = await context.new_page()

    logger.info("Browser launched successfully")
    return pw, context, page


async def close_browser(pw, context):
    """Gracefully close browser and playwright."""
    try:
        await context.close()
    except Exception as e:
        logger.warning(f"Error closing browser context: {e}")
    try:
        await pw.stop()
    except Exception as e:
        logger.warning(f"Error stopping playwright: {e}")
