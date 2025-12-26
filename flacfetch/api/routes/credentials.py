"""
Credential health check endpoints for flacfetch HTTP API.
"""
import logging

from fastapi import APIRouter, Depends, Query

from ..auth import verify_api_key
from ..services.credential_check import (
    check_spotify_credentials,
    check_youtube_credentials,
    run_credential_health_check,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["credentials"])


@router.get("/credentials/check")
async def check_credentials(
    notify: bool = Query(False, description="Send Pushbullet notification"),
    notify_on_success: bool = Query(False, description="Notify even if all OK"),
    api_key: str = Depends(verify_api_key),
):
    """
    Check if all credentials (Spotify, YouTube) are working.

    Returns status for each service and whether human action is needed.
    Optionally sends Pushbullet notification.
    """
    return run_credential_health_check(
        notify=notify,
        notify_on_success=notify_on_success,
    )


@router.get("/credentials/spotify")
async def check_spotify(
    api_key: str = Depends(verify_api_key),
):
    """Check Spotify credentials specifically."""
    result = check_spotify_credentials()
    return {
        "service": result.service,
        "status": result.status.value,
        "message": result.message,
        "needs_human_action": result.needs_human_action,
        "fix_command": result.fix_command,
        "tested_at": result.tested_at.isoformat(),
    }


@router.get("/credentials/youtube")
async def check_youtube(
    api_key: str = Depends(verify_api_key),
):
    """Check YouTube credentials specifically."""
    result = check_youtube_credentials()
    return {
        "service": result.service,
        "status": result.status.value,
        "message": result.message,
        "needs_human_action": result.needs_human_action,
        "fix_command": result.fix_command,
        "tested_at": result.tested_at.isoformat(),
    }
