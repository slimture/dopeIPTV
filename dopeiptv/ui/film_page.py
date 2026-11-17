"""A film's page in the middle pane, where the Movies place is (the
window's side is ui/mw_film_page.py; what it says is worked out in
core/film_view.py): built as a series' page is (ui/series_page.py) - Back,
then the header: the backdrop dimmed toward the page or a plain surface,
the poster, the title, year · length · genres · rating, the plot in three
lines with More, one clear button (Play, or Continue with how far it got)
and the film's own actions (Favorite, Watch later, ⋯) - and under it who
is in it, when the source or TMDB says.

Pure presentation: the page draws what it is given and says what was
asked (its signals and its header's); it never plays, fetches or stores.
The pictures come in from the window's loaders, never fetched or decoded
on the window's thread. The body is opaque (``_Body``): a scroll step
moves what is drawn and paints only the strip that comes into view.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from ..i18n import tr
from .media_cards import initials_of
from .series_page import SeriesHeader, _Body, drawn_icon
from .theme import P

__all__ = ["CastChip", "CastStrip", "FilmHeader", "FilmPage"]


class FilmHeader(SeriesHeader):
    """The series page's header for a film: the same backdrop, poster,
    words, button and actions - Favorite and Watch later, no Follow."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("FilmHeader")
        self.follow_btn.hide()
        if self.follow_btn in self._acts:
            self._acts.remove(self.follow_btn)

    def set_actions(self, shown: bool, fav: bool = False, follow: bool = False,
                    later: bool = False) -> None:
        super().set_actions(shown, fav, False, later)
        self.follow_btn.hide()


# -- who is in it ----------------------------------------------------------------------

class CastChip(QWidget):
    """One member of the cast: a round photo (or their initials) and the
    name under it in two lines at most; a click (Enter, Space) asks for
    their other titles when *clickable*."""

    clicked = pyqtSignal()

    W, PHOTO = 92, 56

    def __init__(self, name: str, clickable: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.name = name
        self._photo: QPixmap | None = None
        self._clickable = clickable
        self._hover = False
        self.setFixedSize(self.W, self.PHOTO + 44)
        self.setAccessibleName(name)
        self.setToolTip(name)
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        else:
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def set_photo(self, pm: QPixmap | None) -> None:
        self._photo = pm if pm is not None and not pm.isNull() else None
        self.update()

    def has_photo(self) -> bool:
        return self._photo is not None

    def event(self, e) -> bool:
        if e.type() == e.Type.HoverEnter:
            self._hover = True
            self.update()
        elif e.type() == e.Type.HoverLeave:
            self._hover = False
            self.update()
        return super().event(e)

    def mouseReleaseEvent(self, e) -> None:
        if self._clickable and e.button() == Qt.MouseButton.LeftButton \
                and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e) -> None:
        if self._clickable and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter,
                                           Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(e)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        size = self.PHOTO
        x = (self.width() - size) / 2
        circle = QRectF(x, 2, size, size)
        path = QPainterPath()
        path.addEllipse(circle)
        p.fillPath(path, QColor(P["sel"]))
        if self._photo is not None:
            dpr = self.devicePixelRatioF() or 1.0
            px = round(size * dpr)
            img = self._photo.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                     Qt.TransformationMode.SmoothTransformation)
            img.setDevicePixelRatio(dpr)
            p.save()
            p.setClipPath(path)
            iw, ih = img.width() / dpr, img.height() / dpr
            p.drawPixmap(round(x + (size - iw) / 2), round(2 + (size - ih) / 2), img)
            p.restore()
        else:
            f = QFont()
            f.setPixelSize(18)
            f.setWeight(QFont.Weight.Bold)
            p.setFont(f)
            p.setPen(QColor(P["text"]))
            p.drawText(circle, int(Qt.AlignmentFlag.AlignCenter), initials_of(self.name))
        if self._hover or self.hasFocus():
            ring = QColor(P["accent"])
            p.setPen(ring)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(circle.adjusted(1, 1, -1, -1))
        f = QFont()
        f.setPixelSize(11)
        p.setFont(f)
        fm = QFontMetrics(f)
        p.setPen(QColor(P["text2"] if self._hover else P["muted"]))
        lines = _two_lines(fm, self.name, self.width() - 4)
        y = 2 + size + 6
        for line in lines:
            p.drawText(QRectF(2, y, self.width() - 4, fm.height()),
                       int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop), line)
            y += fm.height()
        p.end()


def _two_lines(fm: QFontMetrics, text: str, width: int) -> list[str]:
    """*text* in two lines at most, broken between words, the second cut
    with an ellipsis."""
    words = text.split()
    if not words:
        return []
    first = words[0]
    i = 1
    while i < len(words) and fm.horizontalAdvance(f"{first} {words[i]}") <= width:
        first = f"{first} {words[i]}"
        i += 1
    lines = [fm.elidedText(first, Qt.TextElideMode.ElideRight, width)]
    rest = " ".join(words[i:])
    if rest:
        lines.append(fm.elidedText(rest, Qt.TextElideMode.ElideRight, width))
    return lines


class CastStrip(QWidget):
    """"Cast" over a row of chips that scrolls sideways when they do not
    fit; hidden while there is no one to show."""

    member_clicked = pyqtSignal(str, object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("FilmCast")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.heading = QLabel(tr("detail_cast"))
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(self.heading)
        self.area = QScrollArea()
        self.area.setObjectName("FilmCastScroll")
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._host = QWidget()
        self._host.setObjectName("FilmCastRow")
        self._row = QHBoxLayout(self._host)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(6)
        self._row.addStretch(1)
        self.area.setWidget(self._host)
        self.area.setFixedHeight(CastChip.PHOTO + 44 + 14)
        lay.addWidget(self.area)
        self.chips: list[CastChip] = []
        self._ids: list = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.restyle()
        self.hide()

    def restyle(self) -> None:
        self.heading.setStyleSheet(f"color:{P['text']}; font-size:15px; font-weight:600;"
                                   " background:transparent;")
        self.area.setStyleSheet("QScrollArea#FilmCastScroll { background:transparent; border:none; }"
                                "QWidget#FilmCastRow { background:transparent; }")
        for chip in self.chips:
            chip.update()

    def retranslate(self) -> None:
        self.heading.setText(tr("detail_cast"))

    def set_cast(self, members: list[tuple[str, str, object]], clickable: bool) -> None:
        """*members*: (name, photo address, person id) each."""
        for chip in self.chips:
            self._row.removeWidget(chip)
            chip.hide()
            chip.deleteLater()
        self.chips = []
        self._ids = []
        for i, (name, _url, pid) in enumerate(members):
            chip = CastChip(name, clickable)
            chip.clicked.connect(lambda i=i: self._chip_clicked(i))
            self._row.insertWidget(i, chip)
            self.chips.append(chip)
            self._ids.append(pid)
        self.area.horizontalScrollBar().setValue(0)
        self.setVisible(bool(members))

    def set_photo(self, index: int, pm: QPixmap | None) -> None:
        if 0 <= index < len(self.chips):
            self.chips[index].set_photo(pm)

    def names(self) -> list[str]:
        return [c.name for c in self.chips]

    def _chip_clicked(self, i: int) -> None:
        if 0 <= i < len(self.chips):
            self.member_clicked.emit(self.chips[i].name, self._ids[i])


# -- the page ------------------------------------------------------------------------

class FilmPage(QWidget):
    """The whole page: Back, the header, the cast - and a calm word while
    the source's information on the film comes in."""

    back_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("FilmPage")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 10, 0, 0)
        outer.setSpacing(6)
        top = QHBoxLayout()
        top.setContentsMargins(8, 0, 8, 0)
        self._back_text = ""
        self.back_btn = QPushButton(tr("btn_back"))
        self.back_btn.setObjectName("FilmBack")
        self.back_btn.setFlat(True)
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_btn.setIconSize(QSize(16, 16))
        self.back_btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.back_btn.clicked.connect(self.back_requested)
        top.addWidget(self.back_btn)
        top.addStretch(1)
        outer.addLayout(top)
        self.area = QScrollArea()
        self.area.setObjectName("FilmScroll")
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        body = _Body()
        body.setObjectName("FilmBody")
        self.body = body
        lay = QVBoxLayout(body)
        lay.setContentsMargins(10, 0, 10, 16)
        lay.setSpacing(16)
        self.header = FilmHeader()
        lay.addWidget(self.header)
        self.loading = QProgressBar()
        self.loading.setObjectName("LoadBar")
        self.loading.setRange(0, 0)
        self.loading.setTextVisible(False)
        self.loading.setFixedHeight(3)
        self.loading.hide()
        lay.addWidget(self.loading)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        self.note.hide()
        lay.addWidget(self.note)
        cast_row = QHBoxLayout()
        cast_row.setContentsMargins(8, 0, 8, 0)
        self.cast = CastStrip()
        cast_row.addWidget(self.cast)
        lay.addLayout(cast_row)
        lay.addStretch(1)
        self.area.setWidget(body)
        outer.addWidget(self.area, 1)
        self.restyle()

    # -- look and words ------------------------------------------------------------

    def restyle(self) -> None:
        self.setStyleSheet(f"QWidget#FilmPage {{ background:{P['bg']}; }}"
                           "QWidget#FilmBody { background:transparent; }")
        self.back_btn.setStyleSheet(
            f"QPushButton#FilmBack {{ color:{P['text2']}; background:transparent;"
            " border:none; padding:5px 10px; font-size:13px; font-weight:600; }"
            f"QPushButton#FilmBack:hover {{ background:{P['hover']}; color:{P['text']}; }}")
        self._back_icon()
        self.note.setStyleSheet(f"color:{P['muted']}; font-size:13px;")
        self.header.restyle()
        self.cast.restyle()
        self.body.update()

    def _back_icon(self) -> None:
        dpr = self.devicePixelRatioF() or 1.0
        self.back_btn.setIcon(drawn_icon("back", 16, P["text2"], self.isRightToLeft(), dpr))

    def retranslate(self) -> None:
        self.set_back_text(self._back_text)
        self.header.retranslate()
        self.cast.retranslate()

    def set_back_text(self, name: str) -> None:
        """Back's words: "Back to *name*" (the list it goes back to), or
        Back."""
        self._back_text = name
        text = tr("fp_back_to", name=name) if name else tr("btn_back")
        self.back_btn.setText(text)
        self.back_btn.setAccessibleName(text)

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        if e.type() == e.Type.LayoutDirectionChange:
            self._back_icon()

    # -- what it shows -------------------------------------------------------------

    def reset(self) -> None:
        """Empty, for another film."""
        self.header.set_backdrop(None)
        self.header.set_primary("", None, "")
        self.cast.set_cast([], False)
        self.set_loading(False)
        self.area.verticalScrollBar().setValue(0)

    def set_loading(self, on: bool, text: str = "") -> None:
        self.loading.setVisible(on)
        self.note.setText(text)
        self.note.setVisible(bool(text))

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.BackButton:
            self.back_requested.emit()
            e.accept()
            return
        super().mousePressEvent(e)
