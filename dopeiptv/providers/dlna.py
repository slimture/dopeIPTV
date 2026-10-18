"""DLNA / UPnP AV: casting to smart TVs and other media renderers.

Most TVs (Samsung, LG, Sony, Philips, Panasonic), many AV receivers, and
apps such as Kodi or BubbleUPnP answer on the LAN as a UPnP MediaRenderer.
They are found with SSDP and driven with SOAP: the TV is handed the
stream's address and fetches it itself, as a Chromecast does. A local file
is served to it from here. Only the standard library - nothing to install.

DlnaManager answers to the same calls as ChromecastManager, and CastHub
(providers/casting.py) puts the two behind the one object the window casts
with.
"""
from __future__ import annotations

import os
import re
import secrets
import socket
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import quote, urljoin, urlparse
from xml.sax.saxutils import escape

from .. import APP_NAME, VERSION
from ..core._lazy_requests import requests
from ..core.log import log, redact_url
from ..i18n import LocalizedError
from .cast_bridge import lan_address

SSDP_ADDR = ("239.255.255.250", 1900)
SEARCH_TARGETS = ("urn:schemas-upnp-org:device:MediaRenderer:1",
                  "urn:schemas-upnp-org:service:AVTransport:1")
AVT_PREFIX = "urn:schemas-upnp-org:service:AVTransport:"
RC_PREFIX = "urn:schemas-upnp-org:service:RenderingControl:"
# A renderer's name in the device list says what it is, and keeps it apart
# from a Chromecast of the same name - many TVs are both.
SUFFIX = " (DLNA)"
# A device description is a few kilobytes. Anything this size is not one.
MAX_XML = 512 * 1024
UA = f"{APP_NAME}/{VERSION} UPnP/1.0 DLNADOC/1.50"
POLL_S = 1.5
# How long a TV gets to start playing before the cast counts as refused.
START_WAIT_S = 20.0

_MIME = {
    ".ts": "video/mp2t", ".m2ts": "video/mp2t", ".mts": "video/mp2t",
    ".m3u8": "application/vnd.apple.mpegurl", ".mp4": "video/mp4",
    ".m4v": "video/mp4", ".mkv": "video/x-matroska", ".webm": "video/webm",
    ".avi": "video/x-msvideo", ".mov": "video/quicktime",
    ".mpg": "video/mpeg", ".mpeg": "video/mpeg", ".wmv": "video/x-ms-wmv",
    ".mp3": "audio/mpeg", ".flac": "audio/flac", ".m4a": "audio/mp4",
    ".aac": "audio/aac", ".ogg": "audio/ogg", ".opus": "audio/ogg",
    ".wav": "audio/wav",
}


def mime_for(url: str) -> str:
    """The stream's type from its address. An Xtream live address has no
    extension at all and is a transport stream."""
    path = urlparse(url).path if "://" in url else url
    return _MIME.get(os.path.splitext(path)[1].lower(), "video/mp2t")


def hms(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def parse_hms(text: str | None) -> float:
    """"1:02:03" or "0:01:02.500" in seconds; 0 for anything else (a TV
    that does not know says NOT_IMPLEMENTED)."""
    m = re.fullmatch(r"\s*(\d+):(\d{1,2}):(\d{1,2})(?:\.\d+)?\s*", text or "")
    if not m:
        return 0.0
    h, mi, s = (int(g) for g in m.groups())
    return float(h * 3600 + mi * 60 + s)


def didl(url: str, title: str, mime: str) -> str:
    """The DIDL-Lite description a renderer shows the title from."""
    kind = ("object.item.audioItem.musicTrack" if mime.startswith("audio/")
            else "object.item.videoItem")
    return (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        '<item id="0" parentID="-1" restricted="1">'
        f"<dc:title>{escape(title or APP_NAME)}</dc:title>"
        f"<upnp:class>{kind}</upnp:class>"
        f'<res protocolInfo="http-get:*:{escape(mime)}:*">{escape(url)}</res>'
        "</item></DIDL-Lite>")


def _clean_name(name: str) -> str:
    name = re.sub(r"[\x00-\x1f\x7f]", "", name or "").strip()
    return name[:80] or "TV"


def _parse_xml(body: bytes) -> ET.Element:
    """Parse what a device on the network sent. A device description never
    needs a DTD or entities, so a document carrying one is refused rather
    than expanded."""
    if len(body) > MAX_XML or b"<!DOCTYPE" in body or b"<!ENTITY" in body:
        raise ValueError("not a device description")
    root = ET.fromstring(body)
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


@dataclass
class Renderer:
    name: str
    location: str
    av_url: str
    av_type: str
    rc_url: str = ""
    rc_type: str = ""
    udn: str = ""


def ssdp_search(timeout: float = 3.0,
                addr: tuple[str, int] = SSDP_ADDR) -> list[str]:
    """The description address of everything that answers a search for a
    media renderer."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM,
                         socket.IPPROTO_UDP)
    found: list[str] = []
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        try:
            # Out of the LAN interface, not whichever one the kernel likes -
            # a VPN or Docker bridge otherwise swallows the search.
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                            socket.inet_aton(lan_address()))
        except OSError:
            pass
        sock.settimeout(0.4)
        for st in SEARCH_TARGETS:
            msg = ("M-SEARCH * HTTP/1.1\r\n"
                   f"HOST: {SSDP_ADDR[0]}:{SSDP_ADDR[1]}\r\n"
                   'MAN: "ssdp:discover"\r\n'
                   "MX: 2\r\n"
                   f"ST: {st}\r\n"
                   f"USER-AGENT: {UA}\r\n\r\n").encode()
            for _ in range(2):          # UDP: a search can simply get lost
                sock.sendto(msg, addr)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, _addr = sock.recvfrom(65507)
            except socket.timeout:
                continue
            for line in data.decode("utf-8", "replace").split("\r\n"):
                key, _, value = line.partition(":")
                value = value.strip()
                if (key.strip().lower() == "location"
                        and value.startswith("http://")
                        and value not in found):
                    found.append(value)
    except OSError as e:
        log.info("dlna: the search could not be sent (%s)", e)
    finally:
        sock.close()
    return found


def describe(location: str, session: Any = None) -> Renderer | None:
    """The renderer behind *location*, or None when it cannot play media
    (no AVTransport) or does not answer sensibly."""
    http = session or requests
    try:
        r = http.get(location, timeout=4, headers={"User-Agent": UA})
        if r.status_code != 200:
            return None
        root = _parse_xml(r.content)
    except Exception as e:
        log.info("dlna: no description from %s (%s)", location, e)
        return None
    base = (root.findtext("URLBase") or "").strip() or location
    for dev in root.iter("device"):
        av = rc = None
        for svc in dev.iter("service"):
            stype = (svc.findtext("serviceType") or "").strip()
            ctrl = (svc.findtext("controlURL") or "").strip()
            if not ctrl:
                continue
            url = urljoin(base, ctrl)
            if not url.startswith("http://"):
                continue
            if stype.startswith(AVT_PREFIX) and av is None:
                av = (url, stype)
            elif stype.startswith(RC_PREFIX) and rc is None:
                rc = (url, stype)
        if av is not None:
            return Renderer(
                name=_clean_name(dev.findtext("friendlyName") or ""),
                location=location, av_url=av[0], av_type=av[1],
                rc_url=rc[0] if rc else "", rc_type=rc[1] if rc else "",
                udn=(dev.findtext("UDN") or "").strip())
    return None


class SoapError(Exception):
    def __init__(self, code: str, text: str) -> None:
        super().__init__(f"{code} {text}".strip())
        self.code = code


def soap(url: str, service: str, action: str,
         args: list[tuple[str, Any]], timeout: float = 6.0,
         session: Any = None) -> dict[str, str]:
    """Call *action* on a renderer. Arguments in the order the service
    declares them - some TVs read them by position."""
    body = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in args)
    envelope = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f'<s:Body><u:{action} xmlns:u="{service}">{body}</u:{action}>'
        "</s:Body></s:Envelope>")
    http = session or requests
    r = http.post(url, data=envelope.encode("utf-8"), timeout=timeout,
                  headers={"Content-Type": 'text/xml; charset="utf-8"',
                           "SOAPAction": f'"{service}#{action}"',
                           "User-Agent": UA})
    try:
        root = _parse_xml(r.content or b"<x/>")
    except Exception:
        root = ET.Element("x")
    if r.status_code != 200:
        code = (root.findtext(".//errorCode") or str(r.status_code)).strip()
        text = (root.findtext(".//errorDescription") or "").strip()
        raise SoapError(code, text)
    answer = root.find(f".//{action}Response")
    if answer is None:
        return {}
    return {child.tag: (child.text or "") for child in answer}


class _FileHandler(BaseHTTPRequestHandler):
    """One file, at one unguessable address, with byte ranges - a TV seeks
    in a film by asking for the part it wants."""

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):        # noqa: A003 - stdlib hook
        pass

    def _target(self) -> tuple[str, int] | None:
        srv = self.server
        if self.path.split("?", 1)[0] != srv.url_path:  # type: ignore[attr-defined]
            self.send_error(404)
            return None
        path = srv.file_path                            # type: ignore[attr-defined]
        try:
            return path, os.path.getsize(path)
        except OSError:
            self.send_error(404)
            return None

    def _head(self, status: int, length: int, size: int,
              first: int = 0) -> None:
        self.send_response(status)
        self.send_header("Content-Type", self.server.mime)  # type: ignore[attr-defined]
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range",
                             f"bytes {first}-{first + length - 1}/{size}")
        # Some TVs will not seek, or will not play at all, without these.
        self.send_header("transferMode.dlna.org", "Streaming")
        self.send_header("contentFeatures.dlna.org",
                         "DLNA.ORG_OP=01;DLNA.ORG_CI=0;"
                         "DLNA.ORG_FLAGS=01700000000000000000000000000000")
        self.end_headers()

    def _range(self, size: int) -> tuple[int, int] | None:
        m = re.fullmatch(r"bytes=(\d*)-(\d*)",
                         (self.headers.get("Range") or "").strip())
        if not m or not (m.group(1) or m.group(2)):
            return None
        if m.group(1):
            first = int(m.group(1))
            last = int(m.group(2)) if m.group(2) else size - 1
        else:                                # the last N bytes
            first, last = max(0, size - int(m.group(2))), size - 1
        return first, min(last, size - 1)

    def do_HEAD(self) -> None:
        target = self._target()
        if target is not None:
            self._head(200, target[1], target[1])

    def do_GET(self) -> None:
        target = self._target()
        if target is None:
            return
        path, size = target
        span = self._range(size) if self.headers.get("Range") else None
        if span is not None and (span[0] >= size or span[0] > span[1]):
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        first, last = span if span else (0, size - 1)
        self._head(206 if span else 200, last - first + 1, size, first)
        try:
            with open(path, "rb") as fh:
                fh.seek(first)
                left = last - first + 1
                while left > 0:
                    chunk = fh.read(min(256 * 1024, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass                     # the TV seeks by hanging up and asking
        except OSError as e:
            log.info("dlna: serving the file stopped (%s)", e)


class _FileServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        pass                         # a renderer hanging up is not news


class FileShare:
    """Serves one local file to the TV for as long as it is being cast."""

    def __init__(self) -> None:
        self._server: _FileServer | None = None

    def start(self, path: str) -> str:
        self.stop()
        name = quote(re.sub(r"[^\w.-]+", "_", os.path.basename(path))[-80:]
                     or "media")
        srv = _FileServer(("0.0.0.0", 0), _FileHandler)
        srv.url_path = f"/{secrets.token_urlsafe(12)}/{name}"  # type: ignore[attr-defined]
        srv.file_path = path                                    # type: ignore[attr-defined]
        srv.mime = mime_for(path)                               # type: ignore[attr-defined]
        threading.Thread(target=srv.serve_forever,
                         kwargs={"poll_interval": 0.3}, daemon=True).start()
        self._server = srv
        return (f"http://{lan_address()}:{srv.server_address[1]}"
                f"{srv.url_path}")                              # type: ignore[attr-defined]

    def stop(self) -> None:
        srv, self._server = self._server, None
        if srv is not None:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass


class _NoBridge:
    """What a DLNA cast has instead of the Chromecast converter: nothing.
    The window asks the converter questions (is it HLS, where is it, how
    much may a pause hold); a TV fetching the stream itself answers no to
    all of them."""

    hls = False
    path = None
    subs = None
    cap = 0

    def stop(self) -> None:
        return

    def lead(self) -> str:
        return ""


class DlnaManager:
    """Finds renderers and casts to one, the way ChromecastManager does."""

    def __init__(self, session: Any = None) -> None:
        self.session = session
        self.renderers: dict[str, Renderer] = {}
        self.active: Renderer | None = None
        self.bridge = _NoBridge()
        self.files = FileShare()
        self.state = ""
        self.duration = 0.0
        self.last_position = 0.0
        self.position_offset = 0.0
        self.position_at = 0.0
        self._volume: tuple[float, bool] = (0.0, False)
        self._played = False
        self._poll_gen = 0
        self._lock = threading.RLock()

    @staticmethod
    def available() -> bool:
        return True

    def knows(self, name: str) -> bool:
        return name in self.renderers or name.endswith(SUFFIX)

    def scan(self, timeout: float = 3.0) -> list[str]:
        locations = ssdp_search(timeout)
        found: dict[str, Renderer] = {}
        lock = threading.Lock()

        def look(loc: str) -> None:
            r = describe(loc, self.session)
            if r is None:
                return
            with lock:
                if any(x.udn and x.udn == r.udn for x in found.values()):
                    return              # the same TV answered twice
                name = r.name + SUFFIX
                n = 2
                while name in found:
                    name = f"{r.name} #{n}{SUFFIX}"
                    n += 1
                found[name] = r

        threads = [threading.Thread(target=look, args=(loc,), daemon=True)
                   for loc in locations]
        for t in threads:
            t.start()
        for t in threads:
            t.join(6)
        with self._lock:
            self.renderers = found
            if self.active is not None:
                # A running cast keeps its renderer under the new scan.
                same = next((r for r in found.values()
                             if r.location == self.active.location), None)
                self.active = same or self.active
        log.info("dlna: %d renderer(s) on the network%s", len(found),
                 (": " + ", ".join(sorted(found))) if found else "")
        return sorted(found)

    def _call(self, action: str, args: list[tuple[str, Any]],
              rc: bool = False, timeout: float = 6.0) -> dict[str, str]:
        r = self.active
        if r is None:
            return {}
        if rc:
            if not r.rc_url:
                return {}
            return soap(r.rc_url, r.rc_type, action, args, timeout,
                        self.session)
        return soap(r.av_url, r.av_type, action, args, timeout,
                    self.session)

    def position(self) -> float:
        at = self.position_offset + self.last_position
        if self.position_at and self.state.startswith("PLAYING"):
            at += max(0.0, time.monotonic() - self.position_at)
        return at

    def bridged(self) -> bool:
        return False

    def stale(self, _status: Any) -> bool:
        return False

    def cast(self, device_name: str, url: str, title: str,
             known_codecs: list[str] | None = None,
             audio: dict | None = None, subs: dict | None = None,
             start: float = 0.0, duration: float = 0.0,
             settle: bool = False, source: str | None = None,
             quality: str = "original", height: int = 0,
             fps: float = 0.0, dvr: bool = False) -> str:
        with self._lock:
            r = self.renderers.get(device_name)
            if r is None:
                log.info("dlna: %s not discovered yet - scanning first",
                         device_name)
                self.scan()
                r = self.renderers.get(device_name)
            if r is None:
                raise LocalizedError("cast_err_not_found", name=device_name)
        if settle:
            time.sleep(1.5)
        # The address the player itself reads: a TV takes a transport
        # stream where it may not take the provider's HLS wrapper of it.
        target = source or url
        if not target:
            raise LocalizedError("cast_err_no_address")
        if "://" not in target:
            if not os.path.isfile(target):
                raise LocalizedError("cast_err_no_address")
            target = self.files.start(target)
        else:
            self.files.stop()
        mime = mime_for(source or url)
        self._poll_gen += 1             # the last cast's watcher stands down
        self.active = r
        self.state = ""
        self._played = False
        self.last_position = self.position_offset = self.position_at = 0.0
        self.duration = 0.0 if dvr else float(duration or 0.0)
        log.info("dlna -> %s: %s (%s)", device_name, redact_url(target), mime)
        meta = didl(target, title, mime)
        try:
            self._set_uri(target, meta)
            self._call("Play", [("InstanceID", 0), ("Speed", "1")])
        except SoapError as e:
            log.info("dlna: %s refused the stream (%s)", device_name, e)
            self.active = None
            self.files.stop()
            raise LocalizedError("cast_err_dlna_refused", msg=str(e)) from e
        except requests.RequestException as e:
            log.info("dlna: %s did not answer (%s)", device_name, e)
            self.active = None
            self.files.stop()
            raise LocalizedError("cast_err_dlna_no_answer",
                                 name=device_name) from e
        if not self._wait_for_playing():
            self.stop()
            raise LocalizedError("cast_err_dlna_no_start")
        if start and start > 1:
            try:
                self.seek(start)
            except Exception as e:
                log.info("dlna: could not start part way in (%s)", e)
        self._start_polling()
        return device_name

    def _set_uri(self, url: str, meta: str) -> None:
        args = [("InstanceID", 0), ("CurrentURI", url),
                ("CurrentURIMetaData", meta)]
        try:
            self._call("SetAVTransportURI", args)
            return
        except SoapError as first:
            log.info("dlna: SetAVTransportURI refused (%s) - stopping what "
                     "plays and trying plainer", first)
        # Busy with something else (705, transport locked), or fussy about
        # the description: stop it, and try again without one.
        try:
            self._call("Stop", [("InstanceID", 0)])
        except Exception:
            pass
        self._call("SetAVTransportURI", args[:2] + [("CurrentURIMetaData", "")])

    def _transport(self) -> tuple[str, str]:
        info = self._call("GetTransportInfo", [("InstanceID", 0)])
        return (info.get("CurrentTransportState", "").upper(),
                info.get("CurrentTransportStatus", "").upper())

    def _wait_for_playing(self) -> bool:
        """Whether the TV starts within START_WAIT_S. A TV still loading
        when the time is up is given the benefit of the doubt; one that has
        stopped with an error, or never left STOPPED, is not."""
        deadline = time.monotonic() + START_WAIT_S
        last = ""
        while time.monotonic() < deadline:
            try:
                state, status = self._transport()
            except Exception as e:
                log.info("dlna: no transport state (%s)", e)
                return True             # it took the stream; trust it
            if state != last:
                log.info("dlna: renderer %s/%s", state or "?", status or "?")
                last = state
            if state == "PLAYING":
                self._played = True
                self.state = "PLAYING/None"
                self.position_at = time.monotonic()
                return True
            if status == "ERROR_OCCURRED":
                return False
            time.sleep(0.7)
        return last in ("TRANSITIONING", "PAUSED_PLAYBACK", "")

    def _start_polling(self) -> None:
        gen = self._poll_gen

        def run() -> None:
            n = 0
            while gen == self._poll_gen and self.active is not None:
                try:
                    self._poll(n)
                except Exception as e:
                    log.debug("dlna: poll failed (%s)", e)
                n += 1
                time.sleep(POLL_S)

        threading.Thread(target=run, daemon=True).start()

    def _poll(self, n: int) -> None:
        state, status = self._transport()
        if state == "PLAYING":
            self._played = True
            new = "PLAYING/None"
        elif state == "PAUSED_PLAYBACK":
            new = "PAUSED/None"
        elif state == "TRANSITIONING":
            new = "BUFFERING/None"
        elif status == "ERROR_OCCURRED":
            new = "IDLE/ERROR"
        else:
            # Stopped after playing is the end of the title; before, the TV
            # is still getting round to it.
            new = "IDLE/FINISHED" if self._played else "IDLE/None"
        if new != self.state:
            log.info("dlna %s: renderer %s", getattr(self.active, "name", "?"),
                     new)
        self.state = new
        pos = self._call("GetPositionInfo", [("InstanceID", 0)])
        # The length is the one the cast was started with: a TV reports a
        # broadcast's buffer as its "duration", and a bar measuring that
        # would be measuring nothing.
        at = parse_hms(pos.get("RelTime"))
        if at > 0 or new.startswith("PLAYING"):
            self.last_position, self.position_at = at, time.monotonic()
        if n % 4 == 0:
            self._read_volume()

    def _read_volume(self) -> None:
        if self.active is None or not self.active.rc_url:
            return
        try:
            vol = self._call("GetVolume", [("InstanceID", 0),
                                           ("Channel", "Master")], rc=True)
            mute = self._call("GetMute", [("InstanceID", 0),
                                          ("Channel", "Master")], rc=True)
            level = max(0, min(100, int(vol.get("CurrentVolume") or 0)))
            self._volume = (level / 100.0,
                            str(mute.get("CurrentMute", "0")) in ("1", "true"))
        except (SoapError, ValueError, requests.RequestException) as e:
            log.debug("dlna: volume unknown (%s)", e)

    def volume(self) -> tuple[float, bool]:
        """The TV's level as last read - never a network call, the window
        asks this on its own thread."""
        return self._volume

    def set_volume(self, level: float) -> None:
        level = max(0.0, min(1.0, float(level)))
        try:
            self._call("SetVolume", [("InstanceID", 0), ("Channel", "Master"),
                                     ("DesiredVolume", int(round(level * 100)))],
                       rc=True)
            self._volume = (level, self._volume[1])
        except Exception as e:
            log.info("dlna: the TV would not change volume (%s)", e)

    def set_muted(self, muted: bool) -> None:
        try:
            self._call("SetMute", [("InstanceID", 0), ("Channel", "Master"),
                                   ("DesiredMute", 1 if muted else 0)],
                       rc=True)
            self._volume = (self._volume[0], bool(muted))
        except Exception as e:
            log.info("dlna: the TV would not mute (%s)", e)

    def pause(self) -> None:
        try:
            self._call("Pause", [("InstanceID", 0)])
        except Exception as e:
            log.info("dlna: the TV would not pause (%s)", e)

    def resume(self) -> None:
        try:
            self._call("Play", [("InstanceID", 0), ("Speed", "1")])
        except Exception as e:
            log.info("dlna: the TV would not resume (%s)", e)

    def seek(self, seconds: float) -> None:
        if self.active is None:
            return
        try:
            self._call("Seek", [("InstanceID", 0), ("Unit", "REL_TIME"),
                                ("Target", hms(seconds))])
            self.last_position = max(0.0, seconds)
            self.position_at = time.monotonic()
        except Exception as e:
            log.info("dlna: the TV would not seek (%s)", e)

    def stop(self) -> None:
        with self._lock:
            self._poll_gen += 1
            if self.active is not None:
                try:
                    self._call("Stop", [("InstanceID", 0)], timeout=3)
                except Exception:
                    pass
                self.active = None
        self.files.stop()

    def shutdown(self) -> None:
        if self.active is not None:
            log.info("dlna: the app is closing - stopping the cast on %s",
                     self.active.name)
        self.stop()
