"""What the places show, in the real window (ui/mw_place_look.py): with a
source of 45 categories, the places folded into their compact block keep
the entry lit and give the categories the room, a tile goes to its place,
the icon rail stays as it was, unfolding brings the labels back; entries
taken out of the sidebar are gone (the hairline with the last lower one)
and their places still open; Recently watched, Continue and Popular right
now hidden from their heading - Popular right now in its own key - with a
note saying where they come back, and brought back from Settings >
Appearance, where a change shows at once; Just the list hides the rows,
makes the headers compact and opens Movies and Series as rows and TV
compact without storing over the person's own choices, a pick on a
place's switch holding for the session, and off again everything is as it
was; another profile shows its own."""
from __future__ import annotations

import os
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QDialog

tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
app = QApplication.instance() or QApplication([])

from dopeiptv.core import places, profiles
from dopeiptv.core.profiles import MAIN_ID, ProfileSettings
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw_mod, theme
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui.settings_ui import Chip, SettingsShell, ToggleSwitch

s = ProfileSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
s.setValue("autoplay_preview", "false")
profiles.start(s, [], "Me")
theme.apply_theme(s)
app.setStyleSheet(theme.build_style())
mw_mod.launch_player = lambda *a, **k: None                   # never a real mpv
w = MainWindow(DemoClient(), s)
w.cover.resolver = None
w._choice_dialog = lambda *a, **k: 0
if w.player is not None:
    w.player.play = lambda url, title, start=0.0, **k: True


def settle(cond, secs=10, what=""):
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


c = w.client
cats = [{"category_id": str(i), "category_name": f"Country {i:02d}"} for i in range(1, 46)]
chans = [{"stream_id": n, "num": n, "name": f"Channel {n}", "stream_icon": "",
          "category_id": str((n - 1) // 4 + 1), "epg_channel_id": ""} for n in range(1, 181)]
c.live_categories = lambda: cats
c.live_streams = lambda category_id=None: [dict(x) for x in chans
                                          if category_id in (None, "", x["category_id"])]
c.live_url = lambda sid, fmt="ts": f"http://127.0.0.1:9/live/{sid}.ts"
films = [{"stream_id": 500 + i, "name": f"Film {i:02d}", "category_id": "m1",
          "container_extension": "mkv"} for i in range(12)]
c.vod_categories = lambda: [{"category_id": "m1", "category_name": "Drama"}]
c.vod_streams = lambda cat=None: [f for f in films if cat in (None, f["category_id"])]
shows = [{"series_id": 700 + i, "name": f"Show {i:02d}", "category_id": "s1"} for i in range(8)]
c.series_categories = lambda: [{"category_id": "s1", "category_name": "Crime"}]
c.series_list = lambda cat=None: [x for x in shows if cat in (None, x["category_id"])]
for sid in (6, 5, 2):
    w.history.add(f"http://127.0.0.1:9/live/{sid}.ts", f"Channel {sid}", "", sid, "live",
                  extra={"stream_id": sid, "category_id": "1"})
w.resume.record("vod", 503, 1500, 6000, item=films[3])

w.resize(1400, 900)
w.show()
settle(lambda: w.list_model.rowCount() == 4, what="the first category")
assert w._show_place("tv") is True
top = w._place_tv_top
settle(lambda: top.isVisible() and top.recent.isVisible(), what="Recently watched")
btns, lay, fold = w.nav_btns, w._places_lay, w._places_fold_btn


def rows_of_entries():
    return len({b.geometry().y() for b in btns.values() if not b.isHidden()})


# 1. Folded: the same entries as icons in even rows (ten and the fold: three),
#    the one lit still lit, the categories given the room; kept for the profile.
assert not lay.is_folded() and fold.isVisible() and fold.toolTip() == tr("places_fold")
assert rows_of_entries() == 10 and w._places_rule.isVisible()
h0, box0 = w.cat_list.height(), w._places_box.height()
fold.click()
settle(lambda: lay.is_folded() and w.cat_list.height() > h0 + 150, what="folded")
assert s.value(places.FOLDED_KEY) == "true"
assert all(b.text() == "" and b.property("tile") and b.property("rail") for b in btns.values())
assert all(b.toolTip() and b.accessibleName() for b in btns.values())
assert btns["live"].isChecked() and [k for k, b in btns.items() if b.isChecked()] == ["live"]
assert rows_of_entries() == lay.grid(w._places_box.width(), len(btns) + 1)[1] == 3
assert not w._places_rule.isVisible()
assert fold.isVisible() and fold.is_folded() and fold.toolTip() == tr("places_unfold")
# Its status on its icon: a recording's red dot, its words in the tooltip.
w.rec.stop_inplayer_cb = lambda: None
job = w.rec.add_inplayer_job("News", "", None)
settle(lambda: btns["rec"].badge() == ("dot", 1), what="the recording's dot")
assert tr("nav_status_recording", n=1) in btns["rec"].toolTip()
img = btns["rec"].grab().toImage()
want = QColor(theme.P["rec"])
assert any(abs(img.pixelColor(x, y).red() - want.red()) + abs(img.pixelColor(x, y).green() - want.green())
           + abs(img.pixelColor(x, y).blue() - want.blue()) < 40
           for x in range(img.width()) for y in range(img.height() // 2)), "no red dot"
w.rec.finish_inplayer(job["id"], "stopped")
settle(lambda: btns["rec"].badge() == (None, 0), what="the dot gone")
# A tile goes to its place, and is the one lit.
btns["vod"].click()
settle(lambda: w._place == "films" and btns["vod"].isChecked(), what="a tile's place")
# The icon rail is as ever: a column, no fold control; back to the block after.
w._set_sidebar_collapsed(True)
idle(0.3)
assert not lay.is_folded() and not fold.isVisible() and w._places_rule.isVisible()
assert all(b.text() == "" and not b.property("tile") for b in btns.values())
assert rows_of_entries() == 10
w._set_sidebar_collapsed(False)
idle(0.3)
assert lay.is_folded() and all(b.text() == "" for b in btns.values())
folded_h = w.cat_list.height()
# In full again: the labels, the same entry lit, the block as tall as it was.
fold.click()
settle(lambda: not lay.is_folded() and btns["vod"].text() == tr("nav_movies"), what="in full")
settle(lambda: w._places_box.height() == box0, what="the block's height")
assert btns["vod"].isChecked() and w.cat_list.height() < folded_h - 150
assert s.value(places.FOLDED_KEY) == "false"
w._show_place("tv")
settle(lambda: top.recent.isVisible(), what="TV again")

# 2. Entries taken out: gone, the hairline with the last lower one; their
#    places still open; the app does not open on one.
for p in ("guide", "local", "radio", "rec", "dl"):
    places.set_hidden(s, p, True)
w._places_look_changed("test")
assert all(btns[k].isHidden() for k in ("guide", "local", "radio", "rec", "dl"))
assert not any(btns[k].isHidden() for k in ("mitt", "live", "vod", "series", "search"))
assert not w._places_rule.isVisible()
w._shortcut_epg_guide()
settle(lambda: w._guide_page_showing(), what="the guide by its shortcut")
assert w._show_place("rec") and w._place == "rec"            # the menu's way in
s.setValue(places.LAST_KEY, "rec")
assert places.start_places(s) == ("tv", "")
w._show_place("tv")
places.set_hidden(s, "radio", False)
w._places_look_changed("test")
assert not btns["radio"].isHidden() and w._places_rule.isVisible()
s.setValue(places.HIDDEN_KEY, "")
w._places_look_changed("test")
assert not any(b.isHidden() for b in btns.values())
settle(lambda: top.recent.isVisible(), what="Recently watched still")

# 3. Hide a row where you see it: at once, kept, a note says where it comes back.
top.recent.heading_menu().actions()[0].trigger()
settle(lambda: not top.recent.isVisible(), what="Recently watched hidden")
assert s.value("place_row_tv_recent") == "false"
note = w.notices.note_text()
assert tr("tv_recent") in note and tr("set_page_appearance") in note, note
w._place_tv_tick()                                             # it stays hidden
assert not top.recent.isVisible()
assert w._show_place("films") is True
cont = w._pf_continue_row
settle(lambda: cont.isVisible() and [x.key for x in cont.cards()] == ["503"], what="Continue")
cont.heading_menu().actions()[0].trigger()
settle(lambda: not cont.isVisible(), what="Continue hidden")
assert s.value("place_row_films_continue") == "false"
# Popular right now: its own key on Posters & info, never a second one.
w._place_row_hide("films_popular")
assert s.value(places.POPULAR_KEYS["vod"]) == "false" and not w.popular_on("vod")
assert tr("set_page_posters") in w.notices.note_text()
assert not any("films_popular" in k for k in s.allKeys())
places.set_row(s, "films_popular", True)

# 4. Brought back from Settings > Appearance, shown at once; Just the list on.
seen = {}


def visit(self):
    self.show()
    shell = self.findChild(SettingsShell)
    shell.show_page("appearance")
    idle(0.3)
    chips = {ch.text(): ch for ch in self.findChildren(Chip)}
    for words in (f"{tr('nav_tv')} › {tr('tv_recent')}",
                  f"{tr('nav_movies')} › {tr('card_continue')}"):
        assert not chips[words].isChecked(), words
        chips[words].click()
    idle(0.6)                                                  # applied as made
    seen["cont"] = cont.isVisible()
    seen["entries"] = [ch.text() for ch in self.findChildren(Chip)
                       if ch.text() in (tr("nav_guide"), tr("nav_recordings"))]
    jl = next(x for x in self.findChildren(ToggleSwitch) if x.accessibleName() == tr("set_just_list"))
    jl.setChecked(True)
    idle(0.6)
    seen["compact"] = w._films_place.is_compact() and top.is_compact()
    seen["layout"] = w._pf_view.layout_kind
    self.hide()
    return 0


QDialog.exec = visit
s.setValue("tv_density", "airy")                               # the person's own
w.open_settings()
assert seen["cont"] and len(seen["entries"]) == 2, seen
assert seen["compact"] and seen["layout"] == "list", seen
assert s.value("place_row_tv_recent") == "true" and s.value("place_row_films_continue") == "true"
assert s.value(places.JUST_LIST_KEY) == "true"

# 5. Just the list: no rows, compact headers, rows and compact - nothing
#    stored over the person's own choices.
assert not cont.isVisible() and s.value("films_view") is None
w._films_place.header.switch.buttons[0].click()                # Grid, for now
settle(lambda: w._pf_view.layout_kind == "poster", what="grid picked")
assert s.value("films_view") is None
assert w._show_place("series") is True
settle(lambda: w._place_series is not None and w._place_series_active(), what="Series")
assert w._place_series.grid.layout_kind == "list" and w._place_series.is_compact()
assert w._place_series.cont.isHidden()
assert w._show_place("tv") is True
settle(lambda: top.isVisible(), what="TV")
assert not top.recent.isVisible() and top.is_compact() and w.delegate.tv_density == "compact"
assert s.value("tv_density") == "airy"
settle(lambda: w.list_model.rowCount() == 4
       and w.listw.visualRect(w.list_model.index(1)).height() == 56, what="compact rows")
# Off again: everything as it was.
s.setValue(places.JUST_LIST_KEY, "false")
w._places_look_changed("test")
settle(lambda: top.recent.isVisible(), what="Recently watched back")
assert not top.is_compact() and w.delegate.tv_density == "airy"
settle(lambda: w.listw.visualRect(w.list_model.index(1)).height() == 84, what="airy rows")
assert w._show_place("films") is True
settle(lambda: cont.isVisible(), what="Continue back")
assert w._pf_view.layout_kind == "poster" and not w._films_place.is_compact()
assert s.value("place_row_tv_recent") == "true" and s.value("films_view") is None

# 6. Another profile shows its own; back again, this one's.
s.setValue(places.FOLDED_KEY, "true")
places.set_hidden(s, "radio", True)
w._places_look_changed("test")
assert lay.is_folded() and btns["radio"].isHidden()
kid = w.profiles.add("Kid", colour="pink")
w._profiles_changed()
assert w.switch_profile(kid.id)
settle(lambda: not lay.is_folded(), what="the kid's sidebar")
assert not btns["radio"].isHidden() and btns["radio"].text() == tr("nav_radio")
s.setValue(places.JUST_LIST_KEY, "true")                        # the kid's own
w._places_look_changed("test")
assert w.switch_profile(MAIN_ID)
settle(lambda: lay.is_folded(), what="mine again")
assert btns["radio"].isHidden() and not places.just_list(s)
print("PLACE LOOK OK", flush=True)
os._exit(0)
"""


def test_what_the_places_show_in_the_window(tmp_path):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        env[var] = str(tmp_path / var.lower())
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=240)
    assert "PLACE LOOK OK" in out.stdout, out.stdout[-2000:] + out.stderr[-4000:]
