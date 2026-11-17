"""Ledger enhancement (constitution L-1, ch.6): negative ledger, anchors,
signed bundles + offline verification, and point-in-time replay.

Builds on audit.AuditLog's append-only hash chain; does not duplicate it.
All logic is stdlib-only and file-backed so it can be verified offline.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Optional

from . import fvc, util

REASON_CODES = (
    "whitelist_miss", "budget_exhausted", "approval_timeout", "cooldown",
    "suppressed_by_parent", "silenced", "rate_limited", "undo_unavailable",
    "shadow_exceeded",
)


def bundle_hash(bundle: dict) -> str:
    return fvc.sha(bundle)


class NegativeLedger:
    """Records 'what could have been done but was not' with a reason code."""

    def __init__(self, path: str):
        self.path = path
        self._lock = __import__("threading").Lock()
        parent = os.path.dirname(path)
        if parent:
            util.ensure_dir(parent, 0o750)

    def record(self, reason_code: str, subject: str, *, trigger: str = "",
               checks: Optional[list] = None, actor: str = "auto") -> dict:
        if reason_code not in REASON_CODES:
            raise ValueError(f"unknown reason_code: {reason_code}")
        entry = {
            "type": "absence",
            "ts": util.iso(),
            "epoch": round(util.now(), 3),
            "actor": actor,
            "trigger": trigger,
            "reason_code": reason_code,
            "subject": subject,
            "checks": checks or [],
        }
        entry["hash"] = fvc.sha(entry)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
                fh.flush()
        return entry

    def explain(self, subject: str = "", reason_code: str = "", limit: int = 50) -> list[dict]:
        out = []
        if not os.path.exists(self.path):
            return out
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if subject and subject not in str(e.get("subject", "")):
                    continue
                if reason_code and e.get("reason_code") != reason_code:
                    continue
                out.append(e)
        return out[-limit:]

    def coverage(self) -> dict:
        seen = {e.get("reason_code") for e in self.explain(limit=100000)}
        return {"total_codes": len(REASON_CODES), "covered": len(seen),
                "missing": sorted(set(REASON_CODES) - seen)}


class LedgerAnchor:
    """Daily anchor: seq_from/seq_to/root_hash/count over a hash chain."""

    def __init__(self, path: str):
        self.path = path
        parent = os.path.dirname(path)
        if parent:
            util.ensure_dir(parent, 0o750)

    def anchor(self, records: list[dict]) -> dict:
        if not records:
            raise ValueError("cannot anchor empty ledger")
        seqs = [int(r.get("seq", 0)) for r in records]
        a = {
            "type": "anchor",
            "date": util.iso()[:10],
            "seq_from": min(seqs),
            "seq_to": max(seqs),
            "count": len(records),
            "root_hash": fvc.sha([r.get("hash", "") for r in records]),
            "created_at": util.iso(),
        }
        a["hash"] = fvc.sha(a)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(a, ensure_ascii=False, sort_keys=True) + "\n")
        return a

    def list(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("hash") and fvc.sha({k: v for k, v in e.items() if k != "hash"}) != e["hash"]:
                    e["tampered"] = True
                out.append(e)
        return out


def _bundle_body(bundle: dict) -> dict:
    """Canonical body used for BOTH the bundle hash and the signature.

    Excludes ``hash`` and ``signature`` so the two operations are consistent
    regardless of whether a signature has been attached yet.
    """
    return {k: v for k, v in bundle.items() if k not in ("hash", "signature")}


def export_bundle(records: list[dict], anchors: list[dict], signer: str = "",
                  private_seed: Optional[bytes] = None) -> dict:
    """Create a ledger export bundle.

    If a private seed is supplied the bundle is signed with real Ed25519 over
    its canonical body; ``signer`` is the hex public key. Without a seed the
    bundle is unsigned (hash-only) and verify_bundle reports signature_ok=None.
    """
    bundle = {
        "format": "ao-ledger-bundle/1",
        "exported_at": util.iso(),
        "records": records,
        "anchors": anchors,
        "root_hash": fvc.sha([r.get("hash", "") for r in records]),
        "signer": signer,
        "signature": "",
    }
    # Resolve the signer (public key) BEFORE hashing, so the canonical body is
    # final when both the hash and the signature are computed over it.
    if private_seed is not None and not signer:
        from . import signing
        bundle["signer"] = signing.hexlify(signing.public_key_from_seed(private_seed))
    bundle["hash"] = fvc.sha(_bundle_body(bundle))
    if private_seed is not None:
        from . import signing
        bundle["signature"] = signing.hexlify(
            signing.sign(private_seed, fvc.canon(_bundle_body(bundle)).encode()))
    return bundle


def verify_bundle(bundle: dict, public_key_hex: Optional[str] = None) -> dict:
    """Offline verification: recompute hashes and, if present, the Ed25519 sig."""
    errors = []
    root = bundle.get("root_hash")
    calc = fvc.sha([r.get("hash", "") for r in bundle.get("records", [])])
    if root != calc:
        errors.append("root_hash_mismatch")
    if bundle.get("hash") != fvc.sha(_bundle_body(bundle)):
        errors.append("bundle_hash_mismatch")
    sig_ok = None
    sig = bundle.get("signature") or ""
    pub = public_key_hex or bundle.get("signer") or ""
    if sig and pub:
        from . import signing
        try:
            sig_ok = signing.verify(signing.unhexlify(pub),
                                    fvc.canon(_bundle_body(bundle)).encode(),
                                    signing.unhexlify(sig))
        except Exception:  # noqa: BLE001
            sig_ok = False
        if not sig_ok:
            errors.append("signature_invalid")
    return {"ok": not errors, "errors": errors, "signature_ok": sig_ok,
            "count": len(bundle.get("records", []))}


def replay(records: list[dict], at_epoch: float) -> dict:
    """Rebuild the state of covenants/objects at a point in time."""
    state = {"covenants": {}, "objects": {}, "verdicts": {}}
    for r in records:
        if float(r.get("epoch", 0)) > at_epoch:
            continue
        rt = r.get("type", "receipt")
        if rt == "covenant" and r.get("id"):
            state["covenants"][r["id"]] = r
        elif rt == "object" and r.get("id"):
            state["objects"][r["id"]] = r
        elif rt == "verdict" and r.get("id"):
            state["verdicts"][r["id"]] = r
    state["replayed_at"] = at_epoch
    state["hash"] = fvc.sha(state)
    return state
