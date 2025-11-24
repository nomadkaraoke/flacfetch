# flacfetch

[![PyPI version](https://badge.fury.io/py/flacfetch.svg)](https://pypi.org/project/flacfetch/)
[![Python Version](https://img.shields.io/pypi/pyversions/flacfetch.svg)](https://pypi.org/project/flacfetch/)
[![Tests](https://github.com/nomadkaraoke/flacfetch/workflows/Tests/badge.svg)](https://github.com/nomadkaraoke/flacfetch/actions/workflows/test.yml)
[![codecov](https://codecov.io/gh/nomadkaraoke/flacfetch/branch/main/graph/badge.svg)](https://codecov.io/gh/nomadkaraoke/flacfetch)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**flacfetch** is a Python tool designed to search for and download high-quality audio files from various sources. It is optimized for finding specific tracks (songs) across both private music trackers and public sources, with intelligent prioritization of "Official" and "Original" releases.

## Features

-   **Precise Track Search**:
    -   **Private Music Trackers**: Redacted and OPS (API integration). Uses advanced file list filtering to find specific songs within album torrents, downloading only the required track.
    -   **Public Sources**: YouTube (via `yt-dlp`).
-   **Smart Prioritization**:
    -   **Official Sources**: Automatically prioritizes "Topic" channels and "Official Audio" on YouTube.
    -   **Quality Heuristics**: 
        -   **Trackers (Redacted/OPS)**: Prioritizes Lossless (FLAC) and healthy torrents (Seeders). Matches filename exactly to your query.
        -   **YouTube**: Prioritizes newer uploads (Opus codec) over legacy uploads (AAC). Color-codes upload years to help you spot modern, high-quality streams (Green: 2020+, Yellow: 2015-2019, Red: <2015).
-   **Flexible Interaction**:
    -   **Interactive Mode**: Present search results to the user for manual selection with rich, color-coded metadata (Seeders, Views, Duration).
    -   **Automatic Mode**: Automatically select the highest ranked release.
-   **Smart Downloading**:
    -   **Selective BitTorrent**: Uses Transmission daemon to download *only* the specific file matching your search query from larger album torrents (saving bandwidth).
    -   **Direct Downloads**: Handles HTTP/Stream downloads for public sources.

## Requirements

-   Python 3.8+
-   `requests`
-   `yt-dlp`
-   `transmission-rpc`
-   **Transmission** (daemon) - *Required for BitTorrent downloads* (Optional if only using YouTube)

### Installing Transmission

Transmission is a lightweight, cross-platform BitTorrent client with RPC support.

-   **Ubuntu/Debian**: `sudo apt install transmission-daemon`
-   **macOS**: `brew install transmission-cli`
-   **Windows**: Download from [transmissionbt.com](https://transmissionbt.com)

flacfetch will automatically start the transmission daemon if it's not running.

## Installation

### From PyPI (Recommended)

```bash
pip install flacfetch
```

### From Source

```bash
git clone https://github.com/nomadkaraoke/flacfetch.git
cd flacfetch
pip install .
```

### Development Installation

```bash
git clone https://github.com/nomadkaraoke/flacfetch.git
cd flacfetch
pip install -e ".[dev]"
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

**Output Options**
```bash
# Specify output directory
flacfetch --artist "Seether" --title "Tonight" -o ~/Music

# Auto-rename to "ARTIST - TITLE.ext"
flacfetch --artist "Seether" --title "Tonight" --rename

# Specify exact filename
flacfetch --artist "Seether" --title "Tonight" --filename "my_song"

# Combine options
flacfetch --artist "Seether" --title "Tonight" -o ~/Music --rename
```

**Verbose Logging**
```bash
flacfetch -v "Seether - Tonight"
```

**Configuration**

To use private music trackers, you must provide an API Key:
```bash
# Redacted
export REDACTED_API_KEY="your_api_key_here"
# OR
flacfetch "..." --redacted-key "your_key"

# OPS
export OPS_API_KEY="your_api_key_here"
# OR
flacfetch "..." --ops-key "your_key"
```

**Provider Priority**

When multiple providers are configured, flacfetch searches them in priority order. By default: **Redacted > OPS > YouTube**

This means Redacted is searched first, and only if it returns no results will OPS be searched, then YouTube. This is useful for conserving buffer on trackers with stricter limits.

```bash
# Use default priority (Redacted > OPS > YouTube)
export REDACTED_API_KEY="..."
export OPS_API_KEY="..."
flacfetch "Artist" "Title" --auto

# Custom priority
flacfetch "Artist" "Title" --provider-priority "OPS,Redacted,YouTube"

# Or via environment variable
export FLACFETCH_PROVIDER_PRIORITY="OPS,Redacted,YouTube"
flacfetch "Artist" "Title" --auto

# Disable fallback (only search highest priority provider)
flacfetch "Artist" "Title" --auto --no-fallback
```

### Library Usage

```python
from flacfetch.core.manager import FetchManager
from flacfetch.core.models import TrackQuery
from flacfetch.providers.redacted import RedactedProvider
from flacfetch.providers.ops import OPSProvider

manager = FetchManager()
manager.add_provider(RedactedProvider(api_key="..."))
manager.add_provider(OPSProvider(api_key="..."))

# Search for a specific track
results = manager.search(TrackQuery(artist="Seether", title="Tonight"))
best = manager.select_best(results)

if best:
    # Download returns the path to the downloaded file
    file_path = manager.download(
        best, 
        output_path="./downloads",
        output_filename="Seether - Tonight"  # Optional: custom filename
    )
    print(f"Downloaded to: {file_path}")
```

## Architecture & Design

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed architecture, design choices, and implementation learnings.

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Legal Disclaimer

This tool is intended for use with content to which you have legal access. Users are responsible for complying with all applicable laws and terms of service for the supported providers.

## License

MIT License - see [LICENSE](LICENSE) file for details.
