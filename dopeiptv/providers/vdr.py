"""VDR (Video Disk Recorder) through two of its plugins: restfulapi for
what it knows, streamdev-server for the streams.

restfulapi (default port 8002) gives the channels with their groups and
logos (``/channels.json``; a channel's group is the ``:Group`` line above it
in channels.conf, its logo ``/channels/image/<channel id>``), the guide
(``/events/<channel id>.json``), the recordings (``/recordings.json``) and
timers (``POST /timers``). streamdev-server's HTTP side (default port 3000,
the ``streamdev_port`` option) streams a channel at ``/TS/<channel id>``
and a recording at ``/TS/<device>:<inode>.rec``, and lists the channels as
``/channels.m3u`` (and, from 0.6, the recordings as ``/recordings.m3u``).

A VDR with only streamdev (no restfulapi) is still a source: its M3U's
channels play, but there is no guide and nothing to record on - which
``guide_note`` says plainly. One whose restfulapi does not list
streamdev-server among VDR's plugins says that its channels will not play
(``live_note``). A login, where a proxy in front asks for one, is answered
as boxes.py answers any (basic or digest).

Checked against restfulapi's README (yavdr's vdr-plugin-restfulapi 0.2.6)
and streamdev's README (vdr-plugin-streamdev 0.6), as known from their
documentation - there is no running VDR here: the paths above, the channel
fields (channel_id, name, number, group, image, is_radio), the event fields
(id, title, short_text, description, start_time, duration, contents), the
recording fields (name, file_name, inode, duration, channel_id, event_title,
event_short_text, event_description, event_start_time), the timer's form
(flags, file, weekdays, day, channel, start, stop as HHMM, priority,
lifetime), streamdev's ``/TYPE/CHANNEL`` addresses. Not checked against a
real one: whether restfulapi's ``inode`` already ends in ``.rec`` (both are
taken), how streamdev's M3U writes a group (``group-title`` and
``#EXTGRP`` are read), and the timer's answer (only its status counts).
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any, Callable
from urllib.parse import quote, urlsplit, urlunsplit

from ..core._lazy_requests import requests
from ..core.log import log
from ..i18n import LocalizedError, tr
from .boxes import BoxClient, build_xmltv, clean_text

STREAMDEV_PORT = 3000
# How far ahead the guide is asked, and from how long ago (the running
# programme started before now).
GUIDE_SECS = 3 * 86400
GUIDE_BACK = 6 * 3600
PAGE = 1000
LOGIN_KEYS = ("err_wrong_login", "err_box_login_needed")

# As providers/client.py reads an M3U line: linear on any line.
_EXTINF = re.compile(r'#EXTINF:\s*+-?[\d.]*+(?P<attrs>(?:[^,"]|"[^"]*")*+),(?P<name>.*)')
_ATTR = re.compile(r'(?<![\w-])([\w-]++)="([^"]*)"')
_NUMBERED = re.compile(r"^(\d+)\s+(.+)$")


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def parse_m3u(text: str) -> list[dict]:
    """[{name, url, group, logo, tvg_id}] of an M3U playlist, as streamdev
    writes one (a group from ``group-title`` or an ``#EXTGRP`` line)."""
    out: list[dict] = []
    pending: dict | None = None
    group = ""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            m = _EXTINF.match(line)
            attrs = dict(_ATTR.findall(m.group("attrs"))) if m else {}
            name = (m.group("name") if m else line.split(",", 1)[-1]).strip()
            pending = {"name": name, "group": attrs.get("group-title", ""),
                       "logo": attrs.get("tvg-logo", ""), "tvg_id": attrs.get("tvg-id", "")}
        elif line.startswith("#EXTGRP:"):
            group = line[len("#EXTGRP:"):].strip()
        elif not line.startswith("#"):
            entry = pending or {"name": "", "group": "", "logo": "", "tvg_id": ""}
            entry["url"] = line
            entry["group"] = entry["group"] or group
            out.append(entry)
            pending = None
    return out


class VdrClient(BoxClient):
    LOG_NAME = "vdr"
    KIND = "VDR"
    DEFAULT_PORT = 8002

    def __init__(self, server: str, username: str = "", password: str = "",
                 options: dict | None = None) -> None:
        super().__init__(server, username, password, options)
        # "restfulapi" or "streamdev": what answered.
        self.mode = ""
        self.live_note = ""
        self.guide_note = ""
        self._streamdev = ""
        self._m3u = b""
        self._names: dict[str, str] = {}

    # -- where it answers -----------------------------------------------------

    def streamdev_base(self) -> str:
        """streamdev-server on the same host, at its port."""
        host = urlsplit(self.server).hostname or ""
        if ":" in host:
            host = f"[{host}]"
        port = self.opt_int("streamdev_port", STREAMDEV_PORT)
        if not 0 < port < 65536:
            port = STREAMDEV_PORT
        return f"http://{host}:{port}"

    def _probe(self) -> dict:
        self.live_note = self.guide_note = ""
        self._m3u = b""
        info = self._restful_info()
        if info is not None:
            self.mode = "restfulapi"
            self._streamdev = self.streamdev_base()
            vdr: dict = info["vdr"] if isinstance(info.get("vdr"), dict) else {}
            version = str(vdr.get("version") or "")
            plugins = [str(p.get("name") or "") for p in vdr.get("plugins") or [] if isinstance(p, dict)]
            if plugins and not any(p.startswith("streamdev") for p in plugins):
                self.live_note = tr("vdr_no_streamdev")
            return {"kind": "vdr", "name": f"VDR {version}".strip(), "version": version,
                    "api": str(info.get("version") or ""), "plugins": plugins}
        base = self._find_streamdev()
        if not base:
            raise LocalizedError("err_box_not_found", kind=self.KIND)
        self.mode = "streamdev"
        self._streamdev = base
        self.guide_note = tr("vdr_streamdev_only")
        return {"kind": "vdr", "name": "VDR (streamdev)", "version": "", "streamdev": base}

    def _restful_info(self) -> dict | None:
        try:
            js = self.get_json("/info.json")
        except LocalizedError as e:
            if e.key in LOGIN_KEYS:
                raise
            return None
        except (requests.RequestException, ValueError):
            return None
        if isinstance(js, dict) and ("vdr" in js or "diskusage" in js):
            return js
        return None

    def _find_streamdev(self) -> str:
        """The address streamdev answers at: the one typed (a streamdev
        address itself), else the VDR's host at streamdev's port."""
        for base in dict.fromkeys((self.server, self.streamdev_base())):
            try:
                r = self.get(base + "/channels.m3u", absolute=True, timeout=(4.0, 30.0))
            except LocalizedError as e:
                if e.key in LOGIN_KEYS:
                    raise
                continue
            except requests.RequestException:
                continue
            if r.content.lstrip()[:7] == b"#EXTM3U":
                self._m3u = r.content
                return base
        return ""

    # -- addresses --------------------------------------------------------------

    def _channel_url(self, channel_id: str) -> str:
        return self.login_url(f"{self._streamdev}/TS/{quote(channel_id, safe='')}")

    def _rebase(self, url: str) -> str:
        """An address from streamdev's M3U on the address that answered (it
        names itself as it sees itself, behind NAT or in a container)."""
        u = urlsplit(url)
        b = urlsplit(self._streamdev)
        if not u.scheme:
            return self.login_url(self._streamdev + "/" + url.lstrip("/"))
        return self.login_url(urlunsplit((b.scheme, b.netloc, u.path, u.query, "")))

    # -- the catalogue ------------------------------------------------------------

    def _load(self) -> tuple[list[dict], list[dict]]:
        if self.mode == "streamdev":
            rows = self._streamdev_channels()
            return rows, self._streamdev_recordings() if self.opt_flag("recordings", True) else []
        rows = self._restful_channels()
        return rows, self._restful_recordings() if self.opt_flag("recordings", True) else []

    def _paged(self, path: str, key: str, params: dict | None = None,
               timeout: tuple[float, float] = (6.0, 90.0)) -> list[dict]:
        out: list[dict] = []
        start = 0
        for _ in range(200):
            js = self.get_json(path, dict(params or {}, start=str(start), limit=str(PAGE)), timeout=timeout)
            got = js.get(key) if isinstance(js, dict) else None
            rows = [r for r in got or [] if isinstance(r, dict)]
            out += rows
            total = _int(js.get("total")) if isinstance(js, dict) else 0
            if not rows or len(out) >= total:
                break
            start += len(rows)
        return out

    def _restful_channels(self) -> list[dict]:
        radio = self.opt_flag("radio", True)
        everything = tr("box_channels")
        rows: list[dict] = []
        self._names = {}
        channels = self._paged("/channels.json", "channels")
        channels.sort(key=lambda c: _int(c.get("number")) or 10 ** 9)
        for ch in channels:
            cid = str(ch.get("channel_id") or "")
            if not cid or (ch.get("is_radio") and not radio):
                continue
            name = clean_text(ch.get("name")) or cid
            self._names[cid] = name
            icon = (self.login_url(self.url("/channels/image/" + quote(cid, safe="")))
                    if ch.get("image") else "")
            rows.append({"key": cid, "name": name, "category": clean_text(ch.get("group")) or everything,
                         "url": self._channel_url(cid), "icon": icon, "epg_id": cid,
                         "num": _int(ch.get("number"))})
        return rows

    def _restful_recordings(self) -> list[dict]:
        try:
            recs = self._paged("/recordings.json", "recordings")
        except LocalizedError:
            raise
        except Exception as e:
            log.info("vdr: no recordings (%s)", type(e).__name__)
            return []
        out = []
        for rec in recs:
            inode = str(rec.get("inode") or "").strip()
            inode = inode[:-4] if inode.endswith(".rec") else inode
            if not inode:
                continue
            name = clean_text(rec.get("name"))
            title = clean_text(rec.get("event_title")) or name.split("~")[-1]
            sub = clean_text(rec.get("event_short_text"))
            out.append({"key": str(rec.get("file_name") or inode),
                        "title": f"{title} - {sub}" if title and sub and sub != title else title or sub,
                        "plot": rec.get("event_description") or "",
                        "channel": self._names.get(str(rec.get("channel_id") or ""), ""),
                        "start": _int(rec.get("event_start_time")),
                        "secs": _int(rec.get("duration")) or _int(rec.get("event_duration")),
                        "url": self.login_url(f"{self._streamdev}/TS/{quote(inode, safe=':')}.rec")})
        return out

    def _m3u_text(self, path: str) -> str:
        if path == "/channels.m3u" and self._m3u:
            data = self._m3u
        else:
            data = self.get(self._streamdev + path, absolute=True, timeout=(6.0, 60.0)).content
        return data.decode("utf-8", errors="replace")

    def _streamdev_channels(self) -> list[dict]:
        everything = tr("box_channels")
        rows: list[dict] = []
        for e in parse_m3u(self._m3u_text("/channels.m3u")):
            seg = urlsplit(e["url"]).path.rstrip("/").rsplit("/", 1)[-1]
            name, num = clean_text(e["name"]), 0
            m = _NUMBERED.match(name)
            if m and m.group(1) == seg:
                num, name = int(m.group(1)), m.group(2).strip()
            elif seg.isdigit():
                num = int(seg)
            # A channel id (S19.2E-1-1019-10301) names it for good; a number
            # changes when the list is sorted, its name does not.
            key = name if seg.isdigit() or not seg else seg
            rows.append({"key": key or e["url"], "name": name or seg, "category": clean_text(e["group"]) or everything,
                         "url": self._rebase(e["url"]), "icon": e["logo"], "epg_id": e["tvg_id"], "num": num})
        return rows

    def _streamdev_recordings(self) -> list[dict]:
        try:
            entries = parse_m3u(self._m3u_text("/recordings.m3u"))
        except LocalizedError:
            raise
        except Exception as e:
            log.info("vdr: streamdev lists no recordings (%s)", type(e).__name__)
            return []
        return [{"key": urlsplit(e["url"]).path, "title": e["name"] or urlsplit(e["url"]).path,
                 "url": self._rebase(e["url"])} for e in entries]

    # -- the guide ------------------------------------------------------------------

    def _guide(self, progress: Callable[[int], None] | None) -> bytes:
        if self.mode != "restfulapi":
            return b""
        channels = self.guide_channels()
        events: list[dict] = []
        since = int(time.time()) - GUIDE_BACK
        failed = 0
        for i, (cid, _name) in enumerate(channels):
            try:
                js = self.get_json(f"/events/{quote(cid, safe='')}.json",
                                   {"timespan": str(GUIDE_SECS + GUIDE_BACK), "from": str(since),
                                    "start": "0", "limit": "2000"}, timeout=(6.0, 60.0))
            except LocalizedError:
                raise
            except Exception as e:
                failed += 1
                log.debug("vdr: no guide for %s (%s)", cid, type(e).__name__)
                if failed >= 5 and not events:
                    break
                continue
            for ev in (js.get("events") if isinstance(js, dict) else None) or []:
                if not isinstance(ev, dict):
                    continue
                start = _int(ev.get("start_time"))
                contents = [str(c) for c in ev.get("contents") or [] if c]
                events.append({"channel": cid, "start": start, "stop": start + _int(ev.get("duration")),
                               "title": clean_text(ev.get("title")),
                               "subtitle": clean_text(ev.get("short_text")),
                               "desc": clean_text(ev.get("description"), True),
                               "category": ", ".join(contents)})
            if progress:
                progress(min(99, (i + 1) * 100 // max(1, len(channels))))
        return build_xmltv(channels, events) if events else b""

    # -- record on the VDR ------------------------------------------------------------

    def add_timer(self, stream_id: int | str, start: float, stop: float,
                  title: str, description: str = "") -> tuple[bool, str]:
        """A timer on the VDR, in its local time (its own margins apply).
        Only through restfulapi: streamdev sets none."""
        row = self._channel(stream_id)
        if row is None or self.mode != "restfulapi":
            return False, ""
        a, b = datetime.fromtimestamp(start), datetime.fromtimestamp(stop)
        # A colon separates timers.conf's fields; VDR writes one in a name as |.
        data = {"flags": "1", "file": (title or "dopeIPTV").replace(":", "|"), "weekdays": "-------",
                "day": a.strftime("%Y-%m-%d"), "channel": row["_key"], "start": a.strftime("%H%M"),
                "stop": b.strftime("%H%M"), "priority": "50", "lifetime": "99", "aux": ""}
        r = self.post("/timers", data)
        return bool(r.ok), ""
