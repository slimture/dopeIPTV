"""Commands from outside, carried out: the command line and dopeiptv://
links (core/commands.py), a second start handing over (core/instance.py),
and the remote API, which asks the same way."""

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


class _CommandsMixin:
    def _start_command_server(self) -> None:
        from ..core.instance import CommandServer
        self._cmd_server = CommandServer(self.handle_command, self)

    def handle_command(self, cmd: dict) -> dict[str, Any]:
        """Do what *cmd* asks: {"play": address}, {"channel": name},
        {"open": True}. Returns at once; a channel is looked up off the
        window's thread."""
        if not isinstance(cmd, dict):
            return {"ok": False}
        log.info("command: %s", ", ".join(sorted(cmd)))
        if cmd.get("open") or cmd.get("play") or cmd.get("channel") or cmd.get("supporter"):
            self._come_forward()
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
        return {"ok": bool(cmd.get("open"))}

    def _play_address(self, url: str) -> None:
        """A file plays as one opened from Local files (resume, its name);
        an address as a stream, kept out of Recently viewed - it is no
        channel or title of a source."""
        if url.startswith("file://"):
            from urllib.parse import unquote, urlsplit
            url = unquote(urlsplit(url).path)
        if url.startswith("/") or os.path.isfile(url):
            self._play_local_path(url)
            return
        title = os.path.basename(url.split("?")[0].rstrip("/")) or url
        self._start_playback(url, title, None, url, "live", record=False)

    def _play_channel_named(self, name: str) -> None:
        client = self.client

        def done(rows: Any) -> None:
            it = find_channel(list(rows or []), name)
            if it is None:
                from ..i18n import tr
                self._show_toast(tr("cmd_no_channel", name=name), 5000)
                return
            self.play_live_channel(it)
        run_async(self.pool, lambda: client.live_streams(None), done,
                  lambda _e=None: done([]))
