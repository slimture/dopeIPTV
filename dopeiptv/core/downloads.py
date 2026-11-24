"""Downloads: films and episodes kept on this computer, to watch without the
network. No Qt.

**The queue.** One job at a time - one connection to the provider, as the
account allows. A job is a film or an episode of a source, kept by the
source's id, the title's stream id and its kind - never by its address: an
Xtream address holds the login, and nothing with a login is written to disk
outside the settings files. The address is asked of the source's client as
the job starts (``Downloads.resolve``, the window's: a recording asks for
its address so too), off the window's thread.

**The bytes.** Fetched through the app's own request path with its guards -
core/dvr_ingest.open_for: the source's route, user agent, headers and
proxy as the player has them, a login in the address answered as the
server asks, every connection and redirect judged by core/dest_guard.py,
nothing sent to dopeiptv.org that holds a login (core/site_out.py). HTTP
Range carries on a download that was cut; a server that ignores it is read
from the start again (``MAX_RESTARTS`` times at most, then it is said). The
bytes go to ``<name>.part``, renamed only once whole (Content-Length matched
where it was given). An answer that is no video - a web page, a few
kilobytes of an error - is refused as the rewind buffer refuses one
(dvr_ingest.not_a_stream). An HLS film (an .m3u8) is read by ffmpeg through
the app's bridge under an alias, exactly as server mode copies an HLS
channel (core/restream.ffmpeg_opener: no address, login or token header in
its arguments, ffmpeg kept to the bridge's protocols), and saved as MP4 the
way a recording is (core/rec_mp4.remux); it cannot be carried on where it
was cut - a pause starts it again.

**The files.** In the downloads folder (``DIR_KEY``; by default a folder in
the person's own videos folder, ``default_folder``): films under Movies,
episodes under Series/<the series>, each name made from the title through
recording's safe_filename, every folder on the way checked as recording's
_stays_inside does (no link that leads out of the downloads folder). Beside
each file a sidecar (``<file>.dl.json``) with what offline mode needs and
nothing else: the kind, the source's id and the name the person gave it,
the stream id and the playback key, the title, year, plot, length and
category, the poster as a file of its own beside it (its name, never its
address), and for an episode its series (id, title, plot, category, its
poster's file), season, number and title. No address, no login, no account
(``SIDECAR_FIELDS``). What is downloaded is read back from the sidecars
(``scan``): the disk is the index.

**Space.** The downloads together stay within the person's limit
(``CAP_KEY``, 50 GB by default, 0 for none), and never leave less than
recording's DISK_FLOOR free on their disk: a job past either stops and
says so (its .part kept, Try again carries it on once there is room).

**Privacy and the account.** No job starts, and none runs on, while private
viewing is on (it waits, and carries on after). The window says per job
whether it may run now (``holds``): a download gives way to everything else
of the app's on its account - a known limit with no room, or none known
while something else of the app's uses it - and goes on once there is room
(core/connections.py counts the rest). Titles go to the log through
privacy.for_log. The queue and the folder are this computer's: never
synced (core/sync.py), never in a backup (core/backup.py).

``Downloads`` is the state; its worker thread moves the bytes, the window's
thread reads ``jobs``/``items``, writes the settings (``save``) and decides
the holds. ``as_row`` and ``series_row`` give a downloaded title as the
lists' row of it - what offline mode will show.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from typing import Any, Protocol
from urllib.parse import urlsplit

from . import privacy
from .log import log
from .privacy import for_log

KINDS = ("movie", "episode")

#: The settings - the household's, this computer's own (core/sync.py and
#: core/backup.py leave every "downloads_" key out).
QUEUE_KEY = "downloads_queue"
DIR_KEY = "downloads_dir"
CAP_KEY = "downloads_cap_gb"
DEFAULT_CAP_GB = 50
GB = 10 ** 9
#: The most jobs the queue keeps (a whole series is a few hundred).
QUEUE_MAX = 2000

SIDECAR = ".dl.json"
SIDECAR_MAX = 64 * 1024
FORMAT = 1
PART = ".part"
POSTER = ".poster"
SERIES_POSTER = "series.poster"
MOVIES_DIR, SERIES_DIR = "Movies", "Series"
VIDEO_EXTS = frozenset({"mp4", "mkv", "m4v", "avi", "mov", "webm", "ts", "m2ts", "mpg",
                        "mpeg", "wmv", "flv", "ogv", "3gp"})
PICTURE_EXTS = ("jpg", "png", "webp", "gif")

#: A job's states. Waiting (its hold - the window's word - says why it does
#: not run now, if it does not), running, paused by the person, failed (its
#: why says why, its .part kept for Try again).
WAITING, RUNNING, PAUSED, FAILED = "waiting", "running", "paused", "failed"
STATES = (WAITING, RUNNING, PAUSED, FAILED)

#: Why a job failed - the window says each in the person's words.
WHY_SOURCE = "source"           # its source is not there any more
WHY_ADDRESS = "address"         # the source gave no address for it
WHY_SCHEME = "scheme"           # an address the app does not download
WHY_NOT_VIDEO = "not_video"     # a page or a message (detail: what), no video
WHY_STATUS = "status"           # the server said no (detail: its code)
WHY_DROPS = "drops"             # the connection kept dropping
WHY_RESUME = "resume"           # cut too often, and the server cannot carry on
WHY_CAP = "cap"                 # past the downloads' space limit
WHY_DISK = "disk"               # the disk is nearly full
WHY_FOLDER = "folder"           # the folder cannot be made, or leads elsewhere
WHY_FFMPEG = "ffmpeg"           # an HLS film and no ffmpeg
WHY_CONVERT = "convert"         # ffmpeg could not read the HLS film
WHY_PRIVATE_WAY = "private_way"  # no way to hand ffmpeg the address privately
WHY_CERT = "certificate"        # its certificate does not check out
WHY_REFUSED = "refused"         # it led where the app does not fetch from
WHY_OTHER = "other"
#: In plain English, for the log (the window has its own words).
WHY_WORDS = {
    WHY_SOURCE: "its source is not there", WHY_ADDRESS: "the source gave no address",
    WHY_SCHEME: "an address the app does not download", WHY_NOT_VIDEO: "no video",
    WHY_STATUS: "the server said no", WHY_DROPS: "the connection kept dropping",
    WHY_RESUME: "the server cannot carry on a download that was cut",
    WHY_CAP: "past the downloads' space limit", WHY_DISK: "the disk is nearly full",
    WHY_FOLDER: "the downloads folder cannot be written", WHY_FFMPEG: "no ffmpeg",
    WHY_CONVERT: "ffmpeg could not read it",
    WHY_PRIVATE_WAY: "no way to hand ffmpeg its address privately",
    WHY_CERT: "its certificate does not check out",
    WHY_REFUSED: "it led where the app does not fetch from", WHY_OTHER: "it failed"}

USER_AGENT = "dopeIPTV/1.0"     # what the player and the rewind buffer send
CHUNK = 256 * 1024
#: Connect, and silence while reading.
TIMEOUT = (15.0, 60.0)
#: Tries in a row that brought nothing before a job is given up.
MAX_TRIES = 6
BACKOFF = 1.0
#: Connections one job may open in all before it is given up (each cut
#: that brought bytes is carried on at once).
MAX_CONNECTIONS = 500
#: A server that ignores Range is read from the start again this often.
MAX_RESTARTS = 3
#: How often (bytes) the space left is looked at while a job writes.
SPACE_EVERY = 16 * 1024 * 1024
#: A connection's first bytes, looked at before they are kept.
SNIFF = 1024
#: The folder is read this deep and this far at most.
SCAN_DEPTH = 4
SCAN_MAX = 50_000
#: An HLS film's ffmpeg: how long its end may take once its output ended.
HLS_END_S = 30.0

#: What a sidecar keeps beside the fields every one has, and of what type.
_TEXT = 4000
SIDECAR_FIELDS: dict[str, type | tuple[type, ...]] = {
    "year": str, "plot": str, "length": (int, float), "category_id": str, "rating": (int, float),
    "genre": str, "series_id": str, "series_title": str, "series_plot": str,
    "series_category_id": str, "series_year": str, "season": (int, str),
    "episode": (int, str), "episode_title": str, "source_name": str,
    # The category's name as the provider gives it: a kids profile's lists
    # judge it offline (core/profiles.adult_category); without it, a title
    # is of no category a kids profile may see (providers/downloaded.py).
    "category_name": str, "series_category_name": str,
}
_URL = re.compile(r"\S+://\S*")


class Settings(Protocol):
    def value(self, key: str, default: Any = None) -> Any: ...

    def setValue(self, key: str, value: Any) -> None: ...  # noqa: N802 - Qt's name


class Unavailable(Exception):
    """resolve's word that the job's source is not there."""


# -- the folder ------------------------------------------------------------------------

def _xdg_videos(home: str) -> str:
    """Linux: the person's own videos folder as their desktop names it
    (user-dirs.dirs' XDG_VIDEOS_DIR), "" when none is named."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    try:
        with open(os.path.join(base, "user-dirs.dirs"), encoding="utf-8") as fh:
            text = fh.read(16 * 1024)
    except OSError:
        return ""
    m = re.search(r'^\s*XDG_VIDEOS_DIR\s*=\s*"([^"\n]*)"', text, re.M)
    if not m:
        return ""
    path = m.group(1).replace("$HOME", home)
    if not os.path.isabs(path) or os.path.normpath(path) == os.path.normpath(home):
        return ""
    return path


def default_folder(platform: str = sys.platform) -> str:
    """Where downloads go unless the person chose a folder: one of the
    app's own in the platform's usual videos folder - ~/Movies on a Mac,
    the desktop's videos folder on Linux, ~/Videos elsewhere. Beside the
    recordings' folder (~/Videos/dopeIPTV), never inside it."""
    home = os.path.expanduser("~")
    if platform == "darwin":
        base = os.path.join(home, "Movies")
    elif platform.startswith("linux"):
        base = _xdg_videos(home) or os.path.join(home, "Videos")
    else:
        base = os.path.join(home, "Videos")
    return os.path.join(base, "dopeIPTV Downloads")


def folder_of(settings: Settings | None) -> str:
    d = str(settings.value(DIR_KEY, "") or "") if settings is not None else ""
    return d or default_folder()


def cap_gb(settings: Settings | None) -> float:
    """The downloads' space limit in GB (0: none)."""
    try:
        v = float(settings.value(CAP_KEY, DEFAULT_CAP_GB) if settings is not None
                  else DEFAULT_CAP_GB)
    except (TypeError, ValueError):
        return float(DEFAULT_CAP_GB)
    return v if v >= 0 and v == v and v < 1e9 else float(DEFAULT_CAP_GB)


def _floor() -> int:
    from .recording import DISK_FLOOR
    return int(DISK_FLOOR)


def _safe_name(name: str) -> str:
    from .recording import safe_filename
    return safe_filename(name)


def _stays_inside(root: str, folder: str) -> bool:
    from .recording import RecordingManager
    return bool(RecordingManager._stays_inside(root, folder))


def _inside(root: str, path: str) -> bool:
    try:
        top = os.path.realpath(root)
        return os.path.commonpath([top, os.path.realpath(path)]) == top
    except (ValueError, OSError):
        return False


def _nofollow() -> int:
    return getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)


def _write_file(path: str, data: bytes) -> None:
    """*data* into *path*, never through a link put there."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _nofollow(), 0o644)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


def _size(path: str) -> int:
    try:
        st = os.lstat(path)
    except OSError:
        return 0
    import stat
    return st.st_size if stat.S_ISREG(st.st_mode) else 0


def _drop(path: str) -> None:
    if not path:
        return
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as e:
        log.warning("downloads: could not remove %s: %s", for_log(os.path.basename(path)), e)


# -- what a sidecar keeps ----------------------------------------------------------------

def _clean_text(value: Any, limit: int = _TEXT) -> str:
    text = _URL.sub(" ", str(value)) if value is not None else ""
    return " ".join(text.split())[:limit]


def clean_meta(meta: dict | None) -> dict:
    """The fields of *meta* a sidecar may keep (``SIDECAR_FIELDS``), each of
    its type and bounded - an address in a text left out of it. Nothing
    else: no address, no login, no account."""
    out: dict[str, Any] = {}
    for k, v in (meta or {}).items():
        want = SIDECAR_FIELDS.get(str(k))
        if want is None or v is None or isinstance(v, bool):
            continue
        types = want if isinstance(want, tuple) else (want,)
        number = int in types or float in types
        if isinstance(v, (int, float)):
            if number:
                if v == v and abs(v) < 1e12:
                    out[str(k)] = v
            elif str in types:
                out[str(k)] = str(v)
            continue
        if not isinstance(v, str):
            continue
        if str in types:
            text = _clean_text(v)
            if text:
                out[str(k)] = text
        elif number:
            # A number the provider wrote as text ("5400", "7.4").
            try:
                n = float(v.strip())
            except ValueError:
                continue
            if n == n and abs(n) < 1e12:
                out[str(k)] = int(n) if n.is_integer() else n
    return out


def _file_name(name: Any) -> str:
    """A file's own name as a sidecar may give it: a name alone, beside the
    sidecar - never a path, a hidden file or one going up."""
    text = str(name or "")
    if (not text or text != os.path.basename(text) or "/" in text or "\\" in text
            or text.startswith(".") or "\x00" in text or len(text) > 255):
        return ""
    return text


def _picture_ext(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return ""


def _video_ext(head: bytes, given: str, url: str) -> str:
    """The extension a finished file gets: what its first bytes are, else
    the source's word for it, else its address's, else mkv."""
    if head[4:8] == b"ftyp":
        return "mp4"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "webm" if b"webm" in head[:64] else "mkv"
    if head[:1] == b"\x47" and len(head) > 188 and head[188:189] == b"\x47":
        return "ts"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "avi"
    if head[:3] == b"FLV":
        return "flv"
    ext = str(given or "").lower().lstrip(".")
    if ext in VIDEO_EXTS:
        return ext
    try:
        tail = os.path.splitext(urlsplit(url).path)[1].lower().lstrip(".")
    except ValueError:
        tail = ""
    return tail if tail in VIDEO_EXTS else "mkv"


# -- a job, a downloaded file -------------------------------------------------------------

def _whole(value: Any) -> int | None:
    """A season's or an episode's number as a whole number, None for none."""
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if 0 <= n < 100000 else None


def _ident(source: Any, kind: Any, stream_id: Any) -> tuple[str, str, str]:
    return (str(source or ""), str(kind or ""), str(stream_id if stream_id is not None else ""))


@dataclass
class Job:
    """One film or episode to download: kept by its source, kind and stream
    id (never its address); *key* its playback key (resume points, marks);
    *meta* what its sidecar will keep (clean_meta); *poster* and
    *series_poster* their pictures' addresses, kept in the settings only."""
    id: str
    kind: str
    source: str
    stream_id: str
    key: str
    title: str
    ext: str = ""
    meta: dict = field(default_factory=dict)
    poster: str = ""
    series_poster: str = ""
    state: str = WAITING
    why: str = ""
    detail: str = ""
    done: int = 0
    total: int = 0
    part: str = ""
    added: float = 0.0
    #: This session only: the person said it may run beside what else of
    #: the app's uses the account (one more connection, asked first).
    go_ahead: bool = field(default=False, compare=False)

    @property
    def ident(self) -> tuple[str, str, str]:
        return _ident(self.source, self.kind, self.stream_id)

    @property
    def progress(self) -> float | None:
        if self.total <= 0:
            return None
        return max(0.0, min(1.0, self.done / self.total))

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "source": self.source,
                "stream_id": self.stream_id, "key": self.key, "title": self.title,
                "ext": self.ext, "meta": dict(self.meta), "poster": self.poster,
                "series_poster": self.series_poster, "state": self.state, "why": self.why,
                "detail": self.detail, "done": int(self.done), "total": int(self.total),
                "part": self.part, "added": float(self.added)}

    @classmethod
    def from_dict(cls, d: Any) -> Job | None:
        """A job as the settings kept it; None for one that is not one."""
        if not isinstance(d, dict) or d.get("kind") not in KINDS:
            return None
        src, sid = d.get("source"), d.get("stream_id")
        if not isinstance(src, str) or not src or not isinstance(sid, str) or not sid:
            return None
        state = str(d.get("state")) if d.get("state") in STATES else WAITING
        if state == RUNNING:
            state = WAITING             # cut by the app's end: carried on

        def num(k: str) -> int:
            v = d.get(k)
            return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) \
                and 0 <= v < 1e15 else 0

        def text(k: str, limit: int = 2048) -> str:
            v = d.get(k)
            return v[:limit] if isinstance(v, str) else ""

        try:
            added = float(d.get("added") or 0)
        except (TypeError, ValueError):
            added = 0.0
        return cls(id=text("id", 40) or uuid.uuid4().hex[:12], kind=d["kind"], source=src[:200],
                   stream_id=sid[:200], key=text("key", 200) or sid[:200],
                   title=text("title", 500), ext=text("ext", 10),
                   meta=clean_meta(d.get("meta") if isinstance(d.get("meta"), dict) else {}),
                   poster=text("poster", 4096), series_poster=text("series_poster", 4096),
                   state=state, why=text("why", 40), detail=text("detail", 200),
                   done=num("done"), total=num("total"), part=text("part", 4096), added=added)


@dataclass(frozen=True)
class Item:
    """A downloaded title: its file, its size and its sidecar (*info*)."""
    path: str
    size: int
    info: dict = field(compare=False)

    @property
    def ident(self) -> tuple[str, str, str]:
        return _ident(self.info.get("source"), self.info.get("kind"), self.info.get("stream_id"))

    @property
    def kind(self) -> str:
        return str(self.info.get("kind") or "")

    @property
    def source(self) -> str:
        return str(self.info.get("source") or "")

    @property
    def stream_id(self) -> str:
        return str(self.info.get("stream_id") or "")

    @property
    def key(self) -> str:
        return str(self.info.get("key") or self.stream_id)

    @property
    def title(self) -> str:
        return str(self.info.get("title") or os.path.splitext(os.path.basename(self.path))[0])

    @property
    def added(self) -> float:
        v = self.info.get("added")
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0

    def _beside(self, key: str) -> str:
        name = _file_name(self.info.get(key))
        return os.path.join(os.path.dirname(self.path), name) if name else ""

    @property
    def poster(self) -> str:
        """Its poster's file, "" for none."""
        return self._beside("poster")

    @property
    def series_poster(self) -> str:
        return self._beside("series_poster")

    @property
    def series_id(self) -> str:
        return str(self.info.get("series_id") or "")


def picture_address(path: str) -> str:
    """A poster file as a row's picture address (core/picture_files reads a
    file's path, or a file: address on a system whose paths start with a
    drive)."""
    if not path:
        return ""
    return path if path.startswith("/") else "file://" + path


def as_row(item: Item) -> dict:
    """A downloaded title as the lists' row of it - its film's or its
    episode's, with its file (``_dl_path``) and its poster's file as its
    picture: what Movies, a series' page and Mine show of it with no
    network."""
    info = item.info
    pic = picture_address(item.poster) or picture_address(item.series_poster)
    if item.kind == "movie":
        row: dict[str, Any] = {"name": item.title, "stream_id": item.stream_id,
                               "stream_icon": pic, "cover": pic, "_kind": "vod"}
        for k in ("year", "plot", "rating", "genre"):
            if info.get(k) not in (None, ""):
                row[k] = info[k]
    else:
        row = {"id": item.stream_id, "title": str(info.get("episode_title") or item.title),
               "name": item.title, "season": info.get("season"),
               "episode_num": info.get("episode"), "stream_icon": pic, "_kind": "episode",
               "info": {k2: v for k2, v in (("plot", info.get("plot")),
                                             ("duration_secs", info.get("length")))
                        if v not in (None, "")},
               "_series_ctx": series_row(item)}
    if info.get("category_id") not in (None, ""):
        row["category_id"] = info["category_id"]
    if isinstance(info.get("length"), (int, float)) and info["length"]:
        row["_dl_length"] = info["length"]
    row["_dl_path"] = item.path
    row["_dl_size"] = item.size
    row["_dl_source"] = item.source
    return row


def series_row(item: Item) -> dict:
    """An episode's series as the lists' row of it (a series' context)."""
    info = item.info
    pic = picture_address(item.series_poster) or picture_address(item.poster)
    row: dict[str, Any] = {"series_id": item.series_id,
                           "name": str(info.get("series_title") or ""),
                           "cover": pic, "stream_icon": pic}
    if info.get("series_plot"):
        row["plot"] = info["series_plot"]
    if info.get("series_category_id") not in (None, ""):
        row["category_id"] = info["series_category_id"]
    return row


def read_sidecar(path: str) -> dict | None:
    """A sidecar's fields, None for a file that is not one (too large, not
    JSON, of no kind or source this app writes)."""
    try:
        fd = os.open(path, os.O_RDONLY | _nofollow())
    except OSError:
        return None
    try:
        with os.fdopen(fd, "rb") as fh:
            raw = fh.read(SIDECAR_MAX + 1)
    except OSError:
        return None
    if len(raw) > SIDECAR_MAX:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    if not isinstance(data, dict) or data.get("kind") not in KINDS:
        return None
    for k in ("source", "stream_id"):
        if not isinstance(data.get(k), str) or not data[k]:
            return None
    info = clean_meta(data)
    for k in ("kind", "source", "stream_id", "key", "title", "file", "poster", "series_poster"):
        if isinstance(data.get(k), str):
            info[k] = data[k][:500]
    for k in ("added", "size", "format"):
        v = data.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            info[k] = v
    return info


# -- a series' episodes, known without asking -----------------------------------------

#: The most one series' list keeps, and the most its file may be.
EPISODES_MAX = 3000
EPISODES_FILE_MAX = 512 * 1024


def episodes_file(folder: str, source: Any, series_id: Any) -> str:
    """Where the episodes of a series are kept in *folder* (the app's cache):
    named by a hash of its source and id, never by either as it came."""
    import hashlib
    key = f"{source}\x1f{series_id}".encode("utf-8", "replace")
    return os.path.join(folder, hashlib.sha256(key).hexdigest()[:32] + ".json")


def _episode_row(row: Any) -> dict | None:
    if not isinstance(row, dict):
        return None
    eid = row.get("id")
    if eid in (None, "") or isinstance(eid, bool) or not isinstance(eid, (int, str)):
        return None
    eid = str(eid).strip()[:200]
    if not eid:
        return None
    out: dict[str, Any] = {"id": eid}
    season = _whole(row.get("season"))
    number = _whole(row.get("episode_num") if "episode_num" in row else row.get("episode"))
    if season is not None:
        out["season"] = season
    if number is not None:
        out["episode"] = number
    title = _clean_text(row.get("title"), 300) if isinstance(row.get("title"), str) else ""
    if title:
        out["title"] = title
    return out


def keep_episodes(folder: str, source: Any, series_id: Any, rows: Iterable[Any]) -> bool:
    """The episodes of a series (the lists' rows of them: id, season,
    number, title - nothing else) kept in *folder*, for a series' page
    offline to show those not downloaded. Never while viewing privately.
    True once written."""
    if privacy.private() or not str(source or "") or series_id in (None, ""):
        return False
    kept: list[dict] = []
    for row in rows:
        clean = _episode_row(row)
        if clean is not None:
            kept.append(clean)
        if len(kept) >= EPISODES_MAX:
            break
    if not kept:
        return False
    data = json.dumps({"format": FORMAT, "source": str(source)[:200],
                       "series_id": str(series_id)[:200], "episodes": kept},
                      ensure_ascii=False).encode("utf-8")
    if len(data) > EPISODES_FILE_MAX:
        return False
    path = episodes_file(folder, source, series_id)
    try:
        os.makedirs(folder, exist_ok=True)
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.part"
        _write_file(tmp, data)
        os.replace(tmp, path)
    except OSError as e:
        log.debug("downloads: a series' episodes not kept: %s", e)
        return False
    return True


def known_episodes(folder: str, source: Any, series_id: Any) -> list[dict]:
    """The episodes kept of a series (``keep_episodes``) - [] for none, or
    for a file that is not one this app wrote (too large, not JSON, of
    another series). Bounded, no link followed."""
    path = episodes_file(folder, source, series_id)
    try:
        fd = os.open(path, os.O_RDONLY | _nofollow())
    except OSError:
        return []
    try:
        with os.fdopen(fd, "rb") as fh:
            raw = fh.read(EPISODES_FILE_MAX + 1)
    except OSError:
        return []
    if len(raw) > EPISODES_FILE_MAX:
        return []
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError):
        return []
    if not isinstance(data, dict) or data.get("source") != str(source)[:200] \
            or data.get("series_id") != str(series_id)[:200]:
        return []
    eps = data.get("episodes")
    out = []
    for row in eps[:EPISODES_MAX] if isinstance(eps, list) else []:
        clean = _episode_row(row)
        if clean is not None:
            out.append(clean)
    return out


# -- the manager ------------------------------------------------------------------------

class _Stopped(Exception):
    """A running job stopped on purpose: "pause", "cancel", "hold", "private"
    or "close"."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Failed(Exception):
    def __init__(self, why: str, detail: str = "") -> None:
        super().__init__(why)
        self.why = why
        self.detail = detail


class _Playlist(Exception):
    """The address answered with an HLS playlist: read it as one."""


#: (job) -> (its address, its source as core/nerd.set_source keeps one).
Resolver = Callable[[Job], tuple[str, dict | None]]
#: (a picture's address) -> its bytes, checked as a picture, or None.
Picture = Callable[[str], bytes | None]


def _open_source(src: dict | None, url: str, **kw: Any) -> Any:
    from .dvr_ingest import open_for
    return open_for(src, url, **kw)


def _ffmpeg() -> str | None:
    from .recording import RecordingManager
    return RecordingManager.ffmpeg()


def _content_range(value: Any) -> tuple[int | None, int | None]:
    """(where it starts, the whole length) of a Content-Range header
    ("bytes 100-199/1000", "bytes */1000"); None for what it does not say."""
    m = re.match(r"\s*bytes\s+(?:(\d{1,15})-\d{1,15}|\*)\s*/\s*(\d{1,15}|\*)\s*$",
                 str(value or ""))
    if not m:
        return None, None
    start = int(m.group(1)) if m.group(1) else None
    total = int(m.group(2)) if m.group(2) != "*" else None
    return start, total


def _definitive(e: BaseException) -> tuple[str, str] | None:
    """Why an error ends a job at once - a certificate, where it led, an
    answer past its ceiling - or None for one worth trying again (a dropped
    connection)."""
    name = type(e).__name__
    if name in ("DestinationRefused", "RedirectRefused", "LoginHeld", "LoginRefused",
                "InvalidURL", "InvalidSchema", "MissingSchema", "TooManyRedirects"):
        return WHY_REFUSED, ""
    if name == "SSLError" or "CERTIFICATE_VERIFY_FAILED" in str(e):
        return WHY_CERT, ""
    return None


class Downloads:
    """The queue and what is downloaded. The window's thread makes it, adds,
    pauses, resumes, cancels and deletes, decides the holds and saves; the
    worker (``start``) does the rest."""

    def __init__(self, settings: Settings, resolve: Resolver | None = None,
                 picture: Picture | None = None,
                 opener: Callable[..., Any] | None = None,
                 ffmpeg: Callable[[], str | None] | None = None,
                 free: Callable[[str], int] | None = None, gated: bool = True) -> None:
        self.settings = settings
        #: A job runs only once the window said it may (``holds``); without
        #: a window to ask (*gated* False) unless a hold says otherwise.
        self.gated = gated
        self.resolve = resolve
        self.picture = picture
        self._open = opener or _open_source
        self._ffmpeg = ffmpeg or _ffmpeg
        self._free = free or (lambda p: shutil.disk_usage(p).free)
        #: The least free space a job leaves where it writes.
        self.floor = _floor()
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._halted = threading.Event()
        self._halt = ""
        self._resp: Any = None
        self._current: Job | None = None
        self._jobs: list[Job] = self._load()
        self._items: dict[tuple[str, str, str], Item] = {}
        self._scanned = False
        self._rescan = True
        self._folder = folder_of(settings)
        self._cap = int(cap_gb(settings) * GB)
        #: Job id -> why it may not run now ("" or missing: it may) - the
        #: window's word, given on its thread. A job not in it waits.
        self.holds: dict[str, str] = {}
        #: Moves on with every change the window shows.
        self.version = 0
        self.dirty = False
        self._events: list[tuple[str, Job, Item | None]] = []
        self._thread: threading.Thread | None = None
        self._closed = False
        #: The running job's connections so far, and the bytes its answer
        #: now brought (the worker's own).
        self._tries = 0
        self._got = 0

    # -- the settings (the window's thread) ------------------------------------------

    def _load(self) -> list[Job]:
        try:
            raw = json.loads(str(self.settings.value(QUEUE_KEY, "") or "[]"))
        except (ValueError, TypeError, RecursionError):
            raw = []
        jobs: list[Job] = []
        seen: set[tuple[str, str, str]] = set()
        for d in raw if isinstance(raw, list) else []:
            job = Job.from_dict(d)
            if job is not None and job.ident not in seen:
                seen.add(job.ident)
                jobs.append(job)
        return jobs[:QUEUE_MAX]

    def save(self) -> None:
        """The queue into the settings (the window's thread)."""
        with self._lock:
            data = [j.to_dict() for j in self._jobs]
            self.dirty = False
        self.settings.setValue(QUEUE_KEY, json.dumps(data))

    def folder(self) -> str:
        return self._folder

    def set_folder(self, path: str) -> None:
        """Downloads go to *path* from now on; what is downloaded is read from
        there (a download already there stays where it is)."""
        path = str(path or "")
        self.settings.setValue(DIR_KEY, path)
        with self._lock:
            self._folder = path or default_folder()
            self._rescan = True
        self._wake.set()

    def cap_bytes(self) -> int:
        return self._cap

    def set_cap_gb(self, gb: float) -> None:
        self.settings.setValue(CAP_KEY, f"{max(0.0, float(gb)):g}")
        self._cap = int(cap_gb(self.settings) * GB)

    # -- what there is (any thread) ---------------------------------------------------

    def _bump(self) -> None:
        self.version += 1

    def jobs(self) -> list[Job]:
        """The queue, in order (copies)."""
        with self._lock:
            return [replace(j, meta=dict(j.meta)) for j in self._jobs]

    def job(self, job_id: str) -> Job | None:
        with self._lock:
            j = next((j for j in self._jobs if j.id == job_id), None)
            return replace(j, meta=dict(j.meta)) if j is not None else None

    def job_of(self, source: Any, kind: str, stream_id: Any) -> Job | None:
        ident = _ident(source, kind, stream_id)
        with self._lock:
            j = next((j for j in self._jobs if j.ident == ident), None)
            return replace(j, meta=dict(j.meta)) if j is not None else None

    def current(self) -> Job | None:
        with self._lock:
            return replace(self._current) if self._current is not None else None

    def items(self) -> list[Item]:
        with self._lock:
            return list(self._items.values())

    def find(self, source: Any, kind: str, stream_id: Any) -> Item | None:
        """The downloaded file of a title, while it is there."""
        with self._lock:
            return self._items.get(_ident(source, kind, stream_id))

    @property
    def scanned(self) -> bool:
        return self._scanned

    def used_bytes(self) -> int:
        """What the downloads take on the disk, and the one being written."""
        with self._lock:
            total = sum(i.size for i in self._items.values())
            cur = self._current
        if cur is not None and cur.part:
            total += _size(cur.part)
        return total

    def take_events(self) -> list[tuple[str, Job, Item | None]]:
        """("done", job, item) and ("failed", job, None) since asked last."""
        with self._lock:
            out, self._events = self._events, []
        return out

    # -- the person's own (the window's thread) ------------------------------------

    def add(self, kind: str, source: Any, stream_id: Any, key: Any = None, title: str = "",
            ext: str = "", meta: dict | None = None, poster: str = "",
            series_poster: str = "") -> Job | None:
        """A title into the queue (at its end): the job, or the one there is
        already for it (started again when paused or failed); None for one
        downloaded already, one that is no film or episode - or while viewing
        privately (nothing downloads then)."""
        if privacy.private():
            log.info("downloads: nothing is added while viewing privately")
            return None
        if kind not in KINDS or not str(source or "") or stream_id in (None, ""):
            return None
        ident = _ident(source, kind, stream_id)
        with self._lock:
            if ident in self._items:
                return None
            old = next((j for j in self._jobs if j.ident == ident), None)
            if old is not None:
                if old.state in (PAUSED, FAILED):
                    old.state, old.why, old.detail = WAITING, "", ""
                    self.dirty = True
                    self._bump()
                self._wake.set()
                return replace(old)
            if len(self._jobs) >= QUEUE_MAX:
                return None
            job = Job(id=uuid.uuid4().hex[:12], kind=kind, source=ident[0],
                      stream_id=ident[2], key=str(key if key is not None else ident[2])[:200],
                      title=str(title or "")[:500], ext=str(ext or "")[:10],
                      meta=clean_meta(meta), poster=str(poster or "")[:4096],
                      series_poster=str(series_poster or "")[:4096], added=time.time())
            self._jobs.append(job)
            self.dirty = True
            self._bump()
        log.info("downloads: %s queued", for_log(job.title))
        self._wake.set()
        return replace(job)

    def pause(self, job_id: str) -> None:
        """Paused by the person: it stays until resumed (its .part kept)."""
        with self._lock:
            j = next((j for j in self._jobs if j.id == job_id), None)
            if j is None:
                return
            if j is self._current:
                self._stop_current("pause")
            elif j.state == WAITING:
                j.state = PAUSED
                self.dirty = True
                self._bump()

    def resume(self, job_id: str, go_ahead: bool = False) -> None:
        """A paused or failed job waits to run again (*go_ahead*: beside
        what else of the app's uses the account)."""
        if privacy.private():
            return
        with self._lock:
            j = next((j for j in self._jobs if j.id == job_id), None)
            if j is None:
                return
            if go_ahead:
                j.go_ahead = True
            if j.state in (PAUSED, FAILED):
                j.state, j.why, j.detail = WAITING, "", ""
                self.dirty = True
                self._bump()
        self._wake.set()

    def allow(self, job_id: str) -> None:
        """The person said it may run beside the rest (this session)."""
        with self._lock:
            j = next((j for j in self._jobs if j.id == job_id), None)
            if j is not None:
                j.go_ahead = True
        self._wake.set()

    def cancel(self, job_id: str) -> None:
        """Out of the queue, its .part removed."""
        with self._lock:
            j = next((j for j in self._jobs if j.id == job_id), None)
            if j is None:
                return
            if j is self._current:
                self._stop_current("cancel")
                return
            self._jobs.remove(j)
            part = j.part
            self.dirty = True
            self._bump()
        self._drop_part(part)
        log.info("downloads: %s cancelled", for_log(j.title))

    def hold_now(self, job_id: str, why: str = "connection") -> None:
        """The window needs the connection the running job holds: it stops
        now and waits (its .part kept) - before the next connection opens."""
        with self._lock:
            self.holds[job_id] = why
            if self._current is not None and self._current.id == job_id:
                self._stop_current("hold")

    def set_holds(self, holds: dict[str, str]) -> None:
        """The window's word on every job: why each may not run now ("" for
        none). The running one stops when it gets one."""
        with self._lock:
            self.holds = dict(holds)
            cur = self._current
            if cur is not None and self.holds.get(cur.id):
                self._stop_current("hold")
        self._wake.set()

    def private_on(self) -> None:
        """Private viewing began: the running job stops at once and waits."""
        with self._lock:
            if self._current is not None:
                self._stop_current("private")

    def delete(self, source: Any, kind: str, stream_id: Any) -> bool:
        """A downloaded title's file, sidecar and poster gone (its series'
        poster too once no episode beside it names it). False when there is
        none."""
        ident = _ident(source, kind, stream_id)
        with self._lock:
            item = self._items.pop(ident, None)
            root = self._folder
            others = [i for i in self._items.values()
                      if os.path.dirname(i.path) == os.path.dirname(item.path)] if item else []
            self._bump()
        if item is None:
            return False
        if not _inside(root, item.path):
            log.warning("downloads: %s is not in the downloads folder - left as it is",
                        for_log(item.title))
            return False
        for p in (item.path, item.path + SIDECAR, item.poster):
            _drop(p)
        sp = item.series_poster
        if sp and not any(o.series_poster == sp for o in others):
            _drop(sp)
        folder = os.path.dirname(item.path)
        if item.kind == "episode" and folder != os.path.normpath(root) and not others:
            try:
                os.rmdir(folder)
            except OSError:
                pass
        log.info("downloads: %s deleted", for_log(item.title))
        return True

    # -- the worker ---------------------------------------------------------------

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="downloads", daemon=True)
            self._thread.start()

    def close(self, wait: float = 2.0) -> None:
        """The app closes: the running job stops (it carries on at the next
        start) and the worker ends."""
        self._closed = True
        with self._lock:
            if self._current is not None:
                self._stop_current("close")
        self._wake.set()
        t = self._thread
        if t is not None and t.is_alive() and wait:
            t.join(wait)

    def wake(self) -> None:
        self._wake.set()

    def _stop_current(self, reason: str) -> None:
        # (under the lock) The running job told to stop; its connection
        # closed at once - a read blocked on it never holds the account's
        # one connection until the timeout.
        self._halt = reason
        self._halted.set()
        resp = self._resp
        if resp is not None:
            from .dvr_ingest import _hard_close
            try:
                _hard_close(resp)
            except Exception:
                pass

    def _run(self) -> None:
        while not self._closed:
            if self._rescan:
                self._rescan = False
                try:
                    self.scan()
                except Exception:
                    log.exception("downloads: the folder was not read")
            job = self._next()
            if job is None:
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            self._work(job)

    def _next(self) -> Job | None:
        if privacy.private():
            return None
        with self._lock:
            for j in self._jobs:
                said = j.id in self.holds or not self.gated
                if j.state == WAITING and said and not self.holds.get(j.id):
                    j.state = RUNNING
                    self._current = j
                    self._halt = ""
                    self._halted.clear()
                    self._bump()
                    return j
        return None

    def _check(self) -> None:
        """A stop asked for, or private viewing begun: the job stops here."""
        if self._halt:
            raise _Stopped(self._halt)
        if privacy.private():
            raise _Stopped("private")

    def _work(self, job: Job) -> None:
        log.info("downloads: %s - starting", for_log(job.title))
        try:
            url, src = self._address(job)
            self._check()
            path = self._fetch(job, url, src)
            item = self._settle(job, path, url)
            with self._lock:
                if job in self._jobs:
                    self._jobs.remove(job)
                self._items[item.ident] = item
                self._events.append(("done", replace(job), item))
                self.dirty = True
            log.info("downloads: %s - done (%d MB)", for_log(job.title), item.size // 10**6)
        except _Stopped as s:
            part = ""
            with self._lock:
                if s.reason == "cancel":
                    if job in self._jobs:
                        self._jobs.remove(job)
                    part = job.part
                elif s.reason == "pause":
                    job.state = PAUSED
                else:
                    job.state = WAITING
                self.dirty = True
            if part:
                self._drop_part(part)
            log.info("downloads: %s - %s", for_log(job.title),
                     {"cancel": "cancelled", "pause": "paused", "close": "the app closes",
                      "private": "waits while viewing privately"}.get(
                          s.reason, "waits for a free connection"))
        except _Failed as f:
            with self._lock:
                job.state, job.why, job.detail = FAILED, f.why, f.detail[:200]
                self._events.append(("failed", replace(job), None))
                self.dirty = True
            log.info("downloads: %s failed - %s%s", for_log(job.title),
                     WHY_WORDS.get(f.why, f.why), f" ({f.detail})" if f.detail else "")
        except Exception as e:          # never out of the worker
            with self._lock:
                job.state, job.why, job.detail = FAILED, WHY_OTHER, type(e).__name__
                self._events.append(("failed", replace(job), None))
                self.dirty = True
            log.exception("downloads: %s failed", for_log(job.title))
        finally:
            with self._lock:
                self._current = None
                self._resp = None
                self._halt = ""
                self._halted.clear()
                self._bump()

    def _address(self, job: Job) -> tuple[str, dict | None]:
        if self.resolve is None:
            raise _Failed(WHY_SOURCE)
        try:
            url, src = self.resolve(job)
        except Unavailable as e:
            raise _Failed(WHY_SOURCE) from e
        except Exception as e:
            log.info("downloads: no address for %s: %s", for_log(job.title), type(e).__name__)
            raise _Failed(WHY_ADDRESS) from e
        url = str(url or "")
        if not url:
            raise _Failed(WHY_ADDRESS)
        from .net_route import url_host
        from .player_exec import fetchable
        if url_host(url) is None or not fetchable(url):
            raise _Failed(WHY_SCHEME)
        return url, src

    # -- where it goes ------------------------------------------------------------

    def _target_dir(self, job: Job) -> str:
        root = self._folder
        if job.kind == "movie":
            rel = MOVIES_DIR
        else:
            series = str(job.meta.get("series_title") or "") or job.title
            rel = os.path.join(SERIES_DIR, _safe_name(series))
        folder = os.path.join(root, rel)
        try:
            os.makedirs(folder, exist_ok=True)
        except (OSError, ValueError) as e:
            raise _Failed(WHY_FOLDER, type(e).__name__) from e
        if not _stays_inside(root, folder):
            log.warning("downloads: a folder on the way leads out of the downloads folder "
                        "(a link) - not written")
            raise _Failed(WHY_FOLDER, "outside")
        return folder

    def _stem(self, job: Job) -> str:
        m = job.meta
        if job.kind == "movie":
            year = str(m.get("year") or "")
            name = job.title if (not year or year in job.title) else f"{job.title} ({year})"
            return _safe_name(name)
        series = str(m.get("series_title") or "")
        season, number = _whole(m.get("season")), _whole(m.get("episode"))
        se = f"S{season:02d}E{number:02d}" if season is not None and number is not None else ""
        ep = str(m.get("episode_title") or "")
        name = " ".join(x for x in (series, se) if x)
        if ep and ep not in name:
            name = f"{name} - {ep}" if name else ep
        return _safe_name(name or job.title)

    def _part_for(self, job: Job) -> str:
        """The job's .part: the one it began, while that is still in the
        downloads folder; else a new one, under a name nothing has."""
        root = self._folder
        old = job.part
        if old and old.endswith(PART) and os.path.isdir(os.path.dirname(old)) \
                and _stays_inside(root, os.path.dirname(old)) and not os.path.islink(old):
            return old
        if old and old.endswith(PART) and _size(old) and not _inside(root, old):
            self._drop_part(old)            # the folder was changed meanwhile
        folder = self._target_dir(job)
        stem = os.path.join(folder, self._stem(job))
        path, n = stem + PART, 2
        while os.path.lexists(path) or self._part_taken(path, job):
            path = f"{stem} ({n}){PART}"
            n += 1
        with self._lock:
            job.part = path
            job.done = 0
            self.dirty = True
        return path

    def _part_taken(self, path: str, job: Job) -> bool:
        with self._lock:
            return any(j is not job and j.part == path for j in self._jobs)

    def _drop_part(self, part: str) -> None:
        if part and part.endswith(PART) and _inside(self._folder, part):
            _drop(part)

    # -- space ----------------------------------------------------------------------

    def _room(self, needed: int, folder: str) -> None:
        """Room for *needed* more bytes: within the downloads' limit and with
        the disk's floor left - else the job stops and says which."""
        try:
            free = int(self._free(folder))
        except OSError:
            free = None
        if free is not None and free - max(0, needed) < self.floor:
            raise _Failed(WHY_DISK)
        cap = self._cap
        if cap and self.used_bytes() + max(0, needed) > cap:
            raise _Failed(WHY_CAP)

    # -- the bytes ------------------------------------------------------------------

    def _fetch(self, job: Job, url: str, src: dict | None) -> str:
        path = urlsplit(url).path.lower()
        if path.endswith((".m3u8", ".m3u")):
            return self._fetch_hls(job, url, src)
        try:
            return self._fetch_http(job, url, src)
        except _Playlist:
            log.info("downloads: %s is an HLS playlist - read by ffmpeg", for_log(job.title))
            return self._fetch_hls(job, url, src)

    def _try_again(self, e: BaseException, fails: int, wrote: bool = False) -> int:
        """A try that failed: given up at once for a certificate or where it
        led, after MAX_TRIES in a row that brought nothing for a dropped
        connection; one that brought bytes is carried on at once, else after
        a pause that grows."""
        self._check()
        why = _definitive(e)
        if why is not None:
            raise _Failed(*why)
        self._tries += 1
        if self._tries > MAX_CONNECTIONS:
            raise _Failed(WHY_DROPS, type(e).__name__)
        if wrote:
            return 0
        fails += 1
        if fails > MAX_TRIES:
            raise _Failed(WHY_DROPS, type(e).__name__)
        log.info("downloads: the connection dropped - try %d of %d (%s)", fails, MAX_TRIES,
                 type(e).__name__)
        self._halted.wait(min(16.0, BACKOFF * (2 ** (fails - 1))))
        self._check()
        return fails

    def _fetch_http(self, job: Job, url: str, src: dict | None) -> str:
        part = self._part_for(job)
        folder = os.path.dirname(part)
        restarts = [0]
        fails = 0
        self._tries = 0
        while True:
            self._check()
            have = _size(part)
            headers = {"User-Agent": USER_AGENT}
            if have:
                headers["Range"] = f"bytes={have}-"
            self._got = 0
            try:
                resp = self._open(src, url, stream=True, timeout=TIMEOUT, headers=headers)
            except _Stopped:
                raise
            except Exception as e:
                fails = self._try_again(e, fails)
                continue
            with self._lock:
                self._resp = resp
                halted = bool(self._halt)
            if halted:
                self._close(resp)
                self._check()
            try:
                done = self._read_answer(job, resp, part, folder, have, restarts)
            except (_Stopped, _Failed, _Playlist):
                self._close(resp)
                raise
            except _Again:
                self._close(resp)
                continue
            except Exception as e:
                self._close(resp)
                fails = self._try_again(e, fails, wrote=self._got > 0)
                continue
            self._close(resp)
            if done:
                return part
            # Cut before its end: carried on where it stopped.
            log.info("downloads: %s was cut at %d MB - carried on", for_log(job.title),
                     _size(part) // 10**6)
            fails = self._try_again(ConnectionError("cut"), fails, wrote=self._got > 0)

    def _close(self, resp: Any) -> None:
        with self._lock:
            if self._resp is resp:
                self._resp = None
        try:
            resp.close()
        except Exception:
            pass

    def _read_answer(self, job: Job, resp: Any, part: str, folder: str, have: int,
                     restarts: list[int]) -> bool:
        """One answer into *part* (from *have* on). True once the file is
        whole; False when it was cut (to be carried on); _Again to ask anew.
        *restarts*: how often it was read from the start again."""
        status = int(getattr(resp, "status_code", 0) or 0)
        hdrs = getattr(resp, "headers", None) or {}
        ctype = str(hdrs.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        try:
            length: int | None = int(str(hdrs.get("Content-Length") or "").strip())
            if length is not None and length < 0:
                length = None
        except ValueError:
            length = None
        if status == 416 and have:
            _start, whole = _content_range(hdrs.get("Content-Range"))
            if whole is not None and whole == have:
                with self._lock:
                    job.done = job.total = have
                return True
            log.info("downloads: %s - the server has another file now: from the start",
                     for_log(job.title))
            restarts[0] += 1
            if restarts[0] > MAX_RESTARTS:
                raise _Failed(WHY_RESUME)
            self._truncate(part)
            raise _Again()
        if status >= 400 or status not in (200, 206):
            raise _Failed(WHY_STATUS, str(status))
        total: int | None
        if status == 206:
            start, total = _content_range(hdrs.get("Content-Range"))
            if start is None:
                raise _Failed(WHY_RESUME)
            if start != have:
                if start != 0:
                    raise _Failed(WHY_RESUME)
                have = 0
                self._truncate(part)
            if total is None and length is not None:
                total = have + length
        else:
            if have:
                # A server that ignores Range sends the whole file again:
                # read from its start, a few times at most.
                restarts[0] += 1
                if restarts[0] > MAX_RESTARTS:
                    raise _Failed(WHY_RESUME)
                log.info("downloads: the server does not carry on a cut download - "
                         "%s from the start again", for_log(job.title))
                have = 0
                self._truncate(part)
            total = length
        return self._write(job, resp, part, folder, have, total, ctype)

    def _truncate(self, part: str) -> None:
        try:
            fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _nofollow(), 0o644)
            os.close(fd)
        except OSError as e:
            raise _Failed(WHY_FOLDER, type(e).__name__) from e

    def _write(self, job: Job, resp: Any, part: str, folder: str, have: int,
               total: int | None, ctype: str) -> bool:
        if "html" in ctype:
            raise _Failed(WHY_NOT_VIDEO, "a web page")
        with self._lock:
            job.done = have
            job.total = int(total or 0)
            self._bump()
        self._room((total - have) if total else 0, folder)
        chunks = resp.iter_content(CHUNK)
        if not have:
            from .dvr_ingest import not_a_stream
            head = bytearray()
            for chunk in chunks:
                self._check()
                head += chunk
                if len(head) >= SNIFF:
                    break
            what = not_a_stream(bytes(head[:4 * SNIFF]), ctype)
            if what == "a playlist":
                raise _Playlist()
            if what:
                raise _Failed(WHY_NOT_VIDEO, what)
            if not head:
                if total == 0:
                    raise _Failed(WHY_NOT_VIDEO, "nothing")
                return False
            first: list[bytes] = [bytes(head)]
        else:
            first = []
        flags = os.O_WRONLY | os.O_CREAT | _nofollow() | (os.O_APPEND if have else os.O_TRUNC)
        try:
            fd = os.open(part, flags, 0o644)
        except OSError as e:
            raise _Failed(WHY_FOLDER, type(e).__name__) from e
        done = have
        since = 0
        with os.fdopen(fd, "ab" if have else "wb") as fh:
            for chunk in _chain(first, chunks):
                self._check()
                if not chunk:
                    continue
                fh.write(chunk)
                done += len(chunk)
                since += len(chunk)
                self._got += len(chunk)
                with self._lock:
                    job.done = done
                    self.version += 1
                if since >= SPACE_EVERY:
                    since = 0
                    fh.flush()
                    self._room(0, folder)
                if total is not None and done > total:
                    raise _Failed(WHY_NOT_VIDEO, "longer than it said")
        if total is not None:
            return done >= total
        return True

    def _fetch_hls(self, job: Job, url: str, src: dict | None) -> str:
        ff = self._ffmpeg()
        if not ff:
            raise _Failed(WHY_FFMPEG)
        from .dvr_ingest import NotAStream
        from .restream import ffmpeg_opener
        part = self._part_for(job)
        folder = os.path.dirname(part)
        ts_part = part[:-len(PART)] + ".ts" + PART
        self._room(0, folder)
        try:
            resp = ffmpeg_opener(ff, src)(url)
        except NotAStream as e:
            raise _Failed(WHY_PRIVATE_WAY) from e
        except OSError as e:
            raise _Failed(WHY_FFMPEG, type(e).__name__) from e
        with self._lock:
            self._resp = resp
            job.done, job.total = 0, 0
        done = 0
        since = 0
        try:
            fd = os.open(ts_part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _nofollow(), 0o644)
            with os.fdopen(fd, "wb") as fh:
                for chunk in resp.iter_content(CHUNK):
                    self._check()
                    if not chunk:
                        continue
                    fh.write(chunk)
                    done += len(chunk)
                    since += len(chunk)
                    with self._lock:
                        job.done = done
                        self.version += 1
                    if since >= SPACE_EVERY:
                        since = 0
                        fh.flush()
                        self._room(0, folder)
            self._check()
            proc = getattr(resp, "proc", None)
            code = proc.wait(HLS_END_S) if proc is not None else 0
        except (_Stopped, _Failed):
            self._close(resp)
            _drop(ts_part)              # an HLS film starts again: nothing to carry on
            with self._lock:
                job.done = 0
            raise
        except OSError as e:
            self._close(resp)
            _drop(ts_part)
            raise _Failed(WHY_FOLDER, type(e).__name__) from e
        except Exception as e:
            self._close(resp)
            _drop(ts_part)
            raise _Failed(WHY_CONVERT, type(e).__name__) from e
        self._close(resp)
        if code != 0 or not done:
            _drop(ts_part)
            raise _Failed(WHY_CONVERT, f"ffmpeg {code}")
        stem = part[:-len(PART)]
        ts, n = stem + ".ts", 2
        while os.path.lexists(ts):
            ts = f"{stem} ({n}).ts"
            n += 1
        os.replace(ts_part, ts)
        from . import rec_mp4
        result = rec_mp4.remux(ts, ff)
        if not result.ok:
            log.info("downloads: %s kept as .ts (%s)", for_log(job.title), result.why)
        return result.path

    # -- once whole ------------------------------------------------------------------

    def _settle(self, job: Job, path: str, url: str) -> Item:
        """The file named, its sidecar and poster beside it."""
        folder = os.path.dirname(path)
        if path.endswith(PART):
            try:
                with open(path, "rb") as fh:
                    head = fh.read(512)
            except OSError:
                head = b""
            stem = path[:-len(PART)]
            ext = _video_ext(head, job.ext, url)
            final, n = f"{stem}.{ext}", 2
            while os.path.lexists(final):
                final = f"{stem} ({n}).{ext}"
                n += 1
            os.replace(path, final)
        else:
            final = path
        size = _size(final)
        info: dict[str, Any] = {"format": FORMAT, "kind": job.kind, "source": job.source,
                                "stream_id": job.stream_id, "key": job.key,
                                "title": job.title or os.path.basename(final),
                                "file": os.path.basename(final), "size": size,
                                "added": int(time.time())}
        info.update(clean_meta(job.meta))
        poster = self._poster(job.poster, final + POSTER)
        if poster:
            info["poster"] = poster
        if job.kind == "episode":
            sp = self._series_poster(job.series_poster or job.poster, folder)
            if sp:
                info["series_poster"] = sp
        try:
            _write_file(final + SIDECAR, json.dumps(info, ensure_ascii=False).encode("utf-8"))
        except OSError as e:
            log.warning("downloads: %s has no sidecar (%s)", for_log(job.title), e)
        return Item(final, size, read_sidecar(final + SIDECAR) or info)

    def _picture(self, address: str) -> bytes | None:
        if not address or self.picture is None:
            return None
        try:
            data = self.picture(address)
        except Exception as e:
            log.debug("downloads: no poster: %s", type(e).__name__)
            return None
        return data if data and _picture_ext(data) else None

    def _poster(self, address: str, stem: str) -> str:
        """A poster saved as *stem*.<its type>: its file's name, "" for none."""
        data = self._picture(address)
        if data is None:
            return ""
        path = f"{stem}.{_picture_ext(data)}"
        try:
            _write_file(path, data)
        except OSError as e:
            log.debug("downloads: poster not saved: %s", e)
            return ""
        return os.path.basename(path)

    def _series_poster(self, address: str, folder: str) -> str:
        for ext in PICTURE_EXTS:
            name = f"{SERIES_POSTER}.{ext}"
            if os.path.isfile(os.path.join(folder, name)):
                return name
        name = self._poster(address, os.path.join(folder, SERIES_POSTER))
        return name

    # -- what is there ----------------------------------------------------------------

    def scan(self) -> None:
        """What is downloaded, read from the sidecars in the folder (no link
        followed, bounded in depth and number)."""
        root = self._folder
        found: dict[tuple[str, str, str], Item] = {}
        seen = 0
        stack = [(root, 0)]
        while stack and seen <= SCAN_MAX:
            d, depth = stack.pop()
            try:
                entries = list(os.scandir(d))
            except OSError:
                continue
            for e in entries:
                seen += 1
                if seen > SCAN_MAX:
                    log.info("downloads: the folder holds more than %d entries - read so far",
                             SCAN_MAX)
                    break
                try:
                    if e.is_dir(follow_symlinks=False):
                        if depth < SCAN_DEPTH:
                            stack.append((e.path, depth + 1))
                        continue
                    if not e.name.endswith(SIDECAR) or not e.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                item = self._item_of(e.path)
                if item is not None:
                    old = found.get(item.ident)
                    if old is None or item.added > old.added:
                        found[item.ident] = item
        with self._lock:
            if root == self._folder:
                self._items = found
                self._scanned = True
                self._bump()

    @staticmethod
    def _item_of(sidecar: str) -> Item | None:
        info = read_sidecar(sidecar)
        if info is None:
            return None
        video = sidecar[:-len(SIDECAR)]
        if os.path.islink(video):
            return None
        size = _size(video)
        if size <= 0:
            return None
        return Item(video, size, info)


class _Again(Exception):
    """Ask the server anew."""


def _chain(first: Iterable[bytes], rest: Iterable[bytes]) -> Iterable[bytes]:
    yield from first
    yield from rest
