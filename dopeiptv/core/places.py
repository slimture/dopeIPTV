"""The app's places - what the sidebar is: Mine, TV, Movies, Series, Guide,
Search, then Local files, Radio, Recordings - with no Qt: their names, the
sidebar entry and list mode of each, where the app opens (where the person
was last, or always Mine), and Home's settings retired into these.

A place is one of two kinds: a list place (Mine, TV, Movies, Series, Local
files, Radio, Recordings: the middle pane's list, or a page a place builds
over it) or a page place (the Guide, Search: a page over the list place
the person came from, which Back and Esc return to).

``last_place`` is the profile's own (where each person left off);
``start_place`` the household's ("last" or "mitt").

What the places show, each profile its own (Settings > Appearance, "What
the places show"): the sidebar's entries a person never uses taken out
(``HIDEABLE``: never Mine, TV, Movies or Series - a hidden one still opens
from its shortcut, the palette and the menus), the entries folded into a
compact row of icons (``FOLDED_KEY``), the rows over a place's list
(``ROWS``: Recently watched, Continue, Popular right now, what records now,
My stations - hidden from their heading or in Settings; Popular right now
keeps its own keys on Posters & info, ``POPULAR_KEYS``), and "Just the
list" (``JUST_LIST_KEY``), which hides the rows over TV, Movies, Series and
Local files without touching the rows' own keys.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

#: Every place, in the sidebar's order: the upper group, then the lower.
PLACES = ("mitt", "tv", "films", "series", "guide", "search", "local", "radio", "rec")
UPPER = PLACES[:6]
LOWER = PLACES[6:]
#: Pages over the list place under them.
PAGE_PLACES = frozenset({"guide", "search"})
LIST_PLACES = tuple(p for p in PLACES if p not in PAGE_PLACES)

#: The sidebar entry (nav key) of each place. TV, Movies and Series keep the
#: list modes' names, as the entries always had.
NAV_OF = {"mitt": "mitt", "tv": "live", "films": "vod", "series": "series",
          "guide": "guide", "search": "search", "local": "local", "radio": "radio",
          "rec": "rec"}
PLACE_OF_NAV = {nav: place for place, nav in NAV_OF.items()}
#: The list mode a list place shows; Mine's are its tabs (MITT_MODES).
MODE_OF = {"tv": "live", "films": "vod", "series": "series", "local": "local",
           "radio": "radio", "rec": "rec"}
#: Mine's lists, as its tabs: favourites, Watch later, History (and History's
#: Watched filter, a mode of its own).
MITT_MODES = ("fav", "watchlist", "history", "watched")
MITT_TABS = ("fav", "watchlist", "history")

#: The window's methods the places' own pages answer to: each returns True
#: once it showed its page (ui/mw_places.py calls them when they exist).
SHOW_HOOKS = {"mitt": "_mitt_show", "tv": "_place_tv_show",
              "films": "_place_films_show", "series": "_place_series_show",
              "local": "_place_local_show", "radio": "_place_radio_show",
              "rec": "_place_rec_show"}
#: ...and the ones asked to show their place's content again (a profile,
#: a source, followed series or sync changed what they show).
REFRESH_HOOKS = {"mitt": "_mitt_refresh", "tv": "_place_tv_refresh",
                 "films": "_place_films_refresh", "series": "_place_series_refresh",
                 "local": "_place_local_refresh", "radio": "_place_radio_refresh",
                 "rec": "_place_rec_refresh"}

START_KEY = "start_place"
LAST_KEY = "last_place"
LAST_LIST_KEY = "last_list_place"
#: Where the app opens: where the person was last, or always on Mine.
START_CHOICES = ("last", "mitt")
#: A first start (or nowhere known): TV, as the app always opened.
DEFAULT_PLACE = "tv"
#: Popular right now at the top of Movies and of Series.
POPULAR_KEYS = {"vod": "popular_movies", "series": "popular_series"}
#: Set once Home's settings have been carried over and taken away.
RETIRED_KEY = "home_retired"

#: The sidebar's entries a person may take out of it, in the sidebar's
#: order; Mine, TV, Movies and Series always stay.
HIDEABLE = ("guide", "search", "local", "radio", "rec")
#: The places hidden from the sidebar ("guide,radio"), the entries folded
#: into a compact row, and "Just the list" - each profile's own.
HIDDEN_KEY = "places_hidden"
FOLDED_KEY = "places_folded"
JUST_LIST_KEY = "just_the_list"


@dataclass(frozen=True)
class Row:
    """A row over a place's list that can be hidden: its *name*, the
    *place* it is on, the settings *key* that keeps it on or off (on by
    default), the words of its heading (*words*, an i18n key), whether
    that key is the profile's own (*own*; Popular right now keeps the
    household's on Posters & info) and whether Just the list hides it
    (*plain*: the rows over TV, Movies, Series and Local files; what
    records now and the favourite stations stay)."""
    name: str
    place: str
    key: str
    words: str
    own: bool = True
    plain: bool = True


#: Every row that can be hidden - one place to add another place's.
ROWS: dict[str, Row] = {r.name: r for r in (
    Row("tv_recent", "tv", "place_row_tv_recent", "tv_recent"),
    Row("films_popular", "films", POPULAR_KEYS["vod"], "card_popular", own=False),
    Row("films_continue", "films", "place_row_films_continue", "card_continue"),
    Row("series_popular", "series", POPULAR_KEYS["series"], "card_popular", own=False),
    Row("series_continue", "series", "place_row_series_continue", "card_continue"),
    Row("local_continue", "local", "place_row_local_continue", "card_continue"),
    Row("radio_favs", "radio", "place_row_radio_favs", "radio_cat_favorites", plain=False),
    Row("rec_now", "rec", "place_row_rec_now", "rec_place_now", plain=False),
)}
#: The rows' own keys (the profile's), and every key of what the places
#: show: a change to one of them is shown at once.
ROW_KEYS = frozenset(r.key for r in ROWS.values() if r.own)
LOOK_KEYS = frozenset({HIDDEN_KEY, FOLDED_KEY, JUST_LIST_KEY, *ROW_KEYS,
                       *POPULAR_KEYS.values()})
#: What a place opens as while Just the list is on - never stored over the
#: person's own choice (``films_view``, ``place_series_view``, ``tv_density``).
JUST_LIST_VIEWS = {"tv": "compact", "films": "list", "series": "list"}


def place_of_mode(mode: str) -> str:
    """The place a list mode belongs to: Mine's lists to Mine."""
    if mode in MITT_MODES:
        return "mitt"
    for place, m in MODE_OF.items():
        if m == mode:
            return place
    return DEFAULT_PLACE


def nav_of_mode(mode: str) -> str:
    return NAV_OF[place_of_mode(mode)]


def valid(place: Any) -> str:
    """*place* when it is one, else ""."""
    p = str(place or "")
    return p if p in PLACES else ""


def _flag(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def start_choice(settings: Any) -> str:
    v = str(settings.value(START_KEY, "last") or "last")
    return v if v in START_CHOICES else "last"


def start_places(settings: Any) -> tuple[str, str]:
    """Where the app opens: (the list place, the page over it or "").
    "mitt" always opens Mine; "last" where the profile left off - a page
    (Guide, Search) over the list it was opened from. A place taken out of
    the sidebar is not opened on: a hidden list place gives way to TV, a
    hidden page to the list under it."""
    if start_choice(settings) == "mitt":
        return "mitt", ""
    gone = hidden(settings)
    last = valid(settings.value(LAST_KEY, ""))
    base = valid(settings.value(LAST_LIST_KEY, ""))
    if base in PAGE_PLACES or base in gone:
        base = ""
    if last in PAGE_PLACES:
        return base or DEFAULT_PLACE, ("" if last in gone else last)
    if last in gone:
        return DEFAULT_PLACE, ""
    return last or base or DEFAULT_PLACE, ""


def note(settings: Any, place: str, list_place: str) -> None:
    """Where the person is now, for the next start (each profile's own)."""
    place, list_place = valid(place), valid(list_place)
    if not place:
        return
    if settings.value(LAST_KEY, "") != place:
        settings.setValue(LAST_KEY, place)
    if list_place and list_place not in PAGE_PLACES \
            and settings.value(LAST_LIST_KEY, "") != list_place:
        settings.setValue(LAST_LIST_KEY, list_place)


def popular_on(settings: Any, kind: str) -> bool:
    """Popular right now at the top of Movies ("vod") or Series - as the
    person set it (Settings > Posters & info); Just the list aside."""
    key = POPULAR_KEYS.get(kind)
    return bool(key) and _flag(settings.value(key, "true"), True)


# -- what the places show ------------------------------------------------------------

def hidden(settings: Any) -> frozenset[str]:
    """The places taken out of the sidebar (only those that may be)."""
    raw = settings.value(HIDDEN_KEY, "") or ""
    if isinstance(raw, (list, tuple)):
        parts: Iterable[Any] = raw
    else:
        parts = str(raw).split(",")
    return frozenset(p for p in (str(x).strip() for x in parts) if p in HIDEABLE)


def hidden_text(names: Iterable[str]) -> str:
    """*names* as the key keeps them: the hideable ones, in the sidebar's
    order ("" for none)."""
    want = set(names)
    return ",".join(p for p in HIDEABLE if p in want)


def set_hidden(settings: Any, place: str, hide: bool) -> bool:
    """Take *place* out of the sidebar (*hide*) or put it back; True when
    that changed what is kept."""
    if place not in HIDEABLE:
        return False
    now = hidden(settings)
    want = (now | {place}) if hide else (now - {place})
    if want == now:
        return False
    settings.setValue(HIDDEN_KEY, hidden_text(want))
    return True


def shown(settings: Any) -> tuple[str, ...]:
    """The places the sidebar lists, in its order."""
    gone = hidden(settings)
    return tuple(p for p in PLACES if p not in gone)


def lower_shown(settings: Any) -> bool:
    """Whether the sidebar's lower group (Local files, Radio, Recordings)
    has an entry left - the hairline over it goes when it has none."""
    gone = hidden(settings)
    return any(p not in gone for p in LOWER)


def folded(settings: Any) -> bool:
    """The places folded into a compact row of icons at the sidebar's top."""
    return _flag(settings.value(FOLDED_KEY, "false"), False)


def just_list(settings: Any) -> bool:
    """Just the list: no rows over the lists, a compact header."""
    return _flag(settings.value(JUST_LIST_KEY, "false"), False)


def row_on(settings: Any, name: str) -> bool:
    """Whether row *name* is on, as the person set it (Just the list aside):
    on unless switched off. An unknown row is off."""
    row = ROWS.get(name)
    if row is None:
        return False
    return _flag(settings.value(row.key, "true"), True)


def set_row(settings: Any, name: str, on: bool) -> bool:
    """Row *name* on or off - Popular right now in its own key on Posters &
    info. True when that changed what is kept."""
    row = ROWS.get(name)
    if row is None or row_on(settings, name) == bool(on):
        return False
    settings.setValue(row.key, "true" if on else "false")
    return True


def row_shown(settings: Any, name: str) -> bool:
    """Whether row *name* shows now: on, and not one Just the list hides
    while it is on."""
    row = ROWS.get(name)
    if row is None or not row_on(settings, name):
        return False
    return not (row.plain and just_list(settings))


def rows_of(place: str) -> tuple[Row, ...]:
    """The rows over *place*'s list that can be hidden, in order."""
    return tuple(r for r in ROWS.values() if r.place == place)


def view_of(settings: Any, place: str, stored: str) -> str:
    """What *place* opens as: the person's *stored* choice, or while Just
    the list is on that place's plain form (rows, compact)."""
    if just_list(settings):
        return JUST_LIST_VIEWS.get(place, stored)
    return stored


def retire_home(settings: Any, active: str = "") -> bool:
    """Home's settings carried over once: "start on Home" becomes start on
    Mine, a popular shelf switched off stays off at the top of its place,
    and the rest of Home's keys go - the household's and every profile's
    (*settings*: the file itself, a ProfileSettings' raw(); *active*: the
    profile whose shelves count). True when it ran."""
    if _flag(settings.value(RETIRED_KEY, ""), False):
        return False
    keys = list(settings.allKeys())
    has = set(keys)
    shown = _flag(settings.value("home_enabled", "true"), True)
    if shown and _flag(settings.value("home_start", "false"), False) \
            and START_KEY not in has:
        settings.setValue(START_KEY, "mitt")
    for kind, key in POPULAR_KEYS.items():
        old_key = "home_sh_pop_movies" if kind == "vod" else "home_sh_pop_series"
        mine = f"profiles/{active}/{old_key}" if active else ""
        old = settings.value(mine) if mine and mine in has else (
            settings.value(old_key) if old_key in has else None)
        if old is not None and not _flag(old, True) and key not in has:
            settings.setValue(key, "false")
    for k in keys:
        leaf = k.rsplit("/", 1)[-1]
        if leaf in ("home_enabled", "home_start") or leaf.startswith(("home_sh_",
                                                                      "home_pcache_")):
            settings.remove(k)
    settings.setValue(RETIRED_KEY, "true")
    return True
