"""MythTV backends through the Services API (JSON, default port 6544).

``/Myth/GetHostName`` and ``/Myth/GetBackendInfo`` name the backend,
``/Channel/GetChannelInfoList`` lists the channels (ChanId, ChanNum,
CallSign, ChannelName, IconURL), ``/Guide/GetChannelGroupList`` the channel
groups (their channels through ``ChannelGroupID``), ``/Guide/GetProgramGuide``
gives the guide (UTC times, in steps of half a day), ``/Dvr/GetRecordedList``
the recordings - played from ``/Content/GetRecording?RecordedId=`` with
``/Content/GetPreviewImage`` as the picture - and ``/Dvr/AddRecordSchedule``
sets a single recording from the guide.

Live TV: MythTV hands it to its own frontends over the Myth protocol
(port 6543), not as an address a player opens - the Services API has none.
The channels are listed all the same, for the guide and for recording from
it, without a play address (``no_live``); ``live_note`` says why they do not
play. Recordings and the guide work. A login is answered as boxes.py
answers any (MythTV asks for one only where it is set to).

Checked against the MythTV wiki's Services API pages (Myth, Channel, Guide,
Dvr and Content services, 0.28 to v34), as known from them - there is no
running backend here: the paths and parameters above, the JSON wrappers
(``ChannelInfoList.ChannelInfos``, ``ProgramGuide.Channels[].Programs``,
``ProgramList.Programs`` with ``Recording.RecordedId``/``RecGroup`` and
``Channel``), values as strings (and numbers, as v33 and later write some),
AddRecordSchedule's required Title, ChanId, Station, StartTime and EndTime
(Type "Single Record"). Not checked against a real backend: GetChannelInfoList's
``ChannelGroupID`` on versions before it existed (a group then lists every
channel), a backend with API authorisation turned on (v34), and the
answer's ``uint``/``int`` name for the new rule's id.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlencode

from ..core.log import log
from ..i18n import LocalizedError, tr
from .boxes import BoxClient, build_xmltv, clean_text

GUIDE_SECS = 3 * 86400
GUIDE_STEP = 12 * 3600
# Recording groups that are not the recordings a person watches.
HIDDEN_GROUPS = ("deleted", "livetv")


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def iso(stamp: float) -> str:
    """A Unix time as the Services API takes one: ISO 8601, UTC."""
    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def unix(text: Any) -> int:
    """An ISO 8601 time as the Services API writes one (UTC, with a Z or an
    offset) as a Unix time; 0 for anything else."""
    s = str(text or "").strip()
    if not s:
        return 0
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return max(0, int(dt.timestamp()))
    except (OverflowError, OSError, ValueError):
        return 0


def _list(js: Any, *path: str) -> list[dict]:
    for key in path:
        js = js.get(key) if isinstance(js, dict) else None
    return [x for x in js or [] if isinstance(x, dict)] if isinstance(js, list) else []


class MythTVClient(BoxClient):
    LOG_NAME = "mythtv"
    KIND = "MythTV"
    DEFAULT_PORT = 6544

    def __init__(self, server: str, username: str = "", password: str = "",
                 options: dict | None = None) -> None:
        super().__init__(server, username, password, options)
        self.session.headers["Accept"] = "application/json"
        self.live_note = ""
        self._callsigns: dict[str, str] = {}

    def _probe(self) -> dict:
        js = self.get_json("/Myth/GetHostName")
        host = js.get("String") if isinstance(js, dict) else None
        if not isinstance(host, str):
            raise ValueError("not a MythTV backend")
        version = ""
        try:
            info = self.get_json("/Myth/GetBackendInfo")
            build = ((info.get("BackendInfo") or {}).get("Build") or {}) if isinstance(info, dict) else {}
            version = str(build.get("Version") or "") if isinstance(build, dict) else ""
        except LocalizedError:
            raise
        except Exception as e:
            log.debug("mythtv: no backend info (%s)", type(e).__name__)
        name = " ".join(x for x in ("MythTV", version, f"({clean_text(host)})" if host else "") if x)
        return {"kind": "mythtv", "name": name, "version": version, "host": clean_text(host)}

    def _abs(self, path: str) -> str:
        if not path:
            return ""
        if path.startswith(("http://", "https://")):
            return path
        return self.login_url(self.url("/" + path.lstrip("/")))

    # -- the catalogue --------------------------------------------------------------

    def _channel_list(self, **params: str) -> list[dict]:
        js = self.get_json("/Channel/GetChannelInfoList", dict(params, OnlyVisible="true", Details="true"),
                           timeout=(6.0, 90.0))
        return _list(js, "ChannelInfoList", "ChannelInfos")

    def _load(self) -> tuple[list[dict], list[dict]]:
        everything = tr("box_channels")
        base: dict[str, dict] = {}
        self._callsigns = {}
        for ch in self._channel_list():
            cid = str(ch.get("ChanId") or "")
            if not cid or str(ch.get("Visible", "true")).lower() == "false":
                continue
            number = str(ch.get("ChanNum") or "")
            self._callsigns[cid] = str(ch.get("CallSign") or "")
            base[cid] = {"key": cid, "name": ch.get("ChannelName") or ch.get("CallSign") or number or cid,
                         "category": everything, "url": "", "no_live": True,
                         "icon": self._abs(str(ch.get("IconURL") or "")), "epg_id": cid,
                         "num": _int(number.replace("_", ".").split(".")[0]) if number else 0}
        order = sorted(base.values(), key=lambda r: (r["num"] or 10 ** 9, r["name"].lower()))
        grouped: list[dict] = []
        for gid, name in self._groups():
            try:
                ids = {str(c.get("ChanId") or "") for c in self._channel_list(ChannelGroupID=gid)}
            except LocalizedError:
                raise
            except Exception as e:
                log.info("mythtv: group %s not read (%s)", name, type(e).__name__)
                continue
            grouped += [dict(r, category=name) for r in order if r["key"] in ids]
        self.live_note = tr("myth_no_live") if order else ""
        return order + grouped, self._recordings() if self.opt_flag("recordings", True) else []

    def _groups(self) -> list[tuple[str, str]]:
        try:
            js = self.get_json("/Guide/GetChannelGroupList", {"IncludeEmpty": "false"})
        except LocalizedError:
            raise
        except Exception as e:
            log.info("mythtv: no channel groups (%s)", type(e).__name__)
            return []
        out = []
        for g in _list(js, "ChannelGroupList", "ChannelGroups"):
            gid, name = str(g.get("GroupId") or ""), clean_text(g.get("Name"))
            if gid and name:
                out.append((gid, name))
        return out

    def _recordings(self) -> list[dict]:
        try:
            js = self.get_json("/Dvr/GetRecordedList", {"Descending": "true"}, timeout=(6.0, 120.0))
        except LocalizedError:
            raise
        except Exception as e:
            log.info("mythtv: no recordings (%s)", type(e).__name__)
            return []
        out = []
        for p in _list(js, "ProgramList", "Programs"):
            rec: dict = p["Recording"] if isinstance(p.get("Recording"), dict) else {}
            if str(rec.get("RecGroup") or "").replace(" ", "").lower() in HIDDEN_GROUPS:
                continue
            chan: dict = p["Channel"] if isinstance(p.get("Channel"), dict) else {}
            rid = str(rec.get("RecordedId") or "")
            if rid and rid != "0":
                query = {"RecordedId": rid}
            elif chan.get("ChanId") and rec.get("StartTs"):
                query = {"ChanId": str(chan["ChanId"]), "StartTime": str(rec["StartTs"])}
            else:
                continue
            start = unix(rec.get("StartTs")) or unix(p.get("StartTime"))
            stop = unix(rec.get("EndTs")) or unix(p.get("EndTime"))
            title, sub = clean_text(p.get("Title")), clean_text(p.get("SubTitle"))
            name = str(p.get("FileName") or "")
            out.append({"key": rid or f"{query.get('ChanId')}@{query.get('StartTime')}",
                        "title": f"{title} - {sub}" if title and sub and sub != title else title or sub,
                        "plot": p.get("Description") or "",
                        "channel": chan.get("ChannelName") or chan.get("CallSign") or "",
                        "start": start, "secs": max(0, stop - start),
                        "ext": name.rsplit(".", 1)[-1] if "." in name else "ts",
                        "icon": self._abs("/Content/GetPreviewImage?" + urlencode(query)),
                        "url": self._abs("/Content/GetRecording?" + urlencode(query))})
        return out

    # -- the guide ---------------------------------------------------------------------

    def _guide(self, progress: Callable[[int], None] | None) -> bytes:
        known = {cid for cid, _n in self.guide_channels()}
        events: list[dict] = []
        start = int(time.time()) - 3600
        steps = (GUIDE_SECS + 3600) // GUIDE_STEP + 1
        for step in range(steps):
            a = start + step * GUIDE_STEP
            try:
                js = self.get_json("/Guide/GetProgramGuide",
                                   {"StartTime": iso(a), "EndTime": iso(a + GUIDE_STEP), "Details": "true"},
                                   timeout=(6.0, 120.0))
            except LocalizedError:
                raise
            except Exception as e:
                log.info("mythtv: guide step %d failed (%s)", step, type(e).__name__)
                break
            for ch in _list(js, "ProgramGuide", "Channels"):
                cid = str(ch.get("ChanId") or "")
                if cid not in known:
                    continue
                for p in _list(ch, "Programs"):
                    events.append({"channel": cid, "start": unix(p.get("StartTime")),
                                   "stop": unix(p.get("EndTime")), "title": clean_text(p.get("Title")),
                                   "subtitle": clean_text(p.get("SubTitle")),
                                   "desc": clean_text(p.get("Description"), True),
                                   "category": clean_text(p.get("Category"))})
            if progress:
                progress(min(99, (step + 1) * 100 // steps))
        return build_xmltv(self.guide_channels(), events) if events else b""

    # -- record on the backend -------------------------------------------------------------

    def add_timer(self, stream_id: int | str, start: float, stop: float,
                  title: str, description: str = "") -> tuple[bool, str]:
        """A single recording on the backend (its own padding and profile
        apply): the programme at that time on that channel."""
        row = self._channel(stream_id)
        if row is None:
            return False, ""
        cid = str(row["_key"])
        data = {"Title": title or "dopeIPTV", "ChanId": cid, "Station": self._callsigns.get(cid) or row["name"],
                "StartTime": iso(start), "EndTime": iso(stop), "Type": "Single Record"}
        if description:
            data["Description"] = description
        js = self.post("/Dvr/AddRecordSchedule", data).json()
        got = js.get("uint", js.get("int", js.get("RecordId", ""))) if isinstance(js, dict) else ""
        return _int(got) > 0, str(got or "")
