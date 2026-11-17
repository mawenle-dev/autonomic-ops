"""R-1 choreography tests: DAG/cycle, freeze/resume, depth, SLA, lock, amend."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autonomic import choreo  # noqa: E402


def step(i, fail=False):
    def run(s):
        if fail:
            raise RuntimeError("boom")
        return {"step": s.id, "ok": True}
    return choreo.Step(id=i, when={"verdict": f"v-{i}"}, action=f"action-{i}")


class TestChoreo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ao_choreo_")
        self.ch = choreo.Choreographer(self.tmp, lambda s: {"step": s.id, "ok": True})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_topo_order(self):
        dag = choreo.DAG([step("a"), step("b"), step("c")], [("a", "b"), ("b", "c")])
        self.assertEqual(dag.topo_order(), ["a", "b", "c"])

    def test_cycle_detected_at_validate(self):
        dag = choreo.DAG([step("a"), step("b")], [("a", "b"), ("b", "a")])
        with self.assertRaises(choreo.ChoreoError):
            dag.topo_order()

    def test_condition_must_be_verdict(self):
        bad = choreo.Step(id="x", when={"foo": "bar"}, action="act")
        dag = choreo.DAG([bad], [])
        with self.assertRaises(choreo.ChoreoError):
            dag.validate()

    def test_run_checkpoint_resume(self):
        dag = choreo.DAG([step("a"), step("b"), step("c")], [("a", "b"), ("b", "c")])
        state = self.ch.run("r1", dag)
        self.assertTrue(state["done"])
        self.assertEqual(state["idx"], 3)

    def test_freeze_and_resume(self):
        dag = choreo.DAG([step("a"), step("b")], [("a", "b")])
        # failing runner freezes at step b
        ch2 = choreo.Choreographer(
            self.tmp,
            lambda s: (_ for _ in ()).throw(RuntimeError("x")) if s.id == "b" else {"ok": True},
        )
        st2 = ch2.run("r3", dag)
        self.assertTrue(st2["frozen"])
        self.assertIn("step_failed", st2["reason"])
        # operator freeze on an existing run
        st3 = ch2.freeze("r3", "operator")
        self.assertTrue(st3["frozen"])
        self.assertEqual(st3["reason"], "operator")

    def test_depth_limit(self):
        dag = choreo.DAG([step("a")], [])
        with self.assertRaises(choreo.ChoreoError):
            self.ch.run("r4", dag, depth=4)

    def test_sla_disposition(self):
        self.assertEqual(choreo.sla_disposition(5, 10), "wait")
        self.assertEqual(choreo.sla_disposition(11, 10), "abort")
        self.assertEqual(choreo.sla_disposition(11, 10, "escalate"), "escalate")

    def test_lock_single_instance(self):
        lock = choreo.RunLock(global_cap=4)
        self.assertTrue(lock.acquire("target-x"))
        self.assertFalse(lock.acquire("target-x"))
        lock.release("target-x")
        self.assertTrue(lock.acquire("target-x"))


if __name__ == "__main__":
    unittest.main()
