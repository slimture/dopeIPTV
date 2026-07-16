"""Keyboard-shortcut mixin for MainWindow.

The single-key player shortcuts (pause/mute/PiP/record/stats/guide/fullscreen)
and the shortcut registry (keyPressEvent dispatch, per-action key storage,
install/apply, and the video-focused arrow handling). These are thin dispatch
into self.player / self.* - no playback logic lives here. Moved out of
main_window.py; behaviour is identical.
"""
from __future__ import annotations

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QLineEdit

from ..media.embedded import _is_bare_arrow, _is_shift_arrow


class _ShortcutsMixin:
    def _toggle_pause_shortcut(self) -> None:
        if self.player and self.player.isVisible():
            self.player.toggle_pause()

    def _typing(self) -> bool:
        """True when a text field has focus, so single-letter player shortcuts
        don't steal the keystroke from the search box etc."""
        return isinstance(self.focusWidget(), QLineEdit)

    def _player_up(self) -> bool:
        return bool(self.player and self.player.isVisible())

    def _shortcut_mute(self) -> None:
        if not self._typing() and self._player_up():
            self.player.toggle_mute()

    def _shortcut_pip(self) -> None:
        if not self._typing() and self._player_up():
            self._toggle_pip()

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
        if self.player and self.player.isVisible():
            self._toggle_player_fullscreen()

    def keyPressEvent(self, event) -> None:
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

    # Rebindable actions: (id, default key sequence, i18n label key). The
    # callbacks are wired in _install_shortcuts. Order here is display order.
    SHORTCUT_ACTIONS = (
        ("zap_next", "Ctrl+Right", "sc_next_channel"),
        ("zap_prev", "Ctrl+Left", "sc_prev_channel"),
        ("last_channel", "Backspace", "sc_last_channel"),
        ("pause", "Space", "sc_play_pause"),
        ("fullscreen", "F", "sc_fullscreen"),
        ("mute", "M", "sc_mute"),
        ("pip", "P", "sc_pip"),
        ("record", "R", "sc_record"),
        ("stats", "I", "sc_stats"),
        ("epg_guide", "Ctrl+G", "sc_epg_guide"),
        ("epg_search", "Ctrl+Shift+F", "sc_epg_search"),
        ("reminders", "Ctrl+Shift+R", "sc_reminders"),
        ("sidebar", "Ctrl+B", "sc_sidebar"),
        ("focus_mode", "Ctrl+Shift+M", "sc_focus_mode"),
    )

    def _shortcut_callbacks(self):
        return {
            "zap_next": lambda: self._zap(1),
            "zap_prev": lambda: self._zap(-1),
            "last_channel": self._last_channel,
            "pause": self._toggle_pause_shortcut,
            "fullscreen": self._toggle_fullscreen_shortcut,
            "mute": self._shortcut_mute,
            "pip": self._shortcut_pip,
            "record": self._shortcut_record,
            "stats": self._shortcut_stats,
            "epg_guide": self._shortcut_epg_guide,
            "epg_search": self._open_epg_search,
            "reminders": self._open_reminders,
            "sidebar": lambda: self.side_btn.toggle(),
            "focus_mode": lambda: self._set_focus_mode(not self._focus_mode),
        }

    def _shortcut_key(self, sid: str) -> str:
        # Scope per-OS: modifier conventions differ (Qt maps Ctrl->Cmd on
        # macOS) and a config shared across machines shouldn't cross-bind.
        return f"shortcut/{sys.platform}/{sid}"

    def shortcut_sequence(self, sid: str, default: str) -> str:
        """The user's key sequence for *sid*, or the default when unset."""
        return self.settings.value(self._shortcut_key(sid), default) or default

    def save_shortcut(self, sid: str, seq: str, default: str) -> None:
        """Persist (or clear, when equal to default/empty) one binding."""
        if seq and seq != default:
            self.settings.setValue(self._shortcut_key(sid), seq)
        else:
            self.settings.remove(self._shortcut_key(sid))

    def _install_shortcuts(self) -> None:
        """Create every rebindable QShortcut from its saved (or default) key
        sequence. Kept in self._shortcuts so apply_shortcuts() can rebind them
        live when the user edits them in Settings."""
        cbs = self._shortcut_callbacks()
        self._shortcuts = {}
        for sid, default, _label in self.SHORTCUT_ACTIONS:
            seq = self.shortcut_sequence(sid, default)
            sc = QShortcut(QKeySequence(seq), self, activated=cbs[sid])
            self._shortcuts[sid] = sc

    def apply_shortcuts(self) -> None:
        """Re-read the saved key sequences and rebind the live QShortcuts."""
        for sid, default, _label in self.SHORTCUT_ACTIONS:
            sc = getattr(self, "_shortcuts", {}).get(sid)
            if sc is not None:
                sc.setKey(QKeySequence(self.shortcut_sequence(sid, default)))

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
