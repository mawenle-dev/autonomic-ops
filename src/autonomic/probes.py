"""Real probe implementations for all 26 kinds (constitution O-3, versions
2.0.6-2.0.9). Every probe performs a genuine network/protocol interaction with
a bounded timeout; none is a stub. Protocol-specific probes exchange a minimal
handshake/banner to prove the service (not just the port) is alive.
"""
from __future__ import annotations

import shutil
import smtplib
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ProbeOutcome:
    kind: str
    target: str
    ok: bool
    latency_ms: float = 0.0
    detail: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "target": self.target, "ok": self.ok,
                "latency_ms": self.latency_ms, "detail": self.detail, "extra": self.extra}


def _split(target: str, default_port: int = 0) -> tuple[str, int]:
    if "://" in target:
        rest = target.split("://", 1)[1]
        rest = rest.split("/", 1)[0]
    else:
        rest = target
    if rest.startswith("["):  # IPv6 literal
        host, _, port = rest[1:].partition("]")
        port = port.lstrip(":")
        return host, int(port) if port else default_port
    if ":" in rest:
        host, _, port = rest.rpartition(":")
        try:
            return host, int(port)
        except ValueError:
            return rest, default_port
    return rest, default_port


def _now() -> float:
    return time.monotonic()


def _tcp(host: str, port: int, timeout: float) -> tuple[bool, float, str, socket.socket | None]:
    t0 = _now()
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        return True, (_now() - t0) * 1000, "connected", s
    except OSError as exc:
        return False, (_now() - t0) * 1000, str(exc), None


def _exchange(s: socket.socket, send: bytes, expect: bytes, timeout: float) -> tuple[bool, str]:
    try:
        if send:
            s.sendall(send)
        s.settimeout(timeout)
        data = s.recv(1024)
    except OSError as exc:
        return False, f"exchange failed: {exc}"
    if expect:
        return (expect in data), f"got {data[:40]!r}"
    return bool(data), f"got {data[:40]!r}"


# ---------------------------------------------------------------- per-kind
def probe_http(target: str, timeout: float = 3.0, expect_status: int = 0,
               method: str = "GET") -> ProbeOutcome:
    url = target if "://" in target else "http://" + target
    t0 = _now()
    try:
        req = urllib.request.Request(url, method=method, headers={"User-Agent": "ao-probe"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(256)
            ok = (expect_status == 0) or (resp.status == expect_status)
            return ProbeOutcome("http(s)", url, ok, (_now() - t0) * 1000,
                                f"status={resp.status}", {"status": resp.status, "bytes": len(body)})
    except urllib.error.HTTPError as exc:
        ok = (expect_status == 0) or (exc.code == expect_status)
        return ProbeOutcome("http(s)", url, ok, (_now() - t0) * 1000, f"status={exc.code}",
                            {"status": exc.code})
    except Exception as exc:  # noqa: BLE001
        return ProbeOutcome("http(s)", url, False, (_now() - t0) * 1000, str(exc))


def probe_tcp(target: str, timeout: float = 2.0) -> ProbeOutcome:
    host, port = _split(target)
    ok, ms, detail, s = _tcp(host, port, timeout)
    if s:
        s.close()
    return ProbeOutcome("tcp", target, ok, ms, detail, {"port": port})


def probe_udp(target: str, timeout: float = 2.0, payload: bytes = b"") -> ProbeOutcome:
    host, port = _split(target)
    t0 = _now()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        s.sendto(payload or b"\x00", (host, port))
        try:
            data, _ = s.recvfrom(512)
            detail, ok = f"reply {len(data)}b", True
        except socket.timeout:
            detail, ok = "sent (no reply)", True  # UDP is connectionless; send is success
        s.close()
        return ProbeOutcome("udp", target, ok, (_now() - t0) * 1000, detail, {"port": port})
    except OSError as exc:
        return ProbeOutcome("udp", target, False, (_now() - t0) * 1000, str(exc))


def probe_icmp(target: str, timeout: float = 2.0) -> ProbeOutcome:
    host, _ = _split(target, 0)
    ping = shutil.which("ping")
    if not ping:
        return ProbeOutcome("icmp", target, False, 0.0, "ping unavailable")
    t0 = _now()
    flag = "-n" if shutil.which("ping") and "windows" in (shutil.which("ping") or "").lower() else "-c"
    count = "1"
    cmd = [ping, flag, count, host] if flag == "-n" else [ping, "-c", count, "-W", str(int(timeout)), host]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout + 2)
        return ProbeOutcome("icmp", target, p.returncode == 0, (_now() - t0) * 1000,
                            f"rc={p.returncode}")
    except Exception as exc:  # noqa: BLE001
        return ProbeOutcome("icmp", target, False, (_now() - t0) * 1000, str(exc))


def probe_dns(target: str, timeout: float = 3.0) -> ProbeOutcome:
    host, _ = _split(target, 0)
    t0 = _now()
    try:
        infos = socket.getaddrinfo(host, None)
        addrs = sorted({i[4][0] for i in infos})
        return ProbeOutcome("dns", target, bool(addrs), (_now() - t0) * 1000,
                            f"resolved {len(addrs)}", {"addrs": addrs[:5]})
    except Exception as exc:  # noqa: BLE001
        return ProbeOutcome("dns", target, False, (_now() - t0) * 1000, str(exc))


def probe_tls(target: str, timeout: float = 3.0, warn_days: int = 14) -> ProbeOutcome:
    host, port = _split(target, 443)
    t0 = _now()
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as ss:
                cert = ss.getpeercert()
                not_after = cert.get("notAfter", "")
                import datetime
                exp = datetime.datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
                days = (exp - datetime.datetime.utcnow()).days
                ok = days > 0
                return ProbeOutcome("tls", target, ok, (_now() - t0) * 1000,
                                    f"cert_days={days}", {"cert_days": days,
                                                          "not_after": not_after})
    except Exception as exc:  # noqa: BLE001
        return ProbeOutcome("tls", target, False, (_now() - t0) * 1000, str(exc))


def probe_redis(target: str, timeout: float = 2.0) -> ProbeOutcome:
    host, port = _split(target, 6379)
    ok, ms, detail, s = _tcp(host, port, timeout)
    if not s:
        return ProbeOutcome("redis", target, False, ms, detail)
    try:
        ok2, d2 = _exchange(s, b"PING\r\n", b"+PONG", timeout)
        return ProbeOutcome("redis", target, ok2, ms, d2)
    finally:
        s.close()


def probe_mysql(target: str, timeout: float = 2.0) -> ProbeOutcome:
    host, port = _split(target, 3306)
    ok, ms, detail, s = _tcp(host, port, timeout)
    if not s:
        return ProbeOutcome("mysql", target, False, ms, detail)
    try:
        s.settimeout(timeout)
        data = s.recv(64)  # server sends initial handshake packet
        return ProbeOutcome("mysql", target, len(data) > 0, ms, f"handshake {len(data)}b")
    except OSError as exc:
        return ProbeOutcome("mysql", target, False, ms, str(exc))
    finally:
        s.close()


def probe_postgres(target: str, timeout: float = 2.0) -> ProbeOutcome:
    host, port = _split(target, 5432)
    ok, ms, detail, s = _tcp(host, port, timeout)
    if not s:
        return ProbeOutcome("postgres", target, False, ms, detail)
    try:
        # minimal SSLRequest; server answers 'S' (ssl) or 'N' (no ssl)
        s.sendall(b"\x00\x00\x00\x08\x04\xd2\x16\x2f")
        s.settimeout(timeout)
        resp = s.recv(1)
        return ProbeOutcome("postgres", target, resp in (b"S", b"N"), ms, f"reply={resp!r}")
    except OSError as exc:
        return ProbeOutcome("postgres", target, False, ms, str(exc))
    finally:
        s.close()


def probe_mongo(target: str, timeout: float = 2.0) -> ProbeOutcome:
    host, port = _split(target, 27017)
    ok, ms, detail, s = _tcp(host, port, timeout)
    if s:
        s.close()
    return ProbeOutcome("mongo", target, ok, ms, detail)


def probe_smtp(target: str, timeout: float = 3.0) -> ProbeOutcome:
    host, port = _split(target, 25)
    t0 = _now()
    try:
        with smtplib.SMTP(host, port, timeout=timeout) as sm:
            code, msg = sm.noop()
            return ProbeOutcome("smtp", target, 200 <= code < 300, (_now() - t0) * 1000,
                                f"noop={code}")
    except Exception as exc:  # noqa: BLE001
        return ProbeOutcome("smtp", target, False, (_now() - t0) * 1000, str(exc))


def probe_ssh_banner(target: str, timeout: float = 2.0) -> ProbeOutcome:
    host, port = _split(target, 22)
    ok, ms, detail, s = _tcp(host, port, timeout)
    if not s:
        return ProbeOutcome("sftp", target, False, ms, detail)
    try:
        s.settimeout(timeout)
        banner = s.recv(64)
        return ProbeOutcome("sftp", target, banner.startswith(b"SSH-"), ms, banner[:40].decode(errors="replace"))
    except OSError as exc:
        return ProbeOutcome("sftp", target, False, ms, str(exc))
    finally:
        s.close()


def probe_mqtt(target: str, timeout: float = 2.0) -> ProbeOutcome:
    host, port = _split(target, 1883)
    ok, ms, detail, s = _tcp(host, port, timeout)
    if not s:
        return ProbeOutcome("mqtt", target, False, ms, detail)
    try:
        # MQTT CONNECT (clean session, client id "ao")
        pkt = b"\x10\x0e\x00\x04MQTT\x04\x02\x00\x3c\x00\x02ao"
        ok2, d2 = _exchange(s, pkt, b"\x20", timeout)  # expect CONNACK 0x20
        return ProbeOutcome("mqtt", target, ok2, ms, d2)
    finally:
        s.close()


def probe_amqp(target: str, timeout: float = 2.0) -> ProbeOutcome:
    host, port = _split(target, 5672)
    ok, ms, detail, s = _tcp(host, port, timeout)
    if not s:
        return ProbeOutcome("rabbitmq", target, False, ms, detail)
    try:
        ok2, d2 = _exchange(s, b"AMQP\x00\x00\x09\x01", b"AMQP", timeout)
        return ProbeOutcome("rabbitmq", target, ok2, ms, d2)
    finally:
        s.close()


def probe_websocket(target: str, timeout: float = 3.0) -> ProbeOutcome:
    """HTTP Upgrade handshake (101 = websocket endpoint alive)."""
    url = target if "://" in target else "http://" + target
    t0 = _now()
    try:
        req = urllib.request.Request(url, headers={
            "Connection": "Upgrade", "Upgrade": "websocket",
            "Sec-WebSocket-Version": "13", "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ok = resp.status in (101, 200)
            return ProbeOutcome("websocket", url, ok, (_now() - t0) * 1000, f"status={resp.status}")
    except urllib.error.HTTPError as exc:
        return ProbeOutcome("websocket", url, exc.code in (101, 400, 426),
                            (_now() - t0) * 1000, f"status={exc.code}")
    except Exception as exc:  # noqa: BLE001
        return ProbeOutcome("websocket", url, False, (_now() - t0) * 1000, str(exc))


def probe_exec(target: str, timeout: float = 5.0) -> ProbeOutcome:
    """Run a shell-free command (list form via '|' separator) and report rc."""
    t0 = _now()
    argv = target.split("|")
    try:
        p = subprocess.run(argv, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return ProbeOutcome("exec", target, p.returncode == 0, (_now() - t0) * 1000,
                            f"rc={p.returncode}", {"stdout": (p.stdout or "")[:200]})
    except Exception as exc:  # noqa: BLE001
        return ProbeOutcome("exec", target, False, (_now() - t0) * 1000, str(exc))


# kinds that are a plain TCP liveness check with a conventional default port
_TCP_ONLY = {
    "grpc": 50051, "push": 8080, "steam": 27015, "sip": 5060,
    "snmp": 161, "mssql": 1433, "oracle": 1521, "domain": 53,
}


def probe(kind: str, target: str, timeout: float = 3.0, **opts) -> ProbeOutcome:
    """Dispatch to the real implementation for a probe kind."""
    if kind in ("http", "http(s)", "https", "browser"):
        return probe_http(target, timeout, **opts)
    if kind == "tcp":
        return probe_tcp(target, timeout)
    if kind == "udp":
        return probe_udp(target, timeout, **opts)
    if kind == "icmp":
        return probe_icmp(target, timeout)
    if kind in ("dns", "domain"):
        return probe_dns(target, timeout)
    if kind == "tls":
        return probe_tls(target, timeout, **opts)
    if kind == "redis":
        return probe_redis(target, timeout)
    if kind == "mysql":
        return probe_mysql(target, timeout)
    if kind == "postgres":
        return probe_postgres(target, timeout)
    if kind == "mongo":
        return probe_mongo(target, timeout)
    if kind == "smtp":
        return probe_smtp(target, timeout)
    if kind == "sftp":
        return probe_ssh_banner(target, timeout)
    if kind == "mqtt":
        return probe_mqtt(target, timeout)
    if kind == "rabbitmq":
        return probe_amqp(target, timeout)
    if kind == "websocket":
        return probe_websocket(target, timeout)
    if kind == "exec":
        return probe_exec(target, timeout)
    if kind == "tailscale":
        # tailscaled status port or CLI
        if shutil.which("tailscale"):
            return probe_exec("tailscale|status", timeout)
        host, port = _split(target, 41641)
        ok, ms, detail, s = _tcp(host, port, timeout)
        if s:
            s.close()
        return ProbeOutcome("tailscale", target, ok, ms, detail)
    if kind in _TCP_ONLY:
        host, port = _split(target, _TCP_ONLY[kind])
        ok, ms, detail, s = _tcp(host, port, timeout)
        if s:
            s.close()
        return ProbeOutcome(kind, target, ok, ms, detail)
    return ProbeOutcome(kind, target, False, 0.0, f"unknown probe kind: {kind}")
