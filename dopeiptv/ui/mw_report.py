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

    def _offer_error_report(self, trace: str) -> None:
        key = (trace.strip().splitlines() or [""])[-1][:200]
        if self._report_offered is None or key in self._report_offered:
            return
        self._report_offered.add(key)
        self.open_report("error", trace, tr("report_error_intro"))

    def open_report(self, kind: str, trace: str = "", intro: str = "") -> None:
        """The report window, filled in and scrubbed; sent on Send only."""
        from .dialogs import ReportDialog
        text = report.build(kind, trace=trace, personal=self._personal_values(),
                            with_log=True)
        d = ReportDialog(self, kind, text, intro)
        if not d.exec():
            return
        payload = report.scrub(d.payload(), self._personal_values())
        if not payload.strip():
            return

        def done(ok) -> None:
            if ok:
                self._show_toast(tr("report_sent"), 5000)
                return
            from PyQt6.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText(payload)
            self._show_toast(tr("report_not_sent"), 9000)
        run_async(self.pool, lambda: report.send(kind, payload), done, lambda _e=None: done(False))
        log.info("report: %s sent by the person", kind)

    @staticmethod
    def _report_folder() -> str:
        return log_dir()
