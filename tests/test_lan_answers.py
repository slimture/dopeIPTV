"""Answers from the network that cannot be read are passed over, and a
read ends on time however the answers come (providers/hdhomerun.py,
mdns.py, core/rtsp.py): one device's crafted packet never stops a search
for every other, nor holds it open."""
from __future__ import annotations

import socket
import struct
import threading
import time
import zlib

from dopeiptv.providers import hdhomerun

LOOP = "127.0.0.1"


def _reply(tlv: bytes) -> bytes:
    body = struct.pack(">HH", 0x0003, len(tlv)) + tlv
    return body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)


GOOD = _reply(bytes([0x01, 4]) + struct.pack(">I", 1) + bytes([0x02, 4]) + b"\x12\x34\x56\x78")
#: A tag whose length says "one more byte" as the body's last byte.
BAD = _reply(bytes([0x2A, 0x80]))


def test_a_reply_whose_tags_run_past_its_end_is_none():
    assert hdhomerun.parse_reply(BAD, "192.0.2.9") is None
    assert hdhomerun.parse_reply(_reply(bytes([0x2A, 0x85, 0x01]) + b"x"), "192.0.2.9")["server"]
    assert hdhomerun.parse_reply(GOOD, "192.0.2.9")["device_id"] == "12345678"


def _flood(packets: list[bytes], seconds: float):
    """A UDP 'tuner' that answers the discover request with *packets*, then
    keeps sending the first for *seconds*."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind((LOOP, 0))
    srv.settimeout(3)

    def answer():
        try:
            _data, addr = srv.recvfrom(2048)
        except socket.timeout:
            return
        for p in packets:
            srv.sendto(p, addr)
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                srv.sendto(packets[0], addr)
            except OSError:
                return
            time.sleep(0.01)
    t = threading.Thread(target=answer, daemon=True)
    t.start()
    return srv, t


def test_a_crafted_reply_is_passed_over_and_the_search_goes_on():
    srv, t = _flood([BAD, GOOD], 0)
    got = hdhomerun.discover(timeout=1.0, target=(LOOP, srv.getsockname()[1]))
    t.join(3)
    srv.close()
    assert [d["device_id"] for d in got] == ["12345678"]


def test_an_mdns_name_longer_than_a_name_is_refused():
    import pytest

    from dopeiptv.providers import mdns
    labels = b"".join(bytes([63]) + b"a" * 63 for _ in range(5)) + b"\x00"     # 320 octets
    pkt = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 0) + labels + struct.pack("!HHIH", 1, 1, 1, 4) + b"\x01" * 4
    with pytest.raises(ValueError):
        mdns.parse(pkt)
    ok = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 0) + bytes([4]) + b"kodi\x05local\x00" \
        + struct.pack("!HHIH", 1, 1, 1, 4) + bytes([192, 168, 1, 20])
    assert mdns.parse(ok) == [(("kodi", "local"), mdns.T_A, "192.168.1.20")]


def test_a_trickling_rtsp_answer_is_given_up_on_time():
    from dopeiptv.core import rtsp
    srv = socket.socket()
    srv.bind((LOOP, 0))
    srv.listen(1)

    def trickle():
        c, _ = srv.accept()
        c.recv(4096)
        try:
            for ch in b"RTSP/1.0 200 OK\r\nCSeq: 1\r\nServer: x" * 4:
                c.send(bytes([ch]))
                time.sleep(0.05)
        except OSError:
            pass
        c.close()
    t = threading.Thread(target=trickle, daemon=True)
    t.start()
    start = time.monotonic()
    assert rtsp.options(LOOP, srv.getsockname()[1], timeout=0.6) is None
    assert time.monotonic() - start < 2.0
    srv.close()


def test_the_search_ends_on_time_while_answers_keep_coming():
    srv, t = _flood([BAD, GOOD], 4.0)
    start = time.monotonic()
    got = hdhomerun.discover(timeout=0.8, target=(LOOP, srv.getsockname()[1]))
    took = time.monotonic() - start
    srv.close()
    t.join(5)
    assert took < 2.5 and [d["device_id"] for d in got] == ["12345678"]
