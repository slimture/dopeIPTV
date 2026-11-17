"""Profiles: one per person in the household, the way a streaming service
has them - each with their own favorites, history and resume points, watched
marks, followed series, reminders, statistics, Trakt and Simkl accounts,
Home's shelves, look, and the languages they want to hear and read.

What is a profile's own and what the household shares is declared here, in
one place (OWN_KEYS, OWN_FAMILIES, OWN_PREFIXES; OWN_STORES says which store
keeps what). Everything else is shared: the sources, the recordings and
their rules, the guide, the devices (cast, boxes), Nerd, how the app plays
and reaches the network, and where the windows sit.

How it is kept. A profile's own keys live under ``profiles/<id>/<key>`` in
the same settings files they always lived in (the app's settings and the
resume, follow and cache files beside them). ProfileSettings - the
QSettings the window holds - reads and writes a profile's own key under the
active profile's name and every other key as it is, so the code that reads
"theme" or "favorites_<source>" does not change. A store is given a view
pinned to its profile (pinned()), so one kept past a switch - a worker
finishing - still writes to the profile it belongs to.

The first start with profiles copies everything a person has into the first
profile (MAIN_ID, "Me" in their language) - copied, not moved: the old keys
stay until the next version, so a downgrade still finds them (migrate()).

A profile: a name, a colour from a fixed palette (COLOURS - ui/theme.py's
ACCENTS) or an emoji for its avatar, an optional 4-digit PIN (salted SHA-256,
as the parental PIN), and a parental level: "kids" sees only what the
parental lock leaves open and never asks for the rest.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

from PyQt6.QtCore import QSettings

from .log import log

#: Where a profile's own keys are kept: ``profiles/<id>/<key>``.
ROOT = "profiles"
#: The household's list of profiles (JSON), the one in use, and whether to
#: ask who is watching at start. Shared keys, never a profile's own.
K_LIST = "profiles_list"
K_ACTIVE = "profile_active"
K_ASK = "profile_ask_start"
#: The version that copied everything into the first profile.
K_MIGRATED = "profiles_migrated"
#: The profiles taken out on this computer (JSON list of ids): settings sync
#: never takes one out, and must not bring one back here either.
K_REMOVED = "profiles_removed"
_REMOVED_KEPT = 64
#: The first profile's id: the same on every computer, so settings sync
#: carries it under the names it always had.
MAIN_ID = "me"
#: The most profiles a household keeps.
MAX_PROFILES = 8
MAX_NAME = 24

#: The avatar's colours, by their names in ui/theme.py's ACCENTS.
COLOURS = ("blue", "purple", "teal", "green", "orange", "pink", "red", "gold",
           "indigo", "mint")
#: Emoji offered for an avatar (any other works too).
EMOJI = ("🦊", "🐼", "🐯", "🐸", "🦄", "🐙", "🚀", "⚽", "🎮", "🎸", "🌟", "🍕")

# -- what is a profile's own -----------------------------------------------------

#: A profile's own keys, by their whole name.
OWN_KEYS = frozenset({
    # Your statistics.
    "viewing_stats",
    # Watched marks and Watch later (the cache file).
    "trakt_watched_cache", "trakt_watchlist_cache",
    # The Trakt and Simkl accounts: each person signs in to their own.
    "trakt_access_token", "trakt_refresh_token", "trakt_expires_at",
    "trakt_fav_list_slug", "simkl_access_token", "simkl_refresh_token",
    "simkl_token_expires",
    # Recently searched (kept here when a search remembers them).
    "recent_searches",
    # How the app looks; the TV place's rows, compact or airy (ui/mw_place_tv.py).
    "theme", "accent", "accent_custom", "theme_follow_system", "theme_light",
    "tv_density",
    # The languages to hear and read, and the tracks picked per title.
    "audio_lang", "sub_mode", "sub_lang", "sub_lang2", "sub_last_lang",
    "track_prefs",
    # The season last picked on each series' page (ui/mw_series_page.py).
    "series_seasons",
    # Mine's queue: Watch later in the person's own order (core/mitt.py).
    "watch_later_order",
    # Where each person left off: the app opens there (core/places.py).
    "last_place", "last_list_place",
})
#: Families: the name itself or the name + "_<source id>" (and the
#: favourites' "_locked" / "_colors" beside it).
OWN_FAMILIES = ("favorites", "movie_favorites", "series_favorites",
                "radio_favorites", "web_favorites", "history",
                "resume_positions", "epg_reminders", "followed_series")
#: Prefixes of a profile's own keys (none since Home's shelves went: they
#: are carried over and taken away by core/places.retire_home).
OWN_PREFIXES: tuple[str, ...] = ()

#: The stores that keep a profile's own, by what they keep (core/stores.py,
#: services/resume.py, services/reminders.py, core/viewing_stats.py).
OWN_STORES = {
    "FavoriteStore": "favorites of every kind (channels, films, series, radio, web)",
    "HistoryStore": "Recently viewed",
    "ResumeStore": "Continue watching: where playback stopped",
    "WatchlistStore": "Watch later",
    "WatchedStore": "watched marks",
    "FollowedSeriesStore": "followed series and their news",
    "ReminderStore": "reminders",
    "ViewingLog": "your statistics",
    "TraktClient": "the Trakt account",
    "SimklClient": "the Simkl account",
}
#: What the household shares, in words (and a few of its keys for the test
#: that keeps them out of a profile).
SHARED = ("sources", "recordings and recording rules", "the guide",
          "devices: cast, boxes", "Nerd", "playback and network settings",
          "the window layout", "the parental lock's PIN and locked categories",
          "asking for a donation: a donation is for the app, not one person "
          "(core/donate_rhythm.py)")
SHARED_EXAMPLES = ("playlists", "active_playlist", "recording_jobs", "recordings_dir",
                   "series_rules_abc", "category_overrides_abc", "channel_overrides_abc",
                   "cast_devices", "kodi_devices", "nerd_enabled", "nerd_proxy",
                   "stream_format", "cache_secs", "hwdec_mode", "volume",
                   "window_geometry", "splitter_state", "language",
                   "parental_pin_hash", "parental_salt", "epg_delay_min",
                   "trakt_client_id", "trakt_client_secret", "start_place",
                   "donate_ask", "donate_until", "donate_period",
                   "donate_period_end", "donate_first_seen",
                   K_LIST, K_ACTIVE, K_ASK, K_MIGRATED, K_REMOVED)


def is_own(key: str) -> bool:
    """Whether settings key *key* is a profile's own."""
    key = str(key)
    if key in OWN_KEYS or key.startswith(OWN_PREFIXES):
        return True
    return any(key == f or key.startswith(f + "_") for f in OWN_FAMILIES)


def stored_key(key: str, pid: str) -> str:
    """Where *key* is kept for profile *pid*: under the profile for one of
    its own, as it is otherwise (and for no profile)."""
    key = str(key)
    if not pid or key.startswith(ROOT + "/") or not is_own(key):
        return key
    return f"{ROOT}/{pid}/{key}"


def split_key(key: str) -> tuple[str, str] | None:
    """(profile id, key) of a key kept under a profile, else None."""
    parts = str(key).split("/", 2)
    if len(parts) == 3 and parts[0] == ROOT and parts[1] and parts[2]:
        return parts[1], parts[2]
    return None


def view_keys(raw: Iterable[str], pid: str) -> list[str]:
    """The keys profile *pid* sees among *raw*: its own (by their plain
    names) and the shared ones - not another profile's, nor the copies the
    first start left behind."""
    if not pid:
        return [str(k) for k in raw]
    out = []
    for k in raw:
        k = str(k)
        got = split_key(k)
        if got is not None:
            if got[0] == pid:
                out.append(got[1])
            continue
        if k.startswith(ROOT + "/") or is_own(k):
            continue
        out.append(k)
    return out


# -- the active profile ----------------------------------------------------------

_active = ""


def activate(pid: str) -> None:
    """Make *pid* the profile every ProfileSettings reads for ("" for none:
    every key as it is)."""
    global _active
    if pid != _active:
        log.info("profiles: %s in use", pid or "none")
    _active = str(pid or "")


def active_id() -> str:
    return _active


# -- the settings, as a profile sees them ----------------------------------------

class ProfileSettings(QSettings):
    """The app's settings as the active profile sees them: its own keys
    under its name, the household's as they are. allKeys() lists what the
    profile sees; raw() is the file itself (backup, sync, the first start)."""

    def value(self, key: str, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        return super().value(stored_key(key, _active), *args, **kwargs)

    def setValue(self, key: str, value: Any) -> None:  # type: ignore[override]  # noqa: N802 - Qt's name
        super().setValue(stored_key(key, _active), value)

    def remove(self, key: str) -> None:  # type: ignore[override]
        super().remove(stored_key(key, _active))

    def contains(self, key: str) -> bool:  # type: ignore[override]
        return super().contains(stored_key(key, _active))

    def allKeys(self) -> list[str]:  # noqa: N802 - Qt's name
        return view_keys(super().allKeys(), _active)

    def raw(self) -> RawSettings:
        return RawSettings(self)


class RawSettings:
    """A ProfileSettings' file as it is: every key by its stored name."""

    def __init__(self, base: QSettings) -> None:
        self._base = base

    def value(self, key: str, *args: Any, **kwargs: Any) -> Any:
        return QSettings.value(self._base, key, *args, **kwargs)

    def setValue(self, key: str, value: Any) -> None:  # noqa: N802 - Qt's name
        QSettings.setValue(self._base, key, value)

    def remove(self, key: str) -> None:
        QSettings.remove(self._base, key)

    def contains(self, key: str) -> bool:
        return bool(QSettings.contains(self._base, key))

    def allKeys(self) -> list[str]:  # noqa: N802 - Qt's name
        return list(QSettings.allKeys(self._base))

    def sync(self) -> None:
        self._base.sync()

    def fileName(self) -> str:  # noqa: N802 - Qt's name
        return self._base.fileName()

    def fallbacksEnabled(self) -> bool:  # noqa: N802 - Qt's name
        return bool(self._base.fallbacksEnabled())

    def setFallbacksEnabled(self, on: bool) -> None:  # noqa: N802 - Qt's name
        self._base.setFallbacksEnabled(on)


def raw(settings: Any) -> Any:
    """*settings* as the file has it: a ProfileSettings' file itself, any
    other settings object as it is."""
    if isinstance(settings, ProfileSettings):
        return settings.raw()
    if isinstance(settings, PinnedSettings):
        return settings.raw()
    return settings


def view_of(settings: Any) -> tuple[Any, str] | None:
    """(the object behind *settings*' file, the profile its keys are read
    for): the same for every pinned view of one profile in one file, made
    when it may be (core/stores.KeptValues shares by it). A plain settings
    object is its own, for no profile; None for a ProfileSettings itself,
    whose view follows the profile in use."""
    if isinstance(settings, ProfileSettings):
        return None
    pid = ""
    if isinstance(settings, PinnedSettings):
        pid, settings = settings.pid, settings.raw()
    if isinstance(settings, RawSettings):
        settings = settings._base
    return settings, pid


class PinnedSettings:
    """One profile's view of a settings file, whichever profile is active:
    what a store is given, so a store kept past a switch writes to its own
    profile still."""

    def __init__(self, base: Any, pid: str) -> None:
        self._raw = raw(base)
        self.pid = str(pid or "")

    def value(self, key: str, *args: Any, **kwargs: Any) -> Any:
        return self._raw.value(stored_key(key, self.pid), *args, **kwargs)

    def setValue(self, key: str, value: Any) -> None:  # noqa: N802 - Qt's name
        self._raw.setValue(stored_key(key, self.pid), value)

    def remove(self, key: str) -> None:
        self._raw.remove(stored_key(key, self.pid))

    def contains(self, key: str) -> bool:
        return bool(self._raw.contains(stored_key(key, self.pid)))

    def allKeys(self) -> list[str]:  # noqa: N802 - Qt's name
        return view_keys(self._raw.allKeys(), self.pid)

    def sync(self) -> None:
        self._raw.sync()

    def fileName(self) -> str:  # noqa: N802 - Qt's name
        return str(self._raw.fileName())

    def raw(self) -> Any:
        return self._raw


def pinned(settings: Any, pid: str | None = None) -> Any:
    """*settings* pinned to profile *pid* (the active one by default); a
    plain settings object - no profiles in play - as it is."""
    if isinstance(settings, (ProfileSettings, PinnedSettings)):
        return PinnedSettings(settings, _active if pid is None else pid)
    return settings


# -- a kids profile ----------------------------------------------------------------

_ADULT = re.compile(r"(?<![\w])(adults?|xxx|18\s*\+|porn\w*|erotic\w*|for adults)(?![\w])",
                    re.IGNORECASE)


def adult_category(name: str) -> bool:
    """Whether a category's name says it is for adults ("XXX", "Adult",
    "18+"): never shown in a kids profile, locked or not."""
    return bool(_ADULT.search(str(name or "")))


def adult_item(item: dict) -> bool:
    """Whether the provider marks a row for adults (Xtream's is_adult, a
    portal's censored)."""
    flag = item.get("is_adult") if isinstance(item, dict) else None
    return str(flag).strip().lower() in ("1", "true", "yes")


# -- a profile -------------------------------------------------------------------

_WORD = re.compile(r"[^\W_]+")
_PIN = re.compile(r"\d{4}")


def initials(name: str) -> str:
    """A profile's initials: the first letters of its first two words, or
    the first two letters of one word (as a source's avatar has them)."""
    words = _WORD.findall(name or "")
    if not words:
        return "?"
    text = (words[0][0] + words[1][0]) if len(words) > 1 else words[0][:2]
    return text.upper()


def clean_name(text: str) -> str:
    """A profile's name as kept: one line, no control characters, at most
    MAX_NAME characters."""
    text = " ".join("".join(c for c in str(text or "") if c.isprintable()).split())
    return text[:MAX_NAME].strip()


def valid_pin(text: str) -> bool:
    return bool(_PIN.fullmatch(str(text or "")))


def hash_pin(salt: str, pin: str) -> str:
    """As the parental PIN is kept (core/stores.ParentalControl)."""
    return hashlib.sha256((salt + pin).encode()).hexdigest()


@dataclass
class Profile:
    id: str
    name: str
    colour: str = "blue"
    emoji: str = ""
    kids: bool = False
    pin_hash: str = ""
    pin_salt: str = ""

    @property
    def has_pin(self) -> bool:
        return bool(self.pin_hash)

    def initials(self) -> str:
        return initials(self.name)

    def verify(self, pin: str) -> bool:
        return bool(self.pin_hash) and hash_pin(self.pin_salt, str(pin or "").strip()) == self.pin_hash

    def public(self) -> dict[str, Any]:
        """What another program may know of it: never the PIN."""
        return {"id": self.id, "name": self.name, "initials": self.initials(),
                "colour": self.colour, "emoji": self.emoji, "kids": self.kids,
                "pin": self.has_pin}

    @classmethod
    def from_dict(cls, d: dict) -> Profile | None:
        pid = str(d.get("id") or "").strip()
        if not pid or "/" in pid:
            return None
        colour = str(d.get("colour") or "blue")
        return cls(id=pid, name=clean_name(d.get("name") or "") or "?",
                   colour=colour if colour in COLOURS else "blue",
                   emoji=str(d.get("emoji") or "")[:8], kids=bool(d.get("kids")),
                   pin_hash=str(d.get("pin_hash") or ""),
                   pin_salt=str(d.get("pin_salt") or ""))


@dataclass
class ProfileStore:
    """The household's profiles, kept in the shared settings (K_LIST)."""

    settings: Any
    items: list[Profile] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._raw = raw(self.settings)
        self.reload()

    def reload(self) -> None:
        try:
            data = json.loads(self._raw.value(K_LIST, "") or "[]")
        except (TypeError, ValueError):
            data = []
        seen: set[str] = set()
        self.items = []
        for d in data if isinstance(data, list) else []:
            p = Profile.from_dict(d) if isinstance(d, dict) else None
            if p is not None and p.id not in seen:
                seen.add(p.id)
                self.items.append(p)

    def exists(self) -> bool:
        return bool(self.items)

    def _save(self) -> None:
        self._raw.setValue(K_LIST, json.dumps([asdict(p) for p in self.items],
                                              ensure_ascii=False))

    def profiles(self) -> list[Profile]:
        return list(self.items)

    def get(self, pid: str) -> Profile | None:
        return next((p for p in self.items if p.id == pid), None)

    @property
    def active_id(self) -> str:
        pid = str(self._raw.value(K_ACTIVE, "") or "")
        if self.get(pid) is not None:
            return pid
        return self.items[0].id if self.items else ""

    def active(self) -> Profile | None:
        return self.get(self.active_id)

    def set_active(self, pid: str) -> None:
        if self.get(pid) is not None:
            self._raw.setValue(K_ACTIVE, pid)

    @property
    def ask_at_start(self) -> bool:
        return str(self._raw.value(K_ASK, "true")) == "true"

    def set_ask_at_start(self, on: bool) -> None:
        self._raw.setValue(K_ASK, "true" if on else "false")

    def add(self, name: str, colour: str = "", emoji: str = "", kids: bool = False,
            pid: str | None = None) -> Profile:
        """A new profile. Raises ValueError for no name, a name taken, or
        one profile too many."""
        name = clean_name(name)
        if not name:
            raise ValueError("name")
        if self.name_taken(name):
            raise ValueError("taken")
        if len(self.items) >= MAX_PROFILES:
            raise ValueError("full")
        used = {p.colour for p in self.items}
        if colour not in COLOURS:
            colour = next((c for c in COLOURS if c not in used), COLOURS[len(self.items) % len(COLOURS)])
        new_id = pid or uuid.uuid4().hex[:8]
        while self.get(new_id) is not None or new_id == MAIN_ID and pid is None:
            new_id = uuid.uuid4().hex[:8]
        p = Profile(id=new_id, name=name, colour=colour, emoji=emoji[:8], kids=kids)
        self.items.append(p)
        self._save()
        return p

    def name_taken(self, name: str, but: str = "") -> bool:
        folded = clean_name(name).casefold()
        return any(p.name.casefold() == folded and p.id != but for p in self.items)

    def update(self, pid: str, **fields: Any) -> None:
        p = self.get(pid)
        if p is None:
            return
        if "name" in fields:
            name = clean_name(fields["name"])
            if not name or self.name_taken(name, but=pid):
                raise ValueError("taken" if name else "name")
            p.name = name
        if "colour" in fields and fields["colour"] in COLOURS:
            p.colour = fields["colour"]
        if "emoji" in fields:
            p.emoji = str(fields["emoji"] or "")[:8]
        if "kids" in fields:
            p.kids = bool(fields["kids"])
        self._save()

    def set_pin(self, pid: str, pin: str) -> None:
        if not valid_pin(pin):
            raise ValueError("pin")
        p = self.get(pid)
        if p is None:
            return
        p.pin_salt = uuid.uuid4().hex
        p.pin_hash = hash_pin(p.pin_salt, pin)
        self._save()

    def clear_pin(self, pid: str) -> None:
        p = self.get(pid)
        if p is not None:
            p.pin_hash = p.pin_salt = ""
            self._save()

    def verify(self, pid: str, pin: str) -> bool:
        p = self.get(pid)
        return p is not None and p.verify(pin)

    def grown_up_pin(self, pin: str) -> bool:
        """Whether *pin* is the PIN of a profile that is not a kids' one
        (what opens Settings from a kids profile)."""
        return any(not p.kids and p.verify(pin) for p in self.items)

    def grown_up_pins(self) -> bool:
        return any(not p.kids and p.has_pin for p in self.items)

    def remove(self, pid: str, files: Iterable[Any] = ()) -> bool:
        """Take profile *pid* out, and everything of its own in *files*
        (the settings files). Never the last one, nor the one in use."""
        if self.get(pid) is None or len(self.items) <= 1 or pid == self.active_id:
            return False
        self.items = [p for p in self.items if p.id != pid]
        self._save()
        gone = [x for x in self.removed() if x != pid] + [pid]
        self._raw.setValue(K_REMOVED, json.dumps(gone[-_REMOVED_KEPT:]))
        head = f"{ROOT}/{pid}/"
        for s in files:
            if s is None:
                continue
            r = raw(s)
            for k in [k for k in r.allKeys() if str(k).startswith(head)]:
                r.remove(k)
            r.sync()
        return True

    def removed(self) -> list[str]:
        """The ids of the profiles taken out on this computer."""
        try:
            got = json.loads(self._raw.value(K_REMOVED, "") or "[]")
        except (TypeError, ValueError):
            return []
        return [str(x) for x in got] if isinstance(got, list) else []

    def merge_public(self, rows: Iterable[dict]) -> bool:
        """Profiles another computer has (settings sync): one not here is
        added, one here takes its name, colour, emoji and level. The PIN
        stays each computer's own, and nothing is ever taken out - nor one
        taken out here brought back."""
        changed = False
        gone = set(self.removed())
        for row in rows:
            p = Profile.from_dict(row) if isinstance(row, dict) else None
            if p is None or p.id in gone:
                continue
            mine = self.get(p.id)
            if mine is None:
                if len(self.items) >= MAX_PROFILES:
                    continue
                name = p.name
                n = 2
                while self.name_taken(name):
                    name, n = clean_name(f"{p.name} {n}"), n + 1
                self.items.append(Profile(p.id, name, p.colour, p.emoji, p.kids))
                changed = True
            elif (mine.name, mine.colour, mine.emoji, mine.kids) != (p.name, p.colour, p.emoji, p.kids):
                if not self.name_taken(p.name, but=mine.id):
                    mine.name = p.name
                mine.colour, mine.emoji, mine.kids = p.colour, p.emoji, p.kids
                changed = True
        if changed:
            self._save()
        return changed


# -- the first start with profiles ------------------------------------------------

def migrate(main: Any, others: Iterable[Any] = (), name: str = "Me",
            version: str = "") -> bool:
    """Make everything a person has the first profile's (MAIN_ID, *name*):
    each of its own keys in *main* and the *others* files copied under it.
    Copied, not moved - the old keys stay, so a downgrade finds them. Does
    nothing when the household has profiles already; True when it ran.
    A copy already made (a start that stopped half way) is kept as it is."""
    store = ProfileStore(main)
    if store.exists():
        return False
    files = [raw(main)] + [raw(s) for s in others if s is not None]
    copied = 0
    for s in files:
        for key in list(s.allKeys()):
            key = str(key)
            if key.startswith(ROOT + "/") or not is_own(key):
                continue
            dest = stored_key(key, MAIN_ID)
            if s.contains(dest):
                continue
            s.setValue(dest, s.value(key))
            copied += 1
        s.sync()
    store.items = [Profile(MAIN_ID, clean_name(name) or "Me", COLOURS[0])]
    store._save()
    r = raw(main)
    r.setValue(K_ACTIVE, MAIN_ID)
    r.setValue(K_MIGRATED, version or "1")
    r.sync()
    log.info("profiles: the first profile made (%d of its own copied)", copied)
    return True


def left_behind(settings: Any) -> Any | None:
    """The keys' old names in *settings*, where migrate() left the first
    profile's own as they were - for the first profile's view of a settings
    file; None for another profile's, and with no profiles in play. What a
    person takes out for good (history cleared, an account signed out) is
    taken out there too: left there it stayed in the file, went into every
    backup, and a downgrade brought it back."""
    if isinstance(settings, PinnedSettings):
        pid = settings.pid
    elif isinstance(settings, ProfileSettings):
        pid = _active
    else:
        return None
    return PinnedSettings(settings, "") if pid == MAIN_ID else None


def drop_left_behind(settings: Any, key: str, keep: Callable[[Any], Any]) -> None:
    """One thing a person takes out of the first profile's own - a row of
    Recently viewed, a resume point, a followed series - taken out of the
    copy under *key*'s old name too (left_behind): *keep* is given that
    copy's JSON and returns what stays of it. Nothing for another profile,
    with no profiles in play, or where there is no such copy."""
    old = left_behind(settings)
    if old is None or not old.contains(key):
        return
    try:
        data = json.loads(old.value(key, "") or "null")
    except (TypeError, ValueError):
        return
    if data is None:
        return
    kept = keep(data)
    if kept != data:
        old.setValue(key, json.dumps(kept))


def start(main: Any, others: Iterable[Any] = (), name: str = "Me",
          version: str = "") -> ProfileStore:
    """At start, before anything reads the settings: the first profile made
    if there is none yet, and the last one used made active."""
    try:
        migrate(main, others, name, version)
    except Exception as e:
        # Left on the keys as they always were: nothing is lost, and the
        # next start tries again.
        log.warning("profiles: the first profile was not made: %s", e)
        activate("")
        return ProfileStore(main)
    store = ProfileStore(main)
    activate(store.active_id)
    return store
