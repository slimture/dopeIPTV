"""What a screen reader is told about the app's controls (Orca, VoiceOver,
Narrator and NVDA all read Qt's QAccessible), and the one rule the tests
hold every window to.

PyQt6 does not wrap QAccessible itself, so nothing here asks the
accessibility tree: Qt builds it from what a widget says - its accessible
name, else its own text (a button's, a check box's), else the QLabel whose
buddy it is or the group box it sits in. ``spoken_name`` follows those
rules, and ``unnamed`` lists the controls a person could reach that would
be read as nothing but their kind ("button", "edit").

A name is never more than the screen shows: a title, a channel or a
programme in one is the one painted there, so private viewing reads out
nothing the picture does not."""
from __future__ import annotations

import re

from PyQt6.QtCore import QEvent, QItemSelectionModel, QObject, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QAbstractScrollArea, QAbstractSlider,
    QAbstractSpinBox, QComboBox, QGroupBox, QLabel, QLineEdit, QMenu,
    QMenuBar, QPlainTextEdit, QScrollBar, QStyle, QTabBar, QTextEdit, QWidget,
)

from .theme import P

#: Kinds of control a person works with - each needs words of its own.
CONTROLS = (QAbstractButton, QLineEdit, QComboBox, QAbstractSlider, QAbstractSpinBox,
            QAbstractItemView, QTextEdit, QPlainTextEdit)

#: Qt's own pieces, which say what they are by their role: a scroll bar, a
#: menu, a tab bar (its tabs are named by their text).
_QT_OWN = (QScrollBar, QMenu, QMenuBar, QTabBar)


def _strip_amp(text: str) -> str:
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&").strip()


def _words(text: str) -> str:
    """*text* if it says something: a glyph alone ("✕", "⚙", "●") is read
    out as its Unicode name, or not at all."""
    return text if any(ch.isalnum() for ch in text) else ""


def _buddy_words(w: QWidget) -> str:
    """The label Qt reads for *w*: a sibling QLabel whose buddy it is, or
    the title of the group box it sits in."""
    parent = w.parentWidget()
    if parent is None:
        return ""
    for o in parent.children():
        if isinstance(o, QLabel) and o.buddy() is w:
            return _strip_amp(o.text())
    if isinstance(parent, QGroupBox):
        return _strip_amp(parent.title())
    return ""


def spoken_name(w: QWidget) -> str:
    """The name a screen reader announces for *w*, as Qt works it out."""
    own = w.accessibleName().strip()
    if own:
        return _words(own)
    if isinstance(w, QAbstractButton):
        text = _words(_strip_amp(w.text()))
        if text:
            return text
    if w.isWindow():
        return _words(w.windowTitle().strip())
    return _words(_buddy_words(w))


def _inside_qt_own(w: QWidget) -> bool:
    """A part of a Qt control rather than a control of the app's: a line
    edit's clear button, a spin box's editor, a combo box's popup."""
    p = w.parentWidget()
    while p is not None:
        if isinstance(p, (QComboBox, QAbstractSpinBox, QLineEdit)):
            return True
        p = p.parentWidget()
    return False


def reachable(w: QWidget) -> bool:
    """A control a person can get to: one of the kinds above, or any widget
    Tab stops at. A scroll area that only holds controls is no stop of its
    own (``unfocused``)."""
    if isinstance(w, _QT_OWN) or w.objectName().startswith("qt_") or _inside_qt_own(w):
        return False
    if isinstance(w, CONTROLS):
        return True
    if w.focusProxy() is not None:
        return False        # its focus is another's (a view's viewport)
    return bool(w.focusPolicy() & Qt.FocusPolicy.TabFocus) and not isinstance(w, QLabel)


_LATER = "a11yWordsLater"


def words_later(w: QWidget) -> QWidget:
    """*w* gets its words as it is shown (an update's version, a decision's
    action, the device it is about) and is hidden until then: no name is
    asked of it while it is hidden - one shown without words still counts."""
    w.setProperty(_LATER, True)
    return w


def unnamed(root: QWidget, visible_only: bool = False) -> list[QWidget]:
    """Every control under *root* (and *root* itself) a screen reader would
    read without a name. *visible_only*: only those shown now."""
    out = []
    for w in [root, *root.findChildren(QWidget)]:
        if not reachable(w):
            continue
        if visible_only and not (w is root or w.isVisibleTo(root)):
            continue
        if w.property(_LATER) and w.isHidden():
            continue
        if not spoken_name(w):
            out.append(w)
    return out


def describe(w: QWidget) -> str:
    """*w* in a line for a test's message: its class, its tooltip, where it is."""
    parts, p = [], w
    while p is not None and len(parts) < 5:
        parts.append(f"{type(p).__name__}#{p.objectName()}" if p.objectName()
                     else type(p).__name__)
        p = p.parentWidget()
    tip = w.toolTip()[:40]
    return " < ".join(parts) + (f" (tooltip {tip!r})" if tip else "")


_KEY_AT_END = re.compile(r"\s*[(（][^()（）]*[)）]\s*$")


def without_key(text: str) -> str:
    """A tooltip's words without the key it ends in, "(Ctrl+B)": the name
    of the control (the key stays in the tooltip, read as its description)."""
    m = _KEY_AT_END.search(text)
    return (text[:m.start()] if m else text).strip()


def name_fields(holder: QWidget, words: str) -> None:
    """The fields under *holder* that have no name yet named *words* - the
    label a settings row shows beside them (a line edit, a number, a list
    of choices: Qt reads a field's label only from a buddy QLabel, which
    would also take the label's ``&`` as a mnemonic)."""
    if not words:
        return
    targets = [holder, *holder.findChildren(QWidget)]
    for w in targets:
        if isinstance(w, (QLineEdit, QComboBox, QAbstractSpinBox, QAbstractSlider,
                          QTextEdit, QPlainTextEdit, QAbstractItemView)) \
                and not _inside_qt_own(w) and not spoken_name(w):
            w.setAccessibleName(words)


class _CurrentShown(QObject):
    """Tab into a list makes its first row current but not chosen (Qt's
    own focus-in, "NoUpdate"): a list that paints only the chosen row then
    showed nothing where the keyboard was. The current row is chosen as the
    focus comes - what a click on it, or the arrows, would do."""

    def eventFilter(self, obj, event) -> bool:      # noqa: N802 - Qt hook
        if event.type() == QEvent.Type.FocusIn and isinstance(obj, QAbstractItemView) \
                and event.reason() in (Qt.FocusReason.TabFocusReason,
                                       Qt.FocusReason.BacktabFocusReason):
            QTimer.singleShot(0, lambda v=obj: _choose_current(v))
        return False


def _choose_current(view: QAbstractItemView) -> None:
    try:
        model, sel = view.model(), view.selectionModel()
        if model is None or sel is None or not view.hasFocus():
            return
        if view.selectionMode() not in (QAbstractItemView.SelectionMode.SingleSelection,
                                        QAbstractItemView.SelectionMode.ExtendedSelection):
            return
        idx = view.currentIndex()
        if idx.isValid() and not sel.hasSelection() \
                and model.flags(idx) & Qt.ItemFlag.ItemIsSelectable:
            sel.select(idx, QItemSelectionModel.SelectionFlag.ClearAndSelect
                       | QItemSelectionModel.SelectionFlag.Rows)
    except RuntimeError:            # the view gone meanwhile
        return


def show_current(view: QAbstractItemView) -> QAbstractItemView:
    """*view*'s current row chosen when the keyboard's focus comes into it
    with none chosen (``_CurrentShown``; the filter is the view's own child,
    gone with it)."""
    view.installEventFilter(_CurrentShown(view))
    return view


def keyboard_ring(painter, option) -> None:
    """The row the keyboard is on, ringed in the accent while its list has
    the focus - only once the keyboard moved it (Tab, an arrow: Qt's
    State_KeyboardFocusChange, which a click clears), so the look under a
    mouse stays as it was. A list styled ``outline: none`` drew no focus at
    all: a chosen row looked the same with the focus anywhere else."""
    st = option.state
    if not (st & QStyle.StateFlag.State_HasFocus and st & QStyle.StateFlag.State_KeyboardFocusChange):
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(P.get("accent", "#4C8DFF")), 2))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(option.rect).adjusted(1.5, 1.5, -1.5, -1.5), 6, 6)
    painter.restore()


def unfocused(area: QAbstractScrollArea) -> QAbstractScrollArea:
    """A scroll area whose content is all controls: Tab goes to them, not
    to an invisible stop on the area around them (it still scrolls to the
    one that has the focus)."""
    area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return area
