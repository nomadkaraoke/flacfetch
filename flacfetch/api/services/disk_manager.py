"""
Disk space management for flacfetch service.

Monitors disk usage and automatically cleans up old torrents when space runs low.
"""
import logging
import os
import shutil
from datetime import datetime
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class DiskManager:
    """
    Manages disk space by removing old torrents when space runs low.
    """

    def __init__(
        self,
        min_free_gb: float = 5.0,
        download_dir: Optional[str] = None,
    ):
        """
        Initialize disk manager.

        Args:
            min_free_gb: Minimum free space to maintain (triggers cleanup if below)
            download_dir: Directory where downloads are stored
        """
        self.min_free_gb = min_free_gb
        self.download_dir = download_dir or os.environ.get(
            "FLACFETCH_DOWNLOAD_DIR",
            "/var/lib/transmission-daemon/downloads"
        )

    def get_disk_usage(self) -> Tuple[float, float, float]:
        """
        Get disk usage for the download directory.

        Returns:
            Tuple of (total_gb, used_gb, free_gb)
        """
        try:
            stat = shutil.disk_usage(self.download_dir)
            total_gb = stat.total / (1024 ** 3)
            used_gb = stat.used / (1024 ** 3)
            free_gb = stat.free / (1024 ** 3)
            return total_gb, used_gb, free_gb
        except Exception as e:
            logger.error(f"Failed to get disk usage: {e}")
            # Return safe defaults
            return 0, 0, 0

    def needs_cleanup(self) -> bool:
        """Check if cleanup is needed based on free space."""
        _, _, free_gb = self.get_disk_usage()
        return free_gb < self.min_free_gb

    def cleanup_oldest(
        self,
        transmission_client,
        target_free_gb: Optional[float] = None,
    ) -> Tuple[int, int]:
        """
        Remove oldest torrents until target free space is reached.

        Args:
            transmission_client: Transmission RPC client
            target_free_gb: Target free space (default: min_free_gb + 5)

        Returns:
            Tuple of (removed_count, freed_bytes)
        """
        if target_free_gb is None:
            target_free_gb = self.min_free_gb + 5

        _, _, free_gb = self.get_disk_usage()
        if free_gb >= target_free_gb:
            return 0, 0

        # Get torrents sorted by date added (oldest first)
        torrents = transmission_client.get_torrents()
        torrents_with_date = []
        for t in torrents:
            added_date = getattr(t, 'date_added', None) or datetime.min
            torrents_with_date.append((added_date, t))

        torrents_with_date.sort(key=lambda x: x[0])

        removed_count = 0
        freed_bytes = 0

        for _, torrent in torrents_with_date:
            _, _, free_gb = self.get_disk_usage()
            if free_gb >= target_free_gb:
                break

            try:
                size = torrent.total_size
                logger.info(f"Removing old torrent for cleanup: {torrent.name} ({size / (1024**2):.1f} MB)")
                transmission_client.remove_torrent(torrent.id, delete_data=True)
                removed_count += 1
                freed_bytes += size
            except Exception as e:
                logger.error(f"Failed to remove torrent {torrent.name}: {e}")

        logger.info(f"Cleanup complete: removed {removed_count} torrents, freed {freed_bytes / (1024**2):.1f} MB")
        return removed_count, freed_bytes

    def cleanup_largest(
        self,
        transmission_client,
        target_free_gb: Optional[float] = None,
    ) -> Tuple[int, int]:
        """
        Remove largest torrents until target free space is reached.

        Args:
            transmission_client: Transmission RPC client
            target_free_gb: Target free space (default: min_free_gb + 5)

        Returns:
            Tuple of (removed_count, freed_bytes)
        """
        if target_free_gb is None:
            target_free_gb = self.min_free_gb + 5

        _, _, free_gb = self.get_disk_usage()
        if free_gb >= target_free_gb:
            return 0, 0

        # Get torrents sorted by size (largest first)
        torrents = transmission_client.get_torrents()
        torrents.sort(key=lambda t: t.total_size, reverse=True)

        removed_count = 0
        freed_bytes = 0

        for torrent in torrents:
            _, _, free_gb = self.get_disk_usage()
            if free_gb >= target_free_gb:
                break

            try:
                size = torrent.total_size
                logger.info(f"Removing large torrent for cleanup: {torrent.name} ({size / (1024**2):.1f} MB)")
                transmission_client.remove_torrent(torrent.id, delete_data=True)
                removed_count += 1
                freed_bytes += size
            except Exception as e:
                logger.error(f"Failed to remove torrent {torrent.name}: {e}")

        logger.info(f"Cleanup complete: removed {removed_count} torrents, freed {freed_bytes / (1024**2):.1f} MB")
        return removed_count, freed_bytes

    def cleanup_lowest_ratio(
        self,
        transmission_client,
        target_free_gb: Optional[float] = None,
    ) -> Tuple[int, int]:
        """
        Remove torrents with lowest upload ratio until target free space is reached.

        Args:
            transmission_client: Transmission RPC client
            target_free_gb: Target free space (default: min_free_gb + 5)

        Returns:
            Tuple of (removed_count, freed_bytes)
        """
        if target_free_gb is None:
            target_free_gb = self.min_free_gb + 5

        _, _, free_gb = self.get_disk_usage()
        if free_gb >= target_free_gb:
            return 0, 0

        # Get torrents sorted by ratio (lowest first)
        torrents = transmission_client.get_torrents()
        torrents.sort(key=lambda t: t.ratio)

        removed_count = 0
        freed_bytes = 0

        for torrent in torrents:
            _, _, free_gb = self.get_disk_usage()
            if free_gb >= target_free_gb:
                break

            try:
                size = torrent.total_size
                logger.info(f"Removing low-ratio torrent for cleanup: {torrent.name} (ratio: {torrent.ratio:.2f})")
                transmission_client.remove_torrent(torrent.id, delete_data=True)
                removed_count += 1
                freed_bytes += size
            except Exception as e:
                logger.error(f"Failed to remove torrent {torrent.name}: {e}")

        logger.info(f"Cleanup complete: removed {removed_count} torrents, freed {freed_bytes / (1024**2):.1f} MB")
        return removed_count, freed_bytes


# Singleton instance
_disk_manager: Optional[DiskManager] = None


def get_disk_manager() -> DiskManager:
    """Get the singleton DiskManager instance."""
    global _disk_manager
    if _disk_manager is None:
        min_free_gb = float(os.environ.get("FLACFETCH_MIN_FREE_GB", "5"))
        _disk_manager = DiskManager(min_free_gb=min_free_gb)
    return _disk_manager

