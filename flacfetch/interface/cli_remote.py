"""
Remote CLI for flacfetch - download via remote flacfetch HTTP API.

Usage:
    flacfetch-remote "Artist" "Title"
    flacfetch-remote -a "Artist" -t "Title" --auto
    flacfetch-remote "Artist" "Title" -o ~/Music

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

from .cli import Colors, print_releases


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

    def search(self, artist: str, title: str) -> Dict[str, Any]:
        """Search for audio."""
        with httpx.Client() as client:
            resp = client.post(
                f"{self.base_url}/search",
                headers=self._headers(),
                json={"artist": artist, "title": title},
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


def convert_api_result_to_display(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert API search result to format expected by print_releases.

    The API returns SearchResultItem format, we need to map it to
    what format_release_line expects.
    """
    # Map provider back to source_name
    result["source_name"] = result.get("provider", "Unknown")

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


def main():
    """Main entry point for flacfetch-remote CLI."""
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

    # Output options
    output_group = parser.add_argument_group("Output Options")
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
    print(f"{Colors.BOLD}Server:{Colors.RESET}    {Colors.CYAN}{api_url}{Colors.RESET}\n")

    try:
        search_result = client.search(artist, title)
    except Exception as e:
        print(f"\n{Colors.RED}✗ Search failed: {e}{Colors.RESET}")
        sys.exit(1)

    results = search_result.get("results", [])
    search_id = search_result.get("search_id")

    if not results:
        print(f"{Colors.YELLOW}No results found.{Colors.RESET}")
        sys.exit(0)

    # Convert results for display
    display_results = [convert_api_result_to_display(r) for r in results]

    # Display results
    use_colors = not args.no_color
    print_releases(display_results, target_artist=artist, use_colors=use_colors)

    # Select result
    selected_idx = 0
    if args.auto:
        # Auto-select first result (API already returns sorted by quality)
        selected_idx = 0
        selected = display_results[0]
        print(f"\n{Colors.BOLD}Auto-selected:{Colors.RESET} {selected.get('artist', artist)} - {selected.get('title', title)}")
    else:
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

    # Success summary
    print(f"\n{Colors.GREEN}{'='*60}{Colors.RESET}")
    print(f"{Colors.GREEN}✓ Download Complete!{Colors.RESET}\n")
    print(f"{Colors.BOLD}Track:{Colors.RESET}     {artist} - {title}")
    print(f"{Colors.BOLD}Source:{Colors.RESET}    {final_status.get('provider', 'Unknown')}")

    if final_status.get("output_path"):
        print(f"{Colors.BOLD}File:{Colors.RESET}      {Colors.CYAN}{final_status['output_path']}{Colors.RESET}")

    if final_status.get("gcs_path"):
        print(f"{Colors.BOLD}GCS:{Colors.RESET}       {Colors.CYAN}{final_status['gcs_path']}{Colors.RESET}")

    if final_status.get("status") == "seeding":
        print(f"{Colors.BOLD}Status:{Colors.RESET}    {Colors.GREEN}Seeding{Colors.RESET} (torrent will continue seeding on server)")

    print(f"{Colors.GREEN}{'='*60}{Colors.RESET}\n")


if __name__ == "__main__":
    main()

