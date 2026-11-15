"""Teletext in plain Python (core/teletext.py): Hamming 8/4 and 24/18 and odd
parity, a page put together from a generated DVB transport stream (PAT,
PMT with the teletext descriptor, EN 300 472 PES), the national option
subsets, Fastext links, subpages, serial and parallel magazines, the level
1 attributes as cells, and reading the app's own rewind buffer."""
from __future__ import annotations

import time

import pytest

from tests import ttx_stream as g
from dopeiptv.core import teletext as ttx
from dopeiptv.core.dvr_store import SegmentStore


def _decode(data: bytes, chunk: int = 1000) -> tuple[ttx.Decoder, ttx.TsDemux]:
    dec, demux = ttx.Decoder(), ttx.TsDemux()
    for i in range(0, len(data), chunk):          # cut anywhere, mid-packet
        for _pid, pes in demux.feed(data[i:i + chunk]):
            dec.feed_pes(pes)
    return dec, demux


def _feed(*packets: bytes) -> ttx.Decoder:
    dec = ttx.Decoder()
    for p in packets:
        dec.feed_packet(p)
    return dec


def _page(dec: ttx.Decoder, number: int, sub: int | None = None) -> list[list[ttx.Cell]]:
    page = dec.get(number, sub)
    assert page is not None, f"page {number:x} not received"
    return ttx.render(page)


# -- error protection ---------------------------------------------------------------

def test_hamming_8_4_corrects_one_bit_and_refuses_two():
    for n in range(16):
        code = g.ham8(n)
        assert ttx.unham8(code) == n
        for bit in range(8):
            assert ttx.unham8(code ^ (1 << bit)) == n
        for a in range(8):
            for b in range(a + 1, 8):
                assert ttx.unham8(code ^ (1 << a) ^ (1 << b)) == -1
    assert ttx.unham16(g.ham8(0x9), g.ham8(0x1)) == 0x19
    assert ttx.unham16(0x00, g.ham8(1)) in (-1, 0x10 | ttx.unham8(0))


def test_hamming_24_18_corrects_one_bit_and_refuses_two():
    for value in (0, 1, 0x2AAAA, 0x15555, 0x3FFFF, 0x12345, 0x00F0F):
        code = g.ham24(value)
        assert ttx.unham24(*code) == value
        t = code[0] | code[1] << 8 | code[2] << 16
        for bit in range(24):
            f = t ^ (1 << bit)
            assert ttx.unham24(f & 0xFF, f >> 8 & 0xFF, f >> 16) == value, bit
        for a, b in ((0, 1), (3, 9), (2, 23), (16, 20)):
            f = t ^ (1 << a) ^ (1 << b)
            assert ttx.unham24(f & 0xFF, f >> 8 & 0xFF, f >> 16) == -1, (a, b)


def test_odd_parity_and_a_character_that_fails_it_shows_as_a_space():
    for c in range(0x20, 0x80):
        assert ttx.parity_ok(g.odd(c)) and not ttx.parity_ok(g.odd(c) ^ 0x80)
    row = bytearray(g.text("ABC"))
    row[1] ^= 0x80                                   # B's parity broken
    cells, _ = ttx._row_cells(bytes(row), "en", False)
    assert "".join(c.ch for c in cells[:3]) == "A C"


def test_reverse_and_page_numbers():
    assert ttx.REVERSE[0x01] == 0x80 and ttx.REVERSE[0xE4] == 0x27
    assert ttx.page_from_text("199") == 0x199 and ttx.page_text(0x199) == "199"
    for bad in ("099", "900", "1a9", "19", "1999", ""):
        assert ttx.page_from_text(bad) is None
    assert ttx.page_step(0x899, 1) == 0x100 and ttx.page_step(0x100, -1) == 0x899
    assert ttx.page_step(0x199, 1) == 0x200
    assert ttx.is_decimal(0x100) and not ttx.is_decimal(0x1FE) and not ttx.is_decimal(0x900)


# -- the transport stream and the page -------------------------------------------------

def test_a_page_is_put_together_from_a_generated_transport_stream():
    pages = [g.page_packets(0x100, {1: "\x03 Hello from page 100", 2: "\x06 Second row",
                                    23: "\x02 Bottom"},
                            title="SVT Text 100  ons 15 nov 21:42"),
             [g.filler(1)]]
    desc = g.teletext_descriptor([("swe", 1, 0x100), ("swe", 2, 0x199), ("fin", 2, 0x333)])
    dec, demux = _decode(g.stream(pages, descriptor=desc))
    assert demux.pmt_seen and demux.teletext_pids() == [0x500]
    entries = demux.streams[0x500].teletext
    assert [(e.lang, e.kind, e.page) for e in entries] == [
        ("swe", 1, 0x100), ("swe", 2, 0x199), ("fin", 2, 0x333)]
    assert dec.numbers() == [0x100]
    cells = ttx.render(dec.get(0x100), dec.rolling_header(), "P100")
    assert ttx.text_of(cells, 0).startswith("P100    SVT Text 100")
    assert ttx.text_of(cells, 1).strip() == "Hello from page 100"
    assert cells[1][2].fg == 3 and cells[2][2].fg == 6 and cells[23][2].fg == 2
    assert ttx.text_of(cells, 5).strip() == ""


def test_subtitle_data_units_and_other_data_identifiers():
    pages = [g.page_packets(0x199, {22: "\x0b\x0b Undertext\x0a\x0a"}, subtitle=True,
                            suppress=True), [g.filler(1)]]
    dec, _ = _decode(g.stream(pages, unit_id=0x03))
    page = dec.get(0x199)
    assert page is not None and page.subtitle and dec.subtitle_pages() == [0x199]
    cells = ttx.render(page, None, "P199")
    assert cells[22][3].boxed and ttx.text_of(cells, 22).strip() == "Undertext"
    assert ttx.text_of(cells, 0)[8:].strip() == ""          # no header on a subtitle
    # A PES that says it is not EBU teletext (data_identifier 0x80) is not read.
    other = g.pes(g.page_packets(0x150, {1: "x"}) + [g.filler(1)], data_identifier=0x80)
    assert ttx.Decoder().feed_pes(other) == 0


def test_dvb_subtitle_descriptor_and_streams_read_from_a_piece_of_ts():
    sub = g.subtitling_descriptor([("fin", 0x10, 1, 1), ("fin", 0x20, 2, 2)])
    data = g.stream([[g.filler(1)]], extra=[(0x06, 0x600, sub)])
    streams = ttx.read_streams(data)
    assert streams is not None and streams[0x600].kind == "dvbsub"
    assert [(e.lang, e.kind) for e in streams[0x600].subtitling] == [("fin", 0x10), ("fin", 0x20)]
    assert ttx.read_streams(b"\x47" + b"\x1f\xff\x10" + b"\xff" * 184) is None


# -- character sets ---------------------------------------------------------------------

@pytest.mark.parametrize("national, raw, text", [
    (0, b"#$@[\\]^_`{|}~", "£$@←½→↑#—¼‖¾÷"),
    (1, b"[\\]{|}~@", "ÄÖÜäöüß§"),
    (2, b"[\\]{|}`@", "ÄÖÅäöåéÉ"),
    (4, b"#@`{}|~", "éàèâûôç"),
    (5, b"#@[\\]^_`|}~", "ç¡áéíóú¿ñèà"),
    (3, b"#`{|}~", "£ùàòèì"),
])
def test_national_option_subsets(national, raw, text):
    dec = _feed(*g.page_packets(0x100, {1: raw}, national=national), g.filler(1))
    assert ttx.text_of(_page(dec, 0x100), 1)[:len(text)] == text


def test_a_designation_in_x28_or_m29_picks_another_group():
    # Group 1 (Polish in place of English): X/28/0 on the page itself.
    packets = g.page_packets(0x100, {1: b"[\\]"}, national=0)
    dec = _feed(packets[0], g.x28_designation(0x100, 0x08), *packets[1:], g.filler(1))
    assert ttx.text_of(_page(dec, 0x100), 1)[:3] == "ƵŚŁ"
    # Cyrillic for the magazine (M/29/0: its group; the header's C12-C14
    # say which set in it, 4 for Russian), kept for its pages.
    dec = _feed(g.m29_designation(2, 0x20),
                *g.page_packets(0x200, {1: b"PRIWET"}, national=4), g.filler(2))
    assert ttx.text_of(_page(dec, 0x200), 1)[:6] == "ПРИВЕТ"
    assert ttx.charset(None, 2) == "sv" and ttx.charset(0x10, 6) == "tr"


# -- Fastext and subpages ---------------------------------------------------------------

def test_fastext_links_from_x27_and_a_row_24_made_from_them():
    packets = g.page_packets(0x100, {1: "Index"}, links=[0x101, 0x200, 0x355, 0x800, 0x8FF, 0x100])
    dec = _feed(*packets, g.filler(1))
    page = dec.get(0x100)
    assert page is not None
    assert page.links[:4] == (0x101, 0x200, 0x355, 0x800) and page.link(5) == 0x100
    assert page.link(4) is None                    # 8FF: no page
    cells = ttx.render(page)
    row = ttx.text_of(cells, 24)
    assert "101" in row and "200" in row and "355" in row and "800" in row
    assert cells[24][row.index("101")].fg == 1 and cells[24][row.index("200")].fg == 2
    # A page with its own row 24 keeps it.
    own = _feed(*g.page_packets(0x100, {24: "\x01 Nyheter"}, links=[0x101]), g.filler(1))
    assert ttx.text_of(_page(own, 0x100), 24).strip() == "Nyheter"


def test_subpages_are_kept_apart_and_the_newest_is_shown():
    packets = []
    for sub in (1, 2, 3):
        packets += g.page_packets(0x200, {3: f"Sport {sub}"}, sub=sub) + [g.filler(2)]
    dec = _feed(*packets)
    assert dec.subpages(0x200) == [1, 2, 3]
    for sub in (1, 2, 3):
        assert ttx.text_of(_page(dec, 0x200, sub), 3).strip() == f"Sport {sub}"
    assert ttx.text_of(_page(dec, 0x200), 3).strip() == "Sport 3"
    # A subcode that counts the time is one page, not hundreds.
    clock = _feed(*g.page_packets(0x300, {1: "a"}, sub=0x2142), g.filler(3))
    assert clock.subpages(0x300) == [0]


def test_a_row_sent_again_updates_the_page_unless_the_header_says_erase():
    dec = _feed(*g.page_packets(0x100, {1: "one", 2: "two"}), g.filler(1),
                *g.page_packets(0x100, {2: "TWO"}, erase=False), g.filler(1))
    cells = _page(dec, 0x100)
    assert ttx.text_of(cells, 1).strip() == "one" and ttx.text_of(cells, 2).strip() == "TWO"
    dec = _feed(*g.page_packets(0x100, {1: "one", 2: "two"}), g.filler(1),
                *g.page_packets(0x100, {2: "TWO"}, erase=True), g.filler(1))
    cells = _page(dec, 0x100)
    assert ttx.text_of(cells, 1).strip() == "" and ttx.text_of(cells, 2).strip() == "TWO"


def test_parallel_magazines_interleave_and_serial_mode_ends_every_page():
    a = g.page_packets(0x100, {1: "first", 2: "second"})
    b = g.page_packets(0x200, {1: "other"})
    # Parallel: page 100's rows go on after magazine 2's header.
    dec = _feed(a[0], a[1], *b, a[2], g.filler(1), g.filler(2))
    assert ttx.text_of(_page(dec, 0x100), 2).strip() == "second"
    # Serial (C11): any header ends the page in transmission.
    a = g.page_packets(0x100, {1: "first", 2: "second"}, serial=True)
    b = g.page_packets(0x200, {1: "other"}, serial=True)
    dec = _feed(a[0], a[1], *b, a[2], g.filler(1), g.filler(2))
    assert ttx.text_of(_page(dec, 0x100), 2).strip() == ""


# -- the level 1 attributes ----------------------------------------------------------------

def _row(raw: bytes) -> list[ttx.Cell]:
    cells, _ = ttx._row_cells(g.text(raw), "en", True)
    return cells


def test_mosaics_contiguous_separated_held_and_blast_through():
    cells = _row(b"\x12\x7f\x21\x1a\x7f\x41\x1e\x13\x1f\x14")
    assert cells[1].mosaic == 63 and cells[1].fg == 2 and not cells[1].separated
    assert cells[2].mosaic == 1
    assert cells[4].mosaic == 63 and cells[4].separated
    assert cells[5].mosaic < 0 and cells[5].ch == "A"     # capitals blast through
    # Hold: a colour change shows the last mosaic instead of a space.
    assert cells[7].mosaic == 63 and cells[7].separated
    assert cells[8].mosaic == 63                          # release is set-after
    assert cells[9].mosaic < 0 and cells[9].ch == " "


def test_colours_backgrounds_flash_conceal_and_boxes():
    cells = _row(b"\x01\x1dRed\x1c\x08F\x09S\x02\x18H\x0b\x0bB\x0a\x0a")
    assert cells[1].bg == 1 and cells[2].fg == 1 and cells[2].bg == 1
    assert cells[6].bg == 0
    assert cells[7].flash and not cells[9].flash
    assert cells[12].conceal and cells[12].ch == "H" and cells[12].fg == 2
    assert cells[15].boxed and cells[15].ch == "B" and not cells[12].boxed


def test_double_height_takes_the_row_below():
    dec = _feed(*g.page_packets(0x100, {3: b"\x0d\x03BIG", 4: "hidden", 5: "next"}), g.filler(1))
    cells = _page(dec, 0x100)
    assert cells[3][2].height == 1 and cells[3][2].ch == "B"
    assert cells[4][2].height == 2 and cells[4][2].ch == "B"
    assert "hidden" not in ttx.text_of(cells, 4)
    assert ttx.text_of(cells, 5).strip() == "next"


def test_render_without_a_page_and_the_status_columns():
    cells = ttx.render(None, None, "P1--")
    assert len(cells) == ttx.ROWS and all(len(r) == ttx.COLS for r in cells)
    assert ttx.text_of(cells, 0).startswith("P1--")


def test_the_demo_page_renders():
    dec = _feed(*g.demo_page(), g.filler(1))
    cells = ttx.render(dec.get(0x100), dec.rolling_header(), "P100")
    assert "Räksmörgås på menyn" in ttx.text_of(cells, 13)
    assert any(c.conceal for c in cells[17]) and any(c.flash for c in cells[15])
    assert "21:42:07" in ttx.text_of(cells, 0)


# -- the rewind buffer as the source -----------------------------------------------------------

def _store(tmp_path) -> SegmentStore:
    return SegmentStore(tmp_path / "seg", keep_seconds=600, max_bytes=1 << 30, min_free=0)


def test_the_feed_reads_the_buffer_and_follows_it_live(tmp_path):
    store = _store(tmp_path)
    store.append(g.stream([g.page_packets(0x100, {1: "Before"}), [g.filler(1)]]))
    dec = ttx.Decoder()
    feed = ttx.StoreFeed(store, dec)
    feed.start()
    try:
        deadline = time.monotonic() + 5
        while dec.get(0x100) is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ttx.text_of(_page(dec, 0x100), 1).strip() == "Before"
        # Live bytes after it (same run): read as they come.
        mux = g.Muxer()
        more = b"".join(mux.pes_packets(0x500, g.pes(chunk)) for chunk in (
            g.page_packets(0x300, {1: "Live"}) + [g.filler(3)],))
        store.append(more)
        deadline = time.monotonic() + 5
        while dec.get(0x300) is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ttx.text_of(_page(dec, 0x300), 1).strip() == "Live"
        assert not feed.no_teletext() and feed.error == ""
    finally:
        feed.stop()
        feed.join(3)
        store.close()
    assert not feed.running and feed.last_seq is not None


def test_a_channel_without_teletext_is_said_so_and_the_pmt_is_read_from_the_store(tmp_path):
    store = _store(tmp_path)
    sub = g.subtitling_descriptor([("swe", 0x10, 1, 1)])
    store.append(g.stream([[g.filler(1)]], pid=0x600, descriptor=sub))
    streams = ttx.streams_from_store(store)
    assert streams is not None and streams[0x600].kind == "dvbsub"
    feed = ttx.StoreFeed(store, ttx.Decoder())
    feed.start()
    try:
        deadline = time.monotonic() + 5
        while not feed.no_teletext() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert feed.no_teletext()
    finally:
        feed.stop()
        feed.join(3)
        store.close()
    assert ttx.streams_from_store(_store(tmp_path / "empty")) is None
