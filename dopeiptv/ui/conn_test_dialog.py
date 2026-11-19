"""Nerd: the window of a connection test (core/conn_test.py) - each step on
a row as it goes: a mark, what it is, what it found and its time; then the
speed set against what SD, HD, Full HD and 4K need. Copy puts it on the
clipboard scrubbed of anything identifying; Stop ends a test under way,
Test again starts over.

The test runs on a thread of its own, never the window's: the window looks
at where it is a few times a second (a timer of the dialog's own, stopped
when it closes), so nothing it waits for can hold the app.
"""

from __future__ import annotations

from typing import Any, Callable

from PyQt6 import sip
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
    QWidget,
)

from ..core import conn_test as ct
from ..core import mirrors
from ..i18n import tr
from .theme import P, _luminance, _mix

STEP_NAMES = {"dns": "ct_step_dns", "tcp": "ct_step_tcp", "tls": "ct_step_tls",
              "login": "ct_step_login", "stream": "ct_step_stream", "speed": "ct_step_speed"}
MARKS = {ct.OK: "✓", ct.WARN: "!", ct.FAIL: "✗", ct.SKIP: "–", ct.RUN: "…", ct.WAIT: "·"}


def _colour(state: str) -> str:
    return {ct.OK: P.get("ok", P["text2"]), ct.WARN: P.get("warn", P["text2"]),
            ct.FAIL: P["error"], ct.RUN: P["text2"]}.get(state, P["muted"])


def _surface() -> tuple[str, str]:
    """The section's colour and its hairlines, as Settings works them out."""
    if _luminance(P["bg"]) > 0.4:
        return P["input"], P["border"]
    return _mix(P["text"], P["bg"], 0.05), _mix(P["text"], P["bg"], 0.12)


def step_text(step: ct.Step) -> str:
    if not step.key:
        return tr("ct_checking") if step.state == ct.RUN else ""
    return tr(step.key, **step.params)


def step_time(step: ct.Step) -> str:
    return tr("ct_ms", ms=step.ms) if step.ms is not None and step.state != ct.SKIP else ""


class StepRow(QWidget):
    def __init__(self, name: str) -> None:
        super().__init__()
        self.setObjectName("CtRow")
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 9, 0, 9)
        h.setSpacing(12)
        self.mark = QLabel(MARKS[ct.WAIT])
        self.mark.setFixedWidth(16)
        self.mark.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title = QLabel(tr(STEP_NAMES[name]))
        self.title.setStyleSheet(f"font-size:13px; font-weight:600; color:{P['text']};")
        self.result = QLabel("")
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.more = QLabel("")
        # A step's result may quote what the server said: words, never markup.
        for lbl in (self.result, self.more):
            lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.more.setWordWrap(True)
        self.more.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        self.more.hide()
        col.addWidget(self.title)
        col.addWidget(self.result)
        col.addWidget(self.more)
        self.time = QLabel("")
        self.time.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        self.time.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        self.time.setMinimumWidth(64)
        h.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignTop)
        h.addLayout(col, 1)
        h.addWidget(self.time, 0, Qt.AlignmentFlag.AlignTop)
        self._said: tuple = ()

    def show_step(self, step: ct.Step) -> None:
        said = (step.state, step.key, tuple(sorted(step.params.items())), tuple(
            (k, tuple(sorted(p.items()))) for k, p in step.more), step.ms)
        if said == self._said:
            return
        self._said = said
        colour = _colour(step.state)
        self.mark.setText(MARKS.get(step.state, ""))
        self.mark.setStyleSheet(f"color:{colour}; font-weight:800; font-size:14px;")
        self.result.setText(step_text(step))
        tone = colour if step.state in (ct.FAIL, ct.WARN) else (
            P["muted"] if step.state in (ct.SKIP, ct.WAIT) else P["text2"])
        self.result.setStyleSheet(f"font-size:12px; color:{tone};")
        more = [tr(k, **p) for k, p in step.more]
        self.more.setText("\n".join(more))
        self.more.setVisible(bool(more))
        self.time.setText(step_time(step))


class ConnTestDialog(QDialog):
    """*source*: the source as it would be saved (kind, address, login, its
    options); *make_client* builds its client as the app does; *settings*
    the app's (the source's history, stream format, the backup address in
    use)."""

    POLL_MS = 120

    def __init__(self, parent: QWidget | None, source: dict, settings: Any = None,
                 make_client: Callable[[dict], Any] | None = None,
                 speed_secs: float = ct.SPEED_SECS, autostart: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("ConnTest")
        self.setWindowTitle(tr("ct_title"))
        self.setModal(True)
        self.setMinimumWidth(560)
        self._source = dict(source or {})
        self._settings = settings
        self._make_client = make_client
        self._speed_secs = speed_secs
        self.test: ct.ConnTest | None = None
        card, line = _surface()
        self.setStyleSheet(
            f"QFrame#CtSurface {{ background:{card}; border:none; border-radius:12px; }}"
            f" QFrame#CtLine {{ background:{line}; border:none; }}"
            f" QLabel#CtNeed {{ border-radius:8px; padding:4px 10px; font-size:12px; }}")
        col = QVBoxLayout(self)
        col.setContentsMargins(24, 22, 24, 20)
        col.setSpacing(8)
        head = QLabel(tr("ct_title"))
        head.setStyleSheet(f"font-size:16px; font-weight:700; color:{P['text']};")
        self.sub = QLabel(self._where())
        self.sub.setWordWrap(True)
        self.sub.setStyleSheet(f"font-size:13px; color:{P['text2']};")
        note = QLabel(tr("ct_note", secs=int(speed_secs)))
        note.setWordWrap(True)
        note.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        col.addWidget(head)
        col.addWidget(self.sub)
        col.addWidget(note)
        col.addSpacing(6)
        surface = QFrame()
        surface.setObjectName("CtSurface")
        rows = QVBoxLayout(surface)
        rows.setContentsMargins(16, 4, 16, 4)
        rows.setSpacing(0)
        self.rows: dict[str, StepRow] = {}
        for i, name in enumerate(ct.STEPS):
            if i:
                hair = QFrame()
                hair.setObjectName("CtLine")
                hair.setFixedHeight(1)
                rows.addWidget(hair)
            row = StepRow(name)
            self.rows[name] = row
            rows.addWidget(row)
        col.addWidget(surface)
        # What the speed is enough for: SD, HD, Full HD, 4K, lit when met.
        needs = QGridLayout()
        needs.setHorizontalSpacing(8)
        self.needs: list[QLabel] = []
        for i, (key, mbps) in enumerate(ct.NEEDS):
            chip = QLabel(tr("ct_need", name=ct.NEED_NAMES[key], n=f"{mbps:g}"))
            chip.setObjectName("CtNeed")
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.needs.append(chip)
            needs.addWidget(chip, 0, i)
        col.addSpacing(4)
        col.addLayout(needs)
        self.copied = QLabel("")
        self.copied.setWordWrap(True)
        self.copied.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        self.copied.hide()
        col.addWidget(self.copied)
        col.addSpacing(10)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self.copy_btn = QPushButton(tr("problem_copy"))
        self.copy_btn.clicked.connect(self.copy)
        self.again_btn = QPushButton(tr("ct_again"))
        self.again_btn.clicked.connect(self.start)
        self.close_btn = QPushButton(tr("common_close"), objectName="Primary")
        self.close_btn.clicked.connect(self._close_or_stop)
        buttons.addWidget(self.copy_btn)
        buttons.addWidget(self.again_btn)
        buttons.addWidget(self.close_btn)
        col.addLayout(buttons)
        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_MS)
        self._timer.timeout.connect(self._poll)
        self.finished.connect(self._stop)
        self._show_needs(None)
        if autostart:
            QTimer.singleShot(0, self.start)

    def _where(self) -> str:
        name = str(self._source.get("name") or "").strip()
        addr = str(self._source.get("server") or "")
        shown = mirrors.shown(addr) if "://" in addr else addr
        return " · ".join(x for x in (name, shown) if x)

    # -- the run --------------------------------------------------------------------

    def start(self) -> None:
        if self.test is not None and not self.test.finished:
            return
        self.copied.hide()
        # The stream steps take one of the account's connections: beside
        # what plays from it, the window asks first (ui/mw_connections.py).
        from .mw_connections import may_read_stream
        stream = may_read_stream(self, str(self._source.get("id") or ""))
        if sip.isdeleted(self):
            return
        self.test = ct.ConnTest(self._source, self._make_client, self._settings,
                                speed_secs=self._speed_secs, stream=stream)
        for name, row in self.rows.items():
            row.show_step(ct.Step(name))
        self._show_needs(None)
        self.again_btn.setEnabled(False)
        self.close_btn.setText(tr("ct_stop"))
        self.test.start()
        self._timer.start()

    def _poll(self) -> None:
        if sip.isdeleted(self):
            return
        t = self.test
        if t is None:
            return
        grew = False
        for step in t.snapshot():
            row = self.rows[step.name]
            had = row.more.isVisibleTo(self)
            row.show_step(step)
            grew = grew or (row.more.isVisibleTo(self) and not had)
        if grew:
            self.adjustSize()       # a line under a step: the window grows to show it
        if t.address and "://" in t.address:
            self.sub.setText(" · ".join(x for x in (
                str(self._source.get("name") or "").strip(), mirrors.shown(t.address)) if x))
        if t.finished:
            self._timer.stop()
            self._show_needs(t.mbps)
            self.again_btn.setEnabled(True)
            self.close_btn.setText(tr("common_close"))

    def _show_needs(self, mbps: float | None) -> None:
        card, _line = _surface()
        for chip, ok in zip(self.needs, ct.met(mbps), strict=True):
            if mbps is None:
                chip.setStyleSheet(f"background:{card}; color:{P['muted']};")
            elif ok:
                chip.setStyleSheet(f"background:{card}; color:{P.get('ok', P['text'])}; font-weight:700;")
            else:
                chip.setStyleSheet(f"background:{card}; color:{P['muted2']}; text-decoration: line-through;")

    def _close_or_stop(self) -> None:
        t = self.test
        if t is not None and not t.finished:
            t.cancel()
            self.close_btn.setText(tr("common_close"))
            return
        self.accept()

    def _stop(self, *_a) -> None:
        self._timer.stop()
        if self.test is not None and not self.test.finished:
            self.test.cancel()

    # -- what it found, as text ---------------------------------------------------------

    def lines(self) -> list[str]:
        t = self.test
        steps = t.snapshot() if t is not None else []
        out = [tr("ct_title") + f" - {self._source.get('kind') or 'xtream'}"]
        for i, s in enumerate(steps, 1):
            parts = [f"{i}. {tr(STEP_NAMES[s.name])}", s.state]
            if step_time(s):
                parts.append(step_time(s))
            if step_text(s):
                parts.append(step_text(s))
            out.append(" - ".join(parts))
            out += [f"   {tr(k, **p)}" for k, p in s.more]
            if s.detail:
                out.append(f"   ({s.detail})")
        if t is not None and t.mbps is not None:
            out.append(" · ".join(tr("ct_need", name=ct.NEED_NAMES[k], n=f"{n:g}") + (" ✓" if ok else " ✗")
                                  for (k, n), ok in zip(ct.NEEDS, ct.met(t.mbps), strict=True)))
        return out

    def copy(self) -> None:
        from PyQt6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(ct.copy_text(self.lines(), self._source))
        self.copied.setText("✓ " + tr("ct_copied"))
        if not self.copied.isVisibleTo(self):
            self.copied.show()
            self.adjustSize()
