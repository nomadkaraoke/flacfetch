"""
Tests for the remote CLI module.
"""
from flacfetch.interface.cli_remote import (
    RemoteClient,
    convert_api_result_to_display,
    print_progress,
)


class TestConvertApiResult:
    """Tests for convert_api_result_to_display function."""

    def test_converts_provider_to_source_name(self):
        """Test that provider is mapped to source_name."""
        result = {"provider": "RED", "title": "Test"}
        converted = convert_api_result_to_display(result)
        assert converted["source_name"] == "RED"

    def test_preserves_quality_string(self):
        """Test that quality string is preserved as quality_str."""
        result = {
            "provider": "RED",
            "quality": "FLAC 16bit CD",
            "quality_data": {"format": "FLAC", "bit_depth": 16},
        }
        converted = convert_api_result_to_display(result)
        assert converted["quality_str"] == "FLAC 16bit CD"

    def test_maps_quality_data_to_quality_dict(self):
        """Test that quality_data is mapped to quality dict."""
        result = {
            "provider": "RED",
            "quality": "FLAC 16bit CD",
            "quality_data": {"format": "FLAC", "bit_depth": 16, "media": "CD"},
        }
        converted = convert_api_result_to_display(result)
        assert converted["quality"] == {"format": "FLAC", "bit_depth": 16, "media": "CD"}

    def test_handles_missing_quality_data(self):
        """Test that missing quality_data doesn't cause error."""
        result = {"provider": "YouTube", "title": "Test"}
        converted = convert_api_result_to_display(result)
        assert converted["quality"] == {}

    def test_handles_none_quality_data(self):
        """Test that None quality_data doesn't cause error."""
        result = {"provider": "YouTube", "title": "Test", "quality_data": None}
        converted = convert_api_result_to_display(result)
        assert converted["quality"] == {}


class TestRemoteClient:
    """Tests for RemoteClient class."""

    def test_init_stores_base_url(self):
        """Test that base_url is stored correctly."""
        client = RemoteClient("http://example.com", "test-key")
        assert client.base_url == "http://example.com"

    def test_init_strips_trailing_slash(self):
        """Test that trailing slash is stripped from base_url."""
        client = RemoteClient("http://example.com/", "test-key")
        assert client.base_url == "http://example.com"

    def test_init_stores_api_key(self):
        """Test that api_key is stored correctly."""
        client = RemoteClient("http://example.com", "my-secret-key")
        assert client.api_key == "my-secret-key"

    def test_init_default_timeout(self):
        """Test that default timeout is 30 seconds."""
        client = RemoteClient("http://example.com", "test-key")
        assert client.timeout == 30

    def test_init_custom_timeout(self):
        """Test that custom timeout can be set."""
        client = RemoteClient("http://example.com", "test-key", timeout=60)
        assert client.timeout == 60

    def test_headers_returns_api_key(self):
        """Test that _headers returns correct API key header."""
        client = RemoteClient("http://example.com", "my-key")
        headers = client._headers()
        assert headers == {"X-API-Key": "my-key"}


class TestPrintProgress:
    """Tests for print_progress function."""

    def test_downloading_status(self, capsys):
        """Test progress output for downloading status."""
        status = {
            "status": "downloading",
            "progress": 50.0,
            "download_speed_kbps": 500,
            "peers": 5,
        }
        print_progress(status)
        captured = capsys.readouterr()
        assert "50.0%" in captured.out
        assert "Downloading" in captured.out
        assert "500 KB/s" in captured.out
        assert "5 peers" in captured.out

    def test_complete_status(self, capsys):
        """Test progress output for complete status."""
        status = {
            "status": "complete",
            "progress": 100.0,
        }
        print_progress(status)
        captured = capsys.readouterr()
        assert "100.0%" in captured.out
        assert "Complete" in captured.out

    def test_seeding_status(self, capsys):
        """Test progress output for seeding status."""
        status = {
            "status": "seeding",
            "progress": 100.0,
        }
        print_progress(status)
        captured = capsys.readouterr()
        assert "Complete (seeding)" in captured.out

    def test_queued_status(self, capsys):
        """Test progress output for queued status."""
        status = {
            "status": "queued",
            "progress": 0.0,
        }
        print_progress(status)
        captured = capsys.readouterr()
        assert "0.0%" in captured.out
        assert "Queued" in captured.out

    def test_uploading_status(self, capsys):
        """Test progress output for uploading status."""
        status = {
            "status": "uploading",
            "progress": 100.0,
        }
        print_progress(status)
        captured = capsys.readouterr()
        assert "Uploading to GCS" in captured.out

    def test_high_speed_displays_as_mbps(self, capsys):
        """Test that speeds > 1000 KB/s display as MB/s."""
        status = {
            "status": "downloading",
            "progress": 50.0,
            "download_speed_kbps": 2500,  # 2.5 MB/s
            "peers": 10,
        }
        print_progress(status)
        captured = capsys.readouterr()
        assert "2.5 MB/s" in captured.out

