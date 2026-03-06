"""Google account login automation.

Handles logging into accounts.google.com when the session has expired.
The Google account (nomadflacfetch@gmail.com) has 2FA disabled for automation.
"""
import logging
import os

logger = logging.getLogger(__name__)


async def is_google_logged_in(page) -> bool:
    """Check if the browser is currently logged into Google.

    Navigates to accounts.google.com which is lighter than myaccount.google.com
    and quickly reveals whether the user is signed in or redirected to a login form.
    """
    try:
        await page.goto(
            "https://accounts.google.com/",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        await page.wait_for_timeout(2000)

        url = page.url
        # If redirected to a sign-in page, we're not logged in
        if "signin" in url or "ServiceLogin" in url or "identifier" in url:
            logger.info("Google session expired - not logged in")
            return False

        # If we're on the account management page, we're logged in
        if "myaccount.google.com" in url or "accounts.google.com" in url:
            logger.info("Google session active - logged in")
            return True

        logger.info(f"Unexpected URL after login check: {url}")
        return False
    except Exception as e:
        logger.warning(f"Error checking Google login status: {e}")
        return False


async def google_login(page) -> bool:
    """Log into Google using stored credentials on the current sign-in page.

    Assumes the page is already on the Google sign-in form (e.g. after
    is_google_logged_in redirected there). Does NOT navigate again to avoid
    double-navigation issues.

    Returns True if login succeeded.
    """
    email = os.environ.get("FLACFETCH_ACCOUNT_EMAIL")
    password = os.environ.get("FLACFETCH_ACCOUNT_PASSWORD")

    if not email or not password:
        logger.error("FLACFETCH_ACCOUNT_EMAIL and FLACFETCH_ACCOUNT_PASSWORD must be set")
        return False

    try:
        logger.info(f"Logging into Google as {email}")

        # Check if we're already on the sign-in page (from is_google_logged_in redirect)
        current_url = page.url
        if "signin" not in current_url and "identifier" not in current_url and "ServiceLogin" not in current_url:
            # Not on sign-in page yet, navigate there
            await page.goto("https://accounts.google.com/signin", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

        # Enter email - use query_selector + fill as wait_for_selector has issues
        # with Patchright under systemd (times out despite element being present)
        email_input = None
        for _ in range(20):
            email_input = await page.query_selector('input[type="email"]')
            if email_input and await email_input.is_visible():
                break
            await page.wait_for_timeout(1000)
        if not email_input:
            logger.error("Could not find email input after 20s polling")
            return False

        await email_input.fill(email)
        await page.wait_for_timeout(500)

        # Click Next
        next_button = await page.query_selector("#identifierNext")
        if next_button:
            await next_button.click()
        else:
            await page.keyboard.press("Enter")

        # Wait for password page
        password_input = None
        for _ in range(15):
            password_input = await page.query_selector('input[type="password"]')
            if password_input and await password_input.is_visible():
                break
            await page.wait_for_timeout(1000)
        if not password_input:
            logger.error("Could not find password input after 15s polling")
            return False

        await password_input.fill(password)
        await page.wait_for_timeout(500)

        # Click Next
        next_button = await page.query_selector("#passwordNext")
        if next_button:
            await next_button.click()
        else:
            await page.keyboard.press("Enter")

        # Wait for navigation to complete
        await page.wait_for_timeout(5000)

        # Check for common post-login prompts
        try:
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
    # Page is already on the sign-in page from is_google_logged_in redirect
    return await google_login(page)
