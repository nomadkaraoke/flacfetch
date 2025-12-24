# flacfetch

[![PyPI version](https://badge.fury.io/py/flacfetch.svg)](https://pypi.org/project/flacfetch/)
[![Python Version](https://img.shields.io/pypi/pyversions/flacfetch.svg)](https://pypi.org/project/flacfetch/)
[![Tests](https://github.com/nomadkaraoke/flacfetch/workflows/Tests/badge.svg)](https://github.com/nomadkaraoke/flacfetch/actions/workflows/test.yml)
[![codecov](https://codecov.io/gh/nomadkaraoke/flacfetch/branch/main/graph/badge.svg)](https://codecov.io/gh/nomadkaraoke/flacfetch)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**flacfetch** is a Python tool designed to search for and download high-quality audio files from various sources. It is optimized for finding specific tracks (songs) across both private music trackers and public sources, with intelligent prioritization of "Official" and "Original" releases.

## Features

-   **Precise Track Search**:
    -   **Private Music Trackers**: RED and OPS (API integration). Uses advanced file list filtering to find specific songs within album torrents, downloading only the required track.
    -   **Streaming Services**: Spotify (via `librespot`, requires Premium account) - CD Quality FLAC (44.1kHz/16-bit).
    -   **Public Sources**: YouTube (via `yt-dlp`).
-   **Smart Prioritization**:
    -   **Official Sources**: Automatically prioritizes "Topic" channels and "Official Audio" on YouTube. Spotify results are always from official sources.
    -   **Quality Heuristics**: 
        -   **Trackers (RED/OPS)**: Prioritizes Lossless (FLAC) and healthy torrents (Seeders). Matches filename exactly to your query.
        -   **Spotify**: CD-quality FLAC (44.1kHz/16-bit) via librespot capture. Prioritizes by popularity.
        -   **YouTube**: Prioritizes newer uploads (Opus codec) over legacy uploads (AAC). Color-codes upload years to help you spot modern, high-quality streams (Green: 2020+, Yellow: 2015-2019, Red: <2015).
-   **Flexible Interaction**:
    -   **Interactive Mode**: Present search results to the user for manual selection with rich, color-coded metadata (Seeders, Views, Duration).
    -   **Automatic Mode**: Automatically select the highest ranked release.
-   **Smart Downloading**:
    -   **Selective BitTorrent**: Uses Transmission daemon to download *only* the specific file matching your search query from larger album torrents (saving bandwidth).
    -   **Direct Downloads**: Handles HTTP/Stream downloads for public sources.

## Requirements

-   Python 3.10+
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
flacfetch "Seether" "Tonight"
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
flacfetch -v "Seether" "Tonight"
```

**Configuration**

To use private music trackers, you must provide both an API Key and API URL:
```bash
# RED
export RED_API_KEY="your_api_key_here"
export RED_API_URL="your_tracker_url_here"
# OR
flacfetch "..." --red-key "your_key" --red-url "your_url"

# OPS
export OPS_API_KEY="your_api_key_here"
export OPS_API_URL="your_tracker_url_here"
# OR
flacfetch "..." --ops-key "your_key" --ops-url "your_url"
```

**Spotify Configuration** (Optional - requires Premium account)

Spotify provides CD-quality audio (44.1kHz/16-bit) captured via librespot and converted to FLAC. This uses the official Spotify Web API for authentication (OAuth) and librespot for audio capture.

**Prerequisites:**
- Spotify Premium account
- `librespot` binary: `brew install librespot` or `cargo install librespot`
- `ffmpeg` for audio conversion

**Setup:**
```bash
# 1. Install Spotify extra dependencies
pip install flacfetch[spotify]

# 2. Create a Spotify Developer App
# Go to: https://developer.spotify.com/dashboard
# Click "Create App"
# Set redirect URI to: http://127.0.0.1:8888/callback
# Note your Client ID and Client Secret

# 3. Set environment variables
export SPOTIPY_CLIENT_ID='your-client-id'
export SPOTIPY_CLIENT_SECRET='your-client-secret'
export SPOTIPY_REDIRECT_URI='http://127.0.0.1:8888/callback'

# 4. First run will open browser for OAuth login (token cached automatically)
flacfetch "Artist" "Title"

# Disable Spotify if needed
flacfetch "Artist" "Title" --no-spotify
```

**How it works:**
1. Uses Spotify Web API (via spotipy) for search and playback control
2. Starts librespot as a Spotify Connect device with OAuth token
3. Triggers playback via Web API, captures raw PCM via pipe backend
4. Converts PCM to FLAC using ffmpeg

**Note:** The redirect URI must use `127.0.0.1` (not `localhost`) as per Spotify's updated security requirements.

**Provider Priority**

When multiple providers are configured, flacfetch searches them in priority order. By default: **RED > OPS > Spotify > YouTube**

This means RED is searched first, and only if it returns no results will OPS be searched, then Spotify, then YouTube. This prioritizes lossless sources first, then high-quality streaming.

```bash
# Use default priority (RED > OPS > Spotify > YouTube)
export RED_API_KEY="..."
export RED_API_URL="..."
export OPS_API_KEY="..."
export OPS_API_URL="..."
flacfetch "Artist" "Title" --auto

# Custom priority (e.g., prefer Spotify over trackers)
flacfetch "Artist" "Title" --provider-priority "Spotify,RED,OPS,YouTube"

# Or via environment variable
export FLACFETCH_PROVIDER_PRIORITY="OPS,RED,Spotify,YouTube"
flacfetch "Artist" "Title" --auto

# Disable fallback (only search highest priority provider)
flacfetch "Artist" "Title" --auto --no-fallback
```

### Library Usage

**Quick Example:**

```python
from flacfetch.core.manager import FetchManager
from flacfetch.core.models import TrackQuery
from flacfetch.providers.red import REDProvider
from flacfetch.providers.ops import OPSProvider
from flacfetch.providers.spotify import SpotifyProvider  # Optional
from flacfetch.downloaders.spotify import SpotifyDownloader  # Optional

manager = FetchManager()
manager.add_provider(REDProvider(api_key="...", base_url="..."))
manager.add_provider(OPSProvider(api_key="...", base_url="..."))

# Spotify (requires SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, SPOTIPY_REDIRECT_URI env vars)
spotify_provider = SpotifyProvider()
manager.add_provider(spotify_provider)
manager.register_downloader("Spotify", SpotifyDownloader(provider=spotify_provider))

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

**For comprehensive library documentation**, including:
- Complete API reference for all classes and methods
- Data models and type hints
- Provider configuration options
- Advanced usage patterns (filtering, custom sorting, batch processing)
- Error handling best practices
- 5+ detailed examples

See **[LIBRARY.md](LIBRARY.md)** for full library API documentation.

## Architecture & Design

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed architecture, design choices, and implementation learnings.

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Legal Disclaimer

This tool is intended for use with content to which you have legal access. Users are responsible for complying with all applicable laws and terms of service for the supported providers.

## License

MIT License - see [LICENSE](LICENSE) file for details.
