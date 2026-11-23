"""A series' page's own actions in the real window (ui/mw_series_page.py):
the header's Favorite, Follow and Watch later pressed and pressed again -
each kept in the store the lists and Mine read, the button saying so -,
the ⋯ menu's Mark as watched - the whole series, every episode
with it - and back, one episode unmarked of a series seen whole (only that one), an episode's menu (its address
copied, Remove from Continue watching, Mark as watched), and a series the
provider will not give: said on the page, and Try again fetches it."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QPoint, QSettings
from PyQt6.QtWidgets import QApplication, QMenu

_tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _tmp)

from dopeiptv.core import series_view as sv
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw_mod
from dopeiptv.ui.main_window import MainWindow

menus, pick = [], []
def _all(menu):
    out = []
    for a in menu.actions():
        if a.menu() is not None:
            out += _all(a.menu())
        elif not a.isSeparator():
            out.append(a)
    return out
def _exec(self, *a, **k):
    acts = _all(self)
    menus.append([x.text() for x in acts])
    if not pick:
        return None
    want = pick.pop(0)
    for x in acts:
        if x.text() == want:
            x.trigger()
            return x
    raise AssertionError(f"no {want!r} in {[x.text() for x in acts]}")
QMenu.exec = _exec

app = QApplication.instance() or QApplication([])
settings = QSettings(os.path.join(_tmp, "s.ini"), QSettings.Format.IniFormat)
store = PlaylistStore(settings)
store.add({"id": "pa", "name": "Pirat", "kind": "xtream", "server": "http://pa.invalid",
           "username": "u", "password": "p", "epg_url": "", "refresh": "never"})
store.set_active("pa")
mw_mod.launch_player = lambda *a, **k: None
w = MainWindow(DemoClient(), settings, store)
w.cover.resolver = None
w._choice_dialog = lambda *a, **k: 0
w.player.play = lambda *a, **k: True

def settle(cond, secs=10):
    end = time.time() + secs
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert cond(), "timed out"

def season(n, count):
    return [{"id": str(n * 1000 + e), "episode_num": e, "title": f"Ep {e}",
             "container_extension": "mkv", "info": {"duration_secs": 2700}}
            for e in range(1, count + 1)]
info = {"info": {"name": "Show 30", "plot": "The series."},
        "episodes": {"1": season(1, 3), "2": season(2, 2)}}
w.client.episode_url = lambda i, ext=None: f"http://pa.invalid/series/{i}.{ext or 'mkv'}"

w.resize(1440, 900)
w.show()
settle(lambda: w.list_model.rowCount() > 0 or w.mode != "live")

# -- a series the provider will not give: said, and Try again fetches it --------------
calls = []
def refused(sid):
    calls.append(sid)
    raise RuntimeError("502 Bad Gateway")
w.client.series_info = refused
series = {"series_id": 130, "name": "Show 30"}
w._open_series_page(series)
page = w._series_page
settle(lambda: page.retry.isVisibleTo(page))
assert page.note.text() == tr("err_series_open") and not w._sp_seasons
assert calls == [130]
w.client.series_info = lambda sid: calls.append(sid) or info
page.retry.click()
settle(lambda: bool(w._sp_seasons))
assert calls == [130, 130], "Try again asked once more"
assert not page.retry.isVisibleTo(page) and [s.number for s in w._sp_seasons] == [1, 2]
h = page.header

# -- Favorite, Follow, Watch later: kept, and the button says so ----------------------
assert not h.fav_btn.isChecked() and not w.series_favs.is_favorite(130)
h.fav_btn.click()
settle(lambda: w.series_favs.is_favorite(130))
assert h.fav_btn.isChecked()
h.fav_btn.click()
settle(lambda: not w.series_favs.is_favorite(130))
assert not h.fav_btn.isChecked()

h.follow_btn.click()
settle(lambda: "130" in w.followed.series)
assert h.follow_btn.isChecked() and h.follow_btn.text() == tr("sp_following")
h.follow_btn.click()
settle(lambda: "130" not in w.followed.series)
assert h.follow_btn.text() == tr("sp_follow")

h.later_btn.click()
settle(lambda: w.watchlist.has_show(None, 130))
assert h.later_btn.isChecked()
h.later_btn.click()
settle(lambda: not w.watchlist.has_show(None, 130))

# -- the ⋯ menu: the whole series watched, and back ----------------------------------
pick.append(tr("ctx_mark_watched"))
w._sp_more_menu(QPoint(0, 0))
settle(lambda: w.show_watched_count(w._sp_ctx) > 0)
assert w.watched.is_series_watched_by_stream(130)
settle(lambda: all(s.watched for s in page.states.values()))   # every episode with it
pick.append(tr("ctx_unmark_watched"))
w._sp_more_menu(QPoint(0, 0))
assert tr("ctx_mark_watched") not in menus[-1], "it offered what was already so"
settle(lambda: w.show_watched_count(w._sp_ctx) == 0)
assert not w.watched.is_series_watched_by_stream(130)
settle(lambda: not any(s.watched for s in page.states.values()))
w._sp_more_menu(QPoint(0, 0))
assert tr("ctx_mark_watched") in menus[-1] and tr("ctx_unmark_watched") not in menus[-1]

# -- an episode's menu ---------------------------------------------------------------
page.season_bar.chips[1].click()                              # season 2, as a click picks it
settle(lambda: "2002" in {sv.episode_key(e) for e in page.shown_episodes()})
eps = {sv.episode_key(e): e for e in page.shown_episodes()}
QApplication.clipboard().setText("")
pick.append(tr("ctx_copy_stream_url"))
w._sp_episode_menu(eps["2002"], QPoint(0, 0))
assert QApplication.clipboard().text() == "http://pa.invalid/series/2002.mkv", \
    QApplication.clipboard().text()

w.resume.record("episode", "2001", 900, 2700, item={"id": "2001"},
                series_ctx={"series_id": 130, "name": "Show 30"})
w._sp_refresh()
settle(lambda: page.states["2001"].progress.started)
pick.append(tr("ctx_continue_remove"))
w._sp_episode_menu(eps["2001"], QPoint(0, 0))
assert w.resume.saved_position("2001", "episode") == 0.0
settle(lambda: not page.states["2001"].progress.started)

pick.append(tr("ctx_mark_watched"))
w._sp_episode_menu(eps["2002"], QPoint(0, 0))
settle(lambda: page.states["2002"].watched)
assert not page.states["2001"].watched, "only that episode"
w._sp_episode_menu(eps["2002"], QPoint(0, 0))
assert tr("ctx_unmark_watched") in menus[-1] and tr("ctx_mark_watched") not in menus[-1]

# -- the whole series watched, then one episode unmarked: only that one ----------------
pick.append(tr("ctx_mark_watched"))
w._sp_more_menu(QPoint(0, 0))
settle(lambda: all(s.watched for s in page.states.values()))
pick.append(tr("ctx_unmark_watched"))
w._sp_episode_menu(eps["2001"], QPoint(0, 0))
settle(lambda: not page.states["2001"].watched)
assert all(s.watched for k, s in page.states.items() if k != "2001"), \
    {k: s.watched for k, s in page.states.items()}
assert not w.watched.is_series_watched_by_stream(130), "the whole mark lifted"
w._open_series_page(series)                                  # read again: kept so
settle(lambda: not page.states["2001"].watched and page.states["1001"].watched)
assert pick == []
print("SERIES_ACTIONS_OK", flush=True)
os._exit(0)
"""


def test_a_series_pages_own_actions_do_what_they_say(tmp_path):
    pytest.importorskip("PyQt6")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", XDG_CONFIG_HOME=str(tmp_path))
    proc = subprocess.run([sys.executable, "-c", _CHILD], capture_output=True, text=True,
                          env=env, cwd=_REPO_ROOT, timeout=300)
    assert "SERIES_ACTIONS_OK" in proc.stdout, (
        f"stdout={proc.stdout[-3000:]!r}\nstderr={proc.stderr[-5000:]!r}")
