import pytest
from unittest.mock import MagicMock
from flacfetch.providers.redacted import RedactedProvider
from flacfetch.core.models import TrackQuery, AudioFormat, MediaSource

# Updated Mock to include file lists and multiple qualities
SAMPLE_GROUP_RESPONSE = {
    "status": "success",
    "response": {
        "group": {
            "name": "Fear Not",
            "year": 2012,
            "recordLabel": "Test Label",
            "catalogueNumber": "TEST001",
            "releaseType": 1, # Album
            "musicInfo": {"artists": [{"name": "Logistics"}]}
        },
        "torrents": [
            {
                "id": 29991962,
                "format": "FLAC",
                "encoding": "Lossless",
                "media": "CD",
                "size": 527749302,
                "fileList": "01 - Fear Not.flac{{{3000}}}|||02 - Other.flac{{{2000}}}",
                "remastered": False
            },
            {
                "id": 30028889,
                "format": "MP3",
                "encoding": "320",
                "media": "CD",
                "size": 167593347,
                "fileList": "01 - Fear Not.mp3{{{1000}}}|||02 - Other.mp3{{{500}}}",
                "remastered": False
            },
            {
                "id": 12345678,
                "format": "FLAC",
                "encoding": "24bit Lossless",
                "media": "WEB",
                "size": 800000000,
                "fileList": "01 Fear Not.flac{{{5000}}}",
                "remastered": False
            }
        ]
    }
}

def test_redacted_lossless_filtering():
    provider = RedactedProvider(api_key="test")
    provider.session.get = MagicMock()
    
    # Mock the browse search response
    browse_resp = MagicMock()
    browse_resp.status_code = 200
    browse_resp.json.return_value = {
        "status": "success", 
        "response": {"results": [{"groupId": 123}]}
    }
    
    # Mock the group details response
    details_resp = MagicMock()
    details_resp.status_code = 200
    details_resp.json.return_value = SAMPLE_GROUP_RESPONSE
    
    # Configure side effect to return different responses for different calls
    provider.session.get.side_effect = [browse_resp, details_resp]
    
    q = TrackQuery(artist="Logistics", title="Fear Not")
    releases = provider.search(q)
    
    # Should find 2 releases (FLAC 16bit and FLAC 24bit)
    # The MP3 release should be filtered out
    assert len(releases) == 2
    
    formats = [r.quality.format for r in releases]
    assert all(f == AudioFormat.FLAC for f in formats)
    
    # Verify target files
    assert releases[0].target_file == "01 - Fear Not.flac"
    assert releases[1].target_file == "01 Fear Not.flac"

def test_redacted_no_match_filtered():
    provider = RedactedProvider(api_key="test")
    provider.session.get = MagicMock()
    
    # Response with no matching file
    NO_MATCH_RESPONSE = {
        "status": "success",
        "response": {
            "group": {"name": "Test", "musicInfo": {"artists": [{"name": "Test"}]}},
            "torrents": [{
                "id": 1, "format": "FLAC", "encoding": "Lossless", "media": "CD",
                "fileList": "01 - Completely Different Song.flac{{{100}}}"
            }]
        }
    }
    
    browse_resp = MagicMock()
    browse_resp.status_code = 200
    browse_resp.json.return_value = {"status": "success", "response": {"results": [{"groupId": 1}]}}
    
    details_resp = MagicMock()
    details_resp.status_code = 200
    details_resp.json.return_value = NO_MATCH_RESPONSE
    
    provider.session.get.side_effect = [browse_resp, details_resp]
    
    q = TrackQuery(artist="Test", title="Fear Not")
    releases = provider.search(q)
    
    assert len(releases) == 0
