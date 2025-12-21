"""
Tests for flacfetch API services (download manager, disk manager).
"""
import tempfile
from datetime import datetime
from unittest.mock import Mock

from flacfetch.api.models import DownloadStatus
from flacfetch.api.services.disk_manager import DiskManager
from flacfetch.api.services.download_manager import DownloadManager, DownloadTask, SearchCache


class TestDiskManager:
    """Tests for DiskManager."""

    def test_init_defaults(self):
        """Test disk manager initialization with defaults."""
        dm = DiskManager()
        assert dm.min_free_gb == 5.0

    def test_init_custom_values(self):
        """Test disk manager initialization with custom values."""
        dm = DiskManager(min_free_gb=10.0, download_dir="/custom/path")
        assert dm.min_free_gb == 10.0
        assert dm.download_dir == "/custom/path"

    def test_get_disk_usage(self):
        """Test getting disk usage."""
        # Use temp directory which always exists
        dm = DiskManager(download_dir=tempfile.gettempdir())
        total, used, free = dm.get_disk_usage()

        assert total > 0
        assert used >= 0
        assert free >= 0
        # On Linux, total >= used + free due to reserved blocks for root
        assert total >= used + free

    def test_get_disk_usage_invalid_path(self):
        """Test disk usage with invalid path returns zeros."""
        dm = DiskManager(download_dir="/nonexistent/path/that/does/not/exist")
        total, used, free = dm.get_disk_usage()

        assert total == 0
        assert used == 0
        assert free == 0

    def test_needs_cleanup_low_space(self):
        """Test needs_cleanup returns True when space is low."""
        dm = DiskManager(min_free_gb=1000.0)  # Set impossibly high threshold
        dm.download_dir = tempfile.gettempdir()

        assert dm.needs_cleanup() is True

    def test_needs_cleanup_sufficient_space(self):
        """Test needs_cleanup returns False when space is sufficient."""
        dm = DiskManager(min_free_gb=0.001)  # Very low threshold
        dm.download_dir = tempfile.gettempdir()

        assert dm.needs_cleanup() is False

    def test_cleanup_oldest_no_action_needed(self):
        """Test cleanup returns 0 when no cleanup needed."""
        dm = DiskManager(min_free_gb=0.001)
        dm.download_dir = tempfile.gettempdir()

        mock_client = Mock()
        removed, freed = dm.cleanup_oldest(mock_client, target_free_gb=0.001)

        assert removed == 0
        assert freed == 0

    def test_cleanup_oldest_removes_torrents(self):
        """Test cleanup removes oldest torrents."""
        dm = DiskManager(min_free_gb=1000.0)  # Force cleanup
        dm.download_dir = tempfile.gettempdir()

        # Mock torrents with dates
        torrent1 = Mock()
        torrent1.id = 1
        torrent1.name = "Old Torrent"
        torrent1.date_added = datetime(2020, 1, 1)
        torrent1.total_size = 1000000

        torrent2 = Mock()
        torrent2.id = 2
        torrent2.name = "New Torrent"
        torrent2.date_added = datetime(2024, 1, 1)
        torrent2.total_size = 2000000

        mock_client = Mock()
        mock_client.get_torrents.return_value = [torrent2, torrent1]  # Out of order
        mock_client.remove_torrent.return_value = None

        # Patch disk usage to simulate cleanup working after each removal
        call_count = [0]
        def mock_disk_usage():
            call_count[0] += 1
            if call_count[0] <= 2:
                return (30.0, 29.0, 1.0)  # Low space
            return (30.0, 20.0, 10.0)  # Space freed

        dm.get_disk_usage = mock_disk_usage

        removed, freed = dm.cleanup_oldest(mock_client, target_free_gb=5.0)

        # Should have removed the oldest torrent
        assert removed >= 1
        mock_client.remove_torrent.assert_called()

    def test_cleanup_largest_removes_biggest_first(self):
        """Test cleanup largest removes biggest torrents first."""
        dm = DiskManager(min_free_gb=1000.0)
        dm.download_dir = tempfile.gettempdir()

        small_torrent = Mock()
        small_torrent.id = 1
        small_torrent.name = "Small"
        small_torrent.total_size = 100

        large_torrent = Mock()
        large_torrent.id = 2
        large_torrent.name = "Large"
        large_torrent.total_size = 10000000

        mock_client = Mock()
        mock_client.get_torrents.return_value = [small_torrent, large_torrent]

        call_count = [0]
        def mock_disk_usage():
            call_count[0] += 1
            if call_count[0] <= 2:
                return (30.0, 29.0, 1.0)
            return (30.0, 20.0, 10.0)

        dm.get_disk_usage = mock_disk_usage

        dm.cleanup_largest(mock_client, target_free_gb=5.0)

        # Should remove large torrent first
        first_call = mock_client.remove_torrent.call_args_list[0]
        assert first_call[0][0] == 2  # Large torrent ID

    def test_cleanup_lowest_ratio(self):
        """Test cleanup by lowest ratio."""
        dm = DiskManager(min_free_gb=1000.0)
        dm.download_dir = tempfile.gettempdir()

        high_ratio = Mock()
        high_ratio.id = 1
        high_ratio.name = "High Ratio"
        high_ratio.ratio = 2.5
        high_ratio.total_size = 1000

        low_ratio = Mock()
        low_ratio.id = 2
        low_ratio.name = "Low Ratio"
        low_ratio.ratio = 0.1
        low_ratio.total_size = 1000

        mock_client = Mock()
        mock_client.get_torrents.return_value = [high_ratio, low_ratio]

        call_count = [0]
        def mock_disk_usage():
            call_count[0] += 1
            if call_count[0] <= 2:
                return (30.0, 29.0, 1.0)
            return (30.0, 20.0, 10.0)

        dm.get_disk_usage = mock_disk_usage

        dm.cleanup_lowest_ratio(mock_client, target_free_gb=5.0)

        # Should remove low ratio torrent first
        first_call = mock_client.remove_torrent.call_args_list[0]
        assert first_call[0][0] == 2  # Low ratio torrent ID


class TestDownloadManager:
    """Tests for DownloadManager."""

    def test_init_defaults(self):
        """Test download manager initialization."""
        dm = DownloadManager()
        assert dm.keep_seeding is True
        assert len(dm._downloads) == 0
        assert len(dm._searches) == 0

    def test_init_custom_values(self):
        """Test download manager with custom values."""
        dm = DownloadManager(keep_seeding=False, gcs_bucket="test-bucket")
        assert dm.keep_seeding is False
        assert dm.gcs_bucket == "test-bucket"

    def test_cache_search(self):
        """Test caching search results."""
        dm = DownloadManager()

        mock_results = [Mock(), Mock()]
        dm.cache_search("search_123", "ABBA", "Waterloo", mock_results)

        cache = dm.get_search("search_123")
        assert cache is not None
        assert cache.search_id == "search_123"
        assert cache.artist == "ABBA"
        assert cache.title == "Waterloo"
        assert len(cache.results) == 2

    def test_get_search_expired(self):
        """Test that expired searches return None."""
        import time

        dm = DownloadManager()
        dm._search_ttl_seconds = 0.01  # Expire after 10ms

        dm.cache_search("search_123", "ABBA", "Waterloo", [])
        time.sleep(0.02)  # Wait for expiration

        # Should return None because TTL expired
        cache = dm.get_search("search_123")
        assert cache is None

    def test_get_search_not_found(self):
        """Test getting non-existent search returns None."""
        dm = DownloadManager()
        assert dm.get_search("nonexistent") is None

    def test_create_download(self):
        """Test creating a download task."""
        dm = DownloadManager()

        # Cache a search first
        mock_release = Mock()
        mock_release.source_name = "YouTube"
        mock_release.title = "Waterloo"
        dm.cache_search("search_123", "ABBA", "Waterloo", [mock_release])

        task = dm.create_download(
            search_id="search_123",
            result_index=0,
            output_filename="test",
            upload_to_gcs=True,
            gcs_destination="uploads/",
        )

        assert task.download_id.startswith("dl_")
        assert task.search_id == "search_123"
        assert task.result_index == 0
        assert task.status == DownloadStatus.QUEUED
        assert task.upload_to_gcs is True

    def test_get_download(self):
        """Test getting a download task."""
        dm = DownloadManager()
        dm.cache_search("search_123", "ABBA", "Waterloo", [Mock()])

        task = dm.create_download("search_123", 0)

        retrieved = dm.get_download(task.download_id)
        assert retrieved is task

    def test_get_download_not_found(self):
        """Test getting non-existent download returns None."""
        dm = DownloadManager()
        assert dm.get_download("nonexistent") is None

    def test_update_download(self):
        """Test updating a download task."""
        dm = DownloadManager()
        dm.cache_search("search_123", "ABBA", "Waterloo", [Mock()])

        task = dm.create_download("search_123", 0)

        dm.update_download(
            task.download_id,
            status=DownloadStatus.DOWNLOADING,
            progress=50.0,
            peers=3,
        )

        updated = dm.get_download(task.download_id)
        assert updated.status == DownloadStatus.DOWNLOADING
        assert updated.progress == 50.0
        assert updated.peers == 3

    def test_list_downloads(self):
        """Test listing all downloads."""
        dm = DownloadManager()
        dm.cache_search("search_1", "Artist1", "Song1", [Mock()])
        dm.cache_search("search_2", "Artist2", "Song2", [Mock()])

        task1 = dm.create_download("search_1", 0)
        task2 = dm.create_download("search_2", 0)

        downloads = dm.list_downloads()
        assert len(downloads) == 2
        assert task1 in downloads
        assert task2 in downloads


class TestDownloadTask:
    """Tests for DownloadTask dataclass."""

    def test_download_task_defaults(self):
        """Test DownloadTask default values."""
        task = DownloadTask(
            download_id="dl_123",
            search_id="search_123",
            result_index=0,
        )

        assert task.status == DownloadStatus.QUEUED
        assert task.progress == 0.0
        assert task.peers == 0
        assert task.output_path is None
        assert task.error is None


class TestSearchCache:
    """Tests for SearchCache dataclass."""

    def test_search_cache_created_at(self):
        """Test SearchCache has created_at timestamp."""
        cache = SearchCache(
            search_id="search_123",
            artist="ABBA",
            title="Waterloo",
            results=[],
        )

        assert cache.created_at is not None
        assert isinstance(cache.created_at, datetime)

