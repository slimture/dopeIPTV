"""The middle pane follows what plays (MainWindow mixin).

Whatever starts playing from outside the middle pane - the phone remote,
the remote API and Home Assistant, the media keys, channel or episode up
and down, Home's shelves, the command palette, autoplay - and is not what
the pane shows already, the pane goes to it: a live channel to TV, its
category, the channel selected and scrolled to; an episode to its series'
page with the episode marked; a film to the Movies place, its category,
its card picked (a film's page showing it stays); a
local file to Local files, its folder; a station to Radio; a recording to
Recordings. A web stream leaves the pane as it is.

One function does it (``_pane_follow``), called by the playback path
(``_start_playback``) with what started, a moment after it started - never
in the way of the stream itself. It does nothing when:

- the play came from the middle pane itself: the list (what it plays is
  its own row: selected, nothing else), a series' page showing that
  series, the Movies place (ui/mw_place_films.py), the
  Search page or the Guide page (``from_pane``: their plays
  keep the page; ``_links_later`` carries that on to a play that waits
  for its address);
- the same title started again (the app's reconnect, a backup address, a
  go-back in the archive);
- a live channel of another source plays (failover, the Search page);
- the person is typing in a field of the window, a dialog or a menu is
  open, or TV mode is on (it has screens of its own).

Leaving a page goes as its Back would to the list (the series page's
timer stops, its way back is forgotten), and then as the sidebar logo's
jump to what plays does (``_jump_to_now_playing``).

What plays is one thing, here or on a TV: ``_now_playing`` says which and
where - the player's title, else what a running cast shows (ui/mw_cast.py's
context) - for the jump, and for every list and place that marks it
(``on_tv``: their mark says "On TV").
"""

from __future__ import annotations

import functools
from contextlib import contextmanager
from typing import Any, NamedTuple

from PyQt6 import sip
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QAbstractSpinBox, QApplication, QComboBox, QDialog, QLineEdit, QPlainTextEdit, QTextEdit,
)

from ..core.log import log

#: The play kinds the pane follows (a web stream: none).
FOLLOWED = frozenset({"live", "movie", "episode", "local", "radio", "recording"})
#: A list row's kind (its own, else the list's) as the play kind it is.
_ROW_KIND = {"live": "live", "fav": "live", "vod": "movie", "movie": "movie",
             "episode": "episode", "local": "local", "recording": "recording",
             "rec": "recording", "radio": "radio", "web": "web"}
#: The lists of one kind of row (the list's kind -> the play kind its rows
#: are); Favourites, History, Watch later and Watched mix them.
_ONE_KIND = {"live": "live", "vod": "movie", "series": "series", "episode": "episode",
             "local": "local", "radio": "radio", "rec": "recording"}


#: A play kind as the lists' group of what plays (as _start_playback keeps it).
GROUP_OF = {"live": "live", "movie": "vod", "episode": "episode", "recording": "rec",
            "local": "local", "radio": "radio", "web": "web"}


class NowPlaying(NamedTuple):
    """What plays now: its key (``_item_key``'s), the lists' group of it
    (live, vod, episode, rec, local, radio, web), its play kind, its row, an
    episode's series, a local file's path, how far it got (0-1), and
    whether it plays on a TV (*device*) rather than here."""
    key: Any
    group: str
    kind: str
    item: dict
    series_ctx: dict | None
    path: str
    fraction: float
    on_tv: bool
    device: str


def _typing(w: Any) -> bool:
    """Whether the person is typing in *w* (the window's focus): a search
    or filter box with something in it - an empty one that merely has the
    focus (the window gives its search box the focus at start) is no one
    typing - or a text editor or number box being edited."""
    if w is None or not w.isVisible():
        return False
    if isinstance(w, QLineEdit):
        return not w.isReadOnly() and bool(w.text())
    if isinstance(w, (QTextEdit, QPlainTextEdit)):
        return not w.isReadOnly()
    if isinstance(w, QAbstractSpinBox):
        return True
    if isinstance(w, QComboBox):
        return w.isEditable() and bool(w.currentText())
    return False


def from_pane(fn):
    """A method that plays from the middle pane itself: what it starts
    leaves the pane as it is."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        mark = getattr(self, "_from_pane", None)
        if mark is None:
            return fn(self, *args, **kwargs)
        with mark():
            return fn(self, *args, **kwargs)
    return wrapper


class _PaneFollowMixin:
    _play_origin: str | None = None
    _pane_follow_job: tuple | None = None

    @contextmanager
    def _from_pane(self):
        before, self._play_origin = self._play_origin, "pane"
        try:
            yield
        finally:
            self._play_origin = before

    def _keep_origin(self, fn):
        """*fn* run later with the origin of now (a play waiting for its
        address comes from where it was asked)."""
        origin = self._play_origin
        if origin is None:
            return fn
        return functools.partial(self._run_from, origin, fn)

    def _run_from(self, origin: str | None, fn, *args) -> Any:
        before, self._play_origin = self._play_origin, origin
        try:
            return fn(*args)
        finally:
            self._play_origin = before

    # -- what plays -------------------------------------------------------------------

    def _now_playing(self) -> NowPlaying | None:
        """What plays now - in the player here, else on a TV (a cast) - or
        None while nothing does. Every place asks this one."""
        lp = getattr(self, "_last_playback", None) or {}
        key = getattr(self, "_playing_key", None)
        if key is not None:
            item = getattr(self, "_playing_item", None) or lp.get("item") or {}
            frac = 0.0
            p = getattr(self, "player", None)
            try:
                if p is not None and p.playback_duration() > 0:
                    frac = p.playback_position() / p.playback_duration()
            except Exception:
                frac = 0.0
            return NowPlaying(key, str(getattr(self, "_playing_group", None) or ""),
                              str(lp.get("kind") or ""), item, lp.get("series_ctx"),
                              str(lp.get("url") or item.get("_path") or ""),
                              max(0.0, min(1.0, frac)), False, "")
        runs = getattr(self, "_cast_runs", None)
        ctx = getattr(self, "_cast_ctx", None) or {}
        if not callable(runs) or not runs() or ctx.get("key") is None:
            return None
        kind = str(ctx.get("kind") or "")
        group = ("live" if ctx.get("sid") is not None else str(ctx.get("group") or "")
                 or GROUP_OF.get(kind, ""))
        item = ctx.get("item") or {}
        frac = 0.0
        cast = getattr(self, "cast", None)
        try:
            dur = float(getattr(cast, "duration", 0.0) or 0.0)
            if dur > 0:
                frac = float(cast.position() or 0.0) / dur
        except Exception:
            frac = 0.0
        return NowPlaying(ctx.get("key"), group, kind, item, ctx.get("series_ctx"),
                          str(item.get("_path") or ctx.get("row_url") or ""),
                          max(0.0, min(1.0, frac)), True,
                          str(getattr(self, "_cast_device", None) or ""))

    def _now_key(self, *groups: str) -> str:
        """The key of what plays (here or on a TV) when it is of *groups*,
        else ""."""
        now = self._now_playing()
        if now is None or now.group not in groups:
            return ""
        return str(now.key)

    # -- the playback path's call ---------------------------------------------------

    def _pane_follow_soon(self, kind: str, key: Any, restart: bool) -> None:
        """_start_playback: *kind* / *key* started (*restart*: the same title
        again). The pane follows a moment later, once the stream is on its
        way."""
        if restart or self._play_origin == "pane" or kind not in FOLLOWED or key is None:
            self._pane_follow_job = None
            return
        self._pane_follow_job = (kind, str(key))
        QTimer.singleShot(0, self._pane_follow_run)

    def _pane_follow_run(self) -> None:
        job, self._pane_follow_job = self._pane_follow_job, None
        if job is None:
            return
        try:
            self._pane_follow(*job)
        except Exception:
            log.exception("the middle pane did not follow what plays")

    # -- the one place ----------------------------------------------------------------

    def _pane_follow(self, kind: str, key: str) -> bool:
        """The pane to *kind* / *key* when that is still what plays and the
        pane may move; True when it moved (or showed it already)."""
        lp = getattr(self, "_last_playback", None) or {}
        if (lp.get("kind") != kind or str(getattr(self, "_playing_key", None)) != key
                or not self._pane_may_follow()):
            return False
        if kind == "live":
            elsewhere = getattr(self, "_playing_elsewhere", None)
            if callable(elsewhere) and elsewhere():
                return False        # another source's channel: no row of these lists
        if str(getattr(self, "_pending_jump_key", None)) == key:
            return True             # on its way there already (the guide)
        if self._pane_shows(kind, key, lp):
            return True
        if kind == "episode":
            series = lp.get("series_ctx") or {}
            if series.get("series_id") is None or not self._pane_series_open(series):
                return False
            log.info("pane: follows an episode to its series' page")
            self._open_series_page(series, focus=key)
            return True
        log.info("pane: follows what plays (%s)", kind)
        self._pane_leave_pages()
        self._jump_to_now_playing()
        return True

    def _pane_may_follow(self) -> bool:
        """Not in TV mode, with no dialog or menu open, not while the person
        types in the window."""
        if getattr(self, "_tv_on", False):
            return False
        if QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None:
            return False
        active = QApplication.activeWindow()
        if isinstance(active, QDialog) and active is not self:
            return False
        return not (self.isActiveWindow() and _typing(QApplication.focusWidget()))

    def _pane_shows(self, kind: str, key: str, lp: dict) -> bool:
        """Whether the pane shows what plays now - the list holding its row
        (then selected), or the series' page of its series."""
        if (self._search_page_showing() or self._guide_page_showing()
                or getattr(self, "_mitt_showing", lambda: False)()):
            return False
        if self._series_page_showing():
            if kind != "episode":
                return False
            ctx = getattr(self, "_sp_ctx", None) or {}
            series = lp.get("series_ctx") or {}
            return (getattr(self, "_sp_local", None) is None
                    and str(ctx.get("series_id")) == str(series.get("series_id")))
        model = self.list_model
        listed = self._content_kind()
        # A list of one kind of row holds nothing of another (a channel list
        # of tens of thousands is not looked through for a film).
        only = _ONE_KIND.get(listed)
        if only is not None and only != kind:
            return False
        current = self.listw.currentIndex().row()

        def holds(row: int) -> bool:
            it = model.item_at(row)
            if not it or it.get("_header"):
                return False
            ident = self._item_key(it)
            if ident is None or str(ident) != key:
                return False
            own = it.get("_kind") or it.get("_ekind") or listed
            return _ROW_KIND.get(str(own), str(own)) == kind
        if current >= 0 and holds(current):
            return True                 # its own row, selected: a play from the list
        for row in range(model.rowCount()):
            if not holds(row):
                continue
            if row != current:
                from PyQt6.QtWidgets import QAbstractItemView
                idx = model.index(row)
                self.listw.setCurrentIndex(idx)
                self.listw.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)
                # Selecting a channel arms the preview: it plays already.
                self._preview_timer.stop()
            return True
        return False

    def _pane_series_open(self, series: dict) -> bool:
        """Whether its page may open without a question: never a PIN asked
        for (a locked category) because something played."""
        cat = series.get("category_id")
        overrides = getattr(self, "overrides", None)
        parental = getattr(self, "parental", None)
        if (cat is not None and overrides is not None and overrides.is_locked("series", cat)
                and not getattr(parental, "session_unlocked", False)):
            return False
        return True

    def _pane_leave_pages(self) -> None:
        """The Guide, Search and a series' page give way to the list, as
        their Back to it does."""
        self._close_guide_page()
        self._close_series_page()
        self._close_search_page()
        getattr(self, "_close_mitt_page", lambda: None)()
        self._check_nav(self._nav_key_for_mode(self.mode))

    # -- the jump to what plays: its card on screen ---------------------------------

    #: How often, and how long, the jump looks for the playing card while
    #: the place's rows come in (a folder read in slices, a list loading).
    _NP_STEP_MS = 150
    _NP_TRIES = 60

    def _np_cards(self) -> tuple[Any, Any] | None:
        """The place on screen that draws its list as cards, and its view:
        (page, view) - None for the TV place and the pages over the list,
        whose list scrolls itself."""
        place = getattr(self, "_place", "")
        if place == "local":
            page = getattr(self, "_lp_page", None)
            view = getattr(page, "view", None)
        elif place == "rec":
            page = getattr(self, "_rp_page", None)
            view = getattr(page, "view", None)
        elif place == "radio":
            page = getattr(self, "_rap_page", None)
            view = getattr(page, "view", None)
        elif place == "films":
            page = getattr(self, "_films_place", None)
            view = getattr(self, "_pf_view", None)
        elif place == "series":
            page = getattr(self, "_place_series", None)
            view = getattr(page, "grid", None)
        else:
            return None
        if page is None or view is None or sip.isdeleted(page) or not page.isVisible():
            return None
        return page, view

    def _np_reveal_soon(self, key: Any = None) -> None:
        """Start looking for the playing card (the jump to what plays) -
        *key*'s, what plays here or on a TV when not given."""
        if key is None:
            now = self._now_playing()
            key = now.key if now is not None else None
        self._np_gen = getattr(self, "_np_gen", 0) + 1
        QTimer.singleShot(self._NP_STEP_MS, functools.partial(
            self._np_reveal, self._np_gen, key, self._NP_TRIES))

    def _np_reveal(self, gen: int, key: Any, tries: int) -> None:
        """The playing card picked and scrolled into sight - once the rows
        are in and laid out: a card revealed while a folder was still being
        read was scrolled to where it was, and the rows after it moved it
        far out of sight. Given up on another jump, another title, or after
        a few seconds."""
        now = self._now_playing()
        if (gen != getattr(self, "_np_gen", 0) or key is None or now is None
                or now.key != key):
            return
        found = self._np_cards()
        busy = getattr(self, "_local_listing", None) is not None
        row = found[1].row_of(str(key)) if found is not None else -1
        if found is None or row < 0 or busy:
            if tries > 0:
                QTimer.singleShot(self._NP_STEP_MS, functools.partial(
                    self._np_reveal, gen, key, tries - 1))
            return
        page, view = found
        view.doItemsLayout()
        lay = getattr(page, "body", None) and page.body.layout()
        if lay is not None:
            lay.activate()
        view.focus_key(str(key), take_focus=False)
