"""stats_inbox.py - the statistics' lines, straight from nginx into memory.

nginx hands each line of the statistics (a page view, the page's /hit, a
click on a download) to this service over a socket of its own instead of
writing a log (the content server's nginx file: access_log
syslog:server=unix:...). They wait here, in memory, until the next run of
stats_collect.py takes them - a minute at most - and are gone with it: the
visitor's address is never written anywhere, not for an hour, not in a log
that turns over, not in a backup.

    Receiver(path, inbox)   the socket (a datagram socket, 0660, the group
                            nginx's workers run as - donate.open_to_nginx)
                            and a thread that reads it
    Inbox                   the lines waiting, bounded: past MAX_BYTES a new
                            line is dropped and counted, never kept

A line arrives as syslog frames it - "<190>Nov 20 12:00:00 iptv_stats:
{...}" - and is kept under its tag (iptv_stats, iptv_dl); anything else
the socket gets is passed over.

Standard library only.
"""
from __future__ import annotations

import contextlib
import os
import re
import socket
import threading
from collections import deque

#: What may wait in memory between two runs: far more than a minute of any
#: real day; a flood past it is dropped (and said, as a number).
MAX_BYTES = 32 << 20
#: A datagram's largest: nginx's lines are cut to fit 2 KiB (the conf).
DATAGRAM = 65536
TAGS = ("iptv_stats", "iptv_dl")
FRAME = re.compile(rb"^<\d{1,3}>[A-Z][a-z]{2} [ \d]\d \d\d:\d\d:\d\d (?:\S+ )?(" +
                   b"|".join(t.encode() for t in TAGS) + rb"): (.*?)\n?$", re.S)


class Inbox:
    """Lines waiting for the next run, oldest first."""

    def __init__(self, most: int = MAX_BYTES) -> None:
        self.most = most
        self._lines: deque = deque()
        self._size = 0
        self._lock = threading.Lock()
        self.dropped = 0
        self.passed = 0

    def put(self, tag: str, line: str) -> bool:
        n = len(line)
        with self._lock:
            if self._size + n > self.most:
                self.dropped += 1
                return False
            self._lines.append((tag, line))
            self._size += n
            return True

    def take(self) -> tuple:
        """Everything waiting, and how many were dropped since the last take:
        ([(tag, line)...], dropped). The inbox is empty after it."""
        with self._lock:
            lines, self._lines = list(self._lines), deque()
            self._size = 0
            dropped, self.dropped = self.dropped, 0
        return lines, dropped

    def __len__(self) -> int:
        with self._lock:
            return len(self._lines)


def frame(data: bytes) -> tuple | None:
    """(tag, line) from one syslog datagram, None for anything else."""
    m = FRAME.match(data)
    if not m:
        return None
    return m.group(1).decode(), m.group(2).decode("utf-8", "replace")


class Receiver:
    """The socket nginx sends the statistics' lines to, and its reader."""

    def __init__(self, path: str, inbox: Inbox) -> None:
        self.path = path
        self.inbox = inbox
        with contextlib.suppress(OSError):
            os.unlink(path)                 # one left by a stopped run
        old = os.umask(0o117)               # 0660 from the start, never wider
        try:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            self.sock.bind(path)
        finally:
            os.umask(old)
        # Room for a burst while the thread is busy (FreeBSD gives 16 KiB):
        # nginx never waits on a full socket, it drops the line.
        with contextlib.suppress(OSError):
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._read, name="stats-inbox", daemon=True)
        self._thread.start()

    def _read(self) -> None:
        while True:
            try:
                data = self.sock.recv(DATAGRAM)
            except OSError:
                return                      # closed
            got = frame(data)
            if got is None:
                self.inbox.passed += 1
                continue
            self.inbox.put(*got)

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.sock.close()
        with contextlib.suppress(OSError):
            os.unlink(self.path)

