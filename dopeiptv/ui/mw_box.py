"""The box itself, from the app: zap it to a channel, wake it, put it to
standby, and set a recording on it from the guide (it records whether
the app runs or not). Only there when the source is such a box - an
Enigma2 receiver zaps and records; Tvheadend, VDR (restfulapi), DVBViewer,
NextPVR and MythTV servers record - so no one else sees a thing. Nerd adds
Copy stream address. A channel a source lists without a play address
(MythTV's, for the guide) says why it does not play.

An Enigma2 box also has: its info, a screenshot of its TV and its remote
(ui/box_tools.py); its recordings renamed, moved and deleted from the
films' menu; its tuner's signal in the stream stats (I), asked every 2 s
while they show; its transcoding for the player (and, if its source says
so, casting and multiview); and a channel it cannot decrypt said so - in
the player, stream health, multiview and recordings - with failover then
free to try another source. Every request to the box runs off the
window's thread."""

from __future__ import annotations

import os
import time
from typing import Any, Callable

from PyQt6.QtWidgets import QApplication, QMenu

from ..core.log import log
from ..core.workers import run_async
from ..i18n import tr
from .widgets import confirm

BOX_SIGNAL_MS = 2000
# How much of a recorded file is read to tell whether it is encrypted.
SCRAMBLE_FILE_BYTES = 188 * 2000


class _BoxMixin:
    def _box_run(self, fn: Callable[..., Any], ok: Callable[[Any], str], *args: Any) -> None:
        """Ask the box off the window's thread; say how it went."""
        def done(result: Any) -> None:
            text = ok(result)
            if text:
                self._show_toast(text, 4000)

        def failed(err: Any = None) -> None:
            msg = getattr(err, "localized", None)
            self._show_toast(tr("box_failed", why=msg() if callable(msg) else str(err or "")), 6000)
        run_async(self.pool, fn, done, failed, *args)

    def _box_channel_menu(self, menu: QMenu, it: dict) -> None:
        """A channel's right-click menu: the box's own actions, and with Nerd
        its play address to copy."""
        from ..core import nerd
        c, sid = self.client, it.get("stream_id")
        self._box_recording_menu(menu, it)
        if sid is None or self.mode != "live":
            return
        if hasattr(c, "zap"):
            sub = menu.addMenu(tr("box_menu"))
            sub.addAction(tr("box_zap"), lambda: self._box_run(
                c.zap, lambda ok: tr("box_zapped") if ok else tr("box_failed", why=""), sid))
            sub.addAction(tr("box_wake"), lambda: self._box_run(
                c.power, lambda _ok: tr("box_woken"), 4))
            sub.addAction(tr("box_standby"), lambda: self._box_run(
                c.power, lambda _ok: tr("box_sleeping"), 5))
            self._box_windows_menu(sub, c)
        if nerd.current().on:
            menu.addAction(tr("nerd_copy_url"), lambda: self._copy_stream_url(it))
            menu.addAction(tr("nerd_epg_map"), lambda: self._map_guide_channel(it))

    def _map_guide_channel(self, it: dict) -> None:
        """Nerd: which of the guide's channels this channel's programmes come
        from, chosen by hand (or back to the automatic match)."""
        from PyQt6.QtWidgets import QInputDialog

        from ..core import nerd_lists
        pl = self.playlist_store.active() if self.playlist_store else None
        pid = (pl or {}).get("id")
        if not pid:
            return
        ids = sorted(getattr(self.xmltv, "_by_id", {}) or {})
        auto = tr("nerd_epg_auto")
        current = str(it.get("epg_channel_id") or "")
        items = [auto, *ids]
        at = items.index(current) if current in items else 0
        choice, ok = QInputDialog.getItem(
            self, tr("nerd_epg_map"), tr("nerd_epg_map_hint", name=it.get("name") or ""),
            items, at, True)
        if not ok:
            return
        epg_id = "" if choice in ("", auto) else choice.strip()
        nerd_lists.set_epg_map(self.settings, pid, it["stream_id"], epg_id)
        if epg_id:
            it.setdefault("_epg_own", it.get("epg_channel_id") or "")
            it["epg_channel_id"] = epg_id
        else:
            it["epg_channel_id"] = it.pop("_epg_own", it.get("epg_channel_id") or "")
        cur = self.listw.currentIndex()
        if cur.isValid():
            self._on_current_changed(cur)

    def _copy_stream_url(self, it: dict) -> None:
        try:
            url = self.client.live_url(it["stream_id"])
        except Exception:
            url = ""
        if url:
            QApplication.clipboard().setText(url)
            self._show_toast(tr("nerd_url_copied"), 3000)

    def _box_record_action(self, menu: QMenu, ch: dict, title: str,
                           start: float, stop: float) -> None:
        """Record on the box, in a programme's menu, for a programme not
        over yet."""
        c, sid = self.client, ch.get("stream_id")
        if sid is None or not hasattr(c, "add_timer") or stop <= time.time():
            return
        start = max(start, time.time())

        def said(result: Any) -> str:
            ok, text = result if isinstance(result, tuple) else (bool(result), "")
            return tr("box_timer_set", title=title or "") if ok else tr("box_failed", why=text)
        menu.addAction(tr("box_record"), lambda: self._box_run(
            c.add_timer, said, sid, start, stop, title or ""))

    def _box_no_live(self, it: dict | None) -> bool:
        """A channel its source lists without a play address (MythTV's,
        there for the guide and to record on): say why it does not play.
        True when it was one."""
        if not (it or {}).get("_no_live"):
            return False
        note = str(getattr(self.client, "live_note", "") or "")
        if note:
            self._show_toast(note, 6000)
        return True

    # -- the address a live channel plays from (a box's transcoding) -----------

    def _live_play_url(self, stream_id: Any, fmt: str = "ts", purpose: str = "play") -> str:
        """A live channel's address for the player (*purpose* "play"), a cast
        or a multiview cell: a box that transcodes says which (its source's
        own options); every other source's live_url as it is."""
        play = getattr(self.client, "play_url", None)
        if callable(play):
            return play(stream_id, purpose)
        return self.client.live_url(stream_id, fmt)

    # -- the box's own windows -------------------------------------------------

    def _box_windows_menu(self, sub: QMenu, c: Any) -> None:
        """Info, a screenshot and the remote, for a box that has them."""
        if not hasattr(c, "remote"):
            return
        sub.addSeparator()
        sub.addAction(tr("box_info"), self._box_open_info)
        sub.addAction(tr("box_screenshot"), self._box_open_screenshot)
        sub.addAction(tr("box_remote"), self._box_open_remote)

    def _box_window(self, make: Callable[[], Any]) -> None:
        """One of each window per box: an open one comes forward."""
        open_ = getattr(self, "_box_open", None)
        if open_ is None:
            open_ = self._box_open = {}
        key = (make.__name__, id(self.client))
        w = open_.get(key)
        from PyQt6 import sip
        if w is not None and not sip.isdeleted(w) and w.isVisible():
            w.raise_()
            w.activateWindow()
            return
        w = make()
        open_[key] = w
        w.show()

    def _box_name(self) -> str:
        pl = self.playlist_store.active() if getattr(self, "playlist_store", None) else None
        return str((pl or {}).get("name") or (self.client.box_info or {}).get("name") or "")

    def _box_open_info(self) -> None:
        from .box_tools import BoxInfoDialog

        def info() -> Any:
            return BoxInfoDialog(self, self.client, self.pool)
        self._box_window(info)

    def _box_open_screenshot(self) -> None:
        from .box_tools import BoxScreenshotDialog

        def screenshot() -> Any:
            return BoxScreenshotDialog(self, self.client, self.pool)
        self._box_window(screenshot)

    def _box_open_remote(self) -> None:
        from .box_tools import BoxRemoteDialog

        def remote() -> Any:
            return BoxRemoteDialog(self, self.client, self.pool, self._box_name())
        self._box_window(remote)

    # -- the box's recordings ----------------------------------------------------

    def _box_recording_menu(self, menu: QMenu, it: dict) -> None:
        """A recording on the box, in the films' menu: rename, move, delete."""
        from ..providers.boxes import RECORDINGS
        c, rid = self.client, it.get("stream_id")
        if (rid is None or self.mode != "vod" or it.get("category_id") != RECORDINGS
                or not hasattr(c, "movie_delete") or not c.is_movie(rid)):
            return
        sub = menu.addMenu(tr("box_rec_menu"))
        sub.addAction(tr("ctx_rename"), lambda: self._box_rec_rename(it))
        sub.addAction(tr("box_rec_move"), lambda: self._box_rec_move(it))
        sub.addSeparator()
        sub.addAction(tr("box_rec_delete"), lambda: self._box_rec_delete(it))

    def _box_rec_done(self, said: str) -> Callable[[Any], str]:
        """What a recording's change says when the box has made it - and the
        list shown again with it."""
        def done(result: Any) -> str:
            ok, text = result if isinstance(result, tuple) else (bool(result), "")
            if not ok:
                return tr("box_failed", why=text)
            if self.mode == "vod":
                self._load_items(getattr(self, "_current_cat", None))
            return said
        return done

    def _box_rec_delete(self, it: dict) -> None:
        title = str(it.get("name") or "")
        if not confirm(self, tr("box_rec_delete"), tr("box_rec_delete_q", title=title),
                       default_yes=False):
            return
        self._box_run(self.client.movie_delete, self._box_rec_done(
            tr("box_rec_deleted", title=title)), it["stream_id"])

    def _box_rec_rename(self, it: dict) -> None:
        from PyQt6.QtWidgets import QInputDialog
        title = str(it.get("name") or "")
        name, ok = QInputDialog.getText(self, tr("ctx_rename"),
                                        tr("box_rec_rename_q", title=title), text=title)
        name = (name or "").strip()
        if not ok or not name or name == title:
            return
        if "/" in name:
            self._show_toast(tr("box_rec_bad_name"), 5000)
            return
        self._box_run(self.client.movie_rename, self._box_rec_done(
            tr("box_rec_renamed", title=name)), it["stream_id"], name)

    def _box_rec_move(self, it: dict) -> None:
        """The box's folders asked for first (off the window's thread), then
        where to."""
        client = self.client

        def chosen(folders: Any) -> None:
            from PyQt6.QtWidgets import QInputDialog
            if client is not self.client:
                return
            title = str(it.get("name") or "")
            items = [f for f in (folders or []) if isinstance(f, str)] or ["/media/hdd/movie/"]
            folder, ok = QInputDialog.getItem(self, tr("box_rec_move"),
                                              tr("box_rec_move_q", title=title), items, 0, True)
            folder = (folder or "").strip()
            if not ok or not folder.startswith("/"):
                return
            self._box_run(client.movie_move, self._box_rec_done(
                tr("box_rec_moved", title=title, folder=folder)), it["stream_id"], folder)
        self._box_run(client.movie_folders, lambda found: chosen(found) or "")

    # -- the signal in the stream stats ------------------------------------------

    def _box_stats_shown(self, on: bool) -> None:
        """The stream stats came up or went: the box's signal is asked every
        2 s while they show, never otherwise."""
        timer = getattr(self, "_box_sig_timer", None)
        if not on:
            if timer is not None:
                timer.stop()
            if self.player is not None:
                self.player.stats_extra = []
            return
        if timer is None:
            from PyQt6.QtCore import QTimer
            timer = self._box_sig_timer = QTimer(self)
            timer.setInterval(BOX_SIGNAL_MS)
            timer.timeout.connect(self._box_signal_poll)
        timer.start()
        self._box_signal_poll()

    def _box_tuned_now(self) -> Any:
        """The box client whose own tuned channel plays now, or None."""
        c, it = self.client, getattr(self, "_playing_item", None) or {}
        if (not hasattr(c, "signal") or getattr(self, "_playing_group", None) != "live"
                or getattr(self, "_playing_catchup", False) or it.get("stream_id") is None):
            return None
        try:
            return c if c.tuned(it["stream_id"]) else None
        except Exception:
            return None

    def _box_signal_poll(self) -> None:
        c = self._box_tuned_now()
        if c is None:
            if self.player is not None:
                self.player.stats_extra = []
            return
        if getattr(self, "_box_sig_busy", False):
            return
        self._box_sig_busy = True
        run_async(self.pool, c.signal, self._box_signal_came, self._box_signal_failed)

    def _box_signal_came(self, sig: Any) -> None:
        from ..providers.enigma2 import signal_rows
        self._box_sig_busy = False
        timer = getattr(self, "_box_sig_timer", None)
        if self.player is None or timer is None or not timer.isActive():
            return
        self.player.stats_extra = (signal_rows(sig if isinstance(sig, dict) else None)
                                   if self._box_tuned_now() is not None else [])

    def _box_signal_failed(self, err: Any = None) -> None:
        self._box_sig_busy = False
        log.debug("box signal not read: %s", err)
        if self.player is not None:
            self.player.stats_extra = []

    # -- a channel the box cannot decrypt --------------------------------------

    def _box_no_picture(self) -> None:
        """A live channel froze with its quick reconnects spent: from a box,
        read the first packets of its stream - one the box cannot decrypt is
        said so (the player, stream health), and failover may then try
        another source. Nothing for any other source."""
        lp = getattr(self, "_last_playback", None) or {}
        url, key = lp.get("url"), lp.get("key")
        check = getattr(self.client, "scrambled", None)
        if not (url and callable(check)) or lp.get("kind") != "live":
            return
        hkey = self.health.current

        def said(found: Any) -> None:
            if found is not True or getattr(self, "_playing_key", None) != key:
                return
            log.info("live: the box cannot decrypt what plays - no picture to wait for")
            # The slow reconnect would only meet the same encrypted stream.
            self._slow_reconnect_key = None
            self._diag_shown = True
            self._health_failed(tr("diag_scrambled"), hkey)
            self._box_scrambled_key = key
            if not self._try_failover(self._box_scrambled_end):
                self._box_scrambled_end()
        run_async(self.pool, check, said, None, url)

    def _box_scrambled_end(self) -> None:
        """No other source plays it: the encrypted stream is let go and the
        reason said, where every other failure's is."""
        if getattr(self, "_playing_key", None) != getattr(self, "_box_scrambled_key", None):
            return
        if self.player is not None and self.player.current_url:
            # The Stop button, not stop() alone: the window also ends the
            # recording, the scrobbles and the wake lock on it.
            self.player.stop_btn.click()
        self._show_stream_error(tr("diag_scrambled"))

    def _box_rec_stopped(self, title: str, reason: str) -> None:
        """A recording from a box ended: one that failed, or whose file holds
        only encrypted packets, is said to be a channel the box cannot
        decrypt (asked off the window's thread)."""
        for j in list(getattr(self.rec, "jobs", [])):
            if (j.get("title") != title or j.get("_box_checked")
                    or j.get("status") not in ("done", "failed")):
                continue
            client = self._box_client_for(j)
            if client is None:
                continue
            j["_box_checked"] = True
            run_async(self.pool, _recording_scrambled, self._box_rec_said, None,
                      client, dict(j))

    def _box_client_for(self, job: dict) -> Any:
        """The box a recording was made from, when it is one that can tell
        an encrypted channel; None for every other source."""
        pid = str(job.get("source") or "")
        store = getattr(self, "playlist_store", None)
        if not pid or (store is not None and pid == store.active_id):
            return self.client if hasattr(self.client, "scrambled") else None
        pl = store.get(pid) if store is not None else None
        if not pl or pl.get("kind") != "enigma2":
            return None
        from ..providers.client import make_client
        try:
            return make_client(pl)
        except Exception:
            return None

    def _box_rec_said(self, result: Any) -> None:
        if not (isinstance(result, tuple) and result[1] is True):
            return
        from ..core.recording import SCRAMBLED
        jid = result[0]
        for j in self.rec.jobs:
            if j.get("id") == jid:
                j["status"], j["error"] = "failed", SCRAMBLED
                self._flash_status("● " + tr("rec_stopped_named", title=j.get("title") or "",
                                             reason=tr("rec_reason_scrambled")))
                self.rec.jobs_changed.emit()
                return


def _recording_scrambled(client: Any, job: dict) -> tuple[str, bool | None]:
    """(off the window's thread) (job id, whether what it recorded is
    encrypted): its file's first packets when it wrote one, else the
    stream's own."""
    from ..core import ts_scramble
    path = str(job.get("path") or "")
    found: bool | None = None
    if path and os.path.isfile(path):
        try:
            with open(path, "rb") as fh:
                found = ts_scramble.verdict(fh.read(SCRAMBLE_FILE_BYTES))
        except OSError:
            found = None
    if found is None and job.get("status") == "failed" and job.get("url"):
        found = client.scrambled(str(job["url"]))
    return str(job.get("id") or ""), found
