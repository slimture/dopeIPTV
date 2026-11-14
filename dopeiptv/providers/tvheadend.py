"""Tvheadend servers (DVB-S/T/C, ATSC, IPTV and SAT>IP tuners behind one
server) through their HTTP API.

Channel tags are the categories, a channel streams from
``/stream/channel/<uuid>`` (with a stream profile of choice: pass, matroska,
a transcoding one), the guide is the server's own XMLTV, and finished DVR
recordings are films. The login is the server's (digest by default, basic
where it is set so); none is needed for an anonymous one.
"""

from __future__ import annotations

import json
from typing import Any, Callable
from urllib.parse import quote

from ..core.log import log
from ..i18n import tr
from .boxes import BoxClient, clean_text

GRID = {"limit": "999999", "start": "0"}


def _num(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


class TvheadendClient(BoxClient):
    LOG_NAME = "tvheadend"
    KIND = "Tvheadend"
    DEFAULT_PORT = 9981

    def _probe(self) -> dict:
        info = self.get_json("/api/serverinfo")
        if not isinstance(info, dict) or "sw_version" not in info:
            raise ValueError("not a Tvheadend server")
        version = str(info.get("sw_version") or "")
        return {"kind": "tvheadend", "name": f"{info.get('name') or 'Tvheadend'} {version}".strip(),
                "version": version, "api": info.get("api_version"),
                "capabilities": list(info.get("capabilities") or [])}

    def _entries(self, path: str, extra: dict | None = None) -> list[dict]:
        js = self.get_json(path, dict(GRID, **(extra or {})), timeout=(6.0, 90.0))
        rows = js.get("entries") if isinstance(js, dict) else None
        return [r for r in rows or [] if isinstance(r, dict)]

    def _stream_url(self, uuid: str) -> str:
        url = self.url(f"/stream/channel/{quote(uuid, safe='')}")
        profile = str(self.opt("profile", "") or "").strip()
        if profile:
            url += "?profile=" + quote(profile, safe="")
        return self.login_url(url)

    def _icon(self, ch: dict) -> str:
        icon = str(ch.get("icon") or "")
        if icon.startswith(("http://", "https://")):
            return icon
        public = str(ch.get("icon_public_url") or "").lstrip("/")
        if public and not public.startswith(("http://", "https://")):
            return self.login_url(self.url("/" + public))
        return public

    def _load(self) -> tuple[list[dict], list[dict]]:
        tags = {}
        try:
            for t in self._entries("/api/channeltag/grid"):
                if t.get("enabled", True) and not t.get("internal") and t.get("uuid"):
                    tags[str(t["uuid"])] = (_num(t.get("index")), clean_text(t.get("name")))
        except Exception as e:
            log.info("tvheadend: no channel tags (%s)", e)
        order = sorted(tags, key=lambda u: (tags[u][0], tags[u][1].lower()))
        rank = {u: i for i, u in enumerate(order)}
        rows: list[dict] = []
        channels = self._entries("/api/channel/grid", {"sort": "number", "dir": "ASC"})
        channels.sort(key=lambda c: (_num(c.get("number")) or 10 ** 9, str(c.get("name") or "").lower()))
        everything = tr("box_all_channels")
        for ch in channels:
            uuid = str(ch.get("uuid") or "")
            if not uuid or not ch.get("enabled", True):
                continue
            base = {"key": uuid, "name": ch.get("name") or uuid, "url": self._stream_url(uuid),
                    "icon": self._icon(ch), "epg_id": uuid, "num": _num(ch.get("number"))}
            rows.append(dict(base, category=everything))
            for t in sorted((u for u in ch.get("tags") or [] if u in tags), key=rank.__getitem__):
                rows.append(dict(base, category=tags[t][1] or everything))
        return rows, self._recordings() if self.opt_flag("recordings", True) else []

    def _recordings(self) -> list[dict]:
        try:
            entries = self._entries("/api/dvr/entry/grid_finished")
        except Exception as e:
            log.info("tvheadend: no recordings (%s)", e)
            return []
        out = []
        for rec in entries:
            uuid = str(rec.get("uuid") or "")
            rel = str(rec.get("url") or (f"dvrfile/{uuid}" if uuid else "")).lstrip("/")
            if not rel:
                continue
            start = _num(rec.get("start_real")) or _num(rec.get("start"))
            stop = _num(rec.get("stop_real")) or _num(rec.get("stop"))
            secs = _num(rec.get("duration")) or max(0, stop - start)
            title = clean_text(rec.get("disp_title"))
            sub = clean_text(rec.get("disp_subtitle"))
            plot = (clean_text(rec.get("disp_description"), True) or clean_text(rec.get("disp_summary"), True)
                    or clean_text(rec.get("disp_extratext"), True))
            name = str(rec.get("filename") or "")
            out.append({"key": uuid or rel, "title": f"{title} - {sub}" if title and sub else title or sub,
                        "plot": plot, "channel": rec.get("channelname") or "", "start": start, "secs": secs,
                        "ext": name.rsplit(".", 1)[-1] if "." in name.rsplit("/", 1)[-1] else "ts",
                        "url": self.login_url(self.url("/" + rel))})
        return out

    def add_timer(self, stream_id: int | str, start: float, stop: float,
                  title: str, description: str = "") -> tuple[bool, str]:
        """A DVR entry on the server (its own padding and profile apply)."""
        row = self._channel(stream_id)
        if row is None:
            return False, ""
        conf = {"start": int(start), "stop": int(stop), "channel": row["_key"],
                "title": {"eng": title or "dopeIPTV"}, "comment": "dopeIPTV"}
        if description:
            conf["description"] = {"eng": description}
        js = self.post("/api/dvr/entry/create", {"conf": json.dumps(conf)}).json()
        uuid = str((js or {}).get("uuid") or "") if isinstance(js, dict) else ""
        return bool(uuid), uuid

    def _guide(self, progress: Callable[[int], None] | None) -> bytes:
        """The server's own XMLTV (channel ids are the channel uuids, or
        names where the server is set to; the guide matches either)."""
        data = self.get("/xmltv/channels", timeout=(6.0, 300.0)).content
        if progress:
            progress(99)
        return data if b"<tv" in data[:4096] else b""
