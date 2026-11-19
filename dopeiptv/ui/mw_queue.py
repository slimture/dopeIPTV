"""What is dropped on the window, and the music it plays (MainWindow
mixin). A drop (``dragEnterEvent``, ``dropEvent``): a video or a track
played, an M3U file or a provider's link opening the Add source window
filled in (``_add_dropped_playlist``) - nothing while "Who's watching?"
waits for a PIN (``_drop_waits``), and for a kids profile a source only
with a grown-up's PIN, as in Settings. The play queue of local music
(``queue_add``, ``queue_clear``, ``_queue_step``, ``_play_queued``,
``_place_in_queue``, its window ``open_queue``), in charge only while a
local file plays (``_local_is_playing``) and rolling on at a file's end
for music or an episode alone (``_local_autoadvance_ok``,
``_queue_autoplay``). Sound with no picture (``_on_audio_only``), the
visualiser and the equaliser (``_apply_audio_visuals``, ``_vis_choose``,
``open_equaliser``).
"""

from __future__ import annotations

import os

from PyQt6.QtCore import Qt, QTimer

from ..core.log import log
from ..core.privacy import for_log
from ..i18n import tr
from ..providers.client import DemoClient, OfflineClient, dropped_playlist
from .dialogs import PlaylistDialog
from .notices import hub_of
from .widgets import confirm


class _QueueMixin:

    def _add_dropped_playlist(self, found) -> None:
        """An M3U file or a provider link dropped on the window: the Add
        playlist dialog with it filled in, then on to the new playlist."""
        if not self.playlist_store:
            return
        # Adding a source is Settings' to do: a kids profile needs a
        # grown-up's PIN for it there (mw_profiles), so here too - else a
        # dropped playlist was the way round that.
        unlock = getattr(self, "_kids_settings_unlock", None)
        if callable(unlock) and not unlock():
            return
        kind, server, user, pw = found
        name = ""
        if kind == "m3u" and "://" not in server:
            name = os.path.splitext(os.path.basename(server))[0]
        dlg = PlaylistDialog(self, prefill={
            "name": name, "kind": kind, "server": server,
            "username": user, "password": pw})
        if not dlg.exec():
            return
        pl = self.playlist_store.add(dlg.values())
        self._update_playlist_btn()
        # The first real provider is simply used; with one already playing
        # the switch is offered, not forced.
        first = type(self.client) in (OfflineClient, DemoClient)
        if first or confirm(self, tr("pl_dropped_title"),
                            tr("pl_dropped_switch", name=pl["name"])):
            self.switch_playlist(pl["id"])

    @staticmethod
    def _dropped_sources(mime) -> list[str]:
        """The local paths and link addresses in a drop. A link dragged out
        of a browser's address bar or page usually comes as a URL; selected
        text holding one as plain text."""
        out = [u.toLocalFile() if u.isLocalFile() else u.toString()
               for u in mime.urls()]
        if not out and mime.hasText():
            out = [t for t in mime.text().split() if "://" in t][:1]
        return out

    def _drop_waits(self) -> bool:
        """"Who's watching?" waits for a profile's PIN at start: nothing
        dropped on the window plays, or adds a source, before it is given."""
        return bool(getattr(self, "_profile_locked", lambda: False)())

    def dragEnterEvent(self, e) -> None:
        if self._drop_waits():
            return
        mime = e.mimeData()
        if (any(u.isLocalFile()
                and u.toLocalFile().lower().endswith(self.MEDIA_EXTS)
                for u in mime.urls())
                or dropped_playlist(self._dropped_sources(mime))):
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        if self._drop_waits():
            return
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if p and p.lower().endswith(self.MEDIA_EXTS):
                self._play_local_path(p)
                e.acceptProposedAction()
                return
        found = dropped_playlist(self._dropped_sources(e.mimeData()))
        if found:
            e.acceptProposedAction()
            # After the drop, not inside it: a dialog opened here holds the
            # file manager the drag came from until it is closed.
            QTimer.singleShot(0, lambda: self._add_dropped_playlist(found))

    # -- music: the play queue -------------------------------------------------

    def queue_add(self, items, play_next: bool = False) -> None:
        """Put tracks in the queue - at the front (play next) or the end."""
        rows = [it for it in (items or [])
                if it and self._is_audio(it.get("_path"))]
        if not rows:
            return
        q = list(getattr(self, "_track_queue", []) or [])
        i = getattr(self, "_track_index", -1)
        if play_next and 0 <= i < len(q):
            q[i + 1:i + 1] = rows
        else:
            q += rows
        self._track_queue = q
        # Hand-built: _start_playback must not silently replace it with
        # whatever listing happens to be on screen.
        self._queue_explicit = True
        self._sync_queue_buttons()
        hub_of(self).post(tr("queue_added", n=len(rows)), ms=4000)

    def queue_clear(self) -> None:
        self._track_queue = []
        self._track_index = -1
        self._queue_explicit = False
        self._sync_queue_buttons()

    def _sync_queue_buttons(self) -> None:
        pl = getattr(self, "player", None)
        if pl is None:
            return
        q = getattr(self, "_track_queue", []) or []
        i = getattr(self, "_track_index", -1)
        try:
            pl.set_next_available(self._local_is_playing()
                                  and bool(q) and i + 1 < len(q))
        except Exception:
            pass

    def _queue_step(self, direction: int) -> bool:
        """Play the next/previous queued track. False when music is not
        what is playing, so the caller falls back to its normal zap.

        The queue is only in charge while a track is actually playing:
        with a channel or a film on, prev/next mean the channel list
        again, and a queue left over from earlier must not hijack them."""
        q = getattr(self, "_track_queue", []) or []
        i = getattr(self, "_track_index", -1)
        if not self._local_is_playing():
            log.info("queue: not stepping - no local file is playing "
                     "(key=%r last=%r)", for_log(getattr(self, "_playing_key", None)),
                     (getattr(self, "_last_playback", None) or {}).get("kind"))
            return False
        if not q or i < 0:
            log.info("queue: nothing to step to (%d tracks, index %d)",
                     len(q), i)
            return False
        step = 1 if direction > 0 else -1
        j = i + step
        # Walk past tracks whose file has gone - an unmounted share, a
        # renamed folder. Stopping dead on the first missing one is the
        # other way an album fell silent halfway through.
        while 0 <= j < len(q):
            if self._play_queued(j):
                return True
            j += step
        log.info("queue: no playable track %s of %d (index %d)",
                 "after" if step > 0 else "before", len(q), i)
        return False

    def _play_queued(self, index: int) -> bool:
        """Play queue entry *index*. False when there is nothing playable
        there, so the caller can move on to the next one."""
        q = getattr(self, "_track_queue", []) or []
        if not (0 <= index < len(q)):
            return False
        it = q[index]
        path = it.get("_path")
        if not path or not os.path.isfile(path):
            log.info("queue: skipping missing track %s", for_log(path))
            return False
        self._track_index = index
        self._start_playback(path, it.get("name") or os.path.basename(path),
                             it.get("stream_icon"), path, "local",
                             record=True, item=it)
        self._sync_queue_buttons()
        return True

    def _place_in_queue(self, url: str, title: str, item=None) -> None:
        """Point the play queue at the track that is starting, so that
        end-of-track knows what comes next.

        Three cases. The track is already queued: just position on it. It
        is not, but the user built this queue by hand: slot it in ahead of
        what they queued and play on into it - discarding a hand-built
        queue loses the one thing they asked for. Otherwise the listing on
        screen becomes the queue, which is what makes a folder play
        through without anyone queueing anything.

        The middle case is the bug this fixes: a hand-built queue used to
        be replaced by the listing, and if that listing held no audio at
        all (browsing TV, say) the queue collapsed to the single track
        being played - so it fell silent the moment that track ended."""
        q = getattr(self, "_track_queue", []) or []
        here = next((n for n, t in enumerate(q)
                     if t.get("_path") == url), -1)
        if here < 0 and q and getattr(self, "_queue_explicit", False):
            row = dict(item) if isinstance(item, dict) else {}
            row["_path"] = url
            row.setdefault("name", title)
            i = getattr(self, "_track_index", -1)
            here = i + 1 if 0 <= i < len(q) else 0
            q = list(q)
            q.insert(here, row)
            self._track_queue = q
        elif here < 0:
            # Same media family only: a folder holding both an album and a
            # film must not roll from the last track into the film.
            same = self._is_audio if self._is_audio(url) else self._is_video
            # A search match plays on through its own folder, as it would
            # from there - not through the other matches (mw_local); a file
            # of a folder still being read, through all of it.
            if isinstance(item, dict) and item.get("_hit"):
                listing = self._local_hit_folder(url, item)
            elif isinstance(item, dict) and item.get("_kind") == "local":
                listing = self._local_listing_rows(url, item)
            else:
                listing = None
            q = [r for r in (listing or self.all_items or [])
                 if not r.get("_header") and same(r.get("_path") or "")]
            here = next((n for n, t in enumerate(q)
                         if t.get("_path") == url), -1)
            if here < 0:
                q, here = [{"_path": url, "name": title}], 0
            self._track_queue = q
            self._queue_explicit = False
        self._track_index = here

    def _local_is_playing(self) -> bool:
        """Is a local file what the player is on right now? The queue is
        only ever in charge then - with a channel or a provider film on,
        prev/next mean the channel list again."""
        if getattr(self, "_playing_key", None) is None:
            return False
        lp = getattr(self, "_last_playback", None) or {}
        return lp.get("kind") == "local"

    def _local_autoadvance_ok(self, url: str) -> bool:
        """Should end-of-file roll on to the next local file?

        Music always does - an album is meant to play through. Video only
        when it is an EPISODE: either the file names one (S01E02 / 1x02)
        or we are inside a local series. Nobody wants an unrelated film to
        start because the one before it happened to end."""
        if self._is_audio(url):
            return True
        if getattr(self, "_local_series", None):
            return True
        from .mw_local import episode_info
        stem = os.path.splitext(os.path.basename(str(url or "")))[0]
        return episode_info(stem) is not None

    def _queue_autoplay(self) -> bool:
        """End of a track/episode: roll on to the next queued one."""
        lp = getattr(self, "_last_playback", None) or {}
        if not self._local_autoadvance_ok(lp.get("url") or ""):
            log.info("queue: not auto-advancing past %r (not an episode)",
                     for_log(lp.get("url")))
            return False
        return self._queue_step(1)

    def open_queue(self) -> None:
        """The queue, with what is playing marked; double-click to jump."""
        from PyQt6.QtWidgets import (
            QDialog, QDialogButtonBox, QListWidget, QListWidgetItem,
            QVBoxLayout,
        )
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("queue_title"))
        dlg.resize(460, 420)
        v = QVBoxLayout(dlg)
        lst = QListWidget()
        q = getattr(self, "_track_queue", []) or []
        cur = getattr(self, "_track_index", -1)
        for n, it in enumerate(q):
            row = QListWidgetItem(
                ("▶  " if n == cur else "     ") + (it.get("name") or ""))
            row.setData(Qt.ItemDataRole.UserRole, n)
            lst.addItem(row)
        v.addWidget(lst, 1)

        def jump(item):
            self._play_queued(item.data(Qt.ItemDataRole.UserRole))
            dlg.accept()

        lst.itemDoubleClicked.connect(jump)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        clear = bb.addButton(tr("queue_clear"),
                             QDialogButtonBox.ButtonRole.ActionRole)
        clear.clicked.connect(lambda: (self.queue_clear(), dlg.accept()))
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        dlg.exec()

    # -- music: visualiser + equaliser -----------------------------------------

    def _eq_settings(self) -> tuple[list[float], bool]:
        raw = self.settings.value("eq_gains", "")
        try:
            gains = [float(x) for x in str(raw).split(",")] if raw else []
        except ValueError:
            gains = []
        gains = (gains + [0.0] * 10)[:10]
        return gains, self.settings.value("eq_on", "false") == "true"

    def _apply_audio_visuals(self, url: str, audio: bool = False) -> None:
        """Music gets a visualiser in place of the black video pane, and
        whatever equaliser the user set.

        VIDEO IS NOT TOUCHED - not the filter graph, not the audio chain,
        nothing. Writing mpv's lavfi-complex here (even to clear it) made
        a clicked TV channel fail to open, so it fell back to an external
        player; the auto-preview, which never came through here, played
        fine and that is what gave the bug away. A stale visualiser graph
        from previous music is cleared inside player.play() instead, which
        only writes when one is actually set.

        A stream with no audio extension that turns out to be sound alone
        gets its visualiser later, once mpv has listed its tracks
        (_on_audio_only) - never here, before anything is known of it."""
        pl = getattr(self, "player", None)
        if pl is None:
            return
        self._vis_tracks = False
        if not audio and not str(url or "").lower().endswith(self.AUDIO_EXTS):
            # Video. The ONLY thing allowed here is taking down a
            # visualiser we ourselves put up for a previous track - and
            # only when there is one. An ordinary channel play must reach
            # mpv untouched.
            if getattr(self, "_vis_active", False):
                self._vis_active = False
                try:
                    pl.set_visualiser(False)
                except Exception as e:
                    log.debug("visualiser teardown failed: %s", e)
            return
        try:
            style = self.settings.value("vis_style", "bars")
            want = self.settings.value("vis_on", "true") == "true"
            pl.set_visualiser(want, style)
            self._vis_active = want
            gains, on = self._eq_settings()
            pl.set_equaliser(gains, on)
        except Exception as e:
            log.debug("audio visuals failed: %s", e)

    def _vis_choose(self, on: bool, style: str) -> None:
        """Remember the visualiser choice and apply it to what is playing:
        music, a radio station, or a stream mpv found to be sound alone."""
        self.settings.setValue("vis_on", "true" if on else "false")
        self.settings.setValue("vis_style", style)
        pl = getattr(self, "player", None)
        lp = getattr(self, "_last_playback", None) or {}
        url = lp.get("url", "")
        if pl is None:
            return
        music = (str(url).lower().endswith(self.AUDIO_EXTS)
                 or lp.get("kind") == "radio")
        tracks = not music and self._playing_audio_only()
        if music or tracks:
            pl.set_visualiser(on, style, playing=True)
            # Whatever is up now is what the next video takes down.
            self._vis_active = on
            self._vis_tracks = tracks and on

    def _playing_audio_only(self) -> bool:
        pl = getattr(self, "player", None)
        try:
            return bool(pl is not None and pl.audio_only())
        except Exception:
            return False

    def _on_audio_only(self, audio_only: bool) -> None:
        """The stream playing turned out to be sound with no picture - an
        iHeart channel in an M3U, a film that is only a soundtrack: the
        visualiser the user chose in place of the black pane, as for music.
        Or it has a picture after all: the visualiser goes again.

        Only ever after mpv has opened the stream and listed its tracks, so
        a stream with video never gets a filter graph written; the next
        stream takes this one down as it does music's (_vis_active)."""
        pl = getattr(self, "player", None)
        if pl is None:
            return
        try:
            if audio_only:
                if getattr(self, "_vis_active", False):
                    return          # music or radio: up before it loaded
                if self.settings.value("vis_on", "true") != "true":
                    return          # turned off: nothing written
                pl.set_visualiser(True, self.settings.value("vis_style", "bars"),
                                  playing=True)
                self._vis_active = True
                self._vis_tracks = True
            elif getattr(self, "_vis_tracks", False):
                self._vis_tracks = False
                if getattr(self, "_vis_active", False):
                    self._vis_active = False
                    pl.set_visualiser(False)
        except Exception as e:
            log.debug("visualiser for a stream without a picture: %s", e)

    def open_equaliser(self) -> None:
        """A ten-band graphic equaliser with the usual presets. Applies live
        while playing and is remembered for the next track."""
        from PyQt6.QtWidgets import (
            QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
            QLabel, QSlider, QVBoxLayout,
        )
        pl = getattr(self, "player", None)
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("eq_title"))
        v = QVBoxLayout(dlg)
        gains, on = self._eq_settings()
        top = QHBoxLayout()
        chk = QCheckBox(tr("eq_enable"))
        chk.setChecked(on)
        top.addWidget(chk)
        top.addStretch(1)
        top.addWidget(QLabel(tr("eq_preset")))
        presets = QComboBox()
        for key in ("flat", "bass", "treble", "vocal", "rock"):
            presets.addItem(tr(f"eq_preset_{key}"), key)
        top.addWidget(presets)
        v.addLayout(top)

        row = QHBoxLayout()
        sliders = []
        bands = (pl.EQ_BANDS if pl is not None
                 else (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000))
        for i, f in enumerate(bands):
            col = QVBoxLayout()
            sl = QSlider(Qt.Orientation.Vertical)
            sl.setRange(-12, 12)
            sl.setValue(int(round(gains[i])))
            sl.setMinimumHeight(120)
            lab = QLabel(f"{f // 1000}k" if f >= 1000 else str(f))
            lab.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            col.addWidget(sl, 1, Qt.AlignmentFlag.AlignHCenter)
            col.addWidget(lab)
            row.addLayout(col)
            sliders.append(sl)
        v.addLayout(row)

        def apply_now():
            vals = [s.value() for s in sliders]
            self.settings.setValue("eq_gains",
                                   ",".join(str(x) for x in vals))
            self.settings.setValue("eq_on",
                                   "true" if chk.isChecked() else "false")
            if pl is not None:
                pl.set_equaliser(vals, chk.isChecked())

        def use_preset():
            key = presets.currentData()
            vals = (pl.EQ_PRESETS if pl is not None
                    else {}).get(key, (0,) * 10)
            for sl, g in zip(sliders, vals, strict=False):
                sl.setValue(int(g))
            apply_now()

        for sl in sliders:
            sl.valueChanged.connect(lambda _v: apply_now())
        chk.toggled.connect(lambda _v: apply_now())
        presets.activated.connect(lambda _i: use_preset())
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        bb.accepted.connect(dlg.accept)
        v.addWidget(bb)
        dlg.exec()
