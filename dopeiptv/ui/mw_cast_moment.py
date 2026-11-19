"""Going back in time while a live channel is cast (MainWindow mixin): the
moment the TV shows taken from the player's rewind buffer while it holds
it, else from the provider's archive. The buffer kept beside the cast only
with Settings > Casting's switch on (``_cast_keep_buffer``: one more
connection), the pieces read from the buffer or the archive into one
stream (``_buffer_feed``, ``_archive_feed``, ui's side of
providers/cast_feed.py); the strip's timeshift menu and its steps back
(``_cast_timeshift_menu``, ``_cast_timeshift_entries``,
``_cast_back_steps``), a moment cast (``_cast_to_moment``,
``_cast_from_moment``, ``_cast_from_archive``), back to live
(``_cast_go_live``) and an archive cast carried on past its playlist's end
(``_cast_continue_archive``). The rest of casting is ui/mw_cast.py, whose
``_CastMixin`` has this as its base.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

from PyQt6.QtWidgets import QMenu

from ..core.log import log
from ..core.privacy import for_log
from ..i18n import tr
from ..providers import cast_feed


class _CastMomentMixin:

    # How far behind live the player has to be for a cast to come from the
    # rewind buffer rather than from the provider's own stream - and how
    # close to live a moment picked on the strip is live.
    CAST_BUFFER_BEHIND_S = 10.0
    # The strip's steps back that are shorter than the archive's
    # (TIMESHIFT_STEPS), in seconds: what a rewind buffer of minutes holds.
    CAST_BUFFER_STEPS = (10, 30, 60, 300, 900)

    def _playing_buffer(self, it):
        """The player's rewind buffer when *it* is the channel it plays from
        it, streaming; else None."""
        p = getattr(self, "player", None)
        session = getattr(p, "_dvr", None)
        if (session is None or not getattr(session, "streaming", False)
                or self._playing_key is None
                or self._item_key(it) != self._playing_key):
            return None
        return session

    def _cast_buffer_feed(self, it) -> "cast_feed.Feed | None":
        """The rewind buffer from where the player is, as a cast's pieces -
        when *it* is the channel playing from the buffer, behind live. The
        session is held streaming until the feed is closed."""
        session = self._playing_buffer(it)
        wall_now = getattr(getattr(self, "player", None), "_dvr_wall_now", None)
        if session is None or not callable(wall_now):
            return None
        try:
            wall = wall_now()
        except Exception:
            wall = None
        if wall is None or time.time() - wall < self.CAST_BUFFER_BEHIND_S:
            return None
        return self._buffer_feed(session, wall)

    def _cast_hold_name(self, what: str, session) -> str:
        """A name of its own for each hold on a buffer: two with one name
        would be let go together."""
        n = self._cast_hold_n = getattr(self, "_cast_hold_n", 0) + 1
        return f"{what}:{id(session)}:{n}"

    def _cast_keeps_buffer(self) -> bool:
        """Settings > Casting: keep the rewind buffer while casting - off
        unless asked for, as it is a second connection to the provider."""
        settings = getattr(self, "settings", None)
        try:
            return settings is not None and str(
                settings.value("cast_keep_buffer", "false")) == "true"
        except Exception:
            return False

    def _cast_keep_buffer(self, it, feed=None, url: str = "",
                          source_id: str = "") -> tuple:
        """(session, hold): the rewind buffer of *it* - the channel the player
        plays from its buffer, or the one *feed* reads - held streaming for
        a live cast of *url* (from source *source_id*), so the strip can go
        back in it while the TV plays live. Only with Settings > Casting's
        switch on: it is one more connection to the provider next to the
        cast's, and over a full or unknown limit the person is asked first.
        (None, None) when not kept - it freezes as the player stops (a feed
        holds it only while it reads it), so no second connection is ever
        opened by itself."""
        session = feed.source if feed is not None else self._playing_buffer(it)
        if (session is None or not getattr(session, "streaming", False)
                or not self._cast_keeps_buffer()):
            return None, None
        ask = getattr(self, "_conn_ok_cast_buffer", None)
        if not callable(ask) or not ask(url, source_id=source_id):
            log.info("cast: the rewind buffer is not kept beside the cast")
            return None, None
        who = self._cast_hold_name("cast-live", session)
        try:
            session.hold(who)
        except Exception as e:
            log.debug("cast: the rewind buffer could not be held: %s", e)
            return None, None
        log.info("cast: the rewind buffer goes on recording beside the cast")
        return session, who

    def _cast_release_buffer(self, ctx: dict | None = None) -> None:
        """The rewind buffer kept for a live cast let go: it freezes, unless
        the player, a recording or a feed still reads it."""
        if ctx is None:
            ctx = getattr(self, "_cast_ctx", None) or {}
        session, who = ctx.pop("buffer", None), ctx.pop("buffer_hold", None)
        if session is None or not who:
            return
        log.info("cast: the rewind buffer kept for the cast is let go")
        try:
            session.let_go(who)
        except Exception as e:
            log.debug("cast: letting the rewind buffer go: %s", e)

    def _cast_buffer(self):
        """The rewind buffer of the channel on the TV, while it streams and
        the receiver is fed through a converter here: the session a running
        feed reads, else the one kept for the cast. None for a DLNA TV or a
        Kodi - its address is this computer's loopback."""
        device = getattr(self, "_cast_device", None)
        if (not device or not self._cast_takes_feed(device)
                or not self._cast_converts()):
            return None
        feed = self._cast_feed()
        session = getattr(feed, "source", None) if feed is not None else None
        if session is None:
            session = (getattr(self, "_cast_ctx", None) or {}).get("buffer")
        if (session is None or getattr(session, "closed", True)
                or not getattr(session, "streaming", False)):
            return None
        return session

    def _cast_converts(self) -> bool:
        """The running cast's receiver has a converter here (ffmpeg found),
        the only way the rewind buffer's pieces reach a TV."""
        bridge = getattr(getattr(self, "cast", None), "bridge", None)
        available = getattr(bridge, "available", None)
        try:
            return not callable(available) or bool(available())
        except Exception:
            return False

    def _cast_buffer_span(self) -> tuple[float, float] | None:
        """What the cast's rewind buffer holds, (oldest, newest) wall-clock
        seconds - or None when there is none to go back in."""
        session = self._cast_buffer()
        if session is None:
            return None
        try:
            span = session.span()
        except Exception:
            return None
        if not span or span[1] - span[0] < 1.0:
            return None
        return float(span[0]), float(span[1])

    def _cast_can_go_back(self) -> bool:
        """The live channel on the TV can be wound back: it has an archive,
        or its rewind buffer holds some of it. Never on an Apple TV: each
        step back there was a new stream the Apple TV started over, slow and
        out of step with its sound - it plays a channel live (and pauses it
        itself), films and episodes as before."""
        if self._cast_on_airplay():
            return False
        ctx = getattr(self, "_cast_ctx", None) or {}
        if ctx.get("archive"):
            return True
        return ctx.get("sid") is not None and self._cast_buffer_span() is not None

    def _buffer_feed(self, session, wall: float) -> "cast_feed.Feed | None":
        who = self._cast_hold_name("cast", session)
        last: list[int | None] = [None]

        def url_at(moment: float):
            if session.closed or not session.streaming:
                raise EOFError("the rewind buffer was let go")
            # After a piece, the next RUN - the segment holding *moment*
            # is the one just read (a run ends at a reconnect).
            seg = (session.segment_at(moment) if last[0] is None
                   else session.next_run_after(moment, last[0]))
            if seg is None:
                return None
            last[0] = seg.seq
            return session.url_for(seg), 0.0, float(seg.t0)

        session.hold(who)
        try:
            feed = cast_feed.Feed(url_at, wall, lag=0.0, kind="rewind buffer",
                                  on_close=lambda: session.let_go(who))
        except Exception as e:
            log.info("cast: the rewind buffer cannot feed the cast (%s)", e)
            session.let_go(who)
            return None
        if feed.first is None:
            feed.close()
            return None
        feed.source = session
        log.info("cast: from the rewind buffer, %.0f s behind live",
                 time.time() - feed.first_wall)
        return feed

    def _archive_feed(self, sid, when: datetime) -> "cast_feed.Feed | None":
        """The provider's archive from *when* on, as a cast's pieces: each
        stretch ends where the panel had written to, the next is asked
        from there (ARCHIVE_LAG behind now at the least)."""
        lag = self.ARCHIVE_LAG

        def url_at(moment: float):
            at = min(datetime.fromtimestamp(moment), datetime.now() - lag)
            minute = at.replace(second=0, microsecond=0)
            behind = max(1, int((datetime.now() - minute).total_seconds()
                                // 60))
            cands = self.client.timeshift_urls(sid, minute, behind + 240) or []
            # The transport stream, not the panel's HLS wrapper of the same
            # archive (see _cast_from_archive).
            url = next((c for c in cands if ".ts" in c), "") or \
                next(iter(cands), "")
            if not url:
                return None
            return url, (at - minute).total_seconds(), at.timestamp()

        try:
            feed = cast_feed.Feed(url_at, when.timestamp(),
                                  lag=lag.total_seconds(), kind="archive")
        except Exception as e:
            log.info("cast: no archive address (%s)", e)
            return None
        return feed if feed.first is not None else None

    def _cast_drop_feed(self, ending: bool = False) -> None:
        """The cast's context is done with its pieces. One a converter
        reads is the bridge's to close when the next stream replaces it
        (the TV plays on meanwhile); one never read, or a cast that is over
        (*ending*), is closed here - the reader stops, the rewind buffer is
        let go."""
        ctx = getattr(self, "_cast_ctx", None) or {}
        feed = ctx.pop("feed", None)
        if feed is not None and (ending or not feed.running()):
            threading.Thread(target=feed.close, daemon=True).start()

    def _cast_takes_feed(self, device) -> bool:
        takes = getattr(self.cast, "takes_feed", None)
        return bool(callable(takes) and takes(device))

    def _cast_feed(self):
        feed = (getattr(self, "_cast_ctx", None) or {}).get("feed")
        return feed if feed is not None and not feed.closed else None

    def _cast_written(self) -> float:
        """Seconds of the running feed's playlist - how far the TV can be
        moved within it without a new stream."""
        fn = getattr(getattr(self.cast, "bridge", None), "written", None)
        try:
            return float(fn()) if callable(fn) else 0.0
        except Exception:
            return 0.0

    def _cast_timeshift_menu(self) -> None:
        """The archive and the rewind buffer, for what is playing on the TV.

        The player's own timeshift menu, pointed at the receiver instead of at
        mpv: go back to live, start the programme that is on from its
        beginning, browse what has been, or simply wind back a while. The
        archive does not care who is watching it, and the buffer is read
        here and fed to the TV.
        """
        entries = self._cast_timeshift_entries()
        if not entries:
            return
        m = QMenu(self)
        for entry in entries:
            if entry is None:
                m.addSeparator()
                continue
            text, act = entry
            if act is None:
                m.addAction(text).setEnabled(False)
            else:
                m.addAction(text, act)
        m.exec(self.cast_bar_ts.mapToGlobal(
            self.cast_bar_ts.rect().bottomLeft()))

    def _cast_timeshift_entries(self) -> list:
        """The timeshift menu's entries: (text, what it does) - None for a
        separator, and no action for a line that only says something.

        Go back to live; with an archive, the programme on from its start
        and the past programmes; then the steps back from where the picture
        is, as far as the buffer or the archive reaches."""
        ctx = self._cast_ctx or {}
        it = ctx.get("item")
        if not it or not self._cast_can_go_back():
            return []
        archive = bool(ctx.get("archive"))
        out: list = [(tr("ts_go_live"), self._cast_go_live), None]
        # Where the picture is now, so a step back is a step back from THERE
        # and not from live - winding back twice has to go twice as far.
        at = self._cast_moment()
        if archive:
            prog = self.xmltv.current_programme(it)
            if prog:
                out.append((
                    tr("ts_watch_from_start_named", title=prog["title"]),
                    lambda: self._cast_to_moment(
                        datetime.fromtimestamp(prog["start_timestamp"]))))
            out.append((tr("ts_browse_past"),
                        lambda: self._open_catchup_dialog(
                            it, on_pick=lambda p: self._cast_to_moment(
                                datetime.fromtimestamp(p["start_timestamp"])))))
            out.append(None)
        for secs, label in self._cast_back_steps(at):
            out.append((tr("ts_go_back", t=label),
                        lambda secs=secs: self._cast_to_moment(
                            at - timedelta(seconds=secs))))
        if archive:
            out.append((tr("ts_archive_depth", n=self._timeshift_days(it)),
                        None))
        return out

    def _cast_back_steps(self, at: datetime) -> list[tuple[int, str]]:
        """The steps back from *at* the cast can go, (seconds, how long it
        says): the buffer's short ones and the archive's, each only where the
        moment it lands on is held - in the rewind buffer, or in the archive
        (its depth, and never the minutes it is still writing)."""
        ctx = self._cast_ctx or {}
        span = self._cast_buffer_span()
        archive = bool(ctx.get("archive"))
        eff_min = (self._effective_ts_minutes(ctx.get("item") or {})
                   if archive else 0)
        newest = datetime.now() - self.ARCHIVE_LAG
        steps = [(s, tr("health_secs", s=s) if s < 60
                  else tr("opt_minutes", n=s // 60))
                 for s in self.CAST_BUFFER_STEPS]
        steps += [(mins * 60, tr(key)) for mins, key in self.TIMESHIFT_STEPS]
        out = []
        for secs, label in steps:
            to = at - timedelta(seconds=secs)
            in_buffer = span is not None and to.timestamp() >= span[0]
            in_archive = archive and secs <= eff_min * 60 and to <= newest
            if in_buffer or in_archive:
                out.append((secs, label))
        return out

    def _cast_moment(self):
        """The broadcast moment the TV is showing.

        Not the clock. Every pause leaves the picture that much further
        behind the broadcast and it never catches up again - the recording
        simply carries on from where it was held - so the strip said LIVE
        while showing something ten minutes old.
        """
        began = (self._cast_ctx or {}).get("archive_from")
        if began is not None:
            return began + timedelta(seconds=float(self.cast.position() or 0.0))
        behind = getattr(self, "_cast_behind", 0.0)
        if getattr(self, "_cast_paused_at", None) is not None:
            # Still held: the gap is growing as we speak.
            behind += (datetime.now() - self._cast_paused_at).total_seconds()
        return datetime.now() - timedelta(seconds=behind)

    def _cast_to_moment(self, when) -> None:
        """Point the cast at *when*: out of the rewind buffer while it holds
        that moment, else within what the archive actually holds. Not on an
        Apple TV (_cast_can_go_back) - the phone remote and the media keys
        come through here too."""
        if self._cast_on_airplay():
            log.info("cast: an Apple TV plays the channel live - not going back")
            return
        ctx = self._cast_ctx or {}
        it = ctx.get("item") or {}
        now = datetime.now()
        span = self._cast_buffer_span()
        if span is not None and when.timestamp() >= span[0]:
            if (now - when).total_seconds() < self.CAST_BUFFER_BEHIND_S:
                # Next to the live edge is live.
                if ctx.get("archive_from") is not None:
                    self._cast_go_live()
                return
        elif ctx.get("archive"):
            floor = now - timedelta(minutes=self._effective_ts_minutes(it))
            # A minute inside the live edge: the archive is written as it
            # goes and the newest minute is not there yet.
            when = max(floor, min(when, now - timedelta(minutes=1)))
        elif span is not None:
            when = datetime.fromtimestamp(span[0])  # as far as the buffer goes
        else:
            return
        self._cast_paused_at = None
        self.cast_bar_pause.set_glyph("pause")
        feed = self._cast_feed()
        if feed is not None and self.cast.bridged():
            # Within what the playlist holds: the TV moves there itself.
            at = when.timestamp() - feed.first_wall
            if 0 <= at <= self._cast_written() - 3:
                log.info("cast: moving within the playlist to %s",
                         when.strftime("%H:%M:%S"))
                threading.Thread(target=self.cast.seek, args=(at,),
                                 daemon=True).start()
                return
        self._cast_from_moment(when)

    def _cast_go_live(self, settle: bool = False) -> None:
        """Back to the live edge, off the archive. *settle*: whatever feeds
        the TV now lets go of the provider first, and the provider is given
        a moment, before the channel is asked for again (a stuck cast sent
        again, mw_cast_watch)."""
        ctx, device = self._cast_ctx or {}, self._cast_device
        url = ctx.get("row_url")
        if not device or not url:
            return
        log.info("cast: back to the live edge")
        self._cast_moving_to(ctx, "live")
        # The rewind buffer was all the cast had of the provider (not kept
        # beside it): its feed ends first, and the provider is given a
        # moment, before the TV asks for the channel - never two at once.
        feed = self._cast_feed()
        settle = settle or bool(feed is not None and feed.source is not None
                                and ctx.get("buffer") is None and self.cast.bridged())
        self._cast_drop_feed()
        ctx.update(url=url, source=ctx.get("row_source") or url,
                   archive_from=None)
        self._cast_paused_at = None
        self._cast_behind = 0.0
        self.cast_bar_pause.set_glyph("pause")
        title = ctx.get("title") or "dopeIPTV"
        self._cast_send(
            lambda: self.cast.cast(device, url, title, self._local_codecs(),
                                   settle=settle,
                                   source=ctx.get("row_source") or url,
                                   quality=self._cast_quality(),
                                   height=ctx.get("height") or 0,
                                   fps=ctx.get("fps") or 0.0,
                                   # Still recorded, so it can still be
                                   # paused. Going back to live used to hand
                                   # the channel straight to the receiver,
                                   # and the pause button quietly vanished.
                                   dvr=bool(ctx.get("archive"))),
            lambda _n: self.show_cast_strip(device, title),
            self._cast_send_failed)

    def _cast_moving_to(self, ctx: dict, to) -> None:
        """A seek on its way to the TV, said on the card (mw_cast_watch)."""
        say = getattr(self, "_cast_moving", None)
        if callable(say):
            say(ctx, to)

    def _cast_continue_archive(self) -> None:
        """Keep a timeshifted cast going past the end of its playlist.

        The archive is served as a finite playlist - it stops where it caught
        up with now, and the receiver reports the end of the media. In the
        player that is invisible, because the player asks for more as it
        goes; a cast cannot, so watching an hour behind live used to end
        without warning whenever the requested stretch ran out.

        So notice the end and ask for the next stretch, from exactly where
        the last one stopped.
        """
        ctx = self._cast_ctx or {}
        if not ctx.get("archive_from") or not self._cast_device:
            return
        if getattr(self, "_cast_paused_at", None) is not None:
            return                      # paused on purpose, not run out
        # ONLY a stream the receiver says has ended. Buffering is not an
        # ending, and treating it as one was doing the damage: a stretch
        # that was playing, eleven seconds in and pausing to fill its
        # buffer, got killed and asked for again - and the new one paid the
        # whole start-up cost afresh, seek discard and all, only to buffer
        # again a little later. The restarts were ours.
        #
        # A slow stretch is slow whoever asks for it. There is nothing to
        # rescue it with, and the picture comes back on its own.
        if not str(getattr(self.cast, "state", "")
                   ).startswith("IDLE/FINISHED"):
            return
        # Once. Loading the next stretch takes a few seconds, during which
        # the receiver still reports the end of the last one.
        now = time.monotonic()
        if now - getattr(self, "_cast_continued", 0.0) < 20:
            return
        self._cast_continued = now
        at = self._cast_moment()
        log.info("cast: the archive ran out at %s - asking for the next of it",
                 at.strftime("%H:%M:%S"))
        feed = ctx.get("feed")
        if feed is not None and feed.source is not None:
            self._cast_from_moment(at)      # the rewind buffer's pieces
            return
        self._cast_from_archive(at)

    # How close to live the archive can be read from. Only the timeshift
    # menu comes through here now - going back an hour, picking a programme
    # out of the guide - and the panel cannot serve the minute it is still
    # writing.
    ARCHIVE_LAG = timedelta(seconds=75)

    def _cast_from_moment(self, when) -> None:
        """Cast this channel from *when*: out of the rewind buffer - the one
        a feed reads, or the one kept for the cast - while it still holds
        that moment, else out of the provider's archive."""
        if self._cast_on_airplay():
            return
        ctx, device = self._cast_ctx or {}, self._cast_device
        session = self._cast_buffer()
        span = self._cast_buffer_span() if session is not None else None
        if span is not None and device and span[0] <= when.timestamp():
            new = self._buffer_feed(session, when.timestamp())
            if new is not None:
                self._cast_send_feed(new)
                return
        if ctx.get("archive"):
            self._cast_from_archive(when)
        else:
            log.info("cast: nothing holds %s - neither the rewind buffer nor "
                     "an archive", when.strftime("%H:%M:%S"))

    def _cast_send_feed(self, feed) -> None:
        """Cast the running channel from *feed*'s pieces, as one stream."""
        ctx, device = self._cast_ctx or {}, self._cast_device
        if not device or not self._cast_takes_feed(device):
            # A TV or a Kodi plays an address as it is: the first piece's
            # (the rewind buffer's loopback one) is never handed to it.
            log.info("cast: %s cannot be fed in pieces", device)
            threading.Thread(target=feed.close, daemon=True).start()
            return
        # The one on the TV now plays on until this one replaces it - the
        # bridge closes it then.
        ctx.update(url=feed.first_url or ctx.get("url"),
                   source=feed.first_url or ctx.get("source"),
                   archive_from=datetime.fromtimestamp(feed.first_wall),
                   feed=feed)
        title = ctx.get("title") or "dopeIPTV"
        self._cast_moving_to(ctx, feed.first_wall)
        log.info("cast: %s from %s, in pieces as one playlist",
                 for_log(title),
                 datetime.fromtimestamp(feed.first_wall).strftime("%H:%M:%S"))
        self._cast_send(
            lambda: self.cast.cast(device, ctx.get("url") or "", title,
                                   self._local_codecs(), ctx.get("audio"),
                                   None, start=0.0, source=ctx.get("source"),
                                   quality=self._cast_quality(),
                                   height=ctx.get("height") or 0,
                                   fps=ctx.get("fps") or 0.0, dvr=True,
                                   feed=feed),
            lambda _n: self.show_cast_strip(device, title),
            self._cast_send_failed)

    def _cast_from_archive(self, paused_at) -> None:
        """Cast this channel from a moment in the provider's catch-up.

        This is the timeshift menu's doing - going back an hour, picking a
        programme out of the guide. Pausing does not come through here: it is
        held on this machine, which is the only way it ever worked.
        Not on an Apple TV (_cast_can_go_back).
        """
        if self._cast_on_airplay():
            return
        ctx, device = self._cast_ctx or {}, self._cast_device
        sid = ctx.get("sid")
        if sid is None or not device:
            return
        try:
            self._cast_moving_to(ctx, paused_at.timestamp())
        except (AttributeError, TypeError, ValueError, OverflowError):
            pass
        # The TRANSPORT STREAM, through the converter - not the panel's HLS
        # wrapper of the same archive. This is decided by the logs, not by
        # preference: across every attempt, each cast that actually played
        # the right content read the .ts (11:36 played a two-minute-old
        # window correctly), and every freeze-after-one-second was the
        # .m3u8 near live - including a minute over four minutes old, which
        # sank the theory that age was the problem. The panel's HLS archive
        # endpoint is simply broken for short windows; its .ts endpoint is
        # not.
        #
        # The .ts is one stream, not a segment list, so no window arithmetic
        # is needed: ask from the paused minute with generous room ahead,
        # the panel serves what exists and closes at the write head, ffmpeg
        # ends cleanly, and the continuation asks for the next stretch from
        # the exact moment the picture stopped. What ruined this path before
        # is fixed elsewhere: the retry no longer spawns a second ffmpeg,
        # the bridge no longer holds half a megabyte back, and the reconnect
        # flags - which re-request a closed window from its START on panels
        # that ignore Range, replaying television at random - are off for
        # timeshift input.
        paused_at = min(paused_at, datetime.now() - self.ARCHIVE_LAG)
        if self._cast_takes_feed(device):
            # A converter here: the archive in pieces, one playlist on the
            # TV - no new stream where a stretch ends.
            feed = self._archive_feed(sid, paused_at)
            if feed is not None:
                log.info("cast: resuming %s from %s (%d min behind live)",
                         for_log(ctx.get("title") or ""),
                         paused_at.strftime("%H:%M:%S"),
                         max(1, int((datetime.now() - paused_at
                                     ).total_seconds() // 60)))
                self._cast_send_feed(feed)
                return
        # Addressed by the minute; the seconds ride along as ffmpeg's own
        # starting offset, so nothing already watched is replayed.
        minute = paused_at.replace(second=0, microsecond=0)
        offset = (paused_at - minute).total_seconds()
        behind = max(1, int((datetime.now() - minute).total_seconds() // 60))
        cands = self.client.timeshift_urls(sid, minute, behind + 240) or []
        url = next((c for c in cands if ".ts" in c), "")
        source = url or next(iter(cands), "")
        url = url or source
        if not url:
            return
        # The moment itself, not how it was worked out - it is the one thing
        # that can be checked against what the picture actually shows.
        log.info("cast: resuming %s from %s (%d min behind live)",
                 for_log(ctx.get("title") or ""), paused_at.strftime("%H:%M:%S"),
                 behind)
        title = ctx.get("title") or "dopeIPTV"
        # The session now lives at the archive address. Recording it keeps the
        # strip's own panel pointing at what is actually playing - it opened
        # on an empty address otherwise, and cast it. archive_from is what a
        # later pause measures against, so this cast can be paused again
        # without losing the shift it already has.
        ctx.update(url=url, source=source, archive_from=minute)
        # Everything the live cast was doing, the archive cast does too. It is
        # the same channel at the same size, so the device's picture ceiling,
        # the chosen tracks and the known codecs all still apply - dropping
        # them here handed an older receiver the full-size picture it had just
        # been spared, halfway through watching.
        self._cast_send(
            lambda: self.cast.cast(device, url, title, self._local_codecs(),
                                   ctx.get("audio"), ctx.get("subs"),
                                   start=offset, source=source,
                                   quality=self._cast_quality(),
                                   height=ctx.get("height") or 0,
                                   fps=ctx.get("fps") or 0.0,
                                   # Recorded here, so a programme picked out
                                   # of the archive can be held too.
                                   dvr=True),
            lambda _n: self.show_cast_strip(device, title),
            self._cast_send_failed)
