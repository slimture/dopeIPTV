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
  series, a film's page and the Movies place (ui/mw_film_page.py), the
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
"""

from __future__ import annotations

import functools
from contextlib import contextmanager
from typing import Any

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
        if self._search_page_showing() or self._guide_page_showing():
            return False
        if self._film_page_showing():
            # A film's page shows that film, or the place under it follows.
            return kind == "movie" and self._fp_key() == key
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
        self._close_film_page()
        self._close_search_page()
        self._check_nav(self._nav_key_for_mode(self.mode))
