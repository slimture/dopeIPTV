"""Settings sync for everyone who uses the app: encrypted bytes kept per slot.

The app (dopeiptv/core/sync.py on the testing branch) encrypts what it syncs
on the person's own computer, with a key made from a random code that only
their computers know, and sends the result here under a "slot": 64 hex
characters, another hash of that same code. This service cannot read a
byte of it, and cannot tell from the slot whose it is.

A request carries nothing about the person: no account, no name, no key
(an older app still sends its supporter key in X-Dopeiptv-Supporter; it is
not read). It does carry the slot's credential (X-Sync-Auth, 64 hex,
another value from the same code): the first write with one binds the
slot to its SHA-256, which is all that is kept of it, and from then on a
request without it - GET, PUT or DELETE - is answered 403. A slot alone
opens nothing. A slot only an older app has written (it sends no
credential) is read and written as before until a newer app writes it. Nothing ties a slot to anyone or logs one; nginx does not log
/sync/ at all. What is kept, per slot, is the ciphertext, a version number
and when it was written. A slot nobody has read or written for KEEP_DAYS
goes (cleanup, once a day).

Since anyone may store, there are limits: MAX_BLOB per slot, a number of
requests and of writes per address (donate.py, in memory only), and
MAX_TOTAL for all slots together - past it a new slot is refused (507)
while the slots there can still be written. Past WARN_SHARE of it the
admin page says so and the owner's devices are told (donate.py).

    every request          403 - the slot is bound and X-Sync-Auth is not
                            its credential
    GET    /sync/<slot>   200 {"version", "t", "blob"} | 404
    PUT    /sync/<slot>   {"version_seen": n, "blob": base64}
                          -> 200 {"version", "t"}
                          | 409 {"version", "t", "blob"?} - another computer
                            wrote since version n: the app merges and tries
                            again
                          | 413 more than MAX_BLOB
                          | 507 a new slot while all of them keep MAX_TOTAL,
                            or one that grows past GROW_SHARE of it
    DELETE /sync/<slot>   200 {"deleted": true | false}

Stored as <STATS_DIR>/sync/<slot>.json, mode 600, written whole (a temporary
file and a rename), one lock per slot (64 striped locks: a slot's readers
and writers wait for each other, different slots mostly do not).

    python3 sync.py --cleanup [stats dir]   removes the slots past KEEP_DAYS
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time

SLOT = re.compile(r"^[0-9a-f]{64}$")
#: A slot's credential as a request carries it (X-Sync-Auth).
AUTH = re.compile(r"^[0-9a-f]{64}$")
#: The most ciphertext one slot keeps (the app refuses more before sending).
MAX_BLOB = 2 * 1024 * 1024
#: A PUT's body: the blob in base64 and a little JSON around it.
MAX_BODY = (MAX_BLOB + 2) // 3 * 4 + 4096
#: The smallest blob the app makes: a format byte, a nonce and a tag.
MIN_BLOB = 1 + 12 + 16
#: A slot nobody has read or written for this long goes (the privacy policy).
KEEP_DAYS = 180
#: All slots together, on disk: past it a new slot is refused (507).
MAX_TOTAL = 5 * 1024 ** 3
#: A slot there already may still grow past MAX_TOTAL, but not past this:
#: without it, slots made small while there was room could each be written
#: up to MAX_BLOB afterwards, and the disk had no limit at all.
#: (As a share of MAX_TOTAL.)
GROW_SHARE = 1.5
#: Past this share of MAX_TOTAL the admin page and the owner's devices are told.
WARN_SHARE = 0.8
#: How long a count of the folder holds before it is made again (put and
#: delete keep it up to date in between).
COUNT_SECS = 3600
#: A read moves a slot's time on, at most once a day (not a write per read).
TOUCH_SECS = 86400
_LOCKS = [threading.Lock() for _ in range(64)]
#: Per statistics folder: [bytes, slots, when counted] - the slots' files.
_USAGE: dict[str, list] = {}
_USAGE_LOCK = threading.Lock()


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


def _size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _count(stats_dir: str) -> list:
    """[bytes, slots] the slot files in the folder take, counted now."""
    total = slots = 0
    try:
        names = os.listdir(folder(stats_dir))
    except FileNotFoundError:
        names = []
    for name in names:
        if name.endswith(".json") and SLOT.match(name[:-5]):
            total += _size(os.path.join(folder(stats_dir), name))
            slots += 1
    return [total, slots]


def _changed(stats_dir: str, by_bytes: int, by_slots: int) -> None:
    with _USAGE_LOCK:
        known = _USAGE.get(stats_dir)
        if known is not None:
            known[0] = max(0, known[0] + by_bytes)
            known[1] = max(0, known[1] + by_slots)


def usage(stats_dir: str, recount: bool = False) -> dict:
    """What all the slots keep together: {"bytes", "slots", "most", "share",
    "warn", "full"}. Counted from the folder at most every COUNT_SECS (or
    when *recount*), kept up to date by put and delete in between."""
    now = time.monotonic()
    with _USAGE_LOCK:
        known = _USAGE.get(stats_dir)
        fresh = known is not None and not recount and now - known[2] < COUNT_SECS
    if not fresh:
        total, slots = _count(stats_dir)
        with _USAGE_LOCK:
            known = _USAGE[stats_dir] = [total, slots, now]
    with _USAGE_LOCK:
        total, slots = known[0], known[1]
    share = total / MAX_TOTAL if MAX_TOTAL else 1.0
    return {"bytes": total, "slots": slots, "most": MAX_TOTAL, "share": round(share, 4),
            "warn": share >= WARN_SHARE, "full": total >= MAX_TOTAL}


class Refused(Exception):
    """The slot is bound to a credential and the request did not carry it."""


def _seal(auth: str) -> str:
    """What is kept of a credential: its SHA-256."""
    return hashlib.sha256(auth.encode("ascii")).hexdigest()


def _check(data: dict | None, auth: str) -> None:
    """Refused unless the slot is unbound or *auth* is its credential."""
    kept = data.get("auth") if data else None
    if kept and not (auth and hmac.compare_digest(_seal(auth), str(kept))):
        raise Refused()


def exists(stats_dir: str, slot: str) -> bool:
    return os.path.exists(_path(stats_dir, slot))


def get(stats_dir: str, slot: str, auth: str = "") -> dict | None:
    """{"version", "t", "blob"} kept under *slot*, or None. Refused when
    the slot is bound and *auth* is not its credential."""
    path = _path(stats_dir, slot)
    with _lock(slot):
        data = _read(path)
        if data is None:
            return None
        _check(data, auth)
        try:
            if time.time() - os.path.getmtime(path) > TOUCH_SECS:
                os.utime(path)
        except OSError:
            pass
    return {"version": data["version"], "t": int(data.get("t") or 0), "blob": data["blob"]}


def put(stats_dir: str, slot: str, version_seen: object, blob: object,
        auth: str = "") -> tuple[int, dict]:
    """Keep *blob* as the next version of *slot* - if *version_seen* is the
    version kept now (0: none yet). A new slot is refused while all the
    slots keep MAX_TOTAL; one there already can still be written. A bound
    slot takes only its credential (403, before anything of it is said);
    an unbound one is bound to *auth* when one comes. (status, body to
    answer)."""
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
        try:
            _check(cur, auth)
        except Refused:
            return 403, {"error": "refused"}
        version = cur["version"] if cur else 0
        if version_seen != version:
            body: dict = {"error": "conflict", "version": version,
                          "t": int(cur.get("t") or 0) if cur else 0}
            if cur:
                body["blob"] = cur["blob"]
            return 409, body
        if cur is None and usage(stats_dir)["bytes"] + size > MAX_TOTAL:
            return 507, {"error": "full"}
        before = _size(path) if cur else 0
        grows = len(str(blob)) - len(cur["blob"]) if cur else 0
        if grows > 0 and usage(stats_dir)["bytes"] + grows > MAX_TOTAL * GROW_SHARE:
            # Rewritten no larger it would still be taken: a household that
            # syncs goes on syncing while the space is full.
            return 507, {"error": "full"}
        now = int(time.time())
        kept = {"version": version + 1, "t": now, "blob": blob}
        bound = (cur or {}).get("auth") or (_seal(auth) if auth else "")
        if bound:
            kept["auth"] = bound
        _write(path, kept)
        _changed(stats_dir, _size(path) - before, 0 if cur else 1)
    return 200, {"version": version + 1, "t": now}


def delete(stats_dir: str, slot: str, auth: str = "") -> bool:
    """Remove *slot*. Refused when it is bound and *auth* is not its
    credential."""
    path = _path(stats_dir, slot)
    with _lock(slot):
        _check(_read(path), auth)
        size = _size(path)
        try:
            os.unlink(path)
        except FileNotFoundError:
            return False
        _changed(stats_dir, -size, -1)
        return True


def cleanup(stats_dir: str, now: float | None = None) -> int:
    """Remove the slots nobody has read or written for KEEP_DAYS (and
    temporary files a crash left), then count what is left (usage).
    Returns how many slots went."""
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
    usage(stats_dir, recount=True)
    return gone


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--cleanup":
        target = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "stats")
        print(f"{cleanup(target)} slot(s) removed")
        used = usage(target)
        print(f"{used['slots']} slot(s) kept, {used['bytes']} bytes, "
              f"{used['share']:.1%} of {MAX_TOTAL} bytes")
    else:
        print(__doc__)
