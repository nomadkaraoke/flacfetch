"""
Tests for the /search endpoint's concurrency behavior.

The provider sweep is synchronous and slow (40s+ uncached), so the route must:
- run it off the event loop (dedicated single-thread executor)
- coalesce identical in-flight searches into one sweep
- keep serving other requests (e.g. cache hits) while a sweep runs
"""
import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from flacfetch.api.models import SearchRequest
from flacfetch.api.routes import search as search_route
from flacfetch.api.routes.search import _inflight_key, search_audio


def make_release(title="Waterloo", artist="ABBA", provider="RED"):
    """Minimal release object with the attributes the response builder reads."""
    return SimpleNamespace(
        title=title,
        artist=artist,
        source_name=provider,
        provider=provider,
        quality=None,
        seeders=10,
        size_bytes=1024,
        target_file=None,
        target_file_size=None,
        year=1974,
        label=None,
        edition_info=None,
        release_type=None,
        channel=None,
        view_count=None,
        duration_seconds=180,
        match_score=0.9,
        formatted_size="1 KB",
        formatted_duration="3:00",
        source_id="12345",
    )


def make_manager(fetch_manager):
    manager = Mock()
    manager._get_fetch_manager.return_value = fetch_manager
    manager.cache_search = Mock()
    return manager


def make_cache_service(cached=None):
    cache_service = AsyncMock()
    cache_service.get_cached_search.return_value = cached
    cache_service.cache_search_results.return_value = True
    return cache_service


@pytest.fixture(autouse=True)
def clear_inflight():
    """Each test starts with no in-flight searches."""
    search_route._inflight_searches.clear()
    yield
    search_route._inflight_searches.clear()


class TestInflightKey:
    """The coalescing key must match the search cache's normalization."""

    def test_case_insensitive(self):
        assert _inflight_key("The Kane Gang", "Closest Thing To Heaven", False) == \
            _inflight_key("the kane gang", "closest thing to heaven", False)

    def test_whitespace_normalized(self):
        assert _inflight_key("ABBA", "Waterloo", False) == \
            _inflight_key(" ABBA ", "Waterloo  ", False)

    def test_exhaustive_flag_differentiates(self):
        assert _inflight_key("ABBA", "Waterloo", False) != \
            _inflight_key("ABBA", "Waterloo", True)

    def test_different_songs_differ(self):
        assert _inflight_key("ABBA", "Waterloo", False) != \
            _inflight_key("ABBA", "SOS", False)


class TestSearchEndpoint:
    """Handler-level tests (services patched, handler called directly)."""

    @pytest.mark.asyncio
    async def test_search_success_runs_sweep_off_loop(self):
        """Uncached search runs the sweep in the executor and returns results."""
        fetch_manager = Mock()
        fetch_manager.providers = []
        sweep_thread_names = []

        def fake_search(query):
            sweep_thread_names.append(threading.current_thread().name)
            return [make_release()]

        fetch_manager.search.side_effect = fake_search

        with patch.object(search_route, "get_download_manager", return_value=make_manager(fetch_manager)), \
             patch.object(search_route, "get_search_cache_service", return_value=make_cache_service()):
            response = await search_audio(SearchRequest(artist="ABBA", title="Waterloo"), api_key="k")

        assert response.results_count == 1
        assert response.results[0].title == "Waterloo"
        # Sweep ran in the dedicated executor thread, not the event loop thread
        assert sweep_thread_names[0].startswith("provider-search")

    @pytest.mark.asyncio
    async def test_identical_concurrent_searches_coalesce(self):
        """Two identical searches in flight → one provider sweep, same results."""
        fetch_manager = Mock()
        fetch_manager.providers = []
        release_gate = threading.Event()
        call_count = 0

        def slow_search(query):
            nonlocal call_count
            call_count += 1
            assert release_gate.wait(timeout=10), "test gate never released"
            return [make_release()]

        fetch_manager.search.side_effect = slow_search

        with patch.object(search_route, "get_download_manager", return_value=make_manager(fetch_manager)), \
             patch.object(search_route, "get_search_cache_service", return_value=make_cache_service()):
            # Same normalized key despite different capitalization
            task1 = asyncio.create_task(
                search_audio(SearchRequest(artist="The Kane Gang", title="Closest Thing to Heaven"), api_key="k")
            )
            task2 = asyncio.create_task(
                search_audio(SearchRequest(artist="The Kane Gang", title="Closest Thing To Heaven"), api_key="k")
            )
            # Let both handlers reach the coalescing point
            await asyncio.sleep(0.1)
            release_gate.set()
            resp1, resp2 = await asyncio.gather(task1, task2)

        assert call_count == 1
        assert resp1.results_count == 1
        assert resp2.results_count == 1
        assert search_route._inflight_searches == {}

    @pytest.mark.asyncio
    async def test_different_searches_do_not_coalesce(self):
        """Different songs each get their own sweep."""
        fetch_manager = Mock()
        fetch_manager.providers = []
        fetch_manager.search.return_value = [make_release()]

        with patch.object(search_route, "get_download_manager", return_value=make_manager(fetch_manager)), \
             patch.object(search_route, "get_search_cache_service", return_value=make_cache_service()):
            await search_audio(SearchRequest(artist="ABBA", title="Waterloo"), api_key="k")
            await search_audio(SearchRequest(artist="ABBA", title="SOS"), api_key="k")

        assert fetch_manager.search.call_count == 2

    @pytest.mark.asyncio
    async def test_cache_hit_served_while_sweep_in_flight(self):
        """The event loop stays responsive: a cache-hit search completes while
        a slow sweep for a different song is still running."""
        fetch_manager = Mock()
        fetch_manager.providers = []
        release_gate = threading.Event()
        fetch_manager.search.side_effect = lambda q: (
            release_gate.wait(timeout=10) and [make_release()]
        )

        with patch.object(search_route, "get_download_manager", return_value=make_manager(fetch_manager)):
            with patch.object(search_route, "get_search_cache_service", return_value=make_cache_service()):
                slow_task = asyncio.create_task(
                    search_audio(SearchRequest(artist="ABBA", title="Waterloo"), api_key="k")
                )
                await asyncio.sleep(0.05)  # sweep is now blocked in the executor

            cached_release = [make_release(title="SOS")]
            with patch.object(search_route, "get_search_cache_service", return_value=make_cache_service(cached=cached_release)):
                fast_response = await asyncio.wait_for(
                    search_audio(SearchRequest(artist="ABBA", title="SOS"), api_key="k"),
                    timeout=2,
                )

            assert fast_response.results[0].title == "SOS"
            release_gate.set()
            slow_response = await slow_task
            assert slow_response.results_count == 1

    @pytest.mark.asyncio
    async def test_sweep_error_returns_500_and_clears_inflight(self):
        """A failed sweep returns 500 and doesn't leave a stuck in-flight entry."""
        from fastapi import HTTPException

        fetch_manager = Mock()
        fetch_manager.providers = []
        fetch_manager.search.side_effect = RuntimeError("provider exploded")

        with patch.object(search_route, "get_download_manager", return_value=make_manager(fetch_manager)), \
             patch.object(search_route, "get_search_cache_service", return_value=make_cache_service()):
            with pytest.raises(HTTPException) as exc_info:
                await search_audio(SearchRequest(artist="ABBA", title="Waterloo"), api_key="k")

        assert exc_info.value.status_code == 500
        assert search_route._inflight_searches == {}

        # A later retry runs a fresh sweep (errors are not cached)
        fetch_manager.search.side_effect = None
        fetch_manager.search.return_value = [make_release()]
        with patch.object(search_route, "get_download_manager", return_value=make_manager(fetch_manager)), \
             patch.object(search_route, "get_search_cache_service", return_value=make_cache_service()):
            response = await search_audio(SearchRequest(artist="ABBA", title="Waterloo"), api_key="k")
        assert response.results_count == 1

    @pytest.mark.asyncio
    async def test_no_results_returns_404(self):
        from fastapi import HTTPException

        fetch_manager = Mock()
        fetch_manager.providers = []
        fetch_manager.search.return_value = []

        with patch.object(search_route, "get_download_manager", return_value=make_manager(fetch_manager)), \
             patch.object(search_route, "get_search_cache_service", return_value=make_cache_service()):
            with pytest.raises(HTTPException) as exc_info:
                await search_audio(SearchRequest(artist="Unknown", title="Nothing"), api_key="k")

        assert exc_info.value.status_code == 404
        assert search_route._inflight_searches == {}

    @pytest.mark.asyncio
    async def test_provider_config_applied_inside_executor(self):
        """Exhaustive flag configures providers atomically with the sweep."""
        provider = Mock(spec=["early_termination", "search_limit", "name"])
        provider.name = "RED"
        fetch_manager = Mock()
        fetch_manager.providers = [provider]
        fetch_manager.search.return_value = [make_release()]

        with patch.object(search_route, "get_download_manager", return_value=make_manager(fetch_manager)), \
             patch.object(search_route, "get_search_cache_service", return_value=make_cache_service()):
            await search_audio(SearchRequest(artist="ABBA", title="Waterloo", exhaustive=True), api_key="k")

        assert provider.early_termination is False
        assert provider.search_limit == 20
