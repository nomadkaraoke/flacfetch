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
    print(f"{Colors.BOLD}Server:{Colors.RESET}    {Colors.CYAN}{api_url}{Colors.RESET}\n")

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
                            # Find the index in original results
                            selected_release = cat_display[idx - 1]
                            # Find matching result in display_results by download_url
                            for i, r in enumerate(display_results):
                                if r.get('download_url') == selected_release.download_url:
                                    selected_idx = i
                                    break
                            selected = display_results[selected_idx]
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

