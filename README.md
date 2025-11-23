# flacfetch

**flacfetch** is a Python tool designed to search for and download high-quality audio files from various sources. It can be used as a command-line interface (CLI) or as a library within other Python projects.

## Features

-   **Multi-Provider Search**: Search for songs (Artist + Title) across multiple platforms:
    -   **Private BitTorrent Trackers**: Redacted (API integration), Orpheus (planned).
    -   **Public Sources**: Bandcamp, Soundcloud, YouTube (via `yt-dlp`).
-   **Flexible Interaction**:
    -   **Interactive Mode**: Present search results to the user for manual selection. Supports shell input for CLI and callbacks/hooks for library usage.
    -   **Automatic Mode**: Automatically select the highest quality available based on a predefined hierarchy (e.g., 24-bit FLAC WEB > 16-bit FLAC CD > Lossy).
-   **Integrated Downloading**:
    -   **BitTorrent Client**: Built-in support for downloading from private trackers using `libtorrent` (compatible with authorized client lists).
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
git clone https://github.com/yourusername/flacfetch.git
cd flacfetch
pip install .
```

## Usage

### CLI Usage

```bash
# Interactive search and download
flacfetch "Daft Punk - Get Lucky"

# Auto-download highest quality
flacfetch --auto "Daft Punk - Get Lucky"

# With Redacted API Key
flacfetch "Daft Punk - Get Lucky" --redacted-key YOUR_KEY
# Or set env var REDACTED_API_KEY
```

### Library Usage

```python
from flacfetch.core.manager import FetchManager
from flacfetch.core.models import TrackQuery
from flacfetch.providers.redacted import RedactedProvider

manager = FetchManager()
manager.add_provider(RedactedProvider(api_key="..."))

results = manager.search(TrackQuery(artist="Daft Punk", title="Get Lucky"))
best = manager.select_best(results)

if best:
    manager.download(best, output_path="./downloads")
```

## Architecture

See [PLAN.md](PLAN.md) for detailed architecture and implementation notes.

## Legal Disclaimer

This tool is intended for use with content to which you have legal access. Users are responsible for complying with all applicable laws and terms of service for the supported providers.
