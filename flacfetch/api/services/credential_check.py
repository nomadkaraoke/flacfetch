"""
Credential health check service for flacfetch.

Validates that Spotify and YouTube credentials are working,
and sends Pushbullet notifications with status and fix instructions.
"""
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class CredentialStatus(str, Enum):
    OK = "ok"
    EXPIRED = "expired"
    REVOKED = "revoked"
    MISSING = "missing"
    ERROR = "error"


@dataclass
class CredentialCheckResult:
    """Result of a credential check."""
    service: str
    status: CredentialStatus
    message: str
    needs_human_action: bool = False
    fix_command: Optional[str] = None
    tested_at: datetime = None

    def __post_init__(self):
        if self.tested_at is None:
            self.tested_at = datetime.now(timezone.utc)


def check_spotify_credentials() -> CredentialCheckResult:
    """
    Test if Spotify OAuth credentials are working.

    Tries to make a simple search API call to verify the token is valid.
    """
    client_id = os.environ.get("SPOTIPY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET")

    if not client_id or not client_secret:
        return CredentialCheckResult(
            service="Spotify",
            status=CredentialStatus.MISSING,
            message="SPOTIPY_CLIENT_ID or SPOTIPY_CLIENT_SECRET not configured",
            needs_human_action=True,
            fix_command="Set SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET in GCP Secret Manager",
        )

    # Check if cache file exists
    cache_path = "/opt/flacfetch/.cache"
    if not os.path.exists(cache_path):
        return CredentialCheckResult(
            service="Spotify",
            status=CredentialStatus.MISSING,
            message="No OAuth token cached - needs browser authentication",
            needs_human_action=True,
            fix_command="flacfetch spotify-auth login && flacfetch spotify-auth upload",
        )

    try:
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth

        auth_manager = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=os.environ.get("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
            scope="user-read-playback-state user-modify-playback-state streaming",
            cache_path=cache_path,
        )

        sp = spotipy.Spotify(auth_manager=auth_manager)

        # Try to get current user - this validates the token
        user = sp.current_user()

        # Also try a search to make sure the API is fully working
        sp.search(q="test", type="track", limit=1)

        return CredentialCheckResult(
            service="Spotify",
            status=CredentialStatus.OK,
            message=f"Authenticated as {user.get('display_name', user.get('id'))}",
            needs_human_action=False,
        )

    except Exception as e:
        error_str = str(e).lower()

        if "invalid_grant" in error_str or "revoked" in error_str:
            return CredentialCheckResult(
                service="Spotify",
                status=CredentialStatus.REVOKED,
                message=f"OAuth token revoked: {e}",
                needs_human_action=True,
                fix_command="flacfetch spotify-auth login && flacfetch spotify-auth upload",
            )
        elif "expired" in error_str:
            return CredentialCheckResult(
                service="Spotify",
                status=CredentialStatus.EXPIRED,
                message=f"OAuth token expired and refresh failed: {e}",
                needs_human_action=True,
                fix_command="flacfetch spotify-auth login && flacfetch spotify-auth upload",
            )
        else:
            return CredentialCheckResult(
                service="Spotify",
                status=CredentialStatus.ERROR,
                message=f"Error checking credentials: {e}",
                needs_human_action=True,
                fix_command="flacfetch spotify-auth login && flacfetch spotify-auth upload",
            )


def check_youtube_credentials() -> CredentialCheckResult:
    """
    Test if YouTube cookies are working.

    Tries to extract info from a video that requires authentication.
    """
    cookies_file = os.environ.get("YOUTUBE_COOKIES_FILE", "/opt/flacfetch/youtube_cookies.txt")

    if not cookies_file or not os.path.exists(cookies_file):
        # YouTube can work without cookies for most videos
        return CredentialCheckResult(
            service="YouTube",
            status=CredentialStatus.MISSING,
            message="No cookies configured (may still work for non-restricted videos)",
            needs_human_action=False,  # Not critical
        )

    try:
        import yt_dlp

        # Try to extract info from a private video that requires authentication
        # This video is only accessible with valid cookies
        test_url = "https://www.youtube.com/watch?v=nFMXRiXnOXI"  # Private test video

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'cookiefile': cookies_file,
            'extract_flat': False,
            'skip_download': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(test_url, download=False)

        if info and info.get('title'):
            return CredentialCheckResult(
                service="YouTube",
                status=CredentialStatus.OK,
                message=f"Cookies valid - extracted info for: {info.get('title', 'unknown')[:50]}",
                needs_human_action=False,
            )
        else:
            return CredentialCheckResult(
                service="YouTube",
                status=CredentialStatus.ERROR,
                message="Could not extract video info",
                needs_human_action=False,
            )

    except Exception as e:
        error_str = str(e).lower()

        if "429" in error_str or "too many requests" in error_str or "rate" in error_str:
            # Rate limited by YouTube - cookies might still be valid
            return CredentialCheckResult(
                service="YouTube",
                status=CredentialStatus.OK,
                message="Rate limited by YouTube (429) - cookies assumed valid, try again later",
                needs_human_action=False,
            )
        elif "private" in error_str and "sign in" not in error_str and "login" not in error_str:
            # Video is private but no auth prompt - cookies might be for wrong account
            # Check if cookies file exists and has content
            if os.path.exists(cookies_file) and os.path.getsize(cookies_file) > 100:
                return CredentialCheckResult(
                    service="YouTube",
                    status=CredentialStatus.OK,
                    message="Cookies configured (test video inaccessible - may need different account)",
                    needs_human_action=False,
                )
        elif "sign in" in error_str or "login" in error_str or "cookies" in error_str:
            return CredentialCheckResult(
                service="YouTube",
                status=CredentialStatus.EXPIRED,
                message=f"Cookies expired or invalid: {e}",
                needs_human_action=True,
                fix_command="flacfetch cookies upload",
            )
        elif "age" in error_str or "restricted" in error_str:
            # Age-restricted content - cookies might still be valid
            return CredentialCheckResult(
                service="YouTube",
                status=CredentialStatus.OK,
                message="Cookies present (age-restricted test video)",
                needs_human_action=False,
            )
        else:
            # Other errors might be transient
            return CredentialCheckResult(
                service="YouTube",
                status=CredentialStatus.ERROR,
                message=f"Error checking: {e}",
                needs_human_action=False,
            )


# =============================================================================
# Local credential check functions (for CLI use on user's machine)
# =============================================================================

def get_local_spotify_cache_path() -> str:
    """Return the local Spotify cache path (for user's machine)."""
    return os.path.expanduser("~/.cache-spotipy")


def get_local_youtube_cookies_path() -> str:
    """Return the local YouTube cookies path (for user's machine)."""
    return os.path.expanduser("~/.flacfetch/youtube_cookies.txt")


def check_local_spotify_credentials() -> CredentialCheckResult:
    """
    Test if LOCAL Spotify OAuth credentials are working.

    Uses ~/.cache-spotipy for the token cache (local machine path).
    This is for CLI use on the user's machine, not the server.
    """
    client_id = os.environ.get("SPOTIPY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET")

    if not client_id or not client_secret:
        return CredentialCheckResult(
            service="Spotify",
            status=CredentialStatus.MISSING,
            message="SPOTIPY_CLIENT_ID or SPOTIPY_CLIENT_SECRET not configured",
            needs_human_action=True,
            fix_command="Set SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET environment variables",
        )

    # Check if local cache file exists
    cache_path = get_local_spotify_cache_path()
    if not os.path.exists(cache_path):
        return CredentialCheckResult(
            service="Spotify",
            status=CredentialStatus.MISSING,
            message="No OAuth token cached - needs browser authentication",
            needs_human_action=True,
            fix_command="flacfetch fix",
        )

    try:
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth

        auth_manager = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=os.environ.get("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
            scope="user-read-playback-state user-modify-playback-state streaming",
            cache_path=cache_path,
        )

        sp = spotipy.Spotify(auth_manager=auth_manager)

        # Try to get current user - this validates the token
        user = sp.current_user()

        # Also try a search to make sure the API is fully working
        sp.search(q="test", type="track", limit=1)

        return CredentialCheckResult(
            service="Spotify",
            status=CredentialStatus.OK,
            message=f"Authenticated as {user.get('display_name', user.get('id'))}",
            needs_human_action=False,
        )

    except Exception as e:
        error_str = str(e).lower()

        if "invalid_grant" in error_str or "revoked" in error_str:
            return CredentialCheckResult(
                service="Spotify",
                status=CredentialStatus.REVOKED,
                message=f"OAuth token revoked: {e}",
                needs_human_action=True,
                fix_command="flacfetch fix",
            )
        elif "expired" in error_str:
            return CredentialCheckResult(
                service="Spotify",
                status=CredentialStatus.EXPIRED,
                message=f"OAuth token expired and refresh failed: {e}",
                needs_human_action=True,
                fix_command="flacfetch fix",
            )
        else:
            return CredentialCheckResult(
                service="Spotify",
                status=CredentialStatus.ERROR,
                message=f"Error checking credentials: {e}",
                needs_human_action=True,
                fix_command="flacfetch fix",
            )


def check_local_youtube_credentials() -> CredentialCheckResult:
    """
    Test if LOCAL YouTube cookies are configured.

    Uses ~/.flacfetch/youtube_cookies.txt for the cookies file (local machine path).
    This is for CLI use on the user's machine, not the server.

    Note: We don't test the cookies with a private video here because
    that requires network access and can be slow. We just check if the file exists.
    """
    cookies_file = get_local_youtube_cookies_path()

    if not os.path.exists(cookies_file):
        return CredentialCheckResult(
            service="YouTube",
            status=CredentialStatus.MISSING,
            message="No cookies configured (may still work for non-restricted videos)",
            needs_human_action=False,  # Not critical
        )

    # Check file size to ensure it's not empty
    try:
        file_size = os.path.getsize(cookies_file)
        if file_size < 100:  # Too small to be valid cookies
            return CredentialCheckResult(
                service="YouTube",
                status=CredentialStatus.ERROR,
                message=f"Cookies file too small ({file_size} bytes) - may be invalid",
                needs_human_action=True,
                fix_command="flacfetch fix",
            )

        return CredentialCheckResult(
            service="YouTube",
            status=CredentialStatus.OK,
            message=f"Cookies file present ({file_size} bytes)",
            needs_human_action=False,
        )

    except Exception as e:
        return CredentialCheckResult(
            service="YouTube",
            status=CredentialStatus.ERROR,
            message=f"Error checking cookies: {e}",
            needs_human_action=True,
            fix_command="flacfetch fix",
        )


def send_pushbullet_notification(
    title: str,
    body: str,
    api_key: Optional[str] = None,
) -> bool:
    """
    Send a Pushbullet notification.

    Args:
        title: Notification title
        body: Notification body
        api_key: Pushbullet API key (or from PUSHBULLET_API_KEY env var)

    Returns:
        True if notification was sent successfully
    """
    api_key = api_key or os.environ.get("PUSHBULLET_API_KEY")

    if not api_key:
        logger.warning("PUSHBULLET_API_KEY not configured, skipping notification")
        return False

    try:
        response = httpx.post(
            "https://api.pushbullet.com/v2/pushes",
            headers={
                "Access-Token": api_key,
                "Content-Type": "application/json",
            },
            json={
                "type": "note",
                "title": title,
                "body": body,
            },
            timeout=30,
        )

        if response.status_code == 200:
            logger.info(f"Pushbullet notification sent: {title}")
            return True
        else:
            logger.error(f"Pushbullet error: {response.status_code} - {response.text}")
            return False

    except Exception as e:
        logger.error(f"Failed to send Pushbullet notification: {e}")
        return False


def run_credential_health_check(
    notify: bool = True,
    notify_on_success: bool = False,
) -> dict:
    """
    Run a full credential health check and optionally send notifications.

    Args:
        notify: Whether to send Pushbullet notifications
        notify_on_success: Whether to notify even if everything is OK

    Returns:
        Dictionary with check results
    """
    logger.info("Starting credential health check...")

    results = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "services": {},
        "needs_action": False,
        "fix_commands": [],
    }

    # Check Spotify
    spotify_result = check_spotify_credentials()
    results["services"]["spotify"] = {
        "status": spotify_result.status.value,
        "message": spotify_result.message,
        "needs_human_action": spotify_result.needs_human_action,
        "fix_command": spotify_result.fix_command,
    }
    if spotify_result.needs_human_action:
        results["needs_action"] = True
        if spotify_result.fix_command:
            results["fix_commands"].append(f"Spotify: {spotify_result.fix_command}")

    # Check YouTube
    youtube_result = check_youtube_credentials()
    results["services"]["youtube"] = {
        "status": youtube_result.status.value,
        "message": youtube_result.message,
        "needs_human_action": youtube_result.needs_human_action,
        "fix_command": youtube_result.fix_command,
    }
    if youtube_result.needs_human_action:
        results["needs_action"] = True
        if youtube_result.fix_command:
            results["fix_commands"].append(f"YouTube: {youtube_result.fix_command}")

    # Send notification if needed
    if notify:
        if results["needs_action"]:
            # Something needs fixing
            title = "⚠️ Flacfetch: Credentials Need Attention"
            body_lines = ["One or more services need re-authentication:\n"]

            if spotify_result.needs_human_action:
                body_lines.append(f"❌ Spotify: {spotify_result.message}")
                body_lines.append(f"   Fix: {spotify_result.fix_command}\n")

            if youtube_result.needs_human_action:
                body_lines.append(f"❌ YouTube: {youtube_result.message}")
                body_lines.append(f"   Fix: {youtube_result.fix_command}\n")

            body_lines.append("\nRun 'flacfetch fix' for guided repair.")

            send_pushbullet_notification(title, "\n".join(body_lines))

        elif notify_on_success:
            # Everything is OK
            title = "✅ Flacfetch: All Credentials OK"
            body = f"Spotify: {spotify_result.message}\nYouTube: {youtube_result.message}"
            send_pushbullet_notification(title, body)

    logger.info(f"Credential check complete. Needs action: {results['needs_action']}")
    return results
