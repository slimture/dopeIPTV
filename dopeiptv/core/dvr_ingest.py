"""The one provider connection of an application-kept rewind buffer.

It reads the live channel into the store whether anything plays it or not -
which is the point: a pause no longer stops the download, so LIVE never has
to reopen the provider stream. One connection, as mpv's own was; a dropped
one is opened again at once, a few times, and then given up, for the app's
own reconnect to take over.

A reconnect goes on in the same run when it can: providers start a new
connection with the last seconds they hold, so the bytes the dropped one
ended on come again, byte for byte. Found there, what came before them is
skipped and the run carries on - the player never sees the drop. Not found
(a gap, or a provider that sends no such start), what follows is a run of
its own, which playback does not cross.

Bytes are moved in large blocks - socket reads and file writes, which leave
the interpreter lock free while they wait - so the window does not feel it.

Only a stream is kept. A connection that brings a few kilobytes and ends (a
web page, an error message) is a failed try, not a run: such an address was
opened again twice a second for ever. And an answer that is plainly no
stream - a web page, JSON, a playlist, a line of text - is given up at
once (``NotAStream``), with no try after it; the window then plays the
address as it did before the buffer (core/dvr_pool.py remembers it). Once
the address has streamed, such an answer on a reconnect is a failed try
instead: a provider's message while it still counts the dropped connection.
"""
from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any

from .dvr_store import SegmentStore
from .log import log

if TYPE_CHECKING:
    import requests

USER_AGENT = "dopeIPTV/1.0"     # what mpv sends; providers may check it


def _hard_close(resp: "requests.Response") -> None:
    """Close the connection at once: a plain close from another thread can
    leave a read blocked on the socket - and the account's only connection
    held - until data or the timeout arrives. shutdown() sends the FIN and
    wakes it."""
    sock = None
    try:
        conn = getattr(resp.raw, "_connection", None)
        sock = getattr(conn, "sock", None)
        if sock is None:
            fp = getattr(resp.raw, "_fp", None)
            sock = getattr(getattr(getattr(fp, "fp", None), "raw", None),
                           "_sock", None)
    except Exception:
        sock = None
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    try:
        resp.close()
    except Exception:
        pass


class NotAStream(Exception):
    """The provider answered with something that is plainly no stream: what
    it is ("a web page") is the message."""


TS_PACKET = 188


def ts_synced(head: bytes) -> bool:
    """Whether *head* holds MPEG-TS packets: 0x47 every 188 bytes, from any
    of its first 188 (a relay need not start on a packet)."""
    n = len(head)
    for i in range(min(TS_PACKET, n - TS_PACKET)):
        if (head[i] == 0x47 and head[i + TS_PACKET] == 0x47
                and (i + 2 * TS_PACKET >= n or head[i + 2 * TS_PACKET] == 0x47)):
            return True
    return False


def _texty(head: bytes) -> bool:
    """Printable text all through, as a page or a message is - never a
    picture or sound, whose bytes are mostly not."""
    if b"\x00" in head:
        return False
    text = head.decode("utf-8", "replace").lstrip("﻿")
    plain = sum(1 for c in text if c.isprintable() or c in "\r\n\t")
    return plain >= 0.97 * len(text)


def not_a_stream(head: bytes, content_type: str = "") -> str | None:
    """What *head*, the first bytes of a connection, is when it is plainly
    no stream - "a web page", "JSON", "a playlist", "text" - else None. A
    transport stream never is, nor anything binary: an MP3 radio or an FLV
    channel plays through the buffer as well."""
    if not head or ts_synced(head) or not _texty(head):
        return None
    start = head.lstrip(b"\xef\xbb\xbf \t\r\n")[:8]
    kind = (content_type or "").split(";", 1)[0].strip().lower()
    if start.upper().startswith(b"#EXTM3U") or "mpegurl" in kind:
        return "a playlist"
    if start.startswith(b"<") or "html" in kind:
        return "a web page"
    if start[:1] in (b"{", b"[") or "json" in kind:
        return "JSON"
    return "text"


class Ingest:
    CHUNK = 64 * 1024
    MAX_FAILURES = 5            # in a row, without a run that streamed between
    TIMEOUT = (10.0, 20.0)      # connect, and silence while reading
    BACKOFF = 0.5               # doubled per failure, at most 8 s
    # A connection shorter than this that ended is retried after BACKOFF,
    # not at once, so a provider that hangs up straight away is not hammered.
    SHORT_RUN = 2.0
    # A connection streamed when it brought this much, or went on for
    # SHORT_RUN once the provider answered; less is a failed try. A web
    # page or an error message is a few kilobytes and over: counted as
    # streamed, the failures never added up and it was asked again twice
    # a second.
    GOOD_BYTES = 256 * 1024
    # A connection's first bytes, held back until they show they are no
    # page or message (not_a_stream).
    SNIFF = 1024
    # The dropped connection's last bytes, looked for in the next one's start.
    NEEDLE = 10 * 188
    # How long the next connection's start is held back while they are looked
    # for: the repeat comes at the provider's full speed, so it is quick.
    MERGE_BYTES = 24 * 1024 * 1024
    MERGE_SECONDS = 3.0
    # Into the store in blocks: a provider may send the stream in pieces of
    # a few packets, and each append takes the store's lock, writes, and
    # wakes the readers - hundreds of times a second, it fell behind.
    BLOCK = 128 * 1024
    BLOCK_SECONDS = 0.1
    # This many connections that streamed and then dropped within the window
    # is a tug of war: the account's one connection is wanted elsewhere too
    # (another device or app), and the provider takes it from one to give it
    # to the other, over and over.
    CONTEST_DROPS = 3
    CONTEST_WINDOW = 60.0

    def __init__(self, url: str, store: SegmentStore,
                 opener: Callable[..., Any] | None = None
                 ) -> None:
        self.url = url
        self.store = store
        self._open = opener or self._default_open
        self._stop = threading.Event()
        self._resp: Any = None
        self._lock = threading.Lock()
        self.state = "connecting"   # streaming / reconnecting / failed / stopped
        # What the provider sent instead of a stream, when that was why it
        # failed ("a web page"): nothing is tried after it.
        self.refused: str | None = None
        self.connections = 0
        self.merged = 0             # reconnects that went on in the same run
        self._tail = b""            # the current run's last bytes
        self._drops: list[float] = []
        self._thread = threading.Thread(target=self._run, name="dvr-ingest",
                                        daemon=True)

    @staticmethod
    def _default_open(url: str, **kw: Any) -> Any:
        from . import nerd
        return open_for(nerd.source(), url, **kw)

    @staticmethod
    def _open_source(src: dict | None, url: str, **kw: Any) -> Any:
        # A login in the address is answered as the box asks (Tvheadend
        # wants digest, which requests alone never sends); the Nerd page's
        # user agent, headers and proxy apply as they do for the player -
        # the proxy only while "streams through the proxy too" is on.
        # The source's own route (own DNS, IP version, a pinned
        # certificate) as the app's other requests have it.
        from . import nerd, net_route
        from .http_login import get_with_login
        n = nerd.current()
        if n.on:
            headers = dict(kw.pop("headers", None) or {})
            headers["User-Agent"] = n.user_agent(src)
            headers.update(n.headers(src))
            kw["headers"] = headers
            px = n.proxy(src, url) if n.flag("nerd_proxy_player", True) else ""
            if px:
                kw["proxies"] = {"http": px, "https": px}
            route = net_route.route_for(src, n)
            if route.active:
                from .net_adapter import session_for
                return get_with_login(url, session_for(route).get, **kw)
        return get_with_login(url, **kw)

    def start(self) -> None:
        self._thread.start()

    def _append(self, data: bytes) -> None:
        self.store.append(data)
        self._tail = (self._tail + data)[-self.NEEDLE:]

    def _overlap_end(self, head: bytearray, start: int) -> int | None:
        """Where the dropped connection's bytes end in *head* (searched from
        *start* on), or None while they are not there."""
        needle = self._tail
        if len(needle) < 188:
            return None
        at = head.find(needle, max(0, start - len(needle)))
        return None if at < 0 else at + len(needle)

    def _end_run(self) -> None:
        self.store.new_run()
        self._tail = b""

    @staticmethod
    def _content_type(resp: Any) -> str:
        try:
            return str((getattr(resp, "headers", None) or {}).get("Content-Type") or "")
        except Exception:
            return ""

    def _checked(self, resp: Any) -> Iterator[bytes]:
        """*resp*'s bytes, its first SNIFF held back until they show they
        are no page or message - else NotAStream."""
        chunks = resp.iter_content(self.CHUNK)
        held = bytearray()
        for chunk in chunks:
            if self._stop.is_set():
                return
            held += chunk
            if len(held) >= self.SNIFF:
                break
        what = not_a_stream(bytes(held[:4 * self.SNIFF]), self._content_type(resp))
        if what:
            raise NotAStream(what)
        if held:
            yield bytes(held)
        yield from chunks

    def _run(self) -> None:
        failures = 0
        resume = False      # the run before ended; this one may carry it on
        while not self._stop.is_set():
            got = 0
            began = time.monotonic()
            # When the provider answered: how long it streamed is counted
            # from there, not from a slow connect (bytes come in CHUNKs, so
            # the first may only arrive when the connection ends).
            answered: float | None = None
            head: bytearray | None = None
            block = bytearray()
            try:
                self.connections += 1
                resp = self._open(url=self.url, stream=True,
                                  timeout=self.TIMEOUT,
                                  headers={"User-Agent": USER_AGENT})
                with self._lock:
                    if self._stop.is_set():
                        resp.close()
                        break
                    self._resp = resp
                resp.raise_for_status()
                answered = time.monotonic()
                self.state = "streaming"
                if resume:
                    head = bytearray()
                block = bytearray()
                flushed = time.monotonic()
                for chunk in self._checked(resp):
                    if self._stop.is_set():
                        break
                    if not chunk:
                        continue
                    got += len(chunk)
                    if head is None:
                        block += chunk
                        now = time.monotonic()
                        if (len(block) >= self.BLOCK
                                or now - flushed >= self.BLOCK_SECONDS):
                            self._append(bytes(block))
                            block.clear()
                            flushed = now
                        continue
                    start = len(head)
                    head += chunk
                    end = self._overlap_end(head, start)
                    if end is not None:
                        self.merged += 1
                        log.info("DVR ingest: reconnected in %.1f s - %.1f MB "
                                 "sent again skipped, the same run",
                                 time.monotonic() - began, end / 1e6)
                        rest = bytes(head[end:])
                        head = None
                        resume = False
                        self._append(rest)
                    elif (len(head) >= self.MERGE_BYTES
                          or time.monotonic() - began >= self.MERGE_SECONDS):
                        log.info("DVR ingest: reconnected in %.1f s - not "
                                 "where it left off, a new run",
                                 time.monotonic() - began)
                        self._end_run()
                        resume = False
                        rest = bytes(head)
                        head = None
                        self._append(rest)
                if block:
                    self._append(bytes(block))
                    block.clear()
                if not self._stop.is_set():
                    log.info("DVR ingest: the provider ended the stream "
                             "after %.0f s and %.1f MB",
                             time.monotonic() - began, got / 1e6)
            except NotAStream as e:
                if self._stop.is_set():
                    pass
                elif self._drops:
                    # The address streamed a moment ago: a message now (a
                    # provider still counting the dropped connection against
                    # the account's limit) is a failed try, as before - the
                    # failures add up, the page is not taken for good.
                    log.info("DVR ingest: the provider sent %s (%s) after a "
                             "stream - a failed try", e,
                             self._content_type(resp) or "no type")
                else:
                    self.refused = str(e)
                    log.info("DVR ingest: the provider sent %s (%s), not a "
                             "stream - given up, not asked again", e,
                             self._content_type(resp) or "no type")
            except Exception as e:
                if not self._stop.is_set():
                    detail = str(e).strip().replace("\n", " ")[:160]
                    log.info("DVR ingest: %s after %.0f s and %.1f MB%s",
                             type(e).__name__, time.monotonic() - began,
                             got / 1e6, f" ({detail})" if detail else "")
            finally:
                with self._lock:
                    if self._resp is not None:
                        try:
                            self._resp.close()
                        except Exception:
                            pass
                        self._resp = None
            if block and not self._stop.is_set():
                self._append(bytes(block))      # what came before the drop
            if self._stop.is_set():
                break
            if self.refused:
                if resume:
                    self._end_run()
                self.state = "failed"
                return
            if head is not None:
                # Ended while its start was still being looked through.
                self._end_run()
                resume = False
                self._append(bytes(head))
            streamed = got >= self.GOOD_BYTES or (
                got > 0 and answered is not None
                and time.monotonic() - answered >= self.SHORT_RUN)
            if streamed:
                # Gone on from where it was, or begun anew: either way the
                # next connection may carry this run on.
                failures = 0
                resume = True
                self._drops = [*self._drops[-9:], time.monotonic()]
            else:
                failures += 1
                if got:
                    log.info("DVR ingest: %.0f KB and over - no stream, try "
                             "%d of %d", got / 1024, failures, self.MAX_FAILURES)
                if resume or got:
                    # The run is over for whoever reads it; what little
                    # came is one of its own, carried on by nothing.
                    self._end_run()
                    resume = False
            if failures >= self.MAX_FAILURES:
                self.state = "failed"
                log.info("DVR ingest: gave up after %d tries", failures)
                return
            self.state = "reconnecting"
            if streamed and time.monotonic() - began >= self.SHORT_RUN:
                continue            # a long run dropped: straight back
            self._stop.wait(min(8.0, self.BACKOFF * (2 ** failures)))
        self.state = "stopped"

    def stop(self, wait: float = 0.0) -> None:
        """Close the provider connection now (another channel may need the
        account's only one), and let the thread end."""
        self._stop.set()
        with self._lock:
            resp = self._resp
        if resp is not None:
            _hard_close(resp)
        if wait and self._thread.is_alive():
            self._thread.join(wait)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    @property
    def contested(self) -> bool:
        """The connection keeps being taken away (see CONTEST_DROPS)."""
        now = time.monotonic()
        return sum(1 for t in self._drops
                   if now - t <= self.CONTEST_WINDOW) >= self.CONTEST_DROPS


def open_for(src: dict | None, url: str, **kw: Any) -> Any:
    """The provider stream *url* of source *src* (its options and kind, as
    core/nerd.set_source keeps them), opened as the player opens it."""
    return Ingest._open_source(src, url, **kw)


def opener_for(src: dict | None) -> Callable[..., Any]:
    """An Ingest opener for source *src* - a channel of a source that is
    not the one on screen (server mode) keeps its own options."""
    def opener(url: str, **kw: Any) -> Any:
        return open_for(src, url, **kw)
    return opener


def wait_until(predicate: Callable[[], bool], timeout: float,
               step: float = 0.05) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(step)
    return predicate()
