"""The window closing while it records (MainWindow mixin; what it decides
with is core/rec_close.py).

**Carried on.** When a ``dopeiptv --server`` runs already - its presence in
the recordings' claims says so (core/rec_claims.py: ``server.alive``) - a
scheduled recording the window records now goes on there
(``_rec_close_carry``): closeEvent hands the ids to the recorder's shutdown,
which ends each one's connection here and puts the job back on the list
with where it left off; only at the very end of the closing, once the
player, its rewind buffers and a cast have let go, are their claims let go
(``_rec_close_let_go``) - the server then opens its own connection and
records the rest into a recording of its own. A server is never started.
Any failure leaves the recording to stop as it always did.

**Asked.** When closing would stop a recording running here, or miss one
due within the next 12 hours that no server takes (core/rec_close.at_stake),
the window asks first - calmly, on this computer only (never the phone's
question; in TV mode TV mode's own, large): recordings are made only while
dopeIPTV is open; "Keep dopeIPTV open" (the default; Escape and closing the
question too) or "Quit anyway". No "Don't ask again": the recordings'
other questions have none. Not asked when the closing is the app's own
(Reset all, a restore, an update's restart, a settings file that cannot be
kept private, Ctrl+C in the terminal: ``_close_forced``).
"""

from __future__ import annotations

import time

from ..core import privacy, rec_claims, rec_close
from ..core.log import log
from ..core.recording import RecordingManager
from ..i18n import tr


def _has_address(j: dict) -> bool:
    return bool(j.get("url")) or RecordingManager.address_later(j)


class _RecCloseMixin:
    """MainWindow mixin: recordings as the window closes."""

    #: The closing is the app's own: nothing asked (the recordings are still
    #: carried on to a server that runs).
    _close_forced = False
    _rec_close_asking = False

    def _rec_close_server(self) -> bool:
        """Whether a ``dopeiptv --server`` runs that records what the window
        leaves (its presence among the recordings' claims)."""
        rec = getattr(self, "rec", None)
        claims = getattr(rec, "claims", None)
        if claims is None or getattr(rec, "_no_claims", ""):
            return False
        try:
            return bool(claims.present(rec_claims.SERVER))
        except Exception as e:
            log.debug("closing: whether a server runs not known: %s", e)
            return False

    def _rec_close_takes(self, j: dict) -> bool:
        """Whether the server takes recording *j*: one of the household's
        sources' (services/server_recorder.py takes no other)."""
        pid = str(j.get("source") or "")
        store = getattr(self, "playlist_store", None)
        pl = store.get(pid) if pid and store is not None else None
        return bool(pl) and pl.get("kind") != "local"

    def _rec_close_carry(self) -> list[str]:
        """The recordings a server that runs carries on as the window
        closes - none after Reset all or with a restore to come (the app's
        settings are about to change under it)."""
        if getattr(self, "_resetting", False) or not self._rec_close_server():
            return []
        try:
            rec = getattr(self, "rec", None)
            now = time.time()
            private = privacy.playing_private()
            return [str(j["id"]) for j in list(getattr(rec, "jobs", None) or [])
                    if rec_close.carriable(j, now, self._rec_close_takes, _has_address,
                                           private=bool(j.get("inplayer")) and private)]
        except Exception:               # ended as before, then
            log.warning("closing: the recordings to carry on not looked at", exc_info=True)
            return []

    def _rec_close_go_on(self) -> bool:
        """Before anything of the window is let go: True to close now, False
        to stay open (the person said so). Asked only when closing would
        stop or miss a recording; never raises."""
        if getattr(self, "_resetting", False) or self._close_forced:
            return True
        if self._rec_close_asking:
            return False                # asked already: the answer decides
        try:
            rec = getattr(self, "rec", None)
            server = self._rec_close_server()
            carried = self._rec_close_carry() if server else []
            stake = rec_close.at_stake(list(getattr(rec, "jobs", None) or []), time.time(),
                                       server, self._rec_close_takes, carried)
        except Exception:
            log.warning("closing: the recordings not looked at", exc_info=True)
            return True
        if not stake.any:
            return True
        words = tr("rec_close_running" if stake.running else "rec_close_due")
        if not server:
            words += "\n\n" + tr("rec_close_server")
        self._rec_close_asking = True
        # The computer's own question: never the phone's (mw_phone_rec).
        phone = self.__dict__.pop("_phone_ask_hook", None)
        try:
            idx = self._choice_dialog(tr("rec_close_title"), words,
                                      [(tr("rec_close_keep"), "primary"),
                                       (tr("rec_close_quit"), "danger")])
        except Exception:
            log.warning("closing: the question about the recordings not asked", exc_info=True)
            idx = 1
        finally:
            self._rec_close_asking = False
            if phone is not None:
                self._phone_ask_hook = phone
        go = idx == 1
        log.info("closing: recordings %s - %s", "running" if stake.running else "due",
                 "quit anyway" if go else "kept open")
        return go

    def _rec_close_let_go(self) -> None:
        """Last in the closing: what the server carries on let go of, now
        that nothing of the window's reads the stream."""
        rec = getattr(self, "rec", None)
        let_go = getattr(rec, "let_go_carried", None)
        if callable(let_go):
            try:
                let_go()
            except Exception:
                log.warning("closing: the carried recordings not let go", exc_info=True)
