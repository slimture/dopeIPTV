"""XML from outside declares no DTD of its own, however it is written
(core/safe_xml.py): a "]" inside a declaration, UTF-16 with or without a
byte order mark, a declared UTF-7, a long comment first - each is refused
by every parser of a box's, a device's or a guide's XML, and what the boxes
always parsed still parses."""
from __future__ import annotations

import gzip
import time
import xml.etree.ElementTree as ET

import pytest

from dopeiptv.core import safe_xml
from dopeiptv.providers import boxes, dlna, epg, satip
from dopeiptv.services import share

UNIT = "A" * 10_000
REFS = "&b;" * 90                        # 900 000 characters from 270


def _bombs(root: str, inner: str) -> list[bytes]:
    """The same small document that expands to 900 000 characters, written
    every way a byte check or a pattern misses."""
    subset = f'<!ENTITY a "]"><!ENTITY b "{UNIT}">'
    body = f"<!DOCTYPE {root} [{subset}]><{root}>{inner}</{root}>"
    return [
        body.encode(),
        ("<!--" + "x" * 70_000 + "-->" + body).encode(),
        body.encode("utf-16-le"),
        body.encode("utf-16-be"),
        ('<?xml version="1.0" encoding="UTF-16"?>' + body).encode("utf-16"),
    ]


def _expanded(root: ET.Element | None) -> int:
    return 0 if root is None else len("".join(root.itertext()))


def test_the_check_refuses_every_way_of_writing_a_dtd():
    for doc in _bombs("x", REFS):
        with pytest.raises(ValueError):
            safe_xml.refuse_dtd(doc)
        with pytest.raises(ValueError):
            safe_xml.fromstring(doc)
    seven = ('<?xml version="1.0" encoding="utf-7"?>'
             '+ADwAIQ-DOCTYPE x +AFs-+ADwAIQ-ENTITY b +ACI-aaaa+ACIAPgBdAD4-+ADw-x/+AD4-')
    with pytest.raises(ValueError):
        safe_xml.refuse_dtd(seven.encode("ascii").decode("utf-7"))
    # What a guide or a device really sends parses.
    safe_xml.refuse_dtd(b'<?xml version="1.0"?><!DOCTYPE tv SYSTEM "xmltv.dtd"><tv/>')
    assert safe_xml.fromstring("<root><a>1</a></root>").findtext("a") == "1"
    safe_xml.refuse_dtd(b"not xml at all")          # left to the parse to refuse


def test_a_box_s_dtd_is_dropped_and_never_expanded():
    for doc in _bombs("e2about", f"<e2model>{REFS}</e2model>"):
        try:
            root = boxes.parse_xml(doc)
        except ET.ParseError:
            root = None
        assert _expanded(root) < 1000
    seven = ('<?xml version="1.0" encoding="utf-7"?>'
             '+ADwAIQ-DOCTYPE e +AFsAPAAh-ENTITY a +ACIAXQAi-+AD4-'
             '+ADwAIQ-ENTITY b +ACI-' + UNIT + '+ACIAPgBdAD4-+ADw-e+AD4-'
             + "+ACY-b;" * 90 + '+ADw-/e+AD4-').encode("ascii")
    with pytest.raises(ET.ParseError):
        boxes.parse_xml(seven)
    # As before: a DOCTYPE with entities it does not use is dropped, the rest
    # parses - with a "]" or a ">" inside a literal, a comment in the subset.
    for dtd in ('<!DOCTYPE x [<!ENTITY a "aaaa">]>', '<!DOCTYPE x [<!ENTITY a "]>">]>',
                "<!DOCTYPE x [<!-- ] > --><!ENTITY a 'b'>]>", '<!DOCTYPE x SYSTEM "x.dtd">',
                "<!doctype x>"):
        raw = f'<?xml version="1.0" encoding="UTF-8"?>{dtd}<e2service><n>ok</n></e2service>'
        assert boxes.parse_xml(raw.encode()).findtext("n") == "ok", dtd


def test_a_device_s_description_with_a_dtd_is_refused():
    caps = "<X_SATIPCAP>DVBS2-4</X_SATIPCAP>"
    inner = (f"<device><deviceType>urn:ses-com:device:SatIPServer:1</deviceType>"
             f"<friendlyName>{REFS}</friendlyName>{caps}</device>")
    for doc in _bombs("root", inner):
        assert satip.parse_description(doc, "http://127.0.0.1:1/d.xml") is None
        with pytest.raises(ValueError):
            dlna._parse_xml(doc)
    # A real one, with or without a DOCTYPE naming nothing of its own.
    for head in ("", "<!DOCTYPE root>"):
        doc = f'<?xml version="1.0"?>{head}<root><device><friendlyName>TV</friendlyName></device></root>'
        assert dlna._parse_xml(doc.encode()).findtext("device/friendlyName") == "TV"


def test_a_shared_guide_with_a_dtd_is_left_out(tmp_path):
    bomb = tmp_path / "bomb.xml"
    bomb.write_bytes(gzip.compress(
        (f'<?xml version="1.0"?><!DOCTYPE tv [<!ENTITY b "{UNIT}">]><tv>'
         f'<channel id="a"><display-name>{REFS}</display-name></channel></tv>').encode()))
    good = tmp_path / "good.xml"
    good.write_bytes(b'<?xml version="1.0"?><!DOCTYPE tv SYSTEM "xmltv.dtd"><tv>'
                     b'<channel id="a"><display-name>A</display-name></channel></tv>')
    chans = [{"number": 1, "name": "A", "source": "s", "epg_id": "a", "logo": ""}]
    out = b"".join(share.lineup_guide([("s", str(bomb))], chans))
    assert len(out) < 2000 and UNIT.encode() not in out
    out = b"".join(share.lineup_guide([("s", str(good))], chans))
    assert b"<display-name>A</display-name>" in out


def test_the_epg_check_is_the_shared_one():
    with pytest.raises(ValueError):
        epg.refuse_entities(_bombs("tv", REFS)[2])


def test_dropping_a_dtd_takes_linear_time_on_hostile_text():
    for text in ("<!DOCTYPE" * 40_000, "<!DOCTYPE x [" + "'" * 200_000,
                 "<!DOCTYPE x [" + "<!--" * 100_000, "<!DOCTYPE x [" + "]" * 200_000):
        t = time.perf_counter()
        safe_xml.drop_dtd(text)
        try:
            boxes.parse_xml(text.encode())
        except ET.ParseError:
            pass
        assert time.perf_counter() - t < 2.0, text[:20]
