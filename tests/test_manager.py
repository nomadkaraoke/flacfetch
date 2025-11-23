import pytest
from unittest.mock import MagicMock
from flacfetch.core.manager import FetchManager
from flacfetch.core.models import TrackQuery, Release, Quality, AudioFormat
from flacfetch.core.interfaces import Provider

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

