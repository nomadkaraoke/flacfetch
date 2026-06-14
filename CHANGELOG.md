# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.21.1] - 2026-06-14

### Fixed
- Secret-version prune now actually destroys stale versions. The flacfetch
  service account was granted `secretVersionAdder` but not `secretVersionManager`,
  so `destroy_secret_version` silently failed with PERMISSION_DENIED and ENABLED
  versions of `youtube-cookies` / `spotify-oauth-token` accumulated unbounded
  (a GCP cost regression). IAM role upgraded to `secretVersionManager`.
- The Spotify token health-check writeback (`_persist_spotify_token_to_secret_manager`)
  now prunes old versions too — previously it added a version without pruning,
  bypassing the cleanup the config endpoints already did.

### Changed
- Prune logic consolidated into `flacfetch/api/services/secret_manager_utils.py`
  so every secret write path prunes consistently.

## [0.19.2] - 2026-03-06

### Added
- Pushbullet success notifications when credential keeper refreshes YouTube cookies or Spotify tokens
- Controlled by `KEEPER_NOTIFY_ON_SUCCESS` env var (default: `true`), set to `false` to disable

### Changed
- Credential keeper failure notifications now use emoji prefixes for quick visual scanning

## [0.19.1] - 2026-03-06

### Fixed
- Fixed credential keeper Chrome launch failures due to missing system shared libraries (libnss3, libatk, etc.)
- Fixed "Opening in existing browser session" errors by cleaning up Chrome `SingletonLock` files before launch
- Fixed `wait_for_selector` timeouts under systemd by switching to polling with `query_selector` (Patchright bug)
- Fixed Google login check using heavy `myaccount.google.com` — now uses lightweight `accounts.google.com/`
- Fixed Spotify OAuth redirect capture using overly broad route interceptor — switched to `page.on("request")` event listener
- Fixed Spotify `page.goto` throwing `ERR_CONNECTION_REFUSED` on auto-approved localhost redirect
- Fixed initial refresh never triggering due to monotonic clock timing (`float("-inf")` instead of `0.0`)

## [0.19.0] - 2026-03-06

### Added
- Credential keeper: browser automation service that auto-renews YouTube cookies and Spotify OAuth tokens
- Uses Patchright (anti-detection Playwright fork) with persistent Chrome profile logged into Google
- Runs as systemd service alongside existing flacfetch API on the GCE VM
- YouTube cookies extracted in Netscape format and uploaded via flacfetch API every 8 hours
- Spotify OAuth flow via "Continue with Google" button, token refreshed every 12 hours
- Xvfb virtual display for headed browser mode (avoids headless detection)
- Browser profile persisted on data disk (survives VM restarts without re-login)
- Pushbullet notifications on credential refresh failures
- VM upgraded from e2-small to e2-medium (4GB RAM) for Chromium
- GCP secrets for Google account credentials (`flacfetch-account-email`, `flacfetch-account-password`)

## [0.18.3] - 2026-03-05

### Fixed
- Fixed API server becoming completely unresponsive during long-running downloads
- Spotify/torrent downloads using synchronous `time.sleep()` loops were blocking the uvicorn event loop
- Health endpoint and all other requests now remain responsive during downloads
- Downloads now execute in a thread pool via `asyncio.run_in_executor()`

## [0.18.0] - 2026-01-25

### Fixed
- Fixed tracker search failing for tracks with `?` and other Sphinx wildcard characters
- Song titles like "Is It Any Wonder?" now return tracker results instead of only YouTube
- Complete sanitization of all 24 Sphinx special characters based on Gazelle's `sph_escape_string()`

### Changed
- Refactored RED and OPS providers to inherit from new `GazelleProvider` base class
- Reduced code duplication by extracting shared Gazelle tracker functionality
- Improved code organization with single source of truth for Sphinx query sanitization

### Added
- `GazelleProvider` base class with shared functionality for Gazelle-based trackers
- Comprehensive test suite for Sphinx query sanitization (68 test cases)
- Tests for international character preservation (CJK, Cyrillic, Arabic, Hebrew, etc.)

## [0.8.2] - 2025-12-24

### Added
- Search results now show per-provider stats (e.g., "RED: 0 | OPS: 0 | YouTube: 5")
- Makes it explicit when lossless providers return no results

## [0.8.1] - 2025-12-24

### Added
- Debug endpoint `GET /debug/providers` to help diagnose provider initialization and API connectivity issues

## [0.4.0] - 2025-12-22

### Changed
- **BREAKING**: Renamed provider to "RED" and removed hardcoded API URLs
- **BREAKING**: Both RED and OPS providers now require `base_url` parameter in constructor
- Private tracker API URLs must now be set via environment variables (`RED_API_URL`, `OPS_API_URL`)
- This change improves security and respects privacy of private trackers
- Environment variable renamed to `RED_API_KEY`
- Default provider priority updated to: RED > OPS > YouTube

## [0.3.4] - 2025-11-26

### Fixed
- Fixed RED and OPS filelist search failing for tracks with special characters (colons, parentheses, etc.)
- Sphinx search engine treats certain characters as operators, now sanitizing queries to remove: `:/()\[\]!,.;`
- Tracks like "Flight 717: Going To Denmark" now search correctly
- Added comprehensive tests for filelist query sanitization

## [0.3.3] - 2025-11-25

### Fixed
- Unified release workflow now works correctly

## [0.3.2] - 2025-11-25

### Changed
- Unified release workflow - single automated pipeline from version bump to PyPI
- Removed separate auto-tag and release workflows
- Simplified release process: just bump version in pyproject.toml and push

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
- Support for OPS tracker with API integration
- `OPS_API_KEY` environment variable and `--ops-key` CLI option
- OPS provider with the same features as RED (lossless FLAC, file matching, caching)
- Comprehensive test suite for OPS provider
- **Provider Priority System**: Configure which trackers to search first
  - `--provider-priority` CLI flag for custom priority order
  - `FLACFETCH_PROVIDER_PRIORITY` environment variable
  - `--no-fallback` flag to only search highest priority provider
  - Default priority: RED > OPS > YouTube (conserves buffer on limited trackers)
  - Intelligent fallback: automatically tries lower priority providers if higher ones return no results

### Changed
- **BREAKING**: Dropped Python 3.8 and 3.9 support, minimum version is now Python 3.10
- Modernized type hints to use built-in generic types (`list`, `dict`, `set`, `tuple` instead of `typing.List`, etc.)
- Updated CLI help text to mention both RED and OPS trackers
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
- Support for private music trackers with API integration
- Support for YouTube with yt-dlp integration
- Intelligent release matching and prioritization
- Interactive and automatic selection modes
- Selective torrent file downloading via Transmission
- Color-coded CLI output with metadata display
- Quality comparison (lossless vs lossy, bitrate, etc.)
- Flexible output options (directory, rename, custom filename)

