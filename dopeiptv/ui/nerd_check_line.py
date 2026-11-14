"""The line under a Nerd setting that says whether it works: ✓, ! or ✗ and
a sentence, "Details…" for what was tried and the exact error (in the app's
problem window, selectable and copyable), and a Test button where a check
asks the network.

A check is made in two halves: *job()* runs on the window's thread and reads
the controls, returning the function to run off it (or None: nothing to
check, the line hides); that function returns a core.nerd_check.Check.
"""

from __future__ import annotations

from typing import Callable

from PyQt6 import sip
from PyQt6.QtCore import QThreadPool, QTimer
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from ..core.nerd_check import FAIL, INFO, OK, WARN, Check
from ..core.workers import run_async
from ..i18n import tr
from .theme import P

GLYPHS = {OK: "✓", WARN: "!", FAIL: "✗", INFO: "i"}

Job = Callable[[], "Callable[[], Check | None] | None"]


class CheckLine(QWidget):
    """*title*: what the problem window is called when this check fails.
    *test*: a Test button (a check that asks the network); otherwise it runs
    by itself, shortly after its setting changes. *preview*: with a Test
    button, what the line says until Test is pressed (and again shortly
    after the setting changes) - a quick look made on the window's thread,
    never one that waits on the network."""

    def __init__(self, job: Job, title: str, test: bool = False,
                 parent: QWidget | None = None, test_text: str = "",
                 preview: Callable[[], Check | None] | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("NerdCheck")
        self._job, self._title = job, title
        self._preview = preview if test else None
        self._test_text = test_text or tr("nerd_ck_test")
        self._seq = 0
        self._result: Check | None = None
        self._explicit = False
        self._started = False
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self.glyph = QLabel("")
        self.glyph.setFixedWidth(14)
        self.text = QLabel("")
        self.text.setWordWrap(True)
        self.details = QPushButton(tr("nerd_ck_details"))
        self.details.setFlat(True)
        self.details.setObjectName("NerdLink")
        self.details.setStyleSheet(f"color:{P['text2']}; text-decoration: underline;"
                                   " padding:0; border:none; background:transparent;")
        self.details.clicked.connect(self.show_problem)
        self.details.hide()
        h.addWidget(self.glyph)
        h.addWidget(self.text, 1)
        h.addWidget(self.details)
        self.test_btn: QPushButton | None = None
        if test:
            self.test_btn = QPushButton(self._test_text)
            self.test_btn.clicked.connect(lambda: self.run(explicit=True))
            h.addWidget(self.test_btn)
        self._soon = QTimer(self)
        self._soon.setSingleShot(True)
        self._soon.setInterval(600)
        self._soon.timeout.connect(self.run)
        self._look = QTimer(self)
        self._look.setSingleShot(True)
        self._look.setInterval(300)
        self._look.timeout.connect(self.show_preview)
        if self._preview is not None:
            self.show_preview()
        elif test:
            # A Test button is there from the start, so the test can be found.
            self._show(INFO, tr("nerd_ck_untested"))
        else:
            self.hide()

    # -- running -------------------------------------------------------------

    def changed(self, *_a) -> None:
        """Its setting changed: check again in a moment (a check that asks
        the network waits for Test, but says the last answer is old)."""
        if self._preview is not None:
            self._seq += 1          # a test still under way answers for the old setting
            if self.test_btn is not None:
                self.test_btn.setText(self._test_text)
            self._look.start()
            return
        if self.test_btn is not None:
            if self._result is not None:
                self._show(INFO, tr("nerd_ck_retest"))
            return
        self._soon.start()

    def show_preview(self) -> None:
        """What the setting comes to now, without asking the network."""
        self._look.stop()
        if self._preview is None:
            return
        try:
            result = self._preview()
        except Exception as e:     # a control in a state nobody foresaw
            self._result = None
            self._show(FAIL, tr("nerd_ck_error"), f"{type(e).__name__}: {e}")
            return
        self._result = result
        if result is None:
            self._show(INFO, tr("nerd_ck_untested"))
        else:
            self._show(result.state, tr(result.key, **result.params), result.detail)

    def start(self) -> None:
        """The first look, when the page is built."""
        if not self._started:
            self._started = True
            self.run()

    def run(self, explicit: bool = False) -> None:
        self._soon.stop()
        try:
            work = self._job()
        except Exception as e:     # a control in a state nobody foresaw
            work = None
            self._show(FAIL, tr("nerd_ck_error"), f"{type(e).__name__}: {e}")
            return
        if work is None:
            self._result = None
            if self.test_btn is None:
                self.hide()
            elif self._preview is not None:
                self.show_preview()
            else:
                self._show(INFO, tr("nerd_ck_nothing") if explicit else tr("nerd_ck_untested"))
            return
        self._seq += 1
        seq = self._seq
        self._explicit = explicit
        self._show(INFO, tr("nerd_ck_checking"))
        if self.test_btn is not None:
            self.test_btn.setText(tr("nerd_ck_testing"))

        def done(result: Check | None) -> None:
            # Settings may have closed while the check ran: its line is gone
            # then, and touching it would end the app.
            if sip.isdeleted(self) or seq != self._seq:
                return          # gone, or a newer check is under way
            self._finish(result)

        def failed(err=None) -> None:
            if not sip.isdeleted(self) and seq == self._seq:
                self._finish(Check(FAIL, "nerd_ck_error", {}, str(err or "")))
        run_async(QThreadPool.globalInstance(), work, done, failed)

    def _finish(self, result: Check | None) -> None:
        if self.test_btn is not None:
            self.test_btn.setText(self._test_text)
        self._result = result
        if result is None:
            if self.test_btn is None:
                self.hide()
            elif self._preview is not None:
                self.show_preview()
            else:
                self._show(INFO, tr("nerd_ck_untested"))
            return
        self._show(result.state, tr(result.key, **result.params), result.detail)
        if self._explicit and result.state == FAIL and result.detail:
            QTimer.singleShot(0, self.show_problem)

    # -- showing -------------------------------------------------------------

    def _show(self, state: str, text: str, detail: str = "") -> None:
        colour = {OK: P.get("ok", P["text2"]), WARN: P.get("warn", P["text2"]),
                  FAIL: P["error"]}.get(state, P["muted"])
        self.glyph.setText(GLYPHS.get(state, ""))
        self.glyph.setStyleSheet(f"color:{colour}; font-weight:800; font-size:13px;")
        self.text.setText(text)
        self.text.setStyleSheet(f"color:{colour if state in (FAIL, WARN) else P['text2']};"
                                " font-size:12px;")
        self.state = state
        self.details.setVisible(bool(detail) and state in (FAIL, WARN, OK))
        self.details.setText(tr("nerd_ck_details") if state != OK else tr("nerd_ck_what"))
        self.show()

    def show_problem(self) -> None:
        r = self._result
        if r is None:
            return
        from .dialogs import ProblemDialog
        title = self._title if r.state != OK else tr("nerd_ck_what_title", what=self._title)
        ProblemDialog(self.window(), title, tr(r.key, **r.params), r.detail or "-",
                      state=r.state).exec()

    @property
    def result(self) -> Check | None:
        return self._result
