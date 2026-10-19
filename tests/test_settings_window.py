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
                       if not nav.item(i).isHidden()]
    assert len(visible()) == nav.count() >= 15
    shell.search.setText(tr("setting_sub_lang_fallback")[:-2])
    assert visible() == ["audio"], visible()
    assert shell.stack.currentWidget() is shell.pages[[p.key for p in
                                                       shell.pages].index("audio")]
    shell.search.setText("trakt")                    # a page title
    assert "trakt" in visible()
    shell.search.setText("zzzz-nothing")
    assert visible() == []
    assert shell.stack.currentWidget() is shell._none
    shell.search.setText("")
    assert len(visible()) == nav.count()
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
