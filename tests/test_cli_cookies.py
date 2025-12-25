"""Tests for CLI cookies subcommand."""
import argparse
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest


class TestCookiesCommand:
    """Tests for cookies_command function."""

    def test_upload_from_file(self):
        """Test uploading cookies from a file."""
        from flacfetch.interface.cli import cookies_command

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(""".youtube.com\tTRUE\t/\tTRUE\t1735689600\tPREF\tvalue1
.youtube.com\tTRUE\t/\tTRUE\t1735689600\tSID\tvalue2
""")
            temp_path = f.name

        try:
            args = argparse.Namespace(
                action="upload",
                file=temp_path,
                browser=None,
                server=None,
                api_key=None,
            )

            with patch.dict(os.environ, {"FLACFETCH_REMOTE_URL": ""}, clear=False):
                with patch("builtins.print") as mock_print:
                    cookies_command(args)

                    # Should save locally when no server specified
                    printed_msgs = [str(call) for call in mock_print.call_args_list]
                    # Check that it mentions saving locally
                    assert any("locally" in str(msg).lower() for msg in printed_msgs)
        finally:
            os.unlink(temp_path)

    def test_upload_from_file_not_found(self):
        """Test error when file not found."""
        from flacfetch.interface.cli import cookies_command

        args = argparse.Namespace(
            action="upload",
            file="/nonexistent/file.txt",
            browser=None,
            server=None,
            api_key=None,
        )

        with pytest.raises(SystemExit):
            cookies_command(args)

    def test_upload_to_server_no_api_key(self):
        """Test error when no API key provided for server upload."""
        from flacfetch.interface.cli import cookies_command

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(""".youtube.com\tTRUE\t/\tTRUE\t1735689600\tPREF\tvalue1
""")
            temp_path = f.name

        try:
            args = argparse.Namespace(
                action="upload",
                file=temp_path,
                browser=None,
                server="http://example.com",
                api_key=None,
            )

            with patch.dict(os.environ, {"FLACFETCH_API_KEY": ""}, clear=False):
                with pytest.raises(SystemExit):
                    cookies_command(args)
        finally:
            os.unlink(temp_path)

    def test_upload_to_server_success(self):
        """Test successful upload to server."""
        from flacfetch.interface.cli import cookies_command

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(""".youtube.com\tTRUE\t/\tTRUE\t1735689600\tPREF\tvalue1
""")
            temp_path = f.name

        try:
            args = argparse.Namespace(
                action="upload",
                file=temp_path,
                browser=None,
                server="http://example.com",
                api_key="test-key",
            )

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"success": True, "message": "Uploaded"}

            with patch("httpx.post", return_value=mock_response):
                with patch("builtins.print") as mock_print:
                    cookies_command(args)
                    printed_msgs = " ".join(str(call) for call in mock_print.call_args_list)
                    assert "Uploaded" in printed_msgs or "success" in printed_msgs.lower()
        finally:
            os.unlink(temp_path)

    def test_status_local(self):
        """Test status command for local cookies."""
        from flacfetch.interface.cli import cookies_command

        args = argparse.Namespace(
            action="status",
            file=None,
            browser=None,
            server=None,
            api_key=None,
        )

        with patch.dict(os.environ, {"YOUTUBE_COOKIES_FILE": "/tmp/test.txt"}, clear=False):
            with patch("os.path.exists", return_value=True):
                with patch("builtins.print") as mock_print:
                    cookies_command(args)
                    printed_msgs = " ".join(str(call) for call in mock_print.call_args_list)
                    assert "Local cookies found" in printed_msgs

    def test_status_local_not_found(self):
        """Test status command when no local cookies."""
        from flacfetch.interface.cli import cookies_command

        args = argparse.Namespace(
            action="status",
            file=None,
            browser=None,
            server=None,
            api_key=None,
        )

        with patch.dict(os.environ, {"YOUTUBE_COOKIES_FILE": ""}, clear=False):
            with patch("os.path.exists", return_value=False):
                with patch("builtins.print") as mock_print:
                    cookies_command(args)
                    printed_msgs = " ".join(str(call) for call in mock_print.call_args_list)
                    assert "No local cookies" in printed_msgs

    def test_status_remote(self):
        """Test status command for remote server."""
        from flacfetch.interface.cli import cookies_command

        args = argparse.Namespace(
            action="status",
            file=None,
            browser=None,
            server="http://example.com",
            api_key="test-key",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "configured": True,
            "source": "file",
            "cookies_valid": True,
            "validation_message": "Valid: 5 cookies",
        }

        with patch("httpx.get", return_value=mock_response):
            with patch("builtins.print") as mock_print:
                cookies_command(args)
                printed_msgs = " ".join(str(call) for call in mock_print.call_args_list)
                assert "configured" in printed_msgs.lower()

    def test_delete_local(self):
        """Test delete command for local cookies."""
        from flacfetch.interface.cli import cookies_command

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("# cookies")
            temp_path = f.name

        args = argparse.Namespace(
            action="delete",
            file=None,
            browser=None,
            server=None,
            api_key=None,
        )

        with patch.dict(os.environ, {"YOUTUBE_COOKIES_FILE": temp_path}, clear=False):
            with patch("builtins.print") as mock_print:
                cookies_command(args)
                printed_msgs = " ".join(str(call) for call in mock_print.call_args_list)
                assert "Deleted" in printed_msgs

        # File should be deleted
        assert not os.path.exists(temp_path)

    def test_delete_local_not_found(self):
        """Test delete when no local cookies exist."""
        from flacfetch.interface.cli import cookies_command

        args = argparse.Namespace(
            action="delete",
            file=None,
            browser=None,
            server=None,
            api_key=None,
        )

        with patch.dict(os.environ, {"YOUTUBE_COOKIES_FILE": ""}, clear=False):
            with patch("os.path.exists", return_value=False):
                with patch("builtins.print") as mock_print:
                    cookies_command(args)
                    printed_msgs = " ".join(str(call) for call in mock_print.call_args_list)
                    assert "No local cookies" in printed_msgs

    def test_delete_remote(self):
        """Test delete command for remote server."""
        from flacfetch.interface.cli import cookies_command

        args = argparse.Namespace(
            action="delete",
            file=None,
            browser=None,
            server="http://example.com",
            api_key="test-key",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True, "message": "Deleted"}

        with patch("httpx.delete", return_value=mock_response):
            with patch("builtins.print") as mock_print:
                cookies_command(args)
                printed_msgs = " ".join(str(call) for call in mock_print.call_args_list)
                assert "Deleted" in printed_msgs


class TestCookiesParser:
    """Tests for cookies CLI argument parsing."""

    def test_cookies_subcommand_recognized(self):
        """Test that 'cookies' subcommand is recognized."""
        import sys

        with patch.object(sys, "argv", ["flacfetch", "cookies", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                from flacfetch.interface.cli import main

                main()
            # --help exits with 0
            assert exc_info.value.code == 0

    def test_cookies_upload_action(self):
        """Test cookies upload action parsing."""
        import sys

        # Just test that the parser accepts these arguments
        with patch.object(sys, "argv", ["flacfetch", "cookies", "upload", "--file", "/tmp/test.txt"]):
            with patch("flacfetch.interface.cli.cookies_command") as mock_cmd:
                from flacfetch.interface.cli import main

                main()
                mock_cmd.assert_called_once()
                args = mock_cmd.call_args[0][0]
                assert args.action == "upload"
                assert args.file == "/tmp/test.txt"

    def test_cookies_status_action(self):
        """Test cookies status action parsing."""
        import sys

        with patch.object(sys, "argv", ["flacfetch", "cookies", "status"]):
            with patch("flacfetch.interface.cli.cookies_command") as mock_cmd:
                from flacfetch.interface.cli import main

                main()
                mock_cmd.assert_called_once()
                args = mock_cmd.call_args[0][0]
                assert args.action == "status"

    def test_cookies_delete_action(self):
        """Test cookies delete action parsing."""
        import sys

        with patch.object(sys, "argv", ["flacfetch", "cookies", "delete"]):
            with patch("flacfetch.interface.cli.cookies_command") as mock_cmd:
                from flacfetch.interface.cli import main

                main()
                mock_cmd.assert_called_once()
                args = mock_cmd.call_args[0][0]
                assert args.action == "delete"

    def test_cookies_browser_option(self):
        """Test cookies --browser option."""
        import sys

        with patch.object(sys, "argv", ["flacfetch", "cookies", "upload", "--browser", "firefox"]):
            with patch("flacfetch.interface.cli.cookies_command") as mock_cmd:
                from flacfetch.interface.cli import main

                main()
                args = mock_cmd.call_args[0][0]
                assert args.browser == "firefox"

    def test_cookies_server_option(self):
        """Test cookies --server option."""
        import sys

        with patch.object(sys, "argv", ["flacfetch", "cookies", "status", "--server", "http://example.com"]):
            with patch("flacfetch.interface.cli.cookies_command") as mock_cmd:
                from flacfetch.interface.cli import main

                main()
                args = mock_cmd.call_args[0][0]
                assert args.server == "http://example.com"

    def test_cookies_api_key_option(self):
        """Test cookies --api-key option."""
        import sys

        with patch.object(sys, "argv", ["flacfetch", "cookies", "status", "-k", "my-key"]):
            with patch("flacfetch.interface.cli.cookies_command") as mock_cmd:
                from flacfetch.interface.cli import main

                main()
                args = mock_cmd.call_args[0][0]
                assert args.api_key == "my-key"

