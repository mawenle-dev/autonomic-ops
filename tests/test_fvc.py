"""FVC-R core tests: half-life, confidence, budget ledger, receipt schema (ch.4/7)."""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autonomic import fvc  # noqa: E402


class TestFVC(unittest.TestCase):
    def test_fact_label_whitelist(self):
        f = fvc.Fact(metric="cpu", value=0.5, labels={"host": "h1", "secret": "x"})
        self.assertIn("host", f.labels)
        self.assertNotIn("secret", f.labels)

    def test_confidence_formula(self):
        self.assertEqual(fvc.confidence_for(1), 0.6)
        self.assertEqual(fvc.confidence_for(2), 0.85)
        self.assertEqual(fvc.confidence_for(3), 0.95)
        self.assertLess(fvc.confidence_for(2, fresh=False), fvc.confidence_for(2, fresh=True))

    def test_verdict_requires_chain(self):
        with self.assertRaises(ValueError):
            fvc.Verdict(kind="disk", subject="/", severity="high", chain=[])

    def test_half_life_decays_and_retires(self):
        v = fvc.Verdict(kind="process", subject="svc", severity="high",
                        chain=[{"step": "x"}], confidence=0.6)
        now = v.born_at
        v0 = v.visible(now)
        v1 = v.visible(now + 60)
        self.assertGreater(v0, v1, "visibility must decay over time")
        self.assertEqual(v.visible(v.retire_at + 1), 0.0)
        v.refresh(now + 100)
        self.assertGreater(v.retire_at, now + 100, "confirming fact extends life")

    def test_budget_atomic_debit(self):
        acct = fvc.BudgetAccount(fvc.Budget(bytes=100, files=2, actions=10, blast=1, rate=5))
        ok, why = acct.debit({"bytes": 60, "files": 1})
        self.assertTrue(ok)
        ok, why = acct.debit({"bytes": 60, "files": 1})
        self.assertFalse(ok)
        self.assertIn("budget_exhausted", why)

    def test_covenant_requires_five_dims(self):
        b = fvc.Budget(bytes=1, files=1, actions=1, blast=1, rate=1)
        c = fvc.Covenant(scope="cleanup", budget=b, expiry=time.time() + 60)
        self.assertTrue(c.valid())
        self.assertEqual(len(c.budget.to_dict()), 5)

    def test_receipt_requires_all_elements(self):
        with self.assertRaises(ValueError):
            fvc.Receipt(trace_id="t", fact_refs=[], verdict={}, covenant={}, txn={}, undo={})

    def test_receipt_hash_verifies(self):
        r = fvc.Receipt(trace_id="t", fact_refs=["f1"], verdict={"id": "v1", "chain_hash": "x"},
                        covenant={"before": "a", "after": "b"}, txn={"id": "tx1"},
                        undo={"kind": "trash", "ref": "s1"})
        self.assertTrue(r.verify())
        r.txn = {"id": "tampered"}
        self.assertFalse(r.verify())


if __name__ == "__main__":
    unittest.main()
