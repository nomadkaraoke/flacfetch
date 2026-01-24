# Spotify Provider for flacfetch

The Spotify provider enables high-quality audio capture from Spotify Premium accounts. Unlike YouTube (which re-encodes audio), Spotify provides 320kbps Vorbis audio that is captured and converted to lossless FLAC format.

## Architecture Overview

The Spotify provider uses a unique approach combining three components:

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   spotipy       │───▶│   librespot     │───▶│    ffmpeg       │
│  (Web API)      │    │  (Audio Capture)│    │  (Conversion)   │
└─────────────────┘    └─────────────────┘    └─────────────────┘
        │                      │                      │
        ▼                      ▼                      ▼
   Search tracks         Capture raw PCM        Convert to FLAC
   Control playback      44.1kHz/16-bit         Lossless output
   OAuth tokens          via pipe backend
```

### Why This Approach?

1. **spotipy (Spotify Web API)**: Official, stable API for searching tracks and controlling playback. Uses standard OAuth2 authentication.

2. **librespot**: Open-source Spotify client that can output raw PCM audio via its pipe backend. Authenticates using OAuth tokens from spotipy.

3. **ffmpeg**: Converts the raw PCM capture to FLAC format.

This architecture bypasses Spotify's blocked Mercury API (which broke tools like zotify) by using the officially supported Web API for control and librespot only for audio capture.

## Requirements

### Spotify Premium Account
A Spotify Premium subscription is **required**. Free accounts cannot stream high-quality audio.

### Spotify Developer App
You need to create a Spotify Developer application to get API credentials:

1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Click "Create App"
3. Fill in:
   - App Name: `flacfetch` (or any name)
   - App Description: `Audio fetching tool`
   - Redirect URI: `http://127.0.0.1:8888/callback`
4. Check the "Web API" checkbox
5. Save and note your **Client ID** and **Client Secret**

### Software Dependencies

- **librespot**: Spotify Connect receiver
  ```bash
  # macOS
  brew install librespot
  
  # Linux (Debian/Ubuntu) - use pre-built binary from GCS (flacfetch infra)
  gsutil cp gs://karaoke-gen-storage-nomadkaraoke/binaries/librespot-0.8.0-linux-x86_64 /usr/local/bin/librespot
  chmod +x /usr/local/bin/librespot
  
  # Or download from GitHub releases
  wget https://github.com/librespot-org/librespot/releases/download/v0.5.0/librespot-linux-x86_64.tar.gz
  tar -xzf librespot-linux-x86_64.tar.gz
  sudo mv librespot /usr/local/bin/
  
  # From source (slow ~30min, requires Rust)
  cargo install librespot --locked
  ```

- **ffmpeg**: Audio conversion
  ```bash
  # macOS
  brew install ffmpeg
  
  # Linux (Debian/Ubuntu)
  sudo apt-get install ffmpeg
  ```

- **spotipy**: Python Spotify Web API client
  ```bash
  pip install flacfetch[spotify]
  # or
  pip install spotipy>=2.25.1
  ```

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SPOTIPY_CLIENT_ID` | Yes | Spotify Developer App Client ID |
| `SPOTIPY_CLIENT_SECRET` | Yes | Spotify Developer App Client Secret |
| `SPOTIPY_REDIRECT_URI` | Yes | OAuth redirect URI (default: `http://127.0.0.1:8888/callback`) |

### Local Setup

```bash
export SPOTIPY_CLIENT_ID="your-client-id"
export SPOTIPY_CLIENT_SECRET="your-client-secret"
export SPOTIPY_REDIRECT_URI="http://127.0.0.1:8888/callback"
```

### First-Time OAuth Authentication

The first time you use the Spotify provider, it will open a browser for OAuth login:

```bash
flacfetch "artist - song title"
```

1. A browser window opens to Spotify login
2. Log in with your Spotify Premium account
3. Authorize the application
4. You'll be redirected to `localhost:8888/callback`
5. The token is cached at `.cache` in the current directory

**Important**: The OAuth token includes a refresh token, so subsequent runs will automatically refresh the access token without requiring browser login.

## Infrastructure Setup (GCP)

For deploying flacfetch with Spotify support on Google Cloud:

### 1. GCP Secret Manager

Create secrets for Spotify credentials:

```bash
# Create secrets
gcloud secrets create spotipy-client-id --replication-policy="automatic"
gcloud secrets create spotipy-client-secret --replication-policy="automatic"

# Add values
echo -n "YOUR_CLIENT_ID" | gcloud secrets versions add spotipy-client-id --data-file=-
echo -n "YOUR_CLIENT_SECRET" | gcloud secrets versions add spotipy-client-secret --data-file=-
```

### 2. VM Configuration

The infrastructure Pulumi code automatically:
- Creates the secrets in Secret Manager
- Installs librespot on the VM
- Configures the systemd service with Spotify environment variables
- Installs flacfetch with `[api,spotify]` extras

### 3. OAuth Token for Headless Server

Since the server is headless, you need to generate the OAuth token locally and upload it to the server.

#### Option A: Using flacfetch-remote (Recommended)

The easiest way is to use the `flacfetch-remote fix` command, which handles token generation and upload automatically:

```bash
# This will:
# 1. Generate token locally (opens browser for OAuth)
# 2. Upload token via API (takes effect immediately, no restart needed)
# 3. Update GCP Secret Manager for persistence
flacfetch-remote fix
```

#### Option B: Using the API Directly

You can also upload tokens directly via the API for hot-reload without server restart:

```bash
# 1. Generate token locally (opens browser)
export SPOTIPY_CLIENT_ID="your-client-id"
export SPOTIPY_CLIENT_SECRET="your-client-secret"
export SPOTIPY_REDIRECT_URI="http://127.0.0.1:8888/callback"

python -c "
import spotipy
from spotipy.oauth2 import SpotifyOAuth
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    scope='user-read-playback-state user-modify-playback-state streaming'
))
print('Token generated successfully!')
print(sp.me()['display_name'])
"

# 2. Upload token via API (takes effect immediately)
curl -X POST http://SERVER:8080/config/spotify-token \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"token\": $(cat ~/.cache-spotipy)}"

# 3. Verify credentials
curl http://SERVER:8080/credentials/check -H "X-API-Key: $API_KEY"
```

The API upload automatically:
- Validates token format (JSON, required fields, scopes)
- Writes token to `/opt/flacfetch/.cache`
- Invalidates the SpotifyProvider cache (hot-reload)
- Updates GCP Secret Manager for persistence

#### Option C: Manual File Copy (Legacy)

For manual file copying (requires server restart):

```bash
# 1. Generate token locally (as above)
# 2. Copy token to server
gcloud compute scp ~/.cache-spotipy flacfetch-service:/opt/flacfetch/.cache --zone us-central1-a

# 3. Set ownership
gcloud compute ssh flacfetch-service --zone us-central1-a --command "sudo chown root:root /opt/flacfetch/.cache"

# 4. Restart service (required for file copy method)
gcloud compute ssh flacfetch-service --zone us-central1-a --command "sudo systemctl restart flacfetch"
```

### 4. Systemd Service

The flacfetch systemd service includes Spotify environment variables:

```ini
[Service]
Environment="SPOTIPY_CLIENT_ID=${SPOTIPY_CLIENT_ID}"
Environment="SPOTIPY_CLIENT_SECRET=${SPOTIPY_CLIENT_SECRET}"
Environment="SPOTIPY_REDIRECT_URI=http://127.0.0.1:8888/callback"
```

## How Downloads Work

1. **Search**: Uses Spotify Web API via spotipy to search for tracks
2. **Start librespot**: Launches librespot process with pipe backend, outputting raw PCM to a file
3. **Authenticate librespot**: Passes OAuth access token via `LIBRESPOT_ACCESS_TOKEN` environment variable
4. **Control playback**: Uses Web API to transfer playback to the librespot device and play the track
5. **Capture audio**: librespot buffers the entire track quickly (faster than real-time)
6. **Convert**: ffmpeg converts raw PCM (44.1kHz/16-bit stereo) to FLAC
7. **Cleanup**: Temporary PCM file is deleted

### Audio Quality

| Stage | Format | Quality |
|-------|--------|---------|
| Spotify Stream | Vorbis | 320kbps |
| librespot Capture | PCM | 44.1kHz/16-bit |
| Final Output | FLAC | Lossless |

Note: While the source is 320kbps Vorbis (lossy), it's only compressed once. Converting to FLAC preserves this quality without additional lossy compression.

## Troubleshooting

### "librespot not found"

Install librespot:
```bash
# macOS
brew install librespot

# Linux - download pre-built binary
wget https://github.com/librespot-org/librespot/releases/latest/download/librespot
chmod +x librespot
sudo mv librespot /usr/local/bin/
```

### "SpotifyProvider not configured"

Ensure environment variables are set:
```bash
echo $SPOTIPY_CLIENT_ID
echo $SPOTIPY_CLIENT_SECRET
```

### "Device not found" / "Transfer playback failed"

This usually means:
1. librespot failed to start (check logs)
2. OAuth token expired (delete `.cache` and re-authenticate)
3. Another device is actively playing (pause other devices)

Check librespot logs:
```bash
# The downloader creates a log file in /tmp
cat /tmp/librespot-*.log
```

### "Token refresh failed"

For server deployments, use the hot-reload API:
```bash
# Upload fresh token via API (no restart needed)
flacfetch-remote fix

# Or verify current status
curl http://SERVER:8080/config/spotify-token/status -H "X-API-Key: $API_KEY"
```

For local development, delete the cached token and re-authenticate:
```bash
rm ~/.cache-spotipy
flacfetch "test query"  # Will prompt for OAuth
```

### "403 Forbidden" errors

This may indicate:
1. Spotify Premium subscription lapsed
2. Account was flagged (rare)
3. Too many rapid requests (implement rate limiting)

### ffmpeg conversion errors

Ensure ffmpeg is installed and in PATH:
```bash
ffmpeg -version
```

### Slow downloads

librespot should buffer the entire track in a few seconds. If it's slow:
1. Check network connectivity
2. Ensure no other Spotify clients are competing
3. Check CPU usage (librespot is lightweight but ffmpeg uses CPU for FLAC encoding)

## Security Considerations

1. **Credentials**: Never commit Spotify credentials to git. Use environment variables or secret managers.

2. **OAuth Tokens**: The `.cache` file contains OAuth tokens. Treat it as sensitive:
   ```bash
   chmod 600 .cache
   ```

3. **librespot Authentication**: We pass the OAuth token via `LIBRESPOT_ACCESS_TOKEN` environment variable rather than command-line arguments to prevent exposure in process listings.

## Limitations

1. **Premium Required**: Only works with Spotify Premium accounts
2. **Single Track**: Downloads one track at a time (album/playlist support via sequential downloads)
3. **No DRM Content**: Some Spotify content may have additional restrictions
4. **Rate Limits**: Spotify Web API has rate limits; avoid rapid successive requests

## Provider Priority

In flacfetch's default priority order:

```
RED > OPS > Spotify > YouTube
```

This means:
- Lossless sources (RED, OPS) are preferred
- Spotify (320kbps source → FLAC) is preferred over YouTube
- YouTube is the fallback for content not found elsewhere

## Example Usage

```bash
# Search and download
flacfetch "Daft Punk - Get Lucky"

# Disable other providers to force Spotify
flacfetch "Daft Punk - Get Lucky" --no-red --no-ops --no-youtube

# Check available providers
flacfetch --help
```

## Files

| File | Purpose |
|------|---------|
| `flacfetch/providers/spotify.py` | SpotifyProvider class - search via Web API |
| `flacfetch/downloaders/spotify.py` | SpotifyDownloader class - audio capture |
| `flacfetch/core/config.py` | Centralized credential paths |
| `flacfetch/api/routes/config.py` | Token upload API endpoints |
| `tests/test_spotify.py` | Unit tests |
| `tests/test_api_config.py` | API config tests |
| `.cache` or `~/.cache-spotipy` | OAuth token cache (auto-generated) |

