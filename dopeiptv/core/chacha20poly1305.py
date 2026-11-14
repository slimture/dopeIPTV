"""ChaCha20-Poly1305 (RFC 8439), in plain Python.

Settings sync (core/sync.py) encrypts what it sends to dopeiptv.org with
it, so the site keeps bytes it cannot read. Plain Python, as core/ed25519.py
is: the frozen app ships no cryptography library, and what is synced is
small (tens to a few hundred KB).

ChaCha20 here works on many 64-byte blocks at once: each of the sixteen
state words of up to ``_LANES`` blocks sits in one Python int, a 64-bit
lane per block (the word in its low 32 bits, the high ones a guard for the
carry of an addition, masked off after each step). One operation on those
ints is then one operation on every block, done in C - about a hundred
times faster than a block at a time. Poly1305 is Python's own big ints.

Nothing here runs in constant time; it encrypts the person's own data on
their own computer, where there is nobody to time it.
"""
from __future__ import annotations

import hmac
import struct

_M32 = 0xFFFFFFFF
_SIGMA = (0x61707865, 0x3320646E, 0x79622D32, 0x6B206574)
_LANES = 256
#: 1 in the lowest bit of every lane, and each lane's own number in it.
_ONES = sum(1 << (64 * i) for i in range(_LANES))
_IOTA = sum(i << (64 * i) for i in range(_LANES))
_P1305 = (1 << 130) - 5
_CLAMP = 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF


def _quarter(x: list[int], ia: int, ib: int, ic: int, id_: int,
             mask: int) -> None:
    """The quarter round (2.1) on four words of every lane at once. A
    rotation is a shift each way, one OR and one mask: what one lane shifts
    out lands above bit 31 of its own lane or the one below, and the mask
    drops it."""
    a, b, c, d = x[ia], x[ib], x[ic], x[id_]
    a = (a + b) & mask
    d ^= a
    d = ((d << 16) | (d >> 16)) & mask
    c = (c + d) & mask
    b ^= c
    b = ((b << 12) | (b >> 20)) & mask
    a = (a + b) & mask
    d ^= a
    d = ((d << 8) | (d >> 24)) & mask
    c = (c + d) & mask
    b ^= c
    b = ((b << 7) | (b >> 25)) & mask
    x[ia], x[ib], x[ic], x[id_] = a, b, c, d


def _keystream(key: bytes, nonce: bytes, counter: int, blocks: int) -> bytes:
    """*blocks* blocks of ChaCha20's key stream from block *counter* on."""
    if len(key) != 32 or len(nonce) != 12:
        raise ValueError("ChaCha20 takes a 32-byte key and a 12-byte nonce")
    if counter < 0 or counter + blocks > 1 << 32:
        raise ValueError("ChaCha20's block counter would wrap")
    k = struct.unpack("<8I", key)
    n = struct.unpack("<3I", nonce)
    out = bytearray(64 * blocks)
    done = 0
    while done < blocks:
        lanes = min(_LANES, blocks - done)
        ones = _ONES & ((1 << (64 * lanes)) - 1)
        mask = ones * _M32
        start = ((counter + done) * ones) + (_IOTA & ((1 << (64 * lanes)) - 1))
        init = [c * ones for c in _SIGMA] + [w * ones for w in k] \
            + [start] + [w * ones for w in n]
        x = list(init)
        for _ in range(10):
            _quarter(x, 0, 4, 8, 12, mask)      # columns
            _quarter(x, 1, 5, 9, 13, mask)
            _quarter(x, 2, 6, 10, 14, mask)
            _quarter(x, 3, 7, 11, 15, mask)
            _quarter(x, 0, 5, 10, 15, mask)     # diagonals
            _quarter(x, 1, 6, 11, 12, mask)
            _quarter(x, 2, 7, 8, 13, mask)
            _quarter(x, 3, 4, 9, 14, mask)
        base = 64 * done
        span = 64 * lanes
        for w in range(16):
            # Lane i of word w is block i's word w: 4 bytes at 64*i + 4*w.
            raw = ((x[w] + init[w]) & mask).to_bytes(8 * lanes, "little")
            for j in range(4):
                out[base + 4 * w + j:base + span:64] = raw[j::8]
        done += lanes
    return bytes(out)


def chacha20(key: bytes, nonce: bytes, counter: int, data: bytes) -> bytes:
    """*data* XOR the key stream from block *counter* (encrypt = decrypt)."""
    if not data:
        return b""
    stream = _keystream(key, nonce, counter, (len(data) + 63) // 64)
    n = len(data)
    return (int.from_bytes(data, "little")
            ^ int.from_bytes(stream[:n], "little")).to_bytes(n, "little")


def block(key: bytes, nonce: bytes, counter: int) -> bytes:
    """One 64-byte block of the key stream (RFC 8439, 2.3)."""
    return _keystream(key, nonce, counter, 1)


def poly1305(key: bytes, msg: bytes) -> bytes:
    """The 16-byte Poly1305 tag of *msg* under the one-time *key* (2.5)."""
    if len(key) != 32:
        raise ValueError("Poly1305 takes a 32-byte key")
    r = int.from_bytes(key[:16], "little") & _CLAMP
    s = int.from_bytes(key[16:], "little")
    acc = 0
    p = _P1305
    full = len(msg) - len(msg) % 16
    pad = 1 << 128
    for i in range(0, full, 16):
        acc = (acc + int.from_bytes(msg[i:i + 16], "little") + pad) * r % p
    if full < len(msg):
        rest = msg[full:]
        acc = (acc + int.from_bytes(rest, "little") + (1 << (8 * len(rest)))) * r % p
    return ((acc + s) & ((1 << 128) - 1)).to_bytes(16, "little")


def _pad16(n: int) -> bytes:
    return b"\0" * (-n % 16)


def _tag(key: bytes, nonce: bytes, aad: bytes, ct: bytes) -> bytes:
    otk = block(key, nonce, 0)[:32]
    mac = b"".join((aad, _pad16(len(aad)), ct, _pad16(len(ct)),
                    struct.pack("<QQ", len(aad), len(ct))))
    return poly1305(otk, mac)


def encrypt(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    """The ciphertext with its 16-byte tag after it (2.8)."""
    ct = chacha20(key, nonce, 1, plaintext)
    return ct + _tag(key, nonce, aad, ct)


def decrypt(key: bytes, nonce: bytes, data: bytes, aad: bytes = b"") -> bytes:
    """The plaintext; ValueError when the tag does not match (a changed
    byte, the wrong key or nonce or aad)."""
    if len(data) < 16:
        raise ValueError("too short for a Poly1305 tag")
    ct, tag = data[:-16], data[-16:]
    if not hmac.compare_digest(_tag(key, nonce, aad, ct), tag):
        raise ValueError("the data does not check out")
    return chacha20(key, nonce, 1, ct)
