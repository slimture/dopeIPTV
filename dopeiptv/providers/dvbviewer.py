"""DVBViewer Media Server (DMS, the DVBViewer Recording Service before it)
through its web API - XML answers under ``/api/`` on the web interface's
port (8089 by default), behind its basic login when one is set.

The channel list (``getchannelsxml.html``: roots, groups, channels with
their number, name, ID, EPGID and logo) gives the channels and their groups,
the favourites (``getfavourites.html``) groups of their own on top, the
guide is ``epg.html`` per channel, the recordings ``recordings.html``, and a
timer is ``timeradd.html``. Streams come from the media server's HTTP/UPnP
side - ``upnp/channelstream/<nr>.ts`` for a channel and
``upnp/recordings/<id>.ts`` for a recording - on its own port (7522 by
default, the ``stream_port`` option). Its dates are the server's local time:
"yyyymmddhhmmss" in answers, Delphi (OLE) day numbers in questions.

Checked against how the DMS's API is documented and used by Kodi's
pvr.dvbviewer, as known from them - there is no running DMS here: the
paths and their parameters (``lvl=2``, ``utf8=1``, ``images=1``, a timer's
``ch``/``dor``/``start``/``stop`` in minutes/``title``/``encoding=255``),
``version.html``'s ``iver``, the channel attributes, the EPG programme's
``titles``/``events``/``descriptions``, a recording's ``id``/``start``/
``duration`` (hhmmss) with ``title``/``info``/``desc``/``channel``/``image``
and the answer's ``imageURL``. Not checked against a real server: the
stream port and paths (they differ between DMS versions and set-ups - the
port is an option), the favourites answer's shape (read tolerantly: a
section's caption, an entry's ID or "ID|name"), and whether a timer answers
anything beyond its status.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from ..core.log import log
from ..i18n import LocalizedError, tr
from .boxes import BoxClient, build_xmltv, child_text, clean_text

STREAM_PORT = 7522
GUIDE_SECS = 3 * 86400
_DELPHI_EPOCH = datetime(1899, 12, 30)


def ole_day(stamp: float) -> float:
    """A Unix time as the server's day number (days since 1899-12-30, in
    the local time it runs in)."""
    return (datetime.fromtimestamp(stamp) - _DELPHI_EPOCH).total_seconds() / 86400.0


def local_time(text: str) -> int:
    """A Unix time from what the server writes: "yyyymmddhhmmss",
    "dd.mm.yyyy hh:mm:ss" or ISO, all in its local time; 0 for anything else."""
    s = (text or "").strip()
    for fmt, size in (("%Y%m%d%H%M%S", 14), ("%d.%m.%Y %H:%M:%S", 19),
                      ("%Y-%m-%dT%H:%M:%S", 19), ("%Y-%m-%d %H:%M:%S", 19)):
        try:
            return int(time.mktime(datetime.strptime(s[:size], fmt).timetuple()))
        except (ValueError, OverflowError):
            continue
    return 0


def duration_secs(text: str) -> int:
    """hhmmss, hh:mm:ss or plain seconds."""
    s = (text or "").strip()
    parts = s.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        if len(s) == 6 and s.isdigit():
            return int(s[:2]) * 3600 + int(s[2:4]) * 60 + int(s[4:])
        return max(0, int(float(s)))
    except (ValueError, OverflowError):
        return 0


def _first_text(el: ET.Element, *paths: str) -> str:
    for p in paths:
        found = el.find(p)
        if found is not None and (found.text or "").strip():
            return found.text or ""
    return ""


class DVBViewerClient(BoxClient):
    LOG_NAME = "dvbviewer"
    KIND = "DVBViewer"
    DEFAULT_PORT = 8089

    def stream_base(self) -> str:
        host = urlsplit(self.server).hostname or ""
        if ":" in host:
            host = f"[{host}]"
        port = self.opt_int("stream_port", STREAM_PORT)
        if not 0 < port < 65536:
            port = STREAM_PORT
        return f"http://{host}:{port}"

    def _xml(self, path: str, params: dict | None = None,
             timeout: tuple[float, float] | None = None) -> ET.Element:
        try:
            return self.get_xml(path, params, timeout)
        except ET.ParseError:
            raise ValueError("not XML") from None

    def _probe(self) -> dict:
        root = self._xml("/api/version.html")
        text = clean_text(root.text)
        if root.tag != "version" or not text:
            raise ValueError("not a DVBViewer Media Server")
        return {"kind": "dvbviewer", "name": text, "version": str(root.get("iver") or "")}

    # -- the catalogue ----------------------------------------------------------------

    def _load(self) -> tuple[list[dict], list[dict]]:
        root = self._xml("/api/getchannelsxml.html", {"logo": "1"}, timeout=(6.0, 90.0))
        roots = root.findall("root") or [root]
        several = len(roots) > 1
        stream = self.stream_base()
        everything = tr("box_channels")
        rows: list[dict] = []
        by_id: dict[str, dict] = {}
        for top in roots:
            top_name = clean_text(top.get("name"))
            for group in top.findall("group") or [top]:
                gname = clean_text(group.get("name"))
                cat = (f"{top_name} · {gname}" if several and top_name and gname
                       else gname or top_name or everything)
                for ch in group.findall("channel"):
                    nr = str(ch.get("nr") or "").strip()
                    if not nr:
                        continue
                    cid = str(ch.get("ID") or ch.get("id") or "").strip()
                    epg = str(ch.get("EPGID") or cid).strip()
                    logo = child_text(ch, "logo")
                    icon = ""
                    if logo:
                        icon = logo if logo.startswith(("http://", "https://")) else self.login_url(
                            self.url("/" + logo.lstrip("/").replace("\\", "/")))
                    row = {"key": cid or f"nr:{nr}", "name": ch.get("name") or nr, "category": cat,
                           "url": self.login_url(f"{stream}/upnp/channelstream/{nr}.ts"),
                           "icon": icon, "epg_id": epg, "num": _int(nr)}
                    rows.append(row)
                    if cid:
                        by_id.setdefault(cid, row)
        favs = self._favourites(rows, by_id) if self.opt_flag("favourites", True) else []
        recs = self._recordings(stream) if self.opt_flag("recordings", True) else []
        return favs + rows, recs

    def _favourites(self, rows: list[dict], by_id: dict[str, dict]) -> list[dict]:
        """The server's favourites as groups of their own, read tolerantly:
        a section's caption names its group, an entry names its channel by
        ID, "ID|name" or name."""
        try:
            root = self._xml("/api/getfavourites.html")
        except LocalizedError:
            raise
        except Exception as e:
            log.info("dvbviewer: no favourites (%s)", type(e).__name__)
            return []
        by_name = {clean_text(r["name"]).lower(): r for r in rows}
        default = tr("box_favorites")
        out: list[dict] = []
        seen: set[tuple[str, str]] = set()

        def walk(el: ET.Element, cat: str) -> None:
            for child in el:
                tag = child.tag.lower()
                if tag in ("section", "group", "folder"):
                    walk(child, clean_text(child.get("caption") or child.get("name")
                                           or child.get("Header")) or cat)
                    continue
                if tag not in ("entry", "channel", "favourite", "favorite"):
                    continue
                text = clean_text(child.text)
                cid = str(child.get("ID") or child.get("id") or child.get("channelid") or "")
                name = clean_text(child.get("name"))
                if not cid and "|" in text:
                    cid, name = (x.strip() for x in text.split("|", 1))
                row = by_id.get(cid) or by_name.get((name or text).lower())
                if row is None or (row["key"], cat) in seen:
                    continue
                seen.add((row["key"], cat))
                out.append(dict(row, category=cat))
        walk(root, default)
        return out

    def _recordings(self, stream: str) -> list[dict]:
        try:
            root = self._xml("/api/recordings.html", {"utf8": "1", "images": "1", "nofilename": "1"},
                             timeout=(6.0, 90.0))
        except LocalizedError:
            raise
        except Exception as e:
            log.info("dvbviewer: no recordings (%s)", type(e).__name__)
            return []
        images = child_text(root, "imageURL")
        out = []
        for rec in root.findall("recording"):
            rid = str(rec.get("id") or "").strip()
            if not rid:
                continue
            title = clean_text(_first_text(rec, "title"))
            sub = clean_text(_first_text(rec, "info"))
            image = child_text(rec, "image")
            icon = ""
            if image:
                icon = image if image.startswith(("http://", "https://")) else (
                    images.rstrip("/") + "/" + image.lstrip("/") if images else self.url("/" + image.lstrip("/")))
            out.append({"key": rid, "title": f"{title} - {sub}" if title and sub and sub != title else title or sub,
                        "plot": _first_text(rec, "desc", "description"), "channel": child_text(rec, "channel"),
                        "start": local_time(rec.get("start") or ""),
                        "secs": duration_secs(rec.get("duration") or ""), "icon": icon,
                        "url": self.login_url(f"{stream}/upnp/recordings/{rid}.ts")})
        return out

    # -- the guide -------------------------------------------------------------------------

    def _guide(self, progress: Callable[[int], None] | None) -> bytes:
        channels = self.guide_channels()
        now = time.time()
        span = {"lvl": "2", "start": f"{ole_day(now - 3600):.6f}", "end": f"{ole_day(now + GUIDE_SECS):.6f}"}
        events: list[dict] = []
        failed = 0
        for i, (epg, _name) in enumerate(channels):
            try:
                root = self._xml("/api/epg.html", dict(span, channel=epg), timeout=(6.0, 60.0))
            except LocalizedError:
                raise
            except Exception as e:
                failed += 1
                log.debug("dvbviewer: no guide for %s (%s)", epg, type(e).__name__)
                if failed >= 5 and not events:
                    break
                continue
            for p in root.iter("programme"):
                start, stop = local_time(p.get("start") or ""), local_time(p.get("stop") or "")
                events.append({"channel": epg, "start": start, "stop": stop,
                               "title": clean_text(_first_text(p, "titles/title", "title")),
                               "subtitle": clean_text(_first_text(p, "events/event", "event")),
                               "desc": clean_text(_first_text(p, "descriptions/description", "description"),
                                                  True)})
            if progress:
                progress(min(99, (i + 1) * 100 // max(1, len(channels))))
        return build_xmltv(channels, events) if events else b""

    # -- record on the server ------------------------------------------------------------

    def add_timer(self, stream_id: int | str, start: float, stop: float,
                  title: str, description: str = "") -> tuple[bool, str]:
        """A timer on the server, in its local time (its own margins apply)."""
        row = self._channel(stream_id)
        cid = str((row or {}).get("_key") or "")
        if not cid or cid.startswith("nr:"):
            return False, ""
        a, b = datetime.fromtimestamp(start), datetime.fromtimestamp(stop)
        params = {"ch": cid, "dor": str(int(ole_day(start))), "enable": "1",
                  "start": str(a.hour * 60 + a.minute), "stop": str(b.hour * 60 + b.minute),
                  "prio": "50", "title": title or "dopeIPTV", "encoding": "255"}
        r = self.get("/api/timeradd.html", params)
        return bool(r.ok), ""


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return 0
