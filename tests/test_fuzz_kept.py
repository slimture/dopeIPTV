"""Fuzzing what the app keeps on disk and reads back as from outside
(tests/fuzz_util.py): a download's sidecar (core/downloads.read_sidecar - a
downloads folder on a NAS or a shared disk, another computer's copy of the
app, a hand that edited one), a series' episodes kept for offline
(known_episodes) and a recording's claim (core/rec_claims.parse - written by
the other recorder, or planted). Each says "not one" or gives values of
their type and in range - never an exception, a hang or a RecursionError."""
from __future__ import annotations

import json
import math
import os
import random

from dopeiptv.core import downloads as dl
from dopeiptv.core import rec_claims as rc
from dopeiptv.core.names import holds_login
from tests import fuzz_util as fz

SIDECAR = {"format": 1, "kind": "episode", "source": "a1b2c3d4", "stream_id": "501", "key": "501",
           "title": "Huset S01E02 - Piloten", "file": "Huset S01E02 - Piloten.mp4", "size": 2000,
           "added": 1_780_000_000, "year": "2021", "plot": "Ett hus.", "length": 2700,
           "category_id": "7", "rating": 7.4, "series_id": "77", "series_title": "Huset",
           "season": 1, "episode": 2, "episode_title": "Piloten", "source_name": "Pirat",
           "poster": "Huset S01E02 - Piloten.mp4.poster.jpg", "series_poster": "series.poster.jpg",
           "category_name": "Drama", "series_category_name": "Drama"}
FILM = {"format": 1, "kind": "movie", "source": "a1b2c3d4", "stream_id": "9001", "title": "Hemma",
        "file": "Hemma (2021).mp4", "size": 2000, "added": 5, "length": "5400"}
EPISODES = {"format": 1, "source": "a1b2c3d4", "series_id": "77",
            "episodes": [{"id": "501", "season": 1, "episode": 2, "title": "Piloten"},
                         {"id": 502, "season": "1", "episode": "3", "title": "Två"}]}
CLAIM = {"v": 1, "job": "j1", "who": "window", "state": "recording", "pid": 4242,
         "host": "kontoret", "started": "boot+1234",
         "info": {"title": "News", "channel": "One", "start": 1.7e9, "stop": 1.7e9 + 3600,
                  "source": "p1", "stream_key": "1", "path": "/rec/News.ts", "own": True,
                  "private": False, "since": 1.7e9}}


def _make(base: dict):
    def make(rng: random.Random) -> bytes:
        if rng.random() < 0.85:
            return fz.json_body(rng, base)
        return fz.mutate_bytes(rng, json.dumps(base).encode())
    return make


def _finite(v: object, most: float) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) \
        and 0 <= v <= most


def test_fuzz_download_sidecars(tmp_path):
    video = str(tmp_path / "Huset S01E02 - Piloten.mp4")
    with open(video, "wb") as f:
        f.write(b"x" * 2000)
    path = video + dl.SIDECAR

    def target(data: bytes) -> object:
        with open(path, "wb") as f:
            f.write(data)
        info = dl.read_sidecar(path)
        if info is None:
            return None
        assert isinstance(info, dict) and info.get("kind") in dl.KINDS
        for k, most in (("added", 1e12), ("size", 1e15), ("format", 1e6)):
            assert k not in info or _finite(info[k], most), (k, info[k])
        for k in ("length", "rating"):
            assert k not in info or (isinstance(info[k], (int, float)) and math.isfinite(info[k])), k
        assert "source_name" not in info or not holds_login(info["source_name"])
        item = dl.Item(video, 2000, info)
        assert isinstance(item.added, float) and 0 <= item.added <= 1e12
        assert isinstance(item.title, str) and isinstance(item.key, str)
        for pic in (item.poster, item.series_poster):
            assert pic == "" or os.path.dirname(pic) == os.path.dirname(video), pic
        dl.as_row(item)
        dl.series_row(item)
        return info
    fz.fuzz("download-sidecar", target, lambda rng: _make(rng.choice((SIDECAR, FILM)))(rng),
            seeds=[json.dumps(SIDECAR).encode(), json.dumps(FILM).encode()])


def test_fuzz_a_series_episodes_kept_for_offline(tmp_path):
    folder = str(tmp_path / "episodes")
    os.makedirs(folder)
    path = dl.episodes_file(folder, "a1b2c3d4", "77")

    def read(data: bytes) -> object:
        with open(path, "wb") as f:
            f.write(data)
        got = dl.known_episodes(folder, "a1b2c3d4", "77")
        assert isinstance(got, list) and len(got) <= dl.EPISODES_MAX
        for row in got:
            assert isinstance(row.get("id"), str) and 0 < len(row["id"]) <= 200
            for k in ("season", "episode"):
                assert k not in row or (isinstance(row[k], int) and 0 <= row[k] < 100000), row
            assert "title" not in row or isinstance(row["title"], str)
        return got
    fz.fuzz("known-episodes", read, _make(EPISODES), seeds=[json.dumps(EPISODES).encode()])

    def kept(rows: object) -> object:
        if not isinstance(rows, list):
            rows = [rows]
        dl.keep_episodes(folder, "a1b2c3d4", "88", rows)
        got = dl.known_episodes(folder, "a1b2c3d4", "88")
        assert isinstance(got, list)
        return got
    fz.fuzz("keep-episodes", kept, lambda rng: fz.json_value(rng, EPISODES["episodes"]),
            seeds=[EPISODES["episodes"]])


def test_fuzz_recording_claims():
    def target(data: bytes) -> object:
        c = rc.parse("k" * 32, data, 12.5)
        assert isinstance(c, rc.Claim) and c.key == "k" * 32 and c.beat == 12.5
        assert isinstance(c.job, str) and len(c.job) <= 64
        assert c.who in ("", *rc.WHO) and c.state in ("", *rc.LIVE, *rc.FINAL)
        assert isinstance(c.pid, int) and 0 <= c.pid <= 2 ** 31
        assert isinstance(c.host, str) and len(c.host) <= 64
        assert isinstance(c.started, str) and len(c.started) <= 64
        for k, v in c.info.items():
            assert k in rc.INFO_FIELDS, k
            if k in ("start", "stop", "since"):
                assert isinstance(v, float) and 0 <= v < 1e11
            elif k in ("own", "private"):
                assert isinstance(v, bool)
            else:
                assert isinstance(v, str) and len(v) <= 2048 and all(ch >= " " or ch == "\t" for ch in v)
        return c
    fz.fuzz("recording-claim", target, _make(CLAIM), seeds=[json.dumps(CLAIM).encode(), b""])
