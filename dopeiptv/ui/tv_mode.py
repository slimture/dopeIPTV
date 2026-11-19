"""TV mode's screens: a full-screen layout of its own, run from a remote.

Along the top the tabs - Home, Channels, Guide, Movies, Series, Search,
Options - and under them the screen: Home's shelves (continue watching,
favorites, live now), categories and then their channels with what is on,
the guide (the app's own, larger), the films and series in posters, a
search with a keyboard on screen, and the few options that matter on a
TV - recording what plays among them. While a stream goes on behind the
page, what plays is in a row of its own above the tabs (Up from a tab; OK
there is the picture). Inside safe
margins (a TV's overscan cuts the edges off), in the theme's colours with
the accent as the focus ring.

Everything is reached with the arrows (core/tv_nav.py: a list moves its
own rows, else the nearest element that way; up from a screen's top is
its tab), OK plays or opens, Back goes back a step and then to Home, and
on Home asks to leave TV mode (pressed again, it leaves). The page is laid
out for the full screen before it is first drawn (``prepare``). The
data is the window's (its stores, its client, its guide) and playing goes
through the window's own path (ui/mw_tv.py); nothing here plays itself.
Here: the page, the screens' base, Home, Channels and Options; the guide,
Movies/Series and Search are in ui/tv_screens.py.
"""

from __future__ import annotations

import time

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QMargins, QPoint, QRect, QSize, Qt, QTimer
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QScrollArea, QStackedWidget,
    QVBoxLayout, QWidget,
)

from ..core import tv_nav
from ..core.log import log
from ..core.profiles import adult_category, adult_item
from ..core.workers import run_async
from ..i18n import tr
from .theme import P
from .tv_widgets import (
    ColourLegend, TvGrid, TvList, TvShelf, TvTile, font,
    px, set_unit,
)


def now_next(window, it: dict) -> tuple[str, str, float | None]:
    """What is on a channel (its time and title), what comes next, and how
    far the one on air has got - from the guide, "" where it knows none.
    Only a guide in memory: one not in yet is never loaded here, on the
    window's thread (a download and parse froze TV mode, and its loading
    strip had no end) - the window loads it on the pool and the screens
    show it once it is in (``TvPage.guide_in``)."""
    try:
        guide = window.xmltv
        if not guide.is_loaded():
            ensure = getattr(window, "_ensure_xmltv_loaded", None)
            if callable(ensure):
                ensure()
            return "", "", None
        progs = guide.listings_for(it, 2) or []
    except Exception:
        progs = []
    if not progs:
        return "", "", None
    now = time.time()
    first = progs[0]
    start, stop = float(first.get("start_timestamp") or 0), float(first.get("stop_timestamp") or 0)
    on = (f"{time.strftime('%H:%M', time.localtime(start))}–"
          f"{time.strftime('%H:%M', time.localtime(stop))}  {first.get('title') or ''}")
    frac = (now - start) / (stop - start) if stop > start and start <= now else None
    nxt = ""
    if len(progs) > 1:
        nxt = tr("tv_next", time=time.strftime(
            "%H:%M", time.localtime(float(progs[1].get("start_timestamp") or 0))),
            title=progs[1].get("title") or "")
    return on, nxt, frac


def _sig(channels: list) -> tuple:
    return tuple((c.get("stream_id"), c.get("name")) for c in channels)


def _kids(window) -> bool:
    """A kids profile in use: no category named for adults and nothing the
    provider marks for them, as in the window's own lists (mw_profiles)."""
    return bool(getattr(window, "_profile_kids", lambda: False)())


def _set_image(tile: TvTile, url, loader) -> None:
    """*tile*'s picture from *loader*, now or when it comes."""
    if not url or loader is None:
        return
    pm = loader.cache.get(url)
    if pm is not None:
        tile.set_pixmap(pm)
        return

    def got(pm, tile=tile) -> None:
        if not sip.isdeleted(tile) and pm is not None and not pm.isNull():
            tile.set_pixmap(pm)
    loader.get(url, got)


# -- screens ---------------------------------------------------------------------------

class TvScreen(QWidget):
    """One screen under the tabs. The page asks it what can take the focus,
    whether an arrow moves inside it (a list's rows), what OK and Back do
    there; everything else is the page's."""

    key = ""

    def __init__(self, page: "TvPage") -> None:
        super().__init__()
        self.page = page
        self.w = page.window

    def enter(self) -> None:
        """Shown: fill or refresh what it shows."""

    def focusables(self) -> list[QWidget]:
        return []

    def first_focus(self) -> QWidget | None:
        got = self.focusables()
        return got[0] if got else None

    def move(self, cur: QWidget, direction: str) -> bool:
        """An arrow inside the screen; False hands it to the page."""
        if isinstance(cur, (TvList, TvGrid)) and direction in ("up", "down"):
            return cur.step(direction)
        if isinstance(cur, TvGrid):
            return cur.step(direction)
        return False

    def activate(self, cur: QWidget) -> None:
        """OK on something that is not a tile (a list's row)."""

    def back(self, cur: QWidget | None) -> bool:
        """Back inside the screen; False leaves it."""
        return False

    def page_step(self, cur: QWidget | None, direction: str) -> bool:
        """Channel up/down: a page of a list."""
        if isinstance(cur, (TvList, TvGrid)):
            by = cur.page_rows() if isinstance(cur, TvList) else max(1, cur.columns() * 2)
            return cur.step(direction, by)
        return False

    def focused_item(self, cur: QWidget | None) -> dict | None:
        """The channel the focus is on (for the red key), or None."""
        if isinstance(cur, TvTile) and isinstance(cur.payload, tuple) \
                and cur.payload[0] == "channel":
            it = cur.payload[1]
            return it if isinstance(it, dict) else None
        if isinstance(cur, TvList) and cur.model_.kind == "live":
            return cur.current_item()
        return None

    def _title(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setTextFormat(Qt.TextFormat.PlainText)      # a category's, a series' name
        lbl.setFont(font(34, bold=True))
        lbl.setStyleSheet(f"color:{P['text']};")
        return lbl

    def _note(self, text: str = "") -> QLabel:
        lbl = QLabel(text)
        lbl.setTextFormat(Qt.TextFormat.PlainText)
        lbl.setFont(font(28))
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color:{P['muted']};")
        return lbl


class HomeScreen(TvScreen):
    """Continue watching, favorite channels and what is on now."""

    key = "home"
    LIVE_MAX = 18

    def __init__(self, page: "TvPage") -> None:
        super().__init__(page)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.area.setStyleSheet("background: transparent;")
        lay.addWidget(self.area)
        self.shelves: list[TvShelf] = []
        self.empty_tile: TvTile | None = None
        self._live: TvShelf | None = None
        self._lineup: list | None = None
        self._body: QVBoxLayout | None = None

    def enter(self) -> None:
        self.rebuild()

    def rebuild(self) -> None:
        old = self.area.takeWidget()
        if old is not None:
            old.deleteLater()
        canvas = QWidget()
        canvas.setStyleSheet("background: transparent;")
        body = QVBoxLayout(canvas)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(px(30))
        self._body = body
        self.shelves, self.empty_tile, self._live = [], None, None
        w = self.w
        try:
            rows = list(w._continue_rows())[:14]
        except Exception:
            rows = []
        if rows:
            shelf = TvShelf(tr("home_resume"))
            for it in rows:
                left = _minutes_left(it)
                tile = TvTile(str(it.get("name") or it.get("title") or "?"),
                              tr("home_min_left", n=left) if left else "",
                              w=400, h=225, kind="wide",
                              progress=float(it.get("_progress_pct") or 0) / 100.0)
                tile.payload = ("continue", it)
                tile.clicked.connect(self._clicked)
                kind = "episode" if it.get("_kind") == "episode" else "vod"
                _set_image(tile, _art(w, it, kind), w.poster_art)
                shelf.add(tile)
            self._add(shelf)
        try:
            favs = list(w._favorite_channels())[:24]
        except Exception:
            favs = []
        if favs:
            shelf = TvShelf(tr("nav_favorites"))
            for it in favs:
                shelf.add(self._channel_tile(it, favs))
            self._add(shelf)
        live = self._live_channels()
        shelf = TvShelf(tr("tv_live_now"))
        for it in live:
            shelf.add(self._channel_tile(it, live))
        self._live = shelf
        self._add(shelf)
        if not live:
            shelf.hide()
        if len(live) < self.LIVE_MAX:
            self._fetch_lineup()
        if not rows and not favs and not live:
            self.empty_tile = TvTile(tr("fav_channels"), w=420, h=90, kind="option")
            self.empty_tile.payload = ("screen", "channels")
            self.empty_tile.clicked.connect(self._clicked)
            body.addWidget(self._note(tr("tv_home_empty")))
            body.addWidget(self.empty_tile)
        body.addStretch(1)
        self.area.setWidget(canvas)

    def _add(self, shelf: TvShelf) -> None:
        if self._body is not None:
            self._body.addWidget(shelf)
        self.shelves.append(shelf)

    def _channel_tile(self, it: dict, lineup: list) -> TvTile:
        on, _next, frac = now_next(self.w, it)
        tile = TvTile(str(it.get("name") or "?"), on, w=300, h=170, kind="logo",
                      progress=frac)
        tile.payload = ("channel", it, lineup)
        tile.clicked.connect(self._clicked)
        _set_image(tile, it.get("stream_icon"), self.w.logos)
        return tile

    def _live_channels(self) -> list[dict]:
        """Channels watched lately, then the line-up's first: those with
        something on air first."""
        w = self.w
        seen: set[str] = set()
        out: list[dict] = []
        for e in list(getattr(w.history, "entries", [])):
            if e.get("_kind") == "live" and e.get("stream_id") is not None:
                sid = str(e.get("stream_id"))
                if sid not in seen:
                    seen.add(sid)
                    out.append(e)
        for it in self._lineup or []:
            sid = str(it.get("stream_id"))
            if sid not in seen:
                seen.add(sid)
                out.append(it)
        kids = _kids(w)
        out = [it for it in w._visible_catalog(out, "live")
               if not (kids and adult_item(it))][:self.LIVE_MAX]
        return sorted(out, key=lambda it: now_next(w, it)[2] is None)

    def _fetch_lineup(self) -> None:
        if self._lineup is not None:
            return
        self._lineup = []
        w, client = self.w, self.w.client
        keep = w._catalog_filter("live")

        def work():
            return [c for c in (client.live_streams(None) or [])[:400]
                    if c.get("stream_id") is not None and keep(c)][:self.LIVE_MAX]

        run_async(w.pool, work, self._lineup_in, lambda _e: None)

    def _lineup_in(self, chans) -> None:
        self._lineup = list(chans or [])
        if not self._lineup or sip.isdeleted(self):
            return
        shelf = self._live
        if self.empty_tile is not None or shelf is None or sip.isdeleted(shelf):
            # Built empty: built again, with what is on now.
            self.rebuild()
            if self.page.current == self.key:
                self.page.focus_into()
            return
        live = self._live_channels()
        have = {str(t.payload[1].get("stream_id")) for t in shelf.tiles()
                if isinstance(t.payload, tuple)}
        for it in live:
            if str(it.get("stream_id")) not in have:
                shelf.add(self._channel_tile(it, live))
        shelf.setVisible(bool(shelf.tiles()))

    def _clicked(self) -> None:
        tile = self.sender()
        if isinstance(tile, TvTile) and isinstance(tile.payload, tuple):
            self.page.run(tile.payload)

    def guide_in(self) -> None:
        """The guide is in: each channel tile says what is on, in place -
        the focus stays where it is."""
        for shelf in self.shelves:
            if sip.isdeleted(shelf):
                continue
            for tile in shelf.tiles():
                payload = tile.payload
                if isinstance(payload, tuple) and payload and payload[0] == "channel":
                    on, _next, frac = now_next(self.w, payload[1])
                    tile.progress = frac
                    tile.set_text(tile.title, on)

    def focusables(self) -> list[QWidget]:
        out: list[QWidget] = []
        for shelf in self.shelves:
            if not shelf.isHidden():
                out.extend(shelf.tiles())
        if self.empty_tile is not None:
            out.append(self.empty_tile)
        return out


class ChannelsScreen(TvScreen):
    """Categories on the left, the chosen one's channels on the right with
    what is on now and next."""

    key = "channels"

    def __init__(self, page: "TvPage") -> None:
        super().__init__(page)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(px(30))
        left = QVBoxLayout()
        left.addWidget(self._title(tr("tv_categories")))
        self.cats = TvList(logos=False, row_h=78)
        left.addWidget(self.cats, 1)
        lay.addLayout(left, 3)
        right = QVBoxLayout()
        self.head = self._title("")
        right.addWidget(self.head)
        self.chans = TvList(loader=self.w.logos, sub_fn=self._sub)
        right.addWidget(self.chans, 1)
        self.note = self._note(tr("status_loading_channels"))
        right.addWidget(self.note)
        lay.addLayout(right, 7)
        self._loaded = False
        self._gen = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.load_channels)
        self.cats.selectionModel().currentChanged.connect(self._cat_moved)

    def _sub(self, it: dict):
        on, nxt, frac = now_next(self.w, it)
        return "   ·   ".join(x for x in (on, nxt) if x), frac

    def guide_in(self) -> None:
        """The guide is in: the rows ask for now and next as they paint."""
        self.chans.viewport().update()

    def enter(self) -> None:
        if not self._loaded:
            self._loaded = True
            run_async(self.w.pool, self.w.client.live_categories,
                      self._cats_in, self._failed)
        else:
            self.chans.viewport().update()

    def rebuild(self) -> None:
        """Another profile (mw_profiles): its categories and channels, read
        again - never what the one before was let see."""
        self._loaded = False
        self._gen += 1
        self._timer.stop()
        self.cats.set_items([], "cat")
        self.chans.set_items([], "live")
        self.head.setText("")
        self.note.setText(tr("status_loading_channels"))
        self.note.show()
        if self.page.current == self.key:
            self.enter()

    def _failed(self, msg) -> None:
        log.info("tv: channels did not load: %s", msg)
        self.note.setText(tr("detail_epg_failed"))
        self.note.show()

    def _cats_in(self, cats) -> None:
        w = self.w
        rows: list[dict] = []
        if w._favorite_channels():
            rows.append({"name": tr("nav_favorites"), "_cat": "__fav__"})
        rows.append({"name": tr("cat_all"), "_cat": None})
        unlocked = w.parental.session_unlocked
        kids = _kids(w)
        for c in cats or []:
            cid = c.get("category_id")
            if w.overrides.is_hidden("live", cid):
                continue
            if w.overrides.is_locked("live", cid) and not unlocked:
                continue
            name = w.overrides.display_name("live", cid, c.get("category_name", "?"))
            if kids and adult_category(name):
                continue
            rows.append({"name": name, "_cat": cid})
        # Favorites first when there are some, else the first real category
        # (as the window's list lands), else All.
        start = 0
        if rows[0].get("_cat") != "__fav__":
            start = next((i for i, r in enumerate(rows) if r.get("_cat") is not None), 0)
        self.cats.set_items(rows, "cat", start)
        self.load_channels()

    def _cat_moved(self, *_a) -> None:
        self._timer.start()

    def load_channels(self) -> None:
        self._timer.stop()
        cat = self.cats.current_item()
        if cat is None:
            return
        self._gen += 1
        gen = self._gen
        self.head.setText(str(cat.get("name") or ""))
        w = self.w
        if cat.get("_cat") == "__fav__":
            self._chans_in((gen, list(w._favorite_channels())))
            return
        self.note.setText(tr("status_loading_channels"))
        self.note.show()
        client, cid = w.client, cat.get("_cat")
        keep = w._catalog_filter("live")
        hidden = w._channel_hidden
        kids = _kids(w)

        def work():
            return gen, [c for c in (client.live_streams(cid) or [])
                         if c.get("stream_id") is not None and keep(c)
                         and not hidden(c, "live") and not (kids and adult_item(c))]

        run_async(w.pool, work, self._chans_in, self._failed)

    def _chans_in(self, got) -> None:
        gen, rows = got
        if gen != self._gen or sip.isdeleted(self):
            return
        rows = list(rows or [])
        self.chans.set_items(rows, "live", self._playing_row(rows))
        self.note.setVisible(not rows)
        if not rows:
            self.note.setText(tr("tv_nothing_here"))

    def _playing_row(self, rows: list) -> int:
        key = getattr(self.w, "_playing_key", None)
        for i, it in enumerate(rows):
            if key is not None and it.get("stream_id") == key:
                return i
        return 0

    def focusables(self) -> list[QWidget]:
        out: list[QWidget] = [self.cats]
        if self.chans.count():
            out.append(self.chans)
        return out

    def activate(self, cur: QWidget) -> None:
        if cur is self.cats:
            if self._timer.isActive():
                self.load_channels()
            if self.chans.count():
                self.page.set_focus(self.chans)
        elif cur is self.chans:
            it = self.chans.current_item()
            if it:
                self.page.run(("channel", it, self.chans.items()))

    def back(self, cur: QWidget | None) -> bool:
        if cur is self.chans:
            self.page.set_focus(self.cats)
            return True
        return False

    def lineup(self) -> tuple[str, list] | None:
        """The channels on show, for the guide."""
        if self.chans.count():
            cat = self.cats.current_item() or {}
            return str(cat.get("name") or ""), self.chans.items()
        return None


class OptionsScreen(TvScreen):
    """What matters on a TV: the sound and subtitles of what plays,
    recording it, the sleep timer, starting in TV mode, and leaving it.
    Record is the player's record button's (ui/mw_tv.py ``_tv_record``):
    only while a live channel plays, and while it records, Stop recording
    with how long it has run. The tiles scroll when the screen has less
    room than they need (two profiles, a small screen)."""

    key = "settings"
    SLEEP_STEPS = (0, 15, 30, 45, 60, 90, 120)
    #: How often Record's time is said again while the screen shows.
    REC_TICK_MS = 10_000

    def __init__(self, page: "TvPage") -> None:
        super().__init__(page)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.area.setStyleSheet("background: transparent;")
        lay.addWidget(self.area)
        canvas = QWidget()
        canvas.setStyleSheet("background: transparent;")
        body = QVBoxLayout(canvas)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(px(6))
        self.tiles: dict[str, TvTile] = {}
        for key, label in (("audio", tr("opt_audio_track")),
                           ("subtitles", tr("opt_subtitles")),
                           ("record", tr("rec_record")),
                           ("sleep", tr("opt_sleep_timer")),
                           ("profile", tr("profile_who")),
                           ("start", tr("tv_start_in")),
                           ("exit", tr("tv_exit"))):
            tile = TvTile(label, w=1100, h=96, kind="option")
            # Narrower where the screen is: the area scrolls down, never sideways.
            tile.set_width(tile.width(), shrink=True)
            tile.payload = ("option", key)
            tile.clicked.connect(self._clicked)
            body.addWidget(tile)
            self.tiles[key] = tile
        self.note = self._note("")
        body.addWidget(self.note)
        body.addStretch(1)
        self.area.setWidget(canvas)
        self._rec_timer = QTimer(self)
        self._rec_timer.setInterval(self.REC_TICK_MS)
        self._rec_timer.timeout.connect(self._rec_tick)

    def enter(self) -> None:
        self.refresh()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._rec_timer.start()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._rec_timer.stop()

    def _rec_tick(self) -> None:
        if not self.isVisible():
            self._rec_timer.stop()
            return
        self.refresh_rec()

    def refresh_rec(self) -> None:
        """Record's tile as it is now: Record and the channel, Stop
        recording with its minutes and what it records, or not to be
        pressed (nothing live plays)."""
        get = getattr(self.w, "_tv_rec_tile", None)
        title, sub, on = get() if callable(get) else (tr("rec_record"), "", False)
        tile = self.tiles["record"]
        tile.set_text(title, sub)
        tile.setEnabled(on)

    def refresh(self) -> None:
        w = self.w
        playing = w._tv_has_picture()
        for key in ("audio", "subtitles"):
            tile = self.tiles[key]
            tile.setEnabled(playing)
            tile.set_text(tile.title, w._tv_track_label(key) if playing else "")
        self.refresh_rec()
        self.note.setText("" if playing else tr("tv_nothing_playing"))
        mins = w._tv_sleep_minutes()
        self.tiles["sleep"].set_text(self.tiles["sleep"].title,
                                     tr("opt_minutes", n=mins) if mins else tr("opt_off"))
        self.tiles["sleep"].setEnabled(playing)
        on = w.settings.value("tv_mode_start", "false") == "true"
        self.tiles["start"].set_text(self.tiles["start"].title,
                                     tr("option_on") if on else tr("option_off"))
        # Who is watching: only with two profiles or more.
        prof = w._profile() if getattr(w, "_profile_many", lambda: False)() else None
        self.tiles["profile"].setVisible(prof is not None)
        if prof is not None:
            self.tiles["profile"].set_text(self.tiles["profile"].title, prof.name)
        # Leaving: its key on a keyboard beside it (Ctrl+Shift+T).
        key = getattr(w, "_tv_leave_key_text", lambda: "")()
        self.tiles["exit"].set_text(self.tiles["exit"].title, key)

    def _clicked(self) -> None:
        tile = self.sender()
        if not isinstance(tile, TvTile) or not isinstance(tile.payload, tuple):
            return
        key, w = tile.payload[1], self.w
        if key in ("audio", "subtitles"):
            w._tv_cycle_track(key)
        elif key == "sleep":
            mins = w._tv_sleep_minutes()
            nxt = next((m for m in self.SLEEP_STEPS if m > mins), 0)
            w._tv_set_sleep(nxt)
        elif key == "start":
            on = w.settings.value("tv_mode_start", "false") == "true"
            w.settings.setValue("tv_mode_start", "false" if on else "true")
        elif key == "exit":
            QTimer.singleShot(0, w.exit_tv_mode)
            return
        elif key == "record":
            # Its questions after this press is done: a press from the
            # phone (tv_command) gets its answer at once, never one held
            # until the question on the TV is answered.
            QTimer.singleShot(0, w._tv_record)
            return
        elif key == "profile":
            self.page.show_screen("profiles")
            return
        self.refresh()

    def focusables(self) -> list[QWidget]:
        return [t for t in self.tiles.values() if t.isEnabled() and not t.isHidden()]


def _minutes_left(it: dict) -> int:
    try:
        pos = float(it.get("_resume_pos") or 0)
        dur = float(it.get("_resume_dur") or 0)
    except (TypeError, ValueError):
        return 0
    return max(1, round((dur - pos) / 60)) if dur > pos > 0 else 0


def _art(w, it: dict, kind: str):
    ctx = it.get("_series_ctx") or {}
    raw = (it.get("cover") or it.get("stream_icon")
           or ctx.get("cover") or ctx.get("stream_icon"))
    try:
        return w.cover.cover_url(it, kind) or raw
    except Exception:
        return raw


# -- the page ----------------------------------------------------------------------------

class TvPage(QWidget):
    """TV mode's whole window: the tabs, the screen under them, what the
    colour keys do. ``handle`` takes an action while no picture shows."""

    TABS: tuple[tuple[str, str], ...] = (
        ("home", "nav_home"), ("channels", "fav_channels"), ("guide", "nav_guide"),
        ("vod", "nav_movies"), ("series", "nav_series"), ("search", "tv_search"),
        ("settings", "tv_options"))
    # The share of the screen kept clear at each edge: a TV's overscan.
    SAFE = 0.045

    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.setObjectName("TvPage")
        screen = QApplication.primaryScreen()
        set_unit(screen.geometry().height() if screen is not None else 1080)
        self.setAutoFillBackground(True)
        self.setStyleSheet(f"#TvPage {{ background: {P['bg']}; }}")
        outer = QVBoxLayout(self)
        outer.setSpacing(px(22))
        self._outer = outer
        # What plays behind the page, in a row of its own above the tabs
        # (beside them it pushed the row off a 1080p screen): OK on it is
        # the picture again, so are Play and Back. As wide as its words up
        # to the page's, then cut at the end; hidden, it takes no room.
        self.now = TvTile("", w=150, h=64, kind="tab")
        self.now.payload = ("now",)
        self.now.clicked.connect(self._now_clicked)
        self.now.hide()
        outer.addWidget(self.now, 0, Qt.AlignmentFlag.AlignLeft)
        head = QHBoxLayout()
        head.setSpacing(px(6))
        self.tabs: list[TvTile] = []
        for key, label in self.TABS:
            text = tr(label)
            tab = TvTile(text, w=150, h=64, kind="tab")
            tab.payload = ("tab", key)
            tab.clicked.connect(self._tab_clicked)
            head.addWidget(tab)
            self.tabs.append(tab)
        head.addStretch(1)
        # The source the screens show, muted beside the clock - a household
        # with several sources sees which one it is on (cut when long).
        self.source_lbl = QLabel("")
        self.source_lbl.setFont(font(24))
        self.source_lbl.setStyleSheet(f"color:{P['muted']};")
        self.source_lbl.hide()
        head.addWidget(self.source_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addSpacing(px(20))
        self.clock = QLabel("")
        self.clock.setFont(font(34, bold=True))
        self.clock.setStyleSheet(f"color:{P['text2']};")
        head.addWidget(self.clock)
        outer.addLayout(head)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        foot = QHBoxLayout()
        self.legend = ColourLegend(self.legend_labels())
        foot.addWidget(self.legend, 1)
        self.hint = QLabel(tr("tv_hint"))
        self.hint.setFont(font(22))
        self.hint.setStyleSheet(f"color:{P['muted']};")
        foot.addWidget(self.hint)
        outer.addLayout(foot)
        self.flash_lbl = QLabel("", self)
        self.flash_lbl.setFont(font(34, bold=True))
        self.flash_lbl.setStyleSheet(
            f"background:{P['accent']}; color:{P['on_accent']};"
            f" border-radius:{px(14)}px; padding:{px(10)}px {px(24)}px;")
        self.flash_lbl.hide()
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self.flash_lbl.hide)
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(15_000)
        self._clock_timer.timeout.connect(self._tick)
        self._clock_timer.start()
        self.screens: dict[str, TvScreen] = {}
        self.current = ""
        self._focus: QWidget | None = None
        self._last: dict[str, QWidget] = {}
        # Shown from the picture for a look (the guide's key over it): Back
        # returns to the picture (ui/mw_tv.py _tv_browse).
        self.picture_back = False
        self._tick()
        self.refresh_source()
        self._place_margins()

    # -- layout ------------------------------------------------------------------------

    @staticmethod
    def legend_labels() -> dict[str, str]:
        """What each colour key does (the phone remote's page says it too)."""
        return {"red": tr("tv_key_fav"), "green": tr("nav_guide"),
                "yellow": tr("tv_search"), "blue": tr("tv_options")}

    def _place_margins(self, size: QSize | None = None) -> None:
        w = max(1, size.width() if size is not None else self.width())
        h = max(1, size.height() if size is not None else self.height())
        mx, my = int(w * self.SAFE), int(h * self.SAFE)
        if self._outer.contentsMargins() != QMargins(mx, my, mx, my):
            self._outer.setContentsMargins(mx, my, mx, my)

    def prepare(self, size: QSize) -> None:
        """Laid out for *size* now - its safe margins and every layout in it
        - so that the page is drawn complete the first time it is drawn."""
        if size.isEmpty():
            return
        self._place_margins(size)
        if self.size() != size:
            self.resize(size)
        QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        lay = self.layout()
        if lay is not None:
            lay.activate()
        self._place_flash()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._place_margins()
        self._place_flash()

    def _tick(self) -> None:
        self.clock.setText(time.strftime("%H:%M"))
        self.refresh_now()

    def refresh_now(self) -> None:
        """The Now playing row: shown, with what plays, while a stream goes
        on behind the page; the hint at the foot says how to get back."""
        w = self.window
        title = w._tv_now_title() if w._tv_has_picture() else ""
        playing = bool(title)
        if playing:
            text = f"\u25b6  {title}"
            if text != self.now.title:
                self.now.set_text(text)
                ring = px(TvTile.RING)
                words = QFontMetrics(font(28, bold=True)).horizontalAdvance(text)
                self.now.set_width(words + px(56) + 2 * ring, shrink=True)
        elif self.focus_widget() is self.now:
            self._to_current_tab()
        self.now.setVisible(playing)
        self.hint.setText(self.hint_text(playing))
        self._place_flash()

    def hint_text(self, playing: bool) -> str:
        """The foot's hint: what OK and Back do here - and, but for a look
        from the picture (Back returns to it), how TV mode is left."""
        if playing and self.picture_back:
            return tr("tv_hint_playing")
        return f"{tr('tv_hint_now') if playing else tr('tv_hint')} · {tr('tv_hint_leave')}"

    def now_shown(self) -> bool:
        return not self.now.isHidden()

    #: The widest the source's name grows beside the clock.
    SOURCE_W = 420

    def refresh_source(self) -> None:
        """The source's name beside the clock, as it is now ("" hides it)."""
        get = getattr(self.window, "_tv_source_name", None)
        name = get() if callable(get) else ""
        lbl = self.source_lbl
        text = QFontMetrics(lbl.font()).elidedText(
            name, Qt.TextElideMode.ElideRight, px(self.SOURCE_W))
        lbl.setText(text)
        lbl.setToolTip(name if text != name else "")
        lbl.setAccessibleName(name)
        lbl.setVisible(bool(name))

    def _to_current_tab(self) -> None:
        tab = self.tab(self.current)
        if tab is not None:
            self.set_focus(tab)
        else:
            self.focus_into()

    def _now_clicked(self) -> None:
        self.window._tv_picture()

    def flash(self, text: str, ms: int = 2000) -> None:
        """A word over the page's corner: a number typed, a favorite added."""
        self.flash_lbl.setText(text)
        self.flash_lbl.adjustSize()
        self._place_flash()
        self.flash_lbl.show()
        self.flash_lbl.raise_()
        self._flash_timer.start(max(500, ms))

    def _place_flash(self) -> None:
        lbl = self.flash_lbl
        mx, my = int(self.width() * self.SAFE), int(self.height() * self.SAFE)
        top = my + px(84)               # under the tabs
        if self.now_shown():
            top += self.now.height() + self._outer.spacing()
        lbl.move(self.width() - mx - lbl.width(), top)

    # -- screens -----------------------------------------------------------------------

    def screen(self, key: str) -> TvScreen:
        scr = self.screens.get(key)
        if scr is None:
            from .tv_screens import GuideScreen, MediaScreen, SearchScreen
            if key in ("vod", "series"):
                scr = MediaScreen(self, key)
            elif key == "profiles":
                from .profiles_ui import tv_profiles_screen
                scr = tv_profiles_screen(self)
            else:
                kind = {"channels": ChannelsScreen, "guide": GuideScreen,
                        "search": SearchScreen, "settings": OptionsScreen}.get(key, HomeScreen)
                scr = kind(self)
            self.screens[key] = scr
            self.stack.addWidget(scr)
        return scr

    def guide_in(self) -> None:
        """The programme guide came in: the screens made so far show what is
        on (they never load it themselves - ``now_next``)."""
        for scr in list(self.screens.values()):
            fn = getattr(scr, "guide_in", None)
            if callable(fn) and not sip.isdeleted(scr):
                fn()

    def show_screen(self, key: str, focus: bool = True) -> TvScreen:
        scr = self.screen(key)
        self.current = key
        self.stack.setCurrentWidget(scr)
        for tab in self.tabs:
            tab.current = tab.payload == ("tab", key)
            tab.update()
        scr.enter()
        if focus:
            self.focus_into()
        return scr

    def tab(self, key: str) -> TvTile | None:
        return next((t for t in self.tabs if t.payload == ("tab", key)), None)

    def _tab_clicked(self) -> None:
        tab = self.sender()
        if getattr(self.window, "_profile_locked", lambda: False)():
            return          # who is watching first (a click, as handle's keys)
        if isinstance(tab, TvTile) and isinstance(tab.payload, tuple):
            self.show_screen(str(tab.payload[1]))

    # -- focus -------------------------------------------------------------------------

    def focus_widget(self) -> QWidget | None:
        """What has the focus in the page: Qt's own when it is in here,
        else the one last given it (a window not active yet)."""
        fw = QApplication.focusWidget()
        if fw is not None and self.isAncestorOf(fw):
            return fw
        f = self._focus
        if f is not None and not sip.isdeleted(f) and f.isVisibleTo(self) and self.isAncestorOf(f):
            return f
        return None

    def set_focus(self, w: QWidget | None) -> None:
        if w is None or sip.isdeleted(w):
            return
        self._focus = w
        w.setFocus(Qt.FocusReason.OtherFocusReason)
        if w not in self.tabs and w is not self.now and self.current:
            self._last[self.current] = w
        w.update()

    def focus_into(self) -> None:
        """The focus into the screen: where it was last, else its first
        element, else its tab."""
        scr = self.screens.get(self.current)
        if scr is None:
            return
        last = self._last.get(self.current)
        cands = scr.focusables()
        if last is not None and not sip.isdeleted(last) and last in cands:
            self.set_focus(last)
            return
        first = scr.first_focus()
        self.set_focus(first if first is not None else self.tab(self.current))

    def focusables(self) -> list[QWidget]:
        scr = self.screens.get(self.current)
        inside = [w for w in (scr.focusables() if scr else [])
                  if not sip.isdeleted(w) and w.isVisibleTo(self) and w.isEnabled()]
        head: list[QWidget] = [self.now] if self.now_shown() else []
        return head + list(self.tabs) + inside

    def _rect(self, w: QWidget) -> tv_nav.Rect:
        r = QRect(w.mapTo(self, QPoint(0, 0)), w.size())
        return (float(r.x()), float(r.y()), float(r.width()), float(r.height()))

    def move(self, direction: str) -> None:
        cur = self.focus_widget()
        if cur is None:
            self.focus_into()
            return
        scr = self.screens.get(self.current)
        if cur is self.now:
            # Alone in its row: Down to the screen's tab, nothing else.
            if direction == "down":
                self._to_current_tab()
            return
        if cur in self.tabs:
            if direction == "down":
                key = str(cur.payload[1]) if isinstance(cur.payload, tuple) \
                    and len(cur.payload) > 1 else self.current
                if key != self.current:
                    self.show_screen(key)
                else:
                    self.focus_into()
                return
            if direction == "up":
                if self.now_shown():
                    self.set_focus(self.now)
                return
            cands: list[QWidget] = list(self.tabs)
        else:
            if scr is not None and scr.move(cur, direction):
                return
            cands = [c for c in self.focusables() if c is not self.now]
            if direction != "up":
                # Only Up leaves a screen for its tabs.
                cands = [c for c in cands if c not in self.tabs]
        others = [w for w in cands if w is not cur]
        i = tv_nav.pick(self._rect(cur), [self._rect(w) for w in others], direction)
        target = others[i] if i is not None else None
        if cur not in self.tabs and (target in self.tabs or (target is None and direction == "up")):
            # Up out of a screen: to its own tab.
            target = self.tab(self.current)
        if target is not None:
            self.set_focus(target)

    # -- actions -----------------------------------------------------------------------

    def handle(self, action: str) -> bool:
        """An action while the page shows; False when it does nothing here."""
        if getattr(self.window, "_profile_locked", lambda: False)():
            # Who is watching first (a profile with a PIN at start): only
            # the profiles' tiles, the arrows between them and OK.
            if self.current != "profiles":
                self.show_screen("profiles")
            scr = self.screens.get("profiles")
            tiles = scr.focusables() if scr is not None else []
            cur = self.focus_widget()
            if action in ("left", "right") and tiles:
                i = tiles.index(cur) if cur in tiles else 0
                i = max(0, min(len(tiles) - 1, i + (1 if action == "right" else -1)))
                self.set_focus(tiles[i])
            elif action == "ok" and cur in tiles:
                cur.click()
            elif cur not in tiles and tiles:
                self.set_focus(tiles[0])
            return True
        if action in tv_nav.DIRECTIONS:
            self.move(action)
            return True
        cur = self.focus_widget()
        scr = self.screens.get(self.current)
        if action == "ok":
            if cur is None:
                self.focus_into()
            elif isinstance(cur, TvTile):
                cur.click()
            elif scr is not None:
                scr.activate(cur)
            return True
        if action == "back":
            self.back()
            return True
        if action in ("menu", "blue"):
            self.show_screen("settings")
            return True
        if action in ("green", "guide"):
            self.show_screen("guide")
            return True
        if action in ("yellow", "search"):
            self.show_screen("search")
            return True
        if action == "home":
            self.show_screen("home")
            return True
        if action in ("ch_up", "ch_down"):
            if scr is not None:
                scr.page_step(cur, "up" if action == "ch_up" else "down")
            return True
        if action in ("red", "fav"):
            it = scr.focused_item(cur) if scr is not None else None
            if it is not None:
                self.window._tv_toggle_favorite(it)
            return True
        return False

    def back(self) -> None:
        """A step back inside the screen, else out of it: to the picture
        after a look from it (or from Now playing), else to Home - and on
        Home, Back asks to leave TV mode, and leaves it when pressed again.
        Back always leads out: never round between the page and the picture."""
        cur = self.focus_widget()
        scr = self.screens.get(self.current)
        if scr is not None and cur is not None and cur not in self.tabs \
                and cur is not self.now and scr.back(cur):
            return
        w = self.window
        if w._tv_has_picture() and (self.picture_back or cur is self.now):
            w._tv_picture()
            return
        if self.current != "home":
            self.show_screen("home")
            return
        self.set_focus(self.tab("home"))
        leave = getattr(w, "_tv_back_at_home", None)
        if callable(leave):
            leave()

    def run(self, payload: tuple) -> None:
        """What a tile stands for, done: play, open a series, a screen."""
        w = self.window
        what = payload[0]
        if what == "channel":
            w._tv_play_channel(payload[1], payload[2] if len(payload) > 2 else None)
        elif what == "continue":
            w._tv_play_continue(payload[1])
        elif what == "movie":
            w._tv_play_movie(payload[1])
        elif what == "episode":
            w._tv_play_episode(payload[1], payload[2], payload[3])
        elif what == "series":
            scr = self.show_screen("series", focus=False)
            open_series = getattr(scr, "open_series", None)
            if open_series is not None:
                open_series(payload[1])
            self.focus_into()
        elif what == "screen":
            self.show_screen(str(payload[1]))

    def search_screen(self):
        """The search screen while it shows (keys type into it), else None."""
        scr = self.screens.get("search")
        return scr if self.current == "search" and hasattr(scr, "type") else None

    def restyle(self) -> None:
        self.setStyleSheet(f"#TvPage {{ background: {P['bg']}; }}")
        self.clock.setStyleSheet(f"color:{P['text2']};")
        self.source_lbl.setStyleSheet(f"color:{P['muted']};")
        self.hint.setStyleSheet(f"color:{P['muted']};")
        self.update()
