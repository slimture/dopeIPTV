"""Every way into the first-run guide, end to end: the real guide (ui/welcome.py)
driven as a person would - first page, what to watch, the kind's form, Connect -
with the window's real connect step (ui/mw_onboarding.py: make_client, the
try on a worker, providers/connect_check.py) against servers of the tests' own
on 127.0.0.1: an Xtream panel, playlists of every shape, a Stalker portal,
Jellyfin, Plex and an HDHomeRun tuner. Each path ends with the source kept and
its channels in the client the window takes - or, for every way it can fail,
the reason in plain words, nothing kept, and the form as it was typed.

The window around the guide is a small stand-in (its _use_source records what
it was handed); the whole window is tests/test_welcome_window.py. Nothing
here reaches the network beyond 127.0.0.1."""
from __future__ import annotations

import gzip
import json
import os
import socket
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("PyQt6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSettings, QThreadPool, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget  # noqa: E402

from dopeiptv import i18n  # noqa: E402
from dopeiptv.core.stores import PlaylistStore  # noqa: E402
from dopeiptv.i18n import tr  # noqa: E402
from dopeiptv.providers import client as client_mod  # noqa: E402
from dopeiptv.providers.client import OfflineClient  # noqa: E402
from dopeiptv.ui.welcome import PAGE_FORM, PAGE_WORK  # noqa: E402

QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())


@pytest.fixture(scope="module")
def app():
    i18n.set_language("en")
    return QApplication.instance() or QApplication([])


# -- servers of the tests' own ------------------------------------------------------

class Served(ThreadingHTTPServer):
    """routes: path -> (status, headers, body) or callable(query) -> that;
    every request kept as (path, query)."""

    daemon_threads = True

    def __init__(self, routes: dict, tls: tuple[str, str] | None = None) -> None:
        self.routes, self.seen = routes, []
        super().__init__(("127.0.0.1", 0), _Handler)
        if tls:
            import ssl
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(*tls)
            self.socket = ctx.wrap_socket(self.socket, server_side=True)
        threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def hits(self, path: str) -> int:
        return sum(1 for p, _q in self.seen if p == path)


class _Handler(BaseHTTPRequestHandler):
    server: Served

    def log_message(self, *a) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802 - the stdlib's name
        u = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
        self.server.seen.append((u.path, q))
        route = self.server.routes.get(u.path)
        if callable(route):
            route = route(q)
        status, headers, body = route if route is not None else (404, {}, b"not found")
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        for k, v in {"Content-Length": str(len(body)), **headers}.items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass


@pytest.fixture
def serve():
    made: list[Served] = []

    def make(routes: dict, tls: tuple[str, str] | None = None) -> Served:
        s = Served(routes, tls)
        made.append(s)
        return s
    yield make
    for s in made:
        s.shutdown()
        s.server_close()


def xtream_panel(user="anna", pw="s3cret pw", mode="", channels=12, films=7, shows=3):
    """An Xtream panel's player_api.php: *mode* "expired", "banned", "full"
    (every connection in use), "html" (an error page), "empty"."""
    def api(q):
        if mode == "html":
            return 200, {"Content-Type": "text/html"}, "<!DOCTYPE html><html>Gateway</html>"
        ok = (q.get("username"), q.get("password")) == (user, pw)
        info = {"auth": 1 if ok else 0, "status": "Active", "exp_date": str(int(time.time()) + 86400 * 30),
                "max_connections": "2", "active_cons": "0", "is_trial": "0"}
        if mode == "expired":
            info.update(status="Expired", exp_date=str(int(time.time()) - 86400))
        elif mode == "banned":
            info.update(status="Banned")
        elif mode == "full":
            info.update(max_connections="1", active_cons="1")
        action = q.get("action")
        n_live, n_vod, n_series = (0, 0, 0) if mode == "empty" else (channels, films, shows)
        if not ok:
            return 200, {}, {"user_info": {"auth": 0}}
        if action is None:
            return 200, {}, {"user_info": info, "server_info": {"url": "127.0.0.1"}}
        lists = {
            "get_live_categories": [{"category_id": "1", "category_name": "News"}],
            "get_live_streams": [{"stream_id": i, "num": i, "name": f"Ch {i}", "category_id": "1",
                                  "stream_icon": "", "epg_channel_id": ""} for i in range(1, n_live + 1)],
            "get_vod_categories": [{"category_id": "2", "category_name": "Films"}],
            "get_vod_streams": [{"stream_id": 100 + i, "name": f"Film {i}", "category_id": "2"}
                                for i in range(n_vod)],
            "get_series_categories": [{"category_id": "3", "category_name": "Shows"}],
            "get_series": [{"series_id": 200 + i, "name": f"Show {i}", "category_id": "3"}
                           for i in range(n_series)],
        }
        return 200, {}, lists.get(action, [])
    return {"/player_api.php": api}


def playlist(n=5, header="#EXTM3U", crlf=False, bom=False) -> bytes:
    lines = [header] + [x for i in range(n) for x in (
        f'#EXTINF:-1 tvg-id="c{i}" group-title="Group {i % 2}",Channel {i}',
        f"http://127.0.0.1:9/stream/{i}.ts")]
    text = ("\r\n" if crlf else "\n").join(lines) + "\n"
    return (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")


# -- the window around the guide ------------------------------------------------------

def _window(tmp_path):
    from dopeiptv.ui.mw_onboarding import _OnboardingMixin

    class Win(_OnboardingMixin, QMainWindow):
        def __init__(self):
            super().__init__()
            self.setCentralWidget(QWidget())
            self.resize(1100, 760)
            self.settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
            self.playlist_store = PlaylistStore(self.settings)
            self.pool = QThreadPool.globalInstance()
            self._welcome = None
            self._player_fs = False
            self.client = OfflineClient()
            self.used: list[tuple] = []
            self.places: list[str] = []

        def _use_source(self, pid, client, auth=None, then=None):
            self.used.append((pid, client, auth))
            self.client = client

        def _show_place(self, name):
            self.places.append(name)

        def _set_status(self, text):
            pass

        def _set_language(self, code):
            pass

        def start_demo(self):
            self.places.append("demo")

    w = Win()
    w.show()
    w.show_welcome()
    return w, w._welcome


def _settle(w, secs=20.0) -> bool:
    end = time.monotonic() + secs
    while time.monotonic() < end:
        QApplication.processEvents()
        if not w._welcome.trying():
            QApplication.processEvents()
            return True
        time.sleep(0.01)
    return False


def _to_form(ov, family="iptv", kind=None):
    ov._w_next.click()
    ov._cards[family].click()
    if kind:
        ov._set_kind(kind)
    assert ov._stack.currentIndex() == PAGE_FORM


def _connect(w, ov) -> None:
    ov._c_connect.click()
    assert ov._stack.currentIndex() == PAGE_WORK
    assert _settle(w), "the try did not end"


def _kept(w, ov, channels: int, kind: str):
    """The guide said Done with *channels*, the source of *kind* is kept and
    active, and the client the window took lists its channels."""
    assert ov._done, ov._k_text.text()
    assert tr("count_channels", n=str(channels)) in ov._k_text.text() or \
        tr("count_channels", n=f"{channels:,}") in ov._k_text.text(), ov._k_text.text()
    pls = w.playlist_store.playlists()
    assert len(pls) == 1 and pls[0]["kind"] == kind and w.playlist_store.active_id == pls[0]["id"]
    pid, client, _auth = w.used[-1]
    assert pid == pls[0]["id"]
    assert len(client.live_streams(None)) == channels
    return pls[0], client


def _failed(w, ov, words: str) -> None:
    assert not ov._done and ov._k_title.text() == tr("onb_fail_title")
    assert ov._k_text.text() == words, ov._k_text.text()
    assert w.playlist_store.playlists() == [] and w.used == []


# -- Xtream -----------------------------------------------------------------------------

def test_xtream_typed_by_hand(app, tmp_path, serve):
    s = serve(xtream_panel())
    w, ov = _window(tmp_path)
    _to_form(ov)
    ov._server.setText(s.base)
    ov._user.setText("anna")
    ov._pw.setText("s3cret pw")
    _connect(w, ov)
    pl, client = _kept(w, ov, 12, "xtream")
    assert (pl["server"], pl["username"], pl["password"]) == (s.base, "anna", "s3cret pw")
    assert tr("count_movies", n="7") in ov._k_text.text() and tr("count_series", n="3") in ov._k_text.text()
    # The window asks nothing again: its client holds the lists it read.
    before = s.hits("/player_api.php")
    assert len(client.vod_streams(None)) == 7 and s.hits("/player_api.php") == before


@pytest.mark.parametrize("shape", [
    "{base}/get.php?username=anna&password=s3cret%20pw&type=m3u_plus&output=ts",
    "{base}/player_api.php?username=anna&password=s3cret%20pw",
    "{base}/live/anna/s3cret%20pw/1.ts",
    "  {base}/get.php?username=anna&password=s3cret+pw&type=m3u_plus  ",
    "{base}/live/anna/s3cret%20pw/1.ts/",
])
def test_xtream_from_every_shape_of_its_link(app, tmp_path, serve, shape):
    s = serve(xtream_panel())
    w, ov = _window(tmp_path)
    _to_form(ov)
    ov._server.clear()
    ov._server.insert(shape.format(base=s.base))
    # The login as the panel knows it, whichever way the link wrote it.
    assert ov._conn_kind.currentData() == "xtream" and ov._server.text() == s.base
    assert (ov._user.text(), ov._pw.text()) == ("anna", "s3cret pw")
    _connect(w, ov)
    _kept(w, ov, 12, "xtream")


def test_an_xtream_panel_under_a_path_of_its_own(app, tmp_path, serve):
    routes = {"/iptv" + k: v for k, v in xtream_panel().items()}
    s = serve(routes)
    w, ov = _window(tmp_path)
    _to_form(ov)
    ov._server.insert(f"{s.base}/iptv/get.php?username=anna&password=s3cret+pw")
    assert ov._server.text() == s.base + "/iptv"
    _connect(w, ov)
    _kept(w, ov, 12, "xtream")


@pytest.mark.parametrize("mode,words", [
    ("expired", "diag_expired"), ("banned", "diag_account_status"), ("html", "onb_err_not_xtream"),
    ("empty", "onb_err_nothing")])
def test_an_xtream_account_that_cannot_be_used_is_said_so(app, tmp_path, serve, mode, words):
    s = serve(xtream_panel(mode=mode))
    w, ov = _window(tmp_path)
    _to_form(ov)
    ov._server.setText(s.base)
    ov._user.setText("anna")
    ov._pw.setText("s3cret pw")
    _connect(w, ov)
    _failed(w, ov, tr(words, status="Banned"))
    # Back: the form as it was typed, Connect again works.
    ov._k_back.click()
    assert ov._stack.currentIndex() == PAGE_FORM
    assert (ov._server.text(), ov._user.text(), ov._pw.text()) == (s.base, "anna", "s3cret pw")


def test_a_wrong_login_is_said_and_the_next_try_works(app, tmp_path, serve):
    s = serve(xtream_panel())
    w, ov = _window(tmp_path)
    _to_form(ov)
    ov._server.setText(s.base)
    ov._user.setText("anna")
    ov._pw.setText("Zq9-nope")
    _connect(w, ov)
    _failed(w, ov, tr("onb_err_login"))
    assert "Zq9-nope" not in ov._k_text.text()
    ov._k_back.click()
    ov._pw.setText("s3cret pw")
    _connect(w, ov)
    _kept(w, ov, 12, "xtream")


def test_every_connection_in_use_connects_and_says_so(app, tmp_path, serve):
    s = serve(xtream_panel(mode="full"))
    w, ov = _window(tmp_path)
    _to_form(ov)
    ov._server.setText(s.base)
    ov._user.setText("anna")
    ov._pw.setText("s3cret pw")
    _connect(w, ov)
    _kept(w, ov, 12, "xtream")
    assert ov._k_warn.text() == tr("diag_conn_limit", active=1, maxc=1)


def test_connect_twice_is_one_try_and_one_source(app, tmp_path, serve):
    gate = threading.Event()
    panel = xtream_panel()
    first = panel["/player_api.php"]

    def slow(q):
        gate.wait(5)
        return first(q)
    s = serve({"/player_api.php": slow})
    w, ov = _window(tmp_path)
    _to_form(ov)
    ov._server.setText(s.base)
    ov._user.setText("anna")
    ov._pw.setText("s3cret pw")
    ov._c_connect.click()
    ov._c_connect.click()
    ov._do_connect()
    w._wizard_connect(s.base, "anna", "s3cret pw")      # and asked of the window again
    QApplication.processEvents()
    gate.set()
    assert _settle(w)
    logins = [q for p, q in s.seen if p == "/player_api.php" and "action" not in q]
    assert len(logins) == 1, logins
    _kept(w, ov, 12, "xtream")


def test_closing_the_guide_while_it_tries_keeps_nothing(app, tmp_path, serve):
    from dopeiptv.providers.epg import _epg_cache_dir
    gate = threading.Event()
    first = xtream_panel()["/player_api.php"]

    def slow(q):
        if q.get("action") == "get_live_streams":
            gate.wait(5)
        return first(q)
    s = serve({"/player_api.php": slow})
    w, ov = _window(tmp_path)
    _to_form(ov)
    ov._server.setText(s.base)
    ov._user.setText("anna")
    ov._pw.setText("s3cret pw")
    ov._c_connect.click()
    end = time.monotonic() + 5
    while not s.hits("/player_api.php") and time.monotonic() < end:
        QApplication.processEvents()
    pid = w._wtry["row"]["id"]
    ov.dismiss()                                       # Esc
    assert not ov.isVisible() and w._wtry is None
    gate.set()
    QThreadPool.globalInstance().waitForDone(5000)
    for _ in range(20):
        QApplication.processEvents()
    assert w.playlist_store.playlists() == [] and w.used == []
    # What the try read for that id went with it.
    from dopeiptv.core.source_files import file_part
    assert not (_epg_cache_dir() / f"lists_{file_part(pid)}.d").exists()


def test_a_server_that_refuses_and_one_that_does_not_answer(app, tmp_path, serve, monkeypatch):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    w, ov = _window(tmp_path)
    _to_form(ov)
    ov._server.setText(f"http://127.0.0.1:{port}")
    ov._user.setText("anna")
    ov._pw.setText("s3cret pw")
    _connect(w, ov)
    _failed(w, ov, tr("onb_err_unreachable"))
    # One that answers nothing: said once its time is up (a second here).
    gate = threading.Event()
    s = serve({"/player_api.php": lambda q: gate.wait(10) and (200, {}, {})})
    get = client_mod.Mirrored.get_from_server
    monkeypatch.setattr(client_mod.Mirrored, "get_from_server",
                        lambda self, path, params, timeout, *a, **k: get(self, path, params, (2, 1), *a, **k))
    ov._k_back.click()
    ov._server.setText(s.base)
    _connect(w, ov)
    gate.set()
    _failed(w, ov, tr("onb_err_timeout"))


def test_a_certificate_that_does_not_check_out(app, tmp_path, serve):
    from tests.test_net_advanced import _make_cert
    s = serve(xtream_panel(), tls=_make_cert(tmp_path, "127.0.0.1", "w"))
    w, ov = _window(tmp_path)
    _to_form(ov)
    ov._server.setText(s.base.replace("http://", "https://"))
    ov._user.setText("anna")
    ov._pw.setText("s3cret pw")
    _connect(w, ov)
    _failed(w, ov, tr("diag_certificate") + " " + tr("onb_err_certificate_do"))


# -- M3U --------------------------------------------------------------------------------

M3U_SHAPES = {
    "plain": ("/list.m3u", (200, {"Content-Type": "audio/x-mpegurl"}, playlist())),
    "m3u8 with a query": ("/tv/list.m3u8?token=abc&x=1", (200, {}, playlist())),
    "gzip transfer": ("/z.m3u", (200, {"Content-Encoding": "gzip"}, gzip.compress(playlist()))),
    "gzip file": ("/list.m3u.gz", (200, {"Content-Type": "application/gzip"}, gzip.compress(playlist()))),
    "bom": ("/bom.m3u", (200, {}, playlist(bom=True))),
    "crlf": ("/crlf.m3u", (200, {}, playlist(crlf=True))),
    "url-tvg": ("/guide.m3u", (200, {}, playlist(header='#EXTM3U url-tvg="http://127.0.0.1:9/g.xml"'))),
    "no header": ("/bare.m3u", (200, {}, b"\n".join(playlist().split(b"\n")[1:]))),
    "addresses alone": ("/plain.m3u", (200, {}, b"#EXTM3U\n" + b"\n".join(
        f"http://127.0.0.1:9/a/{i}.ts".encode() for i in range(5)) + b"\n")),
    "dispatcharr path": ("/output/m3u", (200, {}, playlist())),
}


@pytest.mark.parametrize("shape", sorted(M3U_SHAPES))
def test_m3u_from_every_shape_of_link(app, tmp_path, serve, shape):
    path, route = M3U_SHAPES[shape]
    s = serve({path.split("?")[0]: route})
    w, ov = _window(tmp_path)
    _to_form(ov, kind="m3u")
    ov._server.setText(s.base + path)
    _connect(w, ov)
    pl, client = _kept(w, ov, 5, "m3u")
    assert pl["server"] == s.base + path
    if shape == "url-tvg":
        assert client.epg_url == "http://127.0.0.1:9/g.xml"
    if shape != "addresses alone":
        assert client.live_streams(None)[0]["name"] == "Channel 0"


def test_an_m3u_link_pasted_into_the_xtream_field_switches_and_connects(app, tmp_path, serve):
    s = serve({"/list.m3u": (200, {}, playlist())})
    w, ov = _window(tmp_path)
    _to_form(ov)
    assert ov._conn_kind.currentData() == "xtream"
    ov._server.insert(s.base + "/list.m3u")
    assert ov._conn_kind.currentData() == "m3u"
    _connect(w, ov)
    _kept(w, ov, 5, "m3u")


def test_a_redirect_to_the_real_list(app, tmp_path, serve):
    s = serve({"/real.m3u": (200, {}, playlist())})
    s.routes["/short"] = (302, {"Location": s.base + "/real.m3u"}, b"")
    w, ov = _window(tmp_path)
    _to_form(ov, kind="m3u")
    ov._server.setText(s.base + "/short")
    _connect(w, ov)
    _kept(w, ov, 5, "m3u")
    assert s.hits("/real.m3u") == 1


@pytest.mark.parametrize("how", ["path", "file url"])
def test_a_playlist_file_on_this_computer(app, tmp_path, how):
    from urllib.parse import quote
    folder = tmp_path / "My Lists"
    folder.mkdir()
    f = folder / "tv.m3u"
    f.write_bytes(playlist(n=4))
    text = str(f) if how == "path" else "file://" + quote(str(f))
    w, ov = _window(tmp_path)
    _to_form(ov)                                       # pasted where the Xtream server goes
    ov._server.insert(text)
    assert ov._conn_kind.currentData() == "m3u"
    _connect(w, ov)
    _kept(w, ov, 4, "m3u")


@pytest.mark.parametrize("body,status,words", [
    (b"<!DOCTYPE html><html><body>Log in</body></html>", 200, "err_m3u_html"),
    (b"", 200, "err_m3u_empty"),
    (b"#EXTM3U\n#EXTINF:-1,Nothing after it\n#EXTINF:-1,Nor here\n", 200, "err_m3u_no_channels"),
    (b"not found", 404, "onb_err_404"),
    (b"no", 403, "onb_err_refused"),
    (b"oops", 500, "onb_err_http"),
])
def test_a_playlist_that_is_none_is_said_so(app, tmp_path, serve, body, status, words):
    s = serve({"/list.m3u": (status, {}, body)})
    w, ov = _window(tmp_path)
    _to_form(ov, kind="m3u")
    ov._server.setText(s.base + "/list.m3u")
    _connect(w, ov)
    _failed(w, ov, tr(words, code=status))
    ov._k_back.click()
    assert ov._server.text() == s.base + "/list.m3u" and ov._conn_kind.currentData() == "m3u"


def test_a_big_playlist_loads_and_the_window_goes_on(app, tmp_path, serve):
    s = serve({"/big.m3u": (200, {}, playlist(n=30_000))})
    w, ov = _window(tmp_path)
    _to_form(ov, kind="m3u")
    ov._server.setText(s.base + "/big.m3u")
    beats: list[float] = []
    timer = QTimer()
    timer.timeout.connect(lambda: beats.append(time.monotonic()))
    timer.start(20)
    _connect(w, ov)
    timer.stop()
    _kept(w, ov, 30_000, "m3u")
    gaps = [b - a for a, b in zip(beats, beats[1:], strict=False)]
    # The window's own thread kept beating while the list was read and parsed
    # on a worker (generous: a busy machine runs the whole suite at once).
    assert beats and max(gaps or [0]) < 2.0, max(gaps or [0])


# -- a portal, a media server, a box --------------------------------------------------

def test_a_stalker_portal(app, tmp_path):
    from dopeiptv.ui.dialogs import stalker_available
    if not stalker_available():
        pytest.skip("no Stalker client in this build")
    from tests.test_stalker import CHANNELS, Portal
    p = Portal()
    threading.Thread(target=p.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        w, ov = _window(tmp_path)
        _to_form(ov, kind="stalker")
        ov._server.setText(p.url + "/stalker_portal/c/")
        ov._mac.setText("00:1a:79:12:34:56")
        _connect(w, ov)
        pl, _client = _kept(w, ov, len(CHANNELS), "stalker")
        assert pl["mac"] == "00:1A:79:12:34:56"
        # A portal's films are read page by page as they are opened: not here.
        assert tr("count_movies", n="4") not in ov._k_text.text()
    finally:
        p.shutdown()
        p.server_close()


@pytest.fixture
def media_device():
    from dopeiptv.providers import mediaserver
    store: dict = {}

    class S:
        def value(self, k, d=None):
            return store.get(k, d)

        def setValue(self, k, v):  # noqa: N802 - QSettings' name
            store[k] = v
    mediaserver.install_device_store(S())
    yield
    mediaserver.install_device_store(None)


def test_jellyfin_signed_in_from_the_guide(app, tmp_path, media_device):
    from tests.test_media_servers import CHANNELS, TOKEN, FakeServer, _auth_route, jellyfin_routes
    s = FakeServer({**jellyfin_routes(), ("POST", "/Users/AuthenticateByName"): _auth_route})
    try:
        w, ov = _window(tmp_path)
        _to_form(ov, family="media")
        assert ov._conn_kind.currentData() == "jellyfin"
        ov._server.setText(s.base)
        ov._user.setText("anna")
        ov._pw.setText("secret pw")
        ov._c_connect.click()                          # signs in, then connects
        end = time.monotonic() + 15
        while not (ov._done or ov._k_title.text() == tr("onb_fail_title")) \
                and time.monotonic() < end:
            QApplication.processEvents()
            time.sleep(0.01)
        pl, _client = _kept(w, ov, len(CHANNELS), "jellyfin")
        assert pl["token"] == TOKEN and pl["password"] == "" and "secret pw" not in repr(pl)
        # Its two films and its recording (the server's recordings are films).
        assert tr("count_movies", n="3") in ov._k_text.text()
    finally:
        s.close()


def test_plex_signed_in_in_the_browser_from_the_guide(app, tmp_path, media_device, monkeypatch):
    from dopeiptv.core import xdg
    from dopeiptv.providers import plex
    from dopeiptv.ui import media_signin
    from tests.test_media_servers import FakeServer, plex_routes
    polls = {"n": 0}

    def pin(_q, _b, _h):
        polls["n"] += 1
        return {"id": 7, "code": "wxyz", "authToken": "acct-tok" if polls["n"] > 1 else None}
    s = FakeServer({
        **plex_routes(),
        ("POST", "/api/v2/pins"): {"id": 7, "code": "wxyz"}, ("GET", "/api/v2/pins/7"): pin,
        ("GET", "/api/v2/user"): {"username": "anna"},
        ("GET", "/identity"): {"MediaContainer": {"machineIdentifier": "m1"}}})
    s.routes[("GET", "/api/v2/resources")] = [
        {"name": "Den", "provides": "server", "clientIdentifier": "m1", "accessToken": "den-tok",
         "owned": True, "connections": [{"uri": s.base, "local": True}]}]
    monkeypatch.setattr(plex, "PLEX_TV", s.base)
    monkeypatch.setattr(media_signin, "PIN_EVERY_MS", 30)
    opened: list[str] = []
    monkeypatch.setattr(xdg, "open_url", lambda u: opened.append(u) or True)
    try:
        w, ov = _window(tmp_path)
        _to_form(ov, family="media", kind="plex")
        assert ov._c_title.text() == tr("onb_head_media", name="Plex")
        ov._signin.plex_btn.click()
        end = time.monotonic() + 15
        while not ov._signin.signed_in() and time.monotonic() < end:
            QApplication.processEvents()
            time.sleep(0.01)
        assert ov._signin.signed_in() and opened
        ov._c_connect.click()
        assert _settle(w)
        assert ov._done, ov._k_text.text()
        pl = w.playlist_store.playlists()[0]
        assert pl["kind"] == "plex" and pl["token"] == "den-tok" and pl["server"] == s.base
        # No live TV on this server: its films and series came.
        assert tr("count_movies", n="1") in ov._k_text.text()
    finally:
        s.close()


def test_a_box_at_home_by_its_address(app, tmp_path, serve):
    s = serve({
        "/discover.json": (200, {}, {"FriendlyName": "HDHomeRun FLEX", "ModelNumber": "HDFX",
                                     "DeviceID": "1053ABCD", "TunerCount": 2,
                                     "LineupURL": "http://10.9.9.9/lineup.json"}),
        "/lineup.json": (200, {}, [
            {"GuideNumber": "2.1", "GuideName": "WCBS", "URL": "http://127.0.0.1:9/auto/v2.1"},
            {"GuideNumber": "4.1", "GuideName": "WNBC", "URL": "http://127.0.0.1:9/auto/v4.1"}])})
    w, ov = _window(tmp_path)
    _to_form(ov, family="box", kind="hdhomerun")
    assert ov._c_title.text() == tr("onb_head_box", name="HDHomeRun")
    ov._server.setText(s.base)
    _connect(w, ov)
    _kept(w, ov, 2, "hdhomerun")


def test_just_my_files_and_radio_needs_no_source_and_is_remembered(app, tmp_path):
    w, ov = _window(tmp_path)
    ov._w_next.click()
    ov._cards["files"].click()
    assert not ov.isVisible() and w.places == ["local"]
    assert w.settings.value("welcome_files_only") == "true"
    assert w.playlist_store.playlists() == []
