"""One app answers commands from outside: a second start with a command
(--play, a dopeiptv:// link) hands it to the copy already running over a
local socket and quits, instead of opening a second window. A plain second
start still opens one, as it always has (two copies are allowed: MPRIS
gives the second a name of its own)."""

from __future__ import annotations

import getpass
import json
import os
import secrets
import stat
import sys
import zlib
from typing import Callable

from PyQt6.QtCore import QObject

from .log import log

try:
    from PyQt6.QtNetwork import QLocalServer, QLocalSocket
    UNAVAILABLE = ""
except (ImportError, OSError) as e:
    # Qt's network library links Kerberos' GSSAPI library, which a runtime
    # may not carry: the Flatpak crashed at every start from 1.5.0 on (#36).
    # Without it the app still starts - a second start opens a window of
    # its own instead of handing its command over.
    UNAVAILABLE = str(e) or e.__class__.__name__


#: The longest path a Unix socket can have, with room to spare (108 bytes).
SOCKET_PATH_MAX = 100
#: Windows: the file in the user's local app data holding the word in the
#: pipe's name (``_pipe_key``).
PIPE_KEY = "instance.key"


def server_name() -> str:
    """Per user: another account's copy never takes this one's commands.
    In a Flatpak each start has a /tmp of its own, where Qt would put the
    socket; the app's runtime folder is the one place two starts share.
    Elsewhere on Linux the user's own runtime folder ($XDG_RUNTIME_DIR,
    which only they can enter) - without one a folder of their own in
    their cache (0700, ``_make_folder``) - never the /tmp everyone shares,
    where another account could take the name first and hear this one's
    commands. macOS keeps Qt's per-user temporary folder. Windows' named
    pipes are the whole machine's: the name carries a random word only
    this user can read (``_pipe_key``), so no other account can make it
    first; the pipe itself is the user's alone (UserAccessOption)."""
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
    if sys.platform.startswith("linux"):
        if runtime and os.path.isabs(runtime) and os.path.isdir(runtime):
            return os.path.join(runtime, "dopeiptv", name)
        cache = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
        own = os.path.join(cache, "dopeiptv", "instance", name)
        if os.path.isabs(own) and len(own.encode("utf-8")) <= SOCKET_PATH_MAX:
            return own
    if sys.platform == "win32":
        key = _pipe_key()
        if key:
            return f"{name}-{key}"
    return name


def _pipe_key() -> str:
    """The random word in this user's pipe name, made once and kept in their
    local app data, which no other account can read; "" where there is none
    (the name as before)."""
    base = os.environ.get("LOCALAPPDATA", "")
    if not base or not os.path.isabs(base):
        return ""
    path = os.path.join(base, "dopeiptv", PIPE_KEY)
    for _attempt in range(2):
        try:
            with open(path, encoding="ascii") as fh:
                key = fh.read().strip()
            if len(key) == 32 and all(c in "0123456789abcdef" for c in key):
                return key
            return ""
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            return ""
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue                # another start made it first: read that one
        except OSError as e:
            log.info("commands: no key for the pipe's name (%s)", e)
            return ""
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(secrets.token_hex(16))
    return ""


def _make_folder(name: str) -> bool:
    """The folder a path name lives in, only the user's (0700): made so,
    narrowed when it is wider; False when another account owns it - the
    socket is then not made there."""
    folder = os.path.dirname(name)
    if not folder:
        return True
    try:
        os.makedirs(folder, mode=0o700, exist_ok=True)
        if os.name == "posix":
            st = os.stat(folder)
            if st.st_uid != os.getuid():
                log.warning("commands: %s is another account's - not listening there", folder)
                return False
            if stat.S_IMODE(st.st_mode) & 0o077:
                os.chmod(folder, 0o700)
    except OSError as e:
        log.info("commands: no folder for the socket (%s)", e)
    return True


def send_to_running(command: dict, timeout_ms: int = 800) -> bool:
    """Hand *command* to the copy running; False when there is none (or
    Qt's network library could not load, UNAVAILABLE)."""
    if UNAVAILABLE:
        return False
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
        self._server: QLocalServer | None = None
        if UNAVAILABLE:
            log.warning("commands: Qt's network library could not load (%s) - "
                        "a second start opens a window of its own", UNAVAILABLE)
            return
        server = self._server = QLocalServer(self)
        # The socket (Unix) or pipe (Windows) is the user's alone.
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        server.newConnection.connect(self._accept)
        name = server_name()
        if not _make_folder(name):
            return
        if not server.listen(name):
            # A copy that crashed leaves its socket file behind (Unix).
            QLocalServer.removeServer(name)
            if not server.listen(name):
                log.info("commands: not listening (%s)", server.errorString())

    @property
    def listening(self) -> bool:
        return self._server is not None and self._server.isListening()

    def _accept(self) -> None:
        server = self._server
        if server is None:
            return
        while server.hasPendingConnections():
            pending = server.nextPendingConnection()
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
        if self._server is not None:
            self._server.close()
