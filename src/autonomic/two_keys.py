"""Two-key system (constitution 4.2/#4, 11.22): irreversible actions require
policy_key + operator_key dual signature. Includes offline recovery codes and a
3-of-3 recovery flow. Keys never stored on the same medium (enforced by
separate directory paths). Stdlib only (HMAC-based; Ed25519 plug-in point kept).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Optional

from . import fvc, util


class KeyError_(Exception):
    pass


@dataclass
class RecoveryCode:
    share: str
    holder: str
    created_at: float = field(default_factory=time.time)


class TwoKeyVault:
    def __init__(self, policy_key_path: str, operator_key_path: str):
        # red line 22: keys must not live on the same medium (directory)
        if os.path.dirname(os.path.abspath(policy_key_path)) == os.path.dirname(os.path.abspath(operator_key_path)):
            raise KeyError_("policy_key and operator_key must be stored on separate media/paths")
        self.policy_path = policy_key_path
        self.operator_path = operator_key_path
        self.recovery: list[RecoveryCode] = []

    def generate(self, seed_policy: str = "", seed_operator: str = "") -> dict:
        policy = seed_policy or secrets.token_hex(32)
        operator = seed_operator or secrets.token_hex(32)
        for path, val in ((self.policy_path, policy), (self.operator_path, operator)):
            util.ensure_dir(os.path.dirname(path), 0o700)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(val)
            os.chmod(path, 0o600)
        return {"policy_generated": True, "operator_generated": True,
                "note": "store the two keys on different media"}

    def _load(self, path: str) -> bytes:
        if not os.path.exists(path):
            raise KeyError_(f"key file missing: {path}")
        with open(path, "rb") as fh:
            return fh.read().strip()

    def sign(self, payload: dict, kind: str) -> str:
        key = self._load(self.policy_path if kind == "policy" else self.operator_path)
        return hmac.new(key, fvc.canon(payload).encode(), hashlib.sha256).hexdigest()

    def dual_sign(self, payload: dict) -> dict:
        return {"policy": self.sign(payload, "policy"),
                "operator": self.sign(payload, "operator")}

    def verify_dual(self, payload: dict, signatures: dict) -> tuple[bool, str]:
        if not signatures.get("policy") or not signatures.get("operator"):
            return False, "missing_key"
        try:
            p_ok = hmac.compare_digest(self.sign(payload, "policy"), signatures["policy"])
            o_ok = hmac.compare_digest(self.sign(payload, "operator"), signatures["operator"])
        except KeyError_ as exc:
            return False, str(exc)
        if not (p_ok and o_ok):
            return False, "dual_signature_invalid"
        return True, ""

    def make_recovery_codes(self, holders: list[str]) -> list[dict]:
        """Split a recovery secret into 3 shares; all three are required."""
        if len(holders) != 3:
            raise KeyError_("recovery requires exactly 3 holders")
        secret = secrets.token_hex(16)
        # trivial XOR-style 3-share split (s1 ^ s2 ^ s3 = secret)
        a, b = secrets.token_hex(16), secrets.token_hex(16)
        c = hex(int(a, 16) ^ int(b, 16) ^ int(secret, 16))[2:].zfill(32)
        self.recovery = [RecoveryCode(a, holders[0]), RecoveryCode(b, holders[1]),
                         RecoveryCode(c, holders[2])]
        return [{"holder": r.holder, "share": r.share} for r in self.recovery]

    def recover(self, shares: list[str]) -> Optional[str]:
        if len(shares) != 3:
            return None
        try:
            return hex(int(shares[0], 16) ^ int(shares[1], 16) ^ int(shares[2], 16))[2:].zfill(32)
        except ValueError:
            return None


def requires_dual_key(action: dict) -> bool:
    """Irreversible actions: hard delete, core config change, package install."""
    a = action.get("action", "")
    return a in ("hard_delete", "config-modify", "package-install") or bool(action.get("irreversible"))
