"""The sidebar's navigation: places on top (Home, TV, Guide, Movies, Series,
Recordings, Local files), MINE (Favourites, Watch later, History) under
them, Settings and Multiview named at the foot. The Guide opens its window
and is never the current entry; Watched is History's second filter; the
entries carry a quiet status (a recording running, new episodes, what waits
under Watch later); the selected entry is a light wash with a leading bar;
the rail keeps its groups apart with hairlines."""
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

# -- the order: places, MINE, the foot ----------------------------------------
places = ["home", "live", "guide", "vod", "series", "rec", "local"]
mine = ["fav", "watchlist", "history"]
assert list(w.nav_btns) == places + mine, list(w.nav_btns)
assert "watched" not in w.nav_btns
content = w._side_scroll.widget()
def y(widget):
    return widget.mapTo(content, widget.rect().topLeft()).y()
ys = [y(w.nav_btns[k]) for k in places] + [y(w._lib_header)] + [y(w.nav_btns[k]) for k in mine]
assert ys == sorted(ys) and len(set(ys)) == len(ys), ys
assert all(w.nav_btns[k].property("primary") == "true" for k in places)
assert all(w.nav_btns[k].property("primary") == "false" for k in mine)
assert all(w.nav_btns[k].parentWidget() is w._library_box for k in mine)
assert w._lib_section_label.text() == tr("sidebar_mine") == "MITT"
assert w.nav_btns["guide"].text() == tr("nav_guide")
# Settings and Multiview at the foot, under its hairline, with their names.
assert y(w.cat_list) < y(w._bottom_rule) < y(w._settings_btn) < y(w._multiview_btn)
assert w._settings_btn.text() == tr("btn_settings")
assert w._multiview_btn.text() == tr("menu_multiview")
assert not w._settings_btn.icon().isNull() and not w._multiview_btn.icon().isNull()
assert all(not b.icon().isNull() for b in w.nav_btns.values())

# -- the guide: a window, never the current entry ------------------------------
w.switch_mode("live")
pump()
opened = []
import dopeiptv.ui.mw_settings as ms
class FakeGuide:
    def __init__(self, parent, channels, title=""):
        opened.append((parent, len(channels), title))
    def exec(self):
        return 0
ms.EpgGridDialog = FakeGuide
w.all_items = [{"name": "Kanal", "stream_id": 1}]
guide = w.nav_btns["guide"]
assert not guide.isCheckable()
guide.click()
pump()
assert opened and opened[-1][0] is w and opened[-1][1] == 1, opened
assert w.mode == "live" and w.nav_btns["live"].isChecked() and not guide.isChecked()
# Its shortcut and the TV entry's menu still open it, the same way.
n = len(opened)
w._shortcut_epg_guide()
assert len(opened) == n + 1
assert w.mode == "live"

# -- Watched: History's second filter ------------------------------------------
w.switch_mode("watched")
pump()
assert w.mode == "watched"
assert w.nav_btns["history"].isChecked() and not w.nav_btns["live"].isChecked()
chips = w._history_chip_btns
assert not w._history_chips.isHidden()
assert chips["watched"].isChecked() and not chips["all"].isChecked()
assert chips["all"].text() == tr("history_chip_all") and chips["watched"].text() == tr("history_chip_watched")
chips["all"].click()
pump()
assert w.mode == "history" and chips["all"].isChecked() and w.nav_btns["history"].isChecked()
chips["watched"].click()
pump()
assert w.mode == "watched"
# Remembered while the app runs: History comes back to Watched.
w.switch_mode("live")
pump()
assert w._history_chips.isHidden()
w.nav_btns["history"].click()
pump()
assert w.mode == "watched" and chips["watched"].isChecked()
chips["all"].click()
w.switch_mode("vod")
w.nav_btns["history"].click()
pump()
assert w.mode == "history"
# Home and back lights History again for Watched.
w.switch_mode("watched")
w._show_home_page()
assert w.nav_btns["home"].isChecked() and not w.nav_btns["history"].isChecked()
w._leave_home()
assert w.nav_btns["history"].isChecked() and w.mode == "watched"
w.switch_mode("live")
pump()

# -- status: recording, new episodes, watch later ------------------------------
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

wl = w.nav_btns["watchlist"]
assert wl.badge() == (None, 0)
film = {"name": "Film", "stream_id": 5, "_tmdb_id": 42}
w._add_watchlist(film, "vod", False)
assert wl.badge() == ("count", 1), wl.badge()
w._remove_watchlist(film, "vod", False)
assert wl.badge() == (None, 0), wl.badge()

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
assert w._settings_btn.text() == "" and w._multiview_btn.text() == ""
assert w._settings_btn.toolTip() == tr("btn_settings")
assert w._lib_header.isHidden() and not w._rail_rule_mine.isHidden()
assert w._rail_rule_mine.isVisible() and w._bottom_rule.isVisible() and w._bottom_rule.is_rail()
assert y(w.nav_btns["local"]) < y(w._rail_rule_mine) < y(w.nav_btns["fav"])
assert y(w.nav_btns["history"]) < y(w._bottom_rule) < y(w._settings_btn)
assert rec.badge() == ("dot", 1)
w.rec.finish_inplayer(job["id"], "stopped")
w._set_sidebar_collapsed(False)
pump(20)
assert w._rail_rule_mine.isHidden() and not w._lib_header.isHidden()
assert not w._bottom_rule.is_rail()
assert w.nav_btns["guide"].text() == tr("nav_guide")
assert w._settings_btn.text() == tr("btn_settings")

# -- a language switch renames all of it ---------------------------------------
w._set_language("en")
pump()
assert w.nav_btns["guide"].text() == "Guide" and w._lib_section_label.text() == "MINE"
assert w.nav_btns["home"].text() == "Home"
assert w._settings_btn.text() == "Settings" and chips["watched"].text() == "Watched"
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
