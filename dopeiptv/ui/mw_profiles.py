"""Profiles in the window (core/profiles.py keeps them, ui/profiles_ui.py
draws them): the stores of the profile in use, switching to another - the
playback stopped first, everything of the profile's read again, no restart -
the "Who's watching?" picker at start, the PIN of a profile that has one, a
kids profile's limits (no locked category or folder, never a PIN question
for one, Settings only with a grown-up's PIN), and the profile as the phone
remote, the remote API and Home Assistant see and switch it."""

from __future__ import annotations

from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMessageBox

from ..core import profiles
from ..core.log import log
from ..core.profiles import Profile, ProfileSettings
from ..core.stores import (
    FavoriteStore, FollowedSeriesStore, HistoryStore, WatchedStore, WatchlistStore,
)
from ..i18n import tr


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
        self._guide_source_changed()      # its reminders' marks: drawn anew
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
        if tv is not None and not sip.isdeleted(tv) and getattr(self, "_tv_on", False):
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
            self._profile_must_pick = False
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
        asks it of every key)."""
        return bool(self._profile_must_pick)

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
        if p.id != profiles.active_id() and p.has_pin:
            pin = str(params.get("pin") or "")
            if not (pin and (p.verify(pin) or (self.parental.has_pin() and self.parental.verify(pin)))):
                raise ValueError("this profile has a PIN")
        if not self.switch_profile(p.id):
            raise ValueError("profiles are not available")
        self._profile_close_picker()
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
