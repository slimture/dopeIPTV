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
from typing import Any, Callable, Iterable

from .log import log, recent_lines

ENDPOINT = "https://dopeiptv.org/feedback/report"
REPLIES = "https://dopeiptv.org/feedback/replies"
#: How long the app asks whether a report was answered, and keeps its answers.
ASK_DAYS, KEEP_DAYS = 30, 90
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
# A supporter key pasted into a report would be the one thing that could tie it to a donation.
_SUPPORTER = re.compile(r"(?i)\bDS1-[A-Za-z0-9_-]{20,}")
_KEYVAL = re.compile(r"(?i)\b(user(?:name)?|pass(?:word)?|pwd|token|key|api_key|apikey|secret|"
                     r"auth|sid|session|mac|play_token|login)(\s*[=:]\s*)([^\s&'\",;]+)")


def scrub(text: str, personal: Iterable[str] = ()) -> str:
    """*text* without anything that identifies a stream or a person."""
    # Whole addresses first (a login or a name inside one goes with it),
    # then the person's own values, longest first (a name inside another).
    out = _URL.sub(lambda m: f"{m.group(1)}://<address>", text or "")
    for value in sorted({v.strip() for v in personal if v and len(v.strip()) >= 3}, key=len, reverse=True):
        out = re.sub(re.escape(value), "<private>", out, flags=re.IGNORECASE)
    out = _SUPPORTER.sub("<supporter key>", out)
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


# -- the part of a log that matters -------------------------------------------------

#: What a log line's message starts with, for each answer to "Where?" in the
#: feedback window (the app's own prefixes: "play: ...", "EPG: ...").
AREA_WORDS: dict[str, tuple[str, ...]] = {
    "playback": ("play", "mpv", "stream", "eof", "embedded playback", "dvr", "local buffer", "gl context",
                 "opengl", "vid", "mirror", "subtitle", "audio", "resume", "failover", "nerd stream", "retry",
                 "qt platform"),
    "guide": ("epg", "xmltv", "guide"),
    "recording": ("record", "dvr recording", "stream-record", "rec "),
    "lists": ("m3u", "xtream", "stalker", "enigma2", "tvheadend", "hdhomerun", "lan scan", "queue",
              "playlist", "channel", "categor", "source", "tmdb", "popular"),
    "settings": ("nerd", "settings", "i18n", "icon theme", "desktop entry", "remote", "share"),
    "cast": ("cast", "dlna", "chromecast"),
    "updates": ("update", "self-check", "download", "signature"),
}
_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_LOUD = ("WARNING", "ERROR", "CRITICAL")


def _parts(line: str) -> tuple[str, str]:
    """(level, message) of a log line, in this run's form ("21:04:11 INFO
    play: ...") or the file's ("2026-11-14 21:04:11,123 INFO play: ...")."""
    words = line.split(" ", 4)
    for i, w in enumerate(words[:4]):
        if w in _LEVELS:
            return w, " ".join(words[i + 1:])
    return "", line


def relevant_log(lines: list[str], area: str = "", most: int = 120, tail: int = 15) -> list[str]:
    """The lines of *lines* that matter: every warning and error with two
    lines either side, the lines of *area* (AREA_WORDS), and the last
    *tail* lines - what happened just before. The same line over and over
    becomes one, with how many; a gap between kept lines is shown as "…".
    At most *most* lines, the latest kept when there are more."""
    words = AREA_WORDS.get(area, ())
    keep: set[int] = set(range(max(0, len(lines) - tail), len(lines)))
    for i, line in enumerate(lines):
        level, msg = _parts(line)
        if level in _LOUD or msg.startswith(("Traceback", "  File ")):
            keep.update(range(max(0, i - 2), min(len(lines), i + 3)))
        elif words and msg.lower().startswith(words):
            keep.add(i)
    out: list[str] = []
    last, same, prev_key = -2, 0, None
    for i in sorted(keep):
        key = re.sub(r"\d+", "N", _parts(lines[i])[1])
        if i == last + 1 and key == prev_key:
            same += 1
            last = i
            continue
        if same:
            out.append(f"   … the same line ×{same + 1}")
            same = 0
        if out and i != last + 1:
            out.append("…")
        out.append(lines[i])
        last, prev_key = i, key
    if same:
        out.append(f"   … the same line ×{same + 1}")
    return out[-most:]


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


#: The most the site takes in one report, in bytes as sent.
MAX_BODY = 56_000


def _post(url: str, body: bytes, timeout: float) -> tuple[int, bytes]:
    """POST JSON with requests, whose certificates (certifi) travel with the
    app: urllib asks the system's OpenSSL for them, and a bundled Python on
    macOS has none, so every report failed there."""
    from .. import __version__
    from ._lazy_requests import requests
    r = requests.post(url, data=body, timeout=timeout, headers={
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": f"dopeIPTV/{__version__}", "X-Dopeiptv-Report": "1"})
    return r.status_code, r.content


def _fit(text: str, room: int) -> str:
    """The text cut to ``room`` bytes of UTF-8, on a character boundary."""
    raw = text.encode("utf-8")
    return text if len(raw) <= room else raw[:max(0, room)].decode("utf-8", "ignore")


def send(kind: str, text: str, timeout: float = 15.0, endpoint: str | None = None,
         extra: dict | None = None) -> bool:
    """POST the report as the person left it. True when it arrived."""
    from .. import __version__
    extra = extra or {}
    fields = {"kind": kind if kind in ("error", "crash", "feedback") else "feedback",
              "version": __version__, "os": platform.system(),
              "text": (text or "")[:MAX_TEXT]}
    # What feedback is about (a topic, an area, how often) and "supporter":
    # "yes" from a supporter (read first): single words from a short list.
    fields.update({k: v for k, v in extra.items()
                   if k in ("topic", "area", "often", "supporter")
                   and isinstance(v, str) and v.isalpha() and len(v) <= 12})
    # The hash of the code this computer keeps to be told the answers.
    if re.fullmatch(r"[0-9a-f]{64}", str(extra.get("reply") or "")):
        fields["reply"] = str(extra["reply"])
    # UTF-8 as it is (an escaped "å" is six bytes), and cut to what the site
    # takes - a long log once made the whole report too big to be kept.
    body = json.dumps(fields, ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_BODY:
        fields["text"] = _fit(fields["text"], len(fields["text"].encode("utf-8")) - (len(body) - MAX_BODY) - 64)
        body = json.dumps(fields, ensure_ascii=False).encode("utf-8")
    try:
        status, _data = _post(endpoint or ENDPOINT, body, timeout)
    except Exception as e:
        log.info("report: not sent (%s)", type(e).__name__)
        return False
    if not 200 <= status < 300:
        log.info("report: not sent (HTTP %s)", status)
        return False
    log.info("report: sent (%s)", kind)
    return True


# -- answers: a random code kept here, its hash sent with the report ---------------

def new_code() -> tuple[str, str]:
    """(code, its SHA-256): the code stays on this computer, the hash goes
    with the report - the one link between the two, naming nobody."""
    import hashlib
    import secrets
    code = secrets.token_urlsafe(24)
    return code, hashlib.sha256(code.encode("ascii")).hexdigest()


def code_hash(code: str) -> str:
    import hashlib
    return hashlib.sha256(code.encode("ascii")).hexdigest()


def tickets(settings: Any) -> list[dict]:
    """The reports this computer asked answers to: {"c": code, "t": sent,
    "w": the first words (kept here only), "r": answers, "seen": how many
    were shown}. Old ones without answers drop off."""
    import time
    try:
        raw = json.loads(str(settings.value("feedback_tickets", "") or "[]"))
    except (ValueError, TypeError):
        raw = []
    now = time.time()
    out = []
    for t in raw if isinstance(raw, list) else []:
        if not isinstance(t, dict) or not isinstance(t.get("c"), str):
            continue
        age = now - float(t.get("t") or 0)
        if age < ASK_DAYS * 86400 or (t.get("r") and age < KEEP_DAYS * 86400):
            out.append(t)
    return out[-20:]


def save_tickets(settings: Any, items: list[dict]) -> None:
    settings.setValue("feedback_tickets", json.dumps(items[-20:], ensure_ascii=False))


def fetch_replies(codes: list[str], timeout: float = 15.0, endpoint: str | None = None) -> dict | None:
    """{code: [answers]} for the codes whose reports were answered; None
    when the site could not be asked. Only the hashes are sent."""
    by_hash = {code_hash(c): c for c in codes[:20]}
    try:
        status, data = _post(endpoint or REPLIES, json.dumps({"codes": list(by_hash)}).encode("utf-8"), timeout)
        if status != 200:
            raise OSError(f"HTTP {status}")
        got = json.loads(data or b"{}").get("replies") or {}
    except Exception as e:
        log.info("report: answers not asked (%s)", type(e).__name__)
        return None
    return {by_hash[h]: a for h, a in got.items() if h in by_hash and isinstance(a, list)}


#: The fixed answers an admin picks; the app says them in its own language.
REPLY_CODES = ("thanks", "known", "fixed", "needinfo", "note")


# -- not too many: feedback is a press of Send, not a stream ----------------------

#: A minute between two, a few a day, from one copy of the app (the site
#: has limits of its own - this one says why before anything is sent).
FEEDBACK_GAP = 60.0
FEEDBACK_PER_DAY = 8


def feedback_wait(sent: Iterable[float], now: float) -> str:
    """"" when feedback may go now; "wait" right after one, "day" when
    today's are used up."""
    recent = [t for t in sent if 0 <= now - t < 86400]
    if recent and now - max(recent) < FEEDBACK_GAP:
        return "wait"
    if len(recent) >= FEEDBACK_PER_DAY:
        return "day"
    return ""


def text_hash(text: str) -> str:
    """The same words, however spaced, give the same hash."""
    import hashlib
    return hashlib.sha256(" ".join((text or "").lower().split()).encode("utf-8")).hexdigest()[:24]


def sent_times(raw: object) -> list[float]:
    out: list[float] = []
    for part in str(raw or "").split(","):
        try:
            out.append(float(part))
        except ValueError:
            continue
    return out


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
