"""Spotify token keeper - performs OAuth flow via 'Continue with Google' in the browser."""
import json
import logging
import os
from urllib.parse import parse_qs, urlparse

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

    return (
        "https://accounts.spotify.com/authorize"
        f"?client_id={client_id}"
        f"&response_type=code"
        f"&redirect_uri={redirect_uri}"
        f"&scope={scope.replace(' ', '%20')}"
    )


async def _exchange_code_for_token(code: str) -> dict:
    """Exchange an authorization code for access + refresh tokens."""
    client_id = os.environ.get("SPOTIPY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET")
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

    Returns True if token was successfully obtained and uploaded.
    """
    try:
        oauth_url = _build_oauth_url()
        redirect_uri = os.environ.get("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
        redirect_host = urlparse(redirect_uri).netloc  # "127.0.0.1:8888"

        logger.info("Starting Spotify OAuth flow...")
        await page.goto(oauth_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)

        # Check if Spotify auto-approved (already authorized + logged in via Google)
        current_url = page.url
        if redirect_host in current_url:
            # Already redirected - extract code
            code = _extract_code_from_url(current_url)
            if code:
                logger.info("Spotify auto-approved (already authorized)")
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
                await page.wait_for_timeout(3000)
                current_url = page.url
                if redirect_host in current_url:
                    code = _extract_code_from_url(current_url)
                    if code:
                        token_data = await _exchange_code_for_token(code)
                        return await _upload_token(token_data)

            logger.error(f"Could not find 'Continue with Google' button. Current URL: {page.url}")
            return False

        logger.info("Clicking 'Continue with Google'")
        await google_button.click()

        # Wait for the Google OAuth consent + redirect back to Spotify, then to our redirect_uri
        # The redirect to localhost will fail (nothing listening), but we capture the URL
        try:
            await page.wait_for_url(f"**{redirect_host}**", timeout=30000)
        except Exception:
            # page.wait_for_url may throw if navigation "fails" (localhost not reachable)
            # but the URL is still captured
            pass

        await page.wait_for_timeout(1000)
        current_url = page.url

        if redirect_host not in current_url:
            # Try checking if we ended up on a Spotify consent page
            agree_button = await page.query_selector('button[data-testid="auth-accept"]')
            if agree_button:
                logger.info("Clicking Spotify authorize/agree button after Google login")
                await agree_button.click()
                try:
                    await page.wait_for_url(f"**{redirect_host}**", timeout=15000)
                except Exception:
                    pass
                await page.wait_for_timeout(1000)
                current_url = page.url

        code = _extract_code_from_url(current_url)
        if not code:
            logger.error(f"No authorization code in redirect URL: {current_url}")
            return False

        logger.info("Got authorization code, exchanging for token...")
        token_data = await _exchange_code_for_token(code)
        return await _upload_token(token_data)

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
