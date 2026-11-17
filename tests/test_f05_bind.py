"""1.0.6 / F-05 regression: dashboard --bind HOST:PORT parsing (E1-E5).

README commands must work: --bind 127.0.0.1:8081 / --port 8081 / --bind :8081
all bind; a bare host gives a clear error; default is loopback only.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autonomic.dashboard import parse_bind  # noqa: E402


class TestF05Bind(unittest.TestCase):
    def test_e1_host_port(self):
        self.assertEqual(parse_bind("127.0.0.1:8081"), ("127.0.0.1", 8081))

    def test_e2_compat_port(self):
        self.assertEqual(parse_bind("", port=8081), ("127.0.0.1", 8081))

    def test_e3_colon_port(self):
        self.assertEqual(parse_bind(":8081"), ("127.0.0.1", 8081))

    def test_e4_bare_host_rejected_with_examples(self):
        with self.assertRaises(ValueError) as ctx:
            parse_bind("127.0.0.1")
        msg = str(ctx.exception)
        self.assertIn("--bind", msg)
        self.assertIn("8081", msg)

    def test_e5_default_is_loopback(self):
        host, port = parse_bind()
        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(port, 8081)

    def test_nonloopback_host_parsed(self):
        self.assertEqual(parse_bind("0.0.0.0:9090"), ("0.0.0.0", 9090))


if __name__ == "__main__":
    unittest.main()
