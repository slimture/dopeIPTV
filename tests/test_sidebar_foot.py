"""The sidebar's foot: the active source as an avatar - its initials on the
accent, the full name in the tooltip, the source switcher on a click - and
Settings beside it, centred under the hairline (one above the other on the
icon rail). Nothing under the logo any more, and no Multiview in the
sidebar: the menu bar's View group and the channel menu still open it."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytest.importorskip("PyQt6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dopeiptv.ui import theme  # noqa: E402
from dopeiptv.ui.nav_widgets import source_initials  # noqa: E402

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.parametrize("name, initials", [
    ("Min IPTV", "MI"),            # the first letters of the first two words
    ("dopeIPTV", "DO"),            # one word: its first two letters
    ("TV-Box (Home)", "TB"),       # words are runs of letters and digits
    ("box 2 extra", "B2"),
    ("über tv", "ÜT"),
    ("中国电视", "中国"),
    ("a", "A"),
    ("x_y", "XY"),
    ("  ", "?"),
    ("", "?"),
    ("--", "?"),
])
def test_a_sources_initials(name, initials):
    assert source_initials(name) == initials


def test_the_initials_read_on_every_accent():
    # Every accent a theme brings and every one the user can pick: the ink
    # chosen for the initials reaches 4.5:1, as the nav's selected entry.
    accents: set[str] = set()
    for t in theme.THEMES.values():
        own = t.get("accent")                    # (accent, hovered) or one
        accents |= set(own) if isinstance(own, (tuple, list)) else {own} - {None}
    accents |= {hexes[1] for hexes in theme.ACCENTS.values()}
    accents |= {hexes[2] for hexes in theme.ACCENTS.values()}   # hovered
    for accent in accents:
        ink = theme.ink_on(accent)
        assert theme.contrast(ink, accent) >= 4.5, (accent, ink)


_CHILD = r"""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings, Qt
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QMenu, QPushButton
app = QApplication.instance() or QApplication([])

from dopeiptv import i18n
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw
from dopeiptv.ui import theme
from dopeiptv.ui.theme import P

i18n.set_language("sv")
menus = []
QMenu.exec = lambda self, *a, **k: menus.append(self)


def pump(n=10):
    for _ in range(n):
        app.processEvents()
        w.pool.waitForDone(5)


s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
s.setValue("language", "sv")
store = PlaylistStore(s)
mine = store.add({"name": "Min IPTV", "server": "http://example.invalid",
                  "username": "u", "password": "p"})
family = store.add({"name": "Familjen", "server": "http://example.invalid",
                    "username": "u", "password": "p"})
store.set_active(mine["id"])
theme.apply_theme(s)
app.setStyleSheet(theme.build_style())
w = mw.MainWindow(DemoClient(), s, store)
w.resize(1280, 800)
w.show()
pump(30)
w._set_sidebar_collapsed(False)
w.switch_mode("live")
pump(20)

avatar, gear = w._playlist_btn, w._settings_btn
content = w._side_scroll.widget()


def rect(widget):
    tl = widget.mapTo(content, widget.rect().topLeft())
    return tl.x(), tl.y(), widget.width(), widget.height()


# -- the avatar: initials on the accent, the full name for the pointer ----------
assert avatar.isVisible() and gear.isVisible()
assert avatar.initials() == "MI"
assert "Min IPTV" in avatar.toolTip() and avatar.accessibleName() == "Min IPTV"
fill, ink = avatar.colors()
assert fill == P["accent"] and theme.contrast(ink, fill) >= 4.5
img = avatar.grab().toImage()
c = img.pixelColor(4, img.height() // 2)
want = QColor(P["accent"])
assert abs(c.red() - want.red()) + abs(c.green() - want.green()) + abs(c.blue() - want.blue()) < 30, c.name()
assert gear.text() == "" and not gear.icon().isNull()
assert gear.toolTip() == tr("btn_settings")

# -- the foot: under the hairline, side by side, centred; nothing under the logo
ax, ay, aw, ah = rect(avatar)
gx, gy, gw, gh = rect(gear)
_rx, ry, _rw, _rh = rect(w._bottom_rule)
assert ry < ay and ry < gy
assert abs(ay - gy) <= 1 and ax + aw <= gx            # avatar first (left)
mid = (ax + gx + gw) / 2
side_mid = content.width() / 2
assert abs(mid - side_mid) <= 3, (mid, side_mid)
_lx, ly, _lw, lh = rect(w._sidebar_logo)
_hx, hy, _hw, _hh = rect(w.nav_btns["home"])
assert hy - (ly + lh) < 24, (ly + lh, hy)             # Home right under the logo

# -- a click opens the same switcher, the active source ticked --------------------
avatar.click()
pump()
assert menus, "no menu"
acts = [(a.text(), a.isChecked()) for a in menus[-1].actions()]
# The sources, then the active one's own page in Settings, then the
# household's profiles (one profile: no list of them above).
assert acts == [("Min IPTV", True), ("Familjen", False), ("", False),
                (tr("src_settings_menu"), False), ("", False),
                (tr("profile_manage"), False)], acts

# -- another source, a new name, a new theme: the avatar follows ------------------
store.set_active(family["id"])
w._update_playlist_btn()
assert avatar.initials() == "FA" and "Familjen" in avatar.toolTip()
store.update(family["id"], name="Kvällens Kanaler")
w._update_playlist_btn()
assert avatar.initials() == "KK" and avatar.accessibleName() == "Kvällens Kanaler"
for name in ("light", "nord", "dracula", "graphite"):
    s.setValue("theme", name)
    w._apply_theme_now()
    pump(2)
    fill, ink = avatar.colors()
    assert fill == P["accent"], (name, fill)
    assert theme.contrast(ink, fill) >= 4.5, (name, fill, ink)
w._set_language("en")
pump()
assert tr("menu_playlists") in avatar.toolTip() and gear.toolTip() == "Settings"
w._set_language("sv")
pump()

# -- the rail: one above the other, centred -------------------------------------
w._set_sidebar_collapsed(True)
pump(20)
ax, ay, aw, ah = rect(avatar)
gx, gy, gw, gh = rect(gear)
assert ay + ah <= gy and abs((ax + aw / 2) - (gx + gw / 2)) <= 1
assert abs((ax + aw / 2) - content.width() / 2) <= 3, (ax, aw, content.width())
assert avatar.isVisible() and gear.isVisible()
w._set_sidebar_collapsed(False)
pump(20)

# -- right to left: the pair mirrored -------------------------------------------
app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
pump(10)
ax, ay, aw, ah = rect(avatar)
gx, gy, gw, gh = rect(gear)
assert abs(ay - gy) <= 1 and gx + gw <= ax, (ax, gx)
app.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
pump(10)

# -- no source of one's own (demo, explore): no avatar to show -------------------
for p in store.playlists():
    store.remove(p["id"])
w._update_playlist_btn()
assert not avatar.isVisible() and gear.isVisible()

# -- Multiview: not in the sidebar, still in the View menu and the channel menu ---
assert not hasattr(w, "_multiview_btn")
side_buttons = w._side.findChildren(QPushButton)
mv_words = tr("menu_multiview")
assert not [b for b in side_buttons
            if mv_words in (b.text(), b.toolTip(), b.accessibleName())]
opened = []
w._multiview_available = lambda: True
w._ensure_multiview_window = lambda: opened.append("mv")
def actions(menu):
    for a in menu.actions():
        if a.menu() is not None:
            yield from actions(a.menu())
        else:
            yield a
bar_actions = [a for top in w.menuBar().actions() if top.menu() is not None
               for a in actions(top.menu())]
mv_act = next(a for a in bar_actions if a.text() == mv_words)
mv_act.trigger()
assert opened == ["mv"], opened
added = []
w._add_channel_to_multiview = lambda it, n=None: added.append((it.get("name"), n))
assert w.list_model.rowCount() > 0
idx = w.list_model.index(0, 0)
menus.clear()
w._context_menu(w.listw.visualRect(idx).center())
assert menus, "no channel menu"
sub = next(a.menu() for a in menus[-1].actions()
           if a.menu() is not None and a.text() == tr("mv_add"))
sub.actions()[0].trigger()
assert added and added[0][1] == 0, added
print("FOOT_OK", flush=True)
os._exit(0)
"""


def _child_env(tmp_path) -> dict:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
                "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                "XDG_CACHE_HOME"):
        env[var] = str(tmp_path / var.lower())
    return env


def test_the_sidebars_foot_avatar_settings_and_no_multiview(tmp_path):
    for _attempt in (1, 2):
        out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT,
                             env=_child_env(tmp_path), capture_output=True,
                             text=True, timeout=180)
        if "FOOT_OK" in out.stdout:
            return
        if "AssertionError" in out.stderr:
            break
    raise AssertionError(out.stdout + out.stderr[-4000:])
