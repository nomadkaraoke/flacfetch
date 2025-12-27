import argparse
import os
import re
import sys
from typing import Any, Callable, List, Optional, Union

from ..core.interfaces import InteractionHandler
from ..core.log import setup_logging
from ..core.manager import FetchManager
from ..core.models import Release, TrackQuery
from ..downloaders.youtube import YoutubeDownloader
from ..providers.ops import OPSProvider
from ..providers.red import REDProvider
from ..providers.youtube import YoutubeProvider

try:
    from ..downloaders.torrent import TorrentDownloader
except ImportError:
    TorrentDownloader = None  # type: ignore[assignment,misc]

try:
    from ..downloaders.spotify import SpotifyDownloader
    from ..providers.spotify import SpotifyProvider
    SPOTIFY_AVAILABLE = True
except ImportError:
    SpotifyProvider = None  # type: ignore[assignment,misc]
    SpotifyDownloader = None  # type: ignore[assignment,misc]
    SPOTIFY_AVAILABLE = False


def serve_command(args):
    """Run the flacfetch HTTP API server."""
    try:
        from ..api import run_server
    except ImportError as e:
        print("Error: API dependencies not installed. Install with: pip install flacfetch[api]")
        print(f"Details: {e}")
        sys.exit(1)

    setup_logging(args.verbose)

    run_server(
        host=args.host,
        port=args.port,
        log_level="debug" if args.verbose else "info",
    )


def cookies_command(args):
    """Handle cookies subcommand for YouTube authentication."""
    import tempfile

    action = args.action

    if action == "upload":
        cookies_content = None

        if args.file:
            # Read from provided file
            try:
                with open(args.file) as f:
                    cookies_content = f.read()
                print(f"{Colors.CYAN}Read cookies from: {args.file}{Colors.RESET}")
            except Exception as e:
                print(f"{Colors.RED}Error reading file: {e}{Colors.RESET}")
                sys.exit(1)
        else:
            # Extract from browser using yt-dlp
            browser = args.browser or "chrome"
            print(f"{Colors.CYAN}Extracting cookies from {browser}...{Colors.RESET}")
            print(f"{Colors.DIM}(You may need to authenticate in the browser if prompted){Colors.RESET}")

            try:
                # Use yt-dlp to extract cookies
                import yt_dlp

                # Create a temp file to store cookies
                with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                    temp_cookies = f.name

                try:
                    # yt-dlp will extract cookies from browser and save to file
                    ydl_opts = {
                        'quiet': True,
                        'cookiesfrombrowser': (browser, None, None, None),
                        'cookiefile': temp_cookies,
                    }

                    # We need to make at least one request to trigger cookie extraction
                    # Using a simple YouTube URL
                    try:
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            # Just extract info, don't download
                            ydl.extract_info("https://www.youtube.com/", download=False)
                    except Exception:
                        # It's okay if extraction fails, cookies may still be written
                        pass

                    # Read the extracted cookies
                    with open(temp_cookies) as f:
                        cookies_content = f.read()
                finally:
                    # Always clean up temp file
                    if os.path.exists(temp_cookies):
                        os.unlink(temp_cookies)

                if not cookies_content or "youtube" not in cookies_content.lower():
                    print(f"{Colors.YELLOW}Warning: No YouTube cookies found in {browser}.{Colors.RESET}")
                    print(f"{Colors.DIM}Make sure you are logged into YouTube in {browser}.{Colors.RESET}")
                    sys.exit(1)

                print(f"{Colors.GREEN}Successfully extracted cookies from {browser}{Colors.RESET}")

            except Exception as e:
                print(f"{Colors.RED}Error extracting cookies: {e}{Colors.RESET}")
                print(f"\n{Colors.BOLD}Tip:{Colors.RESET} You can also export cookies manually:")
                print("  1. Install a browser extension like 'Get cookies.txt LOCALLY'")
                print("  2. Export cookies from youtube.com")
                print("  3. Run: flacfetch cookies upload --file cookies.txt")
                sys.exit(1)

        # Now upload to remote server
        if not args.server:
            # Try to get from environment
            server = os.environ.get("FLACFETCH_REMOTE_URL")
            if not server:
                print(f"{Colors.YELLOW}No server specified.{Colors.RESET}")
                print("Use --server URL or set FLACFETCH_REMOTE_URL environment variable.")

                # If no server, just save locally
                local_path = os.path.expanduser("~/.flacfetch/youtube_cookies.txt")
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                with open(local_path, 'w') as f:
                    f.write(cookies_content)
                os.chmod(local_path, 0o600)
                print(f"{Colors.GREEN}Saved cookies locally to: {local_path}{Colors.RESET}")
                print(f"{Colors.DIM}Set YOUTUBE_COOKIES_FILE={local_path} to use them.{Colors.RESET}")
                return
        else:
            server = args.server

        api_key = args.api_key or os.environ.get("FLACFETCH_API_KEY")
        if not api_key:
            print(f"{Colors.RED}Error: API key required for upload.{Colors.RESET}")
            print("Use --api-key KEY or set FLACFETCH_API_KEY environment variable.")
            sys.exit(1)

        # Upload to server
        try:
            import httpx

            url = f"{server.rstrip('/')}/config/youtube-cookies"
            headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

            print(f"{Colors.CYAN}Uploading cookies to {server}...{Colors.RESET}")

            response = httpx.post(
                url,
                json={"cookies": cookies_content},
                headers=headers,
                timeout=30.0,
            )

            if response.status_code == 200:
                data = response.json()
                print(f"{Colors.GREEN}✓ {data.get('message', 'Cookies uploaded successfully')}{Colors.RESET}")
            else:
                print(f"{Colors.RED}Error: {response.status_code}{Colors.RESET}")
                try:
                    print(f"{Colors.RED}{response.json().get('detail', response.text)}{Colors.RESET}")
                except Exception:
                    print(f"{Colors.RED}{response.text}{Colors.RESET}")
                sys.exit(1)

        except ImportError:
            print(f"{Colors.RED}Error: httpx not installed. Install with: pip install flacfetch[remote]{Colors.RESET}")
            sys.exit(1)
        except Exception as e:
            print(f"{Colors.RED}Error uploading cookies: {e}{Colors.RESET}")
            sys.exit(1)

    elif action == "status":
        # Check cookie status
        server = args.server or os.environ.get("FLACFETCH_REMOTE_URL")
        api_key = args.api_key or os.environ.get("FLACFETCH_API_KEY")

        if server and api_key:
            # Check remote status
            try:
                import httpx

                url = f"{server.rstrip('/')}/config/youtube-cookies/status"
                headers = {"X-API-Key": api_key}

                response = httpx.get(url, headers=headers, timeout=10.0)

                if response.status_code == 200:
                    data = response.json()
                    if data.get("configured"):
                        print(f"{Colors.GREEN}✓ YouTube cookies configured{Colors.RESET}")
                        print(f"  Source: {data.get('source', 'unknown')}")
                        if data.get("file_path"):
                            print(f"  Path: {data.get('file_path')}")
                        if data.get("last_updated"):
                            print(f"  Updated: {data.get('last_updated')}")
                        if data.get("cookies_valid"):
                            print(f"  {Colors.GREEN}Valid: {data.get('validation_message')}{Colors.RESET}")
                        else:
                            print(f"  {Colors.YELLOW}Validation: {data.get('validation_message')}{Colors.RESET}")
                    else:
                        print(f"{Colors.YELLOW}✗ No YouTube cookies configured{Colors.RESET}")
                        print(f"  {data.get('validation_message', '')}")
                else:
                    print(f"{Colors.RED}Error: {response.status_code}{Colors.RESET}")

            except ImportError:
                print(f"{Colors.RED}Error: httpx not installed{Colors.RESET}")
            except Exception as e:
                print(f"{Colors.RED}Error checking status: {e}{Colors.RESET}")
        else:
            # Check local status
            local_path = os.environ.get("YOUTUBE_COOKIES_FILE") or os.path.expanduser("~/.flacfetch/youtube_cookies.txt")
            if os.path.exists(local_path):
                print(f"{Colors.GREEN}✓ Local cookies found: {local_path}{Colors.RESET}")
            else:
                print(f"{Colors.YELLOW}✗ No local cookies configured{Colors.RESET}")
                print(f"  Expected at: {local_path}")

    elif action == "delete":
        server = args.server or os.environ.get("FLACFETCH_REMOTE_URL")
        api_key = args.api_key or os.environ.get("FLACFETCH_API_KEY")

        if server and api_key:
            try:
                import httpx

                url = f"{server.rstrip('/')}/config/youtube-cookies"
                headers = {"X-API-Key": api_key}

                response = httpx.delete(url, headers=headers, timeout=10.0)

                if response.status_code == 200:
                    data = response.json()
                    print(f"{Colors.GREEN}✓ {data.get('message', 'Cookies deleted')}{Colors.RESET}")
                else:
                    print(f"{Colors.RED}Error: {response.status_code}{Colors.RESET}")

            except Exception as e:
                print(f"{Colors.RED}Error: {e}{Colors.RESET}")
        else:
            # Delete local
            local_path = os.environ.get("YOUTUBE_COOKIES_FILE") or os.path.expanduser("~/.flacfetch/youtube_cookies.txt")
            if os.path.exists(local_path):
                os.unlink(local_path)
                print(f"{Colors.GREEN}✓ Deleted local cookies: {local_path}{Colors.RESET}")
            else:
                print(f"{Colors.YELLOW}No local cookies to delete{Colors.RESET}")


def spotify_auth_command(args):
    """Handle Spotify OAuth authentication for headless server deployment.

    This command helps generate and manage Spotify OAuth tokens for use
    on headless servers (like GCE VMs) where browser-based authentication
    isn't possible.
    """
    import json

    action = args.action

    # Lazy import for Colors since it's defined later
    class C:
        RESET = "\033[0m"
        BOLD = "\033[1m"
        DIM = "\033[2m"
        CYAN = "\033[36m"
        GREEN = "\033[32m"
        YELLOW = "\033[33m"
        RED = "\033[31m"

    if action == "login":
        # Perform OAuth login and show the token
        if not SPOTIFY_AVAILABLE:
            print(f"{C.RED}Error: Spotify dependencies not installed.{C.RESET}")
            print("Install with: pip install flacfetch[spotify]")
            sys.exit(1)

        client_id = os.environ.get("SPOTIPY_CLIENT_ID")
        client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET")

        if not client_id or not client_secret:
            print(f"{C.RED}Error: Spotify credentials not configured.{C.RESET}")
            print("\nSet these environment variables:")
            print("  export SPOTIPY_CLIENT_ID=your_client_id")
            print("  export SPOTIPY_CLIENT_SECRET=your_client_secret")
            print("\nGet credentials from: https://developer.spotify.com/dashboard")
            sys.exit(1)

        print(f"{C.CYAN}Starting Spotify OAuth flow...{C.RESET}")
        print(f"{C.DIM}A browser window will open for authentication.{C.RESET}")

        try:
            import spotipy
            from spotipy.oauth2 import SpotifyOAuth

            # Use explicit cache path
            cache_path = os.path.expanduser("~/.cache-spotipy")

            auth_manager = SpotifyOAuth(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=os.environ.get("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
                scope="user-read-playback-state user-modify-playback-state streaming",
                cache_path=cache_path,
            )

            # This triggers the OAuth flow
            sp = spotipy.Spotify(auth_manager=auth_manager)
            user = sp.current_user()

            print(f"\n{C.GREEN}✓ Successfully authenticated as: {user.get('display_name', user.get('id'))}{C.RESET}")

            # Read the cached token
            if os.path.exists(cache_path):
                with open(cache_path) as f:
                    token_data = f.read()

                print(f"\n{C.BOLD}Token saved to: {cache_path}{C.RESET}")
                print(f"\n{C.CYAN}To deploy to cloud server:{C.RESET}")
                print(f"  gcloud secrets versions add spotify-oauth-token --data-file={cache_path}")
                print("\nThen restart the server or run:")
                print("  gcloud compute instances reset flacfetch-service --zone=us-central1-a")
            else:
                print(f"{C.RED}Warning: Token file not found at expected location{C.RESET}")

        except Exception as e:
            print(f"{C.RED}Error during authentication: {e}{C.RESET}")
            sys.exit(1)

    elif action == "show":
        # Show the current cached token
        cache_path = os.path.expanduser("~/.cache-spotipy")
        if os.path.exists(cache_path):
            with open(cache_path) as f:
                token_data = json.load(f)

            print(f"{C.GREEN}✓ Cached token found at: {cache_path}{C.RESET}")
            print(f"\n{C.BOLD}Token info:{C.RESET}")
            if "expires_at" in token_data:
                import datetime
                expires = datetime.datetime.fromtimestamp(token_data["expires_at"])
                print(f"  Expires: {expires}")
            if "refresh_token" in token_data:
                print("  Has refresh token: Yes")
            if "scope" in token_data:
                print(f"  Scopes: {token_data['scope']}")
        else:
            print(f"{C.YELLOW}No cached token found.{C.RESET}")
            print("Run 'flacfetch spotify-auth login' to authenticate.")

    elif action == "upload":
        # Upload the token to GCP Secret Manager
        cache_path = os.path.expanduser("~/.cache-spotipy")

        if not os.path.exists(cache_path):
            print(f"{C.RED}Error: No cached token found.{C.RESET}")
            print("Run 'flacfetch spotify-auth login' first.")
            sys.exit(1)

        print(f"{C.CYAN}Uploading token to GCP Secret Manager...{C.RESET}")

        import subprocess
        result = subprocess.run(
            ["gcloud", "secrets", "versions", "add", "spotify-oauth-token",
             f"--data-file={cache_path}", f"--project={args.project or 'nomadkaraoke'}"],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            print(f"{C.GREEN}✓ Token uploaded successfully!{C.RESET}")
            print(f"\n{C.CYAN}To apply the change, restart the server:{C.RESET}")
            print(f"  gcloud compute instances reset flacfetch-service --zone=us-central1-a --project={args.project or 'nomadkaraoke'}")
        else:
            print(f"{C.RED}Error uploading token:{C.RESET}")
            print(result.stderr)
            sys.exit(1)

    elif action == "test":
        # Test the current token by making an API call
        if not SPOTIFY_AVAILABLE:
            print(f"{C.RED}Error: Spotify dependencies not installed.{C.RESET}")
            sys.exit(1)

        cache_path = os.path.expanduser("~/.cache-spotipy")

        if not os.path.exists(cache_path):
            print(f"{C.RED}No cached token found.{C.RESET}")
            print("Run 'flacfetch spotify-auth login' first.")
            sys.exit(1)

        try:
            import spotipy
            from spotipy.oauth2 import SpotifyOAuth

            auth_manager = SpotifyOAuth(
                client_id=os.environ.get("SPOTIPY_CLIENT_ID"),
                client_secret=os.environ.get("SPOTIPY_CLIENT_SECRET"),
                redirect_uri=os.environ.get("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
                scope="user-read-playback-state user-modify-playback-state streaming",
                cache_path=cache_path,
            )

            sp = spotipy.Spotify(auth_manager=auth_manager)
            user = sp.current_user()

            print(f"{C.GREEN}✓ Token is valid!{C.RESET}")
            print(f"  Authenticated as: {user.get('display_name', user.get('id'))}")

            # Try a search to confirm full functionality
            results = sp.search(q="test", type="track", limit=1)
            if results.get("tracks", {}).get("items"):
                print("  Search API: Working")

        except Exception as e:
            print(f"{C.RED}Token validation failed: {e}{C.RESET}")
            print("\nThe token may be expired or revoked.")
            print("Run 'flacfetch spotify-auth login' to re-authenticate.")
            sys.exit(1)


def fix_command(args):
    """
    Interactive command to fix LOCAL credential issues.

    Sets up Spotify and YouTube credentials on your local machine.
    To deploy to cloud server, use 'flacfetch-remote push' afterward.
    """
    import subprocess

    from flacfetch.api.services.credential_check import (
        get_local_spotify_cache_path,
        get_local_youtube_cookies_path,
    )

    # Colors for output
    class C:
        RESET = "\033[0m"
        BOLD = "\033[1m"
        DIM = "\033[2m"
        CYAN = "\033[36m"
        GREEN = "\033[32m"
        YELLOW = "\033[33m"
        RED = "\033[31m"

    target = args.target  # 'all', 'spotify', or 'youtube'

    print(f"\n{C.BOLD}🔧 Flacfetch LOCAL Credential Fix Tool{C.RESET}")
    print(f"{C.DIM}This tool sets up credentials on your local machine.{C.RESET}\n")

    if target in ("all", "spotify"):
        print(f"{C.CYAN}━━━ Spotify ━━━{C.RESET}")

        # Check if Spotify credentials are configured locally
        client_id = os.environ.get("SPOTIPY_CLIENT_ID")
        client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET")

        if not client_id or not client_secret:
            print(f"{C.YELLOW}⚠️  Spotify credentials not found in environment.{C.RESET}")
            print("\nPlease set these environment variables:")
            print("  export SPOTIPY_CLIENT_ID=your_client_id")
            print("  export SPOTIPY_CLIENT_SECRET=your_client_secret")
            print("\nGet credentials from: https://developer.spotify.com/dashboard")
        else:
            print(f"{C.GREEN}✓ Spotify credentials found in environment{C.RESET}")

            # Ask to proceed
            proceed = input(f"\n{C.BOLD}Re-authenticate Spotify? [Y/n]: {C.RESET}").strip().lower()
            if proceed in ("", "y", "yes"):
                print(f"\n{C.CYAN}Opening browser for Spotify login...{C.RESET}")
                print(f"{C.DIM}Complete the login in your browser.{C.RESET}\n")

                try:
                    import spotipy
                    from spotipy.oauth2 import SpotifyOAuth

                    cache_path = get_local_spotify_cache_path()

                    # Remove old cache to force re-auth
                    if os.path.exists(cache_path):
                        os.unlink(cache_path)

                    auth_manager = SpotifyOAuth(
                        client_id=client_id,
                        client_secret=client_secret,
                        redirect_uri=os.environ.get("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
                        scope="user-read-playback-state user-modify-playback-state streaming",
                        cache_path=cache_path,
                    )

                    sp = spotipy.Spotify(auth_manager=auth_manager)
                    user = sp.current_user()

                    print(f"\n{C.GREEN}✓ Authenticated as: {user.get('display_name', user.get('id'))}{C.RESET}")
                    print(f"{C.DIM}Token saved to: {cache_path}{C.RESET}")

                except Exception as e:
                    print(f"{C.RED}Error: {e}{C.RESET}")
            else:
                print(f"{C.DIM}Skipping Spotify...{C.RESET}")

    if target in ("all", "youtube"):
        print(f"\n{C.CYAN}━━━ YouTube ━━━{C.RESET}")

        proceed = input(f"\n{C.BOLD}Update YouTube cookies? [Y/n]: {C.RESET}").strip().lower()
        if proceed in ("", "y", "yes"):
            print(f"\n{C.DIM}Options:{C.RESET}")
            print("  1. Extract from browser (requires browser to be closed)")
            print("  2. Provide existing cookies file")
            print("  3. Use browser extension export (recommended)")

            choice = input(f"\n{C.BOLD}Choose method [1/2/3] (default: 1): {C.RESET}").strip()
            if not choice:
                choice = "1"

            cookies_content = None
            temp_cookies = None

            if choice == "1":
                # Extract from browser
                browser = input(f"{C.BOLD}Browser [chrome/firefox/edge/safari/brave] (default: chrome): {C.RESET}").strip().lower()
                if not browser:
                    browser = "chrome"

                print(f"\n{C.YELLOW}⚠️  Make sure {browser} is completely closed!{C.RESET}")
                input(f"{C.DIM}Press Enter when ready...{C.RESET}")

                print(f"\n{C.CYAN}Extracting cookies from {browser}...{C.RESET}")

                try:
                    import tempfile

                    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                        temp_cookies = f.name

                    # Use yt-dlp CLI directly - more reliable than Python API
                    subprocess.run(
                        ["yt-dlp", "--cookies-from-browser", browser,
                         "--cookies", temp_cookies,
                         "--skip-download", "--flat-playlist",
                         "https://www.youtube.com/feed/subscriptions"],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )

                    if os.path.exists(temp_cookies):
                        with open(temp_cookies) as f:
                            cookies_content = f.read()

                    if not cookies_content or "youtube" not in cookies_content.lower():
                        print(f"{C.YELLOW}Cookie extraction failed.{C.RESET}")
                        print(f"{C.DIM}Try option 3 (browser extension) instead.{C.RESET}")
                        cookies_content = None

                except subprocess.TimeoutExpired:
                    print(f"{C.RED}Timeout - browser might not be closed.{C.RESET}")
                except Exception as e:
                    print(f"{C.RED}Error: {e}{C.RESET}")

            elif choice == "2":
                # Provide existing file
                file_path = input(f"{C.BOLD}Path to cookies.txt file: {C.RESET}").strip()
                file_path = os.path.expanduser(file_path)

                if os.path.exists(file_path):
                    with open(file_path) as f:
                        cookies_content = f.read()

                    if "youtube" not in cookies_content.lower():
                        print(f"{C.YELLOW}Warning: File doesn't appear to contain YouTube cookies.{C.RESET}")
                        confirm = input(f"{C.BOLD}Use anyway? [y/N]: {C.RESET}").strip().lower()
                        if confirm not in ("y", "yes"):
                            cookies_content = None
                else:
                    print(f"{C.RED}File not found: {file_path}{C.RESET}")

            elif choice == "3":
                # Browser extension instructions
                print(f"\n{C.CYAN}Browser Extension Method:{C.RESET}")
                print("\n1. Install a cookie export extension:")
                print(f"   {C.DIM}• Chrome: 'Get cookies.txt LOCALLY' or 'EditThisCookie'{C.RESET}")
                print(f"   {C.DIM}• Firefox: 'cookies.txt' by Lennon Hill{C.RESET}")
                print("\n2. Go to youtube.com and make sure you're logged in")
                print("\n3. Click the extension and export cookies in Netscape format")
                print("\n4. Save the file (e.g., ~/Downloads/youtube_cookies.txt)")

                file_path = input(f"\n{C.BOLD}Path to exported cookies file: {C.RESET}").strip()
                file_path = os.path.expanduser(file_path)

                if file_path and os.path.exists(file_path):
                    with open(file_path) as f:
                        cookies_content = f.read()

                    if "youtube" not in cookies_content.lower():
                        print(f"{C.YELLOW}Warning: File doesn't appear to contain YouTube cookies.{C.RESET}")
                elif file_path:
                    print(f"{C.RED}File not found: {file_path}{C.RESET}")

            # Save cookies locally if we got them
            if cookies_content and "youtube" in cookies_content.lower():
                local_cookies_path = get_local_youtube_cookies_path()

                # Ensure directory exists
                os.makedirs(os.path.dirname(local_cookies_path), exist_ok=True)

                with open(local_cookies_path, 'w') as f:
                    f.write(cookies_content)

                print(f"\n{C.GREEN}✓ YouTube cookies saved ({len(cookies_content)} bytes){C.RESET}")
                print(f"{C.DIM}Saved to: {local_cookies_path}{C.RESET}")

            # Cleanup temp file
            if temp_cookies and os.path.exists(temp_cookies):
                os.unlink(temp_cookies)

        else:
            print(f"{C.DIM}Skipping YouTube...{C.RESET}")

    # Show next steps
    print(f"\n{C.CYAN}━━━ Next Steps ━━━{C.RESET}")
    print(f"\n{C.BOLD}Local credentials are now set up.{C.RESET}")
    print("\nTo deploy to the cloud server:")
    print(f"  {C.CYAN}flacfetch-remote push{C.RESET}     - Upload credentials to GCP Secret Manager")
    print(f"  {C.CYAN}flacfetch-remote restart{C.RESET}  - Restart server to apply changes")
    print("\nOr use the all-in-one command:")
    print(f"  {C.CYAN}flacfetch-remote fix{C.RESET}      - Authenticate, upload, and restart")

    print(f"\n{C.GREEN}Done!{C.RESET}\n")


def check_command(args):
    """
    Check LOCAL credential status for Spotify and YouTube.

    Checks credentials on your local machine (not the remote server).
    To check the remote server, use 'flacfetch-remote check'.
    """
    # Colors for output
    class C:
        RESET = "\033[0m"
        BOLD = "\033[1m"
        DIM = "\033[2m"
        CYAN = "\033[36m"
        GREEN = "\033[32m"
        YELLOW = "\033[33m"
        RED = "\033[31m"

    print(f"\n{C.BOLD}🔍 Flacfetch LOCAL Credential Check{C.RESET}\n")

    # Check local credentials using local-specific functions
    try:
        from flacfetch.api.services.credential_check import (
            check_local_spotify_credentials,
            check_local_youtube_credentials,
        )

        checks = [
            ("Spotify", check_local_spotify_credentials),
            ("YouTube", check_local_youtube_credentials),
        ]

        all_ok = True
        for name, check_fn in checks:
            print(f"{C.CYAN}Checking {name}...{C.RESET}")
            result = check_fn()

            if result.status.value == 'ok':
                icon = f"{C.GREEN}✓{C.RESET}"
                color = C.GREEN
            elif result.status.value == 'missing':
                icon = f"{C.YELLOW}○{C.RESET}"
                color = C.YELLOW
                if result.needs_human_action:
                    all_ok = False
            else:
                icon = f"{C.RED}✗{C.RESET}"
                color = C.RED
                all_ok = False

            print(f"{icon} {C.BOLD}{result.service}{C.RESET}: {color}{result.status.value}{C.RESET}")
            print(f"  {C.DIM}{result.message}{C.RESET}")

            if result.needs_human_action and result.fix_command:
                print(f"  {C.CYAN}Fix: {result.fix_command}{C.RESET}")
            print()

        if all_ok:
            print(f"{C.GREEN}✓ All local credentials OK!{C.RESET}")
            print(f"{C.DIM}To deploy to cloud: flacfetch-remote push{C.RESET}\n")
        else:
            print(f"{C.YELLOW}⚠️  Some local credentials need attention.{C.RESET}")
            print(f"{C.DIM}Run 'flacfetch fix' to repair them.{C.RESET}\n")

    except ImportError as e:
        print(f"{C.RED}Error: Could not import credential check module: {e}{C.RESET}")
        print(f"{C.DIM}Make sure flacfetch is installed with API dependencies.{C.RESET}")
    except Exception as e:
        print(f"{C.RED}Error: {e}{C.RESET}")


# ANSI Color Codes
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    MAGENTA = "\033[35m"
    BLUE = "\033[34m"
    RED = "\033[31m"
    BRIGHT_MAGENTA = "\033[95m" # Lighter/Brighter Pink/Purple
    ORANGE = "\033[38;5;208m" # Roughly orange


def _get_release_field(release: Union[Release, dict], field: str, default: Any = None) -> Any:
    """Get a field from a Release object or dict."""
    if isinstance(release, dict):
        return release.get(field, default)
    return getattr(release, field, default)


def format_release_line(
    idx: int,
    release: Union[Release, dict],
    target_artist: Optional[str] = None,
    use_colors: bool = True,
) -> str:
    """
    Format a single release for display.

    This is the shared formatting function that can work with either:
    - Release objects (local CLI)
    - Dicts from Release.to_dict() (remote CLI via API)

    Args:
        idx: 1-based display index
        release: Release object or dict from Release.to_dict()
        target_artist: Artist name for highlighting matches
        use_colors: Whether to use ANSI color codes

    Returns:
        Formatted string for display (without trailing newline)
    """
    # Color setup
    if use_colors:
        C = Colors
    else:
        # No-op colors
        class NoColors:
            RESET = BOLD = DIM = CYAN = GREEN = YELLOW = MAGENTA = BLUE = RED = ""
            BRIGHT_MAGENTA = ORANGE = ""
        C = NoColors

    # Extract fields (works with both Release and dict)
    source_name = _get_release_field(release, "source_name", "Unknown")
    title = _get_release_field(release, "title", "Unknown")
    artist = _get_release_field(release, "artist", "Unknown")
    year = _get_release_field(release, "year")
    label = _get_release_field(release, "label")
    edition_info = _get_release_field(release, "edition_info")
    release_type = _get_release_field(release, "release_type")
    seeders = _get_release_field(release, "seeders")
    channel = _get_release_field(release, "channel")
    view_count = _get_release_field(release, "view_count")
    target_file = _get_release_field(release, "target_file")
    track_pattern = _get_release_field(release, "track_pattern")
    download_url = _get_release_field(release, "download_url")
    source_id = _get_release_field(release, "source_id")

    # Get quality info - handle both Release and dict
    if isinstance(release, Release):
        is_lossless = release.quality.is_lossless()
        is_true_lossless = release.quality.is_true_lossless(source_name)
        quality_str = str(release.quality)
        media_name = release.quality.media.name
        formatted_size = release.formatted_size
        formatted_duration = release.formatted_duration
        formatted_views = release.formatted_views
    else:
        # Dict from to_dict()
        is_lossless = release.get("is_lossless", False)
        # Spotify is not true lossless (transcoded from 320kbps)
        is_true_lossless = is_lossless and source_name.lower() != "spotify"
        quality_str = release.get("quality_str", "")
        quality_data = release.get("quality", {})
        media_name = quality_data.get("media", "OTHER") if isinstance(quality_data, dict) else "OTHER"
        formatted_size = release.get("formatted_size", "?")
        formatted_duration = release.get("formatted_duration")
        formatted_views = release.get("formatted_views")

    # 1. Format indicator (true lossless vs spotify vs lossy)
    if is_true_lossless:
        format_indicator = f"{C.GREEN}[LOSSLESS]{C.RESET}"
    elif source_name.lower() == "spotify":
        # Spotify is 320kbps Vorbis transcoded to FLAC - better than YouTube but not true lossless
        format_indicator = f"{C.YELLOW}[320kbps]{C.RESET}"
    else:
        format_indicator = f"{C.DIM}[lossy]{C.RESET}"

    # 2. Source Name
    source_tag = f"[{C.CYAN}{source_name}{C.RESET}]"

    # Title/Artist Display
    if source_name == "YouTube":
        subject = channel
        color = C.ORANGE
        if subject and target_artist and subject.lower() == target_artist.lower():
            color = C.GREEN
        subject_str = f"{color}{subject}{C.RESET}" if subject else "Unknown"
        title_str = f"{C.BOLD}{title}{C.RESET}"
        main_info = f"{subject_str}: {title_str}"
    elif source_name == "Spotify":
        # Spotify: Artist is always "official", show track name prominently
        subject = artist
        color = C.GREEN  # Spotify is always "official" source
        subject_str = f"{color}{subject}{C.RESET}" if subject else "Unknown"
        # Track name is in target_file, album name is in title
        track_name = target_file or title
        title_str = f"{C.BOLD}{track_name}{C.RESET}"
        main_info = f"{subject_str} - {title_str}"
        # Show album name if different from track
        if title and title != track_name:
            main_info += f" ({C.DIM}{title}{C.RESET})"
    else:
        subject = artist
        color = C.ORANGE
        if subject and target_artist and subject.lower() == target_artist.lower():
            color = C.GREEN
        subject_str = f"{color}{subject}{C.RESET}" if subject else "Unknown"
        title_str = f"{C.BOLD}{title}{C.RESET}"
        main_info = f"{subject_str} - {title_str}"

    header = f"{idx}. {format_indicator} {source_tag} {main_info}"

    # Metadata
    meta_parts = []
    if source_name == "YouTube":
        if formatted_duration:
            meta_parts.append(formatted_duration)
        if year:
            if year >= 2020:
                c = C.GREEN
            elif year >= 2015:
                c = C.YELLOW
            else:
                c = C.RED
            meta_parts.append(f"{c}{year}{C.RESET}")
        if download_url:
            short_url = download_url
            if "youtube.com/watch?v=" in short_url:
                try:
                    vid_id = short_url.split("v=")[1].split("&")[0]
                    short_url = f"youtu.be/{vid_id}"
                except IndexError:
                    pass
            elif "googlevideo.com" in short_url:
                short_url = "Stream"
            meta_parts.append(short_url)
    elif source_name == "Spotify":
        # Spotify: Show duration, year, and release type
        if formatted_duration:
            meta_parts.append(formatted_duration)
        if release_type:
            meta_parts.append(f"{C.MAGENTA}{release_type}{C.RESET}")
        if year:
            meta_parts.append(f"{C.YELLOW}{year}{C.RESET}")
    else:
        if release_type:
            meta_parts.append(f"{C.MAGENTA}{release_type}{C.RESET}")
        if year:
            meta_parts.append(f"{C.YELLOW}{year}{C.RESET}")
        if label:
            meta_parts.append(label)
        if edition_info:
            meta_parts.append(edition_info)
        meta_parts.append(media_name)

    meta_str = f" [{' / '.join(meta_parts)}]" if meta_parts else ""

    # Quality
    qual_str = ""
    if source_name == "Spotify":
        # Spotify: Show quality simply (VORBIS 320kbps)
        qual_str = f" ({C.GREEN}{quality_str}{C.RESET})"
    elif source_name != "YouTube":
        qual_text = quality_str
        if qual_text.endswith(media_name):
            qual_text = qual_text[:-len(media_name)].strip()
        if "24bit" in qual_text:
            qual_str = f" ({C.YELLOW}{qual_text}{C.RESET})"
        else:
            qual_str = f" ({C.GREEN}{qual_text}{C.RESET})"

    # Stats (Size, Seeders/Views/Popularity)
    stats_parts = []
    if formatted_size and formatted_size != "?":
        stats_parts.append(formatted_size)

    if seeders is not None:
        if seeders > 50:
            s_color = C.GREEN
        elif seeders >= 10:
            s_color = C.YELLOW
        else:
            s_color = C.RED
        stats_parts.append(f"Seeders: {s_color}{seeders}{C.RESET}")

    if view_count is not None and source_name == "YouTube":
        # Only show views for YouTube (Spotify uses view_count for popularity internally)
        if view_count > 1_000_000:
            v_color = C.GREEN
        elif view_count >= 10_000:
            v_color = C.YELLOW
        else:
            v_color = C.RED
        stats_parts.append(f"Views: {v_color}{formatted_views}{C.RESET}")
    elif view_count is not None and source_name == "Spotify":
        # Show popularity for Spotify (stored scaled in view_count)
        popularity = view_count // 10000  # Unscale
        if popularity >= 70:
            p_color = C.GREEN
        elif popularity >= 40:
            p_color = C.YELLOW
        else:
            p_color = C.DIM
        stats_parts.append(f"Pop: {p_color}{popularity}{C.RESET}")

    stats_str = f" - {', '.join(stats_parts)}" if stats_parts else ""

    # Target File with highlighting
    file_str = ""
    if target_file:
        fname = target_file
        fname = re.sub(r'^\d+[\.\-\s]+', '', fname)
        if track_pattern and use_colors:
            pattern = re.escape(track_pattern)
            fname = re.sub(f"({pattern})", f"{C.YELLOW}\\1{C.RESET}", fname, flags=re.IGNORECASE)
        file_str = f', "{fname}"'

    # Source ID inline at the end (compact display)
    source_str = ""
    if source_id:
        source_lower = source_name.lower()
        if source_lower == "youtube":
            source_str = f" {C.DIM}[{source_id}]{C.RESET}"
        elif source_lower == "spotify":
            source_str = f" {C.DIM}[{source_id}]{C.RESET}"
        elif source_lower in ("red", "ops"):
            source_str = f" {C.DIM}[t{source_id}]{C.RESET}"

    return f"{header}{meta_str}{qual_str}{stats_str}{file_str}{source_str}"


def print_releases(
    releases: List[Union[Release, dict]],
    target_artist: Optional[str] = None,
    use_colors: bool = True,
    output_func: Callable[[str], None] = print,
) -> None:
    """
    Print formatted release list for user selection.

    This is the shared display function usable by both local and remote CLIs.

    Args:
        releases: List of Release objects or dicts from Release.to_dict()
        target_artist: Artist name for highlighting matches
        use_colors: Whether to use ANSI color codes
        output_func: Function to use for output (default: print)
    """
    output_func(f"\nFound {len(releases)} releases:\n")
    for idx, release in enumerate(releases, 1):
        line = format_release_line(idx, release, target_artist, use_colors)
        output_func(line)


def print_categorized_releases(
    categorized,  # CategorizedResults from categorize.py
    target_artist: Optional[str] = None,
    use_colors: bool = True,
    output_func: Callable[[str], None] = print,
) -> List[Release]:
    """
    Print releases organized by category for cleaner display.

    Args:
        categorized: CategorizedResults from categorize_releases()
        target_artist: Artist name for highlighting matches
        use_colors: Whether to use ANSI color codes
        output_func: Function to use for output (default: print)

    Returns:
        Flat list of displayed releases (for index-based selection)
    """
    C = Colors if use_colors else type('NoColors', (), {
        attr: '' for attr in dir(Colors) if not attr.startswith('_')
    })()

    total_categories = len(categorized.categories)
    output_func(f"\n{C.BOLD}Found {categorized.total_count} results across {total_categories} categories:{C.RESET}\n")

    display_releases = []
    global_idx = 1

    for category in categorized.categories:
        # Category header
        cat_count = len(category.releases)
        output_func(f"{C.CYAN}{C.BOLD}{category.name}{C.RESET} ({cat_count}):")

        # Show releases up to max_display
        for release in category.releases[:category.max_display]:
            line = format_release_line(global_idx, release, target_artist, use_colors)
            output_func(line)
            display_releases.append(release)
            global_idx += 1

        # Show "and N more" if there are more
        hidden = cat_count - category.max_display
        if hidden > 0:
            output_func(f"   {C.DIM}... and {hidden} more{C.RESET}")

        output_func("")  # Blank line between categories

    return display_releases


class CLIHandler(InteractionHandler):
    def __init__(self, target_artist: Optional[str] = None, use_categories: bool = True):
        self.target_artist = target_artist
        self.use_categories = use_categories

    def select_release(self, releases: list[Release]) -> Optional[Release]:
        from ..core.categorize import categorize_releases
        from ..core.models import TrackQuery

        # Create a query for categorization
        query = TrackQuery(artist=self.target_artist or "", title="") if self.target_artist else None

        # Categorize and display
        if self.use_categories and len(releases) > 10:
            categorized = categorize_releases(releases, query)
            display_releases = print_categorized_releases(
                categorized, self.target_artist, use_colors=True
            )

            while True:
                prompt = f"{Colors.BOLD}Select (1-{len(display_releases)}), 'more' for full list, 0 to cancel: {Colors.RESET}"
                choice = input(prompt)

                if choice.lower() in ('more', 'm', 'all', 'a'):
                    # Show full flat list
                    print_releases(releases, self.target_artist, use_colors=True)
                    display_releases = releases
                    continue

                try:
                    idx = int(choice)
                    if idx == 0:
                        return None
                    if 1 <= idx <= len(display_releases):
                        return display_releases[idx - 1]
                    print(f"{Colors.RED}Invalid selection. Enter 1-{len(display_releases)}, 'more', or 0.{Colors.RESET}")
                except ValueError:
                    print(f"{Colors.RED}Please enter a number or 'more'.{Colors.RESET}")
        else:
            # Use simple flat list for small result sets
            print_releases(releases, self.target_artist, use_colors=True)

            while True:
                choice = input(f"\n{Colors.BOLD}Select a release (1-{len(releases)}, 0 to cancel): {Colors.RESET}")
                try:
                    idx = int(choice)
                    if idx == 0:
                        return None
                    if 1 <= idx <= len(releases):
                        return releases[idx - 1]
                    print(f"{Colors.RED}Invalid selection.{Colors.RESET}")
                except ValueError:
                    print(f"{Colors.RED}Please enter a number.{Colors.RESET}")

def main():
    # Custom formatter with wider width to prevent awkward wrapping
    class WideHelpFormatter(argparse.RawDescriptionHelpFormatter):
        def __init__(self, prog, max_help_position=35, width=100):
            super().__init__(prog, max_help_position=max_help_position, width=width)

    # Check for 'serve' subcommand first (special handling)
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        serve_parser = argparse.ArgumentParser(
            prog="flacfetch serve",
            description="Run the flacfetch HTTP API server",
            formatter_class=WideHelpFormatter,
        )
        serve_parser.add_argument(
            "--host",
            default=os.environ.get("FLACFETCH_API_HOST", "0.0.0.0"),
            help="Host to bind to (default: 0.0.0.0)"
        )
        serve_parser.add_argument(
            "--port", "-p",
            type=int,
            default=int(os.environ.get("FLACFETCH_API_PORT", "8080")),
            help="Port to listen on (default: 8080)"
        )
        serve_parser.add_argument(
            "-v", "--verbose",
            action="store_true",
            help="Enable verbose/debug logging"
        )
        serve_args = serve_parser.parse_args(sys.argv[2:])
        serve_command(serve_args)
        return

    # Check for 'cookies' subcommand
    if len(sys.argv) > 1 and sys.argv[1] == "cookies":
        cookies_parser = argparse.ArgumentParser(
            prog="flacfetch cookies",
            description="Manage YouTube cookies for authenticated downloads",
            formatter_class=WideHelpFormatter,
            epilog="""
Examples:
  flacfetch cookies upload
      Extract cookies from Chrome and upload to remote server

  flacfetch cookies upload --browser firefox
      Extract from Firefox instead

  flacfetch cookies upload --file cookies.txt
      Upload existing cookies file

  flacfetch cookies status
      Check if cookies are configured

  flacfetch cookies delete
      Remove stored cookies
            """.strip(),
        )
        cookies_parser.add_argument(
            "action",
            choices=["upload", "status", "delete"],
            help="Action to perform: upload, status, or delete"
        )
        cookies_parser.add_argument(
            "--browser", "-b",
            choices=["chrome", "firefox", "edge", "safari", "opera", "brave"],
            help="Browser to extract cookies from (default: chrome)"
        )
        cookies_parser.add_argument(
            "--file", "-f",
            help="Path to existing cookies file (Netscape format)"
        )
        cookies_parser.add_argument(
            "--server", "-s",
            help="Remote flacfetch server URL (or use FLACFETCH_REMOTE_URL env var)"
        )
        cookies_parser.add_argument(
            "--api-key", "-k",
            help="API key for remote server (or use FLACFETCH_API_KEY env var)"
        )
        cookies_args = cookies_parser.parse_args(sys.argv[2:])
        cookies_command(cookies_args)
        return

    # Check for 'spotify-auth' subcommand
    if len(sys.argv) > 1 and sys.argv[1] == "spotify-auth":
        spotify_auth_parser = argparse.ArgumentParser(
            prog="flacfetch spotify-auth",
            description="Manage Spotify OAuth authentication for headless servers",
            formatter_class=WideHelpFormatter,
            epilog="""
Examples:
  flacfetch spotify-auth login
      Authenticate with Spotify (opens browser)

  flacfetch spotify-auth show
      Show current token status

  flacfetch spotify-auth test
      Test if the token is valid

  flacfetch spotify-auth upload
      Upload token to GCP Secret Manager

Workflow for cloud deployment:
  1. Set SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET env vars
  2. Run: flacfetch spotify-auth login
  3. Run: flacfetch spotify-auth upload
  4. Restart the cloud server to pick up the new token
            """.strip(),
        )
        spotify_auth_parser.add_argument(
            "action",
            choices=["login", "show", "test", "upload"],
            help="Action: login (browser auth), show (token info), test (validate), upload (to GCP)"
        )
        spotify_auth_parser.add_argument(
            "--project", "-p",
            default="nomadkaraoke",
            help="GCP project ID for upload (default: nomadkaraoke)"
        )
        spotify_auth_args = spotify_auth_parser.parse_args(sys.argv[2:])
        spotify_auth_command(spotify_auth_args)
        return

    # Check for 'fix' subcommand - interactive LOCAL credential setup
    if len(sys.argv) > 1 and sys.argv[1] == "fix":
        fix_parser = argparse.ArgumentParser(
            prog="flacfetch fix",
            description="Interactive tool to set up LOCAL credentials (Spotify and YouTube)",
            formatter_class=WideHelpFormatter,
            epilog="""
Examples:
  flacfetch fix
      Set up all local credentials (Spotify and YouTube)

  flacfetch fix spotify
      Set up only Spotify credentials locally

  flacfetch fix youtube
      Set up only YouTube cookies locally

To deploy credentials to the cloud server:
  flacfetch-remote push      - Upload local credentials to GCP
  flacfetch-remote restart   - Restart server to apply changes
  flacfetch-remote fix       - All-in-one: authenticate, upload, and restart
            """.strip(),
        )
        fix_parser.add_argument(
            "target",
            nargs="?",
            choices=["all", "spotify", "youtube"],
            default="all",
            help="What to fix: all (default), spotify, or youtube"
        )
        fix_args = fix_parser.parse_args(sys.argv[2:])
        fix_command(fix_args)
        return

    # Check for 'check' subcommand - credential status check (LOCAL only)
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        check_parser = argparse.ArgumentParser(
            prog="flacfetch check",
            description="Check LOCAL credential status for Spotify and YouTube",
            formatter_class=WideHelpFormatter,
            epilog="""
Examples:
  flacfetch check
      Check local credentials (Spotify token, YouTube cookies)

To check the REMOTE server's credentials:
  flacfetch-remote check
            """.strip(),
        )
        check_args = check_parser.parse_args(sys.argv[2:])
        check_command(check_args)
        return

    parser = argparse.ArgumentParser(
        prog="flacfetch",
        description="""
flacfetch - High-Quality Audio Downloader

Search and download music from multiple sources including private torrent trackers
(RED, OPS) for lossless FLAC and YouTube. Intelligently matches tracks and
presents quality options.
        """.strip(),
        epilog="""
Examples:
  flacfetch "Artist" "Title"
      Search with positional args

  flacfetch -a "Artist" -t "Title"
      Search with explicit flags

  flacfetch "Artist" "Title" --auto
      Auto-select best quality

  flacfetch -a "Artist" -t "Title" -o ~/Music --rename
      Download to ~/Music with auto-rename

  flacfetch -t "Title"
      Search YouTube only (no artist required)

  flacfetch serve --port 8080
      Run as HTTP API server

  flacfetch cookies upload
      Upload YouTube cookies for authenticated downloads

  flacfetch cookies status
      Check YouTube cookies status

  flacfetch spotify-auth login
      Authenticate with Spotify for cloud deployment

  flacfetch spotify-auth upload
      Upload Spotify token to GCP Secret Manager

  flacfetch fix
      Interactive tool to fix credentials (run when you get a notification)

  flacfetch check
      Check credential status (Spotify and YouTube)

  flacfetch check --remote http://server:8080
      Check credentials on a remote flacfetch server

Environment Variables:
  RED_API_KEY                  API key for RED (lossless FLAC source)
  RED_API_URL                  Base URL for RED API (required if using RED)
  OPS_API_KEY                  API key for OPS (lossless FLAC source)
  OPS_API_URL                  Base URL for OPS API (required if using OPS)
  SPOTIPY_CLIENT_ID            Spotify app client ID
  SPOTIPY_CLIENT_SECRET        Spotify app client secret
  SPOTIPY_REDIRECT_URI         OAuth redirect URI (http://127.0.0.1:8888/callback)
  FLACFETCH_PROVIDER_PRIORITY  Provider priority (e.g. 'RED,OPS,Spotify,YouTube')
  FLACFETCH_API_KEY            API key for HTTP API authentication (serve mode)
  FLACFETCH_API_PORT           HTTP API port (serve mode, default: 8080)

Spotify Setup (requires Premium account):
  1. Create app at https://developer.spotify.com/dashboard
  2. Set redirect URI: http://127.0.0.1:8888/callback
  3. Install librespot: brew install librespot (or cargo install librespot)
  4. Export: SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, SPOTIPY_REDIRECT_URI
  5. First run opens browser for OAuth (token cached automatically)
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
        help="Artist name (enables torrent trackers if API keys set)"
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
    search_group.add_argument(
        "--limit",
        type=int,
        default=10,
        metavar="N",
        help="Limit torrent tracker result groups (default: 10, use -e for 20)"
    )

    # Output options
    output_group = parser.add_argument_group("Output Options")
    output_group.add_argument(
        "-o", "--output",
        metavar="DIR",
        help="Output directory (default: current dir)"
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
        help="Exact output filename (extension optional)"
    )

    # Provider options
    provider_group = parser.add_argument_group("Provider Options")
    provider_group.add_argument(
        "--red-key",
        metavar="KEY",
        help="RED API key (or use RED_API_KEY env var)"
    )
    provider_group.add_argument(
        "--red-url",
        metavar="URL",
        help="RED API base URL (or use RED_API_URL env var)"
    )
    provider_group.add_argument(
        "--ops-key",
        metavar="KEY",
        help="OPS API key (or use OPS_API_KEY env var)"
    )
    provider_group.add_argument(
        "--ops-url",
        metavar="URL",
        help="OPS API base URL (or use OPS_API_URL env var)"
    )
    provider_group.add_argument(
        "--no-spotify",
        action="store_true",
        help="Disable Spotify provider even if configured"
    )
    provider_group.add_argument(
        "--provider-priority",
        metavar="NAMES",
        help="Provider priority (comma-separated, e.g. 'RED,OPS,Spotify,YouTube')"
    )
    provider_group.add_argument(
        "--no-fallback",
        action="store_true",
        help="Don't search lower priority providers if higher ones return results"
    )

    # General options
    general_group = parser.add_argument_group("General Options")
    general_group.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )

    args = parser.parse_args()

    setup_logging(args.verbose)

    artist = args.artist
    title = args.title

    # Parse positional arguments
    if not (artist and title) and args.query:
        if len(args.query) == 2:
            # Two positional args: treat as artist and title
            if not artist: artist = args.query[0].strip()
            if not title: title = args.query[1].strip()
        elif len(args.query) == 1:
            # Single positional arg: treat as title only
            if not title: title = args.query[0].strip()
        elif len(args.query) > 2:
            # Multiple args: join all as title
            if not title: title = " ".join(args.query).strip()

    # Validate required arguments
    if not title:
        print(f"\n{Colors.RED}✗ Error: Track title is required{Colors.RESET}\n")
        print(f"{Colors.BOLD}Usage examples:{Colors.RESET}")
        print(f'  {Colors.CYAN}flacfetch "Artist" "Title"{Colors.RESET}')
        print(f'  {Colors.CYAN}flacfetch -a "Artist" -t "Title"{Colors.RESET}')
        print(f'  {Colors.CYAN}flacfetch -t "Title"{Colors.RESET} (YouTube only)\n')
        print(f"Run {Colors.CYAN}flacfetch --help{Colors.RESET} for more information.")
        sys.exit(1)

    manager = FetchManager()

    manager.add_provider(YoutubeProvider())
    manager.register_downloader("YouTube", YoutubeDownloader())

    # Determine search limits and early termination
    search_limit = args.limit
    use_early_termination = not args.exhaustive
    if args.exhaustive and args.limit == 10:  # Default wasn't changed
        search_limit = 20  # Use higher limit for exhaustive mode

    # Register RED provider
    red_key = args.red_key or os.environ.get("RED_API_KEY")
    red_url = args.red_url or os.environ.get("RED_API_URL")
    if red_key and red_url:
        if artist:
            rp = REDProvider(api_key=red_key, base_url=red_url)
            rp.search_limit = search_limit
            rp.early_termination = use_early_termination
            manager.add_provider(rp)

            if TorrentDownloader is not None:
                try:
                    manager.register_downloader("RED", TorrentDownloader())
                except ImportError:
                    pass
        else:
             if args.verbose:
                print("Info: RED provider skipped (requires Artist name).")
    elif red_key and not red_url:
        print(f"{Colors.YELLOW}Warning: RED_API_KEY set but RED_API_URL not set. RED provider disabled.{Colors.RESET}")

    # Register OPS provider
    ops_key = args.ops_key or os.environ.get("OPS_API_KEY")
    ops_url = args.ops_url or os.environ.get("OPS_API_URL")
    if ops_key and ops_url:
        if artist:
            ops = OPSProvider(api_key=ops_key, base_url=ops_url)
            ops.search_limit = search_limit
            ops.early_termination = use_early_termination
            manager.add_provider(ops)

            if TorrentDownloader is not None:
                try:
                    manager.register_downloader("OPS", TorrentDownloader())
                except ImportError:
                    pass
        else:
             if args.verbose:
                print("Info: OPS provider skipped (requires Artist name).")
    elif ops_key and not ops_url:
        print(f"{Colors.YELLOW}Warning: OPS_API_KEY set but OPS_API_URL not set. OPS provider disabled.{Colors.RESET}")

    # Register Spotify provider
    if not args.no_spotify and SPOTIFY_AVAILABLE:
        # Check if Spotify is configured via environment variables
        spotify_client_id = os.environ.get("SPOTIPY_CLIENT_ID")
        spotify_client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET")

        if spotify_client_id and spotify_client_secret:
            try:
                from ..downloaders.spotify import is_librespot_available

                if not is_librespot_available():
                    if args.verbose:
                        print(f"{Colors.YELLOW}Warning: librespot not found. Spotify downloads disabled.{Colors.RESET}")
                        print(f"{Colors.DIM}  Install with: brew install librespot{Colors.RESET}")
                else:
                    sp = SpotifyProvider()
                    manager.add_provider(sp)
                    # Pass provider to downloader so they share OAuth
                    manager.register_downloader("Spotify", SpotifyDownloader(provider=sp))
                    if args.verbose:
                        print("Info: Spotify provider enabled (OAuth)")
            except Exception as e:
                if args.verbose:
                    print(f"{Colors.YELLOW}Warning: Could not initialize Spotify provider: {e}{Colors.RESET}")
        elif args.verbose:
            if spotify_client_id or spotify_client_secret:
                print(f"{Colors.YELLOW}Warning: Spotify partially configured. Need both SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET.{Colors.RESET}")
    elif args.no_spotify and args.verbose:
        print("Info: Spotify provider disabled by --no-spotify flag.")

    if not manager.providers:
        print(f"\n{Colors.RED}✗ Error: No providers configured{Colors.RESET}")
        print(f"\n{Colors.BOLD}Tip:{Colors.RESET} Set RED_API_KEY + RED_API_URL or OPS_API_KEY + OPS_API_URL environment variables to enable lossless FLAC downloads.")
        sys.exit(1)

    # Configure provider priority
    priority_str = args.provider_priority or os.environ.get("FLACFETCH_PROVIDER_PRIORITY")
    if priority_str:
        priority_list = [p.strip() for p in priority_str.split(",")]
        manager.set_provider_priority(priority_list)
    else:
        # Default priority: RED > OPS > Spotify > YouTube
        available_providers = [p.name for p in manager.providers]
        default_priority = []
        for name in ["RED", "OPS", "Spotify", "YouTube"]:
            if name in available_providers:
                default_priority.append(name)
        if default_priority:
            manager.set_provider_priority(default_priority)

    # Configure fallback behavior
    if args.no_fallback:
        manager.enable_fallback_search(False)

    # Show configured providers
    provider_names = [p.name for p in manager.providers]
    provider_str = ", ".join(f"{Colors.CYAN}{p}{Colors.RESET}" for p in provider_names)
    print(f"\n{Colors.BOLD}Searching:{Colors.RESET} {Colors.GREEN}{artist or 'Unknown Artist'}{Colors.RESET} - {Colors.GREEN}{title}{Colors.RESET}")
    print(f"{Colors.BOLD}Providers:{Colors.RESET} {provider_str}\n")

    q = TrackQuery(artist=artist or "", title=title)
    releases = manager.search(q)

    if not releases:
        print(f"{Colors.YELLOW}No results found.{Colors.RESET}")
        print(f"\n{Colors.BOLD}Suggestions:{Colors.RESET}")
        print("  • Try a different spelling or search term")
        print("  • Check that your artist/title are correct")
        if not artist:
            print("  • Provide an artist name for better torrent tracker results")
        if "RED" not in provider_names and "OPS" not in provider_names and not red_key and not ops_key:
            print("  • Set RED_API_KEY + RED_API_URL or OPS_API_KEY + OPS_API_URL to search lossless FLAC sources")
        sys.exit(0)

    selected = None
    if args.auto:
        from ..core.categorize import is_excellent_match

        selected = manager.select_best(releases)

        # Check if we have an excellent match for aggressive auto-selection
        if selected and is_excellent_match(selected, q):
            # Excellent match - just show brief info and proceed immediately
            seeders_info = f", {selected.seeders} seeders" if selected.seeders else ""
            print(f"\n{Colors.GREEN}✓ Auto-selected:{Colors.RESET} {Colors.BOLD}{selected.artist} - {selected.title}{Colors.RESET}")
            print(f"   {Colors.DIM}[{selected.release_type}] {selected.quality}{seeders_info}{Colors.RESET}")
        else:
            # Not an excellent match - show top results so user can see what was chosen
            print(f"\n{Colors.BOLD}Top results:{Colors.RESET}")
            for idx, r in enumerate(releases[:5], 1):
                line = format_release_line(idx, r, artist, use_colors=True)
                print(line)
            if len(releases) > 5:
                print(f"   {Colors.DIM}... and {len(releases) - 5} more{Colors.RESET}")
            print(f"\n{Colors.BOLD}Auto-selected:{Colors.RESET} #{1} - {selected.title} ({selected.quality})")
    else:
        selected = manager.select_interactive(releases, CLIHandler(artist))

    if selected:
        if not args.auto:
            print(f"\n{Colors.BOLD}Selected:{Colors.RESET} {selected.title} ({selected.quality})")

        # Determine output directory
        output_dir = args.output or "."

        # Determine output filename if needed
        output_filename = None
        if args.filename:
            output_filename = args.filename
        elif args.auto_rename and artist and title:
            # Auto-rename to "ARTIST - TITLE.ext"
            # Extension will be determined by the downloader
            output_filename = f"{artist} - {title}"

        try:
            downloaded_file = manager.download(selected, output_dir, output_filename=output_filename)

            # Friendly summary message
            print(f"\n{Colors.GREEN}{'='*60}{Colors.RESET}")
            print(f"{Colors.GREEN}✓ Download Complete!{Colors.RESET}\n")
            print(f"{Colors.BOLD}Track:{Colors.RESET}     {artist or 'Unknown'} - {title}")
            print(f"{Colors.BOLD}Source:{Colors.RESET}    {selected.source_name}")
            print(f"{Colors.BOLD}Quality:{Colors.RESET}   {selected.quality}")
            if selected.size_bytes:
                size_mb = selected.size_bytes / (1024 * 1024)
                print(f"{Colors.BOLD}Size:{Colors.RESET}      {size_mb:.1f} MB")
            if downloaded_file:
                # Get relative path if possible
                try:
                    rel_path = os.path.relpath(downloaded_file)
                    if len(rel_path) < len(downloaded_file):
                        file_display = rel_path
                    else:
                        file_display = downloaded_file
                except:
                    file_display = downloaded_file
                print(f"{Colors.BOLD}Saved to:{Colors.RESET}  {Colors.CYAN}{file_display}{Colors.RESET}")
            print(f"{Colors.GREEN}{'='*60}{Colors.RESET}\n")

        except Exception as e:
            if args.verbose:
                import traceback
                traceback.print_exc()
            print(f"\n{Colors.RED}{'='*60}{Colors.RESET}")
            print(f"{Colors.RED}✗ Download Failed{Colors.RESET}")
            print(f"{Colors.RED}Error: {e}{Colors.RESET}")
            print(f"{Colors.RED}{'='*60}{Colors.RESET}\n")
            sys.exit(1)
    else:
        print(f"\n{Colors.YELLOW}No selection made. Exiting.{Colors.RESET}")

if __name__ == "__main__":
    main()
