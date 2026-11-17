"""AO Release Studio core tests (ch.9): roadmap, version state, one-step,
preflight gate enforcement, publish receipt, retract. Uses a temp copy of release files.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from studio import core  # noqa: E402


class TestStudio(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ao_studio_")
        src = os.path.join(os.path.dirname(__file__), "..", "release")
        dst = os.path.join(self.tmp, "release")
        os.makedirs(os.path.join(dst, "receipts"), exist_ok=True)
        os.makedirs(os.path.join(dst, "channel"), exist_ok=True)
        # Copy only the manifest inputs; never copy build artifacts
        # (release/dist, release/portable) into the test sandbox.
        for name in ("versions.yaml", "release.yaml"):
            shutil.copy2(os.path.join(src, name), os.path.join(dst, name))
        with open(os.path.join(self.tmp, "CHANGELOG.md"), "w", encoding="utf-8") as fh:
            fh.write("# Changelog\n\n## [1.0.0] - old\n")
        self.calls = []
        self.studio = core.ReleaseStudio(self.tmp, runner=self._fake_runner)

    def _fake_runner(self, cmd):
        self.calls.append(cmd)
        return 0, "ok " + " ".join(cmd)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_roadmap_has_120_versions(self):
        vs = self.studio.versions()
        self.assertEqual(len(vs), 120)
        self.assertEqual(vs[0]["version"], "1.0.1")
        self.assertEqual(vs[-1]["version"], "3.0.3")

    def test_version_state(self):
        st = self.studio.state()
        self.assertEqual(st["current"], "1.0.0")
        self.assertEqual(st["next"], "1.0.1")
        self.assertIsNone(st["previous"])

    def test_preflight_gate_code_enforced(self):
        pf = core.Preflight()
        pf.add("test", lambda: (True, "all good"))
        self.assertTrue(pf.run()["publish_enabled"])
        pf.add("coverage", lambda: (False, "below 75%"))
        r = pf.run()
        self.assertFalse(r["publish_enabled"])
        self.assertEqual([c["name"] for c in r["checks"] if not c["ok"]], ["coverage"])

    def test_publish_blocked_when_not_green(self):
        r = self.studio.publish(preflight_ok=False, gate="fix_quarantine")
        self.assertEqual(r["reason"], "preflight_not_green")
        self.assertFalse(r["publish_enabled"])

    def test_publish_advances_one_version_and_writes_receipt(self):
        st = self.studio.state()
        gate = st["next_detail"]["gate"]
        r = self.studio.publish(preflight_ok=True, gate=gate, notes="first fix")
        self.assertEqual(r["version"], "1.0.1")
        self.assertEqual(core.read_release_version(self.studio.release_yaml()), "1.0.1")
        rp = os.path.join(self.tmp, "release", "receipts", "1.0.1.json")
        self.assertTrue(os.path.exists(rp))
        with open(rp, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["previous"], "1.0.0")
        with open(os.path.join(self.tmp, "CHANGELOG.md"), encoding="utf-8") as fh:
            self.assertIn("[1.0.1]", fh.read())
        with open(os.path.join(self.tmp, "release", "channel", "version.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["version"], "1.0.1")

    def test_gate_mismatch_rejected(self):
        r = self.studio.publish(preflight_ok=True, gate="wrong_gate")
        self.assertEqual(r["reason"], "gate_mismatch")

    def test_retract_rolls_back_version_and_marks_receipt(self):
        self.studio.publish(preflight_ok=True, gate="fix_quarantine")
        r = self.studio.retract("1.0.1")
        self.assertEqual(r["rolled_back_to"], "1.0.0")
        self.assertEqual(core.read_release_version(self.studio.release_yaml()), "1.0.0")
        rp = os.path.join(self.tmp, "release", "receipts", "1.0.1.json")
        with open(rp, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["status"], "retracted")

    def test_no_skipping_versions(self):
        # next is always exactly the roadmap successor; state cannot jump
        st = self.studio.state()
        self.assertNotEqual(st["next"], "1.0.2")


if __name__ == "__main__":
    unittest.main()
