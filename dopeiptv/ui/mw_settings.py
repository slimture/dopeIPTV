"""Settings dialog, theme/language/view application, and About (mixin).

Split out of main_window.py verbatim; MainWindow inherits this. All methods
keep their original `self.*` access, so behaviour is unchanged. The pages'
builders are one module per group of the Settings sidebar - ui/set_watch.py,
set_content.py, set_services.py and set_system.py - each handed the
window's SettingsContext; Nerd's pages are ui/nerd_settings.py's.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QLocale, QObject, Qt, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QAbstractSlider, QAbstractSpinBox,
    QApplication,
    QComboBox, QDialog, QDialogButtonBox,
    QFileDialog,
    QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QScrollBar, QSpinBox, QTextBrowser, QVBoxLayout, QWidget,
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

from .. import APP_NAME, BUILD_VERSION, VERSION
from ..i18n import tr
from .dialogs import PlaylistDialog
from .source_page import SourcePage
from ..providers.metadata import CacheFiles
from ..providers.client import make_client
from ..core import connections
from ..core import places as _places
from ..core.log import log
from ..core.xdg import open_url
from . import set_content, set_services, set_system, set_watch
from .theme import (
    P, THEMES, apply_theme, build_style, effective_theme,
)
from .settings_ui import (
    Chip, SettingsPage, SettingsShell, page_icon, settings_style,
)
from .widgets import confirm, file_dialog_options
from ..core.updates import GITHUB_REPO, fetch_update, offered
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
    #: The supporter key, read once ({"mine": ..., "read": ...}).
    key_state: dict[str, Any]
    #: supporter_key(): the key, read the first time it is asked for.
    supporter_key: Callable[[], Any]
    #: restyle_dialog(): the window's own rules again, from the palette.
    restyle_dialog: Callable[[], None]
    #: supporter_window(): the key's window; the pages follow what it
    #: changed.
    supporter_window: Callable[[], None]
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
    #: General's backup: saved, and restored (Sources' list follows).
    export_playlists: Callable[[], None]
    import_playlists: Callable[[], None]
    #: A change made: stored and applied a moment later (set once the
    #: window's change timer is made, before any page is built).
    soon: Callable[..., None] | None = None


class _SettingsMixin:
    """MainWindow mixin: the Settings dialog, its tabs and persistence."""

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
        # Clear() drops every key from a QSettings scope; sync() flushes it to
        # disk before we tell the user to restart. Watched and Watch Later live
        # in the separate cache-settings file, and resume positions in the
        # resume file - clear all three or those survive a "reset all".
        for st in (self.settings,
                   getattr(self, "_resume_settings", None),
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

    def _backup_save(self, parent) -> None:
        """Settings > General > Backup: everything to one file."""
        from datetime import date

        from ..core import backup
        path, _f = QFileDialog.getSaveFileName(
            parent, tr("backup_title"),
            f"dopeIPTV-backup-{date.today().isoformat()}.json",
            tr("file_filter_json"),
            options=file_dialog_options())
        if not path:
            return
        doc = backup.collect(self._backup_stores(), VERSION)
        try:
            backup.write(path, doc)
        except OSError as exc:
            log.warning("backup: not written: %s", exc)
            QMessageBox.warning(parent, tr("backup_title"),
                                tr("pl_export_failed", error=exc))
            return
        counts = backup.summary(backup.parse(doc))
        log.info("backup: written (%s)", ", ".join(
            f"{k} {n}" for k, n in counts.items() if n))
        QMessageBox.information(
            parent, tr("backup_title"),
            tr("backup_done", what=self._backup_counts(counts), path=path))

    def _backup_restore(self, parent, playlists_added) -> None:
        """Settings > General > Backup: a backup back - over everything, as
        the app next starts, or its playlists beside the ones here now."""
        from ..core import backup
        from .dialogs import RestoreBackupDialog, pick_backup, stage_backup
        b, why = pick_backup(parent)
        if b is None:
            if why:
                QMessageBox.warning(parent, tr("restore_title"), why)
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
            QMessageBox.warning(parent, tr("restore_title"), why)
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

    def _unlock_nerd(self) -> None:
        """Nerd on, from About's seven taps: the page appears in Settings."""
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
        outer.addWidget(shell, 1)

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

        # -- the pages, in the order the sidebar shows them ---------------------
        # Made first, each filled by its builder below the first time it is
        # needed; each setting has one page, the one a person looks on
        # (docs/SETTINGS_MAP.md).
        nerd_tint = P.get("nerd", P["accent"])
        play = page("playback", tr("tab_playback"),
                    tr("set_page_playback_sub"), "play")
        # Sound, subtitles and picture on one page: they were two pages of
        # four and five rows, both about how a stream plays.
        audio = page("audio", tr("set_page_av"), tr("set_page_av_sub"),
                     "picture")
        guide = page("guide", tr("sec_guide"), tr("set_page_guide_sub"),
                     "guide")
        mv = page("multiview", tr("tab_multiview"),
                  tr("set_page_multiview_sub"), "grid")
        rec = page("recording", tr("tab_recording"),
                   tr("set_page_recording_sub"), "rec")
        casting = page("casting", tr("set_page_casting"),
                       tr("set_page_casting_sub"), "cast")
        playlists = page("playlists", tr("set_page_sources"),
                         tr("set_page_playlists_sub"), "stack")
        # Mine's own (the place that holds favourites, Watch later and
        # history): the series followed and how their news is said.
        mine = page("mine", tr("nav_mine"), tr("set_page_mine_sub"), "star")
        loc = page("local", tr("nav_local"), tr("local_settings_hint"),
                   "folder")
        meta = page("metadata", tr("set_page_posters"),
                    tr("set_page_metadata_sub"), "image")
        simkl = getattr(self, "simkl", None)
        simkl_offered = simkl is not None and simkl.available()
        history = page("history", tr("set_page_history"),
                       tr("set_page_history_sub" if simkl_offered
                          else "set_page_trakt_sub"), "trakt")
        # Its whole title is too long for the sidebar in most languages.
        sync_page = page("sync", tr("sync_title"), tr("set_page_sync_sub"),
                         "sync", nav=tr("sync_nav"))
        integrations = page("integrations", tr("set_page_integrations"),
                            tr("set_page_integrations_sub"), "plug")
        general = page("general", tr("set_page_general"),
                       tr("set_page_general_sub"), "gear")
        look = page("appearance", tr("set_page_appearance"),
                    tr("set_page_appearance_sub"), "palette")
        # A whole feature of its own (every action's key), for everyone: it
        # was lost at the top of Advanced.
        keys_page = page("shortcuts", tr("sc_title"),
                         tr("set_page_shortcuts_sub"), "keyboard")
        # One per person in the household (mw_profiles): a service of its
        # own, beside the parental lock its kids profiles follow.
        prof_page = page("profiles", tr("profile_page"), tr("profile_page_sub"), "person")
        par = page("parental", tr("set_page_parental"),
                   tr("set_page_parental_sub"), "lock")
        adv = page("advanced", tr("set_page_advanced"),
                   tr("set_page_advanced_sub"), "wrench")
        # Nerd's pages, in its own colour; all but Status only while it is on.
        nerd_page = page("nerd", tr("nerd_pg_status"), tr("nerd_page_sub"),
                         "nerd", nerd_tint)
        nerd_pages = {
            "net": page("nerd_net", tr("nerd_pg_net"), tr("nerd_pg_net_sub"), "network", nerd_tint),
            "boxes": page("nerd_boxes", tr("nerd_pg_boxlists"), tr("nerd_pg_boxlists_sub"), "box", nerd_tint),
            "player": page("nerd_player", tr("nerd_pg_player"), tr("nerd_pg_player_sub"), "knobs", nerd_tint),
            "serve": page("nerd_serve", tr("nerd_pg_remote"), tr("nerd_pg_remote_sub"), "remote", nerd_tint),
            "diag": page("nerd_diag", tr("nerd_pg_diag"), tr("nerd_pg_diag_sub"), "pulse", nerd_tint),
        }
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
        # The supporter key (General and Appearance): read once, again only
        # after the key's window (every check of a key is a signature check).
        key_state: dict = {"mine": None, "read": False}

        def supporter_key():
            if not key_state["read"]:
                key_state["read"] = True
                if hasattr(self, "open_supporter"):
                    key_state["mine"] = self._supporter()
            return key_state["mine"]

        def restyle_dialog() -> None:
            # The dialog's own rules are built from the palette too.
            d.setStyleSheet(settings_style())
            sync = shared.get("sync_pickers")
            if sync is not None:
                sync()
            for chip in d.findChildren(Chip):
                chip.refresh()

        def supporter_window() -> None:
            """The key's window, from the Support card or a supporter theme;
            the page follows what it changed - the card's row, the locks,
            and the palette if a supporter theme gave way."""
            before = P.get("name")
            self.open_supporter()
            from .qt_i18n import format_month
            key_state["read"] = True
            mine = key_state["mine"] = self._supporter()
            row = shared.get("supporter_row")
            if row is not None:
                key_btn, key_row, support_card = row
                key_btn.setText(tr("set_supporter_open") if mine
                                else tr("set_supporter_enter"))
                if key_row.hint_label is not None:
                    key_row.hint_label.setText(
                        tr("supporter_tip", date=format_month(mine["since"]))
                        if mine else tr("set_supporter_hint"))
                support_card.refresh()
            picked = shared.get("theme_locks")
            if picked is not None:
                sync_locks, themes = picked
                sync_locks()
                themes.set_value(self.settings.value("theme", "graphite"))
            if P.get("name") != before:
                restyle_dialog()

        # -- the sources: shared by Sources, General's backup and Nerd's boxes --
        store = self.playlist_store

        def sources_changed(pid) -> None:
            """A source added, saved or restored: the Sources page's list
            again with *pid* picked, if it is built - else the sidebar's
            switcher alone, which that list's reload also updates."""
            lst = shared.get("sources")
            if lst is not None:
                lst["reload"]()
                lst["select"](pid)
            else:
                self._update_playlist_btn()

        def account_of(pl, done) -> None:
            """The account behind source *pl* (looked up off the window's
            thread), handed to *done(info)*; the app's own connections to
            it as it is asked: what the provider counts besides them is
            another device's."""
            pid = pl.get("id")
            seen = getattr(self, "_conn_seen", None)
            own = (self._conn_own(self._conn_uses(), str(pid))
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

            run_async(self.pool, fetch, got, lambda _e: done({}))

        def source_saved(pid) -> None:
            sources_changed(pid)

        def source_left(pid, changed: bool) -> None:
            # The source in use connects again with what was changed.
            if changed and store and pid == store.active_id:
                self.switch_playlist(pid)

        def source_account(pl, done) -> None:
            if (pl.get("kind") or "xtream") == "m3u":
                return
            account_of(pl, lambda info: done(
                self._format_account(info) if (info or {}).get("user_info") else ""))

        def edit_source(pid, under=None):
            """The source's own page, in place of the page it is opened
            from (Sources, or Nerd's boxes), with a way back to it."""
            pl = store.get(pid) if (store and pid) else None
            if not pl:
                return
            under = under or "playlists"
            title = next((p.title for p in shell.pages if p.key == under), "")
            src_page = SourcePage(pl, store=store, nerd_on=nerd_on(), crumb=title,
                                  back=shell.leave_sub, open_page=shell.show_page,
                                  account=source_account, saved=source_saved,
                                  left=source_left, settings=self.settings)
            self._install_wheel_guard(src_page)
            shell.open_sub(src_page, under)

        def add_playlist(prefill=None):
            # The quick form, then on to the new source's page.
            dlg = PlaylistDialog(d, prefill=prefill)
            if dlg.exec():
                pl = store.add(dlg.values())
                sources_changed(pl["id"])
                edit_source(pl["id"], shell.current_key())

        def export_playlists():
            self._backup_save(d)

        def playlists_added() -> None:
            sources_changed(store.active_id)

        def import_playlists():
            self._backup_restore(d, playlists_added)

        # -- each page's builder, run the first time the page is needed ------
        # One builder per page, one module per group (ui/set_watch.py,
        # set_content.py, set_services.py, set_system.py), and what they
        # take from here; ctx.soon is set once the change timer is made,
        # below, before any page is built.
        ctx = SettingsContext(
            win=self, dialog=d, shell=shell, pages={pg.key: pg for pg in shell.pages},
            on=on, rtl=rtl, is_on=is_on, flag=flag, own=own, nerd_link=nerd_link,
            nerd_switch=nerd_switch, link_cards=link_cards,
            page_commits=page_commits, shared=shared, key_state=key_state,
            supporter_key=supporter_key, restyle_dialog=restyle_dialog,
            supporter_window=supporter_window, store=store,
            account_of=account_of, edit_source=edit_source,
            add_playlist=add_playlist, export_playlists=export_playlists,
            import_playlists=import_playlists)
        play.set_builder(lambda: set_watch.build_playback(ctx))
        audio.set_builder(lambda: set_watch.build_audio(ctx))
        guide.set_builder(lambda: set_watch.build_guide(ctx))
        mv.set_builder(lambda: set_watch.build_multiview(ctx))
        rec.set_builder(lambda: set_watch.build_recording(ctx))
        casting.set_builder(lambda: set_watch.build_casting(ctx))
        playlists.set_builder(lambda: set_content.build_sources(ctx))
        mine.set_builder(lambda: set_content.build_mine(ctx))
        loc.set_builder(lambda: set_content.build_local(ctx))
        meta.set_builder(lambda: set_content.build_metadata(ctx))
        history.set_builder(lambda: set_services.build_history(ctx))
        sync_page.set_builder(lambda: set_services.build_sync(ctx))
        integrations.set_builder(lambda: set_services.build_integrations(ctx))
        general.set_builder(lambda: set_system.build_general(ctx))
        look.set_builder(lambda: set_system.build_appearance(ctx))
        keys_page.set_builder(lambda: set_system.build_shortcuts(ctx))
        prof_page.set_builder(lambda: set_system.build_profiles(ctx))
        par.set_builder(lambda: set_system.build_parental(ctx))
        adv.set_builder(lambda: set_system.build_advanced(ctx))

        # -- Nerd -------------------------------------------------------------
        # Its six pages are one build (nerd_settings.build): the first of
        # them needed fills them all. Status, with the switch, is always
        # there; the rest while it is on - shown or left out from the start,
        # as stored, without building them.
        shell.set_gated("nerd", [p.key for p in nerd_pages.values()])
        shell.set_group_enabled("nerd", is_on("nerd_enabled", "false"))
        nerd_client = self.client

        def build_nerd() -> None:
            from . import nerd_settings

            def show_nerd_pages(on: bool) -> None:
                shell.set_group_enabled("nerd", on)
            nerd_commit = nerd_settings.build(
                nerd_page, self.settings, d,
                boxes=[p for p in (self.playlist_store.playlists() if self.playlist_store else [])
                       if p.get("kind") in nerd_settings.BOX_NAMES],
                edit=lambda pid: edit_source(pid, "nerd_boxes"),
                add=lambda: add_playlist({"kind": "enigma2"}),
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

        # Grouped by what a person is doing (AGENTS.md, "Where a setting
        # goes"): watching, what there is to watch, the accounts and services
        # around it, the app itself - and Nerd, in its own colour.
        shell.arrange([
            (tr("set_grp_watch"), ["playback", "audio", "guide", "multiview",
                                   "recording", "casting"]),
            (tr("set_grp_content"), ["playlists", "mine", "local",
                                     "metadata"]),
            (tr("set_grp_accounts"), ["history", "sync", "integrations"]),
            (tr("set_sec_system"), ["general", "appearance", "shortcuts",
                                    "profiles", "parental", "advanced"]),
            (tr("nerd_page"), ["nerd", *(p.key for p in nerd_pages.values())],
             nerd_tint),
        ])
        # Whatever changed during this visit, for what only runs on close.
        visit: set[str] = set()
        # The profile the controls were read for: a switch while the window
        # is open (the phone remote, the remote API, Home Assistant) must
        # not put this profile's own values into the next one.
        from ..core import profiles as _profiles
        built_for = _profiles.active_id()
        player_keys = {
            "audio_lang", "sub_mode", "sub_lang", "sub_lang2", "sub_live", "sub_scale",
            "sub_color", "sub_background", "aspect_mode",
            "video_deinterlace", "video_sharpen", "video_tonemapping"}

        def commit(final: bool = False) -> None:
            """Store what the controls of every page built say and apply it.
            Runs a moment after each change and once more on close. Writes
            only what differs from what is stored, so an untouched window
            changes nothing."""
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
            if changed & player_keys and self.player:
                self.player.apply_default_options()
            if "show_stream_info" in changed and self.player:
                self.player._read_stream_info()
            if changed & {"view_density", "sort_order", "show_stream_info"}:
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

        controls_of = (QComboBox, QAbstractButton, QAbstractSpinBox,
                       QAbstractSlider, QLineEdit, QPlainTextEdit)

        def page_built(pg: SettingsPage) -> None:
            """A page just built: one walk over its widgets for every
            control the wheel guard and the changes applied as made need."""
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
            edit_source(pending)
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

    @staticmethod
    def _format_release_notes(body: str, current: str = "",
                              update: bool = False) -> str:
        """Clean a GitHub release body for QTextBrowser's markdown renderer,
        keeping only what is new to the one reading it.

        The body carries every version back to the last big release, for
        someone who skips several on GitHub; the small box in About showed
        all of it. Up to date (*update* False), only *current*'s own notes
        are kept; with an update waiting, the versions after *current*. One
        version left, its '## dopeIPTV x.y.z' title goes too - the status
        line names it. GitHub's <details>/<summary> wrappers are stripped
        (Qt's markdown swallowed the headings inside them), and so is "On
        the AppImage ...? Click Update now": About has its own button."""
        from ..core.updates import is_newer, same_release
        body = re.sub(r"(?is)<summary>.*?</summary>", "", body)
        body = re.sub(r"(?i)</?details>", "", body)
        parts = re.split(r"(?im)^[ \t]*##[ \t]+dopeIPTV[ \t]+(\S+)[^\n]*$", body)
        sections = list(zip(parts[1::2], parts[2::2], strict=False))
        if sections:
            if current and update:
                keep = [s for s in sections if is_newer(s[0], current)]
            elif current:
                keep = [s for s in sections if same_release(s[0], current)]
            else:
                keep = []
            keep = keep or sections[:1]
            if len(keep) == 1:
                body = keep[0][1]
            else:
                body = "\n\n".join(f"## dopeIPTV {v}\n{text}" for v, text in keep)
        body = re.sub(r"(?ims)^On the AppImage from .*?(?=\n\s*\n|\Z)", "", body)
        body = re.sub(r"\n{3,}", "\n\n", body)
        return body.strip()

    def show_about(self, *, parent: QWidget | None = None) -> None:
        """About and the update check; over *parent* when given (Settings),
        else over the window. Keyword-only: a signal's argument never
        lands in it."""
        d = QDialog(parent or self)
        d.setWindowTitle(tr("menu_about"))   # "About dopeIPTV": named already
        # Wide enough for the release notes from the start: see notes below.
        d.setMinimumWidth(520)
        lay = QVBoxLayout(d)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)

        # Small, flat text-link buttons so the dialog reads as info, not a
        # wall of chunky buttons.
        link_css = (
            "QPushButton { border:none; background:transparent; padding:2px 0;"
            " font-size:12px; text-align:left; color:%s; }"
            "QPushButton:hover { color:%s; text-decoration:underline; }"
        ) % (P["muted"], P["text"])

        def link(text, url):
            b = QPushButton(text)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(link_css)
            b.clicked.connect(lambda: open_url(url))
            return b

        title = QLabel(
            f"<b style='font-size:18px'>{APP_NAME}</b>"
            f"&nbsp;&nbsp;<span style='color:{P['muted2']}'>{BUILD_VERSION}</span>")
        lay.addWidget(title)
        # Seven taps on the version, as Android's developer options: Nerd.
        taps: list[float] = []

        def tap(_event) -> None:
            import time as _time
            now = _time.monotonic()
            taps[:] = [t for t in taps if now - t < 3.0] + [now]
            if len(taps) >= 7:
                taps.clear()
                self._unlock_nerd()

        title.mousePressEvent = tap
        desc = QLabel(tr("about_desc"))
        desc.setWordWrap(True)
        lay.addWidget(desc)
        # Not the official app: a line says so (core/build_check.py); a
        # modified build's online services are off, its update check too.
        from ..core import build_check as _build
        build = _build.kind()
        if build != _build.OFFICIAL:
            modified = build == _build.MODIFIED
            line = QLabel(tr("about_build_modified" if modified else "about_build_source"))
            line.setObjectName("AboutBuild")
            line.setWordWrap(True)
            line.setStyleSheet(f"color:{P['warn'] if modified else P['muted']}; font-size:12px;"
                               + (" font-weight:600;" if modified else ""))
            lay.addWidget(line)

        # -- update section: one clear status line, with an inline re-check --
        status = QLabel(tr("about_checking"))
        status.setWordWrap(True)
        status.setStyleSheet(f"color:{P['muted']}; font-size:12px;")
        lay.addWidget(status)
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        # Roomier + a sensible minimum so the release notes are actually
        # readable when an update is offered (it scrolls past the max).
        notes.setMinimumHeight(140)
        notes.setMaximumHeight(340)
        notes.setStyleSheet(
            f"QTextBrowser {{ background:{P['input']}; color:{P['text']};"
            f" border:1px solid {P['border']};"
            f" border-radius:8px; padding:4px 8px; font-size:12px; }}")
        # Its room is kept while the check runs, so the notes never have to
        # make the dialog grow once it is up: on Wayland a dialog can stay at
        # its first size, and the notes then lay over the buttons, the links
        # and the TMDB line below them.
        keep = notes.sizePolicy()
        keep.setRetainSizeWhenHidden(True)
        notes.setSizePolicy(keep)
        notes.hide()
        lay.addWidget(notes)

        act_row = QHBoxLayout()
        act_row.setSpacing(8)
        dl_btn = QPushButton(tr("about_download"))
        dl_btn.setStyleSheet(
            "QPushButton { padding:4px 12px; font-size:12px; }")
        dl_btn.hide()
        recheck = QPushButton(tr("about_check_updates"))
        recheck.setStyleSheet(link_css)
        recheck.setCursor(Qt.CursorShape.PointingHandCursor)
        act_row.addWidget(dl_btn)
        act_row.addWidget(recheck)
        act_row.addStretch(1)
        lay.addLayout(act_row)

        # -- links + attribution ------------------------------------------
        links = QHBoxLayout()
        links.setSpacing(16)
        # ?ref=: from the app, for the site's statistics (see mw_updates).
        links.addWidget(link(tr("about_website"),
                             "https://dopeiptv.org/?ref=app-about"))
        links.addWidget(link(tr("about_github"),
                             f"https://github.com/{GITHUB_REPO}"))
        links.addWidget(link(tr("about_all_releases"),
                             f"https://github.com/{GITHUB_REPO}/releases"))
        links.addStretch(1)
        # Donating, last and in the heart's own colour (mw_donate).
        from .theme import HEART
        from .widgets import heart_pixmap
        give = link(tr("donate_support"), "")
        give.clicked.disconnect()
        give.clicked.connect(lambda: self.open_donate("app-about"))
        give.setIcon(QIcon(heart_pixmap(13, HEART)))
        give.setStyleSheet(link_css.replace(P["muted"], HEART, 1))
        links.addWidget(give)
        lay.addLayout(links)
        # A supporter's thank-you, and where a key goes (mw_donate).
        from ..core import supporter as _supporter
        from .qt_i18n import format_month
        mine = _supporter.stored(getattr(self, "settings", None))
        sup_row = QHBoxLayout()
        if mine is not None:
            mark = QLabel(f"<span style='color:{HEART}'>\u2665</span>&nbsp; "
                          + tr("supporter_tip", date=format_month(mine["since"])))
            mark.setStyleSheet(f"color:{P['text2']}; font-size:12px; font-weight:600;")
            sup_row.addWidget(mark)
        if hasattr(self, "open_supporter"):
            key_link = link(tr("supporter_menu") + "…", "")
            key_link.clicked.disconnect()
            key_link.clicked.connect(lambda: (d.accept(), self.open_supporter()))
            sup_row.addWidget(key_link)
        sup_row.addStretch(1)
        lay.addLayout(sup_row)

        credit = QLabel(tr("about_tmdb_credit"))
        credit.setWordWrap(True)
        credit.setOpenExternalLinks(True)
        credit.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        # TMDB's logo beside its credit, as its attribution rules ask.
        from .widgets import TMDB_URL, TmdbLink
        tmdb = TmdbLink()
        tmdb.clicked.connect(lambda: open_url(TMDB_URL))
        credit_row = QHBoxLayout()
        credit_row.setSpacing(10)
        credit_row.addWidget(tmdb, 0, Qt.AlignmentFlag.AlignTop)
        credit_row.addWidget(credit, 1)
        lay.addLayout(credit_row)

        # Who makes it, beside Close: the Solecraft logotype, quiet like the
        # links above (the website's footer says it the same way).
        from .widgets import SolecraftLink
        foot = QHBoxLayout()
        foot.setSpacing(6)
        made = QLabel(tr("about_made_by"))
        made.setStyleSheet(f"color:{P['muted2']}; font-size:12px;")
        foot.addWidget(made)
        maker = SolecraftLink("https://solecraft.se", P["muted"], P["text"])
        maker.clicked.connect(lambda: open_url("https://solecraft.se"))
        foot.addWidget(maker)
        foot.addStretch(1)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(d.accept)
        btns.accepted.connect(d.accept)
        foot.addWidget(btns)
        lay.addLayout(foot)

        def done(rel) -> None:
            # Opened over Settings, it goes when that window is deleted.
            if sip.isdeleted(d):
                return
            recheck.setEnabled(True)
            if not rel or not rel.get("tag"):
                status.setText(tr("about_check_failed"))
                return
            self._remember_update(rel)
            if offered(rel["tag"], VERSION, self._update_channel()):
                status.setText(tr("about_update_available", version=rel["tag"]))
                status.setStyleSheet(
                    f"color:{P['text']}; font-size:12px; font-weight:600;")
                if rel.get("body"):
                    notes.setMarkdown(self._format_release_notes(
                        rel["body"], VERSION, update=True))
                    notes.show()
                if rel.get("url"):
                    try:
                        dl_btn.clicked.disconnect()
                    except TypeError:
                        pass
                    dl_btn.setText(self._update_action_label())
                    dl_btn.clicked.connect(
                        lambda: (d.accept(), self._update_action()))
                    dl_btn.show()
            else:
                status.setText("✓ " + tr("about_up_to_date"))
                # Up to date: the room is there anyway, and the newest
                # release's notes are what this version brought.
                if rel.get("body"):
                    notes.setMarkdown(self._format_release_notes(
                        rel["body"], VERSION))
                    notes.show()
                else:
                    notes.hide()
                dl_btn.hide()

        def fail(_e) -> None:
            if sip.isdeleted(d):
                return
            recheck.setEnabled(True)
            status.setText(tr("about_check_failed"))

        def check() -> None:
            recheck.setEnabled(False)
            status.setText(tr("about_checking"))
            status.setStyleSheet(f"color:{P['muted']}; font-size:12px;")
            channel = self._update_channel()
            run_async(self.pool,
                      lambda: fetch_update(GITHUB_REPO, channel), done, fail)

        recheck.clicked.connect(check)
        if build == _build.MODIFIED:
            # Nothing is asked; the line above says why - and no room is
            # kept for release notes that never come.
            status.hide()
            recheck.hide()
            keep.setRetainSizeWhenHidden(False)
            notes.setSizePolicy(keep)
        else:
            check()               # auto-check on open
        d.exec()

    # -- EPG refresh with progress -------------------------------------------------
