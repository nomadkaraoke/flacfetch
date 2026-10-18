# flacfetch deploy — single root VPS

Portable, idempotent provisioner for running the full flacfetch stack
(Transmission seedbox + FastAPI + headed-Chromium credential keeper) on a single
root VPS. This replaces the GCE startup script (`infrastructure/__main__.py`)
when flacfetch moves off Google Compute Engine.

See `../docs/archive/2026-06-18-netcup-provision-design.md` for the design and
the GCP→VPS deltas, and the workspace plan
`nomadkaraoke/docs/archive/2026-06-14-flacfetch-gcp-exit-execution-plan.md` for
the migration cutover.

## Files

| File | Purpose |
|---|---|
| `provision.sh` | The provisioner. Run as root, re-runnable. |
| `_sm_get.py` | Fetch a Secret Manager secret via the SA key (no gcloud needed). |
| `_gcs_get.py` | Download one GCS object via the SA key (librespot binary). |
| `flacfetch.env.example` | Template for the static secrets. |

## Usage

```bash
# 1. (first run only) create the data partition + deps + app + units.
#    With no secrets yet, credentialed services are created but not started.
sudo bash deploy/provision.sh

# 2. Seed credentials (one time):
sudo install -d -m 700 /etc/flacfetch
sudo cp deploy/flacfetch.env.example /etc/flacfetch/flacfetch.env
sudo nano /etc/flacfetch/flacfetch.env            # fill in from Secret Manager
sudo chmod 600 /etc/flacfetch/flacfetch.env
# place the flacfetch-service SA key:
sudo cp /path/to/flacfetch-sa.json /etc/flacfetch/gcs-sa.json
sudo chmod 600 /etc/flacfetch/gcs-sa.json

# 3. Re-run to bring up the credentialed services:
sudo bash deploy/provision.sh
```

### Optional: create a non-root sudo login user

```bash
sudo FF_ADMIN_USER=andrew \
     FF_ADMIN_PUBKEY='ssh-ed25519 AAAA... you@host' \
     bash deploy/provision.sh
```

### Env knobs

`FF_GIT_REF` (default `main`), `FF_REPO`, `FF_GCP_PROJECT` (`nomadkaraoke`),
`FF_GCS_BUCKET`, `FF_MIN_DATA_GB` (default `50`), `LIBRESPOT_VERSION` (`0.8.0`),
`FF_ADMIN_USER`, `FF_ADMIN_PUBKEY`, `FF_ENABLE_KEEPER` (default `true`).

### Staging vs cutover (`FF_ENABLE_KEEPER`)

The credential keeper is a **single-writer** — it must not run while another box's
keeper is live (shared Google account + rotating Secret Manager secrets). While the
old box is still serving:

```bash
sudo FF_ENABLE_KEEPER=false bash deploy/provision.sh   # stage: everything up except keeper
```

At cutover, after stopping the old box's keeper, re-run with `FF_ENABLE_KEEPER=true`.
Full sequencing in `../docs/archive/2026-06-18-netcup-provision-design.md`.

## Safety notes

- The data-partition step **only** creates a new partition in free space and
  **never** mkfs's a device that already has a filesystem — safe to re-run.
- `flacfetch`, `credential-keeper` and the cred-check timer run as the dedicated
  non-root **`flacfetch`** service user (override with `FF_SERVICE_USER`). It is
  added to the `debian-transmission` group and the download tree is group-shared
  + setgid so both processes read/write completed files. Only `xvfb` and the
  yt-dlp updater (which restarts services) stay `root`. The keeper still runs
  headed Chrome `--no-sandbox`; its browser is installed into
  `$APP_DIR/ms-playwright` (`PLAYWRIGHT_BROWSERS_PATH`) so the non-root user can
  find it. A host firewall on `:8080` is handled at the Cloudflare edge / nftables.
- `transmission-daemon`, `flacfetch` and `credential-keeper` have
  `RequiresMountsFor=/mnt/flacfetch-data`. The partition is `nofail` in fstab so a
  bad disk can't block boot; the services fail visibly instead of running on root.

## Disk space

The root fs is small (~8.6 GB on netcup VPS 1000); torrents, downloads and the
browser profile live on `/mnt/flacfetch-data`. Stage 1a keeps root from creeping:
no cached `.deb`s (`/etc/apt/apt.conf.d/99flacfetch-no-deb-cache`), journal capped
at 300 MB (`/etc/systemd/journald.conf.d/flacfetch.conf`), weekly logrotate of
`/var/log/flacfetch-*.log`, no pip cache, and the legacy `/root/.cache/ms-playwright`
removed.

`flacfetch-disk-watch.timer` checks both filesystems hourly and sends a Pushbullet
alert when either crosses 85% (`FF_DISK_ALERT_PCT` in `runtime.env`), at most once a
day per filesystem. Log: `/var/log/flacfetch-disk-watch.log`.

If it fires:

```bash
ssh flacup 'df -h / /mnt/flacfetch-data; du -xh --max-depth=2 / | sort -rh | head -20'
ssh flacup 'apt-get clean; journalctl --vacuum-size=200M'   # root fs
```

On the data disk, seeded torrents (`KEEP_SEEDING=true`) grow ~3-4 GB/month; prune
the oldest/least-seeded torrents in Transmission (remove *with* data) rather than
deleting files under `downloads/` by hand.
