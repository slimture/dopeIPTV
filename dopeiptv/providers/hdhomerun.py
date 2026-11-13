"""HDHomeRun network tuners (SiliconDust) and everything that speaks their
HTTP API: Threadfin, xTeVe, Dispatcharr, Channels DVR, Plex-style tuner
emulators, Tvheadend's and Jellyfin's HDHomeRun outputs.

``/discover.json`` names the device, ``/lineup.json`` lists its channels
with their stream addresses. The guide is SiliconDust's own for a real
device (what every HDHomeRun app shows), or the playlist's own guide address
for an emulator, which serves its XMLTV elsewhere. An EXTEND model can
transcode (a profile on the stream address).
"""

from __future__ import annotations

import socket
import struct
import zlib
from typing import Any, Callable
from urllib.parse import urlsplit

from ..core.log import log
from ..i18n import tr
from .boxes import BoxClient, build_xmltv, clean_text

GUIDE_API = "https://api.hdhomerun.com/api/guide.php"
DISCOVER_PORT = 65001


def _major(number: str) -> int:
    try:
        return int(str(number).split(".")[0].split("-")[0])
    except ValueError:
        return 0


class HDHomeRunClient(BoxClient):
    LOG_NAME = "hdhomerun"
    KIND = "HDHomeRun"
    DEFAULT_PORT = 80
    GUIDE_STEPS = 6          # each answer covers a few hours

    def _probe(self) -> dict:
        d = self.get_json("/discover.json")
        if not isinstance(d, dict) or not (d.get("DeviceID") or d.get("LineupURL")):
            raise ValueError("not an HDHomeRun")
        self._device = d
        name = clean_text(d.get("FriendlyName")) or "HDHomeRun"
        model = clean_text(d.get("ModelNumber"))
        return {"kind": "hdhomerun", "name": f"{name} ({model})" if model else name,
                "model": model, "device_id": str(d.get("DeviceID") or ""),
                "tuners": int(d.get("TunerCount") or 0),
                "firmware": str(d.get("FirmwareVersion") or "")}

    def _lineup_url(self) -> str:
        url = str(getattr(self, "_device", {}).get("LineupURL") or "")
        # A device behind NAT or a container names an address of its own;
        # the one the user typed is the one that answers.
        if url.startswith(("http://", "https://")):
            path = urlsplit(url)
            return self.url(path.path + (f"?{path.query}" if path.query else ""))
        return self.url("/lineup.json")

    def _load(self) -> tuple[list[dict], list[dict]]:
        js = self.get_json(self._lineup_url(), timeout=(6.0, 60.0), absolute=True)
        entries = js if isinstance(js, list) else []
        transcode = str(self.opt("transcode", "") or "").strip()
        show_drm = self.opt_flag("show_drm", False)
        rows: list[dict] = []
        favs: list[dict] = []
        everything = tr("box_channels")
        for e in entries:
            if not isinstance(e, dict) or not e.get("URL"):
                continue
            if _flag(e.get("DRM")) and not show_drm:
                continue
            number = str(e.get("GuideNumber") or "")
            url = str(e["URL"])
            if transcode:
                url += ("&" if "?" in url else "?") + "transcode=" + transcode
            row = {"key": number or url, "name": e.get("GuideName") or number,
                   "category": everything, "url": self.login_url(url),
                   "icon": str(e.get("ImageURL") or e.get("Logo") or ""),
                   "epg_id": number, "num": _major(number)}
            rows.append(row)
            if _flag(e.get("Favorite")):
                favs.append(dict(row, category=tr("box_favorites")))
            if _flag(e.get("HD")) and self.opt_flag("hd_group", False):
                favs.append(dict(row, category="HD"))
        return favs + rows, []

    def _guide(self, progress: Callable[[int], None] | None) -> bytes:
        auth = str(getattr(self, "_device", {}).get("DeviceAuth") or "")
        if not auth or not self.opt_flag("cloud_guide", True):
            return b""
        events: list[dict] = []
        start = 0
        for step in range(self.GUIDE_STEPS):
            params: dict[str, Any] = {"DeviceAuth": auth}
            if start:
                params["Start"] = str(start)
            try:
                r = self.session.get(GUIDE_API, params=params, timeout=(6.0, 60.0))
                r.raise_for_status()
                js = r.json()
            except Exception as e:
                log.info("hdhomerun: guide step %d failed (%s)", step, e)
                break
            last = start
            for ch in js if isinstance(js, list) else []:
                number = str(ch.get("GuideNumber") or "")
                for p in ch.get("Guide") or []:
                    s, t = int(p.get("StartTime") or 0), int(p.get("EndTime") or 0)
                    if not (number and s and t > s):
                        continue
                    last = max(last, t)
                    events.append({"channel": number, "start": s, "stop": t,
                                   "title": clean_text(p.get("Title")),
                                   "subtitle": clean_text(p.get("EpisodeTitle")),
                                   "desc": clean_text(p.get("Synopsis"), True),
                                   "category": ", ".join(p.get("Filter") or [])})
            if progress:
                progress(min(99, (step + 1) * 100 // self.GUIDE_STEPS))
            if last <= start:
                break
            start = last
        return build_xmltv(self.guide_channels(), events) if events else b""


def _flag(value: Any) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes")


# -- discovery on the local network (UDP 65001) -------------------------------

def _tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag, len(value)]) + value


def discover_packet() -> bytes:
    """A discover request for any tuner: type 2, device type and id
    wildcards, the CRC32 of it all little-endian at the end."""
    payload = _tlv(0x01, struct.pack(">I", 0xFFFFFFFF)) + _tlv(0x02, struct.pack(">I", 0xFFFFFFFF))
    body = struct.pack(">HH", 0x0002, len(payload)) + payload
    return body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)


def parse_reply(data: bytes, ip: str) -> dict | None:
    """A discover reply (type 3) as {"server", "device_id", "kind"}, or None."""
    if len(data) < 8:
        return None
    body, crc = data[:-4], struct.unpack("<I", data[-4:])[0]
    if zlib.crc32(body) & 0xFFFFFFFF != crc:
        return None
    ptype, length = struct.unpack(">HH", body[:4])
    if ptype != 0x0003:
        return None
    tags: dict[int, bytes] = {}
    pos, end = 4, min(len(body), 4 + length)
    while pos + 2 <= end:
        tag, ln = body[pos], body[pos + 1]
        pos += 2
        if ln & 0x80:
            ln = (ln & 0x7F) | (body[pos] << 7)
            pos += 1
        tags[tag] = body[pos:pos + ln]
        pos += ln
    dev_type = struct.unpack(">I", tags[0x01])[0] if len(tags.get(0x01, b"")) == 4 else 0
    dev_id = tags.get(0x02, b"")
    base = tags.get(0x2A, b"").decode("utf-8", "replace").strip()
    return {"server": base or f"http://{ip}", "kind": "hdhomerun",
            "device_id": dev_id.hex().upper() if len(dev_id) == 4 else "",
            "storage": dev_type == 5}


def discover(timeout: float = 1.5, target: tuple[str, int] = ("255.255.255.255", DISCOVER_PORT)
             ) -> list[dict]:
    """Every HDHomeRun tuner that answers a broadcast on the local network."""
    found: dict[str, dict] = {}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return []
    with s:
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.settimeout(timeout)
            s.sendto(discover_packet(), target)
            while True:
                data, (ip, _port) = s.recvfrom(2048)
                got = parse_reply(data, ip)
                if got and not got["storage"]:
                    found.setdefault(got["server"], got)
        except (socket.timeout, TimeoutError):
            pass
        except OSError as e:
            log.debug("hdhomerun: discovery: %s", e)
    return list(found.values())
