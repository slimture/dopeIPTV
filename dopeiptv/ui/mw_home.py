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
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from ..core.workers import run_async
from ..i18n import tr
from .theme import ACCENT, P

# Card geometry (image area; the card adds a text strip underneath).
HERO_W, HERO_H = 240, 360          # big hero posters
POSTER_W, POSTER_H = 150, 225      # shelf posters
CHAN_W, CHAN_H = 240, 120          # landscape live-channel tiles


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
        head.setStyleSheet("font-size:17px; font-weight:700;")
        lay.addWidget(head)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._row = QWidget()
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

    def _top_bar(self) -> QWidget:
        """Quick-nav pills (like the reference apps' top bar) + a close X."""
        bar = QWidget()
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 0, 0)
        h.addStretch(1)

        def pill(text, fn):
            b = QPushButton(text)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(fn)
            h.addWidget(b)

        w = self.window
        pill(tr("nav_tv"), lambda: self._leave_to(lambda: w.switch_mode("live")))
        pill(tr("btn_epg_guide"), self._open_guide)
        pill(tr("nav_movies"),
             lambda: self._leave_to(lambda: w.switch_mode("vod")))
        pill(tr("nav_series"),
             lambda: self._leave_to(lambda: w.switch_mode("series")))
        pill(tr("btn_settings"), w.open_settings)
        h.addStretch(1)
        close = QPushButton("✕")
        close.setFixedWidth(34)
        close.setToolTip(tr("common_close"))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
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

        # Hero + media shelves fill in when the async provider fetch lands.
        self._hero_box = QWidget()
        QHBoxLayout(self._hero_box).setContentsMargins(0, 0, 0, 0)
        self._hero_box.layout().setSpacing(16)
        self._v.addWidget(self._hero_box)

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
                    self._set_art(card, it.get("_icon"),
                                  w.logos if live else w.poster_art,
                                  contain=live)
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

    @staticmethod
    def _num(v) -> float:
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    def _load_media(self) -> None:
        w, gen = self.window, self._gen
        cache = getattr(w, "_home_media_cache", None)
        if cache and time.time() - cache[0] < self.MEDIA_CACHE_SECS:
            self._fill_media(cache[1], cache[2])
            return
        client = w.client

        def work():
            vod = list(client.vod_streams(None) or [])
            ser = list(client.series_list(None) or [])
            return vod, ser

        def done(res):
            vod, ser = res
            vod.sort(key=lambda i: self._num(i.get("added")), reverse=True)
            ser.sort(key=lambda i: self._num(i.get("last_modified")),
                     reverse=True)
            vod, ser = vod[:14], ser[:14]
            w._home_media_cache = (time.time(), vod, ser)
            if gen == self._gen:
                try:
                    self._fill_media(vod, ser)
                except RuntimeError:
                    pass

        run_async(w.pool, work, done, lambda _e: None)

    def _fill_media(self, vod: list, ser: list) -> None:
        w = self.window
        s = w.settings

        # Hero: the freshest movies as oversized posters with rating.
        for it in vod[:4]:
            rating = str(it.get("rating") or "").strip()
            card = _Card(HERO_W, HERO_H, it.get("name") or "?",
                         (f"★ {rating}" if rating else ""))
            card.clicked.connect(lambda it=it: self._play_media(it))
            self._set_art(card, self._media_art(it), w.poster_art)
            self._hero_box.layout().addWidget(card)
        self._hero_box.layout().addStretch(1)

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

    def _media_art(self, it: dict, kind: str = "vod") -> str | None:
        try:
            return self.window.cover.cover_url(it, kind) \
                or it.get("stream_icon") or it.get("cover")
        except Exception:
            return it.get("stream_icon") or it.get("cover")

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
        self.window._leave_home()
        try:
            self.window.play_live_channel(it)
        except Exception:
            pass

    def _play_media(self, it: dict) -> None:
        w = self.window
        w._leave_home()
        # play_item's series special-case keys off the current MODE; a movie
        # clicked while the classic view sits in Series would be "entered"
        # instead of played, so steer the mode first.
        if w.mode == "series" and it.get("series_id") is None:
            w.switch_mode("vod")
        w.play_item(it)

    def _open_series(self, it: dict) -> None:
        w = self.window
        w._leave_home()
        if w.mode != "series":
            w.switch_mode("series")
        w._enter_series(it)

    def _play_history(self, it: dict) -> None:
        w = self.window
        w._leave_home()
        w.play_item(it)

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
