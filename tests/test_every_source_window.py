"""Every source in the real window (offscreen, in a child process): the TV
place's category column offers it only with two sources of one's own or
more - and takes it away again; picked, every source's channels in one
list under each source's name, as each source's own lists show them (a
hidden category, a locked one while the lock is shut and a channel hidden
by hand left out, a renamed one by its own name; for a kids profile nothing
for adults), read off the window's thread - another source's live lists
alone, never its films or series, its guide from its copy on disk and never
downloaded; one list of uniform rows, the filter and a grid of tiles as on
any list. A row of the source in use plays as its own; another source's
plays from that source without switching to it - its address asked for
only then, the list staying where it is, the row marked playing, the status
line saying where it is from - and so does channel up and down through
them; its menu is its own (Play, its own source's favourite, where it is
from). A kids profile plays another source's channel only when that
source's own lists, read for it, hold it."""
from __future__ import annotations

import os
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import faulthandler, json, os, tempfile, time
from datetime import datetime, timedelta, timezone
faulthandler.dump_traceback_later(240, exit=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox
app = QApplication.instance() or QApplication([])
QMessageBox.warning = staticmethod(lambda *a, **k: 0)
tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)

from dopeiptv import i18n
from dopeiptv.core import every_source as es
from dopeiptv.core import profiles
from dopeiptv.core.profiles import MAIN_ID, ProfileSettings
from dopeiptv.core.stores import CategoryOverrides, ChannelOverrides, FavoriteStore, PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers import epg as epgmod
from dopeiptv.providers.client import DemoClient
from dopeiptv.services import search_sources as ss
from dopeiptv.ui import main_window as mw, theme

i18n.set_language("en")
mw.launch_player = lambda *a, **k: None                    # never a real mpv window
downloads = []


def no_download(self):
    downloads.append(str(self.cache_path))
    raise OSError("offline")


epgmod.XmltvGuide.PARSE_ELSEWHERE = False
epgmod.XmltvGuide._download = no_download


class Pirat(DemoClient):
    def live_categories(self):
        return [{"category_id": "sp", "category_name": "Sport"},
                {"category_id": "x", "category_name": "XXX Adult"}]

    def live_streams(self, cat=None):
        rows = [{"stream_id": 1, "name": "Vegas Sport", "category_id": "sp", "stream_icon": ""},
                {"stream_id": 2, "name": "Pirat Two", "category_id": "sp", "stream_icon": ""},
                {"stream_id": 3, "name": "Late", "category_id": "x", "stream_icon": ""}]
        return [r for r in rows if cat in (None, r["category_id"])]

    def live_url(self, sid, fmt="ts"):
        return f"http://pirat/live/{sid}.{fmt}"


class Nordic(DemoClient):
    asked = []
    lists = []

    def authenticate(self):
        return {"user_info": {"auth": 1}}

    def live_categories(self):
        return [{"category_id": "n", "category_name": "News"},
                {"category_id": "h", "category_name": "Hidden"},
                {"category_id": "l", "category_name": "Locked"},
                {"category_id": "xx", "category_name": "Adults 18+"}]

    def live_streams(self, cat=None):
        Nordic.lists.append("live")
        rows = [{"stream_id": 8, "name": "Vegas Nordic", "category_id": "n", "epg_channel_id": "vn"},
                {"stream_id": 9, "name": "Nordic Nine", "category_id": "n"},
                {"stream_id": 10, "name": "Hidden row", "category_id": "h"},
                {"stream_id": 11, "name": "Locked row", "category_id": "l"},
                {"stream_id": 12, "name": "By hand", "category_id": "n"},
                {"stream_id": 13, "name": "Adult row", "category_id": "xx"},
                {"stream_id": 14, "name": "Rename me", "category_id": "n"}]
        return [r for r in rows if cat in (None, r["category_id"])]

    def vod_streams(self, cat=None):
        Nordic.lists.append("vod")
        return []

    def series_list(self, cat=None):
        Nordic.lists.append("series")
        return []

    def live_url(self, sid, fmt="ts"):
        Nordic.asked.append(sid)
        return f"http://nordic/live/{sid}.{fmt}"


def pump(n=10):
    for _ in range(n):
        app.processEvents()
        w.pool.waitForDone(5)
        if w._sa_pools:
            for p in w._sa_pools:
                p.waitForDone(5)


def settle(cond, secs=15, what=""):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            pump(2)
            if cond():
                return
        pump(2)
        time.sleep(0.01)
    assert cond(), "timed out: " + what


def cat_rows():
    return [w.cat_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(w.cat_list.count())]


def shown():
    return [r.get("_header") or r.get("name") for r in w.list_model.items()]


def row_of(name):
    return next(i for i, r in enumerate(w.list_model.items()) if r.get("name") == name)


def pick(name):
    w.listw.setCurrentIndex(w.list_model.index(row_of(name)))


def write_nordic_guide():
    # Nordic's guide on this computer, from when it was the source in use
    # (written once Nordic is a source: the window prunes the guides of
    # sources it does not have as it starts).
    now =datetime.now(timezone.utc).replace(microsecond=0)
    fmt = "%Y%m%d%H%M%S +0000"
    os.makedirs(os.path.dirname(epgmod.epg_cache_path("b")), exist_ok=True)
    with open(epgmod.epg_cache_path("b"), "wb") as f:
        f.write(('<tv><channel id="vn"><display-name>Vegas Nordic</display-name></channel>'
                 f'<programme channel="vn" start="{(now - timedelta(minutes=10)).strftime(fmt)}" '
                 f'stop="{(now + timedelta(minutes=50)).strftime(fmt)}"><title>Nordic Show</title>'
                 f'</programme><programme channel="vn" start="{(now + timedelta(minutes=50)).strftime(fmt)}" '
                 f'stop="{(now + timedelta(minutes=90)).strftime(fmt)}"><title>Nordic Late</title>'
                 '</programme></tv>').encode())


def pick_every_source():
    w.cat_list.setCurrentRow(cat_rows().index(es.CATEGORY))


s = ProfileSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
reg = profiles.start(s, [], "Me")
kid = reg.add("Lisa", colour="pink", kids=True)
s.setValue("autoplay_preview", "false")
store = PlaylistStore(s)
store.add({"id": "a", "name": "Pirat", "kind": "xtream", "server": "http://pirat.invalid",
           "username": "u", "password": "p"})
store.set_active("a")
# Nordic's own rules: a hidden category, a locked one, a channel hidden by
# hand and one renamed - as its own lists have them.
nordic_cats = CategoryOverrides(s, "category_overrides_b")
nordic_cats.update("live", "h", hidden=True)
nordic_cats.update("live", "l", locked=True)
nordic_chans = ChannelOverrides(s, "channel_overrides_b")
nordic_chans.update("live", "12", hidden=True)
nordic_chans.update("live", "14", name="Nordic Renamed")
theme.apply_theme(s)
app.setStyleSheet(theme.build_style())
w = mw.MainWindow(Pirat(), s, store)
w.cover.resolver = None
nordic = Nordic()
w._sa_sources = ss.Sources(make_client=lambda pl: nordic)
played = []
w.player.play = lambda url, *a, **k: played.append(url) or True
w._choice_dialog = lambda *a, **k: 0
w.resize(1400, 900)
w.show()
pump(30)
w.switch_mode("live")
settle(lambda: w.list_model.rowCount() > 0 and len(cat_rows()) > 2, what="TV's categories")

# -- one source: not offered -------------------------------------------------------------
assert es.CATEGORY not in cat_rows(), cat_rows()

# -- a second one: offered, right under All ----------------------------------------------
store.add({"id": "b", "name": "Nordic", "kind": "xtream", "server": "http://nordic.invalid",
           "username": "u", "password": "p"})
write_nordic_guide()
w._update_playlist_btn()
settle(lambda: es.CATEGORY in cat_rows(), what="the entry offered")
assert cat_rows().index(es.CATEGORY) == 1 and cat_rows()[0] is None
assert w.cat_list.item(1).text().endswith(tr("cat_every_source"))

# -- picked: every source's channels, as each source's lists show them --------------------
pick_every_source()
settle(lambda: any(r.get("_xs") for r in w.list_model.items()), what="Nordic's channels")
assert shown() == [tr("status_list_source", source="Pirat", text=tr("count_channels", n=3)),
                   "Vegas Sport", "Pirat Two", "Late",
                   tr("status_list_source", source="Nordic", text=tr("count_channels", n=4)),
                   "Vegas Nordic", "Nordic Nine", "Adult row", "Nordic Renamed"], shown()
assert Nordic.lists == ["live"], Nordic.lists              # never its films or series
assert Nordic.asked == []                                  # no address asked for
assert w.list_model.uniform and w.list_model.headed
assert w.list_model.flags(w.list_model.index(0)) == Qt.ItemFlag.NoItemFlags
others = [r for r in w.list_model.items() if r.get("_xs")]
assert others and not any("stream_id" in r for r in others)
assert w.listw.visualRect(w.list_model.index(0)).height() \
    == w.listw.visualRect(w.list_model.index(1)).height()   # a heading as tall as a row
top = w._place_tv_top
settle(lambda: top.isVisible(), what="the TV place's header")
assert top.title_text() == tr("cat_every_source"), top.title_text()
assert top.header.count.text() == tr("count_channels", n=7), top.header.count.text()
assert w._list_readout[1] == tr("count_channels", n=7)       # no one source named
assert not w._list_shown.startswith("Pirat")
assert store.active_id == "a"

# -- what is on: Nordic's own guide from disk, never downloaded --------------------------
settle(lambda: (w._xs_tables or {}).get("b") is not None, what="Nordic's guide around now")
vegas = w.list_model.items()[row_of("Vegas Nordic")]
nn, _at = w.delegate._now_next(vegas)
assert nn is not None and nn.title == "Nordic Show", nn
assert epgmod.epg_cache_path("b") not in downloads, downloads
pick("Vegas Nordic")
pump()
assert w.now_title.text() == "Nordic Show", w.now_title.text()
# Its tooltip says where it is from.
from PyQt6.QtCore import QEvent
from PyQt6.QtGui import QHelpEvent
from PyQt6.QtWidgets import QStyleOptionViewItem, QToolTip
idx = w.list_model.index(row_of("Vegas Nordic"))
at = w.listw.visualRect(idx).center()
w.delegate.helpEvent(QHelpEvent(QEvent.Type.ToolTip, at, w.listw.viewport().mapToGlobal(at)),
                     w.listw, QStyleOptionViewItem(), idx)
assert QToolTip.text() == "Vegas Nordic\n" + tr("sa_from_source", source="Nordic"), QToolTip.text()
QToolTip.hideText()

# -- the filter, across every source -------------------------------------------------------
w.search.setText("vegas")
settle(lambda: len(shown()) == 4, what="the filter")
assert shown() == [tr("status_list_source", source="Pirat", text=tr("count_channels", n=1)),
                   "Vegas Sport",
                   tr("status_list_source", source="Nordic", text=tr("count_channels", n=1)),
                   "Vegas Nordic"], shown()
w.search.setText("")
settle(lambda: len(shown()) == 9, what="the filter emptied")

# -- a grid of tiles: no headings ----------------------------------------------------------
s.setValue("view_grid", "true")
w._apply_list_layout()
w._apply_filter()
assert not any(r.get("_header") for r in w.list_model.items()) and len(shown()) == 7
s.setValue("view_grid", "false")
w._apply_list_layout()
w._apply_filter()
assert len(shown()) == 9

# -- the keys: the arrows step over a heading, Enter plays ----------------------------------
from PyQt6.QtTest import QTest
w.listw.setFocus()
pick("Late")
QTest.keyClick(w.listw, Qt.Key.Key_Down)
assert w.listw.currentIndex().row() == row_of("Vegas Nordic"), w.listw.currentIndex().row()
QTest.keyClick(w.listw, Qt.Key.Key_Up)
assert w.listw.currentIndex().row() == row_of("Late")

# -- the source in use's row plays as its own -----------------------------------------------
pick("Vegas Sport")
QTest.keyClick(w.listw, Qt.Key.Key_Return)
settle(lambda: played and played[-1] == "http://pirat/live/1.ts", what="Enter plays")
pick("Vegas Sport")
w.play()
settle(lambda: played and played[-1] == "http://pirat/live/1.ts", what="Pirat's channel")
assert w._xs_showing()

# -- another source's: from it, without switching - the list stays ------------------------
pick("Vegas Nordic")
w.play()
settle(lambda: played[-1] == "http://nordic/live/8.ts", what="Nordic's channel")
assert store.active_id == "a" and Nordic.asked == [8]
assert w._last_playback["key"] == es.play_key("b", 8) == "failover:b:8"
assert w._playing_elsewhere() == "Nordic"
assert w._status_playing("Vegas Nordic") == tr("status_playing_from", title="Vegas Nordic",
                                                source="Nordic")
pump(30)
time.sleep(0.2)
pump(10)
assert w._xs_showing() and w.mode == "live"                # the pane did not move
assert w.delegate._is_playing(w.list_model.items()[row_of("Vegas Nordic")], "live")
assert not w.delegate._is_playing(w.list_model.items()[row_of("Vegas Sport")], "live")
assert w.history.entries[0]["_key"] == 1                    # History is the source in use's

# -- channel up and down go through every source's rows -----------------------------------
w._zap(1)
settle(lambda: played[-1] == "http://nordic/live/9.ts", what="channel up")
w._zap(-1)
settle(lambda: played[-1] == "http://nordic/live/8.ts", what="channel down")
w._zap(-1)                                                  # past Nordic's heading
settle(lambda: played[-1] == "http://pirat/live/3.ts", what="back into Pirat's")
assert store.active_id == "a"

# -- another source's row's menu: its own ---------------------------------------------------
menus = []
_exec = QMenu.exec
QMenu.exec = lambda self, *a: menus.append([x.text() for x in self.actions() if x.text()])
pick("Nordic Nine")
w._context_menu(w.listw.visualRect(w.list_model.index(row_of("Nordic Nine"))).center())
assert menus[-1] == [tr("btn_play"), tr("ctx_add_to_favorites"),
                     tr("sa_from_source", source="Nordic")], menus
nine = w.list_model.items()[row_of("Nordic Nine")]
w._xs_toggle_favorite(nine)
assert FavoriteStore(w._own(s), "favorites_b").is_favorite(9)
assert not w.favs.is_favorite(9) and w.is_favorite_item(nine, "live")
w._context_menu(w.listw.visualRect(w.list_model.index(row_of("Nordic Nine"))).center())
assert menus[-1][1] == tr("ctx_remove_from_favorites")
w._context_menu(w.listw.visualRect(w.list_model.index(0)).center())   # a heading: none
assert len(menus) == 2
QMenu.exec = _exec

# -- the Guide over it is the source in use's ----------------------------------------------
assert [c.get("stream_id") for c in w._guide_scope(False)[2]] == [1, 2, 3]

# -- the lock opened: Nordic's locked category comes in ------------------------------------
w.parental.session_unlocked = True
w._load_categories()
settle(lambda: "Locked row" in shown(), what="the lock opened")
w.parental.session_unlocked = False
w._load_categories()
settle(lambda: shown() and "Locked row" not in shown() and "Vegas Nordic" in shown(),
       what="the lock shut")

# -- a kids profile: nothing for adults, and another source's plays only as listed --------
assert w.switch_profile(kid.id)
settle(lambda: es.CATEGORY in cat_rows(), what="the kids profile's categories")
pick_every_source()
settle(lambda: "Vegas Nordic" in shown(), what="the kids profile's every source")
assert "Adult row" not in shown() and "Late" not in shown(), shown()
pick("Vegas Nordic")
before = len(played)
w.play()
settle(lambda: len(played) > before, what="a kids profile plays another source's")
assert played[-1] == "http://nordic/live/8.ts"
w._failover_key = es.play_key("b", 13)
w._failover_source, w._failover_row = store.get("b"), {"stream_id": 13, "name": "Adult row"}
assert w._kids_may_play("live", None, es.play_key("b", 13)) is False
w._failover_row = {"stream_id": 9, "name": "Nordic Nine"}
assert w._kids_may_play("live", None, es.play_key("b", 13)) is True
assert w._kids_may_play("live", None, "failover:b:99") is False      # not what plays
assert w.switch_profile(MAIN_ID)

# -- the second source gone: not offered any more - the list it showed gives way to All ----
w.switch_mode("live")
settle(lambda: es.CATEGORY in cat_rows(), what="back as a grown-up")
pick_every_source()
settle(lambda: "Vegas Nordic" in shown(), what="every source again")
store.remove("b")
w._update_playlist_btn()
settle(lambda: es.CATEGORY not in cat_rows() and not w._xs_showing()
       and w.list_model.rowCount() == 3, what="the entry gone")
assert w.cat_list.currentRow() == 0 and not any(r.get("_xs") for r in w.list_model.items())
# Offered again with a second source, in place - the list shown stays as it is.
rows_before = list(w.list_model.items())
store.add({"id": "c", "name": "Third", "kind": "m3u", "server": "http://third.invalid/l.m3u"})
w._update_playlist_btn()
assert cat_rows()[1] == es.CATEGORY and w.cat_list.currentRow() == 0
assert w.list_model.items() == rows_before
print("EVERY SOURCE OK", flush=True)
os._exit(0)
"""


def _child_env(tmp_path) -> dict:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=_REPO_ROOT)
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
                "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                "XDG_CACHE_HOME"):
        env[var] = str(tmp_path / var.lower())
    return env


def test_every_source_in_the_window(tmp_path):
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT,
                         env=_child_env(tmp_path), capture_output=True, text=True,
                         timeout=300)
    assert "EVERY SOURCE OK" in out.stdout, out.stdout[-3000:] + out.stderr[-6000:]
