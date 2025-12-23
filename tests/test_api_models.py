"""
Tests for flacfetch API Pydantic models.
"""

import pytest

from flacfetch.api.models import (
    CleanupRequest,
    CleanupResponse,
    DiskHealth,
    DownloadRequest,
    DownloadStartResponse,
    DownloadStatus,
    DownloadStatusResponse,
    HealthResponse,
    ProvidersHealth,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
    TorrentInfo,
    TorrentListResponse,
    TransmissionHealth,
)


class TestSearchModels:
    """Tests for search-related models."""

    def test_search_request_valid(self):
        """Test valid search request."""
        req = SearchRequest(artist="ABBA", title="Waterloo")
        assert req.artist == "ABBA"
        assert req.title == "Waterloo"

    def test_search_request_requires_fields(self):
        """Test that search request requires both fields."""
        with pytest.raises(ValueError):
            SearchRequest(artist="ABBA")  # Missing title

        with pytest.raises(ValueError):
            SearchRequest(title="Waterloo")  # Missing artist

    def test_search_result_item(self):
        """Test search result item model."""
        item = SearchResultItem(
            index=0,
            title="Waterloo",
            artist="ABBA",
            provider="RED",
            quality="FLAC 16bit CD",
            seeders=45,
            size_bytes=31457280,
            is_lossless=True,
        )
        assert item.index == 0
        assert item.provider == "RED"
        assert item.is_lossless is True

    def test_search_response(self):
        """Test search response model."""
        resp = SearchResponse(
            search_id="abc123",
            artist="ABBA",
            title="Waterloo",
            results=[
                SearchResultItem(
                    index=0,
                    title="Waterloo",
                    artist="ABBA",
                    provider="YouTube",
                    quality="opus 128kbps",
                    is_lossless=False,
                )
            ],
            results_count=1,
        )
        assert resp.search_id == "abc123"
        assert len(resp.results) == 1
        assert resp.results_count == 1


class TestDownloadModels:
    """Tests for download-related models."""

    def test_download_request_basic(self):
        """Test basic download request."""
        req = DownloadRequest(
            search_id="abc123",
            result_index=0,
        )
        assert req.search_id == "abc123"
        assert req.result_index == 0
        assert req.upload_to_gcs is False

    def test_download_request_with_gcs(self):
        """Test download request with GCS upload."""
        req = DownloadRequest(
            search_id="abc123",
            result_index=0,
            upload_to_gcs=True,
            gcs_path="uploads/job123/audio/",
        )
        assert req.upload_to_gcs is True
        assert req.gcs_path == "uploads/job123/audio/"

    def test_download_status_enum(self):
        """Test download status enum values."""
        assert DownloadStatus.QUEUED == "queued"
        assert DownloadStatus.DOWNLOADING == "downloading"
        assert DownloadStatus.UPLOADING == "uploading"
        assert DownloadStatus.SEEDING == "seeding"
        assert DownloadStatus.COMPLETE == "complete"
        assert DownloadStatus.FAILED == "failed"
        assert DownloadStatus.CANCELLED == "cancelled"

    def test_download_start_response(self):
        """Test download start response."""
        resp = DownloadStartResponse(
            download_id="dl_xyz789",
            status=DownloadStatus.QUEUED,
        )
        assert resp.download_id == "dl_xyz789"
        assert resp.status == DownloadStatus.QUEUED

    def test_download_status_response_full(self):
        """Test full download status response."""
        resp = DownloadStatusResponse(
            download_id="dl_xyz789",
            status=DownloadStatus.DOWNLOADING,
            progress=45.2,
            peers=3,
            download_speed_kbps=1250.5,
            eta_seconds=120,
            provider="RED",
            title="Waterloo",
            artist="ABBA",
        )
        assert resp.progress == 45.2
        assert resp.peers == 3
        assert resp.eta_seconds == 120


class TestTorrentModels:
    """Tests for torrent management models."""

    def test_torrent_info(self):
        """Test torrent info model."""
        info = TorrentInfo(
            id=1,
            name="ABBA - Waterloo [FLAC]",
            status="seeding",
            progress=100.0,
            size_bytes=314572800,
            downloaded_bytes=314572800,
            uploaded_bytes=628145600,
            ratio=2.0,
            peers=5,
            download_speed_kbps=0,
            upload_speed_kbps=125.5,
        )
        assert info.ratio == 2.0
        assert info.peers == 5

    def test_torrent_list_response(self):
        """Test torrent list response."""
        resp = TorrentListResponse(
            torrents=[
                TorrentInfo(
                    id=1,
                    name="Test",
                    status="seeding",
                    progress=100.0,
                    size_bytes=1000000,
                    downloaded_bytes=1000000,
                    uploaded_bytes=500000,
                    ratio=0.5,
                    peers=0,
                    download_speed_kbps=0,
                    upload_speed_kbps=0,
                )
            ],
            total_size_bytes=1000000,
            count=1,
        )
        assert resp.count == 1
        assert len(resp.torrents) == 1

    def test_cleanup_request(self):
        """Test cleanup request model."""
        req = CleanupRequest(strategy="oldest", target_free_gb=15.0)
        assert req.strategy == "oldest"
        assert req.target_free_gb == 15.0

    def test_cleanup_request_defaults(self):
        """Test cleanup request defaults."""
        req = CleanupRequest()
        assert req.strategy == "oldest"
        assert req.target_free_gb == 10.0

    def test_cleanup_response(self):
        """Test cleanup response model."""
        resp = CleanupResponse(
            removed_count=3,
            freed_bytes=1073741824,
            free_space_gb=12.5,
        )
        assert resp.removed_count == 3
        assert resp.freed_bytes == 1073741824


class TestHealthModels:
    """Tests for health check models."""

    def test_transmission_health_available(self):
        """Test transmission health when available."""
        health = TransmissionHealth(
            available=True,
            version="4.0.5",
            active_torrents=5,
        )
        assert health.available is True
        assert health.version == "4.0.5"
        assert health.error is None

    def test_transmission_health_unavailable(self):
        """Test transmission health when unavailable."""
        health = TransmissionHealth(
            available=False,
            error="Connection refused",
        )
        assert health.available is False
        assert health.error == "Connection refused"

    def test_disk_health(self):
        """Test disk health model."""
        disk = DiskHealth(
            total_gb=30.0,
            used_gb=18.5,
            free_gb=11.5,
        )
        assert disk.total_gb == 30.0
        assert disk.free_gb == 11.5

    def test_providers_health(self):
        """Test providers health model."""
        providers = ProvidersHealth(
            red=True,
            ops=False,
            youtube=True,
        )
        assert providers.red is True
        assert providers.ops is False
        assert providers.youtube is True

    def test_health_response_full(self):
        """Test full health response."""
        resp = HealthResponse(
            status="healthy",
            version="0.1.0",
            transmission=TransmissionHealth(available=True, active_torrents=3),
            disk=DiskHealth(total_gb=30, used_gb=15, free_gb=15),
            providers=ProvidersHealth(red=True, ops=True, youtube=True),
        )
        assert resp.status == "healthy"
        assert resp.transmission.available is True
        assert resp.providers.youtube is True

