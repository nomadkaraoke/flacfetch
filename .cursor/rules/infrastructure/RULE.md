---
description: "Infrastructure guidelines for GCP resources managed via Pulumi IaC"
alwaysApply: true
---

# Infrastructure as Code (IaC) Guidelines

## Pulumi is the Source of Truth

All infrastructure changes for this project **must** be made using Pulumi IaC, located in the `infrastructure/` directory. This includes:

- Compute Engine instances
- Persistent disks
- Static IPs
- Firewall rules
- Service accounts
- IAM bindings
- Secrets in Secret Manager

## Using gcloud CLI

The `gcloud` CLI is acceptable for:

- ✅ **Querying resources** - `gcloud compute instances list`, `gcloud secrets versions list`, etc.
- ✅ **Investigating issues** - SSH into VMs, checking logs, describing resources
- ✅ **Temporary/emergency changes** - Quick fixes that need immediate deployment

However, **any temporary changes made via gcloud must be**:

1. Cleaned up after the investigation/emergency is resolved
2. Reflected in the Pulumi code (`infrastructure/__main__.py`) if they should be permanent
3. Applied via `pulumi up` to ensure state consistency

## Making Infrastructure Changes

1. Navigate to `infrastructure/` directory
2. Activate the venv: `source venv/bin/activate`
3. Make changes to `__main__.py`
4. Preview: `pulumi preview`
5. Apply: `pulumi up`

## Authentication Issues

If you encounter authentication errors with `gcloud` or `pulumi` (e.g., expired credentials, permission denied), **STOP** and ask the user to re-authenticate in their external terminal:

```bash
# For gcloud
gcloud auth login
gcloud auth application-default login

# For Pulumi
pulumi login
```

Do not attempt to run auth commands yourself - the user needs to complete the interactive OAuth flow in their own terminal.

## Key Resources Reference

| Resource | Pulumi Name | GCP Path |
|----------|-------------|----------|
| VM | `flacfetch-service` | `us-central1-a/instances/flacfetch-service` |
| Data Disk | `flacfetch-data` | `us-central1-a/disks/flacfetch-data` |
| Static IP | `flacfetch-static-ip` | `us-central1/addresses/flacfetch-static-ip` |
| Service Account | `flacfetch-sa` | `flacfetch-service@nomadkaraoke.iam.gserviceaccount.com` |

## GCP Project

- Project ID: `nomadkaraoke`
- Region: `us-central1`
- Zone: `us-central1-a`

