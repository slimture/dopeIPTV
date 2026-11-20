"""Teletext from a DVB transport stream, in plain Python.

The chain, as the standards have it:

- TS packets (188 bytes) of the program's PAT and PMT, and of the teletext
  PID the PMT names (stream_type 0x06 with the teletext descriptor 0x56, or
  the VBI descriptor 0x46);
- their PES (EN 300 472: private_stream_1, data_identifier 0x10-0x1F),
  whose data units 0x02 and 0x03 each carry one teletext packet of 42
  bytes, every byte sent least significant bit first;
- the packets themselves (ETS 300 706): the magazine and row in Hamming
  8/4, the page header's number, subcode and control bits in Hamming 8/4,
  display rows in 7-bit characters with odd parity, the Fastext links of
  X/27/0 in Hamming 8/4, the character set designations of X/28/0 and
  M/29/0 in Hamming 24/18;
- pages put together per magazine (parallel or serial transmission), a row
  sent again updating the page unless its header says erase, each subpage
  kept apart.

What it gives back is the page as cells (``render``): 40 x 25, each with
its character or its 2 x 3 mosaic, the teletext colours, double height,
flash, conceal and boxes, with the national option subsets of the Latin
G0 set (English, German, Swedish/Finnish/Hungarian, Italian, French,
Portuguese/Spanish, Czech/Slovak, Polish, Turkish and the rest of their
groups) and the three Cyrillic sets. Greek, Arabic and Hebrew pages, the
level 2.5 enhancements (X/26) and DRCS are not drawn.

``StoreFeed`` reads the app's own rewind buffer (core/dvr_store.py) into a
decoder on a thread of its own - never a connection of its own to the
account.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from .log import log

TS = 188
#: The page numbers a viewer can show: 100-899, as their digits are sent.
FIRST_PAGE, LAST_PAGE = 0x100, 0x899
INDEX_PAGE = 0x100
ROWS, COLS = 25, 40
#: Black, red, green, yellow, blue, magenta, cyan, white.
PALETTE: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0), (255, 0, 0), (0, 255, 0), (255, 255, 0),
    (0, 0, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255))
#: Subpages kept per page; a page that counts its subcode as a clock would
#: otherwise grow without end.
MAX_SUBPAGES = 80
#: The teletext_type values of descriptor 0x56.
TYPE_INITIAL, TYPE_SUBTITLE, TYPE_INFO, TYPE_SCHEDULE, TYPE_HOH = 1, 2, 3, 4, 5

# -- error protection -------------------------------------------------------------

REVERSE: bytes = bytes(int(f"{i:08b}"[::-1], 2) for i in range(256))
_HAM8 = (0x15, 0x02, 0x49, 0x5E, 0x64, 0x73, 0x38, 0x2F,
         0xD0, 0xC7, 0x8C, 0x9B, 0xA1, 0xB6, 0xFD, 0xEA)


def _ham8_inverse() -> tuple[int, ...]:
    out = []
    for b in range(256):
        hit = [n for n, code in enumerate(_HAM8) if bin(code ^ b).count("1") <= 1]
        out.append(hit[0] if hit else -1)
    return tuple(out)


HAM8_INV = _ham8_inverse()
ODD = tuple(bin(b).count("1") & 1 == 1 for b in range(256))
# Bit masks of the five Hamming 24/18 checks: each covers the positions
# (1-based) that have its bit set.
_H24_CHECKS = tuple(sum(1 << (pos - 1) for pos in range(1, 24) if pos & (1 << k))
                    for k in range(5))
_H24_DATA = (3, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 17, 18, 19, 20, 21, 22, 23)


def unham8(b: int) -> int:
    """A Hamming 8/4 byte (bit 0 sent first): its nibble, a single bit
    error corrected; -1 for two."""
    return HAM8_INV[b & 0xFF]


def unham16(b0: int, b1: int) -> int:
    """Two Hamming 8/4 bytes: the first one's nibble low; -1 on an error."""
    lo, hi = HAM8_INV[b0 & 0xFF], HAM8_INV[b1 & 0xFF]
    return -1 if lo < 0 or hi < 0 else lo | hi << 4


def unham24(b0: int, b1: int, b2: int) -> int:
    """A Hamming 24/18 triplet: its 18 data bits, one bit error corrected;
    -1 for two."""
    t = (b0 & 0xFF) | (b1 & 0xFF) << 8 | (b2 & 0xFF) << 16
    syndrome = 0
    for k, mask in enumerate(_H24_CHECKS):
        if not bin(t & mask).count("1") & 1:      # every check is odd
            syndrome |= 1 << k
    whole = bin(t).count("1") & 1
    if syndrome:
        if whole or syndrome > 23:
            return -1                             # two errors
        t ^= 1 << (syndrome - 1)
    value = 0
    for i, pos in enumerate(_H24_DATA):
        value |= ((t >> (pos - 1)) & 1) << i
    return value


def parity_ok(b: int) -> bool:
    return ODD[b & 0xFF]


# -- page numbers -----------------------------------------------------------------

def is_decimal(page: int) -> bool:
    """A page a viewer offers: 100-899 with decimal digits."""
    return (FIRST_PAGE <= page <= LAST_PAGE
            and (page >> 4) & 15 <= 9 and page & 15 <= 9)


def page_text(page: int) -> str:
    """"199" for 0x199."""
    return f"{page:03X}"


def page_from_text(text: str) -> int | None:
    """0x199 for "199"; None for anything but a page 100-899."""
    text = (text or "").strip()
    if len(text) != 3 or not text.isdecimal():
        return None
    page = int(text, 16)
    return page if is_decimal(page) else None


def page_step(page: int, step: int) -> int:
    """The decimal page *step* away (P+ / P-), wrapping 899 -> 100."""
    n = int(page_text(page)) - 100 + step
    return int(str(100 + n % 800), 16)


# -- character sets ---------------------------------------------------------------

_POS = (0x23, 0x24, 0x40, 0x5B, 0x5C, 0x5D, 0x5E, 0x5F, 0x60, 0x7B, 0x7C, 0x7D, 0x7E)
#: The 13 positions of each national option subset (ETS 300 706, table 36).
SUBSETS: dict[str, str] = {
    "en": "£$@←½→↑#—¼‖¾÷",
    "de": "#$§ÄÖÜ^_°äöüß",
    "sv": "#¤ÉÄÖÅÜ_éäöåü",
    "it": "£$é°ç→↑#ùàòèì",
    "fr": "éïàëêùî#èâôûç",
    "es": "ç$¡áéíóú¿üñèà",
    "cs": "#ůčťžýířéáěúš",
    "pl": "#ńąƵŚŁćóężśłź",
    "tr": "₺ğİŞÖÇÜĞışöçü",
    "sh": "#ËČĆŽĐŠëčćžđš",
    "ro": "#¤ŢÂŞǍÍıţâşǎî",
    "et": "#õŠÄÖŽÜÕšäöžü",
    "lv": "#$Šėęžčūšąųžį",
}
_CYR1 = ("ЧАБЦДЕФГХИЈКЛМНОПЌРСТУВЃЉЊЗЋЖЂШЏ"
         "чабцдефгхијклмнопќрстувѓљњзћжђш■")
_CYR2 = ("ЮАБЦДЕФГХИЙКЛМНОПЯРСТУЖВЬЪЗШЭЩЧЫ"
         "юабцдефгхийклмнопярстужвьъзшэщч■")
_CYR3 = ("ЮАБЦДЕФГХИЙКЛМНОПЯРСТУЖВЬІЗШЄЩЧЇ"
         "юабцдефгхийклмнопярстужвьізшєщч■")
#: The designation (X/28/0, M/29/0: 7 bits, the national option bits of the
#: header in its last three) -> the set: a national subset of Latin G0, or
#: "cyr1".."cyr3". What is not listed is read as Latin with no subset.
DESIGNATIONS: dict[int, str] = {
    0x00: "en", 0x01: "de", 0x02: "sv", 0x03: "it", 0x04: "fr", 0x05: "es", 0x06: "cs",
    0x08: "pl", 0x09: "de", 0x0A: "sv", 0x0B: "it", 0x0C: "fr", 0x0E: "cs",
    0x10: "en", 0x11: "de", 0x12: "sv", 0x13: "it", 0x14: "fr", 0x15: "es", 0x16: "tr",
    0x1D: "sh", 0x1F: "ro",
    0x20: "cyr1", 0x21: "de", 0x22: "et", 0x23: "lv", 0x24: "cyr2", 0x25: "cyr3", 0x26: "cs",
    0x36: "tr",
}


def charset(designation: int | None, national: int) -> str:
    """The set a page is drawn in: its designation's group (X/28/0 or
    M/29/0; group 0 without one) and the header's national option bits."""
    group = (designation or 0) & 0x78
    return DESIGNATIONS.get(group | (national & 7), "")


def g0_char(code: int, cset: str) -> str:
    """The character of G0 code *code* (0x20-0x7F) in set *cset*."""
    if cset.startswith("cyr"):
        if code >= 0x40:
            return {"cyr1": _CYR1, "cyr2": _CYR2, "cyr3": _CYR3}[cset][code - 0x40]
        if code == 0x26 and cset in ("cyr2", "cyr3"):
            return "ы" if cset == "cyr2" else "ї"
        return chr(code)
    if code == 0x7F:
        return "■"
    subset = SUBSETS.get(cset)
    if subset is not None and code in _POS:
        return subset[_POS.index(code)]
    if subset is None:
        if code == 0x24:
            return "¤"
        if code == 0x7C:
            return "¦"
    return chr(code)


# -- pages ------------------------------------------------------------------------

_BLANK = bytes((0x20,)) * COLS


@dataclass
class Page:
    """One subpage as it was last received."""
    number: int                     # 0x100..0x8FF
    sub: int = 0
    rows: list[bytes] = field(default_factory=lambda: [_BLANK] * 26)
    erase: bool = False
    newsflash: bool = False
    subtitle: bool = False
    suppress_header: bool = False
    inhibit: bool = False
    national: int = 0
    links: tuple[int, ...] = ()     # red, green, yellow, cyan, next, index
    show_links: bool = True
    designation: int | None = None  # X/28/0's G0 set
    received: float = 0.0

    def copy(self) -> "Page":
        return Page(self.number, self.sub, list(self.rows), self.erase, self.newsflash,
                    self.subtitle, self.suppress_header, self.inhibit, self.national,
                    self.links, self.show_links, self.designation, self.received)

    def link(self, n: int) -> int | None:
        """Fastext link *n* (0 red .. 3 cyan, 5 index) when it is a page."""
        if n < len(self.links) and is_decimal(self.links[n]):
            return self.links[n]
        return None


def _subcode(raw: int) -> int:
    """A subpage's number as kept: 1-79 rotate; anything else is one page."""
    sub = raw & 0x3F7F
    return sub if 0 < sub <= 0x79 else 0


class Decoder:
    """Teletext packets in, pages out. Fed from a reader's thread, read from
    the window's: every public method takes the lock."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._pages: dict[int, dict[int, Page]] = {}
        self._newest: dict[int, int] = {}        # page -> subcode received last
        self._building: dict[int, Page | None] = {}
        self._m29: dict[int, int] = {}
        self.header = _BLANK                     # the rolling header row
        self.version = 0                         # counts committed pages
        self.packets = 0

    # -- what comes in ------------------------------------------------------

    def feed_pes(self, pes: bytes) -> int:
        """One teletext PES; how many teletext packets it held."""
        n = 0
        for packet in pes_packets(pes):
            self.feed_packet(packet)
            n += 1
        return n

    def feed_packet(self, p: bytes) -> None:
        """One packet of 42 bytes, in the teletext bit order."""
        if len(p) < 42:
            return
        addr = unham16(p[0], p[1])
        if addr < 0:
            return
        mag, row = addr & 7, addr >> 3
        with self._lock:
            self.packets += 1
            if row == 0:
                self._header(mag, p)
            elif row <= 25:
                page = self._building.get(mag)
                if page is not None:
                    page.rows[row] = bytes(p[2:42])
            elif row == 27:
                self._links(mag, p)
            elif row == 28:
                page = self._building.get(mag)
                g0 = self._designation(p)
                if page is not None and g0 is not None:
                    page.designation = g0
            elif row == 29:
                g0 = self._designation(p)
                if g0 is not None:
                    self._m29[mag] = g0

    def _header(self, mag: int, p: bytes) -> None:
        units, tens = unham8(p[2]), unham8(p[3])
        s1, s2, s3, s4 = unham8(p[4]), unham8(p[5]), unham8(p[6]), unham8(p[7])
        c7_10, c11_14 = unham8(p[8]), unham8(p[9])
        if min(units, tens, s1, s2, s3, s4, c7_10, c11_14) < 0:
            self._close(mag)
            return
        serial = bool(c11_14 & 1)
        if serial:
            for m in list(self._building):
                self._close(m)
        else:
            self._close(mag)
        title = bytes(p[10:42])
        # The rolling header: the newest header row with something in it,
        # from no subtitle or newsflash page (C7 suppress, C5, C6).
        if not (c7_10 & 1) and not (s4 & 0xC) and not _is_blank(title):
            self.header = bytes((0x20,)) * 8 + title
        if tens == 0xF and units == 0xF:
            return                                   # time filling: no page
        number = ((mag or 8) << 8) | tens << 4 | units
        sub = _subcode(s1 | (s2 & 7) << 4 | s3 << 8 | (s4 & 3) << 12)
        erase = bool(s2 & 8)
        flags = {
            "newsflash": bool(s4 & 4), "subtitle": bool(s4 & 8),
            "suppress_header": bool(c7_10 & 1), "inhibit": bool(c7_10 & 8),
            # C12 C13 C14: C12 is the subset number's top bit.
            "national": (c11_14 >> 1 & 1) << 2 | (c11_14 >> 2 & 1) << 1 | (c11_14 >> 3 & 1),
        }
        old = self._pages.get(number, {}).get(sub)
        if old is not None and not erase:
            page = old.copy()
        else:
            page = Page(number, sub)
        page.erase = erase
        for name, value in flags.items():
            setattr(page, name, value)
        page.rows[0] = bytes((0x20,)) * 8 + title
        self._building[mag] = page

    def _close(self, mag: int) -> None:
        page = self._building.pop(mag, None)
        if page is None or not is_decimal(page.number):
            return
        page.received = self._clock()
        if page.designation is None and mag in self._m29:
            page.designation = self._m29[mag]
        subs = self._pages.setdefault(page.number, {})
        subs.pop(page.sub, None)
        subs[page.sub] = page
        while len(subs) > MAX_SUBPAGES:
            subs.pop(next(iter(subs)))
        self._newest[page.number] = page.sub
        self.version += 1

    def _links(self, mag: int, p: bytes) -> None:
        page = self._building.get(mag)
        if page is None or unham8(p[2]) != 0:
            return
        links = []
        for i in range(6):
            b = p[3 + 6 * i: 9 + 6 * i]
            units, tens = unham8(b[0]), unham8(b[1])
            m1, m23 = unham8(b[3]), unham8(b[5])
            if min(units, tens, m1, m23) < 0:
                links.append(0xFFF)
                continue
            rel = (m1 >> 3 & 1) | (m23 >> 2 & 3) << 1
            target = (mag ^ rel) or 8
            links.append(target << 8 | tens << 4 | units)
        control = unham8(p[39])
        page.links = tuple(links)
        page.show_links = control < 0 or bool(control & 8)

    @staticmethod
    def _designation(p: bytes) -> int | None:
        if unham8(p[2]) not in (0, 4):
            return None
        t = unham24(p[3], p[4], p[5])
        return None if t < 0 else (t >> 7) & 0x7F

    # -- what goes out --------------------------------------------------------

    def numbers(self) -> list[int]:
        """The pages received, in order."""
        with self._lock:
            return sorted(self._pages)

    def subpages(self, number: int) -> list[int]:
        with self._lock:
            return sorted(self._pages.get(number, {}))

    def get(self, number: int, sub: int | None = None) -> Page | None:
        """A copy of page *number*: subpage *sub*, else the one received
        last."""
        with self._lock:
            subs = self._pages.get(number)
            if not subs:
                return None
            if sub is None or sub not in subs:
                sub = self._newest.get(number, next(iter(subs)))
            page = subs.get(sub)
            return page.copy() if page is not None else None

    def rolling_header(self) -> bytes:
        with self._lock:
            return self.header

    def subtitle_pages(self) -> list[int]:
        """The pages whose header says they are subtitles."""
        with self._lock:
            return sorted(n for n, subs in self._pages.items()
                          if any(p.subtitle for p in subs.values()))


# -- a page as cells --------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Cell:
    ch: str = " "
    mosaic: int = -1            # 0..63, the six blocks (bit 0 top left .. bit 5 bottom right)
    separated: bool = False
    fg: int = 7
    bg: int = 0
    flash: bool = False
    conceal: bool = False
    height: int = 0             # 0 normal, 1 the top of double height, 2 its bottom
    boxed: bool = False


BLANK_CELL = Cell()


def _row_cells(raw: bytes, cset: str, allow_double: bool) -> tuple[list[Cell], bool]:
    """One row's cells by the level 1 rules: spacing attributes set at their
    cell or after it, hold mosaics, blast-through capitals in mosaics."""
    cells: list[Cell] = []
    fg, bg = 7, 0
    graphics = separated = flash = conceal = double = hold = boxed = False
    held, held_sep = 0, False
    has_double = False
    for b in raw[:COLS]:
        c = b & 0x7F if ODD[b] else 0x20
        if c < 0x20:
            # Set-at.
            if c == 0x09:
                flash = False
            elif c == 0x0C:
                if double:
                    held, held_sep = 0, False
                double = False
            elif c == 0x18:
                conceal = True
            elif c == 0x19:
                separated = False
            elif c == 0x1A:
                separated = True
            elif c == 0x1C:
                bg = 0
            elif c == 0x1D:
                bg = fg
            elif c == 0x1E:
                hold = True
            if hold and graphics and held:
                cells.append(Cell("", held, held_sep, fg, bg, flash, conceal,
                                  1 if double else 0, boxed))
            else:
                cells.append(Cell(" ", -1, False, fg, bg, flash, conceal,
                                  1 if double else 0, boxed))
            # Set-after.
            if c <= 0x07:
                if graphics:
                    held, held_sep = 0, False
                graphics, fg, conceal = False, c, False
            elif c == 0x08:
                flash = True
            elif c == 0x0A:
                boxed = False
            elif c == 0x0B:
                boxed = True
            elif c == 0x0D and allow_double:
                if not double:
                    held, held_sep = 0, False
                double = has_double = True
            elif 0x10 <= c <= 0x17:
                if not graphics:
                    held, held_sep = 0, False
                graphics, fg, conceal = True, c - 0x10, False
            elif c == 0x1F:
                hold = False
            continue
        if graphics and (c < 0x40 or c >= 0x60):
            mask = (c & 0x1F) | (0x20 if c & 0x40 else 0)
            held, held_sep = mask, separated
            cells.append(Cell("", mask, separated, fg, bg, flash, conceal,
                              1 if double else 0, boxed))
        else:
            cells.append(Cell(g0_char(c, cset), -1, False, fg, bg, flash, conceal,
                              1 if double else 0, boxed))
    while len(cells) < COLS:
        cells.append(Cell(" ", -1, False, 7, bg))
    return cells, has_double


def render(page: Page | None, header: bytes | None = None,
           status: str = "") -> list[list[Cell]]:
    """25 rows of 40 cells for *page* (an empty page for None). *header*:
    the rolling header for row 0's columns 8-39 (the page's own without
    one); *status*: row 0's first eight columns ("P199"). Row 24 shows the
    Fastext links in their colours when the page sends no row of its own.
    A double-height row hides the one under it, which shows its bottoms."""
    cset = charset(page.designation, page.national) if page is not None else "en"
    rows = list(page.rows) if page is not None else [_BLANK] * 26
    head = bytes(_odd(c) for c in status[:8].ljust(8).encode("latin-1", "replace"))
    if page is not None and (page.suppress_header or page.subtitle or page.newsflash):
        title = _BLANK[8:]
    elif header is not None:
        title = bytes(header[8:40]).ljust(32, b" ")
    else:
        title = bytes(rows[0][8:40]).ljust(32, b" ")
    top = head + title
    out: list[list[Cell]] = []
    cells, _ = _row_cells(top, cset, False)
    out.append(cells)
    if page is not None and page.links and _is_blank(rows[24]) and page.show_links:
        rows[24] = _links_row(page)
    skip = False
    inhibit = page is not None and page.inhibit
    for y in range(1, ROWS):
        if skip:
            above = out[-1]
            out.append([Cell(c.ch, c.mosaic, c.separated, c.fg, c.bg, c.flash,
                             c.conceal, 2, c.boxed) if c.height == 1
                        else Cell(" ", -1, False, c.fg, c.bg, False, False, 0, c.boxed)
                        for c in above])
            skip = False
            continue
        raw = _BLANK if inhibit else bytes(rows[y])
        cells, double = _row_cells(raw, cset, 1 <= y <= 22)
        out.append(cells)
        skip = double
    return out


def _odd(c: int) -> int:
    c &= 0x7F
    return c if ODD[c] else c | 0x80


def _is_blank(raw: bytes) -> bool:
    return all((b & 0x7F) in (0x20, 0x00) for b in raw)


def _links_row(page: Page) -> bytes:
    """Row 24 from the Fastext links: each page number in its key's colour."""
    out = bytearray()
    for i, colour in enumerate((1, 2, 3, 6)):
        n = page.link(i)
        text = page_text(n) if n is not None else "   "
        cell = bytes((colour,)) + text.encode("ascii")
        out += cell.ljust(10, b" ")
    return bytes(_odd(c) for c in bytes(out[:COLS]).ljust(COLS, b" "))


def text_of(cells: list[list[Cell]], row: int) -> str:
    """Row *row* as plain text (mosaics as spaces) - for tests and copying."""
    return "".join(c.ch if c.mosaic < 0 else " " for c in cells[row])


# -- the transport stream -----------------------------------------------------------

@dataclass(frozen=True)
class TeletextEntry:
    """One line of descriptor 0x56: a language and the page it names."""
    lang: str
    kind: int
    page: int


@dataclass(frozen=True)
class SubtitlingEntry:
    """One line of descriptor 0x59 (DVB subtitles)."""
    lang: str
    kind: int
    composition: int
    ancillary: int


@dataclass
class Stream:
    pid: int
    stream_type: int
    kind: str = ""                   # "teletext", "dvbsub" or ""
    teletext: list[TeletextEntry] = field(default_factory=list)
    subtitling: list[SubtitlingEntry] = field(default_factory=list)


def _lang(raw: bytes) -> str:
    try:
        text = raw.decode("ascii").strip().lower()
    except UnicodeDecodeError:
        return ""
    return text if text.isalpha() else ""


def parse_descriptors(data: bytes, stream: Stream) -> None:
    i = 0
    while i + 2 <= len(data):
        tag, length = data[i], data[i + 1]
        body = data[i + 2:i + 2 + length]
        i += 2 + length
        if tag in (0x56, 0x46):
            stream.kind = "teletext"
            for j in range(0, len(body) - 4, 5):
                kind, mag = body[j + 3] >> 3, body[j + 3] & 7
                stream.teletext.append(TeletextEntry(
                    _lang(body[j:j + 3]), kind, (mag or 8) << 8 | body[j + 4]))
        elif tag == 0x59:
            stream.kind = "dvbsub"
            for j in range(0, len(body) - 7, 8):
                stream.subtitling.append(SubtitlingEntry(
                    _lang(body[j:j + 3]), body[j + 3],
                    int.from_bytes(body[j + 4:j + 6], "big"),
                    int.from_bytes(body[j + 6:j + 8], "big")))


class TsDemux:
    """TS bytes in (any split); the PMT's streams known, and the PES of the
    teletext PIDs (or those asked for) handed back whole."""

    def __init__(self, pids: set[int] | None = None) -> None:
        self.want = pids                      # None: every teletext PID
        self.streams: dict[int, Stream] = {}
        self.pmt_pids: set[int] = set()
        self.pmt_seen = False
        self.packets = 0
        self._rest = b""
        self._psi: dict[int, bytearray] = {}
        self._pes: dict[int, bytearray] = {}

    def teletext_pids(self) -> list[int]:
        return sorted(pid for pid, s in self.streams.items() if s.kind == "teletext")

    def _wanted(self, pid: int) -> bool:
        if self.want is not None:
            return pid in self.want
        s = self.streams.get(pid)
        return s is not None and s.kind == "teletext"

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        """The PES completed by *data*: (pid, PES from its start code)."""
        buf = self._rest + bytes(data) if self._rest else bytes(data)
        out: list[tuple[int, bytes]] = []
        i, n = 0, len(buf)
        while i + TS <= n:
            if buf[i] != 0x47:
                j = buf.find(b"\x47", i + 1)
                if j < 0:
                    i = n
                    break
                i = j
                continue
            pid = (buf[i + 1] & 0x1F) << 8 | buf[i + 2]
            if pid == 0 or pid in self.pmt_pids or self._wanted(pid):
                self._packet(buf[i:i + TS], pid, out)
            self.packets += 1
            i += TS
        self._rest = buf[i:] if i < n else b""
        if len(self._rest) > TS:
            self._rest = self._rest[-TS:]
        return out

    def _packet(self, pkt: bytes, pid: int, out: list[tuple[int, bytes]]) -> None:
        if pkt[1] & 0x80:
            return                                       # transport error
        pusi = bool(pkt[1] & 0x40)
        afc = (pkt[3] >> 4) & 3
        if not afc & 1:
            return                                       # no payload
        start = 4
        if afc & 2:
            start = 5 + pkt[4]
            if start >= TS:
                return
        payload = pkt[start:]
        if pid == 0 or pid in self.pmt_pids:
            self._section(pid, payload, pusi)
            return
        pes = self._pes.get(pid)
        if pusi:
            if pes is not None and len(pes) >= 6:
                out.append((pid, bytes(pes)))
            pes = bytearray(payload)
            self._pes[pid] = pes
        elif pes is None:
            return
        else:
            pes += payload
        if len(pes) >= 6:
            length = pes[4] << 8 | pes[5]
            if length and len(pes) >= 6 + length:
                out.append((pid, bytes(pes[:6 + length])))
                self._pes.pop(pid, None)
            elif len(pes) > 65536 + 6:
                self._pes.pop(pid, None)

    def _section(self, pid: int, payload: bytes, pusi: bool) -> None:
        if pusi:
            if not payload:
                return
            pointer = payload[0]
            buf = bytearray(payload[1 + pointer:])
            self._psi[pid] = buf
        else:
            buf = self._psi.get(pid, bytearray())
            if not buf:
                return
            buf += payload
        if len(buf) < 3:
            return
        length = (buf[1] & 0x0F) << 8 | buf[2]
        if len(buf) < 3 + length:
            return
        section = bytes(buf[:3 + length])
        self._psi.pop(pid, None)
        if section[0] == 0x00 and pid == 0:
            self._pat(section)
        elif section[0] == 0x02:
            self._pmt(section)

    def _pat(self, s: bytes) -> None:
        end = len(s) - 4
        for i in range(8, end - 3, 4):
            program = s[i] << 8 | s[i + 1]
            if program:
                self.pmt_pids.add((s[i + 2] & 0x1F) << 8 | s[i + 3])

    def _pmt(self, s: bytes) -> None:
        if len(s) < 16:
            return
        info = (s[10] & 0x0F) << 8 | s[11]
        i, end = 12 + info, len(s) - 4
        seen: dict[int, Stream] = {}
        while i + 5 <= end:
            stype = s[i]
            pid = (s[i + 1] & 0x1F) << 8 | s[i + 2]
            es_len = (s[i + 3] & 0x0F) << 8 | s[i + 4]
            stream = Stream(pid, stype)
            parse_descriptors(s[i + 5:i + 5 + es_len], stream)
            seen[pid] = stream
            i += 5 + es_len
        self.streams.update(seen)
        self.pmt_seen = True


def pes_packets(pes: bytes) -> Iterator[bytes]:
    """The teletext packets of one EN 300 472 PES, in the teletext bit order."""
    if len(pes) < 10 or pes[0] or pes[1] or pes[2] != 1 or pes[3] != 0xBD:
        return
    i = 9 + pes[8]
    if i >= len(pes) or not 0x10 <= pes[i] <= 0x1F:
        return
    i += 1
    end = len(pes)
    while i + 2 <= end:
        uid, ulen = pes[i], pes[i + 1]
        if uid in (0x02, 0x03) and ulen == 0x2C and i + 46 <= end:
            yield pes[i + 4:i + 46].translate(REVERSE)
        i += 2 + ulen


def read_streams(data: bytes) -> dict[int, Stream] | None:
    """The PMT's streams in *data* (a piece of a TS), None when no PMT."""
    demux = TsDemux(pids=set())
    demux.feed(data)
    return demux.streams if demux.pmt_seen else None


def streams_from_store(store: "SegmentSource", back: float = 4.0,
                       limit: int = 8 << 20) -> dict[int, Stream] | None:
    """The PMT's streams from the newest seconds of a rewind buffer (a PMT
    comes several times a second); None when there is none in them."""
    newest = store.newest()
    if newest is None:
        return None
    seg = store.segment_at(newest.t1 - back) or newest
    demux = TsDemux(pids=set())
    read = 0
    chunks = store.read_from(seg.seq, stop=_always)
    try:
        for chunk in chunks:
            demux.feed(chunk)
            read += len(chunk)
            if demux.pmt_seen or read >= limit:
                break
    finally:
        close = getattr(chunks, "close", None)
        if callable(close):
            close()              # lets go of the segment it holds open
    return demux.streams if demux.pmt_seen else None


def _always() -> bool:
    return True


# -- the rewind buffer as the source --------------------------------------------------

class SegmentSource(Protocol):
    """What StoreFeed needs of core/dvr_store.SegmentStore."""
    closed: bool

    def newest(self) -> Any: ...

    def segment_at(self, t: float) -> Any: ...

    def first_from(self, seq: int) -> Any: ...

    def read_from(self, seq: int, chunk: int = ..., wait: float = ...,
                  stop: Callable[[], bool] | None = ...,
                  on_segment: Callable[[Any], None] | None = ...) -> Iterator[bytes]: ...


class StoreFeed:
    """Reads a channel's rewind buffer into a decoder until stopped: from a
    little before live (a whole teletext cycle is seldom over a minute), on
    with the live bytes, past a reconnect's gap. Its own thread; nothing
    else - the provider is never asked."""

    BACK_SECONDS = 90.0

    def __init__(self, store: SegmentSource, decoder: Decoder, start_seq: int | None = None) -> None:
        self.store = store
        self.decoder = decoder
        self.demux = TsDemux()
        self.last_seq = start_seq
        self.started = time.monotonic()
        self.error = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="teletext", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float = 1.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def no_teletext(self) -> bool:
        """The PMT has been read and names no teletext."""
        return self.demux.pmt_seen and not self.demux.teletext_pids()

    def _first(self) -> Any:
        if self.last_seq is not None:
            return self.store.first_from(self.last_seq + 1)
        newest = self.store.newest()
        if newest is None:
            return None
        return self.store.segment_at(newest.t1 - self.BACK_SECONDS) or newest

    def _run(self) -> None:
        stopped = self._stop.is_set
        try:
            while not stopped() and not self.store.closed:
                seg = self._first()
                if seg is None:
                    self._stop.wait(0.5)
                    continue
                # Until its run ends (a reconnect) or the segment is gone;
                # then on from the next segment there is.
                for chunk in self.store.read_from(seg.seq, stop=stopped,
                                                  on_segment=self._on_segment):
                    for _pid, pes in self.demux.feed(chunk):
                        self.decoder.feed_pes(pes)
                    if stopped():
                        return
                if self.last_seq is None:
                    self.last_seq = seg.seq
                self._stop.wait(0.3)
        except Exception as e:      # never take the app down over a page
            self.error = f"{type(e).__name__}: {e}"
            log.warning("teletext: reading the buffer stopped: %s", self.error)

    def _on_segment(self, seg: Any) -> None:
        self.last_seq = seg.seq
