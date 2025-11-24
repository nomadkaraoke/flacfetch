# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.1] - 2025-11-24

### Added
- Comprehensive test suite with 87 tests covering core functionality
- Tests for provider error handling and edge cases
- Tests for manager sorting logic (release type, seeders, quality, year, YouTube channel matching)
- Tests for downloader registration and usage
- Tests for model formatting and string representations
- Tests for logging configuration
- Test coverage improved from 56% to 69%

### Fixed
- Fixed linting errors (whitespace, bare except, unused variables)
- Updated ruff ignore rules for code style preferences

## [0.3.0] - 2025-11-24

### Added
- Support for OPS (orpheus.network) tracker with API integration
- `OPS_API_KEY` environment variable and `--ops-key` CLI option
- OPS provider with the same features as Redacted (lossless FLAC, file matching, caching)
- Comprehensive test suite for OPS provider
- **Provider Priority System**: Configure which trackers to search first
  - `--provider-priority` CLI flag for custom priority order
  - `FLACFETCH_PROVIDER_PRIORITY` environment variable
  - `--no-fallback` flag to only search highest priority provider
  - Default priority: Redacted > OPS > YouTube (conserves buffer on limited trackers)
  - Intelligent fallback: automatically tries lower priority providers if higher ones return no results

### Changed
- **BREAKING**: Dropped Python 3.8 and 3.9 support, minimum version is now Python 3.10
- Modernized type hints to use built-in generic types (`list`, `dict`, `set`, `tuple` instead of `typing.List`, etc.)
- Updated CLI help text to mention both Redacted and OPS trackers
- Updated README to document OPS support and provider priority system
- FetchManager now searches providers in priority order with configurable fallback behavior

## [0.2.0] - 2024-11-24

### Added
- Modern `pyproject.toml` build system configuration
- GitHub Actions CI/CD workflows:
  - Automated testing across Python 3.8-3.12 and multiple OS platforms
  - Automated PyPI publishing on release
  - CodeQL security scanning
  - Automated GitHub release creation with release notes
- Code coverage reporting with Codecov integration
- Development tools configuration (ruff, mypy, pytest)
- Comprehensive contributing guidelines (CONTRIBUTING.md)
- Updated README with badges and improved installation instructions
- Version management in `__init__.py`

### Changed
- Improved CLI help text with better organization and examples
- Simplified CLI argument parsing (removed "Artist - Title" format, now requires separate args)
- Better error messages and user guidance throughout CLI
- Enhanced release display with lossless/lossy indicators
- Moved file information to end of release lines for better readability
- Strip track numbers from displayed filenames
- YouTube results now prioritize channels matching the artist name

### Fixed
- Release sorting now properly prioritizes matching artist channels for YouTube results
- Filename display improvements with quotes and cleaner formatting

## [0.1.0] - Initial Release

### Added
- Core functionality for searching and downloading high-quality audio
- Support for Redacted (private tracker) with API integration
- Support for YouTube with yt-dlp integration
- Intelligent release matching and prioritization
- Interactive and automatic selection modes
- Selective torrent file downloading via Transmission
- Color-coded CLI output with metadata display
- Quality comparison (lossless vs lossy, bitrate, etc.)
- Flexible output options (directory, rename, custom filename)

