"""
Configuration endpoints for flacfetch HTTP API.

Handles runtime configuration like YouTube cookies and Spotify token upload.
"""
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...core.config import get_spotify_cache_path, get_youtube_cookies_path
from ..auth import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/config", tags=["config"])


# =============================================================================
# Models
# =============================================================================


class CookiesUploadRequest(BaseModel):
    """Request to upload YouTube cookies."""

    cookies: str = Field(..., description="Cookies in Netscape format (cookies.txt content)")


class CookiesUploadResponse(BaseModel):
    """Response after uploading cookies."""

    success: bool
    message: str
    updated_at: Optional[datetime] = None


class CookiesStatusResponse(BaseModel):
    """Status of YouTube cookies configuration."""

    configured: bool
    source: Optional[str] = None  # "file", "secret", or None
    file_path: Optional[str] = None
    last_updated: Optional[datetime] = None
    cookies_valid: bool = False
    validation_message: Optional[str] = None


# =============================================================================
# Helper Functions
# =============================================================================


def _get_cookies_file_path() -> str:
    """Get the path to the YouTube cookies file."""
    return get_youtube_cookies_path()


def _validate_cookies_format(cookies_content: str) -> tuple[bool, str]:
    """
    Validate that cookies are in Netscape format.

    Returns:
        Tuple of (is_valid, message)
    """
    lines = cookies_content.strip().split("\n")

    if not lines:
        return False, "Empty cookies content"

    # Count valid cookie lines (should have 7 tab-separated fields)
    valid_cookie_lines = 0
    youtube_cookies = 0

    for line in lines:
        line = line.strip()
        # Skip comments and empty lines
        if not line or line.startswith("#"):
            continue

        parts = line.split("\t")
        if len(parts) == 7:
            valid_cookie_lines += 1
            # Check if it's a YouTube/Google cookie
            domain = parts[0].lower()
            if "youtube" in domain or "google" in domain:
                youtube_cookies += 1

    if valid_cookie_lines == 0:
        return False, "No valid cookie lines found. Expected Netscape format with 7 tab-separated fields."

    if youtube_cookies == 0:
        return False, f"Found {valid_cookie_lines} cookies but none for YouTube/Google domains."

    return True, f"Valid: {valid_cookie_lines} cookies ({youtube_cookies} YouTube/Google)"


def _update_secret(cookies_content: str) -> bool:
    """
    Update the youtube-cookies secret in GCP Secret Manager.

    Returns True if successful, False otherwise.
    """
    try:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()

        # Get project ID
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT")
        if not project_id:
            # Try to get from metadata server
            import requests

            try:
                response = requests.get(
                    "http://metadata.google.internal/computeMetadata/v1/project/project-id",
                    headers={"Metadata-Flavor": "Google"},
                    timeout=2,
                )
                project_id = response.text
            except Exception:
                logger.error("Could not determine GCP project ID")
                return False

        secret_name = f"projects/{project_id}/secrets/youtube-cookies"

        # Add new version
        response = client.add_secret_version(
            request={
                "parent": secret_name,
                "payload": {"data": cookies_content.encode("utf-8")},
            }
        )

        logger.info(f"Updated youtube-cookies secret: {response.name}")
        return True

    except ImportError:
        logger.error("google-cloud-secret-manager not installed")
        return False
    except Exception as e:
        logger.error(f"Failed to update secret: {e}")
        return False


def _write_cookies_file(cookies_content: str, file_path: str) -> bool:
    """
    Write cookies to a local file.

    Returns True if successful, False otherwise.
    """
    temp_path = None
    try:
        # Write to temp file first, then move atomically
        dir_path = os.path.dirname(file_path)
        os.makedirs(dir_path, exist_ok=True)

        with tempfile.NamedTemporaryFile(mode="w", dir=dir_path, delete=False) as f:
            f.write(cookies_content)
            temp_path = f.name

        os.chmod(temp_path, 0o600)
        os.rename(temp_path, file_path)

        logger.info(f"Wrote cookies to {file_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to write cookies file: {e}")
        # Clean up temp file if rename failed
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        return False


# =============================================================================
# Endpoints
# =============================================================================


@router.post("/youtube-cookies", response_model=CookiesUploadResponse)
async def upload_youtube_cookies(
    request: CookiesUploadRequest,
    api_key: str = Depends(verify_api_key),
) -> CookiesUploadResponse:
    """
    Upload YouTube cookies for authenticated downloads.

    Cookies should be in Netscape format (as exported by browser extensions
    or `yt-dlp --cookies-from-browser`).

    The cookies are stored in GCP Secret Manager and written to a local file
    for immediate use.
    """
    # Validate cookies format
    is_valid, message = _validate_cookies_format(request.cookies)
    if not is_valid:
        raise HTTPException(status_code=400, detail=message)

    # Try to update GCP Secret Manager
    secret_updated = _update_secret(request.cookies)
    if not secret_updated:
        logger.warning("Could not update GCP secret, writing to local file only")

    # Write to local file for immediate use
    file_path = _get_cookies_file_path()
    file_written = _write_cookies_file(request.cookies, file_path)

    if not file_written and not secret_updated:
        raise HTTPException(
            status_code=500, detail="Failed to store cookies (both secret and file write failed)"
        )

    # Update environment variable so yt-dlp picks it up
    if file_written:
        os.environ["YOUTUBE_COOKIES_FILE"] = file_path

    result_message = message
    if secret_updated:
        result_message += " Stored in GCP Secret Manager."
    if file_written:
        result_message += f" Written to {file_path}."

    return CookiesUploadResponse(
        success=True,
        message=result_message,
        updated_at=datetime.now(timezone.utc),
    )


@router.get("/youtube-cookies/status", response_model=CookiesStatusResponse)
async def get_youtube_cookies_status(
    api_key: str = Depends(verify_api_key),
) -> CookiesStatusResponse:
    """
    Check the status of YouTube cookies configuration.

    Returns whether cookies are configured and validates their format.
    """
    file_path = _get_cookies_file_path()

    # Check if file exists
    if file_path and os.path.exists(file_path):
        try:
            with open(file_path) as f:
                content = f.read()

            stat = os.stat(file_path)
            last_updated = datetime.fromtimestamp(stat.st_mtime)

            is_valid, message = _validate_cookies_format(content)

            return CookiesStatusResponse(
                configured=True,
                source="file",
                file_path=file_path,
                last_updated=last_updated,
                cookies_valid=is_valid,
                validation_message=message,
            )
        except Exception as e:
            return CookiesStatusResponse(
                configured=True,
                source="file",
                file_path=file_path,
                cookies_valid=False,
                validation_message=f"Error reading cookies file: {e}",
            )

    return CookiesStatusResponse(
        configured=False,
        validation_message="No YouTube cookies configured",
    )


@router.delete("/youtube-cookies", response_model=CookiesUploadResponse)
async def delete_youtube_cookies(
    api_key: str = Depends(verify_api_key),
) -> CookiesUploadResponse:
    """
    Delete stored YouTube cookies.

    Removes the local cookies file. The GCP secret version will remain
    but won't be loaded on next service restart.
    """
    file_path = _get_cookies_file_path()
    deleted = False

    # Remove local file
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
            logger.info(f"Deleted cookies file: {file_path}")
            deleted = True
        except Exception as e:
            logger.error(f"Failed to delete cookies file: {e}")

    # Clear environment variable
    if "YOUTUBE_COOKIES_FILE" in os.environ:
        del os.environ["YOUTUBE_COOKIES_FILE"]

    if deleted:
        return CookiesUploadResponse(
            success=True,
            message="YouTube cookies deleted. Note: GCP secret version still exists but won't be loaded.",
            updated_at=datetime.now(timezone.utc),
        )
    else:
        return CookiesUploadResponse(
            success=True,
            message="No cookies file found to delete.",
            updated_at=datetime.now(timezone.utc),
        )


# =============================================================================
# Spotify Token Models
# =============================================================================


class SpotifyTokenUploadRequest(BaseModel):
    """Request to upload Spotify OAuth token."""

    token: str = Field(..., description="Spotify OAuth token JSON (content of .cache file)")


class SpotifyTokenUploadResponse(BaseModel):
    """Response after uploading Spotify token."""

    success: bool
    message: str
    updated_at: Optional[datetime] = None


class SpotifyTokenStatusResponse(BaseModel):
    """Status of Spotify OAuth token configuration."""

    configured: bool
    file_path: Optional[str] = None
    last_updated: Optional[datetime] = None
    token_valid: bool = False
    expires_at: Optional[datetime] = None
    validation_message: Optional[str] = None


# =============================================================================
# Spotify Token Helper Functions
# =============================================================================


def _validate_spotify_token_format(token_content: str) -> tuple[bool, str, Optional[dict]]:
    """
    Validate that the token is a valid Spotify OAuth token JSON.

    Returns:
        Tuple of (is_valid, message, parsed_token)
    """
    try:
        token = json.loads(token_content)
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON: {e}", None

    # Check required fields
    required_fields = ["access_token", "token_type", "refresh_token"]
    missing = [f for f in required_fields if f not in token]

    if missing:
        return False, f"Missing required fields: {', '.join(missing)}", None

    # Check token type
    if token.get("token_type", "").lower() != "bearer":
        return False, f"Invalid token_type: expected 'Bearer', got '{token.get('token_type')}'", None

    # Check scope (should include streaming scope)
    scope = token.get("scope", "")
    expected_scopes = ["streaming", "user-read-playback-state", "user-modify-playback-state"]
    has_required_scope = any(s in scope for s in expected_scopes)

    if not has_required_scope:
        return False, f"Token missing required scopes. Has: {scope}", None

    return True, "Valid Spotify OAuth token", token


def _update_spotify_secret(token_content: str) -> bool:
    """
    Update the spotify-oauth-token secret in GCP Secret Manager.

    Returns True if successful, False otherwise.
    """
    try:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()

        # Get project ID
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT")
        if not project_id:
            # Try to get from metadata server
            import requests

            try:
                response = requests.get(
                    "http://metadata.google.internal/computeMetadata/v1/project/project-id",
                    headers={"Metadata-Flavor": "Google"},
                    timeout=2,
                )
                project_id = response.text
            except Exception:
                logger.error("Could not determine GCP project ID")
                return False

        secret_name = f"projects/{project_id}/secrets/spotify-oauth-token"

        # Add new version
        response = client.add_secret_version(
            request={
                "parent": secret_name,
                "payload": {"data": token_content.encode("utf-8")},
            }
        )

        logger.info(f"Updated spotify-oauth-token secret: {response.name}")
        return True

    except ImportError:
        logger.error("google-cloud-secret-manager not installed")
        return False
    except Exception as e:
        logger.error(f"Failed to update secret: {e}")
        return False


def _write_spotify_token_file(token_content: str, file_path: str) -> bool:
    """
    Write Spotify token to a local file.

    Returns True if successful, False otherwise.
    """
    temp_path = None
    try:
        # Write to temp file first, then move atomically
        dir_path = os.path.dirname(file_path)
        os.makedirs(dir_path, exist_ok=True)

        with tempfile.NamedTemporaryFile(mode="w", dir=dir_path, delete=False) as f:
            f.write(token_content)
            temp_path = f.name

        os.chmod(temp_path, 0o600)
        os.rename(temp_path, file_path)

        logger.info(f"Wrote Spotify token to {file_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to write Spotify token file: {e}")
        # Clean up temp file if rename failed
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        return False


def _invalidate_spotify_provider() -> bool:
    """
    Invalidate the SpotifyProvider in DownloadManager to force credential reload.

    Returns True if successful, False if provider not found or error.
    """
    try:
        from ..services.download_manager import get_download_manager

        dm = get_download_manager()
        dm.invalidate_provider("Spotify")
        logger.info("SpotifyProvider invalidated for hot-reload")
        return True
    except Exception as e:
        logger.warning(f"Could not invalidate SpotifyProvider: {e}")
        return False


# =============================================================================
# Spotify Token Endpoints
# =============================================================================


@router.post("/spotify-token", response_model=SpotifyTokenUploadResponse)
async def upload_spotify_token(
    request: SpotifyTokenUploadRequest,
    api_key: str = Depends(verify_api_key),
) -> SpotifyTokenUploadResponse:
    """
    Upload Spotify OAuth token for authenticated downloads.

    The token should be the JSON content of the spotipy cache file
    (typically ~/.cache-spotipy or ~/.cache).

    The token is stored in GCP Secret Manager for persistence and written
    to a local file for immediate use. The SpotifyProvider is automatically
    invalidated to pick up the new credentials without server restart.
    """
    # Validate token format
    is_valid, message, _ = _validate_spotify_token_format(request.token)
    if not is_valid:
        raise HTTPException(status_code=400, detail=message)

    # Try to update GCP Secret Manager
    secret_updated = _update_spotify_secret(request.token)
    if not secret_updated:
        logger.warning("Could not update GCP secret, writing to local file only")

    # Write to local file for immediate use
    file_path = get_spotify_cache_path()
    file_written = _write_spotify_token_file(request.token, file_path)

    if not file_written and not secret_updated:
        raise HTTPException(
            status_code=500, detail="Failed to store token (both secret and file write failed)"
        )

    # Invalidate SpotifyProvider to pick up new credentials (hot-reload)
    provider_invalidated = _invalidate_spotify_provider()

    result_message = message
    if secret_updated:
        result_message += " Stored in GCP Secret Manager."
    if file_written:
        result_message += f" Written to {file_path}."
    if provider_invalidated:
        result_message += " Provider reloaded (immediate effect)."

    return SpotifyTokenUploadResponse(
        success=True,
        message=result_message,
        updated_at=datetime.now(timezone.utc),
    )


@router.get("/spotify-token/status", response_model=SpotifyTokenStatusResponse)
async def get_spotify_token_status(
    api_key: str = Depends(verify_api_key),
) -> SpotifyTokenStatusResponse:
    """
    Check the status of Spotify OAuth token configuration.

    Returns whether token is configured, valid, and when it expires.
    """
    file_path = get_spotify_cache_path()

    # Check if file exists
    if file_path and os.path.exists(file_path):
        try:
            with open(file_path) as f:
                content = f.read()

            stat = os.stat(file_path)
            last_updated = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

            is_valid, message, token = _validate_spotify_token_format(content)

            # Get expiration time if available
            expires_at = None
            if token and "expires_at" in token:
                try:
                    expires_at = datetime.fromtimestamp(token["expires_at"], tz=timezone.utc)
                except (ValueError, TypeError):
                    pass

            return SpotifyTokenStatusResponse(
                configured=True,
                file_path=file_path,
                last_updated=last_updated,
                token_valid=is_valid,
                expires_at=expires_at,
                validation_message=message,
            )
        except Exception as e:
            return SpotifyTokenStatusResponse(
                configured=True,
                file_path=file_path,
                token_valid=False,
                validation_message=f"Error reading token file: {e}",
            )

    return SpotifyTokenStatusResponse(
        configured=False,
        validation_message="No Spotify OAuth token configured",
    )


@router.delete("/spotify-token", response_model=SpotifyTokenUploadResponse)
async def delete_spotify_token(
    api_key: str = Depends(verify_api_key),
) -> SpotifyTokenUploadResponse:
    """
    Delete stored Spotify OAuth token.

    Removes the local token file and invalidates the SpotifyProvider.
    The GCP secret version will remain but won't be loaded on next service restart.
    """
    file_path = get_spotify_cache_path()
    deleted = False

    # Remove local file
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
            logger.info(f"Deleted Spotify token file: {file_path}")
            deleted = True
        except Exception as e:
            logger.error(f"Failed to delete Spotify token file: {e}")

    # Invalidate provider
    _invalidate_spotify_provider()

    if deleted:
        return SpotifyTokenUploadResponse(
            success=True,
            message="Spotify token deleted. Note: GCP secret version still exists but won't be loaded.",
            updated_at=datetime.now(timezone.utc),
        )
    else:
        return SpotifyTokenUploadResponse(
            success=True,
            message="No token file found to delete.",
            updated_at=datetime.now(timezone.utc),
        )

