"""Counsel / optional read-only advisor (constitution X-1/ch.6): off by default,
local endpoint only, evidence pack (references + digests, never raw text),
7 read-only tools, strict output schema, proposal-only execution (double-signed
elsewhere), and replayable trace. The advisor has NO execution handle by design.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import fvc, util

TOOLS = ("read_facts", "read_verdicts", "read_receipts", "read_covenants",
         "read_choreo", "read_compliance", "explain")
OUTPUT_SCHEMA = ("cause", "confidence", "evidence_refs", "suggested_txn", "risk")


@dataclass
class EvidencePack:
    window: dict
    facts: list           # references/digests only
    changes: list
    similar: list
    digest: str = ""

    def __post_init__(self):
        if not self.digest:
            self.digest = fvc.sha({"w": self.window, "f": self.facts,
                                   "c": self.changes, "s": self.similar})[:16]

    def to_dict(self) -> dict:
        return {"window": self.window, "facts": self.facts, "changes": self.changes,
                "similar": self.similar, "digest": self.digest}


@dataclass
class AiTrace:
    model: str
    template_version: str
    evidence_digest: str
    output: dict
    schema_ok: bool
    human_edit: str = ""
    ts: float = field(default_factory=time.time)
    id: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = fvc.sha({"m": self.model, "e": self.evidence_digest, "t": self.ts})[:16]

    def replay(self) -> dict:
        return {"model": self.model, "template_version": self.template_version,
                "evidence_digest": self.evidence_digest, "output": self.output,
                "human_edit": self.human_edit, "ts": self.ts}


def validate_output(output: dict) -> tuple[bool, list[str]]:
    missing = [k for k in OUTPUT_SCHEMA if k not in output]
    if "evidence_refs" in output and not isinstance(output["evidence_refs"], list):
        missing.append("evidence_refs:list")
    # every conclusion must cite evidence
    if "evidence_refs" in output and not output["evidence_refs"] and output.get("cause"):
        missing.append("evidence_refs:empty")
    return (not missing), missing


class Counsel:
    def __init__(self, enabled: bool = False, tools: Optional[dict[str, Callable]] = None,
                 trace_dir: str = ""):
        self.enabled = enabled                  # default off (ch.6)
        self.tools = tools or {}
        self.trace_dir = trace_dir
        self.traces: dict[str, AiTrace] = {}
        if trace_dir:
            util.ensure_dir(trace_dir, 0o750)

    def call_tool(self, name: str, *args, **kwargs):
        """Only the 7 read-only tools exist; no exec tool can be registered."""
        if name not in TOOLS:
            raise PermissionError(f"tool not available: {name}")
        fn = self.tools.get(name)
        if not fn:
            return {"error": "tool_not_configured"}
        return fn(*args, **kwargs)

    def ask(self, pack: EvidencePack, output: dict, model: str = "local",
            template_version: str = "1") -> dict:
        if not self.enabled:
            return {"ok": False, "reason": "counsel_disabled"}
        ok, missing = validate_output(output)
        if not ok:
            return {"ok": False, "reason_code": "schema_rejected", "missing": missing}
        trace = AiTrace(model, template_version, pack.digest, output, schema_ok=True)
        self.traces[trace.id] = trace
        if self.trace_dir:
            with open(os.path.join(self.trace_dir, f"{trace.id}.json"), "w", encoding="utf-8") as fh:
                json.dump(trace.replay(), fh, ensure_ascii=False, sort_keys=True)
        # proposal only: no execution handle exists on this object
        return {"ok": True, "trace_id": trace.id, "proposal": output.get("suggested_txn"),
                "requires_double_sign": True}

    def replay(self, trace_id: str) -> dict:
        t = self.traces.get(trace_id)
        return t.replay() if t else {}
