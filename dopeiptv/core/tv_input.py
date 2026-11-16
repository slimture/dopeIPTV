"""TV mode's input: a remote, a game controller or LIRC, as actions.

TV mode (ui/mw_tv.py) is run by actions - "up", "ok", "back", "ch_up",
"red", "digit_5" - whatever sent them:

- keys, which covers a remote that acts as a keyboard (most USB and
  Bluetooth ones, an IR receiver the kernel decodes) and an HDMI-CEC
  remote the system turns into keys (libcec's cec-client, or a kernel
  CEC adapter with its keymap): the window maps those itself;
- a game controller, through SDL2's game controller API when pysdl2 can
  be imported (it knows the layout of hundreds of pads), else straight
  from Linux's evdev when the pad's /dev/input/event* can be read (the
  logged-in user can, through logind's uaccess);
- LIRC, through lircd's socket (/run/lirc/lircd), its key names
  (KEY_UP, KEY_OK, KEY_BACK, ...) mapped as the kernel's.

The same actions come from the phone remote, the remote API and Home
Assistant (``switch_mode``, ``remote_action`` read what they ask).

Each source is optional and says nothing when it is not there. A reader
runs on a thread of its own and hands each action to a callback, from
that thread: the window takes it over to its own (a queued signal).
Nothing here needs Qt.
"""

from __future__ import annotations

import os
import select
import socket
import stat
import struct
import threading
import time
from collections.abc import Callable
from typing import Any

from .log import log

#: Every action TV mode knows, besides the digits ("picture": what plays
#: behind the page, full screen again - the phone remote's Now playing).
ACTIONS: tuple[str, ...] = (
    "up", "down", "left", "right", "ok", "back", "menu", "info", "guide",
    "home", "search", "play_pause", "play", "pause", "stop", "ch_up",
    "ch_down", "vol_up", "vol_down", "mute", "red", "green", "yellow",
    "blue", "fav", "subtitles", "audio", "fast_forward", "rewind", "last",
    "picture")
DIGITS: tuple[str, ...] = tuple(f"digit_{n}" for n in range(10))
ALL: frozenset[str] = frozenset(ACTIONS + DIGITS)
#: Actions a held button repeats; the rest act once per press.
REPEATABLE: frozenset[str] = frozenset({
    "up", "down", "left", "right", "vol_up", "vol_down", "ch_up",
    "ch_down", "fast_forward", "rewind"})

Emit = Callable[[str], None]


def digit(action: str) -> int | None:
    """The number of a digit action ("digit_7" -> 7), else None."""
    if action.startswith("digit_") and action[6:].isdecimal():
        return int(action[6:])
    return None


# -- asked from elsewhere: the phone remote, the remote API, Home Assistant -------

_SWITCH = {"on": "on", "true": "on", "1": "on", "off": "off", "false": "off", "0": "off",
           "toggle": "toggle"}


def switch_mode(value: Any) -> str | None:
    """"on", "off" or "toggle" of a request to turn TV mode on or off
    (Home Assistant's ON/OFF, true/false, 1/0), else None."""
    if isinstance(value, bool):
        return "on" if value else "off"
    if not isinstance(value, str):
        return None
    return _SWITCH.get(value.strip().lower())


def remote_action(value: Any) -> str | None:
    """A key asked from elsewhere as one of TV mode's actions ("up",
    "digit_5"), else None."""
    if not isinstance(value, str):
        return None
    action = value.strip().lower()
    return action if action in ALL else None


#: What a text entry of TV mode's (its search) takes from elsewhere at most -
#: the search's own cap.
TEXT_MAX = 60
#: Kept of the invisible characters: the joiners emoji are made of.
_JOINERS = frozenset("‌‍")


def entry_text(value: Any) -> str:
    """Text typed elsewhere (the phone's own keyboard) for TV mode's text
    entry: plain text - line breaks and tabs as spaces, control and other
    invisible characters left out - at most TEXT_MAX characters. ValueError
    for anything that is not text, or longer."""
    import unicodedata
    if not isinstance(value, str):
        raise ValueError('"text" is the text to type')
    if len(value) > TEXT_MAX * 4:
        raise ValueError(f'"text" is at most {TEXT_MAX} characters')
    out = []
    for c in value:
        if c in "\t\r\n":
            out.append(" ")
        elif unicodedata.category(c).startswith("C") and c not in _JOINERS:
            continue
        else:
            out.append(c)
    text = "".join(out)
    if len(text) > TEXT_MAX:
        raise ValueError(f'"text" is at most {TEXT_MAX} characters')
    return text


# -- LIRC ---------------------------------------------------------------------

#: lircd's key names (the kernel's, as lircd.conf files use them).
LIRC_KEYS: dict[str, str] = {
    "KEY_UP": "up", "KEY_DOWN": "down", "KEY_LEFT": "left", "KEY_RIGHT": "right",
    "KEY_OK": "ok", "KEY_ENTER": "ok", "KEY_SELECT": "ok", "KEY_KPENTER": "ok",
    "KEY_BACK": "back", "KEY_EXIT": "back", "KEY_ESC": "back",
    "KEY_BACKSPACE": "back",
    "KEY_MENU": "menu", "KEY_CONTEXT_MENU": "menu", "KEY_OPTION": "menu",
    "KEY_SETUP": "menu", "KEY_INFO": "info", "KEY_EPG": "guide",
    "KEY_PROGRAM": "guide", "KEY_GUIDE": "guide", "KEY_HOME": "home",
    "KEY_HOMEPAGE": "home", "KEY_TV": "home", "KEY_SEARCH": "search",
    "KEY_FIND": "search",
    "KEY_PLAYPAUSE": "play_pause", "KEY_PLAY": "play", "KEY_PAUSE": "pause",
    "KEY_STOP": "stop", "KEY_STOPCD": "stop",
    "KEY_CHANNELUP": "ch_up", "KEY_CHANNELDOWN": "ch_down",
    "KEY_PAGEUP": "ch_up", "KEY_PAGEDOWN": "ch_down", "KEY_NEXT": "ch_up",
    "KEY_PREVIOUS": "ch_down",
    "KEY_VOLUMEUP": "vol_up", "KEY_VOLUMEDOWN": "vol_down", "KEY_MUTE": "mute",
    "KEY_RED": "red", "KEY_GREEN": "green", "KEY_YELLOW": "yellow",
    "KEY_BLUE": "blue", "KEY_FAVORITES": "fav", "KEY_BOOKMARKS": "fav",
    "KEY_SUBTITLE": "subtitles", "KEY_AUDIO": "audio", "KEY_LANGUAGE": "audio",
    "KEY_FASTFORWARD": "fast_forward", "KEY_FORWARD": "fast_forward",
    "KEY_REWIND": "rewind", "KEY_LAST": "last",
}
for _n in range(10):
    LIRC_KEYS[f"KEY_{_n}"] = f"digit_{_n}"
    LIRC_KEYS[f"KEY_NUMERIC_{_n}"] = f"digit_{_n}"

#: Where lircd's socket is, newest first.
LIRC_SOCKETS = ("/run/lirc/lircd", "/var/run/lirc/lircd", "/dev/lircd")
# A held button's first repeats are dropped: a press that lasted a little
# long would otherwise move two rows.
LIRC_REPEAT_FROM = 3


def lirc_action(name: str) -> str | None:
    """The action of lircd's key *name* ("KEY_OK", or an old file's "OK")."""
    key = (name or "").strip().upper()
    if not key:
        return None
    return LIRC_KEYS.get(key) or LIRC_KEYS.get("KEY_" + key)


def lirc_line(line: str) -> tuple[str, int] | None:
    """(action, repeat count) of one line lircd sends for a key -
    ``<code> <repeat, hex> <key name> <remote>`` - or None."""
    parts = (line or "").split()
    if len(parts) < 3:
        return None
    try:
        repeat = int(parts[1], 16)
    except ValueError:
        return None
    action = lirc_action(parts[2])
    return (action, repeat) if action else None


def lirc_event(line: str) -> str | None:
    """The action one line of lircd's stands for, or None: a repeat only
    for an action that repeats, and only once the button is held."""
    got = lirc_line(line)
    if got is None:
        return None
    action, repeat = got
    if repeat == 0:
        return action
    if repeat >= LIRC_REPEAT_FROM and action in REPEATABLE:
        return action
    return None


def lirc_socket(env: dict[str, str] | None = None) -> str | None:
    """lircd's socket where there is one ($LIRC_SOCKET_PATH first)."""
    first = (os.environ if env is None else env).get("LIRC_SOCKET_PATH", "")
    for path in (first, *LIRC_SOCKETS):
        if not path:
            continue
        try:
            if stat.S_ISSOCK(os.stat(path).st_mode):
                return path
        except OSError:
            continue
    return None


# -- game controllers -------------------------------------------------------------

class PadState:
    """Where a pad's hat and sticks point, so a push counts once: past
    PUSH a direction goes, back under RELEASE it may go again."""

    PUSH = 0.6
    RELEASE = 0.3

    def __init__(self) -> None:
        self._at: dict[str, int] = {}

    def axis(self, name: str, value: float, neg: str, pos: str) -> list[str]:
        """*value* (-1..1) of the axis *name*: [*neg*] or [*pos*] when it
        just went that way, else []."""
        was = self._at.get(name, 0)
        if value <= -self.PUSH and was != -1:
            self._at[name] = -1
            return [neg] if neg else []
        if value >= self.PUSH and was != 1:
            self._at[name] = 1
            return [pos] if pos else []
        if abs(value) < self.RELEASE:
            self._at[name] = 0
        return []


# Linux's input events (linux/input-event-codes.h).
EV_KEY, EV_ABS = 0x01, 0x03
ABS_X, ABS_Y, ABS_HAT0X, ABS_HAT0Y = 0x00, 0x01, 0x10, 0x11
#: A pad's buttons in the kernel's gamepad layout. BTN_NORTH (0x133) is
#: the top face button (Y on an Xbox pad), BTN_WEST (0x134) the left (X).
EVDEV_BUTTONS: dict[int, str] = {
    0x130: "ok",            # BTN_SOUTH (A)
    0x131: "back",          # BTN_EAST (B)
    0x133: "menu",          # BTN_NORTH (Y)
    0x134: "play_pause",    # BTN_WEST (X)
    0x136: "ch_down",       # BTN_TL
    0x137: "ch_up",         # BTN_TR
    0x138: "rewind",        # BTN_TL2
    0x139: "fast_forward",  # BTN_TR2
    0x13a: "guide",         # BTN_SELECT
    0x13b: "menu",          # BTN_START
    0x13c: "home",          # BTN_MODE
    0x220: "up", 0x221: "down", 0x222: "left", 0x223: "right",   # BTN_DPAD_*
}
_EVENT = struct.Struct("llHHi")      # struct input_event, this machine's


def evdev_event(etype: int, code: int, value: int, pad: PadState,
                ranges: dict[int, tuple[int, int]] | None = None) -> list[str]:
    """The actions one input event of a pad stands for."""
    if etype == EV_KEY:
        action = EVDEV_BUTTONS.get(code)
        if action is None or value == 0:
            return []
        if value == 2 and action not in REPEATABLE:
            return []
        return [action]
    if etype != EV_ABS:
        return []
    if code == ABS_HAT0X:
        return pad.axis("hx", float(value), "left", "right")
    if code == ABS_HAT0Y:
        return pad.axis("hy", float(value), "up", "down")
    if code in (ABS_X, ABS_Y):
        lo, hi = (ranges or {}).get(code, (-32768, 32767))
        if hi <= lo:
            return []
        norm = 2.0 * (value - lo) / (hi - lo) - 1.0
        if code == ABS_X:
            return pad.axis("sx", norm, "left", "right")
        return pad.axis("sy", norm, "up", "down")
    return []


def parse_input_devices(text: str, dev_dir: str = "/dev/input") -> list[tuple[str, str]]:
    """The game controllers in /proc/bus/input/devices: (name, its
    event* path in *dev_dir*) for each device with a joystick handler."""
    out: list[tuple[str, str]] = []
    for block in (text or "").split("\n\n"):
        name, handlers = "", []
        for line in block.splitlines():
            if line.startswith("N: Name="):
                name = line[len("N: Name="):].strip().strip('"')
            elif line.startswith("H: Handlers="):
                handlers = line[len("H: Handlers="):].split()
        if any(h.startswith("js") for h in handlers):
            ev = next((h for h in handlers if h.startswith("event")), None)
            if ev:
                out.append((name or ev, os.path.join(dev_dir, ev)))
    return out


def find_gamepads(proc: str = "/proc/bus/input/devices",
                  dev_dir: str = "/dev/input") -> list[tuple[str, str, bool]]:
    """(name, path, readable) for each game controller Linux has."""
    try:
        with open(proc, encoding="utf-8", errors="replace") as fh:
            found = parse_input_devices(fh.read(), dev_dir)
    except OSError:
        return []
    return [(name, path, os.access(path, os.R_OK)) for name, path in found]


# SDL's game controller buttons and axes (SDL_GameControllerButton).
SDL_BUTTONS: dict[int, str] = {
    0: "ok", 1: "back", 2: "play_pause", 3: "menu",   # A, B, X, Y
    4: "guide", 5: "home", 6: "menu",                 # Back, Guide, Start
    9: "ch_down", 10: "ch_up",                        # shoulders
    11: "up", 12: "down", 13: "left", 14: "right",    # D-pad
}
SDL_AXIS_LEFTX, SDL_AXIS_LEFTY, SDL_TRIGGER_LEFT, SDL_TRIGGER_RIGHT = 0, 1, 4, 5


def sdl_button(button: int) -> str | None:
    return SDL_BUTTONS.get(int(button))


def sdl_axis(axis: int, value: int, pad: PadState) -> list[str]:
    """The actions an SDL axis' move stands for (-32768..32767)."""
    norm = max(-1.0, min(1.0, value / 32767.0))
    if axis == SDL_AXIS_LEFTX:
        return pad.axis("sx", norm, "left", "right")
    if axis == SDL_AXIS_LEFTY:
        return pad.axis("sy", norm, "up", "down")
    if axis == SDL_TRIGGER_LEFT:
        return pad.axis("lt", norm, "", "rewind")
    if axis == SDL_TRIGGER_RIGHT:
        return pad.axis("rt", norm, "", "fast_forward")
    return []


_sdl: dict[str, Any] = {}


def sdl_module() -> Any:
    """pysdl2 when it can be imported (with its SDL2 library), else None.
    Tried once."""
    if "mod" not in _sdl:
        try:
            import sdl2  # type: ignore[import-not-found]
            _sdl["mod"] = sdl2
        except Exception as e:      # not installed, or no libSDL2 for it
            log.debug("tv input: no pysdl2 (%s)", e)
            _sdl["mod"] = None
    return _sdl["mod"]


# -- the readers ------------------------------------------------------------------

class _Reader(threading.Thread):
    def __init__(self, emit: Emit, name: str) -> None:
        super().__init__(name=name, daemon=True)
        self._emit = emit
        self._halt = threading.Event()

    def halt(self) -> None:
        self._halt.set()

    def _send(self, actions: list[str]) -> None:
        for a in actions:
            if self._halt.is_set():
                # Halted while still reading (up to half a second): TV mode
                # left and on again has a new reader, and lircd and evdev
                # hand every client the same key - it would act twice.
                return
            try:
                self._emit(a)
            except Exception as e:
                log.debug("tv input: %s not taken: %s", a, e)


class LircReader(_Reader):
    """lircd's socket, read line by line; connected again when lircd comes
    back (it restarts, or starts after the app)."""

    RETRY_S = 5.0

    def __init__(self, emit: Emit, path: str) -> None:
        super().__init__(emit, "tv-lirc")
        self.path = path

    def run(self) -> None:
        said = False
        while not self._halt.is_set():
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(0.5)
                sock.connect(self.path)
            except OSError as e:
                if not said:
                    log.debug("tv input: lircd at %s not reachable: %s", self.path, e)
                    said = True
                self._halt.wait(self.RETRY_S)
                continue
            log.info("tv input: reading LIRC at %s", self.path)
            said = False
            try:
                self._read(sock)
            finally:
                sock.close()
            self._halt.wait(self.RETRY_S)

    def _read(self, sock: socket.socket) -> None:
        buf, block = b"", False
        while not self._halt.is_set():
            try:
                data = sock.recv(4096)
            except TimeoutError:
                continue
            except OSError:
                return
            if not data:
                return
            buf += data
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("utf-8", "replace").strip()
                # A reply to a command (BEGIN ... END), never a key.
                if line == "BEGIN":
                    block = True
                elif line == "END":
                    block = False
                elif not block:
                    action = lirc_event(line)
                    if action:
                        self._send([action])


def _abs_range(fd: int, code: int) -> tuple[int, int] | None:
    """An axis' (minimum, maximum), asked of the kernel (EVIOCGABS)."""
    try:
        import fcntl
        size = struct.calcsize("6i")
        req = (2 << 30) | (size << 16) | (ord("E") << 8) | (0x40 + code)
        info = struct.unpack("6i", fcntl.ioctl(fd, req, bytes(size)))
        return info[1], info[2]
    except (OSError, ImportError, struct.error):
        return None


class EvdevReader(_Reader):
    """Linux's game controllers, read from /dev/input/event* - those this
    user may read. Looked for again now and then (a pad switched on)."""

    RESCAN_S = 5.0

    def __init__(self, emit: Emit, proc: str = "/proc/bus/input/devices",
                 dev_dir: str = "/dev/input") -> None:
        super().__init__(emit, "tv-evdev")
        self.proc = proc
        self.dev_dir = dev_dir
        self._fds: dict[int, tuple[str, PadState, dict[int, tuple[int, int]]]] = {}

    def _open_new(self) -> None:
        have = {path for path, _p, _r in self._fds.values()}
        for name, path, readable in find_gamepads(self.proc, self.dev_dir):
            if not readable or path in have:
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError as e:
                log.debug("tv input: %s not opened: %s", path, e)
                continue
            ranges = {c: r for c in (ABS_X, ABS_Y) if (r := _abs_range(fd, c))}
            self._fds[fd] = (path, PadState(), ranges)
            log.info("tv input: game controller %s (%s)", name, path)

    def run(self) -> None:
        next_scan = 0.0
        try:
            while not self._halt.is_set():
                if time.monotonic() >= next_scan:
                    self._open_new()
                    next_scan = time.monotonic() + self.RESCAN_S
                if not self._fds:
                    self._halt.wait(1.0)
                    continue
                try:
                    ready, _w, _x = select.select(list(self._fds), [], [], 0.5)
                except OSError:
                    ready = []
                for fd in ready:
                    self._read(fd)
        finally:
            for fd in list(self._fds):
                self._close(fd)

    def _close(self, fd: int) -> None:
        self._fds.pop(fd, None)
        try:
            os.close(fd)
        except OSError:
            pass

    def _read(self, fd: int) -> None:
        try:
            data = os.read(fd, _EVENT.size * 64)
        except BlockingIOError:
            return
        except OSError:             # unplugged
            self._close(fd)
            return
        _path, pad, ranges = self._fds.get(fd, ("", PadState(), {}))
        for off in range(0, len(data) - _EVENT.size + 1, _EVENT.size):
            _s, _us, etype, code, value = _EVENT.unpack_from(data, off)
            self._send(evdev_event(etype, code, value, pad, ranges))


class SdlReader(_Reader):
    """Game controllers through SDL2 (pysdl2), polled on this thread."""

    def __init__(self, emit: Emit, sdl: Any) -> None:
        super().__init__(emit, "tv-sdl")
        self.sdl = sdl

    def run(self) -> None:
        import ctypes
        sdl = self.sdl
        try:
            sdl.SDL_SetHint(b"SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", b"1")
            if sdl.SDL_Init(sdl.SDL_INIT_GAMECONTROLLER) != 0:
                log.info("tv input: SDL did not start: %s", sdl.SDL_GetError())
                return
        except Exception as e:
            log.info("tv input: SDL did not start: %s", e)
            return
        log.info("tv input: game controllers through SDL2")
        pads: dict[int, tuple[Any, PadState]] = {}
        ev = sdl.SDL_Event()
        try:
            while not self._halt.is_set():
                if not sdl.SDL_WaitEventTimeout(ctypes.byref(ev), 250):
                    continue
                t = ev.type
                if t == sdl.SDL_CONTROLLERDEVICEADDED:
                    c = sdl.SDL_GameControllerOpen(ev.cdevice.which)
                    if c:
                        jid = sdl.SDL_JoystickInstanceID(
                            sdl.SDL_GameControllerGetJoystick(c))
                        pads[int(jid)] = (c, PadState())
                        name = sdl.SDL_GameControllerName(c) or b"?"
                        log.info("tv input: game controller %s",
                                 name.decode("utf-8", "replace"))
                elif t == sdl.SDL_CONTROLLERDEVICEREMOVED:
                    got = pads.pop(int(ev.cdevice.which), None)
                    if got is not None:
                        sdl.SDL_GameControllerClose(got[0])
                elif t == sdl.SDL_CONTROLLERBUTTONDOWN:
                    action = sdl_button(ev.cbutton.button)
                    if action:
                        self._send([action])
                elif t == sdl.SDL_CONTROLLERAXISMOTION:
                    pad = pads.get(int(ev.caxis.which), (None, PadState()))[1]
                    self._send(sdl_axis(ev.caxis.axis, ev.caxis.value, pad))
        finally:
            for c, _pad in pads.values():
                try:
                    sdl.SDL_GameControllerClose(c)
                except Exception:
                    pass
            try:
                sdl.SDL_QuitSubSystem(sdl.SDL_INIT_GAMECONTROLLER)
            except Exception:
                pass


# -- all of them ----------------------------------------------------------------------

def detect(proc: str = "/proc/bus/input/devices") -> dict[str, Any]:
    """What TV mode can read here: {"lirc": socket path or None, "sdl":
    pysdl2 importable, "pads": [(name, path, readable)]}."""
    return {"lirc": lirc_socket(), "sdl": sdl_module() is not None,
            "pads": find_gamepads(proc)}


class InputHub:
    """The readers TV mode runs while it is on: LIRC when lircd is there,
    game controllers through SDL2 when pysdl2 is there, else evdev."""

    def __init__(self, emit: Emit, proc: str = "/proc/bus/input/devices") -> None:
        self._emit = emit
        self._proc = proc
        self._readers: list[_Reader] = []

    @property
    def running(self) -> list[str]:
        return [r.name for r in self._readers]

    def start(self) -> list[str]:
        """Start what is there; the names of the readers running."""
        if self._readers:
            return self.running
        path = lirc_socket()
        if path:
            self._readers.append(LircReader(self._emit, path))
        sdl = sdl_module()
        if sdl is not None:
            self._readers.append(SdlReader(self._emit, sdl))
        elif os.path.exists(self._proc):
            self._readers.append(EvdevReader(self._emit, self._proc))
        for r in self._readers:
            r.start()
        return self.running

    def stop(self) -> None:
        for r in self._readers:
            r.halt()
        self._readers = []
