"""Which way the player's picture is drawn, and how that stands.

The picture is drawn on a thread of its own by default (media/video_thread.py):
mpv's frames go from that thread straight to a native window of their own,
so nothing the window does can slow the picture and the picture slows
nothing in the window. The window's own thread (media/video_surface.py, the
way it always was) is what is used when that is switched off - Settings,
or DOPEIPTV_VIDEO_THREAD=0 - or when the own thread could not start; then
the reason is kept here, for Nerd > Diagnostics and the error report.

No Qt here: the player's side tells this module what it did.
"""

from __future__ import annotations

import os
from typing import Any

ENV = "DOPEIPTV_VIDEO_THREAD"
SETTING = "video_own_thread"

THREAD = "thread"
WINDOW = "window"

_OFF = frozenset({"0", "no", "off", "false"})
_ON = frozenset({"1", "yes", "on", "true"})

_state: dict[str, str] = {"mode": "", "why": "", "failed": "", "setting_off": "", "chosen": ""}


def wanted(settings: Any | None = None) -> tuple[bool, str]:
    """(the own thread is wanted, why not). The environment wins over the
    setting, either way. Without *settings* (a multiview cell), what the
    setting said when it was last read."""
    env = os.environ.get(ENV, "").strip().lower()
    if env in _OFF:
        return False, f"switched off ({ENV}=0)"
    if env in _ON:
        return True, ""
    if settings is None:
        off = _state["setting_off"] == "1"
    else:
        try:
            off = str(settings.value(SETTING, "true")).strip().lower() in _OFF
        except Exception:
            off = False
        _state["setting_off"] = "1" if off else ""
    if off:
        return False, "switched off in Settings"
    return True, ""


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
    _state.update(mode="", why="", failed="", setting_off="", chosen="")
