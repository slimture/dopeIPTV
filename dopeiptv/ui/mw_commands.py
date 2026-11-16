"""Commands from outside, carried out: the command line and dopeiptv://
links (core/commands.py), a second start handing over (core/instance.py),
and the remote API, which asks the same way - also for a channel by its
number, as a typed number zaps."""

from __future__ import annotations

import os
import re
from typing import Any

from ..core.log import log
from ..core.workers import run_async


def _norm(s: str) -> str:
    return re.sub(r"[^0-9a-z]", "", (s or "").casefold())


def find_channel(rows: list[dict], name: str) -> dict | None:
    """The channel *name* means: its exact name, then the same letters and
    digits ("svt1hd" for "SVT1 HD"), then one it starts, then one holding
    it."""
    want, flat = (name or "").casefold().strip(), _norm(name)
    if not want:
        return None
    for test in (lambda r: str(r.get("name") or "").casefold() == want,
                 lambda r: _norm(str(r.get("name") or "")) == flat,
                 lambda r: _norm(str(r.get("name") or "")).startswith(flat),
                 lambda r: flat in _norm(str(r.get("name") or ""))):
        for r in rows:
            if r.get("stream_id") is not None and test(r):
                return r
    return None


def _rows_job(win: Any, client: Any):
    """For the pool: *client*'s channels (with a kids profile's categories
    read beside them, mw_profiles)."""
    job = getattr(win, "_live_rows_job", None)
    return job(client) if callable(job) else (lambda: client.live_streams(None))


class _CommandsMixin:
    def _start_command_server(self) -> None:
        from ..core.instance import CommandServer
        self._cmd_server = CommandServer(self.handle_command, self)

    def handle_command(self, cmd: dict) -> dict[str, Any]:
        """Do what *cmd* asks: {"play": address}, {"channel": name},
        {"number": channel number}, {"open": True}, {"tv": True} (TV mode,
        before whatever else it asks). Returns at once; a channel is looked
        up off the window's thread."""
        if not isinstance(cmd, dict):
            return {"ok": False}
        log.info("command: %s", ", ".join(sorted(cmd)))
        if (cmd.get("open") or cmd.get("play") or cmd.get("channel") or cmd.get("supporter")
                or cmd.get("number") or cmd.get("tv")):
            self._come_forward()
        if cmd.get("tv") is True and hasattr(self, "enter_tv_mode"):
            self.enter_tv_mode()
        if (cmd.get("play") or cmd.get("channel") or cmd.get("number") is not None) \
                and getattr(self, "_profile_locked", lambda: False)():
            # "Who's watching?" waits for the PIN at start (it is in front
            # now): nothing is played for the profile before it is given.
            log.info("command: nothing played - who is watching is not chosen yet")
            return {"ok": False, "locked": True}
        key = str(cmd.get("supporter") or "").strip()
        if key and hasattr(self, "open_supporter"):
            # Shown in the supporter window (checked there, offline), never
            # taken silently; after this call returns - the asker is waiting.
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self.open_supporter(key))
            return {"ok": True}
        url = str(cmd.get("play") or "").strip()
        if url:
            self._play_address(url)
            return {"ok": True}
        name = str(cmd.get("channel") or "").strip()
        if name:
            self._play_channel_named(name)
            return {"ok": True}
        from ..core.channel_numbers import valid
        number = valid(cmd.get("number"))
        if number is not None:
            self._play_channel_number(number)
            return {"ok": True}
        return {"ok": bool(cmd.get("open") or cmd.get("tv"))}

    def _play_address(self, url: str, title: str | None = None) -> None:
        """A file plays as one opened from Local files (resume, its name);
        a page of YouTube, Twitch and the like through yt-dlp
        (mw_webstreams); any other address as a stream - opened by mpv
        itself, never through dopeIPTV's own rewind buffer: it is no
        provider's channel and need not be a stream at all (a web page),
        which mpv then says at once. Kept out of Recently viewed - it is
        no channel or title of a source."""
        if url.startswith("file://"):
            # file:///C:/Films/a.mkv is C:\Films\a.mkv on Windows.
            from ..core.commands import file_url_path
            url = file_url_path(url)
        if url.startswith("/") or os.path.isfile(url):
            self._play_local_path(url)
            return
        from ..core.ytdlp import is_web_stream
        if is_web_stream(url):
            self._play_web(url, title)
            return
        title = title or os.path.basename(url.split("?")[0].rstrip("/")) or url
        self._start_playback(url, title, None, url, "live", record=False,
                             own_buffer=False)

    def _play_channel_named(self, name: str) -> None:
        client = self.client

        def done(rows: Any) -> None:
            # Only what the lists show, as by number and from the phone:
            # none from a hidden category, or a locked one while locked.
            it = find_channel(self._chno_rows(list(rows or [])), name)
            if it is None:
                from ..i18n import tr
                self._show_toast(tr("cmd_no_channel", name=name), 5000)
                return
            self.play_live_channel(it)
        run_async(self.pool, _rows_job(self, client), done,
                  lambda _e=None: done([]))

    def _play_channel_step(self, direction: int) -> bool:
        """The next (1) or previous (-1) channel from the one playing, as
        a remote's channel up and down: in the list on screen when it
        holds it (the list follows), else in the whole source. False when
        no channel plays - here or on a cast (mw_cast), which then gets
        the next."""
        last = getattr(self, "_last_playback", None) or {}
        cur = (last.get("item") or {}).get("stream_id")
        casting = getattr(self, "_cast_live_item", None)
        on_cast = casting() if callable(casting) else None
        if on_cast is not None:
            cur = on_cast.get("stream_id")
        elif last.get("kind") != "live" or cur is None or self.player is None \
                or not self._media_active():
            return False
        if self.mode in ("live", "fav") and not self.series_ctx:
            for i in range(self.list_model.rowCount()):
                it = self.list_model.item_at(i)
                if it and not it.get("_header") and str(it.get("stream_id")) == str(cur):
                    self.listw.setCurrentIndex(self.list_model.index(i))
                    self._zap(direction)
                    return True
        client = self.client

        def done(rows: Any) -> None:
            if client is not self.client:
                return
            shown = self._chno_rows(list(rows or []))
            at = next((i for i, r in enumerate(shown) if str(r.get("stream_id")) == str(cur)), None)
            if at is not None and len(shown) > 1:
                self.play_live_channel(shown[(at + direction) % len(shown)])
        run_async(self.pool, _rows_job(self, client), done, lambda _e=None: None)
        return True

    def _play_channel_number(self, number: int) -> None:
        """The channel with *number* in the whole source - the user's own
        number (Nerd) first, else the source's - typed or asked for. Only
        channels the lists show: none from a hidden or locked category."""
        from ..core import channel_numbers
        client, pid = self.client, self._chno_pid()

        def done(rows: Any) -> None:
            if client is not self.client:
                return          # the source changed meanwhile
            it = channel_numbers.find(self._chno_rows(list(rows or [])), number,
                                      channel_numbers.active(pid))
            if it is None:
                self._chan_not_found(number)
                return
            self.play_live_channel(it)
        run_async(self.pool, _rows_job(self, client), done,
                  lambda _e=None: done([]))
