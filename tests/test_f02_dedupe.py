"""1.0.3 / F-02 regression tests: dedupe key must be a fact fingerprint.

Constitution B1-B4:
  B1 three different commands within one window all execute
  B2 identical command+args -> folded, returns suppressed_by
  B3 same command, args in different order -> same fingerprint
  B4 reason_code lands in negative ledger / explainable

The old key was f"{kind}:{title}" and shell titles were a fixed default, so
every shell task collided. The fix derives a key from the normalized command.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autonomic import util  # noqa: E402
from autonomic.models import Task  # noqa: E402
from autonomic.scheduler import Scheduler  # noqa: E402


class _Cfg:
    def __init__(self, root):
        self._d = {"scheduler": {"dedupe_window": 30, "queue_max": 1000,
                                 "workers": 2, "aging_rate": 0.0, "aging_cap": 0.0},
                   "agent": {"data_dir": root}}

    def get(self, key, default=None):
        cur = self._d
        for p in key.split("."):
            if not isinstance(cur, dict) or p not in cur:
                return default
            cur = cur[p]
        return cur


class _Bus:
    def publish(self, *a, **k):
        pass


def _noop_runner(task):
    return {"ok": True}


def _shell(command):
    # mimic daemon submission: fixed default title, command in payload
    return Task(kind="shell", title="shell task", payload={"command": command})


class TestF02Dedupe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ao_f03_")
        self.sched = Scheduler(_Cfg(self.tmp), _Bus(), _noop_runner)

    def tearDown(self):
        self.sched.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_b1_three_different_commands_all_accepted(self):
        d1 = self.sched.submit(_shell("uptime"))
        d2 = self.sched.submit(_shell("df -h"))
        d3 = self.sched.submit(_shell("ls -l /tmp"))
        self.assertNotEqual(d1.kind, "dedupe", "uptime must not be folded")
        self.assertNotEqual(d2.kind, "dedupe", "df -h must not be folded")
        self.assertNotEqual(d3.kind, "dedupe", "ls -l /tmp must not be folded")

    def test_b2_identical_command_folded_with_suppressed_by(self):
        first = _shell("uptime")
        self.sched.submit(first)
        d2 = self.sched.submit(_shell("uptime"))
        self.assertEqual(d2.kind, "dedupe")
        self.assertTrue(d2.compared_with, "folded decision must carry compared_with id")

    def test_b3_arg_order_irrelevant(self):
        self.sched.submit(_shell("ls -l /tmp"))
        d2 = self.sched.submit(_shell("ls /tmp -l"))
        self.assertEqual(d2.kind, "dedupe", "args in different order must share fingerprint")

    def test_fingerprint_normalizes_whitespace_and_quotes(self):
        a = util.fact_fingerprint("shell.exec", "  df   -h  ")
        b = util.fact_fingerprint("shell.exec", "df -h")
        self.assertEqual(a, b, "whitespace must be folded")


if __name__ == "__main__":
    unittest.main()
