"""Settings dialog, theme/language/view application, and About (mixin).

Split out of main_window.py verbatim; MainWindow inherits this. All methods
keep their original `self.*` access, so behaviour is unchanged.
"""

from __future__ import annotations

import sys
from datetime import datetime

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView, QAbstractSlider, QAbstractSpinBox, QApplication,
    QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QTabWidget, QTextBrowser,
    QVBoxLayout, QWidget,
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
_WHEEL_GUARD = _WheelGuard()

from .. import APP_NAME, BUILD_VERSION, VERSION
from ..i18n import tr
from .dialogs import PlaylistDialog
from .epg_grid import EpgGridDialog
from ..providers.metadata import TmdbClient, bundled_tmdb_key
from ..providers.client import make_client
from ..core.recording import format_size
from ..media.players import embedded_playback_reason
from .theme import ACCENTS, P, THEMES, apply_theme, build_style
from ..core.updates import GITHUB_REPO, fetch_latest_release, is_newer
from ..core.workers import (
    clear_directory, default_image_cache_dir, dir_size_bytes, run_async)


class _SettingsMixin:
    def _choose_rec_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, "Choose recordings folder", self.rec.directory(),
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
        if QMessageBox.question(
                self, tr("msg_clear_history_title"),
                tr("msg_clear_history_body")) \
                == QMessageBox.StandardButton.Yes:
            if kinds:
                self.history.clear_kind(kinds)
            else:
                self.history.clear()
            self._load_items(sub)

    # -- EPG guide -----------------------------------------------------------------

    def _open_epg_guide(self) -> None:
        self._ensure_xmltv_loaded()
        if self.mode == "live" and self.all_items:
            cat = self.cat_list.currentItem()
            cat_name = cat.text() if cat else "All"
            EpgGridDialog(self, list(self.all_items), cat_name).exec()
            return
        # In Favorites, scope the guide to the favorite CHANNELS shown (a
        # folder, or all of them) - movies/series have no EPG. Other Trakt/
        # media sections simply have no live channels to guide.
        if self.mode == "fav":
            chans = self._favorite_channels_for_guide()
            cat = self.cat_list.currentItem()
            EpgGridDialog(self, chans,
                          cat.text().strip() if cat else tr("nav_favorites")
                          ).exec()
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("btn_epg_guide"))
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(tr("status_loading_channels")))
        dlg.resize(300, 100)
        dlg.show()

        def done(channels):
            dlg.close()
            EpgGridDialog(self, channels or []).exec()

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
        if self._is_combined_view(getattr(self, "_current_cat", None)):
            self._load_items(getattr(self, "_current_cat", None))
        else:
            self._apply_filter()

    def _set_theme(self, theme: str, accent: str) -> None:
        self.settings.setValue("theme", theme)
        self.settings.setValue("accent", accent)
        apply_theme(self.settings)
        QApplication.instance().setStyleSheet(build_style())
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

    def _reset_all_settings(self, parent_dialog) -> None:
        """Wipe every stored preference (theme, layout, playlists, favorites,
        history, credentials, ...) and ask the user to restart. Two-step
        confirmation because it can't be undone. Playlists live in the same
        QSettings config, so this really does reset back to the login screen."""
        first = QMessageBox.question(
            parent_dialog, tr("settings_reset_all"),
            tr("settings_reset_confirm_1"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if first != QMessageBox.StandardButton.Yes:
            return
        second = QMessageBox.warning(
            parent_dialog, tr("settings_reset_all"),
            tr("settings_reset_confirm_2"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if second != QMessageBox.StandardButton.Yes:
            return
        # Clear() drops every key from this QSettings scope; sync() flushes
        # it to disk before we tell the user to restart.
        self.settings.clear()
        self.settings.sync()
        QMessageBox.information(
            parent_dialog, tr("settings_reset_all"),
            tr("settings_reset_done"))
        parent_dialog.reject()
        # Quit so the next launch reads a fresh (empty) config and shows the
        # login screen. Direct restart is intentionally left to the user so
        # they can inspect that everything actually went away.
        QApplication.instance().quit()

    def _set_language(self, code: str) -> None:
        from ..i18n import set_language, current_language
        if code == current_language():
            return
        self.settings.setValue("language", code)
        set_language(code)
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
        }
        for key, btn in self.nav_btns.items():
            if key in nav_labels:
                btn.setText(tr(nav_labels[key]))
        self._cat_section_label.setText(tr("sidebar_categories"))
        self._guide_btn.setText(tr("btn_epg_guide"))
        self._settings_btn.setText(tr("btn_settings"))
        self.search.setPlaceholderText(tr("search_placeholder"))
        self._size_label.setText(tr("label_size"))
        self._sort_label.setText(tr("label_sort"))
        self.play_mpv.setText("▶  " + tr("btn_play"))
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
        self._apply_view_settings()

    # -- settings dialog -----------------------------------------------------------

    @staticmethod
    def _combo(items, current) -> QComboBox:
        box = QComboBox()
        for value, label in items:
            box.addItem(label, value)
        idx = box.findData(current)
        if idx >= 0:
            box.setCurrentIndex(idx)
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
        d.setMinimumSize(820, 600)
        # Tall enough that the grouped tabs aren't cramped, but a fixed, modest
        # width - the forms don't need to get wider, only taller (the earlier
        # width-scaling made it far too wide on a big window). Clamped to the
        # main window so it never spills past it.
        geo = self.geometry()
        d.resize(min(geo.width(), 900),
                 min(geo.height(), max(660, int(geo.height() * 0.85))))
        outer = QVBoxLayout(d)
        outer.setContentsMargins(18, 18, 18, 18)
        tabs = QTabWidget()
        # On macOS the native tab style hands each tab a fixed slot and elides
        # anything that doesn't fit ("Playba…", "Interfac…"). Ask the tab bar
        # to never elide and let scroll buttons appear if we run out of room
        # instead. No effect on Linux, where tabs already size to their text.
        if sys.platform == "darwin":
            tabs.tabBar().setElideMode(Qt.TextElideMode.ElideNone)
            tabs.tabBar().setUsesScrollButtons(True)
            tabs.setStyleSheet(
                "QTabBar::tab { padding: 6px 14px; min-width: 90px; }")
        outer.addWidget(tabs)

        # Playback tab
        play_tab = QWidget()
        pf = QFormLayout(play_tab)
        pf.setSpacing(10)
        mode_items = [("embedded", tr("option_embedded")),
                      ("window", tr("option_reused_window")),
                      ("external", tr("option_external_player"))]
        if not self.player:
            mode_items = [m for m in mode_items if m[0] != "embedded"]
        mode_box = self._combo(mode_items, self.playback_mode())
        autoplay_box = self._combo(
            [("true", tr("option_yes")), ("false", tr("option_no"))],
            "true" if self._autoplay_preview() else "false")
        autoplay_next_box = self._combo(
            [("true", tr("option_yes")), ("false", tr("option_no"))],
            self.settings.value("autoplay_next_episode", "true"))
        autorecon_box = self._combo(
            [("true", tr("option_yes")), ("false", tr("option_no"))],
            self.settings.value("auto_reconnect_live", "true"))
        fmt_box = self._combo(
            [("ts", "ts"), ("m3u8", "m3u8")],
            self.settings.value("stream_format", "ts"))
        LANGS = [
            ("", tr("option_lang_auto")), ("swe", tr("lang_swe")),
            ("eng", tr("lang_eng")), ("nor", tr("lang_nor")),
            ("dan", tr("lang_dan")), ("fin", tr("lang_fin")),
            ("ger", tr("lang_ger")), ("fre", tr("lang_fre")),
            ("spa", tr("lang_spa")), ("ita", tr("lang_ita")),
            ("por", tr("lang_por")), ("pol", tr("lang_pol")),
            ("ara", tr("lang_ara")), ("tur", tr("lang_tur")),
        ]
        alang_box = self._combo(
            LANGS, self.settings.value("audio_lang", ""))
        sub_box = self._combo(
            [("off", tr("option_sub_off")), ("auto", tr("option_sub_auto")),
             ("lang", tr("option_sub_lang")),
             ("forced", tr("option_sub_forced"))],
            self.settings.value("sub_mode", "auto"))
        slang_box = self._combo(
            LANGS, self.settings.value("sub_lang", ""))
        slang2_box = self._combo(
            LANGS, self.settings.value("sub_lang2", ""))
        aspect_box = self._combo(
            [("auto", tr("option_aspect_auto")), ("16:9", "16:9"),
             ("4:3", "4:3"), ("2.35:1", "2.35:1"),
             ("stretch", tr("option_aspect_stretch"))],
            self.settings.value("aspect_mode", "auto"))
        hwdec_box = self._combo(
            [("no", tr("option_hwdec_off")),
             ("auto-copy-safe", tr("option_hwdec_safe")),
             ("auto-safe", tr("option_hwdec_direct"))],
            str(self.settings.value("hwdec_mode", "") or "no"))
        deint_box = self._combo(
            [("false", tr("option_no")), ("true", tr("option_yes"))],
            self.settings.value("video_deinterlace", "false"))
        sharpen_box = self._combo(
            [("0.0", tr("option_off")), ("0.5", tr("option_low")),
             ("1.0", tr("option_medium")), ("2.0", tr("option_high"))],
            str(self.settings.value("video_sharpen", "0.0")))
        tonemap_box = self._combo(
            [("auto", tr("option_tonemap_auto")), ("hable", "Hable"),
             ("mobius", "Mobius"), ("reinhard", "Reinhard"),
             ("bt.2390", "BT.2390"), ("clip", tr("option_tonemap_clip"))],
            self.settings.value("video_tonemapping", "auto"))
        buf_box = self._combo(
            [("1", "1 s"), ("3", "3 s"), ("5", "5 s"),
             ("10", "10 s"), ("30", "30 s")],
            str(self.settings.value("cache_secs", "10")))

        def delay_row(key: str):
            try:
                total = int(self.settings.value(key, 0))
            except (TypeError, ValueError):
                total = 0
            sign_box = self._combo(
                [("+", "+ (later)"), ("-", "- (earlier)")],
                "-" if total < 0 else "+")
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
            row.addWidget(sign_box)
            row.addWidget(hours_box)
            row.addWidget(minutes_box)
            row.addStretch(1)
            return row, sign_box, hours_box, minutes_box

        (replay_delay_row, replay_sign_box, replay_hours_box,
         replay_minutes_box) = delay_row("replay_delay_min")
        (epg_delay_row, epg_sign_box, epg_hours_box,
         epg_minutes_box) = delay_row("epg_delay_min")
        def section(text: str) -> None:
            """A small caps subheading that groups the rows under it, so the
            Playback tab reads as a few labelled clusters instead of one long
            flat list."""
            lbl = QLabel(text)
            lbl.setStyleSheet(
                f"color:{P['accent']}; font-size:10px; font-weight:700;"
                "text-transform:uppercase; letter-spacing:1px; margin-top:12px;")
            pf.addRow(lbl)

        section(tr("sec_playback"))
        pf.addRow(tr("setting_playback_mode"), mode_box)
        pf.addRow(tr("setting_autoplay_preview"), autoplay_box)
        pf.addRow(tr("setting_autoplay_next"), autoplay_next_box)
        pf.addRow(tr("setting_auto_reconnect"), autorecon_box)
        pf.addRow(tr("setting_stream_format"), fmt_box)
        section(tr("sec_audio_subs"))
        pf.addRow(tr("setting_audio_lang"), alang_box)
        pf.addRow(tr("setting_subtitles"), sub_box)
        pf.addRow(tr("setting_sub_lang"), slang_box)
        pf.addRow(tr("setting_sub_lang_fallback"), slang2_box)
        section(tr("sec_video"))
        pf.addRow(tr("setting_aspect_ratio"), aspect_box)
        pf.addRow(tr("setting_deinterlace"), deint_box)
        pf.addRow(tr("setting_sharpen"), sharpen_box)
        pf.addRow(tr("setting_tonemapping"), tonemap_box)
        pf.addRow(tr("setting_hwdec"), hwdec_box)
        hwdec_hint = QLabel(tr("setting_hwdec_hint"))
        hwdec_hint.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        hwdec_hint.setWordWrap(True)
        pf.addRow(hwdec_hint)
        section(tr("sec_network"))
        pf.addRow(tr("setting_network_buffer"), buf_box)
        pf.addRow(tr("setting_replay_delay"), replay_delay_row)
        pf.addRow(tr("setting_epg_delay"), epg_delay_row)
        section(tr("sec_guide"))
        epg_cache_row = QHBoxLayout()
        refresh_epg_btn = QPushButton(tr("btn_refresh_epg"))
        clear_epg_btn = QPushButton(tr("btn_clear_epg"))
        refresh_epg_btn.clicked.connect(self._refresh_epg_now)
        clear_epg_btn.clicked.connect(self._clear_epg_cache)
        epg_cache_row.addWidget(refresh_epg_btn)
        epg_cache_row.addWidget(clear_epg_btn)
        epg_cache_row.addStretch()
        pf.addRow(tr("setting_epg_cache"), epg_cache_row)
        x11_box = None
        if sys.platform.startswith("linux"):
            x11_box = QCheckBox(tr("setting_force_x11"))
            x11_box.setChecked(
                self.settings.value("force_x11", "false") == "true")
            x11_box.setToolTip(tr("setting_force_x11_hint"))
            pf.addRow("", x11_box)
            x11_hint = QLabel(tr("setting_force_x11_hint"))
            x11_hint.setWordWrap(True)
            x11_hint.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
            pf.addRow("", x11_hint)
        delay_hint = QLabel(
            "Replay delay shifts where catch-up/timeshift starts "
            "playback, for providers whose stream lags behind their "
            "listed schedule. EPG delay shifts all programme guide "
            "times shown in the app. Both default to no offset.")
        delay_hint.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        delay_hint.setWordWrap(True)
        pf.addRow(delay_hint)
        mode_hint = QLabel(
            "Embedded plays in the app. Reused mpv window keeps "
            "one external window you can zap in (Ctrl+←/→). "
            "External opens a fresh window each time.")
        mode_hint.setStyleSheet(
            f"color:{P['muted2']}; font-size:11px;")
        mode_hint.setWordWrap(True)
        pf.addRow(mode_hint)
        if not self.player:
            reason = embedded_playback_reason() or "unknown reason"
            hint = QLabel(
                f"Embedded playback unavailable: {reason}")
            hint.setStyleSheet(
                f"color:{P['muted2']}; font-size:11px;")
            hint.setWordWrap(True)
            pf.addRow(hint)
        # The grouped Playback form is taller than the dialog, so let it scroll
        # instead of clipping the last rows (the hint text and EPG buttons).
        play_scroll = QScrollArea()
        play_scroll.setWidgetResizable(True)
        play_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        play_scroll.setWidget(play_tab)
        tabs.addTab(play_scroll, tr("tab_playback"))

        # Interface tab
        ui_tab = QWidget()
        uf = QFormLayout(ui_tab)
        uf.setSpacing(10)
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
        theme_box = self._combo(
            [(key, tr(f"theme_{key}")) for key in THEMES],
            self.settings.value("theme", "graphite"))
        accent_box = self._combo(
            [(key, tr(f"accent_{key}")) for key in ACCENTS],
            self.settings.value("accent", "blue"))
        theme_box.currentIndexChanged.connect(
            lambda _i: self._set_theme(
                theme_box.currentData(), accent_box.currentData()))
        accent_box.currentIndexChanged.connect(
            lambda _i: self._set_theme(
                theme_box.currentData(), accent_box.currentData()))
        from ..i18n import LANGUAGES, current_language
        lang_box = self._combo(
            [(code, name) for code, name in LANGUAGES.items()],
            current_language())
        lang_box.currentIndexChanged.connect(
            lambda _i: self._set_language(lang_box.currentData()))
        epg_count_box = QSpinBox()
        epg_count_box.setRange(self.EPG_UPCOMING_MIN, self.EPG_UPCOMING_MAX)
        epg_count_box.setValue(self._epg_upcoming_count())
        epg_count_box.setFixedWidth(90)
        # Wrap in a row with a stretch so it stays a small box (a full-width
        # form field rendered as one long, hard-to-read strip).
        epg_count_row = QHBoxLayout()
        epg_count_row.addWidget(epg_count_box)
        epg_count_row.addStretch(1)
        uf.addRow(tr("setting_language"), lang_box)
        uf.addRow(tr("setting_list_size"), density_box)
        uf.addRow(tr("setting_upcoming_count"), epg_count_row)
        uf.addRow(tr("setting_sort_by"), sort_box)
        uf.addRow(tr("setting_theme"), theme_box)
        uf.addRow(tr("setting_accent_color"), accent_box)
        theme_hint = QLabel(tr("misc_theme_applies_immediately"))
        theme_hint.setStyleSheet(
            f"color:{P['muted2']}; font-size:11px;")
        theme_hint.setWordWrap(True)
        uf.addRow(theme_hint)

        updates_box = QCheckBox(tr("setting_check_updates"))
        updates_box.setChecked(
            self.settings.value("check_updates", "true") == "true")
        uf.addRow("", updates_box)

        # -- Maintenance: the three actions grouped as one tidy button row,
        # each explained by a tooltip so no inline hints clutter the form.
        shortcuts_btn = QPushButton(tr("sc_open"))
        shortcuts_btn.setToolTip(tr("sc_title"))
        shortcuts_btn.clicked.connect(self._open_shortcuts)

        # Disk-cache controls: covers/logos accumulate under
        # QStandardPaths.CacheLocation and don't clean themselves. The live
        # cache is the shared "images" dir; the two legacy dirs predate the
        # loaders sharing one directory - keep them in the sweep so files from
        # old sessions still get counted and cleared.
        cache_dirs = [default_image_cache_dir("images"),
                      default_image_cache_dir("logos"),
                      default_image_cache_dir("posters")]
        cache_lbl = QLabel()
        clear_cache_btn = QPushButton(tr("settings_image_cache_clear"))
        clear_cache_btn.setToolTip(tr("settings_image_cache_hint"))

        def refresh_cache_label() -> None:
            total = sum(dir_size_bytes(d) for d in cache_dirs)
            cache_lbl.setText(
                tr("settings_image_cache_label", size=format_size(total)))
            clear_cache_btn.setEnabled(total > 0)

        def clear_cache() -> None:
            for d in cache_dirs:
                clear_directory(d)
            # Also drop the in-memory LRUs so a re-scroll doesn't just
            # rewrite the same pixmaps back to disk from RAM.
            self.logos.cache.clear()
            self.poster_art.cache.clear()
            refresh_cache_label()

        clear_cache_btn.clicked.connect(clear_cache)

        ts_reset_btn = QPushButton(tr("ts_reset_broken"))
        ts_reset_btn.setToolTip(tr("ts_reset_hint"))

        def reset_ts() -> None:
            self._clear_ts_broken()
            self._flash_status(tr("ts_reset_done"))

        ts_reset_btn.clicked.connect(reset_ts)

        maint_lbl = QLabel(tr("sec_maintenance"))
        maint_lbl.setStyleSheet(
            f"color:{P['accent']}; font-size:10px; font-weight:700;"
            "text-transform:uppercase; letter-spacing:1px; margin-top:12px;")
        uf.addRow(maint_lbl)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        for b in (shortcuts_btn, clear_cache_btn, ts_reset_btn):
            b.setSizePolicy(QSizePolicy.Policy.Expanding,
                            QSizePolicy.Policy.Fixed)
            btn_row.addWidget(b)
        uf.addRow(btn_row)
        cache_lbl.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        uf.addRow(cache_lbl)
        refresh_cache_label()

        tabs.addTab(ui_tab, tr("tab_interface"))

        # Playlists tab
        pl_tab = QWidget()
        pv = QVBoxLayout(pl_tab)
        pv.setSpacing(10)
        # Account status for the selected Xtream provider (expiry,
        # connections). Follows the list selection so you can check any
        # provider without switching to it.
        account_lbl = QLabel(tr("account_loading"))
        account_lbl.setWordWrap(True)
        account_lbl.setObjectName("DetailMeta")
        pv.addWidget(account_lbl)
        pl_list = QListWidget()
        pv.addWidget(pl_list, 1)
        pl_btns = QHBoxLayout()
        add_btn = QPushButton(tr("btn_add"))
        edit_btn = QPushButton(tr("btn_edit"))
        remove_btn = QPushButton(tr("btn_remove"))
        refresh_pl_btn = QPushButton(tr("btn_refresh"))
        refresh_pl_btn.setToolTip(tr("tooltip_reload_channels_epg"))
        use_btn = QPushButton(tr("btn_use"), objectName="Primary")
        for b in (add_btn, edit_btn, remove_btn, refresh_pl_btn, use_btn):
            pl_btns.addWidget(b)
        pv.addLayout(pl_btns)
        io_btns = QHBoxLayout()
        export_btn = QPushButton(tr("btn_export"))
        export_btn.setToolTip(tr("settings_export_tip"))
        import_btn = QPushButton(tr("btn_import"))
        import_btn.setToolTip(tr("settings_import_tip"))
        io_btns.addWidget(export_btn)
        io_btns.addWidget(import_btn)
        io_btns.addStretch()
        pv.addLayout(io_btns)
        tabs.addTab(pl_tab, tr("tab_playlists"))

        # Parental tab
        par_tab = QWidget()
        parv = QVBoxLayout(par_tab)
        parv.setSpacing(10)
        pin_status = QLabel()
        parv.addWidget(pin_status)
        set_pin_btn = QPushButton(tr("pin_set_change"))
        remove_pin_btn = QPushButton(tr("pin_remove"))
        lock_now_btn = QPushButton(tr("pin_lock_now"))
        pin_btns = QHBoxLayout()
        pin_btns.setSpacing(8)
        pin_btns.addWidget(set_pin_btn)
        pin_btns.addWidget(remove_pin_btn)
        pin_btns.addWidget(lock_now_btn)
        pin_btns.addStretch(1)
        parv.addLayout(pin_btns)
        par_hint = QLabel(
            "Lock favorite groups (right-click a group under "
            "Favorites) or whole categories (right-click a "
            "category in TV/Movies/Series). Locked content is "
            "hidden - including from 'All' - until the PIN is "
            "entered.")
        par_hint.setStyleSheet(
            f"color:{P['muted2']}; font-size:11px;")
        par_hint.setWordWrap(True)
        parv.addWidget(par_hint)
        parv.addStretch()
        tabs.addTab(par_tab, tr("tab_parental"))

        # Recording tab
        rec_tab = QWidget()
        recv = QVBoxLayout(rec_tab)
        recv.setSpacing(10)
        rec_dir_lbl = QLabel(self.rec.directory())
        rec_dir_lbl.setWordWrap(True)
        recv.addWidget(QLabel(tr("rec_saved_in")))
        recv.addWidget(rec_dir_lbl)
        rec_dir_btn = QPushButton(tr("btn_choose_folder"))

        def pick_rec_dir():
            path = QFileDialog.getExistingDirectory(
                d, "Choose recordings folder", self.rec.directory(),
                QFileDialog.Option.DontUseNativeDialog
                | QFileDialog.Option.ShowDirsOnly)
            if path:
                self.rec.set_directory(path)
                rec_dir_lbl.setText(path)
                if self.mode == "rec":
                    self._load_categories()

        rec_dir_btn.clicked.connect(pick_rec_dir)
        rec_dir_row = QHBoxLayout()
        rec_dir_row.addWidget(rec_dir_btn)
        rec_dir_row.addStretch(1)
        recv.addLayout(rec_dir_row)
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel(
            "Stop a recording when the file reaches"))
        rec_max_edit = QLineEdit(
            str(self.settings.value("rec_max_value", "")))
        rec_max_edit.setPlaceholderText(tr("ph_no_limit"))
        rec_max_edit.setMaximumWidth(90)
        rec_max_unit = self._combo(
            [("MB", "MB"), ("GB", "GB"), ("TB", "TB")],
            self.settings.value("rec_max_unit", "GB"))
        size_row.addWidget(rec_max_edit)
        size_row.addWidget(rec_max_unit)
        size_row.addStretch()
        recv.addLayout(size_row)
        total_row = QHBoxLayout()
        total_row.addWidget(QLabel(tr("rec_total_label")))
        rec_total_edit = QLineEdit(
            str(self.settings.value("rec_total_value", "")))
        rec_total_edit.setPlaceholderText(tr("ph_no_limit"))
        rec_total_edit.setMaximumWidth(90)
        rec_total_unit = self._combo(
            [("GB", "GB"), ("TB", "TB")],
            self.settings.value("rec_total_unit", "GB"))
        total_row.addWidget(rec_total_edit)
        total_row.addWidget(rec_total_unit)
        total_row.addStretch()
        recv.addLayout(total_row)
        rk, rexe = self.rec.recorder()
        rec_hint = QLabel(
            f"Recorder: {rk} ({rexe})" if rexe else
            "No recorder found - install ffmpeg (recommended) "
            "or mpv.")
        rec_hint.setStyleSheet(
            f"color:{P['muted2']}; font-size:11px;")
        rec_hint.setWordWrap(True)
        recv.addWidget(rec_hint)
        rec_hint2 = QLabel(
            "Right-click a TV channel → Record to record "
            "immediately or on a start/stop timer. Scheduled "
            "recordings need the app to be running when they "
            "start. Manage files under Recordings in the sidebar.")
        rec_hint2.setStyleSheet(
            f"color:{P['muted2']}; font-size:11px;")
        rec_hint2.setWordWrap(True)
        recv.addWidget(rec_hint2)
        recv.addStretch()
        tabs.addTab(rec_tab, tr("tab_recording"))

        # Metadata tab (TMDB artwork)
        meta_tab = QWidget()
        mf = QFormLayout(meta_tab)
        mf.setSpacing(10)
        _bundled = bool(bundled_tmdb_key())
        # Source options: the built-in key (only offered when one ships),
        # the user's own key, or the provider's own artwork. The key
        # field appears only for the "own key" choice, so there's no
        # stray field hanging under the built-in option.
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
        tmdb_key_row = QHBoxLayout()
        tmdb_key_edit = QLineEdit(self.settings.value("tmdb_api_key", ""))
        tmdb_key_edit.setPlaceholderText(tr("tmdb_key_placeholder"))
        tmdb_test_btn = QPushButton(tr("btn_test"))
        tmdb_key_row.addWidget(tmdb_key_edit, 1)
        tmdb_key_row.addWidget(tmdb_test_btn)
        mf.addRow(tr("setting_artwork_source"), meta_source_box)
        key_row_idx = mf.rowCount()
        mf.addRow(tr("setting_tmdb_key"), tmdb_key_row)
        tmdb_status = QLabel()
        tmdb_status.setWordWrap(True)
        status_row_idx = mf.rowCount()
        mf.addRow("", tmdb_status)
        meta_hint = QLabel(
            ("TMDB works out of the box - no account needed. Posters are "
             "matched by title and cached, so lookups happen once per "
             "movie/series (not used for live TV). You can optionally "
             "enter your own free key from themoviedb.org -> Settings -> "
             "API to use your own quota. This product uses the TMDB API "
             "but is not endorsed or certified by TMDB.")
            if _bundled else
            ("Get a free key at themoviedb.org -> Settings -> API. "
             "Posters are matched by title and cached, so lookups "
             "only happen once per movie/series. Not used for live TV. "
             "This product uses the TMDB API but is not endorsed or "
             "certified by TMDB."))
        meta_hint.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        meta_hint.setWordWrap(True)
        mf.addRow(meta_hint)
        tabs.addTab(meta_tab, tr("tab_metadata"))

        def update_meta_visibility() -> None:
            show_key = meta_source_box.currentData() == "custom"
            mf.setRowVisible(key_row_idx, show_key)
            mf.setRowVisible(status_row_idx, show_key)

        def test_tmdb_key() -> None:
            key = tmdb_key_edit.text().strip()
            if not key:
                tmdb_status.setText(tr("tmdb_enter_key"))
                tmdb_status.setStyleSheet(
                    f"color:{P['error']}; font-size:11px;")
                return
            tmdb_status.setText(tr("tmdb_checking"))
            tmdb_status.setStyleSheet(
                f"color:{P['muted2']}; font-size:11px;")

            def check():
                TmdbClient(key).poster_url("Inception", "vod")
                return True

            def ok(_r):
                tmdb_status.setText(tr("tmdb_key_works"))
                tmdb_status.setStyleSheet(
                    f"color:{P['accent']}; font-size:11px; font-weight:600;")

            def fail(msg):
                tmdb_status.setText(tr("tmdb_key_failed", msg=msg))
                tmdb_status.setStyleSheet(
                    f"color:{P['error']}; font-size:11px;")

            run_async(self.pool, check, ok, fail)

        meta_source_box.currentIndexChanged.connect(
            lambda _i: update_meta_visibility())
        tmdb_test_btn.clicked.connect(test_tmdb_key)
        update_meta_visibility()

        # Trakt tab
        trakt_tab = QWidget()
        tf = QVBoxLayout(trakt_tab)
        tf.setSpacing(10)
        trakt_status = QLabel()
        trakt_status.setWordWrap(True)
        tf.addWidget(trakt_status)

        # -- Easy path: one-click browser sign-in with the built-in app -------
        trakt_browser_btn = QPushButton(tr("trakt_connect_browser"),
                                        objectName="Primary")
        browser_row = QHBoxLayout()
        browser_row.addWidget(trakt_browser_btn)
        browser_row.addStretch(1)
        tf.addLayout(browser_row)
        browser_hint = QLabel(tr("trakt_connect_browser_hint"))
        browser_hint.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        browser_hint.setWordWrap(True)
        tf.addWidget(browser_hint)

        # -- Session actions --------------------------------------------------
        trakt_btns = QHBoxLayout()
        trakt_disconnect_btn = QPushButton(tr("trakt_disconnect"))
        trakt_watchlist_btn = QPushButton(tr("trakt_watchlist_btn"))
        trakt_btns.addWidget(trakt_disconnect_btn)
        trakt_btns.addWidget(trakt_watchlist_btn)
        trakt_btns.addStretch()
        tf.addLayout(trakt_btns)
        trakt_hint = QLabel(
            "While connected, movies and episodes you play are "
            "scrobbled to Trakt. Live TV and recordings are not.")
        trakt_hint.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        trakt_hint.setWordWrap(True)
        tf.addWidget(trakt_hint)

        # Watched-history sync controls.
        sync_row = QHBoxLayout()
        sync_status = QLabel()
        sync_status.setWordWrap(True)
        sync_now_btn = QPushButton(tr("trakt_sync_now"))
        sync_row.addWidget(sync_status, 1)
        sync_row.addWidget(sync_now_btn)
        tf.addLayout(sync_row)
        sync_hint = QLabel(tr("trakt_sync_hint"))
        sync_hint.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        sync_hint.setWordWrap(True)
        tf.addWidget(sync_hint)

        tf.addStretch()

        def refresh_sync_status():
            n_m, n_e = (len(self.watched.movies),
                        sum(len(v) for v in self.watched.episodes.values()))
            if self.watched.last_sync_at:
                when = datetime.fromtimestamp(
                    self.watched.last_sync_at).strftime("%Y-%m-%d %H:%M")
                sync_status.setText(
                    tr("trakt_sync_status", when=when,
                       movies=n_m, episodes=n_e))
            else:
                sync_status.setText(tr("trakt_sync_never"))
            sync_now_btn.setEnabled(
                self.trakt.is_connected()
                and not self._watched_sync_running)

        def do_sync_now():
            sync_now_btn.setEnabled(False)
            sync_status.setText(tr("trakt_syncing"))
            # Kick a forced sync and re-poll status every second while it
            # runs so the label flips to "synced" without needing the
            # user to close and reopen the dialog.
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
            if self.trakt.is_connected():
                trakt_status.setText(tr("trakt_connected"))
            else:
                trakt_status.setText(tr("trakt_not_connected"))
            trakt_disconnect_btn.setEnabled(self.trakt.is_connected())
            refresh_sync_status()

        def do_connect_browser():
            # Easy path: sign in through the browser with whatever Trakt app is
            # active (the built-in one unless the user saved their own). Doesn't
            # touch the credential fields.
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
        tabs.addTab(trakt_tab, tr("tab_trakt"))

        def refresh_pin_status():
            if self.parental.has_pin():
                state = ("unlocked for this session"
                         if self.parental.session_unlocked
                         else "locked")
                pin_status.setText(
                    f"PIN is set - currently {state}.")
            else:
                pin_status.setText(tr("pin_none_set"))
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

        store = self.playlist_store

        def reload_pl_list():
            pl_list.clear()
            if not store:
                pl_list.addItem(tr("pl_mgmt_unavailable"))
                return
            for p in store.playlists():
                suffix = ("   (active)"
                          if p["id"] == store.active_id else "")
                item = QListWidgetItem(
                    f"{p['name']}  -  {p['server']}{suffix}")
                item.setData(Qt.ItemDataRole.UserRole, p["id"])
                pl_list.addItem(item)

        def selected_pid():
            item = pl_list.currentItem()
            return (item.data(Qt.ItemDataRole.UserRole)
                    if item else None)

        def refresh_account(pid):
            pl = store.get(pid) if (store and pid) else None
            if not pl:
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

        def add_playlist():
            dlg = PlaylistDialog(d)
            if dlg.exec():
                store.add(dlg.values())
                reload_pl_list()

        def edit_playlist():
            pid = selected_pid()
            pl = store.get(pid) if (store and pid) else None
            if not pl:
                return
            dlg = PlaylistDialog(d, pl)
            if dlg.exec():
                store.update(pid, **dlg.values())
                reload_pl_list()
                if pid == store.active_id:
                    self.switch_playlist(pid)

        def remove_playlist():
            pid = selected_pid()
            if not (store and pid):
                return
            if QMessageBox.question(
                    d, tr("msg_remove_playlist_title"),
                    tr("msg_remove_playlist_body")) \
                    == QMessageBox.StandardButton.Yes:
                store.remove(pid)
                reload_pl_list()

        def use_playlist():
            pid = selected_pid()
            if store and pid and pid != store.active_id:
                self.switch_playlist(pid)
                reload_pl_list()

        def export_playlists():
            if not store or not store.playlists():
                QMessageBox.information(
                    d, "Export", "No playlists to export.")
                return
            path, _ = QFileDialog.getSaveFileName(
                d, "Export playlists", "playlists.json",
                "JSON files (*.json)",
                options=QFileDialog.Option.DontUseNativeDialog)
            if not path:
                return
            import json
            data = []
            for p in store.playlists():
                data.append({
                    "name": p.get("name", ""),
                    "server": p.get("server", ""),
                    "username": p.get("username", ""),
                    "password": p.get("password", ""),
                    "epg_url": p.get("epg_url", ""),
                    "refresh": p.get("refresh", "never"),
                })
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            QMessageBox.information(
                d, "Export",
                f"Exported {len(data)} playlist(s) to:\n{path}")

        def import_playlists():
            if not store:
                return
            path, _ = QFileDialog.getOpenFileName(
                d, "Import playlists", "",
                "JSON files (*.json);;All files (*)",
                options=QFileDialog.Option.DontUseNativeDialog)
            if not path:
                return
            import json
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as exc:
                QMessageBox.warning(
                    d, "Import", f"Could not read file:\n{exc}")
                return
            if not isinstance(data, list):
                QMessageBox.warning(
                    d, "Import", "Invalid format — expected a JSON list.")
                return
            added = 0
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                server = entry.get("server", "").strip()
                user = entry.get("username", "").strip()
                pw = entry.get("password", "").strip()
                if not (server and user and pw):
                    continue
                store.add({
                    "name": entry.get("name", "").strip()
                            or server.split("//")[-1].split("/")[0],
                    "server": server, "username": user,
                    "password": pw,
                    "epg_url": entry.get("epg_url", "").strip(),
                    "refresh": entry.get("refresh", "never"),
                })
                added += 1
            reload_pl_list()
            QMessageBox.information(
                d, "Import", f"Imported {added} playlist(s).")

        add_btn.clicked.connect(add_playlist)
        edit_btn.clicked.connect(edit_playlist)
        remove_btn.clicked.connect(remove_playlist)
        refresh_pl_btn.clicked.connect(self.refresh_playlist)
        use_btn.clicked.connect(use_playlist)
        export_btn.clicked.connect(export_playlists)
        import_btn.clicked.connect(import_playlists)
        if not store:
            for b in (add_btn, edit_btn, remove_btn,
                      refresh_pl_btn, use_btn, export_btn, import_btn):
                b.setEnabled(False)
        reload_pl_list()
        # Start on the active provider so its account status shows first
        # (selecting a row drives refresh_account via currentItemChanged).
        if store:
            active_row = next(
                (i for i in range(pl_list.count())
                 if pl_list.item(i).data(Qt.ItemDataRole.UserRole)
                 == store.active_id), 0)
            if pl_list.count():
                pl_list.setCurrentRow(active_row)
            else:
                account_lbl.setText(tr("account_unavailable"))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Reset)
        for b in buttons.buttons():
            b.setIcon(QIcon())
        reset_btn = buttons.button(QDialogButtonBox.StandardButton.Reset)
        reset_btn.setText(tr("settings_reset_all"))
        reset_btn.clicked.connect(lambda: self._reset_all_settings(d))
        buttons.accepted.connect(d.accept)
        buttons.rejected.connect(d.reject)
        outer.addWidget(buttons)

        # Guard every value control so scrolling the page doesn't change a
        # setting under the cursor - you click to change, not scroll past.
        for cls in (QComboBox, QAbstractSpinBox, QAbstractSlider):
            for w in d.findChildren(cls):
                w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                w.installEventFilter(_WHEEL_GUARD)

        if d.exec():
            self.settings.setValue(
                "stream_format", fmt_box.currentData())
            self.settings.setValue(
                "autoplay_preview", autoplay_box.currentData())
            self.settings.setValue(
                "autoplay_next_episode", autoplay_next_box.currentData())
            self.settings.setValue(
                "auto_reconnect_live", autorecon_box.currentData())
            self.settings.setValue(
                "check_updates",
                "true" if updates_box.isChecked() else "false")
            if x11_box is not None:
                self.settings.setValue(
                    "force_x11", "true" if x11_box.isChecked() else "false")
            if mode_box.currentData():
                self.settings.setValue(
                    "playback_mode", mode_box.currentData())
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
                # Stage it for the next mpv build (a fresh core reads the widget
                # attribute, not live settings). A changed decode mode takes
                # effect on the next opened stream - we never re-assign hwdec on
                # the running stream, which would reinitialise the decoder and
                # glitch the video.
                self.player.video.hwdec_pref = hwdec_box.currentData()
            self.settings.setValue(
                "video_deinterlace", deint_box.currentData())
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
            self.settings.setValue(
                "metadata_source", meta_source_box.currentData())
            self.settings.setValue(
                "tmdb_api_key", tmdb_key_edit.text().strip())
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
            b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
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
        notes.setMaximumHeight(220)
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
                    notes.setMarkdown(rel["body"])
                    notes.show()
                if rel.get("url"):
                    try:
                        dl_btn.clicked.disconnect()
                    except TypeError:
                        pass
                    dl_btn.clicked.connect(
                        lambda: QDesktopServices.openUrl(QUrl(rel["url"])))
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
