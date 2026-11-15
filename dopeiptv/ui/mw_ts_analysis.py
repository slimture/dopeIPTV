"""Nerd's stream analysis in the window: "Stream analysis…" in the player's
options menu and in the menu of the channel that plays (while Nerd is on),
its window (ui/ts_analysis_dialog.py), and what it reads.

It reads the live channel from the app's own rewind buffer (the player's
DvrSession) - never a second connection to the account - with a Runner
(core/ts_analysis.py) of its own, on its own thread, started while the
window is open and stopped when it closes or the channel changes. Without
the buffer the window says why there is nothing to read: an HLS channel,
the buffer turned off, a film or a file."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from PyQt6 import sip

from ..core import nerd
from ..core.log import log
from ..i18n import tr

#: Seconds without a byte from the buffer before the window says it stalls.
STALL = 3.0


class _TsAnalysisMixin:
    _tsa_window: Any = None
    _tsa_runner: Any = None
    _tsa_store: Any = None

    # -- where it is offered -----------------------------------------------------

    @staticmethod
    def _tsa_offered() -> bool:
        return nerd.current().on

    def _tsa_menu_text(self) -> str:
        """The player's options menu: its entry while Nerd is on ("" hides it)."""
        return tr("tsa_menu") if self._tsa_offered() else ""

    def _tsa_channel_menu(self, menu: Any, it: dict) -> None:
        """The channel menu of the channel that plays, while Nerd is on."""
        if not self._tsa_offered():
            return
        try:
            playing = (getattr(self, "_playing_group", None) == "live"
                       and self._item_key(it) == getattr(self, "_playing_key", None))
        except Exception:
            playing = False
        if playing:
            menu.addAction(tr("tsa_menu"), self.open_ts_analysis)

    # -- the window ----------------------------------------------------------------

    def open_ts_analysis(self, *_a) -> None:
        w = self._tsa_window
        if w is not None and not sip.isdeleted(w):
            w.show()
            w.raise_()
            w.activateWindow()
            return
        from .ts_analysis_dialog import TsAnalysisDialog
        w = TsAnalysisDialog(self, self._tsa_poll, self._tsa_reset, self._tsa_scrub)
        w.finished.connect(self._tsa_closed)
        self._tsa_window = w
        w.show()

    def _tsa_closed(self, *_a) -> None:
        self._tsa_stop()
        w, self._tsa_window = self._tsa_window, None
        if w is not None and not sip.isdeleted(w):
            w.deleteLater()

    def _tsa_quit(self) -> None:
        """The app ends: the window and its reader with it."""
        w = self._tsa_window
        if w is not None and not sip.isdeleted(w):
            w.close()
        self._tsa_stop()

    def _tsa_stop(self) -> None:
        r, self._tsa_runner = self._tsa_runner, None
        self._tsa_store = None
        if r is not None:
            r.stop()

    def _tsa_reset(self) -> None:
        r = self._tsa_runner
        if r is not None:
            r.reset()

    def _tsa_scrub(self, text: str) -> str:
        from ..core import report
        try:
            personal = self._personal_values()
        except Exception:
            personal = []
        return report.scrub(text, personal)

    # -- what plays, and the reader ------------------------------------------------------

    def _tsa_status(self) -> tuple[str, Any]:
        """(status, the buffer's store when there is one to read)."""
        p = getattr(self, "player", None)
        url = str(getattr(p, "current_url", None) or "") if p is not None else ""
        if not url:
            return "idle", None
        lp = getattr(self, "_last_playback", None) or {}
        if lp.get("kind") != "live" or getattr(self, "_playing_catchup", False):
            return "not_live", None
        session = getattr(p, "_dvr", None)
        if session is None or getattr(session, "closed", True):
            if urlparse(url).path.lower().endswith((".m3u8", ".m3u")) or ".m3u8" in url.lower():
                return "hls", None
            settings = getattr(self, "settings", None)
            kept = settings is not None and settings.value("live_rewind_kept", "false") == "true"
            return ("not_buffered" if kept else "need_buffer"), None
        return "running", session.store

    def _tsa_poll(self) -> tuple[str, Any, float]:
        """The window's once a second: (status, snapshot, seconds without bytes)."""
        try:
            status, store = self._tsa_status()
        except Exception:
            log.debug("TS analysis: status", exc_info=True)
            status, store = "idle", None
        if store is None:
            self._tsa_stop()
            return status, None, 0.0
        r = self._tsa_runner
        if r is None or self._tsa_store is not store or (r.ended and not store.closed):
            self._tsa_stop()
            from ..core.ts_analysis import Runner
            r = Runner(store)
            self._tsa_runner, self._tsa_store = r, store
            r.start()
            log.info("TS analysis: reading the channel's rewind buffer")
        snap = r.latest
        idle = r.idle()
        if snap is None or snap.state == "waiting":
            return "waiting", snap, idle
        if snap.state != "running":
            return snap.state, snap, idle
        return ("stalled" if idle > STALL else "running"), snap, idle
