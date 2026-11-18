"""Ledger writer daemon (constitution 1.1.6): a separate process that only
APPENDS to the ledger and never serves reads.

Design:
  * The daemon binds a unix socket and accepts append-only JSON lines.
  * Every append is written + fsync'd before the ack, so `kill -9` never loses
    an acknowledged record.
  * The client object exposes `append()` only - there is deliberately no read
    or query method, matching "只写不回读" (write-only, no read-back).
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time


class LedgerClient:
    """Append-only client. No read/query API by design."""

    def __init__(self, sock_path: str, timeout: float = 5.0):
        self.sock_path = sock_path
        self.timeout = timeout

    def append(self, record: dict) -> dict:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        try:
            s.connect(self.sock_path)
            s.sendall((json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
            buf = s.recv(64)
            return json.loads(buf.decode("utf-8")) if buf else {"ok": False, "reason": "no_ack"}
        finally:
            s.close()


def serve(sock_path: str, ledger_path: str, ready_path: str = "") -> None:
    """Run the write-only ledger daemon until killed."""
    parent = os.path.dirname(sock_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.exists(sock_path):
        try:
            os.unlink(sock_path)
        except OSError:
            pass
    os.makedirs(os.path.dirname(ledger_path) or ".", exist_ok=True)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(16)
    if ready_path:
        with open(ready_path, "w") as fh:
            fh.write("ready")
    try:
        while True:
            conn, _ = srv.accept()
            try:
                data = b""
                while not data.endswith(b"\n"):
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                if not data.strip():
                    conn.sendall(b'{"ok":false,"reason":"empty"}')
                    continue
                rec = json.loads(data.decode("utf-8"))
                # append + fsync BEFORE ack (durable across kill -9)
                with open(ledger_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
                conn.sendall(json.dumps({"ok": True, "seq": rec.get("seq")}).encode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                try:
                    conn.sendall(json.dumps({"ok": False, "reason": str(exc)}).encode("utf-8"))
                except OSError:
                    pass
            finally:
                conn.close()
    finally:
        srv.close()
        try:
            os.unlink(sock_path)
        except OSError:
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--socket", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--ready", default="")
    args = ap.parse_args(argv)
    serve(args.socket, args.ledger, args.ready)
    return 0


if __name__ == "__main__":
    sys.exit(main())
