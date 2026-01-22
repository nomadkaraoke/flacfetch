# Plan: Migrate Flacfetch Infrastructure from karaoke-gen to flacfetch Repo

**Created:** 2026-01-22
**Status:** Pending Approval

## Overview

Migrate management of the flacfetch VM infrastructure from karaoke-gen repo to the flacfetch repo, preserving all torrent data and the static IP address.

## Current State

### Pulumi State Conflict
Both repos currently manage the SAME GCP resources (discovered via `pulumi stack export`):

| Resource | karaoke-gen (prod) | flacfetch (dev) |
|----------|-------------------|-----------------|
| VM | flacfetch-service | flacfetch-service |
| Static IP | flacfetch-ip | flacfetch-static-ip |
| Service Account | flacfetch-sa | flacfetch-sa |
| Firewall | 2 rules (split) | 1 rule (combined) |
| Secrets | api-key, api-url only | All secrets including Spotify/YouTube |
| Persistent Disk | NOT managed | flacfetch-data (50GB) |

### GCP Resources (Actual)
- VM: `flacfetch-service` in us-central1-a, internal IP 10.128.0.41
- Static IP: 104.198.214.26 (whitelisted on music trackers)
- Persistent Disk: `flacfetch-data` exists but NOT attached to VM
- Torrent data: ~4.3GB on boot disk (14 seeding torrents)

### Why Migration is Needed
karaoke-gen has a simple 120-line startup script missing:
- Deno runtime (required for YouTube EJS challenges)
- yt-dlp auto-update timer
- Persistent disk for data resilience
- Spotify support (librespot)
- Credential health checks

flacfetch has comprehensive 850-line startup script with all features.

## Migration Strategy

**Approach**: Remove resources from karaoke-gen Pulumi state (NOT from GCP), then let flacfetch stack be the sole owner.

## Phase 0: Backup and Verification (15 min)

### 0.1 Backup Torrent Data to GCS
```bash
gcloud compute ssh flacfetch-service --zone=us-central1-a --project=nomadkaraoke --command="
sudo tar -czf /tmp/torrent-backup.tar.gz \
  /var/lib/transmission-daemon/downloads \
  /var/lib/transmission-daemon/.config/transmission-daemon/torrents \
  /var/lib/transmission-daemon/.config/transmission-daemon/resume && \
gsutil cp /tmp/torrent-backup.tar.gz gs://karaoke-gen-storage-nomadkaraoke/backups/flacfetch-torrent-backup-\$(date +%Y%m%d-%H%M).tar.gz"
```

### 0.2 Document Current State
```bash
# Record internal IP (used by karaoke-gen backend via VPC connector)
gcloud compute instances describe flacfetch-service --zone=us-central1-a \
  --format="value(networkInterfaces[0].networkIP)"
# Expected: 10.128.0.41
```

## Phase 1: Attach Persistent Disk and Migrate Data (30 min)

### 1.1 Stop VM and Attach Disk
```bash
gcloud compute instances stop flacfetch-service --zone=us-central1-a --project=nomadkaraoke
gcloud compute instances attach-disk flacfetch-service \
  --disk=flacfetch-data --device-name=flacfetch-data --zone=us-central1-a --project=nomadkaraoke
gcloud compute instances start flacfetch-service --zone=us-central1-a --project=nomadkaraoke
```

### 1.2 Format, Mount, and Migrate Data
```bash
gcloud compute ssh flacfetch-service --zone=us-central1-a --project=nomadkaraoke --command="
# Format disk if needed
sudo file -s /dev/sdb | grep -q 'ext4' || sudo mkfs.ext4 -F /dev/sdb

# Mount
sudo mkdir -p /mnt/flacfetch-data
sudo mount /dev/sdb /mnt/flacfetch-data

# Add to fstab
UUID=\$(sudo blkid -s UUID -o value /dev/sdb)
grep -q \"\$UUID\" /etc/fstab || echo \"UUID=\$UUID /mnt/flacfetch-data ext4 defaults,nofail,discard 0 2\" | sudo tee -a /etc/fstab

# Create directories
sudo mkdir -p /mnt/flacfetch-data/transmission/{downloads,.incomplete,config/torrents,config/resume}

# Stop transmission and copy data
sudo systemctl stop transmission-daemon
sudo cp -a /var/lib/transmission-daemon/downloads/* /mnt/flacfetch-data/transmission/downloads/ 2>/dev/null || true
sudo cp -a /var/lib/transmission-daemon/.config/transmission-daemon/torrents/* /mnt/flacfetch-data/transmission/config/torrents/ 2>/dev/null || true
sudo cp -a /var/lib/transmission-daemon/.config/transmission-daemon/resume/* /mnt/flacfetch-data/transmission/config/resume/ 2>/dev/null || true
sudo chown -R debian-transmission:debian-transmission /mnt/flacfetch-data/transmission

# Verify
echo 'Boot disk files:' && find /var/lib/transmission-daemon/downloads -type f 2>/dev/null | wc -l
echo 'Persistent disk files:' && find /mnt/flacfetch-data/transmission/downloads -type f | wc -l
"
```

## Phase 2: Remove Flacfetch from karaoke-gen Pulumi State (30 min)

### 2.1 Remove Resources from karaoke-gen State (NOT from GCP)

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen/infrastructure
pulumi stack select prod

# Remove each flacfetch resource from state
pulumi state delete 'urn:pulumi:prod::karaoke-gen-infrastructure::gcp:compute/instance:Instance::flacfetch-service' --yes
pulumi state delete 'urn:pulumi:prod::karaoke-gen-infrastructure::gcp:compute/address:Address::flacfetch-ip' --yes
pulumi state delete 'urn:pulumi:prod::karaoke-gen-infrastructure::gcp:serviceaccount/account:Account::flacfetch-sa' --yes
pulumi state delete 'urn:pulumi:prod::karaoke-gen-infrastructure::gcp:compute/firewall:Firewall::flacfetch-bittorrent-firewall' --yes
pulumi state delete 'urn:pulumi:prod::karaoke-gen-infrastructure::gcp:compute/firewall:Firewall::flacfetch-api-firewall' --yes
pulumi state delete 'urn:pulumi:prod::karaoke-gen-infrastructure::gcp:projects/iAMMember:IAMMember::flacfetch-secrets-access' --yes
pulumi state delete 'urn:pulumi:prod::karaoke-gen-infrastructure::gcp:storage/bucketIAMMember:BucketIAMMember::flacfetch-storage-writer' --yes
pulumi state delete 'urn:pulumi:prod::karaoke-gen-infrastructure::gcp:storage/bucketIAMMember:BucketIAMMember::flacfetch-storage-reader' --yes
pulumi state delete 'urn:pulumi:prod::karaoke-gen-infrastructure::gcp:secretmanager/secretVersion:SecretVersion::flacfetch-api-url-internal' --yes

# Keep secrets - they're still used by karaoke-gen backend:
# - flacfetch-api-key (for auth)
# - flacfetch-api-url (endpoint)
```

### 2.2 Remove flacfetch Code from karaoke-gen

**Delete files:**
- `infrastructure/compute/flacfetch_vm.py`
- `infrastructure/compute/startup_scripts/flacfetch.sh`

**Modify files:**

`infrastructure/__main__.py` - Remove lines:
```python
# Remove import
from compute import flacfetch_vm

# Remove instantiation (around lines 247-258)
flacfetch_ip = flacfetch_vm.create_flacfetch_ip()
flacfetch_instance = flacfetch_vm.create_flacfetch_vm(...)
flacfetch_bittorrent_fw, flacfetch_api_fw = flacfetch_vm.create_flacfetch_firewall()
flacfetch_url_version = secrets.create_flacfetch_internal_url_version(...)

# Remove exports (around lines 342-345)
pulumi.export("flacfetch_static_ip", ...)
pulumi.export("flacfetch_service_url", ...)
pulumi.export("flacfetch_service_account", ...)
```

`infrastructure/compute/__init__.py` - Remove flacfetch imports

`infrastructure/modules/iam/worker_sas.py` - Remove:
- `create_flacfetch_service_account()` function
- `grant_flacfetch_permissions()` function

`infrastructure/modules/secrets.py` - Remove:
- `create_flacfetch_internal_url_version()` function

### 2.3 Preview and Apply karaoke-gen Changes

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen/infrastructure
pulumi preview  # Should show only resource removals from management, NO destroys
pulumi up
```

## Phase 3: Update flacfetch Infrastructure (20 min)

### 3.1 Remove Duplicate Secrets from flacfetch

The following secrets are managed by karaoke-gen and used by karaoke-gen backend. Remove them from `flacfetch/infrastructure/__main__.py`:

```python
# REMOVE these (karaoke-gen owns them):
# - flacfetch_api_key_secret
# - flacfetch_api_url_secret
# - red_api_key_secret, red_api_url_secret
# - ops_api_key_secret, ops_api_url_secret

# KEEP these (flacfetch-specific):
# - spotipy_client_id_secret, spotipy_client_secret_secret
# - spotify_oauth_token_secret
# - youtube_cookies_secret
# - pushbullet_api_key_secret
```

### 3.2 Remove Duplicate Secrets from flacfetch Pulumi State

```bash
cd /Users/andrew/Projects/nomadkaraoke/flacfetch/infrastructure
pulumi stack select dev

# Remove secrets that karaoke-gen should own
pulumi state delete 'urn:pulumi:dev::flacfetch-infrastructure::gcp:secretmanager/secret:Secret::flacfetch-api-key' --yes
pulumi state delete 'urn:pulumi:dev::flacfetch-infrastructure::gcp:secretmanager/secret:Secret::flacfetch-api-url' --yes
pulumi state delete 'urn:pulumi:dev::flacfetch-infrastructure::gcp:secretmanager/secret:Secret::red-api-key' --yes
pulumi state delete 'urn:pulumi:dev::flacfetch-infrastructure::gcp:secretmanager/secret:Secret::red-api-url' --yes
pulumi state delete 'urn:pulumi:dev::flacfetch-infrastructure::gcp:secretmanager/secret:Secret::ops-api-key' --yes
pulumi state delete 'urn:pulumi:dev::flacfetch-infrastructure::gcp:secretmanager/secret:Secret::ops-api-url' --yes
```

### 3.3 Refresh flacfetch Stack (Pick Up Disk Attachment)

```bash
cd /Users/andrew/Projects/nomadkaraoke/flacfetch/infrastructure
pulumi refresh  # Updates state to reflect disk attachment from Phase 1
pulumi preview  # Should show minimal changes (startup script update)
```

## Phase 4: Deploy New Startup Script (20 min)

### 4.1 Apply flacfetch Infrastructure

```bash
cd /Users/andrew/Projects/nomadkaraoke/flacfetch/infrastructure
pulumi up
```

### 4.2 Restart VM to Apply Changes

```bash
# Stop and start to run new startup script
gcloud compute instances stop flacfetch-service --zone=us-central1-a --project=nomadkaraoke
gcloud compute instances start flacfetch-service --zone=us-central1-a --project=nomadkaraoke

# Wait for startup and monitor
sleep 60
gcloud compute ssh flacfetch-service --zone=us-central1-a --project=nomadkaraoke \
  --command="sudo tail -50 /var/log/flacfetch-startup.log"
```

### 4.3 Verify Service Health

```bash
# Health check (should show deno_available: true)
curl -s http://104.198.214.26:8080/health | jq '{status, ytdlp}'

# Verify torrents loaded from persistent disk
curl -s http://104.198.214.26:8080/health | jq '.transmission.active_torrents'

# Verify disk mount
gcloud compute ssh flacfetch-service --zone=us-central1-a --project=nomadkaraoke \
  --command="df -h /mnt/flacfetch-data"
```

## Phase 5: Verification and Cleanup (15 min)

### 5.1 Verify karaoke-gen Backend Still Works

```bash
# Check the flacfetch-api-url secret (should still be set)
gcloud secrets versions access latest --secret=flacfetch-api-url --project=nomadkaraoke

# Test audio search (uses flacfetch via VPC connector)
curl -X POST https://api.nomadkaraoke.com/api/audio/search \
  -H "Content-Type: application/json" \
  -d '{"artist": "test", "track": "test"}'
```

### 5.2 Verify Both Stacks Show No Changes

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen/infrastructure
pulumi preview  # Should show: 0 changes

cd /Users/andrew/Projects/nomadkaraoke/flacfetch/infrastructure
pulumi preview  # Should show: 0 changes
```

### 5.3 Clean Up Boot Disk (After Verification)

Only after confirming everything works:

```bash
gcloud compute ssh flacfetch-service --zone=us-central1-a --project=nomadkaraoke --command="
# Verify torrents are running from persistent disk
transmission-remote localhost:9091 -l

# Remove old data from boot disk
sudo rm -rf /var/lib/transmission-daemon/downloads/*
"
```

### 5.4 Commit Changes

```bash
# karaoke-gen repo
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen
git add -A
git commit -m "refactor: Remove flacfetch VM infrastructure (moved to flacfetch repo)"

# flacfetch repo
cd /Users/andrew/Projects/nomadkaraoke/flacfetch
git add -A
git commit -m "refactor: Remove duplicate secrets (managed by karaoke-gen)"
```

## Files to Modify

### karaoke-gen (remove flacfetch)

| File | Action |
|------|--------|
| `infrastructure/compute/flacfetch_vm.py` | DELETE |
| `infrastructure/compute/startup_scripts/flacfetch.sh` | DELETE |
| `infrastructure/__main__.py` | Remove flacfetch VM, firewall, exports |
| `infrastructure/compute/__init__.py` | Remove flacfetch imports |
| `infrastructure/modules/iam/worker_sas.py` | Remove flacfetch SA functions |
| `infrastructure/modules/secrets.py` | Remove `create_flacfetch_internal_url_version()` |

### flacfetch (remove duplicate secrets)

| File | Action |
|------|--------|
| `infrastructure/__main__.py` | Remove 6 duplicate secret definitions |

## Rollback Plan

### If Data Migration Fails (Phase 1)
- Restore from GCS backup
- Detach disk: `gcloud compute instances detach-disk flacfetch-service --disk=flacfetch-data --zone=us-central1-a`

### If State Migration Fails (Phase 2-3)
- Re-import resources: `pulumi import gcp:compute/instance:Instance flacfetch-service ...`
- Restore code from git: `git checkout -- .`

### If Service Breaks (Phase 4)
- Rollback startup script and `pulumi up`
- Or manually restart transmission: `sudo systemctl restart transmission-daemon`

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Torrent data loss | GCS backup before any changes |
| Static IP change | Never delete IP resource, only transfer ownership |
| Service downtime | ~5 min during VM restart, schedule during low usage |
| Pulumi state conflict | Remove from state (not GCP) before transferring |

## Verification Checklist

- [ ] Backup created in GCS
- [ ] Persistent disk attached and mounted
- [ ] Data migrated to persistent disk
- [ ] karaoke-gen state cleaned up
- [ ] karaoke-gen code removed
- [ ] flacfetch duplicate secrets removed
- [ ] flacfetch stack updated
- [ ] VM restarted with new startup script
- [ ] Health check passes (deno_available: true)
- [ ] Torrents loading from persistent disk
- [ ] karaoke-gen backend can reach flacfetch
- [ ] Both Pulumi stacks show 0 changes
