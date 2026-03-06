"""Spotify token keeper - performs OAuth flow via 'Continue with Google' in the browser."""
import json
import logging
import os
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

logger = logging.getLogger(__name__)

FLACFETCH_API_URL = "http://localhost:8080"


def _build_oauth_url() -> str:
    """Build the Spotify OAuth authorization URL (same params as spotipy)."""
    client_id = os.environ.get("SPOTIPY_CLIENT_ID")
    if not client_id:
        raise ValueError("SPOTIPY_CLIENT_ID not set")

    redirect_uri = os.environ.get("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
    scope = "user-read-playback-state user-modify-playback-state streaming"

    params = urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scope,
    })
    return f"https://accounts.spotify.com/authorize?{params}"


async def _exchange_code_for_token(code: str) -> dict:
    """Exchange an authorization code for access + refresh tokens."""
    client_id = os.environ.get("SPOTIPY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ValueError("SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET must be set")
    redirect_uri = os.environ.get("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://accounts.spotify.com/api/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=30,
        )

    if response.status_code != 200:
        raise ValueError(f"Token exchange failed ({response.status_code}): {response.text}")

    token_data = response.json()

    # Add expires_at field (spotipy convention)
    import time
    token_data["expires_at"] = int(time.time()) + token_data.get("expires_in", 3600)

    return token_data


async def _upload_token(token_data: dict) -> bool:
    """Upload the token to flacfetch API (writes to file + Secret Manager)."""
    api_key = os.environ.get("FLACFETCH_API_KEY")
    if not api_key:
        logger.error("FLACFETCH_API_KEY not set, cannot upload token")
        return False

    token_json = json.dumps(token_data)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{FLACFETCH_API_URL}/config/spotify-token",
                json={"token": token_json},
                headers={"X-API-Key": api_key},
                timeout=30,
            )

        if response.status_code == 200:
            logger.info(f"Spotify token uploaded: {response.json().get('message', '')}")
            return True
        else:
            logger.error(f"Token upload failed ({response.status_code}): {response.text}")
            return False
    except Exception as e:
        logger.error(f"Token upload error: {e}")
        return False


async def refresh_spotify_token(page) -> bool:
    """Perform Spotify OAuth flow using 'Continue with Google'.

    The browser must already be logged into Google. This navigates to Spotify's
    OAuth authorize URL, clicks 'Continue with Google', and captures the
    authorization code from the redirect.

    Uses route interception to capture the localhost redirect URL before Chrome
    tries to navigate to it (which would fail since nothing is listening).

    Returns True if token was successfully obtained and uploaded.
    """
    try:
        oauth_url = _build_oauth_url()
        redirect_uri = os.environ.get("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
        redirect_host = urlparse(redirect_uri).netloc  # "127.0.0.1:8888"

        # Capture the redirect URL via request event listener.
        # When Spotify redirects to our localhost callback, Chrome can't load it
        # (nothing listening), but we can capture the URL from the request event.
        captured_url = {}

        def on_request(request):
            url = request.url
            if url.startswith(redirect_uri.rsplit("/", 1)[0]):
                captured_url["url"] = url
                logger.info(f"Captured redirect URL: {url[:80]}...")

        page.on("request", on_request)

        try:
            logger.info("Starting Spotify OAuth flow...")
            try:
                await page.goto(oauth_url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                # If Spotify auto-redirects to localhost (which isn't listening),
                # Chrome will throw ERR_CONNECTION_REFUSED. That's expected -
                # we capture the URL via the request event listener.
                if captured_url.get("url"):
                    logger.info("Spotify redirected immediately (auto-approved, caught via request event)")
                else:
                    logger.warning(f"Navigation error (may be expected): {e}")
            await page.wait_for_timeout(1000)

            # Check if already redirected (auto-approved)
            if captured_url.get("url"):
                code = _extract_code_from_url(captured_url["url"])
                if code:
                    logger.info("Spotify auto-approved (already authorized)")
                    token_data = await _exchange_code_for_token(code)
                    return await _upload_token(token_data)

            # Also check current URL in case Chrome followed the redirect
            current_url = page.url
            if redirect_host in current_url:
                code = _extract_code_from_url(current_url)
                if code:
                    logger.info("Spotify auto-approved (URL redirect)")
                    token_data = await _exchange_code_for_token(code)
                    return await _upload_token(token_data)

            # Look for "Continue with Google" button
            google_button = None
            for selector in [
                'button:has-text("Continue with Google")',
                '[data-testid="google-login-button"]',
                'button:has-text("Google")',
                'a:has-text("Continue with Google")',
            ]:
                google_button = await page.query_selector(selector)
                if google_button:
                    break

            if not google_button:
                # Maybe we're on the Spotify consent/agree page
                agree_button = await page.query_selector('button[data-testid="auth-accept"]')
                if agree_button:
                    logger.info("Clicking Spotify authorize/agree button")
                    await agree_button.click()
                    await page.wait_for_timeout(5000)
                    if captured_url.get("url"):
                        code = _extract_code_from_url(captured_url["url"])
                        if code:
                            token_data = await _exchange_code_for_token(code)
                            return await _upload_token(token_data)

                logger.error(f"Could not find 'Continue with Google' button. Current URL: {page.url}")
                return False

            logger.info("Clicking 'Continue with Google'")
            await google_button.click()

            # Wait for the redirect to be captured (via request event or URL change)
            for _ in range(30):
                if captured_url.get("url"):
                    break
                # Also check current URL
                current_url = page.url
                if redirect_host in current_url or "chrome-error" in current_url:
                    # Chrome may have tried to load the redirect and failed
                    break
                await page.wait_for_timeout(1000)

            # Try extracting from captured request or current URL
            redirect_url = captured_url.get("url") or page.url
            if "chrome-error" in redirect_url:
                # Chrome error page - the redirect URL might be in navigation history
                # Try going back to find it
                logger.warning("Chrome showed error page for redirect. Checking navigation entries...")
                redirect_url = captured_url.get("url", "")

            # Check for Spotify consent page if no redirect yet
            if not captured_url.get("url") and redirect_host not in page.url:
                agree_button = await page.query_selector('button[data-testid="auth-accept"]')
                if agree_button:
                    logger.info("Clicking Spotify authorize/agree button after Google login")
                    await agree_button.click()
                    for _ in range(15):
                        if captured_url.get("url"):
                            break
                        await page.wait_for_timeout(1000)
                    redirect_url = captured_url.get("url") or page.url

            code = _extract_code_from_url(redirect_url)
            if not code:
                logger.error(f"No authorization code in URL: {redirect_url[:100]}")
                return False

            logger.info("Got authorization code, exchanging for token...")
            token_data = await _exchange_code_for_token(code)
            return await _upload_token(token_data)

        finally:
            page.remove_listener("request", on_request)

    except Exception as e:
        logger.error(f"Spotify OAuth flow failed: {e}")
        return False


def _extract_code_from_url(url: str) -> str | None:
    """Extract the 'code' parameter from a redirect URL."""
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        codes = params.get("code", [])
        return codes[0] if codes else None
    except Exception:
        return None
