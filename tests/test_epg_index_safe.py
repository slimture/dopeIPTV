"""The guide's index on disk is read as an index and as nothing else.

It is a pickle, and a pickle may name any callable and call it: a file in
the cache folder that something else wrote ran as the app the moment the
guide was read (providers/epg.py read_index_blob). The loader takes the two
names an index holds - array's own, for its time columns - and refuses the
rest; what it reads is then checked to be an index. Anything else is a
cache miss: the guide is parsed again from its XML, and nothing is run."""
from __future__ import annotations

import gzip
import io
import logging
import os
import pickle
import pickletools
import subprocess
import sys
from array import array

import pytest

from dopeiptv.core.log import log
from dopeiptv.providers import epg
from dopeiptv.providers.epg import XmltvGuide

XML = (b'<?xml version="1.0" encoding="UTF-8"?>\n'
       b'<tv><channel id="svt1.se"><display-name>SVT1</display-name></channel>'
       b'<programme channel="svt1.se" start="20990101120000 +0000" '
       b'stop="20990101130000 +0000"><title>Rapport</title><desc>News</desc></programme></tv>')


class _Run:
    """Unpickling this calls *fn(*args)*."""

    def __init__(self, fn, *args):
        self.fn, self.args = fn, args

    def __reduce__(self):
        return self.fn, self.args


def _touch_command(marker) -> str:
    return f'"{sys.executable}" -c "open(r\'{marker}\', \'w\').close()"'


def _blob(*objects, packed: bool = True) -> bytes:
    """An index file's bytes holding *objects*, one pickle after another."""
    buf = io.BytesIO()
    # One pickler for them all, as index_blob has: its memo runs on from
    # one pickle to the next, and so does the reader's.
    p = pickle.Pickler(buf, protocol=pickle.HIGHEST_PROTOCOL)
    for obj in objects:
        p.dump(obj)
    return gzip.compress(buf.getvalue()) if packed else buf.getvalue()


@pytest.fixture
def warnings():
    seen: list[str] = []

    class Catch(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING:
                seen.append(record.getMessage())
    h = Catch()
    log.addHandler(h)
    yield seen
    log.removeHandler(h)


def test_an_index_that_calls_os_system_is_refused_logged_and_never_run(tmp_path, warnings):
    marker = tmp_path / "ran"
    head = {"v": epg._INDEX_VERSION, "pieces": 1, "by_name": {}}
    for packed in (True, False):
        for blob in (_blob(_Run(os.system, _touch_command(marker)), packed=packed),
                     # Hidden behind a head that looks right, in a piece.
                     _blob(head, {"c": _Run(os.system, _touch_command(marker))}, packed=packed),
                     _blob(head, {"c": _Run(subprocess.getoutput, _touch_command(marker))}, packed=packed)):
            assert epg.read_index_blob(blob) is None
    assert not marker.exists(), "the command in the index ran"
    assert warnings and all("refused" in w for w in warnings), warnings
    assert any("system" in w for w in warnings), warnings


def test_no_other_name_gets_through_the_ones_an_index_holds():
    for fn, args in ((eval, ("1+1",)), (getattr, ("x", "upper")), (__import__, ("os",)),
                     (exec, ("pass",)), (open, (os.devnull,)), (pickle.loads, (b"N.",)),
                     (bytearray, (8,)), (io.BytesIO, ())):
        assert epg.read_index_blob(_blob(_Run(fn, *args))) is None, fn
    # An old-style global and an instance made without a call.
    assert epg.read_index_blob(b"cos\nsystem\n(S'true'\ntR.") is None
    assert epg.read_index_blob(b"(S'true'\nios\nsystem\n.") is None
    # A persistent id and an extension code are no part of an index either.
    assert epg.read_index_blob(b"\x80\x02U\x01xQ.") is None
    assert epg.read_index_blob(b"\x80\x02\x82\x01.") is None


def test_an_index_this_app_wrote_names_only_what_the_loader_takes():
    by_id, by_name = epg.parse_xmltv(XML, 1)
    raw = gzip.decompress(epg.index_blob(by_id, by_name))
    names: set[tuple[str, str]] = set()
    pos = 0
    while pos < len(raw):
        strings: list[str] = []
        end = pos
        for op, arg, at in pickletools.genops(raw[pos:]):
            if isinstance(arg, str):
                strings.append(arg)
            if op.name == "STACK_GLOBAL":
                names.add((strings[-2], strings[-1]))
            elif op.name in ("GLOBAL", "INST"):
                names.add(tuple(str(arg).split(" ", 1)))
            assert op.name not in ("EXT1", "EXT2", "EXT4", "PERSID", "BINPERSID")
            end = pos + at + 1
        pos = end
    assert names == set(epg._INDEX_GLOBALS)


def test_today_s_index_still_reads_and_what_is_no_index_is_a_miss():
    by_id, by_name = epg.parse_xmltv(XML, 1)
    got = epg.read_index_blob(epg.index_blob(by_id, by_name))
    assert got is not None
    assert got[1] == {"svt1": "svt1.se"}
    starts, stops, titles, descs = got[0]["svt1.se"]
    assert isinstance(starts, array) and starts.typecode == "q" and titles == ("Rapport",)
    assert descs == ("News",) and len(stops) == 1

    def head(pieces: int = 1, by_name=None):
        return {"v": epg._INDEX_VERSION, "pieces": pieces, "by_name": by_name or {}}
    q = array("q", [1])
    for wrong in (
            _blob(head(), {"c": (q, q, ("t",))}),                    # three columns
            _blob(head(), {"c": (q, q, ("t",), ("d", "e"))}),         # not the same length
            _blob(head(), {"c": (array("d", [1.0]), q, ("t",), ("d",))}),
            _blob(head(), {"c": ([1], q, ("t",), ("d",))}),
            _blob(head(), {"c": (q, q, (b"t",), ("d",))}),            # a title that is no text
            _blob(head(), {"c": (q, q, ("t",), (None,))}),
            _blob(head(), {7: (q, q, ("t",), ("d",))}),
            _blob(head(), ["c"]),
            _blob(head(by_name={"a": 1}), {"c": (q, q, ("t",), ("d",))}),
            _blob(head(by_name=["a"]), {"c": (q, q, ("t",), ("d",))}),
            _blob(head(pieces=2), {"c": (q, q, ("t",), ("d",))}),     # ends early
            _blob(head(pieces="1"), {"c": (q, q, ("t",), ("d",))})):
        assert epg.read_index_blob(wrong) is None
    assert epg.read_index_blob(_blob(head(), {"c": (q, q, ("t",), ("d",))})) is not None


def test_a_planted_index_is_a_cache_miss_and_the_guide_is_read_again(tmp_path, monkeypatch, warnings):
    monkeypatch.setattr(XmltvGuide, "PARSE_ELSEWHERE", False)
    marker = tmp_path / "ran"
    cache = tmp_path / "epg_a.xml"
    cache.write_bytes(gzip.compress(XML))
    index = tmp_path / "epg_a.xml.pkl"
    index.write_bytes(_blob(_Run(os.system, _touch_command(marker))))
    later = cache.stat().st_mtime + 5
    os.utime(index, (later, later))                 # newer than the guide: it is read
    g = XmltvGuide(client=None, cache_path=str(cache))
    assert g._load_from_disk() is True
    assert not marker.exists()
    assert g._by_id["svt1.se"][2] == ("Rapport",)
    assert any("refused" in w for w in warnings)
    # What stands there now is an index of the app's own.
    assert epg.read_index_blob(index.read_bytes()) is not None
