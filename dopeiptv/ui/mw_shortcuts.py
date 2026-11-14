"""Keyboard-shortcut mixin for MainWindow.

The single-key player shortcuts (pause/mute/pop-out/record/stats/guide/fullscreen)
and the shortcut registry (keyPressEvent dispatch, the keys in force - the
user's over the app's own (core/shortcut_keys.py) - install and live apply,
and the video-focused arrow handling). These are thin dispatch into
self.player / self.* - no playback logic lives here.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QApplication, QLineEdit

from ..core import shortcut_keys
from ..core.log import log
from ..i18n import tr
from ..media.embedded import _is_bare_arrow, _is_shift_arrow
from .shortcuts import tip_with_key


class _ShortcutsMixin:
    """MainWindow mixin: the keyboard-shortcuts editor and dispatch."""
    def _multiview_active(self):
        """The multiview window while it is the one in use, else None.

        The shortcuts are application-wide (see _install_shortcuts), so in
        the grid Space reached the docked player - which multiview had
        stopped, so "resume" started the old channel again behind it - while
        the cell you were watching never paused. There, the player keys act
        on the focused cell instead, and the rest do nothing."""
        win = getattr(self, "_multiview_win", None)
        if win is not None and QApplication.activeWindow() is win:
            return win
        return None

    def _toggle_pause_shortcut(self) -> None:
        mv = self._multiview_active()
        if mv is not None:
            if mv._focused is not None:
                mv._focused.toggle_pause()
            return
        if self.player and self.player.isVisible():
            self.player.toggle_pause()

    def _typing(self) -> bool:
        """True when a text field has focus, so single-letter player shortcuts
        don't steal the keystroke from the search box etc.

        The field that has the keys now, in whichever window is active - not
        this window's last one. Over the pop-out, and in fullscreen on macOS
        (the pop-out filling the screen), a search box clicked into earlier
        still counted as typing, so I, M, P and R did nothing there."""
        return isinstance(QApplication.focusWidget(), QLineEdit)

    def _player_up(self) -> bool:
        if self._multiview_active() is not None:
            return False
        return bool(self.player and self.player.isVisible())

    def _shortcut_mute(self) -> None:
        mv = self._multiview_active()
        if mv is not None:
            if mv._focused is not None:
                mv._focused.set_muted(not mv._focused.is_muted())
            return
        if not self._typing() and self._player_up():
            self.player.toggle_mute()

    def _shortcut_zap(self, step: int) -> None:
        if self._multiview_active() is None:
            self._zap(step)

    def _shortcut_last_channel(self) -> None:
        if self._multiview_active() is None:
            self._last_channel()

    def _shortcut_popout(self) -> None:
        if not self._typing() and self._player_up():
            self._toggle_popout()

    def _shortcut_record(self) -> None:
        if not self._typing() and self._player_up():
            self.player.record_menu.emit(self.player.rec_btn)

    def _shortcut_stats(self) -> None:
        if not self._typing() and self._player_up():
            self.player._show_stats()

    def _shortcut_epg_guide(self) -> None:
        if not self._typing():
            self._open_epg_guide()

    # -- fullscreen ----------------------------------------------------------------

    def _toggle_fullscreen_shortcut(self) -> None:
        mv = self._multiview_active()
        if mv is not None:
            mv._toggle_max()
            return
        if self.player and self.player.isVisible():
            self._toggle_player_fullscreen()

    def _number_key(self, event) -> bool:
        """A digit, or Enter after digits, that reached the window - from
        the player, the pop-out, or anything else nobody took it from:
        number zapping (the channel list hands over its own). True when
        taken."""
        # Shift is fine: an AZERTY keyboard types its digits with it.
        held = (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
                | Qt.KeyboardModifier.MetaModifier)
        if event.modifiers() & held or self._typing():
            return False
        if self._multiview_active() is not None:
            return False
        txt = event.text()
        if len(txt) == 1 and txt.isdecimal():
            if not (self._live_playing() or self.mode in ("live", "fav")):
                return False
            self._channel_digit(txt, from_player=True)
            return True
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._chan_buffer:
            self._channel_jump()
            return True
        return False

    def keyPressEvent(self, event) -> None:
        if self._number_key(event):
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            bare = _is_bare_arrow(event.modifiers())
            shift = _is_shift_arrow(event.modifiers())
            if bare or shift:
                # Scrub the player (fine-seek, or Shift = timeline step) before
                # any zap - covers the fullscreen case, where this handler would
                # otherwise zap.
                if self._handle_player_arrow(event.key(), step=shift):
                    return
                if bare and self._player_fs:
                    self._zap(1 if event.key() == Qt.Key.Key_Right else -1)
                    return
        super().keyPressEvent(event)

    # Rebindable actions: (id, default key sequence, i18n label key), in
    # display order - core/shortcut_keys.py holds them and the rules for the
    # user's own keys. The callbacks are wired in _install_shortcuts.
    SHORTCUT_ACTIONS = shortcut_keys.ACTIONS

    def _shortcut_zap_next(self) -> None:
        self._shortcut_zap(1)

    def _shortcut_zap_prev(self) -> None:
        self._shortcut_zap(-1)

    def _shortcut_sidebar(self) -> None:
        self.side_btn.toggle()

    def _shortcut_focus_mode(self) -> None:
        self._set_focus_mode(not self._focus_mode)

    def _shortcut_callbacks(self):
        return {
            "zap_next": self._shortcut_zap_next,
            "zap_prev": self._shortcut_zap_prev,
            "last_channel": self._shortcut_last_channel,
            "pause": self._toggle_pause_shortcut,
            "fullscreen": self._toggle_fullscreen_shortcut,
            "mute": self._shortcut_mute,
            "popout": self._shortcut_popout,
            "record": self._shortcut_record,
            "stats": self._shortcut_stats,
            "epg_guide": self._shortcut_epg_guide,
            "epg_search": self._open_epg_search,
            "reminders": self._open_reminders,
            "sidebar": self._shortcut_sidebar,
            "focus_mode": self._shortcut_focus_mode,
        }

    def shortcut_keys(self) -> dict[str, str]:
        """Each action's key in force, in Qt's portable text ("" for none):
        the user's over the app's own."""
        return shortcut_keys.from_settings(self.settings)

    def shortcut_sequence(self, sid: str) -> str:
        """The key in force for *sid* ("" when it has none)."""
        return self.shortcut_keys().get(sid, "")

    def _install_shortcuts(self) -> None:
        """Create every rebindable QShortcut from the key in force. Kept in
        self._shortcuts so apply_shortcuts() can rebind them live when the
        user edits them in Settings."""
        # The old editor's keys (one setting per action), kept: moved once.
        moved = shortcut_keys.migrate(self.settings)
        if moved:
            log.info("shortcuts: %d key(s) of the old editor carried over", moved)
        cbs = self._shortcut_callbacks()
        keys = self.shortcut_keys()
        self._shortcuts = {}
        for sid, _default, _label in self.SHORTCUT_ACTIONS:
            sc = QShortcut(QKeySequence(keys.get(sid, "")), self)
            sc.activated.connect(cbs[sid])
            # Application-wide, not window-wide. Qt's default context only
            # fires a shortcut while THIS window is the active one, and in
            # fullscreen on macOS the active window is the pop-out mirror -
            # so space did not pause, and nor did any other shortcut, in the
            # one mode where they matter most.
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
            self._shortcuts[sid] = sc
        self._shortcut_tips(keys)

    def apply_shortcuts(self) -> None:
        """Rebind the live QShortcuts to the keys in force now."""
        keys = self.shortcut_keys()
        for sid, _default, _label in self.SHORTCUT_ACTIONS:
            sc = getattr(self, "_shortcuts", {}).get(sid)
            if sc is not None:
                seq = QKeySequence(keys.get(sid, ""))
                if sc.key() != seq:
                    sc.setKey(seq)
        self._shortcut_tips(keys)

    # Buttons whose tooltip ends in their key, "(Ctrl+B)".
    _KEY_TIPS = (("sidebar", "side_btn", "tooltip_toggle_sidebar"),
                 ("focus_mode", "focus_btn", "tooltip_hide_list"))

    def _shortcut_tips(self, keys: dict[str, str]) -> None:
        """The tooltips that name a key say the one in force, as this
        system writes it (⌘B on a Mac)."""
        for sid, attr, tip in self._KEY_TIPS:
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.setToolTip(tip_with_key(tr(tip), shortcut_keys.DEFAULTS[sid], keys.get(sid, "")))

    def _handle_player_arrow(self, key, step: bool = False) -> bool:
        """Left/Right while the player is up: a bare arrow fine-seeks (VOD /
        catch-up / live buffer), Shift+arrow (*step*) jumps the timeshift
        timeline into the archive - instead of letting the key navigate the
        channel list (which with auto-preview swaps the channel). Called from
        the list's key handler so it works even where the app-level input filter
        doesn't catch it (seen on macOS). Returns True when it consumed the
        key."""
        p = self.player
        if not p or not p.isVisible():
            return False
        return p.arrow_scrub(key == Qt.Key.Key_Left, step=step)
