"""AO Release Studio core logic (constitution ch.9) - UI-independent and testable.

Responsibilities:
  * load the 120-version roadmap (versions.yaml via a tiny parser, no PyYAML dep)
  * show current / previous / next version, and the next version's deliverables+gate
  * enforce one-step-only advancement (no skipping versions)
  * preflight gate: every check must pass before publish (code-enforced)
  * publish: bump version, write CHANGELOG, generate receipt, update channel json
  * retract: remove tag, roll back version, mark receipt retracted
  * real subprocess log streaming (caller supplies a runner)
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

# Minimal YAML subset reader for our generated versions.yaml (avoids a runtime dep).
# The file uses a strict, generator-controlled shape; parsing is deterministic.


def parse_versions_yaml(path: str) -> list[dict]:
    """Parse the strict versions.yaml shape emitted by the roadmap generator."""
    out: list[dict] = []
    cur: Optional[dict] = None
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if line.startswith("  - version:"):
                if cur:
                    out.append(cur)
                cur = {"version": line.split(":", 1)[1].strip().strip('"')}
            elif cur is not None and line.strip().startswith(("gate:", "series:", "deliver:",
                                                              "acceptance:", "status:")):
                k, _, v = line.strip().partition(":")
                cur[k.strip()] = v.strip().strip('"')
    if cur:
        out.append(cur)
    return out


def _version_key(v: str) -> tuple:
    return tuple(int(x) for x in v.split("."))


def read_release_version(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("version:"):
                return line.split(":", 1)[1].strip().strip('"')
    raise ValueError("release.yaml has no version")


def write_release_version(path: str, version: str) -> None:
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()
    for i, line in enumerate(lines):
        if line.startswith("version:"):
            lines[i] = f'version: "{version}"\n'
            break
    else:
        raise ValueError("release.yaml has no version line")
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)


@dataclass
class Check:
    name: str
    runner: Callable[[], tuple[bool, str]]
    last_ok: bool = False
    last_output: str = ""
    last_ms: float = 0.0


class Preflight:
    """Every registered check must pass; publish button is disabled in code."""

    def __init__(self):
        self.checks: list[Check] = []

    def add(self, name: str, runner: Callable[[], tuple[bool, str]]) -> None:
        self.checks.append(Check(name, runner))

    def run(self) -> dict:
        results = []
        all_ok = True
        for c in self.checks:
            t0 = time.time()
            try:
                ok, out = c.runner()
            except Exception as exc:  # noqa: BLE001
                ok, out = False, str(exc)
            c.last_ok, c.last_output = bool(ok), str(out)
            c.last_ms = round((time.time() - t0) * 1000, 1)
            all_ok = all_ok and c.last_ok
            results.append({"name": c.name, "ok": c.last_ok,
                            "output": c.last_output, "ms": c.last_ms})
        return {"ok": all_ok, "checks": results,
                "publish_enabled": all_ok}  # code-enforced, not a prompt


class ReleaseStudio:
    def __init__(self, root_dir: str, runner: Optional[Callable[[list[str]], tuple[int, str]]] = None):
        self.root = root_dir
        self.runner = runner or self._default_runner
        self.receipts_dir = os.path.join(root_dir, "release", "receipts")
        self.channel_dir = os.path.join(root_dir, "release", "channel")
        os.makedirs(self.receipts_dir, exist_ok=True)
        os.makedirs(self.channel_dir, exist_ok=True)

    @staticmethod
    def _default_runner(cmd: list[str]) -> tuple[int, str]:
        p = subprocess.run(cmd, capture_output=True, text=True, shell=False)
        return p.returncode, (p.stdout + p.stderr)

    def versions(self) -> list[dict]:
        return parse_versions_yaml(os.path.join(self.root, "release", "versions.yaml"))

    def release_yaml(self) -> str:
        return os.path.join(self.root, "release", "release.yaml")

    def state(self) -> dict:
        vs = self.versions()
        cur = read_release_version(self.release_yaml())
        keys = [_version_key(v["version"]) for v in vs]
        cur_key = _version_key(cur)
        idx = keys.index(cur_key) if cur_key in keys else -1
        prev = vs[idx - 1]["version"] if idx > 0 else None
        nxt = vs[idx + 1] if idx + 1 < len(vs) else None
        return {"current": cur, "previous": prev,
                "next": nxt["version"] if nxt else None,
                "next_detail": nxt, "total_versions": len(vs)}

    def publish(self, preflight_ok: bool, gate: str, notes: str = "") -> dict:
        if not preflight_ok:
            return {"ok": False, "reason": "preflight_not_green", "publish_enabled": False}
        st = self.state()
        if not st["next"]:
            return {"ok": False, "reason": "no_next_version"}
        target = st["next"]
        # gate from the roadmap must match the gate key passed in
        if st["next_detail"] and st["next_detail"].get("gate") != gate:
            return {"ok": False, "reason": "gate_mismatch"}
        # one-step only: target must be exactly current + one roadmap entry
        code, log = self.runner(["git", "tag", f"v{target}"])
        receipt = {
            "version": target,
            "previous": st["current"],
            "gate": gate,
            "notes": notes,
            "ts": time.time(),
            "tag_exit_code": code,
            "tag_log": log,
            "checks_hash": "",
            "status": "published" if code == 0 else "tag_failed",
        }
        if code == 0:
            write_release_version(self.release_yaml(), target)
            self._write_changelog(target, gate, notes)
            self._write_channel(target)
        with open(os.path.join(self.receipts_dir, f"{target}.json"), "w", encoding="utf-8") as fh:
            json.dump(receipt, fh, ensure_ascii=False, sort_keys=True)
        return receipt

    def retract(self, version: str) -> dict:
        code, log = self.runner(["git", "tag", "-d", f"v{version}"])
        rp = os.path.join(self.receipts_dir, f"{version}.json")
        receipt = {}
        if os.path.exists(rp):
            with open(rp, "r", encoding="utf-8") as fh:
                receipt = json.load(fh)
        receipt["status"] = "retracted"
        receipt["retracted_at"] = time.time()
        with open(rp, "w", encoding="utf-8") as fh:
            json.dump(receipt, fh, ensure_ascii=False, sort_keys=True)
        prev = receipt.get("previous")
        if prev:
            write_release_version(self.release_yaml(), prev)
        return {"ok": code == 0, "version": version, "rolled_back_to": prev, "log": log}

    def _write_changelog(self, version: str, gate: str, notes: str) -> None:
        p = os.path.join(self.root, "CHANGELOG.md")
        if not os.path.exists(p):
            return
        with open(p, "r", encoding="utf-8") as fh:
            body = fh.read()
        entry = (f"\n## [{version}] - {time.strftime('%Y-%m-%d')} (gate `{gate}`)\n\n"
                 f"{notes or '见 versions.yaml 该版本条目。'}\n")
        marker = "\n## ["
        pos = body.find(marker)
        if pos == -1:
            body += entry
        else:
            body = body[:pos] + entry + body[pos:]
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)

    def _write_channel(self, version: str) -> None:
        data = {"version": version, "channel": "stable",
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        with open(os.path.join(self.channel_dir, "version.json"), "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, sort_keys=True)
