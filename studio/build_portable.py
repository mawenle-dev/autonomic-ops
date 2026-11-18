"""Build portable AO Release Studio package (constitution D-8, ch.9).

Produces release/portable/AO-Studio-portable-vX/ with:
  AO-Release-Studio.exe  (PyInstaller onefile, web console)
  Data/release/versions.yaml, release.yaml   (all 120 versions)
  Data/src/  (sources for preflight compile check)
  Data/tests/ (tests for preflight unit check)
  MANIFEST.json (sha256 of every file, self-check)
All paths are relative; no VM, no test toolchain, no absolute paths.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "release", "dist")
# Prefer a project-local PyInstaller (installed under .tools) so the build never
# depends on a system-wide install or writes outside the project.
_TOOLS = os.path.join(ROOT, ".tools")
if os.path.isdir(_TOOLS) and _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)


def _build_env() -> dict:
    """Environment for the PyInstaller subprocess: put .tools on PYTHONPATH."""
    env = dict(os.environ)
    parts = [p for p in (_TOOLS, os.path.join(ROOT, "studio")) if os.path.isdir(p)]
    existing = env.get("PYTHONPATH", "")
    if existing:
        parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build(spec_dir: str | None = None) -> dict:
    ver = "1.0.0"
    rp = os.path.join(ROOT, "release", "release.yaml")
    if os.path.exists(rp):
        for line in open(rp, encoding="utf-8"):
            if line.startswith("version:"):
                ver = line.split(":", 1)[1].strip().strip('"')
                break
    dest = os.path.join(OUT, f"AO-Studio-portable-v{ver}")
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest, exist_ok=True)
    raw = os.path.join(OUT, "_pyi")          # raw PyInstaller onedir output
    if os.path.exists(raw):
        shutil.rmtree(raw)
    data = os.path.join(dest, "Data")
    for d in ("release", "src", "tests", "studio", "config", "docs"):
        os.makedirs(os.path.join(data, d), exist_ok=True)

    def copy_tree(srcdir, dstdir, allow_dirs=None, allow_files=None):
        for base, dirs, names in os.walk(srcdir):
            # Never recurse into generated/derived trees: doing so copies a
            # previous portable package into the next one and explodes paths.
            dirs[:] = [d for d in dirs if d not in
                       ("__pycache__", "portable", ".build", ".spec", ".tmp",
                        ".disttest", ".wtest", ".stest", "_pyi", ".tools")]
            if allow_dirs is not None:
                dirs[:] = [d for d in dirs if d in allow_dirs]
            for n in names:
                if n.endswith((".pyc", ".pyo")):
                    continue
                if allow_files is not None and n not in allow_files:
                    continue
                p = os.path.join(base, n)
                rel = os.path.relpath(p, srcdir)
                target = os.path.join(dstdir, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy2(p, target)

    # release/: only the two manifests + receipts/channel - NEVER portable/.
    copy_tree(os.path.join(ROOT, "release"), os.path.join(data, "release"),
              allow_dirs={"receipts", "channel"},
              allow_files={"versions.yaml", "release.yaml"})
    for src, dst in (("src", "src"), ("tests", "tests"), ("studio", "studio"),
                     ("config", "config"), ("docs", "docs")):
        copy_tree(os.path.join(ROOT, src), os.path.join(data, dst))
    # PyInstaller ONEDIR build (fast start, no onefile extraction delay).
    # --paths studio lets the frozen entry resolve `import core`; the autonomic
    # package is bundled so the built-in self-test can run in-process.
    entry = os.path.join(ROOT, "studio", "app.py")
    cmd = [sys.executable, "-m", "PyInstaller", "--onedir", "--noconfirm",
           "--paths", os.path.join(ROOT, "studio"),
           "--paths", os.path.join(ROOT, "src"),
           "--hidden-import", "core",
           "--collect-submodules", "autonomic",
           "--distpath", raw, "--workpath", os.path.join(OUT, ".build"),
           "--specpath", os.path.join(OUT, ".spec"),
           "--name", "AO-Release-Studio", entry]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          env=_build_env())
    exe = os.path.join(dest, "AO-Release-Studio.exe")
    produced = os.path.join(raw, "AO-Release-Studio")
    if proc.returncode != 0 or not os.path.isdir(produced):
        return {"ok": False, "reason": "pyinstaller_failed", "stdout": proc.stdout[-2000:],
                "stderr": proc.stderr[-2000:]}
    # Move the onedir bundle (exe + _internal) to the top of dest, next to Data.
    for name in os.listdir(produced):
        s = os.path.join(produced, name)
        d = os.path.join(dest, name)
        if os.path.isdir(s):
            if os.path.exists(d):
                shutil.rmtree(d)
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
    shutil.rmtree(raw, ignore_errors=True)
    if not os.path.exists(exe):
        return {"ok": False, "reason": "exe_missing_after_onedir"}
    # MANIFEST self-check data
    files = []
    for base, dirs, names in os.walk(dest):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for n in names:
            p = os.path.join(base, n)
            if n == "MANIFEST.json":
                continue
            files.append({"path": os.path.relpath(p, dest), "sha256": sha256(p),
                          "size": os.path.getsize(p)})
    manifest = {"format": "ao-portable-manifest/1", "version": ver,
                "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
                "files": sorted(files, key=lambda x: x["path"])}
    manifest["root_hash"] = hashlib.sha256(
        "".join(f["sha256"] for f in manifest["files"]).encode()).hexdigest()
    with open(os.path.join(dest, "MANIFEST.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    return {"ok": True, "version": ver, "dir": dest, "files": len(files),
            "exe": "AO-Release-Studio.exe", "root_hash": manifest["root_hash"]}


def verify(dest: str) -> dict:
    mp = os.path.join(dest, "MANIFEST.json")
    if not os.path.exists(mp):
        return {"ok": False, "reason": "manifest_missing"}
    m = json.load(open(mp, encoding="utf-8"))
    bad = []
    for f in m["files"]:
        p = os.path.join(dest, f["path"])
        if not os.path.exists(p) or sha256(p) != f["sha256"]:
            bad.append(f["path"])
    return {"ok": not bad, "tampered": bad, "version": m.get("version")}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "verify":
        d = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
            OUT, "AO-Studio-portable-v1.0.0")
        print(json.dumps(verify(d), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(build(), ensure_ascii=False, indent=2))
