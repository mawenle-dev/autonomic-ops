"""Tests for newly built modules: dispatch/sentinel/converge/reliquary/counsel/prove."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autonomic import dispatch, sentinel, converge, reliquary, counsel, prove  # noqa: E402


class TestDispatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ao_dispatch_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fingerprint_and_event_merge(self):
        d = dispatch.Dispatcher(self.tmp)
        v = {"kind": "disk", "subject": "/", "evidence_refs": ["f1"]}
        r1 = d.emit(v)
        r2 = d.emit(v)
        self.assertEqual(r1["incident"], r2["incident"], "same verdict merges into one incident")

    def test_quiet_window_silences(self):
        d = dispatch.Dispatcher(self.tmp)
        d.add_silence(10)
        r = d.emit({"kind": "x", "subject": "y"})
        self.assertEqual(r["reason_code"], "silenced")

    def test_parent_suppresses_child(self):
        d = dispatch.Dispatcher(self.tmp, parents={"child": "parent"})
        d.emit({"kind": "disk", "subject": "parent", "evidence_refs": ["f"]})
        r = d.emit({"kind": "disk", "subject": "child", "evidence_refs": ["f"]})
        self.assertEqual(r["reason_code"], "suppressed_by_parent")

    def test_status_page_offline(self):
        d = dispatch.Dispatcher(self.tmp)
        d.emit({"kind": "x", "subject": "y"})
        p = os.path.join(self.tmp, "status.html")
        r = d.export_status_page(p)
        self.assertTrue(os.path.exists(p))
        with open(p, encoding="utf-8") as fh:
            self.assertIn("<!doctype html>", fh.read())


class TestSentinel(unittest.TestCase):
    def test_self_address_never_banned(self):
        for ip in ("127.0.0.1", "10.0.0.1", "192.168.1.1", "::1", "169.254.1.1"):
            self.assertTrue(sentinel.is_self_address(ip), f"{ip} must be protected")
        self.assertFalse(sentinel.is_self_address("8.8.8.8"))

    def test_ban_rejects_self(self):
        s = sentinel.Sentinel(tempfile.mkdtemp(prefix="ao_sent_"))
        r = s.ban("127.0.0.1")
        self.assertEqual(r["reason_code"], "self_lock_protected")

    def test_five_levels_and_auto_release(self):
        s = sentinel.Sentinel(tempfile.mkdtemp(prefix="ao_sent_"))
        s.set_level("8.8.8.8", "ban", now=0)
        rel = s.auto_release(now=99999)
        self.assertIn("8.8.8.8", rel)

    def test_story_seven_fields(self):
        st = sentinel.Story("1.2.3.4", "sshd", "brute", 50, False, "rate_limit", 120)
        self.assertEqual(len(st.to_dict()), 7)

    def test_compliance_30_controls(self):
        self.assertEqual(len(sentinel.CONTROL_IDS), 30)
        r = sentinel.compliance_score({c: True for c in sentinel.CONTROL_IDS[:15]})
        self.assertEqual(r["passed"], 15)
        self.assertEqual(r["total"], 30)


class TestConverge(unittest.TestCase):
    def setUp(self):
        self.state = {"f": "old"}
        self.c = converge.Converger(
            tempfile.mkdtemp(prefix="ao_conv_"),
            reader=lambda r: self.state.get(r.target, ""),
            applier=lambda r: (self.state.__setitem__(r.target, r.desired) or True),
        )

    def test_check_zero_side_effect(self):
        r = converge.Resource("file", "f", "new")
        before = dict(self.state)
        self.c.check(r)
        self.assertEqual(self.state, before)

    def test_apply_and_idempotency(self):
        r = converge.Resource("file", "f", "new")
        a = self.c.apply(r)
        self.assertTrue(a["changed"])
        b = self.c.apply(r)
        self.assertFalse(b["changed"])

    def test_drift_produces_verdict_not_action(self):
        r = converge.Resource("file", "f", "new")
        v = self.c.observe_drift(r)
        self.assertEqual(v["kind"], "drift")
        self.assertEqual(self.state["f"], "old", "drift must not mutate")

    def test_20_templates(self):
        self.assertEqual(len(converge.TEMPLATES), 20)
        self.assertTrue(converge.load_template("ssh-hardening"))


class TestReliquary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ao_rel_")
        from autonomic import signing
        self.seed, self.pub = signing.generate_keypair()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unsigned_rejected(self):
        rel = reliquary.Reliquary(os.path.join(self.tmp, "inst"), self.pub)
        m = reliquary.PackManifest("rules/test", "1.0", ["read"])
        p = reliquary.build_pack(os.path.join(self.tmp, "t.tar.gz"), m, {"r.yaml": b"x"}, self.seed)
        m2 = reliquary.PackManifest("rules/test", "1.0", ["read"])
        # tamper signature off
        m2.signature = ""
        self.assertFalse(reliquary.verify_manifest(m2, self.pub))

    def test_signed_install(self):
        rel = reliquary.Reliquary(os.path.join(self.tmp, "inst"), self.pub)
        m = reliquary.PackManifest("rules/ok", "1.0", ["read"])
        p = reliquary.build_pack(os.path.join(self.tmp, "ok.tar.gz"), m, {"r.yaml": b"content"}, self.seed)
        res = rel.install(p)
        self.assertTrue(res["ok"])
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "inst", "r.yaml")))

    def test_wrong_key_rejected(self):
        from autonomic import signing
        other_seed, other_pub = signing.generate_keypair()
        rel = reliquary.Reliquary(os.path.join(self.tmp, "inst2"), other_pub)
        m = reliquary.PackManifest("rules/bad", "1.0", ["read"])
        p = reliquary.build_pack(os.path.join(self.tmp, "bad.tar.gz"), m, {"r.yaml": b"x"}, self.seed)
        res = rel.install(p)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason_code"], "unsigned_pack_rejected")

    def test_capability_token_one_shot_60s(self):
        t = reliquary.CapabilityToken("plugin:hello")
        self.assertTrue(t.consume("plugin:hello"))
        self.assertFalse(t.consume("plugin:hello"), "one-shot token cannot be reused")
        t2 = reliquary.CapabilityToken("plugin:x", issued_at=0, ttl=60)
        self.assertFalse(t2.valid("plugin:x", now=1000), "expired token rejected")


class TestCounsel(unittest.TestCase):
    def test_off_by_default(self):
        c = counsel.Counsel(enabled=False)
        pack = counsel.EvidencePack({"from": 0, "to": 1}, ["f"], [], [])
        out = {"cause": "x", "confidence": 0.5, "evidence_refs": ["f"],
               "suggested_txn": {}, "risk": "low"}
        self.assertFalse(c.ask(pack, out)["ok"])

    def test_schema_reject_and_proposal_only(self):
        c = counsel.Counsel(enabled=True)
        pack = counsel.EvidencePack({"from": 0, "to": 1}, ["f"], [], [])
        bad = {"cause": "x"}
        self.assertEqual(c.ask(pack, bad)["reason_code"], "schema_rejected")
        good = {"cause": "x", "confidence": 0.5, "evidence_refs": ["f"],
                "suggested_txn": {"action": "restart"}, "risk": "low"}
        r = c.ask(pack, good)
        self.assertTrue(r["ok"])
        self.assertTrue(r["requires_double_sign"])

    def test_no_exec_tool(self):
        c = counsel.Counsel(enabled=True)
        with self.assertRaises(PermissionError):
            c.call_tool("exec_shell")


class TestProve(unittest.TestCase):
    def test_nonce_in_drill_report(self):
        h = prove.ProveHarness(nonce="abc123")
        rep = h.run_all({})
        self.assertEqual(rep["nonce"], "abc123")
        # A drill with no supplied function must be reported as NOT implemented.
        self.assertFalse(rep["all_ok"])
        self.assertIn("not implemented", rep["results"]["disk-pressure"]["detail"])

    def test_nonce_in_drill_report_when_all_supplied(self):
        h = prove.ProveHarness(nonce="abc123")
        fns = {name: (lambda: {"ok": True, "detail": "ran"}) for name in prove.DRILLS}
        rep = h.run_all(fns)
        self.assertTrue(rep["all_ok"])

    def test_sample_coverage(self):
        reg = prove.SampleRegistry()
        reg.register("decoder", "ssh", {"line": "fail"}, {"kind": "ssh"})
        cov = reg.coverage({"decoder": ["ssh", "nginx"]})
        self.assertEqual(cov["covered"], 1)
        self.assertIn("decoder/nginx", cov["missing"])

    def test_reverse_tests_all_ok(self):
        r = prove.reverse_tests()
        self.assertTrue(r["all_ok"], r["checks"])


if __name__ == "__main__":
    unittest.main()
