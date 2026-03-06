"""
Pulumi infrastructure for Flacfetch audio download service on Google Cloud Platform.

This manages all GCP resources for the flacfetch service:
- Compute Engine VM for running Transmission + flacfetch API
- Persistent disk for torrent data (survives VM deletion)
- Static IP address (for tracker whitelist)
- Service account with appropriate IAM permissions
- Firewall rules for BitTorrent and API access
- Secret Manager secrets for API keys

The key design principle is that torrent data is stored on a SEPARATE persistent disk
that has autoDelete=false, ensuring data survives VM redeployments.
"""
import pulumi
import pulumi_gcp as gcp
from pulumi_gcp import compute, secretmanager, serviceaccount

# Get the current GCP project
project = gcp.organizations.get_project()
project_id = project.project_id
project_number = project.number

# Configuration
config = pulumi.Config()
zone = "us-central1-a"
region = "us-central1"

# =============================================================================
# Static IP Address
# =============================================================================
# Static IP for flacfetch (required for tracker account whitelist)

flacfetch_ip = compute.Address(
    "flacfetch-static-ip",
    name="flacfetch-static-ip",
    region=region,
    address_type="EXTERNAL",
    description="Static IP for flacfetch service (torrent downloads)",
)

# =============================================================================
# Service Account
# =============================================================================

flacfetch_sa = serviceaccount.Account(
    "flacfetch-sa",
    account_id="flacfetch-service",
    display_name="Flacfetch Service Account",
    description="Service account for flacfetch torrent/audio download VM",
)

# Grant Secret Manager access (to read API keys)
flacfetch_secrets_iam = gcp.projects.IAMMember(
    "flacfetch-secrets-access",
    project=project_id,
    role="roles/secretmanager.secretAccessor",
    member=flacfetch_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant GCS write permissions (to upload downloaded files)
# Uses the shared karaoke-gen bucket
bucket_name = f"karaoke-gen-storage-{project_id}"

flacfetch_storage_writer = gcp.storage.BucketIAMMember(
    "flacfetch-storage-writer",
    bucket=bucket_name,
    role="roles/storage.objectCreator",
    member=flacfetch_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

flacfetch_storage_reader = gcp.storage.BucketIAMMember(
    "flacfetch-storage-reader",
    bucket=bucket_name,
    role="roles/storage.objectViewer",
    member=flacfetch_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# =============================================================================
# Secrets
# =============================================================================
# Note: flacfetch-api-key and flacfetch-api-url are managed by karaoke-gen since
# the backend needs them to communicate with flacfetch. All other secrets below
# are flacfetch-specific.

# RED tracker secrets (for private music tracker access)
red_api_key_secret = secretmanager.Secret(
    "red-api-key",
    secret_id="red-api-key",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

red_api_url_secret = secretmanager.Secret(
    "red-api-url",
    secret_id="red-api-url",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# OPS tracker secrets (for private music tracker access)
ops_api_key_secret = secretmanager.Secret(
    "ops-api-key",
    secret_id="ops-api-key",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

ops_api_url_secret = secretmanager.Secret(
    "ops-api-url",
    secret_id="ops-api-url",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# Spotify secrets (for Spotify Premium audio capture)
spotipy_client_id_secret = secretmanager.Secret(
    "spotipy-client-id",
    secret_id="spotipy-client-id",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

spotipy_client_secret_secret = secretmanager.Secret(
    "spotipy-client-secret",
    secret_id="spotipy-client-secret",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# YouTube cookies secret (for authenticated downloads when required)
youtube_cookies_secret = secretmanager.Secret(
    "youtube-cookies",
    secret_id="youtube-cookies",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# Spotify OAuth token secret (cached token for headless server auth)
spotify_oauth_token_secret = secretmanager.Secret(
    "spotify-oauth-token",
    secret_id="spotify-oauth-token",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# Pushbullet API key (for credential health check notifications)
pushbullet_api_key_secret = secretmanager.Secret(
    "pushbullet-api-key",
    secret_id="pushbullet-api-key",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# Flacfetch account credentials (for credential keeper browser automation)
flacfetch_account_email_secret = secretmanager.Secret(
    "flacfetch-account-email",
    secret_id="flacfetch-account-email",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

flacfetch_account_password_secret = secretmanager.Secret(
    "flacfetch-account-password",
    secret_id="flacfetch-account-password",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# Grant flacfetch service account permission to update spotify-oauth-token secret
# Needed for credential keeper and token refresh writeback
spotify_oauth_token_iam = secretmanager.SecretIamMember(
    "spotify-oauth-token-writer",
    secret_id=spotify_oauth_token_secret.id,
    role="roles/secretmanager.secretVersionAdder",
    member=flacfetch_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant flacfetch service account permission to update youtube-cookies secret
# This is needed for the cookie upload API endpoint
youtube_cookies_iam = secretmanager.SecretIamMember(
    "youtube-cookies-writer",
    secret_id=youtube_cookies_secret.id,
    role="roles/secretmanager.secretVersionAdder",
    member=flacfetch_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# =============================================================================
# Persistent Data Disk
# =============================================================================
# This disk stores all torrent data and survives VM deletion.
# CRITICAL: autoDelete is NOT set on the disk attachment, so it persists.

flacfetch_data_disk = compute.Disk(
    "flacfetch-data",
    name="flacfetch-data",
    zone=zone,
    size=50,  # GB - plenty for torrent downloads
    type="pd-standard",  # Standard persistent disk (cost-effective for storage)
    description="Persistent storage for flacfetch torrent data",  # Match imported description
    physical_block_size_bytes=4096,
)

# =============================================================================
# Startup Script
# =============================================================================
# This script runs on every VM boot and:
# 1. Mounts the persistent data disk
# 2. Installs/updates flacfetch from GitHub
# 3. Configures Transmission to use the persistent disk
# 4. Starts the flacfetch API service

STARTUP_SCRIPT = r'''#!/bin/bash
# GCE Startup Script for flacfetch
# This script runs on every VM boot and handles:
# 1. Mounting persistent disk for torrent data
# 2. Installing/updating flacfetch
# 3. Configuring Transmission to use persistent storage
# 4. Starting the flacfetch API service
#
# IMPORTANT: Torrent data is stored on a separate persistent disk (flacfetch-data)
# that survives VM deletion, ensuring torrents are never lost.

set -e

# Ensure HOME is set for git config commands
export HOME=/root

# Log to a file for debugging
exec > >(tee /var/log/flacfetch-startup.log) 2>&1
echo "========================================"
echo "Starting flacfetch setup at $(date)"
echo "========================================"

# Install dependencies (idempotent)
echo "Installing/updating system dependencies..."
apt-get update
apt-get install -y python3-pip python3-venv transmission-daemon ffmpeg git curl unzip xvfb

# =============================================================================
# Install librespot (for Spotify audio capture)
# =============================================================================
# Pre-compiled binary is stored in GCS to avoid 30+ minute compilation time
echo "Installing/updating librespot..."
LIBRESPOT_VERSION="0.8.0"
LIBRESPOT_BIN="/usr/local/bin/librespot"

if [ -f "$LIBRESPOT_BIN" ]; then
    INSTALLED_VERSION=$("$LIBRESPOT_BIN" --version 2>&1 | grep -oP 'librespot \K[0-9.]+' || echo "unknown")
    echo "librespot already installed: $INSTALLED_VERSION"
fi

if [ ! -f "$LIBRESPOT_BIN" ] || [ "$INSTALLED_VERSION" != "$LIBRESPOT_VERSION" ]; then
    echo "Downloading librespot $LIBRESPOT_VERSION from GCS..."
    gsutil cp gs://karaoke-gen-storage-nomadkaraoke/binaries/librespot-${LIBRESPOT_VERSION}-linux-x86_64 "$LIBRESPOT_BIN"
    chmod +x "$LIBRESPOT_BIN"
    echo "Installed: $($LIBRESPOT_BIN --version 2>&1 | head -1)"
fi

# =============================================================================
# Install Deno Runtime (for yt-dlp EJS challenge solving)
# =============================================================================
# Deno is required for yt-dlp to solve YouTube's JavaScript challenges
# See: https://github.com/yt-dlp/yt-dlp/wiki/EJS
echo "Installing/updating Deno runtime..."
export DENO_INSTALL="/opt/deno"
DENO_BIN="$DENO_INSTALL/bin/deno"

if [ -f "$DENO_BIN" ]; then
    DENO_VERSION=$("$DENO_BIN" --version 2>&1 | head -1 | awk '{print $2}')
    echo "Deno already installed: $DENO_VERSION"
else
    echo "Installing Deno..."
    mkdir -p "$DENO_INSTALL"
    curl -fsSL https://deno.land/install.sh | DENO_INSTALL="$DENO_INSTALL" sh
    echo "Installed: $($DENO_BIN --version 2>&1 | head -1)"
fi

# Add Deno to PATH for current session
export PATH="$DENO_INSTALL/bin:$PATH"

# Ensure Deno is in system PATH for all users
cat > /etc/profile.d/deno.sh << DENO_PROFILE
export DENO_INSTALL="/opt/deno"
export PATH="\$DENO_INSTALL/bin:\$PATH"
DENO_PROFILE
chmod +x /etc/profile.d/deno.sh

# =============================================================================
# Persistent Disk Setup
# =============================================================================
PERSISTENT_MOUNT="/mnt/flacfetch-data"
TRANSMISSION_DATA="$PERSISTENT_MOUNT/transmission"
TRANSMISSION_CONFIG_DIR="/var/lib/transmission-daemon/.config/transmission-daemon"
TRANSMISSION_SETTINGS="/etc/transmission-daemon/settings.json"

echo "Setting up persistent storage..."

# Check if the persistent disk is attached (should be /dev/sdb)
if [ -b /dev/sdb ]; then
    echo "Persistent disk found at /dev/sdb"

    # Create mount point
    mkdir -p "$PERSISTENT_MOUNT"

    # Check if already mounted (skip formatting if so)
    if mountpoint -q "$PERSISTENT_MOUNT"; then
        echo "Persistent disk already mounted at $PERSISTENT_MOUNT"
    else
        # Check if disk is formatted
        if ! blkid /dev/sdb | grep -q 'TYPE="ext4"'; then
            echo "Formatting persistent disk..."
            mkfs.ext4 -F /dev/sdb
        else
            echo "Persistent disk already formatted"
        fi

        # Mount the disk
        echo "Mounting persistent disk..."
        mount /dev/sdb "$PERSISTENT_MOUNT"
    fi

    # Add to fstab if not already there
    UUID=$(blkid -s UUID -o value /dev/sdb)
    if ! grep -q "$UUID" /etc/fstab; then
        echo "UUID=$UUID $PERSISTENT_MOUNT ext4 defaults,nofail,discard 0 2" >> /etc/fstab
        echo "Added persistent disk to fstab"
    fi

    # Create directory structure on persistent disk
    mkdir -p "$TRANSMISSION_DATA/downloads"
    mkdir -p "$TRANSMISSION_DATA/.incomplete"
    mkdir -p "$TRANSMISSION_DATA/config/torrents"
    mkdir -p "$TRANSMISSION_DATA/config/resume"
    chown -R debian-transmission:debian-transmission "$TRANSMISSION_DATA"

    echo "Persistent disk mounted at $PERSISTENT_MOUNT"
    df -h "$PERSISTENT_MOUNT"

    USE_PERSISTENT_DISK=true
else
    echo "WARNING: No persistent disk found. Torrent data will be stored on boot disk."
    echo "To enable persistent storage, attach a disk named 'flacfetch-data'"
    USE_PERSISTENT_DISK=false
fi

# =============================================================================
# Transmission Configuration
# =============================================================================
echo "Configuring Transmission daemon..."

# Stop transmission to configure
systemctl stop transmission-daemon || true
sleep 2

if [ "$USE_PERSISTENT_DISK" = true ]; then
    # Configure transmission to use persistent disk
    cat > "$TRANSMISSION_SETTINGS" << SETTINGS
{
    "download-dir": "$TRANSMISSION_DATA/downloads",
    "incomplete-dir": "$TRANSMISSION_DATA/.incomplete",
    "incomplete-dir-enabled": true,
    "rpc-authentication-required": false,
    "rpc-bind-address": "127.0.0.1",
    "rpc-enabled": true,
    "rpc-port": 9091,
    "rpc-whitelist-enabled": false,
    "peer-port": 51413,
    "port-forwarding-enabled": false,
    "speed-limit-down": 0,
    "speed-limit-down-enabled": false,
    "speed-limit-up": 0,
    "speed-limit-up-enabled": false,
    "ratio-limit-enabled": false,
    "umask": 2,
    "encryption": 1,
    "dht-enabled": true,
    "pex-enabled": true,
    "utp-enabled": true
}
SETTINGS

    # Set up symlinks for torrent/resume data (transmission looks in config dir)
    mkdir -p "$TRANSMISSION_CONFIG_DIR"
    rm -rf "$TRANSMISSION_CONFIG_DIR/torrents" 2>/dev/null || true
    rm -rf "$TRANSMISSION_CONFIG_DIR/resume" 2>/dev/null || true
    ln -sf "$TRANSMISSION_DATA/config/torrents" "$TRANSMISSION_CONFIG_DIR/torrents"
    ln -sf "$TRANSMISSION_DATA/config/resume" "$TRANSMISSION_CONFIG_DIR/resume"
    chown -h debian-transmission:debian-transmission "$TRANSMISSION_CONFIG_DIR/torrents" "$TRANSMISSION_CONFIG_DIR/resume"

    echo "Transmission configured to use persistent disk"
    echo "  Downloads: $TRANSMISSION_DATA/downloads"
    echo "  Torrents:  $TRANSMISSION_DATA/config/torrents"
else
    # Fallback: use default paths on boot disk
    cat > "$TRANSMISSION_SETTINGS" << 'SETTINGS'
{
    "download-dir": "/var/lib/transmission-daemon/downloads",
    "incomplete-dir": "/var/lib/transmission-daemon/.incomplete",
    "incomplete-dir-enabled": true,
    "rpc-authentication-required": false,
    "rpc-bind-address": "127.0.0.1",
    "rpc-enabled": true,
    "rpc-port": 9091,
    "rpc-whitelist-enabled": false,
    "peer-port": 51413,
    "port-forwarding-enabled": false,
    "speed-limit-down": 0,
    "speed-limit-down-enabled": false,
    "speed-limit-up": 0,
    "speed-limit-up-enabled": false,
    "ratio-limit-enabled": false,
    "umask": 2,
    "encryption": 1,
    "dht-enabled": true,
    "pex-enabled": true,
    "utp-enabled": true
}
SETTINGS

    mkdir -p /var/lib/transmission-daemon/downloads
    mkdir -p /var/lib/transmission-daemon/.incomplete
    mkdir -p "$TRANSMISSION_CONFIG_DIR/torrents"
    mkdir -p "$TRANSMISSION_CONFIG_DIR/resume"
    chown -R debian-transmission:debian-transmission /var/lib/transmission-daemon
fi

# Start transmission
echo "Starting Transmission daemon..."
systemctl start transmission-daemon
sleep 2

# Verify transmission is responding
echo "Verifying Transmission daemon..."
for i in {1..10}; do
    if transmission-remote localhost:9091 -l >/dev/null 2>&1; then
        TORRENT_COUNT=$(transmission-remote localhost:9091 -l 2>/dev/null | tail -n +2 | head -n -1 | wc -l)
        echo "Transmission daemon is running with $TORRENT_COUNT torrent(s)"
        break
    fi
    echo "Waiting for Transmission ($i/10)..."
    sleep 1
done

# =============================================================================
# Flacfetch Installation/Update
# =============================================================================
echo "Installing/updating flacfetch..."
cd /opt

if [ -d "flacfetch" ]; then
    echo "Updating existing flacfetch installation..."
    cd flacfetch

    # Mark directory as safe (ownership may differ between runs)
    git config --global --add safe.directory /opt/flacfetch

    # Stash any local changes (shouldn't be any)
    git stash 2>/dev/null || true

    # Fetch and pull latest
    git fetch origin
    git reset --hard origin/main

    # Activate venv and update
    source venv/bin/activate
    pip install -e ".[api,spotify]" --quiet

    # Install/update yt-dlp-ejs for YouTube JS challenge solving
    pip install --upgrade yt-dlp yt-dlp-ejs --quiet

    # Install credential keeper dependencies (patchright + browser)
    pip install -e ".[keeper]" --quiet
    patchright install chromium --quiet 2>/dev/null || python -m patchright install chromium
else
    echo "Fresh flacfetch installation..."
    git clone https://github.com/nomadkaraoke/flacfetch.git
    cd flacfetch

    # Create virtual environment
    python3 -m venv venv
    source venv/bin/activate

    # Install flacfetch with API and Spotify dependencies
    pip install -e ".[api,spotify]"

    # Install yt-dlp-ejs for YouTube JS challenge solving
    pip install yt-dlp-ejs

    # Install credential keeper dependencies (patchright + browser)
    pip install -e ".[keeper]"
    patchright install chromium || python -m patchright install chromium
fi

# Get version
FLACFETCH_VERSION=$(python -c "import flacfetch; print(flacfetch.__version__)" 2>/dev/null || echo "unknown")
echo "Flacfetch version: $FLACFETCH_VERSION"

# Get yt-dlp version
YTDLP_VERSION=$(python -c "import yt_dlp; print(yt_dlp.version.__version__)" 2>/dev/null || echo "unknown")
echo "yt-dlp version: $YTDLP_VERSION"

# Verify EJS is working
if python -c "import yt_dlp_ejs" 2>/dev/null; then
    echo "yt-dlp-ejs: installed and available"
else
    echo "WARNING: yt-dlp-ejs not available"
fi

# =============================================================================
# Get Secrets from Secret Manager
# =============================================================================
echo "Fetching secrets from Secret Manager..."
FLACFETCH_API_KEY=$(gcloud secrets versions access latest --secret=flacfetch-api-key 2>/dev/null || echo "")
RED_API_KEY=$(gcloud secrets versions access latest --secret=red-api-key 2>/dev/null || echo "")
RED_API_URL=$(gcloud secrets versions access latest --secret=red-api-url 2>/dev/null || echo "")
OPS_API_KEY=$(gcloud secrets versions access latest --secret=ops-api-key 2>/dev/null || echo "")
OPS_API_URL=$(gcloud secrets versions access latest --secret=ops-api-url 2>/dev/null || echo "")
SPOTIPY_CLIENT_ID=$(gcloud secrets versions access latest --secret=spotipy-client-id 2>/dev/null || echo "")
SPOTIPY_CLIENT_SECRET=$(gcloud secrets versions access latest --secret=spotipy-client-secret 2>/dev/null || echo "")
SPOTIPY_REDIRECT_URI="http://127.0.0.1:8888/callback"

# Get bucket name from project metadata
GCS_BUCKET=$(gcloud compute project-info describe --format='value(commonInstanceMetadata.items.gcs-bucket)' 2>/dev/null || echo "")
if [ -z "$GCS_BUCKET" ]; then
    PROJECT_ID=$(curl -s "http://metadata.google.internal/computeMetadata/v1/project/project-id" -H "Metadata-Flavor: Google")
    GCS_BUCKET="karaoke-gen-storage-${PROJECT_ID}"
fi
echo "Using GCS bucket: $GCS_BUCKET"

# =============================================================================
# YouTube Cookies (for authenticated downloads)
# =============================================================================
# If youtube-cookies secret exists, write it to a file for yt-dlp
YOUTUBE_COOKIES_FILE="/opt/flacfetch/youtube_cookies.txt"
YOUTUBE_COOKIES=$(gcloud secrets versions access latest --secret=youtube-cookies 2>/dev/null || echo "")

if [ -n "$YOUTUBE_COOKIES" ]; then
    echo "$YOUTUBE_COOKIES" > "$YOUTUBE_COOKIES_FILE"
    chmod 600 "$YOUTUBE_COOKIES_FILE"
    echo "YouTube cookies configured at $YOUTUBE_COOKIES_FILE"
else
    # Remove old cookies file if secret is empty/deleted
    rm -f "$YOUTUBE_COOKIES_FILE"
    YOUTUBE_COOKIES_FILE=""
    echo "No YouTube cookies configured (downloads may require authentication in future)"
fi

# =============================================================================
# Spotify OAuth Token (for headless server authentication)
# =============================================================================
# Spotipy stores its OAuth token in a .cache file. On a headless server,
# we need to pre-populate this from Secret Manager since we can't do
# browser-based OAuth.
SPOTIFY_CACHE_FILE="/opt/flacfetch/.cache"
SPOTIFY_OAUTH_TOKEN=$(gcloud secrets versions access latest --secret=spotify-oauth-token 2>/dev/null || echo "")

if [ -n "$SPOTIFY_OAUTH_TOKEN" ]; then
    echo "$SPOTIFY_OAUTH_TOKEN" > "$SPOTIFY_CACHE_FILE"
    chmod 600 "$SPOTIFY_CACHE_FILE"
    echo "Spotify OAuth token configured at $SPOTIFY_CACHE_FILE"
else
    echo "WARNING: No Spotify OAuth token configured. Spotify search will not work."
    echo "To fix: Run 'flacfetch spotify-auth' locally, then upload the token with:"
    echo "  gcloud secrets versions add spotify-oauth-token --data-file=~/.cache"
fi

# =============================================================================
# Pushbullet API Key (for credential health check notifications)
# =============================================================================
PUSHBULLET_API_KEY=$(gcloud secrets versions access latest --secret=pushbullet-api-key 2>/dev/null || echo "")
if [ -n "$PUSHBULLET_API_KEY" ]; then
    echo "Pushbullet notifications configured"
else
    echo "WARNING: No Pushbullet API key configured. Credential health check notifications disabled."
fi

# =============================================================================
# Credential Keeper Account Credentials
# =============================================================================
FLACFETCH_ACCOUNT_EMAIL=$(gcloud secrets versions access latest --secret=flacfetch-account-email 2>/dev/null || echo "")
FLACFETCH_ACCOUNT_PASSWORD=$(gcloud secrets versions access latest --secret=flacfetch-account-password 2>/dev/null || echo "")

if [ -n "$FLACFETCH_ACCOUNT_EMAIL" ]; then
    echo "Credential keeper account configured: $FLACFETCH_ACCOUNT_EMAIL"
else
    echo "WARNING: No credential keeper account configured. Browser automation will not work."
fi

# Create browser profile directory on persistent disk
BROWSER_PROFILE_DIR="$PERSISTENT_MOUNT/browser-profiles"
mkdir -p "$BROWSER_PROFILE_DIR/google"
echo "Browser profile directory: $BROWSER_PROFILE_DIR"

# =============================================================================
# Create/Update Systemd Service
# =============================================================================
echo "Creating/updating systemd service..."

# Set download dir based on whether persistent disk is available
if [ "$USE_PERSISTENT_DISK" = true ]; then
    DOWNLOAD_DIR="$TRANSMISSION_DATA/downloads"
else
    DOWNLOAD_DIR="/var/lib/transmission-daemon/downloads"
fi

cat > /etc/systemd/system/flacfetch.service << SYSTEMD
[Unit]
Description=Flacfetch HTTP API Service
After=network.target transmission-daemon.service
Requires=transmission-daemon.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/flacfetch
Environment="FLACFETCH_API_KEY=${FLACFETCH_API_KEY}"
Environment="RED_API_KEY=${RED_API_KEY}"
Environment="RED_API_URL=${RED_API_URL}"
Environment="OPS_API_KEY=${OPS_API_KEY}"
Environment="OPS_API_URL=${OPS_API_URL}"
Environment="SPOTIPY_CLIENT_ID=${SPOTIPY_CLIENT_ID}"
Environment="SPOTIPY_CLIENT_SECRET=${SPOTIPY_CLIENT_SECRET}"
Environment="SPOTIPY_REDIRECT_URI=${SPOTIPY_REDIRECT_URI}"
Environment="PATH=/opt/deno/bin:/usr/local/bin:/usr/bin:/bin"
Environment="DENO_INSTALL=/opt/deno"
Environment="GCS_BUCKET=${GCS_BUCKET}"
Environment="FLACFETCH_KEEP_SEEDING=true"
Environment="FLACFETCH_MIN_FREE_GB=5"
Environment="FLACFETCH_DOWNLOAD_DIR=${DOWNLOAD_DIR}"
Environment="TRANSMISSION_HOST=localhost"
Environment="TRANSMISSION_PORT=9091"
Environment="YOUTUBE_COOKIES_FILE=${YOUTUBE_COOKIES_FILE}"
Environment="PUSHBULLET_API_KEY=${PUSHBULLET_API_KEY}"
ExecStart=/opt/flacfetch/venv/bin/flacfetch serve --host 0.0.0.0 --port 8080
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SYSTEMD

systemctl daemon-reload
systemctl enable flacfetch

# Restart flacfetch service (it's okay to restart this - it doesn't hold torrent state)
echo "Restarting flacfetch service..."
systemctl restart flacfetch

# Wait for service to be healthy
echo "Waiting for flacfetch service to be healthy..."
for i in {1..30}; do
    if curl -s http://localhost:8080/health | grep -q '"status":"healthy"'; then
        echo "Flacfetch service is healthy!"
        break
    fi
    echo "Waiting for flacfetch ($i/30)..."
    sleep 2
done

# =============================================================================
# yt-dlp Auto-Update Timer
# =============================================================================
# Create a systemd timer to update yt-dlp and yt-dlp-ejs daily
# This ensures YouTube downloads continue working as YouTube changes its API
echo "Creating yt-dlp auto-update timer..."

# Create the update script
cat > /opt/flacfetch/update-ytdlp.sh << 'UPDATE_SCRIPT'
#!/bin/bash
# Update yt-dlp and yt-dlp-ejs to latest versions
set -e
exec >> /var/log/ytdlp-update.log 2>&1

echo "========================================"
echo "yt-dlp update started at $(date)"
echo "========================================"

cd /opt/flacfetch
source venv/bin/activate

# Get current versions
OLD_YTDLP=$(python -c "import yt_dlp; print(yt_dlp.version.__version__)" 2>/dev/null || echo "unknown")
echo "Current yt-dlp version: $OLD_YTDLP"

# Update packages
pip install --upgrade yt-dlp yt-dlp-ejs --quiet

# Get new versions
NEW_YTDLP=$(python -c "import yt_dlp; print(yt_dlp.version.__version__)" 2>/dev/null || echo "unknown")
echo "New yt-dlp version: $NEW_YTDLP"

if [ "$OLD_YTDLP" != "$NEW_YTDLP" ]; then
    echo "yt-dlp updated from $OLD_YTDLP to $NEW_YTDLP"
    # Restart flacfetch to use new version
    systemctl restart flacfetch
    echo "Restarted flacfetch service"
else
    echo "yt-dlp already at latest version"
fi

# Update Deno if available
if command -v /opt/deno/bin/deno &> /dev/null; then
    echo "Updating Deno..."
    /opt/deno/bin/deno upgrade --quiet 2>/dev/null || true
fi

echo "Update complete at $(date)"
UPDATE_SCRIPT

chmod +x /opt/flacfetch/update-ytdlp.sh

# Create the systemd service for updates
cat > /etc/systemd/system/ytdlp-update.service << 'YTDLP_SERVICE'
[Unit]
Description=Update yt-dlp and yt-dlp-ejs
After=network.target

[Service]
Type=oneshot
ExecStart=/opt/flacfetch/update-ytdlp.sh
User=root
WorkingDirectory=/opt/flacfetch
Environment="PATH=/opt/deno/bin:/usr/local/bin:/usr/bin:/bin"
Environment="DENO_INSTALL=/opt/deno"
YTDLP_SERVICE

# Create the systemd timer (runs daily at 4am UTC)
cat > /etc/systemd/system/ytdlp-update.timer << 'YTDLP_TIMER'
[Unit]
Description=Daily yt-dlp update

[Timer]
OnCalendar=*-*-* 04:00:00
RandomizedDelaySec=1800
Persistent=true

[Install]
WantedBy=timers.target
YTDLP_TIMER

# Enable and start the timer
systemctl daemon-reload
systemctl enable ytdlp-update.timer
systemctl start ytdlp-update.timer

echo "yt-dlp auto-update timer enabled (runs daily at 4am UTC)"

# =============================================================================
# Credential Health Check Timer
# =============================================================================
# Create a systemd timer to check Spotify and YouTube credentials daily
# and send Pushbullet notifications if action is needed
echo "Creating credential health check timer..."

# Create the check script
cat > /opt/flacfetch/check-credentials.sh << 'CRED_CHECK_SCRIPT'
#!/bin/bash
# Check Spotify and YouTube credentials and notify via Pushbullet
set -e
exec >> /var/log/flacfetch-credential-check.log 2>&1

echo "========================================"
echo "Credential check started at $(date)"
echo "========================================"

cd /opt/flacfetch
source venv/bin/activate

# Run the credential check via the API (calls locally)
# This uses the flacfetch module directly instead of HTTP to avoid API key requirement
python -c "
from flacfetch.api.services.credential_check import run_credential_health_check
import json

result = run_credential_health_check(notify=True, notify_on_success=False)
print(json.dumps(result, indent=2))
"

echo "Credential check complete at $(date)"
CRED_CHECK_SCRIPT

chmod +x /opt/flacfetch/check-credentials.sh

# Create the systemd service for credential check
cat > /etc/systemd/system/flacfetch-credential-check.service << CRED_CHECK_SERVICE
[Unit]
Description=Check flacfetch credentials (Spotify, YouTube)
After=network.target flacfetch.service

[Service]
Type=oneshot
ExecStart=/opt/flacfetch/check-credentials.sh
User=root
WorkingDirectory=/opt/flacfetch
Environment="SPOTIPY_CLIENT_ID=${SPOTIPY_CLIENT_ID}"
Environment="SPOTIPY_CLIENT_SECRET=${SPOTIPY_CLIENT_SECRET}"
Environment="SPOTIPY_REDIRECT_URI=${SPOTIPY_REDIRECT_URI}"
Environment="YOUTUBE_COOKIES_FILE=${YOUTUBE_COOKIES_FILE}"
Environment="PUSHBULLET_API_KEY=${PUSHBULLET_API_KEY}"
Environment="PATH=/opt/deno/bin:/usr/local/bin:/usr/bin:/bin"
CRED_CHECK_SERVICE

# Create the systemd timer (runs daily at 7pm UTC / 2pm EST)
cat > /etc/systemd/system/flacfetch-credential-check.timer << 'CRED_CHECK_TIMER'
[Unit]
Description=Daily flacfetch credential health check

[Timer]
OnCalendar=*-*-* 19:00:00
RandomizedDelaySec=1800
Persistent=true

[Install]
WantedBy=timers.target
CRED_CHECK_TIMER

# Enable and start the timer
systemctl daemon-reload
systemctl enable flacfetch-credential-check.timer
systemctl start flacfetch-credential-check.timer

echo "Credential health check timer enabled (runs daily at 7pm UTC / 2pm EST)"

# Run initial credential check
echo "Running initial credential check..."
/opt/flacfetch/check-credentials.sh || true

# =============================================================================
# Xvfb Virtual Display (for credential keeper browser)
# =============================================================================
echo "Creating Xvfb virtual display service..."

cat > /etc/systemd/system/xvfb.service << 'XVFB_SERVICE'
[Unit]
Description=Xvfb Virtual Display
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/Xvfb :99 -screen 0 1280x720x24 -nolisten tcp
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
XVFB_SERVICE

systemctl daemon-reload
systemctl enable xvfb
systemctl start xvfb
echo "Xvfb virtual display started on :99"

# =============================================================================
# Credential Keeper Service (browser automation)
# =============================================================================
if [ -n "$FLACFETCH_ACCOUNT_EMAIL" ] && [ -n "$FLACFETCH_ACCOUNT_PASSWORD" ]; then
    echo "Creating credential keeper service..."

    cat > /etc/systemd/system/credential-keeper.service << KEEPER_SERVICE
[Unit]
Description=Flacfetch Credential Keeper (browser automation)
After=network.target xvfb.service flacfetch.service
Requires=xvfb.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/flacfetch
Environment="DISPLAY=:99"
Environment="FLACFETCH_API_KEY=${FLACFETCH_API_KEY}"
Environment="FLACFETCH_ACCOUNT_EMAIL=${FLACFETCH_ACCOUNT_EMAIL}"
Environment="FLACFETCH_ACCOUNT_PASSWORD=${FLACFETCH_ACCOUNT_PASSWORD}"
Environment="SPOTIPY_CLIENT_ID=${SPOTIPY_CLIENT_ID}"
Environment="SPOTIPY_CLIENT_SECRET=${SPOTIPY_CLIENT_SECRET}"
Environment="SPOTIPY_REDIRECT_URI=${SPOTIPY_REDIRECT_URI}"
Environment="PUSHBULLET_API_KEY=${PUSHBULLET_API_KEY}"
Environment="BROWSER_PROFILE_DIR=${BROWSER_PROFILE_DIR}"
Environment="KEEPER_STATUS_FILE=${BROWSER_PROFILE_DIR}/keeper-status.json"
Environment="PATH=/opt/deno/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=/opt/flacfetch/venv/bin/python -m flacfetch.credential_keeper
Restart=always
RestartSec=30
StandardOutput=append:/var/log/flacfetch-credential-keeper.log
StandardError=append:/var/log/flacfetch-credential-keeper.log

[Install]
WantedBy=multi-user.target
KEEPER_SERVICE

    systemctl daemon-reload
    systemctl enable credential-keeper
    systemctl restart credential-keeper
    echo "Credential keeper service started"
else
    echo "Skipping credential keeper (no account credentials configured)"
fi

# =============================================================================
# Summary
# =============================================================================
echo ""
echo "========================================"
echo "Setup complete at $(date)"
echo "========================================"
echo ""

# Show storage status
echo "Storage Status:"
echo "---------------"
if [ "$USE_PERSISTENT_DISK" = true ]; then
    echo "✅ Persistent disk mounted at $PERSISTENT_MOUNT"
    echo "   Torrent data will survive VM deletion!"
    df -h "$PERSISTENT_MOUNT"
    echo ""
    echo "   Torrent files: $(find $TRANSMISSION_DATA/config/torrents -name '*.torrent' 2>/dev/null | wc -l)"
    echo "   Downloaded files: $(find $TRANSMISSION_DATA/downloads -type f 2>/dev/null | wc -l)"
else
    echo "⚠️  No persistent disk - using boot disk (data will be lost on VM deletion)"
fi

echo ""
echo "Service Status:"
echo "---------------"
systemctl status flacfetch --no-pager -l | head -20

echo ""
echo "Transmission Status:"
echo "--------------------"
transmission-remote localhost:9091 -l 2>/dev/null || echo "Could not connect to transmission"

echo ""
echo "yt-dlp Status:"
echo "--------------"
source /opt/flacfetch/venv/bin/activate
echo "yt-dlp version: $(python -c 'import yt_dlp; print(yt_dlp.version.__version__)' 2>/dev/null || echo 'unknown')"
echo "yt-dlp-ejs: $(python -c 'import yt_dlp_ejs; print("installed")' 2>/dev/null || echo 'not installed')"
echo "Deno: $(/opt/deno/bin/deno --version 2>/dev/null | head -1 || echo 'not installed')"
echo "Update timer: $(systemctl is-active ytdlp-update.timer 2>/dev/null || echo 'not configured')"

echo ""
echo "Credential Keeper Status:"
echo "-------------------------"
if systemctl is-active credential-keeper >/dev/null 2>&1; then
    echo "Credential keeper: running"
    echo "Xvfb display: $(systemctl is-active xvfb 2>/dev/null || echo 'not running')"
    if [ -f "$BROWSER_PROFILE_DIR/keeper-status.json" ]; then
        cat "$BROWSER_PROFILE_DIR/keeper-status.json" | python3 -m json.tool 2>/dev/null
    fi
else
    echo "Credential keeper: not running"
fi

echo ""
echo "Health Check:"
echo "-------------"
curl -s http://localhost:8080/health | python3 -m json.tool 2>/dev/null || echo "Health check failed"
'''

# =============================================================================
# Compute Instance
# =============================================================================

flacfetch_vm = compute.Instance(
    "flacfetch-service",
    name="flacfetch-service",
    machine_type="e2-medium",  # 1 vCPU, 4GB RAM - needed for Chromium browser (credential keeper)
    zone=zone,
    boot_disk=compute.InstanceBootDiskArgs(
        initialize_params=compute.InstanceBootDiskInitializeParamsArgs(
            image="debian-cloud/debian-12",
            size=30,  # GB - for OS + flacfetch code
            type="pd-ssd",  # SSD for faster boot/code operations
        ),
        auto_delete=True,  # Boot disk can be recreated
    ),
    # Attach the persistent data disk
    attached_disks=[
        compute.InstanceAttachedDiskArgs(
            source=flacfetch_data_disk.self_link,
            device_name="flacfetch-data",
            mode="READ_WRITE",
            # NOTE: auto_delete defaults to False, meaning the disk survives VM deletion!
        ),
    ],
    network_interfaces=[compute.InstanceNetworkInterfaceArgs(
        network="default",
        access_configs=[compute.InstanceNetworkInterfaceAccessConfigArgs(
            nat_ip=flacfetch_ip.address,  # Static IP
            network_tier="PREMIUM",
        )],
    )],
    service_account=compute.InstanceServiceAccountArgs(
        email=flacfetch_sa.email,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    ),
    # Use metadata instead of metadata_startup_script to match imported state
    metadata={
        "gcs-bucket": bucket_name,
        "startup-script": STARTUP_SCRIPT,
    },
    tags=["flacfetch-service"],
    allow_stopping_for_update=True,
)

# =============================================================================
# Firewall Rules
# =============================================================================

flacfetch_firewall = compute.Firewall(
    "flacfetch-firewall",
    name="flacfetch-firewall",
    network="default",
    allows=[
        # BitTorrent peer connections
        compute.FirewallAllowArgs(protocol="tcp", ports=["51413"]),
        compute.FirewallAllowArgs(protocol="udp", ports=["51413"]),
        # Flacfetch HTTP API (auth required at app level)
        compute.FirewallAllowArgs(protocol="tcp", ports=["8080"]),
    ],
    source_ranges=["0.0.0.0/0"],  # Permissive, auth enforced at app level
    target_tags=["flacfetch-service"],
    description="Firewall rules for flacfetch torrent/API service",
)

# =============================================================================
# Exports
# =============================================================================

pulumi.export("project_id", project_id)
pulumi.export("zone", zone)
pulumi.export("region", region)

# VM exports
pulumi.export("vm_name", flacfetch_vm.name)
pulumi.export("vm_self_link", flacfetch_vm.self_link)

# Network exports
pulumi.export("static_ip", flacfetch_ip.address)
pulumi.export("service_url", flacfetch_ip.address.apply(lambda ip: f"http://{ip}:8080"))

# Storage exports
pulumi.export("data_disk_name", flacfetch_data_disk.name)
pulumi.export("data_disk_size_gb", flacfetch_data_disk.size)
pulumi.export("gcs_bucket", bucket_name)

# Service account exports
pulumi.export("service_account_email", flacfetch_sa.email)

# Secret exports (just names, not values)
# Note: flacfetch-api-key and flacfetch-api-url are managed by karaoke-gen
pulumi.export("secrets", {
    "red_api_key": red_api_key_secret.name,
    "red_api_url": red_api_url_secret.name,
    "ops_api_key": ops_api_key_secret.name,
    "ops_api_url": ops_api_url_secret.name,
    "spotipy_client_id": spotipy_client_id_secret.name,
    "spotipy_client_secret": spotipy_client_secret_secret.name,
    "spotify_oauth_token": spotify_oauth_token_secret.name,
    "pushbullet_api_key": pushbullet_api_key_secret.name,
    "youtube_cookies": youtube_cookies_secret.name,
    "flacfetch_account_email": flacfetch_account_email_secret.name,
    "flacfetch_account_password": flacfetch_account_password_secret.name,
})

