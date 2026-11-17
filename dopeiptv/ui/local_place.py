"""Local files in the shared form of every place, pure presentation (the
window's side is ui/mw_place_local.py): a way back to the folder above
over the header ("‹ Back to Films" - and the mouse's back button, and
Backspace), the header (the folder's name, or "Local files" at the top;
how many folders and files, or the library's series and films; a filter;
Rescan; in the library Grid / List; the Folders / Library switch), the
"Continue" row of the files in progress, a calm word where there is
nothing (with "Add folder…" while there is no folder at all), then the
content: a folder's folders and files as rows, the library as posters or
rows.

Fast with big folders and big libraries: one view of uniform rows or cells
with a painted delegate, what a card shows beyond its name - its picture,
its line, its pills, how far it got - worked out only as the card is
painted or about to be (``LocalCardView.dress``), the pictures through the
loaders for those cards alone; rows put in and taken out where they are,
without a reset (``insert_items``, ``remove_rows``), so a folder read in
slices grows at its end and one changed on disk changes in place.

A picture that is not the shape of its box - a folder's glyph, an album's
square cover, a still in a poster's box - is fitted inside it on the
card's surface rather than cut (``_Delegate.picture_fits``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget,
)

from ..i18n import tr
from .media_cards import Card, CardDelegate, CardRow, CardView, PageHeader, PlacePage, Segmented
from .series_page import drawn_icon
from .theme import P, corner_radius

__all__ = ["LocalCard", "LocalCardView", "LocalPlace", "layout_icon"]


@dataclass(slots=True)
class LocalCard(Card):
    """A row of Local files on a card; *gen* the dressing it was dressed
    for (``LocalCardView``: -1, not yet)."""
    gen: int = -1


#: How far a picture's shape may be from its box's and still cover it.
FIT_RATIO = 1.25
#: A picture this much wider than its box covers it again: a frame of a
#: film in a poster's box shows its middle, as a poster would.
CROP_RATIO = 2.0


def fits_inside(pic_w: int, pic_h: int, box_w: float, box_h: float) -> bool:
    """Whether a picture is fitted inside its box rather than covering it:
    its shape too far from the box's - a square cover or a folder's glyph
    in a poster's box, a poster in a row's wide box - but not a frame of a
    film in a poster's box (its middle covers it)."""
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
        kind = v.list_picture if v.layout_kind == "list" else v.layout_kind
        box = (16.0, 9.0) if kind == "wide" else (2.0, 3.0)
        return fits_inside(pm.width(), pm.height(), *box)


class LocalCardView(CardView):
    """The folder's rows or the library's cards: a ``CardView`` whose cards
    are dressed as they paint (*dress_fn*, the window's) and again after a
    change (``redress``). One click picks (the detail panel follows), a
    double click or Enter plays or opens; Ctrl / Shift gather several for
    the menu; Backspace goes back as the mouse's back button does."""

    OBJECT_NAME = "LocalPlaceView"

    def __init__(self, loader=None, layout: str = "list", parent=None) -> None:
        self.dress_fn: Callable[[Card], None] | None = None
        self._gen = 0
        super().__init__(loader, layout, parent)
        self.setItemDelegate(_Delegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.click_plays = lambda: False

    def dress(self, card: Card) -> None:
        if not isinstance(card, LocalCard) or card.gen == self._gen:
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
        if it is None:
            return
        sm = self.selectionModel()
        if sm.isSelected(idx) and len(sm.selectedRows()) > 1:
            from PyQt6.QtCore import QItemSelectionModel
            sm.setCurrentIndex(idx, QItemSelectionModel.SelectionFlag.NoUpdate)
        else:
            self.setCurrentIndex(idx)
        self.menu.emit(it, e.globalPos())

    def selected_items(self) -> list:
        rows = sorted(ix.row() for ix in self.selectionModel().selectedIndexes())
        return [c for c in (self.item_at(r) for r in rows) if c is not None]


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

    def sizeHint(self):
        hint = super().sizeHint()
        hint.setWidth(max(hint.width(), LocalPlace.FILTER_W - 20))
        return hint


class LocalPlace(PlacePage):
    """The place: Back over the header; the header with the filter,
    Rescan, Grid / List (the library) and Folders / Library; Continue; a
    calm word; the cards. The window fills each part and listens to their
    signals."""

    back_requested = pyqtSignal()
    add_requested = pyqtSignal()
    #: The library's layout picked: "poster" or "list".
    layout_changed = pyqtSignal(str)

    MARGIN_TOP = 6
    FILTER_W = 240
    #: The room the title keeps beside the controls; with less, the controls
    #: take rows of their own under it.
    TITLE_ROOM = 220

    def __init__(self, loader=None, parent=None) -> None:
        super().__init__(tr("nav_local"), scroll_whole=True, parent=parent)
        self.setAccessibleName(tr("nav_local"))
        h = self.header
        for label in (h.title, h.count):
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        # Back, over the header: "‹ Back to Films" - a control that reads as
        # one, shown only below the top.
        self.back = QPushButton(tr("btn_back"))
        self.back.setObjectName("LocalBack")
        self.back.setFlat(True)
        self.back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back.setIconSize(QSize(16, 16))
        self.back.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.back.clicked.connect(self.back_requested)
        self._back_row = QWidget()
        self._back_row.setObjectName("LocalBackRow")
        br = QHBoxLayout(self._back_row)
        br.setContentsMargins(0, 0, 0, 0)
        br.addWidget(self.back)
        br.addStretch(1)
        self._lay.insertWidget(0, self._back_row)
        self._rows_at += 1
        self._back_row.hide()
        # The header's own: the filter, Rescan, the library's Grid / List.
        self.filter = _Filter(objectName="Search")
        self.filter.setClearButtonEnabled(True)
        self.filter.setMinimumWidth(90)
        self.filter.setMaximumWidth(self.FILTER_W)
        self.filter.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        h.add_widget(self.filter)
        self.rescan = QToolButton()
        self.rescan.setObjectName("LocalRescan")
        self.rescan.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rescan.setFixedSize(PageHeader.CONTROL_H, PageHeader.CONTROL_H)
        self.rescan.setIconSize(QSize(15, 15))
        h.add_widget(self.rescan)
        self._layout_shown = False
        self.layout_switch = Segmented(name=tr("card_view_switch_a11y"))
        self.layout_switch.setFixedHeight(PageHeader.CONTROL_H)
        self.layout_switch.changed.connect(self.layout_changed)
        h.add_widget(self.layout_switch)
        self._set_layout_options()
        # On a narrow pane the controls take rows of their own under the
        # header - the switches and Rescan, then the filter as wide as the
        # page - so the title keeps its room and the page never runs wider
        # than the pane.
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
        self.cont = CardRow(tr("card_continue"), loader, picture="wide")
        self.add_row(self.cont)
        self._note_box = QWidget()
        nb = QHBoxLayout(self._note_box)
        nb.setContentsMargins(0, 0, 0, 0)
        nb.setSpacing(12)
        self.note = QLabel("")
        self.note.setObjectName("LocalPlaceNote")
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        self.note.setWordWrap(True)
        nb.addWidget(self.note, 1)
        self.add_btn = QPushButton(tr("local_add_folder"))
        self.add_btn.setObjectName("LocalAdd")
        self.add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_btn.clicked.connect(self.add_requested)
        nb.addWidget(self.add_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.add_row(self._note_box)
        self._note_box.hide()
        self.view = LocalCardView(loader, "list")
        self.set_content(self.view)
        self.view.back.connect(self.back_requested)
        self.filter.returnPressed.connect(lambda: self.view.setFocus(
            Qt.FocusReason.TabFocusReason))
        self.retranslate()
        self.restyle()

    # -- look and words ------------------------------------------------------------

    def restyle(self) -> None:
        super().restyle()
        if not hasattr(self, "note"):
            return                      # PlacePage's own call, before the parts are made
        r = round(corner_radius(10))
        self.back.setStyleSheet(
            f"QPushButton#LocalBack {{ color:{P['text2']}; background:transparent;"
            f" border:none; border-radius:{r}px; padding:5px 10px 5px 4px; font-size:13px;"
            " font-weight:600; }"
            f"QPushButton#LocalBack:hover {{ background:{P['hover']}; color:{P['text']}; }}")
        dpr = self.devicePixelRatioF() or 1.0
        self.back.setIcon(drawn_icon("back", 16, P["text2"], self.isRightToLeft(), dpr))
        self.rescan.setStyleSheet(
            f"QToolButton#LocalRescan {{ background:{P['input']}; border:none;"
            f" border-radius:{r}px; }}"
            f"QToolButton#LocalRescan:hover {{ background:{P['btn']}; }}")
        self.note.setStyleSheet(f"color:{P['muted']}; font-size:13px;"
                                " background:transparent; padding:12px 0;")
        self.layout_switch.restyle()
        self._set_layout_icons()

    def retranslate(self) -> None:
        self.setAccessibleName(tr("nav_local"))
        self.filter.setAccessibleName(tr("list_filter_placeholder"))
        if not self.filter.placeholderText():
            self.filter.setPlaceholderText(tr("list_filter_placeholder"))
        self.cont.set_title(tr("card_continue"))
        self.add_btn.setText(tr("local_add_folder"))
        self.rescan.setAccessibleName(tr("local_rescan"))
        self._set_layout_options(self.layout_switch.current())

    def _set_layout_options(self, current: str = "") -> None:
        self.layout_switch.set_options([("poster", tr("card_grid")), ("list", tr("card_list"))],
                                       current or "poster")
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
            self.back.setToolTip(text)
            self.back.setAccessibleName(text)
            self._fit_back()
        self._back_row.setVisible(bool(text))

    def _fit_back(self) -> None:
        """The way back's words cut with "…" to the page's width: a long
        folder name never holds the page wider than the pane."""
        full = getattr(self, "_back_full", "") or self.back.text()
        room = max(60, self.width() - 2 * self.MARGIN_X - 16 - 36)
        text = self.back.fontMetrics().elidedText(full, Qt.TextElideMode.ElideMiddle, room)
        if self.back.text() != text:
            self.back.setText(text)
        self.back.setMaximumWidth(room + 36)

    def set_note(self, text: str, add: bool = False) -> None:
        """A calm word where the cards are while there are none - with
        "Add folder…" beside it (*add*) while there is no folder at all."""
        self.note.setText(text)
        self.add_btn.setVisible(add)
        self._note_box.setVisible(bool(text) or add)

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
        ws = [self.filter.sizeHint().width(), self.rescan.width(),
              h.switch.sizeHint().width() if h.switch.keys() else 0,
              self.layout_switch.sizeHint().width() if self._layout_shown else 0]
        shown = [w for w in ws if w]
        return (2 * self.MARGIN_X + self.TITLE_ROOM + sum(shown)
                + 8 * max(0, len(shown) - 1) + 12)

    def tools_width(self) -> int:
        """How wide the page must be for the narrow pane's row of switches
        to hold Grid / List too (else it leaves them out)."""
        h = self.header
        return (2 * self.MARGIN_X + self.rescan.width() + 16
                + (h.switch.sizeHint().width() if h.switch.keys() else 0)
                + self.layout_switch.sizeHint().width())

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._fit_controls()
        if self._back_row.isVisible():
            self._fit_back()

    def _fit_controls(self, force: bool = False) -> None:
        narrow = self.width() < self.needed_width()
        tight = narrow and self.width() < self.tools_width()
        if force or narrow != self._narrow or tight != self._tight:
            self._narrow, self._tight = narrow, tight
            self._arrange()

    def _arrange(self) -> None:
        """The controls in the header, or - on a narrow pane - in rows of
        their own under it (the switches and Rescan, then the filter)."""
        h = self.header
        extra, row = h._extra, h.layout()
        f, r, ls, sw = self.filter, self.rescan, self.layout_switch, h.switch
        had = f.hasFocus()
        for w in (f, r, ls):
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
            self._tools_row.addWidget(r)
            self._filter_lay.addWidget(f)
            self._tools.show()
        else:
            f.setMaximumWidth(self.FILTER_W)
            f.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            for w in (f, r, ls):
                extra.addWidget(w, 0, Qt.AlignmentFlag.AlignBottom)
            row.addWidget(sw, 0, Qt.AlignmentFlag.AlignBottom)
            self._tools.hide()
        f.setVisible(True)
        r.setVisible(True)
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
