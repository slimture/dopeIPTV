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
"""
from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable
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


class Ingest:
    CHUNK = 64 * 1024
    MAX_FAILURES = 5            # in a row, without a byte in between
    TIMEOUT = (10.0, 20.0)      # connect, and silence while reading
    BACKOFF = 0.5               # doubled per failure, at most 8 s
    # A connection shorter than this that ended is retried after BACKOFF,
    # not at once, so a provider that hangs up straight away is not hammered.
    SHORT_RUN = 2.0
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
        self.connections = 0
        self.merged = 0             # reconnects that went on in the same run
        self._tail = b""            # the current run's last bytes
        self._drops: list[float] = []
        self._thread = threading.Thread(target=self._run, name="dvr-ingest",
                                        daemon=True)

    @staticmethod
    def _default_open(url: str, **kw: Any) -> Any:
        # A login in the address is answered as the box asks (Tvheadend
        # wants digest, which requests alone never sends); the Nerd page's
        # user agent, headers and proxy apply as they do for the player.
        from . import nerd
        from .http_login import get_with_login
        n = nerd.current()
        if n.on:
            src = nerd.source()
            headers = dict(kw.pop("headers", None) or {})
            headers["User-Agent"] = n.user_agent(src)
            headers.update(n.headers(src))
            kw["headers"] = headers
            px = n.proxy(src, url)
            if px:
                kw["proxies"] = {"http": px, "https": px}
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

    def _run(self) -> None:
        failures = 0
        resume = False      # the run before ended; this one may carry it on
        while not self._stop.is_set():
            got = 0
            began = time.monotonic()
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
                self.state = "streaming"
                if resume:
                    head = bytearray()
                block = bytearray()
                flushed = time.monotonic()
                for chunk in resp.iter_content(self.CHUNK):
                    if self._stop.is_set():
                        break
                    if not chunk:
                        continue
                    got += len(chunk)
                    failures = 0
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
            if head is not None:
                # Ended while its start was still being looked through.
                self._end_run()
                resume = False
                self._append(bytes(head))
            if got:
                # Gone on from where it was, or begun anew: either way the
                # next connection may carry this run on.
                resume = True
                self._drops = [*self._drops[-9:], time.monotonic()]
            else:
                failures += 1
                if resume:
                    # Nothing came: the run is over for whoever reads it.
                    self._end_run()
                    resume = False
            if failures >= self.MAX_FAILURES:
                self.state = "failed"
                log.info("DVR ingest: gave up after %d tries", failures)
                return
            self.state = "reconnecting"
            if got and time.monotonic() - began >= self.SHORT_RUN:
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


def wait_until(predicate: Callable[[], bool], timeout: float,
               step: float = 0.05) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(step)
    return predicate()
