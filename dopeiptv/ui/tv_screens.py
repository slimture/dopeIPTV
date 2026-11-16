"""TV mode's bigger screens (ui/tv_mode.py has the page and the others):
the guide - the app's own (ui/epg_grid.py) in large, run by the arrows -
films and series in posters with a series' episodes, and the search with
its keyboard on screen. What they show is the window's; OK hands what was
picked to the page, which plays it through the window's own path.

A series' episodes are laid out as on its page in the window
(ui/series_page.py, core/series_view.py): a row of seasons over the
season's episodes, each with its length, how far it got or that it was
watched, opened on the season of the episode to watch next - Left and
Right in the list go to the season before or after, Up to the row.
"""

from __future__ import annotations

from PyQt6 import sip
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPen
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea, QStackedWidget, QVBoxLayout,
    QWidget,
)

from ..core import series_view as sv
from ..core.log import log
from ..core.profiles import adult_category, adult_item
from ..core.workers import run_async
from ..i18n import tr
from .epg_grid import EpgGrid
from .theme import P
from .tv_mode import (
    ChannelsScreen, TvPage, TvScreen, _art, _kids, _set_image, _sig, now_next,
)
from .tv_widgets import TvGrid, TvKeyboard, TvList, TvShelf, TvTile, colour, font, px


class TvGuide(EpgGrid):
    """The app's guide in large: taller rows, larger names and titles, its
    bar left out (the remote has the keys for it)."""

    NAME_PT = 18

    def __init__(self, window, channels, title, on_play) -> None:
        # On the class: the scale's fit (auto_span) reads them there.
        cls = type(self)
        cls.ROW_H, cls.HEADER_H, cls.CH_COL_W = px(96), px(56), px(380)
        cls.MIN_HALF_HOUR_W, cls.LABEL_DY, cls.LABEL_PX = px(170), px(32), px(26)
        self._on_play = on_play
        super().__init__(window, channels, title)
        self._hide_chrome()

    def _chrome(self) -> list[QWidget]:
        return [self.title_lbl, self.scope_lbl, self.filter, self.popout_btn,
                self.search_btn, self.day_back_btn, self.now_btn,
                self.tonight_btn, self.day_fwd_btn, self.playing_btn,
                self._zoom_box, self.refresh_btn, self.reminders_btn,
                self.rec_sched_btn, self.play_btn, self.close_btn]

    def _hide_chrome(self) -> None:
        for wdg in self._chrome():
            wdg.hide()
            wdg.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def _fit_bar(self) -> None:
        self._zoom_box.hide()       # no bar here: never shown again by a resize

    def _highlight_item(self, item) -> None:
        # The focus ring, as on every tile: thick, in the accent.
        super()._highlight_item(item)
        if self._sel_outline is not None:
            self._sel_outline.setPen(QPen(colour("accent"), px(5)))

    def restyle(self) -> None:
        super().restyle()
        self.info.setFont(font(28))
        self.desc.setFont(font(24))
        self.desc.setMaximumHeight(px(96))
        self.info.setStyleSheet(f"color:{P['text']};")

    def _build(self) -> None:
        super()._build()
        if hasattr(self, "playing_btn"):
            self.playing_btn.hide()

    def refresh_playing(self) -> None:
        super().refresh_playing()
        self.playing_btn.hide()

    def _tune(self, ch: dict) -> None:
        self._on_play(ch, list(self._all_channels))
        self.played.emit()

    def row(self) -> int | None:
        return self._focus[0] if self._focus is not None else None


class GuideScreen(TvScreen):
    """The guide for the channels on show in Channels, else the favorite
    channels, else the whole line-up."""

    key = "guide"

    def __init__(self, page: TvPage) -> None:
        super().__init__(page)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self.note = self._note(tr("status_loading_channels"))
        self._lay.addWidget(self.note)
        self.grid: TvGuide | None = None
        self._gen = 0

    def enter(self) -> None:
        w = self.w
        chans = self.page.screens.get("channels")
        got = chans.lineup() if isinstance(chans, ChannelsScreen) else None
        if got is None:
            favs = list(w._favorite_channels())
            got = (tr("nav_favorites"), favs) if favs else None
        if got is not None:
            self._show(*got)
            return
        if self.grid is not None and not self.grid.stale():
            return
        self._gen += 1
        gen, client = self._gen, w.client
        keep = w._catalog_filter("live")
        kids = _kids(w)
        self.note.setText(tr("status_loading_channels"))
        self.note.show()

        def work():
            return gen, [c for c in client.live_streams(None) or []
                         if keep(c) and not (kids and adult_item(c))]

        run_async(w.pool, work, self._lineup_in, lambda _e: None)

    def rebuild(self) -> None:
        """Another profile (mw_profiles): the guide of its channels, made
        again - never the one the profile before was let see."""
        self._gen += 1
        if self.grid is not None:
            self._lay.removeWidget(self.grid)
            self.grid.hide()
            self.grid.deleteLater()
            self.grid = None
        self.note.setText(tr("status_loading_channels"))
        self.note.show()
        if self.page.current == self.key:
            self.enter()

    def _lineup_in(self, got) -> None:
        gen, rows = got
        if gen == self._gen:
            self._show(None, list(rows or []))

    def _show(self, title, channels: list) -> None:
        if sip.isdeleted(self):
            return
        sig = _sig(channels)
        if self.grid is not None and self.grid.scope_key == sig and not self.grid.stale():
            return
        if self.grid is not None:
            self._lay.removeWidget(self.grid)
            self.grid.hide()
            self.grid.deleteLater()
        self.w._ensure_xmltv_loaded()
        self.grid = TvGuide(self.w, channels, title, self.page.window._tv_play_channel)
        self.grid.scope_key = sig
        self._lay.addWidget(self.grid, 1)
        self.note.hide()
        if self.page.current == self.key and self.page.focus_widget() is None:
            self.page.focus_into()

    def focusables(self) -> list[QWidget]:
        return [self.grid.view] if self.grid is not None else []

    def first_focus(self) -> QWidget | None:
        if self.grid is None:
            return None
        if self.grid._focus is None:
            self.grid._nav(0, 0)
        return self.grid.view

    def move(self, cur: QWidget, direction: str) -> bool:
        g = self.grid
        if g is None or cur is not g.view:
            return False
        if g._focus is None:
            g._nav(0, 0)
            return True
        if direction == "up" and g.row() == 0:
            return False
        dc, dr = {"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1)}[direction]
        g._nav(dc, dr)
        return True

    def activate(self, cur: QWidget) -> None:
        g = self.grid
        if g is not None and cur is g.view:
            if g._selected is None:
                g._nav(0, 0)
            g._play_selected()

    def page_step(self, cur: QWidget | None, direction: str) -> bool:
        g = self.grid
        if g is None or cur is not g.view:
            return False
        rows = max(1, g.view.viewport().height() // max(1, g.ROW_H) - 1)
        g._nav(0, -rows if direction == "up" else rows)
        return True

    def focused_item(self, cur: QWidget | None) -> dict | None:
        g = self.grid
        if g is not None and g._selected:
            return g._selected.get("channel")
        return None


class MediaScreen(TvScreen):
    """Films or series: categories, then posters; a series opens its
    episodes."""

    def __init__(self, page: TvPage, kind: str) -> None:
        super().__init__(page)
        self.kind = kind
        self.key = kind
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
        self.stack = QStackedWidget()
        self.grid = TvGrid(self.w.poster_art, self._art)
        self.episodes = TvList(loader=None, logos=False, sub_fn=self._ep_sub, row_h=84)
        # A series: its seasons in a row over the season's episodes.
        self.ep_box = QWidget()
        box = QVBoxLayout(self.ep_box)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(px(12))
        self.season_area = QScrollArea()
        self.season_area.setWidgetResizable(True)
        self.season_area.setFrameShape(QFrame.Shape.NoFrame)
        self.season_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.season_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.season_area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.season_area.setStyleSheet("background: transparent;")
        row = QWidget()
        self.season_row = QHBoxLayout(row)
        self.season_row.setContentsMargins(0, 0, 0, 0)
        self.season_row.setSpacing(px(6))
        self.season_row.addStretch(1)
        self.season_area.setWidget(row)
        self.season_area.setFixedHeight(px(64 + 2 * TvTile.RING))
        box.addWidget(self.season_area)
        box.addWidget(self.episodes, 1)
        self.season_tiles: list[TvTile] = []
        self.seasons: list[sv.Season] = []
        self.season_at = -1
        self.queue: list[dict] = []
        self.next_key = ""
        self.ep_states: dict[str, tuple[bool, sv.Progress]] = {}
        self.stack.addWidget(self.grid)
        self.stack.addWidget(self.ep_box)
        right.addWidget(self.stack, 1)
        self.note = self._note(tr("status_loading_channels"))
        right.addWidget(self.note)
        lay.addLayout(right, 8)
        self.series: dict | None = None
        self._loaded = False
        self._gen = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self.load_items)
        self.cats.selectionModel().currentChanged.connect(self._cat_moved)

    def _art(self, it: dict):
        raw = it.get("cover") or it.get("stream_icon")
        try:
            return self.w.cover.cover_url(it, self.kind) or raw
        except Exception:
            return raw

    def _ep_sub(self, it: dict):
        """Under an episode: up next, its length, how far it got (the bar)
        or that it was watched."""
        key = sv.episode_key(it)
        watched, prog = self.ep_states.get(key, (False, sv.Progress()))
        parts = []
        if key and key == self.next_key:
            parts.append(tr("sp_up_next"))
        mins = sv.minutes_of(it)
        if mins:
            parts.append(tr("opt_minutes", n=mins))
        if watched:
            parts.append(tr("nav_watched"))
        elif prog.started and prog.minutes_left:
            parts.append(tr("home_min_left", n=prog.minutes_left))
        if not parts:
            info = it.get("info") or {}
            parts.append(str(info.get("duration") or info.get("releasedate") or ""))
        frac = prog.fraction if prog.started and not watched else None
        return " · ".join(p for p in parts if p), frac

    def enter(self) -> None:
        if self._loaded:
            if self.series is not None and self.seasons:
                self._ep_refresh()
            return
        self._loaded = True
        fn = (self.w.client.vod_categories if self.kind == "vod"
              else self.w.client.series_categories)
        run_async(self.w.pool, fn, self._cats_in, self._failed)

    def rebuild(self) -> None:
        """Another profile (mw_profiles): its categories and titles, read
        again - never what the one before was let see."""
        self._loaded = False
        self._gen += 1
        self._timer.stop()
        self.series = None
        self.cats.set_items([], "cat")
        self.grid.set_items([], self.kind)
        self.episodes.set_items([], "episode")
        self._set_seasons([])
        self.stack.setCurrentWidget(self.grid)
        self.head.setText("")
        if self.page.current == self.key:
            self.enter()

    def _failed(self, msg) -> None:
        log.info("tv: %s did not load: %s", self.kind, msg)
        self.note.setText(tr("tv_nothing_here"))
        self.note.show()

    def _cats_in(self, cats) -> None:
        w = self.w
        rows: list[dict] = [{"name": tr("cat_all"), "_cat": None}]
        unlocked = w.parental.session_unlocked
        kids = _kids(w)
        for c in cats or []:
            cid = c.get("category_id")
            if w.overrides.is_hidden(self.kind, cid):
                continue
            if w.overrides.is_locked(self.kind, cid) and not unlocked:
                continue
            name = w.overrides.display_name(self.kind, cid, c.get("category_name", "?"))
            if kids and adult_category(name):
                continue
            rows.append({"name": name, "_cat": cid})
        self.cats.set_items(rows, "cat", 1 if len(rows) > 1 else 0)
        self.load_items()

    def _cat_moved(self, *_a) -> None:
        self._timer.start()

    def load_items(self) -> None:
        self._timer.stop()
        cat = self.cats.current_item()
        if cat is None:
            return
        self._gen += 1
        gen = self._gen
        self.head.setText(str(cat.get("name") or ""))
        self.series = None
        self.stack.setCurrentWidget(self.grid)
        self.note.setText(tr("status_loading_channels"))
        self.note.show()
        w, cid, kind = self.w, cat.get("_cat"), self.kind
        fn = w.client.vod_streams if kind == "vod" else w.client.series_list
        keep = w._catalog_filter(kind)
        hidden = w._channel_hidden
        id_key = "stream_id" if kind == "vod" else "series_id"
        kids = _kids(w)

        def work():
            return gen, [c for c in (fn(cid) or []) if c.get(id_key) is not None
                         and keep(c) and not hidden(c, kind) and not (kids and adult_item(c))]

        run_async(w.pool, work, self._items_in, self._failed)

    def _items_in(self, got) -> None:
        gen, rows = got
        if gen != self._gen or sip.isdeleted(self):
            return
        rows = list(rows or [])
        self.grid.set_items(rows, self.kind)
        self.note.setVisible(not rows)
        if not rows:
            self.note.setText(tr("tv_nothing_here"))

    def open_series(self, series: dict) -> None:
        """*series*' episodes in place of the posters."""
        sid = series.get("series_id")
        if sid is None:
            return
        self._gen += 1
        gen = self._gen
        self.series = series
        self.head.setText(str(series.get("name") or series.get("title") or ""))
        self.note.setText(tr("status_loading_episodes"))
        self.note.show()
        client = self.w.client

        def work():
            return gen, series, client.series_info(sid)

        run_async(self.w.pool, work, self._episodes_in, self._failed)

    def _episodes_in(self, got) -> None:
        gen, series, info = got
        if gen != self._gen or sip.isdeleted(self):
            return
        rows = self.w._episode_rows(info or {}, series)
        seasons = sv.seasons(rows)
        self.queue = sv.flow(seasons)
        self._set_seasons(seasons)
        nxt = self._ep_refresh(draw=False)
        at = sv.opening_season(seasons, nxt, None)
        self.season_at = -1
        self._show_tv_season(at, nxt.episode)
        self.stack.setCurrentWidget(self.ep_box)
        self.note.setVisible(not rows)
        if not rows:
            self.note.setText(tr("tv_nothing_here"))
        if rows and self.page.current == self.key:
            self.page.set_focus(self.episodes)

    def _set_seasons(self, seasons: list) -> None:
        for tile in self.season_tiles:
            self.season_row.removeWidget(tile)
            tile.hide()
            tile.deleteLater()
        self.season_tiles = []
        self.seasons = list(seasons)
        self.season_at = -1
        for i, season in enumerate(self.seasons):
            if season.specials:
                text = tr("sp_specials")
            elif season.number is None:
                text = tr("sp_episodes")
            else:
                text = tr("local_season", n=season.number)
            tile = TvTile(text, w=150, h=64, kind="tab")
            tile.payload = ("season", i)
            tile.clicked.connect(self._season_clicked)
            self.season_row.insertWidget(i, tile)
            self.season_tiles.append(tile)
        self.season_area.setVisible(len(self.seasons) > 1)

    def _season_clicked(self) -> None:
        tile = self.sender()
        if isinstance(tile, TvTile) and isinstance(tile.payload, tuple):
            self._show_tv_season(int(tile.payload[1]))
            if self.episodes.count():
                self.page.set_focus(self.episodes)

    def _show_tv_season(self, i: int, focus: dict | None = None) -> None:
        if not 0 <= i < len(self.seasons):
            self.episodes.set_items([], "episode")
            return
        season = self.seasons[i]
        row = 0
        if focus is not None:
            key = sv.episode_key(focus)
            row = next((n for n, e in enumerate(season.episodes)
                        if sv.episode_key(e) == key), 0)
        self.season_at = i
        self.episodes.set_items(season.episodes, "episode", row)
        for n, tile in enumerate(self.season_tiles):
            tile.current = n == i
            tile.update()

    def _ep_refresh(self, draw: bool = True) -> sv.NextUp:
        """Every episode's marks and the one next, from the window (back
        from watching one, too)."""
        series = self.series or {}
        prog = getattr(self.w, "_sp_episode_progress", None)
        states: dict[str, tuple[bool, sv.Progress]] = {}
        for s in self.seasons:
            for ep in s.episodes:
                states[sv.episode_key(ep)] = (prog(ep, series) if prog is not None
                                              else (False, sv.Progress()))
        self.ep_states = states
        nxt = sv.next_up(self.queue,
                         lambda e: states.get(sv.episode_key(e), (False,))[0],
                         lambda e: states.get(sv.episode_key(e), (False, sv.Progress()))[1])
        self.next_key = sv.episode_key(nxt.episode) if nxt.episode is not None else ""
        if draw:
            self.episodes.viewport().update()
        return nxt

    def focusables(self) -> list[QWidget]:
        out: list[QWidget] = [self.cats]
        shown = self.stack.currentWidget()
        if shown is self.ep_box:
            if len(self.season_tiles) > 1:
                out.extend(self.season_tiles)
            if self.episodes.count():
                out.append(self.episodes)
        elif isinstance(shown, (TvGrid, TvList)) and shown.count():
            out.append(shown)
        return out

    def move(self, cur: QWidget, direction: str) -> bool:
        if (cur is self.episodes and direction in ("left", "right")
                and len(self.seasons) > 1):
            # Left/Right in the episodes: the season before or after.
            step = -1 if direction == "left" else 1
            if self.isRightToLeft():
                step = -step
            i = max(0, min(len(self.seasons) - 1, self.season_at + step))
            if i != self.season_at:
                self._show_tv_season(i)
            return True
        return super().move(cur, direction)

    def activate(self, cur: QWidget) -> None:
        if cur is self.cats:
            if self._timer.isActive() or self.series is not None:
                self.load_items()
            elif self.grid.count():
                self.page.set_focus(self.grid)
            return
        if cur is self.grid:
            it = self.grid.current_item()
            if not it:
                return
            if self.kind == "vod":
                self.page.run(("movie", it))
            else:
                self.open_series(it)
        elif cur is self.episodes:
            ep = self.episodes.current_item()
            if ep and self.series is not None:
                # Autoplay goes on through the seasons (Specials among
                # themselves), as from the series' page.
                specials = 0 <= self.season_at < len(self.seasons) \
                    and self.seasons[self.season_at].specials
                queue = sv.flow(self.seasons, specials=specials)
                self.page.run(("episode", ep, self.series, queue))

    def back(self, cur: QWidget | None) -> bool:
        if self.stack.currentWidget() is self.ep_box:
            self._gen += 1
            self.series = None
            cat = self.cats.current_item() or {}
            self.head.setText(str(cat.get("name") or ""))
            self.stack.setCurrentWidget(self.grid)
            self.note.setVisible(not self.grid.count())
            self.page.set_focus(self.grid if self.grid.count() else self.cats)
            return True
        if cur is self.grid:
            self.page.set_focus(self.cats)
            return True
        return False


class SearchScreen(TvScreen):
    """A search over the channels, films and series, typed on a keyboard on
    screen (or a real one)."""

    key = "search"
    MAX = 24

    def __init__(self, page: TvPage) -> None:
        super().__init__(page)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(px(20))
        self.query_lbl = QLabel("")
        self.query_lbl.setFont(font(40, bold=True))
        lay.addWidget(self.query_lbl)
        row = QHBoxLayout()
        row.setSpacing(px(36))
        self.keyboard = TvKeyboard()
        self.keyboard.pressed.connect(self.key_pressed)
        col = QVBoxLayout()
        col.addWidget(self.keyboard)
        col.addStretch(1)
        row.addLayout(col)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.area.setStyleSheet("background: transparent;")
        row.addWidget(self.area, 1)
        lay.addLayout(row, 1)
        self.query = ""
        self.shelves: list[TvShelf] = []
        self._index: dict[str, list] | None = None
        self._index_gen = 0
        self._loading = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(350)
        self._timer.timeout.connect(self.search)
        self._show_query()
        self._results([])

    def _show_query(self) -> None:
        if self.query:
            self.query_lbl.setText(self.query + "▏")
            self.query_lbl.setStyleSheet(f"color:{P['text']};")
        else:
            self.query_lbl.setText(tr("tv_search_hint"))
            self.query_lbl.setStyleSheet(f"color:{P['muted']};")

    def key_pressed(self, value: str) -> None:
        if value == "space":
            self.type(" ")
        elif value == "del":
            self.delete()
        elif value == "clear":
            self.query = ""
            self._changed()
        else:
            self.type(value)

    def type(self, text: str) -> None:
        self.query = (self.query + text)[:60]
        self._changed()

    def delete(self) -> bool:
        if not self.query:
            return False
        self.query = self.query[:-1]
        self._changed()
        return True

    def _changed(self) -> None:
        self._show_query()
        self._timer.start()

    def _load_index(self) -> None:
        if self._index is not None or self._loading:
            return
        self._loading = True
        w, client = self.w, self.w.client
        keeps = {k: w._catalog_filter(k) for k in ("live", "vod", "series")}
        kids, gen = _kids(w), self._index_gen

        def work():
            out: dict[str, list] = {}
            for kind, fn, id_key in (("live", client.live_streams, "stream_id"),
                                     ("vod", client.vod_streams, "stream_id"),
                                     ("series", client.series_list, "series_id")):
                try:
                    rows = fn(None) or []
                except Exception:
                    rows = []
                out[kind] = [r for r in rows if r.get(id_key) is not None and keeps[kind](r)
                             and not (kids and adult_item(r))]
            return gen, out

        run_async(w.pool, work, self._index_in, self._index_failed)

    def _index_failed(self, _e=None) -> None:
        self._index_in((self._index_gen, {}))

    def _index_in(self, got) -> None:
        gen, index = got
        if gen != self._index_gen or sip.isdeleted(self):
            return              # made for the profile before (rebuild)
        self._loading = False
        self._index = dict(index or {})
        if self.query:
            self.search()

    def rebuild(self) -> None:
        """Another profile (mw_profiles): what it searches is read again -
        never what the one before was let see - and the query goes."""
        self._index_gen += 1
        self._index = None
        self._loading = False
        self._timer.stop()
        self.query = ""
        self._show_query()
        self._results([])

    def search(self) -> None:
        self._timer.stop()
        q = self.query.strip().casefold()
        if not q:
            self._results([])
            return
        if self._index is None:
            self._load_index()
            return
        found = []
        for kind, title in (("live", tr("fav_channels")), ("vod", tr("nav_movies")),
                            ("series", tr("nav_series"))):
            rows = [r for r in self._index.get(kind, [])
                    if q in str(r.get("name") or r.get("title") or "").casefold()][:self.MAX]
            if rows:
                found.append((kind, title, rows))
        self._results(found)

    def _results(self, found: list) -> None:
        old = self.area.takeWidget()
        if old is not None:
            old.deleteLater()
        canvas = QWidget()
        canvas.setStyleSheet("background: transparent;")
        body = QVBoxLayout(canvas)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(px(24))
        self.shelves = []
        w = self.w
        for kind, title, rows in found:
            shelf = TvShelf(title)
            for it in rows:
                name = str(it.get("name") or it.get("title") or "?")
                if kind == "live":
                    tile = TvTile(name, now_next(w, it)[0], w=260, h=146, kind="logo")
                    tile.payload = ("channel", it, rows)
                    _set_image(tile, it.get("stream_icon"), w.logos)
                else:
                    tile = TvTile(name, "", w=180, h=270, kind="poster")
                    tile.payload = ("movie", it) if kind == "vod" else ("series", it)
                    _set_image(tile, _art(w, it, kind), w.poster_art)
                tile.clicked.connect(self._clicked)
                shelf.add(tile)
            body.addWidget(shelf)
            self.shelves.append(shelf)
        if self.query and not found and self._index is not None:
            body.addWidget(self._note(tr("tv_no_results")))
        body.addStretch(1)
        self.area.setWidget(canvas)

    def _clicked(self) -> None:
        tile = self.sender()
        if isinstance(tile, TvTile) and isinstance(tile.payload, tuple):
            self.page.run(tile.payload)

    def focusables(self) -> list[QWidget]:
        out: list[QWidget] = list(self.keyboard.keys)
        for shelf in self.shelves:
            out.extend(shelf.tiles())
        return out
