# Flacfetch Infrastructure

This directory contains Pulumi infrastructure code that manages all Google Cloud Platform resources for the flacfetch audio download service.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         GCP Project                                  │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │                    Compute Engine VM                            │ │
│  │                   (flacfetch-service)                           │ │
│  │                                                                  │ │
│  │  ┌──────────────┐    ┌────────────────┐    ┌───────────────┐   │ │
│  │  │ Transmission │◄──►│ Flacfetch API  │◄──►│ GCS Bucket    │   │ │
│  │  │   Daemon     │    │   :8080        │    │ (uploads)     │   │ │
│  │  └──────────────┘    └────────────────┘    └───────────────┘   │ │
│  │                            ▲                                     │ │
│  │                            │ uploads cookies/tokens              │ │
│  │  ┌─────────────────────────┴──────────────────────────────────┐ │ │
│  │  │  Credential Keeper (Patchright + Xvfb)                      │ │ │
│  │  │  Maintains YouTube cookies & Spotify OAuth via browser      │ │ │
│  │  └────────────────────────────────────────────────────────────┘ │ │
│  │         │                                                        │ │
│  │         ▼                                                        │ │
│  │  ┌────────────────────────────────────────────────────────────┐ │ │
│  │  │              Persistent Data Disk (50GB)                    │ │ │
│  │  │              /mnt/flacfetch-data                            │ │ │
│  │  │                                                              │ │ │
│  │  │  • /transmission/downloads/     - Downloaded files          │ │ │
│  │  │  • /transmission/config/torrents/ - .torrent metadata       │ │ │
│  │  │  • /transmission/config/resume/   - Download state          │ │ │
│  │  │  • /browser-profiles/google/    - Persistent browser session│ │ │
│  │  │                                                              │ │ │
│  │  │  ⚠️  This disk has autoDelete=false                         │ │ │
│  │  │     Torrent data survives VM deletion!                      │ │ │
│  │  └────────────────────────────────────────────────────────────┘ │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                              │                                       │
│                              │ Static IP: 104.198.214.26            │
│                              ▼                                       │
│                     ┌─────────────────┐                             │
│                     │   Firewall      │                             │
│                     │ • :8080 (API)   │                             │
│                     │ • :51413 (P2P)  │                             │
│                     └─────────────────┘                             │
└─────────────────────────────────────────────────────────────────────┘
```

## Resources Managed

| Resource | Type | Purpose |
|----------|------|---------|
| `flacfetch-service` | Compute Instance | VM running Transmission + flacfetch API |
| `flacfetch-data` | Persistent Disk | Torrent data storage (survives VM deletion) |
| `flacfetch-static-ip` | Static IP | Fixed IP for tracker whitelist |
| `flacfetch-service` | Service Account | IAM identity for the VM |
| `flacfetch-firewall` | Firewall Rule | Allow API + BitTorrent traffic |
| `flacfetch-api-key` | Secret | API authentication key |
| `red-api-key/url` | Secrets | RED tracker API credentials |
| `ops-api-key/url` | Secrets | OPS tracker API credentials |
| `flacfetch-account-email` | Secret | Google account for credential keeper |
| `flacfetch-account-password` | Secret | Google account password |

## Prerequisites

1. **Pulumi CLI**: Install with `brew install pulumi`
2. **Pulumi Account**: Sign up at https://app.pulumi.com
3. **Google Cloud SDK**: `gcloud` authenticated with project access
4. **Python 3.10+**: For running Pulumi

## Initial Setup

```bash
cd infrastructure

# Create Python virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Login to Pulumi
pulumi login

# Initialize the stack (first time only)
pulumi stack init dev

# Set GCP configuration
pulumi config set gcp:project nomadkaraoke
pulumi config set gcp:region us-central1
```

## Importing Existing Resources

Since the infrastructure already exists in GCP, we need to import it into Pulumi state:

```bash
# Import in this order (dependencies first):

# 1. Static IP
pulumi import gcp:compute/address:Address flacfetch-static-ip \
  projects/nomadkaraoke/regions/us-central1/addresses/flacfetch-static-ip

# 2. Service Account
pulumi import gcp:serviceaccount/account:Account flacfetch-sa \
  projects/nomadkaraoke/serviceAccounts/flacfetch-service@nomadkaraoke.iam.gserviceaccount.com

# 3. Persistent Data Disk
pulumi import gcp:compute/disk:Disk flacfetch-data \
  projects/nomadkaraoke/zones/us-central1-a/disks/flacfetch-data

# 4. Secrets (import each)
pulumi import gcp:secretmanager/secret:Secret flacfetch-api-key \
  projects/nomadkaraoke/secrets/flacfetch-api-key
pulumi import gcp:secretmanager/secret:Secret flacfetch-api-url \
  projects/nomadkaraoke/secrets/flacfetch-api-url
pulumi import gcp:secretmanager/secret:Secret red-api-key \
  projects/nomadkaraoke/secrets/red-api-key
pulumi import gcp:secretmanager/secret:Secret red-api-url \
  projects/nomadkaraoke/secrets/red-api-url
pulumi import gcp:secretmanager/secret:Secret ops-api-key \
  projects/nomadkaraoke/secrets/ops-api-key
pulumi import gcp:secretmanager/secret:Secret ops-api-url \
  projects/nomadkaraoke/secrets/ops-api-url

# 5. VM Instance
pulumi import gcp:compute/instance:Instance flacfetch-service \
  projects/nomadkaraoke/zones/us-central1-a/instances/flacfetch-service

# 6. Firewall Rule
pulumi import gcp:compute/firewall:Firewall flacfetch-firewall \
  projects/nomadkaraoke/global/firewalls/flacfetch-firewall

# 7. IAM Bindings (may need to be recreated rather than imported)
```

## Usage

### Preview Changes

```bash
pulumi preview
```

Shows what will be created/updated/deleted without making changes.

### Apply Changes

```bash
pulumi up
```

Applies infrastructure changes after confirmation.

### View Current State

```bash
pulumi stack output
```

Shows exported values (IP address, service URL, etc.)

### Redeploy VM (Without Losing Data)

To redeploy the VM while preserving torrent data:

```bash
# The persistent disk (flacfetch-data) will NOT be deleted
pulumi destroy --target "urn:pulumi:dev::flacfetch-infrastructure::gcp:compute/instance:Instance::flacfetch-service"

# Then recreate
pulumi up
```

The persistent data disk has `autoDelete=false` so it survives VM deletion.

### Update Startup Script

Edit the `STARTUP_SCRIPT` in `__main__.py`, then:

```bash
pulumi up
```

The VM will be updated with the new startup script (may require restart).

## Secret Management

Secrets are stored in GCP Secret Manager. To update values:

```bash
# Update flacfetch API key
echo -n "your-new-key" | gcloud secrets versions add flacfetch-api-key --data-file=-

# Update RED API credentials
echo -n "your-red-key" | gcloud secrets versions add red-api-key --data-file=-
echo -n "https://your.red.url" | gcloud secrets versions add red-api-url --data-file=-

# Update OPS API credentials
echo -n "your-ops-key" | gcloud secrets versions add ops-api-key --data-file=-
echo -n "https://your.ops.url" | gcloud secrets versions add ops-api-url --data-file=-

# Update credential keeper account (for browser automation)
echo -n "nomadflacfetch@gmail.com" | gcloud secrets versions add flacfetch-account-email --data-file=-
echo -n "your-google-password" | gcloud secrets versions add flacfetch-account-password --data-file=-
```

## Monitoring

Check service health:

```bash
# Health endpoint (public)
curl http://104.198.214.26:8080/health

# Torrent summary (public)
curl http://104.198.214.26:8080/torrents/summary

# Full torrent list (requires API key)
curl http://104.198.214.26:8080/torrents -H "X-API-Key: YOUR_KEY"
```

View VM logs:

```bash
# Flacfetch API logs
gcloud compute ssh flacfetch-service --zone=us-central1-a \
  --command="sudo journalctl -u flacfetch -f"

# Credential keeper logs
gcloud compute ssh flacfetch-service --zone=us-central1-a \
  --command="sudo tail -f /var/log/flacfetch-credential-keeper.log"
```

## Troubleshooting

### "Resource already exists"

If a resource was created outside of Pulumi, import it:

```bash
pulumi import gcp:compute/instance:Instance flacfetch-service \
  projects/nomadkaraoke/zones/us-central1-a/instances/flacfetch-service
```

### VM Not Starting

Check the startup script logs:

```bash
gcloud compute ssh flacfetch-service --zone=us-central1-a \
  --command="sudo cat /var/log/flacfetch-startup.log"
```

### Persistent Disk Not Mounting

Verify the disk is attached:

```bash
gcloud compute instances describe flacfetch-service --zone=us-central1-a \
  --format="yaml(disks)"
```

### State Conflicts

If multiple people are working on the infrastructure:

```bash
pulumi refresh  # Sync state with actual cloud resources
```

## Cost Estimate

| Resource | Monthly Cost (approx) |
|----------|----------------------|
| e2-medium VM (24/7) | ~$24 |
| 30GB SSD boot disk | ~$5 |
| 50GB standard data disk | ~$2 |
| Static IP | ~$3 |
| **Total** | **~$34/month** |

## Integration with karaoke-gen

This infrastructure shares the GCS bucket (`karaoke-gen-storage-nomadkaraoke`) with the karaoke-gen backend. The flacfetch service uploads downloaded audio files to this bucket for use by the karaoke generator.

Some secrets (like `red-api-key`, `ops-api-key`) may also be defined in the karaoke-gen infrastructure. When migrating, ensure you don't have duplicate resource definitions.

