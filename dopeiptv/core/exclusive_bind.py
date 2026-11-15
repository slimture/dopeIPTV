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
"""

from __future__ import annotations

import socket
import socketserver
import sys
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
