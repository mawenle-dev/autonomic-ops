"""FVC-R core model: Fact, Verdict, Covenant, Receipt (constitution ch.4/ch.7).

This module is dependency-free (stdlib only) and holds the four atomic objects
plus the two original mechanisms that must be shared across subsystems:

  * verdict half-life  T = base(kind) * confidence * freshness
  * five-dimension budget account (atomic debit, sliding window)

Schema hard rules (ch.7) are enforced in the constructors: an object that lacks
a required field cannot be built, so an invalid action cannot even be expressed.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

# ---- canonical helpers ----------------------------------------------------

def canon(obj: Any) -> str:
    """Deterministic canonical JSON used by every hash in the system."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(obj: Any) -> str:
    return hashlib.sha256(canon(obj).encode("utf-8")).hexdigest()


# ---- Fact -----------------------------------------------------------------

QUALITY = ("measured", "derived", "stale")
LABEL_KEY_WHITELIST = {"host", "unit", "device", "service", "ip", "path", "kind", "source", "env"}


@dataclass
class Fact:
    metric: str
    value: float
    labels: dict = field(default_factory=dict)
    unit: str = ""
    ts: float = field(default_factory=time.time)
    source: str = "local"
    quality: str = "measured"
    id: str = ""

    def __post_init__(self):
        if self.quality not in QUALITY:
            raise ValueError(f"invalid fact quality: {self.quality}")
        # label key whitelist (ch.7 hard constraint)
        self.labels = {k: v for k, v in self.labels.items() if k in LABEL_KEY_WHITELIST}
        if not self.id:
            self.id = sha({"m": self.metric, "l": self.labels, "t": round(self.ts, 3)})[:16]


# ---- Verdict ---------------------------------------------------------------

VERDICT_STATES = ("sprout", "formed", "covenanted", "executed", "reverted", "fixed", "retired")
BASE_HALF_LIFE = {"disk": 1800.0, "process": 300.0, "security": 7200.0, "compliance": 86400.0}
CONFIDENCE = {1: 0.6, 2: 0.85, 3: 0.95}


def confidence_for(evidence_count: int, independent: bool = True, fresh: bool = True) -> float:
    """confidence = base(evidence count) * independence * freshness (ch.4.2)."""
    base = CONFIDENCE.get(max(1, min(3, evidence_count)), 0.95)
    return round(base * (1.0 if independent else 0.7) * (1.0 if fresh else 0.5), 4)


@dataclass
class Verdict:
    kind: str
    subject: str
    severity: str
    chain: list = field(default_factory=list)
    evidence_refs: list = field(default_factory=list)
    confidence: float = 0.6
    state: str = "sprout"
    id: str = ""
    born_at: float = field(default_factory=time.time)
    retire_at: float = 0.0
    recur_of: Optional[str] = None
    last_seen: float = field(default_factory=time.time)

    def __post_init__(self):
        # ch.7: a verdict without chain and retire_at cannot be built
        if not self.chain:
            raise ValueError("verdict requires a non-empty reasoning chain")
        if not self.id:
            self.id = sha({"k": self.kind, "s": self.subject, "c": self.chain})[:16]
        if not self.retire_at:
            self.retire_at = self.born_at + self.half_life_seconds()

    def half_life_seconds(self, fresh: bool = True) -> float:
        base = BASE_HALF_LIFE.get(self.kind, 1800.0)
        return base * self.confidence * (1.0 if fresh else 0.5)

    def visible(self, now: float | None = None) -> float:
        """Visibility decays by half each half-life; 0 at/after retire_at."""
        now = now if now is not None else time.time()
        if now >= self.retire_at:
            return 0.0
        elapsed = now - self.born_at
        hl = max(1.0, self.retire_at - self.born_at) / 3.0  # 3 half-lives to retire
        return round(2 ** (-elapsed / hl), 4)

    def refresh(self, now: float | None = None) -> None:
        """A confirming fact extends the verdict (revival carries history)."""
        now = now if now is not None else time.time()
        self.last_seen = now
        hl = max(1.0, self.retire_at - self.born_at) / 3.0
        self.retire_at = max(self.retire_at, now + 3 * hl)

    def retire(self) -> None:
        self.state = "retired"


# ---- Covenant -------------------------------------------------------------

BUDGET_DIMS = ("bytes", "files", "actions", "blast", "rate")


@dataclass
class Budget:
    bytes: int = 0
    files: int = 0
    actions: int = 0
    blast: int = 0
    rate: int = 0

    def to_dict(self) -> dict:
        return {d: int(getattr(self, d)) for d in BUDGET_DIMS}


class BudgetAccount:
    """Sliding-window atomic debit (ch.4.2, F-02 of budget design)."""

    def __init__(self, budget: Budget, window_seconds: float = 3600.0):
        self.budget = budget
        self.window = window_seconds
        self._used: list[tuple[float, dict]] = []
        self._lock = threading.Lock()

    def _prune(self, now: float):
        cutoff = now - self.window
        self._used = [(t, u) for t, u in self._used if t >= cutoff]

    def used(self, now: float | None = None) -> dict:
        now = now if now is not None else time.time()
        with self._lock:
            self._prune(now)
            out = {d: 0 for d in BUDGET_DIMS}
            for _t, u in self._used:
                for d in BUDGET_DIMS:
                    out[d] += u.get(d, 0)
            return out

    def debit(self, cost: dict, now: float | None = None) -> tuple[bool, str]:
        """Atomically deduct; refuse and return reason if any dimension exhausted."""
        now = now if now is not None else time.time()
        with self._lock:
            self._prune(now)
            current = {d: 0 for d in BUDGET_DIMS}
            for _t, u in self._used:
                for d in BUDGET_DIMS:
                    current[d] += u.get(d, 0)
            caps = self.budget.to_dict()
            for d in BUDGET_DIMS:
                want = int(cost.get(d, 0))
                if want <= 0:
                    continue
                # A zero cap means the dimension permits no consumption at all
                # (security red line: budget=0 must refuse, never treat as
                # unlimited). An explicit zero budget with a positive cost is
                # exhausted; only a genuinely unlimited covenant uses -1.
                cap = caps[d]
                if cap == 0 or (cap > 0 and current[d] + want > cap):
                    return False, f"budget_exhausted:{d}"
            self._used.append((now, {d: int(cost.get(d, 0)) for d in BUDGET_DIMS}))
            return True, ""


@dataclass
class Covenant:
    scope: str
    budget: Budget
    expiry: float
    obligations: dict = field(default_factory=lambda: {"undo": True, "notify": False, "verify": True})
    signer: str = ""
    signature: str = ""
    id: str = ""

    def __post_init__(self):
        # ch.7: a covenant without all five budget dimensions cannot be signed
        b = self.budget.to_dict()
        if any(v is None for v in b.values()):
            raise ValueError("covenant budget must define all five dimensions")
        if not self.id:
            self.id = sha({"scope": self.scope, "budget": b, "expiry": self.expiry})[:16]

    def valid(self, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) < self.expiry


# ---- Receipt --------------------------------------------------------------

RECEIPT_ELEMENTS = ("trace_id", "fact_refs", "verdict", "covenant", "txn", "undo")


@dataclass
class Receipt:
    trace_id: str
    fact_refs: list
    verdict: dict        # {id, chain_hash}
    covenant: dict       # {before, after}
    txn: dict
    undo: dict           # {kind, ref} - required; no undo -> cannot sign
    shadow: dict = field(default_factory=dict)
    keys: list = field(default_factory=list)
    prev_hash: str = ""
    hash: str = ""
    id: str = ""
    ts: float = field(default_factory=time.time)

    def __post_init__(self):
        missing = [e for e in RECEIPT_ELEMENTS if not getattr(self, e, None)]
        if missing:
            raise ValueError(f"receipt missing required elements: {missing}")
        if not self.id:
            self.id = sha({"trace": self.trace_id, "txn": self.txn, "ts": self.ts})[:16]
        if not self.hash:
            self.hash = sha(self.signable())

    def signable(self) -> dict:
        return {
            "id": self.id, "trace_id": self.trace_id, "fact_refs": self.fact_refs,
            "verdict": self.verdict, "covenant": self.covenant, "txn": self.txn,
            "undo": self.undo, "shadow": self.shadow, "keys": self.keys,
            "prev_hash": self.prev_hash, "ts": self.ts,
        }

    def verify(self) -> bool:
        return self.hash == sha(self.signable())
