"""Home: a full-window start page - hero posters and horizontal shelves
(continue watching, favorites now, recently viewed, recently added) in the
style of modern TV apps. Lives in the central stack ON TOP of the classic
three-column view: showing it swaps the whole window content, clicking
anything (or Esc) drops back to the classic view and acts there.

Data comes exclusively from services the window already owns (resume,
history, favorites, EPG, the provider client and the cover-art pipeline),
so this module is pure presentation + wiring.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap,
)
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from ..core.workers import run_async
from ..i18n import tr
from .theme import ACCENT, P

# Card geometry (image area; the card adds a text strip underneath).
HERO_W, HERO_H = 300, 420          # big hero posters
POSTER_W, POSTER_H = 160, 240      # shelf posters
CHAN_W, CHAN_H = 250, 130          # landscape live-channel tiles


def _x_icon(size: int, color: str) -> QIcon:
    """A drawn close X (the ✕ glyph renders as a tofu box on some fonts)."""
    scale = 3
    pm = QPixmap(size * scale, size * scale)
    pm.setDevicePixelRatio(float(scale))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidthF(max(1.6, size * 0.14))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    m = size * 0.3
    p.drawLine(int(m), int(m), int(size - m), int(size - m))
    p.drawLine(int(size - m), int(m), int(m), int(size - m))
    p.end()
    return QIcon(pm)


def _cover_pixmap(pm: QPixmap, w: int, h: int, radius: int = 10) -> QPixmap:
    """Scale-crop *pm* to exactly w x h (cover fit) with rounded corners."""
    out = QPixmap(w, h)
    out.fill(Qt.GlobalColor.transparent)
    scaled = pm.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                       Qt.TransformationMode.SmoothTransformation)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0.0, 0.0, float(w), float(h), radius, radius)
    p.setClipPath(path)
    p.drawPixmap((w - scaled.width()) // 2, (h - scaled.height()) // 2, scaled)
    p.end()
    return out


def _contain_pixmap(pm: QPixmap, w: int, h: int, radius: int = 10) -> QPixmap:
    """Fit *pm* inside w x h on a panel background (for channel logos, which
    must not be cropped the way posters can be)."""
    out = QPixmap(w, h)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0.0, 0.0, float(w), float(h), radius, radius)
    p.fillPath(path, QColor(P["pane"]).lighter(118))
    scaled = pm.scaled(int(w * 0.55), int(h * 0.62),
                       Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
    p.setClipPath(path)
    p.drawPixmap((w - scaled.width()) // 2,
                 (h - scaled.height()) // 2 - 8, scaled)
    p.end()
    return out


class _Card(QFrame):
    """One clickable tile: image, title, optional subtitle, optional
    progress strip (resume shelf). Hover raises an accent border."""

    clicked = pyqtSignal()

    def __init__(self, w: int, h: int, title: str, subtitle: str = "",
                 progress: int | None = None) -> None:
        super().__init__()
        self._w, self._h = w, h
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(w)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        self.img = QLabel()
        self.img.setFixedSize(w, h)
        self.img.setStyleSheet(
            f"background:{QColor(P['pane']).lighter(115).name()};"
            "border-radius:10px;")
        self.img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Placeholder: big dimmed initial, so an artless card still reads.
        self.img.setText((title or "?").strip()[:1].upper())
        f = QFont()
        f.setPointSize(26)
        f.setBold(True)
        self.img.setFont(f)
        lay.addWidget(self.img)
        if progress is not None:
            bar = QFrame(self.img)
            bar.setGeometry(8, h - 10, max(6, int((w - 16) * progress / 100)),
                            4)
            bar.setStyleSheet(f"background:{ACCENT}; border-radius:2px;")
        t = QLabel(title or "?")
        t.setStyleSheet("font-weight:600; font-size:13px;")
        t.setFixedWidth(w)
        t.setWordWrap(False)
        lay.addWidget(t)
        self._elide(t, w)
        if subtitle:
            s = QLabel(subtitle)
            s.setStyleSheet(f"color:{P['muted']}; font-size:12px;")
            s.setFixedWidth(w)
            lay.addWidget(s)
            self._elide(s, w)
        lay.addStretch(1)

    @staticmethod
    def _elide(lbl: QLabel, w: int) -> None:
        fm = lbl.fontMetrics()
        lbl.setText(fm.elidedText(lbl.text(), Qt.TextElideMode.ElideRight, w))

    def set_pixmap(self, pm: QPixmap, contain: bool = False) -> None:
        fn = _contain_pixmap if contain else _cover_pixmap
        self.img.setText("")
        self.img.setPixmap(fn(pm, self._w, self._h))

    def enterEvent(self, e) -> None:
        self.img.setStyleSheet(self.img.styleSheet()
                               + f"border:2px solid {ACCENT};")
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self.img.setStyleSheet(self.img.styleSheet().replace(
            f"border:2px solid {ACCENT};", ""))
        super().leaveEvent(e)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


class _Shelf(QWidget):
    """A titled, horizontally scrolling row of cards."""

    def __init__(self, title: str) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        head = QLabel(title)
        # Opaque background: a transparent label ghosts on macOS when the Home
        # canvas scrolls (the old glyphs aren't cleared, so the title paints on
        # top of itself and reads as garbled, overlapping text). The cards
        # don't show this because they sit on their own opaque frames.
        head.setAutoFillBackground(True)
        head.setStyleSheet(
            f"font-size:17px; font-weight:700; background:{P['bg']};")
        lay.addWidget(head)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # Opaque backgrounds on the viewport + row: without them a horizontal
        # scroll on macOS leaves the previous cards behind as trails (the
        # transparent widgets aren't repainted under the moved content).
        self._scroll.viewport().setStyleSheet(f"background:{P['bg']};")
        self._row = QWidget()
        self._row.setAutoFillBackground(True)
        self._row.setStyleSheet(f"background:{P['bg']};")
        self._h = QHBoxLayout(self._row)
        self._h.setContentsMargins(0, 0, 0, 0)
        self._h.setSpacing(14)
        self._h.addStretch(1)
        self._scroll.setWidget(self._row)
        lay.addWidget(self._scroll)

    def add(self, card: _Card) -> None:
        self._h.insertWidget(self._h.count() - 1, card)

    def finish(self, img_h: int) -> None:
        # Height = image + text strip + horizontal scrollbar allowance.
        self._scroll.setFixedHeight(img_h + 66)

    def count(self) -> int:
        return self._h.count() - 1


class HomePage(QWidget):
    """The scrollable Home canvas. refresh() rebuilds everything from the
    window's stores; provider lists (movies/series) load async with a
    10-minute cache kept on the window."""

    MEDIA_CACHE_SECS = 600

    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self._gen = 0
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Opaque viewport so a vertical scroll fully repaints under the moved
        # content instead of smearing transparent labels (the garbled-title
        # ghosting on macOS).
        self._scroll.viewport().setAutoFillBackground(True)
        self._scroll.viewport().setStyleSheet(f"background:{P['bg']};")
        outer.addWidget(self._scroll)
        self._canvas = QWidget()
        # Own dark background, ID-scoped so it doesn't cascade onto children
        # (the app theme normally covers this; explicit = robust everywhere).
        self._canvas.setObjectName("HomeCanvas")
        self._canvas.setStyleSheet(
            f"#HomeCanvas {{ background: {P['bg']}; }}")
        self._v = QVBoxLayout(self._canvas)
        self._v.setContentsMargins(28, 18, 28, 24)
        self._v.setSpacing(22)
        self._scroll.setWidget(self._canvas)

    # -- building --------------------------------------------------------------

    def _clear(self) -> None:
        while self._v.count():
            it = self._v.takeAt(0)
            wdg = it.widget()
            if wdg is not None:
                wdg.deleteLater()

    # One connected, pill-shaped nav group: a rounded container holds the
    # buttons edge-to-edge, and each button gets a soft rounded highlight on
    # hover (no hard borders) - so the row reads as a single smooth segment.
    _SEG_QSS = (
        "#HomeNavGroup { background: %(pane)s; border: 1px solid %(border)s;"
        " border-radius: 21px; }"
        "#HomeNavBtn { background: transparent; color: %(text)s; border: none;"
        " border-radius: 15px; padding: 7px 18px; font-size: 13px;"
        " font-weight: 600; }"
        "#HomeNavBtn:hover { background: %(hover)s; }"
    )

    def _top_bar(self) -> QWidget:
        """Quick-nav segment (like the reference apps' top bar) + a close X.
        Text-only labels sitting inside one rounded pill, each with a rounded
        per-item hover highlight."""
        qss = self._SEG_QSS % {
            "pane": QColor(P["pane"]).lighter(112).name(), "text": P["text"],
            "border": QColor(P["pane"]).lighter(135).name(),
            "hover": QColor(P["pane"]).lighter(132).name()}
        bar = QWidget()
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 4, 0, 4)
        h.setSpacing(10)
        h.addStretch(1)
        w = self.window

        group = QFrame(objectName="HomeNavGroup")
        group.setStyleSheet(qss)
        g = QHBoxLayout(group)
        g.setContentsMargins(5, 4, 5, 4)
        g.setSpacing(2)

        def pill(text, fn):
            b = QPushButton(text, objectName="HomeNavBtn")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(fn)
            g.addWidget(b)

        pill(tr("nav_tv"),
             lambda: self._leave_to(lambda: w.switch_mode("live")))
        pill(tr("btn_epg_guide"), self._open_guide)
        pill(tr("nav_movies"),
             lambda: self._leave_to(lambda: w.switch_mode("vod")))
        pill(tr("nav_series"),
             lambda: self._leave_to(lambda: w.switch_mode("series")))
        pill(tr("btn_settings"), w.open_settings)
        h.addWidget(group)
        h.addStretch(1)
        close = QPushButton()
        close.setIcon(_x_icon(16, P["text"]))
        close.setFixedSize(36, 34)
        close.setToolTip(tr("common_close"))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(
            "QPushButton { background: %s; border:1px solid %s;"
            " border-radius: 17px; }"
            "QPushButton:hover { border-color: %s; }" % (
                QColor(P["pane"]).lighter(112).name(),
                QColor(P["pane"]).lighter(135).name(), ACCENT))
        close.clicked.connect(self.window._leave_home)
        h.addWidget(close)
        return bar

    def refresh(self) -> None:
        self._gen += 1
        self._clear()
        self._v.addWidget(self._top_bar())
        w = self.window
        s = w.settings

        def on(key):
            return s.value(key, "true") == "true"

        # Hero shelf fills in when the async provider fetch lands; hidden
        # until then so it doesn't reserve an empty gap (or linger on a
        # provider with no VOD).
        self._hero_shelf = _Shelf(tr("home_featured"))
        self._hero_shelf.hide()
        self._v.addWidget(self._hero_shelf)

        if on("home_sh_resume"):
            rows = []
            try:
                rows = w.resume.continue_watching()[:14]
            except Exception:
                pass
            if rows:
                shelf = _Shelf(tr("home_resume"))
                for it in rows:
                    card = _Card(POSTER_W, POSTER_H,
                                 it.get("name") or it.get("title") or "?",
                                 progress=int(it.get("_progress_pct") or 0))
                    card.clicked.connect(
                        lambda it=it: self._play_media(it))
                    self._set_art(card, self._media_art(it), w.poster_art)
                    shelf.add(card)
                shelf.finish(POSTER_H)
                self._v.addWidget(shelf)

        if on("home_sh_fav"):
            chans = self._favorite_channels()[:14]
            if chans:
                shelf = _Shelf(tr("home_fav_now"))
                for it in chans:
                    now_t = ""
                    try:
                        prog = w.xmltv.current_programme(it)
                        now_t = (prog or {}).get("title") or ""
                    except Exception:
                        pass
                    card = _Card(CHAN_W, CHAN_H, it.get("name") or "?", now_t)
                    card.clicked.connect(
                        lambda it=it: self._play_channel(it))
                    self._set_art(card, it.get("stream_icon"), w.logos,
                                  contain=True)
                    shelf.add(card)
                shelf.finish(CHAN_H)
                self._v.addWidget(shelf)

            media = self._favorite_media()[:20]
            if media:
                shelf = _Shelf(tr("home_fav_media"))
                for it in media:
                    card = _Card(POSTER_W, POSTER_H, it.get("name")
                                 or it.get("title") or "?")
                    if it.get("_kind") == "series":
                        card.clicked.connect(
                            lambda it=it: self._open_series(it))
                        art = self._media_art(it, "series")
                    else:
                        card.clicked.connect(
                            lambda it=it: self._play_media(it))
                        art = self._media_art(it, "vod")
                    self._set_art(card, art, w.poster_art)
                    shelf.add(card)
                shelf.finish(POSTER_H)
                self._v.addWidget(shelf)

        if on("home_sh_history"):
            rows = list(getattr(w.history, "entries", []))[:14]
            if rows:
                shelf = _Shelf(tr("home_recent"))
                for it in rows:
                    live = it.get("_kind") == "live"
                    card = _Card(CHAN_W if live else POSTER_W,
                                 CHAN_H if live else POSTER_H,
                                 it.get("name") or "?")
                    card.clicked.connect(
                        lambda it=it: self._play_history(it))
                    if live:
                        # A channel logo, contained on a panel.
                        self._set_art(card, it.get("stream_icon"), w.logos,
                                      contain=True)
                    else:
                        # A movie/episode: resolve a real poster through the
                        # cover pipeline (the raw history icon is often a tiny
                        # provider thumb, or missing).
                        k = "episode" if it.get("_kind") == "episode" else "vod"
                        self._set_art(card, self._media_art(it, k),
                                      w.poster_art)
                    shelf.add(card)
                shelf.finish(POSTER_H)
                self._v.addWidget(shelf)

        self._movies_box = QVBoxLayout()
        self._v.addLayout(self._movies_box)
        self._v.addStretch(1)
        self._load_media()

    # -- data ------------------------------------------------------------------

    def _favorite_channels(self) -> list[dict]:
        seen, out = set(), []
        for items in getattr(self.window.favs, "groups", {}).values():
            for it in items:
                sid = it.get("stream_id")
                if sid is not None and sid not in seen:
                    seen.add(sid)
                    out.append(it)
        return out

    def _favorite_media(self) -> list[dict]:
        """Favourited movies and series, tagged with the kind so a card can
        play a movie or drill into a series. Channel favourites live in their
        own 'now playing' shelf; this is the poster shelf for VOD content, so
        a user whose favourites are all films/shows still sees them on Home."""
        w = self.window
        out: list[dict] = []
        seen: set = set()
        for store, kind, id_key in (
                (getattr(w, "movie_favs", None), "movie", "stream_id"),
                (getattr(w, "series_favs", None), "series", "series_id")):
            if store is None:
                continue
            for items in getattr(store, "groups", {}).values():
                for it in items:
                    ident = it.get(id_key)
                    tag = (kind, ident)
                    if ident is None or tag in seen:
                        continue
                    seen.add(tag)
                    row = dict(it)
                    row["_kind"] = kind
                    out.append(row)
        return out

    @staticmethod
    def _is_movie(it: dict) -> bool:
        """True for a real VOD movie row - has a stream id and a type that
        isn't a live/radio channel. Filters channels a provider may have mixed
        into the VOD list out of the Movies shelf."""
        if it.get("stream_id") is None:
            return False
        st = str(it.get("stream_type") or "").lower()
        return st not in ("live", "radio", "created_live")

    @staticmethod
    def _num(v) -> float:
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    def _load_media(self) -> None:
        w, gen = self.window, self._gen
        cache = getattr(w, "_home_media_cache", None)
        if (cache and len(cache) == 4
                and time.time() - cache[0] < self.MEDIA_CACHE_SECS):
            self._fill_media(cache[1], cache[2], cache[3])
            return
        client = w.client

        def work():
            vod = list(client.vod_streams(None) or [])
            ser = list(client.series_list(None) or [])
            chan = list(client.live_streams(None) or [])
            return vod, ser, chan

        def done(res):
            vod, ser, chan = res
            # Keep each shelf to its own content type. Some providers dump live
            # channels into the VOD "all" list (or otherwise mix types), which
            # is how a TV channel ended up under "Recently added movies" - so
            # the Movies shelf takes only real movie rows (a stream_id, and a
            # stream_type that isn't a live/radio channel), and the Series shelf
            # takes only rows that actually carry a series_id.
            vod = [i for i in vod if self._is_movie(i)]
            ser = [i for i in ser if i.get("series_id") is not None]
            chan = [i for i in chan if i.get("stream_id") is not None]
            vod.sort(key=lambda i: self._num(i.get("added")), reverse=True)
            ser.sort(key=lambda i: self._num(i.get("last_modified")),
                     reverse=True)
            chan.sort(key=lambda i: self._num(i.get("added")), reverse=True)
            vod, ser, chan = vod[:24], ser[:20], chan[:24]
            w._home_media_cache = (time.time(), vod, ser, chan)
            if gen == self._gen:
                try:
                    self._fill_media(vod, ser, chan)
                except RuntimeError:
                    pass

        run_async(w.pool, work, done, lambda _e: None)

    def _fill_media(self, vod: list, ser: list, chan: list) -> None:
        w = self.window
        s = w.settings

        # Hero: the freshest movies as oversized posters with rating, in a
        # horizontally scrolling row (not a fixed four).
        for it in vod[:12]:
            rating = str(it.get("rating") or "").strip()
            card = _Card(HERO_W, HERO_H, it.get("name") or "?",
                         (f"★ {rating}" if rating else ""))
            card.clicked.connect(lambda it=it: self._play_media(it))
            self._set_art(card, self._media_art(it), w.poster_art)
            self._hero_shelf.add(card)
        if vod:
            self._hero_shelf.finish(HERO_H)
            self._hero_shelf.show()

        if s.value("home_sh_movies", "true") == "true" and vod:
            shelf = _Shelf(tr("home_new_movies"))
            for it in vod:
                card = _Card(POSTER_W, POSTER_H, it.get("name") or "?")
                card.clicked.connect(lambda it=it: self._play_media(it))
                self._set_art(card, self._media_art(it), w.poster_art)
                shelf.add(card)
            shelf.finish(POSTER_H)
            self._movies_box.addWidget(shelf)
        if s.value("home_sh_series", "true") == "true" and ser:
            shelf = _Shelf(tr("home_new_series"))
            for it in ser:
                card = _Card(POSTER_W, POSTER_H, it.get("name") or "?")
                card.clicked.connect(lambda it=it: self._open_series(it))
                self._set_art(card, self._media_art(it, "series"),
                              w.poster_art)
                shelf.add(card)
            shelf.finish(POSTER_H)
            self._movies_box.addWidget(shelf)
        if s.value("home_sh_channels", "true") == "true" and chan:
            shelf = _Shelf(tr("home_new_channels"))
            for it in chan:
                card = _Card(CHAN_W, CHAN_H, it.get("name") or "?")
                card.clicked.connect(lambda it=it: self._play_channel(it))
                self._set_art(card, it.get("stream_icon"), w.logos,
                              contain=True)
                shelf.add(card)
            shelf.finish(CHAN_H)
            self._movies_box.addWidget(shelf)

    def _media_art(self, it: dict, kind: str = "vod") -> str | None:
        # Episodes rarely carry their own poster: borrow the series'
        # artwork from the stored context so an episode card still shows a
        # poster instead of a bare initial.
        ctx = it.get("_series_ctx") or {}
        raw = (it.get("cover") or it.get("stream_icon")
               or ctx.get("cover") or ctx.get("stream_icon"))
        try:
            return self.window.cover.cover_url(it, kind) or raw
        except Exception:
            return raw

    def _set_art(self, card: _Card, url, loader, contain: bool = False) -> None:
        if not url or loader is None:
            return
        gen = self._gen

        def cb(pm, card=card, gen=gen, contain=contain):
            if gen != self._gen or pm is None or pm.isNull():
                return
            try:
                card.set_pixmap(pm, contain=contain)
            except RuntimeError:
                pass   # card torn down by a rebuild

        loader.get(url, cb)

    # -- actions (always leave Home first, then act in the classic UI) --------

    def _leave_to(self, fn) -> None:
        self.window._leave_home()
        fn()

    def _open_guide(self) -> None:
        self.window._leave_home()
        self.window._open_epg_guide()

    def _play_channel(self, it: dict) -> None:
        # Land under TV, then tune the channel (so the classic view is on the
        # right category behind the player).
        w = self.window
        w._leave_home()
        if w.mode != "live":
            w.switch_mode("live")
        try:
            w._current_key = w._item_key(it)
            w._show_detail(it)   # panel follows the channel, not the last row
        except Exception:
            pass
        try:
            w.play_live_channel(it)
        except Exception:
            pass

    def _play_media(self, it: dict) -> None:
        """Movie or a continue-watching row: land under Movies, then play it.
        Built directly (not via play_item, whose URL/kind logic keys off the
        classic view's mode - a movie clicked from Home would otherwise be
        built as a live URL)."""
        w = self.window
        w._leave_home()
        # A continue-watching episode replays from its stored series context.
        if it.get("_kind") == "episode" and it.get("_series_ctx") is not None:
            if w.mode != "series":
                w.switch_mode("series")
            w.play_item(it)
            return
        sid = it.get("stream_id")
        if sid is None:
            return
        if w.mode != "vod":
            w.switch_mode("vod")
        url = w.client.vod_url(sid, it.get("container_extension"))
        if not url:
            return
        # Populate the detail panel with THIS movie. Home plays straight from a
        # card without selecting a list row, so without this the panel under
        # the player keeps showing whatever was last selected in the classic
        # view (e.g. the TV channel played before). Set _current_key too, so the
        # poster's play/pause overlay recognises this as the playing item and
        # syncs (otherwise it kept showing 'play' while the movie ran).
        try:
            w._current_key = w._item_key(it)
            w._show_detail(it)
        except Exception:
            pass
        w._start_playback(
            url, it.get("name") or it.get("title") or "dopeIPTV",
            it.get("stream_icon") or it.get("cover"),
            w._item_key(it), "movie", item=it)

    def _open_series(self, it: dict) -> None:
        # Land under Series and drill straight into this show's episode list.
        w = self.window
        w._leave_home()
        if w.mode != "series":
            w.switch_mode("series")
        w._enter_series(it)

    def _play_history(self, it: dict) -> None:
        """Replay a history row: land under the right category, then play from
        its stored URL + kind (works regardless of the prior mode)."""
        w = self.window
        w._leave_home()
        url = it.get("_url")
        if not url:
            return
        kind = it.get("_kind") or "live"
        target = {"live": "live", "movie": "vod",
                  "episode": "series"}.get(kind)
        if target and w.mode != target:
            w.switch_mode(target)
        if kind == "episode" and it.get("_series_ctx") is None:
            kind = "movie"   # no series context to autoplay-next; just play it
        try:
            w._current_key = it.get("_key")
            w._show_detail(it)   # panel follows this row, not the last selection
        except Exception:
            pass
        w._start_playback(url, it.get("name") or "dopeIPTV",
                          it.get("stream_icon"), it.get("_key"), kind, item=it)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.window._leave_home()
            return
        super().keyPressEvent(e)


class _HomeMixin:
    """MainWindow glue: nav button, central-stack swapping, settings."""

    def _home_enabled(self) -> bool:
        return self.settings.value("home_enabled", "true") == "true"

    def _ensure_home_page(self) -> HomePage:
        page = getattr(self, "_home_page", None)
        if page is None:
            page = self._home_page = HomePage(self)
            self._center_stack.addWidget(page)
        return page

    def _show_home_page(self) -> None:
        if not self._home_enabled():
            return
        page = self._ensure_home_page()
        page.refresh()
        self._center_stack.setCurrentWidget(page)
        page.setFocus()
        for k, b in self.nav_btns.items():
            b.setChecked(k == "home")

    def _leave_home(self) -> None:
        self._center_stack.setCurrentIndex(0)
        if "home" in self.nav_btns:
            self.nav_btns["home"].setChecked(False)
        if self.mode in self.nav_btns:
            self.nav_btns[self.mode].setChecked(True)

    def _home_showing(self) -> bool:
        page = getattr(self, "_home_page", None)
        return page is not None and self._center_stack.currentWidget() is page

    def _apply_home_settings(self) -> None:
        if "home" in self.nav_btns:
            self.nav_btns["home"].setVisible(self._home_enabled())
        if not self._home_enabled() and self._home_showing():
            self._leave_home()

    def _maybe_open_home_at_start(self) -> None:
        if (self._home_enabled()
                and self.settings.value("home_start", "true") == "true"):
            QTimer.singleShot(0, self._show_home_page)
