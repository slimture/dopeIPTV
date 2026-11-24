"""A backup of everything a person would miss on a new computer, and putting
one back.

One JSON file: a format number, the app version that made it, when, and the
keys of four settings files - the app's own settings ("main") and the three
files beside them (main_window._open_*_settings): resume positions, followed
series, and the cache file's watched and watch-later marks. It is written
atomically, readable only by its owner where the system has such a thing.

What goes in: every key of the app's settings but those _LEFT_OUT - where
the windows sit on this screen, this computer's graphics and desktop entry,
what the update check last found, the feedback reply codes, the remote
control's token (one is made on the computer that turns it on), and caches
the app fills again by itself. Of the other three files only the person's
own keys go; the rest of them is cache.

Logins go in only when the person gives the backup a password. Without one
they are left out (without_logins): the settings that are a login, a
playlist's password, token and box MAC, a login in its addresses - the file
says so (NO_LOGINS), and a restore keeps the logins of the computer it is
put back on (Backup.keep, _logins_from_here). With one (lock, for_file) the
file is one JSON object of its own: a
header that says how it is locked (MAGIC "encrypted", v, kdf, cipher,
rounds, salt, nonce) and *data* - the whole backup, logins and all,
encrypted with ChaCha20-Poly1305 (core/chacha20poly1305.py) under the
key PBKDF2-HMAC-SHA256 makes of the password (NFC, UTF-8) and the salt, the
header itself as the associated data, so a changed header fails as a
changed byte does. read_any() checks the header before any work (Locked);
Locked.unlock() makes the key and opens the file - slow, a worker's job -
and a wrong password and a changed file are one error ("password"), never
which. What is inside then goes on as a backup without one does. The
design, and what it does not protect against: docs/SECURITY.md, "A
backup's password".

Folders and files a setting names (_PATHS) go in as they are. A restore
takes one only where it exists on the computer restoring and keeps that
computer's own otherwise - a recordings folder that is not there would fail
every recording.

Putting a backup back over everything replaces the files the backup has,
and only those: a file it does not have stays exactly as it is here. One
whose app settings hold neither playlists nor settings is not put back over
everything at all (replaceable()): it would only empty what is here.

What runs something, lets others in or sends the connections elsewhere
(risky, risky_sources - one choice each, risky_choices) is brought only when
the person ticks it, in either way of restoring (without): a setting not
ticked stays as it is here (Backup.keep, kept with a staged restore), a
playlist not ticked comes without those options.

That is done as the app starts: stage() now, apply_pending() at the next
start, before anything reads the settings.
The running window holds its stores in memory and writes them back as it
closes (recordings, resume points, watched marks), which would have
overwritten a restore written under it. A file that does not read as a whole
is refused before anything changes.

An export from before this module - a list of playlists - still reads, as
playlists only.

Profiles (core/profiles.py) go in whole: the household's list and each
profile's own keys, kept under ``profiles/<id>/`` in the same four files.
A key is covered by what it is without that part. A backup that has them
counts each profile's own; the copies the first start left under the old
names (for a downgrade) go along but are not counted twice. One from before
profiles puts the old names back, and the next start makes them the first
profile's again.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
import re
import secrets
import tempfile
import unicodedata
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from . import chacha20poly1305 as _aead
from . import profiles as _profiles
from .log import log

FORMAT = 1
MAGIC = "dopeiptv_backup"
#: The settings files a backup covers, by the name it gives them.
STORES = ("main", "resume", "follow", "cache")
#: A backup is a few MB at most; past this it is not one.
MAX_BYTES = 64 * 1024 * 1024
PENDING_NAME = "restore-pending.json"

#: What a backup counts, in the order a summary lists it.
KINDS = ("sources", "profiles", "favorites", "history", "resume", "watched",
         "watchlist", "followed", "reminders", "recordings", "hidden", "numbers",
         "settings")

# The other files give only these keys (the name, or the name + "_<source>").
_ONLY = {"resume": "resume_positions", "follow": "followed_series"}
_CACHE_KEYS = frozenset({"trakt_watched_cache", "trakt_watchlist_cache"})

#: Never in a backup, and never taken from one.
_LEFT_OUT = frozenset({
    # Where the windows sit on this computer's screen.
    "window_geometry", "window_state", "splitter_state", "sidebar_collapsed",
    "docked_video_height", "mv_geometry", "popout_geometry",
    "guide_channel_col",
    # This computer's graphics and desktop: on another one X11 or a decoder
    # chosen here can cost the picture.
    "force_x11", "hwdec_mode", "desktop_entry", "desktop_entry_asked",
    # The copy an update replaced (deleted at start).
    "selfupdate_old_path",
    # The reply codes of this computer's feedback reports.
    "feedback_tickets", "feedback_last", "feedback_sent",
    # Whoever has it controls the player: never from a file (a new one is
    # made here when remote control or sharing is turned on).
    "nerd_remote_token",
    # The phone remote: its address (the token in it), its port and whether
    # it is on belong to this computer and its home network.
    "phone_remote", "phone_remote_token", "phone_remote_port",
    # This install's own id at Jellyfin, Emby and Plex: each computer has its
    # own (providers/mediaserver.py).
    "media_device_id",
    # Server mode's token (whoever has it watches through this computer) and
    # its HDHomeRun device id: two computers with one id confuse Plex.
    "nerd_server_token", "nerd_server_admin_token", "nerd_server_device_id",
    # Caches the app fills again by itself.
    "local_tmdb_posters", "local_tmdb_posters_v2", "music_art_v1",
    "cast_devices", "cast_last_device", "subtitle_files", "nerd_health_data",
    "channel_stream_info",
    # Recordings of this computer still to be saved as MP4 (its own paths).
    "rec_mp4_pending",
    # When this computer's own copy of yt-dlp was last compared with the
    # newest release.
    "ytdlp_checked",
    # The PIN's wrong guesses and how long they lock it (core/pins.Tries):
    # an older file put back would give the guesses back.
    "pin_tries",
})
# update_*: the update channel and what its check last found; ts_*: what a
# source's timeshift probe learned; "_": the files' own markers;
# nerd_mirror_*: the backup address that answered last on this computer;
# downloads_*: the downloads' queue, folder and limit - this computer's files
# (core/downloads.py), never in a backup.
_LEFT_OUT_PREFIXES = ("update_", "home_pcache_", "ts_broken/", "ts_depth/", "_",
                      "nerd_mirror_", "downloads_")
_TMDB_CACHE = re.compile(r"tmdb_.*(cache|matcher)")

#: Settings naming a folder ("dir"), a JSON list of folders ("dirs") or files
#: one per line ("files").
_PATHS = {"recordings_dir": "dir", "local_open_dir": "dir",
          "nerd_picon_dir": "dir", "nerd_mpv_config_dir": "dir",
          "local_dirs": "dirs", "nerd_shaders": "files"}

#: Settings that run something or send the connections, or what is played,
#: elsewhere: a restore shows them before it is made. Headers by their
#: names only (a value may be a token).
#: The shaders run on the graphics card; the other networks let more of the
#: network reach server mode's tuner, which takes no token.
RISKY = ("nerd_rec_post", "nerd_mpv_options", "nerd_mpv_config_dir", "nerd_shaders",
         "nerd_proxy", "nerd_headers", "nerd_udpxy", "nerd_hooks_urls", "nerd_hooks_header",
         "nerd_mqtt_host", "nerd_dns_url", "nerd_other_networks")
#: Switches that let others in or send out what is played: shown when on.
#: Server mode among them: it serves the channels to the home network, its
#: tuner without a token; and a log file kept, which names what plays.
RISKY_ON = ("nerd_remote", "nerd_share", "nerd_mqtt_commands", "nerd_server", "nerd_log_file")
#: Those of them that the home network choice (nerd_remote_lan) opens wider.
_LAN_ON = ("nerd_remote", "nerd_share")
#: Server mode's channels for adults: said with its switch when both are on.
_SERVER_ADULT = "nerd_server_adult"
#: The sync code: a backup that has one joins this computer to that sync -
#: whoever made the file reads what is synced from here on. Shown as set,
#: never its value (core/sync.K_CODE).
SYNC_CODE = "sync_code"
#: A sign-in to a watch history (any profile's): what plays here is marked
#: on that account from then on - another person's, in a file of theirs.
#: Shown as set, never its token.
SIGN_INS = ("trakt_access_token", "simkl_access_token")
#: A playlist's own options that do the same for its connections (its
#: backup addresses, a certificate trusted for it, its own DNS server).
RISKY_SOURCE = ("proxy", "headers", "rewrite", "udpxy", "catchup_template",
                "backup_servers", "dns_url", "cert_pin", "tls_unchecked")
#: A playlist's options that are one setting with one of RISKY_SOURCE (as
#: core/sync.py holds them back together): left out with it.
_SOURCE_WITH = {"cert_pin": ("cert_pin_for",), "catchup_template": ("catchup_mode",),
                "dns_url": ("dns",)}
#: What a risky setting brings with it, brought only with it: server mode's
#: reach with its switch, a sync's own with its code, a watch history's
#: whole sign-in with its token (a renewal token alone would sign in again;
#: providers/trakt.SIGN_IN_KEYS, providers/simkl.SIGN_IN_KEYS).
_WITH = {"nerd_server": (_SERVER_ADULT, "nerd_server_open", "nerd_server_open_hosts"),
         SYNC_CODE: ("sync_cats", "sync_source_ids"),
         "trakt_access_token": ("trakt_refresh_token", "trakt_expires_at"),
         "simkl_access_token": ("simkl_refresh_token", "simkl_token_expires")}
#: The remote API's and sharing's reach: brought only with one of them.
_LAN = "nerd_remote_lan"
#: A choice of risky_choices() that is a playlist's: "source:<its place>".
SOURCE_CHOICE = "source:"
#: The most keys a restore keeps as they are here (Backup.keep).
_KEEP_MAX = 256
_HEADERS = frozenset({"nerd_headers", "nerd_hooks_header", "headers"})
_HEADER_NAME = re.compile(r"^\s*([A-Za-z0-9!#$%&'*+.^_`|~-]+)\s*[:=]")
#: The kinds a summary counts, by the file that holds them.
_KINDS_OF = {"main": ("sources", "profiles", "favorites", "history", "reminders",
                      "recordings", "hidden", "numbers", "settings"),
             "resume": ("resume",), "follow": ("followed",), "cache": ("watched", "watchlist")}

_SOURCE_KEYS = frozenset({"playlists", "active_playlist", "playlist_name",
                          "server", "username", "password"})
_FAVORITES = re.compile(r"(?:movie_|series_|radio_|web_)?favorites(?:_.*)?")
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
    (not a backup, or damaged), "newer" (*detail*: the version it came
    from), "locked" (a backup with a password, read where none is asked
    for: read_any() and Locked.unlock() open one) or "password" (the wrong
    password, or a file changed since it was made - never which)."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass
class Backup:
    """A backup as read: *kind* "full", or "playlists" for an old export.
    *keep*: the app settings (by their names without a profile's part) a
    restore over everything leaves as they are here - the risky ones the
    person did not tick (``without``), the logins a backup without a
    password left out. *logins_out*: it holds no logins (without_logins)."""

    kind: str
    stores: dict[str, dict[str, Any]] = field(default_factory=dict)
    sources: list[dict] = field(default_factory=list)
    app_version: str = ""
    created: str = ""
    keep: list[str] = field(default_factory=list)
    logins_out: bool = False

    @property
    def full(self) -> bool:
        return self.kind == "full"

    def document(self) -> dict[str, Any]:
        doc: dict[str, Any] = {MAGIC: FORMAT, "app_version": self.app_version,
                               "created": self.created, "stores": self.stores}
        if self.keep:
            doc["keep"] = list(self.keep)
        if self.logins_out:
            doc[NO_LOGINS] = _LEFT
        return doc


# -- what goes in ----------------------------------------------------------------

def left_out(key: str) -> bool:
    """Whether the app's settings key *key* stays out of a backup."""
    return (key in _LEFT_OUT or key.startswith(_LEFT_OUT_PREFIXES)
            or _TMDB_CACHE.match(key) is not None)


def _base(key: str) -> str:
    """A profile's own key without its ``profiles/<id>/`` part."""
    got = _profiles.split_key(key)
    return got[1] if got is not None else key


def covered(store: str, key: str) -> bool:
    """Whether *key* of the file *store* goes in a backup (and is replaced by
    a restore). A profile's own key is covered as the key it is."""
    key = _base(key)
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


def encode(doc: Mapping[str, Any]) -> bytes:
    """*doc* as the bytes of its file (and, with a password, what is
    encrypted). Raises ValueError for what JSON cannot hold."""
    return json.dumps(doc, ensure_ascii=False, indent=2,
                      allow_nan=False).encode("utf-8")


def write(path: str, doc: Mapping[str, Any]) -> None:
    """*doc* to *path* as a whole or not at all: written beside it and moved
    over it. Raises OSError (and leaves no half file) when it cannot be."""
    data = encode(doc)
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


# -- a password on it ------------------------------------------------------------

#: MAGIC's value in a backup with a password, and that layout's number.
LOCKED = "encrypted"
LOCK_V = 1
KDF = "pbkdf2-sha256"
CIPHER = "chacha20-poly1305"
#: PBKDF2's rounds for a backup locked now - as many as a PIN's
#: (core/pins.ROUNDS), some tenths of a second.
ROUNDS = 600_000
#: The shortest password a backup is locked with: a short one is guessed
#: whatever the rounds.
PASSWORD_MIN = 10
#: What a file may say: fewer is no file of ours and quicker to guess at,
#: more would take minutes to open. Refused before any work.
MIN_ROUNDS = 100_000
MAX_ROUNDS = 5_000_000
SALT_BYTES = 16
NONCE_BYTES = 12
_TAG_BYTES = 16
#: A locked file's fields besides "data", in the order it has them.
_HEADER = (MAGIC, "v", "kdf", "cipher", "rounds", "salt", "nonce")


def _key(password: str, salt: bytes, rounds: int) -> bytes:
    """The 32-byte key PBKDF2-HMAC-SHA256 makes of *password* and *salt*.
    NFC first: the same word typed on another keyboard or system, its
    letters composed otherwise, is the same key."""
    return hashlib.pbkdf2_hmac("sha256", unicodedata.normalize("NFC", password).encode("utf-8"),
                               salt, rounds, 32)


def _aad(header: Mapping[str, Any]) -> bytes:
    """The header as the tag covers it: canonical JSON - keys sorted, no
    spaces, ASCII - so any change to it fails as a changed byte does."""
    return json.dumps(dict(header), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("ascii")


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: Any, size: int = 0) -> bytes:
    """Base64 as this app writes it, strictly: the alphabet and padding
    only, the canonical spelling, *size* bytes when given."""
    if not isinstance(text, str) or not text.isascii():
        raise BackupError("invalid", "base64")
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise BackupError("invalid", "base64") from None
    if _b64(raw) != text or (size and len(raw) != size):
        raise BackupError("invalid", "base64")
    return raw


def seal(plain: bytes, password: str, rounds: int = ROUNDS,
         salt: bytes | None = None, nonce: bytes | None = None) -> dict[str, Any]:
    """*plain* - a backup's bytes (encode()) - locked with *password*: the
    file's object, its header and its data. *salt* and *nonce* are new
    random bytes every time (secrets); given only by the test vectors.
    Raises ValueError for an empty password, rounds out of range, or a salt
    or nonce of the wrong size."""
    if not password:
        raise ValueError("an empty password locks nothing")
    if isinstance(rounds, bool) or not MIN_ROUNDS <= rounds <= MAX_ROUNDS:
        raise ValueError("rounds out of range")
    salt = secrets.token_bytes(SALT_BYTES) if salt is None else salt
    nonce = secrets.token_bytes(NONCE_BYTES) if nonce is None else nonce
    if len(salt) != SALT_BYTES or len(nonce) != NONCE_BYTES:
        raise ValueError("a salt or nonce of the wrong size")
    header = {MAGIC: LOCKED, "v": LOCK_V, "kdf": KDF, "cipher": CIPHER,
              "rounds": rounds, "salt": _b64(salt), "nonce": _b64(nonce)}
    key = _key(password, salt, rounds)
    try:
        data = _aead.encrypt(key, nonce, plain, _aad(header))
    finally:
        del key
    return {**header, "data": _b64(data)}


def lock(doc: Mapping[str, Any], password: str, rounds: int = ROUNDS) -> dict[str, Any]:
    """collect()'s *doc* locked with *password* (seal(), of its bytes).
    Slow - PBKDF2's rounds, then the whole file in plain Python: never on
    the window's thread."""
    return seal(encode(doc), password, rounds)


def for_file(doc: Mapping[str, Any], password: str = "") -> Mapping[str, Any]:
    """What goes in the file: with a password, lock()'s - every login in it,
    encrypted; without one, *doc* without its logins (without_logins), so
    a file anyone can read holds none."""
    return lock(doc, password) if password else without_logins(doc)


# -- without a password: no logins -------------------------------------------------

#: The app settings that are a login, a token or the sync code: a backup
#: without a password leaves them out, and a restore of one keeps this
#: computer's own (Backup.keep).
LOGIN_SETTINGS = ("trakt_access_token", "trakt_refresh_token", "trakt_client_secret",
                  "simkl_access_token", "simkl_refresh_token", "simkl_token_expires",
                  "os_password", "os_api_key", "tmdb_api_key", SYNC_CODE,
                  "nerd_mqtt_pass", "nerd_hooks_header")
#: Settings that hold a login only sometimes - a proxy's address with one,
#: a header that opens something, a Kodi's password: left out then.
_LOGIN_IN = ("nerd_proxy", "nerd_headers", "kodi_devices")
#: A playlist's own fields that are a login.
LOGIN_FIELDS = ("password", "token", "mac")
#: A playlist's addresses that may carry one (user:password@, a query's
#: login words), and its options that may.
_LOGIN_URLS = ("server", "url", "epg_url")
_LOGIN_URL_OPTIONS = ("proxy", "backup_servers")
#: The document's own word that its logins were left out.
NO_LOGINS = "logins"
_LEFT = "left out"


def _no_login_word(piece: str) -> str:
    from urllib.parse import unquote_plus

    from .player_exec import LOGIN_WORDS
    key, eq, _value = piece.partition("=")
    return f"{key}=" if eq and unquote_plus(key).strip().lower() in LOGIN_WORDS else piece


def _no_login_url(text: str) -> str:
    """*text* - an address, or lines of them - without what opens an
    account in it: a login before its host and the values of a query's
    login words (player_exec.LOGIN_WORDS). A login in an address's path
    (an Xtream stream's) cannot be told from a folder: a source's server
    holds none."""
    from urllib.parse import urlsplit, urlunsplit
    out = []
    for line in str(text or "").split("\n"):
        if "://" not in line:
            out.append(line)
            continue
        try:
            parts = urlsplit(line)
        except ValueError:
            out.append("")
            continue
        query = "&".join(_no_login_word(p) for p in parts.query.split("&")) if parts.query else ""
        out.append(urlunsplit((parts.scheme, parts.netloc.rpartition("@")[2], parts.path,
                               query, parts.fragment)))
    return "\n".join(out)


def _no_login_headers(value: Any) -> Any:
    """Headers (lines of "Name: value", or a mapping) with only those that
    open nothing (net_bridge.SAFE_HEADERS, and the user agent)."""
    from .net_bridge import SAFE_HEADERS
    safe = SAFE_HEADERS | {"user-agent"}
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if str(k).strip().lower() in safe}
    lines = [ln for ln in str(value or "").splitlines()
             if (m := _HEADER_NAME.match(ln)) and m.group(1).lower() in safe]
    return "\n".join(lines)


def _holds_login(key: str, value: Any) -> bool:
    """Whether the setting *key* (one of _LOGIN_IN) holds a login now."""
    if key == "nerd_proxy":
        return _no_login_url(str(value or "")) != str(value or "")
    if key == "nerd_headers":
        return _no_login_headers(value) != (value if isinstance(value, dict) else
                                            "\n".join(str(value or "").splitlines()))
    try:
        devices = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return bool(value)
    return any(isinstance(d, dict) and d.get("password") for d in devices or [])


def _playlists_without_logins(text: str) -> str:
    try:
        rows = json.loads(text)
    except ValueError:
        return "[]"
    out = []
    for p in rows if isinstance(rows, list) else []:
        if not isinstance(p, dict):
            continue
        p = dict(p)
        for f in LOGIN_FIELDS:
            if p.get(f):
                p[f] = ""
        for f in _LOGIN_URLS:
            if isinstance(p.get(f), str):
                p[f] = _no_login_url(p[f])
        opts = p.get("options")
        if isinstance(opts, dict):
            opts = dict(opts)
            for f in _LOGIN_URL_OPTIONS:
                if isinstance(opts.get(f), str):
                    opts[f] = _no_login_url(opts[f])
            if opts.get("headers"):
                opts["headers"] = _no_login_headers(opts["headers"])
            p["options"] = opts
        out.append(p)
    return json.dumps(out, ensure_ascii=False)


def without_logins(doc: Mapping[str, Any]) -> dict[str, Any]:
    """collect()'s *doc* with no login in it - for a file without a
    password, which anyone who gets it can read: the settings that are one
    (LOGIN_SETTINGS) or hold one now (_LOGIN_IN) left out, a playlist's
    password, token and box MAC emptied and a login in its addresses taken
    out (_no_login_url, _no_login_headers). It says so (NO_LOGINS), and the
    settings left out are kept as they are wherever it is put back (keep);
    a playlist that is there takes that computer's login (_logins_from_here)."""
    stores = {name: dict(values) for name, values in dict(doc.get("stores") or {}).items()}
    main = stores.get("main", {})
    keep = set(doc.get("keep") or [])
    for key in list(main):
        base = _base(key)
        if base in LOGIN_SETTINGS or (base in _LOGIN_IN and _holds_login(base, main[key])):
            del main[key]
            keep.add(base)
        elif base == "playlists" and isinstance(main[key], str):
            main[key] = _playlists_without_logins(main[key])
    out: dict[str, Any] = {k: v for k, v in doc.items() if k not in ("stores", "keep")}
    out["stores"] = stores
    out[NO_LOGINS] = _LEFT
    if keep:
        out["keep"] = sorted(keep)[:_KEEP_MAX]
    return out


def _logins_from_here(incoming: Any, here: Any) -> Any:
    """A playlist list from a backup without logins (*incoming*, JSON text)
    with this computer's logins for the playlists it has too (*here*): the
    fields emptied filled in again, an address that is this one's but for
    its login this one's. Anything that does not read as playlists stays as
    it came."""
    try:
        rows, mine = json.loads(incoming), json.loads(here) if here else []
    except (TypeError, ValueError):
        return incoming
    if not isinstance(rows, list) or not isinstance(mine, list):
        return incoming
    by_id = {str(h.get("id")): h for h in mine if isinstance(h, dict) and h.get("id")}
    for p in rows:
        h = by_id.get(str(p.get("id"))) if isinstance(p, dict) else None
        if h is None:
            continue
        for f in LOGIN_FIELDS:
            if not p.get(f) and h.get(f):
                p[f] = h[f]
        for f in _LOGIN_URLS:
            if isinstance(h.get(f), str) and p.get(f) == _no_login_url(h[f]):
                p[f] = h[f]
        opts, theirs = p.get("options"), h.get("options")
        if isinstance(opts, dict) and isinstance(theirs, dict):
            for f in _LOGIN_URL_OPTIONS:
                if isinstance(theirs.get(f), str) and opts.get(f) == _no_login_url(theirs[f]):
                    opts[f] = theirs[f]
            if theirs.get("headers") and opts.get("headers", "") == _no_login_headers(theirs["headers"]):
                opts["headers"] = theirs["headers"]
    return json.dumps(rows, ensure_ascii=False)


def is_locked(data: Any) -> bool:
    """Whether the decoded JSON *data* is a backup with a password."""
    return isinstance(data, dict) and data.get(MAGIC) == LOCKED


@dataclass(frozen=True, repr=False)
class Locked:
    """A backup with a password as read: its header checked (locked()),
    what it holds still sealed until unlock()."""

    rounds: int
    salt: bytes
    nonce: bytes
    data: bytes
    aad: bytes

    def __repr__(self) -> str:
        return f"Locked({self.rounds} rounds, {len(self.data)} bytes)"

    def unlock(self, password: str) -> Backup:
        """The backup inside, opened with *password*. Slow - PBKDF2's
        rounds, then the whole file in plain Python - so a worker's job,
        never the window's thread. A wrong password and a changed file are
        one error, BackupError "password": nothing says which. What is
        inside is then read as a backup without a password is."""
        key = _key(password, self.salt, self.rounds)
        try:
            plain: bytes | None = _aead.decrypt(key, self.nonce, self.data, self.aad)
        except ValueError:
            plain = None
        finally:
            del key
        if plain is None:
            raise BackupError("password")
        data = _decoded(plain, said=False)
        if is_locked(data):
            raise BackupError("invalid", "locked twice")
        return parse(data)


def locked(data: Mapping[str, Any]) -> Locked:
    """The header of a backup with a password (is_locked) checked whole,
    before any work: its layout, scheme, rounds within MIN_ROUNDS and
    MAX_ROUNDS, a salt and a nonce of their exact sizes, strict base64.
    Raises BackupError ("invalid", or "newer" for a later layout)."""
    v = data.get("v")
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise BackupError("invalid", "format")
    if v > LOCK_V:
        raise BackupError("newer", "")
    if set(data) != {*_HEADER, "data"}:
        raise BackupError("invalid", "fields")
    if data["kdf"] != KDF or data["cipher"] != CIPHER:
        raise BackupError("invalid", "scheme")
    rounds = data["rounds"]
    if isinstance(rounds, bool) or not isinstance(rounds, int) \
            or not MIN_ROUNDS <= rounds <= MAX_ROUNDS:
        raise BackupError("invalid", "rounds")
    salt = _unb64(data["salt"], SALT_BYTES)
    nonce = _unb64(data["nonce"], NONCE_BYTES)
    sealed = _unb64(data["data"])
    if len(sealed) <= _TAG_BYTES:
        raise BackupError("invalid", "data")
    return Locked(rounds, salt, nonce, sealed, _aad({k: data[k] for k in _HEADER}))


# -- reading one -----------------------------------------------------------------

def _raw(path: str) -> bytes:
    try:
        if os.path.getsize(path) > MAX_BYTES:
            raise BackupError("invalid", "too large")
        with open(path, "rb") as fh:
            raw = fh.read(MAX_BYTES + 1)
    except OSError as e:
        raise BackupError("unreadable", str(e)) from e
    if len(raw) > MAX_BYTES:
        raise BackupError("invalid", "too large")
    return raw


def _decoded(raw: bytes, said: bool = True) -> Any:
    """*raw* as JSON. *said*: the parser's words in the error (not for what
    a password opened - they can quote a byte of it)."""
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError) as e:
        raise BackupError("invalid", str(e) if said else type(e).__name__) from None


def read_any(path: str) -> Backup | Locked:
    """The backup in the file *path*, checked whole - or, for one with a
    password, its header checked and the rest still sealed (Locked).
    Raises BackupError."""
    data = _decoded(_raw(path))
    return locked(data) if is_locked(data) else parse(data)


def read(path: str) -> Backup:
    """The backup in the file *path*, checked whole. Raises BackupError -
    "locked" for one with a password (read_any)."""
    got = read_any(path)
    if isinstance(got, Locked):
        raise BackupError("locked")
    return got


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
    # What a restore keeps here (a staged one's: the risky settings not
    # ticked). From someone else's file it can only keep more of what is
    # here; anything that is not a short list of keys is passed over.
    keep = data.get("keep")
    keep = ([k for k in keep if _good_key(k)][:_KEEP_MAX]
            if isinstance(keep, list) and all(isinstance(k, str) for k in keep) else [])
    return Backup("full", clean, _sources(clean.get("main", {}).get("playlists")),
                  app_version, created if isinstance(created, str) else "", keep,
                  data.get(NO_LOGINS) == _LEFT)


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
    if key in _SOURCE_KEYS or key in (_profiles.K_ACTIVE, _profiles.K_ASK,
                                      _profiles.K_MIGRATED, _profiles.K_REMOVED):
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


def _taken(b: Backup, name: str) -> dict[str, Any]:
    """What a restore takes of the file *name* in *b*."""
    return {k: v for k, v in b.stores.get(name, {}).items() if covered(name, k)}


def _counted(b: Backup, name: str) -> list[tuple[str, Any]]:
    """(key without its profile, value) of what a restore takes of file
    *name*, each profile's own once: with profiles in the backup, the old
    names' copies are not counted again."""
    taken = _taken(b, name)
    profiled = any(_profiles.split_key(k) is not None for k in _taken(b, "main")) \
        or any(_profiles.split_key(k) is not None for k in taken)
    out = []
    for key, value in taken.items():
        if profiled and _profiles.split_key(key) is None and _profiles.is_own(key):
            continue
        out.append((_base(key), value))
    return out


def summary(b: Backup) -> dict[str, int]:
    """How much of each of KINDS *b* holds - of what a restore takes."""
    counts = dict.fromkeys(KINDS, 0)
    counts["sources"] = len(b.sources)
    for key, value in _counted(b, "main"):
        if key == _profiles.K_LIST:
            got = _json(value)
            counts["profiles"] = len(got) if isinstance(got, list) else 0
            continue
        kind = _kind_of(key)
        if kind:
            counts[kind] += _count(kind, value)
    for name, kind in (("resume", "resume"), ("follow", "followed")):
        for _key, value in _counted(b, name):
            data = _json(value)
            counts[kind] += len(data) if isinstance(data, dict) else 0
    for key, value in _counted(b, "cache"):
        data = _json(value)
        if key == "trakt_watched_cache" and isinstance(data, dict) \
                and isinstance(data.get("local_items"), list):
            counts["watched"] += len(data["local_items"])
        elif key == "trakt_watchlist_cache" and isinstance(data, dict):
            counts["watchlist"] += sum(len(data[k]) for k in ("movies", "shows")
                                       if isinstance(data.get(k), list))
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


def replaceable(b: Backup) -> bool:
    """Whether *b* can be put back over everything: a full backup whose app
    settings hold playlists or settings. One without them would only empty
    what is here."""
    if not b.full or "main" not in b.stores:
        return False
    counts = summary(b)
    return counts["sources"] > 0 or counts["settings"] > 0


def kept(b: Backup) -> list[str]:
    """The KINDS a restore of *b* over everything leaves as they are here:
    those of the files it does not have."""
    return [kind for name in STORES if name not in b.stores for kind in _KINDS_OF[name]]


def _on(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() in ("true", "1"))


def _shown(key: str, value: str) -> str:
    """*value* of the setting *key* as the restore shows it: headers by
    their names (core/nerd.parse_headers' lines; a webhook header that is a
    token alone goes as Authorization)."""
    if key not in _HEADERS:
        return " ".join(value.split())
    names = []
    for line in value.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        m = _HEADER_NAME.match(line)
        if m or key == "nerd_hooks_header":
            names.append(m.group(1) if m else "Authorization")
    return ", ".join(dict.fromkeys(names))


def risky(b: Backup) -> list[tuple[str, str]]:
    """(key, what to show) of the RISKY settings *b* sets and the RISKY_ON
    switches it turns on ("" for a switch, or the setting that widens it:
    "nerd_remote_lan" when it also opens them to the home network,
    "nerd_server_adult" when server mode hands out channels for adults),
    and (SYNC_CODE, "") when it joins this computer to a sync, (a SIGN_INS
    key, "") for each watch history it signs in to."""
    main = _taken(b, "main")
    out = [(key, shown) for key in RISKY
           if isinstance(main.get(key), str) and (shown := _shown(key, main[key]))]
    lan = "nerd_remote_lan" if _on(main.get("nerd_remote_lan")) else ""
    adult = _SERVER_ADULT if _on(main.get(_SERVER_ADULT)) else ""
    out += [(key, lan if key in _LAN_ON else adult if key == "nerd_server" else "")
            for key in RISKY_ON if _on(main.get(key))]
    if isinstance(main.get(SYNC_CODE), str) and main[SYNC_CODE].strip():
        out.append((SYNC_CODE, ""))
    for token in SIGN_INS:
        if any(isinstance(v, str) and v.strip() and _base(k) == token for k, v in main.items()):
            out.append((token, ""))
    return out


def _risky_keys(options: Any) -> list[str]:
    """The RISKY_SOURCE options a playlist's *options* set."""
    if not isinstance(options, dict):
        return []
    return [k for k in RISKY_SOURCE if isinstance(options.get(k), str) and options[k].strip()]


def _risky_rows(b: Backup) -> list[tuple[int, str, list[str]]]:
    """(its place in b.sources, its name, its RISKY_SOURCE options) of each
    playlist in *b* that sets any."""
    out = []
    for i, p in enumerate(b.sources):
        keys = _risky_keys(p.get("options"))
        if keys:
            name = p.get("name") if isinstance(p.get("name"), str) else ""
            server = p.get("server") if isinstance(p.get("server"), str) else ""
            out.append((i, name or server or "?", keys))
    return out


def risky_sources(b: Backup) -> list[tuple[str, list[str]]]:
    """(playlist name, its RISKY_SOURCE options set) of each playlist in *b*
    that has any."""
    return [(name, keys) for _i, name, keys in _risky_rows(b)]


def risky_choices(b: Backup) -> list[str]:
    """Each risky thing *b* would bring, as one choice the person ticks or
    not: the keys risky() names, in its order, then SOURCE_CHOICE + the
    place in b.sources of each playlist risky_sources() names, in its order
    - one per line the restore shows."""
    return ([key for key, _shown in risky(b)]
            + [f"{SOURCE_CHOICE}{i}" for i, _name, _keys in _risky_rows(b)])


def without(b: Backup, take: Iterable[str] = ()) -> Backup:
    """*b* bringing only the risky things in *take* (risky_choices' names)
    and everything else as it is: a risky setting not ticked - with what is
    one setting with it (_WITH, the remote's reach _LAN unless the remote
    or sharing is ticked) - is not taken, and a restore over everything
    leaves this computer's own as it is (``Backup.keep``); a playlist not
    ticked comes without its RISKY_SOURCE options (and their companions,
    _SOURCE_WITH), in either way of restoring. Nothing is ticked by default:
    ``without(b)`` brings none of them."""
    take = set(take)
    main = dict(b.stores.get("main", {}))
    keep = set(b.keep)
    choices = risky_choices(b)
    left = [key for key in choices if key not in take]
    for key in left:
        if key.startswith(SOURCE_CHOICE):
            continue
        keep.update((key, *_WITH.get(key, ())))
    if any(key in _LAN_ON for key in left) and not take & set(_LAN_ON):
        keep.add(_LAN)
    for k in list(main):
        if _base(k) in keep:
            del main[k]
    gone = {int(key[len(SOURCE_CHOICE):]) for key in left if key.startswith(SOURCE_CHOICE)}
    sources = []
    for i, p in enumerate(b.sources):
        if i in gone:
            keys = _risky_keys(p.get("options"))
            drop = set(keys) | {c for k in keys for c in _SOURCE_WITH.get(k, ())}
            p = dict(p, options={k: v for k, v in p["options"].items() if k not in drop})
        sources.append(p)
    stores = dict(b.stores)
    if "main" in stores:
        if gone and isinstance(main.get("playlists"), str):
            main["playlists"] = json.dumps(sources, ensure_ascii=False)
        stores["main"] = main
    out = Backup(b.kind, stores, sources, b.app_version, b.created,
                 sorted(keep)[:_KEEP_MAX] if b.full else [], b.logins_out)
    if gone or left:
        log.info("restore: %d risky setting(s) not brought", len(left))
    return out


def new_token_here(b: Backup) -> bool:
    """Whether a restore should say that the remote control's token is not
    taken from *b*: it has one, or turns remote control or sharing on."""
    main = b.stores.get("main", {})
    return "nerd_remote_token" in main or any(_on(main.get(k)) for k in _LAN_ON)


# -- putting it back -------------------------------------------------------------

def restore(b: Backup, stores: Mapping[str, Settings | None],
            exists: Callable[[str], bool] = os.path.exists) -> list[str]:
    """Replace everything a backup covers in *stores* with what *b* holds,
    in each file *b* has: keys it covers that *b* lacks go, keys it leaves
    out stay as they are, and a file *b* does not have is not touched.
    Returns the path settings left out (see missing_paths). All is worked out
    before the first write. Raises ValueError for a backup that is not
    replaceable()."""
    if not b.full:
        raise ValueError("a playlists file holds no settings to put back")
    if not replaceable(b):
        raise ValueError("the backup holds no playlists or settings to put back")
    plan: list[tuple[Settings, list[str], dict[str, Any]]] = []
    left: list[str] = []
    for name in STORES:
        s = stores.get(name)
        if s is None or name not in b.stores:
            continue
        incoming = {k: v for k, v in b.stores.get(name, {}).items()
                    if covered(name, k)}
        keep_here: set[str] = set()
        if name == "main" and b.keep:
            # The risky settings not ticked (without): this computer's own
            # stay as they are, whichever profile's they are.
            kept_names = set(b.keep)
            incoming = {k: v for k, v in incoming.items() if _base(k) not in kept_names}
            with _no_fallbacks(s):
                keep_here |= {k for k in s.allKeys() if _base(k) in kept_names}
        if name == "main" and b.logins_out:
            # A backup without logins: a playlist this computer has too
            # keeps its login here (_logins_from_here).
            with _no_fallbacks(s):
                for key in [k for k in incoming if _base(k) == "playlists"]:
                    incoming[key] = _logins_from_here(incoming[key], s.value(key))
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
    the file it waits in. Raises OSError, and ValueError for a backup that
    is not replaceable()."""
    if not b.full:
        raise ValueError("a playlists file holds no settings to put back")
    if not replaceable(b):
        raise ValueError("the backup holds no playlists or settings to put back")
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
        if not replaceable(b):
            raise BackupError("invalid", "no playlists or settings")
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
