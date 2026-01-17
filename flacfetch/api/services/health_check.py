"""
Deep health check service for flacfetch providers.

Performs real API/connectivity tests on each provider with caching.
"""
import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..models import DeepHealthResponse, ProviderDeepHealth, ProviderHealthStatus

logger = logging.getLogger(__name__)

# Cache TTL in seconds
CACHE_TTL_SECONDS = 300  # 5 minutes

# Known good YouTube video for connectivity test (short, always available)
YOUTUBE_TEST_VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


class DeepHealthService:
    """
    Service for performing deep health checks on providers.

    Results are cached for 5 minutes to avoid hammering external APIs.
    """

    def __init__(self):
        self._cache: Optional[DeepHealthResponse] = None
        self._cache_time: Optional[datetime] = None
        self._lock = asyncio.Lock()

    def _is_cache_valid(self) -> bool:
        """Check if the cached result is still valid."""
        if self._cache is None or self._cache_time is None:
            return False
        age = (datetime.now(timezone.utc) - self._cache_time).total_seconds()
        return age < CACHE_TTL_SECONDS

    def _get_cache_age(self) -> Optional[int]:
        """Get the age of the cache in seconds."""
        if self._cache_time is None:
            return None
        return int((datetime.now(timezone.utc) - self._cache_time).total_seconds())

    async def check_health(self, refresh: bool = False) -> DeepHealthResponse:
        """
        Perform deep health check on all providers.

        Args:
            refresh: If True, bypass cache and perform fresh check

        Returns:
            DeepHealthResponse with detailed provider status
        """
        async with self._lock:
            # Return cached result if valid and not forcing refresh
            if not refresh and self._is_cache_valid() and self._cache is not None:
                # Update cache_age_seconds in the response
                return DeepHealthResponse(
                    status=self._cache.status,
                    checked_at=self._cache.checked_at,
                    cache_age_seconds=self._get_cache_age(),
                    providers=self._cache.providers,
                    healthy_count=self._cache.healthy_count,
                    degraded_count=self._cache.degraded_count,
                    error_count=self._cache.error_count,
                )

            # Perform fresh checks
            logger.info("Performing deep health checks on all providers...")
            checked_at = datetime.now(timezone.utc)

            # Run all provider checks concurrently
            results = await asyncio.gather(
                self._check_red(),
                self._check_ops(),
                self._check_youtube(),
                self._check_spotify(),
                return_exceptions=True,
            )

            providers: List[ProviderDeepHealth] = []
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Provider check raised exception: {result}")
                    # Create an error result for failed checks
                    providers.append(ProviderDeepHealth(
                        name="Unknown",
                        status=ProviderHealthStatus.ERROR,
                        configured=False,
                        error=str(result),
                    ))
                else:
                    providers.append(result)

            # Calculate counts
            healthy = sum(1 for p in providers if p.status == ProviderHealthStatus.OK)
            degraded = sum(1 for p in providers if p.status == ProviderHealthStatus.DEGRADED)
            errors = sum(1 for p in providers if p.status == ProviderHealthStatus.ERROR)

            # Determine overall status
            if errors > 0 and healthy == 0:
                overall_status = "unhealthy"
            elif degraded > 0 or errors > 0:
                overall_status = "degraded"
            else:
                overall_status = "healthy"

            response = DeepHealthResponse(
                status=overall_status,
                checked_at=checked_at,
                cache_age_seconds=0,
                providers=providers,
                healthy_count=healthy,
                degraded_count=degraded,
                error_count=errors,
            )

            # Cache the result
            self._cache = response
            self._cache_time = checked_at

            return response

    async def _check_red(self) -> ProviderDeepHealth:
        """Check RED provider connectivity and auth."""
        return await self._check_tracker("RED", "RED_API_KEY", "RED_API_URL")

    async def _check_ops(self) -> ProviderDeepHealth:
        """Check OPS provider connectivity and auth."""
        return await self._check_tracker("OPS", "OPS_API_KEY", "OPS_API_URL")

    async def _check_tracker(
        self, name: str, api_key_env: str, api_url_env: str
    ) -> ProviderDeepHealth:
        """
        Check tracker (RED or OPS) connectivity and auth.

        Uses the index action which is a lightweight auth check.
        """
        api_key = os.environ.get(api_key_env)
        api_url = os.environ.get(api_url_env)

        if not api_key or not api_url:
            return ProviderDeepHealth(
                name=name,
                status=ProviderHealthStatus.UNCONFIGURED,
                configured=False,
                last_check=datetime.now(timezone.utc),
                details={"reason": f"Missing {api_key_env} or {api_url_env}"},
            )

        # Perform the actual API check
        start_time = time.monotonic()
        try:
            import requests

            session = requests.Session()
            session.headers.update({"Authorization": api_key})

            url = f"{api_url.rstrip('/')}/ajax.php?action=index"
            resp = session.get(url, timeout=10)
            latency_ms = int((time.monotonic() - start_time) * 1000)

            if resp.status_code != 200:
                return ProviderDeepHealth(
                    name=name,
                    status=ProviderHealthStatus.ERROR,
                    configured=True,
                    last_check=datetime.now(timezone.utc),
                    latency_ms=latency_ms,
                    error=f"HTTP {resp.status_code}",
                )

            try:
                data = resp.json()
            except ValueError:
                return ProviderDeepHealth(
                    name=name,
                    status=ProviderHealthStatus.ERROR,
                    configured=True,
                    last_check=datetime.now(timezone.utc),
                    latency_ms=latency_ms,
                    error="Invalid JSON response",
                )

            if data.get("status") == "success":
                response_data = data.get("response", {})
                return ProviderDeepHealth(
                    name=name,
                    status=ProviderHealthStatus.OK,
                    configured=True,
                    last_check=datetime.now(timezone.utc),
                    latency_ms=latency_ms,
                    details={
                        "username": response_data.get("username"),
                        "userstats": {
                            "uploaded": response_data.get("userstats", {}).get("uploaded"),
                            "downloaded": response_data.get("userstats", {}).get("downloaded"),
                            "ratio": response_data.get("userstats", {}).get("ratio"),
                        } if response_data.get("userstats") else None,
                    },
                )
            else:
                return ProviderDeepHealth(
                    name=name,
                    status=ProviderHealthStatus.ERROR,
                    configured=True,
                    last_check=datetime.now(timezone.utc),
                    latency_ms=latency_ms,
                    error=data.get("error", "Unknown API error"),
                )

        except requests.Timeout:
            return ProviderDeepHealth(
                name=name,
                status=ProviderHealthStatus.ERROR,
                configured=True,
                last_check=datetime.now(timezone.utc),
                error="Connection timeout",
            )
        except requests.RequestException as e:
            return ProviderDeepHealth(
                name=name,
                status=ProviderHealthStatus.ERROR,
                configured=True,
                last_check=datetime.now(timezone.utc),
                error=f"Connection error: {e}",
            )
        except Exception as e:
            logger.exception(f"Unexpected error checking {name}")
            return ProviderDeepHealth(
                name=name,
                status=ProviderHealthStatus.ERROR,
                configured=True,
                last_check=datetime.now(timezone.utc),
                error=str(e),
            )

    async def _check_youtube(self) -> ProviderDeepHealth:
        """
        Check YouTube connectivity and yt-dlp functionality.

        Tests that yt-dlp can extract info from a known video.
        This validates:
        - yt-dlp is installed and working
        - Network connectivity to YouTube
        - Not blocked by bot detection (when cookies are configured)
        """
        start_time = time.monotonic()

        try:
            import yt_dlp

            # Configure yt-dlp for a quick info extraction only
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": True,  # Don't download, just get metadata
                "skip_download": True,
                "socket_timeout": 10,
            }

            # Add cookies if configured
            cookies_file = os.environ.get("YOUTUBE_COOKIES_FILE")
            cookies_configured = bool(cookies_file and os.path.exists(cookies_file))
            if cookies_configured:
                ydl_opts["cookiefile"] = cookies_file

            # Run in thread pool to not block async loop
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(
                None,
                lambda: self._extract_youtube_info(ydl_opts),
            )

            latency_ms = int((time.monotonic() - start_time) * 1000)

            if info is None:
                return ProviderDeepHealth(
                    name="YouTube",
                    status=ProviderHealthStatus.ERROR,
                    configured=True,
                    last_check=datetime.now(timezone.utc),
                    latency_ms=latency_ms,
                    error="Failed to extract video info",
                    details={"cookies_configured": cookies_configured},
                )

            # Check for signs of bot detection
            if info.get("title") == "[Private video]" or "Sign in" in str(info.get("title", "")):
                return ProviderDeepHealth(
                    name="YouTube",
                    status=ProviderHealthStatus.DEGRADED,
                    configured=True,
                    last_check=datetime.now(timezone.utc),
                    latency_ms=latency_ms,
                    error="Possible bot detection or private video",
                    details={
                        "cookies_configured": cookies_configured,
                        "video_title": info.get("title"),
                    },
                )

            return ProviderDeepHealth(
                name="YouTube",
                status=ProviderHealthStatus.OK,
                configured=True,
                last_check=datetime.now(timezone.utc),
                latency_ms=latency_ms,
                details={
                    "cookies_configured": cookies_configured,
                    "yt_dlp_version": yt_dlp.version.__version__,
                    "test_video_title": info.get("title"),
                },
            )

        except ImportError:
            return ProviderDeepHealth(
                name="YouTube",
                status=ProviderHealthStatus.ERROR,
                configured=False,
                last_check=datetime.now(timezone.utc),
                error="yt-dlp not installed",
            )
        except Exception as e:
            latency_ms = int((time.monotonic() - start_time) * 1000)
            error_str = str(e)

            # Check for specific YouTube errors
            if "Sign in" in error_str or "bot" in error_str.lower():
                return ProviderDeepHealth(
                    name="YouTube",
                    status=ProviderHealthStatus.DEGRADED,
                    configured=True,
                    last_check=datetime.now(timezone.utc),
                    latency_ms=latency_ms,
                    error="Bot detection - cookies may be required",
                    details={"cookies_configured": bool(os.environ.get("YOUTUBE_COOKIES_FILE"))},
                )

            logger.exception("Unexpected error checking YouTube")
            return ProviderDeepHealth(
                name="YouTube",
                status=ProviderHealthStatus.ERROR,
                configured=True,
                last_check=datetime.now(timezone.utc),
                latency_ms=latency_ms,
                error=error_str[:200],  # Truncate long errors
            )

    def _extract_youtube_info(self, ydl_opts: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract info from YouTube test video (runs in thread pool)."""
        import yt_dlp

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(YOUTUBE_TEST_VIDEO, download=False)
        except Exception as e:
            logger.warning(f"YouTube info extraction failed: {e}")
            return None

    async def _check_spotify(self) -> ProviderDeepHealth:
        """
        Check Spotify API connectivity.

        Validates that the Spotify OAuth credentials are working.
        """
        client_id = os.environ.get("SPOTIPY_CLIENT_ID")
        client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET")

        if not client_id or not client_secret:
            return ProviderDeepHealth(
                name="Spotify",
                status=ProviderHealthStatus.UNCONFIGURED,
                configured=False,
                last_check=datetime.now(timezone.utc),
                details={"reason": "Missing SPOTIPY_CLIENT_ID or SPOTIPY_CLIENT_SECRET"},
            )

        start_time = time.monotonic()

        try:
            import spotipy  # noqa: F401 - used in _test_spotify_connection
            from spotipy.oauth2 import SpotifyClientCredentials  # noqa: F401 - used in _test_spotify_connection

            # Run in thread pool to not block async loop
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self._test_spotify_connection(client_id, client_secret),
            )

            latency_ms = int((time.monotonic() - start_time) * 1000)

            if result.get("success"):
                return ProviderDeepHealth(
                    name="Spotify",
                    status=ProviderHealthStatus.OK,
                    configured=True,
                    last_check=datetime.now(timezone.utc),
                    latency_ms=latency_ms,
                    details=result.get("details"),
                )
            else:
                return ProviderDeepHealth(
                    name="Spotify",
                    status=ProviderHealthStatus.ERROR,
                    configured=True,
                    last_check=datetime.now(timezone.utc),
                    latency_ms=latency_ms,
                    error=result.get("error"),
                )

        except ImportError:
            return ProviderDeepHealth(
                name="Spotify",
                status=ProviderHealthStatus.ERROR,
                configured=True,
                last_check=datetime.now(timezone.utc),
                error="spotipy not installed",
            )
        except Exception as e:
            latency_ms = int((time.monotonic() - start_time) * 1000)
            logger.exception("Unexpected error checking Spotify")
            return ProviderDeepHealth(
                name="Spotify",
                status=ProviderHealthStatus.ERROR,
                configured=True,
                last_check=datetime.now(timezone.utc),
                latency_ms=latency_ms,
                error=str(e),
            )

    def _test_spotify_connection(
        self, client_id: str, client_secret: str
    ) -> Dict[str, Any]:
        """Test Spotify connection (runs in thread pool)."""
        try:
            import spotipy
            from spotipy.oauth2 import SpotifyClientCredentials

            auth_manager = SpotifyClientCredentials(
                client_id=client_id,
                client_secret=client_secret,
            )
            sp = spotipy.Spotify(auth_manager=auth_manager)

            # Just search for something simple to validate the connection
            results = sp.search(q="test", type="track", limit=1)

            return {
                "success": True,
                "details": {
                    "auth_type": "client_credentials",
                    "test_search_worked": bool(results.get("tracks", {}).get("items")),
                },
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }


# Singleton instance
_deep_health_service: Optional[DeepHealthService] = None


def get_deep_health_service() -> DeepHealthService:
    """Get the singleton DeepHealthService instance."""
    global _deep_health_service
    if _deep_health_service is None:
        _deep_health_service = DeepHealthService()
    return _deep_health_service
