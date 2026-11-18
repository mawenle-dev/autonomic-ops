"""One-command release helper (append-only; dry-run by default).

    # 1) 先看计划，不发任何写请求
    python tools/publish_release.py --version 1.0.1 --asset path/to/AO-Release-Studio.exe

    # 2) 确认后才真正创建 release 并上传资产（需要 token）
    python tools/publish_release.py --version 1.0.1 --asset ... --confirm PUBLISH

    # 3) 需要把文档改动推上去时，额外加 --push-docs
    python tools/publish_release.py --version 1.0.1 --asset ... --confirm PUBLISH --push-docs

Safety:
  * If a release for the tag already exists it refuses (no overwrite, no delete).
  * If an asset filename already exists it refuses (GitHub returns 422).
  * Without --confirm PUBLISH it never issues a mutating request.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

from studio.core import ReleaseStudio  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--asset", action="append", default=[])
    ap.add_argument("--body-file", default="")
    ap.add_argument("--confirm", default="", help="must be exactly PUBLISH to really publish")
    ap.add_argument("--push-docs", action="store_true")
    args = ap.parse_args(argv)

    studio = ReleaseStudio(ROOT)
    body = ""
    if args.body_file and os.path.exists(args.body_file):
        with open(args.body_file, encoding="utf-8") as fh:
            body = fh.read()

    dry = args.confirm != "PUBLISH"
    plan = studio.publish_github(args.version, args.asset, body=body, dry_run=dry)
    print(plan)

    if dry:
        print("\n[dry-run] 未发布。确认无误后加 --confirm PUBLISH。")
        return 0 if plan.get("ok") else 1
    if not plan.get("ok"):
        return 1

    if args.push_docs:
        docs = [f"release/docs/{args.version}.md", "release/docs/INDEX.md",
                "CHANGELOG.md", "交付说明-完整版.md"]
        docs = [d for d in docs if os.path.exists(os.path.join(ROOT, d))]
        print(studio.sync_docs(docs, f"docs: release {args.version}", allow_push=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
