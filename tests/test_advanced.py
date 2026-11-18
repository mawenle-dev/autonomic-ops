"""Advanced subsystem tests: every function in advanced.py is exercised."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autonomic import advanced as A, fvc, decoders  # noqa: E402


class TestVerdict(unittest.TestCase):
    def test_fingerprint_dedupe_and_recur(self):
        r = A.VerdictRegistry()
        a = r.add("disk", "/", ["e1"], now=100)
        b = r.add("disk", "/", ["e1"], now=200)
        self.assertEqual(a["fp"], b["fp"])
        self.assertEqual(b["count"], 2)
        self.assertEqual(b["recur_of"], a["fp"])

    def test_baseline_dual_condition(self):
        b = A.Baseline(bucket_seconds=60)
        for i in range(20):
            b.observe(10.0, ts=0)  # tight baseline around 10
        anom, why = b.is_anomalous(10.0, ts=0)
        self.assertFalse(anom)
        anom, why = b.is_anomalous(1000.0, ts=0)
        self.assertTrue(anom)
        self.assertEqual(why, "deviate_and_beyond")

    def test_parser_lab(self):
        lab = A.ParserLab(decoders.decode_line)
        r = lab.run([
            {"line": "Failed password for root from 1.2.3.4 port 22 ssh2", "expect_decoder": "sshd_failed"},
            {"line": "hello world nothing", "expect_decoder": "sshd_failed"},
            {"line": "hello world nothing", "expect_trigger": False},
        ])
        self.assertEqual(r["hits"], 1)
        self.assertEqual(r["misses"], 1)
        self.assertEqual(r["false_positives"], 0)

    def test_explain(self):
        e = A.explain({"kind": "disk", "subject": "/", "severity": "high",
                       "chain": [{"s": 1}], "next_action": "clean"})
        self.assertEqual(set(e), {"conclusion", "basis", "blocker", "next"})
        self.assertEqual(e["next"], "clean")


class TestCovenant(unittest.TestCase):
    def test_default_covenant_requires_channel(self):
        with self.assertRaises(ValueError):
            A.default_covenant("x", has_channel=False)
        c = A.default_covenant("x", has_channel=True)
        self.assertTrue(c["obligations"]["notify"])
        self.assertTrue(c["obligations"]["diagnostic"])

    def test_escalate_budget(self):
        b = fvc.Budget(bytes=100, files=1, actions=1, blast=1, rate=1)
        e = A.escalate_budget(b, factor=2.0)
        self.assertEqual(e["budget"].bytes, 200)

    def test_merge_strict(self):
        b1 = fvc.Budget(bytes=100, files=5, actions=5, blast=5, rate=5)
        b2 = fvc.Budget(bytes=50, files=9, actions=9, blast=9, rate=9)
        m = A.merge_strict([{"budget": b1, "expiry": 100}, {"budget": b2, "expiry": 50}])
        self.assertEqual(m["budget"]["bytes"], 50)
        self.assertEqual(m["budget"]["files"], 5)
        self.assertEqual(m["expiry"], 50)


class TestShadow(unittest.TestCase):
    def test_overlay_predict_and_diff(self):
        pred = A.overlay_predict({"a": "1"}, {"b": "2"})
        self.assertEqual(pred["predicted"], {"a": "1", "b": "2"})
        d = A.diff_impact(pred, {"a": "1", "c": "3"})
        self.assertIn("b", d["removed"])
        self.assertIn("c", d["added"])

    def test_fallback_and_calibrate(self):
        f = A.fallback_estimate({"x": "yy"})
        self.assertEqual(f["confidence"], 0.5)
        self.assertTrue(A.calibrate(10, 15)["recommend_lower_budget"])
        self.assertFalse(A.calibrate(10, 11)["over_20pct"])


class TestUndo(unittest.TestCase):
    def test_typed_undo(self):
        for k in A.UNDO_KINDS:
            h = A.UndoHandle(k, "ref")
            self.assertEqual(h.kind, k)
        with self.assertRaises(ValueError):
            A.UndoHandle("bad", "ref")

    def test_undo_window(self):
        w = A.UndoWindow(600)
        self.assertFalse(w.needs_approval(created_at=1000, now=1500))
        self.assertTrue(w.needs_approval(created_at=1000, now=1700))


class TestDispatch(unittest.TestCase):
    def test_channels(self):
        for k in A.CHANNELS:
            c = A.Channel(k)
            self.assertTrue(c.deliver({"m": 1}))
            self.assertEqual(c.sent, 1)
        with self.assertRaises(ValueError):
            A.Channel("bogus")

    def test_dlq_dedupe_and_retry(self):
        q = A.DLQ(max_attempts=2)
        q.push({"m": 1}, "err")
        q.push({"m": 1}, "err2")  # dedupe -> attempts 2
        self.assertEqual(len(q.items), 1)
        self.assertEqual(q.items[0]["attempts"], 2)
        res = q.retry(lambda m: True)
        self.assertEqual(res["delivered"], 1)

    def test_escalate_unacked(self):
        incs = [{"id": "i1", "opened_at": 0}, {"id": "i2", "opened_at": 0}]
        out = A.escalate_unacked(incs, {"i2": "acknowledged"}, now=2000, sla=900)
        self.assertEqual([o["incident"] for o in out], ["i1"])


class TestFabric(unittest.TestCase):
    def test_readonly_aggregate(self):
        f = A.Fabric("A")
        f.register("B", {"cpu": 10})
        f.register("C", {"cpu": 20})
        agg = f.aggregate("cpu")
        self.assertEqual(agg["nodes"], 2)
        self.assertEqual(agg["max"], 20)

    def test_cannot_register_self(self):
        f = A.Fabric("A")
        with self.assertRaises(ValueError):
            f.register("A", {})

    def test_topology_and_assert(self):
        f = A.Fabric("A")
        f.register("B", {})
        self.assertEqual(f.topology()["peers"], ["B"])
        self.assertTrue(f.assert_readonly({"op": "read"}))
        self.assertFalse(f.assert_readonly({"op": "exec"}))

    def test_cross_verify(self):
        f = A.Fabric("A")
        self.assertTrue(f.cross_verify({"r": 1}, lambda r: r["r"] == 1))


class TestSealStateIngestIntel(unittest.TestCase):
    def test_seal_roundtrip_and_tamper(self):
        from autonomic import signing
        seed, _ = signing.generate_keypair()
        files = {"a.txt": b"hello", "b.txt": b"world"}
        bundle = A.seal_build(files, seed)
        self.assertTrue(A.seal_verify(bundle, files)["ok"])
        self.assertFalse(A.seal_verify(bundle, {"a.txt": b"HELLO", "b.txt": b"world"})["ok"])

    def test_derive_state_and_env(self):
        st = A.derive_state({"version": "1.0.0", "released": {"1.0.0": 1},
                             "gates": {"g1": True, "g2": False}, "pending": 50})
        self.assertEqual(st["gates_open"], 1)
        self.assertEqual(st["pending_versions"], 50)
        ok = A.check_env_separation({"dev": {"data_dir": "/a"}, "prod": {"data_dir": "/a"}})
        self.assertFalse(ok["ok"])

    def test_remote_ingest_and_metrics(self):
        from autonomic import collectors
        import tempfile, shutil
        d = tempfile.mkdtemp()
        try:
            db = collectors.TSDB(d)
            r = A.remote_ingest(db, {"samples": [{"name": "x", "value": 1, "ts": 1}]})
            self.assertEqual(r["ingested"], 1)
            txt = A.metrics_text({"x": [{"ts": 1, "value": 1}]})
            self.assertIn("x 1", txt)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_intel_pack_signature(self):
        from autonomic import signing
        seed, _ = signing.generate_keypair()
        pack = A.build_intel_pack([{"ip": "1.2.3.4", "score": 90}], seed)
        self.assertTrue(A.verify_intel_pack(pack))
        pack["body"]["entries"].append({"ip": "9.9.9.9"})
        self.assertFalse(A.verify_intel_pack(pack))


if __name__ == "__main__":
    unittest.main()


class TestRemainingVersions(unittest.TestCase):
    def test_gate_check(self):
        self.assertTrue(A.gate_check("n1", True, True, 80)["pass"])
        self.assertFalse(A.gate_check("", True, True, 80)["pass"])
        self.assertIn("coverage_below_threshold", A.gate_check("n", True, True, 50)["reasons"])

    def test_recent_terminal(self):
        tasks = [{"state": "done", "finished_at": i} for i in range(20)]
        tasks.append({"state": "running", "finished_at": 99})
        r = A.recent_terminal(tasks, 10)
        self.assertEqual(len(r), 10)
        self.assertEqual(r[0]["finished_at"], 19)

    def test_codegen_validate(self):
        self.assertTrue(A.codegen_validate("make unit", "srv", "text")["ok"])
        self.assertFalse(A.codegen_validate("x", "bogus", "text")["ok"])
        self.assertFalse(A.codegen_validate("x", "cfg", "path /etc/passwd")["ok"])

    def test_rotate_keys(self):
        from autonomic import signing
        o, _ = signing.generate_keypair()
        n, _ = signing.generate_keypair()
        r = A.rotate_keys(o, n, {"a": 1})
        self.assertTrue(r["verify_new"])

    def test_choreo_receipt_and_render(self):
        rc = A.choreo_receipt({"id": "r1", "results": {"a": 1, "b": 2}, "done": True})
        self.assertEqual(rc["steps"], 2)
        svg = A.render_dag([{"id": "a", "state": "done"}, {"id": "b"}], [("a", "b")])
        self.assertIn("<svg", svg)
        self.assertIn("a", svg)

    def test_private_source_sync(self):
        import tempfile, shutil
        d = tempfile.mkdtemp()
        try:
            src = A.PrivateSource(d)
            p = os.path.join(d, "pack.tar.gz")
            open(p, "wb").write(b"data")
            src.publish("rules/x", "1.0", p)
            out = src.sync(os.path.join(d, "air"))
            self.assertEqual(out["copied"], 1)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_public_report_and_repo_hygiene(self):
        r = A.public_report("nonce", {"a": 1})
        self.assertIn("hash", r)
        h = A.repo_hygiene(os.path.join(os.path.dirname(__file__), ".."))
        self.assertIn("test_loc", h)

    def test_memory_docs(self):
        import tempfile, shutil
        d = tempfile.mkdtemp()
        try:
            out = A.memory_docs(d, "2026-01-01", ["did a", "did b"])
            self.assertTrue(os.path.exists(out["log"]))
            self.assertTrue(os.path.exists(os.path.join(d, "docs", "memory", "INDEX.md")))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestScenarioEngine(unittest.TestCase):
    def test_slow_accumulation_caught_by_count(self):
        from autonomic.sentinel import ScenarioEngine
        e = ScenarioEngine(window=600,
                           count_thresholds=[(5, "rate_limit"), (15, "challenge")],
                           rate_capacity=1000, rate_refill=1000)  # rate never trips
        last = None
        for i in range(6):
            last = e.observe({"ip": "1.2.3.4"}, now=i * 10)
        self.assertTrue(last["rate_ok"])           # under rate
        self.assertEqual(last["by_count"], "rate_limit")  # but count caught it

    def test_burst_caught_by_rate(self):
        from autonomic.sentinel import ScenarioEngine
        e = ScenarioEngine(window=600, count_thresholds=[(100, "ban")],
                           rate_capacity=2, rate_refill=0.0)
        e.observe({"ip": "9.9.9.9"}, now=0)
        e.observe({"ip": "9.9.9.9"}, now=0)
        third = e.observe({"ip": "9.9.9.9"}, now=0)
        self.assertFalse(third["rate_ok"])
        self.assertEqual(third["by_rate"], "rate_limit")

    def test_dims_separate(self):
        from autonomic.sentinel import ScenarioEngine
        e = ScenarioEngine(window=600, rate_capacity=1000, rate_refill=1000)
        e.observe({"ip": "1.1.1.1"}, now=0)
        r = e.observe({"ip": "2.2.2.2"}, now=0)
        self.assertEqual(r["count"], 1.0)
