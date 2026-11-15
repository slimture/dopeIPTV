"""A transport stream made in the test (tests/test_ts_analysis.py): PAT,
PMT and SDT with their CRCs, a picture PID carrying the PCR, a sound PID,
null packets - at a fixed bitrate, so where each packet sits in time is
known exactly."""

from __future__ import annotations

PACKET = 188


def _crc_table() -> list[int]:
    out = []
    for i in range(256):
        c = i << 24
        for _ in range(8):
            c = ((c << 1) ^ 0x04C11DB7) if c & 0x80000000 else (c << 1)
        out.append(c & 0xFFFFFFFF)
    return out


_TABLE = _crc_table()


def crc32_mpeg_slow(data: bytes) -> int:
    """CRC-32/MPEG-2 bit by bit (a table), to check the fast one against."""
    c = 0xFFFFFFFF
    for b in data:
        c = ((c << 8) & 0xFFFFFFFF) ^ _TABLE[((c >> 24) ^ b) & 0xFF]
    return c


def section(table_id: int, ext: int, body: bytes, version: int = 0) -> bytes:
    """A long-form PSI section with its CRC."""
    length = 5 + len(body) + 4
    head = bytes([table_id, 0xB0 | (length >> 8), length & 0xFF,
                  ext >> 8, ext & 0xFF, 0xC1 | (version << 1), 0x00, 0x00])
    data = head + body
    return data + crc32_mpeg_slow(data).to_bytes(4, "big")


def pat(programs: dict[int, int], tsid: int = 0x0042) -> bytes:
    body = b"".join(bytes([n >> 8, n & 0xFF, 0xE0 | (pid >> 8), pid & 0xFF])
                    for n, pid in programs.items())
    return section(0x00, tsid, body)


def pmt(number: int, pcr_pid: int, streams: list[tuple[int, int, bytes]],
        program_info: bytes = b"") -> bytes:
    body = bytes([0xE0 | (pcr_pid >> 8), pcr_pid & 0xFF,
                  0xF0 | (len(program_info) >> 8), len(program_info) & 0xFF]) + program_info
    for stype, pid, descs in streams:
        body += bytes([stype, 0xE0 | (pid >> 8), pid & 0xFF,
                       0xF0 | (len(descs) >> 8), len(descs) & 0xFF]) + descs
    return section(0x02, number, body)


def sdt(service_id: int, name: str, provider: str, tsid: int = 0x0042,
        onid: int = 0x0001) -> bytes:
    p, n = provider.encode("latin-1"), name.encode("latin-1")
    desc = bytes([0x48, 3 + len(p) + len(n), 0x01, len(p)]) + p + bytes([len(n)]) + n
    loop = bytes([service_id >> 8, service_id & 0xFF, 0xFC,
                  0x80 | (len(desc) >> 8), len(desc) & 0xFF]) + desc
    body = bytes([onid >> 8, onid & 0xFF, 0xFF]) + loop
    return section(0x42, tsid, body)


def packet(pid: int, cc: int, payload: bytes = b"", pusi: bool = False,
           pcr: int | None = None, disc: bool = False, scrambled: int = 0,
           tei: bool = False, adaptation_only: bool = False) -> bytes:
    """One 188-byte packet: *pcr* in 27 MHz ticks; a short payload padded
    out with stuffing in the adaptation field, as a muxer does."""
    b1 = (0x80 if tei else 0) | (0x40 if pusi else 0) | (pid >> 8)
    body = b""
    if pcr is not None or disc:
        body = bytes([(0x80 if disc else 0) | (0x10 if pcr is not None else 0)])
        if pcr is not None:
            base, ext = divmod(pcr, 300)
            body += bytes([(base >> 25) & 0xFF, (base >> 17) & 0xFF, (base >> 9) & 0xFF,
                           (base >> 1) & 0xFF, ((base & 1) << 7) | 0x7E | (ext >> 8), ext & 0xFF])
    room = PACKET - 4
    if adaptation_only:
        payload = b""
        af = bytes([room - 1]) + (body or b"\x00") + b"\xFF" * (room - 1 - max(1, len(body)))
        afc = 0x20
    else:
        payload = payload[:room - (1 + len(body) if body else 0)]
        need = room - len(payload)
        if need == 0:
            af, afc = b"", 0x10
        elif need == 1:
            af, afc = b"\x00", 0x30
        else:
            fill = body or b"\x00"
            af = bytes([need - 1]) + fill + b"\xFF" * (need - 1 - len(fill))
            afc = 0x30
    b3 = (scrambled << 6) | afc | (cc & 0x0F)
    out = bytes([0x47, b1, pid & 0xFF, b3]) + af + payload
    assert len(out) == PACKET, len(out)
    return out


def table_packet(pid: int, cc: int, sec: bytes) -> bytes:
    return packet(pid, cc, b"\x00" + sec + b"\xFF" * (PACKET - 5 - len(sec)), pusi=True)


def pes_head(stream_id: int, pts: int) -> bytes:
    p = pts & ((1 << 33) - 1)
    pts_b = bytes([0x21 | ((p >> 29) & 0x0E), (p >> 22) & 0xFF, ((p >> 14) & 0xFE) | 1,
                   (p >> 7) & 0xFF, ((p << 1) & 0xFE) | 1])
    return b"\x00\x00\x01" + bytes([stream_id, 0, 0, 0x80, 0x80, 5]) + pts_b


class Gen:
    """A single-programme stream at *bitrate*: picture on 0x100 (H.264,
    the PCR every *pcr_ms*), sound on 0x101 (AAC), PAT and PMT every
    *table_ms*, SDT once a second, the rest null packets."""

    VIDEO, AUDIO, PMT_PID = 0x100, 0x101, 0x1000

    def __init__(self, bitrate: float = 8e6, pcr_ms: float = 20.0, table_ms: float = 100.0,
                 name: str = "Test One", provider: str = "dope", video_share: float = 0.75,
                 audio_share: float = 0.05, pcr_start: int = 0) -> None:
        self.dt = PACKET * 8 / bitrate
        self.pcr_ms = pcr_ms
        self.table_ms = table_ms
        self.video_share = video_share
        self.audio_share = audio_share
        self.cc: dict[int, int] = {}
        self.k = 0
        self.k0 = 0
        self.t0 = 0.0
        self.pcr_start = pcr_start
        self.pat = pat({1: self.PMT_PID})
        self.pmt = pmt(1, self.VIDEO, [
            (0x1B, self.VIDEO, b""),
            (0x0F, self.AUDIO, bytes([0x0A, 4]) + b"swe\x00"),
            (0x06, 0x102, bytes([0x59, 8]) + b"swe\x10\x00\x01\x00\x01"),
        ])
        self.sdt = sdt(1, name, provider)
        self._next = {"pat": 0.0, "pmt": 0.0, "sdt": 0.0, "pcr": 0.0, "vpes": 0.0, "apes": 0.0}
        self._acc_v = 0.0
        self._acc_a = 0.0
        self.skip: set[str] = set()

    def _cc(self, pid: int) -> int:
        c = self.cc.get(pid, -1)
        c = (c + 1) & 0x0F
        self.cc[pid] = c
        return c

    def now(self) -> float:
        return self.t0 + (self.k - self.k0) * self.dt

    def set_bitrate(self, bitrate: float) -> None:
        """From here on at *bitrate* (the picture fills the rest)."""
        self.t0, self.k0 = self.now(), self.k
        self.dt = PACKET * 8 / bitrate

    def pcr_at(self, t: float) -> int:
        return (self.pcr_start + int(round(t * 27_000_000))) % ((1 << 33) * 300)

    def next_packet(self) -> bytes:
        t = self.now()
        self.k += 1
        nx = self._next
        if t >= nx["pat"]:
            nx["pat"] = t + self.table_ms / 1000
            if "pat" not in self.skip:
                return table_packet(0, self._cc(0), self.pat)
        if t >= nx["pmt"]:
            nx["pmt"] = t + self.table_ms / 1000
            if "pmt" not in self.skip:
                return table_packet(self.PMT_PID, self._cc(self.PMT_PID), self.pmt)
        if t >= nx["sdt"]:
            nx["sdt"] = t + 1.0
            return table_packet(0x11, self._cc(0x11), self.sdt)
        if t >= nx["pcr"]:
            nx["pcr"] = t + self.pcr_ms / 1000
            pusi = t >= nx["vpes"]
            if pusi:
                nx["vpes"] = t + 0.04
            body = pes_head(0xE0, int(t * 90000)) if pusi else b"\x00" * 170
            return packet(self.VIDEO, self._cc(self.VIDEO), body, pusi=pusi, pcr=self.pcr_at(t))
        self._acc_a += self.audio_share
        if self._acc_a >= 1.0:
            self._acc_a -= 1.0
            pusi = t >= nx["apes"]
            if pusi:
                nx["apes"] = t + 0.024
            body = pes_head(0xC0, int(t * 90000)) if pusi else b"\x11" * 184
            return packet(self.AUDIO, self._cc(self.AUDIO), body + b"\x11" * 184, pusi=pusi)
        self._acc_v += self.video_share
        if self._acc_v >= 1.0:
            self._acc_v -= 1.0
            pusi = t >= nx["vpes"]
            if pusi:
                nx["vpes"] = t + 0.04
            body = pes_head(0xE0, int(t * 90000)) if pusi else b""
            return packet(self.VIDEO, self._cc(self.VIDEO), body + b"\x22" * 184, pusi=pusi)
        return packet(0x1FFF, 0, b"\xFF" * 184)

    def take(self, n: int) -> list[bytes]:
        return [self.next_packet() for _ in range(n)]

    def seconds(self, secs: float) -> bytes:
        return b"".join(self.take(int(round(secs / self.dt))))
