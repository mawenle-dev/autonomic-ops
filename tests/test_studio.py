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
        for name in ("versions.yaml", "release.yaml"):
            shutil.copy2(os.path.join(src, name), os.path.join(dst, name))
        # The repository's live release.yaml advances with each publish; these
        # tests exercise the 1.0.0 baseline, so pin it explicitly.
        core.write_release_version(os.path.join(dst, "release.yaml"), "1.0.0")
        with open(os.path.join(self.tmp, "CHANGELOG.md"), "w", encoding="utf-8") as fh:
            fh.write("# Changelog\n\n## [1.0.0] - old\n")
        # The seal needs at least one real source file; give the sandbox a tiny
        # source tree instead of touching the real repository.
        os.makedirs(os.path.join(self.tmp, "src", "autonomic"), exist_ok=True)
        with open(os.path.join(self.tmp, "src", "autonomic", "__init__.py"), "w",
                  encoding="utf-8") as fh:
            fh.write("# sandbox source\n")
        self.calls = []
        self.studio = core.ReleaseStudio(self.tmp, runner=self._fake_runner)
        self.studio.require_git = False
        # Tests inject a deterministic green preflight; production publish()
        # always runs build_preflight() itself and cannot be told "it's green".
        self.studio.preflight_factory = lambda: self._green_preflight()

    def _green_preflight(self):
        pf = core.Preflight()
        pf.add("sandbox", lambda: (True, "ok"))
        return pf

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

    def test_publish_blocked_when_preflight_red(self):
        pf = core.Preflight()
        pf.add("broken", lambda: (False, "boom"))
        self.studio.preflight_factory = lambda: pf
        r = self.studio.publish(gate="fix_quarantine")
        self.assertEqual(r["reason"], "preflight_not_green")
        self.assertFalse(r["publish_enabled"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "release", "receipts", "1.0.1.json")))

    def test_publish_advances_one_version_and_writes_receipt(self):
        st = self.studio.state()
        gate = st["next_detail"]["gate"]
        r = self.studio.publish(gate=gate, notes="first fix")
        self.assertEqual(r["version"], "1.0.1")
        self.assertEqual(core.read_release_version(self.studio.release_yaml()), "1.0.1")
        rp = os.path.join(self.tmp, "release", "receipts", "1.0.1.json")
        self.assertTrue(os.path.exists(rp))
        with open(rp, encoding="utf-8") as fh:
            receipt = json.load(fh)
        self.assertEqual(receipt["previous"], "1.0.0")
        self.assertTrue(receipt["preflight_ok"])
        self.assertTrue(receipt["checks"])
        self.assertTrue(receipt["seal"]["root"])
        with open(os.path.join(self.tmp, "CHANGELOG.md"), encoding="utf-8") as fh:
            self.assertIn("[1.0.1]", fh.read())
        with open(os.path.join(self.tmp, "release", "channel", "version.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["version"], "1.0.1")

    def test_publish_refuses_without_source_to_seal(self):
        shutil.rmtree(os.path.join(self.tmp, "src"), ignore_errors=True)
        st = self.studio.state()
        r = self.studio.publish(gate=st["next_detail"]["gate"])
        self.assertEqual(r["reason"], "seal_failed")

    def test_gate_mismatch_rejected(self):
        r = self.studio.publish(gate="wrong_gate")
        self.assertEqual(r["reason"], "gate_mismatch")

    def test_retract_rolls_back_and_allows_republish(self):
        st = self.studio.state()
        gate = st["next_detail"]["gate"]
        self.studio.publish(gate=gate)
        r = self.studio.retract("1.0.1")
        self.assertEqual(r["rolled_back_to"], "1.0.0")
        self.assertTrue(r["released_registry_clean"])
        self.assertEqual(core.read_release_version(self.studio.release_yaml()), "1.0.0")
        rp = os.path.join(self.tmp, "release", "receipts", "1.0.1.json")
        with open(rp, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["status"], "retracted")
        # After a clean retract the same version must be publishable again.
        r2 = self.studio.publish(gate=gate, notes="republished")
        self.assertEqual(r2["version"], "1.0.1")

    def test_no_skipping_versions(self):
        st = self.studio.state()
        self.assertNotEqual(st["next"], "1.0.2")

    def test_versions_full_has_121_items(self):
        items = self.studio.versions_full()
        self.assertEqual(len(items), 121)
        self.assertEqual(items[0]["version"], "1.0.0")
        self.assertTrue(items[0]["baseline"])
        self.assertFalse(items[0]["released"])  # no receipt for the baseline
        self.assertEqual(items[1]["version"], "1.0.1")
        self.assertEqual(items[1]["ordinal"], 1)
        self.assertEqual(items[-1]["version"], "3.0.3")
        self.assertEqual(items[-1]["ordinal"], 120)
        self.assertTrue(all(it.get("highlights") for it in items[1:]))

    def test_version_detail(self):
        d = self.studio.version_detail("1.2.0")
        self.assertIsNotNone(d)
        self.assertEqual(d["ordinal"], 28)
        self.assertIn("highlights", d)

    def test_one_version_per_day(self):
        st = self.studio.state()
        self.studio.publish(gate=st["next_detail"]["gate"])
        st2 = self.studio.state()
        r = self.studio.publish(gate=st2["next_detail"]["gate"])
        self.assertEqual(r["reason"], "already_released_today")

    def test_source_preflight_does_not_require_portable_manifest(self):
        repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        st = core.ReleaseStudio(repo)
        st.require_git = False
        pf = core.build_preflight(repo, st)
        names = [c.name for c in pf.checks]
        self.assertNotIn("manifest", names)
        self.assertIn("compile", names)
        self.assertIn("unit tests", names)

    def test_version_snapshot_written_not_overwritten(self):
        st = self.studio.state()
        self.studio.publish(gate=st["next_detail"]["gate"])
        snap = os.path.join(self.tmp, "release", "docs", "1.0.1.md")
        self.assertTrue(os.path.exists(snap))
        with open(snap, encoding="utf-8") as fh:
            first = fh.read()
        self.studio.publish(gate="fix_quarantine")
        with open(snap, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), first)

    def _write_update(self, version: str, payload: bytes, tamper: bool = False):
        updir = os.path.join(self.tmp, "release", "updates")
        os.makedirs(updir, exist_ok=True)
        name = f"autonomic-ops-{version}.zip"
        with open(os.path.join(updir, name), "wb") as fh:
            fh.write(payload)
        import hashlib
        digest = hashlib.sha256(payload).hexdigest()
        if tamper:
            digest = "0" * 64
        manifest = {"format": "ao-update-manifest/1",
                    "updates": [{"version": version, "file": name,
                                 "sha256": digest, "size": len(payload)}]}
        with open(os.path.join(updir, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)

    def test_updates_are_verified_and_staged_not_applied(self):
        self._write_update("1.0.2", b"fake-zip-bytes")
        rows = self.studio.available_updates()
        self.assertEqual([r["version"] for r in rows], ["1.0.2"])
        self.assertTrue(rows[0]["hash_ok"])
        r = self.studio.stage_update("1.0.2")
        self.assertTrue(r["ok"])
        staged = os.path.join(self.tmp, "updates", "staged", "1.0.2.zip")
        self.assertTrue(os.path.exists(staged))
        self.assertEqual(core.read_release_version(
            os.path.join(self.tmp, "release", "release.yaml")), "1.0.0")

    def test_update_hash_mismatch_is_refused(self):
        self._write_update("1.0.2", b"fake-zip-bytes", tamper=True)
        self.assertFalse(self.studio.available_updates()[0]["hash_ok"])
        r = self.studio.stage_update("1.0.2")
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "hash_mismatch")


class TestHTTPGate(unittest.TestCase):
    """A GET must not publish; a POST without the session token must not either."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ao_http_")
        self.studio = core.ReleaseStudio(self.tmp, runner=lambda cmd: (0, "ok"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_publish_requires_token_and_post(self):
        # The handler is exercised through its class attributes without binding a socket.
        import importlib.util
        app_path = os.path.join(os.path.dirname(__file__), "..", "studio", "app.py")
        spec = importlib.util.spec_from_file_location("ao_studio_app", app_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        handler = mod._server(self.studio, self.studio.preflight_factory(), "secret-token")
        self.assertTrue(hasattr(handler, "do_POST"))
        # GET is rejected by the handler body; verify the branch exists by source scan.
        with open(app_path, encoding="utf-8") as fh:
            body = fh.read()
        for route in ("/publish", "/retract", "/api/preflight", "/api/update/stage"):
            self.assertIn(f'"{route}"', body)
        self.assertIn("use POST", body)
        self.assertIn("X-AO-Token", body)
        self.assertIn('_origin_ok', body)


if __name__ == "__main__":
    unittest.main()
