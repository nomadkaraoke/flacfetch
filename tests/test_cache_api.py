"""
Tests for the cache management API endpoints.
"""
import os
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


class TestCacheRoutes:
    """Test that cache routes are registered."""

    def test_cache_routes_exist(self):
        """Test that cache routes are registered in the app."""
        from flacfetch.api import create_app
        app = create_app()

        routes = [route.path for route in app.routes]

        assert "/cache/stats" in routes
        assert "/cache/search" in routes
        assert "/cache" in routes


class TestCacheStatsEndpoint:
    """Tests for GET /cache/stats endpoint."""

    def test_cache_stats_requires_auth(self):
        """Test that cache stats requires API key."""
        from flacfetch.api import create_app

        with patch.dict(os.environ, {"FLACFETCH_API_KEY": "test-key"}):
            app = create_app()
            client = TestClient(app)

            response = client.get("/cache/stats")
            assert response.status_code == 401

    def test_cache_stats_returns_stats(self):
        """Test that cache stats returns statistics."""
        from flacfetch.api import create_app

        mock_stats = {
            "count": 5,
            "total_size_bytes": 1024,
            "oldest_entry": "2025-01-01T00:00:00Z",
            "newest_entry": "2025-01-03T00:00:00Z",
            "configured": True,
        }

        with patch.dict(os.environ, {"FLACFETCH_API_KEY": "test-key"}):
            app = create_app()
            client = TestClient(app)

            with patch("flacfetch.api.routes.cache.get_search_cache_service") as mock_get_service:
                mock_service = AsyncMock()
                mock_service.get_stats.return_value = mock_stats
                mock_get_service.return_value = mock_service

                response = client.get(
                    "/cache/stats",
                    headers={"X-API-Key": "test-key"}
                )

                assert response.status_code == 200
                data = response.json()
                assert data["count"] == 5
                assert data["total_size_bytes"] == 1024
                assert data["configured"] is True


class TestClearSearchCacheEndpoint:
    """Tests for DELETE /cache/search endpoint."""

    def test_clear_search_cache_requires_auth(self):
        """Test that clearing search cache requires API key."""
        from flacfetch.api import create_app

        with patch.dict(os.environ, {"FLACFETCH_API_KEY": "test-key"}):
            app = create_app()
            client = TestClient(app)

            response = client.request(
                "DELETE",
                "/cache/search",
                json={"artist": "Test", "title": "Song"}
            )
            assert response.status_code == 401

    def test_clear_search_cache_success(self):
        """Test clearing a specific cache entry."""
        from flacfetch.api import create_app

        with patch.dict(os.environ, {"FLACFETCH_API_KEY": "test-key"}):
            app = create_app()
            client = TestClient(app)

            with patch("flacfetch.api.routes.cache.get_search_cache_service") as mock_get_service:
                mock_service = AsyncMock()
                mock_service.delete_cache_entry.return_value = True
                mock_get_service.return_value = mock_service

                response = client.request(
                    "DELETE",
                    "/cache/search",
                    json={"artist": "Avril Lavigne", "title": "I'm With You"},
                    headers={"X-API-Key": "test-key"}
                )

                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "success"
                assert data["deleted"] is True
                assert "Avril Lavigne" in data["message"]

                mock_service.delete_cache_entry.assert_called_once_with(
                    "Avril Lavigne", "I'm With You"
                )

    def test_clear_search_cache_not_found(self):
        """Test clearing a cache entry that doesn't exist."""
        from flacfetch.api import create_app

        with patch.dict(os.environ, {"FLACFETCH_API_KEY": "test-key"}):
            app = create_app()
            client = TestClient(app)

            with patch("flacfetch.api.routes.cache.get_search_cache_service") as mock_get_service:
                mock_service = AsyncMock()
                mock_service.delete_cache_entry.return_value = False
                mock_get_service.return_value = mock_service

                response = client.request(
                    "DELETE",
                    "/cache/search",
                    json={"artist": "Unknown", "title": "Song"},
                    headers={"X-API-Key": "test-key"}
                )

                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "success"
                assert data["deleted"] is False


class TestClearAllCacheEndpoint:
    """Tests for DELETE /cache endpoint."""

    def test_clear_all_cache_requires_auth(self):
        """Test that clearing all cache requires API key."""
        from flacfetch.api import create_app

        with patch.dict(os.environ, {"FLACFETCH_API_KEY": "test-key"}):
            app = create_app()
            client = TestClient(app)

            response = client.delete("/cache")
            assert response.status_code == 401

    def test_clear_all_cache_success(self):
        """Test clearing all cache entries."""
        from flacfetch.api import create_app

        with patch.dict(os.environ, {"FLACFETCH_API_KEY": "test-key"}):
            app = create_app()
            client = TestClient(app)

            with patch("flacfetch.api.routes.cache.get_search_cache_service") as mock_get_service:
                mock_service = AsyncMock()
                mock_service.clear_all.return_value = 15
                mock_get_service.return_value = mock_service

                response = client.delete(
                    "/cache",
                    headers={"X-API-Key": "test-key"}
                )

                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "success"
                assert data["deleted_count"] == 15
                assert "15" in data["message"]

                mock_service.clear_all.assert_called_once()
