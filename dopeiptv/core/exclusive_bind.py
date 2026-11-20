"""How the app's servers bind: a port another program holds stays its own
on Windows too, and no name lookup holds a server up.

http.server turns SO_REUSEADDR on. On Linux and macOS that only lets a
server bind again right after a restart; on Windows it lets a second
socket take a port another program is listening on. SO_EXCLUSIVEADDRUSE
makes a taken port fail there as it does elsewhere.

http.server also asks socket.getfqdn() for the address it bound to, only
to keep the answer as ``server_name`` - which nothing here reads (CGI did).
For 0.0.0.0 that is a lookup of the computer's own name and then its
address backwards, and on Windows it can take seconds: every cast, every
server start waited for it. The name kept is the address itself.

And how long a client may hold a connection without asking anything
(``IdleTimeoutMixin``, the LAN servers a TV reads from), and how many
connections there may be at once (``ConnectionLimitMixin``): each takes a
thread and a file handle of the app's, and an address on the network that
opened them by the thousand took the player's with them.
"""

from __future__ import annotations

import socket
import socketserver
import sys
import threading
from http.server import HTTPServer
from typing import Any


class ExclusiveBindMixin:
    """Mixed in before a socketserver class: its port is never shared."""

    socket: Any
    server_address: Any
    server_name: str
    server_port: int

    def server_bind(self) -> None:
        if sys.platform == "win32":
            self.allow_reuse_address = False
            excl = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
            if excl is not None:
                self.socket.setsockopt(socket.SOL_SOCKET, excl, 1)
        if isinstance(self, HTTPServer):
            # HTTPServer.server_bind without its getfqdn().
            socketserver.TCPServer.server_bind(self)
            host, port = self.server_address[:2]
            self.server_name, self.server_port = str(host), int(port)
            return
        super().server_bind()  # type: ignore[misc]


#: Connections one of the app's servers serves at once, and how many of them
#: one address may have. Far above what a home's devices open (a phone's
#: browser six, a TV a handful, a media server one a stream), and what the
#: app can lose to a server without the player noticing.
MAX_CONNECTIONS = 256
MAX_PER_CLIENT = 64


class ConnectionLimitMixin:
    """Mixed in before a threading socketserver class: at most
    ``max_connections`` connections are served at once, ``max_per_client``
    of them from one address. One more is closed unanswered - its sender
    has its share already - and counted (``turned_away``)."""

    max_connections: int = MAX_CONNECTIONS
    max_per_client: int = MAX_PER_CLIENT

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._conn_lock = threading.Lock()
        self._conn_by: dict[str, int] = {}
        self._conn_total = 0
        self.turned_away = 0
        super().__init__(*args, **kwargs)

    @staticmethod
    def _conn_who(client_address: Any) -> str:
        try:
            return str(client_address[0])
        except (TypeError, IndexError, KeyError):
            return ""

    def verify_request(self, request: Any, client_address: Any) -> bool:
        who = self._conn_who(client_address)
        with self._conn_lock:
            if (self._conn_total >= self.max_connections
                    or self._conn_by.get(who, 0) >= self.max_per_client):
                self.turned_away += 1
                return False
            self._conn_total += 1
            self._conn_by[who] = self._conn_by.get(who, 0) + 1
        return True

    def _conn_done(self, client_address: Any) -> None:
        who = self._conn_who(client_address)
        with self._conn_lock:
            self._conn_total = max(0, self._conn_total - 1)
            left = self._conn_by.get(who, 0) - 1
            if left > 0:
                self._conn_by[who] = left
            else:
                self._conn_by.pop(who, None)

    def connections(self, who: str | None = None) -> int:
        """Connections being served now (from *who*, else in all)."""
        with self._conn_lock:
            return self._conn_total if who is None else self._conn_by.get(who, 0)

    def process_request(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request(request, client_address)  # type: ignore[misc]
        except BaseException:
            self._conn_done(client_address)     # no thread took it
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)  # type: ignore[misc]
        finally:
            self._conn_done(client_address)


#: Seconds a connection may wait for its request - before the first one, or
#: between two on a kept-alive connection - before it is closed.
IDLE_S = 30.0


class IdleTimeoutMixin:
    """Mixed in before BaseHTTPRequestHandler: a client that opens a
    connection and asks nothing on it (or stops halfway through its
    request) is let go after ``idle_timeout`` instead of holding a server
    thread for as long as it likes. The answer has no limit: a TV reads a
    stream at the pace it plays it, and a paused one reads nothing for
    minutes - a timeout there would end the cast."""

    idle_timeout: float = IDLE_S
    connection: Any

    def _idle_limit(self, secs: float | None) -> None:
        try:
            self.connection.settimeout(secs)
        except OSError:
            pass

    def handle_one_request(self) -> None:
        self._idle_limit(self.idle_timeout)        # waiting for the request
        super().handle_one_request()  # type: ignore[misc]

    def parse_request(self) -> bool:
        ok = bool(super().parse_request())  # type: ignore[misc]
        self._idle_limit(None)                     # the answer takes its time
        return ok
