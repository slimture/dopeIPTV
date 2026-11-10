#!/usr/bin/env python3
"""donate.py - the one piece of the site that is not static.

The donate dialog on iptv.dope.rs takes the payment on the page itself:
Stripe's card form (the Payment Element), in Stripe's iframes, on our page,
and Apple Pay / Google Pay in the wallet's own sheet. Both pay a
PaymentIntent, and only a server holding the secret key can make one - this
is that server, and all it does. It never sees a card. It also serves the
site's admin page, /admin/ (admin.py), behind nginx's password.

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
"""
from __future__ import annotations

import json
import os
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
                                  "DOWNLOADS_JSON", "SITE_HOST", "GEO_"))})
    return conf


CONF = _load_config()


def _int(key: str, default: int) -> int:
    try:
        return int(CONF.get(key, default))
    except (TypeError, ValueError):
        return default


# What a donation can be in each currency, in minor units (cents, öre; yen
# and won have none): the four buttons, the smallest and the largest. Round
# amounts that mean about the same everywhere - the euro row is 3/5/10/25 -
# and well above Stripe's own smallest charge in each.
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
    }.items()}

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
        allowed = [u.strip() for u in CONF.get("ADMIN_USERS", "").split(",") if u.strip()]
        if not user or (allowed and user not in allowed):
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
            return self._send(200, body)
        hit = admin.static(path)
        if not hit:
            return self._send(404, {"error": "not found"})
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
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
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
        if currency == "huf" and amount % 100:        # whole forints only
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
          f"{'configured' if PUBLISHABLE and _secret() else 'NOT configured'}",
          file=sys.stderr, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
