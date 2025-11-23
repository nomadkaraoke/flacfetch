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

class CLIHandler(InteractionHandler):
    def select_release(self, releases: List[Release]) -> Optional[Release]:
        print(f"\nFound {len(releases)} releases:")
        for idx, r in enumerate(releases):
            print(f"{idx + 1}. {r}")
        
        while True:
            choice = input("\nSelect a release (1-N, 0 to cancel): ")
            try:
                idx = int(choice)
                if idx == 0:
                    return None
                if 1 <= idx <= len(releases):
                    return releases[idx - 1]
                print("Invalid selection.")
            except ValueError:
                print("Please enter a number.")

def main():
    parser = argparse.ArgumentParser(description="flacfetch - Audio Downloader")
    parser.add_argument("query", nargs="+", help="Search query (e.g. 'Artist - Title')")
    parser.add_argument("--auto", action="store_true", help="Auto select best quality")
    parser.add_argument("--redacted-key", help="Redacted API Key (or set REDACTED_API_KEY env var)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging(args.verbose)
    
    query_str = " ".join(args.query)
    
    if " - " in query_str:
        parts = query_str.split(" - ", 1)
        artist = parts[0].strip()
        title = parts[1].strip()
    else:
        artist = "" 
        title = query_str
        
    manager = FetchManager()
    
    # Configure Providers & Downloaders
    
    # YouTube (Always available via yt-dlp)
    manager.add_provider(YoutubeProvider())
    manager.register_downloader("YouTube", YoutubeDownloader())

    # Redacted
    redacted_key = args.redacted_key or os.environ.get("REDACTED_API_KEY")
    if redacted_key:
        manager.add_provider(RedactedProvider(redacted_key))
        if TorrentDownloader:
            try:
                # Instantiate with default settings
                manager.register_downloader("Redacted", TorrentDownloader())
            except ImportError:
                print("Warning: libtorrent not installed properly. Redacted downloads disabled.")
        else:
            print("Warning: libtorrent not installed. Redacted downloads disabled.")
    else:
        # Only log info if verbose, or print warning?
        # If user expected it, they'd have provided key. 
        if args.verbose:
            print("Info: No Redacted API Key provided. Private tracker search disabled.")
        
    if not manager.providers:
        print("No providers configured. Exiting.")
        sys.exit(1)
            
    print(f"Searching for: {artist} - {title} ...")
    q = TrackQuery(artist=artist, title=title)
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
        print(f"\nSelected: {selected}")
        try:
            manager.download(selected, ".")
        except Exception as e:
            if args.verbose:
                import traceback
                traceback.print_exc()
            print(f"Download failed: {e}")
    else:
        print("No selection made.")

if __name__ == "__main__":
    main()
