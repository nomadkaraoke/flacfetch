"""
Tests for flacfetch API routes.

Note: These are unit tests that mock dependencies. Integration tests
that hit real APIs should be in test_api_integration.py and marked
appropriately.
"""

import pytest


class TestSearchRoute:
    """Tests for /search endpoint logic."""

    def test_search_request_validation(self):
        """Test that search request validates input."""
        from flacfetch.api.models import SearchRequest

        # Valid request
        req = SearchRequest(artist="ABBA", title="Waterloo")
        assert req.artist == "ABBA"
        assert req.title == "Waterloo"

        # Missing fields should raise
        with pytest.raises(ValueError):
            SearchRequest(artist="ABBA")  # Missing title

    def test_search_response_format(self):
        """Test search response model structure."""
        from flacfetch.api.models import SearchResponse, SearchResultItem

        result = SearchResultItem(
            index=0,
            title="Waterloo",
            artist="ABBA",
            provider="YouTube",
            quality="opus 128kbps",
            is_lossless=False,
        )

        resp = SearchResponse(
            search_id="test123",
            artist="ABBA",
            title="Waterloo",
            results=[result],
            results_count=1,
        )

        assert resp.search_id == "test123"
        assert len(resp.results) == 1
        assert resp.results[0].provider == "YouTube"


class TestDownloadRoute:
    """Tests for /download endpoint logic."""

    def test_download_request_validation(self):
        """Test download request validation."""
        from flacfetch.api.models import DownloadRequest

        # Basic request
        req = DownloadRequest(search_id="abc", result_index=0)
        assert req.search_id == "abc"
        assert req.result_index == 0
        assert req.upload_to_gcs is False

        # With GCS upload
        req = DownloadRequest(
            search_id="abc",
            result_index=0,
            upload_to_gcs=True,
            gcs_path="uploads/test/",
        )
        assert req.upload_to_gcs is True
        assert req.gcs_path == "uploads/test/"

    def test_download_status_values(self):
        """Test download status enum values."""
        from flacfetch.api.models import DownloadStatus

        assert DownloadStatus.QUEUED == "queued"
        assert DownloadStatus.DOWNLOADING == "downloading"
        assert DownloadStatus.COMPLETE == "complete"
        assert DownloadStatus.FAILED == "failed"


class TestTorrentRoute:
    """Tests for /torrents endpoint logic."""

    def test_torrent_info_model(self):
        """Test torrent info model structure."""
        from flacfetch.api.models import TorrentInfo

        info = TorrentInfo(
            id=1,
            name="Test Torrent",
            status="seeding",
            progress=100.0,
            size_bytes=1000000,
            downloaded_bytes=1000000,
            uploaded_bytes=500000,
            ratio=0.5,
            peers=2,
            download_speed_kbps=0,
            upload_speed_kbps=100,
        )

        assert info.id == 1
        assert info.ratio == 0.5
        assert info.peers == 2

    def test_cleanup_request_defaults(self):
        """Test cleanup request default values."""
        from flacfetch.api.models import CleanupRequest

        req = CleanupRequest()
        assert req.strategy == "oldest"
        assert req.target_free_gb == 10.0

    def test_cleanup_request_custom_values(self):
        """Test cleanup request with custom values."""
        from flacfetch.api.models import CleanupRequest

        req = CleanupRequest(strategy="largest", target_free_gb=20.0)
        assert req.strategy == "largest"
        assert req.target_free_gb == 20.0


class TestHealthRoute:
    """Tests for /health endpoint."""

    def test_health_response_model(self):
        """Test health response model structure."""
        from flacfetch.api.models import (
            DiskHealth,
            HealthResponse,
            ProvidersHealth,
            TransmissionHealth,
        )

        resp = HealthResponse(
            status="healthy",
            version="0.1.0",
            transmission=TransmissionHealth(available=True, active_torrents=5),
            disk=DiskHealth(total_gb=30, used_gb=15, free_gb=15),
            providers=ProvidersHealth(red=True, ops=True, youtube=True),
        )

        assert resp.status == "healthy"
        assert resp.transmission.available is True
        assert resp.disk.free_gb == 15
        assert resp.providers.youtube is True
