"""
Download endpoints for flacfetch HTTP API.
"""
import logging
import os

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from ..auth import verify_api_key
from ..models import (
    DownloadRequest,
    DownloadStartResponse,
    DownloadStatus,
    DownloadStatusResponse,
)
from ..services import get_download_manager

logger = logging.getLogger(__name__)
router = APIRouter(tags=["download"])


@router.post("/download", response_model=DownloadStartResponse)
async def start_download(
    request: DownloadRequest,
    background_tasks: BackgroundTasks,
    api_key: str = Depends(verify_api_key),
) -> DownloadStartResponse:
    """
    Start downloading an audio file from a previous search result.

    The download runs in the background. Use GET /download/{download_id}/status
    to check progress.

    If upload_to_gcs is true, the file will be uploaded to GCS after download.
    """
    manager = get_download_manager()

    # Validate search exists
    search = manager.get_search(request.search_id)
    if not search:
        raise HTTPException(
            status_code=404,
            detail=f"Search not found or expired: {request.search_id}"
        )

    # Validate result index
    if request.result_index < 0 or request.result_index >= len(search.results):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid result index {request.result_index}. Valid range: 0-{len(search.results)-1}"
        )

    # Validate GCS params
    if request.upload_to_gcs and not request.gcs_path:
        raise HTTPException(
            status_code=400,
            detail="gcs_path is required when upload_to_gcs is true"
        )

    # Create download task
    task = manager.create_download(
        search_id=request.search_id,
        result_index=request.result_index,
        output_filename=request.output_filename,
        upload_to_gcs=request.upload_to_gcs,
        gcs_destination=request.gcs_path,
    )

    logger.info(f"Created download task: {task.download_id} for {task.provider}: {task.artist} - {task.title}")

    # Start download in background
    background_tasks.add_task(manager.execute_download, task.download_id)

    return DownloadStartResponse(
        download_id=task.download_id,
        status=DownloadStatus.QUEUED,
    )


@router.get("/download/{download_id}/status", response_model=DownloadStatusResponse)
async def get_download_status(
    download_id: str,
    api_key: str = Depends(verify_api_key),
) -> DownloadStatusResponse:
    """
    Get the status of a download.

    Poll this endpoint to track download progress.

    Status values:
    - queued: Waiting to start
    - downloading: Currently downloading
    - uploading: Uploading to GCS (if requested)
    - seeding: Download complete, torrent is seeding (for torrent sources)
    - complete: Download complete (for YouTube or if seeding disabled)
    - failed: Download failed (check error field)
    - cancelled: Download was cancelled
    """
    manager = get_download_manager()
    task = manager.get_download(download_id)

    if not task:
        raise HTTPException(status_code=404, detail=f"Download not found: {download_id}")

    # If downloading from torrent, try to get live progress from Transmission
    if task.status == DownloadStatus.DOWNLOADING and task.provider in ["Redacted", "OPS"]:
        try:
            progress_info = _get_transmission_progress(task.torrent_id)
            if progress_info:
                task.progress = progress_info.get("progress", task.progress)
                task.peers = progress_info.get("peers", task.peers)
                task.download_speed_kbps = progress_info.get("download_speed_kbps", task.download_speed_kbps)
                task.upload_speed_kbps = progress_info.get("upload_speed_kbps", task.upload_speed_kbps)
                task.eta_seconds = progress_info.get("eta_seconds")
        except Exception as e:
            logger.debug(f"Could not get Transmission progress: {e}")

    return DownloadStatusResponse(
        download_id=task.download_id,
        status=task.status,
        progress=task.progress,
        peers=task.peers,
        download_speed_kbps=task.download_speed_kbps,
        upload_speed_kbps=task.upload_speed_kbps,
        eta_seconds=task.eta_seconds,
        provider=task.provider,
        title=task.title,
        artist=task.artist,
        output_path=task.output_path,
        gcs_path=task.gcs_path,
        error=task.error,
        started_at=task.started_at,
    )


def _get_transmission_progress(torrent_id: int) -> dict:
    """Get progress info from Transmission for a specific torrent."""
    if not torrent_id:
        return {}

    try:
        import transmission_rpc

        host = os.environ.get("TRANSMISSION_HOST", "localhost")
        port = int(os.environ.get("TRANSMISSION_PORT", "9091"))

        client = transmission_rpc.Client(host=host, port=port, timeout=5)
        torrent = client.get_torrent(torrent_id)

        return {
            "progress": torrent.progress,
            "peers": torrent.peers_connected if hasattr(torrent, 'peers_connected') else 0,
            "download_speed_kbps": (torrent.rate_download / 1024) if hasattr(torrent, 'rate_download') else 0,
            "upload_speed_kbps": (torrent.rate_upload / 1024) if hasattr(torrent, 'rate_upload') else 0,
            "eta_seconds": torrent.eta.total_seconds() if hasattr(torrent, 'eta') and torrent.eta else None,
        }
    except Exception:
        return {}

