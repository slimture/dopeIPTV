"""Teletext in the window: the viewer (View menu, the player's options
menu, T while a live channel plays) and where its pages come from.

The pages are read from the app's own rewind buffer for the channel
playing (core/teletext.StoreFeed on a thread of its own) - never from a
second connection to the account, which may allow only one. Without the
buffer the viewer says it is needed, with the way to Settings. Each
channel's pages are kept for the session (a few channels, the newest), so
going back to one shows its pages at once; nothing is written to disk.
"""
from __future__ import annotations

import weakref
from collections import OrderedDict
from typing import Any

from ..core import teletext as ttx
from ..core.log import log
from ..i18n import tr


class _TeletextMixin:
    #: Channels whose pages are kept for the session.
    TTX_KEEP = 6
    _ttx_view = None
    _ttx_feed: ttx.StoreFeed | None = None
    _ttx_feed_key: str | None = None
    _ttx_feed_store: Any = None

    def _ttx_decoders(self) -> "OrderedDict[str, ttx.Decoder]":
        d = getattr(self, "_ttx_cache", None)
        if d is None:
            d = self._ttx_cache = OrderedDict()
        return d

    def _ttx_seqs(self) -> dict[str, tuple[Any, int]]:
        """Each channel's buffer and the last segment read from it."""
        d = getattr(self, "_ttx_last_seq", None)
        if d is None:
            d = self._ttx_last_seq = {}
        return d

    # -- the viewer ---------------------------------------------------------------

    def _ttx_live_playing(self) -> bool:
        p = getattr(self, "player", None)
        return bool(p is not None and p.current_url and getattr(p, "_ttx_live", False))

    def open_teletext(self, *_args) -> None:
        """The teletext viewer for the channel playing (raised when open)."""
        from .teletext_view import TeletextDialog
        view = self._ttx_view
        if view is not None:
            try:
                view.show()
                view.raise_()
                view.activateWindow()
                return
            except RuntimeError:
                self._ttx_view = None
        view = TeletextDialog(self, self)
        view.finished.connect(self._ttx_view_finished)
        self._ttx_view = view
        view.show()

    def toggle_teletext(self) -> None:
        """T: the viewer opens while a live channel plays, and closes."""
        view = self._ttx_view
        if view is not None:
            try:
                if view.isVisible():
                    view.close()
                    return
            except RuntimeError:
                self._ttx_view = None
        if not self._ttx_live_playing():
            flash = getattr(self, "_flash_status", None)
            if callable(flash):
                flash(tr("ttx_not_live"))
            return
        self.open_teletext()

    def _ttx_view_finished(self, _result: int = 0) -> None:
        view, self._ttx_view = self._ttx_view, None
        if view is not None:
            try:
                view.deleteLater()
            except RuntimeError:
                pass
        self._ttx_stop_feed()

    def _ttx_menu_state(self) -> None:
        """The View menu's "Teletext…": only while a live channel plays."""
        act = getattr(self, "_teletext_action", None)
        if act is not None:
            act.setEnabled(self._ttx_live_playing())

    def _ttx_key_text(self) -> str:
        """The viewer's key as this system writes it, for the player's menu."""
        from PyQt6.QtGui import QKeySequence
        seq = self.shortcut_sequence("teletext")
        return QKeySequence(seq).toString(QKeySequence.SequenceFormat.NativeText) if seq else ""

    # -- the source the viewer asks -------------------------------------------------

    def teletext_state(self) -> tuple[str, ttx.Decoder | None, str]:
        """("ok" / "no_teletext" / "error" / "no_buffer" / "not_live", the
        channel's pages, its name) - and the buffer read for the channel
        playing now, the one before let go."""
        p = getattr(self, "player", None)
        if not self._ttx_live_playing() or p is None:
            self._ttx_stop_feed()
            return "not_live", None, ""
        title = p.title_lbl.text() if hasattr(p, "title_lbl") else ""
        key = p._chan_key or p.current_url
        session = getattr(p, "_dvr", None)
        if session is None:
            self._ttx_stop_feed()
            return "no_buffer", None, title
        decoders = self._ttx_decoders()
        decoder = decoders.get(key)
        if decoder is None:
            decoder = decoders[key] = ttx.Decoder()
            while len(decoders) > self.TTX_KEEP:
                old, _ = decoders.popitem(last=False)
                self._ttx_seqs().pop(old, None)
        else:
            decoders.move_to_end(key)
        feed = self._ttx_feed
        if feed is None or self._ttx_feed_key != key or self._ttx_feed_store is not session.store \
                or not feed.running:
            self._ttx_start_feed(key, session.store, decoder)
            feed = self._ttx_feed
        if feed is not None and feed.error:
            return "error", decoder, title
        if feed is not None and feed.no_teletext() and not decoder.packets:
            return "no_teletext", decoder, title
        return "ok", decoder, title

    def _ttx_start_feed(self, key: str, store: Any, decoder: ttx.Decoder) -> None:
        self._ttx_stop_feed()
        # Read on from where this channel's buffer was left, if it is the
        # same buffer: its history again would put older pages back.
        kept = self._ttx_seqs().get(key)
        start = kept[1] if kept is not None and kept[0]() is store else None
        feed = ttx.StoreFeed(store, decoder, start_seq=start)
        self._ttx_feed, self._ttx_feed_key, self._ttx_feed_store = feed, key, store
        feed.start()
        log.info("teletext: reading the rewind buffer of the channel playing")

    def _ttx_stop_feed(self) -> None:
        feed, self._ttx_feed = self._ttx_feed, None
        if feed is None:
            return
        feed.stop()
        if self._ttx_feed_key and feed.last_seq is not None:
            self._ttx_seqs()[self._ttx_feed_key] = (weakref.ref(feed.store), feed.last_seq)

    def teletext_closed(self) -> None:
        self._ttx_stop_feed()

    def teletext_open_settings(self) -> None:
        try:
            self.open_settings(at_page="playback")
        except Exception as e:
            log.debug("teletext: settings not opened: %s", e)
