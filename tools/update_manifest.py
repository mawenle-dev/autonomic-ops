"""Regenerate release/updates/manifest.json from the .zip files beside it.

The update channel is deliberately dumb: a folder of immutable zips plus a
manifest of sha256 values. The release studio can *check* and *stage* an
update, but never applies it while the app is running (a running EXE cannot
replace itself on Windows). Staged packages live under updates/staged/ and are
activated by closing the app and swapping the portable folder.

Usage:
    python tools/update_manifest.py            # rebuild manifest
    python tools/update_manifest.py --check    # verify existing packages
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPD = os.path.join(ROOT, "release", "updates")
VER_RE = re.compile(r"(\d+\.\d+\.\d+)")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def rebuild() -> dict:
    os.makedirs(UPD, exist_ok=True)
    items = []
    for name in sorted(os.listdir(UPD)):
        if not name.endswith(".zip"):
            continue
        m = VER_RE.search(name)
        if not m:
            continue
        path = os.path.join(UPD, name)
        items.append({"version": m.group(1), "file": name,
                      "sha256": sha256(path), "size": os.path.getsize(path)})
    manifest = {"format": "ao-update-manifest/1", "updates": items}
    with open(os.path.join(UPD, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    return {"ok": True, "updates": len(items), "dir": UPD}


def check() -> dict:
    p = os.path.join(UPD, "manifest.json")
    if not os.path.exists(p):
        return {"ok": False, "reason": "manifest_missing"}
    with open(p, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    bad = []
    for item in data.get("updates", []):
        path = os.path.join(UPD, item.get("file", ""))
        if not os.path.exists(path) or sha256(path) != item.get("sha256"):
            bad.append(item.get("file"))
    return {"ok": not bad, "updates": len(data.get("updates", [])), "bad": bad}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    print(json.dumps(check() if args.check else rebuild(), ensure_ascii=False, indent=2))
