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

TREES: list[str] = []

USER_DOCS = [
    "docs/human/能力对照.md",
    "docs/human/发布状态.md",
]

VERSION_DOCS = [
    "release/docs/INDEX.md",
    "release/versions.yaml",
    "release/release.yaml",
]


def collect(version: str) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for name in TOP_FILES:
        p = os.path.join(ROOT, name)
        if os.path.exists(p):
            items.append((p, name))
    for name in USER_DOCS:
        p = os.path.join(ROOT, name)
        if os.path.exists(p):
            items.append((p, name))
    for name in [f"release/docs/{version}.md", *VERSION_DOCS]:
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
