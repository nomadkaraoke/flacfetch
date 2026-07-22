# Flacfetch Infrastructure (Pulumi)

This Pulumi stack manages the **GCP-side resources** for flacfetch.

> **Flacfetch's compute is no longer on GCP.** In the July 2026 GCP-exit migration
> it moved off a Compute Engine VM to a **Netcup VPS** (`flacfetch.nomadkaraoke.com`,
> fronted by Cloudflare), provisioned by [`deploy/provision.sh`](../deploy/README.md).
> This stack now manages only the GCP resources the VPS still uses via a
> service-account key.

## What this stack manages

```
GCP project (nomadkaraoke)
├── Service account: flacfetch-service
│   ├── roles/secretmanager.secretAccessor           (project-wide: read all secrets)
│   ├── roles/storage.objectCreator + objectViewer   (bucket: karaoke-gen-storage-nomadkaraoke)
│   └── roles/secretmanager.secretVersionManager      (on youtube-cookies + spotify-oauth-token
│                                                        so the credential keeper can rotate them)
└── Secret Manager secrets (values populated out-of-band):
    red-api-key/url, ops-api-key/url, spotipy-client-id/secret,
    youtube-cookies, spotify-oauth-token, pushbullet-api-key,
    flacfetch-account-email, flacfetch-account-password
```

The VPS authenticates to GCS + Secret Manager with a **key for `flacfetch-service`**
at `/etc/flacfetch/gcs-sa.json` (created out-of-band, not by Pulumi). Static secrets
are also copied into `/etc/flacfetch/flacfetch.env`; the rotating credentials stay in
Secret Manager and the on-box keeper writes refreshed values back.

> `flacfetch-api-key` and `flacfetch-api-url` are managed by **karaoke-gen's** stack,
> not here — karaoke-gen is the consumer of the flacfetch API.

## Deploy

```bash
cd infrastructure
python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt   # first time
pulumi preview          # review changes
pulumi up               # apply
```

Stack: `flacfetch-infrastructure` (project/region in `Pulumi.dev.yaml`).

## Compute / runtime

The VPS stack (Transmission seedbox + FastAPI + headed-Chromium credential keeper,
transmission 4.1.3 built from source, `:8080` locked to Cloudflare) lives entirely in
[`deploy/`](../deploy/README.md). See the design note in `docs/archive/` for the
migration rationale and cutover sequencing.
