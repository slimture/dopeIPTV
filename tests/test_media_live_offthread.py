"""A media server's live channel is opened only off the window's thread.

Jellyfin and Emby give a live channel's address through PlaybackInfo with
the live stream opened, Plex through a tune: seconds on a slow server, and a
tuner taken. The docked player asked for it off the window's thread; a
multiview cell, a cast and an immediate recording asked on it (the window
froze), and Nerd's Copy stream address - and the list's Copy stream URL, as
the menu opened - made the server open a channel nobody would play.

Runs in a subprocess, as the other window tests do.
"""
import os
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os, tempfile, threading, time
from functools import partial
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])
tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
s = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
s.setValue("nerd_enabled", "false")
from dopeiptv.core import nerd
nerd.install(s)
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers import mediaserver
from dopeiptv.providers.client import DemoClient, make_client
from dopeiptv.ui import mw_cast
from dopeiptv.ui.main_window import MainWindow
from tests.test_media_servers import TOKEN, UID, FakeServer, jellyfin_routes, plex_routes

mediaserver.install_device_store(None)
CID = "f1" * 16
routes = jellyfin_routes()
routes[("POST", f"/Items/{CID}/PlaybackInfo")] = {"PlaySessionId": "ps1", "MediaSources": [
    {"Id": "ms1", "LiveStreamId": "ls1", "SupportsDirectStream": True, "Container": "ts"}]}
srv = FakeServer(routes)
c = make_client({"kind": "jellyfin", "server": srv.base, "username": "anna", "token": TOKEN,
                 "options": {"user_id": UID}})
c.authenticate()
news = dict(next(r for r in c.live_streams(None) if r["name"] == "News 24"), _kind="live")
opened = []
real_open = c._live_open


def counting(row):
    opened.append("worker" if threading.current_thread() is not threading.main_thread() else "WINDOW")
    return real_open(row)


c._live_open = counting
w = MainWindow(DemoClient(), s, PlaylistStore(s))
w.client = c
w.mode = "live"


def wait(cond, secs=8.0):
    end = time.monotonic() + secs
    while not cond() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    return cond()


def fresh():
    c._live_cache.clear()
    opened.clear()


# The window's stand-in has the client's own methods only: a media server
# has no play_url, so what the play path asks for is its live_url.
fresh()
assert w._link_waits(partial(w._stream_for, news))[0] == "live_url"
assert opened == []

# -- a multiview cell (on a busy machine the worker may already have
# begun by the next line: what matters is that it is never the window's thread)
cells = []
w.add_to_multiview = lambda url, title, *a, **k: cells.append(url)
fresh()
w._add_channel_to_multiview(news)
assert "WINDOW" not in opened and cells == [], (opened, cells)
assert wait(lambda: cells), "the cell gets its address"
assert opened == ["worker"] and "LiveStreamId=ls1" in cells[0], (opened, cells)

# -- a cast
cast = {}


class Dialog:
    def __init__(self, window, url, title, *a, **k):
        cast["url"] = url

    def exec(self):
        return 0


mw_cast.CastDialog = Dialog
fresh()
w._open_cast_dialog(news)
assert "WINDOW" not in opened and not cast, (opened, cast)
assert wait(lambda: cast), "the cast dialog opens once the address is in"
assert opened == ["worker"] and "LiveStreamId=ls1" in cast["url"], (opened, cast)

# -- a recording, right now, over its own connection
jobs = []
w._recorder_ready = lambda: True
w._within_storage_cap = lambda: True
w.rec.add_job = lambda url, title, *a, **k: jobs.append(url)
fresh()
w._record_now(news, 30)
assert "WINDOW" not in opened and jobs == [], (opened, jobs)
assert wait(lambda: jobs), "the recording starts once the address is in"
assert opened == ["worker"] and "LiveStreamId=ls1" in jobs[0], (opened, jobs)

# -- Copy stream address (Nerd) and the list's Copy stream URL: never opened
toasts = []
w._show_toast = lambda text, ms=0: toasts.append(text)
fresh()
w._copy_stream_url(news)
app.processEvents()
copied = app.clipboard().text()
assert opened == [] and f"/Videos/{CID}/stream?" in copied and "LiveStreamId" not in copied, copied
assert toasts == [tr("nerd_url_copied")], toasts
app.clipboard().setText("")
w._copy_row_address(news)
assert wait(lambda: app.clipboard().text()), "copied"
assert opened == [] and f"/Videos/{CID}/stream?" in app.clipboard().text()
assert len(srv.hits("POST", f"/Items/{CID}/PlaybackInfo")) == 3

# Plex: a channel's own address is the tune - nothing to copy, said so.
psrv = FakeServer(plex_routes(dvr=True))
p = make_client({"kind": "plex", "server": psrv.base, "username": "anna", "token": "den-tok",
                 "options": {"server_id": "m1"}})
p.authenticate()
kqed = dict(p.live_streams(None)[0], _kind="live")
w.client = p
toasts.clear()
app.clipboard().setText("")
w._copy_stream_url(kqed)
w._copy_row_address(kqed)
app.processEvents()
assert toasts == [tr("nerd_url_none")] * 2 and app.clipboard().text() == "", toasts
assert not psrv.hits("POST", "/livetv/dvrs/7/channels/5.1/tune")
print("MEDIA_LIVE_OFFTHREAD_OK", flush=True)
os._exit(0)
"""


def test_a_media_server_channel_is_opened_off_the_window_thread_and_never_to_copy():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert "MEDIA_LIVE_OFFTHREAD_OK" in out.stdout, out.stdout[-2000:] + out.stderr[-4000:]
