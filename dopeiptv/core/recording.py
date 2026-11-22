"""Recording manager: ffmpeg/mpv stream-copy and in-player recording."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlsplit

from PyQt6.QtCore import QObject, QSettings, Qt, QTimer, pyqtSignal

from . import rec_mp4
from .log import log
from .player_exec import (FFMPEG_PROTOCOLS, NO_WINDOW, address_scheme, carries_login,
                          drop_file, fetchable, ffmpeg_protocols, find_player_executable,
                          find_program, private_file)
from .privacy import for_log
from .rec_extras import not_a_device

#: What the player sends, so a panel that plays a channel also records it.
RECORD_UA = "dopeIPTV/1.0"


def piped(url: str) -> bool:
    """Whether a recording of *url* over its own connection is read by the
    app and handed to the recorder on its standard input (``Feed``): an
    http(s) address that names no HLS playlist. Its address - an Xtream
    login in its path, a portal's token in its query - then never stands in
    the recorder's command line, which every account on a Linux computer
    can read (/proc/<pid>/cmdline), and the connection is the app's own,
    under core/dest_guard.py and the source's route like the rewind
    buffer's. An HLS playlist the recorder must fetch itself, segment by
    segment, is read through the app's bridge under an alias that names
    nothing of it (core/net_bridge.py, ``Bridge.alias``)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return (parts.scheme.lower() in ("http", "https") and bool(parts.hostname)
            and not parts.path.lower().endswith((".m3u8", ".m3u")))


def live_address(url: str) -> bool:
    """A live channel's address, which reconnects carry on - never an
    archive's stretch, which a new connection starts again from its top."""
    path = url.split("?", 1)[0].lower()
    return not ("/timeshift/" in path or "timeshift.php" in path)


class _Pipe:
    """A Feed's store (core/dvr_ingest.Ingest writes to one): the
    recorder's standard input."""

    def __init__(self, feed: "Feed") -> None:
        self.feed = feed

    def append(self, data: bytes) -> None:
        self.feed.write(data)

    def new_run(self) -> None:
        """A reconnect that did not carry on where the last one ended: the
        recorder reads on across the seam, as it did across its own."""


class Feed:
    """A recording's own connection made by the app (core/dvr_ingest.Ingest:
    the source's route, user agent and login as the player has them,
    reconnects that carry on where they left off, a page or a message told
    from a stream) and handed to the recorder on its standard input.

    ``classic`` says why the recorder is to be started again reading the
    address itself - through the bridge, under an alias (core/net_bridge.py):
    the address is an HLS playlist after all, or its certificate is one the
    app cannot check while the player would play it unchecked (a server at
    home, or a source the person let play unchecked; the bridge then does
    not check it either) - never for a stream whose certificate the player
    checks (core/nerd.checks_certificate, *strict*)."""

    def __init__(self, proc: subprocess.Popen, url: str, src: dict | None,
                 live: bool = True, opener: Callable[..., Any] | None = None,
                 strict: bool = False) -> None:
        from .dvr_ingest import Ingest, opener_for
        self.proc = proc
        self.written = 0
        self.classic = ""
        self._strict = strict
        self.ingest = Ingest(url, _Pipe(self), opener=opener or opener_for(src),  # type: ignore[arg-type]
                             reconnects=live)
        self._thread = threading.Thread(target=self._run, name="rec-feed", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def write(self, data: bytes) -> None:
        stdin = getattr(self.proc, "stdin", None)
        try:
            if stdin is None:
                raise BrokenPipeError
            stdin.write(data)
            self.written += len(data)
        except (OSError, ValueError):
            # The recorder ended (its time was up, it was stopped): so does
            # the connection - never read on into nothing.
            self.ingest.stop()
            raise

    def _tls_refused(self) -> bool:
        if self._strict or self.written:
            return False
        try:
            from requests.exceptions import SSLError
        except ImportError:
            return False
        return isinstance(self.ingest.last_error, SSLError)

    def _run(self) -> None:
        self.ingest.start()
        while self.ingest.alive and not self._tls_refused():
            self.ingest.join(0.25)
        if self._tls_refused():
            self.classic = "a certificate the app cannot check"
            self.ingest.stop()
        elif not self.written and self.ingest.refused == "a playlist":
            self.classic = "an HLS playlist"
        stdin = getattr(self.proc, "stdin", None)
        if stdin is not None:
            try:
                stdin.close()           # the recorder ends its file
            except OSError:
                pass

    def stop(self) -> None:
        self.ingest.stop()

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()


#: Why a recording was not started for an address that is no stream's
#: (player_exec.fetchable): the recorder is never handed one.
NOT_A_STREAM = "its address is not a stream's"
#: Why one was not started when the app's bridge, which hands the recorder
#: an alias of its address (core/net_bridge.py), could not start: never the
#: address itself in its command line instead.
NO_BRIDGE = "the app could not hand the recorder its address privately"
#: Why one was not started whose address (not http) holds a login and no
#: private file to hand it over in could be made.
NO_PRIVATE_FILE = "its address holds a login and no private file for it could be made"
_LIST_PREFIX = "dopeiptv-rec-"


def _concat_word(text: str) -> str:
    """*text* as one word of ffmpeg's concat list: in single quotes, a quote
    in it closed, escaped and opened again."""
    return "'" + text.replace("'", "'\\''") + "'"


def concat_list(url: str, options: list[str]) -> str:
    """ffmpeg's concat list (ffconcat) that reads *url* - the one way to hand
    ffmpeg an address that is not its argument - with *options* (["-key",
    "value", ...], the input's own) as its ``option`` lines (which an
    ffmpeg older than 5 does not know: its caller gives none then)."""
    lines = ["ffconcat version 1.0", f"file {_concat_word(url)}"]
    for key, value in zip(options[0::2], options[1::2], strict=False):
        lines.append(f"option {key.lstrip('-')} {_concat_word(value)}")
    return "\n".join(lines) + "\n"


_ffmpeg_majors: dict[str, int | None] = {}


def ffmpeg_major(exe: str) -> int | None:
    """*exe*'s major version ("ffmpeg version 6.1.1-3ubuntu5": 6), asked
    once; None for a build that does not say (a git build: new)."""
    if exe in _ffmpeg_majors:
        return _ffmpeg_majors[exe]
    major: int | None = None
    try:
        out = subprocess.run([exe, "-hide_banner", "-version"], capture_output=True, text=True,
                             timeout=10, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
        word = (out.stdout.split() + ["", "", ""])[2].lstrip("nN")
        head = word.split(".", 1)[0]
        major = int(head) if head.isdecimal() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        major = None
    _ffmpeg_majors[exe] = major
    return major


def _bundled_ffmpeg() -> str | None:
    """Path to an ffmpeg shipped inside a frozen (PyInstaller/AppImage) build.

    The spec bundles the ffmpeg binary next to the executable so recording
    works without a system install; it is not on PATH, so look for it here
    before falling back to shutil.which().
    """
    if not getattr(sys, "frozen", False):
        return None
    exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)
    for cand in (os.path.join(base, exe),
                 os.path.join(os.path.dirname(sys.executable), exe)):
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def free_path(path: str, current: str | None = None) -> str:
    """*path*, or "name (2).ext", "name (3).ext"... when a different file is
    already there. A rename or a move onto an existing name replaced that
    recording without a word - os.rename overwrites on Linux and macOS."""
    if current is not None and os.path.abspath(path) == os.path.abspath(
            current):
        return path
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(path):
        path = f"{stem} ({n}){ext}"
        n += 1
    return path


def safe_filename(name: str | None) -> str:
    """Strip characters that are unsafe in filenames; at most 120
    characters and 200 bytes, so the date, a " (2)" and the extension added
    to it stay under a file system's 255 bytes a name (120 characters of
    Japanese alone were 360)."""
    # Control characters and half a surrogate pair too: a title is a
    # provider's, and a NUL in one raised out of every later look at the
    # path (the recorder's tick, every five seconds).
    cleaned = "".join(c for c in (name or "recording")
                      if c not in '/\\:*?"<>|' and ord(c) >= 32 and ord(c) != 127
                      and not 0xD800 <= ord(c) <= 0xDFFF).strip()[:120]
    cleaned = cleaned.encode("utf-8", "ignore")[:200].decode("utf-8", "ignore").strip()
    return not_a_device(cleaned) if cleaned else "recording"


def format_size(nbytes: float) -> str:
    """Human-readable file size."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if nbytes < 1024 or unit == "TB":
            return (f"{nbytes:.1f} {unit}" if unit not in ("B", "KB")
                    else f"{int(nbytes)} {unit}")
        nbytes /= 1024
    return f"{nbytes:.1f} TB"


#: Why a recording from a box failed when what came is encrypted: the box
#: has no card, CI module or rights for the channel (ui/mw_box.py).
SCRAMBLED = "the box cannot decrypt this channel"


class RecordingManager(QObject):
    """Records live streams to local files.

    Uses ffmpeg (stream copy, no re-encode) when available, otherwise
    mpv's --stream-record.  Scheduled-but-not-yet-started jobs survive
    an app restart (persisted via QSettings).
    """

    jobs_changed = pyqtSignal()
    recording_stopped = pyqtSignal(str, str)
    # (from the thread that asked) job id, its address ("" for none), why not.
    _address_in = pyqtSignal(str, str, str)
    # (from a remux's thread) the .ts, the file it is now, why not an MP4
    # ("" when it is one).
    _remuxed = pyqtSignal(str, str, str)

    #: How long after the start the remuxes left from the last run begin:
    #: well clear of the start's own work.
    REMUX_RESUME_MS = 20_000

    #: A job that asks for its address when it starts keeps what that takes
    #: here - {"source": playlist id, "stream_id": ...} - and no address: a
    #: portal's link (create_link) lasts seconds, not until the recording.
    LATER = "url_later"
    #: How long asking for that address may take before the recording is
    #: given up.
    ADDRESS_TIMEOUT_S = 30.0
    NO_ADDRESS = "could not get the stream address"

    #: What the window says of a recording due now (may_open): OPEN its
    #: own connection; RIDE - it made it a recording of what the player
    #: plays already (ride_player), no connection of its own; HOLD - one
    #: more connection would go over the account's limit, or one not known
    #: while the app holds one to it: it waits, nothing opened, until there
    #: is room or the person says go ahead.
    OPEN, RIDE, HOLD = "open", "ride", "hold"
    NO_ROOM = "no free connection to the account"

    VIDEO_EXTS: set[str] = {".ts", ".mp4", ".mkv", ".avi", ".mov",
                             ".webm", ".m2ts"}

    def __init__(self, settings: QSettings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.jobs: list[dict] = []
        self._load()
        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self.tick)
        self._timer.start()
        self.session_cap: int | None = None
        self.stop_inplayer_cb: Callable | None = None
        self._ended_on_quit: list[dict] = []
        # (window's thread) Given a job that asks for its address at start:
        # a function that gets it, run off the window's thread - or None
        # when there is no way to (its source is gone). Set by the window.
        self.address_for: Callable[[dict], Callable[[], str] | None] | None = None
        # (window's thread) Told just before a recording opens a connection
        # of its own: server mode lets go of a channel it streams to other
        # devices when the account has no room for both. Set by the window
        # while server mode runs.
        self.before_spawn: Callable[[dict], None] | None = None
        # (window's thread) Asked as a recording comes due, before it opens
        # a connection of its own - OPEN, RIDE or HOLD. None: it opens, as
        # before (no window to count the account's connections).
        self.may_open: Callable[[dict], str] | None = None
        self._address_in.connect(self._address_came,
                                 Qt.ConnectionType.QueuedConnection)
        # Recordings saved as MP4 (rec_mp4): the .ts files being copied now,
        # and whether the app is closing (a remux due then waits for the
        # next start).
        self._remuxing: set[str] = set()
        self._closing = False
        self._remuxed.connect(self._remux_done, Qt.ConnectionType.QueuedConnection)
        if rec_mp4.Journal(settings).items():
            QTimer.singleShot(self.REMUX_RESUME_MS, self._resume_remuxes)

    # -- storage location ---------------------------------------------------

    def directory(self) -> str:
        d = self.settings.value("recordings_dir", "")
        if not d:
            d = os.path.join(os.path.expanduser("~"), "Videos", "dopeIPTV")
        return d

    def set_directory(self, d: str) -> None:
        self.settings.setValue("recordings_dir", d)

    def folders(self) -> list[str]:
        """All subfolders (relative paths) below the recordings directory."""
        root = self.directory()
        found: list[str] = []
        if os.path.isdir(root):
            for base, dirs, _files in os.walk(root):
                dirs.sort()
                for d in dirs:
                    found.append(os.path.relpath(os.path.join(base, d), root))
        return found

    def files(self, folder: str | None = None) -> list[dict]:
        """Recording files as list items."""
        root = self.directory()
        base = os.path.join(root, folder) if folder else root
        out: list[dict] = []
        if not os.path.isdir(base):
            return out
        walker = (os.walk(base) if folder is None or folder == ""
                  else [(base, [], os.listdir(base))])
        for dirpath, _dirs, names in walker:
            for n in names:
                p = os.path.join(dirpath, n)
                if (os.path.splitext(n)[1].lower() in self.VIDEO_EXTS
                        and os.path.isfile(p)):
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    info = self.read_info(p)
                    out.append({
                        "name": info.get("title") or os.path.splitext(n)[0],
                        "_path": p, "_key": p, "_kind": "recording",
                        "_size": st.st_size, "added": str(int(st.st_mtime)),
                        "_desc": info.get("description", ""),
                        "stream_icon": info.get("icon", ""),
                        "_channel": str(info.get("channel") or ""),
                        "_start": info.get("start") or 0,
                        "_filename": os.path.splitext(n)[0]})
        out.sort(key=lambda f: f["added"], reverse=True)
        return out

    # -- editable info (title / description) sidecar --------------------------

    @staticmethod
    def info_path(video_path: str) -> str:
        return video_path + ".info.json"

    def read_info(self, video_path: str) -> dict:
        try:
            with open(self.info_path(video_path), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def write_info(self, video_path: str, title: str, description: str,
                   icon: str | None = None, channel: str | None = None,
                   start: float | None = None) -> None:
        """Save a title/description/icon sidecar next to the recording.

        *icon* is the channel logo URL, kept so the Recordings list can show
        the same badge as live TV even after a restart (the running job's
        in-memory icon is gone by then). Passing ``icon=None`` leaves any
        existing icon untouched (so editing the title doesn't wipe the logo);
        pass ``icon=""`` to clear it. *channel* (its name) and *start* (when
        it began) likewise: None keeps what is there - the Recordings place
        says them. When title, description, icon and channel are all empty
        the sidecar is removed so the filename is used again."""
        sidecar = self.info_path(video_path)
        old = self.read_info(video_path) if None in (icon, channel, start) else {}
        if icon is None:
            icon = old.get("icon", "")
        if channel is None:
            channel = str(old.get("channel") or "")
        if start is None:
            try:
                start = float(old.get("start") or 0)
            except (TypeError, ValueError):
                start = 0.0
        title, description, icon, channel = (title.strip(), description.strip(),
                                             (icon or "").strip(), channel.strip())
        if not title and not description and not icon and not channel:
            try:
                os.remove(sidecar)
            except OSError:
                pass
            return
        data: dict[str, Any] = {"title": title, "description": description, "icon": icon}
        if channel:
            data["channel"] = channel
        if start:
            data["start"] = int(start)
        try:
            with open(sidecar, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
        except OSError:
            pass

    def drop_info(self, video_path: str) -> None:
        """The sidecar of a recording that was deleted, gone with it."""
        try:
            os.remove(self.info_path(video_path))
        except OSError:
            pass

    def move_info(self, old_video: str, new_video: str) -> None:
        """Keep the sidecar attached when a recording is renamed or moved."""
        src = self.info_path(old_video)
        if os.path.exists(src):
            try:
                shutil.move(src, self.info_path(new_video))
            except OSError:
                pass

    # -- recorder backend ---------------------------------------------------

    @staticmethod
    def ffmpeg() -> str | None:
        """The app's ffmpeg: the one a packaged build bundles, else the
        system's."""
        return _bundled_ffmpeg() or find_program("ffmpeg")

    @staticmethod
    def recorder() -> tuple[str | None, str | None]:
        """(kind, executable) of the available recorder, or (None, None)."""
        ff = RecordingManager.ffmpeg()
        if ff:
            return "ffmpeg", ff
        mpv = find_player_executable("mpv")
        if mpv:
            return "mpv", mpv
        return None, None

    # -- jobs ---------------------------------------------------------------

    def _load(self) -> None:
        try:
            data = json.loads(
                self.settings.value("recording_jobs", "") or "[]")
        except Exception:
            data = []
        now = time.time()
        for j in data if isinstance(data, list) else []:
            # A hand-edited or damaged entry would raise in tick() every
            # five seconds for the rest of the session: keep only jobs that
            # have what the scheduler reads.
            if (not isinstance(j, dict)
                    or not (j.get("url") or self.address_later(j))
                    or not isinstance(j.get("start"), (int, float))
                    or not isinstance(j.get("stop"), (int, float, type(None)))):
                continue
            if (j.get("status") == "scheduled"
                    and (j.get("stop") is None or j["stop"] > now)):
                for key, default in (("id", uuid.uuid4().hex[:10]),
                                     ("title", ""), ("folder", ""),
                                     ("path", ""), ("error", ""),
                                     ("stream_icon", "")):
                    j.setdefault(key, default)
                j["proc"] = None
                self.jobs.append(j)

    def _save(self) -> None:
        keep = [{k: v for k, v in j.items()
                 if k not in ("proc", "recorder", "_asking", "_held", "_go_ahead",
                              "_feed", "_classic", "_alias", "_listed")}
                for j in self.jobs if j.get("status") == "scheduled"]
        self.settings.setValue("recording_jobs", json.dumps(keep))

    def add_job(self, url: str, title: str, start_ts: float,
                stop_ts: float | None, folder: str = "",
                icon: str | None = None, channel: str = "",
                extra: dict | None = None) -> dict:
        """A recording from *start_ts* to *stop_ts*. *extra*: what else
        the job keeps (a programme's own times, the series rule that made
        it), saved with it; never one of the keys above."""
        job: dict[str, Any] = {
            "id": uuid.uuid4().hex[:10], "url": url, "title": title,
            "start": start_ts, "stop": stop_ts, "folder": folder or "",
            "status": "scheduled", "path": "", "error": "", "proc": None,
            "stream_icon": icon or "", "channel": channel or ""}
        for k, v in (extra or {}).items():
            job.setdefault(k, v)
        self.jobs.append(job)
        self._save()
        self.tick()
        self.jobs_changed.emit()
        return job

    def update_job_times(self, job_id: str, start_ts: float,
                         stop_ts: float | None,
                         extra: dict | None = None) -> None:
        """Adjust a not-yet-started scheduled job's start/stop time (and
        what add_job's *extra* gave it)."""
        for j in self.jobs:
            if j["id"] == job_id and j["status"] == "scheduled":
                j["start"] = start_ts
                j["stop"] = stop_ts
                j.pop("_held", None)        # looked at again when it is due
                for k, v in (extra or {}).items():
                    if k not in ("id", "url", "start", "stop", "status",
                                 "path", "proc", "recorder"):
                        j[k] = v
                self._save()
                self.jobs_changed.emit()
                return

    def add_inplayer_job(self, title: str, path: str,
                         stop_ts: float | None, url: str = "",
                         icon: str | None = None,
                         recorder: Any = None, channel: str = "",
                         extra: dict | None = None) -> dict:
        """Register a recording that rides the embedded player's stream -
        or, with *recorder*, reads the player's own rewind buffer (a
        BufferRecorder): no connection of its own, whole through a pause,
        and able to go on in the background (keep_in_background). *extra*:
        what else it keeps (its source, which a recording continued over a
        connection of its own takes), never one of the keys above."""
        job: dict[str, Any] = {
            "id": uuid.uuid4().hex[:10], "url": url, "title": title,
            "start": time.time(), "stop": stop_ts, "folder": "",
            "status": "recording", "path": path, "error": "",
            "proc": None, "inplayer": True, "stream_icon": icon or "",
            "recorder": recorder, "channel": channel or ""}
        for k, v in (extra or {}).items():
            job.setdefault(k, v)
        self.jobs.append(job)
        if path and (icon or channel):
            self.write_info(path, "", "", icon=icon or "", channel=channel or "",
                            start=job["start"])
        self.jobs_changed.emit()
        return job

    def ride_player(self, j: dict, path: str, recorder: Any = None,
                    url: str = "") -> None:
        """Scheduled recording *j*, due now, made from what the player plays
        already - its rewind buffer through *recorder* (a BufferRecorder),
        else its own stream: from now on one in the player, as
        add_inplayer_job's, with no connection of its own. *url*: the
        address it keeps where it had none (a portal's, asked at the start).
        The tick that asked (may_open) says the jobs changed."""
        j.pop("_held", None)
        j.pop("_go_ahead", None)
        j.update(status="recording", inplayer=True, path=path, recorder=recorder,
                 proc=None, error="")
        if url and not j.get("url"):
            j["url"] = url
        if path and (j.get("stream_icon") or j.get("channel")):
            self.write_info(path, "", "", icon=j.get("stream_icon") or "",
                            channel=j.get("channel") or "", start=time.time())
        log.info("record: %s from what the player plays (%s) - no connection of its own",
                 for_log(j.get("title")), "its rewind buffer" if recorder is not None
                 else "its stream")

    def finish_inplayer(self, job_id: str, reason: str = "") -> None:
        for j in self.jobs:
            if (j["id"] != job_id or not j.get("inplayer")
                    or j["status"] != "recording"):
                continue
            if j.get("recorder") is not None:
                j["recorder"].stop()
            elif self.stop_inplayer_cb:
                try:
                    self.stop_inplayer_cb()
                except Exception:
                    pass
            j["status"] = "done"
            self.recording_stopped.emit(j["title"], reason or "stopped")
            self.jobs_changed.emit()

            def validate(j=j, reason=reason):
                if (j.get("path") and os.path.exists(j["path"])
                        and os.path.getsize(j["path"]) > 0):
                    self._after(j)
                    return
                j["status"] = "failed"
                j["error"] = reason or "no data captured"
                self.jobs_changed.emit()

            QTimer.singleShot(1500, validate)
            return

    def keep_in_background(self, job_id: str) -> bool:
        """A recording from the rewind buffer goes on while the player
        changes channel: it is no longer the player's, it ends at its stop
        time or when stopped. False for a recording that cannot."""
        for j in self.jobs:
            if (j["id"] == job_id and j.get("inplayer")
                    and j["status"] == "recording"
                    and j.get("recorder") is not None):
                j["inplayer"] = False
                j["background"] = True
                self.jobs_changed.emit()
                return True
        return False

    def buffer_recording(self, job: dict) -> bool:
        return job.get("recorder") is not None

    def _finish_buffer(self, j: dict, reason: str) -> None:
        j["recorder"].stop()
        j["status"] = "done"
        self.recording_stopped.emit(j["title"], reason)

        def validate(j=j, reason=reason):
            if (j.get("path") and os.path.exists(j["path"])
                    and os.path.getsize(j["path"]) > 0):
                self._after(j)
                return
            j["status"] = "failed"
            j["error"] = j["recorder"].error or reason or "no data captured"
            self.jobs_changed.emit()

        QTimer.singleShot(1500, validate)

    def finish_all_inplayer(self, reason: str = "") -> None:
        for j in list(self.jobs):
            if j.get("inplayer") and j["status"] == "recording":
                self.finish_inplayer(j["id"], reason)

    def cancel(self, job_id: str) -> None:
        for j in self.jobs:
            if j["id"] != job_id:
                continue
            if j.get("inplayer") and j["status"] == "recording":
                self.finish_inplayer(job_id)
            elif j["status"] == "recording" and j.get("recorder") is not None:
                self._finish_buffer(j, "stopped")
            elif j["status"] == "recording":
                self._stop_proc(j)
                j["status"] = "done"
            elif j["status"] == "scheduled":
                j["status"] = "cancelled"
            self._save()
            self.jobs_changed.emit()
            return

    def remove_job(self, job_id: str) -> None:
        self.jobs = [j for j in self.jobs
                     if j["id"] != job_id
                     or j["status"] in ("recording", "scheduled")]
        self._save()
        self.jobs_changed.emit()

    def clear_finished(self) -> None:
        """Remove all done/failed/cancelled jobs from the list."""
        self.jobs = [j for j in self.jobs
                     if j["status"] in ("recording", "scheduled")]
        self._save()
        self.jobs_changed.emit()

    def prune_path(self, path: str) -> None:
        """Remove finished jobs whose file matches *path*."""
        self.jobs = [j for j in self.jobs
                     if j.get("path") != path
                     or j["status"] in ("recording", "scheduled")]
        self._save()
        self.jobs_changed.emit()

    def active_count(self) -> int:
        return sum(1 for j in self.jobs if j["status"] == "recording")

    def subfolder(self, folder: str | None) -> str:
        """The recordings folder's own subfolder *folder* - or the
        recordings folder itself for a name that leads out of it ("..",
        another drive, a path from the root): a job kept in the settings,
        or brought by a backup, never writes anywhere else."""
        root = self.directory()
        if not folder:
            return root
        target = os.path.normpath(os.path.join(root, folder))
        try:
            top = os.path.abspath(root)
            inside = os.path.commonpath([top, os.path.abspath(target)]) == top
        except ValueError:                  # another drive (Windows), a NUL
            inside = False
        if not inside:
            log.warning("record: the folder %r is not in the recordings folder - "
                        "recorded there instead", folder)
            return root
        return target

    @staticmethod
    def _stays_inside(root: str, folder: str) -> bool:
        """Whether *folder* (made already) is the recordings folder *root*
        or one inside it on disk too: no link on the way from *root* down
        that leads elsewhere, and where it resolves to still inside *root* -
        a folder another account can change cannot send a recording out."""
        try:
            top = os.path.realpath(root)
            rel = os.path.relpath(os.path.abspath(folder), os.path.abspath(root))
        except ValueError:
            return False
        if rel == os.curdir:
            return True
        if rel.startswith(os.pardir):
            return False
        here = os.path.abspath(root)
        for part in rel.split(os.sep):
            here = os.path.join(here, part)
            try:
                if os.path.islink(here):
                    return False
            except OSError:
                return False
        try:
            return os.path.commonpath([top, os.path.realpath(folder)]) == top
        except ValueError:
            return False

    @staticmethod
    def _shared_folder(folder: str) -> bool:
        """POSIX: *folder* is another account's, or others may write in it
        without the sticky bit - a link put there could redirect a file."""
        if os.name != "posix":
            return False
        try:
            st = os.stat(folder)
        except OSError:
            return False
        others_write = bool(st.st_mode & 0o002) and not (st.st_mode & 0o1000)
        return others_write or (st.st_uid != os.getuid() and bool(st.st_mode & 0o022))

    def build_path(self, title: str, folder: str = "", channel: str = "") -> str:
        """A unique target file for a new recording: "Title 2026-11-14
        21.00.ts", or as the Nerd page's template names it (folders
        included, never outside the recording folder)."""
        from . import nerd
        from .rec_extras import render_name
        n_ = nerd.current()
        template = n_.get("nerd_rec_template").strip() if n_.on else ""
        base = self.subfolder(folder)
        if template:
            stem = os.path.join(base, render_name(template, title, channel))
        else:
            stamp = datetime.fromtimestamp(time.time()).strftime("%Y-%m-%d %H.%M")
            stem = os.path.join(base, f"{safe_filename(title)} {stamp}")
        os.makedirs(os.path.dirname(stem), exist_ok=True)
        root = self.directory()
        if not self._stays_inside(root, os.path.dirname(stem)):
            log.warning("record: a folder on the way to the recording leads out of the "
                        "recordings folder (a link) - recorded in the recordings folder instead")
            stamp = datetime.fromtimestamp(time.time()).strftime("%Y-%m-%d %H.%M")
            stem = os.path.join(root, f"{safe_filename(title)} {stamp}")
        if self._shared_folder(os.path.dirname(stem)):
            log.warning("record: other accounts on this computer can change the recordings "
                        "folder - a recording there is only as safe as they are")
        path = stem + ".ts"
        n = 1
        # lexists: a link left where the file goes is a name taken too.
        while os.path.lexists(path):
            path = f"{stem} ({n}).ts"
            n += 1
        return path

    def _after(self, j: dict) -> None:
        """A recording is done and has data - once: saved as MP4 when
        Settings > Recording says so, then the Nerd page's command for it
        (if there is one) with the file it is in the end."""
        if j.get("_after_ran"):
            return
        j["_after_ran"] = True
        path = j.get("path") or ""
        if not self._has_data(path):
            return
        if rec_mp4.enabled(self.settings) and rec_mp4.wanted(path):
            self._remux(path, j.get("title") or "", j.get("channel") or "", j)
            return
        self._post(path, j.get("title") or "", j.get("channel") or "")

    @staticmethod
    def _drop_empty(path: object) -> None:
        """The file a recorder that read nothing left, empty."""
        try:
            if path and os.path.isfile(str(path)) and os.path.getsize(str(path)) == 0:
                os.remove(str(path))
        except OSError:
            pass

    @staticmethod
    def _has_data(path: str) -> bool:
        try:
            return bool(path) and os.path.getsize(path) > 0
        except OSError:
            return False

    def _post(self, path: str, title: str, channel: str) -> None:
        """The Nerd page's command after a recording, for *path*."""
        from . import nerd
        from .rec_extras import run_post
        n_ = nerd.current()
        cmd = n_.get("nerd_rec_post").strip() if n_.on else ""
        if cmd and self._has_data(path):
            run_post(cmd, path, title, channel)

    # -- saved as MP4 (rec_mp4) ------------------------------------------------

    def _remux(self, path: str, title: str, channel: str,
               j: dict | None = None) -> None:
        """Copy the finished *path* into an MP4 off this thread; the command
        after it follows with the file it ends up as. Kept in the journal
        until done, so a closing app does it at the next start."""
        rec_mp4.Journal(self.settings).add(path, title, channel)
        if self._closing or path in self._remuxing:
            return
        ff = self.ffmpeg()
        if not ff:
            rec_mp4.Journal(self.settings).done(path)
            log.info("recording: kept as .ts - no ffmpeg to save it as MP4")
            if j is not None:
                j["remux"], j["remux_error"] = "failed", "no ffmpeg"
                self.jobs_changed.emit()
            self._post(path, title, channel)
            return
        self._remuxing.add(path)
        if j is not None:
            j["remux"] = "running"
            self.jobs_changed.emit()
        recorder = j.get("recorder") if j is not None else None
        keep = rec_mp4.keep_subs(self.settings)
        threading.Thread(target=self._remux_work, args=(path, ff, recorder, keep),
                         name="rec-mp4", daemon=True).start()

    def _remux_work(self, path: str, ff: str, recorder: Any = None,
                    keep_subtitles: bool = False) -> None:
        """(a thread of its own) The remux, its outcome handed back. A
        recording from the rewind buffer has written its last bytes first."""
        try:
            if recorder is not None:
                recorder.join(30.0)
            res = rec_mp4.remux(path, ff, keep_subtitles=keep_subtitles)
        except Exception as e:          # never lose the .ts over a bug here
            res = rec_mp4.Result(False, path, f"{type(e).__name__}: {e}")
        try:
            self._remuxed.emit(path, res.path, res.why)
        except RuntimeError:            # the app closed meanwhile
            pass

    def _remux_done(self, src: str, final: str, why: str) -> None:
        self._remuxing.discard(src)
        if self._closing:
            return                      # cut short: done at the next start
        journal = rec_mp4.Journal(self.settings)
        entry = journal.get(src) or {}
        journal.done(src)
        if why == rec_mp4.SUBS_KEPT:
            log.info("recording kept as .ts: %s", why)
        elif why:
            log.warning("recording kept as .ts, not saved as MP4: %s", why)
        else:
            log.info("recording saved as MP4: %s", for_log(os.path.basename(final)))
        for j in self.jobs:
            if j.get("path") == src and j.get("remux") == "running":
                j["remux"] = ("subs" if why == rec_mp4.SUBS_KEPT
                              else "failed" if why else "done")
                if why:
                    j["remux_error"] = why
                else:
                    j["path"] = final
        self.jobs_changed.emit()
        self._post(final, str(entry.get("title") or ""),
                   str(entry.get("channel") or ""))

    def _resume_remuxes(self) -> None:
        """The remuxes the last run left: each whose .ts is still there done
        now (a half MP4 of it removed first), the rest forgotten."""
        journal = rec_mp4.Journal(self.settings)
        for e in journal.items():
            ts = e["ts"]
            if not os.path.isfile(ts):
                journal.done(ts)
                continue
            try:
                os.remove(rec_mp4.part_path(ts))
            except OSError:
                pass
            self._remux(ts, str(e.get("title") or ""), str(e.get("channel") or ""))

    def _way_in(self, j: dict) -> str:
        """How recording *j*, due now, starts (may_open): OPEN, RIDE or
        HOLD - OPEN with no window to ask, or when asking failed (as
        before: counting never stands in a recording's way)."""
        if self.may_open is None:
            return self.OPEN
        try:
            way = self.may_open(j)
        except Exception as e:
            log.debug("record: the account's connections not asked: %s", e)
            return self.OPEN
        if way == self.RIDE and j.get("status") != "recording":
            return self.HOLD                # said so, but not made one: never a second
        return way if way in (self.RIDE, self.HOLD) else self.OPEN

    @classmethod
    def address_later(cls, j: dict) -> bool:
        """Whether *j* asks for its address when it starts."""
        later = j.get(cls.LATER)
        return isinstance(later, dict) and later.get("stream_id") is not None

    def _ask_address(self, j: dict) -> None:
        """(window's thread) A recording that asks for its address now it
        starts: asked off this thread, started when it is in
        (_address_came), given up after ADDRESS_TIMEOUT_S (tick)."""
        fetch = None
        try:
            fetch = self.address_for(dict(j)) if self.address_for else None
        except Exception as e:
            log.info("record: no way to ask for the address of %s: %s",
                     for_log(j.get("title")), e)
        if fetch is None:
            self._no_address(j, "its source is not there")
            return
        j["_asking"] = time.monotonic()
        threading.Thread(target=self._fetch_address, args=(j["id"], fetch),
                         name="rec-address", daemon=True).start()

    def _fetch_address(self, job_id: str, fetch: Callable[[], str]) -> None:
        """(a thread of its own) The address, handed to the window's thread."""
        url, why = "", ""
        try:
            url = str(fetch() or "")
        except Exception as e:
            why = f"{type(e).__name__}: {e}"
        try:
            self._address_in.emit(job_id, url, why)
        except RuntimeError:            # the app closed meanwhile
            pass

    def _address_came(self, job_id: str, url: str, why: str) -> None:
        j = next((j for j in self.jobs if j["id"] == job_id), None)
        if (j is None or j["status"] != "scheduled"
                or j.pop("_asking", None) is None):
            return                      # cancelled or given up meanwhile
        if not url:
            self._no_address(j, why or "the source gave none")
        elif j.get("stop") is not None and j["stop"] <= time.time():
            j["status"] = "failed"
            j["error"] = "stop time passed before the app could start it"
        elif j["start"] > time.time():
            return                      # moved later meanwhile: asked again then
        else:
            j["url"] = url
            self._spawn(j)
        self._save()
        self.jobs_changed.emit()

    def _no_address(self, j: dict, why: str) -> None:
        j.pop("_asking", None)
        j["status"] = "failed"
        j["error"] = self.NO_ADDRESS
        log.info("record: %s not started - %s (%s)", for_log(j.get("title")),
                 self.NO_ADDRESS, why)

    def _spawn(self, j: dict) -> None:
        from . import nerd
        if nerd.blocks_stream(str(j.get("url") or ""), j.get("source_kind")):
            # Nerd's VPN guard: no VPN up, nothing streams - recordings too.
            j["status"] = "failed"
            j["error"] = "no VPN up (Nerd's VPN guard)"
            log.info("record: %s not started - no VPN up", for_log(j.get("title")))
            return
        if not fetchable(str(j.get("url") or "")):
            # A playlist's "address" that is no stream's (concat:, subfile:,
            # gopher:...): the recorder would read this computer's files with
            # it, or send what it says to a port. Never handed to one.
            j["status"] = "failed"
            j["error"] = NOT_A_STREAM
            log.warning("record: %s not started - %s (%s:)", for_log(j.get("title")),
                        NOT_A_STREAM, address_scheme(str(j.get("url") or ""))[:20])
            return
        kind, exe = self.recorder()
        if not exe:
            j["status"] = "failed"
            j["error"] = "neither ffmpeg nor mpv found"
            return
        url = str(j["url"])
        src = self._source_of(j)
        # How the recorder gets the address, which never stands in its
        # command line (every account on a Linux computer reads that):
        # - read by the app and handed over on its input (Feed);
        # - an HLS playlist, and once more an address the app could not
        #   read, through the app's bridge under an alias that names nothing
        #   of it (core/net_bridge.py), the bridge in its environment - the
        #   bridge also keeps its segments, keys and redirects off this
        #   computer and the home network for a source on the internet;
        # - another address with a login in it (rtsp://user:pw@...) in a
        #   private file: ffmpeg's concat list, mpv's playlist;
        # - anything else (a tuner's rtsp address, udp, a file) as before.
        feed = piped(url) and not j.get("_classic")
        web = address_scheme(url) in ("http", "https")
        read, listed = url, ""
        env: dict[str, str] | None = None
        if web and not feed:
            from . import net_bridge
            bridge = net_bridge.guard_for(src, url, proxy=self._proxy_for(src, url))
            try:
                read = bridge.alias(url, src) if bridge is not None else ""
            except ValueError:
                read = ""
            if bridge is None or not read:
                self._not_started(j, NO_BRIDGE)
                return
            j["_alias"] = (bridge, read)
            env = {**os.environ, **net_bridge.child_env(bridge)}
        elif not web and carries_login(url):
            listed = self._private_list(kind, exe, url, j)
            if not listed:
                self._not_started(j, NO_PRIVATE_FILE)
                return
            j["_listed"] = listed
        if self.before_spawn is not None:
            try:
                self.before_spawn(j)
            except Exception as e:
                log.debug("record: before the connection: %s", e)
        try:
            path = self.build_path(str(j.get("title") or ""), str(j.get("folder") or ""),
                                   str(j.get("channel") or ""))
        except (OSError, ValueError) as e:
            # ValueError: a name no file system takes (a NUL in the folder a
            # damaged job names) - failed here, not raised out of the tick.
            self._let_go(j)
            j["status"] = "failed"
            j["error"] = str(e)
            return
        secs = (max(1, int(j["stop"] - time.time()))
                if j.get("stop") else None)
        through = [] if feed else self._certificate_options(kind, url, src, bridged=read != url)
        if not feed and not (listed and kind == "ffmpeg"):
            # The RTSP transport a SAT>IP source asks for (ffmpeg's goes in
            # its list with an address read from one).
            through = [*self._rtsp_options(j, kind), *through]
        if kind == "ffmpeg":
            if feed:
                # Its input only: no network protocol at all for ffmpeg.
                way_in = ["-protocol_whitelist", "pipe", "-i", "pipe:0"]
            elif listed:
                # The list on this computer, what it names a stream's.
                way_in = ["-f", "concat", "-safe", "0", "-protocol_whitelist",
                          "file," + FFMPEG_PROTOCOLS, "-i", listed]
            else:
                # ffmpeg_protocols: what the address leads to (a redirect, a
                # playlist's segments) stays a stream's too - through the
                # bridge only what it needs.
                way_in = [*self._input_options(url), *ffmpeg_protocols(read, bridged=read != url),
                          *through, "-i", read]
            cmd = [exe, "-y", "-loglevel", "error", *way_in, "-c", "copy"]
            if secs:
                cmd += ["-t", str(secs)]
            cmd.append(path)
        else:
            cmd = [exe, f"--stream-record={path}", "--vo=null",
                   "--ao=null", "--no-terminal", "--ytdl=no", f"--user-agent={RECORD_UA}"]
            cmd += through
            if secs:
                cmd.append(f"--length={secs}")
            # The address last, after "--": a playlist's line that starts
            # with a dash is a file that does not open, never an option of
            # mpv's (media/players.player_command). "-": its input.
            cmd += [f"--playlist={listed}"] if listed else ["--", "-" if feed else read]
        try:
            j["proc"] = subprocess.Popen(
                cmd, stdin=subprocess.PIPE if feed else subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, creationflags=NO_WINDOW,
                bufsize=0, env=env)
            if feed:
                from .nerd import checks_certificate
                j["_feed"] = Feed(j["proc"], url, src, live=live_address(url),
                                  strict=checks_certificate(src, url))
                j["_feed"].start()
                log.info("record: %s - read by the app, handed to %s on its input",
                         for_log(j.get("title")), kind)
            elif read != url:
                log.info("record: %s - %s reads it through the app's bridge, under an alias",
                         for_log(j.get("title")), kind)
            j["path"] = path
            j["status"] = "recording"
            if j.get("stream_icon") or j.get("channel"):
                self.write_info(path, "", "", icon=j.get("stream_icon") or "",
                                channel=j.get("channel") or "", start=time.time())
        except Exception as e:
            self._let_go(j)
            j["status"] = "failed"
            j["error"] = str(e)

    @staticmethod
    def _proxy_for(src: dict | None, url: str) -> str:
        """Nerd's proxy for a stream the recorder reads through the bridge,
        as the player and a recording's own connection take it (while
        "streams through the proxy too" is on). The bridge goes through an
        http proxy only: through another (socks) it connects itself, as the
        recorder always did. With none of Nerd's, the environment's
        http_proxy, which the recorder once read itself (net_bridge
        .system_proxy) - its http_proxy is the bridge now."""
        from . import nerd, net_bridge
        n = nerd.current()
        proxy = n.proxy(src, url) if n.on and n.flag("nerd_proxy_player", True) else ""
        try:
            net_bridge.upstream_of(proxy)
        except ValueError:
            log.info("record: Nerd's proxy is no http proxy - the recorder's bridge connects itself")
            return ""
        return proxy or net_bridge.system_proxy(url)

    @staticmethod
    def _not_started(j: dict, why: str) -> None:
        j["status"] = "failed"
        j["error"] = why
        log.warning("record: %s not started - %s", for_log(j.get("title")), why)

    @staticmethod
    def _let_go(j: dict) -> None:
        """What a recording held to hand its recorder the address: the
        bridge's alias forgotten, the private list deleted."""
        held = j.pop("_alias", None)
        if held is not None:
            held[0].forget(held[1])
        listed = j.pop("_listed", None)
        if listed:
            drop_file(listed)

    def _private_list(self, kind: str, exe: str, url: str, j: dict) -> str:
        """A private file the recorder reads *url* from (player_exec
        .private_file): ffmpeg's concat list with the input's own options -
        an ffmpeg older than 5 knows no options there, and then reads it with
        its own defaults (said in the log) - or mpv's one-entry playlist. ""
        when none can be made."""
        if any(c in url for c in "\r\n"):
            return ""
        if kind != "ffmpeg":
            return private_file(f"#EXTM3U\n{url}\n", _LIST_PREFIX, ".m3u")
        options = [*self._input_options(url), *self._rtsp_options(j, kind)]
        major = ffmpeg_major(exe)
        if options and major is not None and major < 5:
            log.info("record: ffmpeg %d reads a list without its own options - %s left to "
                     "its defaults", major, " ".join(options[0::2]))
            options = []
        return private_file(concat_list(url, options), _LIST_PREFIX, ".ffconcat")

    def _certificate_options(self, kind: str, url: str, src: dict | None,
                             bridged: bool) -> list[str]:
        """The recorder's arguments for *url*'s certificate, as the player
        checks it (core/nerd.strict_tls_recorder). Through the bridge
        (*bridged*) ffmpeg reads the alias over plain http to the bridge,
        which makes the TLS connection itself with the stream's own check
        (core/net_bridge.Alias.trust) - and refuses a TLS option it has no
        use for - so it takes none; mpv's apply to what else it opens (an
        HLS segment on another https host, through a tunnel), and where the
        source pins the stream's host the bridge checks the pin and mpv the
        chain no more (net_bridge.checks_pin)."""
        from . import net_bridge
        from .nerd import strict_tls_recorder
        if bridged and kind == "ffmpeg":
            return []
        if bridged and net_bridge.checks_pin(src, url):
            return ["--tls-verify=no"]
        return strict_tls_recorder(kind, url, src)

    def _source_of(self, j: dict) -> dict | None:
        """The source a recording was made from, as core/nerd.set_source
        keeps one (its options and kind) with its server (``_server``,
        make_client's form - where it is: core/net_bridge.policy_for), read
        again as it is now; the one playing when the job names none or it
        is gone."""
        from . import nerd
        pid = str(j.get("source") or "")
        if pid:
            try:
                from .stores import PlaylistStore
                pl = PlaylistStore(self.settings).get(pid)
            except Exception as e:
                log.debug("record: no source %s: %s", pid, e)
                pl = None
            if pl:
                return dict(pl.get("options") or {}, _kind=pl.get("kind") or "xtream",
                            _server=str(pl.get("server") or ""))
        return nerd.source()

    def _rtsp_options(self, j: dict, kind: str) -> list[str]:
        """The RTSP transport a SAT>IP source asks for, for a recording of
        one of its RTSP addresses (core/rtsp.py) - the source it was made
        from read again, as it is now; none for anything else."""
        from . import rtsp
        url = str(j.get("url") or "")
        if j.get("source_kind") not in rtsp.KINDS or not rtsp.is_rtsp(url):
            return []
        src: dict = {"_kind": j.get("source_kind")}
        pid = str(j.get("source") or "")
        if pid:
            try:
                from .stores import PlaylistStore
                pl = PlaylistStore(self.settings).get(pid)
            except Exception as e:
                log.debug("record: no source %s for its transport: %s", pid, e)
                pl = None
            if pl:
                src = dict(pl.get("options") or {}, _kind=pl.get("kind") or "xtream")
        return rtsp.ffmpeg_options(src, url) if kind == "ffmpeg" else rtsp.mpv_args(src, url)

    @staticmethod
    def _input_options(url: str) -> list[str]:
        """ffmpeg's HTTP input options for *url*.

        The same User-Agent the player sends: panels that accept the player
        refuse ffmpeg's own "Lavf", and a recording that started fine failed
        at once. Reconnects keep a live recording going over the dropped
        connections these panels hand out; not for an archive window, where
        a reconnect restarts the stretch from its beginning. Only options
        every ffmpeg in use knows - an unknown one fails the whole command.
        A local file takes none of them."""
        if "://" not in url:
            return []
        opts = ["-user_agent", RECORD_UA]
        path = url.split("?", 1)[0].lower()
        if url.lower().startswith("http") and not (
                "/timeshift/" in path or "timeshift.php" in path):
            opts += ["-reconnect", "1", "-reconnect_streamed", "1",
                     "-reconnect_delay_max", "5"]
        return opts

    @staticmethod
    def _stop_proc(j: dict) -> None:
        feed = j.pop("_feed", None)
        if feed is not None:
            feed.stop()             # the provider's connection first
        p = j.get("proc")
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        RecordingManager._let_go(j)

    def _max_bytes(self) -> int:
        """Size cap for recordings (0 = no cap). Session override wins."""
        if self.session_cap is not None:
            return self.session_cap
        try:
            val = float(self.settings.value("rec_max_value", 0) or 0)
        except (TypeError, ValueError):
            return 0
        mult = {"MB": 10**6, "GB": 10**9, "TB": 10**12}.get(
            self.settings.value("rec_max_unit", "GB"), 10**9)
        return int(val * mult) if val > 0 else 0

    def total_cap_bytes(self) -> int:
        """User's total size limit for the whole recordings folder
        (0 = unlimited)."""
        try:
            val = float(self.settings.value("rec_total_value", 0) or 0)
        except (TypeError, ValueError):
            return 0
        mult = {"MB": 10**6, "GB": 10**9, "TB": 10**12}.get(
            self.settings.value("rec_total_unit", "GB"), 10**9)
        return int(val * mult) if val > 0 else 0

    def folder_used_bytes(self) -> int:
        """Bytes currently occupied by recordings in the folder."""
        return sum(int(f.get("_size") or 0) for f in self.files(None))

    def total_cap_exceeded(self) -> bool:
        """True when a total cap is set and the folder already meets/exceeds
        it - a new recording should be refused."""
        cap = self.total_cap_bytes()
        return cap > 0 and self.folder_used_bytes() >= cap

    def _over_size_cap(self, j: dict, cap: int) -> bool:
        try:
            return (cap > 0 and bool(j.get("path"))
                    and os.path.exists(j["path"])
                    and os.path.getsize(j["path"]) >= cap)
        except OSError:
            return False

    def tick(self) -> None:
        now = time.time()
        cap = self._max_bytes()
        changed = False
        for j in self.jobs:
            if j["status"] == "scheduled" and j.get("_asking") is not None:
                # Its address is being asked for.
                if time.monotonic() - j["_asking"] > self.ADDRESS_TIMEOUT_S:
                    self._no_address(j, "no answer in time")
                    self._save()
                    changed = True
            elif j["status"] == "scheduled" and j["start"] <= now:
                if j.get("stop") is not None and j["stop"] <= now:
                    j["status"] = "failed"
                    if j.pop("_held", None):
                        j["error"] = self.NO_ROOM
                        log.info("record: %s not started - %s", for_log(j.get("title")),
                                 self.NO_ROOM)
                    else:
                        j["error"] = "stop time passed before the app could start it"
                else:
                    way = self._way_in(j)
                    if way == self.HOLD:
                        if j.get("_held"):
                            continue        # still waiting: nothing new
                        j["_held"] = True
                        log.info("record: %s waits for a free connection to the account",
                                 for_log(j.get("title")))
                    elif way == self.RIDE:
                        pass                # the window made it one in the player
                    elif self.address_later(j):
                        j.pop("_held", None)
                        self._ask_address(j)
                    else:
                        j.pop("_held", None)
                        self._spawn(j)
                self._save()
                changed = True
            elif j["status"] == "recording" and j.get("inplayer"):
                if j.get("stop") is not None and j["stop"] <= now:
                    self.finish_inplayer(j["id"], "finished")
                elif self._over_size_cap(j, cap):
                    self.finish_inplayer(j["id"], "size limit reached")
            elif j["status"] == "recording" and j.get("recorder") is not None:
                # From the rewind buffer, in the background.
                rec = j["recorder"]
                if j.get("stop") is not None and j["stop"] <= now:
                    self._finish_buffer(j, "finished")
                    changed = True
                elif self._over_size_cap(j, cap):
                    self._finish_buffer(j, "size limit reached")
                    changed = True
                elif not rec.alive:
                    # Its buffer went (the app's buffer turned off, ...).
                    self._finish_buffer(j, "the buffer ended")
                    changed = True
            elif j["status"] == "recording":
                rc = j["proc"].poll() if j.get("proc") else 0
                if ((j.get("stop") is not None and j["stop"] <= now)
                        or self._over_size_cap(j, cap)):
                    self._stop_proc(j)
                    j["status"] = "done"
                    self.recording_stopped.emit(
                        j["title"],
                        "size limit reached" if self._over_size_cap(j, cap)
                        else "finished")
                    self._after(j)
                    changed = True
                elif rc is not None:
                    feed = j.get("_feed")
                    if feed is not None and feed.alive:
                        feed.stop()
                        continue        # its last word is still on its way
                    j.pop("_feed", None)
                    self._let_go(j)
                    if feed is not None and feed.classic and not j.get("_classic"):
                        # An HLS playlist after all, or a certificate the
                        # app cannot check: the recorder reads it itself,
                        # through the bridge under an alias.
                        log.info("record: %s - %s; the recorder reads it itself, through "
                                 "the app's bridge", for_log(j.get("title")), feed.classic)
                        self._drop_empty(j.get("path"))
                        j["_classic"] = True
                        self._spawn(j)
                        changed = True
                        continue
                    ok = (rc == 0 and j.get("path")
                          and os.path.exists(j["path"])
                          and os.path.getsize(j["path"]) > 0)
                    j["status"] = "done" if ok else "failed"
                    if ok:
                        self._after(j)
                    else:
                        j["error"] = f"recorder exited early (code {rc})"
                    self.recording_stopped.emit(
                        j["title"], "finished" if ok
                        else "recorder stopped unexpectedly")
                    changed = True
        if changed:
            self.jobs_changed.emit()

    def shutdown(self) -> None:
        """The app closes: every recording ends. The command after a
        recording runs for them in after_shutdown(), once the player has
        let go of its file - their usual check, 1.5 s later, never comes."""
        self._ended_on_quit = [j for j in self.jobs if j["status"] == "recording"]
        # MP4s being written are ended: each keeps its .ts and is done at
        # the next start (rec_mp4's journal), as is any due from now on.
        self._closing = True
        stopped = rec_mp4.stop_all()
        if stopped:
            log.info("recording: %d MP4(s) left for the next start", stopped)
        self.finish_all_inplayer("app closed")
        for j in self.jobs:
            if j["status"] == "recording" and j.get("recorder") is not None:
                j["recorder"].stop()
                j["status"] = "done"
            elif j["status"] == "recording" and not j.get("inplayer"):
                self._stop_proc(j)
                j["status"] = "done"
        self._save()

    #: How long the app's closing waits, in all, for the rewind buffer's
    #: recordings to write their last bytes before their command runs.
    QUIT_WAIT = 1.0

    def after_shutdown(self) -> None:
        """The command after a recording, for each one the closing ended
        and that holds data (started on its own, as always: the app does
        not wait for it). A buffer's recording still writing after
        QUIT_WAIT is left without it rather than hold the closing up."""
        ended, self._ended_on_quit = self._ended_on_quit, []
        deadline = time.monotonic() + self.QUIT_WAIT
        for j in ended:
            rec = j.get("recorder")
            if rec is not None:
                rec.join(max(0.0, deadline - time.monotonic()))
                if rec.alive:
                    continue
            try:
                self._after(j)
            except Exception as e:      # never in the way of closing
                log.warning("recording: the command after it failed: %s", e)
