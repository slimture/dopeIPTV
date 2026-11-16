"""Server mode in the window (services/home_server.py serves it): started and
stopped as Nerd > Remote and automation says - off by default, only while
Nerd is on - and the window as its host.

One connection per channel for everyone in this process: the restream reads
from the player's own buffer pool (DvrPlayerMixin.dvr_pool_for_server), so a
channel the person watches and a device watches has one connection, whoever
opened it first - the player plays the server's session (``live_hook``), the
server joins the player's (``app_session``). The app's own use wins: before
the player, a multiview cell, a cast, a test or a recording opens a
connection of its own, the server lets go of the channels of its own that
leave no room for it (``_server_make_room``), and what the server counts as
taken is the window's own count (mw_connections). With the server off
nothing here runs: no hook on the player, nothing counted.

Nothing a device watches is the person's: no history, statistics, Trakt or
Simkl, Discord or events - the restream never goes through the playback
path those hook into.
"""

from __future__ import annotations

import threading
from typing import Any

from ..core import connections
from ..core.log import log
from ..core.restream import channel_key
from ..i18n import tr
from .mw_remote import _Bridge

#: Seconds of history a server-only buffer keeps in the player's pool.
SERVER_KEEP_S = 120.0


class _WindowHost:
    """What the server asks of the window. What needs the window's thread
    goes over the bridge; what the restream asks under its lock (the pool)
    never waits for it."""

    #: The app's own use as the window counts it holds another device's too
    #: (mw_connections' ledger).
    counts_others = True

    def __init__(self, win: Any, bridge: _Bridge) -> None:
        self.win, self.bridge = win, bridge
        self.snapshot: list[dict] = []
        self.kept_pool: Any = None

    def sources(self) -> list[dict]:
        return list(self.snapshot)

    def filters(self, pid: str) -> tuple[set[str], set[str], dict[str, int]]:
        return self.bridge.call("filters", {"pid": pid})

    def app_use(self, pid: str) -> tuple[int, list[str]]:
        return self.bridge.call("app_use", {"pid": pid})

    def app_session(self, pid: str, sid: str) -> Any:
        w = self.win
        p = getattr(w, "player", None)
        dvr = getattr(p, "_dvr", None) if p is not None else None
        item = getattr(w, "_playing_item", None) or {}
        try:
            # The session must be this channel's own, not only the item's: a
            # zap sets the item some way before the player leaves the session
            # of the channel before it - a device asking then was given that
            # one, and the player then played it under the new channel's name.
            own = (f"live:{w._item_key(item)}", "srv:" + channel_key(pid, sid))
            if (dvr is not None and dvr.streaming and str(item.get("stream_id")) == sid
                    and getattr(dvr, "key", None) in own
                    and w._conn_player_source() == pid):
                return dvr
        except Exception:
            return None
        return None

    def pool(self) -> Any:
        p = getattr(self.win, "player", None)
        pool = getattr(p, "_dvr_pool", None) if p is not None else None
        return pool if pool is not None else self.kept_pool

    def save(self, changes: dict[str, str]) -> Any:
        return self.bridge.call("save", {"changes": changes})

    def page(self) -> tuple[bytes, str]:
        return self.bridge.call("page", {})


def server_wanted(settings: Any) -> bool:
    from ..core import nerd
    n = nerd.Nerd(settings)
    return n.on and n.flag("nerd_server")


class _ServerMixin:
    _home: Any = None
    _home_host: _WindowHost | None = None
    _home_bridge: _Bridge | None = None
    _home_error: str = ""
    _home_timer: Any = None
    _home_stopping: threading.Thread | None = None

    # -- on and off (the window's thread) -----------------------------------------------
    def _server_apply(self, wanted: bool | None = None) -> dict[str, Any]:
        """Start or stop the server to match *wanted* (the settings when
        None); a running one takes changed settings (port, tuners,
        sources). Returns how it stands, for the Nerd page's line."""
        on = server_wanted(self.settings) if wanted is None else wanted
        srv = self._home
        if not on:
            self._server_stop()
        elif srv is None:
            self._server_start()
        else:
            self._server_sources_now()
            from ..services.home_server import read_config
            cfg = read_config(self.settings)
            srv.refresh(cfg)
            if cfg.port != srv.port and not srv.move(cfg.port):
                self._home_error = srv.error
        return self._server_state()

    def _server_start(self) -> None:
        from ..services.home_server import HomeServer, read_config
        stopping, self._home_stopping = self._home_stopping, None
        if stopping is not None:
            stopping.join(3.0)          # turned off a moment ago: its port free first
        p = getattr(self, "player", None)
        if self._home_bridge is None:
            self._home_bridge = _Bridge(self._server_do)
        host = self._home_host = _WindowHost(self, self._home_bridge)
        shared = getattr(p, "dvr_pool_for_server", None)
        if callable(shared):
            host.kept_pool = shared(SERVER_KEEP_S)
        else:                       # no embedded player: a pool of the server's own
            from ..core.dvr_pool import DvrPool
            from ..media.dvr_player import dvr_base
            host.kept_pool = DvrPool(dvr_base(), SERVER_KEEP_S, 2 << 30)
        # The player keeps it while the server runs, its own buffer off or not.
        host.kept_pool.shared = True
        self._server_sources_now()
        cfg = read_config(self.settings)
        srv = HomeServer(host, cfg)
        try:
            srv.start()
        except OSError as e:
            srv.stop()                  # its restream's thread, started with it
            self._home_error = f"{type(e).__name__}: {e}"
            log.warning("server: cannot listen on port %s: %s", cfg.port, e)
            self._show_toast(tr("nerd_ck_server_down", port=cfg.port), 6000)
            host.kept_pool.shared = False
            return
        self._home, self._home_error = srv, ""
        if p is not None and callable(shared):
            p.live_hook = self._server_live_hook
        rec = getattr(self, "rec", None)
        if rec is not None:
            rec.before_spawn = self._server_rec_room
        from PyQt6.QtCore import QTimer
        if self._home_timer is None:
            self._home_timer = QTimer(self)
            self._home_timer.setInterval(30000)
            self._home_timer.timeout.connect(self._server_sources_now)
        self._home_timer.start()

    def _server_stop(self, wait: bool = False) -> None:
        """Every device's stream ends, every connection of the server's
        closes (*wait*: before this returns - the app is closing)."""
        srv, self._home = self._home, None
        p = getattr(self, "player", None)
        if p is not None and getattr(p, "live_hook", None) is not None:
            p.live_hook = None
        rec = getattr(self, "rec", None)
        if rec is not None and getattr(rec, "before_spawn", None) == self._server_rec_room:
            rec.before_spawn = None
        if self._home_timer is not None:
            self._home_timer.stop()
        host = self._home_host
        if host is not None and host.kept_pool is not None:
            host.kept_pool.shared = False
        if srv is None:
            return
        if wait:
            srv.stop()
        else:
            t = self._home_stopping = threading.Thread(target=srv.stop, name="home-server-stop",
                                                       daemon=True)
            t.start()

    def _server_state(self) -> dict[str, Any]:
        srv = self._home
        from ..services.home_server import DEFAULT_PORT
        if srv is None:
            try:
                port = int(self.settings.value("nerd_server_port", DEFAULT_PORT))
            except (TypeError, ValueError):
                port = DEFAULT_PORT
            return {"on": server_wanted(self.settings), "running": False, "port": port,
                    "error": self._home_error, "status": None, "addresses": None}
        base = srv.base()
        return {"on": True, "running": srv.running, "port": srv.port, "error": self._home_error,
                "status": srv.restream.status(),
                "addresses": srv.addresses(base)}

    def _server_sources_now(self) -> None:
        host = self._home_host
        store = getattr(self, "playlist_store", None)
        if host is None or store is None:
            return
        items = [dict(p) for p in store.playlists()]
        active = store.active_id
        host.snapshot = sorted(items, key=lambda p: p.get("id") != active)
        srv = self._home
        if srv is not None:
            srv.refresh()

    # -- the server's questions (the window's thread) --------------------------------------
    def _server_do(self, action: str, params: dict) -> Any:
        pid = str(params.get("pid") or "")
        if action == "filters":
            return self._server_filters(pid)
        if action == "app_use":
            try:
                self._conn_seen()       # what the provider last said, into the ledger
            except Exception as e:
                log.debug("server: the provider's count not read: %s", e)
            uses = self._conn_uses()
            own = connections.own_count(uses, pid) + self._conn_ledger().others(pid)
            return own, [u.url for u in uses if u.url and (not u.source or u.source == pid)]
        if action == "save":
            from ..services.home_server import read_config
            for key, value in dict(params.get("changes") or {}).items():
                if value == "":
                    self.settings.remove(key)
                else:
                    self.settings.setValue(key, value)
            self._server_sources_now()
            return read_config(self.settings)
        if action == "page":
            from ..core import server_page
            from ..i18n import current_language, is_rtl
            from .mw_phone import page_palettes
            from .theme import effective_theme, is_light
            dark, light = page_palettes(is_light(effective_theme(self.settings)))
            return server_page.render(dark, light, {k: tr(k) for k in server_page.WORDS},
                                      current_language(), is_rtl())
        raise ValueError(action)

    def _server_filters(self, pid: str) -> tuple[set[str], set[str], dict[str, int]]:
        """Hidden and locked categories (never served - a locked one not
        even while it is unlocked here), channels hidden by hand, and the
        channel numbers of the app's own, of source *pid*."""
        from ..core import channel_numbers
        from ..core.stores import CategoryOverrides, ChannelOverrides
        cats = CategoryOverrides(self.settings, f"category_overrides_{pid}")
        chans = ChannelOverrides(self.settings, f"channel_overrides_{pid}")
        hidden = {str(k) for k, e in (chans.data.get("live") or {}).items()
                  if isinstance(e, dict) and e.get("hidden")}
        return (cats.excluded_ids("live", include_locked=True), hidden,
                dict(channel_numbers.active(pid)))

    # -- the app's own use wins ---------------------------------------------------------------
    def _server_live_hook(self, url: str, key: str | None) -> Any:
        """(the player, before a stream opens; *key* the live channel's, else
        None) The server's session when it streams this channel - else room
        made for the player's connection."""
        srv = self._home
        if srv is None or not connections.is_network(url):
            return None
        src = self._conn_player_source()
        sid = key.split(":", 1)[1] if key and key.startswith("live:") else None
        if sid is not None:
            shared = srv.restream.session_of(src, sid)
            if shared is not None:
                return shared
        self._server_make_room(self._conn_uses(free_player=True), src)
        return None

    def _server_make_room(self, uses: list | None = None, source: str | None = None) -> None:
        """One connection of the app's own is about to open to *source*
        (the active source when None), next to *uses* (as they are now when
        None): the server lets go of what leaves no room for it."""
        srv = self._home
        if srv is None:
            return
        try:
            src = self._conn_source() if source is None else source
            listed = self._conn_uses() if uses is None else uses
            own = connections.own_count(listed, src) + self._conn_ledger().others(src) + 1
            gone = srv.restream.make_room(src, own)
            if gone:
                log.info("server: %d channel(s) let go for the app's own viewing", gone)
        except Exception as e:      # never in the way of the person's own viewing
            log.debug("server: no room made: %s", e)

    def _server_rec_room(self, job: dict) -> None:
        self._server_make_room(None, str(job.get("source") or "") or None)

    def _server_in_use_lines(self) -> list[tuple[str, str]]:
        """Nerd > Diagnostics' In use now: what the server holds."""
        srv = self._home
        if srv is None:
            return []
        st = srv.restream.status()
        lines = [("head", tr("inuse_server", n=st["in_use"], total=srv.total()))]
        for ch in st["channels"]:
            who = ", ".join(v["client"] for v in ch["viewers"]) or "-"
            name = tr("private_menu") if ch.get("private") else ch["name"]
            lines.append(("item", f"• {name} → {who}"))
        return lines
