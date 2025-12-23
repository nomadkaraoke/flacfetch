# flacfetch Library API Documentation

This document provides comprehensive documentation for using flacfetch as a library in your Python projects.

## Table of Contents

- [Installation](#installation)
- [Quick Start](#quick-start)
- [Core Components](#core-components)
- [Providers](#providers)
- [Data Models](#data-models)
- [Manager API](#manager-api)
- [Advanced Usage](#advanced-usage)
- [Error Handling](#error-handling)
- [Examples](#examples)

## Installation

```bash
pip install flacfetch
```

For type checking support:
```bash
pip install flacfetch[dev]
```

## Quick Start

```python
from flacfetch.core.manager import FetchManager
from flacfetch.core.models import TrackQuery
from flacfetch.providers.red import REDProvider
from flacfetch.providers.youtube import YoutubeProvider

# Create manager and add providers
manager = FetchManager()
# Note: base_url must be provided (typically from environment variable)
manager.add_provider(REDProvider(api_key="your_key", base_url="https://your.tracker.url"))
manager.add_provider(YoutubeProvider())

# Search for a track
query = TrackQuery(artist="Artist Name", title="Track Title")
results = manager.search(query)

# Select best result
best = manager.select_best(results)

# Download
if best:
    file_path = manager.download(best, output_path="./downloads")
    print(f"Downloaded: {file_path}")
```

## Core Components

### FetchManager

The main orchestration class for searching and downloading audio.

```python
from flacfetch.core.manager import FetchManager

manager = FetchManager()
```

#### Methods

**`add_provider(provider: Provider) -> None`**

Add a provider to the search pool.

```python
manager.add_provider(REDProvider(api_key="...", base_url="..."))
manager.add_provider(YoutubeProvider())
```

**`search(query: TrackQuery) -> list[Release]`**

Search all providers for the given track.

```python
query = TrackQuery(artist="Seether", title="Tonight")
results = manager.search(query)
# Returns list of Release objects from all providers
```

**`select_best(releases: list[Release]) -> Release | None`**

Select the best release from a list based on quality heuristics.

```python
best = manager.select_best(results)
```

Sorting priority:
1. Match score (filename/title match)
2. YouTube channel match (for YouTube results)
3. Official audio indicators (for YouTube)
4. Release type (Album > Single > EP > etc.)
5. Seeders/Views
6. Quality (Lossless > Lossy, bit depth, bitrate)
7. Year (context-dependent: private trackers prefer oldest, YouTube prefers newest)

**`download(release: Release, output_path: str, output_filename: str | None = None) -> str`**

Download a release to the specified path.

```python
# Auto-generate filename
file_path = manager.download(best, "./downloads")

# Custom filename (without extension)
file_path = manager.download(best, "./downloads", output_filename="My Song")
```

Returns the full path to the downloaded file.

**`set_provider_priority(priority_list: list[str]) -> None`**

Set the order in which providers are searched.

```python
# Search OPS first, then RED, then YouTube
manager.set_provider_priority(["OPS", "RED", "YouTube"])
```

**`enable_fallback_search(enabled: bool = True) -> None`**

Control whether to search lower-priority providers if higher ones return no results.

```python
# Only search highest priority provider
manager.enable_fallback_search(False)

# Search all providers (default)
manager.enable_fallback_search(True)
```

**`register_downloader(source_name: str, downloader: Downloader) -> None`**

Register a custom downloader for a specific provider.

```python
from flacfetch.downloaders.torrent import TorrentDownloader

manager.register_downloader("RED", TorrentDownloader())
```

**`set_default_downloader(downloader: Downloader) -> None`**

Set a default downloader for providers without a specific downloader.

```python
manager.set_default_downloader(TorrentDownloader())
```

## Providers

### REDProvider

Provider for RED private music tracker.

```python
import os
from flacfetch.providers.red import REDProvider

# Base URL must be provided (typically from environment variable for security)
provider = REDProvider(
    api_key=os.environ.get("RED_API_KEY"),
    base_url=os.environ.get("RED_API_URL"),
    cache_dir="/path/to/cache"  # Optional: custom cache directory
)

# Configure search limit (default: 20)
provider.search_limit = 10

manager.add_provider(provider)
```

**Properties:**
- `name: str` - Provider name ("RED")
- `search_limit: int` - Maximum number of results per search
- `cache_dir: Path | None` - Directory for caching torrent files

**Methods:**
- `search(query: TrackQuery) -> list[Release]` - Search for tracks
- `populate_details(release: Release) -> None` - Load file list for a release
- `fetch_artifact(release: Release) -> bytes | None` - Download torrent file

### OPSProvider

Provider for OPS private music tracker.

```python
import os
from flacfetch.providers.ops import OPSProvider

# Base URL must be provided (typically from environment variable for security)
provider = OPSProvider(
    api_key=os.environ.get("OPS_API_KEY"),
    base_url=os.environ.get("OPS_API_URL"),
    cache_dir="/path/to/cache"  # Optional
)

provider.search_limit = 15
manager.add_provider(provider)
```

**Properties and methods:** Same as REDProvider, with `name = "OPS"`

### YoutubeProvider

Provider for YouTube audio (via yt-dlp).

```python
from flacfetch.providers.youtube import YoutubeProvider

provider = YoutubeProvider()
manager.add_provider(provider)
```

**Properties:**
- `name: str` - Provider name ("YouTube")

**Methods:**
- `search(query: TrackQuery) -> list[Release]` - Search YouTube
- `populate_details(release: Release) -> None` - No-op for YouTube
- `fetch_artifact(release: Release) -> None` - Returns None (no artifact needed)

## Data Models

### TrackQuery

Query parameters for searching.

```python
from flacfetch.core.models import TrackQuery

query = TrackQuery(
    artist="Artist Name",
    title="Track Title"
)
```

**Fields:**
- `artist: str` - Artist name
- `title: str` - Track title

### Release

Represents a single search result.

```python
from flacfetch.core.models import Release, Quality, AudioFormat, MediaSource

release = Release(
    title="Album Title",
    artist="Artist Name",
    quality=Quality(format=AudioFormat.FLAC, bit_depth=24, media=MediaSource.WEB),
    source_name="RED",
    download_url="...",
    info_hash="...",  # For torrents
    size_bytes=50000000,
    year=2020,
    edition_info="Deluxe Edition",
    label="Record Label",
    catalogue_number="CAT123",
    release_type="Album",
    seeders=50,
    target_file="01. Track.flac",
    target_file_size=12345678,
    match_score=0.95
)
```

**Core Fields:**
- `title: str` - Album/release title
- `artist: str` - Artist name
- `quality: Quality` - Audio quality information
- `source_name: str` - Provider name (e.g., "RED", "OPS", "YouTube")
- `download_url: str | None` - URL or path for downloading
- `info_hash: str | None` - BitTorrent info hash (for torrents)
- `size_bytes: int | None` - Total release size in bytes

**Metadata Fields:**
- `year: int | None` - Release year
- `edition_info: str | None` - Edition details (e.g., "Deluxe Edition")
- `label: str | None` - Record label
- `catalogue_number: str | None` - Catalogue number
- `release_type: str | None` - Type (e.g., "Album", "Single", "EP")
- `seeders: int | None` - Number of seeders (for torrents)

**YouTube-specific Fields:**
- `channel: str | None` - YouTube channel name
- `view_count: int | None` - View count
- `duration_seconds: int | None` - Video duration

**Track-specific Fields:**
- `target_file: str | None` - Specific file within release
- `target_file_size: int | None` - Size of target file
- `track_pattern: str | None` - Pattern for finding target file
- `match_score: float` - Confidence score (0.0 to 1.0) for title match

**Computed Properties:**

```python
# Formatted file size
print(release.formatted_size)  # e.g., "50.0 MB"

# Formatted duration
print(release.formatted_duration)  # e.g., "3:45"

# Formatted view count
print(release.formatted_views)  # e.g., "1.2M"

# String representation
print(str(release))
# [RED] Artist Name - Album Title [Album, 2020 / Label / WEB] (FLAC 24bit WEB) Seeders: 50 - 50.0 MB
```

### Quality

Represents audio quality specifications.

```python
from flacfetch.core.models import Quality, AudioFormat, MediaSource

# Lossless
quality = Quality(
    format=AudioFormat.FLAC,
    bit_depth=24,
    sample_rate=96000,
    media=MediaSource.WEB
)

# Lossy
quality = Quality(
    format=AudioFormat.MP3,
    bitrate=320,
    media=MediaSource.CD
)
```

**Fields:**
- `format: AudioFormat` - Audio format (FLAC, MP3, AAC, OPUS, WAV, OTHER)
- `bit_depth: int | None` - Bit depth for lossless (e.g., 16, 24)
- `sample_rate: int | None` - Sample rate in Hz (e.g., 44100, 96000)
- `bitrate: int | None` - Bitrate in kbps for lossy (e.g., 320)
- `media: MediaSource` - Source media (WEB, CD, VINYL, DVD, CASSETTE, OTHER)

**Methods:**

```python
# Check if lossless
is_lossless = quality.is_lossless()  # True for FLAC/WAV

# String representation
print(str(quality))  # "FLAC 24bit WEB"

# Comparison (for sorting)
better_quality = quality1 > quality2  # True if quality1 is better
```

**Quality Comparison Logic:**
1. Lossless > Lossy
2. For lossless: Higher bit depth > Lower bit depth
3. For lossless: Higher sample rate > Lower sample rate
4. For lossy: Higher bitrate > Lower bitrate
5. Media preference: WEB ≈ CD > VINYL > CASSETTE

### AudioFormat (Enum)

```python
from flacfetch.core.models import AudioFormat

AudioFormat.FLAC   # Lossless
AudioFormat.WAV    # Lossless
AudioFormat.MP3    # Lossy
AudioFormat.AAC    # Lossy
AudioFormat.OPUS   # Lossy (high quality)
AudioFormat.OTHER  # Unknown
```

### MediaSource (Enum)

```python
from flacfetch.core.models import MediaSource

MediaSource.WEB
MediaSource.CD
MediaSource.VINYL
MediaSource.DVD
MediaSource.CASSETTE
MediaSource.OTHER
```

## Manager API

### Provider Priority

Control the order in which providers are searched:

```python
import os
manager = FetchManager()

# Add providers (base_url required for private trackers)
manager.add_provider(REDProvider(api_key=os.environ["RED_API_KEY"], base_url=os.environ["RED_API_URL"]))
manager.add_provider(OPSProvider(api_key=os.environ["OPS_API_KEY"], base_url=os.environ["OPS_API_URL"]))
manager.add_provider(YoutubeProvider())

# Set priority (searches in this order)
manager.set_provider_priority(["RED", "OPS", "YouTube"])

# Disable fallback (only search first provider)
manager.enable_fallback_search(False)

# With fallback disabled, only RED will be searched
# even if it returns no results
results = manager.search(query)
```

### Custom Downloaders

Implement custom downloaders for different sources:

```python
from flacfetch.core.interfaces import Downloader
from flacfetch.core.models import Release

class CustomDownloader(Downloader):
    def download(self, release: Release, output_path: str, 
                 output_filename: str | None = None) -> str:
        # Custom download logic
        # Return path to downloaded file
        return "/path/to/downloaded/file.flac"

# Register for specific provider
manager.register_downloader("CustomProvider", CustomDownloader())

# Or set as default
manager.set_default_downloader(CustomDownloader())
```

## Advanced Usage

### Filtering Results

```python
# Search
results = manager.search(query)

# Filter by quality
lossless_only = [r for r in results if r.quality.is_lossless()]

# Filter by source
red_only = [r for r in results if r.source_name == "RED"]

# Filter by year
recent = [r for r in results if r.year and r.year >= 2020]

# Sort by seeders
sorted_by_seeders = sorted(results, key=lambda r: r.seeders or 0, reverse=True)

# Select best
best = manager.select_best(filtered_results)
```

### Custom Sorting

```python
def custom_sort(releases: list[Release]) -> Release | None:
    """Custom sorting logic"""
    if not releases:
        return None
    
    # Example: Prioritize CD rips over WEB
    cd_rips = [r for r in releases if r.quality.media == MediaSource.CD]
    if cd_rips:
        # Sort by quality within CD rips
        return max(cd_rips, key=lambda r: r.quality)
    
    # Fallback to default sorting
    return max(releases, key=lambda r: r.quality)

# Use custom sorting
best = custom_sort(results)
```

### Batch Processing

```python
tracks = [
    ("Artist 1", "Title 1"),
    ("Artist 2", "Title 2"),
    ("Artist 3", "Title 3"),
]

for artist, title in tracks:
    query = TrackQuery(artist=artist, title=title)
    results = manager.search(query)
    best = manager.select_best(results)
    
    if best:
        try:
            file_path = manager.download(best, "./downloads")
            print(f"✓ {artist} - {title} -> {file_path}")
        except Exception as e:
            print(f"✗ {artist} - {title}: {e}")
    else:
        print(f"✗ {artist} - {title}: No results found")
```

### Provider Configuration

```python
import os
from pathlib import Path

# Create provider with custom settings (base_url required)
red = REDProvider(
    api_key=os.environ["RED_API_KEY"],
    base_url=os.environ["RED_API_URL"]
)
red.search_limit = 5  # Limit results
red.cache_dir = Path("/custom/cache")  # Custom cache location

# YouTube searches are not configurable (uses yt-dlp defaults)
youtube = YoutubeProvider()

manager.add_provider(red)
manager.add_provider(youtube)
```

### Working with Release Details

```python
# Search
results = manager.search(query)

# Inspect results
for release in results:
    print(f"Title: {release.title}")
    print(f"Artist: {release.artist}")
    print(f"Source: {release.source_name}")
    print(f"Quality: {release.quality}")
    print(f"Size: {release.formatted_size}")
    print(f"Match Score: {release.match_score:.2%}")
    
    if release.seeders:
        print(f"Seeders: {release.seeders}")
    
    if release.view_count:
        print(f"Views: {release.formatted_views}")
    
    if release.target_file:
        print(f"Target File: {release.target_file}")
    
    print("---")
```

## Error Handling

### Common Exceptions

```python
from flacfetch.core.manager import FetchManager
from flacfetch.core.models import TrackQuery

manager = FetchManager()

try:
    # Search may raise exceptions for network errors, API errors, etc.
    results = manager.search(TrackQuery(artist="Artist", title="Title"))
    
except Exception as e:
    print(f"Search failed: {e}")
    results = []

# Download may raise exceptions
if results:
    best = manager.select_best(results)
    try:
        file_path = manager.download(best, "./downloads")
        print(f"Downloaded: {file_path}")
    except ValueError as e:
        # No downloader registered for this source
        print(f"Download error: {e}")
    except RuntimeError as e:
        # Download failed (network, disk space, etc.)
        print(f"Download failed: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")
```

### Provider Error Handling

Providers handle errors gracefully:

```python
# Network errors, API errors, and parsing errors are caught
# Empty list is returned on error
results = manager.search(query)  # Returns [] on error, never raises

# Check if any provider succeeded
if not results:
    print("No results found or all providers failed")
```

### Logging

```python
from flacfetch.core.log import setup_logging

# Enable verbose logging
setup_logging(verbose=True)  # DEBUG level

# Or regular logging
setup_logging(verbose=False)  # INFO level

# Now all operations will log details
manager.search(query)
# INFO: Searching RED for 'Artist - Title'...
# INFO: Found 5 results from RED
# ...
```

## Examples

### Example 1: Simple Search and Download

```python
from flacfetch.core.manager import FetchManager
from flacfetch.core.models import TrackQuery
from flacfetch.providers.youtube import YoutubeProvider

manager = FetchManager()
manager.add_provider(YoutubeProvider())

query = TrackQuery(artist="Seether", title="Tonight")
results = manager.search(query)

if results:
    best = manager.select_best(results)
    file_path = manager.download(best, "./music")
    print(f"Downloaded: {file_path}")
```

### Example 2: Multiple Providers with Priority

```python
import os
from flacfetch.core.manager import FetchManager
from flacfetch.core.models import TrackQuery
from flacfetch.providers.red import REDProvider
from flacfetch.providers.ops import OPSProvider
from flacfetch.providers.youtube import YoutubeProvider

manager = FetchManager()

# Add all providers (base_url required for private trackers)
manager.add_provider(REDProvider(
    api_key=os.environ["RED_API_KEY"],
    base_url=os.environ["RED_API_URL"]
))
manager.add_provider(OPSProvider(
    api_key=os.environ["OPS_API_KEY"],
    base_url=os.environ["OPS_API_URL"]
))
manager.add_provider(YoutubeProvider())

# Set priority: Try RED first, then OPS, finally YouTube
manager.set_provider_priority(["RED", "OPS", "YouTube"])

# Search (will try RED first, fallback to OPS, then YouTube)
results = manager.search(TrackQuery(artist="Artist", title="Title"))

best = manager.select_best(results)
if best:
    print(f"Best result from: {best.source_name}")
    file_path = manager.download(best, "./downloads")
```

### Example 3: Custom Filtering and Selection

```python
import os
from flacfetch.core.manager import FetchManager
from flacfetch.core.models import TrackQuery, MediaSource
from flacfetch.providers.red import REDProvider

manager = FetchManager()
manager.add_provider(REDProvider(
    api_key=os.environ["RED_API_KEY"],
    base_url=os.environ["RED_API_URL"]
))

query = TrackQuery(artist="Artist", title="Title")
results = manager.search(query)

# Custom filtering: Only CD rips, 24-bit if possible
cd_rips = [r for r in results 
           if r.quality.media == MediaSource.CD 
           and r.quality.is_lossless()]

if cd_rips:
    # Prefer 24-bit if available
    best = max(cd_rips, key=lambda r: (r.quality.bit_depth or 0, r.seeders or 0))
    print(f"Selected: {best.title} - {best.quality}")
    file_path = manager.download(best, "./downloads")
```

### Example 4: Batch Download with Error Handling

```python
import os
import csv
from pathlib import Path
from flacfetch.core.manager import FetchManager
from flacfetch.core.models import TrackQuery
from flacfetch.providers.red import REDProvider

# Setup
manager = FetchManager()
manager.add_provider(REDProvider(
    api_key=os.environ["RED_API_KEY"],
    base_url=os.environ["RED_API_URL"]
))

output_dir = Path("./downloads")
output_dir.mkdir(exist_ok=True)

# Load tracks from CSV
tracks = []
with open("tracks.csv") as f:
    reader = csv.DictReader(f)
    tracks = [(row["artist"], row["title"]) for row in reader]

# Download
successful = []
failed = []

for artist, title in tracks:
    query = TrackQuery(artist=artist, title=title)
    
    try:
        results = manager.search(query)
        if not results:
            failed.append((artist, title, "No results"))
            continue
        
        best = manager.select_best(results)
        file_path = manager.download(
            best, 
            str(output_dir),
            output_filename=f"{artist} - {title}"
        )
        
        successful.append((artist, title, file_path))
        print(f"✓ {artist} - {title}")
        
    except Exception as e:
        failed.append((artist, title, str(e)))
        print(f"✗ {artist} - {title}: {e}")

# Summary
print(f"\nSuccessful: {len(successful)}/{len(tracks)}")
print(f"Failed: {len(failed)}/{len(tracks)}")

# Save failed tracks
if failed:
    with open("failed.csv", "w") as f:
        writer = csv.writer(f)
        writer.writerow(["artist", "title", "error"])
        writer.writerows(failed)
```

### Example 5: Inspecting Release Details

```python
import os
from flacfetch.core.manager import FetchManager
from flacfetch.core.models import TrackQuery
from flacfetch.providers.red import REDProvider

manager = FetchManager()
manager.add_provider(REDProvider(
    api_key=os.environ["RED_API_KEY"],
    base_url=os.environ["RED_API_URL"]
))

query = TrackQuery(artist="Artist", title="Title")
results = manager.search(query)

# Detailed inspection
for i, release in enumerate(results, 1):
    print(f"\n=== Result {i} ===")
    print(f"Source: {release.source_name}")
    print(f"Title: {release.title}")
    print(f"Artist: {release.artist}")
    print(f"Quality: {release.quality}")
    print(f"Format: {release.quality.format.name}")
    
    if release.quality.is_lossless():
        print(f"Bit Depth: {release.quality.bit_depth or 'Unknown'}-bit")
        if release.quality.sample_rate:
            print(f"Sample Rate: {release.quality.sample_rate} Hz")
    else:
        print(f"Bitrate: {release.quality.bitrate or 'Unknown'} kbps")
    
    print(f"Media: {release.quality.media.name}")
    print(f"Size: {release.formatted_size}")
    print(f"Match Score: {release.match_score:.1%}")
    
    if release.year:
        print(f"Year: {release.year}")
    if release.label:
        print(f"Label: {release.label}")
    if release.seeders:
        print(f"Seeders: {release.seeders}")
    if release.target_file:
        print(f"Target File: {release.target_file}")
        print(f"File Size: {release.formatted_size}")
```

## Type Hints

flacfetch is fully type-hinted. Use a type checker like mypy for better development experience:

```python
from flacfetch.core.manager import FetchManager
from flacfetch.core.models import TrackQuery, Release

def download_track(manager: FetchManager, artist: str, title: str) -> str | None:
    """Download a track and return the file path."""
    query = TrackQuery(artist=artist, title=title)
    results: list[Release] = manager.search(query)
    
    best: Release | None = manager.select_best(results)
    if best:
        return manager.download(best, "./downloads")
    return None
```

## API Reference Summary

### Manager
- `FetchManager()` - Create manager instance
- `.add_provider(provider)` - Add search provider
- `.search(query)` - Search all providers
- `.select_best(releases)` - Select best release
- `.download(release, path, filename?)` - Download release
- `.set_provider_priority(list)` - Set search order
- `.enable_fallback_search(bool)` - Enable/disable fallback
- `.register_downloader(name, downloader)` - Register downloader
- `.set_default_downloader(downloader)` - Set default downloader

### Providers
- `REDProvider(api_key, base_url, cache_dir?)` - RED private tracker provider
- `OPSProvider(api_key, base_url, cache_dir?)` - OPS private tracker provider
- `YoutubeProvider()` - YouTube provider

### Models
- `TrackQuery(artist, title)` - Search query
- `Release(...)` - Search result with all metadata
- `Quality(format, bit_depth?, bitrate?, media)` - Audio quality
- `AudioFormat` - Enum: FLAC, MP3, AAC, OPUS, WAV, OTHER
- `MediaSource` - Enum: WEB, CD, VINYL, DVD, CASSETTE, OTHER

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development guidelines.

## Support

For issues, questions, or contributions, visit:
https://github.com/nomadkaraoke/flacfetch


