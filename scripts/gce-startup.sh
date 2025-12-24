#!/bin/bash
# GCE Startup Script for flacfetch
# This script runs on every VM boot and handles:
# 1. Installing/updating flacfetch
# 2. Configuring Transmission (only if not already configured)
# 3. Starting the flacfetch API service
#
# IMPORTANT: This script is designed to preserve torrent state across reboots.

set -e

# Log to a file for debugging
exec > >(tee /var/log/flacfetch-startup.log) 2>&1
echo "========================================"
echo "Starting flacfetch setup at $(date)"
echo "========================================"

# Install dependencies (idempotent)
echo "Installing/updating system dependencies..."
apt-get update
apt-get install -y python3-pip python3-venv transmission-daemon ffmpeg git curl

# =============================================================================
# Transmission Configuration (only if not already configured)
# =============================================================================
TRANSMISSION_CONFIG_DIR="/var/lib/transmission-daemon/.config/transmission-daemon"
TRANSMISSION_SETTINGS="/etc/transmission-daemon/settings.json"

# Check if this is first-time setup or if transmission needs configuration
NEEDS_TRANSMISSION_CONFIG=false

if [ ! -f "$TRANSMISSION_SETTINGS" ]; then
    NEEDS_TRANSMISSION_CONFIG=true
    echo "Transmission settings not found - first time setup"
elif ! grep -q '"rpc-authentication-required": false' "$TRANSMISSION_SETTINGS" 2>/dev/null; then
    # Check if RPC is properly configured
    NEEDS_TRANSMISSION_CONFIG=true
    echo "Transmission RPC not configured for local access"
fi

if [ "$NEEDS_TRANSMISSION_CONFIG" = true ]; then
    echo "Configuring Transmission daemon..."
    
    # Stop transmission gracefully to modify settings
    systemctl stop transmission-daemon || true
    sleep 2  # Give it time to save state
    
    # Create transmission directories (preserves existing data)
    mkdir -p /var/lib/transmission-daemon/downloads
    mkdir -p /var/lib/transmission-daemon/.incomplete
    mkdir -p "$TRANSMISSION_CONFIG_DIR/torrents"
    mkdir -p "$TRANSMISSION_CONFIG_DIR/resume"
    chown -R debian-transmission:debian-transmission /var/lib/transmission-daemon
    
    # Write settings to the correct location (Debian uses /etc/transmission-daemon/)
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
    
    chown debian-transmission:debian-transmission "$TRANSMISSION_SETTINGS"
    echo "Transmission settings configured"
else
    echo "Transmission already configured, preserving existing settings and state"
fi

# Ensure transmission is running
if ! systemctl is-active --quiet transmission-daemon; then
    echo "Starting Transmission daemon..."
    systemctl start transmission-daemon
    sleep 2
fi

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
    
    # Stash any local changes (shouldn't be any)
    git stash 2>/dev/null || true
    
    # Fetch and pull latest
    git fetch origin
    git reset --hard origin/main
    
    # Activate venv and update
    source venv/bin/activate
    pip install -e ".[api]" --quiet
else
    echo "Fresh flacfetch installation..."
    git clone https://github.com/nomadkaraoke/flacfetch.git
    cd flacfetch
    
    # Create virtual environment
    python3 -m venv venv
    source venv/bin/activate
    
    # Install flacfetch with API dependencies
    pip install -e ".[api]"
fi

# Get version
FLACFETCH_VERSION=$(python -c "import flacfetch; print(flacfetch.__version__)" 2>/dev/null || echo "unknown")
echo "Flacfetch version: $FLACFETCH_VERSION"

# =============================================================================
# Get Secrets from Secret Manager
# =============================================================================
echo "Fetching secrets from Secret Manager..."
FLACFETCH_API_KEY=$(gcloud secrets versions access latest --secret=flacfetch-api-key 2>/dev/null || echo "")
RED_API_KEY=$(gcloud secrets versions access latest --secret=red-api-key 2>/dev/null || echo "")
RED_API_URL=$(gcloud secrets versions access latest --secret=red-api-url 2>/dev/null || echo "")
OPS_API_KEY=$(gcloud secrets versions access latest --secret=ops-api-key 2>/dev/null || echo "")
OPS_API_URL=$(gcloud secrets versions access latest --secret=ops-api-url 2>/dev/null || echo "")

# Get bucket name from project metadata
GCS_BUCKET=$(gcloud compute project-info describe --format='value(commonInstanceMetadata.items.gcs-bucket)' 2>/dev/null || echo "")
if [ -z "$GCS_BUCKET" ]; then
    PROJECT_ID=$(curl -s "http://metadata.google.internal/computeMetadata/v1/project/project-id" -H "Metadata-Flavor: Google")
    GCS_BUCKET="karaoke-gen-storage-${PROJECT_ID}"
fi
echo "Using GCS bucket: $GCS_BUCKET"

# =============================================================================
# Create/Update Systemd Service
# =============================================================================
echo "Creating/updating systemd service..."
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
Environment="GCS_BUCKET=${GCS_BUCKET}"
Environment="FLACFETCH_KEEP_SEEDING=true"
Environment="FLACFETCH_MIN_FREE_GB=5"
Environment="FLACFETCH_DOWNLOAD_DIR=/var/lib/transmission-daemon/downloads"
Environment="TRANSMISSION_HOST=localhost"
Environment="TRANSMISSION_PORT=9091"
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
# Summary
# =============================================================================
echo ""
echo "========================================"
echo "Setup complete at $(date)"
echo "========================================"
echo ""

# Show current status
echo "Service Status:"
echo "---------------"
systemctl status flacfetch --no-pager -l | head -20

echo ""
echo "Transmission Status:"
echo "--------------------"
transmission-remote localhost:9091 -l 2>/dev/null || echo "Could not connect to transmission"

echo ""
echo "Health Check:"
echo "-------------"
curl -s http://localhost:8080/health | python3 -m json.tool 2>/dev/null || echo "Health check failed"

