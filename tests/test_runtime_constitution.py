"""Runtime constitution tests: every executed action produces a receipt and a
five-dimension budget that is actually enforced (and survives a restart)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autonomic.executor import ActionExecutor, ExecContext  # noqa: E402
from autonomic.models import ActionRequest  # noqa: E402
from autonomic.runtime import ConstitutionRuntime  # noqa: E402


class DummyConfig:
    def __init__(self, data_dir):
        self.data_dir = data_dir

    def get(self, key, default=None):
        if key == "agent.data_dir":
            return self.data_dir
        if key.startswith("constitution.budget."):
            return {"bytes": 10 ** 9, "files": 100, "actions": 100,
                    "blast": 10, "rate": 100}.get(key.rsplit(".", 1)[-1], default)
        return default


class FakeDecision:
    allowed = True
    risk = "low"
    reason = "test"

    def to_dict(self):
        return {"allowed": True, "risk": "low", "reason": "test"}


class FakePolicy:
    def decide(self, request, approved_by=None):
        return FakeDecision()


class FakeAudit:
    def __init__(self):
        self.rows = []

    def record(self, **kw):
        self.rows.append(kw)


class TestRuntimeReceipts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ao_runtime_")
        self.cfg = DummyConfig(self.tmp)
        self.audit = FakeAudit()
        self.ctx = ExecContext(self.cfg, None, None, None, None, FakePolicy(),
                               audit=self.audit, dry_run=False)
        self.executor = ActionExecutor(self.cfg, self.ctx)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_every_executed_action_has_a_receipt(self):
        r = self.executor.execute(ActionRequest(action="noop", target="demo"))
        self.assertTrue(r.ok)
        self.assertTrue(r.receipt_id)
        self.assertTrue(r.receipt_hash)
        journal = os.path.join(self.tmp, "receipts", "receipts.jsonl")
        with open(journal, encoding="utf-8") as fh:
            entry = json.loads(fh.readlines()[-1])
        self.assertEqual(entry["receipt"]["txn"]["action"], "noop")
        self.assertTrue(entry["receipt"]["undo"])
        self.assertEqual(entry["receipt"]["id"], r.receipt_id)

    def test_receipt_chain_is_hash_linked(self):
        self.executor.execute(ActionRequest(action="noop", target="a"))
        self.executor.execute(ActionRequest(action="noop", target="b"))
        journal = os.path.join(self.tmp, "receipts", "receipts.jsonl")
        with open(journal, encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual(rows[1]["prev_hash"], rows[0]["hash"])
        self.assertNotEqual(rows[0]["hash"], rows[1]["hash"])

    def test_budget_refuses_and_records_negative_entry(self):
        cfg = DummyConfig(self.tmp)
        budget = {"bytes": 10 ** 9, "files": 100, "actions": 1, "blast": 10, "rate": 100}

        class Tight(DummyConfig):
            def get(self, key, default=None):
                if key.startswith("constitution.budget."):
                    dim = key.rsplit(".", 1)[-1]
                    return budget.get(dim, default)
                return super().get(key, default)

        cfg2 = Tight(self.tmp)
        ctx = ExecContext(cfg2, None, None, None, None, FakePolicy(), audit=FakeAudit())
        ex = ActionExecutor(cfg2, ctx)
        first = ex.execute(ActionRequest(action="noop", target="one"))
        self.assertTrue(first.ok)
        second = ex.execute(ActionRequest(action="noop", target="two"))
        self.assertFalse(second.ok)
        self.assertIn("budget_exhausted", second.detail)
        neg = os.path.join(self.tmp, "ledger", "negative.jsonl")
        with open(neg, encoding="utf-8") as fh:
            codes = [json.loads(line)["reason_code"] for line in fh if line.strip()]
        self.assertIn("budget_exhausted", codes)

    def test_budget_persists_across_runtime_restart(self):
        rt1 = ConstitutionRuntime(self.cfg)
        ok, _ = rt1.budget.debit({"actions": 3})
        self.assertTrue(ok)
        rt2 = ConstitutionRuntime(self.cfg)
        self.assertGreaterEqual(rt2.budget.used()["actions"], 3)

    def test_chain_verifier_detects_tampering(self):
        self.executor.execute(ActionRequest(action="noop", target="a"))
        self.executor.execute(ActionRequest(action="noop", target="b"))
        journal = os.path.join(self.tmp, "receipts", "receipts.jsonl")
        self.assertTrue(ConstitutionRuntime(self.cfg).journal.verify_chain()["ok"])
        with open(journal, encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        rows[0]["receipt"]["txn"]["target"] = "tampered"
        with open(journal, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        verdict = ConstitutionRuntime(self.cfg).journal.verify_chain()
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["broken_at"], 1)
        self.assertEqual(verdict["reason"], "hash_mismatch")

    def test_policy_denial_is_recorded_as_negative(self):
        class Deny:
            def decide(self, request, approved_by=None):
                return type("D", (), {"allowed": False, "risk": "high", "reason": "nope",
                                      "to_dict": lambda self: {"allowed": False}})()
        ctx = ExecContext(self.cfg, None, None, None, None, Deny(), audit=FakeAudit())
        ex = ActionExecutor(self.cfg, ctx)
        r = ex.execute(ActionRequest(action="noop", target="x"))
        self.assertFalse(r.ok)
        neg = os.path.join(self.tmp, "ledger", "negative.jsonl")
        with open(neg, encoding="utf-8") as fh:
            codes = [json.loads(line)["reason_code"] for line in fh if line.strip()]
        self.assertIn("whitelist_miss", codes)


if __name__ == "__main__":
    unittest.main()
