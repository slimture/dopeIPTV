"""Recordings as their place shows them, no Qt.

* What records now and what is to come (``pending``: the running ones
  first, then the scheduled by their start), how long one has run
  (``elapsed_text``: "12:34", "1:02:03"), the day a recording is of
  (``day_of``: today, yesterday, this week, earlier - the list's groups).
* A recording's length: what ffmpeg said of its file, else where it was
  played to its end, else its job's times (``length_of``) - never a
  guess. Its frame and length are found off the window's thread
  (``probe``): ffmpeg asked what the file holds, then one frame from a
  tenth of the way in (``seek_for``) into the image cache - beside Local
  files' frames, by the same name - and the length kept next to it
  (``.len``), so neither is asked again. Never a file being recorded.
* Watched marks (``WatchedMarks``, the profile's own ``rec_watched``): a
  recording played to its end, or marked by hand; renamed, moved and
  deleted with its file; at most ``WATCHED_MAX``, the newest kept.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from .rec_mp4 import parse_probe

__all__ = ["Probe", "WatchedMarks", "day_of", "elapsed_text", "frame_path", "length_of",
           "pending", "probe", "seek_for", "shown_title"]

WATCHED_KEY = "rec_watched"
WATCHED_MAX = 500
#: How long a probe or a frame may take.
PROBE_TIMEOUT_S = 30.0
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class Settings(Protocol):
    def value(self, key: str, default: Any = None) -> Any: ...
    def setValue(self, key: str, value: Any) -> None: ...  # noqa: N802 - Qt's name


# -- what records now and next --------------------------------------------------------

def pending(jobs: Iterable[dict]) -> list[dict]:
    """The recordings running now (the oldest first), then the scheduled
    ones by their start."""
    rows = [j for j in jobs if isinstance(j, dict)
            and j.get("status") in ("recording", "scheduled")]

    def order(j: dict) -> tuple[int, float]:
        try:
            start = float(j.get("start") or 0)
        except (TypeError, ValueError):
            start = 0.0
        return (0 if j.get("status") == "recording" else 1, start)
    return sorted(rows, key=order)


def elapsed_text(seconds: float) -> str:
    """How long it has run: "0:42", "12:34", "1:02:03"."""
    s = max(0, int(seconds))
    h, rest = divmod(s, 3600)
    m, sec = divmod(rest, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def day_of(ts: float, now: float | None = None) -> str:
    """"today", "yesterday", "week" (within the last seven days),
    "earlier", or "later" (a day still to come) - by the local calendar, as
    the list groups them."""
    import datetime
    now = time.time() if now is None else now
    try:
        d = datetime.date.fromtimestamp(float(ts))
    except (TypeError, ValueError, OSError, OverflowError):
        return "earlier"
    delta = (datetime.date.fromtimestamp(now) - d).days
    if delta < 0:
        return "later"
    if delta == 0:
        return "today"
    if delta == 1:
        return "yesterday"
    return "week" if delta < 7 else "earlier"


_STAMP = re.compile(r"\s+\d{4}-\d{2}-\d{2} \d{2}\.\d{2}(?: \(\d+\))?$")


def shown_title(name: str) -> str:
    """A recording's name without the date and time its file was named
    with ("Evening News 2026-11-17 21.00" -> "Evening News"): its card says
    when on its line."""
    name = str(name or "")
    short = _STAMP.sub("", name).strip()
    return short or name


def _num(v: Any) -> float:
    try:
        return max(0.0, float(v or 0))
    except (TypeError, ValueError):
        return 0.0


def length_of(probed: float = 0.0, played: float = 0.0, job: dict | None = None) -> float:
    """A recording's length in seconds: what ffmpeg said of the file, else
    the length the player found, else what its job ran for (a programme's
    own times first); 0 when nothing says."""
    for v in (probed, played):
        if _num(v) > 0:
            return _num(v)
    if job:
        for a, b in (("prog_start", "prog_stop"), ("start", "stop")):
            start, stop = _num(job.get(a)), _num(job.get(b))
            if start and stop > start:
                return stop - start
    return 0.0


# -- a frame and the length, off the window's thread ----------------------------------

def frame_path(thumbs_dir: str, path: str) -> str:
    """Where a recording's frame is kept: Local files' name for the same
    file, so one grabbed there serves here too."""
    return os.path.join(thumbs_dir, hashlib.sha1(path.encode("utf-8")).hexdigest() + ".jpg")


def length_path(thumbs_dir: str, path: str) -> str:
    return os.path.splitext(frame_path(thumbs_dir, path))[0] + ".len"


def seek_for(length: float) -> float:
    """Where its frame is taken: a tenth of the way in (past the padding
    and a channel's trailers), at least a few seconds in, never past the
    middle; two minutes in when its length is not known."""
    if length <= 0:
        return 120.0
    return round(min(length / 2, max(3.0, length * 0.1)), 1)


@dataclass(frozen=True)
class Probe:
    """What was found of a recording: its frame's file ("" for none) and
    its length in seconds (0: not known)."""
    frame: str = ""
    length: float = 0.0


Runner = Callable[[list[str], float], tuple[int, str]]


def _run(argv: list[str], timeout: float) -> tuple[int, str]:
    try:
        done = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, timeout=timeout, check=False,
                              creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return -1, ""
    return done.returncode, done.stderr.decode("utf-8", "replace")


def _read_length(p: str) -> float:
    try:
        with open(p, encoding="utf-8") as fh:
            return _num(fh.read().strip())
    except (OSError, ValueError):
        return 0.0


def _write_length(p: str, length: float) -> None:
    try:
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(f"{length:.1f}")
    except OSError:
        pass


def probe(ffmpeg: str, thumbs_dir: str, path: str, run: Runner = _run) -> Probe:
    """A recording's frame and length (on a worker): kept ones read back,
    else ffmpeg asked what the file holds and one frame taken from a tenth
    of the way in (a short one from its start), both kept for next time."""
    os.makedirs(thumbs_dir, exist_ok=True)
    fp, lp = frame_path(thumbs_dir, path), length_path(thumbs_dir, path)
    have = os.path.isfile(fp) and os.path.getsize(fp) > 0
    length = _read_length(lp)
    if have and length > 0:
        return Probe(fp, length)
    if length <= 0 and ffmpeg:
        _rc, text = run([ffmpeg, "-hide_banner", "-nostdin", "-i", path], PROBE_TIMEOUT_S)
        length = _num(parse_probe(text).duration)
        if length > 0:
            _write_length(lp, length)
    if not have and ffmpeg:
        for ss in (seek_for(length), 1.0):
            run([ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-ss", f"{ss:g}", "-i", path,
                 "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", "-y", fp],
                PROBE_TIMEOUT_S)
            # A seek past a short one's end makes no frame at all.
            if os.path.isfile(fp) and os.path.getsize(fp) > 0:
                have = True
                break
    return Probe(fp if have else "", length)


# -- watched marks ------------------------------------------------------------------

class WatchedMarks:
    """The recordings watched to their end (or marked so), by file: the
    profile's own ``rec_watched`` - {path: when} - read once and written as
    it changes."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._marks: dict[str, int] | None = None

    def _all(self) -> dict[str, int]:
        if self._marks is None:
            self._marks = parse(self._settings.value(WATCHED_KEY, ""))
        return self._marks

    def reload(self) -> None:
        """Read again (another profile in use)."""
        self._marks = None

    def has(self, path: str) -> bool:
        return bool(path) and path in self._all()

    def mark(self, path: str, when: float | None = None) -> None:
        if not path:
            return
        marks = self._all()
        marks[path] = int(time.time() if when is None else when)
        self._save(bounded(marks))

    def unmark(self, path: str) -> None:
        marks = self._all()
        if marks.pop(path, None) is not None:
            self._save(marks)

    def moved(self, old: str, new: str) -> None:
        """A recording renamed or moved: its mark goes with it."""
        marks = self._all()
        when = marks.pop(old, None)
        if when is not None:
            marks[new] = when
            self._save(marks)

    def _save(self, marks: dict[str, int]) -> None:
        self._marks = marks
        self._settings.setValue(WATCHED_KEY, json.dumps(marks, sort_keys=True))


def parse(raw: Any) -> dict[str, int]:
    """The marks as kept, anything malformed left out."""
    try:
        data = json.loads(raw) if isinstance(raw, str) and raw else raw
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, int] = {}
    for k, v in data.items():
        try:
            out[str(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return out


def bounded(marks: dict[str, int], cap: int = WATCHED_MAX) -> dict[str, int]:
    """At most *cap* marks, the newest kept."""
    if len(marks) <= cap:
        return marks
    keep = sorted(marks.items(), key=lambda kv: kv[1], reverse=True)[:cap]
    return dict(keep)
