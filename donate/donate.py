#!/usr/bin/env python3
"""donate.py - the one piece of the site that is not static.

The donate dialog on iptv.dope.rs takes the payment on the page itself:
Stripe's card form (the Payment Element), in Stripe's iframes, on our page,
and Apple Pay / Google Pay in the wallet's own sheet. Both pay a
PaymentIntent, and only a server holding the secret key can make one - this
is that server, and all it does. It never sees a card.

    GET  /donate/config   {"publishableKey", "currency", "country", "name",
                           "min", "max", "presets"}
    POST /donate/session  {"amount": 500}  ->  {"clientSecret", "publishableKey"}
    POST /donate/wallet   {"amount", "paymentMethod", "email", "name"}
                          ->  {"ok": true} | {"action": client_secret} | {"error"}

/donate/session makes the PaymentIntent the card form confirms in the
browser. /donate/wallet is Apple Pay and Google Pay, as on solequiz.com: the
page opens the wallet's sheet (Stripe's Payment Request), the wallet hands
back a payment method, and this confirms a PaymentIntent with it - or hands
back what 3-D Secure still needs.

Standard library only (Python 3.9+). It listens on a Unix socket - no
network, so nothing outside the machine can reach it and a jail's loopback
does not matter - and nginx passes /donate/ to it
(deploy/content-iptv.dope.rs.conf), with a rate limit in front.

    python3 donate.py --check     asks the running service for its config Settings come from the environment, or from KEY=VALUE lines in the
file named by DONATE_CONFIG (default /usr/local/etc/iptv-donate.conf):

    STRIPE_SECRET_KEY_FILE  file holding the key (default
                            /usr/local/etc/iptv-donate.key). Use a restricted
                            key with only "PaymentIntents: Write".
    STRIPE_PUBLISHABLE_KEY  pk_live_... (public; handed to the page)
    DONATE_CURRENCY         eur (default)
    DONATE_COUNTRY          the account's country, for the wallets (SE)
    DONATE_MIN / DONATE_MAX smallest / largest amount, minor units (200 / 100000)
    DONATE_PRESETS          the amount buttons, minor units (300,500,1000,2500)
    DONATE_NAME             what the receipt says it was for ("Support dopeIPTV")
    DONATE_METHODS          what the card form offers: "card" (default), a
                            comma list of Stripe's payment method types
                            ("card,klarna"), or "auto" for everything turned on
                            in the Dashboard - which the account shares with
                            other sites, so it is not the default
    DONATE_RETURN_URL       where Stripe sends the payer back after a payment
                            method that leaves the page (Klarna and the like)
    DONATE_ORIGIN           the site's origin; other origins are refused
    STRIPE_API_VERSION      empty: the account's default
    LISTEN                  unix:/var/run/iptv_donate/donate.sock (default),
                            or host:port for TCP
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
                 if k.startswith(("STRIPE_", "DONATE_", "LISTEN"))})
    return conf


CONF = _load_config()


def _int(key: str, default: int) -> int:
    try:
        return int(CONF.get(key, default))
    except (TypeError, ValueError):
        return default


CURRENCY = CONF.get("DONATE_CURRENCY", "eur").lower()
COUNTRY = CONF.get("DONATE_COUNTRY", "SE").upper()
MIN = _int("DONATE_MIN", 200)
MAX = _int("DONATE_MAX", 100000)
PRESETS = [int(x) for x in CONF.get("DONATE_PRESETS", "300,500,1000,2500").split(",")
           if x.strip().isdigit()]
NAME = CONF.get("DONATE_NAME", "Support dopeIPTV")
METHODS = [m.strip() for m in CONF.get("DONATE_METHODS", "card").split(",") if m.strip()] or ["card"]
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


def wallet_pay(amount: int, method: str, email: str, name: str) -> tuple[int, dict]:
    """Apple Pay / Google Pay: confirm a PaymentIntent with the payment
    method the wallet handed the page. Without redirects - a wallet card
    needs none - so the answer is final, or 3-D Secure for the page."""
    key = _secret()
    if not key:
        return 503, {"error": "not configured"}
    form = {
        "amount": str(amount),
        "currency": CURRENCY,
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
    status, body = _post(STRIPE_PI, form, key)
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


def create_session(amount: int) -> tuple[int, dict]:
    """The PaymentIntent the card form pays: Stripe's Payment Element shows
    the methods turned on in the Dashboard and confirms it in the browser."""
    key = _secret()
    if not key or not PUBLISHABLE:
        return 503, {"error": "not configured"}
    form = {
        "amount": str(amount),
        "currency": CURRENCY,
        "description": NAME,
        "metadata[source]": "iptv.dope.rs donate dialog",
    }
    # Cards only by default: the Dashboard's list (Bancontact, EPS, ...) is
    # the whole account's, and the wallets have their own button anyway.
    if METHODS == ["auto"]:
        form["automatic_payment_methods[enabled]"] = "true"
    else:
        for n, m in enumerate(METHODS):
            form[f"payment_method_types[{n}]"] = m
    status, body = _post(STRIPE_PI, form, key)
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

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] != "/donate/config":
            return self._send(404, {"error": "not found"})
        if not PUBLISHABLE or not _secret():
            return self._send(503, {"error": "not configured"})
        self._send(200, {"publishableKey": PUBLISHABLE, "currency": CURRENCY,
                         "country": COUNTRY, "name": NAME,
                         "min": MIN, "max": MAX, "presets": PRESETS})

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
        except (ValueError, TypeError, AttributeError):
            return self._send(400, {"error": "amount"})
        if not MIN <= amount <= MAX:
            return self._send(400, {"error": "amount", "min": MIN, "max": MAX})
        if path == "/donate/session":
            status, body = create_session(amount)
            return self._send(status, body)
        method = str(data.get("paymentMethod") or "")
        if not method.startswith("pm_") or len(method) > 255:
            return self._send(400, {"error": "payment method"})
        email = str(data.get("email") or "").strip()[:254]
        name = str(data.get("name") or "").strip()
        status, body = wallet_pay(amount, method, email if "@" in email else "", name)
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
    print(f"[donate] listening on {LISTEN}, {CURRENCY}, "
          f"{'configured' if PUBLISHABLE and _secret() else 'NOT configured'}",
          file=sys.stderr, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
