"""Build the user-facing documentation bundle attached to a GitHub release.

Only documentation is included: no source, no release studio, no secrets, no
internal work logs. GitHub already generates source archives from the tag.
"""
from __future__ import annotations

import argparse
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TOP_FILES = [
    "README.md", "README.en.md", "CHANGELOG.md", "KNOWN_GAPS.md",
    "DISCLAIMER.md", "SECURITY.md", "CONTRIBUTING.md", "LICENSE",
]

TREES = [
    "docs/human",
]


def collect(version: str) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for name in TOP_FILES:
        p = os.path.join(ROOT, name)
        if os.path.exists(p):
            items.append((p, name))
    for tree in TREES:
        base = os.path.join(ROOT, tree)
        for dirpath, _dirs, names in os.walk(base):
            for n in sorted(names):
                if not n.endswith((".md", ".txt", ".png", ".jpg", ".svg")):
                    continue
                p = os.path.join(dirpath, n)
                rel = os.path.relpath(p, ROOT).replace(os.sep, "/")
                items.append((p, rel))
    for name in (f"release/docs/{version}.md", "release/docs/INDEX.md",
                 "release/versions.yaml", "release/release.yaml"):
        p = os.path.join(ROOT, name)
        if os.path.exists(p):
            items.append((p, name))
    return items


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    out = args.out or os.path.join(ROOT, "release", "dist",
                                   f"AutonomicOps-{args.version}-docs.zip")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    items = collect(args.version)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for src, rel in sorted(items, key=lambda x: x[1]):
            zf.write(src, rel)
    print({"ok": True, "version": args.version, "files": len(items),
           "zip": out, "bytes": os.path.getsize(out)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
