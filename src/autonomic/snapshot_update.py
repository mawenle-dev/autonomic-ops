"""Signed snapshot updates (constitution 1.0.17 + release updater).

A snapshot is a tar.gz containing the full file tree plus a signed manifest of
per-file SHA-256 digests. Update = verify signature + hashes, extract to a
staging dir, atomically swap with the live tree, keep the old tree as a
rollback point. This is the standard full-snapshot update model used by desktop
updaters (Sparkle ships a full zip plus optional deltas; deltas are an
optimisation, the snapshot is the source of truth).

No delta patches are produced here: correctness first. A delta layer can be
added later without changing the snapshot format.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tarfile
import time
from typing import Optional

from . import fvc, signing

MANIFEST = "manifest.json"


def _sha_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_snapshot(root: str, out_path: str, private_seed: bytes,
                   include: Optional[list[str]] = None) -> dict:
    """Create a signed snapshot of ``root`` at ``out_path``."""
    files = {}
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        for n in names:
            if n.endswith((".pyc", ".pyo")):
                continue
            p = os.path.join(base, n)
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            if include is not None and rel not in include:
                continue
            files[rel] = _sha_file(p)
    manifest = {"format": "ao-snapshot/1", "created": int(time.time()),
                "files": files, "count": len(files)}
    # pub must be part of the signed body, so set it before signing.
    manifest["pub"] = signing.hexlify(signing.public_key_from_seed(private_seed))
    manifest["signature"] = signing.hexlify(
        signing.sign(private_seed, fvc.canon(manifest).encode()))
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with tarfile.open(out_path, "w:gz") as tf:
        data = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
        info = tarfile.TarInfo(MANIFEST)
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
        for rel in sorted(files):
            tf.add(os.path.join(root, rel), arcname=rel)
    return {"path": out_path, "count": len(files), "pub": manifest["pub"],
            "sha256": _sha_file(out_path)}


def _read_manifest(path: str) -> dict:
    with tarfile.open(path, "r:gz") as tf:
        f = tf.extractfile(MANIFEST)
        if not f:
            raise ValueError("snapshot missing manifest")
        return json.loads(f.read())


def verify_snapshot(path: str, public_key: Optional[bytes] = None) -> dict:
    """Verify signature and every member's SHA-256 without extracting to disk."""
    try:
        m = _read_manifest(path)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "errors": [f"manifest: {exc}"]}
    errors = []
    pub = public_key or signing.unhexlify(m.get("pub", ""))
    body = {k: v for k, v in m.items() if k != "signature"}
    try:
        if not signing.verify(pub, fvc.canon(body).encode(), signing.unhexlify(m["signature"])):
            errors.append("signature_invalid")
    except Exception:  # noqa: BLE001
        errors.append("signature_invalid")
    with tarfile.open(path, "r:gz") as tf:
        for rel, want in m.get("files", {}).items():
            try:
                f = tf.extractfile(rel)
            except KeyError:
                errors.append(f"missing:{rel}")
                continue
            if f is None:
                errors.append(f"missing:{rel}")
                continue
            got = hashlib.sha256(f.read()).hexdigest()
            if got != want:
                errors.append(f"tampered:{rel}")
    return {"ok": not errors, "errors": errors, "count": len(m.get("files", {})),
            "manifest": m}


def apply_snapshot(path: str, target: str, public_key: Optional[bytes] = None,
                   keep_backup: bool = True) -> dict:
    """Atomically replace ``target`` with the snapshot; rollback on any failure."""
    v = verify_snapshot(path, public_key)
    if not v["ok"]:
        return {"ok": False, "reason": "verify_failed", "errors": v["errors"],
                "rolled_back": True}
    parent = os.path.dirname(os.path.abspath(target))
    os.makedirs(parent, exist_ok=True)
    staging = target + ".staging-" + str(int(time.time() * 1000))
    backup = target + ".backup-" + str(int(time.time() * 1000))
    try:
        os.makedirs(staging, exist_ok=True)
        with tarfile.open(path, "r:gz") as tf:
            for member in tf.getmembers():
                if member.name == MANIFEST:
                    continue
                # path traversal guard
                if member.name.startswith("/") or ".." in member.name.split("/"):
                    raise ValueError(f"unsafe member: {member.name}")
            tf.extractall(staging, filter="data")
        if os.path.exists(target):
            os.rename(target, backup)
        os.rename(staging, target)
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(staging, ignore_errors=True)
        if os.path.exists(backup) and not os.path.exists(target):
            os.rename(backup, target)
        return {"ok": False, "reason": f"apply_failed:{exc}", "rolled_back": True}
    if not keep_backup:
        shutil.rmtree(backup, ignore_errors=True)
        backup = ""
    return {"ok": True, "target": target, "files": v["count"], "backup": backup}


def rollback(target: str, backup: str) -> dict:
    """Restore the previous tree from a kept backup."""
    if not backup or not os.path.isdir(backup):
        return {"ok": False, "reason": "no_backup"}
    failed = target + ".failed-" + str(int(time.time() * 1000))
    if os.path.exists(target):
        os.rename(target, failed)
    os.rename(backup, target)
    shutil.rmtree(failed, ignore_errors=True)
    return {"ok": True, "restored": target}
