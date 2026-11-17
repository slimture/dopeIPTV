"""The TV place in the real window: TV's entry (mw_places' _show_place,
its hook _place_tv_show) puts it in the middle pane - its own header in
the places' shared one's stead (the category, its count and order, the sort, Compact /
Airy kept), Recently watched, the channels as the place's rows - and
everything the list carries keeps working on it: zapping with the arrows,
numbers typed, channel up and down, a double click, the channel's menu and
favouriting from it; a card of Recently watched plays (as its row when the
list holds it, from the pane when it does not, the category left as it is)
and has a menu of its own; Recently watched is this source's, and keeps out
a channel played in private viewing, one hidden by hand, one of a locked
category and, for a kids profile, one for adults. Leaving TV hides the
header and gives the control strip its size and sort back (and the places'
header); Back from the Guide lands on it as it was; a grid of tiles keeps
the size and drops the switch."""
from __future__ import annotations

import os
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os, tempfile, time
from datetime import datetime, timedelta, timezone
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QPoint, QSettings, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMenu

_tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _tmp)

from dopeiptv.i18n import tr
from dopeiptv.providers import epg as epgmod
from dopeiptv.providers.client import OfflineClient
from dopeiptv.ui import main_window as mw_mod
from dopeiptv.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])
CATS = [("1", "Sweden"), ("2", "Sport"), ("3", "XXX Adult")]
streams = []
for cid, n in (("1", 12), ("2", 2), ("3", 1)):
    for _ in range(n):
        sid = len(streams) + 1
        streams.append({"stream_id": sid, "num": sid, "name": f"Channel {sid:02d}",
                        "stream_icon": "", "category_id": cid, "epg_channel_id": f"ch{sid}"})

def guide_xml():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    base = now - timedelta(hours=2)
    fmt = "%Y%m%d%H%M%S +0000"
    out = ['<?xml version="1.0" encoding="UTF-8"?><tv>']
    for s in streams:
        out.append(f'<channel id="{s["epg_channel_id"]}"><display-name>{s["name"]}'
                   '</display-name></channel>')
        t = base
        k = 0
        while t < now + timedelta(hours=4):
            out.append(f'<programme channel="{s["epg_channel_id"]}" start="{t.strftime(fmt)}" '
                       f'stop="{(t + timedelta(minutes=30)).strftime(fmt)}">'
                       f'<title>Show {s["stream_id"]}-{k}</title></programme>')
            t += timedelta(minutes=30)
            k += 1
    out.append("</tv>")
    return "".join(out).encode()

class Client(OfflineClient):
    def live_categories(self):
        return [{"category_id": c, "category_name": n} for c, n in CATS]
    def live_streams(self, category_id=None):
        return [dict(s) for s in streams if category_id in (None, "", s["category_id"])]
    def live_url(self, stream_id, fmt="ts"):
        return f"http://127.0.0.1:9/live/{stream_id}.ts"

xml = guide_xml()
epgmod.XmltvGuide.PARSE_ELSEWHERE = False
epgmod.XmltvGuide._download = lambda self: xml
settings = QSettings(os.path.join(_tmp, "s.ini"), QSettings.Format.IniFormat)
settings.setValue("home_enabled", "false")
settings.setValue("autoplay_preview", "false")
mw_mod.launch_player = lambda *a, **k: None                # never a real mpv window
w = MainWindow(Client(), settings)
w.cover.resolver = None
w._choice_dialog = lambda *a, **k: 0

def settle(cond, secs=12, what=""):
    end = time.time() + secs
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert cond(), "timed out: " + what

def idle(secs):
    end = time.time() + secs
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)

def cat_data():
    item = w.cat_list.currentItem()
    return None if item is None else item.data(Qt.ItemDataRole.UserRole)

def row_of(sid):
    for r in range(w.list_model.rowCount()):
        it = w.list_model.item_at(r)
        if it and it.get("stream_id") == sid:
            return r
    return -1

def playing():
    return str(w._playing_key) if w._playing_group == "live" else None

w.resize(1400, 900)
w.show()
settle(lambda: w.list_model.rowCount() == 12 and cat_data() == "1", what="Sweden's channels")
top = w._place_tv_top

# 1. The place: header, count and order, the rows; the strip's own size and
#    sort give way to the header's.
assert w._show_place("tv") is True and w._place == "tv"
settle(lambda: top.isVisible(), what="the header")
assert not w._place_header_holder.isVisible()      # the places' shared one gives way
assert top.title_text() == "Sweden", top.title_text()
# The count alone; the sort's button names the order the list is in.
assert top.header.count.text() == tr("count_channels", n=12), top.header.count.text()
assert top.header.sort_key() == "global" and top.header.sort_btn.text() == tr("sort_provider")
assert not top.recent.isVisible()                   # nothing watched yet
assert not w._sort_ctl.isVisible() and not w._size_ctl.isVisible()
assert w.delegate.tv_rows(w.list_model) and w.delegate.tv_density == "compact"
assert w.listw.visualRect(w.list_model.index(1)).height() == 56
w._ensure_xmltv_loaded()
settle(lambda: w.xmltv.is_loaded(), what="the guide")
nn, _at = w.delegate._now_next(w.list_model.item_at(0))
assert nn is not None and nn.title.startswith("Show 1-"), nn

# 2. Zapping on it: the arrows move, a double click plays, a number typed
#    plays its channel, channel up plays the next.
w.listw.setFocus()
w.listw.setCurrentIndex(w.list_model.index(0))
QTest.keyClick(w.listw, Qt.Key.Key_Down)
assert w.listw.currentIndex().row() == 1
rect = w.listw.visualRect(w.list_model.index(1))
QTest.mouseClick(w.listw.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
idle(0.5)                                           # a click picks; no preview (off)
assert playing() is None and w.listw.currentIndex().row() == 1
QTest.mouseDClick(w.listw.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
settle(lambda: playing() == "2", what="double click plays")
QTest.keyClicks(w.listw, "5")
QTest.keyClick(w.listw, Qt.Key.Key_Return)
settle(lambda: playing() == "5", what="a number typed")
assert w.listw.currentIndex().row() == row_of(5)
w._zap(1)
settle(lambda: playing() == "6", what="channel up")

# 3. Recently watched: newest first, the one playing marked.
settle(lambda: top.recent.isVisible() and top.recent.keys()[:3] == ["6", "5", "2"],
       what="recently watched")
assert top.recent.strip.playing_key == "6"
assert top.recent.cards()[0].line.startswith("Show 6-")

# 4. A card plays: as its row when the list holds it - on a double click
# (one click only picks while autoplay is off, as in the list).
strip = top.recent.strip
idx = strip.model_.index(top.recent.keys().index("2"), 0)
QTest.mouseClick(strip.viewport(), Qt.MouseButton.LeftButton, pos=strip.visualRect(idx).center())
idle(0.3)
assert playing() != "2"
QTest.mouseDClick(strip.viewport(), Qt.MouseButton.LeftButton, pos=strip.visualRect(idx).center())
settle(lambda: playing() == "2", what="a card played")
assert w.listw.currentIndex().row() == row_of(2)
settle(lambda: top.recent.strip.playing_key == "2", what="the card marked")

# 5. ...and from the pane when the list does not hold it: the category stays.
sport = next(i for i in range(w.cat_list.count())
             if w.cat_list.item(i).data(Qt.ItemDataRole.UserRole) == "2")
w.cat_list.setCurrentRow(sport)
settle(lambda: w.list_model.rowCount() == 2 and top.title_text() == "Sport", what="Sport")
assert tr("count_channels", n=2) in top.header.count.text()
assert top.recent.keys()[:3] == ["2", "6", "5"]
card = next(c for c in top.recent.cards() if c.key == "5")
top.activated.emit(card)
settle(lambda: playing() == "5", what="a card from another category")
idle(0.6)
assert cat_data() == "2" and w.mode == "live", cat_data()

# 6. Airy, kept; the current row stays.
w.cat_list.setCurrentRow(next(i for i in range(w.cat_list.count())
                              if w.cat_list.item(i).data(Qt.ItemDataRole.UserRole) == "1"))
settle(lambda: w.list_model.rowCount() == 12, what="back to Sweden")
w.listw.setCurrentIndex(w.list_model.index(7))
top.header.switch.buttons[1].click()
settle(lambda: w.listw.visualRect(w.list_model.index(1)).height() == 84, what="airy rows")
assert settings.value("tv_density") == "airy" and w.delegate.tv_density == "airy"
assert w.listw.currentIndex().row() == 7
w.delegate.set_tv_density("compact")             # another profile's, say; read again
w._place_tv_sync()
assert w.delegate.tv_density == "airy" and top.header.view_key() == "airy"

# 7. The header's sort: the category's own order, named on its button.
top.header._sort_picked("alpha_desc")
settle(lambda: w.list_model.item_at(0)["stream_id"] == 12, what="Z to A")
assert top.header.sort_btn.text() == tr("sort_za")
assert w.sort_box.currentData() == "alpha_desc"
top.header._sort_picked("global")
settle(lambda: w.list_model.item_at(0)["stream_id"] == 1, what="back to the default")

# 8. The channel's menu on a row, and favouriting from it.
want, seen = [], []
def fake_exec(menu, *a, **k):
    def walk(m):
        for act in m.actions():
            seen.append(act.text())
            if act.menu() is not None:
                walk(act.menu())
            elif want and act.text() == want[0]:
                want.pop(0)
                act.trigger()
                return True
        return False
    walk(menu)
    return None
QMenu.exec = fake_exec
want.append(tr("ctx_add_to_favorites"))
rect = w.listw.visualRect(w.list_model.index(row_of(3)))
w._context_menu(rect.center())
assert tr("ctx_play_in_mpv") in seen and tr("rec_record") in seen, seen
settle(lambda: w.favs.is_favorite(3), what="favourited from the menu")
assert w.is_favorite_item(w.list_model.item_at(row_of(3)), "live")
assert w.listw.currentIndex().row() == row_of(3)

# 9. A card's own menu: Remove takes it out of Recently watched (and History).
seen.clear()
want.append(tr("tv_recent_remove"))
card = next(c for c in top.recent.cards() if c.key == "6")
top.menu.emit(card, QPoint(0, 0))
assert tr("btn_play") in seen and tr("phone_page_show_in_list") in seen, seen
assert "6" not in top.recent.keys()
assert not any(str(e.get("stream_id")) == "6" for e in w.history.entries)

# 10. Private viewing: what plays in it never gets there.
w.set_private_viewing(True)
w.play_live_channel(streams[8])
settle(lambda: playing() == "9", what="a private play")
idle(0.3)
assert "9" not in top.recent.keys()
w.set_private_viewing(False)

# 11. One hidden by hand, one of a locked category: kept out.
w.channel_ov.update("live", 5, hidden=True)
w._place_tv_recent()
assert "5" not in top.recent.keys()
w.channel_ov.update("live", 5, hidden=False)
w._place_tv_recent()
assert "5" in top.recent.keys()
w.play_live_channel(streams[12])                     # Sport's first
settle(lambda: "13" in top.recent.keys(), what="Sport's channel watched")
w.overrides.update("live", "2", locked=True)
w.parental.session_unlocked = False
w._place_tv_recent()
assert "13" not in top.recent.keys()
w.overrides.update("live", "2", locked=False)
w._place_tv_recent()
assert "13" in top.recent.keys()

# 12. A kids profile: nothing for adults.
w.play_live_channel(dict(streams[14]))               # XXX Adult's
settle(lambda: "15" in top.recent.keys(), what="the adult channel watched")
w._profile_kids = lambda: True
def kids_out():
    w._place_tv_recent()
    return "15" not in top.recent.keys() and "13" in top.recent.keys()
settle(kids_out, what="kept from a kids profile")
del w._profile_kids
w._place_tv_recent()
assert "15" in top.recent.keys()

# 13. This source's only: another source's history is another list.
w._build_source_stores("other")
w._place_tv_recent()
assert not top.recent.isVisible() and top.recent.keys() == []
w._build_source_stores(None)
w._place_tv_recent()
assert top.recent.isVisible()

# 14. A grid of tiles keeps the size, drops the switch; rows again after.
w.grid_btn.setChecked(True)
settle(lambda: not w.delegate.tv_rows(w.list_model), what="a grid")
assert w._size_ctl.isVisible() and not w._sort_ctl.isVisible()
assert not top.header.switch.isVisible()
assert w.listw.viewportMargins().left() > 0         # the grid's balancing inset
w.grid_btn.setChecked(False)
settle(lambda: w.delegate.tv_rows(w.list_model), what="rows again")
assert top.header.switch.isVisible() and not w._size_ctl.isVisible()
# The inset goes with the grid: no empty strip left of the rows (it stayed
# until the app was started again).
assert w.listw.viewportMargins().left() == 0
assert w.listw.visualRect(w.list_model.index(0)).x() == 0
mid = w._list_page
def left_of(widget):
    return widget.mapTo(mid, widget.rect().topLeft()).x()
assert left_of(top.header.title) == left_of(w.search) == left_of(top.recent.strip)

# 15. Another place: the header goes, the strip's controls and the places'
#     shared header come back; TV again from its entry, and Back from the
#     Guide lands on it as it was.
w._show_place("films")
settle(lambda: w.mode == "vod" and not top.isVisible(), what="the header away")
# (Movies is a place of its own now: its header and controls are its own.)
assert not top.isVisible()
w._on_nav_clicked("live")
settle(lambda: w.mode == "live" and top.isVisible() and w.list_model.rowCount() > 0,
       what="TV again")
assert w._place == "tv" and w.nav_btns["live"].isChecked()
assert not w._place_header_holder.isVisible()
w._open_epg_guide()
settle(lambda: w._guide_page_showing(), what="the guide page")
w._leave_guide_page()
assert not w._guide_page_showing() and top.isVisible() and w._place == "tv"
w._open_epg_guide()
settle(lambda: w._guide_page_showing(), what="the guide page again")
# Another category picked while the guide shows: the guide stays and
# follows it - that category's channels; the same one again changes nothing.
def cat_item(cid):
    return next(w.cat_list.item(i) for i in range(w.cat_list.count())
                if w.cat_list.item(i).data(Qt.ItemDataRole.UserRole) == cid)
for cid, n in (("2", 2), ("1", 12)):
    item = cat_item(cid)
    w.cat_list.setCurrentItem(item)
    w.cat_list.itemClicked.emit(item)
    settle(lambda n=n: w._guide_page_showing() and w._guide_grid is not None
           and len(w._guide_grid.channels) == n, what="the guide follows " + cid)
    assert w._guide_page.grid is w._guide_grid and w._guide_grid.category_name == item.text()
grid = w._guide_grid
w.cat_list.itemClicked.emit(w.cat_list.currentItem())
app.processEvents()
assert w._guide_grid is grid and w._guide_page_showing()
assert w._show_place("tv") is True
assert not w._guide_page_showing() and top.isVisible()
# A category double-clicked in the list: the guide opens on it.
item = cat_item("2")
w.cat_list.setCurrentItem(item)
w.cat_list.itemDoubleClicked.emit(item)
settle(lambda: w._guide_page_showing() and w._guide_grid is not None
       and len(w._guide_grid.channels) == 2, what="the guide of a double-clicked category")
assert w._guide_grid.category_name == item.text()
# Another category double-clicked while the guide shows: the guide moves
# there and stays; the category it shows double-clicked again (picked
# before this double click): back to its list.
other = cat_item("1")
w.cat_list.setCurrentItem(other)
w.cat_list.itemDoubleClicked.emit(other)
settle(lambda: w._guide_page_showing() and w._guide_grid is not None
       and len(w._guide_grid.channels) == 12, what="the guide moves on a double click")
w._cat_changed_at -= 5                                # picked a while ago
w.cat_list.itemDoubleClicked.emit(other)
settle(lambda: not w._guide_page_showing() and top.isVisible(), what="a double click goes back")
assert w._place == "tv" and w.cat_list.currentItem() is other
w.cat_list.setCurrentItem(item)
w.cat_list.itemDoubleClicked.emit(item)
settle(lambda: w._guide_page_showing() and w._guide_grid is not None
       and len(w._guide_grid.channels) == 2, what="the guide again")
assert w._show_place("tv") is True
assert not w._guide_page_showing() and top.isVisible()
w._place_tv_refresh()                                # a profile, a source, sync
assert top.isVisible() and top.title_text()

# Enter on a row picked plays it, as a double click does (the category
# shown here holds one channel: stopped first, Enter starts it).
w.player.stop()
settle(lambda: playing() is None, what="stopped")
w.listw.setFocus()
w.listw.setCurrentIndex(w.list_model.index(0))
QTest.keyClick(w.listw, Qt.Key.Key_Return)
settle(lambda: playing() == str(w.list_model.item_at(0).get("stream_id")), what="Enter plays")
print("TV PLACE OK", flush=True)
os._exit(0)
"""


def test_tv_place_in_the_real_window(tmp_path):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=_REPO_ROOT,
               XDG_CONFIG_HOME=str(tmp_path), XDG_DATA_HOME=str(tmp_path / "data"),
               XDG_CACHE_HOME=str(tmp_path / "cache"))
    r = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0 and "TV PLACE OK" in r.stdout, (r.stdout[-3000:], r.stderr[-6000:])
