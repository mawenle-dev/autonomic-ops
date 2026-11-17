"""Sentinel (constitution S-1/ch.6): five escalation levels with auto-release,
attack narrative (7 fields), self-lock protection (never ban loopback/private),
integrity baseline (FIM, reversible), rootkit indicators, 30 compliance controls.
"""
from __future__ import annotations

import ipaddress
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from . import fvc, util

LEVELS = ("observe", "rate_limit", "challenge", "isolate", "ban")
MAX_LEVEL_TIME = {"observe": 300, "rate_limit": 600, "challenge": 900, "isolate": 1800, "ban": 3600}


def is_self_address(ip: str) -> bool:
    """Never ban loopback/private/link-local/unspecified/gateway-ish addresses."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return True  # unparseable -> treat as protected
    return a.is_loopback or a.is_private or a.is_link_local or a.is_unspecified or a.is_multicast


@dataclass
class Story:
    source: str
    target: str
    method: str
    count: int
    succeeded: bool
    action: str
    duration: float
    id: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = fvc.sha(self.to_dict())[:16]

    def to_dict(self) -> dict:
        return {"source": self.source, "target": self.target, "method": self.method,
                "count": self.count, "succeeded": self.succeeded,
                "action": self.action, "duration": self.duration}


class Sentinel:
    def __init__(self, state_dir: str):
        self.state_dir = state_dir
        self.levels: dict[str, dict] = {}
        self.stories: list[Story] = []
        self.integrity: dict[str, str] = {}
        util.ensure_dir(state_dir, 0o750)

    def set_level(self, subject: str, level: str, now: Optional[float] = None) -> dict:
        if level not in LEVELS:
            raise ValueError(f"invalid level: {level}")
        now = now if now is not None else util.now()
        entry = {"subject": subject, "level": level, "since": now,
                 "expires": now + MAX_LEVEL_TIME[level]}
        self.levels[subject] = entry
        return entry

    def auto_release(self, now: Optional[float] = None) -> list[str]:
        now = now if now is not None else util.now()
        released = [s for s, e in self.levels.items() if e["expires"] <= now]
        for s in released:
            self.levels[s]["level"] = "observe"
            self.levels[s]["expires"] = now + MAX_LEVEL_TIME["observe"]
        return released

    def ban(self, ip: str) -> dict:
        if is_self_address(ip):
            return {"ok": False, "reason_code": "self_lock_protected", "ip": ip}
        return self.set_level(ip, "ban")

    def add_story(self, story: Story) -> dict:
        self.stories.append(story)
        return story.to_dict()

    def snapshot_integrity(self, paths: list[str]) -> dict:
        """Baseline hashes; change detection produces a reversible verdict."""
        self.integrity = {p: fvc.sha({"h": util.sha256_file(p)}) for p in paths if os.path.exists(p)}
        return dict(self.integrity)

    def integrity_changes(self) -> list[str]:
        return [p for p, h in self.integrity.items()
                if not os.path.exists(p) or fvc.sha({"h": util.sha256_file(p)}) != h]


def rootkit_indicators(proc_cmdlines: list[str], suid_paths: list[str],
                       ld_preload: str, crontabs: list[str]) -> list[str]:
    """Synthetic, testable indicators - no live system mutation."""
    out = []
    if ld_preload and "/tmp" in ld_preload:
        out.append("LD_PRELOAD points into tmp")
    for p in suid_paths:
        if "/tmp" in p or "/home" in p:
            out.append(f"unexpected SUID: {p}")
    for c in crontabs:
        if "curl" in c and "| sh" in c:
            out.append("crontab pipes curl to shell")
    return out


# 30 immutable compliance control ids (ch.6: control id immutable)
CONTROL_IDS = tuple(f"C{i:02d}" for i in range(1, 31))


def compliance_score(results: dict[str, bool]) -> dict:
    passed = sum(1 for cid in CONTROL_IDS if results.get(cid))
    return {"total": len(CONTROL_IDS), "passed": passed,
            "score": round(passed / len(CONTROL_IDS) * 100, 1),
            "failed": [cid for cid in CONTROL_IDS if not results.get(cid)]}
