"""The provider's archive in the window (MainWindow mixin): catch-up and
timeshift. The guide's delay and the replay delay; how far back a channel's
archive reaches (its tv_archive days, a shorter depth learned per channel,
a channel whose archive turned out not to be one remembered for a while);
a channel played from a moment or a programme in the past
(``_play_timeshift``: a portal's archive through mw_portal_archive.py,
else the candidate addresses probed off the window's thread - Nerd's own
catch-up address first - while the live picture plays on, then
``_start_playback``); the timeshift menu and the window of past
programmes. Recording proper is ui/mw_recording.py, whose
``_RecordingMixin`` has this as its base.
"""

from __future__ import annotations

import time
from ..providers.client import b64, epg_times
from ..i18n import tr
from ..core.log import log
from ..core.privacy import for_log, url_for_log
from .notices import hub_of
from ..core.workers import run_async
from .qt_i18n import format_when
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout
from datetime import datetime, timedelta, timezone


class _TimeshiftMixin:
    # -- EPG / replay time offsets ---------------------------------------------------

    def _epg_delay_minutes(self) -> int:
        try:
            base = int(self.settings.value("epg_delay_min", 0))
        except (TypeError, ValueError):
            base = 0
        # Nerd: a source's own guide shift on top (a provider whose guide
        # runs an hour off, say).
        from ..core import nerd
        if nerd.current().on:
            try:
                base += int(float((nerd.source() or {}).get("epg_shift") or 0))
            except (TypeError, ValueError):
                pass
        return base

    def _replay_delay_minutes(self) -> int:
        try:
            return int(self.settings.value("replay_delay_min", 0))
        except (TypeError, ValueError):
            return 0

    def _apply_epg_delay(self, dt):
        if dt is None:
            return None
        mins = self._epg_delay_minutes()
        return dt + timedelta(minutes=mins) if mins else dt

    # -- timeshift / catch-up ------------------------------------------------------

    def _timeshift_days(self, it) -> int:
        try:
            # A channel we've learned doesn't actually serve catch-up (the
            # provider set tv_archive but the archive URL returns HTML/live) is
            # no longer treated as timeshift, so its marker + menu disappear.
            if str(self._item_key(it)) in self._ts_broken_set():
                return 0
            if int(it.get("tv_archive") or 0):
                return int(it.get("tv_archive_duration") or 1) or 1
        except (TypeError, ValueError):
            pass
        return 0

    # How long a learned catch-up failure sticks before the channel is
    # re-tested - so a channel that gains a real archive later lights up again
    # on its own, without waiting for a manual refresh.
    TS_BROKEN_TTL = 14 * 86400   # 14 days

    def _ts_broken_map(self) -> dict:
        """{channel_key: failed_at_epoch} for this playlist. Loaded lazily and
        cached; persisted so it survives sessions."""
        pid = ((self.playlist_store.active() or {}).get("id", "")
               if self.playlist_store else "")
        if getattr(self, "_ts_broken_pid", None) != pid:
            raw = str(self.settings.value(f"ts_broken/{pid}", "") or "")
            m: dict = {}
            for tok in raw.split(","):
                if ":" in tok:
                    k, _, ts = tok.rpartition(":")
                    try:
                        m[k] = float(ts)
                    except ValueError:
                        pass
                elif tok:
                    m[tok] = 0.0   # legacy entry (no timestamp) - never expires
            self._ts_broken = m
            self._ts_broken_pid = pid
        return self._ts_broken

    def _ts_broken_set(self) -> set:
        """Channel keys still considered broken (failure within the TTL)."""
        now = time.time()
        return {k for k, ts in self._ts_broken_map().items()
                if ts == 0.0 or now - ts < self.TS_BROKEN_TTL}

    def _save_ts_broken(self) -> None:
        pid = getattr(self, "_ts_broken_pid", "")
        m = self._ts_broken
        self.settings.setValue(
            f"ts_broken/{pid}",
            ",".join(f"{k}:{int(ts)}" for k, ts in sorted(m.items())))

    def _mark_ts_broken(self, it) -> None:
        if it is None:
            return
        key = str(self._item_key(it))
        m = self._ts_broken_map()
        if key:
            m[key] = time.time()
            self._save_ts_broken()
            # Redraw so the amber ◀◀ marker drops immediately.
            self.listw.viewport().update()
            # If this is the channel on screen, drop the in-player timeshift
            # affordance too: _sync_player_buttons re-reads _timeshift_days
            # (now 0 for this key) and hides the control-bar ◀◀ button, so a
            # channel we just learned has no working archive stops advertising
            # it. The live video is untouched - this only toggles button
            # visibility.
            self._sync_player_buttons()

    # How much a "go back to the limit" request is pulled inside the stated
    # depth, how shallow a failing request must be to count as "no archive at
    # all" (learn-and-hide), and how long a learned real-depth cap sticks.
    TS_MARGIN_MIN = 10               # 10 min inside the deepest requestable point
    # Only a failure this shallow (~the smallest 'go back' step) means the
    # channel serves NO catch-up at all. Anything deeper failing just caps the
    # learned depth - it must never unmark a channel that works closer to live.
    TS_SHALLOW_MIN = 45              # <= 45 min back (the 30-min step + slack)
    TS_DEPTH_TTL = 14 * 86400        # a learned short depth re-expands after 14 d

    def _clear_ts_broken(self) -> None:
        """Forget learned catch-up failures (manual refresh / reset button) so
        the provider's tv_archive flags are trusted again."""
        pid = ((self.playlist_store.active() or {}).get("id", "")
               if self.playlist_store else "")
        self.settings.remove(f"ts_broken/{pid}")
        self.settings.remove(f"ts_depth/{pid}")
        self._ts_broken = {}
        self._ts_broken_pid = pid
        self._ts_depth = {}
        self._ts_depth_pid = pid
        if hasattr(self, "listw"):
            self.listw.viewport().update()

    # -- learned real archive depth ------------------------------------------
    # A provider's tv_archive_duration can overstate what it actually serves,
    # so a request near the stated limit fails. We remember the shallowest depth
    # (minutes back) that failed and only offer/allow less than that - without
    # unmarking the channel as timeshift. Stored with a timestamp so a transient
    # failure re-expands after a while.

    def _ts_depth_map(self) -> dict:
        """{channel_key: (fail_minutes, learned_at)} for this playlist."""
        pid = ((self.playlist_store.active() or {}).get("id", "")
               if self.playlist_store else "")
        if getattr(self, "_ts_depth_pid", None) != pid:
            raw = str(self.settings.value(f"ts_depth/{pid}", "") or "")
            m: dict = {}
            for tok in raw.split(","):
                parts = tok.split(":")
                if len(parts) >= 2 and parts[0]:
                    try:
                        mins = int(float(parts[1]))
                        ts = float(parts[2]) if len(parts) >= 3 else 0.0
                    except ValueError:
                        continue
                    m[parts[0]] = (mins, ts)
            self._ts_depth = m
            self._ts_depth_pid = pid
        return self._ts_depth

    def _save_ts_depth(self) -> None:
        pid = getattr(self, "_ts_depth_pid", "")
        self.settings.setValue(
            f"ts_depth/{pid}",
            ",".join(f"{k}:{v[0]}:{int(v[1])}"
                     for k, v in sorted(self._ts_depth.items())))

    def _learn_ts_maxdepth(self, it, minutes: int) -> None:
        """A catch-up request at *minutes* back failed: the real archive is
        shorter. Record it (only ever tightening) so the menu/requests shrink."""
        key = str(self._item_key(it))
        m = self._ts_depth_map()
        cur = m.get(key)
        if cur is None or minutes < cur[0]:
            m[key] = (int(minutes), time.time())
            self._save_ts_depth()
            if hasattr(self, "listw"):
                self.listw.viewport().update()

    def _confirm_ts_depth(self, it, minutes: int) -> None:
        """A catch-up request at *minutes* back worked: if a stale (shorter) cap
        was learned, the archive actually reaches this deep, so drop it."""
        key = str(self._item_key(it))
        m = self._ts_depth_map()
        cur = m.get(key)
        if cur is not None and minutes >= cur[0]:
            del m[key]
            self._save_ts_depth()

    def _effective_ts_minutes(self, it) -> int:
        """The deepest catch-up we'll actually request (minutes back): the
        provider's stated depth, capped by any learned-shorter real depth, and
        always pulled a margin inside so the very oldest edge isn't requested."""
        days = self._timeshift_days(it)
        if not days:
            return 0
        full = days * 1440
        cur = self._ts_depth_map().get(str(self._item_key(it)))
        if cur and (cur[1] == 0.0 or time.time() - cur[1] < self.TS_DEPTH_TTL):
            full = min(full, cur[0])
        return max(self.TS_MARGIN_MIN, full - self.TS_MARGIN_MIN)

    @staticmethod
    def _ts_provider_flagged(it) -> bool:
        """Whether the provider marks this channel as having catch-up
        (tv_archive), independent of any learned-broken state - i.e. a
        'timeshift channel' from the user's point of view."""
        try:
            return bool(it) and int(it.get("tv_archive") or 0) > 0
        except (TypeError, ValueError):
            return False

    def _reset_channel_timeshift(self, it) -> None:
        """Forget a single channel's learned catch-up failure and short-depth
        cap, so its ◀◀ marker, full menu and archive depth come back. Used from
        the live channel's context menu."""
        key = str(self._item_key(it))
        m = self._ts_broken_map()
        if key in m:
            del m[key]
            self._save_ts_broken()
        dm = self._ts_depth_map()
        if key in dm:
            del dm[key]
            self._save_ts_depth()
        if hasattr(self, "listw"):
            self.listw.viewport().update()
        self._flash_status(tr("ts_reset_done_one"))

    def _play_timeshift(self, it, back_min=None, prog=None,
                        prog_origin=None, _depth_retry=False) -> None:
        # prog_origin: the picked programme's *original* start (the seek-bar's
        # 0-point). Set when re-loading the archive at a scrubbed position so
        # the bar keeps spanning the whole programme with the playhead offset,
        # instead of restarting the bar at each seek. Defaults to the segment
        # start for a fresh programme pick.
        sid = it.get("stream_id")
        days = self._timeshift_days(it)
        if sid is None or not days:
            return
        now = time.time()
        if prog:
            start = prog["start_timestamp"]
            what = prog.get("title") or tr("ts_programme")
        else:
            start = now - (back_min or 30) * 60
            what = None
        # Clamp to the effective archive depth: the provider's stated depth,
        # capped by any learned-shorter real depth, and a margin inside the
        # oldest edge (providers drop the last few minutes, so a request at the
        # exact stated limit lands just past the end and fails).
        floor = now - self._effective_ts_minutes(it) * 60
        # A failure on a request *near the limit* only means that depth isn't
        # available - it must NOT unmark a working channel. Only a shallow,
        # un-clamped request failing implies no catch-up at all (learn-and-hide).
        clamped = start < floor - 1
        start = max(start, floor)
        requested_back_min = (now - start) / 60.0
        if prog:
            duration_min = max(
                1, int((prog["stop_timestamp"] - start) // 60) + 2)
        else:
            duration_min = max(1, int((now - start) // 60) + 1)
        # Only a shallow "go back N" that fails means the channel serves no
        # catch-up at all. A programme pick / scrub is never allowed to unmark
        # a channel (a picked-programme URL can fail on a channel whose plain
        # archive works fine), and a retry never unmarks either.
        allow_mark_broken = (not _depth_retry and prog is None
                             and not clamped
                             and requested_back_min <= self.TS_SHALLOW_MIN)
        start += self._replay_delay_minutes() * 60
        if getattr(self.client, "archive_by_programme", False):
            # A portal (StalkerClient) keeps its archive by programme, and
            # asking for a link takes the network (mw_portal_archive).
            self._play_portal_archive(it, start, prog, prog_origin)
            return
        # Candidate archive-URL formats (providers differ). Play the first;
        # _playback_error walks to the next on an early failure so we auto-pick
        # whichever scheme this provider actually serves.
        urls = self.client.timeshift_urls(
            sid, datetime.fromtimestamp(start), duration_min)
        # Nerd: the source's own catch-up address goes first; the app's
        # own still follow it, so a template that does not answer with a
        # stream costs nothing but the try.
        own = self._nerd_catchup_url(it, start, start + duration_min * 60)
        if own:
            urls = [own] + [u for u in (urls or []) if u != own]
        if not urls:
            return
        log.debug("[ts] probe back_min=%s prog=%s days=%s start=%s "
                  "candidates=%s", back_min, bool(prog), days,
                  for_log(datetime.fromtimestamp(start)),
                  [url_for_log(u) for u in urls])
        name = self.channel_display_name(it)
        title = (tr("ts_title_programme", what=what, name=name) if what
                 else tr("ts_title_channel", name=name))
        key = self._item_key(it)
        # Probe the candidate URLs over HTTP in the background - the live video
        # keeps playing untouched. Only when a candidate is confirmed to serve
        # real video do we switch the player to it (one clean swap); if none do,
        # the channel is marked as having no working catch-up and the user just
        # gets a status message, with no stutter.
        # The check is work the window does: a job of its own (a newer
        # check replaces it), ended with its answer.
        hub_of(self).start(tr("ts_checking"), key="ts_check", kind="guide", mine=True,
                           timeout_ms=60_000)
        probe_token = getattr(self, "_ts_probe_token", 0) + 1
        self._ts_probe_token = probe_token

        def done(res):
            if probe_token != getattr(self, "_ts_probe_token", 0):
                return   # a newer play/probe superseded this one
            hub_of(self).end_key("ts_check")
            url, proven_dead = (res if isinstance(res, tuple)
                                else (res, True))
            if not url:
                if not proven_dead:
                    # Every candidate failed at the NETWORK level (timeout,
                    # DNS, TLS, refused) - the provider never answered, so
                    # this proves nothing about the channel's archive. Say so
                    # and leave the learned marks/depths untouched; the next
                    # attempt starts clean.
                    self._flash_status(tr("ts_check_failed"), ms=6000)
                elif allow_mark_broken:
                    # A shallow request failed: the provider serves no catch-up.
                    self._mark_ts_broken(it)
                else:
                    # A deep request failed: the archive is shorter than stated.
                    # Learn the real limit (keeping the channel as timeshift),
                    # then retry one step shallower so a single click still lands
                    # at the deepest point that works - instead of dumping the
                    # user to live with no seek bar.
                    self._learn_ts_maxdepth(it, int(round(requested_back_min)))
                    if not prog:
                        nxt = self._next_shallower_step(
                            int(round(requested_back_min)))
                        if nxt is not None:
                            self._flash_status(tr("ts_shorter_archive"))
                            self._play_timeshift(it, back_min=nxt,
                                                 _depth_retry=True)
                            return
                        # Walked the retry chain all the way down to the
                        # shallowest step (30 min) and even that serves no
                        # archive: the provider advertises catch-up it doesn't
                        # actually have. Hide it, same as a shallow failure -
                        # 30 min back is well within any real archive, so this
                        # can't wrongly hide a channel that only has a shallow
                        # window (that path returns a URL and never gets here).
                        self._mark_ts_broken(it)
                if proven_dead:
                    self._flash_status(tr("ts_archive_unavailable"), ms=6000)
                # The live video never stopped. Restore the live timeline (not
                # plain live) so the user can still scrub to a shallower point
                # without restarting the channel.
                if self.player:
                    self._playing_catchup = False
                    if self._timeshift_days(it):
                        self._apply_seek_mode(it, "live")
                    else:
                        self.player.set_seek_mode("live")
                        self.player.set_live_badge(None)
                return
            # Archive reaches this depth after all - drop any stale short cap.
            self._confirm_ts_depth(it, int(round(requested_back_min)))
            self._ts_candidates = [url]
            self._ts_candidate_idx = 0
            self._ts_candidate_started = time.monotonic()
            self._ts_segment_start = start
            self._ts_catchup_program = bool(prog)
            self._ts_from_portal = False
            # Remember the programme's span so the seek bar can be clamped to
            # just this programme (the provider's archive URL runs on to the
            # live edge). _ts_program_start is the bar's 0-point; it stays fixed
            # across scrub-reloads (prog_origin) so the playhead moves inside the
            # same bar instead of the bar restarting each seek.
            if prog:
                self._ts_program_start = (
                    prog_origin if prog_origin is not None
                    else prog.get("start_timestamp"))
                self._ts_program_stop = prog.get("stop_timestamp")
                self._ts_program_title = prog.get("title")
            else:
                self._ts_program_start = None
                self._ts_program_stop = None
                self._ts_program_title = None
            self._start_playback(url, title, it.get("stream_icon"), key,
                                 "live", record=False, item=it, catchup=True)

        run_async(self.pool,
                  lambda u=list(urls): self._probe_ts_candidates(u),
                  # A crashed probe proves nothing either - never mark on it.
                  done, lambda _e: done((None, False)))

    def _nerd_catchup_url(self, it, start: float, end: float) -> str | None:
        """Nerd: the active source's own catch-up address (Add/Edit source >
        Nerd options) for a programme from *start* to *end* (epoch seconds) -
        None, and nothing changes, while Nerd is off or the source asks for
        nothing of its own."""
        from ..core import catchup, nerd
        src = nerd.source()
        if not catchup.wanted(src):
            return None
        try:
            fmt = str(self.settings.value("stream_format", "ts") or "ts")
            live = self.client.live_url(it.get("stream_id"), fmt) or ""
            begin, finish = (datetime.fromtimestamp(start, timezone.utc),
                             datetime.fromtimestamp(end, timezone.utc))
            cid, channel = str(it.get("catchup_id") or ""), str(it.get("catchup_source") or "")
            now = datetime.now(timezone.utc)
            url = catchup.for_programme(src, live, begin, finish, catchup_id=cid,
                                        channel_source=channel, now=now)
            template, mode = catchup.settings_of(src)
            if url and channel and not template and url == catchup.archive_url(
                    live, begin, finish, template, mode, now, cid, channel):
                # The channel's own catch-up address, no template or rule of
                # the person's in it: the source's word, judged as its stream
                # addresses are (core/stream_address.py).
                judge = getattr(self.client, "played_from_source", None)
                if callable(judge):
                    url = judge(url) or None
        except Exception as e:
            log.info("[ts] the source's catch-up address could not be made: %s", e)
            return None
        if url:
            log.debug("[ts] the source's own catch-up address first: %s", url_for_log(url))
        return url

    def _probe_ts_candidates(self, urls) -> tuple:
        """(worker thread) Return (url, proven_dead): the first candidate URL
        that actually serves video, or (None, flag). Filters out the HTML
        error pages / redirects a provider hands back for channels it doesn't
        really archive - so we never churn the player through dead URLs.

        proven_dead only goes True when the provider RESPONDED with something
        that is not a stream (HTML/JSON error page, wrong content). A timeout,
        DNS/TLS or connection error says nothing about whether the channel has
        an archive, so those must never count as proof - a flaky link once
        marked working channels as broken for 14 days, silently."""
        sess = getattr(self.client, "session", None)
        proven_dead = False
        network_failed = False
        for u in urls:
            try:
                # Generous read timeout: providers assemble archive segments
                # on demand and the first byte can take a long while cold -
                # the probe runs in the background while live keeps playing,
                # so waiting costs nothing visible.
                r = sess.get(u, stream=True, timeout=(5, 25),
                             headers={"Range": "bytes=0-8191"})
                ctype = (r.headers.get("Content-Type") or "").lower()
                chunk = next(r.iter_content(4096), b"") or b""
                r.close()
            except Exception as e:
                log.debug("[ts] probe network error for %s: %s",
                          url_for_log(u), e)
                network_failed = True
                continue
            head = chunk.lstrip()
            if "text/html" in ctype or head[:1] in (b"<", b"{"):
                proven_dead = True   # a real answer: this URL is not a stream
                continue
            if (chunk[:1] == b"\x47"                 # MPEG-TS sync byte
                    or head[:7] == b"#EXTM3U"        # HLS playlist
                    or "mpegurl" in ctype
                    or "video" in ctype
                    or "octet-stream" in ctype):
                return u, False
            proven_dead = True       # responded, but with something unplayable
        # Dead is only proven when EVERY candidate got a real answer. If any
        # format failed at the network level, the working scheme may simply
        # have been the one that timed out - that must not hide the channel.
        return None, proven_dead and not network_failed

    # (minutes back, duration i18n key) - the label is "Go back <duration>".
    TIMESHIFT_STEPS = (
        (30, "dur_30min"), (60, "dur_1h"), (120, "dur_2h"), (360, "dur_6h"),
        (720, "dur_12h"), (1440, "dur_1d"), (2880, "dur_2d"),
        (4320, "dur_3d"), (7200, "dur_5d"), (10080, "dur_7d"),
    )

    def _next_shallower_step(self, minutes: int):
        """The largest 'go back' step strictly shallower than *minutes* (used to
        fall back one level when a deeper request turns out to exceed the real
        archive). None when there's nothing shallower."""
        lower = [s for s, _ in self.TIMESHIFT_STEPS if s < minutes]
        return max(lower) if lower else None

    def _build_timeshift_menu(self, ts_menu, it) -> None:
        days = self._timeshift_days(it)
        eff_min = self._effective_ts_minutes(it)
        ts_menu.addAction(tr("ts_go_live"),
                          lambda: self.play_live_channel(it))
        ts_menu.addSeparator()
        prog = self.xmltv.current_programme(it)
        if prog:
            ts_menu.addAction(
                tr("ts_watch_from_start_named", title=prog['title']),
                lambda: self._play_timeshift(it, prog=prog))
        ts_menu.addAction(tr("ts_browse_past"),
                          lambda: self._open_catchup_dialog(it))
        ts_menu.addSeparator()
        # Only offer depths the archive can actually reach (stated depth, capped
        # by any learned-shorter real depth), so "go back 3 days" never appears
        # for an archive that only really serves ~1 day.
        for mins, dur_key in self.TIMESHIFT_STEPS:
            if mins > eff_min:
                break
            ts_menu.addAction(
                tr("ts_go_back", t=tr(dur_key)),
                lambda mins=mins: self._play_timeshift(it, back_min=mins))
        note = ts_menu.addAction(tr("ts_archive_depth", n=days))
        note.setEnabled(False)

    def _open_catchup_dialog(self, it, on_pick=None) -> None:
        """Pick a past programme. *on_pick* decides what happens to it - the
        player by default, the Chromecast when a cast is what is running."""
        days = self._timeshift_days(it)
        if not days:
            return
        d = QDialog(self)
        d.setWindowTitle(
            tr("ts_catchup_title", name=self.channel_display_name(it)))
        d.setMinimumSize(480, 500)
        lay = QVBoxLayout(d)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(10)
        info = QLabel(tr("ts_loading_past"))
        info.setWordWrap(True)
        lay.addWidget(info)
        lst = QListWidget()
        lay.addWidget(lst, 1)
        btns = QHBoxLayout()
        watch_btn = QPushButton(tr("common_watch"), objectName="Primary")
        close_btn = QPushButton(tr("common_close"))
        btns.addStretch()
        btns.addWidget(watch_btn)
        btns.addWidget(close_btn)
        lay.addLayout(btns)
        close_btn.clicked.connect(d.reject)

        def watch(_item=None):
            cur = lst.currentItem()
            p = cur.data(Qt.ItemDataRole.UserRole) if cur else None
            if p:
                (on_pick or (lambda prog: self._play_timeshift(it,
                                                               prog=prog)))(p)
                d.accept()

        watch_btn.clicked.connect(watch)
        lst.itemDoubleClicked.connect(watch)

        def fetch():
            progs = self.xmltv.past_programmes(it, days)
            if progs or it.get("stream_id") is None:
                return progs
            now = time.time()
            out = []
            # A portal lists its archive by day (StalkerClient.archive_table);
            # elsewhere the provider's own table is all there is.
            table = getattr(self.client, "archive_table", None)
            rows = (table(it["stream_id"], days) if table is not None
                    else self.client.epg_table(it["stream_id"]))
            for e in rows:
                start, stop = epg_times(e)
                start, stop = (self._apply_epg_delay(start),
                              self._apply_epg_delay(stop))
                if not start or not stop:
                    continue
                start_ts, stop_ts = start.timestamp(), stop.timestamp()
                if stop_ts <= now and start_ts >= now - days * 86400:
                    out.append({
                        "title": b64(e.get("title")) or "?",
                        "start_timestamp": int(start_ts),
                        "stop_timestamp": int(stop_ts),
                    })
            out.sort(key=lambda p: p["start_timestamp"], reverse=True)
            return out

        def done(progs):
            if not progs:
                info.setText(tr("ts_no_past_programmes"))
                return
            info.setText(tr("ts_past_programmes", n=len(progs), days=days))
            last_day = None
            for p in progs:
                start = datetime.fromtimestamp(p["start_timestamp"])
                stop = datetime.fromtimestamp(p["stop_timestamp"])
                day = format_when(start, "dddd d MMMM")
                if day != last_day:
                    last_day = day
                    head = QListWidgetItem(f"—  {day}  —")
                    head.setFlags(Qt.ItemFlag.NoItemFlags)
                    lst.addItem(head)
                row = QListWidgetItem(
                    f"{start.strftime('%H:%M')}–"
                    f"{stop.strftime('%H:%M')}   "
                    f"{p.get('title') or '?'}")
                row.setData(Qt.ItemDataRole.UserRole, p)
                lst.addItem(row)

        run_async(self.pool, fetch, done,
                  lambda e: info.setText(tr("ts_guide_failed", error=e)))
        d.exec()
