"""Recordings with the window closed: ``dopeiptv --server`` (headless.py)
records what is scheduled - the window's own recorder (core/recording.py)
run without a window, beside the home's tuner (services/home_server.py).

**Which jobs.** The household's list of scheduled recordings
(``recording_jobs``, shared by every profile) and the household's series
rules (``series_rules_<source id>``) - never a profile's own reminders. A
job is the server's only while no window runs (``window.alive``,
core/rec_claims.py: the window holds it locked): the window counts its
player and its connections and asks the person; the server leaves it what
comes due meanwhile, and a recording it already records goes on. A job
whose source is not one of the household's is left to the window. Each job
is claimed by exactly one recorder as it comes due (core/rec_claims.py), so
a window and a server - on one computer, or two that share the settings
folder - never record it twice; a claim whose recorder crashed is taken
over, the rest of the job recorded.

**The same recording.** Through RecordingManager: the padding, the file's
name (Nerd's template), its folder inside the recordings folder, the
``.info.json`` beside it, MP4 (rec_mp4), the command after it, DISK_FLOOR,
the size limits, a portal's address asked for as it starts.

**One connection.** A recording counts against the account's limit with
the restream's channels (core/restream.py) and, while fresh, what the
provider said of other devices; it never goes over it: it waits - said in
the log, retried every few seconds - until there is room (a channel nobody
watches gives way, a device watching never does), and fails at its stop
time saying why (``NO_ROOM``), as the window does. A channel the server
streams to a device already is recorded from that buffer session
(core/dvr_record.BufferRecorder) - no connection of its own. One it opens
itself - an http(s) channel - it reads into a session of the same pool
(``SharedRecording``), so a device that tunes to it then shares that one
connection (the restream's ``app_session``); any other address the
recorder reads itself as the window's does (``Feed``, the bridge, a private
file - never an address in its arguments).

**Series.** Every twenty minutes, while no window runs, each source's
recording rules are looked through its guide on disk (``load_cached``: no
download here - guide_keeper.py keeps those guides fresh) on a thread of
its own, and what they find is scheduled on the main thread
(core/series_plan.schedule) - what a box's AutoTimer records left to the
box, as the window does.

**What it says.** The log (titles through privacy.for_log), and the
server's page (``status``: what records, waits and comes next, and what the
window records). Nothing is sent anywhere. QtCore only - no widget.
"""
from __future__ import annotations

import threading
import time
from functools import partial
from typing import Any, Callable, cast

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal

from ..core import connections, rec_claims, series_plan, series_rules
from ..core.dvr_session import DvrSession
from ..core.log import log
from ..core.privacy import for_log
from ..core.recording import RecordingManager, live_address, piped
from ..core.restream import BOX, SERVER, channel_key

#: The hold a recording puts on the buffer session it opens.
HOLD_PREFIX = "rec:"


class SharedRecording:
    """A recording read from a buffer session of the server's pool, which
    it opened for itself: the recorder's own connection, shared with every
    device that tunes to the channel meanwhile. It waits for the provider's
    first bytes on a thread of its own, records as a BufferRecorder does
    from then on, and lets go of the session as it ends. What
    RecordingManager asks of a buffer's recorder: stop, alive, stopping,
    join, error - and ``classic`` when the provider sent an HLS playlist
    (the recorder reads it itself then)."""

    #: How long the provider's first bytes are waited for.
    FIRST_DATA_S = 60.0

    def __init__(self, pool: Any, session: DvrSession, who: str, path: str,
                 since: float | None = None) -> None:
        self.pool, self.session, self.who = pool, session, who
        self.path = path
        self.since = since
        self.error = ""
        self.classic = ""
        self._real: Any = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="rec-shared", daemon=True)

    def start(self) -> "SharedRecording":
        self._thread.start()
        return self

    def _run(self) -> None:
        s = self.session
        try:
            deadline = time.monotonic() + self.FIRST_DATA_S
            while not self._stop.is_set():
                if s.store.wait_for_data(0.5):
                    break
                refused = s.refused
                if refused == "a playlist":
                    self.classic = "an HLS playlist"
                    return
                if refused:
                    self.error = f"the provider sent {refused}, not a stream"
                    return
                if s.state in ("failed", "stopped") or time.monotonic() > deadline:
                    self.error = "no data captured"
                    return
            if self._stop.is_set():
                return
            real = s.record(self.path, self.since)
            self._real = real
            if self._stop.is_set():
                real.stop()             # stopped while it began
            real.join()
            self.error = real.error
        except Exception as e:
            self.error = str(e) or type(e).__name__
            log.info("record: from the server's buffer: %s", self.error)
        finally:
            try:
                self.pool.let_go(s, self.who)
            except Exception as e:
                log.debug("record: the buffer not let go: %s", e)

    def stop(self, wait: float = 0.0) -> None:
        self._stop.set()
        real = self._real
        if real is not None:
            real.stop()
        if wait and self._thread.is_alive():
            self._thread.join(wait)

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    @property
    def bytes(self) -> int:
        real = self._real
        return int(getattr(real, "bytes", 0)) if real is not None else 0

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)


def _live(client: Any, stream_id: Any) -> str:
    """(the recorder's thread) A channel's address, asked as it starts."""
    return str(client.live_url(stream_id, "ts") or "")


def _plan_source(client: Any, pl: dict, rules: list[dict], keep: Any, shift: float,
                 guide_path: str) -> Any:
    """(the planner's thread) What *rules* find in source *pl*'s guide on
    disk - never a download."""
    from ..providers.epg import XmltvGuide
    guide = XmltvGuide(client, pl.get("epg_url") or None, cache_path=guide_path)
    if not guide.load_cached():
        log.info("server: no guide on disk for source %s - its series rules wait for it",
                 pl.get("id"))
        return None
    return series_plan.find(client, guide, rules, keep, time.time(), shift, str(pl["id"]))


class ServerRecorder(QObject):
    """The server's recorder (headless.py makes one beside its HomeServer).
    Lives on the main thread, whose Qt event loop runs its ticks; what the
    restream asks of it (``connections``, ``session_for``) and the page
    (``status``) may come from any thread."""

    #: The first look through the guides, a while after the start (the
    #: guide keeper's own first look goes before it), then this often - the
    #: window's own guide check.
    PLAN_FIRST_S = 90.0
    PLAN_EVERY_S = 20 * 60.0
    #: How long a box's answer about its tuners stands for a waiting job.
    BOX_KEEP_S = 20.0
    #: How many recordings to come the page lists.
    NEXT_SHOWN = 8

    _found = pyqtSignal(object)

    def __init__(self, settings: Any, server: Any, pool: Callable[[], Any],
                 claims: Any, guide_path: Callable[[str], str] | None = None,
                 plan: bool = True, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.server = server
        self.claims = claims
        self._pool = pool
        self._guide_path = guide_path
        self._box: dict[str, tuple[float, bool]] = {}
        self._planning = False
        self._window = False
        self._left: set[str] = set()
        self.rec = RecordingManager(settings, self, claims=claims)
        self.rec.defer = self._window_runs
        self.rec.takes = self._takes
        self.rec.may_open = self._may_open
        self.rec.address_for = self._address_for
        self.rec.open_shared = self._open_shared
        # Through Any: PyQt6's stubs differ between versions on connect's type
        # (as services/media_session.py does).
        cast(Any, self._found).connect(self._schedule, Qt.ConnectionType.QueuedConnection)
        # The sources' names for the page, read here (the settings are this
        # thread's) and kept for the server's threads.
        self._names: dict[str, str] = {}
        self._with_rules: list[dict] = []
        self._read_names()
        self.rec.jobs_changed.connect(self._read_names)
        # A stop the window asks of a recording here, seen within a second:
        # the person's next channel opens its connection right after.
        self._stops = QTimer(self)
        self._stops.setInterval(1000)
        self._stops.timeout.connect(self.rec.check_stops)
        self._stops.start()
        self._plan_timer: QTimer | None = None
        if plan:
            self._plan_timer = QTimer(self)
            self._plan_timer.setInterval(int(self.PLAN_EVERY_S * 1000))
            self._plan_timer.timeout.connect(self.plan_soon)
            self._plan_timer.start()
            QTimer.singleShot(int(self.PLAN_FIRST_S * 1000), self.plan_soon)
        n = sum(1 for j in self.rec.jobs if j.get("status") == "scheduled")
        log.info("server: recording what is scheduled - %d recording(s) to come", n)

    # -- the sources ----------------------------------------------------------------------
    @property
    def settings(self) -> Any:
        return self.rec.settings

    def _sources(self) -> list[dict]:
        from ..core.stores import PlaylistStore
        try:
            return [p for p in PlaylistStore(self.settings).playlists()
                    if p.get("id") and p.get("kind") != "local"]
        except Exception as e:
            log.debug("server: the sources not read: %s", e)
            return []

    def _source(self, pid: str) -> dict | None:
        return next((p for p in self._sources() if str(p["id"]) == pid), None)

    def _read_names(self) -> None:
        """(this thread) What the server's threads read of the settings,
        kept for them: the sources' names, the sources with rules."""
        self._names = {str(p["id"]): str(p.get("name") or "") for p in self._sources()}
        self._with_rules = [dict(p) for p in self.rule_sources()]

    def guide_sources(self) -> list[dict]:
        """(any thread: the guide keeper's) The sources with a recording
        rule, as last read here - their guides kept fresh too."""
        return list(self._with_rules)

    def _client(self, pl: dict) -> Any:
        return self.server.share._client(pl)

    def rule_sources(self) -> list[dict]:
        """The sources with a series rule that records: their guides are
        kept fresh too (guide_keeper.py)."""
        out = []
        for pl in self._sources():
            store = series_rules.SeriesRuleStore(self.settings, str(pl["id"]))
            if series_rules.of_action(store.rules(), series_rules.RECORD):
                out.append(pl)
        return out

    # -- a window runs ----------------------------------------------------------------------
    def _window_runs(self) -> bool:
        runs = bool(self.claims.present(rec_claims.WINDOW))
        if runs != self._window:
            self._window = runs
            log.info("server: %s", "the app's window runs - it records what comes due "
                     "meanwhile" if runs else "no window runs - the server records what "
                     "comes due")
        return runs

    def _takes(self, j: dict) -> bool:
        """(RecordingManager.takes) A job of one of the household's sources;
        one without - from before sources were kept with a recording, or of
        a source taken out - is the window's (said once)."""
        pid = str(j.get("source") or "")
        if pid and self._source(pid) is not None:
            return True
        if str(j["id"]) not in self._left:
            self._left.add(str(j["id"]))
            log.info("record: %s - its source is not one of the household's; left to the "
                     "app's window", for_log(j.get("title")))
        return False

    # -- a recording due now (RecordingManager.may_open) -----------------------------------
    @staticmethod
    def _stream_of(j: dict) -> str:
        sid = j.get("stream_key") or (j.get(RecordingManager.LATER) or {}).get("stream_id")
        return "" if sid is None else str(sid)

    def _may_open(self, j: dict) -> str:
        """OPEN, RIDE or HOLD for job *j*, due now. Its channel streamed by
        the server already: recorded from that buffer, no connection of its
        own (RIDE). Room on the account next to the restream's channels and
        the server's other recordings: OPEN. Otherwise HOLD - never one
        connection too many."""
        pid = str(j.get("source") or "")
        sid = self._stream_of(j)
        if sid and self._ride(j, pid, sid):
            return RecordingManager.RIDE
        url = str(j.get("url") or "")
        if url and not connections.is_network(url):
            return RecordingManager.OPEN        # a file of this computer's
        if self._box_busy(j, pid, sid, url):
            j.pop("_opening", None)
            return RecordingManager.HOLD
        # Counted as opening from here: a device that asks meanwhile counts it.
        j["_opening"] = True
        limit = max(0, int(self.server.limit(pid)))
        need = self.connections(pid)[0] + self.server.ledger.others(pid)
        self.server.restream.make_room(pid, need, idle_only=True)
        if need + self.server.restream.in_use(pid) <= limit:
            return RecordingManager.OPEN
        j.pop("_opening", None)
        return RecordingManager.HOLD

    def _ride(self, j: dict, pid: str, sid: str) -> bool:
        s = self.server.restream.session_of(pid, sid) or self.session_for(pid, sid)
        if s is None or not s.streaming:
            return False
        try:
            path = self.rec.build_path(str(j.get("title") or ""), str(j.get("folder") or ""),
                                       str(j.get("channel") or ""))
            span = s.span()
            lead = float(getattr(s, "KEY_LEAD", 0.0))
            # From its start - or, carried on from a window that closed, from
            # where that one left off (RecordingManager.resumes_at).
            since = (max(self.rec.resumes_at(j) or 0.0, float(span[0]) + lead)
                     if span else None)
            recorder = s.record(path, since)
        except Exception as e:
            log.info("record: %s not from the server's buffer (%s)", for_log(j.get("title")), e)
            return False
        self.rec.ride_player(j, path, recorder, player=False)
        return True

    def _box_busy(self, j: dict, pid: str, sid: str, url: str) -> bool:
        """An Enigma2 box's tuners: would this recording cut off one of its
        recordings or what its TV shows? Asked once in BOX_KEEP_S per job."""
        pl = self._source(pid)
        if pl is None or not url:
            return False
        try:
            client = self._client(pl)
        except Exception:
            return False
        if not hasattr(client, "tuner_state"):
            return False
        held = self._box.get(str(j["id"]))
        if held is not None and time.monotonic() - held[0] < self.BOX_KEEP_S:
            return held[1]
        busy = False
        try:
            refused = self.server._check(pid, sid, url, self.server.restream.urls(pid))
            busy = refused is not None and refused.reason == BOX
        except Exception as e:
            log.debug("record: the box not asked: %s", e)
        was = held[1] if held is not None else False
        self._box[str(j["id"])] = (time.monotonic(), busy)
        if busy and not was:
            log.info("record: %s waits - it would cut off a recording or what the box's TV "
                     "shows", for_log(j.get("title")))
        return busy

    # -- what the restream counts (any thread) -------------------------------------------
    def connections(self, pid: str) -> tuple[int, list[str]]:
        """The connections this computer's recordings hold to source *pid*,
        and the addresses they read: the server's own - over a connection of
        their own, or the buffer sessions they keep streaming - those about
        to open one, and the window's over its own (its claims)."""
        n = 0
        urls: list[str] = []
        for j in list(self.rec.jobs):
            src = str(j.get("source") or "")
            if src and src != pid:
                continue
            el = j.get("_elsewhere")
            if el is not None:
                if el.get("state") == rec_claims.RECORDING and el.get("own"):
                    n += 1
                continue
            status = j.get("status")
            if status == "scheduled" and (j.get("_opening") or j.get("_asking") is not None):
                n += 1
            elif (status == "recording" and j.get("proc") is not None
                  and connections.is_network(j.get("url"))):
                n += 1
                urls.append(str(j.get("url")))
        pool = self._pool()
        for s in (pool.sessions if pool is not None else []):
            if (s.streaming and str(getattr(s, "source", "") or "") in (pid, "")
                    and not s.held_only_by(SERVER) and (s.recording or s.held)):
                n += 1
                urls.append(str(s.url))
        return n, urls

    def session_for(self, pid: str, sid: str) -> DvrSession | None:
        """The buffer session a recording of the server keeps streaming for
        channel *sid* of *pid* - what a device that tunes to it joins."""
        pool = self._pool()
        key = "srv:" + channel_key(pid, sid)
        for s in (pool.sessions if pool is not None else []):
            if s.key == key and s.streaming and (s.recording or s.held):
                return s
        return None

    # -- its own connection, as a buffer session (RecordingManager.open_shared) -------------
    def _open_shared(self, j: dict) -> tuple[str, Any] | None:
        """An http(s) channel opened into a session of the pool, keyed as
        the restream keys it: one connection, shared with every device
        that tunes to it. None for another address (the recorder's own)."""
        url = str(j.get("url") or "")
        pid = str(j.get("source") or "")
        if not (pid and piped(url) and live_address(url)):
            return None
        pl = self._source(pid)
        if pl is None:
            return None
        sid = self._stream_of(j)
        pool = self._pool()
        path = self.rec.build_path(str(j.get("title") or ""), str(j.get("folder") or ""),
                                   str(j.get("channel") or ""))
        who = HOLD_PREFIX + str(j["id"])
        key = "srv:" + channel_key(pid, sid) if sid else who
        session = pool.acquire(url, key, who, self.server._ingest_of(pl), source=pid)
        rec = SharedRecording(pool, session, who, path, since=self.rec.resumes_at(j)).start()
        log.info("record: %s - read into the server's buffer, its one connection shared "
                 "with every device that tunes to it", for_log(j.get("title")))
        return path, rec

    def _address_for(self, j: dict) -> Callable[[], str] | None:
        """(RecordingManager.address_for) How a portal's recording gets its
        address as it starts: its source's client, off the main thread."""
        later = j.get(RecordingManager.LATER) or {}
        sid = later.get("stream_id")
        pid = str(later.get("source") or j.get("source") or "")
        pl = self._source(pid) if pid else None
        if sid is None or pl is None:
            return None
        return partial(_live, self._client(pl), sid)

    # -- series rules ------------------------------------------------------------------------
    def plan_soon(self) -> None:
        """Look through each source's guide for what its recording rules
        ask for, on a thread of its own - not while a window runs (it does
        that itself), one look at a time."""
        if self._planning or self.rec._no_claims:
            return
        try:
            if self._window_runs():
                return
        except OSError:
            return
        try:
            self.settings.sync()        # rules the window changed meanwhile
        except Exception as e:
            log.debug("server: the settings not read again: %s", e)
        self._read_names()
        work = []
        from ..core.stores import CategoryOverrides, ChannelOverrides
        for pl in self.rule_sources():
            pid = str(pl["id"])
            rules = [r for r in series_rules.SeriesRuleStore(self.settings, pid).rules()
                     if series_rules.action(r) == series_rules.RECORD]
            keep = None
            if any(r.get("scope") == series_rules.ANY for r in rules):
                excluded = CategoryOverrides(self.settings, f"category_overrides_{pid}") \
                    .excluded_ids("live", include_locked=True)
                chans = ChannelOverrides(self.settings, f"channel_overrides_{pid}")

                def keep(c: dict, excluded: set[str] = excluded, chans: Any = chans) -> bool:
                    return (str(c.get("category_id")) not in excluded
                            and not chans.is_hidden("live", str(c.get("stream_id"))))
            try:
                client = self._client(pl)
            except Exception as e:
                log.info("server: source %s not read for its series rules (%s)", pid, e)
                continue
            work.append((pl, client, rules, keep, self._shift(pl), self._guide_of(pid)))
        if not work:
            return
        self._planning = True
        threading.Thread(target=self._plan, args=(work,), name="server-series",
                         daemon=True).start()

    def _guide_of(self, pid: str) -> str:
        if self._guide_path is not None:
            return self._guide_path(pid)
        from ..providers.epg import epg_cache_path
        return epg_cache_path(pid)

    def _shift(self, pl: dict) -> float:
        """The guide offset in seconds, as the window's (Settings, and a
        source's own with Nerd)."""
        from ..core import nerd
        from ..core.num import whole
        base = whole(self.settings.value("epg_delay_min", 0), 0)
        if nerd.current().on:
            base += whole((pl.get("options") or {}).get("epg_shift"), 0)
        return base * 60.0

    def _plan(self, work: list) -> None:
        """(the planner's thread) Each source's look, handed to the main
        thread as it is done."""
        try:
            for pl, client, rules, keep, shift, guide_path in work:
                try:
                    found = _plan_source(client, pl, rules, keep, shift, guide_path)
                except Exception as e:
                    log.info("server: the guide of source %s not looked through: %s",
                             pl.get("id"), e)
                    continue
                if found is not None:
                    try:
                        self._found.emit((pl, found, series_plan.links_later(client),
                                          self.server.known_limit(str(pl["id"]))))
                    except RuntimeError:    # the server stopped meanwhile
                        return
        finally:
            self._planning = False

    def _schedule(self, payload: Any) -> None:
        """(the main thread) What a source's rules found, scheduled."""
        pl, found, later, maxc = payload
        pid = str(pl["id"])
        try:
            if self._window_runs():
                log.info("server: a window runs now - it schedules the series rules itself")
                return
            self.settings.sync()
            from ..core.stores import ChannelOverrides
            chans = ChannelOverrides(self.settings, f"channel_overrides_{pid}")
            kind = str(pl.get("kind") or "xtream")

            def name_of(ch: dict) -> str:
                base = str(ch.get("name") or ch.get("title") or "?")
                return chans.display_name("live", str(ch.get("stream_id")), base)

            def extra_of(ch: dict) -> dict:
                out: dict = {"source": pid, "source_kind": kind}
                if later and ch.get("stream_id") is not None:
                    out[RecordingManager.LATER] = {"source": pid, "stream_id": ch["stream_id"]}
                return out

            self.rec.refresh_shared()
            store = series_rules.SeriesRuleStore(self.settings, pid)
            done = series_plan.schedule(found, self.rec, store, self.settings, later=later,
                                        max_connections=maxc, name_of=name_of,
                                        extra_of=extra_of)
            if done.added:
                log.info("server: %d recording(s) of source %s scheduled by its series rules",
                         len(done.added), pid)
        except Exception as e:
            log.warning("server: the series rules of source %s not scheduled: %s", pid, e)

    # -- what the page says --------------------------------------------------------------------
    def status(self) -> dict:
        """What records, waits and comes next, for the server's page (any
        thread): each with its title (none for what the window plays
        privately), channel, source, times, and who records it."""
        names = self._names
        now = time.time()
        rows: list[dict[str, Any]] = []
        upcoming: list[dict[str, Any]] = []
        for j in list(self.rec.jobs):
            el = j.get("_elsewhere") or {}
            status = j.get("status")
            if status == "recording":
                state = "recording"
            elif status == "scheduled" and j.get("_held"):
                state = "held"
            elif status == "scheduled":
                state = "scheduled"
            else:
                continue
            private = bool(el.get("private"))
            row = {"title": "" if private else str(j.get("title") or ""),
                   "channel": "" if private else str(j.get("channel") or ""),
                   "private": private, "state": state,
                   "source_name": names.get(str(j.get("source") or ""), ""),
                   "start": int(float(j.get("start") or 0)),
                   "stop": int(float(j["stop"])) if j.get("stop") else None,
                   "since": round(now - float(j.get("_since") or j.get("start") or now)),
                   "by": str(el.get("who") or rec_claims.SERVER)}
            (upcoming if state == "scheduled" else rows).append(row)
        upcoming.sort(key=lambda r: r["start"])
        rows.sort(key=lambda r: (r["state"] != "recording", r["start"]))
        return {"now": rows, "next": upcoming[:self.NEXT_SHOWN],
                "more": max(0, len(upcoming) - self.NEXT_SHOWN)}

    # -- on and off ------------------------------------------------------------------------------
    def close(self) -> None:
        """The server stops: every recording ends (what it holds kept), a
        job still to come left for the next run - or the window."""
        if self._plan_timer is not None:
            self._plan_timer.stop()
        self._stops.stop()
        self.rec._timer.stop()
        try:
            self.rec.jobs_changed.disconnect(self._read_names)
        except (TypeError, RuntimeError):
            pass
        self.rec.shutdown()

    def after_close(self) -> None:
        """Once the buffers let go: the command after each recording."""
        self.rec.after_shutdown()


def start(settings: Any, server: Any, pool: Callable[[], Any],
          guide_path: Callable[[str], str] | None = None) -> ServerRecorder | None:
    """The server's recorder - None when its claims cannot be kept (then it
    records nothing: it could not know what the window records)."""
    claims = rec_claims.for_settings(settings, rec_claims.SERVER)
    if claims is None:
        log.warning("server: records nothing - the recordings' claims cannot be kept")
        return None
    return ServerRecorder(settings, server, pool, claims, guide_path=guide_path)
