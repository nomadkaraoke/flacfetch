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
