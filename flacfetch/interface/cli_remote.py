"""
Remote CLI for flacfetch - download via remote flacfetch HTTP API.

Usage:
    # Search and download
    flacfetch-remote "Artist" "Title"
    flacfetch-remote -a "Artist" -t "Title" --auto
    flacfetch-remote "Artist" "Title" -o ~/Music

    # Credential management
    flacfetch-remote check                     # Check remote server credentials
    flacfetch-remote fix                       # Interactive credential fix (local auth → upload → restart)
    flacfetch-remote push                      # Push local credentials to GCP Secret Manager
    flacfetch-remote restart                   # Restart cloud server to pick up new secrets

Requires environment variables:
    FLACFETCH_API_URL   - URL of the flacfetch API (e.g., http://104.198.214.26:8080)
    FLACFETCH_API_KEY   - API key for authentication
"""
import argparse
import os
import sys
import time
from typing import Any, Dict, Optional

try:
    import httpx
except ImportError:
    httpx = None

from .cli import Colors, format_release_line, print_categorized_releases, print_releases


class RemoteClient:
    """Client for the remote flacfetch HTTP API."""

    def __init__(self, base_url: str, api_key: str, timeout: int = 30):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        return {"X-API-Key": self.api_key}

    def health_check(self) -> Dict[str, Any]:
        """Check if service is healthy."""
        with httpx.Client() as client:
            resp = client.get(
                f"{self.base_url}/health",
                headers=self._headers(),
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()

    def check_credentials(self) -> Dict[str, Any]:
        """Check credentials on the remote server."""
        with httpx.Client() as client:
            resp = client.get(
                f"{self.base_url}/credentials/check",
                headers=self._headers(),
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()

    def search(self, artist: str, title: str, exhaustive: bool = False) -> Dict[str, Any]:
        """Search for audio."""
        with httpx.Client() as client:
            payload = {"artist": artist, "title": title}
            if exhaustive:
                payload["exhaustive"] = True
            resp = client.post(
                f"{self.base_url}/search",
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            )
            if resp.status_code == 404:
                return {"search_id": None, "results": [], "results_count": 0}
            resp.raise_for_status()
            return resp.json()

    def download(
        self,
        search_id: str,
        result_index: int,
        output_filename: Optional[str] = None,
        gcs_path: Optional[str] = None,
    ) -> str:
        """Start download and return download_id."""
        with httpx.Client() as client:
            payload = {
                "search_id": search_id,
                "result_index": result_index,
            }
            if output_filename:
                payload["output_filename"] = output_filename
            if gcs_path:
                payload["upload_to_gcs"] = True
                payload["gcs_path"] = gcs_path

            resp = client.post(
                f"{self.base_url}/download",
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json()["download_id"]

    def get_download_status(self, download_id: str) -> Dict[str, Any]:
        """Get download status."""
        with httpx.Client() as client:
            resp = client.get(
                f"{self.base_url}/download/{download_id}/status",
                headers=self._headers(),
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()

    def wait_for_download(
        self,
        download_id: str,
        timeout: int = 600,
        poll_interval: float = 2.0,
        progress_callback=None,
    ) -> Dict[str, Any]:
        """
        Wait for download to complete with progress updates.

        Args:
            download_id: Download ID to poll
            timeout: Maximum wait time in seconds
            poll_interval: Seconds between status checks
            progress_callback: Optional callback(status_dict) for progress updates

        Returns:
            Final status dict

        Raises:
            RuntimeError on failure or timeout
        """
        elapsed = 0
        while elapsed < timeout:
            status = self.get_download_status(download_id)

            if progress_callback:
                progress_callback(status)

            if status["status"] == "complete":
                return status
            elif status["status"] == "seeding":
                # Seeding means download is done, just seeding now
                return status
            elif status["status"] == "failed":
                raise RuntimeError(f"Download failed: {status.get('error', 'Unknown error')}")
            elif status["status"] == "cancelled":
                raise RuntimeError("Download was cancelled")

            time.sleep(poll_interval)
            elapsed += poll_interval

        raise RuntimeError(f"Download timed out after {timeout}s")

    def upload_cookies(self, cookies_content: str) -> Dict[str, Any]:
        """
        Upload YouTube cookies to the remote server (immediate effect).

        Args:
            cookies_content: Cookies in Netscape format

        Returns:
            Response dict with success status and message
        """
        with httpx.Client() as client:
            resp = client.post(
                f"{self.base_url}/config/youtube-cookies",
                headers=self._headers(),
                json={"cookies": cookies_content},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()

    def fetch_file(
        self,
        download_id: str,
        output_path: str,
        progress_callback=None,
    ) -> str:
        """
        Download the completed file from the server to local disk.

        Args:
            download_id: Download ID
            output_path: Local path (file or directory) to save the file
            progress_callback: Optional callback(downloaded_bytes, total_bytes) for progress

        Returns:
            Path to the downloaded file

        Raises:
            RuntimeError on failure
        """
        url = f"{self.base_url}/download/{download_id}/file"

        with httpx.stream("GET", url, headers=self._headers(), timeout=300) as response:
            if response.status_code != 200:
                raise RuntimeError(f"Failed to fetch file: HTTP {response.status_code}")

            # Get total size from Content-Length header
            total_size = int(response.headers.get("content-length", 0))

            # Get filename from Content-Disposition header
            # Formats:
            #   attachment; filename="Avril Lavigne - Unwanted.flac"
            #   attachment; filename*=utf-8''Avril%20Lavigne%20-%20Unwanted.flac
            filename = None
            content_disp = response.headers.get("content-disposition", "")
            if "filename" in content_disp:
                import re
                from urllib.parse import unquote

                # Try RFC 5987 format first: filename*=utf-8''encoded%20name
                match = re.search(r"filename\*=(?:utf-8''|UTF-8'')([^;\s]+)", content_disp)
                if match:
                    filename = unquote(match.group(1))
                else:
                    # Standard format: filename="name" or filename=name
                    match = re.search(r'filename="?([^";\n]+)"?', content_disp)
                    if match:
                        filename = match.group(1).strip()

            # Determine final output path
            final_path = output_path
            if os.path.isdir(output_path) or output_path.endswith("/"):
                # output_path is a directory - need filename
                if not filename:
                    filename = f"download_{download_id}.flac"
                os.makedirs(output_path, exist_ok=True)
                final_path = os.path.join(output_path.rstrip("/"), filename)

            downloaded = 0
            with open(final_path, "wb") as f:
                for chunk in response.iter_bytes(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total_size > 0:
                        progress_callback(downloaded, total_size)

        return final_path


def convert_api_result_to_display(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert API search result to format expected by print_releases.

    The API returns SearchResultItem format, we need to map it to
    what format_release_line expects.
    """
    # Map provider back to source_name
    result["source_name"] = result.get("provider", "Unknown")

    # Preserve API index for accurate selection (avoids 24-bit vs 16-bit confusion)
    result["api_index"] = result.get("index")

    # Store quality string separately - API has "quality" as the display string
    # and "quality_data" as the structured data
    result["quality_str"] = result.get("quality", "")

    # The quality dict is for structured access (format, bit_depth, etc.)
    # Keep quality_data as-is for media name extraction
    quality_data = result.get("quality_data") or {}
    result["quality"] = quality_data

    return result


def print_progress(status: Dict[str, Any]) -> None:
    """Print download progress to terminal."""
    progress = status.get("progress", 0)
    dl_status = status.get("status", "unknown")
    speed = status.get("download_speed_kbps", 0)
    peers = status.get("peers", 0)

    # Build progress bar
    bar_width = 30
    filled = int(bar_width * progress / 100)
    bar = "█" * filled + "░" * (bar_width - filled)

    # Status indicator
    if dl_status == "downloading":
        status_str = f"{Colors.CYAN}Downloading{Colors.RESET}"
    elif dl_status == "uploading":
        status_str = f"{Colors.YELLOW}Uploading to GCS{Colors.RESET}"
    elif dl_status == "seeding":
        status_str = f"{Colors.GREEN}Complete (seeding){Colors.RESET}"
    elif dl_status == "complete":
        status_str = f"{Colors.GREEN}Complete{Colors.RESET}"
    elif dl_status == "queued":
        status_str = f"{Colors.DIM}Queued{Colors.RESET}"
    else:
        status_str = dl_status

    # Speed and peers info
    extra = ""
    if dl_status == "downloading":
        if speed > 0:
            if speed > 1000:
                speed_str = f"{speed/1000:.1f} MB/s"
            else:
                speed_str = f"{speed:.0f} KB/s"
            extra = f" | {speed_str}"
        if peers > 0:
            extra += f" | {peers} peers"

    # Print on same line (carriage return)
    print(f"\r{Colors.BOLD}Progress:{Colors.RESET} [{bar}] {progress:5.1f}% | {status_str}{extra}   ", end="", flush=True)


# =============================================================================
# Credential Management Commands
# =============================================================================

def get_api_credentials():
    """Get API URL and key from environment or fail with helpful message."""
    api_url = os.environ.get("FLACFETCH_API_URL")
    api_key = os.environ.get("FLACFETCH_API_KEY")

    if not api_url:
        print(f"\n{Colors.RED}Error: FLACFETCH_API_URL not set{Colors.RESET}")
        print("\nSet the environment variable:")
        print("  export FLACFETCH_API_URL=http://your-server:8080")
        sys.exit(1)

    if not api_key:
        print(f"\n{Colors.RED}Error: FLACFETCH_API_KEY not set{Colors.RESET}")
        print("\nSet the environment variable:")
        print("  export FLACFETCH_API_KEY=your-api-key")
        sys.exit(1)

    return api_url, api_key


def check_command(args):
    """Check credentials on the remote server."""
    if httpx is None:
        print(f"{Colors.RED}Error: httpx not installed. Install with: pip install httpx{Colors.RESET}")
        sys.exit(1)

    api_url, api_key = get_api_credentials()
    client = RemoteClient(api_url, api_key)

    print(f"\n{Colors.BOLD}Checking REMOTE server credentials...{Colors.RESET}")
    print(f"{Colors.DIM}Server: {api_url}{Colors.RESET}\n")

    try:
        result = client.check_credentials()
    except Exception as e:
        print(f"{Colors.RED}Error connecting to server: {e}{Colors.RESET}")
        sys.exit(1)

    services = result.get("services", {})
    all_ok = True

    for name, data in services.items():
        status = data.get("status", "unknown")
        message = data.get("message", "")
        needs_action = data.get("needs_human_action", False)

        if status == "ok":
            icon = f"{Colors.GREEN}✓{Colors.RESET}"
            color = Colors.GREEN
        elif status == "missing":
            icon = f"{Colors.YELLOW}○{Colors.RESET}"
            color = Colors.YELLOW
            if needs_action:
                all_ok = False
        else:
            icon = f"{Colors.RED}✗{Colors.RESET}"
            color = Colors.RED
            all_ok = False

        print(f"{icon} {name.title()}: {color}{status}{Colors.RESET}")
        print(f"  {Colors.DIM}{message}{Colors.RESET}")
        if data.get("fix_command"):
            print(f"  {Colors.DIM}Fix: {data['fix_command']}{Colors.RESET}")
        print()

    if all_ok:
        print(f"{Colors.GREEN}All remote credentials are OK!{Colors.RESET}\n")
    else:
        print(f"{Colors.YELLOW}Some credentials need attention.{Colors.RESET}")
        print(f"{Colors.DIM}Run 'flacfetch-remote fix' to repair them.{Colors.RESET}\n")


def push_command(args):
    """Push local credentials to GCP Secret Manager."""
    import subprocess

    from ..api.services.credential_check import (
        get_local_spotify_cache_path,
        get_local_youtube_cookies_path,
    )

    project = args.project or "nomadkaraoke"

    print(f"\n{Colors.BOLD}Pushing local credentials to GCP Secret Manager...{Colors.RESET}")
    print(f"{Colors.DIM}Project: {project}{Colors.RESET}\n")

    # Push Spotify token
    spotify_cache = get_local_spotify_cache_path()
    if os.path.exists(spotify_cache):
        print(f"{Colors.CYAN}Uploading Spotify token...{Colors.RESET}")
        result = subprocess.run(
            ["gcloud", "secrets", "versions", "add", "spotify-oauth-token",
             f"--data-file={spotify_cache}", f"--project={project}"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print(f"{Colors.GREEN}✓ Spotify token uploaded{Colors.RESET}")
        else:
            print(f"{Colors.RED}✗ Failed to upload Spotify token: {result.stderr}{Colors.RESET}")
    else:
        print(f"{Colors.YELLOW}○ No local Spotify token found at {spotify_cache}{Colors.RESET}")
        print(f"  {Colors.DIM}Run 'flacfetch fix' first to authenticate locally{Colors.RESET}")

    # Push YouTube cookies
    cookies_path = get_local_youtube_cookies_path()
    if os.path.exists(cookies_path):
        print(f"{Colors.CYAN}Uploading YouTube cookies...{Colors.RESET}")
        result = subprocess.run(
            ["gcloud", "secrets", "versions", "add", "youtube-cookies",
             f"--data-file={cookies_path}", f"--project={project}"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print(f"{Colors.GREEN}✓ YouTube cookies uploaded{Colors.RESET}")
        else:
            print(f"{Colors.RED}✗ Failed to upload YouTube cookies: {result.stderr}{Colors.RESET}")
    else:
        print(f"{Colors.YELLOW}○ No local YouTube cookies found at {cookies_path}{Colors.RESET}")
        print(f"  {Colors.DIM}Run 'flacfetch fix' first to set up cookies locally{Colors.RESET}")

    print(f"\n{Colors.GREEN}Done!{Colors.RESET}")
    print(f"{Colors.DIM}Run 'flacfetch-remote restart' to apply changes on the server.{Colors.RESET}\n")


def restart_command(args):
    """Restart the cloud server to pick up new secrets."""
    import subprocess

    project = args.project or "nomadkaraoke"
    zone = args.zone or "us-central1-a"
    instance = args.instance or "flacfetch-service"

    print(f"\n{Colors.BOLD}Restarting cloud server...{Colors.RESET}")
    print(f"{Colors.DIM}Instance: {instance} (zone: {zone}, project: {project}){Colors.RESET}\n")

    if not args.yes:
        confirm = input(f"{Colors.YELLOW}Restart server? [Y/n]: {Colors.RESET}").strip().lower()
        if confirm and confirm != 'y':
            print(f"{Colors.YELLOW}Cancelled.{Colors.RESET}")
            return

    result = subprocess.run(
        ["gcloud", "compute", "instances", "reset", instance,
         f"--zone={zone}", f"--project={project}"],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        print(f"{Colors.GREEN}✓ Server restart initiated!{Colors.RESET}")
        print(f"{Colors.DIM}It may take 1-2 minutes for the server to come back online.{Colors.RESET}\n")
    else:
        print(f"{Colors.RED}✗ Failed to restart server: {result.stderr}{Colors.RESET}")


def _input_with_completion(prompt: str, default: str = "") -> str:
    """Input with tab completion for file paths."""
    import glob
    import readline

    def complete_path(text, state):
        """Tab completion for file paths."""
        # Expand ~ to home directory for matching
        if text.startswith("~"):
            expanded = os.path.expanduser(text)
            matches = glob.glob(expanded + "*")
            # Convert back to ~ notation for display
            home = os.path.expanduser("~")
            matches = [m.replace(home, "~") for m in matches]
        else:
            matches = glob.glob(text + "*")

        # Add trailing slash for directories
        matches = [m + "/" if os.path.isdir(os.path.expanduser(m)) else m for m in matches]

        try:
            return matches[state]
        except IndexError:
            return None

    # Set up readline with path completion
    old_completer = readline.get_completer()
    old_delims = readline.get_completer_delims()

    readline.set_completer(complete_path)
    readline.set_completer_delims(" \t\n;")
    readline.parse_and_bind("tab: complete")

    try:
        # Show default in prompt if provided
        if default:
            display_prompt = f"{prompt} [{default}]: "
        else:
            display_prompt = f"{prompt}: "

        result = input(display_prompt).strip()
        return result if result else default
    finally:
        # Restore old completer
        readline.set_completer(old_completer)
        readline.set_completer_delims(old_delims)


def _extract_cookies_playwright(headless: bool = False) -> tuple[bool, str, str]:
    """
    Extract YouTube cookies using Playwright.

    Returns:
        Tuple of (success, cookies_content, message)
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, "", "Playwright not installed. Install with: pip install playwright && playwright install chromium"

    print(f"{Colors.CYAN}Launching your Chrome browser for YouTube login...{Colors.RESET}")
    print(f"{Colors.DIM}Please log in to YouTube if prompted.{Colors.RESET}\n")

    try:
        with sync_playwright() as p:
            # Use persistent context to remember login
            # Use a separate profile dir to avoid conflicts with your main Chrome profile
            user_data_dir = os.path.expanduser("~/.flacfetch/chrome-profile")
            os.makedirs(user_data_dir, exist_ok=True)

            # Use the real Chrome browser (not Chromium) to avoid Google's bot detection
            # channel="chrome" uses the system-installed Chrome
            try:
                browser = p.chromium.launch_persistent_context(
                    user_data_dir,
                    headless=headless,
                    channel="chrome",  # Use real Chrome, not Chromium
                )
            except Exception as e:
                # Fall back to chromium if Chrome isn't installed
                print(f"{Colors.YELLOW}Could not launch Chrome ({e}), trying Chromium...{Colors.RESET}")
                browser = p.chromium.launch_persistent_context(
                    user_data_dir,
                    headless=headless,
                )

            page = browser.new_page()

            # Navigate to YouTube Studio first - this ensures the user selects the correct channel
            # YouTube Studio requires channel selection, so cookies will be for the right profile
            studio_url = "https://studio.youtube.com"
            print(f"{Colors.CYAN}Navigating to YouTube Studio...{Colors.RESET}")
            print(f"{Colors.DIM}This ensures you're logged in with the correct channel profile.{Colors.RESET}\n")

            page.goto(studio_url)

            # Wait for user to be logged in to the correct channel
            print(f"{Colors.YELLOW}Please complete the following in the browser:{Colors.RESET}")
            print("  1. Sign in to Google if prompted")
            print("  2. Select the correct YouTube channel if prompted")
            print("  3. Wait until you see the YouTube Studio dashboard")
            print()

            # Wait for Studio dashboard to load (indicates successful channel login)
            try:
                # YouTube Studio dashboard has specific elements when loaded
                # Wait up to 120 seconds for user to complete login and channel selection
                print(f"{Colors.DIM}Waiting for YouTube Studio dashboard to load...{Colors.RESET}")

                for _ in range(60):
                    # Check if we're on the Studio dashboard
                    current_url = page.url
                    if "studio.youtube.com/channel/" in current_url:
                        print(f"{Colors.GREEN}✓ Detected YouTube Studio dashboard{Colors.RESET}")
                        break
                    # Also check for the dashboard content area
                    dashboard = page.query_selector('ytcp-dashboard, #dashboard, [page-id="dashboard"]')
                    if dashboard:
                        print(f"{Colors.GREEN}✓ Detected YouTube Studio dashboard{Colors.RESET}")
                        break
                    time.sleep(2)
                else:
                    # After timeout, ask user to confirm
                    print(f"\n{Colors.YELLOW}Could not auto-detect dashboard.{Colors.RESET}")

            except Exception:
                pass

            # Let user confirm they're ready
            print()
            print(f"{Colors.YELLOW}⚠ DO NOT close the browser window!{Colors.RESET}")
            input(f"{Colors.CYAN}Press Enter here (keep browser open) to extract cookies...{Colors.RESET}")

            # Navigate to YouTube main site to collect all YouTube cookies
            print(f"\n{Colors.DIM}Collecting cookies from YouTube...{Colors.RESET}")
            try:
                page.goto("https://www.youtube.com", timeout=10000)
                time.sleep(2)  # Let cookies settle
            except Exception:
                # Browser might have been closed
                return False, "", "Browser was closed before cookies could be extracted. Please keep the browser open until extraction completes."

            # Get all cookies
            cookies = browser.cookies()

            # Filter for YouTube/Google cookies and convert to Netscape format
            netscape_lines = ["# Netscape HTTP Cookie File", "# https://curl.haxx.se/rfc/cookie_spec.html", "# This is a generated file! Do not edit.", ""]

            youtube_cookies = 0
            login_info = None
            for cookie in cookies:
                # Try to identify the logged-in account from cookies
                if cookie.get("name") == "LOGIN_INFO" and "youtube" in cookie.get("domain", ""):
                    login_info = cookie.get("value", "")[:50] + "..."
                domain = cookie.get("domain", "")
                if "youtube" in domain.lower() or "google" in domain.lower():
                    youtube_cookies += 1

                # Convert to Netscape format
                # domain, flag, path, secure, expiry, name, value
                flag = "TRUE" if domain.startswith(".") else "FALSE"
                secure = "TRUE" if cookie.get("secure", False) else "FALSE"
                expiry = str(int(cookie.get("expires", 0)))
                if expiry == "-1" or expiry == "0":
                    expiry = "0"

                line = "\t".join([
                    domain,
                    flag,
                    cookie.get("path", "/"),
                    secure,
                    expiry,
                    cookie.get("name", ""),
                    cookie.get("value", ""),
                ])
                netscape_lines.append(line)

            browser.close()

            if youtube_cookies == 0:
                return False, "", "No YouTube/Google cookies found. Make sure you're logged in to YouTube."

            cookies_content = "\n".join(netscape_lines)
            msg = f"Extracted {youtube_cookies} YouTube/Google cookies"
            if login_info:
                msg += " (LOGIN_INFO present)"
            return True, cookies_content, msg

    except Exception as e:
        return False, "", f"Error extracting cookies: {e}"


def fix_command(args):
    """
    Interactive credential fix for remote server.

    Checks credentials first, then only fixes what needs fixing.
    Uses API for live cookie updates (no restart needed for cookies).
    """
    import subprocess


    project = args.project or "nomadkaraoke"

    print(f"\n{Colors.BOLD}Flacfetch Remote Credential Fix Tool{Colors.RESET}")
    print(f"{Colors.DIM}This tool will check and fix credentials on the cloud server.{Colors.RESET}\n")

    # Track what was actually fixed
    spotify_fixed = False
    youtube_fixed = False
    any_errors = False

    # -------------------------------------------------------------------------
    # Check remote credentials first
    # -------------------------------------------------------------------------
    print(f"{Colors.BOLD}━━━ Checking Remote Credentials ━━━{Colors.RESET}\n")

    api_url, api_key = get_api_credentials()
    client = None
    remote_spotify_ok = False
    remote_youtube_ok = False

    try:
        client = RemoteClient(api_url, api_key)
        result = client.check_credentials()
        services = result.get("services", {})

        for name, data in services.items():
            status = data.get("status", "unknown")
            message = data.get("message", "")
            needs_action = data.get("needs_human_action", False)

            if status == "ok":
                icon = f"{Colors.GREEN}✓{Colors.RESET}"
                if name == "spotify":
                    remote_spotify_ok = True
                elif name == "youtube":
                    remote_youtube_ok = True
            elif status == "missing" and not needs_action:
                icon = f"{Colors.YELLOW}○{Colors.RESET}"
                if name == "youtube":
                    remote_youtube_ok = True  # YouTube without cookies is OK for most videos
            else:
                icon = f"{Colors.RED}✗{Colors.RESET}"

            print(f"{icon} {name.title()}: {message}")

        print()

    except Exception as e:
        print(f"{Colors.YELLOW}○ Could not check remote credentials: {e}{Colors.RESET}")
        print(f"{Colors.DIM}Will proceed with credential setup anyway.{Colors.RESET}\n")

    # -------------------------------------------------------------------------
    # Spotify
    # -------------------------------------------------------------------------
    print(f"{Colors.BOLD}━━━ Spotify ━━━{Colors.RESET}")

    client_id = os.environ.get("SPOTIPY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET")

    if not client_id or not client_secret:
        print(f"{Colors.YELLOW}○ Spotify credentials not found in environment{Colors.RESET}")
        print(f"  {Colors.DIM}Set SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET{Colors.RESET}\n")
    elif remote_spotify_ok:
        print(f"{Colors.GREEN}✓ Spotify credentials are working{Colors.RESET}")
        reauth = input("\nRe-authenticate anyway? [y/N]: ").strip().lower()
        if reauth != 'y':
            print(f"{Colors.DIM}Skipping Spotify (already working){Colors.RESET}\n")
        else:
            # User wants to re-auth anyway
            spotify_fixed = _fix_spotify(client_id, client_secret, project)
            if not spotify_fixed:
                any_errors = True
    else:
        print(f"{Colors.YELLOW}○ Spotify needs authentication{Colors.RESET}\n")
        spotify_fixed = _fix_spotify(client_id, client_secret, project)
        if not spotify_fixed:
            any_errors = True

    # -------------------------------------------------------------------------
    # YouTube
    # -------------------------------------------------------------------------
    print(f"{Colors.BOLD}━━━ YouTube ━━━{Colors.RESET}")

    if remote_youtube_ok:
        print(f"{Colors.GREEN}✓ YouTube cookies are working{Colors.RESET}")
        update_yt = input("\nUpdate cookies anyway? [y/N]: ").strip().lower()
        if update_yt != 'y':
            print(f"{Colors.DIM}Skipping YouTube (already working){Colors.RESET}\n")
        else:
            youtube_fixed, yt_error = _fix_youtube(client, project)
            if yt_error:
                any_errors = True
    else:
        print(f"{Colors.YELLOW}○ YouTube cookies need updating{Colors.RESET}\n")
        youtube_fixed, yt_error = _fix_youtube(client, project)
        if yt_error:
            any_errors = True

    # -------------------------------------------------------------------------
    # Summary and restart
    # -------------------------------------------------------------------------
    print(f"{Colors.BOLD}━━━ Summary ━━━{Colors.RESET}\n")

    if not spotify_fixed and not youtube_fixed:
        if any_errors:
            print(f"{Colors.YELLOW}No credentials were updated due to errors.{Colors.RESET}")
            print(f"{Colors.DIM}Please resolve the errors above and try again.{Colors.RESET}\n")
        else:
            print(f"{Colors.GREEN}All credentials are working! Nothing to fix.{Colors.RESET}\n")
        return

    # Report what was fixed
    if spotify_fixed:
        print(f"{Colors.GREEN}✓ Spotify credentials updated{Colors.RESET}")
    if youtube_fixed:
        print(f"{Colors.GREEN}✓ YouTube cookies updated{Colors.RESET}")
    print()

    # YouTube cookies are uploaded via API and take effect immediately
    # Spotify token is uploaded to GCP Secret Manager and needs a restart
    if spotify_fixed:
        print(f"{Colors.BOLD}━━━ Apply Changes ━━━{Colors.RESET}\n")
        print(f"{Colors.DIM}Spotify token was uploaded to GCP Secret Manager.{Colors.RESET}")
        print(f"{Colors.DIM}A server restart is needed for Spotify changes to take effect.{Colors.RESET}\n")

        restart = input("Restart cloud server? [Y/n]: ").strip().lower()
        if not restart or restart == 'y':
            print(f"\n{Colors.CYAN}Restarting flacfetch-service...{Colors.RESET}")
            result = subprocess.run(
                ["gcloud", "compute", "instances", "reset", "flacfetch-service",
                 "--zone=us-central1-a", f"--project={project}"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                print(f"{Colors.GREEN}✓ Server restart initiated!{Colors.RESET}")
                print(f"{Colors.DIM}It may take 1-2 minutes for the server to come back online.{Colors.RESET}\n")
            else:
                print(f"{Colors.RED}✗ Failed to restart: {result.stderr}{Colors.RESET}\n")
    elif youtube_fixed:
        print(f"{Colors.GREEN}YouTube cookies were uploaded via API and are active immediately.{Colors.RESET}")
        print(f"{Colors.DIM}No server restart needed.{Colors.RESET}\n")

    print(f"{Colors.GREEN}Done!{Colors.RESET}\n")


def _fix_spotify(client_id: str, client_secret: str, project: str) -> bool:
    """
    Fix Spotify credentials.

    Returns True if successfully fixed, False otherwise.
    """
    import subprocess

    from ..api.services.credential_check import get_local_spotify_cache_path

    cache_path = get_local_spotify_cache_path()

    # Remove old cache to force re-auth
    if os.path.exists(cache_path):
        os.remove(cache_path)

    print(f"\n{Colors.CYAN}Opening browser for Spotify login...{Colors.RESET}")
    print(f"{Colors.DIM}Complete the login in your browser.{Colors.RESET}\n")

    try:
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth

        auth_manager = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=os.environ.get("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
            scope="user-read-playback-state user-modify-playback-state streaming",
            cache_path=cache_path,
            open_browser=True,
        )

        sp = spotipy.Spotify(auth_manager=auth_manager)
        user = sp.current_user()
        print(f"\n{Colors.GREEN}✓ Authenticated as: {user.get('display_name', user.get('id'))}{Colors.RESET}\n")

        # Upload to GCP
        print(f"{Colors.CYAN}Uploading token to GCP Secret Manager...{Colors.RESET}")
        result = subprocess.run(
            ["gcloud", "secrets", "versions", "add", "spotify-oauth-token",
             f"--data-file={cache_path}", f"--project={project}"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print(f"{Colors.GREEN}✓ Spotify token uploaded to GCP!{Colors.RESET}\n")
            return True
        else:
            print(f"{Colors.RED}✗ Failed to upload: {result.stderr}{Colors.RESET}\n")
            return False

    except ImportError:
        print(f"{Colors.RED}✗ spotipy not installed. Install with: pip install spotipy{Colors.RESET}\n")
        return False
    except Exception as e:
        print(f"{Colors.RED}✗ Error: {e}{Colors.RESET}\n")
        return False


def _fix_youtube(client: Optional["RemoteClient"], project: str) -> tuple[bool, bool]:
    """
    Fix YouTube cookies.

    Returns tuple of (fixed, had_error).
    """
    import subprocess

    from ..api.services.credential_check import get_local_youtube_cookies_path

    print(f"\n{Colors.CYAN}Options:{Colors.RESET}")
    print("  1. Automated browser login (Playwright)")
    print("  2. Provide cookies file (from browser extension export)")
    print()

    choice = input("Choose method [1/2] (default: 1): ").strip() or "1"

    cookies_content = None
    upload_file = None

    if choice == "1":
        # Playwright extraction
        success, cookies_content, message = _extract_cookies_playwright(headless=False)
        if success:
            print(f"{Colors.GREEN}✓ {message}{Colors.RESET}")
        else:
            print(f"{Colors.RED}✗ {message}{Colors.RESET}")
            print(f"\n{Colors.DIM}Falling back to file input...{Colors.RESET}\n")
            choice = "2"  # Fall back to file input

    if choice == "2":
        # File input with tab completion
        print(f"\n{Colors.CYAN}Provide a cookies file exported from your browser.{Colors.RESET}")
        print(f"{Colors.DIM}Use a browser extension like 'Get cookies.txt LOCALLY' (Chrome){Colors.RESET}")
        print(f"{Colors.DIM}or 'cookies.txt' by Lennon Hill (Firefox).{Colors.RESET}\n")

        default_path = "~/Downloads/youtube_cookies.txt"
        file_path = _input_with_completion("Path to cookies file", default_path)
        file_path = os.path.expanduser(file_path)

        if os.path.exists(file_path):
            try:
                with open(file_path) as f:
                    cookies_content = f.read()
                print(f"{Colors.GREEN}✓ Found cookies file ({os.path.getsize(file_path)} bytes){Colors.RESET}")
            except Exception as e:
                print(f"{Colors.RED}✗ Error reading file: {e}{Colors.RESET}")
                return False, True
        else:
            print(f"{Colors.RED}✗ File not found: {file_path}{Colors.RESET}")
            return False, True

    if not cookies_content:
        return False, True

    # Validate cookies format
    lines = cookies_content.strip().split("\n")
    youtube_cookies = 0
    for line in lines:
        if line.strip() and not line.startswith("#"):
            parts = line.split("\t")
            if len(parts) == 7:
                domain = parts[0].lower()
                if "youtube" in domain or "google" in domain:
                    youtube_cookies += 1

    if youtube_cookies == 0:
        print(f"{Colors.RED}✗ No YouTube/Google cookies found in the file{Colors.RESET}")
        return False, True

    print(f"{Colors.GREEN}✓ Validated {youtube_cookies} YouTube/Google cookies{Colors.RESET}")

    # Save locally
    local_cookies = get_local_youtube_cookies_path()
    os.makedirs(os.path.dirname(local_cookies), exist_ok=True)
    with open(local_cookies, "w") as f:
        f.write(cookies_content)
    print(f"{Colors.GREEN}✓ Saved locally to {local_cookies}{Colors.RESET}")

    # Try to upload via API first (immediate effect, no restart needed)
    if client:
        print(f"{Colors.CYAN}Uploading cookies via API (immediate effect)...{Colors.RESET}")
        try:
            result = client.upload_cookies(cookies_content)
            if result.get("success"):
                print(f"{Colors.GREEN}✓ Cookies uploaded to server{Colors.RESET}")

                # Verify the cookies actually work by re-checking credentials
                print(f"{Colors.CYAN}Verifying cookies work on server...{Colors.RESET}")
                try:
                    check_result = client.check_credentials()
                    yt_status = check_result.get("services", {}).get("youtube", {})
                    if yt_status.get("status") == "ok":
                        print(f"{Colors.GREEN}✓ Verified: {yt_status.get('message')}{Colors.RESET}\n")
                        return True, False
                    else:
                        print(f"{Colors.RED}✗ Cookies uploaded but verification failed: {yt_status.get('message')}{Colors.RESET}")
                        print(f"{Colors.YELLOW}The cookies may not have the correct channel permissions.{Colors.RESET}")
                        print(f"{Colors.DIM}Make sure you're logged in as the YouTube channel that owns the test video.{Colors.RESET}\n")
                        return False, True
                except Exception as verify_err:
                    print(f"{Colors.YELLOW}○ Could not verify: {verify_err}{Colors.RESET}")
                    print(f"{Colors.GREEN}✓ Cookies uploaded (verification skipped){Colors.RESET}\n")
                    return True, False
            else:
                print(f"{Colors.YELLOW}○ API upload failed: {result.get('message', 'Unknown error')}{Colors.RESET}")
                print(f"{Colors.DIM}Falling back to GCP Secret Manager...{Colors.RESET}")
        except Exception as e:
            print(f"{Colors.YELLOW}○ API upload failed: {e}{Colors.RESET}")
            print(f"{Colors.DIM}Falling back to GCP Secret Manager...{Colors.RESET}")

    # Fall back to GCP Secret Manager (requires restart)
    print(f"{Colors.CYAN}Uploading cookies to GCP Secret Manager...{Colors.RESET}")

    # Write to temp file for gcloud
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write(cookies_content)
        upload_file = f.name

    try:
        result = subprocess.run(
            ["gcloud", "secrets", "versions", "add", "youtube-cookies",
             f"--data-file={upload_file}", f"--project={project}"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print(f"{Colors.GREEN}✓ YouTube cookies uploaded to GCP!{Colors.RESET}")
            print(f"{Colors.YELLOW}Note: Server restart required for GCP secret to take effect.{Colors.RESET}\n")
            return True, False
        else:
            print(f"{Colors.RED}✗ Failed to upload: {result.stderr}{Colors.RESET}\n")
            return False, True
    finally:
        if upload_file and os.path.exists(upload_file):
            os.remove(upload_file)


def main():
    """Main entry point for flacfetch-remote CLI."""
    # Handle credential subcommands before main parser
    if len(sys.argv) > 1:
        subcommand = sys.argv[1]

        if subcommand == "check":
            # Check remote server credentials
            parser = argparse.ArgumentParser(
                prog="flacfetch-remote check",
                description="Check credentials on the remote server",
            )
            args = parser.parse_args(sys.argv[2:])
            check_command(args)
            return

        elif subcommand == "push":
            # Push local credentials to GCP
            parser = argparse.ArgumentParser(
                prog="flacfetch-remote push",
                description="Push local credentials to GCP Secret Manager",
            )
            parser.add_argument("--project", default="nomadkaraoke", help="GCP project ID")
            args = parser.parse_args(sys.argv[2:])
            push_command(args)
            return

        elif subcommand == "restart":
            # Restart cloud server
            parser = argparse.ArgumentParser(
                prog="flacfetch-remote restart",
                description="Restart the cloud server to pick up new secrets",
            )
            parser.add_argument("--project", default="nomadkaraoke", help="GCP project ID")
            parser.add_argument("--zone", default="us-central1-a", help="GCE zone")
            parser.add_argument("--instance", default="flacfetch-service", help="Instance name")
            parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")
            args = parser.parse_args(sys.argv[2:])
            restart_command(args)
            return

        elif subcommand == "fix":
            # Interactive credential fix
            parser = argparse.ArgumentParser(
                prog="flacfetch-remote fix",
                description="Interactive credential fix (authenticate locally → upload to cloud → restart)",
            )
            parser.add_argument("--project", default="nomadkaraoke", help="GCP project ID")
            args = parser.parse_args(sys.argv[2:])
            fix_command(args)
            return

    # Check for httpx
    if httpx is None:
        print(f"{Colors.RED}Error: httpx not installed. Install with: pip install httpx{Colors.RESET}")
        sys.exit(1)

    # Custom formatter
    class WideHelpFormatter(argparse.RawDescriptionHelpFormatter):
        def __init__(self, prog, max_help_position=35, width=100):
            super().__init__(prog, max_help_position=max_help_position, width=width)

    parser = argparse.ArgumentParser(
        prog="flacfetch-remote",
        description="""
flacfetch-remote - Download via Remote Flacfetch API

Same interface as 'flacfetch' but uses a remote flacfetch HTTP API server
for downloading. This is useful for torrent downloads which require a
dedicated server with proper network connectivity.
        """.strip(),
        epilog="""
Examples:
  flacfetch-remote "Artist" "Title"
      Search and download interactively

  flacfetch-remote -a "Artist" -t "Title" --auto
      Auto-select best quality

  flacfetch-remote "Artist" "Title" --gcs-path uploads/job123/audio/
      Download and upload to GCS

Environment Variables (required):
  FLACFETCH_API_URL      URL of the flacfetch API server
  FLACFETCH_API_KEY      API key for authentication

Optional:
  FLACFETCH_TIMEOUT      API timeout in seconds (default: 120)
        """.strip(),
        formatter_class=WideHelpFormatter
    )

    # Positional arguments
    parser.add_argument(
        "query",
        nargs="*",
        help="Artist and title as two separate args: 'Artist' 'Title'"
    )

    # Search options
    search_group = parser.add_argument_group("Search Options")
    search_group.add_argument(
        "-a", "--artist",
        metavar="NAME",
        help="Artist name"
    )
    search_group.add_argument(
        "-t", "--title",
        dest="title",
        metavar="NAME",
        help="Track/song title (required)"
    )
    search_group.add_argument(
        "--auto",
        action="store_true",
        help="Auto-select best quality without prompting"
    )
    search_group.add_argument(
        "-e", "--exhaustive",
        action="store_true",
        help="Disable early termination and search more groups (slower but comprehensive)"
    )

    # Output options
    output_group = parser.add_argument_group("Output Options")
    output_group.add_argument(
        "-o", "--output",
        metavar="DIR",
        default=".",
        help="Output directory (default: current directory)"
    )
    output_group.add_argument(
        "--rename",
        action="store_true",
        dest="auto_rename",
        help="Auto-rename to 'ARTIST - TITLE.ext'"
    )
    output_group.add_argument(
        "--filename",
        metavar="NAME",
        help="Custom output filename (without extension)"
    )
    output_group.add_argument(
        "--gcs-path",
        metavar="PATH",
        help="Upload to GCS at this path (e.g., uploads/job123/audio/)"
    )
    output_group.add_argument(
        "--no-local",
        action="store_true",
        help="Don't download file locally (only with --gcs-path)"
    )

    # Connection options
    conn_group = parser.add_argument_group("Connection Options")
    conn_group.add_argument(
        "--api-url",
        metavar="URL",
        help="API URL (or use FLACFETCH_API_URL env var)"
    )
    conn_group.add_argument(
        "--api-key",
        metavar="KEY",
        help="API key (or use FLACFETCH_API_KEY env var)"
    )
    conn_group.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("FLACFETCH_TIMEOUT", "120")),
        metavar="SECS",
        help="API timeout in seconds (default: 120)"
    )

    # General options
    general_group = parser.add_argument_group("General Options")
    general_group.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output"
    )
    general_group.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output"
    )

    args = parser.parse_args()

    # Get API URL and key
    api_url = args.api_url or os.environ.get("FLACFETCH_API_URL")
    api_key = args.api_key or os.environ.get("FLACFETCH_API_KEY")

    if not api_url:
        print(f"\n{Colors.RED}✗ Error: FLACFETCH_API_URL not set{Colors.RESET}")
        print("\nSet the environment variable or use --api-url:")
        print("  export FLACFETCH_API_URL=http://your-server:8080")
        sys.exit(1)

    if not api_key:
        print(f"\n{Colors.RED}✗ Error: FLACFETCH_API_KEY not set{Colors.RESET}")
        print("\nSet the environment variable or use --api-key:")
        print("  export FLACFETCH_API_KEY=your-api-key")
        sys.exit(1)

    # Parse positional arguments
    artist = args.artist
    title = args.title

    if not (artist and title) and args.query:
        if len(args.query) == 2:
            if not artist:
                artist = args.query[0].strip()
            if not title:
                title = args.query[1].strip()
        elif len(args.query) == 1:
            if not title:
                title = args.query[0].strip()
        elif len(args.query) > 2:
            if not title:
                title = " ".join(args.query).strip()

    # Validate required arguments
    if not title:
        print(f"\n{Colors.RED}✗ Error: Track title is required{Colors.RESET}\n")
        print(f"{Colors.BOLD}Usage examples:{Colors.RESET}")
        print(f'  {Colors.CYAN}flacfetch-remote "Artist" "Title"{Colors.RESET}')
        print(f'  {Colors.CYAN}flacfetch-remote -a "Artist" -t "Title"{Colors.RESET}')
        sys.exit(1)

    if not artist:
        print(f"\n{Colors.RED}✗ Error: Artist name is required for remote downloads{Colors.RESET}")
        print("\nRemote mode requires both artist and title for torrent searches.")
        sys.exit(1)

    # Create client
    client = RemoteClient(api_url, api_key, timeout=args.timeout)

    # Check connection
    if args.verbose:
        print(f"\n{Colors.DIM}Connecting to {api_url}...{Colors.RESET}")

    try:
        health = client.health_check()
        if args.verbose:
            print(f"{Colors.DIM}Connected to flacfetch API v{health.get('version', '?')}{Colors.RESET}")
            providers = health.get("providers", {})
            active = [k for k, v in providers.items() if v]
            print(f"{Colors.DIM}Available providers: {', '.join(active)}{Colors.RESET}")
    except Exception as e:
        print(f"\n{Colors.RED}✗ Error: Cannot connect to flacfetch API{Colors.RESET}")
        print(f"{Colors.RED}  {api_url}: {e}{Colors.RESET}")
        sys.exit(1)

    # Search
    print(f"\n{Colors.BOLD}Searching:{Colors.RESET} {Colors.GREEN}{artist}{Colors.RESET} - {Colors.GREEN}{title}{Colors.RESET}")
    print(f"{Colors.BOLD}Server:{Colors.RESET}    {Colors.CYAN}{api_url}{Colors.RESET}")

    # Show server version and status info
    server_version = health.get("version", "?")
    ytdlp_version = health.get("ytdlp", {}).get("version", "?") if health.get("ytdlp") else "?"
    started_at = health.get("started_at")

    # Format started_at as relative time
    started_str = ""
    if started_at:
        try:
            from datetime import datetime, timezone
            # Parse ISO format datetime
            if isinstance(started_at, str):
                # Handle ISO format with timezone
                started_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            else:
                started_dt = started_at
            now = datetime.now(timezone.utc)
            delta = now - started_dt
            if delta.days > 0:
                started_str = f"{delta.days}d ago"
            elif delta.seconds >= 3600:
                started_str = f"{delta.seconds // 3600}h ago"
            elif delta.seconds >= 60:
                started_str = f"{delta.seconds // 60}m ago"
            else:
                started_str = "just now"
        except Exception:
            started_str = ""

    version_line = f"{Colors.BOLD}Version:{Colors.RESET}   {Colors.DIM}flacfetch {server_version}, yt-dlp {ytdlp_version}"
    if started_str:
        version_line += f" (started {started_str})"
    version_line += f"{Colors.RESET}"
    print(version_line)
    print()  # Empty line before results

    try:
        search_result = client.search(artist, title, exhaustive=args.exhaustive)
    except Exception as e:
        print(f"\n{Colors.RED}✗ Search failed: {e}{Colors.RESET}")
        sys.exit(1)

    results = search_result.get("results", [])
    search_id = search_result.get("search_id")

    # Display provider stats if available
    provider_stats = search_result.get("provider_stats", [])
    if provider_stats:
        stats_parts = []
        for stat in provider_stats:
            provider = stat.get("provider", "?")
            count = stat.get("results_count", 0)
            if count > 0:
                stats_parts.append(f"{Colors.GREEN}{provider}: {count}{Colors.RESET}")
            else:
                stats_parts.append(f"{Colors.DIM}{provider}: 0{Colors.RESET}")
        print(f"{Colors.BOLD}Results:{Colors.RESET}   {' | '.join(stats_parts)}\n")

    if not results:
        print(f"{Colors.YELLOW}No results found.{Colors.RESET}")
        sys.exit(0)

    # Convert results for display
    display_results = [convert_api_result_to_display(r) for r in results]

    # Display results and select
    use_colors = not args.no_color
    selected_idx = 0

    if args.auto:
        # Auto-select first result (API already returns sorted by quality)
        selected_idx = 0
        selected = display_results[0]

        # Check for excellent match (lossless, matching artist, good seeders)
        is_excellent = (
            selected.get("is_lossless", False) and
            selected.get("seeders", 0) >= 50 and
            selected.get("release_type") in ("Album", "Single", "EP")
        )

        if is_excellent:
            # Excellent match - brief output and proceed
            seeders_info = f", {selected.get('seeders')} seeders" if selected.get('seeders') else ""
            print(f"\n{Colors.GREEN}✓ Auto-selected:{Colors.RESET} {Colors.BOLD}{selected.get('artist', artist)} - {selected.get('title', title)}{Colors.RESET}")
            print(f"   {Colors.DIM}[{selected.get('release_type')}] {selected.get('quality_str', '')}{seeders_info}{Colors.RESET}")
        else:
            # Show top results so user can see what was chosen
            print(f"\n{Colors.BOLD}Top results:{Colors.RESET}")
            for idx, r in enumerate(display_results[:5], 1):
                line = format_release_line(idx, r, artist, use_colors=use_colors)
                print(line)
            if len(display_results) > 5:
                print(f"   {Colors.DIM}... and {len(display_results) - 5} more{Colors.RESET}")
            print(f"\n{Colors.BOLD}Auto-selected:{Colors.RESET} #{1} - {selected.get('title', title)} ({selected.get('quality_str', '')})")
    else:
        # Interactive mode - use categorized display for large result sets
        if len(display_results) > 10:
            from ..core.categorize import categorize_releases
            from ..core.models import Release, TrackQuery

            # Convert dicts back to Release objects for categorization
            release_objects = []
            for d in display_results:
                try:
                    release_objects.append(Release.from_dict(d))
                except Exception:
                    # If conversion fails, skip categorization
                    release_objects = []
                    break

            if release_objects:
                query = TrackQuery(artist=artist, title=title)
                categorized = categorize_releases(release_objects, query)
                cat_display = print_categorized_releases(categorized, target_artist=artist, use_colors=use_colors)

                while True:
                    prompt = f"{Colors.BOLD}Select (1-{len(cat_display)}), 'more' for full list, 0 to cancel: {Colors.RESET}"
                    choice = input(prompt)

                    if choice.lower() in ('more', 'm', 'all', 'a'):
                        # Show full flat list
                        print_releases(display_results, target_artist=artist, use_colors=use_colors)
                        cat_display = release_objects  # Switch to full list
                        continue

                    try:
                        idx = int(choice)
                        if idx == 0:
                            print(f"\n{Colors.YELLOW}Cancelled.{Colors.RESET}")
                            sys.exit(0)
                        if 1 <= idx <= len(cat_display):
                            # Get the selected release from categorized display
                            selected_release = cat_display[idx - 1]
                            # Use api_index for precise matching (avoids 24-bit vs 16-bit confusion)
                            if selected_release.api_index is not None:
                                selected_idx = selected_release.api_index
                                selected = display_results[selected_idx]
                            else:
                                # Fallback: match by provider + title + artist + bit_depth
                                found = False
                                sel_bit_depth = selected_release.quality.bit_depth if selected_release.quality else None
                                for r in display_results:
                                    r_bit_depth = r.get('quality_data', {}).get('bit_depth') if r.get('quality_data') else None
                                    if (r.get('provider') == selected_release.source_name and
                                        r.get('title') == selected_release.title and
                                        r.get('artist') == selected_release.artist and
                                        r_bit_depth == sel_bit_depth):
                                        selected_idx = r.get('index', 0)
                                        selected = r
                                        found = True
                                        break
                                if not found:
                                    print(f"{Colors.RED}Error: Could not find matching release{Colors.RESET}")
                                    continue
                            break
                        print(f"{Colors.RED}Invalid selection. Enter 1-{len(cat_display)}, 'more', or 0.{Colors.RESET}")
                    except ValueError:
                        print(f"{Colors.RED}Please enter a number or 'more'.{Colors.RESET}")
            else:
                # Fallback to flat list
                print_releases(display_results, target_artist=artist, use_colors=use_colors)
                while True:
                    choice = input(f"\n{Colors.BOLD}Select a release (1-{len(results)}, 0 to cancel): {Colors.RESET}")
                    try:
                        idx = int(choice)
                        if idx == 0:
                            print(f"\n{Colors.YELLOW}Cancelled.{Colors.RESET}")
                            sys.exit(0)
                        if 1 <= idx <= len(results):
                            selected_idx = idx - 1
                            selected = display_results[selected_idx]
                            break
                        print(f"{Colors.RED}Invalid selection.{Colors.RESET}")
                    except ValueError:
                        print(f"{Colors.RED}Please enter a number.{Colors.RESET}")
        else:
            # Simple flat list for small results
            print_releases(display_results, target_artist=artist, use_colors=use_colors)
            while True:
                choice = input(f"\n{Colors.BOLD}Select a release (1-{len(results)}, 0 to cancel): {Colors.RESET}")
                try:
                    idx = int(choice)
                    if idx == 0:
                        print(f"\n{Colors.YELLOW}Cancelled.{Colors.RESET}")
                        sys.exit(0)
                    if 1 <= idx <= len(results):
                        selected_idx = idx - 1
                        selected = display_results[selected_idx]
                        break
                    print(f"{Colors.RED}Invalid selection.{Colors.RESET}")
                except ValueError:
                    print(f"{Colors.RED}Please enter a number.{Colors.RESET}")

    # Determine output filename
    output_filename = None
    if args.filename:
        output_filename = args.filename
    elif args.auto_rename:
        output_filename = f"{artist} - {title}"

    # Start download
    print(f"\n{Colors.BOLD}Starting download...{Colors.RESET}")

    if args.verbose:
        print(f"{Colors.DIM}Selected: index={selected_idx}, provider={selected.get('provider')}, title={selected.get('title')}{Colors.RESET}")

    try:
        download_id = client.download(
            search_id=search_id,
            result_index=selected_idx,
            output_filename=output_filename,
            gcs_path=args.gcs_path,
        )
    except Exception as e:
        print(f"\n{Colors.RED}✗ Failed to start download: {e}{Colors.RESET}")
        sys.exit(1)

    if args.verbose:
        print(f"{Colors.DIM}Download ID: {download_id}{Colors.RESET}")

    # Wait for download with progress
    try:
        final_status = client.wait_for_download(
            download_id,
            timeout=600,  # 10 minute timeout
            poll_interval=2.0,
            progress_callback=print_progress,
        )
        print()  # New line after progress bar
    except RuntimeError as e:
        print()  # New line after progress bar
        print(f"\n{Colors.RED}✗ Download failed: {e}{Colors.RESET}")
        sys.exit(1)
    except KeyboardInterrupt:
        print()
        print(f"\n{Colors.YELLOW}Download interrupted.{Colors.RESET}")
        sys.exit(1)

    # Download file locally (unless --no-local and --gcs-path)
    local_file = None
    if not args.no_local or not args.gcs_path:
        # Determine local output path
        output_dir = args.output or "."
        os.makedirs(output_dir, exist_ok=True)

        print(f"\n{Colors.BOLD}Fetching file to local machine...{Colors.RESET}")

        def fetch_progress(downloaded: int, total: int):
            pct = (downloaded / total * 100) if total > 0 else 0
            bar_width = 30
            filled = int(bar_width * pct / 100)
            bar = "█" * filled + "░" * (bar_width - filled)
            mb_done = downloaded / (1024 * 1024)
            mb_total = total / (1024 * 1024)
            print(f"\r{Colors.BOLD}Fetching:{Colors.RESET} [{bar}] {pct:5.1f}% ({mb_done:.1f}/{mb_total:.1f} MB)   ", end="", flush=True)

        try:
            local_file = client.fetch_file(
                download_id=download_id,
                output_path=output_dir,
                progress_callback=fetch_progress,
            )
            print()  # New line after progress bar
        except Exception as e:
            print()
            print(f"\n{Colors.RED}✗ Failed to fetch file: {e}{Colors.RESET}")
            print(f"{Colors.DIM}File is available on server at: {final_status.get('output_path')}{Colors.RESET}")

    # Success summary
    print(f"\n{Colors.GREEN}{'='*60}{Colors.RESET}")
    print(f"{Colors.GREEN}✓ Download Complete!{Colors.RESET}\n")
    print(f"{Colors.BOLD}Track:{Colors.RESET}     {artist} - {title}")
    print(f"{Colors.BOLD}Source:{Colors.RESET}    {final_status.get('provider', 'Unknown')}")

    if local_file:
        # Show local file path
        try:
            rel_path = os.path.relpath(local_file)
            if len(rel_path) < len(local_file):
                file_display = rel_path
            else:
                file_display = local_file
        except ValueError:
            file_display = local_file
        print(f"{Colors.BOLD}Saved to:{Colors.RESET}  {Colors.CYAN}{file_display}{Colors.RESET}")

    if final_status.get("gcs_path"):
        print(f"{Colors.BOLD}GCS:{Colors.RESET}       {Colors.CYAN}{final_status['gcs_path']}{Colors.RESET}")

    if final_status.get("status") == "seeding":
        print(f"{Colors.BOLD}Server:{Colors.RESET}    {Colors.GREEN}Seeding{Colors.RESET} (torrent continues seeding on server)")

    print(f"{Colors.GREEN}{'='*60}{Colors.RESET}\n")


if __name__ == "__main__":
    main()

