"""The sidebar's navigation - the app's places (core/places.py,
ui/mw_places.py): Mine, TV, Movies, Series, Guide, Search, a hairline, then
Local files, Radio, Recordings; no Home. The source's avatar and Settings
at the foot (tests/test_sidebar_foot.py has the foot itself). Mine holds
Favorites, Watch later and History as tabs in the place's header; the Guide
is a place too, its page in the middle pane; Watched is History's second
filter; the entries carry a quiet status (a recording running, new episodes,
what waits under Watch later - on Mine); the selected entry is a light wash
with a leading bar; the rail keeps its groups apart with a hairline."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytest.importorskip("PyQt6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QColor  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from dopeiptv.ui.nav_widgets import NavButton, SideRule  # noqa: E402
from dopeiptv.ui.theme import P  # noqa: E402

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _near(c: QColor, hex_: str, tol: int = 40) -> bool:
    want = QColor(hex_)
    return (abs(c.red() - want.red()) + abs(c.green() - want.green())
            + abs(c.blue() - want.blue())) <= tol


def _has(img, hex_: str, xs, ys) -> bool:
    return any(_near(img.pixelColor(x, y), hex_) for x in xs for y in ys)


def _button(app, rtl: bool = False) -> NavButton:
    b = NavButton("rec", "Recordings")
    b.setStyleSheet(f"QPushButton{{background:{P['side']};border:none;}}"
                    f"QPushButton:checked{{background:{P['nav_tint']};}}")
    b.resize(200, 32)
    if rtl:
        b.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    return b


def test_the_selected_entry_has_its_bar_at_the_leading_edge(app):
    for rtl in (False, True):
        b = _button(app, rtl)
        b.setChecked(True)
        img = b.grab().toImage()
        lead = range(b.width() - 3, b.width()) if rtl else range(0, 3)
        trail = range(0, 3) if rtl else range(b.width() - 3, b.width())
        mid = range(14, 18)
        assert _has(img, P["nav_ink"], lead, mid), rtl
        assert not _has(img, P["nav_ink"], trail, mid), rtl
        b.setChecked(False)
        img = b.grab().toImage()
        assert not _has(img, P["nav_ink"], lead, mid), rtl


def test_a_status_never_moves_the_label_and_sits_at_the_trailing_end(app):
    b = _button(app)
    before = b.sizeHint()
    b.set_badge("dot", 1)
    assert b.sizeHint() == before and b.badge() == ("dot", 1)
    img = b.grab().toImage()
    assert _has(img, P["rec"], range(b.width() - 24, b.width() - 8), range(12, 20))
    assert not _has(img, P["rec"], range(0, 100), range(0, 32))
    # In a right-to-left language the trailing end is on the left.
    r = _button(app, rtl=True)
    r.set_badge("dot", 1)
    img = r.grab().toImage()
    assert _has(img, P["rec"], range(8, 24), range(12, 20))
    b.set_badge(None)
    assert b.badge() == (None, 0)
    assert not _has(b.grab().toImage(), P["rec"], range(0, 200), range(0, 32))


def test_on_the_rail_a_status_is_a_dot_in_the_icons_corner(app):
    from PyQt6.QtCore import QSize
    b = _button(app)
    b.setText("")
    b.setIconSize(QSize(22, 22))
    b.setProperty("rail", True)
    b.resize(36, 30)
    for kind, colour in (("dot", P["rec"]), ("pill", P["nav_ink"]),
                         ("count", P["muted"])):
        b.set_badge(kind, 3)
        img = b.grab().toImage()
        # Top right of the 22 px icon centred in the button.
        assert _has(img, colour, range(24, 32), range(2, 10)), kind
        assert not _has(img, colour, range(0, 12), range(18, 30)), kind


def test_a_rule_is_short_on_the_rail_and_long_in_the_sidebar(app):
    rule = SideRule()
    rule.resize(200, 13)
    img = rule.grab().toImage()
    assert _near(img.pixelColor(20, 6), P["border"], 6)
    rule.set_rail(True)
    rule.resize(36, 13)
    img = rule.grab().toImage()
    assert _near(img.pixelColor(18, 6), P["border"], 6)
    assert not _near(img.pixelColor(1, 6), P["border"], 0) or P["border"] == P["side"]


_CHILD = r"""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtWidgets import QApplication, QWidget
app = QApplication.instance() or QApplication([])

from dopeiptv import i18n
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw
from dopeiptv.ui import theme
from dopeiptv.ui.theme import P

i18n.set_language("sv")


class Client(DemoClient):
    def vod_categories(self):
        return [{"category_id": "m", "category_name": "Film"}]

    def series_categories(self):
        return [{"category_id": "s", "category_name": "Serie"}]


def pump(n=10):
    for _ in range(n):
        app.processEvents()
        w.pool.waitForDone(5)


s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
s.setValue("language", "sv")
theme.apply_theme(s)
app.setStyleSheet(theme.build_style())
w = mw.MainWindow(Client(), s, PlaylistStore(s))
w.resize(1400, 900)
w.show()
pump(30)
w._set_sidebar_collapsed(False)
pump(10)

# -- the order: the places, a hairline, the lower places, the foot ------------
upper = ["mitt", "live", "vod", "series", "guide", "search"]
lower = ["local", "radio", "rec"]
assert list(w.nav_btns) == upper + lower, list(w.nav_btns)
assert not ({"home", "fav", "watchlist", "history", "watched"} & set(w.nav_btns))
content = w._side      # the part that scrolls and the foot under it
def y(widget):
    return widget.mapTo(content, widget.rect().topLeft()).y()
ys = [y(w.nav_btns[k]) for k in upper] + [y(w._places_rule)] + [y(w.nav_btns[k]) for k in lower]
assert ys == sorted(ys) and len(set(ys)) == len(ys), ys
assert all(b.property("primary") == "true" for b in w.nav_btns.values())
assert w.nav_btns["mitt"].text() == tr("nav_mine") == "Mitt"
assert w.nav_btns["guide"].text() == tr("nav_guide")
assert not hasattr(w, "_lib_header") and not hasattr(w, "_home_page")
# Settings at the foot, under its hairline: an icon, its name in the tooltip.
# Multiview has left the sidebar (the menu bar's View group has it).
assert y(w.cat_list) < y(w._bottom_rule) < y(w._settings_btn)
assert w._settings_btn.text() == "" and w._settings_btn.toolTip() == tr("btn_settings")
assert not w._settings_btn.icon().isNull()
assert not hasattr(w, "_multiview_btn")
assert all(not b.icon().isNull() for b in w.nav_btns.values())

# -- the guide: a place, its page in the middle pane --------------------------
w.switch_mode("live")
pump()
assert w._place == "tv" and w.nav_btns["live"].isChecked()
w.all_items = [{"name": "Kanal", "stream_id": 1}]
guide = w.nav_btns["guide"]
assert guide.isCheckable()
guide.click()
pump()
assert w._guide_page_showing() and len(w._guide_grid.channels) == 1
assert guide.isChecked() and not w.nav_btns["live"].isChecked()
assert w.mode == "live" and w._place == "guide" and w._list_place == "tv"
# TV, the list under it, takes the page away; the list is as it was.
w.nav_btns["live"].click()
pump()
assert not w._guide_page_showing() and w._place == "tv"
assert w.nav_btns["live"].isChecked() and not guide.isChecked()
# Its shortcut opens it the same way; leaving it is back on TV.
w._shortcut_epg_guide()
assert w._guide_page_showing() and guide.isChecked()
w._leave_guide_page()
assert w.mode == "live" and w.nav_btns["live"].isChecked() and w._place == "tv"

# -- Mine: Favorites, Watch later and History as the header's tabs -----------
w.nav_btns["mitt"].click()
pump()
assert w.mode == "fav" and w._place == "mitt" and w.nav_btns["mitt"].isChecked()
head = w._place_header
assert head.title.text() == tr("nav_mine")
assert head.switch.keys() == ["fav", "watchlist", "history"] and head.view_key() == "fav"
assert [b.text() for b in head.switch.buttons] == [tr("nav_favorites"), tr("nav_watchlist"),
                                                   tr("nav_history")]
head.switch.buttons[1].click()
pump()
assert w.mode == "watchlist" and w.nav_btns["mitt"].isChecked() and w._place == "mitt"
head.switch.buttons[2].click()
pump()
assert w.mode == "history" and w._place == "mitt"
# Watched: History's second filter, still Mine's History tab.
chips = w._history_chip_btns
assert not w._history_chips.isHidden()
chips["watched"].click()
pump()
assert w.mode == "watched" and head.view_key() == "history" and w.nav_btns["mitt"].isChecked()
assert chips["all"].text() == tr("history_chip_all") and chips["watched"].text() == tr("history_chip_watched")
# Remembered while the app runs: Mine comes back to History's Watched.
w.nav_btns["vod"].click()
pump()
assert w.mode == "vod" and w._place == "films" and head.title.text() == tr("nav_movies")
assert head.switch.isHidden()
w.nav_btns["mitt"].click()
pump()
assert w.mode == "watched" and chips["watched"].isChecked()
chips["all"].click()
pump()
assert w.mode == "history"
# Every other place: its list, its entry lit, its name in the header.
for key, place, mode in (("live", "tv", "live"), ("vod", "films", "vod"),
                         ("series", "series", "series"), ("local", "local", "local"),
                         ("radio", "radio", "radio"), ("rec", "rec", "rec")):
    w.nav_btns[key].click()
    pump()
    assert w.mode == mode and w._place == place and w.nav_btns[key].isChecked(), key
    assert [k for k, b in w.nav_btns.items() if b.isChecked()] == [key]
w.switch_mode("live")
pump()

# -- status: recording, new episodes, watch later on Mine ----------------------
rec = w.nav_btns["rec"]
assert rec.badge() == (None, 0)
w.rec.stop_inplayer_cb = lambda: None
job = w.rec.add_inplayer_job("Nyheter", "", None)
pump()
assert rec.badge() == ("dot", 1), rec.badge()
assert tr("nav_status_recording", n=1) in rec.toolTip()
w.rec.finish_inplayer(job["id"], "stopped")
pump()
assert rec.badge() == (None, 0), rec.badge()
assert rec.toolTip() == tr("nav_recordings")

mine = w.nav_btns["mitt"]
assert mine.badge() == (None, 0)
film = {"name": "Film", "stream_id": 5, "_tmdb_id": 42}
w._add_watchlist(film, "vod", False)
assert mine.badge() == ("count", 1), mine.badge()
w._remove_watchlist(film, "vod", False)
assert mine.badge() == (None, 0), mine.badge()

ser = w.nav_btns["series"]
assert ser.badge() == (None, 0)
w.followed.series = {"7": {"snap": {"series_id": 7, "name": "Serie"}, "known": ["a"],
                           "primed": True, "checked": 0,
                           "new": [{"id": "b", "added": 1}, {"id": "c", "added": 2}]}}
w._refresh_nav_status()
assert ser.badge() == ("pill", 2), ser.badge()
w._follow_mark_seen("7")
assert ser.badge() == (None, 0), ser.badge()

# -- the selected entry's colours, the theme's or the entry's own -------------
live = w.nav_btns["live"]
assert live.active_colors() == (P["nav_tint"], P["nav_ink"])
s.setValue("nav_color_vod", "#E5B454")
w._apply_nav_color("vod")
fill, ink = w.nav_btns["vod"].active_colors()
assert (fill, ink) == theme.nav_active_colors("#E5B454", P["side"], P["text"])
assert theme.contrast(ink, fill) >= 4.5
w._reset_nav_color("vod")
assert w.nav_btns["vod"].active_colors() == (P["nav_tint"], P["nav_ink"])

# -- the rail: icons alone, the groups kept apart ------------------------------
job = w.rec.add_inplayer_job("Nyheter", "", None)
w._set_sidebar_collapsed(True)
pump(20)
assert all(b.text() == "" for b in w.nav_btns.values())
assert w._settings_btn.text() == ""
assert w._settings_btn.toolTip() == tr("btn_settings")
assert w._places_rule.isVisible() and w._places_rule.is_rail()
assert w._bottom_rule.isVisible() and w._bottom_rule.is_rail()
assert y(w.nav_btns["search"]) < y(w._places_rule) < y(w.nav_btns["local"])
assert y(w.nav_btns["rec"]) < y(w._bottom_rule) < y(w._settings_btn)
assert rec.badge() == ("dot", 1)
w.rec.finish_inplayer(job["id"], "stopped")
w._set_sidebar_collapsed(False)
pump(20)
assert w._places_rule.isVisible() and not w._places_rule.is_rail()
assert not w._bottom_rule.is_rail()
assert w.nav_btns["guide"].text() == tr("nav_guide")
assert w._settings_btn.toolTip() == tr("btn_settings")

# -- a language switch renames all of it ---------------------------------------
w._set_language("en")
pump()
assert w.nav_btns["guide"].text() == "Guide" and w.nav_btns["mitt"].text() == "Mine"
assert w._settings_btn.toolTip() == "Settings" and chips["watched"].text() == "Watched"
w.nav_btns["mitt"].click()
pump()
assert w._place_header.title.text() == "Mine"
assert [b.text() for b in w._place_header.switch.buttons][0] == "Favorites"
print("NAV_OK", flush=True)
os._exit(0)
"""


def _child_env(tmp_path) -> dict:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        env[var] = str(tmp_path / var.lower())
    return env


def test_the_window_nav_order_guide_filters_status_and_rail(tmp_path):
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT,
                         env=_child_env(tmp_path), capture_output=True,
                         text=True, timeout=180)
    assert "NAV_OK" in out.stdout, out.stdout + out.stderr[-3000:]
