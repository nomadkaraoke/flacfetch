#!/usr/bin/env python3
"""Copy .torrent files Transmission already holds into flacfetch's torrent cache.

flacfetch serves a torrent from its cache (``~flacfetch/.flacfetch/cache/<red|ops>/
<torrent id>.torrent``) before asking the tracker, and RED allows only four API
downloads of each .torrent ever. Torrents added before the cache existed (or on
a previous host) are loaded in Transmission but missing from the cache, so a
re-request would spend a tracker download. This fills those gaps. It never
contacts a tracker and never overwrites a cached file.

Transmission keeps its copies mode 0600 as debian-transmission, so run as root:

    sudo /opt/flacfetch/venv/bin/python deploy/backfill_torrent_cache.py --dry-run
    sudo /opt/flacfetch/venv/bin/python deploy/backfill_torrent_cache.py

The torrent ID comes from the comment trackers write into each .torrent
(``https://<tracker>/torrents.php?torrentid=<id>``); the tracker host picks the
cache subdir via RED_API_URL / OPS_API_URL (read from /etc/flacfetch/runtime.env).
"""

import argparse
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse

import transmission_rpc

RUNTIME_ENV = Path("/etc/flacfetch/runtime.env")
TORRENT_ID_RE = re.compile(r"torrentid=(\d+)")


def _env(name):
    if os.environ.get(name):
        return os.environ[name]
    if RUNTIME_ENV.exists():
        for line in RUNTIME_ENV.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == name:
                return value.strip().strip("'\"")
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache-root", default="/opt/flacfetch/.flacfetch/cache")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=9091)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    subdirs = {}
    for env_name, subdir in (("RED_API_URL", "red"), ("OPS_API_URL", "ops")):
        url = _env(env_name)
        if url:
            subdirs[urlparse(url).hostname] = subdir
    if not subdirs:
        sys.exit("No RED_API_URL / OPS_API_URL found; cannot map trackers to cache dirs")

    client = transmission_rpc.Client(host=args.host, port=args.port)
    torrents = client.get_torrents(arguments=["id", "name", "comment", "torrentFile"])

    copied = present = skipped = failed = 0
    for t in torrents:
        comment = t.comment or ""
        match = TORRENT_ID_RE.search(comment)
        subdir = subdirs.get(urlparse(comment).hostname) if match else None
        if not subdir:
            skipped += 1
            continue
        cache_dir = Path(args.cache_root) / subdir
        dest = cache_dir / f"{match.group(1)}.torrent"
        if dest.exists() and dest.stat().st_size > 0:
            present += 1
            continue
        source = Path(t.torrent_file)
        if not source.exists():
            print(f"missing in Transmission: {source} ({t.name})")
            continue
        print(f"{'would copy' if args.dry_run else 'copy'} {source.name} -> {dest}  ({t.name})")
        if not args.dry_run:
            # Copy under a temp name and rename, so flacfetch never reads a partial file.
            tmp = dest.with_suffix(".torrent.tmp")
            try:
                cache_dir.mkdir(parents=True, exist_ok=True)
                stat = cache_dir.stat()
                shutil.copyfile(source, tmp)
                os.chown(tmp, stat.st_uid, stat.st_gid)
                os.chmod(tmp, 0o664)
                os.replace(tmp, dest)
            except OSError as e:
                print(f"failed to copy {source} -> {dest}: {e}")
                tmp.unlink(missing_ok=True)
                failed += 1
                continue
        copied += 1

    verb = "would copy" if args.dry_run else "copied"
    print(f"{verb} {copied}, already cached {present}, not a known tracker {skipped}, failed {failed}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
