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
"""
from __future__ import annotations

from typing import Any

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
              "local": "_place_local_show"}
#: ...and the ones asked to show their place's content again (a profile,
#: a source, followed series or sync changed what they show).
REFRESH_HOOKS = {"mitt": "_mitt_refresh", "tv": "_place_tv_refresh",
                 "films": "_place_films_refresh", "series": "_place_series_refresh",
                 "local": "_place_local_refresh"}

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
    (Guide, Search) over the list it was opened from."""
    if start_choice(settings) == "mitt":
        return "mitt", ""
    last = valid(settings.value(LAST_KEY, ""))
    base = valid(settings.value(LAST_LIST_KEY, ""))
    if base in PAGE_PLACES:
        base = ""
    if last in PAGE_PLACES:
        return base or DEFAULT_PLACE, last
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
    """Popular right now at the top of Movies ("vod") or Series."""
    key = POPULAR_KEYS.get(kind)
    return bool(key) and _flag(settings.value(key, "true"), True)


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
