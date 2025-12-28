"""
Flacfetch API services.
"""
from datetime import datetime, timezone
from typing import Optional

from .disk_manager import DiskManager, get_disk_manager
from .download_manager import DownloadManager, get_download_manager
from .search_cache import SearchCacheService, get_search_cache_service

# Server start time tracking (set by main.py lifespan)
_server_started_at: Optional[datetime] = None


def set_server_started_at(dt: datetime) -> None:
    """Set the server start time (called from main.py lifespan)."""
    global _server_started_at
    _server_started_at = dt


def get_server_started_at() -> Optional[datetime]:
    """Get the server start time."""
    return _server_started_at


__all__ = [
    "DownloadManager",
    "get_download_manager",
    "DiskManager",
    "get_disk_manager",
    "SearchCacheService",
    "get_search_cache_service",
    "set_server_started_at",
    "get_server_started_at",
]

