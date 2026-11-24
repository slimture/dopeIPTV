"""The Search page in the middle pane, where the lists are (the window's
side is ui/mw_search_all.py; what a query finds, core/search_all.py): one
field at the top, chips to show one group alone, and the results in four
groups - Channels, Programmes, Movies, Series - each under a heading with
its count, a few rows of each and "Show all (N)" for the rest.

Pure presentation: the page draws the results it is given and says what
was asked (its signals); it never searches, plays or fetches anything.
Pictures come through the window's image loaders, asked for only for the
rows on screen. A row is painted, not built: thousands of hits are one
model reset. Each row names its source in its muted second line, and a
programme on the air now has a Live mark and how far it has got.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import (
    QAbstractListModel, QEvent, QLocale, QModelIndex, QPoint, QRect, QRectF, QSize, Qt,
    QTimer, pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QHBoxLayout, QLabel, QLineEdit, QListView,
    QPushButton, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout, QWidget,
)

from ..core import search_all as sa
from ..core.palette_match import words
from ..i18n import tr
from .qt_i18n import format_when
from .series_cards import ImageSource
from .theme import P, corner_radius

#: Rows of each group shown under "All" before its "Show all (N)".
FEW = {"channels": 4, "programmes": 5, "movies": 4, "series": 4}
#: The chips, in order: everything, or one group alone.
FILTERS = ("all",) + sa.GROUPS
#: Typing has to pause this long before the query is looked up.
DEBOUNCE_MS = 250
_TITLES = {"channels": "sa_group_channels", "programmes": "sa_group_programmes",
           "movies": "sa_group_movies", "series": "sa_group_series"}
_ROW = Qt.ItemDataRole.UserRole


@dataclass
class Row:
    """One line of the list: a group's heading ("head"), a hit ("hit"),
    its "Show all" ("more") or the word that only the best are shown
    ("cap")."""
    kind: str
    group: str
    hit: sa.Hit | None = None
    count: int = 0

    def enabled(self) -> bool:
        return self.kind in ("hit", "more")


def group_title(group: str) -> str:
    return tr(_TITLES.get(group, "sa_group_channels"))


def when_text(prog: dict, now: float) -> str:
    """"Today 21:00–22:30", "Tomorrow 06:00–07:00", "Fri 14 Nov 20:00–21:00"
    - or, on the air now, "Now · until 22:30"."""
    start, stop = float(prog["start_timestamp"]), float(prog["stop_timestamp"])
    if start <= now < stop:
        return tr("sa_now_until", time=format_when(stop, "HH:mm"))
    span = f"{format_when(start, 'HH:mm')}–{format_when(stop, 'HH:mm')}"
    today = time.localtime(now)
    then = time.localtime(start)
    days = (time.mktime((then.tm_year, then.tm_mon, then.tm_mday, 12, 0, 0, 0, 0, -1))
            - time.mktime((today.tm_year, today.tm_mon, today.tm_mday, 12, 0, 0, 0, 0, -1)))
    days = round(days / 86400)
    if days == 0:
        return tr("sa_today_at", span=span)
    if days == 1:
        return tr("sa_tomorrow_at", span=span)
    return f"{format_when(start, 'ddd d MMM')} {span}"


def second_line(hit: sa.Hit, now: float) -> str:
    """The muted line under a hit's title: always its source -
    "Pirat · Sport", "SVT1 HD · Pirat · Today 21:00–22:30"."""
    if hit.group == "programmes" and hit.programme is not None:
        parts = [hit.category, hit.source.name, when_text(hit.programme, now)]
    else:
        parts = [hit.source.name, hit.category]
        if hit.group in ("movies", "series"):
            year = str(hit.row.get("year") or hit.row.get("releaseDate")
                       or hit.row.get("releasedate") or "")[:4]
            if year.isdigit():
                parts.append(year)
    return " · ".join(p for p in parts if p)


class ResultsModel(QAbstractListModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.rows: list[Row] = []

    def set_rows(self, rows: list[Row]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def flags(self, index) -> Qt.ItemFlag:
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return Qt.ItemFlag.NoItemFlags
        if self.rows[index.row()].enabled():
            return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        return Qt.ItemFlag.NoItemFlags

    def data(self, index, role=Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        row = self.rows[index.row()]
        if role == _ROW:
            return row
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            if row.kind == "hit" and row.hit is not None:
                return f"{row.hit.title}, {second_line(row.hit, time.time())}"
            if row.kind == "head":
                return f"{group_title(row.group)} {row.count}"
            if row.kind == "more":
                return tr("sa_show_all", n=row.count)
        return None


class ResultsDelegate(QStyledItemDelegate):
    """A group's heading in small capitals with its count; a hit with its
    logo or poster, its title and a muted line naming its source; a Live
    mark and a progress bar for a programme on the air; "Show all (N)" in
    the accent."""

    HEAD_H, HIT_H, MORE_H = 34, 56, 36

    def __init__(self, images: ImageSource, parent=None) -> None:
        super().__init__(parent)
        self.images = images

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        row: Row | None = index.data(_ROW)
        fm = QFontMetrics(option.font)
        if row is None or row.kind == "head":
            return QSize(0, max(self.HEAD_H, fm.height() + 16))
        if row.kind in ("more", "cap"):
            return QSize(0, max(self.MORE_H, fm.height() + 14))
        return QSize(0, max(self.HIT_H, fm.height() * 2 + 18))

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index) -> None:
        row: Row | None = index.data(_ROW)
        if row is None:
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rtl = option.direction == Qt.LayoutDirection.RightToLeft
        rect = option.rect
        base = QFont(option.font)
        if row.kind == "head":
            self._head(p, rect, base, row, rtl)
        elif row.kind in ("more", "cap"):
            self._more(p, option, rect, base, row, rtl)
        elif row.hit is not None:
            self._hit(p, option, rect, base, row.hit, rtl)
        p.restore()

    @staticmethod
    def _aligned(rtl: bool) -> Qt.AlignmentFlag:
        """The leading edge, as placed here (absolute: the painter would
        mirror a plain left or right once more in a right-to-left layout)."""
        return Qt.AlignmentFlag.AlignAbsolute | (
            Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft)

    def _head(self, p: QPainter, rect: QRect, base: QFont, row: Row, rtl: bool) -> None:
        small = QFont(base)
        small.setPointSizeF(max(7.0, base.pointSizeF() * 0.82))
        small.setWeight(QFont.Weight.DemiBold)
        title = QLocale().toUpper(group_title(row.group))
        if all(ord(ch) < 0x0590 for ch in title):
            # Spaced capitals for Latin, Greek and Cyrillic; spacing would
            # break the letters of a joined script (Arabic, Devanagari...).
            small.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 106)
        p.setFont(small)
        r = rect.adjusted(14, 0, -14, -6)
        count = QLocale().toString(row.count)
        fm = QFontMetrics(small)
        p.setPen(QColor(P["muted"]))
        bottom = Qt.AlignmentFlag.AlignBottom
        p.drawText(r, bottom | self._aligned(rtl), title)
        gap = fm.horizontalAdvance(title) + 10
        r2 = r.adjusted(0, 0, -gap, 0) if rtl else r.adjusted(gap, 0, 0, 0)
        p.setPen(QColor(P["muted3"]))
        p.drawText(r2, bottom | self._aligned(rtl), count)

    def _surface(self, p: QPainter, option: QStyleOptionViewItem, rect: QRect, rtl: bool) -> bool:
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(P["sel"] if selected else P["hover"]))
            radius = corner_radius(8)
            p.drawRoundedRect(QRectF(rect.adjusted(4, 1, -4, -1)), radius, radius)
            if selected:
                p.setBrush(QColor(P["accent"]))
                bar = (QRectF(rect.right() - 6, rect.top() + 8, 3, rect.height() - 16) if rtl
                       else QRectF(rect.left() + 4, rect.top() + 8, 3, rect.height() - 16))
                p.drawRoundedRect(bar, 1.5, 1.5)
        return selected

    def _more(self, p: QPainter, option: QStyleOptionViewItem, rect: QRect, base: QFont,
              row: Row, rtl: bool) -> None:
        f = QFont(base)
        if row.kind == "more":
            self._surface(p, option, rect, rtl)
            f.setWeight(QFont.Weight.DemiBold)
            p.setPen(QColor(P["accent"]))
            text = tr("sa_show_all", n=QLocale().toString(row.count))
        else:
            f.setPointSizeF(max(7.0, base.pointSizeF() * 0.9))
            p.setPen(QColor(P["muted"]))
            text = tr("sa_best_of", shown=QLocale().toString(sa.CAP),
                      n=QLocale().toString(row.count))
        p.setFont(f)
        p.drawText(rect.adjusted(70, 0, -70, 0) if rtl else rect.adjusted(70, 0, -14, 0),
                   Qt.AlignmentFlag.AlignVCenter | self._aligned(rtl), text)

    def _hit(self, p: QPainter, option: QStyleOptionViewItem, rect: QRect, base: QFont,
             hit: sa.Hit, rtl: bool) -> None:
        self._surface(p, option, rect, rtl)
        now = time.time()
        poster = hit.group in ("movies", "series")
        h = rect.height() - 12
        w = round(h * 2 / 3) if poster else h
        lead = 14 + (44 - w) // 2
        pic = (QRect(rect.right() - lead - w + 1, rect.top() + 6, w, h) if rtl
               else QRect(rect.left() + lead, rect.top() + 6, w, h))
        self._picture(p, pic, hit, poster)
        x0 = 14 + 44 + 12
        right_pad = 14
        live = hit.group == "programmes" and hit.on_air(now)
        tag_w = 0
        small = QFont(base)
        small.setPointSizeF(max(7.0, base.pointSizeF() * 0.78))
        small.setWeight(QFont.Weight.Bold)
        if live:
            fm = QFontMetrics(small)
            label = QLocale().toUpper(tr("sa_live"))
            tag_w = fm.horizontalAdvance(label) + 14
            th = fm.height() + 4
            tag = (QRectF(rect.left() + right_pad, rect.center().y() - th / 2, tag_w, th) if rtl
                   else QRectF(rect.right() - right_pad - tag_w, rect.center().y() - th / 2,
                               tag_w, th))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(P["accent"]))
            p.drawRoundedRect(tag, th / 2, th / 2)
            p.setPen(QColor(P.get("on_accent") or P["text"]))
            p.setFont(small)
            p.drawText(tag, Qt.AlignmentFlag.AlignCenter, label)
            tag_w += 10
        text_rect = (rect.adjusted(right_pad + tag_w, 0, -x0, 0) if rtl
                     else rect.adjusted(x0, 0, -right_pad - tag_w, 0))
        tf = QFont(base)
        tf.setWeight(QFont.Weight.Medium)
        mf = QFont(base)
        mf.setPointSizeF(max(7.0, base.pointSizeF() * 0.86))
        tm, mm = QFontMetrics(tf), QFontMetrics(mf)
        gap = 3
        block = tm.height() + gap + mm.height()
        top = rect.top() + (rect.height() - block) // 2 - (2 if live else 0)
        align = Qt.AlignmentFlag.AlignVCenter | self._aligned(rtl)
        p.setFont(tf)
        p.setPen(QColor(P["text"]))
        p.drawText(QRect(text_rect.left(), top, text_rect.width(), tm.height()), align,
                   tm.elidedText(hit.title or "?", Qt.TextElideMode.ElideRight,
                                 max(10, text_rect.width())))
        p.setFont(mf)
        p.setPen(QColor(P["muted"]))
        line = second_line(hit, now)
        p.drawText(QRect(text_rect.left(), top + tm.height() + gap, text_rect.width(),
                         mm.height()), align,
                   mm.elidedText(line, Qt.TextElideMode.ElideRight,
                                 max(10, text_rect.width())))
        if live and hit.programme is not None:
            start = float(hit.programme["start_timestamp"])
            stop = float(hit.programme["stop_timestamp"])
            frac = min(1.0, max(0.0, (now - start) / (stop - start))) if stop > start else 0.0
            bar_w = min(160, text_rect.width())
            y = top + block + 5
            track = (QRectF(text_rect.right() - bar_w + 1, y, bar_w, 3) if rtl
                     else QRectF(text_rect.left(), y, bar_w, 3))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(P["btn"]))
            p.drawRoundedRect(track, 1.5, 1.5)
            done = QRectF(track)
            done.setWidth(bar_w * frac)
            if rtl:
                done.moveRight(track.right())
            p.setBrush(QColor(P["accent"]))
            p.drawRoundedRect(done, 1.5, 1.5)

    def _picture(self, p: QPainter, box: QRect, hit: sa.Hit, poster: bool) -> None:
        url = (hit.row.get("stream_icon") or hit.row.get("cover")) if poster \
            else hit.row.get("stream_icon")
        pm = self.images.get(str(url or "")) if url else None
        radius = corner_radius(6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(P["btn"]))
        p.drawRoundedRect(QRectF(box), radius, radius)
        if pm is None or pm.isNull():
            f = QFont()
            f.setPointSize(max(9, box.height() // 3))
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor(P["text3"]))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, (hit.title.strip()[:1] or "?").upper())
            return
        self._draw_fit(p, box, pm, cover=poster)

    @staticmethod
    def _draw_fit(p: QPainter, box: QRect, pm: QPixmap, cover: bool) -> None:
        dpr = pm.devicePixelRatio() or 1.0
        pw, ph = pm.width() / dpr, pm.height() / dpr
        if pw <= 0 or ph <= 0:
            return
        if cover:       # a poster fills its box, cut to its corners
            scale = max(box.width() / pw, box.height() / ph)
        else:           # a logo fits inside its plate
            scale = min((box.width() - 6) / pw, (box.height() - 6) / ph)
        w, h = pw * scale, ph * scale
        target = QRectF(box.center().x() - w / 2 + 0.5, box.center().y() - h / 2 + 0.5, w, h)
        p.save()
        if cover:
            clip = QPainterPath()
            r = corner_radius(6)
            clip.addRoundedRect(QRectF(box), r, r)
            p.setClipPath(clip)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(target, pm, QRectF(0, 0, pm.width(), pm.height()))
        p.restore()


class ResultsView(QListView):
    """The results: Up and Down go through the rows of every group (the
    headings are passed over), Enter or a double click acts on one,
    Shift+Tab goes back to the field and Tab on out of the page, the menu
    key or a right click asks for a row's menu."""

    chosen = pyqtSignal(object)
    menu_wanted = pyqtSignal(object, QPoint)
    to_field = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SearchResults")
        self.setUniformItemSizes(False)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setMouseTracking(True)
        self.setFrameShape(QListView.Shape.NoFrame)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context)
        self.doubleClicked.connect(self._double)

    def row_at(self, index: QModelIndex) -> Row | None:
        return index.data(_ROW) if index.isValid() else None

    def _double(self, index: QModelIndex) -> None:
        row = self.row_at(index)
        if row is not None and row.enabled():
            self.chosen.emit(row)

    def _context(self, pos: QPoint) -> None:
        index = self.indexAt(pos)
        row = self.row_at(index)
        if row is None or row.kind != "hit":
            return
        self.setCurrentIndex(index)
        self.menu_wanted.emit(row, self.viewport().mapToGlobal(pos))

    def first_row(self) -> int:
        model = self.model()
        for i in range(model.rowCount() if model is not None else 0):
            row = self.row_at(model.index(i, 0))
            if row is not None and row.enabled():
                return i
        return -1

    def focusNextPrevChild(self, next_: bool) -> bool:  # noqa: N802 - Qt hook
        """Shift+Tab: back to the field. Tab: on out of the page, as it goes
        anywhere else - Tab both ways to the field kept the keyboard on the
        page, the sidebar out of its reach."""
        if not next_:
            self.to_field.emit()
            return True
        return super().focusNextPrevChild(True)

    def keyPressEvent(self, e) -> None:
        key = e.key()
        row = self.row_at(self.currentIndex())
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if row is not None and row.enabled():
                self.chosen.emit(row)
            e.accept()
            return
        if key == Qt.Key.Key_Menu and row is not None and row.kind == "hit":
            r = self.visualRect(self.currentIndex())
            self.menu_wanted.emit(row, self.viewport().mapToGlobal(r.center()))
            e.accept()
            return
        if key == Qt.Key.Key_Up and self.currentIndex().row() <= self.first_row():
            self.to_field.emit()
            e.accept()
            return
        super().keyPressEvent(e)


class SearchPage(QWidget):
    """The whole page: the field, the chips, a calm line (searching, no
    matches, the sources searched only in part), the results."""

    #: The query typed, once typing paused (DEBOUNCE_MS).
    query_changed = pyqtSignal(str)
    activated = pyqtSignal(object)
    menu_requested = pyqtSignal(object, QPoint)
    #: On screen (True) or left (False): another page in its place, the
    #: pane folded away, the window minimised.
    shown_changed = pyqtSignal(bool)

    def __init__(self, loader=None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SearchPage")
        self.results: sa.Results | None = None
        self.filter = "all"
        self.busy = False
        self.images = ImageSource(loader, self._repaint)
        lay = QVBoxLayout(self)
        # The list page's own margins, so the two pages line up.
        lay.setContentsMargins(0, 14, 0, 10)
        lay.setSpacing(10)
        self.field = QLineEdit(objectName="Search")
        self.field.setClearButtonEnabled(True)
        self.field.setPlaceholderText(tr("sa_placeholder_all"))
        self.field.setAccessibleName(tr("sa_title"))
        self.field.textChanged.connect(self._typed)
        self.field.returnPressed.connect(self._enter_in_field)
        self.field.installEventFilter(self)
        lay.addWidget(self.field)
        chips = QHBoxLayout()
        chips.setContentsMargins(0, 0, 0, 0)
        chips.setSpacing(6)
        self.chip_group = QButtonGroup(self)
        self.chip_group.setExclusive(True)
        self.chips: dict[str, QPushButton] = {}
        for i, key in enumerate(FILTERS):
            chip = QPushButton(objectName="FilterChip")
            chip.setCheckable(True)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.chip_group.addButton(chip, i)
            self.chips[key] = chip
            chips.addWidget(chip)
        chips.addStretch(1)
        self.chips["all"].setChecked(True)
        self.chip_group.idClicked.connect(self._chip_clicked)
        lay.addLayout(chips)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        self.partial = QLabel("")
        self.partial.setWordWrap(True)
        self.partial.hide()
        lay.addWidget(self.partial)
        self.model = ResultsModel(self)
        self.view = ResultsView()
        self.view.setAccessibleName(tr("sa_results_a11y"))
        self.view.setModel(self.model)
        self.delegate = ResultsDelegate(self.images, self.view)
        self.view.setItemDelegate(self.delegate)
        self.view.chosen.connect(self._chosen)
        self.view.menu_wanted.connect(self._menu_wanted)
        self.view.to_field.connect(self.focus_field)
        lay.addWidget(self.view, 1)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self._fire)
        self._tick = QTimer(self)
        self._tick.setInterval(30_000)
        self._tick.timeout.connect(self._repaint)
        self.retranslate()
        self.restyle()

    # -- look and words ------------------------------------------------------------

    def restyle(self) -> None:
        self.note.setStyleSheet(f"color:{P['muted']}; font-size:12px;")
        self.partial.setStyleSheet(f"color:{P['muted']}; font-size:11px;")
        self.view.setStyleSheet("QListView#SearchResults { background: transparent;"
                                " border: none; outline: none; }")
        self.view.viewport().update()

    def retranslate(self) -> None:
        self.field.setPlaceholderText(tr("sa_placeholder_all"))
        self.field.setAccessibleName(tr("sa_title"))
        self.view.setAccessibleName(tr("sa_results_a11y"))
        self.chips["all"].setText(tr("sa_filter_all"))
        for group in sa.GROUPS:
            self.chips[group].setText(group_title(group))
        self._say()
        self.view.viewport().update()

    # -- the field -----------------------------------------------------------------

    def query(self) -> str:
        return self.field.text()

    def set_query(self, text: str) -> None:
        """*text* in the field, looked up at once."""
        self.field.blockSignals(True)
        self.field.setText(text)
        self.field.blockSignals(False)
        self._fire()

    def focus_field(self) -> None:
        self.field.setFocus()
        self.field.selectAll()

    def _typed(self, _text: str) -> None:
        self._debounce.start()

    def _fire(self) -> None:
        self._debounce.stop()
        self.query_changed.emit(self.field.text())

    def _enter_in_field(self) -> None:
        """Enter in the field: the query at once, then its best row."""
        if self._debounce.isActive():
            self._fire()
            return
        first = self.view.first_row()
        if first >= 0:
            row = self.model.rows[first]
            self.view.setCurrentIndex(self.model.index(first, 0))
            self._chosen(row)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.field and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Tab) and self.view.first_row() >= 0:
                row = self.view.row_at(self.view.currentIndex())
                if row is None or not row.enabled():
                    self.view.setCurrentIndex(self.model.index(self.view.first_row(), 0))
                self.view.setFocus()
                return True
        return super().eventFilter(obj, event)

    def escape(self) -> bool:
        """Esc on the page: the field emptied first (True), else nothing
        to do here (False: the page is left)."""
        if self.field.text():
            self.field.clear()
            self._fire()
            self.focus_field()
            return True
        return False

    # -- the results ---------------------------------------------------------------

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self._say()

    def show_results(self, results: sa.Results | None, keep_place: bool = True) -> None:
        """*results* on the page; with *keep_place* the row picked and the
        scroll stay where they were (more sources came in)."""
        picked = self._picked() if keep_place else None
        scroll = self.view.verticalScrollBar().value() if keep_place else 0
        self.results = results
        self._fill()
        if picked is not None:
            self._pick(picked)
        self.view.verticalScrollBar().setValue(scroll)
        self._say()
        on_air = results is not None and any(
            h.on_air(time.time()) for h in results.hits.get("programmes", []))
        if on_air and self.isVisible():
            self._tick.start()
        else:
            self._tick.stop()

    def _picked(self) -> tuple | None:
        row = self.view.row_at(self.view.currentIndex())
        if row is None or row.hit is None:
            return (row.kind, row.group) if row is not None else None
        h = row.hit
        return ("hit", h.group, h.source.id, sa.row_key(h.row),
                (h.programme or {}).get("start_timestamp"))

    def _pick(self, picked: tuple) -> None:
        for i, row in enumerate(self.model.rows):
            if row.kind != picked[0] or row.group != picked[1]:
                continue
            if row.kind == "hit" and row.hit is not None:
                h = row.hit
                if (h.source.id, sa.row_key(h.row),
                        (h.programme or {}).get("start_timestamp")) != picked[2:]:
                    continue
            self.view.setCurrentIndex(self.model.index(i, 0))
            return

    def _fill(self) -> None:
        rows: list[Row] = []
        res = self.results
        if res is not None:
            for group in sa.GROUPS:
                if self.filter not in ("all", group):
                    continue
                hits = res.hits.get(group) or []
                if not hits:
                    continue
                total = max(res.totals.get(group, 0), len(hits))
                rows.append(Row("head", group, count=total))
                few = len(hits) if self.filter == group else FEW[group]
                rows += [Row("hit", group, hit=h) for h in hits[:few]]
                if self.filter == "all" and len(hits) > few:
                    rows.append(Row("more", group, count=total))
                elif self.filter == group and total > len(hits):
                    rows.append(Row("cap", group, count=total))
        self.model.set_rows(rows)

    def _say(self) -> None:
        res = self.results
        terms = words(self.field.text())
        if not terms or len(" ".join(terms)) < sa.MIN_QUERY:
            text = tr("sa_hint")
        elif self.busy and (res is None or not res.count()):
            text = tr("sa_searching")
        elif res is not None and not res.count():
            text = tr("sa_none")
        else:
            text = ""
        self.note.setText(text)
        self.note.setVisible(bool(text))
        self.partial.setVisible(bool(self.partial.text()))

    def set_partial(self, text: str) -> None:
        self.partial.setText(text)
        self.partial.setVisible(bool(text))

    def set_filter(self, key: str) -> None:
        if key not in FILTERS:
            key = "all"
        self.filter = key
        chip = self.chips[key]
        if not chip.isChecked():
            chip.setChecked(True)
        self._fill()
        self.view.verticalScrollBar().setValue(0)
        first = self.view.first_row()
        if first >= 0:
            self.view.setCurrentIndex(self.model.index(first, 0))

    def _chip_clicked(self, i: int) -> None:
        self.set_filter(FILTERS[i])

    def _chosen(self, row: Row) -> None:
        if row.kind == "more":
            self.set_filter(row.group)
            self.view.setFocus()
            return
        if row.kind == "hit" and row.hit is not None:
            self.activated.emit(row.hit)

    def _menu_wanted(self, row: Row, pos: QPoint) -> None:
        if row.kind == "hit" and row.hit is not None:
            self.menu_requested.emit(row.hit, pos)

    def current_hit(self) -> sa.Hit | None:
        row = self.view.row_at(self.view.currentIndex())
        return row.hit if row is not None else None

    def _repaint(self, *_a) -> None:
        self.view.viewport().update()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if self.results is not None and any(
                h.on_air(time.time()) for h in self.results.hits.get("programmes", [])):
            self._tick.start()
        self.shown_changed.emit(True)

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._tick.stop()
        self.shown_changed.emit(False)
