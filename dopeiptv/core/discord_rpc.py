"""Discord status ("Rich Presence") over Discord's local IPC, in plain Python.

The Discord app on this computer listens on a Unix socket
(``$XDG_RUNTIME_DIR/discord-ipc-0`` and its kin) or, on Windows, a named pipe
(``\\\\.\\pipe\\discord-ipc-0``). A frame is an opcode and a length, both
32-bit little-endian, then that many bytes of JSON. The app says who it is
with a HANDSHAKE (op 0) naming its Discord application, and sets what its
user is doing with a SET_ACTIVITY command in a FRAME (op 1); an activity of
null clears it. Discord clears it by itself when the connection closes.

Presence keeps all of that on a thread of its own: the window only hands it
the activity wanted (never blocking, never raising), and the thread connects
when there is something to show, says it, and - when Discord is not running,
or quits - tries again quietly a little later.
"""
from __future__ import annotations

import json
import os
import socket
import struct
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from .log import log

#: dopeIPTV's application on discord.com/developers. Public, not a secret:
#: it names the app whose status this is. While empty the switch is hidden.
DISCORD_APP_ID = ""
#: The name of the picture uploaded under the application's Rich Presence
#: art assets; Discord shows none when there is no asset by that name.
LARGE_IMAGE = "dopeiptv"

OP_HANDSHAKE, OP_FRAME, OP_CLOSE, OP_PING, OP_PONG = 0, 1, 2, 3, 4
#: A frame bigger than this is not one Discord sent.
MAX_FRAME = 64 * 1024
#: Discord's activity types: 3 is "Watching".
TYPE_WATCHING = 3
#: Discord takes a details or state line of 2 to 128 characters.
_MAX_TEXT = 128
#: Between two tries at reaching Discord.
RETRY_S = 20.0
#: How often a connection that shows something is checked: Discord quitting
#: and starting again is noticed, and the status said again.
CHECK_S = 30.0
IO_TIMEOUT_S = 5.0

_FLATPAK_DIRS = ("app/com.discordapp.Discord", "app/com.discordapp.DiscordCanary",
                 "app/dev.vencord.Vesktop", "snap.discord", "snap.discord-canary")


def encode(op: int, payload: Mapping[str, Any] | None) -> bytes:
    data = json.dumps(payload if payload is not None else {},
                      separators=(",", ":")).encode("utf-8")
    return struct.pack("<II", op, len(data)) + data


def decode_header(header: bytes) -> tuple[int, int]:
    if len(header) != 8:
        raise ValueError("a frame header is 8 bytes")
    op, size = struct.unpack("<II", header)
    if size > MAX_FRAME:
        raise ValueError("a frame too big to be Discord's")
    return op, size


def handshake(client_id: str) -> dict[str, Any]:
    return {"v": 1, "client_id": str(client_id)}


def set_activity(activity: Mapping[str, Any] | None, pid: int,
                 nonce: str | None = None) -> dict[str, Any]:
    return {"cmd": "SET_ACTIVITY",
            "args": {"pid": int(pid), "activity": dict(activity) if activity else None},
            "nonce": nonce or str(uuid.uuid4())}


def socket_paths(env: Mapping[str, str] | None = None,
                 platform: str | None = None) -> list[str]:
    """Where Discord may be listening, in the order to try: the plain app,
    then the Flatpak's and the Snap's own folders, for discord-ipc-0..9."""
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    if platform.startswith("win"):
        return [rf"\\.\pipe\discord-ipc-{i}" for i in range(10)]
    bases: list[str] = []
    for var in ("XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP"):
        v = env.get(var)
        if v and v not in bases:
            bases.append(v)
    if "/tmp" not in bases:
        bases.append("/tmp")
    dirs: list[str] = []
    for base in bases:
        dirs.append(base)
        dirs += [os.path.join(base, sub) for sub in _FLATPAK_DIRS]
    return [os.path.join(d, f"discord-ipc-{i}") for i in range(10) for d in dirs]


def _text(value: Any) -> str:
    s = " ".join(str(value or "").split())
    if len(s) > _MAX_TEXT:
        s = s[:_MAX_TEXT - 1].rstrip() + "…"
    return s if len(s) >= 2 else ""


def activity_for(what: Mapping[str, Any] | None, names: bool, paused: bool = False,
                 texts: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    """The activity Discord shows for *what* plays: {"kind", "title",
    "channel", "programme", "series", "season", "episode", "started"}.
    *names*: whether to say what it is at all, or only "Watching TV".
    *texts*: the words, as format strings ("watching", "tv", "paused",
    "episode"); English when not given."""
    if not what:
        return None
    t = {"watching": "Watching {title}", "tv": "Watching TV",
         "paused": "Paused", "episode": "S{season} E{episode}"}
    t.update(texts or {})
    act: dict[str, Any] = {"type": TYPE_WATCHING,
                           "assets": {"large_image": LARGE_IMAGE,
                                      "large_text": "dopeIPTV"}}
    kind = str(what.get("kind") or "")

    def watching(name: Any) -> str:
        name = " ".join(str(name or "").split())
        return t["watching"].format(title=name) if name else t["tv"]

    details = state = ""
    if not names:
        details = t["tv"]
    elif kind == "live":
        details = watching(what.get("channel") or what.get("title"))
        state = str(what.get("programme") or "")
    elif kind == "episode":
        series = str(what.get("series") or "")
        details = watching(series or what.get("title"))
        try:
            ep = t["episode"].format(season=int(str(what.get("season"))),
                                     episode=int(str(what.get("episode"))))
        except (TypeError, ValueError):
            ep = ""
        title = str(what.get("title") or "")
        state = " · ".join(x for x in (ep, title if series and title != series else "") if x)
    else:
        details = watching(what.get("title"))
    if paused:
        state = t["paused"]
    elif what.get("started"):
        act["timestamps"] = {"start": int(float(what["started"]))}
    act["details"] = _text(details) or _text(t["tv"])
    if _text(state):
        act["state"] = _text(state)
    return act


# -- the connection ----------------------------------------------------------------

class Pipe(Protocol):
    def send(self, data: bytes) -> None: ...

    def recv(self, n: int) -> bytes: ...

    def close(self) -> None: ...


class _UnixPipe:
    def __init__(self, path: str) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(IO_TIMEOUT_S)
        try:
            self.sock.connect(path)
        except OSError:
            self.sock.close()
            raise

    def send(self, data: bytes) -> None:
        self.sock.sendall(data)

    def recv(self, n: int) -> bytes:
        return self.sock.recv(n)

    def alive(self) -> bool:
        """Whether Discord is still at the other end - without waiting, and
        without taking what it may have sent (a later read skips that)."""
        try:
            return bool(self.sock.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT))
        except BlockingIOError:
            return True
        except OSError:
            return False

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class _WinPipe:
    def __init__(self, path: str) -> None:
        self.fh = open(path, "r+b", buffering=0)     # kept open: the pipe

    def send(self, data: bytes) -> None:
        self.fh.write(data)

    def recv(self, n: int) -> bytes:
        return self.fh.read(n) or b""

    def close(self) -> None:
        try:
            self.fh.close()
        except OSError:
            pass


def open_pipe(paths: list[str] | None = None) -> Pipe:
    """The first of *paths* Discord answers on; OSError when none."""
    win = sys.platform.startswith("win")
    last: OSError | None = None
    for path in paths if paths is not None else socket_paths():
        if not win and not os.path.exists(path):
            continue
        try:
            return _WinPipe(path) if win else _UnixPipe(path)
        except OSError as e:
            last = e
    raise last or FileNotFoundError("Discord is not running")


def read_frame(pipe: Pipe) -> tuple[int, dict[str, Any]]:
    def exactly(n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = pipe.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("Discord closed the connection")
            buf += chunk
        return buf

    op, size = decode_header(exactly(8))
    body = exactly(size) if size else b"{}"
    try:
        data = json.loads(body.decode("utf-8"))
    except ValueError as e:
        raise ConnectionError("not Discord's JSON") from e
    return op, data if isinstance(data, dict) else {}


class Presence:
    """What this app's user is watching, as Discord shows it."""

    def __init__(self, client_id: str,
                 connect: Callable[[], Pipe] | None = None,
                 retry_s: float = RETRY_S, pid: int | None = None) -> None:
        self.client_id = client_id
        self._connect = connect or open_pipe
        self.retry_s = retry_s
        self.pid = os.getpid() if pid is None else pid
        self._cond = threading.Condition()
        self._want: dict[str, Any] | None = None
        self._changed = False
        self._stopped = False
        self._pipe: Pipe | None = None
        self._said: dict[str, Any] | None = None
        self._ready = False
        self._quiet = False             # "Discord is not running" said once
        self._thread: threading.Thread | None = None

    # -- from the window's thread: never blocks -----------------------------------

    def show(self, activity: Mapping[str, Any] | None) -> None:
        with self._cond:
            want = dict(activity) if activity else None
            if want == self._want and not self._changed:
                return
            self._want = want
            self._changed = True
            if self._thread is None and not self._stopped and want is not None:
                self._thread = threading.Thread(target=self._run, daemon=True,
                                                name="discord-presence")
                self._thread.start()
            self._cond.notify_all()

    def stop(self) -> None:
        with self._cond:
            self._stopped = True
            self._cond.notify_all()

    @property
    def connected(self) -> bool:
        return self._ready

    # -- the thread -----------------------------------------------------------------

    def _run(self) -> None:
        next_try = 0.0
        next_check = time.monotonic() + CHECK_S
        while True:
            check = False
            with self._cond:
                while not self._stopped:
                    now = time.monotonic()
                    pending = self._changed or self._want != self._said
                    wait: float | None = None
                    if pending:
                        if now >= next_try:
                            break
                        wait = next_try - now
                    elif self._pipe is not None and self._said is not None:
                        if now >= next_check:
                            check = True
                            break
                        wait = next_check - now
                    self._cond.wait(None if wait is None else max(0.05, wait))
                if self._stopped:
                    break
                want, self._changed = self._want, False
            if check:
                next_check = time.monotonic() + CHECK_S
                if not self._still_there():
                    log.info("discord: the connection went away")
                    self._close()       # said nothing now: said again below
                continue
            if self._pipe is None and want is None and self._said is None:
                continue                # nothing shown, nothing to clear
            try:
                self._ensure()
                self._say(want)
                next_try = 0.0
                next_check = time.monotonic() + CHECK_S
            except (OSError, ValueError, ConnectionError) as e:
                self._close()
                if not self._quiet:
                    log.info("discord: not reachable (%s) - trying again "
                             "now and then", type(e).__name__)
                    self._quiet = True
                with self._cond:
                    self._changed = True
                next_try = time.monotonic() + self.retry_s
        self._close()

    def _still_there(self) -> bool:
        alive = getattr(self._pipe, "alive", None)
        try:
            return bool(alive()) if callable(alive) else True
        except Exception:
            return False

    def _ensure(self) -> None:
        if self._pipe is not None:
            return
        pipe = self._connect()
        try:
            pipe.send(encode(OP_HANDSHAKE, handshake(self.client_id)))
            op, data = read_frame(pipe)
            if op == OP_CLOSE or data.get("evt") != "READY":
                raise ConnectionError(f"Discord refused the handshake "
                                      f"({data.get('code')} {data.get('message', '')})")
        except BaseException:
            pipe.close()
            raise
        self._pipe, self._ready, self._said = pipe, True, None
        if self._quiet:
            log.info("discord: connected")
        self._quiet = False

    def _say(self, activity: dict[str, Any] | None) -> None:
        pipe = self._pipe
        assert pipe is not None
        if activity == self._said:
            return
        pipe.send(encode(OP_FRAME, set_activity(activity, self.pid)))
        while True:
            op, data = read_frame(pipe)
            if op == OP_PING:
                pipe.send(encode(OP_PONG, data))
                continue
            if op == OP_CLOSE:
                raise ConnectionError("Discord closed the connection")
            if data.get("cmd") == "SET_ACTIVITY" or data.get("evt") == "ERROR":
                break
        if data.get("evt") == "ERROR":
            # Refused (a text too long, an application that is gone): said
            # once, and not again until what is shown changes.
            log.info("discord: the status was refused (%s)",
                     (data.get("data") or {}).get("message", "?"))
        self._said = activity

    def _close(self) -> None:
        pipe, self._pipe = self._pipe, None
        self._ready = False
        self._said = None
        if pipe is not None:
            pipe.close()
