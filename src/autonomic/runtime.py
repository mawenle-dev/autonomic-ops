"""Constitution runtime: the bridge between the FVC-R model and real execution.

Before this module existed, ``fvc`` (Fact/Verdict/Covenant/Receipt) and the
budget account were only exercised by unit tests and the audit script: the
executor never produced a receipt and never debited a budget. This module
closes that gap:

  * one Covenant per process, materialised from ``constitution`` config;
  * a *persistent* five-dimension budget that survives restarts;
  * an append-only, hash-chained receipt journal;
  * a negative-ledger writer for refusals (no action ever fails silently);
  * optional dual-key enforcement for irreversible actions.

Everything is stdlib-only and file-backed so an operator can verify it offline.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from typing import Optional

from . import fvc, ledger, util

IRREVERSIBLE = {"file.delete", "file.delete.hard", "system.config.modify", "plugin.install"}

# Generous but finite defaults: the point is that the dimension is *enforced*,
# not that the numbers are small. An operator can tighten them in config.
DEFAULT_BUDGET = {
    "bytes": 10 * 1024 ** 3,
    "files": 2000,
    "actions": 2000,
    "blast": 30,
    "rate": 200,
}


class PersistentBudget:
    """Five-dimension sliding-window budget persisted to a single JSON file."""

    def __init__(self, path: str, budget: fvc.Budget, window_seconds: float = 3600.0):
        self.path = path
        self.budget = budget
        self.window = float(window_seconds)
        self._lock = threading.Lock()
        util.ensure_dir(os.path.dirname(path), 0o750)

    # ---------------------------------------------------------------- storage
    def _load(self, now: float) -> list[dict]:
        try:
            raw = json.loads(util.read_text(self.path, max_bytes=2 * 1024 ** 2) or "{}")
        except (ValueError, OSError):
            raw = {}
        rows = raw.get("used") or []
        cutoff = now - self.window
        return [r for r in rows if float(r.get("t", 0)) >= cutoff]

    def _save(self, rows: list[dict]) -> None:
        body = {"window_seconds": self.window, "used": rows[-5000:]}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(body, fh, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, self.path)

    def used(self, now: Optional[float] = None) -> dict:
        now = now if now is not None else time.time()
        with self._lock:
            rows = self._load(now)
            out = {d: 0 for d in fvc.BUDGET_DIMS}
            for r in rows:
                for d in fvc.BUDGET_DIMS:
                    out[d] += int(r.get(d, 0))
            return out

    def debit(self, cost: dict, now: Optional[float] = None) -> tuple[bool, str]:
        now = now if now is not None else time.time()
        with self._lock:
            rows = self._load(now)
            caps = self.budget.to_dict()
            current = {d: 0 for d in fvc.BUDGET_DIMS}
            for r in rows:
                for d in fvc.BUDGET_DIMS:
                    current[d] += int(r.get(d, 0))
            for d in fvc.BUDGET_DIMS:
                want = int(cost.get(d, 0))
                if want <= 0:
                    continue
                cap = caps[d]
                # 0 means "no consumption allowed" (red line), -1 means unlimited.
                if cap == 0 or (cap > 0 and current[d] + want > cap):
                    return False, f"budget_exhausted:{d}"
            row = {"t": round(now, 3)}
            for d in fvc.BUDGET_DIMS:
                row[d] = int(cost.get(d, 0))
            rows.append(row)
            self._save(rows)
            return True, ""


class ReceiptJournal:
    """Append-only receipt journal with a hash chain (prev_hash -> hash)."""

    def __init__(self, path: str):
        self.path = path
        util.ensure_dir(os.path.dirname(path), 0o750)
        self._lock = threading.Lock()
        self._seq, self._prev = self._tail()

    def _tail(self) -> tuple[int, str]:
        if not os.path.exists(self.path):
            return 0, ""
        last = None
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    last = line
        if not last:
            return 0, ""
        try:
            e = json.loads(last)
            return int(e.get("seq", 0)), str(e.get("hash", ""))
        except ValueError:
            return 0, ""

    def append(self, receipt: fvc.Receipt, request: dict, decision: dict,
               result: dict, covenant_id: str) -> dict:
        with self._lock:
            receipt.prev_hash = self._prev
            receipt.hash = fvc.sha(receipt.signable())
            self._seq += 1
            entry = {
                "seq": self._seq,
                "type": "receipt",
                "ts": util.iso(),
                "epoch": round(util.now(), 3),
                "covenant_id": covenant_id,
                "request": request,
                "decision": decision,
                "result": result,
                "receipt": receipt.signable(),
                "prev_hash": self._prev,
                "hash": "",
            }
            entry["hash"] = fvc.sha({k: v for k, v in entry.items() if k != "hash"})
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            self._prev = entry["hash"]
            return entry

    def records(self, limit: int = 0) -> list[dict]:
        out = []
        if not os.path.exists(self.path):
            return out
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    out.append({"type": "corrupt", "raw": line[:120]})
                    continue
                body = {k: v for k, v in e.items() if k != "hash"}
                if e.get("hash") and fvc.sha(body) != e["hash"]:
                    e["tampered"] = True
                out.append(e)
        return out[-limit:] if limit else out

    def last(self) -> dict:
        rows = self.records(1)
        return rows[-1] if rows else {}

    def verify_chain(self) -> dict:
        """Recompute every entry hash and the prev_hash link; report first break."""
        rows = self.records()
        prev = ""
        for i, e in enumerate(rows, start=1):
            if e.get("type") == "corrupt":
                return {"ok": False, "checked": i, "broken_at": i, "reason": "corrupt_json"}
            if e.get("tampered"):
                return {"ok": False, "checked": i, "broken_at": i, "reason": "hash_mismatch"}
            if str(e.get("prev_hash", "")) != prev:
                return {"ok": False, "checked": i, "broken_at": i, "reason": "prev_hash_mismatch"}
            prev = str(e.get("hash", ""))
        return {"ok": True, "checked": len(rows), "last_hash": prev}


class ConstitutionRuntime:
    def __init__(self, config, logger=None):
        self.config = config
        self.log = logger
        data_dir = str(config.get("agent.data_dir"))
        self.data_dir = data_dir
        self.journal = ReceiptJournal(os.path.join(data_dir, "receipts", "receipts.jsonl"))
        self.negatives = ledger.NegativeLedger(os.path.join(data_dir, "ledger", "negative.jsonl"))
        self._lock = threading.Lock()

        budget = fvc.Budget(**self._budget_from_config())
        window = float(config.get("constitution.budget_window_seconds", 3600) or 3600)
        self.budget = PersistentBudget(
            os.path.join(data_dir, "state", "budget.json"), budget, window)
        scope = str(config.get("constitution.scope") or f"host:{socket.gethostname()}")
        expiry_hours = float(config.get("constitution.covenant_hours", 24) or 24)
        self.covenant = fvc.Covenant(
            scope=scope, budget=budget, expiry=time.time() + expiry_hours * 3600,
            obligations={"undo": True, "notify": False, "verify": True},
            signer="local-operator")
        self.two_key_enabled = bool(config.get("constitution.two_key.enabled", False))
        self.two_key = None
        if self.two_key_enabled:
            from .two_keys import TwoKeyVault
            policy_key = str(config.get("constitution.two_key.policy_key"))
            operator_key = str(config.get("constitution.two_key.operator_key"))
            self.two_key = TwoKeyVault(policy_key, operator_key)

    # ------------------------------------------------------------- covenant
    def _budget_from_config(self) -> dict:
        out = dict(DEFAULT_BUDGET)
        for dim, default in DEFAULT_BUDGET.items():
            value = self.config.get(f"constitution.budget.{dim}", default)
            try:
                out[dim] = int(value)
            except (TypeError, ValueError):
                out[dim] = default
        return out

    def covenant_dict(self) -> dict:
        return {"id": self.covenant.id, "scope": self.covenant.scope,
                "budget": self.covenant.budget.to_dict(), "expiry": self.covenant.expiry}

    # -------------------------------------------------------------- precheck
    @staticmethod
    def _estimate(request) -> dict:
        params = request.params or {}
        raw_bytes = params.get("max_bytes") or params.get("bytes") or 0
        try:
            est_bytes = int(raw_bytes)
        except (TypeError, ValueError):
            est_bytes = 0
        est_files = 1
        if request.action in ("disk.cleanup", "cache.cleanup", "file.delete", "log.rotate"):
            est_files = max(1, int(params.get("max_files") or 1))
        return {"bytes": est_bytes, "files": est_files, "actions": 1,
                "blast": 1, "rate": 1}

    def precheck(self, request, decision) -> tuple[bool, str]:
        """Called *before* any side effect. Refuses on budget or dual-key."""
        if self.two_key is not None:
            payload = {"action": request.action, "target": request.target,
                       "request_id": request.id, "params_hash": fvc.sha(request.params)}
            irreversible = request.action in IRREVERSIBLE or bool(request.params.get("irreversible"))
            if irreversible:
                ok, why = self.two_key.verify_dual(payload, request.params.get("dual") or {})
                if not ok:
                    self.negatives.record("dual_key_missing", request.target,
                                          trigger=request.rule_id or request.id,
                                          checks=[why or "dual_key_missing"])
                    return False, f"dual_key:{why or 'missing'}"
        if not self.covenant.valid():
            self.negatives.record("approval_timeout", request.target,
                                  trigger=request.rule_id or request.id,
                                  checks=["covenant_expired"])
            return False, "covenant_expired"
        ok, why = self.budget.debit(self._estimate(request))
        if not ok:
            self.negatives.record("budget_exhausted", request.target,
                                  trigger=request.rule_id or request.id,
                                  checks=[why])
            return False, why
        return True, ""

    def record_refusal(self, request, reason_code: str, detail: str) -> None:
        try:
            self.negatives.record(reason_code, request.target,
                                  trigger=request.rule_id or request.id,
                                  checks=[detail[:200]])
        except ValueError:
            if self.log:
                self.log.warning("unknown negative reason_code=%s", reason_code)

    # -------------------------------------------------------------- finalize
    def finalize(self, request, decision, result) -> Optional[dict]:
        """Build a Receipt for every executed action (success or failure)."""
        if result.skipped and not result.ok:
            return None
        if result.rollback_point:
            undo = {"kind": "snapshot", "ref": str(result.rollback_point)}
        elif result.changed_files:
            undo = {"kind": "none", "ref": "irreversible"}
        else:
            undo = {"kind": "noop", "ref": "no-mutation"}
        fact_refs = [request.finding_id] if request.finding_id else ["manual"]
        verdict = {"id": request.rule_id or request.finding_id or request.id,
                   "chain_hash": fvc.sha([request.action, request.target, request.reason])}
        txn = {"id": request.id, "action": request.action, "target": request.target,
               "ok": bool(result.ok), "verified": bool(result.verified),
               "dry_run": bool(getattr(request, "dry_run", False)),
               "changed": len(result.changed_files)}
        try:
            receipt = fvc.Receipt(
                trace_id=request.id,
                fact_refs=fact_refs,
                verdict=verdict,
                covenant={"before": self.covenant.id, "after": self.covenant.id},
                txn=txn,
                undo=undo,
                shadow={"predicted_error": None},
                keys=[],
            )
        except ValueError as exc:
            if self.log:
                self.log.error("receipt construction refused: %s", exc)
            self.negatives.record("undo_unavailable", request.target,
                                  trigger=request.rule_id or request.id, checks=[str(exc)])
            return None
        entry = self.journal.append(
            receipt, request.to_dict(), decision.to_dict(), result.to_dict(), self.covenant.id)
        result.receipt_id = receipt.id
        result.receipt_hash = receipt.hash
        return entry

    # ------------------------------------------------------------------ read
    def status(self) -> dict:
        return {
            "covenant": self.covenant_dict(),
            "budget": {"caps": self.covenant.budget.to_dict(), "used": self.budget.used()},
            "receipts": {"last": self.journal.last(), "count": len(self.journal.records()),
                         "chain": self.journal.verify_chain()},
            "negative": self.negatives.coverage(),
            "two_key": {"enabled": self.two_key_enabled},
        }
