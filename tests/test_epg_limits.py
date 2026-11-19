"""A guide from an EPG address is not trusted to be small or plain: a DTD of
its own is refused however far into the document it is put, an answer that
never ends and a gzip bomb are cut off at a size no real guide has
(providers/epg.py: refuse_entities, read_capped, gunzip_gently)."""
from __future__ import annotations

import gzip
import zlib

import pytest

from dopeiptv.providers import epg
from dopeiptv.providers.epg import XmltvGuide


def _xmltv(title: str = "Rapport", head: bytes = b"") -> bytes:
    return (b'<?xml version="1.0" encoding="UTF-8"?>\n' + head
            + b'<tv><channel id="svt1.se"><display-name>SVT1</display-name></channel>'
            b'<programme channel="svt1.se" start="20990101120000 +0000" '
            b'stop="20990101130000 +0000"><title>' + title.encode() + b"</title></programme></tv>")


def test_a_dtd_of_its_own_is_refused_after_a_long_comment():
    long = b"<!--" + b"x" * 70_000 + b"-->\n"
    for head in (long + b'<!DOCTYPE tv [<!ENTITY a "Rapport">]>\n',
                 long + b"<!DOCTYPE tv []>\n",
                 b'<!DOCTYPE tv [<!ENTITY a "Rapport">]>\n'):
        body = _xmltv("&a;", head)
        with pytest.raises(ValueError):
            epg.parse_xmltv(body, 1)
        with pytest.raises(ValueError):
            epg.parse_xmltv(gzip.compress(body), 1)


def test_a_real_guide_s_doctype_and_a_long_comment_still_parse():
    head = b'<!DOCTYPE tv SYSTEM "xmltv.dtd">\n<!--' + b"y" * 100_000 + b"-->\n"
    for body in (_xmltv(head=head), _xmltv(), gzip.compress(_xmltv(head=head))):
        by_id, by_name = epg.parse_xmltv(body, 1)
        assert by_name == {"svt1": "svt1.se"}
        assert by_id["svt1.se"][2] == ("Rapport",)
    epg.refuse_entities(b"not xml at all")          # left to the parse to refuse


def test_a_gzip_bomb_is_refused_and_a_real_one_unpacked_whole():
    plain = b"<tv>" + b" " * (8 << 20) + b"</tv>"
    bomb = gzip.compress(plain)
    assert len(bomb) < 64 << 10
    with pytest.raises(epg.GuideTooLarge):
        epg.gunzip_gently(bomb, limit=1 << 20)
    assert epg.gunzip_gently(bomb, limit=16 << 20) == plain
    two = gzip.compress(b"<tv>a") + gzip.compress(b"b</tv>")
    assert epg.gunzip_gently(two) == b"<tv>ab</tv>"
    with pytest.raises(zlib.error):
        epg.gunzip_gently(bomb[:len(bomb) // 2])


def test_a_bomb_is_refused_by_the_parse_too(monkeypatch):
    monkeypatch.setattr(epg, "GUIDE_UNPACKED_MAX", 1 << 20)
    with pytest.raises(ValueError):
        epg.parse_xmltv(gzip.compress(_xmltv() + b" " * (4 << 20)), 1)


class _Endless:
    def __init__(self, length: str | None = None) -> None:
        self.headers = {"content-length": length} if length else {}
        self.closed = False
        self.read = 0

    def raise_for_status(self) -> None:
        pass

    def iter_content(self, n):
        while True:
            self.read += n
            yield b" " * n

    def close(self) -> None:
        self.closed = True


class _Whole(_Endless):
    def __init__(self, body: bytes) -> None:
        super().__init__(str(len(body)))
        self.body = body

    def iter_content(self, n):
        for i in range(0, len(self.body), n):
            yield self.body[i:i + n]


def test_an_answer_that_never_ends_is_cut_off(monkeypatch):
    monkeypatch.setattr(epg, "GUIDE_DOWNLOAD_MAX", 1 << 20)
    g = XmltvGuide(None, "http://guide.invalid/a.xml")
    r = _Endless()
    monkeypatch.setattr(g, "_get", lambda url, **kw: r)
    with pytest.raises(epg.GuideTooLarge):
        g._download()
    assert r.closed and r.read <= (1 << 20) + (64 << 10)
    said = _Endless(str(5 << 30))                    # its Content-Length says so
    monkeypatch.setattr(g, "_get", lambda url, **kw: said)
    with pytest.raises(epg.GuideTooLarge):
        g._download()
    assert said.read == 0 and said.closed
    whole = _Whole(_xmltv())
    monkeypatch.setattr(g, "_get", lambda url, **kw: whole)
    assert g._download() == _xmltv()


def test_of_several_guides_the_one_too_large_is_left_out(monkeypatch):
    monkeypatch.setattr(epg, "GUIDE_DOWNLOAD_MAX", 1 << 20)
    asked: list[dict] = []

    def get(url, **kw):
        asked.append(kw)
        return _Endless() if "big" in url else _Whole(_xmltv("TV Avisen"))
    g = XmltvGuide(None, "http://a.invalid/big.xml, http://b.invalid/ok.xml")
    monkeypatch.setattr(g, "_get", get)
    data = g._download()
    assert b"TV Avisen" in data and data.count(b"<programme") == 1
    assert all(kw.get("stream") for kw in asked), "read as it comes, never whole first"
