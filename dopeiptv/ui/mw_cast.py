"""MainWindow mixin: casting - a Chromecast, a DLNA TV or a Kodi plays what
the window would.

The cast dialog; the strip under the list while a cast runs (pause, volume,
tracks, quality, the timeshift menu and the progress timeline); a live
channel handed over from the provider's archive at the moment it was
paused; and the position a film reached on the TV, kept for resume. The
devices themselves are providers/casting.py's CastHub. Kept out of
main_window.py to keep that file lean.

Zapping while casting: a live channel picked while a cast runs - from the
list, the guide, a number, channel up/down, the phone, Home Assistant, the
media keys - goes to the same receiver, not to the player (_cast_zap,
asked by the playback path after its checks). One at a time: a channel
picked while the last is still on its way waits, and only the newest goes.
A film or an episode picked while a cast runs goes there too
(_cast_title): with its resume point, its remembered sound track (and
subtitle, where the converter carries one), its scrobbles and reports; at
an episode's end the next one follows on the TV. Only Stop casting ends the
cast.

The archive and the rewind buffer reach a TV in pieces: a cast_feed.Feed
reads them here into one converter run, so the TV plays one growing
playlist (_cast_from_moment) and a move within what is written is a seek
there, not a new stream. The buffer's loopback address never leaves this
computer.

Going back during a live cast (the strip's timeshift menu, its timeline):
a moment the rewind buffer still holds comes out of it, earlier ones out of
the provider's archive where the channel has one. By default the buffer is
kept only while a feed reads it (a cast started behind live) - it is then
the cast's one connection. Kept recording beside a live cast is a second
connection, so only with Settings > Casting's "Keep the rewind buffer while
casting" on, and asked first over a full or unknown limit. Only a receiver
fed through a converter here can take the buffer - a DLNA TV or a Kodi gets
the archive alone.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from functools import partial

from PyQt6 import sip
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMenu,
    QPushButton, QVBoxLayout,
)

from ..core import privacy
from ..core.log import log
from ..core.privacy import for_log
from ..core.workers import run_async
from ..i18n import LocalizedError, tr
from ..providers import cast_feed
from ..providers.chromecast import CastDialog
from ..providers.kodi import DEFAULT_PORT, parse_address
from .theme import P
from .widgets import cast_strip_icon


def _live_cast_address(win, sid) -> str:
    """A live channel's address for a cast: its HLS, or what a box that
    transcodes gives for a cast (ui/mw_box.py)."""
    play = getattr(win, "_live_play_url", None)
    return play(sid, "m3u8", "cast") if play is not None else win.client.live_url(sid, "m3u8")


def _live_hls(win, sid) -> str:
    return win.client.live_url(sid, "m3u8")


class _CastMixin:
    """MainWindow mixin: casting to a Chromecast or a DLNA TV."""

    def _open_cast_dialog(self, it) -> None:
        # No refusal here when pychromecast is missing: a DLNA TV needs
        # nothing installed, and the panel says what is missing for a
        # Chromecast when it finds nothing at all (CastDialog._scan).
        # Already casting this very title? Then this is not a new cast - it is
        # someone coming back to change the device or a track. Reopen the
        # panel on the running session: no question about resuming (that was
        # answered when it started) and nothing touched if it is closed again.
        ctx = self._cast_ctx or {}
        if (getattr(self.cast, "active", None) is not None
                and ctx.get("key") is not None
                and ctx.get("key") == self._item_key(it)):
            self.manage_cast()
            return
        row_kind = it.get("_kind")
        if row_kind:
            live = row_kind == "live"
        else:
            live = (self._content_kind() in ("live", "fav")
                    or (self.mode == "fav" and self._fav_section == "chan"))
        sid = it.get("stream_id")
        # A media server opens the channel to give its address, a portal asks
        # create_link: off the window's thread first, as the player's.
        later = getattr(self, "_links_later", None)
        if live and sid is not None and later is not None and later(
                partial(_live_cast_address, self, sid), partial(self._open_cast_dialog, it),
                key="cast"):
            return
        url, title = self._stream_for(it)
        if not url:
            url = it.get("_url")
            title = it.get("name") or it.get("title") or "dopeIPTV"
        # A live channel has to go as HLS: the receiver cannot decode a raw
        # MPEG transport stream at all, whatever we label it. Every section
        # can show a live row - Channels, Favorites, History, Home - so the
        # rule belongs here and not inside one section's branch. History used
        # to hand over its stored .ts address verbatim, which is why casting
        # worked from the channel list and never from History.
        # The ROW decides, and only when it says nothing does the section get
        # a vote. Letting the section decide made a favourite film into a
        # channel: a movie row in Favorites was handed a /live/ address built
        # from its own id, which the panel answers with a 4XX - to the
        # receiver, to the converter, to everything.
        # Two addresses for the same channel. HLS is the one a Chromecast can
        # take directly; the plain stream is the one the player is watching -
        # and some channels are not served as HLS at all, which is a 4XX to
        # everything that asks. The converter reads that one, since ffmpeg has
        # no trouble with a transport stream and the receiver never sees it.
        source = url
        if live and sid is not None:
            url = _live_cast_address(self, sid)
        elif row_kind in ("movie", "vod") and sid is not None:
            # Build the film's own address rather than trust what the section
            # produced. In the Favorites channel folders _stream_for answers
            # for the section, not the row, so a favourite film came back with
            # a /live/ address built from its id - which the panel refuses.
            url = source = self.client.vod_url(
                sid, it.get("container_extension"))
        # A row built from nothing but the address on screen (a play from
        # Home, a resumed title) says so, and that address is used as it is -
        # there is no provider id to build anything else from.
        if it.get("_cast_url"):
            url = source = it["_cast_url"]
        if not url:
            return
        # The way in every stream takes: Nerd's VPN guard, and the account's
        # connections - the TV, Kodi or the converter here holds one.
        if (self._vpn_guard_blocks(source) or not self._cast_pin_ok(url, source)
                or not self._conn_ok_cast(source)):
            return
        # Remember what is being cast, not just where to. Pausing a live
        # channel is answered from the provider's archive, and that needs the
        # channel's own id long after this dialog is gone.
        # A film or an episode picks up where you left off, exactly as it
        # does here - the same stored point, and the same question about it.
        rkind = self._CAST_RESUME_KIND.get(
            it.get("_kind") or self._content_kind())
        key = self._item_key(it)
        # Where the film actually is, which is the player when the player is
        # the one playing it. The stored point is only written when playback
        # switches or stops, so casting a film you are 22 minutes into asked
        # a store that knew nothing yet and offered to start it over.
        # Only where there is something to resume. A live channel has no
        # point to come back to - what a timeshift channel is a few minutes
        # into is this session, not a place in a title - and being asked
        # whether to carry on from 23 minutes in before casting the news was
        # a question about nothing.
        start = 0.0
        if rkind:
            here = self._playing_position(it)
            start = (self._ask_resume(here) if here > 60
                     else self._resume_offset(key, rkind))
        # A channel watched from the rewind buffer, behind live: the TV
        # carries on from there, out of the buffer (held streaming while
        # the cast reads it) - the player stops before the cast starts.
        if not getattr(self, "_cast_device", None):
            self._cast_drop_feed()
        self._cast_release_buffer()
        feed = self._cast_buffer_feed(it) if live else None
        # Asked for in Settings > Casting: the buffer goes on recording while
        # the TV plays live, so the strip can go back in it - held before the
        # player stops (which would freeze it). A second connection, so
        # never by itself.
        buffer, hold = (self._cast_keep_buffer(it, feed, source) if live
                        else (None, None))
        self._cast_ctx = {
            "sid": it.get("stream_id") if live else None,
            # Not a portal's: it is asked for by programme, which only the
            # player does (mw_portal_archive).
            "archive": bool(live and it.get("stream_id") is not None
                            and it.get("tv_archive")
                            and not getattr(self.client,
                                            "archive_by_programme", False)),
            "title": title,
            "group": self._RESUME_GROUP.get(rkind or ""),
            "key": key,
            "item": it,
            # The profile's own Continue watching, kept past a switch: where
            # the TV got to belongs to whoever started the cast - and its
            # History, written when the receiver says it plays.
            "resume": getattr(self, "resume", None),
            "history": getattr(self, "history", None),
            # For History: the row's own kind and address, kept apart from
            # the address currently on the receiver, which an archive resume
            # or a track change replaces.
            "kind": self._play_kind_for(it),
            "row_url": url,
            "row_source": source,
            "feed": feed,
            "archive_from": (datetime.fromtimestamp(feed.first_wall)
                             if feed is not None else None),
            "buffer": buffer,
            "buffer_hold": hold,
        }
        CastDialog(self, url, title, self._local_codecs(),
                   self._local_audio_index(), start,
                   self._local_tracks(it),
                   probe=not self._busy_elsewhere(it), source=source,
                   live=self._play_kind_for(it) == "live").exec()
        if not getattr(self, "_cast_device", None):
            if feed is not None:
                self._cast_drop_feed(ending=True)   # nothing was cast from it
            self._cast_release_buffer()

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

    def _cast_keep_buffer(self, it, feed=None, url: str = "") -> tuple:
        """(session, hold): the rewind buffer of *it* - the channel the player
        plays from its buffer, or the one *feed* reads - held streaming for
        a live cast of *url*, so the strip can go back in it while the TV
        plays live. Only with Settings > Casting's switch on: it is one more
        connection to the provider next to the cast's, and over a full or
        unknown limit the person is asked first. (None, None) when not kept -
        it freezes as the player stops (a feed holds it only while it reads
        it), so no second connection is ever opened by itself."""
        session = feed.source if feed is not None else self._playing_buffer(it)
        if (session is None or not getattr(session, "streaming", False)
                or not self._cast_keeps_buffer()):
            return None, None
        ask = getattr(self, "_conn_ok_cast_buffer", None)
        if not callable(ask) or not ask(url):
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
        or its rewind buffer holds some of it."""
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

    def _cast_pin_ok(self, *urls: str) -> bool:
        """Nerd: the source trusts one certificate for its server. A TV,
        Kodi or an Apple TV fetches the stream itself, so the pin cannot
        protect a cast: asked first, and "Cast anyway" holds for that source
        until the app closes (never stored)."""
        from ..core import cert_pin, nerd
        if not nerd.current().on:
            return True             # the pin is in force only with Nerd on
        store = getattr(self, "playlist_store", None)
        pl = (store.active() if store is not None else None) or {}
        hit = cert_pin.covers(pl.get("options"), *urls)
        if hit is None:
            return True
        allowed = getattr(self, "_cast_pin_allowed", None)
        if allowed is None:
            allowed = self._cast_pin_allowed = set()
        sid = str(pl.get("id") or "")
        if sid in allowed:
            return True
        host, port = hit
        server = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        idx = self._choice_dialog(
            tr("cast_pin_title"),
            tr("cast_pin_body", name=str(pl.get("name") or ""), server=server),
            [(tr("cast_pin_anyway"), "normal"), (tr("common_cancel"), "primary")])
        log.info("cast: the source's trusted certificate cannot cover a cast - %s",
                 "cast anyway" if idx == 0 else "not cast")
        if idx != 0:
            return False
        allowed.add(sid)
        return True

    # The list vocabulary and the resume store's do not match: a movie row is
    # "vod" in one and "movie" in the other, and History rows carry their own.
    _CAST_RESUME_KIND = {"vod": "movie", "movie": "movie",
                         "episode": "episode",
                         "rec": "recording", "recording": "recording",
                         "local": "local"}
    _RESUME_GROUP = {"movie": "vod", "episode": "episode", "recording": "rec",
                     "local": "local"}

    def _busy_elsewhere(self, it) -> bool:
        """Is the app holding a provider connection for something else?

        Listing tracks with ffprobe opens the stream again, and one of these
        accounts allows a single connection at a time. While the player has
        one for another title, that probe cannot succeed and asking anyway
        just spends the seconds the panel then keeps counting.
        """
        p = getattr(self, "player", None)
        if p is None or not getattr(p, "current_url", None):
            return False
        return self._item_key(it) != self._playing_key

    def cast_playing(self) -> None:
        """Send what the player is showing to a Chromecast.

        The same dialog the list's right-click opens, on the row that is
        playing - so you can start watching here and move it to the TV
        without hunting for where the channel was in the list.
        """
        p = getattr(self, "player", None)
        if p is None or not getattr(p, "current_url", None):
            return
        it = getattr(self, "_playing_item", None)
        if not it:
            it = (getattr(self, "_last_playback", None) or {}).get("item")
        if not it:
            # Not every way into the player leaves the row behind - a play
            # from Home, a resumed title. The address on screen is enough to
            # cast, and having the entry disappear for those would be the
            # wrong half of the feature.
            it = {"name": getattr(self, "_detail_name", "") or "dopeIPTV",
                  "_url": p.current_url, "_cast_url": p.current_url}
        self._open_cast_dialog(it)

    def can_cast_playing(self) -> bool:
        p = getattr(self, "player", None)
        return bool(p is not None and getattr(p, "current_url", None))

    def _playing_position(self, it) -> float:
        """How far into *it* the app itself has got, or 0 when it is not the
        thing playing (or has no length, as live has none)."""
        p = getattr(self, "player", None)
        if (p is None or self._playing_key is None
                or self._item_key(it) != self._playing_key):
            return 0.0
        try:
            return p.playback_position() if p.playback_duration() > 1 else 0.0
        except Exception:
            return 0.0

    def _local_tracks(self, it) -> dict:
        """The tracks mpv can already see, in the shape ffprobe would give.

        Asking ffprobe means opening the stream a second time, and these
        accounts are sold with one connection: the probe's session was still
        counted when the cast went for the same stream, and the panel answered
        the converter with a 4XX. mpv is playing the very thing being cast and
        has the whole track list in memory - free, instant, and correct.

        Only when it IS the same thing: this returns nothing for any other row
        and ffprobe answers for those instead.
        """
        if self._playing_key is None or self._item_key(it) != self._playing_key:
            return {}
        m = getattr(getattr(getattr(self, "player", None), "video", None),
                    "mpv", None)
        if m is None:
            return {}
        out: dict = {"audio": [], "subtitle": [], "duration": 0.0,
                     "height": 0, "fps": 0.0}
        try:
            # How long the film is. mpv knows, and without it the cast strip
            # has nothing to measure a position against - so a film handed
            # over from the player, which is the ordinary way of casting one,
            # arrived at the TV with no way to move within it.
            #
            # A broadcast has no length, whatever mpv answers: for a live HLS
            # stream that number is the seekable window, a minute or so of
            # buffer, and putting a position bar on THAT turns the strip into
            # a buffer gauge for a channel whose way back is the archive.
            if self._play_kind_for(it) != "live":
                out["duration"] = float(getattr(m, "duration", 0) or 0.0)
            for t in (m.track_list or []):
                kind = t.get("type")
                if kind == "video" and not out["height"]:
                    out["height"] = int(t.get("demux-h") or 0)
                    out["fps"] = float(t.get("demux-fps") or 0.0)
                    if not out["height"]:
                        # The track list fills demux-h in when the demuxer
                        # gets round to it, and on a live stream that can be
                        # after the cast has already been asked for. mpv's own
                        # properties know as soon as the first frame is
                        # decoded, which is always sooner.
                        out["height"] = int(getattr(m, "height", 0) or 0)
                        out["fps"] = float(
                            getattr(m, "container_fps", 0) or 0.0)
                if kind not in ("audio", "sub"):
                    continue
                key = "audio" if kind == "audio" else "subtitle"
                out[key].append({
                    "index": len(out[key]),
                    "codec": t.get("codec") or "?",
                    "lang": (t.get("lang") or "").strip(),
                    "title": (t.get("title") or "").strip(),
                    "default": bool(t.get("default")),
                })
        except Exception as e:
            log.debug("cast: could not read the local tracks (%s)", e)
            return {}
        log.info("cast: %d audio and %d subtitle track(s), from the player",
                 len(out["audio"]), len(out["subtitle"]))
        return out

    def _local_audio_index(self) -> int:
        """Which audio track the app itself is playing, counted the way
        ffmpeg counts them.

        If you switched to the Swedish track here, that is the one you meant
        to send to the TV - the cast dialog opens on it rather than on
        whatever the stream calls its default. Zero means the default, and
        the default is what keeps a cast native.
        """
        m = getattr(getattr(getattr(self, "player", None), "video", None),
                    "mpv", None)
        if m is None:
            return 0
        try:
            audio = [t for t in (m.track_list or []) if t.get("type") == "audio"]
            for i, t in enumerate(audio):
                if t.get("selected"):
                    return i
        except Exception as e:
            log.debug("cast: could not read the local audio track (%s)", e)
        return 0

    def _local_codecs(self) -> list[str]:
        """Write down what the stream actually is, while we still know.

        A Chromecast refuses a stream it cannot decode without ever saying
        which part it choked on, and an HLS media playlist carries no codec
        information at all - the segments are just paths. The one place the
        answer exists is mpv, which may be decoding the very same stream right
        now, so ask it before handing the channel to a TV. HEVC video or AC-3
        audio on an older receiver is a refusal no address can fix.
        """
        m = getattr(getattr(getattr(self, "player", None), "video", None),
                    "mpv", None)
        if m is None:
            return []
        found = []
        try:
            for t in (m.track_list or []):
                if t.get("selected") and t.get("type") in ("video", "audio"):
                    codec = t.get("codec") or "?"
                    log.info("cast: what is playing here - %s %s",
                             t.get("type"), codec)
                    found.append(codec)
        except Exception as e:
            log.debug("cast: could not read the local codecs (%s)", e)
        return found

    def _stop_cast_for_local_playback(self) -> None:
        """End a running cast when something starts playing in the app.

        Casting is a handover: the stream goes to the TV and local playback
        stops. Starting a video here is the same handover in reverse, so the
        cast has to end - otherwise the account holds two connections at once
        and on a tight limit the new stream is simply refused, which looks
        like the app failing to play anything after a cast.

        """
        self._end_cast("local playback took over")

    # -- zapping while casting -------------------------------------------------

    _cast_zap_busy = False
    _cast_zap_sent: int | None = None   # the cast's generation it went in
    _cast_zap_gen = 0                   # one up as a cast ends
    _cast_hooked = False

    def _cast_runs(self) -> bool:
        """A cast is on a receiver now - not one that ended on the device
        itself (a Kodi's own remote)."""
        cc = getattr(self, "cast", None)
        if (not getattr(self, "_cast_device", None) or cc is None
                or getattr(cc, "active", None) is None):
            return False
        ended = getattr(cc, "ended_elsewhere", None)
        return not (callable(ended) and ended())

    def _cast_live_item(self) -> dict | None:
        """The channel a running cast shows, or None (nothing cast, or a
        film): what channel up/down and a typed number go on from."""
        ctx = getattr(self, "_cast_ctx", None) or {}
        if ctx.get("sid") is None or not self._cast_runs():
            return None
        return ctx.get("item") or None

    def _cast_zap(self, url: str, title: str, icon_url, key, kind: str,
                  item, catchup: bool) -> bool:
        """A live channel picked while a cast runs: sent to the same
        receiver, the player left as it is. True when it was taken (sent,
        or refused by a check the user answered); False for the player.

        Asked by the playback path after its own checks (a recording, the
        VPN guard); a cast's own come here: the source's trusted
        certificate and the account's connections."""
        if kind in ("movie", "episode") and not catchup and item \
                and self._cast_runs():
            return self._cast_title(url, title, icon_url, key, kind, item)
        if (kind != "live" or catchup or not item
                or item.get("stream_id") is None or not self._cast_runs()):
            return False
        timer = getattr(self, "_preview_timer", None)
        if timer is not None:
            timer.stop()
        device = self._cast_device
        old = self._cast_ctx or {}
        if old.get("sid") is not None and old.get("key") == key:
            log.info("cast: that channel is on %s already", device)
            return True
        sid = item.get("stream_id")
        # The same two addresses the cast dialog takes: HLS for a receiver
        # that fetches it itself, the plain stream for the converter and
        # for a TV or Kodi, which play it as it is.
        waits = getattr(self, "_link_waits", None)
        try:
            if waits is not None and waits(partial(_live_hls, self, sid)) is not None:
                # Slow to get - a media server would open the channel once
                # more, a portal ask create_link - and theirs is the same
                # address whatever the format: the one the play path got.
                hls = url
            else:
                hls = self.client.live_url(sid, "m3u8") or url
        except Exception as e:
            log.debug("cast: no HLS address for the channel (%s)", e)
            hls = url
        if not self._cast_pin_ok(hls, url) or not self._conn_ok_cast(url):
            return True
        # The one on the TV ends as it would here: a film keeps its point,
        # its scrobbles end, failover may try again.
        self._save_cast_position()
        self._failover_leave(url, key)
        self._trakt_stop_current()
        self._simkl_stop_current()
        self._ms_leave()
        if old.get("sid") is not None and old.get("item"):
            self._prev_live_item = old["item"]
        self._cast_drop_feed()
        self._cast_release_buffer()         # the last channel's buffer
        self._cast_ctx = {
            "sid": sid,
            "archive": bool(item.get("tv_archive")
                            and not getattr(self.client,
                                            "archive_by_programme", False)),
            "title": title, "group": None, "key": key, "item": item,
            "history": getattr(self, "history", None),
            "kind": "live", "row_url": hls, "row_source": url,
            "url": hls, "source": url, "codecs": [], "height": 0, "fps": 0.0,
            "audio": None, "subs": None, "duration": 0.0, "tracks": {},
            "_zap_pending": True,       # picked, not sent yet
        }
        # Play after Stop, the media keys and the phone go on from here.
        self._last_playback = {"url": url, "title": title,
                               "icon_url": icon_url, "key": key,
                               "kind": "live", "item": item,
                               "series_ctx": None}
        log.info("cast: zapping %s to %s", device, for_log(title))
        self._set_status(tr("cast_starting", name=device))
        # The strip says the new channel at once: History, the reporters.
        self.show_cast_strip(device, title)
        self._cast_zap_send()
        return True

    def _cast_title(self, url: str, title: str, icon_url, key, kind: str,
                    item) -> bool:
        """A film or an episode picked while a cast runs: to the same
        receiver, from its resume point, as a local start would - the
        scrobbles, a media server's report, History, the episode queue."""
        timer = getattr(self, "_preview_timer", None)
        if timer is not None:
            timer.stop()
        device = self._cast_device
        if not self._cast_pin_ok(url) or not self._conn_ok_cast(url):
            return True
        old = self._cast_ctx or {}
        if old.get("key") == key and old.get("kind") == kind:
            log.info("cast: that title is on %s already", device)
            return True
        self._save_cast_position()
        self._failover_leave(url, key)
        self._trakt_stop_current()
        self._simkl_stop_current()
        self._ms_leave()
        if old.get("sid") is not None and old.get("item"):
            self._prev_live_item = old["item"]
        self._cast_drop_feed()
        self._cast_release_buffer()
        rkind = self._CAST_RESUME_KIND.get(kind)
        start = self._resume_offset(key, kind, item)
        audio, subs = self._cast_title_tracks(kind, key, device)
        self._cast_ctx = {
            "sid": None, "archive": False, "title": title,
            "group": self._RESUME_GROUP.get(rkind or ""), "key": key,
            "resume": getattr(self, "resume", None),
            "history": getattr(self, "history", None),
            "item": item, "kind": kind, "row_url": url, "row_source": url,
            "url": url, "source": url, "codecs": [], "height": 0, "fps": 0.0,
            "audio": audio, "subs": subs, "duration": 0.0, "tracks": {},
            "start": float(start or 0.0),
            "_zap_pending": True,
        }
        self._last_playback = {"url": url, "title": title,
                               "icon_url": icon_url, "key": key,
                               "kind": kind, "item": item,
                               "series_ctx": (self.series_ctx
                                              if kind == "episode" else None)}
        if kind == "episode":
            # The queue the next episode comes from, as a local start keeps.
            queue = getattr(self, "_ep_queue_override", None)
            idx = getattr(self, "_ep_index_override", -1)
            self._ep_queue_override, self._ep_index_override = None, -1
            if queue is None and self.series_ctx:
                queue = [e for e in (self.all_items or [])
                         if not e.get("_header")]
                idx = next((i for i, e in enumerate(queue)
                            if e is item or self._item_key(e) == key), -1)
            self._last_playback["ep_queue"] = queue or []
            self._last_playback["ep_index"] = idx
        self._trakt_start_for_item(kind, item)
        self._simkl_start_for_item(kind, item)
        self._ms_started(url, key, kind, item, float(start or 0.0))
        log.info("cast: %s to %s, from %d s", for_log(title), device,
                 int(start or 0))
        self._set_status(tr("cast_starting", name=device))
        self.show_cast_strip(device, title)
        self._cast_zap_send()
        return True

    def _cast_title_tracks(self, kind: str, key, device) -> tuple:
        """The sound track (and subtitle) remembered for this title, as
        the cast takes them: a Chromecast through the converter, AirPlay
        the sound track, a TV or a Kodi neither (it plays the stream as
        it is). The default sound track is no choice - it keeps a cast
        native."""
        if self._cast_plays_as_is(device):
            return None, None
        prefs = self._track_prefs()
        pref = prefs.get(f"{kind}:{key}")
        if not isinstance(pref, dict):
            pref = next((prefs[k] for k in self._series_pref_keys(kind)
                         if isinstance(prefs.get(k), dict)), None) or {}
        aid, sid = pref.get("aid"), pref.get("sid")
        audio = ({"index": int(aid) - 1}
                 if isinstance(aid, int) and int(aid) > 1 else None)
        subs = ({"index": int(sid) - 1}
                if isinstance(sid, int) and int(sid) > 0
                and not self._cast_on_airplay() else None)
        return audio, subs

    def _cast_zap_send(self) -> None:
        """Send the channel the cast should show, unless one is on its way:
        then the newest goes once that one is in (two at once would race
        on the receiver, its converter and the account's one connection)."""
        if self._cast_zap_busy:
            return
        ctx, device = self._cast_ctx or {}, self._cast_device
        if not device or not ctx.pop("_zap_pending", False) or not ctx.get("url"):
            return
        self._cast_zap_busy = True
        self._cast_zap_sent = self._cast_zap_gen
        # A converter feeding the old channel lets go of its connection
        # first, and the provider is given a moment to notice.
        settle = bool(self.cast.bridged())
        run_async(self.pool, self._cast_zap_work, self._cast_zap_done,
                  self._cast_zap_failed, device, ctx, self._cast_quality(),
                  settle, self._cast_zap_gen)

    def _cast_zap_work(self, device: str, ctx: dict, quality: str,
                       settle: bool, gen: int) -> int:
        """Off the window's thread: the receiver's own cast call, to the
        device it is on (AirPlay swaps the item in the player it has)."""
        if gen != self._cast_zap_gen:
            raise LocalizedError("cast_stopped")
        audio, subs = ctx.get("audio"), ctx.get("subs")
        duration = float(ctx.get("duration") or 0.0)
        if ctx.get("kind") in ("movie", "episode") \
                and not self._cast_on_airplay() \
                and not self._cast_plays_as_is(device):
            # A Chromecast is told the film's length (it is what lets the
            # strip move within it), and a remembered subtitle needs its
            # kind - from one look into the stream, once the converter of
            # the last one has let go of the account's connection.
            if settle:
                self.cast.bridge.stop()
                time.sleep(1.5)
                settle = False
            from ..providers.cast_bridge import probe_tracks
            try:
                found = probe_tracks(ctx.get("source") or ctx["url"]) or {}
            except Exception as e:
                log.info("cast: could not look into the title (%s)", e)
                found = {}
            duration = float(found.get("duration") or duration or 0.0)
            ctx["duration"] = duration
            ctx["tracks"] = {"audio": found.get("audio") or [],
                             "subtitle": found.get("subtitle") or []}
            if subs is not None:
                hit = next((t for t in found.get("subtitle") or []
                            if t.get("index") == subs.get("index")), None)
                subs = dict(subs, codec=hit.get("codec") or "",
                            lang=hit.get("lang") or "") if hit else None
                ctx["subs"] = subs
        self.cast.cast(device, ctx["url"], ctx.get("title") or "dopeIPTV",
                       [], audio, subs, float(ctx.get("start") or 0.0),
                       duration, settle, ctx.get("source"),
                       quality, 0, 0.0, dvr=bool(ctx.get("archive")))
        return gen

    def _cast_zap_next(self) -> int:
        """After one went: when it was sent (the cast's generation), and
        the channel picked meanwhile, if any, on its way now."""
        gen = -1 if self._cast_zap_sent is None else self._cast_zap_sent
        self._cast_zap_busy, self._cast_zap_sent = False, None
        self._cast_zap_send()
        return gen

    def _cast_zap_done(self, _gen) -> None:
        gen = self._cast_zap_next()
        if self._cast_zap_busy:
            return                      # a newer channel is on its way
        if gen == self._cast_zap_gen and self._cast_device:
            device, ctx = self._cast_device, self._cast_ctx or {}
            self._set_status(tr("cast_casting_to", name=device))
            self.show_cast_strip(device, ctx.get("title") or "")
            return
        cc = getattr(self, "cast", None)
        if not self._cast_device and cc is not None \
                and getattr(cc, "active", None) is not None:
            # Stopped while it was on its way: the TV stops as it was told.
            threading.Thread(target=cc.stop, daemon=True).start()

    def _cast_zap_failed(self, msg: str) -> None:
        gen = self._cast_zap_next()
        if self._cast_zap_busy or not self._cast_device \
                or gen != self._cast_zap_gen:
            return
        self._error(tr("cast_failed", msg=msg))
        if getattr(self.cast, "active", None) is None:
            self.show_cast_strip(None)          # the receiver let go of it

    def _cast_began(self, ctx: dict) -> None:
        """What a cast shows is what is watched: the reporters (events,
        Discord) hear of it as of a play here, so a zap on the cast moves
        them on as a local zap does. Statistics count the player only."""
        key, kind = ctx.get("key"), ctx.get("kind")
        if key is None or not kind:
            return
        privacy.playback_started(kind, key)
        self._cast_hooked = True
        title, item = ctx.get("title") or "", ctx.get("item")
        for name in ("_events_started", "_discord_started"):
            hook = getattr(self, name, None)
            if callable(hook):
                hook(key, title, item, kind, False)

    def _cast_hooks_end(self) -> None:
        if not self._cast_hooked:
            return
        self._cast_hooked = False
        privacy.playback_stopped()
        for name in ("_events_stopped", "_discord_stopped"):
            hook = getattr(self, name, None)
            if callable(hook):
                hook()

    def show_cast_strip(self, device: str | None, title: str = "") -> None:
        """Show - or take down - the "Casting to X" strip above the player.

        Casting stops local playback (the receiver pulls the stream itself),
        so the player pane goes black and without this nothing anywhere says
        that anything is happening at all. The cast dialog calls it when a
        cast starts, fails or is stopped.
        """
        self._cast_device = device
        ctx = getattr(self, "_cast_ctx", None) or {}
        if not device:
            # The cast is over: a channel still on its way is not sent, and
            # whoever heard of it hears it stopped.
            self._cast_zap_gen += 1
            self._cast_hooks_end()
            self._cast_drop_feed(ending=True)
            self._cast_release_buffer()
        elif not self._cast_takes_feed(device):
            # A TV or a Kodi took the address as it is: no pieces read here,
            # and the rewind buffer (this computer's loopback) is no use to it.
            if ctx.get("feed") is not None:
                self._cast_drop_feed(ending=True)
                ctx["archive_from"] = None
            self._cast_release_buffer(ctx)
        elif ctx.get("buffer") is not None and not self._cast_converts():
            # No ffmpeg here: the buffer kept beside the cast cannot reach it.
            self._cast_release_buffer(ctx)
        self._cast_key = ctx.get("key") if device else None
        self._cast_group = ctx.get("group") if device else None
        if self._cast_group is None and device:
            # A live channel has no resume group, but it still has a row.
            self._cast_group = "live" if ctx.get("sid") else None
            self._cast_key = ctx.get("key")
        for name in ("listw", "grid"):
            view = getattr(self, name, None)
            try:
                view.viewport().update()
            except AttributeError:
                pass
        bar = getattr(self, "cast_bar", None)
        if bar is None:
            return
        self.cast_bar_pause.setIcon(cast_strip_icon("pause", P["text"]))
        # Shown only where pausing works. A film the receiver fetched itself
        # holds its place; a broadcast can be held only while the converter
        # is recording it. A channel that ended up going straight to the
        # receiver has neither, and a button that cannot do what it says is
        # worse than no button.
        self.cast_bar_pause.setVisible(
            bool(device) and (not ctx.get("sid") or self.cast.bridged()))
        self.cast_bar_ts.setVisible(bool(device) and self._cast_can_go_back())
        airplay_btn = getattr(self, "cast_bar_airplay", None)
        if airplay_btn is not None:
            airplay_btn.setVisible(bool(device) and self._cast_on_airplay())
        if not device:
            self._cast_tick.stop()
            bar.hide()
            return
        # A different thing is being cast now: how far behind live the last
        # one was is not about this one. Left standing, a channel paused for
        # ten minutes made the next cast open ten minutes behind itself.
        #
        # Marked in the context rather than reset on every call, because this
        # runs again for a track change and for going back to live - and both
        # of those are in the middle of managing this very counter. Only
        # starting something new builds a new context, so the mark is gone.
        if not ctx.get("_counted"):
            ctx["_counted"] = True
            self._cast_behind = 0.0
            self._cast_paused_at = None
            self._cast_began(ctx)
        # The player pane goes with it. Casting stops local playback - the
        # receiver pulls the stream itself, and on a one-connection account
        # two readers is one too many - so what is left behind is a black
        # rectangle with a toolbar under it that controls nothing. The strip
        # above it is what is playing now.
        #
        # Not shown again from here: when the cast ends nothing is playing
        # locally either, and every path that starts playback shows the
        # player itself.
        pl = getattr(self, "player", None)
        if pl is not None and pl.isVisible():
            pl.hide()
        self._record_cast_history()
        self._show_cast_progress()
        self._show_cast_label()
        self.cast_bar_title.setText(title or "")
        self.cast_bar_title.setVisible(bool(title))
        self._show_cast_volume()
        bar.show()

    def _show_cast_label(self) -> None:
        """What the cast is doing, on the strip: "Casting to X" - and for
        AirPlay, until the Apple TV has the stream, what to click."""
        device = self._cast_device
        if not device:
            return
        waiting = getattr(self.cast, "waiting_for_route", None)
        text = (tr("airplay_pick_window")
                if self._cast_on_airplay() and callable(waiting) and waiting()
                else tr("cast_casting_to", name=device))
        self.cast_bar_lbl.setText(text)

    def _cast_airplay_window(self) -> None:
        """The strip's AirPlay button: the AirPlay window back."""
        show = getattr(self, "_airplay_show_window", None)
        if callable(show):
            show()

    def _history_extra(self, kind: str, item, title: str) -> dict | None:
        """What a History row needs beyond its address, for this kind.

        For a live channel, its stream_id and archive depth, so a later replay
        from History still has timeshift and catch-up available.

        For an episode, its series - so a replay from History (or Home's
        Recently viewed) resumes as an EPISODE, lands in the series' episode
        list and resolves the series' poster. Without it the row degraded to a
        context-less "movie": restarted from zero, duplicated in History (the
        kind mismatch broke the dedup) and posterless. Same slim snapshot the
        resume store keeps.
        """
        if kind == "live" and item is not None:
            return {"stream_id": item.get("stream_id"),
                    "num": item.get("num"),
                    "tv_archive": item.get("tv_archive"),
                    "tv_archive_duration": item.get("tv_archive_duration")}
        if kind == "local" and item is not None:
            return {k: item.get(k)
                    for k in ("_year", "_filename", "_clean_title", "_path")
                    if item.get(k)}
        if kind != "episode":
            return None
        sctx = self.series_ctx or {}
        if sctx.get("series_id") is None:
            return None
        slim = {k: sctx.get(k)
                for k in ("series_id", "name", "title", "cover",
                          "stream_icon", "category_id", "_tmdb_id")
                if sctx.get(k) is not None}
        sname = sctx.get("name") or sctx.get("title")
        extra = {"_series_ctx": slim, "_series_title": sname}
        # Store the row as "Series · S1 * E2 - ..." so History and the Home
        # shelf say WHICH show the episode belongs to - a bare "S01 E01" told
        # the user nothing. Skip when the title already carries it (a
        # continue-watching replay).
        if sname and not title.startswith(sname):
            extra["name"] = f"{sname} · {title}"
        return extra

    def _record_cast_history(self) -> None:
        """A cast is a play, and belongs in History like any other.

        Nothing else recorded it. Every other route into playback goes through
        _start_playback, which a cast deliberately does not - the stream never
        touches this machine - so an evening's television watched on the TV
        left no trace at all, and could not be picked up again from History
        the way anything played here can.
        """
        ctx = getattr(self, "_cast_ctx", None) or {}
        it = ctx.get("item") or {}
        # The row's own address, not the one currently on the receiver: an
        # archive resume replaces that with a timeshift URL good for minutes.
        url, key, kind = ctx.get("row_url"), ctx.get("key"), ctx.get("kind")
        if not url or key is None or not kind:
            return
        # The History of the profile that started the cast: the receiver
        # says it plays a few seconds on, perhaps after a switch.
        store = ctx.get("history")
        if store is None:
            store = self.history
        store.add(url, ctx.get("title") or "",
                         it.get("stream_icon") or it.get("cover"), key, kind,
                         extra=self._history_extra(kind, it,
                                                   ctx.get("title") or ""))

    def manage_cast(self) -> None:
        """Reopen the cast panel on the session that is already running.

        Same dialog, but it starts nothing and changes nothing by itself:
        the device list is the remembered one (discovery disconnects every
        device to start again, which is the last thing a running cast needs),
        the tracks are the ones playing, and casting from here picks up where
        the TV has got to rather than at the beginning.
        """
        ctx = self._cast_ctx or {}
        if not ctx.get("url"):
            # Nothing recorded to reopen on. Better the ordinary panel than
            # one that would cast an empty address.
            log.info("cast: no address recorded for the running cast")
            return
        url, source = ctx.get("url") or "", ctx.get("source")
        if cast_feed.is_loopback(url) or cast_feed.is_loopback(source or ""):
            # A cast out of the rewind buffer: its pieces are the feed's, and
            # another device picked here is given the channel's own address,
            # never this computer's loopback one.
            url = ctx.get("row_url") or url
            source = ctx.get("row_source") or url
        CastDialog(self, url, ctx.get("title") or "",
                   self._local_codecs(), 0, self.cast.position(),
                   ctx.get("tracks") or {}, probe=False,
                   source=source, managing=True,
                   chosen=(ctx.get("audio"), ctx.get("subs")),
                   live=ctx.get("kind") == "live").exec()

    def _cast_on_airplay(self) -> bool:
        is_airplay = getattr(getattr(self, "cast", None), "is_airplay", None)
        return bool(callable(is_airplay)
                    and is_airplay(getattr(self, "_cast_device", None)))

    def _cast_volume(self, level: float) -> None:
        """Set the TV's volume. Off the UI thread: it is a message to a device
        on the network, and nothing here should wait for it. Not AirPlay's:
        the Apple TV's remote sets its volume (_show_cast_volume)."""
        if getattr(self.cast, "active", None) is None or self._cast_on_airplay():
            return
        self._cast_level = int(round(level * 100))
        if getattr(self, "_cast_muted", False) and level > 0:
            # Reaching for the volume while it is muted means you want to
            # hear it - leaving the mute on would make the slider do nothing.
            self._toggle_cast_mute()
        threading.Thread(target=self.cast.set_volume, args=(level,),
                         daemon=True).start()

    def _toggle_cast_mute(self) -> None:
        """Silence the TV, and say so on the slider.

        A slider still sitting at half while nothing comes out of the
        television is the control disagreeing with itself. It goes to zero
        with the mute and comes back to where it was when the sound does -
        the level itself is never changed, so unmuting needs no guess.
        """
        if getattr(self.cast, "active", None) is None or self._cast_on_airplay():
            return
        self._cast_muted = not getattr(self, "_cast_muted", False)
        if self._cast_muted:
            self._cast_level = self.cast_bar_vol.value()
        self.cast_bar_mute.setIcon(cast_strip_icon(
            "muted" if self._cast_muted else "volume", P["text"]))
        # Without the guard the move would be read as someone setting the
        # volume to zero, which is a different thing and would lose the level.
        self.cast_bar_vol.blockSignals(True)
        self.cast_bar_vol.setValue(
            0 if self._cast_muted else getattr(self, "_cast_level", 50))
        self.cast_bar_vol.blockSignals(False)
        threading.Thread(target=self.cast.set_muted,
                         args=(self._cast_muted,), daemon=True).start()

    def _show_cast_volume(self) -> None:
        """Put the TV's own level on the slider, so it starts where the
        television actually is rather than where the app guessed.

        Not for AirPlay: AVPlayer's volume does nothing while the Apple TV
        plays the video - its own remote (or the TV) sets it - so the
        slider and the mute are not shown for it."""
        airplay = self._cast_on_airplay()
        self.cast_bar_vol.setVisible(not airplay)
        self.cast_bar_mute.setVisible(not airplay)
        if airplay:
            return
        try:
            level, muted = self.cast.volume()
        except Exception:
            return
        self._cast_muted = muted
        self._cast_level = int(round(level * 100))
        self.cast_bar_vol.blockSignals(True)
        self.cast_bar_vol.setValue(0 if muted else self._cast_level)
        self.cast_bar_vol.blockSignals(False)
        self.cast_bar_mute.setIcon(cast_strip_icon(
            "muted" if muted else "volume", P["text"]))

    def _cast_tracks_menu(self) -> None:
        """Offer another audio track or subtitle while the cast runs.

        There is no switching a track in place - the stream is built again,
        from where the TV has got to, which is the difference between
        changing the subtitles and starting the film over.

        Every subtitle in the stream is offered. There used to be a libass
        question here, because a text subtitle had to be drawn into the
        picture; it now travels beside it as WebVTT, which any ffmpeg
        writes.
        """
        ctx = self._cast_ctx or {}
        tracks = ctx.get("tracks") or {}
        audio = tracks.get("audio") or []
        subs = tracks.get("subtitle") or []
        # Kept for the test that opens this menu: how many subtitles were
        # actually offered, which is the thing that used to be filtered.
        self._last_menu_subs = len(subs)
        menu = QMenu(self)
        menu.addAction(tr("cast_title"), self.manage_cast)
        is_airplay = getattr(self.cast, "is_airplay", None)
        airplay = bool(callable(is_airplay) and is_airplay(self._cast_device))
        if airplay or self._cast_plays_as_is(self._cast_device):
            # A TV or a Kodi plays the stream as it is. Tracks and the
            # picture setting are the converter's, and only a Chromecast is
            # fed through it. AirPlay's own choices are in Apple's menu, in
            # the panel.
            if airplay:
                # Closed, the AirPlay window (Apple's button) is one click
                # from here - the cast dialog closes as AirPlay starts.
                show = getattr(self, "_airplay_show_window", None)
                if callable(show):
                    menu.addAction(tr("airplay_show_window"), show)
                text = tr("airplay_menu_note")
            elif self._cast_is_kodi(self._cast_device):
                text = tr("cast_kodi_note")
            else:
                text = tr("cast_dlna_note")
            note = menu.addAction(text)
            note.setEnabled(False)
            menu.exec(self.cast_bar_tracks.mapToGlobal(
                self.cast_bar_tracks.rect().bottomLeft()))
            return
        menu.addSeparator()
        cur_a = (ctx.get("audio") or {}).get("index")
        cur_s = (ctx.get("subs") or {}).get("index")

        def entry(parent, label, checked, chooser):
            act = parent.addAction(label)
            act.setCheckable(True)
            act.setChecked(checked)
            act.triggered.connect(chooser)

        if audio:
            am = menu.addMenu(tr("cast_audio"))
            entry(am, tr("cast_track_default"), cur_a is None,
                  lambda: self._recast_with(None, ctx.get("subs")))
            for t in audio:
                entry(am, self._track_label(t), t["index"] == cur_a,
                      lambda _c=False, t=t: self._recast_with(t,
                                                              ctx.get("subs")))
        if subs:
            sm = menu.addMenu(tr("cast_subtitles"))
            entry(sm, tr("cast_subs_off"), cur_s is None,
                  lambda: self._recast_with(ctx.get("audio"), None))
            for t in subs:
                entry(sm, self._track_label(t), t["index"] == cur_s,
                      lambda _c=False, t=t: self._recast_with(ctx.get("audio"),
                                                              t))
        # The picture setting belongs here too. It is remembered per device,
        # so a channel that did need scaling leaves it set for everything
        # after it - and having to reopen the panel to put it back would be
        # the wrong place to keep it.
        current = self._cast_quality()
        # What the setting is doing to THIS stream. It is a ceiling, so an SD
        # or HD channel passes untouched while the ceiling still reads 720p -
        # and a menu that only shows the setting looks like it is scaling
        # everything.
        older = menu.addAction(
            tr("cast_older_device", name=self._cast_device or ""))
        older.setCheckable(True)
        older.setChecked(current != "original")
        older.triggered.connect(
            lambda on: self._set_cast_quality("older" if on else "original"))
        height = ctx.get("height") or 0
        fps = ctx.get("fps") or 0.0
        if height:
            from ..providers.chromecast import ChromecastManager as _CM
            adapted = _CM._needed_quality(current, height, fps) != "original"
            now = f"{height}p{fps:g}" if fps else f"{height}p"
            shown = menu.addAction(
                f"{now} → {_CM.quality_label(current, height, fps)}"
                if adapted else now)
            shown.setEnabled(False)
        menu.exec(self.cast_bar_tracks.mapToGlobal(
            self.cast_bar_tracks.rect().bottomLeft()))

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
        that moment, else within what the archive actually holds."""
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
        self.cast_bar_pause.setIcon(cast_strip_icon("pause", P["text"]))
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

    def _cast_go_live(self) -> None:
        """Back to the live edge, off the archive."""
        ctx, device = self._cast_ctx or {}, self._cast_device
        url = ctx.get("row_url")
        if not device or not url:
            return
        log.info("cast: back to the live edge")
        # The rewind buffer was all the cast had of the provider (not kept
        # beside it): its feed ends first, and the provider is given a
        # moment, before the TV asks for the channel - never two at once.
        feed = self._cast_feed()
        settle = bool(feed is not None and feed.source is not None
                      and ctx.get("buffer") is None and self.cast.bridged())
        self._cast_drop_feed()
        ctx.update(url=url, source=ctx.get("row_source") or url,
                   archive_from=None)
        self._cast_paused_at = None
        self._cast_behind = 0.0
        self.cast_bar_pause.setIcon(cast_strip_icon("pause", P["text"]))
        title = ctx.get("title") or "dopeIPTV"
        run_async(
            self.pool,
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
            lambda msg: self._error(tr("cast_failed", msg=msg)))

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

    def _cast_title_ended(self) -> bool:
        """An episode the receiver played to its end: its point and its
        scrobble end as at the player's end, and the next episode follows
        on the TV. True when that is under way."""
        ctx = self._cast_ctx or {}
        if (ctx.get("kind") != "episode" or ctx.get("_ended")
                or not self._cast_device
                or not str(getattr(self.cast, "state", "")
                           ).startswith("IDLE/FINISHED")
                or getattr(self, "_cast_zap_busy", False)):
            return False
        ctx["_ended"] = True
        log.info("cast: the episode played to its end on %s",
                 self._cast_device)
        self._save_cast_position()
        self._trakt_stop_current()
        self._simkl_stop_current()
        self._ms_leave(ended=True)
        if not (self._autoplay_next_episode() and self._has_next_episode()):
            return False
        return bool(self._advance_to_next_episode())

    def _cast_plays_as_is(self, name: str | None) -> bool:
        """A DLNA TV or a Kodi: it plays the stream as it is."""
        as_is = (getattr(self.cast, "plays_as_is", None)
                 or getattr(self.cast, "is_dlna", None))
        return bool(callable(as_is) and as_is(name))

    def _cast_is_kodi(self, name: str | None) -> bool:
        is_kodi = getattr(self.cast, "is_kodi", None)
        return bool(callable(is_kodi) and is_kodi(name))

    def _cast_ended_there(self) -> bool:
        """The cast ended on the device itself - a Kodi stopped with its own
        remote, or playing something else now: the strip comes down, and
        the device is left alone (a stop would end what it plays now)."""
        ended = getattr(self.cast, "ended_elsewhere", None)
        if not (self._cast_device and callable(ended) and ended()):
            return False
        log.info("cast: %s stopped by itself", self._cast_device)
        self._save_cast_position()
        self.show_cast_strip(None)
        return True

    def _show_cast_progress(self) -> None:
        """Put the receiver's own position on the strip.

        Hidden for a broadcast: there is no end to measure against, so a bar
        showing how far through it you are would be showing nothing.
        """
        bar = getattr(self, "cast_bar_seek", None)
        if bar is None:
            return
        if self._cast_ended_there():
            return
        ts = getattr(self, "cast_bar_ts", None)
        if ts is not None:
            # The rewind buffer can come and go while the cast runs (the
            # pool lets one go for space), and the menu goes with it.
            ts.setVisible(bool(self._cast_device) and self._cast_can_go_back())
        span = self._cast_timeline()
        dur = float(getattr(self.cast, "duration", 0.0) or 0.0)
        # A broadcast the converter is recording can be held even with no
        # catch-up behind it, and then there is no timeline to scrub - but
        # how far behind live it is still matters, and while it is paused
        # that counter is the only thing saying so.
        held = bool(self._cast_device) and dur <= 0 and self.cast.bridged()
        on = bool(self._cast_device) and (span is not None or dur > 0)
        bar.setVisible(on)
        self.cast_bar_time.setVisible(on or held)
        # The ticker runs for the whole of a cast, not only where there is a
        # bar to move: a timeshifted channel has no length, and is exactly
        # the thing that needs watching for the end of its playlist.
        if not self._cast_device:
            self._cast_tick.stop()
            bar.set_segments([])
            return
        if not self._cast_tick.isActive():
            self._cast_tick.start()
        self._show_cast_label()
        if self._cast_title_ended():
            return
        self._cast_continue_archive()
        if span is not None:
            self._show_cast_timeline(*span)
            return
        # Not a broadcast, so nothing on the groove. The programme blocks were
        # only ever cleared when the cast ENDED, so casting a film straight
        # after a timeshifted channel left last night's evening drawn along a
        # bar that is now measuring a film.
        bar.set_segments([])
        if held:
            self._show_cast_edge(self._cast_moment().timestamp(), time.time())
            return
        self.cast_bar_live.setVisible(False)
        if not on:
            return
        pos = min(float(self.cast.position() or 0.0), dur)
        # Not while it is being dragged: the handle belongs to the hand
        # holding it until it is let go.
        if not bar.isSliderDown():
            bar.setValue(int(pos / dur * 1000))
        self.cast_bar_time.setText(
            f"{self._fmt_hms(pos)} / {self._fmt_hms(dur)}")

    # How much of a channel's archive the strip's bar spans. The archive
    # itself can be a week deep, and a week across two hundred pixels is a
    # bar where every click is half an hour out. Longer jumps are what the
    # menu's "go back a day" is for; this is for finding your way around the
    # evening.
    CAST_TIMELINE_MIN = 360

    def _cast_timeline(self) -> tuple[float, float] | None:
        """The stretch of broadcast the bar spans, as (start, span) seconds -
        or None when what is playing is not a broadcast: the archive's depth
        and what the rewind buffer holds, whichever reaches further back."""
        ctx = self._cast_ctx or {}
        if not getattr(self, "_cast_device", None):
            return None
        now = time.time()
        widths = []
        if ctx.get("archive"):
            try:
                depth = self._effective_ts_minutes(ctx.get("item") or {}) or 0
            except Exception:
                depth = 0
            depth = min(depth, self.CAST_TIMELINE_MIN)
            if depth > 0:
                widths.append(depth * 60.0)
        held = (self._cast_buffer_span() if ctx.get("sid") is not None
                else None)
        if held is not None:
            widths.append(min(now - held[0], self.CAST_TIMELINE_MIN * 60.0))
        width = max(widths, default=0.0)
        if width < 1.0:
            return None
        return now - width, width

    def _cast_time_at(self, frac: float) -> str:
        """What is at *frac* of the way along the bar.

        A film says how far in it is. A broadcast says the time of day, and
        what was on then - which is the whole point of a timeshift bar, and
        is answered before you click rather than after.
        """
        span = self._cast_timeline()
        if span is not None:
            start, width = span
            when = start + frac * width
            label = time.strftime("%H:%M", time.localtime(when))
            prog = self._cast_programme_at(when)
            return f"{label} · {prog}" if prog else label
        dur = float(getattr(self.cast, "duration", 0.0) or 0.0)
        return self._fmt_hms(frac * dur) if dur > 0 else ""

    def _cast_programme_at(self, when: float) -> str:
        ctx = self._cast_ctx or {}
        item = ctx.get("item")
        if item is None:
            return ""
        for p in self.xmltv.programmes_in(item, when - 1, when + 1) or []:
            if p["start_timestamp"] <= when < p["stop_timestamp"]:
                return p.get("title") or ""
        return ""

    def _show_cast_timeline(self, start: float, span: float) -> None:
        """The archive, drawn as the evening it is.

        A broadcast has no length to run a position bar against, so the strip
        used to show nothing for the very channels that can be moved around
        in most freely. What it spans instead is time itself: programme
        boundaries along the groove, the moment being shown as the handle,
        and how far behind live that is - which is also what says, without a
        word, that a channel is paused.
        """
        at = self._cast_moment().timestamp()
        now = time.time()
        segs = []
        for p in self.xmltv.programmes_in(
                self._cast_ctx.get("item") or {}, start, now) or []:
            a = max(0.0, (p["start_timestamp"] - start) / span)
            b = min(1.0, (p["stop_timestamp"] - start) / span)
            label = "%s–%s" % (
                time.strftime("%H:%M", time.localtime(p["start_timestamp"])),
                time.strftime("%H:%M", time.localtime(p["stop_timestamp"])))
            segs.append((a, b, p.get("title") or "", label))
        self.cast_bar_seek.set_segments(segs)
        if not self.cast_bar_seek.dragging:
            frac = max(0.0, min(1.0, (at - start) / span))
            self.cast_bar_seek.setValue(int(frac * 1000))
        self._show_cast_edge(at, now)

    def _show_cast_edge(self, at: float, now: float) -> None:
        """How far behind the broadcast the picture is, in the words the
        player already uses for it: a white dot at the live edge, a red
        counter behind it, and the red button back.

        Anything else asked the user to learn a second vocabulary for the
        same thing - and a grey "LIVE" that stayed put whether or not it was
        true taught them nothing at all.
        """
        behind = max(0.0, now - at)
        paused = getattr(self, "_cast_paused_at", None) is not None
        live = not paused and behind < 5
        held = "⏸ " if paused else ""
        if live:
            edge = "● " + tr("mv_live")
        elif paused and behind < 1:
            edge = "⏸ " + tr("player_paused")
        elif behind < 60:
            edge = f"{held}−{int(behind)}s"
        else:
            edge = f"{held}−{self._fmt_hms(behind)}"
        prog = self._cast_programme_at(at)
        self.cast_bar_time.setText(f"{edge} · {prog}" if prog else edge)
        self.cast_bar_time.setStyleSheet(
            "font-size:11px; font-weight:700;" if live else
            f"font-size:11px; font-weight:700; color:{P['rec']};")
        self.cast_bar_live.setVisible(not live)

    def _cast_seek_released(self) -> None:
        span = self._cast_timeline()
        if span is not None:
            start, width = span
            at = start + self.cast_bar_seek.value() / 1000 * width
            self._cast_to_moment(datetime.fromtimestamp(at))
            return
        dur = float(getattr(self.cast, "duration", 0.0) or 0.0)
        if dur > 0:
            self._cast_seek(self.cast_bar_seek.value() / 1000 * dur)

    def _cast_seek(self, to: float) -> None:
        """Move to *to* seconds into what is playing on the TV.

        A file the receiver fetched itself it can seek on its own. What the
        converter serves it cannot: that is a pipe with no length and no
        index, so moving inside it means building the stream again from the
        new point - which is what the converter is already good at, and the
        same thing changing a subtitle does.
        """
        log.info("cast: moving to %s", self._fmt_hms(to))
        ctx = self._cast_ctx or {}
        # A playlist can be seeked where a single long response cannot: every
        # segment is still there and named, so the receiver jumps within it
        # by itself. Rebuilding the stream for that started the film over,
        # which is what dragging the bar appeared to do.
        # A playlist can be seeked, but only into what has been made. The
        # converter runs a little ahead of the picture and no further, so a
        # jump forward lands at the end of what exists - which on the
        # television looks like it moved on by a minute and stopped. Going
        # BACK is free, because those segments are all still there.
        playlist = getattr(getattr(self.cast, "bridge", None), "hls", False)
        if playlist and to > self.cast.position() + 5:
            playlist = False            # rebuild from there instead
        if self.cast.bridged() and not playlist:
            self._recast_with(ctx.get("audio"), ctx.get("subs"), start=to)
        else:
            threading.Thread(target=lambda: self.cast.seek(to),
                             daemon=True).start()

    def _cast_quality_key(self) -> str:
        return f"cast_quality_{self._cast_device or ''}"

    def _cast_quality(self) -> str:
        # Through the normaliser: a device set before the question became one
        # checkbox is still remembered as "720p30", which is no longer an
        # answer that exists.
        from ..providers.cast_bridge import normalise_quality
        return normalise_quality(
            str(self.settings.value(self._cast_quality_key(), "original")
                or "original"))

    def _set_cast_quality(self, key: str) -> None:
        """Change what this device is allowed, and show it straight away."""
        if key == self._cast_quality():
            return
        self.settings.setValue(self._cast_quality_key(), key)
        log.info("cast: %s is now set to %s", self._cast_device, key)
        ctx = self._cast_ctx or {}
        self._recast_with(ctx.get("audio"), ctx.get("subs"))

    @staticmethod
    def _track_label(t: dict) -> str:
        bits = [b for b in (t.get("lang"), t.get("title")) if b]
        bits.append(t.get("codec") or "?")
        return " · ".join(bits)

    def _recast_with(self, audio, subs, start: float | None = None) -> None:
        """Cast the same title again with different tracks, from where the TV
        is now - or from *start*, when it is being moved somewhere else."""
        ctx, device = self._cast_ctx or {}, self._cast_device
        url = ctx.get("url")
        if not device or not url:
            return
        at = self.cast.position() if start is None else start
        ctx.update(audio=audio, subs=subs)
        if self._cast_feed() is not None:
            # The pieces again, from the moment on the TV, with the track.
            self._cast_from_moment(self._cast_moment())
            return
        log.info("cast: switching tracks and picking up at %d s", at)
        title = ctx.get("title") or "dopeIPTV"
        run_async(
            self.pool,
            lambda: self.cast.cast(device, url, title, self._local_codecs(),
                                   audio, subs, at,
                                   ctx.get("duration") or 0.0,
                                   False, ctx.get("source"),
                                   self._cast_quality(),
                                   ctx.get("height") or 0,
                                   ctx.get("fps") or 0.0,
                                   # A channel with an archive stays
                                   # recorded here. Changing the audio track
                                   # is no reason to lose the pause button.
                                   dvr=bool(ctx.get("archive"))),
            lambda _n: self.show_cast_strip(device, title),
            lambda msg: self._error(tr("cast_failed", msg=msg)))

    # How close to live the archive can be read from. Only the timeshift
    # menu comes through here now - going back an hour, picking a programme
    # out of the guide - and the panel cannot serve the minute it is still
    # writing.
    ARCHIVE_LAG = timedelta(seconds=75)

    def _toggle_cast_pause(self) -> None:
        """Hold the picture on the TV, and let it go again.

        One mechanism, for a film and for a broadcast alike: the receiver
        stops, and whatever is feeding it waits. A film the receiver fetched
        itself holds its own place. A broadcast is coming through the
        converter, which goes on recording into its spool while the
        television sits still - so play carries on at the very next frame.

        Nothing is asked of the provider either way. Buying the missing
        minutes back from its catch-up afterwards is what eight rounds of
        this foundered on, and the button is not offered at all where that
        would be the only way to answer it.
        """
        if self._cast_paused_at is not None:
            # However long that was, the picture is now that much further
            # behind the broadcast, and stays there.
            self._cast_behind = getattr(self, "_cast_behind", 0.0) + (
                datetime.now() - self._cast_paused_at).total_seconds()
            self._cast_paused_at = None
            self.cast_bar_pause.setIcon(cast_strip_icon("pause", P["text"]))
            threading.Thread(target=self.cast.resume, daemon=True).start()
            return
        self._cast_paused_at = datetime.now()
        self.cast_bar_pause.setIcon(cast_strip_icon("play", P["text"]))
        # However much room the user has allowed for it.
        try:
            gb = float(self.settings.value("cast_pause_gb", 4.5) or 4.5)
        except (TypeError, ValueError):
            gb = 4.5
        self.cast.bridge.cap = int(max(0.1, gb) * 10**9)
        threading.Thread(target=self.cast.pause, daemon=True).start()

    def _cast_from_moment(self, when) -> None:
        """Cast this channel from *when*: out of the rewind buffer - the one
        a feed reads, or the one kept for the cast - while it still holds
        that moment, else out of the provider's archive."""
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
        log.info("cast: %s from %s, in pieces as one playlist",
                 for_log(title),
                 datetime.fromtimestamp(feed.first_wall).strftime("%H:%M:%S"))
        run_async(
            self.pool,
            lambda: self.cast.cast(device, ctx.get("url") or "", title,
                                   self._local_codecs(), ctx.get("audio"),
                                   None, start=0.0, source=ctx.get("source"),
                                   quality=self._cast_quality(),
                                   height=ctx.get("height") or 0,
                                   fps=ctx.get("fps") or 0.0, dvr=True,
                                   feed=feed),
            lambda _n: self.show_cast_strip(device, title),
            lambda msg: self._error(tr("cast_failed", msg=msg)))

    def _cast_from_archive(self, paused_at) -> None:
        """Cast this channel from a moment in the provider's catch-up.

        This is the timeshift menu's doing - going back an hour, picking a
        programme out of the guide. Pausing does not come through here: it is
        held on this machine, which is the only way it ever worked.
        """
        ctx, device = self._cast_ctx or {}, self._cast_device
        sid = ctx.get("sid")
        if sid is None or not device:
            return
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
        run_async(
            self.pool,
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
            lambda msg: self._error(tr("cast_failed", msg=msg)))

    def _save_cast_position(self) -> None:
        """Keep the point the TV reached, so it resumes there next time.

        The receiver is the only thing that knows where the film has got to,
        and it stops knowing the moment the cast ends - so this has to happen
        before anything is torn down.
        """
        ctx = getattr(self, "_cast_ctx", None) or {}
        group, key = ctx.get("group"), ctx.get("key")
        if not group or key is None:
            return
        pos, dur = self.cast.position(), self.cast.duration
        # Under a minute in, the store treats a position as "at the start" and
        # DROPS whatever was saved before. A cast that had barely begun would
        # therefore wipe the resume point the film already had - so a position
        # too small to be worth keeping is a reason to leave the store alone,
        # not to write to it.
        if pos <= 60 or dur <= 0:
            return
        if privacy.playback_private(ctx.get("kind") or "", key):
            return      # private at some point: private to its end (core/privacy)
        log.info("cast: keeping the position, %d s into %d", pos, dur)
        store = ctx.get("resume")
        if store is None:
            store = self.resume
        store.record(group, key, pos, dur, item=ctx.get("item"))

    def _end_cast(self, why: str) -> None:
        """Stop a running cast and take the strip down.

        Stopping talks to the receiver over the network, so it runs off the UI
        thread: nothing here - least of all starting playback - may wait for a
        TV to answer.
        """
        cc = getattr(self, "cast", None)
        if cc is not None and getattr(cc, "active", None) is not None:
            self._save_cast_position()
        self.show_cast_strip(None)
        if cc is None or getattr(cc, "active", None) is None:
            return
        log.info("cast: stopping - %s", why)
        threading.Thread(target=cc.stop, daemon=True).start()

    def add_kodi(self, parent, current: str | None = None) -> str | None:
        """The cast panel's "Add Kodi…": a Kodi's address and its web
        server's login, checked before it is kept. Opened on a listed Kodi,
        it changes that one (or forgets it). Returns the name it now has in
        the device list, "" when it was forgotten, None when nothing changed."""
        kodi = getattr(self.cast, "kodi", None)
        if kodi is None:
            return None
        d = _KodiAddDialog(parent, kodi, current, self.pool)
        if d.exec() != QDialog.DialogCode.Accepted:
            return None
        return d.result_label

    def add_cast_device(self, parent, current: str | None = None) -> str | None:
        """The cast panel's "Add a device by address…": a Chromecast, a TV
        or a Kodi the search does not reach (another part of a network split
        in VLANs), found out at its address before it is kept. Opened on one
        added that way, it can forget it. The name it has in the device list,
        "" when it was forgotten, None when nothing changed."""
        hub = getattr(self, "cast", None)
        if hub is None or not callable(getattr(hub, "add_by_address", None)):
            return None
        d = _AddressDialog(parent, hub, current, self.pool)
        if d.exec() != QDialog.DialogCode.Accepted:
            return None
        return d.result_label

    def _cast_added_card(self, c, nerd_link) -> None:
        """Settings > Casting: the devices added by address - Chromecasts
        and TVs (cast_added_devices) and Kodis (kodi_devices) - each with
        Remove; and, while Nerd is on, the way to the other networks it
        searches. Adding is in the cast window, where the device is tried."""
        from ..providers import kodi as kodi_mod
        hub = getattr(self, "cast", None)
        kinds = {"chromecast": "Chromecast", "dlna": tr("cast_addr_kind_dlna")}
        rows: list[tuple[str, str, str, str]] = []
        stored = getattr(hub, "stored", None)
        for d in (stored() if callable(stored) else []):
            rows.append((d.name, f"{kinds.get(d.kind, d.kind)} · {d.address}", "cast", d.key))
        for k in kodi_mod.load_devices(self.settings):
            rows.append((k.name, f"Kodi · {k.address}", "kodi", k.address))
        gone: set[str] = set()
        none = c.note(tr("set_cast_added_none"))
        none_row = c.rows[-1][0]
        c.shown_if(none_row, lambda: len(gone) == len(rows))

        def forget(kind: str, key: str) -> None:
            if kind == "cast" and hub is not None:
                hub.forget_added(key)
            elif kind == "kodi":
                mgr = getattr(hub, "kodi", None)
                label = next((n for n, d in (getattr(mgr, "devices", None) or {}).items()
                              if d.address == key), None)
                if mgr is not None and label is not None:
                    mgr.forget(label)
                else:
                    kodi_mod.save_devices(self.settings, [
                        d for d in kodi_mod.load_devices(self.settings) if d.address != key])
            gone.add(kind + ":" + key)
            c.refresh()
        for name, hint, kind, key in rows:
            btn = QPushButton(tr("cast_addr_remove"))
            btn.clicked.connect(lambda _c=False, kind=kind, key=key: forget(kind, key))
            row = c.row(name, btn, hint, search=f"{hint} {tr('cast_add_by_address')}")
            c.shown_if(row, lambda kind=kind, key=key: kind + ":" + key not in gone)
        none.setWordWrap(True)
        nerd_link(c, "set_link_cast_net", [tr("nerd_card_lan")], "net")

    def stop_local_playback_for_cast(self) -> bool:
        """Free the local stream when a cast starts. The Chromecast pulls the
        URL itself (one connection from the device), so on a single-connection
        account leaving the embedded player running too would be a second
        connection the provider refuses. Called by the cast dialog on a
        successful cast. Returns whether anything was actually stopped, so
        the caller can let the provider notice before asking for the stream
        again - these panels keep counting a session for a few seconds after
        the socket closes."""
        p = getattr(self, "player", None)
        if p is not None and getattr(p, "current_url", None):
            try:
                p.stop()
                return True
            except Exception:
                pass
        return False


class _KodiAddDialog(QDialog):
    """A Kodi by its address: the web server's port (8080 unless changed in
    Kodi) and, when it has one, its login. Kept only once Kodi answers."""

    def __init__(self, parent, kodi, current: str | None, pool) -> None:
        super().__init__(parent)
        self.kodi = kodi
        self.pool = pool
        self.current = current
        self.result_label: str | None = None
        self._checking = False
        dev = kodi.device(current) if current else None
        self.setWindowTitle(tr("kodi_add_title"))
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(10)
        intro = QLabel(tr("kodi_add_intro"))
        intro.setWordWrap(True)
        intro.setObjectName("DetailMeta")
        lay.addWidget(intro)
        form = QFormLayout()
        self.address = QLineEdit(
            "" if dev is None else (dev.address if dev.port != DEFAULT_PORT
                                    else dev.host))
        self.address.setPlaceholderText(tr("kodi_address_ph"))
        self.user = QLineEdit("" if dev is None else dev.user)
        self.user.setPlaceholderText("kodi")
        self.password = QLineEdit("" if dev is None else dev.password)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(tr("kodi_address"), self.address)
        form.addRow(tr("login_username"), self.user)
        form.addRow(tr("login_password"), self.password)
        lay.addLayout(form)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.hide()
        lay.addWidget(self.status)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        self.ok_btn = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_btn.setText(tr("common_save") if dev is not None else tr("btn_connect"))
        self.forget_btn = None
        if dev is not None and getattr(dev, "manual", False):
            self.forget_btn = QPushButton(tr("kodi_forget"))
            self.buttons.addButton(self.forget_btn,
                                   QDialogButtonBox.ButtonRole.DestructiveRole)
            self.forget_btn.clicked.connect(self._forget)
        self.buttons.accepted.connect(self._check)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)

    def _say(self, text: str, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(
            f"color:{P['error'] if error else P['muted']}; font-size:12px;")
        if self.status.isHidden():
            self.status.show()
            # Room for the line: the fields are not squeezed to make it.
            self.adjustSize()

    def _forget(self) -> None:
        self.kodi.forget(self.current)
        self.result_label = ""
        self.accept()

    def _check(self) -> None:
        if self._checking:
            return
        where = parse_address(self.address.text())
        if where is None:
            self._say(tr("kodi_bad_address"), error=True)
            return
        host, port = where
        user, password = self.user.text().strip(), self.password.text()
        self._checking = True
        self.ok_btn.setEnabled(False)
        self._say(tr("kodi_checking", address=f"{host}:{port}"))
        run_async(self.pool, self.kodi.add, self._added, self._failed,
                  host, port, user, password)

    def _alive(self) -> bool:
        return not sip.isdeleted(self) and self.isVisible()

    def _added(self, label: str) -> None:
        if not self._alive():
            return
        self._checking = False
        self.result_label = label
        self.accept()

    def _failed(self, msg: str) -> None:
        if not self._alive():
            return
        self._checking = False
        self.ok_btn.setEnabled(True)
        self._say(msg, error=True)


class _AddressDialog(QDialog):
    """A device by its address: what kind it is found out (or the one
    chosen), and it is kept only once it answers as that."""

    KINDS = (("auto", "cast_addr_kind_auto"), ("chromecast", "cast_addr_kind_cc"),
             ("dlna", "cast_addr_kind_dlna"), ("kodi", "cast_addr_kind_kodi"))

    def __init__(self, parent, hub, current: str | None, pool) -> None:
        super().__init__(parent)
        self.hub = hub
        self.pool = pool
        self.current = current
        self.result_label: str | None = None
        self._checking = False
        self.setWindowTitle(tr("cast_addr_title"))
        self.setMinimumWidth(440)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(10)
        intro = QLabel(tr("cast_addr_intro"))
        intro.setWordWrap(True)
        intro.setObjectName("DetailMeta")
        lay.addWidget(intro)
        form = QFormLayout()
        self.kind = QComboBox()
        for key, label in self.KINDS:
            self.kind.addItem(tr(label), key)
        self.address = QLineEdit("")
        self.address.setPlaceholderText(tr("cast_addr_ph"))
        form.addRow(tr("cast_addr_kind"), self.kind)
        form.addRow(tr("kodi_address"), self.address)
        lay.addLayout(form)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.hide()
        lay.addWidget(self.status)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        self.ok_btn = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_btn.setText(tr("cast_addr_add"))
        self.forget_btn = None
        if current:
            self.forget_btn = QPushButton(tr("cast_addr_forget"))
            self.buttons.addButton(self.forget_btn,
                                   QDialogButtonBox.ButtonRole.DestructiveRole)
            self.forget_btn.clicked.connect(self._forget)
        self.buttons.accepted.connect(self._check)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)

    def _say(self, text: str, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(
            f"color:{P['error'] if error else P['muted']}; font-size:12px;")
        if self.status.isHidden():
            self.status.show()
            self.adjustSize()

    def _forget(self) -> None:
        key = self.hub.added_key(self.current)
        if key:
            self.hub.forget_added(key)
        self.result_label = ""
        self.accept()

    def _check(self) -> None:
        if self._checking:
            return
        from ..providers.cast_devices import parse_address
        text = self.address.text().strip()
        if parse_address(text) is None:
            self._say(tr("cast_addr_bad"), error=True)
            return
        self._checking = True
        self.ok_btn.setEnabled(False)
        self._say(tr("cast_addr_checking", address=text))
        run_async(self.pool, self.hub.add_by_address, self._added, self._failed,
                  text, self.kind.currentData() or "auto")

    def _alive(self) -> bool:
        return not sip.isdeleted(self) and self.isVisible()

    def _added(self, label: str) -> None:
        if not self._alive():
            return
        self._checking = False
        self.result_label = label
        self.accept()

    def _failed(self, msg: str) -> None:
        if not self._alive():
            return
        self._checking = False
        self.ok_btn.setEnabled(True)
        self._say(msg, error=True)
