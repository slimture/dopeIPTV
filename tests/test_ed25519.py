"""Ed25519 (core/ed25519.py): RFC 8032's own test vectors, and that a
changed message or signature does not pass."""
from __future__ import annotations

from dopeiptv.core import ed25519

VECTORS = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bac"
     "c61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e"
     "458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
]


def test_rfc_8032_vectors():
    for sk, pk, msg, sig in VECTORS:
        sk_b, pk_b, msg_b, sig_b = (bytes.fromhex(x) for x in (sk, pk, msg, sig))
        assert ed25519.public_key(sk_b) == pk_b
        assert ed25519.sign(sk_b, msg_b) == sig_b
        assert ed25519.verify(pk_b, msg_b, sig_b)


def test_a_changed_message_or_signature_fails():
    sk, pk = bytes(range(32)), ed25519.public_key(bytes(range(32)))
    sig = ed25519.sign(sk, b"SHA256SUMS")
    assert ed25519.verify(pk, b"SHA256SUMS", sig)
    assert not ed25519.verify(pk, b"SHA256SUMs", sig)
    assert not ed25519.verify(pk, b"SHA256SUMS", sig[:-1] + bytes([sig[-1] ^ 1]))
    assert not ed25519.verify(pk, b"SHA256SUMS", b"")
