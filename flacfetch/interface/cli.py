import argparse
import os
import re
import sys
from typing import Optional

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

class CLIHandler(InteractionHandler):
    def __init__(self, target_artist: Optional[str] = None):
        self.target_artist = target_artist

    def select_release(self, releases: list[Release]) -> Optional[Release]:
        print(f"\nFound {len(releases)} releases:\n")
        for idx, r in enumerate(releases):
            self._print_release(idx + 1, r)

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

    def _print_release(self, idx: int, r: Release):
        # 1. Format indicator (lossless vs lossy)
        if r.quality.is_lossless():
            format_indicator = f"{Colors.GREEN}[LOSSLESS]{Colors.RESET}"
        else:
            format_indicator = f"{Colors.DIM}[lossy]{Colors.RESET}"

        # 2. Source Name
        source_tag = f"[{Colors.CYAN}{r.source_name}{Colors.RESET}]"

        # Title/Artist Display
        if r.source_name == "YouTube":
            # Channel: Title
            subject = r.channel
            color = Colors.ORANGE
            if subject and self.target_artist and subject.lower() == self.target_artist.lower():
                color = Colors.GREEN

            subject_str = f"{color}{subject}{Colors.RESET}" if subject else "Unknown"
            title_str = f"{Colors.BOLD}{r.title}{Colors.RESET}"
            main_info = f"{subject_str}: {title_str}"
        else:
            # Torrent trackers (Redacted/OPS): Artist - Title
            subject = r.artist
            color = Colors.ORANGE
            if subject and self.target_artist and subject.lower() == self.target_artist.lower():
                color = Colors.GREEN

            subject_str = f"{color}{subject}{Colors.RESET}" if subject else "Unknown"
            title_str = f"{Colors.BOLD}{r.title}{Colors.RESET}"
            main_info = f"{subject_str} - {title_str}"

        header = f"{idx}. {format_indicator} {source_tag} {main_info}"

        # Metadata
        meta_parts = []

        if r.source_name == "YouTube":
            # YouTube Metadata: [Duration | Year (colored) | URL]
            if r.formatted_duration:
                meta_parts.append(r.formatted_duration)

            if r.year:
                y = r.year
                if y >= 2020:
                    c = Colors.GREEN
                elif y >= 2015:
                    c = Colors.YELLOW
                else:
                    c = Colors.RED
                meta_parts.append(f"{c}{y}{Colors.RESET}")

            # Shorten URL: https://www.youtube.com/watch?v=ID -> youtu.be/ID
            if r.download_url:
                short_url = r.download_url
                if "youtube.com/watch?v=" in short_url:
                    try:
                        vid_id = short_url.split("v=")[1].split("&")[0]
                        short_url = f"youtu.be/{vid_id}"
                    except IndexError:
                        pass # Keep original if format unexpected
                elif "googlevideo.com" in short_url:
                    # Fallback if ID extraction failed before, though provider tries to fix it now.
                    short_url = "Stream"
                meta_parts.append(short_url)
        else:
            # Torrent tracker Metadata: [Album, 2014 / Label / WEB]
            if r.release_type: meta_parts.append(f"{Colors.MAGENTA}{r.release_type}{Colors.RESET}")
            if r.year: meta_parts.append(f"{Colors.YELLOW}{r.year}{Colors.RESET}")
            if r.label: meta_parts.append(r.label)
            if r.edition_info: meta_parts.append(r.edition_info)
            meta_parts.append(r.quality.media.name)

        meta_str = f" [{ ' / '.join(meta_parts) }]" if meta_parts else ""

        # Quality
        qual_str = ""
        if r.source_name != "YouTube":
            qual_text = str(r.quality)
            media_name = r.quality.media.name
            if qual_text.endswith(media_name):
                qual_text = qual_text[:-len(media_name)].strip()

            if "24bit" in qual_text:
                qual_str = f" ({Colors.YELLOW}{qual_text}{Colors.RESET})"
            else:
                qual_str = f" ({Colors.GREEN}{qual_text}{Colors.RESET})"

        # Stats (Size, Seeders/Views)
        stats_parts = []

        # Size
        size_str = r.formatted_size
        if size_str != "?":
            stats_parts.append(size_str)

        # Seeders (Redacted)
        if r.seeders is not None:
            s = r.seeders
            if s > 50:
                s_color = Colors.GREEN
            elif s >= 10:
                s_color = Colors.YELLOW
            else:
                s_color = Colors.RED
            stats_parts.append(f"Seeders: {s_color}{s}{Colors.RESET}")

        # Views (YouTube)
        if r.view_count is not None:
            v = r.view_count
            if v > 1_000_000:
                v_color = Colors.GREEN
            elif v >= 10_000:
                v_color = Colors.YELLOW
            else:
                v_color = Colors.RED
            stats_parts.append(f"Views: {v_color}{r.formatted_views}{Colors.RESET}")

        stats_str = f" - {', '.join(stats_parts)}" if stats_parts else ""

        # Target File (Redacted) with highlighting - moved to end of line
        file_str = ""
        if r.target_file:
            fname = r.target_file

            # Strip track numbers (e.g., "12. " or "1.12 - " or "05 - ")
            fname = re.sub(r'^\d+[\.\-\s]+', '', fname)

            if r.track_pattern:
                # Highlight match
                pattern = re.escape(r.track_pattern)
                fname = re.sub(f"({pattern})", f"{Colors.YELLOW}\\1{Colors.RESET}", fname, flags=re.IGNORECASE)

            file_str = f', "{fname}"'

        print(f"{header}{meta_str}{qual_str}{stats_str}{file_str}")

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
