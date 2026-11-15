"""Nerd's backup addresses in the window (core/mirrors.py): a live channel
that would not play, on a server that cannot be reached or answers 5xx, is
asked of the source's next address (the client's stream_failover, off the
window's thread) - and when one serves it, the channel starts again from
there. The lists, the guide and catch-up follow the client by themselves.
"""

from __future__ import annotations

from ..core import mirrors
from ..core.log import log
from ..core.workers import run_async
from ..i18n import tr


class _MirrorsMixin:

    def _mirror_probe_said(self, url: str | None, code: int | None) -> None:
        """(window's thread) The first look at a live stream that did not
        play answered *code* (None: its server could not be reached)."""
        client = getattr(self, "client", None)
        look = getattr(client, "stream_failover", None)
        picker = getattr(client, "picker", None)
        if (look is None or not url or picker is None or len(picker.addresses) < 2
                or (code is not None and not mirrors.fails_over_status(code))
                or getattr(self, "_mirror_looking", False)):
            return
        self._mirror_looking = True
        self._mirror_for = ((getattr(self, "_last_playback", None) or {}).get("key"), client)
        log.info("backup addresses: the stream's server did not answer (%s) - asking the next",
                 code if code is not None else "unreachable")
        run_async(self.pool, look, self._mirror_found, self._mirror_failed, url, code)

    def _mirror_failed(self, _err=None) -> None:
        self._mirror_looking = False

    def _mirror_found(self, base: str | None) -> None:
        self._mirror_looking = False
        key, client = getattr(self, "_mirror_for", (None, None))
        if not base or client is not getattr(self, "client", None):
            return
        self._show_toast(tr("mirror_switched", host=mirrors.shown(base)), 6000)
        lp = getattr(self, "_last_playback", None) or {}
        p = getattr(self, "player", None)
        if lp.get("key") != key or lp.get("kind") != "live" or (p is not None and p.current_url):
            return          # another channel since, or it plays after all
        # The channel again, from the address that answered: a fresh start,
        # as a click is.
        self._stream_retries = 0
        self._diag_shown = False
        self._retry_last_stream()
