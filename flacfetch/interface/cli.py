import argparse
import os
import sys
from typing import List, Optional
from ..core.models import Release, TrackQuery
from ..core.interfaces import InteractionHandler
from ..core.manager import FetchManager
from ..core.log import setup_logging
from ..providers.redacted import RedactedProvider
from ..providers.youtube import YoutubeProvider
from ..downloaders.youtube import YoutubeDownloader

try:
    from ..downloaders.torrent import TorrentDownloader
except ImportError:
    TorrentDownloader = None

# ANSI Color Codes
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    MAGENTA = "\033[35m"
    BLUE = "\033[34m"
    RED = "\033[31m"
    BRIGHT_MAGENTA = "\033[95m" # Lighter/Brighter Pink/Purple

class CLIHandler(InteractionHandler):
    def select_release(self, releases: List[Release]) -> Optional[Release]:
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
        # 1. [Redacted] Artist - Title
        header = f"{idx}. [{Colors.CYAN}{r.source_name}{Colors.RESET}] {Colors.BOLD}{r.artist} - {r.title}{Colors.RESET}"
        
        # Metadata: [Album, 2014 / Label / WEB]
        meta_parts = []
        if r.release_type: meta_parts.append(f"{Colors.MAGENTA}{r.release_type}{Colors.RESET}")
        if r.year: meta_parts.append(f"{Colors.YELLOW}{r.year}{Colors.RESET}")
        if r.label: meta_parts.append(r.label)
        if r.edition_info: meta_parts.append(r.edition_info)
        meta_parts.append(r.quality.media.name)
        
        meta_str = f" [{ ' / '.join(meta_parts) }]" if meta_parts else ""
        
        # Quality: (FLAC 24bit) - Removing redundant media
        qual_text = str(r.quality)
        media_name = r.quality.media.name
        if qual_text.endswith(media_name):
            qual_text = qual_text[:-len(media_name)].strip()
            
        # Colorize quality
        if "24bit" in qual_text:
            qual_str = f" ({Colors.YELLOW}{qual_text}{Colors.RESET})"
        else:
            qual_str = f" ({Colors.GREEN}{qual_text}{Colors.RESET})"
        
        # Size & Seeders
        size_str = r.formatted_size
        if size_str == "?":
             # Only show ? if verbose or if no other stats? 
             # Actually keeping it minimal is better if unknown.
             pass
             
        stats = f" - {size_str}"
        if r.seeders is not None:
            stats += f", {Colors.BRIGHT_MAGENTA}Seeders: {r.seeders}{Colors.RESET}"
            
        print(f"{header}{meta_str}{qual_str}{stats}")
        
        # Target File
        if r.target_file:
            print(f"   {Colors.YELLOW}-> File: {r.target_file}{Colors.RESET}")

def main():
    parser = argparse.ArgumentParser(description="flacfetch - Audio Downloader")
    parser.add_argument("query", nargs="*", help="Search query (e.g. 'Artist - Title')")
    parser.add_argument("-a", "--artist", help="Artist name")
    parser.add_argument("-t", "--title", "--track", dest="title", help="Track/Song title")
    parser.add_argument("--auto", action="store_true", help="Auto select best quality")
    parser.add_argument("--redacted-key", help="Redacted API Key (or set REDACTED_API_KEY env var)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--limit", type=int, default=20, help="Limit number of API result groups to process (default 20)")
    
    args = parser.parse_args()
    
    setup_logging(args.verbose)
    
    artist = args.artist
    title = args.title
    
    if not (artist and title) and args.query:
        query_str = " ".join(args.query)
        if " - " in query_str:
            parts = query_str.split(" - ", 1)
            if not artist: artist = parts[0].strip()
            if not title: title = parts[1].strip()
        else:
            if not title: title = query_str
    
    if not title:
        print(f"{Colors.RED}Error: Track title is required.{Colors.RESET}")
        sys.exit(1)
        
    manager = FetchManager()
    
    manager.add_provider(YoutubeProvider())
    manager.register_downloader("YouTube", YoutubeDownloader())

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
    
    if not manager.providers:
        print("No providers configured.")
        sys.exit(1)
            
    print(f"Searching for: {Colors.BOLD}{artist or 'Unknown'} - {title}{Colors.RESET} ...")
    q = TrackQuery(artist=artist or "", title=title)
    releases = manager.search(q)
    
    if not releases:
        print("No results found.")
        sys.exit(0)
    
    selected = None
    if args.auto:
        selected = manager.select_best(releases)
    else:
        selected = manager.select_interactive(releases, CLIHandler())
        
    if selected:
        print(f"\nSelected: {selected.title} ({selected.quality})")
        try:
            manager.download(selected, ".")
        except Exception as e:
            if args.verbose:
                import traceback
                traceback.print_exc()
            print(f"{Colors.RED}Download failed: {e}{Colors.RESET}")
    else:
        print("No selection made.")

if __name__ == "__main__":
    main()
