"""Which way the player's picture is drawn, and how that stands.

The picture is drawn on a thread of its own by default (media/video_thread.py):
mpv's frames go from that thread straight to a native window of their own,
so nothing the window does can slow the picture and the picture slows
nothing in the window. The window's own thread (media/video_surface.py, the
way it always was) is what is used when that is switched off - Settings,
or DOPEIPTV_VIDEO_THREAD=0 - or when the own thread could not start; then
the reason is kept here, for Nerd > Diagnostics and the error report.

On macOS the own thread is new (its own NSOpenGLContext,
media/video_macos.py): it is on by default only in a test build - a
nightly, or run from a checkout - until it is as good as the window's
thread there; a release on a Mac draws on the window's thread unless the
person turned it on (`default_on`). DOPEIPTV_VIDEO_THREAD=0/1 and a choice
made in Settings win everywhere.

No Qt here: the player's side tells this module what it did.
"""

from __future__ import annotations

import os
import sys
from typing import Any

ENV = "DOPEIPTV_VIDEO_THREAD"
SETTING = "video_own_thread"

THREAD = "thread"
WINDOW = "window"
#: Why the window's thread is used on a Mac nobody switched: its default.
MAC_DEFAULT = "the default on macOS"

_OFF = frozenset({"0", "no", "off", "false"})
_ON = frozenset({"1", "yes", "on", "true"})

_state: dict[str, str] = {"mode": "", "why": "", "failed": "", "setting": "", "chosen": ""}
_HERE: list[tuple[str, bool]] = []


def is_test_build(version: str, frozen: bool, checkout: bool) -> bool:
    """A test build: a nightly - a fourth part in its version, 1.6.2.191
    (core/updates.is_nightly's rule) - or run from a git checkout."""
    return len(version.lstrip("vV").strip().split(".")) > 3 or (not frozen and checkout)


def default_on(platform: str, test: bool) -> bool:
    """The own thread with nothing switched: on everywhere - on macOS
    (*platform* "darwin") only in a test build, until it draws as well as
    the window's thread there."""
    return platform != "darwin" or test


def _here() -> tuple[str, bool]:
    """(this system, a test build): looked at once."""
    if not _HERE:
        from .. import VERSION
        try:
            from . import build_check
            root = os.path.dirname(build_check.package_dir())
            test = is_test_build(VERSION, build_check.is_frozen(), build_check.in_checkout(root))
        except Exception:
            test = is_test_build(VERSION, True, False)
        _HERE.append((sys.platform, test))
    return _HERE[0]


def default_on_here() -> bool:
    """The own thread's default in this copy (Settings shows it until the
    person chooses)."""
    return default_on(*_here())


def choice(settings: Any) -> str:
    """What the person chose in Settings: "on", "off", or "" (nothing yet -
    the default applies)."""
    try:
        v = settings.value(SETTING, None)
    except Exception:
        return ""
    if v is None:
        return ""
    v = str(v).strip().lower()
    return "off" if v in _OFF else "on" if v in _ON else ""


def wanted(settings: Any | None = None) -> tuple[bool, str]:
    """(the own thread is wanted, why not). The environment wins over the
    setting, either way, and the setting over this copy's default. Without
    *settings* (a multiview cell), what the setting said when it was last
    read."""
    env = os.environ.get(ENV, "").strip().lower()
    if env in _OFF:
        return False, f"switched off ({ENV}=0)"
    if env in _ON:
        return True, ""
    if settings is None:
        chose = _state["setting"]
    else:
        chose = _state["setting"] = choice(settings)
    if chose == "off":
        return False, "switched off in Settings"
    if chose == "on" or default_on_here():
        return True, ""
    return False, MAC_DEFAULT


def failed() -> str:
    """Why the own thread could not start in this run ("" when it could, or
    was not tried): every player after that uses the window's thread."""
    return _state["failed"]


def note_failed(reason: str) -> None:
    _state["failed"] = reason or "unknown"
    note(WINDOW, f"fallback: {_state['failed']}")


def chosen(mode: str) -> None:
    """The surface made for the player: the own thread starts with the first
    video, so until then this is what is known (Nerd > Diagnostics said
    "window thread" before anything had played)."""
    _state["chosen"] = mode


def pending() -> bool:
    """The own thread was chosen and has not started (nor failed) yet."""
    return _state["mode"] == "" and _state["chosen"] == THREAD


def note(mode: str, why: str = "") -> None:
    """What the player uses now (THREAD or WINDOW) and, for the window's
    thread, why."""
    _state["mode"], _state["why"] = mode, why


def mode() -> str:
    return _state["mode"]


def why() -> str:
    return _state["why"]


def line() -> str:
    """The report's line: "Video: own thread", "Video: window thread
    (fallback: ...)", or nothing known yet."""
    m = _state["mode"]
    if m == THREAD:
        return "Video: own thread"
    if m == WINDOW:
        return f"Video: window thread ({_state['why']})" if _state["why"] else "Video: window thread"
    if pending():
        return "Video: own thread (not started yet)"
    return "Video: not started yet"


def reset() -> None:
    """Tests only: as at start."""
    _state.update(mode="", why="", failed="", setting="", chosen="")
