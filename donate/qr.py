"""qr.py - a QR code as SVG, for the admin page's authenticator setup.

An authenticator app adds an account by scanning its otpauth:// address;
this draws that address as a QR code (ISO/IEC 18004: byte mode, error
correction level M, versions 1 to 10 - up to 213 bytes, plenty for one
address). Written here so the page needs no library and no outside service:
the secret in the address never leaves the server except to the admin's
own browser.

    svg(text) -> '<svg ...>...</svg>'
"""
from __future__ import annotations

# Error correction level M, per version: (EC codewords per block,
# [(blocks, data codewords per block), ...]).
_BLOCKS = {
    1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]),
    5: (24, [(2, 43)]), 6: (16, [(4, 27)]), 7: (18, [(4, 31)]), 8: (22, [(2, 38), (2, 39)]),
    9: (22, [(3, 36), (2, 37)]), 10: (26, [(4, 43), (1, 44)]),
}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
          7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}
_REMAINDER = {1: 0, 2: 7, 3: 7, 4: 7, 5: 7, 6: 7, 7: 0, 8: 0, 9: 0, 10: 0}
_LEVEL_M = 0                     # the two format bits of level M

# -- GF(256) and Reed-Solomon ----------------------------------------------------
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a: int, b: int) -> int:
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _generator(n: int) -> list:
    g = [1]
    for i in range(n):
        nxt = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            nxt[j] ^= c
            nxt[j + 1] ^= _mul(c, _EXP[i])
        g = nxt
    return g


def _ec(data: list, n: int) -> list:
    g = _generator(n)
    rem = list(data) + [0] * n
    for i in range(len(data)):
        c = rem[i]
        if c:
            for j in range(1, len(g)):
                rem[i + j] ^= _mul(g[j], c)
    return rem[len(data):]


# -- the codewords ---------------------------------------------------------------
def _capacity(version: int) -> int:
    return sum(n * size for n, size in _BLOCKS[version][1])


def _codewords(data: bytes, version: int) -> list:
    count_bits = 8 if version < 10 else 16
    bits: list = []

    def put(value: int, length: int) -> None:
        bits.extend((value >> (length - 1 - i)) & 1 for i in range(length))
    put(0b0100, 4)
    put(len(data), count_bits)
    for b in data:
        put(b, 8)
    room = _capacity(version) * 8
    put(0, min(4, room - len(bits)))
    put(0, -len(bits) % 8)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(words) < _capacity(version):
        words.append(pad)
        pad ^= 0xEC ^ 0x11
    # Split into blocks, add each block's error correction, interleave.
    ec_len, groups = _BLOCKS[version]
    blocks, ecs, at = [], [], 0
    for n, size in groups:
        for _ in range(n):
            blk = words[at:at + size]
            at += size
            blocks.append(blk)
            ecs.append(_ec(blk, ec_len))
    out = []
    for i in range(max(len(b) for b in blocks)):
        out += [b[i] for b in blocks if i < len(b)]
    for i in range(ec_len):
        out += [e[i] for e in ecs]
    return out


# -- the matrix ------------------------------------------------------------------
class _Matrix:
    def __init__(self, version: int) -> None:
        self.v = version
        self.n = 17 + 4 * version
        self.dark = [[False] * self.n for _ in range(self.n)]
        self.fixed = [[False] * self.n for _ in range(self.n)]

    def set(self, x: int, y: int, dark: bool) -> None:
        self.dark[y][x] = dark
        self.fixed[y][x] = True

    def functions(self) -> None:
        n = self.n
        for i in range(n):                       # timing patterns
            self.set(6, i, i % 2 == 0)
            self.set(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):    # finders and their separators
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        d = max(abs(dx), abs(dy))
                        self.set(x, y, d not in (2, 4))
        pos = _ALIGN[self.v]
        for i, ay in enumerate(pos):              # alignment patterns, clear of the finders
            for j, ax in enumerate(pos):
                if (i, j) in ((0, 0), (0, len(pos) - 1), (len(pos) - 1, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.format(0)                            # reserved now, written once the mask is chosen
        if self.v >= 7:
            rem = self.v
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            bits = self.v << 12 | rem
            for i in range(18):
                bit = (bits >> i) & 1 == 1
                a, b = n - 11 + i % 3, i // 3
                self.set(a, b, bit)
                self.set(b, a, bit)

    def format(self, mask: int) -> None:
        data = _LEVEL_M << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        n = self.n

        def bit(i: int) -> bool:
            return (bits >> i) & 1 == 1
        for i in range(6):
            self.set(8, i, bit(i))
        self.set(8, 7, bit(6))
        self.set(8, 8, bit(7))
        self.set(7, 8, bit(8))
        for i in range(9, 15):
            self.set(14 - i, 8, bit(i))
        for i in range(8):
            self.set(n - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set(8, n - 15 + i, bit(i))
        self.set(8, n - 8, True)                  # the dark module

    def place(self, words: list) -> None:
        bits = [(w >> (7 - i)) & 1 for w in words for i in range(8)]
        bits += [0] * _REMAINDER[self.v]
        n, i = self.n, 0
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            upward = ((right + 1) & 2) == 0
            for vert in range(n):
                y = n - 1 - vert if upward else vert
                for x in (right, right - 1):
                    if not self.fixed[y][x] and i < len(bits):
                        self.dark[y][x] = bits[i] == 1
                        i += 1
            right -= 2

    def masked(self, mask: int) -> list:
        rule = (
            lambda y, x: (y + x) % 2 == 0,
            lambda y, x: y % 2 == 0,
            lambda y, x: x % 3 == 0,
            lambda y, x: (y + x) % 3 == 0,
            lambda y, x: (y // 2 + x // 3) % 2 == 0,
            lambda y, x: (y * x) % 2 + (y * x) % 3 == 0,
            lambda y, x: ((y * x) % 2 + (y * x) % 3) % 2 == 0,
            lambda y, x: ((y + x) % 2 + (y * x) % 3) % 2 == 0,
        )[mask]
        return [[self.dark[y][x] ^ (not self.fixed[y][x] and rule(y, x)) for x in range(self.n)]
                for y in range(self.n)]


def _penalty(m: list) -> int:
    n, score = len(m), 0
    lines = m + [list(col) for col in zip(*m, strict=True)]
    for line in lines:                            # runs of five or more
        run, prev = 0, None
        for d in line:
            run = run + 1 if d == prev else 1
            prev = d
            if run == 5:
                score += 3
            elif run > 5:
                score += 1
        s = "".join("1" if d else "0" for d in line)   # finder-like patterns
        for pat in ("10111010000", "00001011101"):
            at = s.find(pat)
            while at >= 0:
                score += 40
                at = s.find(pat, at + 1)
    for y in range(n - 1):                        # 2x2 blocks of one colour
        for x in range(n - 1):
            if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark = sum(sum(row) for row in m)
    score += abs(dark * 20 - n * n * 10) // (n * n) * 10
    return score


def matrix(text: str) -> list:
    """The modules of a QR code for *text* (rows of booleans, True dark)."""
    data = text.encode("utf-8")
    version = next((v for v in range(1, 11) if len(data) <= _capacity(v) - (2 if v < 10 else 3)), 0)
    if not version:
        raise ValueError("too long for a QR code of version 10")
    words = _codewords(data, version)
    best = None
    for mask in range(8):
        m = _Matrix(version)
        m.functions()
        m.place(words)
        m.format(mask)
        out = m.masked(mask)
        score = _penalty(out)
        if best is None or score < best[0]:
            best = (score, out)
    return best[1]


def svg(text: str, quiet: int = 4) -> str:
    """*text* as an SVG QR code: dark modules on white, with its quiet zone."""
    m = matrix(text)
    size = len(m) + 2 * quiet
    path = "".join(f"M{x + quiet} {y + quiet}h1v1h-1z"
                   for y, row in enumerate(m) for x, dark in enumerate(row) if dark)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
            f'shape-rendering="crispEdges" role="img" aria-label="QR code">'
            f'<rect width="{size}" height="{size}" fill="#fff"/><path d="{path}" fill="#000"/></svg>')
