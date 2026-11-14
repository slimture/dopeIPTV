"""A backup of everything a person would miss on a new computer, and putting
one back.

One JSON file: a format number, the app version that made it, when, and the
keys of four settings files - the app's own settings ("main") and the three
files beside them (main_window._open_*_settings): resume positions, followed
series, and the cache file's watched and watch-later marks. It is written
atomically, readable only by its owner where the system has such a thing.

What goes in: every key of the app's settings but those _LEFT_OUT - where
the windows sit on this screen, this computer's graphics and desktop entry,
what the update check last found, the feedback reply codes, and caches the
app fills again by itself. Of the other three files only the person's own
keys go; the rest of them is cache.

Logins go in as plain text, as they are in the settings themselves: with no
crypto library to lock the file, the app says so where the file is made.

Folders and files a setting names (_PATHS) go in as they are. A restore
takes one only where it exists on the computer restoring and keeps that
computer's own otherwise - a recordings folder that is not there would fail
every recording.

Putting a backup back over everything is done as the app starts: stage()
now, apply_pending() at the next start, before anything reads the settings.
The running window holds its stores in memory and writes them back as it
closes (recordings, resume points, watched marks), which would have
overwritten a restore written under it. A file that does not read as a whole
is refused before anything changes.

An export from before this module - a list of playlists - still reads, as
playlists only.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from .log import log

FORMAT = 1
MAGIC = "dopeiptv_backup"
#: The settings files a backup covers, by the name it gives them.
STORES = ("main", "resume", "follow", "cache")
#: A backup is a few MB at most; past this it is not one.
MAX_BYTES = 64 * 1024 * 1024
PENDING_NAME = "restore-pending.json"

#: What a backup counts, in the order a summary lists it.
KINDS = ("sources", "favorites", "history", "resume", "watched", "watchlist",
         "followed", "reminders", "recordings", "hidden", "numbers", "settings")

# The other files give only these keys (the name, or the name + "_<source>").
_ONLY = {"resume": "resume_positions", "follow": "followed_series"}
_CACHE_KEYS = frozenset({"trakt_watched_cache", "trakt_watchlist_cache"})

#: Never in a backup, and never taken from one.
_LEFT_OUT = frozenset({
    # Where the windows sit on this computer's screen.
    "window_geometry", "window_state", "splitter_state", "sidebar_collapsed",
    "docked_video_height", "mv_geometry", "popout_geometry",
    # This computer's graphics and desktop: on another one X11 or a decoder
    # chosen here can cost the picture.
    "force_x11", "hwdec_mode", "desktop_entry", "desktop_entry_asked",
    # The copy an update replaced (deleted at start).
    "selfupdate_old_path",
    # The reply codes of this computer's feedback reports.
    "feedback_tickets", "feedback_last", "feedback_sent",
    # Caches the app fills again by itself.
    "local_tmdb_posters", "local_tmdb_posters_v2", "music_art_v1",
    "cast_devices", "subtitle_files", "nerd_health_data",
})
# update_*: the update channel and what its check last found; ts_*: what a
# source's timeshift probe learned; "_": the files' own markers.
_LEFT_OUT_PREFIXES = ("update_", "home_pcache_", "ts_broken/", "ts_depth/", "_")
_TMDB_CACHE = re.compile(r"tmdb_.*(cache|matcher)")

#: Settings naming a folder ("dir"), a JSON list of folders ("dirs") or files
#: one per line ("files").
_PATHS = {"recordings_dir": "dir", "local_open_dir": "dir",
          "nerd_picon_dir": "dir", "nerd_mpv_config_dir": "dir",
          "local_dirs": "dirs", "nerd_shaders": "files"}

#: Settings that run something or send the connections elsewhere: a restore
#: shows them before it is made.
RISKY = ("nerd_rec_post", "nerd_mpv_options", "nerd_mpv_config_dir", "nerd_proxy")

_SOURCE_KEYS = frozenset({"playlists", "active_playlist", "playlist_name",
                          "server", "username", "password"})
_FAVORITES = re.compile(r"(?:movie_|series_)?favorites(?:_.*)?")
_HISTORY = re.compile(r"history(?:_.*)?")
_REMINDERS = re.compile(r"epg_reminders(?:_.*)?")
_OVERRIDES = re.compile(r"(?:category|channel)_overrides(?:_.*)?")


class Settings(Protocol):
    """What a backup needs of a QSettings."""

    def value(self, key: str, default: Any = None) -> Any: ...

    def setValue(self, key: str, value: Any) -> None: ...  # noqa: N802 - Qt's name

    def remove(self, key: str) -> None: ...

    def allKeys(self) -> list[str]: ...  # noqa: N802

    def sync(self) -> None: ...


class BackupError(ValueError):
    """A file that is not a backup this app can put back. *reason*:
    "unreadable" (the file could not be read; *detail* says why), "invalid"
    (not a backup, or damaged) or "newer" (*detail*: the version it came from)."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass
class Backup:
    """A backup as read: *kind* "full", or "playlists" for an old export."""

    kind: str
    stores: dict[str, dict[str, Any]] = field(default_factory=dict)
    sources: list[dict] = field(default_factory=list)
    app_version: str = ""
    created: str = ""

    @property
    def full(self) -> bool:
        return self.kind == "full"

    def document(self) -> dict[str, Any]:
        return {MAGIC: FORMAT, "app_version": self.app_version,
                "created": self.created, "stores": self.stores}


# -- what goes in ----------------------------------------------------------------

def left_out(key: str) -> bool:
    """Whether the app's settings key *key* stays out of a backup."""
    return (key in _LEFT_OUT or key.startswith(_LEFT_OUT_PREFIXES)
            or _TMDB_CACHE.match(key) is not None)


def covered(store: str, key: str) -> bool:
    """Whether *key* of the file *store* goes in a backup (and is replaced by
    a restore)."""
    if not key or key.startswith("_"):
        return False
    if store == "main":
        # Keys the other files hold now, left in old settings, are not read.
        return not (left_out(key) or key in _CACHE_KEYS
                    or any(_own(key, p) for p in _ONLY.values()))
    if store == "cache":
        return key in _CACHE_KEYS
    prefix = _ONLY.get(store)
    return prefix is not None and _own(key, prefix)


def _own(key: str, prefix: str) -> bool:
    return key == prefix or key.startswith(prefix + "_")


def _scalar(v: Any) -> bool:
    if v is None or isinstance(v, (bool, str, int)):
        return True
    return isinstance(v, float) and math.isfinite(v)


def _plain(v: Any) -> bool:
    """A value a JSON file holds as QSettings gave it."""
    return _scalar(v) or (isinstance(v, list) and all(_scalar(x) for x in v))


def _good_key(key: Any) -> bool:
    return (isinstance(key, str) and 0 < len(key) <= 512 and "\x00" not in key
            and "\\" not in key and not key.startswith("/")
            and not key.endswith("/") and "//" not in key)


@contextmanager
def _no_fallbacks(s: Any) -> Iterator[None]:
    """The file's own keys only: with fallbacks on, a Mac's QSettings lists
    the system's global preferences as well."""
    getter = getattr(s, "fallbacksEnabled", None)
    setter = getattr(s, "setFallbacksEnabled", None)
    was = bool(getter()) if callable(getter) else False
    if was and callable(setter):
        setter(False)
    try:
        yield
    finally:
        if was and callable(setter):
            setter(True)


def _fits(name: str, key: str, value: Any) -> bool:
    """Whether *value* goes in a file that parse() then takes back whole -
    a backup this app made must always restore."""
    if not (_good_key(key) and _plain(value)):
        return False
    if name == "main" and key == "playlists":
        try:
            _sources(value)
        except BackupError:
            return False
    return True


def collect(stores: Mapping[str, Settings | None], app_version: str,
            now: datetime | None = None) -> dict[str, Any]:
    """The backup document of *stores* (STORES names to settings). A value
    a file cannot hold (a kind JSON has not, a damaged playlist list) is
    passed over."""
    out: dict[str, dict[str, Any]] = {}
    passed = 0
    for name in STORES:
        s = stores.get(name)
        if s is None:
            continue
        kept: dict[str, Any] = {}
        with _no_fallbacks(s):
            for key in sorted(s.allKeys()):
                if not covered(name, key):
                    continue
                value = s.value(key)
                if isinstance(value, tuple):
                    value = list(value)
                if not _fits(name, key, value):
                    passed += 1
                    log.debug("backup: %s/%s (%s) passed over", name, key,
                              type(value).__name__)
                    continue
                kept[key] = value
        out[name] = kept
    if passed:
        log.info("backup: %d value(s) a file cannot hold passed over", passed)
    when = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    return {MAGIC: FORMAT, "app_version": app_version, "created": when,
            "stores": out}


def write(path: str, doc: Mapping[str, Any]) -> None:
    """*doc* to *path* as a whole or not at all: written beside it and moved
    over it. Raises OSError (and leaves no half file) when it cannot be."""
    data = json.dumps(doc, ensure_ascii=False, indent=2,
                      allow_nan=False).encode("utf-8")
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".dopeiptv-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# -- reading one -----------------------------------------------------------------

def read(path: str) -> Backup:
    """The backup in the file *path*, checked whole. Raises BackupError."""
    try:
        if os.path.getsize(path) > MAX_BYTES:
            raise BackupError("invalid", "too large")
        with open(path, "rb") as fh:
            raw = fh.read(MAX_BYTES + 1)
    except OSError as e:
        raise BackupError("unreadable", str(e)) from e
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as e:
        raise BackupError("invalid", str(e)) from e
    return parse(data)


def _sources(raw: Any) -> list[dict]:
    if raw is None or raw == "":
        return []
    if not isinstance(raw, str):
        raise BackupError("invalid", "playlists")
    try:
        rows = json.loads(raw)
    except ValueError as e:
        raise BackupError("invalid", "playlists") from e
    if not isinstance(rows, list):
        raise BackupError("invalid", "playlists")
    return [p for p in rows if isinstance(p, dict)]


def parse(data: Any) -> Backup:
    """A backup from its decoded JSON. Raises BackupError."""
    if isinstance(data, list):
        # An export from before full backups: the playlists, nothing else.
        return Backup("playlists", sources=[p for p in data if isinstance(p, dict)])
    if not isinstance(data, dict) or MAGIC not in data:
        raise BackupError("invalid", "not a backup")
    version = data.get(MAGIC)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise BackupError("invalid", "format")
    app_version = data.get("app_version")
    app_version = app_version if isinstance(app_version, str) else ""
    if version > FORMAT:
        raise BackupError("newer", app_version)
    created = data.get("created")
    stores = data.get("stores")
    if not isinstance(stores, dict):
        raise BackupError("invalid", "stores")
    clean: dict[str, dict[str, Any]] = {}
    for name, values in stores.items():
        if name not in STORES:
            log.info("backup: a part this version does not know (%r) passed over", name)
            continue
        if not isinstance(values, dict):
            raise BackupError("invalid", str(name))
        for key, value in values.items():
            if not _good_key(key) or not _plain(value):
                raise BackupError("invalid", f"{name}/{key}")
        clean[name] = dict(values)
    return Backup("full", clean, _sources(clean.get("main", {}).get("playlists")),
                  app_version, created if isinstance(created, str) else "")


# -- what it holds ---------------------------------------------------------------

def _json(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return None
    try:
        return json.loads(value)
    except ValueError:
        return None


def _kind_of(key: str) -> str:
    """The summary's kind of an app settings key ("" counts nothing)."""
    if key in _SOURCE_KEYS:
        return ""
    if _FAVORITES.fullmatch(key):
        return "" if key.endswith(("_locked", "_colors")) else "favorites"
    if _HISTORY.fullmatch(key):
        return "history"
    if _REMINDERS.fullmatch(key):
        return "reminders"
    if key == "recording_jobs":
        return "recordings"
    if _OVERRIDES.fullmatch(key):
        return "hidden"
    if key.startswith("nerd_chno_"):
        return "numbers"
    return "settings"


def _count(kind: str, value: Any) -> int:
    if kind == "settings":
        return 1
    data = _json(value)
    if kind == "favorites":
        if not isinstance(data, dict):
            return 0
        rows = {json.dumps(r, sort_keys=True) for g in data.values()
                if isinstance(g, list) for r in g}
        return len(rows)
    if kind == "hidden":
        return sum(len(v) for v in data.values()
                   if isinstance(v, dict)) if isinstance(data, dict) else 0
    return len(data) if isinstance(data, (list, dict)) else 0


def summary(b: Backup) -> dict[str, int]:
    """How much of each of KINDS *b* holds."""
    counts = dict.fromkeys(KINDS, 0)
    counts["sources"] = len(b.sources)
    for key, value in b.stores.get("main", {}).items():
        kind = _kind_of(key)
        if kind:
            counts[kind] += _count(kind, value)
    for name, kind in (("resume", "resume"), ("follow", "followed")):
        for value in b.stores.get(name, {}).values():
            data = _json(value)
            counts[kind] += len(data) if isinstance(data, dict) else 0
    cache = b.stores.get("cache", {})
    watched = _json(cache.get("trakt_watched_cache"))
    if isinstance(watched, dict) and isinstance(watched.get("local_items"), list):
        counts["watched"] = len(watched["local_items"])
    later = _json(cache.get("trakt_watchlist_cache"))
    if isinstance(later, dict):
        counts["watchlist"] = sum(len(later[k]) for k in ("movies", "shows")
                                  if isinstance(later.get(k), list))
    return counts


def _here(kind: str, value: Any,
          exists: Callable[[str], bool]) -> tuple[bool, Any, bool]:
    """(take it, what to write, something it names is not here)."""
    def found(p: str) -> bool:
        return exists(os.path.expanduser(p))

    if kind == "dir":
        text = value.strip() if isinstance(value, str) else ""
        if not text or found(text):
            return True, value, False
        return False, None, True
    if kind == "dirs":
        items = _json(value)
        if not isinstance(items, list) or not items:
            return True, value, False
        kept = [p for p in items if isinstance(p, str) and found(p)]
        if not kept:
            return False, None, True
        return True, json.dumps(kept), len(kept) != len(items)
    text = value if isinstance(value, str) else ""
    files = [p.strip() for p in re.split(r"[\n;]", text) if p.strip()]
    kept = [p for p in files if found(p)]
    if files and not kept:
        return False, None, True
    if len(kept) != len(files):
        return True, "\n".join(kept), True
    return True, value, False


def missing_paths(b: Backup, exists: Callable[[str], bool] = os.path.exists) -> list[str]:
    """The settings of *b* naming a folder or file this computer does not
    have: a restore leaves those out (and keeps this computer's own)."""
    main = b.stores.get("main", {})
    return [key for key, kind in _PATHS.items()
            if key in main and _here(kind, main[key], exists)[2]]


def risky(b: Backup) -> list[tuple[str, str]]:
    """(key, value) of RISKY settings *b* sets."""
    main = b.stores.get("main", {})
    return [(key, main[key].strip()) for key in RISKY
            if isinstance(main.get(key), str) and main[key].strip()]


# -- putting it back -------------------------------------------------------------

def restore(b: Backup, stores: Mapping[str, Settings | None],
            exists: Callable[[str], bool] = os.path.exists) -> list[str]:
    """Replace everything a backup covers in *stores* with what *b* holds.
    Keys it covers that *b* lacks go; keys it leaves out stay as they are.
    Returns the path settings left out (see missing_paths). All is worked out
    before the first write."""
    if not b.full:
        raise ValueError("a playlists file holds no settings to put back")
    plan: list[tuple[Settings, list[str], dict[str, Any]]] = []
    left: list[str] = []
    for name in STORES:
        s = stores.get(name)
        if s is None:
            continue
        incoming = {k: v for k, v in b.stores.get(name, {}).items()
                    if covered(name, k)}
        keep_here: set[str] = set()
        if name == "main":
            for key, kind in _PATHS.items():
                if key not in incoming:
                    continue
                take, value, missing = _here(kind, incoming[key], exists)
                if missing:
                    left.append(key)
                if take:
                    incoming[key] = value
                else:
                    del incoming[key]
                    keep_here.add(key)
        with _no_fallbacks(s):
            gone = [k for k in s.allKeys() if covered(name, k) and k not in keep_here]
        plan.append((s, gone, incoming))
    for s, gone, incoming in plan:
        for key in gone:
            s.remove(key)
        for key, value in incoming.items():
            s.setValue(key, value)
    for s, _gone, _incoming in plan:
        s.sync()
    return left


def open_stores() -> dict[str, Any]:
    """The app's four settings files, opened as the app opens them."""
    from PyQt6.QtCore import QSettings

    from .. import ORG
    ini, user = QSettings.Format.IniFormat, QSettings.Scope.UserScope
    return {"main": QSettings(ORG, ORG),
            "resume": QSettings(ini, user, ORG, "resume"),
            "follow": QSettings(ini, user, ORG, "follow"),
            "cache": QSettings(ini, user, ORG, "cache")}


def pending_path(folder: str | None = None) -> str:
    """Where a restore waits for the next start: beside the app's own files
    (the resume file's folder - the main settings are a registry key on
    Windows)."""
    if folder is None:
        from PyQt6.QtCore import QSettings

        from .. import ORG
        folder = os.path.dirname(QSettings(
            QSettings.Format.IniFormat, QSettings.Scope.UserScope,
            ORG, "resume").fileName())
    return os.path.join(folder, PENDING_NAME)


def stage(b: Backup, path: str | None = None) -> str:
    """Leave *b* to be put back over everything at the next start. Returns
    the file it waits in. Raises OSError."""
    if not b.full:
        raise ValueError("a playlists file holds no settings to put back")
    path = path or pending_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    write(path, b.document())
    log.info("restore: a backup waits for the next start")
    return path


def apply_pending(stores: Mapping[str, Settings | None] | None = None,
                  path: str | None = None,
                  exists: Callable[[str], bool] = os.path.exists) -> bool | None:
    """Put a staged backup in place (at start, before anything reads the
    settings). None: none was waiting; True: it is in place; False: it could
    not be - the file goes either way, so a bad one is never tried again (the
    person still has the backup itself)."""
    path = path or pending_path()
    if not os.path.exists(path):
        return None
    ok = False
    try:
        b = read(path)
        if not b.full:
            raise BackupError("invalid", "no settings")
        left = restore(b, stores if stores is not None else open_stores(), exists)
        ok = True
        log.info("restore: the backup of %s (dopeIPTV %s) is in place%s",
                 b.created or "?", b.app_version or "?",
                 f"; left as they were here: {', '.join(left)}" if left else "")
    except Exception as e:
        log.error("restore: the backup could not be put in place: %s", e)
    try:
        os.remove(path)
    except OSError as e:
        log.warning("restore: the waiting backup stays (%s)", e)
    return ok
