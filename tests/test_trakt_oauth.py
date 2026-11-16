"""Browser (authorization-code) Trakt sign-in: URL building, code exchange,
and the loopback redirect capture. No network - requests is monkeypatched and
the loopback server is driven over real localhost sockets."""

import socket
import threading
import time
import urllib.parse
import urllib.request

import pytest
from PyQt6.QtCore import QSettings

from dopeiptv.providers import trakt as T
from dopeiptv.providers.oauth_loopback import capture_oauth_redirect


def _client(tmp_name, **stored):
    """A client over settings that hold *stored* already: it reads its
    sign-in once, when it is made."""
    s = QSettings("dopeIPTV-test", tmp_name)
    s.clear()
    s.setValue("trakt_client_id", "CID")
    s.setValue("trakt_client_secret", "SECRET")
    for key, value in stored.items():
        s.setValue(key, value)
    return T.TraktClient(s)


def test_authorize_url_carries_the_oauth_params():
    # Trakt moved its sign-in to auth.trakt.tv in 2026; the old
    # trakt.tv/oauth/authorize lost the parameters on the way and the user
    # got "invalid_client: client_id is required".
    url = _client("auth_url").authorize_url("STATE", "CHALLENGE")
    assert url.startswith("https://auth.trakt.tv/oauth/authorize?")
    q = dict(urllib.parse.parse_qsl(url.split("?", 1)[1]))
    assert q["response_type"] == "code"
    assert q["client_id"] == "CID"
    assert q["state"] == "STATE"
    assert q["redirect_uri"] == T.REDIRECT_URI
    assert q["code_challenge"] == "CHALLENGE"
    assert q["code_challenge_method"] == "S256"


def test_pkce_pair_follows_rfc_7636():
    import base64
    import hashlib
    import re
    verifier, challenge = T.new_pkce_pair()
    assert 43 <= len(verifier) <= 128
    assert re.fullmatch(r"[A-Za-z0-9\-._~]+", verifier)
    want = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == want and "=" not in challenge
    assert T.new_pkce_pair()[0] != verifier          # fresh every sign-in


def test_oauth_calls_go_to_the_auth_host(monkeypatch):
    urls = []

    class Resp:
        status_code = 200

        def json(self):
            return {"device_code": "D", "user_code": "U",
                    "access_token": "AT", "refresh_token": "RT",
                    "expires_in": 100}

        def raise_for_status(self):
            pass

    def fake_post(url, json=None, timeout=None):
        urls.append(url)
        return Resp()

    monkeypatch.setattr(T.requests, "post", fake_post)
    tc = _client("auth_host")
    tc.start_device_auth()
    tc.poll_device_token("D")
    tc.exchange_code("C", "V")
    tc.settings.setValue("trakt_refresh_token", "RT")
    tc.refresh_tokens()
    assert urls == ["https://auth.trakt.tv/oauth/device/code",
                    "https://auth.trakt.tv/oauth/device/token",
                    "https://auth.trakt.tv/oauth/token",
                    "https://auth.trakt.tv/oauth/token"]


def test_the_bundled_app_has_no_secret_and_returns_through_the_site(
        monkeypatch):
    s = QSettings("dopeIPTV-test", "secret_rules")
    s.clear()
    tc = T.TraktClient(s)
    assert tc.client_id == T.DEFAULT_CLIENT_ID
    # Trakt issues apps no secret since October 2026: PKCE proves the app.
    assert tc.client_secret == ""
    # A Trakt app every account can use may only redirect to https://, so
    # the sign-in comes back through the project's site - in the sign-in
    # page's address and in the code exchange alike.
    assert T.WEB_REDIRECT_URI.startswith("https://")
    q = dict(urllib.parse.parse_qsl(
        tc.authorize_url("S", "C").split("?", 1)[1]))
    assert q["redirect_uri"] == T.WEB_REDIRECT_URI
    sent = {}

    class Resp:
        status_code = 200

        def json(self):
            return {"access_token": "AT", "refresh_token": "RT",
                    "expires_in": 100}

    monkeypatch.setattr(T.requests, "post",
                        lambda url, json=None, timeout=None:
                        sent.update(json) or Resp())
    tc.exchange_code("CODE", "V")
    assert sent["redirect_uri"] == T.WEB_REDIRECT_URI
    assert "client_secret" not in sent
    # A secret stored without an id of one's own never goes with ours.
    s.setValue("trakt_client_secret", "STRAY")
    assert tc.client_secret == ""
    # Somebody's own app: its own secret, and the loopback address.
    s.setValue("trakt_client_id", "THEIRS")
    assert tc.client_secret == "STRAY"
    assert tc.redirect_uri == T.REDIRECT_URI
    s.clear()


def test_the_device_sign_in_finds_its_page_under_either_name():
    pytest.importorskip("PyQt6.QtWidgets")
    from dopeiptv.ui.mw_trakt import device_pages
    # Trakt's own name for it...
    assert device_pages({"verification_url": "https://trakt.tv/activate"}) \
        == ("https://trakt.tv/activate", "https://trakt.tv/activate")
    # ...RFC 8628's, with the code already filled in where it is offered...
    assert device_pages({
        "verification_uri": "https://trakt.tv/activate",
        "verification_uri_complete": "https://trakt.tv/activate?code=AB12",
    }) == ("https://trakt.tv/activate", "https://trakt.tv/activate?code=AB12")
    # ...and Trakt's activation page when the answer names none.
    assert device_pages({}) == (T.ACTIVATE_URL, T.ACTIVATE_URL)


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
    tc.exchange_code("THECODE", "VERIFIER")
    assert seen["url"] == "https://auth.trakt.tv/oauth/token"
    assert seen["json"]["grant_type"] == "authorization_code"
    assert seen["json"]["code"] == "THECODE"
    # PKCE: the verifier replaces the secret, which a desktop app must not
    # send (developer.trakt.tv/docs/pkce).
    assert seen["json"]["code_verifier"] == "VERIFIER"
    assert "client_secret" not in seen["json"]
    assert tc.access_token == "AT" and tc.is_connected()


def test_exchange_code_raises_on_rejection(monkeypatch):
    class Resp:
        status_code = 403

        def json(self):
            return {}

    monkeypatch.setattr(T.requests, "post", lambda *a, **k: Resp())
    tc = _client("exchange_fail")
    try:
        tc.exchange_code("X", "V")
        raise AssertionError("expected TraktAuthError")
    except T.TraktAuthError:
        pass


def _free_port() -> int:
    # A port the system hands out now. A fixed one (46123) sat in Linux's
    # range for outgoing connections, and another test's client socket
    # sometimes held it: "Address already in use" failed a CI run.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_loopback_captures_the_redirect_query():
    port = _free_port()
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
        try:
            out["r"] = capture_oauth_redirect(
                _free_port(), timeout=10, should_cancel=lambda: cancel["v"])
        except OSError as e:            # the reason, not a KeyError below
            out["error"] = e

    th = threading.Thread(target=run)
    th.start()
    time.sleep(0.3)
    cancel["v"] = True
    th.join(5)
    assert "error" not in out, out.get("error")
    assert out["r"] is None


class _TokenResp:
    def __init__(self, code, body=None):
        self.status_code = code
        self._body = body or {}

    def json(self):
        return self._body


def test_an_expiring_token_is_refreshed_before_the_call(monkeypatch):
    # The refresh token and expiry were stored and never read: when the
    # access token ran out, Trakt stopped working without a word.
    posts = []

    def fake_post(url, json=None, timeout=None):
        posts.append(json)
        return _TokenResp(200, {"access_token": "AT2", "refresh_token": "RT2",
                                "expires_in": 86400})

    monkeypatch.setattr(T.requests, "post", fake_post)
    tc = _client("refresh_due",
                 trakt_access_token="AT1",
                 trakt_refresh_token="RT1",
                 trakt_expires_at=int(time.time()) + 60)
    h = tc._headers()
    assert h["Authorization"] == "Bearer AT2"
    assert posts[0]["grant_type"] == "refresh_token"
    assert posts[0]["refresh_token"] == "RT1"
    assert tc.refresh_token == "RT2"
    tc._headers()
    assert len(posts) == 1                       # fresh now: no second call


def test_a_valid_token_is_left_alone(monkeypatch):
    def no_post(*_a, **_k):
        raise AssertionError("refreshed a token that is still valid")

    monkeypatch.setattr(T.requests, "post", no_post)
    tc = _client("refresh_not_due",
                 trakt_access_token="AT1",
                 trakt_refresh_token="RT1",
                 trakt_expires_at=int(time.time()) + 86400)
    assert tc._headers()["Authorization"] == "Bearer AT1"


def test_a_refused_refresh_tries_the_oob_uri_then_backs_off(monkeypatch):
    posts = []

    def fake_post(url, json=None, timeout=None):
        posts.append(json["redirect_uri"])
        return _TokenResp(401)

    monkeypatch.setattr(T.requests, "post", fake_post)
    tc = _client("refresh_refused",
                 trakt_access_token="AT1",
                 trakt_refresh_token="RT1",
                 trakt_expires_at=int(time.time()) - 5)
    assert tc._headers()["Authorization"] == "Bearer AT1"
    assert posts == [T.REDIRECT_URI, T.OOB_REDIRECT_URI]
    tc._headers()
    assert len(posts) == 2                       # backed off, no hammering


def test_a_retired_refresh_token_disconnects(monkeypatch):
    # Trakt's 2026 sign-in move retired every refresh token issued before
    # it. Keeping the dead token left Settings saying "Connected" while
    # every Trakt call failed; now the sign-in is dropped so Connect shows.
    def fake_post(url, json=None, timeout=None):
        return _TokenResp(400, {"error": "invalid_grant",
                                "error_description": "session not found"})

    monkeypatch.setattr(T.requests, "post", fake_post)
    tc = _client("refresh_retired",
                 trakt_access_token="AT1",
                 trakt_refresh_token="RT1",
                 trakt_expires_at=int(time.time()) - 5)
    assert tc.is_connected()
    assert tc.refresh_tokens() is False
    assert not tc.is_connected() and tc.refresh_token == ""


class _ThreadSettings(dict):
    """A settings file that notes every read and write of the sign-in made
    off the main thread (the window's)."""

    def __init__(self, **stored):
        super().__init__(**stored)
        self.off_main = []

    def _note(self, what, key):
        if key in T.TOKEN_KEYS and threading.current_thread() is not threading.main_thread():
            self.off_main.append((what, key))

    def value(self, key, default=None):
        self._note("read", key)
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802
        self._note("write", key)
        self[key] = value

    def remove(self, key):
        self._note("remove", key)
        self.pop(key, None)


def _on_a_worker(fn):
    out = {}

    def run():
        try:
            out["result"] = fn()
        except Exception as e:  # noqa: BLE001 - handed back to the test
            out["error"] = e
    t = threading.Thread(target=run)
    t.start()
    t.join(10)
    if "error" in out:
        raise out["error"]
    return out.get("result")


def test_a_refresh_on_a_worker_is_used_at_once_and_stored_on_the_windows_thread(monkeypatch):
    widgets = pytest.importorskip("PyQt6.QtWidgets")
    app = widgets.QApplication.instance() or widgets.QApplication([])
    answers = [_TokenResp(200, {"access_token": "AT2", "refresh_token": "RT2",
                                "expires_in": 86400})]
    monkeypatch.setattr(T.requests, "post", lambda *a, **k: answers.pop(0))
    s = _ThreadSettings(trakt_client_id="CID", trakt_access_token="AT1",
                        trakt_refresh_token="RT1", trakt_expires_at=int(time.time()) + 60)
    tc = T.TraktClient(s)
    assert _on_a_worker(tc._headers)["Authorization"] == "Bearer AT2"   # at once
    assert tc.refresh_token == "RT2" and s["trakt_access_token"] == "AT1"
    app.processEvents()                                    # the window's thread stores it
    assert (s["trakt_access_token"], s["trakt_refresh_token"]) == ("AT2", "RT2")
    assert int(s["trakt_expires_at"]) > time.time() + 80000
    # Retired on a worker: signed out at once, and in the settings on the
    # window's thread - the account's favorites list with it.
    s["trakt_fav_list_slug"] = "mine"
    tc = T.TraktClient(s)
    answers[:] = [_TokenResp(400, {"error": "invalid_grant"})] * 2
    assert _on_a_worker(tc.refresh_tokens) is False
    assert not tc.is_connected() and s["trakt_access_token"] == "AT2"
    app.processEvents()
    assert not any(k in s for k in T.TOKEN_KEYS)
    assert s.off_main == []                                # never read or written there


def test_a_renewal_out_when_sign_out_is_pressed_brings_nothing_back(monkeypatch):
    """Sign out while a worker's renewal waits for Trakt's answer: the
    answer that comes after neither signs in again (in memory or in the
    settings) nor is a refusal of it anything to undo."""
    widgets = pytest.importorskip("PyQt6.QtWidgets")
    app = widgets.QApplication.instance() or widgets.QApplication([])
    out, answered = threading.Event(), threading.Event()
    answers = [_TokenResp(200, {"access_token": "AT2", "refresh_token": "RT2",
                                "expires_in": 86400})]

    def post(*_a, **_k):
        out.set()
        answered.wait(10)
        return answers.pop(0)
    monkeypatch.setattr(T.requests, "post", post)
    s = _ThreadSettings(trakt_client_id="CID", trakt_access_token="AT1",
                        trakt_refresh_token="RT1", trakt_expires_at=int(time.time()) + 60,
                        trakt_fav_list_slug="mine")
    tc = T.TraktClient(s)
    worker = threading.Thread(target=tc._ensure_fresh_token)
    worker.start()
    assert out.wait(10)
    tc.disconnect()                          # Sign out, on the window's thread
    answered.set()
    worker.join(10)
    assert not tc.is_connected() and tc.refresh_token == ""
    app.processEvents()
    assert not any(k in s for k in T.TOKEN_KEYS) and s.off_main == []
    assert not T.TraktClient(s).is_connected()
    # The same with Trakt refusing it: still signed out, nothing written.
    s = _ThreadSettings(trakt_client_id="CID", trakt_access_token="AT1",
                        trakt_refresh_token="RT1", trakt_expires_at=int(time.time()) + 60)
    tc = T.TraktClient(s)
    out.clear()
    answered.clear()
    answers[:] = [_TokenResp(400, {"error": "invalid_grant"})] * 2
    worker = threading.Thread(target=tc._ensure_fresh_token)
    worker.start()
    assert out.wait(10)
    tc.disconnect()
    answered.set()
    worker.join(10)
    app.processEvents()
    assert not tc.is_connected() and not any(k in s for k in T.TOKEN_KEYS)


def test_a_server_error_keeps_the_sign_in(monkeypatch):
    monkeypatch.setattr(T.requests, "post",
                        lambda *a, **k: _TokenResp(503))
    tc = _client("refresh_503",
                 trakt_access_token="AT1",
                 trakt_refresh_token="RT1",
                 trakt_expires_at=int(time.time()) - 5)
    assert tc.refresh_tokens() is False
    assert tc.is_connected() and tc.refresh_token == "RT1"


def _poll(monkeypatch, code, body=None, name="poll"):
    monkeypatch.setattr(T.requests, "post",
                        lambda *a, **k: _TokenResp(code, body))
    monkeypatch.setattr(T.time, "sleep", lambda _s: None)
    return _client(name).poll_device_token("D")


def test_device_poll_keeps_waiting_when_asked_to_slow_down(monkeypatch):
    # A 429 went to raise_for_status and aborted the sign-in; RFC 8628 says
    # slow down and keep going.
    assert _poll(monkeypatch, 429) is None
    assert _poll(monkeypatch, 400, {"error": "slow_down"}) is None
    assert _poll(monkeypatch, 400, {"error": "authorization_pending"}) is None
    assert _poll(monkeypatch, 400) is None


def test_device_poll_reports_a_denial_and_an_expiry(monkeypatch):
    from dopeiptv.i18n import tr
    for code, body, want in ((400, {"error": "access_denied"}, "trakt_denied"),
                             (418, None, "trakt_denied"),
                             (400, {"error": "expired_token"},
                              "trakt_code_expired"),
                             (410, None, "trakt_code_expired"),
                             (404, None, "trakt_code_invalid"),
                             (409, None, "trakt_code_used")):
        try:
            _poll(monkeypatch, code, body)
            raise AssertionError(f"{code} {body} did not raise")
        except T.TraktAuthError as e:
            assert str(e) == tr(want)


def test_device_poll_names_an_unknown_oauth_error(monkeypatch):
    try:
        _poll(monkeypatch, 401, {"error": "invalid_client",
                                 "error_description": "unknown app"})
        raise AssertionError("expected TraktAuthError")
    except T.TraktAuthError as e:
        assert "invalid_client" in str(e) and "unknown app" in str(e)


def test_disconnect_forgets_the_accounts_favorites_list():
    tc = _client("disconnect",
                 trakt_access_token="AT",
                 trakt_fav_list_slug="someone-elses-list")
    tc.disconnect()
    assert not tc.is_connected()
    assert not tc.settings.value("trakt_fav_list_slug", "")


def test_a_failed_watched_sync_lets_the_next_one_run(monkeypatch):
    """An error while storing the result left the running flag set, and no
    sync ran again until the app was restarted."""
    from types import SimpleNamespace

    from dopeiptv.ui import mw_trakt
    from dopeiptv.ui.mw_trakt import _TraktMixin

    monkeypatch.setattr(mw_trakt.QTimer, "singleShot",
                        staticmethod(lambda _ms, fn: fn()))

    class _Thread:
        def __init__(self, target, daemon=False):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(mw_trakt.threading, "Thread", _Thread)

    def broken(*_a, **_k):
        raise ValueError("bad row")

    w = SimpleNamespace(
        _watched_sync_running=False,
        trakt=SimpleNamespace(is_connected=lambda: True,
                              watched_movies=lambda _t: {},
                              watched_shows=lambda _t: {},
                              watchlist_movies=lambda: set(),
                              watchlist_shows=lambda: set()),
        watched=SimpleNamespace(replace=broken),
        watchlist=SimpleNamespace(replace=lambda *_a: None))
    w._on_watched_sync_failed = lambda: _TraktMixin._on_watched_sync_failed(w)
    w._push_local_watched_to_trakt = lambda: None
    w._watch_accounts = lambda: _TraktMixin._watch_accounts(w)
    _TraktMixin._maybe_sync_watched(w, force=True)
    assert w._watched_sync_running is False
