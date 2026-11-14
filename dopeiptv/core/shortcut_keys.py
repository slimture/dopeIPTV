"""Keyboard shortcuts: the app's own keys and the user's over them.

The app's rebindable actions and their keys are listed here (``ACTIONS``).
The user's keys (Settings > Advanced > Keyboard shortcuts) win over these,
always: only what differs from the app's own is stored, as JSON (action ->
key in Qt's portable text, "Ctrl+Shift+M"; "" for an action given no key)
under ``keyboard_shortcuts``. The old editor's keys (one setting per action
and platform) are moved there once by the window.

A key does one thing: given to an action, the one that had it loses it
(``assign`` says which). The keys the app keeps for itself - Esc, Delete,
digits for channel numbers, Enter, the arrows, Tab, the media keys and the
app menu's own - cannot be taken; a few others work but are said to be
risky (``reserved``). Qt is not needed here: the window turns these strings
into QKeySequences, and shows them as the system writes keys (⌘ on macOS).
"""

from __future__ import annotations

import json
import sys
from typing import Any, NamedTuple, Protocol

SETTING = "keyboard_shortcuts"
#: Where the old editor kept each key: per platform, per action.
LEGACY = "shortcut/{platform}/{action}"

#: (id, the app's own key, i18n label key), in the order they are shown.
ACTIONS: tuple[tuple[str, str, str], ...] = (
    ("zap_next", "Ctrl+Right", "sc_next_channel"),
    ("zap_prev", "Ctrl+Left", "sc_prev_channel"),
    ("last_channel", "Backspace", "sc_last_channel"),
    ("pause", "Space", "sc_play_pause"),
    ("fullscreen", "F", "sc_fullscreen"),
    ("mute", "M", "sc_mute"),
    ("popout", "P", "sc_popout"),
    ("record", "R", "sc_record"),
    ("stats", "I", "sc_stats"),
    ("epg_guide", "Ctrl+G", "sc_epg_guide"),
    ("epg_search", "Ctrl+Shift+F", "sc_epg_search"),
    ("reminders", "Ctrl+Shift+R", "sc_reminders"),
    ("sidebar", "Ctrl+B", "sc_sidebar"),
    ("focus_mode", "Ctrl+Shift+M", "sc_focus_mode"),
)
DEFAULTS: dict[str, str] = {sid: key for sid, key, _label in ACTIONS}
LABELS: dict[str, str] = {sid: label for sid, _key, label in ACTIONS}

BLOCKED = "blocked"
WARN = "warn"


class Reserved(NamedTuple):
    """Why a key is the app's (BLOCKED: it cannot be taken) or risky (WARN:
    it works, with a word of warning). *why* is an i18n key; *name*, when
    set, the i18n key of the menu entry the key belongs to."""
    level: str
    why: str
    name: str = ""


class Readable(Protocol):
    def value(self, key: str, default: Any = None) -> Any: ...


class Writable(Readable, Protocol):
    def setValue(self, key: str, value: Any) -> None: ...  # noqa: N802 - Qt's name

    def remove(self, key: str) -> None: ...


# -- the text form -------------------------------------------------------------

# Qt's portable order of the modifiers, and the spellings of each it reads.
_MOD_ORDER = ("Meta", "Ctrl", "Alt", "Shift", "Num")
_MODS = {"meta": "Meta", "ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt",
         "shift": "Shift", "num": "Num"}
_KEY_NAMES = {"esc": "Esc", "escape": "Esc", "del": "Del", "delete": "Del",
              "ins": "Ins", "insert": "Ins", "backspace": "Backspace",
              "space": "Space", "return": "Return", "enter": "Enter", "tab": "Tab",
              "backtab": "Backtab", "left": "Left", "right": "Right", "up": "Up",
              "down": "Down", "home": "Home", "end": "End", "pgup": "PgUp",
              "pageup": "PgUp", "pgdown": "PgDown", "pagedown": "PgDown"}


def _chord(text: str) -> str:
    t = text.strip()
    if not t:
        return ""
    if t == "+":
        mods, key = [], "+"
    elif t.endswith("++"):
        mods, key = t[:-2].split("+"), "+"
    else:
        *mods, key = t.split("+")
    names = []
    for m in mods:
        name = _MODS.get(m.strip().casefold())
        if name is None:
            return ""
        names.append(name)
    key = key.strip()
    if not key:
        return ""
    if len(key) == 1:
        key = key.upper()
    else:
        low = key.casefold()
        if low in _KEY_NAMES:
            key = _KEY_NAMES[low]
        elif low[0] == "f" and low[1:].isdigit():
            key = "F" + low[1:]
    return "+".join([m for m in _MOD_ORDER if m in names] + [key])


def canon(seq: Any) -> str:
    """*seq* in Qt's portable form - modifiers in Qt's order, a letter in
    capitals, the usual names for the named keys - or "" for no key or one
    that cannot be read. A sequence of several chords keeps them all."""
    if not isinstance(seq, str):
        return ""
    chords = [_chord(c) for c in seq.split(", ")]
    if not chords or not all(chords):
        return ""
    return ", ".join(chords)


def same(a: str, b: str) -> bool:
    """The same key, however it is written."""
    ca, cb = canon(a), canon(b)
    return bool(ca) and ca.casefold() == cb.casefold()


# -- the keys the app keeps --------------------------------------------------------

#: The app menu's own keys (main_window.py, mw_privacy.py) and the entry
#: each opens.
APP_KEYS: dict[str, str] = {"Ctrl+O": "menu_open_video",
                            "Ctrl+Shift+N": "private_menu",
                            "Ctrl+Shift+Del": "clear_menu"}


def _table() -> dict[str, Reserved]:
    out: dict[str, Reserved] = {}

    def add(keys: list[str], r: Reserved) -> None:
        for k in keys:
            out[canon(k).casefold()] = r
    add(["Esc"], Reserved(BLOCKED, "sc_why_escape"))
    add(["Del"], Reserved(BLOCKED, "sc_why_delete"))
    digits = [str(d) for d in range(10)]
    add(digits + ["Shift+" + d for d in digits] + ["Num+" + d for d in digits],
        Reserved(BLOCKED, "sc_why_digits"))
    add(["Return", "Enter", "Num+Enter", "Shift+Return", "Shift+Enter"],
        Reserved(BLOCKED, "sc_why_enter"))
    add(["Left", "Right", "Up", "Down", "Shift+Left", "Shift+Right"],
        Reserved(BLOCKED, "sc_why_arrows"))
    add(["Tab", "Shift+Tab", "Backtab", "Shift+Backtab"], Reserved(BLOCKED, "sc_why_tab"))
    add(["Toggle Media Play/Pause", "Media Play", "Media Pause", "Media Stop",
         "Media Next", "Media Previous"], Reserved(BLOCKED, "sc_why_media"))
    for key, name in APP_KEYS.items():
        add([key], Reserved(BLOCKED, "sc_why_menu", name))
    add(["Ctrl+C", "Ctrl+V", "Ctrl+X", "Ctrl+A", "Ctrl+Z", "Ctrl+Y", "Ctrl+Shift+Z"],
        Reserved(WARN, "sc_warn_edit"))
    return out


# What the system itself may take first, by platform. On macOS Qt's "Ctrl"
# is the Command key and "Meta" the Control key.
_SYSTEM = {
    "darwin": ["Ctrl+Q", "Ctrl+H", "Ctrl+M", "Ctrl+W", "Ctrl+Space", "Ctrl+Tab", "Ctrl+,",
               "Ctrl+Alt+Esc", "Ctrl+Shift+3", "Ctrl+Shift+4", "Ctrl+Shift+5"],
    "other": ["Alt+F4", "Alt+Tab", "Alt+Space", "Ctrl+Alt+Del", "Ctrl+Alt+T", "Ctrl+Esc",
              "Alt+Esc", "Print"],
}
_TABLE = _table()


def reserved(seq: str, platform: str = sys.platform) -> Reserved | None:
    """Whether *seq* is one the app keeps (BLOCKED), works with a warning
    (WARN), or is free (None)."""
    c = canon(seq)
    if not c:
        return None
    hit = _TABLE.get(c.casefold())
    if hit is not None:
        return hit
    mac = platform == "darwin"
    system = _SYSTEM["darwin" if mac else "other"]
    if any(same(c, k) for k in system) or (not mac and c.split("+")[0] == "Meta"):
        return Reserved(WARN, "sc_warn_system")
    return None


def blocked(seq: str, platform: str = sys.platform) -> bool:
    r = reserved(seq, platform)
    return r is not None and r.level == BLOCKED


# -- the user's keys over the app's ---------------------------------------------

def parse(text: Any) -> dict[str, str]:
    """The stored overrides: known actions only, each key in portable form
    ("" kept: that action has no key). Anything unreadable is left out."""
    try:
        data = json.loads(text) if isinstance(text, str) and text.strip() else {}
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for sid, seq in data.items():
        if sid not in DEFAULTS or not isinstance(seq, str):
            continue
        c = canon(seq)
        if c or not seq.strip():
            out[sid] = c
    return out


def effective(overrides: dict[str, str], platform: str = sys.platform) -> dict[str, str]:
    """Each action's key in force ("" for none): the user's key where it can
    be had - not one the app keeps, not one an action before it in ACTIONS
    has - else the app's own if no other action has that, else none. Never
    two actions on one key."""
    used: set[str] = set()
    out: dict[str, str] = {}
    for sid, _default, _label in ACTIONS:
        if sid not in overrides:
            continue
        seq = canon(overrides[sid])
        if not overrides[sid].strip():
            out[sid] = ""
        elif seq and seq.casefold() not in used and not blocked(seq, platform):
            out[sid] = seq
            used.add(seq.casefold())
    for sid, default, _label in ACTIONS:
        if sid in out:
            continue
        free = bool(default) and default.casefold() not in used
        out[sid] = default if free else ""
        if free:
            used.add(default.casefold())
    return {sid: out[sid] for sid, _d, _l in ACTIONS}


def tidy(overrides: dict[str, str], platform: str = sys.platform) -> dict[str, str]:
    """The fewest overrides that give the same keys: one per action whose
    key is not the app's own, "" for one left without a key."""
    eff = effective(overrides, platform)
    return {sid: eff[sid] for sid, default, _l in ACTIONS if eff[sid] != default}


def dump(overrides: dict[str, str], platform: str = sys.platform) -> str:
    """What is stored: the overrides as compact JSON, "" when there are none."""
    t = tidy(overrides, platform)
    return json.dumps(t, sort_keys=True, separators=(",", ":"), ensure_ascii=False) if t else ""


def assign(overrides: dict[str, str], action: str, seq: str,
           platform: str = sys.platform) -> tuple[dict[str, str], list[str]]:
    """*action* gets *seq* ("" for no key): the new overrides, tidied, and
    the actions that lost the key to it. A key the app keeps changes
    nothing."""
    if action not in DEFAULTS:
        raise KeyError(action)
    base = tidy(overrides, platform)
    c = canon(seq)
    if seq.strip() and (not c or blocked(c, platform)):
        return base, []
    lost = [sid for sid, key in effective(base, platform).items()
            if c and sid != action and same(key, c)]
    new = dict(base)
    for sid in lost:
        new[sid] = ""
    new[action] = c
    return tidy(new, platform), lost


def from_settings(settings: Readable | None) -> dict[str, str]:
    """The keys in force for *settings*: the stored ones over the app's."""
    if settings is None:
        return dict(DEFAULTS)
    return effective(parse(settings.value(SETTING, "") or ""))


def migrate(settings: Writable, platform: str = sys.platform) -> int:
    """The old editor's keys (``LEGACY``, this platform's) into SETTING,
    once: kept, conflicts resolved as everywhere, a key set since winning.
    The old ones are removed. Returns how many there were."""
    old: dict[str, str] = {}
    for sid, _default, _label in ACTIONS:
        key = LEGACY.format(platform=platform, action=sid)
        value = settings.value(key)
        if value is not None:
            old[sid] = str(value)
            settings.remove(key)
    if not old:
        return 0
    merged = tidy(parse(json.dumps(old)), platform)
    for sid, seq in parse(settings.value(SETTING, "") or "").items():
        merged, _lost = assign(merged, sid, seq, platform)
    settings.setValue(SETTING, dump(merged, platform))
    return len(old)
