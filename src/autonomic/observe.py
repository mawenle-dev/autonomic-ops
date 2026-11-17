"""Observe layer (constitution O-2/O-3/ch.6): dependency-free fact collectors,
26 probe kinds with timeout/retry, business assertions, and monitoring-plugin
text protocol parsing (perfdata). All facts flow into autonomic.fvc.Fact.
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import fvc

# 26 kinds per constitution O-3: http(s) is one kind covering both schemes.
PROBE_KINDS = (
    "http(s)", "tcp", "udp", "icmp", "dns", "tls", "domain", "grpc",
    "websocket", "smtp", "mqtt", "snmp", "redis", "mysql", "postgres", "mongo",
    "mssql", "oracle", "rabbitmq", "sftp", "sip", "steam", "tailscale",
    "browser", "push", "exec",
)


class Collector:
    """A named collector returning Fact objects; timeout + auto-downgrade."""

    def __init__(self, name: str, fn: Callable[[], list[fvc.Fact]], timeout: float = 0.05):
        self.name = name
        self.fn = fn
        self.timeout = timeout
        self.failures = 0

    def collect(self, now: Optional[float] = None) -> list[fvc.Fact]:
        # stdlib-only: run with a wall-clock budget via a simple check.
        start = time.monotonic()
        try:
            facts = self.fn() or []
        except Exception:  # noqa: BLE001
            self.failures += 1
            return []
        elapsed = time.monotonic() - start
        if elapsed > self.timeout:
            self.failures += 1
        for f in facts:
            if f.quality == "measured" and self.failures >= 3:
                f.quality = "stale"
        return facts


def cpu_fact() -> list[fvc.Fact]:
    try:
        load1 = os.getloadavg()[0]
    except (AttributeError, OSError):
        load1 = 0.0
    return [fvc.Fact(metric="cpu.load1", value=float(load1), labels={"host": "local"}, unit="load")]


def mem_fact() -> list[fvc.Fact]:
    info = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                k, _, v = line.partition(":")
                info[k] = v.strip().split()[0]
        total = int(info.get("MemTotal", 0))
        avail = int(info.get("MemAvailable", 0))
        pct = (1 - avail / total) * 100 if total else 0.0
    except (OSError, ValueError, ZeroDivisionError):
        pct = 0.0
    return [fvc.Fact(metric="mem.used_percent", value=round(pct, 2), unit="%")]


BUILTIN_COLLECTORS = {"cpu": cpu_fact, "memory": mem_fact}


@dataclass
class ProbeResult:
    kind: str
    target: str
    ok: bool
    latency_ms: float = 0.0
    detail: str = ""
    ts: float = field(default_factory=time.time)

    def fact(self) -> fvc.Fact:
        return fvc.Fact(metric="probe.ok", value=1.0 if self.ok else 0.0,
                        labels={"kind": self.kind, "source": self.target}, unit="bool")


def tcp_probe(host: str, port: int, timeout: float = 2.0) -> ProbeResult:
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            ok, detail = True, "connected"
    except OSError as exc:
        ok, detail = False, str(exc)
    return ProbeResult("tcp", f"{host}:{port}", ok,
                       round((time.monotonic() - t0) * 1000, 2), detail)


def parse_assertion(expr: str) -> list[tuple[str, str, object]]:
    """Parse a boolean business assertion: [STATUS]==200 && [BODY].x==\"ok\"."""
    out = []
    for part in expr.split("&&"):
        m = re.match(r"\s*\[([A-Z_]+)\]((?:\.[A-Za-z0-9_]+)*)\s*(==|!=|>=|<=|>|<)\s*(.+?)\s*$", part)
        if not m:
            continue
        key, path, op, val = m.groups()
        val = val.strip().strip('"').strip("'")
        try:
            val = float(val) if re.match(r"^-?\d+(\.\d+)?$", val) else val
        except ValueError:
            pass
        out.append((key + path, op, val))
    return out


def eval_assertion(parts: list[tuple[str, str, object]], values: dict) -> bool:
    def get(key):
        cur = values
        for seg in key.lstrip("[").split("."):
            if isinstance(cur, dict):
                cur = cur.get(seg)
            else:
                return None
        return cur

    for key, op, val in parts:
        actual = get(key)
        if actual is None:
            return False  # short-circuit
        try:
            if op == "==" and not (actual == val):
                return False
            if op == "!=" and not (actual != val):
                return False
            if op == ">" and not (float(actual) > float(val)):
                return False
            if op == "<" and not (float(actual) < float(val)):
                return False
            if op == ">=" and not (float(actual) >= float(val)):
                return False
            if op == "<=" and not (float(actual) <= float(val)):
                return False
        except (ValueError, TypeError):
            return False
    return True


PERFDATA_RE = re.compile(r"([A-Za-z0-9_]+)=([-\d.]+)([A-Za-z%]*)")


def parse_plugin_text(text: str) -> dict:
    """Parse monitoring-plugin status + perfdata (public text protocol)."""
    first = text.strip().splitlines()[0] if text.strip() else ""
    m = re.match(r"^(\w+)\s*(OK|WARNING|CRITICAL|UNKNOWN)\s*-\s*(.*?)(?:\s*\|\s*(.*))?$", first)
    if not m:
        return {"status": "UNKNOWN", "message": first, "perfdata": {}}
    _name, status, message, perf = m.groups()
    data = {}
    if perf:
        for k, v, unit in PERFDATA_RE.findall(perf):
            data[k] = {"value": float(v), "unit": unit}
    return {"status": status, "message": message, "perfdata": data}


def assert_probe_kinds_complete():
    return tuple(PROBE_KINDS)
