"""Google account login automation.

Handles logging into accounts.google.com when the session has expired.
The Google account (nomadflacfetch@gmail.com) has 2FA disabled for automation.
"""
import logging
import os

logger = logging.getLogger(__name__)


async def is_google_logged_in(page) -> bool:
    """Check if the browser is currently logged into Google."""
    try:
        await page.goto("https://myaccount.google.com/", wait_until="domcontentloaded", timeout=15000)
        # If we land on myaccount and see profile info, we're logged in.
        # If redirected to accounts.google.com/signin, we're not.
        url = page.url
        if "accounts.google.com" in url and ("signin" in url or "ServiceLogin" in url):
            logger.info("Google session expired - not logged in")
            return False

        # Check for profile content on the myaccount page
        try:
            await page.wait_for_selector('a[aria-label*="Google Account"]', timeout=5000)
            logger.info("Google session active - logged in")
            return True
        except Exception:
            # Fallback: if we're on myaccount.google.com and not redirected to signin, likely logged in
            if "myaccount.google.com" in page.url:
                logger.info("Google session appears active (on myaccount page)")
                return True
            return False
    except Exception as e:
        logger.warning(f"Error checking Google login status: {e}")
        return False


async def google_login(page) -> bool:
    """Log into Google using stored credentials.

    Credentials are read from environment variables:
    - FLACFETCH_ACCOUNT_EMAIL
    - FLACFETCH_ACCOUNT_PASSWORD

    Returns True if login succeeded.
    """
    email = os.environ.get("FLACFETCH_ACCOUNT_EMAIL")
    password = os.environ.get("FLACFETCH_ACCOUNT_PASSWORD")

    if not email or not password:
        logger.error("FLACFETCH_ACCOUNT_EMAIL and FLACFETCH_ACCOUNT_PASSWORD must be set")
        return False

    try:
        logger.info(f"Logging into Google as {email}")

        # Navigate to Google sign-in
        await page.goto("https://accounts.google.com/signin", wait_until="domcontentloaded", timeout=30000)

        # Enter email
        email_input = await page.wait_for_selector('input[type="email"]', timeout=10000)
        await email_input.fill(email)
        await page.click("#identifierNext")

        # Wait for password page
        password_input = await page.wait_for_selector('input[type="password"]:visible', timeout=10000)
        await password_input.fill(password)
        await page.click("#passwordNext")

        # Wait for navigation to complete (successful login redirects away from accounts.google.com)
        await page.wait_for_timeout(3000)

        # Check for common post-login prompts
        try:
            # "Stay signed in?" prompt
            stay_signed_in = await page.query_selector('button:has-text("Yes")')
            if stay_signed_in:
                await stay_signed_in.click()
                await page.wait_for_timeout(2000)
        except Exception:
            pass

        # Verify login succeeded
        if await is_google_logged_in(page):
            logger.info("Google login successful")
            return True

        # Check if we're stuck on a verification page
        current_url = page.url
        if "challenge" in current_url or "verify" in current_url:
            logger.error(f"Google login requires additional verification at: {current_url}")
            return False

        logger.error(f"Google login may have failed - current URL: {current_url}")
        return False

    except Exception as e:
        logger.error(f"Google login failed: {e}")
        return False


async def ensure_google_logged_in(page) -> bool:
    """Ensure the browser is logged into Google, logging in if needed.

    Returns True if logged in (or login succeeded).
    """
    if await is_google_logged_in(page):
        return True
    return await google_login(page)
