"""Settings sync for the app's supporters: encrypted bytes kept per slot.

The app (dopeiptv/core/sync.py on the testing branch) encrypts what it syncs
on the person's own computer, with a key made from a random code that only
their computers know, and sends the result here under a "slot": 64 hex
characters, another hash of that same code. This service cannot read a
byte of it, and cannot tell from the slot whose it is.

Each request carries the person's supporter key (X-Dopeiptv-Supporter):
donate.py checks the signature (supporter.check) and forgets it. Nothing
keeps the key, or ties it to a slot, or logs either; nginx does not log
/sync/ at all. What is kept, per slot, is the ciphertext, a version number
and when it was written. A slot nobody has read or written for KEEP_DAYS
goes (cleanup, once a day).

    GET    /sync/<slot>   200 {"version", "t", "blob"} | 404
    PUT    /sync/<slot>   {"version_seen": n, "blob": base64}
                          -> 200 {"version", "t"}
                          | 409 {"version", "t", "blob"?} - another computer
                            wrote since version n: the app merges and tries
                            again
                          | 413 more than MAX_BLOB
    DELETE /sync/<slot>   200 {"deleted": true | false}

Stored as <STATS_DIR>/sync/<slot>.json, mode 600, written whole (a temporary
file and a rename), one lock per slot (64 striped locks: a slot's readers
and writers wait for each other, different slots mostly do not).

    python3 sync.py --cleanup [stats dir]   removes the slots past KEEP_DAYS
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import secrets
import sys
import threading
import time

HEADER = "X-Dopeiptv-Supporter"
SLOT = re.compile(r"^[0-9a-f]{64}$")
#: The most ciphertext one slot keeps (the app refuses more before sending).
MAX_BLOB = 2 * 1024 * 1024
#: A PUT's body: the blob in base64 and a little JSON around it.
MAX_BODY = (MAX_BLOB + 2) // 3 * 4 + 4096
#: The smallest blob the app makes: a format byte, a nonce and a tag.
MIN_BLOB = 1 + 12 + 16
KEEP_DAYS = 400
#: A read moves a slot's time on, at most once a day (not a write per read).
TOUCH_SECS = 86400
_LOCKS = [threading.Lock() for _ in range(64)]


def _lock(slot: str) -> threading.Lock:
    return _LOCKS[int(slot[:4], 16) % len(_LOCKS)]


def folder(stats_dir: str) -> str:
    return os.path.join(stats_dir, "sync")


def _path(stats_dir: str, slot: str) -> str:
    if not SLOT.match(slot or ""):
        raise ValueError("not a slot")
    return os.path.join(folder(stats_dir), slot + ".json")


def _read(path: str) -> dict | None:
    try:
        with open(path, encoding="ascii") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("version"), int) \
            or not isinstance(data.get("blob"), str):
        return None
    return data


def _write(path: str, data: dict) -> None:
    where = os.path.dirname(path)
    os.makedirs(where, mode=0o700, exist_ok=True)
    tmp = os.path.join(where, f".{os.path.basename(path)}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            json.dump(data, fh, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def blob_size(blob: object) -> int:
    """The decoded size of a base64 blob; -1 for anything that is not one."""
    if not isinstance(blob, str) or len(blob) > MAX_BODY:
        return -1 if not isinstance(blob, str) else MAX_BLOB + 1
    try:
        return len(base64.b64decode(blob.encode("ascii"), validate=True))
    except (binascii.Error, ValueError, UnicodeEncodeError):
        return -1


def get(stats_dir: str, slot: str) -> dict | None:
    """{"version", "t", "blob"} kept under *slot*, or None."""
    path = _path(stats_dir, slot)
    with _lock(slot):
        data = _read(path)
        if data is None:
            return None
        try:
            if time.time() - os.path.getmtime(path) > TOUCH_SECS:
                os.utime(path)
        except OSError:
            pass
    return {"version": data["version"], "t": int(data.get("t") or 0), "blob": data["blob"]}


def put(stats_dir: str, slot: str, version_seen: object, blob: object) -> tuple[int, dict]:
    """Keep *blob* as the next version of *slot* - if *version_seen* is the
    version kept now (0: none yet). (status, body to answer)."""
    path = _path(stats_dir, slot)
    if not isinstance(version_seen, int) or isinstance(version_seen, bool) or version_seen < 0:
        return 400, {"error": "version_seen"}
    size = blob_size(blob)
    if size > MAX_BLOB:
        return 413, {"error": "too big", "most": MAX_BLOB}
    if size < MIN_BLOB:
        return 400, {"error": "blob"}
    with _lock(slot):
        cur = _read(path)
        version = cur["version"] if cur else 0
        if version_seen != version:
            body: dict = {"error": "conflict", "version": version,
                          "t": int(cur.get("t") or 0) if cur else 0}
            if cur:
                body["blob"] = cur["blob"]
            return 409, body
        now = int(time.time())
        _write(path, {"version": version + 1, "t": now, "blob": blob})
    return 200, {"version": version + 1, "t": now}


def delete(stats_dir: str, slot: str) -> bool:
    path = _path(stats_dir, slot)
    with _lock(slot):
        try:
            os.unlink(path)
            return True
        except FileNotFoundError:
            return False


def cleanup(stats_dir: str, now: float | None = None) -> int:
    """Remove the slots nobody has read or written for KEEP_DAYS (and
    temporary files a crash left). Returns how many slots went."""
    now = time.time() if now is None else now
    where = folder(stats_dir)
    try:
        names = os.listdir(where)
    except FileNotFoundError:
        return 0
    gone = 0
    for name in names:
        path = os.path.join(where, name)
        slot = name[:-5] if name.endswith(".json") else ""
        try:
            age = now - os.path.getmtime(path)
            if SLOT.match(slot) and age > KEEP_DAYS * 86400:
                with _lock(slot):
                    if now - os.path.getmtime(path) > KEEP_DAYS * 86400:
                        os.unlink(path)
                        gone += 1
            elif name.endswith(".tmp") and age > 86400:
                os.unlink(path)
        except OSError:
            continue
    return gone


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--cleanup":
        target = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "stats")
        print(f"{cleanup(target)} slot(s) removed")
    else:
        print(__doc__)
