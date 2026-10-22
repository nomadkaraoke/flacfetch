"""Album search: map an album's tracklist onto one torrent's files (one search per album)."""

import asyncio
import threading
from unittest.mock import Mock, patch

import pytest
import requests
from fastapi import HTTPException

from flacfetch.api.models import AlbumSearchRequest, AlbumSearchTrack
from flacfetch.api.routes import search as search_route
from flacfetch.core.album_match import (
    AlbumCandidate,
    AlbumTrack,
    TrackFile,
    file_track_number,
    match_album_tracks,
    parse_file_list,
)
from flacfetch.core.categorize import BEST_CHOICE_MIN_SEEDERS, is_excellent_match
from flacfetch.core.models import AudioFormat, MediaSource, Quality, Release
from flacfetch.providers.gazelle import GazelleProvider


def file_list(*names, size=30_000_000):
    """Gazelle fileList string for the given names."""
    return "|||".join(f"{n}{{{{{{{size}}}}}}}" for n in names)


def tracks(*titles):
    return [AlbumTrack(key=f"k{i}", title=t, position=i) for i, t in enumerate(titles, start=1)]


# =============================================================================
# Pure matching
# =============================================================================

class TestParseFileList:
    def test_audio_only_with_sizes(self):
        files = parse_file_list(file_list("01 Glass Harbour.flac", "cover.jpg", "rip.log", "02 Lantern.flac"))
        assert [f[0] for f in files] == ["01 Glass Harbour.flac", "02 Lantern.flac"]
        assert files[0][1] == 30_000_000

    def test_html_entities_decoded(self):
        files = parse_file_list(file_list("03 Salt &amp; Static.flac"))
        assert files[0][0] == "03 Salt & Static.flac"

    def test_empty(self):
        assert parse_file_list("") == []

    def test_malformed_size(self):
        assert parse_file_list("01 Glass Harbour.flac{{{abc}}}") == [("01 Glass Harbour.flac", None)]


class TestFileTrackNumber:
    @pytest.mark.parametrize("name,expected", [
        ("06 Hollow Crown.flac", 6),
        ("06 - Hollow Crown.flac", 6),
        ("06. Hollow Crown.flac", 6),
        ("1-06 Hollow Crown.flac", 6),
        ("CD2/11 Hollow Crown.flac", 11),
        ("Hollow Crown.flac", None),
        ("A1 Hollow Crown.flac", None),
    ])
    def test_variants(self, name, expected):
        assert file_track_number(name) == expected


class TestMatchAlbumTracks:
    def test_maps_every_track(self):
        files = parse_file_list(file_list(
            "01 Glass Harbour.flac", "02 Lantern (feat. Mira Vale).flac", "03 Northbound.flac",
        ))
        result = match_album_tracks(files, tracks("Glass Harbour", "Lantern", "Northbound"))
        assert {k: f.target_file for k, f in result.items()} == {
            "k1": "01 Glass Harbour.flac",
            "k2": "02 Lantern (feat. Mira Vale).flac",
            "k3": "03 Northbound.flac",
        }

    def test_disc_track_prefix_and_featuring_variants(self):
        # Tracker WEB rip uses "1-06" and "feat."; requested title uses "ft." in brackets.
        files = parse_file_list(file_list("1-06 Hollow Crown (feat. Tempo Kid).flac"))
        result = match_album_tracks(files, [AlbumTrack("k", "Hollow Crown (ft. Tempo Kid)", 6)])
        assert result["k"].target_file == "1-06 Hollow Crown (feat. Tempo Kid).flac"

    def test_exact_title_beats_remix_for_short_common_titles(self):
        files = parse_file_list(file_list(
            "13 Hours (Night Remix).flac", "07 Hours.flac", "14 Hours Apart.flac",
        ))
        result = match_album_tracks(files, [AlbumTrack("k", "Hours", 7)])
        assert result["k"].target_file == "07 Hours.flac"
        assert result["k"].match_score == 1.0

    def test_one_file_per_track(self):
        # Two requested tracks that both match one file well: only one gets it.
        files = parse_file_list(file_list("05 Ember.flac"))
        result = match_album_tracks(files, [AlbumTrack("a", "Ember", 5), AlbumTrack("b", "Ember", 9)])
        assert list(result) == ["a"]  # position tie-break picks track 5

    def test_reprise_resolved_by_position(self):
        files = parse_file_list(file_list("02 Ember.flac", "11 Ember.flac"))
        result = match_album_tracks(files, [AlbumTrack("late", "Ember", 11), AlbumTrack("early", "Ember", 2)])
        assert result["early"].target_file == "02 Ember.flac"
        assert result["late"].target_file == "11 Ember.flac"

    def test_unmatched_track_left_out(self):
        files = parse_file_list(file_list("01 Glass Harbour.flac"))
        result = match_album_tracks(files, tracks("Glass Harbour", "Bonus Thing Entirely"))
        assert list(result) == ["k1"]

    def test_position_bonus_cannot_rescue_a_wrong_title(self):
        files = parse_file_list(file_list("04 Something Else Entirely.flac"))
        assert match_album_tracks(files, [AlbumTrack("k", "Lantern", 4)]) == {}

    def test_files_in_disc_folders(self):
        files = parse_file_list(file_list("CD1/01 Glass Harbour.flac", "CD2/01 Lantern.flac"))
        result = match_album_tracks(files, [AlbumTrack("a", "Glass Harbour", 1), AlbumTrack("b", "Lantern", 1)])
        assert result["a"].target_file == "CD1/01 Glass Harbour.flac"
        assert result["b"].target_file == "CD2/01 Lantern.flac"


class TestWrongRecordingGuards:
    """Album mode resolves many tracks at once; a plausible-but-wrong file is the
    worst outcome (the wrong recording becomes karaoke). Better no match."""

    def _m(self, files, trs):
        return {k: f.target_file for k, f in match_album_tracks(parse_file_list(file_list(*files)), trs).items()}

    def test_live_request_gets_the_live_file_not_the_studio_one(self):
        assert self._m(["01 Ember.flac", "02 Ember (Live).flac"], [AlbumTrack("l", "Ember (Live)")]) == {
            "l": "02 Ember (Live).flac"}

    def test_live_and_studio_requested_together_are_not_swapped(self):
        got = self._m(["01 Ember.flac", "02 Ember (Live).flac"],
                      [AlbumTrack("l", "Ember (Live)", 1), AlbumTrack("s", "Ember", 2)])
        assert got == {"l": "02 Ember (Live).flac", "s": "01 Ember.flac"}

    def test_repeated_positions_across_discs(self):
        got = self._m(["CD1/06 Ember.flac", "CD2/06 Ember (Demo).flac"], [AlbumTrack("d", "Ember (Demo)", 6)])
        assert got == {"d": "CD2/06 Ember (Demo).flac"}

    def test_remix_request_never_matches_the_original(self):
        assert self._m(["01 Ember.flac"], [AlbumTrack("r", "Ember (Night Remix)", 1)]) == {}

    def test_studio_request_never_matches_only_a_live_file(self):
        assert self._m(["01 Ember (Live).flac"], [AlbumTrack("s", "Ember", 1)]) == {}

    @pytest.mark.parametrize("request_title,file_name,position", [
        ("Hope", "01 Home.flac", 2),                        # fuzzy only, position disagrees
        ("Run", "03 Fun.flac", 4),
        ("Tide", "05 Tide Is Turning Now.flac", 3),          # contained, weak, position disagrees
    ])
    def test_weak_matches_without_position_agreement_rejected(self, request_title, file_name, position):
        assert self._m([file_name], [AlbumTrack("k", request_title, position)]) == {}

    def test_contained_title_accepted_when_position_agrees(self):
        assert self._m(["05 Tide Is Turning Now.flac"], [AlbumTrack("k", "Tide", 5)]) == {
            "k": "05 Tide Is Turning Now.flac"}

    def test_title_words_that_look_like_version_markers(self):
        assert self._m(["05 Live Wire Mix Tape.flac"], [AlbumTrack("k", "Live Wire Mix Tape", 5)]) == {
            "k": "05 Live Wire Mix Tape.flac"}

    def test_neutral_suffixes_ignored_on_both_sides(self):
        got = self._m(["06 Hollow Crown (feat. Tempo Kid) [Remastered].flac"], [AlbumTrack("k", "Hollow Crown", None)])
        assert got == {"k": "06 Hollow Crown (feat. Tempo Kid) [Remastered].flac"}

    @pytest.mark.parametrize("suffix", ["(A Cappella)", "(Acappella)", "(a capella)"])
    def test_a_cappella_is_a_version(self, suffix):
        assert self._m([f"05 Ember {suffix}.flac"], [AlbumTrack("k", "Ember", 5)]) == {}

    def test_maximum_cardinality_assignment(self):
        # Greedy alone gives "01 Track.flac" to "Track" and strands "Tracks", whose
        # only acceptable file it is; augmenting moves "Track" to its other file.
        files = ["01 Track.flac", "03 Track.flac"]
        got = self._m(files, [AlbumTrack("t", "Track", 1), AlbumTrack("s", "Tracks", 1)])
        assert set(got) == {"t", "s"}
        assert got["s"] == "01 Track.flac" and got["t"] == "03 Track.flac"

    def test_dots_in_titles_are_not_extensions(self):
        assert self._m(["05 Mr. Lantern.flac"], [AlbumTrack("k", "Mr. Lantern", 5)]) == {"k": "05 Mr. Lantern.flac"}


# =============================================================================
# Gazelle provider
# =============================================================================

class FakeGazelle(GazelleProvider):
    @property
    def name(self):
        return "RED"

    def search(self, query):
        return []


def torrent(tid, files, media="CD", fmt="FLAC", encoding="Lossless", seeders=31):
    return {
        "id": tid, "media": media, "format": fmt, "encoding": encoding, "seeders": seeders,
        "size": 300_000_000, "fileList": file_list(*files), "remastered": False,
    }


def group_response(name, torrents, release_type=1):
    return {
        "status": "success",
        "response": {
            "group": {
                "name": name, "year": 2011, "releaseType": release_type,
                "musicInfo": {"artists": [{"name": "Test Artist"}]},
            },
            "torrents": torrents,
        },
    }


def json_resp(payload):
    r = Mock(status_code=200)
    r.json.return_value = payload
    return r


@pytest.fixture
def provider():
    with patch("flacfetch.providers.gazelle.time.sleep"):
        yield FakeGazelle(api_key="k", base_url="https://tracker.example", cache_subdir="test-album")


class TestGazelleSearchAlbum:
    ALBUM_FILES = ["01 Glass Harbour.flac", "02 Lantern.flac", "03 Northbound.flac"]

    def test_one_browse_then_group_details(self, provider):
        browse = {"status": "success", "response": {"results": [
            {"groupId": 7, "torrents": [{"torrentId": 70, "canUseToken": True}]},
        ]}}
        group = group_response("Paper Lanterns", [
            torrent(70, self.ALBUM_FILES, media="CD", seeders=31),
            torrent(71, self.ALBUM_FILES, media="WEB", seeders=15),
            torrent(72, self.ALBUM_FILES, fmt="MP3", encoding="320"),  # lossy: dropped
        ])
        provider.session.get = Mock(side_effect=[json_resp(browse), json_resp(group)])

        candidates = provider.search_album("Test Artist", "Paper Lanterns",
                                           tracks("Glass Harbour", "Lantern", "Northbound"))

        browse_params = provider.session.get.call_args_list[0].kwargs["params"]
        assert browse_params["groupname"] == "Paper Lanterns"
        assert browse_params["artistname"] == "Test Artist"
        assert "filelist" not in browse_params
        assert [c.release.source_id for c in candidates] == ["70", "71"]
        best = candidates[0]
        assert best.matched_count == 3
        assert best.track_files["k2"].target_file == "02 Lantern.flac"
        assert best.release.title == "Paper Lanterns"
        assert best.release.release_type == "Album"
        assert best.release.can_use_token is True
        assert best.release.target_file is None  # torrent-level release

    def test_group_limit(self, provider):
        browse = {"status": "success", "response": {"results": [{"groupId": g} for g in range(1, 10)]}}
        group = group_response("Paper Lanterns", [torrent(70, self.ALBUM_FILES)])
        provider.session.get = Mock(side_effect=[json_resp(browse)] + [json_resp(group)] * 9)
        provider.search_album("Test Artist", "Paper Lanterns", tracks("Glass Harbour"), max_groups=2)
        assert provider.session.get.call_count == 3  # browse + 2 groups

    def test_torrents_covering_nothing_are_dropped(self, provider):
        browse = {"status": "success", "response": {"results": [{"groupId": 7}]}}
        group = group_response("Paper Lanterns", [torrent(70, ["01 Unrelated Song.flac"])])
        provider.session.get = Mock(side_effect=[json_resp(browse), json_resp(group)])
        assert provider.search_album("Test Artist", "Paper Lanterns", tracks("Glass Harbour")) == []

    def test_browse_http_failure_raises(self, provider):
        """A tracker outage must reach the route as an error (502), not 'not found' (404)."""
        provider.session.get = Mock(return_value=Mock(status_code=500, text="boom"))
        with pytest.raises(RuntimeError):
            provider.search_album("Test Artist", "Paper Lanterns", tracks("Glass Harbour"))

    def test_browse_connection_failure_propagates(self, provider):
        provider.session.get = Mock(side_effect=requests.ConnectionError("tracker down"))
        with pytest.raises(requests.ConnectionError):
            provider.search_album("Test Artist", "Paper Lanterns", tracks("Glass Harbour"))

    def test_all_group_fetches_failing_raises(self, provider):
        browse = {"status": "success", "response": {"results": [{"groupId": 1}, {"groupId": 2}]}}
        provider.session.get = Mock(side_effect=[json_resp(browse), RuntimeError("x"), RuntimeError("y")])
        with pytest.raises(RuntimeError):
            provider.search_album("Test Artist", "Paper Lanterns", tracks("Glass Harbour"))

    def test_no_groups_is_empty_not_error(self, provider):
        provider.session.get = Mock(return_value=json_resp({"status": "success", "response": {"results": []}}))
        assert provider.search_album("Test Artist", "Paper Lanterns", tracks("Glass Harbour")) == []

    def test_track_search_browse_failure_still_returns_empty(self, provider):
        from flacfetch.core.models import TrackQuery
        provider.session.get = Mock(return_value=Mock(status_code=500, text="boom"))
        assert provider._search_browse(TrackQuery(artist="Test Artist", title="Glass Harbour")) == []

    def test_bad_group_does_not_sink_others(self, provider):
        browse = {"status": "success", "response": {"results": [{"groupId": 1}, {"groupId": 2}]}}
        good = group_response("Paper Lanterns", [torrent(70, self.ALBUM_FILES)])
        provider.session.get = Mock(side_effect=[json_resp(browse), RuntimeError("timeout"), json_resp(good)])
        candidates = provider.search_album("Test Artist", "Paper Lanterns", tracks("Glass Harbour"))
        assert [c.release.source_id for c in candidates] == ["70"]

    def test_album_name_sanitized_for_sphinx(self, provider):
        provider.session.get = Mock(return_value=json_resp({"status": "success", "response": {"results": []}}))
        provider.search_album("Test Artist", "Live!? (Deluxe)", tracks("Glass Harbour"))
        assert provider.session.get.call_args.kwargs["params"]["groupname"] == "Live Deluxe"


# =============================================================================
# Route
# =============================================================================

def make_candidate(source_id, matched, seeders=20, media=MediaSource.CD):
    release = Release(
        title="Paper Lanterns", artist="Test Artist", source_name="RED",
        quality=Quality(format=AudioFormat.FLAC, bit_depth=16, media=media),
        seeders=seeders, size_bytes=300_000_000, year=2011, release_type="Album", source_id=source_id,
    )
    files = {f"k{i}": TrackFile(f"{i:02d} Track.flac", 1000, 1.0) for i in range(1, matched + 1)}
    return AlbumCandidate(release=release, track_files=files)


def manager_with(providers):
    fetch_manager = Mock()
    fetch_manager.providers = providers
    manager = Mock()
    manager._get_fetch_manager.return_value = fetch_manager
    return manager


def album_request(n=3):
    return AlbumSearchRequest(
        artist="Test Artist", album="Paper Lanterns",
        tracks=[AlbumSearchTrack(key=f"k{i}", title=f"Track {i}", position=i) for i in range(1, n + 1)],
    )


@pytest.fixture(autouse=True)
def clear_album_inflight():
    search_route._inflight_album_searches.clear()
    yield
    search_route._inflight_album_searches.clear()


class TestAlbumSearchRequest:
    def test_duplicate_track_keys_rejected(self):
        with pytest.raises(ValueError):
            AlbumSearchRequest(artist="A", album="B", tracks=[
                AlbumSearchTrack(key="k", title="One"), AlbumSearchTrack(key="k", title="Two"),
            ])


class TestAlbumSearchRoute:
    @pytest.mark.asyncio
    async def test_ranks_by_coverage_then_non_vinyl_then_seeders(self):
        red = Mock(spec=["name", "search_album"])
        red.name = "RED"
        red.search_album.return_value = [
            make_candidate("partial", matched=2, seeders=500),
            make_candidate("vinyl", matched=3, seeders=400, media=MediaSource.VINYL),
            make_candidate("cd-low", matched=3, seeders=12),
        ]
        ops = Mock(spec=["name", "search_album"])
        ops.name = "OPS"
        ops.search_album.return_value = [make_candidate("web-high", matched=3, seeders=40, media=MediaSource.WEB)]
        youtube = Mock(spec=["name", "search"])  # no album search: skipped
        youtube.name = "YouTube"

        with patch.object(search_route, "get_download_manager", return_value=manager_with([red, ops, youtube])):
            resp = await search_route.search_album(album_request(), api_key="k")

        assert [r.source_id for r in resp.results] == ["web-high", "cd-low", "vinyl", "partial"]
        top = resp.results[0]
        assert top.matched_count == 3 and top.is_lossless is True
        assert top.track_files["k1"].target_file == "01 Track.flac"
        assert resp.tracks_requested == 3

    @pytest.mark.asyncio
    async def test_studio_release_and_better_matches_rank_above_live(self):
        live = make_candidate("live", matched=3, seeders=900)
        live.release.release_type = "Live album"
        weak = make_candidate("weak", matched=3, seeders=500)
        for f in weak.track_files.values():
            f.match_score = 0.9
        prov = Mock(spec=["name", "search_album"])
        prov.name = "RED"
        prov.search_album.return_value = [live, weak, make_candidate("exact", matched=3, seeders=20)]
        with patch.object(search_route, "get_download_manager", return_value=manager_with([prov])):
            resp = await search_route.search_album(album_request(), api_key="k")
        assert [r.source_id for r in resp.results] == ["exact", "weak", "live"]

    @pytest.mark.asyncio
    async def test_all_trackers_failing_is_502_not_404(self):
        bad = Mock(spec=["name", "search_album"])
        bad.name = "RED"
        bad.search_album.side_effect = RuntimeError("tracker down")
        with patch.object(search_route, "get_download_manager", return_value=manager_with([bad])):
            with pytest.raises(HTTPException) as exc:
                await search_route.search_album(album_request(), api_key="k")
        assert exc.value.status_code == 502

    @pytest.mark.asyncio
    async def test_runs_on_the_search_executor(self):
        thread_names = []
        prov = Mock(spec=["name", "search_album"])
        prov.name = "RED"

        def album(*_a, **_k):
            thread_names.append(threading.current_thread().name)
            return [make_candidate("t", matched=1)]

        prov.search_album.side_effect = album
        with patch.object(search_route, "get_download_manager", return_value=manager_with([prov])):
            await search_route.search_album(album_request(1), api_key="k")
        assert thread_names[0].startswith("provider-search")

    @pytest.mark.asyncio
    async def test_no_candidates_is_404(self):
        prov = Mock(spec=["name", "search_album"])
        prov.name = "RED"
        prov.search_album.return_value = []
        with patch.object(search_route, "get_download_manager", return_value=manager_with([prov])):
            with pytest.raises(HTTPException) as exc:
                await search_route.search_album(album_request(), api_key="k")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_one_tracker_failing_does_not_sink_the_other(self):
        bad = Mock(spec=["name", "search_album"])
        bad.name = "RED"
        bad.search_album.side_effect = RuntimeError("tracker down")
        good = Mock(spec=["name", "search_album"])
        good.name = "OPS"
        good.search_album.return_value = [make_candidate("ops-1", matched=3)]
        with patch.object(search_route, "get_download_manager", return_value=manager_with([bad, good])):
            resp = await search_route.search_album(album_request(), api_key="k")
        assert [r.source_id for r in resp.results] == ["ops-1"]

    @pytest.mark.asyncio
    async def test_identical_concurrent_album_searches_coalesce(self):
        gate = threading.Event()
        prov = Mock(spec=["name", "search_album"])
        prov.name = "RED"

        def slow(*_a, **_k):
            assert gate.wait(timeout=10)
            return [make_candidate("t", matched=3)]

        prov.search_album.side_effect = slow
        with patch.object(search_route, "get_download_manager", return_value=manager_with([prov])):
            t1 = asyncio.create_task(search_route.search_album(album_request(), api_key="k"))
            t2 = asyncio.create_task(search_route.search_album(album_request(), api_key="k"))
            await asyncio.sleep(0.05)
            gate.set()
            r1, r2 = await asyncio.gather(t1, t2)
        assert prov.search_album.call_count == 1
        assert r1.results[0].source_id == r2.results[0].source_id == "t"


# =============================================================================
# Lowered "excellent" seeder bar (parity with karaoke-gen BEST CHOICE)
# =============================================================================

class TestBestChoiceThreshold:
    def _release(self, seeders):
        return Release(
            title="Paper Lanterns", artist="Test Artist", source_name="RED",
            quality=Quality(format=AudioFormat.FLAC, bit_depth=16, media=MediaSource.CD),
            seeders=seeders, release_type="Album",
        )

    def test_value(self):
        assert BEST_CHOICE_MIN_SEEDERS == 10

    def test_excellent_at_ten_not_nine(self):
        assert is_excellent_match(self._release(10)) is True
        assert is_excellent_match(self._release(9)) is False

    def test_early_termination_still_needs_a_well_seeded_hit(self):
        provider = FakeGazelle(api_key="k", base_url="https://tracker.example", cache_subdir="test-album")
        assert provider.early_termination_seeders == 50
