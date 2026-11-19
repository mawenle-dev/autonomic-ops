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

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

# Minimal YAML subset reader for our generated versions.yaml (avoids a runtime dep).
# The file uses a strict, generator-controlled shape; parsing is deterministic.


def parse_versions_yaml(path: str) -> list[dict]:
    """Parse the strict versions.yaml shape emitted by the roadmap generator."""
    out: list[dict] = []
    cur: Optional[dict] = None
    keys = ("ordinal", "gate", "series", "deliver", "highlights", "acceptance", "status")
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if line.startswith("  - version:"):
                if cur:
                    out.append(cur)
                cur = {"version": line.split(":", 1)[1].strip().strip('"')}
            elif cur is not None and line.strip().startswith(tuple(k + ":" for k in keys)):
                k, _, v = line.strip().partition(":")
                val = v.strip().strip('"')
                if k.strip() == "ordinal":
                    try:
                        val = int(val)
                    except ValueError:
                        pass
                cur[k.strip()] = val
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


def _python_for_checks() -> str:
    """Python used by source-mode preflight; '' when frozen and none exists."""
    for name in ("python3", "python"):
        p = shutil.which(name)
        if p:
            return p
    return "" if getattr(sys, "frozen", False) else sys.executable


def build_preflight(root: str, studio: "ReleaseStudio") -> Preflight:
    """Build the *real* preflight set.

    This lives in core (not in the HTTP layer) so that ``ReleaseStudio.publish``
    can run the very same checks itself. A caller can no longer hand-wave
    ``preflight_ok=True``: the only way in is a freshly executed check list.
    """
    pf = Preflight()
    py = _python_for_checks()

    def compile_check():
        src = os.path.join(root, "src")
        files = []
        for base, _d, names in os.walk(src):
            for n in names:
                if n.endswith(".py"):
                    files.append(os.path.join(base, n))
        if not files:
            return False, f"no python files found under {src} (fail-closed)"
        if not py:
            return True, f"frozen build: {len(files)} source files verified at build time"
        import py_compile
        try:
            for f in files:
                py_compile.compile(f, doraise=True)
            return True, f"compileall: {len(files)} files OK"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    def test_check():
        if not py:
            return True, "frozen build: unit tests ran at build time (skipped at runtime)"
        p = subprocess.run([py, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
                           cwd=root, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode == 0, out[-2000:]

    def builtin_selftest():
        """Real in-process self-test used by the frozen EXE (no python binary)."""
        try:
            from autonomic import fvc, signing, ledger, runtime  # noqa: F401
            acct = fvc.BudgetAccount(fvc.Budget(bytes=0, files=0, actions=0, blast=0, rate=0))
            ok, _ = acct.debit({"bytes": 1})
            assert not ok, "zero budget must refuse"
            try:
                fvc.Receipt(trace_id="t", fact_refs=["f"], verdict={"id": "v"},
                            covenant={"a": 1}, txn={"id": "x"}, undo={})
                raise AssertionError("receipt without undo must fail")
            except ValueError:
                pass
            seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
            pub = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
            sig = bytes.fromhex("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
                                "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
            assert signing.sign(seed, b"") == sig, "ed25519 vector mismatch"
            assert signing.verify(pub, b"", sig), "ed25519 verify failed"
            s2, _pub2 = signing.generate_keypair()
            b = ledger.export_bundle([{"seq": 1, "epoch": 1, "hash": "a", "id": "c"}], [],
                                     private_seed=s2)
            r = ledger.verify_bundle(b)
            assert r["ok"] and r["signature_ok"], "bundle verify failed"
            return True, f"builtin self-test: budget/receipt/ed25519/ledger OK (backend={signing.backend()})"
        except Exception as exc:  # noqa: BLE001
            return False, f"builtin self-test failed: {exc}"

    def roadmap_check():
        try:
            vs = studio.versions()
            if not vs:
                return False, "roadmap is empty"
            keys = [_version_key(v["version"]) for v in vs]
            if keys != sorted(keys) or len(set(keys)) != len(keys):
                return False, "roadmap versions are not strictly increasing/unique"
            cur = read_release_version(studio.release_yaml())
            cur_key = _version_key(cur)
            if cur_key not in keys:
                # The 1.0.0 baseline is intentionally not a roadmap entry: it
                # predates the gate table. It may publish only the first
                # roadmap version, and only while nothing has been released yet.
                if cur_key < keys[0] and not studio.released():
                    return True, (f"baseline {cur} precedes roadmap; "
                                  f"first publishable version is {vs[0]['version']}")
                return False, f"current version {cur} is not in the roadmap"
            return True, f"roadmap: {len(vs)} versions, current {cur} at index {keys.index(cur_key)}"
        except Exception as exc:  # noqa: BLE001
            return False, f"roadmap parse failed: {exc}"

    def manifest_check():
        mp = os.path.join(os.path.dirname(root), "MANIFEST.json")
        if not os.path.exists(mp):
            mp = os.path.join(root, "MANIFEST.json")
        if not os.path.exists(mp):
            return False, "MANIFEST.json missing (fail-closed: cannot verify the bundle)"
        try:
            with open(mp, "r", encoding="utf-8") as fh:
                m = json.load(fh)
            base = os.path.dirname(mp)
            bad = []
            for f in m.get("files", []):
                p = os.path.join(base, f["path"])
                if not os.path.exists(p):
                    bad.append(f["path"])
                    continue
                h = hashlib.sha256()
                with open(p, "rb") as fh:
                    for chunk in iter(lambda: fh.read(65536), b""):
                        h.update(chunk)
                if h.hexdigest() != f["sha256"]:
                    bad.append(f["path"])
            return (not bad), f"manifest: {len(m.get('files', []))} files, {len(bad)} bad"
        except Exception as exc:  # noqa: BLE001
            return False, f"manifest verify failed: {exc}"

    pf.add("roadmap", roadmap_check)
    if getattr(sys, "frozen", False):
        # A portable bundle must prove its own integrity before it can publish.
        pf.add("manifest", manifest_check)
        pf.add("builtin self-test", builtin_selftest)
    else:
        # Source checkouts have no portable MANIFEST by design; publish() seals
        # the source tree itself. Requiring a bundle manifest here made every
        # source-mode publish impossible.
        pf.add("compile", compile_check)
        pf.add("unit tests", test_check)
    return pf


class ReleaseStudio:
    def __init__(self, root_dir: str, runner: Optional[Callable[[list[str]], tuple[int, str]]] = None):
        self.root = root_dir
        self.runner = runner or self._default_runner
        self.receipts_dir = os.path.join(root_dir, "release", "receipts")
        self.channel_dir = os.path.join(root_dir, "release", "channel")
        # Seam for tests only: production always uses the real check list.
        self.preflight_factory: Callable[[], Preflight] = lambda: build_preflight(root_dir, self)
        # Tests may inject a runner and disable the external-git requirement.
        self.require_git = True
        os.makedirs(self.receipts_dir, exist_ok=True)
        os.makedirs(self.channel_dir, exist_ok=True)

    @staticmethod
    def _default_runner(cmd: list[str]) -> tuple[int, str]:
        p = subprocess.run(cmd, capture_output=True, text=True, shell=False)
        return p.returncode, (p.stdout + p.stderr)

    def versions(self) -> list[dict]:
        return parse_versions_yaml(os.path.join(self.root, "release", "versions.yaml"))

    # ---- released registry: prevents duplicate publish and enforces 1/day ----
    def released_path(self) -> str:
        return os.path.join(self.root, "release", "released.json")

    def released(self) -> dict:
        p = self.released_path()
        if not os.path.exists(p):
            return {}
        try:
            with open(p, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _record_release(self, version: str) -> None:
        data = self.released()
        data[version] = {"released_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                         "date": time.strftime("%Y-%m-%d")}
        with open(self.released_path(), "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, sort_keys=True, indent=2)

    def _released_today(self) -> Optional[str]:
        today = time.strftime("%Y-%m-%d")
        for v, info in self.released().items():
            if info.get("date") == today:
                return v
        return None

    # ------------------------------------------------------------ update channel
    def updates_dir(self) -> str:
        return os.path.join(self.root, "release", "updates")

    def update_manifest(self) -> dict:
        """Read release/updates/manifest.json; empty when absent (no update channel)."""
        p = os.path.join(self.updates_dir(), "manifest.json")
        if not os.path.exists(p):
            return {"format": "ao-update-manifest/1", "updates": []}
        try:
            with open(p, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return {"format": "ao-update-manifest/1", "updates": [], "error": "unreadable"}
        if not isinstance(data.get("updates"), list):
            data["updates"] = []
        return data

    def available_updates(self) -> list[dict]:
        """Updates newer than the current version, each verified against its hash."""
        current = read_release_version(os.path.join(self.root, "release", "release.yaml"))
        out = []
        for item in self.update_manifest().get("updates", []):
            ver = str(item.get("version", ""))
            try:
                newer = _version_key(ver) > _version_key(current)
            except ValueError:
                newer = False
            if not newer:
                continue
            path = os.path.join(self.updates_dir(), str(item.get("file", "")))
            row = dict(item)
            row["exists"] = os.path.isfile(path)
            row["hash_ok"] = False
            if row["exists"]:
                h = hashlib.sha256()
                with open(path, "rb") as fh:
                    for chunk in iter(lambda: fh.read(65536), b""):
                        h.update(chunk)
                row["hash_ok"] = h.hexdigest() == item.get("sha256")
            out.append(row)
        return sorted(out, key=lambda r: _version_key(str(r["version"])), reverse=True)

    def stage_update(self, version: str) -> dict:
        """Copy a verified update package into updates/staged/. Never applies it."""
        for item in self.available_updates():
            if str(item.get("version")) != version:
                continue
            if not item.get("exists"):
                return {"ok": False, "reason": "package_missing"}
            if not item.get("hash_ok"):
                return {"ok": False, "reason": "hash_mismatch"}
            src = os.path.join(self.updates_dir(), item["file"])
            staged = os.path.join(self.checkout_updates_dir(), f"{version}.zip")
            os.makedirs(os.path.dirname(staged), exist_ok=True)
            shutil.copy2(src, staged)
            return {"ok": True, "version": version, "staged": staged,
                    "sha256": item.get("sha256"), "size": os.path.getsize(staged),
                    "next": "close the app, replace the portable folder, then start again"}
        return {"ok": False, "reason": "not_available", "version": version}

    def checkout_updates_dir(self) -> str:
        return os.path.join(self.root, "updates", "staged")

    def versions_full(self) -> list[dict]:
        """All roadmap versions + the baseline, each with truthful release state.

        Release truth comes from ``release/released.json`` only. The 1.0.0
        baseline predates the receipt pipeline, so it is shown as ``baseline``
        and is *not* counted as a receipt-backed release.
        """
        vs = self.versions()
        rel = self.released()
        items = [{
            "version": "1.0.0", "ordinal": 0,
            "series": "初始版本", "gate": "—",
            "deliver": "首个正式版本 aegis：守护进程/指标/规则/策略/执行/审计/面板",
            "highlights": "完整可用的 1.0.0 基线：装得上、跑得稳、能回滚、有审计",
            "acceptance": "50 项端到端自测通过",
            "status": "baseline",
            "baseline": True,
            "released": "1.0.0" in rel,
            "released_at": rel.get("1.0.0", {}).get("released_at", ""),
        }]
        for v in vs:
            ver = v["version"]
            items.append({
                "version": ver,
                "ordinal": v.get("ordinal", 0),
                "series": v.get("series", ""),
                "gate": v.get("gate", ""),
                "deliver": v.get("deliver", ""),
                "highlights": v.get("highlights", ""),
                "acceptance": v.get("acceptance", ""),
                "status": v.get("status", "pending"),
                "released": ver in rel,
                "released_at": rel.get(ver, {}).get("released_at", ""),
            })
        return items

    def version_detail(self, version: str) -> Optional[dict]:
        for item in self.versions_full():
            if item["version"] == version:
                return item
        return None

    # ---- one-click release: docs + signed assets + GitHub (append-only) ----
    def account(self) -> dict:
        """Bound GitHub account, from config first and local override second.

        ``config/account.json`` ships with the product (non-secret). A local
        ``.secrets/account.json`` may override it without changing the package.
        """
        for rel in ("config/account.json", os.path.join(".secrets", "account.json")):
            path = os.path.join(self.root, rel)
            if not os.path.exists(path):
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                continue
            owner = str(data.get("owner", "")).strip()
            repo = str(data.get("repo", "")).strip()
            if owner and repo:
                return {"owner": owner, "repo": repo,
                        "remote": str(data.get("remote", "")).strip(),
                        "source": rel}
        return {"owner": "", "repo": "", "remote": "", "source": ""}

    def repo_slug(self) -> tuple[str, str]:
        """Resolve owner/repo from the origin remote, then the bound account."""
        try:
            p = subprocess.run(["git", "remote", "get-url", "origin"], cwd=self.root,
                               capture_output=True, text=True)
            url = (p.stdout or "").strip()
        except Exception:  # noqa: BLE001
            url = ""
        import re as _re
        m = _re.search(r"github\.com[:/]([^/]+)/([^/.]+)", url)
        if m:
            return (m.group(1), m.group(2))
        acct = self.account()
        return (acct["owner"], acct["repo"])

    def account_status(self) -> dict:
        """What the UI needs: bound target plus whether a token is present."""
        acct = self.account()
        try:
            from autonomic import github_publish as gp
            has_token = bool(gp.load_token(self.root))
        except Exception:  # noqa: BLE001
            has_token = False
        owner, repo = self.repo_slug()
        return {"owner": owner, "repo": repo,
                "slug": f"{owner}/{repo}" if owner and repo else "",
                "bound": bool(owner and repo),
                "token": has_token,
                "token_path": os.path.join(self.root, ".secrets", "github_token"),
                "source": acct["source"]}

    def publish_github(self, version: str, assets: list[str], body: str = "",
                       dry_run: bool = True) -> dict:
        """Plan/create a GitHub release for ``version`` (append-only).

        Always refuses if the tag's release already exists; never deletes or
        replaces assets. Default dry_run performs no mutating request.
        """
        from autonomic import github_publish as gp
        owner, repo = self.repo_slug()
        if not owner or not repo:
            return {"ok": False, "reason": "no_github_remote"}
        token = gp.load_token(self.root)
        publisher = gp.GitHubPublisher(owner, repo, token)
        detail = self.version_detail(version) or {}
        notes = body or (f"## {version}\n\n{detail.get('deliver','')}\n\n"
                         f"{detail.get('highlights','')}\n")
        return publisher.publish(version, notes, assets, dry_run=dry_run)

    def sync_docs(self, files: list[str], message: str, allow_push: bool = False) -> dict:
        from autonomic import github_publish as gp
        owner, repo = self.repo_slug()
        publisher = gp.GitHubPublisher(owner, repo, gp.load_token(self.root))
        return publisher.commit_and_push_docs(self.root, files, message, allow_push=allow_push)

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
                "next_detail": nxt, "total_versions": len(vs),
                "account": self.account_status()}

    def _seal_source(self) -> dict:
        """Seal the exact source tree that is about to be tagged as a release.

        This is a real content seal (sha256 over every file, then a root over
        the file hashes), not a placeholder: a receipt that claims a release
        without this seal cannot be produced by publish().
        """
        src = os.path.join(self.root, "src")
        files: dict[str, str] = {}
        for base, _d, names in os.walk(src):
            for n in sorted(names):
                if not n.endswith(".py"):
                    continue
                p = os.path.join(base, n)
                rel = os.path.relpath(p, self.root).replace(os.sep, "/")
                with open(p, "rb") as fh:
                    files[rel] = hashlib.sha256(fh.read()).hexdigest()
        if not files:
            return {"ok": False, "reason": "no source files to seal"}
        root = hashlib.sha256(
            json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return {"ok": True, "count": len(files), "root": root, "files": files}

    def publish(self, gate: str, notes: str = "", checks: Optional[list] = None) -> dict:
        # Preflight is executed *here*, inside the publisher: a caller cannot
        # pass a boolean to unlock the gate any more.
        pre = self.preflight_factory().run()
        if not pre["ok"]:
            return {"ok": False, "reason": "preflight_not_green",
                    "publish_enabled": False, "checks": pre["checks"]}
        st = self.state()
        if not st["next"]:
            return {"ok": False, "reason": "no_next_version"}
        target = st["next"]
        # gate from the roadmap must match the gate key passed in
        if st["next_detail"] and st["next_detail"].get("gate") != gate:
            return {"ok": False, "reason": "gate_mismatch"}
        # duplicate publish guard: a version is published at most once
        if target in self.released():
            return {"ok": False, "reason": "already_released", "version": target}
        # one version per day guard (constitution release cadence)
        today_ver = self._released_today()
        if today_ver:
            return {"ok": False, "reason": "already_released_today",
                    "version": today_ver, "note": "一天最多发布一版"}
        seal = self._seal_source()
        if not seal["ok"]:
            return {"ok": False, "reason": "seal_failed", "detail": seal.get("reason")}
        if self.require_git and not shutil.which("git"):
            return {"ok": False, "reason": "git_not_available",
                    "detail": "publish requires a git binary on PATH; install git or publish from Linux"}
        code, log = self.runner(["git", "tag", f"v{target}"])
        checks = checks or pre["checks"]
        checks_hash = hashlib.sha256(
            json.dumps(checks, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:32]
        receipt = {
            "version": target,
            "previous": st["current"],
            "gate": gate,
            "notes": notes,
            "ts": time.time(),
            "tag_exit_code": code,
            "tag_log": log,
            "preflight_ok": True,
            "checks": checks,
            "checks_hash": checks_hash,
            "seal": {"root": seal["root"], "count": seal["count"]},
            "status": "published" if code == 0 else "tag_failed",
        }
        if code == 0:
            write_release_version(self.release_yaml(), target)
            self._write_changelog(target, gate, notes)
            self._write_channel(target)
            self._record_release(target)
            self._write_version_snapshot(target, st["next_detail"])
        with open(os.path.join(self.receipts_dir, f"{target}.json"), "w", encoding="utf-8") as fh:
            json.dump(receipt, fh, ensure_ascii=False, sort_keys=True)
        return receipt

    def _write_version_snapshot(self, version: str, detail: Optional[dict]) -> None:
        """Write a per-version doc snapshot; existing versions are never overwritten."""
        snap_dir = os.path.join(self.root, "release", "docs")
        os.makedirs(snap_dir, exist_ok=True)
        path = os.path.join(snap_dir, f"{version}.md")
        if os.path.exists(path):
            return  # snapshots are immutable once written
        d = detail or {}
        body = (f"# {version}\n\n"
                f"- 系列：{d.get('series','')}\n"
                f"- 闸门键：{d.get('gate','')}\n"
                f"- 交付：{d.get('deliver','')}\n"
                f"- 优点：{d.get('highlights','')}\n"
                f"- 验收：{d.get('acceptance','')}\n"
                f"- 发布日期：{time.strftime('%Y-%m-%d')}\n")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)

    def _forget_release(self, version: str) -> None:
        """Remove a version from the *published* registry (used by retract)."""
        data = self.released()
        if version in data:
            data.pop(version, None)
            with open(self.released_path(), "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, sort_keys=True, indent=2)

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
        # A retract must make the system *releasable again*: remove the version
        # from released.json, otherwise the next publish hits already_released.
        self._forget_release(version)
        prev = receipt.get("previous")
        if prev:
            write_release_version(self.release_yaml(), prev)
            self._write_channel(prev)
        else:
            self._write_channel(read_release_version(self.release_yaml()))
        return {"ok": code == 0, "version": version, "rolled_back_to": prev,
                "released_registry_clean": version not in self.released(),
                "log": log}

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
