"""MainWindow mixin: downloads in the window (core/downloads.py keeps them,
ui/downloads_place.py draws their place).

* **Asking for one**: "Download" on a film's and an episode's menu (the
  list's, the series page's, the Movies place's - it is the list's menu),
  "Download this season" and "Download all episodes" on a series page's ⋯
  and "Download all episodes" on a series' row; the detail column's own
  button under the poster. Never while viewing privately; for a kids profile
  only what its lists show (``_dl_allowed``). A download takes a connection
  to the provider: with something else of the app's on the account and its
  limit full or not known, the person is asked first - now anyway, or once
  the connection is free (``_dl_ask_room``).
* **Giving way**: a download yields to everything else of the app's on its
  account - the player, a recording, a multiview cell, a cast, a test,
  server mode - whenever the account's limit is full or not known: it
  stops before their connection opens where the app knows of it
  (``_dl_before_play`` from the player's guard, ``_dl_make_room`` from
  mw_connections and a recording coming due) and is held while they run
  (``_dl_hold_reason``, every second), then goes on by itself. One the
  person let run beside them (``Job.go_ahead``) counts as a connection of
  its own (``_dl_uses``, kind "download").
* **What it shows**: the job in the notices (ui/notices.py: its progress,
  Cancel, a click opens the place), a word when one is done and a message
  when one failed (why, in the person's words: ``WHY_KEYS``); the
  Downloaded pill on the Movies and Series places' cards, a series page's
  episodes and Mine's films (``_dl_pills``); "Downloaded · 1.4 GB" with
  Delete download under the poster (``_dl_detail_show``); the Downloaded
  place (``_place_dl_show``): what downloads and waits on top, then the
  films and the series with their downloaded episodes, sort, All / Movies
  / Series, Grid / List.
* **Playing one**: ``_dl_start`` stands where a film or an episode of the
  source in use starts (the list, the Movies place, a series page, Mine's
  Continue, the next episode): a downloaded one plays its file through
  the window's one playback path as that same title - its kind and key, so
  resume points, watched marks and Continue are the title's own - and
  History keeps the source's address of it, never the file's path. The
  VPN guard lets a file of this computer's play (core/nerd.is_own_file). A
  download of another source plays as a file of its own until offline mode
  can play it as that source's title (its resume point by its path: never
  filed under the source in use's ids).
* **Hidden**: a kids profile sees only the source in use's downloads its
  lists would show (``KidsFilter``); a locked category's while the lock is
  shut, as the lists leave its rows out (core/stores.lock_hides).
"""

from __future__ import annotations

import os
import time
from functools import partial
from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QWidget

from ..core import connections, offline, picture_files, places, privacy
from ..core import downloads as dl
from ..core.connections import Use
from ..core.log import log
from ..core.privacy import for_log
from ..core.workers import run_async
from ..i18n import tr
from .downloads_place import SECTIONS, DownloadsPlace
from .list_place import PlaceCard, line_of
from .media_cards import Card
from .notices import hub_of

#: The place's posters or rows, and its order - this computer's.
VIEW_KEY = "downloads_view"
VIEWS = ("poster", "list")
SORT_KEY = "downloads_sort"
NEWEST, NAME, LARGEST = "newest", "name", "largest"
SORT_WORDS = {NEWEST: "rec_sort_newest", NAME: "sort_az", LARGEST: "rec_sort_largest"}
TICK_MS = 1000
#: The beat while nothing is queued, the folder read and nothing to save:
#: a timer a second for nothing kept a laptop awake (tests/test_perf_idle.py).
#: Whatever queues or resumes one beats at once (_dl_tick), so no one waits.
IDLE_TICK_MS = 30_000
#: For this long after the window opens nothing downloads: the app's own
#: start (the channel the player opens again) comes first.
START_GRACE_S = 15.0
#: Why a job failed, in the person's words.
WHY_KEYS = {dl.WHY_SOURCE: "dl_why_source", dl.WHY_ADDRESS: "dl_why_address",
            dl.WHY_SCHEME: "dl_why_address", dl.WHY_NOT_VIDEO: "dl_why_not_video",
            dl.WHY_STATUS: "dl_why_status", dl.WHY_DROPS: "dl_why_drops",
            dl.WHY_RESUME: "dl_why_drops", dl.WHY_CAP: "dl_why_cap", dl.WHY_DISK: "dl_why_disk",
            dl.WHY_FOLDER: "dl_why_folder", dl.WHY_FFMPEG: "dl_why_ffmpeg",
            dl.WHY_CONVERT: "dl_why_ffmpeg", dl.WHY_PRIVATE_WAY: "dl_why_ffmpeg",
            dl.WHY_CERT: "dl_why_certificate", dl.WHY_REFUSED: "dl_why_refused",
            dl.WHY_OTHER: "dl_why_other"}
#: How a waiting job's hold is said.
HOLD_KEYS = {"connection": "dl_state_wait_conn", "private": "dl_state_wait_private",
             "starting": "dl_state_waiting", "offline": "dl_state_wait_offline"}


def size_text(n: Any) -> str:
    from ..core.recording import format_size
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return ""
    return format_size(n) if n > 0 else ""


def _resolve_with(sources: dict, clients: dict, job: dl.Job) -> tuple[str, dict | None]:
    """(the downloads' worker) The job's address from its source's client -
    made once per source and settings, off the window's thread - and the
    source as core/nerd.set_source keeps one (its route, headers, proxy)."""
    pl = sources.get(job.source)
    if not isinstance(pl, dict):
        raise dl.Unavailable(job.source)
    sig = repr(sorted((k, repr(v)) for k, v in pl.items()))
    got = clients.get(job.source)
    if got is None or got[0] != sig:
        from ..providers.client import make_client
        got = clients[job.source] = (sig, make_client(dict(pl)))
    client = got[1]
    ext = job.ext or None
    if job.kind == "movie":
        fn = getattr(client, "vod_url", None)
    else:
        fn = getattr(client, "episode_url", None)
    url = fn(job.stream_id, ext) if fn is not None else ""
    src = dict(pl.get("options") or {}, _kind=pl.get("kind") or "xtream",
               _server=str(pl.get("server") or ""))
    return str(url or ""), src


class _DownloadsMixin:
    """MainWindow mixin: downloads."""

    downloads: dl.Downloads
    _dl_page: DownloadsPlace | None = None
    _dl_timer: QTimer | None = None
    _dl_notice = 0
    _dl_notice_job = ""
    _dl_seen = -1
    _dl_items_sig: tuple = ()
    _dl_jobs_sig: tuple = ()
    _dl_section = "all"
    #: (source, series id or title) of the series whose episodes the place shows.
    _dl_series: tuple[str, str] | None = None
    #: (row, kind, series) the detail column shows, for its row.
    _dl_detail_of: tuple | None = None
    _dl_born = 0.0

    # -- made, and let go ----------------------------------------------------------------

    def _init_downloads(self) -> None:
        self._dl_born = time.monotonic()
        self._dl_sources: dict = {}
        self._dl_clients: dict = {}
        self.downloads = dl.Downloads(self.settings, resolve=self._dl_resolve,
                                      picture=self._dl_picture)
        self._dl_trust()
        t = QTimer(self)
        t.setInterval(TICK_MS)
        t.timeout.connect(self._dl_tick)
        t.start()
        self._dl_timer = t
        self.downloads.start()

    def _dl_shutdown(self) -> None:
        d = getattr(self, "downloads", None)
        if d is None:
            return
        if self._dl_timer is not None:
            self._dl_timer.stop()
        d.close()
        try:
            d.save()
        except Exception:
            log.debug("downloads: the queue was not kept", exc_info=True)

    def _dl_trust(self) -> None:
        """The posters saved beside the downloads are the app's own
        pictures (core/picture_files)."""
        picture_files.trust("downloads", [self.downloads.folder()])

    def _dl_resolve(self, job: dl.Job) -> tuple[str, dict | None]:
        return _resolve_with(self._dl_sources, self._dl_clients, job)

    def _dl_picture(self, address: str) -> bytes | None:
        """(the downloads' worker) A poster's bytes, fetched into the image
        cache as the lists' pictures are (their rules, cooldowns and size
        cap), checked as a picture."""
        if not address or address.startswith(("/", "file:")):
            return None
        from ..core.workers import checked_picture
        loader = getattr(self, "poster_art", None) or getattr(self, "logos", None)
        if loader is None:
            return None
        path = loader.fetch_file(address, timeout=8.0, max_bytes=8 * 1024 * 1024)
        if path is None:
            return None
        try:
            data = path.read_bytes()
        except OSError:
            return None
        return checked_picture(data)

    # -- the beat ------------------------------------------------------------------

    def _dl_tick(self) -> None:
        d = getattr(self, "downloads", None)
        if d is None:
            return
        try:
            jobs = d.jobs()
            if jobs:
                self._dl_sources = {str(p.get("id") or ""): dict(p)
                                    for p in self.playlist_store.playlists()
                                    if isinstance(p, dict)}
                d.set_holds({j.id: self._dl_hold_reason(j) for j in jobs
                             if j.state in (dl.WAITING, dl.RUNNING)})
            if d.dirty:
                d.save()
            for what, job, item in d.take_events():
                self._dl_announce(what, job, item)
            self._dl_notice_sync(jobs)
            if d.version != self._dl_seen:
                self._dl_seen = d.version
                self._dl_changed(jobs)
            busy = bool(jobs) or bool(d.dirty) or not d.scanned
            want = TICK_MS if busy else IDLE_TICK_MS
            if self._dl_timer is not None and self._dl_timer.interval() != want:
                self._dl_timer.setInterval(want)
        except Exception:
            log.exception("downloads: the beat failed")

    def _dl_hold_reason(self, job: dl.Job) -> str:
        """Why *job* may not run now ("" when it may): private viewing, the
        app's own start, or another of the app's on its account with the
        limit full or not known - most accounts allow one connection."""
        if privacy.private():
            return "private"
        if offline.on():
            return "offline"            # offline mode: it waits for the network
        if time.monotonic() - self._dl_born < START_GRACE_S:
            return "starting"
        if job.go_ahead:
            return ""
        return "connection" if self._dl_crowded(job.source) else ""

    def _dl_crowded(self, source: str, uses: list[Use] | None = None) -> bool:
        """Whether one more connection to *source*'s account - the
        download's - would go over its limit, or with none known whether
        something else of the app's uses it now."""
        try:
            listed, key = self._conn_by_account(self._conn_uses() if uses is None else uses,
                                                source)
            own = connections.own_count(listed, key) + self._conn_served(source)
            cap = self._conn_cap(key)
            if cap:
                return self._conn_ledger().check(key, listed, 1, limit=cap) is not None
            return own > 0
        except Exception as e:      # counting must never stand in the way
            log.debug("downloads: the account's connections not counted: %s", e)
            return False

    def _dl_uses(self) -> list[Use]:
        """The connection a download the person let run beside the rest holds
        (mw_connections counts it); one that gives way holds none of theirs."""
        d = getattr(self, "downloads", None)
        cur = d.current() if d is not None else None
        return [Use("download", cur.source)] if cur is not None and cur.go_ahead else []

    def _dl_on_account(self, source: str) -> dl.Job | None:
        """The running download that gives way, when it is on *source*'s
        account (the source in use when ""): None for none."""
        d = getattr(self, "downloads", None)
        cur = d.current() if d is not None else None
        if cur is None or cur.go_ahead:
            return None
        acc = self._conn_accounts()
        src = str(source or self._conn_source())
        if acc.get(cur.source, cur.source) != acc.get(src, src):
            return None
        return cur

    def _dl_room_for(self, cur: dl.Job, uses: list[Use]) -> bool:
        """A known limit with room for the download beside *uses*."""
        try:
            listed, key = self._conn_by_account(uses, cur.source)
            cap = self._conn_cap(key)
            return bool(cap) and self._conn_ledger().check(key, listed, 1, limit=cap) is None
        except Exception:
            return False

    def _dl_before_play(self, url: str) -> bool:
        """The player about to open *url* (its guard, mw_recording): a
        download on the account gives way first - with its limit full or not
        known - and goes on by itself once the player lets go. Never stops
        the playback (True)."""
        if not connections.is_network(url):
            return True
        cur = self._dl_on_account("")
        if cur is None:
            return True
        uses = list(self._conn_uses(free_player=True))
        uses.append(Use("player", self._conn_source(), str(url)))
        if self._dl_room_for(cur, uses):
            return True
        self.downloads.hold_now(cur.id)
        log.info("downloads: %s waits while the player plays", for_log(cur.title))
        self._show_toast(tr("dl_waits_for_play"), 5000)
        return True

    def _dl_make_room(self, source: str | None = None) -> None:
        """Another of the app's is about to open a connection to *source*'s
        account (a multiview cell, a cast, a test, a recording): a download
        there gives way now unless a known limit has room for both."""
        cur = self._dl_on_account(str(source or ""))
        if cur is None:
            return
        uses = list(self._conn_uses())
        uses.append(Use("player", str(source or self._conn_source())))
        if not self._dl_room_for(cur, uses):
            self.downloads.hold_now(cur.id)
            log.info("downloads: %s gives way to another connection", for_log(cur.title))

    def _dl_private_on(self) -> None:
        """Private viewing began: the running download stops and waits."""
        d = getattr(self, "downloads", None)
        if d is not None:
            d.private_on()

    # -- what is said ------------------------------------------------------------------

    def _dl_why(self, job: dl.Job) -> str:
        key = WHY_KEYS.get(job.why, "dl_why_other")
        if key == "dl_why_status":
            return tr(key, code=job.detail or "?")
        return tr(key)

    def _dl_state_text(self, job: dl.Job) -> str:
        if job.state == dl.RUNNING:
            if job.total:
                return tr("dl_state_running", pct=round(100 * (job.progress or 0.0)))
            return tr("dl_state_running_size", size=size_text(job.done) or "0 B")
        if job.state == dl.PAUSED:
            return tr("dl_state_paused")
        if job.state == dl.FAILED:
            return tr("dl_state_failed", why=self._dl_why(job))
        hold = self.downloads.holds.get(job.id, "")
        return tr(HOLD_KEYS.get(hold, "dl_state_waiting"))

    def _dl_announce(self, what: str, job: dl.Job, item: dl.Item | None) -> None:
        if what == "done":
            self._show_toast(tr("dl_done", title=job.title), 4000)
            return
        hub_of(self).post(tr("dl_failed", title=job.title, why=self._dl_why(job)),
                          level="error", key=f"download-failed:{job.id}",
                          action=self._dl_open_place, action_label=tr("dl_show"))

    def _dl_notice_sync(self, jobs: list[dl.Job]) -> None:
        """The running download as a job in the notices: its words and its
        progress, Cancel, a click opens the place."""
        hub = hub_of(self)
        cur = next((j for j in jobs if j.state == dl.RUNNING), None)
        if cur is None:
            if self._dl_notice:
                hub.finish(self._dl_notice)
                self._dl_notice, self._dl_notice_job = 0, ""
            return
        text = tr("dl_notice", title=cur.title)
        done = float(cur.done) if cur.total else None
        total = float(cur.total) if cur.total else None
        if self._dl_notice and self._dl_notice_job == cur.id and hub.alive(self._dl_notice):
            hub.update(self._dl_notice, text, done=done, total=total)
            return
        if self._dl_notice:
            hub.finish(self._dl_notice)
        self._dl_notice = hub.start(text, key="download", kind="download", mine=True,
                                    cancel=partial(self._dl_cancel, cur.id),
                                    cancel_text=tr("dl_cancel"), act=self._dl_open_place,
                                    done=done, total=total, timeout_ms=30 * 60_000)
        self._dl_notice_job = cur.id

    def _dl_changed(self, jobs: list[dl.Job]) -> None:
        """What the downloads show, again: the place, the pills where a file
        came or went, the column under the video, the sidebar's count."""
        d = self.downloads
        items_sig = tuple(sorted((i.ident, i.size) for i in d.items()))
        jobs_sig = tuple((j.id, j.state) for j in jobs)
        files = items_sig != self._dl_items_sig
        self._dl_items_sig = items_sig
        listed = jobs_sig != self._dl_jobs_sig
        self._dl_jobs_sig = jobs_sig
        if self._dl_showing():
            if files:
                self._dl_render()
            else:
                self._dl_render_running()
        if files:
            self._dl_pills_changed()
            # Offline mode's lists are the downloads: read again.
            again = getattr(self, "_offline_downloads_changed", None)
            if callable(again):
                again()
        if files or listed:
            shown = self._dl_detail_of
            if shown is not None:
                self._dl_detail_show(*shown)
            nav = getattr(self, "_refresh_nav_status", None)
            if callable(nav):
                nav()

    def _dl_pills_changed(self) -> None:
        """A file came or went: the cards and episode rows say it again."""
        for name in ("_pf_view",):
            v = getattr(self, name, None)
            if v is not None and not sip.isdeleted(v) and hasattr(v, "redress"):
                v.redress()
        for hook in ("_place_series_refresh", "_sp_refresh", "_mitt_refresh"):
            fn = getattr(self, hook, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    log.debug("downloads: %s not drawn again", hook, exc_info=True)

    def _dl_queued_count(self) -> int:
        """Downloads still to come - running, waiting or paused: the
        sidebar's count on Downloaded (mw_nav)."""
        d = getattr(self, "downloads", None)
        try:
            return sum(1 for j in d.jobs() if j.state != dl.FAILED) if d is not None else 0
        except Exception:
            return 0

    def _dl_active(self) -> str:
        store = getattr(self, "playlist_store", None)
        return str(getattr(store, "active_id", "") or "") if store is not None else ""

    # -- what a title is ------------------------------------------------------------------

    @staticmethod
    def _dl_stream_id(kind: str, row: Any, key: Any = None) -> str:
        """A title's stream id as a download keeps it: a film's stream id, an
        episode's own id (what the source's address is made from)."""
        if isinstance(row, dict):
            v = row.get("stream_id") if kind == "movie" else row.get("id")
            if v in (None, "") and kind == "movie":
                v = row.get("_key")
            if v not in (None, ""):
                return str(v)
        return "" if key is None else str(key)

    def _dl_find(self, kind: str, row: Any, key: Any = None,
                 source: str | None = None) -> dl.Item | None:
        """The downloaded file of a title of the source in use (or
        *source*), while it is there."""
        d = getattr(self, "downloads", None)
        if d is None or kind not in dl.KINDS:
            return None
        sid = self._dl_stream_id(kind, row, key)
        if not sid:
            return None
        hit = d.find(source if source is not None else self._dl_active(), kind, sid)
        if hit is None or not os.path.isfile(hit.path):
            return None
        return hit

    def _dl_job_for(self, kind: str, row: Any) -> dl.Job | None:
        d = getattr(self, "downloads", None)
        sid = self._dl_stream_id(kind, row)
        if d is None or not sid:
            return None
        return d.job_of(self._dl_active(), kind, sid)

    def _dl_pills(self, kind: str, row: Any) -> tuple:
        """("dl",) for a title whose file is downloaded, else ()."""
        try:
            return ("dl",) if self._dl_find(kind, row) is not None else ()
        except Exception:
            return ()

    def _dl_series_has(self, series: Any) -> bool:
        """Whether episodes of *series* (of the source in use) are downloaded."""
        d = getattr(self, "downloads", None)
        if d is None or not isinstance(series, dict) or series.get("series_id") in (None, ""):
            return False
        src, sid = self._dl_active(), str(series.get("series_id"))
        return any(i.kind == "episode" and i.source == src and i.series_id == sid
                   for i in d.items())

    def _dl_allowed(self, kind: str, row: Any, series: Any = None) -> bool:
        """Whether this profile may download (and see the download of) a
        title: a kids profile only what its lists show."""
        kids = getattr(self, "_profile_kids", None)
        if not (callable(kids) and kids()):
            return True
        test = dict(row) if isinstance(row, dict) else {}
        test["_kind"] = "vod" if kind == "movie" else "episode"
        if kind == "episode" and isinstance(series, dict):
            test.setdefault("_series_ctx", series)
        return bool(self._kids_allows(test, "vod" if kind == "movie" else "series"))

    # -- asking for one ---------------------------------------------------------------------

    def _dl_meta(self, kind: str, row: dict, series: dict | None) -> tuple[dict, str, str, str]:
        """(what its sidecar keeps, its title, its poster's and its series'
        poster's addresses) of a film or an episode as its row has them."""
        from ..core import film_view as fv
        from ..core import series_view as sv
        pl = self.playlist_store.active() if getattr(self, "playlist_store", None) else None
        meta: dict[str, Any] = {"source_name": str((pl or {}).get("name") or "")}
        if kind == "movie":
            title = str(self.channel_display_name(row) or row.get("name") or row.get("title")
                        or "")
            meta.update(year=fv.year_of(row), plot=fv.plot_of(row),
                        length=fv.minutes_of(row) * 60 or None, rating=fv.rating_of(row),
                        category_id=row.get("category_id"), genre=row.get("genre"),
                        category_name=self._dl_category_name("vod", row.get("category_id")))
            try:
                poster = str(self.cover.cover_url(row, "vod") or "")
            except Exception:
                poster = ""
            poster = poster or str(row.get("stream_icon") or row.get("cover") or "")
            return meta, title, poster, ""
        s = series or {}
        stitle = str(s.get("name") or s.get("title") or row.get("_series_title") or "")
        ep_title = sv.episode_title(row, stitle)
        season, number = sv.season_of(row.get("season")), sv.episode_number(row)
        secs = sv.minutes_of(row) * 60
        meta.update(series_id=s.get("series_id"), series_title=stitle,
                    series_plot=fv.plot_of(s), series_category_id=s.get("category_id"),
                    series_year=sv.year_of(s.get("releaseDate"), s.get("year")),
                    season=season, episode=number, episode_title=ep_title,
                    plot=sv.plot_of(row), length=secs or None,
                    category_id=s.get("category_id"),
                    series_category_name=self._dl_category_name("series", s.get("category_id")))
        se = tr("sp_se", s=season, e=number) if season is not None and number is not None else ""
        title = " · ".join(x for x in (stitle, se, ep_title) if x) or str(row.get("name") or "")
        try:
            series_poster = str(self.cover.cover_url(s, "series") or "") if s else ""
        except Exception:
            series_poster = ""
        series_poster = series_poster or str(s.get("cover") or s.get("stream_icon") or "")
        return meta, title, sv.still_of(row), series_poster

    def _dl_category_name(self, mode: str, cid: Any) -> str:
        """The provider's own name of the category *cid* of *mode* ("vod",
        "series"), as the window or its client holds it - never a request;
        "" when not known (a kids profile then does not see the download
        offline: providers/downloaded.py)."""
        if cid in (None, ""):
            return ""
        cats = getattr(self, "_raw_categories", None) if getattr(self, "mode", "") == mode else None
        if not cats:
            held = getattr(getattr(self, "client", None), "held", None)
            key = {"vod": ("get_vod_categories",), "series": ("get_series_categories",)}.get(mode)
            try:
                from ..providers.client import list_rows
                cats = list_rows(held(key)) if callable(held) and key else None
            except Exception:
                cats = None
        for c in cats or []:
            if isinstance(c, dict) and str(c.get("category_id")) == str(cid):
                return str(c.get("category_name") or "")
        return ""

    def _dl_title(self, kind: str, row: dict, series: dict | None = None) -> None:
        """One film or episode into the downloads."""
        self._dl_queue_many(kind, [row], series)

    def _dl_queue_many(self, kind: str, rows: list, series: dict | None = None) -> list[dl.Job]:
        """Films or episodes into the downloads, in order: never while
        viewing privately, for a kids profile only what its lists show, the
        connection asked about first when the account has no room."""
        if privacy.private():
            self._show_toast(tr("dl_private_no"), 5000)
            return []
        src = self._dl_active()
        if not src:
            return []
        made: list[dl.Job] = []
        for row in rows:
            if not isinstance(row, dict) or not self._dl_allowed(kind, row, series):
                continue
            sid = self._dl_stream_id(kind, row)
            if not sid:
                continue
            meta, title, poster, series_poster = self._dl_meta(kind, row, series)
            job = self.downloads.add(kind, src, sid, key=self._item_key(row), title=title,
                                     ext=str(row.get("container_extension") or ""), meta=meta,
                                     poster=poster, series_poster=series_poster)
            if job is not None:
                made.append(job)
        if not made:
            if rows and not any(self._dl_allowed(kind, r, series) for r in rows
                                if isinstance(r, dict)):
                self._show_toast(tr("profile_kids_not_here"), 4000)
            return []
        self._dl_ask_room(made)
        self._dl_tick()                 # the beat back to every second
        if len(made) == 1:
            self._show_toast(tr("dl_queued", title=made[0].title), 3000)
        else:
            self._show_toast(tr("dl_queued_n", n=len(made)), 3000)
        self._dl_tick()
        return made

    def _dl_ask_room(self, jobs: list[dl.Job]) -> None:
        """Something else of the app's on the account and its limit full or
        not known: asked once whether these download now anyway (one more
        connection) or once the connection is free."""
        first = next((j for j in jobs if j.state != dl.RUNNING), None)
        if first is None or time.monotonic() - self._dl_born < START_GRACE_S \
                or not self._dl_crowded(first.source):
            return
        v = None
        try:
            listed, key = self._conn_by_account(self._conn_uses(), first.source)
            cap = self._conn_cap(key)
            v = self._conn_ledger().check(key, listed, 1, limit=cap) if cap else None
        except Exception:
            v = None
        body = tr("dl_ask_body") + "\n\n" + (self._conn_warning(v) if v is not None
                                            else tr("conn_limit_unknown"))
        idx = self._choice_dialog(tr("dl_ask_title"), body,
                                  [(tr("dl_ask_now"), "normal"), (tr("dl_ask_later"), "primary")])
        now = idx == 0
        log.info("connections: a download beside what else uses the account - %s",
                 "now anyway" if now else "once it is free")
        if now:
            for j in jobs:
                self.downloads.allow(j.id)
            self._conn_server_room(None, first.source)

    def _dl_series_all(self, series: dict, season: Any = None) -> None:
        """Every episode of *series* (of one season, *season*) into the
        downloads - its episodes asked of the source off the window's thread."""
        sid = series.get("series_id")
        if sid is None or not self._dl_allowed("episode", {}, series):
            return
        if privacy.private():
            self._show_toast(tr("dl_private_no"), 5000)
            return
        client = self.client
        series = dict(series)

        def done(info) -> None:
            if self.client is not client:
                return
            eps = self._episode_rows(info, series)
            if season is not None:
                eps = [e for e in eps if str(e.get("season")) == str(season)]
            self._dl_queue_many("episode", eps, series)

        run_async(self.pool, lambda: client.series_info(sid), done,
                  lambda _e: self._show_toast(tr("err_series_open"), 4000))

    # -- the menus ---------------------------------------------------------------------------

    def _dl_item_menu(self, m: QMenu, it: dict, content_kind: str) -> None:
        """The list's menu of a row (mw_context): Download for a film or an
        episode, Download all episodes for a series."""
        if content_kind == "vod" and it.get("stream_id") is not None:
            self._dl_title_actions(m, "movie", it)
        elif self.series_ctx and it.get("id") is not None:
            self._dl_title_actions(m, "episode", it, self.series_ctx)
        elif content_kind == "series" and it.get("series_id") is not None \
                and self._dl_allowed("episode", {}, it):
            m.addSeparator()
            m.addAction(tr("dl_all_episodes"), partial(self._dl_series_all, dict(it)))

    def _dl_title_actions(self, m: QMenu, kind: str, row: dict, series: dict | None = None) -> None:
        """A title's download actions: Download; while it downloads Pause,
        Resume or Cancel; once downloaded Delete download."""
        if not self._dl_allowed(kind, row, series):
            return
        m.addSeparator()
        hit = self._dl_find(kind, row)
        if hit is not None:
            m.addAction(tr("dl_delete"), partial(self._dl_delete_asked, hit))
            return
        job = self._dl_job_for(kind, row)
        if job is None:
            act = m.addAction(tr("dl_download"), partial(self._dl_title, kind, dict(row),
                                                          dict(series) if series else None))
            act.setEnabled(not privacy.private())
            return
        if job.state == dl.PAUSED:
            m.addAction(tr("dl_resume"), partial(self._dl_resume, job.id))
        elif job.state == dl.FAILED:
            m.addAction(tr("dl_retry"), partial(self._dl_resume, job.id))
        else:
            m.addAction(tr("dl_pause"), partial(self.downloads.pause, job.id))
        m.addAction(tr("dl_cancel"), partial(self._dl_cancel, job.id))

    def _dl_episode_menu(self, m: QMenu, ep: dict) -> None:
        """A series page's episode menu (mw_series_page)."""
        if getattr(self, "_sp_local", None) is None and ep.get("id") is not None:
            self._dl_title_actions(m, "episode", ep, self._sp_ctx or {})

    def _dl_series_menu(self, m: QMenu) -> None:
        """A series page's ⋯: Download this season, Download all episodes."""
        page = getattr(self, "_series_page", None)
        ctx = getattr(self, "_sp_ctx", None) or {}
        if page is None or getattr(self, "_sp_local", None) is not None \
                or ctx.get("series_id") is None or not self._dl_allowed("episode", {}, ctx):
            return
        seasons = list(getattr(self, "_sp_seasons", None) or [])
        m.addSeparator()
        cur = getattr(page, "current", -1)
        on = not privacy.private()
        if len(seasons) > 1 and 0 <= cur < len(seasons):
            act = m.addAction(tr("dl_season"), partial(
                self._dl_queue_many, "episode", list(seasons[cur].episodes), dict(ctx)))
            act.setEnabled(on)
        every = [e for s in seasons for e in s.episodes]
        act = m.addAction(tr("dl_all_episodes"), partial(
            self._dl_queue_many, "episode", every, dict(ctx)))
        act.setEnabled(on and bool(every))

    def _dl_cancel(self, job_id: str) -> bool:
        self.downloads.cancel(job_id)
        self._dl_tick()
        return True

    def _dl_resume(self, job_id: str) -> None:
        if privacy.private():
            self._show_toast(tr("dl_private_no"), 5000)
            return
        self.downloads.resume(job_id)
        job = self.downloads.job(job_id)
        if job is not None:
            self._dl_ask_room([job])
        self._dl_tick()

    def _dl_delete_asked(self, item: dl.Item) -> None:
        idx = self._choice_dialog(tr("dl_delete"), tr("dl_delete_ask", title=item.title),
                                  [(tr("dl_delete"), "danger"), (tr("common_cancel"), "primary")])
        if idx == 0:
            self.downloads.delete(item.source, item.kind, item.stream_id)
            self._dl_tick()

    def _dl_delete_many_asked(self, title: str, items: list[dl.Item]) -> None:
        idx = self._choice_dialog(tr("dl_delete"),
                                  tr("dl_delete_series_ask", n=len(items), title=title),
                                  [(tr("dl_delete"), "danger"), (tr("common_cancel"), "primary")])
        if idx == 0:
            for item in items:
                self.downloads.delete(item.source, item.kind, item.stream_id)
            self._dl_tick()

    # -- playing one ----------------------------------------------------------------------------

    #: _start_playback's arguments after its kind, in order.
    _START_ARGS = ("record", "item", "catchup", "start_at", "to_cast", "own_buffer")

    def _dl_start(self, url: str, title: str, icon_url, key, kind: str, *args, **kw) -> None:
        """_start_playback for a film or an episode of the source in use: a
        downloaded one plays its file as that same title - its resume point,
        marks, Continue - and History keeps the source's address of it,
        never the file's path. Anything else as it is, with the very same
        arguments."""
        given = {**dict(zip(self._START_ARGS, args, strict=False)), **kw}
        item = given.get("item")
        hit = None if given.get("catchup") else self._dl_find(kind, item, key)
        if hit is None:
            self._start_playback(url, title, icon_url, key, kind, *args, **kw)
            return
        record = given.pop("record", True)
        given["record"] = False
        log.info("play: %s from its download", for_log(title))
        if not connections.is_network(url):
            # Offline mode's lists hand out the file's path (providers/
            # downloaded.py): History keeps the source's address, never it.
            url = self._dl_address_now(kind, item) if isinstance(item, dict) else ""
        self._start_playback(hit.path, title, icon_url, key, kind, **given)
        lp = getattr(self, "_last_playback", None) or {}
        if record and url and lp.get("url") == hit.path \
                and str(getattr(self, "_playing_key", None)) == str(key):
            self.history.add(url, title, icon_url, key, kind,
                             extra=self._history_extra(kind, item, title))

    def _dl_address_now(self, kind: str, row: dict) -> str:
        """The source's address of a title when the client has it at once
        (History keeps it); "" for one that would wait on the network."""
        client = self.client
        if getattr(client, "offline", False):
            # Offline mode: the source's own client, held aside, makes it -
            # an Xtream address is words alone; one that would ask the
            # network is "" (_link_waits), and nothing is sent.
            held = getattr(self, "_off_held", None)
            if held is None:
                return ""
            self.client = held

        def plan() -> str:
            if kind == "movie":
                return self.client.vod_url(row.get("stream_id"), row.get("container_extension"))
            return self.client.episode_url(row.get("id"), row.get("container_extension"))
        try:
            if self._link_waits(plan) is not None:
                return ""
            got = str(plan() or "")
            return got if connections.is_network(got) else ""
        except Exception:
            return ""
        finally:
            self.client = client        # _link_waits puts it back; so does this

    def _dl_play(self, item: dl.Item) -> None:
        """A downloaded title from the place: of the source in use as that
        title (an episode with its series' downloaded episodes as the queue
        autoplay goes on with), another source's as a file of its own."""
        if not os.path.isfile(item.path):
            self._show_toast(tr("dl_missing"), 4000)
            return
        row = dl.as_row(item)
        icon = row.get("stream_icon") or None
        if item.source != self._dl_active():
            self._start_playback(item.path, item.title, icon, item.path, "local", record=True,
                                 item={"name": item.title, "_path": item.path, "_key": item.path,
                                       "_kind": "local"})
            return
        if item.kind == "movie":
            self._dl_start(self._dl_address_now("movie", row), item.title, icon, item.key,
                           "movie", record=True, item=row)
            return
        series = dl.series_row(item)
        queue = [dl.as_row(i) for i in self._dl_episodes_of(item.source, self._dl_series_key(item))]
        index = next((n for n, r in enumerate(queue) if r.get("id") == item.stream_id), -1)
        if index >= 0:
            row = queue[index]
        saved = self.series_ctx
        self.series_ctx = series
        try:
            self._ep_queue_override = queue
            self._ep_index_override = index
            self._dl_start(self._dl_address_now("episode", row), item.title, icon, item.key,
                           "episode", record=True, item=row)
        finally:
            self.series_ctx = saved
            self._ep_queue_override = None
            self._ep_index_override = -1

    # -- the column under the video ----------------------------------------------------------------

    def _build_dl_detail(self, il) -> None:
        """Under the poster: "Downloaded · 1.4 GB" and Delete download, how
        far it downloads, or Download (mw_build_detail, after Continue)."""
        box = QWidget(objectName="DetailDownload")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.d_dl_label = QLabel("", objectName="DetailMeta")
        self.d_dl_label.setTextFormat(Qt.TextFormat.PlainText)
        self.d_dl_label.setWordWrap(True)
        # Its words from the start (a screen reader never meets it nameless);
        # what it does is said again as the row changes (_dl_detail_show).
        self.d_dl_btn = QPushButton(tr("dl_download"))
        self.d_dl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.d_dl_btn.clicked.connect(self._dl_detail_clicked)
        lay.addWidget(self.d_dl_label, 1)
        lay.addWidget(self.d_dl_btn, 0)
        box.hide()
        il.addWidget(box)
        self.d_dl = box

    def _dl_detail_show(self, row: Any = None, kind: str = "", series: Any = None) -> None:
        """What the column says of a film's or an episode's download (None:
        nothing - another kind of row)."""
        box = getattr(self, "d_dl", None)
        if box is None:
            return
        if not isinstance(row, dict) or kind not in dl.KINDS \
                or not self._dl_allowed(kind, row, series):
            self._dl_detail_of = None
            box.hide()
            return
        self._dl_detail_of = (row, kind, series)
        hit = self._dl_find(kind, row)
        job = None if hit is not None else self._dl_job_for(kind, row)
        if hit is not None:
            text = tr("dl_downloaded_size", size=size_text(hit.size))
            if offline.on():
                text = f"{tr('offline_mark')} · {text}"     # offline mode: said by it
            self.d_dl_label.setText(text)
            self.d_dl_btn.setText(tr("dl_delete"))
        elif job is not None:
            self.d_dl_label.setText(self._dl_state_text(job))
            self.d_dl_btn.setText(tr("dl_retry") if job.state == dl.FAILED
                                  else tr("dl_resume") if job.state == dl.PAUSED
                                  else tr("dl_cancel"))
        else:
            if not self._dl_active() or privacy.private():
                box.hide()
                return
            self.d_dl_label.setText("")
            self.d_dl_btn.setText(tr("dl_download"))
        box.show()

    def _dl_detail_clicked(self) -> None:
        shown = self._dl_detail_of
        if shown is None:
            return
        row, kind, series = shown
        hit = self._dl_find(kind, row)
        if hit is not None:
            self._dl_delete_asked(hit)
        else:
            job = self._dl_job_for(kind, row)
            if job is None:
                self._dl_title(kind, dict(row), dict(series) if isinstance(series, dict) else None)
            elif job.state in (dl.PAUSED, dl.FAILED):
                self._dl_resume(job.id)
            else:
                self._dl_cancel(job.id)
        self._dl_detail_show(row, kind, series)

    # -- the place ---------------------------------------------------------------------------------

    def _dl_showing(self) -> bool:
        page, stack = self._dl_page, getattr(self, "_mid_stack", None)
        return (page is not None and stack is not None and not sip.isdeleted(page)
                and stack.currentWidget() is page)

    def _ensure_dl_page(self) -> DownloadsPlace:
        page = self._dl_page
        if page is not None and not sip.isdeleted(page):
            return page
        loader = getattr(self, "poster_art", None) or getattr(self, "logos", None)
        page = DownloadsPlace(loader)
        self._dl_page = page
        v = page.view
        v.dress_fn = self._dl_dress
        v.activated.connect(self._dl_activate)
        v.menu.connect(self._dl_menu)
        page.running.strip.click_plays = None
        page.running.activated.connect(self._dl_job_menu_here)
        page.running.menu.connect(self._dl_job_menu)
        page.back_requested.connect(self._dl_back)
        page.filter.textChanged.connect(lambda _t: self._dl_render())
        page.header.sort_changed.connect(self._dl_sort_picked)
        page.header.view_changed.connect(self._dl_section_picked)
        page.layout_changed.connect(self._dl_layout_picked)
        adopt = getattr(self, "_place_look_adopt", None)
        if callable(adopt):
            adopt(page)
        self._mid_stack.addWidget(page)
        cats = getattr(self, "cat_list", None)
        if cats is not None:
            cats.itemClicked.connect(self._dl_category_clicked)
        return page

    def _place_dl_show(self) -> bool:
        """Downloaded in the middle pane (core/places: the place's own page):
        mw_places' hook for its entry and for Back from a page over it. False
        in TV mode, which holds the window."""
        if getattr(self, "_tv_on", False):
            return False
        if getattr(self, "_player_fs", False):
            self._exit_player_fullscreen()
        if getattr(self, "_focus_mode", False):
            self._set_focus_mode(False)
        self._pages_give_way()
        page = self._ensure_dl_page()
        self._mid_stack.setCurrentWidget(page)
        self._place_entered("dl")
        self._dl_render()
        page.view.setFocus(Qt.FocusReason.OtherFocusReason)
        return True

    def _place_dl_refresh(self) -> None:
        """A profile, a source or a sync changed what it may show."""
        if self._dl_page is not None and not sip.isdeleted(self._dl_page):
            self._dl_render()

    def _close_dl_page(self) -> None:
        if self._dl_showing():
            self._mid_stack.setCurrentIndex(0)

    def _dl_category_clicked(self, _item=None) -> None:
        """A category picked in the sidebar: its list, in the page's place."""
        if self._dl_showing():
            self._close_dl_page()
            self._place_entered(places.place_of_mode(self.mode))

    def _dl_open_place(self) -> None:
        self._show_place("dl")

    def _dl_back(self) -> None:
        if self._dl_series is not None:
            self._dl_series = None
            self._dl_render()

    def _dl_sort_picked(self, key: str) -> None:
        if key in SORT_WORDS:
            self.settings.setValue(SORT_KEY, key)
            self._dl_render()

    def _dl_section_picked(self, key: str) -> None:
        if key in SECTIONS and key != self._dl_section:
            self._dl_section = key
            self._dl_series = None
            self._dl_render()

    def _dl_layout_picked(self, kind: str) -> None:
        if kind in VIEWS:
            self.settings.setValue(VIEW_KEY, kind)
            self._dl_render()

    def _dl_sort(self) -> str:
        v = str(self.settings.value(SORT_KEY, NEWEST) or NEWEST)
        return v if v in SORT_WORDS else NEWEST

    def _dl_layout(self) -> str:
        v = str(self.settings.value(VIEW_KEY, "poster") or "poster")
        return v if v in VIEWS else "poster"

    # -- what the place holds ------------------------------------------------------------------

    def _dl_locked(self, item: dl.Item, shut_of: dict) -> bool:
        """Whether the parental lock leaves *item* out now: its category one
        locked while the lock is shut - for a kids profile always - as the
        lists leave its rows out; one that does not say its category while
        a category of its kind is locked, held back."""
        from ..core.stores import CategoryOverrides, lock_hides
        kids = bool(getattr(self, "_profile_kids", lambda: False)())
        parental = getattr(self, "parental", None)
        if parental is not None and parental.session_unlocked and not kids:
            return False
        shut = shut_of.get(item.source)
        if shut is None:
            if item.source == self._dl_active():
                ov = self.overrides
            else:
                ov = CategoryOverrides(self.settings, f"category_overrides_{item.source}")
            shut = shut_of[item.source] = {m: ov.locked_ids(m) for m in ("vod", "series")
                                           if ov.locked_ids(m)}
        if not shut:
            return False
        info = item.info
        if item.kind == "movie":
            row = {"_kind": "movie", "category_id": info.get("category_id"),
                   "stream_id": item.stream_id}
        else:
            row = {"_kind": "episode", "_series_ctx": {
                "category_id": info.get("series_category_id") or info.get("category_id"),
                "series_id": item.series_id}}
        return lock_hides(row, shut, {}) is not False

    def _dl_visible(self) -> list[dl.Item]:
        """The downloads this profile may see now: a kids profile the source
        in use's that its lists show; none of a locked category."""
        kids = bool(getattr(self, "_profile_kids", lambda: False)())
        active = self._dl_active()
        shut: dict = {}
        out = []
        for item in self.downloads.items():
            if kids:
                if item.source != active:
                    continue
                row = dl.as_row(item)
                if not self._dl_allowed(item.kind, row, row.get("_series_ctx")):
                    continue
            if self._dl_locked(item, shut):
                continue
            out.append(item)
        return out

    @staticmethod
    def _dl_series_key(item: dl.Item) -> str:
        return item.series_id or str(item.info.get("series_title") or "")

    def _dl_episodes_of(self, source: str, key: str) -> list[dl.Item]:
        """A series' downloaded episodes the profile may see, in order."""
        from ..core import series_view as sv
        eps = [i for i in self._dl_visible() if i.kind == "episode" and i.source == source
               and self._dl_series_key(i) == key]

        def order(i: dl.Item) -> tuple:
            s, e = sv.season_of(i.info.get("season")), dl._whole(i.info.get("episode"))
            return (s if s is not None else 10 ** 6, e if e is not None else 10 ** 6, i.title)
        return sorted(eps, key=order)

    def _dl_cards(self) -> tuple[list[PlaceCard], str, str]:
        """The cards, the header's title and its count."""
        from ..core.palette_match import fold
        page = self._dl_page
        # The filter's words folded as the palette folds them (case and
        # accents left out); fold pads with spaces, so blank is none.
        typed = page.filter.text() if page is not None else ""
        words = fold(typed).strip() if typed.strip() else ""
        items = self._dl_visible()
        if self._dl_series is not None:
            src, key = self._dl_series
            eps = self._dl_episodes_of(src, key)
            if not eps:
                self._dl_series = None
            else:
                cards = [PlaceCard(key="dl:" + "|".join(e.ident), title=self._dl_episode_title(e),
                                   data=e) for e in eps
                         if not words or words in fold(self._dl_episode_title(e))]
                title = str(eps[0].info.get("series_title") or eps[0].title)
                count = " · ".join(x for x in (tr("count_episodes", n=len(eps)),
                                               size_text(sum(e.size for e in eps))) if x)
                return cards, title, count
        films = [i for i in items if i.kind == "movie"]
        groups: dict[tuple[str, str], list[dl.Item]] = {}
        for i in items:
            if i.kind == "episode":
                groups.setdefault((i.source, self._dl_series_key(i)), []).append(i)
        sort = self._dl_sort()
        entries: list[tuple[str, float, int, PlaceCard]] = []
        if self._dl_section in ("all", "movie"):
            for f in films:
                entries.append((f.title, f.added, f.size,
                                PlaceCard(key="dl:" + "|".join(f.ident), title=f.title, data=f)))
        if self._dl_section in ("all", "series"):
            for (src, key), eps in groups.items():
                name = str(eps[0].info.get("series_title") or eps[0].title)
                entries.append((name, max(e.added for e in eps), sum(e.size for e in eps),
                                PlaceCard(key=f"dls:{src}|{key}", title=name,
                                          data=("series", src, key, eps))))
        if words:
            entries = [e for e in entries if words in fold(e[0])]
        if sort == NAME:
            entries.sort(key=lambda e: fold(e[0]))
        elif sort == LARGEST:
            entries.sort(key=lambda e: -e[2])
        else:
            entries.sort(key=lambda e: -e[1])
        total = sum(i.size for i in items)
        count = " · ".join(x for x in (tr("count_movies", n=len(films)) if films else "",
                                       tr("count_series", n=len(groups)) if groups else "",
                                       size_text(total)) if x)
        return [e[3] for e in entries], tr("nav_downloads"), count

    def _dl_episode_title(self, item: dl.Item) -> str:
        info = item.info
        s, e = info.get("season"), info.get("episode")
        ep = str(info.get("episode_title") or "")
        try:
            se = tr("sp_se", s=int(s), e=int(e))
        except (TypeError, ValueError):
            se = ""
        return " · ".join(x for x in (se, ep) if x) or item.title

    def _dl_render(self) -> None:
        page = self._dl_page
        if page is None or sip.isdeleted(page):
            return
        try:
            cards, title, count = self._dl_cards()
            layout = self._dl_layout()
            series = self._dl_series is not None
            page.set_layout(layout, "wide" if series else "poster")
            page.view.set_items(cards)
            page.header.set_title(title)
            page.header.set_count(count)
            page.set_back(tr("dl_back") if series else None)
            page.set_sections(self._dl_section)
            page.header.switch.setVisible(not series)
            page.header.set_sort([(k, tr(w)) for k, w in SORT_WORDS.items()], self._dl_sort())
            page.show_layout_switch(True, layout)
            if cards:
                page.set_note("")
            elif page.filter.text().strip() or self.downloads.items():
                page.set_note(tr("dl_place_none"))
            else:
                page.set_note(tr("dl_place_empty"))
            self._dl_render_running()
            self._dl_mark_playing()
        except Exception:
            log.exception("downloads: the place was not drawn")

    def _dl_render_running(self) -> None:
        page = self._dl_page
        if page is None or sip.isdeleted(page):
            return
        cards = []
        for j in self.downloads.jobs():
            pills: tuple = (("fail", ""),) if j.state == dl.FAILED else ()
            cards.append(Card(key="job:" + j.id, title=j.title, line=self._dl_state_text(j),
                              image=j.poster or j.series_poster,
                              progress=j.progress if j.state == dl.RUNNING else None,
                              pills=pills, data=j))
        old = page.running.cards()
        if [(c.key, c.line, c.progress, c.pills) for c in old] \
                != [(c.key, c.line, c.progress, c.pills) for c in cards]:
            if [c.key for c in old] == [c.key for c in cards]:
                for a, b in zip(old, cards, strict=True):
                    a.line, a.progress, a.pills, a.data = b.line, b.progress, b.pills, b.data
                page.running.strip.viewport().update()
            else:
                page.running.set_cards(cards)

    def _dl_mark_playing(self) -> None:
        page = self._dl_page
        if page is None or sip.isdeleted(page):
            return
        lp = getattr(self, "_last_playback", None) or {}
        playing = getattr(getattr(self, "player", None), "current_url", None)
        if not playing:
            page.view.set_playing(None)
            return
        for c in page.view.items():
            if isinstance(c.data, dl.Item) and c.data.path == lp.get("url"):
                page.view.set_playing(c.key)
                return
        page.view.set_playing(None)

    def _dl_dress(self, card: Card) -> None:
        data = card.data
        if isinstance(data, dl.Item):
            row = dl.as_row(data)
            card.image = str(row.get("stream_icon") or "")
            info = data.info
            parts: list[str] = []
            if data.kind == "movie":
                parts.append(str(info.get("year") or ""))
            length = info.get("length")
            if isinstance(length, (int, float)) and length > 0:
                from .series_cards import minutes_text
                parts.append(minutes_text(max(1, round(length / 60))))
            parts.append(size_text(data.size))
            if data.source != self._dl_active():
                parts.append(str(info.get("source_name") or ""))
            card.line = line_of(parts)
            pills: list = ["dl"]
            if data.source == self._dl_active():
                group = "movie" if data.kind == "movie" else "episode"
                try:
                    pos, dur, _at = self.resume.progress(data.key, group)
                except Exception:
                    pos, dur = 0.0, 0.0
                card.progress = pos / dur if pos > 60 and dur > pos else None
                try:
                    if self.watched_source(row, "vod" if data.kind == "movie" else "episode"):
                        pills.append("seen")
                except Exception:
                    pass
            card.pills = tuple(pills)
        elif isinstance(data, tuple) and len(data) == 4:
            eps = data[3]
            first = eps[0]
            series = dl.series_row(first)
            card.image = str(series.get("cover") or "")
            card.line = line_of([tr("count_episodes", n=len(eps)),
                                 size_text(sum(e.size for e in eps))])
            card.pills = ("dl",)

    def _dl_activate(self, card) -> None:
        data = getattr(card, "data", None)
        if isinstance(data, dl.Item):
            self._dl_play(data)
        elif isinstance(data, tuple) and len(data) == 4:
            self._dl_series = (data[1], data[2])
            self._dl_render()

    def _dl_menu(self, card, pos: QPoint) -> None:
        data = getattr(card, "data", None)
        m = QMenu(self)
        if isinstance(data, dl.Item):
            m.addAction(tr("ctx_play_in_mpv"), partial(self._dl_play, data))
            m.addSeparator()
            m.addAction(tr("dl_delete"), partial(self._dl_delete_asked, data))
        elif isinstance(data, tuple) and len(data) == 4:
            m.addAction(tr("dl_delete"), partial(self._dl_delete_many_asked, card.title,
                                                 list(data[3])))
        else:
            return
        m.exec(pos)

    def _dl_job_menu_here(self, card) -> None:
        from PyQt6.QtGui import QCursor
        self._dl_job_menu(card, QCursor.pos())

    def _dl_job_menu(self, card, pos: QPoint) -> None:
        """A download's own: Pause, Resume, Try again, Download now anyway,
        Cancel download."""
        job = getattr(card, "data", None)
        if not isinstance(job, dl.Job):
            return
        job = self.downloads.job(job.id) or job
        m = QMenu(self)
        if job.state == dl.PAUSED:
            m.addAction(tr("dl_resume"), partial(self._dl_resume, job.id))
        elif job.state == dl.FAILED:
            m.addAction(tr("dl_retry"), partial(self._dl_resume, job.id))
        else:
            m.addAction(tr("dl_pause"), partial(self.downloads.pause, job.id))
            if job.state == dl.WAITING and self.downloads.holds.get(job.id) == "connection":
                m.addAction(tr("dl_ask_now"), partial(self._dl_anyway, job.id))
        m.addSeparator()
        m.addAction(tr("dl_cancel"), partial(self._dl_cancel, job.id))
        m.exec(pos)

    def _dl_anyway(self, job_id: str) -> None:
        """Download now beside what else uses the account: the person's one
        more connection."""
        job = self.downloads.job(job_id)
        if job is None:
            return
        self.downloads.allow(job_id)
        self._conn_server_room(None, job.source)
        self._dl_tick()
