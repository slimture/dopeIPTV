"""The series page in the middle pane, where the lists are (the window's
side is ui/mw_series_page.py; what is shown is worked out in
core/series_view.py): a header - backdrop or poster, title, year,
rating, genres, the plot and one clear button to play on - a row of
seasons, and the season's episodes as cards (ui/series_cards.py).

Pure presentation: the page draws what it is given and says what was
asked (its signals); it never plays, fetches or stores anything. Pictures
come through the window's image loaders (core/workers.LogoLoader), asked
for only for the cards on screen and painted when they are in - never
fetched on the window's thread. A season of hundreds of episodes is one
list of uniform rows: a season shown is a model reset, nothing built per
episode.
"""

from __future__ import annotations

import time

from PyQt6 import sip
from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from ..core import series_view as sv
from ..i18n import tr
from .series_cards import (
    EpisodeDelegate, EpisodeModel, EpisodeState, EpisodeView, ImageSource, _c, _cover,
    row_height, wrap_lines,
)
from .theme import P, corner_radius

__all__ = ["EpisodeState", "SeriesHeader", "SeriesPage", "SeasonBar", "drawn_icon"]


def drawn_icon(kind: str, size: int, colour: str, rtl: bool = False,
               dpr: float = 1.0) -> QIcon:
    """A small icon drawn here, the same on every system: "back" (a
    chevron toward the start of the line), "play", "more" (three dots)."""
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    col = QColor(colour)
    s = float(size)
    if kind == "back":
        pen = QPen(col, max(1.6, s * 0.12))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        path = QPainterPath()
        if rtl:
            path.moveTo(s * 0.38, s * 0.2)
            path.lineTo(s * 0.68, s * 0.5)
            path.lineTo(s * 0.38, s * 0.8)
        else:
            path.moveTo(s * 0.62, s * 0.2)
            path.lineTo(s * 0.32, s * 0.5)
            path.lineTo(s * 0.62, s * 0.8)
        p.drawPath(path)
    elif kind == "play":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        path = QPainterPath()
        path.moveTo(s * 0.28, s * 0.18)
        path.lineTo(s * 0.84, s * 0.5)
        path.lineTo(s * 0.28, s * 0.82)
        path.closeSubpath()
        p.drawPath(path)
    else:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        r = s * 0.09
        for x in (0.22, 0.5, 0.78):
            p.drawEllipse(QPointF(s * x, s * 0.5), r, r)
    p.end()
    return QIcon(pm)


# -- the seasons --------------------------------------------------------------------

class SeasonBar(QScrollArea):
    """The seasons in one row of chips (S1 S2 ... Specials); it scrolls
    sideways when they do not fit."""

    chosen = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SeasonBar")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._host = QWidget()
        self._lay = QHBoxLayout(self._host)
        self._lay.setContentsMargins(0, 2, 0, 2)
        self._lay.setSpacing(6)
        self._lay.addStretch(1)
        self.setWidget(self._host)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.idClicked.connect(self.chosen)
        self.chips: list[QPushButton] = []
        self.setFixedHeight(40)

    def set_seasons(self, labels: list[tuple[str, str]], current: int) -> None:
        """*labels*: (chip text, its tooltip) per season."""
        for chip in self.chips:
            self._group.removeButton(chip)
            self._lay.removeWidget(chip)
            chip.hide()
            chip.deleteLater()
        self.chips = []
        for i, (text, tip) in enumerate(labels):
            chip = QPushButton(text)
            chip.setObjectName("FilterChip")
            chip.setCheckable(True)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip(tip)
            chip.setAccessibleName(tip)
            chip.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            self._group.addButton(chip, i)
            self._lay.insertWidget(i, chip)
            self.chips.append(chip)
        self.set_current(current)

    def set_current(self, i: int) -> None:
        if 0 <= i < len(self.chips):
            chip = self.chips[i]
            chip.setChecked(True)
            self.ensureWidgetVisible(chip, 24, 0)

    def current(self) -> int:
        return self._group.checkedId()


# -- the header ----------------------------------------------------------------------

POSTER = (116, 174)


class SeriesHeader(QWidget):
    """The series' backdrop (dimmed toward the page so the words read on
    it) or a plain surface, its poster, title, year · rating · genres, the
    plot in three lines (More shows all of it), the button to play on and
    the series' own actions."""

    primary = pyqtSignal()
    favorite = pyqtSignal(bool)
    follow = pyqtSignal(bool)
    later = pyqtSignal(bool)
    more = pyqtSignal(QPoint)

    PLOT_LINES = 3

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SeriesHeader")
        self._backdrop: QPixmap | None = None
        self._expanded = False
        self._plot = ""
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(18)
        self.poster = QLabel()
        self.poster.setObjectName("SeriesPoster")
        self.poster.setFixedSize(*POSTER)
        self.poster.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.poster, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(6)
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        col.addWidget(self.title)
        self.meta = QLabel()
        self.meta.setWordWrap(True)
        self.meta.setTextFormat(Qt.TextFormat.PlainText)
        col.addWidget(self.meta)
        self.plot = QLabel()
        self.plot.setWordWrap(True)
        # Plain: a synopsis is the provider's text, never markup.
        self.plot.setTextFormat(Qt.TextFormat.PlainText)
        self.plot.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeading)
        col.addWidget(self.plot)
        self.more_plot = QPushButton(tr("sp_more"))
        self.more_plot.setObjectName("SeriesLink")
        self.more_plot.setFlat(True)
        self.more_plot.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_plot.clicked.connect(self._toggle_plot)
        self.more_plot.hide()
        col.addWidget(self.more_plot, 0, Qt.AlignmentFlag.AlignLeading)
        play_row = QHBoxLayout()
        play_row.setSpacing(10)
        self.play_btn = QPushButton()
        self.play_btn.setObjectName("Primary")
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.clicked.connect(self.primary)
        self.play_btn.setIconSize(QSize(14, 14))
        play_col = QVBoxLayout()
        play_col.setSpacing(4)
        play_col.addWidget(self.play_btn)
        self.progress = QProgressBar()
        self.progress.setObjectName("EpgBar")
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.hide()
        play_col.addWidget(self.progress)
        play_row.addLayout(play_col)
        self.left = QLabel()
        play_row.addWidget(self.left, 0, Qt.AlignmentFlag.AlignVCenter)
        play_row.addStretch(1)
        col.addSpacing(4)
        col.addLayout(play_row)
        acts = QHBoxLayout()
        acts.setSpacing(6)
        self.fav_btn = self._toggle(tr("sp_favorite"), self.favorite)
        self.follow_btn = self._toggle(tr("sp_follow"), self.follow)
        self.later_btn = self._toggle(tr("sp_watch_later"), self.later)
        for b in (self.fav_btn, self.follow_btn, self.later_btn):
            acts.addWidget(b)
        self.more_btn = QToolButton()
        self.more_btn.setObjectName("InlineToggle")
        self.more_btn.setToolTip(tr("sp_more_actions"))
        self.more_btn.setAccessibleName(tr("sp_more_actions"))
        self.more_btn.setFixedSize(32, 26)
        self.more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_btn.clicked.connect(self._more_clicked)
        acts.addWidget(self.more_btn)
        acts.addStretch(1)
        self._acts = [self.fav_btn, self.follow_btn, self.later_btn, self.more_btn]
        col.addLayout(acts)
        col.addStretch(1)
        lay.addLayout(col, 1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.restyle()

    def _toggle(self, text: str, signal) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("InlineToggle")
        b.setCheckable(True)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(signal)
        return b

    def _more_clicked(self) -> None:
        b = self.more_btn
        self.more.emit(b.mapToGlobal(QPoint(0, b.height())))

    def restyle(self) -> None:
        self.title.setStyleSheet(f"color:{P['text']}; font-size:22px; font-weight:700;"
                                 " background:transparent;")
        self.meta.setStyleSheet(f"color:{P['text2']}; font-size:12px;"
                                " background:transparent;")
        self.plot.setStyleSheet(f"color:{P['text2']}; font-size:13px;"
                                " background:transparent;")
        self.left.setStyleSheet(f"color:{P['text2']}; font-size:11px;"
                                " background:transparent;")
        self.more_plot.setStyleSheet(
            f"QPushButton#SeriesLink {{ color:{P['accent']}; background:transparent;"
            " border:none; padding:0; font-size:12px; font-weight:600; }"
            f"QPushButton#SeriesLink:hover {{ color:{P['accent_hi']}; }}")
        dpr = self.devicePixelRatioF() or 1.0
        self.play_btn.setIcon(drawn_icon("play", 14, P["on_accent"], dpr=dpr))
        self.more_btn.setIcon(drawn_icon("more", 16, P["text2"], dpr=dpr))
        self.update()

    def retranslate(self) -> None:
        self.more_plot.setText(tr("sp_less") if self._expanded else tr("sp_more"))
        self.fav_btn.setText(tr("sp_favorite"))
        self.later_btn.setText(tr("sp_watch_later"))
        self.follow_btn.setText(tr("sp_following") if self.follow_btn.isChecked()
                                else tr("sp_follow"))
        self.more_btn.setToolTip(tr("sp_more_actions"))

    # -- what it shows --------------------------------------------------------------

    def set_text(self, title: str, meta: str, plot: str) -> None:
        self.title.setText(title)
        self.meta.setText(meta)
        self.meta.setVisible(bool(meta))
        if plot != self._plot:
            self._expanded = False      # the same plot drawn again stays open
        self._plot = plot
        self.plot.setText(plot)
        self.plot.setVisible(bool(plot))
        self._fit_plot()

    def set_poster(self, pm: QPixmap | None, initial: str = "") -> None:
        w, h = POSTER
        if pm is None or pm.isNull():
            self.poster.setPixmap(QPixmap())
            self.poster.setText(initial[:1].upper())
            self.poster.setStyleSheet(
                f"background:{P['sel']}; color:{P['text']}; font-size:34px;"
                f" font-weight:700; border-radius:{round(corner_radius(10))}px;")
            return
        dpr = self.devicePixelRatioF() or 1.0
        tile = QPixmap(round(w * dpr), round(h * dpr))
        tile.setDevicePixelRatio(dpr)
        tile.fill(Qt.GlobalColor.transparent)
        p = QPainter(tile)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), corner_radius(10), corner_radius(10))
        p.setClipPath(path)
        img = _cover(pm, round(w * dpr), round(h * dpr))
        img.setDevicePixelRatio(dpr)
        p.drawPixmap(QPointF(-(img.width() / dpr - w) / 2, -(img.height() / dpr - h) / 2), img)
        p.end()
        self.poster.setText("")
        self.poster.setStyleSheet("background:transparent;")
        self.poster.setPixmap(tile)

    def set_backdrop(self, pm: QPixmap | None) -> None:
        self._backdrop = pm if pm is not None and not pm.isNull() else None
        self.update()

    def has_backdrop(self) -> bool:
        return self._backdrop is not None

    def set_primary(self, text: str, fraction: float | None, left: str,
                    enabled: bool = True) -> None:
        self.play_btn.setText("  " + text if text else "")
        self.play_btn.setEnabled(enabled and bool(text))
        self.play_btn.setVisible(bool(text))
        self.progress.setVisible(fraction is not None)
        if fraction is not None:
            self.progress.setValue(round(max(0.0, min(1.0, fraction)) * 1000))
            self.progress.setFixedWidth(max(60, self.play_btn.sizeHint().width()))
        self.left.setText(left)
        self.left.setVisible(bool(left))

    def set_actions(self, shown: bool, fav: bool = False, follow: bool = False,
                    later: bool = False) -> None:
        for b in self._acts:
            b.setVisible(shown)
        self.fav_btn.setChecked(fav)
        self.follow_btn.setChecked(follow)
        self.follow_btn.setText(tr("sp_following") if follow else tr("sp_follow"))
        self.later_btn.setChecked(later)

    # -- the plot's three lines --------------------------------------------------------

    def _toggle_plot(self) -> None:
        self._expanded = not self._expanded
        self._fit_plot()

    def _fit_plot(self) -> None:
        """Three lines of the plot, the last one cut, with More beside it -
        or all of it, with Less."""
        lbl = self.plot
        if not self._plot:
            self.more_plot.hide()
            return
        lbl.ensurePolished()
        fm = lbl.fontMetrics()
        width = lbl.contentsRect().width()
        if width < 60:
            width = max(60, self.width() - POSTER[0] - 72)
        # A few pixels short of the label: its own wrap never splits a line
        # this made.
        lines = wrap_lines(fm, self._plot, width - 6, self.PLOT_LINES + 1)
        long = len(lines) > self.PLOT_LINES
        if long and not self._expanded:
            lbl.setText("\n".join(wrap_lines(fm, self._plot, width - 6, self.PLOT_LINES)))
        else:
            lbl.setText(self._plot)
        self.more_plot.setVisible(long)
        self.more_plot.setText(tr("sp_less") if self._expanded else tr("sp_more"))

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._fit_plot()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        radius = corner_radius(14)
        path = QPainterPath()
        path.addRoundedRect(r, radius, radius)
        p.setClipPath(path)
        p.fillPath(path, _c("pane"))
        pm = self._backdrop
        if pm is not None:
            img = _cover(pm, self.width(), self.height())
            p.drawPixmap((self.width() - img.width()) // 2,
                         (self.height() - img.height()) // 2, img)
            # Toward the page's own colour - dark on a dark theme, light on a
            # light one - so the words on it read in either.
            p.fillPath(path, _c("bg", 0.58))
            grad = QLinearGradient(QPointF(0, 0), QPointF(self.width(), 0))
            lead, trail = (_c("bg", 0.9), _c("bg", 0.25))
            if self.isRightToLeft():
                lead, trail = trail, lead
            grad.setColorAt(0.0, lead)
            grad.setColorAt(1.0, trail)
            p.fillPath(path, grad)
            fade = QLinearGradient(QPointF(0, r.bottom() - 60), QPointF(0, r.bottom()))
            fade.setColorAt(0.0, _c("bg", 0.0))
            fade.setColorAt(1.0, _c("bg", 0.55))
            p.fillPath(path, fade)
        p.end()


# -- the page ------------------------------------------------------------------------

class SeriesPage(QWidget):
    """The whole page: Back, the header, the seasons, the episodes - and a
    calm word in the episodes' place while they load (or why they did
    not)."""

    back_requested = pyqtSignal()
    play_requested = pyqtSignal(object)
    menu_requested = pyqtSignal(object, QPoint)
    #: A season on show (any way); one the person picked (a chip, an arrow).
    season_shown = pyqtSignal(int)
    season_picked = pyqtSignal(int)
    retry_requested = pyqtSignal()

    #: A second ask to play the same episode within this long is the
    #: same ask (a double click, a key held down).
    PLAY_DEBOUNCE_S = 0.8

    def __init__(self, stills_loader=None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SeriesPage")
        self.states: dict[str, EpisodeState] = {}
        self.playing_key = ""
        self.playing_fraction = 0.0
        self.next_key = ""
        self.seasons: list[sv.Season] = []
        self.current = -1
        self._fallback: QPixmap | None = None
        self._last_play: tuple[str, float] = ("", 0.0)
        self.stills = ImageSource(stills_loader, self._repaint_cards)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 10, 0, 0)
        outer.setSpacing(6)
        top = QHBoxLayout()
        top.setContentsMargins(8, 0, 8, 0)
        self.back_btn = QPushButton(tr("btn_back"))
        self.back_btn.setObjectName("SeriesBack")
        self.back_btn.setFlat(True)
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_btn.setIconSize(QSize(16, 16))
        self.back_btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.back_btn.clicked.connect(self.back_requested)
        top.addWidget(self.back_btn)
        top.addStretch(1)
        outer.addLayout(top)
        self.area = QScrollArea()
        self.area.setObjectName("SeriesScroll")
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        body = QWidget()
        body.setObjectName("SeriesBody")
        self.body = body
        lay = QVBoxLayout(body)
        lay.setContentsMargins(10, 0, 10, 12)
        lay.setSpacing(10)
        self.header = SeriesHeader()
        lay.addWidget(self.header)
        season_row = QHBoxLayout()
        season_row.setSpacing(10)
        self.season_bar = SeasonBar()
        self.season_bar.chosen.connect(self.pick_season)
        season_row.addWidget(self.season_bar, 1)
        self.count = QLabel()
        season_row.addWidget(self.count, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(season_row)
        self.loading = QProgressBar()
        self.loading.setObjectName("LoadBar")
        self.loading.setRange(0, 0)
        self.loading.setTextVisible(False)
        self.loading.setFixedHeight(3)
        self.loading.hide()
        lay.addWidget(self.loading)
        note_row = QHBoxLayout()
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.hide()
        note_row.addWidget(self.note, 1)
        self.retry = QPushButton(tr("sp_retry"))
        self.retry.clicked.connect(self.retry_requested)
        self.retry.hide()
        note_row.addWidget(self.retry, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(note_row)
        self.view = EpisodeView()
        self.model = EpisodeModel(self.view)
        self.view.setModel(self.model)
        self.delegate = EpisodeDelegate(self)
        self.view.setItemDelegate(self.delegate)
        self.view.play.connect(self._play)
        self.view.menu.connect(self.menu_requested)
        self.view.season_step.connect(self.step_season)
        self.view.back.connect(self.back_requested)
        self.view.moved.connect(self._reveal)
        self.view.width_changed.connect(self._fit_view)
        lay.addWidget(self.view)
        lay.addStretch(1)
        self.area.setWidget(body)
        outer.addWidget(self.area, 1)
        self.restyle()

    # -- look and words ------------------------------------------------------------

    def restyle(self) -> None:
        self.setStyleSheet(f"QWidget#SeriesPage {{ background:{P['bg']}; }}"
                           "QWidget#SeriesBody { background:transparent; }")
        self.back_btn.setStyleSheet(
            f"QPushButton#SeriesBack {{ color:{P['text2']}; background:transparent;"
            " border:none; padding:5px 10px; font-size:13px; font-weight:600; }"
            f"QPushButton#SeriesBack:hover {{ background:{P['hover']}; color:{P['text']}; }}")
        dpr = self.devicePixelRatioF() or 1.0
        self.back_btn.setIcon(drawn_icon("back", 16, P["text2"], self.isRightToLeft(), dpr))
        self.count.setStyleSheet(f"color:{P['muted']}; font-size:11px;")
        self.note.setStyleSheet(f"color:{P['muted']}; font-size:13px;")
        self.header.restyle()
        self.view.restyle()
        self.delegate.forget_text()
        self.view.viewport().update()

    def retranslate(self) -> None:
        self.back_btn.setText(tr("btn_back"))
        self.retry.setText(tr("sp_retry"))
        self.header.retranslate()
        self.delegate.forget_text()
        self.view.viewport().update()

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        if e.type() == e.Type.LayoutDirectionChange:
            dpr = self.devicePixelRatioF() or 1.0
            self.back_btn.setIcon(drawn_icon("back", 16, P["text2"], self.isRightToLeft(), dpr))

    # -- what it shows -------------------------------------------------------------

    def reset(self) -> None:
        """Empty, for another series."""
        self.seasons = []
        self.current = -1
        self.states = {}
        self.playing_key = self.next_key = ""
        self.playing_fraction = 0.0
        self._fallback = None
        self.stills.forget()
        self.model.set_items([])
        self.season_bar.set_seasons([], -1)
        self.season_bar.hide()
        self.count.setText("")
        self.header.set_backdrop(None)
        self.area.verticalScrollBar().setValue(0)
        self._fit_view()

    def set_loading(self, on: bool, text: str = "") -> None:
        self.loading.setVisible(on)
        self.note.setText(text)
        self.note.setVisible(bool(text))
        self.retry.hide()

    def set_failed(self, text: str) -> None:
        self.loading.hide()
        self.note.setText(text)
        self.note.show()
        self.retry.show()

    def set_fallback_art(self, pm: QPixmap | None) -> None:
        """The series' picture the cards without a still of their own show."""
        if pm is not None and not pm.isNull():
            self._fallback = pm
            self.view.viewport().update()

    def fallback_art(self) -> QPixmap | None:
        return self._fallback

    def set_seasons(self, seasons: list[sv.Season], labels: list[tuple[str, str]],
                    current: int) -> None:
        self.seasons = list(seasons)
        self.set_loading(False)
        self.season_bar.set_seasons(labels, current)
        # One season of nothing but episodes needs no row of one chip.
        self.season_bar.setVisible(len(seasons) > 1)
        self.current = -1
        self.show_season(current)
        if not seasons:
            self.note.setText(tr("sp_no_episodes"))
            self.note.show()

    def show_season(self, i: int) -> None:
        """Season *i* in place of the one shown - the page stays."""
        if not 0 <= i < len(self.seasons):
            self.model.set_items([])
            self._fit_view()
            return
        changed = i != self.current
        self.current = i
        season = self.seasons[i]
        if changed:
            self.model.set_items(season.episodes)
        self.season_bar.set_current(i)
        self.count.setText(tr("count_episodes", n=len(season.episodes)))
        self._fit_view()
        if changed:
            self.season_shown.emit(i)

    def pick_season(self, i: int) -> None:
        """Season *i*, picked by the person (a chip clicked)."""
        before = self.current
        self.show_season(i)
        if self.current != before:
            self.season_picked.emit(self.current)

    def step_season(self, step: int) -> None:
        """The season before (-1) or after (+1), from the keyboard."""
        n = len(self.seasons)
        if n < 2:
            return
        i = max(0, min(n - 1, self.current + step))
        if i != self.current:
            self.pick_season(i)
            if self.model.rowCount():
                self.view.setCurrentIndex(self.model.index(0, 0))

    def set_states(self, states: dict[str, EpisodeState], next_key: str,
                   playing_key: str, playing_fraction: float = 0.0) -> None:
        self.states = states
        self.next_key = next_key or ""
        self.playing_key = playing_key or ""
        self.playing_fraction = playing_fraction
        self.view.viewport().update()

    def focus_episode(self, key: str, take_focus: bool = True) -> bool:
        """Its season shown, its card current and on screen."""
        at = next((i for i, s in enumerate(self.seasons)
                   if any(sv.episode_key(e) == key for e in s.episodes)), None)
        if at is None:
            return False
        self.show_season(at)
        row = self.model.row_of(key)
        if row < 0:
            return False
        idx = self.model.index(row, 0)
        self.view.setCurrentIndex(idx)
        self._reveal(self.view.visualRect(idx))
        if take_focus:
            self.view.setFocus(Qt.FocusReason.OtherFocusReason)
        return True

    def shown_episodes(self) -> list[dict]:
        return self.model.items()

    def reveal_current(self) -> None:
        """The current card on screen (once the page has its size)."""
        idx = self.view.currentIndex()
        if idx.isValid():
            self._fit_view()
            self._reveal(self.view.visualRect(idx))

    # -- inside ----------------------------------------------------------------------

    def _play(self, ep) -> None:
        key = sv.episode_key(ep or {})
        now = time.monotonic()
        last_key, last_at = self._last_play
        if key and key == last_key and now - last_at < self.PLAY_DEBOUNCE_S:
            return
        self._last_play = (key, now)
        self.play_requested.emit(ep)

    def _fit_view(self) -> None:
        rows = self.model.rowCount()
        h = rows * row_height(max(1, self.view.viewport().width() or self.width())) + 4
        self.view.setFixedHeight(max(4, h))

    def _reveal(self, rect: QRect) -> None:
        """The card *rect* (in the list's own coordinates) on screen."""
        vp = self.view.viewport()
        if sip.isdeleted(vp):
            return
        top = vp.mapTo(self.body, rect.topLeft())
        self.area.ensureVisible(top.x() + rect.width() // 2, top.y() + rect.height() // 2,
                                0, rect.height() // 2 + 12)

    def _repaint_cards(self) -> None:
        if not sip.isdeleted(self):
            self.view.viewport().update()

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.BackButton:
            self.back_requested.emit()
            e.accept()
            return
        super().mousePressEvent(e)
