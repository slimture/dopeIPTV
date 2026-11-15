"""The QR encoder (core/qr.py) against known vectors: the standard's own
tables (format and version information, alignment positions, byte
capacities), the error correction codewords of the well-known "HELLO WORLD"
1-M example, and whole symbols as two independent encoders made them -
Kazuhiko Arase's QR library and segno 1.6.6 - for the same data, version,
level and mask (each grid's rows of 0 and 1, hashed with SHA-256)."""
from __future__ import annotations

import hashlib

import pytest

from dopeiptv.core import qr


def digest(code: qr.QrCode) -> str:
    rows = ["".join("1" if d else "0" for d in row) for row in code.modules]
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def test_error_correction_of_the_hello_world_example():
    data = [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17]
    assert qr.rs_remainder(data, qr.rs_divisor(10)) == [196, 35, 39, 119, 235, 215, 231, 226, 93, 23]


def test_format_information_as_the_standard_lists_it():
    m = [0b101010000010010, 0b101000100100101, 0b101111001111100, 0b101101101001011,
         0b100010111111001, 0b100000011001110, 0b100111110010111, 0b100101010100000]
    assert [qr.format_bits("M", i) for i in range(8)] == m
    assert qr.format_bits("L", 0) == 0b111011111000100
    assert qr.format_bits("Q", 0) == 0b011010101011111
    assert qr.format_bits("H", 0) == 0b001011010001001


def test_version_information_as_the_standard_lists_it():
    assert qr.version_bits(7) == 0x07C94
    assert qr.version_bits(8) == 0x085BC
    assert qr.version_bits(10) == 0x0A4D3
    assert qr.version_bits(40) == 0x28C69


def test_alignment_patterns_where_the_standard_puts_them():
    assert qr.alignment_positions(1) == []
    assert qr.alignment_positions(2) == [6, 18]
    assert qr.alignment_positions(7) == [6, 22, 38]
    assert qr.alignment_positions(32) == [6, 34, 60, 86, 112, 138]
    assert qr.alignment_positions(36) == [6, 24, 50, 76, 102, 128, 154]
    assert qr.alignment_positions(40) == [6, 30, 58, 86, 114, 142, 170]


def test_byte_capacities():
    assert [qr.capacity(v, "M") for v in range(1, 11)] == [14, 26, 42, 62, 84, 106, 122, 152, 180, 213]
    assert qr.capacity(40, "M") == 2331
    assert qr.capacity(40, "L") == 2953
    assert (qr.capacity(1, "L"), qr.capacity(1, "Q"), qr.capacity(1, "H")) == (17, 11, 7)
    assert qr.capacity(15, "H") == 220          # 11 blocks of 12 and 7 of 13 data codewords


V1_ART = """\
111111100100001111111
100000100110101000001
101110101110001011101
101110101001001011101
101110101111101011101
100000101000101000001
111111101010101111111
000000001001100000000
101111100000101111100
010000001010110001001
101001100101001001010
011111010010000101100
100110111011000001010
000000001011111000101
111111100010101000110
100000101101111101100
101110101010100100001
101110101100100001100
101110101111010001100
100000100100000001100
111111101111010110110"""


def test_a_small_symbol_module_for_module():
    code = qr.encode("dopeIPTV", "M", version=1, mask=2)
    assert code.text(quiet=0, dark="1", light="0") == V1_ART
    assert (code.version, code.size, code.level, code.mask) == (1, 21, "M", 2)


# (data, version, level, mask, SHA-256 of the reference encoders' grid)
VECTORS = [
    ("https://dopeiptv.org", 2, "M", 5, "0ec4005fe3e5c8b63b2faee6d33787e01af6071d26904705079fa915b33c61bc"),
    ("http://192.168.1.23:3674/" + "Ab3_-x9" * 4 + "Qz12/", 4, "M", 3,
     "a682b1f164e84b48acccfc94bab4db382ded0eb1c81eb9a80fed7c978090ac63"),
    ("http://192.168.100.200:3674/" + "k" * 43 + "/calendar.ics?x=" + "7" * 13, 6, "M", 0,
     "155b73f675182febe8f0908e8ff72f2d7c90ad25c977a251d01bfc5efb52fa9b"),
    # Version information, and four blocks.
    ("http://10.0.0.2:3674/" + "t" * 100, 7, "M", 6, "2907afa2818ace26bb757685737ae8bef026d2fa8a4e5a871e381cb7608aac4c"),
    # Blocks of two lengths.
    ("webcal://10.0.0.2:3674/" + "c" * 120, 8, "M", 1, "41f9a5d668d9e595caf77e99757e2a49798fd052638f083af9113567a0ae937b"),
    ("Q" * 120, 9, "Q", 7, "72f59ad58a974bccf947ba51eca576b9cdbb1b21aa9ee6a20faaea87b0063fc6"),
    # A 16-bit character count.
    ("x" * 250, 12, "M", 4, "3537cb16233b6aacc020bf20d5f66edad5d949938c66a757f3ef4d88b820a3e9"),
    # Full: no terminator room, 18 blocks of two lengths (segno).
    ("z" * 220, 15, "H", 2, "58a6cc45fc28d444d5be354b91c13dfe45a5641716eb18cc83ea7dc7e6f15aaf"),
]


@pytest.mark.parametrize("data,version,level,mask,expected", VECTORS)
def test_whole_symbols_as_independent_encoders_make_them(data, version, level, mask, expected):
    code = qr.encode(data, level, version=version, mask=mask)
    assert code.size == version * 4 + 17
    assert digest(code) == expected


def test_the_smallest_version_that_holds_it_and_the_best_mask():
    url = "http://192.168.100.200:3674/" + "k" * 43 + "/calendar.ics?x=" + "7" * 13
    assert len(url) == 100
    code = qr.encode(url)
    assert (code.version, code.level) == (6, "M")
    assert qr.encode("x" * 14).version == 1 and qr.encode("x" * 15).version == 2
    scores = [qr.penalty([list(r) for r in qr.encode(url, mask=m).modules]) for m in range(8)]
    assert scores[code.mask] == min(scores)
    # Text is UTF-8.
    assert qr.encode("Ö").modules == qr.encode("Ö".encode("utf-8")).modules


def test_what_does_not_fit_is_said():
    with pytest.raises(qr.QrError):
        qr.encode("x" * 2332, "M")
    with pytest.raises(qr.QrError):
        qr.encode("x" * 15, "M", version=1)
    with pytest.raises(qr.QrError):
        qr.encode("x", "Z")
    with pytest.raises(qr.QrError):
        qr.encode("x", mask=8)


def test_the_quiet_zone_reads_light():
    code = qr.encode("dopeIPTV", version=1, mask=2)
    assert code.dark(0, 0) and not code.dark(-1, 0) and not code.dark(21, 5)
    lines = code.text().splitlines()
    assert len(lines) == 29 and all(len(line) == 29 for line in lines)
    assert set(lines[0]) == {"."}
