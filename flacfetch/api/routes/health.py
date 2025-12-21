"""
Health check endpoints for flacfetch HTTP API.
"""
import logging
import os

from fastapi import APIRouter

from ..models import (
    DiskHealth,
    HealthResponse,
    ProvidersHealth,
    TransmissionHealth,
)
from ..services import get_disk_manager

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])

# Version - should match pyproject.toml
VERSION = "0.5.1"


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """
    Check health of flacfetch service.

    Returns status of:
    - Transmission daemon
    - Disk space
    - Available providers
    """
    # Check Transmission
    transmission = _check_transmission()

    # Check disk space
    disk_manager = get_disk_manager()
    total_gb, used_gb, free_gb = disk_manager.get_disk_usage()
    disk = DiskHealth(
        total_gb=round(total_gb, 2),
        used_gb=round(used_gb, 2),
        free_gb=round(free_gb, 2),
    )

    # Check providers
    providers = _check_providers()

    # Overall status
    status = "healthy"
    if not transmission.available:
        status = "degraded"
    if free_gb < 1:
        status = "unhealthy"

    return HealthResponse(
        status=status,
        version=VERSION,
        transmission=transmission,
        disk=disk,
        providers=providers,
    )


def _check_transmission() -> TransmissionHealth:
    """Check Transmission daemon status."""
    try:
        import transmission_rpc

        host = os.environ.get("TRANSMISSION_HOST", "localhost")
        port = int(os.environ.get("TRANSMISSION_PORT", "9091"))

        client = transmission_rpc.Client(host=host, port=port, timeout=5)
        session = client.get_session()
        torrents = client.get_torrents()

        return TransmissionHealth(
            available=True,
            version=session.version if hasattr(session, 'version') else None,
            active_torrents=len(torrents),
        )
    except ImportError:
        return TransmissionHealth(
            available=False,
            error="transmission-rpc not installed",
        )
    except Exception as e:
        return TransmissionHealth(
            available=False,
            error=str(e),
        )


def _check_providers() -> ProvidersHealth:
    """Check which providers are configured."""
    redacted = bool(os.environ.get("REDACTED_API_KEY"))
    ops = bool(os.environ.get("OPS_API_KEY"))

    # YouTube is always available
    youtube = True

    return ProvidersHealth(
        redacted=redacted,
        ops=ops,
        youtube=youtube,
    )

