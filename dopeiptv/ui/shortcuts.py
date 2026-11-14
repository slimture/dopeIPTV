"""Keyboard shortcuts in Settings: a card on the Advanced page with every
action and its key, for everyone (core/shortcut_keys.py holds the rules).

A key is a button: click it and press the new combination - Esc gives up,
and so does clicking anywhere else. ✕ leaves the action without a key, ↺
puts the app's own back, Reset to defaults puts them all back. A key
another action had moves to this one and both rows say so; a key the app
keeps for itself is refused, with the reason. Keys are shown as the system
writes them (⌘ on a Mac) and stored in Qt's portable form.
"""

from __future__ import annotations

import re
import sys

from PyQt6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from ..core import shortcut_keys
from ..i18n import tr

_PORTABLE = QKeySequence.SequenceFormat.PortableText
_NATIVE = QKeySequence.SequenceFormat.NativeText


def native(seq: str) -> str:
    """*seq* (portable text) as this system writes it - ⌘⇧M on a Mac."""
    if not seq:
        return ""
    return QKeySequence(seq, _PORTABLE).toString(_NATIVE) or seq


_TIP_KEY = re.compile(r"\s*[(（][^()（）]*[)）]\s*$")


def tip_with_key(text: str, default: str, active: str) -> str:
    """A tooltip that ends in its key, "(Ctrl+B)", naming *active* instead
    where that is not the app's own key - and on a Mac, where Ctrl is ⌘."""
    if active == default and sys.platform != "darwin":
        return text
    m = _TIP_KEY.search(text)
    head = text[:m.start()] if m else text
    if not active:
        return head
    if m and "（" in m.group(0):
        return f"{head}（{native(active)}）"
    return f"{head} ({native(active)})"


_K = Qt.Key
# Held alone, these are half of a combination: recording waits for the rest.
_WAIT = {k.value for k in (_K.Key_Control, _K.Key_Shift, _K.Key_Alt, _K.Key_Meta, _K.Key_AltGr,
                           _K.Key_Super_L, _K.Key_Super_R, _K.Key_Hyper_L, _K.Key_Hyper_R,
                           _K.Key_CapsLock, _K.Key_NumLock, _K.Key_ScrollLock, _K.Key_unknown)}
_M = Qt.KeyboardModifier


def combination(key: int, mods: Qt.KeyboardModifier, text: str) -> str:
    """A key press as portable text. Shift counts as QKeySequenceEdit counts
    it - not when it only types the symbol ("!", not Shift+!) - and the
    keypad marker is left out (a Mac sets it on the arrows)."""
    held = 0
    if mods & _M.ShiftModifier and (not text or not text[0].isprintable()
                                    or text[0].isalnum() or text[0].isspace()):
        held |= _M.ShiftModifier.value
    for m in (_M.ControlModifier, _M.AltModifier, _M.MetaModifier):
        if mods & m:
            held |= m.value
    try:
        return QKeySequence(held | int(key)).toString(_PORTABLE)
    except (TypeError, ValueError, OverflowError):
        return ""


class KeyButton(QPushButton):
    """An action's key, as a button: a click starts recording, the next
    combination pressed is the new key (``captured``: action id, portable
    text). Esc, or the focus going elsewhere, gives up."""

    captured = pyqtSignal(str, str)

    def __init__(self, sid: str, label: str) -> None:
        super().__init__()
        self.recording = False
        self._seq = ""
        self.sid = sid
        self.setObjectName("KeyCap")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tr("sc_capture_tip"))
        self.setAccessibleName(label)
        self.setMinimumWidth(130)
        self.clicked.connect(self.start)
        self._show()

    def sequence(self) -> str:
        return self._seq

    def set_sequence(self, seq: str) -> None:
        self._seq = seq
        self._show()

    def _show(self) -> None:
        from .theme import P
        self.setText(tr("sc_press") if self.recording else (native(self._seq) or tr("sc_none")))
        # Listening in the accent colour; no key in a muted one.
        colour = P["accent"] if self.recording else ("" if self._seq else P.get("muted", ""))
        self.setStyleSheet(f"QPushButton#KeyCap {{ color: {colour}; }}" if colour else "")

    def start(self) -> None:
        self.recording = True
        self._show()
        self.setFocus(Qt.FocusReason.MouseFocusReason)

    def stop(self) -> None:
        self.recording = False
        self._show()

    def focusOutEvent(self, event) -> None:          # noqa: N802 - Qt hook
        if getattr(self, "recording", False):
            self.stop()
        super().focusOutEvent(event)

    def event(self, e) -> bool:
        if getattr(self, "recording", False):
            t = e.type()
            # Every key is the new one while recording: no shortcut, no
            # dialog's Esc, no Tab to the next control gets it first.
            if t == QEvent.Type.ShortcutOverride:
                e.accept()
                return True
            if t == QEvent.Type.KeyPress:
                self._pressed(e)
                return True
            if t == QEvent.Type.KeyRelease:
                return True
        return super().event(e)

    def _pressed(self, e) -> None:
        key = e.key()
        if key in _WAIT:
            return
        mods = e.modifiers()
        bare = not (mods & (_M.ControlModifier | _M.AltModifier | _M.MetaModifier | _M.ShiftModifier))
        if key == _K.Key_Escape.value and bare:
            self.stop()
            return
        seq = combination(key, mods, e.text())
        self.stop()
        if seq:
            self.captured.emit(self.sid, seq)


def _icon(kind: str) -> QIcon:
    """A cross ("clear") or a circular arrow turning back ("reset"), drawn:
    "↺" is an empty box in fonts without it. Dimmed while there is nothing
    to clear or put back."""
    from .theme import P
    icon = QIcon(_drawn(kind, P["text"]))
    icon.addPixmap(_drawn(kind, P.get("muted", P["text2"])), QIcon.Mode.Disabled)
    return icon


def _drawn(kind: str, colour: str) -> QPixmap:
    s, scale = 14, 3
    pm = QPixmap(s * scale, s * scale)
    pm.setDevicePixelRatio(float(scale))
    pm.fill(Qt.GlobalColor.transparent)
    pr = QPainter(pm)
    pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    col = QColor(colour)
    pen = QPen(col)
    pen.setWidthF(s * 0.11)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pr.setPen(pen)
    if kind == "clear":
        pr.drawLine(QPointF(s * 0.28, s * 0.28), QPointF(s * 0.72, s * 0.72))
        pr.drawLine(QPointF(s * 0.72, s * 0.28), QPointF(s * 0.28, s * 0.72))
    else:
        pr.drawArc(QRectF(s * 0.22, s * 0.22, s * 0.56, s * 0.56), 110 * 16, -290 * 16)
        pr.setPen(Qt.PenStyle.NoPen)
        pr.setBrush(col)
        head = QPainterPath()
        head.moveTo(s * 0.50, s * 0.06)
        head.lineTo(s * 0.26, s * 0.21)
        head.lineTo(s * 0.50, s * 0.36)
        head.closeSubpath()
        pr.drawPath(head)
    pr.end()
    return pm


class _RowButton(QPushButton):
    """✕ or ↺ beside a key; ``hit`` says which action it is for."""

    hit = pyqtSignal(str)

    def __init__(self, kind: str, sid: str, tip: str) -> None:
        super().__init__()
        self.setIcon(_icon(kind))
        self.sid = sid
        self.setObjectName("KeyCapTool")
        self.setToolTip(tip)
        self.setAccessibleName(tip)
        self.setFixedWidth(34)
        self.clicked.connect(self._hit)

    def _hit(self) -> None:
        self.hit.emit(self.sid)


def why(r: shortcut_keys.Reserved) -> str:
    """Why a key is the app's, in words (a menu entry named without its …)."""
    return tr(r.why, name=tr(r.name).rstrip("…").strip()) if r.name else tr(r.why)


class ShortcutEditor(QObject):
    """The card's rows and the overrides they hold: changed by the rows
    (``changed``), read back by Settings (``text()``)."""

    changed = pyqtSignal()

    def __init__(self, card, stored: str, parent: QWidget) -> None:
        from .settings_ui import hbox
        super().__init__(parent)
        self.overrides = shortcut_keys.tidy(shortcut_keys.parse(stored))
        self.keys: dict[str, KeyButton] = {}
        self.clears: dict[str, _RowButton] = {}
        self.resets: dict[str, _RowButton] = {}
        self.lines: dict[str, QLabel] = {}
        card.note(tr("sc_card_intro"))
        for sid, default, label_key in shortcut_keys.ACTIONS:
            label = tr(label_key)
            key = KeyButton(sid, label)
            key.captured.connect(self._captured)
            clear = _RowButton("clear", sid, tr("sc_clear_tip"))
            clear.hit.connect(self.clear)
            reset = _RowButton("reset", sid, tr("sc_reset_one_tip", shortcut=native(default)))
            reset.hit.connect(self.reset)
            line = QLabel("")
            line.setWordWrap(True)
            line.setObjectName("SettingsHint")
            line.hide()
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(key, 1)
            row.addWidget(clear)
            row.addWidget(reset)
            # Found by the card's name too, in the language shown, as the
            # one row of the old editor was.
            card.row(label, row, search=f"{tr('sc_title')} keyboard shortcut key hotkey", below=line)
            self.keys[sid], self.clears[sid], self.resets[sid], self.lines[sid] = key, clear, reset, line
        self.reset_all_btn = QPushButton(tr("sc_reset"))
        self.reset_all_btn.clicked.connect(self.reset_all)
        card.layout_row(hbox(self.reset_all_btn), f"{tr('sc_title')} keyboard shortcuts")
        self._show_all()

    # -- what is stored ------------------------------------------------------------

    def text(self) -> str:
        return shortcut_keys.dump(self.overrides)

    def current(self) -> dict[str, str]:
        return shortcut_keys.effective(self.overrides)

    # -- the rows ----------------------------------------------------------------------

    def _show_all(self) -> None:
        eff = self.current()
        for sid, default, _label in shortcut_keys.ACTIONS:
            self.keys[sid].set_sequence(eff[sid])
            self.clears[sid].setEnabled(bool(eff[sid]))
            self.resets[sid].setEnabled(eff[sid] != default)
        self.reset_all_btn.setEnabled(bool(self.overrides))

    def _say(self, sid: str, text: str, level: str = "") -> None:
        from .theme import P
        line = self.lines[sid]
        colour = {"error": P["error"], "warn": P.get("warn", P["text2"])}.get(level, P["text2"])
        line.setStyleSheet(f"color:{colour}; font-size:12px;")
        line.setText(text)
        line.setVisible(bool(text))
        line.setProperty("level", level if text else "")

    def _hush(self, sid: str | None = None) -> None:
        """A new change: what was said about *sid* goes, and so does any
        refusal; a row that lost its key keeps saying where it went until
        that row is changed itself."""
        for other, line in self.lines.items():
            if sid is None or other == sid or line.property("level") == "error":
                self._say(other, "")

    def _captured(self, sid: str, seq: str) -> None:
        self._hush(sid)
        if shortcut_keys.same(seq, self.current()[sid]):
            return
        r = shortcut_keys.reserved(seq)
        if r is not None and r.level == shortcut_keys.BLOCKED:
            self._say(sid, tr("sc_blocked", shortcut=native(seq), why=why(r)), "error")
            return
        self._assign(sid, seq, r)

    def _assign(self, sid: str, seq: str, warn: shortcut_keys.Reserved | None = None) -> None:
        self.overrides, lost = shortcut_keys.assign(self.overrides, sid, seq)
        self._show_all()
        shown = native(seq)
        said = [tr("sc_taken", shortcut=shown, other=tr(shortcut_keys.LABELS[o])) for o in lost]
        if warn is not None and warn.level == shortcut_keys.WARN:
            said.append(tr("sc_warned", shortcut=shown, why=why(warn)))
        self._say(sid, " ".join(said), "warn")
        for other in lost:
            self._say(other, tr("sc_lost", shortcut=shown, other=tr(shortcut_keys.LABELS[sid])), "warn")
        self.changed.emit()

    def clear(self, sid: str) -> None:
        """*sid* has no key."""
        self._hush(sid)
        self._assign(sid, "")

    def reset(self, sid: str) -> None:
        """*sid* has the app's own key again (taken from whoever has it)."""
        self._hush(sid)
        self._assign(sid, shortcut_keys.DEFAULTS[sid])

    def reset_all(self) -> None:
        self._hush()
        self.overrides = {}
        self._show_all()
        self.changed.emit()
