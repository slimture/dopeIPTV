"""MainWindow mixin: casting - a Chromecast or a DLNA TV plays what the
window would.

The cast dialog; the strip under the list while a cast runs (pause, volume,
tracks, quality, the timeshift menu and the progress timeline); a live
channel handed over from the provider's archive at the moment it was
paused; and the position a film reached on the TV, kept for resume. The
devices themselves are providers/casting.py's CastHub. Kept out of
main_window.py to keep that file lean.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

from PyQt6.QtWidgets import QMenu

from ..core.log import log
from ..core.workers import run_async
from ..i18n import tr
from ..providers.chromecast import CastDialog
from .theme import P
from .widgets import cast_strip_icon


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
        row_kind = it.get("_kind")
        if row_kind:
            live = row_kind == "live"
        else:
            live = (self._content_kind() in ("live", "fav")
                    or (self.mode == "fav" and self._fav_section == "chan"))
        # Two addresses for the same channel. HLS is the one a Chromecast can
        # take directly; the plain stream is the one the player is watching -
        # and some channels are not served as HLS at all, which is a 4XX to
        # everything that asks. The converter reads that one, since ffmpeg has
        # no trouble with a transport stream and the receiver never sees it.
        source = url
        sid = it.get("stream_id")
        if live and sid is not None:
            url = self.client.live_url(sid, "m3u8")
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
        self._cast_ctx = {
            "sid": it.get("stream_id") if live else None,
            "archive": bool(live and it.get("stream_id") is not None
                            and it.get("tv_archive")),
            "title": title,
            "group": self._RESUME_GROUP.get(rkind or ""),
            "key": key,
            "item": it,
            # For History: the row's own kind and address, kept apart from
            # the address currently on the receiver, which an archive resume
            # or a track change replaces.
            "kind": self._play_kind_for(it),
            "row_url": url,
            "row_source": source,
        }
        CastDialog(self, url, title, self._local_codecs(),
                   self._local_audio_index(), start,
                   self._local_tracks(it),
                   probe=not self._busy_elsewhere(it), source=source,
                   live=self._play_kind_for(it) == "live").exec()

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

    def show_cast_strip(self, device: str | None, title: str = "") -> None:
        """Show - or take down - the "Casting to X" strip above the player.

        Casting stops local playback (the receiver pulls the stream itself),
        so the player pane goes black and without this nothing anywhere says
        that anything is happening at all. The cast dialog calls it when a
        cast starts, fails or is stopped.
        """
        self._cast_device = device
        ctx = getattr(self, "_cast_ctx", None) or {}
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
        self.cast_bar_ts.setVisible(bool(device) and bool(ctx.get("archive")))
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
        self.cast_bar_lbl.setText(tr("cast_casting_to", name=device))
        self.cast_bar_title.setText(title or "")
        self.cast_bar_title.setVisible(bool(title))
        self._show_cast_volume()
        bar.show()

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
        self.history.add(url, ctx.get("title") or "",
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
        CastDialog(self, ctx.get("url") or "", ctx.get("title") or "",
                   self._local_codecs(), 0, self.cast.position(),
                   ctx.get("tracks") or {}, probe=False,
                   source=ctx.get("source"), managing=True,
                   chosen=(ctx.get("audio"), ctx.get("subs")),
                   live=ctx.get("kind") == "live").exec()

    def _cast_volume(self, level: float) -> None:
        """Set the TV's volume. Off the UI thread: it is a message to a device
        on the network, and nothing here should wait for it."""
        if getattr(self.cast, "active", None) is None:
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
        if getattr(self.cast, "active", None) is None:
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
        television actually is rather than where the app guessed."""
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
        if self.cast.is_dlna(self._cast_device):
            # A TV plays the stream as it is. Tracks and the picture setting
            # are the converter's, and only a Chromecast is fed through it.
            note = menu.addAction(tr("cast_dlna_note"))
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
        """The archive, for what is playing on the TV.

        The player's own timeshift menu, pointed at the receiver instead of at
        mpv: go back to live, start the programme that is on from its
        beginning, browse what has been, or simply wind back a while. The
        archive does not care who is watching it.
        """
        ctx = self._cast_ctx or {}
        it = ctx.get("item")
        if not ctx.get("archive") or not it:
            return
        m = QMenu(self)
        m.addAction(tr("ts_go_live"), self._cast_go_live)
        m.addSeparator()
        # Where the picture is now, so a step back is a step back from THERE
        # and not from live - winding back twice has to go twice as far.
        at = self._cast_moment()
        prog = self.xmltv.current_programme(it)
        if prog:
            m.addAction(
                tr("ts_watch_from_start_named", title=prog["title"]),
                lambda: self._cast_to_moment(
                    datetime.fromtimestamp(prog["start_timestamp"])))
        m.addAction(tr("ts_browse_past"),
                    lambda: self._open_catchup_dialog(
                        it, on_pick=lambda p: self._cast_to_moment(
                            datetime.fromtimestamp(p["start_timestamp"]))))
        m.addSeparator()
        eff_min = self._effective_ts_minutes(it)
        for mins, dur_key in self.TIMESHIFT_STEPS:
            if mins > eff_min:
                break
            m.addAction(tr("ts_go_back", t=tr(dur_key)),
                        lambda mins=mins: self._cast_to_moment(
                            at - timedelta(minutes=mins)))
        note = m.addAction(tr("ts_archive_depth", n=self._timeshift_days(it)))
        note.setEnabled(False)
        m.exec(self.cast_bar_ts.mapToGlobal(
            self.cast_bar_ts.rect().bottomLeft()))

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
        """Point the cast at *when*, within what the archive actually holds."""
        ctx = self._cast_ctx or {}
        it = ctx.get("item") or {}
        now = datetime.now()
        floor = now - timedelta(minutes=self._effective_ts_minutes(it))
        # A minute inside the live edge: the archive is written as it goes and
        # the newest minute is not there yet.
        when = max(floor, min(when, now - timedelta(minutes=1)))
        self._cast_paused_at = None
        self.cast_bar_pause.setIcon(cast_strip_icon("pause", P["text"]))
        self._cast_from_archive(when)

    def _cast_go_live(self) -> None:
        """Back to the live edge, off the archive."""
        ctx, device = self._cast_ctx or {}, self._cast_device
        url = ctx.get("row_url")
        if not device or not url:
            return
        log.info("cast: back to the live edge")
        ctx.update(url=url, source=ctx.get("row_source") or url,
                   archive_from=None)
        self._cast_paused_at = None
        self._cast_behind = 0.0
        self.cast_bar_pause.setIcon(cast_strip_icon("pause", P["text"]))
        title = ctx.get("title") or "dopeIPTV"
        run_async(
            self.pool,
            lambda: self.cast.cast(device, url, title, self._local_codecs(),
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
        self._cast_from_archive(at)

    def _show_cast_progress(self) -> None:
        """Put the receiver's own position on the strip.

        Hidden for a broadcast: there is no end to measure against, so a bar
        showing how far through it you are would be showing nothing.
        """
        bar = getattr(self, "cast_bar_seek", None)
        if bar is None:
            return
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
        or None when what is playing is not a broadcast."""
        ctx = self._cast_ctx or {}
        if not ctx.get("archive") or not self._cast_device:
            return None
        try:
            depth = self._effective_ts_minutes(ctx.get("item") or {}) or 0
        except Exception:
            return None
        depth = min(depth, self.CAST_TIMELINE_MIN)
        if depth <= 0:
            return None
        now = time.time()
        return now - depth * 60, depth * 60.0

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
                 ctx.get("title") or "", paused_at.strftime("%H:%M:%S"),
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
        log.info("cast: keeping the position, %d s into %d", pos, dur)
        self.resume.record(group, key, pos, dur, item=ctx.get("item"))

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
