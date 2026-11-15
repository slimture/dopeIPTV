"""NextPVR (v5 and later, default port 8866) through its session API.

``/service?method=session.initiate`` hands out a session id and a salt;
``session.login`` takes the MD5 of ":" + MD5(PIN) + ":" + salt, as NextPVR
documents it (the PIN is the source's password; 0000, NextPVR's own
default, when none is given). Every later call carries the session id:
``channel.list`` (id, name, number, type - 0xa is radio - and whether it has
an icon, ``channel.icon``), ``channel.groups`` and ``channel.list`` by
group, ``channel.listings`` per channel (its times in milliseconds or
seconds), ``recording.list`` (ready ones) and ``recording.save`` for a
recording set from the guide (with the server's own padding from
``setting.list``). A channel plays from ``/live?channeloid=<id>`` and a
recording from ``/live?recording=<id>``, both with the session id. An
expired session (a restarted server) is logged in again once.

Checked against NextPVR's API as Kodi's pvr.nextpvr uses it and NextPVR's
documentation of the session login, as known from them - there is no
running NextPVR here: the methods and their parameters above, the XML
``<rsp stat="ok">`` answers, ``<channels><channel>``, ``<listings><l>``
(name, description, subtitle, genre, start, end), ``<recordings>
<recording>`` (id, name, desc, subtitle, channel, start_time_ticks,
duration_seconds, status), the login's MD5. Not checked against a real
server: that a stream address takes ``client=`` freely, that
``recording.save`` needs nothing more for a one-off recording, the
``setting.list`` names of the padding (PrePadding, PostPadding), and
NextPVR 4's older recording fields (start_time, duration; both read).
"""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Any, Callable
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

from ..core.log import log, register_secrets
from ..i18n import LocalizedError, tr
from .boxes import BoxClient, build_xmltv, child_text, clean_text, parse_xml

DEVICE = "dopeIPTV"
DEFAULT_PIN = "0000"
GUIDE_SECS = 3 * 86400


def login_digest(pin: str, salt: str) -> str:
    """What session.login takes: md5(":" + md5(pin) + ":" + salt), hex."""
    pin_md5 = hashlib.md5((pin or DEFAULT_PIN).encode("utf-8")).hexdigest().lower()
    return hashlib.md5(f":{pin_md5}:{salt}".encode()).hexdigest()


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _secs(text: str) -> int:
    """A time NextPVR writes as Unix seconds or milliseconds."""
    s = (text or "").strip()
    if not s.lstrip("-").isdigit():
        return 0
    n = int(s)
    return n // 1000 if n > 10 ** 11 else n


class NextPVRClient(BoxClient):
    LOG_NAME = "nextpvr"
    KIND = "NextPVR"
    DEFAULT_PORT = 8866

    def __init__(self, server: str, username: str = "", password: str = "",
                 options: dict | None = None) -> None:
        super().__init__(server, username, password, options)
        self.sid = ""
        self._padding = (0, 0)
        self._sid_lock = threading.Lock()

    # -- the session --------------------------------------------------------------

    def _rsp(self, params: dict, timeout: tuple[float, float] | None = None) -> ET.Element:
        r = self.get("/service", params, timeout)
        try:
            root = parse_xml(r.content)
        except ET.ParseError:
            raise ValueError("not a NextPVR answer") from None
        if root.tag != "rsp":
            raise ValueError("not a NextPVR answer")
        return root

    def _login(self) -> None:
        root = self._rsp({"method": "session.initiate", "ver": "1.0", "device": DEVICE})
        sid, salt = child_text(root, "sid"), child_text(root, "salt")
        if root.get("stat") != "ok" or not sid:
            raise ValueError("NextPVR gave no session")
        register_secrets(sid)
        got = self._rsp({"method": "session.login", "sid": sid,
                         "md5": login_digest(self.password, salt)})
        if got.get("stat") != "ok":
            raise LocalizedError("err_wrong_pin")
        self.sid = sid

    def call(self, method: str, timeout: tuple[float, float] | None = None, **params: str) -> ET.Element:
        """A method with the session id. A refusal is asked once more after
        a new login: the session may have gone (a server restarted since),
        and NextPVR's wording of that is not one to match on."""
        for again in (False, True):
            with self._sid_lock:
                if not self.sid:
                    self._login()
                sid = self.sid
            root = self._rsp(dict(params, method=method, sid=sid), timeout)
            if root.get("stat") == "ok":
                return root
            if again:
                err = root.find("err")
                raise LocalizedError(clean_text(err.get("msg") if err is not None else "")
                                     or "err_unexpected_response")
            with self._sid_lock:
                if self.sid == sid:
                    self.sid = ""
        raise LocalizedError("err_unexpected_response")

    def _probe(self) -> dict:
        self.sid = ""
        self._login()
        info: dict[str, Any] = {"kind": "nextpvr", "name": "NextPVR", "version": ""}
        try:
            ver = self.call("setting.version")
            readable = child_text(ver, "readableVersion") or child_text(ver, "version")
            if readable:
                info.update(name=f"NextPVR {readable}", version=readable)
        except LocalizedError:
            pass
        except Exception as e:
            log.debug("nextpvr: no version (%s)", type(e).__name__)
        try:
            st = self.call("setting.list")
            self._padding = (_int(child_text(st, "PrePadding")), _int(child_text(st, "PostPadding")))
        except LocalizedError:
            pass
        except Exception as e:
            log.debug("nextpvr: no settings (%s)", type(e).__name__)
        return info

    # -- addresses ---------------------------------------------------------------------

    def _service_url(self, path: str, **params: str) -> str:
        return self.url(path) + "?" + urlencode(dict(params, sid=self.sid))

    # The session in use when asked (a new login since the list was read
    # gave a new one), not the one the list was read with.
    def live_url(self, stream_id: int | str, fmt: str = "ts") -> str:
        self._ensure(refresh=False)
        row = self._channel(stream_id)
        return self._service_url("/live", channeloid=str(row["_key"]), client=DEVICE) if row else ""

    def vod_url(self, stream_id: int | str, ext: str | None = None) -> str:
        self._ensure(refresh=False)
        try:
            row = self._rec_by_id.get(int(stream_id))
        except (TypeError, ValueError):
            row = None
        return self._service_url("/live", recording=str(row["_key"]), client=DEVICE) if row else ""

    # -- the catalogue -------------------------------------------------------------------

    def _channels(self, **params: str) -> list[ET.Element]:
        root = self.call("channel.list", timeout=(6.0, 90.0), **params)
        found = root.find("channels")
        return list(found.findall("channel")) if found is not None else []

    def _load(self) -> tuple[list[dict], list[dict]]:
        radio = self.opt_flag("radio", True)
        everything = tr("box_channels")
        base: dict[str, dict] = {}
        for ch in self._channels():
            cid = child_text(ch, "id")
            if not cid or (child_text(ch, "type").lower() == "0xa" and not radio):
                continue
            number, minor = _int(child_text(ch, "number")), _int(child_text(ch, "minor"))
            name = child_text(ch, "name") or cid
            icon = (self._service_url("/service", method="channel.icon", channel_id=cid)
                    if ch.find("icon") is not None else "")
            base[cid] = {"key": cid, "name": name, "category": everything, "icon": icon,
                         "url": self._service_url("/live", channeloid=cid, client=DEVICE),
                         "epg_id": cid, "num": number, "_minor": minor}
        order = sorted(base.values(), key=lambda r: (r["num"] or 10 ** 9, r["_minor"], r["name"].lower()))
        grouped: list[dict] = []
        for name in self._groups():
            try:
                members = [child_text(ch, "id") for ch in self._channels(group_id=name)]
            except LocalizedError:
                raise
            except Exception as e:
                log.info("nextpvr: group %s not read (%s)", name, type(e).__name__)
                continue
            ids = set(members)
            grouped += [dict(r, category=name) for r in order if r["key"] in ids]
        return order + grouped, self._recordings() if self.opt_flag("recordings", True) else []

    def _groups(self) -> list[str]:
        try:
            root = self.call("channel.groups")
        except LocalizedError:
            raise
        except Exception as e:
            log.info("nextpvr: no channel groups (%s)", type(e).__name__)
            return []
        found = root.find("groups")
        names = [child_text(g, "name") for g in (found.findall("group") if found is not None else [])]
        return [n for n in dict.fromkeys(names) if n and n.lower() != "all channels"]

    def _recordings(self) -> list[dict]:
        try:
            root = self.call("recording.list", timeout=(6.0, 90.0), filter="ready")
        except LocalizedError:
            raise
        except Exception as e:
            log.info("nextpvr: no recordings (%s)", type(e).__name__)
            return []
        found = root.find("recordings")
        out = []
        for rec in found.findall("recording") if found is not None else []:
            rid = child_text(rec, "id")
            status = child_text(rec, "status").lower()
            if not rid or status not in ("", "ready", "recording"):
                continue
            title, sub = child_text(rec, "name"), child_text(rec, "subtitle")
            start = _secs(child_text(rec, "start_time_ticks")) or _secs(child_text(rec, "start_time"))
            secs = _int(child_text(rec, "duration_seconds")) or _int(child_text(rec, "duration"))
            out.append({"key": rid, "title": f"{title} - {sub}" if title and sub and sub != title else title or sub,
                        "plot": child_text(rec, "desc", True), "channel": child_text(rec, "channel"),
                        "start": start, "secs": secs,
                        "url": self._service_url("/live", recording=rid, client=DEVICE)})
        return out

    # -- the guide ----------------------------------------------------------------------

    def _guide(self, progress: Callable[[int], None] | None) -> bytes:
        channels = self.guide_channels()
        now = int(time.time())
        events: list[dict] = []
        failed = 0
        for i, (cid, _name) in enumerate(channels):
            try:
                root = self.call("channel.listings", timeout=(6.0, 60.0), channel_id=cid,
                                 start=str(now - 3600), end=str(now + GUIDE_SECS))
            except Exception as e:
                failed += 1
                log.debug("nextpvr: no guide for %s (%s)", cid, type(e).__name__)
                if failed >= 5 and not events:
                    break
                continue
            found = root.find("listings")
            for item in found.findall("l") if found is not None else []:
                genre = child_text(item, "genre") or ", ".join(
                    clean_text(g.text) for g in item.iter("genre") if clean_text(g.text))
                events.append({"channel": cid, "start": _secs(child_text(item, "start")),
                               "stop": _secs(child_text(item, "end")), "title": child_text(item, "name"),
                               "subtitle": child_text(item, "subtitle"),
                               "desc": child_text(item, "description", True), "category": genre})
            if progress:
                progress(min(99, (i + 1) * 100 // max(1, len(channels))))
        return build_xmltv(channels, events) if events else b""

    # -- record on the server ---------------------------------------------------------------

    def add_timer(self, stream_id: int | str, start: float, stop: float,
                  title: str, description: str = "") -> tuple[bool, str]:
        """A one-off recording on the server, with its own padding."""
        row = self._channel(stream_id)
        if row is None:
            return False, ""
        pre, post = self._padding
        root = self.call("recording.save", name=title or DEVICE, channel=str(row["_key"]),
                         time_t=str(int(start)), duration=str(max(60, int(stop - start))),
                         pre_padding=str(pre), post_padding=str(post))
        return True, child_text(root, "recording_id") or child_text(root, "id")
