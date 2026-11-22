"""The phone remote (core/phone_remote.py, core/phone_page.py, ui/mw_phone.py),
asked as a phone's browser asks: nothing at all without the token in the
address, a client that keeps guessing shut out, too many requests slowed,
reading with GET and changing with POST, only the page and what it needs
(never the remote API), a logo only when it is a picture, the page itself
with nothing from elsewhere and a policy that allows only its own script
and style; and the window doing what the phone asks, on its own thread,
for the channels its lists show."""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import os
import re
import subprocess
import sys
import time

import pytest

from dopeiptv.core import phone_page, phone_remote
from dopeiptv.core.phone_remote import PhoneServer, RateLimiter
from tests.phone_sign import signed_request

TOKEN = "Ab3_-x9Ab3_-x9Ab3_-x9Ab3_-x9Qz12"
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)


def ask(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    path, data, h = signed_request(TOKEN, method, path, body, headers)
    conn.request(method, path, body=data, headers=h)
    r = conn.getresponse()
    raw = r.read()
    conn.close()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, raw


class Window:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.answers: dict = {}

    def __call__(self, action, params):
        self.calls.append((action, dict(params)))
        a = self.answers.get(action)
        if isinstance(a, BaseException):
            raise a
        return a if a is not None else {"ok": True}


def page():
    return phone_page.render({"bg": "#17171C", "text": "#ECECF1", "accent": "#4C8DFF"},
                             {"bg": "#F5F5F7", "text": "#1B1B1F", "accent": "#4C8DFF"},
                             {"channels": "Channels"})


@pytest.fixture
def served():
    started = []

    def start(window=None, limiter=None):
        srv = PhoneServer(0, TOKEN, window or Window(), page, host="127.0.0.1", limiter=limiter)
        srv.start()
        started.append(srv)
        return srv
    yield start
    for srv in started:
        srv.stop()


# -- the limiter ----------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_a_client_asking_too_often_is_slowed_and_let_in_again():
    clock = Clock()
    lim = RateLimiter(rate=2.0, burst=5, clock=clock)
    assert all(lim.allow("10.0.0.5") for _ in range(5))
    assert not lim.allow("10.0.0.5")
    assert lim.allow("10.0.0.6")                     # another phone is not held up
    clock.t += 0.5                                   # one more by now
    assert lim.allow("10.0.0.5") and not lim.allow("10.0.0.5")
    clock.t += 60
    assert sum(lim.allow("10.0.0.5") for _ in range(10)) == 5     # never more than the burst


def test_a_client_that_keeps_guessing_is_shut_out_for_a_while():
    clock = Clock()
    lim = RateLimiter(fails=3, window=60, lockout=600, clock=clock)
    assert not lim.failed("10.0.0.9") and not lim.failed("10.0.0.9")
    assert not lim.blocked("10.0.0.9")
    assert lim.failed("10.0.0.9") and lim.blocked("10.0.0.9")
    assert not lim.blocked("10.0.0.8")
    clock.t += 599
    assert lim.blocked("10.0.0.9")
    clock.t += 2
    assert not lim.blocked("10.0.0.9")
    # Wrong guesses far apart never add up.
    for _ in range(5):
        clock.t += 61
        assert not lim.failed("10.0.0.7")


def test_the_whole_server_has_a_budget_beside_each_address_s():
    """A device that asks from address after address gets a fresh bucket
    with each - never more than the whole server's budget, all addresses
    together. Lock-outs stay per address; this computer is neither shut out
    nor counted in the whole."""
    clock = Clock()
    lim = RateLimiter(rate=2.0, burst=5, total_rate=4.0, total_burst=10, clock=clock,
                      spare=phone_remote.this_computer)
    assert sum(lim.allow(f"192.168.7.{i}") for i in range(50)) == 10
    assert not lim.allow("192.168.7.200"), "a fresh address: the whole is spent"
    assert all(lim.allow("127.0.0.1") for _ in range(5)), "this computer is not counted"
    assert not lim.allow("127.0.0.1"), "but has its own bucket"
    clock.t += 1.0                                        # four more for everyone
    assert sum(lim.allow(f"10.1.0.{i}") for i in range(20)) == 4
    # A refusal by the whole does not spend that address's own bucket.
    clock.t += 100
    lim2 = RateLimiter(rate=0.001, burst=3, total_rate=0.001, total_burst=1, clock=clock)
    assert lim2.allow("10.0.0.1") and not lim2.allow("10.0.0.2")
    assert lim2._buckets["10.0.0.2"][0] >= 3 - 1e-6
    # Ten wrong tokens shut one address out, never another, never loopback.
    lim3 = RateLimiter(fails=10, total_rate=60.0, total_burst=360.0, clock=clock,
                       spare=phone_remote.this_computer)
    for _ in range(10):
        lim3.failed("192.168.1.20")
        assert not lim3.failed("127.0.0.1") and not lim3.failed("::1")
    assert lim3.blocked("192.168.1.20") and not lim3.blocked("192.168.1.21")
    assert not lim3.blocked("127.0.0.1") and not lim3.blocked("::1")


def test_the_whole_budget_never_bothers_real_phones():
    """Four phones polling twice a second and Home Assistant every second,
    for a minute: never refused by the default budgets."""
    from dopeiptv.core import phone_remote as pr
    clock = Clock()
    lim = RateLimiter(total_rate=pr.SERVER_RATE, total_burst=pr.SERVER_BURST, clock=clock)
    pics = RateLimiter(rate=pr.PICTURES_RATE, burst=pr.PICTURES_BURST,
                       total_rate=pr.PICTURES_SERVER_RATE, total_burst=pr.PICTURES_SERVER_BURST,
                       clock=clock)
    for step in range(120):
        clock.t += 0.5
        for phone in range(4):
            assert lim.allow(f"192.168.1.{30 + phone}")
        if step % 2 == 0:
            assert lim.allow("192.168.1.10")             # Home Assistant
    # Each phone scrolling a list at once: its posters.
    assert all(pics.allow(f"192.168.1.{30 + phone}") for phone in range(4) for _ in range(150))


def test_a_server_answers_429_once_its_whole_budget_is_spent(served):
    srv = served(limiter=RateLimiter(rate=100.0, burst=100.0, total_rate=0.001, total_burst=3))
    codes = [ask(srv.port, "GET", f"/{TOKEN}/api/state")[0] for _ in range(5)]
    assert codes[:3] == [200] * 3 and set(codes[3:]) == {429}
    # This computer, as the server's own limiter spares it: never held by the whole.
    srv2 = served(limiter=RateLimiter(rate=100.0, burst=100.0, total_rate=0.001, total_burst=1,
                                      spare=phone_remote.this_computer))
    assert [ask(srv2.port, "GET", f"/{TOKEN}/api/state")[0] for _ in range(5)] == [200] * 5
    # The default: a whole-server budget on both buckets, this computer spared.
    srv3 = served()
    assert srv3.limiter.total_rate == phone_remote.SERVER_RATE
    assert srv3.pictures.total_rate == phone_remote.PICTURES_SERVER_RATE
    for lim in (srv3.limiter, srv3.pictures):
        assert lim.spare is phone_remote.this_computer
        for _ in range(12):
            lim.failed("127.0.0.1")
        assert not lim.blocked("127.0.0.1")


def test_the_limiter_remembers_a_bounded_number_of_clients():
    lim = RateLimiter(clients=8)
    for i in range(50):
        lim.allow(f"10.0.1.{i}")
        lim.failed(f"10.0.2.{i}")
    assert len(lim._buckets) <= 8 and len(lim._failures) <= 8


# -- the server ---------------------------------------------------------------------------------

def test_nothing_without_the_token(served):
    srv = served()
    port = srv.port
    for path in ("/", "/api/state", "/playlist.m3u", "/favicon.ico", f"/{TOKEN[:-1]}/", f"/{TOKEN}x/api/state",
                 f"/x/{TOKEN}/"):
        code, h, raw = ask(port, "GET", path)
        assert code == 403 and raw == b"forbidden", path
        assert h["cache-control"] == "no-store" and h["x-content-type-options"] == "nosniff"
    assert ask(port, "POST", "/api/toggle", {})[0] == 403
    assert srv.call.calls == []


def test_the_token_opens_the_page_and_only_what_it_needs(served):
    w = Window()
    w.answers["state"] = {"state": "stopped", "volume": 40}
    srv = served(w)
    port = srv.port
    code, h, _raw = ask(port, "GET", f"/{TOKEN}")
    assert code == 301 and h["location"] == "/r/"
    code, h, raw = ask(port, "GET", f"/{TOKEN}/")
    assert code == 200 and h["content-type"] == "text/html; charset=utf-8"
    assert raw.startswith(b"<!doctype html>") and h["x-frame-options"] == "DENY"
    assert "default-src 'none'" in h["content-security-policy"]
    assert h["referrer-policy"] == "no-referrer"
    code, _h, raw = ask(port, "GET", f"/{TOKEN}/api/state")
    assert code == 200 and json.loads(raw) == {"state": "stopped", "volume": 40}
    for name in ("toggle", "stop", "next", "previous", "mute"):
        assert ask(port, "POST", f"/{TOKEN}/api/{name}")[0] == 200
    assert ask(port, "POST", f"/{TOKEN}/api/volume", {"value": 30})[0] == 200
    assert ask(port, "POST", f"/{TOKEN}/api/play", {"id": "401"})[0] == 200
    assert [c[0] for c in w.calls] == ["state", "toggle", "stop", "next", "previous", "mute", "volume", "play"]
    assert w.calls[-2][1] == {"value": 30} and w.calls[-1][1] == {"id": "401"}
    # Reading is GET, changing is POST, and nothing else is there.
    assert ask(port, "GET", f"/{TOKEN}/api/toggle")[0] == 405
    assert ask(port, "POST", f"/{TOKEN}/api/state")[0] == 405
    for path in ("/api/status", "/api/sources", "/playlist.m3u", "/guide.xml.gz", "/stream/a/1", "/nope"):
        assert ask(port, "GET", f"/{TOKEN}{path}")[0] == 404, path
    assert ask(port, "PUT", f"/{TOKEN}/api/toggle")[0] == 405
    n = len(w.calls)
    assert ask(port, "POST", f"/{TOKEN}/api/play", b"[1, 2]", {"Content-Type": "application/json"})[0] == 400
    assert ask(port, "POST", f"/{TOKEN}/api/play", b"x" * (phone_remote.MAX_BODY + 1))[0] == 400
    assert len(w.calls) == n


def test_a_phone_connecting_is_told_once_per_address():
    told = []
    srv = PhoneServer(0, TOKEN, Window(), page, host="127.0.0.1", on_client=told.append)
    srv.start()
    try:
        assert ask(srv.port, "GET", "/favicon.ico")[0] == 403         # no token: not a phone
        assert ask(srv.port, "GET", f"/{TOKEN}")[0] == 301             # the redirect: not yet
        assert told == []
        assert ask(srv.port, "GET", f"/{TOKEN}/")[0] == 200
        assert ask(srv.port, "GET", f"/{TOKEN}/api/state")[0] == 200
        assert told == ["127.0.0.1"]
    finally:
        srv.stop()
    assert srv.note_client("192.168.1.9") and not srv.note_client("192.168.1.9")
    assert told == ["127.0.0.1", "192.168.1.9"]


def test_what_goes_wrong_in_the_window_comes_back_as_an_answer(served):
    w = Window()
    srv = served(w)
    w.answers["play"] = ValueError("no such channel")
    code, _h, raw = ask(srv.port, "POST", f"/{TOKEN}/api/play", {"id": "9"})
    assert code == 400 and json.loads(raw)["error"] == "no such channel"
    w.answers["state"] = TimeoutError("state")
    assert ask(srv.port, "GET", f"/{TOKEN}/api/state")[0] == 503
    w.answers["state"] = RuntimeError("boom")
    code, _h, raw = ask(srv.port, "GET", f"/{TOKEN}/api/state")
    assert code == 500 and json.loads(raw) == {"error": "RuntimeError"}
    w.answers.clear()
    assert ask(srv.port, "GET", f"/{TOKEN}/api/state")[0] == 200


def test_a_client_that_keeps_guessing_gets_nothing_more_even_with_the_token(served):
    srv = served(limiter=RateLimiter(fails=4))
    for _ in range(3):
        assert ask(srv.port, "GET", "/" + "g" * 32 + "/")[0] == 403
    # The browser's own asks (a short path) are no guesses.
    for _ in range(10):
        assert ask(srv.port, "GET", "/favicon.ico")[0] == 403
    assert ask(srv.port, "GET", f"/{TOKEN}/api/state")[0] == 200
    assert ask(srv.port, "GET", "/" + "h" * 32 + "/")[0] == 403
    code, h, _raw = ask(srv.port, "GET", f"/{TOKEN}/api/state")
    assert code == 429 and h["retry-after"]


def test_a_web_page_changes_nothing_even_with_the_token(served):
    """A page of another site in the phone's browser - a form posting
    text/plain that reads as JSON, a fetch - is refused; the remote's own
    page (same origin) is not."""
    w = Window()
    srv = served(w)
    own = f"http://127.0.0.1:{srv.port}"
    for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"},
                    {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
                    {"Origin": "http://127.0.0.1:1", "Sec-Fetch-Site": "same-site"}):
        code, _h, _raw = ask(srv.port, "POST", f"/{TOKEN}/api/volume", b'{"value": 99}',
                             {"Content-Type": "text/plain", **headers})
        assert code == 403, headers
    assert w.calls == []
    for headers in ({}, {"Origin": own, "Sec-Fetch-Site": "same-origin"},
                    {"Sec-Fetch-Site": "none"}):
        assert ask(srv.port, "POST", f"/{TOKEN}/api/volume", {"value": 30}, headers)[0] == 200
    assert [c[0] for c in w.calls] == ["volume"] * 3
    assert ask(srv.port, "GET", f"/{TOKEN}/", headers={"Sec-Fetch-Site": "cross-site",
                                                        "Sec-Fetch-Mode": "navigate"})[0] == 200


def test_a_web_page_s_guesses_never_shut_the_phone_out(served):
    srv = served(limiter=RateLimiter(fails=3))
    for _ in range(6):
        assert ask(srv.port, "GET", "/" + "g" * 32 + "/api/state",
                   headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
        assert ask(srv.port, "POST", "/" + "g" * 32 + "/api/stop", {},
                   {"Origin": "https://evil.example"})[0] == 403
    assert not srv.limiter.blocked("127.0.0.1")
    assert ask(srv.port, "GET", f"/{TOKEN}/api/state")[0] == 200
    for _ in range(3):
        assert ask(srv.port, "GET", "/" + "g" * 32 + "/")[0] == 403
    assert ask(srv.port, "GET", f"/{TOKEN}/api/state")[0] == 429, "the phone's own guesses as before"


def test_a_name_a_dns_rebinding_pointed_here_is_refused_and_never_counted(served):
    """A web page whose own name was made to point at this computer asks
    under that name: refused before anything is counted, so its tries -
    without Origin or Fetch Metadata - never shut the phone out."""
    srv = served(limiter=RateLimiter(fails=3))
    for _ in range(6):
        code = ask(srv.port, "GET", "/" + "g" * 32 + "/api/state",
                   headers={"Host": "attacker.example:%d" % srv.port})[0]
        assert code == 421, code
        assert ask(srv.port, "POST", f"/{TOKEN}/api/stop", {},
                   {"Host": "rebind.attacker.example"})[0] == 421
    assert not srv.limiter.blocked("127.0.0.1")
    for host in (f"127.0.0.1:{srv.port}", f"192.168.1.20:{srv.port}", "livingroom.local",
                 "mypc", f"[::1]:{srv.port}"):
        assert ask(srv.port, "GET", f"/{TOKEN}/api/state", headers={"Host": host})[0] == 200, host


def test_a_flood_is_slowed(served):
    srv = served(limiter=RateLimiter(rate=0.5, burst=6))
    codes = [ask(srv.port, "GET", f"/{TOKEN}/api/state")[0] for _ in range(10)]
    assert codes[:6] == [200] * 6 and set(codes[6:]) == {429}


def test_a_logo_only_when_it_is_a_picture(served, tmp_path):
    png = tmp_path / "a"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 64)
    svg = tmp_path / "b"
    svg.write_bytes(b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>")
    big = tmp_path / "c"
    big.write_bytes(b"\xff\xd8\xff" + b"\0" * phone_remote.LOGO_MAX)
    w = Window()
    srv = served(w)
    for name, path in (("png", png), ("svg", svg), ("big", big), ("gone", tmp_path / "nope")):
        w.answers["logo"] = str(path)
        code, h, raw = ask(srv.port, "GET", f"/{TOKEN}/logo/{name}")
        if name == "png":
            assert code == 200 and h["content-type"] == "image/png" and raw == png.read_bytes()
            assert h["cache-control"].startswith("private")
        else:
            assert code == 404, name
    assert w.calls[-1] == ("logo", {"id": "gone"})
    assert phone_remote.image_type(b"RIFF\0\0\0\0WEBPVP8 ") == "image/webp"
    assert phone_remote.image_type(b"GIF89a....") == "image/gif"


def test_the_calendar_with_the_token(served):
    w = Window()
    w.answers["calendar"] = "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"
    srv = served(w)
    code, h, raw = ask(srv.port, "GET", f"/c/{phone_remote.calendar_token(TOKEN)}/calendar.ics")
    assert code == 200 and h["content-type"] == "text/calendar; charset=utf-8"
    assert raw == b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"
    assert ask(srv.port, "GET", "/calendar.ics")[0] == 403


def test_it_listens_on_the_home_network_and_a_taken_port_is_said():
    srv = PhoneServer(0, TOKEN, Window(), page)
    assert srv.host == "0.0.0.0"
    srv.start()
    try:
        with pytest.raises(OSError):
            PhoneServer(srv.port, TOKEN, Window(), page).start()
    finally:
        srv.stop()
    assert not srv.running
    assert phone_remote.page_url("192.168.1.23", 3674, TOKEN) == f"http://192.168.1.23:3674/r/#{TOKEN}"
    assert len(phone_remote.new_token()) >= 32 and phone_remote.new_token() != phone_remote.new_token()


# -- the page -------------------------------------------------------------------------------

def _sha(text):
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


def test_the_page_is_whole_in_itself_and_its_policy_allows_only_it():
    words = {k: k.upper() for k in phone_page.WORDS}
    words["offline"] = "</script><script>alert(1)</script>"
    dark = {"bg": "#17171C", "text": "#ECECF1", "accent": "#4C8DFF", "on_accent": "#FFFFFF",
            "pane": "red;}body{display:none"}
    light = {"bg": "#F5F5F7", "text": "#1B1B1F", "accent": "#4C8DFF"}
    html_bytes, csp = phone_page.render(dark, light, words, "sv")
    html = html_bytes.decode()
    script = re.search(r"<script>(.*)</script>", html, re.S).group(1)
    style = re.search(r"<style>(.*)</style>", html, re.S).group(1)
    assert html.count("<script>") == 1 and html.count("</script>") == 1   # the word did not break out
    assert f"script-src {_sha(script)};" in csp and f"style-src {_sha(style)};" in csp
    assert "connect-src 'self'" in csp and "img-src 'self' data:" in csp and "unsafe" not in csp
    # Nothing from anywhere else: no address but its own.
    assert not re.search(r"(src|href)=\"(https?:)?//", html)
    assert "http://" not in script and "https://" not in script.replace("http://www.w3.org/2000/svg", "")
    # The app's colours, dark and light, and nothing that is not one.
    assert "--bg:#17171c" in style and "--bg:#f5f5f7" in style
    assert "prefers-color-scheme: light" in style and "display:none" not in style.split("}")[0]
    assert "--pane:" not in style and "red;" not in style            # not a colour: left out
    assert '<html lang="sv" dir="ltr">' in html
    assert '"channels": "CHANNELS"' in script or '"channels":"CHANNELS"' in script.replace(" ", "")
    rtl = phone_page.render(dark, light, words, "ar", rtl=True)[0].decode()
    assert '<html lang="ar" dir="rtl">' in rtl
    assert '<html lang="en"' in phone_page.render(dark, light, words, 'en"><script>')[0].decode()


def test_the_page_asks_only_its_own_server():
    script = phone_page._JS
    asked = set(re.findall(r'"(api/[a-z_]+|logo/|thumb/)[?"]', script))
    assert asked == {"api/state", "api/channels", "api/play", "api/toggle", "api/stop", "api/next",
                     "api/previous", "api/mute", "api/volume", "api/tv", "api/tv_key", "api/touch", "api/profile",
                     "api/play_ref", "api/browse", "api/episodes", "api/seek", "api/tv_text", "api/tracks",
                     "api/track", "api/sleep", "api/rec", "api/rec_stop", "api/rec_next", "api/rec_go",
                     "api/rec_ask", "logo/", "thumb/"}
    assert {a[4:] for a in asked if a.startswith("api/")} <= phone_remote.READ | phone_remote.WRITE


# -- the window ---------------------------------------------------------------------------------

_CHILD = r"""
import faulthandler, json, os, socket, sys, tempfile, threading, http.client, time
faulthandler.dump_traceback_later(150, exit=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
app = QApplication.instance() or QApplication([])
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import make_client
from dopeiptv.ui import main_window as mw
from dopeiptv.ui import mw_phone
from tests.test_sources_side_by_side import Xtream
from tests.phone_sign import signed_request
from dopeiptv.core import phone_remote

def _failed(*exc):
    import traceback
    traceback.print_exception(*exc)
    sys.stderr.flush()
    os._exit(1)
sys.excepthook = _failed

xt = Xtream()
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
pls = [{"id": "a", "name": "Main", "kind": "xtream", "server": xt.base, "username": "anna", "password": "pw-anna"}]
s.setValue("playlists", json.dumps(pls))
s.setValue("active_playlist", "a")
store = PlaylistStore(s)
w = mw.MainWindow(make_client(pls[0]), s, store)
played, media = [], []
def start_playback(url, title, icon, key, kind, record=True, item=None, catchup=False):
    played.append((url, title, kind))
    w._last_playback = {"url": url, "title": title, "kind": kind, "item": item or {}}
w._start_playback = start_playback
w._media_active = lambda: bool(played)
w.mpris_command = lambda name, arg=None: media.append((name, arg))
w._come_forward = lambda: None

def ask(port, method, path, body=None, key=None):
    out = {}
    path, data, h = signed_request(key or w._phone.token, method, path, body)
    def run():
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            c.request(method, path, body=data, headers=h)
            r = c.getresponse(); out["code"] = r.status; out["raw"] = r.read(); c.close()
        except Exception as e:
            out["error"] = e
    t = threading.Thread(target=run); t.start()
    while t.is_alive():
        app.processEvents(); t.join(0.01)
    if "error" in out:
        raise out["error"]
    raw = out["raw"]
    return out["code"], (json.loads(raw) if raw[:1] in (b"{", b"[") else raw)

def wait(cond, n=500):
    for _ in range(n):
        app.processEvents(); w.pool.waitForDone(5)
        if cond():
            return True
    return False

# Off until it is turned on.
assert not w.phone_remote_running() and w.phone_remote_url() == ""
w._phone_apply()
assert not w.phone_remote_running()
assert w.phone_remote_start()
port, token = w._phone.port, w._phone.token
assert len(token) >= 30 and s.value("phone_remote") == "true" and s.value("phone_remote_token") == token
assert int(s.value("phone_remote_port")) == port and w._phone.host == "0.0.0.0"
url = w.phone_remote_url()
assert url.endswith(f":{port}/r/#{token}"), url
base = f"/{token}"

code, html = ask(port, "GET", base + "/")
assert code == 200 and b"dopeIPTV" in html and b"Channels" in html
code, st = ask(port, "GET", base + "/api/state")
assert code == 200 and st["state"] == "stopped" and st["source"] == "Main" and st["stamp"], st
code, ch = ask(port, "GET", base + "/api/channels")
assert [c["name"] for c in ch["channels"]] == ["TV4 HD", "Kanal 5"] and ch["favorites"] == [], ch
assert ch["channels"][0] == {"id": 401, "name": "TV4 HD", "number": 1, "logo": False}, ch

# A favourite: listed as one, and the page's lists are told to look again.
w.favs.add("all", {"name": "Kanal 5", "stream_id": 405, "category_id": "1"})
code, st2 = ask(port, "GET", base + "/api/state")
assert st2["stamp"] != st["stamp"]
assert ask(port, "GET", base + "/api/channels")[1]["favorites"] == [405]

# Play from the phone: the window plays it, on its own thread.
code, out = ask(port, "POST", base + "/api/play", {"id": "401"})
assert code == 200 and played[-1] == (xt.base + "/live/anna/pw-anna/401.ts", "TV4 HD", "live"), played
assert ask(port, "POST", base + "/api/play", {"id": "999"})[0] == 400
# What plays now, as the phone shows it.
code, st = ask(port, "GET", base + "/api/state")
assert st["kind"] == "live" and st["channel"]["id"] == 401 and st["channel"]["number"] == 1, st

# Channel up and down from the one playing, through the whole source.
w.mode = "vod"
ask(port, "POST", base + "/api/next")
assert wait(lambda: played[-1][1] == "Kanal 5"), played
ask(port, "POST", base + "/api/next")
assert wait(lambda: played[-1][1] == "TV4 HD"), played
ask(port, "POST", base + "/api/previous")
assert wait(lambda: played[-1][1] == "Kanal 5"), played

# A category hidden in the lists is hidden from the phone too, and cannot be played.
w.overrides.excluded_ids = lambda kind, include_locked=True: {"1"}
code, ch = ask(port, "GET", base + "/api/channels")
assert ch["channels"] == [] and ch["favorites"] == []
n = len(played)
assert ask(port, "POST", base + "/api/play", {"id": "401"})[0] == 400 and len(played) == n
del w.overrides.excluded_ids

# The controls: the same as the media keys.
ask(port, "POST", base + "/api/toggle"); ask(port, "POST", base + "/api/stop")
code, out = ask(port, "POST", base + "/api/volume", {"value": 140})
assert out["volume"] == 100 and media[-3:] == [("playpause", None), ("stop", None), ("volume", 1.0)], media
assert ask(port, "POST", base + "/api/volume", {"value": "loud"})[0] == 400
if w.player is not None:
    code, out = ask(port, "POST", base + "/api/mute")
    assert out["muted"] is True
    code, st = ask(port, "GET", base + "/api/state")
    assert st["muted"] is True
    ask(port, "POST", base + "/api/mute")

# TV mode from the phone: the state says it; its keys go TV mode's own way in.
keys = []
w._tv_input = keys.append
assert ask(port, "GET", base + "/api/state")[1]["tv_mode"] is False
n_played, n_media = len(played), len(media)
for a in ("up", "ok", "left", "red", "digit_5", "back", "menu"):        # off: playback left alone
    assert ask(port, "POST", base + "/api/tv_key", {"action": a}) == (200, {"ok": False, "tv_mode": False}), a
assert (len(played), len(media), keys) == (n_played, n_media, [])
code, out = ask(port, "POST", base + "/api/tv", {"mode": "on"})
assert out == {"ok": True, "tv_mode": True} and w._tv_on, out
assert ask(port, "GET", base + "/api/state")[1]["tv_mode"] is True
ask(port, "POST", base + "/api/tv_key", {"action": "down"})
ask(port, "POST", base + "/api/touch", {"dx": 0, "dy": 0, "ms": 60})
assert keys == ["down", "ok"], keys
assert ask(port, "POST", base + "/api/tv_key", {"action": "reboot"})[0] == 400
code, out = ask(port, "POST", base + "/api/tv", {"mode": "toggle"})
assert out["tv_mode"] is False and not w._tv_on
del w._tv_input

# The calendar, for a calendar app on the network.
w.rec.add_job("http://127.0.0.1:9/x.ts", "Film", time.time() + 3600, time.time() + 7200, channel="SVT2")
code, ics = ask(port, "GET", "/c/" + phone_remote.calendar_token(token) + "/calendar.ics")
assert code == 200 and b"SUMMARY:Film" in ics and b"BEGIN:VCALENDAR" in ics

# A new address: the old one is shut out at once, the new one works.
w.phone_remote_new_token()
assert w._phone.token != token and s.value("phone_remote_token") == w._phone.token
assert ask(w._phone.port, "GET", base + "/api/state", key=token)[0] == 401
assert ask(w._phone.port, "GET", f"/{w._phone.token}/api/state")[0] == 200
# On again at the next start, with the same address.
port, token = w._phone.port, w._phone.token
w.phone_remote_stop(keep=True)
assert s.value("phone_remote") == "true"
w._phone_apply()
assert w._phone.port == port and w._phone.token == token
# Stop: off, the port closed, and off at the next start.
w.phone_remote_stop()
assert s.value("phone_remote") == "false" and not w.phone_remote_running()
try:
    # Windows tries a refused connection to this computer again for about
    # two seconds before it says so: room for that, not a timeout.
    socket.create_connection(("127.0.0.1", port), timeout=10).close()
    raise AssertionError("still listening")
except ConnectionRefusedError:
    pass
w._phone_apply()
assert not w.phone_remote_running()
# The remote API stays as Nerd has it: the phone remote never opened it.
assert w._remote is None

# The dialog: off, then on with the code and the address, then a new one, then stopped.
from dopeiptv.ui.phone_remote import PhoneRemoteDialog
d = PhoneRemoteDialog(w)
assert d.on_btn.isVisibleTo(d) and not d.code.isVisibleTo(d)
d.on_btn.click()
assert w.phone_remote_running() and d.code.isVisibleTo(d) and d.address.text() == w.phone_remote_url()
code_now = d.code.code
assert code_now is not None and code_now.level == "M"
d.new_btn.click()
assert d.address.text() == w.phone_remote_url() and d.code.code.modules != code_now.modules
d.copy.click()
assert QApplication.clipboard().text() == d.address.text()
d.stop_btn.click()
assert not w.phone_remote_running() and d.on_btn.isVisibleTo(d)
d.close()

# A phone connecting (told on the server's thread): a note says so and the
# window with the code goes - its job is done.
from dopeiptv.i18n import tr
notes = []
w._show_toast = lambda text, *a, **k: notes.append(text)
d = PhoneRemoteDialog(w)
w._phone_dialog = d
d.show(); app.processEvents()
threading.Thread(target=w._phone_client, args=("192.168.1.9",)).start()
for _ in range(300):
    app.processEvents()
    if notes and not d.isVisible():
        break
    time.sleep(0.01)
assert notes == [tr("phone_connected")] and not d.isVisible(), notes
w._phone_dialog = None
xt.close()
print("PHONE_WINDOW_OK", flush=True)
os._exit(0)
"""


def test_the_window_does_what_the_phone_asks(tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        env[var] = str(tmp_path / var.lower())
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert "PHONE_WINDOW_OK" in out.stdout, out.stdout + out.stderr[-4000:]


def test_the_code_is_drawn_whole_modules_on_a_light_plate(tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtGui import QColor, QImage, QPainter
    from PyQt6.QtWidgets import QApplication

    from dopeiptv.ui.phone_remote import QUIET, QrView
    app = QApplication.instance() or QApplication([])  # noqa: F841
    view = QrView(side=232)
    view.resize(232, 232)
    view.set_text("http://192.168.1.23:3674/" + TOKEN + "/")
    code = view.code
    n = code.size + 2 * QUIET
    px = view.module_px()
    assert px == 232 // n and px >= 3
    img = QImage(232, 232, QImage.Format.Format_RGB32)
    img.fill(QColor(128, 128, 128))
    p = QPainter(img)
    view.render(p)
    p.end()
    off = (232 - px * n) // 2
    for y in range(code.size):
        for x in range(code.size):
            cx, cy = off + (x + QUIET) * px + px // 2, off + (y + QUIET) * px + px // 2
            shade = QColor(img.pixel(cx, cy)).lightness()
            assert (shade < 40) == code.modules[y][x], (x, y, shade)
    assert QColor(img.pixel(off + 1, off + 1)).lightness() > 240          # the quiet zone is white


# -- the key never crosses the network ------------------------------------------------------

def _plain(port, method, path, body=b"", headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=body or None, headers=headers or {})
    r = conn.getresponse()
    raw = r.read()
    conn.close()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, raw


def test_the_code_holds_the_key_after_the_hash_which_no_browser_sends():
    url = phone_remote.page_url("192.168.1.5", 8899, TOKEN)
    assert url == f"http://192.168.1.5:8899/r/#{TOKEN}"
    assert phone_remote.page_url("fd00::5", 8899, TOKEN).startswith("http://[fd00::5]:8899/r/#")
    cal = phone_remote.calendar_url("192.168.1.5", 8899, TOKEN)
    assert TOKEN not in cal and cal.endswith("/calendar.ics")


def test_a_request_the_page_did_not_sign_is_refused_and_one_seen_cannot_be_sent_again(served):
    w = Window()
    w.answers["state"] = {"state": "stopped"}
    srv = served(w)
    port = srv.port
    # The page, its manifest: nothing of the person's, no key needed.
    assert _plain(port, "GET", "/r/")[0] == 200
    code, h, _raw = _plain(port, "GET", "/r/api/state")
    assert code == 401 and int(h["x-remote-now"]) > 0
    stamp, nonce = str(int(time.time() * 1000)), "n" * 24
    good = {"X-Remote-Time": stamp, "X-Remote-Nonce": nonce,
            "X-Remote-Sign": phone_remote.signature(TOKEN, "GET", "/r/api/state", stamp, nonce)}
    assert _plain(port, "GET", "/r/api/state", headers=good)[0] == 200
    assert _plain(port, "GET", "/r/api/state", headers=good)[0] == 401, "seen once, never again"
    # Changed on the way: another address, another body, another time.
    for method, path, body in (("GET", "/r/api/channels", b""), ("POST", "/r/api/stop", b"{}")):
        h2 = dict(good, **{"X-Remote-Nonce": "m" * 24})
        h2["X-Remote-Sign"] = phone_remote.signature(TOKEN, "GET", "/r/api/state", stamp, "m" * 24)
        assert _plain(port, method, path, body, h2)[0] == 401, path
    old = str(int(time.time() * 1000) - 3 * phone_remote.SIGN_WINDOW_MS)
    stale = {"X-Remote-Time": old, "X-Remote-Nonce": "o" * 24,
             "X-Remote-Sign": phone_remote.signature(TOKEN, "GET", "/r/api/state", old, "o" * 24)}
    assert _plain(port, "GET", "/r/api/state", headers=stale)[0] == 401
    body = b'{"value": 30}'
    post = {"X-Remote-Time": stamp, "X-Remote-Nonce": "p" * 24, "Content-Type": "application/json",
            "X-Remote-Sign": phone_remote.signature(TOKEN, "POST", "/r/api/volume", stamp, "p" * 24, body)}
    assert _plain(port, "POST", "/r/api/volume", body, post)[0] == 200
    other = b'{"value": 99}'
    post2 = dict(post, **{"X-Remote-Nonce": "q" * 24})
    post2["X-Remote-Sign"] = phone_remote.signature(TOKEN, "POST", "/r/api/volume", stamp, "q" * 24, body)
    assert _plain(port, "POST", "/r/api/volume", other, post2)[0] == 401, "the body is signed too"
    assert [c for c in w.calls if c[0] == "volume"] == [("volume", {"value": 30})]


def test_a_picture_needs_its_signed_address(served):
    w = Window()
    w.answers["logo"] = ""
    srv = served(w)
    port = srv.port
    assert _plain(port, "GET", "/r/logo/401")[0] == 401
    until = str(int(time.time() * 1000) + 60_000)
    sign = phone_remote.signature(TOKEN, "GET", "/r/logo/401", until, "picture")
    assert _plain(port, "GET", f"/r/logo/401?x={until}&s={sign}")[0] == 404      # signed: no such logo
    assert _plain(port, "GET", f"/r/logo/402?x={until}&s={sign}")[0] == 401      # another picture's
    far = str(int(time.time() * 1000) + 10 * phone_remote.PICTURE_TTL_MS)
    far_sign = phone_remote.signature(TOKEN, "GET", "/r/logo/401", far, "picture")
    assert _plain(port, "GET", f"/r/logo/401?x={far}&s={far_sign}")[0] == 401    # too far ahead


def test_an_address_of_before_makes_a_new_key_at_once(served):
    srv = served()
    leaked: list = []
    srv.on_leaked = leaked.append
    code, h, raw = _plain(srv.port, "GET", f"/{TOKEN}/")
    assert code == 410 and b"scan its code again" in raw
    assert leaked == [srv.token] and srv.token != TOKEN
    stamp, nonce = str(int(time.time() * 1000)), "z" * 24
    old_sign = {"X-Remote-Time": stamp, "X-Remote-Nonce": nonce,
                "X-Remote-Sign": phone_remote.signature(TOKEN, "GET", "/r/api/state", stamp, nonce)}
    assert _plain(srv.port, "GET", "/r/api/state", headers=old_sign)[0] == 401, "the old key opens nothing"


def test_the_calendar_has_a_key_of_its_own_that_opens_nothing_else(served):
    w = Window()
    w.answers["calendar"] = "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"
    srv = served(w)
    cal = phone_remote.calendar_token(TOKEN)
    code, h, raw = _plain(srv.port, "GET", f"/c/{cal}/calendar.ics")
    assert code == 200 and raw.startswith(b"BEGIN:VCALENDAR")
    assert _plain(srv.port, "GET", f"/c/{cal[:-1]}x/calendar.ics")[0] == 403
    assert _plain(srv.port, "GET", f"/{cal}/api/state")[0] == 403


def test_the_page_signs_as_the_server_checks():
    """The page's own SHA-256 and HMAC (plain script: crypto.subtle is there
    only over https) give what core/phone_remote.signature gives."""
    import shutil
    node = shutil.which("node")
    if not node:
        pytest.skip("no node to run the page's script")
    js = phone_page._JS
    start, end = js.index("const K256"), js.index("// The key: after the #")
    msgs = ["", "abc", "GET\n/r/api/state\n1700000000000\nnonce\n", "x" * 55, "y" * 64,
            "z" * 119, "é€😀" * 40]
    prog = js[start:end] + (
        "const out = [];\n"
        f"for (const m of {json.dumps(msgs)}) out.push([hex(sha256(enc.encode(m))),"
        f" hex(hmac(enc.encode({json.dumps(TOKEN)}), enc.encode(m))),"
        f" hex(hmac(enc.encode('k'.repeat(100)), enc.encode(m)))]);\n"
        "process.stdout.write(JSON.stringify(out));\n")
    got = json.loads(subprocess.run([node, "-e", prog], capture_output=True, text=True, timeout=60,
                                    check=True).stdout)
    for m, (sha, mac, long_mac) in zip(msgs, got, strict=True):
        b = m.encode("utf-8")
        assert sha == hashlib.sha256(b).hexdigest(), m
        assert mac == hmac.new(TOKEN.encode(), b, hashlib.sha256).hexdigest(), m
        assert long_mac == hmac.new(b"k" * 100, b, hashlib.sha256).hexdigest(), m
    js_sign = js[js.index("function signed("):js.index("// A picture's address")]
    assert '"\\n"' in js_sign or '"\n"' in js_sign
    assert "location.hash" in js and 'localStorage.setItem("key"' in js
    assert "fetch(path, sent)" in js and js.count(" fetch(") == 1, "every request goes through signedFetch()"
