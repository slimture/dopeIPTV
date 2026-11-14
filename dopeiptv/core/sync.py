"""Settings sync for supporters: favorites, settings, playlists and watch
history kept the same on several computers through dopeiptv.org, end-to-end
encrypted.

Privacy. The supporter key is signed by dopeiptv.org, so the site knows
everything derived from it alone. The secret here is something else: a
random 256-bit *sync code*, made on the first computer and typed or pasted
on the others. From it come (HKDF-SHA256) the *slot* - the name the site
keeps the data under - and the key it is encrypted with (ChaCha20-Poly1305,
core/chacha20poly1305.py). The site sees the slot, the size and when; it
never sees the code or the key, and cannot read a byte of what is kept.
Each request carries the supporter key so the site can check, offline,
that a supporter asks; it keeps neither the key nor a link from it to the
slot (donate/sync.py on the website branch). Nothing else about the person
goes along.

What syncs (one switch each): favorites with their folders, settings from
an explicit allow-list (never window sizes, folders and paths of this
computer, the supporter key, the sync code, logins of other services),
playlists with their logins (off by default), and watch history with resume
points (off by default).

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
import threading
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import chacha20poly1305 as aead
from .log import log
from .stores import FavoriteStore, HistoryStore

ENDPOINT = "https://dopeiptv.org/sync/"
HEADER = "X-Dopeiptv-Supporter"
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
    is busy, or rate-limited), key (the supporter key was not taken), big
    (too much to sync), none (nothing under this code), read (the copy
    there could not be read with this code), newer (a newer app made it)."""

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


# -- a snapshot, sealed -----------------------------------------------------------

def empty_doc() -> dict[str, Any]:
    return {"v": FORMAT, "c": {}}


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
    "corner_style", "ui_scale", "view_density", "view_grid", "sort_order",
    "home_enabled", "home_start", "follow_notify",
    "stream_format", "cache_secs", "autoplay_preview", "autoplay_next_episode",
    "nextup_lead_secs", "auto_reconnect_live", "hide_upcoming_prompt",
    "live_rewind_minutes", "live_rewind_disk", "live_rewind_kept",
    "audio_lang", "sub_mode", "sub_lang", "sub_lang2", "sub_scale", "sub_color",
    "sub_background", "aspect_mode", "video_deinterlace", "video_sharpen",
    "video_tonemapping",
    "epg_upcoming_count", "epg_delay_min", "replay_delay_min",
    "mv_frameless", "mv_on_top", "mv_remember_geo", "mv_stop_docked",
    "mv_new_unmuted", "mv_conflict_mode", "mv_autohide_secs", "mv_seek_step_min",
    "mv_cells",
    "metadata_source", "music_art_online", "check_updates",
})
#: Families of settings that sync: Home's shelves, the sidebar's colours,
#: keyboard shortcuts (stored per operating system, so they never cross).
SETTINGS_ALLOW_PREFIX = ("home_sh_", "nav_color_", "nav_bg_", "shortcut/")
#: Never, whatever the lists above say: what belongs to this computer, a
#: secret, or the sync itself.
SETTINGS_DENY = frozenset({
    "supporter_key", "window_geometry", "splitter_state", "docked_video_height",
    "mv_geometry", "popout_geometry", "recordings_dir", "local_dirs",
    "local_open_dir", "playlists", "active_playlist", "server", "username",
    "password", "parental_pin_hash", "parental_salt", "hwdec_mode", "volume",
    "live_rewind_space_gb", "cast_pause_gb", "recording_jobs", "cast_devices",
    "tmdb_api_key", "os_api_key", "os_username", "os_password",
    "feedback_tickets", "force_x11", "desktop_entry", "mpris",
})
SETTINGS_DENY_PREFIX = ("sync_", "trakt_", "nerd_", "selfupdate_", "update_",
                        "donate_", "feedback_", "parental_", "resume_positions",
                        "favorites", "movie_favorites", "series_favorites",
                        "history", "category_overrides", "channel_overrides",
                        "followed_series", "ts_broken/", "ts_depth/", "sort::")
_DENY_PARTS = ("geometry", "splitter", "_dir", "path", "token", "password",
               "secret", "private")


def syncable(key: str) -> bool:
    key = str(key)
    if key in SETTINGS_DENY or key.startswith(SETTINGS_DENY_PREFIX):
        return False
    if any(part in key.lower() for part in _DENY_PARTS):
        return False
    return key in SETTINGS_ALLOW or key.startswith(SETTINGS_ALLOW_PREFIX)


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
#: History of these kinds stays here: a file or a recording of this computer.
_LOCAL_KINDS = ("local", "recording")
_RESUME_GROUPS = ("vod:", "episode:")


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
    positions' file, and the playlists (a PlaylistStore, or None)."""
    settings: Any
    resume_settings: Any = None
    playlists: Any = None

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


def _collect_settings(settings: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in settings.allKeys():
        if syncable(key):
            value = _scalar(settings.value(key))
            if value is not None:
                out[str(key)] = value
    return out


def _collect_favorites(settings: Any, fps: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for fp, pid in fps.items():
        for kind, prefix, id_key in _FAV_KINDS:
            store = FavoriteStore(settings, f"{prefix}_{pid}", id_key)
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
            colours = _json(settings.value(f"{prefix}_{pid}_colors"), {})
            for group, colour in colours.items():
                if isinstance(colour, dict) and any(colour.values()):
                    out[_eid(fp, kind, "c", group)] = colour
    return out


def _collect_history(ctx: Context, fps: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for fp, pid in fps.items():
        rows = _json(ctx.settings.value(f"history_{pid}"), [])
        for e in rows:
            if not isinstance(e, dict) or e.get("_kind") in _LOCAL_KINDS:
                continue
            if e.get("_key") in (None, "") or not e.get("_kind"):
                continue
            out[_eid(fp, "h", e["_kind"], e["_key"])] = e
        if ctx.resume_settings is not None:
            data = _json(ctx.resume_settings.value(f"resume_positions_{pid}"), {})
            for rkey, v in data.items():
                if str(rkey).startswith(_RESUME_GROUPS) and isinstance(v, dict):
                    out[_eid(fp, "r", rkey)] = v
    return out


def _collect_sources(ctx: Context) -> dict[str, Any]:
    out: dict[str, Any] = {}
    srcs = ctx.sources()
    for p in srcs:
        out[_eid("s", p["id"])] = dict(p)
    out[_eid("o")] = [str(p["id"]) for p in srcs]
    return out


def collect(ctx: Context, cats: tuple[str, ...] | list[str]) -> Snapshot:
    """What syncs of *cats*, read from the settings (the window's thread)."""
    fps = ctx.fps()
    snap = Snapshot(fps=fps)
    if "settings" in cats:
        snap.values["settings"] = _collect_settings(ctx.settings)
    if "favorites" in cats:
        snap.values["favorites"] = _collect_favorites(ctx.settings, fps)
    if "history" in cats:
        snap.values["history"] = _collect_history(ctx, fps)
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
    pids = [str(p["id"]) for p in ctx.sources()]
    if "sources" in cats:
        take(st, "playlists")
    for pid in pids:
        if "favorites" in cats:
            for _kind, prefix, _id in _FAV_KINDS:
                take(st, f"{prefix}_{pid}")
                take(st, f"{prefix}_{pid}_colors")
        if "history" in cats:
            take(st, f"history_{pid}")
            if ctx.resume_settings is not None:
                take(ctx.resume_settings, f"resume_positions_{pid}")
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
        return syncable(ident)
    parts = _parts(ident)
    if cat in ("favorites", "history"):
        return bool(parts) and parts[0] in snap.fps
    if cat == "sources":
        if parts[:1] == ["s"] and isinstance(entry.get("v"), dict):
            # Another computer's copy of a playlist this one has under an
            # id of its own is not removed while that one is here.
            return source_fp(entry["v"]) not in snap.fps
        return bool(parts)
    return False


def stamp(doc: dict[str, Any], snap: Snapshot, cats: tuple[str, ...] | list[str],
          now: int, fresh: bool | set[str] | frozenset[str] = False) -> bool:
    """Bring this computer's copy *doc* up to *snap*: a value that is new or
    changed gets the time *now*; one that went gets a tombstone. History
    keeps the time it was watched. *fresh* (True, or the categories): this
    computer is new to them - joining, or a category just turned on that
    others sync already - so its own get the time 0 and lose where the
    others differ, and nothing it lacks counts as removed. True when
    anything changed."""
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
            if timed:
                t = _own_time(ident, value)
                if e is not None and int(e["t"]) > t:
                    continue        # removed or newer elsewhere: apply catches up
            elif fresh_cat:
                t = 0
            else:
                t = max(now, int(e["t"]) + 1) if e is not None else now
            entries[ident] = {"t": t, "v": value}
            changed = True
        if fresh_cat:
            continue
        for ident, e in list(entries.items()):
            if "v" in e and ident not in local and _in_domain(cat, ident, e, snap):
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

class Remote:
    """GET, PUT and DELETE of one slot on dopeiptv.org, the supporter key in
    a header. Blocking: a worker thread's."""

    def __init__(self, slot: str, supporter_key: str, endpoint: str | None = None,
                 timeout: float = 20.0) -> None:
        self.url = (endpoint or ENDPOINT) + slot
        self.timeout = timeout
        from .. import __version__
        self.headers = {HEADER: supporter_key, "User-Agent": f"dopeIPTV/{__version__}",
                        "Content-Type": "application/json"}

    def _call(self, method: str, body: dict | None = None) -> Any:
        from ._lazy_requests import requests
        try:
            return requests.request(method, self.url, headers=self.headers,
                                    data=None if body is None
                                    else json.dumps(body).encode("ascii"),
                                    timeout=self.timeout)
        except Exception as e:
            log.info("sync: %s not answered (%s)", method, type(e).__name__)
            raise SyncError("net") from e

    @staticmethod
    def _fail(status: int) -> SyncError:
        if status in (401, 403):
            return SyncError("key")
        if status == 413:
            return SyncError("big")
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


def round_trip(remote: Remote, key: bytes, doc: dict[str, Any], snap: Snapshot,
               cats: tuple[str, ...] | list[str], now: int | None = None,
               fresh: bool = False, joining: bool = False,
               seen: set[str] | frozenset[str] | None = None) -> RoundResult:
    """One sync, on a worker thread: the site's copy fetched, this
    computer's copy brought up to *snap* and merged in, and the result put
    back if it is any different - on a conflict (another computer was
    quicker) merged again with what it put there. *fresh*: joining; *seen*:
    the categories this computer has synced before (one it has not, that
    the site has already, starts fresh). *doc* itself is not changed."""
    now = int(time.time()) if now is None else now
    got = remote.get()
    if got is None and joining:
        raise SyncError("none")
    theirs = unseal(key, got[1]) if got else empty_doc()
    new_here: bool | set[str] = True if fresh else {
        c for c in cats if seen is not None and c not in seen and theirs["c"].get(c)}
    stamped = _copy(doc)
    stamp(stamped, snap, cats, now, new_here)
    for attempt in range(4):
        if attempt:
            theirs = unseal(key, got[1]) if got else empty_doc()
        version = got[0] if got else 0
        merged = merge(theirs, stamped)
        prune(merged, now)
        if got is not None and _canon(merged) == _canon(theirs):
            return RoundResult(merged, stamped, version, False, len(got[1]))
        blob = seal(key, merged)
        if len(blob) > MAX_BLOB:
            raise SyncError("big", len(blob))
        ok, info = remote.put(version, blob)
        if ok:
            return RoundResult(merged, stamped, int(info), True, len(blob))
        got = info
    raise SyncError("busy")


# -- putting it here ---------------------------------------------------------------------

@dataclass
class Applied:
    """What a round changed on this computer."""
    settings: set[str] = field(default_factory=set)
    favorites: set[str] = field(default_factory=set)     # playlist ids
    history: set[str] = field(default_factory=set)       # playlist ids
    sources: bool = False
    skipped: set[str] = field(default_factory=set)       # changed meanwhile

    def any(self) -> bool:
        return bool(self.settings or self.favorites or self.history or self.sources)


def _apply_settings(settings: Any, new: dict[str, Any], old: dict[str, Any],
                    out: Applied) -> None:
    for key in set(new) | set(old):
        if not syncable(key):
            continue
        value = new.get(key)
        if value == old.get(key):
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
                     old: dict[str, Any], fps: dict[str, str], out: Applied) -> None:
    new_b, old_b = _by_bucket(new, 2), _by_bucket(old, 2)
    kinds = {k: (prefix, id_key) for k, prefix, id_key in _FAV_KINDS}
    for bucket in set(new_b) | set(old_b):
        fp, kind = bucket
        pid = fps.get(fp)
        if pid is None or kind not in kinds:
            continue
        mine = new_b.get(bucket, {})
        if _canon(mine) == _canon(old_b.get(bucket, {})):
            continue
        prefix, _id_key = kinds[kind]
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
        ctx.settings.setValue(f"{prefix}_{pid}", json.dumps(groups))
        if colours:
            ctx.settings.setValue(f"{prefix}_{pid}_colors", json.dumps(colours))
        else:
            ctx.settings.remove(f"{prefix}_{pid}_colors")
        out.favorites.add(pid)


def _apply_history(ctx: Context, new: dict[str, Any], old: dict[str, Any],
                   fps: dict[str, str], out: Applied) -> None:
    new_b, old_b = _by_bucket(new, 2), _by_bucket(old, 2)
    for bucket in set(new_b) | set(old_b):
        fp, sub = bucket
        pid = fps.get(fp)
        mine = new_b.get(bucket, {})
        if pid is None or _canon(mine) == _canon(old_b.get(bucket, {})):
            continue
        if sub == "h":
            here = [e for e in _json(ctx.settings.value(f"history_{pid}"), [])
                    if isinstance(e, dict) and e.get("_kind") in _LOCAL_KINDS]
            rows = here + [v for v in mine.values() if isinstance(v, dict)]
            rows.sort(key=lambda e: str(e.get("_watched_at") or ""), reverse=True)
            ctx.settings.setValue(f"history_{pid}", json.dumps(rows[:MAX_HISTORY]))
        elif sub == "r" and ctx.resume_settings is not None:
            key = f"resume_positions_{pid}"
            data = {k: v for k, v in _json(ctx.resume_settings.value(key), {}).items()
                    if not str(k).startswith(_RESUME_GROUPS)}
            for ident, v in mine.items():
                parts = _parts(ident)
                if len(parts) >= 3 and isinstance(v, dict):
                    data[parts[2]] = v
            ctx.resume_settings.setValue(key, json.dumps(data))
        else:
            continue
        out.history.add(pid)


def _apply_sources(ctx: Context, new: dict[str, Any], old: dict[str, Any],
                   out: Applied) -> None:
    store = ctx.playlists
    if store is None or _canon(new) == _canon(old):
        return
    here = {str(p["id"]): p for p in ctx.sources()}
    fps_here = {source_fp(p) for p in here.values()}
    wanted: dict[str, dict] = {}
    for ident, v in new.items():
        parts = _parts(ident)
        if parts[:1] == ["s"] and len(parts) == 2 and isinstance(v, dict):
            wanted[parts[1]] = v
    for pid, p in here.items():
        v = wanted.get(pid)
        if v is None:
            if _eid("s", pid) not in old:
                continue
            if pid == str(store.active_id):
                log.info("sync: playlist removed elsewhere kept - it is in use here")
                continue
            store.remove(pid)
            out.sources = True
        elif _canon(dict(v, id=pid)) != _canon(p):
            # A new dict, not this one changed: the playlist in use is held
            # by what plays now, which goes on as it was until the next start.
            store.items[store.items.index(p)] = dict(v, id=pid)
            store.update(pid)
            out.sources = True
    for pid, v in wanted.items():
        if pid in here or source_fp(v) in fps_here:
            continue                # here already, under an id of this computer's
        store.add(dict(v, id=pid))
        fps_here.add(source_fp(v))
        out.sources = True
    order = new.get(_eid("o"))
    if isinstance(order, list) and order != old.get(_eid("o")):
        current = [str(p["id"]) for p in ctx.sources()]
        store.reorder([str(x) for x in order])
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
            _apply_settings(ctx.settings, new, old, out)
        elif cat == "sources":
            _apply_sources(ctx, new, old, out)
        elif cat == "favorites":
            _apply_favorites(ctx, result.merged["c"].get(cat) or {}, new, old,
                             ctx.fps(), out)
        elif cat == "history":
            _apply_history(ctx, new, old, ctx.fps(), out)
    return out, keep


# -- this computer's copy --------------------------------------------------------------------

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

    @classmethod
    def load(cls, path: Path, slot: str) -> State:
        st = cls(path, slot)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return st
        if not isinstance(data, dict) or data.get("slot") != slot:
            return st
        doc = data.get("doc")
        if isinstance(doc, dict) and isinstance(doc.get("c"), dict):
            st.doc = {"v": FORMAT, "c": {str(c): dict(m) for c, m in doc["c"].items()
                                         if isinstance(m, dict)}}
        st.version = int(data.get("version") or 0)
        st.fresh = bool(data.get("fresh"))
        st.joining = bool(data.get("joining"))
        seen = data.get("seen")
        st.seen = {str(c) for c in seen} if isinstance(seen, list) else set()
        return st

    def dump(self) -> str:
        return json.dumps({"slot": self.slot, "version": self.version,
                           "fresh": self.fresh, "joining": self.joining,
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
