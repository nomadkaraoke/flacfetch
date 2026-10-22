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


class TestCheckYoutubeRoute:
    """Tests for /check-youtube endpoint logic."""

    def test_check_youtube_request_validation(self):
        """Test check youtube request validation."""
        from flacfetch.api.models import CheckYoutubeRequest

        req = CheckYoutubeRequest(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert req.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

        req = CheckYoutubeRequest(url="-yV25PrHglw")
        assert req.url == "-yV25PrHglw"

    def test_check_youtube_response_available(self):
        """Test check youtube response for available video."""
        from flacfetch.api.models import CheckYoutubeResponse

        resp = CheckYoutubeResponse(
            available=True,
            video_id="dQw4w9WgXcQ",
            title="Rick Astley - Never Gonna Give You Up",
        )
        assert resp.available is True
        assert resp.is_geo_restricted is False

    def test_check_youtube_response_geo_restricted(self):
        """Test check youtube response for geo-restricted video."""
        from flacfetch.api.models import CheckYoutubeResponse

        resp = CheckYoutubeResponse(
            available=False,
            video_id="-yV25PrHglw",
            error="This video is not available in your server's country/region.",
            is_geo_restricted=True,
        )
        assert resp.available is False
        assert resp.is_geo_restricted is True
        assert resp.is_private is False


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


class TestSpotifyBudgetRoutes:
    """POST /download-by-id batch gate, GET /spotify/budget, delete-after-upload."""

    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        from unittest.mock import AsyncMock, patch

        from fastapi.testclient import TestClient

        from flacfetch.api.main import create_app
        from flacfetch.api.services import spotify_budget
        from flacfetch.api.services.download_manager import DownloadManager

        monkeypatch.setenv("FLACFETCH_SPOTIFY_LEDGER", str(tmp_path / "ledger.sqlite"))
        monkeypatch.delenv("FLACFETCH_API_KEY", raising=False)
        spotify_budget.reset_spotify_ledger()
        dm = DownloadManager(download_dir=str(tmp_path / "dl"))
        dm.execute_download_by_id = AsyncMock()  # don't run real downloads
        with patch("flacfetch.api.routes.download.get_download_manager", return_value=dm):
            yield TestClient(create_app()), dm, spotify_budget.get_spotify_ledger()
        spotify_budget.reset_spotify_ledger()

    @staticmethod
    def _body(**kw):
        return {"source_name": "Spotify", "source_id": "trk1", **kw}

    def test_batch_requires_expected_duration(self, env):
        client, _, _ = env
        r = client.post("/download-by-id", json=self._body(priority="batch"))
        assert r.status_code == 400
        assert "expected_duration_s" in r.json()["detail"]

    def test_batch_429_shape(self, env):
        client, dm, ledger = env
        ok = client.post("/download-by-id", json=self._body(priority="batch", expected_duration_s=240))
        assert ok.status_code == 200
        r = client.post("/download-by-id", json=self._body(priority="batch", expected_duration_s=240))
        assert r.status_code == 429
        detail = r.json()["detail"]
        assert detail["code"] == "batch_gap"
        assert detail["retry_after_s"] >= 1
        assert r.headers["Retry-After"] == str(detail["retry_after_s"])
        assert len(dm.list_downloads()) == 1  # refused task discarded
        assert ledger.usage().batch_in_flight == 1

    def test_budget_endpoint(self, env):
        client, _, ledger = env
        r = client.get("/spotify/budget")
        assert r.status_code == 200
        body = r.json()
        assert body["batch_allowed_now"] is True and body["retry_after_s"] == 0
        assert body["batch_budget_s"] == 43200 and body["hard_cap_s"] == 86400
        assert body["window_s"] == 86400 and body["total_s"] == 0
        client.post("/download-by-id", json=self._body(priority="batch", expected_duration_s=240))
        body = client.get("/spotify/budget").json()
        assert body["batch_in_flight"] == 1 and body["batch_s"] == 240
        assert body["batch_allowed_now"] is False

    def test_budget_endpoint_requires_api_key(self, env, monkeypatch):
        client, _, _ = env
        monkeypatch.setenv("FLACFETCH_API_KEY", "k")
        assert client.get("/spotify/budget").status_code == 401
        assert client.get("/spotify/budget", headers={"X-API-Key": "k"}).status_code == 200

    def test_interactive_never_refused(self, env, monkeypatch):
        client, _, ledger = env
        # exhaust the batch budget and the hard cap
        ledger.reserve("old", "interactive", 90000)
        r = client.post("/download-by-id", json=self._body(expected_duration_s=200))
        assert r.status_code == 200
        r2 = client.post("/download-by-id", json=self._body())  # default 300 s reservation
        assert r2.status_code == 200
        assert ledger.usage().interactive_in_flight == 3
        # batch is refused while interactive is active
        b = client.post("/download-by-id", json=self._body(priority="batch", expected_duration_s=240))
        assert b.status_code == 429

    def test_non_spotify_batch_not_gated(self, env):
        client, _, ledger = env
        r = client.post("/download-by-id", json={"source_name": "YouTube", "source_id": "x", "priority": "batch"})
        assert r.status_code == 200
        assert ledger.usage().total_s == 0

    def test_expected_duration_bounds(self, env):
        client, _, _ = env
        r = client.post("/download-by-id", json=self._body(expected_duration_s=1201))
        assert r.status_code == 422

    def test_delete_local_after_upload(self, env, tmp_path):
        import asyncio
        from unittest.mock import AsyncMock, Mock, patch

        from flacfetch.api.services.download_manager import DownloadManager

        client, _, ledger = env
        dm = DownloadManager(download_dir=str(tmp_path / "dl"))
        sub = tmp_path / "dl" / "sub"
        sub.mkdir(parents=True)
        f = sub / "song.flac"
        f.write_bytes(b"x")

        from flacfetch.downloaders.spotify import CapturedPath

        task = dm.create_download_by_id(
            source_name="Spotify", source_id="t", upload_to_gcs=True,
            gcs_destination="voice-pool/audio/x", delete_local_after_upload=True,
        )
        ledger.reserve(task.download_id, "batch", 240)
        task.spotify_reserved = True
        mgr = Mock()
        mgr.download_by_id.return_value = CapturedPath(str(f), 200.0)

        async def run():
            with patch.object(dm, "_get_fetch_manager", return_value=mgr), \
                    patch.object(dm, "_upload_to_gcs", AsyncMock(return_value="gs://b/x/song.flac")):
                await dm.execute_download_by_id(task.download_id)

        asyncio.new_event_loop().run_until_complete(run())
        assert not f.exists() and not sub.exists()
        assert (tmp_path / "dl").exists()
        assert ledger.usage().batch_s == 200.0 and ledger.usage().batch_in_flight == 0

    def test_failed_download_releases_reservation(self, env, tmp_path):
        import asyncio
        from unittest.mock import Mock, patch

        from flacfetch.api.services.download_manager import DownloadManager

        _, _, ledger = env
        dm = DownloadManager(download_dir=str(tmp_path / "dl"))
        task = dm.create_download_by_id(source_name="Spotify", source_id="t")
        ledger.reserve(task.download_id, "batch", 240)
        task.spotify_reserved = True
        mgr = Mock()
        mgr.download_by_id.side_effect = RuntimeError("no audio")

        async def run():
            with patch.object(dm, "_get_fetch_manager", return_value=mgr):
                await dm.execute_download_by_id(task.download_id)

        asyncio.new_event_loop().run_until_complete(run())
        assert ledger.usage().total_s == 0

    def test_ledger_error_interactive_fails_open(self, env):
        from unittest.mock import patch

        client, dm, _ = env
        with patch("flacfetch.api.routes.download.get_spotify_ledger", side_effect=OSError("disk full")):
            r = client.post("/download-by-id", json=self._body())
        assert r.status_code == 200
        assert len(dm.list_downloads()) == 1
        assert dm.list_downloads()[0].spotify_reserved is False

    def test_ledger_error_batch_fails_closed_503(self, env):
        from unittest.mock import patch

        client, dm, _ = env
        with patch("flacfetch.api.routes.download.get_spotify_ledger", side_effect=OSError("disk full")):
            r = client.post("/download-by-id", json=self._body(priority="batch", expected_duration_s=240))
        assert r.status_code == 503
        assert r.json()["detail"]["code"] == "budget_unavailable"
        assert r.headers["Retry-After"] == "60"
        assert dm.list_downloads() == []

    def test_budget_endpoint_503_when_ledger_unavailable(self, env):
        from unittest.mock import patch

        client, _, _ = env
        with patch("flacfetch.api.routes.spotify.get_spotify_ledger", side_effect=OSError("disk full")):
            r = client.get("/spotify/budget")
        assert r.status_code == 503
        assert r.json()["detail"] == {"code": "budget_unavailable", "retry_after_s": 60}
        assert r.headers["Retry-After"] == "60"

    @staticmethod
    def _run(dm, mgr, download_id, upload):
        import asyncio
        from unittest.mock import patch

        async def go():
            with patch.object(dm, "_get_fetch_manager", return_value=mgr), \
                    patch.object(dm, "_upload_to_gcs", upload):
                await dm.execute_download_by_id(download_id)

        asyncio.new_event_loop().run_until_complete(go())

    def test_concurrent_batch_same_filename_use_own_dirs(self, env, tmp_path):
        import asyncio
        from unittest.mock import patch

        from flacfetch.api.services.download_manager import DownloadManager
        from flacfetch.downloaders.spotify import CapturedPath

        _, _, ledger = env
        dm = DownloadManager(download_dir=str(tmp_path / "dl"))
        tasks = [
            dm.create_download_by_id(
                source_name="Spotify", source_id=f"t{i}", output_filename="original",
                upload_to_gcs=True, gcs_destination="p", delete_local_after_upload=True)
            for i in range(2)
        ]
        uploaded = {}

        class Mgr:
            def download_by_id(self, output_path, output_filename, **kw):
                import os
                os.makedirs(output_path, exist_ok=True)
                f = os.path.join(output_path, output_filename + ".flac")
                open(f, "w").write(os.path.basename(output_path))
                return CapturedPath(f, 100.0)

        async def upload(path, dest):
            await asyncio.sleep(0.05)
            uploaded[path] = open(path).read()  # raises if another task deleted/replaced it
            return "gs://b/x"

        async def go():
            with patch.object(dm, "_get_fetch_manager", return_value=Mgr()), \
                    patch.object(dm, "_upload_to_gcs", upload):
                await asyncio.gather(*(dm.execute_download_by_id(t.download_id) for t in tasks))

        asyncio.new_event_loop().run_until_complete(go())
        assert len(uploaded) == 2
        for path, content in uploaded.items():
            assert content == tasks[0].download_id or content == tasks[1].download_id
            assert content in path
        assert all(dm.get_download(t.download_id).status.value == "complete" for t in tasks)
        assert (tmp_path / "dl").exists() and list((tmp_path / "dl").iterdir()) == []

    def test_upload_failure_after_capture_settles_captured_seconds(self, env, tmp_path):
        from unittest.mock import AsyncMock, Mock

        from flacfetch.api.services.download_manager import DownloadManager
        from flacfetch.downloaders.spotify import CapturedPath

        _, _, ledger = env
        dm = DownloadManager(download_dir=str(tmp_path / "dl"))
        task = dm.create_download_by_id(
            source_name="Spotify", source_id="t", upload_to_gcs=True, gcs_destination="p")
        ledger.reserve(task.download_id, "batch", 240)
        task.spotify_reserved = True
        mgr = Mock()
        mgr.download_by_id.return_value = CapturedPath("/tmp/none.flac", 123.0)
        self._run(dm, mgr, task.download_id, AsyncMock(side_effect=RuntimeError("gcs down")))
        u = ledger.usage()
        assert u.batch_s == 123.0 and u.batch_in_flight == 0

    def test_delete_flag_ignored_for_torrent_providers(self, env, tmp_path):
        from unittest.mock import AsyncMock, Mock

        from flacfetch.api.services.download_manager import DownloadManager

        dm = DownloadManager(download_dir=str(tmp_path / "dl"))
        f = tmp_path / "seed.flac"
        f.write_bytes(b"x")
        task = dm.create_download_by_id(
            source_name="RED", source_id="1", upload_to_gcs=True, gcs_destination="p",
            delete_local_after_upload=True)
        mgr = Mock()
        mgr.download_by_id.return_value = str(f)
        self._run(dm, mgr, task.download_id, AsyncMock(return_value="gs://b/x"))
        assert f.exists()
        assert mgr.download_by_id.call_args.kwargs["output_path"] == dm.download_dir
