"""Observe tests: facts, 26 probes, assertions short-circuit, plugin text protocol."""
import os
import socket
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autonomic import observe  # noqa: E402


class TestObserve(unittest.TestCase):
    def test_26_probe_kinds(self):
        self.assertEqual(len(observe.PROBE_KINDS), 26)

    def test_collector_fact(self):
        c = observe.Collector("cpu", observe.cpu_fact)
        facts = c.collect()
        self.assertTrue(all(hasattr(f, "metric") for f in facts))

    def test_collector_never_raises(self):
        c = observe.Collector("bad", lambda: (_ for _ in ()).throw(RuntimeError("x")))
        self.assertEqual(c.collect(), [])

    def test_tcp_probe(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def accept_once():
            try:
                conn, _ = srv.accept()
                conn.close()
            except OSError:
                pass

        t = threading.Thread(target=accept_once, daemon=True)
        t.start()
        r = observe.tcp_probe("127.0.0.1", port, timeout=1)
        self.assertTrue(r.ok)
        srv.close()

    def test_assertion_short_circuit(self):
        parts = observe.parse_assertion('[STATUS] == 200 && [BODY].status == "ok" && [RESPONSE_TIME] < 800')
        self.assertEqual(len(parts), 3)
        self.assertTrue(observe.eval_assertion(parts, {"STATUS": 200, "BODY": {"status": "ok"}, "RESPONSE_TIME": 100}))
        self.assertFalse(observe.eval_assertion(parts, {"STATUS": 500, "BODY": {"status": "ok"}, "RESPONSE_TIME": 100}))

    def test_plugin_text_protocol_perfdata(self):
        r = observe.parse_plugin_text("DISK OK - usage is 42% | used=42GB;total=100GB;warn=80%")
        self.assertEqual(r["status"], "OK")
        self.assertEqual(r["perfdata"]["used"]["value"], 42.0)
        self.assertEqual(r["perfdata"]["warn"]["unit"], "%")


if __name__ == "__main__":
    unittest.main()
