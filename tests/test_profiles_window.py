"""Profiles in the window (ui/mw_profiles.py), on the real window in a child
process (offscreen): an existing settings file opens as the first profile
with everything in place; switching to another stops what plays and shows
that profile's own - favorites, history, Trakt, theme - with no restart; the
avatar and its menu, the picker (keys, TV mode's own); a kids profile never
sees a locked category, is never asked for the PIN of one, and opens
Settings only with a grown-up's PIN; the phone remote and the remote API
show the profile and switch it."""
from __future__ import annotations

import os
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import faulthandler, json, os, tempfile, time
faulthandler.dump_traceback_later(200, exit=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QEvent, QSettings, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QDialog, QInputDialog, QMenu, QMessageBox
app = QApplication.instance() or QApplication([])
told = []
QMessageBox.warning = staticmethod(lambda *a, **k: told.append(a[2] if len(a) > 2 else "") or 0)
QMessageBox.information = staticmethod(lambda *a, **k: 0)
tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)

from dopeiptv import i18n
from dopeiptv.core import profiles
from dopeiptv.core.profiles import MAIN_ID, ProfileSettings
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw, profiles_ui, theme
from dopeiptv.ui.theme import P

i18n.set_language("en")
menus = []
QMenu.exec = lambda self, *a, **k: menus.append(self)
mw.launch_player = lambda *a, **k: None


class Client(DemoClient):
    def live_categories(self):
        return [{"category_id": "1", "category_name": "News"},
                {"category_id": "2", "category_name": "Late night"},
                {"category_id": "3", "category_name": "XXX"}]

    def live_streams(self, category_id=None):
        rows = [{"stream_id": i + 1, "num": i + 1, "name": f"Chan {i + 1}", "stream_icon": "",
                 "category_id": "1" if i < 5 else "2", "epg_channel_id": ""} for i in range(9)]
        return [r for r in rows if category_id in (None, r["category_id"])]


# -- an existing settings file, as 1.5 left it ------------------------------------
path = os.path.join(tmp, "s.ini")
old = QSettings(path, QSettings.Format.IniFormat)
store0 = PlaylistStore(old)
pl = store0.add({"name": "Min IPTV", "server": "http://example.invalid", "username": "u",
                 "password": "p"})
pid = pl["id"]
old.setValue(f"favorites_{pid}", json.dumps({"all": [{"stream_id": 1, "name": "Chan 1"}]}))
old.setValue(f"history_{pid}", json.dumps([{"_key": 2, "_kind": "live", "name": "Chan 2",
                                            "_url": "http://x/2", "_watched_at": "2026-11-01T20:00:00"}]))
old.setValue("theme", "nord")
old.setValue("trakt_access_token", "me-token")
old.setValue("tv_inputs", "false")
old.sync()
del old

s = ProfileSettings(path, QSettings.Format.IniFormat)
reg = profiles.start(s, [], "Me")
assert profiles.active_id() == MAIN_ID
theme.apply_theme(s)
w = mw.MainWindow(Client(), s, PlaylistStore(s))
w.resize(1280, 800)
w.show()


def pump(n=10):
    for _ in range(n):
        app.processEvents()
        w.pool.waitForDone(5)


def until(cond, secs=4.0):
    end = time.monotonic() + secs
    while not cond() and time.monotonic() < end:
        pump(2)
    return cond()


pump(30)
# One profile: no picker, everything where it was.
assert w._profile_picker is None
assert w.favs.is_favorite(1) and [e["_key"] for e in w.history.items()] == [2]
assert w.trakt.access_token == "me-token" and P["name"] == theme.THEMES["nord"]["name"]
assert w.favs.settings.pid == MAIN_ID
# The avatar shows the source while there is one profile; its menu offers profiles.
assert w._playlist_btn.initials() == "MI"
w._playlist_btn.click()
pump()
texts = [a.text() for a in menus[-1].actions()]
assert texts[-1] == tr("profile_manage") and texts[0] == "Min IPTV", texts

# -- a second profile, for a kid ----------------------------------------------------
kid = w.profiles.add("Lisa", colour="pink", kids=True)
w._profiles_changed()
assert w._playlist_btn.initials() == "ME"              # the profile in use, now
w._playlist_btn.click()
pump()
acts = [(a.text(), a.isChecked()) for a in menus[-1].actions()]
assert acts[0] == ("Me", True) and acts[1] == ("Lisa", False), acts
assert acts[-1][0] == tr("profile_manage")


# What plays stops as Stop does, and the kid's own shows - no restart.
class Btn:
    def __init__(self):
        self.clicks = 0

    def click(self):
        self.clicks += 1
        player.current_url = None


class Player:
    current_url = "http://x/2"
    stop_btn = Btn()

    def __getattr__(self, name):
        return lambda *a, **k: 0


real_player = w.player
player = Player()
w.player = player
saved = []
w._save_resume_position = lambda: saved.append(1)
w.parental.set_pin("9999")
w.overrides.update("live", "2", locked=True)
w.switch_mode("live")
assert until(lambda: w.cat_list.count() >= 4)
assert any("Late night" in w.cat_list.item(i).text() for i in range(w.cat_list.count()))
old_favs = w.favs
# A week of asking for a donation under way: the heart shows for the grown-up...
from dopeiptv.core import donate_rhythm
s.setValue(donate_rhythm.K_FIRST, time.time() - 60 * 86400)
s.setValue(donate_rhythm.K_PERIOD, time.time() - 60)
w._donate_minute()
assert not w._donate_btn.isHidden()
assert w.switch_profile(kid.id)
# ...and never for a kids profile - gone with the switch, not a minute later.
assert w._donate_btn.isHidden()
assert player.stop_btn.clicks == 1 and saved
w.player = real_player
del w._save_resume_position
assert profiles.active_id() == kid.id and w.profiles.active_id == kid.id
assert w.favs is not old_favs and w.favs.items() == [] and w.history.items() == []
assert w.favs.settings.pid == kid.id and w.trakt.access_token == ""
assert P["name"] == theme.THEMES[theme.effective_theme(s)]["name"] != theme.THEMES["nord"]["name"]
assert w._playlist_btn.initials() == "LI"
assert w._playlist_btn.colors()[0] == profiles_ui.profile_fill(kid)
# The old profile's store, kept past the switch, still writes to its own.
old_favs.add("all", {"stream_id": 7, "name": "Chan 7"})
assert not w.favs.is_favorite(7)

# A kids profile: no locked category (nor one for adults), never a PIN question.
assert until(lambda: w.cat_list.count() >= 2)
pump(20)
names = [w.cat_list.item(i).text() for i in range(w.cat_list.count())]
assert not any("Late night" in n or "XXX" in n for n in names), names
assert any("News" in n for n in names), names
QInputDialog.getText = lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked for a PIN"))
assert w._request_unlock() is False
# Settings only with a grown-up's PIN.
opened = []
QDialog.exec = lambda self: opened.append(self) or 0
w.profiles.set_pin(MAIN_ID, "2468")
profiles_ui.ask_pin = lambda *a, **k: "1111"
w.open_settings()
assert not opened and told and told[-1] == tr("msg_wrong_pin")
profiles_ui.ask_pin = lambda *a, **k: "2468"
w.open_settings()
assert opened
opened.clear()

# -- the phone remote and the remote API ---------------------------------------------
st = w._phone_state()
assert st["profile"]["id"] == kid.id and {p["id"] for p in st["profiles"]} == {MAIN_ID, kid.id}
assert all("pin_hash" not in p and "pin_salt" not in p for p in st["profiles"])
assert next(p for p in st["profiles"] if p["id"] == MAIN_ID)["pin"] is True
try:
    w._phone_do("profile", {"id": MAIN_ID})
    raise SystemExit("a profile with a PIN opened without it")
except ValueError:
    pass
got = w._phone_do("profile", {"id": MAIN_ID, "pin": "2468"})
assert got["ok"] and profiles.active_id() == MAIN_ID
assert w.favs.is_favorite(1) and w.favs.is_favorite(7) and w.trakt.access_token == "me-token"
assert w._remote_do("status", {})["profile"] == "Me"
listed = w._remote_do("profiles", {})
assert listed["profile"]["name"] == "Me" and len(listed["profiles"]) == 2
assert w._remote_do("profile", {"name": "lisa"})["profile"]["id"] == kid.id
# A grown-up's profile again: the locked category is there, marked.
w.switch_profile(MAIN_ID)
w.switch_mode("live")
pump(30)
names = [w.cat_list.item(i).text() for i in range(w.cat_list.count())]
assert any("Late night" in n and tr("cat_locked_suffix") in n for n in names), names

# -- the picker: keys, and TV mode's own -------------------------------------------------
w.profiles.clear_pin(MAIN_ID)
w.show_profile_picker()
pump(5)
picker = w._profile_picker
assert picker is not None and picker.isVisible() and len(picker.tiles) == 2
assert picker.geometry() == w.rect()
assert not w.menuBar().isEnabled()
focused = w.focusWidget()
assert getattr(focused, "profile", None) is not None and focused.profile.id == MAIN_ID


def key(k):
    target = w.focusWidget() or picker
    QApplication.sendEvent(target, QKeyEvent(QEvent.Type.KeyPress, k, Qt.KeyboardModifier.NoModifier))
    pump(3)


key(Qt.Key.Key_Right)
assert w.focusWidget().profile.id == kid.id
key(Qt.Key.Key_Return)
pump(10)
assert profiles.active_id() == kid.id and w._profile_picker is None and w.menuBar().isEnabled()
# At start, with two profiles: asked.
w._maybe_pick_profile_at_start()
pump(5)
assert w._profile_picker is not None and w._profile_picker.isVisible()
w.enter_tv_mode()
pump(10)
assert w._profile_picker is None and w._tv_page.current == "profiles"
scr = w._tv_page.screens["profiles"]
assert [t.profile.name for t in scr.tiles] == ["Me", "Lisa"]
w._tv_page.focus_into()
w._tv_page.handle("left")
w._tv_page.handle("ok")
pump(10)
assert profiles.active_id() == MAIN_ID and w._tv_page.current == "home"
w.exit_tv_mode()
pump(5)
# A profile with a PIN at start: nothing else until it is given.
w.profiles.set_pin(MAIN_ID, "2468")
w._maybe_pick_profile_at_start()
pump(5)
assert w._profile_must_pick and not w._kids_settings_unlock()
QApplication.sendEvent(w._profile_picker, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                                    Qt.KeyboardModifier.NoModifier))
pump(3)
assert w._profile_picker is not None                    # Esc does not get past it
# Nor the phone remote, the remote API or Home Assistant asking for the
# profile in use: its PIN is asked of them too, until it is given.
try:
    w._profile_switch_remote({"id": MAIN_ID})
    raise AssertionError("switched without the PIN")
except ValueError:
    pass
assert w._profile_must_pick and w._profile_picker is not None
assert not w.menuBar().isEnabled()
profiles_ui.ask_pin = lambda *a, **k: "2468"
assert w._profile_pick(MAIN_ID)
assert not w._profile_must_pick and w._profile_picker is None
print("PROFILES_WINDOW_OK", flush=True)
os._exit(0)
"""


_CHILD_SETTINGS_OPEN = r"""
import faulthandler, json, os, tempfile
faulthandler.dump_traceback_later(200, exit=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QDialog
app = QApplication.instance() or QApplication([])
tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)

from dopeiptv import i18n
from dopeiptv.core import profiles
from dopeiptv.core.profiles import MAIN_ID, ProfileSettings
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw, theme

i18n.set_language("en")
mw.launch_player = lambda *a, **k: None
s = ProfileSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
reg = profiles.start(s, [], "Me")
kid = reg.add("Lisa", colour="pink")
mine, theirs = profiles.pinned(s, MAIN_ID), profiles.pinned(s, kid.id)
for view, lang, shelf in ((mine, "swe", "true"), (theirs, "fre", "false")):
    view.setValue("audio_lang", lang)
    view.setValue("home_sh_resume", shelf)
s.sync()
theme.apply_theme(s)
w = mw.MainWindow(DemoClient(), s, PlaylistStore(s))
w.show()
for _ in range(20):
    app.processEvents()

# The phone remote, the remote API or Home Assistant switches the profile
# while Settings is open, built for the one in use before.
real_exec = QDialog.exec
switched = []


def exec_(dlg):
    if dlg.windowTitle() == tr("settings_title") and not switched:
        switched.append(w._profile_switch_remote({"id": kid.id})["ok"])
        return 0
    return real_exec(dlg)


QDialog.exec = exec_
w.open_settings()
assert switched == [True] and profiles.active_id() == kid.id
# Closing it stores none of the other profile's own into this one.
got = (theirs.value("audio_lang"), theirs.value("home_sh_resume"))
assert got == ("fre", "false"), got
assert (mine.value("audio_lang"), mine.value("home_sh_resume")) == ("swe", "true")
print("SETTINGS_OPEN_OK", flush=True)
os._exit(0)
"""


def test_a_switch_while_settings_is_open_writes_nothing_of_one_profile_into_another():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", _CHILD_SETTINGS_OPEN], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=240)
    assert "SETTINGS_OPEN_OK" in out.stdout, out.stdout[-3000:] + out.stderr[-4000:]


def test_profiles_in_the_window():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=240)
    assert "PROFILES_WINDOW_OK" in out.stdout, out.stdout[-3000:] + out.stderr[-4000:]
