"""Recordings saved as MP4 (Settings > Recording, off by default).

A recording is written as MPEG-TS, the only container that survives a
connection dropped halfway. Once it has ended well, the app's own ffmpeg
copies it into an MP4 - stream copy, nothing re-encoded, so it takes as
long as reading the file once - with its index at the front (faststart),
so it opens at once in any player and on a TV. Picture and sound go in;
the subtitles, teletext and data a channel sends cannot go in an MP4 and
stay only in a .ts.

The .ts is deleted only once the MP4 is plausibly whole: ffmpeg ended
well, the MP4 has the same kinds of streams (video, audio), it lasts as
long as the .ts within a little (when ffmpeg can tell the .ts's length),
and it is not absurdly small beside it. Anything else - ffmpeg missing or
failing, a codec MP4 cannot hold (DVB's AAC-LATM), too little room on the
disk, the .ts gone meanwhile - keeps the .ts as it is and leaves no half
MP4 behind. The MP4 is written under a name the recordings list does not
show (``.mp4.part``) and renamed only when it passed; the title and
description beside it (``.info.json``) move with it.

A remux the app's closing cut short, or that was due as it closed, is
kept in a small journal (``JOURNAL``) and done at the next start - as is
the command after the recording, which gets the final file.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .log import log

#: The setting: "true" saves recordings as MP4.
SETTING = "rec_save_mp4"
#: Remuxes still to do (the app closed first): JSON list of entries.
JOURNAL = "rec_mp4_pending"
#: The MP4 while it is written; renamed once it passed.
PART = ".mp4.part"
#: The title/description file beside a recording (RecordingManager).
INFO = ".info.json"
#: The MP4 may last this much less than the .ts: what ffmpeg reads of a
#: .ts's length is an estimate from its first and last timestamps.
DURATION_RATIO = 0.97
DURATION_SLACK_S = 3.0
#: An MP4 smaller than this share of the .ts is not the whole of it. MPEG-TS
#: carries 5-25% of packet headers and padding, so a whole MP4 is well over.
MIN_SIZE_RATIO = 0.3
#: Room left on the disk after the MP4 is written, so a recording still
#: going in the same folder is never the one that runs out.
SPARE_BYTES = 512 * 1024 * 1024
#: How long a remux may take: a minute, plus a second per 5 MB.
BASE_TIMEOUT_S = 60.0
BYTES_PER_S = 5 * 1024 * 1024
PROBE_TIMEOUT_S = 60.0

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

_DURATION = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_STREAM = re.compile(r"^\s*Stream #\d+:\d+.*?:\s*(Video|Audio|Subtitle|Data):\s*(\w+)", re.M)


class Readable(Protocol):
    def value(self, key: str, default: Any = None) -> Any: ...


class Settings(Readable, Protocol):
    def setValue(self, key: str, value: Any) -> None: ...  # noqa: N802 - Qt's name


def enabled(settings: Readable | None) -> bool:
    """Whether recordings are saved as MP4 (off unless the user said so)."""
    if settings is None:
        return False
    try:
        return str(settings.value(SETTING, "false")).lower() == "true"
    except Exception:
        return False


def wanted(path: str) -> bool:
    """Whether *path* is a recording to remux: an MPEG-TS file."""
    return str(path or "").lower().endswith(".ts")


def part_path(src: str) -> str:
    """Where the MP4 for *src* is written until it passed."""
    return os.path.splitext(src)[0] + PART


def target(src: str, exists: Callable[[str], bool] = os.path.exists) -> str:
    """The MP4's name: the recording's own with .mp4 - its name template's
    folders and all - or "name (2).mp4"... when another file has it."""
    stem = os.path.splitext(src)[0]
    path, n = stem + ".mp4", 2
    while exists(path):
        path = f"{stem} ({n}).mp4"
        n += 1
    return path


def command(ffmpeg: str, src: str, out: str, hevc: bool = False) -> list[str]:
    """ffmpeg's remux of *src* into *out*: every video and audio stream
    copied as it is, timestamps made where the stream lacks them, the index
    at the front. HEVC tagged hvc1, the tag Apple's players need."""
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
           "-fflags", "+genpts", "-i", src,
           "-map", "0:v?", "-map", "0:a?", "-c", "copy"]
    if hevc:
        cmd += ["-tag:v", "hvc1"]
    cmd += ["-max_muxing_queue_size", "9999", "-movflags", "+faststart",
            "-f", "mp4", out]
    return cmd


def probe_command(ffmpeg: str, path: str) -> list[str]:
    """ffmpeg asked what *path* holds (its length and streams, on stderr;
    it exits with an error for having no output, which is expected)."""
    return [ffmpeg, "-hide_banner", "-nostdin", "-i", path]


@dataclass(frozen=True)
class Probe:
    """What ffmpeg said a file holds."""
    duration: float | None = None
    kinds: frozenset[str] = field(default_factory=frozenset)
    hevc: bool = False


def parse_probe(text: str) -> Probe:
    """ffmpeg's description of a file: its length (None when it gave none,
    "N/A") and the kinds of streams in it."""
    m = _DURATION.search(text or "")
    duration = (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
                if m else None)
    found = _STREAM.findall(text or "")
    kinds = frozenset(kind.lower() for kind, _codec in found)
    hevc = any(kind == "Video" and codec.lower() in ("hevc", "h265")
               for kind, codec in found)
    return Probe(duration, kinds, hevc)


def plausible(src_size: int, src: Probe, out_size: int, out: Probe) -> str:
    """"" when the MP4 is plausibly the whole recording, else why not."""
    want = src.kinds & {"video", "audio"}
    if not want:
        return "the recording has neither picture nor sound ffmpeg can read"
    if not want <= out.kinds:
        return "the MP4 lacks the " + " and ".join(sorted(want - out.kinds))
    if out.duration is None or out.duration <= 0:
        return "the MP4 has no length"
    if (src.duration is not None and src.duration > 0
            and out.duration < src.duration * DURATION_RATIO - DURATION_SLACK_S):
        return (f"the MP4 lasts {out.duration:.0f} s, the recording "
                f"{src.duration:.0f} s")
    if out_size < src_size * MIN_SIZE_RATIO:
        return f"the MP4 is {out_size} bytes of {src_size}"
    return ""


@dataclass(frozen=True)
class Result:
    """How a remux went: *path* is the file the recording is now (the MP4,
    or the .ts kept), *why* empty when it is the MP4."""
    ok: bool
    path: str
    why: str = ""


#: (argv, timeout) -> (exit code, what it said on stderr).
Runner = Callable[[list[str], float], tuple[int, str]]

_procs: set[subprocess.Popen] = set()
_lock = threading.Lock()


def _run(argv: list[str], timeout: float) -> tuple[int, str]:
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, creationflags=NO_WINDOW)
    with _lock:
        _procs.add(proc)
    try:
        _out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return -1, "timed out"
    finally:
        with _lock:
            _procs.discard(proc)
    return proc.returncode, (err or b"").decode("utf-8", "replace")


def stop_all() -> int:
    """The app closes: every remux running is ended (each keeps its .ts and
    is done again at the next start). How many there were."""
    with _lock:
        procs = list(_procs)
    for p in procs:
        try:
            p.terminate()
        except OSError:
            pass
    return len(procs)


def _drop(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as e:
        log.warning("recording: could not remove %s: %s", os.path.basename(path), e)


def _tail(text: str, n: int = 300) -> str:
    text = " ".join((text or "").split())
    return text[-n:]


def remux(src: str, ffmpeg: str, run: Runner = _run,
          free: Callable[[str], int] | None = None) -> Result:
    """*src* into an MP4 beside it, and *src* deleted once the MP4 passed;
    *src* kept, and nothing left of the MP4, otherwise. Never raises for a
    file or ffmpeg that fails: the Result says why."""
    try:
        size = os.path.getsize(src)
    except OSError as e:
        return Result(False, src, f"the recording is not there: {e}")
    part = part_path(src)
    room = free(os.path.dirname(src) or ".") if free is not None else \
        shutil.disk_usage(os.path.dirname(src) or ".").free
    if room < size * 1.05 + SPARE_BYTES:
        return Result(False, src, "too little room on the disk for the MP4")
    _code, said = run(probe_command(ffmpeg, src), PROBE_TIMEOUT_S)
    before = parse_probe(said)
    _drop(part)
    code, said = run(command(ffmpeg, src, part, before.hevc),
                     BASE_TIMEOUT_S + size / BYTES_PER_S)
    if code != 0:
        _drop(part)
        return Result(False, src, f"ffmpeg ended with {code}: {_tail(said)}".rstrip(": "))
    try:
        out_size = os.path.getsize(part)
    except OSError:
        return Result(False, src, "ffmpeg wrote no MP4")
    _code, said = run(probe_command(ffmpeg, part), PROBE_TIMEOUT_S)
    why = plausible(size, before, out_size, parse_probe(said))
    if why:
        _drop(part)
        return Result(False, src, why)
    if not os.path.exists(src):
        # Deleted or moved while it was copied: the MP4 is of nothing kept.
        _drop(part)
        return Result(False, src, "the recording was moved or deleted meanwhile")
    final = target(src)
    try:
        os.replace(part, final)
    except OSError as e:
        _drop(part)
        return Result(False, src, f"the MP4 could not be named: {e}")
    if os.path.exists(src + INFO):
        try:
            os.replace(src + INFO, final + INFO)
        except OSError as e:
            log.warning("recording: its info stays with the .ts: %s", e)
    try:
        os.remove(src)
    except OSError as e:
        # Open elsewhere (Windows): both are kept rather than the MP4 lost.
        log.warning("recording: saved as MP4, the .ts could not be removed: %s", e)
    return Result(True, final)


class Journal:
    """The remuxes still to do, kept in the settings: each recording's
    .ts, its title and channel (for the command after it)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def items(self) -> list[dict]:
        try:
            data = json.loads(self._settings.value(JOURNAL, "") or "[]")
        except (TypeError, ValueError):
            data = []
        return [e for e in (data if isinstance(data, list) else [])
                if isinstance(e, dict) and isinstance(e.get("ts"), str) and e["ts"]]

    def get(self, ts: str) -> dict | None:
        return next((e for e in self.items() if e["ts"] == ts), None)

    def add(self, ts: str, title: str = "", channel: str = "") -> None:
        items = [e for e in self.items() if e["ts"] != ts]
        items.append({"ts": ts, "title": title or "", "channel": channel or ""})
        self._save(items)

    def done(self, ts: str) -> None:
        items = self.items()
        keep = [e for e in items if e["ts"] != ts]
        if len(keep) != len(items):
            self._save(keep)

    def _save(self, items: list[dict]) -> None:
        self._settings.setValue(JOURNAL, json.dumps(items) if items else "")
