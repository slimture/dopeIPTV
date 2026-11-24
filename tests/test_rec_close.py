"""The window closing while it records (core/rec_close.py, ui/mw_rec_close.py,
core/recording.py's shutdown *carry*): a ``dopeiptv --server`` that runs
carries on what the window records - its connection here ended first, the
claim let go after - and with none to take them, closing asks first when it
would stop a recording or miss one due within 12 hours: "Keep dopeIPTV
open" the default. tests/test_server_recorder.py runs the hand-over with a
real server and provider."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from dopeiptv.core import rec_claims as rc, rec_close  # noqa: E402
from dopeiptv.core.recording import RecordingManager  # noqa: E402

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
H = 3600.0


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _running(jid="a", stop_in=H, **kw):
    now = time.time()
    j = {"id": jid, "status": "recording", "url": "http://a.example.com/1.ts", "title": "News",
         "start": now - 60, "stop": now + stop_in, "source": "p1", "_claim_state": rc.RECORDING}
    j.update(kw)
    return j


def _sched(jid="b", start_in=H, **kw):
    now = time.time()
    j = {"id": jid, "status": "scheduled", "url": "http://a.example.com/2.ts", "title": "Film",
         "start": now + start_in, "stop": now + start_in + H, "source": "p1"}
    j.update(kw)
    return j


def takes(j):
    return j.get("source") == "p1"


def has_address(j):
    return bool(j.get("url")) or RecordingManager.address_later(j)


# -- what goes on, what closing costs -------------------------------------------------

def test_what_a_server_carries_on():
    now = time.time()
    assert rec_close.carriable(_running(), now, takes, has_address)
    later = _running(url="", url_later={"source": "p1", "stream_id": 5})
    assert rec_close.carriable(later, now, takes, has_address), "a portal's: asked as it starts"
    for j in (_running(stop_in=30),                 # about to end: it stops
              _running(stop=None),                  # started by hand, until stopped
              _running(source="gone"),              # a source the server does not take
              _running(url=""),                     # no way to its address
              {k: v for k, v in _running().items() if k != "_claim_state"},   # never claimed here
              _running(_elsewhere={"who": rc.SERVER}),
              _sched(), None, "x"):
        assert not rec_close.carriable(j, now, takes, has_address), j
    assert not rec_close.carriable(_running(), now, takes, has_address, private=True)
    assert not rec_close.carriable(_running(), now, lambda j: 1 / 0, has_address)


def test_what_closing_would_stop_or_miss():
    now = time.time()
    jobs = [_running("a"), _sched("b", start_in=11 * H), _sched("c", start_in=13 * H),
            _sched("d", start_in=-60, _held=True),              # due now, waiting for room
            _sched("e", start_in=-2 * H),                       # over: stop passed
            _running("f", _elsewhere={"who": rc.SERVER}), {"status": "done"}, None]
    assert rec_close.at_stake(jobs, now, False, takes) == rec_close.Stake(1, 2)
    # A server that runs takes what is due, and carries on what it can.
    assert rec_close.at_stake(jobs, now, True, takes, carried=["a"]) == rec_close.Stake(0, 0)
    assert rec_close.at_stake(jobs, now, True, takes) == rec_close.Stake(1, 0)
    other = [_sched("g", source="gone")]
    assert rec_close.at_stake(other, now, True, takes).due == 1, "one the server does not take"
    assert not rec_close.at_stake([], now, False, takes).any


# -- the window's question, on a stand-in window -----------------------------------------

from dopeiptv.ui import mw_rec_close as mrc  # noqa: E402


class W(mrc._RecCloseMixin):
    def __init__(self, jobs, server=False, answer=0):
        claims = SimpleNamespace(present=lambda who: server and who == rc.SERVER)
        self.rec = SimpleNamespace(jobs=jobs, claims=claims, _no_claims="")
        self.playlist_store = SimpleNamespace(
            get=lambda pid: {"id": pid, "kind": "xtream"} if pid == "p1" else None)
        self.answer = answer
        self.asked: list = []

    def _choice_dialog(self, title, message, options, dont_ask_setting=None):
        self.asked.append((title, message, [o[0] for o in options], dont_ask_setting,
                           "_phone_ask_hook" in self.__dict__))
        return self.answer


def test_nothing_at_stake_nothing_asked():
    w = W([_sched("c", start_in=13 * H)])
    assert w._rec_close_go_on() and w.asked == []


def test_asked_when_closing_would_stop_a_recording():
    from dopeiptv.i18n import tr
    w = W([_running()], answer=0)
    w._phone_ask_hook = lambda *a: 1               # a phone request: not its question
    assert not w._rec_close_go_on(), "Keep dopeIPTV open"
    title, words, options, dont, phone = w.asked[0]
    assert title == tr("rec_close_title") and tr("rec_close_running") in words
    assert tr("rec_close_server") in words and "dopeiptv --server" in words
    assert options == [tr("rec_close_keep"), tr("rec_close_quit")] and dont is None
    assert not phone and w._phone_ask_hook is not None, "the phone's hook put back"
    w.answer = None                                  # Escape, the question closed
    assert not w._rec_close_go_on()
    w.answer = 1
    assert w._rec_close_go_on(), "Quit anyway"


def test_asked_when_closing_would_miss_one_due():
    from dopeiptv.i18n import tr
    w = W([_sched("b", start_in=11 * H)], answer=1)
    assert w._rec_close_go_on()
    assert tr("rec_close_due") in w.asked[0][1]


def test_a_server_that_runs_takes_them_unasked():
    w = W([_running(), _sched("b", start_in=H)], server=True)
    assert w._rec_close_go_on() and w.asked == []
    assert w._rec_close_carry() == ["a"]
    # One it cannot carry on (started by hand) is still asked about - no
    # word of starting a server that runs already.
    from dopeiptv.i18n import tr
    w = W([_running(stop=None)], server=True)
    assert not w._rec_close_go_on()
    assert tr("rec_close_server") not in w.asked[0][1]


def test_never_asked_when_the_app_closes_itself():
    for flag in ("_resetting", "_close_forced"):
        w = W([_running()])
        setattr(w, flag, True)
        assert w._rec_close_go_on() and w.asked == []
    w = W([_running()], server=True)
    w._resetting = True
    assert w._rec_close_carry() == [], "nothing carried on as the settings are reset"


def test_a_failure_closes_as_before():
    w = W([_running()])
    w.rec.jobs = None
    w.rec = SimpleNamespace(jobs=[_running()], claims=SimpleNamespace(present=lambda who: 1 / 0),
                            _no_claims="")
    assert not w._rec_close_server()
    w._choice_dialog = lambda *a, **k: 1 / 0
    assert w._rec_close_go_on(), "a question that cannot be asked never keeps the app open"
    w = W([_running()])
    w._rec_close_asking = True
    assert not w._rec_close_go_on(), "a second close while it asks: the answer decides"


# -- the recorder's side: carried on through the jobs' list ------------------------------

def _manager(path: Path, who: str, spawned: list) -> RecordingManager:
    s = QSettings(str(path), QSettings.Format.IniFormat)
    rec = RecordingManager(s, claims=rc.Claims(rc.folder_for(s), who))
    rec._timer.stop()
    rec.recorder = lambda: ("ffmpeg", "/bin/true")

    def spawn(j, rec=rec):
        spawned.append((who, j["title"]))
        j.update(status="recording", path=str(path.parent / f"{who}-{len(spawned)}.ts"),
                 proc=SimpleNamespace(poll=lambda: None, wait=lambda _t=None: 0,
                                      terminate=lambda: spawned.append((who, "stopped"))))
        Path(j["path"]).write_bytes(b"\x47" * 188)
    rec._spawn = spawn
    return rec


def test_carried_on_through_the_list(app, tmp_path):
    path = tmp_path / "s.ini"
    spawned: list = []
    window = _manager(path, rc.WINDOW, spawned)
    server = _manager(path, rc.SERVER, spawned)
    server.defer = lambda: server.claims.present(rc.WINDOW)
    assert window.claims.present(rc.SERVER) and server.claims.present(rc.WINDOW)
    now = time.time()
    job = window.add_job("http://a.example.com/live/u/p/1.ts", "News", now + 0.3, now + H,
                         channel="One", extra={"source": "p1", "stream_key": "1"})
    server.tick()                       # known from the list, scheduled
    time.sleep(0.4)
    window.tick()
    server.tick()
    theirs = next(j for j in server.jobs if j["id"] == job["id"])
    assert spawned == [(rc.WINDOW, "News")] and theirs["_elsewhere"]["who"] == rc.WINDOW
    first = job["path"]
    window.shutdown(carry=[job["id"]])
    assert spawned[-1] == (rc.WINDOW, "stopped"), "its connection ended first"
    stored = json.loads(QSettings(str(path), QSettings.Format.IniFormat).value("recording_jobs"))
    back = next(s for s in stored if s["id"] == job["id"])
    assert back["status"] == "scheduled" and back["carry"]["part"] == os.path.basename(first)
    assert "proc" not in back and "inplayer" not in back
    server.tick()
    assert theirs["_elsewhere"] and len(spawned) == 2, "the claim still the window's"
    window.let_go_carried()
    # The server ticks every second; on a loaded machine the claim the window
    # let go can take a tick more to be seen free - as the app goes on ticking.
    for _ in range(50):
        server.tick()
        if spawned[-1] == (rc.SERVER, "News"):
            break
        time.sleep(0.05)
    assert spawned[-1] == (rc.SERVER, "News") and theirs["status"] == "recording"
    assert theirs["path"] != first and os.path.getsize(first) == 188
    assert server.carried_from(theirs) == back["carry"]["at"]
    note = json.loads(Path(theirs["path"] + ".info.json").read_text())["description"]
    assert os.path.basename(first) in note
    first_note = json.loads(Path(first + ".info.json").read_text())["description"]
    assert "dopeiptv --server" in first_note
    # Its end is the server's: done there, and the window - when it runs
    # again - never records it a second time.
    server.cancel(job["id"])
    assert server.claims.read(job["id"]).state == rc.DONE
    again = _manager(path, rc.WINDOW, spawned)
    again.tick()
    assert spawned.count((rc.WINDOW, "News")) == 1
    again.shutdown()
    server.shutdown()


def test_a_carry_s_times_are_read_as_from_outside():
    now = time.time()
    j = {"start": now - 60, "stop": now + 60, "carry": {"at": now}}
    assert RecordingManager.carried_from(j) == now
    for carry in ({"at": now - 3600}, {"at": now + 3600}, {"at": "1e999"}, {"at": None},
                  "x", None):
        assert RecordingManager.carried_from({**j, "carry": carry}) is None, carry


def test_closed_nothing_starts_any_more(app, tmp_path):
    spawned: list = []
    rec = _manager(tmp_path / "s.ini", rc.WINDOW, spawned)
    rec.shutdown()
    now = time.time()
    rec.jobs.append({"id": "z", "url": "http://a.example.com/1.ts", "title": "Late",
                     "start": now - 1, "stop": now + H, "status": "scheduled", "folder": "",
                     "path": "", "error": "", "proc": None})
    rec.tick()
    rec._address_came("z", "http://a.example.com/1.ts", "")
    assert spawned == [] and not rec._timer.isActive()


def test_the_server_holds_its_presence_while_it_runs(app, tmp_path):
    spawned: list = []
    server = _manager(tmp_path / "s.ini", rc.SERVER, spawned)
    folder = rc.folder_for(server.settings)
    window = rc.Claims(folder, rc.WINDOW)
    assert window.present(rc.SERVER)
    server.shutdown()
    assert not window.present(rc.SERVER)


# -- on the real window -----------------------------------------------------------------

_CHILD = r"""
import faulthandler, json, os, sys, tempfile, time
faulthandler.dump_traceback_later(120, exit=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QMessageBox
app = QApplication.instance() or QApplication([])
QMessageBox.information = staticmethod(lambda *a, **k: 0)
tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
from dopeiptv import i18n
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw
i18n.set_language("en")
s = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
s.setValue("check_updates", "false")
s.setValue("recordings_dir", os.path.join(tmp, "rec"))
pid = PlaylistStore(s).add({"name": "Min IPTV", "server": "http://example.invalid",
                           "username": "u", "password": "p"})["id"]
w = mw.MainWindow(DemoClient(), s, PlaylistStore(s))
w.show()
for _ in range(30):
    app.processEvents()
now = time.time()
w.rec.add_job("http://example.invalid/live/u/p/1.ts", "Late news", now + 3600, now + 7200,
              extra={"source": pid})
answers = [0, None]
asked = []


def choice(title, message, options, dont_ask_setting=None):
    asked.append(title)
    return answers.pop(0)
w._choice_dialog = choice
out = {}
w.close()
app.processEvents()
out["kept"] = w.isVisible() and w.player is not None and w.rec._timer.isActive()
w.close()                               # Escape: kept open too
app.processEvents()
out["kept_again"] = w.isVisible()
out["asked"] = len(asked)
w._resetting = True                     # Reset all: closes, nothing asked
print(json.dumps(out))
sys.stdout.flush()
w.close()
os._exit(0 if len(asked) == 2 else 3)
"""


def test_the_real_window_stays_open_when_asked_to():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=_REPO_ROOT)
    p = subprocess.run([sys.executable, "-c", _CHILD], capture_output=True, text=True,
                       timeout=150, cwd=_REPO_ROOT, env=env)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-3000:]
    got = json.loads(p.stdout.strip().splitlines()[-1])
    assert got == {"kept": True, "kept_again": True, "asked": 2}, got
