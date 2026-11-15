"""Your statistics (History menu): how much was watched this week, month and
year, on which days and at what hours, the channels, films and series
watched most, the longest streak of days - from the log the window counts
into (core/viewing_stats.py), on this computer only.

Calm on purpose: one series per chart in the accent, no second axis, a
hairline baseline and nothing else behind the bars, the busiest bar's value
on its cap and every bar's in its tooltip; text in the theme's text colours,
never the accent. The period chosen above the charts scopes them and the
top lists alike.
"""

from __future__ import annotations

from datetime import date

from PyQt6.QtCore import QDate, QLocale, QPoint, QRectF, QSize, Qt, QTime
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QButtonGroup, QDialog, QFrame, QHBoxLayout, QLabel,
    QPushButton, QSizePolicy, QToolTip, QVBoxLayout, QWidget,
)

from ..core import viewing_stats as vs
from ..i18n import tr
from .theme import P

#: The setting that remembers the period last shown (this computer's).
PERIOD_KEY = "stats_period"


def fmt_time(secs: float) -> str:
    """A length of time as people say it: "45 min", "3 h 20 min", "140 h"."""
    mins = int(round(secs / 60.0))
    if secs <= 0:
        return tr("opt_minutes", n=0)
    if mins < 1:
        return tr("stats_under_min")
    if mins < 60:
        return tr("opt_minutes", n=mins)
    h, m = divmod(mins, 60)
    if m == 0 or h >= 100:
        return tr("stats_h", h=h)
    return tr("stats_h_min", h=h, m=m)


def fmt_days(n: int) -> str:
    return tr("dur_1d") if n == 1 else tr("stats_days", n=n)


def week_start() -> int:
    """The locale's first day of the week, 0 = Monday ... 6 = Sunday."""
    try:
        return (QLocale().firstDayOfWeek().value - 1) % 7
    except Exception:
        return 0


class ColumnChart(QWidget):
    """One series as columns: a bar per slot from one baseline, the
    accent's, at most 24 px wide with a 4 px rounded cap; the tallest bar
    has its value on its cap, every bar its own in a tooltip (and all of
    them in the accessible description). Nothing else is drawn but the
    baseline and the slots' labels."""

    BAR_MAX = 24
    GAP = 2

    def __init__(self, names: list[str], labels: list[str], title: str) -> None:
        super().__init__()
        self._names = names            # each slot's full name (tooltip)
        self._labels = labels          # under the slot ("" for none)
        self._values = [0.0] * len(names)
        self._hover = -1
        self.setAccessibleName(title)
        self.setMouseTracking(True)
        self.setMinimumHeight(168)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:      # noqa: N802 - Qt's name
        return QSize(320, 168)

    def set_values(self, values: list[float]) -> None:
        self._values = [max(0.0, float(v)) for v in values]
        self.setAccessibleDescription("; ".join(
            f"{n}: {fmt_time(v)}" for n, v in zip(self._names, self._values, strict=False)))
        self.update()

    def values(self) -> list[float]:
        return list(self._values)

    def _plot(self) -> QRectF:
        return QRectF(2, 22, max(10, self.width() - 4), max(10, self.height() - 22 - 22))

    def _slot(self, i: int) -> QRectF:
        plot = self._plot()
        w = plot.width() / max(1, len(self._values))
        return QRectF(plot.left() + i * w, plot.top(), w, plot.height())

    def _bar(self, i: int) -> QRectF | None:
        top = max(self._values) if self._values else 0.0
        v = self._values[i]
        if top <= 0 or v <= 0:
            return None
        slot, plot = self._slot(i), self._plot()
        width = min(self.BAR_MAX, max(2.0, slot.width() - self.GAP))
        height = max(2.0, plot.height() * v / top)
        return QRectF(slot.center().x() - width / 2, plot.bottom() - height, width, height)

    def paintEvent(self, _event) -> None:          # noqa: N802 - Qt's name
        pr = QPainter(self)
        pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        plot = self._plot()
        base = QPen(QColor(P["border_in"]))
        base.setWidthF(1.0)
        pr.setPen(base)
        pr.drawLine(QPoint(int(plot.left()), int(plot.bottom()) + 1),
                    QPoint(int(plot.right()), int(plot.bottom()) + 1))
        top = max(self._values) if self._values else 0.0
        peak = self._values.index(top) if top > 0 else -1
        small = QFont(self.font())
        small.setPixelSize(11)
        pr.setFont(small)
        fm = pr.fontMetrics()
        for i, label in enumerate(self._labels):
            if not label:
                continue
            slot = self._slot(i)
            pr.setPen(QColor(P["text2"] if i == self._hover else P["muted"]))
            # Under its slot, kept inside the chart at either end.
            tw = fm.horizontalAdvance(label) + 2
            x = min(max(0.0, slot.center().x() - tw / 2), self.width() - tw)
            pr.drawText(QRectF(x, plot.bottom() + 5, tw, 16),
                        int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop), label)
        for i in range(len(self._values)):
            bar = self._bar(i)
            if bar is None:
                continue
            colour = QColor(P["accent_hi"] if i == self._hover else P["accent"])
            r = min(4.0, bar.width() / 2, bar.height())
            path = QPainterPath()
            path.moveTo(bar.left(), bar.bottom())
            path.lineTo(bar.left(), bar.top() + r)
            path.quadTo(bar.left(), bar.top(), bar.left() + r, bar.top())
            path.lineTo(bar.right() - r, bar.top())
            path.quadTo(bar.right(), bar.top(), bar.right(), bar.top() + r)
            path.lineTo(bar.right(), bar.bottom())
            path.closeSubpath()
            pr.fillPath(path, colour)
        if peak >= 0:
            bar = self._bar(peak)
            if bar is not None:
                pr.setPen(QColor(P["text2"]))
                text = fmt_time(top)
                w = fm.horizontalAdvance(text) + 4
                x = min(max(0.0, bar.center().x() - w / 2), self.width() - w)
                pr.drawText(QRectF(x, bar.top() - 18, w, 16),
                            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom), text)
        pr.end()

    def _at(self, x: float) -> int:
        for i in range(len(self._values)):
            if self._slot(i).left() <= x < self._slot(i).right():
                return i
        return -1

    def mouseMoveEvent(self, event) -> None:       # noqa: N802 - Qt's name
        i = self._at(event.position().x())
        if i != self._hover:
            self._hover = i
            self.update()
        if i >= 0:
            QToolTip.showText(event.globalPosition().toPoint(),
                              f"{self._names[i]} · {fmt_time(self._values[i])}", self)
        else:
            QToolTip.hideText()

    def leaveEvent(self, _event) -> None:          # noqa: N802 - Qt's name
        self._hover = -1
        QToolTip.hideText()
        self.update()


class _Share(QWidget):
    """A thin bar under a top list's row: its time beside the list's first."""

    def __init__(self, share: float) -> None:
        super().__init__()
        self._share = max(0.0, min(1.0, share))
        self.setFixedHeight(4)

    def paintEvent(self, _event) -> None:          # noqa: N802 - Qt's name
        pr = QPainter(self)
        pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = self.width() * self._share
        if w >= 1:
            path = QPainterPath()
            path.addRoundedRect(QRectF(0, 0, w, self.height()), 2, 2)
            pr.fillPath(path, QColor(P["accent"]))
        pr.end()


def _label(text: str, size: int, colour: str, weight: int = 400) -> QLabel:
    lab = QLabel(text)
    lab.setTextFormat(Qt.TextFormat.PlainText)
    lab.setStyleSheet(f"font-size:{size}px; color:{P[colour]}; font-weight:{weight};"
                      " background:transparent;")
    return lab


class _Elided(QLabel):
    """A title cut with "…" to the room it has, whole in its tooltip."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self._full = text
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setToolTip(text)
        self.setStyleSheet(f"font-size:13px; color:{P['text']}; background:transparent;")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def full(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:          # noqa: N802 - Qt's name
        super().resizeEvent(event)
        self.setText(self.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideRight, max(0, self.width())))


class StatsWindow(QDialog):
    """The statistics window. *log* is the window's ViewingLog; *settings*
    remembers the period last shown."""

    def __init__(self, parent, log: vs.ViewingLog, settings=None,
                 today: date | None = None) -> None:
        super().__init__(parent)
        self._log = log
        self._settings = settings
        self._today = today
        self.setWindowTitle(tr("stats_title"))
        self.setObjectName("StatsWindow")
        self.setMinimumSize(720, 560)
        self.setStyleSheet(self._style())
        col = QVBoxLayout(self)
        col.setContentsMargins(24, 22, 24, 18)
        col.setSpacing(14)
        col.addWidget(_label(tr("stats_title"), 18, "text", 700))
        sub = _label(tr("stats_sub"), 13, "muted")
        sub.setWordWrap(True)
        col.addWidget(sub)
        self._rows = list(log.rows())
        if not self._rows:
            col.addStretch(1)
            empty = QVBoxLayout()
            empty.setSpacing(6)
            head = _label(tr("stats_empty"), 16, "text2", 600)
            head.setObjectName("StatsEmpty")
            head.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            hint = _label(tr("stats_empty_hint"), 13, "muted")
            hint.setWordWrap(True)
            hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            empty.addWidget(head)
            empty.addWidget(hint)
            col.addLayout(empty)
            col.addStretch(2)
        else:
            self._build(col)
        foot = QHBoxLayout()
        self.foot = _label("", 12, "muted")
        self.foot.setWordWrap(True)
        foot.addWidget(self.foot, 1)
        close = QPushButton(tr("common_close"))
        close.clicked.connect(self.accept)
        foot.addWidget(close, 0, Qt.AlignmentFlag.AlignBottom)
        col.addLayout(foot)
        if self._rows:
            self._show(self._period)

    @staticmethod
    def _style() -> str:
        tint = QColor(P["accent"])
        tint.setAlphaF(0.24)
        wash = f"rgba({tint.red()}, {tint.green()}, {tint.blue()}, {tint.alphaF():.2f})"
        return (f"QDialog#StatsWindow {{ background:{P['bg']}; }}"
                f"QFrame#StatTile, QFrame#StatList {{ background:{P['pane']};"
                " border:none; border-radius:10px; }"
                f"QPushButton#StatsPeriod {{ background:transparent; color:{P['muted']};"
                f" border:1px solid {P['border_in']}; border-radius:13px;"
                " padding:4px 14px; font-weight:500; }"
                f"QPushButton#StatsPeriod:hover {{ color:{P['text2']}; }}"
                f"QPushButton#StatsPeriod:checked {{ background:{wash}; color:{P['text']};"
                " border-color:transparent; }")

    # -- building ----------------------------------------------------------

    def _build(self, col: QVBoxLayout) -> None:
        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        self.tiles: dict[str, QLabel] = {}
        for key, label in ((vs.WEEK, "stats_this_week"), (vs.MONTH, "stats_this_month"),
                           (vs.YEAR, "stats_this_year"), ("streak", "stats_streak")):
            tile = QFrame(objectName="StatTile")
            v = QVBoxLayout(tile)
            v.setContentsMargins(14, 10, 14, 12)
            v.setSpacing(2)
            v.addWidget(_label(tr(label), 12, "muted"))
            value = _label("", 20, "text", 600)
            value.setObjectName("StatValue")
            v.addWidget(value)
            self.tiles[key] = value
            if key == "streak":
                self.streak_now = _label("", 12, "muted")
                v.addWidget(self.streak_now)
            v.addStretch(1)
            tiles.addWidget(tile, 1)
        col.addLayout(tiles)

        # The period: one row above everything it scopes.
        stored = str(self._settings.value(PERIOD_KEY, vs.MONTH)) if self._settings else vs.MONTH
        self._period = stored if stored in vs.PERIODS else vs.MONTH
        row = QHBoxLayout()
        row.setSpacing(6)
        self._group = QButtonGroup(self)
        self.period_buttons: dict[str, QPushButton] = {}
        for key, label in ((vs.WEEK, "stats_this_week"), (vs.MONTH, "stats_this_month"),
                           (vs.YEAR, "stats_this_year"), (vs.ALL, "stats_all_time")):
            b = QPushButton(tr(label), objectName="StatsPeriod")
            b.setCheckable(True)
            b.setChecked(key == self._period)
            b.setProperty("period", key)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self._group.addButton(b)
            self.period_buttons[key] = b
            row.addWidget(b)
        row.addStretch(1)
        self._group.buttonClicked.connect(self._picked)
        col.addLayout(row)

        loc = QLocale()
        days = [loc.dayName(d, QLocale.FormatType.LongFormat) for d in range(1, 8)]
        short = [loc.dayName(d, QLocale.FormatType.ShortFormat) for d in range(1, 8)]
        first = week_start()
        self._order = [(first + i) % 7 for i in range(7)]
        hours = [loc.toString(QTime(h, 0), QLocale.FormatType.ShortFormat) for h in range(24)]
        # On the cards' surface, as the lists are: the accent keeps its
        # contrast there in the light themes too.
        charts = QHBoxLayout()
        charts.setSpacing(10)
        self.days_chart = ColumnChart([days[i] for i in self._order],
                                      [short[i] for i in self._order], tr("stats_by_day"))
        self.hours_chart = ColumnChart(hours, [hours[h] if h % 6 == 0 else "" for h in range(24)],
                                       tr("stats_by_hour"))
        for title, chart, stretch in ((tr("stats_by_day"), self.days_chart, 2),
                                      (tr("stats_by_hour"), self.hours_chart, 3)):
            card = QFrame(objectName="StatList")
            v = QVBoxLayout(card)
            v.setContentsMargins(14, 10, 14, 8)
            v.setSpacing(2)
            v.addWidget(_label(title, 13, "text2", 600))
            v.addWidget(chart)
            charts.addWidget(card, stretch)
        col.addLayout(charts)

        lists = QHBoxLayout()
        lists.setSpacing(10)
        self.lists: dict[str, QVBoxLayout] = {}
        for kind, label in ((vs.CHANNEL, "stats_top_channels"), (vs.FILM, "stats_top_films"),
                            (vs.SERIES, "stats_top_series")):
            box = QFrame(objectName="StatList")
            v = QVBoxLayout(box)
            v.setContentsMargins(14, 10, 14, 12)
            v.setSpacing(6)
            v.addWidget(_label(tr(label), 13, "text2", 600))
            body = QVBoxLayout()
            body.setSpacing(6)
            v.addLayout(body)
            v.addStretch(1)
            self.lists[kind] = body
            lists.addWidget(box, 1)
        col.addLayout(lists, 1)

    # -- showing -----------------------------------------------------------

    def _picked(self, button) -> None:
        period = button.property("period")
        if period in vs.PERIODS:
            self._show(period)
            if self._settings is not None:
                self._settings.setValue(PERIOD_KEY, period)

    def summary(self, period: str) -> vs.Summary:
        return vs.summarize(self._rows, self._today or date.today(), period, week_start())

    def _show(self, period: str) -> None:
        self._period = period
        s = self.summary(period)
        for key in (vs.WEEK, vs.MONTH, vs.YEAR):
            self.tiles[key].setText(fmt_time(s.totals.get(key, 0.0)))
        self.tiles["streak"].setText(fmt_days(s.longest) if s.longest else "–")
        self.streak_now.setText(tr("stats_streak_now", days=fmt_days(s.current))
                                if s.current else tr("stats_streak_none"))
        self.days_chart.set_values([s.by_weekday[i] for i in self._order])
        self.hours_chart.set_values(s.by_hour)
        for kind, body in self.lists.items():
            while body.count():
                item = body.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.deleteLater()
            top = s.top.get(kind) or []
            if not top:
                body.addWidget(_label(tr("stats_nothing"), 12, "muted"))
                continue
            most = top[0][1] or 1.0
            for name, secs in top:
                row = QWidget()
                v = QVBoxLayout(row)
                v.setContentsMargins(0, 0, 0, 0)
                v.setSpacing(3)
                line = QHBoxLayout()
                line.setSpacing(8)
                line.addWidget(_Elided(name), 1)
                line.addWidget(_label(fmt_time(secs), 12, "muted"))
                v.addLayout(line)
                v.addWidget(_Share(secs / most))
                body.addWidget(row)
        first = s.first
        since = (QLocale().toString(QDate(first.year, first.month, first.day),
                                    QLocale.FormatType.LongFormat) if first else "")
        self.foot.setText(tr("stats_foot", date=since))
