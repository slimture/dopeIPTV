"""Nerd: the stream analysis window (core/ts_analysis.py) - a simple TR 101
290 for the live channel that plays. A summary line (OK, warnings,
errors), the tiles (bitrate, null packets, PCR interval, PCR jitter with
its sparkline), the indicators of priorities 1 and 2 with their counts,
when each was last seen and what it means, and the PIDs (type, bitrate,
continuity errors, scrambled). Copy puts it on the clipboard as plain
text, scrubbed as an error report is; Reset counters starts the counts
over.

The window only looks: *poll* (the main window's) is asked once a second
for (status, snapshot, seconds without bytes); the analysis itself runs on
a thread of its own and caps its own work. Not modal - the picture plays
on beside it.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PyQt6 import sip
from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from ..core import ts_analysis as ta
from ..i18n import tr
from .theme import P, _luminance, _mix

Poll = Callable[[], "tuple[str, ta.Snapshot | None, float]"]

#: Statuses the body (tiles, indicators, PIDs) shows for; the others are a
#: sentence of their own.
SHOWN = ("running", "stalled")
MARKS = {ta.OK: "✓", ta.WARN: "!", ta.ERROR: "✗", ta.WAITING: "…"}
#: Codecs said in the app's language; the rest by their own names.
_SAID = {"null": "tsa_codec_null", "private": "tsa_codec_private",
         "sections": "tsa_codec_private", "unknown": "tsa_codec_unknown"}


def _surface() -> tuple[str, str]:
    """A section's colour and its hairlines, as Settings works them out."""
    if _luminance(P["bg"]) > 0.4:
        return P["input"], P["border"]
    return _mix(P["text"], P["bg"], 0.05), _mix(P["text"], P["bg"], 0.12)


def tone(state: str) -> str:
    return {ta.OK: P.get("ok", P["text2"]), ta.WARN: P.get("warn", P["text2"]),
            ta.ERROR: P["error"]}.get(state, P["muted"])


def fmt_rate(bps: float | None) -> str:
    if bps is None:
        return "–"
    if bps >= 1e6:
        return f"{bps / 1e6:.2f} Mbit/s"
    if bps >= 1e3:
        return f"{bps / 1e3:.0f} kbit/s"
    return f"{bps:.0f} bit/s"


def fmt_ms(ms: float | None) -> str:
    if ms is None:
        return "–"
    return f"{ms:.1f} ms" if ms < 10 else f"{ms:.0f} ms"


def fmt_when(wall: float | None) -> str:
    return time.strftime("%H:%M:%S", time.localtime(wall)) if wall else "–"


def codec_text(p: ta.PidInfo) -> str:
    name = tr(_SAID[p.codec]) if p.codec in _SAID else ta.CODEC_NAMES.get(p.codec, p.codec)
    return f"{name} · {p.lang}" if p.lang else name


def summary(snap: ta.Snapshot | None) -> tuple[str, str]:
    """(state, the sentence) for the summary line."""
    state, behind = ta.verdict(snap)
    if state == ta.WAITING:
        return state, tr("tsa_waiting")
    if state == ta.OK:
        return state, tr("tsa_ok", n=int(ta.RECENT))
    names = ", ".join(f"{i.name} ({i.recent})" for i in behind)
    return state, tr("tsa_errors" if state == ta.ERROR else "tsa_warnings", names=names)


def status_text(status: str, idle: float = 0.0) -> str:
    """A status that is not an analysis under way, said plainly."""
    if status == "need_buffer":
        return tr("tsa_need_buffer", page=tr("tab_playback"), card=tr("set_card_rewind"),
                  setting=tr("setting_dvr_kept"))
    if status == "stalled":
        return tr("tsa_stalled", n=int(idle))
    if status == "waiting":
        return tr("tsa_waiting")
    return tr(f"tsa_state_{status}")


def report_lines(status: str, snap: ta.Snapshot | None, idle: float = 0.0) -> list[str]:
    """What the window shows, as plain text in the app's language."""
    out = [tr("tsa_title")]
    if snap is not None:
        who = " · ".join(x for x in (snap.service(), snap.provider()) if x)
        if who:
            out.append(who)
    if status not in SHOWN or snap is None:
        out.append(status_text(status, idle))
        return out
    out.append(summary(snap)[1])
    if status == "stalled":
        out.append(status_text(status, idle))
    out.append(" · ".join((
        f"{tr('tsa_tile_rate')}: {fmt_rate(snap.bps)}",
        f"{tr('tsa_tile_null')}: " + ("–" if snap.null_share is None else f"{snap.null_share * 100:.1f} %"),
        f"{tr('tsa_tile_pcr')}: {fmt_ms(snap.pcr_avg)} ({tr('tsa_at_most', max=fmt_ms(snap.pcr_max))})",
        f"{tr('tsa_tile_jitter')}: {fmt_ms(snap.jitter_now)} "
        f"({tr('tsa_at_most', max=fmt_ms(snap.jitter_max))})")))
    for prio in (1, 2):
        out.append("")
        out.append(tr(f"tsa_p{prio}"))
        for i in snap.indicators:
            if i.priority == prio:
                last = f" ({tr('tsa_last', when=fmt_when(i.last))})" if i.count else ""
                out.append(f"  {i.ref} {i.name}: {i.count}{last}")
    out.append("")
    out.append(tr("tsa_pids"))
    for p in snap.pids:
        bits = [f"  0x{p.pid:04X} ({p.pid})", codec_text(p), fmt_rate(p.bps),
                f"{tr('tsa_col_cc')}: {p.cc_errors}"]
        if p.scrambled:
            bits.append(tr("tsa_col_scrambled"))
        out.append("  ".join(bits))
    if snap.analysed < 0.98:
        out.append(tr("tsa_analysed", pct=int(snap.analysed * 100)))
    return out


def _label(text: str, size: int, colour: str, weight: int = 400) -> QLabel:
    lab = QLabel(text)
    lab.setTextFormat(Qt.TextFormat.PlainText)
    lab.setStyleSheet(f"font-size:{size}px; color:{P[colour]}; font-weight:{weight};"
                      " background:transparent;")
    return lab


class Sparkline(QWidget):
    """One series - the PCR jitter, a value a second - as a line in the
    accent over a hairline baseline, the peak's value at its end. A second
    without a value is a gap."""

    def __init__(self) -> None:
        super().__init__()
        self._values: list[float | None] = []
        self.setMinimumHeight(40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:          # noqa: N802 - Qt's name
        return QSize(220, 40)

    def set_values(self, values: list[float | None]) -> None:
        self._values = list(values)[-ta.HISTORY:]
        got = [v for v in self._values if v is not None]
        self.setAccessibleDescription(f"{fmt_ms(got[-1] if got else None)} ("
                                      f"{tr('tsa_at_most', max=fmt_ms(max(got) if got else None))})")
        self.update()

    def values(self) -> list[float | None]:
        return list(self._values)

    def paintEvent(self, _event) -> None:          # noqa: N802 - Qt's name
        pr = QPainter(self)
        pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        base = QPen(QColor(P["border_in"]))
        base.setWidthF(1.0)
        pr.setPen(base)
        pr.drawLine(QPointF(0, h - 1.5), QPointF(w, h - 1.5))
        got = [v for v in self._values if v is not None]
        if not got:
            pr.end()
            return
        top = max(max(got), 1.0)
        n = max(len(self._values), 2)
        step = w / (ta.HISTORY - 1)
        x0 = w - (n - 1) * step
        pen = QPen(QColor(P["accent"]))
        pen.setWidthF(1.5)
        pr.setPen(pen)
        path = QPainterPath()
        drawing = False
        for k, v in enumerate(self._values):
            if v is None:
                drawing = False
                continue
            pt = QPointF(x0 + k * step, (h - 3) - (h - 8) * min(v, top) / top)
            if drawing:
                path.lineTo(pt)
            else:
                path.moveTo(pt)
                drawing = True
        pr.drawPath(path)
        pr.end()


class PidTable(QWidget):
    """The PIDs, a row each: PID, type, bitrate (a bar in the accent and
    its value), continuity errors, scrambled."""

    ROW = 26
    HEAD = 24

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[ta.PidInfo] = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(420)
        self._fit()

    def _fit(self) -> None:
        self.setFixedHeight(self.HEAD + max(1, len(self._rows)) * self.ROW + 4)

    def set_rows(self, rows: list[ta.PidInfo]) -> None:
        self._rows = list(rows)
        self.setAccessibleDescription("; ".join(
            f"0x{p.pid:04X} {codec_text(p)} {fmt_rate(p.bps)} {tr('tsa_col_cc')}: {p.cc_errors}"
            for p in self._rows))
        self._fit()
        self.update()

    def rows(self) -> list[ta.PidInfo]:
        return list(self._rows)

    def _columns(self) -> list[tuple[float, float]]:
        w = float(self.width())
        fm = self.fontMetrics()
        cc_w = max(56.0, fm.horizontalAdvance(tr("tsa_col_cc")) + 12.0)
        scr_w = max(56.0, fm.horizontalAdvance(tr("tsa_col_scrambled")) + 12.0)
        pid_w, rate_w = 64.0, 168.0
        type_w = max(80.0, w - pid_w - cc_w - scr_w - rate_w)
        xs, out = 0.0, []
        for cw in (pid_w, type_w, rate_w, cc_w, scr_w):
            out.append((xs, cw))
            xs += cw
        return out

    def paintEvent(self, _event) -> None:          # noqa: N802 - Qt's name
        pr = QPainter(self)
        pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        _card, line = _surface()
        cols = self._columns()
        small = QFont(self.font())
        small.setPixelSize(11)
        body = QFont(self.font())
        body.setPixelSize(12)
        left = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        right = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        pr.setFont(small)
        pr.setPen(QColor(P["muted"]))
        heads = ("PID", tr("tsa_col_type"), tr("tsa_col_rate"), tr("tsa_col_cc"),
                 tr("tsa_col_scrambled"))
        for k, ((x, cw), head) in enumerate(zip(cols, heads, strict=True)):
            pr.drawText(QRectF(x + 4, 0, cw - 8, self.HEAD), right if k >= 3 else left, head)
        top = max((p.bps for p in self._rows), default=0.0) or 1.0
        pr.setFont(body)
        fm = pr.fontMetrics()
        for r, p in enumerate(self._rows):
            y = self.HEAD + r * self.ROW
            pr.setPen(QPen(QColor(line), 1.0))
            pr.drawLine(QPointF(0, y + 0.5), QPointF(self.width(), y + 0.5))
            muted = p.packets == 0
            pr.setPen(QColor(P["muted"] if muted else P["text"]))
            x, cw = cols[0]
            pr.drawText(QRectF(x + 4, y, cw - 8, self.ROW), left, f"0x{p.pid:04X}")
            x, cw = cols[1]
            pr.setPen(QColor(P["muted"] if muted else P["text2"]))
            pr.drawText(QRectF(x + 4, y, cw - 8, self.ROW), left,
                        fm.elidedText(codec_text(p), Qt.TextElideMode.ElideRight, int(cw - 8)))
            x, cw = cols[2]
            text = fmt_rate(p.bps)
            tw = fm.horizontalAdvance("00.00 Mbit/s")
            bar_w = max(20.0, cw - tw - 16)
            track = QRectF(x + 4, y + self.ROW / 2 - 3, bar_w, 6)
            path = QPainterPath()
            path.addRoundedRect(track, 3, 3)
            pr.fillPath(path, QColor(line))
            share = max(0.0, min(1.0, p.bps / top))
            if share * bar_w >= 1:
                fill = QPainterPath()
                fill.addRoundedRect(QRectF(track.x(), track.y(), bar_w * share, 6), 3, 3)
                pr.fillPath(fill, QColor(P["accent"]))
            pr.setPen(QColor(P["text2"]))
            pr.drawText(QRectF(x + 4 + bar_w + 8, y, cw - bar_w - 12, self.ROW), right, text)
            x, cw = cols[3]
            pr.setPen(QColor(P["error"] if p.cc_errors else P["muted"]))
            pr.drawText(QRectF(x + 4, y, cw - 8, self.ROW), right, str(p.cc_errors))
            x, cw = cols[4]
            pr.setPen(QColor(P.get("warn", P["text2"]) if p.scrambled else P["muted"]))
            pr.drawText(QRectF(x + 4, y, cw - 8, self.ROW), right, "✓" if p.scrambled else "–")
        if not self._rows:
            pr.setPen(QColor(P["muted"]))
            pr.drawText(QRectF(4, self.HEAD, self.width() - 8, self.ROW), left, tr("tsa_waiting"))
        pr.end()


class IndicatorRow(QWidget):
    """An indicator: its number and name, what it means, its count and when
    it was last seen - the count in the summary's colour while it is new."""

    def __init__(self, key: str, ref: str, name: str) -> None:
        super().__init__()
        self.key = key
        g = QGridLayout(self)
        g.setContentsMargins(0, 7, 0, 7)
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(2)
        self.title = _label(f"{ref}  {name}", 12, "text", 600)
        self.why = _label(tr(f"tsa_why_{key}"), 11, "muted")
        self.why.setWordWrap(True)
        self.count = _label("0", 13, "muted", 700)
        self.count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.count.setMinimumWidth(44)
        self.last = _label("–", 11, "muted")
        self.last.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        g.addWidget(self.title, 0, 0)
        g.addWidget(self.count, 0, 1)
        g.addWidget(self.why, 1, 0)
        g.addWidget(self.last, 1, 1)
        g.setColumnStretch(0, 1)
        self.setToolTip(tr(f"tsa_why_{key}"))
        self._said: tuple = ()

    def show_indicator(self, ind: ta.Indicator) -> None:
        said = (ind.count, ind.recent, ind.last)
        if said == self._said:
            return
        self._said = said
        self.count.setText(str(ind.count))
        if ind.recent:
            colour = tone(ta.ERROR if ind.priority == 1 else ta.WARN)
        else:
            colour = P["text2"] if ind.count else P["muted"]
        self.count.setStyleSheet(f"font-size:13px; font-weight:700; color:{colour}; background:transparent;")
        self.last.setText(tr("tsa_last", when=fmt_when(ind.last)) if ind.count else "–")


class _Tile(QFrame):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("TsaTile")
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(2)
        self.title = _label(title, 11, "muted", 600)
        self.value = _label("–", 17, "text", 700)
        self.sub = _label("", 11, "muted")
        v.addWidget(self.title)
        v.addWidget(self.value)
        v.addWidget(self.sub)
        self.extra = QVBoxLayout()
        v.addLayout(self.extra)
        v.addStretch(1)


class TsAnalysisDialog(QDialog):
    """*poll* gives (status, snapshot, seconds without bytes) for what plays;
    *reset* starts the counts over; *scrub* takes anything identifying out
    of the copied text."""

    POLL_MS = 1000

    def __init__(self, parent: QWidget | None, poll: Poll,
                 reset: Callable[[], None] | None = None,
                 scrub: Callable[[str], str] | None = None,
                 autostart: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("TsAnalysis")
        self.setWindowTitle(tr("tsa_title"))
        self.setModal(False)
        self.setMinimumSize(780, 560)
        self.resize(1000, 740)
        self._poll_fn = poll
        self._reset_fn = reset
        self._scrub = scrub
        self.status = "waiting"
        self.snap: ta.Snapshot | None = None
        self.idle = 0.0
        card, line = _surface()
        self.setStyleSheet(
            f"QDialog#TsAnalysis {{ background:{P['bg']}; }}"
            f" QFrame#TsaSurface, QFrame#TsaTile {{ background:{card}; border:none; border-radius:12px; }}"
            f" QFrame#TsaLine {{ background:{line}; border:none; }}"
            " QScrollArea#TsaScroll { background:transparent; border:none; }"
            " QWidget#TsaInner { background:transparent; }")
        col = QVBoxLayout(self)
        col.setContentsMargins(24, 20, 24, 18)
        col.setSpacing(10)
        col.addWidget(_label(tr("tsa_title"), 16, "text", 700))
        self.sub = _label("", 13, "text2")
        self.sub.setWordWrap(True)
        self.sub.hide()
        col.addWidget(self.sub)
        note = _label(tr("tsa_note"), 12, "muted")
        note.setWordWrap(True)
        col.addWidget(note)
        head = QHBoxLayout()
        head.setSpacing(10)
        self.mark = QLabel(MARKS[ta.WAITING])
        self.mark.setFixedWidth(18)
        self.summary = _label("", 14, "text", 600)
        self.summary.setWordWrap(True)
        head.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignTop)
        head.addWidget(self.summary, 1)
        col.addLayout(head)
        self.message = _label("", 13, "text2")
        self.message.setWordWrap(True)
        self.message.setObjectName("TsaMessage")
        col.addWidget(self.message)
        self.body = QWidget()
        col.addWidget(self.body, 1)
        self._build_body(self.body)
        self.foot = _label("", 12, "muted")
        self.foot.setWordWrap(True)
        col.addWidget(self.foot)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.copied = _label("", 12, "muted")
        buttons.addWidget(self.copied, 1)
        self.copy_btn = QPushButton(tr("problem_copy"))
        self.copy_btn.clicked.connect(self.copy)
        self.reset_btn = QPushButton(tr("tsa_reset"))
        self.reset_btn.clicked.connect(self.reset)
        close = QPushButton(tr("common_close"), objectName="Primary")
        close.clicked.connect(self.accept)
        buttons.addWidget(self.copy_btn)
        buttons.addWidget(self.reset_btn)
        buttons.addWidget(close)
        col.addLayout(buttons)
        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_MS)
        self._timer.timeout.connect(self.refresh)
        self.finished.connect(self._stop)
        self.show_state("waiting", None, 0.0)
        if autostart:
            self._timer.start()
            QTimer.singleShot(0, self.refresh)

    def _build_body(self, body: QWidget) -> None:
        v = QVBoxLayout(body)
        v.setContentsMargins(0, 4, 0, 0)
        v.setSpacing(12)
        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        self.t_rate = _Tile(tr("tsa_tile_rate"))
        self.t_null = _Tile(tr("tsa_tile_null"))
        self.t_pcr = _Tile(tr("tsa_tile_pcr"))
        self.t_jit = _Tile(tr("tsa_tile_jitter"))
        self.spark = Sparkline()
        self.spark.setAccessibleName(tr("tsa_tile_jitter"))
        self.t_jit.extra.addWidget(self.spark)
        for t, stretch in ((self.t_rate, 2), (self.t_null, 2), (self.t_pcr, 2), (self.t_jit, 3)):
            tiles.addWidget(t, stretch)
        v.addLayout(tiles)
        cols = QHBoxLayout()
        cols.setSpacing(12)
        # The indicators, priority 1 then 2.
        left = QFrame()
        left.setObjectName("TsaSurface")
        lv = QVBoxLayout(left)
        lv.setContentsMargins(16, 10, 8, 10)
        lv.addWidget(_label(tr("tsa_indicators"), 13, "text", 700))
        scroll = QScrollArea()
        scroll.setObjectName("TsaScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner.setObjectName("TsaInner")
        iv = QVBoxLayout(inner)
        iv.setContentsMargins(0, 0, 8, 0)
        iv.setSpacing(0)
        self.rows: dict[str, IndicatorRow] = {}
        for prio in (1, 2):
            head = _label(tr(f"tsa_p{prio}"), 11, "muted", 700)
            head.setContentsMargins(0, 10 if prio == 2 else 2, 0, 2)
            iv.addWidget(head)
            first = True
            for key, p, ref, name in ta.INDICATORS:
                if p != prio:
                    continue
                if not first:
                    hair = QFrame()
                    hair.setObjectName("TsaLine")
                    hair.setFixedHeight(1)
                    iv.addWidget(hair)
                first = False
                row = IndicatorRow(key, ref, name)
                self.rows[key] = row
                iv.addWidget(row)
        iv.addStretch(1)
        scroll.setWidget(inner)
        lv.addWidget(scroll, 1)
        # The PIDs.
        right = QFrame()
        right.setObjectName("TsaSurface")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(16, 10, 16, 10)
        rv.addWidget(_label(tr("tsa_pids"), 13, "text", 700))
        pscroll = QScrollArea()
        pscroll.setObjectName("TsaScroll")
        pscroll.setWidgetResizable(True)
        pscroll.setFrameShape(QFrame.Shape.NoFrame)
        pin = QWidget()
        pin.setObjectName("TsaInner")
        pv = QVBoxLayout(pin)
        pv.setContentsMargins(0, 0, 0, 0)
        self.pids = PidTable()
        pv.addWidget(self.pids)
        pv.addStretch(1)
        pscroll.setWidget(pin)
        rv.addWidget(pscroll, 1)
        cols.addWidget(left, 4)
        cols.addWidget(right, 6)
        v.addLayout(cols, 1)

    # -- what it shows -------------------------------------------------------------

    def refresh(self) -> None:
        if sip.isdeleted(self):
            return
        try:
            status, snap, idle = self._poll_fn()
        except Exception:
            from ..core.log import log
            log.debug("TS analysis: poll", exc_info=True)
            return
        self.show_state(status, snap, idle)

    def show_state(self, status: str, snap: ta.Snapshot | None, idle: float) -> None:
        self.status, self.snap, self.idle = status, snap, idle
        shown = status in SHOWN and snap is not None
        self.body.setVisible(shown)
        self.reset_btn.setEnabled(shown)
        who = " · ".join(x for x in (snap.service(), snap.provider()) if x) if snap else ""
        if snap is not None and len(snap.programs) > 1:
            who = " · ".join(x for x in (who, tr("tsa_programs", n=len(snap.programs))) if x)
        self.sub.setText(who)
        self.sub.setVisible(bool(who) and shown)
        if not shown:
            state = ta.WAITING
            text = status_text(status, idle)
            self.summary.setText(tr("tsa_waiting") if status == "waiting" else "")
            self.summary.setVisible(status == "waiting")
            self.message.setText("" if status == "waiting" else text)
            self.message.setVisible(status != "waiting")
            self.foot.setText("")
            self._mark(state if status == "waiting" else "")
            return
        assert snap is not None
        state, text = summary(snap)
        self.summary.setText(text)
        self.summary.setVisible(True)
        self.summary.setStyleSheet(f"font-size:14px; font-weight:600; color:{tone(state)};"
                                   " background:transparent;")
        self._mark(state)
        self.message.setText(status_text(status, idle) if status == "stalled" else "")
        self.message.setVisible(status == "stalled")
        self.t_rate.value.setText(fmt_rate(snap.bps))
        self.t_rate.sub.setText(tr("tsa_window", n=ta.WINDOW))
        self.t_null.value.setText("–" if snap.null_share is None else f"{snap.null_share * 100:.1f} %")
        self.t_null.sub.setText(tr("tsa_null_hint"))
        self.t_pcr.value.setText(fmt_ms(snap.pcr_avg))
        self.t_pcr.sub.setText(tr("tsa_at_most", max=fmt_ms(snap.pcr_max)))
        self.t_jit.value.setText(fmt_ms(snap.jitter_now) if snap.timed else "–")
        self.t_jit.sub.setText(tr("tsa_at_most", max=fmt_ms(snap.jitter_max))
                               if snap.timed else tr("tsa_no_timing"))
        self.spark.set_values(list(snap.jitter))
        for ind in snap.indicators:
            row = self.rows.get(ind.key)
            if row is not None:
                row.show_indicator(ind)
        self.pids.set_rows(list(snap.pids))
        self.foot.setText(tr("tsa_analysed", pct=int(snap.analysed * 100)) if snap.analysed < 0.98 else "")

    def _mark(self, state: str) -> None:
        self.mark.setText(MARKS.get(state, ""))
        self.mark.setStyleSheet(f"color:{tone(state)}; font-weight:800; font-size:15px;"
                                " background:transparent;")

    # -- the buttons ---------------------------------------------------------------

    def text(self) -> str:
        return "\n".join(report_lines(self.status, self.snap, self.idle))

    def copy(self) -> None:
        from PyQt6.QtGui import QGuiApplication
        text = self.text()
        if self._scrub is not None:
            text = self._scrub(text)
        else:
            from ..core.report import scrub
            text = scrub(text)
        QGuiApplication.clipboard().setText(text)
        self.copied.setText("✓ " + tr("ct_copied"))

    def reset(self) -> None:
        if self._reset_fn is not None:
            self._reset_fn()
        self.copied.setText("")

    def _stop(self, *_a) -> None:
        self._timer.stop()
