"""Keyboard shortcuts of the user's own: their keys over the app's, always
(Nerd or not), never two actions on one key, the keys the app keeps
refused, only what differs stored, the old editor's keys carried over -
and the card on Settings > Advanced that records them, applied live."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from dopeiptv.core import shortcut_keys as nk

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's name
        self[key] = value

    def remove(self, key):
        self.pop(key, None)


def test_the_apps_own_keys_are_one_each_and_none_is_reserved():
    keys = [k.casefold() for k in nk.DEFAULTS.values()]
    assert len(keys) == len(set(keys))
    for sid, key in nk.DEFAULTS.items():
        assert nk.canon(key) == key, sid
        assert not nk.blocked(key, "linux") and not nk.blocked(key, "darwin"), sid


def test_the_text_form_is_qts_portable_one():
    assert nk.canon("shift+ctrl+a") == "Ctrl+Shift+A"
    assert nk.canon("Meta+Alt+Shift+Ctrl+X") == "Meta+Ctrl+Alt+Shift+X"
    assert nk.canon("escape") == "Esc" and nk.canon("Delete") == "Del"
    assert nk.canon("ctrl+right") == "Ctrl+Right" and nk.canon("f5") == "F5"
    assert nk.canon("Ctrl++") == "Ctrl++" and nk.canon("+") == "+"
    assert nk.canon("Ctrl+K, Ctrl+B") == "Ctrl+K, Ctrl+B"
    for bad in ("", "Ctrl+", "Hyper+X", None, 5):
        assert nk.canon(bad) == ""
    assert nk.same("ctrl+shift+m", "Shift+Ctrl+M") and not nk.same("", "")


def test_the_users_keys_apply_whatever_nerd_says():
    over = {"pause": "K", "mute": ""}
    eff = nk.effective(over)
    assert eff["pause"] == "K" and eff["mute"] == "" and eff["fullscreen"] == "F"
    for nerd in (None, "false", "true"):
        s = _Settings(keyboard_shortcuts=nk.dump(over))
        if nerd is not None:
            s["nerd_enabled"] = nerd
        assert nk.from_settings(s) == eff, nerd
    assert nk.from_settings(_Settings()) == nk.DEFAULTS
    assert nk.from_settings(None) == nk.DEFAULTS


def test_the_old_editors_keys_are_carried_over_once():
    s = _Settings({"shortcut/linux/pause": "K", "shortcut/linux/stats": "Ctrl+B",
                   "shortcut/darwin/mute": "J"})
    assert nk.migrate(s, "linux") == 2
    assert "shortcut/linux/pause" not in s and "shortcut/linux/stats" not in s
    assert s["shortcut/darwin/mute"] == "J"                 # another system's, left alone
    # The old editor never checked for clashes: Ctrl+B was the sidebar's too.
    assert json.loads(s["keyboard_shortcuts"]) == {"pause": "K", "stats": "Ctrl+B", "sidebar": ""}
    assert nk.migrate(s, "linux") == 0                       # once
    # A key set since wins over an old one.
    s = _Settings({"shortcut/linux/stats": "F9", "keyboard_shortcuts": json.dumps({"mute": "F9"})})
    nk.migrate(s, "linux")
    eff = nk.from_settings(s)
    assert eff["mute"] == "F9" and eff["stats"] == ""
    assert nk.migrate(_Settings(), "linux") == 0


def test_an_old_key_the_app_keeps_now_goes_back_to_the_default_with_a_word():
    import logging

    from dopeiptv.core.log import log
    seen: list[str] = []

    class Catch(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())
    h = Catch()
    level = log.level
    log.setLevel(logging.INFO)
    log.addHandler(h)
    try:
        s = _Settings({"shortcut/linux/mute": "Esc", "shortcut/linux/pause": "K"})
        assert nk.migrate(s, "linux") == 2
    finally:
        log.removeHandler(h)
        log.setLevel(level)
    eff = nk.from_settings(s)
    assert eff["mute"] == nk.DEFAULTS["mute"] and eff["pause"] == "K"
    said = [m for m in seen if m.startswith("shortcuts:")]
    assert len(said) == 1 and "mute (Esc)" in said[0] and "pause" not in said[0], said
    # Nothing lost: nothing said.
    seen.clear()
    log.addHandler(h)
    try:
        nk.migrate(_Settings({"shortcut/linux/pause": "K"}), "linux")
    finally:
        log.removeHandler(h)
    assert not [m for m in seen if m.startswith("shortcuts:")]


def test_a_key_given_to_another_action_leaves_the_first_without():
    over, lost = nk.assign({}, "pause", "M")
    assert lost == ["mute"]
    eff = nk.effective(over)
    assert eff["pause"] == "M" and eff["mute"] == ""
    assert over == {"pause": "M", "mute": ""}
    # Its own key back: taken from whoever has it now, said again.
    over, lost = nk.assign(over, "mute", "M")
    assert lost == ["pause"]
    eff = nk.effective(over)
    assert eff["mute"] == "M" and eff["pause"] == ""
    # The app's own key again: nothing stored for it.
    over, lost = nk.assign(over, "pause", "Space")
    assert lost == [] and over == {}
    # However it is spelled.
    over, lost = nk.assign({}, "sidebar", "shift+ctrl+m")
    assert lost == ["focus_mode"] and over == {"focus_mode": "", "sidebar": "Ctrl+Shift+M"}


def test_no_key_and_back_again():
    over, lost = nk.assign({}, "stats", "")
    assert lost == [] and nk.effective(over)["stats"] == "" and over == {"stats": ""}
    over, lost = nk.assign(over, "stats", nk.DEFAULTS["stats"])
    assert over == {} and lost == []


def test_never_two_actions_on_one_key_whatever_is_stored():
    # Written by hand, or an older version's keys: two on one key, a key the
    # app keeps, one taking a default from an action that kept its own.
    over = nk.parse(json.dumps({"mute": "Space", "popout": "Space", "record": "Esc",
                                "stats": "Ctrl+B", "nope": "X", "fullscreen": 3}))
    assert "nope" not in over and "fullscreen" not in over
    eff = nk.effective(over)
    taken = [k.casefold() for k in eff.values() if k]
    assert len(taken) == len(set(taken)), eff
    assert eff["mute"] == "Space" and eff["pause"] == ""          # the user's key wins
    assert eff["popout"] == "P"                                    # a second claim: its own key
    assert eff["record"] == "R"                                    # Esc is the app's
    assert eff["stats"] == "Ctrl+B" and eff["sidebar"] == ""
    # Tidied, the same keys with the fewest overrides.
    assert nk.effective(nk.tidy(over)) == eff
    assert nk.tidy(over) == {"mute": "Space", "pause": "", "stats": "Ctrl+B", "sidebar": ""}


@pytest.mark.parametrize("key,why", [
    ("Esc", "sc_why_escape"), ("Delete", "sc_why_delete"), ("5", "sc_why_digits"),
    ("Shift+5", "sc_why_digits"), ("Num+5", "sc_why_digits"), ("Return", "sc_why_enter"),
    ("Enter", "sc_why_enter"), ("Left", "sc_why_arrows"), ("Shift+Right", "sc_why_arrows"),
    ("Up", "sc_why_arrows"), ("Tab", "sc_why_tab"), ("Shift+Backtab", "sc_why_tab"),
    ("Media Play", "sc_why_media"), ("Toggle Media Play/Pause", "sc_why_media"),
    ("Ctrl+O", "sc_why_menu"), ("Ctrl+Shift+N", "sc_why_menu"), ("Ctrl+Shift+Delete", "sc_why_menu"),
])
def test_the_keys_the_app_keeps_cannot_be_taken(key, why):
    r = nk.reserved(key, "linux")
    assert r is not None and r.level == nk.BLOCKED and r.why == why
    over, lost = nk.assign({}, "pause", key)
    assert over == {} and lost == []
    assert nk.effective({"pause": key})["pause"] == "Space"


def test_risky_keys_work_with_a_warning_as_the_system_has_them():
    assert nk.reserved("Ctrl+C", "linux") == nk.Reserved(nk.WARN, "sc_warn_edit")
    assert nk.reserved("Alt+F4", "linux").why == "sc_warn_system"
    assert nk.reserved("Meta+L", "linux").why == "sc_warn_system"
    # On a Mac, Qt's Meta is the Control key and Ctrl is ⌘: ⌘Q quits there.
    assert nk.reserved("Meta+L", "darwin") is None
    assert nk.reserved("Ctrl+Q", "darwin").why == "sc_warn_system"
    assert nk.reserved("Ctrl+Q", "linux") is None
    assert nk.reserved("Ctrl+K", "linux") is None
    over, _lost = nk.assign({}, "pause", "Alt+F4", platform="linux")
    assert nk.effective(over, platform="linux")["pause"] == "Alt+F4"


def test_the_app_menus_keys_are_the_ones_the_window_sets():
    from dopeiptv.ui import mw_privacy
    for key in (mw_privacy.PRIVATE_SHORTCUT, mw_privacy.CLEAR_SHORTCUT):
        assert any(nk.same(key, k) for k in nk.APP_KEYS), key
    src = open(os.path.join(_REPO_ROOT, "dopeiptv", "ui", "main_window.py"), encoding="utf-8").read()
    assert 'open_video_action.setShortcut("Ctrl+O")' in src


def test_only_what_differs_is_stored_and_read_back_the_same():
    assert nk.dump({}) == "" and nk.dump({"pause": "Space"}) == ""
    text = nk.dump({"pause": "k", "sidebar": "ctrl+shift+b"})
    assert json.loads(text) == {"pause": "K", "sidebar": "Ctrl+Shift+B"}
    assert nk.dump(nk.parse(text)) == text
    for bad in ("", "not json", "[1, 2]", "null", None):
        assert nk.parse(bad) == {}


def test_every_action_and_reason_has_its_words():
    from dopeiptv import i18n
    for _sid, _key, label in nk.ACTIONS:
        assert label in i18n._STRINGS
    for r in {nk.reserved(k, p) for k in ("Esc", "Del", "1", "Enter", "Left", "Tab", "Media Play",
                                          "Ctrl+O", "Ctrl+C", "Alt+F4") for p in ("linux",)}:
        assert r is not None and r.why in i18n._STRINGS
        assert not r.name or r.name in i18n._STRINGS
    # The card is everyone's: its words do not send anyone to Nerd.
    for key, entry in i18n._STRINGS.items():
        if key.startswith("sc_"):
            assert "Nerd" not in entry["en"], key


# -- the card ------------------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _editor(stored: str = ""):
    from PyQt6.QtWidgets import QDialog

    from dopeiptv.i18n import tr
    from dopeiptv.ui.settings_ui import SettingsPage
    from dopeiptv.ui.shortcuts import ShortcutEditor
    d = QDialog()
    page = SettingsPage("advanced", "Advanced", "")
    ed = ShortcutEditor(page.card(tr("sc_title")), stored, d)
    return d, page, ed


def test_the_card_records_a_key_and_says_plainly_who_lost_it(app):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest

    from dopeiptv.i18n import tr
    _d, _page, ed = _editor()
    said = []
    ed.changed.connect(lambda: said.append(1))
    assert ed.text() == ""                            # an untouched card has nothing to store
    pause = ed.keys["pause"]
    assert pause.sequence() == "Space" and not ed.resets["pause"].isEnabled()
    pause.click()
    assert pause.recording and pause.text() == tr("sc_press")
    QTest.keyClick(pause, Qt.Key.Key_M)
    assert not pause.recording and said
    assert pause.sequence() == "M" and ed.keys["mute"].sequence() == ""
    assert ed.keys["mute"].text() == tr("sc_none") and not ed.clears["mute"].isEnabled()
    assert ed.lines["pause"].text() == tr("sc_taken", shortcut="M", other=tr("sc_mute"))
    assert ed.lines["mute"].text() == tr("sc_lost", shortcut="M", other=tr("sc_play_pause"))
    assert json.loads(ed.text()) == {"pause": "M", "mute": ""}
    # Esc gives up: the key stays.
    pause.click()
    QTest.keyClick(pause, Qt.Key.Key_Escape)
    assert not pause.recording and pause.sequence() == "M"
    # Modifiers as pressed; the action that had the combination loses it.
    side = ed.keys["sidebar"]
    side.click()
    QTest.keyClick(side, Qt.Key.Key_Control, Qt.KeyboardModifier.ControlModifier)
    assert side.recording                              # a modifier alone: waits for the key
    QTest.keyClick(side, Qt.Key.Key_M, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert side.sequence() == "Ctrl+Shift+M" and ed.keys["focus_mode"].sequence() == ""
    # The row that lost its key still says where it went.
    assert ed.lines["mute"].text() == tr("sc_lost", shortcut="M", other=tr("sc_play_pause"))
    # No key, its own back, all back.
    ed.clears["stats"].click()
    assert ed.keys["stats"].sequence() == "" and ed.resets["stats"].isEnabled()
    ed.resets["stats"].click()
    assert ed.keys["stats"].sequence() == "I" and ed.lines["stats"].isHidden()
    ed.reset_all_btn.click()
    assert ed.text() == "" and pause.sequence() == "Space" and side.sequence() == "Ctrl+B"


def test_the_card_refuses_the_apps_own_keys_and_warns_of_risky_ones(app):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest

    from dopeiptv.i18n import tr
    _d, _page, ed = _editor(json.dumps({"popout": "Ctrl+J"}))
    assert ed.keys["popout"].sequence() == "Ctrl+J" and ed.resets["popout"].isEnabled()
    rec = ed.keys["record"]
    for key, mods, why in ((Qt.Key.Key_5, Qt.KeyboardModifier.NoModifier, tr("sc_why_digits")),
                           (Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier, tr("sc_why_delete")),
                           (Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier, tr("sc_why_tab")),
                           (Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier,
                            tr("sc_why_menu", name=tr("menu_open_video").rstrip("…")))):
        rec.click()
        QTest.keyClick(rec, key, mods)
        assert rec.sequence() == "R", key
        assert ed.lines["record"].text().endswith(why + "."), ed.lines["record"].text()
    assert json.loads(ed.text()) == {"popout": "Ctrl+J"}            # nothing changed
    rec.click()
    QTest.keyClick(rec, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert rec.sequence() == "Ctrl+C"
    assert tr("sc_warn_edit") in ed.lines["record"].text()


def test_the_card_is_found_by_its_name(app):
    from dopeiptv.i18n import tr
    _d, page, _ed = _editor()
    assert page.apply_filter(tr("sc_title").casefold())
    shown = [w for c in page.cards for w, _t in c.rows if not w.isHidden()]
    assert len(shown) >= len(nk.ACTIONS)


def test_a_tooltip_names_the_key_in_force(app):
    from dopeiptv.ui.shortcuts import tip_with_key
    if sys.platform != "darwin":
        assert tip_with_key("Collapse (Ctrl+B)", "Ctrl+B", "Ctrl+B") == "Collapse (Ctrl+B)"
    assert tip_with_key("Collapse (Strg+B)", "Ctrl+B", "F9") == "Collapse (F9)"
    assert tip_with_key("折りたたむ（Ctrl+B）", "Ctrl+B", "F9") == "折りたたむ（F9）"
    assert tip_with_key("Collapse (Ctrl+B)", "Ctrl+B", "") == "Collapse"


# -- the window: a key of the user's does the action, the old one no longer --

_WINDOW = r"""
import os, sys, json, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog
app = QApplication.instance() or QApplication([])
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from dopeiptv.core import nerd
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui.settings_ui import SettingsPage, ToggleSwitch
from dopeiptv.ui.shortcuts import KeyButton

s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
# Nerd off, as for most people: the user's keys apply all the same.
s.setValue("nerd_enabled", "false")
s.setValue("keyboard_shortcuts", json.dumps({"sidebar": "F9"}))
# A key of the old editor, from before this version: carried over.
s.setValue("shortcut/" + sys.platform + "/stats", "Ctrl+J")
nerd.install(s)
w = MainWindow(DemoClient(), s, PlaylistStore(s))
w.show()
w.activateWindow()
QTest.qWaitForWindowActive(w, 3000)
assert QApplication.activeWindow() is w, QApplication.activeWindow()
# Not in a text field, which keeps some keys for itself while it is typed in.
w.listw.setFocus()
app.processEvents()
assert s.value("shortcut/" + sys.platform + "/stats") is None
assert json.loads(s.value("keyboard_shortcuts")) == {"sidebar": "F9", "stats": "Ctrl+J"}, s.value("keyboard_shortcuts")
fired = []
w._shortcuts["sidebar"].activated.connect(lambda: fired.append("sidebar"))
CTRL, NONE = Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.NoModifier

def toggles(key, mods):
    fired.clear()
    was = w.side_btn.isChecked()
    QTest.keyClick(w, key, mods)
    app.processEvents()
    assert bool(fired) == (w.side_btn.isChecked() != was), (fired, was)
    return bool(fired)

def users_keys():
    assert w._shortcuts["sidebar"].key() == QKeySequence("F9")
    assert w._shortcuts["stats"].key() == QKeySequence("Ctrl+J")
    assert toggles(Qt.Key.Key_F9, NONE), "the user's key does the action"
    assert not toggles(Qt.Key.Key_B, CTRL), "the old key does nothing"

users_keys()
# The tooltip that names the key says the one in force.
tip = w.side_btn.toolTip()
assert "F9" in tip and "B)" not in tip, tip
# Nerd on, then off again: the keys do not move.
for on in ("true", "false"):
    s.setValue("nerd_enabled", on)
    nerd.install(s)
    w.apply_shortcuts()
    users_keys()

# From Settings: the card is on the Advanced page; a key recorded there is
# in force a moment later, while Settings is still open; Nerd's switch
# changes no key.
def wait(cond):
    end = time.monotonic() + 3
    while not cond() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    return cond()

def page_of(widget):
    p = widget.parentWidget()
    while p is not None and not isinstance(p, SettingsPage):
        p = p.parentWidget()
    return p

def in_settings(self):
    btn = next(b for b in self.findChildren(KeyButton) if b.sid == "sidebar")
    assert page_of(btn).key == "advanced", page_of(btn).key
    assert {page_of(b).key for b in self.findChildren(KeyButton)} == {"advanced"}
    assert btn.sequence() == "F9"
    btn.click()
    QTest.keyClick(btn, Qt.Key.Key_F8)
    assert wait(lambda: w._shortcuts["sidebar"].key() == QKeySequence("F8")), "applied live"
    assert json.loads(s.value("keyboard_shortcuts"))["sidebar"] == "F8"
    switch = next(x for x in self.findChildren(ToggleSwitch) if x.accessibleName() == tr("nerd_switch"))
    switch.setChecked(not switch.isChecked())
    assert wait(lambda: s.value("nerd_enabled") == "true")
    switch.setChecked(not switch.isChecked())
    assert wait(lambda: s.value("nerd_enabled") == "false")
    assert w._shortcuts["sidebar"].key() == QKeySequence("F8"), "Nerd changes no key"
    return 0
QDialog.exec = in_settings
w.open_settings()
assert w._shortcuts["sidebar"].key() == QKeySequence("F8")
# Settings gone, the window is the active one again (offscreen leaves none).
w.activateWindow()
QTest.qWaitForWindowActive(w, 3000)
w.listw.setFocus()
app.processEvents()
assert toggles(Qt.Key.Key_F8, NONE) and not toggles(Qt.Key.Key_F9, NONE)

# Opening Settings and changing nothing stores nothing for keys never set.
s.remove("keyboard_shortcuts")
w.apply_shortcuts()
QDialog.exec = lambda self: 0
w.open_settings()
assert s.value("keyboard_shortcuts") is None, s.value("keyboard_shortcuts")
assert w._shortcuts["sidebar"].key() == QKeySequence("Ctrl+B")
w.hide()
print("SHORTCUTS_WINDOW_OK")
"""


def test_the_window_takes_the_users_key_with_nerd_on_or_off():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    out = subprocess.run([sys.executable, "-c", _WINDOW], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=120)
    assert "SHORTCUTS_WINDOW_OK" in out.stdout, out.stdout + out.stderr[-3000:]
