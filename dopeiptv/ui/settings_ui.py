"""Building blocks of the Settings window.

The window is a sidebar of pages (with a search box over it) beside the
page on show. A page is a column of sections; a section is a heading over a
stack of rows, each a label - with an optional line of explanation under
it - and its control on the right. Sections are flat: no frame around them
and no border round a dropdown or a button inside one. Framed cards held
bordered fields, so a page read as boxes inside boxes inside boxes. Every page is built from these, so they all look the same and
line up the same, which the old row of ten tabs with ten different layouts
did not.

Nothing here knows about settings keys: mw_settings creates the controls,
reads them back on OK and only places them through these helpers.
"""

from __future__ import annotations

import math

import bisect

from PyQt6.QtCore import QLocale, QPoint, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor, QConicalGradient, QFont, QIcon, QPainter, QPainterPath, QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QAbstractButton, QAbstractSpinBox, QButtonGroup, QCheckBox, QFrame,
    QHBoxLayout, QLabel, QLayout, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QStackedWidget, QStyle,
    QStyledItemDelegate, QToolButton, QVBoxLayout, QWidget,
)

from .theme import P, THEMES, corner_radius, resolve_accent, rounded

#: Widest a page's content gets. A form stretched over a large window puts
#: the label and its control a screen apart.
PAGE_MAX_WIDTH = 760


class ToggleSwitch(QCheckBox):
    """An on/off switch. Still a QCheckBox - isChecked, setChecked and
    toggled work as before - drawn as a sliding knob, and without text:
    the row it sits in carries the label."""

    W, H = 40, 22

    def __init__(self, checked: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(self.W, self.H)

    def sizeHint(self) -> QSize:
        return QSize(self.W, self.H)

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, _event) -> None:
        pr = QPainter(self)
        pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        on = self.isChecked()
        track = QColor(P["accent"] if on else P["scroll"])
        if not self.isEnabled():
            track.setAlphaF(0.45)
        r = QRectF(0.5, 0.5, self.W - 1.0, self.H - 1.0)
        pr.setPen(Qt.PenStyle.NoPen)
        pr.setBrush(track)
        pr.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        if self.hasFocus():
            ring = QPen(QColor(P["accent_hi"]))
            ring.setWidthF(1.5)
            pr.setPen(ring)
            pr.setBrush(Qt.BrushStyle.NoBrush)
            pr.drawRoundedRect(r, r.height() / 2, r.height() / 2)
            pr.setPen(Qt.PenStyle.NoPen)
        d = self.H - 6.0
        # The knob sits at the "on" end: right, or left when mirrored.
        right = on != self.isRightToLeft()
        x = self.W - d - 3.0 if right else 3.0
        pr.setBrush(QColor("#FFFFFF"))
        pr.drawEllipse(QRectF(x, 3.0, d, d))
        pr.end()


def _muted(text: str, size: int = 12) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setObjectName("SettingsHint")
    lbl.setStyleSheet(f"font-size:{size}px;")
    return lbl


#: Space above and below the content of a row. The dividers between rows
#: are what separate them, so this only has to keep a row a comfortable
#: click target - a dropdown is about 30 px.
ROW_PAD = 8


class SettingsCard(QFrame):
    """A section of a page: its rows, with a hairline between them. No frame
    of its own - the heading over it and the space around it group it."""

    def __init__(self, page: "SettingsPage") -> None:
        super().__init__()
        self.setObjectName("SettingsCard")
        self._page = page
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        self.rows: list[tuple[QWidget, str]] = []
        self._shown_if: dict[int, object] = {}
        self._needle, self._whole = "", False

    def _add(self, w: QWidget, search: str) -> QWidget:
        if self.rows:
            line = QFrame()
            line.setObjectName("SettingsDivider")
            line.setFixedHeight(1)
            self._lay.addWidget(line)
            w.setProperty("_divider", line)
        self._lay.addWidget(w)
        self.rows.append((w, search.casefold()))
        return w

    def row(self, label: str, control=None, hint: str = "",
            stretch_control: bool = False, search: str = "") -> QWidget:
        """Label on the left (and its explanation under it), the control -
        a widget or a layout - on the right. *search* adds words the row is
        found under besides its label and hint."""
        w = QWidget()
        w.setObjectName("SettingsRow")
        h = QHBoxLayout(w)
        h.setContentsMargins(0, ROW_PAD, 0, ROW_PAD)
        h.setSpacing(16)
        text = QVBoxLayout()
        text.setSpacing(2)
        name = QLabel(label)
        name.setWordWrap(True)
        name.setObjectName("SettingsLabel")
        text.addWidget(name)
        w.label = name
        w.hint_label = None
        if hint:
            w.hint_label = _muted(hint)
            text.addWidget(w.hint_label)
        h.addLayout(text, 1)
        if isinstance(control, QLayout):
            holder = QWidget()
            control.setContentsMargins(0, 0, 0, 0)
            holder.setLayout(control)
            control = holder
        if control is not None:
            # One column of controls down the card: the same width for all,
            # unless a control has a size of its own (a switch, a number, a
            # button - stretched to the column a button was a wide slab).
            fixed = control.minimumWidth() == control.maximumWidth()
            if not (stretch_control or fixed
                    or isinstance(control, (QCheckBox, QPushButton))):
                control.setMinimumWidth(max(control.minimumWidth(), 220))
                control.setMaximumWidth(max(control.minimumWidth(), 320))
                self._page.column.append(control)
            h.addWidget(control, 1 if stretch_control else 0,
                        Qt.AlignmentFlag.AlignVCenter)
        return self._add(w, f"{label} {hint} {search}")

    def toggle(self, label: str, checked: bool, hint: str = "") -> ToggleSwitch:
        sw = ToggleSwitch(checked)
        sw.setAccessibleName(label)
        self.row(label, sw, hint)
        return sw

    def widget(self, w: QWidget, search: str = "") -> QWidget:
        """Something that takes the card's whole width: a list, a button
        row, a status line."""
        holder = QWidget()
        v = QVBoxLayout(holder)
        v.setContentsMargins(0, ROW_PAD, 0, ROW_PAD)
        v.addWidget(w)
        return self._add(holder, search)

    def layout_row(self, lay: QLayout, search: str = "") -> QWidget:
        """A full-width row of controls - buttons, mostly. Found by search
        under the texts on them."""
        holder = QWidget()
        lay.setContentsMargins(0, ROW_PAD, 0, ROW_PAD)
        holder.setLayout(lay)
        words = [search] + [w.text() for w in holder.findChildren(QWidget)
                            if hasattr(w, "text") and callable(w.text)
                            and isinstance(w.text(), str)]
        return self._add(holder, " ".join(words))

    def note(self, text: str) -> QLabel:
        lbl = _muted(text)
        holder = QWidget()
        v = QVBoxLayout(holder)
        v.setContentsMargins(0, ROW_PAD, 0, ROW_PAD)
        v.addWidget(lbl)
        self._add(holder, text)
        return lbl

    def shown_if(self, row: QWidget, predicate) -> None:
        """Show *row* only while *predicate()* holds (re-checked by
        refresh() and by every search)."""
        self._shown_if[id(row)] = predicate
        self.refresh()

    def refresh(self) -> None:
        self.apply_filter(self._needle, self._whole)

    def apply_filter(self, needle: str, whole: bool) -> bool:
        """Show the rows that match *needle* (all of them when *whole*);
        True when anything is left showing."""
        self._needle, self._whole = needle, whole
        shown = False
        first = True
        for w, text in self.rows:
            hit = whole or not needle or needle in text
            cond = self._shown_if.get(id(w))
            if cond is not None and not cond():
                hit = False
            w.setVisible(hit)
            line = w.property("_divider")
            if line is not None:
                line.setVisible(hit and not first)
            if hit:
                first = False
                shown = True
        self.setVisible(shown)
        title = self.property("_title")
        if title is not None:
            title.setVisible(shown)
        return shown


class SettingsPage(QScrollArea):
    """One page: a title, a line saying what is on it, and its cards."""

    def __init__(self, key: str, title: str, subtitle: str = "") -> None:
        super().__init__()
        self.key = key
        self.title = title
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("SettingsPageBody")
        outer = QHBoxLayout(body)
        outer.setContentsMargins(28, 22, 28, 22)
        col = QWidget()
        col.setMaximumWidth(PAGE_MAX_WIDTH)
        self._col = QVBoxLayout(col)
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(6)
        outer.addStretch(1)
        outer.addWidget(col, 100)
        outer.addStretch(1)
        head = QLabel(title)
        head.setObjectName("SettingsTitle")
        self._col.addWidget(head)
        self._subtitle = None
        if subtitle:
            self._subtitle = _muted(subtitle, 13)
            self._col.addWidget(self._subtitle)
        self._col.addSpacing(8)
        self._tail = QWidget()        # keeps the cards at the top
        self._col.addWidget(self._tail, 1)
        self.setWidget(body)
        self.cards: list[SettingsCard] = []
        #: The controls in the column down the right of the cards.
        self.column: list[QWidget] = []

    def card(self, title: str = "") -> SettingsCard:
        at = self._col.indexOf(self._tail)
        c = SettingsCard(self)
        if title:
            # A heading in the page's own type, not small capitals: with no
            # frame round the section, the heading is what groups it.
            head = QLabel(title)
            head.setObjectName("SettingsCardTitle")
            # A styled label indents its text by half an "x" unless told
            # not to, which set every heading off the rows under it.
            head.setIndent(0)
            self._col.insertWidget(at, head)
            at += 1
            c.setProperty("_title", head)
        self._col.insertWidget(at, c)
        self._col.insertSpacing(at + 1, 4)
        self.cards.append(c)
        return c

    def apply_filter(self, needle: str) -> bool:
        whole = bool(needle) and needle in self.title.casefold()
        shown = False
        for c in self.cards:
            if c.apply_filter(needle, whole):
                shown = True
        if self._subtitle is not None:
            self._subtitle.setVisible(not needle)
        return shown or not needle


class SettingsNav(QListWidget):
    """The sidebar: one entry per page, with its icon."""

    page_chosen = pyqtSignal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("SettingsNav")
        self.setIconSize(QSize(18, 18))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.currentRowChanged.connect(self.page_chosen.emit)


class SettingsShell(QWidget):
    """Search + sidebar on the left, the pages on the right."""

    def __init__(self, search_placeholder: str, no_match: str) -> None:
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        side = QFrame()
        side.setObjectName("SettingsSide")
        sv = QVBoxLayout(side)
        sv.setContentsMargins(12, 14, 12, 12)
        sv.setSpacing(10)
        self.search = QLineEdit()
        self.search.setObjectName("SettingsSearch")
        self.search.setPlaceholderText(search_placeholder)
        self.search.setClearButtonEnabled(True)
        sv.addWidget(self.search)
        self.nav = SettingsNav()
        sv.addWidget(self.nav, 1)
        side.setFixedWidth(232)
        h.addWidget(side)
        self.stack = QStackedWidget()
        self.stack.setObjectName("SettingsPages")
        h.addWidget(self.stack, 1)
        self._none = QLabel(no_match)
        self._none.setObjectName("SettingsHint")
        self._none.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(self._none)
        self.pages: list[SettingsPage] = []
        self._items: dict[str, QListWidgetItem] = {}
        self._sections: list[tuple[QListWidgetItem, list[QListWidgetItem]]] = []
        self._aligned = False
        self.nav.page_chosen.connect(self._show)
        self.search.textChanged.connect(self._filter)

    def showEvent(self, event) -> None:
        # Measured on show, not as the rows are built: a size hint is only
        # right once the style sheet has been applied.
        if not self._aligned:
            self._aligned = True
            self.align_column()
        super().showEvent(event)

    def align_column(self) -> None:
        """One width for the column of controls on every page: the widest
        control's, within the column's bounds. Each control used to take the
        width of its own text, so a long entry ("Default (provider order)")
        stuck out past the dropdowns above it. Only the minimum is set: a
        control whose text grows later widens instead of being cut off."""
        controls = [c for p in self.pages for c in p.column]
        if not controls:
            return
        for c in controls:
            c.ensurePolished()
        widest = max(c.sizeHint().width() for c in controls)
        for c in controls:
            c.setMinimumWidth(min(max(widest, c.minimumWidth()),
                                  c.maximumWidth()))

    def add_page(self, page: SettingsPage, icon: QIcon) -> SettingsPage:
        self.pages.append(page)
        self.stack.addWidget(page)
        item = QListWidgetItem(icon, page.title)
        item.setData(Qt.ItemDataRole.UserRole, page.key)
        # 32 px, not 36: with the section headings, fifteen pages at 36 no
        # longer fitted the sidebar of a window of the default height.
        item.setSizeHint(QSize(0, 32))
        self.nav.addItem(item)
        self._items[page.key] = item
        return page

    def arrange(self, sections: list[tuple[str, list[str]]]) -> None:
        """Put the sidebar in this order, under these headings: (heading,
        [page keys]). The pages are built in whatever order their code needs;
        this is the order a person reads them in. A page left out keeps its
        place at the end, so a new page can never go missing."""
        self.nav.blockSignals(True)
        while self.nav.count():
            self.nav.takeItem(0)
        self._sections = []
        placed: set[str] = set()
        for heading, keys in sections:
            items = [self._items[k] for k in keys if k in self._items]
            if not items:
                continue
            head = QListWidgetItem(QLocale().toUpper(heading))
            head.setFlags(Qt.ItemFlag.NoItemFlags)
            head.setSizeHint(QSize(0, 24 if self._sections else 18))
            f = head.font()
            f.setPointSizeF(max(7.0, f.pointSizeF() * 0.8))
            f.setBold(True)
            head.setFont(f)
            head.setForeground(QColor(P["muted"]))
            head.setTextAlignment(Qt.AlignmentFlag.AlignLeft
                                  | Qt.AlignmentFlag.AlignBottom)
            self.nav.addItem(head)
            for it in items:
                self.nav.addItem(it)
            self._sections.append((head, items))
            placed.update(keys)
        for p in self.pages:
            if p.key not in placed:
                self.nav.addItem(self._items[p.key])
        self.nav.blockSignals(False)

    def _page_at(self, row: int) -> SettingsPage | None:
        item = self.nav.item(row)
        key = item.data(Qt.ItemDataRole.UserRole) if item else None
        return next((p for p in self.pages if p.key == key), None)

    def _show(self, row: int) -> None:
        page = self._page_at(row)
        if page is not None:
            self.stack.setCurrentWidget(page)

    def show_page(self, key: str | None) -> None:
        item = self._items.get(key or "")
        if item is not None and not item.isHidden():
            self.nav.setCurrentItem(item)
            return
        for i in range(self.nav.count()):
            if self._page_at(i) is not None and not self.nav.item(i).isHidden():
                self.nav.setCurrentRow(i)
                return

    def current_key(self) -> str | None:
        page = self._page_at(self.nav.currentRow())
        return page.key if page is not None else None

    def _filter(self, text: str) -> None:
        needle = " ".join(text.split()).casefold()
        first = None
        for page in self.pages:
            hit = page.apply_filter(needle)
            self._items[page.key].setHidden(not hit)
        # A heading goes with the last of its pages.
        for head, items in self._sections:
            head.setHidden(all(it.isHidden() for it in items))
        for i in range(self.nav.count()):
            if self._page_at(i) is not None and not self.nav.item(i).isHidden():
                first = i
                break
        if first is None:
            self.nav.setCurrentRow(-1)
            self.stack.setCurrentWidget(self._none)
            return
        row = self.nav.currentRow()
        if row < 0 or self.nav.item(row).isHidden():
            self.nav.setCurrentRow(first)
        else:
            self._show(row)


def settings_style() -> str:
    """The Settings window's own rules, on top of the app theme."""
    p = P
    edge = (p['border_in'] if p.get("name") == THEMES["contrast"]["name"]
            else "transparent")
    return rounded(f"""
#SettingsSide {{ background: {p['side']}; border-right: 1px solid {p['border']}; }}
QListWidget#SettingsNav {{ background: transparent; border: none; outline: none; }}
QListWidget#SettingsNav::item {{
    border-radius: 8px; padding: 0 10px; margin: 1px 0; color: {p['text2']};
}}
QListWidget#SettingsNav::item:hover {{ background: {p['hover']}; }}
QListWidget#SettingsNav QScrollBar:vertical {{
    width: 6px; background: transparent; margin: 0;
}}
QListWidget#SettingsNav QScrollBar::handle:vertical {{
    background: {p['border']}; border-radius: 3px; min-height: 30px;
}}
QListWidget#SettingsNav QScrollBar::add-line, QListWidget#SettingsNav QScrollBar::sub-line {{
    height: 0;
}}
QListWidget#SettingsNav::item:disabled,
QListWidget#SettingsNav::item:disabled:hover {{
    background: transparent; color: {p['muted']}; padding: 0 10px 2px 10px;
}}
QListWidget#SettingsNav::item:selected {{
    background: {p['accent']}; color: {p.get('on_accent', '#FFFFFF')};
}}
QLineEdit#SettingsSearch {{ padding: 7px 10px; border-radius: 9px; }}
#SettingsPageBody {{ background: {p['bg']}; }}
QLabel#SettingsTitle {{ font-size: 22px; font-weight: 700; color: {p['text']}; }}
QLabel#SettingsCardTitle {{
    color: {p['text']}; font-size: 15px; font-weight: 600;
    padding: 18px 0 2px 0;
}}
QFrame#SettingsCard {{ background: transparent; border: none; }}
QFrame#SettingsDivider {{ background: {p['border']}; border: none; }}
/* Controls on a page are filled, not outlined: the fill says "this is a
   control", and a border on top of it was one box too many. High contrast
   keeps its borders - there the outline is the point. */
#SettingsPageBody QComboBox, #SettingsPageBody QLineEdit,
#SettingsPageBody QSpinBox {{
    background: {p['btn']}; border: 1px solid {edge}; border-radius: 8px;
}}
#SettingsPageBody QComboBox:hover, #SettingsPageBody QLineEdit:hover,
#SettingsPageBody QSpinBox:hover {{ background: {p['btn_hover']}; }}
#SettingsPageBody QComboBox:focus, #SettingsPageBody QLineEdit:focus,
#SettingsPageBody QSpinBox:focus {{ border: 1px solid {p['accent']}; }}
#SettingsPageBody QComboBox:disabled, #SettingsPageBody QLineEdit:disabled {{
    color: {p['muted2']}; background: {p['hover']};
}}
#SettingsPageBody QPushButton {{
    background: {p['btn']}; border: 1px solid {edge};
    padding: 7px 14px;
}}
#SettingsPageBody QPushButton:hover {{ background: {p['btn_hover']}; }}
#SettingsPageBody QPushButton:disabled {{ color: {p['muted2']}; }}
#SettingsPageBody QPushButton#Primary {{
    background: {p['accent']}; border-color: {p['accent']};
    color: {p.get('on_accent', '#FFFFFF')};
}}
#SettingsPageBody QPushButton#Primary:hover {{ background: {p['accent_hi']}; }}
#SettingsPageBody QPushButton#Danger {{ color: {p['error']}; }}
#SettingsPageBody QPushButton#Danger:hover {{ border-color: {p['error']}; }}
QFrame#Stepper {{
    background: {p['btn']}; border: 1px solid {edge}; border-radius: 8px;
}}
QFrame#Stepper QSpinBox, QFrame#Stepper QSpinBox:hover,
QFrame#Stepper QSpinBox:focus {{
    background: transparent; border: none; padding: 0;
    min-height: 22px; font-size: 13px; color: {p['text']};
}}
/* The app's spin box rule pins 20 px arrow buttons to the right edge, and
   without buttons the text field still measured itself up to them: one
   pixel wide. Parked at the left at no size, they leave it the width. */
QFrame#Stepper QSpinBox::up-button, QFrame#Stepper QSpinBox::down-button {{
    subcontrol-origin: margin; subcontrol-position: center left;
    width: 0px; height: 0px; border: none; image: none;
}}
QFrame#Stepper QToolButton {{
    background: transparent; border: none; border-radius: 6px;
    color: {p['text2']}; font-size: 16px; font-weight: 600;
}}
QFrame#Stepper QToolButton:hover {{ background: {p['btn_hover']}; color: {p['text']}; }}
QFrame#Stepper QToolButton:disabled {{ color: {p['muted3']}; }}
QLabel#SettingsGroupHead {{ color: {p['muted']}; font-size: 12px; }}
#SettingsRow, QFrame#SettingsCard > QWidget {{ background: transparent; }}
QLabel#SettingsLabel {{ font-size: 13px; color: {p['text']}; }}
QLabel#SettingsHint {{ color: {p['muted']}; }}
#SettingsFooter {{ background: {p['bg']}; border-top: 1px solid {p['border']}; }}
""") + _chip_style()


def _chip_style() -> str:
    """A chip is a pill: its radius half its height, whatever the corner
    style. Left to rounded(), "round" scaled it past half the height, and
    Qt then draws no rounding at all - a square chip."""
    p = P
    radius = min(13, round(corner_radius(12)))
    tint = QColor(p["accent"])
    tint.setAlphaF(0.24)
    tint_hi = QColor(p["accent"])
    tint_hi.setAlphaF(0.34)

    def rgba(c: QColor) -> str:
        return f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alphaF():.2f})"

    # On: a wash of the accent and a tick, like a filter chip. A solid
    # accent fill on eleven chips at once shouted.
    return f"""
#SettingsPageBody QPushButton#Chip {{
    border: 1px solid {p['border_in']}; border-radius: {radius}px;
    min-height: 18px; padding: 4px 12px 4px 9px; background: transparent;
    color: {p['muted']}; font-weight: 500;
}}
#SettingsPageBody QPushButton#Chip:hover {{
    border-color: {p['muted3']}; color: {p['text2']};
}}
#SettingsPageBody QPushButton#Chip:checked {{
    background: {rgba(tint)}; border-color: transparent; color: {p['text']};
}}
#SettingsPageBody QPushButton#Chip:checked:hover {{ background: {rgba(tint_hi)}; }}
#SettingsPageBody QPushButton#Chip:focus {{ border-color: {p['accent_hi']}; }}
"""


# -- playlist rows -----------------------------------------------------------

class PlaylistDelegate(QStyledItemDelegate):
    """A playlist as a two-line row: its name (and whether it is the one in
    use) over the address it comes from, with a grip to drag it by."""

    ROLE = Qt.ItemDataRole.UserRole + 1
    H = 54

    def __init__(self, active_text: str, kinds: dict[str, str],
                 parent=None) -> None:
        super().__init__(parent)
        self._active = active_text
        self._kinds = kinds

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), self.H)

    def paint(self, pr: QPainter, option, index) -> None:
        info = index.data(self.ROLE) or {}
        pr.save()
        pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(option.rect).adjusted(2, 2, -2, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hover:
            pr.setPen(Qt.PenStyle.NoPen)
            pr.setBrush(QColor(P["sel"] if selected else P["hover"]))
            pr.drawRoundedRect(r, 8, 8)
        rtl = option.direction == Qt.LayoutDirection.RightToLeft
        # The grip on the leading edge: two columns of three dots.
        pr.setPen(Qt.PenStyle.NoPen)
        pr.setBrush(QColor(P["muted3"]))
        gx = r.right() - 19 if rtl else r.left() + 12
        cy = r.center().y()
        for dx in (0.0, 5.0):
            for dy in (-5.0, 0.0, 5.0):
                pr.drawEllipse(QPointF(gx + dx, cy + dy), 1.6, 1.6)
        lead = 32.0
        left = r.left() + (12.0 if rtl else lead)
        right = r.right() - (lead if rtl else 12.0)
        align = (Qt.AlignmentFlag.AlignRight if rtl
                 else Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignVCenter
        base = QFont(option.font)
        small = _scaled(base, 0.85)
        bold = QFont(base)
        bold.setBold(True)
        # Badges at the trailing end: "in use" outermost, then the kind.
        badges = []
        if info.get("active"):
            badges.append((self._active, True))
        kind = self._kinds.get(info.get("kind") or "xtream", "")
        if kind:
            badges.append((kind, False))
        pr.setFont(small)
        fm = pr.fontMetrics()
        edge = left if rtl else right
        for text, active in badges:
            w = fm.horizontalAdvance(text) + 14.0
            box = (QRectF(edge, r.top() + 9, w, 18) if rtl
                   else QRectF(edge - w, r.top() + 9, w, 18))
            edge = edge + w + 6 if rtl else edge - w - 6
            pr.setPen(Qt.PenStyle.NoPen)
            pr.setBrush(QColor(P["accent"] if active else P["btn"]))
            pr.drawRoundedRect(box, 9, 9)
            pr.setPen(QColor("#FFFFFF" if active else P["text2"]))
            pr.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        name_box = (QRectF(edge, r.top() + 6, max(10.0, right - edge), 22)
                    if rtl else
                    QRectF(left, r.top() + 6, max(10.0, edge - left), 22))
        pr.setFont(bold)
        pr.setPen(QColor(P["text"]))
        pr.drawText(name_box, align, pr.fontMetrics().elidedText(
            str(info.get("name") or ""), Qt.TextElideMode.ElideRight,
            int(name_box.width())))
        pr.setFont(base)
        pr.setPen(QColor(P["muted"]))
        line = QRectF(left, r.top() + 28, right - left, 20)
        pr.drawText(line, align, pr.fontMetrics().elidedText(
            str(info.get("server") or ""), Qt.TextElideMode.ElideMiddle,
            int(line.width())))
        pr.restore()


def _scaled(font: QFont, factor: float) -> QFont:
    """*font* at *factor* of its size. The theme sets sizes in pixels, and
    then pointSizeF() is -1 - scaling that gave a one-point font."""
    out = QFont(font)
    if font.pixelSize() > 0:
        out.setPixelSize(max(1, round(font.pixelSize() * factor)))
    elif font.pointSizeF() > 0:
        out.setPointSizeF(font.pointSizeF() * factor)
    return out


# -- icons -------------------------------------------------------------------

def page_icon(kind: str, base, size: int = 18) -> QIcon:
    """The sidebar icon for a page: muted at rest, in the accent's text
    colour when selected.
    *base* draws the kinds the main window already has (gear, home, ...);
    the rest are drawn here."""
    icon = QIcon()
    for mode, colour in ((QIcon.Mode.Normal, P["text2"]),
                         (QIcon.Mode.Selected, P.get("on_accent", "#FFFFFF"))):
        pm = _own_icon(kind, size, colour)
        if pm is None:
            pm = base(kind, size, colour)
        icon.addPixmap(pm, mode)
    return icon


def _own_icon(kind: str, s: int, colour: str) -> QPixmap | None:
    if kind not in ("palette", "play", "subtitles", "picture", "lock",
                    "wrench", "image", "trakt"):
        return None
    scale = 3
    pm = QPixmap(s * scale, s * scale)
    pm.setDevicePixelRatio(float(scale))
    pm.fill(Qt.GlobalColor.transparent)
    pr = QPainter(pm)
    pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    col = QColor(colour)
    pen = QPen(col)
    pen.setWidthF(max(1.3, s * 0.085))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pr.setPen(pen)
    pr.setBrush(Qt.BrushStyle.NoBrush)
    f = float(s)
    if kind == "palette":
        path = QPainterPath()
        path.addEllipse(QRectF(f * 0.12, f * 0.12, f * 0.76, f * 0.76))
        pr.drawPath(path)
        pr.setPen(Qt.PenStyle.NoPen)
        pr.setBrush(col)
        for a in (200, 255, 310, 10):
            rad = math.radians(a)
            pr.drawEllipse(QPointF(f * 0.5 + math.cos(rad) * f * 0.22,
                                   f * 0.5 + math.sin(rad) * f * 0.22),
                           f * 0.06, f * 0.06)
    elif kind == "play":
        pr.drawEllipse(QRectF(f * 0.1, f * 0.1, f * 0.8, f * 0.8))
        pr.setPen(Qt.PenStyle.NoPen)
        pr.setBrush(col)
        tri = QPainterPath()
        tri.moveTo(f * 0.42, f * 0.33)
        tri.lineTo(f * 0.68, f * 0.5)
        tri.lineTo(f * 0.42, f * 0.67)
        tri.closeSubpath()
        pr.drawPath(tri)
    elif kind == "subtitles":
        pr.drawRoundedRect(QRectF(f * 0.1, f * 0.22, f * 0.8, f * 0.56),
                           f * 0.1, f * 0.1)
        pr.drawLine(QPointF(f * 0.24, f * 0.43), QPointF(f * 0.52, f * 0.43))
        pr.drawLine(QPointF(f * 0.6, f * 0.43), QPointF(f * 0.76, f * 0.43))
        pr.drawLine(QPointF(f * 0.24, f * 0.6), QPointF(f * 0.4, f * 0.6))
        pr.drawLine(QPointF(f * 0.48, f * 0.6), QPointF(f * 0.76, f * 0.6))
    elif kind == "picture":
        pr.drawRoundedRect(QRectF(f * 0.1, f * 0.18, f * 0.8, f * 0.54),
                           f * 0.08, f * 0.08)
        pr.drawLine(QPointF(f * 0.34, f * 0.86), QPointF(f * 0.66, f * 0.86))
        pr.drawLine(QPointF(f * 0.5, f * 0.72), QPointF(f * 0.5, f * 0.86))
    elif kind == "image":
        pr.drawRoundedRect(QRectF(f * 0.12, f * 0.16, f * 0.76, f * 0.68),
                           f * 0.08, f * 0.08)
        hill = QPainterPath()
        hill.moveTo(f * 0.16, f * 0.74)
        hill.lineTo(f * 0.4, f * 0.5)
        hill.lineTo(f * 0.56, f * 0.64)
        hill.lineTo(f * 0.66, f * 0.56)
        hill.lineTo(f * 0.84, f * 0.74)
        pr.drawPath(hill)
        pr.drawEllipse(QPointF(f * 0.66, f * 0.34), f * 0.06, f * 0.06)
    elif kind == "lock":
        pr.drawRoundedRect(QRectF(f * 0.2, f * 0.44, f * 0.6, f * 0.44),
                           f * 0.08, f * 0.08)
        arc = QPainterPath()
        arc.moveTo(f * 0.32, f * 0.44)
        arc.lineTo(f * 0.32, f * 0.32)
        arc.arcTo(QRectF(f * 0.32, f * 0.12, f * 0.36, f * 0.36), 180, -180)
        arc.lineTo(f * 0.68, f * 0.44)
        pr.drawPath(arc)
    elif kind == "trakt":
        pr.drawEllipse(QRectF(f * 0.1, f * 0.1, f * 0.8, f * 0.8))
        tick = QPainterPath()
        tick.moveTo(f * 0.32, f * 0.52)
        tick.lineTo(f * 0.45, f * 0.65)
        tick.lineTo(f * 0.7, f * 0.38)
        pr.drawPath(tick)
    else:                                   # "wrench": three sliders
        for y, x in ((0.28, 0.62), (0.5, 0.36), (0.72, 0.56)):
            pr.setPen(pen)
            pr.drawLine(QPointF(f * 0.14, f * y), QPointF(f * 0.86, f * y))
            pr.setPen(Qt.PenStyle.NoPen)
            pr.setBrush(col)
            pr.drawEllipse(QPointF(f * x, f * y), f * 0.11, f * 0.11)
            pr.setBrush(Qt.BrushStyle.NoBrush)
    pr.end()
    return pm


def hbox(*widgets, stretch: bool = True, spacing: int = 8) -> QHBoxLayout:
    """A row of buttons (or anything), packed to the start. A None among
    them is a gap that pushes what follows to the far end."""
    h = QHBoxLayout()
    h.setSpacing(spacing)
    for w in widgets:
        if w is None:
            h.addStretch(1)
            stretch = False
        else:
            h.addWidget(w)
    if stretch:
        h.addStretch(1)
    return h


class ReorderList(QListWidget):
    """A list whose rows are dragged into a new order; says so once the
    drop has landed."""

    reordered = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def dropEvent(self, event) -> None:
        super().dropEvent(event)
        self.reordered.emit()


def fit_to_rows(lst: QListWidget, most: int = 6) -> None:
    """As tall as its rows, up to *most* (then it scrolls). A list left at
    Qt's default height showed one folder above an empty well."""
    def fit() -> None:
        lst.ensurePolished()
        row = lst.sizeHintForRow(0) if lst.count() else -1
        if row <= 0:
            row = lst.fontMetrics().height() + 18
        rows = max(1, min(lst.count(), most))
        lst.setFixedHeight(rows * (row + 2 * lst.spacing())
                           + 2 * lst.frameWidth() + 4)

    lst.model().rowsInserted.connect(lambda *_a: fit())
    lst.model().rowsRemoved.connect(lambda *_a: fit())
    fit()


def expanding(w: QWidget) -> QWidget:
    w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return w


class FlowLayout(QLayout):
    """Lays its items out in rows, wrapping to the next row when one is
    full - so a set of tiles fits a narrow window as well as a wide one."""

    def __init__(self, parent=None, spacing: int = 8,
                 fill: bool = False) -> None:
        super().__init__(parent)
        self._items: list = []
        self._gap = spacing
        # Equal tiles in columns that share out the whole width: a grid
        # packed to the left left an empty band down the right.
        self._fill = fill
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._place(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect) -> None:
        super().setGeometry(rect)
        self._place(rect, apply=True)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _place(self, rect, apply: bool) -> int:
        if self._fill and self._items:
            return self._place_grid(rect, apply)
        x, y, line = rect.x(), rect.y(), 0
        rtl = self.parentWidget() is not None and \
            self.parentWidget().isRightToLeft()
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and line > 0:
                x, y, line = rect.x(), y + line + self._gap, 0
            if apply:
                left = (rect.right() + 1 - (x - rect.x()) - hint.width()
                        if rtl else x)
                item.setGeometry(QRect(QPoint(left, y), hint))
            x += hint.width() + self._gap
            line = max(line, hint.height())
        return y + line - rect.y()

    def _place_grid(self, rect, apply: bool) -> int:
        """As many columns as tiles of their own width fit, then the columns
        widened to share the row out - so the grid ends flush with the page
        edge on both sides, and two grids of the same tiles line up."""
        w = max(item.sizeHint().width() for item in self._items)
        h = max(item.sizeHint().height() for item in self._items)
        cols = max(1, (rect.width() + self._gap) // (w + self._gap))
        cell = max(w, (rect.width() - (cols - 1) * self._gap) // cols)
        rtl = self.parentWidget() is not None and \
            self.parentWidget().isRightToLeft()
        rows = 0
        for i, item in enumerate(self._items):
            r, c = divmod(i, cols)
            if rtl:
                c = cols - 1 - c
            rows = r + 1
            if apply:
                item.setGeometry(QRect(rect.x() + c * (cell + self._gap),
                                       rect.y() + r * (h + self._gap),
                                       cell, h))
        return rows * h + (rows - 1) * self._gap


class _Choice(QAbstractButton):
    """A checkable tile of a ChoicePicker, drawn by the subclass."""

    def __init__(self, key: str, name: str) -> None:
        super().__init__()
        self.key = key
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(name)
        self.setAccessibleName(name)
        self.setText(name)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def sizeHint(self) -> QSize:
        return self.minimumSizeHint()

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)


class ThemeTile(_Choice):
    """A small picture of the app in one theme - sidebar, a list with its
    selected row, the accent - with the theme's name under it."""

    W, H, PREVIEW_H = 118, 92, 64

    def __init__(self, key: str, name: str) -> None:
        super().__init__(key, name)
        self.accent = ("blue", "")

    def minimumSizeHint(self) -> QSize:
        return QSize(self.W, self.H)

    def paintEvent(self, _event) -> None:
        t = THEMES[self.key]
        acc = QColor(resolve_accent(self.key, *self.accent)[0])
        pr = QPainter(self)
        pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(3, 3, self.width() - 6, self.PREVIEW_H - 6)
        rad = corner_radius(8)
        clip = QPainterPath()
        clip.addRoundedRect(r, rad, rad)
        pr.save()
        pr.setClipPath(clip)
        pr.fillRect(r, QColor(t["bg"]))
        side = QRectF(r.x(), r.y(), r.width() * 0.27, r.height())
        pr.fillRect(side, QColor(t["side"]))
        pr.setPen(Qt.PenStyle.NoPen)
        for i in range(4):
            bar = QRectF(side.x() + 5, side.y() + 7 + i * 10,
                         side.width() - 12 if i else side.width() - 9, 4)
            pr.setBrush(acc if i == 1 else QColor(t["muted3"]))
            pr.drawRoundedRect(bar, 2, 2)
        x0 = side.right() + 7
        w = r.right() - x0 - 7
        pr.setBrush(QColor(t["text"]))
        pr.drawRoundedRect(QRectF(x0, r.y() + 7, w * 0.55, 5), 2, 2)
        for i in range(3):
            row = QRectF(x0 - 3, r.y() + 17 + i * 12, w + 6, 10)
            if i == 1:
                pr.setBrush(QColor(t["sel"]))
                pr.drawRoundedRect(row, 2, 2)
                pr.setBrush(acc)
                pr.drawRoundedRect(QRectF(row.x(), row.y(), 3, row.height()),
                                   1, 1)
            pr.setBrush(QColor(t["text2"] if i == 1 else t["muted"]))
            pr.drawRoundedRect(QRectF(x0 + 2, row.y() + 3,
                                      w * (0.7 - 0.12 * i), 4), 2, 2)
        pr.restore()
        on = self.isChecked()
        ring = QColor(P["accent"] if on else
                      (P["muted3"] if self.underMouse() else t["border_in"]))
        pen = QPen(ring)
        pen.setWidthF(2.0 if on or self.underMouse() else 1.0)
        pr.setPen(pen)
        pr.setBrush(Qt.BrushStyle.NoBrush)
        pr.drawRoundedRect(r, rad, rad)
        if self.hasFocus():
            focus = QPen(QColor(P["accent_hi"]))
            focus.setWidthF(1.0)
            focus.setStyle(Qt.PenStyle.DashLine)
            pr.setPen(focus)
            pr.drawRoundedRect(r.adjusted(-2.5, -2.5, 2.5, 2.5),
                               rad + 2, rad + 2)
        if on:
            badge = QRectF(r.right() - 16, r.y() + 4, 13, 13)
            pr.setPen(Qt.PenStyle.NoPen)
            pr.setBrush(QColor(P["accent"]))
            pr.drawEllipse(badge)
            tick = QPen(QColor(P.get("on_accent", "#FFFFFF")))
            tick.setWidthF(1.6)
            pr.setPen(tick)
            pr.drawPolyline([QPointF(badge.x() + 3.4, badge.y() + 6.8),
                             QPointF(badge.x() + 5.6, badge.y() + 9.0),
                             QPointF(badge.x() + 9.8, badge.y() + 4.2)])
        f = QFont(self.font())
        f.setPixelSize(11)
        f.setWeight(QFont.Weight.DemiBold if on else QFont.Weight.Normal)
        pr.setFont(f)
        pr.setPen(QColor(P["text"] if on else P["text2"]))
        label = QRectF(0, self.PREVIEW_H, self.width(),
                       self.H - self.PREVIEW_H)
        text = pr.fontMetrics().elidedText(self.text(),
                                           Qt.TextElideMode.ElideRight,
                                           self.width() - 4)
        pr.drawText(label, Qt.AlignmentFlag.AlignHCenter
                    | Qt.AlignmentFlag.AlignTop, text)
        pr.end()


class ColorDot(_Choice):
    """A round swatch: one accent colour, the theme's own (half the theme's
    background, half its accent) or a colour of the user's own (a colour
    wheel, around the chosen colour once there is one)."""

    D = 30

    def __init__(self, key: str, name: str, color: str = "") -> None:
        super().__init__(key, name)
        self.color = color
        self.theme = "graphite"

    def minimumSizeHint(self) -> QSize:
        return QSize(self.D, self.D)

    def paintEvent(self, _event) -> None:
        from .theme import ACCENT_CUSTOM, ACCENT_THEME
        pr = QPainter(self)
        pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        outer = QRectF(1.5, 1.5, self.D - 3, self.D - 3)
        inner = outer.adjusted(4, 4, -4, -4)
        pr.setPen(Qt.PenStyle.NoPen)
        if self.key == ACCENT_THEME:
            t = THEMES.get(self.theme, THEMES["graphite"])
            pr.setBrush(QColor(t["bg"]))
            pr.drawEllipse(inner)
            pr.setBrush(QColor(resolve_accent(self.theme, ACCENT_THEME)[0]))
            pr.drawPie(inner, 90 * 16, -180 * 16)
            pen = QPen(QColor(t["border_in"]))
            pen.setWidthF(1.0)
            pr.setPen(pen)
            pr.setBrush(Qt.BrushStyle.NoBrush)
            pr.drawEllipse(inner)
        elif self.key == ACCENT_CUSTOM:
            wheel = QConicalGradient(inner.center(), 90)
            for i, hue in enumerate((0, 60, 120, 180, 240, 300, 360)):
                wheel.setColorAt(i / 6, QColor.fromHsv(hue % 360, 200, 255))
            pr.setBrush(wheel)
            pr.drawEllipse(inner)
            if self.color:
                pr.setBrush(QColor(self.color))
                pr.drawEllipse(inner.adjusted(4, 4, -4, -4))
        else:
            pr.setBrush(QColor(self.color))
            pr.drawEllipse(inner)
        if self.isChecked() or self.underMouse() or self.hasFocus():
            pen = QPen(QColor(P["text"] if self.isChecked() else P["muted3"]))
            pen.setWidthF(2.0 if self.isChecked() else 1.2)
            if self.hasFocus() and not self.isChecked():
                pen.setColor(QColor(P["accent_hi"]))
            pr.setPen(pen)
            pr.setBrush(Qt.BrushStyle.NoBrush)
            pr.drawEllipse(outer)
        pr.end()


class _Flow(QWidget):
    """A FlowLayout's widget: as tall as its rows at the width it gets."""

    def __init__(self, spacing: int, fill: bool) -> None:
        super().__init__()
        self.flow = FlowLayout(self, spacing, fill)
        sp = QSizePolicy(QSizePolicy.Policy.Preferred,
                         QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self.flow.heightForWidth(width)


def _group_head(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("SettingsGroupHead")
    return lbl


class ChoicePicker(QWidget):
    """Tiles of which one is chosen, wrapping over as many rows as the
    width needs. ``picked`` says which, by key. With *groups* - (heading,
    [keys]) - the tiles stand under small headings, still one choice among
    all of them; *fill* makes equal tiles share out the whole width."""

    picked = pyqtSignal(str)

    def __init__(self, tiles: list, current: str, spacing: int = 10,
                 groups: list[tuple[str, list[str]]] | None = None,
                 fill: bool = False) -> None:
        super().__init__()
        self.tiles = tiles
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        if groups:
            by_key = {t.key: t for t in tiles}
            v = QVBoxLayout(self)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(6)
            placed: set[str] = set()
            sets = [(head, [by_key[k] for k in keys if k in by_key])
                    for head, keys in groups]
            sets.append(("", [t for t in tiles if t.key not in
                              {k for _h, keys in groups for k in keys}]))
            for head, members in sets:
                if not members:
                    continue
                if placed:
                    v.addSpacing(8)
                if head:
                    v.addWidget(_group_head(head))
                box = _Flow(spacing, fill)
                for tile in members:
                    box.flow.addWidget(tile)
                    placed.add(tile.key)
                v.addWidget(box)
        else:
            lay = FlowLayout(self, spacing, fill)
            for tile in tiles:
                lay.addWidget(tile)
        for tile in tiles:
            self._group.addButton(tile)
            tile.clicked.connect(lambda _c=False, k=tile.key: self.picked.emit(k))
        self.set_value(current)
        sp = QSizePolicy(QSizePolicy.Policy.Preferred,
                         QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)

    def value(self) -> str:
        tile = self._group.checkedButton()
        return tile.key if tile is not None else ""

    def set_value(self, key: str) -> None:
        for tile in self.tiles:
            if tile.key == key:
                tile.setChecked(True)
                return
        if self.tiles:
            self.tiles[0].setChecked(True)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self.layout().heightForWidth(width)

    def refresh(self) -> None:
        for tile in self.tiles:
            tile.update()


# -- chips -------------------------------------------------------------------

class Chip(QPushButton):
    """An on/off choice as a small pill: filled in the accent with a tick
    when on, an outline with an empty ring when off. Still a checkable
    button - isChecked and toggled work as on a switch - so a dozen of them
    sit in two lines where as many switches made a column of rows."""

    def __init__(self, text: str, checked: bool) -> None:
        super().__init__(text)
        self.setObjectName("Chip")
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(text)
        self.toggled.connect(self._mark)
        self._mark(checked)

    def _mark(self, on: bool) -> None:
        s, scale = 14, 3
        pm = QPixmap(s * scale, s * scale)
        pm.setDevicePixelRatio(float(scale))
        pm.fill(Qt.GlobalColor.transparent)
        pr = QPainter(pm)
        pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(P["text"] if on else P["muted"]))
        pen.setWidthF(1.8 if on else 1.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pr.setPen(pen)
        if on:
            tick = QPainterPath()
            tick.moveTo(s * 0.2, s * 0.52)
            tick.lineTo(s * 0.42, s * 0.74)
            tick.lineTo(s * 0.82, s * 0.28)
            pr.drawPath(tick)
        else:
            pr.drawEllipse(QRectF(s * 0.18, s * 0.18, s * 0.64, s * 0.64))
        pr.end()
        self.setIcon(QIcon(pm))
        self.setIconSize(QSize(s, s))

    def refresh(self) -> None:
        """Redraw the mark in the palette now in use (after a theme change)."""
        self._mark(self.isChecked())


class ChipGroups(QWidget):
    """Chips under small headings, each group wrapping to the width."""

    def __init__(self, groups: list[tuple[str, list[Chip]]]) -> None:
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        self.chips: list[Chip] = []
        for i, (heading, chips) in enumerate(groups):
            if i:
                v.addSpacing(6)
            v.addWidget(_group_head(heading))
            box = _Flow(8, False)
            for chip in chips:
                box.flow.addWidget(chip)
                self.chips.append(chip)
            v.addWidget(box)
        sp = QSizePolicy(QSizePolicy.Policy.Preferred,
                         QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self.layout().heightForWidth(width)


# -- steppers ----------------------------------------------------------------

class Stepper(QFrame):
    """A number between a minus and a plus, in one filled pill. The spin
    box's own arrows were two 8-pixel targets in a column at its edge, and
    a box beside a box when two numbers went together.

    The value is the spin box's (``value``/``setValue``/``valueChanged``);
    holding a button repeats, and the arrow keys and the wheel still step."""

    def __init__(self, spin: QSpinBox | None = None, lo: int = 0,
                 hi: int = 99, value: int = 0, width: int = 120) -> None:
        super().__init__()
        self.setObjectName("Stepper")
        self.spin = spin if spin is not None else QSpinBox()
        if spin is None:
            self.spin.setRange(lo, hi)
            self.spin.setValue(value)
        self.spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.spin.setFrame(False)
        self.valueChanged = self.spin.valueChanged
        h = QHBoxLayout(self)
        h.setContentsMargins(2, 2, 2, 2)
        h.setSpacing(0)
        self.minus = self._button("\u2212", -1)
        self.plus = self._button("+", 1)
        h.addWidget(self.minus)
        h.addWidget(self.spin, 1)
        h.addWidget(self.plus)
        self.setFixedWidth(width)
        self.spin.valueChanged.connect(self._ends)
        self._ends()

    def _button(self, text: str, step: int) -> QToolButton:
        b = QToolButton()
        b.setText(text)
        b.setFixedSize(28, 26)
        b.setAutoRepeat(True)
        b.setAutoRepeatDelay(400)
        b.setAutoRepeatInterval(70)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.clicked.connect(lambda _c=False: self.spin.stepBy(step))
        return b

    def _ends(self, *_a) -> None:
        v = self.spin.value()
        self.minus.setEnabled(v > self.spin.minimum())
        self.plus.setEnabled(v < self.spin.maximum())

    def value(self) -> int:
        return self.spin.value()

    def setValue(self, value: int) -> None:
        self.spin.setValue(value)

    def setAccessibleName(self, name: str) -> None:
        super().setAccessibleName(name)
        self.spin.setAccessibleName(name)


def _offset_grid(most: int) -> list[int]:
    # Five minutes at a time up to an hour, then quarters of an hour.
    up = list(range(5, 60, 5)) + list(range(60, most + 1, 15))
    return [-v for v in reversed(up)] + [0] + up


class DurationSpin(QSpinBox):
    """Minutes, signed, shown in words: "1 h 30 min later", "45 min
    earlier", or the word for none at 0. One control where a sign dropdown,
    an hours box and a minutes box made three.

    Steps go five minutes at a time under an hour and fifteen above it; a
    stored value off that grid (the old boxes took any minute) is kept
    until it is stepped, and the first step lands on the grid."""

    def __init__(self, later: str, earlier: str, none: str, hours: str,
                 minutes: str, most: int = 24 * 60 - 1) -> None:
        super().__init__()
        self._later, self._earlier, self._none = later, earlier, none
        self._hours, self._minutes = hours, minutes
        self._grid = _offset_grid(most - most % 15)
        self.setRange(-most, most)
        # Not typed: the words around the number made typing a guessing game
        # (which sign, which unit). The buttons, arrow keys and wheel step.
        self.lineEdit().setReadOnly(True)

    def textFromValue(self, value: int) -> str:
        if value == 0:
            return self._none
        h, m = divmod(abs(value), 60)
        parts = []
        if h:
            parts.append(self._hours.format(n=h))
        if m:
            parts.append(self._minutes.format(n=m))
        return (self._later if value > 0 else self._earlier).format(
            time=" ".join(parts))

    def valueFromText(self, text: str) -> int:
        return self.value()

    def validate(self, text: str, pos: int):
        from PyQt6.QtGui import QValidator
        return QValidator.State.Acceptable, text, pos

    def stepBy(self, steps: int) -> None:
        v = self.value()
        grid = self._grid
        for _ in range(abs(steps)):
            if steps > 0:
                i = bisect.bisect_right(grid, v)
                v = grid[i] if i < len(grid) else v
            else:
                i = bisect.bisect_left(grid, v) - 1
                v = grid[i] if i >= 0 else v
        self.setValue(v)

    def stepEnabled(self):
        flags = QAbstractSpinBox.StepEnabledFlag.StepNone
        if self.value() > self._grid[0]:
            flags |= QAbstractSpinBox.StepEnabledFlag.StepDownEnabled
        if self.value() < self._grid[-1]:
            flags |= QAbstractSpinBox.StepEnabledFlag.StepUpEnabled
        return flags
