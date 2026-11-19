"""Create a local, immutable version snapshot (no git required).

A snapshot is the answer to "what exactly did version X look like?" for
operators whose machine has no git binary. It copies documentation and the
release ledgers into ``release/snapshots/<version>/`` and writes a
``MANIFEST.json`` with sha256 for every file. Snapshots are never overwritten:
re-running for an existing version creates ``<version>-r2`` instead.

Usage:
    python tools/snapshot_version.py --version 1.1.0 [--note "runtime hardening"]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP_ROOT = os.path.join(ROOT, "release", "snapshots")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _next_slot(base: str) -> str:
    if not os.path.exists(base):
        return base
    n = 2
    while os.path.exists(f"{base}-r{n}"):
        n += 1
    return f"{base}-r{n}"


def create(version: str, note: str = "") -> dict:
    dest = _next_slot(os.path.join(SNAP_ROOT, f"v{version}"))
    os.makedirs(dest, exist_ok=True)
    for rel in ("docs/human", "docs/memory", "docs/handover"):
        src = os.path.join(ROOT, rel)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(dest, rel),
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for rel in ("release/versions.yaml", "release/release.yaml", "CHANGELOG.md"):
        src = os.path.join(ROOT, rel)
        if os.path.exists(src):
            target = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(src, target)
    files = []
    for base, dirs, names in os.walk(dest):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in sorted(names):
            if name == "MANIFEST.json":
                continue
            p = os.path.join(base, name)
            files.append({"path": os.path.relpath(p, dest).replace(os.sep, "/"),
                          "sha256": sha256(p), "size": os.path.getsize(p)})
    manifest = {"format": "ao-version-snapshot/1", "version": version,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "note": note, "files": sorted(files, key=lambda f: f["path"])}
    with open(os.path.join(dest, "MANIFEST.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    return {"ok": True, "dir": dest, "files": len(files), "version": version}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--note", default="")
    args = ap.parse_args()
    print(json.dumps(create(args.version, args.note), ensure_ascii=False, indent=2))
