"""
Flacfetch API services.
"""
from .download_manager import DownloadManager, get_download_manager
from .disk_manager import DiskManager, get_disk_manager

__all__ = [
    "DownloadManager",
    "get_download_manager",
    "DiskManager", 
    "get_disk_manager",
]

