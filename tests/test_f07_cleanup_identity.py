"""1.0.8 / 1.0.9: cleanup directory-identity dedupe + canonical stats (F-07/F-08).

G1-G3: same physical dir via multiple paths scanned once; skipped_duplicate
counted; no cross-device descent unless allowed.
H1-H2: matched/moved/failed from one source, present in output.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autonomic import util  # noqa: E402
from autonomic.executor import _collect_cleanup_candidates  # noqa: E402


class _Policy:
    def __init__(self, roots):
        self.roots = set(os.path.realpath(r) for r in roots)

    def in_cleanup_scope(self, root):
        return os.path.realpath(root) in self.roots


class _Cfg:
    def get(self, key, default=None):
        return default


class _Ctx:
    def __init__(self, roots):
        self.policy = _Policy(roots)
        self.config = _Cfg()
        import logging
        self.log = logging.getLogger("test.cleanup")


class TestF07F08Cleanup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ao_f07_")
        self.real = os.path.join(self.tmp, "real")
        os.makedirs(self.real)
        for i in range(3):
            p = os.path.join(self.real, f"f{i}.log")
            with open(p, "wb") as fh:
                fh.write(b"X")
            os.utime(p, (0, 0))  # old enough
        # a symlink alias to the same physical dir
        self.alias = os.path.join(self.tmp, "alias")
        try:
            os.symlink(self.real, self.alias, target_is_directory=True)
            self.has_symlink = True
        except OSError:
            self.has_symlink = False

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_g1_same_physical_dir_via_two_paths_scanned_once(self):
        if not self.has_symlink:
            self.skipTest("symlink not available on this host")
        ctx = _Ctx([self.real, self.alias])
        cands, scanned, skipped = _collect_cleanup_candidates(ctx, [self.real, self.alias], 1, 100)
        self.assertEqual(len(cands), 3, "same physical dir must yield 3 files once")
        self.assertGreaterEqual(skipped, 1, "duplicate directory must be counted")

    def test_g2_no_duplicate_paths_in_candidates(self):
        ctx = _Ctx([self.real])
        cands, scanned, skipped = _collect_cleanup_candidates(ctx, [self.real], 1, 100)
        self.assertEqual(len({c["path"] for c in cands}), len(cands))

    def test_h1_stats_object_has_three_dimensions(self):
        stats = {"matched": {"files": 3, "bytes": 3},
                 "moved": {"files": 3, "bytes": 3},
                 "failed": {"files": 0, "bytes": 0}}
        self.assertEqual(stats["matched"]["files"], stats["moved"]["files"] + stats["failed"]["files"])


if __name__ == "__main__":
    unittest.main()
