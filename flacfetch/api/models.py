"""
Pydantic models for flacfetch HTTP API requests and responses.
"""
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# =============================================================================
# Search Models
# =============================================================================

class SearchRequest(BaseModel):
    """Request to search for audio."""
    artist: str = Field(..., description="Artist name to search for")
    title: str = Field(..., description="Track title to search for")


class SearchResultItem(BaseModel):
    """A single search result."""
    index: int
    title: str
    artist: str
    provider: str  # source_name
    quality: str
    quality_data: Optional[Dict[str, Any]] = None
    seeders: Optional[int] = None
    size_bytes: Optional[int] = None
    target_file: Optional[str] = None
    target_file_size: Optional[int] = None
    year: Optional[int] = None
    label: Optional[str] = None
    edition_info: Optional[str] = None
    release_type: Optional[str] = None
    channel: Optional[str] = None  # YouTube
    view_count: Optional[int] = None  # YouTube
    duration_seconds: Optional[int] = None
    match_score: float = 0.0
    formatted_size: Optional[str] = None
    formatted_duration: Optional[str] = None
    is_lossless: bool = False
    download_url: Optional[str] = None  # For internal use, not exposed


class SearchResponse(BaseModel):
    """Response from search endpoint."""
    search_id: str
    artist: str
    title: str
    results: List[SearchResultItem]
    results_count: int


# =============================================================================
# Download Models
# =============================================================================

class DownloadRequest(BaseModel):
    """Request to start a download."""
    search_id: str = Field(..., description="Search ID from previous search")
    result_index: int = Field(..., description="Index of result to download")
    output_filename: Optional[str] = Field(None, description="Custom output filename (without extension)")
    upload_to_gcs: bool = Field(False, description="Upload to GCS when complete")
    gcs_path: Optional[str] = Field(None, description="GCS path (required if upload_to_gcs)")


class DownloadStatus(str, Enum):
    """Status of a download."""
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    UPLOADING = "uploading"  # Uploading to GCS
    SEEDING = "seeding"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DownloadStartResponse(BaseModel):
    """Response when starting a download."""
    download_id: str
    status: DownloadStatus


class DownloadStatusResponse(BaseModel):
    """Response for download status check."""
    download_id: str
    status: DownloadStatus
    progress: float = 0.0  # 0-100
    peers: int = 0
    download_speed_kbps: float = 0.0
    upload_speed_kbps: float = 0.0
    eta_seconds: Optional[int] = None
    provider: Optional[str] = None
    title: Optional[str] = None
    artist: Optional[str] = None
    output_path: Optional[str] = None  # Local path when complete
    gcs_path: Optional[str] = None  # GCS path when uploaded
    error: Optional[str] = None
    started_at: Optional[datetime] = None


# =============================================================================
# Torrent Management Models
# =============================================================================

class TorrentInfo(BaseModel):
    """Information about a torrent in Transmission."""
    id: int
    name: str
    status: str
    progress: float  # 0-100
    size_bytes: int
    downloaded_bytes: int
    uploaded_bytes: int
    ratio: float
    peers: int
    download_speed_kbps: float
    upload_speed_kbps: float
    added_at: Optional[datetime] = None
    done_at: Optional[datetime] = None


class TorrentListResponse(BaseModel):
    """Response listing all torrents."""
    torrents: List[TorrentInfo]
    total_size_bytes: int
    count: int


class TorrentDeleteResponse(BaseModel):
    """Response after deleting a torrent."""
    status: str
    message: str


class CleanupRequest(BaseModel):
    """Request to trigger disk cleanup."""
    strategy: str = Field("oldest", description="Cleanup strategy: oldest, largest, lowest_ratio")
    target_free_gb: float = Field(10.0, description="Target free space in GB")


class CleanupResponse(BaseModel):
    """Response after cleanup."""
    removed_count: int
    freed_bytes: int
    free_space_gb: float


# =============================================================================
# Health Models
# =============================================================================

class TransmissionHealth(BaseModel):
    """Transmission daemon health status."""
    available: bool
    version: Optional[str] = None
    active_torrents: int = 0
    error: Optional[str] = None


class DiskHealth(BaseModel):
    """Disk space status."""
    total_gb: float
    used_gb: float
    free_gb: float


class ProvidersHealth(BaseModel):
    """Provider availability status."""
    redacted: bool
    ops: bool
    youtube: bool


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    version: str
    transmission: TransmissionHealth
    disk: DiskHealth
    providers: ProvidersHealth

