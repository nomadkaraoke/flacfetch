"""Tests for the YouTube search provider's query construction."""
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from flacfetch.core.models import TrackQuery
from flacfetch.providers.youtube import YoutubeProvider


def _search_and_capture_query(artist: str, title: str) -> str:
    ydl = MagicMock()
    ydl.extract_info.return_value = {"entries": []}
    ydl_cm = MagicMock()
    ydl_cm.__enter__.return_value = ydl

    @contextmanager
    def fake_isolated(opts):
        yield opts

    with patch("flacfetch.providers.youtube.get_ytdlp_base_opts", return_value={}), \
         patch("flacfetch.providers.youtube.ytdlp_opts_isolated", fake_isolated), \
         patch("flacfetch.providers.youtube.yt_dlp.YoutubeDL", return_value=ydl_cm):
        YoutubeProvider().search(TrackQuery(artist=artist, title=title))

    return ydl.extract_info.call_args.args[0]


def test_search_query_is_plain_artist_title():
    """A ' topic' suffix pushed the actual song out of the results
    (Playa Fly - N God We Trust), so the query must be just artist + title."""
    query = _search_and_capture_query("Playa Fly", "N God We Trust")
    assert query == "ytsearch5:Playa Fly N God We Trust"
    assert "topic" not in query.lower()


# --- Bot-block handling: fix B (re-export + retry once) and fix D's error signal ---

BOT_WALL = "ERROR: [youtube] abc: Sign in to confirm you’re not a bot."


def _entry(video_id="vid"):
    return {"id": video_id, "title": "Song", "webpage_url": f"https://youtu.be/{video_id}", "formats": []}


def _run_search(attempts, wait_result=True):
    """Run provider.search; each item in ``attempts`` is one yt-dlp run:
    {"errors": [logged yt-dlp errors], "entries": [...], "raises": Exception}."""
    attempts = iter(attempts)
    runs = []

    @contextmanager
    def fake_isolated(opts):
        yield opts

    def make_ydl(opts):
        attempt = next(attempts)
        runs.append(attempt)
        ydl = MagicMock()

        def extract_info(_query, download=False):
            for message in attempt.get("errors", []):
                opts["logger"].error(message)
            if attempt.get("raises"):
                raise attempt["raises"]
            return {"entries": attempt.get("entries", [])}

        ydl.extract_info.side_effect = extract_info
        cm = MagicMock()
        cm.__enter__.return_value = ydl
        return cm

    provider = YoutubeProvider()
    with patch("flacfetch.providers.youtube.get_ytdlp_base_opts", return_value={}), \
         patch("flacfetch.providers.youtube.ytdlp_opts_isolated", fake_isolated), \
         patch("flacfetch.providers.youtube.get_cookies_file", return_value="/x/cookies.txt"), \
         patch("flacfetch.providers.youtube.cookies_mtime", return_value=1), \
         patch("flacfetch.providers.youtube.wait_for_fresh_cookies", return_value=wait_result) as wait, \
         patch("flacfetch.providers.youtube.yt_dlp.YoutubeDL", side_effect=make_ydl):
        releases = provider.search(TrackQuery(artist="A", title="T"))
    return provider, releases, runs, wait


def test_clean_search_has_no_error_and_requests_nothing():
    provider, releases, runs, wait = _run_search([{"entries": [_entry()]}])
    assert len(releases) == 1
    assert len(runs) == 1
    assert provider.last_search_error is None
    wait.assert_not_called()


def test_bot_walled_search_refreshes_cookies_and_retries_once():
    provider, releases, runs, wait = _run_search([
        {"errors": [BOT_WALL] * 5},
        {"entries": [_entry()]},
    ])
    assert len(runs) == 2
    assert len(releases) == 1
    assert provider.last_search_error is None
    wait.assert_called_once_with("/x/cookies.txt", 1)


def test_bot_walled_retry_failure_stops_and_reports_error():
    provider, releases, runs, _ = _run_search([
        {"errors": [BOT_WALL]},
        {"errors": [BOT_WALL]},
    ])
    assert len(runs) == 2
    assert releases == []
    assert "not a bot" in provider.last_search_error


def test_no_fresh_cookies_means_no_retry_and_reports_error():
    provider, releases, runs, _ = _run_search([{"errors": [BOT_WALL]}], wait_result=False)
    assert len(runs) == 1
    assert "not a bot" in provider.last_search_error


def test_partial_bot_block_with_results_is_still_an_error():
    """Some entries were walled, so the result set is incomplete: don't cache it."""
    provider, releases, _, _ = _run_search(
        [{"errors": [BOT_WALL], "entries": [_entry()]}], wait_result=False
    )
    assert len(releases) == 1
    assert "not a bot" in provider.last_search_error


def test_one_unavailable_entry_among_results_is_not_an_error():
    """An age-restricted or removed video in the top 5 is a normal search."""
    provider, releases, runs, wait = _run_search([
        {"errors": ["ERROR: [youtube] x: Video unavailable"], "entries": [_entry()]},
    ])
    assert len(releases) == 1
    assert len(runs) == 1
    assert provider.last_search_error is None
    wait.assert_not_called()


def test_errors_with_zero_results_are_an_error():
    provider, releases, runs, wait = _run_search([
        {"errors": ["ERROR: unable to download webpage: timed out"]},
    ])
    assert releases == []
    assert "timed out" in provider.last_search_error
    wait.assert_not_called()


def test_raised_exception_is_an_error():
    provider, releases, _, _ = _run_search([{"raises": RuntimeError("boom")}])
    assert releases == []
    assert "boom" in provider.last_search_error


def test_zero_results_without_errors_is_not_an_error():
    provider, releases, _, _ = _run_search([{"entries": []}])
    assert releases == []
    assert provider.last_search_error is None


def test_ytdlp_really_routes_ignored_errors_to_our_logger():
    """The error signal depends on yt-dlp behaviour; pin it against the real class."""
    import yt_dlp

    from flacfetch.providers.youtube import _YtdlpErrorLog

    log = _YtdlpErrorLog()
    with yt_dlp.YoutubeDL({"logger": log, "ignoreerrors": True, "quiet": True}) as ydl:
        ydl.report_error(BOT_WALL)
    assert any("not a bot" in message for message in log.errors)
