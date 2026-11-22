"""MainWindow mixin: the cast card's content and state, and a stuck cast
got going again.

The card (ui/cast_card.py) stands in the video's place while a cast runs;
ui/mw_cast.py shows it and drives its controls. Here:

- what it shows (``_cast_card_fill``): the picture of what plays - one the
  window's image loaders hold in memory, or have on disk (decoded on their
  pool), never a download for the card -, the title, what is on now on a
  channel or which episode of which series, the TV's name with its mark;
- its state, once a second with the cast's tick (``_cast_card_tick``): the
  receiver's own ("PLAYING/None", "BUFFERING/NONE", "WAITING/ROUTE"...)
  read by core/cast_watch.py's ``phase``, the person's pause, an Apple TV
  still to be picked;
- the watchdog (core/cast_watch.Watchdog): a TV buffering ten seconds that
  nobody paused is sent the stream again - the channel at the live edge or
  where the rewind buffer or the archive had it, a title from where it got
  to - at most twice, then the card says it gave up with Try again, and a
  message says so. Every stall, try and outcome is in the log. A try never
  opens a second connection: it goes the way the controls already go (the
  receiver's own cast call, ``settle``: the converter feeding the TV lets
  go of the provider first, and only one call is ever on its way -
  ``_cast_send``), and where the account's connections are taken by
  something else it is not sent by itself (Try again asks first, as a cast
  does: mw_connections);
- the skips (``_cast_skip``): 30 s in a title; on a channel only where going
  back works (its archive, or the rewind buffer kept beside the cast);
- what plays marked everywhere (``_cast_marks_changed``): every place asks
  ``_now_playing`` (mw_pane_follow), and a pill says "On TV" while it plays
  there.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from typing import Any

from PyQt6 import sip

from ..core import cast_watch, film_view as fv, series_view as sv
from ..core.log import log
from ..core.privacy import for_log
from ..i18n import tr
from . import cast_card, media_cards


class _CastWatchMixin:
    """MainWindow mixin: the cast card's content, its state, the watchdog."""

    #: A skip on the card, in seconds.
    CAST_SKIP_S = 30
    #: A seek has reached the TV once it has played this long (a moment of
    #: the old stream's Playing right after the send is not it).
    CAST_MOVED_S = 2.0
    #: A seek said on the card at most this long; the watchdog has it after.
    CAST_MOVING_MAX_S = 120.0

    def _cast_moving(self, ctx: dict, to) -> None:
        """A seek on its way to the TV - to a moment (seconds since the
        epoch) or to live ("live"): the card says so until the TV plays it."""
        ctx["_moving"], ctx["_moving_at"] = to, time.monotonic()
        self._cast_watch().new_stream()         # what plays now is not it
        self._cast_card_state()

    # -- what the card shows ------------------------------------------------------------

    def _cast_card_fill(self, title: str = "") -> None:
        """The card's picture and words for the cast now."""
        card = getattr(self, "cast_bar", None)
        if not isinstance(card, cast_card.CastCard):
            return
        ctx = getattr(self, "_cast_ctx", None) or {}
        name, line = self._cast_card_words(ctx, title)
        device = str(getattr(self, "_cast_device", None) or "")
        where = getattr(getattr(self, "cast", None), "where", None)
        card.set_media(name, line, device, self._cast_on_airplay(),
                       where=str(where(device) or "") if callable(where) and device else "")
        pm, shape = self._cast_card_picture(ctx)
        card.set_art(pm, shape, name)
        self._cast_card_line_at = time.monotonic()

    def _cast_card_words(self, ctx: dict, title: str = "") -> tuple[str, str]:
        """(title, line) of what the TV plays: a channel and what is on; a
        series and "S2 E5 · Title"; a film and its year and length."""
        kind = str(ctx.get("kind") or "")
        item = ctx.get("item") or {}
        title = str(title or ctx.get("title") or "")
        try:
            if ctx.get("sid") is not None:
                name = self.channel_display_name(item) if item else title
                return name or title, self._cast_card_on_now(item)
            if kind == "episode":
                series = ctx.get("series_ctx") or {}
                sname = str(series.get("name") or series.get("title") or "")
                sid = series.get("series_id")
                if sid is not None and sname:
                    sname = self.channel_ov.display_name("series", sid, sname)
                s, e = sv.season_of(item.get("season")), sv.episode_number(item)
                se = (tr("sp_se", s=s, e=e) if s is not None and e is not None
                      else tr("sp_episode_short", n=e) if e is not None else "")
                ep = sv.episode_title(item, sname)
                line = f"{se} · {ep}" if se and ep else se or ep
                return (sname or title), (line if sname else "")
            if kind == "movie":
                from .series_cards import minutes_text
                dur = float(getattr(self.cast, "duration", 0.0) or 0.0)
                mins = fv.minutes_of(item, dur_secs=dur)
                line = " · ".join(t for t in (fv.year_of(item), minutes_text(mins) if mins else "")
                                  if t)
                return title, line
            if ctx.get("group") == "local":
                path = str(item.get("_path") or ctx.get("row_url") or "")
                return title, os.path.basename(os.path.dirname(path)) if path else ""
        except Exception as e:
            log.debug("cast card: its words were not worked out: %s", e)
        return title, ""

    def _cast_card_on_now(self, item: dict) -> str:
        """What is on now on the channel cast, with its times."""
        guide = getattr(self, "xmltv", None)
        if guide is None or not item:
            return ""
        try:
            prog = guide.current_programme(item)
        except Exception:
            return ""
        if not prog or not prog.get("title"):
            return ""
        a, b = prog.get("start_timestamp"), prog.get("stop_timestamp")
        if a and b:
            span = "%s–%s" % (time.strftime("%H:%M", time.localtime(a)),
                              time.strftime("%H:%M", time.localtime(b)))
            return f"{prog['title']} · {span}"
        return str(prog["title"])

    def _cast_card_picture(self, ctx: dict) -> tuple[Any, str]:
        """(the picture, its shape): a film's or a series' poster, a
        channel's or a station's logo - one the loaders hold in memory; one
        they have on disk is decoded on their pool and shown when it is
        (never fetched for the card)."""
        kind = str(ctx.get("kind") or "")
        item = ctx.get("item") or {}
        poster = kind in ("movie", "episode")
        urls: list[str] = []
        try:
            if kind == "movie":
                urls.append(str(self.cover.cover_url(item, "vod") or ""))
            elif kind == "episode":
                series = ctx.get("series_ctx") or {}
                urls += [str(series.get("cover") or ""), str(series.get("stream_icon") or "")]
                try:
                    urls.append(str(self.cover.cover_url(series, "series") or ""))
                except Exception:
                    pass
            urls += [str(item.get("stream_icon") or ""), str(item.get("cover") or ""),
                     str(item.get("favicon") or "")]
            last = getattr(self, "_last_playback", None) or {}
            if last.get("key") == ctx.get("key"):
                urls.append(str(last.get("icon_url") or ""))
        except Exception as e:
            log.debug("cast card: no picture asked: %s", e)
        urls = [u for u in dict.fromkeys(urls) if u]
        loaders = [x for x in (getattr(self, "poster_art", None), getattr(self, "logos", None))
                   if x is not None]
        for url in urls:
            for loader in loaders:
                pm = loader.cache.get(url)
                if pm is not None and not pm.isNull():
                    return pm, "poster" if poster else "logo"
        for url in urls:
            for loader in loaders:
                disk = getattr(loader, "disk_file", None)
                if url.startswith("/") or (callable(disk) and disk(url) is not None):
                    key = ctx.get("key")
                    loader.get(url, lambda _pm, key=key: self._cast_card_art_in(key))
                    return None, "poster" if poster else "logo"
        return None, "poster" if poster else "logo"

    def _cast_card_art_in(self, key) -> None:
        ctx = getattr(self, "_cast_ctx", None) or {}
        if getattr(self, "_cast_device", None) and ctx.get("key") == key:
            self._cast_card_fill(str(ctx.get("title") or ""))

    # -- once a second ----------------------------------------------------------------

    def _cast_watch(self) -> cast_watch.Watchdog:
        """This cast's watchdog (a new title or channel brings a new one)."""
        ctx = getattr(self, "_cast_ctx", None)
        if ctx is None:
            ctx = self._cast_ctx = {}
        watch = ctx.get("_watch")
        if not isinstance(watch, cast_watch.Watchdog):
            watch = ctx["_watch"] = cast_watch.Watchdog()
        return watch

    def _cast_busy(self) -> bool:
        """A stream is on its way to the receiver (a zap, a recast)."""
        return bool(getattr(self, "_cast_zap_busy", False)
                    or getattr(self, "_cast_inflight", 0) > 0)

    def _cast_preparing(self) -> bool:
        preparing = getattr(getattr(self, "cast", None), "preparing", None)
        try:
            return bool(callable(preparing) and preparing())
        except Exception:
            return False

    def _cast_waiting_route(self) -> bool:
        waiting = getattr(getattr(self, "cast", None), "waiting_for_route", None)
        return bool(self._cast_on_airplay() and callable(waiting) and waiting())

    def _cast_card_tick(self) -> None:
        """The cast's tick, after the position: the watchdog asked, the
        card's state, the skips and which rows show."""
        if not getattr(self, "_cast_device", None):
            return
        watch = self._cast_watch()
        raw = str(getattr(getattr(self, "cast", None), "state", "") or "")
        ph = cast_watch.phase(raw)
        held = getattr(self, "_cast_paused_at", None) is not None or self._cast_waiting_route()
        what = watch.tick(time.monotonic(), ph, held=held, busy=self._cast_busy())
        self._cast_watch_said(what, watch, raw)
        if what == cast_watch.RECOVER:
            self._cast_recover(watch)
        elif what == cast_watch.FAIL:
            self._cast_gave_up(tr("cast_note_failed"))
        ctx = getattr(self, "_cast_ctx", None) or {}
        if ctx.get("sid") is not None and time.monotonic() - getattr(
                self, "_cast_card_line_at", 0.0) > 30:
            self._cast_card_fill(str(ctx.get("title") or ""))   # what is on now moved on
        self._cast_card_state()
        self._cast_skips_shown()
        card = getattr(self, "cast_bar", None)
        if isinstance(card, cast_card.CastCard):
            card.sync()

    def _cast_watch_said(self, what: str, watch: cast_watch.Watchdog, raw: str) -> None:
        """Every stall, try and outcome in the log."""
        if not what:
            return
        device = getattr(self, "_cast_device", None) or "?"
        title = for_log(str((getattr(self, "_cast_ctx", None) or {}).get("title") or ""))
        if what == cast_watch.BEGAN:
            log.info("cast: %s is %s - watching it (%s)", device,
                     "buffering" if cast_watch.phase(raw) == cast_watch.BUFFERING
                     else "not playing", raw or "no state yet")
        elif what == cast_watch.RESOLVED:
            log.info("cast: %s plays again by itself after %.0f s", device, watch.last_stall)
        elif what == cast_watch.RECOVER:
            log.info("cast: %s stuck for %.0f s (%s) - sending %s again, try %d of %d",
                     device, watch.last_stall, raw or "no state", title, watch.tries,
                     watch.tries_max)
        elif what == cast_watch.RECOVERED:
            log.info("cast: %s plays again after it was sent again - recovered", device)
        elif what == cast_watch.FAIL:
            log.info("cast: %s still stuck after %d tries (%s) - given up; Try again is "
                     "on the card", device, watch.tries_max, raw or "no state")

    def _cast_card_state(self) -> None:
        """The pill, from the watchdog, the person's pause and the receiver."""
        card = getattr(self, "cast_bar", None)
        if not isinstance(card, cast_card.CastCard) or not getattr(self, "_cast_device", None):
            return
        watch = self._cast_watch()
        ctx = getattr(self, "_cast_ctx", None) or {}
        ph = cast_watch.phase(getattr(getattr(self, "cast", None), "state", ""))
        now = time.monotonic()
        moving = ctx.get("_moving")
        if moving is not None and not watch.failed and not watch.recovering() \
                and not self._cast_waiting_route():
            there = (not self._cast_busy() and ph == cast_watch.PLAYING and watch.started
                     and watch.playing_since is not None
                     and now - watch.playing_since >= self.CAST_MOVED_S)
            if there or now - float(ctx.get("_moving_at") or now) > self.CAST_MOVING_MAX_S:
                ctx.pop("_moving", None)
            elif moving == "live":
                card.set_state(cast_card.TO_LIVE, note=tr("cast_note_to_live"))
                return
            else:
                try:
                    when = time.strftime("%H:%M", time.localtime(float(moving)))
                except (TypeError, ValueError, OverflowError):
                    when = ""
                card.set_state(cast_card.MOVING, note=tr("cast_note_moving", time=when)
                               if when else "")
                return
        if watch.failed:
            card.set_state(cast_card.FAILED, note=str(ctx.get("_fail_note") or ""),
                           action="retry")
        elif watch.recovering():
            card.set_state(cast_card.RECONNECTING, watch.tries, watch.tries_max,
                           note=tr("cast_note_reconnecting"))
        elif self._cast_waiting_route():
            card.set_state(cast_card.WAITING, note=tr("airplay_pick_window"), action="airplay")
        elif self._cast_busy() and self._cast_preparing():
            # Repackaged or converted here first: seconds the TV says
            # nothing in - it may play the last channel on meanwhile.
            card.set_state(cast_card.PREPARING, note=tr("cast_note_preparing"))
        elif self._cast_busy() and ph != cast_watch.PLAYING:
            # A send on its way while the TV says it plays (it plays on
            # until the new stream replaces it): the pill is the TV's.
            card.set_state(cast_card.STARTING)
        elif getattr(self, "_cast_paused_at", None) is not None or ph == cast_watch.PAUSED:
            card.set_state(cast_card.PAUSED)
        elif ph == cast_watch.BUFFERING or (ph != cast_watch.PLAYING
                                            and watch.buffering_shown(now)):
            card.set_state(cast_card.BUFFERING)
        elif ph == cast_watch.PLAYING:
            card.set_state(cast_card.BUFFERING if watch.buffering_shown(now)
                           else cast_card.PLAYING)
        elif ph == cast_watch.ENDED and ctx.get("sid") is None:
            card.set_state(cast_card.ENDED)
        else:
            card.set_state(cast_card.STARTING)

    # -- a stuck cast sent again -------------------------------------------------------

    def _cast_room_for_retry(self, ctx: dict) -> bool:
        """Whether a try may go by itself: the cast's own connection is
        given way to, as a zap's is (mw_connections' _conn_ok_cast) - only
        where nothing else would need asking about first."""
        uses_of = getattr(self, "_conn_uses", None)
        verdict = getattr(self, "_conn_verdict", None)
        if not callable(uses_of) or not callable(verdict):
            return True
        url = str(ctx.get("row_source") or ctx.get("source") or ctx.get("url") or "")
        try:
            from ..core import connections
            if not connections.is_network(url):
                return True
            uses = [u for u in uses_of(free_player=True) if u.kind != "cast"]
            if verdict(uses, source=str(ctx.get("source_id") or "")) is not None:
                return False
            tuner = getattr(self, "_tuner_verdict", None)
            return not (callable(tuner) and tuner(url, uses) is not None)
        except Exception as e:
            log.debug("cast: the room for a try was not counted: %s", e)
            return True

    def _cast_recover(self, watch: cast_watch.Watchdog) -> None:
        """The stream sent to the same TV again, from the same place."""
        ctx = getattr(self, "_cast_ctx", None) or {}
        if not getattr(self, "_cast_device", None) or self._cast_busy():
            return
        if not self._cast_room_for_retry(ctx):
            log.info("cast: not sent again by itself - the account's connections are in use")
            watch.failed = True
            self._cast_gave_up(tr("cast_note_no_room"))
            return
        self._cast_restart(ctx)
        self._cast_card_state()

    def _cast_restart(self, ctx: dict) -> None:
        """One send of the cast now, the way the controls send it: a channel
        at the live edge, or where the rewind buffer or the archive has it;
        a title from where the TV got to. Whatever feeds the TV lets go
        first (*settle*) - never two streams."""
        ctx["_recovering"] = True
        if ctx.get("sid") is not None:
            if ctx.get("archive_from") is not None:
                self._cast_from_moment(self._cast_moment())
            else:
                self._cast_go_live(settle=True)
            return
        try:
            at = float(self.cast.position() or 0.0)
        except Exception:
            at = float(ctx.get("start") or 0.0)
        self._recast_with(ctx.get("audio"), ctx.get("subs"), start=at, settle=True)

    def _cast_recover_failed(self, msg: str) -> bool:
        """A try's cast call failed: the next try comes as the watchdog
        says, the last one gives up. False when no try was under way."""
        ctx = getattr(self, "_cast_ctx", None) or {}
        if not ctx.pop("_recovering", False):
            return False
        watch = self._cast_watch()
        log.info("cast: sending it again failed (%s)", msg)
        if watch.tries >= watch.tries_max:
            watch.failed = True
            self._cast_gave_up(tr("cast_note_send_failed", msg=msg))
        self._cast_card_state()
        return True

    def _cast_gave_up(self, note: str) -> None:
        """The card says it gave up, with Try again - and so does a
        message, as the app's other failures do."""
        ctx = getattr(self, "_cast_ctx", None) or {}
        ctx["_fail_note"] = note
        ctx.pop("_recovering", None)
        device = str(getattr(self, "_cast_device", None) or "")
        toast = getattr(self, "_show_action_toast", None)
        if callable(toast):
            toast(tr("cast_stuck_notice", name=device, title=str(ctx.get("title") or "")),
                  0, self._cast_try_again, label=tr("cast_try_again"), key="cast_stuck")
        self._cast_card_state()

    def _cast_try_again(self) -> None:
        """Try again (the card's, the message's): a stall of its own - asked
        first where the account's connections are in use, as a cast is."""
        ctx = getattr(self, "_cast_ctx", None) or {}
        device = getattr(self, "_cast_device", None)
        if not device or self._cast_busy():
            return
        hub = getattr(self, "notices", None)
        dismiss = getattr(hub, "dismiss_key", None)
        if callable(dismiss):
            dismiss("cast_stuck")
        url = str(ctx.get("row_source") or ctx.get("source") or ctx.get("url") or "")
        ask = getattr(self, "_conn_ok_cast", None)
        if callable(ask) and not ask(url, source_id=str(ctx.get("source_id") or "")):
            return
        watch = self._cast_watch()
        watch.reset()
        watch.tries = 1
        ctx["_fail_note"] = ""
        log.info("cast: Try again - %s sent to %s again", for_log(str(ctx.get("title") or "")),
                 device)
        self._cast_restart(ctx)
        self._cast_card_state()

    def _cast_card_action(self, kind: str) -> None:
        if kind == "retry":
            self._cast_try_again()
        elif kind == "airplay":
            self._cast_airplay_window()

    # -- the skips ---------------------------------------------------------------------

    def _cast_skips_shown(self) -> None:
        """Back and forward where they work: a title the TV knows the length
        of; a channel that can be wound back - forward only while behind
        live."""
        back, fwd = getattr(self, "cast_bar_back", None), getattr(self, "cast_bar_fwd", None)
        if back is None or fwd is None:
            return
        ctx = getattr(self, "_cast_ctx", None) or {}
        on = bool(getattr(self, "_cast_device", None))
        if ctx.get("sid") is not None:
            can = on and self._cast_can_go_back()
            behind = (datetime.now() - self._cast_moment()).total_seconds() if can else 0.0
            back.setVisible(can)
            fwd.setVisible(can and behind > self.CAST_BUFFER_BEHIND_S)
            return
        titled = on and float(getattr(self.cast, "duration", 0.0) or 0.0) > 0
        back.setVisible(titled)
        fwd.setVisible(titled)

    def _cast_skip(self, delta: float) -> None:
        """*delta* seconds on (or back) on the TV: in a title within its
        length; on a channel within the rewind buffer or the archive, and
        forward next to live is live."""
        ctx = getattr(self, "_cast_ctx", None) or {}
        if not getattr(self, "_cast_device", None):
            return
        if ctx.get("sid") is not None:
            if not self._cast_can_go_back():
                return
            to = self._cast_moment() + timedelta(seconds=delta)
            if delta > 0 and (datetime.now() - to).total_seconds() < self.CAST_BUFFER_BEHIND_S:
                if ctx.get("archive_from") is not None or getattr(self, "_cast_behind", 0.0) > 0:
                    self._cast_go_live()
                return
            log.info("cast: %+d s on the channel", int(delta))
            self._cast_to_moment(to)
            return
        media = self._cast_media()
        if not media or media["length"] <= 0:
            return
        to = max(0.0, min(media["length"], media["position"] + delta))
        self._cast_seek(to)

    # -- what plays, marked everywhere -------------------------------------------------

    _CAST_MARK_HOOKS = ("_place_tv_playing_changed", "_pf_mark_playing",
                        "_place_series_mark", "_lp_mark", "_rp_mark", "_rap_mark",
                        "_mitt_mark", "_sp_refresh_soon")

    def _cast_marks_changed(self) -> None:
        """A cast started, moved on or ended: every list and place marks
        what plays now - here, or "On TV"."""
        now = self._now_playing()
        media_cards.set_on_tv(bool(now is not None and now.on_tv))
        for name in self._CAST_MARK_HOOKS:
            hook = getattr(self, name, None)
            if not callable(hook):
                continue
            try:
                hook()
            except Exception:
                log.exception("cast: %s did not mark what plays", name)
        for name in ("listw", "grid"):
            view = getattr(self, name, None)
            try:
                if view is not None and not sip.isdeleted(view):
                    view.viewport().update()
            except (AttributeError, RuntimeError):
                pass
