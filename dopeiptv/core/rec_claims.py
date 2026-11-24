"""Who records a scheduled recording: the window, ``dopeiptv --server``, or
another computer's copy of the app that shares the settings folder - each
job by exactly one of them (core/recording.py, services/server_recorder.py).

A **claim** is a small file per job in the app's own folder beside its
settings (``folder_for``: ``recording-claims``, 0700, this user's own), named
by a hash of the job's id - never the id itself, which a backup or a hand
edit could make a path. Whoever records the job holds the file locked with
the system's lock for as long as it does (``take``): ``flock`` on Linux and
macOS, a byte locked far past its end on Windows. A lock dies with its
process, so a crashed recorder's claim is free at once on that computer; the
file says who holds it - ``window`` or ``server``, the process id and when
that process started, the computer's name - and what it does (``state``:
``recording``, ``held`` while it waits for a free connection), with the
job's title, channel, times and file for the other to show. The holder
touches it every few seconds (``beat``): a claim of another computer - where
the lock may not reach through a network share - counts as long as it was
touched within ``STALE_S``. A claim taken over says so in the log.

A job that ends leaves its claim behind, finished (``FINAL``: done, failed,
cancelled) - so the other recorder never records the rest of it again -
until ``KEEP_S`` after its end (``sweep``). A cancel takes the claim and
finishes it, or, when another holds it, asks that one to stop
(``ask_stop``: a ``.stop`` file beside it, seen at its next look).

Beside the claims: ``window.alive``, locked while a window runs (``hold
presence``) - the server takes no new job while one does (``present``) -
and ``server.alive``, locked while a server records: a window that closes
carries on what it records to it (core/rec_close.py), holding those
claims a moment longer than the rest (``let_go_all``'s *keep*);
``jobs.lock``, held for the moment a recorder reads and writes the list of
scheduled jobs (``jobs_lock``: the window waits a moment for it at most and
does its part at its next look; each goes on without it once the other has
kept it ``JOBS_PATIENCE_S``); ``jobs.stamp``, changed after each write, so
the other reads the list again only then (``touch_jobs``, ``jobs_stamp``).

Nothing here is sent anywhere, and nothing in a claim is a login: a stream's
address never goes into one. Every file is 0600; what is read from one is
size-limited and checked as from outside (a damaged or planted file is a
claim of nobody's)."""
from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import secrets
import socket
import stat
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

from .log import log
from .num import number, whole

#: The folder beside the settings, and the files in it.
FOLDER = "recording-claims"
SUFFIX = ".claim"
STOP = ".stop"
PRESENCE = ".alive"
JOBS_LOCK = "jobs.lock"
JOBS_STAMP = "jobs.stamp"
#: A claim of another computer not touched for this long is nobody's.
STALE_S = 90.0
#: A finished claim, and a stop asked of nobody, is kept this long.
KEEP_S = 2 * 86400.0
#: The most a claim file may hold.
MAX_FILE = 16 * 1024
#: What a holder may be doing, and what a finished one says.
RECORDING, HELD = "recording", "held"
LIVE = (RECORDING, HELD)
DONE, FAILED, CANCELLED = "done", "failed", "cancelled"
FINAL = (DONE, FAILED, CANCELLED)
#: Who holds one.
WINDOW, SERVER = "window", "server"
WHO = (WINDOW, SERVER)
#: What take() found: the claim is ours now, another holds it, or the job
#: was finished (by anyone).
TAKEN, OTHER, ENDED = "taken", "other", "ended"
#: The fields a claim carries about its job (no address: no login).
INFO_FIELDS = ("title", "channel", "stream_icon", "start", "stop", "source", "stream_key",
               "path", "error", "own", "private", "since")
#: Windows: the byte locked, far past any claim's end.
_WIN_LOCK_AT = 1 << 30
#: How long a lock is tried for before it counts as another's (the other
#: side looking at it holds it for a moment).
_TRY_S = 0.06
#: How long the other recorder may keep the jobs' list locked before a
#: recorder goes on without it (jobs_lock): never held up longer by it.
JOBS_PATIENCE_S = 5.0
#: How long jobs_lock waits at once: the server, with no window to draw,
#: its whole patience; the window, on the thread that draws it, a moment -
#: then it does its part at its next tick (a stuck or slow other recorder
#: never freezes the window).
JOBS_WAIT_S = {WINDOW: 0.2, SERVER: JOBS_PATIENCE_S}
#: Misses this far apart are a new wait, not the same one going on.
_JOBS_MISS_GAP_S = 3 * JOBS_PATIENCE_S


class ClaimsUnavailable(OSError):
    """The claims' folder cannot be had as this user's own."""


def _process_started() -> str:
    """When this process started, as the system tells it - with its pid it
    names this process and no other that came after it."""
    try:
        with open("/proc/self/stat", "rb") as fh:
            tail = fh.read(4096).rsplit(b")", 1)[1].split()
        return "boot+" + tail[19].decode("ascii", "replace")
    except (OSError, IndexError):
        return f"{time.time() - time.monotonic():.0f}+{time.monotonic():.3f}"


_STARTED = _process_started()


def folder_for(settings: Any) -> str:
    """Where the claims live for *settings*: beside its file when it is a
    file (Linux, and any INI file), else - a macOS plist in the system's
    Preferences, Windows' registry - beside the app's own resume file, as a
    restore that waits does (core/backup.pending_path)."""
    try:
        from PyQt6.QtCore import QSettings

        from .. import ORG
        path = str(settings.fileName() or "")
        plain = (settings.format() == QSettings.Format.IniFormat
                 or sys.platform not in ("darwin", "win32"))
        if not (plain and path and os.path.isabs(path)):
            path = str(QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                                 ORG, "resume").fileName())
    except Exception as e:
        raise ClaimsUnavailable(errno.ENOENT, f"no folder for the claims ({e})") from None
    if not path or not os.path.isabs(path):
        raise ClaimsUnavailable(errno.ENOENT, "no folder for the claims")
    return os.path.join(os.path.dirname(path), FOLDER)


def for_settings(settings: Any, who: str) -> "Claims | None":
    """The claims beside *settings* for *who* - None (said in the log)
    when their folder cannot be had as this user's own."""
    try:
        claims = Claims(folder_for(settings), who)
        claims.ensure()
        return claims
    except OSError as e:
        log.warning("record: no folder for the recordings' claims (%s)", e)
        return None


def key_of(job_id: Any) -> str:
    """The file name a job's claim goes by: a hash of its id."""
    return hashlib.sha256(str(job_id).encode("utf-8", "replace")).hexdigest()[:32]


@dataclass
class Claim:
    """A claim as read from its file."""
    key: str
    job: str = ""
    who: str = ""
    state: str = ""
    pid: int = 0
    host: str = ""
    started: str = ""
    beat: float = 0.0
    #: Its holder runs (a lock held on this computer, or a fresh beat).
    live: bool = False
    info: dict[str, Any] = field(default_factory=dict)

    @property
    def final(self) -> bool:
        return self.state in FINAL


# -- the system's lock --------------------------------------------------------------------

def _lock(fd: int, shared: bool = False) -> bool:
    """*fd* locked without waiting; False when another holds it."""
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, _WIN_LOCK_AT, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)       # type: ignore[attr-defined]
        except OSError as e:
            if e.errno in (errno.EACCES, errno.EDEADLK, errno.EAGAIN):
                return False
            raise
        return True
    import fcntl
    try:
        fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
    except OSError as e:
        if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
            return False
        raise
    return True


def _lock_soon(fd: int, shared: bool = False, wait: float = _TRY_S) -> bool:
    """_lock, tried for *wait* seconds: another looking at the file holds
    it for a moment."""
    end = time.monotonic() + wait
    while True:
        if _lock(fd, shared):
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(0.01)


def _unlock(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, _WIN_LOCK_AT, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)       # type: ignore[attr-defined]
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


def _open(path: str, create: bool = True) -> int:
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0) \
        | getattr(os, "O_BINARY", 0)
    if create:
        flags |= os.O_CREAT
    fd = os.open(path, flags, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or (os.name == "posix" and st.st_uid != os.geteuid()):
            raise OSError(errno.EPERM, "not a claim of this user's")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _same_file(fd: int, path: str) -> bool:
    """*fd* is still the file at *path* (not one taken out from under it)."""
    if os.name == "nt":
        return True                     # an open file cannot be taken out there
    try:
        a, b = os.fstat(fd), os.lstat(path)
    except OSError:
        return False
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def _write(fd: int, data: bytes) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        view = view[n:]


def _read_fd(key: str, fd: int) -> "Claim":
    """The claim in the open file *fd*, read from its start."""
    st = os.fstat(fd)
    if st.st_size > MAX_FILE:
        return Claim(key, beat=st.st_mtime)
    os.lseek(fd, 0, os.SEEK_SET)
    return parse(key, os.read(fd, MAX_FILE + 1), st.st_mtime)


def _clean_info(raw: Any) -> dict[str, Any]:
    """A claim's job fields, as from outside: plain text, numbers in range."""
    out: dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out
    for k in ("title", "channel", "stream_icon", "source", "stream_key", "path", "error"):
        v = raw.get(k)
        if isinstance(v, str):
            out[k] = "".join(c for c in v[:2048] if c >= " " or c == "\t")
    for k in ("start", "stop", "since"):
        n = number(raw.get(k))
        if n is not None and 0 <= n < 1e11:
            out[k] = float(n)
    for k in ("own", "private"):
        if isinstance(raw.get(k), bool):
            out[k] = raw[k]
    return out


def parse(key: str, data: bytes, beat: float = 0.0) -> Claim:
    """A claim file's *data* - nobody's when it is damaged or not one."""
    c = Claim(key, beat=beat)
    try:
        raw = json.loads(data.decode("utf-8")) if data else None
    except (ValueError, UnicodeDecodeError, RecursionError):
        # RecursionError: nesting deeper than Python reads - a claim's size
        # (MAX_FILE) holds far more than that.
        return c
    if not isinstance(raw, dict) or raw.get("v") != 1:
        return c
    def text(k: str) -> str:
        v = raw.get(k)
        return v[:64] if isinstance(v, str) else ""

    who = raw.get("who")
    state = raw.get("state")
    c.job = text("job")
    c.who = who if who in WHO else ""
    c.state = state if state in LIVE + FINAL else ""
    pid = raw.get("pid")
    c.pid = whole(pid, 0, lo=0, hi=2 ** 31) if isinstance(pid, (int, float, str)) else 0
    c.host = text("host")
    c.started = text("started")
    c.info = _clean_info(raw.get("info"))
    return c


# -- the claims ------------------------------------------------------------------------------

class _Held:
    def __init__(self, fd: int, job: str, state: str) -> None:
        self.fd, self.job, self.state = fd, job, state


class Claims:
    """This process's side of the claims in *folder*, as *who* (WINDOW or
    SERVER). Thread-safe; every failure to read or write a file is an
    OSError the recorder decides about (the window records as before, the
    server does not)."""

    def __init__(self, folder: str, who: str, host: str | None = None,
                 clock: Any = time.time) -> None:
        if who not in WHO:
            raise ValueError(who)
        self.folder = folder
        self.who = who
        self.host = (host if host is not None else socket.gethostname())[:64]
        self._clock = clock
        self._lock = threading.RLock()
        self._held: dict[str, _Held] = {}
        self._presence: int | None = None
        self._jobs_fd: int | None = None
        self._jobs_depth = 0
        #: What the outermost jobs_lock said (the nested ones say the same).
        self._jobs_go = True
        #: (first, last) monotonic times the other kept the jobs' list from
        #: this one in a row; None while it gets it.
        self._jobs_missed: tuple[float, float] | None = None
        self._ready = False

    # -- the folder --------------------------------------------------------------------------
    def ensure(self) -> None:
        """The folder made, this user's alone (0700, no link); ClaimsUnavailable
        when it cannot be."""
        if self._ready:
            return
        try:
            os.makedirs(self.folder, mode=0o700, exist_ok=True)
            st = os.lstat(self.folder)
            if not stat.S_ISDIR(st.st_mode):
                raise ClaimsUnavailable(errno.ENOTDIR, f"{self.folder} is not a folder")
            if os.name == "posix":
                if st.st_uid != os.geteuid():
                    raise ClaimsUnavailable(errno.EPERM, f"{self.folder} is another account's")
                if stat.S_IMODE(st.st_mode) & 0o077:
                    os.chmod(self.folder, 0o700)
                    if stat.S_IMODE(os.lstat(self.folder).st_mode) & 0o077:
                        raise ClaimsUnavailable(errno.EPERM,
                                                f"{self.folder} cannot be kept to this user")
        except ClaimsUnavailable:
            raise
        except OSError as e:
            raise ClaimsUnavailable(e.errno or errno.EIO, f"{self.folder}: {e.strerror or e}") \
                from None
        self._ready = True

    def _path(self, name: str) -> str:
        return os.path.join(self.folder, name)

    def _content(self, job: str, state: str, info: dict[str, Any]) -> bytes:
        data = {"v": 1, "job": str(job)[:64], "who": self.who, "state": state,
                "pid": os.getpid(), "host": self.host, "started": _STARTED,
                "info": _clean_info(info)}
        return json.dumps(data, ensure_ascii=False).encode("utf-8")[:MAX_FILE]

    # -- reading -------------------------------------------------------------------------------
    def _read_file(self, key: str) -> Claim | None:
        path = self._path(key + SUFFIX)
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_BINARY", 0))
        except FileNotFoundError:
            return None
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE:
                return Claim(key, beat=st.st_mtime)
            data = os.read(fd, MAX_FILE + 1)
        finally:
            os.close(fd)
        return parse(key, data, st.st_mtime)

    def _judge(self, c: Claim) -> Claim:
        """Whether *c*'s holder runs: on this computer by its lock, on
        another by its beat."""
        if c.final or c.state not in LIVE:
            c.live = False
            return c
        if c.host and c.host != self.host:
            c.live = self._clock() - c.beat < STALE_S
            return c
        try:
            fd = _open(self._path(c.key + SUFFIX), create=False)
        except OSError:
            c.live = False
            return c
        try:
            # Once, not waited for: only a holder's (or a taker's) own lock
            # stands in a look's way.
            free = _lock(fd, shared=True)
            if free:
                _unlock(fd)
            c.live = not free
        finally:
            os.close(fd)
        return c

    def read(self, job_id: Any) -> Claim | None:
        """The claim of *job_id* (None: none), its holder judged."""
        key = key_of(job_id)
        with self._lock:
            held = self._held.get(key)
        c = self._read_file(key)
        if c is None:
            return None
        if held is not None:
            c.live = held.state in LIVE
            return c
        return self._judge(c)

    def mine(self, job_id: Any) -> bool:
        with self._lock:
            return key_of(job_id) in self._held

    def others(self) -> list[Claim]:
        """The live claims held by another process."""
        try:
            names = [e.name for e in os.scandir(self.folder)
                     if e.name.endswith(SUFFIX) and e.is_file(follow_symlinks=False)]
        except OSError:
            return []
        with self._lock:
            mine = set(self._held)
        out = []
        for name in sorted(names)[:500]:
            key = name[:-len(SUFFIX)]
            if key in mine:
                continue
            try:
                c = self._read_file(key)
            except OSError:
                continue
            if c is not None and c.job and self._judge(c).live:
                out.append(c)
        return out

    # -- holding -------------------------------------------------------------------------------
    def take(self, job_id: Any, info: dict[str, Any], state: str = RECORDING
             ) -> tuple[str, Claim | None]:
        """The claim of *job_id*, taken for this process: (TAKEN, None) - it
        is ours now (or was) -, (OTHER, claim) while another holds it live,
        (ENDED, claim) when it was finished already."""
        self.ensure()
        key = key_of(job_id)
        with self._lock:
            if key in self._held:
                return TAKEN, None
            path = self._path(key + SUFFIX)
            for _attempt in range(3):
                fd = _open(path)
                try:
                    if not _lock_soon(fd):
                        os.close(fd)
                        c = self._read_file(key) or Claim(key)
                        c.live = True
                        return OTHER, c
                    if not _same_file(fd, path):
                        _unlock(fd)
                        os.close(fd)
                        continue            # taken out meanwhile: the new one
                    before = _read_fd(key, fd)
                    if before.final:
                        _unlock(fd)
                        os.close(fd)
                        return ENDED, before
                    if (before.state in LIVE and before.host and before.host != self.host
                            and self._clock() - before.beat < STALE_S):
                        # Another computer's, beaten lately: the lock did not
                        # reach it through the share.
                        _unlock(fd)
                        os.close(fd)
                        before.live = True
                        return OTHER, before
                    _write(fd, self._content(str(job_id), state, info))
                    self._held[key] = _Held(fd, str(job_id), state)
                except BaseException:
                    if key not in self._held:
                        _unlock(fd)
                        os.close(fd)
                    raise
                if before.state in LIVE:
                    log.info("record: a recording's claim of the %s's (process %d) taken over - "
                             "it no longer runs", before.who or "other recorder", before.pid)
                return TAKEN, None
            raise OSError(errno.EAGAIN, "the claim kept changing")

    def write(self, job_id: Any, state: str, info: dict[str, Any]) -> None:
        """What a claim of ours says now (*state* LIVE)."""
        key = key_of(job_id)
        with self._lock:
            held = self._held.get(key)
            if held is None:
                return
            _write(held.fd, self._content(held.job, state, info))
            held.state = state

    def finish(self, job_id: Any, state: str, info: dict[str, Any]) -> str:
        """A claim of ours ends, *state* FINAL - kept, so the job is never
        recorded again - and its lock let go (TAKEN). A job that was never
        ours (a cancel) is finished so too while nobody holds it; OTHER when
        another does (ask_stop asks it), ENDED when it was finished already."""
        key = key_of(job_id)
        with self._lock:
            held = self._held.pop(key, None)
            if held is None:
                got, c = self.take(job_id, info, HELD)
                if got == ENDED and c is not None and c.pid == os.getpid() \
                        and c.started == _STARTED:
                    # Our own, finished before: said again as it is now.
                    self._rewrite(key, job_id, state, info)
                    return TAKEN
                if got != TAKEN:
                    return got
                held = self._held.pop(key)
            try:
                _write(held.fd, self._content(held.job, state, info))
            finally:
                _unlock(held.fd)
                os.close(held.fd)
            return TAKEN

    def _rewrite(self, key: str, job_id: Any, state: str, info: dict[str, Any]) -> None:
        path = self._path(key + SUFFIX)
        fd = _open(path, create=False)
        try:
            if _lock_soon(fd):
                try:
                    if _same_file(fd, path) and _read_fd(key, fd).final:
                        _write(fd, self._content(str(job_id), state, info))
                finally:
                    _unlock(fd)
        finally:
            os.close(fd)

    def free(self, job_id: Any) -> None:
        """A claim of ours let go, as if never taken (a job that waits)."""
        key = key_of(job_id)
        with self._lock:
            held = self._held.pop(key, None)
            if held is None:
                return
            path = self._path(key + SUFFIX)
            try:
                if os.name != "nt" and _same_file(held.fd, path):
                    os.remove(path)     # under its lock: nobody holds what is left
                else:
                    _write(held.fd, b"")
            except OSError:
                with contextlib.suppress(OSError):
                    _write(held.fd, b"")
            finally:
                _unlock(held.fd)
                os.close(held.fd)
            if os.name == "nt":
                # Refused while another has it open: then it is theirs.
                with contextlib.suppress(OSError):
                    os.remove(path)

    def beat(self) -> None:
        """Every claim of ours, and the presence, touched: still held."""
        with self._lock:
            names = [k + SUFFIX for k in self._held]
            if self._presence is not None:
                names.append(self.who + PRESENCE)
        now = self._clock()
        for name in names:
            with contextlib.suppress(OSError):
                os.utime(self._path(name), (now, now))

    def let_go_all(self, keep: Any = ()) -> None:
        """Every lock this process holds let go (its claims as they are) -
        but the claims of the jobs in *keep*, still held (a window closing
        holds what a server carries on until nothing of its own reads the
        stream: core/recording.py's let_go_carried). The presence goes
        either way."""
        kept = {key_of(j) for j in (keep or ())}
        with self._lock:
            held = {k: h for k, h in self._held.items() if k not in kept}
            self._held = {k: h for k, h in self._held.items() if k in kept}
            for h in held.values():
                _unlock(h.fd)
                with contextlib.suppress(OSError):
                    os.close(h.fd)
            self.drop_presence()

    # -- asking another to stop ------------------------------------------------------------
    def ask_stop(self, job_id: Any) -> None:
        """Ask whoever records *job_id* to stop it."""
        self.ensure()
        path = self._path(key_of(job_id) + STOP)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.close(fd)

    def stop_asked(self, job_id: Any) -> bool:
        """Whether a stop of *job_id* was asked (the ask is taken out)."""
        path = self._path(key_of(job_id) + STOP)
        if not os.path.lexists(path):
            return False
        with contextlib.suppress(OSError):
            os.remove(path)
        return True

    # -- the window's presence --------------------------------------------------------------
    def hold_presence(self) -> bool:
        """This process says it runs (as its *who*), as long as it does.
        True once it is said."""
        self.ensure()
        with self._lock:
            if self._presence is not None:
                return True
            fd = _open(self._path(self.who + PRESENCE))
            # A server's look holds it for a moment; another window of this
            # user's (sharing the folder) for good - then it is that one's.
            if not _lock_soon(fd, wait=0.3):
                os.close(fd)
                return False
            _write(fd, self._content("", HELD, {}))
            self._presence = fd
            return True

    def drop_presence(self) -> None:
        with self._lock:
            fd, self._presence = self._presence, None
        if fd is not None:
            _unlock(fd)
            with contextlib.suppress(OSError):
                os.close(fd)

    def present(self, who: str = WINDOW) -> bool:
        """Whether a process of *who* runs, on this computer or (by its
        beat) another that shares the folder - not this one itself."""
        if who == self.who and self._presence is not None:
            return False
        path = self._path(who + PRESENCE)
        try:
            fd = _open(path, create=False)
        except OSError:
            return False
        try:
            if not _lock(fd, shared=True):
                return True
            _unlock(fd)
            c = _read_fd("", fd)
        finally:
            os.close(fd)
        return bool(c.host and c.host != self.host and self._clock() - c.beat < STALE_S)

    # -- the scheduled jobs' list ------------------------------------------------------------
    @contextlib.contextmanager
    def jobs_lock(self, wait: float | None = None) -> Iterator[bool]:
        """The list of scheduled jobs read and written by one recorder at a
        time (re-entrant here). Yields whether to go on: True with the list
        locked - or without the lock once the other has kept it
        JOBS_PATIENCE_S in a row (a recording is never held up by it); False
        while that has not passed: the caller does its part at its next look.

        *wait*: how long to wait at once (JOBS_WAIT_S by who this is - the
        window a moment, never its whole patience on the thread that draws
        it); a wait that goes on from the last miss is one short try."""
        with self._lock:
            self._jobs_depth += 1
            try:
                if self._jobs_depth == 1:
                    self._jobs_go = self._take_jobs(wait)
                yield self._jobs_go
            finally:
                self._jobs_depth -= 1
                if self._jobs_depth == 0 and self._jobs_fd is not None:
                    fd, self._jobs_fd = self._jobs_fd, None
                    _unlock(fd)
                    os.close(fd)

    def _take_jobs(self, wait: float | None) -> bool:
        """(jobs_lock, outermost) The jobs' list locked: whether to go on."""
        start = time.monotonic()
        missed = self._jobs_missed
        going_on = missed is not None and start - missed[1] < _JOBS_MISS_GAP_S
        if wait is None:
            wait = _TRY_S if going_on else JOBS_WAIT_S.get(self.who, JOBS_PATIENCE_S)
        try:
            self.ensure()
            fd = _open(self._path(JOBS_LOCK))
        except OSError as e:
            log.debug("record: the jobs' list not locked: %s", e)
            return True                 # no lock to be had: as before, on
        try:
            got = _lock_soon(fd, wait=max(0.0, wait))
        except OSError as e:
            os.close(fd)
            log.debug("record: the jobs' list not locked: %s", e)
            return True
        if got:
            self._jobs_fd = fd
            self._jobs_missed = None
            return True
        os.close(fd)
        first = missed[0] if going_on and missed is not None else start
        now = time.monotonic()
        self._jobs_missed = (first, now)
        if now - first >= JOBS_PATIENCE_S:
            log.debug("record: the other recorder kept the jobs' list - on without it")
            return True
        return False

    def touch_jobs(self) -> str:
        """The jobs' list changed: a new stamp (returned) for the other."""
        self.ensure()
        stamp = secrets.token_hex(8)
        tmp = self._path(f"{JOBS_STAMP}.{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
                     0o600)
        try:
            os.write(fd, stamp.encode("ascii"))
        finally:
            os.close(fd)
        try:
            os.replace(tmp, self._path(JOBS_STAMP))
        except OSError:
            with contextlib.suppress(OSError):
                os.remove(tmp)
            raise
        return stamp

    def jobs_stamp(self) -> str:
        """The jobs' list's stamp now ("" for none)."""
        try:
            fd = os.open(self._path(JOBS_STAMP), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            return ""
        try:
            return os.read(fd, 64).decode("ascii", "replace").strip()
        finally:
            os.close(fd)

    # -- tidying ---------------------------------------------------------------------------------
    def sweep(self, now: float | None = None) -> int:
        """Finished claims past KEEP_S, claims of nobody's for a job long
        over, and stops nobody saw: taken out. How many."""
        now = self._clock() if now is None else now
        gone = 0
        try:
            entries = list(os.scandir(self.folder))
        except OSError:
            return 0
        with self._lock:
            mine = set(self._held)
        for e in entries[:2000]:
            try:
                if not e.is_file(follow_symlinks=False):
                    continue
                age = now - e.stat(follow_symlinks=False).st_mtime
                if e.name.endswith(STOP) and age > KEEP_S:
                    os.remove(e.path)
                    gone += 1
                elif e.name.endswith(SUFFIX) and e.name[:-len(SUFFIX)] not in mine \
                        and self._sweep_one(e.name[:-len(SUFFIX)], now):
                    gone += 1
            except OSError:
                continue
        return gone

    def _sweep_one(self, key: str, now: float) -> bool:
        c = self._read_file(key)
        if c is None:
            return False
        stop = c.info.get("stop") or c.info.get("start") or 0.0
        old = now - max(c.beat, float(stop)) > KEEP_S
        if not old or (c.state in LIVE and self._judge(c).live):
            return False
        path = self._path(key + SUFFIX)
        fd = _open(path, create=False)
        if os.name == "nt":
            os.close(fd)
            os.remove(path)             # refused while anyone has it open
            return True
        try:
            if not _lock_soon(fd) or not _same_file(fd, path):
                return False
            try:
                os.remove(path)
            finally:
                _unlock(fd)
            return True
        finally:
            os.close(fd)
