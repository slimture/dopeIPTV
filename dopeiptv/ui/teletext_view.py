"""The teletext viewer: a channel's pages as a TV draws them.

``TeletextPage`` paints the 40 x 25 cells core/teletext.py renders - the
eight teletext colours, the 2 x 3 mosaics (contiguous and separated),
double height, flash and conceal - scaled to the room it has. The dialog
around it takes pages 100-899: three digits, P+ / P- (the arrows up and
down, Page Up / Page Down), the subpages (left and right), the four colour
keys (R, G, Y, B: the page's Fastext links when it sends them), the index
(I), hold (H) and reveal (?). Its keys are its own while it has the focus:
the window's single-key shortcuts (R records, I shows the stats) wait.

Where the pages come from is the window's (ui/mw_teletext.py), asked a few
times a second: the app's own rewind buffer for the channel playing, or a
word that it is needed.
"""
from __future__ import annotations

from typing import Any, Protocol

from PyQt6.QtCore import QEvent, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QKeyEvent, QPainter
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                             QStackedWidget, QVBoxLayout, QWidget)

from ..core import teletext as ttx
from ..i18n import tr
from .theme import P

_COLOURS = [QColor(*rgb) for rgb in ttx.PALETTE]
#: A cell's width to its height: a TV's 4:3 page at most, never narrower
#: than the classic 12 x 20.
_ASPECT = (0.6, 0.85)


class TeletextSource(Protocol):
    """What the dialog asks of the window."""

    def teletext_state(self) -> tuple[str, ttx.Decoder | None, str]: ...

    def teletext_closed(self) -> None: ...

    def teletext_open_settings(self) -> None: ...


class TeletextPage(QWidget):
    """40 x 25 teletext cells, scaled to the widget."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cells: list[list[ttx.Cell]] = ttx.render(None)
        self._boxed_only = False
        self.reveal = False
        self._flash_on = True
        self._font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self._font.setStyleHint(QFont.StyleHint.TypeWriter)
        self._fonts: dict[tuple[int, int], QFont] = {}
        self.setMinimumSize(320, 250)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._flash = QTimer(self)
        self._flash.setInterval(500)
        self._flash.timeout.connect(self._blink)
        self._flash.start()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        return QSize(640, 500)

    def set_cells(self, cells: list[list[ttx.Cell]], boxed_only: bool = False) -> None:
        self._cells = cells
        self._boxed_only = boxed_only
        self.update()

    def cells(self) -> list[list[ttx.Cell]]:
        return self._cells

    def set_reveal(self, on: bool) -> None:
        self.reveal = on
        self.update()

    def _blink(self) -> None:
        self._flash_on = not self._flash_on
        if any(c.flash for row in self._cells for c in row):
            self.update()

    def page_rect(self) -> QRectF:
        """Where the page goes: as wide as the room allows within the cell
        shapes a page may have, centred."""
        w, h = max(1, self.width()), max(1, self.height())
        cw, ch = w / ttx.COLS, h / ttx.ROWS
        lo, hi = _ASPECT
        if cw / ch > hi:
            cw = ch * hi
        elif cw / ch < lo:
            ch = cw / lo
        pw, ph = cw * ttx.COLS, ch * ttx.ROWS
        return QRectF((w - pw) / 2, (h - ph) / 2, pw, ph)

    def _font_for(self, cw: float, ch: float) -> QFont:
        key = (int(cw * 4), int(ch * 4))
        font = self._fonts.get(key)
        if font is None:
            font = QFont(self._font)
            font.setPixelSize(max(6, int(ch * 0.82)))
            adv = QFontMetricsF(font).horizontalAdvance("M") or 1.0
            font.setStretch(max(50, min(200, int(100 * cw * 0.98 / adv))))
            if len(self._fonts) > 16:
                self._fonts.clear()
            self._fonts[key] = font
        return font

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt's name
        p = QPainter(self)
        p.fillRect(self.rect(), _COLOURS[0])
        page = self.page_rect()
        cw, ch = page.width() / ttx.COLS, page.height() / ttx.ROWS
        font = self._font_for(cw, ch)
        tall = self._font_for(cw, ch * 2)
        x0, y0 = page.x(), page.y()
        for y, row in enumerate(self._cells[:ttx.ROWS]):
            for x, c in enumerate(row[:ttx.COLS]):
                if self._boxed_only and not c.boxed and y:
                    continue
                cell = QRectF(x0 + x * cw, y0 + y * ch, cw + 0.5, ch + 0.5)
                p.fillRect(cell, _COLOURS[c.bg & 7])
                if (c.conceal and not self.reveal) or (c.flash and not self._flash_on):
                    continue
                area = cell
                if c.height == 1:
                    area = QRectF(cell.x(), cell.y(), cell.width(), ch * 2)
                elif c.height == 2:
                    area = QRectF(cell.x(), cell.y() - ch, cell.width(), ch * 2)
                p.save()
                p.setClipRect(cell)
                if c.mosaic >= 0:
                    self._mosaic(p, area, c.mosaic, c.separated, _COLOURS[c.fg & 7])
                elif c.ch.strip():
                    p.setPen(_COLOURS[c.fg & 7])
                    p.setFont(tall if c.height else font)
                    p.drawText(area, Qt.AlignmentFlag.AlignCenter, c.ch)
                p.restore()
        p.end()

    @staticmethod
    def _mosaic(p: QPainter, area: QRectF, mask: int, separated: bool, colour: QColor) -> None:
        xs = (area.left(), area.left() + area.width() / 2, area.right())
        ys = (area.top(), area.top() + area.height() / 3,
              area.top() + area.height() * 2 / 3, area.bottom())
        gap_x = area.width() / 12 if separated else 0.0
        gap_y = area.height() / 18 if separated else 0.0
        for bit in range(6):
            if not mask & (1 << bit):
                continue
            col, row = bit & 1, bit >> 1
            r = QRectF(xs[col] + gap_x, ys[row], xs[col + 1] - xs[col] - gap_x,
                       ys[row + 1] - ys[row] - gap_y)
            p.fillRect(r, colour)


class TeletextDialog(QDialog):
    """Pages 100-899 of the channel playing."""

    POLL_MS = 250

    def __init__(self, parent: QWidget | None, source: TeletextSource,
                 start: int = ttx.INDEX_PAGE) -> None:
        super().__init__(parent)
        self.setObjectName("TeletextDialog")
        self.setWindowTitle(tr("ttx_title"))
        self.source = source
        self.page = start
        self.sub: int | None = None          # None: the newest subpage
        self.hold = False
        self.typed = ""
        self._shown: ttx.Page | None = None
        self._state = ""
        self._decoder: ttx.Decoder | None = None
        self._version = -1
        self.resize(720, 600)

        self.view = TeletextPage(self)
        self.view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.note = QLabel("", self)
        self.note.setWordWrap(True)
        self.note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.note.setStyleSheet(f"color:{P['text']}; font-size:15px;")
        self.settings_btn = QPushButton(tr("ttx_open_settings"), self)
        self.settings_btn.clicked.connect(self._open_settings)
        self.settings_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        note_page = QWidget(self)
        nl = QVBoxLayout(note_page)
        nl.addStretch(1)
        nl.addWidget(self.note)
        nl.addWidget(self.settings_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        nl.addStretch(1)
        self.stack = QStackedWidget(self)
        self.stack.addWidget(self.view)
        self.stack.addWidget(note_page)

        bar = QWidget(self)
        bar.setObjectName("TeletextBar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(8, 6, 8, 6)
        bl.setSpacing(6)

        def button(text: str, tip: str, slot) -> QPushButton:
            b = QPushButton(text, bar)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(slot)
            return b

        bl.addWidget(button("◀", tr("ttx_prev_page"), self.prev_page))
        self.field = QLineEdit(bar)
        self.field.setFixedWidth(56)
        self.field.setMaxLength(3)
        self.field.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.field.setToolTip(tr("ttx_page_tip"))
        # The keys are the viewer's until the field is clicked.
        self.field.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.field.returnPressed.connect(self._field_go)
        bl.addWidget(self.field)
        bl.addWidget(button("▶", tr("ttx_next_page"), self.next_page))
        bl.addSpacing(8)
        bl.addWidget(button("‹", tr("ttx_prev_sub"), self.prev_sub))
        self.sub_lbl = QLabel("", bar)
        self.sub_lbl.setMinimumWidth(40)
        self.sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(self.sub_lbl)
        bl.addWidget(button("›", tr("ttx_next_sub"), self.next_sub))
        bl.addSpacing(8)
        self.hold_btn = button(tr("ttx_hold"), tr("ttx_hold_tip"), self.toggle_hold)
        self.hold_btn.setCheckable(True)
        bl.addWidget(self.hold_btn)
        self.reveal_btn = button(tr("ttx_reveal"), tr("ttx_reveal_tip"), self.toggle_reveal)
        self.reveal_btn.setCheckable(True)
        bl.addWidget(self.reveal_btn)
        bl.addSpacing(8)
        self.colour_btns: list[QPushButton] = []
        for i, colour in enumerate((1, 2, 3, 6)):
            b = button("", tr("ttx_colour_tip"), lambda _c=False, n=i: self.colour_key(n))
            rgb = _COLOURS[colour].name()
            b.setStyleSheet(f"QPushButton {{ background:{rgb}; color:{_COLOURS[0].name()};"
                            f" min-width:44px; border-radius:4px; font-weight:600; }}")
            self.colour_btns.append(b)
            bl.addWidget(b)
        bl.addStretch(1)
        self.status = QLabel("", bar)
        self.status.setStyleSheet(f"color:{P['muted']};")
        bl.addWidget(self.status)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.stack, 1)
        lay.addWidget(bar)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._poll = QTimer(self)
        self._poll.setInterval(self.POLL_MS)
        self._poll.timeout.connect(self.refresh)
        self._poll.start()
        self.refresh()

    # -- the keys ---------------------------------------------------------------

    _KEYS = {Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown,
             Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Backspace, Qt.Key.Key_Home,
             Qt.Key.Key_Plus, Qt.Key.Key_Minus, Qt.Key.Key_Escape}
    _LETTERS = set("rgybihtv?+-")

    def _ours(self, e: QKeyEvent) -> bool:
        mods = e.modifiers() & ~(Qt.KeyboardModifier.KeypadModifier
                                 | Qt.KeyboardModifier.ShiftModifier)
        if mods:
            return False
        text = (e.text() or "").lower()
        return (e.key() in self._KEYS or (text.isdigit() and len(text) == 1)
                or (len(text) == 1 and text in self._LETTERS))

    def event(self, e) -> bool:  # noqa: D401 - Qt's name
        # The window's single-key shortcuts (application-wide) would take R,
        # I, M...: while the viewer has the keyboard they are its own.
        if e.type() == QEvent.Type.ShortcutOverride and isinstance(e, QKeyEvent) \
                and not self.field.hasFocus() and self._ours(e):
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802 - Qt's name
        if self.field.hasFocus() and e.key() not in (Qt.Key.Key_Escape,):
            super().keyPressEvent(e)
            return
        key, text = e.key(), (e.text() or "").lower()
        if text.isdigit() and len(text) == 1:
            self.type_digit(text)
        elif key == Qt.Key.Key_Backspace:
            self.typed = self.typed[:-1]
            self._draw()
        elif key in (Qt.Key.Key_Up, Qt.Key.Key_PageUp, Qt.Key.Key_Plus) or text == "+":
            self.next_page()
        elif key in (Qt.Key.Key_Down, Qt.Key.Key_PageDown, Qt.Key.Key_Minus) or text == "-":
            self.prev_page()
        elif key == Qt.Key.Key_Right:
            self.next_sub()
        elif key == Qt.Key.Key_Left:
            self.prev_sub()
        elif key == Qt.Key.Key_Home or text == "i":
            self.index()
        elif text in ("r", "g", "y", "b"):
            self.colour_key("rgyb".index(text))
        elif text == "h":
            self.toggle_hold()
        elif text in ("?", "v"):
            self.toggle_reveal()
        elif text == "t" or key == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(e)

    # -- moving about -----------------------------------------------------------

    def go(self, page: int, sub: int | None = None) -> None:
        if not ttx.is_decimal(page):
            return
        self.page, self.sub, self.typed = page, sub, ""
        self.hold = False
        self.hold_btn.setChecked(False)
        self._shown = None
        self._version = -1
        self.refresh()

    def type_digit(self, d: str) -> None:
        if not self.typed and d in "09":
            return                          # pages are 100-899
        self.typed += d
        if len(self.typed) == 3:
            page = ttx.page_from_text(self.typed)
            self.typed = ""
            if page is not None:
                self.go(page)
                return
        self._draw()

    def _field_go(self) -> None:
        page = ttx.page_from_text(self.field.text())
        self.view.setFocus()
        self.setFocus()
        if page is not None:
            self.go(page)

    def _step(self, step: int) -> int:
        """The next page there is either way, as a TV with a page list
        does; the next number while none has come yet."""
        numbers = self._decoder.numbers() if self._decoder is not None else []
        if numbers:
            later = [n for n in numbers if (n > self.page if step > 0 else n < self.page)]
            if later:
                return later[0] if step > 0 else later[-1]
            return numbers[0] if step > 0 else numbers[-1]
        return ttx.page_step(self.page, step)

    def next_page(self) -> None:
        self.go(self._step(1))

    def prev_page(self) -> None:
        self.go(self._step(-1))

    def _subs(self) -> list[int]:
        return self._decoder.subpages(self.page) if self._decoder is not None else []

    def _step_sub(self, step: int) -> None:
        subs = self._subs()
        if len(subs) < 2:
            return
        cur = self.sub if self.sub in subs else (self._shown.sub if self._shown else subs[0])
        at = subs.index(cur) if cur in subs else 0
        self.sub = subs[(at + step) % len(subs)]
        self._shown = None
        self._version = -1
        self.refresh()

    def next_sub(self) -> None:
        self._step_sub(1)

    def prev_sub(self) -> None:
        self._step_sub(-1)

    def index(self) -> None:
        link = self._shown.link(5) if self._shown is not None else None
        self.go(link or ttx.INDEX_PAGE)

    def colour_key(self, n: int) -> None:
        """Red, green, yellow, blue: the page's Fastext link; without one,
        P-, P+, the next magazine and the index."""
        link = self._shown.link(n) if self._shown is not None else None
        if link is not None:
            self.go(link)
        elif n == 0:
            self.prev_page()
        elif n == 1:
            self.next_page()
        elif n == 2:
            self.go(ttx.page_step(self.page, 100))
        else:
            self.go(ttx.INDEX_PAGE)

    def toggle_hold(self) -> None:
        self.hold = not self.hold
        self.hold_btn.setChecked(self.hold)
        self._draw()

    def toggle_reveal(self) -> None:
        self.view.set_reveal(not self.view.reveal)
        self.reveal_btn.setChecked(self.view.reveal)

    def _open_settings(self) -> None:
        self.source.teletext_open_settings()

    # -- what is shown ----------------------------------------------------------

    def refresh(self) -> None:
        """(a few times a second) The source's state, and the page again
        when a newer one came."""
        try:
            state, decoder, title = self.source.teletext_state()
        except Exception:
            state, decoder, title = "error", None, ""
        self._decoder = decoder
        self.setWindowTitle(f"{tr('ttx_title')} - {title}" if title else tr("ttx_title"))
        if state in ("no_buffer", "not_live"):
            self._state = state
            self.note.setText(tr("ttx_needs_buffer") if state == "no_buffer"
                              else tr("ttx_not_live"))
            self.settings_btn.setVisible(state == "no_buffer")
            self.stack.setCurrentIndex(1)
            return
        self.stack.setCurrentIndex(0)
        self._state = state
        if decoder is None:
            return
        if not self.hold and decoder.version != self._version:
            self._version = decoder.version
            page = decoder.get(self.page, self.sub)
            if page is not None:
                self._shown = page
        self._draw()

    def _draw(self) -> None:
        decoder = self._decoder
        page = self._shown if self._shown is not None and self._shown.number == self.page else None
        if self.typed:
            status = "P" + self.typed.ljust(3, "-")
        else:
            status = "P" + ttx.page_text(self.page)
        header = decoder.rolling_header() if decoder is not None and not self.hold else None
        cells = ttx.render(page, header, status)
        boxed = page is not None and (page.subtitle or page.newsflash)
        self.view.set_cells(cells, boxed_only=boxed)
        if not self.field.hasFocus():
            self.field.setText(self.typed or ttx.page_text(self.page))
        subs = self._subs()
        if page is not None and len(subs) > 1 and page.sub in subs:
            self.sub_lbl.setText(f"{subs.index(page.sub) + 1}/{len(subs)}")
        else:
            self.sub_lbl.setText("")
        for i, b in enumerate(self.colour_btns):
            link = page.link(i) if page is not None else None
            b.setText(ttx.page_text(link) if link is not None else "")
        if self._state == "no_teletext":
            self.status.setText(tr("ttx_none"))
        elif self._state == "error":
            self.status.setText(tr("ttx_error"))
        elif page is None:
            self.status.setText(tr("ttx_waiting", page=ttx.page_text(self.page)))
        elif self.hold:
            self.status.setText(tr("ttx_held"))
        else:
            self.status.setText("")

    def showEvent(self, e: Any) -> None:  # noqa: N802 - Qt's name
        super().showEvent(e)
        self.setFocus()

    def closeEvent(self, e: Any) -> None:  # noqa: N802 - Qt's name
        self._poll.stop()
        try:
            self.source.teletext_closed()
        finally:
            super().closeEvent(e)
