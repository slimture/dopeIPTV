"""MainWindow mixin: Every source - the TV place's one channel list across
every source (core/every_source.py lays it out; services/search_sources.py
reads the sources - the Search page's own reader, so a source's lists are
asked for once a session, whichever of the two asks first).

With two sources of one's own or more the TV place's category column has
"Every source" after All (``_xs_category_item``). Picked, the list holds
every source's live channels as that source's own lists show them - none
hidden by hand, of a hidden category or of a locked one while the lock is
shut, a renamed one by its own name, and for a kids profile nothing for
adults and only what a grown-up allows of that source (``Plan.allowed``) -
under the source's name and how many it has, the sources in their order:

- Read off the window's thread (the Search page's workers): the source in
  use through the window's own client, every other through the search's
  client of it, a portal only as far as its lists were opened (its heading
  says so), a source that cannot be read left out with a word in its
  heading. A source that comes in is put in at its place, without a reset:
  the selection and the scroll stay (ChannelListModel.insert_items).
- What is on: the source in use's rows from the window's guide in memory,
  another's from that source's guide on disk (``now_table``, read with
  load_cached) - never a download for this. Another source's row picked
  shows its programmes under the video at that source's own guide offset
  (Settings', and with Nerd the source's own shift: ``_xs_delay``), their
  descriptions read for that row alone once it rests there
  (``_xs_listings``, Sources.texts).
- A row of the source in use is that source's own row: it plays, has its
  menu and its star as in any of its lists. Another source's
  (core/every_source.wrap: no id of the source in use's) plays from that
  source without switching to it - as the Search page plays one
  (mw_search_all._play_channel_elsewhere): its address asked for only now,
  off the window's thread, then the window's usual path with its questions,
  counted against that source's own account, the status line saying where
  it is from. The list stays (``from_pane``), the row marked playing. Its
  menu: Play, its own source's favourite, Switch to this source (the
  window's switch, then the channel picked in that source's own list:
  ``_xs_switch_to``), where it is from.
- The filter typed, the category's own order, Compact / Airy, a grid of
  tiles (no headings there), the arrows, Enter, numbers typed and channel
  up and down work as on any channel list; the Guide over it covers the
  source in use's channels.

Nothing is read or opened until the view is picked, and nothing streams
until a row is played.
"""

from __future__ import annotations

import time
from functools import partial
from typing import Any

from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtWidgets import QListWidgetItem, QMenu

from ..core import every_source as es
from ..core import search_all as sa
from ..core.log import log
from ..core.stores import FAV_DEFAULT_GROUP, FavoriteStore
from ..core.workers import run_async
from ..i18n import tr
from ..services import search_sources as ss
from .mw_pane_follow import from_pane
from .notices import hub_of

#: Why a source's channels are here only in part, as its heading says it.
_PARTLY = {sa.PAGED: "sa_partly_paged", sa.FAILED: "sa_partly_failed"}
#: The category row's mark before its name (as Recently added has one).
_MARK = "📺"


def _sources_group(n: int) -> str:
    return tr("notice_reading_sources", n=n)


class _EverySourceMixin:
    """MainWindow mixin: Every source."""

    #: Per source id: (the catalogue, whether it was the source in use,
    #: its Section) - laid out again only when one of the two changes.
    _xs_sections: dict | None = None
    #: Other sources' guides around now, by source id.
    _xs_tables: dict | None = None
    _xs_loading: set | None = None
    #: The rows the model holds while the view shows, and each source's
    #: block in them - a source that comes in is put in at its place.
    _xs_shown: list | None = None
    _xs_blocks: dict | None = None
    #: Who the rows were laid out for (the profile, kids, the lock, every
    #: source's rules): rows made for another are not played.
    _xs_made_for: tuple | None = None
    #: Other sources' favourites, read once for the view.
    _xs_favs: dict | None = None
    #: The sources as the view was laid out for (_xs_sources_sig).
    _xs_laid_for: tuple | None = None

    # -- where it is ------------------------------------------------------------------

    def _xs_offered(self) -> bool:
        store = getattr(self, "playlist_store", None)
        return store is not None and es.offered(store.playlists())

    def _xs_category_item(self) -> QListWidgetItem | None:
        """The category column's "Every source" row - with two sources of
        one's own or more, else None."""
        if not self._xs_offered():
            return None
        item = QListWidgetItem(f"{_MARK}  " + tr("cat_every_source"))
        item.setData(Qt.ItemDataRole.UserRole, es.CATEGORY)
        return item

    def _xs_showing(self) -> bool:
        """The middle list is Every source's."""
        return (getattr(self, "mode", "") == "live" and not getattr(self, "series_ctx", None)
                and getattr(self, "_current_cat", None) == es.CATEGORY)

    def _xs_plans(self) -> list[ss.Plan]:
        """Every source of one's own as the window has it now (the Search
        page's plans; the demo's channels are no source)."""
        return [p for p in self._sa_plans() if p.source.id != "-"]

    def _xs_active_id(self) -> str:
        store = getattr(self, "playlist_store", None)
        return str(getattr(store, "active_id", "") or "")

    # -- picked: shown and read ---------------------------------------------------------

    def _xs_load(self) -> None:
        """The view picked (_load_items): what is in shown at once, the rest
        read off the window's thread."""
        if self._xs_loading is None:
            self._xs_loading = set()
        if self._xs_sections is None:
            self._xs_sections = {}
        if self._xs_tables is None:
            self._xs_tables = {}
        self._xs_favs = {}
        self._xs_laid_for = self._xs_sources_sig()
        store = self._sa_store()
        store.forget(p.source.id for p in self._sa_plans())     # removed ones
        plans = self._xs_plans()
        self._xs_shown = None
        self._xs_render()
        if any(store.channels_ready(p) is None for p in plans):
            self._show_busy(tr("status_loading_channels"))
        self._xs_prepare(plans)

    def _xs_prepare(self, plans: list[ss.Plan]) -> None:
        """Each source's channels, then another source's guide around now,
        read off the window's thread - once: those not in yet, not being
        read, not given up on."""
        loading = self._xs_loading if self._xs_loading is not None else set()
        self._xs_loading = loading
        gave_up = self.__dict__.setdefault("_xs_gave_up", set())
        store = self._sa_store()
        for plan in plans:
            pid = plan.source.id
            if pid in loading or (pid, plan.key) in gave_up:
                continue
            cat = store.channels_ready(plan)
            # Another source's guide offset: what is on in its own time.
            behind = max(0, self._xs_delay(plan.playlist)) * 60.0 if plan.client is None else 0.0
            if cat is None:
                job, done = partial(_read_channels, store, plan), partial(self._xs_lists_in, pid)
            elif plan.client is None and not store.now_looked_for(plan, behind=behind):
                job = partial(_read_now, store, plan, cat, behind)
                done = partial(self._xs_guide_in, pid)
            else:
                continue
            loading.add(pid)
            hub_of(self).start(tr("notice_reading_source", name=plan.source.name),
                               key="xs:" + pid, kind="search", group="sa_sources",
                               group_text=_sources_group, timeout_ms=5 * 60_000)
            run_async(self._sa_pool(0), job, done,
                      partial(self._xs_read_failed, pid, plan.key))

    def _xs_done_reading(self, pid: str) -> None:
        hub_of(self).end_key("xs:" + pid)
        if self._xs_loading is not None:
            self._xs_loading.discard(pid)

    def _xs_lists_in(self, pid: str, _got: Any = None) -> None:
        """A source's channels in: put in at its place while the view shows
        (away from it nothing more is read; coming back reads on)."""
        self._xs_done_reading(pid)
        if not self._xs_showing():
            return
        try:
            self._xs_render(arrived=pid)
        except Exception:
            log.exception("every source: a source's channels not shown")
        self._xs_prepare(self._xs_plans())
        if not self._xs_loading:
            self._hide_busy()

    def _xs_guide_in(self, pid: str, _got: Any = None) -> None:
        """Another source's guide around now in: its rows' now and next."""
        self._xs_done_reading(pid)
        plan = next((p for p in self._xs_plans() if p.source.id == pid), None)
        table = self._sa_store().now_in(plan) if plan is not None else None
        if self._xs_tables is not None:
            if table is not None:
                table.delay_minutes = self._xs_delay(plan.playlist if plan else None)
                self._xs_tables[pid] = table
            else:
                self._xs_tables.pop(pid, None)
        if not self._xs_showing():
            return
        self.delegate.forget_guide()
        self.listw.viewport().update()
        cur = self.list_model.item_at(self.listw.currentIndex().row())
        if cur and cur.get("_xs") == pid:
            self._xs_listings(cur)
        self._xs_prepare(self._xs_plans())

    def _xs_read_failed(self, pid: str, key: tuple, _err: Any = None) -> None:
        log.info("every source: a source was not read")
        self.__dict__.setdefault("_xs_gave_up", set()).add((pid, key))
        self._xs_lists_in(pid)

    def _xs_delay(self, playlist: Any) -> int:
        """Another source's guide offset: Settings' - and with Nerd its own."""
        try:
            base = int(self.settings.value("epg_delay_min", 0))
        except (TypeError, ValueError):
            base = 0
        from ..core import nerd
        if nerd.current().on and isinstance(playlist, dict):
            try:
                base += int(float((playlist.get("options") or {}).get("epg_shift") or 0))
            except (TypeError, ValueError, AttributeError):
                pass
        return base

    # -- laid out -----------------------------------------------------------------------

    def _xs_section(self, plan: ss.Plan, cat: sa.Catalogue, active: str) -> es.Section:
        cache = self._xs_sections if self._xs_sections is not None else {}
        self._xs_sections = cache
        here = plan.source.id == active
        got = cache.get(plan.source.id)
        if got is not None and got[0] is cat and got[1] == here:
            return got[2]
        sec = es.section_of(cat, here)
        cache[plan.source.id] = (cat, here, sec)
        return sec

    def _xs_render(self, arrived: str | None = None) -> None:
        """The list as it is now: every source in that is in, each under its
        heading, filtered and in the category's order. *arrived*: the source
        that just came in, put in at its place when the rest is as it was."""
        if not self._xs_showing():
            return
        store = self._sa_store()
        active = self._xs_active_id()
        tables = self._xs_tables if self._xs_tables is not None else {}
        self._xs_tables = tables
        sections: list[es.Section] = []
        for plan in self._xs_plans():
            cat = store.channels_ready(plan)
            if cat is None:
                continue
            sections.append(self._xs_section(plan, cat, active))
            if plan.source.id != active and plan.source.id not in tables:
                table = store.now_in(plan)
                if table is not None:
                    table.delay_minutes = self._xs_delay(plan.playlist)
                    tables[plan.source.id] = table
        self._xs_made_for = self._sa_made_key()
        text = self.search.text().lower().strip()
        name = self._display_name_fn()
        hidden = self.channel_ov.anything_hidden()

        def keep(sec: es.Section, rows: list[dict]) -> list[dict]:
            if sec.here and hidden:
                # Hidden by hand since it was read (its menu): out at once.
                rows = [r for r in rows if not self._channel_hidden(r, "live")]
            if text:
                rows = [r for r in rows if text in name(r).lower()]
            return self._sorted(rows)

        rows, blocks, total = es.arrange(sections, keep, self._xs_heading_text,
                                         headings=not self._grid_on(), filtered=bool(text))
        self.all_items = [r for sec in sections for r in sec.rows]
        model = self.list_model
        prev, before = self._xs_shown, self._xs_blocks or {}
        put_in = False
        if (arrived is not None and prev is not None and model.items() is prev
                and arrived not in before and arrived in blocks):
            at, n = blocks[arrived]
            if len(prev) + n == len(rows) and prev[:at] == rows[:at] and prev[at:] == rows[at + n:]:
                model.insert_items(at, rows[at:at + n])
                put_in = True
        if not put_in:
            model.set_items(rows, "live")
        self._xs_shown = model.items()
        self._xs_blocks = blocks
        self._set_list_status(self._xs_status_text(total, sections))
        if getattr(self, "_pending_jump_key", None) is not None \
                and self._try_select_key(self._pending_jump_key):
            self._pending_jump_key = None
        if not self._xs_loading:
            self._hide_busy()
        shown = getattr(self, "_place_tv_list_shown", None)
        if shown is not None:
            shown()
        follow = getattr(self, "_guide_follow_list", None)
        if follow is not None:
            follow()

    def _xs_filter_hook(self) -> bool:
        """_apply_filter: the filter typed, the order picked, a grid or rows
        - laid out again here while the view shows."""
        if not self._xs_showing():
            return False
        try:
            self._xs_render()
        except Exception:
            log.exception("every source: the list not laid out")
        return True

    def _xs_heading_text(self, sec: es.Section, n: int) -> str:
        """A source's heading: "Pirat · 120 channels" - and what it holds
        only in part, or does not hold, said."""
        name = sec.source.name or "?"
        if sec.partial in _PARTLY:
            name = tr("sa_partly_item", source=name, why=tr(_PARTLY[sec.partial]))
        return tr("status_list_source", source=name, text=self._count_text("live", n)) if n else name

    def _xs_status_text(self, total: int, sections: list[es.Section]) -> str:
        """The status line: how many channels - and the sources read only in
        part (a grid of tiles has no headings to say it)."""
        words = [self._count_text("live", total)]
        words += [tr("sa_partly_item", source=sec.source.name or "?", why=tr(_PARTLY[sec.partial]))
                  for sec in sections if sec.partial in _PARTLY]
        return " · ".join(words)

    def _xs_own_rows(self, rows: Any) -> list:
        """*rows* the Guide over the list covers: the source in use's channels
        alone while Every source shows (the guide is that source's)."""
        rows = list(rows or [])
        if not self._xs_showing():
            return rows
        return [r for r in rows if not r.get("_header") and not r.get("_xs")]

    # -- what is on -----------------------------------------------------------------------

    def _guide_for_row(self, it: Any) -> Any:
        """The guide a row's now and next is drawn from: another source's
        own around now (None without one), else the window's in memory."""
        pid = it.get("_xs") if isinstance(it, dict) else None
        if pid:
            return (self._xs_tables or {}).get(pid)
        return getattr(self, "xmltv", None)

    def _xs_detail(self, it: dict) -> None:
        """Another source's row picked: its programmes under the video from
        its guide around now - and, with autoplay, it plays as any row
        does."""
        self._xs_listings(it)
        if (self.player and self._autoplay_preview()
                and self.playback_mode() == "embedded"
                and not self._rmb_selecting
                and not self._extending_selection()):
            self._preview_timer.start(350)

    def _xs_delay_for(self, pid: str) -> int:
        """Source *pid*'s guide offset as it is now (_xs_delay)."""
        store = getattr(self, "playlist_store", None)
        return self._xs_delay(store.get(pid) if store is not None and pid else None)

    def _xs_listings(self, it: dict) -> None:
        """Another source's row's programmes under the video, at that
        source's own guide offset: at once as its guide around now has them
        (their titles), then - for the row picked alone, once it rests there
        (XS_TEXTS_MS) - with their descriptions, read from that source's
        guide on disk on the search's workers (Sources.texts: never a
        download, only that row's kept)."""
        pid, key = str(it.get("_xs") or ""), str(it.get("_key") or "")
        table = self._guide_for_row(it)
        delay = self._xs_delay_for(pid)
        if table is not None:
            table.delay_minutes = delay
        texts = self._sa_store().texts_in(pid, key, delay) if pid and key else None
        if texts:
            listings = texts
        else:
            listings = table.listings(it, time.time() - delay * 60) if table is not None else []
            if listings and texts is None:
                self._xs_texts_soon(it)
        self._show_epg(listings, self._item_key(it), delay)

    #: How long a row of another source stays picked before its programmes'
    #: descriptions are read (the arrows run through rows faster than that).
    XS_TEXTS_MS = 300

    def _xs_texts_soon(self, it: dict) -> None:
        self._xs_texts_want = it
        timer = self.__dict__.get("_xs_texts_timer")
        if timer is None:
            timer = QTimer(self if isinstance(self, QObject) else None)
            timer.setSingleShot(True)
            timer.setInterval(self.XS_TEXTS_MS)
            timer.timeout.connect(self._xs_texts_read)
            self._xs_texts_timer = timer
        timer.start()

    def _xs_current(self) -> dict | None:
        try:
            return self.list_model.item_at(self.listw.currentIndex().row())
        except Exception:
            return None

    def _xs_texts_read(self) -> None:
        """The row still picked: its programmes with their descriptions,
        read off the window's thread."""
        it = self.__dict__.pop("_xs_texts_want", None)
        if it is None or not self._xs_showing():
            return
        cur = self._xs_current()
        pid, key = str(it.get("_xs") or ""), str(it.get("_key") or "")
        if not cur or str(cur.get("_key") or "") != key or str(cur.get("_xs") or "") != pid:
            return                          # another row picked since
        row = self._xs_original(it)
        if row is None:
            return
        delay = self._xs_delay_for(pid)
        run_async(self._sa_pool(0), partial(self._sa_store().texts, pid, dict(row), key, delay),
                  partial(self._xs_texts_in, pid, key), partial(self._xs_texts_failed, pid))

    def _xs_texts_in(self, pid: str, key: str, _listings: Any = None) -> None:
        if not self._xs_showing():
            return
        cur = self._xs_current()
        if not cur or str(cur.get("_key") or "") != key or str(cur.get("_xs") or "") != pid \
                or self._current_key != self._item_key(cur):
            return
        self._xs_listings(cur)

    def _xs_texts_failed(self, _pid: str, _err: Any = None) -> None:
        log.info("every source: a programme's words were not read")

    # -- played --------------------------------------------------------------------------

    def _xs_original(self, it: dict) -> dict | None:
        got = (self._xs_sections or {}).get(str(it.get("_xs") or ""))
        return got[2].originals.get(str(it.get("_key") or "")) if got is not None else None

    @from_pane
    def _xs_play(self, it: dict) -> None:
        """Another source's row played - from that source, the source in use
        left as it is (as the Search page plays one); the list stays."""
        row = self._xs_original(it)
        if row is None:
            return
        if self._xs_made_for is not None and self._xs_made_for != self._sa_made_key():
            # Laid out for another profile, the lock as it was or a rule
            # changed since: laid out anew, nothing played.
            log.info("every source: the list was laid out for others - laid out anew")
            self._xs_render()
            return
        pid = str(it.get("_xs") or "")
        store = getattr(self, "playlist_store", None)
        if pid == self._xs_active_id():
            self.play_live_channel(row)         # switched to it meanwhile
            return
        pl = store.get(pid) if store is not None else None
        if pl is None:
            self._show_toast(tr("sa_source_gone", source=str(it.get("_xs_name") or "")), 4000)
            return
        log.info("every source: a channel of another source played")
        self._play_channel_elsewhere(pl, row)

    def _other_source_listed(self, pl: Any, row: Any) -> bool:
        """Whether channel *row* of source *pl* is one that source's own
        lists hold, as they were read for whoever uses the window now - the
        Search page's and Every source's catalogue (a kids profile's door,
        mw_profiles._kids_may_play). False while none was read."""
        store = getattr(self, "_sa_sources", None)
        if store is None or not isinstance(pl, dict) or not isinstance(row, dict):
            return False
        pid, sid = str(pl.get("id") or ""), row.get("stream_id")
        if not pid or sid is None:
            return False
        plan = next((p for p in self._sa_plans() if p.source.id == pid), None)
        cat = store.channels_ready(plan) if plan is not None else None
        return cat is not None and any(str(r.get("stream_id")) == str(sid) for r in cat.channels)

    # -- its menu and its star ----------------------------------------------------------

    def _xs_menu(self, it: dict, at: Any) -> None:
        """Another source's row's menu: Play, its own source's favourite,
        Switch to this source, where it is from."""
        m = QMenu(self)
        m.addAction(tr("btn_play"), partial(self._xs_play, it))
        if self._xs_original(it) is not None:
            on = self._xs_is_favorite(it)
            m.addAction(tr("ctx_remove_from_favorites" if on else "ctx_add_to_favorites"),
                        partial(self._xs_toggle_favorite, it))
            m.addSeparator()
            m.addAction(tr("xs_switch_source"), partial(self._xs_switch_to, it))
        m.addSeparator()
        note = m.addAction(tr("sa_from_source", source=str(it.get("_xs_name") or "")))
        note.setEnabled(False)
        m.exec(at)
        m.deleteLater()

    def _xs_switch_to(self, it: dict) -> None:
        """Another source's row's own source in use from now on - the
        window's switch, as the source menu makes it -, then the channel
        picked in that source's list, in its category, once the list holds
        it (as a jump to what plays lands: _pending_jump_key). Nothing is
        played."""
        row = self._xs_original(it)
        pid = str(it.get("_xs") or "")
        store = getattr(self, "playlist_store", None)
        if row is None or not pid or store is None:
            return
        if store.get(pid) is None:
            self._show_toast(tr("sa_source_gone", source=str(it.get("_xs_name") or "")), 4000)
            return
        if pid == self._xs_active_id():
            self._reveal_channel_in_list(row)      # in use already (switched meanwhile)
            return
        log.info("every source: switching to a row's own source")
        self.switch_playlist(pid, then=partial(self._xs_switched, pid, dict(row)))

    def _xs_switched(self, pid: str, row: dict) -> None:
        """Switched to *row*'s source: its row picked once that source's
        categories and list are in (the switch reads them anew) - nothing
        is done to the list as it is now."""
        if self._xs_active_id() != pid or row.get("stream_id") is None:
            return
        self._pending_jump_key = row.get("stream_id")
        self._pending_jump_cat = row.get("category_id")
        QTimer.singleShot(8000, self._clear_pending_jump)
        if getattr(self, "mode", "") != "live":
            self.switch_mode("live")       # its categories honour the jump

    def _xs_fav_store(self, pid: str) -> FavoriteStore:
        """Source *pid*'s channel favourites, this profile's."""
        favs = self._xs_favs if self._xs_favs is not None else {}
        self._xs_favs = favs
        store = favs.get(pid)
        if store is None:
            store = favs[pid] = FavoriteStore(self._own(self.settings), f"favorites_{pid}")
        return store

    def _xs_is_favorite(self, it: dict) -> bool:
        """Another source's row's star: whether it is among that source's
        own favourites."""
        row = self._xs_original(it)
        pid = str(it.get("_xs") or "")
        if row is None or not pid or row.get("stream_id") is None:
            return False
        try:
            return self._xs_fav_store(pid).is_favorite(row.get("stream_id"))
        except Exception:
            return False

    def _xs_toggle_favorite(self, it: dict) -> None:
        """Another source's channel into its own favourites, or out of them."""
        row = self._xs_original(it)
        pid = str(it.get("_xs") or "")
        if row is None or not pid or row.get("stream_id") is None or pid == self._xs_active_id():
            return
        store = self._xs_fav_store(pid)
        sid = row.get("stream_id")
        if store.is_favorite(sid):
            store.remove(sid)
        else:
            keep = {k: row.get(k) for k in ("stream_id", "name", "stream_icon", "num",
                                            "epg_channel_id", "category_id", "tv_archive",
                                            "tv_archive_duration") if row.get(k) is not None}
            store.add(FAV_DEFAULT_GROUP, keep)
        self.list_model.refresh_all()

    # -- the sources changed ---------------------------------------------------------------

    def _xs_sources_changed(self) -> None:
        """A source added, removed, edited, turned on or off (the avatar
        shows it): the entry offered or taken away, the view laid out anew."""
        cats = getattr(self, "cat_list", None)
        if cats is None or getattr(self, "mode", "") != "live" or not cats.count():
            return
        try:
            at = next((i for i in range(cats.count())
                       if cats.item(i).data(Qt.ItemDataRole.UserRole) == es.CATEGORY), -1)
            offered = self._xs_offered()
            first = cats.item(0)
            if offered and at < 0:
                # Under TV's All - the column as its categories are, not a
                # category search's results; nothing else moves.
                if (first is not None and first.data(Qt.ItemDataRole.UserRole) is None
                        and first.text() == tr("cat_all")):
                    item = self._xs_category_item()
                    if item is not None:
                        cats.blockSignals(True)
                        cats.insertItem(1, item)
                        cats.blockSignals(False)
            elif not offered and at >= 0:
                showing = self._xs_showing()
                cats.blockSignals(True)
                cats.takeItem(at)
                cats.blockSignals(False)
                if showing:
                    cats.setCurrentRow(0)       # its list gives way to All's
            elif self._xs_showing() and self._xs_sources_sig() != self._xs_laid_for:
                # (The avatar is drawn again for a theme or a language too:
                # only a change of the sources lays the list out anew.)
                self._xs_load()
        except Exception:
            log.exception("every source: not brought up to date")

    def _xs_sources_sig(self) -> tuple:
        """The sources as the view reads them: which, and as what."""
        store = getattr(self, "playlist_store", None)
        pls = ss.searchable(store.playlists()) if store is not None else []
        return (self._xs_active_id(),
                tuple((str(p.get("id")), ss.signature(p), str(p.get("name") or "")) for p in pls))


def _read_channels(store: ss.Sources, plan: ss.Plan) -> str:
    """(a worker) A source's channels."""
    store.channels(plan)
    return plan.source.id


def _read_now(store: ss.Sources, plan: ss.Plan, cat: sa.Catalogue, behind: float = 0.0) -> str:
    """(a worker) Another source's guide around now, from its copy on disk
    (*behind*: how far its own guide shift puts it behind now)."""
    store.now_table(plan, cat, behind=behind)
    return plan.source.id
