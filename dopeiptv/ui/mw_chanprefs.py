"""Settings of a channel's own in the window (Nerd; core/channel_prefs.py
keeps them): the player is told which channel it is about to play, so it
plays it with them; "Channel settings…" in a channel's menu, marked when
the channel has its own, and its dialog. While Nerd is off the entry is not
offered and the player applies none of them - they are kept for when it is
on again. Each hook swallows its own errors: playback never depends on it."""

from __future__ import annotations

from typing import Any

from ..core import channel_prefs
from ..core.log import log
from ..i18n import tr


def own_mark(size: int = 10) -> Any:
    """The dot beside "Channel settings…" for a channel with its own."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap

    from . import theme
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(theme.ACCENT))
    p.drawEllipse(1, 1, size - 2, size - 2)
    p.end()
    return QIcon(pm)


class _ChannelPrefsMixin:
    _chanprefs_store: Any = None

    def _chanprefs(self) -> channel_prefs.ChannelPrefs:
        if self._chanprefs_store is None:
            self._chanprefs_store = channel_prefs.ChannelPrefs(self.settings)
        return self._chanprefs_store

    def _chanprefs_key(self, key: Any, item: dict | None) -> str | None:
        """The channel *key*/*item* stands for - its source's id and its
        stream's id, as stream health keys it (a failover stream carries
        both in its key). An address or a file is no channel: None."""
        return self._health_key(key, item)

    def _chanprefs_legacy(self, it: dict | None) -> str:
        """Where the row's audio delay was kept before it moved into its
        settings: by the player's key for the channel."""
        return channel_prefs.legacy_key(f"live:{self._item_key(it)}")

    # -- the playback path ------------------------------------------------------

    def _chanprefs_arm(self, key: Any, item: dict | None, kind: str) -> None:
        """Right before the player is asked to play: which channel it is (a
        live channel, its catch-up too), else none. The player looks its
        settings up itself, and only while Nerd is on."""
        player = getattr(self, "player", None)
        if player is None:
            return
        try:
            ckey = self._chanprefs_key(key, item) if kind == "live" else None
        except Exception:
            log.debug("channel settings: which channel", exc_info=True)
            ckey = None
        try:
            player.set_channel(ckey)
        except Exception:
            log.debug("channel settings: arm", exc_info=True)

    # -- the channel's menu and its dialog ----------------------------------------

    def _chanprefs_menu(self, menu: Any, it: dict) -> None:
        """Nerd: "Channel settings…", with a dot when the channel has its own."""
        try:
            if not channel_prefs.enabled():
                return
            ckey = self._chanprefs_key(None, it)
            if ckey is None:
                return
            act = menu.addAction(tr("ctx_channel_settings"),
                                 lambda it=it: self._show_channel_settings(it))
            if self._chanprefs().has_own(ckey, self._chanprefs_legacy(it)):
                act.setIcon(own_mark())
        except Exception:
            log.debug("channel settings: menu", exc_info=True)

    def _channel_settings_dialog(self, it: dict) -> Any:
        from .dialogs import ChannelSettingsDialog
        ckey = self._chanprefs_key(None, it)
        prefs = self._chanprefs().get(ckey, self._chanprefs_legacy(it))
        name = self.channel_display_name(it) or str(it.get("name") or "")
        return ChannelSettingsDialog(self, name, prefs, delay=channel_prefs.delay_enabled())

    def _show_channel_settings(self, it: dict) -> None:
        d = self._channel_settings_dialog(it)
        if d.exec():
            self._chanprefs_save(it, d.choice())

    def _chanprefs_save(self, it: dict, prefs: dict) -> None:
        """Keep *prefs* for the channel *it* ({}: back to the app's), and
        put them in force at once when it is the one playing."""
        ckey = self._chanprefs_key(None, it)
        if ckey is None:
            return
        store = self._chanprefs()
        before = store.get(ckey, self._chanprefs_legacy(it))
        if prefs:
            kept = store.put(ckey, prefs)
        else:
            store.reset(ckey, self._chanprefs_legacy(it))
            kept = {}
        if kept == before:
            return
        try:
            if self.player is not None:
                self.player.channel_prefs_changed(ckey)
        except Exception:
            log.debug("channel settings: the channel playing", exc_info=True)
        name = self.channel_display_name(it) or str(it.get("name") or "")
        self._show_toast(tr("chanprefs_saved" if kept else "chanprefs_cleared", name=name), 3000)
