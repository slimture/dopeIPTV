"""Stream health in the window (core/health.py keeps the numbers): what the
playback path tells it as a live channel starts, shows its picture, drops
and fails; the "Stream health…" entry in a channel's menu with that
channel's numbers; the dot beside a channel's name in the list. All of it
only while Nerd and its stream-health switch are on. It only watches: each
hook swallows its own errors, and nothing in playback waits on it."""

from __future__ import annotations

from typing import Any

from ..core import health, nerd
from ..core.log import log
from ..i18n import tr


class _HealthMixin:
    _health_seen: Any = None
    _health_vis = False

    # -- the channel ------------------------------------------------------------

    def _health_source_id(self) -> str:
        store = getattr(self, "playlist_store", None)
        return str(getattr(store, "active_id", "") or "")

    def _health_key(self, key: Any, item: dict | None = None) -> str | None:
        """The channel *key*/*item* stands for: its source's id and its
        stream's id (a failover stream carries both in its key). An address
        played as it is, or a file, is no channel: None."""
        if isinstance(key, str) and key.startswith("failover:"):
            parts = key.split(":", 2)
            return health.channel_key(parts[1], parts[2]) if len(parts) == 3 else None
        sid = (item or {}).get("stream_id")
        if sid is None:
            sid = key
        if sid is None or sid == "" or "/" in str(sid):
            return None
        return health.channel_key(self._health_source_id(), sid)

    # -- what the playback path tells it ----------------------------------------

    def _health_begin(self, key: Any, title: str, item: dict | None,
                      kind: str, catchup: bool) -> None:
        """The player was asked to play something (_start_playback)."""
        try:
            if kind != "live" or catchup:
                self.health.leave()
                return
            name = (item or {}).get("name") or title or ""
            self.health.begin(self._health_key(key, item), str(name))
        except Exception:
            log.debug("stream health: begin", exc_info=True)

    def _health_retry(self) -> None:
        """The app reconnects what it plays (_retry_last_stream)."""
        try:
            self.health.expect_retry()
        except Exception:
            log.debug("stream health: retry", exc_info=True)

    def _health_frame(self, ahead: float = 0.0) -> None:
        """The player's first_frame: the picture is on."""
        try:
            took = self.health.frame(ahead)
            if took is not None:
                log.debug("stream health: picture after %.2f s", took)
            self._health_repaint()
        except Exception:
            log.debug("stream health: frame", exc_info=True)

    def _health_drop(self) -> None:
        """What played dropped, froze or ended, and is reconnected."""
        try:
            if self.health.drop():
                self._health_repaint()
        except Exception:
            log.debug("stream health: drop", exc_info=True)

    def _health_failed(self, text: str = "", key: str | None = None) -> None:
        """The channel did not play: the reconnects gave up, or the reason
        is known (*key*: the channel it was found for)."""
        try:
            if self.health.fail(text, key):
                self._health_repaint()
        except Exception:
            log.debug("stream health: fail", exc_info=True)

    def _health_stuck(self) -> None:
        """The quick reconnects are spent on a stream that froze."""
        try:
            if self.health.stuck(tr("health_err_stalled")):
                self._health_repaint()
        except Exception:
            log.debug("stream health: stuck", exc_info=True)

    def _health_repaint(self) -> None:
        if self._health_shown():
            self.listw.viewport().update()

    # -- in the list ------------------------------------------------------------

    def _health_shown(self) -> bool:
        """Nerd and the switch on - asked again only once Nerd's settings
        were put in force anew (each change installs a new profile)."""
        n = nerd.current()
        if n is not self._health_seen:
            self._health_seen, self._health_vis = n, health.enabled(n)
        return self._health_vis

    def health_dot(self, it: dict | None, kind: str) -> str | None:
        """The colour of the dot beside a channel's name, or None for none."""
        if kind not in ("live", "fav") or not it or not self._health_shown():
            return None
        sid = it.get("stream_id")
        if sid is None:
            return None
        try:
            grade = health.grade(self.health.entry(
                health.channel_key(self._health_source_id(), sid)))
        except Exception:
            return None
        if not grade:
            return None
        from .theme import P
        return P.get(grade)

    # -- the channel's menu and its numbers --------------------------------------

    def _health_channel_menu(self, menu: Any, it: dict) -> None:
        if self._health_shown() and it.get("stream_id") is not None:
            menu.addAction(tr("ctx_stream_health"),
                           lambda it=it: self._show_stream_health(it))

    def _stream_health_dialog(self, it: dict) -> Any:
        from .dialogs import StreamHealthDialog
        key = health.channel_key(self._health_source_id(), it.get("stream_id"))
        name = self.channel_display_name(it) or str(it.get("name") or "")
        return StreamHealthDialog(self, name, self.health.entry(key))

    def _show_stream_health(self, it: dict) -> None:
        self._stream_health_dialog(it).exec()
