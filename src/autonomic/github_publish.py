"""GitHub release publisher - append-only, never overwrites, dry-run by default.

Verified against GitHub's documented behaviour (REST releases + release assets):
  * Creating a release for a NEW tag adds a new release; it does not modify
    existing releases.
  * Uploading an asset whose filename already exists on that release returns
    422; the API requires deleting the old asset first. This module NEVER
    deletes or replaces - it refuses and reports, so a previous release can
    never be silently overwritten.

Nothing here publishes automatically: ``publish(dry_run=True)`` is the default
and performs no mutating request. A real publish requires an explicit call with
``dry_run=False`` plus a token (env GITHUB_TOKEN/GH_TOKEN or
``<root>/.secrets/github_token``).
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional


class GitHubError(RuntimeError):
    pass


class Transport:
    """Minimal HTTP transport (urllib). Tests inject a fake with the same API."""

    def request(self, method: str, url: str, headers: dict, data: Optional[bytes] = None):
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()


def load_token(root: str) -> str:
    """Token from env, else <root>/.secrets/github_token. Never hard-coded."""
    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        v = os.environ.get(key)
        if v:
            return v.strip()
    p = os.path.join(root, ".secrets", "github_token")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    return ""


class GitHubPublisher:
    def __init__(self, owner: str, repo: str, token: str = "",
                 api: str = "https://api.github.com",
                 uploads: str = "https://uploads.github.com",
                 transport: Optional[Transport] = None):
        self.owner = owner
        self.repo = repo
        self.token = token
        self.api = api.rstrip("/")
        self.uploads = uploads.rstrip("/")
        self.t = transport or Transport()

    # ------------------------------------------------------------ helpers
    def _headers(self, content_type: str = "application/json") -> dict:
        h = {"Accept": "application/vnd.github+json",
             "X-GitHub-Api-Version": "2022-11-28",
             "Content-Type": content_type,
             "User-Agent": "autonomic-ops-publisher"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _json(self, status: int, raw: bytes) -> dict:
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            return {"raw": raw.decode("utf-8", "replace")[:500], "status": status}

    def get_release_by_tag(self, tag: str) -> Optional[dict]:
        url = f"{self.api}/repos/{self.owner}/{self.repo}/releases/tags/{tag}"
        status, raw = self.t.request("GET", url, self._headers())
        if status == 404:
            return None
        if status >= 400:
            raise GitHubError(f"get_release_by_tag {status}: {self._json(status, raw)}")
        return self._json(status, raw)

    def plan(self, version: str) -> dict:
        tag = f"v{version}"
        existing = self.get_release_by_tag(tag)
        if existing:
            return {"ok": False, "reason": "release_exists_no_overwrite", "tag": tag,
                    "html_url": existing.get("html_url", ""),
                    "note": "该 tag 的 release 已存在；按不可覆盖原则拒绝，不会删除或替换旧版。"}
        return {"ok": True, "tag": tag, "action": "create_new_release"}

    # ------------------------------------------------------------ mutations
    def create_release(self, tag: str, name: str, body: str,
                       draft: bool = False, prerelease: bool = False) -> dict:
        url = f"{self.api}/repos/{self.owner}/{self.repo}/releases"
        payload = json.dumps({"tag_name": tag, "name": name, "body": body,
                              "draft": draft, "prerelease": prerelease,
                              "make_latest": "true"}).encode()
        status, raw = self.t.request("POST", url, self._headers(), payload)
        if status == 422:
            return {"ok": False, "reason": "release_exists_no_overwrite",
                    "detail": self._json(status, raw)}
        if status >= 400:
            raise GitHubError(f"create_release {status}: {self._json(status, raw)}")
        return {"ok": True, "release": self._json(status, raw)}

    def upload_asset(self, release: dict, path: str) -> dict:
        upload_url = release.get("upload_url", "")
        release_id = release.get("id")
        base = upload_url.split("{", 1)[0] if upload_url else \
            f"{self.uploads}/repos/{self.owner}/{self.repo}/releases/{release_id}/assets"
        name = os.path.basename(path)
        url = f"{base}?name={urllib.parse.quote(name)}"
        with open(path, "rb") as fh:
            data = fh.read()
        status, raw = self.t.request("POST", url,
                                     self._headers("application/octet-stream"), data)
        if status == 422:
            # same filename already exists -> refuse, never delete/replace
            return {"ok": False, "reason": "asset_exists_no_overwrite", "name": name,
                    "detail": self._json(status, raw)}
        if status >= 400:
            return {"ok": False, "reason": f"upload_failed:{status}",
                    "name": name, "detail": self._json(status, raw)}
        return {"ok": True, "asset": self._json(status, raw), "name": name}

    def publish(self, version: str, body: str, assets: list[str],
                dry_run: bool = True, draft: bool = False) -> dict:
        """Create a NEW release and upload assets. Never overwrites."""
        plan = self.plan(version)
        if not plan["ok"]:
            return {"ok": False, "dry_run": dry_run, **plan}
        if dry_run:
            return {"ok": True, "dry_run": True, "tag": plan["tag"],
                    "would_create": True, "assets": [os.path.basename(a) for a in assets],
                    "note": "dry-run：未发出任何写请求"}
        if not self.token:
            return {"ok": False, "reason": "missing_token",
                    "note": "在 .secrets/github_token 或环境变量 GITHUB_TOKEN 提供 token"}
        created = self.create_release(plan["tag"], f"{version}", body, draft=draft)
        if not created["ok"]:
            return {"ok": False, "dry_run": False, **created}
        rel = created["release"]
        uploaded, failed = [], []
        for a in assets:
            r = self.upload_asset(rel, a)
            (uploaded if r["ok"] else failed).append(r)
        return {"ok": not failed, "dry_run": False, "tag": plan["tag"],
                "html_url": rel.get("html_url", ""),
                "uploaded": [u.get("name") for u in uploaded],
                "failed": failed}

    # ------------------------------------------------------------ docs sync
    def commit_and_push_docs(self, root: str, files: list[str], message: str,
                             allow_push: bool = False) -> dict:
        """Commit and (only if allow_push) push documentation. Default: no push."""
        if not allow_push:
            return {"ok": True, "pushed": False,
                    "note": "dry-run：仅计划，不 commit/push"}
        cmds = [["git", "add", *files],
                ["git", "commit", "-m", message]]
        for cmd in cmds:
            p = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
            if p.returncode != 0:
                return {"ok": False, "pushed": False, "cmd": cmd,
                        "stderr": p.stderr[-500:]}
        p = subprocess.run(["git", "push", "origin", "HEAD"], cwd=root,
                           capture_output=True, text=True)
        return {"ok": p.returncode == 0, "pushed": p.returncode == 0,
                "stderr": p.stderr[-500:]}
