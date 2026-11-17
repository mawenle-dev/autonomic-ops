"""Choreography engine (constitution R-1/ch.6): DAG with cycle detection and
topological order, freeze/resume via checkpoints, approval SLA, concurrency
locks, nesting depth limit, and amend (changes flow, preserves history).
Stdlib only; no execution handler - steps call an injected runner.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import fvc, util


class ChoreoError(ValueError):
    pass


@dataclass
class Step:
    id: str
    when: dict            # must be a verdict expression (type-enforced)
    action: str
    params: dict = field(default_factory=dict)
    retries: int = 0
    timeout: float = 60.0
    on_failure: str = "freeze"   # freeze | undo | continue | escalate


@dataclass
class DAG:
    steps: list
    edges: list           # [(from_id, to_id)]

    def validate(self) -> None:
        ids = {s.id for s in self.steps}
        if len(ids) != len(self.steps):
            raise ChoreoError("duplicate step id")
        for a, b in self.edges:
            if a not in ids or b not in ids:
                raise ChoreoError(f"edge references unknown step: {a}->{b}")
        for s in self.steps:
            if not isinstance(s.when, dict) or not s.when.get("verdict"):
                raise ChoreoError(f"step {s.id}: condition must be a verdict expression")

    def topo_order(self) -> list[str]:
        indeg = {s.id: 0 for s in self.steps}
        adj = {s.id: [] for s in self.steps}
        for a, b in self.edges:
            indeg[b] += 1
            adj[a].append(b)
        ready = [k for k, v in indeg.items() if v == 0]
        order = []
        while ready:
            node = ready.pop(0)
            order.append(node)
            for nxt in adj[node]:
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    ready.append(nxt)
        if len(order) != len(self.steps):
            raise ChoreoError("DAG contains a cycle")
        return order


class RunLock:
    """Named locks: one instance per target + a global cap."""

    def __init__(self, global_cap: int = 8):
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._sem = threading.BoundedSemaphore(max(1, global_cap))

    def acquire(self, target: str) -> bool:
        if not self._sem.acquire(blocking=False):
            return False
        with self._guard:
            lock = self._locks.setdefault(target, threading.Lock())
        if not lock.acquire(blocking=False):
            self._sem.release()
            return False
        return True

    def release(self, target: str) -> None:
        with self._guard:
            lock = self._locks.get(target)
        if lock:
            lock.release()
        self._sem.release()


class Choreographer:
    def __init__(self, state_dir: str, runner: Callable[[Step], dict],
                 max_depth: int = 3, lock: Optional[RunLock] = None):
        self.state_dir = state_dir
        self.runner = runner
        self.max_depth = max_depth
        self.lock = lock or RunLock()
        util.ensure_dir(state_dir, 0o750)

    def _state_path(self, run_id: str) -> str:
        return os.path.join(self.state_dir, f"{run_id}.json")

    def run(self, run_id: str, dag: DAG, depth: int = 1,
            now: Optional[float] = None) -> dict:
        dag.validate()
        if depth > self.max_depth:
            raise ChoreoError(f"nesting depth {depth} exceeds {self.max_depth}")
        order = dag.topo_order()
        state = {"id": run_id, "order": order, "idx": 0, "results": {},
                 "frozen": False, "started_at": now or util.now()}
        self._save(run_id, state)  # checkpoint before step 0
        by_id = {s.id: s for s in dag.steps}
        for step_id in order:
            step = by_id[step_id]
            if not self.lock.acquire(step.action):
                state["frozen"] = True
                state["reason"] = "lock_busy"
                self._save(run_id, state)
                return state
            try:
                result = self.runner(step)
                state["results"][step_id] = result
                state["idx"] += 1
                self._save(run_id, state)  # checkpoint after each step
            except Exception as exc:  # noqa: BLE001
                state["frozen"] = True
                state["reason"] = f"step_failed:{step_id}:{exc}"
                self._save(run_id, state)
                return state
            finally:
                self.lock.release(step.action)
        state["done"] = True
        self._save(run_id, state)
        return state

    def resume(self, run_id: str, dag: DAG) -> dict:
        state = self._load(run_id)
        if not state.get("frozen"):
            return state
        # resume from last checkpoint (re-runs from idx onward)
        order = state["order"]
        state["frozen"] = False
        self._save(run_id, state)
        by_id = {s.id: s for s in dag.steps}
        for step_id in order[state["idx"]:]:
            state["results"][step_id] = self.runner(by_id[step_id])
            state["idx"] += 1
            self._save(run_id, state)
        state["done"] = True
        self._save(run_id, state)
        return state

    def freeze(self, run_id: str, reason: str = "operator") -> dict:
        state = self._load(run_id)
        state["frozen"] = True
        state["reason"] = reason
        self._save(run_id, state)
        return state

    def amend(self, run_id: str, new_dag: DAG) -> dict:
        """Change the flow but keep the old history under a separate record."""
        old = self._load(run_id)
        history = self._load_history(run_id)
        history.append({"amended_at": util.iso(), "old_order": old.get("order", []),
                        "old_results": old.get("results", {})})
        self._save_history(run_id, history)
        new_dag.validate()
        return self.run(run_id, new_dag)

    def _save(self, run_id, state):
        with open(self._state_path(run_id), "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, sort_keys=True)

    def _load(self, run_id):
        with open(self._state_path(run_id), "r", encoding="utf-8") as fh:
            return json.load(fh)

    def _save_history(self, run_id, hist):
        with open(self._state_path(run_id) + ".history", "w", encoding="utf-8") as fh:
            json.dump(hist, fh, ensure_ascii=False, sort_keys=True)

    def _load_history(self, run_id):
        p = self._state_path(run_id) + ".history"
        if not os.path.exists(p):
            return []
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)


def sla_disposition(elapsed: float, sla: float, on_timeout: str = "abort") -> str:
    """Approval SLA: default abort, or escalate (ch.6)."""
    if elapsed <= sla:
        return "wait"
    return on_timeout if on_timeout in ("abort", "escalate") else "abort"
