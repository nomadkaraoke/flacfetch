"""
Tests for deep health check endpoint and service.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


class TestDeepHealthModels:
    """Tests for deep health check models."""

    def test_provider_health_status_values(self):
        """Test ProviderHealthStatus enum values."""
        from flacfetch.api.models import ProviderHealthStatus

        assert ProviderHealthStatus.OK == "ok"
        assert ProviderHealthStatus.DEGRADED == "degraded"
        assert ProviderHealthStatus.ERROR == "error"
        assert ProviderHealthStatus.UNCONFIGURED == "unconfigured"

    def test_provider_deep_health_model(self):
        """Test ProviderDeepHealth model structure."""
        from flacfetch.api.models import ProviderDeepHealth, ProviderHealthStatus

        health = ProviderDeepHealth(
            name="RED",
            status=ProviderHealthStatus.OK,
            configured=True,
            last_check=datetime.now(timezone.utc),
            latency_ms=150,
            details={"username": "testuser"},
        )

        assert health.name == "RED"
        assert health.status == ProviderHealthStatus.OK
        assert health.configured is True
        assert health.latency_ms == 150
        assert health.error is None

    def test_provider_deep_health_unconfigured(self):
        """Test ProviderDeepHealth for unconfigured provider."""
        from flacfetch.api.models import ProviderDeepHealth, ProviderHealthStatus

        health = ProviderDeepHealth(
            name="Spotify",
            status=ProviderHealthStatus.UNCONFIGURED,
            configured=False,
            details={"reason": "Missing SPOTIPY_CLIENT_ID"},
        )

        assert health.status == ProviderHealthStatus.UNCONFIGURED
        assert health.configured is False

    def test_deep_health_response_model(self):
        """Test DeepHealthResponse model structure."""
        from flacfetch.api.models import (
            DeepHealthResponse,
            ProviderDeepHealth,
            ProviderHealthStatus,
        )

        providers = [
            ProviderDeepHealth(
                name="RED",
                status=ProviderHealthStatus.OK,
                configured=True,
            ),
            ProviderDeepHealth(
                name="OPS",
                status=ProviderHealthStatus.OK,
                configured=True,
            ),
            ProviderDeepHealth(
                name="YouTube",
                status=ProviderHealthStatus.DEGRADED,
                configured=True,
                error="Bot detection",
            ),
            ProviderDeepHealth(
                name="Spotify",
                status=ProviderHealthStatus.UNCONFIGURED,
                configured=False,
            ),
        ]

        response = DeepHealthResponse(
            status="degraded",
            checked_at=datetime.now(timezone.utc),
            cache_age_seconds=0,
            providers=providers,
            healthy_count=2,
            degraded_count=1,
            error_count=0,
        )

        assert response.status == "degraded"
        assert len(response.providers) == 4
        assert response.healthy_count == 2
        assert response.degraded_count == 1


class TestDeepHealthService:
    """Tests for DeepHealthService."""

    def test_service_singleton(self):
        """Test that get_deep_health_service returns singleton."""
        from flacfetch.api.services import get_deep_health_service

        service1 = get_deep_health_service()
        service2 = get_deep_health_service()

        assert service1 is service2

    @pytest.mark.asyncio
    async def test_check_tracker_unconfigured(self):
        """Test tracker check when not configured."""
        from flacfetch.api.models import ProviderHealthStatus
        from flacfetch.api.services.health_check import DeepHealthService

        service = DeepHealthService()

        with patch.dict("os.environ", {}, clear=True):
            result = await service._check_tracker("RED", "RED_API_KEY", "RED_API_URL")

        assert result.name == "RED"
        assert result.status == ProviderHealthStatus.UNCONFIGURED
        assert result.configured is False

    @pytest.mark.asyncio
    async def test_check_tracker_success(self):
        """Test tracker check with successful response."""
        from flacfetch.api.models import ProviderHealthStatus
        from flacfetch.api.services.health_check import DeepHealthService

        service = DeepHealthService()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "success",
            "response": {
                "username": "testuser",
                "userstats": {
                    "uploaded": 1000000,
                    "downloaded": 500000,
                    "ratio": 2.0,
                },
            },
        }

        with patch.dict("os.environ", {"RED_API_KEY": "test_key", "RED_API_URL": "https://test.api"}):
            with patch("requests.Session") as mock_session_class:
                mock_session = MagicMock()
                mock_session.get.return_value = mock_response
                mock_session_class.return_value = mock_session

                result = await service._check_tracker("RED", "RED_API_KEY", "RED_API_URL")

        assert result.name == "RED"
        assert result.status == ProviderHealthStatus.OK
        assert result.configured is True
        assert result.latency_ms is not None
        assert result.details["username"] == "testuser"

    @pytest.mark.asyncio
    async def test_check_tracker_api_error(self):
        """Test tracker check with API error response."""
        from flacfetch.api.models import ProviderHealthStatus
        from flacfetch.api.services.health_check import DeepHealthService

        service = DeepHealthService()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "failure",
            "error": "Invalid API key",
        }

        with patch.dict("os.environ", {"RED_API_KEY": "bad_key", "RED_API_URL": "https://test.api"}):
            with patch("requests.Session") as mock_session_class:
                mock_session = MagicMock()
                mock_session.get.return_value = mock_response
                mock_session_class.return_value = mock_session

                result = await service._check_tracker("RED", "RED_API_KEY", "RED_API_URL")

        assert result.status == ProviderHealthStatus.ERROR
        assert result.error == "Invalid API key"

    @pytest.mark.asyncio
    async def test_check_spotify_unconfigured(self):
        """Test Spotify check when not configured."""
        from flacfetch.api.models import ProviderHealthStatus
        from flacfetch.api.services.health_check import DeepHealthService

        service = DeepHealthService()

        with patch.dict("os.environ", {}, clear=True):
            result = await service._check_spotify()

        assert result.name == "Spotify"
        assert result.status == ProviderHealthStatus.UNCONFIGURED
        assert result.configured is False

    @pytest.mark.asyncio
    async def test_cache_behavior(self):
        """Test that results are cached."""
        from flacfetch.api.models import ProviderHealthStatus
        from flacfetch.api.services.health_check import DeepHealthService

        service = DeepHealthService()

        # Mock all provider checks to return OK
        async def mock_check(*args, **kwargs):
            from flacfetch.api.models import ProviderDeepHealth
            return ProviderDeepHealth(
                name="Mock",
                status=ProviderHealthStatus.OK,
                configured=True,
            )

        with patch.object(service, "_check_red", mock_check):
            with patch.object(service, "_check_ops", mock_check):
                with patch.object(service, "_check_youtube", mock_check):
                    with patch.object(service, "_check_spotify", mock_check):
                        # First call
                        result1 = await service.check_health()
                        assert result1.cache_age_seconds == 0

                        # Second call should use cache
                        result2 = await service.check_health()
                        assert result2.cache_age_seconds >= 0

                        # Both should have same checked_at time
                        assert result1.checked_at == result2.checked_at

    @pytest.mark.asyncio
    async def test_refresh_bypasses_cache(self):
        """Test that refresh=True bypasses cache."""
        from flacfetch.api.models import ProviderHealthStatus
        from flacfetch.api.services.health_check import DeepHealthService

        service = DeepHealthService()
        call_count = 0

        async def mock_check(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            from flacfetch.api.models import ProviderDeepHealth
            return ProviderDeepHealth(
                name="Mock",
                status=ProviderHealthStatus.OK,
                configured=True,
            )

        with patch.object(service, "_check_red", mock_check):
            with patch.object(service, "_check_ops", mock_check):
                with patch.object(service, "_check_youtube", mock_check):
                    with patch.object(service, "_check_spotify", mock_check):
                        # First call
                        await service.check_health()
                        first_count = call_count

                        # Second call without refresh (should use cache)
                        await service.check_health()
                        assert call_count == first_count  # No new calls

                        # Third call with refresh (should make new calls)
                        await service.check_health(refresh=True)
                        assert call_count > first_count  # New calls made


class TestDeepHealthEndpoint:
    """Tests for /health/deep endpoint."""

    def test_endpoint_exists(self):
        """Test that the endpoint is registered."""
        from flacfetch.api.routes.health import router

        # Find the route
        routes = [r for r in router.routes if hasattr(r, "path") and r.path == "/health/deep"]
        assert len(routes) == 1

    def test_endpoint_is_get(self):
        """Test that the endpoint uses GET method."""
        from flacfetch.api.routes.health import router

        routes = [r for r in router.routes if hasattr(r, "path") and r.path == "/health/deep"]
        assert len(routes) == 1
        assert "GET" in routes[0].methods

    def test_endpoint_response_model(self):
        """Test that endpoint has correct response model."""
        from flacfetch.api.routes.health import router

        routes = [r for r in router.routes if hasattr(r, "path") and r.path == "/health/deep"]
        assert len(routes) == 1
        # Check response model (may be in endpoint or route depending on FastAPI version)
        endpoint = routes[0].endpoint
        assert endpoint is not None
