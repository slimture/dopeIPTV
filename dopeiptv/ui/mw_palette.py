"""The command palette in the window: what it finds, what a row does.

Ctrl+K (⌘K; a rebindable key, core/shortcut_keys.py) puts it up over the
window. It finds, as it is typed in:

- ACTIONS: the app menu's entries and the rebindable actions, each with
  its key (the player's own only while the player is up);
- SETTINGS: every row the Settings search reaches (SettingsShell.index),
  Nerd's in its colour and only while Nerd is on - chosen, Settings opens
  on its page with the row lit;
- CHANNELS AND PROGRAMMES: the active source's channels by name or number,
  and the guide's programmes from now on by title - a channel chosen plays,
  a programme offers play, record and remind, as the guide does.

The channels and the guide are folded once per opening on a worker
(core/palette_match.py). A query over a few thousand of them is a pass of
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

import time
from functools import partial

from PyQt6 import sip
from PyQt6.QtCore import QObject, QPoint, QThreadPool, QTimer
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QApplication, QMenu

from .. import APP_NAME
from ..core import channel_numbers, privacy, shortcut_keys
from ..core.log import log
from ..core.palette_match import Index, fold, words
from ..core.workers import run_async
from ..i18n import tr
from .app_menu import GROUPS as MENU_GROUPS
from .command_palette import CommandPalette, Entry
from .qt_i18n import format_when

#: The guide is looked through from now for this long.
GUIDE_HOURS = 12
#: A few of each, the best first.
LIMITS = {"actions": 4, "settings": 4, "channels": 4, "programmes": 3}
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
                 numbers: list[str], airings: list[tuple[dict, dict]]) -> None:
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


class _PaletteMixin:
    """MainWindow mixin: the command palette."""

    # -- open, close -------------------------------------------------------------

    def _open_palette(self) -> None:
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
            kind = tr(_MENU_KIND[where]) if where in _MENU_KIND else APP_NAME
            for name in names:
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
        answer meanwhile, while they are the same source's."""
        client = self.client
        xmltv = getattr(self, "xmltv", None)
        key = (id(client), id(xmltv), self._chno_pid())
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
        self._pal_catalogue = cat
        pal = self._palette_open()
        if pal is not None and pal.query().strip():
            pal.refresh()

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
        if cat is not None and len(cat.names) + len(cat.titles) > MATCH_HERE:
            self._palette_match_later(cat, query, terms, out)
            return out
        out += self._palette_find_channels(query, terms)
        return out

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
        run_async(pool, partial(_channel_hits, cat, query, terms, latest, latest[0]),
                  partial(self._palette_matched, latest[0], cat, query, now),
                  partial(self._palette_match_failed, latest[0], cat, query, now))

    def _palette_matched(self, gen: int, cat: PaletteCatalogue, query: str,
                         now: list[Entry], hits) -> None:
        pal = self._palette_open()
        if (hits is None or pal is None or gen != getattr(self, "_pal_latest", [0])[0]
                or getattr(self, "_pal_catalogue", None) is not cat):
            return
        pal.deliver(query, now + self._palette_channel_entries(cat, *hits))

    def _palette_match_failed(self, gen: int, cat: PaletteCatalogue, query: str,
                              now: list[Entry], err) -> None:
        log.debug("palette: the channels were not looked through: %s", err)
        self._palette_matched(gen, cat, query, now, ([], []))

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
        found = _channel_hits(cat, query, terms)
        return self._palette_channel_entries(cat, *found) if found else []

    def _palette_channel_entries(self, cat: PaletteCatalogue, hits: list[int],
                                 titles: list[int]) -> list[Entry]:
        """The rows of what _channel_hits found (the window's thread)."""
        source = self._palette_source_name()
        out: list[Entry] = []
        for i in hits:
            ch = cat.chans[i]
            category = cat.cats.get(str(ch.get("category_id")), "")
            where = " · ".join(t for t in (cat.numbers[i], source, category) if t)
            out.append(Entry("channels", tr("pal_kind_channel"), str(ch.get("name") or ""),
                             where=where, more=where, payload=("channel", ch)))
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
