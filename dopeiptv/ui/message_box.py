"""Messages and questions to the person, in the app's own look - never Qt's
stock QMessageBox or QInputDialog, which read like any program's: the
desktop icon theme's lightbulb or question mark, a grey layout, its own
buttons.

One modal window for all of them (``MessageDialog``): the title as a
heading in it (and in its title bar, a normal window of the desktop's),
the words under it, a small mark per kind drawn in the theme's colours
(info, warning, error, question - or none for a box that asks for a
value), a field where a value is asked, the buttons at the end. Every
word in it is plain text - a provider's, a box's or a file's words are
shown as written, never read as markup - and a long run with no space (an
address, a path) gets places to break. Long words scroll in the window
rather than pushing it off the screen. Mirrored right to left with the
app.

The buttons: the one Enter presses at the trailing end in the accent
(``PRIMARY``), a destructive one in the theme's red (``DANGER``: filled
when it is what Enter does, red words beside a safer default), the others
plain; side by side when two fit, else one under another. Escape and the
window's close give the cancel answer.

What stands in for the stock calls - each with the same answers:

* ``information``, ``warning``, ``error``: words and OK.
* ``confirm``: Yes / No (or words of the caller's), True for Yes.
* ``choose``: a few buttons, the index of the one pressed or None.
* ``get_text``, ``get_item``, ``get_int``: (value, ok) as QInputDialog's.

``MessageDialog`` is there for what needs more (a "Don't show this again"
box: ``add_check``); ``run`` asks it and lets it go. Tests drive a dialog
through ``exec`` (``QDialog.exec`` or ``MessageDialog.exec``) and
``press``."""
from __future__ import annotations

import re
from itertools import pairwise
from typing import Sequence

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from ..i18n import current_language, tr
from .theme import P, corner_radius, ink_on, tinted

#: The kinds of message, each its own mark.
INFO, WARNING, ERROR, QUESTION = "info", "warning", "error", "question"
KINDS = (INFO, WARNING, ERROR, QUESTION)
#: What a button is: the one Enter presses, a plain one, a destructive one.
PRIMARY, NORMAL, DANGER = "primary", "normal", "danger"

#: The window's width; it grows in height with its words.
WIDTH = 480
#: The mark's size, and the room around the column of words.
MARK = 40
_MARGINS = (24, 22, 24, 20)
_GAP = 16
#: The most a message's words take before they scroll: this much of the
#: screen's height, and never more than ``TEXT_MOST`` px.
TEXT_SCREEN_SHARE = 0.55
TEXT_MOST = 420

#: Languages whose question mark faces the other way (؟).
_MIRRORED_QUESTION = frozenset({"ar", "fa", "ur"})

# A run with no space long enough to need places to break, where it may
# break (after an address's or a path's punctuation), and a break every so
# many characters in what is left long.
_LONG_RUN = re.compile(r"\S{25,}")
_BREAK_AFTER = re.compile(r"([/\\?&=._:@-])(?=\S)")
_STILL_LONG = re.compile(r"[^\u200b]{20}(?=[^\u200b])")


def breakable(text: str) -> str:
    """*text* with zero-width places to break in its long runs - an
    address, a path: a run Qt cannot break is as wide as itself and would
    push the window wider than the screen, or be cut."""
    def cut(m: re.Match[str]) -> str:
        return _STILL_LONG.sub(lambda n: n.group(0) + "\u200b",
                               _BREAK_AFTER.sub("\\1\u200b", m.group(0)))
    return _LONG_RUN.sub(cut, str(text or ""))


def colours() -> dict[str, str]:
    """The window's colours on the theme in use, each made to read: the
    heading and the words at 4.5:1 or more on the window, a mark's glyph at
    3:1 on its wash, the focus ring at 3:1 on the window, a destructive
    button's words at 4.5:1 on its fill."""
    bg, text = P["bg"], P["text"]

    def ink(colour: str, on: str, need: float = 4.5) -> str:
        return tinted(colour, on, 0.0, text, need)[1]

    out = {
        "bg": bg,
        "heading": ink(text, bg, 7.0),
        "body": ink(P["text2"], bg),
        "muted": ink(P["muted"], bg),
        "ring": ink(P["accent"], bg, 3.0),
        "danger": P["error"],
        "danger_ink": ink_on(P["error"]),
        "danger_hover": tinted(text, P["error"], 0.14, text)[0],
        "danger_text": ink(P["error"], P["btn"]),
    }
    for kind, colour in ((INFO, P["accent"]), (QUESTION, P["accent"]),
                         (WARNING, P["warn"]), (ERROR, P["error"])):
        wash, glyph = tinted(colour, bg, 0.18, text, 3.0)
        out[f"{kind}_wash"], out[f"{kind}_glyph"] = wash, glyph
    return out


def paint_mark(p: QPainter, kind: str, r: QRectF, mirrored: bool = False) -> None:
    """A message's mark in *r*: a soft disc in its kind's colour with an
    "i", a "!", a cross or a question mark drawn on it - lines, not a
    font's glyph or the desktop's icons. *mirrored*: the question mark as
    Arabic, Persian and Urdu write it."""
    c = colours()
    kind = kind if kind in KINDS else INFO
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(c[f"{kind}_wash"]))
    p.drawEllipse(r)
    s = r.width()
    x, y, cx = r.left(), r.top(), r.center().x()
    pen = QPen(QColor(c[f"{kind}_glyph"]), max(2.0, s * 0.095))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    if kind == INFO:
        p.drawPoint(QPointF(cx, y + 0.29 * s))
        p.drawLine(QPointF(cx, y + 0.44 * s), QPointF(cx, y + 0.72 * s))
    elif kind == WARNING:
        p.drawLine(QPointF(cx, y + 0.26 * s), QPointF(cx, y + 0.55 * s))
        p.drawPoint(QPointF(cx, y + 0.72 * s))
    elif kind == ERROR:
        a, b = 0.35 * s, 0.65 * s
        p.drawLine(QPointF(x + a, y + a), QPointF(x + b, y + b))
        p.drawLine(QPointF(x + b, y + a), QPointF(x + a, y + b))
    else:
        rad = 0.13 * s
        top = QRectF(cx - rad, y + 0.25 * s, 2 * rad, 2 * rad)
        hook = QPainterPath()
        hook.arcMoveTo(top, 165)
        hook.arcTo(top, 165, -245)
        hook.quadTo(QPointF(cx, y + 0.50 * s), QPointF(cx, y + 0.58 * s))
        if mirrored:
            p.translate(2 * cx, 0)
            p.scale(-1, 1)
        p.drawPath(hook)
        p.drawPoint(QPointF(cx, y + 0.73 * s))
    p.restore()


class _Mark(QWidget):
    """The kind's mark at the head of the window (drawn by ``paint_mark``)."""

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(MARK, MARK)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, _event) -> None:      # noqa: N802 - Qt hook
        p = QPainter(self)
        paint_mark(p, self.kind, QRectF(0.5, 0.5, MARK - 1.0, MARK - 1.0),
                   mirrored=current_language() in _MIRRORED_QUESTION)
        p.end()


class _Button(QPushButton):
    """A button of the window: the app's own look, and a ring around it in
    the accent while it has the keyboard's focus (the one Enter presses as
    the window opens)."""

    def __init__(self, text: str, role: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.role = role
        self.setAutoDefault(True)

    def focusInEvent(self, event) -> None:      # noqa: N802 - Qt hook
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event) -> None:     # noqa: N802 - Qt hook
        super().focusOutEvent(event)
        self.update()

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt hook
        super().paintEvent(event)
        if not self.hasFocus():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(colours()["ring"]), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        radius = corner_radius(9) + 2
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), radius, radius)
        p.end()


def _style(has_primary: bool) -> str:
    c = colours()
    danger = (f"QPushButton#MessageDanger {{ color: {c['danger_text']}; }}"
              if has_primary else
              f"QPushButton#MessageDanger {{ background: {c['danger']}; border: none;"
              f" color: {c['danger_ink']}; }}"
              f"QPushButton#MessageDanger:hover {{ background: {c['danger_hover']}; }}")
    return (
        f"QDialog#MessageBox {{ background: {c['bg']}; }}"
        f"QLabel#MessageTitle {{ background: transparent; color: {c['heading']};"
        " font-size: 16px; font-weight: 700; }"
        f"QLabel#MessageText {{ background: transparent; color: {c['body']}; font-size: 13px; }}"
        f"QCheckBox#MessageCheck {{ color: {c['muted']}; font-size: 12px; }}"
        "QDialog#MessageBox QPushButton { margin: 3px; min-width: 56px; }"
        + danger)


class MessageDialog(QDialog):
    """The one window for a message or a question (see the module's words).

    *options*: (words, role) per button - role ``PRIMARY``, ``NORMAL`` or
    ``DANGER`` - in the order shown; OK alone when none are given.
    *default*: the index Enter presses as it opens (the focus is on it);
    *escape*: the index Escape and the window's close give, None for no
    button. *kind*: its mark, or None for none. *stacked*: the buttons one
    under another (None: when more than two, or when two do not fit side
    by side)."""

    def __init__(self, parent: QWidget | None, title: str, text: str = "", *,
                 kind: str | None = INFO,
                 options: Sequence[tuple[str, str]] | None = None,
                 default: int = 0, escape: int | None = None,
                 stacked: bool | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MessageBox")
        self.setModal(True)
        self._title, self._text = str(title or ""), str(text or "")
        opts = list(options or [(tr("common_ok"), PRIMARY)])
        self._default = max(0, min(int(default), len(opts) - 1))
        self._escape = escape if escape is None or 0 <= escape < len(opts) else None
        self._chosen: int | None = None
        self.setWindowTitle(self._title)
        self.setAccessibleName(self._title)
        self.setAccessibleDescription(self._text)
        self.setStyleSheet(_style(any(role == PRIMARY for _w, role in opts)))

        row = QHBoxLayout(self)
        # The window's size is its own (_fit), not the layout's minimum.
        row.setSizeConstraint(QHBoxLayout.SizeConstraint.SetNoConstraint)
        row.setContentsMargins(*_MARGINS)
        row.setSpacing(_GAP)
        self._mark: _Mark | None = None
        if kind is not None:
            self._mark = _Mark(kind, self)
            row.addWidget(self._mark, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(6)
        row.addLayout(col, 1)
        self._col = col

        self.heading = self._label(self._title, "MessageTitle")
        col.addWidget(self.heading)
        self.body = self._label(self._text, "MessageText")
        self._scroll: QScrollArea | None = None
        self._body_at = col.count()
        col.addWidget(self.body)
        self.body.setVisible(bool(self._text))
        # A field or a check box asked for goes here, above the buttons.
        self._extra = QVBoxLayout()
        self._extra.setSpacing(8)
        col.addSpacing(4)
        col.addLayout(self._extra)
        col.addSpacing(10)

        self._stops: list[QWidget] = []
        self._buttons: list[_Button] = []
        for i, (words, role) in enumerate(opts):
            b = _Button(str(words), role, self)
            if role == PRIMARY:
                b.setObjectName("Primary")
            elif role == DANGER:
                b.setObjectName("MessageDanger")
            b.clicked.connect(lambda _c=False, i=i: self.press(i))
            self._buttons.append(b)
        if stacked is None:
            stacked = len(opts) > 2 or self._row_width() > self.text_width()
        self._stacked = bool(stacked)
        if self._stacked:
            box = QVBoxLayout()
            box.setSpacing(2)
            for b in self._buttons:
                b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                box.addWidget(b)
        else:
            box = QHBoxLayout()
            box.setSpacing(4)
            box.addStretch(1)
            for b in self._buttons:
                box.addWidget(b)
        col.addLayout(box)

        first = self._buttons[self._default]
        first.setDefault(True)
        first.setFocus()
        # Read out with the button the focus is on as the window opens.
        first.setAccessibleDescription(self._text)
        self._fit()

    # -- what it shows ----------------------------------------------------------------

    def _label(self, words: str, name: str) -> QLabel:
        lbl = QLabel(breakable(words), self)
        lbl.setObjectName(name)
        # Words from outside (a provider's, a box's, a file's) are words.
        lbl.setTextFormat(Qt.TextFormat.PlainText)
        lbl.setWordWrap(True)
        lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        # Read out as written, without the places to break.
        lbl.setAccessibleName(words)
        return lbl

    def text_width(self) -> int:
        """The width the words and the buttons have."""
        left, _t, right, _b = _MARGINS
        return WIDTH - left - right - ((MARK + _GAP) if self._mark is not None else 0)

    def _row_width(self) -> int:
        for b in self._buttons:
            b.ensurePolished()
        return sum(b.sizeHint().width() for b in self._buttons) + 4 * (len(self._buttons) - 1)

    def _most_text(self) -> int:
        screen = self.screen() or QGuiApplication.primaryScreen()
        high = screen.availableGeometry().height() if screen is not None else 768
        return max(120, min(TEXT_MOST, int(high * TEXT_SCREEN_SHARE)))

    def _fit(self) -> None:
        """The window as wide as ``WIDTH`` and as tall as its words - the
        words in a scroll area of their own once they would be taller than
        ``_most_text``."""
        for lbl in (self.heading, self.body):
            lbl.ensurePolished()
        if self._scroll is None and self._text \
                and self.body.heightForWidth(self.text_width()) > self._most_text():
            area = QScrollArea(self)
            area.setObjectName("MessageScroll")
            area.setFrameShape(QFrame.Shape.NoFrame)
            area.setWidgetResizable(True)
            area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            area.setFixedHeight(self._most_text())
            # The keyboard scrolls it: a stop of its own, named as the window.
            area.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            area.setAccessibleName(self._title)
            area.setAccessibleDescription(self._text)
            self._col.removeWidget(self.body)
            area.setWidget(self.body)
            self._col.insertWidget(self._body_at, area)
            self._scroll = area
            self._order_tabs()
        if (self.minimumWidth(), self.maximumWidth()) != (WIDTH, WIDTH):
            self.setFixedWidth(WIDTH)
        lay = self.layout()
        if lay is not None:
            # As tall as its words need at this width: a wrapped label's
            # minimum is worked out for a narrow one, far taller.
            high = lay.totalHeightForWidth(WIDTH)
            high = high if high > 0 else lay.totalSizeHint().height()
            if (self.minimumHeight(), self.maximumHeight()) != (high, high):
                self.setFixedHeight(high)

    def showEvent(self, event) -> None:         # noqa: N802 - Qt hook
        self._fit()
        super().showEvent(event)

    def add_field(self, field: QWidget, name: str = "") -> QWidget:
        """*field* above the buttons, named *name* (the label's words) for a
        screen reader; the focus on it as the window opens."""
        field.setParent(self)
        if name and not field.accessibleName():
            field.setAccessibleName(name)
        field.setAccessibleDescription(self._text)
        self._extra.addWidget(field)
        self._stops.append(field)
        self._order_tabs()
        field.setFocus()
        self._fit()
        return field

    def add_check(self, words: str) -> QCheckBox:
        """A check box above the buttons ("Don't show this again")."""
        box = QCheckBox(words, self)
        box.setObjectName("MessageCheck")
        self._extra.addWidget(box)
        self._stops.append(box)
        self._order_tabs()
        self._fit()
        return box

    def _order_tabs(self) -> None:
        """Tab goes as the window reads: the words that scroll, a field or
        a check box, then the buttons in their order."""
        chain = ([self._scroll] if self._scroll is not None else []) + self._stops \
            + list(self._buttons)
        for a, b in pairwise(chain):
            QWidget.setTabOrder(a, b)

    # -- what it answers --------------------------------------------------------------

    def title(self) -> str:
        return self._title

    def text(self) -> str:
        return self._text

    def buttons(self) -> list[QPushButton]:
        return list(self._buttons)

    def stacked(self) -> bool:
        return self._stacked

    def default_index(self) -> int:
        return self._default

    def escape_index(self) -> int | None:
        return self._escape

    def chosen(self) -> int | None:
        return self._chosen

    def press(self, index: int) -> int:
        """Button *index* pressed: the window answers it and closes."""
        self._chosen = index
        self.done(QDialog.DialogCode.Accepted.value)
        return QDialog.DialogCode.Accepted.value

    def reject(self) -> None:
        # Escape and the window's close: the cancel answer.
        if self._chosen is None:
            self._chosen = self._escape
        super().reject()

    def answer(self, code: int) -> int | None:
        """The index the person chose, from what exec() returned: the button
        pressed, else the default for an accept (a window driven by a test)
        and the escape answer for a reject."""
        if self._chosen is not None:
            return self._chosen
        accepted = int(code) == QDialog.DialogCode.Accepted.value
        return self._default if accepted else self._escape


def run(dialog: MessageDialog) -> int | None:
    """Ask *dialog* (modal, as exec() does) and let it go once answered;
    the index chosen, or None. What it holds - a check box - can still be
    read until the event loop runs again."""
    try:
        return dialog.answer(dialog.exec())
    finally:
        # Asked and answered: gone, not a child of its parent for the rest
        # of the session (docs/PERFORMANCE.md).
        dialog.deleteLater()


# -- messages ---------------------------------------------------------------------------

def _say(parent: QWidget | None, title: str, text: str, kind: str) -> None:
    run(MessageDialog(parent, title, text, kind=kind,
                      options=[(tr("common_ok"), PRIMARY)], default=0, escape=0))


def information(parent: QWidget | None, title: str, text: str) -> None:
    """Something to know, and OK."""
    _say(parent, title, text, INFO)


def warning(parent: QWidget | None, title: str, text: str) -> None:
    """Something that did not go as asked, and OK."""
    _say(parent, title, text, WARNING)


def error(parent: QWidget | None, title: str, text: str) -> None:
    """Something that failed, and OK."""
    _say(parent, title, text, ERROR)


def confirm(parent: QWidget | None, title: str, text: str, *, default_yes: bool = True,
            danger: bool = False, yes: str = "", no: str = "") -> bool:
    """Yes or No (or the caller's own words for each): True for Yes.
    *default_yes*: what Enter answers. *danger*: Yes does what cannot be
    undone (a delete, Reset all) - red, and a warning's mark. Escape and
    the window's close answer No. The answer Enter gives is at the end."""
    yes, no = yes or tr("option_yes"), no or tr("option_no")
    if default_yes:
        options = [(no, NORMAL), (yes, DANGER if danger else PRIMARY)]
        yes_at, no_at = 1, 0
    else:
        options = [(yes, DANGER if danger else NORMAL), (no, PRIMARY)]
        yes_at, no_at = 0, 1
    got = run(MessageDialog(parent, title, text, kind=WARNING if danger else QUESTION,
                            options=options, default=1, escape=no_at))
    return got == yes_at


def choose(parent: QWidget | None, title: str, text: str,
           options: Sequence[tuple[str, str]], *, default: int = 0,
           escape: int | None = None, kind: str | None = QUESTION,
           stacked: bool | None = None) -> int | None:
    """A few buttons - (words, role) each, in the order shown: the index of
    the one pressed, *escape* for Escape and the window's close."""
    return run(MessageDialog(parent, title, text, kind=kind, options=options,
                             default=default, escape=escape, stacked=stacked))


# -- values asked for -------------------------------------------------------------------

def _value_dialog(parent: QWidget | None, title: str, label: str) -> MessageDialog:
    return MessageDialog(parent, title, label, kind=None,
                         options=[(tr("common_cancel"), NORMAL), (tr("common_ok"), PRIMARY)],
                         default=1, escape=0, stacked=False)


def _asked(dialog: MessageDialog) -> bool:
    """*dialog* asked: True for OK, False for Cancel, Escape and the
    window's close - as QInputDialog's."""
    return dialog.answer(dialog.exec()) == 1


def get_text(parent: QWidget | None, title: str, label: str, text: str = "", *,
             password: bool = False, placeholder: str = "", max_length: int = 0,
             name: str = "") -> tuple[str, bool]:
    """A line of text asked for: (what was typed, True) on OK, ("", False)
    otherwise. *password*: hidden as it is typed. The field is emptied
    before the window goes, so a PIN is not kept in it."""
    d = _value_dialog(parent, title, label)
    field = QLineEdit(str(text or ""))
    if password:
        field.setEchoMode(QLineEdit.EchoMode.Password)
    if placeholder:
        field.setPlaceholderText(placeholder)
    if max_length > 0:
        field.setMaxLength(int(max_length))
    d.add_field(field, name or label or title)
    field.selectAll()
    try:
        ok = _asked(d)
        got = field.text() if ok else ""
        return got, ok
    finally:
        field.clear()
        d.deleteLater()


def get_item(parent: QWidget | None, title: str, label: str, items: Sequence[str],
             current: int = 0, editable: bool = False) -> tuple[str, bool]:
    """One of *items* picked (or, *editable*, typed): (it, True) on OK,
    (the item at *current*, False) otherwise - as QInputDialog's."""
    items = [str(i) for i in items]
    start = items[current] if 0 <= current < len(items) else ""
    d = _value_dialog(parent, title, label)
    combo = QComboBox()
    combo.setEditable(bool(editable))
    combo.addItems(items)
    if 0 <= current < len(items):
        combo.setCurrentIndex(current)
    combo.setMaxVisibleItems(12)
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(12)
    combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    d.add_field(combo, label or title)
    try:
        ok = _asked(d)
        return (combo.currentText() if ok else start), ok
    finally:
        d.deleteLater()


def get_int(parent: QWidget | None, title: str, label: str, value: int = 0,
            minimum: int = -2147483647, maximum: int = 2147483647,
            step: int = 1) -> tuple[int, bool]:
    """A whole number from *minimum* to *maximum*: (it, True) on OK,
    (*value*, False) otherwise - as QInputDialog's."""
    d = _value_dialog(parent, title, label)
    spin = QSpinBox()
    spin.setRange(int(minimum), int(maximum))
    spin.setSingleStep(max(1, int(step)))
    spin.setValue(int(value))
    spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    d.add_field(spin, label or title)
    spin.selectAll()
    try:
        ok = _asked(d)
        return (spin.value() if ok else int(value)), ok
    finally:
        d.deleteLater()
