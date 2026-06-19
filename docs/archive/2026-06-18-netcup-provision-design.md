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
/etc/flacfetch/gcs-sa.json       # flacfetch-service SA key (operator-placed, chmod 600)
/opt/flacfetch/                  # git checkout + venv
/mnt/flacfetch-data/             # data partition (vda5)
  ├── transmission/{downloads,.incomplete,config/{torrents,resume}}
  └── browser-profiles/google/   # persistent Chrome profile (the crown jewel)
```

Systemd units (ported 1:1 from the GCE script): `flacfetch`, `xvfb`,
`credential-keeper`, `ytdlp-update.timer`, `flacfetch-credential-check.timer`.

## Follow-ups (not in v1)

- Host firewall (ufw/nftables) — lock `:8080` to karaoke-gen egress and/or front
  with TLS at `flacup.nomadkaraoke.com`. v1 leaves app-level API key as the gate.
- De-root the `flacfetch`/keeper service units.
- Cutover data move (`rsync` transmission state), tracker 2FA, karaoke-gen repoint
  — these live in the execution plan §4, run at actual migration time.
