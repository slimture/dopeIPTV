"""Settings' CONTENT pages: Sources, Mine, Local files and Posters and
info (their rows and keys: docs/SETTINGS_MAP.md).

Each ``build_<page>(ctx)`` fills its page the first time it is needed
(SettingsShell.ensure_built), never as the window opens, and leaves what
the page stores and applies, if anything, as
``ctx.page_commits[<page key>]``: commit(put, changed, final), run by
open_settings' commit. *ctx* is the Settings window's SettingsContext
(ui/mw_settings.py). A source's own options are on its own page
(ui/source_page.py), which Sources opens through ``ctx.edit_source``.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton,
)

from ..core import mirrors
from ..core import places as _places
from ..core.workers import run_async
from ..i18n import tr
from ..providers.metadata import TmdbClient, bundled_tmdb_key
from .settings_ui import (
    Chip, ChipGroups, PlaylistDelegate, ReorderList, fit_to_rows, hbox,
    page_icon,
)
from .source_page import KIND_NAMES
from .theme import P
from .widgets import confirm, file_dialog_options

if TYPE_CHECKING:
    from .mw_settings import SettingsContext


def build_sources(ctx: SettingsContext) -> None:
    """Sources (the playlists): the list in its order, the one selected's
    account, and the rows that lead to Nerd's network and lists."""
    win, d, playlists = ctx.win, ctx.dialog, ctx.pages["playlists"]
    nerd_link, shared, store = ctx.nerd_link, ctx.shared, ctx.store
    account_of, edit_source = ctx.account_of, ctx.edit_source
    add_playlist = ctx.add_playlist
    pl_list = ReorderList()
    pl_list.setObjectName("PlaylistList")
    pl_list.setAccessibleName(tr("tab_playlists"))
    pl_list.setItemDelegate(PlaylistDelegate(tr("pl_in_use"), KIND_NAMES, pl_list))
    pl_list.setMouseTracking(True)
    # Each row opens its source's page: a click on its › (or a
    # double click, or Edit).
    pl_list.open_zone = 36
    # Drag a playlist to change its place; the sidebar's playlist
    # menu lists them in this same order.
    pl_list.setStyleSheet(
        "QListWidget#PlaylistList { background: transparent; }"
        "QListWidget#PlaylistList::item { padding: 0; margin: 0; }")
    add_btn = QPushButton(tr("btn_add"))
    edit_btn = QPushButton(tr("btn_edit"))
    remove_btn = QPushButton(tr("btn_remove"))
    use_btn = QPushButton(tr("btn_use"), objectName="Primary")
    c = pl_card = playlists.card()
    pl_row = c.widget(pl_list, tr("tab_playlists"))
    # As tall as its playlists: a fixed 200 px left one playlist
    # above an empty well.
    fit_to_rows(pl_list, most=6)
    c.layout_row(hbox(add_btn, edit_btn, remove_btn, None, use_btn))
    c.note(tr("pl_drag_hint"))
    # Account status for the selected Xtream provider (expiry,
    # connections). Follows the list selection so you can check any
    # provider without switching to it.
    # Refresh reloads the playlist in use, so it is offered while
    # that one is selected.
    refresh_pl_btn = QPushButton(tr("btn_refresh"))
    refresh_pl_btn.setToolTip(tr("tooltip_reload_channels_epg"))
    c = playlists.card(tr("set_card_selected_playlist"))
    account_lbl = c.row(tr("set_account"), refresh_pl_btn,
                        tr("account_loading")).hint_label
    account_lbl.setTextFormat(Qt.TextFormat.PlainText)    # the provider's words
    # How the app reaches every source, and the boxes and channel
    # lists: Nerd's, a row each here, where the sources are.
    c = playlists.card()
    nerd_link(c, "set_link_net",
              [tr("nerd_card_identity"), tr("nerd_card_proxy"),
               tr("nerd_card_conn"), tr("nerd_card_dns"),
               tr("nerd_card_vpn"), tr("nerd_card_lan")], "net")
    nerd_link(c, "set_link_lists",
              [tr("nerd_card_boxes"), tr("nerd_card_picons"),
               tr("nerd_card_udpxy"), tr("nerd_card_failover"),
               tr("nerd_card_chno")], "boxes")

    def reload_pl_list():
        pl_list.clear()
        # The sidebar switcher chip appears/disappears with the
        # second playlist - reflect adds/removes right away.
        win._update_playlist_btn()
        if not store:
            item = QListWidgetItem(tr("pl_mgmt_unavailable"))
            item.setData(PlaylistDelegate.ROLE,
                         {"name": tr("pl_mgmt_unavailable")})
            pl_list.addItem(item)
            pl_list.setDragDropMode(
                QAbstractItemView.DragDropMode.NoDragDrop)
            return
        words = [tr("tab_playlists")]
        for p in store.playlists():
            item = QListWidgetItem(p.get("name", ""))
            item.setData(Qt.ItemDataRole.UserRole, p["id"])
            item.setData(PlaylistDelegate.ROLE, {
                "name": p.get("name", ""),
                "server": p.get("server", ""),
                "kind": p.get("kind") or "xtream",
                "active": p["id"] == store.active_id,
                "opens": True})
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
            pl_list.addItem(item)
            # Found by its name, its kind and where it is (never a
            # path or query: an M3U address may carry a login).
            server = str(p.get("server") or "")
            words += [str(p.get("name") or ""), KIND_NAMES.get(p.get("kind") or "xtream", ""),
                      mirrors.shown(server) if "://" in server else server]
        pl_card.set_search(pl_row, " ".join(words))

    def selected_pid():
        item = pl_list.currentItem()
        return (item.data(Qt.ItemDataRole.UserRole)
                if item else None)

    def select_pid(pid) -> None:
        for i in range(pl_list.count()):
            if pl_list.item(i).data(Qt.ItemDataRole.UserRole) == pid:
                pl_list.setCurrentRow(i)
                return

    def refresh_account(pid):
        pl = store.get(pid) if (store and pid) else None
        has = pl is not None
        for b in (edit_btn, remove_btn):
            b.setEnabled(has)
        use_btn.setEnabled(has and pid != store.active_id)
        refresh_pl_btn.setEnabled(has and pid == store.active_id)
        if not pl:
            account_lbl.setText(tr("account_unavailable"))
            return
        if (pl.get("kind") or "xtream") == "m3u":
            account_lbl.setText(tr("account_unavailable"))
            return
        account_lbl.setText(tr("account_loading"))

        def done(info, pid=pid):
            # Asked as the page is built: the answer can come after
            # the window went (deleted a while after it closed).
            if sip.isdeleted(pl_list) or sip.isdeleted(account_lbl):
                return
            # Ignore a stale answer if the selection moved on
            # meanwhile.
            if selected_pid() == pid:
                account_lbl.setText(win._format_account(info))

        account_of(pl, done)

    pl_list.currentItemChanged.connect(
        lambda *_: refresh_account(selected_pid()))

    def playlist_moved(*_a) -> None:
        if not store:
            return
        order = [pl_list.item(i).data(Qt.ItemDataRole.UserRole)
                 for i in range(pl_list.count())]
        store.reorder([pid for pid in order if pid])
        win._update_playlist_btn()

    pl_list.reordered.connect(playlist_moved)

    def edit_playlist():
        edit_source(selected_pid())

    def remove_playlist():
        pid = selected_pid()
        if not (store and pid):
            return
        if confirm(d, tr("msg_remove_playlist_title"),
                   tr("msg_remove_playlist_body")):
            was_active = pid == store.active_id
            store.remove(pid)
            reload_pl_list()
            # The app went on showing the removed provider until the
            # next start: move to the one that is active now.
            if was_active and store.active_id:
                win.switch_playlist(store.active_id)
                reload_pl_list()
            select_pid(store.active_id)

    def use_playlist():
        pid = selected_pid()
        if store and pid and pid != store.active_id:
            win.switch_playlist(pid)
            reload_pl_list()
            select_pid(pid)

    shared["sources"] = {"reload": reload_pl_list, "select": select_pid}
    add_btn.clicked.connect(lambda _c=False: add_playlist())
    edit_btn.clicked.connect(edit_playlist)
    remove_btn.clicked.connect(remove_playlist)
    refresh_pl_btn.clicked.connect(win.refresh_playlist)
    use_btn.clicked.connect(use_playlist)
    pl_list.itemDoubleClicked.connect(lambda _i: edit_playlist())
    pl_list.open_requested.connect(
        lambda item: edit_source(item.data(Qt.ItemDataRole.UserRole)))
    if not store:
        for b in (add_btn, edit_btn, remove_btn, refresh_pl_btn, use_btn):
            b.setEnabled(False)
    reload_pl_list()
    # Start on the active provider so its account status shows first
    # (selecting a row drives refresh_account via currentItemChanged).
    if store:
        if pl_list.count():
            select_pid(store.active_id)
            if pl_list.currentRow() < 0:
                pl_list.setCurrentRow(0)
        else:
            refresh_account(None)


def build_mine(ctx: SettingsContext) -> None:
    """Mine: the series followed and whether their new episodes are said."""
    win, mine = ctx.win, ctx.pages["mine"]
    is_on, flag = ctx.is_on, ctx.flag
    # Following a series: whether new episodes are announced (Mine
    # and the episode dots show them either way), and the
    # series this playlist follows, to stop following one without
    # finding it again.
    followed_card = mine.card(tr("set_card_followed"))
    follow_notify_box = followed_card.toggle(
        tr("set_follow_notify"), is_on("follow_notify", "true"),
        tr("set_follow_notify_hint", shelf=tr("home_new_episodes")))
    fol_list = QListWidget(objectName="FollowedList")
    series_icon = page_icon("series", win._action_pixmap, 16)
    fol_store = win.followed
    for sid in sorted(fol_store.ids(), key=lambda i: str(
            fol_store.snapshot(i).get("name") or "").casefold()):
        name = fol_store.snapshot(sid).get("name") or str(sid)
        item = QListWidgetItem(series_icon, name)
        item.setData(Qt.ItemDataRole.UserRole, sid)
        fol_list.addItem(item)
    fit_to_rows(fol_list)
    fol_list.setCurrentRow(0)
    fol_none = QLabel(tr("set_follow_none",
                         follow=tr("ctx_follow_series")))
    fol_none.setObjectName("SettingsHint")
    fol_none.setWordWrap(True)
    fol_stop = QPushButton(tr("ctx_unfollow_series"))
    followed_card.shown_if(
        followed_card.widget(fol_none, tr("set_card_followed")),
        lambda: fol_list.count() == 0)
    followed_card.shown_if(
        followed_card.widget(fol_list, tr("set_card_followed")),
        lambda: fol_list.count() > 0)
    followed_card.shown_if(followed_card.layout_row(hbox(fol_stop)),
                           lambda: fol_list.count() > 0)

    def fol_unfollow() -> None:
        # At once, like the right-click menu: Cancel does not bring a
        # series back.
        item = fol_list.currentItem()
        if item is None:
            return
        row = fol_list.row(item)
        win._unfollow_series({
            "series_id": item.data(Qt.ItemDataRole.UserRole),
            "name": item.text()})
        fol_list.takeItem(row)
        fol_list.setCurrentRow(min(row, fol_list.count() - 1))
        followed_card.refresh()

    fol_stop.clicked.connect(fol_unfollow)

    def commit_mine(put, changed: set, final: bool) -> None:
        put("follow_notify", flag(follow_notify_box))
    ctx.page_commits["mine"] = commit_mine


def build_local(ctx: SettingsContext) -> None:
    """Local files: the folders, and the music's art from online."""
    win, d, loc = ctx.win, ctx.dialog, ctx.pages["local"]
    is_on = ctx.is_on
    loc_list = QListWidget(objectName="FolderList")
    folder_icon = page_icon("folder", win._action_pixmap, 16)

    def loc_item(folder: str) -> QListWidgetItem:
        it = QListWidgetItem(folder_icon, folder)
        it.setToolTip(folder)
        return it

    for folder in win._local_dirs():
        loc_list.addItem(loc_item(folder))
    fit_to_rows(loc_list)
    # Selected from the start: Remove says what it would remove.
    loc_list.setCurrentRow(0)
    loc_add = QPushButton(tr("btn_add"))
    loc_del = QPushButton(tr("btn_remove"))
    loc_empty = QLabel(tr("set_local_none"))
    loc_empty.setObjectName("SettingsHint")
    folders = loc.card(tr("set_card_folders"))
    # The rows come and go, not just what is in them: a hidden label
    # in a row still shown left an empty band above the list.
    folders.shown_if(folders.widget(loc_empty, tr("set_card_folders")),
                     lambda: loc_list.count() == 0)
    folders.shown_if(folders.widget(loc_list, tr("set_card_folders")),
                     lambda: loc_list.count() > 0)
    folders.layout_row(hbox(loc_add, loc_del))

    def loc_empty_state() -> None:
        folders.refresh()
        loc_del.setEnabled(loc_list.count() > 0)

    loc_list.model().rowsInserted.connect(lambda *_a: loc_empty_state())
    loc_list.model().rowsRemoved.connect(lambda *_a: loc_empty_state())
    loc_empty_state()
    c = loc.card(tr("set_card_music"))
    art_chk = c.toggle(tr("local_art_online"),
                       is_on("music_art_online", "true"))
    art_chk.toggled.connect(
        lambda checked: win.settings.setValue(
            "music_art_online", "true" if checked else "false"))

    def loc_refresh_side():
        # Reflect a folder change in the section immediately.
        if win.mode == "local":
            win.cat_list.blockSignals(True)
            win.cat_list.clear()
            win.cat_list.blockSignals(False)
            win._load_local_categories()

    def loc_add_dir():
        start = (win.settings.value("local_open_dir", "")
                 or os.path.expanduser("~"))
        path = QFileDialog.getExistingDirectory(
            d, tr("local_add_folder_title"), start,
            file_dialog_options(QFileDialog.Option.ShowDirsOnly))
        dirs = win._local_dirs()
        if path and path not in dirs:
            win._save_local_dirs(dirs + [path])
            loc_list.addItem(loc_item(path))
            loc_list.setCurrentRow(loc_list.count() - 1)
            loc_refresh_side()

    def loc_del_dir():
        it = loc_list.currentItem()
        if it is None:
            return
        win._save_local_dirs(
            [d for d in win._local_dirs() if d != it.text()])
        row = loc_list.row(it)
        loc_list.takeItem(row)
        loc_list.setCurrentRow(min(row, loc_list.count() - 1))
        loc_refresh_side()

    loc_add.clicked.connect(loc_add_dir)
    loc_del.clicked.connect(loc_del_dir)


def build_metadata(ctx: SettingsContext) -> None:
    """Posters & info: where the artwork comes from (TMDB's built-in key,
    the user's own, or the provider's), and Popular right now."""
    win, meta = ctx.win, ctx.pages["metadata"]
    flag = ctx.flag
    _bundled = bool(bundled_tmdb_key())
    # Source options: the built-in key (only offered when one
    # ships), the user's own key, or the provider's own artwork. The
    # key field appears only for the "own key" choice.
    _options = []
    if _bundled:
        _options.append(("tmdb", tr("meta_src_builtin")))
    _options.append(("custom", tr("meta_src_own")))
    _options.append(("playlist", tr("meta_src_provider")))
    # Nothing asked of TMDB at all (coverart.OFF).
    _options.append(("off", tr("meta_src_off")))
    _default_src = win.settings.value(
        "metadata_source",
        "tmdb" if _bundled else
        ("custom" if win.settings.value("tmdb_api_key", "")
         else "playlist"))
    meta_source_box = win._combo(_options, _default_src)
    tmdb_key_edit = QLineEdit(win.settings.value("tmdb_api_key", ""))
    tmdb_key_edit.setPlaceholderText(tr("tmdb_key_placeholder"))
    tmdb_test_btn = QPushButton(tr("btn_test"))
    tmdb_key_row = QHBoxLayout()
    tmdb_key_row.setSpacing(8)
    tmdb_key_row.addWidget(tmdb_key_edit, 1)
    tmdb_key_row.addWidget(tmdb_test_btn)
    c = meta.card()
    c.row(tr("setting_artwork_source"), meta_source_box)
    key_row = c.row(tr("setting_tmdb_key"), tmdb_key_row,
                    stretch_control=True)
    tmdb_status = QLabel()
    tmdb_status.setWordWrap(True)
    status_row = c.widget(tmdb_status)
    on_note = c.note(tr("meta_hint_builtin") if _bundled
                     else tr("meta_hint_own"))
    off_note = c.note(tr("meta_hint_off"))

    def custom_source() -> bool:
        return meta_source_box.currentData() == "custom"

    def tmdb_off() -> bool:
        return meta_source_box.currentData() == "off"

    c.shown_if(key_row, custom_source)
    c.shown_if(status_row, custom_source)
    c.shown_if(on_note.parentWidget(), lambda: not tmdb_off())
    c.shown_if(off_note.parentWidget(), tmdb_off)
    meta_card = c

    def test_tmdb_key() -> None:
        key = tmdb_key_edit.text().strip()
        if not key:
            tmdb_status.setText(tr("tmdb_enter_key"))
            tmdb_status.setStyleSheet(
                f"color:{P['error']}; font-size:12px;")
            return
        tmdb_status.setText(tr("tmdb_checking"))
        tmdb_status.setStyleSheet(
            f"color:{P['muted']}; font-size:12px;")

        def check():
            TmdbClient(key).poster_url("Inception", "vod")
            return True

        def ok(_r):
            if sip.isdeleted(tmdb_status):  # an answer after the window went
                return
            tmdb_status.setText(tr("tmdb_key_works"))
            tmdb_status.setStyleSheet(
                f"color:{P['accent']}; font-size:12px; font-weight:600;")

        def fail(msg):
            if sip.isdeleted(tmdb_status):
                return
            tmdb_status.setText(tr("tmdb_key_failed", msg=msg))
            tmdb_status.setStyleSheet(
                f"color:{P['error']}; font-size:12px;")

        run_async(win.pool, check, ok, fail)

    meta_source_box.currentIndexChanged.connect(
        lambda _i: meta_card.refresh())
    tmdb_test_btn.clicked.connect(test_tmdb_key)
    # TMDB's chart of the week, what the source has: a row at the top
    # of Movies and of Series.
    c = meta.card(tr("card_popular"))
    pop_chips = {kind: Chip(tr(words), _places.popular_on(win.settings, kind))
                 for kind, words in (("vod", "nav_movies"), ("series", "nav_series"))}
    c.widget(ChipGroups([(tr("set_popular_where"), list(pop_chips.values()))]),
             " ".join([tr("card_popular")] + [ch.text() for ch in pop_chips.values()]))
    c.note(tr("set_popular_note"))

    def commit_metadata(put, changed: set, final: bool) -> None:
        put("metadata_source", meta_source_box.currentData())
        put("tmdb_api_key", tmdb_key_edit.text().strip())
        for kind, chip in pop_chips.items():
            # Written once switched (on is the default).
            if chip.isChecked() != _places.popular_on(win.settings, kind):
                put(_places.POPULAR_KEYS[kind], flag(chip))
    ctx.page_commits["metadata"] = commit_metadata
