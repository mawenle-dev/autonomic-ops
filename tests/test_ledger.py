"""L-1 ledger tests: negative ledger, anchors, signed bundle, replay, tamper (ch.6/G2)."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autonomic import ledger, util  # noqa: E402


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ao_ledger_")
        self.neg = ledger.NegativeLedger(os.path.join(self.tmp, "absence.jsonl"))
        self.records = [
            {"seq": 1, "epoch": 100, "type": "receipt", "hash": "a", "id": "c1"},
            {"seq": 2, "epoch": 200, "type": "covenant", "hash": "b", "id": "covenant-1"},
            {"seq": 3, "epoch": 300, "type": "verdict", "hash": "c", "id": "v1"},
        ]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_negative_ledger_records_and_explains(self):
        self.neg.record("budget_exhausted", "/tmp/x", trigger="cleanup", checks=["bytes"])
        found = self.neg.explain(reason_code="budget_exhausted")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["checks"], ["bytes"])

    def test_unknown_reason_rejected(self):
        with self.assertRaises(ValueError):
            self.neg.record("made_up_reason", "x")

    def test_all_nine_reason_codes(self):
        self.assertEqual(len(ledger.REASON_CODES), 9)
        for rc in ledger.REASON_CODES:
            self.neg.record(rc, "subject")
        cov = self.neg.coverage()
        self.assertEqual(cov["covered"], 9)
        self.assertEqual(cov["missing"], [])

    def test_anchor_and_tamper_detection(self):
        anc = ledger.LedgerAnchor(os.path.join(self.tmp, "anchors.jsonl"))
        a = anc.anchor(self.records)
        self.assertEqual((a["seq_from"], a["seq_to"], a["count"]), (1, 3, 3))
        self.assertTrue(anc.list())
        # tamper the anchor file
        with open(anc.path, "a", encoding="utf-8") as fh:
            fh.write('{"hash":"deadbeef","seq_from":1,"seq_to":3,"count":999}\n')
        bad = [x for x in anc.list() if x.get("tampered")]
        self.assertTrue(bad, "tampered anchor must be flagged")

    def test_bundle_offline_verify(self):
        bundle = ledger.export_bundle(self.records, [], signer="test-key")
        res = ledger.verify_bundle(bundle, signatures={"test-key": "pub"})
        self.assertTrue(res["ok"])
        self.assertEqual(res["count"], 3)
        bundle["records"][0]["hash"] = "tampered"
        res2 = ledger.verify_bundle(bundle)
        self.assertFalse(res2["ok"])
        self.assertIn("root_hash_mismatch", res2["errors"])

    def test_replay_reconstructs_state_at_time(self):
        state = ledger.replay(self.records, at_epoch=250)
        self.assertIn("covenant-1", state["covenants"])
        self.assertNotIn("v1", state["verdicts"])
        state2 = ledger.replay(self.records, at_epoch=350)
        self.assertIn("v1", state2["verdicts"])


if __name__ == "__main__":
    unittest.main()
