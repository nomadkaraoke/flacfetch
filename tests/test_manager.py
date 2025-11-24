from unittest.mock import MagicMock

from flacfetch.core.interfaces import Provider
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

