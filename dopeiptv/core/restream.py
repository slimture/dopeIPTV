"""Server mode's restream (services/home_server.py serves it): a live channel
fetched once from the provider and handed to every device on the home
network that watches it - VLC, Kodi, a TV's IPTV app, Plex or Jellyfin
through the HDHomeRun tuner the server plays.

Built on the app-kept rewind buffer's pieces: a channel is a DvrSession -
one provider connection (core/dvr_ingest.py) into a store of short
segments (core/dvr_store.py) - in a DvrPool, and each viewer is a reader of
that store of its own. A viewer joins a few seconds behind live and follows
it; one that falls more than LAG_MAX behind (a slow network, a client that
stopped reading) is dropped: it never holds the others up, and what it may
lag behind is bounded. When the last viewer leaves, the connection stays
GRACE seconds (a client that reconnects, Plex's tuner check) and is let go
then - at once when another channel needs the tuner.

Run inside the window, the pool is the player's own: the app's player and
the restream share a channel's one connection, whichever opened it first
(``app_session``, DvrPool.adopt).

Tuners. A source allows ``limit(source)`` channels at once - the account's
limit when the provider says it, else one, or what the person set - and
the server ``total()`` in all. Everything else that holds a connection to
the source (``others``: the app's own player, recordings, multiview cells,
casts; another device on the account) counts first, and the app's own use
wins: ``make_room`` lets go of the server's channels the app needs, and a
request that would need one more connection than allowed is refused
(Busy) - never opened beyond the limit.

It counts, decides and moves bytes; the HTTP side only asks.
"""
from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from . import privacy
from .dvr_ingest import NotAStream
from .dvr_pool import DvrPool
from .dvr_session import DvrSession, IngestFactory
from .dvr_store import Segment
from .log import log, redact_url

#: The hold the restream puts on a session it reads.
SERVER = "server"
#: Why a request is refused (Busy.reason).
TUNERS, BOX, VPN, NO_DATA = "tuners", "box", "vpn", "no_data"


class Busy(Exception):
    """No tuner for this request: *reason* one of TUNERS, BOX, VPN,
    NO_DATA; *detail* a short sentence for the log and the client."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail or reason


@dataclass(eq=False)
class Viewer:
    """One device watching one channel."""

    id: int
    client: str
    channel: "Channel"
    since: float
    sent: int = 0
    gone: bool = False
    why: str = ""


@dataclass(eq=False)
class Channel:
    key: str
    source: str
    stream: str
    name: str
    url: str
    session: DvrSession
    pool: DvrPool
    opened: float
    viewers: list[Viewer] = field(default_factory=list)
    idle_since: float | None = None
    closed: bool = False
    #: The app's own session was there first (its player watched it).
    joined_app: bool = False


def channel_key(source: str, stream: str) -> str:
    return f"{source}\x1f{stream}"


def _private(stream: str) -> bool:
    """Whether the person's own viewing of channel *stream* is private
    (core/privacy.py): private viewing on, or a playback that began in it -
    the window keys it by the row's stream id, a number or its text."""
    keys: list[object] = [stream]
    if stream.isdigit():
        keys.append(int(stream))
    return any(privacy.playback_private("live", k) for k in keys)


# -- an adaptive (HLS) channel: copied into MPEG-TS by ffmpeg -----------------------------

def is_hls(url: str) -> bool:
    """An HLS playlist rather than a TS stream (an M3U source's .m3u8, a
    media server's master.m3u8): what a tuner client cannot play as it is."""
    from urllib.parse import urlsplit
    try:
        path = urlsplit(url).path.lower()
    except ValueError:
        return False
    return path.endswith((".m3u8", ".m3u"))


class _Piped:
    """ffmpeg's standard output as the Ingest reads a provider's answer: the
    one connection to the provider is ffmpeg's, closed with it."""

    raw = None

    def __init__(self, proc: Any) -> None:
        self.proc = proc

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, size: int) -> Iterator[bytes]:
        out = self.proc.stdout
        while out is not None:
            data = out.read1(size)
            if not data:
                return
            yield data

    def close(self) -> None:
        import subprocess
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(2.0)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.proc.stdout is not None:
            self.proc.stdout.close()


def ffmpeg_opener(ffmpeg: str, src: dict | None) -> Callable[..., Any]:
    """An Ingest opener that has ffmpeg read an HLS channel and copy its
    picture and sound, unchanged, into MPEG-TS - with the source's user
    agent, headers and proxy as the player has them (Nerd)."""
    import subprocess

    from . import nerd
    from .dvr_ingest import USER_AGENT
    from .player_exec import NO_WINDOW

    def opener(url: str, **_kw: Any) -> _Piped:
        n = nerd.current()
        cmd = [ffmpeg, "-nostdin", "-loglevel", "error",
               "-user_agent", n.user_agent(src) if n.on else USER_AGENT]
        if n.on:
            headers = n.headers(src)
            if headers:
                cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
            px = n.proxy(src, url) if n.flag("nerd_proxy_player", True) else ""
            if px:
                cmd += ["-http_proxy", px]
        cmd += ["-i", url, "-map", "0:v?", "-map", "0:a?", "-c", "copy", "-f", "mpegts", "pipe:1"]
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        return _Piped(proc)
    return opener


class Restreamer:
    #: Seconds behind live a viewer may fall before it is dropped.
    LAG_MAX = 20.0
    #: Seconds a channel nobody watches keeps its connection.
    GRACE = 10.0
    #: How long a new viewer waits for the provider's first bytes.
    FIRST_DATA = 25.0
    #: Where a viewer joins: this far behind the newest byte.
    JOIN_LEAD = 3.0
    #: After a reconnect, how long a viewer waits for the next run.
    RUN_WAIT = 15.0
    #: Bytes handed to the HTTP side at a time.
    CHUNK = 188 * 1024
    TICK = 1.0

    def __init__(self, pool: Callable[[], DvrPool],
                 limit: Callable[[str], int],
                 total: Callable[[], int],
                 others: Callable[[str], int] | None = None,
                 app_session: Callable[[str, str], DvrSession | None] | None = None,
                 ingest_for: Callable[[str], IngestFactory | None] | None = None,
                 check: Callable[[str, str, str, list[str]], Busy | None] | None = None,
                 grace: float | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        # *limit* and *total* are asked under the lock: they must not block.
        # *others*, *app_session* and *check* may (the window's thread, a
        # box asked over the network) and are asked outside it.
        self._pool = pool
        self._limit = limit
        self._total = total
        self._others = others or (lambda _s: 0)
        self._app_session = app_session or (lambda _s, _i: None)
        self._ingest_for = ingest_for or (lambda _s: None)
        self._check = check
        self.grace = self.GRACE if grace is None else grace
        self._clock = clock
        self._lock = threading.RLock()
        self._channels: dict[str, Channel] = {}
        self._pools: set[Any] = set()
        self._ids = itertools.count(1)
        self._epoch = 0
        self._closed = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._tick, name="restream", daemon=True)
        self._thread.start()

    # -- what holds a connection -------------------------------------------------

    def _app_uses(self, ch: Channel) -> bool:
        """The app itself reads *ch*'s session (its player, a recording, a
        cast): the connection is the app's, counted in *others*."""
        s = ch.session
        return s is ch.pool.active or not s.held_only_by(SERVER)

    def _own(self, source: str | None = None) -> list[Channel]:
        """The server's channels that hold a connection of their own (on
        *source*, else on every source)."""
        return [ch for ch in self._channels.values()
                if not ch.closed and ch.session.streaming
                and (source is None or ch.source == source) and not self._app_uses(ch)]

    def in_use(self, source: str | None = None) -> int:
        """The server's own connections (to *source*, else to every one)."""
        with self._lock:
            return len(self._own(source))

    def session_of(self, source: str, stream: str) -> DvrSession | None:
        """The session the server streams *stream* of *source* from, while
        it streams: the person tuning to it takes no connection of its own."""
        with self._lock:
            ch = self._channels.get(channel_key(source, stream))
            if ch is None or ch.closed or not ch.session.streaming:
                return None
            return ch.session

    # -- a viewer ------------------------------------------------------------------

    def watch(self, source: str, stream: str, url: str, client: str,
              name: str = "") -> Viewer:
        """A viewer for channel *stream* of *source* (provider address
        *url*): on its channel's connection when it has one, else on a new
        one - Busy when that would be one too many."""
        key = channel_key(source, stream)
        for _attempt in range(3):
            with self._lock:
                if self._closed:
                    raise Busy(TUNERS, "the server is stopping")
                epoch = self._epoch
                ch = self._channels.get(key)
                need_new = ch is None or ch.closed or not ch.session.streaming
                urls = [c.url for c in self._own(source)]
            others = self._others(source)
            app = self._app_session(source, stream) if need_new else None
            if need_new and (app is None or not app.streaming) and self._check is not None:
                refused = self._check(source, stream, url, urls)
                if refused is not None:
                    raise refused
            with self._lock:
                if self._epoch != epoch:
                    continue            # the app took a connection meanwhile: counted again
                v = self._join(key, source, stream, url, client, name, others, app)
                if v is None:
                    break
                return v
        raise Busy(TUNERS, "all tuners in use")

    def _join(self, key: str, source: str, stream: str, url: str, client: str,
              name: str, others: int, app: DvrSession | None) -> Viewer | None:
        ch = self._channels.get(key)
        if ch is not None and (ch.closed or not ch.session.streaming):
            self._drop(ch, "its connection ended")
            ch = None
        if ch is None and app is not None and app.streaming:
            app.hold(SERVER)
            ch = Channel(key, source, stream, name, url, app, self._pool(), self._clock(),
                         joined_app=True)
            self._channels[key] = ch
            log.info("restream: %s joins the app's own connection",
                     "<private>" if _private(stream) else name or "a channel")
        if ch is None:
            if not self._room_for(source, others):
                return None
            pool = self._pool()
            pool.shared = True
            self._pools.add(pool)
            try:
                session = pool.acquire(url, "srv:" + key, SERVER, self._ingest_for(source))
            except NotAStream as e:
                raise Busy(NO_DATA, f"the provider sent {e}, not a stream") from None
            ch = Channel(key, source, stream, name, url, session, pool, self._clock())
            self._channels[key] = ch
            log.info("restream: %s opened (%s) - %d of %d tuner(s) in use", name or "a channel",
                     redact_url(url), len(self._own()), self._total())
        v = Viewer(next(self._ids), client, ch, self._clock())
        ch.viewers.append(v)
        ch.idle_since = None
        return v

    def _room_for(self, source: str, others: int) -> bool:
        """Room for one more connection to *source*: within its limit next
        to everything else on it, and within the server's tuners. Channels
        nobody watches give way first."""
        def fits() -> bool:
            return (others + len(self._own(source)) + 1 <= max(0, self._limit(source))
                    and len(self._own()) + 1 <= max(0, self._total()))
        if fits():
            return True
        idle = sorted((ch for ch in self._own() if not ch.viewers),
                      key=lambda c: c.idle_since or 0.0)
        for ch in idle:
            self._drop(ch, "another channel took its tuner")
            if fits():
                return True
        return False

    def leave(self, v: Viewer) -> None:
        with self._lock:
            v.gone = True
            ch = v.channel
            if v in ch.viewers:
                ch.viewers.remove(v)
                log.info("restream: a viewer left %s after %.0f s, %.1f MB%s",
                         ch.name or "a channel", self._clock() - v.since, v.sent / 1e6,
                         f" ({v.why})" if v.why else "")
            if not ch.viewers and ch.idle_since is None:
                ch.idle_since = self._clock()

    def stream(self, v: Viewer) -> Iterator[bytes]:
        """*v*'s bytes, from a few seconds behind live on, until it leaves,
        falls too far behind or its channel ends. The viewer leaves when the
        generator is done (or closed)."""
        ch = v.channel
        store = ch.session.store
        try:
            if not store.wait_for_data(self.FIRST_DATA):
                v.why = "no picture from the provider"
                return
            seg = store.live_segment(self.JOIN_LEAD)
            while seg is not None and not (v.gone or ch.closed):
                at = [seg]

                def moved(s: Segment, at: list[Segment] = at) -> None:
                    at[0] = s

                for data in store.read_from(seg.seq, chunk=self.CHUNK, wait=0.5,
                                            stop=lambda: v.gone or ch.closed,
                                            on_segment=moved):
                    newest = store.newest()
                    if newest is not None and newest.t1 - at[0].t0 > self.LAG_MAX:
                        v.why = "too far behind live"
                        return
                    v.sent += len(data)
                    yield data
                    if v.gone or ch.closed:
                        return
                seg = self._next_run(ch, v, at[0])
        finally:
            self.leave(v)

    def _next_run(self, ch: Channel, v: Viewer, last: Segment) -> Segment | None:
        """Where a viewer goes on once what it read ended: the next run
        after a reconnect, or - read faster than it could follow, or let go
        behind it - the same run further on."""
        store = ch.session.store
        deadline = time.monotonic() + self.RUN_WAIT
        while not (v.gone or ch.closed) and time.monotonic() < deadline:
            nxt = store.next_run_after(last.t1, last.seq)
            if nxt is not None:
                return nxt
            ahead = store.first_from(last.seq + 1)
            if ahead is not None and ahead.run == last.run:
                return ahead
            if ch.session.state in ("failed", "stopped"):
                v.why = "the provider stopped sending"
                return None
            self._stop.wait(0.2)
        return None

    # -- the app's own use wins ------------------------------------------------------

    def make_room(self, source: str, own: int, keep: str | None = None) -> int:
        """The app is about to hold *own* connections to *source* (the new
        one counted): let go of as many of the server's channels on it as
        that needs - those nobody watches first, then the fewest viewers,
        then the newest. *keep*: a stream id not to let go. How many went."""
        with self._lock:
            self._epoch += 1
            mine = self._own(source)
            over = own + len(mine) - max(0, self._limit(source))
            if over <= 0:
                return 0
            keep_key = channel_key(source, keep) if keep is not None else None
            order = sorted((ch for ch in mine if ch.key != keep_key),
                           key=lambda c: (len(c.viewers), -c.opened))
            gone = 0
            for ch in order[:over]:
                self._drop(ch, "the app's own viewing needs the connection")
                gone += 1
            return gone

    # -- housekeeping ----------------------------------------------------------------

    def _drop(self, ch: Channel, why: str) -> None:
        """Let go of *ch* (under the lock): its viewers end, its connection
        closes unless the app reads it too."""
        if ch.closed:
            return
        ch.closed = True
        for v in ch.viewers:
            v.gone = True
            v.why = v.why or why
        ch.viewers.clear()
        if self._channels.get(ch.key) is ch:
            del self._channels[ch.key]
        try:
            ch.pool.let_go(ch.session, SERVER)
        except Exception as e:
            log.debug("restream: letting go failed: %s", e)
        log.info("restream: %s let go (%s)", ch.name or "a channel", why)

    def _tick(self) -> None:
        while not self._stop.wait(self.TICK):
            try:
                self.housekeep()
            except Exception as e:
                log.debug("restream: %s", e)

    def housekeep(self) -> None:
        now = self._clock()
        with self._lock:
            for ch in list(self._channels.values()):
                state = ch.session.state
                if state in ("failed", "stopped", "frozen") and not ch.closed:
                    self._drop(ch, "its connection ended")
                elif (not ch.viewers and ch.idle_since is not None
                      and now - ch.idle_since >= self.grace):
                    self._drop(ch, "nobody watches it")

    def close(self) -> None:
        """Every viewer ends and every channel is let go."""
        self._stop.set()
        with self._lock:
            self._closed = True
            for ch in list(self._channels.values()):
                self._drop(ch, "the server stopped")
            for pool in self._pools:
                pool.shared = False
            self._pools.clear()

    # -- what is said ----------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        now = self._clock()
        with self._lock:
            chans = []
            for ch in self._channels.values():
                s = ch.session
                shared = self._app_uses(ch)
                # The channel the person watches privately is not named:
                # the page, In use now and the Nerd line read this.
                hidden = shared and _private(ch.stream)
                chans.append({
                    "source": ch.source, "stream": "" if hidden else ch.stream,
                    "name": "" if hidden else ch.name, "private": hidden,
                    "state": s.state, "connections": s.connections + getattr(s.ingest, "connections", 0),
                    "shared": shared, "since": round(now - ch.opened),
                    "viewers": [{"client": v.client, "since": round(now - v.since),
                                 "mb": round(v.sent / 1e6, 1)} for v in ch.viewers]})
            return {"in_use": len(self._own()), "tuners": self._total(), "channels": chans}
