"""Jellyfin, Emby and Plex in the add-a-source forms and in the window: what
each kind asks for, signing in from the form (a token kept, never the
password), Plex's server picked, and the window's side - what plays reported,
the newer resume point taken, the server's partly watched titles under
Continue watching."""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QObject, QThreadPool  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMenu, QWidget  # noqa: E402

from dopeiptv.core import xdg  # noqa: E402
from dopeiptv.providers import mediaserver, plex  # noqa: E402
from dopeiptv.ui import media_signin  # noqa: E402
from dopeiptv.ui.box_finder import FindOnNetwork  # noqa: E402
from dopeiptv.ui.dialogs import PlaylistDialog  # noqa: E402
from dopeiptv.ui.mw_media import _MediaServerMixin  # noqa: E402
from dopeiptv.ui.welcome import WelcomeOverlay  # noqa: E402
from dopeiptv.i18n import LocalizedError, tr  # noqa: E402

from .test_media_servers import TOKEN, UID, FakeServer, _auth_route  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def fake():
    made: list[FakeServer] = []

    def make(routes):
        s = FakeServer(routes)
        made.append(s)
        return s
    yield make
    for s in made:
        s.close()


@pytest.fixture(autouse=True)
def _device():
    mediaserver.install_device_store(None)
    yield


def _wait(cond, timeout=6.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    QThreadPool.globalInstance().waitForDone(500)
    QApplication.processEvents()
    return bool(cond())


def _shown(w) -> bool:
    return not w.isHidden()


def test_each_media_kind_asks_for_what_it_needs(app):
    d = PlaylistDialog()
    for kind in ("jellyfin", "emby", "plex"):
        assert d.kind.findData(kind) >= 0
    d.kind.setCurrentIndex(d.kind.findData("jellyfin"))
    assert _shown(d.server) and _shown(d.user) and _shown(d.pw) and _shown(d.signin)
    assert _shown(d.signin.sign_btn) and _shown(d.signin.qc_btn) and not _shown(d.signin.plex_btn)
    assert _shown(d.finder.row) and d.server.placeholderText() == "http://192.168.1.40:8096"
    d.kind.setCurrentIndex(d.kind.findData("emby"))
    assert _shown(d.signin.sign_btn) and not _shown(d.signin.qc_btn)
    d.kind.setCurrentIndex(d.kind.findData("plex"))
    assert not _shown(d.server) and not _shown(d.user) and not _shown(d.pw)
    assert _shown(d.signin.plex_btn) and not _shown(d.signin.sign_btn)
    d.kind.setCurrentIndex(d.kind.findData("xtream"))
    assert not _shown(d.signin) and _shown(d.server) and _shown(d.user)


def test_signing_in_from_the_form_keeps_a_token_not_the_password(app, fake):
    s = fake({("POST", "/Users/AuthenticateByName"): _auth_route})
    d = PlaylistDialog()
    d.kind.setCurrentIndex(d.kind.findData("jellyfin"))
    d.server.setText(s.base)
    d.user.setText("anna")
    d.pw.setText("secret pw")
    # OK before Sign in signs in, then adds the source.
    d._validate()
    assert d.signin.busy
    assert _wait(lambda: d.result() == 1)
    v = d.values()
    assert v["kind"] == "jellyfin" and v["token"] == TOKEN and v["password"] == ""
    assert v["username"] == "Anna" and v["server"] == s.base
    assert v["options"] == {"user_id": UID, "server_id": "srv1"}
    assert "secret pw" not in repr(v) and d.pw.text() == ""
    assert d.signin.status.text() == "Signed in as Anna."


def test_a_wrong_password_says_so(app, fake):
    s = fake({("POST", "/Users/AuthenticateByName"): _auth_route})
    d = PlaylistDialog()
    d.kind.setCurrentIndex(d.kind.findData("jellyfin"))
    d.server.setText(s.base)
    d.user.setText("anna")
    d.pw.setText("nope")
    d.signin.sign_btn.click()
    assert _wait(lambda: not d.signin.busy)
    assert not d.signin.signed_in() and d.signin.status.text() == "Wrong username or password."


def test_editing_keeps_the_sign_in_until_the_server_changes(app):
    pl = {"name": "Den", "kind": "jellyfin", "server": "http://10.0.0.5:8096", "username": "Anna",
          "password": "", "token": TOKEN, "options": {"user_id": UID, "ms_quality": "8"}}
    d = PlaylistDialog(playlist=pl)
    assert d.signin.signed_in() and d.signin.status.text() == "Signed in as Anna."
    v = d.values()
    assert v["token"] == TOKEN and v["options"] == {"user_id": UID, "ms_quality": "8"}
    assert d.pw.placeholderText() == "Only to sign in again"
    d.server.setText("http://10.0.0.6:8096")
    assert not d.signin.signed_in()
    assert d.signin.status.text() == "The address or the user changed: sign in again."
    # A source that is no media server any more keeps no token.
    d.kind.setCurrentIndex(d.kind.findData("m3u"))
    assert d.values()["token"] == "" and "user_id" not in d.values().get("options", {})


def test_plex_sign_in_in_the_browser_then_the_server(app, fake, monkeypatch):
    polls = {"n": 0}

    def pin(_q, _b, _h):
        polls["n"] += 1
        return {"id": 7, "code": "wxyz", "authToken": "acct-tok" if polls["n"] > 1 else None}
    s = fake({("POST", "/api/v2/pins"): {"id": 7, "code": "wxyz"},
              ("GET", "/api/v2/pins/7"): pin,
              ("GET", "/api/v2/user"): {"username": "anna"},
              ("GET", "/identity"): {"MediaContainer": {"machineIdentifier": "m2"}}})
    s.routes[("GET", "/api/v2/resources")] = [
        {"name": "Den", "provides": "server", "clientIdentifier": "m1", "accessToken": "den-tok",
         "owned": True, "connections": [{"uri": "http://127.0.0.1:9", "local": True}]},
        {"name": "Attic", "provides": "server", "clientIdentifier": "m2", "accessToken": "attic-tok",
         "owned": True, "connections": [{"uri": s.base, "local": True}]}]
    monkeypatch.setattr(plex, "PLEX_TV", s.base)
    monkeypatch.setattr(media_signin, "PIN_EVERY_MS", 30)
    opened = []
    monkeypatch.setattr(xdg, "open_url", lambda u: opened.append(u) or True)
    d = PlaylistDialog()
    d.kind.setCurrentIndex(d.kind.findData("plex"))
    # Found on the network first: that server is the one picked.
    d._box_found({"kind": "plex", "server": s.base, "name": "Attic - 127.0.0.1", "id": "m2"})
    d.signin.plex_btn.click()
    assert _wait(lambda: d.signin.signed_in(), 10)
    assert opened and opened[0].startswith("https://app.plex.tv/auth#?clientID=")
    assert d.signin.servers.count() == 2 and d.signin.servers.currentData() == "m2"
    v = d.values()
    assert v["kind"] == "plex" and v["token"] == "attic-tok" and v["server"] == s.base
    assert v["options"] == {"server_id": "m2", "connections": s.base} and v["username"] == "anna"
    assert v["name"] == "Attic"


def _quick_connect(monkeypatch, ready):
    from dopeiptv.providers import jellyfin
    monkeypatch.setattr(media_signin, "QC_EVERY_MS", 20)
    monkeypatch.setattr(jellyfin, "quick_connect_enabled", lambda server: True)
    monkeypatch.setattr(jellyfin, "quick_connect_start", lambda server: {"secret": "s", "code": "123456"})
    monkeypatch.setattr(jellyfin, "quick_connect_ready", ready)
    monkeypatch.setattr(jellyfin, "quick_connect_finish", lambda server, secret: {
        "token": TOKEN, "user_id": UID, "user": "Anna", "server": server})
    d = PlaylistDialog()
    d.kind.setCurrentIndex(d.kind.findData("jellyfin"))
    d.server.setText("http://10.0.0.5:8096")
    return d


def test_a_wait_rides_out_a_network_hiccup(app, fake, monkeypatch):
    """Quick Connect waits five minutes and Plex's PIN fifteen, a poll every
    few seconds: one that did not get through (Wi-Fi dropping, a server's
    503) ended the whole sign-in. Now a few in a row are tried again, each
    a little later; the code being gone still ends it at once."""
    polls = {"n": 0}

    def ready(server, secret):
        polls["n"] += 1
        if polls["n"] <= 2:
            raise LocalizedError("err_box_not_found", kind="Jellyfin")
        return polls["n"] > 3
    d = _quick_connect(monkeypatch, ready)
    d.signin.qc_btn.click()
    assert _wait(lambda: d.signin.signed_in(), 10), d.signin.status.text()
    assert polls["n"] == 4

    # Plex: the PIN answered 503 twice, then signed in.
    pins = {"n": 0}

    def pin(_q, _b, _h):
        pins["n"] += 1
        if pins["n"] <= 2:
            return 503, {"error": "busy"}
        return {"id": 7, "code": "wxyz", "authToken": "acct-tok"}
    s = fake({("POST", "/api/v2/pins"): {"id": 7, "code": "wxyz"},
              ("GET", "/api/v2/pins/7"): pin,
              ("GET", "/api/v2/user"): {"username": "anna"},
              ("GET", "/identity"): {"MediaContainer": {"machineIdentifier": "m1"}}})
    s.routes[("GET", "/api/v2/resources")] = [
        {"name": "Den", "provides": "server", "clientIdentifier": "m1", "accessToken": "den-tok",
         "owned": True, "connections": [{"uri": s.base, "local": True}]}]
    monkeypatch.setattr(plex, "PLEX_TV", s.base)
    monkeypatch.setattr(media_signin, "PIN_EVERY_MS", 20)
    monkeypatch.setattr(xdg, "open_url", lambda u: True)
    p = PlaylistDialog()
    p.kind.setCurrentIndex(p.kind.findData("plex"))
    p.signin.plex_btn.click()
    assert _wait(lambda: p.signin.signed_in(), 10), p.signin.status.text()
    assert pins["n"] == 3

    # The code gone: no second try.
    gone = {"n": 0}

    def expired(server, secret):
        gone["n"] += 1
        raise LocalizedError("err_ms_qc_expired")
    d = _quick_connect(monkeypatch, expired)
    d.signin.qc_btn.click()
    assert _wait(lambda: not d.signin.busy, 5)
    QThreadPool.globalInstance().waitForDone(500)
    assert gone["n"] == 1 and d.signin.status.text() == tr("err_ms_qc_expired")


def test_a_wait_that_never_gets_through_gives_up_and_a_cancel_stops_it(app, monkeypatch):
    calls = {"n": 0}

    def down(server, secret):
        calls["n"] += 1
        raise LocalizedError("err_box_not_found", kind="Jellyfin")
    d = _quick_connect(monkeypatch, down)
    d.signin.qc_btn.click()
    assert _wait(lambda: not d.signin.busy, 15)
    assert calls["n"] == media_signin.POLL_HICCUPS + 1
    assert d.signin.status.text() == tr("err_box_not_found", kind="Jellyfin")
    assert not d.signin.signed_in()

    # Cancelled (another kind picked) while it waits to try again: no poll after.
    calls["n"] = 0
    d = _quick_connect(monkeypatch, down)
    d.signin.qc_btn.click()
    assert _wait(lambda: calls["n"] >= 1, 5)
    d.kind.setCurrentIndex(d.kind.findData("emby"))
    seen = calls["n"]
    end = time.monotonic() + 1.0
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)
    QThreadPool.globalInstance().waitForDone(500)
    assert calls["n"] <= seen + 1 and not d.signin.busy and not d.signin._timer.isActive()


def test_the_finder_offers_the_forms_kind_first(app):
    f = FindOnNetwork()
    f.wanted = ("jellyfin", "emby", "plex")
    box = {"kind": "enigma2", "server": "http://10.0.0.2", "name": "Vu+"}
    jf = {"kind": "jellyfin", "server": "http://10.0.0.5:8096", "name": "Den"}
    got = []
    f.picked.connect(got.append)
    f._done([box, jf])
    menu = f.findChildren(QMenu)[-1]
    labels = [a.text() for a in menu.actions()]
    assert labels[1].startswith("Den") and labels[2].startswith("Vu+")


def test_welcome_offers_media_servers_and_connects_with_the_token(app, fake):
    s = fake({("POST", "/Users/AuthenticateByName"): _auth_route})
    connected = []
    parent = QWidget()
    w = WelcomeOverlay(parent, settings=None,
                       on_connect=lambda *a: connected.append(a), on_explore=lambda: None,
                       on_language_change=lambda c: None, on_demo=lambda: None)
    w._cards["media"].click()                     # "Media server"
    combo = w._conn_kind
    assert combo.findData("jellyfin") >= 0 and combo.findData("plex") >= 0
    assert combo.findData("enigma2") < 0 and combo.findData("xtream") < 0   # its card's own
    combo.setCurrentIndex(combo.findData("emby"))
    w._server.setText(s.base + "/emby")
    s.routes[("POST", "/emby/Users/AuthenticateByName")] = _auth_route
    w._user.setText("anna")
    w._pw.setText("secret pw")
    w._do_connect()
    assert _wait(lambda: bool(connected))
    server, user, pw, kind, name, mac, signed, _extra = connected[0]
    assert (server, user, pw, kind) == (s.base, "Anna", "", "emby")
    assert signed == {"token": TOKEN, "options": {"user_id": UID, "server_id": "srv1"}}


# -- the window's side --------------------------------------------------------------

class _Client:
    reports_playback = True
    REPORT_EVERY = 10.0

    def __init__(self):
        self.sent: list[tuple] = []
        self.state: dict = {}
        self.rows: list[dict] = []
        self.live_note = ""

    def report_target(self, key, kind, item, url):
        return {"id": f"ms-{key}", "kind": kind} if key is not None else None

    def report(self, event, target, pos, dur, paused=False):
        self.sent.append((event, target["id"], round(pos), round(dur), paused))

    def mark(self, target, played):
        self.sent.append(("mark", target["id"], played))

    def server_state(self, kind, key):
        return self.state.get((kind, str(key)))

    def resume_items(self):
        return [dict(r) for r in self.rows]

    def flush_reports(self, timeout=1.0):
        return True


class _Player:
    def __init__(self):
        self.current_url = None
        self.pos, self.dur = 0.0, 0.0

    def playback_position(self):
        return self.pos

    def playback_duration(self):
        return self.dur


class _Resume:
    def __init__(self, rows=None, at=0):
        self.rows, self.at = rows or [], at

    def continue_watching(self):
        return [dict(r) for r in self.rows]

    def saved_at(self, key, kind):
        return self.at


class _Win(_MediaServerMixin, QObject):
    def __init__(self, client):
        QObject.__init__(self)
        self.client, self.player, self.resume = client, _Player(), _Resume()
        self.status = ""

    def _item_key(self, it):
        return it.get("stream_id") or it.get("series_id") or it.get("id") or it.get("_key")

    def _set_status(self, text):
        self.status = text

    def _lock_filter(self, rows):        # nothing locked (mw_lists)
        return list(rows)


def test_what_plays_is_reported_start_progress_pause_stop(app):
    c = _Client()
    w = _Win(c)
    w.player.current_url = "http://srv/a"
    w._ms_started("http://srv/a", 5, "movie", {"stream_id": 5}, 120)
    w.player.pos, w.player.dur = 300, 6000
    w._ms_tick()
    w._ms_paused(True)
    w._ms_tick()                       # nothing while paused
    w._ms_paused(False)
    w.player.pos = 900
    # Another title: the first one is told where it stopped.
    w.player.current_url = "http://srv/b"
    w._ms_started("http://srv/b", 6, "episode", {"id": 6}, 0)
    w._ms_finished()
    assert c.sent == [("start", "ms-5", 120, 0, False), ("progress", "ms-5", 300, 6000, False),
                      ("progress", "ms-5", 300, 6000, True), ("progress", "ms-5", 300, 6000, False),
                      ("stop", "ms-5", 300, 6000, False), ("start", "ms-6", 0, 0, False),
                      ("stop", "ms-6", 6000, 6000, False)]
    # Another kind of source hears nothing.
    w.client = object()
    w._ms_started("http://x", 1, "movie", {}, 0)
    assert w._ms_now is None


def test_private_viewing_reports_nothing(app, monkeypatch):
    from dopeiptv.core import privacy
    monkeypatch.setattr(privacy, "private", lambda: True)
    c = _Client()
    _Win(c)._ms_started("http://srv/a", 5, "movie", {}, 0)
    assert c.sent == []


def test_the_newer_resume_point_wins(app):
    c = _Client()
    w = _Win(c)
    now = int(time.time())
    c.state[("movie", "5")] = {"pos": 1800.0, "played": False, "last_played": now - 60}
    w.resume.at = now - 3600                     # watched here an hour ago
    assert w._ms_resume_at(5, "movie", None, 600.0) == 1800.0
    w.resume.at = now                            # here is newer
    assert w._ms_resume_at(5, "movie", None, 600.0) == 600.0
    c.state[("movie", "5")] = {"pos": 0.0, "played": True, "last_played": now + 5}
    assert w._ms_resume_at(5, "movie", None, 600.0) == 0.0      # finished elsewhere
    # A row the client no longer knows still carries what the server said.
    item = {"_played": False, "_server_pos": 700.0, "_last_played": now + 9}
    assert w._ms_resume_at(9, "movie", item, 0.0) == 700.0


def test_continue_watching_has_the_servers_titles_too(app):
    c = _Client()
    w = _Win(c)
    now = int(time.time())
    w.resume.rows = [{"_kind": "vod", "stream_id": 1, "_ts": now - 100},
                     {"_kind": "vod", "stream_id": 2, "_ts": now - 50}]
    c.state[("movie", "2")] = {"pos": 0.0, "played": True, "last_played": now}   # finished on a phone
    c.rows = [{"_kind": "vod", "stream_id": 1, "_ts": now - 10},
              {"_kind": "episode", "id": 9, "_ts": now - 5}]
    rows = w._continue_rows()
    assert [(r["_kind"], w._item_key(r)) for r in rows] == [("episode", 9), ("vod", 1)]
    assert rows[1]["_ts"] == now - 100           # this computer's own row, not a second one


def test_marks_and_the_live_note(app):
    c = _Client()
    w = _Win(c)
    film = {"stream_id": 3, "_ms_id": "x", "_played": False, "_progress_pct": 40}
    w._ms_mark(film, True)
    assert c.sent == [("mark", "ms-3", True)] and film["_played"] and "_progress_pct" not in film
    w._ms_mark({"series_id": 4, "_ms_id": "show"}, False, "series")
    assert c.sent[-1] == ("mark", "show", False)
    c.live_note = "No Live TV"
    w._ms_live_note([])
    assert w.status == "No Live TV"


# -- the real window against a fake Jellyfin ------------------------------------------

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import faulthandler, json, os, sys, tempfile, time
faulthandler.dump_traceback_later(150, exit=True)


def _failed(*exc):
    import traceback
    traceback.print_exception(*exc)
    sys.stderr.flush()
    os._exit(1)
sys.excepthook = _failed
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import make_client
from dopeiptv.ui import main_window as mw
from tests.test_media_servers import MOVIE, TOKEN, UID, FakeServer, jellyfin_routes

routes = jellyfin_routes()
for path in ("/Sessions/Playing", "/Sessions/Playing/Progress", "/Sessions/Playing/Stopped"):
    routes[("POST", path)] = (lambda q, b, h: (204, b""))
srv = FakeServer(routes)
mw.launch_player = lambda *a, **k: (_ for _ in ()).throw(AssertionError("external player"))
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
pl = {"id": "j", "name": "Den", "kind": "jellyfin", "server": srv.base, "username": "Anna",
      "password": "", "token": TOKEN, "options": {"user_id": UID}}
s.setValue("playlists", json.dumps([pl]))
s.setValue("active_playlist", "j")
from dopeiptv.providers import mediaserver
mediaserver.install_device_store(s)                    # as app.py does, before the client
client = make_client(pl)
w = mw.MainWindow(client, s, PlaylistStore(s))
if w.player is None:
    print("MEDIA_SKIP: no embedded player here")
    os._exit(0)
assert s.value("media_device_id") == client.device == mediaserver.device_id()
client.authenticate()
played = []


def play(url, *a, **k):
    played.append((url, k.get("start")))
    w.player.current_url = url
    return True
w.player.play = play
w.wake.acquire = w.wake.release = lambda *a, **k: None
asked = []
w._choice_dialog = lambda *a, **k: asked.append(a[1]) or 0      # "Continue from 50:00"

heat, ronin = client.vod_streams("lib-movies")
assert w.is_movie_watched(ronin) and not w.is_movie_watched(heat)
# The server's resume point is offered: it was watched there, not here.
w.mode = "vod"
w.play_item(heat)
assert played and played[-1][1] == 3000.0, played
assert "50:00" in asked[-1], asked


def until(done, secs=8.0):
    end = time.monotonic() + secs
    while time.monotonic() < end and not done():
        app.processEvents()
        time.sleep(0.01)
    return done()

assert until(lambda: srv.hits("POST", "/Sessions/Playing")), srv.calls[-3:]
start = srv.hits("POST", "/Sessions/Playing")[0][3]
assert start["ItemId"] == MOVIE["Id"] and start["PositionTicks"] == 3000 * 10_000_000, start
w.player.playback_position = lambda: 3600.0
w.player.playback_duration = lambda: 10200.0
w.player.stopped.emit()
assert until(lambda: srv.hits("POST", "/Sessions/Playing/Stopped"))
assert srv.hits("POST", "/Sessions/Playing/Stopped")[0][3]["PositionTicks"] == 3600 * 10_000_000
# Continue watching has the server's titles, this computer's first.
cont = w._continue_items("vod")
assert [r["name"] for r in cont][:1] == ["Heat (1995)"], cont
eps = w._continue_items("episode")
assert eps and eps[0]["_series_ctx"]["name"] == "Dark (2017)", eps
print("MEDIA_OK", flush=True)
os._exit(0)
"""


def test_the_window_plays_and_reports_to_a_media_server(tmp_path):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        env[var] = str(tmp_path / var.lower())
        os.makedirs(env[var], exist_ok=True)
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    if "MEDIA_SKIP" in out.stdout:
        pytest.skip(out.stdout.strip())
    assert "MEDIA_OK" in out.stdout, out.stdout + out.stderr[-4000:]
