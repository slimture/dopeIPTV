"""The app's icon, picked on Settings > Appearance from several (the blue
one by default, the heart icon one of them), and every theme everyone's.
The supporter key is gone: nothing reads one, nothing it opened is locked,
and an older app's key and icon switch are removed from the settings."""
from __future__ import annotations

import os
import subprocess
import sys

from dopeiptv.ui import theme
from dopeiptv.ui.theme import THEMES, effective_theme, is_light

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The two themes of the heart icon's shading.
HEARTS = ("aurora", "dawn")


class _Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):
        self[key] = value

    def contains(self, key):
        return key in self

    def remove(self, key):
        self.pop(key, None)


def test_the_two_heart_themes_are_their_own():
    assert set(HEARTS) <= set(THEMES)
    assert sorted(is_light(k) for k in HEARTS) == [False, True]
    others = [t for k, t in THEMES.items() if k not in HEARTS]
    for k in HEARTS:
        t = THEMES[k]
        assert "accent" in t, k
        for o in others:
            assert (t["bg"], t["side"]) != (o["bg"], o["side"]), (k, o["name"])


def test_nothing_locks_a_theme():
    for name in ("SUPPORTER_THEMES", "supporter_themes_open", "drop_locked_themes"):
        assert not hasattr(theme, name), name
    s = _Settings(theme="aurora", theme_light="dawn")
    assert effective_theme(s, system_dark=True) == "aurora"
    s["theme_follow_system"] = "true"
    assert effective_theme(s, system_dark=False) == "dawn"


def test_the_icon_is_the_one_picked_else_the_blue_one():
    from dopeiptv.ui import app_icon
    assert app_icon.DEFAULT == "classic" and "heart" in app_icon.LOOKS
    assert app_icon.chosen(_Settings()) == "classic"
    assert app_icon.chosen(_Settings(app_icon="heart")) == "heart"
    assert app_icon.chosen(_Settings(app_icon="sunset")) == "sunset"
    assert app_icon.chosen(_Settings(app_icon="nonsense")) == "classic"
    # 1.6.10's switch and an old key change nothing (the update's surprise).
    assert app_icon.chosen(_Settings(supporter_icon="true", supporter_key="DS1-" + "a" * 100)) == "classic"

    class Broken:
        def value(self, *_a, **_k):
            raise RuntimeError("unreadable")
    assert app_icon.chosen(Broken()) == "classic"


def test_the_key_is_gone_from_the_app():
    import importlib.util
    from dopeiptv.i18n import _STRINGS
    assert importlib.util.find_spec("dopeiptv.core.supporter") is None
    for k in _STRINGS:
        assert "supporter" not in k, k
    from dopeiptv.ui import app_menu, dialogs
    assert not hasattr(dialogs, "SupporterDialog")
    assert all("supporter" not in names for _g, _m, names in app_menu.GROUPS)
    from dopeiptv.core import commands
    assert commands.parse_link("dopeiptv://supporter?key=DS1-" + "a" * 100) is None


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

inset = app_icon.ICON_SAFE_INSET
# Every icon drawn at every size, each its own picture.
seen = {}
for key in app_icon.LOOKS:
    icon = app_icon.make_app_icon(inset=inset, look=key, accent="#22aa44")
    for size in (256, 48, 32):
        img = icon.pixmap(size, size).toImage()
        assert img.width() == size, (key, size)
        assert img.pixelColor(size // 2, size // 2).alpha() > 0, (key, size)
        assert img.pixelColor(1, size // 2).alpha() == 0, (key, size)    # the margin
    seen[key] = png(icon, 128)
assert len(set(seen.values())) == len(seen), "two icons drawn alike"
# The blue one is what make_app_icon draws by default (the packages' icon).
assert png(app_icon.make_app_icon(inset=inset), 256) == png(app_icon.make_app_icon(inset=inset, look="classic"), 256)
# The heart icon: shaded from the blue into the heart's colour, a white heart.
img = app_icon.make_app_icon(inset=inset, look="heart").pixmap(256, 256).toImage()
off = 256 * inset; side = 256 - 2 * off
at = lambda fx, fy: img.pixelColor(int(off + side * fx), int(off + side * fy))
near = lambda a, b: max(abs(a.red() - b.red()), abs(a.green() - b.green()), abs(a.blue() - b.blue())) < 48
assert near(at(0.12, 0.12), QColor(app_icon.ICON_ACCENT)), at(0.12, 0.12).name()
assert near(at(0.88, 0.88), QColor(HEART)), at(0.88, 0.88).name()
assert at(0.74, 0.29).name() == "#ffffff"
# "accent" in the accent given.
acc = app_icon.make_app_icon(inset=inset, look="accent", accent="#22aa44").pixmap(256, 256).toImage()
assert near(acc.pixelColor(int(off + side * 0.15), int(off + side * 0.5)), QColor("#22aa44"))

# show(): changed only when it changes; the icon theme only when asked.
data = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = data
assert app_icon.show("classic") is True
plain = png(app.windowIcon(), 64)
assert app_icon.show("classic") is False
assert app_icon.show("nonsense") is False                 # the blue one, as shown
assert app_icon.show("heart") is True and png(app.windowIcon(), 64) != plain
assert not os.listdir(data), "a window built outside the start wrote icons"
app_icon.show("classic", theme_icons=True)
if sys.platform.startswith("linux"):
    f = pathlib.Path(data, "icons/hicolor/48x48/apps/dopeiptv.png")
    first = f.read_bytes()
    assert app_icon.show("retro") is True and f.read_bytes() != first
    assert not [p for p in f.parent.iterdir() if p.name.endswith(".tmp")]
    assert app_icon.show("classic") is True and f.read_bytes() == first
print("ICON_OK")
"""


def test_every_icon_is_drawn_and_shown():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", _ICON], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=120)
    assert "ICON_OK" in out.stdout, out.stdout + out.stderr[-3000:]


_WINDOW = r"""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QDialog, QPushButton
app = QApplication.instance() or QApplication([])
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import app_icon, theme
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui.settings_ui import IconTile

s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
s.setValue("theme", "aurora")
s.setValue("supporter_key", "DS1-" + "a" * 100)      # an older app's: not read
theme.apply_theme(s)
w = MainWindow(DemoClient(), s, PlaylistStore(s))
assert app_icon._shown[0] == "classic"
assert not hasattr(w, "open_supporter") and not hasattr(w, "_supporter_action")
assert "supporter" not in w._menu_actions

def in_settings(check):
    def run(self):
        from dopeiptv.ui.settings_ui import SettingsShell
        self.findChild(SettingsShell).build_all()
        check(self)
        return 1
    saved = QDialog.exec
    QDialog.exec = run
    try:
        w.open_settings()
    finally:
        QDialog.exec = saved

def pick(key):
    def go(d):
        tiles = {t.key: t for t in d.findChildren(IconTile)}
        assert set(tiles) == set(app_icon.LOOKS), sorted(tiles)
        assert all("upporter" not in b.text() for b in d.findChildren(QPushButton))
        tiles[key].click()
    return go

in_settings(pick("heart"))
assert s.value("app_icon") == "heart" and app_icon._shown[0] == "heart"
in_settings(pick("classic"))
assert s.value("app_icon") == "classic" and app_icon._shown[0] == "classic"
# "accent" follows the accent: a new accent, a new icon.
in_settings(pick("accent"))
first = app_icon._shown
s.setValue("accent", "green")
w._apply_theme_now()
assert app_icon._shown[0] == "accent" and app_icon._shown != first
print("WINDOW_OK")
"""


def test_the_icon_is_picked_in_settings_and_shown_live():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", _WINDOW], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert "WINDOW_OK" in out.stdout, out.stdout + out.stderr[-3000:]


def test_1_6_10s_heart_switch_is_kept_as_the_pickers_heart():
    """Turned on by hand in 1.6.10, the heart stays after the update; off,
    never touched, or with a choice already made, nothing changes - and the
    old switch goes either way."""
    from dopeiptv.ui import app_icon
    for before, after in (({"heart_icon": "true"}, "heart"),
                          ({"heart_icon": "false"}, "classic"),
                          ({}, "classic"),
                          ({"heart_icon": "true", "app_icon": "forest"}, "forest"),
                          ({"supporter_icon": "true", "supporter_key": "DS1-x"}, "classic")):
        s = _Settings(before)
        app_icon.carry_over(s)
        assert app_icon.chosen(s) == after, (before, dict(s))
        assert "heart_icon" not in s

    class Broken:
        def value(self, *_a, **_k):
            raise RuntimeError("unreadable")
    app_icon.carry_over(Broken())                    # never raises


# -- the Mac app's own icon (app_icon.keep_on_bundle) ---------------------------

_APP = []


def _qapp():
    """A QApplication kept for the module (one let go at once ends Qt)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    if not _APP:
        _APP.append(QApplication.instance() or QApplication([]))
    return _APP[0]


def _fake_mac(monkeypatch, tmp_path, works=True):
    """A built app at a path of its own; set_bundle_icon as Get Info does it:
    an Icon\\r file at the bundle's root, or none."""
    from dopeiptv.core import platform_macos
    from dopeiptv.ui import app_icon
    app = tmp_path / "dopeIPTV.app"
    (app / "Contents").mkdir(parents=True)
    calls = []

    def set_icon(bundle, png):
        calls.append((bundle, png))
        if not works:
            return False
        icon = os.path.join(bundle, platform_macos.CUSTOM_ICON)
        if png is None:
            if os.path.exists(icon):
                os.remove(icon)
        else:
            with open(icon, "wb") as fh:
                fh.write(png)
        return True

    monkeypatch.setattr(app_icon, "_bundle", lambda: app)
    monkeypatch.setattr(platform_macos, "set_bundle_icon", set_icon)
    return app, calls


def test_the_icon_picked_is_kept_on_the_mac_app(monkeypatch, tmp_path):
    """The Dock and Finder show a closed app's own icon: the one picked is put
    on the bundle once, again only when it changes or a new bundle (an update)
    came without it, and the blue one takes it away."""
    _qapp()
    from dopeiptv.core import platform_macos
    from dopeiptv.ui import app_icon
    app, calls = _fake_mac(monkeypatch, tmp_path)
    s = _Settings()
    assert app_icon.keep_on_bundle(s, "heart") == "set"
    assert calls[-1][0] == str(app) and calls[-1][1].startswith(b"\x89PNG")
    assert (app / platform_macos.CUSTOM_ICON).exists()
    assert s[app_icon.BUNDLE_KEY] == f"heart||{app}"
    assert app_icon.keep_on_bundle(s, "heart") == "" and len(calls) == 1
    # An update's new bundle has the blue icon: the next start puts it back.
    (app / platform_macos.CUSTOM_ICON).unlink()
    assert app_icon.keep_on_bundle(s, "heart") == "set" and len(calls) == 2
    assert app_icon.keep_on_bundle(s, "forest") == "set" and len(calls) == 3
    # "accent" follows the accent.
    monkeypatch.setattr(theme, "ACCENT", "#11aa55")
    assert app_icon.keep_on_bundle(s, "accent") == "set"
    monkeypatch.setattr(theme, "ACCENT", "#aa1155")
    assert app_icon.keep_on_bundle(s, "accent") == "set" and len(calls) == 5
    # The blue one: the bundle's own icon again, nothing kept of it.
    assert app_icon.keep_on_bundle(s, app_icon.DEFAULT) == "removed"
    assert calls[-1] == (str(app), None)
    assert not (app / platform_macos.CUSTOM_ICON).exists()
    assert app_icon.BUNDLE_KEY not in s
    assert app_icon.keep_on_bundle(s, app_icon.DEFAULT) == "" and len(calls) == 6
    # One not known is the blue one.
    assert app_icon.keep_on_bundle(s, "no-such-look") == ""


def test_an_icon_the_person_gave_the_app_in_finder_stays(monkeypatch, tmp_path):
    """Never picked here, the app leaves a custom icon of Finder's Get Info
    alone; and a bundle that refuses one keeps nothing said of it."""
    _qapp()
    from dopeiptv.core import platform_macos
    from dopeiptv.ui import app_icon
    app, calls = _fake_mac(monkeypatch, tmp_path)
    (app / platform_macos.CUSTOM_ICON).write_bytes(b"theirs")
    s = _Settings()
    assert app_icon.keep_on_bundle(s, app_icon.DEFAULT) == ""
    assert calls == [] and (app / platform_macos.CUSTOM_ICON).read_bytes() == b"theirs"
    app2, calls2 = _fake_mac(monkeypatch, tmp_path / "other", works=False)
    s2 = _Settings()
    assert app_icon.keep_on_bundle(s2, "heart") == "" and len(calls2) == 1
    assert app_icon.BUNDLE_KEY not in s2


def test_only_a_built_mac_app_of_its_own_is_changed(monkeypatch, tmp_path):
    """A checkout, Linux and Windows, an app macOS moved aside or one this
    user may not change: nothing is written anywhere."""
    from dopeiptv.core import macupdate
    from dopeiptv.ui import app_icon
    monkeypatch.setattr(macupdate, "app_bundle", lambda: None)
    assert app_icon._bundle() is None
    assert app_icon.keep_on_bundle(_Settings(), "heart") == ""
    moved = tmp_path / "AppTranslocation" / "x" / "dopeIPTV.app"
    moved.mkdir(parents=True)
    monkeypatch.setattr(macupdate, "app_bundle", lambda: moved)
    assert app_icon._bundle() is None
    mine = tmp_path / "Applications" / "dopeIPTV.app"
    mine.mkdir(parents=True)
    monkeypatch.setattr(macupdate, "app_bundle", lambda: mine)
    assert app_icon._bundle() == mine
    monkeypatch.setattr(os, "access", lambda *_a, **_k: False)
    assert app_icon._bundle() is None


def test_what_was_put_on_the_bundle_is_this_computers_alone():
    """The note of the icon on the bundle is never in a backup nor synced:
    another computer's app has its own."""
    from dopeiptv.core import backup, sync
    from dopeiptv.ui import app_icon
    assert not backup.covered("main", app_icon.BUNDLE_KEY)
    assert not sync.syncable(app_icon.BUNDLE_KEY)


def test_set_bundle_icon_asks_nsworkspace(monkeypatch, tmp_path):
    """Apple's own setIcon:forFile:options: on the bundle (Get Info's way),
    None to take it away; AppKit missing or failing is False, never raised."""
    import types
    from dopeiptv.core import platform_macos
    seen = []

    class Space:
        def setIcon_forFile_options_(self, image, path, opts):
            seen.append((image, path, opts))
            return True

        def noteFileSystemChanged_(self, path):
            seen.append(("noted", path))

    class Image:
        @staticmethod
        def alloc():
            return Image()

        def initWithData_(self, data):
            self.data = data
            return self

    kit = types.SimpleNamespace(
        NSData=types.SimpleNamespace(dataWithBytes_length_=lambda b, n: (b, n)),
        NSImage=Image,
        NSWorkspace=types.SimpleNamespace(sharedWorkspace=lambda: Space()))
    monkeypatch.setitem(sys.modules, "AppKit", kit)
    app = tmp_path / "dopeIPTV.app"
    app.mkdir()
    assert platform_macos.set_bundle_icon(str(app), b"\x89PNG..")
    image, path, opts = seen[0]
    assert image.data == (b"\x89PNG..", 6) and path == str(app) and opts == 0
    assert ("noted", str(app)) in seen
    seen.clear()
    assert platform_macos.set_bundle_icon(str(app), None)
    assert seen[0] == (None, str(app), 0)
    kit.NSWorkspace = types.SimpleNamespace(sharedWorkspace=lambda: 1 / 0)
    assert platform_macos.set_bundle_icon(str(app), None) is False
    monkeypatch.setitem(sys.modules, "AppKit", None)
    assert platform_macos.set_bundle_icon(str(app), b"x") is False


def test_the_window_keeps_the_icon_on_the_mac_app_a_moment_later(monkeypatch):
    """Only on a Mac, after the window shows and after the last of quick picks;
    Reset all takes away what this app put there."""
    _qapp()
    from PyQt6.QtCore import QObject
    from dopeiptv.ui import app_icon, mw_donate

    class Win(mw_donate._DonateMixin, QObject):
        def __init__(self):
            QObject.__init__(self)
            self.settings = _Settings({"app_icon": "grape"})

    kept = []
    monkeypatch.setattr(app_icon, "show", lambda *_a, **_k: True)
    monkeypatch.setattr(app_icon, "keep_on_bundle", lambda s, look: kept.append(look))
    monkeypatch.setattr(mw_donate.sys, "platform", "linux")
    w = Win()
    w._app_icon_refresh()
    assert getattr(w, "_bundle_icon_timer", None) is None
    monkeypatch.setattr(mw_donate.sys, "platform", "darwin")
    w._app_icon_refresh()
    w._app_icon_refresh()
    timer = w._bundle_icon_timer
    assert timer.isActive() and timer.isSingleShot()
    assert timer.interval() == mw_donate.BUNDLE_ICON_MS and kept == []
    timer.stop()
    w._app_icon_keep()
    assert kept == ["grape"]
    src = open(os.path.join(_REPO_ROOT, "dopeiptv", "ui", "mw_settings.py"),
               encoding="utf-8").read()
    reset = src[src.index("def _reset_all_settings"):]
    assert reset.index("keep_on_bundle(self.settings, app_icon.DEFAULT)") < reset.index("st.clear()")
