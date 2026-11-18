"""Ledger writer daemon tests (1.1.6): write-only, durable across kill -9."""
import multiprocessing as mp
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autonomic import fvc, ledger_daemon  # noqa: E402


def _supported() -> bool:
    return hasattr(__import__("socket"), "AF_UNIX")


class TestLedgerDaemon(unittest.TestCase):
    @unittest.skipUnless(_supported(), "AF_UNIX not supported on this platform")
    def test_append_only_and_durable_after_kill(self):
        d = tempfile.mkdtemp(prefix="ao_ld_")
        sock = os.path.join(d, "ledger.sock")
        led = os.path.join(d, "ledger.jsonl")
        ready = os.path.join(d, "ready")
        try:
            p = mp.Process(target=ledger_daemon.serve, args=(sock, led, ready), daemon=True)
            p.start()
            for _ in range(100):
                if os.path.exists(ready):
                    break
                time.sleep(0.05)
            self.assertTrue(os.path.exists(ready), "daemon did not become ready")

            client = ledger_daemon.LedgerClient(sock)
            for i in range(3):
                r = client.append({"seq": i, "hash": fvc.sha({"i": i})})
                self.assertTrue(r["ok"], r)

            # write-only: the client must expose no read/query API
            self.assertFalse(hasattr(client, "read"))
            self.assertFalse(hasattr(client, "query"))

            # simulate kill -9
            p.kill(); p.join(5)

            with open(led, encoding="utf-8") as fh:
                rows = [__import__("json").loads(l) for l in fh if l.strip()]
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(r["hash"] == fvc.sha({"i": r["seq"]}) for r in rows))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_no_read_method(self):
        c = ledger_daemon.LedgerClient("/nonexistent")
        self.assertTrue(callable(c.append))
        self.assertFalse(hasattr(c, "read"))


if __name__ == "__main__":
    unittest.main()
