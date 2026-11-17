"""1.0.2 / F-01 follow-up: three-state consistency across the read surfaces.

Constitution acceptance A2/A3: `meta` snapshots are restorable=false in the
snapshot list, carry no rollback_point in the ledger, and `restore()` refuses.
The single source of truth is the manifest; list()/audit must not recompute it.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autonomic.audit import AuditLog  # noqa: E402
from autonomic.backup import BackupManager  # noqa: E402
from autonomic.errors import BackupError  # noqa: E402
from autonomic import util  # noqa: E402


class _Cfg:
    def __init__(self, backup_dir):
        self._d = {"backup": {"dir": backup_dir, "enabled": True,
                              "max_file_bytes": 10**7, "max_total_bytes": 10**8}}

    def get(self, key, default=None):
        cur = self._d
        for p in key.split("."):
            if not isinstance(cur, dict) or p not in cur:
                return default
            cur = cur[p]
        return cur


class _Cfg0(_Cfg):
    """Config stand-in that also supplies audit.* keys."""

    def __init__(self, root):
        super().__init__(os.path.join(root, "vault"))
        self._d["audit"] = {"file": os.path.join(root, "audit.log"), "chain": True}


class TestF01StateConsistency(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ao_f02_")
        self.bm = BackupManager(_Cfg(os.path.join(self.tmp, "vault")))
        self.p = os.path.join(self.tmp, "f.log")
        with open(self.p, "wb") as fh:
            fh.write(b"PAYLOAD-XYZ")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_list_reports_kind_and_restorable_for_all_three_states(self):
        full = self.bm.snapshot([self.p], label="full")
        trash = self.bm.quarantine([self.p], label="trash")
        # recreate a payload for meta snapshot
        with open(self.p, "wb") as fh:
            fh.write(b"PAYLOAD-XYZ")
        meta = self.bm.snapshot([self.p], label="meta", metadata_only=True)
        by_id = {s["id"]: s for s in self.bm.list(limit=50)}
        self.assertEqual(by_id[full["id"]]["kind"], "full")
        self.assertTrue(by_id[full["id"]]["restorable"])
        self.assertEqual(by_id[trash["id"]]["kind"], "trash")
        self.assertTrue(by_id[trash["id"]]["restorable"])
        self.assertEqual(by_id[meta["id"]]["kind"], "meta")
        self.assertFalse(by_id[meta["id"]]["restorable"])

    def test_meta_restore_raises_clearly(self):
        meta = self.bm.snapshot([self.p], label="meta", metadata_only=True)
        with self.assertRaises(BackupError) as ctx:
            self.bm.restore(meta["id"])
        self.assertIn("not restorable", str(ctx.exception))

    def test_ledger_meta_record_has_no_rollback_point(self):
        """A meta snapshot must not mint a rollback_point in the ledger."""
        meta = self.bm.snapshot([self.p], label="meta", metadata_only=True)
        audit = AuditLog(_Cfg0(self.tmp))
        rp = meta["id"] if meta.get("restorable") else None
        audit.record(actor="auto", trigger="cleanup", action="disk.cleanup",
                     target=self.p, rollback_point=rp,
                     extra={"kind": meta["kind"], "restorable": meta["restorable"]})
        entry = audit.tail(1)[0]
        self.assertIsNone(entry.get("rollback_point"))
        self.assertEqual(entry["extra"]["kind"], "meta")
        self.assertFalse(entry["extra"]["restorable"])


if __name__ == "__main__":
    unittest.main()
