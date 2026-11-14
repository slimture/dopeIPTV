"""The remote API and the shared lists in the window (core/remote_api.py
serves them, services/share.py builds the lists): started and stopped as
Nerd > Remote and sharing says, and each request's work done on the
window's own thread - the same controls as the media keys (mw_mpris), the
same commands as a dopeiptv:// link (mw_commands)."""

from __future__ import annotations

import threading
from typing import Any

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..core import nerd
from ..core.commands import stream_url_ok
from ..core.log import log
from ..core.remote_api import DEFAULT_PORT, RemoteServer, new_token
from ..i18n import tr

_WAIT_S = 5.0


class _Job:
    def __init__(self, action: str, params: dict) -> None:
        self.action, self.params = action, params
        self.done = threading.Event()
        self.result: Any = None
        self.error: BaseException | None = None


class _Bridge(QObject):
    """A request's work onto the window's thread, its answer back."""

    asked = pyqtSignal(object)

    def __init__(self, do) -> None:
        super().__init__()
        self._do = do
        self.asked.connect(self._run)

    def _run(self, job: _Job) -> None:
        try:
            job.result = self._do(job.action, job.params)
        except BaseException as e:      # handed back to the asking thread
            job.error = e
        finally:
            job.done.set()

    def call(self, action: str, params: dict) -> Any:
        job = _Job(action, params)
        if QThread.currentThread() is self.thread():
            self._run(job)
        else:
            self.asked.emit(job)
            if not job.done.wait(_WAIT_S):
                raise TimeoutError(action)
        if job.error is not None:
            raise job.error
        return job.result


def remote_config(settings: Any) -> dict[str, Any]:
    """What the server should be now, from the settings; nothing while
    Nerd is off."""
    n = nerd.Nerd(settings)
    remote, share = n.on and n.flag("nerd_remote"), n.on and n.flag("nerd_share")
    port = n.num("nerd_remote_port", DEFAULT_PORT)
    if not 1024 <= port <= 65535:
        port = DEFAULT_PORT
    return {"remote": remote, "share": share, "port": port,
            "host": "0.0.0.0" if n.flag("nerd_remote_lan") else "127.0.0.1",
            "token": n.get("nerd_remote_token").strip()}


class _RemoteMixin:
    _remote: RemoteServer | None = None
    _remote_cfg: dict | None = None
    _remote_error: str = ""
    _remote_bridge: _Bridge | None = None

    # -- the server ------------------------------------------------------------
    def _remote_apply(self, wanted: dict | None = None) -> dict[str, Any]:
        """Start, restart or stop the server to match *wanted* (the settings
        when None; then a server that could not start is tried again).
        Returns its state, for the Nerd page's line."""
        cfg = dict(wanted) if wanted is not None else remote_config(self.settings)
        if (cfg["remote"] or cfg["share"]) and not cfg.get("token"):
            cfg["token"] = new_token()
            self.settings.setValue("nerd_remote_token", cfg["token"])
        on = cfg["remote"] or cfg["share"]
        retry = wanted is not None or not self._remote_error
        if cfg != self._remote_cfg or (on and self._remote is None and retry):
            self._remote_stop()
            self._remote_cfg = cfg
            self._remote_error = ""
            if on:
                self._remote_start(cfg)
        return self._remote_state()

    def _remote_start(self, cfg: dict) -> None:
        from ..services.share import Share
        if self._remote_bridge is None:
            self._remote_bridge = _Bridge(self._remote_do)
        bridge = self._remote_bridge
        share = Share(lambda: bridge.call("_sources_full", {})) if cfg["share"] else None
        srv = RemoteServer(cfg["host"], cfg["port"], cfg["token"], self._remote_call,
                           share=share, remote=cfg["remote"])
        try:
            srv.start()
        except OSError as e:
            self._remote_error = f"{type(e).__name__}: {e}"
            log.warning("remote: cannot listen on port %s: %s", cfg["port"], e)
            self._show_toast(tr("remote_port_taken", port=cfg["port"]), 6000)
            return
        self._remote = srv

    def _remote_stop(self) -> None:
        srv, self._remote = self._remote, None
        if srv is not None:
            srv.stop()

    def _remote_state(self) -> dict[str, Any]:
        cfg = self._remote_cfg or remote_config(self.settings)
        srv = self._remote
        return dict(cfg, running=srv is not None, error=self._remote_error,
                    port=srv.port if srv is not None else cfg["port"])

    # -- a request (the server's thread) ------------------------------------------
    def _remote_call(self, action: str, params: dict) -> Any:
        bridge = self._remote_bridge
        if bridge is None:
            raise TimeoutError(action)
        if action == "channels":
            client = bridge.call("_client", {})
            rows = [r for r in (client.live_streams(None) or []) if r.get("stream_id") is not None]
            q = str(params.get("q") or "").casefold().strip()
            if q:
                rows = [r for r in rows if q in str(r.get("name") or "").casefold()]
            return {"channels": [{"id": r.get("stream_id"), "name": r.get("name"),
                                  "number": r.get("num"), "guide_id": r.get("epg_channel_id") or None}
                                 for r in rows]}
        return bridge.call(action, params)

    # -- the work (the window's thread) ---------------------------------------------
    def _remote_do(self, action: str, params: dict) -> Any:
        if action == "_client":
            return self.client
        if action == "_sources_full":
            store = getattr(self, "playlist_store", None)
            return [dict(p) for p in store.playlists()] if store is not None else []
        if action == "sources":
            store = getattr(self, "playlist_store", None)
            active = store.active_id if store is not None else None
            # Names and kinds only: no address, user name or password.
            return {"sources": [{"id": p.get("id"), "name": p.get("name"), "kind": p.get("kind") or "xtream",
                                 "active": p.get("id") == active}
                                for p in (store.playlists() if store is not None else [])]}
        if action == "status":
            return self._remote_status()
        if action == "play":
            url = str(params.get("url") or "").strip()
            channel = str(params.get("channel") or "").strip()
            if params.get("number") not in (None, "") and not (url or channel):
                from ..core.channel_numbers import MAX_NUMBER, valid
                number = valid(params.get("number"))
                if number is None:
                    raise ValueError(f'"number" is a channel number from 1 to {MAX_NUMBER}')
                self.handle_command({"number": number})
                return {"ok": True}
            if not (url or channel):
                raise ValueError('give "channel" (a name), "number" or "url"')
            if url and not stream_url_ok(url):
                # A program on the network plays streams, never a file here.
                raise ValueError('"url" is a stream address: http, https, rtsp, rtmp, rtp, udp or srt')
            self.handle_command({"play": url} if url else {"channel": channel})
            return {"ok": True}
        if action == "volume":
            try:
                value = float(params.get("value"))
            except (TypeError, ValueError):
                raise ValueError('"value" is a number from 0 to 100') from None
            self._remote_media("volume", max(0.0, min(100.0, value)) / 100.0)
            return {"ok": True, "volume": int(round(max(0.0, min(100.0, value))))}
        if action == "seek":
            for key, name in (("by", "seek"), ("to", "set_position")):
                if key in params:
                    try:
                        secs = float(params[key])
                    except (TypeError, ValueError):
                        raise ValueError(f'"{key}" is a number of seconds') from None
                    self._remote_media(name, secs)
                    return {"ok": True}
            raise ValueError('give "by" (seconds, + or -) or "to" (seconds from the start)')
        names = {"pause": "pause", "resume": "play", "playpause": "playpause", "stop": "stop",
                 "next": "next", "previous": "previous", "raise": "raise"}
        if action in names:
            self._remote_media(names[action])
            return {"ok": True}
        raise ValueError(f"unknown action: {action}")

    def _remote_media(self, name: str, arg: Any = None) -> None:
        # The media keys' guard against one press arriving twice is no
        # business of a script asking twice in a row.
        self.__dict__.setdefault("_media_pressed", {}).pop(name, None)
        self.mpris_command(name, arg)

    def _remote_status(self) -> dict[str, Any]:
        snap = self.mpris_snapshot()
        last = getattr(self, "_last_playback", None) or {}
        state = {"Playing": "playing", "Paused": "paused"}.get(snap.get("status"), "stopped")
        store = getattr(self, "playlist_store", None)
        active = store.active() if store is not None else None
        out: dict[str, Any] = {"state": state, "volume": int(round(float(snap.get("volume") or 0) * 100)),
                               "source": (active or {}).get("name")}
        if state != "stopped":
            kind = last.get("kind") or ""
            out.update(kind=kind, title=snap.get("title") or "",
                       position=round(float(snap.get("position") or 0), 1),
                       duration=round(float(snap.get("length") or 0), 1))
            if kind == "live":
                out["programme"] = snap.get("artist") or ""
        return out
