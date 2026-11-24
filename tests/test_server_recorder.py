"""Scheduled recordings with the window closed (services/server_recorder.py):
``dopeiptv --server`` records what is scheduled, each job by one recorder
alone (core/rec_claims.py) - the window or the server, never both, a
crashed one's taken over -, never one connection over the account's limit
next to the tuner's channels, a channel a device watches recorded from its
buffer and one it records shared with a device that tunes to it; the
window shows what the server records and its stop reaches it; series rules
planned from the guide on disk while no window runs; and the whole of it in
a real ``--server`` without a widget."""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QCoreApplication, QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from dopeiptv.core import hdhr, rec_claims as rc, server_page  # noqa: E402
from dopeiptv.core.dvr_pool import DvrPool  # noqa: E402
from dopeiptv.core.recording import RecordingManager  # noqa: E402
from dopeiptv.services import home_server as hs  # noqa: E402
from dopeiptv.services.server_recorder import ServerRecorder  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
TOKEN = "tok-" + "x" * 20
H = 3600.0


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def spin(cond=lambda: False, timeout: float = 10.0) -> bool:
    """The event loop turned (the recorder's timers and queued signals)
    until *cond* holds."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return bool(cond())


# -- a provider on the loopback, serving TS --------------------------------------------------

class Provider(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        self.connections: dict[str, int] = {}
        self.open_now = 0
        self.lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), _ProviderHandler)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


def _pkt(head: bytes) -> bytes:
    return head + b"\xff" * (188 - len(head))


#: A stream's start as a recording looks for it (core/dvr_record.KeyframeStart):
#: a PAT naming the PMT on PID 0x100, the PMT naming H.264 on 0x101, and a
#: key frame there.
TS_START = (_pkt(bytes([0x47, 0x40, 0x00, 0x10, 0x00, 0x00, 0xB0, 0x0D, 0x00, 0x01, 0xC1,
                        0x00, 0x00, 0x00, 0x01, 0xE1, 0x00, 0, 0, 0, 0]))
            + _pkt(bytes([0x47, 0x41, 0x00, 0x10, 0x00, 0x02, 0xB0, 0x12, 0x00, 0x01, 0xC1,
                          0x00, 0x00, 0xE1, 0x01, 0xF0, 0x00, 0x1B, 0xE1, 0x01, 0xF0, 0x00,
                          0, 0, 0, 0]))
            + _pkt(bytes([0x47, 0x41, 0x01, 0x30, 0x01, 0x40])))


class _ProviderHandler(BaseHTTPRequestHandler):
    server: Provider

    def log_message(self, *a) -> None:
        pass

    def do_GET(self) -> None:
        srv = self.server
        with srv.lock:
            srv.connections[self.path] = srv.connections.get(self.path, 0) + 1
            srv.open_now += 1
        try:
            self.send_response(200)
            self.send_header("Content-Type", "video/mp2t")
            self.end_headers()
            pkt = b"\x47" + b"x" * 187
            self.wfile.write(TS_START)
            while True:
                self.wfile.write(pkt * 40)
                self.wfile.flush()
                time.sleep(0.01)
        except OSError:
            pass
        finally:
            with srv.lock:
                srv.open_now -= 1


@pytest.fixture
def provider():
    p = Provider()
    threading.Thread(target=p.serve_forever, daemon=True).start()
    yield p
    p.shutdown()
    p.server_close()


class FakeClient:
    def __init__(self, base: str) -> None:
        self.base = base
        self.max_connections = None

    def live_categories(self):
        return [{"category_id": "1", "category_name": "News"}]

    def live_streams(self, _cat=None):
        return [{"stream_id": 1, "name": "One", "category_id": "1", "epg_channel_id": "one.tv"},
                {"stream_id": 2, "name": "Two", "category_id": "1", "epg_channel_id": "two.tv"}]

    def live_url(self, sid, fmt="ts"):
        return f"{self.base}/live/u/p/{sid}.ts"


PL = {"id": "p1", "name": "Home", "kind": "xtream", "server": "http://x.invalid",
      "username": "u", "password": "p"}


class Host:
    """The headless host's part the recorder plays (headless.HeadlessHost)."""

    def __init__(self, pool: DvrPool) -> None:
        self._pool = pool
        self.recorder: ServerRecorder | None = None

    def sources(self):
        return [PL]

    def filters(self, pid):
        return set(), set(), {}

    def app_use(self, pid):
        return self.recorder.connections(pid) if self.recorder else (0, [])

    def app_session(self, pid, sid):
        return self.recorder.session_for(pid, sid) if self.recorder else None

    def recordings(self):
        return self.recorder.status() if self.recorder else None

    def pool(self):
        return self._pool

    def save(self, changes):
        return hs.Config()

    def page(self):
        return server_page.render({"bg": "#101010"}, {"bg": "#ffffff"}, {})


def _settings(tmp_path: Path) -> QSettings:
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s.setValue("playlists", json.dumps([PL]))
    s.setValue("active_playlist", "p1")
    s.setValue("recordings_dir", str(tmp_path / "rec"))
    s.sync()
    return s


@pytest.fixture
def served(app, tmp_path, provider, monkeypatch):
    """A server with its recorder, ticked by hand. Its provider is on the
    loopback here: counted as one on the network (core/connections.py
    counts no file and no server of this computer's own)."""
    from dopeiptv.core import connections
    monkeypatch.setattr(connections, "is_network", lambda u: "://" in str(u or ""))
    s = _settings(tmp_path)
    pool = DvrPool(tmp_path / "dvr", 60.0, 10**9, min_free=0)
    host = Host(pool)
    client = FakeClient(provider.base)
    cfg = hs.Config(open=hs.OPEN_HOME, port=0, token=TOKEN, device_id=hdhr.new_device_id())
    srv = hs.HomeServer(host, cfg, bind="127.0.0.1", discovery_port=0,
                        make_client=lambda _pl: client,
                        guide_path=lambda pid: str(tmp_path / f"epg_{pid}.xml"))
    srv.start()
    srv.restream.grace = 0.3
    srv.restream.TICK = 0.1
    claims = rc.Claims(rc.folder_for(s), rc.SERVER)
    rec = ServerRecorder(s, srv, host.pool, claims, plan=False,
                         guide_path=lambda pid: str(tmp_path / f"epg_{pid}.xml"))
    rec.rec._timer.stop()
    host.recorder = rec
    yield SimpleNamespace(srv=srv, rec=rec, mgr=rec.rec, host=host, provider=provider,
                          pool=pool, settings=s, claims=claims, tmp=tmp_path)
    rec.close()
    srv.stop()
    pool.close_all(wait=2.0)


def _job(mgr: RecordingManager, sid: int, title: str, base: str, start: float | None = None,
         stop_in: float = H) -> dict:
    now = time.time()
    start = now - 1 if start is None else start
    return mgr.add_job(f"{base}/live/u/p/{sid}.ts", title, start, now + stop_in,
                       channel=title, icon="",
                       extra={"source": "p1", "source_kind": "xtream", "stream_key": str(sid)})


def _watch(srv, path: str):
    r = OPENER.open(f"http://127.0.0.1:{srv.port}{path}", timeout=10)
    assert r.status == 200 and r.read(188 * 10)
    return r


def _size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


# -- the server records ------------------------------------------------------------------------

def test_the_server_records_a_job_due_with_the_window_closed(served):
    p = served.provider
    job = _job(served.mgr, 1, "News", p.base, stop_in=4.0)
    assert job["status"] == "recording", "taken as it came due"
    assert job["background"] and job["recorder"] is not None and job.get("proc") is None
    assert spin(lambda: _size(job["path"]) > 188 * 100)
    assert p.connections == {"/live/u/p/1.ts": 1}
    path = job["path"]
    assert path.startswith(str(served.tmp / "rec")) and path.endswith(".ts")
    info = json.loads(Path(path + ".info.json").read_text())
    assert info["channel"] == "News" and info["start"]
    # A device tuning to the channel it records shares that one connection.
    r = _watch(served.srv, f"/stream/p1/1?token={TOKEN}")
    try:
        assert r.read(188 * 100)
        assert p.connections == {"/live/u/p/1.ts": 1}, "one connection for both"
        st = served.srv.status("http://127.0.0.1")
        assert [j["title"] for j in st["recordings"]["now"]] == ["News"]
        assert st["recordings"]["now"][0]["state"] == "recording"
    finally:
        r.close()
    claim = served.claims.read(job["id"])
    assert claim.state == rc.RECORDING and claim.who == rc.SERVER and claim.info["own"]
    assert "url" not in claim.info and "/live/" not in json.dumps(claim.info), "no address"
    # Its stop time: done, its claim finished - never recorded again by anyone.
    assert spin(lambda: (served.mgr.tick() or True) and job["status"] != "recording", 10)
    assert spin(lambda: job["status"] == "done" and served.claims.read(job["id"]).final, 5)
    assert served.claims.read(job["id"]).state == rc.DONE
    assert _size(path) > 188 * 100
    served.settings.sync()
    assert job["id"] not in (served.settings.value("recording_jobs") or "")


def test_a_channel_a_device_watches_is_recorded_from_its_buffer(served):
    p = served.provider
    r = _watch(served.srv, f"/stream/p1/2?token={TOKEN}")
    try:
        job = _job(served.mgr, 2, "Two's show", p.base)
        assert job["status"] == "recording" and not job["inplayer"] and job["background"]
        assert spin(lambda: _size(job["path"]) > 188 * 100)
        assert p.connections == {"/live/u/p/2.ts": 1}, "no connection of its own"
        assert served.claims.read(job["id"]).info["own"] is False
    finally:
        r.close()


def test_one_connection_too_many_holds_it_until_there_is_room(served, caplog):
    """The account allows one (it says nothing): a device watches channel 2,
    so a recording of channel 1 waits - said once - and starts by itself
    once the device left; nothing is ever opened beyond the limit."""
    p = served.provider
    r = _watch(served.srv, f"/stream/p1/2?token={TOKEN}")
    with caplog.at_level("INFO", logger="dopeiptv"):
        job = _job(served.mgr, 1, "Film", p.base)
        served.mgr.tick()
        served.mgr.tick()
    assert job["status"] == "scheduled" and job["_held"]
    assert caplog.text.count("waits for a free connection") == 1
    assert p.connections == {"/live/u/p/2.ts": 1}
    assert served.claims.read(job["id"]) is None, "held, not kept from the window"
    st = served.srv.status("http://127.0.0.1")["recordings"]
    assert [j["state"] for j in st["now"]] == ["held"]
    # The device leaves: its channel, nobody's now, gives way.
    r.close()
    assert spin(lambda: (served.mgr.tick() or True) and job["status"] == "recording", 10)
    assert spin(lambda: p.connections.get("/live/u/p/1.ts") == 1)
    assert spin(lambda: p.open_now == 1, 5), "the device's channel let go first"


def test_held_to_its_stop_time_it_fails_saying_why(served):
    p = served.provider
    r = _watch(served.srv, f"/stream/p1/2?token={TOKEN}")
    try:
        job = _job(served.mgr, 1, "Film", p.base, stop_in=1.5)
        assert job["_held"]
        assert spin(lambda: (served.mgr.tick() or True) and job["status"] == "failed", 6)
        assert job["error"] == RecordingManager.NO_ROOM
        assert "/live/u/p/1.ts" not in p.connections
    finally:
        r.close()


def test_the_restream_counts_what_the_server_records(served):
    """With a recording over the one connection, a device asking for
    another channel is refused - never a second connection."""
    p = served.provider
    _job(served.mgr, 1, "News", p.base)
    assert spin(lambda: p.connections.get("/live/u/p/1.ts") == 1)
    with pytest.raises(urllib.error.HTTPError) as e:
        OPENER.open(f"http://127.0.0.1:{served.srv.port}/stream/p1/2?token={TOKEN}", timeout=10)
    assert e.value.code == 503
    assert "/live/u/p/2.ts" not in p.connections


# -- one recorder per job ------------------------------------------------------------------------

def _manager(settings_path: Path, who: str, spawned: list) -> RecordingManager:
    s = QSettings(str(settings_path), QSettings.Format.IniFormat)
    rec = RecordingManager(s, claims=rc.Claims(rc.folder_for(s), who))
    rec._timer.stop()
    rec.recorder = lambda: ("ffmpeg", "/bin/true")

    def spawn(j, rec=rec):
        spawned.append((who, j["title"]))
        j.update(status="recording", path=str(settings_path.parent / f"{j['id']}.ts"),
                 proc=SimpleNamespace(poll=lambda: None, terminate=lambda: None,
                                      wait=lambda _t=None: 0))
    rec._spawn = spawn
    return rec


def _stored(path: Path, *jobs: dict) -> None:
    s = QSettings(str(path), QSettings.Format.IniFormat)
    s.setValue("recording_jobs", json.dumps(list(jobs)))
    s.sync()


def _due_job(jid: str, title: str = "News", stop_in: float = H) -> dict:
    now = time.time()
    return {"id": jid, "url": "http://a.example.com/live/u/p/1.ts", "title": title,
            "start": now - 1, "stop": now + stop_in, "status": "scheduled", "folder": "",
            "source": "p1", "stream_key": "1", "channel": "One"}


def test_two_recorders_record_a_job_once(app, tmp_path):
    path = tmp_path / "s.ini"
    _stored(path, _due_job("j1"))
    spawned: list = []
    a = _manager(path, rc.SERVER, spawned)
    b = _manager(path, rc.SERVER, spawned)
    a.tick()
    b.tick()
    assert spawned == [(rc.SERVER, "News")], "one of the two"
    jb = next(j for j in b.jobs if j["id"] == "j1")
    assert jb["status"] == "recording" and jb["_elsewhere"]["who"] == rc.SERVER
    # Stopped where it records: the other shows it done, records nothing.
    a.cancel("j1")
    b.tick()
    assert jb["status"] == "done" and "_elsewhere" not in jb and len(spawned) == 1
    a.shutdown()
    b.shutdown()


def test_the_jobs_list_is_one_for_both(app, tmp_path):
    """A job one recorder schedules, the other knows at its next look; one
    cancelled in one is cancelled in the other - and its claim says so, so
    the other never takes it."""
    path = tmp_path / "s.ini"
    spawned: list = []
    window = _manager(path, rc.WINDOW, spawned)
    server = _manager(path, rc.SERVER, spawned)
    now = time.time()
    later = window.add_job("http://a.example.com/live/u/p/1.ts", "Later", now + H, now + 2 * H,
                           extra={"source": "p1"})
    server.tick()
    assert [j["title"] for j in server.jobs] == ["Later"]
    window.update_job_times(later["id"], now + 2 * H, now + 3 * H)
    server.tick()
    assert server.jobs[0]["start"] == now + 2 * H, "the newer edit"
    window.cancel(later["id"])
    server.tick()
    assert server.jobs[0]["status"] == "cancelled"
    assert server.claims.read(later["id"]).state == rc.CANCELLED
    soon = server.add_job("http://a.example.com/live/u/p/2.ts", "Soon", now + H, now + 2 * H,
                          extra={"source": "p1"})
    window.tick()
    assert {j["id"] for j in window.jobs if j["status"] == "scheduled"} == {soon["id"]}
    window.shutdown()
    server.shutdown()


_HOLDER = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
from dopeiptv.core import rec_claims as rc
c = rc.Claims(sys.argv[2], "server")
print(c.take(sys.argv[3], {"title": "News", "start": time.time() - 1})[0], flush=True)
c.write(sys.argv[3], rc.RECORDING, {"title": "News"})
time.sleep(120)
"""


@pytest.mark.skipif(os.name != "posix", reason="SIGKILL")
def test_a_crashed_recorder_s_job_is_taken_over(app, tmp_path):
    path = tmp_path / "s.ini"
    _stored(path, _due_job("j1"))
    folder = rc.folder_for(QSettings(str(path), QSettings.Format.IniFormat))
    proc = subprocess.Popen([sys.executable, "-c", _HOLDER, str(ROOT), folder, "j1"],
                            stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == rc.TAKEN
        spawned: list = []
        window = _manager(path, rc.WINDOW, spawned)
        window.tick()
        job = next(j for j in window.jobs if j["id"] == "j1")
        assert not spawned and job["status"] == "recording" and job["_elsewhere"]
        proc.send_signal(signal.SIGKILL)
        proc.wait(10)
        window.tick()                   # it no longer runs: free again
        window.tick()                   # due: taken here, the rest recorded
        assert spawned == [(rc.WINDOW, "News")] and window.claims.mine("j1")
        window.shutdown()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_the_window_shows_what_the_server_records_and_its_stop_reaches_it(app, tmp_path):
    path = tmp_path / "s.ini"
    _stored(path, _due_job("j1"))
    spawned: list = []
    server = _manager(path, rc.SERVER, spawned)
    server.tick()
    assert spawned == [(rc.SERVER, "News")]
    # The window opens later: the job is no longer in the list, its claim is.
    window = _manager(path, rc.WINDOW, spawned)
    job = next(j for j in window.jobs if j["id"] == "j1")
    assert job["status"] == "recording" and job["_elsewhere"]["who"] == rc.SERVER
    assert job["title"] == "News" and job["channel"] == "One"
    window.tick()
    assert len(spawned) == 1 and window.active_count() == 1
    from dopeiptv.ui.mw_recording import rec_where
    from dopeiptv.i18n import tr
    assert rec_where(job) == tr("rec_by_server")
    window.cancel("j1")                 # Stop recording, in the window
    server.tick()
    assert server.jobs[0]["status"] == "done"
    window.tick()
    assert job["status"] == "done" and window.active_count() == 0 and len(spawned) == 1
    window.shutdown()
    server.shutdown()


def test_while_a_window_runs_the_server_leaves_it_what_comes_due(app, tmp_path):
    path = tmp_path / "s.ini"
    spawned: list = []
    window = _manager(path, rc.WINDOW, spawned)
    server = _manager(path, rc.SERVER, spawned)
    server.defer = lambda: server.claims.present(rc.WINDOW)
    _stored(path, _due_job("j1"))
    window.claims.touch_jobs()
    server.tick()
    assert not spawned and server.claims.read("j1") is None, "not even claimed"
    window.tick()
    assert spawned == [(rc.WINDOW, "News")]
    server.tick()
    assert next(j for j in server.jobs if j["id"] == "j1")["_elsewhere"]["who"] == rc.WINDOW
    # The window closes: what it recorded ends there, as before; what comes
    # due after, the server records.
    window.shutdown()
    _stored(path, _due_job("j2", "Later"))
    server.claims.touch_jobs()
    server.tick()
    assert spawned[-1] == (rc.SERVER, "Later")
    server.shutdown()


# -- series rules from the guide on disk -----------------------------------------------------

def _guide(tmp_path: Path, start: float) -> None:
    def t(x: float) -> str:
        return time.strftime("%Y%m%d%H%M%S +0000", time.gmtime(x))
    xml = ('<?xml version="1.0" encoding="UTF-8"?><tv>'
           '<channel id="one.tv"><display-name>One</display-name></channel>'
           f'<programme start="{t(start)}" stop="{t(start + 1800)}" channel="one.tv">'
           '<title>The Show</title></programme>'
           f'<programme start="{t(start + 1800)}" stop="{t(start + 3600)}" channel="one.tv">'
           '<title>Something else</title></programme></tv>')
    (tmp_path / "epg_p1.xml").write_text(xml, encoding="utf-8")


def test_series_rules_are_planned_from_the_guide_on_disk(served, monkeypatch):
    from dopeiptv.core import series_rules
    from dopeiptv.providers.epg import XmltvGuide
    monkeypatch.setattr(XmltvGuide, "PARSE_ELSEWHERE", False)
    start = (time.time() // 60 + 120) * 60
    _guide(served.tmp, start)
    s = served.settings
    s.setValue(series_rules.PAD_BEFORE_KEY, 2)
    s.setValue(series_rules.PAD_AFTER_KEY, 5)
    store = series_rules.SeriesRuleStore(s, "p1")
    store.add(series_rules.make_rule("The Show", channel={
        "stream_id": 1, "name": "One", "epg_channel_id": "one.tv"}))
    store.add(series_rules.make_rule("Something", series_rules.WORD,
                                     action=series_rules.REMIND))
    s.sync()
    served.rec.plan_soon()
    assert spin(lambda: any(j.get("rule") for j in served.mgr.jobs), 20)
    [job] = [j for j in served.mgr.jobs if j.get("rule")]
    assert job["title"] == "The Show" and job["stream_key"] == "1" and job["source"] == "p1"
    assert job["start"] == start - 120 and job["stop"] == start + 1800 + 300
    assert job["url"].endswith("/live/u/p/1.ts") and job["status"] == "scheduled"
    assert job["channel"] == "One"
    # Done again: nothing twice; a reminder rule sets nothing here.
    served.rec.plan_soon()
    spin(lambda: not served.rec._planning, 10)
    spin(timeout=0.5)
    assert len([j for j in served.mgr.jobs if j.get("rule")]) == 1
    page = served.srv.status("http://127.0.0.1")["recordings"]
    assert [j["title"] for j in page["next"]] == ["The Show"]


def test_no_series_planning_while_a_window_runs(served, monkeypatch):
    from dopeiptv.core import series_rules
    from dopeiptv.providers.epg import XmltvGuide
    monkeypatch.setattr(XmltvGuide, "PARSE_ELSEWHERE", False)
    _guide(served.tmp, (time.time() // 60 + 120) * 60)
    series_rules.SeriesRuleStore(served.settings, "p1").add(series_rules.make_rule(
        "The Show", channel={"stream_id": 1, "name": "One", "epg_channel_id": "one.tv"}))
    served.settings.sync()
    window = rc.Claims(served.claims.folder, rc.WINDOW)
    window.hold_presence()
    served.rec.plan_soon()
    spin(timeout=1.0)
    assert not served.rec._planning and not served.mgr.jobs
    window.drop_presence()


# -- a real --server ---------------------------------------------------------------------------

_RUN = r"""
import json, os, signal, sys, threading, time
sys.path.insert(0, sys.argv[1])
claims_dir, job_id = sys.argv[2], sys.argv[3]

def watch():
    from dopeiptv.core import rec_claims as rc
    c = rc.Claims(claims_dir, rc.WINDOW)
    end = time.monotonic() + 40
    state = None
    while time.monotonic() < end:
        got = c.read(job_id)
        state = got.state if got else None
        if state in rc.FINAL:
            break
        time.sleep(0.2)
    bad = [m for m in ("PyQt6.QtWidgets", "PyQt6.QtGui", "mpv") if m in sys.modules]
    sys.stdout.write("STATE %s LOADED %r\n" % (state, bad))
    sys.stdout.flush()
    os.kill(os.getpid(), signal.SIGTERM)

threading.Thread(target=watch, daemon=True).start()
from dopeiptv.launcher import main
sys.argv = ["dopeiptv", "--server", "--port", "0", "--no-discovery"]
sys.exit(main())
"""


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM")
def test_dopeiptv_server_records_with_no_window_and_no_widget(tmp_path, provider):
    env = dict(os.environ, HOME=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path / "config"),
               XDG_CACHE_HOME=str(tmp_path / "cache"), XDG_DATA_HOME=str(tmp_path / "data"),
               DOPEIPTV_LOG="info", QT_QPA_PLATFORM="offscreen")
    env.pop("DOPEIPTV_LOG_FILE", None)
    conf = tmp_path / "config" / "dopeiptv" / "dopeiptv.conf"
    conf.parent.mkdir(parents=True)
    s = QSettings(str(conf), QSettings.Format.IniFormat)
    now = time.time()
    job = {"id": "abc123", "url": f"{provider.base}/live/u/p/1.ts", "title": "Night news",
           "start": now - 1, "stop": now + 6, "status": "scheduled", "folder": "",
           "source": "p1", "source_kind": "xtream", "stream_key": "1", "channel": "One",
           "stream_icon": "", "path": "", "error": ""}
    s.setValue("playlists", json.dumps([PL]))
    s.setValue("active_playlist", "p1")
    s.setValue("recordings_dir", str(tmp_path / "rec"))
    s.setValue("recording_jobs", json.dumps([job]))
    s.sync()
    claims_dir = str(conf.parent / rc.FOLDER)
    r = subprocess.run([sys.executable, "-c", _RUN, str(ROOT), claims_dir, job["id"]],
                       cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=90)
    assert "STATE done LOADED []" in r.stdout, r.stdout + r.stderr
    assert r.returncode == 0, r.stderr
    files = [f for f in (tmp_path / "rec").iterdir() if f.suffix == ".ts"]
    assert len(files) == 1 and files[0].name.startswith("Night news")
    assert files[0].stat().st_size > 188 * 100
    assert provider.connections == {"/live/u/p/1.ts": 1}
    assert "server: recording what is scheduled" in r.stderr
    assert re.search(r"record: Night news - read into the server's buffer", r.stderr)
    s.sync()
    assert "abc123" not in (s.value("recording_jobs") or "")
