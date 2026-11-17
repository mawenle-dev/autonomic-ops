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

    def test_versions_full_has_121_items(self):
        items = self.studio.versions_full()
        self.assertEqual(len(items), 121)
        self.assertEqual(items[0]["version"], "1.0.0")
        self.assertTrue(items[0]["released"])
        self.assertEqual(items[1]["version"], "1.0.1")
        self.assertEqual(items[1]["ordinal"], 1)
        self.assertEqual(items[-1]["version"], "3.0.3")
        self.assertEqual(items[-1]["ordinal"], 120)
        # every item carries a highlight (benefit) and a gate
        self.assertTrue(all(it.get("highlights") for it in items[1:]))

    def test_version_detail(self):
        d = self.studio.version_detail("1.2.0")
        self.assertIsNotNone(d)
        self.assertEqual(d["ordinal"], 28)
        self.assertIn("highlights", d)

    def test_publish_duplicate_rejected(self):
        st = self.studio.state()
        gate = st["next_detail"]["gate"]
        r1 = self.studio.publish(preflight_ok=True, gate=gate)
        self.assertEqual(r1["version"], "1.0.1")
        self.assertIn("1.0.1", self.studio.released())
        # retract rolls the version back, but the version stays recorded as
        # released -> a republish of the same version must be refused.
        self.studio.retract("1.0.1")
        core.write_release_version(self.studio.release_yaml(), "1.0.0")
        r2 = self.studio.publish(preflight_ok=True, gate="fix_quarantine")
        self.assertIn(r2["reason"], ("already_released", "already_released_today"))

    def test_one_version_per_day(self):
        st = self.studio.state()
        self.studio.publish(preflight_ok=True, gate=st["next_detail"]["gate"])
        # a second, different version on the same day is refused
        st2 = self.studio.state()
        r = self.studio.publish(preflight_ok=True, gate=st2["next_detail"]["gate"])
        self.assertEqual(r["reason"], "already_released_today")

    def test_version_snapshot_written_not_overwritten(self):
        st = self.studio.state()
        self.studio.publish(preflight_ok=True, gate=st["next_detail"]["gate"])
        snap = os.path.join(self.tmp, "release", "docs", "1.0.1.md")
        self.assertTrue(os.path.exists(snap))
        with open(snap, encoding="utf-8") as fh:
            first = fh.read()
        # simulate a re-publish attempt of the same version (should be blocked,
        # and the snapshot must remain byte-identical / not overwritten)
        self.studio.publish(preflight_ok=True, gate="fix_quarantine")
        with open(snap, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), first)


if __name__ == "__main__":
    unittest.main()
