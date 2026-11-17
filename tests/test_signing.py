"""Ed25519 signing tests: RFC 8032 vectors, round-trip, tamper, both backends."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autonomic import signing  # noqa: E402

# RFC 8032 Section 7.1 TEST 1 (empty message)
RFC8032_SEED = bytes.fromhex(
    "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
RFC8032_PUB = bytes.fromhex(
    "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
RFC8032_SIG = bytes.fromhex(
    "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
    "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")


class TestEd25519(unittest.TestCase):
    def test_backend_available(self):
        self.assertIn(signing.backend(), ("cryptography", "pure-python"))

    def test_rfc8032_test_vector_public_key(self):
        self.assertEqual(signing.public_key_from_seed(RFC8032_SEED), RFC8032_PUB)

    def test_rfc8032_test_vector_signature(self):
        sig = signing.sign(RFC8032_SEED, b"")
        self.assertEqual(sig, RFC8032_SIG)

    def test_rfc8032_verify(self):
        self.assertTrue(signing.verify(RFC8032_PUB, b"", RFC8032_SIG))

    def test_roundtrip_and_tamper(self):
        seed, pub = signing.generate_keypair()
        msg = b"ledger-bundle-root"
        sig = signing.sign(seed, msg)
        self.assertTrue(signing.verify(pub, msg, sig))
        self.assertFalse(signing.verify(pub, msg + b"x", sig))
        bad = bytearray(sig)
        bad[0] ^= 0xFF
        self.assertFalse(signing.verify(pub, msg, bytes(bad)))

    def test_pure_python_matches_rfc_vector(self):
        # Force the pure implementation path directly.
        self.assertEqual(signing._pure_publickey(RFC8032_SEED), RFC8032_PUB)
        self.assertEqual(signing._pure_sign(RFC8032_SEED, b""), RFC8032_SIG)
        self.assertTrue(signing._pure_verify(RFC8032_PUB, b"", RFC8032_SIG))


if __name__ == "__main__":
    unittest.main()
