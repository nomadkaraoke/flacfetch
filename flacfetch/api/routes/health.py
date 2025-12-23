"""
Health check endpoints for flacfetch HTTP API.
"""
import logging
import os
from importlib.metadata import PackageNotFoundError, version

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


def get_version() -> str:
    """Get package version from installed metadata."""
    try:
        return version("flacfetch")
    except PackageNotFoundError:
        return "0.0.0-dev"


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
        version=get_version(),
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
    # RED requires both API key and URL
    red = bool(os.environ.get("RED_API_KEY")) and bool(os.environ.get("RED_API_URL"))
    # OPS requires both API key and URL
    ops = bool(os.environ.get("OPS_API_KEY")) and bool(os.environ.get("OPS_API_URL"))

    # YouTube is always available
    youtube = True

    return ProvidersHealth(
        red=red,
        ops=ops,
        youtube=youtube,
    )
