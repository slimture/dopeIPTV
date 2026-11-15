"""Reminders and scheduled recordings as an iCalendar file (RFC 5545):
"Export to calendar…" saves one, and the remote API and the phone remote
serve it for a calendar to subscribe to.

Each programme is a VEVENT with a UID that stays the same from one export
to the next (a calendar that subscribes updates it, never adds it twice),
DTSTAMP, its times in UTC, the text escaped and every line folded at 75
octets, CRLF between them. A reminder has an alarm at its start, as the
app's own reminder; a recording open at the end has no DTEND (it then
lasts no time, as the standard says).
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

PRODID = "-//dopeIPTV//dopeIPTV//EN"
#: How often a subscribed calendar is asked to look again.
REFRESH = "PT15M"
_UID_HOST = "dopeiptv"


@dataclass(frozen=True)
class Event:
    uid: str
    start: float
    summary: str
    stop: float | None = None
    description: str = ""
    location: str = ""
    alarm: bool = False
    categories: tuple[str, ...] = field(default=())


def escape(text: Any) -> str:
    """TEXT as RFC 5545 3.3.11 has it: backslash, semicolon, comma and
    newline escaped, other control characters left out."""
    s = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    return "".join(c for c in s if c == "\t" or ord(c) >= 0x20 and ord(c) != 0x7F)


def fold(line: str, limit: int = 75) -> str:
    """One content line folded at *limit* octets (3.1): each further line
    starts with a space, and a UTF-8 character is never cut."""
    raw = line.encode("utf-8")
    if len(raw) <= limit:
        return line
    parts: list[str] = []
    room = limit
    cur = bytearray()
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > room:
            parts.append(cur.decode("utf-8"))
            cur = bytearray()
            room = limit - 1            # the space the next line starts with
        cur += b
    parts.append(cur.decode("utf-8"))
    return "\r\n ".join(parts)


def unfold(text: str) -> list[str]:
    """The content lines of *text*, unfolded (for reading one back)."""
    return text.replace("\r\n ", "").replace("\r\n\t", "").split("\r\n")


def utc(ts: float) -> str:
    """A DATE-TIME in UTC (form 2): 20261115T180000Z."""
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def uid(*parts: Any) -> str:
    """A UID made of what names the event, the same each time."""
    h = hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:24]
    return f"{h}@{_UID_HOST}"


def _event_lines(e: Event, stamp: str) -> list[str]:
    lines = ["BEGIN:VEVENT", f"UID:{e.uid}", f"DTSTAMP:{stamp}", f"DTSTART:{utc(e.start)}"]
    if e.stop is not None and int(e.stop) > int(e.start):
        lines.append(f"DTEND:{utc(e.stop)}")
    lines.append(f"SUMMARY:{escape(e.summary)}")
    if e.location:
        lines.append(f"LOCATION:{escape(e.location)}")
    if e.description:
        lines.append(f"DESCRIPTION:{escape(e.description)}")
    if e.categories:
        lines.append("CATEGORIES:" + ",".join(escape(c) for c in e.categories))
    lines += ["TRANSP:TRANSPARENT", "STATUS:CONFIRMED"]
    if e.alarm:
        lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{escape(e.summary)}",
                  "TRIGGER;RELATED=START:PT0S", "END:VALARM"]
    lines.append("END:VEVENT")
    return lines


def calendar(events: Iterable[Event], name: str = "dopeIPTV", now: float | None = None) -> str:
    """The whole VCALENDAR, CRLF line endings and all."""
    stamp = utc(time.time() if now is None else now)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", f"X-WR-CALNAME:{escape(name)}",
             f"REFRESH-INTERVAL;VALUE=DURATION:{REFRESH}", f"X-PUBLISHED-TTL:{REFRESH}"]
    for e in sorted(events, key=lambda ev: (ev.start, ev.uid)):
        lines += _event_lines(e, stamp)
    lines.append("END:VCALENDAR")
    return "".join(fold(line) + "\r\n" for line in lines)


# -- what the app has ------------------------------------------------------------

def _num(value: Any) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def reminder_events(reminders: Iterable[dict], source_id: str = "", source: str = "",
                    note: str = "", stops: dict[tuple[Any, int], float] | None = None) -> list[Event]:
    """A source's reminders (ReminderStore.all()): the programme at its
    start, on its channel, with an alarm. *stops*: a programme's end by
    (stream id, start) where the guide knows it and the reminder does not."""
    out: list[Event] = []
    for r in reminders:
        start = _num(r.get("start"))
        if start is None:
            continue
        ch = r.get("ch") or {}
        sid = r.get("stream_id")
        stop = _num(r.get("stop")) or (stops or {}).get((sid, int(start)))
        channel = str(ch.get("name") or "")
        title = str(r.get("title") or channel or "?")
        desc = " · ".join(p for p in (note, channel, source) if p)
        out.append(Event(uid("reminder", source_id, sid, int(start)), start, title, stop,
                         desc, channel, alarm=True))
    return out


def recording_events(jobs: Iterable[dict], note: str = "", now: float | None = None) -> list[Event]:
    """The recordings to come and the ones under way (RecordingManager.jobs)."""
    t = time.time() if now is None else now
    out: list[Event] = []
    for j in jobs:
        if j.get("status") not in ("scheduled", "recording"):
            continue
        start, stop = _num(j.get("start")), _num(j.get("stop"))
        if start is None or (stop is not None and stop < t and j.get("status") != "recording"):
            continue
        channel = str(j.get("channel") or "")
        desc = " · ".join(p for p in (note, channel) if p)
        out.append(Event(uid("recording", j.get("id")), start, str(j.get("title") or channel or "?"),
                         stop, desc, channel))
    return out
