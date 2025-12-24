"""Test error handling and edge cases in providers"""
from unittest.mock import Mock, patch

from flacfetch.core.models import AudioFormat, TrackQuery
from flacfetch.providers.ops import OPSProvider
from flacfetch.providers.red import REDProvider

# Mock base URLs for testing (no real tracker URLs)
MOCK_RED_URL = "https://mock-red-tracker.test"
MOCK_OPS_URL = "https://mock-ops-tracker.test"


class TestProviderErrorHandling:
    """Test error handling in provider implementations"""

    def test_red_api_error_handling(self):
        """Test that RED handles API errors gracefully"""
        provider = REDProvider("fake_key", base_url=MOCK_RED_URL)

        with patch('requests.get') as mock_get:
            mock_get.side_effect = Exception("Network error")

            query = TrackQuery(artist="Artist", title="Title")
            results = provider.search(query)

            # Should return empty list on error
            assert results == []

    def test_ops_api_error_handling(self):
        """Test that OPS handles API errors gracefully"""
        provider = OPSProvider("fake_key", base_url=MOCK_OPS_URL)

        with patch('requests.get') as mock_get:
            mock_get.side_effect = Exception("Network error")

            query = TrackQuery(artist="Artist", title="Title")
            results = provider.search(query)

            # Should return empty list on error
            assert results == []

    def test_red_empty_response(self):
        """Test RED handles empty API responses"""
        provider = REDProvider("fake_key", base_url=MOCK_RED_URL)

        with patch('requests.get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "status": "success",
                "response": {"results": []}
            }
            mock_get.return_value = mock_response

            query = TrackQuery(artist="Unknown Artist", title="Unknown Title")
            results = provider.search(query)

            assert results == []

    def test_ops_empty_response(self):
        """Test OPS handles empty API responses"""
        provider = OPSProvider("fake_key", base_url=MOCK_OPS_URL)

        with patch('requests.get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "status": "success",
                "response": {"results": []}
            }
            mock_get.return_value = mock_response

            query = TrackQuery(artist="Unknown Artist", title="Unknown Title")
            results = provider.search(query)

            assert results == []

    def test_red_malformed_response(self):
        """Test RED handles malformed API responses"""
        provider = REDProvider("fake_key", base_url=MOCK_RED_URL)

        with patch('requests.get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"unexpected": "format"}
            mock_get.return_value = mock_response

            query = TrackQuery(artist="Artist", title="Title")
            results = provider.search(query)

            # Should handle gracefully
            assert isinstance(results, list)

    def test_ops_malformed_response(self):
        """Test OPS handles malformed API responses"""
        provider = OPSProvider("fake_key", base_url=MOCK_OPS_URL)

        with patch('requests.get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"unexpected": "format"}
            mock_get.return_value = mock_response

            query = TrackQuery(artist="Artist", title="Title")
            results = provider.search(query)

            # Should handle gracefully
            assert isinstance(results, list)

    def test_provider_search_limit(self):
        """Test that search_limit is respected"""
        provider = REDProvider("fake_key", base_url=MOCK_RED_URL)
        provider.search_limit = 5

        with patch('requests.get') as mock_get:
            # Create mock response with many results
            mock_response = Mock()
            mock_response.status_code = 200
            mock_results = []
            for i in range(20):
                mock_results.append({
                    "groupId": i,
                    "groupName": f"Album {i}",
                    "artist": "Artist",
                    "groupYear": 2020
                })

            mock_response.json.return_value = {
                "status": "success",
                "response": {"results": mock_results}
            }
            mock_get.return_value = mock_response

            query = TrackQuery(artist="Artist", title="Title")
            # This will find group IDs but not fetch details (mocked)
            # Just verify it doesn't crash with many results
            results = provider.search(query)
            assert isinstance(results, list)

    def test_red_quality_parsing_edge_cases(self):
        """Test quality parsing with unusual formats"""
        provider = REDProvider("fake_key", base_url=MOCK_RED_URL)

        # Test with minimal data
        torrent = {
            "format": "FLAC",
            "encoding": "Lossless",
            "media": "WEB"
        }
        quality = provider._parse_quality(torrent)
        assert quality.format == AudioFormat.FLAC

        # Test with unknown format (should not crash)
        torrent_unknown = {
            "format": "UnknownFormat",
            "encoding": "Unknown",
            "media": "OTHER"
        }
        quality2 = provider._parse_quality(torrent_unknown)
        assert quality2 is not None

    def test_ops_quality_parsing_edge_cases(self):
        """Test quality parsing with unusual formats"""
        provider = OPSProvider("fake_key", base_url=MOCK_OPS_URL)

        # Test with minimal data
        torrent = {
            "format": "FLAC",
            "encoding": "Lossless",
            "media": "WEB"
        }
        quality = provider._parse_quality(torrent)
        assert quality.format == AudioFormat.FLAC

        # Test with unknown format (should not crash)
        torrent_unknown = {
            "format": "UnknownFormat",
            "encoding": "Unknown",
            "media": "OTHER"
        }
        quality2 = provider._parse_quality(torrent_unknown)
        assert quality2 is not None

    def test_file_matching_edge_cases(self):
        """Test file matching with edge cases"""
        provider = REDProvider("fake_key", base_url=MOCK_RED_URL)

        # Empty file list
        result = provider._find_best_target_file("", "Track Title")
        assert result[0] is None

        # Single file
        file_list = "01 Track Title.flac{{{123456}}}"
        result = provider._find_best_target_file(file_list, "Track Title")
        assert result[0] == "01 Track Title.flac"
        assert result[1] == 123456

    def test_provider_name_property(self):
        """Test provider name properties"""
        red = REDProvider("key", base_url=MOCK_RED_URL)
        ops = OPSProvider("key", base_url=MOCK_OPS_URL)

        assert red.name == "RED"
        assert ops.name == "OPS"

    def test_provider_cache_dir(self):
        """Test cache directory setting"""
        provider = REDProvider("key", base_url=MOCK_RED_URL)
        # Cache dir is set by default to user cache directory
        assert provider.cache_dir is not None

        provider.cache_dir = "/tmp/cache"
        assert str(provider.cache_dir) == "/tmp/cache"

    def test_provider_search_limit_default(self):
        """Test default search limit"""
        provider = REDProvider("key", base_url=MOCK_RED_URL)
        # Default is 10 (reduced for faster searches)
        assert provider.search_limit == 10

        provider.search_limit = 5
        assert provider.search_limit == 5

    def test_provider_requires_base_url(self):
        """Test that providers require base_url"""
        import pytest

        with pytest.raises(ValueError):
            REDProvider("key", base_url="")

        with pytest.raises(ValueError):
            OPSProvider("key", base_url="")
