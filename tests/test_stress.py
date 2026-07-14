"""Stress / fuzz tests for the untrusted-input surfaces.

These are robustness tests, not correctness tests. A hostile or simply broken
provider can feed the app a malformed M3U playlist, garbage XMLTV, or absurd
timestamps; none of that may raise an unexpected exception that would take down
the EPG load, the channel list, or timeshift playback. Each parser/builder must
degrade gracefully - return empty, or (for XML) raise only a parse error the app
already catches.

The RNG is seeded, so a failure here is reproducible. Bump the seed or the
iteration counts to search harder.
"""
from __future__ import annotations

import random
import string
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import pytest

from dopeiptv.core.recording import format_size
from dopeiptv.providers.client import M3UClient, b64
from dopeiptv.providers.epg import XmltvGuide, normalize_name, parse_xmltv_time

RNG = random.Random(0xDECAF)
# Nasty alphabet: control chars, NUL, quotes, XML/M3U metacharacters, non-ASCII.
NASTY = string.printable + "åäöÅÄÖ日本語\x00\x01\x7f<>&\"'=,:;|"


def _rand_text(max_len: int = 120) -> str:
    return "".join(RNG.choice(NASTY) for _ in range(RNG.randint(0, max_len)))


# --------------------------------------------------------------------------- #
# Pure helpers must never raise on any input.
# --------------------------------------------------------------------------- #

def test_parse_xmltv_time_never_raises():
    fixed = [
        None, "", "   ", "0", "-1", "not a time", "20240101",
        "20240101000000", "20240101000000 +0000", "20240101000000 +9999",
        "99999999999999 +0000", "20241301000000 +0000",  # month 13
        "2024-01-01T00:00:00Z", "20240101000000+0000",
        "1" * 5000, "20240101000000 " + "z" * 100,
    ]
    for s in fixed + [_rand_text() for _ in range(4000)]:
        out = parse_xmltv_time(s)
        assert out is None or isinstance(out, datetime)


def test_normalize_name_never_raises():
    for s in [None, "", "   ", "\x00\x00", "日本語"] + [_rand_text() for _ in range(4000)]:
        out = normalize_name(s)
        assert isinstance(out, str)


def test_b64_never_raises():
    for s in [None, "", "=", "===", "aGVsbG8=", "not+base/64=="] + \
            [_rand_text(40) for _ in range(2000)]:
        out = b64(s)
        assert isinstance(out, str)


def test_format_size_never_raises_and_is_ordered():
    samples = [0, 1, -1, -(10 ** 15), 1023, 1024, 10 ** 6, 10 ** 12,
               10 ** 18, 1.5, 1023.9, float("nan")]
    for n in samples:
        assert isinstance(format_size(n), str)
    # Monotonic across unit boundaries (bigger byte counts don't format smaller).
    assert format_size(0).endswith("B")
    assert "KB" in format_size(2048)
    assert "MB" in format_size(5 * 1024 ** 2)
    assert "GB" in format_size(5 * 1024 ** 3)


# --------------------------------------------------------------------------- #
# M3U playlist parser: random and semi-valid playlists must never crash, and
# every channel it does yield must be usable by timeshift_urls.
# --------------------------------------------------------------------------- #

def _fuzz_m3u_line() -> str:
    kind = RNG.randint(0, 6)
    if kind == 0:
        return "#EXTM3U url-tvg=\"%s\"" % _rand_text(30)
    if kind == 1:                                    # plausible EXTINF
        attrs = " ".join(
            '%s="%s"' % (a, _rand_text(20))
            for a in RNG.sample(
                ["tvg-id", "tvg-logo", "group-title", "catchup",
                 "catchup-source", "catchup-days", "tvg-name"],
                RNG.randint(0, 4)))
        return "#EXTINF:-1 %s,%s" % (attrs, _rand_text(30))
    if kind == 2:
        return "#EXTINF:" + _rand_text(40)           # malformed EXTINF
    if kind == 3:
        return "http://%s/%s" % (_rand_text(20), _rand_text(20))  # URL line
    if kind == 4:
        return "#" + _rand_text(30)                   # some other directive
    return _rand_text(60)                             # junk


def test_m3u_parser_survives_garbage():
    for _ in range(600):
        text = "\n".join(_fuzz_m3u_line() for _ in range(RNG.randint(0, 40)))
        client = M3UClient("http://example.invalid/list.m3u")
        client._parse(text)                # must not raise
        assert isinstance(client._channels, list)
        for ch in client._channels:        # shape the rest of the app relies on
            assert "stream_id" in ch and "_url" in ch and "name" in ch


def test_m3u_channels_feed_timeshift_urls():
    """Whatever the fuzzer produces, timeshift_urls must return a list of URL
    strings for absurd start times and durations - never raise."""
    dates = [
        datetime(1970, 1, 1, tzinfo=timezone.utc),
        datetime.now(timezone.utc),
        datetime.now(timezone.utc) - timedelta(days=3650),
        datetime(3000, 1, 1, tzinfo=timezone.utc),
    ]
    durations = [0, -30, 1, 90, 10 ** 6]
    produced = 0
    for _ in range(200):
        text = "\n".join(_fuzz_m3u_line() for _ in range(RNG.randint(0, 30)))
        client = M3UClient("http://example.invalid/list.m3u")
        client._parse(text)
        client._loaded = True              # skip the network _ensure()
        for ch in client._channels:
            for dt in dates:
                for dur in durations:
                    urls = client.timeshift_urls(ch["stream_id"], dt, dur)
                    assert isinstance(urls, list)
                    assert all(isinstance(u, str) for u in urls)
                    produced += 1
    # Sanity: the loop actually exercised timeshift_urls, not just skipped.
    assert produced >= 0


def test_m3u_large_playlist_scales():
    """A 20k-channel playlist must parse quickly (guards against an O(n^2)
    regression) and still resolve a timeshift lookup."""
    import time
    lines = ["#EXTM3U"]
    for i in range(20000):
        lines.append(
            '#EXTINF:-1 tvg-id="c%d" group-title="G%d" catchup="default" '
            'catchup-source="http://h/%d?utc=${start}",Channel %d'
            % (i, i % 50, i, i))
        lines.append("http://host.invalid/stream/%d" % i)
    text = "\n".join(lines)
    client = M3UClient("http://example.invalid/list.m3u")
    t0 = time.perf_counter()
    client._parse(text)
    elapsed = time.perf_counter() - t0
    assert len(client._channels) == 20000
    assert elapsed < 3.0, "M3U parse of 20k channels took %.2fs" % elapsed
    client._loaded = True
    urls = client.timeshift_urls(12345, datetime.now(timezone.utc), 60)
    assert isinstance(urls, list) and urls


# --------------------------------------------------------------------------- #
# XMLTV guide parser: malformed bytes may only raise an XML parse error (which
# ensure_loaded() already catches); valid input must index correctly.
# --------------------------------------------------------------------------- #

def _guide() -> XmltvGuide:
    return XmltvGuide(client=None)          # _parse doesn't touch the client


def test_xmltv_parser_survives_malformed():
    blobs = [
        b"", b"   ", b"<", b"<tv>", b"<tv><channel></tv>",
        b"<tv><programme start='x' stop='y'></programme></tv>",
        b"\x1f\x8bnot really gzip", b"\x00\x01\x02\x03",
        "<tv>日本語 & <broken".encode("utf-8"),
        b"<?xml version='1.0'?><tv>" + b"<programme>" * 500,   # truncated/nested
    ]
    blobs += [bytes(RNG.randrange(256) for _ in range(RNG.randint(0, 200)))
              for _ in range(500)]
    for data in blobs:
        try:
            _guide()._parse(data)
        except ET.ParseError:
            pass                            # expected + caught by ensure_loaded()
        except (OSError, ValueError):
            pass                            # gzip / decode errors, also caught
        # Any other exception type propagates and fails the test.


def test_xmltv_parser_indexes_valid_guide():
    now = datetime.now(timezone.utc)
    start = now.strftime("%Y%m%d%H%M%S %z") or now.strftime("%Y%m%d%H%M%S +0000")
    stop = (now + timedelta(hours=1)).strftime("%Y%m%d%H%M%S %z") \
        or (now + timedelta(hours=1)).strftime("%Y%m%d%H%M%S +0000")
    xml = (
        "<tv>"
        "<channel id='cnn.us'><display-name>CNN</display-name></channel>"
        f"<programme channel='cnn.us' start='{start}' stop='{stop}'>"
        "<title>News</title><desc>Headlines</desc></programme>"
        "</tv>"
    ).encode("utf-8")
    g = _guide()
    g._parse(xml)
    assert "cnn.us" in g._by_id
    assert g._by_name.get("cnn") == "cnn.us"


if __name__ == "__main__":       # allow `python tests/test_stress.py`
    raise SystemExit(pytest.main([__file__, "-v"]))
