"""Values from outside that are no values a real source sends - an encoding
that is no text encoding, a number too long or too large to be one - are
passed over where they stand: the source, the guide or the answer they came
in goes on as before."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from dopeiptv.services import popular, share

#: A digit to str.isdigit() that int() refuses, and digits past int()'s limit.
SUPER, ENDLESS = "²", "9" * 5000


def test_a_number_no_source_sends_never_drops_the_source():
    from dopeiptv.providers import autotimer, nextpvr, satip, simkl, stalker
    from dopeiptv.providers.client import M3UClient
    for days in (SUPER, ENDLESS):
        m3u = M3UClient("x")
        m3u._parse(f'#EXTM3U\n#EXTINF:-1 catchup-days="{days}",News\nhttp://h/1\n')
        assert [c["name"] for c in m3u._channels] == ["News"]
        assert m3u._channels[0]["tv_archive_duration"] == 7
    rows, _guide_url = satip.parse_m3u(f'#EXTINF:0 tvg-chno="{SUPER}",A\nrtsp://h/?freq=1\n')
    assert rows[0]["chno"] == 0
    assert satip.service_pid(f"0,{SUPER},{ENDLESS},5100") == "5100"
    assert stalker._id(SUPER) == SUPER and stalker._id("17") == 17 and stalker._id(ENDLESS) == ENDLESS
    assert stalker._utc_stamp(SUPER) is None and stalker._expiry(ENDLESS) is None
    assert stalker._episode_key(SUPER, 1, 2) == stalker._episode_key(SUPER, 1, 2)
    for rid in (SUPER, "--5", ENDLESS):
        assert autotimer.parse_rule(ET.Element("timer", id=rid)).id is None
    assert autotimer.parse_rule(ET.Element("timer", id="-3")).id == -3
    assert nextpvr._secs(SUPER) == 0 and nextpvr._secs("--5") == 0 and nextpvr._secs("1700000000") == 1700000000
    assert simkl._int(SUPER) is None and simkl._int("42") == 42
    assert popular._is_year("\U00010a40\U00010a41\U00010a42\U00010a43") is False


def test_a_number_too_large_to_be_one_is_none():
    from dopeiptv.providers import autotimer, dvbviewer, enigma2, radio_browser, tvheadend
    from dopeiptv.providers.boxes import build_xmltv
    for text in ("1e999", "-1e999", "inf", "nan"):
        assert enigma2._int(text) == 0 and tvheadend._num(text) == 0
        assert autotimer._int(text) == 0 and dvbviewer.duration_secs(text) == 0
    assert radio_browser._int(float("inf")) == 0
    data = build_xmltv([("a", "A")], [
        {"channel": "a", "start": 10 ** 20, "stop": 10 ** 20 + 60, "title": "Never"},
        {"channel": "a", "start": 1_700_000_000, "stop": 1_700_003_600, "title": "Real"}])
    assert b"Real" in data and b"Never" not in data


def _guide(enc: str) -> list[bytes]:
    return [f'<?xml version="1.0" encoding="{enc}"?>\n<tv>'.encode(),
            b'<channel id="a"><display-name>A</display-name></channel></tv>\n']


def test_a_guide_naming_a_codec_that_is_no_text_encoding_is_passed_on_as_it_is():
    for enc in ("zlib", "bz2", "base64", "hex", "rot13", "uu", "quopri", "idna", "nonsense"):
        body = b"".join(share.guide_body(iter(_guide(enc))))
        assert b'<channel id="a">' in body, enc
    # A real one is still decoded into UTF-8.
    latin = [b'<?xml version="1.0" encoding="ISO-8859-1"?>\n<tv>',
             b'<channel id="a"><display-name>K\xe4nal</display-name></channel></tv>\n']
    assert "Känal".encode() in b"".join(share.guide_body(iter(latin)))


def test_a_cached_guide_is_unpacked_a_slice_at_a_time(tmp_path):
    import gzip
    plain = b"<tv>" + b" " * (64 << 20) + b"</tv>"
    path = tmp_path / "epg_x.xml"
    path.write_bytes(gzip.compress(plain, 9) + gzip.compress(b"<!-- two -->", 9))
    sizes, total = [], 0
    for chunk in share.plain_chunks(str(path)):
        sizes.append(len(chunk))
        total += len(chunk)
    assert max(sizes) <= share._UNPACK_STEP
    assert total == len(plain) + len(b"<!-- two -->")
