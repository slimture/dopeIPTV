"""What a supporter key used to open, everyone's now: the two themes of the
supporter mark's shading and the heart icon for the app. A tip gives
nothing in return (dopeiptv.org/support/), so no theme or icon is locked;
a key from before keeps the icon it had, unless switched off. Sync between
computers was never left among them: it is for everyone."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from dopeiptv.core import supporter
from dopeiptv.ui import theme
from dopeiptv.ui.theme import THEMES, effective_theme, is_light

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The two themes a supporter key opened until November 2026.
FORMER = ("aurora", "dawn")


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


def test_the_two_themes_are_still_their_own():
    assert set(FORMER) <= set(THEMES)
    assert sorted(is_light(k) for k in FORMER) == [False, True]
    others = [t for k, t in THEMES.items() if k not in FORMER]
    for k in FORMER:
        t = THEMES[k]
        assert "accent" in t, k
        for o in others:
            assert (t["bg"], t["side"]) != (o["bg"], o["side"]), (k, o["name"])
            assert t["accent"][0] != o.get("accent", ("",))[0], (k, o["name"])


def test_nothing_locks_a_theme_any_more():
    for name in ("SUPPORTER_THEMES", "supporter_themes_open", "drop_locked_themes"):
        assert not hasattr(theme, name), name
    s = _Settings(theme="aurora", theme_light="dawn")
    assert effective_theme(s, system_dark=True) == "aurora"
    s["theme_follow_system"] = "true"
    assert effective_theme(s, system_dark=False) == "dawn"
    s["supporter_key"] = "DS1-not-a-key"
    assert effective_theme(s, system_dark=False) == "dawn"


def test_the_heart_icon_is_everyones_switch(key):
    from dopeiptv.ui import app_icon
    s = _Settings()
    assert not app_icon.wanted(s)                    # off by default
    s["supporter_icon"] = "true"
    assert app_icon.wanted(s)                        # anyone may switch it on
    s = _Settings(supporter_key=key)
    assert app_icon.wanted(s)                        # a key from before: as it was
    s["supporter_icon"] = "false"
    assert not app_icon.wanted(s)

    class Broken:
        def value(self, *_a, **_k):
            raise RuntimeError("unreadable")
    assert app_icon.wanted(Broken()) is False


def test_the_strings_of_the_perks_are_gone():
    from dopeiptv.i18n import _STRINGS
    for k in ("theme_supporter_tip", "supporter_perks", "supporter_again"):
        assert k not in _STRINGS, k


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
from dopeiptv.ui.settings_ui import SettingsPage, ThemeTile, ToggleSwitch


def page_of(widget):
    # Its page, not the dialog: Settings opens on the first page, and the
    # pages not on show are hidden.
    while not isinstance(widget, SettingsPage):
        widget = widget.parentWidget()
    return widget

s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
# A former supporter theme, no key: it stays as chosen.
s.setValue("theme", "aurora")
theme.apply_theme(s)
assert theme.P["name"] == theme.THEMES["aurora"]["name"]
w = MainWindow(DemoClient(), s, PlaylistStore(s))
assert s.value("theme") == "aurora"
assert app_icon._shown is False                  # the heart icon is off by default

def in_settings(check):
    def run(self):
        from dopeiptv.ui.settings_ui import SettingsShell
        self.findChild(SettingsShell).build_all()     # each is built as it is needed
        check(self)
        return 1
    saved = QDialog.exec
    QDialog.exec = run
    try:
        w.open_settings()
    finally:
        QDialog.exec = saved

def icon_switch(d):
    return [sw for sw in d.findChildren(ToggleSwitch)
            if sw.accessibleName() == tr("set_supporter_icon")][0]

# -- every theme picked like any other, no key ------------------------------
def open_to_all(d):
    tiles = {t.key: t for t in d.findChildren(ThemeTile)}
    assert not hasattr(tiles["aurora"], "locked")
    assert tiles["aurora"].toolTip() == tr("theme_aurora")
    tiles["nord"].click()
    assert s.value("theme") == "nord"
    tiles["aurora"].click()
    assert s.value("theme") == "aurora" and tiles["aurora"].isChecked()
    assert theme.P["name"] == theme.THEMES["aurora"]["name"]
    from PyQt6.QtWidgets import QComboBox
    lights = [b for b in d.findChildren(QComboBox) if b.findData("light") >= 0]
    assert lights and all(b.findData("dawn") >= 0 for b in lights)
    sw = icon_switch(d)
    assert sw.parentWidget().isVisibleTo(page_of(sw)) and not sw.isChecked()
in_settings(open_to_all)

# -- the heart icon, switched on and off live, with no key -------------------
def icon_on(d):
    icon_switch(d).setChecked(True)
in_settings(icon_on)
assert s.value("supporter_icon") == "true" and app_icon._shown is True
w._donate_heart_refresh()                        # the minute's tick keeps it
assert app_icon._shown is True

def icon_off(d):
    icon_switch(d).setChecked(False)
in_settings(icon_off)
assert s.value("supporter_icon") == "false" and app_icon._shown is False

# -- a key from before, never switched: its icon as it had ------------------
s.remove("supporter_icon")
s.setValue("supporter_key", KEY)
w._donate_heart_refresh()
assert app_icon._shown is True
def keyed(d):
    assert icon_switch(d).isChecked()
in_settings(keyed)
s.remove("supporter_key")
w._donate_heart_refresh()
assert app_icon._shown is False
assert s.value("theme") == "aurora"              # the theme never goes with it
print("PERKS_OK")
"""


def test_the_window_has_no_perks_to_open():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", _WINDOW], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert "PERKS_OK" in out.stdout, out.stdout + out.stderr[-3000:]


def test_sync_is_for_everyone_not_a_perk(key):
    """Sync was a thank-you for supporters; it is for everyone now. Nothing
    of a key goes with it, and nothing says it is a supporter's."""
    from dopeiptv.core import build_check, sync
    from dopeiptv.i18n import set_language, tr
    assert not hasattr(sync, "HEADER")
    remote = sync.Remote("ab" * 32)
    # The build's kind goes to the site (official, modified or source) - no key.
    assert set(remote.headers) == {"User-Agent", "Content-Type", build_check.HEADER}
    assert key not in " ".join(remote.headers.values())
    set_language("en")
    for k in ("set_page_sync_sub", "sync_off_hint", "sync_privacy"):
        assert "supporter" not in tr(k).lower(), k
