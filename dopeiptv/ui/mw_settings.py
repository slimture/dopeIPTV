"""Settings dialog, theme/language/view application (mixin).

Split out of main_window.py verbatim; MainWindow inherits this. All methods
keep their original `self.*` access, so behaviour is unchanged. The pages'
builders are one module per group of the Settings sidebar - ui/set_watch.py,
set_content.py, set_services.py and set_system.py - each handed the
window's SettingsContext; Nerd's pages are ui/nerd_settings.py's. About
is ui/about.py; ``show_about`` and ``_format_release_notes`` stay here,
handing on to it.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from functools import partial
from datetime import datetime
from typing import Any, Callable

from PyQt6.QtCore import QEvent, QLocale, QObject, Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QAbstractSlider, QAbstractSpinBox,
    QApplication,
    QComboBox, QDialog,
    QFileDialog,
    QHBoxLayout, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QScrollBar, QSpinBox, QVBoxLayout, QWidget,
)


class _WheelGuard(QObject):
    """Stops the mouse wheel from ever changing a combo/spin/slider in the
    Settings window - it's far too easy to nudge a setting while just trying to
    scroll the page. The wheel is always swallowed on these controls (you change
    a setting by clicking, not scrolling) and instead scrolls the enclosing
    scroll area directly, so the page still moves under the cursor.

    The page is scrolled by nudging the scroll bar value - NOT by re-dispatching
    the wheel event. Re-dispatching (QApplication.sendEvent) re-enters the
    application-level event filters, which recursed infinitely and crashed the
    app the moment you scrolled over a control."""

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Wheel:
            area = obj.parent()
            while area is not None and not isinstance(area, QScrollArea):
                area = area.parent()
            if area is not None:
                sb = area.verticalScrollBar()
                delta = ev.angleDelta().y() or ev.angleDelta().x()
                if delta:
                    sb.setValue(sb.value() - delta)
            return True   # never let the control itself act on the wheel
        return False


# One shared, app-lifetime instance (a QObject filter can serve every control).

from .. import VERSION
from ..i18n import tr
from .dialogs import PlaylistDialog
from .source_page import SourcePage
from ..providers.metadata import CacheFiles
from ..providers.client import make_client
from ..core import connections
from ..core import places as _places
from ..core.log import log
from ..core.num import whole
from . import a11y, about, set_content, set_services, set_system, set_watch
from .theme import P, THEMES, apply_theme, build_style, effective_theme
from .settings_ui import Chip, SettingsPage, SettingsShell, page_icon, settings_style
from .widgets import confirm, file_dialog_options, plain_text
from .notices import hub_of
from ..core.workers import run_async


@dataclass(eq=False)
class SettingsContext:
    """What a Settings page's builder takes from open_settings - what it
    took there by name when it was a closure inside it. One per Settings
    window, handed to every builder (ui/set_watch.py, set_content.py,
    set_services.py, set_system.py); its dicts and lists are the window's
    own, shared by every page."""

    #: The main window (the builders' ``self`` when they were closures).
    win: Any
    #: The Settings window.
    dialog: QDialog
    shell: SettingsShell
    #: Every page, by its key ("playback", "general", ...).
    pages: dict[str, SettingsPage]
    #: A switch on, as stored ("true").
    on: str
    #: The language reads right to left.
    rtl: bool
    #: is_on(key, default): *key*'s stored value is on.
    is_on: Callable[[str, str], bool]
    #: flag(box): a switch as stored, "true" or "false".
    flag: Callable[[Any], str]
    #: own(): the "This profile's" mark for a row while the household has
    #: two profiles or more, else None.
    own: Callable[[], Any]
    #: nerd_link(card, text_key, names, part): the row that leads to one of
    #: Nerd's pages, shown while Nerd is on.
    nerd_link: Callable[[Any, str, list[str], str], None]
    #: Nerd's switch once its pages are built ({"box": None} until then).
    nerd_switch: dict[str, Any]
    #: The cards with a row that leads to one of Nerd's pages.
    link_cards: list
    #: Each page built's commit(put, changed, final), by the page's key.
    page_commits: dict[str, Callable[..., None]]
    #: What one page's code changes on another, while that one is built.
    shared: dict[str, Any]
    #: restyle_dialog(): the window's own rules again, from the palette.
    restyle_dialog: Callable[[], None]
    #: The sources (the window's playlist store; None without one).
    store: Any
    #: account_of(source, done): its account, looked up off the window's
    #: thread, handed to done(info).
    account_of: Callable[[dict, Callable[[dict], None]], None]
    #: edit_source(pid, under=None): the source's own page, in place of
    #: the page it is opened from.
    edit_source: Callable[..., None]
    #: add_playlist(prefill=None): the quick form, then the new source's
    #: page.
    add_playlist: Callable[..., None]
    #: The backup (Privacy and data): saved - export_playlists(password=""), whether it
    #: was written - and restored (Sources' list follows).
    export_playlists: Callable[..., bool]
    import_playlists: Callable[[], None]
    #: A change made: stored and applied a moment later (set once the
    #: window's change timer is made, before any page is built).
    soon: Callable[..., None] | None = None


#: Settings' everyday pages, in the sidebar's order: (key, title, what it
#: holds, icon, a shorter name for the sidebar or "", its builder - one
#: module per group, run the first time the page is needed). Each setting
#: has one page, the one a person looks on (docs/SETTINGS_MAP.md).
PAGES: tuple[tuple[str, str, str, str, str, Callable[[SettingsContext], None]], ...] = (
    ("playback", "tab_playback", "set_page_playback_sub", "play", "", set_watch.build_playback),
    # Sound, subtitles and picture on one page: they were two pages of four
    # and five rows, both about how a stream plays.
    ("audio", "set_page_av", "set_page_av_sub", "picture", "", set_watch.build_audio),
    ("guide", "sec_guide", "set_page_guide_sub", "guide", "", set_watch.build_guide),
    ("multiview", "tab_multiview", "set_page_multiview_sub", "grid", "", set_watch.build_multiview),
    ("recording", "tab_recording", "set_page_recording_sub", "rec", "", set_watch.build_recording),
    ("casting", "set_page_casting", "set_page_casting_sub", "cast", "", set_watch.build_casting),
    # TV mode and what controls it - the remotes, the game controllers and
    # the phone: it was a section of General.
    ("tv", "set_page_tv", "set_page_tv_sub", "tv", "tv_mode", set_watch.build_tv),
    ("playlists", "set_page_sources", "set_page_playlists_sub", "stack", "", set_content.build_sources),
    # Mine's own (the place that holds favourites, Watch later and
    # history): the series followed and how their news is said.
    ("mine", "nav_mine", "set_page_mine_sub", "star", "", set_content.build_mine),
    ("local", "nav_local", "local_settings_hint", "folder", "", set_content.build_local),
    ("metadata", "set_page_posters", "set_page_metadata_sub", "image", "", set_content.build_metadata),
    # Its explanation names Simkl only where this build offers it.
    ("history", "set_page_history", "set_page_history_sub", "trakt", "", set_services.build_history),
    # Its whole title is too long for the sidebar in most languages.
    ("sync", "sync_title", "set_page_sync_sub", "sync", "sync_nav", set_services.build_sync),
    ("integrations", "set_page_integrations", "set_page_integrations_sub", "plug", "",
     set_services.build_integrations),
    ("general", "set_page_general", "set_page_general_sub", "gear", "", set_system.build_general),
    # What the app keeps of what is watched, and everything in one file:
    # private viewing, clearing history and the backup were General's.
    ("privacy", "set_page_privacy", "set_page_privacy_sub", "shield", "", set_system.build_privacy),
    ("appearance", "set_page_appearance", "set_page_appearance_sub", "palette", "",
     set_system.build_appearance),
    # A whole feature of its own (every action's key), for everyone: it was
    # lost at the top of Advanced.
    ("shortcuts", "sc_title", "set_page_shortcuts_sub", "keyboard", "", set_system.build_shortcuts),
    # One per person in the household (mw_profiles): a service of its own,
    # beside the parental lock its kids profiles follow.
    ("profiles", "profile_page", "profile_page_sub", "person", "", set_system.build_profiles),
    ("parental", "set_page_parental", "set_page_parental_sub", "lock", "", set_system.build_parental),
    ("advanced", "set_page_advanced", "set_page_advanced_sub", "wrench", "", set_system.build_advanced),
)

#: Nerd's pages but Status, by the part ui/nerd_settings.py puts on each:
#: (part, key, title, what it holds, icon); in Nerd's own colour, shown
#: only while it is on.
NERD_PAGES = (
    ("net", "nerd_net", "nerd_pg_net", "nerd_pg_net_sub", "network"),
    ("boxes", "nerd_boxes", "nerd_pg_boxlists", "nerd_pg_boxlists_sub", "box"),
    ("player", "nerd_player", "nerd_pg_player", "nerd_pg_player_sub", "knobs"),
    ("serve", "nerd_serve", "nerd_pg_remote", "nerd_pg_remote_sub", "remote"),
    ("diag", "nerd_diag", "nerd_pg_diag", "nerd_pg_diag_sub", "pulse"),
)

#: The sidebar's groups, by what a person is doing (AGENTS.md, "Where a
#: setting goes"): watching, what there is to watch, the accounts and
#: services around it, the app itself - and Nerd's after, in its colour.
GROUPS = (
    ("set_grp_watch", ("playback", "audio", "guide", "multiview", "recording", "casting", "tv")),
    ("set_grp_content", ("playlists", "mine", "local", "metadata")),
    ("set_grp_accounts", ("history", "sync", "integrations")),
    ("set_sec_system", ("general", "privacy", "appearance", "shortcuts", "profiles",
                        "parental", "advanced")),
)

#: How wide the Settings window's sidebar was dragged (SettingsShell): this
#: computer's layout, never synced or in a backup.
SIDE_KEY = "settings_side_w"

#: Each everyday page's title by its key: a menu's way to it reads
#: "Settings › <title>…" (settings_link_text).
PAGE_TITLES = {key: title for key, title, *_rest in PAGES}

#: What the player reads as it starts a stream: a change re-applies its
#: defaults (apply_default_options).
PLAYER_KEYS = frozenset({
    "audio_lang", "sub_mode", "sub_lang", "sub_lang2", "sub_live", "sub_scale",
    "sub_color", "sub_background", "aspect_mode",
    "video_deinterlace", "video_sharpen", "video_tonemapping"})


class _SettingsSources:
    """The sources as one Settings window handles them - shared by Sources,
    the backup (Privacy and data) and Nerd's boxes: a source's own page, the
    quick form for a new one, an account looked up, the backup made and
    put back."""

    def __init__(self, win: Any, dialog: QDialog, shell: SettingsShell,
                 shared: dict, nerd_on: Callable[[], bool]) -> None:
        self.win, self.dialog, self.shell = win, dialog, shell
        self.shared, self.nerd_on = shared, nerd_on
        self.store = win.playlist_store

    def changed(self, pid) -> None:
        """A source added, saved or restored: the Sources page's list
        again with *pid* picked, if it is built - else the sidebar's
        switcher alone, which that list's reload also updates."""
        lst = self.shared.get("sources")
        if lst is not None:
            lst["reload"]()
            lst["select"](pid)
        else:
            self.win._update_playlist_btn()

    def account_of(self, pl, done) -> None:
        """The account behind source *pl* (looked up off the window's
        thread), handed to *done(info)*; the app's own connections to
        it as it is asked: what the provider counts besides them is
        another device's."""
        win = self.win
        pid = pl.get("id")
        seen = getattr(win, "_conn_seen", None)
        own = (win._conn_own(win._conn_uses(), str(pid))
               if seen is not None else 0)

        def fetch(pl=pl):
            try:
                return make_client(pl).account_info()
            except Exception:
                return {}

        def got(info):
            if seen is not None and info:
                seen(str(pid), info, own)
            done(info)

        run_async(win.pool, fetch, got, lambda _e: done({}))

    def saved(self, pid) -> None:
        self.changed(pid)

    def left(self, pid, changed: bool) -> None:
        # The source in use connects again with what was changed.
        if changed and self.store and pid == self.store.active_id:
            self.win.switch_playlist(pid)

    def account(self, pl, done) -> None:
        if (pl.get("kind") or "xtream") == "m3u":
            return
        self.account_of(pl, lambda info: done(
            self.win._format_account(info) if (info or {}).get("user_info") else ""))

    def edit(self, pid, under=None):
        """The source's own page, in place of the page it is opened
        from (Sources, or Nerd's boxes), with a way back to it."""
        store, shell = self.store, self.shell
        pl = store.get(pid) if (store and pid) else None
        if not pl:
            return
        under = under or "playlists"
        title = next((p.title for p in shell.pages if p.key == under), "")
        src_page = SourcePage(pl, store=store, nerd_on=self.nerd_on(), crumb=title,
                              back=shell.leave_sub, open_page=shell.show_page,
                              account=self.account, saved=self.saved,
                              left=self.left, settings=self.win.settings)
        self.win._install_wheel_guard(src_page)
        shell.open_sub(src_page, under)

    def add(self, prefill=None):
        # The quick form, then on to the new source's page.
        dlg = PlaylistDialog(self.dialog, prefill=prefill)
        if dlg.exec():
            pl = self.store.add(dlg.values())
            self.changed(pl["id"])
            self.edit(pl["id"], self.shell.current_key())

    def export(self, password: str = "") -> bool:
        return self.win._backup_save(self.dialog, password)

    def added(self) -> None:
        self.changed(self.store.active_id)

    def import_(self):
        self.win._backup_restore(self.dialog, self.added)


class _SettingsMixin:
    """MainWindow mixin: the Settings dialog, its tabs and persistence."""

    def settings_link_text(self, page: str) -> str:
        """"Settings › Picture & sound…": a menu's way to the page its
        settings are on (PAGE_TITLES), in the window's direction."""
        rtl = QLocale().textDirection() == Qt.LayoutDirection.RightToLeft
        return f"{tr('settings_title')} {'‹' if rtl else '›'} {tr(PAGE_TITLES[page])}…"

    def open_settings_at(self, page: str) -> None:
        """Settings on *page*, once the menu or window that asked is gone."""
        QTimer.singleShot(0, lambda: self.open_settings(at_page=page))

    def add_settings_link(self, menu, page: str):
        """The way from *menu* to Settings' *page*, at its end."""
        return menu.addAction(self.settings_link_text(page),
                              lambda: self.open_settings_at(page))

    def _maybe_offer_desktop_entry(self) -> None:
        """Offer, once, to add the app to the desktop's menu.

        Wayland takes a window's icon from the .desktop file matching its
        app_id and ignores setWindowIcon entirely, so from an AppImage -
        which nothing has installed an entry for - GNOME shows the generic
        placeholder in the dash however good the icon inside the AppImage
        is. From the .deb the entry is there and it looks right.

        Asked rather than done, because the AppImage format's whole
        premise is a file that changes nothing outside itself. The offer is
        a link in the status row: a dialog at startup sat on top of the
        window, and was all AppImageHub's screenshot of the app showed. The
        answer is remembered either way, and Settings can change it later."""
        try:
            from ..core import desktop_entry
            # Logged every start, offer or no offer. "No icon" has four
            # causes that look identical from outside - nothing offered,
            # something else already provides an entry, the entry is there
            # but the session has not picked it up, or the icon file is
            # missing - and one line here says which, instead of a round
            # trip of guessing.
            log.info("%s", desktop_entry.describe())
            desktop_entry.take_links()
            if self.settings.value("desktop_entry_asked", "false") == "true":
                return
            if not desktop_entry.can_offer():
                return
            self.desktop_entry_btn.show()
        except Exception as e:      # never let a nicety break startup
            log.warning("desktop entry offer failed: %s", e)

    def _tell_settings_not_private(self) -> None:
        """Once a start, when a settings file could not be made readable by
        this user alone (core/settings_files.py: a drive that keeps no
        permissions, a file another account owns, a link to nothing): asked
        in a window that waits for an answer - it holds logins - for every
        such file the person has not said to go on with, one after another;
        after that a calm message each start. Its button opens the folder.
        It fails closed: a question that could not be asked closes the
        window rather than go on with an unanswered file."""
        from ..core import settings_files
        try:
            found = settings_files.not_private()
        except Exception as e:
            log.warning("settings: their permissions could not be read: %s", e)
            return
        if not found or getattr(self, "_settings_open_told", False):
            return
        self._settings_open_told = True
        pending: str | None = None
        try:
            for path in settings_files.waiting(self.settings.value(settings_files.ACCEPTED_KEY, "")):
                pending = path
                answer = self._ask_settings_open(path)
                if answer != "go":
                    if answer == "open":
                        from ..core import xdg
                        xdg.open_folder(os.path.dirname(path))
                    log.warning("settings: the app closes - its settings file is not private")
                    self.close()
                    return
                self.settings.setValue(settings_files.ACCEPTED_KEY, settings_files.with_accepted(
                    self.settings.value(settings_files.ACCEPTED_KEY, ""), path))
                log.warning("settings: kept on a drive other accounts may read, as the "
                            "person said")
                pending = None
            folder = os.path.dirname(found[0])

            def show() -> None:
                from ..core import xdg
                xdg.open_folder(folder)
            hub_of(self).post(tr("settings_open_body", path=found[0]), title=tr("settings_open_title"),
                              level="warning", action=show, action_label=tr("btn_open_folder"),
                              key="settings_not_private")
        except Exception as e:
            log.warning("settings: the note on their permissions was not shown: %s", e)
            if pending is not None:
                log.warning("settings: the app closes - a question about a settings file "
                            "others may read could not be asked")
                self.close()

    def _ask_settings_open(self, path: str) -> str:
        """The question a settings file others may read asks: "go" (go on
        with it, for this file), "open" (its folder, and close) or "quit" -
        Close the default, and Escape."""
        box = QMessageBox(QMessageBox.Icon.Warning, tr("settings_open_title"),
                          plain_text(tr("settings_open_body", path=path)),
                          QMessageBox.StandardButton.NoButton, self)
        opened = box.addButton(tr("btn_open_folder"), QMessageBox.ButtonRole.ActionRole)
        quit_btn = box.addButton(tr("settings_open_quit"), QMessageBox.ButtonRole.RejectRole)
        go = box.addButton(tr("settings_open_go"), QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(quit_btn)
        box.setEscapeButton(quit_btn)
        box.exec()
        clicked = box.clickedButton()
        return "go" if clicked is go else "open" if clicked is opened else "quit"

    def _ask_desktop_entry(self) -> None:
        """The status-row link was clicked: ask, and remember the answer."""
        self.desktop_entry_btn.hide()
        try:
            from ..core import desktop_entry
            self.settings.setValue("desktop_entry_asked", "true")
            if confirm(self, tr("desktop_entry_title"),
                       tr("desktop_entry_ask")):
                ok = desktop_entry.install()
                self.settings.setValue(
                    "desktop_entry", "true" if ok else "false")
                if ok:
                    # A window that is already on screen keeps whatever icon
                    # the compositor resolved for it when it appeared, so
                    # this one does not change until the next start. Saying
                    # so beats leaving the user to conclude it did nothing.
                    QMessageBox.information(
                        self, tr("desktop_entry_title"),
                        tr("desktop_entry_done"))
            else:
                self.settings.setValue("desktop_entry", "false")
        except Exception as e:
            log.warning("desktop entry install failed: %s", e)

    def _apply_desktop_entry(self, wanted: bool) -> None:
        """Put the entry in or take it out to match the Settings switch."""
        from ..core import desktop_entry
        try:
            if wanted and not desktop_entry.is_installed():
                desktop_entry.install()
            elif not wanted and desktop_entry.is_installed():
                desktop_entry.remove()
        except Exception as e:
            log.warning("desktop entry change failed: %s", e)

    def _choose_rec_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, tr("rec_choose_folder_title"), self.rec.directory(),
            file_dialog_options(QFileDialog.Option.ShowDirsOnly))
        if d:
            self.rec.set_directory(d)
            if self.mode == "rec":
                self._load_categories()

    def _clear_history(self) -> None:
        """Recently viewed's Clear button: the Clear recent history window
        (a time range, what to clear), with the list's own kind ticked -
        channels from TV channels, titles from Movies or Series."""
        sub = getattr(self, "_history_subcat", None)
        pre = {"live": "channels", "movie": "titles", "series": "titles",
               "local": "titles"}.get(sub or "")
        self.open_clear_history(pre)

    # -- EPG guide -----------------------------------------------------------------

    def _favorite_channels_for_guide(self) -> list:
        """The favorite live channels the EPG guide should cover, honoring the
        currently selected Favorites sub-category (a channel folder, or all)."""
        cat = self.cat_list.currentItem()
        data = cat.data(Qt.ItemDataRole.UserRole) if cat else None
        section, group = data if isinstance(data, tuple) else ("all", None)
        if section not in ("chan", "all"):
            return []          # Movies / Series / Trakt: no live EPG
        exclude = (() if self.parental.session_unlocked
                   else self.favs.locked_groups())
        return self.favs.items(group if section == "chan" else None,
                               exclude_groups=exclude)

    # -- view settings -------------------------------------------------------------

    def _apply_view_settings(self) -> None:
        density = self.settings.value("view_density", "medium")
        grid = self.settings.value("view_grid", "false") == "true"
        self.delegate.set_density(density)
        self._apply_list_layout(False)   # honour the user's grid/list choice
        self.listw.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        step = (self.delegate.grid_size().height() // 2 if grid
                else self.delegate.row_h)
        self.listw.verticalScrollBar().setSingleStep(max(30, step))
        if hasattr(self, "size_box"):
            self.size_box.blockSignals(True)
            i = self.size_box.findData(density)
            if i >= 0:
                self.size_box.setCurrentIndex(i)
            self.size_box.blockSignals(False)
            self._sync_sort_box()       # and the order "As in all categories" names
            self.grid_btn.blockSignals(True)
            self.grid_btn.setChecked(grid)
            self.grid_btn.blockSignals(False)
        # Combined views must rebuild (grid drops their headers, list keeps
        # them); every other view just re-filters the current items.
        # ...but NEVER while drilled into a series. Favorites' "All" and
        # Watched are combined views, so a grid toggle there rebuilt the
        # category - putting the favourites list back on screen while
        # series_ctx still said "inside a series". The two then disagreed:
        # the rows were series, the state said episodes, and a double-click
        # built an episode URL from a series row. Episodes are already
        # loaded; re-filtering is all a view toggle ever needs.
        if (self._is_combined_view(getattr(self, "_current_cat", None))
                and not self.series_ctx):
            self._load_items(getattr(self, "_current_cat", None))
        else:
            self._apply_filter()

    def _watch_color_scheme(self) -> None:
        """Follow the desktop between its light and dark mode, live, when
        Settings says so (Qt 6.5+; elsewhere the setting simply holds)."""
        try:
            hints = QApplication.instance().styleHints()
            hints.colorSchemeChanged.connect(self._on_color_scheme_changed)
        except Exception as e:
            log.debug("colour scheme changes not available: %s", e)

    def _on_color_scheme_changed(self, *_args) -> None:
        if self.settings.value("theme_follow_system", "false") != "true":
            return
        theme = effective_theme(self.settings)
        if P.get("name") != THEMES[theme]["name"]:
            log.info("desktop colour scheme changed - theme %s", theme)
            self._apply_theme_now()

    def _apply_theme_now(self) -> None:
        apply_theme(self.settings)
        QApplication.instance().setStyleSheet(build_style())
        if hasattr(self, "_apply_nav_icons"):
            self._apply_nav_icons()   # re-tint the sidebar icons to the new theme
        self._sidebar_logo.update()
        self.listw.viewport().update()
        if hasattr(self, "media_info"):
            self._style_media_info()
        self.count_lbl.setStyleSheet(
            f"color:{P['muted3']}; font-size:11px;")
        self.update_status_btn.setStyleSheet(
            f"color:{P['accent']}; font-size:11px; font-weight:600;"
            "border:none; background:transparent; padding:0 4px;")
        self._apply_play_icon()   # redraw the play triangle in the new accent
        if self.d_logo.text():
            self.d_logo.setStyleSheet(self.PLACEHOLDER_LOGO_STYLE)
        if self.player:
            # The drawn control icons are baked with the old text colour;
            # redraw them for the new theme.
            self.player.refresh_icons()
        card = getattr(self, "cast_bar", None)
        if card is not None and hasattr(card, "restyle"):
            card.restyle()           # the cast card, in the new colours
        self._places_restyle()       # the places' header, in the new colours
        self._guide_restyle()        # the Guide page's board, in the new colours
        if hasattr(self, "_app_icon_refresh"):
            self._app_icon_refresh()     # an icon in the accent, in the new one
        self._sp_restyle()           # a series' page
        self._pf_restyle()           # the Movies place and a film's page
        self._place_series_restyle()   # the Series place
        self._place_local_restyle()  # the Local files place
        self._place_rec_restyle()    # the Recordings place
        self._place_radio_restyle()  # the Radio place
        self._search_restyle()       # the Search page
        self._place_tv_restyle()     # the TV place's header and row
        getattr(self, "_mitt_restyle", lambda: None)()   # Mine
        # The notices keep a style sheet of their own: the decision card and
        # the panel of messages stayed in the old theme's colours.
        getattr(self, "_notices_restyle", lambda: None)()
        if getattr(self, "_tv_page", None) is not None:
            self._tv_page.restyle()  # TV mode's page (its tiles paint from P)

    def _reset_all_settings(self, parent_dialog) -> None:
        """Wipe every stored preference (theme, layout, playlists, favorites,
        history, credentials, ...) and ask the user to restart. Two-step
        confirmation because it can't be undone. Playlists live in the same
        QSettings config, so this really does reset back to the login screen."""
        if not confirm(parent_dialog, tr("settings_reset_all"),
                       tr("settings_reset_confirm_1"), default_yes=False):
            return
        if not confirm(parent_dialog, tr("settings_reset_all"),
                       tr("settings_reset_confirm_2"), default_yes=False):
            return
        # The recordings stay wherever they are, also inside a folder the
        # reset empties - read before the setting that names it goes.
        rec = getattr(self, "rec", None)
        keep = [rec.directory()] if rec is not None else []
        # Clear() drops every key from a QSettings scope - every profile's
        # (core/profiles.py) - and sync() flushes it to disk before we tell
        # the user to restart. Watched and Watch Later live in the cache
        # file, resume positions in the resume file and followed series in
        # the follow file: clear all four or those survive a "reset all".
        for st in (self.settings,
                   getattr(self, "_resume_settings", None),
                   getattr(self, "_follow_settings", None),
                   getattr(self, "_cache_settings", None)):
            if st is not None:
                st.clear()
                st.sync()
        # The TMDB caches have files of their own beside the cache settings.
        resolver = getattr(getattr(self, "cover", None), "resolver", None)
        if resolver is not None:
            resolver.files.clear()
        elif getattr(self, "_cache_settings", None) is not None:
            CacheFiles(self._cache_settings).clear()
        # A restore staged and not yet put in place would bring it all back
        # at the next start (core/backup.apply_pending).
        from ..core import backup
        from ..core import reset as app_reset
        try:
            os.remove(backup.pending_path())
        except FileNotFoundError:
            pass
        except OSError as e:
            log.warning("reset: the staged restore not removed: %s", e)
        # The folders - the cache, the logs, the app's own yt-dlp - emptied
        # once the window has let go of them (closeEvent) and again at the
        # next start (core/reset.py).
        app_reset.request(os.path.dirname(backup.pending_path()), keep)
        self._reset_wipe = keep
        # closeEvent persists layout/resume/watched on the way out - skip
        # that during a reset, or the freshly cleared config gets seeded
        # with those keys again.
        self._resetting = True
        QMessageBox.information(
            parent_dialog, tr("settings_reset_all"),
            tr("settings_reset_done"))
        parent_dialog.reject()
        # Quit so the next launch reads a fresh (empty) config and shows the
        # login screen. Route through the main window's close (player/pool
        # teardown lives in closeEvent, which also picks the exit strategy) -
        # a bare QApplication.quit() here segfaulted on macOS: AppKit's
        # terminate ran exit() finalizers while Qt timers were still live.
        self.close()

    # -- backup and restore (core/backup.py) ---------------------------------

    def _backup_stores(self) -> dict:
        """The four files as they are - every profile's own in them
        (core/profiles.py), not only the profile's in use."""
        from ..core.profiles import raw
        out = {"main": self.settings,
               "resume": getattr(self, "_resume_settings", None),
               "follow": getattr(self, "_follow_settings", None),
               "cache": getattr(self, "_cache_settings", None)}
        return {k: (raw(v) if v is not None else None) for k, v in out.items()}

    @staticmethod
    def _backup_counts(counts: dict) -> str:
        """One line per kind a backup holds, as many as it has."""
        return "\n".join(tr("backup_count", what=tr(f"backup_kind_{kind}"), n=n)
                         for kind, n in counts.items() if n)

    def _backup_without_password_ok(self, parent) -> bool:
        """Without a password the file leaves every login, token and the
        sync code out (core/backup.without_logins): made so only when the
        person says so, Cancel the default."""
        box = QMessageBox(QMessageBox.Icon.Warning, tr("backup_title"),
                          tr("backup_no_pw_warn"), QMessageBox.StandardButton.Cancel, parent)
        plain = box.addButton(tr("backup_no_pw_go"), QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return box.clickedButton() is plain

    def _backup_save(self, parent, password: str = "") -> bool:
        """Settings > Privacy and data > Backup: everything to one file - with
        *password*, encrypted with it (core/backup.lock) on a thread of its
        own while a small window says so. Whether it was written."""
        from datetime import date

        from ..core import backup
        if password and len(password) < backup.PASSWORD_MIN:
            QMessageBox.warning(parent, tr("backup_title"),
                                tr("backup_pw_short", n=backup.PASSWORD_MIN))
            return False
        if not password and not self._backup_without_password_ok(parent):
            return False
        # The same name with a password or without: the file says inside
        # what it is, and the restore finds either kind the same way.
        path, _f = QFileDialog.getSaveFileName(
            parent, tr("backup_title"),
            f"dopeIPTV-backup-{date.today().isoformat()}.json",
            tr("file_filter_json"),
            options=file_dialog_options())
        if not path:
            return False
        doc = backup.collect(self._backup_stores(), VERSION)
        locked = bool(password)
        try:
            if locked:
                from .backup_dialogs import run_working
                run_working(parent, tr("backup_title"), tr("backup_locking"),
                            lambda: backup.write(path, backup.for_file(doc, password)))
            else:
                backup.write(path, backup.for_file(doc))
        except OSError as exc:
            log.warning("backup: not written: %s", exc)
            QMessageBox.warning(parent, tr("backup_title"),
                                tr("pl_export_failed", error=exc))
            return False
        counts = backup.summary(backup.parse(doc))
        log.info("backup: written%s (%s)", " with a password" if locked else "",
                 ", ".join(f"{k} {n}" for k, n in counts.items() if n))
        done = tr("backup_done_locked" if locked else "backup_done",
                  what=self._backup_counts(counts), path=path)
        QMessageBox.information(parent, tr("backup_title"), done)
        return True

    def _backup_restore(self, parent, playlists_added) -> None:
        """Settings > Privacy and data > Backup: a backup back - over everything, as
        the app next starts, or its playlists beside the ones here now."""
        from ..core import backup
        from .dialogs import RestoreBackupDialog, pick_backup, stage_backup
        b, why = pick_backup(parent)
        if b is None:
            if why:     # the file's own words in it (its version)
                QMessageBox.warning(parent, tr("restore_title"), plain_text(why))
            return
        store = getattr(self, "playlist_store", None)
        on = getattr(self, "_sync_on", None)
        dlg = RestoreBackupDialog(parent, b, backup.summary(b),
                                  len(backup.missing_paths(b)), backup.risky(b),
                                  can_add=store is not None,
                                  syncing=bool(on and on()))
        if not dlg.exec():
            return
        if dlg.choice() == "add":
            added = store.import_data(b.sources)
            log.info("restore: %d playlist(s) added from a backup", added)
            playlists_added()
            QMessageBox.information(parent, tr("restore_title"),
                                    tr("pl_import_done", n=added))
            return
        if not backup.replaceable(b):
            # Nothing it holds would replace anything: it would only empty
            # what is here (the dialog offers no such choice either).
            QMessageBox.warning(parent, tr("restore_title"), tr("restore_cannot_replace"))
            return
        # Over everything: the PIN and what it locks are part of it.
        if self.parental.has_pin() and not self._request_unlock():
            return
        if not confirm(parent, tr("restore_title"), tr("restore_confirm"),
                       default_yes=False):
            return
        why = stage_backup(b)
        if why:
            QMessageBox.warning(parent, tr("restore_title"), plain_text(why))
            return
        # As after Reset all: nothing written on the way out, and the app
        # closes - the backup is put in place as it next starts, where no
        # store held in memory here can write over it.
        self._resetting = True
        QMessageBox.information(parent, tr("restore_title"),
                                tr("restore_closing"))
        parent.reject()
        self.close()

    def _set_language(self, code: str) -> None:
        from ..i18n import current_language, is_rtl, set_language
        if code == current_language():
            return
        self.settings.setValue("language", code)
        set_language(code)
        # Mirror the whole UI live for right-to-left languages (Arabic,
        # Persian) and back for LTR - no restart needed.
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.setLayoutDirection(
                Qt.LayoutDirection.RightToLeft if is_rtl(code)
                else Qt.LayoutDirection.LeftToRight)
        from .qt_i18n import apply_qt_language
        apply_qt_language(app)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        """Re-apply every translatable string on the persistent chrome so
        the language switches live. The settings dialog and context menus
        are rebuilt each time they open, so they pick up the language on
        their own and don't need touching here."""
        for action, getter in getattr(self, "_i18n_actions", {}).items():
            action.setText(getter())
        nav_labels = {
            "mitt": "nav_mine", "search": "nav_search", "live": "nav_tv", "guide": "nav_guide",
            "vod": "nav_movies", "series": "nav_series",
            "rec": "nav_recordings", "local": "nav_local", "radio": "nav_radio",
        }
        icon_only = self._places_icons_only()
        for key, btn in self.nav_btns.items():
            if key in nav_labels:
                self._nav_texts[key] = tr(nav_labels[key])
                btn.setToolTip(self._nav_texts[key])
                btn.setAccessibleName(self._nav_texts[key])
                # On the icon rail (and folded) the buttons are icon-only -
                # don't stamp text back onto them; the label lands when the
                # rail expands or the places unfold.
                if not icon_only:
                    btn.setText(self._nav_texts[key])
        self._apply_places_fold()       # the fold control's words
        self._refresh_nav_status()      # the status's words in the tooltips
        self._cat_section_label.setText(tr("sidebar_categories"))
        self._sync_place_header()       # the place's name and Mine's tabs
        # Settings at the foot: an icon, its name in the tooltip.
        self._settings_btn.setToolTip(tr("btn_settings"))
        self._settings_btn.setAccessibleName(tr("btn_settings"))
        # The list bar's buttons whose tooltip names their key (Rescan's F5).
        if hasattr(self, "local_rescan_btn"):
            self.local_rescan_btn.setAccessibleName(tr("local_rescan"))
            self._shortcut_tips(self.shortcut_keys())
        if hasattr(self, "_history_chip_btns"):
            self._history_chip_btns["all"].setText(tr("history_chip_all"))
            self._history_chip_btns["watched"].setText(
                tr("history_chip_watched"))
        if hasattr(self, "_donate_btn"):
            self._donate_btn.setToolTip(tr("donate_support"))
            self._donate_btn.setAccessibleName(tr("donate_support"))
        self._update_playlist_btn()
        self._sync_search_hint()
        self._sync_cat_search_hint()
        self._size_label.setText(tr("label_size"))
        self._sort_label.setText(tr("label_sort"))
        # Icon only: the 30 px poster overlay has no room for a label, and
        # stamping one on here left clipped text after a language switch.
        self.play_mpv.setToolTip(tr("tooltip_play_in_mpv"))
        self._rename_chrome()
        # Player control tooltips (embedded bar + fullscreen overlay).
        if self.player:
            self.player.retranslate_ui()
        strip = getattr(self, "_together_strip", None)
        if strip is not None:
            strip.retranslate()
        self._guide_restyle()        # the Guide page, in the new language
        self._sp_restyle()           # a series' page
        self._pf_restyle()           # the Movies place and a film's page
        self._place_series_restyle()   # the Series place
        self._place_local_restyle()  # the Local files place
        self._place_rec_restyle()    # the Recordings place
        self._place_radio_restyle()  # the Radio place
        self._search_restyle()       # the Search page
        self._place_tv_restyle()     # the TV place, in the new language
        getattr(self, "_mitt_restyle", lambda: None)()   # Mine
        self._palette_retranslate()

    def _rename_chrome(self) -> None:
        """What a screen reader calls the window's controls that show an
        icon or a glyph (ui/a11y.py), again in the language now in use - the
        names given as they were built (mw_build, mw_build_detail)."""
        for widget, words in (
                (self.play_mpv, tr("tooltip_play_in_mpv")),
                (self._reopen_btn, tr("tooltip_show_list")),
                (self.cat_solo_btn, tr("tooltip_solo_category")),
                (self.cat_list, tr("tv_categories")),
                (self.size_box, tr("label_size")),
                (self._size_menu_btn, tr("label_size")),
                (self.sort_box, tr("label_sort")),
                (self._sort_menu_btn, tr("label_sort")),
                (self.side_btn, a11y.without_key(tr("tooltip_toggle_sidebar"))),
                (self.focus_btn, a11y.without_key(tr("tooltip_hide_list")))):
            widget.setAccessibleName(words)

    def _inline_view_changed(self, *_) -> None:
        # Density and grid are global; the sort dropdown applies only to the
        # current category, so different categories can keep different orders.
        self.settings.setValue(
            "view_density", self.size_box.currentData())
        self.settings.setValue(
            self._sort_setting_key(), self.sort_box.currentData())
        self.settings.setValue(
            "view_grid",
            "true" if self.grid_btn.isChecked() else "false")
        log.info("view toggle: mode=%s series_ctx=%s grid=%s items=%d",
                 self.mode, "set" if self.series_ctx else "none",
                 self.grid_btn.isChecked(), len(self.all_items or []))
        self._apply_view_settings()

    # -- settings dialog -----------------------------------------------------------

    _MAX_VISIBLE = 14

    @staticmethod
    def _install_wheel_guard(d, controls: list | None = None) -> None:
        """Guard every value control in the dialog so scrolling the page can't
        change a setting under the cursor - you click to change, not scroll
        past.

        CRUCIAL exception: QScrollBar (which IS-A QAbstractSlider, so
        findChildren picks every scrollbar up too) must never be guarded.
        QAbstractScrollArea delivers wheel events by forwarding them to its own
        scrollbar - guarding scrollbars therefore froze every scrollable child
        in Settings, most visibly the language list, which ignored the wheel
        entirely no matter how the widget itself was configured. Scrollbars ARE
        the scrolling mechanism; the guard is only for value controls.

        The guard object is parented to the dialog: a module-level singleton
        QObject could have its C++ side torn down under some app lifetimes
        (seen in the test suite), leaving installEventFilter a dangling
        wrapper.

        *controls*: the dialog's widgets from a walk already made (each walk
        wraps every object in the dialog for Python); those of the three
        kinds among them are guarded."""
        wheel_guard = _WheelGuard(d)
        kinds = (QComboBox, QAbstractSpinBox, QAbstractSlider)
        # One tree walk over all three control types (a tuple arg), not three.
        if controls is None:
            controls = d.findChildren(kinds)
        for w in controls:
            if isinstance(w, QScrollBar) or not isinstance(w, kinds):
                continue
            w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            w.installEventFilter(wheel_guard)

    @staticmethod
    def _combo(items, current) -> QComboBox:
        items = list(items)
        box = QComboBox()
        for value, label in items:
            box.addItem(label, value)
        idx = box.findData(current)
        if idx >= 0:
            box.setCurrentIndex(idx)
        # Cap the popup height so a long list (the 27-language picker) becomes a
        # scrollable dropdown instead of a wall taller than the dialog that the
        # bottom entries fall off of. The theme sets `combobox-popup: 0`, which
        # is what makes maxVisibleItems take effect under a stylesheet.
        box.setMaxVisibleItems(_SettingsMixin._MAX_VISIBLE)
        # Only the lists that actually overflow the cap (the 27-language
        # picker) need the scrollable-popup treatment; a 2-5 item combo never
        # scrolls, so touching its view just costs layout on every settings
        # open for no visible effect. For the overflowing ones: scroll per
        # pixel (a trackpad's small deltas register instead of being swallowed
        # by per-item mode) and keep the scrollbar always visible as the drag
        # handle / "more below" cue.
        if len(items) > _SettingsMixin._MAX_VISIBLE:
            view = box.view()
            view.setVerticalScrollMode(
                QAbstractItemView.ScrollMode.ScrollPerPixel)
            view.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        if sys.platform == "darwin":
            # On macOS the styled combo doesn't grow to fit its text, so the
            # closed box clips ("Playba…", "Sven…"). Size it to the widest
            # entry (plus a little for the arrow). Linux is left as-is.
            box.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToContents)
            longest = max((len(lbl) for _v, lbl in items), default=0)
            box.setMinimumContentsLength(longest + 2)
        return box

    def _format_account(self, info: dict) -> str:
        """Turn Xtream user_info into a one-line account summary (status,
        expiry + days left, active/max connections)."""
        ui = (info or {}).get("user_info") or {}
        if not ui:
            return tr("account_unavailable")
        status = str(ui.get("status") or "").strip().capitalize()
        if str(ui.get("is_trial")) in ("1", "true", "True"):
            status = f"{status} · {tr('account_trial')}".strip(" ·")
        exp = ui.get("exp_date")
        if exp in (None, "", "null", "0", 0):
            exp_txt = tr("account_unlimited")
        else:
            try:
                dt = datetime.fromtimestamp(int(exp))
                days = (dt - datetime.now()).days
                exp_txt = dt.strftime("%Y-%m-%d")
                exp_txt += (f" ({tr('account_days_left', days=days)})"
                            if days >= 0 else f" ({tr('account_expired')})")
            except (TypeError, ValueError, OSError):
                exp_txt = str(exp)
        limit, active = connections.numbers(ui)
        if limit is not None and active is not None:
            conns = tr("conn_in_use", n=active, max=limit)
        elif active is not None:
            conns = tr("conn_in_use_only", n=active)
        else:
            conns = (f"{tr('account_connections')}: {ui.get('active_cons', '?')}"
                     f" / {ui.get('max_connections', '?')}")
        return "     ·     ".join([
            f"{tr('account_status')}: {status or '—'}",
            f"{tr('account_expiry')}: {exp_txt}",
            conns,
        ])

    def _nerd_to_player(self) -> None:
        """Nerd's player options onto the core playing now (they used to wait
        for the app's next start)."""
        from ..core import nerd
        player = getattr(self, "player", None)
        video = getattr(player, "video", None)
        m = getattr(video, "mpv", None)
        if m is None:
            return
        try:
            # The player's own way: its variant switch follows the new cap.
            again = getattr(player, "nerd_reapply", None)
            if again is not None:
                again()
            else:
                nerd.reapply_core(m)
        except Exception as e:
            log.warning("nerd: player not updated: %s", e)

    def _nerd_route_to_client(self) -> None:
        """The active source's own route (core/net_route.py: own DNS, IP
        version, a pinned certificate) onto its client's session, as the
        Nerd page says now - or requests' own adapter back when it says
        nothing (core/net_adapter.py)."""
        from ..core import nerd, net_adapter, net_route
        session = getattr(self.client, "session", None)
        if session is None:
            return
        try:
            route = net_route.route_for(nerd.source())
            net_adapter.remount(session, route)
            self.client.net_route = route
        except Exception as e:
            log.warning("nerd: the client's route not updated: %s", e)

    def _nerd_lists_rebuild(self) -> None:
        """Settings closed after a change to picons or udpxy: the channel
        list again, from the source (its cached copy carries the old logos
        and addresses); the guide stays as it is."""
        if not getattr(self, "_nerd_lists_changed", False):
            return
        self._nerd_lists_changed = False
        clear = getattr(self.client, "clear_list_cache", None)
        if clear is not None:
            clear()
        self.refresh_playlist(force=False)

    def _unlock_nerd(self, in_settings: bool = False) -> None:
        """Nerd on, from About's seven taps: the page appears in Settings.
        A kids profile changes Settings only with a grown-up's PIN: the taps
        do it there in About over Settings (*in_settings*), never in the one
        the Help menu opens."""
        kids = getattr(self, "_profile_kids", None)
        if not in_settings and callable(kids) and kids():
            return
        from ..core import nerd
        already = self.settings.value("nerd_enabled", "false") == "true"
        self.settings.setValue("nerd_enabled", "true")
        nerd.install(self.settings)
        self._show_toast(tr("nerd_already") if already else tr("nerd_unlocked"),
                         4000)

    def open_source_settings(self, pid: str) -> None:
        """Settings, opened on source *pid*'s own page."""
        self._settings_source = pid
        self.open_settings()

    def open_settings(self, *_signal_args, at_page: str | None = None,
                      at_row: tuple | None = None, index_only: bool = False) -> None:
        """The Settings window - on page *at_page* and its row *at_row*
        (card, row, name) when asked (the command palette), lit for a
        moment. *index_only* builds it for ``_settings_index`` and shows
        nothing.

        Each page is built the first time it is needed - shown, reached by
        a search, or for the index - not all of them as the window opens:
        a page never built stores nothing (SettingsShell.ensure_built)."""
        # A kids profile opens Settings with a grown-up's PIN (mw_profiles).
        if not index_only and not self._kids_settings_unlock():
            return
        d = QDialog(self)
        d.setWindowTitle(tr("settings_title"))
        d.setMinimumSize(860, 600)
        # Sized from the main window, never past it: wide enough for the
        # sidebar beside a full page, no wider - the pages cap their own
        # width, so more room would only be empty margin.
        geo = self.geometry()
        d.resize(min(geo.width(), 1040),
                 min(geo.height(), max(680, int(geo.height() * 0.85))))
        # Build the window with painting suspended, re-enabled just before
        # exec(): the pages, rows and combos would otherwise each trigger
        # their own paint/polish pass during construction.
        d.setUpdatesEnabled(False)
        d.setStyleSheet(settings_style())
        outer = QVBoxLayout(d)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # A sidebar of pages with a search over it, instead of a row of ten
        # tabs that set the dialog's width (and, translated, overflowed it).
        shell = SettingsShell(tr("set_search"), tr("set_search_none"))
        shell.nav.setAccessibleName(tr("settings_title"))      # the pages, by name
        outer.addWidget(shell, 1)
        # Its sidebar as wide as it was dragged last - this computer's
        # layout, never synced or in a backup.
        shell.set_side_width(whole(self.settings.value(SIDE_KEY), shell.SIDE_W))
        shell.side_moved.connect(lambda w: self.settings.setValue(SIDE_KEY, w))

        def page(key: str, title: str, subtitle: str,
                 icon: str, tint: str | None = None, nav: str = "") -> SettingsPage:
            pg = SettingsPage(key, title, subtitle, nav)
            pg.icon_kind, pg.icon_base = icon, self._action_pixmap
            return shell.add_page(pg, page_icon(icon, self._action_pixmap, tint=tint))

        on = "true"

        def is_on(key: str, default: str) -> bool:
            return self.settings.value(key, default) == on

        def flag(box) -> str:
            return "true" if box.isChecked() else "false"

        # -- the pages, in the order the sidebar shows them (PAGES) ---------------
        # Made first, each filled by its builder below the first time it is
        # needed.
        nerd_tint = P.get("nerd", P["accent"])
        simkl = getattr(self, "simkl", None)
        simkl_offered = simkl is not None and simkl.available()
        for key, title, about_it, icon, nav, _build in PAGES:
            if key == "history" and not simkl_offered:
                about_it = "set_page_trakt_sub"
            page(key, tr(title), tr(about_it), icon, nav=tr(nav) if nav else "")
        # Nerd's pages, in its own colour; all but Status only while it is on.
        nerd_page = page("nerd", tr("nerd_pg_status"), tr("nerd_page_sub"),
                         "nerd", nerd_tint)
        nerd_pages = {part: page(key, tr(title), tr(about_it), icon, nerd_tint)
                      for part, key, title, about_it, icon in NERD_PAGES}
        # A Nerd setting has its place under NERD; the everyday page beside
        # it says so in one row that leads there, while Nerd is on.
        nerd_switch: dict = {"box": None}
        link_cards: list = []

        def nerd_on() -> bool:
            box = nerd_switch["box"]
            if box is None:              # Nerd's pages not built: as stored
                return is_on("nerd_enabled", "false")
            return box.isChecked()

        rtl = QLocale().textDirection() == Qt.LayoutDirection.RightToLeft

        def nerd_link(card, text_key: str, names: list[str], part: str) -> None:
            target = nerd_pages[part]
            row = card.link(tr(text_key, n=len(names)), " · ".join(names),
                            f"{tr('nerd_page')} {'‹' if rtl else '›'} {target.title}",
                            lambda: shell.show_page(target.key))
            card.shown_if(row, nerd_on)
            link_cards.append(card)

        def own():
            """"This profile's" before a profile's own setting, while the
            household has two profiles or more (core/profiles.py)."""
            if not self._profile_many():
                return None
            from .source_page import Origin
            mark = Origin()
            mark.set(tr("profile_own"), True)
            mark.setToolTip(tr("profile_own_tip"))
            return mark

        # What each page built stores and applies: its key -> commit(put,
        # changed, final), run by the window's commit below. A page never
        # built has none, and stores nothing.
        page_commits: dict = {}
        # What one page's code changes on another, while that one is built.
        shared: dict = {}
        def restyle_dialog() -> None:
            # The dialog's own rules are built from the palette too.
            d.setStyleSheet(settings_style())
            sync = shared.get("sync_pickers")
            if sync is not None:
                sync()
            for chip in d.findChildren(Chip):
                chip.refresh()

        # -- the sources: shared by Sources, the backup and Nerd's boxes ----
        sources = _SettingsSources(self, d, shell, shared, nerd_on)

        # -- each page's builder, run the first time the page is needed ------
        # One builder per page, one module per group (ui/set_watch.py,
        # set_content.py, set_services.py, set_system.py), and what they
        # take from here; ctx.soon is set once the change timer is made,
        # below, before any page is built.
        ctx = SettingsContext(
            win=self, dialog=d, shell=shell, pages={pg.key: pg for pg in shell.pages},
            on=on, rtl=rtl, is_on=is_on, flag=flag, own=own, nerd_link=nerd_link,
            nerd_switch=nerd_switch, link_cards=link_cards,
            page_commits=page_commits, shared=shared,
            restyle_dialog=restyle_dialog, store=sources.store,
            account_of=sources.account_of, edit_source=sources.edit,
            add_playlist=sources.add, export_playlists=sources.export,
            import_playlists=sources.import_)
        for key, *_rest, build in PAGES:
            ctx.pages[key].set_builder(lambda build=build: build(ctx))

        # -- Nerd -------------------------------------------------------------
        # Its six pages are one build (nerd_settings.build): the first of
        # them needed fills them all. Status, with the switch, is always
        # there; the rest while it is on - shown or left out from the start,
        # as stored, without building them.
        shell.set_gated("nerd", [p.key for p in nerd_pages.values()])
        shell.set_group_enabled("nerd", is_on("nerd_enabled", "false"))
        build_nerd = self._settings_nerd(ctx, nerd_page, nerd_pages, sources)
        for pg in (nerd_page, *nerd_pages.values()):
            pg.set_builder(build_nerd)

        # -- footer -----------------------------------------------------------
        footer = QWidget()
        footer.setObjectName("SettingsFooter")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(18, 12, 18, 12)
        # Changes apply as they are made, so there is nothing to confirm or
        # take back: one Close (and Esc, and the window's own close).
        close_btn = QPushButton(tr("common_close"), objectName="Primary")
        close_btn.setDefault(True)
        close_btn.clicked.connect(d.accept)
        fl.addStretch(1)
        fl.addWidget(close_btn)
        outer.addWidget(footer)

        # Grouped by what a person is doing (GROUPS) - and Nerd, in its own
        # colour.
        shell.arrange([(tr(group), list(keys)) for group, keys in GROUPS]
                      + [(tr("nerd_page"), ["nerd", *(p.key for p in nerd_pages.values())],
                          nerd_tint)])
        # Whatever changed during this visit, for what only runs on close.
        visit: set[str] = set()
        # The profile the controls were read for: a switch while the window
        # is open (the phone remote, the remote API, Home Assistant) must
        # not put this profile's own values into the next one.
        from ..core import profiles as _profiles
        built_for = _profiles.active_id()
        commit = partial(self._settings_commit, shell, page_commits, built_for, visit)

        # A change is applied a moment after it is made - long enough that a
        # held stepper button or a dragged slider applies once, not forty
        # times.
        live = QTimer(d)
        live.setSingleShot(True)
        live.setInterval(350)
        live.timeout.connect(commit)

        def soon(*_a) -> None:
            live.start()
        ctx.soon = soon

        page_built = partial(self._settings_page_built, shell, soon)

        # Back on the page that was open last time this session (built only
        # once the window is put together, below).
        shell.show_page(getattr(self, "_settings_page", None))
        if index_only:
            # Built for the index alone - every page, the ones left out too:
            # what the command palette finds among the settings, in this
            # language. Never shown, nothing to store; kept a while as every
            # closed Settings window is (_settings_closed).
            shell.build_all()
            self._settings_index = shell.index()
            self._settings_closed(d)
            return
        # Asked to open on a source's page (the source menu's entry), or on
        # a page and row (the command palette): that page is the one built.
        pending, self._settings_source = getattr(self, "_settings_source", None), None
        if pending:
            shell.show_page("playlists")
        elif at_page:
            shell.show_page(at_page)

        shell.page_built = page_built
        # The page it opens on built now; every other the first time it is
        # shown, searched or indexed.
        shell.ready()
        if at_page:
            if not shell.highlight(at_page, *(at_row or (-1, -1, ""))):
                shell.show_page(at_page)
        d.finished.connect(
            lambda _r: setattr(self, "_settings_page", shell.current_key()))
        # A source's page open: what it says is stored as Settings closes.
        d.finished.connect(lambda _r: shell.close_sub())
        # Picons and udpxy change the channel list: built again on close.
        self._nerd_lists_changed = False
        d.finished.connect(lambda _r: self._nerd_lists_rebuild())
        d.setUpdatesEnabled(True)
        if pending:
            sources.edit(pending)
        from ..i18n import current_language
        language = current_language()

        d.exec()
        live.stop()
        commit(final=True)
        # The command palette's index, read again from what this visit
        # leaves (a source added, an account signed in): from this window
        # when every page was built, else by the palette when it next opens.
        if shell.complete() and current_language() == language:
            self._settings_index = shell.index()
        else:
            self._settings_index = None
        self._settings_closed(d)


    def _settings_nerd(self, ctx: SettingsContext, nerd_page: SettingsPage,
                       nerd_pages: dict[str, SettingsPage],
                       sources: _SettingsSources) -> Callable[[], None]:
        """Nerd's six pages' one builder (nerd_settings.build): the first
        of them needed fills them all."""
        shell, d = ctx.shell, ctx.dialog
        nerd_switch, link_cards, page_commits = ctx.nerd_switch, ctx.link_cards, ctx.page_commits
        nerd_client = self.client

        def build_nerd() -> None:
            from . import nerd_settings

            def show_nerd_pages(on: bool) -> None:
                shell.set_group_enabled("nerd", on)
            nerd_commit = nerd_settings.build(
                nerd_page, self.settings, d,
                boxes=[p for p in (self.playlist_store.playlists() if self.playlist_store else [])
                       if p.get("kind") in nerd_settings.BOX_NAMES],
                edit=lambda pid: sources.edit(pid, "nerd_boxes"),
                add=lambda: sources.add({"kind": "enigma2"}),
                pages=nerd_pages, show_pages=show_nerd_pages,
                source=lambda: self.playlist_store.active() if self.playlist_store else None,
                channels=lambda: nerd_client.live_streams(None),
                rec_dir=lambda: self.rec.directory(),
                playlists=lambda: ((self.playlist_store.playlists(), self.playlist_store.active_id)
                                   if self.playlist_store else ([], None)),
                remote=self._remote_apply,
                open_page=shell.show_page,
                health_store=lambda: self.health,
                numbers=self._chno_settings,
                in_use=getattr(self, "_in_use_card", None),
                analysis=lambda: (d.accept(), QTimer.singleShot(0, self.open_ts_analysis)),
                server=getattr(self, "_server_state", None))
            # The link rows to Nerd's pages follow its switch (a turn of it
            # runs every page's filter again); shown now if it is on.
            nerd_switch["box"] = getattr(nerd_commit, "switch", None)
            for card in link_cards:
                card.refresh()

            def commit_nerd(put, changed: set, final: bool) -> None:
                nerd_commit(put)
            page_commits["nerd"] = commit_nerd

        return build_nerd

    def _settings_commit(self, shell: SettingsShell, page_commits: dict, built_for: str,
                         visit: set[str], final: bool = False) -> None:
        """Store what the controls of every page built say and apply it.
        Runs a moment after each change and once more on close. Writes
        only what differs from what is stored, so an untouched window
        changes nothing. *built_for*: the profile the controls were read
        for; *visit*: whatever changed during this visit, gathered."""
        from ..core import profiles as _profiles
        if getattr(self, "_resetting", False):
            return            # "Reset all" cleared everything: leave it
        changed: set[str] = set()

        def put(key: str, value) -> None:
            if _profiles.is_own(key) and _profiles.active_id() != built_for:
                return
            cur = self.settings.value(key)
            if cur is None or str(cur) != str(value):
                self.settings.setValue(key, value)
                changed.add(key)

        for pg in shell.pages:
            page_commit = page_commits.get(pg.key)
            if page_commit is not None:
                page_commit(put, changed, final)
        if final and "general" not in page_commits and sys.platform.startswith("linux"):
            # As when General is built: the window closed with the
            # desktop entry's switch in it answers the status-row offer.
            from ..core import desktop_entry as _de
            if _de.is_installed() or _de.can_offer():
                self.settings.setValue("desktop_entry_asked", "true")
                self.desktop_entry_btn.hide()
        if any(k.startswith("nerd_") for k in changed):
            from ..core import nerd
            nerd.install(self.settings)
            # The player that is playing takes the change now.
            self._nerd_to_player()
            # The source's own route (own DNS, IP version): the client's
            # next request takes it.
            self._nerd_route_to_client()
            if changed & {"nerd_enabled", "nerd_picon_dir", "nerd_picon_override", "nerd_udpxy"}:
                self._nerd_lists_changed = True
            if changed & {"nerd_enabled", "nerd_remote", "nerd_share", "nerd_remote_lan",
                          "nerd_remote_port", "nerd_remote_token"}:
                self._remote_apply()
            if "nerd_enabled" in changed or any(k.startswith(("nerd_hooks", "nerd_mqtt")) for k in changed):
                self._events_apply()
            if "nerd_enabled" in changed or any(k.startswith("nerd_server") for k in changed):
                self._server_apply()

        if any(k.startswith("mv_") for k in changed):
            self._apply_multiview_settings()
        if changed & set(_places.POPULAR_KEYS.values()):
            self._popular_changed()     # Movies' and Series' tops ask again
        if changed & _places.LOOK_KEYS:
            # What the places show: the sidebar and the places at once.
            self._places_look_changed("settings")
        if changed & {"metadata_source", "tmdb_api_key"}:
            self.cover.reload()
            self._popular_changed()     # TMDB on or off: the tops ask again
        if changed & PLAYER_KEYS and self.player:
            self.player.apply_default_options()
        if "show_stream_info" in changed and self.player:
            self.player._read_stream_info()
        if changed & {"view_density", "sort_order", "show_stream_info",
                      "list_channel_numbers"}:
            self._apply_view_settings()
            self.list_model.refresh_all()
        visit.update(changed)
        if final and visit:
            self._apply_view_settings()
            self.list_model.refresh_all()
            # Re-render the open detail panel so a changed "upcoming
            # count" (and other view tweaks) take effect without
            # re-selecting. On close only: re-selecting can start a
            # preview.
            cur = self.listw.currentIndex()
            if cur.isValid():
                self._on_current_changed(cur)


    def _settings_page_built(self, shell: SettingsShell, soon: Callable[..., None],
                             pg: SettingsPage) -> None:
        """A page just built: one walk over its widgets for every
        control the wheel guard and the changes applied as made need."""
        controls_of = (QComboBox, QAbstractButton, QAbstractSpinBox,
                       QAbstractSlider, QLineEdit, QPlainTextEdit)
        controls = pg.findChildren(controls_of)
        self._install_wheel_guard(pg, controls)
        for w in controls:
            if isinstance(w, QComboBox):
                w.currentIndexChanged.connect(soon)
            if isinstance(w, QAbstractButton) and w.isCheckable():
                w.toggled.connect(soon)
            if isinstance(w, (QSpinBox, QAbstractSlider)):
                w.valueChanged.connect(soon)
            if (isinstance(w, QLineEdit) and w is not shell.search
                    and not isinstance(w.parent(), QAbstractSpinBox)):
                w.editingFinished.connect(soon)
            if isinstance(w, QPlainTextEdit):
                w.textChanged.connect(soon)

    #: How long a closed Settings window is kept before it is deleted.
    SETTINGS_KEEP_MS = 60_000

    def _settings_closed(self, d: QDialog) -> None:
        """A closed (or never shown) Settings window: deleted a minute
        later. Kept that long so an answer still on its way to one of its
        rows - an account looked up as it opened, a key being tested -
        finds it there (each such answer also checks its row is still
        there). Never deleted, every window stayed behind: some 2300
        widgets each, all restyled at every theme change, which took
        seconds more with every Settings opened in a session."""
        QTimer.singleShot(self.SETTINGS_KEEP_MS, d.deleteLater)

    #: A GitHub release's body for About's notes (ui/about.py).
    _format_release_notes = staticmethod(about.format_release_notes)

    def show_about(self, *, parent: QWidget | None = None) -> None:
        """About and the update check (ui/about.py); over *parent* when
        given (Settings), else over the window."""
        about.show_about(self, parent=parent)

    # -- EPG refresh with progress -------------------------------------------------
