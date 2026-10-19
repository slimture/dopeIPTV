"""Building blocks of the Settings window.

The window is a sidebar of pages (with a search box over it) beside the
page on show. A page is a column of cards; a card is a stack of rows, each
a label - with an optional line of explanation under it - and its control
on the right. Every page is built from these, so they all look the same and
line up the same, which the old row of ten tabs with ten different layouts
did not.

Nothing here knows about settings keys: mw_settings creates the controls,
reads them back on OK and only places them through these helpers.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QLocale, QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap,
)
from PyQt6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit,
    QListWidget, QListWidgetItem, QScrollArea, QSizePolicy,
    QStackedWidget, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget,
)

from .theme import P

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


class SettingsCard(QFrame):
    """A rounded group of rows, with dividers between them."""

    def __init__(self, page: "SettingsPage") -> None:
        super().__init__()
        self.setObjectName("SettingsCard")
        self._page = page
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(16, 4, 16, 4)
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
            stretch_control: bool = False) -> QWidget:
        """Label on the left (and its explanation under it), the control -
        a widget or a layout - on the right."""
        w = QWidget()
        w.setObjectName("SettingsRow")
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 10, 0, 10)
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
            # unless a control has a size of its own (a switch, a number).
            fixed = control.minimumWidth() == control.maximumWidth()
            if not (stretch_control or fixed
                    or isinstance(control, QCheckBox)):
                control.setMinimumWidth(max(control.minimumWidth(), 220))
                control.setMaximumWidth(max(control.minimumWidth(), 320))
                self._page.column.append(control)
            h.addWidget(control, 1 if stretch_control else 0,
                        Qt.AlignmentFlag.AlignVCenter)
        return self._add(w, f"{label} {hint}")

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
        v.setContentsMargins(0, 10, 0, 10)
        v.addWidget(w)
        return self._add(holder, search)

    def layout_row(self, lay: QLayout, search: str = "") -> QWidget:
        """A full-width row of controls - buttons, mostly. Found by search
        under the texts on them."""
        holder = QWidget()
        lay.setContentsMargins(0, 10, 0, 10)
        holder.setLayout(lay)
        words = [search] + [w.text() for w in holder.findChildren(QWidget)
                            if hasattr(w, "text") and callable(w.text)
                            and isinstance(w.text(), str)]
        return self._add(holder, " ".join(words))

    def note(self, text: str) -> QLabel:
        lbl = _muted(text)
        holder = QWidget()
        v = QVBoxLayout(holder)
        v.setContentsMargins(0, 10, 0, 10)
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
            # QLocale, not str.upper(): Turkish "Dil" is "DİL", and Greek
            # capitals drop their accents.
            head = QLabel(QLocale().toUpper(title))
            head.setObjectName("SettingsCardTitle")
            self._col.insertWidget(at, head)
            at += 1
            c.setProperty("_title", head)
        self._col.insertWidget(at, c)
        self._col.insertSpacing(at + 1, 10)
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
        item.setSizeHint(QSize(0, 36))
        self.nav.addItem(item)
        return page

    def _show(self, row: int) -> None:
        if 0 <= row < len(self.pages):
            self.stack.setCurrentWidget(self.pages[row])

    def show_page(self, key: str | None) -> None:
        for i, p in enumerate(self.pages):
            if p.key == key and not self.nav.item(i).isHidden():
                self.nav.setCurrentRow(i)
                return
        for i in range(self.nav.count()):
            if not self.nav.item(i).isHidden():
                self.nav.setCurrentRow(i)
                return

    def current_key(self) -> str | None:
        row = self.nav.currentRow()
        return self.pages[row].key if 0 <= row < len(self.pages) else None

    def _filter(self, text: str) -> None:
        needle = " ".join(text.split()).casefold()
        first = None
        for i, page in enumerate(self.pages):
            hit = page.apply_filter(needle)
            self.nav.item(i).setHidden(not hit)
            if hit and first is None:
                first = i
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
    return f"""
#SettingsSide {{ background: {p['side']}; border-right: 1px solid {p['border']}; }}
QListWidget#SettingsNav {{ background: transparent; border: none; outline: none; }}
QListWidget#SettingsNav::item {{
    border-radius: 8px; padding: 0 10px; margin: 1px 0; color: {p['text2']};
}}
QListWidget#SettingsNav::item:hover {{ background: {p['hover']}; }}
QListWidget#SettingsNav::item:selected {{
    background: {p['accent']}; color: #FFFFFF;
}}
QLineEdit#SettingsSearch {{ padding: 7px 10px; border-radius: 9px; }}
#SettingsPageBody {{ background: {p['bg']}; }}
QLabel#SettingsTitle {{ font-size: 22px; font-weight: 700; color: {p['text']}; }}
QLabel#SettingsCardTitle {{
    color: {p['muted']}; font-size: 11px; font-weight: 700;
    letter-spacing: 1px; padding: 6px 4px 2px 4px;
}}
QFrame#SettingsCard {{
    background: {p['pane']}; border: 1px solid {p['border_in']};
    border-radius: 12px;
}}
QFrame#SettingsDivider {{ background: {p['border']}; border: none; }}
#SettingsRow, QFrame#SettingsCard > QWidget {{ background: transparent; }}
QLabel#SettingsLabel {{ font-size: 13px; color: {p['text']}; }}
QLabel#SettingsHint {{ color: {p['muted']}; }}
QPushButton#Danger {{ color: {p['error']}; }}
QPushButton#Danger:hover {{ border-color: {p['error']}; }}
#SettingsFooter {{ background: {p['bg']}; border-top: 1px solid {p['border']}; }}
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
    """The sidebar icon for a page: muted at rest, white when selected.
    *base* draws the kinds the main window already has (gear, home, ...);
    the rest are drawn here."""
    icon = QIcon()
    for mode, colour in ((QIcon.Mode.Normal, P["text2"]),
                         (QIcon.Mode.Selected, "#FFFFFF")):
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
