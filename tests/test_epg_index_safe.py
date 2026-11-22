"""The guide's index on disk is plain data, read as nothing else.

It was a pickle, and a pickle may name any callable and call it: a file in
the cache folder that something else wrote ran as the app the moment the
guide was read. A restricted unpickler held that off; now there is no
pickle at all (providers/epg.py index_blob, read_index_blob): a header
with its sizes, the texts, and arrays' bytes read with array.frombytes and
checked against the header. An index an older app wrote - a pickle,
whatever it holds - is a cache miss: the guide is parsed again from its
XML, and nothing is run."""
from __future__ import annotations

import gzip
import io
import os
import pickle
import struct
import sys
from array import array

from dopeiptv.providers import epg
from dopeiptv.providers.epg import XmltvGuide

XML = ('<?xml version="1.0" encoding="UTF-8"?>\n'
       '<tv><channel id="svt1.se"><display-name>SVT1</display-name></channel>'
       '<channel id="svt2.se"><display-name>SVT2</display-name></channel>'
       '<programme channel="svt1.se" start="20990101120000 +0000" '
       'stop="20990101130000 +0000"><title>Rapport</title><desc>Nyheter, åäö</desc></programme>'
       '<programme channel="svt1.se" start="20990101130000 +0000" '
       'stop="20990101140000 +0000"><title>Sport</title><desc></desc></programme>'
       '<programme channel="svt2.se" start="20990101120000 +0000" '
       'stop="20990101130000 +0000"><title>Rapport</title><desc>Nyheter, åäö</desc></programme>'
       '</tv>').encode()


class _Run:
    """Unpickling this calls *fn(*args)*."""

    def __init__(self, fn, *args):
        self.fn, self.args = fn, args

    def __reduce__(self):
        return self.fn, self.args


def _touch_command(marker) -> str:
    return f'"{sys.executable}" -c "open(r\'{marker}\', \'w\').close()"'


def _old_index(*objects, packed: bool = True) -> bytes:
    """An index as an app up to 1.6.8 wrote it: pickles one after another."""
    buf = io.BytesIO()
    p = pickle.Pickler(buf, protocol=pickle.HIGHEST_PROTOCOL)
    for obj in objects:
        p.dump(obj)
    return gzip.compress(buf.getvalue()) if packed else buf.getvalue()


def test_an_index_reads_back_as_the_guide_it_was_made_of():
    by_id, by_name = epg.parse_xmltv(XML, 1)
    blob = epg.index_blob(by_id, by_name)
    assert blob[:2] == b"\x1f\x8b"
    got = epg.read_index_blob(blob)
    assert got == (by_id, by_name)
    starts, stops, titles, descs = got[0]["svt1.se"]
    assert type(starts) is array and starts.typecode == "q" and type(stops) is array
    assert titles == ("Rapport", "Sport") and descs == ("Nyheter, åäö", "")
    # A text shared by channels is one text after loading, as it was kept.
    assert got[0]["svt2.se"][2][0] is titles[0]
    # Unpacked too, and an empty guide.
    assert epg.read_index_blob(gzip.decompress(blob)) == (by_id, by_name)
    assert epg.read_index_blob(epg.index_blob({}, {})) == ({}, {})
    # A NUL (which XML cannot carry) never shifts the texts after it.
    q = array("q", [1, 2])
    got = epg.read_index_blob(epg.index_blob({"c": (q, q, ("a\x00b", "c"), ("", "d"))}, {"n": "c"}))
    assert got == ({"c": (q, q, ("a�b", "c"), ("", "d"))}, {"n": "c"})


def test_an_index_holds_no_code_path_at_all():
    """The format names no type and no callable: a header, NUL-ended texts
    and numbers - nothing a reader could be told to run."""
    by_id, by_name = epg.parse_xmltv(XML, 1)
    raw = gzip.decompress(epg.index_blob(by_id, by_name))
    head = epg._HEAD.unpack(raw[:epg._HEAD.size])
    words = ["svt1", "svt1.se", "svt2", "svt2.se", "Rapport", "Sport", "Nyheter, åäö", ""]
    text = "".join(w + "\0" for w in words).encode()
    assert head == (epg._INDEX_MAGIC, epg._INDEX_VERSION, 8, 4, 2, 2, len(words), 3, len(text))
    rest = raw[epg._HEAD.size:]
    assert rest[:len(text)] == text
    # Then only numbers, exactly as many as the header says: the names' and
    # the channel table's pairs, each channel's starts, stops and texts.
    numbers = rest[len(text):]
    assert len(numbers) == 2 * 2 * 4 + 2 * 2 * 4 + 3 * (8 + 8 + 4 + 4)
    assert array("I" if array("I").itemsize == 4 else "L", numbers[:16]).tolist() == [0, 1, 2, 3]


def test_an_older_apps_pickled_index_is_never_unpickled(tmp_path):
    marker = tmp_path / "ran"
    head = {"v": 3, "pieces": 1, "by_name": {}}
    by_id, by_name = epg.parse_xmltv(XML, 1)
    for packed in (True, False):
        for blob in (_old_index(_Run(os.system, _touch_command(marker)), packed=packed),
                     _old_index(head, {"c": _Run(os.system, _touch_command(marker))}, packed=packed),
                     # Even a harmless one of the old format: no longer an index.
                     _old_index({"v": 3, "pieces": 1, "by_name": by_name}, by_id, packed=packed)):
            assert epg.read_index_blob(blob) is None
    assert not marker.exists(), "the command in the index ran"


def _sections(by_id=None, by_name=None) -> list[bytes]:
    """A real index's raw bytes cut into its header and the rest."""
    if by_id is None:
        by_id, by_name = epg.parse_xmltv(XML, 1)
    raw = gzip.decompress(epg.index_blob(by_id, by_name or {}))
    return [raw[:epg._HEAD.size], raw[epg._HEAD.size:]]


def _with_head(raw_rest: bytes, **changes) -> bytes:
    head, rest = _sections()
    fields = list(epg._HEAD.unpack(head))
    names = ("magic", "version", "time_size", "ref_size", "channels", "names", "texts",
             "programmes", "text_bytes")
    for k, v in changes.items():
        fields[names.index(k)] = v
    return epg._HEAD.pack(*fields) + (raw_rest if raw_rest is not None else rest)


def test_what_is_no_index_of_the_apps_is_a_miss():
    head, rest = _sections()
    whole = head + rest
    assert epg.read_index_blob(whole) is not None
    for wrong in (
            b"", b"\x00" * 10, head,                                    # nothing, or a header alone
            whole[:-1], whole[:len(whole) // 2],                       # cut short
            whole + b"\x00",                                           # more than it says
            _with_head(None, magic=b"dpIPTVgX"),
            _with_head(None, version=epg._INDEX_VERSION - 1),       # an older app's
            _with_head(None, version=epg._INDEX_VERSION + 1),
            _with_head(None, time_size=4), _with_head(None, ref_size=8),
            _with_head(None, texts=7), _with_head(None, texts=9),      # texts it does not hold
            _with_head(None, programmes=4),                            # the table says three
            _with_head(None, channels=3), _with_head(None, names=3),
            _with_head(None, text_bytes=1 << 40),                       # past any guide
            _with_head(None, programmes=1 << 40), _with_head(None, channels=1 << 30)):
        assert epg.read_index_blob(wrong) is None
        assert epg.read_index_blob(gzip.compress(wrong)) is None
    # A text that is no UTF-8, one not ended, one megabytes long.
    texts_end = epg._HEAD.unpack(head)[8]
    assert epg.read_index_blob(head + b"\xff" + rest[1:]) is None
    no_end = rest[:texts_end - 1] + b"x" + rest[texts_end:]
    assert epg.read_index_blob(head + no_end) is None
    long_text = b"x" * (epg._LONGEST_TEXT + 2 * epg._READ_STEP) + b"\x00"
    assert epg.read_index_blob(_with_head(long_text, texts=1, text_bytes=len(long_text), names=0,
                                          channels=0, programmes=0)) is None
    # A text's number past the texts.
    by_name_at = texts_end
    bad = bytearray(rest)
    bad[by_name_at:by_name_at + 4] = struct.pack("<I", 1000)
    assert epg.read_index_blob(head + bytes(bad)) is None
    # A gzip file whose check sum does not hold.
    packed = bytearray(gzip.compress(whole))
    packed[-8] ^= 0xFF
    assert epg.read_index_blob(bytes(packed)) is None


def test_a_planted_index_is_a_cache_miss_and_the_guide_is_read_again(tmp_path, monkeypatch):
    monkeypatch.setattr(XmltvGuide, "PARSE_ELSEWHERE", False)
    marker = tmp_path / "ran"
    cache = tmp_path / "epg_a.xml"
    cache.write_bytes(gzip.compress(XML))
    later = cache.stat().st_mtime + 5
    g = XmltvGuide(client=None, cache_path=str(cache))
    index = g._index_path()
    old = tmp_path / "epg_a.xml.pkl"            # where an app up to 1.6.8 kept it
    for path in (index, old):
        path.write_bytes(_old_index(_Run(os.system, _touch_command(marker))))
        os.utime(path, (later, later))          # newer than the guide: it is looked at
    assert g._load_from_disk() is True
    assert not marker.exists()
    assert g._by_id["svt1.se"][2] == ("Rapport", "Sport")
    # What stands there now is an index of the app's own; the old one is gone.
    assert epg.read_index_blob(index.read_bytes()) is not None
    assert index.name == "epg_a.xml.idx" and not old.exists()
