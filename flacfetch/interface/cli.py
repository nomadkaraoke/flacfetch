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
from ..providers.redacted import RedactedProvider
from ..providers.youtube import YoutubeProvider

try:
    from ..downloaders.torrent import TorrentDownloader
except ImportError:
    TorrentDownloader = None

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

    # Get quality info - handle both Release and dict
    if isinstance(release, Release):
        is_lossless = release.quality.is_lossless()
        quality_str = str(release.quality)
        media_name = release.quality.media.name
        formatted_size = release.formatted_size
        formatted_duration = release.formatted_duration
        formatted_views = release.formatted_views
    else:
        # Dict from to_dict()
        is_lossless = release.get("is_lossless", False)
        quality_str = release.get("quality_str", "")
        quality_data = release.get("quality", {})
        media_name = quality_data.get("media", "OTHER") if isinstance(quality_data, dict) else "OTHER"
        formatted_size = release.get("formatted_size", "?")
        formatted_duration = release.get("formatted_duration")
        formatted_views = release.get("formatted_views")

    # 1. Format indicator (lossless vs lossy)
    if is_lossless:
        format_indicator = f"{C.GREEN}[LOSSLESS]{C.RESET}"
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
    if source_name != "YouTube":
        qual_text = quality_str
        if qual_text.endswith(media_name):
            qual_text = qual_text[:-len(media_name)].strip()
        if "24bit" in qual_text:
            qual_str = f" ({C.YELLOW}{qual_text}{C.RESET})"
        else:
            qual_str = f" ({C.GREEN}{qual_text}{C.RESET})"

    # Stats (Size, Seeders/Views)
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

    if view_count is not None:
        if view_count > 1_000_000:
            v_color = C.GREEN
        elif view_count >= 10_000:
            v_color = C.YELLOW
        else:
            v_color = C.RED
        stats_parts.append(f"Views: {v_color}{formatted_views}{C.RESET}")

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

    return f"{header}{meta_str}{qual_str}{stats_str}{file_str}"


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

class CLIHandler(InteractionHandler):
    def __init__(self, target_artist: Optional[str] = None):
        self.target_artist = target_artist

    def select_release(self, releases: list[Release]) -> Optional[Release]:
        # Use the shared display function
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

    parser = argparse.ArgumentParser(
        prog="flacfetch",
        description="""
flacfetch - High-Quality Audio Downloader

Search and download music from multiple sources including private torrent trackers
(Redacted, OPS) for lossless FLAC and YouTube. Intelligently matches tracks and
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

Environment Variables:
  REDACTED_API_KEY             API key for Redacted (lossless FLAC source)
  OPS_API_KEY                  API key for OPS (lossless FLAC source)
  FLACFETCH_PROVIDER_PRIORITY  Provider priority (e.g. 'Redacted,OPS,YouTube')
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
        "--limit",
        type=int,
        default=20,
        metavar="N",
        help="Limit torrent tracker result groups (default: 20)"
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
        "--redacted-key",
        metavar="KEY",
        help="Redacted API key (or use REDACTED_API_KEY env var)"
    )
    provider_group.add_argument(
        "--ops-key",
        metavar="KEY",
        help="OPS API key (or use OPS_API_KEY env var)"
    )
    provider_group.add_argument(
        "--provider-priority",
        metavar="NAMES",
        help="Provider priority (comma-separated, e.g. 'Redacted,OPS,YouTube')"
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

    # Register Redacted provider
    redacted_key = args.redacted_key or os.environ.get("REDACTED_API_KEY")
    if redacted_key:
        if artist:
            rp = RedactedProvider(redacted_key)
            rp.search_limit = args.limit
            manager.add_provider(rp)

            if TorrentDownloader:
                try:
                    manager.register_downloader("Redacted", TorrentDownloader())
                except ImportError:
                    pass
        else:
             if args.verbose:
                print("Info: Redacted provider skipped (requires Artist name).")

    # Register OPS provider
    ops_key = args.ops_key or os.environ.get("OPS_API_KEY")
    if ops_key:
        if artist:
            ops = OPSProvider(ops_key)
            ops.search_limit = args.limit
            manager.add_provider(ops)

            if TorrentDownloader:
                try:
                    manager.register_downloader("OPS", TorrentDownloader())
                except ImportError:
                    pass
        else:
             if args.verbose:
                print("Info: OPS provider skipped (requires Artist name).")

    if not manager.providers:
        print(f"\n{Colors.RED}✗ Error: No providers configured{Colors.RESET}")
        print(f"\n{Colors.BOLD}Tip:{Colors.RESET} Set REDACTED_API_KEY or OPS_API_KEY environment variable to enable lossless FLAC downloads.")
        sys.exit(1)

    # Configure provider priority
    priority_str = args.provider_priority or os.environ.get("FLACFETCH_PROVIDER_PRIORITY")
    if priority_str:
        priority_list = [p.strip() for p in priority_str.split(",")]
        manager.set_provider_priority(priority_list)
    else:
        # Default priority: Redacted > OPS > YouTube
        available_providers = [p.name for p in manager.providers]
        default_priority = []
        for name in ["Redacted", "OPS", "YouTube"]:
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
        if "Redacted" not in provider_names and "OPS" not in provider_names and not args.redacted_key and not args.ops_key:
            print("  • Set REDACTED_API_KEY or OPS_API_KEY to search lossless FLAC sources")
        sys.exit(0)

    selected = None
    if args.auto:
        selected = manager.select_best(releases)
        print(f"\n{Colors.BOLD}Auto-selected:{Colors.RESET} {selected.title} ({selected.quality})")
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
