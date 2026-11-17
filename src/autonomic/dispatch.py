"""Dispatch / event lifecycle (constitution D-1/ch.6): fingerprint dedupe,
causal inhibition from parent-child objects, quiet windows, incident merging,
attention budget, delivery tri-state, static status page export.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from . import fvc, util


@dataclass
class Incident:
    fingerprint: str
    verdicts: list = field(default_factory=list)
    timeline: list = field(default_factory=list)
    impact: dict = field(default_factory=dict)
    owner: str = ""
    state: str = "open"
    id: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = fvc.sha({"fp": self.fingerprint, "t": round(time.time())})[:16]


class Dispatcher:
    def __init__(self, state_dir: str, parents: Optional[dict] = None,
                 attention_per_night: int = 3):
        self.state_dir = state_dir
        self.parents = parents or {}       # child_id -> parent_id (object tree)
        self.attention = attention_per_night
        self.incidents: dict[str, Incident] = {}
        self.silences: list[dict] = []
        self.delivery: dict[str, str] = {}
        util.ensure_dir(state_dir, 0o750)

    @staticmethod
    def fingerprint(verdict: dict) -> str:
        return fvc.sha({"kind": verdict.get("kind"), "subject": verdict.get("subject"),
                        "evidence": sorted(verdict.get("evidence_refs", []))})[:16]

    def suppressed_by_parent(self, verdict: dict) -> bool:
        """Causal inhibition: if a parent object already has an active verdict,
        suppress the child's (structural, not tag-based)."""
        subj = verdict.get("subject")
        parent = self.parents.get(subj)
        if not parent:
            return False
        for inc in self.incidents.values():
            if inc.state == "open" and any(v.get("subject") == parent for v in inc.verdicts):
                return True
        return False

    def in_quiet_window(self, now: Optional[float] = None) -> bool:
        now = now if now is not None else util.now()
        self.silences = [s for s in self.silences if s["until"] > now]  # auto-expire
        return bool(self.silences)

    def add_silence(self, minutes: int, reason: str = "maintenance") -> dict:
        s = {"until": util.now() + minutes * 60, "reason": reason}
        self.silences.append(s)
        return s

    def emit(self, verdict: dict, now: Optional[float] = None) -> dict:
        now = now if now is not None else util.now()
        if self.suppressed_by_parent(verdict):
            return {"status": "suppressed", "reason_code": "suppressed_by_parent"}
        if self.in_quiet_window(now):
            return {"status": "silenced", "reason_code": "silenced"}
        fp = self.fingerprint(verdict)
        inc = self.incidents.get(fp)
        if inc is None:
            inc = Incident(fingerprint=fp, verdicts=[verdict])
            self.incidents[fp] = inc
        inc.verdicts.append(verdict)
        inc.timeline.append({"ts": now, "event": verdict.get("kind"), "state": verdict.get("state", "formed")})
        # attention budget: per-owner K notifications/night
        key = inc.owner or "default"
        count = sum(1 for i in self.incidents.values() if (i.owner or "default") == key)
        if count > self.attention:
            return {"status": "digested", "reason_code": "attention_budget", "incident": inc.id}
        return {"status": "notified", "incident": inc.id, "fingerprint": fp}

    def mark_delivery(self, incident_id: str, state: str) -> bool:
        if state not in ("delivered", "read", "acknowledged"):
            return False
        self.delivery[incident_id] = state
        return True

    def escalate_unacked(self) -> list[str]:
        return [i.id for i in self.incidents.values()
                if i.state == "open" and self.delivery.get(i.id) != "acknowledged"]

    def export_status_page(self, path: str) -> dict:
        """Static, offline-openable status page data (no scripts, GET-only)."""
        data = {
            "generated_at": util.iso(),
            "incidents": [
                {"id": i.id, "state": i.state, "impact": i.impact,
                 "timeline": i.timeline, "delivery": self.delivery.get(i.id, "pending")}
                for i in self.incidents.values()
            ],
        }
        html = ("<!doctype html><meta charset=utf-8><title>Status</title><h1>Status</h1>"
                "<ul>" + "".join(f"<li>{x['id']}: {x['state']} ({x['delivery']})</li>"
                                 for x in data["incidents"]) + "</ul>")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html)
        with open(path + ".json", "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, sort_keys=True)
        return {"path": path, "incidents": len(data["incidents"])}
