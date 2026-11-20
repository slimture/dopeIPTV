"""Settings' SYSTEM pages: General, Appearance, Keyboard shortcuts,
Profiles, Parental lock and Advanced (their rows and keys:
docs/SETTINGS_MAP.md).

Each ``build_<page>(ctx)`` fills its page the first time it is needed
(SettingsShell.ensure_built), never as the window opens, and leaves what
the page stores and applies, if anything, as
``ctx.page_commits[<page key>]``: commit(put, changed, final), run by
open_settings' commit. *ctx* is the Settings window's SettingsContext
(ui/mw_settings.py): General and Appearance share the supporter key's
state and window through it, General the backup with Sources.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from PyQt6 import sip
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QColorDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout,
    QWidget,
)

from .. import VERSION
from ..core import places as _places
from ..core.recording import format_size
from ..core.workers import (
    clear_directory, default_image_cache_dir, dir_size_bytes, run_async)
from ..i18n import tr
from .settings_ui import (
    Chip, ChipGroups, ChoicePicker, ColorDot, ThemeTile, after_shown, hbox,
)
from .theme import (
    ACCENT_CUSTOM, ACCENT_THEME, ACCENTS, P, SUPPORTER_THEMES, THEME_ORDER,
    UI_SCALES, effective_theme, is_light,
)

if TYPE_CHECKING:
    from .mw_settings import SettingsContext


def build_general(ctx: SettingsContext) -> None:
    """General: the language, where the app opens, privacy, updates, the
    backup, the system's media controls, TV mode, supporting the app and
    About."""
    win, d, general = ctx.win, ctx.dialog, ctx.pages["general"]
    shell, is_on, flag, nerd_switch = ctx.shell, ctx.is_on, ctx.flag, ctx.nerd_switch
    link_cards, shared, key_state = ctx.link_cards, ctx.shared, ctx.key_state
    supporter_key, supporter_window = ctx.supporter_key, ctx.supporter_window
    store, export_playlists = ctx.store, ctx.export_playlists
    import_playlists = ctx.import_playlists
    from ..i18n import LANGUAGES, current_language
    # A normal dropdown: click to open, scroll inside the popup.
    # _combo() caps the popup at 14 rows, scrolls per pixel and
    # keeps the scrollbar visible for the 27 languages.
    lang_box = win._combo(
        [(code, name) for code, name in LANGUAGES.items()],
        current_language())
    lang_box.currentIndexChanged.connect(
        lambda _i: win._set_language(lang_box.currentData()))
    c = general.card(tr("set_card_language"))
    c.row(tr("setting_language"), lang_box,
          tr("setting_language_restart_hint"))
    # Where the app opens: where you left off (each profile its own
    # place), or always on Mine.
    start_box = win._combo(
        [("last", tr("start_place_last")), ("mitt", tr("start_place_mine"))],
        _places.start_choice(win.settings))
    c = general.card(tr("set_card_start"))
    c.row(tr("set_start_place"), start_box, tr("set_start_place_hint"),
          search="start startup open launch home mine")
    # Privacy: private viewing lasts until the app closes, so it is
    # not stored (an untouched window still writes nothing).
    from ..core import privacy as _privacy
    c = general.card(tr("set_card_privacy"))
    private_box = c.toggle(tr("private_menu"), _privacy.private(), tr("private_hint"))
    private_box.toggled.connect(win.set_private_viewing)
    clear_btn = QPushButton(tr("clear_btn"))
    clear_btn.clicked.connect(lambda: win.open_clear_history())
    c.row(tr("clear_menu"), clear_btn, tr("clear_hint"),
          search="history incognito private recently viewed")
    c = general.card(tr("set_card_updates"))
    from ..core import build_check as _build
    if not _build.allowed():
        # A modified build checks for none: said once, above its rows.
        c.note(_build.notice("updates"))
    updates_box = c.toggle(tr("setting_check_updates"),
                           is_on("check_updates", "true"))
    channel_box = win._combo(
        [("stable", tr("option_channel_stable")),
         ("nightly", tr("option_channel_nightly"))],
        win._update_channel())
    c.row(tr("setting_update_channel"), channel_box,
          tr("setting_update_channel_hint"))
    # Everything a person would miss on a new computer, in one file
    # (it was the playlists only, on the Playlists page).
    export_btn = QPushButton(tr("backup_btn"))
    import_btn = QPushButton(tr("restore_btn"))
    c = general.card(tr("set_card_backup"))
    c.row(tr("set_backup_label"), hbox(export_btn, import_btn,
                                       stretch=False),
          tr("set_backup_hint"),
          search="export import backup restore playlists json")
    # An optional password for the next backup made: never stored, and
    # emptied once the file is written. Empty, the file is as it always was.
    backup_pw = QLineEdit()
    backup_pw.setEchoMode(QLineEdit.EchoMode.Password)
    backup_pw.setPlaceholderText(tr("set_backup_pw_none"))
    backup_pw.setAccessibleName(tr("set_backup_pw"))
    backup_again = QLineEdit()
    backup_again.setEchoMode(QLineEdit.EchoMode.Password)
    backup_again.setPlaceholderText(tr("set_backup_pw_again"))
    backup_again.setAccessibleName(tr("set_backup_pw_again"))
    backup_again.setEnabled(False)
    c.row(tr("set_backup_pw"), hbox(backup_pw, backup_again, stretch=False),
          tr("set_backup_pw_hint"), stretch_control=True,
          search="password encrypt encrypted lock protect backup")
    c.note(tr("backup_note"))

    def backup_pw_typed(text: str) -> None:
        # The repeat only for a password; emptied with it.
        backup_again.setEnabled(bool(text) and bool(store))
        if not text:
            backup_again.clear()

    def back_up() -> None:
        password = backup_pw.text()
        if password != backup_again.text():
            QMessageBox.warning(d, tr("backup_title"), tr("backup_pw_mismatch"))
            backup_again.setFocus()
            backup_again.selectAll()
            return
        if export_playlists(password) and password:
            backup_pw.clear()

    backup_pw.textChanged.connect(backup_pw_typed)
    export_btn.clicked.connect(back_up)
    import_btn.clicked.connect(import_playlists)
    if not store:
        export_btn.setEnabled(False)
        import_btn.setEnabled(False)
        backup_pw.setEnabled(False)
    # Only where an entry is actually missing: a .deb or a Flatpak
    # has one already, and offering to add a second would be
    # nonsense.
    desktop_box = None
    mpris_box = None
    if sys.platform.startswith("linux"):
        from ..core import desktop_entry as _de
        c = general.card(tr("set_card_system"))
        if _de.is_installed() or _de.can_offer():
            desktop_box = c.toggle(tr("setting_desktop_entry"),
                                   _de.is_installed(),
                                   tr("setting_desktop_entry_hint"))
        mpris_box = c.toggle(tr("setting_mpris"), is_on("mpris", "true"),
                             tr("setting_mpris_hint"))
    elif sys.platform in ("darwin", "win32"):
        # The same switch for Now Playing / the System Media
        # Transport Controls, where this build has the bridge to them.
        from ..services import media_session as _ms
        if _ms.supported():
            c = general.card(tr("set_card_system"))
            mpris_box = c.toggle(
                tr("setting_media_controls"), is_on("mpris", "true"),
                tr("setting_media_controls_hint_mac"
                   if sys.platform == "darwin"
                   else "setting_media_controls_hint_win"))
    # TV mode (mw_tv): the window as a 10-foot interface, from a remote.
    c = general.card(tr("tv_mode"))
    tv_open_btn = QPushButton(tr("tv_open"))
    tv_open_btn.clicked.connect(lambda _c=False: (d.accept(), QTimer.singleShot(0, win.enter_tv_mode)))
    c.row(tr("tv_mode"), tv_open_btn, tr("tv_mode_hint"),
          search="tv 10-foot sofa couch remote htpc fullscreen kodi")
    tv_start_box = c.toggle(tr("tv_start_in"), is_on("tv_mode_start", "false"),
                            tr("tv_start_hint"))
    tv_inputs_box = c.toggle(tr("tv_inputs"), is_on("tv_inputs", "true"),
                             tr("tv_inputs_hint"))
    tv_found = QLabel(win.tv_inputs_text())
    tv_found.setWordWrap(True)
    c.row(tr("tv_inputs_found"), tv_found, tr("tv_inputs_found_hint"),
          search="lirc gamepad controller joystick cec remote evdev sdl")
    # Supporting the app: feedback, the supporter key, and - last and
    # quiet - whether the app asks for support now and then.
    c = support_card = general.card(tr("donate_support"))
    icon_box = None
    if hasattr(win, "open_answers"):
        # Feedback, and the answers to it (their own window, over
        # this one).
        feedback_btn = QPushButton(tr("feedback_menu") + "…")
        feedback_btn.clicked.connect(lambda _c=False: win.open_feedback())
        answers_btn = QPushButton(win._answers_menu_text())

        def show_answers(_c=False) -> None:
            win.open_answers(d)
            answers_btn.setText(win._answers_menu_text())
        answers_btn.clicked.connect(show_answers)
        # As wide as the two buttons: the column's width for controls
        # cut the second one's text in some languages.
        fb_pair = QWidget()
        fb_pair.setLayout(hbox(feedback_btn, answers_btn, stretch=False))
        fb_pair.layout().setContentsMargins(0, 0, 0, 0)
        fb_pair.setFixedWidth(fb_pair.sizeHint().width())     # kept out of the column
        c.row(tr("fba_settings"), fb_pair, tr("fba_settings_hint"),
              search="feedback answer answers reply replies report idea bug svar")
        fb_pair.ensurePolished()                               # now in the window's style
        fb_pair.setFixedWidth(fb_pair.sizeHint().width())
    if hasattr(win, "open_supporter"):
        from .qt_i18n import format_month
        mine = supporter_key()
        key_btn = QPushButton(tr("set_supporter_open") if mine else tr("set_supporter_enter"))
        key_btn.clicked.connect(lambda _c=False: supporter_window())
        key_row = c.row(tr("supporter_menu"), key_btn,
                        tr("supporter_tip", date=format_month(mine["since"])) if mine else tr("set_supporter_hint"),
                        search="supporter key tip monthly support donation code nyckel")
        shared["supporter_row"] = (key_btn, key_row, support_card)
        # The supporter's app icon - for a supporter only, on unless
        # turned off; applied as it is switched (commit below).
        icon_box = c.toggle(tr("set_supporter_icon"),
                            is_on("supporter_icon", "true"),
                            tr("set_supporter_icon_hint"))
        c.shown_if(c.rows[-1][0], lambda: key_state["mine"] is not None)
    # Asking for a donation now and then (mw_donate): the
    # household's, off for good until switched on again.
    from ..core import donate_rhythm
    ask_box = c.toggle(tr("set_donate_ask"), donate_rhythm.asking(win.settings),
                       tr("set_donate_ask_hint"))

    # About and the version, where a search of Settings finds them:
    # they were only in the menu bar's first menu (issue #31). Over
    # Settings, as its child, so it is never hidden behind it.
    def about() -> None:
        win.show_about(parent=d)
        # Seven taps on its version turn Nerd on; the switch here
        # follows, or closing Settings would store it off again.
        if not is_on("nerd_enabled", "false"):
            return
        box = nerd_switch["box"]
        if box is None:
            # Nerd's pages not built yet: shown, and built as on.
            shell.set_group_enabled("nerd", True)
            for card in link_cards:
                card.refresh()
        elif not box.isChecked():
            box.setChecked(True)
    general.card().link(
        tr("menu_about"), tr("set_about_version", version=VERSION),
        tr("common_open"), about,
        search=f"about version update updates release notes changelog "
               f"{tr('set_card_updates')} {tr('about_check_updates')}")

    def commit_general(put, changed: set, final: bool) -> None:
        put("check_updates", flag(updates_box))
        # Written once chosen ("last" is the default).
        if start_box.currentData() != _places.start_choice(win.settings):
            put(_places.START_KEY, start_box.currentData())
        # Written once switched (on is the default).
        if ask_box.isChecked() != donate_rhythm.asking(win.settings):
            put(donate_rhythm.K_ASK, flag(ask_box))
            win._donate_heart_refresh()
        # Written once switched (on is the default): an untouched
        # window stores nothing.
        if icon_box is not None and icon_box.isChecked() != is_on("supporter_icon", "true"):
            put("supporter_icon", flag(icon_box))
            win._app_icon_refresh()
        if channel_box.currentData() != win._update_channel():
            put("update_channel", channel_box.currentData())
            # Look again now, on the channel just chosen.
            win.settings.setValue("update_check_ts", "0")
            win.settings.setValue("update_banner_dismissed", "")
            QTimer.singleShot(0, win._maybe_check_updates)
        # Written once switched: an untouched window stores nothing.
        for tv_key, tv_box, tv_default in (("tv_mode_start", tv_start_box, "false"),
                                           ("tv_inputs", tv_inputs_box, "true")):
            if tv_box.isChecked() != is_on(tv_key, tv_default):
                put(tv_key, flag(tv_box))
        if mpris_box is not None:
            put("mpris", flag(mpris_box))
            if "mpris" in changed:
                win._apply_media_session_setting()
        if desktop_box is not None:
            put("desktop_entry", flag(desktop_box))
            if "desktop_entry" in changed or final:
                # Answered here, so the status-row offer has nothing
                # left to ask - either way round.
                win.settings.setValue("desktop_entry_asked", "true")
                win.desktop_entry_btn.hide()
            if "desktop_entry" in changed:
                win._apply_desktop_entry(desktop_box.isChecked())
    ctx.page_commits["general"] = commit_general


def build_appearance(ctx: SettingsContext) -> None:
    """Appearance: the theme and its accent, the lists, and what the places
    show."""
    win, d, look = ctx.win, ctx.dialog, ctx.pages["appearance"]
    meta = ctx.pages["metadata"]
    shell, rtl, flag, own, shared = ctx.shell, ctx.rtl, ctx.flag, ctx.own, ctx.shared
    key_state, supporter_key = ctx.key_state, ctx.supporter_key
    restyle_dialog, supporter_window = ctx.restyle_dialog, ctx.supporter_window
    themes = ChoicePicker(
        [ThemeTile(key, tr(f"theme_{key}")) for key in THEME_ORDER],
        win.settings.value("theme", "graphite"),
        groups=[(tr("set_themes_dark"),
                 [k for k in THEME_ORDER if not is_light(k)]),
                (tr("set_themes_light"),
                 [k for k in THEME_ORDER if is_light(k)])],
        fill=True)
    accent_keys = [ACCENT_THEME, *ACCENTS, ACCENT_CUSTOM]
    accents = ChoicePicker(
        [ColorDot(key, tr(f"accent_{key}"),
                  ACCENTS[key][1] if key in ACCENTS else
                  (win.settings.value("accent_custom", "") or ""))
         for key in accent_keys],
        win.settings.value("accent", "blue"), spacing=6)
    # One row of dots where the page is wide enough; they wrap if not.
    accents.setMinimumWidth(min(len(accent_keys) * (ColorDot.D + 6), 440))
    light_box = win._combo(
        [(key, tr(f"theme_{key}")) for key in THEME_ORDER
         if is_light(key) and (key not in SUPPORTER_THEMES
                               or supporter_key() is not None)],
        win.settings.value("theme_light", "light"))
    corner_box = win._combo(
        [("sharp", tr("option_corners_sharp")),
         ("rounded", tr("option_corners_rounded")),
         ("round", tr("option_corners_round"))],
        win.settings.value("corner_style", "rounded"))
    scale_box = win._combo(
        [(v, tr("opt_percent", n=v)) for v in UI_SCALES],
        str(win.settings.value("ui_scale", "100")))

    def sync_pickers() -> None:
        # Each tile shows its theme with the accent chosen now, and
        # the theme's-own dot the theme on show.
        shown = effective_theme(win.settings)
        for tile in themes.tiles:
            tile.accent = (accents.value(),
                           win.settings.value("accent_custom", ""))
        for dot in accents.tiles:
            dot.theme = shown
        themes.refresh()
        accents.refresh()

    def restyle() -> None:
        win._apply_theme_now()
        restyle_dialog()

    def sync_locks() -> None:
        """The supporter themes: a heart and a word on them without a
        key; in the light-theme list only with one."""
        closed = supporter_key() is None
        for tile in themes.tiles:
            tile.set_locked(closed and tile.key in SUPPORTER_THEMES,
                            tr("theme_supporter_tip"))
        light_box.blockSignals(True)
        for key in THEME_ORDER:
            if key not in SUPPORTER_THEMES or not is_light(key):
                continue
            at = light_box.findData(key)
            if not closed and at < 0:
                light_box.addItem(tr(f"theme_{key}"), key)
            elif closed and at >= 0:
                light_box.removeItem(at)
        at = light_box.findData(win.settings.value("theme_light", "light"))
        light_box.setCurrentIndex(max(at, 0))
        light_box.blockSignals(False)

    shared["sync_pickers"] = sync_pickers
    shared["theme_locks"] = (sync_locks, themes)
    sync_pickers()
    sync_locks()

    def pick_theme(key: str) -> None:
        if key in SUPPORTER_THEMES and supporter_key() is None:
            # Not open yet: the supporter window says what opens it,
            # and the theme comes on as soon as a key does.
            themes.set_value(win.settings.value("theme", "graphite"))
            if not hasattr(win, "open_supporter"):
                return
            supporter_window()
            if key_state["mine"] is None:
                return
            themes.set_value(key)
        win.settings.setValue("theme", key)
        restyle()

    def pick_accent(key: str) -> None:
        if key == ACCENT_CUSTOM:
            start = (win.settings.value("accent_custom", "")
                     or P["accent"])
            col = QColorDialog.getColor(
                QColor(start), d, tr("accent_pick_title"),
                QColorDialog.ColorDialogOption.DontUseNativeDialog)
            if not col.isValid():
                accents.set_value(win.settings.value("accent", "blue"))
                return
            hex_ = col.name().upper()
            win.settings.setValue("accent_custom", hex_)
            accents.tiles[-1].color = hex_
        win.settings.setValue("accent", key)
        restyle()

    themes.picked.connect(pick_theme)
    accents.picked.connect(pick_accent)
    light_box.currentIndexChanged.connect(
        lambda _i: (win.settings.setValue("theme_light",
                                           light_box.currentData()),
                    restyle()))
    corner_box.currentIndexChanged.connect(
        lambda _i: (win.settings.setValue("corner_style",
                                           corner_box.currentData()),
                    restyle()))
    density_box = win._combo(
        [("compact", tr("option_compact")), ("medium", tr("option_medium")),
         ("large", tr("option_large")), ("xlarge", tr("option_xlarge"))],
        win.settings.value("view_density", "medium"))
    sort_box = win._combo(
        [("default", tr("sort_provider")),
         ("alpha_asc", tr("option_sort_az")),
         ("alpha_desc", tr("option_sort_za")),
         ("recent", tr("option_sort_recent"))],
        win.settings.value("sort_order", "default"))
    c = theme_card = look.card(tr("set_card_theme"))
    theme_mark = own()
    if theme_mark is not None:
        # The theme is the profile's own: said over its tiles.
        holder = QWidget()
        hv = QVBoxLayout(holder)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.addWidget(theme_mark, 0, Qt.AlignmentFlag.AlignRight)
        hv.addWidget(themes)
        c.widget(holder, " ".join([tr("setting_theme")]
                                  + [t.text() for t in themes.tiles]))
    else:
        c.widget(themes, " ".join([tr("setting_theme")]
                                  + [t.text() for t in themes.tiles]))
    c.row(tr("setting_accent_color"), accents,
          tr("misc_theme_applies_immediately"), stretch_control=True,
          badge=own())
    follow_box = c.toggle(
        tr("setting_follow_system"),
        win.settings.value("theme_follow_system", "false") == "true",
        tr("setting_follow_system_hint"), badge=own())
    light_row = c.row(tr("setting_light_theme"), light_box, badge=own())
    c.shown_if(light_row, follow_box.isChecked)

    def follow(on: bool) -> None:
        win.settings.setValue("theme_follow_system",
                               "true" if on else "false")
        # theme_card, not c: c is the next card by the time it is
        # switched, and the light-theme row stayed as it was.
        theme_card.refresh()
        restyle()

    follow_box.toggled.connect(follow)
    c.row(tr("setting_corners"), corner_box)
    c.row(tr("setting_ui_scale"), scale_box, tr("setting_ui_scale_hint"))
    c = look.card(tr("set_card_lists"))
    c.row(tr("setting_list_size"), density_box)
    c.row(tr("setting_sort_by"), sort_box)

    # What the places show (core/places.py, mw_place_look): Just the
    # list over all of it, the rows over the lists, the sidebar's
    # entries and its fold - each profile's own. Popular right now
    # keeps its one place on Posters & info: a row leads there.
    from .mw_places import PLACE_WORDS
    c = look.card(tr("set_card_places_show"))
    just_box = c.toggle(tr("set_just_list"), _places.just_list(win.settings),
                        tr("set_just_list_hint"), badge=own())

    def chips_row(heading: str, chips: list) -> None:
        box = ChipGroups([(heading, chips)])
        words = " ".join([heading] + [ch.text() for ch in chips])
        mark = own()
        if mark is None:
            c.widget(box, words)
            return
        holder = QWidget()
        hv = QVBoxLayout(holder)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.addWidget(mark, 0, Qt.AlignmentFlag.AlignRight)
        hv.addWidget(box)
        c.widget(holder, words)

    # Pointing the way the words read, as nerd_link's.
    sep = "‹" if rtl else "›"
    row_chips = {r.name: Chip(f"{tr(PLACE_WORDS[r.place])} {sep} {tr(r.words)}",
                              _places.row_on(win.settings, r.name))
                 for r in _places.ROWS.values() if r.own}
    chips_row(tr("set_rows_over_lists"), list(row_chips.values()))
    # "Posters & info": its "&" a letter on the button, not a mnemonic.
    c.link(tr("card_popular"), tr("set_popular_link_hint"), meta.title.replace("&", "&&"),
           lambda: shell.show_page("metadata"),
           search=" ".join([tr("nav_movies"), tr("nav_series")]))
    gone = _places.hidden(win.settings)
    entry_chips = {p: Chip(tr(PLACE_WORDS[p]), p not in gone) for p in _places.HIDEABLE}
    chips_row(tr("set_sidebar_entries"), list(entry_chips.values()))
    c.note(tr("set_sidebar_entries_note"))
    fold_box = c.toggle(tr("places_fold"), _places.folded(win.settings),
                        tr("set_places_fold_hint"), badge=own())

    def commit_appearance(put, changed: set, final: bool) -> None:
        put("view_density", density_box.currentData())
        put("ui_scale", scale_box.currentData())
        put("sort_order", sort_box.currentData())
        # Written once switched: an untouched window stores nothing.
        if just_box.isChecked() != _places.just_list(win.settings):
            put(_places.JUST_LIST_KEY, flag(just_box))
        for name, chip in row_chips.items():
            if chip.isChecked() != _places.row_on(win.settings, name):
                put(_places.ROWS[name].key, flag(chip))
        want = _places.hidden_text(p for p, ch in entry_chips.items() if not ch.isChecked())
        if want != _places.hidden_text(_places.hidden(win.settings)):
            put(_places.HIDDEN_KEY, want)
        if fold_box.isChecked() != _places.folded(win.settings):
            put(_places.FOLDED_KEY, flag(fold_box))
    ctx.page_commits["appearance"] = commit_appearance


def build_shortcuts(ctx: SettingsContext) -> None:
    """Keyboard shortcuts: every rebindable action and its key, recorded on
    a click, applied as it changes."""
    win, d, keys_page = ctx.win, ctx.dialog, ctx.pages["shortcuts"]
    soon = ctx.soon
    from ..core import shortcut_keys
    from .shortcuts import ShortcutEditor
    keys_shown = win.settings.value(shortcut_keys.SETTING, "") or ""
    key_editor = ShortcutEditor(keys_page.card(tr("sc_title")), keys_shown, d)
    # The key rows change no control the change hookups watch: they
    # say so.
    key_editor.changed.connect(soon)

    def commit_shortcuts(put, changed: set, final: bool) -> None:
        # Written only when changed: nothing stored and nothing
        # changed is not a setting.
        keys_now = key_editor.text()
        if keys_now != (win.settings.value(shortcut_keys.SETTING, "") or ""):
            put(shortcut_keys.SETTING, keys_now)
        if shortcut_keys.SETTING in changed:
            win.apply_shortcuts()
    ctx.page_commits["shortcuts"] = commit_shortcuts


def build_profiles(ctx: SettingsContext) -> None:
    """Profiles, one per person in the household (ui/profiles_ui.py)."""
    win, d, prof_page = ctx.win, ctx.dialog, ctx.pages["profiles"]
    from .profiles_ui import ProfilesPage
    ProfilesPage(prof_page, win, d)


def build_parental(ctx: SettingsContext) -> None:
    """Parental lock: the PIN set, changed or removed, and locked now."""
    win, d, par = ctx.win, ctx.dialog, ctx.pages["parental"]
    set_pin_btn = QPushButton(tr("pin_set_change"))
    remove_pin_btn = QPushButton(tr("pin_remove"))
    lock_now_btn = QPushButton(tr("pin_lock_now"))
    c = par.card(tr("set_card_pin"))
    pin_row = c.row(tr("pin_none_set"), None)
    c.layout_row(hbox(set_pin_btn, remove_pin_btn, lock_now_btn))
    c.note(tr("parental_hint"))

    def refresh_pin_status():
        if win.parental.has_pin():
            pin_row.label.setText(tr("pin_set_unlocked")
                                  if win.parental.session_unlocked
                                  else tr("pin_set_locked"))
        else:
            pin_row.label.setText(tr("pin_none_set"))
        remove_pin_btn.setEnabled(win.parental.has_pin())
        lock_now_btn.setEnabled(
            win.parental.has_pin()
            and win.parental.session_unlocked)

    def set_pin():
        if win.parental.has_pin() and not win._request_unlock():
            return
        pin = win._ask_new_parental_pin(d, tr("pin_new_prompt"))
        if pin:
            win.parental.set_pin(pin)
        refresh_pin_status()

    def remove_pin():
        if not win._request_unlock():
            return
        win.parental.clear_pin()
        refresh_pin_status()
        win._load_categories()

    def lock_now():
        win.parental.lock_session()
        refresh_pin_status()
        win._load_categories()

    set_pin_btn.clicked.connect(set_pin)
    remove_pin_btn.clicked.connect(remove_pin)
    lock_now_btn.clicked.connect(lock_now)
    refresh_pin_status()


def build_advanced(ctx: SettingsContext) -> None:
    """Advanced: maintenance, every cache, the system's own switch and
    Reset all."""
    win, d, adv = ctx.win, ctx.dialog, ctx.pages["advanced"]
    is_on, flag, nerd_link = ctx.is_on, ctx.flag, ctx.nerd_link
    # Disk-cache controls: covers/logos accumulate under
    # QStandardPaths.CacheLocation and don't clean themselves. The
    # live cache is the shared "images" dir; the two legacy dirs
    # predate the loaders sharing one directory - keep them in the
    # sweep so files from old sessions still get counted and cleared.
    cache_dirs = [default_image_cache_dir("images"),
                  default_image_cache_dir("logos"),
                  default_image_cache_dir("posters")]
    clear_cache_btn = QPushButton(tr("btn_clear"))
    clear_cache_btn.setAccessibleName(tr("settings_image_cache_clear"))
    ts_reset_btn = QPushButton(tr("common_reset"))
    ts_reset_btn.setAccessibleName(tr("ts_reset_broken"))
    c = adv.card(tr("sec_maintenance"))
    c.row(tr("ts_reset_broken"), ts_reset_btn, tr("ts_reset_hint"))
    maint = c
    # Every cache in one card: the image cache was here and the local
    # files' three on the Local files page. (The guide's own cache
    # stays on the Guide page, beside fetching it again.)
    loc_clear_lib = QPushButton(tr("local_clear_lib_cache"))
    loc_clear_post = QPushButton(tr("local_clear_posters"))
    loc_clear_thumb = QPushButton(tr("local_clear_thumbs"))
    caches = adv.card(tr("set_card_caches"))
    cache_row = caches.row(tr("settings_image_cache_label", size="…"),
                           clear_cache_btn,
                           tr("settings_image_cache_hint"))
    caches.layout_row(hbox(loc_clear_lib, loc_clear_post,
                           loc_clear_thumb), tr("nav_local"))
    loc_note = caches.note(tr("set_caches_hint"))
    c = maint

    def loc_cleared():
        loc_note.setText(tr("local_cleared"))

    loc_clear_lib.clicked.connect(
        lambda: (win._local_clear_library_cache(), loc_cleared()))
    loc_clear_post.clicked.connect(
        lambda: (win._local_clear_poster_cache(), loc_cleared()))
    loc_clear_thumb.clicked.connect(
        lambda: (win._local_clear_thumbs(), loc_cleared()))

    def show_cache_size(total: int) -> None:
        if sip.isdeleted(cache_row.label):
            return
        cache_row.label.setText(
            tr("settings_image_cache_label", size=format_size(total)))
        clear_cache_btn.setEnabled(total > 0)

    def cache_size() -> int:
        return sum(dir_size_bytes(cdir) for cdir in cache_dirs)

    def refresh_cache_label() -> None:
        # Off the window's thread: every cached logo and poster is a
        # stat, tens of thousands of them for a big source.
        run_async(win.pool, cache_size, show_cache_size)

    def clear_cache() -> None:
        for cdir in cache_dirs:
            clear_directory(cdir)
        # Also drop the in-memory LRUs so a re-scroll doesn't just
        # rewrite the same pixmaps back to disk from RAM.
        win.logos.cache.clear()
        win.poster_art.cache.clear()
        refresh_cache_label()

    clear_cache_btn.clicked.connect(clear_cache)

    def reset_ts() -> None:
        win._clear_ts_broken()
        win._flash_status(tr("ts_reset_done"))

    ts_reset_btn.clicked.connect(reset_ts)
    # Portable Windows build: let the user add Start-menu/desktop
    # shortcuts so it feels installed. Each is just a .lnk file (no
    # registry), easy to delete. Hidden on Linux/macOS.
    if sys.platform == "win32":
        win_sc_btn = QPushButton(tr("win_shortcut_btn"))

        def _make_win_shortcut() -> None:
            from ..core.platform_windows import create_shortcut
            made = create_shortcut()
            win._flash_status(tr("win_shortcut_done") if made
                               else tr("win_shortcut_fail"))

        win_sc_btn.clicked.connect(_make_win_shortcut)
        c.row(tr("win_shortcut_hint"), win_sc_btn)
    nerd_link(maint, "set_link_diag",
              [tr("nerd_log_level"), tr("nerd_log_file"),
               tr("nerd_card_view"), tr("nerd_card_health")], "diag")
    after_shown(d, refresh_cache_label)
    x11_box = None
    if sys.platform.startswith("linux"):
        c = adv.card(tr("set_card_system"))
        x11_box = c.toggle(tr("setting_force_x11"),
                           is_on("force_x11", "false"),
                           tr("setting_force_x11_hint"))
    reset_btn = QPushButton(tr("set_reset_btn"), objectName="Danger")
    reset_btn.clicked.connect(lambda: win._reset_all_settings(d))
    c = adv.card(tr("set_card_reset"))
    c.row(tr("set_reset_label"), reset_btn, tr("set_reset_hint"))

    def commit_advanced(put, changed: set, final: bool) -> None:
        if x11_box is not None:
            put("force_x11", flag(x11_box))
    ctx.page_commits["advanced"] = commit_advanced
