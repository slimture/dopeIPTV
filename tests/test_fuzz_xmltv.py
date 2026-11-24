"""Fuzzing XMLTV guides (tests/fuzz_util.py): the parse (plain and
gzipped), the index written beside it and read back, the capped download
and unpacking, several guides in one, the time stamps; the prolog check
every XML from outside takes (core/safe_xml.py) and the HTML taken out of
guide text (core/tagless.py). Guides made as a provider writes them, then
mutated as a tree and as bytes."""
from __future__ import annotations

import gzip
import random
import xml.etree.ElementTree as ET
import zlib

import pytest

from dopeiptv.core import safe_xml, tagless
from dopeiptv.providers import epg
from tests import fuzz_util as fz

VOCAB = ("tv", "channel", "programme", "display-name", "title", "sub-title", "desc", "icon", "url",
         "category", "episode-num", "date", "rating", "value", "credits", "actor")
STAMPS = ("20260101120000 +0100", "20260101120000+0200", "20260101120000", "20260101120000 Z",
          "20260101120000 UTC", "2026010112", "", "99999999999999 +0000", "20261341256199 +9999",
          "20260101120000 +2400", "00000101000000 -1200", "20260101120000 +01:00", "abc", "1" * 40)
TEXTS = ("Rapport", "Nyheter &amp; väder", "<b>Matchen</b> börjar 15:00", "&lt;p&gt;x&lt;/p&gt;",
         "Line\nbreak", "", "  ", "&#xE9;t&#233;", "Ünïcödé 日本")


def guide(rng: random.Random) -> bytes:
    """An XMLTV guide of a few channels and programmes, its stamps and
    words of every kind a provider writes."""
    out = ['<?xml version="1.0" encoding="UTF-8"?>\n<tv generator-info-name="x">']
    ids = [rng.choice(("svt1.se", "SVT1.se ", "", "a" * 50, "x&amp;y", "ch1")) for _ in range(rng.randint(1, 4))]
    for cid in ids:
        out.append(f'<channel id="{cid}"><display-name>{rng.choice(TEXTS)}</display-name>'
                   f'<display-name lang="en">{rng.choice(TEXTS)}</display-name><icon src="http://l/x.png"/></channel>')
    base = 1_780_000_000
    for _ in range(rng.randint(0, 12)):
        start = base + rng.randrange(-86400 * 30, 86400 * 30)
        a = epg_stamp(rng, start)
        b = epg_stamp(rng, start + rng.choice((1800, 3600, -60, 0)))
        out.append(f'<programme start="{a}" stop="{b}" channel="{rng.choice(ids)}">'
                   f'<title lang="sv">{rng.choice(TEXTS)}</title><desc>{rng.choice(TEXTS)}</desc>'
                   f'<category>News</category></programme>')
    out.append("</tv>")
    return "\n".join(out).encode("utf-8")


def epg_stamp(rng: random.Random, t: int) -> str:
    if rng.random() < 0.2:
        return rng.choice(STAMPS)
    import time as _t
    return _t.strftime("%Y%m%d%H%M%S", _t.gmtime(t)) + rng.choice((" +0000", "+0100", "", " Z"))


def make_guide(rng: random.Random) -> bytes:
    data = guide(rng)
    k = rng.random()
    if k < 0.75:
        data = fz.mutate_xml(rng, data, VOCAB)
    elif k < 0.9:
        data = fz.mutate_bytes(rng, data, tokens=("<", ">", "</programme>", "<programme ", "&", '"',
                                                  "<!DOCTYPE tv [", "<!ENTITY", "]>", "]]>"))
    if rng.random() < 0.15:
        data = gzip.compress(data, mtime=0)
        if rng.random() < 0.3:
            data = fz.mutate_bytes(rng, data, 2)
    return data


def _plain_and_sorted(by_id: dict) -> bool:
    for starts, stops, titles, descs in by_id.values():
        if not (len(starts) == len(stops) == len(titles) == len(descs)):
            return False
        if list(starts) != sorted(starts):
            return False
    return True


def test_fuzz_parse_xmltv():
    def target(data: bytes) -> tuple:
        by_id, by_name = epg.parse_xmltv(data, 7)
        assert _plain_and_sorted(by_id)
        assert all(isinstance(k, str) and k == k.strip().lower() for k in by_id)
        assert all(k and k == epg.normalize_name(k) for k in by_name)
        # The index of what was read reads back as the same guide.
        assert epg.read_index_blob(epg.index_blob(by_id, by_name)) == (by_id, by_name)
        return by_id, by_name
    def unpacked(data: bytes, out: tuple) -> str | None:
        # A gzipped guide is held to what it unpacks to (which is capped).
        if data[:2] == b"\x1f\x8b":
            try:
                data = epg.gunzip_gently(data, 1 << 24)
            except (zlib.error, epg.GuideTooLarge):
                pass
        return fz.bounded(4, 5000)(data, out)
    fz.fuzz("xmltv", target, make_guide, allowed=(ValueError, ET.ParseError),
            seeds=[guide(random.Random(i)) for i in range(5)], check=unpacked)


def test_fuzz_index_blob():
    """A damaged index is None - parse the guide again - whatever its bytes
    say, quickly: never an exception, never memory its header asks for."""
    by_id, by_name = epg.parse_xmltv(guide(random.Random(7)), 400)
    blobs = [epg.index_blob(by_id, by_name), epg.index_blob({}, {}),
             zlib.decompress(epg.index_blob(by_id, by_name), 31)]

    def make(rng: random.Random) -> bytes:
        data = rng.choice(blobs)
        if rng.random() < 0.5 and data[:2] == b"\x1f\x8b":
            data = gzip.compress(fz.mutate_bytes(rng, zlib.decompress(data, 31)), mtime=0)
        else:
            data = fz.mutate_bytes(rng, data)
        return data

    def target(data: bytes) -> object:
        got = epg.read_index_blob(data)
        assert got is None or (isinstance(got, tuple) and len(got) == 2)
        return got
    fz.fuzz("xmltv-index", target, make, seeds=blobs)


def test_fuzz_gunzip_gently():
    parts = [gzip.compress(b"<tv>" + b"x" * 5000 + b"</tv>", mtime=0), gzip.compress(b"a", mtime=0) * 3,
             gzip.compress(b"\x00" * 300_000, mtime=0)]

    def target(data: bytes) -> bytes:
        out = epg.gunzip_gently(data, limit=200_000)
        assert len(out) <= 200_000
        return out
    fz.fuzz("gunzip", target, lambda rng: fz.mutate_bytes(rng, rng.choice(parts), others=parts),
            allowed=(zlib.error, epg.GuideTooLarge), seeds=parts)


class _Answer:
    def __init__(self, headers: dict, chunks: list[bytes]) -> None:
        self.headers, self._chunks, self.closed = headers, chunks, False

    def iter_content(self, size: int):
        yield from self._chunks

    def close(self) -> None:
        self.closed = True


def test_fuzz_read_capped():
    def make(rng: random.Random) -> _Answer:
        said = rng.choice(fz.ODD_TEXT + (None, 0, -1, 10, 2 ** 70, "1000", " 99 ", 99.5))
        chunks = [b"x" * rng.choice((0, 1, 100, 5000)) for _ in range(rng.randint(0, 30))]
        return _Answer({"content-length": said} if rng.random() < 0.8 else {}, chunks)

    def target(r: _Answer) -> bytes:
        got = epg.read_capped(r, None, limit=20_000)
        assert len(got) <= 20_000
        return got
    fz.fuzz("read-capped", target, make, allowed=(epg.GuideTooLarge,))


def test_fuzz_guide_bodies_and_addresses():
    def make(rng: random.Random) -> str:
        base = rng.choice(("http://a/g.xml, http://b/g.xml.gz", "http://a/x?y=1,2 https://b/", "",
                           "http://a\nhttp://b|https://c;https://d"))
        return fz.mutate_text(rng, base, tokens=(",", " ", ";", "|", "\n", "http://", "https://"))

    def target(text: str) -> list:
        got = epg.split_guide_urls(text)
        assert all(u and u == u.strip() for u in got)
        return got
    fz.fuzz("guide-urls", target, make)

    def body(data: bytes) -> bytes:
        out = epg._tv_body(data)
        assert len(out) <= len(data)
        return out
    fz.fuzz("tv-body", body, make_guide, allowed=(ValueError,))

    def stamp(s: str) -> object:
        got = epg.parse_xmltv_time(s)
        assert got is None or got.tzinfo is not None
        return got
    fz.fuzz("xmltv-time", stamp, lambda rng: fz.mutate_text(rng, rng.choice(STAMPS), 2), seeds=STAMPS)
    fz.fuzz("normalize-name", epg.normalize_name, lambda rng: fz.mutate_text(rng, rng.choice(TEXTS)))


# -- core/safe_xml.py -----------------------------------------------------------------

PROLOGS = ('<?xml version="1.0"?><!DOCTYPE tv SYSTEM "xmltv.dtd"><tv/>',
           '<!DOCTYPE a [<!ENTITY e "x">]><a>&e;</a>',
           '<!DOCTYPE a [<!ATTLIST a b CDATA "]>">]><a/>',
           '<!-- c --><?pi x?><a b="1">t</a>',
           '<?xml version="1.0" encoding="UTF-16"?><a/>')


def make_document(rng: random.Random) -> bytes:
    text = fz.mutate_text(rng, rng.choice(PROLOGS), tokens=("<!DOCTYPE", "<!ENTITY", "[", "]", ">", "<!--",
                                                             "-->", "<?", "?>", '"', "'", "%", "SYSTEM"))
    return fz.odd_encoding(rng, text) if rng.random() < 0.4 else fz.decoded(text).encode("utf-8")


def test_fuzz_refuse_dtd_in_slices():
    """The prolog check fed a document in slices says what it says of the
    whole - a DTD split over two slices is still a DTD - and raises
    nothing but ValueError."""
    def verdict(data: bytes, step: int) -> bool:
        p = safe_xml.Prolog("x")
        try:
            for i in range(0, len(data), step):
                p.feed(data[i:i + step])
            p.feed(b"", True)
        except ValueError:
            return False
        return True

    def target(arg: tuple[bytes, int]) -> bool:
        data, step = arg
        whole = verdict(data, max(1, len(data)))
        assert verdict(data, step) == whole, "sliced and whole disagree"
        return whole
    def make(rng: random.Random) -> tuple[bytes, int]:
        data = make_document(rng)
        # A slice a byte long for a short document; a long one in slices
        # as long as a network's (the time is the fuzzer's, not the check's).
        return data, max(rng.choice((1, 2, 3, 7, 64)), len(data) // 1024)
    fz.fuzz("prolog", target, make)


def test_fuzz_safe_fromstring_and_drop_dtd():
    def parse(data: bytes) -> object:
        return safe_xml.fromstring(data)
    fz.fuzz("safe-fromstring", parse, make_document, allowed=(ValueError, ET.ParseError))

    def drop(text: str) -> str:
        out = safe_xml.drop_dtd(text)
        assert len(out) <= len(text)
        return out
    fz.fuzz("drop-dtd", drop, lambda rng: fz.decoded(make_document(rng).decode("utf-8", "replace")))


@pytest.mark.parametrize("piece", ['<!DOCTYPE a [<!-- ', "<!DOCTYPE a [ '", '<!DOCTYPE a [ <?',
                                   "<!DOCTYPE a [" + "]" * 3, "<!DOCTYPE a " + '"x" ' * 3])
def test_drop_dtd_takes_linear_time(piece):
    fz.linear(safe_xml.drop_dtd, lambda n: piece * n + "<a/>", 2000)


# -- core/tagless.py ------------------------------------------------------------------

HTML = ("Matchen börjar: <span class=\"t\">15:00</span>.<br/>Sen <b>nyheter</b>",
        "&lt;p&gt;dubbel&lt;/p&gt; &amp;amp; &#65;&#x42; &#0; &#x7f; &not &notin;",
        "<Untitled> a < b > c <!-- kommentar --> <script>x</script>", "plain")


def test_fuzz_tagless_plain():
    def target(text: str) -> str:
        out = tagless.plain(text)
        assert isinstance(out, str)
        made = {c for c in out if (ord(c) < 32 and c not in "\t\n") or 0x7F <= ord(c) < 0xA0} - set(text)
        assert not made, f"a control character made from an entity: {made!r}"
        return out
    # A description of 200 KB at most: longer is the fuzzer's time (the
    # time a text takes grows with it - test_tagless_takes_linear_time).
    fz.fuzz("tagless", target, lambda rng: fz.mutate_text(rng, rng.choice(HTML), tokens=(
        "<", ">", "</", "&", ";", "#", "#x", "<br>", "<p ", "<!--", "-->", "div", "span", "&lt;", "&gt;"))[:200_000],
        check=fz.bounded(2, 100))


@pytest.mark.parametrize("piece", ["<a ", "<span", "</p", "<!--", "&#", "&lt;b ", "<b>&lt;", "&amp;lt;"])
def test_tagless_takes_linear_time(piece):
    fz.linear(tagless.plain, lambda n: piece * n, 3000)


def test_a_guide_of_many_programmes_takes_linear_time():
    def make(n: int) -> bytes:
        rows = "".join(f'<programme start="2030010{1 + i % 8}120000 +0000" stop="20300109120000 +0000" '
                       f'channel="c{i % 50}"><title>T{i % 7}</title><desc>&lt;b&gt;d{i}&lt;/b&gt;</desc></programme>'
                       for i in range(n))
        return f"<tv>{rows}</tv>".encode()
    fz.linear(lambda d: epg.parse_xmltv(d, 7), make, 400)
