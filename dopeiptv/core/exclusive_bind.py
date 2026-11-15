"""A port another program holds stays its own on Windows too.

http.server turns SO_REUSEADDR on. On Linux and macOS that only lets a
server bind again right after a restart; on Windows it lets a second
socket take a port another program is listening on. SO_EXCLUSIVEADDRUSE
makes a taken port fail there as it does elsewhere.
"""

from __future__ import annotations

import socket
import sys
from typing import Any


class ExclusiveBindMixin:
    """Mixed in before a socketserver class: its port is never shared."""

    socket: Any

    def server_bind(self) -> None:
        if sys.platform == "win32":
            self.allow_reuse_address = False
            excl = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
            if excl is not None:
                self.socket.setsockopt(socket.SOL_SOCKET, excl, 1)
        super().server_bind()  # type: ignore[misc]
