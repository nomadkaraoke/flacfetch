from unittest.mock import MagicMock

from flacfetch.core.models import AudioFormat, TrackQuery
from flacfetch.providers.ops import OPSProvider

# Mock base URL for testing (no real URLs in codebase)
MOCK_BASE_URL = "https://mock.tracker.test/api"

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

def test_ops_lossless_filtering():
    provider = OPSProvider(api_key="test", base_url=MOCK_BASE_URL)
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

def test_ops_no_match_filtered():
    provider = OPSProvider(api_key="test", base_url=MOCK_BASE_URL)
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

def test_ops_html_entity_decoding():
    """Test that HTML entities like &amp; are properly decoded in filenames."""
    provider = OPSProvider(api_key="test", base_url=MOCK_BASE_URL)
    provider.session.get = MagicMock()

    # Response with HTML entities in filename
    # Use "Luv Stuck" as the track name to get better matching
    HTML_ENTITY_RESPONSE = {
        "status": "success",
        "response": {
            "group": {
                "name": "Test Album",
                "year": 2024,
                "recordLabel": "Test Label",
                "catalogueNumber": "TEST001",
                "releaseType": 1,
                "musicInfo": {"artists": [{"name": "Salute"}]}
            },
            "torrents": [{
                "id": 1,
                "format": "FLAC",
                "encoding": "Lossless",
                "media": "CD",
                "size": 10000000,
                "seeders": 10,
                "fileList": "05 - Salute &amp; Piri - Luv Stuck.flac{{{1000}}}",
                "remastered": False
            }]
        }
    }

    browse_resp = MagicMock()
    browse_resp.status_code = 200
    browse_resp.json.return_value = {"status": "success", "response": {"results": [{"groupId": 1}]}}

    details_resp = MagicMock()
    details_resp.status_code = 200
    details_resp.json.return_value = HTML_ENTITY_RESPONSE

    provider.session.get.side_effect = [browse_resp, details_resp]

    q = TrackQuery(artist="Salute", title="Luv Stuck")
    releases = provider.search(q)

    # Should find the release and decode &amp; to &
    assert len(releases) == 1
    assert releases[0].target_file == "05 - Salute & Piri - Luv Stuck.flac"
    assert "&amp;" not in releases[0].target_file

def test_ops_filelist_sanitization():
    """Test that special characters are removed from filelist queries.

    Sphinx (the search engine used by OPS) treats certain characters as
    special operators that break the search:
    - : (colon) - field search operator
    - / (slash) - path separator
    - () (parentheses) - grouping
    - [] (brackets) - character classes
    - ! (exclamation) - NOT operator
    - , (comma) - separator
    - . (period) - wildcard
    - ; (semicolon) - separator
    """
    provider = OPSProvider("fake_api_key", base_url=MOCK_BASE_URL)

    # Test cases: (input, expected_output)
    test_cases = [
        ("Flight 717: Going To Denmark", "Flight 717 Going To Denmark"),
        ("Track (feat. Artist)", "Track feat Artist"),
        ("Song [Remix]", "Song Remix"),
        ("Part 1/3", "Part 1 3"),
        ("No! Way!", "No Way"),
        ("Item 1, 2, 3", "Item 1 2 3"),
        ("End. Start", "End Start"),
        ("Part A; Part B", "Part A Part B"),
        # Multiple special chars should be collapsed to single space
        ("Track::(Remix)", "Track Remix"),
        # Hyphens and ampersands should be preserved (they work in Sphinx)
        ("Track - Artist", "Track - Artist"),
        ("Artist & Artist", "Artist & Artist"),
    ]

    for input_query, expected in test_cases:
        result = provider._sanitize_filelist_query(input_query)
        assert result == expected, f"Failed for '{input_query}': got '{result}', expected '{expected}'"

