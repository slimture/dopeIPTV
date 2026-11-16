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
import http.client
import json
import os
import re
import subprocess
import sys

import pytest

from dopeiptv.core import phone_page, phone_remote
from dopeiptv.core.phone_remote import PhoneServer, RateLimiter

TOKEN = "Ab3_-x9Ab3_-x9Ab3_-x9Ab3_-x9Qz12"
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)


def ask(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = dict(headers or {})
    data = body
    if body is not None and not isinstance(body, bytes):
        data = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
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
    assert code == 301 and h["location"] == f"/{TOKEN}/"
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
    code, h, raw = ask(srv.port, "GET", f"/{TOKEN}/calendar.ics")
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
    assert phone_remote.page_url("192.168.1.23", 3674, TOKEN) == f"http://192.168.1.23:3674/{TOKEN}/"
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
                     "api/play_ref", "api/browse", "api/episodes", "api/seek", "api/tv_text", "api/tracks", "api/track", "api/sleep", "logo/", "thumb/"}
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

def ask(port, method, path, body=None):
    out = {}
    def run():
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            h = {}
            data = None
            if body is not None:
                data = json.dumps(body).encode(); h["Content-Type"] = "application/json"
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
assert url.endswith(f":{port}/{token}/"), url
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
code, ics = ask(port, "GET", base + "/calendar.ics")
assert code == 200 and b"SUMMARY:Film" in ics and b"BEGIN:VCALENDAR" in ics

# A new address: the old one is shut out at once, the new one works.
w.phone_remote_new_token()
assert w._phone.token != token and s.value("phone_remote_token") == w._phone.token
assert ask(w._phone.port, "GET", base + "/api/state")[0] == 403
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
