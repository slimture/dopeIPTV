"""MainWindow mixin: the Recordings place (ui/rec_place.py draws it).

The folders stay in the left column as they were (All recordings, Active
& scheduled, Upcoming, the recordings folder's own folders); the middle
pane shows, where the list was, the shared form of every place: the
folder's or the list's name, how many recordings and what they take on
the disk, the filter (the list's own), ⋯ (Scheduled recordings…, Series
recordings… - Settings > Recording -, New folder…, Open folder, Change
recordings folder…, Clear all finished, Delete all recordings here…) and
Grid / List (remembered, ``rec_view``); "Recording now and next" over All
recordings - what records, its red mark with how long it has run, and what
is to come, soonest first, each with its menu (Stop recording, Edit
start/stop time…, Cancel, Record anyway: the app's own) - then the
recordings as rows or 16:9 cards: a frame of each, else its channel's
logo; its channel, day, length and size; how far it was watched; Watched
once played to its end (or marked so: ``core/rec_view.WatchedMarks``, the
profile's own ``rec_watched``). Active & scheduled and Upcoming show their
recordings to come and done as cards with their state.

The list under it is the source of truth (ui/mw_recording.py, the list's
own build in main_window): the place draws what it holds through
``ListMirror`` (ui/list_mirror.py) - a reset drawn again, the same rows
keeping their cards, scroll and choice - and the list follows the card
picked, so its menus (play elsewhere, rename, edit info, move, delete, the
jobs' own), the detail panel and the pane following what plays work as
they did. A double click or Enter plays a recording (a running one's file
as it grows, as the list did); a recording to come with nothing to play
opens its menu. Nothing starts by itself.

A recording's frame and length are found on the pool, only for the cards
that paint, never for a file being recorded or saved as MP4
(``core/rec_view.probe``), one at a time in small batches, a file that
gives none not asked again this session.

The place answers mw_places' hooks: ``_place_rec_show`` (the sidebar's
entry, Back from a page over it) and ``_place_rec_refresh`` (a profile, a
source, a sync).
"""

from __future__ import annotations

import math
import os
from functools import partial
from pathlib import Path

from PyQt6 import sip
from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtWidgets import QMenu

from ..core import rec_view
from ..core.log import log
from ..core.workers import run_async
from ..i18n import tr
from .list_mirror import ListMirror
from .list_place import PlaceCard, line_of
from .media_cards import is_heading
from .qt_i18n import format_when
from .rec_place import RecPlace
from .series_cards import minutes_text
from .series_page import drawn_icon
from .theme import P

#: The recordings as a grid of 16:9 cards or as rows ("wide" / "list").
VIEW_KEY = "rec_view"
VIEWS = ("wide", "list")
JOBS, UPCOMING = "__jobs__", "__upcoming__"
#: How many recordings are probed at once (one after the other).
PROBE_BATCH = 4
#: The sort menu's words for each order (core/rec_view.ORDERS).
ORDER_WORDS = {rec_view.NEWEST: "rec_sort_newest", rec_view.OLDEST: "rec_sort_oldest",
               rec_view.NAME: "sort_az", rec_view.CHANNEL: "rec_sort_channel",
               rec_view.LONGEST: "rec_sort_longest", rec_view.LARGEST: "rec_sort_largest"}


def size_text(n) -> str:
    from ..core.recording import format_size
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return ""
    return format_size(n) if n > 0 else ""


def day_text(ts: float, now: float | None = None) -> str:
    """When, as a card says it: "Today 21:00", "Yesterday 21:00", "Mon
    21:00" within the week, else the date."""
    day = rec_view.day_of(ts, now)
    clock = format_when(ts, "HH:mm")
    if day == "today":
        return tr("rec_when_today", time=clock)
    if day == "yesterday":
        return tr("rec_when_yesterday", time=clock)
    if day == "later":
        return format_when(ts, "ddd d MMM HH:mm")
    return format_when(ts, "ddd HH:mm" if day == "week" else "d MMM yyyy")


class _PlaceRecMixin:
    """MainWindow mixin: the Recordings place."""

    _rp_page: RecPlace | None = None
    _rp_mirror: ListMirror | None = None
    _rp_marks: rec_view.WatchedMarks | None = None
    _rp_tick_timer: QTimer | None = None
    _rp_probe_timer: QTimer | None = None
    _rp_probing = False

    # -- watched marks ----------------------------------------------------------------

    def _rec_watched(self) -> rec_view.WatchedMarks:
        marks = self._rp_marks
        if marks is None:
            marks = self._rp_marks = rec_view.WatchedMarks(self.settings)
        return marks

    def _rec_auto_watched(self, key) -> None:
        """A recording played to its end (_maybe_auto_mark_watched): Watched
        - never one still being recorded."""
        path = str(key or "")
        if not path or path in self._rp_busy_paths():
            return
        self._rec_watched().mark(path)
        if self._rp_page is not None and not sip.isdeleted(self._rp_page):
            self._rp_page.view.redress()

    def _rec_toggle_watched(self, it: dict) -> None:
        path = str(it.get("_path") or "")
        if not path:
            return
        marks = self._rec_watched()
        if marks.has(path):
            marks.unmark(path)
        else:
            marks.mark(path)
        if self._rp_page is not None and not sip.isdeleted(self._rp_page):
            self._rp_page.view.redress()

    # -- where it is ------------------------------------------------------------------

    def _place_rec_active(self) -> bool:
        """The place is where the list is (it may be under another page)."""
        place, stack = self._rp_page, getattr(self, "_list_stack", None)
        return (place is not None and stack is not None and not sip.isdeleted(place)
                and stack.currentWidget() is place)

    def _place_rec_showing(self) -> bool:
        return (self._place_rec_active()
                and self._mid_stack.currentWidget() is self._list_page)

    def _place_rec_show(self) -> bool:
        """The Recordings place in the middle pane: mw_places' hook for the
        sidebar's entry and for Back from a page over it - the place as it
        was left. True."""
        self._close_guide_page()
        self._close_series_page()
        self._close_search_page()
        if getattr(self, "_focus_mode", False):
            self._set_focus_mode(False)
        if self.mode != "rec":
            self.switch_mode("rec")
        else:
            self._place_rec_sync()
        entered = getattr(self, "_place_entered", None)
        if callable(entered):
            entered("rec")
        may = getattr(self, "_place_series_may_focus", None)
        if self._place_rec_showing() and (may is None or may()):
            self._rp_page.view.setFocus(Qt.FocusReason.OtherFocusReason)
        return True

    def _place_rec_sync(self) -> None:
        """switch_mode: the place where the list is while Recordings is the
        mode, the list itself otherwise."""
        stack = getattr(self, "_list_stack", None)
        if stack is None:
            return
        if self.mode == "rec":
            place = self._ensure_rec_place()
            if stack.currentWidget() is not place:
                stack.setCurrentWidget(place)
            self._rp_mirror.rebuild()
            self._rp_now_refresh()
        elif self._rp_page is not None and stack.currentWidget() is self._rp_page:
            stack.setCurrentWidget(self._list_body)
        self._rp_tick_sync()

    def _ensure_rec_place(self) -> RecPlace:
        place = self._rp_page
        if place is not None and not sip.isdeleted(place):
            return place
        place = RecPlace(self.logos)
        self._rp_page = place
        v = place.view
        v.dress_fn = self._rp_dress
        v.activated.connect(self._rp_activate)
        v.menu.connect(self._rp_menu)
        place.now.strip.click_plays = self._autoplay_preview    # as the lists
        place.now.activated.connect(self._rp_now_activate)
        place.now.menu.connect(self._rp_now_menu)
        # What records now hidden from its heading (mw_place_look); the
        # header compact while Just the list.
        place.now.set_hide_key("rec_now")
        place.now.hide_requested.connect(self._place_row_hide)
        self._place_look_adopt(place)
        place.layout_changed.connect(self._rp_layout_picked)
        place.header.sort_changed.connect(self._rec_sort_picked)
        place.header.sort_toggled.connect(self._rec_unwatched_picked)
        more = QMenu(place.more)
        more.aboutToShow.connect(partial(self._rp_fill_more, more))
        place.more.setMenu(more)
        self._rp_mirror = ListMirror(self, place, self._rp_card_of, self._place_rec_active,
                                     self._rp_context, self._rp_after)
        self.rec.jobs_changed.connect(self._rp_jobs_changed)
        self._mid_stack.currentChanged.connect(self._rp_stack_changed)
        t = QTimer(place)
        t.setInterval(1000)
        t.timeout.connect(self._rp_tick)
        self._rp_tick_timer = t
        p = QTimer(place)
        p.setSingleShot(True)
        p.setInterval(150)
        p.timeout.connect(self._rp_probe_run)
        self._rp_probe_timer = p
        self._list_stack.addWidget(place)
        self._rp_icons()
        return place

    def _rp_icons(self) -> None:
        place = self._rp_page
        if place is None:
            return
        dpr = place.devicePixelRatioF() or 1.0
        place.more.setIcon(drawn_icon("more", 15, P["text2"], False, dpr))

    # -- what the cards are of ------------------------------------------------------------

    def _rp_cat(self):
        return getattr(self, "_current_cat", None)

    def _rp_jobs_view(self) -> bool:
        return self._rp_cat() in (JOBS, UPCOMING)

    def _rp_context(self) -> tuple:
        return (self._rp_cat(), self.search.text().strip())

    def _rp_card_of(self, it: dict) -> PlaceCard | None:
        """A list row's card: a heading's line of its own (a day, a
        channel), none for a recording to come over All recordings (it
        stands in "Recording now and next")."""
        if it.get("_header"):
            if it.get("_pending") and not self._rp_jobs_view():
                return None                 # its rows stand in "Recording now and next"
            title = str(it["_header"])
            return PlaceCard(key="head:" + title, title=title, heading=True)
        if it.get("_kind") == "recjob" and not self._rp_jobs_view():
            return None
        title = str(it.get("name") or "")
        if it.get("_kind") == "recjob":
            title = str((self._rp_job(it) or {}).get("title") or title)
        return PlaceCard(key=str(it.get("_key") or it.get("_path") or ""), title=title,
                         data=it)

    def _rp_job(self, it: dict) -> dict | None:
        jid = it.get("_job")
        return next((j for j in self.rec.jobs if j.get("id") == jid), None) if jid else None

    def _rp_busy_paths(self) -> set[str]:
        """Files being written: recorded now, or saved as MP4."""
        return {str(j.get("path")) for j in getattr(self.rec, "jobs", [])
                if j.get("path") and (j.get("status") == "recording"
                                      or j.get("remux") == "running")}

    def _rp_layout(self) -> tuple[str, str]:
        if self._rp_jobs_view():
            return "list", "wide"
        v = str(self.settings.value(VIEW_KEY, "list") or "list")
        return (v if v in VIEWS else "list"), "wide"

    def _rp_after(self) -> None:
        place = self._rp_page
        if place is None or sip.isdeleted(place):
            return
        place.set_layout(*self._rp_layout())
        self._rp_head()
        self._rp_now_refresh()
        self._rp_mark()

    # -- what a card says -----------------------------------------------------------------

    def _rp_dress(self, card: PlaceCard) -> None:
        it = card.data if isinstance(card.data, dict) else {}
        if it.get("_kind") == "recjob":
            self._rp_dress_job(card, it)
            return
        path = str(it.get("_path") or "")
        busy = self._rp_busy_paths()
        found = self._rp_probed().get(path)
        frame = found.frame if found is not None else ""
        card.title = rec_view.shown_title(str(it.get("name") or card.title))
        card.image = frame or str(it.get("stream_icon") or "")
        card.fit = None if frame else True
        job = next((j for j in self.rec.jobs if j.get("path") == path), None)
        pos, dur = self._rp_resume(path)
        length = rec_view.length_of(found.length if found else 0.0, dur, job)
        parts = [str(it.get("_channel") or (job or {}).get("channel") or "")]
        try:
            when = float(it.get("_start") or 0) or float(it.get("added") or 0)
        except (TypeError, ValueError):
            when = 0.0
        if when:
            parts.append(day_text(when))
        if length > 0 and path not in busy:
            parts.append(minutes_text(max(1, math.ceil(length / 60))))
        parts.append(size_text(it.get("_size")))
        if job is not None and job.get("remux") == "running":
            parts.append(tr("rec_mp4_saving"))
        card.line = line_of(parts)
        pills: list = []
        recording = any(j.get("path") == path and j.get("status") == "recording"
                        for j in self.rec.jobs)
        if recording:
            pills.append(("live", "REC"))
        elif self._rec_watched().has(path):
            pills.append("seen")
        card.pills = tuple(pills)
        card.progress = pos / dur if (pos > 60 and dur > pos and not recording) else None
        if path and path not in busy and found is None:
            self._rp_want_probe(path)

    def _rp_dress_job(self, card: PlaceCard, it: dict) -> None:
        j = self._rp_job(it) or {}
        status = str(j.get("status") or it.get("_status") or "")
        card.title = str(j.get("title") or card.title)
        card.image = str(j.get("stream_icon") or it.get("stream_icon") or "")
        card.fit = True
        card.line = self._rp_job_line(j, it, full=True)
        pill = self._rp_job_pill(j, status, short=False)
        card.pills = (pill,) if pill else ()
        card.progress = None

    def _rp_job_pill(self, j: dict, status: str, short: bool = True):
        """A recording's state as a pill: REC with how long it has run, when
        one to come starts (*short*: on a small card, whose line has no
        room for it) or Scheduled, Done, Failed."""
        import time
        if status == "recording":
            try:
                ran = time.time() - float(j.get("start") or time.time())
            except (TypeError, ValueError):
                ran = 0.0
            return ("live", "REC " + rec_view.elapsed_text(ran))
        if status == "scheduled":
            try:
                start = float(j.get("start") or 0)
            except (TypeError, ValueError):
                start = 0.0
            if not start or not short:
                return ("next", tr("rec_status_scheduled"))
            if rec_view.day_of(start) != "today":
                return ("next", format_when(start, "ddd HH:mm"))
            stop = j.get("stop")
            words = format_when(start, "HH:mm")
            if isinstance(stop, (int, float)) and stop > start:
                words += "–" + format_when(float(stop), "HH:mm")
            return ("next", words)
        if status == "done":
            return ("seen", tr("rec_status_done"))
        if status == "failed":
            return ("fail", "")
        return None

    def _rp_job_line(self, j: dict, it: dict, full: bool = False) -> str:
        """A recording to come (or done) in words: its channel, when (from
        its start, until its stop), why it waits or failed."""
        from .mw_recording import rec_reason
        status = str(j.get("status") or it.get("_status") or "")
        parts = [str(j.get("channel") or "")]
        try:
            start = float(j.get("start") or 0)
        except (TypeError, ValueError):
            start = 0.0
        stop = j.get("stop")
        until = (tr("rec_until", time=format_when(float(stop), "HH:mm"))
                 if isinstance(stop, (int, float)) and stop else tr("rec_until_stopped"))
        if full and start and status != "recording":
            parts.append(day_text(start))
        parts.append(until)
        if status == "cancelled":
            parts.insert(0, tr("rec_status_cancelled"))
        if status == "scheduled" and (j.get("_held") or it.get("_held")):
            parts.append(tr("rec_waiting_room"))
        if status == "failed" and (j.get("error") or it.get("_error")):
            parts.append(rec_reason(str(j.get("error") or it.get("_error"))))
        if j.get("remux") == "running":
            parts.append(tr("rec_mp4_saving"))
        elif j.get("remux") == "failed":
            parts.append(tr("rec_mp4_kept_ts"))
        return line_of(parts)

    def _rp_resume(self, path: str) -> tuple[float, float]:
        try:
            pos, dur, _at = self.resume.progress(path, "recording")
            return float(pos or 0), float(dur or 0)
        except Exception:
            return 0.0, 0.0

    # -- frames and lengths, on the pool -----------------------------------------------------

    def _rp_probed(self) -> dict:
        found = self.__dict__.get("_rp_found")
        if found is None:
            found = self.__dict__["_rp_found"] = {}
        return found

    def _rp_want_probe(self, path: str) -> None:
        want = self.__dict__.setdefault("_rp_wanted", [])
        if path not in want:
            want.append(path)
        if self._rp_probe_timer is not None and not self._rp_probing:
            self._rp_probe_timer.start()

    def _rp_probe_run(self) -> None:
        """A few of the recordings painted: their frame and length found on
        the pool, one after the other."""
        want = self.__dict__.setdefault("_rp_wanted", [])
        found = self._rp_probed()
        busy = self._rp_busy_paths()
        todo: list[str] = []
        while want and len(todo) < PROBE_BATCH:
            p = want.pop(0)
            if p not in found and p not in busy:
                todo.append(p)
        if not todo or self._rp_probing:
            return
        from ..core.workers import default_image_cache_dir
        from ..providers.cast_bridge import ffmpeg_path
        ff = ffmpeg_path() or ""
        tdir = str(default_image_cache_dir("thumbs"))
        self._rp_probing = True

        def job() -> dict:
            out = {}
            for p in todo:
                try:
                    out[p] = rec_view.probe(ff, tdir, p)
                except Exception as e:      # noqa: BLE001 - one file's trouble is that file's
                    log.debug("recordings: no frame for one: %s", e)
                    out[p] = rec_view.Probe()
            return out

        run_async(self.pool, job, self._rp_probed_in,
                  lambda _e: self._rp_probed_in({p: rec_view.Probe() for p in todo}))

    def _rp_probed_in(self, out) -> None:
        self._rp_probing = False
        self._rp_probed().update(out or {})
        if self.__dict__.get("_rp_resort") and not self.__dict__.get("_rp_wanted"):
            # Every length asked for the longest first is in: in order again.
            self.__dict__["_rp_resort"] = False
            if rec_view.order_of(self.settings) == rec_view.LONGEST:
                QTimer.singleShot(0, self._rec_reload)
        place = self._rp_page
        if place is not None and not sip.isdeleted(place) and self._place_rec_active():
            place.view.redress()
        if self.__dict__.get("_rp_wanted") and self._rp_probe_timer is not None:
            self._rp_probe_timer.start()

    # -- the header -----------------------------------------------------------------------

    def _rp_title(self) -> str:
        cat = self._rp_cat()
        if cat == JOBS:
            return tr("rec_active_scheduled")
        if cat == UPCOMING:
            return tr("rec_upcoming")
        if isinstance(cat, str) and cat:
            return cat
        return tr("nav_recordings")

    def _rp_count(self) -> str:
        place = self._rp_page
        cards = [c for c in (place.view.items() if place is not None else [])
                 if not is_heading(c)]
        n = len(cards)
        if not n:
            return ""
        parts = [tr("rec_n_recordings", n=n)]
        if not self._rp_jobs_view():
            total = 0
            for c in cards:
                try:
                    total += int((c.data or {}).get("_size") or 0)
                except (TypeError, ValueError, AttributeError):
                    pass
            parts.append(size_text(total))
        return " · ".join(p for p in parts if p)

    def _rp_head(self) -> None:
        place = self._rp_page
        if place is None or sip.isdeleted(place):
            return
        h = place.header
        h.set_title(self._rp_title())
        h.set_count(self._rp_count())
        files = not self._rp_jobs_view()
        # The order the recordings are in, its button naming it; Unwatched
        # only at the foot of its menu (before the layout's switch: the
        # place weighs the sort's room too).
        opts = [(k, tr(ORDER_WORDS[k])) for k in rec_view.ORDERS] if files else []
        order = rec_view.order_of(self.settings)
        only = rec_view.unwatched_only(self.settings)
        shown = tr(ORDER_WORDS[order]) + (" · " + tr("rec_unwatched") if only else "")
        if opts != h._sort_opts:
            h.set_sort(opts, order, shown)
        else:
            h.set_sort_key(order, shown)
        h.set_sort_toggle(tr("rec_unwatched_only") if files else "", only)
        place.show_layout_switch(files, self._rp_layout()[0] if files else "")
        mirror = self._rp_mirror
        if mirror is not None and place.filter.text() != self.search.text():
            mirror.put_filter(self.search.text())
        hint = self.search.placeholderText() or tr("list_filter_placeholder")
        if place.filter.placeholderText() != hint:
            place.filter.setPlaceholderText(hint)
        if place.view.model_.rowCount():
            place.set_note("")
        elif self.search.text().strip():
            place.set_note(tr("tmdb_no_results"))
        elif self._rp_jobs_view():
            place.set_note(tr("rec_no_upcoming"))
        else:
            place.set_note(tr("rec_place_empty"))

    # -- the order, and Unwatched only -----------------------------------------------------

    def _rec_sort_picked(self, order: str) -> None:
        """An order picked: kept, and the recordings put in it. The longest
        first asks every recording's length, the list put in order again
        once they are known."""
        if order not in rec_view.ORDERS:
            return
        self.settings.setValue(rec_view.ORDER_KEY, order)
        if order == rec_view.LONGEST:
            found = self._rp_probed()
            todo = [str(it.get("_path") or "") for it in self.list_model.items()
                    if isinstance(it, dict) and it.get("_path")]
            todo = [p for p in todo if p not in found and not self._rec_length(p)]
            self.__dict__["_rp_resort"] = bool(todo)
            for p in todo:
                self._rp_want_probe(p)
        self._rec_reload()

    def _rec_unwatched_picked(self, on: bool) -> None:
        self.settings.setValue(rec_view.UNWATCHED_KEY, "true" if on else "false")
        self._rec_reload()

    def _rec_reload(self) -> None:
        if getattr(self, "mode", "") == "rec":
            self._load_items(self._rp_cat())

    def _rec_length(self, path: str) -> float:
        """A recording's length as far as it is known - its probe, the
        length kept beside its frame, where it was played to - else 0."""
        probe = self._rp_probed().get(path)
        probed = float(getattr(probe, "length", 0) or 0) if probe is not None else 0.0
        if not probed:
            from ..core.workers import default_image_cache_dir
            probed = rec_view.known_length(str(default_image_cache_dir("thumbs")), path)
        return rec_view.length_of(probed, self._rp_resume(path)[1])

    def _rec_group_title(self, key: tuple) -> str:
        if key and key[0] == rec_view.BY_CHANNEL:
            return str(key[1]) or tr("rec_no_channel")
        return self._mitt_group_title(key)

    def _rp_layout_picked(self, key: str) -> None:
        if key not in VIEWS:
            return
        self.settings.setValue(VIEW_KEY, key)
        place = self._rp_page
        if place is None:
            return
        cur = place.view.current_item()
        place.set_layout(*self._rp_layout())
        if cur is not None:
            QTimer.singleShot(0, lambda k=cur.key: self._rp_page is not None
                              and self._rp_page.view.focus_key(k, take_focus=False))

    def _rp_fill_more(self, m: QMenu) -> None:
        """⋯: what concerns every recording."""
        m.clear()
        m.addAction(tr("rec_manage_scheduled"), self._show_scheduled_manager)
        m.addAction(tr("rec_series_rules"), self._rp_series_rules)
        m.addSeparator()
        m.addAction(tr("ctx_new_folder"), lambda: self._new_rec_folder())
        m.addAction(tr("btn_open_folder"), self._rp_open_folder)
        m.addAction(tr("rec_change_folder"), self._choose_rec_dir)
        finished = any(j.get("status") not in ("recording", "scheduled") for j in self.rec.jobs)
        files = any(isinstance(c.data, dict) and c.data.get("_kind") == "recording"
                    for c in (self._rp_page.view.items() if self._rp_page else []))
        if finished or files:
            m.addSeparator()
        if finished:
            m.addAction(tr("rec_clear_finished"), self.rec.clear_finished)
        if files and not self._rp_jobs_view():
            m.addAction(tr("rec_delete_all"), self._delete_all_recordings)

    def _rp_series_rules(self) -> None:
        self.open_settings(at_page="recording", at_row=(-1, -1, tr("set_card_series")))

    def _rp_open_folder(self) -> None:
        from ..core.xdg import open_folder
        d = Path(self.rec.directory())
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.info("recordings: the folder is not there: %s", e)
        open_folder(d)

    # -- what records now and next ---------------------------------------------------------

    def _rp_now_cards(self) -> list[PlaceCard]:
        """Over All recordings, while nothing is typed in the filter: what
        records now, then what is to come."""
        if self._rp_cat() is not None or self.search.text().strip():
            return []
        out = []
        for j in rec_view.pending(self.rec.jobs):
            it = self._job_item(j)
            status = str(j.get("status") or "")
            pill = self._rp_job_pill(j, status)
            out.append(PlaceCard(key=it["_key"], title=str(j.get("title") or ""),
                                 line=self._rp_now_line(j), image=str(j.get("stream_icon")
                                                                      or ""),
                                 pills=(pill,) if pill else (), data=it, fit=True))
        return out

    def _rp_now_line(self, j: dict) -> str:
        """A small card's line beside its pill (which says when): the
        channel, until when it records, why one to come waits."""
        parts = [str(j.get("channel") or "")]
        if j.get("status") == "recording":
            stop = j.get("stop")
            parts.append(tr("rec_until", time=format_when(float(stop), "HH:mm"))
                         if isinstance(stop, (int, float)) and stop else tr("rec_until_stopped"))
        elif j.get("_held"):
            parts.append(tr("rec_waiting_room"))
        return line_of(parts)

    def _rp_now_refresh(self) -> None:
        place = self._rp_page
        if place is None or sip.isdeleted(place):
            return
        shown = getattr(self, "_place_row_shown", None)
        cards = self._rp_now_cards() if not callable(shown) or shown("rec_now") else []
        old = place.now.cards()
        if [c.key for c in old] == [c.key for c in cards] and old:
            for a, b in zip(old, cards, strict=True):
                a.title, a.line, a.pills, a.data, a.image = b.title, b.line, b.pills, b.data, b.image
            place.now.strip.viewport().update()
        else:
            place.now.set_cards(cards)
        self._rp_tick_sync()

    def _rp_tick_sync(self) -> None:
        t = self._rp_tick_timer
        if t is None:
            return
        place = self._rp_page
        on = (place is not None and not sip.isdeleted(place) and self._place_rec_showing()
              and any(j.get("status") == "recording" for j in self.rec.jobs))
        if on and not t.isActive():
            t.start()
        elif not on and t.isActive():
            t.stop()

    def _rp_tick(self) -> None:
        """Once a second while something records: how long it has run."""
        place = self._rp_page
        if place is None or sip.isdeleted(place) or not self._place_rec_showing():
            self._rp_tick_sync()
            return
        for card in place.now.cards():
            j = self._rp_job(card.data or {})
            if j is not None and j.get("status") == "recording":
                pill = self._rp_job_pill(j, "recording")
                card.pills = (pill,) if pill else ()
        place.now.strip.viewport().update()
        if self._rp_jobs_view():
            place.view.redress()

    def _rp_jobs_changed(self) -> None:
        """A recording began, ended or was scheduled: the row over All
        recordings again (the list itself is drawn again by the window)."""
        if self._place_rec_active():
            self._rp_now_refresh()

    # -- what the cards do -------------------------------------------------------------------

    def _rp_activate(self, card) -> None:
        """A double click, or Enter: a recording plays (the list's own
        path); one to come with nothing to play opens its menu."""
        it = getattr(card, "data", None)
        if not isinstance(it, dict):
            return
        if self._rp_mirror is not None:
            self._rp_mirror.settle()
        path = str(it.get("_path") or "")
        if it.get("_kind") == "recjob" and not (path and os.path.exists(path)):
            place = self._rp_page
            row = place.view.row_of(card.key) if place is not None else -1
            if row >= 0:
                r = place.view.visualRect(place.view.model_.index(row, 0))
                self._rp_menu(card, place.view.viewport().mapToGlobal(r.center()))
            return
        with self._from_pane():
            self.play_item(it)

    def _rp_menu(self, card, pos: QPoint) -> None:
        """A card's menu: the list's own for its row - for every card
        selected with it."""
        it = getattr(card, "data", None)
        if not isinstance(it, dict):
            return
        if self._rp_mirror is not None:
            self._rp_mirror.settle()
        with self._from_pane():
            self._rec_context_menu(None, it, at=pos)

    def _rp_now_activate(self, card) -> None:
        it = getattr(card, "data", None)
        if not isinstance(it, dict):
            return
        path = str(it.get("_path") or "")
        if path and os.path.exists(path):
            with self._from_pane():
                self.play_item(it)
            return
        strip = self._rp_page.now.strip if self._rp_page is not None else None
        if strip is not None:
            row = strip.row_of(card.key)
            if row >= 0:
                r = strip.visualRect(strip.model_.index(row, 0))
                self._rp_now_menu(card, strip.viewport().mapToGlobal(r.center()))

    def _rp_now_menu(self, card, pos: QPoint) -> None:
        it = getattr(card, "data", None)
        if isinstance(it, dict):
            with self._from_pane():
                self._rec_context_menu(None, it, at=pos)

    # -- what plays -------------------------------------------------------------------------

    def _rp_mark(self) -> None:
        place = self._rp_page
        if place is None or sip.isdeleted(place):
            return
        key = self._now_key("rec")          # here or on a TV
        place.view.set_playing(key)

    def _place_rec_playing(self) -> None:
        """_start_playback and Stop: what plays marked; how far, as the
        resume points are now."""
        if self._rp_page is None:
            return
        self._rp_mark()
        if self._place_rec_active():
            self._rp_page.view.redress()

    def _rp_stack_changed(self, _i: int) -> None:
        if self._place_rec_showing():
            self._rp_now_refresh()
            self._rp_mark()
            self._rp_page.view.redress()
        self._rp_tick_sync()

    # -- the window around it -----------------------------------------------------------------

    def _place_rec_restyle(self) -> None:
        """A theme or a language: drawn again in it."""
        place = self._rp_page
        if place is None or sip.isdeleted(place):
            return
        place.restyle()
        place.retranslate()
        self._rp_icons()
        if self._place_rec_active():
            self._rp_head()
            self._rp_now_refresh()
            place.view.redress()

    def _place_rec_refresh(self) -> None:
        """mw_places: what the place shows changed (a profile, a source, a
        sync) - the watched marks read again, the cards redressed."""
        if self._rp_marks is not None:
            self._rp_marks.reload()
        place = self._rp_page
        if place is None or sip.isdeleted(place) or not self._place_rec_active():
            return
        place.view.redress()
        self._rp_now_refresh()
