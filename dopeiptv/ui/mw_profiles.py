"""Profiles in the window (core/profiles.py keeps them, ui/profiles_ui.py
draws them): the stores of the profile in use, switching to another - the
playback stopped first, everything of the profile's read again, no restart -
the "Who's watching?" picker at start, the PIN of a profile that has one, a
kids profile's limits (no locked category or folder, never a PIN question
for one, Settings only with a grown-up's PIN), and the profile as the phone
remote, the remote API and Home Assistant see and switch it."""

from __future__ import annotations

import threading
import time
from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMessageBox

from ..core import profiles
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


def adult_ids(cats: Any, overrides: Any, mode: str) -> frozenset[str]:
    """The ids of *cats* (a source's categories of *mode*) named for adults,
    by the name the lists show them under."""
    out: set[str] = set()
    for c in cats or []:
        if not isinstance(c, dict) or c.get("category_id") is None:
            continue
        cid, name = c.get("category_id"), str(c.get("category_name") or "")
        try:
            if overrides is not None:
                name = overrides.display_name(mode, cid, name)
        except Exception:
            pass
        if adult_category(name):
            out.add(str(cid))
    return frozenset(out)


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
        mode = _KIDS_KIND.get(str(it.get("_kind") or it.get("_ekind") or self.mode))
        if mode is None:
            return True
        cid = it.get("category_id")
        if cid in (None, ""):
            cid = ctx.get("category_id")
        if cid in (None, ""):
            return True
        ids = self._ids(mode)
        if ids is None:
            self.window._kids_held = True
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

    def _kids_allows(self, it: Any, mode: str = "live") -> bool:
        keep = self._kids_keep(mode)
        return keep is None or keep(it)

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
            out: dict[str, frozenset[str]] = {}
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
        try:
            if self._home_showing():
                self._home_page.refresh()
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
        what plays stops, the profile's own is read, and Home, the lists,
        the sidebar and the guide show it - no restart. False when it
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
        if p is not None and getattr(p, "current_url", None):
            self._save_resume_position()
            p.stop_btn.click()

    def _profile_refresh(self) -> None:
        """Everything that shows the profile's own, shown again."""
        self._apply_theme_now()
        self._home_poster_cache = None
        self._home_chan_cache = None
        self._recent_cache = {}
        self._search_index_cache = {}     # the sidebar search's, made for the profile before
        self._guide_source_changed()      # its reminders' marks: drawn anew
        self._sp_source_changed()         # a series' page: the profile's own marks
        self._update_playlist_btn()
        self._refresh_nav_status()
        try:
            self.list_model.refresh_all()
        except Exception:
            pass
        if self._home_showing():
            self._home_page.refresh()
        self._load_categories()
        tv = getattr(self, "_tv_page", None)
        # TV mode's page stays made once it is left: its screens read the
        # new profile's anew even while it is off, or the next time it is
        # on they showed what the profile before was let see.
        if tv is not None and not sip.isdeleted(tv):
            for scr in tv.screens.values():
                if hasattr(scr, "rebuild"):
                    scr.rebuild()
        self._events_profile()

    def _profile_room_name(self) -> str:
        """The name Watch together offers: the profile's, with two or more."""
        p = self._profile() if self._profile_many() else None
        return p.name if p is not None else ""

    def _profiles_changed(self) -> None:
        """A profile was added or edited (Settings > Profiles)."""
        self.profiles.reload()
        self._update_playlist_btn()
        self._events_profile()

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

    def _profile_unlock(self, p: Profile, parent=None) -> bool:
        """Profile *p*'s PIN asked, if it has one (or the parental PIN):
        True when it may be opened."""
        if not p.has_pin:
            return True
        from .profiles_ui import ask_pin
        pin = ask_pin(parent or self, p.name, tr("profile_enter_pin", name=p.name))
        if pin is None:
            return False
        if p.verify(pin) or (self.parental.has_pin() and self.parental.verify(pin)):
            return True
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

    def _kids_settings_unlock(self) -> bool:
        """A kids profile opens Settings with a grown-up's PIN: one of a
        profile that is not a kids one, or the parental PIN. With none set
        there is nothing to keep them out with."""
        if self._profile_must_pick:
            return False        # who is watching is not said yet
        if not self._profile_kids():
            return True
        store = self.profiles
        if not store.grown_up_pins() and not self.parental.has_pin():
            return True
        from .profiles_ui import ask_pin
        pin = ask_pin(self, tr("settings_title"), tr("profile_kids_settings"))
        if pin is None:
            return False
        if store.grown_up_pin(pin) or (self.parental.has_pin() and self.parental.verify(pin)):
            return True
        QMessageBox.warning(self, tr("settings_title"), tr("msg_wrong_pin"))
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
        if (p.id != profiles.active_id() or at_start) and p.has_pin:
            now = time.monotonic()
            fails = [t for t in (getattr(self, "_remote_pin_fails", None) or [])
                     if now - t < REMOTE_PIN_SECS]
            self._remote_pin_fails = fails
            if len(fails) >= REMOTE_PIN_TRIES:
                raise ValueError("too many wrong PINs - try again in a few minutes")
            pin = str(params.get("pin") or "")
            if not (pin and (p.verify(pin) or (self.parental.has_pin() and self.parental.verify(pin)))):
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
