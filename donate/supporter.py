#!/usr/bin/env python3
"""Supporter keys: the app's thank-you for a donation, signed here.

A key is "DS1-" and, in base64url, a version, the day of the donation and
a six-byte id, then an Ed25519 signature over them (the app checks it
offline: dopeiptv/core/supporter.py on the testing branch, with this key's
public half in PUBLIC_KEY_HEX). Nothing about the person is in it.

The id is an HMAC of the PaymentIntent's id under the signing key: the same
donation always gives the same key (a reload, a second try), and without
the signing key nobody can tell which payment a key came from. Nothing is
stored about the payment; supporters.jsonl beside the statistics counts
the keys handed out (id and time).

    python3 supporter.py --new-key [path]
        makes the signing key (32 random bytes, mode 0600; default
        /usr/local/etc/iptv-donate-supporter.key, never overwritten) and
        prints its PUBLIC half - the one line the app needs. The file
        itself stays on this machine and is never sent anywhere.
    python3 supporter.py --public [path]
        prints the public half again.
    python3 supporter.py --check KEY [path]
        says whether KEY checks out against this signing key.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time

import ed25519

DEFAULT_PATH = "/usr/local/etc/iptv-donate-supporter.key"
PREFIX = "DS1-"
_DOMAIN = b"dopeIPTV supporter key v1\x00"
_LOCK = threading.Lock()


def load_seed(path: str = DEFAULT_PATH) -> bytes | None:
    try:
        with open(path, "rb") as fh:
            raw = fh.read().strip()
    except OSError:
        return None
    try:
        seed = bytes.fromhex(raw.decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        return None
    return seed if len(seed) == 32 else None


def _fast():
    """cryptography's Ed25519 (constant-time; Web Push needs the package
    anyway), or None: not installed, or broken - a broken one can fail with
    a Rust panic, which is not an Exception."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        return Ed25519PrivateKey
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return None


_FAST = _fast()


def _sign(seed: bytes, message: bytes) -> bytes:
    if _FAST is not None:
        return _FAST.from_private_bytes(seed).sign(message)
    return ed25519.sign(seed, message)


def make(seed: bytes, day: int, ident: bytes) -> str:
    payload = bytes([1]) + int(day).to_bytes(4, "big") + ident[:6].ljust(6, b"\0")
    sig = _sign(seed, _DOMAIN + payload)
    return PREFIX + base64.urlsafe_b64encode(payload + sig).decode("ascii").rstrip("=")


def for_payment(seed: bytes, payment_id: str, created: int) -> tuple[str, str]:
    """(key, id hex) for one donation: always the same for the same payment."""
    ident = hmac.new(seed, b"supporter id\0" + payment_id.encode("ascii"), hashlib.sha256).digest()[:6]
    day = dt.datetime.fromtimestamp(int(created), dt.timezone.utc).date().toordinal()
    return make(seed, day, ident), ident.hex()


def made_by_hand(seed: bytes) -> tuple[str, str]:
    """A key for someone who gave another way (an admin makes it)."""
    ident = secrets.token_bytes(6)
    return make(seed, dt.date.today().toordinal(), ident), ident.hex()


def check(key: str, public: bytes) -> dict | None:
    text = "".join((key or "").split())
    if not text.upper().startswith(PREFIX):
        return None
    body = text[len(PREFIX):]
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except ValueError:
        return None
    if len(raw) != 75 or raw[0] != 1 or not ed25519.verify(public, _DOMAIN + raw[:11], raw[11:]):
        return None
    return {"since": dt.date.fromordinal(int.from_bytes(raw[1:5], "big")).isoformat(), "id": raw[5:11].hex()}


def record(stats_dir: str, ident: str, how: str) -> None:
    """Counts a key handed out, once per id (no payment, no person)."""
    path = os.path.join(stats_dir, "supporters.jsonl")
    with _LOCK:
        try:
            with open(path, encoding="utf-8") as fh:
                if any(json.loads(line).get("id") == ident for line in fh if line.strip()):
                    return
        except (OSError, ValueError):
            pass
        os.makedirs(stats_dir, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": ident, "t": int(time.time()), "how": how}) + "\n")


def handed_out(stats_dir: str) -> int:
    try:
        with open(os.path.join(stats_dir, "supporters.jsonl"), encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())
    except OSError:
        return 0


def _new_key(path: str) -> int:
    if os.path.exists(path):
        print(f"{path} already exists - not overwritten. Its public half:", file=sys.stderr)
        return _public(path)
    seed = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(seed.hex() + "\n")
    print("Signing key written to", path, "(mode 0600; keep it on this machine, never send it).",
          file=sys.stderr)
    print("Its PUBLIC half, for the app (safe to share):", file=sys.stderr)
    print(ed25519.public_key(seed).hex())
    return 0


def _public(path: str) -> int:
    seed = load_seed(path)
    if seed is None:
        print(f"no signing key in {path}", file=sys.stderr)
        return 1
    print(ed25519.public_key(seed).hex())
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "--new-key":
        return _new_key(argv[2] if len(argv) > 2 else DEFAULT_PATH)
    if len(argv) >= 2 and argv[1] == "--public":
        return _public(argv[2] if len(argv) > 2 else DEFAULT_PATH)
    if len(argv) >= 3 and argv[1] == "--check":
        seed = load_seed(argv[3] if len(argv) > 3 else DEFAULT_PATH)
        if seed is None:
            print("no signing key", file=sys.stderr)
            return 1
        got = check(argv[2], ed25519.public_key(seed))
        print(json.dumps(got) if got else "not a valid key")
        return 0 if got else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
