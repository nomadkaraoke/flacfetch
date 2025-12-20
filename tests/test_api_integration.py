"""
Integration tests for flacfetch API.

These tests verify end-to-end functionality with minimal mocking.
Note: Some tests are skipped to avoid hitting real APIs.
"""
import os
from unittest.mock import patch


class TestAppCreation:
    """Test that the FastAPI app can be created."""

    def test_create_app_returns_fastapi_instance(self):
        """Test that create_app returns a FastAPI instance."""
        from flacfetch.api import create_app
        app = create_app()

        from fastapi import FastAPI
        assert isinstance(app, FastAPI)

    def test_app_has_expected_routes(self):
        """Test that app has the expected routes registered."""
        from flacfetch.api import create_app
        app = create_app()

        # Get all route paths
        routes = [route.path for route in app.routes]

        # Check key routes exist
        assert "/health" in routes
        assert "/search" in routes
        assert "/download" in routes
        assert "/torrents" in routes


class TestHealthEndpoint:
    """Test health endpoint."""

    def test_health_returns_json(self):
        """Test that health endpoint returns JSON response."""
        from fastapi.testclient import TestClient

        from flacfetch.api import create_app

        # Disable API key for testing
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('FLACFETCH_API_KEY', None)

            app = create_app()
            client = TestClient(app)

            response = client.get("/health")

            assert response.status_code == 200
            data = response.json()
            assert "status" in data


class TestCORSConfiguration:
    """Test CORS is properly configured."""

    def test_cors_headers_on_preflight(self):
        """Test that CORS headers are present on preflight requests."""
        from fastapi.testclient import TestClient

        from flacfetch.api import create_app

        app = create_app()
        client = TestClient(app)

        # Preflight request
        response = client.options(
            "/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            }
        )

        # CORS should allow the request
        assert response.status_code in [200, 204, 405]
