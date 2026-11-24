"""Recording manager: ffmpeg/mpv stream-copy and in-player recording."""

from __future__ import annotations

import hashlib
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
                          concat_list, drop_file, fetchable, ffmpeg_protocols,
                          find_player_executable, find_program, handed_privately, private_file)
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


def connections_network(url: str) -> bool:
    """A recording of *url* holds a connection to a provider (core/connections)."""
    from .connections import is_network
    return is_network(url)


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
NO_PRIVATE_FILE = "its address may hold a login and no private file for it could be made"
#: Why one stopped while less than DISK_FLOOR was left where it is written:
#: a stream with no end and no size cap never fills the disk.
DISK_FULL = "the disk the recordings are saved on is nearly full"
#: Why one that came due was not started while offline mode is on
#: (core/offline.py): its source is on the internet.
NO_NETWORK = "no network (offline mode)"
DISK_FLOOR = 1024 ** 3
_LIST_PREFIX = "dopeiptv-rec-"


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


#: What a job keeps only while this process runs - never in the settings.
_TRANSIENT = ("proc", "recorder", "_asking", "_held", "_go_ahead", "_feed", "_classic",
              "_alias", "_listed", "_elsewhere", "_claim_state", "_claim_info", "_opening",
              "_since", "_own", "_from_claim", "_after_ran", "_was_elsewhere", "_carry_noted",
              "_carrying")
#: What a later edit of a scheduled job changes (``edited`` says when).
_EDITABLE = ("start", "stop", "title", "folder", "prog_start", "prog_stop", "url",
             "edited")


def _stamp_of(j: dict) -> float:
    try:
        return float(j.get("edited") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _clock(ts: float) -> str:
    """*ts* as this computer's time of day ("21:13")."""
    try:
        return datetime.fromtimestamp(ts).strftime("%H:%M")
    except (OverflowError, OSError, ValueError):
        return "?"


class RecordingManager(QObject):
    """Records live streams to local files.

    Uses ffmpeg (stream copy, no re-encode) when available, otherwise
    mpv's --stream-record.  Scheduled-but-not-yet-started jobs survive
    an app restart (persisted via QSettings).

    With *claims* (core/rec_claims.py) it shares the scheduled jobs with
    the other recorder of this household - the window and ``dopeiptv
    --server`` (services/server_recorder.py) may both run: the list of jobs
    is read again when the other wrote it and merged as it is written
    (``_merge``), a job is recorded by whoever claims it as it comes due
    (``_claim``), a job the other records shows here as recording - by it
    (``_elsewhere``) - and a stop asked here reaches it (``cancel``).

    A scheduled recording the window records as it closes goes on in a
    `dopeiptv --server` that runs (``shutdown``'s *carry*, chosen by
    core/rec_close.py): its connection here ends, the job goes back on the
    list with what it was (``CARRY``: where it left off, its first part's
    file), and once nothing of the window's reads the stream any more its
    claim is let go (``let_go_carried``) - the server records the rest into
    a recording of its own, both parts saying so in their info.
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

    #: How often the claims' folder is tidied (rec_claims.Claims.sweep).
    SWEEP_S = 3600.0
    #: A recording carried on by the server (shutdown's *carry*) keeps this
    #: on the list: {"at": when the window let go, "part": its first part's
    #: file name} - read as from outside (carried_from).
    CARRY = "carry"

    def __init__(self, settings: QSettings, parent: QObject | None = None,
                 claims: Any = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.jobs: list[dict] = []
        #: core/rec_claims.Claims: who records each scheduled job, the window
        #: or the server; None - this recorder alone, as before.
        self.claims = claims
        self.who = str(getattr(claims, "who", "") or "")
        #: (the server) True while a window runs: it takes no new job then.
        self.defer: Callable[[], bool] | None = None
        #: (the server) Whether it takes job *j* at all (one of the
        #: household's sources); None: every one.
        self.takes: Callable[[dict], bool] | None = None
        #: (the server) Given a job due now with its address, a recording of
        #: it read from a rewind buffer session - (its file, its recorder) -
        #: or None for the recorder's own connection as before.
        self.open_shared: Callable[[dict], tuple[str, Any] | None] | None = None
        #: The ids the list of jobs held when it was last read or written.
        self._stored: set[str] = set()
        self._stamp = ""
        self._swept = 0.0
        #: The server records nothing without its claims; the window as before.
        self._no_claims = ""
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
        #: Shut down (shutdown()): nothing starts any more.
        self._shut = False
        #: The carried jobs whose claims wait for let_go_carried().
        self._carried: list[str] = []
        self._remuxed.connect(self._remux_done, Qt.ConnectionType.QueuedConnection)
        if claims is not None:
            self._share_start()
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
                # A download in a folder under the recordings' is no
                # recording (core/downloads.py's sidecar says it).
                if (os.path.splitext(n)[1].lower() in self.VIDEO_EXTS
                        and os.path.isfile(p) and not os.path.exists(p + ".dl.json")):
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

    def _stored_jobs(self) -> list[dict]:
        """The scheduled jobs the settings hold, each with what the
        scheduler reads - a hand-edited or damaged entry would raise in
        tick() every five seconds for the rest of the session."""
        try:
            data = json.loads(
                self.settings.value("recording_jobs", "") or "[]")
        except Exception:
            data = []
        now = time.time()
        out = []
        for j in data if isinstance(data, list) else []:
            if (not isinstance(j, dict)
                    or not (j.get("url") or self.address_later(j))
                    or not isinstance(j.get("start"), (int, float))
                    or not isinstance(j.get("stop"), (int, float, type(None)))):
                continue
            if (j.get("status") == "scheduled"
                    and (j.get("stop") is None or j["stop"] > now)):
                if not j.get("id"):
                    # The same every time it is read: the other recorder's
                    # merge must know it again.
                    j["id"] = hashlib.sha256(json.dumps(
                        j, sort_keys=True, default=str).encode()).hexdigest()[:10]
                for key, default in (("title", ""), ("folder", ""),
                                     ("path", ""), ("error", ""),
                                     ("stream_icon", "")):
                    j.setdefault(key, default)
                j["id"] = str(j["id"])
                for key in _TRANSIENT:
                    j.pop(key, None)
                j["proc"] = None
                out.append(j)
        return out

    def _load(self) -> None:
        self.jobs.extend(self._stored_jobs())
        self._stored = {j["id"] for j in self.jobs}

    @staticmethod
    def _saved(j: dict) -> dict:
        return {k: v for k, v in j.items() if k not in _TRANSIENT}

    def _save(self) -> None:
        if self.claims is not None and not self._no_claims:
            try:
                with self.claims.jobs_lock():
                    self._merge(write=True)
                return
            except Exception as e:      # never in a recording's way: written as before
                log.warning("record: the jobs not merged with the other recorder's: %s", e)
        keep = [self._saved(j) for j in self.jobs
                if j.get("status") == "scheduled" and not j.get("_elsewhere")]
        self.settings.setValue("recording_jobs", json.dumps(keep))

    # -- the jobs shared with the other recorder (core/rec_claims.py) --------------------------

    def _merge(self, write: bool) -> bool:
        """This recorder's jobs and the list the other wrote, as one: a job
        new there taken in here; a later edit of one taken in; one taken out
        of the list there - cancelled, or started by the other - known as
        that here (_gone_elsewhere). With *write*, the list as it is now
        stored, and the other told (the stamp). Whether the jobs here
        changed."""
        try:
            self.settings.sync()
        except Exception as e:
            log.debug("record: the settings not read again: %s", e)
        theirs = self._stored_jobs()
        t_ids = {s["id"] for s in theirs}
        ours = {str(j["id"]): j for j in self.jobs}
        out: list[dict] = []
        changed = False
        for s in theirs:
            j = ours.get(s["id"])
            if j is None:
                if s["id"] in self._stored:
                    continue                # taken out of the list here
                self.jobs.append(s)
                ours[s["id"]] = s
                out.append(self._saved(s))
                changed = True
            elif j.get("_elsewhere"):
                # The other's to say - but a later edit here of one it holds.
                newer = j.get("status") == "scheduled" and _stamp_of(j) > _stamp_of(s)
                self._carried_in(j, s)
                out.append(self._saved(j if newer else s))
            elif j.get("status") == "scheduled":
                if _stamp_of(s) > _stamp_of(j):
                    for k in _EDITABLE:
                        if k in s:
                            j[k] = s[k]
                    j.pop("_held", None)
                    changed = True
                changed = self._carried_in(j, s) or changed
                out.append(self._saved(j))
            # Started, finished or cancelled here: out of the list.
        for j in list(self.jobs):
            if (j.get("status") != "scheduled" or j.get("_elsewhere")
                    or str(j["id"]) in t_ids):
                continue
            if str(j["id"]) in self._stored:
                self._gone_elsewhere(j)
                changed = True
            else:
                out.append(self._saved(j))  # new here
        if write:
            self.settings.setValue("recording_jobs", json.dumps(out))
            try:
                self.settings.sync()
                self._stamp = self.claims.touch_jobs()
            except Exception as e:
                log.debug("record: the other recorder not told of the jobs: %s", e)
            self._stored = {str(s["id"]) for s in out}
        else:
            self._stored = t_ids
        return changed

    def _carried_in(self, j: dict, s: dict) -> bool:
        """A recording the other recorder carries on to this one (its window
        closed while it recorded it): what it was, from the list - where it
        left off, its first part, and the address a job known only from its
        claim lacks. Whether *j* changed."""
        carry = s.get(self.CARRY)
        if not isinstance(carry, dict) or j.get(self.CARRY) == carry:
            return False
        j[self.CARRY] = carry
        if not j.get("url") and s.get("url"):
            j["url"] = s["url"]
        if self.LATER not in j and isinstance(s.get(self.LATER), dict):
            j[self.LATER] = s[self.LATER]
        return True

    def _gone_elsewhere(self, j: dict) -> None:
        """Scheduled job *j* left the list the other wrote: it started
        there (its claim held), it ended there, or it was cancelled there."""
        from . import rec_claims
        try:
            mine = self.claims.mine(j["id"])
            c = None if mine else self.claims.read(j["id"])
        except Exception as e:
            log.debug("record: a claim not read: %s", e)
            return                      # not known: never taken for a cancel
        if c is not None and c.live:
            self._mark_elsewhere(j, c)
            return
        if c is not None and c.final:
            self._ended_elsewhere(j, c)
            return
        stop = j.get("stop")
        if isinstance(stop, (int, float)) and stop <= time.time():
            return                      # over: its tick says so
        j["status"] = "cancelled"
        if mine:
            try:
                self.claims.finish(j["id"], rec_claims.CANCELLED, self._claim_info(j))
            except Exception as e:
                log.debug("record: a claim not finished: %s", e)
        log.info("record: %s cancelled by the other recorder", for_log(j.get("title")))

    def _mark_elsewhere(self, j: dict, c: Any) -> None:
        """*j* is the other recorder's now: recording, or waiting there."""
        from . import rec_claims
        was = j.get("_elsewhere")
        j["_was_elsewhere"] = True          # its ending is the other's to tell
        j["_elsewhere"] = {"who": c.who, "state": c.state,
                           "own": bool(c.info.get("own", True)),
                           "private": bool(c.info.get("private", False))}
        if c.state == rec_claims.RECORDING:
            j["status"] = "recording"
            j.pop("_held", None)
            if c.info.get("path"):
                j["path"] = c.info["path"]
            if c.info.get("since"):
                j["_since"] = c.info["since"]
        else:
            j["status"] = "scheduled"
            j["_held"] = True
        if was is None:
            log.info("record: %s - %s by the %s", for_log(j.get("title")),
                     "recorded" if c.state == rec_claims.RECORDING else "held",
                     c.who or "other recorder")

    def _ended_elsewhere(self, j: dict, c: Any) -> None:
        j.pop("_elsewhere", None)
        j.pop("_held", None)
        j["status"] = c.state
        j["error"] = str(c.info.get("error") or "")
        if c.info.get("path"):
            j["path"] = c.info["path"]

    def _from_claim(self, c: Any) -> dict:
        """A job known only from the other recorder's claim (it started
        before this one ran): shown, and - should its recorder stop
        running - taken over by its channel."""
        info = c.info
        j: dict[str, Any] = {
            "id": c.job, "url": "", "title": str(info.get("title") or ""),
            "start": float(info.get("start") or time.time()), "stop": info.get("stop"),
            "folder": "", "status": "scheduled", "path": str(info.get("path") or ""),
            "error": "", "proc": None, "stream_icon": str(info.get("stream_icon") or ""),
            "channel": str(info.get("channel") or ""), "_from_claim": True}
        source, sid = str(info.get("source") or ""), str(info.get("stream_key") or "")
        if source:
            j["source"] = source
        if sid:
            j["stream_key"] = sid
            if source:
                j[self.LATER] = {"source": source, "stream_id": sid}
        return j

    def _claim_info(self, j: dict) -> dict:
        """What a claim says of *j* for the other recorder (no address)."""
        from . import privacy
        from .log import for_people
        info: dict[str, Any] = {k: j.get(k) for k in ("title", "channel", "source", "path")}
        # A logo with a login in it stays in the settings; an error says no
        # address but its server's name (core/log.for_people).
        icon = str(j.get("stream_icon") or "")
        info["stream_icon"] = "" if carries_login(icon) or "@" in icon.split("?")[0] else icon
        info["error"] = for_people(str(j.get("error") or ""))
        info["start"], info["stop"] = j.get("start"), j.get("stop")
        info["stream_key"] = str(j.get("stream_key")
                                 or (j.get(self.LATER) or {}).get("stream_id") or "")
        info["own"] = bool(j.get("_own"))
        info["since"] = j.get("_since")
        # What the player plays privately is no other's to name.
        info["private"] = bool(j.get("inplayer")) and privacy.playing_private()
        return info

    def _claims_off(self, e: BaseException) -> None:
        """The claims cannot be had: said once. The window records as it
        always did; the server records nothing (it cannot know what the
        window records)."""
        if self._no_claims:
            return
        self._no_claims = str(e) or type(e).__name__
        if self.who == "server":
            log.warning("record: the recordings' claims cannot be kept (%s) - the server "
                        "records nothing", self._no_claims)
        else:
            log.warning("record: the recordings' claims cannot be kept (%s) - recording as "
                        "before; a `dopeiptv --server` records nothing meanwhile",
                        self._no_claims)

    def _share_start(self) -> None:
        try:
            self.claims.ensure()
            # Each says it runs: the server leaves what comes due to a window
            # that does, and a window closing carries its recordings on to a
            # server that does (core/rec_close.py).
            self.claims.hold_presence()
            self.claims.sweep()
            self._swept = time.monotonic()
            self._stamp = self.claims.jobs_stamp()
            self._share_look()
        except Exception as e:
            self._claims_off(e)

    def _share_tick(self) -> bool:
        """(each tick) The claims held touched, the list read again when
        the other wrote it, a stop the other asked of a recording here, and
        what the other records. Whether the jobs changed."""
        if self.claims is None or self._no_claims:
            return False
        changed = False
        try:
            self.claims.beat()
            stamp = self.claims.jobs_stamp()
            if stamp != self._stamp:
                with self.claims.jobs_lock():
                    changed = self._merge(write=False)
                self._stamp = stamp
            changed = self.check_stops() or changed
            changed = self._share_look() or changed
            if time.monotonic() - self._swept > self.SWEEP_S:
                self._swept = time.monotonic()
                self.claims.sweep()
        except OSError as e:
            log.debug("record: the claims not looked at: %s", e)
        except Exception:               # never out of the tick
            log.warning("record: the claims not looked at", exc_info=True)
        return changed

    def check_stops(self) -> bool:
        """A stop the other recorder asked of a recording here (its Stop
        recording): stopped. Whether one was."""
        if self.claims is None or self._no_claims:
            return False
        stopped = False
        for j in list(self.jobs):
            try:
                asked = (j.get("status") == "recording" and not j.get("_elsewhere")
                         and self.claims.mine(j["id"]) and self.claims.stop_asked(j["id"]))
            except OSError:
                asked = False
            if asked:
                log.info("record: %s - the other recorder asked to stop it",
                         for_log(j.get("title")))
                self.cancel(j["id"])
                stopped = True
        return stopped

    def _share_look(self) -> bool:
        """What the other recorder records or holds, as its claims say:
        each job of it marked so (or ended, or free again - its recorder
        gone: then this one takes it), one known only from its claim added.
        Whether the jobs changed."""
        from . import rec_claims
        # A recording's, not an MP4 being saved (_remux_ours).
        others = {c.job: c for c in self.claims.others() if not c.job.startswith("mp4:")}
        changed = False
        for j in list(self.jobs):
            el = j.get("_elsewhere")
            if el is None:
                continue
            c = others.get(str(j["id"]))
            if c is None:
                c = self.claims.read(j["id"])
            if c is not None and c.live and not self.claims.mine(j["id"]):
                if c.state != el.get("state") or (c.info.get("path") and
                                                  c.info["path"] != j.get("path")):
                    self._mark_elsewhere(j, c)
                    changed = True
            elif c is not None and c.final:
                self._ended_elsewhere(j, c)
                changed = True
            else:
                # Let go there, or its recorder is gone: this one's to take
                # as it comes due (the rest of it, from its channel).
                j.pop("_elsewhere", None)
                j.pop("_was_elsewhere", None)
                j.pop("_held", None)
                j["status"] = "scheduled"
                if not (j.get("url") or self.address_later(j)):
                    j["status"], j["error"] = "failed", "recorder stopped unexpectedly"
                log.info("record: %s - %s", for_log(j.get("title")),
                         f"the {el.get('who') or 'other recorder'} let it go" if c is None
                         else f"the {el.get('who') or 'other recorder'} that recorded it no "
                              "longer runs")
                changed = True
        known = {str(j["id"]) for j in self.jobs}
        for job, c in others.items():
            if job not in known and c.state in rec_claims.LIVE:
                j = self._from_claim(c)
                self.jobs.append(j)
                self._mark_elsewhere(j, c)
                changed = True
        return changed

    def _claim(self, j: dict) -> bool:
        """Due job *j* claimed for this recorder: True when it may go on -
        False when the other records it, holds it, or it ended."""
        if self.claims is None:
            return True
        from . import rec_claims
        if self._no_claims:
            return self.who != "server"
        try:
            if self.claims.mine(j["id"]):
                return True
            got, c = self.claims.take(j["id"], self._claim_info(j), rec_claims.HELD)
        except Exception as e:          # the window records as before; the server not
            self._claims_off(e)
            return self.who != "server"
        if got == rec_claims.TAKEN:
            j["_claim_state"] = rec_claims.HELD
            return True
        if got == rec_claims.OTHER and c is not None:
            self._mark_elsewhere(j, c)
        elif c is not None:
            self._ended_elsewhere(j, c)
            self._save()
        return False

    def _claims_settle(self) -> None:
        """Each claim of this recorder's says what its job does now: held,
        recording, or - ended - finished (and let go). A job the server
        holds for a free connection is let go meanwhile: the window, when it
        runs, may record it from its player."""
        if self.claims is None or self._no_claims:
            return
        from . import rec_claims
        for j in self.jobs:
            if j.get("_elsewhere") or "_claim_state" not in j:
                continue
            status = str(j.get("status") or "")
            if status == "recording":
                j.setdefault("_since", time.time())
                want = rec_claims.RECORDING
            elif status == "scheduled":
                want = rec_claims.HELD
            elif status in rec_claims.FINAL:
                want = status
            else:
                continue
            info = self._claim_info(j)
            if want == j["_claim_state"] and info == j.get("_claim_info"):
                continue
            try:
                if want == rec_claims.HELD and self.who == "server" and j.get("_held"):
                    self.claims.free(j["id"])
                    j.pop("_claim_state", None)
                    j.pop("_claim_info", None)
                    continue
                if want in rec_claims.FINAL:
                    self.claims.finish(j["id"], want, info)
                else:
                    self.claims.write(j["id"], want, info)
                j["_claim_state"], j["_claim_info"] = want, info
            except Exception as e:      # never out of the tick
                log.debug("record: a claim not written: %s", e)

    def refresh_shared(self) -> bool:
        """The list of jobs read again now, the other recorder's changes
        taken in (before a look through the guide schedules more)."""
        if self.claims is None or self._no_claims:
            return False
        try:
            with self.claims.jobs_lock():
                changed = self._merge(write=False)
            self._stamp = self.claims.jobs_stamp()
            changed = self._share_look() or changed
        except Exception as e:
            log.debug("record: the jobs not read again: %s", e)
            return False
        if changed:
            self.jobs_changed.emit()
        return changed

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
            "stream_icon": icon or "", "channel": channel or "", "edited": time.time()}
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
                j["edited"] = time.time()   # the newer edit wins (_merge)
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
                    url: str = "", player: bool = True) -> None:
        """Scheduled recording *j*, due now, made from what the player plays
        already - its rewind buffer through *recorder* (a BufferRecorder),
        else its own stream: from now on one in the player, as
        add_inplayer_job's, with no connection of its own. *url*: the
        address it keeps where it had none (a portal's, asked at the start).
        *player* False: the buffer is no player's (the server's restream of
        the channel) - the recording goes on as one in the background.
        The tick that asked (may_open) says the jobs changed."""
        j.pop("_held", None)
        j.pop("_go_ahead", None)
        j.update(status="recording", inplayer=player, path=path, recorder=recorder,
                 proc=None, error="", _own=False)
        if not player:
            j["background"] = True
        if url and not j.get("url"):
            j["url"] = url
        if path and (j.get("stream_icon") or j.get("channel")):
            self.write_info(path, "", "", icon=j.get("stream_icon") or "",
                            channel=j.get("channel") or "", start=time.time())
        log.info("record: %s from what the %s (%s) - no connection of its own",
                 for_log(j.get("title")), "player plays" if player else "server streams",
                 "its rewind buffer" if recorder is not None else "its stream")

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
            if j.get("_elsewhere"):
                self._stop_elsewhere(j)
                return
            if j.get("inplayer") and j["status"] == "recording":
                self.finish_inplayer(job_id)
            elif j["status"] == "recording" and j.get("recorder") is not None:
                self._finish_buffer(j, "stopped")
            elif j["status"] == "recording":
                self._stop_proc(j)
                j["status"] = "done"
            elif j["status"] == "scheduled":
                j["status"] = "cancelled"
                self._cancel_claim(j)
            self._claims_settle()
            self._save()
            self.jobs_changed.emit()
            return

    def _stop_elsewhere(self, j: dict) -> None:
        """A recording the other recorder makes, or holds: asked to stop
        there (its claim tells when it did)."""
        try:
            self.claims.ask_stop(j["id"])
            log.info("record: %s - the %s asked to stop it", for_log(j.get("title")),
                     (j.get("_elsewhere") or {}).get("who") or "other recorder")
        except Exception as e:
            log.warning("record: the other recorder could not be asked to stop %s: %s",
                        for_log(j.get("title")), e)

    def _cancel_claim(self, j: dict) -> None:
        """A scheduled job cancelled here: finished in its claim too, so the
        other recorder never takes it - or, should it have taken it a moment
        ago, asked to stop it."""
        if self.claims is None or self._no_claims or "_claim_state" in j:
            return                      # ours: _claims_settle finishes it
        from . import rec_claims
        try:
            got = self.claims.finish(j["id"], rec_claims.CANCELLED, self._claim_info(j))
            if got == rec_claims.OTHER:
                self._stop_elsewhere(j)
        except Exception as e:
            log.debug("record: a cancel not kept in its claim: %s", e)

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
        if not self._remux_ours(path):
            return
        ff = self.ffmpeg()
        if not ff:
            self._remux_let_go(path)
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

    def _remux_ours(self, path: str) -> bool:
        """Whether this recorder saves *path* as MP4: the other recorder may
        do it already (a remux the last run left, which each one does at its
        start)."""
        if self.claims is None or self._no_claims:
            return True
        from . import rec_claims
        try:
            got, _c = self.claims.take("mp4:" + path, {"path": path}, rec_claims.HELD)
        except Exception as e:
            log.debug("record: the MP4's claim not taken: %s", e)
            return True
        if got != rec_claims.TAKEN:
            log.info("recording: the other recorder saves it as MP4")
            return False
        return True

    def _remux_let_go(self, path: str) -> None:
        if self.claims is not None and not self._no_claims:
            try:
                self.claims.free("mp4:" + path)
            except Exception as e:
                log.debug("record: the MP4's claim not let go: %s", e)

    def _remux_done(self, src: str, final: str, why: str) -> None:
        self._remuxing.discard(src)
        self._remux_let_go(src)
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
        from . import offline
        if offline.on():
            # Its address is the source's to give: not asked while offline
            # mode is on (a box at home's is asked once offline mode ends).
            self._offline_failed(j)
            return
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
        if (self._shut or j is None or j["status"] != "scheduled"
                or j.pop("_asking", None) is None):
            return                      # cancelled, given up or closed meanwhile
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

    def _offline_failed(self, j: dict) -> None:
        j.pop("_asking", None)
        j["status"] = "failed"
        j["error"] = NO_NETWORK
        log.info("record: %s not started - %s", for_log(j.get("title")), NO_NETWORK)

    def _spawn(self, j: dict) -> None:
        from . import nerd, offline
        if offline.stream_refusal(str(j.get("url") or "")):
            # Offline mode (core/offline.py): no connection to the internet
            # - a recording of a source there fails, saying why.
            self._offline_failed(j)
            return
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
        if self.open_shared is not None and not j.get("_classic"):
            # The server: read from a buffer session of the channel, whose
            # one connection every device watching it shares.
            try:
                shared = self.open_shared(j)
            except Exception as e:
                log.info("record: %s not from a buffer (%s)", for_log(j.get("title")), e)
                shared = None
            if shared is not None:
                path, recorder = shared
                j.update(status="recording", path=path, recorder=recorder, proc=None,
                         error="", background=True, _own=True)
                if j.get("stream_icon") or j.get("channel"):
                    self.write_info(path, "", "", icon=j.get("stream_icon") or "",
                                    channel=j.get("channel") or "", start=time.time())
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
        # - every other network address but a multicast group's, and one
        #   with a login in it (rtsp://user:pw@...), in a private file:
        #   ffmpeg's concat list, mpv's playlist (_goes_privately);
        # - a SAT>IP tuning, udp, a file - and an address an ffmpeg older
        #   than 5 would lose its options for - as before.
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
        elif not web and self._goes_privately(kind, exe, url, j):
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
            j["_own"] = connections_network(url)
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

    def _goes_privately(self, kind: str, exe: str, url: str, j: dict) -> bool:
        """Whether the recorder reads *url* (no http(s) one) from a private
        file: an address with a login the app sees always, and every other
        network address but a multicast group's (player_exec
        .handed_privately) - a token may stand where carries_login cannot
        see it. Two stay an argument, as before: a SAT>IP tuning address,
        which holds the tuning and nothing else, and one an ffmpeg older
        than 5 would read from a list without its own options."""
        if carries_login(url):
            return True
        if not handed_privately(url):
            return False
        from . import rtsp
        if j.get("source_kind") in rtsp.KINDS:
            return False
        if kind != "ffmpeg" or not [*self._input_options(url), *self._rtsp_options(j, kind)]:
            return True
        major = ffmpeg_major(exe)
        return major is None or major >= 5

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

    @staticmethod
    def _disk_low(j: dict) -> bool:
        """Less than DISK_FLOOR left on the disk *j* is written to."""
        path = j.get("path")
        if not path:
            return False
        try:
            return shutil.disk_usage(os.path.dirname(path) or ".").free < DISK_FLOOR
        except OSError:
            return False

    def _limit_of(self, j: dict, cap: int) -> str:
        """Why a running recording must stop now: its size cap or a disk
        nearly full ("" for neither)."""
        if self._over_size_cap(j, cap):
            return "size limit reached"
        return DISK_FULL if self._disk_low(j) else ""

    def _deferring(self) -> bool:
        """(the server) A window runs, which records what comes due."""
        if self.defer is None:
            return False
        try:
            return bool(self.defer())
        except Exception as e:
            log.debug("record: whether a window runs not known: %s", e)
            return True                 # not known: the window's, as it would be

    def _takes(self, j: dict) -> bool:
        if self.takes is None:
            return True
        try:
            return bool(self.takes(j))
        except Exception as e:
            log.debug("record: whether to take a job not known: %s", e)
            return False

    def tick(self) -> None:
        if self._shut:
            return                      # closed: nothing starts any more
        now = time.time()
        cap = self._max_bytes()
        changed = self._share_tick()
        deferring = self._deferring()
        for j in self.jobs:
            if j.get("_elsewhere"):
                continue                # the other recorder's (_share_look)
            if j["status"] == "scheduled" and j.get("_asking") is not None:
                # Its address is being asked for.
                if time.monotonic() - j["_asking"] > self.ADDRESS_TIMEOUT_S:
                    self._no_address(j, "no answer in time")
                    self._claims_settle()
                    self._save()
                    changed = True
            elif j["status"] == "scheduled" and j["start"] <= now:
                if "_claim_state" not in j and (deferring or not self._takes(j)):
                    continue            # a window runs: it records what comes due
                if not self._claim(j):
                    changed = True      # the other recorder's, or over
                    continue
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
                self._claims_settle()
                self._save()
                changed = True
            elif j["status"] == "recording" and j.get("inplayer"):
                why = self._limit_of(j, cap)
                if j.get("stop") is not None and j["stop"] <= now:
                    self.finish_inplayer(j["id"], "finished")
                elif why:
                    self.finish_inplayer(j["id"], why)
            elif j["status"] == "recording" and j.get("recorder") is not None:
                # From the rewind buffer, in the background.
                rec = j["recorder"]
                why = self._limit_of(j, cap)
                if j.get("stop") is not None and j["stop"] <= now:
                    self._finish_buffer(j, "finished")
                    changed = True
                elif why:
                    self._finish_buffer(j, why)
                    changed = True
                elif not rec.alive and getattr(rec, "classic", "") and not j.get("_classic"):
                    # What the server's buffer could not read (an HLS
                    # playlist after all): the recorder reads it itself,
                    # through the app's bridge under an alias.
                    log.info("record: %s - %s; the recorder reads it itself, through the "
                             "app's bridge", for_log(j.get("title")), rec.classic)
                    self._drop_empty(j.get("path"))
                    j.update(_classic=True, recorder=None)
                    j.pop("background", None)
                    self._spawn(j)
                    changed = True
                elif not rec.alive:
                    # Its buffer went (the app's buffer turned off, ...).
                    self._finish_buffer(j, "the buffer ended")
                    changed = True
            elif j["status"] == "recording":
                rc = j["proc"].poll() if j.get("proc") else 0
                why = self._limit_of(j, cap)
                if (j.get("stop") is not None and j["stop"] <= now) or why:
                    self._stop_proc(j)
                    j["status"] = "done"
                    self.recording_stopped.emit(j["title"], why or "finished")
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
        self._claims_settle()
        self._note_carried()
        if changed:
            self.jobs_changed.emit()

    def shutdown(self, carry: Any = ()) -> None:
        """The app closes: every recording ends. The command after a
        recording runs for them in after_shutdown(), once the player has
        let go of its file - their usual check, 1.5 s later, never comes.

        *carry*: the ids of scheduled recordings recording here that a
        `dopeiptv --server` running now carries on (core/rec_close.py chose
        them): each one's connection here ends now, what it recorded stays
        a recording of its own, and the job goes back on the list with where
        it left off (CARRY) - its claim held until let_go_carried(), once
        nothing of the window's reads the stream. One that cannot be carried
        ends as before."""
        self._shut = True
        self._timer.stop()
        self._ended_on_quit = [j for j in self.jobs if j["status"] == "recording"]
        # MP4s being written are ended: each keeps its .ts and is done at
        # the next start (rec_mp4's journal), as is any due from now on.
        self._closing = True
        stopped = rec_mp4.stop_all()
        if stopped:
            log.info("recording: %d MP4(s) left for the next start", stopped)
        self._carry_on({str(c) for c in (carry or ())})
        self.finish_all_inplayer("app closed")
        for j in self.jobs:
            if j.get("_elsewhere"):
                continue                # the other recorder's: it goes on
            if j["status"] == "recording" and j.get("recorder") is not None:
                j["recorder"].stop()
                j["status"] = "done"
            elif j["status"] == "recording" and not j.get("inplayer"):
                self._stop_proc(j)
                j["status"] = "done"
        self._claims_settle()
        self._save()
        self._share_end()

    def _share_end(self) -> None:
        """Closing: a job still waiting here let go for the other recorder
        (a `dopeiptv --server` records it as it comes due), every lock let
        go - this recorder no longer runs - but a carried one's, held until
        let_go_carried()."""
        if self.claims is None:
            return
        for j in self.jobs:
            if j.get("status") == "scheduled" and "_claim_state" in j \
                    and not j.get("_carrying"):
                try:
                    self.claims.free(j["id"])
                except Exception as e:
                    log.debug("record: a claim not let go: %s", e)
                j.pop("_claim_state", None)
        try:
            self.claims.let_go_all(keep=self._carried)
        except Exception as e:
            log.debug("record: the claims not let go: %s", e)

    # -- carried on by the server (core/rec_close.py) -----------------------------------

    def _carry_on(self, ids: set[str]) -> None:
        """(shutdown) Each recording of *ids* ended here for the server to
        carry on: its connection - its recorder, the player's stream it
        rides, its own ffmpeg or mpv - ends now; what it recorded is a
        recording of its own, done, its info saying where the rest is; the
        job scheduled again with CARRY, its claim held. One that cannot be
        ended so is left to end as before."""
        if not ids or self.claims is None or self._no_claims:
            return
        for j in list(self.jobs):
            if (str(j.get("id")) not in ids or j.get("status") != "recording"
                    or j.get("_elsewhere") or "_claim_state" not in j):
                continue
            try:
                part = self._hand_over(j)
            except Exception as e:      # ended as before, then
                log.warning("record: %s not carried on by the server (%s) - it stops "
                            "as the window closes", for_log(j.get("title")), e)
                continue
            self._ended_on_quit = [part if k is j else k for k in self._ended_on_quit]
            self._carried.append(str(j["id"]))

    def _hand_over(self, j: dict) -> dict:
        """Job *j*'s connection here ended, its part kept: the part, done,
        for after_shutdown(); *j* scheduled again with CARRY."""
        rec = j.get("recorder")
        if rec is not None:
            rec.stop()                  # from the rewind buffer
        elif j.get("inplayer"):
            if self.stop_inplayer_cb is not None:
                self.stop_inplayer_cb()     # the player's own stream, recorded
        else:
            self._stop_proc(j)          # its own connection, closed now
        at = time.time()
        path = str(j.get("path") or "")
        part = {**j, "status": "done"}
        if path:
            self._note(path, self._words("rec_carried_note", time=_clock(at)))
        for key in ("proc", "recorder", "inplayer", "background", "_feed", "_own", "_since",
                    "_alias", "_listed", "_classic", "_go_ahead", "_held", "_after_ran"):
            j.pop(key, None)
        j.update(status="scheduled", path="", error="", proc=None, _carrying=True, edited=at)
        j[self.CARRY] = {"at": at, "part": os.path.basename(path)[:255]}
        log.info("record: %s - carried on by the server (dopeiptv --server) as the window "
                 "closes: its connection here ended, the rest goes into a recording of its "
                 "own", for_log(j.get("title")))
        return part

    def let_go_carried(self) -> None:
        """(the window's closing, last) What the server carries on let go:
        each carried recording's claim given up - the server takes it at its
        next look and opens its own connection, the window's ended by now -
        and every lock left held for them. Never raises."""
        ids, self._carried = self._carried, []
        if self.claims is None:
            return
        for jid in ids:
            try:
                self.claims.free(jid)
            except Exception as e:
                log.warning("record: a carried recording's claim not let go (%s) - the "
                            "server takes it once this process has ended", e)
        try:
            self.claims.let_go_all()
        except Exception as e:
            log.debug("record: the claims not let go: %s", e)

    @classmethod
    def carried_from(cls, j: dict) -> float | None:
        """When the recorder before stopped, for a job carried on to this
        one (CARRY) - within the job's own times - else None."""
        carry = j.get(cls.CARRY)
        if not isinstance(carry, dict):
            return None
        from .num import number
        at = number(carry.get("at"))
        start, stop = number(j.get("start")), number(j.get("stop"))
        if at is None or start is None or at < start or (stop is not None and at > stop):
            return None
        return float(at)

    def resumes_at(self, j: dict) -> float | None:
        """Where a recording of *j*, due now, begins in a rewind buffer that
        holds the past: where the window left off for one carried on, else
        its start (None: none known)."""
        at = self.carried_from(j)
        if at is not None:
            return at
        from .num import number
        start = number(j.get("start"))
        return float(start) if start else None

    def _note_carried(self) -> None:
        """(each tick) A carried job recording here now: its info says it
        goes on from where the window left off - once."""
        for j in self.jobs:
            if (j.get("status") != "recording" or j.get("_elsewhere")
                    or j.get("_carry_noted") or not j.get("path")):
                continue
            at = self.carried_from(j)
            if at is None:
                continue
            j["_carry_noted"] = True
            part = str((j.get(self.CARRY) or {}).get("part") or "")
            part = "".join(c for c in os.path.basename(part.replace("\\", "/"))
                           if c >= " ")[:255]
            self._note(str(j["path"]), self._words("rec_carry_note", time=_clock(at),
                                                   part=part or "?"))

    def _note(self, path: str, text: str) -> None:
        """*text* added to recording *path*'s description (its sidecar)."""
        try:
            info = self.read_info(path)
            desc = str(info.get("description") or "").strip()
            self.write_info(path, str(info.get("title") or ""),
                            f"{desc}\n\n{text}" if desc else text)
        except Exception as e:          # never in a recording's way
            log.debug("record: a recording's info not written: %s", e)

    @staticmethod
    def _words(key: str, **kw: Any) -> str:
        """The app's words for *key* (the window's language; a server's its
        settings' - headless.py sets it)."""
        try:
            from ..i18n import tr
            return tr(key, **kw)
        except Exception:
            return key

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
