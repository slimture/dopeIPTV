"""The command palette: one search field over the window for everything.

Ctrl+K (⌘K) anywhere in the window puts it up, centred near the top over a
dimmed window: what is typed finds the app's actions, its settings and the
active source's channels and programmes, in three groups, the best first.
Up and Down choose, Enter runs, Tab says more about the row, Esc (or a
click beside it) closes it.

This is only the overlay - the field, the list and how a row is drawn. What
is found and what a row does are the window's (``mw_palette.py``): *search*
gives the rows for a query, *run* carries one out.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QLocale, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QStyle, QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout, QWidget,
)

from ..i18n import tr
from .shortcuts import native
from .theme import P, _mix, rounded

#: The groups, in the order they are shown.
GROUPS = ("actions", "settings", "channels")
_GROUP_TITLES = {"actions": "pal_actions", "settings": "pal_settings",
                 "channels": "pal_channels"}

#: How long typing has to pause before the rows are looked up again.
DEBOUNCE_MS = 80

_ENTRY = Qt.ItemDataRole.UserRole
_HEAD = Qt.ItemDataRole.UserRole + 1


@dataclass
class Entry:
    """One row: its *group* (one of GROUPS), the *kind* on its chip, its
    *text*, *where* it is (right, muted) or its *keys* (right, as keys), a
    Nerd row in Nerd's colour, *more* for Tab, and what it is (*payload*,
    for the window's *run*)."""
    group: str
    kind: str
    text: str
    where: str = ""
    keys: str = ""
    nerd: bool = False
    more: str = ""
    payload: Any = None


def key_parts(seq: str) -> list[str]:
    """*seq* (portable text) as the keys a person presses, as this system
    writes them: ["Ctrl", "Shift", "N"], or ["⇧", "⌘", "N"] on a Mac."""
    text = native(seq)
    if not text:
        return []
    if sys.platform == "darwin":
        mods = [ch for ch in text if ch in "⌃⌥⇧⌘"]
        rest = "".join(ch for ch in text if ch not in "⌃⌥⇧⌘")
        return mods + ([rest] if rest else [])
    if text.endswith("++"):
        return text[:-2].split("+") + ["+"]
    return [p for p in text.split("+") if p] or [text]


def _search_icon(size: int, colour: str, ratio: float) -> QPixmap:
    pm = QPixmap(round(size * ratio), round(size * ratio))
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(colour), size / 9)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    s = size / 24
    p.drawEllipse(QRectF(4 * s, 4 * s, 14 * s, 14 * s))
    p.drawLine(QPointF(16 * s, 16 * s), QPointF(21 * s, 21 * s))
    p.end()
    return pm


class _Delegate(QStyledItemDelegate):
    """A group's heading in small capitals, or a row: its kind on a chip,
    its text, and where it is or its keys on the right; the chosen row on a
    surface with the accent down its leading edge."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._row_h = 40
        self._head_h = 30

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        fm = QFontMetrics(option.font)
        if index.data(_HEAD):
            return QSize(0, max(self._head_h, fm.height() + 14))
        return QSize(0, max(self._row_h, fm.height() + 20))

    @staticmethod
    def _chip(p: QPainter, x: int, rect: QRect, text: str, font: QFont,
              fg: str, bg: QColor, rtl: bool) -> int:
        """A chip with *text*, its leading edge at *x*; returns its width."""
        fm = QFontMetrics(font)
        w = fm.horizontalAdvance(text) + 14
        h = fm.height() + 6
        left = x - w if rtl else x
        r = QRectF(left, rect.center().y() - h / 2 + 1, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, 6, 6)
        p.setPen(QColor(fg))
        p.setFont(font)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
        return w

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index) -> None:
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rtl = option.direction == Qt.LayoutDirection.RightToLeft
        rect = option.rect
        base = QFont(option.font)
        small = QFont(base)
        small.setPointSizeF(max(7.0, base.pointSizeF() * 0.82))
        small.setWeight(QFont.Weight.DemiBold)
        head = index.data(_HEAD)
        if head:
            small.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 106)
            p.setFont(small)
            p.setPen(QColor(P["muted"]))
            r = rect.adjusted(14, 0, -14, -4)
            align = Qt.AlignmentFlag.AlignBottom | (Qt.AlignmentFlag.AlignRight if rtl
                                                    else Qt.AlignmentFlag.AlignLeft)
            p.drawText(r, align, QLocale().toUpper(head))
            p.restore()
            return
        e: Entry = index.data(_ENTRY)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(P["sel"] if selected else P["hover"]))
            p.drawRoundedRect(QRectF(rect.adjusted(0, 1, 0, -1)), 8, 8)
            if selected:
                p.setBrush(QColor(P["accent"]))
                bar = QRectF(rect.right() - 2, rect.top() + 6, 3, rect.height() - 12) if rtl \
                    else QRectF(rect.left(), rect.top() + 6, 3, rect.height() - 12)
                p.drawRoundedRect(bar, 1.5, 1.5)
        pad = 14
        x = rect.right() - pad if rtl else rect.left() + pad
        if e.kind:
            if e.nerd:
                fg, bg = P.get("nerd", P["accent"]), QColor(P.get("nerd", P["accent"]))
                bg.setAlpha(0x22)
            else:
                fg, bg = P["text3"], QColor(P["btn"])
            w = self._chip(p, x, rect, e.kind, small, fg, bg, rtl)
            x = x - w - 12 if rtl else x + w + 12
        # The right-hand side first, so the text knows how far it may go.
        end = rect.left() + pad if rtl else rect.right() - pad
        right_w = 0
        if e.keys:
            parts = key_parts(e.keys)
            fm = QFontMetrics(small)
            widths = [fm.horizontalAdvance(t) + 14 for t in parts]
            right_w = sum(widths) + 4 * (len(parts) - 1)
            kx = end if rtl else end - right_w
            for t, w in zip(parts, widths, strict=True):
                self._chip(p, kx + (w if rtl else 0), rect, t, small, P["text2"],
                           QColor(P["btn"]), rtl)
                kx += w + 4
        elif e.where:
            wf = QFont(base)
            wf.setPointSizeF(max(7.0, base.pointSizeF() * 0.9))
            fm = QFontMetrics(wf)
            room = max(60, int(rect.width() * 0.42))
            where = fm.elidedText(e.where, Qt.TextElideMode.ElideRight, room)
            right_w = fm.horizontalAdvance(where)
            p.setFont(wf)
            p.setPen(QColor(P["muted"]))
            r = QRect(end, rect.top(), right_w, rect.height()) if rtl \
                else QRect(end - right_w, rect.top(), right_w, rect.height())
            p.drawText(r, Qt.AlignmentFlag.AlignVCenter, where)
        tf = QFont(base)
        tf.setWeight(QFont.Weight.Medium)
        fm = QFontMetrics(tf)
        room = (x - end - right_w - 16) if rtl else (end - right_w - 16 - x)
        text = fm.elidedText(e.text, Qt.TextElideMode.ElideRight, max(20, room))
        p.setFont(tf)
        p.setPen(QColor(P["text"]))
        tw = fm.horizontalAdvance(text)
        r = QRect(x - tw, rect.top(), tw, rect.height()) if rtl \
            else QRect(x, rect.top(), tw, rect.height())
        p.drawText(r, Qt.AlignmentFlag.AlignVCenter, text)
        p.restore()


class CommandPalette(QWidget):
    """The overlay: covers *parent* (dimmed), the palette near its top."""

    closed = pyqtSignal()

    def __init__(self, parent: QWidget, search: Callable[[str], list[Entry]],
                 run: Callable[[Entry, QPoint], None], open_key: str = "") -> None:
        super().__init__(parent)
        self.setObjectName("CommandPalette")
        self._search = search
        self._run = run
        self._open_key = open_key
        self.entries: list[Entry] = []
        #: The query whose rows are still being looked for off the window's
        #: thread (expect / deliver), and whether Enter waits for them.
        self._pending = ""
        self._run_when_in = False
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setStyleSheet(self._style())

        self.panel = QFrame(self)
        self.panel.setObjectName("PalettePanel")
        self.panel.setAccessibleName(tr("sc_palette"))
        v = QVBoxLayout(self.panel)
        v.setContentsMargins(10, 10, 10, 8)
        v.setSpacing(0)

        top = QFrame()
        top.setObjectName("PaletteTop")
        th = QHBoxLayout(top)
        th.setContentsMargins(12, 6, 8, 10)
        th.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(_search_icon(18, P["muted"], self.devicePixelRatioF() or 1.0))
        th.addWidget(icon)
        self.field = QLineEdit()
        self.field.setObjectName("PaletteField")
        self.field.setPlaceholderText(tr("pal_placeholder"))
        self.field.setAccessibleName(tr("pal_placeholder"))
        th.addWidget(self.field, 1)
        th.addWidget(self._kbd(native("Esc") or "Esc"))
        v.addWidget(top)

        self.list = QListWidget()
        self.list.setObjectName("PaletteList")
        self.list.setItemDelegate(_Delegate(self.list))
        self.list.setMouseTracking(True)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.itemClicked.connect(self._clicked)
        v.addWidget(self.list)

        self.none = QLabel()
        self.none.setObjectName("PaletteNone")
        self.none.setWordWrap(True)
        self.none.hide()
        v.addWidget(self.none)

        self.more = QLabel()
        self.more.setObjectName("PaletteMore")
        self.more.setWordWrap(True)
        self.more.hide()
        v.addWidget(self.more)

        self.foot = QFrame()
        self.foot.setObjectName("PaletteFoot")
        fh = QHBoxLayout(self.foot)
        fh.setContentsMargins(12, 10, 12, 4)
        fh.setSpacing(16)
        hints = [self._hint(keys, tr(word)) for keys, word in (
            (("↑", "↓"), "pal_hint_choose"), ((native("Enter") or "Enter",), "pal_hint_run"),
            ((native("Tab") or "Tab",), "pal_hint_more"))]
        for h in hints:
            fh.addWidget(h)
        fh.addStretch(1)
        #: Left out, in this order, while the foot is wider than the palette.
        self._spare: list[QWidget] = []
        if open_key:
            parts = tr("pal_hint_opens", shortcut="\0").split("\0")
            opens = self._hint_around(parts[0], key_parts(open_key),
                                      parts[1] if len(parts) > 1 else "")
            fh.addWidget(opens)
            self._spare.append(opens)
        self._spare.append(hints[2])
        v.addWidget(self.foot)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self.refresh)
        self.field.textChanged.connect(self._typed)
        self.field.installEventFilter(self)
        parent.installEventFilter(self)
        self.hide()

    # -- look ------------------------------------------------------------------

    @staticmethod
    def _style() -> str:
        p = P
        line = p["border"]
        return rounded(f"""
QFrame#PalettePanel {{ background: {p['pane']}; border-radius: 14px; }}
QFrame#PaletteTop {{ background: transparent; border: none;
    border-bottom: 1px solid {line}; }}
QFrame#PaletteFoot {{ background: transparent; border: none;
    border-top: 1px solid {line}; }}
QLineEdit#PaletteField {{ background: transparent; border: none; padding: 2px 0;
    color: {p['text']}; font-size: 16px; font-weight: 500; }}
QListWidget#PaletteList {{ background: transparent; border: none; outline: none; }}
QListWidget#PaletteList::item {{ background: transparent; border: none; }}
QListWidget#PaletteList QScrollBar:vertical {{ width: 6px; background: transparent; margin: 0; }}
QListWidget#PaletteList QScrollBar::handle:vertical {{
    background: {p['scroll']}; border-radius: 3px; min-height: 30px; }}
QListWidget#PaletteList QScrollBar::add-line, QListWidget#PaletteList QScrollBar::sub-line {{
    height: 0; }}
QLabel#PaletteKbd {{ background: {p['btn']}; color: {p['text2']}; border-radius: 5px;
    padding: 1px 6px; font-size: 11px; font-weight: 600; }}
QLabel#PaletteHint, QLabel#PaletteNone {{ color: {p['muted']}; font-size: 12px; }}
QLabel#PaletteNone {{ padding: 14px; }}
QLabel#PaletteMore {{ color: {p['text2']}; font-size: 12px; padding: 8px 14px;
    background: {_mix(p['text'], p['pane'], 0.04)}; border-radius: 8px; }}
""")

    @staticmethod
    def _kbd(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("PaletteKbd")
        return lbl

    @staticmethod
    def _row() -> tuple[QWidget, QHBoxLayout]:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        return w, h

    def _hint(self, keys: tuple[str, ...], word: str) -> QWidget:
        w, h = self._row()
        for k in keys:
            h.addWidget(self._kbd(k))
        lbl = QLabel(word)
        lbl.setObjectName("PaletteHint")
        h.addSpacing(2)
        h.addWidget(lbl)
        return w

    def _hint_around(self, before: str, keys: list[str], after: str) -> QWidget:
        w, h = self._row()
        if before.strip():
            lbl = QLabel(before.strip())
            lbl.setObjectName("PaletteHint")
            h.addWidget(lbl)
        for k in keys:
            kb = QLabel(k)
            kb.setObjectName("PaletteKbd")
            h.addWidget(kb)
        if after.strip():
            lbl = QLabel(after.strip())
            lbl.setObjectName("PaletteHint")
            h.addWidget(lbl)
        return w

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        dim = QColor(Qt.GlobalColor.black)
        dim.setAlpha(150)
        p.fillRect(self.rect(), dim)
        p.end()

    # -- placement -------------------------------------------------------------

    def _place(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.setGeometry(parent.rect())
        w = min(640, max(320, parent.width() - 32))
        top = max(16, min(110, int(parent.height() * 0.14)))
        self.panel.setFixedWidth(w)
        self._fit_foot(w - 20)
        self._fit_list(parent.height() - top - 24)
        self.panel.adjustSize()
        self.panel.move((parent.width() - w) // 2, top)

    def _fit_foot(self, room: int) -> None:
        """Every hint that fits: a cut-off one read as nonsense."""
        for w in self._spare:
            w.show()
        for w in self._spare:
            if self.foot.sizeHint().width() <= room:
                break
            w.hide()

    def _fit_list(self, room: int) -> None:
        rows = self.list.count()
        if not rows:
            self.list.hide()
            return
        self.list.show()
        h = sum(self.list.sizeHintForRow(i) for i in range(rows)) + 4
        others = 120 + (self.more.sizeHint().height() if self.more.isVisible() else 0)
        self.list.setFixedHeight(max(40, min(h, room - others)))

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        t = event.type()
        if obj is self.parentWidget():
            if t == QEvent.Type.Resize and self.isVisible():
                self._place()
            return False
        if obj is self.field:
            if t == QEvent.Type.ShortcutOverride and event.key() in self._KEYS:
                # Ours while the palette is up: the window's Esc and the
                # app's single keys never see them.
                event.accept()
                return True
            if t == QEvent.Type.KeyPress and self._key(event):
                return True
        return False

    _KEYS = (Qt.Key.Key_Escape, Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Return,
             Qt.Key.Key_Enter, Qt.Key.Key_Tab, Qt.Key.Key_Backtab, Qt.Key.Key_PageUp,
             Qt.Key.Key_PageDown)

    def _key(self, event) -> bool:
        k = event.key()
        if k == Qt.Key.Key_Escape:
            self.close_palette()
        elif k in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.move_selection(-1 if k == Qt.Key.Key_Up else 1)
        elif k in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            self.move_selection(-5 if k == Qt.Key.Key_PageUp else 5)
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._debounce.isActive():
                self.refresh()
            if self._pending and self._pending == self.field.text():
                self._run_when_in = True       # run once what was typed is found
            else:
                self.activate()
        elif k in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.toggle_more()
        else:
            return False
        return True

    # -- open, close -------------------------------------------------------------

    def open(self, text: str = "") -> None:
        self.field.blockSignals(True)
        self.field.setText(text)
        self.field.blockSignals(False)
        self.field.selectAll()
        self.refresh()
        self.show()
        self.raise_()
        self._place()
        self.field.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def close_palette(self) -> None:
        if sip.isdeleted(self) or not self.isVisible():
            return
        self._debounce.stop()
        self._pending, self._run_when_in = "", False
        self.hide()
        self.closed.emit()

    def mousePressEvent(self, event) -> None:
        if not self.panel.geometry().contains(event.position().toPoint()):
            self.close_palette()
            return
        super().mousePressEvent(event)

    # -- the rows ------------------------------------------------------------------

    def _typed(self, _text: str) -> None:
        self._run_when_in = False
        self._debounce.start()

    def query(self) -> str:
        return self.field.text()

    def refresh(self) -> None:
        """Look the rows up again for what is in the field now."""
        self._debounce.stop()
        if sip.isdeleted(self):
            return
        q = self.field.text()
        self._pending = ""
        entries = self._search(q)
        self.set_entries(entries, q)

    def expect(self, query: str) -> None:
        """More rows for *query* are being looked for off the window's
        thread (deliver): "nothing found" is not said meanwhile, and Enter
        waits for them."""
        self._pending = query

    def deliver(self, query: str, entries: list[Entry]) -> bool:
        """The rows for *query*, found off the window's thread: shown only
        while it is still what the field says (the row chosen meanwhile kept
        chosen), and run if Enter waited for them. False when too late."""
        if sip.isdeleted(self) or not self._pending or query != self._pending:
            return False
        self._pending = ""
        if query != self.field.text() or self._debounce.isActive():
            self._run_when_in = False
            return False
        chosen = self.current()
        self.set_entries(entries, query)
        if chosen is not None:
            for row in range(self.list.count()):
                if self._entry_at(row) is chosen:
                    self.list.setCurrentRow(row)
                    break
        if self._run_when_in:
            self._run_when_in = False
            self.activate()
        return True

    def set_entries(self, entries: list[Entry], query: str = "") -> None:
        self.entries = list(entries)
        self.list.clear()
        for group in GROUPS:
            rows = [e for e in self.entries if e.group == group]
            if not rows:
                continue
            head = QListWidgetItem()
            head.setData(_HEAD, tr(_GROUP_TITLES[group]))
            head.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(head)
            for e in rows:
                it = QListWidgetItem()
                it.setData(_ENTRY, e)
                it.setData(Qt.ItemDataRole.AccessibleTextRole, f"{e.kind}: {e.text}")
                self.list.addItem(it)
        found = any(self._entry_at(i) is not None for i in range(self.list.count()))
        self.none.setText(tr("pal_none", q=query.strip()) if query.strip() else "")
        self.none.setVisible(bool(query.strip()) and not found and not self._pending)
        self.more.hide()
        if found:
            self.move_selection(1, start=-1)
        if self.isVisible():
            self._place()

    def _entry_at(self, row: int) -> Entry | None:
        it = self.list.item(row)
        return it.data(_ENTRY) if it is not None else None

    def current(self) -> Entry | None:
        return self._entry_at(self.list.currentRow())

    def move_selection(self, step: int, start: int | None = None) -> None:
        n = self.list.count()
        row = self.list.currentRow() if start is None else start
        rows = [i for i in range(n) if self._entry_at(i) is not None]
        if not rows:
            return
        if row < 0:
            target = rows[0] if step > 0 else rows[-1]
        else:
            later = [i for i in rows if (i > row if step > 0 else i < row)]
            if not later:
                target = rows[-1] if step > 0 else rows[0]
            else:
                target = later[min(abs(step), len(later)) - 1] if step > 0 \
                    else later[-min(abs(step), len(later))]
        self.list.setCurrentRow(target)
        self.list.scrollToItem(self.list.item(target))
        if self.more.isVisible():
            self._show_more()

    def toggle_more(self) -> None:
        if self.more.isVisible():
            self.more.hide()
        else:
            self._show_more()
        self._place()

    def _show_more(self) -> None:
        e = self.current()
        text = (e.more or e.where or e.text) if e is not None else ""
        self.more.setText(text)
        self.more.setVisible(bool(text))

    def _clicked(self, item: QListWidgetItem) -> None:
        if item.data(_ENTRY) is not None:
            self.list.setCurrentItem(item)
            self.activate()

    def activate(self) -> None:
        """Run the chosen row."""
        e = self.current()
        if e is None:
            return
        rect = self.list.visualItemRect(self.list.currentItem())
        self._run(e, self.list.viewport().mapToGlobal(rect.bottomLeft()))
