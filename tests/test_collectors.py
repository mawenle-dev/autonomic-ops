"""Collector + TSDB tests (2.0.0-2.0.5)."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autonomic import collectors  # noqa: E402


class TestCollectors(unittest.TestCase):
    LINUX = sys.platform.startswith("linux")

    def test_twelve_collectors(self):
        self.assertEqual(len(collectors.COLLECTORS), 12)

    @unittest.skipUnless(sys.platform.startswith("linux"), "collectors are Linux-only")
    def test_collect_all_no_crash(self):
        out = collectors.collect_all()
        self.assertEqual(len(out), 12)
        for name, facts in out.items():
            self.assertIsInstance(facts, (list, dict), name)

    @unittest.skipUnless(sys.platform.startswith("linux"), "collectors are Linux-only")
    def test_cpu_and_memory_are_facts(self):
        cpu = collectors.collect_cpu()
        mem = collectors.collect_memory()
        self.assertIsInstance(cpu, list)
        self.assertIsInstance(mem, list)
        for f in cpu + mem:
            self.assertTrue(hasattr(f, "metric"))

    @unittest.skipUnless(sys.platform.startswith("linux"), "collectors are Linux-only")
    def test_disk_on_root(self):
        d = collectors.collect_disk(["/"])
        self.assertTrue(d, "expected disk facts on linux")
        self.assertTrue(any(f.metric == "disk.used_percent" for f in d))

    @unittest.skipIf(sys.platform.startswith("linux"), "only meaningful off Linux")
    def test_refuses_off_linux(self):
        from autonomic.platform_guard import UnsupportedPlatform
        with self.assertRaises(UnsupportedPlatform):
            collectors.collect_all()


class TestTSDB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ao_tsdb_")
        self.db = collectors.TSDB(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_and_range_query(self):
        for i in range(10):
            self.db.write("cpu.load1", float(i), ts=1000 + i)
        r = self.db.query("cpu.load1", 1002, 1005)
        self.assertEqual([p["value"] for p in r["points"]], [2.0, 3.0, 4.0, 5.0])

    def test_downsample_avg(self):
        for i in range(60):
            self.db.write("x", 10.0, ts=i)  # 60 points over 60s
        ds = self.db.downsample("x", step=60, agg="avg")
        self.assertEqual(len(ds), 1)
        self.assertEqual(ds[0]["value"], 10.0)
        self.assertEqual(ds[0]["count"], 60)

    def test_downsample_min_max_count(self):
        for i, v in enumerate([5, 1, 9, 3]):
            self.db.write("y", float(v), ts=i)
        self.assertEqual(self.db.downsample("y", 60, "min")[0]["value"], 1.0)
        self.assertEqual(self.db.downsample("y", 60, "max")[0]["value"], 9.0)
        self.assertEqual(self.db.downsample("y", 60, "count")[0]["value"], 4.0)

    def test_labels_separate_series(self):
        self.db.write("disk.used", 50.0, ts=1, labels={"path": "/"})
        self.db.write("disk.used", 80.0, ts=1, labels={"path": "/data"})
        r = self.db.query("disk.used", 0, 10, labels={"path": "/data"})
        self.assertEqual(r["points"][0]["value"], 80.0)

    def test_retention(self):
        self.db.write("old", 1.0, ts=0)
        self.db.write("new", 2.0, ts=10 ** 9)
        removed = self.db.enforce_retention(now=10 ** 9)
        self.assertGreaterEqual(removed, 1)

    def test_persistence_file(self):
        self.db.write("p", 3.0, ts=1000)
        files = [f for f in os.listdir(self.tmp) if f.endswith(".jsonl")]
        self.assertTrue(files)


if __name__ == "__main__":
    unittest.main()
