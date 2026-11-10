#!/usr/bin/env python3
"""donate.py - the one piece of the site that is not static.

The donate dialog on iptv.dope.rs takes the payment on the page itself:
Stripe's card form (the Payment Element), in Stripe's iframes, on our page,
and Apple Pay / Google Pay in the wallet's own sheet. Both pay a
PaymentIntent, and only a server holding the secret key can make one - this
is that server, and all it does. It never sees a card. It also serves the
site's admin page, /admin/ (admin.py), behind nginx's password - signed in
once, a browser stays signed in (a signed cookie, /admin-session) - and
tells the owner's devices when someone has donated: Web Push, turned on per
device on the admin page (needs Python's cryptography package).

    GET  /donate/config   {"publishableKey", "currency", "country", "name", "pmc",
                           "currencies": {"sek": {"presets", "min", "max"}, ...}}
    POST /donate/session  {"amount": 5000, "currency": "sek"}
                          ->  {"clientSecret", "publishableKey"}
    POST /donate/wallet   {"amount", "currency", "paymentMethod", "email", "name"}
                          ->  {"ok": true} | {"action": client_secret} | {"error"}

The donor pays in their own currency: the page picks it from where the
device says it is (nav.js), out of CURRENCIES below, and the amounts are
round ones in that currency - 30/50/100/250 kr, not 3 euro. A currency or
an amount outside that table is refused.

/donate/session makes the PaymentIntent the card form confirms in the
browser. /donate/wallet is Apple Pay and Google Pay, as on solequiz.com: the
page opens the wallet's sheet (Stripe's Payment Request), the wallet hands
back a payment method, and this confirms a PaymentIntent with it - or hands
back what 3-D Secure still needs.

Standard library only (Python 3.9+). It listens on a Unix socket - no
network, so nothing outside the machine can reach it and a jail's loopback
does not matter - and nginx passes /donate/ to it
(deploy/content-iptv.dope.rs.conf), with a rate limit in front.

    python3 donate.py --check     asks the running service for its config

Settings come from the environment, or from KEY=VALUE lines in the file
named by DONATE_CONFIG (default /usr/local/etc/iptv-donate.conf):

    STRIPE_SECRET_KEY_FILE  file holding the key (default
                            /usr/local/etc/iptv-donate.key). Use a restricted
                            key with only "PaymentIntents: Write".
    STRIPE_PUBLISHABLE_KEY  pk_live_... (public; handed to the page)
    DONATE_CURRENCY         the currency when the page cannot tell (eur)
    DONATE_CURRENCIES       the ones offered, e.g. eur,sek,usd (default: all
                            of CURRENCIES)
    DONATE_COUNTRY          the account's country, for the wallets (SE)
    DONATE_MIN / DONATE_MAX / DONATE_PRESETS
                            override DONATE_CURRENCY's row of CURRENCIES
                            (minor units: 200 / 100000 / 300,500,1000,2500)
    DONATE_NAME             what the receipt says it was for ("Support dopeIPTV")
    DONATE_SUFFIX           added to the account's statement descriptor on card
                            statements, so donors see e.g. "SOLECRAFT* DOPEIPTV"
                            ("DOPEIPTV"; empty: the account's descriptor alone)
    DONATE_PMC              a payment method configuration (pmc_...) for the card
                            form: the set of methods made for dopeIPTV in the
                            Dashboard (Settings -> Payment methods). Empty: the
                            account's default set, which it shares with other
                            sites. Stripe no longer lets a PaymentIntent list
                            its methods itself (payment_method_types).
    DONATE_RETURN_URL       where Stripe sends the payer back after a payment
                            method that leaves the page (Klarna and the like)
    DONATE_ORIGIN           the site's origin; other origins are refused
    STRIPE_API_VERSION      empty: the account's default
    LISTEN                  unix:/var/run/iptv_donate/donate.sock (default),
                            or host:port for TCP
    ADMIN_USERS             who may open /admin/, e.g. pontus (default: anyone
                            nginx let in)
    STATS_IGNORE            addresses, networks or host names whose visits
                            stats_collect.py leaves out (your own)
    STATS_DIR, DOWNLOADS_JSON  where stats_collect.py and sync-releases.sh keep
                            their counts (<site>/stats, <site>/downloads.json)
    ADMIN_SESSION_KEY_FILE  the key that signs the admin's session cookie
                            (default <STATS_DIR>/admin-session.key, made on
                            first use). Deleting it signs every browser out.
    PUSH_CONTACT            who runs this, for the push services (a mailto:
                            or https URL; default DONATE_ORIGIN). The push key
                            and the devices live in STATS_DIR (push-vapid.pem,
                            push-subscriptions.json).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# admin.py sits beside this file; -I (as rc.d starts it) leaves this
# directory off the path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STRIPE_PI = "https://api.stripe.com/v1/payment_intents"


def _load_config() -> dict[str, str]:
    conf: dict[str, str] = {}
    path = os.environ.get("DONATE_CONFIG", "/usr/local/etc/iptv-donate.conf")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                conf[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    conf.update({k: v for k, v in os.environ.items()
                 if k.startswith(("STRIPE_", "DONATE_", "LISTEN", "STATS_", "ADMIN_", "DL_LOG",
                                  "DOWNLOADS_JSON", "SITE_HOST", "GEO_", "PUSH_"))})
    return conf


CONF = _load_config()


def _int(key: str, default: int) -> int:
    try:
        return int(CONF.get(key, default))
    except (TypeError, ValueError):
        return default


# What a donation can be in each currency, in minor units (cents, öre; yen,
# won, dong and pesos chilenos have none): the four buttons, the smallest and
# the largest. Round amounts that mean about the same everywhere - the euro
# row is 3/5/10/25 - and well above Stripe's own smallest charge in each.
# The currencies of the site's languages and the world's largest. Not the
# rouble or the rial: Stripe does not take payments from Russia or Iran, so
# those visitors pay in euro or dollars. Rows for currencies that lose value
# fast (the lira) want a look now and then.
CURRENCIES: dict[str, dict[str, object]] = {
    k: {"presets": p, "min": lo, "max": hi} for k, (p, lo, hi) in {
        "eur": ([300, 500, 1000, 2500], 200, 100000),
        "usd": ([300, 500, 1000, 2500], 200, 100000),
        "gbp": ([300, 500, 1000, 2000], 200, 100000),
        "chf": ([300, 500, 1000, 2500], 200, 100000),
        "sek": ([3000, 5000, 10000, 25000], 2000, 1000000),
        "nok": ([3000, 5000, 10000, 25000], 2000, 1000000),
        "dkk": ([2500, 4000, 7500, 20000], 1500, 750000),
        "pln": ([1500, 2500, 5000, 10000], 1000, 400000),
        "czk": ([7500, 12500, 25000, 60000], 5000, 2500000),
        "huf": ([120000, 200000, 400000, 1000000], 80000, 40000000),
        "ron": ([1500, 2500, 5000, 12500], 1000, 500000),
        "aud": ([500, 800, 1500, 4000], 300, 150000),
        "cad": ([500, 800, 1500, 3500], 300, 150000),
        "nzd": ([500, 900, 1800, 4500], 300, 150000),
        "jpy": ([500, 800, 1500, 4000], 300, 150000),
        "krw": ([5000, 8000, 15000, 40000], 3000, 1500000),
        "brl": ([1500, 3000, 6000, 15000], 1000, 600000),
        "mxn": ([6000, 10000, 20000, 50000], 4000, 2000000),
        "ils": ([1200, 2000, 4000, 10000], 800, 400000),
        "cny": ([2500, 4000, 8000, 20000], 1500, 800000),
        "hkd": ([2500, 4000, 8000, 20000], 1500, 800000),
        "twd": ([10000, 15000, 30000, 80000], 7000, 3500000),
        "sgd": ([500, 800, 1500, 3500], 300, 150000),
        "inr": ([25000, 50000, 100000, 250000], 15000, 10000000),
        "idr": ([5000000, 10000000, 20000000, 50000000], 3000000, 2000000000),
        "thb": ([10000, 20000, 40000, 100000], 7000, 4000000),
        "vnd": ([75000, 150000, 300000, 750000], 50000, 30000000),
        "php": ([15000, 30000, 60000, 150000], 10000, 6000000),
        "myr": ([1500, 2500, 5000, 12500], 1000, 500000),
        "pkr": ([80000, 150000, 300000, 750000], 50000, 30000000),
        "aed": ([1000, 2000, 4000, 10000], 800, 400000),
        "sar": ([1000, 2000, 4000, 10000], 800, 400000),
        "egp": ([15000, 25000, 50000, 125000], 10000, 5000000),
        "try": ([15000, 25000, 50000, 125000], 10000, 5000000),
        "uah": ([15000, 25000, 50000, 120000], 10000, 5000000),
        "rsd": ([35000, 60000, 120000, 300000], 25000, 12000000),
        "zar": ([6000, 10000, 20000, 50000], 4000, 2000000),
        "kes": ([40000, 70000, 140000, 350000], 30000, 14000000),
        "tzs": ([800000, 1500000, 3000000, 7500000], 600000, 300000000),
        "cop": ([1200000, 2000000, 4000000, 10000000], 800000, 400000000),
        "clp": ([3000, 5000, 10000, 25000], 2000, 1000000),
    }.items()}
# Two-decimal for Stripe, but nobody pays a fraction: whole units only.
WHOLE_UNITS = {"huf", "twd", "idr"}

CURRENCY = CONF.get("DONATE_CURRENCY", "eur").lower()
_offered = [c.strip().lower() for c in CONF.get("DONATE_CURRENCIES", "").split(",") if c.strip()]
if _offered:
    CURRENCIES = {k: v for k, v in CURRENCIES.items() if k in _offered or k == CURRENCY}
_row = CURRENCIES.setdefault(CURRENCY, {"presets": [300, 500, 1000, 2500], "min": 200, "max": 100000})
if "DONATE_MIN" in CONF:
    _row["min"] = _int("DONATE_MIN", 200)
if "DONATE_MAX" in CONF:
    _row["max"] = _int("DONATE_MAX", 100000)
if CONF.get("DONATE_PRESETS"):
    _row["presets"] = [int(x) for x in CONF["DONATE_PRESETS"].split(",") if x.strip().isdigit()]
COUNTRY = CONF.get("DONATE_COUNTRY", "SE").upper()
NAME = CONF.get("DONATE_NAME", "Support dopeIPTV")
PMC = [CONF.get("DONATE_PMC", "").strip()]        # emptied if Stripe refuses it
SUFFIX = [CONF.get("DONATE_SUFFIX", "DOPEIPTV").strip()[:22]]   # likewise
RETURN_URL = CONF.get("DONATE_RETURN_URL", "https://iptv.dope.rs/?donated=1#donate")
ORIGIN = CONF.get("DONATE_ORIGIN", "https://iptv.dope.rs").rstrip("/")
PUBLISHABLE = CONF.get("STRIPE_PUBLISHABLE_KEY", "")
API_VERSION = CONF.get("STRIPE_API_VERSION", "")


def _secret() -> str:
    path = CONF.get("STRIPE_SECRET_KEY_FILE", "/usr/local/etc/iptv-donate.key")
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


# A second line of defence behind nginx's limit: sessions per address.
_SEEN: dict[str, list[float]] = {}
_LOCK = threading.Lock()
WINDOW, PER_WINDOW = 600.0, 12


def _allowed(addr: str) -> bool:
    now = time.monotonic()
    with _LOCK:
        hits = [t for t in _SEEN.get(addr, []) if now - t < WINDOW]
        if len(hits) >= PER_WINDOW:
            _SEEN[addr] = hits
            return False
        hits.append(now)
        _SEEN[addr] = hits
        if len(_SEEN) > 5000:              # forget the quiet ones
            for key in [k for k, v in _SEEN.items() if not v or now - v[-1] > WINDOW]:
                _SEEN.pop(key, None)
        return True


def _post(url: str, form: dict, key: str) -> tuple[int, dict]:
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(form).encode(), method="POST",
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/x-www-form-urlencoded"})
    if API_VERSION:
        req.add_header("Stripe-Version", API_VERSION)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        return 0, {"error": {"message": str(e)}}


def _intent(form: dict, key: str) -> tuple[int, dict]:
    """POST a PaymentIntent with this site's extras - the statement suffix
    and the payment method configuration. One Stripe refuses (by name in its
    error) is left out from then on and the call made again, so neither can
    stop a payment."""
    while True:
        full = dict(form)
        if SUFFIX[0]:
            full["statement_descriptor_suffix"] = SUFFIX[0]
        if PMC[0] and "payment_method" not in form:
            full["payment_method_configuration"] = PMC[0]
        status, body = _post(STRIPE_PI, full, key)
        if status == 200:
            return status, body
        err = (body.get("error") or {}) if isinstance(body, dict) else {}
        said = str(err.get("param") or "") + " " + str(err.get("message") or "")
        for name, box in (("statement_descriptor_suffix", SUFFIX), ("payment_method_configuration", PMC)):
            if status == 400 and box[0] and name in said:
                print(f"[donate] leaving out {name} {box[0]}: {str(err.get('message', ''))[:200]}",
                      file=sys.stderr, flush=True)
                box[0] = ""
                break
        else:
            return status, body


def wallet_pay(amount: int, currency: str, method: str, email: str, name: str) -> tuple[int, dict]:
    """Apple Pay / Google Pay: confirm a PaymentIntent with the payment
    method the wallet handed the page. Without redirects - a wallet card
    needs none - so the answer is final, or 3-D Secure for the page."""
    key = _secret()
    if not key:
        return 503, {"error": "not configured"}
    form = {
        "amount": str(amount),
        "currency": currency,
        "payment_method": method,
        "confirm": "true",
        "automatic_payment_methods[enabled]": "true",
        "automatic_payment_methods[allow_redirects]": "never",
        "description": NAME,
        "metadata[source]": "iptv.dope.rs donate dialog (wallet)",
    }
    if email:
        form["receipt_email"] = email
    if name:
        form["metadata[name]"] = name[:100]
    status, body = _intent(form, key)
    if status == 200:
        state = body.get("status")
        if state == "succeeded" or state == "processing":
            return 200, {"ok": True}
        if state == "requires_action" and body.get("client_secret"):
            return 200, {"action": body["client_secret"]}
    err = (body.get("error") or {}) if isinstance(body, dict) else {}
    print(f"[donate] wallet {status}: {str(err.get('message', ''))[:200]}",
          file=sys.stderr, flush=True)
    # A decline's own words are for the payer; anything else stays here.
    msg = err.get("message") if err.get("type") == "card_error" else None
    return 402 if err.get("type") == "card_error" else 502, {"error": msg or "stripe"}


def create_session(amount: int, currency: str) -> tuple[int, dict]:
    """The PaymentIntent the card form pays: Stripe's Payment Element shows
    the methods of the payment method configuration (DONATE_PMC, else the
    account's default) and confirms it in the browser."""
    key = _secret()
    if not key or not PUBLISHABLE:
        return 503, {"error": "not configured"}
    form = {
        "amount": str(amount),
        "currency": currency,
        "automatic_payment_methods[enabled]": "true",
        "description": NAME,
        "metadata[source]": "iptv.dope.rs donate dialog",
    }
    status, body = _intent(form, key)
    if status == 200 and body.get("client_secret"):
        return 200, {"clientSecret": body["client_secret"], "publishableKey": PUBLISHABLE}
    err = (body.get("error") or {}) if isinstance(body, dict) else {}
    print(f"[donate] payment intent {status}: {str(err.get('message', ''))[:300]}",
          file=sys.stderr, flush=True)
    return 502, {"error": "stripe"}


# -- the admin's session -------------------------------------------------------
# nginx's password prompt asked again with every new browser session - on a
# phone, every time. Signed in once through it, the browser gets a cookie
# signed with a key of the server's own, and nginx (auth_request to
# /admin-session) lets it in on that alone, for as long as a browser keeps a
# cookie (400 days, renewed by every visit). Deleting the key signs every
# browser out.
SESSION_COOKIE = "iptv_admin"
SESSION_SECS = 400 * 86400
#: Tells the site's own pages that this browser is the owner's, so that only
#: its pages ask /admin/news. No secret; the answer is the session's to give.
HINT_COOKIE = "iptv_admin_hint"
#: How far back the owner's note on the site looks.
NEWS_DAYS = 14


def _is_admin(user: str) -> bool:
    allowed = [u.strip() for u in CONF.get("ADMIN_USERS", "").split(",") if u.strip()]
    return bool(user) and (not allowed or user in allowed)
_USER_OK = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")


def _stats_dir() -> str:
    if CONF.get("STATS_DIR"):
        return CONF["STATS_DIR"]
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "stats")


def _session_key() -> bytes:
    path = CONF.get("ADMIN_SESSION_KEY_FILE") or os.path.join(_stats_dir(), "admin-session.key")
    try:
        with open(path, encoding="ascii") as fh:
            key = fh.read().strip()
        if len(key) >= 32:
            return key.encode()
    except FileNotFoundError:
        pass
    key = secrets.token_hex(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(key + "\n")
    except FileExistsError:            # another request made it just now
        with open(path, encoding="ascii") as fh:
            return fh.read().strip().encode()
    return key.encode()


def _session_sign(user: str, until: int) -> str:
    msg = f"{user}.{until}".encode()
    return hmac.new(_session_key(), msg, hashlib.sha256).hexdigest()


def session_cookie(user: str) -> str | None:
    if not _USER_OK.match(user):
        return None
    until = int(time.time()) + SESSION_SECS
    return f"{user}.{until}.{_session_sign(user, until)}"


def session_user(cookie_header: str) -> str:
    """The admin a valid session cookie names, or ""."""
    for part in (cookie_header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name != SESSION_COOKIE:
            continue
        user, _, rest = value.partition(".")
        until, _, sig = rest.partition(".")
        if not _USER_OK.match(user) or not until.isdigit() or int(until) < time.time():
            return ""
        if hmac.compare_digest(sig, _session_sign(user, int(until))):
            return user
    return ""


# -- telling the owner a donation came in: Web Push ----------------------------
# The admin page turns notifications on per device (the browser's own push
# service: Apple's, Google's, Mozilla's, Microsoft's). This service signs each
# push with a key of its own (VAPID, made on first use) and encrypts it for
# that device (RFC 8291) - no third party in between that can read it.
try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except BaseException as e:  # noqa: BLE001 - missing or broken (a Rust panic is no Exception):
    if isinstance(e, (KeyboardInterrupt, SystemExit)):    # donations must work on regardless,
        raise                                             # the admin page says what to install
    ec = None

PUSH_CONTACT = CONF.get("PUSH_CONTACT", "").strip() or ORIGIN
PUSH_EVERY = 90             # seconds between looks at Stripe
PUSH_MAX = 20               # devices kept
_PUSH_LOCK = threading.Lock()
_VAPID: list = []
_ZERO_DECIMAL = {"bif", "clp", "djf", "gnf", "jpy", "kmf", "krw", "mga", "pyg", "rwf",
                 "ugx", "vnd", "vuv", "xaf", "xof", "xpf"}


def _amount_text(minor: int, currency: str) -> str:
    cur = currency.lower()
    if cur in _ZERO_DECIMAL:
        return f"{minor:,} {currency.upper()}".replace(",", " ")
    major = minor / 100
    text = f"{major:,.2f}".replace(",", " ").removesuffix(".00")
    return f"{text} {currency.upper()}"


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def push_ready() -> bool:
    return ec is not None


def _vapid():
    """This service's push key: made on first use, kept in STATS_DIR (600)."""
    with _PUSH_LOCK:
        if _VAPID:
            return _VAPID[0]
        path = os.path.join(_stats_dir(), "push-vapid.pem")
        try:
            with open(path, "rb") as fh:
                key = serialization.load_pem_private_key(fh.read(), None)
        except FileNotFoundError:
            key = ec.generate_private_key(ec.SECP256R1())
            pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(pem)
        _VAPID.append(key)
        return key


def push_key() -> str:
    """The public half: the page's applicationServerKey."""
    return _b64u(_vapid().public_key().public_bytes(serialization.Encoding.X962,
                                                     serialization.PublicFormat.UncompressedPoint))


def _subs_path() -> str:
    return os.path.join(_stats_dir(), "push-subscriptions.json")


def push_subs() -> list:
    try:
        with open(_subs_path(), encoding="utf-8") as fh:
            got = json.load(fh)
        return [s for s in got if isinstance(s, dict) and s.get("endpoint")] if isinstance(got, list) else []
    except (OSError, ValueError):
        return []


def _save_subs(subs: list) -> None:
    path = _subs_path()
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(subs, fh)
    os.replace(tmp, path)


def push_add(sub: dict, label: str) -> bool:
    """Keep one device's subscription (the same endpoint again replaces it)."""
    endpoint = str((sub or {}).get("endpoint") or "")
    keys = (sub or {}).get("keys") or {}
    try:
        ok = (endpoint.startswith("https://") and len(endpoint) < 1200
              and len(_unb64u(str(keys.get("p256dh") or ""))) == 65
              and len(_unb64u(str(keys.get("auth") or ""))) == 16)
    except (ValueError, TypeError):
        ok = False
    if not ok:
        return False
    with _PUSH_LOCK:
        subs = [s for s in push_subs() if s["endpoint"] != endpoint]
        subs.append({"endpoint": endpoint, "p256dh": keys["p256dh"], "auth": keys["auth"],
                     "label": re.sub(r"[^\w .,()/+-]", "", str(label or ""))[:60], "added": int(time.time())})
        _save_subs(subs[-PUSH_MAX:])
    return True


def push_remove(endpoints: list) -> None:
    with _PUSH_LOCK:
        subs = push_subs()
        kept = [s for s in subs if s["endpoint"] not in endpoints]
        if len(kept) != len(subs):
            _save_subs(kept)


def _encrypt(p256dh: str, auth: str, payload: bytes) -> bytes:
    """RFC 8291 (aes128gcm): only the device that subscribed can read it."""
    ua_pub, secret = _unb64u(p256dh), _unb64u(auth)
    mine = ec.generate_private_key(ec.SECP256R1())
    as_pub = mine.public_key().public_bytes(serialization.Encoding.X962,
                                            serialization.PublicFormat.UncompressedPoint)
    shared = mine.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub))

    def hkdf(salt: bytes, ikm: bytes, info: bytes, n: int) -> bytes:
        prk = hmac.new(salt, ikm, hashlib.sha256).digest()
        return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:n]

    ikm = hkdf(secret, shared, b"WebPush: info\x00" + ua_pub + as_pub, 32)
    salt = secrets.token_bytes(16)
    cek = hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    body = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + (4096).to_bytes(4, "big") + bytes([len(as_pub)]) + as_pub + body


def _vapid_auth(endpoint: str) -> str:
    u = urllib.parse.urlsplit(endpoint)
    enc = lambda d: _b64u(json.dumps(d, separators=(",", ":")).encode())  # noqa: E731
    head = enc({"typ": "JWT", "alg": "ES256"}) + "." + enc(
        {"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": PUSH_CONTACT})
    r, s = decode_dss_signature(_vapid().sign(head.encode(), ec.ECDSA(hashes.SHA256())))
    return f"vapid t={head}.{_b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}, k={push_key()}"


def push(title: str, body: str, url: str = "/admin/#donations", tag: str = "") -> tuple:
    """To every device that turned notifications on: (how many took it, why
    not, "" when all did). A device its push service no longer knows (404,
    410: notifications turned off, the browser reset) is forgotten."""
    if not push_ready():
        return 0, "the server lacks Python's cryptography package"
    subs = push_subs()
    if not subs:
        return 0, "no device has turned notifications on"
    payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag}).encode("utf-8")
    sent, gone, why = 0, [], ""
    for s in subs:
        host = urllib.parse.urlsplit(s["endpoint"]).netloc
        try:
            req = urllib.request.Request(s["endpoint"], data=_encrypt(s["p256dh"], s["auth"], payload),
                                         method="POST", headers={
                                             "Authorization": _vapid_auth(s["endpoint"]),
                                             "Content-Encoding": "aes128gcm",
                                             "Content-Type": "application/octet-stream",
                                             "TTL": "86400", "Urgency": "high"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            sent += 1
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                gone.append(s["endpoint"])
            else:
                why = f"{host} answered {e.code}"
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
            why = f"{host}: {getattr(e, 'reason', '') or e}"[:200]
    if gone:
        push_remove(gone)
    if why:
        print(f"[donate] push not sent: {why}", file=sys.stderr, flush=True)
    return sent, why


def _watch_donations() -> None:
    """Every minute and a half: any donation newer than the newest already
    told goes to the devices. The first look only notes where it is, so a
    start never repeats old donations; nor does a device turned on later."""
    path = os.path.join(_stats_dir(), "notified.json")
    try:
        with open(path, encoding="utf-8") as fh:
            last = float(json.load(fh).get("t") or 0)
    except (OSError, ValueError, AttributeError):
        last = 0.0
    while True:
        try:
            if not push_subs():
                last = time.time()          # nobody to tell: not saved up for later
            else:
                import admin
                got = admin.donations(_secret(), API_VERSION, max_age=60)
                rows = got.get("list") or []
                if not got.get("error"):
                    newest = max([r["t"] for r in rows] + [last])
                    if last:
                        for r in sorted((r for r in rows if r["t"] > last), key=lambda r: r["t"]):
                            who = f" · {r['who']}" if r.get("who") else ""
                            push(f"New donation: {_amount_text(r['amount'], r['currency'])}",
                                 f"{r['how']}{who}", tag=f"donation-{r.get('id', '')}")
                    last = newest or time.time()
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"t": last}, fh)
        except Exception as e:  # noqa: BLE001 - the watcher keeps watching
            print(f"[donate] donation watcher: {e!r}", file=sys.stderr, flush=True)
        time.sleep(PUSH_EVERY)


class Handler(BaseHTTPRequestHandler):
    server_version = "iptv-donate"
    sys_version = ""

    def log_message(self, fmt: str, *args) -> None:     # nginx logs requests
        pass

    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _admin(self, path: str) -> None:
        """The admin page and its numbers. Only behind nginx's auth_basic,
        which names the user in X-Admin-User (and overwrites any a client
        sent); without one - a location that lost its password - nothing."""
        user = (self.headers.get("X-Admin-User") or "").strip()
        if not _is_admin(user):
            return self._send(403, {"error": "admin only"})
        try:
            import admin  # beside this file; loaded on first use
        except Exception as e:  # noqa: BLE001 - a broken admin.py must not stop donations
            print(f"[donate] admin page unavailable: {e}", file=sys.stderr, flush=True)
            return self._send(500, {"error": "admin unavailable"})
        if path in ("/admin/data", "/admin/visits", "/admin/visitor"):
            query = {k: v[0] for k, v in urllib.parse.parse_qs(self.path.partition("?")[2]).items()}
            stats_dir = CONF.get("STATS_DIR", os.path.join(admin.SITE, "stats"))
            try:                            # the newest lines first: the page is never behind
                import stats_collect
                stats_collect.collect_if_stale()
            except Exception as e:  # noqa: BLE001 - old numbers rather than no page
                print(f"[donate] statistics not brought up to date: {e!r}", file=sys.stderr, flush=True)
            if path == "/admin/visits":
                return self._send(200, admin.visits(stats_dir, query.get("day", "")))
            if path == "/admin/visitor":
                return self._send(200, admin.visitor(stats_dir, query.get("id", "")))
            try:
                days = int(query.get("days", "30"))
            except ValueError:
                days = 30
            body = admin.data(days, stats_dir,
                              CONF.get("DOWNLOADS_JSON", os.path.join(admin.SITE, "downloads.json")),
                              _secret(), API_VERSION)
            body["visits_days"] = int(CONF.get("STATS_VISITS_DAYS", "30") or 30)
            body["push"] = {"ready": push_ready(), "key": push_key() if push_ready() else "",
                            "devices": [{"label": d.get("label", ""), "added": d.get("added", 0)}
                                        for d in push_subs()]}
            return self._send(200, body)
        hit = admin.static(path)
        if not hit:
            return self._send(404, {"error": "not found"})
        session = session_cookie(user) if path == "/admin/" else None
        data, ctype = hit
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        if path == "/admin/":
            # This browser is the site's owner: nginx leaves it out of the
            # statistics (deploy/content-iptv.dope.rs.conf, $iptv_count).
            # 400 days is the longest a browser keeps a cookie; every visit
            # here renews it.
            self.send_header("Set-Cookie", "iptv_nostats=1; Max-Age=34560000; Path=/; "
                                           "Secure; HttpOnly; SameSite=Lax")
            if session:                 # signed in for good, renewed by each visit
                self.send_header("Set-Cookie", f"{SESSION_COOKIE}={session}; Max-Age={SESSION_SECS}; "
                                               "Path=/admin/; Secure; HttpOnly; SameSite=Strict")
                # Readable by the site's own pages, so only this browser's
                # pages ask /admin/news; it proves nothing (the session does).
                self.send_header("Set-Cookie", f"{HINT_COOKIE}=1; Max-Age={SESSION_SECS}; Path=/; "
                                               "Secure; SameSite=Lax")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _news(self) -> None:
        """The last two weeks' donations, for the owner's note on the site's
        own pages (public/nav.js). nginx asks no password here - every page
        that asked would meet a prompt - so only a valid session cookie gets
        an answer; anyone else an empty 204, and the hint cookie that made
        the page ask is taken away."""
        user = session_user(self.headers.get("Cookie") or "")
        if not _is_admin(user):
            self.send_response(204)
            self.send_header("Set-Cookie", f"{HINT_COOKIE}=; Max-Age=0; Path=/; Secure; SameSite=Lax")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            import admin
            got = admin.donations(_secret(), API_VERSION, max_age=120)
        except Exception as e:  # noqa: BLE001 - the note is not worth a broken page
            print(f"[donate] news unavailable: {e!r}", file=sys.stderr, flush=True)
            return self._send(200, {"list": [], "error": "unavailable"})
        since = time.time() - NEWS_DAYS * 86400
        rows = [{k: r.get(k) for k in ("t", "amount", "currency", "how", "who")}
                for r in got.get("list") or [] if r.get("t", 0) >= since]
        self._send(200, {"list": rows[:20], "error": got.get("error", "")})

    def _open_file(self, path: str) -> None:
        """The service worker that shows the notifications, and the page's
        app manifest (an iPhone puts the page on its Home Screen from it):
        no secrets in them, and nginx asks no password for them - a worker
        script or a manifest fetched behind one fails to load."""
        import admin
        hit = admin.static(path)
        if not hit:
            return self._send(404, {"error": "not found"})
        data, ctype = hit
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _admin_post(self, path: str) -> None:
        """Turning a device's notifications on or off, and a test - the
        signed-in admin only, from the site's own page."""
        if not _is_admin((self.headers.get("X-Admin-User") or "").strip()):
            return self._send(403, {"error": "admin only"})
        if (self.headers.get("Origin") or "").rstrip("/") != ORIGIN:
            return self._send(403, {"error": "origin"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(length)) if 0 < length <= 8192 else {}
        except (ValueError, TypeError):
            return self._send(400, {"error": "bad request"})
        if not isinstance(data, dict):
            data = {}
        if path == "/admin/notify-test":
            sent, why = push("Notifications work", "This is how a new donation will look.", tag="test")
            return self._send(200 if sent else 502, {"ok": bool(sent), "sent": sent, "error": why})
        if not push_ready():
            return self._send(503, {"error": "the server lacks Python's cryptography package"})
        if path == "/admin/push-subscribe":
            if not push_add(data.get("subscription") or {}, str(data.get("label") or "")):
                return self._send(400, {"error": "not a push subscription"})
        else:
            push_remove([str(data.get("endpoint") or "")])
        return self._send(200, {"ok": True, "devices": len(push_subs())})

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/admin-session":
            # nginx's auth_request: a valid session cookie lets the browser
            # in without the password prompt, and names the admin.
            user = session_user(self.headers.get("Cookie") or "")
            self.send_response(204 if user else 401)
            if user:
                self.send_header("X-Admin-User", user)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/admin/news":
            return self._news()
        if path in ("/admin/sw.js", "/admin/app.webmanifest"):
            return self._open_file(path)
        if path == "/admin/" or path in ("/admin/admin.css", "/admin/admin.js", "/admin/data",
                                         "/admin/visits", "/admin/visitor"):
            return self._admin(path)
        if path != "/donate/config":
            return self._send(404, {"error": "not found"})
        if not PUBLISHABLE or not _secret():
            return self._send(503, {"error": "not configured"})
        # pmc too: the page's card form must name the same payment method
        # configuration as the PaymentIntent it is made for (not a secret).
        row = CURRENCIES[CURRENCY]
        self._send(200, {"publishableKey": PUBLISHABLE, "currency": CURRENCY,
                         "country": COUNTRY, "name": NAME, "pmc": PMC[0],
                         "currencies": CURRENCIES,
                         # a page cached from before the currencies:
                         "min": row["min"], "max": row["max"], "presets": row["presets"]})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/admin/notify-test", "/admin/push-subscribe", "/admin/push-unsubscribe"):
            return self._admin_post(path)
        if path not in ("/donate/session", "/donate/wallet"):
            return self._send(404, {"error": "not found"})
        origin = (self.headers.get("Origin") or "").rstrip("/")
        if origin != ORIGIN:
            return self._send(403, {"error": "origin"})
        addr = self.headers.get("X-Real-IP") or "local"     # a Unix socket has no peer address
        if not _allowed(addr):
            return self._send(429, {"error": "slow down"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 1024:
            return self._send(400, {"error": "body"})
        try:
            data = json.loads(self.rfile.read(length))
            amount = int(data.get("amount"))
            currency = str(data.get("currency") or CURRENCY).lower()
        except (ValueError, TypeError, AttributeError):
            return self._send(400, {"error": "amount"})
        row = CURRENCIES.get(currency)
        if row is None:
            return self._send(400, {"error": "currency"})
        lo, hi = int(row["min"]), int(row["max"])     # type: ignore[call-overload]
        if not lo <= amount <= hi:
            return self._send(400, {"error": "amount", "min": lo, "max": hi})
        if currency in WHOLE_UNITS and amount % 100:
            return self._send(400, {"error": "amount"})
        if path == "/donate/session":
            status, body = create_session(amount, currency)
            return self._send(status, body)
        method = str(data.get("paymentMethod") or "")
        if not method.startswith("pm_") or len(method) > 255:
            return self._send(400, {"error": "payment method"})
        email = str(data.get("email") or "").strip()[:254]
        name = str(data.get("name") or "").strip()
        status, body = wallet_pay(amount, currency, method, email if "@" in email else "", name)
        self._send(status, body)


LISTEN = CONF.get("LISTEN", "unix:/var/run/iptv_donate/donate.sock")


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def _connection():
    import http.client
    if LISTEN.startswith("unix:"):
        path = LISTEN[5:]

        class Conn(http.client.HTTPConnection):
            def connect(self) -> None:
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.settimeout(5)
                self.sock.connect(path)
        return Conn("localhost", timeout=5)
    host, _, port = LISTEN.rpartition(":")
    return http.client.HTTPConnection(host or "127.0.0.1", int(port), timeout=5)


def check() -> int:
    """Ask the running service what the page would: GET /donate/config."""
    try:
        conn = _connection()
        conn.request("GET", "/donate/config")
        resp = conn.getresponse()
        print(resp.status, resp.read().decode("utf-8", "replace"))
        return 0 if resp.status == 200 else 1
    except OSError as e:
        print(f"no answer on {LISTEN}: {e}")
        return 1


def main() -> None:
    if "--check" in sys.argv[1:]:
        raise SystemExit(check())
    if LISTEN.startswith("unix:"):
        path = LISTEN[5:]
        try:
            os.unlink(path)                 # a socket left by a stopped run
        except FileNotFoundError:
            pass
        srv = UnixHTTPServer(path, Handler)
        os.chmod(path, 0o666)               # nginx's worker connects to it
    else:
        host, _, port = LISTEN.rpartition(":")
        srv = ThreadingHTTPServer((host or "127.0.0.1", int(port)), Handler)
    print(f"[donate] listening on {LISTEN}, {len(CURRENCIES)} currencies ({CURRENCY} by default), "
          f"{'configured' if PUBLISHABLE and _secret() else 'NOT configured'}, "
          f"push notifications {'ready' if push_ready() else 'off (no cryptography package)'}",
          file=sys.stderr, flush=True)
    if push_ready() and _secret():
        threading.Thread(target=_watch_donations, name="donations", daemon=True).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
