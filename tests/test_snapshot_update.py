"""Signed snapshot update tests."""
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autonomic import fvc, signing, snapshot_update as su  # noqa: E402


class TestSnapshotUpdate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ao_snap_")
        self.src = os.path.join(self.tmp, "src")
        os.makedirs(os.path.join(self.src, "bin"))
        with open(os.path.join(self.src, "bin", "app"), "w") as fh:
            fh.write("v1")
        with open(os.path.join(self.src, "readme.txt"), "w") as fh:
            fh.write("hello")
        self.seed, self.pub = signing.generate_keypair()
        self.snap = os.path.join(self.tmp, "snap.tar.gz")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build_and_verify(self):
        r = su.build_snapshot(self.src, self.snap, self.seed)
        self.assertEqual(r["count"], 2)
        v = su.verify_snapshot(self.snap, self.pub)
        self.assertTrue(v["ok"], v["errors"])
        self.assertEqual(v["count"], 2)

    def test_wrong_key_rejected(self):
        su.build_snapshot(self.src, self.snap, self.seed)
        _, other = signing.generate_keypair()
        v = su.verify_snapshot(self.snap, other)
        self.assertFalse(v["ok"])
        self.assertIn("signature_invalid", v["errors"])

    def test_tampered_member_detected(self):
        su.build_snapshot(self.src, self.snap, self.seed)
        m = su._read_manifest(self.snap)
        # rebuild the archive with the same manifest but altered file bytes
        with tarfile.open(self.snap, "r:gz") as tf:
            blobs = {n: tf.extractfile(n).read() for n in tf.getnames() if n != su.MANIFEST}
        tampered = os.path.join(self.tmp, "tampered.tar.gz")
        with tarfile.open(tampered, "w:gz") as tf:
            data = json.dumps(m, ensure_ascii=False, sort_keys=True).encode()
            info = tarfile.TarInfo(su.MANIFEST)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
            for name, blob in blobs.items():
                if name == "bin/app":
                    blob = b"EVIL"
                info = tarfile.TarInfo(name)
                info.size = len(blob)
                tf.addfile(info, io.BytesIO(blob))
        v = su.verify_snapshot(tampered, self.pub)
        self.assertFalse(v["ok"])
        self.assertTrue(any(e.startswith("tampered:") for e in v["errors"]), v["errors"])

    def test_apply_replaces_target(self):
        su.build_snapshot(self.src, self.snap, self.seed)
        target = os.path.join(self.tmp, "live")
        os.makedirs(target)
        with open(os.path.join(target, "old.txt"), "w") as fh:
            fh.write("old")
        r = su.apply_snapshot(self.snap, target, self.pub)
        self.assertTrue(r["ok"], r)
        self.assertFalse(os.path.exists(os.path.join(target, "old.txt")))
        with open(os.path.join(target, "bin", "app")) as fh:
            self.assertEqual(fh.read(), "v1")

    def test_apply_tampered_leaves_target_untouched(self):
        su.build_snapshot(self.src, self.snap, self.seed)
        target = os.path.join(self.tmp, "live2")
        os.makedirs(target)
        with open(os.path.join(target, "keep.txt"), "w") as fh:
            fh.write("keep")
        r = su.apply_snapshot(self.snap, target, b"\x00" * 32)  # wrong key
        self.assertFalse(r["ok"])
        self.assertTrue(os.path.exists(os.path.join(target, "keep.txt")))

    def test_rollback(self):
        su.build_snapshot(self.src, self.snap, self.seed)
        target = os.path.join(self.tmp, "live3")
        os.makedirs(target)
        with open(os.path.join(target, "old.txt"), "w") as fh:
            fh.write("old")
        r = su.apply_snapshot(self.snap, target, self.pub)
        self.assertTrue(r["ok"])
        rb = su.rollback(target, r["backup"])
        self.assertTrue(rb["ok"], rb)
        self.assertTrue(os.path.exists(os.path.join(target, "old.txt")))

    def test_path_traversal_rejected(self):
        # craft a snapshot with ../evil and a valid signature
        evil = os.path.join(self.tmp, "evil.tar.gz")
        files = {"../evil": b"x"}
        manifest = {"format": "ao-snapshot/1", "created": 0,
                    "files": {"../evil": su.hashlib.sha256(b"x").hexdigest()}, "count": 1}
        manifest["signature"] = signing.hexlify(
            signing.sign(self.seed, fvc.canon(manifest).encode()))
        manifest["pub"] = signing.hexlify(self.pub)
        with tarfile.open(evil, "w:gz") as tf:
            data = json.dumps(manifest, sort_keys=True).encode()
            info = tarfile.TarInfo(su.MANIFEST); info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
            info = tarfile.TarInfo("../evil"); info.size = 1
            tf.addfile(info, io.BytesIO(b"x"))
        r = su.apply_snapshot(evil, os.path.join(self.tmp, "live4"), self.pub)
        self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main()
