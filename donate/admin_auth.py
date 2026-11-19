"""admin_auth.py - who may open the admin page, and how they sign in.

Several admins, each signing in with a passkey (WebAuthn: Face ID, Touch ID,
a phone or a security key), or with a password and - if they turned it on -
a code from an authenticator app (TOTP, RFC 6238). A new admin comes in
through a one-time invite link another admin makes. A signed cookie keeps a
browser signed in (400 days, renewed by each visit). Each sign-in is a
session of its own, named in its cookie: signing out ends that session on
the server - a copy of the cookie made before is worth nothing after - and
"sign out everywhere" ends all of one admin's sessions at once. An admin
removed and invited again under the same name starts afresh: no cookie
from before fits.

Kept in <STATS_DIR>/admin-users.json (mode 600): password hashes (scrypt),
the passkeys' public keys, authenticator secrets, invites as hashes of their
tokens. Nothing of it leaves the server. Passkeys are checked here (CBOR and
COSE read by hand, signatures with Python's cryptography package, which the
service already needs for its push notifications); no attestation is asked
for, so any passkey provider works.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import struct
import threading
import time
from urllib.parse import quote, urlsplit

SESSION_SECS = 400 * 86400
INVITE_SECS = 7 * 86400
CHALLENGE_SECS = 300
MIN_PASSWORD = 10
FAIL_WINDOW = 15 * 60
FAIL_LIMIT = 10
USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
#: A session's own name in its cookie: random, the same through every renewal.
SID_RE = re.compile(r"^[0-9a-f]{16}$")
#: scrypt as a new password is hashed: N = 2^15 (32 MB), r = 8, p = 3 - one
#: of the settings OWASP's password storage guidance gives as equal. A hash
#: made with less (p = 1, until November 2026) is checked as it was made and
#: made again at that admin's next sign-in with the password.
SCRYPT = (15, 8, 3)
#: A hash takes 32 MB while it is made: at most this many at once, the rest
#: wait their turn - a burst of sign-in tries cannot take the memory.
_HASHING = threading.BoundedSemaphore(4)
#: What a guesser can make the service remember, at the most.
MAX_FAIL_KEYS = 20000
MAX_CHALLENGES = 5000
#: Sessions ended by signing out, remembered per admin until they would
#: have run out by themselves.
MAX_ENDED = 2000

_LOCK = threading.RLock()
_CFG: dict = {"path": "", "origin": "", "key": b""}
_challenges: dict = {}        # challenge -> (until, purpose, subject)
_tickets: dict = {}           # second-step ticket -> (until, user)
_pending_totp: dict = {}      # user -> (until, secret)
_fails: dict = {}             # address or user -> [times]
_SEEN: dict = {"stamp": None, "data": None}     # the admins as last read, for the checks every request makes


class AuthError(Exception):
    """Refused, with a reason fit to show; *why* says more, for the
    service's log only."""

    def __init__(self, msg: str, why: str = "") -> None:
        super().__init__(msg)
        self.why = why


def configure(stats_dir: str, origin: str, key: bytes) -> None:
    if len(key) < 32:
        # Never a short or empty key: a cookie signed with one is anyone's to make.
        raise ValueError("the session key is too short")
    _CFG.update(path=os.path.join(stats_dir, "admin-users.json"), origin=origin.rstrip("/"), key=key)
    _SEEN.update(stamp=None, data=None)


def rp_id() -> str:
    return urlsplit(_CFG["origin"]).hostname or "localhost"


# -- small pieces ------------------------------------------------------------------
def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    if not isinstance(s, str) or not re.fullmatch(r"[A-Za-z0-9_-]*", s):
        raise AuthError("bad encoding")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _now() -> int:
    return int(time.time())


# -- the store ---------------------------------------------------------------------
def _load() -> dict:
    try:
        with open(_CFG["path"], encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            data.setdefault("users", {})
            data.setdefault("invites", {})
            return data
    except FileNotFoundError:
        pass
    return {"users": {}, "invites": {}}


def _save(data: dict) -> None:
    path = _CFG["path"]
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)
    with _LOCK:
        _SEEN.update(stamp=None, data=None)


def _users() -> dict:
    """The admins, to look at only (never changed in place): read again
    when the file is another one than last time - every request to the
    admin page asks who its cookie names, a made-up cookie too, and none of
    them should cost a read of the file."""
    try:
        st = os.stat(_CFG["path"])
    except FileNotFoundError:
        return {}
    stamp = (_CFG["path"], st.st_ino, st.st_mtime_ns, st.st_size)
    with _LOCK:
        if _SEEN["stamp"] == stamp:
            return _SEEN["data"]
    users = _load()["users"]
    with _LOCK:
        _SEEN.update(stamp=stamp, data=users)
    return users


def has_users() -> bool:
    return bool(_users())


def is_admin(user: str) -> bool:
    return bool(user) and user in _users()


# -- passwords ---------------------------------------------------------------------
def hash_password(password: str, how: tuple = SCRYPT) -> str:
    logn, r, p = how
    salt = secrets.token_bytes(16)
    with _HASHING:
        digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** logn, r=r, p=p,
                                maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt${logn}${r}${p}${b64u(salt)}${b64u(digest)}"


def _how(stored: str) -> tuple | None:
    """(log2 N, r, p) a stored hash was made with, or None for none of ours."""
    try:
        kind, logn, r, p, _salt, _digest = stored.split("$")
        how = (int(logn), int(r), int(p))
    except (ValueError, AttributeError):
        return None
    return how if kind == "scrypt" and 10 <= how[0] <= 20 and 1 <= how[1] <= 32 and 1 <= how[2] <= 16 else None


def _work(how: tuple) -> int:
    return 2 ** how[0] * how[1] * how[2]


def check_password(password: str, stored: str) -> bool:
    how = _how(stored)
    if how is None:
        return False
    try:
        _kind, _logn, _r, _p, salt, digest = stored.split("$")
        with _HASHING:
            got = hashlib.scrypt(password.encode(), salt=unb64u(salt), n=2 ** how[0], r=how[1],
                                 p=how[2], maxmem=64 * 1024 * 1024, dklen=32)
        return hmac.compare_digest(got, unb64u(digest))
    except (ValueError, AuthError):
        return False


_DUMMY: dict = {}


def _dummy_hash(users: dict | None = None) -> str:
    """A hash nobody's password fits, made as the admins' own are (the
    lightest of them, while they differ): a name that does not exist takes
    as long to refuse as one that does."""
    made = [h for h in (_how((rec or {}).get("password") or "") for rec in (users or {}).values()) if h]
    how = min(made, key=_work) if made else SCRYPT
    if how not in _DUMMY:
        _DUMMY[how] = hash_password(secrets.token_hex(16), how)
    return _DUMMY[how]


def _good_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise AuthError(f"A password needs at least {MIN_PASSWORD} characters.")
    if len(password) > 256:
        raise AuthError("That password is too long.")


# -- authenticator codes (TOTP, RFC 6238: SHA-1, 6 digits, 30 seconds) -------------
def totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def hotp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    at = mac[-1] & 15
    return f"{(struct.unpack('>I', mac[at:at + 4])[0] & 0x7FFFFFFF) % 1000000:06d}"


def totp_match(secret: str, code: str, last: int, now: float | None = None) -> int | None:
    """The time step *code* belongs to (this one or a neighbour), or None.
    A step at or before *last* - a code already used - never matches."""
    code = re.sub(r"\s", "", str(code or ""))
    if not re.fullmatch(r"\d{6}", code):
        return None
    step = int((time.time() if now is None else now) // 30)
    for c in (step - 1, step, step + 1):
        if c > last and hmac.compare_digest(hotp(secret, c), code):
            return c
    return None


def totp_uri(secret: str, user: str) -> str:
    host = rp_id()
    return (f"otpauth://totp/{quote('dopeIPTV admin')}:{quote(user)}?secret={secret}"
            f"&issuer={quote('dopeIPTV admin')}" + ("" if host == "dopeiptv.org" else f"%20({quote(host)})"))


# -- passkeys: CBOR, COSE keys, signatures --------------------------------------------
def cbor(data: bytes, at: int = 0):
    """One CBOR item from *data* at *at*: (value, the offset after it). The
    subset WebAuthn uses: integers, byte and text strings, arrays, maps,
    true, false and null; definite lengths only."""
    if at >= len(data):
        raise AuthError("cbor: short")
    head = data[at]
    major, info = head >> 5, head & 31
    at += 1
    if info < 24:
        n = info
    elif info in (24, 25, 26, 27):
        size = 1 << (info - 24)
        if at + size > len(data):
            raise AuthError("cbor: short")
        n = int.from_bytes(data[at:at + size], "big")
        at += size
    else:
        raise AuthError("cbor: unsupported")
    if major == 0:
        return n, at
    if major == 1:
        return -1 - n, at
    if major in (2, 3):
        if at + n > len(data):
            raise AuthError("cbor: short")
        raw = data[at:at + n]
        return (raw if major == 2 else raw.decode("utf-8")), at + n
    if major == 4:
        out = []
        for _ in range(n):
            item, at = cbor(data, at)
            out.append(item)
        return out, at
    if major == 5:
        obj = {}
        for _ in range(n):
            k, at = cbor(data, at)
            v, at = cbor(data, at)
            obj[k] = v
        return obj, at
    if major == 7 and info in (20, 21, 22):
        return {20: False, 21: True, 22: None}[info], at
    raise AuthError("cbor: unsupported")


def auth_data(raw: bytes) -> dict:
    if len(raw) < 37:
        raise AuthError("authenticator data too short")
    out = {"rp": raw[:32], "flags": raw[32], "count": struct.unpack(">I", raw[33:37])[0]}
    if raw[32] & 0x40:                      # attested credential data follows
        if len(raw) < 55:
            raise AuthError("authenticator data too short")
        size = struct.unpack(">H", raw[53:55])[0]
        out["cred"] = raw[55:55 + size]
        out["key"], _ = cbor(raw, 55 + size)
    return out


def cose_key(key: dict) -> dict:
    """A COSE public key as stored: ES256, EdDSA (Ed25519) or RS256."""
    if not isinstance(key, dict):
        raise AuthError("not a key")
    kty, alg = key.get(1), key.get(3)
    if kty == 2 and alg == -7 and key.get(-1) == 1:
        return {"kty": "EC2", "alg": -7, "x": b64u(key[-2]), "y": b64u(key[-3])}
    if kty == 1 and alg == -8 and key.get(-1) == 6:
        return {"kty": "OKP", "alg": -8, "x": b64u(key[-2])}
    if kty == 3 and alg == -257:
        return {"kty": "RSA", "alg": -257, "n": b64u(key[-1]), "e": b64u(key[-2])}
    raise AuthError("This kind of passkey is not supported.")


def verify(key: dict, signature: bytes, signed: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
    try:
        if key["kty"] == "EC2":
            pub = ec.EllipticCurvePublicNumbers(int.from_bytes(unb64u(key["x"]), "big"),
                                                int.from_bytes(unb64u(key["y"]), "big"),
                                                ec.SECP256R1()).public_key()
            pub.verify(signature, signed, ec.ECDSA(hashes.SHA256()))
        elif key["kty"] == "OKP":
            ed25519.Ed25519PublicKey.from_public_bytes(unb64u(key["x"])).verify(signature, signed)
        elif key["kty"] == "RSA":
            pub = rsa.RSAPublicNumbers(int.from_bytes(unb64u(key["e"]), "big"),
                                       int.from_bytes(unb64u(key["n"]), "big")).public_key()
            pub.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
        else:
            return False
        return True
    except (InvalidSignature, ValueError):
        return False


def _challenge(purpose: str, subject: str = "") -> str:
    now = _now()
    with _LOCK:
        for k in [k for k, v in _challenges.items() if v[0] < now]:
            del _challenges[k]
        if len(_challenges) >= MAX_CHALLENGES:
            # Asked for faster than they run out: the oldest go first.
            for k in sorted(_challenges, key=lambda k: _challenges[k][0])[:MAX_CHALLENGES // 4]:
                del _challenges[k]
        ch = b64u(secrets.token_bytes(32))
        _challenges[ch] = (now + CHALLENGE_SECS, purpose, subject)
    return ch


def _client(data_b64: str, kind: str, purpose: str, subject: str = "") -> bytes:
    raw = unb64u(data_b64)
    try:
        client = json.loads(raw)
    except (ValueError, RecursionError):
        raise AuthError("bad client data") from None
    if not isinstance(client, dict):
        raise AuthError("bad client data")
    if client.get("type") != kind or client.get("origin") != _CFG["origin"] or client.get("crossOrigin"):
        raise AuthError("This sign-in came from somewhere else.")
    with _LOCK:
        hit = _challenges.pop(str(client.get("challenge")), None)
    if not hit or hit[0] < _now() or hit[1] != purpose or (subject and hit[2] != subject):
        raise AuthError("That took too long, or was already used. Try again.")
    return raw


def _rp_ok(ad: dict) -> None:
    if not hmac.compare_digest(ad["rp"], hashlib.sha256(rp_id().encode()).digest()):
        raise AuthError("This passkey belongs to another site.")
    if not ad["flags"] & 0x01:
        raise AuthError("The passkey was not confirmed.")


def _creation_options(uid: str, user: str, name: str, exclude: list, purpose: str) -> dict:
    return {
        "challenge": _challenge(purpose, user),
        "rp": {"id": rp_id(), "name": "dopeIPTV admin"},
        "user": {"id": uid, "name": user, "displayName": name or user},
        "pubKeyCredParams": [{"type": "public-key", "alg": -7}, {"type": "public-key", "alg": -8},
                             {"type": "public-key", "alg": -257}],
        "authenticatorSelection": {"residentKey": "required", "requireResidentKey": True,
                                   "userVerification": "preferred"},
        "attestation": "none",
        "timeout": 120000,
        "excludeCredentials": [{"type": "public-key", "id": p["id"]} for p in exclude],
    }


def _read_passkey(read):
    """*read()*, what a passkey sent taken apart: anything in it that is not
    what it should be - bytes that end early, a key without its parts, a
    thing nested without end - is a passkey refused, never an error of the
    service's own."""
    try:
        return read()
    except AuthError:
        raise
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError, RecursionError,
            struct.error):
        raise AuthError("This passkey's answer could not be read.") from None


def _new_passkey(cred: dict, purpose: str, user: str, label: str) -> dict:
    resp = cred.get("response") if isinstance(cred, dict) else None
    if not isinstance(resp, dict):
        raise AuthError("No passkey came back.")
    _client(resp.get("clientDataJSON", ""), "webauthn.create", purpose, user)

    def read() -> tuple:
        att, _ = cbor(unb64u(resp.get("attestationObject", "")))
        if not isinstance(att, dict) or not isinstance(att.get("authData"), bytes):
            raise AuthError("bad attestation")
        ad = auth_data(att["authData"])
        _rp_ok(ad)
        if "cred" not in ad:
            raise AuthError("The passkey sent no key.")
        return ad, b64u(ad["cred"]), cose_key(ad["key"])
    ad, cid, key = _read_passkey(read)
    for other in _load()["users"].values():
        if any(p["id"] == cid for p in other.get("passkeys", [])):
            raise AuthError("This passkey is already added.")
    return {"id": cid, "key": key, "count": ad["count"], "added": _now(), "used": 0,
            "name": (str(label or "").strip() or "Passkey")[:60]}


# -- sessions ----------------------------------------------------------------------
def _sign(*parts) -> str:
    msg = ".".join(str(p) for p in parts).encode()
    return hmac.new(_CFG["key"], msg, hashlib.sha256).hexdigest()


def _sign_session(user: str, until: int, epoch: int, sid: str) -> str:
    # Its own first line: never the same bytes as a cookie from before
    # sessions had names (_sign), whatever the name, time and numbers are.
    msg = "\n".join(("session", user, str(until), str(epoch), sid)).encode()
    return hmac.new(_CFG["key"], msg, hashlib.sha256).hexdigest()


_NUMBER = re.compile(r"^[0-9]{1,20}$")


def _read_cookie(value: str) -> tuple | None:
    """(user, until, epoch, session) of a cookie this service signed - the
    session "" for one from before sessions had names (user.until.epoch.sig,
    until November 2026; it holds until it runs out or its browser's next
    visit gives it a name) - or None. Says nothing of whether it still
    holds (session_user)."""
    value = value if isinstance(value, str) and len(value) <= 300 else ""
    parts = value.rsplit(".", 4)
    if len(parts) == 5 and SID_RE.match(parts[3]):
        user, until, epoch, sid, sig = parts
        if USER_RE.match(user) and _NUMBER.match(until) and _NUMBER.match(epoch) \
                and hmac.compare_digest(sig, _sign_session(user, int(until), int(epoch), sid)):
            return user, int(until), int(epoch), sid
    parts = value.rsplit(".", 3)
    if len(parts) == 4:
        user, until, epoch, sig = parts
        if USER_RE.match(user) and _NUMBER.match(until) and _NUMBER.match(epoch) \
                and hmac.compare_digest(sig, _sign(user, until, epoch)):
            return user, int(until), int(epoch), ""
    return None


def _ended_key(sid: str, value: str) -> str:
    """What a session signed out of is remembered by: its name, or - for a
    cookie from before sessions had one - that very cookie."""
    return sid or "c:" + hashlib.sha256(value.encode()).hexdigest()[:32]


def _holds(rec: dict | None, got: tuple, value: str) -> bool:
    _user, until, epoch, sid = got
    return bool(rec) and until >= _now() and int(rec.get("epoch", 1)) == epoch \
        and _ended_key(sid, value) not in (rec.get("out") or {})


def session_cookie(user: str, carry: str = "") -> str:
    """A session cookie for *user*: a new session - or, with *carry* (the
    cookie this browser came with, still holding), that same session for
    another SESSION_SECS."""
    rec = _users().get(user)
    if not rec:
        raise AuthError("unknown admin")
    had = _read_cookie(carry) if carry else None
    sid = had[3] if had and had[0] == user and had[3] and _holds(rec, had, carry) else secrets.token_hex(8)
    until, epoch = _now() + SESSION_SECS, int(rec.get("epoch", 1))
    return f"{user}.{until}.{epoch}.{sid}.{_sign_session(user, until, epoch, sid)}"


def session_user(value: str) -> str:
    """The admin a session cookie's value names, while it holds, or ""."""
    got = _read_cookie(value)
    if not got:
        return ""
    return got[0] if _holds(_users().get(got[0]), got, value) else ""


def sign_out(value: str) -> bool:
    """The session *value* belongs to, ended on the server: the cookie and
    every renewal of it - a copy someone made before - name nobody from now
    on. Remembered with the admin until the session would have run out by
    itself. False for a cookie that names no session that holds."""
    got = _read_cookie(value)
    if not got:
        return False
    user, until, _epoch, sid = got
    with _LOCK:
        data = _load()
        rec = data["users"].get(user)
        if not _holds(rec, got, value):
            return False
        now = _now()
        out = {k: t for k, t in (rec.get("out") or {}).items() if isinstance(t, int) and t > now}
        # A named session could be renewed until now, so until SESSION_SECS
        # from now; a cookie without a name is only itself.
        out[_ended_key(sid, value)] = now + SESSION_SECS if sid else until
        for k in sorted(out, key=lambda k: out[k])[:max(0, len(out) - MAX_ENDED)]:
            del out[k]
        rec["out"] = out
        _save(data)
    return True


def _new_epoch(but: int = 0) -> int:
    """A number no session of this name has carried: an admin removed and
    invited again, or signed out everywhere, starts where no cookie from
    before fits. (Counting from 1 did not: the same name invited again was
    at 1 again, and a removed admin's cookie was good once more.)"""
    while True:
        n = secrets.randbits(48) + 2
        if n != but:
            return n


# -- guessing --------------------------------------------------------------------------
def _failed(*keys: str) -> None:
    now = time.time()
    with _LOCK:
        if len(_fails) >= MAX_FAIL_KEYS:
            # More guessers than are remembered: the longest quiet go first.
            for k in sorted(_fails, key=lambda k: _fails[k][-1])[:MAX_FAIL_KEYS // 4]:
                del _fails[k]
        for k in keys:
            if k:
                _fails.setdefault(k[:100], []).append(now)


def forget(now: float | None = None) -> None:
    """What signing in keeps in memory, let go once it is no longer needed:
    the addresses and names of failed tries after FAIL_WINDOW (the service
    calls this every minute - before, they stayed until the next try, from
    anyone), and challenges, tickets and half-made authenticators once
    they have run out."""
    now = time.time() if now is None else now
    with _LOCK:
        for k in list(_fails):
            _fails[k] = [t for t in _fails[k] if now - t < FAIL_WINDOW]
            if not _fails[k]:
                del _fails[k]
        for store in (_challenges, _tickets, _pending_totp):
            for k in [k for k, v in store.items() if v[0] < now]:
                del store[k]


def blocked(*keys: str) -> bool:
    now = time.time()
    with _LOCK:
        for k in list(_fails):
            _fails[k] = [t for t in _fails[k] if now - t < FAIL_WINDOW]
            if not _fails[k]:
                del _fails[k]
        return any(len(_fails.get(k[:100], ())) >= FAIL_LIMIT for k in keys if k)


def _guard(addr: str, user: str = "") -> None:
    if blocked("a:" + addr, "u:" + user if user else ""):
        raise AuthError("Too many tries. Wait a quarter of an hour and try again.", "held back after 10 failures")


# -- signing in ------------------------------------------------------------------------
def login_options(addr: str) -> dict:
    # Not held back: a challenge proves nothing, and the passkey's answer is.
    return {"challenge": _challenge("login"), "rpId": rp_id(), "userVerification": "preferred",
            "timeout": 120000, "allowCredentials": []}


def login_passkey(cred: dict, addr: str) -> str:
    """The admin a passkey sign-in proves, or AuthError."""
    _guard(addr)
    try:
        resp = cred.get("response") if isinstance(cred, dict) else None
        if not isinstance(resp, dict):
            raise AuthError("No passkey came back.")
        client = _client(resp.get("clientDataJSON", ""), "webauthn.get", "login")
        raw_ad = unb64u(resp.get("authenticatorData", ""))
        ad = _read_passkey(lambda: auth_data(raw_ad))
        _rp_ok(ad)
        cid = str(cred.get("rawId") or cred.get("id") or "")
        with _LOCK:
            data = _load()
            hit = next(((u, p) for u, rec in data["users"].items() for p in rec.get("passkeys", [])
                        if p["id"] == cid), None)
            if not hit:
                raise AuthError("This passkey is not one of this site's admins. Remove it from the device or add it again.")
            user, pk = hit
            handle = resp.get("userHandle")
            if handle and handle != data["users"][user].get("id"):
                raise AuthError("This passkey does not match its admin.")
            signed = raw_ad + hashlib.sha256(client).digest()
            if not verify(pk["key"], unb64u(resp.get("signature", "")), signed):
                raise AuthError("The passkey's signature did not match.")
            if (ad["count"] or pk.get("count")) and ad["count"] <= pk.get("count", 0):
                raise AuthError("This passkey looks copied; it was refused.")
            pk["count"], pk["used"] = ad["count"], _now()
            _save(data)
        return user
    except AuthError:
        _failed("a:" + addr)
        raise


def _named(name: str, users: dict) -> str:
    """The admin a typed name means: the sign-in name, or else the name
    shown (an invite's "Pontus" for "admin") when only one admin has it."""
    name = " ".join(str(name or "").split()).lower()
    if name in users:
        return name
    hits = [u for u, rec in users.items() if " ".join(str(rec.get("name", "")).split()).lower() == name]
    return hits[0] if len(hits) == 1 else name


def login_password(user: str, password: str, addr: str) -> dict:
    """{"user"} when signed in, or {"ticket"} when an authenticator code is
    needed next; AuthError for anything wrong (one message for both a wrong
    name and a wrong password)."""
    users = _load()["users"]
    user = _named(user, users)
    _guard(addr, user)
    rec = users.get(user)
    password = str(password or "")
    # The same work for a name that does not exist: no faster "no".
    stored = (rec or {}).get("password") or _dummy_hash(users)
    if not check_password(password, stored) or not rec or not rec.get("password"):
        _failed("a:" + addr, "u:" + user)
        why = ("no admin by that name" if not rec else
               f"{user} has no password" if not rec.get("password") else f"wrong password for {user}")
        raise AuthError("That name and password do not match.", why)
    if _work(_how(stored) or SCRYPT) < _work(SCRYPT):
        # Hashed when hashing asked for less: made again as it is asked now,
        # with the password just shown to be right.
        fresh = hash_password(password)
        with _LOCK:
            data = _load()
            cur = data["users"].get(user)
            if cur and cur.get("password") == stored:
                cur["password"] = fresh
                _save(data)
    if rec.get("totp"):
        ticket = secrets.token_urlsafe(24)
        with _LOCK:
            now = _now()
            for k in [k for k, v in _tickets.items() if v[0] < now]:
                del _tickets[k]
            _tickets[ticket] = (now + CHALLENGE_SECS, user)
        return {"ticket": ticket}
    return {"user": user}


def login_code(ticket: str, code: str, addr: str) -> str:
    with _LOCK:
        hit = _tickets.get(str(ticket or ""))
    if not hit or hit[0] < _now():
        raise AuthError("That took too long. Sign in again.")
    user = hit[1]
    _guard(addr, user)
    with _LOCK:
        data = _load()
        rec = data["users"].get(user)
        totp = (rec or {}).get("totp") or {}
        step = totp_match(totp.get("secret", ""), code, int(totp.get("last", 0))) if totp else None
        if step is None:
            _failed("a:" + addr, "u:" + user)
            raise AuthError("That code is not right. Codes change every 30 seconds.", f"wrong code for {user}")
        totp["last"] = step
        _save(data)
        _tickets.pop(str(ticket), None)
    return user


# -- invites -------------------------------------------------------------------------
def _token_key(token: str) -> str:
    return hashlib.sha256(str(token or "").encode()).hexdigest()


def invite(by: str, user: str, name: str) -> tuple:
    """(token, until) for a new admin *user*, made by admin *by*."""
    user = str(user or "").strip().lower()
    if not USER_RE.match(user):
        raise AuthError("A name of 2 to 32 lower-case letters, digits, dots, dashes or underscores.")
    with _LOCK:
        data = _load()
        if user in data["users"]:
            raise AuthError("There is already an admin by that name.")
        now = _now()
        data["invites"] = {k: v for k, v in data["invites"].items() if v["until"] > now and v["user"] != user}
        token = secrets.token_urlsafe(24)
        until = now + INVITE_SECS
        data["invites"][_token_key(token)] = {"user": user, "name": str(name or "").strip()[:60], "until": until,
                                              "by": by, "uid": b64u(secrets.token_bytes(16))}
        _save(data)
    return token, until


def account_invites() -> list:
    now = _now()
    return [{"user": v["user"], "name": v.get("name", ""), "until": v["until"], "by": v.get("by", "")}
            for v in _load()["invites"].values() if v["until"] > now]


def invite_info(token: str) -> dict:
    inv = _load()["invites"].get(_token_key(token))
    if not inv or inv["until"] < _now():
        raise AuthError("This invite has expired or was already used. Ask for a new one.")
    return inv


def _join(data: dict, token: str, inv: dict, **fields) -> str:
    user = inv["user"]
    if user in data["users"]:
        raise AuthError("There is already an admin by that name.")
    data["users"][user] = {"id": inv["uid"], "name": inv.get("name") or user, "created": _now(),
                           "by": inv.get("by", ""), "password": "", "totp": None, "passkeys": [],
                           "epoch": _new_epoch(), **fields}
    data["invites"].pop(_token_key(token), None)
    _save(data)
    return user


def invite_password(token: str, password: str) -> str:
    _good_password(password)
    invite_info(token)                  # an invite there is, before any hashing
    hashed = hash_password(password)    # not under the lock: every request's session check waits for that
    with _LOCK:
        data = _load()
        inv = invite_info(token)
        return _join(data, token, inv, password=hashed)


def invite_passkey_options(token: str) -> dict:
    inv = invite_info(token)
    return _creation_options(inv["uid"], inv["user"], inv.get("name", ""), [], "invite")


def invite_passkey(token: str, cred: dict, label: str) -> str:
    inv = invite_info(token)
    pk = _new_passkey(cred, "invite", inv["user"], label)
    with _LOCK:
        data = _load()
        inv = invite_info(token)
        return _join(data, token, inv, passkeys=[pk])


# -- one's own account -------------------------------------------------------------------
def account(user: str) -> dict:
    data = _load()
    rec = data["users"].get(user)
    if rec is None:
        raise AuthError("unknown admin")
    return {
        "me": {"user": user, "name": rec.get("name") or user, "password": bool(rec.get("password")),
               "totp": bool(rec.get("totp")),
               "passkeys": [{k: p.get(k) for k in ("id", "name", "added", "used")} for p in rec.get("passkeys", [])]},
        "admins": [{"user": u, "name": r.get("name") or u, "created": r.get("created", 0), "by": r.get("by", ""),
                    "passkeys": len(r.get("passkeys", [])), "password": bool(r.get("password")),
                    "totp": bool(r.get("totp")), "me": u == user}
                   for u, r in sorted(data["users"].items())],
        "invites": account_invites(),
    }


def passkey_options(user: str) -> dict:
    rec = _load()["users"][user]
    return _creation_options(rec["id"], user, rec.get("name", ""), rec.get("passkeys", []), "add")


def passkey_add(user: str, cred: dict, label: str) -> None:
    pk = _new_passkey(cred, "add", user, label)
    with _LOCK:
        data = _load()
        data["users"][user].setdefault("passkeys", []).append(pk)
        _save(data)


def _ways_in(rec: dict) -> int:
    return len(rec.get("passkeys", [])) + (1 if rec.get("password") else 0)


def passkey_remove(user: str, pid: str) -> None:
    with _LOCK:
        data = _load()
        rec = data["users"][user]
        keep = [p for p in rec.get("passkeys", []) if p["id"] != pid]
        if len(keep) == len(rec.get("passkeys", [])):
            raise AuthError("No such passkey.")
        if not keep and not rec.get("password"):
            raise AuthError("This is your only way in. Add another passkey or a password first.")
        rec["passkeys"] = keep
        _save(data)


def passkey_rename(user: str, pid: str, label: str) -> None:
    with _LOCK:
        data = _load()
        for p in data["users"][user].get("passkeys", []):
            if p["id"] == pid:
                p["name"] = (str(label or "").strip() or "Passkey")[:60]
                _save(data)
                return
    raise AuthError("No such passkey.")


def password_set(user: str, password: str, current: str) -> None:
    _good_password(password)
    stored = (_load()["users"].get(user) or {}).get("password") or ""
    if stored and not check_password(str(current or ""), stored):
        raise AuthError("Your current password is not right.")
    hashed = hash_password(password)    # not under the lock: every request's session check waits for that
    with _LOCK:
        data = _load()
        rec = data["users"][user]
        if (rec.get("password") or "") != stored:       # changed elsewhere meanwhile
            raise AuthError("Your current password is not right.")
        rec["password"] = hashed
        _save(data)


def password_remove(user: str) -> None:
    with _LOCK:
        data = _load()
        rec = data["users"][user]
        if not rec.get("passkeys"):
            raise AuthError("Add a passkey first: without a password it is your only way in.")
        rec["password"] = ""
        rec["totp"] = None            # a code only ever follows a password
        _save(data)


def totp_begin(user: str) -> dict:
    secret = totp_secret()
    with _LOCK:
        _pending_totp[user] = (_now() + 10 * 60, secret)
    import qr
    uri = totp_uri(secret, user)
    return {"secret": secret, "uri": uri, "svg": qr.svg(uri)}


def totp_confirm(user: str, code: str) -> None:
    with _LOCK:
        hit = _pending_totp.get(user)
        if not hit or hit[0] < _now():
            raise AuthError("That took too long. Start again.")
        step = totp_match(hit[1], code, 0)
        if step is None:
            raise AuthError("That code is not right. Check the time on the phone, and try the newest code.")
        data = _load()
        rec = data["users"][user]
        if not rec.get("password"):
            raise AuthError("Codes go with a password; set one first.")
        rec["totp"] = {"secret": hit[1], "last": step}
        _save(data)
        _pending_totp.pop(user, None)


def totp_remove(user: str, code: str) -> None:
    with _LOCK:
        data = _load()
        rec = data["users"][user]
        totp = rec.get("totp") or {}
        if not totp:
            return
        if totp_match(totp["secret"], code, int(totp.get("last", 0))) is None:
            raise AuthError("That code is not right.")
        rec["totp"] = None
        _save(data)


def invite_cancel(user: str) -> None:
    with _LOCK:
        data = _load()
        data["invites"] = {k: v for k, v in data["invites"].items() if v["user"] != user}
        _save(data)


def remove_admin(by: str, user: str) -> None:
    with _LOCK:
        data = _load()
        if user == by:
            raise AuthError("You cannot remove yourself; another admin can.")
        if user not in data["users"]:
            raise AuthError("No such admin.")
        del data["users"][user]
        _save(data)


def sign_out_everywhere(user: str) -> None:
    with _LOCK:
        data = _load()
        rec = data["users"][user]
        rec["epoch"] = _new_epoch(int(rec.get("epoch", 1)))
        rec.pop("out", None)            # every session of before is over; none needs remembering
        _save(data)
