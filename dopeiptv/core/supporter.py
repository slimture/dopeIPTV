"""The supporter key: a thank-you for a donation.

dopeiptv.org signs it with a key of its own (Ed25519, donate/supporter.py
on the website branch) when a donation goes through, and the app checks
the signature here, offline - nothing is asked of a server, and nothing
about the person is in the key: a version, the day it was made and a
random id. It shows a supporter mark in About and opens the supporter
themes.

dopeIPTV is GPL like before: anyone building it can change this check.
It is a thank-you in the app's own builds, not a lock - and it holds
nothing back but a mark and a few colours.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import secrets
import threading
from collections import OrderedDict
from typing import Any

from .ed25519 import public_key, sign, verify

#: The public half of dopeiptv.org's supporter key (32 bytes, hex). Empty
#: until the site has one: then no key checks out.
PUBLIC_KEY_HEX = "65c72e1f1ac5df41e6f268aad6dacbf690f78679f61fa8321488b62637b98826"
PREFIX = "DS1-"
_DOMAIN = b"dopeIPTV supporter key v1\x00"
_PAYLOAD = 11                  # version (1) + day (4) + id (6)


def _pub(public: bytes | None) -> bytes | None:
    if public is not None:
        return public
    try:
        raw = bytes.fromhex(PUBLIC_KEY_HEX)
    except ValueError:
        return None
    return raw if len(raw) == 32 else None


def make(seed: bytes, day: int | None = None, ident: bytes | None = None) -> str:
    """A key signed with *seed* (the site does this; tests too)."""
    day = int(day if day is not None else dt.date.today().toordinal())
    ident = ident if ident is not None else secrets.token_bytes(6)
    payload = bytes([1]) + day.to_bytes(4, "big") + ident[:6].ljust(6, b"\0")
    sig = sign(seed, _DOMAIN + payload)
    return PREFIX + base64.urlsafe_b64encode(payload + sig).decode("ascii").rstrip("=")


def parse(key: str, public: bytes | None = None) -> dict[str, Any] | None:
    """{"since": date, "id": hex} for a key that checks out, else None."""
    pub = _pub(public)
    text = "".join((key or "").split())
    if pub is None or not text.upper().startswith(PREFIX):
        return None
    body = text[len(PREFIX):]
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except (binascii.Error, ValueError):
        return None
    if len(raw) != _PAYLOAD + 64 or raw[0] != 1:
        return None
    payload, sig = raw[:_PAYLOAD], raw[_PAYLOAD:]
    try:
        ok = verify(pub, _DOMAIN + payload, sig)
    except Exception:
        ok = False
    if not ok:
        return None
    day = int.from_bytes(payload[1:5], "big")
    try:
        since = dt.date.fromordinal(day)
    except (ValueError, OverflowError):
        return None
    return {"since": since, "id": payload[5:].hex()}


#: Keys checked lately and what came of it: the check is a pure-Python
#: Ed25519 verify (some milliseconds), and the window asks several times a
#: minute on its own thread - the themes, About, the sidebar.
_SEEN_MAX = 4
_seen: OrderedDict[tuple[str, str], dict[str, Any] | None] = OrderedDict()
_seen_lock = threading.Lock()


def stored(settings: Any) -> dict[str, Any] | None:
    try:
        key = str(settings.value("supporter_key", "") or "")
    except Exception:
        return None
    if not key:
        return None
    seen = (PUBLIC_KEY_HEX, key)
    with _seen_lock:
        if seen in _seen:
            _seen.move_to_end(seen)
            hit = _seen[seen]
            return dict(hit) if hit is not None else None
    got = parse(key)
    with _seen_lock:
        _seen[seen] = got
        while len(_seen) > _SEEN_MAX:
            _seen.popitem(last=False)
    return dict(got) if got is not None else None


def is_supporter(settings: Any) -> bool:
    return stored(settings) is not None


def new_seed() -> tuple[bytes, bytes]:
    """(seed, public) for a new supporter key pair (the site makes its own)."""
    seed = secrets.token_bytes(32)
    return seed, public_key(seed)
