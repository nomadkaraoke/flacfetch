"""
Torrent management endpoints for flacfetch HTTP API.
"""
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import verify_api_key
from ..models import (
    CleanupRequest,
    CleanupResponse,
    TorrentDeleteResponse,
    TorrentInfo,
    TorrentListResponse,
    TorrentSummaryItem,
    TorrentSummaryResponse,
)
from ..services import get_disk_manager

logger = logging.getLogger(__name__)
router = APIRouter(tags=["torrents"])


def _get_transmission_client():
    """Get Transmission RPC client."""
    try:
        import transmission_rpc

        host = os.environ.get("TRANSMISSION_HOST", "localhost")
        port = int(os.environ.get("TRANSMISSION_PORT", "9091"))

        return transmission_rpc.Client(host=host, port=port, timeout=10)
    except ImportError:
        raise HTTPException(status_code=500, detail="transmission-rpc not installed")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Cannot connect to Transmission: {e}")


@router.get("/torrents/summary", response_model=TorrentSummaryResponse)
async def torrent_summary() -> TorrentSummaryResponse:
    """
    Get a summary of torrents in Transmission (public, no auth required).

    Shows basic info about each torrent for visibility into what's seeding.
    """
    client = _get_transmission_client()

    try:
        torrents = client.get_torrents()
    except Exception as e:
        logger.error(f"Failed to get torrent summary: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get torrent summary: {e}")

    torrent_list = []
    total_size = 0
    total_uploaded = 0
    seeding_count = 0
    downloading_count = 0

    for t in torrents:
        status_str = str(t.status) if hasattr(t, 'status') else "unknown"
        size = t.total_size if hasattr(t, 'total_size') else 0
        uploaded = t.uploaded_ever if hasattr(t, 'uploaded_ever') else 0

        total_size += size
        total_uploaded += uploaded

        if status_str in ['seeding', 'seed_pending']:
            seeding_count += 1
        elif status_str in ['downloading', 'download_pending']:
            downloading_count += 1

        torrent_list.append(TorrentSummaryItem(
            id=t.id,
            name=t.name,
            status=status_str,
            progress=t.progress if hasattr(t, 'progress') else 0,
            size_mb=round(size / (1024 * 1024), 2),
            ratio=t.ratio if hasattr(t, 'ratio') else 0,
        ))

    return TorrentSummaryResponse(
        count=len(torrent_list),
        seeding=seeding_count,
        downloading=downloading_count,
        total_size_mb=round(total_size / (1024 * 1024), 2),
        total_uploaded_mb=round(total_uploaded / (1024 * 1024), 2),
        torrents=torrent_list,
    )


@router.get("/torrents", response_model=TorrentListResponse)
async def list_torrents(
    api_key: str = Depends(verify_api_key),
) -> TorrentListResponse:
    """
    List all torrents in Transmission.

    Shows status, progress, size, and seeding stats for each torrent.
    """
    client = _get_transmission_client()

    try:
        torrents = client.get_torrents()
    except Exception as e:
        logger.error(f"Failed to list torrents: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list torrents: {e}")

    torrent_list = []
    total_size = 0

    for t in torrents:
        # Get status string
        status_str = str(t.status) if hasattr(t, 'status') else "unknown"

        # Get dates
        added_at = None
        done_at = None
        if hasattr(t, 'date_added') and t.date_added:
            added_at = t.date_added
        if hasattr(t, 'date_done') and t.date_done:
            done_at = t.date_done

        size = t.total_size if hasattr(t, 'total_size') else 0
        total_size += size

        torrent_list.append(TorrentInfo(
            id=t.id,
            name=t.name,
            status=status_str,
            progress=t.progress if hasattr(t, 'progress') else 0,
            size_bytes=size,
            downloaded_bytes=t.downloaded_ever if hasattr(t, 'downloaded_ever') else 0,
            uploaded_bytes=t.uploaded_ever if hasattr(t, 'uploaded_ever') else 0,
            ratio=t.ratio if hasattr(t, 'ratio') else 0,
            peers=t.peers_connected if hasattr(t, 'peers_connected') else 0,
            download_speed_kbps=(t.rate_download / 1024) if hasattr(t, 'rate_download') else 0,
            upload_speed_kbps=(t.rate_upload / 1024) if hasattr(t, 'rate_upload') else 0,
            added_at=added_at,
            done_at=done_at,
        ))

    return TorrentListResponse(
        torrents=torrent_list,
        total_size_bytes=total_size,
        count=len(torrent_list),
    )


@router.delete("/torrents/{torrent_id}", response_model=TorrentDeleteResponse)
async def delete_torrent(
    torrent_id: int,
    delete_data: bool = Query(True, description="Also delete downloaded files"),
    api_key: str = Depends(verify_api_key),
) -> TorrentDeleteResponse:
    """
    Delete a torrent from Transmission.

    By default, also deletes the downloaded files. Set delete_data=false to keep files.
    """
    client = _get_transmission_client()

    try:
        # Verify torrent exists
        torrent = client.get_torrent(torrent_id)
        name = torrent.name
    except Exception:
        raise HTTPException(status_code=404, detail=f"Torrent not found: {torrent_id}")

    try:
        client.remove_torrent(torrent_id, delete_data=delete_data)
        logger.info(f"Deleted torrent {torrent_id}: {name} (delete_data={delete_data})")
    except Exception as e:
        logger.error(f"Failed to delete torrent {torrent_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete torrent: {e}")

    return TorrentDeleteResponse(
        status="deleted",
        message=f"Deleted torrent: {name}" + (" (including files)" if delete_data else ""),
    )


@router.post("/torrents/cleanup", response_model=CleanupResponse)
async def cleanup_torrents(
    request: CleanupRequest,
    api_key: str = Depends(verify_api_key),
) -> CleanupResponse:
    """
    Clean up torrents to free disk space.

    Strategies:
    - oldest: Remove oldest torrents first
    - largest: Remove largest torrents first
    - lowest_ratio: Remove torrents with lowest upload ratio first
    """
    client = _get_transmission_client()
    disk_manager = get_disk_manager()

    strategy = request.strategy.lower()

    if strategy == "oldest":
        removed_count, freed_bytes = disk_manager.cleanup_oldest(
            client, target_free_gb=request.target_free_gb
        )
    elif strategy == "largest":
        removed_count, freed_bytes = disk_manager.cleanup_largest(
            client, target_free_gb=request.target_free_gb
        )
    elif strategy == "lowest_ratio":
        removed_count, freed_bytes = disk_manager.cleanup_lowest_ratio(
            client, target_free_gb=request.target_free_gb
        )
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown strategy: {strategy}. Valid: oldest, largest, lowest_ratio"
        )

    _, _, free_gb = disk_manager.get_disk_usage()

    return CleanupResponse(
        removed_count=removed_count,
        freed_bytes=freed_bytes,
        free_space_gb=free_gb,
    )

