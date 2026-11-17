"""Reliquary / signed content packs (constitution M-1/ch.6): pack format
(tar.gz + manifest + signature), default-reject unsigned, offline install with
rollback, lock file for reproducibility, 60s one-shot scoped capability tokens.
Stdlib only; signature uses HMAC-style digest with a local key (Ed25519 plug-in
point kept for distribution).
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import tarfile
import time
from dataclasses import dataclass, field
from typing import Optional

from . import fvc, util

PACK_TYPES = ("rules", "parsers", "probes", "choreographies", "plugins", "compliance", "intel")


@dataclass
class PackManifest:
    name: str
    version: str
    capabilities: list = field(default_factory=list)
    deps: list = field(default_factory=list)
    os: str = "any"
    changelog: str = ""
    signature: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "version": self.version,
                "caps": self.capabilities, "deps": self.deps,
                "os": self.os, "changelog": self.changelog}

    def signable(self) -> dict:
        d = self.to_dict()
        d.pop("signature", None)
        return d


def sign_manifest(m: PackManifest, key: bytes) -> str:
    return hmac.new(key, fvc.canon(m.signable()).encode(), hashlib.sha256).hexdigest()


def verify_manifest(m: PackManifest, key: bytes) -> bool:
    if not m.signature:
        return False
    return hmac.compare_digest(m.signature, sign_manifest(m, key))


def build_pack(dest: str, manifest: PackManifest, files: dict[str, bytes], key: bytes) -> str:
    """files = {arcname: bytes}. Returns path to the signed tar.gz pack."""
    manifest.signature = sign_manifest(manifest, key)
    util.ensure_dir(os.path.dirname(dest) or ".", 0o750)
    with tarfile.open(dest, "w:gz") as tf:
        mbytes = fvc.canon(manifest.to_dict() | {"signature": manifest.signature}).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(mbytes)
        tf.addfile(info, io.BytesIO(mbytes))
        for name, data in files.items():
            # reject path traversal in archive member names
            if name.startswith("/") or ".." in name.split("/"):
                raise ValueError(f"unsafe pack member: {name}")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return dest


class Reliquary:
    def __init__(self, install_dir: str, key: bytes, snapshot_fn=None):
        self.install_dir = install_dir
        self.key = key
        self.snapshot_fn = snapshot_fn
        util.ensure_dir(install_dir, 0o750)

    def _read_manifest(self, pack_path: str) -> PackManifest:
        with tarfile.open(pack_path, "r:gz") as tf:
            f = tf.extractfile("manifest.json")
            if not f:
                raise ValueError("pack missing manifest")
            d = json.loads(f.read())
        return PackManifest(name=d["name"], version=d["version"],
                            capabilities=d.get("caps", []), deps=d.get("deps", []),
                            os=d.get("os", "any"), changelog=d.get("changelog", ""),
                            signature=d.get("signature", ""))

    def install(self, pack_path: str, allow_unsigned: bool = False) -> dict:
        m = self._read_manifest(pack_path)
        if not allow_unsigned and not verify_manifest(m, self.key):
            return {"ok": False, "reason_code": "unsigned_pack_rejected", "name": m.name}
        # third-party packs may never declare exec capability
        if "exec" in m.capabilities and m.name.startswith("third_party/"):
            return {"ok": False, "reason_code": "third_party_exec_denied", "name": m.name}
        rp = self.snapshot_fn() if self.snapshot_fn else None
        try:
            with tarfile.open(pack_path, "r:gz") as tf:
                for member in tf.getmembers():
                    if member.name == "manifest.json" or member.name.startswith("/") or ".." in member.name.split("/"):
                        continue
                    data = tf.extractfile(member)
                    if data is None:
                        continue
                    dest = os.path.join(self.install_dir, member.name)
                    util.ensure_dir(os.path.dirname(dest), 0o750)
                    with open(dest, "wb") as fh:
                        fh.write(data.read())
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason_code": "install_failed", "error": str(exc),
                    "rollback_point": rp}
        return {"ok": True, "name": m.name, "version": m.version,
                "caps": m.capabilities, "rollback_point": rp}

    def lock(self, installed: list[dict]) -> dict:
        data = {"format": "ao-lock/1", "packages": sorted(installed, key=lambda x: x.get("name", ""))}
        data["hash"] = fvc.sha(data)
        with open(os.path.join(self.install_dir, "lock.json"), "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, sort_keys=True)
        return data


@dataclass
class CapabilityToken:
    scope: str
    issued_at: float = field(default_factory=time.time)
    ttl: float = 60.0
    used: bool = False
    token_id: str = ""

    def __post_init__(self):
        if not self.token_id:
            self.token_id = fvc.sha({"s": self.scope, "t": self.issued_at})[:16]

    def valid(self, scope: str, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        return (not self.used) and self.scope == scope and (now - self.issued_at) <= self.ttl

    def consume(self, scope: str, now: Optional[float] = None) -> bool:
        if not self.valid(scope, now):
            return False
        self.used = True
        return True
