"""MainWindow mixin: the Search page - one search across every source, a
place in the middle pane where the lists are (ui/search_page.py draws it,
core/search_all.py matches, services/search_sources.py reads the sources).

It opens from its entry in the sidebar, Ctrl+Shift+F (a rebindable key),
the guide's Search button and the command palette's "Search everywhere for
…". Each source's lists and the guide this computer has of it are read off
the window's thread, once a session: a portal's only as far as they were
loaded before, a guide never downloaded for it. A query is matched on a
worker of its own, the newest typed winning; nothing searched is kept but
the page's own query and results while the app runs - and not even those
past leaving the page during private viewing.

A hit acts through the window's own paths:

- a channel, or a programme on now, plays its channel in the docked player
  (the page stays) - one of another source without switching the source in
  use, as failover plays one (mw_failover._play_other_source): its address
  asked for off the window's thread only now, its own user agent, counted
  against its own account, the status line saying "from <source>";
- a programme later sets a reminder (its menu: Remind me, Record) - one of
  another source in that source's own reminders, which fire whichever
  source is in use (mw_reminders), its recording asking that source for
  its address when it starts (mw_recording's LATER);
- a film plays with the usual resume question, a series opens its page and
  Back comes here again, results and scroll as they were. Both need the
  source in use (its resume points, History, Trakt marks and a media
  server's reports are the source in use's): one of another source is
  switched to first - the page and its results stay.

Searching opens no stream; only playing a hit does, through the usual path
with its usual checks.
"""

from __future__ import annotations

import json
import time
from functools import partial
from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import QObject, QPoint, QThreadPool
from PyQt6.QtWidgets import QApplication, QMenu

from ..core import privacy, profiles
from ..core import search_all as sa
from ..core.log import log
from ..core.palette_match import words
from ..core.privacy import for_log
from ..core.stores import FAV_DEFAULT_GROUP, CategoryOverrides, ChannelOverrides
from ..core.workers import run_async
from ..i18n import tr
from ..services import search_sources as ss
from ..services.failover import Shown
from .search_page import SearchPage

#: Why a source was searched only in part, as the page says it.
_PARTLY = {sa.PAGED: "sa_partly_paged", sa.FAILED: "sa_partly_failed",
           sa.GUIDE_CUT: "sa_partly_guide"}
_MODES = ("live", "vod", "series")


class _SearchAllMixin:
    """MainWindow mixin: the Search page."""

    _search_page: SearchPage | None = None
    _sa_return_home = False
    _sa_sources: ss.Sources | None = None
    _sa_latest: sa.Latest | None = None
    _sa_pools: tuple[QThreadPool, QThreadPool] | None = None
    _sa_loading: set | None = None
    _sa_play_gen = 0
    _sa_switch_gen = 0
    #: Who the results were found for (the profile, kids, the lock).
    _sa_made_for: tuple | None = None
    _search_hold = False

    # -- where it is ------------------------------------------------------------------

    def _search_page_showing(self) -> bool:
        page = self._search_page
        stack = getattr(self, "_mid_stack", None)
        return page is not None and stack is not None and stack.currentWidget() is page

    def _search_page_visible(self) -> bool:
        return self._search_page_showing() and self._search_page.isVisible()

    def _ensure_search_page(self) -> SearchPage:
        page = self._search_page
        if page is None:
            page = SearchPage(self.logos)
            page.query_changed.connect(self._sa_query)
            page.activated.connect(self._sa_activate)
            page.menu_requested.connect(self._sa_menu)
            self._search_page = page
            self._mid_stack.addWidget(page)
        return page

    def _sa_pool(self, which: int) -> QThreadPool:
        """The page's own workers: [0] reads the sources (two at a time,
        never two for one source), [1] matches (one at a time: the newest
        query wins anyway)."""
        if self._sa_pools is None:
            parent = self if isinstance(self, QObject) else None
            loads, queries = QThreadPool(parent), QThreadPool(parent)
            loads.setMaxThreadCount(2)
            queries.setMaxThreadCount(1)
            self._sa_pools = (loads, queries)
        return self._sa_pools[which]

    def _sa_store(self) -> ss.Sources:
        if self._sa_sources is None:
            self._sa_sources = ss.Sources()
        return self._sa_sources

    # -- opening it, leaving it --------------------------------------------------------

    def _shortcut_search(self) -> None:
        self._open_search_page()

    def _open_search_page(self, text: str | None = None) -> None:
        """The Search page in the middle pane, the field focused - with
        *text* in it and looked up when given (the palette's row)."""
        if getattr(self, "_tv_on", False):
            return                      # TV mode has a search of its own
        active = QApplication.activeWindow()
        if active is not None and active is not self:
            # From the guide's window: the page is the main window's.
            self.raise_()
            self.activateWindow()
        from_home = self._home_showing()
        back_home = from_home or (self._search_page_showing() and self._sa_return_home)
        if from_home:
            self._leave_home()
        if getattr(self, "_focus_mode", False):
            self._set_focus_mode(False)
        if not self._search_page_showing():
            # Left some other way meanwhile (the Guide, a series): nothing of
            # it kept while viewing privately.
            self._sa_forget_if_private()
        page = self._ensure_search_page()
        self._mid_stack.setCurrentWidget(page)
        self._sa_return_home = back_home
        self._check_nav("search")
        made_for = self._sa_who()
        if self._sa_made_for is not None and self._sa_made_for != made_for:
            # Found for another profile, or with the lock open: not shown.
            page.show_results(None, keep_place=False)
            self._sa_made_for = None
        self._sa_prepare()
        if text is not None:
            page.set_query(text)
        elif page.query().strip() and page.results is None:
            self._sa_query(page.query())
        page.focus_field()

    def _close_search_page(self) -> None:
        """The list back in the middle pane; the page is kept, hidden, with
        its query and results for the next visit (not while viewing
        privately)."""
        self._sa_return_home = False
        if self._search_page_showing():
            self._mid_stack.setCurrentIndex(0)
            self._sa_forget_if_private()

    def _leave_search_page(self) -> None:
        """Back where you were: Home if the page was opened from there, else
        the list under it, with its entry lit."""
        if not self._search_page_showing():
            return
        home = self._sa_return_home
        self._close_search_page()
        if home and self._home_enabled():
            self._show_home_page()
            return
        self._check_nav(self._nav_key_for_mode(self.mode))
        self.listw.setFocus()

    def _search_escape(self) -> None:
        """Esc on the page: the field emptied first, then the page left."""
        page = self._search_page
        if page is not None and page.escape():
            return
        self._leave_search_page()

    def _search_category_clicked(self, _item=None) -> None:
        if self._search_page_showing():
            self._close_search_page()
            self._check_nav(self._nav_key_for_mode(self.mode))

    def _sa_back_from_series(self) -> None:
        """Back on a series' page opened from here: the page as it was."""
        page = self._ensure_search_page()
        self._mid_stack.setCurrentWidget(page)
        self._check_nav("search")
        page.view.setFocus()

    def _sa_forget_if_private(self) -> None:
        if privacy.private() and self._search_page is not None:
            self._sa_latest_gen()
            self._search_page.field.blockSignals(True)
            self._search_page.field.clear()
            self._search_page.field.blockSignals(False)
            self._search_page.show_results(None, keep_place=False)
            self._sa_made_for = None

    # -- the sources ------------------------------------------------------------------

    def _sa_who(self) -> tuple:
        kids = bool(getattr(self, "_profile_kids", lambda: False)())
        locked = kids or not getattr(self.parental, "session_unlocked", False)
        return (profiles.active_id(), kids, locked)

    def _sa_plans(self) -> list[ss.Plan]:
        """Every source searched, as the window has it now (its thread):
        the user's order, what each one's lists leave out, the source in
        use with the window's own client and guide."""
        store = getattr(self, "playlist_store", None)
        active = str(store.active_id or "") if store is not None else ""
        playlists = ss.searchable(store.playlists()) if store is not None else []
        who = self._sa_who()
        kids, locked = who[1], who[2]
        plans: list[ss.Plan] = []
        if not any(str(p.get("id")) == active for p in playlists):
            # No source of the user's in use (the demo's channels).
            pl = {"id": active or "-", "name": getattr(self, "_base_title", "") or "dopeIPTV"}
            playlists = [pl, *playlists]
            active = str(pl["id"])
        for pl in playlists:
            pid = str(pl.get("id"))
            here = pid == active
            if here:
                cats, chans = self.overrides, self.channel_ov
            else:
                cats = CategoryOverrides(self.settings, f"category_overrides_{pid}")
                chans = ChannelOverrides(self.settings, f"channel_overrides_{pid}")
            shown = {m: Shown.of(cats, chans, locked, m) for m in _MODES}
            key = (ss.signature(pl), str(pl.get("name") or ""), who,
                   json.dumps(cats.data, sort_keys=True), json.dumps(chans.data, sort_keys=True))
            plans.append(ss.Plan(ss.source_of(pl), pl, shown, kids,
                                 client=self.client if here else None,
                                 guide=getattr(self, "xmltv", None) if here else None,
                                 key=key))
        return plans

    def _sa_prepare(self, plans: list[ss.Plan] | None = None) -> None:
        """Each source's lists, then its guide week, read off the window's
        thread - once: those not in yet, not being read, not given up on."""
        loading = self._sa_loading if self._sa_loading is not None else set()
        self._sa_loading = loading
        gave_up = self.__dict__.setdefault("_sa_gave_up", set())
        store = self._sa_store()
        plans = self._sa_plans() if plans is None else plans
        store.forget(p.source.id for p in plans)
        for plan in plans:
            pid = plan.source.id
            if pid in loading or (pid, plan.key) in gave_up:
                continue
            if store.ready(plan) is None:
                job = partial(_read_lists, store, plan)
            elif not store.done(plan):
                job = partial(_read_guide, store, plan)
            else:
                continue
            loading.add(pid)
            run_async(self._sa_pool(0), job, partial(self._sa_source_in, pid),
                      partial(self._sa_source_failed, pid, plan.key))

    def _sa_source_in(self, pid: str, _got: Any = None) -> None:
        if self._sa_loading is not None:
            self._sa_loading.discard(pid)
        page = self._search_page
        if page is None or sip.isdeleted(page):
            return
        self._sa_prepare()              # its guide next, once its lists are in
        # More is in: the query again, the row picked and the scroll kept -
        # only while the page is on screen (coming back, it is as it was).
        if self._search_page_showing() and page.query().strip():
            self._sa_query(page.query(), keep_place=True)
        elif not self._sa_loading:
            page.set_busy(False)

    def _sa_source_failed(self, pid: str, key: tuple, err: Any = None) -> None:
        log.info("search: a source was not read: %s", err)
        self.__dict__.setdefault("_sa_gave_up", set()).add((pid, key))
        self._sa_source_in(pid)

    def _search_source_refreshed(self) -> None:
        """Refresh: the source in use's lists and guide read anew next time."""
        store = self._sa_sources
        pl = self._active_source() if hasattr(self, "_active_source") else None
        if store is not None and pl is not None:
            store.drop(str(pl.get("id") or ""))
        self.__dict__.pop("_sa_gave_up", None)

    def _search_profile_changed(self) -> None:
        """Another profile: nothing found for the one before is shown."""
        page = self._search_page
        self._sa_latest_gen()
        self._sa_made_for = None
        if page is not None and not sip.isdeleted(page):
            page.field.blockSignals(True)
            page.field.clear()
            page.field.blockSignals(False)
            page.show_results(None, keep_place=False)

    # -- a query ------------------------------------------------------------------------

    def _sa_latest_gen(self) -> int:
        if self._sa_latest is None:
            self._sa_latest = sa.Latest()
        return self._sa_latest.bump()

    def _sa_query(self, text: str, keep_place: bool = False) -> None:
        """*text* looked up on the page's own worker, in what is in; the
        rows replace the page's once they are - if nothing newer was typed."""
        page = self._search_page
        if page is None:
            return
        gen = self._sa_latest_gen()
        terms = words(text)
        if not terms or len(" ".join(terms)) < sa.MIN_QUERY:
            page.show_results(None, keep_place=False)
            page.set_partial("")
            page.set_busy(False)
            return
        store = self._sa_store()
        plans = self._sa_plans()
        cats = [c for c in (store.ready(p) for p in plans) if c is not None]
        guides = [g for g in (store.guide_in(p) for p in plans) if g is not None]
        if not all(store.done(p) for p in plans):
            self._sa_prepare(plans)
        page.set_busy(bool(self._sa_loading))
        active = next((p.source.id for p in plans if p.client is not None), "")
        latest = self._sa_latest
        made_for = self._sa_who()
        run_async(self._sa_pool(1),
                  partial(sa.search, text, cats, guides, time.time(), latest, gen, sa.CAP, active),
                  partial(self._sa_found, gen, keep_place, made_for),
                  partial(self._sa_search_failed, gen))

    def _sa_found(self, gen: int, keep_place: bool, made_for: tuple,
                  results: sa.Results | None) -> None:
        page = self._search_page
        latest = self._sa_latest
        if (results is None or page is None or sip.isdeleted(page)
                or latest is None or not latest.current(gen)):
            return
        self._sa_made_for = made_for
        page.show_results(results, keep_place=keep_place)
        page.set_partial(self._sa_partial_text(results))
        page.set_busy(bool(self._sa_loading))

    def _sa_search_failed(self, gen: int, err: Any = None) -> None:
        log.warning("search: the query failed: %s", err)
        page = self._search_page
        if page is not None and self._sa_latest is not None and self._sa_latest.current(gen):
            page.set_busy(False)

    @staticmethod
    def _sa_partial_text(results: sa.Results) -> str:
        """"Searched only in part: Portal (only the lists opened so far)" -
        or "" when every source was searched in full."""
        seen: dict[str, str] = {}
        for source, why in results.partial:
            if source.id not in seen and why in _PARTLY:
                seen[source.id] = tr("sa_partly_item", source=source.name or "?",
                                     why=tr(_PARTLY[why]))
        return tr("sa_partly", sources=", ".join(seen.values())) if seen else ""

    # -- what a hit does ----------------------------------------------------------------

    def _sa_is_active(self, source: sa.Source) -> bool:
        store = getattr(self, "playlist_store", None)
        active = str(getattr(store, "active_id", "") or "") if store is not None else ""
        if store is None or store.get(source.id) is None:
            return True                 # the demo's channels: the client in use
        return source.id == active

    def _sa_playlist(self, source: sa.Source) -> dict | None:
        store = getattr(self, "playlist_store", None)
        pl = store.get(source.id) if store is not None else None
        if pl is None:
            self._show_toast(tr("sa_source_gone", source=source.name), 4000)
        return pl

    def _sa_still_allowed(self) -> bool:
        """The results were found for whoever uses the window now (a profile
        switched or the lock shut since: found again, nothing acted on)."""
        if self._sa_made_for is not None and self._sa_made_for != self._sa_who():
            page = self._search_page
            if page is not None:
                page.show_results(None, keep_place=False)
                self._sa_query(page.query())
            return False
        return True

    def _sa_activate(self, hit: sa.Hit) -> None:
        if not self._sa_still_allowed():
            return
        now = time.time()
        if hit.group == "programmes" and hit.later(now):
            self._sa_remind(hit, toggle=True)
        elif hit.group in ("channels", "programmes"):
            self._sa_play_channel(hit)
        elif hit.group == "movies":
            self._sa_play_movie(hit)
        elif hit.group == "series":
            self._sa_open_series(hit)

    def _sa_play_channel(self, hit: sa.Hit) -> None:
        """The channel live in the docked player; the page stays."""
        if self._sa_is_active(hit.source):
            self.play_live_channel(hit.row)
            return
        pl = self._sa_playlist(hit.source)
        if pl is None:
            return
        self._play_channel_elsewhere(pl, hit.row)

    def _play_channel_elsewhere(self, pl: dict, row: dict) -> None:
        """Channel *row* of source *pl* (not the one in use) played without
        switching to it: its address asked for off the window's thread (a
        portal's create_link, a box's lists), then the window's path."""
        self._sa_play_gen += 1
        gen = self._sa_play_gen
        client = self._sa_store().client(pl)
        fmt = self.settings.value("stream_format", "ts")
        self._set_status(tr("status_loading_content"))
        run_async(self.pool, partial(ss.play_address, client, row, fmt),
                  partial(self._sa_address_in, gen, pl, row),
                  partial(self._sa_address_failed, gen, row))

    def _sa_address_in(self, gen: int, pl: dict, row: dict, url: str) -> None:
        if gen != self._sa_play_gen:
            return                      # something else was chosen meanwhile
        if not url:
            self._sa_address_failed(gen, row, "no address")
            return
        log.info("search: %s from another source", for_log(row.get("name")))
        self._play_other_source(pl, row, url)

    def _sa_address_failed(self, gen: int, row: dict, err: Any = None) -> None:
        if gen != self._sa_play_gen:
            return
        log.info("search: no address for %s: %s", for_log(row.get("name")), err)
        self._set_status(tr("sa_no_address", name=str(row.get("name") or "")), error=True)

    def _sa_on_source(self, source: sa.Source, then) -> None:
        """*then* with *source* in use: at once when it is, else once it is
        switched to (the page and its results stay)."""
        if self._sa_is_active(source):
            then()
            return
        pl = self._sa_playlist(source)
        if pl is None:
            return
        self._sa_switch_gen += 1
        gen = self._sa_switch_gen
        self._show_toast(tr("sa_switching", source=source.name), 3000)
        self.switch_playlist(str(pl["id"]), then=partial(self._sa_switched, gen, then))

    def _sa_switched(self, gen: int, then) -> None:
        if gen == self._sa_switch_gen:
            then()

    def _sa_play_movie(self, hit: sa.Hit) -> None:
        self._sa_on_source(hit.source, partial(self._sa_movie_here, hit.row))

    def _sa_movie_here(self, row: dict) -> None:
        """A film of the source in use, in the docked player - with the
        usual resume question; the page stays."""
        sid, ext = row.get("stream_id"), row.get("container_extension")
        if sid is None:
            return
        if self._links_later(partial(self._sa_vod_url, sid, ext),
                             partial(self._sa_movie_here, row)):
            return
        url = self._sa_vod_url(sid, ext)
        if not url:
            self._set_status(tr("sa_no_address", name=str(row.get("name") or "")), error=True)
            return
        self._current_key = self._item_key(row)
        self._start_playback(url, row.get("name") or row.get("title") or "dopeIPTV",
                             row.get("stream_icon") or row.get("cover"),
                             self._item_key(row), "movie", item=row)

    def _sa_vod_url(self, sid: Any, ext: Any) -> str:
        return self.client.vod_url(sid, ext) or ""

    def _sa_open_series(self, hit: sa.Hit) -> None:
        self._sa_on_source(hit.source, partial(self._sa_series_here, hit.row))

    def _sa_series_here(self, row: dict) -> None:
        """The series' page; its Back comes to this page again."""
        self._open_series_page(row, from_search=True)

    # -- a programme later: remind, record --------------------------------------------

    def _sa_reminder_store(self, source: sa.Source):
        """The reminders of *source*: the window's for the source in use,
        else that source's own (they fire whichever is in use)."""
        if self._sa_is_active(source):
            return getattr(self, "reminders", None)
        from ..services.reminders import ReminderStore
        return ReminderStore(self._own(self.settings), source.id)

    def _sa_remind(self, hit: sa.Hit, toggle: bool = True) -> None:
        """A reminder for the programme - said so; given again (*toggle*),
        taken away."""
        p, ch = hit.programme, hit.row
        rem = self._sa_reminder_store(hit.source)
        if p is None or rem is None or ch.get("stream_id") is None:
            return
        title = str(p.get("title") or ch.get("name") or "")
        if rem.has(ch.get("stream_id"), p["start_timestamp"]):
            if not toggle:
                return
            rem.remove(ch.get("stream_id"), p["start_timestamp"])
            self._show_toast(tr("sa_reminder_removed", title=title), 4000)
        elif rem is getattr(self, "reminders", None):
            self._add_reminder(ch, p)
        else:
            rem.add(ch, title, p["start_timestamp"], stop=p.get("stop_timestamp"))
            self._show_toast(tr("reminder_set", title=title), 4000)
        page = self._search_page
        if page is not None:
            page.view.viewport().update()

    def _sa_reminded(self, hit: sa.Hit) -> bool:
        rem = self._sa_reminder_store(hit.source)
        p = hit.programme
        return bool(rem is not None and p is not None
                    and rem.has(hit.row.get("stream_id"), p["start_timestamp"]))

    def _sa_record(self, hit: sa.Hit) -> None:
        """The programme recorded: the source in use's as the guide does it;
        another source's (only one to come) as a recording that asks that
        source for its address when it starts."""
        p, ch = hit.programme, hit.row
        if p is None or ch.get("stream_id") is None:
            return
        if self._sa_is_active(hit.source):
            self._record_programme(ch, {"title": p.get("title")},
                                   p["start_timestamp"], p["stop_timestamp"])
            return
        pl = self._sa_playlist(hit.source)
        if pl is None or not self._within_storage_cap():
            return
        from ..core import series_rules
        from ..core.recording import RecordingManager
        before, after = series_rules.padding(self.settings)
        start, stop = series_rules.padded(p["start_timestamp"], p["stop_timestamp"],
                                          before, after, time.time())
        pid = str(pl.get("id") or "")
        extra = {"source": pid, "source_kind": str(pl.get("kind") or "xtream"),
                 RecordingManager.LATER: {"source": pid, "stream_id": ch["stream_id"]},
                 "stream_key": series_rules.stream_key(ch),
                 "prog_start": p["start_timestamp"], "prog_stop": p["stop_timestamp"]}
        title = str(p.get("title") or ch.get("name") or "")
        self.rec.add_job("", title, start, stop, icon=ch.get("stream_icon"),
                         channel=str(ch.get("name") or ""), extra=extra)
        self._show_toast(tr("sa_recording_set", title=title, source=hit.source.name), 4000)

    # -- its menu -----------------------------------------------------------------------

    def _sa_menu(self, hit: sa.Hit, pos: QPoint) -> None:
        if not self._sa_still_allowed():
            return
        menu = QMenu(self)
        now = time.time()
        here = self._sa_is_active(hit.source)
        if hit.group in ("channels", "programmes"):
            menu.addAction(tr("epg_play_channel"), partial(self._sa_play_channel, hit))
        if hit.group == "programmes" and hit.programme is not None:
            later = hit.later(now)
            if later or (here and hit.on_air(now)):
                menu.addAction(tr("rec_record_programme"), partial(self._sa_record, hit))
            if later:
                menu.addAction(tr("reminder_remove" if self._sa_reminded(hit)
                                  else "reminder_add"), partial(self._sa_remind, hit))
        elif hit.group == "channels" and here:
            on = self.favs.is_favorite(hit.row.get("stream_id"))
            menu.addAction(tr("ctx_remove_from_favorites" if on else "ctx_add_to_favorites"),
                           partial(self._sa_toggle_channel_fav, hit.row))
        elif hit.group == "movies":
            menu.addAction(tr("btn_play"), partial(self._sa_play_movie, hit))
            if here:
                on = self.movie_favs.is_favorite(hit.row.get("stream_id"))
                menu.addAction(tr("ctx_remove_from_favorites" if on else "ctx_add_to_favorites"),
                               partial(self._toggle_media_fav, hit.row, "movie", not on))
        elif hit.group == "series":
            menu.addAction(tr("sa_open_series"), partial(self._sa_open_series, hit))
            if here:
                on = self.series_favs.is_favorite(hit.row.get("series_id"))
                menu.addAction(tr("ctx_remove_from_favorites" if on else "ctx_add_to_favorites"),
                               partial(self._toggle_media_fav, hit.row, "series", not on))
        if not here:
            menu.addSeparator()
            note = menu.addAction(tr("sa_from_source", source=hit.source.name))
            note.setEnabled(False)
        menu.exec(pos)
        menu.deleteLater()

    def _sa_toggle_channel_fav(self, row: dict) -> None:
        sid = row.get("stream_id")
        if sid is None:
            return
        if self.favs.is_favorite(sid):
            self.favs.remove(sid)
        else:
            keep = {k: row.get(k) for k in ("stream_id", "name", "stream_icon", "num",
                                            "epg_channel_id", "category_id", "tv_archive",
                                            "tv_archive_duration") if row.get(k) is not None}
            self.favs.add(FAV_DEFAULT_GROUP, keep)
        self.list_model.refresh_all()

    # -- theme and language -------------------------------------------------------------

    def _search_restyle(self) -> None:
        page = self._search_page
        if page is not None and not sip.isdeleted(page):
            page.restyle()
            page.retranslate()
            if page.results is not None:
                page.set_partial(self._sa_partial_text(page.results))


def _read_lists(store: ss.Sources, plan: ss.Plan) -> str:
    """(the page's worker) A source's lists."""
    store.catalogue(plan)
    return plan.source.id


def _read_guide(store: ss.Sources, plan: ss.Plan) -> str:
    """(the page's worker) A source's guide week, over its lists."""
    store.guide(plan, store.catalogue(plan))
    return plan.source.id
