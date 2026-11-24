"""MainWindow mixin: offline mode in the window (core/offline.py holds the
state, its rules and the guard every request takes; providers/downloaded.py
the lists it shows).

* **The system's word** (``_init_offline``, before the window's first list):
  Qt's QNetworkInformation where its backend loads - read, never asked of
  anyone; ``DOPEIPTV_NETWORK`` instead where it says so. No backend, no
  detection: the window opens as ever. Settled by core/offline.Watch, a
  QTimer for what is still to settle (``_off_said``, ``_off_tick``).
* **By hand**: View's... no - File's "Work offline" (a checkable action, so
  the command palette lists it too) and the strip's "Go online"
  (``set_offline``). It lasts the session.
* **Going offline** (``_off_enter``): the source's client steps aside for
  its downloads (``DownloadedClient``: the Movies and Series places, a
  series' page, Mine, Search, a kids profile's lists and the phone's read
  them), what plays from the internet stops as Stop does, multiview closes,
  Watch together is left, the downloads wait; TV and Radio say they need the
  network (``_off_note_sync``), a passing note says it, the strip stays.
* **Coming back** (``_off_leave``): the source's own client made again and
  its login asked off the window's thread as at the start (its account's end
  said; a login that fails for want of a network takes offline mode back,
  ``_offline_unreached``), its lists and guide read again, the downloads go
  on; nothing opens more than before at once.
* **The strip** (``_build_offline_strip``): "Offline – only what is
  downloaded is shown" over the middle pane - a calm line of its own, the
  pane's width whether the sidebar is full or the icon rail, held while the
  video fills the screen, the picture is popped out or the welcome covers the
  window (as the notices are, ``_notices_held``), wrapped in a narrow window
  and mirrored right to left; TV mode says it in its header.
* **What still plays**: a file (a download, a local file, a recording), and
  a stream at home; anything on the internet is refused at the player's door
  with a calm word (``_offline_may_play``). A downloaded title says
  "Offline" by its title while it plays (``_offline_mark``).
"""

from __future__ import annotations

import os
import time
from functools import partial
from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy

from ..core import offline
from ..core.log import log
from ..i18n import tr
from .theme import P

#: The folder under the app's cache where a series' episodes are kept for a
#: series' page offline (core/downloads.keep_episodes).
EPISODES_CACHE = "series-episodes"


def _network_information() -> Any:
    """Qt's QNetworkInformation with a backend that knows reachability, or
    None: QtNetwork not loadable here (#36's runtime without Kerberos'
    library), no backend - then there is no detection. Never raises."""
    try:
        from PyQt6.QtNetwork import QNetworkInformation
    except (ImportError, OSError) as e:
        log.info("offline mode: Qt's network library is not there (%s) - not detected", e)
        return None
    try:
        feature = QNetworkInformation.Feature.Reachability
        if not QNetworkInformation.loadBackendByFeatures(feature):
            log.info("offline mode: no network information on this system - not detected")
            return None
        info = QNetworkInformation.instance()
    except Exception as e:
        log.info("offline mode: the network information did not load (%s)", e)
        return None
    if info is not None:
        try:
            log.info("offline mode: the system's word from Qt's %s backend", info.backendName())
        except Exception:
            pass
    return info


def word_of(reach: Any) -> str:
    """QNetworkInformation.Reachability as core/offline's word."""
    name = str(getattr(reach, "name", reach) or "").lower()
    return {"disconnected": offline.DISCONNECTED, "local": offline.LOCAL, "site": offline.SITE,
            "online": offline.ONLINE}.get(name, offline.UNKNOWN)


class _Follow(QObject):
    """Places the TV note again whenever the list moves or changes size."""

    def __init__(self, parent: QObject, place) -> None:
        super().__init__(parent)
        self._place = place

    def eventFilter(self, obj, event) -> bool:        # noqa: N802 - Qt hook
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Move):
            self._place()
        return False


class _OfflineMixin:
    """MainWindow mixin: offline mode."""

    _off_watch: offline.Watch | None = None
    _off_info: Any = None
    _off_timer: QTimer | None = None
    _off_strip: QFrame | None = None
    _off_label: QLabel | None = None
    _off_btn: QPushButton | None = None
    _off_note: QLabel | None = None
    _off_action: QAction | None = None
    #: Where a series' episodes are kept for a series' page offline.
    _off_episodes = ""
    #: Every change counts one: a login asked as offline mode ended that
    #: comes back after another change is left alone.
    _off_gen = 0

    @staticmethod
    def _offline() -> bool:
        """Whether offline mode is on (the places and features ask it)."""
        return offline.on()

    # -- the system's word ------------------------------------------------------------

    def _init_offline(self) -> None:
        """Before the window's first list: the system's word now (no wait -
        Qt answers from what it knows), the source's lists its downloads'
        when it says there is no network."""
        try:
            from ..core.workers import default_image_cache_dir
            self._off_episodes = str(default_image_cache_dir(EPISODES_CACHE))
        except Exception:
            self._off_episodes = ""
        self._off_watch = offline.Watch(home_source=self._off_home_source())
        t = QTimer(self)
        t.setSingleShot(True)
        t.timeout.connect(self._off_tick)
        self._off_timer = t
        word = self._off_listen()
        if word:
            # At the start the system's word is as it is now: settled.
            self._off_watch.said(word, time.monotonic() - offline.SETTLE_S)
            change = self._off_watch.tick(time.monotonic(), offline.mode(), offline.why())
            if change is not None:
                offline.set_mode(*change)
        if offline.on():
            self._off_take_client()
            QTimer.singleShot(0, lambda: self._off_say(True))

    def _off_listen(self) -> str:
        """The system's word now, and its changes from here on ("" for no
        detection)."""
        env = offline.env_word()
        if env is not None:
            if env:
                log.info("offline mode: the network taken as %s (%s)", env, offline.ENV)
            else:
                log.info("offline mode: not detected (%s)", offline.ENV)
            return env
        info = _network_information()
        if info is None:
            return ""
        try:
            info.reachabilityChanged.connect(self._off_reach)
            self._off_info = info
            return word_of(info.reachability())
        except Exception as e:
            log.info("offline mode: the network information is not read (%s)", e)
            return ""

    def _off_home_source(self) -> bool:
        """Whether the source in use is at home: there only no network at all
        turns offline mode on by itself - with the local network it answers."""
        store = getattr(self, "playlist_store", None)
        pl = store.active() if store is not None else None
        if not isinstance(pl, dict):
            return False
        kind, server = str(pl.get("kind") or "xtream"), str(pl.get("server") or "")
        if kind == "m3u" and not server.lower().startswith(("http://", "https://")):
            return False                # a file's list: its streams may be anywhere
        try:
            from ..core.net_bridge import _source_on_internet
            return not _source_on_internet(kind, server, pl.get("options") or {})
        except Exception:
            return False

    def _off_reach(self, reach: Any) -> None:
        self._off_said(word_of(reach))

    def _off_said(self, word: str) -> None:
        """The system said *word*: offline mode follows once it held."""
        w = self._off_watch
        if w is None:
            return
        log.info("offline mode: the system says %s", word)
        w.said(word, time.monotonic())
        self._off_schedule()

    def _off_schedule(self) -> None:
        w, t = self._off_watch, self._off_timer
        if w is None or t is None:
            return
        t.start(int(w.wait_left(time.monotonic()) * 1000) + 50)

    def _off_tick(self) -> None:
        w = self._off_watch
        if w is None:
            return
        now = time.monotonic()
        if w.wait_left(now) > 0:
            self._off_schedule()
            return
        change = w.tick(now, offline.mode(), offline.why())
        if change is not None:
            self._offline_apply(*change)

    # -- the person's switch, the login's hint ------------------------------------------

    def set_offline(self, on: bool) -> None:
        """Offline mode on or off by hand (the menu, the palette, the strip)."""
        w = self._off_watch or offline.Watch()
        self._off_watch = w
        self._offline_apply(*w.by_hand(bool(on), time.monotonic()))

    def _offline_unreached(self, exc: BaseException | None) -> bool:
        """The source's login failed: when for want of a network, offline mode
        on by itself (until the system's word changes). True when it did."""
        if not offline.no_network(exc):
            return False
        w = self._off_watch
        change = w.unreached(offline.mode()) if w is not None else None
        if change is None:
            return False
        log.info("offline mode: the source could not be reached - no network")
        self._offline_apply(*change)
        return True

    def _offline_apply(self, mode: str, why: str) -> None:
        was = offline.on()
        offline.set_mode(mode, why)
        on = offline.on()
        if was != on:
            self._off_gen += 1
            try:
                if on:
                    self._off_enter()
                else:
                    self._off_leave()
            except Exception:
                log.exception("offline mode: the window did not follow")
        self._off_show()
        if was != on:
            self._off_say(on)

    def _off_say(self, on: bool) -> None:
        """A passing note, never a message or a question."""
        show = getattr(self, "_show_toast", None)
        if not callable(show):
            return
        if on:
            show(tr("offline_note_hand") if offline.mode() == offline.HAND
                 else tr("offline_note_on"), 5000)
        else:
            show(tr("offline_note_off"), 4000)

    # -- going offline, and back ----------------------------------------------------------

    def _offline_client(self, pid: str) -> Any:
        """Source *pid*'s lists as offline mode shows them."""
        from ..providers.downloaded import DownloadedClient
        return DownloadedClient(getattr(self, "downloads", None), pid, tr("nav_downloads"),
                                known=partial(self._off_known, pid))

    def _off_known(self, pid: str, series_id: str) -> list[dict]:
        """(a worker) What the app kept of a series' episodes."""
        if not self._off_episodes:
            return []
        from ..core import downloads as dl
        return dl.known_episodes(self._off_episodes, pid, series_id)

    def _off_take_client(self) -> None:
        if getattr(self.client, "offline", False):
            return
        store = getattr(self, "playlist_store", None)
        pid = str(getattr(store, "active_id", "") or "") if store is not None else ""
        if not pid:
            return                      # explore mode, the demo: nothing to stand in for
        self._off_held = self.client
        self.client = self._offline_client(pid)

    def _off_enter(self) -> None:
        """Offline mode began (the module's docstring)."""
        self._off_stop_network()
        if getattr(self, "_together_on", lambda: False)():
            self.together_leave(quiet=True)
        self._off_take_client()
        dl_tick = getattr(self, "_dl_tick", None)
        if callable(dl_tick):
            dl_tick()
        self._off_reload()

    def _off_stop_network(self) -> None:
        """What plays from the internet stops as Stop does; multiview's
        cells, which all do, close."""
        mv = getattr(self, "_close_multiview_if_active", None)
        if callable(mv):
            try:
                mv()
            except Exception:
                log.debug("offline mode: multiview not closed", exc_info=True)
        p = getattr(self, "player", None)
        url = str(getattr(p, "current_url", None) or "")
        if p is not None and url and offline.stream_refusal(url):
            log.info("offline mode: what plays from the network stops")
            try:
                p.stop_btn.click()
            except Exception:
                log.debug("offline mode: the player not stopped", exc_info=True)

    def _off_leave(self) -> None:
        """Offline mode ended: the source as at the start."""
        held, self._off_held = getattr(self, "_off_held", None), None
        store = getattr(self, "playlist_store", None)
        pl = store.active() if store is not None else None
        if getattr(self.client, "offline", False):
            if isinstance(pl, dict):
                from ..providers.client import make_client
                self.client = make_client(pl)
            elif held is not None:
                self.client = held
        if isinstance(pl, dict):
            self._off_login(pl)
        dl_tick = getattr(self, "_dl_tick", None)
        if callable(dl_tick):
            dl_tick()
        self._off_reload()

    def _off_login(self, pl: dict) -> None:
        """The source's login, asked off the window's thread as at the start:
        its account's end said, a pinned server's new certificate offered -
        or, failing for want of a network, offline mode back."""
        from ..core.workers import run_async
        client, gen = self.client, self._off_gen

        def ask() -> tuple[str, Any]:
            try:
                return "ok", client.authenticate()
            except Exception as e:      # handed to the window's thread as it is
                return "err", e

        def done(got: tuple[str, Any]) -> None:
            if gen != self._off_gen or self.client is not client or sip.isdeleted(self):
                return
            what, value = got
            if what == "ok":
                exp = getattr(self, "_expiry_check", None)
                if callable(exp):
                    exp(pl, value)
                return
            if self._offline_unreached(value):
                return
            try:
                from .cert_trust import offer_review
                offer_review(self, self.playlist_store, pl, self.switch_playlist)
            except Exception:
                log.debug("offline mode: the certificate review not offered", exc_info=True)

        run_async(self.pool, ask, done, lambda _m: None)

    def _off_reload(self) -> None:
        """The lists, the places and the guide as the client in use has them."""
        self._full_catalog = None
        self._search_index_cache = {}
        try:
            self.refresh_playlist(force=False)
        except Exception:
            log.exception("offline mode: the lists not read again")
        for hook in ("_sp_source_changed", "_pf_source_changed", "_place_series_source_changed",
                     "_popular_changed", "_search_sources_changed"):
            fn = getattr(self, hook, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    log.debug("offline mode: %s", hook, exc_info=True)
        refresh = getattr(self, "_refresh_places", None)
        if callable(refresh):
            refresh("offline")
        radio = getattr(self, "_place_radio_refresh", None)
        if callable(radio):
            radio()
        # TV mode's screens read the lists anew too, shown or not (as a
        # profile switch has them).
        tv = getattr(self, "_tv_page", None)
        if tv is not None and not sip.isdeleted(tv):
            for scr in tv.screens.values():
                if hasattr(scr, "rebuild"):
                    try:
                        scr.rebuild()
                    except Exception:
                        log.debug("offline mode: TV mode's screen", exc_info=True)

    def _offline_downloads_changed(self) -> None:
        """A download came, went, or the folder was read (mw_downloads):
        offline mode's lists - which are the downloads - read again."""
        if not getattr(self.client, "offline", False):
            return
        self._full_catalog = None
        self._search_index_cache = {}
        try:
            if getattr(self, "mode", "") in ("vod", "series") \
                    and not getattr(self, "series_ctx", None):
                self._load_categories()
        except Exception:
            log.debug("offline mode: the lists not read again", exc_info=True)
        refresh = getattr(self, "_refresh_places", None)
        if callable(refresh):
            refresh("offline")

    def _offline_source_switched(self, pid: str) -> Any:
        """switch_playlist while offline: the new source's downloads as its
        client - nothing asked of it."""
        w = self._off_watch
        if w is not None:
            w.home_source = self._off_home_source()
        return self._offline_client(pid)

    def _offline_source_changed(self) -> None:
        """Another source in use (online): whether only the local network
        counts as offline for it."""
        w = self._off_watch
        if w is not None:
            w.home_source = self._off_home_source()

    # -- what is shown ------------------------------------------------------------------

    def _build_offline_action(self) -> QAction:
        """File's "Work offline": checked while offline mode is on."""
        act = QAction(tr("offline_menu"), self)
        act.setCheckable(True)
        act.setChecked(offline.on())
        act.toggled.connect(self._off_action_toggled)
        self._off_action = act
        return act

    def _off_action_toggled(self, on: bool) -> None:
        if bool(on) != offline.on():
            self.set_offline(bool(on))

    def _build_offline_strip(self, layout) -> None:
        """The strip over the middle pane (mw_build): in *layout*, before the
        pane's pages."""
        strip = QFrame(objectName="OfflineStrip")
        strip.setAccessibleName(tr("offline_strip"))
        lay = QHBoxLayout(strip)
        lay.setContentsMargins(14, 6, 10, 6)
        lay.setSpacing(10)
        label = QLabel(tr("offline_strip"), objectName="OfflineStripText")
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        label.setToolTip(tr("offline_strip_tip"))
        btn = QPushButton(tr("offline_go_online"), objectName="OfflineStripButton")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setAccessibleName(tr("offline_go_online"))
        btn.clicked.connect(lambda: self.set_offline(False))
        lay.addWidget(label, 1)
        lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(strip)
        self._off_strip, self._off_label, self._off_btn = strip, label, btn
        self._offline_restyle()
        strip.setVisible(False)
        QTimer.singleShot(0, self._off_show)

    def _offline_restyle(self) -> None:
        strip = self._off_strip
        if strip is None or sip.isdeleted(strip):
            return
        strip.setStyleSheet(
            f"QFrame#OfflineStrip {{ background:{P['sel']}; border:none;"
            f" border-bottom:1px solid {P['border']}; }}"
            f"QLabel#OfflineStripText {{ color:{P['text']}; font-size:12px;"
            f" font-weight:600; background:transparent; }}"
            f"QPushButton#OfflineStripButton {{ background:{P['btn']}; color:{P['text']};"
            f" border:none; border-radius:8px; padding:4px 12px; font-size:12px; }}"
            f"QPushButton#OfflineStripButton:hover {{ background:{P['btn_hover']}; }}")
        note = self._off_note
        if note is not None and not sip.isdeleted(note):
            note.setStyleSheet(f"color:{P['muted']}; font-size:14px; background:transparent;")

    def _offline_retranslate(self) -> None:
        if self._off_action is not None and not sip.isdeleted(self._off_action):
            self._off_action.setText(tr("offline_menu"))
        if self._off_label is not None and not sip.isdeleted(self._off_label):
            self._off_label.setText(tr("offline_strip"))
            self._off_label.setToolTip(tr("offline_strip_tip"))
        if self._off_btn is not None and not sip.isdeleted(self._off_btn):
            self._off_btn.setText(tr("offline_go_online"))
            self._off_btn.setAccessibleName(tr("offline_go_online"))
        if self._off_strip is not None and not sip.isdeleted(self._off_strip):
            self._off_strip.setAccessibleName(tr("offline_strip"))
        self._off_note_sync()

    def _off_show(self) -> None:
        """The strip, the menu's check, TV mode's header and TV's and Radio's
        word, as offline mode is now."""
        on = offline.on()
        act = self._off_action
        if act is not None and not sip.isdeleted(act) and act.isChecked() != on:
            act.blockSignals(True)
            act.setChecked(on)
            act.blockSignals(False)
        self._off_strip_sync()
        self._off_note_sync()
        tv = getattr(self, "_tv_page", None)
        if tv is not None and not sip.isdeleted(tv) and hasattr(tv, "refresh_source"):
            tv.refresh_source()
        nav = getattr(self, "_refresh_nav_status", None)
        if callable(nav):
            try:
                nav()
            except Exception:
                pass

    def _off_strip_sync(self) -> None:
        strip = self._off_strip
        if strip is None or sip.isdeleted(strip):
            return
        held = getattr(self, "_notices_held", None)
        covered = bool(held()) if callable(held) else False
        strip.setVisible(offline.on() and not covered)

    def offline_held_changed(self) -> None:
        """The video left full screen, the welcome went...: the strip again."""
        self._off_strip_sync()

    # -- TV and Radio need the network -----------------------------------------------------

    def _off_note_sync(self) -> None:
        """TV's list says it needs the network while offline mode is on - a
        calm word in the place, not an error."""
        lw = getattr(self, "listw", None)
        if lw is None or sip.isdeleted(lw):
            return
        note = self._off_note
        if note is None or sip.isdeleted(note):
            note = QLabel(lw.viewport(), objectName="OfflineNote")
            note.setTextFormat(Qt.TextFormat.PlainText)
            note.setWordWrap(True)
            note.setAlignment(Qt.AlignmentFlag.AlignCenter)
            note.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self._off_note = note
            self._offline_restyle()
            lw.viewport().installEventFilter(_Follow(note, self._off_place_note))
        live = (getattr(self, "mode", "") == "live" and not getattr(self, "series_ctx", None))
        show = offline.on() and live
        if show:
            note.setText(tr("offline_tv"))
            self._off_place_note()
            note.raise_()
        note.setVisible(show)

    def _off_place_note(self) -> None:
        note, lw = self._off_note, getattr(self, "listw", None)
        if note is None or lw is None or sip.isdeleted(note):
            return
        vp = lw.viewport()
        w = max(120, min(460, vp.width() - 40))
        h = note.heightForWidth(w) if note.hasHeightForWidth() else note.sizeHint().height()
        note.setGeometry((vp.width() - w) // 2, max(16, vp.height() // 3 - h // 2), w, max(h, 20))

    # -- the player's door, the mark ---------------------------------------------------

    def _offline_may_play(self, url: str, kind: str) -> bool:
        """The player's door while offline mode is on: a file, or a stream at
        home - anything on the internet is not opened, said calmly."""
        why = offline.stream_refusal(str(url or ""))
        if not why:
            return True
        log.info("offline mode: %s not played - it needs the network", kind or "a stream")
        if getattr(self, "_tv_on", False) and callable(getattr(self, "_tv_tell", None)):
            # TV mode holds the notices: said as TV mode says things.
            self._tv_tell(tr("offline_mark"), tr("offline_needs_network"))
            return False
        show = getattr(self, "_show_toast", None)
        if callable(show):
            show(tr("offline_needs_network"), 5000)
        return False

    def _offline_playing_download(self) -> bool:
        """Offline mode is on and what plays is a downloaded file."""
        if not offline.on():
            return False
        lp = getattr(self, "_last_playback", None) or {}
        path = str(lp.get("url") or "")
        d = getattr(self, "downloads", None)
        if not path or d is None or "://" in path:
            return False
        try:
            return any(i.path == path for i in d.items())
        except Exception:
            return False

    def _offline_mark(self, text: str) -> str:
        """*text* with "Offline" beside it while a downloaded title plays in
        offline mode ("Playing: X · Offline")."""
        if not text or not self._offline_playing_download():
            return text
        return f"{text} · {tr('offline_mark')}"

    # -- a series' episodes, kept for offline --------------------------------------------

    def _offline_keep_episodes(self, series: dict, episodes: list) -> None:
        """A series' page read its episodes (online): kept, for its page
        offline, when an episode of it is downloaded or on its way - on the
        pool, never while viewing privately."""
        if offline.on() or not self._off_episodes or not isinstance(series, dict):
            return
        sid = series.get("series_id")
        store = getattr(self, "playlist_store", None)
        pid = str(getattr(store, "active_id", "") or "") if store is not None else ""
        d = getattr(self, "downloads", None)
        if sid in (None, "") or not pid or d is None:
            return
        from ..core import privacy
        if privacy.private():
            return
        has = getattr(self, "_dl_series_has", None)
        queued = any(j.source == pid and j.kind == "episode"
                     and str(j.meta.get("series_id") or "") == str(sid) for j in d.jobs())
        if not queued and not (callable(has) and has(series)):
            return
        rows = [dict(e) for e in episodes if isinstance(e, dict)]
        from ..core import downloads as dl
        from ..core.workers import run_async
        folder = self._off_episodes
        run_async(self.pool, lambda: dl.keep_episodes(folder, pid, sid, rows), lambda _r: None,
                  lambda _m: None)

    # -- what the window says of a path ---------------------------------------------------

    @staticmethod
    def _offline_file(path: Any) -> bool:
        text = str(path or "")
        return bool(text) and "://" not in text and os.path.isabs(text)
