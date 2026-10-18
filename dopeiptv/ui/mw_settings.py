"""Settings dialog, theme/language/view application, and About (mixin).

Split out of main_window.py verbatim; MainWindow inherits this. All methods
keep their original `self.*` access, so behaviour is unchanged.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime

import os

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView, QAbstractSlider, QAbstractSpinBox, QApplication,
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QScrollBar,
    QSpinBox, QTextBrowser, QVBoxLayout, QWidget,
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
from .epg_grid import EpgGridDialog
from ..providers.metadata import TmdbClient, bundled_tmdb_key
from ..providers.client import make_client
from ..core.log import log
from ..core.xdg import open_url
from ..core.recording import format_size
from ..media.players import embedded_playback_reason
from .theme import ACCENTS, P, THEMES, apply_theme, build_style
from .settings_ui import (
    PlaylistDelegate, ReorderList, SettingsPage, SettingsShell, hbox,
    page_icon, settings_style,
)
from .widgets import confirm
from ..core.updates import GITHUB_REPO, fetch_latest_release, is_newer
from ..core.workers import (
    clear_directory, default_image_cache_dir, dir_size_bytes, run_async)


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
            QFileDialog.Option.DontUseNativeDialog
            | QFileDialog.Option.ShowDirsOnly)
        if d:
            self.rec.set_directory(d)
            if self.mode == "rec":
                self._load_categories()

    def _clear_history(self) -> None:
        # Delete only the currently selected history category (channels /
        # movies / series), or everything when 'All' is selected.
        sub = getattr(self, "_history_subcat", None)
        kinds = self._HISTORY_KINDS.get(sub)
        if confirm(self, tr("msg_clear_history_title"),
                   tr("msg_clear_history_body")):
            if kinds:
                self.history.clear_kind(kinds)
            else:
                self.history.clear()
            self._load_items(sub)

    # -- EPG guide -----------------------------------------------------------------

    def _open_epg_guide(self, global_scope: bool = False) -> None:
        """Open the TV guide. Scoped to the current list in TV / Favorites
        mode; *global_scope* (Home's guide button) skips those - whatever
        category the hidden classic view happens to have selected is not a
        choice the user made from Home."""
        self._ensure_xmltv_loaded()
        if not global_scope and self.mode == "live" and self.all_items:
            cat = self.cat_list.currentItem()
            cat_name = cat.text() if cat else "All"
            EpgGridDialog(self, list(self.all_items), cat_name).exec()
            return
        # In Favorites, scope the guide to the favorite CHANNELS shown (a
        # folder, or all of them) - movies/series have no EPG. Other Trakt/
        # media sections simply have no live channels to guide.
        if not global_scope and self.mode == "fav":
            chans = self._favorite_channels_for_guide()
            cat = self.cat_list.currentItem()
            EpgGridDialog(self, chans,
                          cat.text().strip() if cat else tr("nav_favorites")
                          ).exec()
            return
        # No meaningful live scope (Home, Movies, Series, ...). The guide
        # used to cover an arbitrary slice here: an auto-selected first
        # category, or the first 300 of the provider's full dump. Favorite
        # channels are the lineup the user actually curated - guide those
        # when any exist; the full lineup stays the fallback.
        try:
            exclude = (() if self.parental.session_unlocked
                       else self.favs.locked_groups())
            favs = self.favs.items(None, exclude_groups=exclude)
        except Exception:
            favs = []
        if favs:
            EpgGridDialog(self, favs, tr("nav_favorites")).exec()
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("btn_epg_guide"))
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(tr("status_loading_channels")))
        dlg.resize(300, 100)
        dlg.show()

        def done(channels):
            dlg.close()
            EpgGridDialog(self, self._visible_catalog(channels, "live")).exec()

        run_async(self.pool, lambda: self.client.live_streams(None),
                  done, lambda _: dlg.close())

    def _open_epg_search(self) -> None:
        """Open the guide search (Ctrl+Shift+F): find a programme by name across
        every live channel this week and tune in or set a reminder."""
        self._ensure_xmltv_loaded()
        from .epg_search import EpgSearchDialog
        EpgSearchDialog(self).exec()

    def _open_shortcuts(self) -> None:
        """Open the keyboard-shortcuts editor: rebind any action to your own
        keys (saved live via apply_shortcuts)."""
        from .shortcuts import ShortcutsDialog
        ShortcutsDialog(self).exec()

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
            for box, key in (
                (self.size_box, density),
                (self.sort_box, self._current_sort_raw()),
            ):
                box.blockSignals(True)
                i = box.findData(key)
                if i >= 0:
                    box.setCurrentIndex(i)
                box.blockSignals(False)
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

    def _set_theme(self, theme: str, accent: str) -> None:
        self.settings.setValue("theme", theme)
        self.settings.setValue("accent", accent)
        apply_theme(self.settings)
        QApplication.instance().setStyleSheet(build_style())
        if hasattr(self, "_apply_nav_icons"):
            self._apply_nav_icons()   # re-tint the sidebar icons to the new theme
        self._sidebar_logo.update()
        self.listw.viewport().update()
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
        # The Home page bakes the accent into its pills and card borders at
        # build time, so rebuild it if it's on screen to pick up the new accent.
        if (getattr(self, "_home_page", None) is not None
                and self._home_showing()):
            self._home_page.refresh()

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
            "live": "nav_tv", "vod": "nav_movies", "series": "nav_series",
            "fav": "nav_favorites", "watchlist": "nav_watchlist",
            "watched": "nav_watched",
            "rec": "nav_recordings", "history": "nav_history",
            "local": "nav_local",
        }
        for key, btn in self.nav_btns.items():
            if key in nav_labels:
                self._nav_texts[key] = tr(nav_labels[key])
                btn.setToolTip(self._nav_texts[key])
                # On the icon rail the buttons are icon-only - don't stamp
                # text back onto them; the label lands when the rail expands.
                if not getattr(self, "_sidebar_collapsed", False):
                    btn.setText(self._nav_texts[key])
        self._cat_section_label.setText(tr("sidebar_categories"))
        if hasattr(self, "_lib_section_label"):
            self._lib_section_label.setText(tr("sidebar_library"))
        # Guide / Settings / Multiview are icon-only (their names live in the
        # tooltip). Only refresh the tooltips here - stamping text back on would
        # turn them into icon+label pills, which showed up as stray "EPG Guide"
        # / "Settings" / "Multiview" text in demo and no-provider mode.
        self._guide_btn.setToolTip(tr("btn_epg_guide"))
        self._settings_btn.setToolTip(tr("btn_settings"))
        if hasattr(self, "_multiview_btn"):
            self._multiview_btn.setToolTip(tr("menu_multiview"))
        self._update_playlist_btn()
        self.search.setPlaceholderText(tr("search_placeholder"))
        self._size_label.setText(tr("label_size"))
        self._sort_label.setText(tr("label_sort"))
        # Icon only: the 30 px poster overlay has no room for a label, and
        # stamping one on here left clipped text after a language switch.
        self.play_mpv.setToolTip(tr("tooltip_play_in_mpv"))
        # Player control tooltips (embedded bar + fullscreen overlay).
        if self.player:
            self.player.retranslate_ui()

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
    def _install_wheel_guard(d) -> None:
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
        wrapper."""
        wheel_guard = _WheelGuard(d)
        # One tree walk over all three control types (a tuple arg), not three.
        for w in d.findChildren(
                (QComboBox, QAbstractSpinBox, QAbstractSlider)):
            if isinstance(w, QScrollBar):
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
        active = ui.get("active_cons", "?")
        maxc = ui.get("max_connections", "?")
        return "     ·     ".join([
            f"{tr('account_status')}: {status or '—'}",
            f"{tr('account_expiry')}: {exp_txt}",
            f"{tr('account_connections')}: {active} / {maxc}",
        ])

    def open_settings(self) -> None:
        d = QDialog(self)
        d.setWindowTitle(tr("settings_title"))
        d.setMinimumSize(860, 600)
        # Sized from the main window, never past it: wide enough for the
        # sidebar beside a full page, no wider - the pages cap their own
        # width, so more room would only be empty margin.
        geo = self.geometry()
        d.resize(min(geo.width(), 1040),
                 min(geo.height(), max(680, int(geo.height() * 0.85))))
        # Build the whole dialog with painting suspended, re-enabled just
        # before exec(): the pages, rows and combos would otherwise each
        # trigger their own paint/polish pass during construction.
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
                 icon: str) -> SettingsPage:
            return shell.add_page(SettingsPage(key, title, subtitle),
                                  page_icon(icon, self._action_pixmap))

        on = "true"

        def is_on(key: str, default: str) -> bool:
            return self.settings.value(key, default) == on

        # -- General ----------------------------------------------------------
        from ..i18n import LANGUAGES, current_language
        general = page("general", tr("set_page_general"),
                       tr("set_page_general_sub"), "gear")
        # A normal dropdown: click to open, scroll inside the popup. _combo()
        # caps the popup at 14 rows, scrolls per pixel and keeps the
        # scrollbar visible for the 27 languages.
        lang_box = self._combo(
            [(code, name) for code, name in LANGUAGES.items()],
            current_language())
        lang_box.currentIndexChanged.connect(
            lambda _i: self._set_language(lang_box.currentData()))
        c = general.card(tr("set_card_language"))
        c.row(tr("setting_language"), lang_box,
              tr("setting_language_restart_hint"))
        c = general.card(tr("set_card_updates"))
        updates_box = c.toggle(tr("setting_check_updates"),
                               is_on("check_updates", "true"))
        # Only where an entry is actually missing: a .deb or a Flatpak has
        # one already, and offering to add a second would be nonsense.
        desktop_box = None
        if sys.platform.startswith("linux"):
            from ..core import desktop_entry as _de
            if _de.is_installed() or _de.can_offer():
                c = general.card(tr("set_card_system"))
                desktop_box = c.toggle(tr("setting_desktop_entry"),
                                       _de.is_installed(),
                                       tr("setting_desktop_entry_hint"))

        # -- Appearance -------------------------------------------------------
        look = page("appearance", tr("set_page_appearance"),
                    tr("set_page_appearance_sub"), "palette")
        theme_box = self._combo(
            [(key, tr(f"theme_{key}")) for key in THEMES],
            self.settings.value("theme", "graphite"))
        accent_box = self._combo(
            [(key, tr(f"accent_{key}")) for key in ACCENTS],
            self.settings.value("accent", "blue"))

        def restyle(_i=None) -> None:
            self._set_theme(theme_box.currentData(), accent_box.currentData())
            # The dialog's own rules are built from the palette too.
            d.setStyleSheet(settings_style())

        theme_box.currentIndexChanged.connect(restyle)
        accent_box.currentIndexChanged.connect(restyle)
        density_box = self._combo(
            [("compact", tr("option_compact")), ("medium", tr("option_medium")),
             ("large", tr("option_large")), ("xlarge", tr("option_xlarge"))],
            self.settings.value("view_density", "medium"))
        sort_box = self._combo(
            [("default", tr("option_sort_default")),
             ("alpha_asc", tr("option_sort_az")),
             ("alpha_desc", tr("option_sort_za")),
             ("recent", tr("option_sort_recent"))],
            self.settings.value("sort_order", "default"))
        c = look.card(tr("set_card_theme"))
        c.row(tr("setting_theme"), theme_box,
              tr("misc_theme_applies_immediately"))
        c.row(tr("setting_accent_color"), accent_box)
        c = look.card(tr("set_card_lists"))
        c.row(tr("setting_list_size"), density_box)
        c.row(tr("setting_sort_by"), sort_box)

        # -- Playback ---------------------------------------------------------
        play = page("playback", tr("tab_playback"),
                    tr("set_page_playback_sub"), "play")
        # How early the "Next episode" card appears. A number the user picks
        # rather than one guessed here: where the credits start varies from
        # show to show.
        nextup_box = self._combo(
            [("0", tr("option_off"))]
            + [(str(m * 60), tr("opt_minutes", n=m))
               for m in (1, 2, 3, 5, 10)],
            str(self.settings.value("nextup_lead_secs", "180")))
        fmt_box = self._combo(
            [("ts", "ts"), ("m3u8", "m3u8")],
            self.settings.value("stream_format", "ts"))
        buf_box = self._combo(
            [("1", "1 s"), ("3", "3 s"), ("5", "5 s"),
             ("10", "10 s"), ("30", "30 s")],
            str(self.settings.value("cache_secs", "10")))
        c = play.card(tr("set_card_behaviour"))
        autoplay_box = c.toggle(tr("setting_autoplay_preview"),
                                self._autoplay_preview())
        autoplay_next_box = c.toggle(
            tr("setting_autoplay_next"),
            is_on("autoplay_next_episode", "true"))
        c.row(tr("setting_nextup_lead"), nextup_box)
        autorecon_box = c.toggle(tr("setting_auto_reconnect"),
                                 is_on("auto_reconnect_live", "true"))
        # Stored inverted (hide_upcoming_prompt): the setting the user sees
        # is "ask"; the "Don't show again" box in the prompt hides it.
        upcoming_box = c.toggle(
            tr("set_upcoming_prompt"),
            self.settings.value("hide_upcoming_prompt", "false") != on)
        c = play.card(tr("set_card_stream"))
        c.row(tr("setting_stream_format"), fmt_box)
        c.row(tr("setting_network_buffer"), buf_box)
        if not self.player:
            c = play.card()
            c.note(tr("set_embedded_unavailable",
                      reason=embedded_playback_reason(localized=True)
                      or tr("set_reason_unknown")))

        # -- Audio & subtitles ------------------------------------------------
        audio = page("audio", tr("sec_audio_subs"),
                     tr("set_page_audio_sub"), "subtitles")
        LANGS = [
            ("", tr("option_lang_auto")), ("swe", tr("lang_swe")),
            ("eng", tr("lang_eng")), ("nor", tr("lang_nor")),
            ("dan", tr("lang_dan")), ("fin", tr("lang_fin")),
            ("ger", tr("lang_ger")), ("fre", tr("lang_fre")),
            ("spa", tr("lang_spa")), ("ita", tr("lang_ita")),
            ("por", tr("lang_por")), ("pol", tr("lang_pol")),
            ("ara", tr("lang_ara")), ("tur", tr("lang_tur")),
        ]
        alang_box = self._combo(LANGS, self.settings.value("audio_lang", ""))
        sub_box = self._combo(
            [("off", tr("option_sub_off")), ("auto", tr("option_sub_auto")),
             ("lang", tr("option_sub_lang")),
             ("forced", tr("option_sub_forced"))],
            self.settings.value("sub_mode", "auto"))
        slang_box = self._combo(LANGS, self.settings.value("sub_lang", ""))
        slang2_box = self._combo(LANGS, self.settings.value("sub_lang2", ""))
        c = audio.card()
        c.row(tr("setting_audio_lang"), alang_box)
        c.row(tr("setting_subtitles"), sub_box)
        c.row(tr("setting_sub_lang"), slang_box)
        c.row(tr("setting_sub_lang_fallback"), slang2_box)

        # -- Video ------------------------------------------------------------
        video = page("video", tr("sec_video"), tr("set_page_video_sub"),
                     "picture")
        aspect_box = self._combo(
            [("auto", tr("option_aspect_auto")), ("16:9", "16:9"),
             ("4:3", "4:3"), ("2.35:1", "2.35:1"),
             ("stretch", tr("option_aspect_stretch"))],
            self.settings.value("aspect_mode", "auto"))
        sharpen_box = self._combo(
            [("0.0", tr("option_off")), ("0.5", tr("option_low")),
             ("1.0", tr("option_medium")), ("2.0", tr("option_high"))],
            str(self.settings.value("video_sharpen", "0.0")))
        tonemap_box = self._combo(
            [("auto", tr("option_tonemap_auto")), ("hable", "Hable"),
             ("mobius", "Mobius"), ("reinhard", "Reinhard"),
             ("bt.2390", "BT.2390"), ("clip", tr("option_tonemap_clip"))],
            self.settings.value("video_tonemapping", "auto"))
        hwdec_box = self._combo(
            [("no", tr("option_hwdec_off")),
             ("auto-copy-safe", tr("option_hwdec_safe")),
             ("auto-safe", tr("option_hwdec_direct"))],
            str(self.settings.value("hwdec_mode", "") or "no"))
        c = video.card()
        c.row(tr("setting_aspect_ratio"), aspect_box)
        deint_box = c.toggle(tr("setting_deinterlace"),
                             is_on("video_deinterlace", "false"))
        c.row(tr("setting_sharpen"), sharpen_box)
        c.row(tr("setting_tonemapping"), tonemap_box)
        c = video.card(tr("set_card_decoding"))
        c.row(tr("setting_hwdec"), hwdec_box, tr("setting_hwdec_hint"))

        # -- Programme guide --------------------------------------------------
        guide = page("guide", tr("sec_guide"), tr("set_page_guide_sub"),
                     "guide")
        epg_count_box = QSpinBox()
        epg_count_box.setRange(self.EPG_UPCOMING_MIN, self.EPG_UPCOMING_MAX)
        epg_count_box.setValue(self._epg_upcoming_count())
        epg_count_box.setFixedWidth(90)

        def delay_row(key: str):
            try:
                total = int(self.settings.value(key, 0))
            except (TypeError, ValueError):
                total = 0
            sign_box = self._combo(
                [("+", tr("opt_delay_later")), ("-", tr("opt_delay_earlier"))],
                "-" if total < 0 else "+")
            sign_box.setMinimumWidth(0)
            hours, minutes = divmod(abs(total), 60)
            hours_box = QSpinBox()
            hours_box.setRange(0, 23)
            hours_box.setSuffix(" h")
            hours_box.setValue(hours)
            minutes_box = QSpinBox()
            minutes_box.setRange(0, 59)
            minutes_box.setSuffix(" m")
            minutes_box.setValue(minutes)
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(sign_box, 1)
            row.addWidget(hours_box)
            row.addWidget(minutes_box)
            return row, sign_box, hours_box, minutes_box

        (replay_delay_row, replay_sign_box, replay_hours_box,
         replay_minutes_box) = delay_row("replay_delay_min")
        (epg_delay_row, epg_sign_box, epg_hours_box,
         epg_minutes_box) = delay_row("epg_delay_min")
        c = guide.card()
        c.row(tr("setting_upcoming_count"), epg_count_box)
        c = guide.card(tr("set_card_offsets"))
        c.row(tr("setting_replay_delay"), replay_delay_row,
              tr("set_replay_delay_hint"))
        c.row(tr("setting_epg_delay"), epg_delay_row,
              tr("set_epg_delay_hint"))
        refresh_epg_btn = QPushButton(tr("btn_refresh_epg"))
        clear_epg_btn = QPushButton(tr("btn_clear_epg"))
        refresh_epg_btn.clicked.connect(self._refresh_epg_now)
        clear_epg_btn.clicked.connect(self._clear_epg_cache)
        c = guide.card(tr("set_card_guide_data"))
        c.layout_row(hbox(refresh_epg_btn, clear_epg_btn),
                     tr("setting_epg_cache"))

        # -- Playlists --------------------------------------------------------
        playlists = page("playlists", tr("tab_playlists"),
                         tr("set_page_playlists_sub"), "stack")
        store = self.playlist_store
        pl_list = ReorderList()
        pl_list.setObjectName("PlaylistList")
        pl_list.setItemDelegate(PlaylistDelegate(
            tr("pl_in_use"), {"xtream": "Xtream", "m3u": "M3U"}, pl_list))
        pl_list.setMouseTracking(True)
        pl_list.setMinimumHeight(200)
        # Drag a playlist to change its place; the sidebar's playlist menu
        # lists them in this same order.
        pl_list.setStyleSheet(
            "QListWidget#PlaylistList { background: transparent; }"
            "QListWidget#PlaylistList::item { padding: 0; margin: 0; }")
        add_btn = QPushButton(tr("btn_add"))
        edit_btn = QPushButton(tr("btn_edit"))
        remove_btn = QPushButton(tr("btn_remove"))
        use_btn = QPushButton(tr("btn_use"), objectName="Primary")
        c = playlists.card()
        c.widget(pl_list, tr("tab_playlists"))
        c.layout_row(hbox(add_btn, edit_btn, remove_btn, None, use_btn))
        c.note(tr("pl_drag_hint"))
        # Account status for the selected Xtream provider (expiry,
        # connections). Follows the list selection so you can check any
        # provider without switching to it.
        # Refresh reloads the playlist in use, so it is offered while that
        # one is selected.
        refresh_pl_btn = QPushButton(tr("btn_refresh"))
        refresh_pl_btn.setToolTip(tr("tooltip_reload_channels_epg"))
        c = playlists.card(tr("set_card_selected_playlist"))
        account_lbl = c.row(tr("set_account"), refresh_pl_btn,
                            tr("account_loading")).hint_label
        export_btn = QPushButton(tr("btn_export"))
        export_btn.setToolTip(tr("settings_export_tip"))
        import_btn = QPushButton(tr("btn_import"))
        import_btn.setToolTip(tr("settings_import_tip"))
        c = playlists.card(tr("set_card_backup"))
        c.row(tr("set_backup_label"), hbox(export_btn, import_btn,
                                           stretch=False),
              tr("set_backup_hint"))

        # -- Home -------------------------------------------------------------
        home = page("home", tr("nav_home"), tr("set_page_home_sub"), "home")
        c = home.card()
        home_show_box = c.toggle(tr("set_home_show"),
                                 is_on("home_enabled", "true"))
        home_start_box = c.toggle(tr("set_home_start"),
                                  is_on("home_start", "false"))
        c = home.card(tr("set_home_shelves"))
        home_shelf_boxes: dict[str, QCheckBox] = {}
        for skey, slabel in (("home_sh_resume", "home_resume"),
                             ("home_sh_fav", "home_fav_now"),
                             ("home_sh_history", "home_recent"),
                             ("home_sh_watchlist", "nav_watchlist"),
                             ("home_sh_movies", "home_new_movies"),
                             ("home_sh_series", "home_new_series"),
                             ("home_sh_channels", "home_new_channels")):
            home_shelf_boxes[skey] = c.toggle(tr(slabel), is_on(skey, "true"))

        # -- Multiview --------------------------------------------------------
        mv = page("multiview", tr("tab_multiview"),
                  tr("set_page_multiview_sub"), "grid")
        c = mv.card(tr("mv_sec_window"))
        mv_titlebar_box = c.toggle(tr("mv_set_titlebar"),
                                   not is_on("mv_frameless", "true"))
        mv_ontop_box = c.toggle(tr("mv_set_on_top"),
                                is_on("mv_on_top", "false"))
        mv_geo_box = c.toggle(tr("mv_set_remember_geo"),
                              is_on("mv_remember_geo", "true"))
        mv_cells_box = QComboBox()
        for n, label in ((2, "2  (1 × 2)"), (4, "4  (2 × 2)"),
                         (6, "6  (2 × 3)"), (9, "9  (3 × 3)")):
            mv_cells_box.addItem(label, n)
        try:
            cur_cells = int(self.settings.value("mv_cells", 4))
        except (TypeError, ValueError):
            cur_cells = 4
        mv_cells_box.setCurrentIndex(max(0, mv_cells_box.findData(cur_cells)))
        c.row(tr("mv_set_cells"), mv_cells_box)
        c = mv.card(tr("mv_sec_behavior"))
        mv_stop_box = c.toggle(tr("mv_set_stop_docked"),
                               is_on("mv_stop_docked", "true"))
        mv_unmute_box = c.toggle(tr("mv_set_new_unmuted"),
                                 is_on("mv_new_unmuted", "true"))
        mv_conflict_box = QComboBox()
        mv_conflict_box.addItem(tr("mv_conflict_ask"), "ask")
        mv_conflict_box.addItem(tr("mv_close"), "close")
        mv_conflict_box.addItem(tr("mv_keep"), "keep")
        cur_mode = str(self.settings.value("mv_conflict_mode", "ask"))
        mv_conflict_box.setCurrentIndex(
            max(0, mv_conflict_box.findData(cur_mode)))
        c.row(tr("mv_set_conflict"), mv_conflict_box)
        c = mv.card(tr("mv_sec_controls"))
        mv_hide_spin = QSpinBox()
        mv_hide_spin.setRange(1, 15)
        try:
            mv_hide_spin.setValue(
                int(float(self.settings.value("mv_autohide_secs", 2))))
        except (TypeError, ValueError):
            mv_hide_spin.setValue(2)
        mv_hide_spin.setFixedWidth(90)
        c.row(tr("mv_set_autohide"), mv_hide_spin)
        mv_step_spin = QSpinBox()
        mv_step_spin.setRange(1, 60)
        try:
            mv_step_spin.setValue(
                int(float(self.settings.value("mv_seek_step_min", 5))))
        except (TypeError, ValueError):
            mv_step_spin.setValue(5)
        mv_step_spin.setFixedWidth(90)
        c.row(tr("mv_set_seek_step"), mv_step_spin)
        mv_info_btn = QPushButton(tr("mv_set_reset_info"))
        mv_info_btn.clicked.connect(
            lambda: self.settings.setValue("mv_info_seen", "false"))
        c.layout_row(hbox(mv_info_btn))

        # -- Recording --------------------------------------------------------
        rec = page("recording", tr("tab_recording"),
                   tr("set_page_recording_sub"), "rec")
        rec_dir_btn = QPushButton(tr("btn_choose_folder"))
        c = rec.card(tr("set_card_folder"))
        rec_dir_row = c.row(tr("rec_saved_in"), rec_dir_btn,
                            self.rec.directory())

        def pick_rec_dir():
            path = QFileDialog.getExistingDirectory(
                d, tr("rec_choose_folder_title"), self.rec.directory(),
                QFileDialog.Option.DontUseNativeDialog
                | QFileDialog.Option.ShowDirsOnly)
            if path:
                self.rec.set_directory(path)
                rec_dir_row.hint_label.setText(path)
                if self.mode == "rec":
                    self._load_categories()

        rec_dir_btn.clicked.connect(pick_rec_dir)
        rec_max_edit = QLineEdit(
            str(self.settings.value("rec_max_value", "")))
        rec_max_edit.setPlaceholderText(tr("ph_no_limit"))
        rec_max_edit.setMaximumWidth(110)
        rec_max_unit = self._combo(
            [("MB", "MB"), ("GB", "GB"), ("TB", "TB")],
            self.settings.value("rec_max_unit", "GB"))
        rec_total_edit = QLineEdit(
            str(self.settings.value("rec_total_value", "")))
        rec_total_edit.setPlaceholderText(tr("ph_no_limit"))
        rec_total_edit.setMaximumWidth(110)
        rec_total_unit = self._combo(
            [("GB", "GB"), ("TB", "TB")],
            self.settings.value("rec_total_unit", "GB"))
        c = rec.card(tr("set_card_limits"))
        c.row(tr("rec_max_label"), hbox(rec_max_edit, rec_max_unit,
                                        stretch=False))
        c.row(tr("rec_total_label"), hbox(rec_total_edit, rec_total_unit,
                                          stretch=False))
        # Pausing a cast channel records it here until you press play, so
        # this is the one setting that decides how long a pause may be.
        cast_pause_edit = QLineEdit(
            str(self.settings.value("cast_pause_gb", "4.5")))
        cast_pause_edit.setMaximumWidth(110)
        c = rec.card(tr("set_card_cast"))
        c.row(tr("cast_pause_label"), hbox(cast_pause_edit, QLabel("GB"),
                                           stretch=False),
              tr("cast_pause_hint"))
        rk, rexe = self.rec.recorder()
        c = rec.card()
        c.note(tr("rec_recorder_found", kind=rk, path=rexe) if rexe
               else tr("rec_recorder_missing"))
        c.note(tr("rec_howto_hint"))

        # -- Local files ------------------------------------------------------
        loc = page("local", tr("nav_local"), tr("local_settings_hint"),
                   "folder")
        loc_list = QListWidget()
        loc_list.setMinimumHeight(120)
        for folder in self._local_dirs():
            loc_list.addItem(folder)
        loc_add = QPushButton(tr("btn_add"))
        loc_del = QPushButton(tr("btn_remove"))
        loc_empty = QLabel(tr("set_local_none"))
        loc_empty.setObjectName("SettingsHint")
        c = loc.card(tr("set_card_folders"))
        c.widget(loc_empty)
        c.widget(loc_list)
        c.layout_row(hbox(loc_add, loc_del))

        def loc_empty_state() -> None:
            empty = loc_list.count() == 0
            loc_empty.setVisible(empty)
            loc_list.setVisible(not empty)
            loc_del.setEnabled(not empty)

        loc_list.model().rowsInserted.connect(lambda *_a: loc_empty_state())
        loc_list.model().rowsRemoved.connect(lambda *_a: loc_empty_state())
        loc_empty_state()
        c = loc.card(tr("set_card_music"))
        art_chk = c.toggle(tr("local_art_online"),
                           is_on("music_art_online", "true"))
        art_chk.toggled.connect(
            lambda checked: self.settings.setValue(
                "music_art_online", "true" if checked else "false"))
        loc_clear_lib = QPushButton(tr("local_clear_lib_cache"))
        loc_clear_post = QPushButton(tr("local_clear_posters"))
        loc_clear_thumb = QPushButton(tr("local_clear_thumbs"))
        c = loc.card(tr("set_card_caches"))
        c.layout_row(hbox(loc_clear_lib, loc_clear_post, loc_clear_thumb))
        loc_note = c.note(tr("set_caches_hint"))

        def loc_refresh_side():
            # Reflect a folder change in the section immediately.
            if self.mode == "local":
                self.cat_list.blockSignals(True)
                self.cat_list.clear()
                self.cat_list.blockSignals(False)
                self._load_local_categories()

        def loc_add_dir():
            start = (self.settings.value("local_open_dir", "")
                     or os.path.expanduser("~"))
            path = QFileDialog.getExistingDirectory(
                d, tr("local_add_folder_title"), start,
                QFileDialog.Option.DontUseNativeDialog
                | QFileDialog.Option.ShowDirsOnly)
            dirs = self._local_dirs()
            if path and path not in dirs:
                self._save_local_dirs(dirs + [path])
                loc_list.addItem(path)
                loc_refresh_side()

        def loc_del_dir():
            it = loc_list.currentItem()
            if it is None:
                return
            self._save_local_dirs(
                [d for d in self._local_dirs() if d != it.text()])
            loc_list.takeItem(loc_list.row(it))
            loc_refresh_side()

        def loc_cleared():
            loc_note.setText(tr("local_cleared"))

        loc_add.clicked.connect(loc_add_dir)
        loc_del.clicked.connect(loc_del_dir)
        loc_clear_lib.clicked.connect(
            lambda: (self._local_clear_library_cache(), loc_cleared()))
        loc_clear_post.clicked.connect(
            lambda: (self._local_clear_poster_cache(), loc_cleared()))
        loc_clear_thumb.clicked.connect(
            lambda: (self._local_clear_thumbs(), loc_cleared()))

        # -- Metadata (TMDB artwork) ------------------------------------------
        meta = page("metadata", tr("tab_metadata"),
                    tr("set_page_metadata_sub"), "image")
        _bundled = bool(bundled_tmdb_key())
        # Source options: the built-in key (only offered when one ships),
        # the user's own key, or the provider's own artwork. The key field
        # appears only for the "own key" choice.
        _options = []
        if _bundled:
            _options.append(("tmdb", tr("meta_src_builtin")))
        _options.append(("custom", tr("meta_src_own")))
        _options.append(("playlist", tr("meta_src_provider")))
        _default_src = self.settings.value(
            "metadata_source",
            "tmdb" if _bundled else
            ("custom" if self.settings.value("tmdb_api_key", "")
             else "playlist"))
        meta_source_box = self._combo(_options, _default_src)
        tmdb_key_edit = QLineEdit(self.settings.value("tmdb_api_key", ""))
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
        c.note(tr("meta_hint_builtin") if _bundled
               else tr("meta_hint_own"))

        def custom_source() -> bool:
            return meta_source_box.currentData() == "custom"

        c.shown_if(key_row, custom_source)
        c.shown_if(status_row, custom_source)
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
                tmdb_status.setText(tr("tmdb_key_works"))
                tmdb_status.setStyleSheet(
                    f"color:{P['accent']}; font-size:12px; font-weight:600;")

            def fail(msg):
                tmdb_status.setText(tr("tmdb_key_failed", msg=msg))
                tmdb_status.setStyleSheet(
                    f"color:{P['error']}; font-size:12px;")

            run_async(self.pool, check, ok, fail)

        meta_source_box.currentIndexChanged.connect(
            lambda _i: meta_card.refresh())
        tmdb_test_btn.clicked.connect(test_tmdb_key)

        # -- Trakt ------------------------------------------------------------
        trakt = page("trakt", tr("tab_trakt"), tr("set_page_trakt_sub"),
                     "trakt")
        trakt_browser_btn = QPushButton(tr("trakt_connect_browser"),
                                        objectName="Primary")
        trakt_disconnect_btn = QPushButton(tr("trakt_disconnect"))
        trakt_watchlist_btn = QPushButton(tr("trakt_watchlist_btn"))
        c = trakt.card(tr("set_card_account"))
        trakt_row = c.row(tr("trakt_not_connected"),
                          hbox(trakt_browser_btn, trakt_disconnect_btn,
                               stretch=False),
                          tr("trakt_connect_browser_hint"))
        c.row(tr("set_trakt_lists"), trakt_watchlist_btn,
              tr("trakt_scrobble_hint"))
        sync_now_btn = QPushButton(tr("trakt_sync_now"))
        c = trakt.card(tr("set_card_watched_history"))
        sync_row = c.row(tr("trakt_sync_never"), sync_now_btn,
                         tr("trakt_sync_hint"))

        def refresh_sync_status():
            n_m, n_e = (len(self.watched.movies),
                        sum(len(v) for v in self.watched.episodes.values()))
            if self.watched.last_sync_at:
                when = datetime.fromtimestamp(
                    self.watched.last_sync_at).strftime("%Y-%m-%d %H:%M")
                sync_row.label.setText(
                    tr("trakt_sync_status", when=when,
                       movies=n_m, episodes=n_e))
            else:
                sync_row.label.setText(tr("trakt_sync_never"))
            sync_now_btn.setEnabled(
                self.trakt.is_connected()
                and not self._watched_sync_running)

        def do_sync_now():
            sync_now_btn.setEnabled(False)
            sync_row.label.setText(tr("trakt_syncing"))
            # Kick a forced sync and re-poll status while it runs so the
            # label flips to "synced" without reopening the dialog.
            self._maybe_sync_watched(force=True)
            poll = QTimer(d)

            def tick():
                if not self._watched_sync_running:
                    poll.stop()
                    refresh_sync_status()
            poll.timeout.connect(tick)
            poll.start(400)

        sync_now_btn.clicked.connect(do_sync_now)

        def refresh_trakt_status():
            connected = self.trakt.is_connected()
            trakt_row.label.setText(tr("trakt_connected") if connected
                                    else tr("trakt_not_connected"))
            # One of the two: connect while signed out, disconnect while in.
            trakt_browser_btn.setVisible(not connected)
            trakt_disconnect_btn.setVisible(connected)
            trakt_row.hint_label.setVisible(not connected)
            trakt_watchlist_btn.setEnabled(connected)
            refresh_sync_status()

        def do_connect_browser():
            # Sign in through the browser with whatever Trakt app is active
            # (the built-in one unless the user saved their own).
            self._trakt_browser_auth_dialog(d)
            refresh_trakt_status()

        def do_trakt_disconnect():
            self.trakt.disconnect()
            refresh_trakt_status()

        trakt_browser_btn.clicked.connect(do_connect_browser)
        trakt_disconnect_btn.clicked.connect(do_trakt_disconnect)
        trakt_watchlist_btn.clicked.connect(
            lambda: self._open_trakt_dialog(d))
        refresh_trakt_status()

        # -- Parental controls ------------------------------------------------
        par = page("parental", tr("tab_parental"),
                   tr("set_page_parental_sub"), "lock")
        set_pin_btn = QPushButton(tr("pin_set_change"))
        remove_pin_btn = QPushButton(tr("pin_remove"))
        lock_now_btn = QPushButton(tr("pin_lock_now"))
        c = par.card(tr("set_card_pin"))
        pin_row = c.row(tr("pin_none_set"), None)
        c.layout_row(hbox(set_pin_btn, remove_pin_btn, lock_now_btn))
        c.note(tr("parental_hint"))

        def refresh_pin_status():
            if self.parental.has_pin():
                pin_row.label.setText(tr("pin_set_unlocked")
                                      if self.parental.session_unlocked
                                      else tr("pin_set_locked"))
            else:
                pin_row.label.setText(tr("pin_none_set"))
            remove_pin_btn.setEnabled(self.parental.has_pin())
            lock_now_btn.setEnabled(
                self.parental.has_pin()
                and self.parental.session_unlocked)

        def set_pin():
            if self.parental.has_pin() and not self._request_unlock():
                return
            pin, ok = QInputDialog.getText(
                d, tr("parental_control"), tr("pin_new_prompt"),
                QLineEdit.EchoMode.Password)
            pin = (pin or "").strip()
            if ok and pin:
                self.parental.set_pin(pin)
            refresh_pin_status()

        def remove_pin():
            if not self._request_unlock():
                return
            self.parental.clear_pin()
            refresh_pin_status()
            self._load_categories()

        def lock_now():
            self.parental.lock_session()
            refresh_pin_status()
            self._load_categories()

        set_pin_btn.clicked.connect(set_pin)
        remove_pin_btn.clicked.connect(remove_pin)
        lock_now_btn.clicked.connect(lock_now)
        refresh_pin_status()

        # -- Advanced ---------------------------------------------------------
        adv = page("advanced", tr("set_page_advanced"),
                   tr("set_page_advanced_sub"), "wrench")
        shortcuts_btn = QPushButton(tr("btn_edit"))
        shortcuts_btn.clicked.connect(self._open_shortcuts)
        # Disk-cache controls: covers/logos accumulate under
        # QStandardPaths.CacheLocation and don't clean themselves. The live
        # cache is the shared "images" dir; the two legacy dirs predate the
        # loaders sharing one directory - keep them in the sweep so files
        # from old sessions still get counted and cleared.
        cache_dirs = [default_image_cache_dir("images"),
                      default_image_cache_dir("logos"),
                      default_image_cache_dir("posters")]
        clear_cache_btn = QPushButton(tr("btn_clear"))
        clear_cache_btn.setAccessibleName(tr("settings_image_cache_clear"))
        ts_reset_btn = QPushButton(tr("common_reset"))
        ts_reset_btn.setAccessibleName(tr("ts_reset_broken"))
        c = adv.card(tr("sec_maintenance"))
        c.row(tr("sc_title"), shortcuts_btn, tr("set_shortcuts_hint"))
        cache_row = c.row(tr("settings_image_cache_label", size="…"),
                          clear_cache_btn, tr("settings_image_cache_hint"))
        c.row(tr("ts_reset_broken"), ts_reset_btn, tr("ts_reset_hint"))

        def refresh_cache_label() -> None:
            total = sum(dir_size_bytes(d) for d in cache_dirs)
            cache_row.label.setText(
                tr("settings_image_cache_label", size=format_size(total)))
            clear_cache_btn.setEnabled(total > 0)

        def clear_cache() -> None:
            for cdir in cache_dirs:
                clear_directory(cdir)
            # Also drop the in-memory LRUs so a re-scroll doesn't just
            # rewrite the same pixmaps back to disk from RAM.
            self.logos.cache.clear()
            self.poster_art.cache.clear()
            refresh_cache_label()

        clear_cache_btn.clicked.connect(clear_cache)

        def reset_ts() -> None:
            self._clear_ts_broken()
            self._flash_status(tr("ts_reset_done"))

        ts_reset_btn.clicked.connect(reset_ts)
        # Portable Windows build: let the user add Start-menu/desktop
        # shortcuts so it feels installed. Each is just a .lnk file (no
        # registry), easy to delete. Hidden on Linux/macOS.
        if sys.platform == "win32":
            win_sc_btn = QPushButton(tr("win_shortcut_btn"))

            def _make_win_shortcut() -> None:
                from ..core.platform_windows import create_shortcut
                made = create_shortcut()
                self._flash_status(tr("win_shortcut_done") if made
                                   else tr("win_shortcut_fail"))

            win_sc_btn.clicked.connect(_make_win_shortcut)
            c.row(tr("win_shortcut_hint"), win_sc_btn)
        refresh_cache_label()
        x11_box = None
        if sys.platform.startswith("linux"):
            c = adv.card(tr("set_card_system"))
            x11_box = c.toggle(tr("setting_force_x11"),
                               is_on("force_x11", "false"),
                               tr("setting_force_x11_hint"))
        reset_btn = QPushButton(tr("set_reset_btn"), objectName="Danger")
        reset_btn.clicked.connect(lambda: self._reset_all_settings(d))
        c = adv.card(tr("set_card_reset"))
        c.row(tr("set_reset_label"), reset_btn, tr("set_reset_hint"))

        # -- the playlist page's behaviour -------------------------------------
        def reload_pl_list():
            pl_list.clear()
            # The sidebar switcher chip appears/disappears with the second
            # playlist - reflect adds/removes right away.
            self._update_playlist_btn()
            if not store:
                item = QListWidgetItem(tr("pl_mgmt_unavailable"))
                item.setData(PlaylistDelegate.ROLE,
                             {"name": tr("pl_mgmt_unavailable")})
                pl_list.addItem(item)
                pl_list.setDragDropMode(
                    QAbstractItemView.DragDropMode.NoDragDrop)
                return
            for p in store.playlists():
                item = QListWidgetItem(p.get("name", ""))
                item.setData(Qt.ItemDataRole.UserRole, p["id"])
                item.setData(PlaylistDelegate.ROLE, {
                    "name": p.get("name", ""),
                    "server": p.get("server", ""),
                    "kind": p.get("kind") or "xtream",
                    "active": p["id"] == store.active_id})
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
                pl_list.addItem(item)

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

            def fetch(pl=pl):
                try:
                    return make_client(pl).account_info()
                except Exception:
                    return {}

            def done(info, pid=pid):
                # Ignore a stale answer if the selection moved on meanwhile.
                if selected_pid() == pid:
                    account_lbl.setText(self._format_account(info))

            run_async(
                self.pool, fetch, done,
                lambda _e: account_lbl.setText(tr("account_unavailable")))

        pl_list.currentItemChanged.connect(
            lambda *_: refresh_account(selected_pid()))

        def playlist_moved(*_a) -> None:
            if not store:
                return
            order = [pl_list.item(i).data(Qt.ItemDataRole.UserRole)
                     for i in range(pl_list.count())]
            store.reorder([pid for pid in order if pid])
            self._update_playlist_btn()

        pl_list.reordered.connect(playlist_moved)

        def add_playlist():
            dlg = PlaylistDialog(d)
            if dlg.exec():
                pl = store.add(dlg.values())
                reload_pl_list()
                select_pid(pl["id"])

        def edit_playlist():
            pid = selected_pid()
            pl = store.get(pid) if (store and pid) else None
            if not pl:
                return
            dlg = PlaylistDialog(d, pl)
            if dlg.exec():
                store.update(pid, **dlg.values())
                reload_pl_list()
                select_pid(pid)
                if pid == store.active_id:
                    self.switch_playlist(pid)

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
                    self.switch_playlist(store.active_id)
                    reload_pl_list()
                select_pid(store.active_id)

        def use_playlist():
            pid = selected_pid()
            if store and pid and pid != store.active_id:
                self.switch_playlist(pid)
                reload_pl_list()
                select_pid(pid)

        def export_playlists():
            if not store or not store.playlists():
                QMessageBox.information(
                    d, tr("pl_export_title"), tr("pl_export_none"))
                return
            path, _ = QFileDialog.getSaveFileName(
                d, tr("pl_export_title"), "playlists.json",
                tr("file_filter_json"),
                options=QFileDialog.Option.DontUseNativeDialog)
            if not path:
                return
            import json
            data = store.export_data()
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            except OSError as exc:
                QMessageBox.warning(d, tr("pl_export_title"),
                                    tr("pl_export_failed", error=exc))
                return
            QMessageBox.information(
                d, tr("pl_export_title"),
                tr("pl_export_done", n=len(data), path=path))

        def import_playlists():
            if not store:
                return
            path, _ = QFileDialog.getOpenFileName(
                d, tr("pl_import_title"), "",
                tr("file_filter_json") + ";;" + tr("file_filter_all"),
                options=QFileDialog.Option.DontUseNativeDialog)
            if not path:
                return
            import json
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(d, tr("pl_import_title"),
                                    tr("pl_import_unreadable", error=exc))
                return
            try:
                added = store.import_data(data)
            except ValueError:
                QMessageBox.warning(d, tr("pl_import_title"),
                                    tr("pl_import_invalid"))
                return
            reload_pl_list()
            select_pid(store.active_id)
            QMessageBox.information(d, tr("pl_import_title"),
                                    tr("pl_import_done", n=added))

        add_btn.clicked.connect(add_playlist)
        edit_btn.clicked.connect(edit_playlist)
        remove_btn.clicked.connect(remove_playlist)
        refresh_pl_btn.clicked.connect(self.refresh_playlist)
        use_btn.clicked.connect(use_playlist)
        export_btn.clicked.connect(export_playlists)
        import_btn.clicked.connect(import_playlists)
        pl_list.itemDoubleClicked.connect(lambda _i: edit_playlist())
        if not store:
            for b in (add_btn, edit_btn, remove_btn,
                      refresh_pl_btn, use_btn, export_btn, import_btn):
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

        # -- footer -----------------------------------------------------------
        footer = QWidget()
        footer.setObjectName("SettingsFooter")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(18, 12, 18, 12)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        for b in buttons.buttons():
            b.setIcon(QIcon())
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName(
            "Primary")
        buttons.accepted.connect(d.accept)
        buttons.rejected.connect(d.reject)
        fl.addStretch(1)
        fl.addWidget(buttons)
        outer.addWidget(footer)

        # Back on the page that was open last time this session.
        shell.show_page(getattr(self, "_settings_page", None))
        d.finished.connect(
            lambda _r: setattr(self, "_settings_page", shell.current_key()))
        d.setUpdatesEnabled(True)
        self._install_wheel_guard(d)

        if d.exec():
            self.settings.setValue(
                "stream_format", fmt_box.currentData())
            # Stored inverted: the switch asks whether to *show* the prompt.
            self.settings.setValue(
                "hide_upcoming_prompt",
                "false" if upcoming_box.isChecked() else "true")

            def flag(box) -> str:
                return "true" if box.isChecked() else "false"

            self.settings.setValue("autoplay_preview", flag(autoplay_box))
            self.settings.setValue(
                "autoplay_next_episode", flag(autoplay_next_box))
            self.settings.setValue(
                "nextup_lead_secs", nextup_box.currentData())
            self.settings.setValue(
                "auto_reconnect_live", flag(autorecon_box))
            self.settings.setValue("check_updates", flag(updates_box))
            if x11_box is not None:
                self.settings.setValue("force_x11", flag(x11_box))
            if desktop_box is not None:
                want = desktop_box.isChecked()
                self.settings.setValue(
                    "desktop_entry", "true" if want else "false")
                # Answered here, so the status-row offer has nothing left
                # to ask - either way round.
                self.settings.setValue("desktop_entry_asked", "true")
                self.desktop_entry_btn.hide()
                self._apply_desktop_entry(want)
            self.settings.setValue(
                "view_density", density_box.currentData())
            self.settings.setValue(
                "epg_upcoming_count", epg_count_box.value())
            self.settings.setValue(
                "sort_order", sort_box.currentData())
            self.settings.setValue(
                "audio_lang", alang_box.currentData())
            self.settings.setValue(
                "sub_mode", sub_box.currentData())
            self.settings.setValue(
                "sub_lang", slang_box.currentData())
            self.settings.setValue(
                "sub_lang2", slang2_box.currentData())
            self.settings.setValue(
                "aspect_mode", aspect_box.currentData())
            self.settings.setValue(
                "hwdec_mode", hwdec_box.currentData())
            if self.player:
                # Stage it for the next mpv build (a fresh core reads the
                # widget attribute, not live settings). A changed decode mode
                # takes effect on the next opened stream - we never re-assign
                # hwdec on the running stream, which would reinitialise the
                # decoder and glitch the video.
                self.player.video.hwdec_pref = hwdec_box.currentData()
            self.settings.setValue("video_deinterlace", flag(deint_box))
            self.settings.setValue(
                "video_sharpen", sharpen_box.currentData())
            self.settings.setValue(
                "video_tonemapping", tonemap_box.currentData())
            self.settings.setValue(
                "cache_secs", buf_box.currentData())

            def delay_value(sign_box, hours_box, minutes_box) -> int:
                total = hours_box.value() * 60 + minutes_box.value()
                return -total if sign_box.currentData() == "-" else total

            self.settings.setValue(
                "replay_delay_min",
                delay_value(replay_sign_box, replay_hours_box,
                            replay_minutes_box))
            self.settings.setValue(
                "epg_delay_min",
                delay_value(epg_sign_box, epg_hours_box, epg_minutes_box))
            self.xmltv.delay_minutes = self._epg_delay_minutes()
            try:
                val = float(
                    rec_max_edit.text().replace(",", ".") or 0)
            except ValueError:
                val = 0
            self.settings.setValue(
                "rec_max_value", val if val > 0 else "")
            self.settings.setValue(
                "rec_max_unit", rec_max_unit.currentData())
            try:
                tval = float(rec_total_edit.text().replace(",", ".") or 0)
            except ValueError:
                tval = 0
            self.settings.setValue(
                "rec_total_value", tval if tval > 0 else "")
            self.settings.setValue(
                "rec_total_unit", rec_total_unit.currentData())
            try:
                pgb = float(cast_pause_edit.text().replace(",", ".") or 0)
            except ValueError:
                pgb = 0
            # A pause with no room at all is not a pause, so a nonsense
            # answer goes back to the default rather than to nothing.
            self.settings.setValue("cast_pause_gb", pgb if pgb > 0 else 4.5)
            self.settings.setValue(
                "metadata_source", meta_source_box.currentData())
            self.settings.setValue(
                "tmdb_api_key", tmdb_key_edit.text().strip())
            self.settings.setValue(
                "mv_frameless",
                "false" if mv_titlebar_box.isChecked() else "true")
            self.settings.setValue("mv_on_top", flag(mv_ontop_box))
            self.settings.setValue("mv_remember_geo", flag(mv_geo_box))
            self.settings.setValue("mv_stop_docked", flag(mv_stop_box))
            self.settings.setValue("mv_new_unmuted", flag(mv_unmute_box))
            self.settings.setValue(
                "mv_conflict_mode", mv_conflict_box.currentData())
            self.settings.setValue("mv_autohide_secs", mv_hide_spin.value())
            self.settings.setValue("mv_seek_step_min", mv_step_spin.value())
            self.settings.setValue("mv_cells", mv_cells_box.currentData())
            self._apply_multiview_settings()
            self.settings.setValue("home_enabled", flag(home_show_box))
            self.settings.setValue("home_start", flag(home_start_box))
            for skey, box in home_shelf_boxes.items():
                self.settings.setValue(skey, flag(box))
            self._apply_home_settings()
            self.cover.reload()
            if self.player:
                self.player.apply_default_options()
            self._apply_view_settings()
            self.list_model.refresh_all()
            # Re-render the open detail panel so a changed "upcoming count"
            # (and other view tweaks) take effect without re-selecting.
            cur = self.listw.currentIndex()
            if cur.isValid():
                self._on_current_changed(cur)

    @staticmethod
    def _format_release_notes(body: str) -> str:
        """Clean a GitHub release body for QTextBrowser's markdown renderer.
        GitHub wraps the detail in <details>/<summary> HTML, which Qt's markdown
        renderer can't handle - it swallowed the section headings and left bare
        bullet lists with no context. Strip those wrappers (and the redundant
        top '## dopeIPTV x.y.z' title - the status line already names the
        version) so the headings and bullets render as clean markdown."""
        body = re.sub(r"(?is)<summary>.*?</summary>", "", body)
        body = re.sub(r"(?i)</?details>", "", body)
        body = re.sub(r"(?im)^\s*##\s+dopeIPTV\b.*$", "", body, count=1)
        body = re.sub(r"\n{3,}", "\n\n", body)
        return body.strip()

    def show_about(self) -> None:
        d = QDialog(self)
        d.setWindowTitle(f"{tr('menu_about')} {APP_NAME}")
        d.setMinimumWidth(440)
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
        desc = QLabel(tr("about_desc"))
        desc.setWordWrap(True)
        lay.addWidget(desc)

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
        links.addWidget(link(tr("about_website"), "https://iptv.dope.rs"))
        links.addWidget(link(tr("about_github"),
                             f"https://github.com/{GITHUB_REPO}"))
        links.addWidget(link(tr("about_all_releases"),
                             f"https://github.com/{GITHUB_REPO}/releases"))
        links.addStretch(1)
        lay.addLayout(links)

        credit = QLabel(tr("about_tmdb_credit"))
        credit.setWordWrap(True)
        credit.setOpenExternalLinks(True)
        credit.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        lay.addWidget(credit)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(d.accept)
        btns.accepted.connect(d.accept)
        lay.addWidget(btns)

        def done(rel) -> None:
            recheck.setEnabled(True)
            if not rel or not rel.get("tag"):
                status.setText(tr("about_check_failed"))
                return
            if is_newer(rel["tag"], VERSION):
                status.setText(tr("about_update_available", version=rel["tag"]))
                status.setStyleSheet(
                    f"color:{P['text']}; font-size:12px; font-weight:600;")
                if rel.get("body"):
                    notes.setMarkdown(self._format_release_notes(rel["body"]))
                    notes.show()
                    # Grow the dialog so the notes get real room the first time
                    # an update appears (the check is async, after show()).
                    d.setMinimumWidth(520)
                    d.adjustSize()
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
                notes.hide()
                dl_btn.hide()

        def fail(_e) -> None:
            recheck.setEnabled(True)
            status.setText(tr("about_check_failed"))

        def check() -> None:
            recheck.setEnabled(False)
            status.setText(tr("about_checking"))
            status.setStyleSheet(f"color:{P['muted']}; font-size:12px;")
            run_async(self.pool,
                      lambda: fetch_latest_release(GITHUB_REPO), done, fail)

        recheck.clicked.connect(check)
        check()               # auto-check on open
        d.exec()

    # -- EPG refresh with progress -------------------------------------------------
