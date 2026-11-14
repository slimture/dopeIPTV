"""Nerd: a source's own catch-up address. Every placeholder and every mode
filled in for fixed programmes in time zones that are not UTC, unknown
placeholders named, nothing at all while Nerd is off, the source dialog
storing what it shows and testing it against a fake panel - and the window
playing an archive programme from the template's address, first asked by
its probe and then played by a real mpv from a fake server."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dopeiptv.core import catchup, nerd
from dopeiptv.core.nerd_check import FAIL, INFO, OK, WARN
from tests.test_nerd import TS, S

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STOCKHOLM_SUMMER = timezone(timedelta(hours=2))
NEW_YORK = timezone(timedelta(hours=-5))
# 21:00:07 in Stockholm (summer time) is 19:00:07 UTC.
START = datetime(2026, 7, 5, 21, 0, 7, tzinfo=STOCKHOLM_SUMMER)
END = START + timedelta(minutes=45)
NOW = START + timedelta(hours=2, minutes=29, seconds=53)
T_START = 1783278007          # START as Unix time
T_END = T_START + 2700
T_NOW = T_START + 8993


@pytest.fixture(autouse=True)
def _nerd_reset(monkeypatch):
    # The fake servers are on the loopback; a proxy of this machine's own
    # stays out of it.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost,::1")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost,::1")
    yield
    nerd.install(None)
    nerd.set_source(None)


def test_the_fixed_times_are_what_the_tests_say():
    assert int(START.timestamp()) == T_START
    assert START.astimezone(timezone.utc).hour == 19


# -- placeholders ------------------------------------------------------------------

@pytest.mark.parametrize("token, value", [
    ("{utc}", str(T_START)), ("${start}", str(T_START)), ("{start}", str(T_START)),
    ("{utcend}", str(T_END)), ("${end}", str(T_END)), ("{end}", str(T_END)),
    ("{lutc}", str(T_NOW)), ("${now}", str(T_NOW)), ("${timestamp}", str(T_NOW)),
    ("{duration}", "2700"), ("${duration}", "2700"), ("{duration:60}", "45"),
    ("{duration:3600}", "0"), ("{offset}", "8993"), ("{offset:60}", "149"),
    ("${offset:1}", "8993"),
    # The start's parts are UTC: 21:00 in Stockholm is 19:00.
    ("{Y}", "2026"), ("{m}", "07"), ("{d}", "05"), ("{H}", "19"), ("{M}", "00"), ("{S}", "07"),
    ("{utc:Y-m-d:H-M}", "2026-07-05:19-00"), ("${start:YmdHMS}", "20260705190007"),
    ("{utcend:H-M}", "19-45"), ("{lutc:H:M:S}", "21:30:00"), ("${now:Y}", "2026"),
    ("{catchup-id}", "svt1-hd"),
])
def test_every_placeholder(token, value):
    assert catchup.render(token, START, END, NOW, catchup_id="svt1-hd") == value
    assert catchup.unknown_placeholders(token) == []


def test_the_start_in_utc_crosses_midnight_and_the_year():
    # Just after midnight on New Year's Day in Stockholm is still the old
    # year in UTC; in New York the evening before is already the new one.
    sthlm = datetime(2026, 1, 1, 0, 30, tzinfo=timezone(timedelta(hours=1)))
    assert catchup.render("{Y}-{m}-{d} {H}:{M}", sthlm, sthlm, sthlm) == "2025-12-31 23:30"
    ny = datetime(2025, 12, 31, 21, 15, tzinfo=NEW_YORK)
    assert catchup.render("{Y}-{m}-{d} {H}:{M}", ny, ny, ny) == "2026-01-01 02:15"
    assert catchup.render("{utc}", ny, ny, ny) == str(int(ny.timestamp()))


def test_a_programme_still_running_and_one_in_the_future_count_from_zero():
    later = START + timedelta(hours=1)
    assert catchup.render("{offset} {duration}", later, START, START) == "0 0"


def test_no_catchup_id_leaves_nothing():
    assert catchup.render("/a/{catchup-id}/b", START, END, NOW) == "/a//b"


def test_unknown_placeholders_are_named_as_written():
    tpl = "http://x/{foo}/{utc}/${bar}/{Y:2}/{duration:x}/{duration:0}/{utc:}/{foo}/{catchup-id:1}"
    assert catchup.unknown_placeholders(tpl) == [
        "{foo}", "${bar}", "{Y:2}", "{duration:x}", "{duration:0}", "{utc:}", "{catchup-id:1}"]
    # Filled in, the unknown ones stay as they were written.
    out = catchup.render(tpl, START, END, NOW)
    assert out.startswith(f"http://x/{{foo}}/{T_START}/${{bar}}/{{Y:2}}/")
    # Case matters: {m} is the month, {M} the minute; {y} is nothing.
    assert catchup.unknown_placeholders("{y}{MM}") == ["{y}", "{MM}"]
    assert catchup.unknown_placeholders("plain/{ not one } {}") == []


# -- modes -----------------------------------------------------------------------

LIVE = "http://tv.example/live/svt1.m3u8"


def url(template="", mode="auto", live=LIVE, **kw):
    return catchup.archive_url(live, START, END, template, mode, NOW, **kw)


def test_default_is_the_template_itself():
    assert url("http://arch.example/{catchup-id}/{utc}-{duration}.ts", "default",
               catchup_id="c7") == f"http://arch.example/c7/{T_START}-2700.ts"
    # No template: the channel's own catchup-source stands in.
    assert url("", "default", channel_source="https://cs.example/{utc}") == f"https://cs.example/{T_START}"
    assert url("", "default") is None


def test_append_goes_after_the_live_address():
    assert url("?utc={utc}&lutc={lutc}", "append") == f"{LIVE}?utc={T_START}&lutc={T_NOW}"
    # A live address that has a query already takes the template with &.
    assert url("?utc={utc}", "append", live=LIVE + "?token=t0k") == f"{LIVE}?token=t0k&utc={T_START}"
    assert url("&utc={utc}", "append") == f"{LIVE}?utc={T_START}"
    assert url("/timeshift/{duration:60}", "append", live="http://h/ch") == "http://h/ch/timeshift/45"
    assert url("", "append", channel_source="?from={utc}") == f"{LIVE}?from={T_START}"
    assert url("", "append") is None


def test_shift_puts_utc_and_lutc_on_the_live_address():
    assert url(mode="shift") == f"{LIVE}?utc={T_START}&lutc={T_NOW}"
    assert url(mode="shift", live=LIVE + "?a=1") == f"{LIVE}?a=1&utc={T_START}&lutc={T_NOW}"
    # The template is not used.
    assert url("http://elsewhere/{utc}", "shift") == f"{LIVE}?utc={T_START}&lutc={T_NOW}"


@pytest.mark.parametrize("live, archive", [
    ("http://fs.example:8080/svt1/index.m3u8?token=s3", f"http://fs.example:8080/svt1/index-{T_START}-2700.m3u8?token=s3"),
    ("http://fs.example/svt1/mono.m3u8", f"http://fs.example/svt1/mono-{T_START}-2700.m3u8"),
    ("https://fs.example/svt1/video.m3u8", f"https://fs.example/svt1/video-{T_START}-2700.m3u8"),
    ("http://fs.example/svt1/mpegts?token=s3", f"http://fs.example/svt1/archive-{T_START}-2700.ts?token=s3"),
    ("http://fs.example/svt1/stream.ts", f"http://fs.example/svt1/archive-{T_START}-2700.ts"),
])
def test_flussonic_rewrites_the_live_address(live, archive):
    assert url(mode="flussonic", live=live) == archive
    assert url(mode="fs", live=live) == archive                   # Kodi's other names
    assert url(mode="Flussonic-HLS", live=live) == archive


@pytest.mark.parametrize("live", ["http://fs.example/svt1/", "http://fs.example/svt1/live.mp4",
                                  "http://fs.example", "udp://239.0.0.1:1234"])
def test_flussonic_leaves_other_addresses_alone(live):
    assert url(mode="flussonic", live=live) is None


def test_auto_reads_the_template():
    assert url("http://a.example/{utc}") == f"http://a.example/{T_START}"     # a whole address: default
    assert url("?utc={utc}") == f"{LIVE}?utc={T_START}"                      # the rest: append
    assert url("") is None and url("   ") is None                           # nothing: the app's own
    assert url("http://a.example/{utc}", "no-such-mode") == f"http://a.example/{T_START}"


def test_only_a_web_address_is_ever_tried():
    assert url("rtmp://a.example/{utc}", "default") is None
    assert url("{utc}", "default") is None
    assert url("?utc={utc}", "append", live="udp://239.0.0.1:1234") is None


# -- Nerd off is no change -----------------------------------------------------------

SRC = {"_kind": "m3u", "catchup_template": "http://a.example/{utc}"}


def _programme(source):
    return catchup.for_programme(source, LIVE, START, END, now=NOW)


def test_nothing_while_nerd_is_off():
    nerd.install(S(nerd_enabled="false"))
    assert not catchup.wanted(SRC) and _programme(SRC) is None
    nerd.install(None)
    assert _programme(SRC) is None


def test_with_nerd_on_only_a_source_that_asks():
    nerd.install(S(nerd_enabled="true"))
    assert _programme(SRC) == f"http://a.example/{T_START}"
    assert _programme({"_kind": "xtream", "catchup_mode": "shift"}) == f"{LIVE}?utc={T_START}&lutc={T_NOW}"
    # Nothing set, or the mode left on auto: the app's own.
    assert _programme({"_kind": "m3u"}) is None
    assert _programme({"_kind": "m3u", "catchup_mode": "auto", "catchup_template": " "}) is None
    assert _programme(None) is None
    # A kind without an archive the app plays.
    for kind in ("stalker", "enigma2", "tvheadend", "hdhomerun"):
        assert _programme(dict(SRC, _kind=kind)) is None, kind


def test_m3u_channels_keep_their_catchup_id(tmp_path):
    from dopeiptv.providers.client import M3UClient
    path = tmp_path / "tv.m3u"
    path.write_text('#EXTM3U\n#EXTINF:-1 catchup="default" catchup-id="svt1-hd" catchup-days="3",SVT1\n'
                    'http://h/svt1.ts\n#EXTINF:-1,Kanal 5\nhttp://h/k5.ts\n', encoding="utf-8")
    c = M3UClient(str(path))
    rows = c.live_streams(None)
    assert rows[0]["catchup_id"] == "svt1-hd" and rows[0]["tv_archive"] == 1
    assert "catchup_id" not in rows[1]


# -- the checks ------------------------------------------------------------------------

XT_LIVE = "http://panel.example:8080/live/anna/pw-anna/401.ts"


def _plain(text: str) -> str:
    return text.replace("​", "")


def test_the_line_shows_the_address_without_the_login():
    c = catchup.check_template("?utc={utc}&lutc={lutc}", "append", XT_LIVE, "TV4 HD", now=NOW,
                               secrets=["anna", "pw-anna"])
    assert c.state == OK and c.key == "nerd_ck_cu_would" and c.params["channel"] == "TV4 HD"
    start = int((NOW - timedelta(hours=2)).replace(second=0).timestamp())
    assert _plain(c.params["url"]) == f"http://panel.example:8080/live/•••/•••/401.ts?utc={start}&lutc={T_NOW}"
    assert "​" in c.params["url"]              # it may wrap in a narrow line
    assert "pw-anna" not in c.detail and "anna" not in c.detail
    assert "Programme: 2026-07-05 19:30 - 20:30 UTC" in c.detail


def test_the_line_on_an_example_channel_and_a_token_in_the_query():
    c = catchup.check_template("", "shift", "http://h/ch/index.m3u8?token=s3cr3t", example=True, now=NOW)
    assert c.key == "nerd_ck_cu_would_example" and "s3cr3t" not in c.params["url"] + c.detail


def test_the_line_names_what_it_cannot_fill_in():
    c = catchup.check_template("http://a/{utc}/{bogus}", "auto", LIVE, now=NOW)
    assert (c.state, c.key, c.params) == (FAIL, "nerd_ck_tpl_unknown", {"names": "{bogus}"})
    assert "Known: " in c.detail and "{catchup-id}" in c.detail


def test_the_line_says_why_nothing_comes_of_it():
    assert catchup.check_template("", "auto", LIVE).state == INFO
    assert catchup.check_template("", "flussonic", "http://h/ch/live.mp4").key == "nerd_ck_cu_not_flussonic"
    assert catchup.check_template("", "default", LIVE).key == "nerd_ck_cu_need_template"
    c = catchup.check_template("rtmp://x/{utc}", "default", LIVE)
    assert (c.state, c.key) == (FAIL, "nerd_ck_cu_not_address")


def test_secrets_of_a_playlist_address():
    src = {"server": "http://h/get.php?username=bob&password=hunter22&type=m3u", "username": "", "password": ""}
    assert catchup.secrets_of(src) == ["bob", "hunter22"]
    assert catchup.shown("http://h/bob/hunter22/1.ts", catchup.secrets_of(src)) == "http://h/•••/•••/1.ts"


class Archive:
    """A provider: an M3U playlist, live and archive streams, and the
    wrong answers an archive address can get. Records each request."""

    def __init__(self, playlist: str = ""):
        self.requests: list[dict] = []
        self.playlist = playlist
        srv = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                srv.requests.append({"path": self.path,
                                     "headers": {k.lower(): v for k, v in self.headers.items()}})
                p = self.path
                if p.startswith("/list.m3u"):
                    self._send(200, srv.playlist.replace("BASE", srv.base).encode(), "audio/x-mpegurl")
                elif p.startswith(("/live/", "/archive/")):
                    self._send(200, TS, "video/mp2t")
                elif p.startswith("/page/"):
                    self._send(200, b"<html><body>Login</body></html>", "text/html")
                elif p.startswith("/denied/"):
                    self._send(401, b"", "text/plain")
                else:
                    self._send(404, b"", "text/plain")

            def _send(self, code, body, ctype):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

        self.httpd = _Quiet(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class _Quiet(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass        # a probe that hangs up after the first bytes is no error


@pytest.fixture
def archive():
    srv = Archive()
    yield srv
    srv.close()


def test_test_asks_the_archive_and_says_what_came_back(archive):
    b = archive.base
    ok = catchup.check_archive(f"{b}/archive/svt1/{T_START}.ts?token=s3cr3t", "VLC/3.0.21",
                               {"X-Box": "vu"}, channel="SVT1 HD", secrets=["s3cr3t"])
    assert (ok.state, ok.key) == (OK, "nerd_ck_cu_ok")
    assert ok.params["channel"] == "SVT1 HD" and ok.params["code"] == 200
    got = archive.requests[-1]
    assert got["path"] == f"/archive/svt1/{T_START}.ts?token=s3cr3t"
    assert got["headers"]["user-agent"] == "VLC/3.0.21" and got["headers"]["x-box"] == "vu"
    assert got["headers"]["range"] == "bytes=0-8191"
    assert "s3cr3t" not in ok.detail and "Content-Type: video/mp2t" in ok.detail
    page = catchup.check_archive(f"{b}/page/1", channel="SVT1 HD")
    assert (page.state, page.key) == (WARN, "nerd_ck_cu_not_stream")
    assert catchup.check_archive(f"{b}/nothing").key == "nerd_ck_cu_missing"
    assert catchup.check_archive(f"{b}/denied/1").key == "nerd_ck_denied"
    gone = catchup.check_archive("http://127.0.0.1:9/x.ts", timeout=3)
    assert gone.state == FAIL and gone.key in ("nerd_ck_unreachable", "nerd_ck_timeout")


def test_what_counts_as_a_stream():
    assert catchup.is_stream("", b"\x47\x40\x00") is True
    assert catchup.is_stream("application/vnd.apple.mpegurl", b"#EXTM3U\n") is True
    assert catchup.is_stream("text/html", b"\x47") is False
    assert catchup.is_stream("", b'{"error": 1}') is False
    assert catchup.is_stream("text/plain", b"hello") is None


# -- the source dialog -------------------------------------------------------------------

@pytest.fixture
def app():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path, app, monkeypatch):
    from PyQt6.QtCore import QSettings

    from dopeiptv.ui.nerd_check_line import CheckLine
    # A failed Test opens the problem window on its own; never in a test.
    monkeypatch.setattr(CheckLine, "show_problem", lambda self: None)
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s.setValue("nerd_enabled", "true")
    nerd.install(s)
    return s


def _rows(panel):
    from PyQt6.QtWidgets import QFormLayout
    out = []
    for i in range(panel.form.rowCount()):
        item = panel.form.itemAt(i, QFormLayout.ItemRole.FieldRole)
        if item is not None and item.widget() is not None:
            out.append(item.widget())
    return out


def test_the_panel_stores_the_template_and_mode_only_when_set(settings):
    from dopeiptv.ui.source_nerd import SourceNerdPanel
    p = SourceNerdPanel("m3u", {"keep_me": "x", "catchup_template": "?utc={utc}", "catchup_mode": "append"})
    assert p.cu_template in _rows(p) and p.cu_mode in _rows(p) and p.cu_line in _rows(p)
    assert p.cu_template.text() == "?utc={utc}" and p.cu_mode.currentData() == "append"
    assert p.values() == {"keep_me": "x", "catchup_template": "?utc={utc}", "catchup_mode": "append"}
    p.cu_mode.setCurrentIndex(p.cu_mode.findData("flussonic"))
    assert not p.cu_template.isEnabled()              # flussonic makes its own
    p.cu_mode.setCurrentIndex(p.cu_mode.findData("auto"))
    p.cu_template.setText("  ")
    assert p.values() == {"keep_me": "x"}
    # Every mode is there; auto, the default, is never stored.
    assert [p.cu_mode.itemData(i) for i in range(p.cu_mode.count())] == list(catchup.MODES)
    p.cu_template.setText("http://a/{utc}")
    p.cu_mode.setCurrentIndex(p.cu_mode.findData("default"))
    # A kind without an archive: the rows go, and so do the options.
    p.set_kind("enigma2")
    assert p.cu_template not in _rows(p) and p.cu_line not in _rows(p)
    assert "catchup_template" not in p.values() and "catchup_mode" not in p.values()
    p.set_kind("xtream")
    assert p.values()["catchup_template"] == "http://a/{utc}" and p.values()["catchup_mode"] == "default"


def test_the_source_dialog_keeps_the_catchup_options(settings):
    from dopeiptv.ui.dialogs import PlaylistDialog
    d = PlaylistDialog(playlist={"name": "IPTV", "kind": "m3u", "server": "http://h/tv.m3u",
                                 "options": {"catchup_mode": "shift"}})
    assert d.nerd_panel.cu_mode.currentData() == "shift"
    d.nerd_panel.cu_mode.setCurrentIndex(d.nerd_panel.cu_mode.findData("default"))
    d.nerd_panel.cu_template.setText("http://arch/{catchup-id}/{utc}.ts")
    assert d.values()["options"] == {"catchup_mode": "default",
                                     "catchup_template": "http://arch/{catchup-id}/{utc}.ts"}
    # Nerd off: no panel, and what the source had rides along unchanged.
    settings.setValue("nerd_enabled", "false")
    nerd.install(settings)
    d = PlaylistDialog(playlist={"name": "IPTV", "kind": "m3u", "server": "http://h/tv.m3u",
                                 "options": {"catchup_mode": "shift"}})
    assert d.nerd_panel is None and d.values()["options"] == {"catchup_mode": "shift"}


def _wait(cond, secs=20.0):
    from PyQt6.QtWidgets import QApplication
    end = time.monotonic() + secs
    while time.monotonic() < end:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_the_dialogs_line_and_its_test_against_a_panel(settings):
    """The line shows a programme two hours ago on an example channel (the
    login masked); Test finds a real channel of the account, asks its
    archive with the source's own user agent and says it streams; from
    then on the line shows that channel."""
    from dopeiptv.i18n import tr
    from dopeiptv.ui.dialogs import PlaylistDialog
    from tests.test_sources_side_by_side import Xtream
    xt = Xtream()
    try:
        d = PlaylistDialog(playlist={"name": "Anna", "kind": "xtream", "server": xt.base,
                                     "username": "anna", "password": "pw-anna",
                                     "options": {"catchup_mode": "shift", "user_agent": "kodi"}})
        line = d.nerd_panel.cu_line
        line.show_preview()
        text = _plain(line.text.text())
        assert line.result.key == "nerd_ck_cu_would_example"
        assert f"{xt.base}/live/•••/•••/1.ts?utc=" in text and "pw-anna" not in text
        assert text.startswith(_plain(tr("nerd_ck_cu_would_example", url="")).strip())
        line.run(explicit=True)
        assert _wait(lambda: line.result is not None and line.result.key.startswith("nerd_ck_cu_ok")), \
            line.text.text()
        assert line.result.state == OK and line.result.params["channel"] == "TV4 HD"
        asked = [r for r in xt.requests if r["path"] == "/live/anna/pw-anna/401.ts"]
        assert asked and asked[-1]["headers"]["user-agent"].startswith("Kodi/")
        assert "pw-anna" not in line.result.detail
        # The line now shows the channel Test found.
        d.nerd_panel.cu_template.setText("x")          # changed: looked at again
        line.show_preview()
        assert line.result.key == "nerd_ck_cu_would" and line.result.params["channel"] == "TV4 HD"
        # Another address: that channel no longer stands for the source.
        d.server.setText("http://other.example")
        line.show_preview()
        assert line.result.key == "nerd_ck_cu_would_example"
        # Unknown placeholders: Test says so without asking anyone.
        d.nerd_panel.cu_mode.setCurrentIndex(d.nerd_panel.cu_mode.findData("default"))
        d.nerd_panel.cu_template.setText("http://a/{nope}")
        before = len(xt.requests)
        line.run(explicit=True)
        assert _wait(lambda: line.result is not None and line.result.key == "nerd_ck_tpl_unknown")
        assert len(xt.requests) == before
    finally:
        xt.close()


# -- the window: an archive programme played from the template -------------------------------

_CHILD = r"""
import os, sys, json, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from dopeiptv.core import nerd
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import make_client
from dopeiptv.ui.main_window import MainWindow
from tests.test_catchup import Archive

srv = Archive('#EXTM3U\n#EXTINF:-1 tvg-id="svt1.se" catchup-days="3" catchup-id="svt1-hd" '
              'group-title="Sverige",SVT1 HD\nBASE/live/svt1.ts\n')
template = srv.base + "/archive/{catchup-id}/{utc}-{duration}.ts?lutc={lutc}"
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
s.setValue("playlists", json.dumps([
    {"id": "m", "name": "Archive", "kind": "m3u", "server": srv.base + "/list.m3u",
     "options": {"catchup_template": template, "user_agent": "kodi"}}]))
s.setValue("active_playlist", "m")
s.setValue("nerd_enabled", "true")
nerd.install(s)
store = PlaylistStore(s)
client = make_client(store.active())
w = MainWindow(client, s, store)
calls = []
w._start_playback = lambda url, title, icon, key, kind, record=True, item=None, catchup=False: \
    calls.append((url, kind, catchup))


def wait(n):
    for _ in range(1000):
        app.processEvents()
        w.pool.waitForDone(10)
        if len(calls) >= n:
            return True
    return False


it = next(r for r in client.live_streams(None) if r["name"] == "SVT1 HD")
live = srv.base + "/live/svt1.ts"
start = int(time.time() // 60 * 60) - 7200
prog = {"start_timestamp": start, "stop_timestamp": start + 1800, "title": "Rapport"}

# Nerd on, a template: the probe asks the template's address first, with the
# source's user agent, and that is what plays.
w._play_timeshift(it, prog=prog)
assert wait(1), "nothing played"
url, kind, cu = calls[-1]
want = srv.base + "/archive/svt1-hd/%d-%d.ts?lutc=" % (start, 32 * 60)
assert url.startswith(want) and kind == "live" and cu is True, (url, want)
probes = [r for r in srv.requests if r["path"].startswith("/archive/")]
assert len(probes) == 1 and probes[0]["headers"]["user-agent"].startswith("Kodi/"), probes
# Only the template's address was asked: it streamed.
assert not [r for r in srv.requests if "utc=" in r["path"] and r["path"].startswith("/live/")]

# A real mpv plays it from the fake server.
try:
    import mpv
    m = mpv.MPV(vo="null", ao="null", terminal=False, keep_open="yes")
except Exception as e:
    print("MPV_SKIPPED", e)
    m = None
if m is not None:
    from tests.test_nerd import _plays
    nerd.apply_stream(m, nerd.source(), False, url)
    assert _plays(m, url), url
    got = [r for r in srv.requests if r["path"].startswith("/archive/")][-1]
    assert got["path"] == url[len(srv.base):] and got["headers"]["user-agent"].startswith("Kodi/"), got
    m.terminate()
    print("MPV_PLAYED")

# Live playback never changes.
w.play_live_channel(it)
assert wait(2)
assert calls[-1] == (live, "live", False), calls[-1]

# A template that does not stream costs only the try: the app's own come after.
nerd.set_source(dict(store.active(), options={"catchup_template": srv.base + "/page/{utc}"}))
before = len(srv.requests)
w._play_timeshift(it, prog=prog)
assert wait(3)
url = calls[-1][0]
asked = [r["path"] for r in srv.requests[before:]]
assert asked[0].startswith("/page/") and url.startswith(live + "?utc=%d" % start), (asked, url)

# Nerd off: exactly as before - the app's own addresses, the template never asked.
s.setValue("nerd_enabled", "false")
nerd.install(s)
nerd.set_source(store.active())
before = len(srv.requests)
w._play_timeshift(it, prog=prog)
assert wait(4)
url = calls[-1][0]
assert url.startswith(live + "?utc=%d&lutc=" % start), url
assert not [r for r in srv.requests[before:] if r["path"].startswith(("/archive/", "/page/"))]
assert w._nerd_catchup_url(it, start, start + 1800) is None

# Nerd on, the source asks for nothing: nothing of Nerd's.
s.setValue("nerd_enabled", "true")
nerd.install(s)
nerd.set_source(dict(store.active(), options={}))
assert w._nerd_catchup_url(it, start, start + 1800) is None
nerd.set_source(store.active())
assert w._nerd_catchup_url(it, start, start + 1800).startswith(
    srv.base + "/archive/svt1-hd/%d-1800.ts?lutc=" % start)
srv.close()
print("CATCHUP_OK")
"""


def test_the_window_plays_an_archive_programme_from_the_template():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               NO_PROXY="127.0.0.1,localhost,::1", no_proxy="127.0.0.1,localhost,::1")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert "CATCHUP_OK" in out.stdout, out.stdout + out.stderr[-4000:]
    if "MPV_SKIPPED" in out.stdout:
        pytest.skip("libmpv not available: the archive was asked, not played")
    assert "MPV_PLAYED" in out.stdout
