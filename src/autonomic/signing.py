"""Ed25519 signatures (RFC 8032) - real asymmetric crypto, offline, no deps.

Two backends, chosen automatically:
  1. ``cryptography`` if installed (fast, constant-time).
  2. A self-contained pure-Python RFC 8032 implementation otherwise, so the
     product works on a bare offline machine with only the stdlib.

This replaces the earlier HMAC placeholder for ledger bundles, content packs
and the two-key vault. Signatures are real Ed25519: a third party can verify
them offline with only the public key.
"""
from __future__ import annotations

import hashlib
import os

# --------------------------------------------------------------------------
# Backend selection
# --------------------------------------------------------------------------
try:  # pragma: no cover - depends on environment
    from cryptography.hazmat.primitives.asymmetric import ed25519 as _ed
    from cryptography.hazmat.primitives import serialization as _ser
    _HAVE_CRYPTOGRAPHY = True
except Exception:  # noqa: BLE001
    _HAVE_CRYPTOGRAPHY = False


# --------------------------------------------------------------------------
# Pure-Python Ed25519 (RFC 8032). Compact, well-known construction.
# --------------------------------------------------------------------------
_p = 2 ** 255 - 19
_q = 2 ** 252 + 27742317777372353535851937790883648493


def _inv(x):
    return pow(x, _p - 2, _p)


_d = -121665 * _inv(121666) % _p
_I = pow(2, (_p - 1) // 4, _p)


def _xrecover(y):
    xx = (y * y - 1) * _inv(_d * y * y + 1)
    x = pow(xx, (_p + 3) // 8, _p)
    if (x * x - xx) % _p != 0:
        x = (x * _I) % _p
    if x % 2 != 0:
        x = _p - x
    return x


_By = (4 * _inv(5)) % _p
_Bx = _xrecover(_By)
_B = (_Bx % _p, _By % _p, 1, (_Bx * _By) % _p)


def _edwards_add(P, Q):
    x1, y1, z1, t1 = P
    x2, y2, z2, t2 = Q
    a = (y1 - x1) * (y2 - x2) % _p
    b = (y1 + x1) * (y2 + x2) % _p
    c = t1 * 2 * _d * t2 % _p
    dd = z1 * 2 * z2 % _p
    e = b - a
    f = dd - c
    g = dd + c
    h = b + a
    return (e * f % _p, g * h % _p, f * g % _p, e * h % _p)


def _edwards_double(P):
    x1, y1, z1, _t1 = P
    a = x1 * x1 % _p
    b = y1 * y1 % _p
    c = 2 * z1 * z1 % _p
    h = a + b
    e = h - (x1 + y1) * (x1 + y1)
    g = a - b
    f = c + g
    return (e * f % _p, g * h % _p, f * g % _p, e * h % _p)


def _scalarmult(P, e):
    if e == 0:
        return (0, 1, 1, 0)
    Q = _scalarmult(P, e // 2)
    Q = _edwards_double(Q)
    if e & 1:
        Q = _edwards_add(Q, P)
    return Q


def _encodeint(y):
    return y.to_bytes(32, "little")


def _encodepoint(P):
    x, y, z, _t = P
    zi = _inv(z)
    x = x * zi % _p
    y = y * zi % _p
    bits = [(y >> i) & 1 for i in range(255)] + [x & 1]
    return bytes(sum(bits[i * 8 + j] << j for j in range(8)) for i in range(32))


def _decodeint(s):
    return int.from_bytes(s, "little")


def _decodepoint(s):
    y = _decodeint(s) & ((1 << 255) - 1)
    x = _xrecover(y)
    if x & 1 != (s[31] >> 7) & 1:
        x = _p - x
    P = (x, y, 1, (x * y) % _p)
    return P


def _H(m):
    return hashlib.sha512(m).digest()


def _expand(seed):
    h = _H(seed)
    a = 2 ** 254 + sum(2 ** i * ((h[i // 8] >> (i % 8)) & 1) for i in range(3, 254))
    return a, h[32:]


def _pure_publickey(seed):
    a, _ = _expand(seed)
    return _encodepoint(_scalarmult(_B, a))


def _pure_sign(seed, msg):
    a, prefix = _expand(seed)
    A = _encodepoint(_scalarmult(_B, a))
    r = _decodeint(_H(prefix + msg)) % _q
    R = _encodepoint(_scalarmult(_B, r))
    h = _decodeint(_H(R + A + msg)) % _q
    S = (r + h * a) % _q
    return R + _encodeint(S)


def _pure_verify(pub, msg, sig):
    if len(sig) != 64 or len(pub) != 32:
        return False
    try:
        R = _decodepoint(sig[:32])
        S = _decodeint(sig[32:])
        A = _decodepoint(pub)
    except Exception:  # noqa: BLE001
        return False
    h = _decodeint(_H(sig[:32] + pub + msg)) % _q
    left = _scalarmult(_B, S)
    right = _edwards_add(R, _scalarmult(A, h))
    return _encodepoint(left) == _encodepoint(right)


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def backend() -> str:
    return "cryptography" if _HAVE_CRYPTOGRAPHY else "pure-python"


def generate_keypair() -> tuple[bytes, bytes]:
    """Return (private_seed_32, public_key_32)."""
    seed = os.urandom(32)
    return seed, public_key_from_seed(seed)


def public_key_from_seed(seed: bytes) -> bytes:
    if _HAVE_CRYPTOGRAPHY:
        priv = _ed.Ed25519PrivateKey.from_private_bytes(seed)
        return priv.public_key().public_bytes(_ser.Encoding.Raw, _ser.PublicFormat.Raw)
    return _pure_publickey(seed)


def sign(seed: bytes, message: bytes) -> bytes:
    if _HAVE_CRYPTOGRAPHY:
        priv = _ed.Ed25519PrivateKey.from_private_bytes(seed)
        return priv.sign(message)
    return _pure_sign(seed, message)


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    if _HAVE_CRYPTOGRAPHY:
        try:
            pub = _ed.Ed25519PublicKey.from_public_bytes(public_key)
            pub.verify(signature, message)
            return True
        except Exception:  # noqa: BLE001
            return False
    return _pure_verify(public_key, message, signature)


def sign_file(seed: bytes, path: str) -> bytes:
    with open(path, "rb") as fh:
        return sign(seed, fh.read())


def verify_file(public_key: bytes, path: str, signature: bytes) -> bool:
    with open(path, "rb") as fh:
        return verify(public_key, fh.read(), signature)


def hexlify(b: bytes) -> str:
    return b.hex()


def unhexlify(s: str) -> bytes:
    return bytes.fromhex(s)
