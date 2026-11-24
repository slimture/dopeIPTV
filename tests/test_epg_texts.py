"""A guide's programmes with their descriptions on disk a channel at a time
(providers/epg_texts.py) and Every source's detail column reading them
(services/search_sources.py ``Sources.texts``): the guide read whole once
per copy on disk - not for every row picked -, a row's own block alone read
after, the same programmes and words the whole guide gives; written again
when the copy changes or a moment past what they cover is asked; a file
made from another copy, by another version or damaged is no texts; never a
download; let go with the guide's cache."""
from __future__ import annotations

import os
import random
import struct
import threading
import time
from array import array

import pytest

from dopeiptv.core import every_source as es
from dopeiptv.providers import epg
from dopeiptv.providers import epg_texts as et
from dopeiptv.services import search_sources as ss

H = 3600
FMT = "%Y%m%d%H%M%S +0000"


def _stamp(t: float) -> str:
    return time.strftime(FMT, time.gmtime(t))


def _xml(channels: int, per: int, word: str = "About", now: float | None = None) -> bytes:
    """A guide of *channels* x *per* programmes of an hour, each with a
    description of its own, the second on now."""
    now = time.time() if now is None else now
    out = ["<tv>"]
    out += [f'<channel id="c{c}.x"><display-name>Chan {c}</display-name></channel>'
            for c in range(channels)]
    for c in range(channels):
        t = int(now) - 2 * H + 600 + (c % 7) * 300
        for i in range(per):
            out.append(f'<programme channel="c{c}.x" start="{_stamp(t)}" stop="{_stamp(t + H)}">'
                       f"<title>Show {c}/{i}</title><desc>{word} {c}/{i}: tonight.</desc></programme>")
            t += H
    out.append("</tv>")
    return "".join(out).encode()


@pytest.fixture
def no_download(monkeypatch):
    monkeypatch.setattr(epg.XmltvGuide, "PARSE_ELSEWHERE", False)
    monkeypatch.setattr(epg.XmltvGuide, "_download",
                        lambda self: (_ for _ in ()).throw(AssertionError("a download")))


class _Opened:
    """The guides read whole, counted."""

    def __init__(self) -> None:
        self.paths: list[str] = []

    def __call__(self, path: str) -> epg.XmltvGuide:
        self.paths.append(path)
        return epg.XmltvGuide(None, None, cache_path=path)    # type: ignore[arg-type]


def _store(path, opened: _Opened | None = None) -> ss.Sources:
    return ss.Sources(make_client=lambda pl: None, guide_path=lambda pid: str(path),
                      open_guide=opened or _Opened())


def _row(c: int) -> dict:
    return {"stream_id": c, "name": f"Chan {c}", "epg_channel_id": f"c{c}.x"}


def _written(store: ss.Sources, pid: str = "b", timeout: float = 30.0) -> et.GuideTexts:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        got = (store._tx.get(pid) or (None, None))[1]
        if isinstance(got, et.GuideTexts):
            return got
        time.sleep(0.01)
    raise AssertionError("the texts were never written down")


def _whole(path, c: int, at: float) -> list[dict]:
    g = epg.XmltvGuide(None, None, cache_path=str(path))    # type: ignore[arg-type]
    assert g.load_cached()
    return es.listings_of(g.schedule(_row(c)), at)


def _texts(store: ss.Sources, c: int, now: float, delay: int = 0) -> list[dict]:
    return store.texts("b", _row(c), es.play_key("b", c), delay, now)


def test_the_guide_is_read_whole_once_and_each_row_after_from_its_own_block(tmp_path, no_download):
    path = tmp_path / "epg_b.xml"
    path.write_bytes(_xml(300, 30))
    now = time.time()
    opened = _Opened()
    store = _store(path, opened)
    first = _texts(store, 7, now)
    assert first and first[0]["description"] == "About 7/1: tonight." and first == _whole(path, 7, now)
    assert len(first) == es.LISTINGS
    texts = _written(store)
    assert os.path.exists(et.path_for(str(path))) and len(texts) == 300
    for c in (0, 1, 150, 299):
        assert _texts(store, c, now) == _whole(path, c, now), c
    # By its name alone (no guide id), and a row the guide has nothing of.
    named = store.texts("b", {"stream_id": 5, "name": "CHAN 5"}, es.play_key("b", 5), 0, now)
    assert named == _whole(path, 5, now)
    assert store.texts("b", {"stream_id": 9, "name": "Nowhere"}, "k9", 0, now) == []
    assert opened.paths == [str(path)], "the whole guide read for more than the first row"
    # Its guide an hour behind (a shift of its own): what is on in its time.
    late = _texts(store, 3, now, delay=60)
    assert late == _whole(path, 3, now - H) and late[0]["title"] != _texts(store, 3, now)[0]["title"]
    assert opened.paths == [str(path)]
    # The next session takes them from disk: the guide not read at all.
    again = _Opened()
    assert _texts(_store(path, again), 42, now + 60) == _whole(path, 42, now + 60)
    assert again.paths == []


def test_moving_from_row_to_row_is_quick_after_the_first(tmp_path, no_download):
    path = tmp_path / "epg_b.xml"
    path.write_bytes(_xml(2000, 24))
    now = time.time()
    store = _store(path)
    t = time.perf_counter()
    _texts(store, 0, now)
    first = time.perf_counter() - t
    _written(store)
    took = []
    for c in range(1, 2000, 97):
        t = time.perf_counter()
        assert _texts(store, c, now)[0]["description"] == f"About {c}/1: tonight."
        took.append(time.perf_counter() - t)
    # A block read and unpacked, not the guide: far below the first row's
    # whole read, and well inside any machine's time.
    assert max(took) < 0.25 and sorted(took)[len(took) // 2] < max(first / 5, 0.02), (first, took)


def test_a_changed_guide_has_its_texts_written_again(tmp_path, no_download):
    path = tmp_path / "epg_b.xml"
    path.write_bytes(_xml(40, 10))
    now = time.time()
    opened = _Opened()
    store = _store(path, opened)
    assert _texts(store, 4, now)[0]["description"] == "About 4/1: tonight."
    old = _written(store)
    path.write_bytes(_xml(40, 10, word="Changed"))
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    got = _texts(store, 4, now)
    assert got[0]["description"] == "Changed 4/1: tonight." and got == _whole(path, 4, now)
    new = _written(store)
    assert new is not old and len(opened.paths) == 2
    assert _texts(store, 30, now)[0]["description"] == "Changed 30/1: tonight."
    # The file says which copy it is of: the old one's texts are none now.
    assert et.read(et.path_for(str(path)), (st.st_size, st.st_mtime_ns)) is None
    # A moment past what they cover (a day on): written again, still right.
    later = now + ss.TEXTS_AHEAD + H
    assert _texts(store, 6, later) == _whole(path, 6, later)
    assert _written(store) is not new and len(opened.paths) == 3


def test_rows_picked_while_they_are_written_are_read_from_the_guide_in_memory(tmp_path, no_download,
                                                                              monkeypatch):
    path = tmp_path / "epg_b.xml"
    path.write_bytes(_xml(30, 8))
    now = time.time()
    gate = threading.Event()
    real = et.write

    def slow(*a, **k):
        gate.wait(10)
        return real(*a, **k)
    monkeypatch.setattr(et, "write", slow)
    opened = _Opened()
    store = _store(path, opened)
    assert _texts(store, 1, now) == _whole(path, 1, now)
    assert isinstance(store._tx["b"][1], ss._Writing)
    assert _texts(store, 2, now) == _whole(path, 2, now)       # from the guide held
    assert opened.paths == [str(path)]
    gate.set()
    _written(store)
    assert _texts(store, 3, now) == _whole(path, 3, now) and opened.paths == [str(path)]


def test_where_they_cannot_be_written_each_row_reads_the_guide_as_before(tmp_path, no_download,
                                                                       monkeypatch):
    path = tmp_path / "epg_b.xml"
    path.write_bytes(_xml(30, 8))
    now = time.time()
    monkeypatch.setattr(et, "CHANNELS_MAX", 10)               # more channels than may be
    opened = _Opened()
    store = _store(path, opened)
    assert _texts(store, 1, now) == _whole(path, 1, now)
    end = time.monotonic() + 10
    while store._tx["b"][1] is not None and time.monotonic() < end:
        time.sleep(0.01)
    assert store._tx["b"][1] is None and not os.path.exists(et.path_for(str(path)))
    assert _texts(store, 2, now) == _whole(path, 2, now) and len(opened.paths) == 2
    # No guide on this computer: nothing, and nothing written.
    gone = _store(tmp_path / "none.xml")
    assert gone.texts("b", _row(1), "k", 0, now) == []


def test_a_damaged_file_is_written_anew(tmp_path, no_download):
    path = tmp_path / "epg_b.xml"
    path.write_bytes(_xml(20, 8))
    now = time.time()
    store = _store(path)
    _texts(store, 1, now)
    texts = _written(store)
    # A block's bytes changed under it: that row is read from the guide,
    # and the texts written again.
    number = texts._ids["c5.x"]
    with open(texts.path, "r+b") as f:
        f.seek(texts._offsets[number] + 4)
        f.write(b"\x00\x01\x02\x03")
    os.utime(texts.path, ns=(texts._identity[3], texts._identity[3]))   # the same identity
    store._texts.clear()
    assert _texts(store, 5, now) == _whole(path, 5, now)
    assert _written(store) is not texts


# -- the file itself ----------------------------------------------------------------------

class _Tables:
    def __init__(self, by_id, by_name=None):
        self._t = (by_id, by_name or {})

    def tables(self):
        return self._t


def _sched(rows):
    rows = sorted(rows)
    return (array("q", [r[0] for r in rows]), array("q", [r[1] for r in rows]),
            tuple(f"T{r[0]}" for r in rows), tuple(f"D{r[0]}" for r in rows))


def test_what_is_kept_shows_what_the_whole_guide_shows_for_every_moment_it_covers(tmp_path):
    """Odd guides too: programmes that end before they begin, last no time,
    run past a day, start at once; a channel with nothing left."""
    rng = random.Random(4)
    base = 1_800_000_000
    by_id = {}
    for c in range(60):
        rows = []
        t = base - rng.randint(0, 3 * 86400)
        for _ in range(rng.randint(0, 120)):
            length = rng.choice((1800, 3600, 0, -600, 90_000, 200))
            rows.append((t, t + length))
            t += rng.choice((0, 600, 1800, 3600, 7200))
        by_id[f"c{c}"] = _sched(rows)
    since, until = base - 3600, base + 86400
    texts = et.write(_Tables(by_id), str(tmp_path / "t.texts"), (1, 2), since, until,
                     es.LISTINGS, es.TEXT_MAX)
    assert texts is not None
    for c, sched in by_id.items():
        kept = texts.schedule({"epg_channel_id": c})
        for at in [since, until - 1] + [rng.randint(since, until - 1) for _ in range(25)]:
            assert es.listings_of(kept, at) == es.listings_of(sched, at), (c, at)


def test_titles_and_words_are_cut_and_read_back_as_written(tmp_path):
    long = "ö" * (es.TEXT_MAX + 50)
    by_id = {"a": (array("q", [100]), array("q", [200]), ("Nul\x00here", ), (long, )),
             "b": (array("q", [100]), array("q", [200]), ("\ud800 lone",), ("",))}
    texts = et.write(_Tables(by_id, {"a": "a", "bee": "b", "gone": "zz"}), str(tmp_path / "t"),
                     (1, 2), 0, 1000, 24, es.TEXT_MAX)
    starts, stops, titles, descs = texts.schedule({"epg_channel_id": "A "})
    assert (list(starts), list(stops), titles) == ([100], [200], ("Nul�here",))
    assert descs == ("ö" * es.TEXT_MAX,)
    assert texts.schedule({"name": "Bee"})[2] == ("\ud800 lone",)
    assert texts.schedule({"name": "gone"}) is None                 # a name of no channel's
    read = et.read(str(tmp_path / "t"), (1, 2))
    assert read is not None and read.schedule({"epg_channel_id": "b"})[2] == ("\ud800 lone",)
    assert read.since == 0 and read.until == 1000 and read.covers(0) and not read.covers(1000)


def test_a_file_of_another_copy_version_or_shape_is_none(tmp_path):
    by_id = {"a": (array("q", [100]), array("q", [200]), ("T",), ("D",))}
    path = str(tmp_path / "t")
    et.write(_Tables(by_id), path, (10, 20), 0, 1000, 24, 100)
    data = open(path, "rb").read()
    assert et.read(path, (10, 20)) is not None
    assert et.read(path, (10, 21)) is None and et.read(path, (11, 20)) is None
    assert et.read(str(tmp_path / "nothing"), (10, 20)) is None
    for bad in (data[:-1], data + b"x", b"", data[:10],
                data[:8] + struct.pack("<H", et.VERSION + 1) + data[10:],
                b"x" * 8 + data[8:]):
        with open(path, "wb") as f:
            f.write(bad)
        assert et.read(path, (10, 20)) is None
    # Made again since its table was read: Stale, not another's programmes.
    with open(path, "wb") as f:
        f.write(data)
    texts = et.read(path, (10, 20))
    et.write(_Tables(by_id), path, (10, 20), 0, 1000, 24, 100)
    with pytest.raises(et.Stale):
        texts.schedule({"epg_channel_id": "a"})


def test_the_texts_go_with_the_guides_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(epg, "_epg_cache_dir", lambda: tmp_path)
    for pid in ("kept", "gone"):
        base = epg.epg_cache_path(pid)
        for suffix in ("", ".idx", ".texts", ".texts.part"):
            open(base + suffix, "wb").close()
    epg.prune_epg_caches(["kept"])
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == ["epg_kept.xml", "epg_kept.xml.idx", "epg_kept.xml.texts",
                    "epg_kept.xml.texts.part"], left
    g = epg.XmltvGuide(None, None, cache_path=epg.epg_cache_path("kept"))   # type: ignore[arg-type]
    g.clear_cache()
    assert not os.path.exists(epg.epg_cache_path("kept") + ".texts")
    from dopeiptv.ui.mw_onboarding import drop_source_files
    for suffix in ("", ".texts"):
        open(epg.epg_cache_path("tried") + suffix, "wb").close()
    drop_source_files("tried")
    assert not any(p.name.startswith("epg_tried") for p in tmp_path.iterdir())
