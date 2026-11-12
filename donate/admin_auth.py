"""admin_auth.py - who may open the admin page, and how they sign in.

Several admins, each signing in with a passkey (WebAuthn: Face ID, Touch ID,
a phone or a security key), or with a password and - if they turned it on -
a code from an authenticator app (TOTP, RFC 6238). A new admin comes in
through a one-time invite link another admin makes. A signed cookie keeps a
browser signed in (400 days, renewed by each visit); "sign out everywhere"
ends all of one admin's sessions at once.

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

_LOCK = threading.RLock()
_CFG: dict = {"path": "", "origin": "", "key": b""}
_challenges: dict = {}        # challenge -> (until, purpose, subject)
_tickets: dict = {}           # second-step ticket -> (until, user)
_pending_totp: dict = {}      # user -> (until, secret)
_fails: dict = {}             # address or user -> [times]


class AuthError(Exception):
    """Refused, with a reason fit to show; *why* says more, for the
    service's log only."""

    def __init__(self, msg: str, why: str = "") -> None:
        super().__init__(msg)
        self.why = why


def configure(stats_dir: str, origin: str, key: bytes) -> None:
    _CFG.update(path=os.path.join(stats_dir, "admin-users.json"), origin=origin.rstrip("/"), key=key)


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


def has_users() -> bool:
    return bool(_load()["users"])


def is_admin(user: str) -> bool:
    return bool(user) and user in _load()["users"]


# -- passwords ---------------------------------------------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 15, r=8, p=1,
                            maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt$15$8$1${b64u(salt)}${b64u(digest)}"


def check_password(password: str, stored: str) -> bool:
    try:
        kind, logn, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=unb64u(salt), n=2 ** int(logn), r=int(r),
                             p=int(p), maxmem=64 * 1024 * 1024, dklen=32)
        return hmac.compare_digest(got, unb64u(digest))
    except (ValueError, AuthError):
        return False


_DUMMY: list = []


def _dummy_hash() -> str:
    if not _DUMMY:
        _DUMMY.append(hash_password(secrets.token_hex(16)))
    return _DUMMY[0]


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
        ch = b64u(secrets.token_bytes(32))
        _challenges[ch] = (now + CHALLENGE_SECS, purpose, subject)
    return ch


def _client(data_b64: str, kind: str, purpose: str, subject: str = "") -> bytes:
    raw = unb64u(data_b64)
    try:
        client = json.loads(raw)
    except ValueError:
        raise AuthError("bad client data") from None
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


def _new_passkey(cred: dict, purpose: str, user: str, label: str) -> dict:
    resp = cred.get("response") if isinstance(cred, dict) else None
    if not isinstance(resp, dict):
        raise AuthError("No passkey came back.")
    _client(resp.get("clientDataJSON", ""), "webauthn.create", purpose, user)
    att, _ = cbor(unb64u(resp.get("attestationObject", "")))
    if not isinstance(att, dict) or not isinstance(att.get("authData"), bytes):
        raise AuthError("bad attestation")
    ad = auth_data(att["authData"])
    _rp_ok(ad)
    if "cred" not in ad:
        raise AuthError("The passkey sent no key.")
    cid = b64u(ad["cred"])
    for other in _load()["users"].values():
        if any(p["id"] == cid for p in other.get("passkeys", [])):
            raise AuthError("This passkey is already added.")
    return {"id": cid, "key": cose_key(ad["key"]), "count": ad["count"], "added": _now(), "used": 0,
            "name": (str(label or "").strip() or "Passkey")[:60]}


# -- sessions ----------------------------------------------------------------------
def _sign(*parts) -> str:
    msg = ".".join(str(p) for p in parts).encode()
    return hmac.new(_CFG["key"], msg, hashlib.sha256).hexdigest()


def session_cookie(user: str) -> str:
    rec = _load()["users"].get(user)
    if not rec:
        raise AuthError("unknown admin")
    until, epoch = _now() + SESSION_SECS, int(rec.get("epoch", 1))
    return f"{user}.{until}.{epoch}.{_sign(user, until, epoch)}"


def session_user(value: str) -> str:
    """The admin a session cookie's value names, while it holds, or ""."""
    parts = (value or "").split(".")
    if len(parts) != 4:
        return ""
    user, until, epoch, sig = parts
    if not USER_RE.match(user) or not until.isdigit() or not epoch.isdigit() or int(until) < _now():
        return ""
    if not hmac.compare_digest(sig, _sign(user, until, epoch)):
        return ""
    rec = _load()["users"].get(user)
    return user if rec and int(rec.get("epoch", 1)) == int(epoch) else ""


# -- guessing --------------------------------------------------------------------------
def _failed(*keys: str) -> None:
    now = time.time()
    with _LOCK:
        for k in keys:
            if k:
                _fails.setdefault(k, []).append(now)


def blocked(*keys: str) -> bool:
    now = time.time()
    with _LOCK:
        for k in list(_fails):
            _fails[k] = [t for t in _fails[k] if now - t < FAIL_WINDOW]
            if not _fails[k]:
                del _fails[k]
        return any(len(_fails.get(k, ())) >= FAIL_LIMIT for k in keys if k)


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
        ad = auth_data(raw_ad)
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
    # The same work for a name that does not exist: no faster "no".
    stored = (rec or {}).get("password") or _dummy_hash()
    if not check_password(str(password or ""), stored) or not rec or not rec.get("password"):
        _failed("a:" + addr, "u:" + user)
        why = ("no admin by that name" if not rec else
               f"{user} has no password" if not rec.get("password") else f"wrong password for {user}")
        raise AuthError("That name and password do not match.", why)
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
                           "epoch": 1, **fields}
    data["invites"].pop(_token_key(token), None)
    _save(data)
    return user


def invite_password(token: str, password: str) -> str:
    _good_password(password)
    with _LOCK:
        data = _load()
        inv = invite_info(token)
        return _join(data, token, inv, password=hash_password(password))


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
    rec = data["users"][user]
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
    with _LOCK:
        data = _load()
        rec = data["users"][user]
        if rec.get("password") and not check_password(str(current or ""), rec["password"]):
            raise AuthError("Your current password is not right.")
        rec["password"] = hash_password(password)
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
        rec["epoch"] = int(rec.get("epoch", 1)) + 1
        _save(data)
