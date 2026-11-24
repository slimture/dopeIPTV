"""Fuzzing transport streams (tests/fuzz_util.py): what the teletext
viewer and subtitles read - TS to PES to pages, each page drawn - the
stream analysis (TR 101 290's indicators, the tables, the clock), the check
for an encrypted stream, and the subtitle menu's pages. Streams made as a
channel sends them (tests/ts_gen.py, tests/ttx_stream.py), then mutated a
packet at a time: odd PIDs, adaptation fields, counters, sections of any
table and length, sync lost."""
from __future__ import annotations

import json
import random

from dopeiptv.core import teletext as ttx
from dopeiptv.core import teletext_subs as subs
from dopeiptv.core import ts_analysis as ta
from dopeiptv.core import ts_scramble
from tests import fuzz_util as fz
from tests import ts_gen
from tests import ttx_stream as g


def _teletext_streams() -> list[bytes]:
    pages = [g.demo_page(), g.page_packets(0x199, {20: "Undertext", 22: "rad två"}, subtitle=True),
             g.page_packets(0x101, {1: "News", 24: "x"}, links=[0x102, 0x200, 0x300, 0x100]),
             [g.x28_designation(0x100, 0x16), g.m29_designation(1, 0x16)]]
    return [g.stream(pages), g.stream(pages[:1], tail=[g.filler()]),
            g.stream(pages, descriptor=g.subtitling_descriptor([("swe", 0x10, 1, 1)]))]


TTX = _teletext_streams()


def _chunks(rng: random.Random, data: bytes) -> list[bytes]:
    out, i = [], 0
    while i < len(data):
        n = rng.choice((1, 7, 188, 1000, 4096, len(data)))
        out.append(data[i:i + n])
        i += n
    return out


def test_fuzz_teletext_stream():
    """A teletext stream of any damage: pages decoded, each drawn as 25
    rows of 40 cells, the subtitle pages found - never an exception."""
    def make(rng: random.Random) -> tuple[bytes, int]:
        return fz.mutate_ts(rng, rng.choice(TTX), pids=(0x500, 0x1000)), rng.randrange(1 << 30)

    def target(arg: tuple[bytes, int]) -> list:
        data, n = arg
        demux = ttx.TsDemux()
        dec = ttx.Decoder(clock=lambda: 1_780_000_000.0)
        for chunk in _chunks(random.Random(n), data):
            for _pid, pes in demux.feed(chunk):
                dec.feed_pes(pes)
        ttx.read_streams(data)
        shown = []
        for number in dec.numbers()[:20]:
            for sub in dec.subpages(number)[:3]:
                page = dec.get(number, sub)
                cells = ttx.render(page, dec.rolling_header(), "P" + ttx.page_text(number))
                assert len(cells) == 25 and all(len(row) == 40 for row in cells)
                shown.append(ttx.text_of(cells, 1))
                if page is not None:
                    for i in range(4):
                        page.link(i)
        dec.subtitle_pages()
        ttx.render(None)
        return shown
    fz.fuzz("teletext-ts", target, make, seeds=[(d, 1) for d in TTX], min_runs=15)


def test_fuzz_teletext_packets():
    """Teletext packets and PES of any bytes, straight into the decoder."""
    pes = []
    demux = ttx.TsDemux()
    for _pid, p in demux.feed(TTX[0]):
        pes.append(p)
    packets = [p for x in pes for p in ttx.pes_packets(x)]

    def make(rng: random.Random) -> list[bytes]:
        if rng.random() < 0.5:
            return [fz.mutate_bytes(rng, rng.choice(pes), 4) for _ in range(rng.randint(1, 4))]
        return [b"PKT" + fz.mutate_bytes(rng, rng.choice(packets), 3) for _ in range(rng.randint(1, 30))]

    def target(items: list[bytes]) -> int:
        dec = ttx.Decoder(clock=lambda: 0.0)
        for item in items:
            if item.startswith(b"PKT"):
                dec.feed_packet(item[3:])
            else:
                dec.feed_pes(item)
        for number in dec.numbers():
            cells = ttx.render(dec.get(number), None)
            assert len(cells) == 25
        return dec.version
    fz.fuzz("teletext-packets", target, make)

    def descriptors(data: bytes) -> object:
        s = ttx.Stream(0x500, 6)
        ttx.parse_descriptors(data, s)
        assert len(s.teletext) <= len(data) and len(s.subtitling) <= len(data)
        return s
    seeds = [g.teletext_descriptor([("swe", 2, 0x199), ("fin", 5, 0x888)]),
             g.subtitling_descriptor([("swe", 0x10, 1, 1)])]
    fz.fuzz("teletext-descriptors", descriptors, lambda rng: fz.mutate_bytes(rng, rng.choice(seeds), others=seeds),
            seeds=seeds)


def _gen_stream(seconds: float = 0.6) -> bytes:
    gen = ts_gen.Gen(bitrate=2e6)
    n = int(seconds * 2e6 / (188 * 8))
    return b"".join(gen.next_packet() for _ in range(n))


GEN = [_gen_stream(), _gen_stream(0.2)]


def test_fuzz_ts_analysis():
    """The stream analysis on a damaged stream fed as it comes, with
    arrival times: a snapshot and a verdict every time, as large as its
    bounds and no larger."""
    def make(rng: random.Random) -> tuple[bytes, int]:
        data = rng.choice(GEN)
        if rng.random() < 0.1:
            data = b"#EXTM3U\n" + data[:500] if rng.random() < 0.5 else bytes(rng.randrange(256) for _ in range(3000))
        return fz.mutate_ts(rng, data, pids=(0x100, 0x101, 0x102, 0x1000, 0x11)), rng.randrange(1 << 30)

    def target(arg: tuple[bytes, int]) -> object:
        data, n = arg
        rng = random.Random(n)
        now = [1_780_000_000.0]
        a = ta.Analyzer(clock=lambda: now[0])
        for chunk in _chunks(rng, data):
            now[0] += rng.choice((0.0, 0.01, 0.2, 1.5, -0.5))
            a.feed(chunk, now[0] if rng.random() < 0.7 else None)
            if rng.random() < 0.05:
                a.gap()
            if rng.random() < 0.02:
                a.reset_counters()
        snap = a.snapshot()
        state, inds = ta.verdict(snap)
        assert state in (ta.OK, ta.WARN, ta.ERROR, ta.WAITING)
        assert len(snap.pids) <= ta.MAX_PIDS + 8 and len(snap.programs) <= ta.MAX_PROGRAMS
        assert isinstance(snap.service(), str) and isinstance(snap.provider(), str)
        return snap
    fz.fuzz("ts-analysis", target, make, seeds=[(d, 3) for d in GEN], min_runs=15)


def test_fuzz_dvb_text_and_scramble():
    texts = [b"\x05Kan\xe4l", b"\x10\x00\x05abc", b"\x11\x00S\x00V", b"\x15\xc3\xa5", b"plain", b"\x1fx"]
    fz.fuzz("dvb-text", lambda raw: ta.dvb_text(raw), lambda rng: fz.mutate_bytes(rng, rng.choice(texts)),
            seeds=texts)

    def target(data: bytes) -> object:
        got = ts_scramble.verdict(data)
        assert got in (True, False, None)
        ts_scramble.sync_offset(data)
        return got
    fz.fuzz("ts-scramble", target, lambda rng: fz.mutate_ts(rng, rng.choice(GEN)), seeds=GEN)


TRACKS = [{"id": 3, "type": "sub", "codec": "dvb_teletext", "lang": "swe,fin", "src-id": 0x500},
          {"id": 4, "type": "sub", "codec": "dvb_subtitle", "lang": "swe", "src-id": 0x501,
           "hearing-impaired": True},
          {"id": 1, "type": "video", "codec": "h264"}]


def test_fuzz_subtitle_menu():
    """mpv's track list (and a stored choice) of any shape through the
    subtitle menu: its entries, the language's page, what mpv is told."""
    streams = ttx.read_streams(TTX[0]) or {}

    class Kept:
        def __init__(self, raw: object) -> None:
            self.raw = raw

        def value(self, key: str, default: object = None) -> object:
            return self.raw

        def setValue(self, key: str, value: object) -> None:  # noqa: N802 - Qt's name
            self.raw = value

    stored = {"src:1": {"p": "199", "l": "swe", "s": 0x500}, "src:2": {"l": "fin"}}

    def make(rng: random.Random) -> tuple:
        tracks = fz.json_value(rng, TRACKS)
        kept = fz.to_json(fz.mutate_json(rng, stored)) if rng.random() < 0.8 else rng.choice(fz.ODD_TEXT)
        return tracks, kept, rng.choice(("mpv 0.38.0", "mpv v0.35.1-dirty", "", "mpv 99999999999999999999.1",
                                         fz.decoded(rng.choice(fz.ODD_TEXT))))

    def target(arg: tuple) -> object:
        tracks, kept, version_text = arg
        tracks = [t for t in tracks if isinstance(t, dict)] if isinstance(tracks, list) else []
        opts = subs.all_choices(tracks, streams)
        version = subs.mpv_version(version_text)
        for c in opts:
            subs.mpv_page(c, version)
        got = subs.pick(opts, ["sv", "fin", "", fz.ODD_TEXT[5]])
        subs.default_page(["sv", "de"], version)
        store = subs.PageStore(Kept(kept))
        for key in ("src:1", "src:2", "nope", ""):
            store.find(key, opts)
            store.get(key)
        if opts:
            store.put("src:3", opts[0])
            store.forget("src:1")
        return got
    fz.fuzz("subtitle-menu", target, make, seeds=[(TRACKS, json.dumps(stored), "mpv 0.38.0")])


def test_streams_take_linear_time():
    data = GEN[0]

    def analyse(d: bytes) -> object:
        a = ta.Analyzer(clock=lambda: 0.0)
        a.feed(d)
        return a.snapshot()
    fz.linear(analyse, lambda n: data[:188 * n], 120)
    fz.linear(lambda d: ttx.TsDemux().feed(d), lambda n: (TTX[0] * (n // 10 + 1))[:188 * n], 200)
    # Sync never found: every byte a sync byte one place off.
    fz.linear(analyse, lambda n: (b"\x47" + bytes(186)) * n, 400)
    fz.linear(lambda d: ttx.TsDemux().feed(d), lambda n: b"\x47\x00" * (94 * n), 400)
