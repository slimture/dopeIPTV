"""Extracted from main_window.py (mixin); MainWindow inherits this.

Verbatim move - self.* access and behaviour unchanged.
"""

from __future__ import annotations

import html
import re
from ..providers.client import b64, epg_times
from ..i18n import tr
from .theme import ACCENT, P
from .widgets import _ClickableWidget
from ..core.workers import run_async, tmdb_url_from_provider
from PyQt6.QtCore import QSize, QTimer, Qt
from PyQt6.QtGui import QIcon, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import QAbstractItemView, QDialog, QDialogButtonBox, QFrame, QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget
from datetime import datetime


class _DetailMixin:
    def _on_current_changed(self, current, _previous=None) -> None:
        it = (self.list_model.item_at(current.row())
              if current.isValid() else None)
        self._current_key = self._item_key(it)
        self._show_detail(it)

    POSTER_SIZE_LIVE = (84, 84)
    POSTER_SIZE_MEDIA = (170, 255)

    def _show_detail(self, it) -> None:
        self.now_card.hide()
        self.media_info.hide()
        self.media_rating_lbl.hide()
        self._clear_cast_row()
        self._clear_epg_rows()
        self._current_epg = None
        self._tmdb_details = None
        if not it:
            self._detail_name = tr("detail_select_something")
            self.d_logo.setFixedSize(*self.POSTER_SIZE_LIVE)
            self.d_logo.setPixmap(QPixmap())
            self.d_logo.setText("")
            return
        name = self.channel_display_name(it)
        self._detail_name = name
        # History and Watch Later rows carry the original content kind
        # in "_kind"; a VOD or series shown from either view should still
        # resolve TMDB metadata + cover the same way the main Movies /
        # Series view does. History uses its own vocabulary
        # ("movie"/"episode"/"live") - normalise it to the vod/series
        # the detail panel and TMDB lookup speak.
        snap_kind = (it.get("_kind")
                     if (self.mode in ("history", "watchlist", "watched")
                         or (self.mode == "fav" and it.get("_kind")))
                     else None)
        snap_kind = {"movie": "vod", "episode": "series"}.get(
            snap_kind, snap_kind)
        # In the Favorites view the content kind follows the selected
        # section, so a favourite movie/series gets the poster + info
        # panel just like it would in the Movies / Series views.
        ckind = self._content_kind()
        media_kind = (
            "series" if self.series_ctx
            else "vod" if ckind == "vod"
            else "series" if ckind == "series"
            else snap_kind if snap_kind in ("vod", "series")
            else None)
        is_media = media_kind is not None
        poster_size = (self.POSTER_SIZE_MEDIA if is_media
                       else self.POSTER_SIZE_LIVE)
        self.d_logo.setFixedSize(*poster_size)
        # Make the Play button span the poster's width so its (centered) label
        # sits centered under the poster/TV icon rather than off to the left.
        self.play_mpv.setFixedWidth(poster_size[0])
        if is_media:
            # Match the info box to the poster height so their bottoms align.
            self.media_info.setFixedHeight(self.POSTER_SIZE_MEDIA[1])
        self.d_logo.setPixmap(QPixmap())
        self.d_logo.setStyleSheet(self.PLACEHOLDER_LOGO_STYLE)
        self.d_logo.setText(name.strip()[:1].upper())
        self._load_detail_poster(it, is_media, media_kind)

        if self.mode == "rec":
            return

        if self.mode == "history" and snap_kind not in ("vod", "series"):
            # For live/recording history rows the artwork + TMDB card
            # is all we have; only fall through for movie/series
            # history rows so they also get provider-side info
            # (plot, year, episode list).
            return

        if self.series_ctx:
            info = (it.get("info")
                    if isinstance(it.get("info"), dict) else {})
            self._show_media_info(info, self._current_key)
        elif ckind in ("live", "fav"):
            if it.get("stream_id") is not None:
                self._request_epg()
                if (self.player and self._autoplay_preview()
                        and self.playback_mode() == "embedded"
                        and not self._rmb_selecting):
                    self._preview_timer.start(350)
        # In History / Watch Later, defer to the snapshot's _kind so
        # a movie row fetches movie info and a series row fetches
        # series info - same as the main Movies / Series views.
        # History rows carry the provider id under "_key" (not
        # stream_id/series_id), so fall back to that - this is what
        # makes the plot/genre/cast panel fill in for history items
        # even with no TMDB account.
        elif (ckind == "vod"
              or (self.mode in ("watchlist", "history", "watched")
                  and snap_kind == "vod")):
            sid = it.get("stream_id")
            if sid is None and self.mode == "history":
                sid = it.get("_key")
            if sid is not None:
                self._request_media_info("vod", sid, self._current_key)
        elif (ckind == "series"
              or (self.mode in ("watchlist", "history", "watched")
                  and snap_kind == "series")):
            sid = it.get("series_id")
            if sid is None and self.mode == "history":
                sid = it.get("_key")
            if sid is not None:
                self._request_media_info("series", sid, self._current_key)

    @property
    def PLACEHOLDER_LOGO_STYLE(self) -> str:
        return (f"background:{P['sel']}; border-radius:18px; "
                "font-size:30px; font-weight:700;")

    def _set_detail_logo(self, pm) -> None:
        w, h = self.d_logo.width(), self.d_logo.height()
        tile = QPixmap(w, h)
        tile.fill(Qt.GlobalColor.transparent)
        p = QPainter(tile)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        s = pm.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap((w - s.width()) // 2, (h - s.height()) // 2, s)
        p.end()
        self.d_logo.setStyleSheet("background:transparent;")
        self.d_logo.setText("")
        self.d_logo.setPixmap(tile)

    def _media_title_for_tmdb(self, it) -> str:
        if self.series_ctx:
            return (self.series_ctx.get("name")
                    or self.series_ctx.get("title") or "")
        return it.get("name") or it.get("title") or ""

    def _load_detail_poster(self, it, is_media: bool,
                            kind: str | None = None) -> None:
        raw_cover = it.get("stream_icon") or it.get("cover")
        # If the provider icon embeds a TMDB path, prefer the direct
        # image.tmdb.org URL - the panel's own proxy is often the one
        # that's 404ing/down. Same rewrite the list delegate uses.
        fallback_url = tmdb_url_from_provider(raw_cover) or raw_cover
        if not (is_media and self.tmdb):
            if fallback_url:
                self.poster_art.get(fallback_url, self._set_detail_logo)
            return
        title = self._media_title_for_tmdb(it)
        if not title:
            if fallback_url:
                self.poster_art.get(fallback_url, self._set_detail_logo)
            return
        kind = kind or ("vod" if self.mode == "vod" else "series")
        key = self._current_key

        def apply(details: dict) -> None:
            if key != self._current_key:
                return
            self._apply_media_card(details)
            url = details.get("poster_url") or fallback_url
            if url:
                self.poster_art.get(url, self._set_detail_logo)

        details = self.tmdb.get_full(title, kind, apply)
        if details is not None:
            apply(details)
        elif fallback_url:
            self.poster_art.get(fallback_url, self._set_detail_logo)

    def _apply_media_card(self, details: dict) -> None:
        self._tmdb_details = details
        rating = details.get("rating")
        imdb_id = details.get("imdb_id")
        cast = details.get("cast") or []
        stars = f"★ {rating:.1f}/10" if rating else ""
        if imdb_id:
            label = stars or "View on IMDb"
            self.media_rating_lbl.setText(
                f'<a href="https://www.imdb.com/title/{imdb_id}/" '
                f'style="color:{ACCENT}; text-decoration:none;">'
                f'{label}</a>')
            self.media_rating_lbl.show()
        elif stars:
            self.media_rating_lbl.setText(stars)
            self.media_rating_lbl.show()
        else:
            self.media_rating_lbl.hide()
        self._clear_cast_row()
        if cast:
            for member in cast:
                self.cast_lay.insertWidget(
                    self.cast_lay.count() - 1,
                    self._build_cast_chip(member.get("name") or "",
                                          member.get("profile_url"),
                                          member.get("person_id")))
            self.cast_scroll.show()
        else:
            self.cast_scroll.hide()
        if (details.get("overview") or details.get("genres")) \
                and not self.media_info.isVisible():
            self._show_media_info({}, self._current_key)

    CAST_PHOTO_SIZE = 56

    def _clear_cast_row(self) -> None:
        while self.cast_lay.count() > 1:
            w = self.cast_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.cast_scroll.hide()

    def _build_cast_chip(self, name: str, profile_url: str | None,
                         person_id=None) -> QWidget:
        size = self.CAST_PHOTO_SIZE
        chip = _ClickableWidget()
        if self.tmdb and name:
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip(f"Find other titles with {name} in your playlist")
            chip.clicked.connect(
                lambda: self._on_cast_clicked(name, person_id))
        v = QVBoxLayout(chip)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        photo = QLabel()
        photo.setFixedSize(size, size)
        photo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        photo.setStyleSheet(
            f"background:{P['sel']}; border-radius:{size // 2}px; "
            "font-size:18px; font-weight:700;")
        photo.setText(name.strip()[:1].upper() if name else "?")
        v.addWidget(photo, 0, Qt.AlignmentFlag.AlignHCenter)
        name_lbl = QLabel(name)
        name_lbl.setStyleSheet(f"color:{P['muted']}; font-size:10px;")
        name_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        name_lbl.setFixedWidth(size + 16)
        name_lbl.setWordWrap(True)
        v.addWidget(name_lbl)
        if profile_url:
            def apply_photo(pm, photo=photo) -> None:
                photo.setPixmap(self._circular_pixmap(pm, size))
                photo.setText("")
                photo.setStyleSheet("background:transparent;")
            self.poster_art.get(profile_url, apply_photo)
        return chip

    @staticmethod
    def _circular_pixmap(pm: QPixmap, size: int) -> QPixmap:
        scaled = pm.scaled(
            size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation)
        x = max(0, (scaled.width() - size) // 2)
        y = max(0, (scaled.height() - size) // 2)
        cropped = scaled.copy(x, y, size, size)
        out = QPixmap(size, size)
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addEllipse(0, 0, size, size)
        p.setClipPath(path)
        p.drawPixmap(0, 0, cropped)
        p.end()
        return out

    # -- cast: find an actor's other titles in this playlist -----------------------

    # Noise commonly appended to VOD titles by providers (movies especially:
    # "Inception (2010) 1080p", "EN| Inception MULTI"), which stopped them
    # from matching TMDB's clean title.
    _TITLE_BRACKETS = re.compile(r"[\(\[\{].*?[\)\]\}]")
    _TITLE_NOISE = re.compile(
        r"\b(19|20)\d{2}\b|\b(2160p|1080p|720p|480p|4k|uhd|fhd|hd|sd|hevc|"
        r"x26[45]|h26[45]|web[- ]?dl|webrip|bluray|bdrip|dvdrip|remux|imax|"
        r"multi|dual|vostfr|truefrench)\b", re.IGNORECASE)
    _TITLE_PREFIX = re.compile(r"^\s*[a-z]{2,4}\s*[|:\-]\s*", re.IGNORECASE)

    @classmethod
    def _normalize_title(cls, s: str) -> str:
        s = (s or "").lower()
        s = cls._TITLE_BRACKETS.sub(" ", s)
        s = cls._TITLE_PREFIX.sub("", s)
        s = cls._TITLE_NOISE.sub(" ", s)
        return "".join(c for c in s if c.isalnum())

    def _ensure_full_catalog(self, callback) -> None:
        if self._full_catalog is not None:
            callback(self._full_catalog)
            return

        def fetch():
            vod = self.client.vod_streams(None) or []
            series = self.client.series_list(None) or []
            return ([(it, "vod") for it in vod]
                    + [(it, "series") for it in series])

        def done(items):
            self._full_catalog = items
            callback(items)

        run_async(self.pool, fetch, done, lambda _e: callback([]))

    def _find_playlist_matches(self, titles: list[str], callback) -> None:
        norm_titles = {self._normalize_title(t) for t in titles}

        long_titles = [t for t in norm_titles if len(t) >= 6]

        def with_catalog(catalog):
            matches = []
            for it, kind in catalog:
                cnorm = self._normalize_title(
                    it.get("name") or it.get("title") or "")
                if not cnorm:
                    continue
                # Exact match, or the provider title begins with a (reasonably
                # long) TMDB title — catches residual trailing noise.
                if cnorm in norm_titles or any(
                        cnorm.startswith(t) for t in long_titles):
                    matches.append((it, kind))
            callback(matches)

        self._ensure_full_catalog(with_catalog)

    def _ensure_cast_dialog(self) -> QDialog:
        """Create (once) a reusable, non-modal, always-on-top panel that
        lists a cast member's other titles. Non-modal so the cast strip on
        the right stays clickable - clicking another cast member just
        refreshes this same panel instead of stacking new dialogs."""
        d = getattr(self, "_cast_dialog", None)
        if d is not None:
            return d
        d = QDialog(self)
        # Tool + stay-on-top keeps it above the main window without blocking
        # it, so you can pick another cast member while it is open.
        d.setWindowFlags(Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint)
        d.setModal(False)
        d.setMinimumSize(440, 520)
        lay = QVBoxLayout(d)
        self._cast_status = QLabel("")
        self._cast_status.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        self._cast_status.setWordWrap(True)
        lay.addWidget(self._cast_status)
        self._cast_result_list = QListWidget()
        # Room for a poster thumbnail beside each title.
        self._cast_result_list.setIconSize(QSize(46, 69))
        self._cast_result_list.setStyleSheet(
            "QListWidget::item { padding: 4px; }")
        self._cast_result_list.itemDoubleClicked.connect(
            lambda item: self._play_cast_match(item, d))
        lay.addWidget(self._cast_result_list, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(d.hide)
        buttons.accepted.connect(d.hide)
        for b in buttons.buttons():
            b.setIcon(QIcon())
        lay.addWidget(buttons)
        self._cast_dialog = d
        return d

    def _on_cast_clicked(self, name: str, person_id) -> None:
        if not self.tmdb:
            return
        d = self._ensure_cast_dialog()
        d.setWindowTitle(tr("actor_other_titles", name=name))
        status = self._cast_status
        result_list = self._cast_result_list
        result_list.clear()
        status.setText(tr("actor_lookup_filmography"))
        # Each click starts a new lookup; stamp a token so stale async
        # callbacks from a previously clicked cast member are ignored.
        token = getattr(self, "_cast_token", 0) + 1
        self._cast_token = token

        def show_matches(titles: list[str]) -> None:
            if self._cast_token != token:
                return
            status.setText(tr("actor_searching_playlist"))

            def with_matches(matches) -> None:
                if self._cast_token != token:
                    return
                result_list.clear()
                if not matches:
                    status.setText(tr("actor_no_matches"))
                    return
                status.setText(tr("actor_matches_found", n=len(matches)))
                for it, kind in matches:
                    label = (f"{it.get('name') or it.get('title')}"
                            f"  ({tr('misc_movie') if kind == 'vod' else tr('misc_series')})")
                    item = QListWidgetItem(label)
                    item.setData(Qt.ItemDataRole.UserRole, (it, kind))
                    result_list.addItem(item)
                    self._load_cast_match_poster(it, kind, item)

            self._find_playlist_matches(titles, with_matches)

        def with_person_id(pid) -> None:
            if self._cast_token != token:
                return
            if not pid:
                status.setText(tr("actor_not_found_tmdb", name=name))
                return
            credits = self.tmdb.get_person_credits(pid, show_matches)
            if credits is not None:
                show_matches(credits)

        if person_id:
            with_person_id(person_id)
        else:
            # Cast names that came from the provider's plain-text list have
            # no TMDB id yet - resolve it by name first, then fetch credits.
            status.setText(tr("actor_lookup_member"))
            pid = self.tmdb.resolve_person_id(name, with_person_id)
            if pid is not None:
                with_person_id(pid)
        d.show()
        d.raise_()
        d.activateWindow()

    def _load_cast_match_poster(self, it, kind, item) -> None:
        """Fetch a poster thumbnail for a cast-filmography match and set it
        as the list item's icon (provider artwork first, then TMDB)."""
        def apply(pm) -> None:
            try:
                icon_pm = pm.scaled(
                    46, 69, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                item.setIcon(QIcon(icon_pm))
            except RuntimeError:
                pass  # the list item was cleared before the art arrived

        url = it.get("stream_icon") or it.get("cover")
        if url:
            self.poster_art.get(url, apply)
            return
        if self.tmdb:
            title = it.get("name") or it.get("title") or ""
            tmdb_kind = "vod" if kind == "vod" else "series"
            details = self.tmdb.get_full(
                title, tmdb_kind,
                lambda d: (d.get("poster_url")
                           and self.poster_art.get(d["poster_url"], apply)))
            if details and details.get("poster_url"):
                self.poster_art.get(details["poster_url"], apply)

    def _play_cast_match(self, item, dialog) -> None:
        it, kind = item.data(Qt.ItemDataRole.UserRole)
        dialog.hide()
        if kind == "vod":
            self.switch_mode("vod")
            self._navigate_to_item(it, "vod")
        else:
            self.switch_mode("series")
            self._navigate_to_item(it, "series")

    def _navigate_to_item(self, target, mode: str) -> None:
        """Switch to the right category and scroll/select a specific item."""
        cat_id = str(target.get("category_id") or "")
        for i in range(self.cat_list.count()):
            ci = self.cat_list.item(i)
            if ci and str(ci.data(Qt.ItemDataRole.UserRole) or "") == cat_id:
                self.cat_list.setCurrentRow(i)
                break
        else:
            self.cat_list.setCurrentRow(0)

        target_key = self._item_key(target)

        def after_load(target_key=target_key, target=target):
            for row in range(self.list_model.rowCount()):
                it = self.list_model.item_at(row)
                if it and self._item_key(it) == target_key:
                    idx = self.list_model.index(row)
                    self.listw.setCurrentIndex(idx)
                    self.listw.scrollTo(
                        idx, QAbstractItemView.ScrollHint.PositionAtCenter)
                    if mode == "series":
                        self._enter_series(it)
                    else:
                        self.play_item(it, "mpv")
                    return
            if mode == "series":
                self._enter_series(target)
            else:
                self.play_item(target, "mpv")

        QTimer.singleShot(300, after_load)

    def _clear_epg_rows(self) -> None:
        while self.epg_lay.count() > 1:
            w = self.epg_lay.takeAt(0).widget()
            if w:
                w.deleteLater()

    def _epg_note(self, text: str) -> None:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color:{P['muted2']}; font-size:12px;")
        lbl.setWordWrap(True)
        self.epg_lay.insertWidget(self.epg_lay.count() - 1, lbl)

    def _request_epg(self) -> None:
        it = self.list_model.item_at(self.listw.currentIndex().row())
        if (not it or self._content_kind() not in ("live", "fav")
                or self.series_ctx):
            return
        sid = it.get("stream_id")
        if sid is None:
            return
        key = self._item_key(it)
        self._clear_epg_rows()
        self._epg_note("Loading programme guide…")

        def fetch():
            listings = self.client.short_epg(sid, 8)
            if not listings:
                listings = self.client.epg_table(sid)
            if not listings:
                listings = self.xmltv.listings_for(it)
            return listings

        run_async(self.pool, fetch,
                  lambda e: self._show_epg(e, key),
                  lambda _: self._epg_error(key))

    def _epg_error(self, key) -> None:
        if key != self._current_key:
            return
        self.now_card.hide()
        self._clear_epg_rows()
        self._epg_note("Could not load the programme guide.")

    # -- movie/series info ---------------------------------------------------------

    def _request_media_info(self, kind: str, mid, key) -> None:
        cached = self._info_cache.get((kind, mid))
        if cached is not None:
            self._show_media_info(cached, key)
            return
        self._epg_note("Loading information…")
        if kind == "vod":
            fetch = lambda: (
                self.client.vod_info(mid) or {}).get("info") or {}
        else:
            fetch = lambda: (
                self.client.series_info(mid) or {}).get("info") or {}

        def done(info):
            if not isinstance(info, dict):
                info = {}
            self._info_cache[(kind, mid)] = info
            # Keep the metadata cache bounded so a long browse doesn't hold
            # every title's info in RAM. A miss just refetches (a few ms), so
            # dropping the oldest entries has no visible effect. dicts preserve
            # insertion order, so the first key is the oldest.
            while len(self._info_cache) > 250:
                self._info_cache.pop(next(iter(self._info_cache)))
            self._show_media_info(info, key)

        run_async(self.pool, fetch, done, lambda _: None)

    def _show_media_info(self, info: dict, key) -> None:
        if key != self._current_key:
            return
        self._clear_epg_rows()
        td = self._tmdb_details or {}
        plot = str(
            info.get("plot") or info.get("description")
            or td.get("overview") or "").strip()
        self.media_plot.setText(plot)
        self.media_plot.setVisible(bool(plot))

        parts: list[str] = []
        simple = (
            ("Genre", info.get("genre") or td.get("genres")),
            ("Director", info.get("director")),
            ("Released",
             info.get("releasedate") or info.get("releaseDate")
             or td.get("release_date")),
            ("Duration", info.get("duration")),
            ("Rating", info.get("rating")),
        )
        for label, value in simple:
            value = str(value or "").strip()
            if value:
                parts.append(f"<b>{label}:</b> {html.escape(value)}")
        cast = str(info.get("cast") or info.get("actors") or "").strip()
        if cast:
            names = [n.strip() for n in cast.split(",") if n.strip()]
            if self.tmdb:
                rendered = [
                    f'<a href="cast:{html.escape(n)}" '
                    f'style="color:{ACCENT}; text-decoration:none;">'
                    f'{html.escape(n)}</a>' for n in names]
            else:
                rendered = [html.escape(n) for n in names]
            parts.append("<b>Cast:</b> " + ", ".join(rendered))
        elif self.tmdb and td.get("cast"):
            names = [c.get("name") for c in td["cast"]
                     if c.get("name")]
            rendered = [
                f'<a href="cast:{html.escape(n)}" '
                f'style="color:{ACCENT}; text-decoration:none;">'
                f'{html.escape(n)}</a>' for n in names]
            if rendered:
                parts.append("<b>Cast:</b> " + ", ".join(rendered))
        self.media_meta.setText("<br>".join(parts))
        self.media_meta.setVisible(bool(parts))

        self.media_info.setVisible(bool(plot or parts))
        if not (plot or parts):
            self._epg_note("No further information available.")

    def _on_cast_link(self, href: str) -> None:
        if href.startswith("cast:"):
            self._on_cast_clicked(href[len("cast:"):], None)

    def _show_epg(self, listings, key) -> None:
        if key != self._current_key:
            return
        self.now_card.hide()
        self._clear_epg_rows()
        self._current_epg = None
        now = datetime.now().astimezone()
        all_posts, upcoming = [], []
        current = None
        seen: set = set()
        for e in listings or []:
            start, stop = epg_times(e)
            start, stop = self._apply_epg_delay(start), self._apply_epg_delay(stop)
            if e.get("_plain"):
                title, desc = (e.get("title") or "",
                               e.get("description") or "")
            else:
                title, desc = b64(e.get("title")), b64(e.get("description"))
            dedup_key = (int(start.timestamp()) if start else None,
                         title.strip().lower())
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            post = {"title": title, "desc": desc,
                    "start": start, "stop": stop}
            all_posts.append(post)
            if start and stop and start <= now < stop and not current:
                current = post
            elif start and start > now:
                upcoming.append(post)
        upcoming.sort(key=lambda p: p["start"])

        if current:
            self._current_epg = current
            self.now_time.setText(
                f"NOW * {current['start']:%H:%M}-{current['stop']:%H:%M}")
            self.now_title.setText(
                current["title"] or "Unknown programme")
            self.now_desc.setText(current["desc"][:400])
            self._refresh_progress()
            if not self._player_fs:
                self.now_card.show()
            if self.player:
                info = (
                    f"{self._detail_name}\n"
                    f"{current['title'] or 'Unknown programme'}   "
                    f"{current['start']:%H:%M}-{current['stop']:%H:%M}")
                nxt = upcoming[0] if upcoming else None
                if nxt:
                    info += (f"\nNext: {nxt['title'] or '?'}"
                             f" at {nxt['start']:%H:%M}")
                self.player.set_overlay_info(info)

        for post in upcoming[:6]:
            self._epg_card(post)

        if not current and not upcoming:
            dated = sorted(
                (p for p in all_posts if p["start"]),
                key=lambda p: p["start"])
            if dated:
                self._epg_note(
                    "The server's schedule times look wrong - "
                    "showing the most recent entries anyway.")
                for post in dated[-6:]:
                    self._epg_card(post, with_date=True)
            else:
                self._epg_note(
                    "No programme guide available for this channel.")

    def _epg_card(self, post, with_date: bool = False) -> None:
        card = QFrame(objectName="Card")
        kl = QVBoxLayout(card)
        kl.setContentsMargins(12, 9, 12, 9)
        kl.setSpacing(2)
        fmt = "%-d/%-m %H:%M" if with_date else "%H:%M"
        t = QLabel(post["start"].strftime(fmt), objectName="EpgRowTime")
        ti = QLabel(post["title"] or "Unknown", objectName="EpgRowTitle")
        ti.setWordWrap(True)
        kl.addWidget(t)
        kl.addWidget(ti)
        self.epg_lay.insertWidget(self.epg_lay.count() - 1, card)

    def _refresh_progress(self) -> None:
        e = self._current_epg
        if not e:
            return
        now = datetime.now().astimezone()
        if now >= e["stop"]:
            self._current_epg = None
            self._request_epg()
            return
        total = (e["stop"] - e["start"]).total_seconds()
        if total > 0:
            pct = (now - e["start"]).total_seconds() / total * 100
            self.now_bar.setValue(max(0, min(100, int(pct))))
