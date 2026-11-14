"""Error reports and feedback, sent only when the person asks, with nothing
in them that identifies a stream or a person.

What goes in: the app's and the system's versions, what happened (a
Python traceback, or what the native crash handler wrote when the app
went down last time), the last log lines, and the person's own words.
What is taken out before they see it: every address (a URL keeps only its
scheme), IP and MAC addresses, e-mail addresses, logins and tokens in
key=value form, the home folder and the account name in paths, and every
value the app knows to be personal - its sources' names, servers, user
names, passwords and box MACs, the channels in the list and the titles in
the history. The person sees the whole report, can change or delete any
of it, and nothing is sent until they press Send.
"""

from __future__ import annotations

import getpass
import json
import os
import platform
import re
import sys
import urllib.request
from typing import Any, Callable, Iterable

from .log import log, recent_lines

ENDPOINT = "https://dopeiptv.org/feedback/report"
MAX_TEXT = 48_000
#: What the native crash handler writes to, in the log folder.
CRASH_FILE = "last-crash.txt"

_URL = re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.-]{1,15})://[^\s'\"<>()\[\]]+")
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
# IPv6 in full (six groups or more, so a time like 21:05:03 is left alone)
# or shortened with "::" (fe80::1, ::1).
_IPV6 = re.compile(r"(?<![\w:])(?:(?:[0-9A-Fa-f]{1,4}:){5,7}[0-9A-Fa-f]{1,4}"
                   r"|[0-9A-Fa-f:]{0,30}::[0-9A-Fa-f:]{0,30}[0-9A-Fa-f])(?![\w:])")
_MAC = re.compile(r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f])")
_EMAIL = re.compile(r"[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63})+")
_KEYVAL = re.compile(r"(?i)\b(user(?:name)?|pass(?:word)?|pwd|token|key|api_key|apikey|secret|"
                     r"auth|sid|session|mac|play_token|login)(\s*[=:]\s*)([^\s&'\",;]+)")


def scrub(text: str, personal: Iterable[str] = ()) -> str:
    """*text* without anything that identifies a stream or a person."""
    # Whole addresses first (a login or a name inside one goes with it),
    # then the person's own values, longest first (a name inside another).
    out = _URL.sub(lambda m: f"{m.group(1)}://<address>", text or "")
    for value in sorted({v.strip() for v in personal if v and len(v.strip()) >= 3}, key=len, reverse=True):
        out = re.sub(re.escape(value), "<private>", out, flags=re.IGNORECASE)
    out = _EMAIL.sub("<email>", out)
    out = _MAC.sub("<mac>", out)
    out = _IPV6.sub("<ip>", out)
    out = _IPV4.sub(lambda m: m.group(0) if m.group(0).startswith("127.") else "<ip>", out)
    out = _KEYVAL.sub(lambda m: f"{m.group(1)}{m.group(2)}<hidden>", out)
    home = os.path.expanduser("~")
    if len(home) > 1:
        out = out.replace(home, "~")
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    if user and len(user) >= 3:
        out = re.sub(rf"(?<![\w]){re.escape(user)}(?![\w])", "<user>", out)
    return out


def personal_values(playlists: Iterable[dict], channels: Iterable[str] = (),
                    titles: Iterable[str] = ()) -> list[str]:
    """What the app knows to be someone's: sources' names, servers, logins
    and boxes; channel names; titles watched."""
    out: list[str] = []
    for p in playlists or []:
        for key in ("name", "server", "username", "password", "mac", "epg_url"):
            v = str(p.get(key) or "")
            if v:
                out.append(v)
                if key == "server":
                    host = re.sub(r"^[a-z]+://", "", v).split("/")[0].split("@")[-1].split(":")[0]
                    out.append(host)
    out += [str(c) for c in channels if c]
    out += [str(t) for t in titles if t]
    return out


def system_lines() -> list[str]:
    from .. import __version__
    lines = [f"dopeIPTV {__version__}", f"System: {platform.system()} {platform.release()} ({platform.machine()})",
             f"Python {platform.python_version()}, frozen: {bool(getattr(sys, 'frozen', False))}"]
    try:
        from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
        lines.append(f"Qt {QT_VERSION_STR}, PyQt {PYQT_VERSION_STR}")
    except Exception:
        pass
    try:
        import mpv  # type: ignore[import-not-found]
        lines.append(f"python-mpv {getattr(mpv, '__version__', '?')}")
    except Exception:
        lines.append("libmpv: not loaded")
    return lines


def build(kind: str, message: str = "", trace: str = "", with_log: bool = True,
          personal: Iterable[str] = ()) -> str:
    """The report as the person will see it, already scrubbed."""
    parts = [f"Kind: {kind}", *system_lines()]
    if message.strip():
        parts += ["", "What happened:", message.strip()]
    if trace.strip():
        parts += ["", "Error:", trace.strip()]
    if with_log:
        tail = recent_lines(80)
        if tail:
            parts += ["", "Recent log:", *tail]
    return scrub("\n".join(parts), personal)[:MAX_TEXT]


def send(kind: str, text: str, timeout: float = 15.0, endpoint: str | None = None) -> bool:
    """POST the report as the person left it. True when it arrived."""
    from .. import __version__
    body = json.dumps({"kind": kind if kind in ("error", "crash", "feedback") else "feedback",
                       "version": __version__, "os": platform.system(),
                       "text": (text or "")[:MAX_TEXT]}).encode("utf-8")
    req = urllib.request.Request(endpoint or ENDPOINT, data=body, method="POST", headers={
        "Content-Type": "application/json", "User-Agent": f"dopeIPTV/{__version__}",
        "X-Dopeiptv-Report": "1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ok = 200 <= r.status < 300
    except Exception as e:
        log.info("report: not sent (%s)", type(e).__name__)
        return False
    log.info("report: sent (%s)", kind)
    return ok


# -- errors as they happen, and a crash from last time ------------------------------

_listeners: list[Callable[[str], None]] = []
_pending_crash = ""


def add_listener(fn: Callable[[str], None]) -> None:
    """*fn(trace)* hears of each uncaught error (from any thread)."""
    _listeners.append(fn)


def notify_error(trace: str) -> None:
    for fn in list(_listeners):
        try:
            fn(trace)
        except Exception:
            pass


def set_pending_crash(text: str) -> None:
    global _pending_crash
    _pending_crash = text or ""


def pending_crash() -> str:
    """What the native crash handler wrote when the app last went down
    (once: asking takes it)."""
    global _pending_crash
    text, _pending_crash = _pending_crash, ""
    return text


# -- a crash the app could not catch (the native handler's file) -------------------

def crash_path(folder: str) -> str:
    return os.path.join(folder, CRASH_FILE)


def take_last_crash(folder: str) -> str:
    """What the native crash handler wrote while the app last ran, if it
    went down; the file is emptied (it is reused for this run)."""
    path = crash_path(folder)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return ""
    try:
        open(path, "w").close()
    except OSError:
        pass
    return text.strip() if "Fatal Python error" in text or "Segmentation fault" in text \
        or "Current thread" in text else ""


def open_crash_file(folder: str) -> Any:
    """The file the native crash handler writes to this run (kept open)."""
    os.makedirs(folder, exist_ok=True)
    return open(crash_path(folder), "w", encoding="utf-8")  # noqa: SIM115 - held for the run
