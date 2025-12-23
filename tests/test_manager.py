from unittest.mock import MagicMock, Mock

from flacfetch.core.interfaces import Downloader, Provider
from flacfetch.core.manager import FetchManager
from flacfetch.core.models import AudioFormat, Quality, Release, TrackQuery


def test_manager_search():
    mgr = FetchManager()


    class MockProvider(Provider):
        @property
        def name(self): return "Mock"
        def search(self, q): return []

    mp = MockProvider()
    mp.search = MagicMock(return_value=[])

    q = TrackQuery(artist="A", title="B")
    r1 = Release(title="B", artist="A", quality=Quality(AudioFormat.FLAC), source_name="Mock")
    mp.search.return_value = [r1]

    mgr.add_provider(mp)

    results = mgr.search(q)
    assert len(results) == 1
    assert results[0] == r1
    mp.search.assert_called_once_with(q)

def test_select_best():
    mgr = FetchManager()
    q_low = Quality(format=AudioFormat.MP3, bitrate=128)
    q_high = Quality(format=AudioFormat.FLAC, bit_depth=16)

    r1 = Release(title="Low", artist="A", quality=q_low, source_name="Mock")
    r2 = Release(title="High", artist="A", quality=q_high, source_name="Mock")

    best = mgr.select_best([r1, r2])
    assert best == r2

    best_reverse = mgr.select_best([r2, r1])
    assert best_reverse == r2

def test_provider_priority():
    """Test that providers are searched in priority order"""
    mgr = FetchManager()

    # Create mock providers
    class MockProvider1(Provider):
        @property
        def name(self): return "Provider1"
        def search(self, q): return []

    class MockProvider2(Provider):
        @property
        def name(self): return "Provider2"
        def search(self, q): return []

    p1 = MockProvider1()
    p2 = MockProvider2()

    r1 = Release(title="T1", artist="A", quality=Quality(AudioFormat.FLAC), source_name="Provider1")
    r2 = Release(title="T2", artist="A", quality=Quality(AudioFormat.FLAC), source_name="Provider2")

    p1.search = MagicMock(return_value=[r1])
    p2.search = MagicMock(return_value=[r2])

    # Add in one order
    mgr.add_provider(p1)
    mgr.add_provider(p2)

    # Set priority to reverse order
    mgr.set_provider_priority(["Provider2", "Provider1"])

    q = TrackQuery(artist="A", title="T")
    results = mgr.search(q)

    # Both should be searched
    assert p1.search.called
    assert p2.search.called
    # Results should be in priority order (Provider2 first)
    assert len(results) == 2
    assert results[0].source_name == "Provider2"
    assert results[1].source_name == "Provider1"

def test_provider_fallback_disabled():
    """Test that lower priority providers aren't searched when fallback is disabled"""
    mgr = FetchManager()

    class MockProvider1(Provider):
        @property
        def name(self): return "Provider1"
        def search(self, q): return []

    class MockProvider2(Provider):
        @property
        def name(self): return "Provider2"
        def search(self, q): return []

    p1 = MockProvider1()
    p2 = MockProvider2()

    r1 = Release(title="T1", artist="A", quality=Quality(AudioFormat.FLAC), source_name="Provider1")
    r2 = Release(title="T2", artist="A", quality=Quality(AudioFormat.FLAC), source_name="Provider2")

    p1.search = MagicMock(return_value=[r1])
    p2.search = MagicMock(return_value=[r2])

    mgr.add_provider(p1)
    mgr.add_provider(p2)

    # Set priority and disable fallback
    mgr.set_provider_priority(["Provider1", "Provider2"])
    mgr.enable_fallback_search(False)

    q = TrackQuery(artist="A", title="T")
    results = mgr.search(q)

    # Only Provider1 should be called (it returned results)
    assert p1.search.called
    assert not p2.search.called
    assert len(results) == 1
    assert results[0].source_name == "Provider1"

def test_provider_fallback_disabled_no_results():
    """Test that search stops even when first provider returns empty if fallback is disabled"""
    mgr = FetchManager()

    class MockProvider1(Provider):
        @property
        def name(self): return "Provider1"
        def search(self, q): return []

    class MockProvider2(Provider):
        @property
        def name(self): return "Provider2"
        def search(self, q): return []

    p1 = MockProvider1()
    p2 = MockProvider2()

    r2 = Release(title="T2", artist="A", quality=Quality(AudioFormat.FLAC), source_name="Provider2")

    p1.search = MagicMock(return_value=[])  # Empty
    p2.search = MagicMock(return_value=[r2])

    mgr.add_provider(p1)
    mgr.add_provider(p2)

    # Set priority and disable fallback
    mgr.set_provider_priority(["Provider1", "Provider2"])
    mgr.enable_fallback_search(False)

    q = TrackQuery(artist="A", title="T")
    results = mgr.search(q)

    # Only Provider1 should be called (fallback disabled stops even on empty)
    assert p1.search.called
    assert not p2.search.called
    assert len(results) == 0

def test_provider_fallback_on_empty():
    """Test that lower priority providers are searched when higher ones return empty"""
    mgr = FetchManager()

    class MockProvider1(Provider):
        @property
        def name(self): return "Provider1"
        def search(self, q): return []

    class MockProvider2(Provider):
        @property
        def name(self): return "Provider2"
        def search(self, q): return []

    p1 = MockProvider1()
    p2 = MockProvider2()

    r2 = Release(title="T2", artist="A", quality=Quality(AudioFormat.FLAC), source_name="Provider2")

    p1.search = MagicMock(return_value=[])  # Empty
    p2.search = MagicMock(return_value=[r2])

    mgr.add_provider(p1)
    mgr.add_provider(p2)

    # Set priority with fallback enabled (default)
    mgr.set_provider_priority(["Provider1", "Provider2"])

    q = TrackQuery(artist="A", title="T")
    results = mgr.search(q)

    # Both should be called
    assert p1.search.called
    assert p2.search.called
    assert len(results) == 1
    assert results[0].source_name == "Provider2"


class TestSorting:
    """Test the sorting logic in select_best"""

    def test_sort_by_match_score(self):
        mgr = FetchManager()
        r1 = Release(title="T", artist="A", quality=Quality(AudioFormat.FLAC),
                     source_name="Test", match_score=0.5)
        r2 = Release(title="T", artist="A", quality=Quality(AudioFormat.FLAC),
                     source_name="Test", match_score=0.9)

        best = mgr.select_best([r1, r2])
        assert best.match_score == 0.9

    def test_sort_by_release_type(self):
        mgr = FetchManager()
        r_album = Release(title="T", artist="A", quality=Quality(AudioFormat.FLAC),
                         source_name="Test", release_type="Album", match_score=1.0)
        r_single = Release(title="T", artist="A", quality=Quality(AudioFormat.FLAC),
                          source_name="Test", release_type="Single", match_score=1.0)
        r_remix = Release(title="T", artist="A", quality=Quality(AudioFormat.FLAC),
                         source_name="Test", release_type="Remix", match_score=1.0)

        # Album should be preferred over Single over Remix
        best = mgr.select_best([r_remix, r_single, r_album])
        assert best.release_type == "Album"

    def test_sort_by_seeders(self):
        mgr = FetchManager()
        r1 = Release(title="T", artist="A", quality=Quality(AudioFormat.FLAC),
                     source_name="Test", match_score=1.0, seeders=5)
        r2 = Release(title="T", artist="A", quality=Quality(AudioFormat.FLAC),
                     source_name="Test", match_score=1.0, seeders=50)

        best = mgr.select_best([r1, r2])
        assert best.seeders == 50

    def test_sort_by_view_count(self):
        mgr = FetchManager()
        r1 = Release(title="T", artist="A", quality=Quality(AudioFormat.OPUS),
                     source_name="YouTube", match_score=1.0, view_count=1000)
        r2 = Release(title="T", artist="A", quality=Quality(AudioFormat.OPUS),
                     source_name="YouTube", match_score=1.0, view_count=100000)

        best = mgr.select_best([r1, r2])
        assert best.view_count == 100000

    def test_sort_by_quality(self):
        mgr = FetchManager()
        q_low = Quality(format=AudioFormat.MP3, bitrate=128)
        q_high = Quality(format=AudioFormat.FLAC, bit_depth=24)

        r1 = Release(title="T", artist="A", quality=q_low, source_name="Test", match_score=1.0)
        r2 = Release(title="T", artist="A", quality=q_high, source_name="Test", match_score=1.0)

        best = mgr.select_best([r1, r2])
        assert best.quality == q_high

    def test_sort_youtube_channel_exact_match(self):
        mgr = FetchManager()
        r_exact = Release(title="T", artist="Artist", quality=Quality(AudioFormat.OPUS),
                         source_name="YouTube", match_score=1.0, channel="Artist")
        r_partial = Release(title="T", artist="Artist", quality=Quality(AudioFormat.OPUS),
                           source_name="YouTube", match_score=1.0, channel="Artist - Topic")
        r_nomatch = Release(title="T", artist="Artist", quality=Quality(AudioFormat.OPUS),
                           source_name="YouTube", match_score=1.0, channel="Random Channel")

        best = mgr.select_best([r_nomatch, r_partial, r_exact])
        assert best.channel == "Artist"

    def test_sort_youtube_official_topic(self):
        mgr = FetchManager()
        r_topic = Release(title="T", artist="A", quality=Quality(AudioFormat.OPUS),
                         source_name="YouTube", match_score=1.0, channel="Artist - Topic")
        r_regular = Release(title="T", artist="A", quality=Quality(AudioFormat.OPUS),
                           source_name="YouTube", match_score=1.0, channel="Regular Channel")

        best = mgr.select_best([r_regular, r_topic])
        assert " - Topic" in best.channel

    def test_sort_youtube_official_vevo(self):
        mgr = FetchManager()
        r_vevo = Release(title="T", artist="A", quality=Quality(AudioFormat.OPUS),
                        source_name="YouTube", match_score=1.0, channel="ArtistVEVO")
        r_regular = Release(title="T", artist="A", quality=Quality(AudioFormat.OPUS),
                           source_name="YouTube", match_score=1.0, channel="Regular")

        best = mgr.select_best([r_regular, r_vevo])
        assert "VEVO" in best.channel

    def test_sort_youtube_official_audio_title(self):
        mgr = FetchManager()
        r_official = Release(title="Song (Official Audio)", artist="A",
                            quality=Quality(AudioFormat.OPUS),
                            source_name="YouTube", match_score=1.0, channel="Ch")
        r_regular = Release(title="Song", artist="A", quality=Quality(AudioFormat.OPUS),
                           source_name="YouTube", match_score=1.0, channel="Ch")

        best = mgr.select_best([r_regular, r_official])
        assert "Official Audio" in best.title

    def test_sort_year_red_prefers_oldest(self):
        mgr = FetchManager()
        r_old = Release(title="T", artist="A", quality=Quality(AudioFormat.FLAC),
                       source_name="RED", match_score=1.0, year=2000)
        r_new = Release(title="T", artist="A", quality=Quality(AudioFormat.FLAC),
                       source_name="RED", match_score=1.0, year=2020)

        best = mgr.select_best([r_new, r_old])
        # For RED, older is better (original release)
        assert best.year == 2000

    def test_sort_year_youtube_prefers_newest(self):
        mgr = FetchManager()
        r_old = Release(title="T", artist="A", quality=Quality(AudioFormat.OPUS),
                       source_name="YouTube", match_score=1.0, year=2000, view_count=1000)
        r_new = Release(title="T", artist="A", quality=Quality(AudioFormat.OPUS),
                       source_name="YouTube", match_score=1.0, year=2020, view_count=1000)

        best = mgr.select_best([r_old, r_new])
        # For YouTube, newer is better
        assert best.year == 2020


class TestDownloaders:
    """Test downloader registration and usage"""

    def test_set_default_downloader(self):
        mgr = FetchManager()
        mock_dl = Mock(spec=Downloader)

        mgr.set_default_downloader(mock_dl)
        assert mgr._default_downloader == mock_dl

    def test_register_provider_downloader(self):
        mgr = FetchManager()
        mock_dl = Mock(spec=Downloader)

        mgr.register_downloader("TestProvider", mock_dl)
        assert "TestProvider" in mgr._downloader_map
        assert mgr._downloader_map["TestProvider"] == mock_dl

    def test_download_with_provider_downloader(self):
        mgr = FetchManager()
        mock_dl = Mock(spec=Downloader)
        mock_dl.download.return_value = "/path/to/file.flac"

        mgr.register_downloader("TestProvider", mock_dl)

        release = Release(title="T", artist="A", quality=Quality(AudioFormat.FLAC),
                         source_name="TestProvider", download_url="http://test.com")

        result = mgr.download(release, "/output")
        assert result == "/path/to/file.flac"
        # Check it was called with output_filename keyword arg
        mock_dl.download.assert_called_once()
        call_args = mock_dl.download.call_args
        assert call_args[0][0] == release
        assert call_args[0][1] == "/output"
        assert call_args[1]['output_filename'] is None

    def test_download_with_default_downloader(self):
        mgr = FetchManager()
        mock_dl = Mock(spec=Downloader)
        mock_dl.download.return_value = "/path/to/file.flac"

        mgr.set_default_downloader(mock_dl)

        release = Release(title="T", artist="A", quality=Quality(AudioFormat.FLAC),
                         source_name="UnknownProvider", download_url="http://test.com")

        result = mgr.download(release, "/output")
        assert result == "/path/to/file.flac"
        mock_dl.download.assert_called_once()

    def test_download_no_downloader_raises(self):
        import pytest
        mgr = FetchManager()

        release = Release(title="T", artist="A", quality=Quality(AudioFormat.FLAC),
                         source_name="UnknownProvider", download_url="http://test.com")

        with pytest.raises(ValueError, match="No downloader registered"):
            mgr.download(release, "/output")

