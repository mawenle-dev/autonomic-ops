"""1.0.1 / F-01 regression tests: deletion must be reversible (quarantine).

Constitution acceptance A1-A5 for the credit chain first link.

These tests encode the promised semantics BEFORE implementation, per 5.13:
  * a cleanup-style snapshot uses a `trash` kind and retains bytes via rename;
  * `restorable` is reported and only a truly restorable snapshot yields a
    rollback_point-equivalent registration;
  * restore of a trash snapshot returns byte-exact content.

Run with: PYTHONPATH=src python -m unittest tests.test_f01_quarantine -v
"""
import hashlib
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autonomic.backup import BackupManager  # noqa: E402
from autonomic.errors import ConfigError  # noqa: E402


class _Cfg:
    """Minimal Config stand-in: BackupManager only reads backup.* keys."""

    def __init__(self, backup_dir):
        self._d = {
            "backup": {
                "dir": backup_dir,
                "enabled": True,
                "max_file_bytes": 10 * 1024 * 1024,
                "max_total_bytes": 100 * 1024 * 1024,
            }
        }

    def get(self, key, default=None):
        cur = self._d
        for part in key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class TestF01Quarantine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ao_f01_")
        self.data = os.path.join(self.tmp, "data")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.data)
        self.files = []
        for i in range(5):
            p = os.path.join(self.data, f"f{i}.log")
            with open(p, "wb") as fh:
                fh.write(f"OLD-DATA-{i}-" .encode() + bytes(range(50)))
            self.files.append(p)
        self.before = {p: _sha(p) for p in self.files}
        self.bm = BackupManager(_Cfg(self.vault))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a1_trash_snapshot_restores_byte_exact(self):
        """A1: delete 5 files into quarantine -> restore -> sha256 all equal."""
        snap = self.bm.quarantine(self.files, label="cleanup")
        self.assertEqual(snap.get("kind"), "trash", "cleanup snapshot must be a trash snapshot")
        self.assertTrue(snap.get("restorable"), "trash snapshot must be restorable")
        # originals are gone from their live location
        for p in self.files:
            self.assertFalse(os.path.exists(p), f"{p} should have left its live path")
        # restore
        result = self.bm.restore(snap["id"])
        self.assertTrue(result.get("ok"), f"restore failed: {result}")
        for p in self.files:
            self.assertTrue(os.path.exists(p), f"{p} missing after restore")
            self.assertEqual(_sha(p), self.before[p], f"{p} byte mismatch after restore")

    def test_a2_meta_snapshot_is_not_restorable(self):
        """A2: a metadata-only snapshot reports restorable=false."""
        snap = self.bm.snapshot(self.files[:1], label="evidence", metadata_only=True)
        self.assertEqual(snap.get("kind"), "meta")
        self.assertFalse(snap.get("restorable"), "meta snapshot must not claim restorable")

    def test_a3_meta_snapshot_yields_no_rollback_point(self):
        """A3: meta snapshot must not register as a rollback_point."""
        snap = self.bm.snapshot(self.files[:1], label="evidence", metadata_only=True)
        self.assertNotIn("rollback_point", snap, "meta snapshot must not mint a rollback_point")
        self.assertFalse(snap.get("restorable"))

    def test_a4_full_snapshot_is_restorable(self):
        """A full (payload-copied) snapshot remains restorable=true."""
        snap = self.bm.snapshot(self.files[:2], label="guard")
        self.assertEqual(snap.get("kind"), "full")
        self.assertTrue(snap.get("restorable"))


if __name__ == "__main__":
    unittest.main()
