# flacfetch `deploy/provision.sh` — design note (Netcup VPS port)

> Companion to the workspace execution plan
> `nomadkaraoke/docs/archive/2026-06-14-flacfetch-gcp-exit-execution-plan.md`.
> This note records *how* the GCE startup script (`infrastructure/__main__.py`,
> `STARTUP_SCRIPT`) was ported into a portable, idempotent provisioner for a single
> root VPS (Netcup VPS 1000 G12).

## Validation that justified this work (2026-06-18)

A throwaway smoke test (`nomadkaraoke/netcup-vps-test/test-setup.sh`) ran on the
provisioned box (`flacup.nomadkaraoke.com`, `152.53.245.109`, Debian 13 trixie, 4
vCPU / 7.8 GiB / 256 GB). **All stages green:**

- **Stage 4 (make-or-break):** headed Patchright Chromium launched under Xvfb and
  loaded `accounts.google.com` (`title='Sign in - Google Accounts'`), `ldd` clean.
  This is the thing seedboxes can't do → Netcup can host the whole stack.
- **Stage 7:** YouTube served metadata to the raw IP (no bot wall).
- apt deps installed with the *plain* package names (no `t64` fallback needed on
  trixie); `flacfetch[api,spotify,keeper,ejs]` built; transmission + Deno fine.

## Environment delta vs prod GCE

| | Prod GCE | Netcup VPS |
|---|---|---|
| OS | Debian 12 | **Debian 13 (trixie)** |
| Data disk | separate `/dev/sdb` (attached PD) | **partition `/dev/vda5`** carved from free space on the single 256 GB disk |
| Identity/auth | VM's attached SA (ADC via metadata) | **SA JSON key** at `/etc/flacfetch/gcs-sa.json` → `GOOGLE_APPLICATION_CREDENTIALS` |
| Static secrets | `gcloud secrets versions access` at boot | **`/etc/flacfetch/flacfetch.env`** (operator-populated once) |
| Rotating secrets (YT cookies, Spotify token) | Secret Manager (read + writeback) | **unchanged — still Secret Manager** via the SA key |
| librespot binary | `gsutil cp gs://…/binaries/…` | python `google-cloud-storage` download via SA key |
| `gcloud`/`gsutil` on box | yes (Cloud SDK) | **no** — use the `google-cloud-*` libs already in the venv |

## Decisions

1. **Secret Manager stays for the rotating credentials.** flacfetch's credential
   keeper writes refreshed YouTube cookies and the Spotify OAuth token *back* to
   Secret Manager at runtime (`api/services/credential_check.py`, prune fix in #29).
   Fully severing it would require changing the keeper to persist locally — out of
   scope and risky. Split adopted: **static → `.env`, rotating → Secret Manager (SA
   key)**. Consistent with the plan's own "off GCP = off GCE, not off GCS" framing.
2. **One SA key for everything GCP** (`flacfetch-service`, which already holds GCS
   object create/view + `secretVersionManager` on the two rotating secrets). Placed
   at `/etc/flacfetch/gcs-sa.json`, exported as `GOOGLE_APPLICATION_CREDENTIALS` in
   every service unit that touches GCS or Secret Manager.
3. **Data partition, not a second disk.** `provision.sh` idempotently creates
   `/dev/vda5` from the contiguous free space after `vda4` (sgdisk; never touches
   vda1–4), `mkfs.ext4` labelled `flacfetch-data`, mounts `/mnt/flacfetch-data`,
   adds an fstab entry by UUID with `nofail`. Re-runs detect the existing
   partition/label and skip creation/format.
4. **Services run as `root`** (matches the proven prod keeper, which runs headed
   Chrome `--no-sandbox`). A non-root `andrew` sudo user is created for SSH login
   hygiene. De-rooting the service units is a noted follow-up.
5. **Idempotent + secret-optional.** Running `provision.sh` with no `.env`/SA key
   installs all infra (partition, deps, venv, Chromium, transmission, Deno, systemd
   units) and *skips* credentialed services with warnings — same posture as the GCE
   script's empty-secret guards. Placing the creds and re-running brings the
   credentialed services up. Safe to run repeatedly.

## Layout produced

```
/etc/flacfetch/flacfetch.env     # static secrets (operator-populated, chmod 600)
/etc/flacfetch/runtime.env       # secrets the units read via EnvironmentFile= (chmod 600, generated)
/etc/flacfetch/gcs-sa.json       # flacfetch-service SA key (operator-placed, chmod 600)
/opt/flacfetch/                  # git checkout + venv
/mnt/flacfetch-data/             # data partition (vda5)
  ├── transmission/{downloads,.incomplete,config/{torrents,resume}}
  └── browser-profiles/google/   # persistent Chrome profile (the crown jewel)
```

Systemd units (ported 1:1 from the GCE script): `flacfetch`, `xvfb`,
`credential-keeper`, `ytdlp-update.timer`, `flacfetch-credential-check.timer`.

**Secrets are NOT inlined in the unit files.** The GCE script embedded
`Environment="RED_API_KEY=…"` directly in each `.service` — and systemd writes
those world-readable (644), exposing them to any local user. Here the secrets are
written to `runtime.env` (chmod 600, root) and pulled in with
`EnvironmentFile=-/etc/flacfetch/runtime.env`; only non-secret config stays inline.
(Worth back-porting this hardening to the GCE box too.)

## Cutover sequencing (conflict-aware) — `flacup` is the future prod box

`flacup.nomadkaraoke.com` will replace the GCE box, but the old box keeps serving
karaoke-gen until a controlled cutover (targeted for a weekend, not interrupting
live traffic). The one thing that **cannot run on both boxes at once**:

> **The credential keeper is a single-writer.** Two keepers would log into the same
> `nomadflacfetch@gmail.com` from two IPs (Google security flags) *and* both write
> rotated `youtube-cookies` / `spotify-oauth-token` versions back to Secret Manager,
> racing + churning the keep-newest-5 prune. The daily `flacfetch-credential-check`
> timer can also refresh+writeback, so it's gated the same way.

Provisioner support: **`FF_ENABLE_KEEPER=false`** (staging default for pre-cutover)
writes the keeper + cred-check units but leaves them stopped. Set `true` only at
cutover, *after* the old box's keeper is stopped.

**Stage now / this weekend (zero conflict — old box untouched):**
1. Enable **2FA on RED + OPS** (plan §3.1) so the new IP is a non-event for trackers.
2. Create a `flacfetch-service` SA key; place `/etc/flacfetch/gcs-sa.json` +
   `/etc/flacfetch/flacfetch.env` (from Secret Manager) on `flacup`.
3. `sudo FF_ENABLE_KEEPER=false bash deploy/provision.sh` → flacfetch API up, GCS
   reachable, **no keeper, no torrents** (transmission empty until cutover, so no
   tracker announce from the new IP).
4. Validate the non-conflicting bits: `/health` + `/health/deep`, a GCS read/write
   round-trip, and the Spotify localhost-OAuth redirect sanity (plan §4.6).

**Cutover (when ready):**
1. **Stop the GCE keeper** (`systemctl stop credential-keeper` on the old box) — now
   there is a single writer.
2. `rsync` transmission state + downloads **and the logged-in Chrome profile**
   (`/mnt/flacfetch-data/browser-profiles/google/`) old → new (copying the warm
   profile beats a fresh login from a new IP, plan §3.6).
3. `sudo FF_ENABLE_KEEPER=true bash deploy/provision.sh` on `flacup` → keeper +
   cred-check come up; confirm cookie + Spotify refresh succeed.
4. Repoint karaoke-gen's flacfetch base URL → `flacup.nomadkaraoke.com`; run one
   end-to-end job; watch RED/OPS ratio.
5. Bake in parallel a few days, then decommission the GCE box (plan §4.9).

## Follow-ups (not in v1)

- Host firewall (ufw/nftables) — lock `:8080` to karaoke-gen egress and/or front
  with TLS at `flacup.nomadkaraoke.com`. v1 leaves app-level API key as the gate.
- De-root the `flacfetch`/keeper service units.
- Cutover data move (`rsync` transmission state), tracker 2FA, karaoke-gen repoint
  — these live in the execution plan §4, run at actual migration time.
