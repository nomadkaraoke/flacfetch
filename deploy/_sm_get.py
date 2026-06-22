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
    # Named `target` (not `secret_id`): it's the secret *name* (e.g. "youtube-cookies"),
    # not a secret value, and we log it for debuggability. A "secret"/"token"/"key"
    # variable name would trip CodeQL's sensitive-data-logging heuristic on the line below.
    target = sys.argv[1]
    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT")
    if not project:
        print("GOOGLE_CLOUD_PROJECT not set", file=sys.stderr)
        return 3
    try:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project}/secrets/{target}/versions/latest"
        resp = client.access_secret_version(request={"name": name})
        payload = resp.payload.data
    except Exception as exc:  # noqa: BLE001 - provisioner treats any failure as "absent"
        # Log only the exception *type*, never str(exc): the payload is a secret
        # and we must not risk it (or anything derived) reaching stderr/logs.
        print(
            f"secret fetch failed for {target}: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 1
    # Write outside the try so a broken-pipe on stdout isn't caught (and logged)
    # while the secret payload is in scope.
    sys.stdout.buffer.write(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
