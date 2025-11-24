# flacfetch

**flacfetch** is a Python tool designed to search for and download high-quality audio files from various sources. It is optimized for finding specific tracks (songs) across both private trackers and public sources.

## Features

-   **Precise Track Search**:
    -   **Private BitTorrent Trackers**: Redacted (API integration). Uses advanced file list filtering to find specific songs within album torrents, downloading only the required track.
    -   **Public Sources**: YouTube (via `yt-dlp`).
-   **Flexible Interaction**:
    -   **Interactive Mode**: Present search results to the user for manual selection. Supports shell input for CLI and callbacks/hooks for library usage.
    -   **Automatic Mode**: Automatically select the highest quality available based on a predefined hierarchy (e.g., 24-bit FLAC WEB > 16-bit FLAC CD > Lossy).
-   **Smart Downloading**:
    -   **Selective BitTorrent**: Uses `libtorrent` to download *only* the specific file matching your search query from larger album torrents.
    -   **Direct Downloads**: Handles HTTP downloads for public sources.
-   **Quality Prioritization**: Intelligent sorting and selection logic to ensure the best audio fidelity.

## Requirements

-   Python 3.8+
-   `requests`
-   `yt-dlp`
-   **libtorrent** (Python bindings) - *Required for BitTorrent downloads*

### Installing libtorrent

`libtorrent` is a C++ library with Python bindings. It is recommended to install it via your system package manager.

-   **Ubuntu/Debian**: `sudo apt install python3-libtorrent`
-   **macOS**: `brew install libtorrent-rasterbar` (Ensure bindings are in your PYTHONPATH)
-   **Windows**: Check [libtorrent.org](https://libtorrent.org) or `pip install libtorrent` (unofficial wheels may exist).

## Installation

```bash
git clone https://github.com/nomadkaraoke/flacfetch.git
cd flacfetch
pip install .
```

## Usage

### CLI Usage

**Standard Search (Artist - Title)**
```bash
flacfetch "Seether - Tonight"
```

**Explicit Arguments (Recommended for precision)**
```bash
flacfetch --artist "Seether" --title "Tonight"
```

**Auto-download Highest Quality**
```bash
flacfetch --auto --artist "Seether" --title "Tonight"
```

**Verbose Logging**
```bash
flacfetch -v "Seether - Tonight"
```

**Configuration**
To use private trackers (Redacted), you must provide an API Key:
```bash
export REDACTED_API_KEY="your_api_key_here"
# OR
flacfetch "..." --redacted-key "your_key"
```

### Library Usage

```python
from flacfetch.core.manager import FetchManager
from flacfetch.core.models import TrackQuery
from flacfetch.providers.redacted import RedactedProvider

manager = FetchManager()
manager.add_provider(RedactedProvider(api_key="..."))

# Search for a specific track
results = manager.search(TrackQuery(artist="Seether", title="Tonight"))
best = manager.select_best(results)

if best:
    # This will download ONLY the specific track file if it's a torrent
    manager.download(best, output_path="./downloads")
```

## Architecture

See [PLAN.md](PLAN.md) for detailed architecture and implementation notes.

## Legal Disclaimer

This tool is intended for use with content to which you have legal access. Users are responsible for complying with all applicable laws and terms of service for the supported providers.
