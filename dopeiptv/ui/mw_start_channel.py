"""Start on the last channel, in the window (MainWindow mixin; what it
decides with is core/start_channel.py).

Once, as the app starts: armed at the end of the window's ``__init__``
(``_start_channel_arm``) and tried a moment later - never before who is
watching is said: while "Who's watching?" is up (the window's picker, TV
mode's screen of it, or a profile's PIN still to be given) it waits, and the
profile picked is the one whose switch and History count. Then, only when
that profile's switch is on and nothing has played yet this session: the
newest live channel of its History on the source in use, looked up among
the source's channels as the lists show them now (``_chno_rows``: none of a
hidden or locked category, none hidden by hand, nothing a kids profile may
not see) off the window's thread, and played through the window's one path
(``play_live_channel`` - the account's connections, server mode's sharing
and every question as on a click; the middle pane stays on the place the app
opened on, ``from_pane``). Not found, nothing plays and only the log says
why.

A command wins: the app started with one (``--play``, ``--channel``, a
dopeiptv:// link - app.py says so, ``_start_channel_skip``), or one handed
over before the channel came (mw_commands). So does anything the person, the
phone remote or the remote API plays first. The first run's welcome, a
window with no source and a source or profile changed while the channel was
looked up play nothing.
"""

from __future__ import annotations

from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import QTimer

from ..core import start_channel
from ..core.log import log
from ..core.workers import run_async
from .mw_commands import _rows_job
from .mw_pane_follow import from_pane

#: The first try, once the window is up and its first list asked for.
FIRST_MS = 800
#: How often it looks again while "Who's watching?" waits, and for how long
#: in all before it lets the start be.
WAIT_MS = 1000
WAIT_TRIES = 30 * 60


class _StartChannelMixin:
    """MainWindow mixin: the last channel played again as the app starts."""

    #: "" while it may still come; else why it will not (or "played").
    _sc_over = ""
    _sc_tries = 0

    def _start_channel_arm(self) -> None:
        """At the end of the window's start: the try a moment later."""
        self._sc_over = ""
        self._sc_tries = 0
        QTimer.singleShot(FIRST_MS, self._start_channel_try)

    def _start_channel_skip(self, why: str) -> None:
        """Nothing is played at the start after all (*why*: a command, a
        playback that came first) - also while the channel is looked up."""
        if self._sc_over in ("", "looking"):
            self._sc_over = why
            log.debug("start: the last channel not played - %s", why)

    def _start_channel_played_yet(self) -> bool:
        """Whether anything plays or played this session already (here or
        on a TV)."""
        if getattr(self, "_last_playback", None) is not None:
            return True
        p = getattr(self, "player", None)
        if p is not None and getattr(p, "current_url", None):
            return True
        casting = getattr(self, "_cast_runs", None)
        return bool(callable(casting) and casting())

    def _start_channel_waits(self) -> bool:
        """Whether who is watching is still to be said: the picker over the
        window, TV mode's screen of it, or a profile's PIN at start."""
        if getattr(self, "_profile_must_pick", False):
            return True
        if getattr(self, "_profile_picker", None) is not None:
            return True
        page = getattr(self, "_tv_page", None)
        return bool(getattr(self, "_tv_on", False) and page is not None
                    and not sip.isdeleted(page) and getattr(page, "current", "") == "profiles")

    def _start_channel_try(self) -> None:
        if sip.isdeleted(self) or self._sc_over:
            return
        try:
            self._start_channel_look()
        except Exception:               # never in the way of the start
            self._sc_over = "failed"
            log.warning("start: the last channel not played", exc_info=True)

    def _start_channel_look(self) -> None:
        if self._start_channel_played_yet():
            self._start_channel_skip("something plays already")
            return
        if self._start_channel_waits():
            self._sc_tries += 1
            if self._sc_tries > WAIT_TRIES:
                self._start_channel_skip("who is watching was not said")
                return
            QTimer.singleShot(WAIT_MS, self._start_channel_try)
            return
        if not start_channel.on(self.settings):
            self._sc_over = "off"
            return
        welcome = getattr(self, "_welcome", None)
        store = getattr(self, "playlist_store", None)
        if (welcome is not None and welcome.isVisible()) or store is None \
                or store.active() is None:
            self._start_channel_skip("no source in use")
            return
        entry = start_channel.last_live(self.history.items())
        if entry is None:
            self._start_channel_skip("History holds no channel")
            return
        self._sc_over = "looking"
        client, gen = self.client, getattr(self, "_profile_gen", 0)

        def found(rows: Any) -> None:
            if sip.isdeleted(self):
                return
            if client is not self.client or gen != getattr(self, "_profile_gen", 0):
                log.info("start: the last channel not played - the source or the profile "
                         "changed meanwhile")
                return
            if self._start_channel_played_yet() or self._sc_over != "looking":
                return
            if getattr(self, "_profile_locked", lambda: False)():
                return
            it = start_channel.find(self._chno_rows(list(rows or [])), entry)
            if it is None:
                self._sc_over = "not found"
                log.info("start: the last channel is not among the channels the lists "
                         "show now - nothing played")
                return
            self._sc_over = "played"
            log.info("start: the last channel played again")
            self._start_channel_play(it)

        def failed(_e: Any = None) -> None:
            if not sip.isdeleted(self) and self._sc_over == "looking":
                self._sc_over = "not read"
                log.info("start: the source's channels could not be read - nothing played")

        run_async(self.pool, _rows_job(self, client), found, failed)

    @from_pane
    def _start_channel_play(self, it: dict) -> None:
        """Played as the channel is from anywhere: the window's one path."""
        self.play_live_channel(it)
