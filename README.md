# flacfetch

**flacfetch** is a Python tool designed to search for and download high-quality audio files from various sources. It is optimized for finding specific tracks (songs) across both private trackers and public sources, with intelligent prioritization of "Official" and "Original" releases.

## Features

-   **Precise Track Search**:
    -   **Private BitTorrent Trackers**: Redacted (API integration). Uses advanced file list filtering to find specific songs within album torrents, downloading only the required track.
    -   **Public Sources**: YouTube (via `yt-dlp`).
-   **Smart Prioritization**:
    -   **Official Sources**: Automatically prioritizes "Topic" channels and "Official Audio" on YouTube.
    -   **Quality Heuristics**: 
        -   **Redacted**: Prioritizes Lossless (FLAC) and healthy torrents (Seeders). Matches filename exactly to your query.
        -   **YouTube**: Prioritizes newer uploads (Opus codec) over legacy uploads (AAC). Color-codes upload years to help you spot modern, high-quality streams (Green: 2020+, Yellow: 2015-2019, Red: <2015).
-   **Flexible Interaction**:
    -   **Interactive Mode**: Present search results to the user for manual selection with rich, color-coded metadata (Seeders, Views, Duration).
    -   **Automatic Mode**: Automatically select the highest ranked release.
-   **Smart Downloading**:
    -   **Selective BitTorrent**: Uses `libtorrent` to download *only* the specific file matching your search query from larger album torrents (saving bandwidth).
    -   **Direct Downloads**: Handles HTTP/Stream downloads for public sources.

## Requirements

-   Python 3.8+
-   `requests`
-   `yt-dlp`
-   **libtorrent** (Python bindings) - *Required for BitTorrent downloads* (Optional if only using YouTube)

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

## Architecture & Design

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed architecture, design choices, and implementation learnings.

## Legal Disclaimer

This tool is intended for use with content to which you have legal access. Users are responsible for complying with all applicable laws and terms of service for the supported providers.
