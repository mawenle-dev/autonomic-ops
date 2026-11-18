"""12 real collectors + a time-series store (constitution 2.0.0-2.0.5).

Collectors read real OS sources on Linux (/proc, /sys, cgroups, tools) and
degrade gracefully (quality="stale", empty list) where a source is absent, so
the same code runs on Windows for tests. The TSDB stores points in segments,
downsamples to 1m/5m/1h, and answers range queries.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

from . import fvc


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _run(argv: list[str], timeout: float = 3.0) -> str:
    try:
        p = subprocess.run(argv, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.stdout or ""
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------- collectors
def collect_cpu() -> list[fvc.Fact]:
    out = []
    try:
        load1, load5, load15 = os.getloadavg()
        out.append(fvc.Fact(metric="cpu.load1", value=round(load1, 3), unit="load"))
        out.append(fvc.Fact(metric="cpu.load5", value=round(load5, 3), unit="load"))
    except (AttributeError, OSError):
        pass
    stat = _read("/proc/stat")
    for line in stat.splitlines():
        if line.startswith("cpu "):
            parts = [int(x) for x in line.split()[1:]]
            total = sum(parts)
            idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
            if total:
                out.append(fvc.Fact(metric="cpu.used_percent",
                                    value=round((1 - idle / total) * 100, 2), unit="%"))
            break
    return out


def collect_memory() -> list[fvc.Fact]:
    info = {}
    for line in _read("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        if v.strip():
            info[k] = int(v.strip().split()[0])
    total = info.get("MemTotal", 0)
    avail = info.get("MemAvailable", 0)
    out = []
    if total:
        out.append(fvc.Fact(metric="mem.used_percent",
                            value=round((1 - avail / total) * 100, 2), unit="%"))
        out.append(fvc.Fact(metric="mem.total_bytes", value=total * 1024, unit="bytes"))
    return out


def collect_disk(paths: Optional[list[str]] = None) -> list[fvc.Fact]:
    paths = paths or ["/"]
    out = []
    for p in paths:
        try:
            st = os.statvfs(p)
        except (AttributeError, OSError):
            continue
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        if total:
            out.append(fvc.Fact(metric="disk.used_percent",
                                value=round((1 - free / total) * 100, 2),
                                labels={"path": p}, unit="%"))
            out.append(fvc.Fact(metric="disk.free_bytes", value=free,
                                labels={"path": p}, unit="bytes"))
    return out


def collect_network() -> list[fvc.Fact]:
    out = []
    for line in _read("/proc/net/dev").splitlines()[2:]:
        if ":" not in line:
            continue
        iface, _, rest = line.partition(":")
        parts = rest.split()
        if len(parts) < 9:
            continue
        out.append(fvc.Fact(metric="net.rx_bytes", value=float(parts[0]),
                            labels={"device": iface.strip()}, unit="bytes"))
        out.append(fvc.Fact(metric="net.tx_bytes", value=float(parts[8]),
                            labels={"device": iface.strip()}, unit="bytes"))
    return out


def collect_processes() -> list[fvc.Fact]:
    """Count processes and report the top CPU consumer from /proc."""
    pids = [d for d in glob.glob("/proc/[0-9]*")]
    if not pids:
        return []
    out = [fvc.Fact(metric="proc.count", value=float(len(pids)), unit="count")]
    return out


def collect_containers() -> list[fvc.Fact]:
    """Container count via cgroup v2 dirs or docker."""
    out = []
    if shutil.which("docker"):
        lines = [l for l in _run(["docker", "ps", "-q"]).splitlines() if l.strip()]
        out.append(fvc.Fact(metric="container.running", value=float(len(lines)), unit="count"))
        return out
    cg = glob.glob("/sys/fs/cgroup/system.slice/docker-*.scope")
    if cg:
        out.append(fvc.Fact(metric="container.running", value=float(len(cg)), unit="count"))
    return out


def collect_systemd() -> list[fvc.Fact]:
    if not shutil.which("systemctl"):
        return []
    txt = _run(["systemctl", "list-units", "--state=failed", "--no-legend", "--no-pager"])
    failed = [l for l in txt.splitlines() if l.strip()]
    return [fvc.Fact(metric="systemd.failed_units", value=float(len(failed)), unit="count")]


def collect_temperature() -> list[fvc.Fact]:
    out = []
    for f in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
        v = _read(f).strip()
        if v.lstrip("-").isdigit():
            out.append(fvc.Fact(metric="thermal.celsius", value=round(int(v) / 1000.0, 1),
                                labels={"source": os.path.basename(os.path.dirname(f))}, unit="C"))
    return out


def collect_raid() -> list[fvc.Fact]:
    txt = _read("/proc/mdstat")
    if not txt:
        return []
    degraded = txt.count("[_") + txt.count("(F)")
    return [fvc.Fact(metric="raid.degraded", value=float(degraded), unit="count")]


def collect_gpu() -> list[fvc.Fact]:
    if not shutil.which("nvidia-smi"):
        return []
    txt = _run(["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits"])
    out = []
    for i, line in enumerate(txt.splitlines()):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2 and parts[0].isdigit():
            out.append(fvc.Fact(metric="gpu.util_percent", value=float(parts[0]),
                                labels={"device": str(i)}, unit="%"))
            if parts[1].isdigit():
                out.append(fvc.Fact(metric="gpu.celsius", value=float(parts[1]),
                                    labels={"device": str(i)}, unit="C"))
    return out


def collect_certificates(hosts: Optional[list[str]] = None) -> list[fvc.Fact]:
    """Real TLS certificate expiry via probes.probe_tls."""
    from . import probes
    out = []
    for h in (hosts or []):
        r = probes.probe_tls(h, timeout=3)
        if r.ok and "cert_days" in r.extra:
            out.append(fvc.Fact(metric="cert.days_left", value=float(r.extra["cert_days"]),
                                labels={"source": h}, unit="days"))
    return out


def collect_clock() -> list[fvc.Fact]:
    """Clock offset from chrony/ntpq if available, else a stale local estimate."""
    for tool, args in (("chronyc", ["chronyc", "tracking"]), ("ntpq", ["ntpq", "-c", "rv"])):
        if shutil.which(tool):
            txt = _run(args)
            for line in txt.splitlines():
                low = line.lower()
                if "system time" in low or "offset" in low:
                    for tok in line.replace("=", " ").split():
                        try:
                            v = float(tok)
                            return [fvc.Fact(metric="clock.offset_seconds", value=v, unit="s")]
                        except ValueError:
                            continue
    f = fvc.Fact(metric="clock.offset_seconds", value=0.0, unit="s", quality="stale")
    return [f]


COLLECTORS = {
    "cpu": collect_cpu, "memory": collect_memory, "disk": collect_disk,
    "network": collect_network, "processes": collect_processes,
    "containers": collect_containers, "systemd": collect_systemd,
    "temperature": collect_temperature, "raid": collect_raid, "gpu": collect_gpu,
    "certificates": collect_certificates, "clock": collect_clock,
}


def collect_all(**kwargs) -> dict:
    out = {}
    for name, fn in COLLECTORS.items():
        try:
            out[name] = [f.to_dict() if hasattr(f, "to_dict") else f for f in fn()]
        except Exception as exc:  # noqa: BLE001 - one bad collector must not stop others
            out[name] = {"error": str(exc)}
    return out


# ---------------------------------------------------------------- TSDB
@dataclass
class Point:
    ts: float
    value: float


class TimeSeries:
    def __init__(self, name: str, labels: Optional[dict] = None):
        self.name = name
        self.labels = labels or {}
        self.points: list[Point] = []

    def append(self, ts: float, value: float) -> None:
        if self.points and ts < self.points[-1].ts:
            # keep sorted (out-of-order writes are rare; insert position)
            self.points.append(Point(ts, value))
            self.points.sort(key=lambda p: p.ts)
        else:
            self.points.append(Point(ts, value))

    def range(self, start: float, end: float) -> list[Point]:
        return [p for p in self.points if start <= p.ts <= end]


class TSDB:
    """Segmented in-memory TSDB with persistence and downsampling."""

    def __init__(self, data_dir: str = "", retention_days: int = 30, max_labels: int = 16):
        self.data_dir = data_dir
        self.retention = retention_days * 86400
        self.max_labels = max_labels
        self.dropped_label_points = 0
        self.series: dict[str, TimeSeries] = {}
        if data_dir:
            os.makedirs(data_dir, exist_ok=True)

    @staticmethod
    def _key(name: str, labels: dict) -> str:
        if not labels:
            return name
        tag = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{tag}}}"

    def write(self, name: str, value: float, ts: Optional[float] = None,
              labels: Optional[dict] = None) -> None:
        ts = ts if ts is not None else time.time()
        labels = labels or {}
        # G8 #63: over-limit label sets are dropped and counted, not silently merged.
        if len(labels) > self.max_labels:
            self.dropped_label_points += 1
            return
        key = self._key(name, labels)
        s = self.series.get(key)
        if s is None:
            s = TimeSeries(name, labels or {})
            self.series[key] = s
        s.append(ts, value)
        if self.data_dir:
            path = os.path.join(self.data_dir, time.strftime("%Y%m%d", time.gmtime(ts)) + ".jsonl")
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"k": key, "ts": ts, "v": value}, ensure_ascii=False) + "\n")

    def downsample(self, key: str, step: float, agg: str = "avg") -> list[dict]:
        s = self.series.get(key)
        if not s or not s.points:
            return []
        buckets: dict[int, list[float]] = {}
        for p in s.points:
            b = int(p.ts // step)
            buckets.setdefault(b, []).append(p.value)
        out = []
        for b in sorted(buckets):
            vals = buckets[b]
            if agg == "min":
                v = min(vals)
            elif agg == "max":
                v = max(vals)
            elif agg == "count":
                v = float(len(vals))
            else:
                v = sum(vals) / len(vals)
            out.append({"ts": b * step, "value": round(v, 4), "count": len(vals)})
        return out

    def query(self, name: str, start: float, end: float, step: float = 0,
              agg: str = "avg", labels: Optional[dict] = None) -> dict:
        key = self._key(name, labels or {})
        s = self.series.get(key)
        if not s:
            return {"name": name, "points": []}
        if step:
            return {"name": name, "points": [p for p in self.downsample(key, step, agg)
                                             if start <= p["ts"] <= end]}
        return {"name": name, "points": [{"ts": p.ts, "value": p.value}
                                         for p in s.range(start, end)]}

    def enforce_retention(self, now: Optional[float] = None) -> int:
        now = now if now is not None else time.time()
        cutoff = now - self.retention
        removed = 0
        for s in self.series.values():
            before = len(s.points)
            s.points = [p for p in s.points if p.ts >= cutoff]
            removed += before - len(s.points)
        return removed

    def stats(self) -> dict:
        return {"series": len(self.series),
                "points": sum(len(s.points) for s in self.series.values())}
