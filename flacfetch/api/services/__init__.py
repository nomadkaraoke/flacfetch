"""
Flacfetch API services.
"""
from .disk_manager import DiskManager, get_disk_manager
from .download_manager import DownloadManager, get_download_manager

__all__ = [
    "DownloadManager",
    "get_download_manager",
    "DiskManager",
    "get_disk_manager",
]

