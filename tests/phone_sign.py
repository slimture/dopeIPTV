"""Requests to the phone remote as its page makes them (core/phone_page.py):
under /r/, an api/ request signed with the key, a picture's address signed
until a while on (core/phone_remote.signature). Tests write their paths the
way the remote's address once was, /<token>/..., and this puts them so."""
from __future__ import annotations

import json
import secrets
import time

from dopeiptv.core import phone_remote


def signed_request(token: str, method: str, path: str, body=None, headers=None):
    """(path, body as bytes or None, headers) for a request to the server
    whose key is *token*."""
    h = dict(headers or {})
    data = body
    if body is not None and not isinstance(body, bytes):
        data = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    root = f"/{phone_remote.PREFIX}/"
    if path.startswith(f"/{token}/"):
        path = root + path[len(token) + 2:]
    elif path == f"/{token}":
        path = root[:-1]
    if path.startswith(root):
        rest = path[len(root):]
        if rest.startswith("api/"):
            stamp, nonce = str(int(time.time() * 1000)), secrets.token_hex(12)
            h.update({"X-Remote-Time": stamp, "X-Remote-Nonce": nonce,
                      "X-Remote-Sign": phone_remote.signature(token, method, path, stamp, nonce,
                                                              data or b"")})
        elif rest.startswith(("logo/", "thumb/")):
            base = path.split("?", 1)[0]
            until = str(int(time.time() * 1000) + 600_000)
            path = f"{base}?x={until}&s={phone_remote.signature(token, 'GET', base, until, 'picture')}"
    return path, data, h
