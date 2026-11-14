"""One app answers commands from outside: a second start with a command
(--play, a dopeiptv:// link) hands it to the copy already running over a
local socket and quits, instead of opening a second window. A plain second
start still opens one, as it always has (two copies are allowed: MPRIS
gives the second a name of its own)."""

from __future__ import annotations

import getpass
import json
import os
import sys
import zlib
from typing import Callable

from PyQt6.QtCore import QObject
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from .log import log


def server_name() -> str:
    """Per user: another account's copy never takes this one's commands.
    In a Flatpak each start has a /tmp of its own, where Qt would put the
    socket; the app's runtime folder is the one place two starts share.
    Elsewhere on Linux the user's own runtime folder ($XDG_RUNTIME_DIR,
    which only they can enter), not the /tmp everyone shares, where another
    account could take the name first and hear this one's commands. macOS
    keeps Qt's per-user temporary folder, Windows a named pipe."""
    forced = os.environ.get("DOPEIPTV_INSTANCE_SOCKET", "").strip()
    if forced:
        return forced
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    name = f"dopeiptv-{zlib.crc32(user.encode('utf-8')):08x}"
    app_id, runtime = os.environ.get("FLATPAK_ID"), os.environ.get("XDG_RUNTIME_DIR")
    if app_id and runtime:
        shared = os.path.join(runtime, "app", app_id)
        if os.path.isdir(shared):
            return os.path.join(shared, name)
    if sys.platform.startswith("linux") and runtime and os.path.isabs(runtime) \
            and os.path.isdir(runtime):
        return os.path.join(runtime, "dopeiptv", name)
    return name


def _make_folder(name: str) -> None:
    """The folder a path name lives in, only the user's (0700)."""
    folder = os.path.dirname(name)
    if not folder or os.path.isdir(folder):
        return
    try:
        os.makedirs(folder, mode=0o700, exist_ok=True)
    except OSError as e:
        log.info("commands: no folder for the socket (%s)", e)


def send_to_running(command: dict, timeout_ms: int = 800) -> bool:
    """Hand *command* to the copy running; False when there is none."""
    sock = QLocalSocket()
    sock.connectToServer(server_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(json.dumps(command).encode("utf-8") + b"\n")
    # Sent once nothing is left to write. Not waitForBytesWritten()'s
    # answer alone: with everything already out it says False, and a
    # command the running copy got would also open a second window.
    while sock.bytesToWrite() > 0:
        if not sock.waitForBytesWritten(timeout_ms):
            break
    ok = sock.bytesToWrite() == 0
    sock.disconnectFromServer()
    if sock.state() != QLocalSocket.LocalSocketState.UnconnectedState:
        sock.waitForDisconnected(timeout_ms)
    return ok


class CommandServer(QObject):
    """Listens for commands; calls *on_command* with each (on the window's
    thread - Qt's socket signals arrive there)."""

    def __init__(self, on_command: Callable[[dict], None], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._on = on_command
        self._server = QLocalServer(self)
        # The socket (Unix) or pipe (Windows) is the user's alone.
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._accept)
        name = server_name()
        _make_folder(name)
        if not self._server.listen(name):
            # A copy that crashed leaves its socket file behind (Unix).
            QLocalServer.removeServer(name)
            if not self._server.listen(name):
                log.info("commands: not listening (%s)", self._server.errorString())

    @property
    def listening(self) -> bool:
        return self._server.isListening()

    def _accept(self) -> None:
        while self._server.hasPendingConnections():
            pending = self._server.nextPendingConnection()
            if pending is None:
                break
            conn: QLocalSocket = pending
            buf = bytearray()

            def read(sock: QLocalSocket = conn, buf: bytearray = buf) -> None:
                buf.extend(sock.readAll().data())
                while b"\n" in buf:
                    line, _, rest = bytes(buf).partition(b"\n")
                    buf[:] = rest
                    try:
                        cmd = json.loads(line.decode("utf-8"))
                    except ValueError:
                        continue
                    if isinstance(cmd, dict):
                        self._on(cmd)
            conn.readyRead.connect(read)
            conn.disconnected.connect(conn.deleteLater)
            read()

    def close(self) -> None:
        self._server.close()
