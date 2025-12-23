# Architecture & Implementation Plan

## 1. High-Level Architecture

The system will be designed using **Clean Architecture** principles to separate concerns between the core logic, external interfaces (CLI/Library), and infrastructure (APIs/Downloaders).

### Core Components

1.  **Orchestrator (`FetchManager`)**: The main entry point that coordinates searching, selection, and downloading.
2.  **Domain Models**:
    -   `TrackQuery`: Input data (Artist, Title).
    -   `Release`: Represents a finding (Source, Quality, Metadata, Download Link/Magnet, **Target File**).
    -   `Quality`: Value object for audio quality (Format, Bitrate, Source Media) with comparison logic.
3.  **Interfaces (Abstract Base Classes)**:
    -   `Provider`: Interface for searching (e.g., `REDProvider`, `OPSProvider`, `YoutubeProvider`).
    -   `Downloader`: Interface for retrieving content (e.g., `TorrentDownloader`, `HttpDownloader`).
    -   `UserInterface`: Interface for interaction (handling selection prompts).

### Component Diagram

```mermaid
graph TD
    CLI[CLI Adapter] --> Core
    Lib[Library User] --> Core
    
    subgraph Core
        FM[FetchManager]
        QS[QualitySorter]
        Handler[InteractionHandler]
    end
    
    FM --> Providers
    FM --> Downloaders
    
    subgraph Providers
        RED
        OPS
        YouTube
    end
    
    subgraph Downloaders
        LibTorrent
        YtDlp
        DirectHttp
    end
```

## 2. Detailed Design

### 2.1 Provider System
Each provider implements a `search(query: TrackQuery) -> List[Release]` method.
-   **REDProvider / OPSProvider**: 
    -   Uses `ajax.php?action=browse` with `artistname` and `filelist` parameters to find torrents containing the specific track.
    -   Requires `base_url` parameter (set via environment variable) in addition to API key.
    -   Parses the `fileList` field in the response to identify the specific file within the torrent that matches the requested song.
    -   Prioritizes "Album" releases (Type 1) and original release years when presenting options.
-   **PublicProviders**: Wrappers around `yt-dlp` or scraping logic for YouTube.

### 2.2 Quality & Selection Logic
-   **Quality Class**: Implements `__lt__`, `__eq__` to allow sorting.
    -   Hierarchy: `FLAC 24bit` > `FLAC 16bit` > `MP3 320` > `AAC` > `Other`.
-   **Auto-Selection**: `FetchManager` sorts results by `Quality` and picks the top.
-   **Interactive Selection**: `FetchManager` delegates to `InteractionHandler`.
    -   **CLI Implementation**: Prints list to stdout, reads index from stdin.
    -   **Library Implementation**: Accepts a callable/hook passed during initialization or method call.

### 2.3 Downloader System
-   **TorrentDownloader**: Uses `libtorrent`.
    -   **Selective Downloading**: Uses the `target_file` information from the `Release` to set file priorities. All files are set to priority 0 (skip) except the target song file, which is set to priority 7 (high).
    -   Manages session, adds magnet/torrent file, monitors progress, alerts on completion.
-   **HttpDownloader**: Standard HTTP GET or `yt-dlp` process.

## 3. Implementation Plan

### Phase 1: Core & Interfaces
-   Define `TrackQuery`, `Release`, `Quality` data classes.
-   Define `Provider` and `Downloader` abstract base classes (ABCs).
-   Implement `Quality` comparison logic.

### Phase 2: Private Tracker Provider Integration
-   Implement authentication (API Key + Base URL support via environment variables).
-   Implement `REDProvider.search` and `OPSProvider.search` using specific `artistname` and `filelist` filtering.
-   Parse `fileList` string (format: `filename{{{size}}}|||...`) to find target track.
-   Parse results into `Release` objects, including `target_file` metadata.

### Phase 3: BitTorrent Integration
-   Set up `libtorrent` session management.
-   Implement `TorrentDownloader` with selective file downloading support.
-   Ensure protocol compatibility.

### Phase 4: Public Providers
-   Implement `YoutubeProvider` using `yt-dlp`.
-   Implement `HttpDownloader`.

### Phase 5: CLI & Library API
-   Build `FetchManager` to tie it all together.
-   Implement CLI with separated `--artist` and `--title` arguments for precision.
-   Implement `ConsoleInteractionHandler` (CLI) and `CallbackInteractionHandler` (Library).

## 4. Testing Strategy

### 4.1 Unit Testing (`pytest`)
-   **Domain Logic**: Test `Quality` sorting and `FetchManager` flow (mocking providers).
-   **Providers**: Test API response parsing using mocked JSON responses (recorded from actual API calls or synthesized based on docs).
-   **Downloaders**: Mock `libtorrent` session and file system operations.

### 4.2 Integration Testing
-   Test the "Search -> Select -> Download" pipeline with a "MockProvider" and "MockDownloader" to ensure the architecture holds together without hitting real external services.

### 4.3 End-to-End Testing (Manual/Staging)
-   Run against real APIs (limited) to verify contract assumptions.

## 5. Technology Stack
-   **Language**: Python 3.10+
-   **HTTP Client**: `httpx` (async support) or `requests`.
-   **Torrent**: `python-libtorrent` (via system package or pip).
-   **CLI**: `argparse` (stdlib).
-   **Testing**: `pytest`, `pytest-mock`.
