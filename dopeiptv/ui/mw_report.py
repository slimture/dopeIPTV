"""Error reports and feedback in the window (core/report.py builds and
scrubs them): "Send feedback…" in the app menu, an offer to send a report
when something went wrong this run or the app went down last time - never
sent until the person has read it and pressed Send."""

from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

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

    def _report_init(self) -> None:
        self._report_offered = set()
        self._report_relay = _Relay()
        self._report_relay.raised.connect(self._offer_error_report)
        report.add_listener(self._report_relay.raised.emit)
        crash = report.pending_crash()
        if crash:
            QTimer.singleShot(4000, lambda: self.open_report("crash", crash, tr("report_crash_intro")))

    def _build_report_action(self, menu):
        action = menu.addAction(tr("feedback_menu") + "…")
        action.triggered.connect(lambda: self.open_report("feedback"))
        return action

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

    #: Offers of an error report in one run: a storm of errors is one problem.
    REPORT_OFFERS = 3

    def _offer_error_report(self, trace: str) -> None:
        key = (trace.strip().splitlines() or [""])[-1][:200]
        if self._report_offered is None or key in self._report_offered \
                or len(self._report_offered) >= self.REPORT_OFFERS:
            return
        self._report_offered.add(key)
        self.open_report("error", trace, tr("report_error_intro"))

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
        words = ""
        settings = getattr(self, "settings", None)
        if kind == "feedback":
            import time
            sent = report.sent_times(settings.value("feedback_sent", "") if settings else "")
            wait = report.feedback_wait(sent, time.time())
            if wait:
                self._show_toast(tr("fb_wait" if wait == "wait" else "fb_enough"), 6000)
                return
            fd = FeedbackDialog(self, self._feedback_details, has_log_file=os.path.isfile(log_file()))
            if not fd.exec():
                return
            fields = fd.fields()
            payload = report.scrub(fd.payload(), self._personal_values())
            words = report.text_hash(fd.message.toPlainText())
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
