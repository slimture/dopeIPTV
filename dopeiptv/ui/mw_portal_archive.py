"""A portal's archive in the window (providers/stalker.py reads it): a
Stalker / Ministra portal keeps catch-up by programme, and the link to one
comes from the portal - so it is asked for off the window's thread, played
from as far into the programme as was asked, and a go-back carries on with
the next programme when one ends. The menus, the bars and the guide are
those every archive has; _play_timeshift hands over to this once it knows
where to start."""

from __future__ import annotations

import time
from datetime import datetime
from functools import partial
from typing import Any

from ..core.log import log, redact_url
from ..core.privacy import for_log
from ..core.workers import run_async
from ..i18n import tr


class _PortalArchiveMixin:
    # A go-back reaching a programme that ended this close to now goes live
    # rather than asking for the one on air.
    PORTAL_LIVE_GAP_SECS = 90
    # A link that played this long before it ended was the programme, not
    # a portal refusing it.
    PORTAL_PLAYED_SECS = 10
    # A link that ended more than this before its programme was cut short.
    PORTAL_RESUME_SECS = 300
    _ts_from_portal = False
    _ts_portal_stop: float | None = None

    def _play_portal_archive(self, it, start: float, prog=None,
                             prog_origin=None, after_end: bool = False) -> None:
        """Channel *it*'s archive from *start* (unix time): the programme on
        then - or *prog*, picked in a guide - its link asked of the portal
        off the window's thread (StalkerClient.archive_link). *after_end*:
        the programme before has just ended."""
        sid = it.get("stream_id")
        if sid is None:
            return
        client = self.client
        stop = (prog or {}).get("stop_timestamp")
        self._flash_status(tr("ts_checking"))
        token = getattr(self, "_ts_probe_token", 0) + 1
        self._ts_probe_token = token
        # _failover_gen moves on with every start - the auto-preview's too -
        # and with Stop (mw_failover); _diag_gen only with _start_playback.
        ctx: dict[str, Any] = {
            "token": token, "client": client, "gen": getattr(self, "_failover_gen", 0),
            "item": it, "prog": prog, "origin": prog_origin, "after_end": after_end}
        log.debug("[ts] portal archive: channel %s from %s%s", for_log(sid),
                  for_log(datetime.fromtimestamp(start)), " (a programme)" if prog else "")
        run_async(self.pool,
                  partial(client.archive_link, sid, float(start),
                          float(stop) if stop else None),
                  partial(self._portal_archive_ready, ctx),
                  partial(self._portal_archive_failed, ctx))

    def _portal_archive_current(self, ctx: dict) -> bool:
        """Nothing newer since it was asked: no other archive, no other
        source, nothing else started playing (a preview included), no Stop."""
        return (ctx["token"] == getattr(self, "_ts_probe_token", 0)
                and self.client is ctx["client"]
                and ctx["gen"] == getattr(self, "_failover_gen", 0))

    def _portal_archive_ready(self, ctx: dict, got) -> None:
        if not self._portal_archive_current(ctx):
            return
        url = got.get("url") if isinstance(got, dict) else None
        if not url:
            self._portal_archive_failed(ctx, None)
            return
        it, prog = ctx["item"], ctx["prog"]
        name = self.channel_display_name(it)
        what = (((prog or {}).get("title") or got.get("title")
                 or tr("ts_programme")) if prog else got.get("title"))
        title = (tr("ts_title_programme", what=what, name=name) if what
                 else tr("ts_title_channel", name=name))
        self._ts_candidates = [url]
        self._ts_candidate_idx = 0
        self._ts_candidate_started = time.monotonic()
        # The link plays the programme from its own start: the bars count
        # from there, and the player starts *offset* in.
        self._ts_segment_start = got["start"]
        self._ts_catchup_program = bool(prog)
        self._ts_from_portal = True
        self._ts_portal_stop = got["stop"]
        if prog:
            origin = ctx["origin"]
            if origin is None:
                # A portal programme that starts before the guide's is on
                # the bar from its own start.
                origin = min(prog.get("start_timestamp") or got["start"],
                             got["start"])
            self._ts_program_start = origin
            self._ts_program_stop = prog.get("stop_timestamp")
            self._ts_program_title = prog.get("title")
        else:
            self._ts_program_start = None
            self._ts_program_stop = None
            self._ts_program_title = None
        log.debug("[ts] portal archive: programme %s %s-%s, %.0f s in: %s",
                  for_log(got.get("id")), for_log(datetime.fromtimestamp(got["start"])),
                  for_log(datetime.fromtimestamp(got["stop"])), got["offset"],
                  for_log(redact_url(url)))
        self._start_playback(url, title, it.get("stream_icon"),
                             self._item_key(it), "live", record=False,
                             item=it, catchup=True, start_at=got["offset"])

    def _portal_archive_failed(self, ctx: dict, err) -> None:
        """No link: the portal has no such programme or gave nothing for
        it (*err* None), or it was not reached (*err*, which proves nothing
        about the archive - no channel is marked for it)."""
        if not self._portal_archive_current(ctx):
            return
        if err is not None:
            log.info("[ts] portal archive: not reached - %s", err)
        self._flash_status(tr("ts_check_failed") if err is not None
                           else tr("ts_archive_unavailable"), ms=6000)
        it = ctx["item"]
        if ctx["after_end"]:
            # The programme before has ended: live, not its last frame.
            self.play_live_channel(it)
            return
        if not self.player or getattr(self, "_playing_catchup", False):
            return      # an archive still plays, and carries on as it was
        # Live never stopped: its timeline as it was, as _play_timeshift
        # leaves it.
        if self._timeshift_days(it):
            self._apply_seek_mode(it, "live")
        else:
            self.player.set_seek_mode("live")
            self.player.set_live_badge(None)

    def _portal_archive_next(self, it) -> bool:
        """The end of a portal archive's link, which is one programme: a
        go-back carries on with the next programme, as an Xtream archive
        runs on by itself (or, cut off well before the end, from where it
        stopped). False for anything else - a programme picked in a guide
        plays to its end and then live, as on every source."""
        stop = self._ts_portal_stop
        played = time.monotonic() - getattr(self, "_ts_candidate_started", 0.0)
        if (not self._ts_from_portal or not stop
                or getattr(self, "_ts_catchup_program", False)
                or played < self.PORTAL_PLAYED_SECS
                or time.time() - stop < self.PORTAL_LIVE_GAP_SECS):
            return False
        seg = getattr(self, "_ts_segment_start", None) or 0.0
        try:
            at = seg + float(self.player.playback_position()) if self.player else 0.0
        except Exception:
            at = 0.0
        frm = float(stop)
        if seg and seg < at < stop - self.PORTAL_RESUME_SECS:
            # The link ended well before its programme did (a drop): on
            # from where the picture stopped, not from the next one.
            frm = at
        log.debug("[ts] portal archive: on from %s (the programme ends %s)",
                  datetime.fromtimestamp(frm), datetime.fromtimestamp(stop))
        self._play_portal_archive(it, frm, after_end=True)
        return True
