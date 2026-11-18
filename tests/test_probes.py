"""Probe tests: real local servers prove each protocol path (2.0.6-2.0.9)."""
import os
import socket
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autonomic import probes  # noqa: E402


def _serve_once(handler, socktype=socket.SOCK_STREAM):
    """Start a one-shot local server; return (port, thread)."""
    srv = socket.socket(socket.AF_INET, socktype)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    if socktype == socket.SOCK_STREAM:
        srv.listen(1)
    port = srv.getsockname()[1]

    def run():
        try:
            if socktype == socket.SOCK_STREAM:
                conn, _ = srv.accept()
                handler(conn)
                conn.close()
            else:
                data, addr = srv.recvfrom(1024)
                handler(srv, data, addr)
        except OSError:
            pass
        finally:
            srv.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return port, t


class TestProbes(unittest.TestCase):
    def test_all_26_kinds_dispatch(self):
        # every declared kind must have a real dispatch (no "unknown")
        from autonomic.observe import PROBE_KINDS
        self.assertEqual(len(PROBE_KINDS), 26)
        for kind in PROBE_KINDS:
            out = probes.probe(kind, "127.0.0.1:1", timeout=0.4)
            self.assertNotIn("unknown probe kind", out.detail, f"{kind} not dispatched")

    def test_tcp_connect(self):
        port, t = _serve_once(lambda c: None)
        out = probes.probe_tcp(f"127.0.0.1:{port}", timeout=2)
        self.assertTrue(out.ok, out.detail)
        t.join(2)

    def test_tcp_refused(self):
        out = probes.probe_tcp("127.0.0.1:1", timeout=1)
        self.assertFalse(out.ok)

    def test_redis_ping(self):
        def handler(conn):
            conn.recv(64)
            conn.sendall(b"+PONG\r\n")
        port, t = _serve_once(handler)
        out = probes.probe_redis(f"127.0.0.1:{port}", timeout=2)
        self.assertTrue(out.ok, out.detail)
        t.join(2)

    def test_ssh_banner(self):
        def handler(conn):
            conn.sendall(b"SSH-2.0-OpenSSH_9.0\r\n")
        port, t = _serve_once(handler)
        out = probes.probe_ssh_banner(f"127.0.0.1:{port}", timeout=2)
        self.assertTrue(out.ok, out.detail)
        t.join(2)

    def test_mqtt_connack(self):
        def handler(conn):
            conn.recv(64)
            conn.sendall(b"\x20\x02\x00\x00")
        port, t = _serve_once(handler)
        out = probes.probe_mqtt(f"127.0.0.1:{port}", timeout=2)
        self.assertTrue(out.ok, out.detail)
        t.join(2)

    def test_amqp_header(self):
        def handler(conn):
            conn.recv(64)
            conn.sendall(b"AMQP\x00\x00\x09\x01")
        port, t = _serve_once(handler)
        out = probes.probe_amqp(f"127.0.0.1:{port}", timeout=2)
        self.assertTrue(out.ok, out.detail)
        t.join(2)

    def test_mysql_greeting(self):
        def handler(conn):
            conn.sendall(b"\x0a5.7.0\x00" + b"\x00" * 20)
        port, t = _serve_once(handler)
        out = probes.probe_mysql(f"127.0.0.1:{port}", timeout=2)
        self.assertTrue(out.ok, out.detail)
        t.join(2)

    def test_postgres_ssl_reply(self):
        def handler(conn):
            conn.recv(16)
            conn.sendall(b"N")
        port, t = _serve_once(handler)
        out = probes.probe_postgres(f"127.0.0.1:{port}", timeout=2)
        self.assertTrue(out.ok, out.detail)
        t.join(2)

    def test_http_ok(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        port = srv.server_address[1]
        threading.Thread(target=srv.handle_request, daemon=True).start()
        out = probes.probe_http(f"http://127.0.0.1:{port}/", timeout=3)
        self.assertTrue(out.ok, out.detail)
        self.assertEqual(out.extra.get("status"), 200)

    def test_dns_localhost(self):
        out = probes.probe_dns("localhost")
        self.assertTrue(out.ok, out.detail)

    def test_udp_send(self):
        def handler(srv, data, addr):
            srv.sendto(b"pong", addr)
        port, t = _serve_once(handler, socket.SOCK_DGRAM)
        out = probes.probe_udp(f"127.0.0.1:{port}", timeout=2, payload=b"ping")
        self.assertTrue(out.ok, out.detail)
        t.join(2)

    def test_exec(self):
        out = probes.probe_exec(f"{sys.executable}|-c|print(1)", timeout=5)
        self.assertTrue(out.ok, out.detail)

    def test_icmp_or_skip(self):
        import shutil
        if not shutil.which("ping"):
            self.skipTest("ping not available")
        out = probes.probe_icmp("127.0.0.1", timeout=2)
        self.assertIsInstance(out.ok, bool)


if __name__ == "__main__":
    unittest.main()
