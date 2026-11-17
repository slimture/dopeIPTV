"""The Settings window: a sidebar of pages, cards of rows, switches.

It was rebuilt from a row of ten tabs; what it stores must not have moved by
a single key. These run the real dialog (offscreen) in a child process - the
main window owns a GL widget, see test_robustness - and check that:

- pressing OK without touching anything leaves every stored value as it was;
- the switches that replaced Yes/No lists store the same "true"/"false";
- search narrows the sidebar to the pages that have a match;
- dragging playlists reorders them, and the playlist menu follows.

Each page is built the first time it is needed (shown, searched, indexed):
the checks that walk every page build them all first (``built``).
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
    "show_stream_info": "true",
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
    "rec_total_unit": "TB", "cast_pause_gb": "9.0", "cast_keep_buffer": "false",
    "metadata_source": "playlist", "tmdb_api_key": "abc123",
    "mv_frameless": "false", "mv_on_top": "true", "mv_remember_geo": "false",
    "mv_stop_docked": "false", "mv_new_unmuted": "false",
    "mv_conflict_mode": "keep", "mv_autohide_secs": "7",
    "mv_seek_step_min": "12", "mv_cells": "9", "start_place": "mitt",
    "follow_notify": "false", "popular_movies": "true", "popular_series": "false",
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

def built(dlg):
    shell = dlg.findChild(SettingsShell)
    shell.build_all()
    return shell

# 1. OK with nothing touched changes nothing - every page built.
before = snapshot()
QDialog.exec = lambda self: built(self) and 1
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
        built(self)
        for sw in self.findChildren(ToggleSwitch):
            if sw.accessibleName() in labels:
                sw.setChecked(not sw.isChecked())
        return 1
    QDialog.exec = run
    w.open_settings()

from dopeiptv.i18n import tr
flip_and_ok({tr("setting_autoplay_preview"), tr("set_upcoming_prompt"),
             tr("setting_deinterlace"), tr("mv_set_titlebar"),
             tr("set_follow_notify")})
assert s.value("autoplay_preview") == "true"
assert s.value("hide_upcoming_prompt") == "false"   # stored inverted
assert s.value("video_deinterlace") == "false"
assert s.value("mv_frameless") == "true"            # stored inverted
assert s.value("follow_notify") == "true"
assert s.value("start_place") == "mitt"             # kept as it was

# 3. Search narrows the pages; nothing found says so.
def search_and_cancel(self):
    shell = self.findChild(SettingsShell)
    nav = shell.nav
    visible = lambda: [nav.item(i).data(0x0100) for i in range(nav.count())
                       if not nav.item(i).isHidden()
                       and nav.item(i).data(0x0100)]
    # Every page, Nerd's Status too (its switch is on it); Nerd's five other
    # pages are left out while it is off.
    shown = len(shell.pages) - 5
    assert shell._off == {"nerd_net", "nerd_boxes", "nerd_player", "nerd_serve", "nerd_diag"}, shell._off
    assert len(visible()) == shown >= 18, (visible(), shown)
    shell.search.setText(tr("setting_sub_lang_fallback")[:-2])
    assert visible() == ["audio"], visible()
    # What a search finds is shown from every page at once, in one view.
    assert shell.stack.currentWidget() is shell.results
    assert shell.search_hits() == ["audio"]
    shell.search.setText(tr("set_next_stream_note"))  # on two pages
    assert shell.search_hits() == ["playback", "audio"], shell.search_hits()
    shell.search.setText("trakt")                    # a section's heading
    assert "history" in visible() and "history" in shell.search_hits()
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
    built(self)
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
    shell = built(self)
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
    shell = built(self)
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
    built(self)
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
    built(self)
    [t for t in self.findChildren(ThemeTile) if t.key == "latte"][0].click()
    [d for d in self.findChildren(ColorDot) if d.key == "mint"][0].click()
    return 0
QDialog.exec = pick_look
w.open_settings()
from dopeiptv.ui.theme import P
assert s.value("theme") == "latte" and s.value("accent") == "mint"
assert P["bg"] == "#EFF1F5" and P["accent"] == "#34CFA0", P

# 9. The sidebar reads in groups - Watch, Content, Services,
# System, Nerd; a heading cannot be selected and goes with its pages in a
# search. The rewind settings, casting and every cache sit where people look
# for them; Nerd's pages come out under NERD, in its colour, while it is on.
def layout(self):
    from PyQt6.QtCore import Qt
    shell = built(self)
    nav = shell.nav
    rows = [(nav.item(i).data(0x0100), nav.item(i).flags())
            for i in range(nav.count())]
    keys = [k for k, _f in rows if k]
    assert keys[:6] == ["playback", "audio", "guide", "multiview", "recording", "casting"], keys
    assert keys[6:10] == ["playlists", "mine", "local", "metadata"], keys
    nerd_pages = ["nerd_net", "nerd_boxes", "nerd_player", "nerd_serve", "nerd_diag"]
    assert keys[-6:] == ["nerd"] + nerd_pages, keys
    at = {k: i for i, (k, _f) in enumerate(rows) if k}
    assert not nav.item(at["nerd"]).isHidden()
    assert all(nav.item(at[k]).isHidden() for k in nerd_pages)
    heads = [i for i, (k, f) in enumerate(rows) if not k]
    assert len(heads) == 5 and heads[0] == 0, heads
    # NERD heads Status, and Nerd's pages carry its colour.
    from dopeiptv.ui.settings_ui import GROUP_ROLE
    from dopeiptv.ui.theme import P as _P
    assert heads[-1] == at["nerd"] - 1 and nav.item(heads[-1]).text() == tr("nerd_page").upper()
    tint = _P.get("nerd", _P["accent"])
    assert {nav.item(at[k]).data(GROUP_ROLE) for k in ["nerd"] + nerd_pages} == {tint}
    assert nav.item(heads[-1]).data(GROUP_ROLE) == tint
    assert nav.item(at["playback"]).data(GROUP_ROLE) is None
    page = {p.key: p for p in shell.pages}
    # Turned on: they come out under it, one after another; off again: gone.
    nerd_switch = [x for x in self.findChildren(ToggleSwitch)
                   if x.accessibleName() == tr("nerd_switch")][0]
    link = next(b for b in page["playback"].findChildren(QPushButton)
                if b.objectName() == "SettingsLink")
    assert not link.isVisibleTo(page["playback"])     # Nerd off: no link row
    import time as _t
    nerd_switch.setChecked(True)
    end = _t.monotonic() + 3
    while any(nav.item(at[k]).isHidden() for k in nerd_pages) and _t.monotonic() < end:
        app.processEvents()
    assert all(not nav.item(at[k]).isHidden() for k in nerd_pages)
    assert link.isVisibleTo(page["playback"])
    # The link row leads to the page its settings are on.
    shell.show_page("playback")
    link.click()
    assert shell.current_key() == "nerd_player", shell.current_key()
    # Each of Nerd's pages says where it belongs, and the way back is a click.
    player = page["nerd_player"]
    assert player.crumb is not None and player.crumb.text().startswith(tr("nerd_page"))
    player.crumb.click()
    assert shell.current_key() == "nerd"
    shell.show_page("nerd_net")
    nerd_switch.setChecked(False)
    assert all(nav.item(at[k]).isHidden() for k in nerd_pages)
    assert not link.isVisibleTo(page["playback"])
    assert shell.current_key() != "nerd_net"
    assert all(not (rows[i][1] & Qt.ItemFlag.ItemIsSelectable) for i in heads)
    shell.show_page("playlists")
    assert shell.current_key() == "playlists"
    assert shell.stack.currentWidget().key == "playlists"
    assert page["playlists"].title == tr("set_page_sources")
    shell.search.setText("trakt")
    shown_heads = [nav.item(i).text() for i in heads
                   if not nav.item(i).isHidden()]
    # Watch history's Trakt card, and General's privacy row (private
    # viewing tells Trakt nothing) - no other group.
    assert shown_heads == [tr("set_grp_accounts").upper(), tr("set_sec_system").upper()], shown_heads
    # Found, each page says where it is.
    hist = page["history"]
    assert hist.path is not None and hist.path.isVisibleTo(shell.results)
    assert hist.path.text().startswith(tr("set_grp_accounts"))
    shell.search.setText("")
    assert not hist.path.isVisibleTo(hist)
    from PyQt6.QtWidgets import QLabel
    def card_titles(key):
        return [l.text() for l in page[key].findChildren(QLabel)
                if l.objectName() == "SettingsCardTitle"]
    assert tr("set_card_rewind") in card_titles("playback")
    assert tr("set_card_cast") in card_titles("casting")
    assert tr("set_card_cast") not in card_titles("playback") + card_titles("recording")
    assert tr("set_card_caches") in card_titles("advanced")
    assert tr("set_card_caches") not in card_titles("local")
    assert "OpenSubtitles.com" in card_titles("integrations")
    assert "OpenSubtitles.com" not in card_titles("audio")
    assert tr("tab_trakt") in card_titles("history")
    assert page["metadata"].title == tr("set_page_posters")
    assert page["sync"].title == tr("sync_title")
    # Its sidebar entry is short: the whole title was cut off there.
    names = {nav.item(i).data(256): nav.item(i).text() for i in range(nav.count())}
    assert names["sync"] == tr("sync_nav") != page["sync"].title
    assert names["metadata"] == page["metadata"].title
    assert tr("sync_privacy_title") in card_titles("sync")
    return 0
QDialog.exec = layout
w.open_settings()

# 10. A change applies while the window is still open, a moment after it is
# made; there is one Close and no Cancel.
def live(self):
    import time as _t
    built(self)
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
