"""A small DVB teletext transport stream generator for the tests.

Pages are built as broadcasters send them (ETS 300 706 packets in EN 300
472 PES: data_identifier 0x10, data units of 44 bytes, every byte of the
packet sent least significant bit first), wrapped in 188-byte TS packets
with a PAT and a PMT that carries the teletext descriptor. Independent of
the decoder under test: its own Hamming 8/4 and 24/18 encoders.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence

# Hamming 8/4 code words for the nibbles 0..15 (bit 0 sent first).
HAM8 = (0x15, 0x02, 0x49, 0x5E, 0x64, 0x73, 0x38, 0x2F,
        0xD0, 0xC7, 0x8C, 0x9B, 0xA1, 0xB6, 0xFD, 0xEA)


def ham8(n: int) -> int:
    return HAM8[n & 15]


def ham24(value: int) -> bytes:
    """18 data bits as a Hamming 24/18 triplet (ETS 300 706, 8.3): data at
    B3, B5-B7, B9-B15, B17-B23, P1..P5 at B1, B2, B4, B8, B16 and P6 at B24,
    every check odd."""
    bits = [0] * 25                      # 1-based
    data_pos = [3, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15,
                17, 18, 19, 20, 21, 22, 23]
    for i, pos in enumerate(data_pos):
        bits[pos] = (value >> i) & 1
    for k, p in enumerate((1, 2, 4, 8, 16)):
        s = 0
        for pos in range(1, 24):
            if pos != p and pos & (1 << k):
                s ^= bits[pos]
        bits[p] = s ^ 1
    bits[24] = (sum(bits[1:24]) & 1) ^ 1
    t = 0
    for pos in range(1, 25):
        t |= bits[pos] << (pos - 1)
    return bytes((t & 0xFF, (t >> 8) & 0xFF, (t >> 16) & 0xFF))


def odd(c: int) -> int:
    c &= 0x7F
    return c | (0x80 if bin(c).count("1") % 2 == 0 else 0)


def text(s: str | bytes, width: int = 40) -> bytes:
    raw = s.encode("latin-1") if isinstance(s, str) else bytes(s)
    raw = raw[:width].ljust(width, b" ")
    return bytes(odd(c) for c in raw)


def mrag(magazine: int, row: int) -> bytes:
    addr = (magazine & 7) | (row << 3)
    return bytes((ham8(addr & 15), ham8(addr >> 4)))


def header(page: int, sub: int = 0, title: str | bytes = "", *,
           erase: bool = True, subtitle: bool = False, newsflash: bool = False,
           national: int = 0, serial: bool = False,
           suppress: bool = False, inhibit: bool = False) -> bytes:
    """Packet X/0 of *page* (hex-coded like 0x100), subcode *sub*. *national*:
    C12-C14 as the subset's number (C12 its top bit)."""
    mag = (page >> 8) & 7
    tens, units = (page >> 4) & 15, page & 15
    s1, s2, s3, s4 = sub & 15, (sub >> 4) & 7, (sub >> 8) & 15, (sub >> 12) & 3
    c4, c5, c6 = int(erase), int(newsflash), int(subtitle)
    c7, c10, c11 = int(suppress), int(inhibit), int(serial)
    c12, c13, c14 = (national >> 2) & 1, (national >> 1) & 1, national & 1
    body = bytes((ham8(units), ham8(tens), ham8(s1), ham8(s2 | c4 << 3),
                  ham8(s3), ham8(s4 | c5 << 2 | c6 << 3),
                  ham8(c7 | c10 << 3),
                  ham8(c11 | c12 << 1 | c13 << 2 | c14 << 3)))
    return mrag(mag, 0) + body + text(title, 32)


def row(page: int, y: int, content: str | bytes) -> bytes:
    return mrag((page >> 8) & 7, y) + text(content)


def link_bytes(page: int, sub: int, magazine: int) -> bytes:
    m = ((page >> 8) & 7) ^ (magazine & 7)
    return bytes((ham8(page & 15), ham8((page >> 4) & 15), ham8(sub & 15),
                  ham8(((sub >> 4) & 7) | (m & 1) << 3), ham8((sub >> 8) & 15),
                  ham8(((sub >> 12) & 3) | ((m >> 1) & 3) << 2)))


def fastext(page: int, links: Sequence[int], show_row24: bool = True) -> bytes:
    """Packet X/27/0: red, green, yellow, cyan, next and index links."""
    mag = (page >> 8) & 7
    out = bytearray(mrag(mag, 27))
    out.append(ham8(0))                              # designation code 0
    pages = list(links) + [0x8FF] * (6 - len(links))
    for p in pages[:6]:
        out += link_bytes(p, 0x3F7F, mag)
    out.append(ham8(0x08 if show_row24 else 0))      # link control
    out += b"\x00\x00"                               # CRC (not checked)
    return bytes(out)


def x28_designation(page: int, g0: int) -> bytes:
    """Packet X/28/0 format 1 with the default G0 set *g0* (7 bits)."""
    mag = (page >> 8) & 7
    out = bytearray(mrag(mag, 28))
    out.append(ham8(0))
    # Triplet 1: page function and coding (0), then the G0/G2 designation
    # in bits 7..13.
    out += ham24((g0 & 0x7F) << 7)
    while len(out) < 42:
        out += ham24(0)
    return bytes(out[:42])


def m29_designation(magazine: int, g0: int) -> bytes:
    out = bytearray(mrag(magazine, 29))
    out.append(ham8(0))
    out += ham24((g0 & 0x7F) << 7)
    while len(out) < 42:
        out += ham24(0)
    return bytes(out[:42])


def page_packets(page: int, rows: dict[int, str | bytes], sub: int = 0,
                 title: str = "", links: Sequence[int] | None = None,
                 **flags) -> list[bytes]:
    out = [header(page, sub, title, **flags)]
    if links is not None:
        out.append(fastext(page, links))
    for y in sorted(rows):
        out.append(row(page, y, rows[y]))
    return out


# -- EN 300 472 PES and the transport stream ---------------------------------

REV = bytes(int(f"{i:08b}"[::-1], 2) for i in range(256))


def data_unit(packet42: bytes, unit_id: int = 0x02, line: int = 7) -> bytes:
    assert len(packet42) == 42
    return (bytes((unit_id, 0x2C, 0xE0 | line, 0xE4))
            + bytes(REV[b] for b in packet42))


def pes(packets: Iterable[bytes], pts: int = 0, unit_id: int = 0x02,
        data_identifier: int = 0x10) -> bytes:
    units = b"".join(data_unit(p, unit_id) for p in packets)
    head = bytes((0x84, 0x80, 0x24)) + _pts(pts) + b"\xff" * 31
    body = bytes((data_identifier,)) + units
    total = 6 + len(head) + len(body)
    while total % 184:
        pad = b"\xff\x2c" + b"\xff" * 44
        body += pad
        total += len(pad)
    length = total - 6
    return b"\x00\x00\x01\xbd" + length.to_bytes(2, "big") + head + body


def _pts(pts: int) -> bytes:
    return bytes((0x21 | ((pts >> 29) & 0x0E), (pts >> 22) & 0xFF,
                  ((pts >> 14) & 0xFE) | 1, (pts >> 7) & 0xFF,
                  ((pts << 1) & 0xFE) | 1))


def crc32(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for b in data:
        crc ^= b << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) if crc & 0x80000000 else crc << 1
            crc &= 0xFFFFFFFF
    return crc


class Muxer:
    """TS packets for one program: PAT, PMT and the PIDs' PES."""

    def __init__(self, pmt_pid: int = 0x1000, program: int = 1) -> None:
        self.pmt_pid = pmt_pid
        self.program = program
        self.cc: dict[int, int] = {}

    def _ts(self, pid: int, payload: bytes, pusi: bool) -> bytes:
        cc = self.cc.get(pid, 0)
        self.cc[pid] = (cc + 1) & 15
        head = bytes((0x47, (0x40 if pusi else 0) | (pid >> 8), pid & 0xFF))
        if len(payload) >= 184:
            return head + bytes((0x10 | cc,)) + payload[:184]
        stuff = 184 - len(payload)
        if stuff == 1:
            af = b"\x00"
        else:
            af = bytes((stuff - 1, 0x00)) + b"\xff" * (stuff - 2)
        return head + bytes((0x30 | cc,)) + af + payload

    def pes_packets(self, pid: int, data: bytes) -> bytes:
        out = bytearray()
        first = True
        while data:
            chunk, data = data[:184], data[184:]
            out += self._ts(pid, chunk, first)
            first = False
        return bytes(out)

    def section(self, pid: int, table: bytes) -> bytes:
        data = b"\x00" + table + crc32(table).to_bytes(4, "big")
        return self._ts(pid, data.ljust(184, b"\xff"), True)

    def pat(self) -> bytes:
        body = (self.program.to_bytes(2, "big")
                + (0xE000 | self.pmt_pid).to_bytes(2, "big"))
        sec_len = 5 + len(body) + 4
        table = (bytes((0x00, 0xB0 | (sec_len >> 8), sec_len & 0xFF))
                 + b"\x00\x01\xc1\x00\x00" + body)
        return self.section(0, table)

    def pmt(self, streams: Sequence[tuple[int, int, bytes]],
            pcr_pid: int = 0x1FFF) -> bytes:
        """*streams*: (stream_type, pid, descriptors)."""
        es = b""
        for stype, pid, desc in streams:
            es += (bytes((stype,)) + (0xE000 | pid).to_bytes(2, "big")
                   + (0xF000 | len(desc)).to_bytes(2, "big") + desc)
        sec_len = 9 + len(es) + 4
        table = (bytes((0x02, 0xB0 | (sec_len >> 8), sec_len & 0xFF))
                 + self.program.to_bytes(2, "big") + b"\xc1\x00\x00"
                 + (0xE000 | pcr_pid).to_bytes(2, "big") + b"\xf0\x00" + es)
        return self.section(self.pmt_pid, table)


def teletext_descriptor(entries: Sequence[tuple[str, int, int]]) -> bytes:
    """Descriptor 0x56: (language, teletext_type, page as 0x199)."""
    body = b""
    for lang, kind, page in entries:
        mag = (page >> 8) & 7
        body += lang.encode("ascii")[:3] + bytes(((kind << 3) | mag,
                                                   page & 0xFF))
    return bytes((0x56, len(body))) + body


def subtitling_descriptor(entries: Sequence[tuple[str, int, int, int]]) -> bytes:
    """Descriptor 0x59: (language, subtitling_type, composition, ancillary)."""
    body = b""
    for lang, kind, comp, anc in entries:
        body += (lang.encode("ascii")[:3] + bytes((kind,))
                 + comp.to_bytes(2, "big") + anc.to_bytes(2, "big"))
    return bytes((0x59, len(body))) + body


def stream(pages: Sequence[Sequence[bytes]], pid: int = 0x500,
           descriptor: bytes | None = None, unit_id: int = 0x02,
           extra: Sequence[tuple[int, int, bytes]] = (),
           tail: Sequence[bytes] = (), step: int = 3600) -> bytes:
    """A whole TS: PAT, PMT, then each page's packets in a PES of its own,
    then *tail* (packets that end the last page); *step* (90 kHz) of PTS
    between the PES."""
    mux = Muxer()
    desc = descriptor if descriptor is not None else teletext_descriptor(
        [("swe", 1, 0x100), ("swe", 2, 0x199)])
    out = bytearray(mux.pat())
    out += mux.pmt([(0x06, pid, desc), *extra])
    pts = 90000
    groups = [*pages, tail] if tail else list(pages)
    for packets in groups:
        packets = list(packets)
        while packets:
            chunk, packets = packets[:16], packets[16:]
            out += mux.pes_packets(pid, pes(chunk, pts, unit_id))
            pts += step
    return bytes(out)


def filler(magazine: int = 1) -> bytes:
    """A time-filling header (page FF) - ends the page before it."""
    return header((magazine << 8) | 0xFF, 0, "")


def demo_page() -> list[bytes]:
    """Page 100 as a channel might send it: a double-height title on blue,
    mosaic bands (contiguous and separated), an index in colours, a
    flashing word, Swedish letters (national option 2), a concealed answer
    and Fastext links."""
    mos = bytes((0x14,)) + bytes((0x7F, 0x2C, 0x7F, 0x70, 0x7F, 0x23, 0x7F, 0x7C) * 4)
    sep = bytes((0x16, 0x1A)) + bytes((0x7F, 0x35, 0x6A, 0x7F) * 8)
    rows: dict[int, str | bytes] = {
        1: b"\x04\x1d\x0d\x03 dopeIPTV Text           100 ",
        4: mos,
        5: sep,
        7: b"\x03 News ......................... 101",
        8: b"\x06 Sport ........................ 200",
        9: b"\x02 Weather ...................... 400",
        10: b"\x05 TV guide ..................... 600",
        11: b"\x07 Subtitles .................... 199",
        13: b"\x01\x1d\x07 Live now \x1c\x03 R\x7bksm\x7crg\x7ds p\x7d menyn ",
        15: b"\x08\x01 Breaking:\x09\x07 the page flashes ",
        17: b"\x07 Quiz: who won?\x02\x18 Sverige ",
        24: b"\x01 Nyheter  \x02 Sport    \x03 V\x7bder    \x06 Index",
    }
    return page_packets(0x100, rows, title=b"SVT Text   ons 15 nov  21:42:07",
                        links=[0x101, 0x200, 0x400, 0x100, 0x8FF, 0x100], national=2)
