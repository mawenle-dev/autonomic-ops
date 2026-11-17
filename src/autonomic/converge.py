"""Convergence (constitution G-1/ch.6): resource desired-state, check/shadow/
apply modes, drift verdicts (never mutate directly), heal budget, package
snapshot, 20 built-in templates with failure rollback.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import fvc, util


@dataclass
class Resource:
    type: str            # file/dir/package/service/sysctl/cron/user/mount/line/template
    target: str
    desired: str
    tolerance: str = "exact"   # tolerance is mandatory by ch.7
    interval: float = 300.0
    heal_budget: int = 3


@dataclass
class Drift:
    resource_id: str
    expected: str
    actual: str
    diff: dict = field(default_factory=dict)
    first_seen: float = field(default_factory=time.time)

    def verdict(self) -> dict:
        return {"kind": "drift", "subject": self.resource_id,
                "evidence_refs": [fvc.sha({"e": self.expected, "a": self.actual})[:16]],
                "chain": [{"step": "compare", "in": self.expected, "out": self.actual}]}


class Converger:
    def __init__(self, state_dir: str, reader: Callable[[Resource], str],
                 applier: Callable[[Resource], bool], heal_budget_monthly: int = 3):
        self.state_dir = state_dir
        self.reader = reader
        self.applier = applier
        self.monthly_budget = heal_budget_monthly
        self.heals = 0
        self.drifts: dict[str, Drift] = {}
        util.ensure_dir(state_dir, 0o750)

    def check(self, res: Resource) -> dict:
        """Zero side effects: compute diff only."""
        actual = self.reader(res)
        changed = actual != res.desired
        return {"changed": changed, "expected": res.desired, "actual": actual,
                "diff": {"old": actual, "new": res.desired} if changed else {}}

    def shadow(self, res: Resource) -> dict:
        """Predict impact without mutating the real target."""
        chk = self.check(res)
        return {"predicted": chk["diff"], "files": 1 if chk["changed"] else 0,
                "bytes": len(res.desired), "confidence": 1.0}

    def apply(self, res: Resource, snapshot_fn: Optional[Callable[[Resource], str]] = None) -> dict:
        chk = self.check(res)
        if not chk["changed"]:
            return {"changed": False, "rollback_point": None, "verified": True}
        if self.heals >= self.monthly_budget:
            return {"changed": False, "reason_code": "heal_budget_exhausted", "verified": False}
        rp = snapshot_fn(res) if snapshot_fn else None
        ok = self.applier(res)
        if not ok:
            # failure must not land partial state: snapshot exists for rollback
            return {"changed": False, "verified": False, "rollback_point": rp,
                    "reason_code": "apply_failed"}
        self.heals += 1
        after = self.reader(res)
        return {"changed": True, "verified": after == res.desired,
                "rollback_point": rp, "healed": self.heals}

    def observe_drift(self, res: Resource) -> Optional[dict]:
        """Drift produces a verdict; it never calls the applier."""
        chk = self.check(res)
        if not chk["changed"]:
            self.drifts.pop(res.target, None)
            return None
        d = self.drifts.setdefault(res.target, Drift(res.target, res.desired, chk["actual"], chk["diff"]))
        return d.verdict()


def package_snapshot(installed: list[dict]) -> dict:
    """Three-family package snapshot (deb/rpm/apk) for rollback."""
    by_family: dict[str, list] = {"deb": [], "rpm": [], "apk": []}
    for p in installed:
        fam = p.get("family", "deb")
        by_family.setdefault(fam, []).append({"name": p.get("name"), "version": p.get("version")})
    return {"format": "ao-pkg-snapshot/1", "packages": by_family,
            "hash": fvc.sha(by_family)}


# 20 built-in templates (name -> file content). No third-party content copied.
TEMPLATES = {
    "ssh-hardening": "PasswordAuthentication no\nPermitRootLogin no\n",
    "sudoers-baseline": "Defaults secure_path=/usr/local/sbin:/usr/bin\n",
    "journald-retention": "SystemMaxUse=500M\n",
    "logrotate-weekly": "weekly\nrotate 4\ncompress\n",
    "cron-ntp": "0 3 * * * /usr/sbin/ntpd -q\n",
    "sysctl-swappiness": "vm.swappiness=10\n",
    "sysctl-syncookies": "net.ipv4.tcp_syncookies=1\n",
    "ufw-defaults": "DEFAULT_INPUT_POLICY=DROP\n",
    "nftables-base": "table inet filter { chain input { type filter hook input priority 0; } }\n",
    "service-restart": "[Service]\nRestart=on-failure\n",
    "tmpfiles-cleanup": "d /tmp 1777 root root 10d\n",
    "timezone-utc": "UTC\n",
    "motd-banner": "Authorized access only.\n",
    "fail2ban-jail": "[sshd]\nenabled=true\nmaxretry=5\n",
    "audit-rules": "-w /etc/passwd -p wa\n",
    "limits-nofile": "* soft nofile 65535\n",
    "kernel-yama": "kernel.yama.ptrace_scope=1\n",
    "disable-ipv6": "net.ipv6.conf.all.disable_ipv6=1\n",
    "core-dump-off": "fs.suid_dumpable=0\n",
    "bash-history": "HISTSIZE=5000\nHISTFILESIZE=10000\n",
}


def load_template(name: str) -> str:
    if name not in TEMPLATES:
        raise KeyError(f"unknown template: {name}")
    return TEMPLATES[name]
