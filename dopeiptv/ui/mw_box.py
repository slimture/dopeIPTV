"""The box itself, from the app: zap it to a channel, wake it, put it to
standby, and set a recording on it from the guide (it records whether
the app runs or not). Only there when the source is such a box - an
Enigma2 receiver zaps and records, a Tvheadend server records - so no one
else sees a thing. Nerd adds Copy stream address."""

from __future__ import annotations

import time
from typing import Any, Callable

from PyQt6.QtWidgets import QApplication, QMenu

from ..core.workers import run_async
from ..i18n import tr


class _BoxMixin:
    def _box_run(self, fn: Callable[..., Any], ok: Callable[[Any], str], *args: Any) -> None:
        """Ask the box off the window's thread; say how it went."""
        def done(result: Any) -> None:
            text = ok(result)
            if text:
                self._show_toast(text, 4000)

        def failed(err: Any = None) -> None:
            msg = getattr(err, "localized", None)
            self._show_toast(tr("box_failed", why=msg() if callable(msg) else str(err or "")), 6000)
        run_async(self.pool, fn, done, failed, *args)

    def _box_channel_menu(self, menu: QMenu, it: dict) -> None:
        """A channel's right-click menu: the box's own actions, and with Nerd
        its play address to copy."""
        from ..core import nerd
        c, sid = self.client, it.get("stream_id")
        if sid is None or self.mode != "live":
            return
        if hasattr(c, "zap"):
            sub = menu.addMenu(tr("box_menu"))
            sub.addAction(tr("box_zap"), lambda: self._box_run(
                c.zap, lambda ok: tr("box_zapped") if ok else tr("box_failed", why=""), sid))
            sub.addAction(tr("box_wake"), lambda: self._box_run(
                c.power, lambda _ok: tr("box_woken"), 4))
            sub.addAction(tr("box_standby"), lambda: self._box_run(
                c.power, lambda _ok: tr("box_sleeping"), 5))
        if nerd.current().on:
            menu.addAction(tr("nerd_copy_url"), lambda: self._copy_stream_url(it))
            menu.addAction(tr("nerd_epg_map"), lambda: self._map_guide_channel(it))

    def _map_guide_channel(self, it: dict) -> None:
        """Nerd: which of the guide's channels this channel's programmes come
        from, chosen by hand (or back to the automatic match)."""
        from PyQt6.QtWidgets import QInputDialog

        from ..core import nerd_lists
        pl = self.playlist_store.active() if self.playlist_store else None
        pid = (pl or {}).get("id")
        if not pid:
            return
        ids = sorted(getattr(self.xmltv, "_by_id", {}) or {})
        auto = tr("nerd_epg_auto")
        current = str(it.get("epg_channel_id") or "")
        items = [auto, *ids]
        at = items.index(current) if current in items else 0
        choice, ok = QInputDialog.getItem(
            self, tr("nerd_epg_map"), tr("nerd_epg_map_hint", name=it.get("name") or ""),
            items, at, True)
        if not ok:
            return
        epg_id = "" if choice in ("", auto) else choice.strip()
        nerd_lists.set_epg_map(self.settings, pid, it["stream_id"], epg_id)
        if epg_id:
            it.setdefault("_epg_own", it.get("epg_channel_id") or "")
            it["epg_channel_id"] = epg_id
        else:
            it["epg_channel_id"] = it.pop("_epg_own", it.get("epg_channel_id") or "")
        cur = self.listw.currentIndex()
        if cur.isValid():
            self._on_current_changed(cur)

    def _copy_stream_url(self, it: dict) -> None:
        try:
            url = self.client.live_url(it["stream_id"])
        except Exception:
            url = ""
        if url:
            QApplication.clipboard().setText(url)
            self._show_toast(tr("nerd_url_copied"), 3000)

    def _box_record_action(self, menu: QMenu, ch: dict, title: str,
                           start: float, stop: float) -> None:
        """Record on the box, in a programme's menu, for a programme not
        over yet."""
        c, sid = self.client, ch.get("stream_id")
        if sid is None or not hasattr(c, "add_timer") or stop <= time.time():
            return
        start = max(start, time.time())

        def said(result: Any) -> str:
            ok, text = result if isinstance(result, tuple) else (bool(result), "")
            return tr("box_timer_set", title=title or "") if ok else tr("box_failed", why=text)
        menu.addAction(tr("box_record"), lambda: self._box_run(
            c.add_timer, said, sid, start, stop, title or ""))
