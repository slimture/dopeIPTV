"""Connections to the account in the window (core/connections.py): the
app's own counted as they are now - the player, the rewind buffers, a
recording over a connection of its own, each multiview cell, a cast - and,
before the app opens one more itself, a plain warning when that would go over
what the account allows, with the choice to go ahead anyway.

For everyone, not only Nerd. Nothing is said while the account gives no
limit, and nothing is ever stopped: the warning is a question.

An Enigma2 box's tuners are in the same count (core/tuners.py): before a
zap, a multiview cell, a cast or a recording over its own connection opens
a stream from the box that would cut off a recording or what the box shows
on its TV, the same question says so. The box is asked off the window's
thread and its answer kept a few seconds (every ~20 s while a box is the
source); a box that does not answer says nothing and stops nothing. A ring
in the channel list marks the channels that would cut something off now,
and Nerd > Diagnostics shows what is in use ("In use now").

A SAT>IP server's tuners are counted by the app itself (providers/satip.py,
no request): each stream from it takes one of its system's, and before a
multiview cell, a cast, a recording over its own connection or a zap beside
them would find none free - or, the server saying no number, would be its
second - the same question says so.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from ..core import connections, tuners
from ..core.connections import Use
from ..core.log import log
from ..core.workers import run_async
from ..i18n import tr

#: How long after the provider answered its count of connections in use
#: still stands next to the app's own count now.
SEEN_FRESH_SECS = 15.0
KIND_WORDS = {"player": "conn_k_player", "buffer": "conn_k_buffer",
              "recording": "conn_k_recording", "multiview": "conn_k_multiview",
              "cast": "conn_k_cast"}
#: How often a box that is the source is asked what its tuners do.
TUNER_TICK_MS = 20000
#: The hold server mode puts on a buffer it reads (core/restream.SERVER).
SERVER_HOLD = "server"


def may_read_stream(widget: Any, source_id: str = "") -> bool:
    """From a dialog or a Settings page: may a test read a stream of
    *source_id* (Test connection's stream and speed, a Test of a source's
    catch-up or rewritten addresses)? The window above *widget* asks first
    when that would be one connection too many (``_conn_ok_test``); True
    where there is no such window."""
    w = widget
    while w is not None:
        ask = getattr(w, "_conn_ok_test", None)
        if callable(ask):
            return bool(ask(source_id))
        parent = getattr(w, "parentWidget", None)
        w = parent() if callable(parent) else None
    return True


def _tuner_fetch(pid: str, client: Any) -> tuple[str, Any, Any]:
    """(off the window's thread) (source id, the box's state or None, why
    not) - never raises."""
    try:
        return pid, client.tuner_state(), None
    except Exception as e:
        return pid, None, e


class _ConnectionsMixin:

    # -- what is counted -----------------------------------------------------------

    def _conn_ledger(self) -> connections.Ledger:
        led = getattr(self, "_conns", None)
        if led is None:
            led = self._conns = connections.Ledger()
        return led

    def _conn_source(self) -> str:
        """The active source's id: whose account the checks are about."""
        store = getattr(self, "playlist_store", None)
        return str(getattr(store, "active_id", "") or "") if store is not None else ""

    def _conn_player_source(self) -> str:
        """The source the docked player plays from: failover's or the Search
        page's other one, else the one what plays was started from
        (mw_source_label) - a switch to another source does not stop the
        stream, and its connection stays its own account's."""
        other = getattr(self, "_failover_source", None)
        if other:
            return str(other.get("id") or "")
        src = getattr(self, "_playing_src", None)
        if src and src[0]:
            return str(src[0])
        return self._conn_source()

    #: (address, play generation) of the stream the player gave up on.
    _conn_dead: tuple[str, Any] | None = None

    def _conn_gave_up(self) -> None:
        """The player gave up on what it plays: its stream ended and nothing
        opens it again (reconnects off, or no player on screen to). The last
        picture stays until Stop, but no connection is held - not counted
        from now to the next play (the player's play generation moves on)."""
        p = getattr(self, "player", None)
        url = getattr(p, "current_url", None) if p is not None else None
        if url:
            self._conn_dead = (str(url), getattr(p, "_play_gen", None))
            log.info("connections: the player gave up on its stream - not counted")

    def _conn_player_dead(self, p: Any) -> bool:
        """The player still shows the stream it gave up on (_conn_gave_up)."""
        dead = self._conn_dead
        return dead is not None and dead == (
            str(getattr(p, "current_url", None) or ""), getattr(p, "_play_gen", None))

    def _conn_uses(self, free_player: bool = False, free_cell=None) -> list[Use]:
        """The connections the app holds now. *free_player*: as they will be
        once the player has let its stream go (its buffer stays while a
        recording reads it); *free_cell*: a multiview cell about to be
        given another stream."""
        uses: list[Use] = []
        p = getattr(self, "player", None)
        src = self._conn_player_source()
        dvr = getattr(p, "_dvr", None) if p is not None else None
        try:
            # Given up on: none held - from the buffer, once its own
            # connection gave up too (one still streaming is read still).
            dead = p is not None and self._conn_player_dead(p)
            if (dvr is not None and dvr.streaming
                    and not (dead and getattr(dvr, "state", "") == "failed")):
                if not free_player or dvr.recording:
                    uses.append(Use("buffer", src, str(getattr(dvr, "url", "") or "")))
            elif (p is not None and getattr(p, "current_url", None)
                  and connections.is_network(p.current_url) and not free_player
                  and not dead):
                uses.append(Use("player", src, str(p.current_url)))
            pool = getattr(p, "_dvr_pool", None) if p is not None else None
            for s in (pool.sessions if pool is not None else []):
                # Kept by a recording or a cast - not one server mode alone
                # reads for other devices: that one gives way (mw_server).
                only = getattr(s, "held_only_by", None)
                if s is not dvr and s.streaming and not (callable(only) and only(SERVER_HOLD)):
                    uses.append(Use("buffer", "", str(getattr(s, "url", "") or "")))
        except Exception as e:      # the player half torn down
            log.debug("connections: player not counted: %s", e)
        rec = getattr(self, "rec", None)
        for j in (rec.jobs if rec is not None else []):
            if (j.get("status") == "recording" and not j.get("inplayer")
                    and j.get("recorder") is None and connections.is_network(j.get("url"))):
                uses.append(Use("recording", str(j.get("source") or ""), str(j.get("url"))))
        win = getattr(self, "_multiview_win", None)
        for c in (getattr(win, "cells", None) or []):
            if c is not free_cell and connections.is_network(getattr(c, "url", None)):
                uses.append(Use("multiview", str(getattr(c, "source_id", "") or ""), str(c.url)))
        if self._conn_casting():
            ctx = getattr(self, "_cast_ctx", None) or {}
            uses.append(Use("cast", self._conn_cast_source(),
                            str(ctx.get("row_source") or ctx.get("row_url") or "")))
        return uses

    def _conn_cast_source(self) -> str:
        """The source the running cast is from (mw_cast keeps it: what plays,
        failover's or the Search page's other source), else the one in use."""
        ctx = getattr(self, "_cast_ctx", None) or {}
        return str(ctx.get("source_id") or "") or self._conn_source()

    def _conn_casting(self) -> bool:
        """A cast of a stream runs: whether the TV or Kodi fetches it or the
        converter here does, it is one connection to the account (a file
        of this computer's is none)."""
        cast = getattr(self, "cast", None)
        try:
            if cast is None or getattr(cast, "active", None) is None:
                return False
        except Exception:
            return False
        ctx = getattr(self, "_cast_ctx", None) or {}
        return connections.is_network(ctx.get("row_source") or ctx.get("row_url"))

    def _conn_seen(self, source: str | None = None, info: dict | None = None,
                   own: int | None = None) -> None:
        """What the provider said of the connections into the ledger: *info*
        (an account_info answer for *source*, the app holding *own* of them
        when it was asked), else what the active source's client last heard
        - its count in use only while it is fresh."""
        led = self._conn_ledger()
        src = self._conn_source() if source is None else source
        if info is not None:
            limit, active = connections.numbers((info or {}).get("user_info"))
            if own is None:
                own = connections.own_count(self._conn_uses(), src) + self._conn_served(src)
            led.report(src, limit, active, own)
            return
        seen = getattr(getattr(self, "client", None), "account_seen", None)
        if not isinstance(seen, dict) or source not in (None, self._conn_source()):
            return
        at = float(seen.get("at") or 0.0)
        fresh = time.monotonic() - at <= SEEN_FRESH_SECS
        led.report(src, seen.get("max"), seen.get("active") if fresh else None,
                   connections.own_count(self._conn_uses(), src) + self._conn_served(src), at=at)

    def _conn_served(self, source: str) -> int:
        """Connections server mode holds to *source* for other devices: the
        app's, as the provider counts them, though not the app's own use."""
        srv = getattr(self, "_home", None)
        try:
            return int(srv.restream.in_use(source)) if srv is not None else 0
        except Exception:
            return 0

    def _conn_limit(self) -> int | None:
        n = getattr(getattr(self, "client", None), "max_connections", None)
        try:
            return int(n) if n else None
        except (TypeError, ValueError):
            return None

    def _conn_verdict(self, uses: list[Use], adding: int = 1,
                      source: str = "") -> connections.Verdict | None:
        """Over the limit of *source* (the active source when not given)
        with *adding* more next to *uses*? Another source's limit is the one
        its provider gave while it was the one in use."""
        try:
            self._conn_seen()
            active = self._conn_source()
            src = str(source or active)
            return self._conn_ledger().check(
                src, uses, adding, limit=self._conn_limit() if src == active else None)
        except Exception as e:      # counting must never stand in the way
            log.debug("connections: not checked: %s", e)
            return None

    # -- what is said ------------------------------------------------------------------

    def _conn_words(self, v: connections.Verdict) -> str:
        """The connections in use, by whom: "player, multiview window ×2,
        another device"."""
        return self._conn_who(v.kinds, v.others)

    @staticmethod
    def _conn_who(kinds, others: int = 0) -> str:
        parts = []
        for kind, n in connections.counted(kinds):
            word = tr(KIND_WORDS.get(kind, "conn_k_player"))
            parts.append(word if n == 1 else tr("conn_times", what=word, n=n))
        if others:
            word = tr("conn_k_other")
            parts.append(word if others == 1 else tr("conn_times", what=word, n=others))
        return ", ".join(parts)

    def _conn_warning(self, v: connections.Verdict) -> str:
        """One plain paragraph: what the account allows, what is in use and
        by whom, and what one more may do."""
        used = self._conn_words(v)
        text = tr("conn_over", max=v.limit, n=v.in_use)
        if used:
            text += " " + tr("conn_used_by", who=used)
        return text + " " + tr("conn_risk")

    def _conn_with_warning(self, body: str, v: connections.Verdict | None) -> str:
        if v is None:
            return body
        self._conn_tell_phone(v)
        return body + "\n\n" + self._conn_warning(v)

    def _conn_tell_phone(self, v: connections.Verdict | None,
                         tuner: tuners.Verdict | None = None) -> None:
        """A play the phone remote asked for waits on this question: its
        page says it too (mw_phone), and where to answer."""
        note = getattr(self, "_phone_note", None)
        said = " ".join(x for x in (self._conn_warning(v) if v is not None else "",
                                    self._tuner_warning(tuner) if tuner is not None else "") if x)
        if callable(note) and said:
            note(said + " " + tr("phone_answer_there"))

    def _conn_ask(self, v: connections.Verdict | None, what: str,
                  tuner: tuners.Verdict | None = None) -> bool:
        """Warn about *v* (the account's limit) and *tuner* (the box's
        tuners) before *what* (an i18n key: what is about to open the
        stream); True to go ahead."""
        self._conn_tell_phone(v, tuner)
        if what == "tuner_what" and tuner is not None and tuner.counted:
            what = "satip_what"
        parts = [tr(what)]
        if v is not None:
            parts.append(self._conn_warning(v))
        if tuner is not None:
            parts.append(self._tuner_warning(tuner))
        idx = self._choice_dialog(
            tr("conn_title" if v is not None else "tuner_title"), "\n\n".join(parts),
            [(tr("conn_anyway"), "normal"), (tr("common_cancel"), "primary")])
        go = idx == 0
        if v is not None:
            log.info("connections: %d of %d in use - one more %s", v.in_use, v.limit,
                     "anyway" if go else "not opened")
        if tuner is not None:
            log.info("tuners: %d of %d in use on the box - one more %s", tuner.in_use,
                     tuner.tuners, "anyway" if go else "not opened")
        return go

    # -- the places a connection is opened -----------------------------------------------

    def _conn_server_room(self, uses: list[Use] | None = None, source: str | None = None) -> None:
        """Server mode, while it runs, lets go of a channel it streams to
        other devices when the app's own new connection needs its room: the
        app's own use wins (mw_server). Asked only once the connection is
        going to open - after the person's go ahead, never before a Cancel."""
        room = getattr(self, "_server_make_room", None)
        if callable(room):
            room(uses, source)

    def _conn_ok_multiview(self, cell: int | None, url: str = "", source: str = "") -> bool:
        """*url* into multiview cell *cell* (None: the first free one): the
        docked player lets its stream go first where Settings says so, and
        a cell that plays already gives its stream up for the new one. A
        file of this computer's takes no connection. *source*: the source
        it is from when not the one in use (the docked player's stream,
        started before a switch or from the Search page)."""
        if not connections.is_network(url):
            return True
        free_player = self.settings.value("mv_stop_docked", "true") == "true"
        win = getattr(self, "_multiview_win", None)
        target = None
        cells = list(getattr(win, "cells", None) or [])
        if cells:
            if cell is not None and 0 <= cell < len(cells):
                target = cells[cell]
            else:
                target = next((c for c in cells if c.url is None),
                              getattr(win, "_focused", None) or cells[0])
        uses = self._conn_uses(free_player=free_player, free_cell=target)
        v = self._conn_verdict(uses, source=source)
        t = self._tuner_verdict(url, uses)
        go = v is None and t is None or self._conn_ask(
            v, "conn_what_multiview" if v is not None else "tuner_what", t)
        if go:
            self._conn_server_room(uses, source or None)
        return go

    def _conn_ok_cast(self, url: str = "", source_id: str = "") -> bool:
        """*url* cast to a TV: the player hands its stream over (it stops
        once the cast plays) and a cast running already gives way, so the
        cast is one more next to the rest - on *source_id*'s account (the
        source in use when not given)."""
        if not connections.is_network(url):
            return True
        src = str(source_id or self._conn_source())
        uses = [u for u in self._conn_uses(free_player=True) if u.kind != "cast"]
        v = self._conn_verdict(uses, source=src)
        t = self._tuner_verdict(url, uses)
        go = v is None and t is None or self._conn_ask(
            v, "conn_what_cast" if v is not None else "tuner_what", t)
        if go:
            self._conn_server_room(uses, src)
        return go

    def _conn_ok_cast_buffer(self, url: str = "", source_id: str = "") -> bool:
        """The rewind buffer kept recording beside a cast of *url* (Settings >
        Casting's switch asks for it): one more connection to the provider
        next to the cast's, both *source_id*'s (the source in use when not
        given). Within a known limit with room for both it goes ahead; over
        a known limit, or with none known - most accounts allow one - it is
        asked first, never opened silently. True to keep it."""
        active = self._conn_source()
        src, cap, v = str(source_id or active), None, None
        uses: list[Use] | None = None
        try:
            self._conn_seen()
            cap = (self._conn_limit() if src == active else None) or self._conn_ledger().limit(src)
            if cap:
                uses = [u for u in self._conn_uses(free_player=True) if u.kind != "cast"]
                uses.append(Use("cast", src, url))
                v = self._conn_ledger().check(src, uses, 1, limit=cap)
        except Exception as e:
            log.debug("connections: the buffer beside the cast not counted: %s", e)
            cap, v, uses = None, None, None
        if cap and v is None:
            self._conn_server_room(uses, src)
            return True
        parts = [tr("conn_what_cast_buffer"),
                 self._conn_warning(v) if v is not None else tr("conn_limit_unknown")]
        idx = self._choice_dialog(
            tr("conn_title"), "\n\n".join(parts),
            [(tr("conn_anyway"), "normal"), (tr("cast_buffer_without"), "primary")])
        go = idx == 0
        log.info("connections: the rewind buffer beside the cast (%s) - %s",
                 f"{v.in_use} of {v.limit} in use" if v is not None else "limit unknown",
                 "kept anyway" if go else "not kept")
        if go and uses is not None:
            self._conn_server_room(uses, src)    # only once the person said so
        return go

    def _conn_ok_test(self, source_id: str = "") -> bool:
        """A test about to read a stream of *source_id* (the active source
        when not given) for a moment: one more connection to the account.
        Nothing of the app's on it and no limit known to be full: it goes
        ahead. Else asked first - over a known limit, or with none known
        while something plays from it (most accounts allow one, and the
        provider may cut what plays) - never opened silently. True to read."""
        active = self._conn_source()
        src = str(source_id or active)
        own, cap, v = 1, None, None
        uses: list[Use] | None = None
        try:
            self._conn_seen()
            uses = self._conn_uses()
            own = connections.own_count(uses, src)
            cap = (self._conn_limit() if src == active else None) or self._conn_ledger().limit(src)
            v = self._conn_ledger().check(src, uses, 1, limit=cap) if cap else None
        except Exception as e:
            log.debug("connections: the test not counted: %s", e)
            own, cap, v = 1, None, None
        if v is None and (cap or not own):
            self._conn_server_room(uses, src)
            return True
        parts = [tr("conn_what_test"),
                 self._conn_warning(v) if v is not None else tr("conn_test_unknown")]
        idx = self._choice_dialog(
            tr("conn_title"), "\n\n".join(parts),
            [(tr("conn_anyway"), "normal"), (tr("common_cancel"), "primary")])
        go = idx == 0
        log.info("connections: a test's stream (%s) - %s",
                 f"{v.in_use} of {v.limit} in use" if v is not None else "limit unknown",
                 "read anyway" if go else "not read")
        if go:
            self._conn_server_room(uses, src)     # only once the person said so
        return go

    # -- a box's tuners (core/tuners.py) -------------------------------------------------

    def _tuner_cache(self) -> tuners.StateCache:
        cache = getattr(self, "_tuner_states", None)
        if cache is None:
            cache = self._tuner_states = tuners.StateCache()
        return cache

    def _tuner_source_name(self, pid: str) -> str:
        store = getattr(self, "playlist_store", None)
        get = getattr(store, "get", None)
        try:
            pl = get(pid) if callable(get) and pid else None
        except Exception:
            pl = None
        name = str((pl or {}).get("name") or "")
        if not name and pid == self._conn_source():
            name = str((getattr(self.client, "box_info", None) or {}).get("name") or "")
        return name or tr("box_info_title")

    def _tuner_boxes(self) -> list[tuple[str, str, Any]]:
        """(source id, name, client) of each Enigma2 box source: the active
        one's own client, the others' made once (no request)."""
        return self._kind_clients("enigma2", "tuner_state", "_tuner_clients")

    def _satip_servers(self) -> list[tuple[str, str, Any]]:
        """(source id, name, client) of each SAT>IP source, as for boxes."""
        return self._kind_clients("satip", "tuner_verdict", "_satip_clients")

    def _kind_clients(self, kind: str, has: str, memo: str) -> list[tuple[str, str, Any]]:
        active = self._conn_source()
        out: list[tuple[str, str, Any]] = []
        client = getattr(self, "client", None)
        if hasattr(client, has):
            out.append((active, self._tuner_source_name(active), client))
        lists = getattr(getattr(self, "playlist_store", None), "playlists", None)
        try:
            pls = list(lists()) if callable(lists) else []
        except Exception:
            pls = []
        made = getattr(self, memo, None)
        if made is None:
            made = {}
            setattr(self, memo, made)
        for pl in pls:
            pid = str(pl.get("id") or "")
            if pl.get("kind") != kind or not pid or pid == active:
                continue
            mark = (pl.get("server"), pl.get("username"), pl.get("password"),
                    repr(sorted((pl.get("options") or {}).items())))
            got = made.get(pid)
            if got is None or got[0] != mark:
                try:
                    from ..providers.client import make_client
                    got = made[pid] = (mark, make_client(pl))
                except Exception as e:
                    log.debug("tuners: no client for a box source: %s", e)
                    continue
            out.append((pid, str(pl.get("name") or "") or tr("box_info_title"), got[1]))
        return out

    def _tuner_box_for(self, url: str) -> tuple[str, Any] | None:
        """(source id, client) of the box *url* streams from, or None."""
        for pid, _name, client in self._tuner_boxes():
            own = getattr(client, "own_stream", None)
            try:
                if callable(own) and own(url):
                    return pid, client
            except Exception:
                continue
        return None

    def _tuner_state(self, pid: str, client: Any, ask: bool = True) -> tuners.BoxState | None:
        """What box *pid* last said (asked again, off the window's thread,
        once it is a few seconds old), or None: nothing yet, or it did not
        answer."""
        cache = self._tuner_cache()
        if ask and cache.due(pid):
            self._tuner_ask(pid, client)
        return cache.state(pid)

    def _tuner_ask(self, pid: str, client: Any) -> None:
        pool = getattr(self, "pool", None)
        if pool is None:
            return
        cache = self._tuner_cache()
        cache.asking(pid)
        try:
            run_async(pool, _tuner_fetch, self._tuner_came, None, pid, client)
        except Exception as e:
            cache.failed(pid)
            log.debug("tuners: the box not asked: %s", e)

    def _tuner_came(self, result: Any) -> None:
        try:
            pid, state, why = result
        except (TypeError, ValueError):
            return
        cache = self._tuner_cache()
        if state is None:
            cache.failed(pid)
            log.debug("tuners: the box did not answer (%s) - nothing checked against it",
                      type(why).__name__ if why is not None else "-")
        else:
            cache.put(pid, state)
        self._tuner_marks = None
        lst = getattr(self, "listw", None)
        if lst is not None and pid == self._conn_source():
            try:
                lst.viewport().update()
            except Exception:
                pass
        self._in_use_refill()

    def _tuner_own(self, uses: list[Use], client: Any) -> list[tuners.Tune]:
        """The app's own streams from *client*'s box (as *uses* are), each
        on its transponder."""
        own = getattr(client, "own_stream", None)
        out: list[tuners.Tune] = []
        for u in uses:
            try:
                mine = bool(u.url) and callable(own) and bool(own(u.url))
            except Exception:
                mine = False
            key = tuners.stream_key(u.url) if mine else None
            if key:
                out.append(tuners.Tune(u.kind, "", key))
        return out

    def _satip_verdict(self, url: str, uses: list[Use]) -> tuple[bool, tuners.Verdict | None]:
        """(whether *url* is a SAT>IP server's, the Verdict when its tuners
        would run out with it next to *uses*) - counted here, no request."""
        try:
            for _pid, _name, client in self._satip_servers():
                if client.own_stream(url):
                    return True, client.tuner_verdict(url, uses)
        except Exception as e:      # the check must never stand in the way
            log.debug("tuners: the SAT>IP server's not checked: %s", e)
        return False, None

    def _tuner_verdict(self, url: str, uses: list[Use]) -> tuners.Verdict | None:
        """Would *url*, from a box, cut off a recording or what the box shows
        on its TV, next to *uses* (the app's own as they will be)? The
        Verdict when it would; None when not, or when it cannot be told. A
        SAT>IP server's: when its tuners would run out (``counted``)."""
        # A SAT>IP server's count first; with room there, an Enigma2 box on
        # the same address (minisatip on the receiver) is still asked - its
        # recordings and its TV are what this check is for. A SAT>IP
        # stream itself names no transponder, so nothing more is asked.
        _mine, sat = self._satip_verdict(url, uses)
        if sat is not None:
            return sat
        try:
            key = tuners.stream_key(url)
            box = self._tuner_box_for(url) if key else None
            if box is None:
                return None
            pid, client = box
            self._tuner_tick_on()
            state = self._tuner_state(pid, client)
            if state is None:
                log.debug("tuners: nothing known of the box yet - not checked")
                return None
            v = tuners.verdict(state, key, self._tuner_own(uses, client))
        except Exception as e:      # the check must never stand in the way
            log.debug("tuners: not checked: %s", e)
            return None
        if v is not None and v.kind == tuners.LAST:
            log.debug("tuners: this takes the box's last free tuner")
        return v if v is not None and v.kind == tuners.CUTS else None

    def _tune_words(self, users) -> str:
        """What holds a tuner, said: "the recording “News”, this app's
        player"."""
        parts: list[str] = []
        for t in users:
            if t.what == tuners.TV:
                word = tr("tuner_w_tv", name=t.name) if t.name else tr("tuner_w_tv_any")
            elif t.what == tuners.RECORDING and t.name:
                word = tr("tuner_w_rec", name=t.name)
            else:
                word = tr("tuner_w_own", what=tr(KIND_WORDS.get(t.what, "conn_k_player")))
            if word not in parts:
                parts.append(word)
        return ", ".join(parts)

    def _tuner_warning(self, v: tuners.Verdict) -> str:
        if v.counted:
            # A SAT>IP server: all its tuners held by the app's own streams
            # (or, no number said, one), whichever gives way.
            who = self._tune_words(v.cut)
            head = (tr("satip_full", n=v.tuners, system=v.system) if v.tuners and v.system
                    else tr("satip_full_any", n=v.tuners) if v.tuners else tr("satip_unknown"))
            return " ".join(x for x in (head, tr("conn_used_by", who=who) if who else "",
                                        tr("satip_risk")) if x)
        return tr("tuner_full", n=v.tuners) + " " + tr("tuner_cuts", what=self._tune_words(v.cut))

    def _tuner_ok(self, url: str, uses: list[Use] | None = None,
                  what: str = "tuner_what") -> bool:
        """*url* about to play in the docked player (*uses* None: the app's
        own as they will be once the player lets its stream go, a cast
        giving way too) or recorded (*uses* as they are): asked first when
        it would cut off a recording or the box's TV. True to go on."""
        if uses is None:
            p = getattr(self, "player", None)
            if p is not None and url and url == getattr(p, "current_url", None):
                return True
            uses = [u for u in self._conn_uses(free_player=True) if u.kind != "cast"]
        t = self._tuner_verdict(url, uses)
        return t is None or self._conn_ask(None, what, t)

    # -- kept fresh while a box is the source, and the list's ring ------------------------

    def _tuner_tick_on(self) -> None:
        try:
            from PyQt6.QtCore import QObject, QTimer
        except Exception:
            return
        if not isinstance(self, QObject):
            return
        timer = getattr(self, "_tuner_timer", None)
        if timer is None:
            timer = self._tuner_timer = QTimer(self)
            timer.setInterval(TUNER_TICK_MS)
            timer.timeout.connect(self._tuner_tick)
        if not timer.isActive():
            timer.start()

    def _tuner_tick(self) -> None:
        client = getattr(self, "client", None)
        if not hasattr(client, "tuner_state"):
            timer = getattr(self, "_tuner_timer", None)
            if timer is not None:
                timer.stop()
            self._tuner_marks = None
            return
        visible = getattr(self, "isVisible", None)
        if callable(visible) and not visible():
            return
        pid = self._conn_source()
        if self._tuner_cache().due(pid):
            self._tuner_ask(pid, client)

    def tuner_mark(self, it: dict | None, kind: str) -> bool:
        """Whether a box's channel in the list carries the quiet ring: it
        cannot be watched now without cutting off a recording or what the
        box shows on its TV."""
        try:
            client = getattr(self, "client", None)
            key_of = getattr(client, "tuner_key", None)
            sid = (it or {}).get("stream_id")
            if kind != "live" or sid is None or not callable(key_of):
                return False
            self._tuner_tick_on()
            state = self._tuner_state(self._conn_source(), client)
            if state is None:
                return False
            memo = getattr(self, "_tuner_marks", None)
            if memo is None or memo[0] is not state:
                uses = [u for u in self._conn_uses(free_player=True) if u.kind != "cast"]
                memo = self._tuner_marks = (state, self._tuner_own(uses, client), {})
            marks = memo[2]
            if sid not in marks:
                v = tuners.verdict(state, key_of(sid), memo[1])
                marks[sid] = v is not None and v.kind == tuners.CUTS
            return bool(marks[sid])
        except Exception as e:
            log.debug("tuners: no mark: %s", e)
            return False

    # -- Nerd > Diagnostics: In use now ---------------------------------------------------

    def _in_use_lines(self, ask: bool = True) -> list[tuple[str, str]]:
        """The card's lines, ("head" | "item" | "note", text): the
        connections the app holds to each source (and another device's, as
        the provider last said), then each box's tuners and what each is
        used for. *ask*: a box not asked a moment ago is asked again."""
        lines: list[tuple[str, str]] = []
        uses = self._conn_uses()
        led = self._conn_ledger()
        active = self._conn_source()
        try:
            self._conn_seen()
        except Exception as e:
            log.debug("in use: the provider's count not read: %s", e)
        by_source: dict[str, list[Use]] = {}
        for u in uses:
            by_source.setdefault(u.source or active, []).append(u)
        if active not in by_source and self._conn_limit():
            by_source[active] = []
        for pid, used in by_source.items():
            limit = (self._conn_limit() if pid == active else None) or led.limit(pid)
            others = led.others(pid)
            n = len(used) + others
            count = tr("conn_in_use", n=n, max=limit) if limit else tr("conn_in_use_only", n=n)
            line = tr("inuse_source", name=self._tuner_source_name(pid), count=count)
            who = self._conn_who(connections.kinds_of(used, pid), others)
            lines.append(("head", line + (" " + tr("conn_used_by", who=who) if who else "")))
        served = getattr(self, "_server_in_use_lines", None)
        lines += served() if callable(served) else []
        if not lines:
            lines.append(("item", tr("inuse_none")))
        boxes = self._tuner_boxes()
        cache = self._tuner_cache()
        for pid, name, client in boxes:
            state = self._tuner_state(pid, client, ask=ask)
            if state is None:
                key = "inuse_box_asking" if cache.is_asking(pid) else "inuse_box_silent"
                lines.append(("head", tr(key, name=name)))
                continue
            if not state.tuners:
                lines.append(("head", tr("inuse_box_unknown", name=name)))
                continue
            held = tuners.groups(state.users(self._tuner_own(uses, client)))
            lines.append(("head", tr("inuse_box_tuners", name=name, n=len(held), total=state.tuners)))
            lines += [("item", "• " + self._tune_words(users)) for users in held.values()]
        if boxes:
            lines.append(("note", tr("inuse_mark_note")))
        for pid, name, client in self._satip_servers():
            try:
                own = [u for u in uses if u.url and client.own_stream(u.url)]
                limit = client.tuner_limit()
            except Exception as e:
                log.debug("in use: a SAT>IP server not counted: %s", e)
                continue
            if pid != active and not own:
                continue
            lines.append(("head", tr("inuse_box_tuners", name=name, n=len(own), total=limit) if limit
                          else tr("inuse_satip_unknown", name=name, n=len(own))))
            if own:
                lines.append(("item", "• " + self._tune_words([tuners.Tune(u.kind) for u in own])))
        return lines

    def _in_use_card(self, fill: Callable[[list[tuple[str, str]]], None]) -> None:
        """Nerd > Diagnostics' In use now, shown: *fill* gets its lines now,
        and again as each box answers while the card shows."""
        self._in_use_fill = fill
        fill(self._in_use_lines())

    def _in_use_refill(self) -> None:
        fill = getattr(self, "_in_use_fill", None)
        if fill is None:
            return
        owner = getattr(fill, "__self__", None)
        try:
            from PyQt6 import sip
            if owner is not None and sip.isdeleted(owner):
                self._in_use_fill = None
                return
            if owner is not None and not owner.isVisible():
                return
            fill(self._in_use_lines(ask=False))
        except Exception as e:
            log.debug("in use: not shown again: %s", e)
