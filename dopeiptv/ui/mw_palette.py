"""The command palette in the window: what it finds, what a row does.

Ctrl+K (⌘K; a rebindable key, core/shortcut_keys.py) puts it up over the
window. It finds, as it is typed in:

- ACTIONS: the app menu's entries and the rebindable actions, each with
  its key (the player's own only while the player is up);
- SETTINGS: every row the Settings search reaches (SettingsShell.index),
  Nerd's in its colour and only while Nerd is on - chosen, Settings opens
  on its page with the row lit;
- CHANNELS, SERIES AND PROGRAMMES: the active source's channels by name or
  number, its series by title and the guide's programmes from now on by
  title - a channel chosen plays, a series opens its page
  (ui/mw_series_page.py), a programme offers play, record and remind, as
  the guide does - and last "Search everywhere for …", the Search page
  with what was typed, across every source (ui/mw_search_all.py).

The channels and the guide are folded once per opening on a worker
(core/palette_match.py); the series follow in a job of their own, never
holding the channels up - their list is asked for at most once per source
in a session, and never of a portal (slow_links): only what it holds. A query over a few thousand of them is a pass of
substring tests on the window's thread; over more (tens of thousands of
channels, hundreds of thousands of programmes: a few tenths of a second, a
stutter in the video) it runs on a worker of its own, the actions and
settings shown at once and the channels added when they are in - only
while the query is still the one typed. Settings are read from the
Settings window itself, built once (and once more after a language change)
for its index. Nothing searched is kept anywhere but in memory, and not even
there during private viewing.
"""

from __future__ import annotations

import copy
import time
from functools import partial

from PyQt6 import sip
from PyQt6.QtCore import QObject, QPoint, QThreadPool, QTimer
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QApplication, QMenu

from .. import APP_NAME
from ..core import channel_numbers, privacy, profiles, shortcut_keys
from ..core.log import log
from ..core.palette_match import Index, fold, words
from ..core.search_all import MIN_QUERY
from ..core.workers import run_async
from ..i18n import tr
from .app_menu import GROUPS as MENU_GROUPS
from .app_menu import in_app_menu
from .command_palette import CommandPalette, Entry
from .qt_i18n import format_when

#: The guide is looked through from now for this long.
GUIDE_HOURS = 12
#: A few of each, the best first.
LIMITS = {"actions": 4, "settings": 4, "channels": 4, "series": 3, "programmes": 3}
#: Shown before anything is typed.
FIRST_ACTIONS = 8
#: Channel names and programme titles matched on the window's thread at
#: most; a guide bigger than this is matched on a worker.
MATCH_HERE = 5000
#: Rebindable actions that act on the player: offered while it is up.
_PLAYER_ONLY = frozenset({"pause", "fullscreen", "mute", "popout", "record", "stats"})
_PLAYER_KIND = _PLAYER_ONLY | {"zap_next", "zap_prev", "last_channel", "night_mode"}
_MENU_KIND = {"file": "menu_file", "view": "menu_view", "history": "nav_history",
              "help": "menu_help"}


class PaletteCatalogue:
    """The active source's channels and the guide's programmes from now
    on, folded for matching. Built on a worker; read on the window's
    thread."""

    def __init__(self, key: tuple, chans: list[dict], cats: dict[str, str],
                 numbers: list[str], airings: list[tuple[dict, dict]],
                 series: list[dict] | None = None,
                 series_cats: dict[str, str] | None = None) -> None:
        self.key = key
        self.chans = chans
        self.cats = cats
        self.numbers = numbers
        self.by_number: dict[str, list[int]] = {}
        for i, n in enumerate(numbers):
            if n:
                self.by_number.setdefault(n, []).append(i)
        self.names = Index(str(c.get("name") or "") for c in chans)
        self.airings = airings
        self.titles = Index(str(p.get("title") or "") for _c, p in airings)
        self.series = list(series or [])
        self.series_cats = dict(series_cats or {})
        self.series_names = Index(str(s.get("name") or s.get("title") or "")
                                  for s in self.series)

    def size(self) -> int:
        """How much a query goes through (on a worker past MATCH_HERE)."""
        return len(self.names) + len(self.titles) + len(self.series_names)

    def with_series(self, series: list[dict], cats: dict[str, str],
                    names: Index | None = None) -> PaletteCatalogue:
        """This catalogue with *series* in it (folded here unless *names*
        has them already): the channels' own folding is kept."""
        out = copy.copy(self)
        out.series = list(series)
        out.series_cats = dict(cats)
        out.series_names = names if names is not None else Index(
            str(s.get("name") or s.get("title") or "") for s in out.series)
        return out


def _series_rows(client, may_fetch: bool) -> tuple[list[dict], dict[str, str]] | None:
    """On a worker: the source's series and their categories' names - as
    the client holds them, else (*may_fetch*) asked for; None when it holds
    none and may not ask."""
    held = getattr(client, "held_series", None)
    got = held() if callable(held) else None
    if got is None:
        if not may_fetch:
            return None
        rows = client.series_list(None) or []
        try:
            cat_rows = client.series_categories() or []
        except Exception:
            cat_rows = []
    else:
        rows, cat_rows = got
    cats = {str(c.get("category_id")): str(c.get("category_name") or "")
            for c in cat_rows if isinstance(c, dict)}
    return [s for s in rows if isinstance(s, dict) and s.get("series_id") is not None], cats


def _number(value) -> str:
    text = str(value if value is not None else "").strip()
    return text.lstrip("0") or ("0" if text else "")


def _channel_hits(cat: PaletteCatalogue, query: str, terms: list[str],
                  latest: list[int] | None = None, gen: int = 0) -> tuple | None:
    """(channels, programmes): the positions of what *query* finds in
    *cat*, the best first - the channel by its number, then by name. On a
    worker for a big guide; None there when a newer query came meanwhile
    (*latest* holds the newest one's *gen*)."""
    if latest is not None and latest[0] != gen:
        return None
    hits: list[int] = []
    q = query.strip()
    if q.isdigit():
        hits = cat.by_number.get(_number(q), [])[:2]
    for i in cat.names.search(query, LIMITS["channels"], terms):
        if i not in hits:
            hits.append(i)
    if latest is not None and latest[0] != gen:
        return None
    return hits[:LIMITS["channels"]], cat.titles.search(query, LIMITS["programmes"] * 3, terms)


def _all_hits(cat: PaletteCatalogue, query: str, terms: list[str],
              latest: list[int] | None = None, gen: int = 0) -> tuple | None:
    """_channel_hits and the series *query* finds: (channels, programmes,
    series), or None when a newer query came meanwhile."""
    found = _channel_hits(cat, query, terms, latest, gen)
    if found is None or (latest is not None and latest[0] != gen):
        return None
    return found[0], found[1], cat.series_names.search(query, LIMITS["series"], terms)


class _PaletteMixin:
    """MainWindow mixin: the command palette."""

    # -- open, close -------------------------------------------------------------

    def _open_palette(self) -> None:
        if getattr(self, "_tv_on", False):
            return            # TV mode takes the keys: the palette's field would get none
        active = QApplication.activeWindow()
        if active is not None and active is not self:
            return            # the pop-out, multiview: the palette is the window's
        pal = getattr(self, "_palette", None)
        if pal is not None and not sip.isdeleted(pal) and pal.isVisible():
            pal.field.selectAll()
            pal.field.setFocus()
            return
        self._palette_focus = QApplication.focusWidget()
        self._pal_actions = self._palette_action_entries()
        self._pal_action_index = Index((f"{e.text} {e.kind}" for e in self._pal_actions),
                                       (e.text for e in self._pal_actions))
        pal = CommandPalette(self, self._palette_search, self._palette_run,
                             self.shortcut_sequence("palette"))
        pal.closed.connect(self._palette_closed)
        self._palette = pal
        last = "" if privacy.private() else getattr(self, "_palette_last", "")
        pal.open(last)
        self._palette_load_catalogue()
        if getattr(self, "_settings_index", None) is None:
            # After the palette is on screen: the Settings window is built
            # for its index once, which takes a moment.
            QTimer.singleShot(40, self._palette_index_settings)

    def _palette_open(self) -> CommandPalette | None:
        pal = getattr(self, "_palette", None)
        if pal is None or sip.isdeleted(pal) or not pal.isVisible():
            return None
        return pal

    def _palette_closed(self) -> None:
        pal = getattr(self, "_palette", None)
        self._palette = None
        if pal is not None and not sip.isdeleted(pal):
            # Only in memory, and never while viewing privately.
            self._palette_last = "" if privacy.private() else pal.query()
            pal.deleteLater()
        back = getattr(self, "_palette_focus", None)
        self._palette_focus = None
        if back is not None and not sip.isdeleted(back) and back.isVisible():
            back.setFocus()

    def _palette_retranslate(self) -> None:
        """The language changed: the settings are named anew next time."""
        self._settings_index = None
        pal = self._palette_open()
        if pal is not None:
            pal.close_palette()

    # -- what is found -----------------------------------------------------------

    def _palette_action_entries(self) -> list[Entry]:
        keys = self.shortcut_keys()
        cbs = self._shortcut_callbacks()
        up = self._player_up()
        out: list[Entry] = []
        labels: dict[str, str] = {}
        for sid, _default, label in shortcut_keys.ACTIONS:
            if sid == "palette" or (sid in _PLAYER_ONLY and not up):
                continue
            labels[fold(tr(label))] = sid
            kind = tr("pal_kind_player") if sid in _PLAYER_KIND else tr("menu_view")
            out.append(Entry("actions", kind, tr(label), keys=keys.get(sid, ""),
                             payload=("call", cbs[sid])))
        acts = getattr(self, "_menu_actions", {})
        for _group, where, names in MENU_GROUPS:
            menu = tr(_MENU_KIND[where]) if where in _MENU_KIND else APP_NAME
            for name in names:
                # About, Settings, Quit and the supporter's entries are in
                # the application menu on macOS: named by it there.
                kind = APP_NAME if in_app_menu(name) else menu
                act = acts.get(name)
                if act is None or sip.isdeleted(act) or not act.isVisible():
                    continue
                enabled = self.can_cast_playing() if name == "cast" else act.isEnabled()
                if not enabled:
                    continue
                text = act.text().replace("&", "").rstrip("…").strip()
                if fold(text) in labels:
                    continue          # the same as a rebindable one, listed with its key
                seq = act.shortcut().toString(QKeySequence.SequenceFormat.PortableText)
                out.append(Entry("actions", kind, text, keys=seq, payload=("action", act)))
        return out

    def _palette_settings_lookup(self) -> Index | None:
        rows = getattr(self, "_settings_index", None)
        if not rows:
            return None
        cached = getattr(self, "_pal_settings_idx", None)
        if cached is None or cached[0] is not rows:
            cached = (rows, Index((f"{r['name']} {r['text']}" for r in rows),
                                 (r["name"] for r in rows)))
            self._pal_settings_idx = cached
        return cached[1]

    def _palette_index_settings(self) -> None:
        if self._palette_open() is None or getattr(self, "_settings_index", None) is not None:
            return
        try:
            self.open_settings(index_only=True)
        except Exception:
            log.exception("palette: the settings could not be indexed")
            self._settings_index = []
        pal = self._palette_open()
        if pal is None:
            return
        if QApplication.activeWindow() is None:
            # Never left without the keys: the window's shortcuts need it active.
            self.activateWindow()
        pal.field.setFocus()
        if pal.query().strip():
            pal.refresh()

    def _palette_source_name(self) -> str:
        store = getattr(self, "playlist_store", None)
        pl = store.get(store.active_id) if (store and store.active_id) else None
        return str((pl or {}).get("name") or (pl or {}).get("server") or "")

    def _palette_load_catalogue(self) -> None:
        """The channels and the guide, folded on a worker. The last ones
        answer meanwhile, while they are the same source's. The series come
        after, in a job of their own (_palette_load_series)."""
        client = self.client
        xmltv = getattr(self, "xmltv", None)
        # Whose catalogue: another profile (a kids one sees less), or the
        # household's lock opened or shut, is another one.
        key = (id(client), id(xmltv), self._chno_pid(), profiles.active_id(),
               bool(self.parental.session_unlocked))
        have = getattr(self, "_pal_catalogue", None)
        if have is not None and have.key != key:
            self._pal_catalogue = None
        keep = self._catalog_filter("live")
        numbers = channel_numbers.active(self._chno_pid())
        now = time.time()

        def work() -> PaletteCatalogue:
            chans = [c for c in (client.live_streams(None) or []) if keep(c)]
            cats: dict[str, str] = {}
            try:
                for c in client.live_categories() or []:
                    cats[str(c.get("category_id"))] = str(c.get("category_name") or "")
            except Exception:
                cats = {}
            nums = [_number(channel_numbers.shown(c, numbers)) for c in chans]
            airings = (xmltv.airings(chans, now, now + GUIDE_HOURS * 3600)
                       if xmltv is not None and hasattr(xmltv, "airings") else [])
            return PaletteCatalogue(key, chans, cats, nums, airings)

        run_async(self.pool, work, self._palette_catalogue_ready,
                  self._palette_catalogue_failed)

    def _palette_catalogue_ready(self, cat: PaletteCatalogue) -> None:
        old = getattr(self, "_pal_catalogue", None)
        if old is not None and old.key == cat.key and old.series:
            # The same source and profile: its series as they were.
            cat = cat.with_series(old.series, old.series_cats, old.series_names)
        self._pal_catalogue = cat
        if not cat.series:
            self._palette_load_series(cat)
        pal = self._palette_open()
        if pal is not None and pal.query().strip():
            pal.refresh()

    def _palette_series_source(self) -> str:
        store = getattr(self, "playlist_store", None)
        return str(getattr(store, "active_id", None) or id(self.client))

    def _palette_load_series(self, cat: PaletteCatalogue) -> None:
        """The source's series into *cat*, on a worker, once the channels
        are in: their list is a provider's whole catalogue (a portal's up to
        two hundred pages), so it is asked for at most once per source in a
        session, and never of a portal (slow_links) - there only what the
        client holds already is looked through."""
        client = self.client
        if cat.key[0] != id(client):
            return
        source = self._palette_series_source()
        had = self.__dict__.setdefault("_pal_series", {}).get(source)
        asked = self.__dict__.setdefault("_pal_series_asked", set())
        may_fetch = source not in asked and not getattr(client, "slow_links", False)
        if had is None and may_fetch:
            asked.add(source)
        keep = self._catalog_filter("series")

        def work() -> tuple:
            got = had if had is not None else _series_rows(client, may_fetch)
            if got is None:
                return None, None
            rows, cats = got
            return got, cat.with_series([s for s in rows if keep(s)], cats)

        run_async(self.pool, work, partial(self._palette_series_ready, cat, source, client),
                  self._palette_series_failed)

    def _palette_series_ready(self, cat: PaletteCatalogue, source: str, client,
                              done: tuple) -> None:
        got, with_series = done
        if got is not None and client is self.client:
            self._pal_series[source] = got
        if with_series is None or getattr(self, "_pal_catalogue", None) is not cat:
            return
        self._pal_catalogue = with_series
        pal = self._palette_open()
        if pal is not None and pal.query().strip():
            pal.refresh()

    def _palette_series_failed(self, err) -> None:
        log.debug("palette: no series to look through: %s", err)

    def _palette_catalogue_failed(self, err) -> None:
        log.debug("palette: no channels to look through: %s", err)

    def _palette_search(self, query: str) -> list[Entry]:
        """The rows for *query*: a few of each group, the best first."""
        terms = words(query)
        acts = getattr(self, "_pal_actions", [])
        if not terms:
            return acts[:FIRST_ACTIONS]
        out = [acts[i] for i in self._pal_action_index.search(query, LIMITS["actions"], terms)]
        out += self._palette_find_settings(query, terms)
        cat = getattr(self, "_pal_catalogue", None)
        if cat is not None and cat.size() > MATCH_HERE:
            self._palette_match_later(cat, query, terms, out)
            return out + self._palette_everywhere(query)
        out += self._palette_find_channels(query, terms)
        return out + self._palette_everywhere(query)

    @staticmethod
    def _palette_everywhere(query: str) -> list[Entry]:
        """The last row: the Search page with *query*, across every source
        (none for a query it would find nothing for)."""
        q = " ".join(query.split())
        if len(" ".join(words(q))) < MIN_QUERY:
            return []
        return [Entry("channels", tr("nav_search"), tr("pal_search_all", query=q),
                      more=tr("pal_search_all_more"), payload=("search_all", q))]

    def _palette_match_later(self, cat: PaletteCatalogue, query: str, terms: list[str],
                             now: list[Entry]) -> None:
        """A big guide's channels and programmes for *query*, matched on a
        worker of the palette's own; *now* (the actions and settings) and
        what it finds go in once it is done - if the query is still the
        one typed (CommandPalette.deliver)."""
        pal = getattr(self, "_palette", None)        # opening: not on screen yet
        if pal is None or sip.isdeleted(pal):
            return
        latest = getattr(self, "_pal_latest", None)
        if latest is None:
            latest = self._pal_latest = [0]
        latest[0] += 1
        pool = getattr(self, "_pal_pool", None)
        if pool is None:
            pool = self._pal_pool = QThreadPool(self) if isinstance(self, QObject) else QThreadPool()
            pool.setMaxThreadCount(1)              # one at a time: the newest wins anyway
        pal.expect(query)
        run_async(pool, partial(_all_hits, cat, query, terms, latest, latest[0]),
                  partial(self._palette_matched, latest[0], cat, query, now),
                  partial(self._palette_match_failed, latest[0], cat, query, now))

    def _palette_matched(self, gen: int, cat: PaletteCatalogue, query: str,
                         now: list[Entry], hits) -> None:
        pal = self._palette_open()
        if (hits is None or pal is None or gen != getattr(self, "_pal_latest", [0])[0]
                or getattr(self, "_pal_catalogue", None) is not cat):
            return
        pal.deliver(query, now + self._palette_channel_entries(cat, *hits)
                    + self._palette_everywhere(query))

    def _palette_match_failed(self, gen: int, cat: PaletteCatalogue, query: str,
                              now: list[Entry], err) -> None:
        log.debug("palette: the channels were not looked through: %s", err)
        self._palette_matched(gen, cat, query, now, ([], [], []))

    def _palette_find_settings(self, query: str, terms: list[str]) -> list[Entry]:
        idx = self._palette_settings_lookup()
        if idx is None:
            return []
        rows = self._settings_index
        nerd = self.settings.value("nerd_enabled", "false") == "true"
        out: list[Entry] = []
        for i in idx.search(query, 40, terms):
            r = rows[i]
            if r["gated"] and not nerd:
                continue
            kind = tr("nerd_page") if r["nerd"] else tr("pal_kind_setting")
            out.append(Entry("settings", kind, r["name"], where=r["where"], nerd=r["nerd"],
                             more=r["hint"], payload=("setting", r)))
            if len(out) >= LIMITS["settings"]:
                break
        return out

    def _palette_find_channels(self, query: str, terms: list[str]) -> list[Entry]:
        cat = getattr(self, "_pal_catalogue", None)
        if cat is None:
            return []
        found = _all_hits(cat, query, terms)
        return self._palette_channel_entries(cat, *found) if found else []

    def _palette_channel_entries(self, cat: PaletteCatalogue, hits: list[int],
                                 titles: list[int],
                                 series: list[int] | None = None) -> list[Entry]:
        """The rows of what _all_hits found (the window's thread)."""
        source = self._palette_source_name()
        out: list[Entry] = []
        for i in hits:
            ch = cat.chans[i]
            category = cat.cats.get(str(ch.get("category_id")), "")
            where = " · ".join(t for t in (cat.numbers[i], source, category) if t)
            out.append(Entry("channels", tr("pal_kind_channel"), str(ch.get("name") or ""),
                             where=where, more=where, payload=("channel", ch)))
        for i in series or []:
            row = cat.series[i]
            category = cat.series_cats.get(str(row.get("category_id")), "")
            where = " · ".join(t for t in (source, category) if t)
            plot = " ".join(str(row.get("plot") or "").split())[:240]
            out.append(Entry("channels", tr("misc_series"),
                             str(row.get("name") or row.get("title") or ""),
                             where=where, more=" · ".join(t for t in (where, plot) if t),
                             payload=("series", row)))
        now = time.time()
        shown = 0
        for i in titles:
            ch, p = cat.airings[i]
            if p["stop_timestamp"] <= now:
                continue
            on_air = p["start_timestamp"] <= now
            when = tr("pal_now") if on_air else format_when(p["start_timestamp"], "HH:mm")
            where = str(ch.get("name") or "")
            if ch.get("stream_id") is not None:
                where += " · " + tr("pal_record_remind")
            span = (f"{format_when(p['start_timestamp'], 'ddd HH:mm')}–"
                    f"{format_when(p['stop_timestamp'], 'HH:mm')}")
            more = " · ".join(t for t in (span, str(p.get("description") or "")[:240]) if t)
            out.append(Entry("channels", tr("pal_kind_programme"),
                             f"{p.get('title') or ''} · {when}", where=where, more=more,
                             payload=("programme", (ch, p))))
            shown += 1
            if shown >= LIMITS["programmes"]:
                break
        return out

    # -- what a row does ----------------------------------------------------------

    def _palette_run(self, entry: Entry, pos: QPoint) -> None:
        kind, what = entry.payload
        if kind == "programme":
            self._palette_programme_menu(*what).exec(pos)
            return
        self._palette_then(kind, what)

    def _palette_programme_menu(self, ch: dict, p: dict) -> QMenu:
        """Play, record, remind - what the guide offers for a programme."""
        m = QMenu(self)
        now = time.time()
        m.addAction(tr("epg_play_channel"), partial(self._palette_then, "channel", ch))
        sid = ch.get("stream_id")
        if sid is not None and p["stop_timestamp"] > now:
            m.addAction(tr("rec_record_programme"),
                        partial(self._palette_then, "record", (ch, p)))
        rem = getattr(self, "reminders", None)
        if sid is not None and p["start_timestamp"] > now and rem is not None:
            has = rem.has(sid, p["start_timestamp"])
            m.addAction(tr("reminder_remove" if has else "reminder_add"),
                        partial(self._palette_then, "remind", (ch, p)))
        return m

    def _palette_then(self, kind: str, what) -> None:
        """Close the palette, then do *kind* - once the key that chose it
        is through."""
        self._pal_pending = (kind, what)
        pal = self._palette_open()
        if pal is not None:
            pal.close_palette()
        QTimer.singleShot(0, self._palette_do_pending)

    def _palette_do_pending(self) -> None:
        pending, self._pal_pending = getattr(self, "_pal_pending", None), None
        if pending is None:
            return
        kind, what = pending
        if kind in ("action", "call"):
            # Chosen in the palette's field: the single-key actions do not
            # take that for typing.
            self._palette_running = True
            try:
                if kind == "call":
                    what()
                elif isinstance(what, QAction) and not sip.isdeleted(what):
                    what.trigger()
            finally:
                self._palette_running = False
            return
        if kind == "setting":
            self.open_settings(at_page=what["page"],
                               at_row=(what["card"], what["row"], what["name"]))
        elif kind == "channel":
            self.tune_from_guide(what)
        elif kind == "series":
            self._open_series_page(what)
        elif kind == "search_all":
            self._open_search_page(what)
        elif kind == "record":
            ch, p = what
            self._record_programme(ch, {"title": p.get("title")},
                                   p["start_timestamp"], p["stop_timestamp"])
        elif kind == "remind":
            ch, p = what
            rem = getattr(self, "reminders", None)
            if rem is not None and rem.has(ch.get("stream_id"), p["start_timestamp"]):
                rem.remove(ch.get("stream_id"), p["start_timestamp"])
            else:
                self._add_reminder(ch, p)
