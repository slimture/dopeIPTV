"""The Settings window: a sidebar of pages, cards of rows, switches.

It was rebuilt from a row of ten tabs; what it stores must not have moved by
a single key. These run the real dialog (offscreen) in a child process - the
main window owns a GL widget, see test_robustness - and check that:

- pressing OK without touching anything leaves every stored value as it was;
- the switches that replaced Yes/No lists store the same "true"/"false";
- search narrows the sidebar to the pages that have a match;
- dragging playlists reorders them, and the playlist menu follows.
"""

import os
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QDialog, QPushButton

from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui.settings_ui import ReorderList, SettingsShell, ToggleSwitch

app = QApplication.instance() or QApplication([])
s = QSettings("dopeiptv-test", "settings-window")
s.clear()
PRESET = {
    "autoplay_preview": "false", "autoplay_next_episode": "false",
    "nextup_lead_secs": "300", "auto_reconnect_live": "false",
    "hide_upcoming_prompt": "true", "stream_format": "m3u8",
    "audio_lang": "swe", "sub_mode": "lang", "sub_lang": "eng",
    "sub_lang2": "swe", "aspect_mode": "16:9", "hwdec_mode": "auto-safe",
    "video_deinterlace": "true", "video_sharpen": "1.0",
    "video_tonemapping": "hable", "cache_secs": "30",
    "live_rewind_minutes": "120", "live_rewind_disk": "true",
    "live_rewind_kept": "true", "live_rewind_space_gb": "5",
    "ui_scale": "125", "sub_scale": "150", "sub_color": "yellow",
    "sub_background": "true",
    "replay_delay_min": "-75", "epg_delay_min": "90",
    "view_density": "large", "epg_upcoming_count": "7",
    "sort_order": "alpha_desc", "check_updates": "false",
    "rec_max_value": "2.5", "rec_max_unit": "TB", "rec_total_value": "100.0",
    "rec_total_unit": "TB", "cast_pause_gb": "9.0",
    "metadata_source": "playlist", "tmdb_api_key": "abc123",
    "mv_frameless": "false", "mv_on_top": "true", "mv_remember_geo": "false",
    "mv_stop_docked": "false", "mv_new_unmuted": "false",
    "mv_conflict_mode": "keep", "mv_autohide_secs": "7",
    "mv_seek_step_min": "12", "mv_cells": "9", "home_enabled": "false",
    "home_start": "true", "home_sh_resume": "false",
    "home_sh_new_eps": "false", "home_sh_fav": "true",
    "home_sh_history": "false", "home_sh_watchlist": "true",
    "home_sh_movies": "false", "home_sh_series": "true",
    "home_sh_channels": "false", "follow_notify": "false",
    "home_sh_pop_movies": "true", "home_sh_pop_series": "false",
    "home_sh_hero": "false",
}
for k, v in PRESET.items():
    s.setValue(k, v)
s.setValue("playlists", '[{"id": "a", "name": "A", "kind": "xtream", '
           '"server": "http://a.invalid", "username": "uuuu", '
           '"password": "pppp"}, {"id": "b", "name": "B", "kind": "m3u", '
           '"server": "http://b.invalid/list.m3u"}, {"id": "c", '
           '"name": "C", "kind": "m3u", "server": "http://c.invalid/x.m3u"}]')
s.setValue("active_playlist", "a")
store = PlaylistStore(s)
w = MainWindow(DemoClient(), s, store)

def snapshot():
    return {k: str(s.value(k)) for k in s.allKeys()}

# 1. OK with nothing touched changes nothing.
before = snapshot()
QDialog.exec = lambda self: 1
w.open_settings()
after = snapshot()
diff = {k: (before[k], v) for k, v in after.items()
        if k in before and before[k] != v}
assert not diff, diff
# Written only where the platform offers them (Linux), with their defaults.
assert set(after) - set(before) <= {"force_x11", "mpris"}, (
    set(after) - set(before))

# 2. The switches store what the Yes/No lists stored.
def flip_and_ok(labels):
    def run(self):
        for sw in self.findChildren(ToggleSwitch):
            if sw.accessibleName() in labels:
                sw.setChecked(not sw.isChecked())
        return 1
    QDialog.exec = run
    w.open_settings()

from dopeiptv.i18n import tr
flip_and_ok({tr("setting_autoplay_preview"), tr("set_upcoming_prompt"),
             tr("setting_deinterlace"), tr("mv_set_titlebar"),
             tr("set_home_show")})
assert s.value("autoplay_preview") == "true"
assert s.value("hide_upcoming_prompt") == "false"   # stored inverted
assert s.value("video_deinterlace") == "false"
assert s.value("mv_frameless") == "true"            # stored inverted
assert s.value("home_enabled") == "true"

# 3. Search narrows the pages; nothing found says so.
def search_and_cancel(self):
    shell = self.findChild(SettingsShell)
    nav = shell.nav
    visible = lambda: [nav.item(i).data(0x0100) for i in range(nav.count())
                       if not nav.item(i).isHidden()
                       and nav.item(i).data(0x0100)]
    # Every page, Nerd too (its switch is on it); Nerd's four pages are
    # left out while it is off.
    shown = len(shell.pages) - 6
    assert shell._off == {"nerd_net", "nerd_boxes", "nerd_lists", "nerd_player", "nerd_rec", "nerd_serve"}, shell._off
    assert len(visible()) == shown >= 14, (visible(), shown)
    shell.search.setText(tr("setting_sub_lang_fallback")[:-2])
    assert visible() == ["audio"], visible()
    # What a search finds is shown from every page at once, in one view.
    assert shell.stack.currentWidget() is shell.results
    assert shell.search_hits() == ["audio"]
    shell.search.setText(tr("set_next_stream_note"))  # on two pages
    assert shell.search_hits() == ["playback", "audio"], shell.search_hits()
    shell.search.setText("trakt")                    # a page title
    assert "trakt" in visible() and "trakt" in shell.search_hits()
    shell.search.setText("zzzz-nothing")
    assert visible() == []
    assert shell.stack.currentWidget() is shell._none
    shell.search.setText("")
    assert len(visible()) == shown
    # Every page has its rows back.
    assert all(p.widget() is p.body for p in shell.pages)
    assert shell.stack.currentWidget() in shell.pages
    return 0
QDialog.exec = search_and_cancel
w.open_settings()

# 4. Dragging a playlist reorders the store and the playlist menu.
def reorder_and_cancel(self):
    lst = self.findChild(ReorderList)
    assert [lst.item(i).data(0x0100) for i in range(lst.count())] == \
        ["a", "b", "c"]
    item = lst.takeItem(2)                           # C to the top
    lst.insertItem(0, item)
    lst.reordered.emit()
    return 0
QDialog.exec = reorder_and_cancel
w.open_settings()
assert [p["id"] for p in store.playlists()] == ["c", "a", "b"]
assert [p["id"] for p in PlaylistStore(s).playlists()] == ["c", "a", "b"]
from PyQt6.QtWidgets import QMenu
menu = QMenu()
w._fill_playlist_menu(menu)
names = [a.text() for a in menu.actions() if a.text()]
assert names.index("C") < names.index("A") < names.index("B"), names

# 5. Reset all settings lives on the Advanced page now, not beside OK.
def reset_button_place(self):
    shell = self.findChild(SettingsShell)
    adv = shell.pages[[p.key for p in shell.pages].index("advanced")]
    assert [b for b in adv.findChildren(QPushButton)
            if b.objectName() == "Danger"]
    return 0
QDialog.exec = reset_button_place
w.open_settings()

# 6. One width for the column of controls, on every page: a dropdown with a
# long entry no longer sticks out past the ones above it.
def column_widths(self):
    self.show()
    app.processEvents()
    shell = self.findChild(SettingsShell)
    widths = set()
    for page in shell.pages:
        shell.stack.setCurrentWidget(page)
        app.processEvents()
        widths |= {c.width() for c in page.column if c.isVisible()}
    self.hide()
    assert len(widths) == 1, widths
    assert 220 <= min(widths) <= 320, widths
    return 0
QDialog.exec = column_widths
w.open_settings()

# 7. Followed series: listed by name, and Stop following stops at once.
w.followed.follow({"series_id": 2, "name": "Beta"})
w.followed.follow({"series_id": 1, "name": "alpha"})
def followed_series(self):
    from PyQt6.QtWidgets import QListWidget
    lst = self.findChild(QListWidget, "FollowedList")
    assert [lst.item(i).text() for i in range(lst.count())] == \
        ["alpha", "Beta"]
    stop = [b for b in self.findChildren(QPushButton)
            if b.text() == tr("ctx_unfollow_series")][0]
    lst.setCurrentRow(1)
    stop.click()
    assert [lst.item(i).text() for i in range(lst.count())] == ["alpha"]
    assert lst.currentRow() == 0
    return 0
QDialog.exec = followed_series
w.open_settings()
assert w.followed.is_followed(1) and not w.followed.is_followed(2)

# 8. A theme tile applies and stores its theme at once, an accent dot its
# accent; following the desktop shows the light-mode row.
def pick_look(self):
    from dopeiptv.ui.settings_ui import ColorDot, ThemeTile
    [t for t in self.findChildren(ThemeTile) if t.key == "latte"][0].click()
    [d for d in self.findChildren(ColorDot) if d.key == "mint"][0].click()
    return 0
QDialog.exec = pick_look
w.open_settings()
from dopeiptv.ui.theme import P
assert s.value("theme") == "latte" and s.value("accent") == "mint"
assert P["bg"] == "#EFF1F5" and P["accent"] == "#34CFA0", P

# 9. The sidebar reads in sections, Playlists right after General; a heading
# cannot be selected and goes with its pages in a search. The rewind
# settings, Chromecast and every cache sit where people look for them.
def layout(self):
    from PyQt6.QtCore import Qt
    shell = self.findChild(SettingsShell)
    nav = shell.nav
    rows = [(nav.item(i).data(0x0100), nav.item(i).flags())
            for i in range(nav.count())]
    keys = [k for k, _f in rows if k]
    assert keys[:4] == ["general", "playlists", "appearance", "home"], keys
    # Nerd last under System, after Advanced; its pages fold out right under
    # it (no heading of their own), left out while it is off.
    nerd_pages = ["nerd_net", "nerd_boxes", "nerd_lists", "nerd_player", "nerd_rec", "nerd_serve"]
    assert keys[-9:] == ["parental", "advanced", "nerd"] + nerd_pages, keys
    at = {k: i for i, (k, _f) in enumerate(rows) if k}
    assert not nav.item(at["nerd"]).isHidden()
    assert all(nav.item(at[k]).isHidden() for k in nerd_pages)
    heads = [i for i, (k, f) in enumerate(rows) if not k]
    assert len(heads) == 5 and heads[0] == 0, heads
    assert at["nerd_net"] == at["nerd"] + 1
    from dopeiptv.ui.settings_ui import CHILD_ROLE, PARENT_ROLE
    # Turned on: they fold out under Nerd, one after another; off again: gone.
    nerd_switch = [x for x in self.findChildren(ToggleSwitch)
                   if x.accessibleName() == tr("nerd_switch")][0]
    import time as _t
    nerd_switch.setChecked(True)
    end = _t.monotonic() + 3
    while any(nav.item(at[k]).isHidden() for k in nerd_pages) and _t.monotonic() < end:
        app.processEvents()
    assert all(not nav.item(at[k]).isHidden() for k in nerd_pages)
    assert [nav.item(at[k]).data(CHILD_ROLE) for k in nerd_pages] == [1, 1, 1, 1, 1, 2]
    assert nav.item(at["nerd"]).data(PARENT_ROLE) == 2
    shell.show_page("nerd_player")
    assert shell.current_key() == "nerd_player"
    # Each says where it belongs, and the way back is a click.
    player = next(p for p in shell.pages if p.key == "nerd_player")
    assert player.crumb is not None and player.crumb.text().startswith(tr("nerd_page"))
    player.crumb.click()
    assert shell.current_key() == "nerd"
    # Folded in with Nerd still on: the pages go, the page shown becomes
    # Nerd's own, the chevron turns; a search still finds them; out again.
    shell.show_page("nerd_net")
    shell.set_folded("nerd", True)
    assert all(nav.item(at[k]).isHidden() for k in nerd_pages)
    assert nav.item(at["nerd"]).data(PARENT_ROLE) == 1 and shell.current_key() == "nerd"
    assert s.value("nerd_folded") == "true"
    shell.search.setText("proxy")
    assert not nav.item(at["nerd_net"]).isHidden()
    shell.search.setText("")
    assert nav.item(at["nerd_net"]).isHidden()
    # A click on the chevron (the far end of Nerd's row) unfolds them.
    from PyQt6.QtCore import QPoint
    from PyQt6.QtTest import QTest
    rect = nav.visualItemRect(nav.item(at["nerd"]))
    QTest.mouseClick(nav.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(rect.right() - 10, rect.center().y()))
    end = _t.monotonic() + 3
    while any(nav.item(at[k]).isHidden() for k in nerd_pages) and _t.monotonic() < end:
        app.processEvents()
    assert all(not nav.item(at[k]).isHidden() for k in nerd_pages)
    assert nav.item(at["nerd"]).data(PARENT_ROLE) == 2 and s.value("nerd_folded") == "false"
    # The keyboard: Left folds, Right unfolds (on Nerd's row).
    shell.show_page("nerd")
    QTest.keyClick(nav, Qt.Key.Key_Left)
    assert nav.item(at["nerd"]).data(PARENT_ROLE) == 1
    QTest.keyClick(nav, Qt.Key.Key_Right)
    end = _t.monotonic() + 3
    while nav.item(at["nerd"]).data(PARENT_ROLE) != 2 and _t.monotonic() < end:
        app.processEvents()
    assert nav.item(at["nerd"]).data(PARENT_ROLE) == 2
    nerd_switch.setChecked(False)
    assert all(nav.item(at[k]).isHidden() for k in nerd_pages)
    assert nav.item(at["nerd"]).data(PARENT_ROLE) == 0
    assert all(not (rows[i][1] & Qt.ItemFlag.ItemIsSelectable) for i in heads)
    shell.show_page("playlists")
    assert shell.current_key() == "playlists"
    assert shell.stack.currentWidget().key == "playlists"
    shell.search.setText("trakt")
    shown_heads = [nav.item(i).text() for i in heads
                   if not nav.item(i).isHidden()]
    # Trakt's page, and General's privacy row (private viewing tells
    # Trakt nothing) - no other section.
    assert shown_heads == [tr("set_sec_basics").upper(), tr("set_sec_services").upper()], shown_heads
    shell.search.setText("")
    page = {p.key: p for p in shell.pages}
    from PyQt6.QtWidgets import QLabel
    def card_titles(key):
        return [l.text() for l in page[key].findChildren(QLabel)
                if l.objectName() == "SettingsCardTitle"]
    assert tr("set_card_rewind") in card_titles("playback")
    assert tr("set_card_cast") in card_titles("playback")
    assert tr("set_card_cast") not in card_titles("recording")
    assert tr("set_card_caches") in card_titles("advanced")
    assert tr("set_card_caches") not in card_titles("local")
    assert page["metadata"].title == tr("set_page_posters")
    return 0
QDialog.exec = layout
w.open_settings()

# 10. A change applies while the window is still open, a moment after it is
# made; there is one Close and no Cancel.
def live(self):
    import time as _t
    names = [b.text() for b in self.findChildren(QPushButton)
             if b.objectName() == "Primary"]
    assert tr("common_close") in names, names
    assert not [b for b in self.findChildren(QPushButton)
                if b.text() in (tr("common_cancel"), "Cancel")]
    sw = [x for x in self.findChildren(ToggleSwitch)
          if x.accessibleName() == tr("setting_deinterlace")][0]
    was = s.value("video_deinterlace")
    sw.setChecked(not sw.isChecked())
    end = _t.monotonic() + 3
    while s.value("video_deinterlace") == was and _t.monotonic() < end:
        app.processEvents()
        _t.sleep(0.02)
    assert s.value("video_deinterlace") != was
    return 0
QDialog.exec = live
w.open_settings()

print("SETTINGS_OK", flush=True)
"""


def test_settings_window(tmp_path):
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    # Followed series live in a settings file of their own in the user's
    # config directory: a fresh one, so the test follows nothing for real.
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               XDG_CONFIG_HOME=str(tmp_path))
    for _attempt in (1, 2):
        proc = subprocess.run(
            [sys.executable, "-c", _CHILD], capture_output=True, text=True,
            env=env, cwd=_REPO_ROOT, timeout=180)
        if "SETTINGS_OK" in proc.stdout:
            return
    raise AssertionError(
        f"settings checks failed\nstdout={proc.stdout!r}\n"
        f"stderr={proc.stderr[-3000:]!r}")
