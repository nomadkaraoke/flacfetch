#!/usr/bin/env python3
"""Print the latest value of a Secret Manager secret to stdout.

Used by provision.sh to seed rotating credentials (youtube-cookies,
spotify-oauth-token) on a non-GCP host. Auth via GOOGLE_APPLICATION_CREDENTIALS
(the flacfetch-service SA key). Project from GOOGLE_CLOUD_PROJECT.

Usage:  python _sm_get.py <secret-id>
Exits 0 and prints the payload on success; exits non-zero (silent) otherwise.
"""
import os
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: _sm_get.py <secret-id>", file=sys.stderr)
        return 2
    secret_id = sys.argv[1]
    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT")
    if not project:
        print("GOOGLE_CLOUD_PROJECT not set", file=sys.stderr)
        return 3
    try:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project}/secrets/{secret_id}/versions/latest"
        resp = client.access_secret_version(request={"name": name})
        sys.stdout.buffer.write(resp.payload.data)
        return 0
    except Exception as exc:  # noqa: BLE001 - provisioner treats any failure as "absent"
        print(f"secret fetch failed for {secret_id}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
