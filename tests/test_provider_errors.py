"""Test error handling and edge cases in providers"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from flacfetch.providers.redacted import RedactedProvider
from flacfetch.providers.ops import OPSProvider
from flacfetch.core.models import TrackQuery, Release, Quality, AudioFormat


class TestProviderErrorHandling:
    """Test error handling in provider implementations"""

    def test_redacted_api_error_handling(self):
        """Test that Redacted handles API errors gracefully"""
        provider = RedactedProvider("fake_key")
        
        with patch('requests.get') as mock_get:
            mock_get.side_effect = Exception("Network error")
            
            query = TrackQuery(artist="Artist", title="Title")
            results = provider.search(query)
            
            # Should return empty list on error
            assert results == []

    def test_ops_api_error_handling(self):
        """Test that OPS handles API errors gracefully"""
        provider = OPSProvider("fake_key")
        
        with patch('requests.get') as mock_get:
            mock_get.side_effect = Exception("Network error")
            
            query = TrackQuery(artist="Artist", title="Title")
            results = provider.search(query)
            
            # Should return empty list on error
            assert results == []

    def test_redacted_empty_response(self):
        """Test Redacted handles empty API responses"""
        provider = RedactedProvider("fake_key")
        
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
        provider = OPSProvider("fake_key")
        
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

    def test_redacted_malformed_response(self):
        """Test Redacted handles malformed API responses"""
        provider = RedactedProvider("fake_key")
        
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
        provider = OPSProvider("fake_key")
        
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
        provider = RedactedProvider("fake_key")
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

    def test_redacted_quality_parsing_edge_cases(self):
        """Test quality parsing with unusual formats"""
        provider = RedactedProvider("fake_key")
        
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
        provider = OPSProvider("fake_key")
        
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
        provider = RedactedProvider("fake_key")
        
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
        red = RedactedProvider("key")
        ops = OPSProvider("key")
        
        assert red.name == "Redacted"
        assert ops.name == "OPS"

    def test_provider_cache_dir(self):
        """Test cache directory setting"""
        provider = RedactedProvider("key")
        # Cache dir is set by default to user cache directory
        assert provider.cache_dir is not None
        
        provider.cache_dir = "/tmp/cache"
        assert str(provider.cache_dir) == "/tmp/cache"

    def test_provider_search_limit_default(self):
        """Test default search limit"""
        provider = RedactedProvider("key")
        # Default is 20
        assert provider.search_limit == 20
        
        provider.search_limit = 5
        assert provider.search_limit == 5

