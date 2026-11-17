"""1.0.5 / F-04 regression: failed undo must not return success exit code.

Constitution D1-D4: all-failed -> 1, partial -> 2, all-success -> 0,
bad argument -> 3. JSON carries exit_code and failed[].
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autonomic.cli import result_exit_code  # noqa: E402


class TestF04ExitCode(unittest.TestCase):
    def test_d1_all_failed(self):
        self.assertEqual(result_exit_code({"ok": False, "restored": [], "failed": [{"path": "a"}]}), 1)

    def test_d2_partial(self):
        self.assertEqual(result_exit_code({"ok": False, "restored": ["a"], "failed": [{"path": "b"}]}), 2)

    def test_d3_all_success(self):
        self.assertEqual(result_exit_code({"ok": True, "restored": ["a", "b"], "failed": []}), 0)

    def test_d4_bad_argument(self):
        self.assertEqual(result_exit_code(None, invalid=True), 3)

    def test_ok_false_without_failed_still_fails(self):
        self.assertEqual(result_exit_code({"ok": False}), 1)


if __name__ == "__main__":
    unittest.main()
