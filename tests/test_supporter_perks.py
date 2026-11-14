"""What a supporter key opens: the supporter's app icon (unless switched
off) and two themes of their own - and what happens without one: the
themes shown with a heart that opens the supporter window, and a supporter
theme stored from before giving way to the default, quietly."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from dopeiptv.core import supporter
from dopeiptv.ui import theme
from dopeiptv.ui.theme import SUPPORTER_THEMES, THEMES, effective_theme, is_light

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):
        self[key] = value


@pytest.fixture
def key(monkeypatch):
    """A supporter key that checks out against a public key of the test's."""
    seed, pub = supporter.new_seed()
    monkeypatch.setattr(supporter, "PUBLIC_KEY_HEX", pub.hex())
    return supporter.make(seed)


def test_one_dark_and_one_light_theme_of_their_own():
    assert SUPPORTER_THEMES <= set(THEMES)
    assert sorted(is_light(k) for k in SUPPORTER_THEMES) == [False, True]
    others = [t for k, t in THEMES.items() if k not in SUPPORTER_THEMES]
    for k in SUPPORTER_THEMES:
        t = THEMES[k]
        assert "accent" in t, k
        # Not a copy of a theme everyone has: no other one shares its page,
        # sidebar or accent.
        for o in others:
            assert (t["bg"], t["side"]) != (o["bg"], o["side"]), (k, o["name"])
            assert t["accent"][0] != o.get("accent", ("",))[0], (k, o["name"])


def test_without_a_key_a_stored_supporter_theme_shows_the_default():
    s = _Settings(theme="aurora", theme_light="dawn")
    assert effective_theme(s, system_dark=True) == "graphite"
    s["theme_follow_system"] = "true"
    assert effective_theme(s, system_dark=False) == "light"
    # A key that does not check out (another site's, or damaged) is none.
    s["supporter_key"] = "DS1-not-a-key"
    assert effective_theme(s, system_dark=False) == "light"


def test_with_a_key_they_are_picked_like_any_theme(key):
    s = _Settings(theme="aurora", theme_light="dawn", supporter_key=key)
    assert effective_theme(s, system_dark=True) == "aurora"
    s["theme_follow_system"] = "true"
    assert effective_theme(s, system_dark=False) == "dawn"
    assert theme.drop_locked_themes(s) is False
    assert s["theme"] == "aurora" and s["theme_light"] == "dawn"


def test_the_key_gone_puts_the_stored_choice_back_to_the_default(key):
    s = _Settings(theme="aurora", theme_light="dawn", supporter_key=key)
    del s["supporter_key"]
    assert theme.drop_locked_themes(s) is True
    assert s["theme"] == "graphite" and s["theme_light"] == "light"
    assert theme.drop_locked_themes(s) is False          # once
    # Everyone else's themes are never touched.
    s2 = _Settings(theme="nord", theme_light="sky")
    assert theme.drop_locked_themes(s2) is False
    assert s2 == {"theme": "nord", "theme_light": "sky"}


def test_settings_that_cannot_be_read_close_the_themes_without_raising():
    class Broken:
        def value(self, *_a, **_k):
            raise RuntimeError("unreadable")

    assert theme.supporter_themes_open(Broken()) is False


def test_the_icon_switch_is_on_unless_turned_off(key):
    from dopeiptv.ui import app_icon
    s = _Settings()
    assert app_icon.switched_on(s) and not app_icon.wanted(s)
    s["supporter_key"] = key
    assert app_icon.wanted(s)
    s["supporter_icon"] = "false"
    assert not app_icon.switched_on(s) and not app_icon.wanted(s)


_ICON = r"""
import os, sys, tempfile, pathlib
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QBuffer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication
app = QApplication([])
from dopeiptv.ui import app_icon
from dopeiptv.ui.theme import HEART

def png(icon, size):
    buf = QBuffer(); buf.open(QBuffer.OpenModeFlag.WriteOnly)
    icon.pixmap(size, size).save(buf, "PNG"); buf.close()
    return bytes(buf.data())

# The plain icon is drawn exactly as before (the packaged ones are compared
# against it byte for byte in test_packaging_icons).
inset = app_icon.ICON_SAFE_INSET
assert png(app_icon.make_app_icon(inset=inset), 256) == \
    png(app_icon.make_app_icon(inset=inset, supporter=False), 256)
sup = app_icon.make_app_icon(inset=inset, supporter=True)
for size in (256, 32):
    assert png(sup, size) != png(app_icon.make_app_icon(inset=inset), size), size
# The supporter's: the tile shades from the accent (top left) into the
# heart's colour (bottom right), and a white heart crowns the triangle.
img = sup.pixmap(256, 256).toImage()
off = 256 * inset; side = 256 - 2 * off
at = lambda fx, fy: img.pixelColor(int(off + side * fx), int(off + side * fy))
blue, heart = QColor(app_icon.ICON_ACCENT), QColor(HEART)
near = lambda a, b: max(abs(a.red() - b.red()), abs(a.green() - b.green()), abs(a.blue() - b.blue())) < 48
assert near(at(0.12, 0.12), blue), at(0.12, 0.12).name()
assert near(at(0.88, 0.88), heart), at(0.88, 0.88).name()
assert at(0.74, 0.29).name() == "#ffffff", at(0.74, 0.29).name()
assert at(0.62, 0.20).name() != "#ffffff"          # the heart is not a blob
assert img.pixelColor(2, 128).alpha() == 0          # the same margin

# show(): the window icon, changed only when it changes; the user's icon
# theme only when the start asked for it.
data = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = data
assert app_icon.show(False) is True
plain_icon = png(app.windowIcon(), 64)
assert app_icon.show(False) is False
assert app_icon.show(True) is True and png(app.windowIcon(), 64) != plain_icon
assert not os.listdir(data), "a window built outside the start wrote icons"
app_icon.show(False, theme_icons=True)
if sys.platform.startswith("linux"):
    f = pathlib.Path(data, "icons/hicolor/48x48/apps/dopeiptv.png")
    first = f.read_bytes()
    assert app_icon.show(True) is True and f.read_bytes() != first
    assert not [p for p in f.parent.iterdir() if p.name.endswith(".tmp")]
    assert app_icon.show(False) is True and f.read_bytes() == first
print("ICON_OK")
"""


def test_the_supporter_icon_is_drawn_and_shown_and_switched():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", _ICON], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=120)
    assert "ICON_OK" in out.stdout, out.stdout + out.stderr[-3000:]


_WINDOW = r"""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QDialog
from dopeiptv.core import supporter
seed, pub = supporter.new_seed()
supporter.PUBLIC_KEY_HEX = pub.hex()
KEY = supporter.make(seed)
app = QApplication.instance() or QApplication([])
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import app_icon, theme
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui.settings_ui import ThemeTile, ToggleSwitch

s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
# Chosen while a supporter, the key since gone: the start shows the default.
s.setValue("theme", "aurora")
theme.apply_theme(s)
assert theme.P["name"] == theme.THEMES["graphite"]["name"]
w = MainWindow(DemoClient(), s, PlaylistStore(s))
assert s.value("theme") == "graphite"           # and Settings says so
assert app_icon._shown is False

# -- the picker without a key: a heart, a word, the supporter window --------
opened = []
real_open = w.open_supporter
w.open_supporter = lambda key="": opened.append(1)

def in_settings(check):
    def run(self):
        check(self)
        return 1
    saved = QDialog.exec
    QDialog.exec = run
    try:
        w.open_settings()
    finally:
        QDialog.exec = saved

def locked(d):
    tiles = {t.key: t for t in d.findChildren(ThemeTile)}
    for k in theme.SUPPORTER_THEMES:
        assert tiles[k].locked and tr("theme_supporter_tip") in tiles[k].toolTip(), k
    assert not tiles["nord"].locked and tiles["nord"].toolTip() == tr("theme_nord")
    tiles["aurora"].click()
    assert opened == [1]                         # the supporter window instead
    assert s.value("theme") == "graphite" and not tiles["aurora"].isChecked()
    assert tiles["graphite"].isChecked()
    icon_sw = [sw for sw in d.findChildren(ToggleSwitch)
               if sw.accessibleName() == tr("set_supporter_icon")][0]
    assert not icon_sw.parentWidget().isVisibleTo(d)   # a supporter's switch
    from PyQt6.QtWidgets import QComboBox
    lights = [b for b in d.findChildren(QComboBox) if b.findData("light") >= 0]
    assert lights and all(b.findData("dawn") < 0 for b in lights)
in_settings(locked)

# A key entered from that window: the theme picked comes on at once.
def enter_key(key=""):
    s.setValue("supporter_key", KEY)
    w._donate_heart_refresh()
w.open_supporter = enter_key

def unlocks(d):
    tiles = {t.key: t for t in d.findChildren(ThemeTile)}
    tiles["aurora"].click()
    assert not tiles["aurora"].locked and tiles["aurora"].isChecked()
    assert s.value("theme") == "aurora"
    assert theme.P["name"] == theme.THEMES["aurora"]["name"]
    icon_sw = [sw for sw in d.findChildren(ToggleSwitch)
               if sw.accessibleName() == tr("set_supporter_icon")][0]
    assert icon_sw.parentWidget().isVisibleTo(d) and icon_sw.isChecked()
in_settings(unlocks)
assert app_icon._shown is True                   # the key brought the icon

# -- the icon switch, live ---------------------------------------------------
def icon_off(d):
    sw = [x for x in d.findChildren(ToggleSwitch)
          if x.accessibleName() == tr("set_supporter_icon")][0]
    sw.setChecked(False)
in_settings(icon_off)
assert s.value("supporter_icon") == "false" and app_icon._shown is False
w._donate_heart_refresh()                        # the minute's tick keeps it off
assert app_icon._shown is False
s.setValue("supporter_icon", "true")
w._app_icon_refresh()
assert app_icon._shown is True

# -- the key removed while a supporter theme is on: the default, quietly -----
s.remove("supporter_key")
w._donate_heart_refresh()
assert s.value("theme") == "graphite"
assert theme.P["name"] == theme.THEMES["graphite"]["name"]
assert app_icon._shown is False
w._donate_heart_refresh()                        # and nothing more after that
assert s.value("theme") == "graphite"
print("PERKS_OK")
"""


def test_the_window_opens_and_closes_the_perks_with_the_key():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", _WINDOW], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert "PERKS_OK" in out.stdout, out.stdout + out.stderr[-3000:]
