import pytest
from unittest.mock import MagicMock
from flacfetch.providers.redacted import RedactedProvider
from flacfetch.core.models import TrackQuery, AudioFormat, MediaSource

# Sample JSON based on API docs (Torrents Search)
SAMPLE_RESPONSE = {
    "status": "success",
    "response": {
        "results": [
            {
                "groupName": "Fear Not",
                "artist": "Logistics",
                "torrents": [
                    {
                        "torrentId": 29991962,
                        "format": "FLAC",
                        "encoding": "Lossless",
                        "media": "CD",
                        "size": 527749302,
                        "hasLog": False,
                        "hasCue": False,
                    },
                    {
                        "torrentId": 30028889,
                        "format": "MP3",
                        "encoding": "320",
                        "media": "CD",
                        "size": 167593347,
                    }
                ]
            }
        ]
    }
}

def test_redacted_search_parsing():
    provider = RedactedProvider(api_key="test")
    provider.session.get = MagicMock()
    
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = SAMPLE_RESPONSE
    provider.session.get.return_value = mock_resp
    
    q = TrackQuery(artist="Logistics", title="Fear Not")
    releases = provider.search(q)
    
    assert len(releases) == 2
    
    r1 = releases[0]
    assert r1.title == "Fear Not"
    assert r1.artist == "Logistics"
    assert r1.quality.format == AudioFormat.FLAC
    assert r1.quality.bit_depth == 16 # Default if no "24bit" in encoding
    assert r1.quality.media == MediaSource.CD
    assert r1.download_url == "https://redacted.sh/ajax.php?action=download&id=29991962"
    
    r2 = releases[1]
    assert r2.quality.format == AudioFormat.MP3
    assert r2.quality.bitrate == 320

