"""Profiles in the window (core/profiles.py keeps them, ui/profiles_ui.py
draws them): the stores of the profile in use, switching to another - the
playback stopped first, everything of the profile's read again, no restart -
the "Who's watching?" picker at start, the PIN of a profile that has one
(wrong ones counted, each making the next try wait a little longer -
``_pin_must_wait``, ``_pin_was_wrong``, core/pins.Tries: in the window and
in TV mode, which ask through here), a kids profile's limits (no locked
category or folder, never a PIN question for one, Settings only with a
grown-up's PIN), and the profile as the phone remote, the remote API and
Home Assistant see and switch it."""

from __future__ import annotations

import math
import threading
import time
from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMessageBox

from ..core import pins, profiles
from ..core.log import log
from ..core.profiles import Profile, ProfileSettings, adult_category, adult_item
from ..core.stores import (
    FavoriteStore, FollowedSeriesStore, HistoryStore, WatchedStore, WatchlistStore,
)
from ..core.workers import run_async
from ..i18n import tr

#: Wrong PINs asked over the network (the phone remote, the remote API, Home
#: Assistant) within REMOTE_PIN_SECS before a switch by PIN is refused until
#: the oldest of them is that old: four digits are 10,000 guesses, which a
#: script runs through in minutes.
REMOTE_PIN_TRIES = 5
REMOTE_PIN_SECS = 300.0

#: What the phone remote, the remote API and Home Assistant are told while
#: "Who's watching?" waits for a profile's PIN at start.
LOCKED = "who is watching is not chosen yet: pick a profile in the app first"

#: A row's own kind -> the catalogue its category is one of (as
#: MainWindow._channel_hidden reads it); an episode's is its series'.
_KIDS_KIND = {"live": "live", "fav": "live", "catchup": "live", "movie": "vod", "vod": "vod",
              "series": "series", "episode": "series"}
_CATS = {"live": "live_categories", "vod": "vod_categories", "series": "series_categories"}


def _gone(obj: Any) -> bool:
    try:
        return sip.isdeleted(obj)
    except TypeError:           # not a Qt object (a test's window)
        return False


class AdultIds(frozenset):
    """The ids of a source's categories named for adults; ``known`` every
    id the source lists - a row of any other is of no category a kids
    profile may see (a provider lists a category for adults or not; what it
    keeps out of its list is not taken to be for children)."""
    known: frozenset[str] = frozenset()


def adult_ids(cats: Any, overrides: Any, mode: str) -> AdultIds:
    """The ids of *cats* (a source's categories of *mode*) named for adults,
    by the name the lists show them under or the provider's own: a category
    renamed in the app is still one."""
    out: set[str] = set()
    every: set[str] = set()
    for c in cats or []:
        if not isinstance(c, dict) or c.get("category_id") is None:
            continue
        cid, own = c.get("category_id"), str(c.get("category_name") or "")
        every.add(str(cid))
        name = own
        try:
            if overrides is not None:
                name = overrides.display_name(mode, cid, own)
        except Exception:
            pass
        if adult_category(name, own):        # renamed or not
            out.add(str(cid))
    ids = AdultIds(out)
    ids.known = frozenset(every)
    return ids


#: What a kids profile never sees or plays: files on this computer and the
#: recordings - nothing says what they are - and addresses opened by hand.
KIDS_NEVER = frozenset(("local", "recording", "rec", "web"))
#: A radio station's tags that keep it from a kids profile.
_ADULT_TAGS = frozenset(("adult", "erotic", "erotica", "sex", "sexy", "xxx", "porn", "18+"))


def adult_station(it: dict) -> bool:
    """Whether a radio station's own tags or name say it is for adults."""
    tags = str(it.get("tags") or "").lower().replace(";", ",").split(",")
    if any(t.strip() in _ADULT_TAGS for t in tags):
        return True
    return adult_item(it)


class KidsFilter:
    """A kids profile's test for one row - True when it may be shown or
    played: nothing the provider marks for adults, nothing from a category
    named for them (core/profiles.py), in any list, shelf or remote. Made on
    the window's thread; it may run on a worker, which reads a source's
    categories itself when the window does not know them yet. On the
    window's thread a row of a category not known yet is held back until
    they are (read meanwhile on the pool, MainWindow._kids_fetch_cats)."""

    def __init__(self, window: Any, mode: str) -> None:
        self.window, self.mode = window, mode
        self.client = getattr(window, "client", None)
        self.overrides = getattr(window, "overrides", None)
        self.known: dict = window._kids_known()
        # "Only what a grown-up allows": per catalogue the categories of the
        # source in use it may see - read here, on the window's thread, with
        # the client they are of (None: every one not for adults).
        allowed = getattr(window, "_kids_allowed", None)
        self.allowed: dict[str, frozenset[str] | None] = {
            m: (allowed(m) if callable(allowed) else None) for m in _CATS}
        self.only = any(v is not None for v in self.allowed.values())

    def category_ok(self, mode: str, cid: Any, name: Any, own: Any = "") -> bool:
        """Whether a category of the source in use (of *mode*, *name* the
        one shown, *own* the provider's) may be listed for a kids profile:
        none named for adults - and, with "Only what a grown-up allows",
        only one a grown-up chose. The lists' one rule for categories, as
        __call__ is for rows."""
        if adult_category(name, own):
            return False
        allow = self.allowed.get(_KIDS_KIND.get(mode, mode))
        return allow is None or str(cid) in allow

    def _ids(self, mode: str) -> frozenset[str] | None:
        hit = self.known.get(mode)
        if hit is not None and hit[0] is self.client:
            return hit[1]
        if threading.current_thread() is threading.main_thread():
            self.window._kids_fetch_cats()
            return None
        fn = getattr(self.client, _CATS[mode], None)
        try:
            ids = adult_ids(fn() if fn is not None else [], self.overrides, mode)
        except Exception:
            return None
        self.known[mode] = (self.client, ids)
        return ids

    def ready(self) -> KidsFilter:
        """Off the window's thread: this catalogue's categories read now
        when the window does not know them, so that rows tested on the
        window's thread afterwards are decided at once."""
        mode = _KIDS_KIND.get(self.mode)
        if mode is not None and threading.current_thread() is not threading.main_thread():
            self._ids(mode)
        return self

    def __call__(self, it: Any) -> bool:
        if not isinstance(it, dict):
            return True
        ctx = it.get("_series_ctx")
        ctx = ctx if isinstance(ctx, dict) else {}
        if adult_item(it) or adult_item(ctx):
            return False
        kind = str(it.get("_kind") or it.get("_ekind") or self.mode)
        if kind in KIDS_NEVER:
            return False
        if kind == "radio":
            # No category a grown-up could choose: not with only theirs.
            return not self.only and not adult_station(it)
        mode = _KIDS_KIND.get(kind)
        if mode is None:
            return not self.only
        cid = it.get("category_id")
        if cid in (None, ""):
            cid = ctx.get("category_id")
        if cid in (None, ""):
            # Of no category the app knows: not for a kids profile - what a
            # provider leaves unlabelled is not taken to be for children.
            return False
        allow = self.allowed.get(mode)
        if allow is not None and str(cid) not in allow:
            return False            # not one a grown-up chose
        ids = self._ids(mode)
        if ids is None:
            self.window._kids_held = True
            return False
        if isinstance(ids, AdultIds) and str(cid) not in ids.known:
            return False
        return str(cid) not in ids


class _ProfilesMixin:
    _profile_picker = None
    #: The picker at start for a profile with a PIN: nothing else until a
    #: profile is picked (its PIN given).
    _profile_must_pick = False
    #: Every switch counts one: a result coming back for a profile no
    #: longer in use is left alone.
    _profile_gen = 0

    # -- the profile in use ---------------------------------------------------------

    def _profiles_on(self) -> bool:
        """Whether the window's settings are a profile's view: always in the
        app (app.py); a window made with plain settings keeps every key as
        it is, and does not switch."""
        return isinstance(self.settings, ProfileSettings)

    def _profile(self) -> Profile | None:
        store = getattr(self, "profiles", None)
        if store is None:
            return None
        return store.get(profiles.active_id()) or (store.active() if not self._profiles_on() else None)

    def _profile_kids(self) -> bool:
        p = self._profile()
        return bool(p is not None and p.kids)

    # -- what a kids profile is let see, everywhere ------------------------------------

    def _kids_keep(self, mode: str = "live") -> KidsFilter | None:
        """The one test every list, shelf and remote puts a row through for
        a kids profile (*mode*: the catalogue the rows are of when they do
        not say); None for any other profile - nothing to test."""
        return KidsFilter(self, mode) if self._profile_kids() else None

    def _kids_only(self, rows: Any, mode: str = "live") -> list:
        """*rows* as a kids profile may see them (all of them otherwise)."""
        rows = list(rows or [])
        keep = self._kids_keep(mode)
        return rows if keep is None else [r for r in rows if keep(r)]

    def _kids_may_play(self, kind: str, item: Any, key: Any = None) -> bool:
        """The one door every playback goes through for a kids profile
        (_start_playback): what its lists may show, and nothing else -
        whichever way it was asked (a list, the phone, the remote API, Home
        Assistant, a link, a drop). A row the app cannot tell about (no
        item, no category) is not played. A channel of another source played
        without switching to it (*key* its failover key: Every source, the
        Search page, failover) only when that source's own lists, as read for
        this profile, hold it (mw_every_source._other_source_listed). Said
        once in a note."""
        if not self._profile_kids():
            return True
        ok = False
        if kind not in KIDS_NEVER and isinstance(item, dict):
            row = dict(item)
            if kind == "episode" and isinstance(self.series_ctx, dict):
                row.setdefault("_series_ctx", self.series_ctx)
            row.setdefault("_kind", {"live": "live", "movie": "vod", "episode": "episode",
                                     "radio": "radio"}.get(kind, kind))
            keep = self._kids_keep("live")
            ok = keep is None or keep(row)
        elif (kind == "live" and item is None and key is not None
              and key == getattr(self, "_failover_key", None)):
            listed = getattr(self, "_other_source_listed", None)
            ok = bool(callable(listed) and listed(getattr(self, "_failover_source", None),
                                                  getattr(self, "_failover_row", None)))
        if not ok:
            log.info("profiles: a kids profile does not play this (%s)", kind)
            self._show_toast(tr("profile_kids_not_here"), 4000)
        return ok

    def _kids_allows(self, it: Any, mode: str = "live") -> bool:
        keep = self._kids_keep(mode)
        return keep is None or keep(it)

    def _kids_allowed(self, mode: str, source_id: str | None = None) -> frozenset[str] | None:
        """With "Only what a grown-up allows" (core/profiles.py
        ``allowed_for``): the categories of *mode* of *source_id* - the
        source in use when None - the kids profile may see; None for any
        other profile, or with the mode off."""
        p = self._profile()
        if p is None or not p.allow_only:
            return None
        if source_id is None:
            store = getattr(self, "playlist_store", None)
            source_id = str(getattr(store, "active_id", "") or "")
        return p.allowed_for(source_id, mode)

    def _kids_only_allowed(self) -> bool:
        """Whether the profile in use sees only what a grown-up allows."""
        p = self._profile()
        return bool(p is not None and p.allow_only)

    def _kids_cat_ok(self, mode: str, cid: Any, name: Any, own: Any = "") -> bool:
        """Whether a category of the source in use may be listed for the
        profile in use (KidsFilter.category_ok): every one for anyone but a
        kids profile."""
        keep = self._kids_keep(mode)
        return keep is None or keep.category_ok(mode, cid, name, own)

    def _kids_never_places(self) -> set[str]:
        """The places a kids profile never has: Local files and Recordings
        (KIDS_NEVER) - and Radio while it sees only what a grown-up
        allows (a station is of no category one could choose)."""
        if not self._profile_kids():
            return set()
        return {"local", "rec", "radio"} if self._kids_only_allowed() else {"local", "rec"}

    def _live_rows_job(self, client: Any):
        """For a worker: *client*'s channels - and a kids profile's
        categories read beside them, so the window's thread that goes
        through the rows (_chno_rows) decides them at once."""
        keep = self._kids_keep("live")

        def job():
            rows = client.live_streams(None)
            if keep is not None:
                keep.ready()
            return rows
        return job

    def _kids_known(self) -> dict:
        """mode -> (client, the ids of its categories named for adults)."""
        return self.__dict__.setdefault("_kids_cats", {})

    def _kids_note_cats(self, mode: str, cats: Any) -> None:
        """A source's categories of *mode* as the window read them (its list)."""
        if mode in _CATS:
            self._kids_known()[mode] = (getattr(self, "client", None),
                                        adult_ids(cats, getattr(self, "overrides", None), mode))

    def _kids_fetch_cats(self) -> None:
        """The source's categories read on the pool, once at a time: a row
        held back on the window's thread for want of them is shown when
        they are in (_kids_cats_in)."""
        client = getattr(self, "client", None)
        if client is None or self.__dict__.get("_kids_fetching") is client:
            return
        self._kids_fetching = client
        overrides = getattr(self, "overrides", None)

        def work():
            out: dict[str, AdultIds] = {}
            for mode, name in _CATS.items():
                fn = getattr(client, name, None)
                try:
                    out[mode] = adult_ids(fn() if fn is not None else [], overrides, mode)
                except Exception as e:
                    log.debug("profiles: %s categories not read (%s)", mode, type(e).__name__)
            return client, out

        run_async(self.pool, work, self._kids_cats_in, self._kids_cats_failed)

    def _kids_cats_failed(self, _msg: Any = None) -> None:
        self._kids_fetching = None

    def _kids_cats_in(self, got: Any) -> None:
        client, found = got
        self._kids_fetching = None
        if _gone(self) or client is not getattr(self, "client", None):
            return
        known = self._kids_known()
        for mode, ids in found.items():
            known[mode] = (client, ids)
        if not self.__dict__.pop("_kids_held", False):
            return
        # Held back while they were not known: shown now.
        self._kids_ver = int(self.__dict__.get("_kids_ver", 0)) + 1
        self._held_rows_in("kids")

    def _held_rows_in(self, reason: str) -> None:
        """Rows held back for want of their categories (a kids profile's
        test, the parental lock's on what the stores kept): shown now in
        every place, list and TV mode's Home."""
        try:
            self._refresh_places(reason)
            if reason == "lock" and getattr(self, "mode", "") == "history" \
                    and not self._guide_page_showing():
                self._load_items(getattr(self, "_current_cat", None))   # History read again
            elif getattr(self, "all_items", None) and not self._guide_page_showing():
                self._apply_filter()
            tv = getattr(self, "_tv_page", None)
            if tv is not None and not _gone(tv) and getattr(self, "_tv_on", False) \
                    and tv.current == "home" and tv.screens.get("home") is not None:
                tv.screens["home"].rebuild()
            if hasattr(self, "_events_favorites"):
                self._events_favorites()
        except Exception:
            log.debug("profiles: the lists after the categories", exc_info=True)

    def _profile_many(self) -> bool:
        store = getattr(self, "profiles", None)
        return store is not None and len(store.profiles()) > 1

    def _own(self, settings: Any) -> Any:
        """*settings* pinned to the profile in use (core/profiles.pinned)."""
        return profiles.pinned(settings)

    # -- its stores -------------------------------------------------------------------

    def _build_source_stores(self, pid: str | None) -> None:
        """The profile's own for source *pid*: favorites, history, resume
        points, reminders, followed series - each pinned to the profile."""
        from ..services.reminders import ReminderStore
        from ..services.resume import ResumeStore
        s = self._own(self.settings)
        self.favs = FavoriteStore(s, f"favorites_{pid}" if pid else "favorites")
        self.favs.on_change = self._favorites_changed
        # Flat, single-group favourites for movies and series - the split
        # Favorites column shows these under 'Movies' and 'Series' beside
        # the grouped channel favourites.
        self.movie_favs = FavoriteStore(
            s, f"movie_favorites_{pid}" if pid else "movie_favorites", id_key="stream_id")
        self.series_favs = FavoriteStore(
            s, f"series_favorites_{pid}" if pid else "series_favorites", id_key="series_id")
        self.history = HistoryStore(s, f"history_{pid}" if pid else "history")
        self.resume = ResumeStore(self._own(self._resume_settings), pid)
        self.reminders = ReminderStore(s, pid)
        self.followed = FollowedSeriesStore(self._own(self._follow_settings), pid)

    def _build_radio_web_favs(self) -> None:
        s = self._own(self.settings)
        self.radio_favs = FavoriteStore(s, "radio_favorites", id_key="stationuuid")
        self.web_favs = FavoriteStore(s, "web_favorites", id_key="url")

    def _build_profile_accounts(self) -> None:
        """The profile's Trakt and Simkl accounts, watched marks and Watch
        later."""
        from ..providers.simkl import SimklClient
        from ..providers.trakt import TraktClient
        s = self._own(self.settings)
        self.trakt = TraktClient(s)
        self.simkl = SimklClient(s)
        cache = self._own(self._cache_settings)
        self.watched = WatchedStore(cache)
        self.watchlist = WatchlistStore(cache)

    # -- switching ----------------------------------------------------------------------

    def switch_profile(self, pid: str) -> bool:
        """Use profile *pid* from now on (its PIN, if any, asked already):
        what plays stops, the profile's own is read, the window goes to its
        start place (where it left off, or Mine), and the lists, the sidebar
        and the guide show it - no restart. False when it
        cannot be (no such profile, or a window without profiles)."""
        store = self.profiles
        target = store.get(pid)
        if target is None:
            return False
        if not self._profiles_on():
            log.warning("profiles: this window keeps no profiles; not switched")
            return False
        if pid == profiles.active_id():
            return True
        log.info("profiles: switching profile")
        self._profile_stop_all()
        self._stats_save()
        self._stats_log = None
        self._profile_gen += 1
        store.set_active(pid)
        profiles.activate(pid)
        # The household's lock opened by one person is not open for the next.
        self.parental.lock_session()
        pl = self.playlist_store.active() if self.playlist_store else None
        self._build_source_stores((pl or {}).get("id"))
        self._watch_favorites()
        self._build_radio_web_favs()
        self._build_profile_accounts()
        self._trakt_active = None
        self._simkl_active = None
        self._fav_trakt_ids = ([], [])
        # A watched sync still running is the profile before's (its answer
        # goes to its own stores): this one syncs at its own time.
        self._watched_sync_running = pid in self.__dict__.get("_watched_sync_pids", ())
        # Watch together: the next room takes this profile's name.
        type(self)._together_name = ""
        self._last_cat = {}
        self._profile_refresh()
        self._show_toast(tr("profile_now", name=target.name), 3000)
        QTimer.singleShot(2000, self._profile_after_switch)
        return True

    def _profile_after_switch(self) -> None:
        if sip.isdeleted(self):
            return
        self._maybe_sync_watched()

    def _profile_stop_all(self) -> None:
        """What plays stops, as Stop does: the resume point, the scrobble
        and the statistics kept for the profile that watched it."""
        try:
            if getattr(self, "_together_room", None) is not None:
                self.together_leave(quiet=True)
        except Exception:
            log.debug("profiles: watch together", exc_info=True)
        mv = getattr(self, "_multiview_win", None)
        if mv is not None and not sip.isdeleted(mv) and mv.isVisible():
            mv.close()
        p = getattr(self, "player", None)
        # Also a channel waiting for its reconnect (no address in the player
        # then): it would come back by itself, in the next profile.
        if p is not None and (getattr(p, "current_url", None)
                              or getattr(self, "_playing_group", None)):
            self._save_resume_position()
            p.stop_btn.click()
        # What played last is the profile before's own: Play after a Stop,
        # Last channel or a reconnect bring nothing of it into this one. (A
        # cast goes on through a switch, and keeps what it plays.)
        casting = getattr(self, "_cast_runs", None)
        if not (callable(casting) and casting()):
            self._last_playback = None
        self._prev_live_item = None
        self._reconnect_off = True

    def _profile_refresh(self) -> None:
        """Everything that shows the profile's own, shown again."""
        self._apply_theme_now()
        self._recent_cache = {}
        self._search_index_cache = {}     # the sidebar search's, made for the profile before
        self._guide_source_changed()      # its reminders' marks: drawn anew
        self._sp_source_changed()         # a series' page: the profile's own marks
        self._pf_source_changed()         # the Movies place and a film's page too
        self._place_series_source_changed()   # the Series place's rows
        self._search_profile_changed()    # the Search page: found for the one before
        self._update_playlist_btn()
        self._refresh_nav_status()
        try:
            self.list_model.refresh_all()
        except Exception:
            pass
        self._refresh_places("profile")
        self._profile_place()
        tv = getattr(self, "_tv_page", None)
        # TV mode's page stays made once it is left: its screens read the
        # new profile's anew even while it is off, or the next time it is
        # on they showed what the profile before was let see.
        if tv is not None and not sip.isdeleted(tv):
            for scr in tv.screens.values():
                if hasattr(scr, "rebuild"):
                    scr.rebuild()
        self._events_profile()
        if hasattr(self, "_donate_show"):
            # A kids profile is never asked (mw_donate): the heart goes now,
            # not at the next minute's look.
            self._donate_show()

    def _profile_room_name(self) -> str:
        """The name Watch together offers: the profile's, with two or more."""
        p = self._profile() if self._profile_many() else None
        return p.name if p is not None else ""

    def _profiles_changed(self) -> None:
        """A profile was added or edited (Settings > Profiles). The kids
        profile in use edited - what a grown-up allows it, say -: what it
        may see is shown again at once."""
        self.profiles.reload()
        self._update_playlist_btn()
        self._events_profile()
        if self._profile_kids():
            self._kids_ver = int(self.__dict__.get("_kids_ver", 0)) + 1
            for again in ("_apply_nav_visibility", "_load_categories"):
                fn = getattr(self, again, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:
                        log.debug("profiles: %s after an edit", again, exc_info=True)
            self._held_rows_in("kids")

    def _profile_remove(self, pid: str) -> bool:
        files = [self.settings, getattr(self, "_resume_settings", None),
                 getattr(self, "_follow_settings", None), getattr(self, "_cache_settings", None)]
        if pid == profiles.active_id():
            return False
        done = self.profiles.remove(pid, files)
        if done:
            log.info("profiles: a profile removed")
            self._profiles_changed()
        return done

    # -- the PIN -------------------------------------------------------------------------

    def _pin_tries(self) -> pins.Tries:
        """The wrong PINs counted (core/pins.py), kept in the household's
        settings so closing the app does not start the count over."""
        tries = self.__dict__.get("_pin_tries_kept")
        if tries is None:
            tries = self._pin_tries_kept = pins.Tries(profiles.raw(self.settings))
        return tries

    def _pin_names(self, *found: Profile) -> list[str]:
        """The PINs a question takes, by their names in the count: those
        profiles', and the parental PIN where there is one (it opens what
        theirs open) - a wrong try is one at each of them."""
        names = [f"profile/{p.id}" for p in found if p.has_pin]
        if self.parental.has_pin():
            names.append("parental")
        return names

    def _pin_must_wait(self, names: list[str], title: str, parent=None) -> bool:
        """Whether a PIN may not be tried yet, after wrong ones - said, with
        how long (a minute at the very most: a slow-down, never a lock)."""
        wait = self._pin_tries().wait(*names)
        if wait <= 0:
            return False
        QMessageBox.information(parent or self, title, tr("pin_wait", n=max(1, math.ceil(wait))))
        return True

    def _pin_was_wrong(self, names: list[str], title: str, parent=None) -> None:
        """A wrong PIN: counted for each PIN the question takes, and said -
        with the wait that follows, from the third on."""
        wait = self._pin_tries().wrong(*names)
        text = tr("msg_wrong_pin")
        if wait > 0:
            text += "\n\n" + tr("pin_wait", n=max(1, math.ceil(wait)))
            log.info("profiles: wrong PINs - the next try waits %d s", math.ceil(wait))
        QMessageBox.warning(parent or self, title, text)

    def _pin_given(self, pin: str, *found: Profile) -> str:
        """Which PIN *pin* is - one of those profiles' ("profile/<id>"), the
        parental one ("parental") - or "" for none of them. Its own wrong
        tries are forgotten, and only its own: knowing one PIN is no way to
        more tries at another."""
        if not pin:
            return ""
        name = next((f"profile/{p.id}" for p in found
                     if p.has_pin and self.profiles.verify(p.id, pin)), "")
        if not name and self.parental.has_pin() and self.parental.verify(pin):
            name = "parental"
        if name:
            self._pin_tries().right(name)
        return name

    def _profile_unlock(self, p: Profile, parent=None) -> bool:
        """Profile *p*'s PIN asked, if it has one (or the parental PIN):
        True when it may be opened."""
        if not p.has_pin:
            return True
        names = self._pin_names(p)
        if self._pin_must_wait(names, p.name, parent):
            return False
        from .profiles_ui import ask_pin
        pin = ask_pin(parent or self, p.name, tr("profile_enter_pin", name=p.name))
        if pin is None:
            return False
        if self._pin_given(pin, p):
            return True
        if pin:
            self._pin_was_wrong(names, p.name, parent)
        else:                           # nothing typed: no guess
            QMessageBox.warning(parent or self, p.name, tr("msg_wrong_pin"))
        return False

    def _profile_pick(self, pid: str, tv: bool = False) -> bool:
        """A profile picked (the picker, the avatar's menu, TV mode): its PIN
        asked, then switched to; the picker goes once it is done."""
        p = self.profiles.get(pid)
        if p is None:
            return False
        at_start = self._profile_picker is not None or tv
        if (pid != profiles.active_id() or at_start) and not self._profile_unlock(p):
            return False
        if pid != profiles.active_id() and not self._kids_leave_unlock(p):
            return False
        ok = self.switch_profile(pid)
        if ok:
            was = self._profile_must_pick
            self._profile_must_pick = False
            if was:
                self._profile_lock_changed()
            self._profile_close_picker()
            if tv:
                page = getattr(self, "_tv_page", None)
                if page is not None and not sip.isdeleted(page):
                    page.show_screen("home")
        return ok

    def _grown_up_pin_exists(self) -> bool:
        return bool(self.profiles.grown_up_pins() or self.parental.has_pin())

    def _kids_leave_unlock(self, target: Profile) -> bool:
        """Leaving a kids profile for a grown-up's that has no PIN of its
        own takes a grown-up's PIN - else a kids profile is left as easily
        as it is picked. A kids profile to another, or to one with a PIN
        (asked already, _profile_unlock), goes as before."""
        if not self._profile_kids() or target.kids or target.has_pin:
            return True
        if not self._grown_up_pin_exists():
            # A kids profile made before one was needed (1.6.8): with no
            # grown-up's PIN anywhere there is no boundary to keep, and the
            # household must not be locked in it.
            log.info("profiles: a kids profile left with no grown-up's PIN set")
            return True
        return self._grown_up_unlock(target.name, tr("profile_kids_leave"))

    def _kids_settings_unlock(self) -> bool:
        """A kids profile opens Settings with a grown-up's PIN: one of a
        profile that is not a kids one, or the parental PIN. With none set
        Settings stays closed to it, and it says how a grown-up sets one -
        from a grown-up's profile, or a kids profile would be no boundary."""
        if self._profile_must_pick:
            return False        # who is watching is not said yet
        if not self._profile_kids():
            return True
        return self._grown_up_unlock(tr("settings_title"), tr("profile_kids_settings"))

    def _grown_up_unlock(self, title: str, text: str) -> bool:
        """A grown-up's PIN asked (a grown-up profile's or the parental
        one): True when it was given. With none set nothing is opened, and
        the person is told how a grown-up sets one."""
        store = self.profiles
        if not self._grown_up_pin_exists():
            QMessageBox.information(self, title, tr("profile_kids_no_pin"))
            return False
        grown_ups = [p for p in store.profiles() if not p.kids]
        names = self._pin_names(*grown_ups)
        if self._pin_must_wait(names, title):
            return False
        from .profiles_ui import ask_pin
        pin = ask_pin(self, title, text, longest=64)
        if pin is None:
            return False
        if self._pin_given(pin, *grown_ups):
            return True
        if pin:
            self._pin_was_wrong(names, title)
        else:
            QMessageBox.warning(self, title, tr("msg_wrong_pin"))
        return False

    # -- the picker -----------------------------------------------------------------------

    def _maybe_pick_profile_at_start(self) -> None:
        """"Who's watching?" as the app starts: with two profiles or more
        when asked for (Settings > Profiles), and always when the one used
        last has a PIN."""
        if not self._profiles_on():
            return
        store = self.profiles
        p = store.active()
        if p is None:
            return
        if (len(store.profiles()) > 1 and store.ask_at_start) or p.has_pin:
            self._profile_must_pick = p.has_pin
            if p.has_pin:
                self._profile_lock_changed()
            self.show_profile_picker()

    def show_profile_picker(self) -> None:
        if getattr(self, "_tv_on", False) and getattr(self, "_tv_page", None) is not None:
            self._tv_page.show_screen("profiles")
            return
        self._profile_close_picker()
        from .profiles_ui import ProfilePicker
        store = self.profiles
        store.reload()
        picker = ProfilePicker(self, store.profiles(), profiles.active_id(), self._profile_picker_can_close)
        picker.picked.connect(self._profile_picked)
        picker.manage.connect(self._profile_manage_from_picker)
        picker.destroyed.connect(self._profile_picker_gone)
        self._profile_picker = picker
        # The menus wait for who is watching.
        self.menuBar().setEnabled(False)
        picker.show()
        picker.fit()

    def _profile_picker_can_close(self) -> bool:
        p = self._profile()
        return p is not None and not p.has_pin

    def _profile_picked(self, pid: str) -> None:
        self._profile_pick(pid)

    def _profile_picker_gone(self, *_a) -> None:
        self._profile_picker = None
        if not sip.isdeleted(self):
            self.menuBar().setEnabled(True)

    def _profile_close_picker(self) -> None:
        picker, self._profile_picker = self._profile_picker, None
        if picker is not None and not sip.isdeleted(picker):
            picker.hide()
            picker.deleteLater()
        self.menuBar().setEnabled(True)

    def _profile_locked(self) -> bool:
        """Whether nothing but picking a profile may be done now (TV mode
        asks it of every key; the window's shortcuts, the media keys, the
        phone remote, the remote API and Home Assistant ask it before they
        play or list anything of the profile's)."""
        return bool(self._profile_must_pick)

    def _profile_refuse_locked(self) -> None:
        """For the phone remote, the remote API and Home Assistant: a clear
        "not yet" while who is watching waits for a PIN."""
        if self._profile_locked():
            raise ValueError(LOCKED)

    def _profile_lock_changed(self) -> None:
        """The start-up lock set or lifted: Home Assistant's favourites list
        follows (the phone's page reads its lists again by itself)."""
        fav = getattr(self, "_events_favorites", None)
        if callable(fav):
            try:
                fav()
            except Exception:
                log.debug("profiles: favourites after the lock", exc_info=True)

    def _profile_manage_from_picker(self) -> None:
        if self._profile_must_pick:
            return          # the profile's PIN first: pick it
        self.open_settings(at_page="profiles")

    def _profile_picker_fit(self) -> None:
        picker = self._profile_picker
        if picker is not None and not sip.isdeleted(picker):
            picker.fit()

    # -- the avatar's menu ------------------------------------------------------------------

    def _fill_profile_menu(self, menu) -> None:
        """The profiles at the top of the avatar's menu (two or more), the
        one in use ticked."""
        if not self._profile_many():
            return
        active = profiles.active_id()
        for p in self.profiles.profiles():
            text = f"{p.emoji}  {p.name}" if p.emoji else p.name
            if p.has_pin:
                text += f"  ·  {tr('profile_pin_on')}"
            act = menu.addAction(text)
            act.setCheckable(True)
            act.setChecked(p.id == active)
            if p.id != active:
                act.triggered.connect(lambda _c=False, pid=p.id: self._profile_pick(pid))
        menu.addSeparator()

    def _profile_manage_action(self, menu) -> None:
        menu.addSeparator()
        act = menu.addAction(tr("profile_manage"))
        act.triggered.connect(self._profile_manage_from_picker)

    # -- the phone remote, the remote API, Home Assistant ---------------------------------------

    def _profile_state(self) -> dict[str, Any]:
        """The profile in use and the household's, as another program may
        know them (never a PIN)."""
        store = getattr(self, "profiles", None)
        p = self._profile()
        rows = [x.public() for x in store.profiles()] if store is not None else []
        return {"profile": p.public() if p is not None else None, "profiles": rows}

    def _profile_switch_remote(self, params: dict) -> dict[str, Any]:
        """A switch asked from the phone, the remote API or Home Assistant:
        by "id" or "name"; one with a PIN only with "pin"."""
        want = str(params.get("id") or params.get("name") or params.get("profile") or "").strip()
        if not want:
            raise ValueError('give "id" or "name" of a profile')
        store = self.profiles
        p = store.get(want) or next(
            (x for x in store.profiles() if x.name.casefold() == want.casefold()), None)
        if p is None:
            raise ValueError("no such profile")
        # "Who's watching?" waiting for the PIN at start: the profile in use
        # is not open yet either - its PIN is asked as the picker asks it.
        at_start = bool(self._profile_must_pick)
        # From a kids profile to a grown-up's without a PIN of its own: a
        # grown-up's PIN, as the window asks one (_kids_leave_unlock).
        leaving = (p.id != profiles.active_id() and self._profile_kids()
                   and not p.kids and not p.has_pin)
        if leaving and not self._grown_up_pin_exists():
            raise ValueError("a grown-up's PIN is needed to leave this profile")
        if ((p.id != profiles.active_id() or at_start) and p.has_pin) or leaving:
            now = time.monotonic()
            fails = [t for t in (getattr(self, "_remote_pin_fails", None) or [])
                     if now - t < REMOTE_PIN_SECS]
            self._remote_pin_fails = fails
            if len(fails) >= REMOTE_PIN_TRIES:
                raise ValueError("too many wrong PINs - try again in a few minutes")
            pin = str(params.get("pin") or "")
            # Through the store: a PIN kept the old way is written anew on
            # a right entry here too (core/pins.py).
            grown = [x for x in store.profiles() if not x.kids and x.has_pin] if leaving else [p]
            if not (pin and (any(store.verify(x.id, pin) for x in grown)
                             or (self.parental.has_pin() and self.parental.verify(pin)))):
                if pin:
                    fails.append(now)
                    if len(fails) >= REMOTE_PIN_TRIES:
                        log.warning("profiles: %d wrong PINs over the network - a switch by PIN "
                                    "is refused for a few minutes", len(fails))
                raise ValueError("this profile has a PIN")
            fails.clear()
        if not self.switch_profile(p.id):
            raise ValueError("profiles are not available")
        # Picked, as in the picker (_profile_pick): nothing is held back any
        # more - Settings, TV mode's keys.
        self._profile_must_pick = False
        if at_start:
            self._profile_lock_changed()
        self._profile_close_picker()
        page = getattr(self, "_tv_page", None)
        if at_start and page is not None and not sip.isdeleted(page) \
                and getattr(page, "current", "") == "profiles":
            page.show_screen("home")
        return dict(self._profile_state(), ok=True)

    def _events_profile(self) -> None:
        """The profile in use in Home Assistant's state."""
        d = getattr(self, "_events", None)
        if d is None:
            return
        p = self._profile()
        try:
            d.set_profile(p.name if p is not None and self._profile_many() else None)
        except Exception:
            log.debug("events: profile", exc_info=True)
