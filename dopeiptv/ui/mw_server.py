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

A tuner kept open to every device of this computer's networks from before
1.6.9 (home_server.open_untold) keeps working, and is said once while the
server runs: a decision card whose "Close it" lets in only the devices that
used it so this run (home_server.closed_mode - nobody when none did, and a
message says what a device needs then); dismissed, it stays as it is and is
not said again (K_OPEN_TOLD).

Its port in use: whose it is - dopeIPTV's own server (a ``--server`` from a
terminal or at login, another window) or another program - asked off the
window's thread (home_server.look_at_port) and said in a note that offers
the next port free; a click stores it and starts the server there, nothing
changes without one. The Nerd page's line says the same, with a button.
"""

from __future__ import annotations

import threading
from typing import Any

from PyQt6.QtCore import QTimer

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
    #: What the server's answers say it is (home_server.MARK).
    kind = "window"

    def __init__(self, win: Any, bridge: _Bridge) -> None:
        self.win, self.bridge = win, bridge
        self.snapshot: list[dict] = []
        self.active = ""
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

    def guides_kept(self) -> set[str]:
        """The window refreshes its active source's guide itself; the
        server keeps the other sources' it serves (services/guide_keeper)."""
        return {self.active} if self.active else set()

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
    #: The port it could not listen on because it is in use (0: none).
    _home_taken: int = 0
    #: The port the note offers in its place (0: none).
    _home_offer: int = 0
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
            if cfg.port == srv.port or srv.move(cfg.port):
                self._home_error, self._home_taken = "", 0
            else:
                # It goes on where it is.
                self._home_error = srv.error
                self._home_taken = cfg.port if srv.taken else 0
                if srv.taken:
                    self._server_ask_holder(cfg.port)
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
            from ..services.home_server import port_taken
            srv.stop()                  # its restream's thread, started with it
            self._home_error = f"{type(e).__name__}: {e}"
            log.warning("server: cannot listen on port %s: %s", cfg.port, e)
            host.kept_pool.shared = False
            if port_taken(e):
                self._home_taken = cfg.port
                self._server_ask_holder(cfg.port)       # the note once it is known whose
            else:
                self._home_taken = 0
                self._show_toast(tr("nerd_ck_server_down", port=cfg.port), 6000)
            return
        self._home, self._home_error, self._home_taken = srv, "", 0
        if p is not None and callable(shared):
            p.live_hook = self._server_live_hook
        rec = getattr(self, "rec", None)
        if rec is not None:
            rec.before_spawn = self._server_rec_room
        if self._home_timer is None:
            self._home_timer = QTimer(self)
            self._home_timer.setInterval(30000)
            self._home_timer.timeout.connect(self._server_sources_now)
        self._home_timer.start()
        # The tuner kept open to every device from before 1.6.9: said once,
        # a moment after it starts (the window up by then).
        QTimer.singleShot(1500, self._server_tell_open)

    # -- the tuner kept open from before 1.6.9 --------------------------------------------
    #: The decision card's key (ui/notices.py).
    _OPEN_CARD = "server_open_home"

    def _server_tell_open(self) -> None:
        """While server mode runs with the tuner open to every device of
        this computer's networks, kept so unasked (home_server.open_untold):
        a decision card says so, plainly - "Close it" lets in only the
        devices that used it so (``_server_close_open``); dismissed, it is
        kept as it is and not said again (K_OPEN_TOLD)."""
        from ..services import home_server as hs
        if self._home is None or not hs.open_untold(self.settings) \
                or self.__dict__.get("_open_card_up"):
            return
        from .notices import NoticeHub, hub_of
        hub = hub_of(self)
        if not isinstance(hub, NoticeHub):
            return
        self._open_card_up = True
        log.info("server: the tuner is open to every device of this computer's networks "
                 "without its token, as before 1.6.9 - said once")
        hub.post(tr("srv_open_carried"), title=tr("srv_open_carried_title"), level="warning",
                 action=self._server_close_open, action_label=tr("srv_open_close"),
                 decide=True, key=self._OPEN_CARD)
        hub.changed.connect(self._server_open_card_changed)

    def _server_open_card_changed(self) -> None:
        """The hub changed: once the card is gone - answered or dismissed -
        it is not said again. Dismissed is "keep it"."""
        from .notices import hub_of
        hub = hub_of(self)
        if any(m.key == self._OPEN_CARD for m in hub.messages()):
            return
        try:
            hub.changed.disconnect(self._server_open_card_changed)
        except (TypeError, RuntimeError):
            pass
        self._open_card_up = False
        from ..services import home_server as hs
        if not hs.open_told(self.settings):
            self.settings.setValue(hs.K_OPEN_TOLD, "true")
            if hs.open_mode_of(self.settings) == hs.OPEN_HOME:
                log.info("server: the open tuner is kept as it is (the card dismissed)")

    def _server_close_open(self) -> None:
        """The card's "Close it": the devices that used the tuner without
        its token this run - and those listed before - let in by their
        address alone; with none known, nobody, and a message says what a
        device needs then (Settings, where a device is let in)."""
        from ..services import home_server as hs
        from .notices import hub_of
        srv = self._home
        seen = srv.open_seen() if srv is not None else []
        mode, hosts = hs.closed_mode(seen, self.settings.value(hs.K_OPEN_HOSTS, "") or "")
        self.settings.setValue(hs.K_OPEN, mode)
        if hosts:
            self.settings.setValue(hs.K_OPEN_HOSTS, hosts)
        self.settings.setValue(hs.K_OPEN_TOLD, "true")
        log.info("server: the open tuner closed - %s",
                 f"{len(hosts.split(','))} device(s) let in by their address"
                 if mode == hs.OPEN_DEVICES else "every device takes its token now")
        if srv is not None:
            self._server_apply()
        hub = hub_of(self)
        if mode == hs.OPEN_DEVICES:
            hub.post(tr("srv_open_closed_devices", hosts=hosts), level="info",
                     action=lambda: self.open_settings(at_page="nerd_serve"),
                     action_label=tr("settings_title"))
        else:
            hub.post(tr("srv_open_closed_off"), level="warning",
                     action=lambda: self.open_settings(at_page="nerd_serve"),
                     action_label=tr("settings_title"))

    def _server_stop(self, wait: bool = False) -> None:
        """Every device's stream ends, every connection of the server's
        closes (*wait*: before this returns - the app is closing)."""
        srv, self._home = self._home, None
        self._home_taken = self._home_offer = 0
        if self.__dict__.get("_open_card_up"):
            # Server mode off: the card goes too, unanswered - said again
            # when it next runs so.
            from .notices import hub_of
            hub = hub_of(self)
            try:
                hub.changed.disconnect(self._server_open_card_changed)
            except (TypeError, RuntimeError):
                pass
            self._open_card_up = False
            hub.dismiss_key(self._OPEN_CARD)
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
        """How it stands, for the Nerd page's line: *taken* the port found in
        use (0: none), *avoid* the app's other servers' ports."""
        srv = self._home
        from ..services.home_server import DEFAULT_PORT, avoided_ports
        avoid = sorted(avoided_ports(self.settings))
        if srv is None:
            try:
                port = int(self.settings.value("nerd_server_port", DEFAULT_PORT))
            except (TypeError, ValueError):
                port = DEFAULT_PORT
            on = server_wanted(self.settings)
            return {"on": on, "running": False, "port": port,
                    "error": self._home_error, "status": None, "addresses": None,
                    "taken": port if on and self._home_taken == port else 0, "avoid": avoid}
        base = srv.base()
        taken = self._home_taken if self._home_taken != srv.port else 0
        return {"on": True, "running": srv.running, "port": srv.port, "error": self._home_error,
                "status": srv.restream.status(),
                "addresses": srv.addresses(base), "taken": taken, "avoid": avoid}

    # -- its port in use -----------------------------------------------------------------------
    def _server_ask_holder(self, port: int) -> None:
        """Whose *port* is, asked off the window's thread (on loopback, a
        moment); the note says it then, with the next port free."""
        from ..services.home_server import avoided_ports
        avoid = avoided_ports(self.settings)
        threading.Thread(target=self._server_holder_job, args=(port, avoid),
                         name="home-server-port", daemon=True).start()

    def _server_holder_job(self, port: int, avoid: set[int]) -> None:
        from ..core.workers import on_gui_thread
        from ..services.home_server import look_at_port
        try:
            taken = look_at_port(port, avoid)
        except Exception as e:          # never in the way: the note stays unsaid
            log.debug("server: whose port %d is not found out: %s", port, e)
            return
        log.info("server: port %d is in use (%s); the next free one: %s", port, taken.holder,
                 taken.free or "none near it")
        on_gui_thread(self._server_holder_said, taken)

    def _server_holder_said(self, taken: Any) -> None:
        """(the window's thread) Whose the port is: said in a note that offers
        the next port free - unless it changed meanwhile."""
        if not taken.port or self._home_taken != taken.port:
            return
        text = tr(taken.key, port=taken.port)
        if not taken.free:
            self._show_toast(text, 8000)
            return
        self._home_offer = taken.free
        # A decision - the card over the activity line, with its button: the
        # port is taken only when the person asks.
        self._show_action_toast(text, 15_000, self._server_take_offer,
                                label=tr("nerd_server_use_port", port=taken.free),
                                decide=True)

    def _server_take_offer(self) -> None:
        """The note clicked: its port taken."""
        port, self._home_offer = self._home_offer, 0
        if port:
            self._server_use_port(port)

    def _server_use_port(self, port: int) -> None:
        """The person chose *port* in place of the one in use: stored, and
        the server started there (or moved, while it runs on another).
        Only ever from their click."""
        from ..services.home_server import K_PORT
        if not server_wanted(self.settings):
            return                      # turned off meanwhile: nothing to start
        self.settings.setValue(K_PORT, str(port))
        self._server_apply()

    def _server_sources_now(self) -> None:
        host = self._home_host
        store = getattr(self, "playlist_store", None)
        if host is None or store is None:
            return
        items = [dict(p) for p in store.playlists()]
        active = store.active_id
        host.snapshot = sorted(items, key=lambda p: p.get("id") != active)
        host.active = str(active or "")
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
            # By account: another source on the same account counts too.
            uses, key = self._conn_by_account(self._conn_uses(), pid)
            own = connections.own_count(uses, key) + self._conn_ledger().others(key)
            return own, [u.url for u in uses if u.url and (not u.source or u.source == key)]
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
            listed, key = self._conn_by_account(self._conn_uses() if uses is None else uses, src)
            own = connections.own_count(listed, key) + self._conn_ledger().others(key) + 1
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
