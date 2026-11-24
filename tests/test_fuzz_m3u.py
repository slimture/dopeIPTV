"""Fuzzing M3U playlists (tests/fuzz_util.py): the M3U client's parse from
the bytes of a playlist file on - #EXTINF and its attributes, the header's
url-tvg, catch-up - its categories, streams and archive addresses, and the
stable channel ids it gives (core/m3u_ids.py). Never an exception but "no
channels", never time that grows faster than the list."""
from __future__ import annotations

import os
import random

import pytest

from dopeiptv.core import m3u_ids
from dopeiptv.i18n import LocalizedError
from dopeiptv.providers.client import M3UClient
from tests import fuzz_util as fz

SEEDS = [
    b'#EXTM3U url-tvg="http://epg.example/guide.xml.gz,http://b.example/g.xml" x-tvg-url="x"\n'
    b'#EXTINF:-1 tvg-id="svt1.se" tvg-name="SVT1" tvg-logo="http://logo.example/svt1.png" '
    b'group-title="Sverige, HD" catchup="default" catchup-days="7" '
    b'catchup-source="http://a.example/{utc}/{duration}.ts",SVT1 HD\n'
    b'http://stream.example/live/u/p/1.ts\n'
    b'#EXTINF:0 tvg-id="" group-title="News",CNN\n#EXTVLCOPT:http-user-agent=x\n'
    b'https://stream.example/cnn/index.m3u8?token=abc\n'
    b'#EXTINF:-1,No group\nrtmp://stream.example/app/key\n'
    b'#EXTINF:-1 timeshift="3" catchup-id="77",Shifted\nhttp://s.example/a/b/c.m3u8\n',
    "﻿#EXTM3U\n#EXTINF:-1 tvg-chno=\"5\" group-title=\"Kanäle\",Das Erste\r\n"
    "udp://@239.1.1.1:1234\r\n#EXTINF:-1 catchup=\"flussonic\" tvg-rec=\"2\",Flu\r\n"
    "http://f.example/ch/index.m3u8\r\n".encode("utf-8"),
    "#EXTM3U\n#EXTINF:-1 group-title=\"Ñoño\",Café\nhttp://x.example/ö.ts\n".encode("cp1252"),
    b"#EXTINF:-1,Two\nhttp://x.example/1\n#EXTINF:-1,Two\nhttp://x.example/2\n",
    b"",
]
TOKENS = ("#EXTINF:", "#EXTINF:-1 ", "#EXTM3U ", "#EXTGRP:", '"', ",", "=", " ", "\n", "\r", "tvg-id=",
          "tvg-name=", "tvg-logo=", "group-title=", "catchup=", "catchup-source=", "catchup-days=",
          "timeshift=", "tvg-rec=", "catchup-id=", "url-tvg=", "x-tvg-url=", "http://", "-", ".", "#")
NOW = 1_780_000_000


def make(rng: random.Random) -> tuple[bytes, bool]:
    base = rng.choice(SEEDS)
    if rng.random() < 0.15:
        data = fz.odd_encoding(rng, fz.mutate_text(rng, base.decode("utf-8", "replace"), tokens=TOKENS))
    else:
        data = fz.mutate_bytes(rng, base, tokens=TOKENS, others=SEEDS)
    return data, rng.random() < 0.5


@pytest.fixture
def playlist(tmp_path):
    path = str(tmp_path / "list.m3u")

    def read(arg: tuple[bytes, bool]) -> M3UClient:
        data, stable = arg
        with open(path, "wb") as f:
            f.write(data)
        c = M3UClient(path, stable=stable, map_path=str(tmp_path / "map.json") if not stable else "")
        try:
            c.authenticate()
        except LocalizedError as e:
            assert e.key in M3UClient.EMPTY_KEYS
            return c
        return c
    return read


def test_fuzz_m3u_client(playlist):
    from datetime import datetime, timezone
    start = datetime.fromtimestamp(NOW - 3600, timezone.utc)

    def target(arg: tuple[bytes, bool]) -> list:
        c = playlist(arg)
        cats = c.live_categories()
        rows = c.live_streams()
        assert all(isinstance(r["stream_id"], int) for r in rows)
        assert len(rows) <= arg[0].count(b"\n") + arg[0].count(b"\r") + 1
        for r in rows[:5]:
            assert isinstance(c.live_url(r["stream_id"]), str)
            for u in c.timeshift_urls(r["stream_id"], start, 60):
                assert isinstance(u, str)
        for cat in cats[:3]:
            c.live_streams(cat["category_id"])
        return rows
    fz.fuzz("m3u", target, make, seeds=[(s, b) for s in SEEDS for b in (False, True)],
            check=fz.bounded(40, 5000))


def test_fuzz_m3u_ids():
    def make_keys(rng: random.Random) -> list[str]:
        # What a playlist's text gives: read from bytes, so never a lone surrogate.
        names = [fz.decoded(fz.mutate_text(rng, rng.choice(("SVT1", "CNN", "", " a  b ")), 2))
                 for _ in range(rng.randint(0, 40))]
        return [m3u_ids.channel_key(rng.choice(("", "x.se", names[0] if names else "")), n) for n in names]

    def target(keys: list[str]) -> list[int]:
        ids = m3u_ids.stable_ids(keys)
        assert len(ids) == len(keys) == len(set(ids))
        assert all(m3u_ids.LINE_MAX <= i < 2 * m3u_ids.LINE_MAX for i in ids)
        return ids
    fz.fuzz("m3u-ids", target, make_keys)

    def make_old(rng: random.Random) -> tuple:
        return (fz.odd_value(rng) if rng.random() < 0.5 else rng.randrange(-5, 50),
                rng.choice(fz.ODD_TEXT), rng.choice(fz.ODD_TEXT))
    remap = m3u_ids.Remap.of([(1, m3u_ids.LINE_MAX + 5, "SVT1", "http://a/1"),
                              (2, m3u_ids.LINE_MAX + 9, "svt1", "http://a/2")])

    def lookup(arg: tuple) -> object:
        old, name, url = arg
        if isinstance(old, fz.Raw):
            old = old.text
        got = remap.new_id(old, name, url)
        assert got is None or isinstance(got, int)
        return got
    fz.fuzz("m3u-ids-remap", lookup, make_old)


# -- how the time grows ---------------------------------------------------------------

def _client_from(text: str) -> M3UClient:
    c = M3UClient("")
    c._parse(text)
    c._loaded = True
    return c


def test_many_groups_take_linear_time():
    """Every channel in a category of its own: the categories are listed in
    time that grows with the list, not with its square."""
    def make_list(n: int) -> M3UClient:
        return _client_from("#EXTM3U\n" + "".join(
            f'#EXTINF:-1 group-title="g{i}",c{i}\nhttp://x.example/{i}\n' for i in range(n)))
    fz.linear(lambda c: c.live_categories(), make_list, 1500)


@pytest.mark.parametrize("line", [
    "#EXTINF:" + "1" * 20000,                                 # digits, no comma
    "#EXTINF:-1 " + 'a="b" ' * 4000,                           # attributes, no comma
    "#EXTINF:-1 " + '"' * 20001 + ",x",                        # quotes that do not balance
    "#EXTINF:-1 " + "tvg-id" * 5000 + ",x",
    "#EXTM3U " + 'url-tvg="' * 5000,
])
def test_long_lines_take_linear_time(line):
    def make_list(n: int) -> str:
        return line[:n] + "\nhttp://x.example/1\n"
    fz.linear(_client_from, make_list, len(line) // 8)


def test_many_channels_alike_take_linear_time():
    fz.linear(lambda keys: m3u_ids.stable_ids(keys), lambda n: [m3u_ids.channel_key("x", "y")] * n, 5000)


def test_the_archive_addresses_of_a_long_address():
    from datetime import datetime, timezone
    start = datetime.fromtimestamp(NOW, timezone.utc)
    for url in ("http://x/" + "/" * 20000 + "a.ts", "http://x/" + "a." * 10000, "http://x/" + "/a" * 10000 + "?"):
        c = _client_from(f'#EXTINF:-1 catchup="default",c\n{url}\n')
        with fz.time_limit(5):
            c.timeshift_urls(c.live_streams()[0]["stream_id"], start, 30)


def test_budget_from_the_environment(monkeypatch):
    monkeypatch.setenv("DOPEIPTV_FUZZ_SECONDS", "2.5")
    assert fz.budget() == 2.5
    monkeypatch.setenv("DOPEIPTV_FUZZ_SECONDS", "x")
    assert fz.budget() == fz.BUDGET
    monkeypatch.delenv("DOPEIPTV_FUZZ_SECONDS")
    assert fz.budget(0.1) == 0.1
    # The same input for the same number, wherever it runs.
    assert fz.rng_for("t", 3, 0).random() == fz.rng_for("t", 3, 0).random()
    assert os.environ.get("DOPEIPTV_FUZZ_SEED", "0").isdecimal()
