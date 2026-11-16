"""Server mode as an HDHomeRun tuner, for Plex, Jellyfin, Emby and Channels
DVR: the HTTP API they read (``discover.json``, ``lineup.json``,
``lineup_status.json``, ``device.xml``) and the answer to their discovery
broadcast on UDP port 65001, so they find the server by themselves.

The packets are libhdhomerun's (hdhomerun_pkt.h, hdhomerun_discover.c): a
frame of type (u16, big-endian), payload length (u16) and the payload's
tags - each a tag byte, its length (one byte up to 127, else two: the low
seven bits with 0x80, then the rest) and its value - closed by the CRC32 of
it all, little-endian. A discover request (type 2) names the device type
(one or more DEVICE_TYPE tags, or MULTI_TYPE's list; 0xFFFFFFFF any) and
id (0xFFFFFFFF any); the answer (type 3) says the type (1: a tuner), the
id, the tuner count, the base address and the lineup's. providers/hdhomerun.py
reads them as a client; this is the device's side.

A device id is eight hex digits whose checksum libhdhomerun checks
(hdhomerun_discover_validate_device_id): made once per install, kept in
the settings, never in a backup or a sync.

The names Plex and Jellyfin know an emulator by - model HDTC-2US, firmware
hdhomeruntc_atsc - are the ones every HDHomeRun emulator (Threadfin, xTeVe,
Telly) gives: a client shows a model it knows as a network tuner it can
stream from over HTTP. Nothing here is fetched from SiliconDust.

Only home addresses get an answer (core/home_net.py); none of these needs
a token - Plex cannot send one - which the settings say.
"""
from __future__ import annotations

import secrets
import socket
import struct
import sys
import threading
import zlib
from collections.abc import Callable
from typing import Any
from xml.sax.saxutils import escape

from . import home_net
from .log import log

DISCOVER_PORT = 65001
TYPE_DISCOVER_REQ = 0x0002
TYPE_DISCOVER_RPY = 0x0003
TAG_DEVICE_TYPE = 0x01
TAG_DEVICE_ID = 0x02
TAG_TUNER_COUNT = 0x10
TAG_LINEUP_URL = 0x27
TAG_BASE_URL = 0x2A
TAG_MULTI_TYPE = 0x2D
DEVICE_TYPE_TUNER = 0x00000001
WILDCARD = 0xFFFFFFFF

FRIENDLY_NAME = "dopeIPTV"
MANUFACTURER = "dopeIPTV"
MODEL = "HDTC-2US"
FIRMWARE_NAME = "hdhomeruntc_atsc"
FIRMWARE_VERSION = "20150826"

_LOOKUP = (0xA, 0x5, 0xF, 0x6, 0x7, 0xC, 0x1, 0xB, 0x9, 0x2, 0x8, 0xD, 0x4, 0x3, 0xE, 0x0)


# -- the device id -------------------------------------------------------------------

def _checksum(device_id: int) -> int:
    c = 0
    for shift in (28, 20, 12, 4):
        c ^= _LOOKUP[(device_id >> shift) & 0x0F]
    for shift in (24, 16, 8, 0):
        c ^= (device_id >> shift) & 0x0F
    return c


def valid_device_id(text: str) -> bool:
    """Whether *text* is an id libhdhomerun takes: eight hex digits whose
    checksum comes to zero."""
    try:
        value = int(text, 16)
    except (TypeError, ValueError):
        return False
    return len(text) == 8 and value not in (0, WILDCARD) and _checksum(value) == 0


def new_device_id() -> str:
    """A random id with a good checksum (its last digit makes it so)."""
    while True:
        head = secrets.randbits(28) << 4
        dev = head | _checksum(head)
        text = f"{dev:08X}"
        if valid_device_id(text):
            return text


# -- the HTTP API ----------------------------------------------------------------------

def discover(device_id: str, tuners: int, base: str, name: str = FRIENDLY_NAME) -> dict[str, Any]:
    """``/discover.json``: what Plex, Jellyfin and Emby read first."""
    return {"FriendlyName": name, "Manufacturer": MANUFACTURER, "ModelNumber": MODEL,
            "FirmwareName": FIRMWARE_NAME, "FirmwareVersion": FIRMWARE_VERSION,
            "DeviceID": device_id, "TunerCount": max(1, int(tuners)),
            "BaseURL": base, "LineupURL": f"{base}/lineup.json"}


def lineup(channels: list[dict[str, Any]], base: str) -> list[dict[str, Any]]:
    """``/lineup.json``: GuideNumber, GuideName and URL per channel (the
    tuner's own address, ``/auto/v<number>``). *channels*: {"number",
    "name", "hd"?}."""
    out = []
    for ch in channels:
        number = str(ch["number"])
        row: dict[str, Any] = {"GuideNumber": number, "GuideName": str(ch.get("name") or number),
                               "URL": f"{base}/auto/v{number}"}
        if ch.get("hd"):
            row["HD"] = 1
        out.append(row)
    return out


def lineup_status() -> dict[str, Any]:
    """``/lineup_status.json``: no scan running, none needed - the lineup is
    always the sources' channels."""
    return {"ScanInProgress": 0, "ScanPossible": 1, "Source": "Cable", "SourceList": ["Cable"]}


def device_xml(device_id: str, base: str, name: str = FRIENDLY_NAME) -> str:
    """``/device.xml``: the UPnP description some clients read."""
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<root xmlns="urn:schemas-upnp-org:device-1-0">\n'
            '<specVersion><major>1</major><minor>0</minor></specVersion>\n'
            f'<URLBase>{escape(base)}</URLBase>\n'
            '<device>\n'
            '<deviceType>urn:schemas-upnp-org:device:MediaServer:1</deviceType>\n'
            f'<friendlyName>{escape(name)}</friendlyName>\n'
            f'<manufacturer>{escape(MANUFACTURER)}</manufacturer>\n'
            f'<modelName>{MODEL}</modelName>\n'
            f'<modelNumber>{MODEL}</modelNumber>\n'
            f'<serialNumber>{escape(device_id)}</serialNumber>\n'
            f'<UDN>uuid:{escape(device_id)}</UDN>\n'
            '</device>\n'
            '</root>\n')


# -- the discovery packets --------------------------------------------------------------

def _tlv(tag: int, value: bytes) -> bytes:
    n = len(value)
    length = bytes([n]) if n <= 127 else bytes([(n & 0x7F) | 0x80, n >> 7])
    return bytes([tag]) + length + value


def frame(ptype: int, payload: bytes) -> bytes:
    body = struct.pack(">HH", ptype, len(payload)) + payload
    return body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)


def tags(data: bytes) -> tuple[int, dict[int, list[bytes]]] | None:
    """A frame's type and its tags (each tag's values, in order), or None
    for one whose CRC or lengths do not hold."""
    if len(data) < 8:
        return None
    body, crc = data[:-4], struct.unpack("<I", data[-4:])[0]
    if zlib.crc32(body) & 0xFFFFFFFF != crc:
        return None
    ptype, length = struct.unpack(">HH", body[:4])
    if 4 + length != len(body):
        return None
    out: dict[int, list[bytes]] = {}
    pos = 4
    while pos + 2 <= len(body):
        tag, ln = body[pos], body[pos + 1]
        pos += 2
        if ln & 0x80:
            if pos >= len(body):
                return None
            ln = (ln & 0x7F) | (body[pos] << 7)
            pos += 1
        if pos + ln > len(body):
            return None
        out.setdefault(tag, []).append(body[pos:pos + ln])
        pos += ln
    return ptype, out


def wants(data: bytes, device_id: str) -> bool:
    """Whether *data* is a discover request this tuner answers: any type or
    a tuner, any id or this one."""
    got = tags(data)
    if got is None or got[0] != TYPE_DISCOVER_REQ:
        return False
    found = got[1]
    types = [struct.unpack(">I", v)[0] for v in found.get(TAG_DEVICE_TYPE, []) if len(v) == 4]
    for multi in found.get(TAG_MULTI_TYPE, []):
        types += [struct.unpack(">I", multi[i:i + 4])[0] for i in range(0, len(multi) - 3, 4)]
    if types and not any(t in (WILDCARD, DEVICE_TYPE_TUNER) for t in types):
        return False
    ids = [struct.unpack(">I", v)[0] for v in found.get(TAG_DEVICE_ID, []) if len(v) == 4]
    mine = int(device_id, 16)
    return not ids or any(i in (WILDCARD, mine) for i in ids)


def reply(device_id: str, tuners: int, base: str) -> bytes:
    """The discover answer: a tuner, its id, how many tuners, where its HTTP
    API and its lineup are."""
    payload = (_tlv(TAG_DEVICE_TYPE, struct.pack(">I", DEVICE_TYPE_TUNER))
               + _tlv(TAG_DEVICE_ID, struct.pack(">I", int(device_id, 16)))
               + _tlv(TAG_TUNER_COUNT, bytes([max(1, min(255, int(tuners)))]))
               + _tlv(TAG_BASE_URL, base.encode("utf-8"))
               + _tlv(TAG_LINEUP_URL, f"{base}/lineup.json".encode("utf-8")))
    return frame(TYPE_DISCOVER_RPY, payload)


def address_towards(ip: str) -> str:
    """This computer's address on the way to *ip* (nothing is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((ip, 9))
        return str(s.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class Responder:
    """Answers discover broadcasts on UDP 65001 while it runs. *answer(ip)*
    gives (device id, tuners, base address) for the asker at *ip* - None
    to stay quiet."""

    def __init__(self, answer: Callable[[str], tuple[str, int, str] | None],
                 port: int = DISCOVER_PORT, host: str = "") -> None:
        self._answer = answer
        self.port, self.host = port, host
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.error = ""

    def start(self) -> bool:
        """Listen; False (and the reason in *error*) when the port is held."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if sys.platform != "win32":
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.host, self.port))
            s.settimeout(0.5)
        except OSError as e:
            s.close()
            self.error = f"{type(e).__name__}: {e}"
            log.warning("hdhomerun discovery: UDP %d not open (%s) - the server's address "
                        "has to be typed in Plex or Jellyfin", self.port, e)
            return False
        self._sock = s
        self.port = s.getsockname()[1]
        self._thread = threading.Thread(target=self._run, name="hdhr-discover", daemon=True)
        self._thread.start()
        log.info("hdhomerun discovery: answering on UDP %d", self.port)
        return True

    def _run(self) -> None:
        s = self._sock
        while s is not None and not self._stop.is_set():
            try:
                data, (ip, port) = s.recvfrom(2048)
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                break
            try:
                self._one(s, data, str(ip), int(port))
            except Exception as e:
                log.debug("hdhomerun discovery: %s", e)

    def _one(self, s: socket.socket, data: bytes, ip: str, port: int) -> None:
        if not home_net.is_home_client(ip):
            return
        got = self._answer(ip)
        if got is None:
            return
        device_id, tuners, base = got
        if wants(data, device_id):
            s.sendto(reply(device_id, tuners, base), (ip, port))
            log.debug("hdhomerun discovery: answered %s", ip)

    def stop(self) -> None:
        self._stop.set()
        s, self._sock = self._sock, None
        if s is not None:
            try:
                s.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(2.0)
