"""Browser (authorization-code) Trakt sign-in: URL building, code exchange,
and the loopback redirect capture. No network - requests is monkeypatched and
the loopback server is driven over real localhost sockets."""

import threading
import time
import urllib.parse
import urllib.request

from PyQt6.QtCore import QSettings

from dopeiptv.providers import trakt as T
from dopeiptv.providers.oauth_loopback import capture_oauth_redirect


def _client(tmp_name):
    s = QSettings("dopeIPTV-test", tmp_name)
    s.clear()
    s.setValue("trakt_client_id", "CID")
    s.setValue("trakt_client_secret", "SECRET")
    return T.TraktClient(s)


def test_authorize_url_carries_the_oauth_params():
    url = _client("auth_url").authorize_url("STATE")
    assert url.startswith("https://trakt.tv/oauth/authorize?")
    q = dict(urllib.parse.parse_qsl(url.split("?", 1)[1]))
    assert q["response_type"] == "code"
    assert q["client_id"] == "CID"
    assert q["state"] == "STATE"
    assert q["redirect_uri"] == T.REDIRECT_URI


def test_exchange_code_posts_and_stores_tokens(monkeypatch):
    seen = {}

    class Resp:
        status_code = 200

        def json(self):
            return {"access_token": "AT", "refresh_token": "RT",
                    "expires_in": 100}

    def fake_post(url, json=None, timeout=None):
        seen["url"] = url
        seen["json"] = json
        return Resp()

    monkeypatch.setattr(T.requests, "post", fake_post)
    tc = _client("exchange")
    tc.exchange_code("THECODE")
    assert seen["url"] == "https://api.trakt.tv/oauth/token"
    assert seen["json"]["grant_type"] == "authorization_code"
    assert seen["json"]["code"] == "THECODE"
    assert seen["json"]["client_secret"] == "SECRET"
    assert tc.access_token == "AT" and tc.is_connected()


def test_exchange_code_raises_on_rejection(monkeypatch):
    class Resp:
        status_code = 403

        def json(self):
            return {}

    monkeypatch.setattr(T.requests, "post", lambda *a, **k: Resp())
    tc = _client("exchange_fail")
    try:
        tc.exchange_code("X")
        raise AssertionError("expected TraktAuthError")
    except T.TraktAuthError:
        pass


def test_loopback_captures_the_redirect_query():
    port = 46123
    out = {}

    def run():
        out["params"] = capture_oauth_redirect(port, timeout=10)

    th = threading.Thread(target=run)
    th.start()
    # Poll until the server is accepting, then send the fake redirect.
    for _ in range(50):
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/callback?code=C&state=S", timeout=2)
            break
        except OSError:
            time.sleep(0.1)
    th.join(5)
    assert out["params"] == {"code": "C", "state": "S"}


def test_loopback_cancel_returns_none():
    cancel = {"v": False}
    out = {}

    def run():
        out["r"] = capture_oauth_redirect(
            46124, timeout=10, should_cancel=lambda: cancel["v"])

    th = threading.Thread(target=run)
    th.start()
    time.sleep(0.3)
    cancel["v"] = True
    th.join(5)
    assert out["r"] is None
