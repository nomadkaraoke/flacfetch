#!/usr/bin/env python3
"""Download a single object from a GCS bucket to a local path.

Used by provision.sh to fetch the pre-compiled librespot binary without
needing the gcloud SDK on the box. Auth via GOOGLE_APPLICATION_CREDENTIALS.

Usage:  python _gcs_get.py <bucket> <object-path> <dest-path>
Exits 0 on success, non-zero otherwise.
"""
import sys


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: _gcs_get.py <bucket> <object> <dest>", file=sys.stderr)
        return 2
    bucket_name, obj, dest = sys.argv[1], sys.argv[2], sys.argv[3]
    try:
        from google.cloud import storage

        client = storage.Client()
        client.bucket(bucket_name).blob(obj).download_to_filename(dest)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"gcs download failed gs://{bucket_name}/{obj}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
