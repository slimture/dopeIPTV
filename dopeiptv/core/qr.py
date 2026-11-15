"""A QR code encoder in plain Python (ISO/IEC 18004): the phone remote's
address as a code a phone's camera opens.

Byte mode only (an address is a few dozen bytes of ASCII), error
correction M by default (L, Q and H too), the smallest of the 40 versions
that holds the data, and the mask with the lowest penalty - or the one
asked for. What comes out is the grid of modules, row by row, True for a
dark one; drawing it (with the four-module quiet zone around it) is the
caller's.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The error correction levels, by name: (index into the tables, the two
#: format bits).
LEVELS: dict[str, tuple[int, int]] = {"L": (0, 1), "M": (1, 0), "Q": (2, 3), "H": (3, 2)}

# Error correction codewords per block, and the number of blocks, for each
# level (L, M, Q, H) and version (index 0 unused) - the standard's table 9.
_ECC_PER_BLOCK = (
    (-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28,
     28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26,
     26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28),
    (-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30,
     28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    (-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28,
     30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
)
_BLOCKS = (
    (-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8,
     8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25),
    (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16,
     17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49),
    (-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20,
     23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68),
    (-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25,
     25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81),
)

# The penalty weights of the four rules a mask is chosen by.
_N1, _N2, _N3, _N4 = 3, 3, 40, 10

_MODE_BYTE = 0x4


class QrError(ValueError):
    """The data does not fit, or what was asked is not a QR code."""


@dataclass(frozen=True)
class QrCode:
    version: int
    level: str
    mask: int
    modules: tuple[tuple[bool, ...], ...]

    @property
    def size(self) -> int:
        return len(self.modules)

    def dark(self, x: int, y: int) -> bool:
        """Column *x*, row *y*; outside the grid (the quiet zone) is light."""
        return 0 <= y < self.size and 0 <= x < self.size and self.modules[y][x]

    def text(self, quiet: int = 4, dark: str = "#", light: str = ".") -> str:
        """The grid as lines of text, the quiet zone included (for tests
        and a terminal)."""
        rng = range(-quiet, self.size + quiet)
        return "\n".join("".join(dark if self.dark(x, y) else light for x in rng) for y in rng)


# -- Reed-Solomon over GF(256), the polynomial x^8 + x^4 + x^3 + x^2 + 1 --------

def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def rs_divisor(degree: int) -> list[int]:
    """The generator polynomial's coefficients, highest power first (its
    leading 1 left out)."""
    if not 1 <= degree <= 255:
        raise QrError("degree out of range")
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def rs_remainder(data: bytes | list[int], divisor: list[int]) -> list[int]:
    """The error correction codewords of *data*."""
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


# -- the sizes ---------------------------------------------------------------------

def raw_modules(version: int) -> int:
    """How many modules of a version carry data and error correction."""
    result = (16 * version + 128) * version + 64
    if version >= 2:
        n = version // 7 + 2
        result -= (25 * n - 10) * n - 55
        if version >= 7:
            result -= 36
    return result


def data_codewords(version: int, level: str = "M") -> int:
    li = LEVELS[level][0]
    return raw_modules(version) // 8 - _ECC_PER_BLOCK[li][version] * _BLOCKS[li][version]


def capacity(version: int, level: str = "M") -> int:
    """The most bytes a version holds in byte mode."""
    bits = data_codewords(version, level) * 8 - 4 - (8 if version <= 9 else 16)
    return max(0, bits // 8)


def alignment_positions(version: int) -> list[int]:
    if version == 1:
        return []
    n = version // 7 + 2
    step = (version * 8 + n * 3 + 5) // (n * 4 - 4) * 2
    size = version * 4 + 17
    return [6] + sorted(size - 7 - i * step for i in range(n - 1))


def format_bits(level: str, mask: int) -> int:
    """The 15 format bits: the level and the mask, BCH-coded and masked."""
    data = LEVELS[level][1] << 3 | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return (data << 10 | rem) ^ 0x5412


def version_bits(version: int) -> int:
    """The 18 version bits (versions 7 and up)."""
    rem = version
    for _ in range(12):
        rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
    return version << 12 | rem


# -- the codewords -------------------------------------------------------------------

def data_bits(data: bytes, version: int, level: str) -> list[int]:
    """The data codewords: mode, count, the bytes, the terminator and the
    padding."""
    bits: list[int] = []

    def put(value: int, n: int) -> None:
        bits.extend((value >> i) & 1 for i in reversed(range(n)))
    put(_MODE_BYTE, 4)
    put(len(data), 8 if version <= 9 else 16)
    for b in data:
        put(b, 8)
    room = data_codewords(version, level) * 8
    if len(bits) > room:
        raise QrError("the data does not fit this version")
    put(0, min(4, room - len(bits)))
    put(0, -len(bits) % 8)
    pad = 0xEC
    while len(bits) < room:
        put(pad, 8)
        pad ^= 0xEC ^ 0x11
    return [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]


def interleaved(codewords: list[int], version: int, level: str) -> list[int]:
    """The data split into blocks, each with its error correction, then
    taken a codeword of each block at a time."""
    li = LEVELS[level][0]
    nblocks = _BLOCKS[li][version]
    ecc_len = _ECC_PER_BLOCK[li][version]
    raw = raw_modules(version) // 8
    nshort = nblocks - raw % nblocks
    short_len = raw // nblocks
    divisor = rs_divisor(ecc_len)
    blocks: list[list[int]] = []
    k = 0
    for i in range(nblocks):
        n = short_len - ecc_len + (0 if i < nshort else 1)
        dat = codewords[k:k + n]
        k += n
        ecc = rs_remainder(dat, divisor)
        if i < nshort:
            dat = dat + [0]          # a place the short blocks skip
        blocks.append(dat + ecc)
    out: list[int] = []
    for i in range(len(blocks[0])):
        for j, blk in enumerate(blocks):
            if i != short_len - ecc_len or j >= nshort:
                out.append(blk[i])
    return out


# -- the grid -------------------------------------------------------------------------

class _Grid:
    def __init__(self, version: int) -> None:
        self.version = version
        self.size = version * 4 + 17
        self.dark = [[False] * self.size for _ in range(self.size)]
        self.fixed = [[False] * self.size for _ in range(self.size)]

    def put(self, x: int, y: int, dark: bool) -> None:
        self.dark[y][x] = dark
        self.fixed[y][x] = True

    def functions(self) -> None:
        size = self.size
        for i in range(size):
            self.put(6, i, i % 2 == 0)
            self.put(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < size and 0 <= y < size:
                        self.put(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = alignment_positions(self.version)
        last = len(pos) - 1
        for i, ax in enumerate(pos):
            for j, ay in enumerate(pos):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.put(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.format(0, "M")            # reserved now, written once the mask is known
        if self.version >= 7:
            bits = version_bits(self.version)
            for i in range(18):
                bit = bool((bits >> i) & 1)
                a, b = size - 11 + i % 3, i // 3
                self.put(a, b, bit)
                self.put(b, a, bit)

    def format(self, mask: int, level: str) -> None:
        bits = format_bits(level, mask)
        size = self.size

        def bit(i: int) -> bool:
            return bool((bits >> i) & 1)
        for i in range(6):
            self.put(8, i, bit(i))
        self.put(8, 7, bit(6))
        self.put(8, 8, bit(7))
        self.put(7, 8, bit(8))
        for i in range(9, 15):
            self.put(14 - i, 8, bit(i))
        for i in range(8):
            self.put(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.put(8, size - 15 + i, bit(i))
        self.put(8, size - 8, True)    # the dark module

    def codewords(self, data: list[int]) -> None:
        size = self.size
        i, total = 0, len(data) * 8
        right = size - 1
        while right >= 1:
            if right == 6:
                right = 5
            upward = ((right + 1) & 2) == 0
            for vert in range(size):
                y = size - 1 - vert if upward else vert
                for j in range(2):
                    x = right - j
                    if not self.fixed[y][x] and i < total:
                        self.dark[y][x] = bool((data[i >> 3] >> (7 - (i & 7))) & 1)
                        i += 1
            right -= 2

    def masked(self, mask: int) -> list[list[bool]]:
        rule = _MASKS[mask]
        return [[d != (not f and rule(x, y)) for x, (d, f) in enumerate(zip(drow, frow, strict=True))]
                for y, (drow, frow) in enumerate(zip(self.dark, self.fixed, strict=True))]


_MASKS = (
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
)


def _line_penalty(line: list[bool]) -> int:
    """Rules 1 and 3 along one row or column: runs of five or more, and
    the finder's 1:1:3:1:1 with four light modules on a side (the quiet
    zone counts as light)."""
    score = 0
    run = 0
    colour = False
    for i, d in enumerate(line):
        if i and d == colour:
            run += 1
            if run == 5:
                score += _N1
            elif run > 5:
                score += 1
        else:
            colour, run = d, 1
    n = len(line)
    padded = [False] * 4 + line + [False] * 4
    pattern = (True, False, True, True, True, False, True)
    for i in range(n - 6 + 8):
        if tuple(padded[i:i + 7]) == pattern:
            before = not any(padded[max(0, i - 4):i]) and i >= 4
            after = not any(padded[i + 7:i + 11]) and i + 11 <= len(padded)
            if before or after:
                score += _N3
    return score


def penalty(modules: list[list[bool]]) -> int:
    size = len(modules)
    score = 0
    for row in modules:
        score += _line_penalty(row)
    for x in range(size):
        score += _line_penalty([modules[y][x] for y in range(size)])
    for y in range(size - 1):
        a, b = modules[y], modules[y + 1]
        for x in range(size - 1):
            if a[x] == a[x + 1] == b[x] == b[x + 1]:
                score += _N2
    dark = sum(sum(row) for row in modules)
    total = size * size
    k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
    return score + max(0, k) * _N4


def encode(data: bytes | str, level: str = "M", version: int | None = None,
           mask: int | None = None) -> QrCode:
    """*data* (text as UTF-8) as a QR code in byte mode: the smallest version
    that holds it unless *version* is given, the best mask unless *mask*
    is. QrError when it does not fit."""
    raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    if level not in LEVELS:
        raise QrError(f"no such error correction level: {level!r}")
    if mask is not None and not 0 <= mask <= 7:
        raise QrError("the mask is 0 to 7")
    if version is None:
        version = next((v for v in range(1, 41) if capacity(v, level) >= len(raw)), 0)
        if not version:
            raise QrError("too much data for a QR code")
    elif not 1 <= version <= 40:
        raise QrError("the version is 1 to 40")
    elif capacity(version, level) < len(raw):
        raise QrError("the data does not fit this version")
    grid = _Grid(version)
    grid.functions()
    grid.codewords(interleaved(data_bits(raw, version, level), version, level))
    best: tuple[int, int, list[list[bool]]] | None = None
    for m in ([mask] if mask is not None else range(8)):
        grid.format(m, level)
        modules = grid.masked(m)
        score = 0 if mask is not None else penalty(modules)
        if best is None or score < best[0]:
            best = (score, m, modules)
    assert best is not None
    return QrCode(version, level, best[1], tuple(tuple(r) for r in best[2]))
