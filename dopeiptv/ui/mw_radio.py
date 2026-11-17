"""Internet radio in the window (mode "radio"): radio-browser.info's
directory as a place in the nav, right under Local files - music on this
computer, then music from the internet.

The sidebar holds the ways in: the person's favourite stations, the most
voted and the most listened, a search of the whole directory, and the
countries, languages and genres (each a list of its own to open, the back
button out again). A station plays in the embedded player as audio, with
the music view local files have: the visualiser and equaliser, its logo and
what the station says is on now (its ICY title) in the detail panel.

Favourite stations are favourites of their own kind (``radio_favorites``,
by station id, no source's): they show under Favourites too, and sync and
back up with the rest. A click is reported to the directory only when
Settings > Integrations says so (off by default). Every request runs on the
pool (providers/radio_browser.py).
"""

from __future__ import annotations

from functools import partial

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QListWidgetItem, QMenu

from ..core.log import log
from ..core.privacy import for_log, private
from ..core.stores import FAV_DEFAULT_GROUP
from ..core.workers import run_async
from ..i18n import tr
from ..providers import radio_browser as rb

FAVS, SEARCH = "__favs__", "__search__"
#: The sidebar's rows, in order: (label key, category data).
CATEGORIES = (("radio_cat_favorites", FAVS),
              ("radio_cat_top_voted", rb.TOP_VOTED),
              ("radio_cat_top_clicked", rb.TOP_CLICKED),
              ("radio_cat_search", SEARCH),
              ("radio_cat_countries", rb.COUNTRY),
              ("radio_cat_languages", rb.LANGUAGE),
              ("radio_cat_genres", rb.TAG))
GROUP_KINDS = (rb.COUNTRY, rb.LANGUAGE, rb.TAG)
#: How often the ICY title of the station on is read.
ICY_POLL_MS = 5000
#: What a favourite keeps of a station.
_FAV_FIELDS = ("stationuuid", "name", "url", "stream_icon", "homepage", "country",
               "countrycode", "language", "tags", "codec", "bitrate", "hls")


def station_sub(st: dict) -> str:
    """The line under a station's name: where it is, what it plays, how."""
    parts = []
    if st.get("country"):
        parts.append(str(st["country"]))
    tags = st.get("tags")
    if isinstance(tags, list) and tags:
        parts.append(", ".join(str(t) for t in tags[:3]))
    quality = " ".join(x for x in (
        str(st.get("codec") or ""),
        tr("radio_kbps", n=st["bitrate"]) if st.get("bitrate") else "") if x)
    if quality:
        parts.append(quality)
    return " · ".join(parts)


def fav_snapshot(st: dict) -> dict:
    snap = {k: st.get(k) for k in _FAV_FIELDS if st.get(k) not in (None, "", [])}
    snap["_kind"] = "radio"
    snap["_key"] = "radio:" + str(st.get("stationuuid") or "")
    return snap


class _RadioMixin:
    """MainWindow mixin: the Radio place, its lists and its stations."""

    _radio_client: rb.RadioBrowser | None = None
    _radio_gen = 0
    #: The country, language or genre open: (kind, value, name), or None.
    _radio_group: tuple[str, str, str] | None = None
    _radio_restoring = False
    _radio_icy_timer: QTimer | None = None
    _radio_icy_now = ""

    def _radio(self) -> rb.RadioBrowser:
        if self._radio_client is None:
            self._radio_client = rb.RadioBrowser()
        return self._radio_client

    # -- the sidebar and the list ----------------------------------------------------

    def _load_radio_categories(self) -> None:
        self.cat_list.blockSignals(True)
        for key, data in CATEGORIES:
            it = QListWidgetItem(tr(key))
            it.setData(Qt.ItemDataRole.UserRole, data)
            self.cat_list.addItem(it)
        self.cat_list.blockSignals(False)
        # Coming back to Radio lands where it was left - a country open too.
        self._radio_restoring = True
        self._select_remembered_cat(0 if self.radio_favs.items() else 1)
        self._radio_restoring = False

    def _radio_rows(self, stations: list[dict]) -> list[dict]:
        return [{**st, "_sub": station_sub(st)} for st in stations]

    def _radio_fav_rows(self, group: str | None = None) -> list[dict]:
        return self._radio_rows(
            [{**st, "_kind": "radio", "_key": "radio:" + str(st.get("stationuuid"))}
             for st in self.radio_favs.items(group)])

    def _load_radio_items(self, cat) -> None:
        self._radio_gen += 1
        gen = self._radio_gen
        restoring, self._radio_restoring = self._radio_restoring, False
        self.back_btn.hide()
        self._sync_search_hint()       # radio-browser.info under Search
        if cat in GROUP_KINDS:
            grp = self._radio_group
            if restoring and grp and grp[0] == cat:
                self._radio_open_group(*grp)
                return
            self._radio_group = None
            self._radio_show_groups(cat, gen)
            return
        self._radio_group = None
        # Nothing asked of the directory for these (the Radio place's word).
        self._radio_loading = self._radio_error = False
        if cat == FAVS:
            self.all_items = self._radio_fav_rows()
            self._apply_filter()
            if not self.all_items:
                self._set_status(tr("radio_favs_empty"))
            return
        if cat == SEARCH:
            self.all_items = []
            self._apply_filter()
            return
        which = cat if cat in (rb.TOP_VOTED, rb.TOP_CLICKED) else rb.TOP_VOTED
        self._radio_fetch(partial(self._radio().top, which), gen)

    def _radio_show_groups(self, kind: str, gen: int) -> None:
        """Countries, languages or genres: asked once a session."""
        cache = getattr(self, "_radio_cache", None)
        if cache is None:
            cache = self._radio_cache = {}
        if kind in cache:
            self._radio_fetched(gen, True, None, cache[kind])
            return
        self._radio_fetch(partial(self._radio().lists, kind), gen, groups=True,
                          cache_as=kind)

    def _radio_fetch(self, fn, gen: int, groups: bool = False,
                     empty: str | None = None, cache_as: str = "") -> None:
        self._show_busy(tr("radio_loading"))
        self._radio_loading = True
        run_async(self.pool, fn,
                  partial(self._radio_fetched, gen, groups, empty, cache_as=cache_as),
                  partial(self._radio_failed, gen))

    def _radio_fetched(self, gen: int, groups: bool, empty: str | None,
                       rows, cache_as: str = "") -> None:
        rows = list(rows or [])
        if cache_as and rows:
            self._radio_cache[cache_as] = rows
        if gen != self._radio_gen or self.mode != "radio":
            return
        self._hide_busy()
        self._radio_loading = self._radio_error = False
        if groups:
            rows = [{**g, "_sub": tr("count_stations", n=g.get("_count", 0))}
                    for g in rows]
        else:
            rows = self._radio_rows(rows)
        if empty is not None:
            # A search: its answer as it came, not filtered again by the box.
            self._render_rows(rows, "radio", empty)
            return
        self.all_items = rows
        self._apply_filter()
        if not rows:
            self._set_status(tr("radio_none"))
        elif groups:
            # Countries, not stations: the list's own name and how many.
            cur = self.cat_list.currentItem()
            if cur is not None:
                self._set_status(f"{cur.text()} · {len(rows)}")

    def _radio_failed(self, gen: int, msg) -> None:
        if gen != self._radio_gen or self.mode != "radio":
            return
        log.info("radio: list failed: %s", msg)
        self._hide_busy()
        self._radio_loading, self._radio_error = False, True
        self.all_items = []
        self.list_model.set_items([], "radio")
        self._set_status(tr("radio_unreachable"), error=True)

    def _radio_search_hook(self) -> bool:
        """The search box under Radio's Search asks the whole directory, not
        the list on screen. True when it took the search."""
        if (self.mode != "radio" or self._current_cat != SEARCH
                or self._radio_group is not None):
            return False
        text = self.search.text().strip()
        self._radio_gen += 1
        if len(text) < 2:
            self._hide_busy()
            self._radio_loading = self._radio_error = False
            self._render_rows([], "radio", tr("radio_search_hint"))
            return True
        self._radio_fetch(partial(self._radio().search, text), self._radio_gen,
                          empty=tr("radio_search_none"))
        return True

    def _radio_open_group(self, kind: str, value: str, name: str) -> None:
        """A country, language or genre opened: its stations, the back
        button out to the list again."""
        self._radio_group = (kind, value, name)
        self._radio_gen += 1
        self.back_btn.setText("<-  " + tr("btn_back"))
        self.back_btn.show()
        self.search.clear()
        self._radio_fetch(partial(self._radio().group, kind, value),
                          self._radio_gen)

    def _radio_up(self) -> None:
        grp, self._radio_group = self._radio_group, None
        self.back_btn.hide()
        if grp is None:
            return
        self._radio_gen += 1
        # Back on the row that was opened, not at the top of the list.
        self._pending_jump_key = f"radiogroup:{grp[0]}:{grp[1]}"
        QTimer.singleShot(8000, self._clear_pending_jump)
        self._radio_show_groups(grp[0], self._radio_gen)

    # -- playing -------------------------------------------------------------------------

    def _radio_activate(self, it: dict, player=None, external: bool = False) -> None:
        """A row in Radio (or a station from Favourites or History) chosen."""
        if it.get("_kind") == "radiogroup":
            kind, value = (list(it.get("_group") or []) + ["", ""])[:2]
            if kind in GROUP_KINDS and value:
                self._radio_open_group(kind, value, it.get("name") or value)
            return
        self._radio_play(it, player, external)

    def _radio_play(self, it: dict, player=None, external: bool = False) -> None:
        from ..media.players import launch_player
        url = it.get("url") or it.get("_url")
        if not url:
            return
        title = it.get("name") or url
        uuid = str(it.get("stationuuid") or str(it.get("_key") or "")[len("radio:"):])
        if external or player == "vlc":
            launch_player(player or "mpv", url, title, self)
        else:
            # Where it was played from, for the jump to what plays: Radio's
            # list (and a country, language or genre open there), else My
            # stations when it is one of them.
            cur = self.cat_list.currentItem() if self.mode == "radio" else None
            self._radio_from = ((cur.data(Qt.ItemDataRole.UserRole), self._radio_group)
                                if cur is not None else (None, None))
            self._radio_icy_now = ""
            self._start_playback(url, title, it.get("stream_icon"),
                                 "radio:" + uuid, "radio", record=True,
                                 item={**it, "_kind": "radio",
                                       "_key": "radio:" + uuid})
            self._radio_icy_start()
        # Told only when turned on, and never during private viewing.
        if (uuid and self.settings.value(rb.SETTING_CLICKS, "false") == "true"
                and not private()):
            client = self._radio()
            run_async(self.pool, partial(client.click, uuid), self._radio_clicked,
                      self._radio_clicked)
        log.info("radio: playing %s", for_log(title))

    def _radio_jump(self) -> bool:
        """The jump to what plays (the sidebar's logo, Now playing) for a
        station: Radio, the list it was played from - a country, language
        or genre opened again - else My stations, the station picked there
        once its list is in (_pending_jump_key). False when there is
        nowhere to go."""
        cat, grp = getattr(self, "_radio_from", (None, None))
        lp = getattr(self, "_last_playback", None) or {}
        if cat is None and self._radio_is_fav(lp.get("item") or {}):
            cat, grp = FAVS, None
        if cat is None:
            return False
        self._pending_jump_key = self._playing_key
        QTimer.singleShot(8000, self._clear_pending_jump)
        self._radio_group = grp if grp and grp[0] == cat else None
        self._last_cat["radio"] = cat
        if self.mode != "radio":
            self.switch_mode("radio")       # its categories restore cat and the group
            return True
        row = next((i for i in range(self.cat_list.count())
                    if self.cat_list.item(i).data(Qt.ItemDataRole.UserRole) == cat), -1)
        if row < 0:
            return False
        self._radio_restoring = True
        if self.cat_list.currentRow() == row:
            self._load_radio_items(cat)
        else:
            self.cat_list.setCurrentRow(row)
        return True

    def _radio_clicked(self, _result=None) -> None:
        pass

    def _radio_is_playing(self) -> bool:
        lp = getattr(self, "_last_playback", None) or {}
        return (lp.get("kind") == "radio"
                and getattr(self, "_playing_key", None) is not None)

    def _radio_icy_start(self) -> None:
        if self._radio_icy_timer is None:
            self._radio_icy_timer = QTimer(self)
            self._radio_icy_timer.setInterval(ICY_POLL_MS)
            self._radio_icy_timer.timeout.connect(self._radio_icy_tick)
        self._radio_icy_timer.start()

    def _radio_icy_tick(self) -> None:
        """What the station says is on now, in the overlay and the panel."""
        if not self._radio_is_playing() or self.player is None:
            if self._radio_icy_timer is not None:
                self._radio_icy_timer.stop()
            return
        now = self.player.icy_title()
        if now == self._radio_icy_now:
            return
        self._radio_icy_now = now
        lp = getattr(self, "_last_playback", None) or {}
        station = lp.get("title") or ""
        self.player.set_overlay_info(f"{station} — {now}" if now else station)
        # ...and on the Radio place's cards (mw_place_radio).
        getattr(self, "_place_radio_icy", lambda: None)()
        cur = self.listw.currentIndex()
        shown = self.list_model.item_at(cur.row()) if cur.isValid() else None
        if not shown or shown.get("_key") == lp.get("key"):
            self._radio_show_detail(lp.get("item") or {})

    def _radio_show_detail(self, it: dict) -> None:
        """The detail panel for a station: its name, what is on (while it
        plays), where it is from and what it plays."""
        import html
        lp = getattr(self, "_last_playback", None) or {}
        playing = lp.get("kind") == "radio" and lp.get("key") == it.get("_key")
        lines = [f"<b>{html.escape(str(it.get('name') or ''))}</b>"]
        if playing and self._radio_icy_now:
            lines.append("♪ " + html.escape(self._radio_icy_now))
        where = " · ".join(str(x) for x in (it.get("country"), it.get("language"))
                           if x)
        if where:
            lines.append(html.escape(where))
        if it.get("_kind") != "radio" and it.get("_sub"):
            # A country's station count, a web stream's address.
            lines.append(html.escape(str(it["_sub"])))
        tags = it.get("tags")
        if isinstance(tags, list) and tags:
            lines.append(html.escape(", ".join(str(t) for t in tags)))
        quality = " ".join(x for x in (
            str(it.get("codec") or ""),
            tr("radio_kbps", n=it["bitrate"]) if it.get("bitrate") else "") if x)
        if quality:
            lines.append(html.escape(quality))
        self.media_plot.setTextFormat(Qt.TextFormat.RichText)
        self.media_plot.setText("<br>".join(lines))
        self.media_plot.setVisible(True)
        self.media_meta.setVisible(False)
        self.media_info.setVisible(True)

    # -- favourites -------------------------------------------------------------------

    def _radio_is_fav(self, it: dict) -> bool:
        return self.radio_favs.is_favorite(it.get("stationuuid"))

    def _radio_toggle_fav(self, it: dict) -> None:
        uuid = it.get("stationuuid")
        if not uuid:
            return
        if self.radio_favs.is_favorite(uuid):
            self.radio_favs.remove(uuid)
        else:
            self.radio_favs.add(FAV_DEFAULT_GROUP, fav_snapshot(it))
        if self.mode == "radio" and self._current_cat == FAVS:
            self.all_items = self._radio_fav_rows()
            self._apply_filter()
        elif self.mode == "fav":
            self._load_categories()
        else:
            self.list_model.refresh_all()
        # My stations and the stars on the Radio place (mw_place_radio).
        getattr(self, "_place_radio_refresh", lambda: None)()

    def _radio_context_menu(self, pos, it: dict, at=None) -> None:
        """A station's menu, or a country's - at the list's *pos*, or at the
        screen's point *at* (a card of the Radio place)."""
        m = QMenu(self)
        if it.get("_kind") == "radiogroup":
            m.addAction(tr("ctx_open"), partial(self._radio_activate, it))
        else:
            self._stream_row_actions(m, it)
        m.exec(at if at is not None else self.listw.viewport().mapToGlobal(pos))

    def _stream_row_actions(self, m: QMenu, it: dict) -> None:
        """Play, open elsewhere, favourite - for a station, and (from
        mw_webstreams) a web stream: the menu wherever such a row is."""
        m.addAction(tr("ctx_play_in_mpv"), partial(self.play_item, it, "mpv"))
        ext = m.addMenu(tr("ctx_open_externally"))
        ext.addAction("mpv", partial(self.play_item, it, "mpv", True))
        ext.addAction("VLC", partial(self.play_item, it, "vlc", True))
        m.addSeparator()
        if it.get("_kind") == "web":
            fav = self.web_favs.is_favorite(it.get("url"))
            m.addAction(tr("ctx_remove_from_favorites") if fav
                        else tr("ctx_add_to_favorites"),
                        partial(self._web_toggle_fav, it))
            snap = {k: it[k] for k in ("url", "name", "_live") if k in it}
            self._add_fav_folder_menu(m, "web", [snap], self.web_favs)
            self._add_fav_move_menu(m, "web", [snap])
            if fav:
                m.addAction(tr("web_rename"), partial(self._web_rename, it))
            m.addAction(tr("web_copy_address"), partial(self._web_copy, it))
            return
        m.addAction(tr("ctx_remove_from_favorites") if self._radio_is_fav(it)
                    else tr("ctx_add_to_favorites"),
                    partial(self._radio_toggle_fav, it))
        self._add_fav_folder_menu(m, "radio", [fav_snapshot(it)], self.radio_favs)
        self._add_fav_move_menu(m, "radio", [fav_snapshot(it)])
        home = str(it.get("homepage") or "")
        if home.startswith(("http://", "https://")):
            from ..core.xdg import open_url
            m.addAction(tr("radio_homepage"), partial(open_url, home))

    # -- Settings > Integrations -------------------------------------------------------

    def _build_radio_card(self, page) -> None:
        c = page.card("radio-browser.info")
        box = c.toggle(tr("radio_report_clicks"),
                       self.settings.value(rb.SETTING_CLICKS, "false") == "true",
                       tr("radio_report_clicks_hint"))
        box.toggled.connect(self._radio_clicks_switched)

    def _radio_clicks_switched(self, on: bool) -> None:
        self.settings.setValue(rb.SETTING_CLICKS, "true" if on else "false")
