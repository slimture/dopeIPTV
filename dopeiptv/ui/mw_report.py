"""Error reports and feedback in the window (core/report.py builds and
scrubs them): "Send feedback…" in the app menu, an offer to send a report
when something went wrong this run or the app went down last time - never
sent until the person has read it and pressed Send.

An error is offered after the code that raised it is done (a queued
signal: the hook runs inside the failing slot - a paint event, the
player's poll - and a window's own event loop there ran everything else
inside it), and never over a video: while one plays or the window is
fullscreen the report waits in the app menu, said by a note that opens it.

And the answers to feedback: asked of the site on one timer (soon after a
report that wants one, then less often), kept here, shown in a window of
their own - its menu entry counts the new ones and a dot on the menu says
so."""

from __future__ import annotations

import time
from functools import partial

from PyQt6 import sip
from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction

from ..core import report
from ..core.log import log, log_dir
from ..core.workers import run_async
from ..i18n import tr


class _Relay(QObject):
    """An error from any thread, onto the window's."""

    raised = pyqtSignal(str)


class _ReportMixin:
    _report_relay: _Relay | None = None
    _report_offered: set | None = None
    _report_waiting: list | None = None
    _report_wait_action = None
    _report_crash = ""

    def _report_init(self) -> None:
        self._report_offered = set()
        self._report_waiting = []
        self._report_relay = _Relay()
        # Queued: offered from the event loop once the failing code is
        # done, never from inside it (the error hook runs there).
        self._report_relay.raised.connect(self._offer_error_report,
                                          Qt.ConnectionType.QueuedConnection)
        report.add_listener(self._report_relay.raised.emit)
        # Answers to feedback: one timer, set to the next ask each time
        # (report.next_ask) and stopped while nothing is asked about; the
        # first a little after the start, not while the app is busy starting.
        self._answers_timer = QTimer(self)
        self._answers_timer.setSingleShot(True)
        self._answers_timer.timeout.connect(self._ask_answers)
        self._schedule_answers(self.ANSWERS_AT_START)
        self._show_unread()
        self._report_crash = report.pending_crash()
        if self._report_crash:
            QTimer.singleShot(4000, self._offer_crash_report)

    def _build_report_actions(self) -> dict[str, QAction]:
        """Send feedback…, Answers to feedback… and an error's report that
        waits (it came while a video played - there only while one does);
        app_menu places them."""
        feedback = QAction(tr("feedback_menu") + "…", self)
        feedback.triggered.connect(self.open_feedback)
        self._answers_action = QAction(self._answers_menu_text(), self)
        self._answers_action.triggered.connect(self._answers_from_menu)
        self._report_wait_action = QAction(tr("report_title") + "…", self)
        self._report_wait_action.setVisible(False)
        self._report_wait_action.triggered.connect(self.open_waiting_report)
        return {"feedback": feedback, "answers": self._answers_action,
                "report_wait": self._report_wait_action}

    def open_feedback(self, *_a) -> None:
        self.open_report("feedback")

    def _answers_from_menu(self, *_a) -> None:
        self.open_answers()

    def _personal_values(self) -> list[str]:
        store = getattr(self, "playlist_store", None)
        playlists = store.playlists() if store is not None else []
        channels = [str(it.get("name") or "") for it in (getattr(self, "all_items", None) or [])
                    if isinstance(it, dict)]
        history = getattr(self, "history", None)
        titles = []
        try:
            titles = [str(h.get("title") or h.get("name") or "") for h in (history.items() if history else [])]
        except Exception:
            pass
        last = getattr(self, "_last_playback", None) or {}
        titles.append(str(last.get("title") or ""))
        return report.personal_values(playlists, channels, titles)

    # -- answers to feedback ---------------------------------------------------

    #: Seconds after the start before the first ask; the least time between
    #: two asks the person starts (Check now, opening the window).
    ANSWERS_AT_START, ANSWERS_GAP = 45.0, 20.0
    _answers_timer: QTimer | None = None
    _answers_action: QAction | None = None
    _answers_menu = None
    _answers_dialog = None
    _answers_dot = None
    _answers_busy = False
    _answers_last = 0.0
    _answers_ok = True

    def _schedule_answers(self, at_least: float = 0.0) -> None:
        """The one timer set to the next ask; stopped while no report is
        asked about (nothing is asked then at all)."""
        timer = self._answers_timer
        settings = getattr(self, "settings", None)
        if timer is None or settings is None:
            return
        wait = report.next_ask(report.tickets(settings), time.time(), self._answers_last)
        if wait is None:
            timer.stop()
            return
        timer.start(int(max(wait, at_least, 1.0) * 1000))

    def _ask_answers(self) -> None:
        """Ask the site for answers to the reports still asked about - off
        the window's thread, one request at a time."""
        settings = getattr(self, "settings", None)
        if settings is None or self._answers_busy:
            return
        now = time.time()
        codes = report.open_codes(report.tickets(settings), now)
        if not codes:
            self._schedule_answers()
            return
        self._answers_busy = True
        self._answers_last = now
        dlg = self._answers_window()
        if dlg is not None:
            dlg.show_checking()
        run_async(self.pool, partial(report.fetch_replies, codes), self._answers_fetched,
                  self._answers_failed)

    def _answers_failed(self, *_e) -> None:
        self._answers_fetched(None)

    def _answers_fetched(self, got) -> None:
        self._answers_busy = False
        self._answers_ok = got is not None
        settings = getattr(self, "settings", None)
        fresh = 0
        if got and settings is not None:
            items = report.tickets(settings)
            fresh = report.merge_replies(items, got)
            if fresh:
                report.save_tickets(settings, items)
        dlg = self._answers_window()
        if dlg is not None:
            if fresh:
                # In front of the person: shown, so no longer new.
                dlg.set_tickets(report.tickets(settings))
                self._answers_mark_seen()
            dlg.show_result(self._answers_ok, fresh, self._answers_last)
        elif fresh:
            self._show_action_toast(tr("fbr_new"), 12_000, self._answers_from_menu)
        self._show_unread()
        self._schedule_answers()

    def check_answers_now(self) -> None:
        """Check now (and the window opening): asked at once - unless a
        request is out already, or one went moments ago and its word stands."""
        if self._answers_busy:
            return
        if time.time() - self._answers_last < self.ANSWERS_GAP:
            dlg = self._answers_window()
            if dlg is not None:
                dlg.show_result(self._answers_ok, 0, self._answers_last)
            return
        self._ask_answers()

    def answers_unread(self) -> int:
        settings = getattr(self, "settings", None)
        return report.unread(report.tickets(settings)) if settings is not None else 0

    def _answers_menu_text(self) -> str:
        n = self.answers_unread()
        return tr("fba_menu") + (f" ({n})" if n else "") + "…"

    def _show_unread(self) -> None:
        """The menu entry's count, and a dot on the menu that holds it."""
        n = self.answers_unread()
        if self._answers_action is not None:
            self._answers_action.setText(self._answers_menu_text())
        dot = self._answers_dot
        if dot is None and n and self._answers_menu is not None:
            bar = self.menuBar()
            # A desktop's own menu bar (macOS, a global menu) has no room for it.
            if not bar.isNativeMenuBar():
                from .widgets import MenuBarDot
                dot = self._answers_dot = MenuBarDot(bar, self._answers_menu.menuAction())
        if dot is not None and not sip.isdeleted(dot):
            dot.set_on(bool(n))

    def _answers_window(self):
        dlg = self._answers_dialog
        if dlg is None or sip.isdeleted(dlg) or not dlg.isVisible():
            return None
        return dlg

    def _answers_mark_seen(self) -> None:
        settings = getattr(self, "settings", None)
        if settings is None:
            return
        items = report.tickets(settings)
        if report.mark_seen(items):
            report.save_tickets(settings, items)
        self._show_unread()

    def open_answers(self, parent=None):
        """The answers window - over *parent* when it comes from another
        window (Send feedback, Settings: one of the main window's would wait
        behind those). Opening it shows the answers, no longer new, and asks
        the site again."""
        from .dialogs import AnswersDialog
        settings = getattr(self, "settings", None)
        if settings is None:
            return None
        dlg = self._answers_window()
        if dlg is not None and (parent is None or dlg.parent() is parent):
            dlg.raise_()
            dlg.activateWindow()
            return dlg
        if dlg is not None:
            dlg.close()
        items = report.tickets(settings)
        dlg = AnswersDialog(parent or self, items, check=self.check_answers_now, send=self._answers_send)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._answers_dialog = dlg
        self._answers_mark_seen()
        dlg.show()
        if report.open_codes(items, time.time()):
            self.check_answers_now()
        return dlg

    def _answers_send(self) -> None:
        """The empty window's Send feedback…: it goes, the feedback window comes."""
        dlg = self._answers_window()
        if dlg is not None:
            dlg.close()
        QTimer.singleShot(0, self.open_feedback)

    def _answered(self) -> list[dict]:
        settings = getattr(self, "settings", None)
        return [t for t in report.tickets(settings) if t.get("r")] if settings is not None else []

    #: Offers of an error report in one run: a storm of errors is one problem.
    REPORT_OFFERS = 3

    def _offer_error_report(self, trace: str) -> None:
        key = (trace.strip().splitlines() or [""])[-1][:200]
        if self._report_offered is None or key in self._report_offered \
                or len(self._report_offered) >= self.REPORT_OFFERS:
            return
        self._report_offered.add(key)
        self._offer_report("error", trace, "report_error_intro")

    def _offer_crash_report(self) -> None:
        crash, self._report_crash = getattr(self, "_report_crash", ""), ""
        if crash:
            self._offer_report("crash", crash, "report_crash_intro")

    def _offer_report(self, kind: str, trace: str, intro: str) -> None:
        if not self._report_would_interrupt():
            self.open_report(kind, trace, tr(intro))
            return
        # Not over a video: it waits in the menu; the note opens it.
        if self._report_waiting is None:
            self._report_waiting = []
        self._report_waiting.append((kind, trace, intro))
        if self._report_wait_action is not None:
            self._report_wait_action.setVisible(True)
        self._show_action_toast(tr("report_waiting"), 10_000, self.open_waiting_report)
        log.info("report: the %s report waits in the menu (a video is on)", kind)

    def _report_would_interrupt(self) -> bool:
        """A video plays (here, popped out or in multiview) or the window
        is fullscreen: a window of questions now would stop the person
        watching."""
        if self.isFullScreen() or getattr(self, "_player_fs", False):
            return True
        pop = getattr(self, "_popout_win", None)
        if pop is not None and pop.isFullScreen():
            return True
        if getattr(getattr(self, "player", None), "current_url", None):
            return True
        mv = getattr(self, "_multiview_win", None)
        return mv is not None and any(getattr(c, "url", None) for c in getattr(mv, "cells", []))

    def open_waiting_report(self) -> None:
        """The report of an error that came while a video played - the
        oldest first, the menu entry gone with the last."""
        waiting = self._report_waiting or []
        kind, trace, intro = waiting.pop(0) if waiting else ("", "", "")
        if self._report_wait_action is not None:
            self._report_wait_action.setVisible(bool(waiting))
        if trace:
            self.open_report(kind, trace, tr(intro))

    def _feedback_details(self, opts: dict) -> str:
        """What feedback carries besides the person's words, as they chose:
        of the logs only the part that matters (report.relevant_log) - the
        warnings and errors, the lines of where it went wrong, what happened
        last."""
        from ..core.log import log_file, recent_lines
        area = str(opts.get("area") or "")
        parts: list[str] = []
        if opts.get("versions"):
            parts += report.system_lines()
        if opts.get("source"):
            store = getattr(self, "playlist_store", None)
            kinds = sorted({str(p.get("kind") or "xtream") for p in (store.playlists() if store else [])})
            parts.append("Sources: " + (", ".join(kinds) or "none"))
        what = f"warnings, errors, {area} and the last lines" if area in report.AREA_WORDS \
            else "warnings, errors and the last lines"
        if opts.get("log"):
            run = recent_lines(300)
            picked = report.relevant_log(run, area)
            parts += ["", f"Log from this run ({what}; {len(picked)} of {len(run)} lines):", *picked]
        if opts.get("file"):
            try:
                with open(log_file(), encoding="utf-8", errors="replace") as fh:
                    tail = fh.read().splitlines()[-2000:]
            except OSError:
                tail = []
            picked = report.relevant_log(tail, area, most=100, tail=10)
            if picked:
                parts += ["", f"Log file from earlier runs ({what}; {len(picked)} lines):", *picked]
        return report.scrub("\n".join(parts), self._personal_values())

    def open_report(self, kind: str, trace: str = "", intro: str = "") -> None:
        """The report window, filled in and scrubbed; sent on Send only."""
        import os

        from ..core.log import log_file
        from .dialogs import FeedbackDialog, ReportDialog
        fields: dict = {}
        words = code = first_words = ""
        settings = getattr(self, "settings", None)
        if kind == "feedback":
            import time
            sent = report.sent_times(settings.value("feedback_sent", "") if settings else "")
            wait = report.feedback_wait(sent, time.time())
            if wait:
                self._show_toast(tr("fb_wait" if wait == "wait" else "fb_enough"), 6000)
                return
            answered = self._answered()
            fd = FeedbackDialog(self, self._feedback_details, has_log_file=os.path.isfile(log_file()),
                                answers=answered, all_answers=self.open_answers)
            if answered:                               # shown now: no longer new
                self._answers_mark_seen()
            if not fd.exec():
                return
            fields = fd.fields()
            # A supporter's feedback is read first: only "yes" goes, never the key.
            if self._supporter_now():
                fields["supporter"] = "yes"
            if fd.answers.isChecked():
                code, fields["reply"] = report.new_code()
            payload = report.scrub(fd.payload(), self._personal_values())
            words = report.text_hash(fd.message.toPlainText())
            first_words = " ".join(report.scrub(fd.message.toPlainText(), self._personal_values()).split())[:90]
            if settings is not None and words == str(settings.value("feedback_last", "") or ""):
                self._show_toast(tr("fb_same"), 5000)
                return
        else:
            text = report.build(kind, trace=trace, personal=self._personal_values(), with_log=True)
            d = ReportDialog(self, kind, text, intro)
            if not d.exec():
                return
            payload = report.scrub(d.payload(), self._personal_values())
        if not payload.strip():
            return

        def done(ok) -> None:
            if ok:
                if kind == "feedback" and settings is not None:
                    import time
                    now = time.time()
                    keep = [t for t in report.sent_times(settings.value("feedback_sent", "")) if now - t < 86400]
                    settings.setValue("feedback_sent", ",".join(str(int(t)) for t in [*keep, now]))
                    settings.setValue("feedback_last", words)
                    if code:
                        report.save_tickets(settings, [*report.tickets(settings),
                                                       {"c": code, "t": int(now), "w": first_words, "seen": 0}])
                        self._schedule_answers()        # two minutes from now
                self._show_toast(tr("report_sent"), 5000)
                return
            from PyQt6.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText(payload)
            self._show_toast(tr("report_not_sent"), 9000)
        run_async(self.pool, lambda: report.send(kind, payload, extra=fields), done, lambda _e=None: done(False))
        log.info("report: %s sent by the person", kind)

    @staticmethod
    def _report_folder() -> str:
        return log_dir()
