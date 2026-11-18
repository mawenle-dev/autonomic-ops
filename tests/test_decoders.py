"""Decoder tests: every decoder decodes its sample; multi-match; unknown line."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autonomic import decoders  # noqa: E402


class TestDecoders(unittest.TestCase):
    def test_at_least_60_decoders(self):
        self.assertGreaterEqual(len(decoders.DECODERS), 60)

    def test_all_samples_decode_with_declared_fields(self):
        r = decoders.self_test()
        self.assertTrue(r["ok"], r["bad"])

    def test_unique_names(self):
        names = decoders.decoder_names()
        self.assertEqual(len(names), len(set(names)))

    def test_multi_match(self):
        # a syslog sshd line matches both syslog_generic and sshd_failed
        line = "Jan  1 00:00:01 web01 sshd[9]: Failed password for root from 1.2.3.4 port 22 ssh2"
        hits = decoders.decode_line(line)
        names = {h["decoder"] for h in hits}
        self.assertIn("sshd_failed", names)
        self.assertIn("syslog_generic", names)

    def test_unknown_line(self):
        self.assertEqual(decoders.decode_line("this line matches nothing at all"), [])

    def test_field_extraction(self):
        r = decoders.decode_with("sshd_failed",
                                 "Failed password for admin from 5.6.7.8 port 2222 ssh2")
        self.assertEqual(r["fields"]["user"], "admin")
        self.assertEqual(r["fields"]["ip"], "5.6.7.8")
        self.assertEqual(r["fields"]["port"], "2222")


if __name__ == "__main__":
    unittest.main()
