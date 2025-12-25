"""
Configuration endpoints for flacfetch HTTP API.

Handles runtime configuration like YouTube cookies upload.
"""
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

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
    return os.environ.get("YOUTUBE_COOKIES_FILE", "/opt/flacfetch/youtube_cookies.txt")


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

