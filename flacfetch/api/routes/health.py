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
    TorrentSummaryItem,
    TransmissionHealth,
    YtdlpHealth,
)
from ..services import get_disk_manager, get_server_started_at

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

    # Check yt-dlp status
    ytdlp = _check_ytdlp()

    # Overall status
    status = "healthy"
    if not transmission.available:
        status = "degraded"
    if free_gb < 1:
        status = "unhealthy"

    return HealthResponse(
        status=status,
        version=get_version(),
        started_at=get_server_started_at(),
        transmission=transmission,
        disk=disk,
        providers=providers,
        ytdlp=ytdlp,
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

        # Build summary list
        torrent_list = []
        seeding_count = 0
        total_size = 0
        total_uploaded = 0

        for t in torrents:
            status_str = str(t.status) if hasattr(t, 'status') else "unknown"
            size = t.total_size if hasattr(t, 'total_size') else 0
            uploaded = t.uploaded_ever if hasattr(t, 'uploaded_ever') else 0

            total_size += size
            total_uploaded += uploaded

            if status_str in ['seeding', 'seed_pending']:
                seeding_count += 1

            torrent_list.append(TorrentSummaryItem(
                id=t.id,
                name=t.name,
                status=status_str,
                progress=t.progress if hasattr(t, 'progress') else 0,
                size_mb=round(size / (1024 * 1024), 2),
                ratio=t.ratio if hasattr(t, 'ratio') else 0,
            ))

        return TransmissionHealth(
            available=True,
            version=session.version if hasattr(session, 'version') else None,
            active_torrents=len(torrents),
            seeding_torrents=seeding_count,
            total_size_mb=round(total_size / (1024 * 1024), 2),
            total_uploaded_mb=round(total_uploaded / (1024 * 1024), 2),
            torrents=torrent_list,
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
    # Spotify requires client ID and secret
    spotify = bool(os.environ.get("SPOTIPY_CLIENT_ID")) and bool(os.environ.get("SPOTIPY_CLIENT_SECRET"))

    # YouTube is always available
    youtube = True

    return ProvidersHealth(
        red=red,
        ops=ops,
        spotify=spotify,
        youtube=youtube,
    )


def _check_ytdlp() -> YtdlpHealth:
    """Check yt-dlp and EJS status for YouTube downloads."""
    import shutil
    import subprocess

    result = YtdlpHealth()

    try:
        # Get yt-dlp version
        import yt_dlp
        result.version = yt_dlp.version.__version__
    except ImportError:
        result.error = "yt-dlp not installed"
        return result
    except Exception as e:
        result.error = f"Failed to get yt-dlp version: {e}"

    # Check for yt-dlp-ejs
    try:
        import yt_dlp_ejs
        result.ejs_installed = True
        if hasattr(yt_dlp_ejs, "__version__"):
            result.ejs_version = yt_dlp_ejs.__version__
        else:
            result.ejs_version = "installed"
    except ImportError:
        result.ejs_installed = False

    # Check for Deno runtime
    deno_paths = [
        shutil.which("deno"),
        "/root/.deno/bin/deno",
        os.path.expanduser("~/.deno/bin/deno"),
    ]

    for deno_path in deno_paths:
        if deno_path and os.path.exists(deno_path):
            try:
                deno_result = subprocess.run(
                    [deno_path, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if deno_result.returncode == 0:
                    result.deno_available = True
                    # Parse version from "deno x.y.z"
                    version_line = deno_result.stdout.strip().split("\n")[0]
                    if version_line.startswith("deno "):
                        result.deno_version = version_line.split()[1]
                    else:
                        result.deno_version = version_line
                    break
            except Exception:
                pass

    # Check if YouTube cookies are configured
    cookies_file = os.environ.get("YOUTUBE_COOKIES_FILE")
    result.cookies_configured = bool(cookies_file and os.path.exists(cookies_file))

    return result


@router.get("/debug/providers")
async def debug_providers():
    """
    Debug endpoint to check actual provider initialization and connectivity.

    Returns detailed information about each provider's status.
    """
    from ..services import get_download_manager

    result = {
        "env_vars": {
            "RED_API_KEY": bool(os.environ.get("RED_API_KEY")),
            "RED_API_URL": bool(os.environ.get("RED_API_URL")),
            "OPS_API_KEY": bool(os.environ.get("OPS_API_KEY")),
            "OPS_API_URL": bool(os.environ.get("OPS_API_URL")),
            "SPOTIPY_CLIENT_ID": bool(os.environ.get("SPOTIPY_CLIENT_ID")),
            "SPOTIPY_CLIENT_SECRET": bool(os.environ.get("SPOTIPY_CLIENT_SECRET")),
        },
        "providers": {},
        "errors": [],
    }

    # Try to get the download manager and fetch manager
    try:
        manager = get_download_manager()
        fetch_manager = manager._get_fetch_manager()

        # List registered providers
        for provider in fetch_manager.providers:
            provider_info = {
                "name": provider.name,
                "initialized": True,
            }

            # Try a simple API test for RED/OPS
            if provider.name in ["RED", "OPS"]:
                try:
                    # Test by checking if the base_url is set
                    base_url = getattr(provider, 'base_url', None)
                    provider_info["base_url_set"] = bool(base_url)

                    # Try a simple API call (index action just checks auth)
                    if hasattr(provider, 'session'):
                        test_url = f"{base_url}/ajax.php?action=index"
                        resp = provider.session.get(test_url, timeout=5)
                        provider_info["api_test_status"] = resp.status_code
                        if resp.status_code == 200:
                            try:
                                data = resp.json()
                                provider_info["api_test_success"] = data.get("status") == "success"
                                if data.get("status") != "success":
                                    provider_info["api_test_error"] = data.get("error", "Unknown error")
                            except Exception as e:
                                provider_info["api_test_error"] = f"JSON parse error: {e}"
                        else:
                            provider_info["api_test_error"] = f"HTTP {resp.status_code}"
                except Exception as e:
                    provider_info["api_test_error"] = str(e)

            result["providers"][provider.name] = provider_info

        # Check which providers are registered
        result["registered_providers"] = [p.name for p in fetch_manager.providers]
        result["provider_priority"] = fetch_manager._provider_priority

    except Exception as e:
        result["errors"].append(f"Failed to get fetch manager: {e}")

    return result
