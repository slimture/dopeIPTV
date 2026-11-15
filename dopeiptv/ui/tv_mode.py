"""TV mode's screens: a full-screen layout of its own, run from a remote.

Along the top the tabs - Home, Channels, Guide, Movies, Series, Search,
Options - and under them the screen: Home's shelves (continue watching,
favorites, live now), categories and then their channels with what is on,
the guide (the app's own, larger), the films and series in posters, a
search with a keyboard on screen, and the few options that matter on a
TV. Inside safe margins (a TV's overscan cuts the edges off), in the
theme's colours with the accent as the focus ring.

Everything is reached with the arrows (core/tv_nav.py: a list moves its
own rows, else the nearest element that way; up from a screen's top is
its tab), OK plays or opens, Back goes back a step and then to Home. The
data is the window's (its stores, its client, its guide) and playing goes
through the window's own path (ui/mw_tv.py); nothing here plays itself.
Here: the page, the screens' base, Home, Channels and Options; the guide,
Movies/Series and Search are in ui/tv_screens.py.
"""

from __future__ import annotations

import time

from PyQt6 import sip
from PyQt6.QtCore import QPoint, QRect, Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QScrollArea, QStackedWidget,
    QVBoxLayout, QWidget,
)

from ..core import tv_nav
from ..core.log import log
from ..core.workers import run_async
from ..i18n import tr
from .theme import P
from .tv_widgets import (
    ColourLegend, TvGrid, TvList, TvShelf, TvTile, font,
    px, set_unit,
)


def now_next(window, it: dict) -> tuple[str, str, float | None]:
    """What is on a channel (its time and title), what comes next, and how
    far the one on air has got - from the guide, "" where it knows none."""
    try:
        progs = window.xmltv.listings_for(it, 2) or []
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
        lbl.setFont(font(34, bold=True))
        lbl.setStyleSheet(f"color:{P['text']};")
        return lbl

    def _note(self, text: str = "") -> QLabel:
        lbl = QLabel(text)
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
        out = list(w._visible_catalog(out, "live"))[:self.LIVE_MAX]
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

    def enter(self) -> None:
        if not self._loaded:
            self._loaded = True
            run_async(self.w.pool, self.w.client.live_categories,
                      self._cats_in, self._failed)
        else:
            self.chans.viewport().update()

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
        for c in cats or []:
            cid = c.get("category_id")
            if w.overrides.is_hidden("live", cid):
                continue
            if w.overrides.is_locked("live", cid) and not unlocked:
                continue
            rows.append({"name": w.overrides.display_name(
                "live", cid, c.get("category_name", "?")), "_cat": cid})
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

        def work():
            return gen, [c for c in (client.live_streams(cid) or [])
                         if c.get("stream_id") is not None and keep(c)
                         and not hidden(c, "live")]

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
    """What matters on a TV: the sound and subtitles of what plays, the
    sleep timer, starting in TV mode, and leaving it."""

    key = "settings"
    SLEEP_STEPS = (0, 15, 30, 45, 60, 90, 120)

    def __init__(self, page: "TvPage") -> None:
        super().__init__(page)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(px(6))
        self.tiles: dict[str, TvTile] = {}
        for key, label in (("audio", tr("opt_audio_track")),
                           ("subtitles", tr("opt_subtitles")),
                           ("sleep", tr("opt_sleep_timer")),
                           ("start", tr("tv_start_in")),
                           ("exit", tr("tv_exit"))):
            tile = TvTile(label, w=1100, h=96, kind="option")
            tile.payload = ("option", key)
            tile.clicked.connect(self._clicked)
            lay.addWidget(tile)
            self.tiles[key] = tile
        self.note = self._note("")
        lay.addWidget(self.note)
        lay.addStretch(1)

    def enter(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        w = self.w
        playing = w._tv_has_picture()
        for key in ("audio", "subtitles"):
            tile = self.tiles[key]
            tile.setEnabled(playing)
            tile.set_text(tile.title, w._tv_track_label(key) if playing else "")
        self.note.setText("" if playing else tr("tv_nothing_playing"))
        mins = w._tv_sleep_minutes()
        self.tiles["sleep"].set_text(self.tiles["sleep"].title,
                                     tr("opt_minutes", n=mins) if mins else tr("opt_off"))
        self.tiles["sleep"].setEnabled(playing)
        on = w.settings.value("tv_mode_start", "false") == "true"
        self.tiles["start"].set_text(self.tiles["start"].title,
                                     tr("option_on") if on else tr("option_off"))

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
        self.refresh()

    def focusables(self) -> list[QWidget]:
        return [t for t in self.tiles.values() if t.isEnabled()]


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
        self._tick()
        self._place_margins()

    # -- layout ------------------------------------------------------------------------

    @staticmethod
    def legend_labels() -> dict[str, str]:
        """What each colour key does (the phone remote's page says it too)."""
        return {"red": tr("tv_key_fav"), "green": tr("nav_guide"),
                "yellow": tr("tv_search"), "blue": tr("tv_options")}

    def _place_margins(self) -> None:
        w, h = max(1, self.width()), max(1, self.height())
        mx, my = int(w * self.SAFE), int(h * self.SAFE)
        self._outer.setContentsMargins(mx, my, mx, my)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._place_margins()
        self._place_flash()

    def _tick(self) -> None:
        self.clock.setText(time.strftime("%H:%M"))

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
        lbl.move(self.width() - mx - lbl.width(), my + px(84))

    # -- screens -----------------------------------------------------------------------

    def screen(self, key: str) -> TvScreen:
        scr = self.screens.get(key)
        if scr is None:
            from .tv_screens import GuideScreen, MediaScreen, SearchScreen
            if key in ("vod", "series"):
                scr = MediaScreen(self, key)
            else:
                kind = {"channels": ChannelsScreen, "guide": GuideScreen,
                        "search": SearchScreen, "settings": OptionsScreen}.get(key, HomeScreen)
                scr = kind(self)
            self.screens[key] = scr
            self.stack.addWidget(scr)
        return scr

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
        if w not in self.tabs and self.current:
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
        return list(self.tabs) + inside

    def _rect(self, w: QWidget) -> tv_nav.Rect:
        r = QRect(w.mapTo(self, QPoint(0, 0)), w.size())
        return (float(r.x()), float(r.y()), float(r.width()), float(r.height()))

    def move(self, direction: str) -> None:
        cur = self.focus_widget()
        if cur is None:
            self.focus_into()
            return
        scr = self.screens.get(self.current)
        if cur in self.tabs:
            if direction == "down":
                key = str(cur.payload[1]) if isinstance(cur.payload, tuple) else self.current
                if key != self.current:
                    self.show_screen(key)
                else:
                    self.focus_into()
                return
            if direction == "up":
                return
            cands: list[QWidget] = list(self.tabs)
        else:
            if scr is not None and scr.move(cur, direction):
                return
            cands = self.focusables()
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
        when one plays behind, else Home, and on Home to its tab."""
        cur = self.focus_widget()
        scr = self.screens.get(self.current)
        if scr is not None and cur is not None and cur not in self.tabs and scr.back(cur):
            return
        if self.window._tv_has_picture():
            self.window._tv_picture()
            return
        if self.current != "home":
            self.show_screen("home")
            return
        self.set_focus(self.tab("home"))

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
        self.hint.setStyleSheet(f"color:{P['muted']};")
        self.update()
