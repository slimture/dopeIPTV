"""Settings sync for everyone: favorites, settings, playlists and watch
history kept the same on several computers through dopeiptv.org, end-to-end
encrypted.

Privacy. The secret is a random 256-bit *sync code*, made on the first
computer and typed or pasted on the others. From it come (HKDF-SHA256) the
*slot* - the random-looking number the site keeps the data under - and the
key it is encrypted with (ChaCha20-Poly1305, core/chacha20poly1305.py),
here, before anything is sent. The site sees the slot, the size and when;
it never sees the code or the key, and cannot read a byte of what is kept.
Nor can it change one unnoticed: the tag refuses a changed copy. What it
could do is hand back an earlier copy of its own; every copy carries a
counter, sealed with the rest (counter()), so a computer that has seen a
later one says so, keeps its own and puts it back (RoundResult.older).
No account, no e-mail, no name and no supporter key go along: nothing that
says who asks (donate/sync.py on the website branch). A copy no computer
has read or written for 180 days is deleted there.

The slot names the copy; it does not open it. Every request also carries
a third value from the code (auth_of, X-Sync-Auth), of which the site
keeps only a SHA-256: the first write with it binds the slot to it, and
from then on a request without it - to read, write or delete - is turned
down. Whoever learns a slot (the site's own log, were there one, or its
disk) can neither delete the copy nor write another in its place. A slot
written only by an older app, which sends none, goes on as before until a
computer with this one writes to it; after that the older app's requests
are turned down and it says so (sync_err_refused) until it is updated.

What syncs (one switch each): favorites with their folders, settings from
an explicit allow-list (never window sizes, folders and paths of this
computer, the supporter key, the sync code, logins of other services),
playlists with their logins (off by default), and watch history with resume
points (off by default).

Keyboard shortcuts sync only between computers of the same system - macOS
with macOS, Linux with Linux, Windows with Windows: ⌘ is not Ctrl, and each
system keeps keys of its own. Kept here as they always were
(``keyboard_shortcuts``), they are sent as "keyboard_shortcuts@<system>"
(entry_of); a computer applies its own system's and leaves another's as it
is, neither applied nor removed (setting_of). An entry naming no system is
nobody's here: no version sent one, and what it was for cannot be told.

Profiles (core/profiles.py). Every profile's own syncs: the first profile's
under the entries it always had (so a computer with an older app syncs on
with it), each other one's under its id - a source's fingerprint as
"<fp>@<profile>", a setting as "profiles/<profile>/<key>" - and the
household's profiles themselves as "profile/<id>": the name, colour, emoji
and level, never the PIN (each computer keeps its own). A profile is added
or renamed by sync, never taken out.

How it merges. Each upload is a whole snapshot. Everything in it is an entry
with a time - a value, or a tombstone where something was removed - and two
snapshots merge entry by entry, the later one winning (ties go the same way
everywhere). Favorites are thereby a union in which a removal travels;
settings and playlists are last-writer-wins per key and per playlist;
history keeps the newest of each title. This computer keeps its own copy of
the last snapshot (with the times) beside the settings, and a change found
since then gets the time it was found. A computer that joins starts with
the time 0 on all of its own, so what the others had wins where both differ
and the rest is added.

What sync never puts in place by itself. A playlist's options that send its
connections elsewhere - its proxy, extra headers, address rewrite, udpxy,
catch-up address, backup addresses, own DNS server and trusted certificate
(core/backup.RISKY_SOURCE: the list a restored backup shows before it is
taken) - are worth more to whoever holds the sync code than anything else
in the copy: changed there, every stream of the playlist goes where they
say, and nothing on this computer would have said so. So where the copy has
one of them otherwise than this computer does, the rest of the playlist is
taken as ever and those stay as they are here - a playlist new here comes
without them (hold_back). What the copy has waits in this computer's state
(State.held), the window asks once (Applied.held; ui/mw_sync.py,
ui/sync_review.py), and only the person's answer moves it: taken
(take_held) or left (drop_held). While one waits, this computer's snapshot
of the playlist has the copy's value for it, so its own is not sent as a
change and the copy stays as it was; left, its own is the newer change, goes
to the others, and they are asked the same. A newer value from the copy
takes the waiting one's place, one the copy no longer has otherwise is
forgotten, and the computer that made a change is asked nothing about it.
An ``options`` that is no set of options - a list of pairs reads as one
where options are used - is never stored. The values are not logged, only
which they are: a proxy's can hold a login.

Plain data and the network only: nothing here touches a widget, and the
round trip (round_trip) runs on a worker thread. The window (ui/mw_sync.py)
reads and writes the stores on its own thread (collect, apply).
"""
from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
import zlib
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import build_check
from . import chacha20poly1305 as aead
from . import profiles as _profiles
from . import shortcut_keys
from .backup import RISKY_SOURCE
from .cert_pin import KEY as _PIN
from .cert_pin import KEY_FOR as _PIN_FOR
from .log import log
from .stores import FavoriteStore, HistoryStore

ENDPOINT = "https://dopeiptv.org/sync/"
#: The most the site keeps for one slot (the ciphertext).
MAX_BLOB = 2 * 1024 * 1024
FORMAT = 1
_AAD = b"dopeIPTV sync v1"
_SALT = b"dopeIPTV sync v1"
#: Inflated, the most a snapshot may be (a broken one cannot fill memory).
_MAX_PLAIN = 64 * 1024 * 1024

CATEGORIES = ("favorites", "settings", "sources", "history")
DEFAULT_CATS = ("favorites", "settings")

# This computer's own keys in the shared settings (all "sync_": none syncs).
K_CODE = "sync_code"
K_CATS = "sync_cats"
K_LAST = "sync_last"

#: A tombstone is forgotten after this long (a computer away for longer can
#: bring a removed thing back).
TOMB_DAYS = 400
#: Per playlist, the most history entries and resume points kept.
MAX_HISTORY = HistoryStore.MAX_ENTRIES
MAX_RESUME = 300


class SyncError(Exception):
    """A round that did not finish. *kind*: net (no answer), busy (the site
    is busy, or rate-limited), refused (the site turned the request down),
    full (the site has no room for a new copy; one kept there already can
    still be written), big (too much to sync), none (nothing under this
    code), read (the copy there could not be read with this code), newer (a
    newer app made it), modified (not asked: a modified build)."""

    def __init__(self, kind: str, size: int = 0) -> None:
        super().__init__(kind)
        self.kind = kind
        self.size = size


# -- the code, the slot and the key ---------------------------------------------

_B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
_LOOKALIKE = str.maketrans("018", "OIB")


def format_code(raw: bytes) -> str:
    """32 bytes as base32 in groups of four: 13 groups."""
    text = base64.b32encode(raw).decode("ascii").rstrip("=")
    return "-".join(text[i:i + 4] for i in range(0, len(text), 4))


def new_code() -> str:
    return format_code(secrets.token_bytes(32))


def parse_code(text: str) -> bytes | None:
    """The 32 bytes of a code as typed or pasted (any case, any separators,
    0/1/8 read as O/I/B), or None for anything that is not one."""
    s = "".join(ch for ch in str(text or "").upper() if ch.isalnum())
    s = s.translate(_LOOKALIKE)
    if len(s) != 52 or any(ch not in _B32 for ch in s):
        return None
    try:
        raw = base64.b32decode(s + "====")
    except (ValueError, TypeError):
        return None
    if len(raw) != 32 or format_code(raw).replace("-", "") != s:
        return None
    return raw


def masked(code: str) -> str:
    """The code with all but its first and last group hidden."""
    groups = code.split("-")
    if len(groups) < 3:
        return "••••"
    return "-".join([groups[0], "••••", "••••", "…", groups[-1]])


def hkdf(ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    """HKDF-SHA256 (RFC 5869)."""
    if not 0 < length <= 255 * 32:
        raise ValueError("HKDF-SHA256 makes 1 to 8160 bytes")
    prk = hmac.new(salt or b"\0" * 32, ikm, hashlib.sha256).digest()
    out, block = b"", b""
    counter = 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]


def derive(raw: bytes) -> tuple[str, bytes]:
    """(slot, key) for a code: the slot the site keeps it under (64 hex)
    and the encryption key, each from its own HKDF label - knowing one says
    nothing about the other."""
    slot = hkdf(raw, _SALT, b"slot", 32).hex()
    return slot, hkdf(raw, _SALT, b"key", 32)


def auth_of(raw: bytes) -> str:
    """The credential a request to the code's slot carries (64 hex): from a
    label of its own, so neither the slot nor the key says anything of it,
    and the site keeps only its SHA-256."""
    return hkdf(raw, _SALT, b"auth", 32).hex()


# -- a snapshot, sealed -----------------------------------------------------------

def empty_doc() -> dict[str, Any]:
    return {"v": FORMAT, "c": {}}


#: The copy's counter: one more on every upload, sealed with the rest, so
#: an earlier copy handed back is told from the latest. An entry in a
#: category of its own that no computer applies - an older app keeps it
#: as it keeps any entry (its merge takes the higher) without counting.
_META = "~"
_COUNTER = "n"


def counter(doc: dict[str, Any]) -> int:
    e = (doc["c"].get(_META) or {}).get(_COUNTER)
    if not isinstance(e, dict) or not isinstance(e.get("v"), int):
        return 0
    return max(0, int(e["v"]))


def _counted(doc: dict[str, Any], n: int) -> dict[str, Any]:
    """*doc* with the counter *n* (its categories copied, so a shared one
    is not changed)."""
    out = _copy(doc)
    out["c"][_META] = dict(out["c"].get(_META) or {}, **{_COUNTER: {"t": n, "v": n}})
    return out


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def seal(key: bytes, doc: dict[str, Any]) -> bytes:
    """The snapshot as the site keeps it: a format byte, a random nonce and
    the compressed JSON, encrypted and authenticated."""
    plain = zlib.compress(_canon(doc).encode("utf-8"), 6)
    nonce = secrets.token_bytes(12)
    head = bytes([FORMAT])
    return head + nonce + aead.encrypt(key, nonce, plain, _AAD + head)


def unseal(key: bytes, blob: bytes) -> dict[str, Any]:
    if not blob:
        raise SyncError("read")
    if blob[0] > FORMAT:
        raise SyncError("newer")
    if blob[0] != FORMAT or len(blob) < 1 + 12 + 16:
        raise SyncError("read")
    try:
        plain = aead.decrypt(key, blob[1:13], blob[13:], _AAD + blob[:1])
        inflate = zlib.decompressobj()
        text = inflate.decompress(plain, _MAX_PLAIN)
        if inflate.unconsumed_tail:
            raise ValueError("too big")
        doc = json.loads(text.decode("utf-8"))
    except (ValueError, zlib.error, UnicodeDecodeError) as e:
        raise SyncError("read") from e
    if not isinstance(doc, dict) or not isinstance(doc.get("c"), dict):
        raise SyncError("read")
    if int(doc.get("v") or 0) > FORMAT:
        raise SyncError("newer")
    clean = empty_doc()
    for cat, entries in doc["c"].items():
        if isinstance(entries, dict):
            clean["c"][str(cat)] = {
                str(k): e for k, e in entries.items()
                if isinstance(e, dict) and isinstance(e.get("t"), int)
                and ("v" in e or e.get("d") == 1)}
    return clean


# -- which settings go ------------------------------------------------------------

#: Settings that sync: how the app looks and plays, never this computer's.
SETTINGS_ALLOW = frozenset({
    "language",
    "theme", "accent", "accent_custom", "theme_follow_system", "theme_light",
    "corner_style", "ui_scale", "view_density", "view_grid", "sort_order", "tv_density",
    "start_place", "popular_movies", "popular_series", "follow_notify",
    # The Movies place's grid or list (ui/mw_place_films.py).
    "films_view",
    # What the places show (core/places.py), each profile its own.
    "places_hidden", "places_folded", "just_the_list",
    "place_row_tv_recent", "place_row_films_continue", "place_row_series_continue",
    "place_row_local_continue", "place_row_radio_favs", "place_row_rec_now",
    "stream_format", "cache_secs", "autoplay_preview", "autoplay_next_episode",
    "nextup_lead_secs", "auto_reconnect_live", "hide_upcoming_prompt",
    "live_rewind_minutes", "live_rewind_disk", "live_rewind_kept",
    "audio_lang", "sub_mode", "sub_lang", "sub_lang2", "sub_live", "sub_scale", "sub_color",
    "sub_background", "aspect_mode", "video_deinterlace", "video_sharpen",
    "video_tonemapping",
    "epg_upcoming_count", "epg_delay_min", "replay_delay_min", "guide_now_line",
    "mv_frameless", "mv_on_top", "mv_remember_geo", "mv_stop_docked",
    "mv_new_unmuted", "mv_conflict_mode", "mv_autohide_secs", "mv_seek_step_min",
    "mv_cells",
    "metadata_source", "music_art_online", "check_updates", "radio_report_clicks",
    # Under this system's name, between computers of one system (entry_of).
    shortcut_keys.SETTING,
})
#: Families of settings that sync: the sidebar's colours.
#: (Not the old editor's "shortcut/<platform>/<action>": moved into
#: keyboard_shortcuts at the first start since 1.5.0, and one left in a file
#: is another system's.)
SETTINGS_ALLOW_PREFIX = ("nav_color_", "nav_bg_")
#: Never, whatever the lists above say: what belongs to this computer, a
#: secret, or the sync itself.
SETTINGS_DENY = frozenset({
    "supporter_key", "window_geometry", "splitter_state", "docked_video_height",
    "mv_geometry", "popout_geometry", "recordings_dir", "local_dirs",
    "local_open_dir", "playlists", "active_playlist", "server", "username",
    "password", "parental_pin_hash", "parental_salt", "hwdec_mode", "volume",
    "live_rewind_space_gb", "cast_pause_gb", "recording_jobs", "cast_devices",
    "cast_last_device",
    "tmdb_api_key", "os_api_key", "os_username", "os_password",
    "feedback_tickets", "force_x11", "desktop_entry", "mpris",
    # The viewing statistics stay on the computer that counted them; the
    # recordings still to save as MP4 are this computer's files.
    "viewing_stats", "rec_mp4_pending",
    # Mine's queue order: Watch later itself does not sync (the cache file),
    # so neither does the order of it.
    "watch_later_order",
})
# Simkl's sign-in like Trakt's; a Kodi's login, and Discord's status, are
# this computer's.
SETTINGS_DENY_PREFIX = ("sync_", "trakt_", "simkl_", "kodi_", "discord_",
                        "nerd_", "selfupdate_", "update_",
                        "donate_", "feedback_", "parental_", "resume_positions",
                        "favorites", "movie_favorites", "series_favorites",
                        "history", "category_overrides", "channel_overrides",
                        "followed_series", "ts_broken/", "ts_depth/", "sort::")
_DENY_PARTS = ("geometry", "splitter", "_dir", "path", "token", "password",
               "secret", "private")


#: A household's profile in the settings category: "profile/<id>".
PROFILE_ENTRY = "profile/"


def syncable(key: str) -> bool:
    """Whether settings key *key*, as this computer keeps it, syncs."""
    key = str(key)
    if key.startswith(PROFILE_ENTRY):
        return len(key) > len(PROFILE_ENTRY)
    own = _profiles.split_key(key)
    if own is not None:
        # Another profile's own setting: as the first profile's would.
        return _profiles.is_own(own[1]) and syncable(own[1])
    if key in SETTINGS_DENY or key.startswith(SETTINGS_DENY_PREFIX):
        return False
    if any(part in key.lower() for part in _DENY_PARTS):
        return False
    return key in SETTINGS_ALLOW or key.startswith(SETTINGS_ALLOW_PREFIX)


# -- keyboard shortcuts, per system ----------------------------------------------

#: In a State's *seen*: this computer has synced its keyboard shortcuts. One
#: that synced before 1.6.1 never sent them, and its first round with them
#: is a newcomer's for them (newcomer()).
SEEN_SHORTCUTS = "settings:" + shortcut_keys.SETTING


def system_of(platform: str = sys.platform) -> str:
    """The operating system as an entry names it: macos, windows, linux, or
    another's own name (freebsd)."""
    if platform == "darwin":
        return "macos"
    if platform.startswith(("win32", "cygwin", "msys")):
        return "windows"
    if platform.startswith("linux"):
        return "linux"
    return platform.rstrip("0123456789") or "other"


def is_shortcuts(key: str) -> bool:
    """Whether settings key *key* (a profile's too) holds keyboard shortcuts."""
    own = _profiles.split_key(key)
    return (own[1] if own is not None else str(key)) == shortcut_keys.SETTING


def entry_of(key: str, platform: str = sys.platform) -> str:
    """The id a setting syncs under: its own name, the shortcuts' with this
    system's ("keyboard_shortcuts@macos")."""
    key = str(key)
    return f"{key}@{system_of(platform)}" if is_shortcuts(key) else key


def setting_of(ident: str, platform: str = sys.platform) -> str | None:
    """The setting a settings entry is here - or None for one this computer
    leaves as it is: what does not sync, another system's shortcuts, and
    shortcuts that name no system (no version sent them; which system they
    were for cannot be told, and applied on another they would be wrong)."""
    ident = str(ident)
    base, at, system = ident.rpartition("@")
    if at and is_shortcuts(base):
        return base if system == system_of(platform) and syncable(base) else None
    if is_shortcuts(ident):
        return None
    return ident if syncable(ident) else None


def newcomer(seen: Collection[str] | None, there: dict[str, Any],
             platform: str = sys.platform) -> set[str]:
    """This system's shortcut entries in *there* (the copy on the site, or
    this computer's own of it) while this computer has not synced its
    shortcuts yet (*seen*: State.seen; None counts as synced): its own then
    lose to what is there, as a joining computer's do, and none it lacks
    counts as removed. Without this the first round after the update
    overrode the other computers' keys with its own at the time now - or
    removed them, with no keys of its own."""
    if seen is None or SEEN_SHORTCUTS in seen:
        return set()
    entries = there.get("c", {}).get("settings") or {}
    return {ident for ident in entries
            if (key := setting_of(ident, platform)) is not None and is_shortcuts(key)}


def seen_after(cats: Collection[str], applied: Applied) -> set[str]:
    """What a round synced here, for State.seen: its categories but one
    changed meanwhile (the next round takes it), and with the settings this
    system's shortcuts."""
    done = set(cats) - applied.skipped
    return done | {SEEN_SHORTCUTS} if "settings" in done else done


def _scalar(value: Any) -> str | None:
    """A setting as the text the ini file keeps (what reads back after a
    restart), or None for what is not a plain value."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return f"{value:g}" if isinstance(value, float) else str(value)
    if isinstance(value, str):
        return value
    return None


# -- what this computer has ---------------------------------------------------------

_FAV_KINDS = (("chan", "favorites", "stream_id"),
              ("movie", "movie_favorites", "stream_id"),
              ("series", "series_favorites", "series_id"))
#: Favourites of no source - radio stations, web streams (YouTube, Twitch):
#: under GLOBAL_FP instead of a source's fingerprint, the key without a
#: source's id.
GLOBAL_FP = "*"
_GLOBAL_FAV_KINDS = (("radio", "radio_favorites", "stationuuid"),
                     ("web", "web_favorites", "url"))
#: History of these kinds stays here: a file or a recording of this computer.
_LOCAL_KINDS = ("local", "recording")
_RESUME_GROUPS = ("vod:", "episode:")


def tag(fp: str, profile: str = "") -> str:
    """A source's fingerprint (or GLOBAL_FP) for *profile*: as it is for
    the first profile, "<fp>@<profile>" for the others."""
    return f"{fp}@{profile}" if profile else fp


def source_fp(p: dict) -> str:
    """The same playlist on another computer, whatever its id there: its
    kind, address, user name and MAC."""
    ident = [str(p.get("kind") or "xtream"),
             str(p.get("server") or "").strip().rstrip("/").lower(),
             str(p.get("username") or "").strip(),
             str(p.get("mac") or "").strip().upper()]
    return hashlib.sha256(_canon(ident).encode("utf-8")).hexdigest()[:20]


def _eid(*parts: Any) -> str:
    return json.dumps([str(p) for p in parts], ensure_ascii=False)


#: The sources' entry that keeps the playlists' order (a list of their ids).
_ORDER = _eid("o")


def _parts(ident: str) -> list[str]:
    try:
        got = json.loads(ident)
    except ValueError:
        return []
    return [str(x) for x in got] if isinstance(got, list) else []


def _json(value: Any, empty: Any) -> Any:
    try:
        got = json.loads(value or "")
    except (TypeError, ValueError):
        return empty
    return got if isinstance(got, type(empty)) else empty


@dataclass
class Context:
    """Where this computer keeps what syncs: the shared settings, the resume
    positions' file, and the playlists (a PlaylistStore, or None). With
    profiles, *settings* and *resume_settings* are the first profile's
    (core/profiles.MAIN_ID); *others* the other profiles' (id, settings,
    resume settings); *registry* the household's profiles."""
    settings: Any
    resume_settings: Any = None
    playlists: Any = None
    others: list[tuple[str, Any, Any]] = field(default_factory=list)
    registry: Any = None
    #: (settings, resume settings) of a profile by its id: for one that a
    #: round brings (its own arrive in the same round).
    pin: Any = None
    #: This computer's system (sys.platform): whose keyboard shortcuts.
    platform: str = sys.platform
    #: What the copy has of a playlist's connection settings that waits for
    #: the person, by the playlist's id here (State.held: the state's own
    #: mapping, so what a round holds back is kept with it).
    held: dict[str, dict[str, Any]] = field(default_factory=dict)

    def refresh_others(self) -> None:
        """The other profiles as the registry has them now."""
        reg, pin = self.registry, self.pin
        if reg is None or pin is None:
            return
        self.others = [(p.id, *pin(p.id)) for p in reg.profiles()
                       if p.id != _profiles.MAIN_ID]

    def views(self) -> list[tuple[str, Any, Any]]:
        """(profile, settings, resume settings) of every profile; "" for
        the first."""
        return [("", self.settings, self.resume_settings)] + list(self.others)

    def places(self) -> dict[str, tuple[str, Any, Any]]:
        """Every source's fingerprint, and GLOBAL_FP, of every profile (as
        tag() makes them) -> (playlist id or GLOBAL_FP, settings, resume
        settings)."""
        out: dict[str, tuple[str, Any, Any]] = {}
        fps = self.fps()
        for label, st, rs in self.views():
            for fp, pid in fps.items():
                out[tag(fp, label)] = (pid, st, rs)
            out[tag(GLOBAL_FP, label)] = (GLOBAL_FP, st, rs)
        return out

    def profile_ids(self) -> set[str]:
        reg = self.registry
        return {p.id for p in reg.profiles()} if reg is not None else set()

    def sources(self) -> list[dict]:
        store = self.playlists
        return [p for p in (store.items if store is not None else [])
                if isinstance(p, dict) and p.get("id")]

    def fps(self) -> dict[str, str]:
        """Fingerprint -> playlist id, the first of two that are the same."""
        out: dict[str, str] = {}
        for p in self.sources():
            out.setdefault(source_fp(p), str(p["id"]))
        return out


@dataclass
class Snapshot:
    """What syncs, as this computer has it now: per category, entry id ->
    value. *fps*: this computer's playlists by fingerprint."""
    values: dict[str, dict[str, Any]] = field(default_factory=dict)
    fps: dict[str, str] = field(default_factory=dict)
    #: The profiles this computer has (their own settings are in domain).
    profiles: set[str] = field(default_factory=set)
    #: This computer's system (Context.platform).
    platform: str = sys.platform
    #: The sources (as tag() names them) whose history list is full here:
    #: a title older than the oldest one kept fell off its end, and was not
    #: removed (stamp()).
    capped: set[str] = field(default_factory=set)


def _collect_settings(ctx: Context) -> dict[str, Any]:
    out: dict[str, Any] = {}
    settings = ctx.settings
    for key in settings.allKeys():
        if syncable(key):
            value = _scalar(settings.value(key))
            if value is not None:
                out[entry_of(key, ctx.platform)] = value
    for label, view, _rs in ctx.others:
        for key in view.allKeys():
            if _profiles.is_own(key) and syncable(key):
                value = _scalar(view.value(key))
                if value is not None:
                    out[entry_of(f"{_profiles.ROOT}/{label}/{key}", ctx.platform)] = value
    reg = ctx.registry
    for p in (reg.profiles() if reg is not None else []):
        out[PROFILE_ENTRY + p.id] = _canon(
            {"name": p.name, "colour": p.colour, "emoji": p.emoji, "kids": p.kids})
    return out


def _collect_store(out: dict[str, Any], settings: Any, fp: str, kind: str,
                   key: str, id_key: str) -> None:
    store = FavoriteStore(settings, key, id_key)
    for group, items in store.groups.items():
        out[_eid(fp, kind, "g", group)] = 1
        order = []
        for item in items:
            ident = item.get(id_key)
            if ident is None or ident == "":
                continue
            out[_eid(fp, kind, "i", group, ident)] = item
            order.append(str(ident))
        if len(order) > 1:
            out[_eid(fp, kind, "o", group)] = order
    colours = _json(settings.value(f"{key}_colors"), {})
    for group, colour in colours.items():
        if isinstance(colour, dict) and any(colour.values()):
            out[_eid(fp, kind, "c", group)] = colour


def _collect_favorites(ctx: Context, fps: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for label, settings, _rs in ctx.views():
        for fp, pid in fps.items():
            for kind, prefix, id_key in _FAV_KINDS:
                _collect_store(out, settings, tag(fp, label), kind, f"{prefix}_{pid}", id_key)
        for kind, key, id_key in _GLOBAL_FAV_KINDS:
            _collect_store(out, settings, tag(GLOBAL_FP, label), kind, key, id_key)
    return out


def _collect_history(ctx: Context, fps: dict[str, str],
                     capped: set[str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for label, settings, resume in ctx.views():
        for fp, pid in fps.items():
            at = tag(fp, label)
            rows = _json(settings.value(f"history_{pid}"), [])
            if capped is not None and len(rows) >= MAX_HISTORY:
                capped.add(at)
            for e in rows:
                if not isinstance(e, dict) or e.get("_kind") in _LOCAL_KINDS:
                    continue
                if e.get("_key") in (None, "") or not e.get("_kind"):
                    continue
                out[_eid(at, "h", e["_kind"], e["_key"])] = e
            if resume is not None:
                data = _json(resume.value(f"resume_positions_{pid}"), {})
                for rkey, v in data.items():
                    if str(rkey).startswith(_RESUME_GROUPS) and isinstance(v, dict):
                        out[_eid(at, "r", rkey)] = v
    return out


#: This computer's playlists kept in the copy under an id that is not
#: their own here (playlist id -> the copy's id): two computers that each
#: had a playlist of another provider under the legacy id "default".
K_SOURCE_IDS = "sync_source_ids"


class SourceIds:
    """A playlist's id here and the id the copy keeps it under: the same,
    but for one whose id another computer's playlist had already taken
    (_keep_own_sources). Kept beside the playlists (K_SOURCE_IDS)."""

    def __init__(self, store: Any) -> None:
        self.settings = getattr(store, "settings", None)
        mine = {str(p.get("id")) for p in getattr(store, "items", None) or []
                if isinstance(p, dict) and p.get("id")}
        raw = _json(self.settings.value(K_SOURCE_IDS) if self.settings is not None
                    else None, {})
        self.to_entry: dict[str, str] = (
            {str(k): str(v) for k, v in raw.items() if str(k) in mine and v}
            if isinstance(raw, dict) else {})
        self.to_local = {v: k for k, v in self.to_entry.items()}

    def entry_of(self, pid: str) -> str:
        return self.to_entry.get(pid, pid)

    def local_of(self, entry: str) -> str | None:
        """The playlist id here for the copy's *entry*: None when that is
        the id of a playlist here that the copy keeps under another."""
        if entry in self.to_local:
            return self.to_local[entry]
        return None if entry in self.to_entry else entry

    def set(self, pid: str, entry: str) -> None:
        if entry == pid:
            self.to_entry.pop(pid, None)
        else:
            self.to_entry[pid] = entry
        self._changed()

    def forget(self, pid: str) -> None:
        if self.to_entry.pop(pid, None) is not None:
            self._changed()

    def _changed(self) -> None:
        self.to_local = {v: k for k, v in self.to_entry.items()}
        if self.settings is not None:
            self.settings.setValue(K_SOURCE_IDS, json.dumps(self.to_entry, sort_keys=True))


# -- a playlist's connection settings: never taken unasked ---------------------------

#: With one of RISKY_SOURCE, what is one setting with it: the address a
#: trusted certificate is for, how a catch-up address is used, the choice
#: that puts an own DNS server in use. They wait and are taken together, so
#: a playlist never has half a setting (a DNS choice without its server
#: fails every lookup).
_HELD_WITH: dict[str, tuple[str, ...]] = {
    _PIN: (_PIN_FOR,), "catchup_template": ("catchup_mode",), "dns_url": ("dns",)}
#: Per setting, by its RISKY_SOURCE name: the options whose difference holds
#: it back, and the options that wait with it.
_UNITS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = tuple(
    (key, (key, _PIN_FOR) if key == _PIN else (key,), (key, *_HELD_WITH.get(key, ())))
    for key in RISKY_SOURCE)
#: Every option that can wait.
HELD_KEYS = frozenset(k for _name, _why, keys in _UNITS for k in keys)
#: A companion's value that goes without saying beside its setting.
_IMPLIED = {"dns": "custom", "catchup_mode": "auto"}
#: In what waits for a playlist: the copy's has no options at all - not even
#: an empty set - so this computer's snapshot of it has none either.
_BARE = "~"
#: The most of one value that is shown (a copy can hold megabytes of it).
SHOWN_MAX = 2000
#: Rewrite rules longer than this are shown as they are stored, not read.
_RULES_MAX = 64 * 1024


def _options(p: Any) -> dict[str, Any]:
    """A playlist's options: a set of them, or none. Anything else under
    that name is none - a list of pairs reads as options where they are
    used (dict(options, ...)), and came past every check of a set."""
    o = p.get("options") if isinstance(p, dict) else None
    return o if isinstance(o, dict) else {}


def _plain(value: Any) -> str:
    """An option as it counts where it is used: its text without the space
    round it, "" for none (not there, empty, false)."""
    if not value:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return _canon(value)


def hold_back(theirs: dict, mine: dict | None = None) -> tuple[dict, dict[str, Any]]:
    """The copy's playlist *theirs* as this computer takes it by itself, and
    what of it waits for the person (State.held's for it; empty for
    nothing). Of the settings that send a playlist's connections elsewhere
    (_UNITS), each one the copy has otherwise than *mine* - this computer's
    playlist, None for one new here - stays as it is here, and the copy's
    options for it wait (None: the copy has none). Everything else is
    *theirs*, as ever. An ``options`` that is no set of them is left out."""
    raw = theirs.get("options")
    there, here = _options(theirs), _options(mine)
    waits: dict[str, Any] = {}
    for _name, why, keys in _UNITS:
        if any(_plain(there.get(k)) != _plain(here.get(k)) for k in why):
            waits.update((k, there.get(k)) for k in keys)
    if not waits and (raw is None or isinstance(raw, dict)):
        return theirs, waits
    opts = dict(there)
    for k in waits:
        if k in here:
            opts[k] = here[k]
        else:
            opts.pop(k, None)
    taken = dict(theirs)
    if opts or isinstance(raw, dict):
        taken["options"] = opts
    else:
        taken.pop("options", None)
    if waits and not isinstance(raw, dict):
        waits[_BARE] = 1
    return taken, waits


def _with_held(p: dict, waits: dict[str, Any] | None) -> dict:
    """Playlist *p* with the copy's values in the options that wait
    (*waits*) - the playlist as the copy has it. It is what goes into this
    computer's snapshot, so that its own values for them are no change to
    send while the person has not answered; and what take_held() stores."""
    if not waits:
        return p
    opts = dict(_options(p))
    for k, v in waits.items():
        if k not in HELD_KEYS:
            continue
        if v is None:
            opts.pop(k, None)
        else:
            opts[k] = v
    out = dict(p)
    if opts or not waits.get(_BARE):
        out["options"] = opts
    else:
        out.pop("options", None)
    return out


def _values(waits: dict[str, Any]) -> str:
    """The options that wait, as one text to compare."""
    return _canon({k: v for k, v in waits.items() if k in HELD_KEYS})


def _still_waits(p: Any, waits: dict[str, Any]) -> list[str]:
    """The settings (their RISKY_SOURCE names) of *waits* that playlist *p*
    still has otherwise than the copy."""
    here = _options(p)
    return [name for name, why, _keys in _UNITS
            if name in waits and any(_plain(waits.get(k)) != _plain(here.get(k)) for k in why)]


def _put(store: Any, p: dict, new: dict) -> None:
    """*new* in the place of playlist *p*. A new dict, not this one changed:
    the playlist in use is held by what plays now, which goes on as it was
    until the next start."""
    store.items[store.items.index(p)] = new
    store.update(str(new.get("id")))


def _hold(ctx: Context, pid: str, waits: dict[str, Any], out: Applied) -> None:
    """What the copy has for playlist *pid* that this computer did not take:
    kept in place of what waited for it before (a newer value from sync),
    or nothing left waiting. Only which settings is logged, never a value."""
    if not waits:
        ctx.held.pop(pid, None)
        return
    was = ctx.held.get(pid)
    ctx.held[pid] = waits
    if was is not None and _values(was) == _values(waits):
        return                      # the same ones as before: no news
    out.held.add(pid)
    log.info("sync: a playlist's connection settings were not taken from the copy - "
             "they wait for the person: %s",
             ", ".join(name for name, _why, _keys in _UNITS if name in waits))


def settle_held(store: Any, held: dict[str, dict[str, Any]]) -> bool:
    """What waits for nothing any more, forgotten: a playlist's that is gone
    from this computer, and values this computer now has itself. True when
    anything went."""
    went = False
    for pid in list(held):
        p = store.get(pid) if store is not None else None
        if not isinstance(p, dict) or not _still_waits(p, held[pid]):
            del held[pid]
            went = True
    return went


@dataclass
class HeldChange:
    """What the copy has of one playlist's connection settings that this
    computer has not taken. *rows*: per setting (its RISKY_SOURCE name) how
    it is here and how the copy has it, as text for the person ("" for
    none; never to be read as markup). *waits*: what it was made from, so an
    answer is about what was looked at."""
    pid: str
    name: str
    rows: list[tuple[str, str, str]] = field(default_factory=list)
    waits: dict[str, Any] = field(default_factory=dict)

    @property
    def settings(self) -> list[str]:
        return [name for name, _here, _there in self.rows]


def _cut(text: str, most: int = SHOWN_MAX) -> str:
    return text if len(text) <= most else text[:most - 1] + "…"


def _text(value: Any) -> str:
    return _plain(value) if not isinstance(value, str) else value.strip()


def _rules_text(value: Any) -> str:
    """Rewrite rules as lines, "find  →  replace" (a regular expression
    marked as its editor marks one); what does not read as rules as it is
    stored."""
    if isinstance(value, str) and len(value) > _RULES_MAX:
        return _text(value)
    from . import url_rewrite
    try:
        rules = url_rewrite.parse(value)
    except (RecursionError, ValueError, TypeError):
        rules = []
    if not rules:
        return _text(value)
    return "\n".join(f"{r.find}  →  {r.replace}" + ("  (.*)" if r.regex else "")
                     for r in rules)


def _shown(name: str, values: dict[str, Any]) -> str:
    """One setting's options (*values*) as text to read: "" for none, cut at
    SHOWN_MAX."""
    if name == "rewrite":
        return _cut(_rules_text(values.get(name)))
    if name == _PIN:
        from .cert_pin import normal, pretty
        fp = normal(_text(values.get(name)))
        return _cut(" · ".join(x for x in (_text(values.get(_PIN_FOR)),
                                           pretty(fp) if fp else _text(values.get(name))) if x))
    parts = [_text(values.get(name))]
    parts += [x for k in _HELD_WITH.get(name, ())
              if (x := _text(values.get(k))) and x != _IMPLIED.get(k)]
    return _cut(" · ".join(x for x in parts if x))


def held_changes(store: Any, held: dict[str, dict[str, Any]]) -> list[HeldChange]:
    """What waits (*held*), per playlist of *store* in their order, for the
    window to ask about: only the settings this computer still has
    otherwise than the copy."""
    out: list[HeldChange] = []
    for p in (getattr(store, "items", None) or []):
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id") or "")
        waits = held.get(pid)
        if not pid or not waits:
            continue
        here, still = _options(p), _still_waits(p, waits)
        rows = [(name, _shown(name, {k: here.get(k) for k in keys}),
                 _shown(name, {k: waits.get(k) for k in keys}))
                for name, _why, keys in _UNITS if name in still]
        if rows:
            name = p.get("name") if isinstance(p.get("name"), str) else ""
            server = p.get("server") if isinstance(p.get("server"), str) else ""
            out.append(HeldChange(pid, _cut(" ".join((name or server or "?").split()), 60),
                                  rows, dict(waits)))
    return out


def _looked_at(held: dict[str, dict[str, Any]], changes: list[HeldChange]) -> list[str]:
    """The playlists of *changes* whose waiting values are still the ones
    that were looked at (a round can bring newer ones meanwhile: those wait
    on, to be looked at first)."""
    return [c.pid for c in changes
            if c.pid in held and _values(held[c.pid]) == _values(c.waits)]


def take_held(store: Any, held: dict[str, dict[str, Any]],
              changes: list[HeldChange]) -> set[str]:
    """The person's "apply": what waits for the playlists of *changes* -
    exactly what was looked at - put in their options as the copy has it,
    stored the way a round stores a playlist, and no longer waiting.
    Returns the playlists (their ids) changed."""
    done: set[str] = set()
    for pid in _looked_at(held, changes):
        waits = held.pop(pid)
        p = store.get(pid) if store is not None else None
        if not isinstance(p, dict):
            continue
        _put(store, p, _with_held(p, waits))
        done.add(pid)
    if done:
        log.info("sync: connection settings from the copy taken for %d playlist(s), "
                 "as the person said", len(done))
    return done


def drop_held(held: dict[str, dict[str, Any]], changes: list[HeldChange]) -> set[str]:
    """The person's "keep mine": what waits for the playlists of *changes*
    forgotten. This computer's own values are then what its snapshot has -
    the newer change, sent with the next round. Returns the playlists."""
    done = set(_looked_at(held, changes))
    for pid in done:
        del held[pid]
    if done:
        log.info("sync: connection settings from the copy left for %d playlist(s) - "
                 "this computer's are kept", len(done))
    return done


def _collect_sources(ctx: Context) -> dict[str, Any]:
    out: dict[str, Any] = {}
    ids = SourceIds(ctx.playlists)
    srcs = ctx.sources()
    for p in srcs:
        pid = str(p["id"])
        e = ids.entry_of(pid)
        out[_eid("s", e)] = dict(_with_held(p, ctx.held.get(pid)), id=e)
    out[_eid("o")] = [ids.entry_of(str(p["id"])) for p in srcs]
    return out


def collect(ctx: Context, cats: tuple[str, ...] | list[str]) -> Snapshot:
    """What syncs of *cats*, read from the settings (the window's thread)."""
    fps = ctx.fps()
    snap = Snapshot(fps={k: v[0] for k, v in ctx.places().items()},
                    profiles=ctx.profile_ids(), platform=ctx.platform)
    if "settings" in cats:
        snap.values["settings"] = _collect_settings(ctx)
    if "favorites" in cats:
        snap.values["favorites"] = _collect_favorites(ctx, fps)
    if "history" in cats:
        snap.values["history"] = _collect_history(ctx, fps, snap.capped)
    if "sources" in cats and ctx.playlists is not None:
        snap.values["sources"] = _collect_sources(ctx)
    return snap


def digest(ctx: Context, cats: tuple[str, ...] | list[str]) -> str:
    """A cheap fingerprint of what *cats* would collect: the stored texts
    as they are, nothing parsed - asked every few seconds to see a change."""
    h = hashlib.sha1(",".join(sorted(cats)).encode())
    st = ctx.settings

    def take(store: Any, key: str) -> None:
        value = store.value(key)
        h.update(key.encode("utf-8", "replace") + b"\0"
                 + str(value).encode("utf-8", "replace") + b"\1")

    if "settings" in cats:
        for key in sorted(k for k in st.allKeys() if syncable(k)):
            take(st, key)
        for label, view, _rs in ctx.others:
            h.update(label.encode("utf-8", "replace") + b"\2")
            for key in sorted(k for k in view.allKeys()
                              if _profiles.is_own(k) and syncable(k)):
                take(view, key)
        if ctx.registry is not None:
            take(_profiles.raw(st), _profiles.K_LIST)
    pids = [str(p["id"]) for p in ctx.sources()]
    if "sources" in cats:
        take(st, "playlists")
        store = getattr(ctx.playlists, "settings", None)
        if store is not None:
            take(store, K_SOURCE_IDS)
        if ctx.held:
            # A snapshot has the copy's values where one waits (_with_held).
            h.update(_canon(ctx.held).encode("utf-8", "replace") + b"\4")
    for label, view, resume in ctx.views():
        h.update(label.encode("utf-8", "replace") + b"\3")
        if "favorites" in cats:
            for _kind, key, _id in _GLOBAL_FAV_KINDS:
                take(view, key)
                take(view, f"{key}_colors")
        for pid in pids:
            if "favorites" in cats:
                for _kind, prefix, _id in _FAV_KINDS:
                    take(view, f"{prefix}_{pid}")
                    take(view, f"{prefix}_{pid}_colors")
            if "history" in cats:
                take(view, f"history_{pid}")
                if resume is not None:
                    take(resume, f"resume_positions_{pid}")
    return h.hexdigest()


# -- times, merging -------------------------------------------------------------------

def _iso_time(text: Any) -> int:
    try:
        return int(datetime.fromisoformat(str(text)).timestamp())
    except (TypeError, ValueError, OverflowError, OSError):
        return 0


def _own_time(ident: str, value: Any) -> int:
    """A history entry's own time: when it was watched."""
    parts = _parts(ident)
    if len(parts) > 1 and parts[1] == "r":
        try:
            return int(value.get("ts") or 0)
        except (TypeError, ValueError, AttributeError):
            return 0
    return _iso_time(value.get("_watched_at") if isinstance(value, dict) else None)


def _in_domain(cat: str, ident: str, entry: dict, snap: Snapshot) -> bool:
    """Whether an entry not found here was removed here - rather than never
    being this computer's (another one's playlist and what belongs to it, a
    setting this version does not sync)."""
    if cat == "settings":
        if ident.startswith(PROFILE_ENTRY):
            return False        # a profile is never taken out by sync
        if setting_of(ident, snap.platform) is None:
            return False        # not synced here: another system's shortcuts too
        own = _profiles.split_key(ident)
        # Another profile's own: only one this computer has.
        return own is None or own[0] in snap.profiles
    parts = _parts(ident)
    if cat == "favorites" and parts[:1] == [GLOBAL_FP]:
        return True
    if cat in ("favorites", "history"):
        return bool(parts) and parts[0] in snap.fps
    if cat == "sources":
        if parts[:1] == ["s"] and isinstance(entry.get("v"), dict):
            # Another computer's copy of a playlist this one has under an
            # id of its own is not removed while that one is here.
            return source_fp(entry["v"]) not in snap.fps
        return bool(parts)
    return False


def _history_floors(local: dict[str, Any], snap: Snapshot) -> dict[str, int]:
    """Per source whose history list is full here (Snapshot.capped): when
    the oldest title it still keeps was watched."""
    floors: dict[str, int] = {}
    if not snap.capped:
        return floors
    for ident, value in local.items():
        parts = _parts(ident)
        if len(parts) > 1 and parts[1] == "h" and parts[0] in snap.capped:
            t = _own_time(ident, value)
            floors[parts[0]] = min(floors.get(parts[0], t), t)
    return floors


def _fell_off(ident: str, entry: dict, floors: dict[str, int]) -> bool:
    """Whether a history title not found here only fell off the end of a
    full list - which also holds this computer's own files and recordings,
    so it keeps fewer titles than the others. Removed is something else:
    as a tombstone it took the title from the computers with room for it."""
    if not floors:
        return False
    parts = _parts(ident)
    return len(parts) > 1 and parts[1] == "h" and parts[0] in floors \
        and int(entry["t"]) <= floors[parts[0]]


def _same_order(theirs: Any, mine: Any, entries: dict[str, dict],
                local: dict[str, Any]) -> bool:
    """Whether the playlists' order kept in the copy (*theirs*) is the one
    here (*mine*) - told by what each playlist is, not by its id: a
    playlist added by hand on two computers has an id of its own on each,
    and each then took the other's order for a change of its own, round
    after round. One this computer does not have is left out."""
    if not isinstance(theirs, list) or not isinstance(mine, list):
        return False
    fp_of: dict[str, str] = {}
    for source in ({k: e.get("v") for k, e in entries.items()}, local):
        for ident, value in source.items():
            parts = _parts(ident)
            if parts[:1] == ["s"] and len(parts) == 2 and isinstance(value, dict):
                fp_of[parts[1]] = source_fp(value)
    here = list(dict.fromkeys(fp_of.get(str(i), str(i)) for i in mine))
    there = list(dict.fromkeys(fp_of.get(str(i), str(i)) for i in theirs))
    return [fp for fp in there if fp in set(here)] == here


def stamp(doc: dict[str, Any], snap: Snapshot, cats: tuple[str, ...] | list[str],
          now: int, fresh: bool | set[str] | frozenset[str] = False,
          new: Collection[str] = ()) -> bool:
    """Bring this computer's copy *doc* up to *snap*: a value that is new or
    changed gets the time *now*; one that went gets a tombstone. History
    keeps the time it was watched. *fresh* (True, or the categories): this
    computer is new to them - joining, or a category just turned on that
    others sync already - so its own get the time 0 and lose where the
    others differ, and nothing it lacks counts as removed. *new*: entries
    it is new to in the same way (newcomer()). True when anything
    changed."""
    new_here = set(cats) if fresh is True else set(fresh or ())
    changed = False
    for cat in cats:
        if cat not in snap.values:
            continue
        local = snap.values[cat]
        entries = doc["c"].setdefault(cat, {})
        timed = cat == "history"
        fresh_cat = cat in new_here
        for ident, value in local.items():
            e = entries.get(ident)
            if e is not None and "v" in e and _canon(e["v"]) == _canon(value):
                continue
            if cat == "sources" and ident == _ORDER and e is not None and "v" in e \
                    and _same_order(e["v"], value, entries, local):
                continue
            if timed:
                t = _own_time(ident, value)
                if e is not None and int(e["t"]) > t:
                    continue        # removed or newer elsewhere: apply catches up
            elif fresh_cat or ident in new:
                t = 0
            else:
                t = max(now, int(e["t"]) + 1) if e is not None else now
            entries[ident] = {"t": t, "v": value}
            changed = True
        if fresh_cat:
            continue
        floors = _history_floors(local, snap) if timed else {}
        for ident, e in list(entries.items()):
            if "v" in e and ident not in local and ident not in new \
                    and _in_domain(cat, ident, e, snap) \
                    and not _fell_off(ident, e, floors):
                entries[ident] = {"t": max(now, int(e["t"]) + 1), "d": 1}
                changed = True
    return changed


def _wins(a: dict, b: dict) -> bool:
    """Whether entry *a* beats *b*: the later, and on a tie the same one on
    every computer."""
    if a["t"] != b["t"]:
        return a["t"] > b["t"]
    return _canon(a) > _canon(b)


def merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Two snapshots as one, entry by entry (the order does not matter)."""
    out = empty_doc()
    for cat in set(a["c"]) | set(b["c"]):
        mine = dict(a["c"].get(cat) or {})
        for ident, e in (b["c"].get(cat) or {}).items():
            cur = mine.get(ident)
            if cur is None or _wins(e, cur):
                mine[ident] = e
        out["c"][cat] = mine
    return out


def prune(doc: dict[str, Any], now: int) -> None:
    """Tombstones older than TOMB_DAYS go; per playlist, history keeps its
    newest MAX_HISTORY and resume points their newest MAX_RESUME (and a
    tombstone older than the oldest kept goes with the rest)."""
    cutoff = now - TOMB_DAYS * 86400
    for entries in doc["c"].values():
        for ident in [k for k, e in entries.items() if "d" in e and e["t"] < cutoff]:
            del entries[ident]
    hist = doc["c"].get("history")
    if not hist:
        return
    groups: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for ident, e in hist.items():
        parts = _parts(ident)
        if len(parts) >= 2:
            groups.setdefault((parts[0], parts[1]), []).append((e["t"], ident))
    for (_fp, sub), rows in groups.items():
        most = MAX_RESUME if sub == "r" else MAX_HISTORY
        live = sorted(((t, i) for t, i in rows if "v" in hist[i]), reverse=True)
        if len(live) < most:
            continue
        oldest = live[most - 1][0]
        for _t, ident in live[most:]:
            del hist[ident]
        for t, ident in rows:
            if ident in hist and "d" in hist[ident] and t < oldest:
                del hist[ident]


def _copy(doc: dict[str, Any]) -> dict[str, Any]:
    """A copy whose categories can change; entries are never changed in
    place, only replaced, so they are shared."""
    return {"v": FORMAT, "c": {cat: dict(m) for cat, m in doc["c"].items()}}


def live(doc: dict[str, Any], cat: str) -> dict[str, Any]:
    return {k: e["v"] for k, e in (doc["c"].get(cat) or {}).items() if "v" in e}


# -- the site ---------------------------------------------------------------------------

#: The header the slot's credential goes in (auth_of).
AUTH_HEADER = "X-Sync-Auth"


class Remote:
    """GET, PUT and DELETE of one slot on dopeiptv.org - with nothing about
    the person: the app's name and version, and the ciphertext. Blocking: a
    worker thread's."""

    def __init__(self, slot: str, endpoint: str | None = None,
                 timeout: float = 20.0, auth: str = "") -> None:
        self.url = (endpoint or ENDPOINT) + slot
        self.timeout = timeout
        from .. import __version__
        self.headers = {"User-Agent": f"dopeIPTV/{__version__}",
                        "Content-Type": "application/json", **build_check.headers()}
        if auth:
            self.headers[AUTH_HEADER] = auth

    @classmethod
    def of(cls, raw: bytes, endpoint: str | None = None, timeout: float = 20.0) -> Remote:
        """The site's side of the code *raw*: its slot, with its credential."""
        return cls(derive(raw)[0], endpoint, timeout, auth=auth_of(raw))

    def _call(self, method: str, body: dict | None = None) -> Any:
        from ._lazy_requests import requests
        if not build_check.allowed():
            # Never asked from a modified build: the site refuses it anyway.
            raise SyncError("modified")
        if not build_check.secure(self.url):
            log.warning("sync: not https - nothing sent")
            raise SyncError("net")
        try:
            # Never followed elsewhere: a copy goes to the slot named, or
            # nowhere.
            return requests.request(method, self.url, headers=self.headers,
                                    data=None if body is None
                                    else json.dumps(body).encode("ascii"),
                                    timeout=self.timeout, allow_redirects=False)
        except Exception as e:
            log.info("sync: %s not answered (%s)", method, type(e).__name__)
            raise SyncError("net") from e

    @staticmethod
    def _fail(status: int) -> SyncError:
        if status in (401, 403):
            return SyncError("refused")
        if status == 413:
            return SyncError("big")
        if status == 507:
            return SyncError("full")
        if status in (429, 502, 503, 504):
            return SyncError("busy")
        return SyncError("net")

    @staticmethod
    def _copy_of(r: Any) -> tuple[int, bytes] | None:
        try:
            got = r.json()
            version = int(got["version"])
            blob = base64.b64decode(str(got.get("blob") or ""), validate=True)
        except (ValueError, KeyError, TypeError) as e:
            raise SyncError("read") from e
        return (version, blob) if blob else None

    def get(self) -> tuple[int, bytes] | None:
        """(version, blob) kept there, or None for nothing yet."""
        r = self._call("GET")
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise self._fail(r.status_code)
        return self._copy_of(r)

    def put(self, version_seen: int, blob: bytes) -> tuple[bool, Any]:
        """(True, the new version) - or (False, (version, blob) | None) when
        the site has a newer copy than *version_seen*."""
        r = self._call("PUT", {"version_seen": version_seen,
                               "blob": base64.b64encode(blob).decode("ascii")})
        if r.status_code == 200:
            try:
                return True, int(r.json()["version"])
            except (ValueError, KeyError, TypeError) as e:
                raise SyncError("read") from e
        if r.status_code == 409:
            try:
                got = r.json()
            except ValueError:
                got = {}
            if not got.get("blob"):
                return False, None
            return False, self._copy_of(r)
        raise self._fail(r.status_code)

    def delete(self) -> bool:
        r = self._call("DELETE")
        return r.status_code in (200, 204, 404)


@dataclass
class RoundResult:
    merged: dict[str, Any]
    stamped: dict[str, Any]
    version: int
    uploaded: bool
    size: int
    #: The copy there was older than one this computer had seen (its
    #: counter lower): this computer's was kept and put back.
    older: bool = False


def round_trip(remote: Remote, key: bytes, doc: dict[str, Any], snap: Snapshot,
               cats: tuple[str, ...] | list[str], now: int | None = None,
               fresh: bool = False, joining: bool = False,
               seen: set[str] | frozenset[str] | None = None,
               bind: bool = False) -> RoundResult:
    """One sync, on a worker thread: the site's copy fetched, this
    computer's copy brought up to *snap* and merged in, and the result put
    back if it is any different - on a conflict (another computer was
    quicker) merged again with what it put there. *fresh*: joining; *seen*:
    the categories this computer has synced before (one it has not, that
    the site has already, starts fresh), and whether its shortcuts
    (newcomer()). *doc* itself is not changed. Each upload counts one more
    than the highest counter either side has (counter()); a copy whose
    counter is below the one *doc* has seen is older than what this
    computer had - the merge keeps this computer's newer entries anyway,
    and the result is put back and said (*older*). *bind*: this computer
    has not yet written the slot with its credential (auth_of) - put it
    back even unchanged, so the site binds the slot on this first round
    and from then on refuses anyone without the credential."""
    now = int(time.time()) if now is None else now
    highest = counter(doc)
    got = remote.get()
    if got is None and joining:
        raise SyncError("none")
    theirs = unseal(key, got[1]) if got else empty_doc()
    older = got is not None and counter(theirs) < highest
    if older:
        log.warning("sync: the copy there is older than one seen here (%d < %d) - "
                    "this computer's is put back", counter(theirs), highest)
    new_here: bool | set[str] = True if fresh else {
        c for c in cats if seen is not None and c not in seen and theirs["c"].get(c)}
    stamped = _copy(doc)
    stamp(stamped, snap, cats, now, new_here, newcomer(seen, theirs, snap.platform))
    for attempt in range(4):
        if attempt:
            # Another computer was quicker: its copy is the latest now.
            theirs = unseal(key, got[1]) if got else empty_doc()
            older = older or (got is not None and counter(theirs) < highest)
        version = got[0] if got else 0
        merged = merge(theirs, stamped)
        prune(merged, now)
        if got is not None and _canon(merged) == _canon(theirs) and not bind:
            return RoundResult(merged, stamped, version, False, len(got[1]), older)
        merged = _counted(merged, max(counter(merged), highest) + 1)
        blob = seal(key, merged)
        if len(blob) > MAX_BLOB:
            raise SyncError("big", len(blob))
        ok, info = remote.put(version, blob)
        if ok:
            return RoundResult(merged, stamped, int(info), True, len(blob), older)
        got = info
    raise SyncError("busy")


# -- putting it here ---------------------------------------------------------------------

@dataclass
class Applied:
    """What a round changed on this computer."""
    settings: set[str] = field(default_factory=set)
    favorites: set[str] = field(default_factory=set)     # playlist ids, GLOBAL_FP
    history: set[str] = field(default_factory=set)       # playlist ids
    sources: bool = False
    skipped: set[str] = field(default_factory=set)       # changed meanwhile
    #: Something of this computer's own the copy does not have yet (a
    #: playlist that kept its own after its id was taken): the next round
    #: soon, not in half an hour.
    again: bool = False
    #: Playlists (their ids here) whose connection settings the copy has
    #: otherwise and this round did not take: new, or other than the ones
    #: that waited (State.held has them; the window asks).
    held: set[str] = field(default_factory=set)

    def any(self) -> bool:
        return bool(self.settings or self.favorites or self.history or self.sources)


def _apply_profiles(ctx: Context, new: dict[str, Any], old: dict[str, Any],
                    out: Applied) -> None:
    """The household's profiles another computer has, merged in (added or
    renamed, never taken out; the PIN stays this computer's)."""
    reg = ctx.registry
    if reg is None:
        return
    rows = []
    for key, value in new.items():
        if not key.startswith(PROFILE_ENTRY) or value == old.get(key):
            continue
        got = _json(value, {})
        if got:
            rows.append(dict(got, id=key[len(PROFILE_ENTRY):]))
    if rows and reg.merge_public(rows):
        out.settings.add(_profiles.K_LIST)


def _apply_settings(settings: Any, new: dict[str, Any], old: dict[str, Any],
                    out: Applied, ctx: Context | None = None) -> None:
    if ctx is not None:
        _apply_profiles(ctx, new, old, out)
    here = ctx.profile_ids() if ctx is not None else set()
    platform = ctx.platform if ctx is not None else sys.platform
    for ident in set(new) | set(old):
        if ident.startswith(PROFILE_ENTRY):
            continue
        key = setting_of(ident, platform)
        if key is None:
            continue            # not synced here, or another system's shortcuts
        own = _profiles.split_key(key)
        if own is not None and own[0] not in here:
            continue            # a profile this computer does not have
        value = new.get(ident)
        if value == old.get(ident):
            continue
        if value is None:
            settings.remove(key)
        else:
            settings.setValue(key, str(value))
        out.settings.add(key)


def _by_bucket(values: dict[str, Any], depth: int) -> dict[tuple[str, ...], dict[str, Any]]:
    out: dict[tuple[str, ...], dict[str, Any]] = {}
    for ident, v in values.items():
        parts = _parts(ident)
        if len(parts) > depth:
            out.setdefault(tuple(parts[:depth]), {})[ident] = v
    return out


def _row_key(place: dict[str, int], row: tuple[int, str, dict]) -> tuple[int, int, str]:
    """A favorite's place in its folder: where the folder's order puts it,
    else after those, the longest there first."""
    return place.get(row[1], len(place)), row[0], row[1]


def _apply_favorites(ctx: Context, entries: dict[str, dict], new: dict[str, Any],
                     old: dict[str, Any], places: dict[str, tuple[str, Any, Any]],
                     out: Applied) -> None:
    new_b, old_b = _by_bucket(new, 2), _by_bucket(old, 2)
    kinds = {k: (prefix, id_key) for k, prefix, id_key in _FAV_KINDS}
    global_kinds = {k: key for k, key, _id in _GLOBAL_FAV_KINDS}
    for bucket in set(new_b) | set(old_b):
        fp, kind = bucket
        where = places.get(fp)
        if where is None:
            continue            # another computer's source, or profile
        found, settings, _rs = where
        if found == GLOBAL_FP:
            if kind not in global_kinds:
                continue
            pid, key = GLOBAL_FP, global_kinds[kind]
        else:
            if kind not in kinds:
                continue
            pid, key = found, f"{kinds[kind][0]}_{found}"
        mine = new_b.get(bucket, {})
        if _canon(mine) == _canon(old_b.get(bucket, {})):
            continue
        items: dict[str, list[tuple[int, str, dict]]] = {}
        orders: dict[str, list[str]] = {}
        colours: dict[str, dict] = {}
        for ident, value in mine.items():
            parts = _parts(ident)
            if len(parts) < 4:
                continue
            what, group = parts[2], parts[3]
            if what == "g":
                items.setdefault(group, [])
            elif what == "i" and len(parts) >= 5 and isinstance(value, dict):
                items.setdefault(group, []).append(
                    (int(entries.get(ident, {}).get("t", 0)), parts[4], value))
            elif what == "o" and isinstance(value, list):
                orders[group] = [str(x) for x in value]
            elif what == "c" and isinstance(value, dict):
                colours[group] = value
        groups: dict[str, list[dict]] = {}
        for group, rows in items.items():
            place = {ident: i for i, ident in enumerate(orders.get(group, []))}
            rows.sort(key=functools.partial(_row_key, place))
            groups[group] = [r[2] for r in rows]
        settings.setValue(key, json.dumps(groups))
        if colours:
            settings.setValue(f"{key}_colors", json.dumps(colours))
        else:
            settings.remove(f"{key}_colors")
        out.favorites.add(pid)


def _apply_history(ctx: Context, new: dict[str, Any], old: dict[str, Any],
                   places: dict[str, tuple[str, Any, Any]], out: Applied) -> None:
    new_b, old_b = _by_bucket(new, 2), _by_bucket(old, 2)
    for bucket in set(new_b) | set(old_b):
        fp, sub = bucket
        place = places.get(fp)
        mine = new_b.get(bucket, {})
        if place is None or place[0] == GLOBAL_FP \
                or _canon(mine) == _canon(old_b.get(bucket, {})):
            continue
        pid, settings, resume = place
        if sub == "h":
            here = [e for e in _json(settings.value(f"history_{pid}"), [])
                    if isinstance(e, dict) and e.get("_kind") in _LOCAL_KINDS]
            rows = here + [v for v in mine.values() if isinstance(v, dict)]
            rows.sort(key=lambda e: str(e.get("_watched_at") or ""), reverse=True)
            rows = rows[:MAX_HISTORY]
            if rows == _json(settings.value(f"history_{pid}"), []):
                # A full list keeps fewer titles than the others have: the
                # same ones again each round, and nothing changed here.
                continue
            settings.setValue(f"history_{pid}", json.dumps(rows))
        elif sub == "r" and resume is not None:
            key = f"resume_positions_{pid}"
            data = {k: v for k, v in _json(resume.value(key), {}).items()
                    if not str(k).startswith(_RESUME_GROUPS)}
            for ident, v in mine.items():
                parts = _parts(ident)
                if len(parts) >= 3 and isinstance(v, dict):
                    data[parts[2]] = v
            resume.setValue(key, json.dumps(data))
        else:
            continue
        out.history.add(pid)


def _keep_own_sources(ctx: Context, ids: SourceIds, wanted: dict[str, dict],
                      entries: dict[str, dict], own: dict[str, dict], out: Applied) -> None:
    """Two computers that each kept a playlist of another provider under the
    legacy id "default" (made from the settings of a version before
    playlists): joining, this one's own lost to the other's under the same
    id - its playlist became the other provider, its favourites with it.
    Told by this computer's own entry: new to the copy (the time 0) and
    beaten by another computer's of another playlist (not one of its own
    changed: the same playlist on both is the same address and login). This
    one keeps its own then, under another id in the copy - the other
    computer's id for it if the copy has one, else a new one - and the
    other's comes in beside it."""
    srcs = ctx.sources()
    used = {ids.entry_of(str(p["id"])) for p in srcs}
    for p in srcs:
        pid = str(p["id"])
        e = ids.entry_of(pid)
        mine, won, v = own.get(_eid("s", e)), entries.get(_eid("s", e)), wanted.get(e)
        if (v is None or not isinstance(mine, dict) or "v" not in mine
                or int(mine.get("t", 1)) != 0 or not isinstance(won, dict)
                or int(won.get("t", 0)) == 0):
            continue
        fp = source_fp(p)
        if source_fp(v) == fp:
            continue
        new = next((k for k, w in wanted.items()
                    if k not in used and source_fp(w) == fp), None)
        while new is None or new in used or (new in wanted and source_fp(wanted[new]) != fp):
            new = secrets.token_hex(4)
        log.info("sync: another computer's playlist had this one's id - each keeps its own")
        ids.set(pid, new)
        used.discard(e)
        used.add(new)
        out.again = True


def _apply_sources(ctx: Context, new: dict[str, Any], old: dict[str, Any],
                   out: Applied, entries: dict[str, dict] | None = None,
                   own: dict[str, dict] | None = None) -> None:
    """The copy's playlists here. Their ids in *new*, *old* and *entries*
    are the copy's (SourceIds); *own*: this computer's entries as it
    stamped them this round."""
    store = ctx.playlists
    if store is None or _canon(new) == _canon(old):
        return
    ids = SourceIds(store)
    wanted: dict[str, dict] = {}
    for ident, v in new.items():
        parts = _parts(ident)
        if parts[:1] == ["s"] and len(parts) == 2 and isinstance(v, dict):
            wanted[parts[1]] = v
    if own:
        _keep_own_sources(ctx, ids, wanted, entries or {}, own, out)
    here = {ids.entry_of(str(p["id"])): p for p in ctx.sources()}
    fps_here = {source_fp(p) for p in here.values()}

    def when(e: str) -> int:
        return int((entries or {}).get(_eid("s", e), {}).get("t", 0))

    # The same playlist kept under an id of its own on another computer
    # (added by hand on both): what was changed there last - its password,
    # its name, its options - comes here too. Without this only a playlist
    # with the same id on both followed a change.
    twins: dict[str, str] = {}
    for e, v in wanted.items():
        if e not in here:
            fp = source_fp(v)
            if fp not in twins or when(e) > when(twins[fp]):
                twins[fp] = e
    for e, p in here.items():
        pid = str(p["id"])
        v = wanted.get(e)
        twin = twins.get(source_fp(p))
        if v is not None and twin is not None and when(twin) > when(e):
            v = wanted[twin]
        if v is None:
            if _eid("s", e) not in old:
                continue
            ctx.held.pop(pid, None)         # the copy has nothing of it to wait for
            if pid == str(store.active_id):
                log.info("sync: playlist removed elsewhere kept - it is in use here")
                continue
            store.remove(pid)
            ids.forget(pid)
            out.sources = True
            continue
        # Its connection settings stay this computer's own (hold_back).
        taken, waits = hold_back(v, p)
        _hold(ctx, pid, waits, out)
        if _canon(dict(taken, id=pid)) != _canon(p):
            _put(store, p, dict(taken, id=pid))
            out.sources = True
    for e, v in wanted.items():
        if e in here or source_fp(v) in fps_here:
            continue                # here already, under an id of this computer's
        pid = e
        if store.get(pid) is not None or ids.local_of(e) is None:
            while store.get(pid) is not None or pid in ids.to_entry or pid in wanted:
                pid = secrets.token_hex(4)
        # New here: without the connection settings the copy has for it.
        taken, waits = hold_back(v)
        store.add(dict(taken, id=pid))
        _hold(ctx, pid, waits, out)
        if pid != e:
            ids.set(pid, e)
        fps_here.add(source_fp(v))
        out.sources = True
    order = new.get(_eid("o"))
    if isinstance(order, list) and order != old.get(_eid("o")):
        current = [str(p["id"]) for p in ctx.sources()]
        # By what each playlist is: one kept here under another id takes
        # its place in the order all the same.
        local_of: dict[str, str] = {}
        for p in ctx.sources():
            local_of.setdefault(source_fp(p), str(p["id"]))
        pids = []
        for x in order:
            i = str(x)
            mine_id = ids.local_of(i)
            if mine_id is not None and mine_id in current:
                pids.append(mine_id)
            elif i in wanted:
                pids.append(local_of.get(source_fp(wanted[i]), i))
            else:
                pids.append(i)
        store.reorder(pids)
        if [str(p["id"]) for p in ctx.sources()] != current:
            out.sources = True


def apply(ctx: Context, result: RoundResult, before: Snapshot, now_here: Snapshot,
          cats: tuple[str, ...] | list[str]) -> tuple[Applied, dict[str, Any]]:
    """Put a round's merged snapshot here (the window's thread), and say what
    changed. *before* is what the round was given; *now_here* what is here
    as it comes back - a category changed meanwhile is left alone this time
    (the next round takes it). Returns this computer's copy to keep."""
    out = Applied()
    keep = _copy(result.merged)
    for cat in cats:
        if cat not in before.values:
            continue
        if _canon(before.values[cat]) != _canon(now_here.values.get(cat, {})):
            out.skipped.add(cat)
            keep["c"][cat] = dict(result.stamped["c"].get(cat) or {})
    order = [c for c in ("sources", "settings", "favorites", "history")
             if c in cats and c in before.values and c not in out.skipped]
    for cat in order:
        new = live(result.merged, cat)
        old = before.values[cat]
        if cat == "settings":
            _apply_settings(ctx.settings, new, old, out, ctx)
            ctx.refresh_others()            # a profile that came with it
        elif cat == "sources":
            _apply_sources(ctx, new, old, out, result.merged["c"].get(cat) or {},
                           result.stamped["c"].get(cat) or {})
        elif cat == "favorites":
            _apply_favorites(ctx, result.merged["c"].get(cat) or {}, new, old,
                             ctx.places(), out)
        elif cat == "history":
            _apply_history(ctx, new, old, ctx.places(), out)
    if ctx.held and ctx.playlists is not None:
        settle_held(ctx.playlists, ctx.held)
    return out, keep


# -- this computer's copy --------------------------------------------------------------------

#: In the state file after a restore (rejoin()): the next round, of any
#: slot, is this computer's first.
_REJOIN = "rejoin"


class State:
    """This computer's copy of the last snapshot, beside the settings (a
    JSON file of its own: it can be large, and is never in the shared
    settings). *fresh*: joined, not yet synced; *joining*: the slot must
    already exist."""

    _lock = threading.Lock()

    def __init__(self, path: Path, slot: str) -> None:
        self.path = path
        self.slot = slot
        self.version = 0
        self.doc = empty_doc()
        self.fresh = False
        self.joining = False
        self.seen: set[str] = set()
        #: This computer has written the slot with its credential, so the
        #: site holds the slot bound to it (round_trip's *bind*).
        self.bound = False
        #: Per playlist (its id here): the connection settings the copy has
        #: for it that this computer has not taken - its options as the copy
        #: has them, None for one it has not (hold_back). They wait for the
        #: person; nothing uses them meanwhile.
        self.held: dict[str, dict[str, Any]] = {}

    @classmethod
    def load(cls, path: Path, slot: str) -> State:
        st = cls(path, slot)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return st
        if not isinstance(data, dict):
            return st
        if data.get("slot") != slot:
            st.fresh = bool(data.get(_REJOIN))     # a restore brought this slot
            return st
        doc = data.get("doc")
        if isinstance(doc, dict) and isinstance(doc.get("c"), dict):
            st.doc = {"v": FORMAT, "c": {str(c): dict(m) for c, m in doc["c"].items()
                                         if isinstance(m, dict)}}
        st.version = int(data.get("version") or 0)
        st.fresh = bool(data.get("fresh"))
        st.joining = bool(data.get("joining"))
        st.bound = bool(data.get("bound"))
        held = data.get("held")
        for pid, waits in (held.items() if isinstance(held, dict) else ()):
            kept = ({k: v for k, v in waits.items() if k in HELD_KEYS or k == _BARE}
                    if isinstance(waits, dict) else {})
            if kept:
                st.held[str(pid)] = kept
        seen = data.get("seen")
        st.seen = {str(c) for c in seen} if isinstance(seen, list) else set()
        return st

    def dump(self) -> str:
        return json.dumps({"slot": self.slot, "version": self.version,
                           "fresh": self.fresh, "joining": self.joining,
                           "bound": self.bound, "held": self.held,
                           "seen": sorted(self.seen), "doc": self.doc},
                          ensure_ascii=False, separators=(",", ":"))


def write_private(path: Path, text: str) -> None:
    """*text* into *path* whole or not at all, readable by this user only
    (it can hold playlists' logins)."""
    with State._lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def rejoin(path: Path) -> bool:
    """After a backup was put in place over everything: this computer's copy
    of the last snapshot forgotten and its next round marked fresh, so it
    joins its slot again as a computer new to it - the others' newer values
    win where they differ and nothing the backup lacks is removed from them.
    Whatever slot the restored settings name (a backup can bring another
    sync code, or one to a computer that never synced). True when sync had
    been used here."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    used = isinstance(data, dict) and bool(data.get("slot"))
    seen = data.get("seen") if used and isinstance(data, dict) else None
    write_private(path, json.dumps(
        {"slot": str(data["slot"]) if used and isinstance(data, dict) else "",
         "version": 0, "fresh": True, "joining": False, _REJOIN: True,
         "seen": sorted(str(c) for c in seen) if isinstance(seen, list) else [],
         "doc": empty_doc()}, separators=(",", ":")))
    return used


def forget(path: Path) -> None:
    with State._lock:
        try:
            path.unlink()
        except OSError:
            pass


def chosen(settings: Any) -> tuple[str, ...]:
    """The categories this computer syncs."""
    raw = settings.value(K_CATS)
    if raw is None:
        return DEFAULT_CATS
    text = ",".join(raw) if isinstance(raw, list) else str(raw)
    return tuple(c for c in CATEGORIES if c in text.split(","))


def set_chosen(settings: Any, cats: tuple[str, ...] | list[str]) -> None:
    settings.setValue(K_CATS, ",".join(c for c in CATEGORIES if c in cats) or "none")


def code_of(settings: Any) -> bytes | None:
    return parse_code(str(settings.value(K_CODE, "") or ""))
