"""TV mode's input and focus rules (core/tv_input.py, core/tv_nav.py):
LIRC's lines, a pad's evdev events and SDL's buttons and axes become the
same actions - each through its reader, with fakes for lircd, the kernel
and SDL - and an arrow always finds the element it means."""
from __future__ import annotations

import ctypes
import os
import socket
import struct
import tempfile
import threading
import time

from dopeiptv.core import tv_input as ti
from dopeiptv.core import tv_nav


def _until(cond, secs: float = 3.0) -> bool:
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


# -- LIRC ---------------------------------------------------------------------------

def test_lirc_names_map_like_the_kernel():
    assert ti.lirc_action("KEY_OK") == "ok"
    assert ti.lirc_action("key_up") == "up"
    assert ti.lirc_action("OK") == "ok"                 # an old lircd.conf's name
    assert ti.lirc_action("KEY_BACK") == ti.lirc_action("KEY_EXIT") == "back"
    assert ti.lirc_action("KEY_CHANNELUP") == "ch_up"
    assert ti.lirc_action("KEY_RED") == "red"
    assert ti.lirc_action("KEY_BLUE") == "blue"
    assert ti.lirc_action("KEY_5") == ti.lirc_action("KEY_NUMERIC_5") == "digit_5"
    assert ti.lirc_action("KEY_EPG") == "guide"
    assert ti.lirc_action("KEY_POWER") is None
    assert ti.lirc_action("") is None
    assert set(ti.LIRC_KEYS.values()) <= ti.ALL


def test_lirc_lines_and_held_buttons():
    assert ti.lirc_line("0000000000f40bf0 00 KEY_UP devinput") == ("up", 0)
    assert ti.lirc_line("0000000000f40bf0 1a KEY_UP devinput") == ("up", 26)
    assert ti.lirc_line("garbage") is None
    assert ti.lirc_line("00 zz KEY_UP remote") is None
    assert ti.lirc_event("0 00 KEY_OK r") == "ok"
    # A held OK is still one press; a held arrow repeats, once held.
    assert ti.lirc_event("0 05 KEY_OK r") is None
    assert ti.lirc_event("0 01 KEY_DOWN r") is None
    assert ti.lirc_event(f"0 {ti.LIRC_REPEAT_FROM:02x} KEY_DOWN r") == "down"
    assert ti.lirc_event("0 00 KEY_UNKNOWN r") is None


def test_lirc_socket_found_by_env(tmp_path):
    path = str(tmp_path / "lircd")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    try:
        assert ti.lirc_socket({"LIRC_SOCKET_PATH": path}) == path
        assert ti.lirc_socket({"LIRC_SOCKET_PATH": str(tmp_path / "nope")}) in (None, *ti.LIRC_SOCKETS)
    finally:
        srv.close()


def test_lirc_reader_reads_a_fake_lircd():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "lircd")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)
    got: list[str] = []

    def serve() -> None:
        conn, _ = srv.accept()
        conn.sendall(b"BEGIN\nSIGHUP\nEND\n"                 # a reply, never a key
                     b"0000000000f40bf0 00 KEY_DOWN devinput\n"
                     b"0000000000f40bf0 01 KEY_DOWN devinput\n"
                     b"0000000000f40bf0 00 KEY_OK devinput\n0000000000f4")
        time.sleep(0.1)
        conn.sendall(b"0bf0 00 KEY_RED devinput\n")         # a line in two pieces
        time.sleep(0.5)
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    reader = ti.LircReader(got.append, path)
    reader.start()
    try:
        assert _until(lambda: got == ["down", "ok", "red"]), got
    finally:
        reader.halt()
        srv.close()


# -- game controllers ------------------------------------------------------------------

def test_evdev_buttons_hat_and_stick():
    pad = ti.PadState()
    assert ti.evdev_event(ti.EV_KEY, 0x130, 1, pad) == ["ok"]          # A
    assert ti.evdev_event(ti.EV_KEY, 0x130, 0, pad) == []              # released
    assert ti.evdev_event(ti.EV_KEY, 0x131, 1, pad) == ["back"]        # B
    assert ti.evdev_event(ti.EV_KEY, 0x137, 1, pad) == ["ch_up"]       # right shoulder
    assert ti.evdev_event(ti.EV_KEY, 0x220, 2, pad) == ["up"]          # D-pad held repeats
    assert ti.evdev_event(ti.EV_KEY, 0x130, 2, pad) == []              # A held does not
    # The hat: once per push, again after it came back.
    assert ti.evdev_event(ti.EV_ABS, ti.ABS_HAT0Y, 1, pad) == ["down"]
    assert ti.evdev_event(ti.EV_ABS, ti.ABS_HAT0Y, 1, pad) == []
    assert ti.evdev_event(ti.EV_ABS, ti.ABS_HAT0Y, 0, pad) == []
    assert ti.evdev_event(ti.EV_ABS, ti.ABS_HAT0Y, -1, pad) == ["up"]
    assert ti.evdev_event(ti.EV_ABS, ti.ABS_HAT0X, -1, pad) == ["left"]
    # A stick with the range the kernel gave: 0..255, centre 128.
    ranges = {ti.ABS_X: (0, 255), ti.ABS_Y: (0, 255)}
    assert ti.evdev_event(ti.EV_ABS, ti.ABS_X, 250, pad, ranges) == ["right"]
    assert ti.evdev_event(ti.EV_ABS, ti.ABS_X, 240, pad, ranges) == []     # still pushed
    assert ti.evdev_event(ti.EV_ABS, ti.ABS_X, 130, pad, ranges) == []     # back
    assert ti.evdev_event(ti.EV_ABS, ti.ABS_X, 250, pad, ranges) == ["right"]
    assert ti.evdev_event(ti.EV_ABS, ti.ABS_Y, 0, pad, ranges) == ["up"]
    assert ti.evdev_event(0x02, 0, 5, pad) == []                         # EV_REL: no
    assert set(ti.EVDEV_BUTTONS.values()) <= ti.ALL


_PROC = """I: Bus=0003 Vendor=046d Product=c52b Version=0111
N: Name="Logitech USB Receiver"
H: Handlers=sysrq kbd leds event3

I: Bus=0003 Vendor=045e Product=028e Version=0110
N: Name="Microsoft X-Box 360 pad"
H: Handlers=event7 js0

"""


def test_gamepads_found_in_proc():
    assert ti.parse_input_devices(_PROC) == [("Microsoft X-Box 360 pad", "/dev/input/event7")]
    assert ti.parse_input_devices(_PROC, "/tmp/x") == [("Microsoft X-Box 360 pad", "/tmp/x/event7")]
    assert ti.parse_input_devices("") == []


def test_evdev_reader_reads_a_fake_pad(tmp_path):
    proc = tmp_path / "devices"
    proc.write_text(_PROC)
    fifo = tmp_path / "event7"
    os.mkfifo(fifo)
    got: list[str] = []
    reader = ti.EvdevReader(got.append, str(proc), str(tmp_path))
    reader.start()
    try:
        assert _until(lambda: bool(reader._fds))
        ev = struct.Struct("llHHi")
        fd = os.open(fifo, os.O_WRONLY)
        os.write(fd, ev.pack(0, 0, ti.EV_KEY, 0x221, 1) + ev.pack(0, 0, 0, 0, 0)
                 + ev.pack(0, 0, ti.EV_KEY, 0x130, 1) + ev.pack(0, 0, ti.EV_ABS, ti.ABS_HAT0X, 1))
        assert _until(lambda: got == ["down", "ok", "right"]), got
        os.close(fd)
    finally:
        reader.halt()


def test_sdl_buttons_and_axes():
    assert ti.sdl_button(0) == "ok"
    assert ti.sdl_button(1) == "back"
    assert ti.sdl_button(11) == "up" and ti.sdl_button(14) == "right"
    assert ti.sdl_button(10) == "ch_up"
    assert ti.sdl_button(99) is None
    pad = ti.PadState()
    assert ti.sdl_axis(ti.SDL_AXIS_LEFTY, 32000, pad) == ["down"]
    assert ti.sdl_axis(ti.SDL_AXIS_LEFTY, 30000, pad) == []
    assert ti.sdl_axis(ti.SDL_AXIS_LEFTY, 0, pad) == []
    assert ti.sdl_axis(ti.SDL_AXIS_LEFTX, -32768, pad) == ["left"]
    assert ti.sdl_axis(ti.SDL_TRIGGER_RIGHT, 32767, pad) == ["fast_forward"]
    assert ti.sdl_axis(3, 32767, pad) == []                              # right stick: nothing
    assert set(ti.SDL_BUTTONS.values()) <= ti.ALL


class _Dev(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("timestamp", ctypes.c_uint32),
                ("which", ctypes.c_int32)]


class _Btn(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("timestamp", ctypes.c_uint32),
                ("which", ctypes.c_int32), ("button", ctypes.c_uint8)]


class _Axis(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("timestamp", ctypes.c_uint32),
                ("which", ctypes.c_int32), ("axis", ctypes.c_uint8),
                ("p1", ctypes.c_uint8), ("p2", ctypes.c_uint8), ("p3", ctypes.c_uint8),
                ("value", ctypes.c_int16)]


class _Event(ctypes.Union):
    _fields_ = [("type", ctypes.c_uint32), ("cdevice", _Dev), ("cbutton", _Btn),
                ("caxis", _Axis)]


class FakeSdl:
    """Just enough of pysdl2 for SdlReader: a queue of events."""
    SDL_INIT_GAMECONTROLLER = 0x2000
    SDL_CONTROLLERAXISMOTION, SDL_CONTROLLERBUTTONDOWN = 0x650, 0x651
    SDL_CONTROLLERDEVICEADDED, SDL_CONTROLLERDEVICEREMOVED = 0x653, 0x654
    SDL_Event = _Event

    def __init__(self, events) -> None:
        self.events = list(events)
        self.closed = self.quit = False

    def SDL_SetHint(self, *_a):  # noqa: N802 - SDL's names
        return True

    def SDL_Init(self, _flags):  # noqa: N802
        return 0

    def SDL_GetError(self):  # noqa: N802
        return b""

    def SDL_WaitEventTimeout(self, ref, _ms):  # noqa: N802
        if not self.events:
            time.sleep(0.01)
            return 0
        kind, fields = self.events.pop(0)
        ev = ref._obj
        ctypes.memset(ctypes.addressof(ev), 0, ctypes.sizeof(ev))
        part = getattr(ev, kind)
        for k, v in fields.items():
            setattr(part, k, v)
        return 1

    def SDL_GameControllerOpen(self, _which):  # noqa: N802
        return 1

    def SDL_GameControllerGetJoystick(self, c):  # noqa: N802
        return c

    def SDL_JoystickInstanceID(self, _j):  # noqa: N802
        return 0

    def SDL_GameControllerName(self, _c):  # noqa: N802
        return b"Fake pad"

    def SDL_GameControllerClose(self, _c):  # noqa: N802
        self.closed = True

    def SDL_QuitSubSystem(self, _flags):  # noqa: N802
        self.quit = True


def test_sdl_reader_with_a_fake_sdl():
    s = FakeSdl
    sdl = FakeSdl([
        ("cdevice", {"type": s.SDL_CONTROLLERDEVICEADDED, "which": 0}),
        ("cbutton", {"type": s.SDL_CONTROLLERBUTTONDOWN, "which": 0, "button": 12}),
        ("caxis", {"type": s.SDL_CONTROLLERAXISMOTION, "which": 0, "axis": 0, "value": 32000}),
        ("caxis", {"type": s.SDL_CONTROLLERAXISMOTION, "which": 0, "axis": 0, "value": 31000}),
        ("cbutton", {"type": s.SDL_CONTROLLERBUTTONDOWN, "which": 0, "button": 1}),
    ])
    got: list[str] = []
    reader = ti.SdlReader(got.append, sdl)
    reader.start()
    try:
        assert _until(lambda: got == ["down", "right", "back"]), got
    finally:
        reader.halt()
        reader.join(2)
    assert sdl.closed and sdl.quit


def test_hub_quiet_without_anything(tmp_path, monkeypatch):
    monkeypatch.setattr(ti, "lirc_socket", lambda env=None: None)
    monkeypatch.setattr(ti, "sdl_module", lambda: None)
    hub = ti.InputHub(lambda _a: None, str(tmp_path / "no-proc"))
    assert hub.start() == []
    hub.stop()
    assert ti.digit("digit_7") == 7 and ti.digit("ok") is None


# -- focus moves ---------------------------------------------------------------------

def test_nearest_element_each_way():
    # Two rows of three tiles, a tab bar above.
    tabs = [(0.0, 0.0, 100.0, 40.0), (120.0, 0.0, 100.0, 40.0)]
    row1 = [(0.0, 100.0, 200.0, 100.0), (220.0, 100.0, 200.0, 100.0), (440.0, 100.0, 200.0, 100.0)]
    row2 = [(0.0, 260.0, 200.0, 100.0), (220.0, 260.0, 200.0, 100.0), (440.0, 260.0, 200.0, 100.0)]
    cands = tabs + row1 + row2
    assert tv_nav.pick(row1[0], cands, "right") == cands.index(row1[1])
    assert tv_nav.pick(row1[1], cands, "left") == cands.index(row1[0])
    assert tv_nav.pick(row1[1], cands, "down") == cands.index(row2[1])
    assert tv_nav.pick(row2[2], cands, "up") == cands.index(row1[2])
    assert tv_nav.pick(row1[0], cands, "up") == cands.index(tabs[0])
    assert tv_nav.pick(row1[2], cands, "right") is None
    assert tv_nav.pick(row2[0], cands, "down") is None
    # The row first: a far tile in the row beats a near one below it.
    assert tv_nav.pick(row1[0], [row1[2], (210.0, 210.0, 50.0, 50.0)], "right") == 0


def test_grid_steps_and_edges():
    # 10 cells, 4 to a row: 0-3, 4-7, 8-9.
    assert tv_nav.grid_step(0, 10, 4, "right") == 1
    assert tv_nav.grid_step(3, 10, 4, "right") is None
    assert tv_nav.grid_step(4, 10, 4, "left") is None
    assert tv_nav.grid_step(5, 10, 4, "up") == 1
    assert tv_nav.grid_step(1, 10, 4, "up") is None
    assert tv_nav.grid_step(6, 10, 4, "down") == 9       # into the short last row
    assert tv_nav.grid_step(9, 10, 4, "down") is None
    assert tv_nav.grid_step(9, 10, 4, "right") is None
    assert tv_nav.grid_step(0, 0, 4, "down") is None
