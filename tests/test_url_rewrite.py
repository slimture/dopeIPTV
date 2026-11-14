"""Nerd: a source's rewrite rules for its stream addresses. Plain text and
regular expressions, in order; a rule that cannot work refused when typed
and skipped when played; the line under the rules with every login masked;
every kind of address a client hands out rewritten (live, film, episode,
catch-up, what is shared) only while Nerd is on and the source has rules -
and the window playing, recording and asking the archive at the rewritten
address of a fake server, its requests checked."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dopeiptv.core import catchup, nerd, url_rewrite
from dopeiptv.core.nerd_check import FAIL, INFO, OK, WARN
from dopeiptv.core.url_rewrite import Rule, apply, check, dump, parse, problem
from tests.test_nerd import TS, S

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _nerd_reset(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost,::1")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost,::1")
    yield
    nerd.install(None)
    nerd.set_source(None)


def _on(**kw) -> None:
    nerd.install(S(nerd_enabled="true", **kw))


# -- the rules ----------------------------------------------------------------------

def test_plain_text_replaces_every_occurrence_and_rules_apply_in_order():
    url = "http://old.example:8080/live/anna/pw/1.ts?next=http://old.example:8080/x"
    rules = [Rule("http://", "https://"), Rule("old.example:8080", "cdn.example"),
             Rule("https://cdn", "https://edge.cdn")]
    assert apply(url, rules) == ("https://edge.cdn.example/live/anna/pw/1.ts"
                                 "?next=https://edge.cdn.example/x")
    # The same rules the other way round: the last one finds nothing yet.
    assert apply(url, rules[::-1]) == ("https://cdn.example/live/anna/pw/1.ts"
                                       "?next=https://cdn.example/x")


def test_regular_expressions_with_groups_and_a_query_parameter():
    assert apply("http://h/live/a/b/401.ts",
                 [Rule(r"/(\d+)\.ts$", r"/\1.m3u8", True)]) == "http://h/live/a/b/401.m3u8"
    tvh = "http://tvh:9981/stream/channel/c1?profile=matroska"
    assert apply(tvh, [Rule(r"profile=\w+", "profile=pass", True)]) == \
        "http://tvh:9981/stream/channel/c1?profile=pass"
    assert apply("http://box:8001/1:0:19:283D:", [Rule("$", "?profile=pass", True)]) == \
        "http://box:8001/1:0:19:283D:?profile=pass"
    assert apply("http://h:8080/x", [Rule(r"^http://(?P<host>[^:/]+):8080", r"https://\g<host>", True)]) == \
        "https://h/x"
    # A plain rule's text is not an expression: its dots are dots.
    assert apply("http://a.b/c", [Rule("a.b", "x")]) == "http://x/c"
    assert apply("http://axb/c", [Rule("a.b", "x")]) == "http://axb/c"


@pytest.mark.parametrize("find, replace", [
    ("(", ""), ("[a-", ""), ("(?P<x", ""), ("(a+)+$", ""), ("(.*)*x", ""), ("(x|y+){2,}", ""),
    (r"(\d+)", r"\2"), (r"(\d+)", r"\g<nope>"), ("x" * 600, ""),
])
def test_a_rule_that_cannot_work_is_named_and_skipped(find, replace):
    bad = Rule(find, replace, True)
    assert problem(bad)
    good = Rule("http://", "https://")
    # Never an error in playback: the good rules still apply around it.
    assert apply("http://h/1.ts", [bad, good]) == "https://h/1.ts"


def test_rules_that_can_work():
    assert problem(Rule("(", "[", False)) == ""            # plain text: anything goes
    assert problem(Rule(r"(\d+)", r"<\1>", True)) == ""
    assert problem(Rule("", "x")) == url_rewrite.EMPTY
    assert apply("http://h/1.ts", [Rule("", "x")]) == "http://h/1.ts"


def test_an_address_the_rules_would_empty_stays_as_it_was():
    url = "http://h/1.ts"
    assert apply(url, [Rule(".*", "", True)]) is url
    assert apply("", [Rule("a", "b")]) == ""
    same = "http://h/1.ts"
    assert apply(same, [Rule("nothing-here", "x")]) is same


def test_what_a_source_stores_and_reads_back():
    rules = [Rule("http://", "https://"), Rule(r"(\d+)\.ts", r"\1.m3u8", True), Rule("a", "")]
    text = dump(rules)
    assert isinstance(text, str) and parse(text) == rules
    assert parse([{"find": "a", "replace": "b", "regex": 1}]) == [Rule("a", "b", True)]
    for junk in ("", "  ", "not json", '{"find": "a"}', "[1, null]", '[{"find": ""}]',
                 '[{"find": 3}]', None, 42):
        assert parse(junk) == []
    assert parse([{"find": "a", "replace": None}]) == [Rule("a", "")]
    assert len(parse([{"find": str(i)} for i in range(50)])) == url_rewrite.MAX_RULES
    assert dump([]) == ""


def test_nothing_at_all_while_nerd_is_off_or_the_source_has_no_rules():
    url = "http://h/live/1.ts"
    src = {"rewrite": dump([Rule("http://", "https://")]), "_kind": "m3u"}
    nerd.install(S(nerd_enabled="false"))
    assert url_rewrite.rewrite(url, src) is url
    _on()
    assert url_rewrite.rewrite(url, src) == "https://h/live/1.ts"
    for none in ({"_kind": "m3u"}, {"rewrite": ""}, {"rewrite": "[]"}, None):
        assert url_rewrite.rewrite(url, none) is url
    assert url_rewrite.rewrite("", src) == ""


# -- the line under the rules ---------------------------------------------------------

def test_the_line_shows_what_an_address_becomes_without_the_login():
    sample = "http://h:8080/live/anna/pw-anna/1.ts"
    secrets = ["anna", "pw-anna"]
    c = check([Rule("http://", "https://"), Rule("token=old", "token=t0ps3cret")], sample,
              example=True, secrets=secrets)
    assert (c.state, c.key) == (OK, "nerd_ck_rw_would_example")
    shown = c.params["before"] + c.params["after"] + c.detail
    assert "pw-anna" not in shown and "anna" not in shown.replace("​", "")
    assert "t0ps3cret" not in c.detail and "token=•••" in c.detail
    assert c.params["after"].replace("​", "") == "https://h:8080/live/•••/•••/1.ts"
    real = check([Rule("http://", "https://")], "http://h/play?token=s3cr3t&ch=1", channel="SVT1")
    assert real.key == "nerd_ck_rw_would" and real.params["channel"] == "SVT1"
    assert "s3cr3t" not in real.params["after"] + real.detail
    box = check([Rule("8001", "17999")], "http://root:pa55@box:8001/1:0:19:", secrets=["root", "pa55"])
    assert "pa55" not in box.params["after"] + box.detail and "17999" in box.params["after"]


def test_the_line_names_the_rule_that_cannot_work():
    rules = [Rule("http://", "https://"), Rule("([0-9]+", "x", True)]
    c = check(rules, "http://h/1.ts")
    assert (c.state, c.key, c.params["n"]) == (FAIL, "nerd_ck_rw_bad", 2)
    assert "missing )" in c.params["error"]
    empty = check([Rule("", "x")], "http://h/1.ts")
    assert (empty.state, empty.key, empty.params) == (FAIL, "nerd_ck_rw_empty", {"n": 1})
    assert check([], "http://h/1.ts").state == INFO
    same = check([Rule("nothing", "x")], "http://h/1.ts")
    assert (same.state, same.key) == (WARN, "nerd_ck_rw_same")
    gone = check([Rule("http://", "")], "http://h/1.ts")
    assert (gone.state, gone.key) == (WARN, "nerd_ck_rw_not_address")


# -- every address a client hands out -------------------------------------------------

def _xtream(options):
    from dopeiptv.providers.client import make_client
    # No id: no list cache on disk.
    return make_client({"kind": "xtream", "server": "http://old.example:8080", "username": "anna",
                        "password": "pw", "options": options})


def test_a_clients_live_film_episode_and_archive_addresses_are_rewritten():
    rules = dump([Rule("http://old.example:8080", "https://new.example"),
                  Rule(r"/(\d+)\.ts$", r"/\1.m3u8", True)])
    _on()
    c = _xtream({"rewrite": rules})
    assert c.live_url(401, "ts") == "https://new.example/live/anna/pw/401.m3u8"
    assert c.vod_url(7, "mkv") == "https://new.example/movie/anna/pw/7.mkv"
    assert c.episode_url(9, "mp4") == "https://new.example/series/anna/pw/9.mp4"
    start = datetime(2026, 7, 5, 21, 0, tzinfo=timezone(timedelta(hours=2)))
    urls = c.timeshift_urls(401, start, 30)
    assert urls and all(u.startswith("https://new.example/") for u in urls)
    assert urls[0].endswith("/401.m3u8")
    # Nerd off: the very addresses the client makes.
    nerd.install(S(nerd_enabled="false"))
    assert c.live_url(401, "ts") == "http://old.example:8080/live/anna/pw/401.ts"
    assert c.vod_url(7, "mkv") == "http://old.example:8080/movie/anna/pw/7.mkv"
    assert all(u.startswith("http://old.example:8080/") for u in c.timeshift_urls(401, start, 30))
    # Nerd on, a source without rules: the same.
    _on()
    plain = _xtream({})
    assert plain.live_url(401, "ts") == "http://old.example:8080/live/anna/pw/401.ts"


def test_the_rules_come_before_udpxy():
    from dopeiptv.providers.client import _nerd_lists

    class Stub:
        def live_streams(self, category_id=None):
            return []

        def live_url(self, stream_id, fmt="ts"):
            return "udp://@239.1.1.1:1234"

    _on(nerd_udpxy="http://router:4022")
    stub = Stub()
    _nerd_lists(stub, {"rewrite": dump([Rule("239.1.1.1", "239.9.9.9")]), "_kind": "m3u"}, None)
    assert stub.live_url(1) == "http://router:4022/udp/239.9.9.9:1234"


def test_what_is_shared_is_the_rewritten_address(tmp_path):
    from dopeiptv.services.share import Share
    m3u = tmp_path / "tv.m3u"
    m3u.write_text("#EXTM3U\n#EXTINF:-1 tvg-id=\"svt1.se\",SVT1\nhttp://h/live/svt1.ts?token=old\n",
                   encoding="utf-8")
    pl = {"id": "s1", "name": "TV", "kind": "m3u", "server": str(m3u),
          "options": {"rewrite": dump([Rule("token=old", "token=new")])}}
    _on()
    share = Share(lambda: [pl], guide_path=lambda pid: str(tmp_path / "none.xml"))
    rows, _cats = share.channels(pl)
    assert share.stream_url("s1", str(rows[0]["stream_id"])) == "http://h/live/svt1.ts?token=new"


def test_a_catchup_template_of_a_whole_address_is_rewritten_once():
    start = datetime(2026, 7, 5, 19, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=30)
    now = start + timedelta(hours=2)
    rules = dump([Rule("http://", "https://"), Rule("/archive/", "/vault/")])
    _on()
    whole = {"_kind": "m3u", "rewrite": rules, "catchup_template": "http://arch/archive/{utc}.ts"}
    assert catchup.for_programme(whole, "https://h/live.ts", start, end, now=now) == \
        f"https://arch/vault/{int(start.timestamp())}.ts"
    # Made from the live address, which the client already rewrote: not twice.
    append = {"_kind": "m3u", "rewrite": rules, "catchup_template": "?utc={utc}", "catchup_mode": "append"}
    assert catchup.for_programme(append, "https://h/archive/live.ts", start, end, now=now) == \
        f"https://h/archive/live.ts?utc={int(start.timestamp())}"
    assert catchup.whole_address("http://a/{utc}", "auto") and not catchup.whole_address("?utc={utc}")
    assert catchup.whole_address("", "default", "http://a/{utc}")
    assert not catchup.whole_address("x", "shift") and not catchup.whole_address("", "flussonic")


# -- the source dialog ---------------------------------------------------------------------

@pytest.fixture
def app():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path, app, monkeypatch):
    from PyQt6.QtCore import QSettings

    from dopeiptv.ui.nerd_check_line import CheckLine
    monkeypatch.setattr(CheckLine, "show_problem", lambda self: None)
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s.setValue("nerd_enabled", "true")
    nerd.install(s)
    return s


def _plain(text: str) -> str:
    return text.replace("​", "")


def test_the_panel_keeps_the_rules_in_order_and_only_when_there_are_any(settings):
    from dopeiptv.ui.source_nerd import SourceNerdPanel
    stored = dump([Rule("http://", "https://"), Rule(r"(\d+)\.ts", r"\1.m3u8", True)])
    p = SourceNerdPanel("xtream", {"keep_me": "x", "rewrite": stored})
    ed = p.rewrite
    assert [r.rule() for r in ed.rows] == parse(stored)
    assert p.values() == {"keep_me": "x", "rewrite": stored}
    assert ed.rows[1].regex.isChecked() and not ed.rows[0].up.isEnabled()
    ed.rows[1].up.click()                        # the second goes first
    assert [r.find for r in ed.rules()] == [r"(\d+)\.ts", "http://"]
    ed.add_btn.click()                          # an empty row is no rule
    assert len(ed.rows) == 3 and parse(p.values()["rewrite"]) == ed.rules()
    ed.rows[2].find.setText("8080")
    ed.rows[2].replace.setText("80")
    assert parse(p.values()["rewrite"])[-1] == Rule("8080", "80")
    for row in list(ed.rows):
        row.remove.click()
    assert ed.rows == [] and p.values() == {"keep_me": "x"}
    # Every kind has them: another kind keeps the rows.
    p.rewrite.add(Rule("a", "b"))
    p.set_kind("enigma2")
    assert parse(p.values()["rewrite"]) == [Rule("a", "b")]


def test_the_line_under_the_rules_and_its_test_against_a_panel(settings):
    """The line shows what an example address of the account becomes (its
    login masked); Test finds a real channel, asks the rewritten address
    with the source's user agent and says it streams - or that nothing is
    there."""
    from dopeiptv.ui.dialogs import PlaylistDialog
    from tests.test_catchup import _wait
    from tests.test_sources_side_by_side import Xtream
    xt = Xtream()
    try:
        d = PlaylistDialog(playlist={"name": "Anna", "kind": "xtream", "server": xt.base,
                                     "username": "anna", "password": "pw-anna",
                                     "options": {"user_agent": "kodi",
                                                 "rewrite": dump([Rule(".ts", ".m3u8")])}})
        line = d.nerd_panel.rw_line
        line.show_preview()
        assert line.result.key == "nerd_ck_rw_would_example"
        text = _plain(line.text.text())
        assert f"{xt.base}/live/•••/•••/1.m3u8" in text and "pw-anna" not in text
        line.run(explicit=True)
        assert _wait(lambda: line.result is not None and line.result.key == "nerd_ck_rw_ok"), \
            line.text.text()
        assert line.result.params["channel"] == "TV4 HD"
        asked = [r for r in xt.requests if r["path"] == "/live/anna/pw-anna/401.m3u8"]
        assert asked and asked[-1]["headers"]["user-agent"].startswith("Kodi/")
        assert "pw-anna" not in line.result.detail
        assert "Before:" in line.result.detail and "401.ts" in line.result.detail
        # From now on the line shows that channel.
        line.show_preview()
        assert line.result.key == "nerd_ck_rw_would" and line.result.params["channel"] == "TV4 HD"
        # A rule that sends it nowhere: Test says nothing is there.
        d.nerd_panel.rewrite.rows[0].find.setText("/live/")
        d.nerd_panel.rewrite.rows[0].replace.setText("/nowhere/")
        line.run(explicit=True)
        assert _wait(lambda: line.result is not None and line.result.key == "nerd_ck_rw_missing")
        assert line.result.state == FAIL
    finally:
        xt.close()


def test_a_rule_that_cannot_work_is_refused_by_the_dialog(settings, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from dopeiptv.i18n import tr
    from dopeiptv.ui.dialogs import PlaylistDialog
    said = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: said.append(a[2]))
    d = PlaylistDialog(playlist={"name": "TV", "kind": "m3u", "server": "http://h/tv.m3u"})
    row = d.nerd_panel.rewrite.add()
    row.find.setText("(unclosed")
    row.regex.setChecked(True)
    d._validate()
    assert d.result() == 0 and said and said[0].startswith(tr("nerd_ck_rw_bad", n=1, error="")[:8])
    assert d.nerd_panel.toggle.isChecked() and d.nerd_panel.rw_line.result.state == FAIL
    row.find.setText("(closed)")
    d._validate()
    assert d.result() == 1
    assert parse(d.values()["options"]["rewrite"]) == [Rule("(closed)", "", True)]


# -- the window: playing, recording and catch-up at the rewritten address ---------------------

class Provider:
    """An M3U provider whose streams are only where the rules send them:
    /live2/ and /vault/ answer with video, /live/ and /archive/ do not.
    Records each request, query and all."""

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
                elif p.startswith(("/live2/", "/vault/")):
                    self._send(200, TS, "video/mp2t")
                elif p.startswith(("/live/", "/archive/")):
                    self._send(200, b"<html><body>Not here</body></html>", "text/html")
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
        pass


_CHILD = r"""
import os, sys, json, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
import requests
from dopeiptv.core import nerd
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.core.url_rewrite import Rule, dump
from dopeiptv.providers.client import make_client
from dopeiptv.ui.main_window import MainWindow
from tests.test_url_rewrite import Provider

srv = Provider('#EXTM3U\n#EXTINF:-1 tvg-id="svt1.se" catchup-days="3" catchup-id="svt1-hd" '
               'group-title="Sverige",SVT1 HD\nBASE/live/svt1.ts?token=old\n')
rules = dump([Rule("/live/", "/live2/"), Rule(r"/archive/([\w-]+)/", r"/vault/\1/", True),
              Rule("token=old", "token=new")])
template = srv.base + "/archive/{catchup-id}/{utc}.ts?token=old"
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
s.setValue("playlists", json.dumps([
    {"id": "m", "name": "Rewrite", "kind": "m3u", "server": srv.base + "/list.m3u",
     "options": {"rewrite": rules, "catchup_template": template}}]))
s.setValue("active_playlist", "m")
s.setValue("nerd_enabled", "true")
nerd.install(s)
store = PlaylistStore(s)
client = make_client(store.active())
w = MainWindow(client, s, store)
calls, recorded = [], []
w._start_playback = lambda url, title, icon, key, kind, record=True, item=None, catchup=False: \
    calls.append((url, kind, catchup))
w._recorder_ready = lambda: True
w._within_storage_cap = lambda: True
w.rec.add_job = lambda url, *a, **k: recorded.append(url)


def wait(n):
    for _ in range(1000):
        app.processEvents()
        w.pool.waitForDone(10)
        if len(calls) >= n:
            return True
    return False


def asked(prefix, since=0):
    return [r["path"] for r in srv.requests[since:] if r["path"].startswith(prefix)]


it = next(r for r in client.live_streams(None) if r["name"] == "SVT1 HD")
raw = srv.base + "/live/svt1.ts?token=old"
new = srv.base + "/live2/svt1.ts?token=new"
start = int(time.time() // 60 * 60) - 7200
prog = {"start_timestamp": start, "stop_timestamp": start + 1800, "title": "Rapport"}

# Live: the rewritten address is what the player gets.
w.play_live_channel(it)
assert wait(1) and calls[-1] == (new, "live", False), calls[-1]

# A real mpv plays it: the provider is asked at the rewritten address only.
try:
    import mpv
    m = mpv.MPV(vo="null", ao="null", terminal=False, keep_open="yes")
except Exception as e:
    print("MPV_SKIPPED", e)
    m = None
if m is not None:
    from tests.test_nerd import _plays
    before = len(srv.requests)
    nerd.apply_stream(m, nerd.source(), False, new)
    assert _plays(m, new), new
    assert asked("/live2/svt1.ts?token=new", before) and not asked("/live/", before), srv.requests[before:]
    m.terminate()
    print("MPV_PLAYED")

# Recording: the recorder is handed the rewritten address, and it streams.
w._record_now(it, 30)
assert recorded == [new], recorded
r = requests.get(recorded[-1], timeout=10)
assert r.status_code == 200 and r.content[:1] == b"\x47"

# Catch-up from the source's template: its whole address goes through the
# rules too, and the probe asks it there.
before = len(srv.requests)
w._play_timeshift(it, prog=prog)
assert wait(2), "nothing played"
url, kind, cu = calls[-1]
want = srv.base + "/vault/svt1-hd/%d.ts?token=new" % start
assert (url, kind, cu) == (want, "live", True), (url, want)
assert asked("/vault/", before) and not asked("/archive/", before), srv.requests[before:]

# The app's own archive addresses (no template): rewritten as well.
nerd.set_source(dict(store.active(), options={"rewrite": rules}))
before = len(srv.requests)
w._play_timeshift(it, prog=prog)
assert wait(3)
url = calls[-1][0]
assert url.startswith(new + "&utc=%d" % start), url
assert not asked("/live/", before), srv.requests[before:]

# Nerd off: exactly the addresses the source gives - live, recording, archive.
s.setValue("nerd_enabled", "false")
nerd.install(s)
nerd.set_source(store.active())
w.play_live_channel(it)
assert wait(4) and calls[-1] == (raw, "live", False), calls[-1]
w._record_now(it, 30)
assert recorded[-1] == raw, recorded
before = len(srv.requests)
w._play_timeshift(it, prog=prog)
for _ in range(400):
    app.processEvents()
    w.pool.waitForDone(10)
assert asked("/live/svt1.ts?token=old&utc=%d" % start, before), srv.requests[before:]
assert not asked("/live2/", before) and not asked("/vault/", before)

# Nerd on, a source without rules: as the source gives them.
s.setValue("nerd_enabled", "true")
nerd.install(s)
plain = make_client(dict(store.active(), options={}))
assert plain.live_url(it["stream_id"], "ts") == raw
srv.close()
print("REWRITE_OK")
"""


def test_the_window_plays_records_and_asks_the_archive_at_the_rewritten_address():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               NO_PROXY="127.0.0.1,localhost,::1", no_proxy="127.0.0.1,localhost,::1")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=240)
    assert "REWRITE_OK" in out.stdout, out.stdout + out.stderr[-4000:]
    if "MPV_SKIPPED" in out.stdout:
        pytest.skip("libmpv not available: the addresses were checked, not played")
    assert "MPV_PLAYED" in out.stdout
