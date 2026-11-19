"""A place of the new look over a list the window keeps, pure presentation:
the form Local files, Recordings and Radio share on ``media_cards``'
``PlacePage`` (their window sides keep the list as the source of truth).

* The header's one quiet line: a way back at its start ("‹ Back to
  Films", cut to its room - shown only below the top; the mouse's back
  button and Backspace too), what it shows and its count, then the filter
  (the list's own), the page's own square buttons (``tool_button``), Grid
  / List as drawn marks (``layout_icon``) and the header's switch.
* On a narrow pane the controls take rows of their own under the title -
  the switches and buttons, then the filter as wide as the page - so the
  title keeps its room and the page never runs wider than the pane.
* The page's rows (``_make_rows``: Continue, what records now, the
  favourite stations), a calm word where there are no cards (with an
  action beside it, ``set_note``), then the cards (``PlaceCardView``).

Fast with big lists: one view of uniform rows or cells with a painted
delegate; what a card shows beyond its name worked out only as it is
painted or about to be (``PlaceCardView.dress``), the pictures through the
loaders for those cards alone. A picture that is not the shape of its box
- a folder's glyph, a channel's or a station's logo, an album's square
cover - is fitted inside it on the card's surface rather than cut
(``fits_inside``).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PyQt6.QtCore import QItemSelectionModel, QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget,
)

from ..i18n import tr
from .media_cards import Card, CardDelegate, CardView, PageHeader, PlacePage, Segmented, is_heading
from .series_page import drawn_icon
from .theme import P, corner_radius
from .widgets import plain_text

__all__ = ["ListPlace", "PlaceCard", "PlaceCardView", "fits_inside", "layout_icon",
           "line_of"]


@dataclass(slots=True)
class PlaceCard(Card):
    """A row of the window's list on a card; *gen* the dressing it was
    dressed for (``PlaceCardView``: -1, not yet); *fit* its picture fitted
    inside its box (True: a logo), covering it (False), or by its shape
    (None)."""
    gen: int = -1
    fit: bool | None = None


def line_of(parts, rtl: bool | None = None) -> str:
    """A card's muted line from its parts ("SVT1 · Today 21:00 · 1.2 GB"),
    in the window's direction: in a right-to-left language a line that
    begins with a Latin name (a channel, an artist) still reads from the
    right, each part whole - not a size split from its unit."""
    text = " · ".join(str(p) for p in parts if p)
    if rtl is None:
        rtl = QApplication.layoutDirection() == Qt.LayoutDirection.RightToLeft
    return "\u200f" + text if (rtl and text) else text


#: How far a picture's shape may be from its box's and still cover it.
FIT_RATIO = 1.25
#: A picture this much wider than its box covers it again: a frame of a
#: film in a poster's box shows its middle, as a poster would.
CROP_RATIO = 2.0


def fits_inside(pic_w: int, pic_h: int, box_w: float, box_h: float) -> bool:
    """Whether a picture is fitted inside its box rather than covering it:
    its shape too far from the box's - a square cover or a logo in a
    poster's box, a poster in a row's wide box - but not a frame of a film
    in a poster's box (its middle covers it)."""
    if pic_w <= 0 or pic_h <= 0 or box_w <= 0 or box_h <= 0:
        return False
    r = (pic_w / pic_h) / (box_w / box_h)
    if r >= CROP_RATIO:
        return False
    return r > FIT_RATIO or r < 1 / FIT_RATIO


class _Delegate(CardDelegate):
    """The shared card, dressed just before it is painted, its picture
    fitted when it is not the shape of its box."""

    def paint(self, p, option, index) -> None:
        v = self._view
        card = v.model_.item_at(index.row())
        if card is not None:
            v.dress(card)
        super().paint(p, option, index)

    def picture_fits(self, card, pm) -> bool:
        if pm is None or pm.isNull():
            return False
        v = self._view
        own = getattr(card, "fit", None)
        if own is not None:
            return bool(own)
        if getattr(v, "fit_always", False):
            return True
        kind = v.list_picture if v.layout_kind == "list" else v.layout_kind
        box = (16.0, 9.0) if kind == "wide" else (2.0, 3.0)
        return fits_inside(pm.width(), pm.height(), *box)


class PlaceCardView(CardView):
    """The place's cards: a ``CardView`` whose cards are dressed as they
    paint (*dress_fn*, the window's) and again after a change
    (``redress``). One click picks (the detail panel follows), a double
    click or Enter plays or opens; Ctrl / Shift gather several for the
    menu; Backspace goes back as the mouse's back button does.
    *fit_always*: every picture fitted inside its box (logos)."""

    OBJECT_NAME = "PlaceCardView"

    def __init__(self, loader=None, layout: str = "list", parent=None) -> None:
        self.dress_fn: Callable[[Card], None] | None = None
        self.fit_always = False
        self._gen = 0
        super().__init__(loader, layout, parent)
        self.setItemDelegate(_Delegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.click_plays = lambda: False

    def dress(self, card: Card) -> None:
        if not isinstance(card, PlaceCard) or card.gen == self._gen or is_heading(card):
            return
        card.gen = self._gen
        fn = self.dress_fn
        if fn is None:
            return
        try:
            fn(card)
        except Exception:               # noqa: BLE001 - a card painted plain rather than none
            pass

    def redress(self) -> None:
        """Every card dressed again as it next paints (art found, marks)."""
        self._gen += 1
        self._ahead = None
        self.viewport().update()
        self._ask_ahead()

    def wanted_urls(self, item) -> list[str]:
        self.dress(item)
        return super().wanted_urls(item)

    def keyPressEvent(self, e) -> None:
        plain = not (e.modifiers() & (Qt.KeyboardModifier.ControlModifier
                                      | Qt.KeyboardModifier.AltModifier
                                      | Qt.KeyboardModifier.MetaModifier))
        if e.key() == Qt.Key.Key_Backspace and plain:
            self.back.emit()
            e.accept()
            return
        super().keyPressEvent(e)

    def contextMenuEvent(self, e) -> None:
        """A right click in a selection of several keeps it (the menu acts
        on all of them), else picks the card under it."""
        idx = self.indexAt(e.pos())
        it = self.model_.item_at(idx.row()) if idx.isValid() else None
        if it is None or is_heading(it):
            return
        sm = self.selectionModel()
        if sm.isSelected(idx) and len(sm.selectedRows()) > 1:
            sm.setCurrentIndex(idx, QItemSelectionModel.SelectionFlag.NoUpdate)
        else:
            self.setCurrentIndex(idx)
        self.menu.emit(it, e.globalPos())

    def selected_items(self) -> list:
        rows = sorted(ix.row() for ix in self.selectionModel().selectedIndexes())
        return [c for c in (self.item_at(r) for r in rows) if c is not None and not is_heading(c)]


def layout_icon(kind: str, ink: str, size: int = 14, dpr: float = 1.0) -> QPixmap:
    """The grid's mark (four rounded squares) or the list's (three rows),
    drawn the same on every system."""
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = float(size)
    col = QColor(ink)
    if kind == "grid":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        cell = s * 0.36
        for x in (s * 0.1, s * 0.54):
            for y in (s * 0.1, s * 0.54):
                p.drawRoundedRect(QRectF(x, y, cell, cell), s * 0.08, s * 0.08)
    else:
        pen = QPen(col, max(1.4, s * 0.12))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        for y in (0.25, 0.5, 0.75):
            p.drawPoint(QPointF(s * 0.14, s * y))
            p.drawLine(QPointF(s * 0.34, s * y), QPointF(s * 0.88, s * y))
    p.end()
    return pm


class _Filter(QLineEdit):
    """The filter: room for its words when there is room (it gives way to
    the title on a narrow pane, or takes a row of its own)."""

    def __init__(self, width: int, **kw) -> None:
        super().__init__(**kw)
        self._room = width

    def sizeHint(self):
        hint = super().sizeHint()
        hint.setWidth(max(hint.width(), self._room - 20))
        return hint


class ListPlace(PlacePage):
    """The form: the header - Back at its start - with the filter, the
    page's own buttons, Grid / List and the header's switch; the page's
    rows; a calm word; the cards. The window fills each part and listens
    to their signals. A page builds on it with ``_make_tools`` (its square
    buttons, in the header after the filter) and ``_make_rows`` (its rows
    under the header)."""

    back_requested = pyqtSignal()
    #: The action beside the calm word (``set_note(..., action=True)``).
    note_action = pyqtSignal()
    #: The layout picked: "poster" / "wide" (a grid) or "list".
    layout_changed = pyqtSignal(str)

    MARGIN_TOP = 6
    FILTER_W = 240
    #: The room the header's name keeps beside the controls; with less, the
    #: controls take rows of their own under it.
    TITLE_ROOM = 140
    #: The least the filter is given beside the line before the controls
    #: take rows of their own (the header narrows it that far for the line).
    FILTER_MIN = 140
    #: The stem of the parts' object names ("LocalBack", "RecNote", ...).
    NAME = "Place"
    VIEW: type[PlaceCardView] = PlaceCardView
    #: The grid's kind beside the list.
    GRID = "poster"
    #: The calm word's action, as words (none: no button).
    NOTE_ACTION = ""

    def __init__(self, title: str, loader=None, parent=None) -> None:
        super().__init__(title, scroll_whole=True, parent=parent)
        self._title_words = title
        self.setAccessibleName(title)
        h = self.header
        # Back, at the start of the header's line: "‹ Back to Films" - a
        # control that reads as one, shown only below the top.
        self.back = QPushButton(tr("btn_back"))
        self.back.setObjectName(f"{self.NAME}Back")
        self.back.setFlat(True)
        self.back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back.setIconSize(QSize(16, 16))
        self.back.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.back.clicked.connect(self.back_requested)
        self.back.setFixedHeight(PageHeader.CONTROL_H)
        h.add_lead(self.back)
        #: Kept by its old name: the way back is its own row no more.
        self._back_row = self.back
        self.back.hide()
        # The header's own: the filter, the page's buttons, Grid / List.
        self.filter = _Filter(self.FILTER_W, objectName="Search")
        self.filter.setClearButtonEnabled(True)
        self.filter.setMinimumWidth(90)
        self.filter.setMaximumWidth(self.FILTER_W)
        self.filter.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        h.add_widget(self.filter)
        self.tools: list[QToolButton] = list(self._make_tools())
        #: Whether each of the page's buttons is wanted (``show_tool``).
        self._tool_on: dict[int, bool] = {}
        for t in self.tools:
            h.add_widget(t)
        self._layout_shown = False
        self.layout_switch = Segmented(name=tr("card_view_switch_a11y"))
        self.layout_switch.setFixedHeight(PageHeader.CONTROL_H)
        self.layout_switch.changed.connect(self.layout_changed)
        h.add_widget(self.layout_switch)
        self._set_layout_options()
        self._tools = QWidget()
        tv = QVBoxLayout(self._tools)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(10)
        self._tools_row = QHBoxLayout()
        self._tools_row.setSpacing(8)
        self._filter_lay = QHBoxLayout()
        tv.addLayout(self._tools_row)
        tv.addLayout(self._filter_lay)
        self.add_row(self._tools)
        self._tools.hide()
        self._narrow = False
        self._tight = False
        self._back_full = ""
        self._make_rows(loader)
        self._note_box = QWidget()
        nb = QHBoxLayout(self._note_box)
        nb.setContentsMargins(0, 0, 0, 0)
        nb.setSpacing(12)
        self.note = QLabel("")
        self.note.setObjectName(f"{self.NAME}PlaceNote")
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        self.note.setWordWrap(True)
        nb.addWidget(self.note, 1)
        self.note_btn = QPushButton(self.NOTE_ACTION)
        self.note_btn.setObjectName(f"{self.NAME}NoteAction")
        self.note_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.note_btn.clicked.connect(self.note_action)
        self.note_btn.setVisible(False)
        nb.addWidget(self.note_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.add_row(self._note_box)
        self._note_box.hide()
        self.view = self.VIEW(loader, "list")
        self.set_content(self.view)
        self.view.back.connect(self.back_requested)
        self.filter.returnPressed.connect(lambda: self.view.setFocus(
            Qt.FocusReason.TabFocusReason))

    # -- what a page adds ----------------------------------------------------------

    def _make_tools(self) -> Sequence[QToolButton]:
        """The page's own square buttons beside the filter."""
        return ()

    def _make_rows(self, loader) -> None:
        """The page's own rows under the header (``add_row``)."""

    def tool_button(self, name: str) -> QToolButton:
        """A square button as tall as the header's controls, filled as they
        are (its icon the window's)."""
        b = QToolButton()
        b.setObjectName(name)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFixedSize(PageHeader.CONTROL_H, PageHeader.CONTROL_H)
        b.setIconSize(QSize(15, 15))
        b.setAutoRaise(False)
        return b

    def show_tool(self, tool: QToolButton, shown: bool) -> None:
        """One of the page's buttons shown or left out (wherever the
        controls stand)."""
        if self._tool_on.get(id(tool), True) == shown:
            return
        self._tool_on[id(tool)] = shown
        tool.setVisible(shown)
        self._fit_controls(force=True)

    def _tool_wanted(self, tool: QToolButton) -> bool:
        return self._tool_on.get(id(tool), True)

    # -- look and words ------------------------------------------------------------

    def restyle(self) -> None:
        super().restyle()
        if not hasattr(self, "note"):
            return                      # PlacePage's own call, before the parts are made
        r = round(corner_radius(10))
        name = self.back.objectName()
        self.back.setStyleSheet(
            f"QPushButton#{name} {{ color:{P['text2']}; background:transparent;"
            f" border:none; border-radius:{r}px; padding:5px 10px 5px 4px; font-size:13px;"
            " font-weight:600; }"
            f"QPushButton#{name}:hover {{ background:{P['hover']}; color:{P['text']}; }}")
        dpr = self.devicePixelRatioF() or 1.0
        self.back.setIcon(drawn_icon("back", 16, P["text2"], self.isRightToLeft(), dpr))
        for t in self.tools:
            n = t.objectName()
            t.setStyleSheet(
                f"QToolButton#{n} {{ background:{P['input']}; border:none;"
                f" border-radius:{r}px; }}"
                f"QToolButton#{n}:hover {{ background:{P['btn']}; }}"
                f"QToolButton#{n}::menu-indicator {{ image:none; width:0; }}")
        self.note.setStyleSheet(f"color:{P['muted']}; font-size:13px;"
                                " background:transparent; padding:12px 0;")
        self.layout_switch.restyle()
        self._set_layout_icons()

    def retranslate(self) -> None:
        self.filter.setAccessibleName(tr("list_filter_placeholder"))
        if not self.filter.placeholderText():
            self.filter.setPlaceholderText(tr("list_filter_placeholder"))
        self._set_layout_options(self.layout_switch.current())

    def _set_layout_options(self, current: str = "") -> None:
        self.layout_switch.set_options([(self.GRID, tr("card_grid")), ("list", tr("card_list"))],
                                       current or self.GRID)
        for b, words in zip(self.layout_switch.buttons, (tr("card_grid"), tr("card_list")),
                            strict=False):
            b.setText("")
            b.setToolTip(words)
            b.setAccessibleName(words)
            b.setIconSize(QSize(14, 14))
        self._set_layout_icons()
        self.layout_switch.setVisible(self._layout_shown and not getattr(self, "_tight", False))

    def _set_layout_icons(self) -> None:
        dpr = self.devicePixelRatioF() or 1.0
        for b, kind in zip(self.layout_switch.buttons, ("grid", "list"), strict=False):
            icon = QIcon()
            icon.addPixmap(layout_icon(kind, P["muted"], 14, dpr), QIcon.Mode.Normal,
                           QIcon.State.Off)
            icon.addPixmap(layout_icon(kind, P["text"], 14, dpr), QIcon.Mode.Normal,
                           QIcon.State.On)
            b.setIcon(icon)

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        if e.type() == e.Type.LayoutDirectionChange and hasattr(self, "back"):
            dpr = self.devicePixelRatioF() or 1.0
            self.back.setIcon(drawn_icon("back", 16, P["text2"], self.isRightToLeft(), dpr))

    # -- what it shows -------------------------------------------------------------

    def set_back(self, text: str | None) -> None:
        """The way back's words ("Back to Films"); None hides it (the top)."""
        if text:
            self._back_full = text
            self.back.setToolTip(plain_text(text))     # a folder's, a country's name
            self.back.setAccessibleName(text)
            self._fit_back()
        if self.back.isHidden() == bool(text):
            self.back.setVisible(bool(text))
            self._fit_controls(force=True)

    #: The most of the line the way back takes, in pixels.
    BACK_MAX = 200

    def _fit_back(self) -> None:
        """The way back's words cut with "…" to its share of the line: a
        long name never holds the page wider than the pane, nor takes the
        line from what is shown."""
        full = getattr(self, "_back_full", "") or self.back.text()
        room = max(60, min(self.BACK_MAX, (self.width() - 2 * self.MARGIN_X) // 3 - 36))
        text = self.back.fontMetrics().elidedText(full, Qt.TextElideMode.ElideMiddle, room)
        if self.back.text() != text:
            self.back.setText(text)
        self.back.setMaximumWidth(room + 36)

    def set_note(self, text: str, action: bool = False) -> None:
        """A calm word where the cards are while there are none - with the
        page's action beside it (*action*)."""
        self.note.setText(text)
        self.note_btn.setVisible(action and bool(self.note_btn.text()))
        self._note_box.setVisible(bool(text) or self.note_btn.isVisibleTo(self._note_box))

    def set_layout(self, layout: str, picture: str) -> None:
        v = self.view
        if v.layout_kind != layout or v.list_picture != picture:
            v.set_layout(layout, picture)

    def show_layout_switch(self, shown: bool, current: str = "") -> None:
        if current:
            self.layout_switch.set_current(current)
        changed = shown != self._layout_shown
        self._layout_shown = shown
        self._fit_controls(force=changed)

    # -- a narrow pane -------------------------------------------------------------

    def needed_width(self) -> int:
        """How wide the page must be for the controls to stand beside the
        title in the header: the title's room, each control shown, the
        spacing and the page's margins."""
        h = self.header
        ws = [max(self.filter.minimumWidth(), self.FILTER_MIN),
              *(t.width() for t in self.tools if self._tool_wanted(t)),
              h.switch.sizeHint().width() if h.switch.keys() else 0,
              self.layout_switch.sizeHint().width() if self._layout_shown else 0]
        if not self.back.isHidden():
            ws.append(self.back.sizeHint().width())
        if not h.sort_btn.isHidden():
            ws.append(h.sort_btn.sizeHint().width())
        shown = [w for w in ws if w]
        return (2 * self.MARGIN_X + self.TITLE_ROOM + sum(shown)
                + 8 * max(0, len(shown) - 1) + 12)

    def tools_width(self) -> int:
        """How wide the page must be for the narrow pane's row of switches
        to hold Grid / List too (else it leaves them out)."""
        h = self.header
        return (2 * self.MARGIN_X + 8
                + sum(t.width() + 8 for t in self.tools if self._tool_wanted(t))
                + (h.switch.sizeHint().width() if h.switch.keys() else 0)
                + self.layout_switch.sizeHint().width())

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._fit_controls()
        if not self.back.isHidden():
            self._fit_back()

    def _fit_controls(self, force: bool = False) -> None:
        narrow = self.width() < self.needed_width()
        tight = narrow and self.width() < self.tools_width()
        if force or narrow != self._narrow or tight != self._tight:
            self._narrow, self._tight = narrow, tight
            self._arrange()

    def _arrange(self) -> None:
        """The controls in the header, or - on a narrow pane - in rows of
        their own under it (the switches and buttons, then the filter)."""
        h = self.header
        extra, row = h._extra, h.layout()
        f, ls, sw = self.filter, self.layout_switch, h.switch
        had = f.hasFocus()
        for w in (f, *self.tools, ls):
            extra.removeWidget(w)
        row.removeWidget(sw)
        for lay in (self._tools_row, self._filter_lay):
            while lay.count():
                lay.takeAt(0)           # the widgets stay; a stretch goes
        if self._narrow:
            f.setMaximumWidth(16777215)
            f.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._tools_row.addWidget(sw)
            self._tools_row.addStretch(1)
            self._tools_row.addWidget(ls)
            for t in self.tools:
                self._tools_row.addWidget(t)
            self._filter_lay.addWidget(f)
            self._tools.show()
        else:
            f.setMaximumWidth(self.FILTER_W)
            f.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            for w in (f, *self.tools, ls):
                extra.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(sw, 0, Qt.AlignmentFlag.AlignVCenter)
            self._tools.hide()
        f.setVisible(True)
        for t in self.tools:
            t.setVisible(self._tool_wanted(t))
        # The narrowest pane: Grid / List left out (the choice stays).
        ls.setVisible(self._layout_shown and not self._tight)
        sw.setVisible(bool(sw.keys()))
        if had:
            f.setFocus()

    def is_narrow(self) -> bool:
        return self._narrow

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Backspace and not self.filter.hasFocus() \
                and QApplication.focusWidget() is not self.filter:
            self.back_requested.emit()
            e.accept()
            return
        super().keyPressEvent(e)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.BackButton:
            self.back_requested.emit()
            e.accept()
            return
        super().mousePressEvent(e)
