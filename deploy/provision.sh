#!/usr/bin/env bash
# =============================================================================
# flacfetch provisioner — single root VPS (Netcup VPS 1000 G12, Debian 13)
# =============================================================================
# Idempotent port of the GCE startup script (infrastructure/__main__.py,
# STARTUP_SCRIPT). Safe to run repeatedly. Run as root:
#
#     sudo bash deploy/provision.sh
#
# What it does (each step is idempotent):
#   1. apt deps (Chromium/Xvfb libs, transmission, ffmpeg, ...)   [Debian 13 t64-aware]
#   2. (optional) non-root sudo login user                        [FF_ADMIN_USER/PUBKEY]
#   3. data partition on the root disk -> /mnt/flacfetch-data     [creates vda5 in free space]
#   4. Deno runtime (yt-dlp EJS)
#   5. flacfetch checkout + venv + extras + Patchright chromium
#   6. secrets: static from /etc/flacfetch/flacfetch.env;
#               GCS + rotating secrets via SA key (GOOGLE_APPLICATION_CREDENTIALS)
#   7. librespot (downloaded from GCS via SA key)
#   8. transmission settings + persistent-disk symlinks
#   9. systemd units: flacfetch, xvfb, credential-keeper, ytdlp/cred-check timers
#
# Secret-optional: with no .env / SA key it installs all infra and SKIPS the
# credentialed services with warnings (same posture as the GCE script). Drop the
# creds in and re-run to bring them up.
#
# Differences vs prod GCE are documented in
#   docs/archive/2026-06-18-netcup-provision-design.md
# =============================================================================
set -uo pipefail

# ---- tunables (override via env) -------------------------------------------
FF_GIT_REF="${FF_GIT_REF:-main}"
FF_REPO="${FF_REPO:-https://github.com/nomadkaraoke/flacfetch.git}"
FF_GCP_PROJECT="${FF_GCP_PROJECT:-nomadkaraoke}"
FF_GCS_BUCKET="${FF_GCS_BUCKET:-karaoke-gen-storage-nomadkaraoke}"
FF_MIN_DATA_GB="${FF_MIN_DATA_GB:-50}"
LIBRESPOT_VERSION="${LIBRESPOT_VERSION:-0.8.0}"
SPOTIPY_REDIRECT_URI="${SPOTIPY_REDIRECT_URI:-http://127.0.0.1:8888/callback}"
# Staging guard: keep the credential keeper + cred-check timer STOPPED so this
# box does not fight the live box for the shared Google session / the rotating
# Secret Manager secrets (single-writer). Set true only at cutover, once the old
# box's keeper is stopped. The units are still written either way.
FF_ENABLE_KEEPER="${FF_ENABLE_KEEPER:-true}"

# ---- fixed paths (match prod so systemd units are identical) ---------------
APP_DIR=/opt/flacfetch
VENV="$APP_DIR/venv"
ETC_DIR=/etc/flacfetch
ENV_FILE="$ETC_DIR/flacfetch.env"
SA_KEY="$ETC_DIR/gcs-sa.json"
RUNTIME_ENV="$ETC_DIR/runtime.env"   # 600, root-only; keeps secrets out of unit files
DATA_MOUNT=/mnt/flacfetch-data
DATA_LABEL=flacfetch-data
TRANSMISSION_DATA="$DATA_MOUNT/transmission"
TRANSMISSION_CONFIG_DIR="/var/lib/transmission-daemon/.config/transmission-daemon"
TRANSMISSION_SETTINGS="/etc/transmission-daemon/settings.json"
BROWSER_PROFILE_DIR="$DATA_MOUNT/browser-profiles"
DENO_INSTALL=/opt/deno
LIBRESPOT_BIN=/usr/local/bin/librespot
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log()  { echo -e ">>> $*"; }
warn() { echo -e "!!! WARNING: $*" >&2; }
die()  { echo -e "XXX FATAL: $*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

[ "$(id -u)" -eq 0 ] || die "run as root (sudo bash deploy/provision.sh)"
export HOME=/root
export DEBIAN_FRONTEND=noninteractive

# =============================================================================
log "Stage 1 — apt dependencies"
# =============================================================================
APT_CORE="python3-pip python3-venv python3-full transmission-daemon ffmpeg git curl unzip xvfb ca-certificates gdisk util-linux"
APT_CHROME="libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libdbus-1-3 \
 libxkbcommon0 libatspi2.0-0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
 libgbm1 libpango-1.0-0 libcairo2 libasound2 libxshmfence1"
apt-get update -qq
if apt-get install -y -qq $APT_CORE $APT_CHROME; then
  log "apt deps installed"
else
  # Debian 13/trixie renamed some libs with a t64 suffix
  warn "first apt pass failed; retrying with t64 library names"
  APT_T64="${APT_CHROME/libasound2/libasound2t64}"; APT_T64="${APT_T64/libcups2/libcups2t64}"
  apt-get install -y -qq $APT_CORE $APT_T64 || die "apt dependency install failed"
  log "apt deps installed (t64 variants)"
fi

# =============================================================================
log "Stage 1b — transmission 4.1.3 (source build)"
# =============================================================================
# Debian 13 ships only transmission 4.1.0-beta2, which RED/OPS reject on announce
# ("client is not on the whitelist") — so the box can download but cannot seed —
# and whose RPC differs. 4.1.3 is the latest stable the trackers whitelist. We keep
# the Debian transmission-daemon package (for its systemd unit, AppArmor profile,
# debian-transmission user and config scaffolding) and overlay the 4.1.3 binaries at
# Debian's /usr/bin paths, then apt-mark hold so apt can't revert them. Binaries are
# copied straight from the build tree (NOT `cmake --install`, which would drop an
# upstream unit with a nonexistent User=transmission into /usr/local and break boot).
TR_VER="4.1.3"
if /usr/bin/transmission-daemon --version 2>&1 | grep -q " ${TR_VER} "; then
  log "transmission ${TR_VER} already installed"
else
  log "building transmission ${TR_VER} from source (Debian ships only 4.1.0-beta2)"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    build-essential cmake pkg-config libcurl4-openssl-dev libssl-dev \
    libevent-dev zlib1g-dev libsystemd-dev gettext >/dev/null \
    || die "transmission build deps install failed"
  TR_SRC="$(mktemp -d)"
  curl -fsSL -o "$TR_SRC/t.tar.xz" \
    "https://github.com/transmission/transmission/releases/download/${TR_VER}/transmission-${TR_VER}.tar.xz" \
    || die "transmission ${TR_VER} source download failed"
  tar -xf "$TR_SRC/t.tar.xz" -C "$TR_SRC"
  ( cd "$TR_SRC/transmission-${TR_VER}" \
      && cmake -B build -G "Unix Makefiles" -DCMAKE_BUILD_TYPE=RelWithDebInfo \
           -DENABLE_DAEMON=ON -DENABLE_UTILS=ON -DENABLE_CLI=OFF -DENABLE_GTK=OFF \
           -DENABLE_QT=OFF -DENABLE_MAC=OFF -DENABLE_TESTS=OFF -DENABLE_WEB=OFF \
           -DINSTALL_DOC=OFF >/dev/null 2>&1 \
      && cmake --build build -j"$(nproc)" >/dev/null 2>&1 ) \
    || die "transmission ${TR_VER} build failed"
  systemctl stop transmission-daemon 2>/dev/null || true
  for b in transmission-daemon transmission-remote transmission-create transmission-edit transmission-show; do
    install -m 0755 "$TR_SRC/transmission-${TR_VER}/build/daemon/$b" "/usr/bin/$b" 2>/dev/null \
      || install -m 0755 "$TR_SRC/transmission-${TR_VER}/build/utils/$b" "/usr/bin/$b" 2>/dev/null \
      || warn "could not install $b"
  done
  rm -rf "$TR_SRC"
  apt-mark hold transmission-daemon transmission-common transmission-cli >/dev/null 2>&1 || true
  systemctl daemon-reload
  log "installed $(/usr/bin/transmission-daemon --version 2>&1 | head -1)"
fi

# =============================================================================
log "Stage 2 — non-root sudo login user (optional)"
# =============================================================================
# Driven by env so no personal key lives in the repo:
#   FF_ADMIN_USER=andrew FF_ADMIN_PUBKEY='ssh-ed25519 AAAA... comment' bash provision.sh
if [ -n "${FF_ADMIN_USER:-}" ]; then
  if ! id "$FF_ADMIN_USER" >/dev/null 2>&1; then
    log "creating sudo user '$FF_ADMIN_USER'"
    adduser --disabled-password --gecos "" "$FF_ADMIN_USER"
  else
    log "user '$FF_ADMIN_USER' already exists"
  fi
  usermod -aG sudo "$FF_ADMIN_USER"
  if [ -n "${FF_ADMIN_PUBKEY:-}" ]; then
    H="/home/$FF_ADMIN_USER/.ssh"; install -d -m 700 -o "$FF_ADMIN_USER" -g "$FF_ADMIN_USER" "$H"
    touch "$H/authorized_keys"
    grep -qxF "$FF_ADMIN_PUBKEY" "$H/authorized_keys" || echo "$FF_ADMIN_PUBKEY" >> "$H/authorized_keys"
    chmod 600 "$H/authorized_keys"; chown "$FF_ADMIN_USER:$FF_ADMIN_USER" "$H/authorized_keys"
    # passwordless sudo so key-only login can still escalate
    echo "$FF_ADMIN_USER ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/90-$FF_ADMIN_USER"
    chmod 440 "/etc/sudoers.d/90-$FF_ADMIN_USER"
    log "authorized key + sudo configured for '$FF_ADMIN_USER'"
  else
    warn "FF_ADMIN_USER set but FF_ADMIN_PUBKEY empty — created user without an SSH key"
  fi
else
  log "skipping admin user (set FF_ADMIN_USER + FF_ADMIN_PUBKEY to create one)"
fi

# =============================================================================
log "Stage 3 — data partition -> $DATA_MOUNT"
# =============================================================================
# On the single-disk VPS we carve a partition from the free space after the
# last existing partition. We NEVER touch existing partitions and NEVER mkfs a
# device that already has a filesystem, so re-runs are safe.
mkdir -p "$DATA_MOUNT"
ensure_fstab() {
  local dev="$1" uuid
  uuid="$(blkid -s UUID -o value "$dev")"
  [ -n "$uuid" ] || { warn "no UUID for $dev; skipping fstab"; return; }
  if ! grep -q "$uuid" /etc/fstab; then
    echo "UUID=$uuid $DATA_MOUNT ext4 defaults,nofail,discard 0 2" >> /etc/fstab
    log "added $DATA_MOUNT to fstab (UUID=$uuid)"
  fi
}
if mountpoint -q "$DATA_MOUNT"; then
  log "$DATA_MOUNT already mounted ($(findmnt -no SOURCE "$DATA_MOUNT"))"
  ensure_fstab "$(findmnt -no SOURCE "$DATA_MOUNT")"
else
  DATA_DEV="$(blkid -L "$DATA_LABEL" 2>/dev/null || true)"
  if [ -z "$DATA_DEV" ]; then
    DATA_DEV="$(lsblk -rno NAME,PARTLABEL 2>/dev/null | awk -v L="$DATA_LABEL" '$2==L{print "/dev/"$1; exit}')"
  fi
  if [ -z "$DATA_DEV" ]; then
    have sgdisk || die "sgdisk (gdisk) not installed; cannot create data partition"
    ROOT_SRC="$(findmnt -no SOURCE /)"
    PK="$(lsblk -no PKNAME "$ROOT_SRC" | head -1)"
    [ -n "$PK" ] || die "could not determine parent disk of root ($ROOT_SRC)"
    DISK="/dev/$PK"
    # largest free block bounds (exactly what `sgdisk -n 0:0:0` will use)
    FS="$(sgdisk -F "$DISK" 2>/dev/null | tr -dc '0-9')"
    LS="$(sgdisk -E "$DISK" 2>/dev/null | tr -dc '0-9')"
    if [ -n "$FS" ] && [ -n "$LS" ] && [ "$LS" -gt "$FS" ]; then
      FREE_GB=$(( (LS - FS + 1) * 512 / 1000000000 ))
    else
      FREE_GB=0
    fi
    log "largest free block on $DISK ≈ ${FREE_GB} GB (need >= ${FF_MIN_DATA_GB})"
    [ "$FREE_GB" -ge "$FF_MIN_DATA_GB" ] || die "not enough free space on $DISK for data partition"
    log "creating data partition on $DISK (label=$DATA_LABEL)"
    sgdisk -n 0:0:0 -t 0:8300 -c "0:$DATA_LABEL" "$DISK" || die "sgdisk partition create failed"
    partx -a "$DISK" 2>/dev/null || true
    udevadm settle 2>/dev/null || true; sleep 1
    DATA_DEV="$(lsblk -rno NAME,PARTLABEL "$DISK" | awk -v L="$DATA_LABEL" '$2==L{print "/dev/"$1; exit}')"
    [ -n "$DATA_DEV" ] || die "created partition but kernel did not expose it (try a reboot)"
    log "new data partition: $DATA_DEV"
  else
    log "found existing data device: $DATA_DEV"
  fi
  if ! blkid "$DATA_DEV" 2>/dev/null | grep -q 'TYPE="ext4"'; then
    if blkid "$DATA_DEV" 2>/dev/null | grep -q 'TYPE='; then
      die "$DATA_DEV already has a non-ext4 filesystem; refusing to mkfs (inspect manually)"
    fi
    log "formatting $DATA_DEV ext4 (label=$DATA_LABEL)"
    mkfs.ext4 -F -L "$DATA_LABEL" "$DATA_DEV" || die "mkfs failed on $DATA_DEV"
  else
    log "$DATA_DEV already ext4"
  fi
  mount "$DATA_DEV" "$DATA_MOUNT" || die "mount $DATA_DEV failed"
  ensure_fstab "$DATA_DEV"
fi
# data directory structure (owned by transmission for its trees)
mkdir -p "$TRANSMISSION_DATA"/{downloads,.incomplete,config/torrents,config/resume}
mkdir -p "$BROWSER_PROFILE_DIR/google"
id debian-transmission >/dev/null 2>&1 && chown -R debian-transmission:debian-transmission "$TRANSMISSION_DATA"
df -h "$DATA_MOUNT"

# =============================================================================
log "Stage 4 — Deno runtime (yt-dlp EJS)"
# =============================================================================
if [ -x "$DENO_INSTALL/bin/deno" ]; then
  log "Deno present: $("$DENO_INSTALL/bin/deno" --version 2>&1 | head -1)"
else
  mkdir -p "$DENO_INSTALL"
  curl -fsSL https://deno.land/install.sh | DENO_INSTALL="$DENO_INSTALL" sh -s -- -y >/dev/null 2>&1 \
    && log "Deno installed: $("$DENO_INSTALL/bin/deno" --version 2>&1 | head -1)" \
    || warn "Deno install failed (yt-dlp EJS challenge solving may not work)"
fi
export PATH="$DENO_INSTALL/bin:$PATH"
cat > /etc/profile.d/deno.sh <<'DENO_PROFILE'
export DENO_INSTALL="/opt/deno"
export PATH="$DENO_INSTALL/bin:$PATH"
DENO_PROFILE
chmod +x /etc/profile.d/deno.sh

# =============================================================================
log "Stage 5 — flacfetch checkout + venv + deps"
# =============================================================================
mkdir -p /opt
if [ -d "$APP_DIR/.git" ]; then
  log "updating existing checkout ($FF_GIT_REF)"
  git config --global --add safe.directory "$APP_DIR"
  git -C "$APP_DIR" stash >/dev/null 2>&1 || true
  git -C "$APP_DIR" fetch origin --tags --prune --quiet || warn "git fetch failed"
  # works for a branch (reset to its remote tip) or a tag/commit (reset to the ref)
  if git -C "$APP_DIR" show-ref --verify --quiet "refs/remotes/origin/$FF_GIT_REF"; then
    git -C "$APP_DIR" reset --hard "origin/$FF_GIT_REF" || warn "git reset failed"
  else
    git -C "$APP_DIR" reset --hard "$FF_GIT_REF" || warn "git reset failed"
  fi
else
  log "fresh clone ($FF_GIT_REF)"
  git clone --quiet "$FF_REPO" "$APP_DIR" || die "git clone failed"
  git -C "$APP_DIR" checkout "$FF_GIT_REF" --quiet || warn "git checkout $FF_GIT_REF failed"
fi
[ -d "$VENV" ] || python3 -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
pip install --upgrade pip --quiet
pip install -e "${APP_DIR}[api,spotify]" --quiet || die "pip install flacfetch[api,spotify] failed"
pip install --upgrade yt-dlp yt-dlp-ejs --quiet || warn "yt-dlp/ejs install failed"
pip install -e "${APP_DIR}[keeper]" --quiet || die "pip install flacfetch[keeper] failed"
patchright install chromium >/dev/null 2>&1 || python -m patchright install chromium \
  || warn "patchright chromium install failed"
FLACFETCH_VERSION="$(python -c 'import flacfetch; print(flacfetch.__version__)' 2>/dev/null || echo unknown)"
log "flacfetch version: $FLACFETCH_VERSION"
python -c 'import yt_dlp_ejs' 2>/dev/null && log "yt-dlp-ejs available" || warn "yt-dlp-ejs not available"

# =============================================================================
log "Stage 6 — secrets"
# =============================================================================
# Static secrets come from the operator-populated env file. Rotating secrets
# (youtube cookies, spotify oauth token) live in Secret Manager and are reached
# via the SA key, matching prod (the keeper writes refreshed values back there).
GOOGLE_APPLICATION_CREDENTIALS=""
if [ -f "$SA_KEY" ]; then
  export GOOGLE_APPLICATION_CREDENTIALS="$SA_KEY"
  export GOOGLE_CLOUD_PROJECT="$FF_GCP_PROJECT"
  log "GCP SA key present -> GCS + Secret Manager enabled"
  HAVE_GCP=true
else
  warn "no SA key at $SA_KEY — GCS upload, librespot download, and rotating-secret seeding are SKIPPED"
  HAVE_GCP=false
fi
# defaults; .env overrides
FLACFETCH_API_KEY=""; RED_API_KEY=""; RED_API_URL=""; OPS_API_KEY=""; OPS_API_URL=""
SPOTIPY_CLIENT_ID=""; SPOTIPY_CLIENT_SECRET=""; PUSHBULLET_API_KEY=""
FLACFETCH_ACCOUNT_EMAIL=""; FLACFETCH_ACCOUNT_PASSWORD=""
GCS_BUCKET="$FF_GCS_BUCKET"
if [ -f "$ENV_FILE" ]; then
  log "loading static secrets from $ENV_FILE"
  set -a; # shellcheck disable=SC1090
  source "$ENV_FILE"; set +a
else
  warn "no env file at $ENV_FILE — credentialed services will be created but not started"
fi
# Secrets go in a root-only (600) EnvironmentFile, NEVER inline in the unit files
# (which systemd writes world-readable 644). Units reference this via
# EnvironmentFile=-... (optional, so a no-secret run still works).
install -d -m 700 "$ETC_DIR"
( umask 077; cat > "$RUNTIME_ENV" <<RT
FLACFETCH_API_KEY=$FLACFETCH_API_KEY
RED_API_KEY=$RED_API_KEY
RED_API_URL=$RED_API_URL
OPS_API_KEY=$OPS_API_KEY
OPS_API_URL=$OPS_API_URL
SPOTIPY_CLIENT_ID=$SPOTIPY_CLIENT_ID
SPOTIPY_CLIENT_SECRET=$SPOTIPY_CLIENT_SECRET
PUSHBULLET_API_KEY=$PUSHBULLET_API_KEY
FLACFETCH_ACCOUNT_EMAIL=$FLACFETCH_ACCOUNT_EMAIL
FLACFETCH_ACCOUNT_PASSWORD=$FLACFETCH_ACCOUNT_PASSWORD
RT
)
chmod 600 "$RUNTIME_ENV"

# =============================================================================
log "Stage 7 — librespot (Spotify capture)"
# =============================================================================
sm_get() { "$VENV/bin/python" "$HERE/_sm_get.py" "$1" 2>/dev/null || true; }
if [ -x "$LIBRESPOT_BIN" ] && [ "$("$LIBRESPOT_BIN" --version 2>&1 | grep -oP 'librespot \K[0-9.]+' || echo x)" = "$LIBRESPOT_VERSION" ]; then
  log "librespot $LIBRESPOT_VERSION already installed"
elif [ "$HAVE_GCP" = true ]; then
  log "downloading librespot $LIBRESPOT_VERSION from GCS"
  if "$VENV/bin/python" "$HERE/_gcs_get.py" \
        "$GCS_BUCKET" "binaries/librespot-${LIBRESPOT_VERSION}-linux-x86_64" "$LIBRESPOT_BIN"; then
    chmod +x "$LIBRESPOT_BIN"; log "installed: $("$LIBRESPOT_BIN" --version 2>&1 | head -1)"
  else
    warn "librespot download failed"
  fi
else
  warn "librespot not installed (needs SA key)"
fi

# rotating secrets -> local files (seeded from Secret Manager; keeper refreshes)
YOUTUBE_COOKIES_FILE="$APP_DIR/youtube_cookies.txt"
SPOTIFY_CACHE_FILE="$APP_DIR/.cache"
if [ "$HAVE_GCP" = true ]; then
  YC="$(sm_get youtube-cookies)"
  if [ -n "$YC" ]; then printf '%s' "$YC" > "$YOUTUBE_COOKIES_FILE"; chmod 600 "$YOUTUBE_COOKIES_FILE"; log "youtube cookies seeded"; else rm -f "$YOUTUBE_COOKIES_FILE"; YOUTUBE_COOKIES_FILE=""; warn "no youtube cookies in Secret Manager"; fi
  ST="$(sm_get spotify-oauth-token)"
  if [ -n "$ST" ]; then printf '%s' "$ST" > "$SPOTIFY_CACHE_FILE"; chmod 600 "$SPOTIFY_CACHE_FILE"; log "spotify token seeded"; else warn "no spotify oauth token in Secret Manager"; fi
else
  YOUTUBE_COOKIES_FILE=""
fi

# =============================================================================
log "Stage 8 — transmission configuration"
# =============================================================================
systemctl stop transmission-daemon 2>/dev/null || true; sleep 2
cat > "$TRANSMISSION_SETTINGS" <<SETTINGS
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
mkdir -p "$TRANSMISSION_CONFIG_DIR"
rm -rf "$TRANSMISSION_CONFIG_DIR/torrents" "$TRANSMISSION_CONFIG_DIR/resume" 2>/dev/null || true
ln -sf "$TRANSMISSION_DATA/config/torrents" "$TRANSMISSION_CONFIG_DIR/torrents"
ln -sf "$TRANSMISSION_DATA/config/resume" "$TRANSMISSION_CONFIG_DIR/resume"
chown -h debian-transmission:debian-transmission "$TRANSMISSION_CONFIG_DIR/torrents" "$TRANSMISSION_CONFIG_DIR/resume" 2>/dev/null || true
systemctl start transmission-daemon
for _ in $(seq 1 10); do transmission-remote localhost:9091 -l >/dev/null 2>&1 && { log "transmission up"; break; }; sleep 1; done

# =============================================================================
log "Stage 9 — systemd units"
# =============================================================================
DOWNLOAD_DIR="$TRANSMISSION_DATA/downloads"

cat > /etc/systemd/system/flacfetch.service <<SYSTEMD
[Unit]
Description=Flacfetch HTTP API Service
After=network.target transmission-daemon.service
Requires=transmission-daemon.service

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
EnvironmentFile=-$RUNTIME_ENV
Environment="SPOTIPY_REDIRECT_URI=${SPOTIPY_REDIRECT_URI}"
Environment="PATH=$DENO_INSTALL/bin:/usr/local/bin:/usr/bin:/bin"
Environment="DENO_INSTALL=$DENO_INSTALL"
Environment="GCS_BUCKET=${GCS_BUCKET}"
Environment="GOOGLE_CLOUD_PROJECT=${FF_GCP_PROJECT}"
Environment="GOOGLE_APPLICATION_CREDENTIALS=${GOOGLE_APPLICATION_CREDENTIALS}"
Environment="FLACFETCH_KEEP_SEEDING=true"
Environment="FLACFETCH_MIN_FREE_GB=5"
Environment="FLACFETCH_DOWNLOAD_DIR=${DOWNLOAD_DIR}"
Environment="TRANSMISSION_HOST=localhost"
Environment="TRANSMISSION_PORT=9091"
Environment="YOUTUBE_COOKIES_FILE=${YOUTUBE_COOKIES_FILE}"
ExecStart=$VENV/bin/flacfetch serve --host 0.0.0.0 --port 8080
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SYSTEMD

# Xvfb virtual display (for the keeper browser)
cat > /etc/systemd/system/xvfb.service <<'XVFB_SERVICE'
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

# yt-dlp auto-update (daily 04:00 UTC)
cat > "$APP_DIR/update-ytdlp.sh" <<'UPDATE_SCRIPT'
#!/bin/bash
set -e
exec >> /var/log/ytdlp-update.log 2>&1
echo "yt-dlp update started at $(date)"
cd /opt/flacfetch && source venv/bin/activate
OLD=$(python -c "import yt_dlp; print(yt_dlp.version.__version__)" 2>/dev/null || echo unknown)
pip install --upgrade yt-dlp yt-dlp-ejs --quiet
NEW=$(python -c "import yt_dlp; print(yt_dlp.version.__version__)" 2>/dev/null || echo unknown)
if [ "$OLD" != "$NEW" ]; then echo "yt-dlp $OLD -> $NEW"; systemctl restart flacfetch; fi
command -v /opt/deno/bin/deno >/dev/null 2>&1 && /opt/deno/bin/deno upgrade --quiet 2>/dev/null || true
echo "Update complete at $(date)"
UPDATE_SCRIPT
chmod +x "$APP_DIR/update-ytdlp.sh"
cat > /etc/systemd/system/ytdlp-update.service <<'YTDLP_SERVICE'
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
cat > /etc/systemd/system/ytdlp-update.timer <<'YTDLP_TIMER'
[Unit]
Description=Daily yt-dlp update
[Timer]
OnCalendar=*-*-* 04:00:00
RandomizedDelaySec=1800
Persistent=true
[Install]
WantedBy=timers.target
YTDLP_TIMER

# Credential health check (daily 19:00 UTC)
cat > "$APP_DIR/check-credentials.sh" <<'CRED_CHECK_SCRIPT'
#!/bin/bash
set -e
exec >> /var/log/flacfetch-credential-check.log 2>&1
echo "Credential check started at $(date)"
cd /opt/flacfetch && source venv/bin/activate
python -c "
from flacfetch.api.services.credential_check import run_credential_health_check
import json
print(json.dumps(run_credential_health_check(notify=True, notify_on_success=False), indent=2))
"
echo "Credential check complete at $(date)"
CRED_CHECK_SCRIPT
chmod +x "$APP_DIR/check-credentials.sh"
cat > /etc/systemd/system/flacfetch-credential-check.service <<CRED_CHECK_SERVICE
[Unit]
Description=Check flacfetch credentials (Spotify, YouTube)
After=network.target flacfetch.service
[Service]
Type=oneshot
ExecStart=$APP_DIR/check-credentials.sh
User=root
WorkingDirectory=$APP_DIR
EnvironmentFile=-$RUNTIME_ENV
Environment="SPOTIPY_REDIRECT_URI=${SPOTIPY_REDIRECT_URI}"
Environment="YOUTUBE_COOKIES_FILE=${YOUTUBE_COOKIES_FILE}"
Environment="KEEPER_STATUS_FILE=${BROWSER_PROFILE_DIR}/keeper-status.json"
Environment="GOOGLE_CLOUD_PROJECT=${FF_GCP_PROJECT}"
Environment="GOOGLE_APPLICATION_CREDENTIALS=${GOOGLE_APPLICATION_CREDENTIALS}"
Environment="PATH=$DENO_INSTALL/bin:/usr/local/bin:/usr/bin:/bin"
CRED_CHECK_SERVICE
cat > /etc/systemd/system/flacfetch-credential-check.timer <<'CRED_CHECK_TIMER'
[Unit]
Description=Daily flacfetch credential health check
[Timer]
OnCalendar=*-*-* 19:00:00
RandomizedDelaySec=1800
Persistent=true
[Install]
WantedBy=timers.target
CRED_CHECK_TIMER

# Credential keeper (only if account creds are present)
if [ -n "$FLACFETCH_ACCOUNT_EMAIL" ] && [ -n "$FLACFETCH_ACCOUNT_PASSWORD" ]; then
  cat > /etc/systemd/system/credential-keeper.service <<KEEPER_SERVICE
[Unit]
Description=Flacfetch Credential Keeper (browser automation)
After=network.target xvfb.service flacfetch.service
Requires=xvfb.service

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
EnvironmentFile=-$RUNTIME_ENV
Environment="DISPLAY=:99"
Environment="SPOTIPY_REDIRECT_URI=${SPOTIPY_REDIRECT_URI}"
Environment="BROWSER_PROFILE_DIR=${BROWSER_PROFILE_DIR}"
Environment="KEEPER_STATUS_FILE=${BROWSER_PROFILE_DIR}/keeper-status.json"
Environment="KEEPER_NOTIFY_ON_SUCCESS=false"
Environment="GOOGLE_CLOUD_PROJECT=${FF_GCP_PROJECT}"
Environment="GOOGLE_APPLICATION_CREDENTIALS=${GOOGLE_APPLICATION_CREDENTIALS}"
Environment="PATH=$DENO_INSTALL/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=$VENV/bin/python -m flacfetch.credential_keeper
Restart=always
RestartSec=30
StandardOutput=append:/var/log/flacfetch-credential-keeper.log
StandardError=append:/var/log/flacfetch-credential-keeper.log

[Install]
WantedBy=multi-user.target
KEEPER_SERVICE
  KEEPER_CONFIGURED=true
else
  warn "credential keeper NOT configured (no account creds in $ENV_FILE)"
  KEEPER_CONFIGURED=false
fi

# ---- enable + (re)start ----------------------------------------------------
systemctl daemon-reload
systemctl enable --now xvfb >/dev/null 2>&1 || true
systemctl enable --now ytdlp-update.timer >/dev/null 2>&1 || true
systemctl enable flacfetch >/dev/null 2>&1 || true
if [ -f "$ENV_FILE" ]; then
  systemctl restart flacfetch
  for _ in $(seq 1 30); do curl -s http://localhost:8080/health 2>/dev/null | grep -q '"status":"healthy"' && { log "flacfetch healthy"; break; }; sleep 2; done
else
  warn "not starting flacfetch (no $ENV_FILE) — it would run without secrets"
fi
# Keeper + cred-check touch the SHARED Google session and the rotating Secret
# Manager secrets. Only enable them once this is the sole live box (cutover).
if [ "$FF_ENABLE_KEEPER" = true ]; then
  systemctl enable --now flacfetch-credential-check.timer >/dev/null 2>&1 || true
  if [ "$KEEPER_CONFIGURED" = true ]; then
    systemctl enable --now credential-keeper >/dev/null 2>&1 && systemctl restart credential-keeper || true
    log "credential keeper started"
  fi
else
  systemctl disable --now credential-keeper flacfetch-credential-check.timer >/dev/null 2>&1 || true
  warn "FF_ENABLE_KEEPER=false — keeper + cred-check left STOPPED (staging; avoids dual-writer conflict with the live GCP box). Set FF_ENABLE_KEEPER=true at cutover."
fi

# =============================================================================
log "Summary"
# =============================================================================
echo "  flacfetch:       $FLACFETCH_VERSION"
echo "  data mount:      $(findmnt -no SOURCE,SIZE,USED "$DATA_MOUNT" 2>/dev/null || echo 'NOT MOUNTED')"
echo "  GCP creds:       $HAVE_GCP    env file: $([ -f "$ENV_FILE" ] && echo present || echo MISSING)"
echo "  flacfetch svc:   $(systemctl is-active flacfetch 2>/dev/null || echo inactive)"
echo "  transmission:    $(systemctl is-active transmission-daemon 2>/dev/null || echo inactive)"
echo "  xvfb:            $(systemctl is-active xvfb 2>/dev/null || echo inactive)"
echo "  keeper:          $(systemctl is-active credential-keeper 2>/dev/null || echo 'not configured')"
echo "  health:          $(curl -s http://localhost:8080/health 2>/dev/null || echo 'n/a (service not started)')"
log "provision.sh complete at $(date)"
