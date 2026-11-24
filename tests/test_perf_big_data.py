"""Big data: a big provider's guide and lists, through the code that reads
them - how long each takes and how much memory.

A generated XMLTV of 2 000 channels (a programme an hour, a description
each) through providers/epg.py: parsed, its index written and read back,
loaded as the app loads it from disk. 50 000 channels, 50 000 films and
10 000 series through the search across sources (core/search_all.py) with
that guide's programmes, and through the command palette's index
(core/palette_match.py). And 50 000 rows in the window's channel list -
shown, filtered, sorted (ui/channel_list.py's model through the window's
own filter and sort). The budgets are about three times what each took
when they were set (docs/PERFORMANCE.md); CI reads a day of the guide,
``DOPEIPTV_PERF_LONG=1`` the whole week (336 000 programmes, 61 MB).
"""
from __future__ import annotations

import gc
import os
import random
import time
import tracemalloc
from datetime import datetime, timezone

import pytest

from tests import perf

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

CHANNELS = 2000
DAYS = 7 if perf.LONG else 1
ROWS = 50_000

#: Seconds, for a day of the guide (48 000 programmes, 8.4 MB); the week's
#: (336 000, 58 MB) are seven times these, its numbers in
#: docs/PERFORMANCE.md. Measured, the most of two runs: the parse 0.48 (the
#: week 3.5 - 10.8 before its times were read without strptime), the index
#: written 0.045 and read 0.016, the guide loaded from disk by its index
#: 0.016.
PARSE_S = 1.5 * DAYS
INDEX_WRITE_S = 0.15 * DAYS
INDEX_READ_S = 0.05 * DAYS
LOAD_S = 0.05 * DAYS
#: Python's peak while 200 channels' day is parsed (4 800 programmes), and
#: what the whole guide takes in memory once loaded, in MB. Measured 2.8,
#: and 2.9 for the day (12 for the week).
PARSE_PEAK_MB = 8.5
GUIDE_MB = 36.0 if perf.LONG else 9.0
#: The search across sources, the palette's index: seconds. Measured: the
#: catalogue folded 0.2, a day's programmes as a GuideTable 0.13 (the
#: week's 250 000 airings 1.1), a search at most 0.033, the palette's
#: index of 100 000 names 0.17 and a query 0.018.
CATALOGUE_S = 0.6
GUIDE_TABLE_S = 0.4 * DAYS
SEARCH_S = 0.1
PALETTE_INDEX_S = 0.5
PALETTE_QUERY_S = 0.06
#: The window's list of 50 000 rows: seconds. Measured: shown 0.062,
#: filtered 0.014, sorted A-Z 0.063 and Z-A 0.066.
LIST_SHOW_S = 0.2
LIST_FILTER_S = 0.05
LIST_SORT_S = 0.2


def _xmltv(channels: int, days: int) -> bytes:
    words = ["News", "Weather", "Film", "Sport", "Kids", "Documentary", "Talk", "Music",
             "Quiz", "Drama"]
    rnd = random.Random(1)
    now = int(time.time())
    first = now - now % 3600 - 3600
    fmt = "%Y%m%d%H%M%S +0000"
    out = ['<?xml version="1.0" encoding="UTF-8"?>\n<tv>\n']
    out += [f'<channel id="ch{c}.tv"><display-name>Channel {c}</display-name></channel>\n'
            for c in range(channels)]
    stamps = [datetime.fromtimestamp(first + h * 3600, timezone.utc).strftime(fmt)
              for h in range(days * 24 + 1)]
    for c in range(channels):
        for h in range(days * 24):
            word = words[rnd.randrange(len(words))]
            n = rnd.randrange(400)
            out.append(f'<programme start="{stamps[h]}" stop="{stamps[h + 1]}" channel="ch{c}.tv">'
                       f'<title>{word} {n}</title><desc>The {word.lower()} programme number '
                       f'{n}, with guests.</desc></programme>\n')
    out.append("</tv>\n")
    return "".join(out).encode()


def _best(fn, tries: int = 2):
    """(the quicker of *tries* runs in seconds, what the last gave)."""
    best, got = float("inf"), None
    for _ in range(tries):
        gc.collect()
        t0 = time.perf_counter()
        got = fn()
        best = min(best, time.perf_counter() - t0)
    return best, got


@pytest.fixture(scope="module")
def guide(tmp_path_factory):
    from dopeiptv.providers import epg
    data = _xmltv(CHANNELS, DAYS)
    took = {}
    took["parse"], (by_id, by_name) = _best(lambda: epg.parse_xmltv(data, 7), 1)
    took["index"], blob = _best(lambda: epg.index_blob(by_id, by_name))
    took["read"], loaded = _best(lambda: epg.read_index_blob(blob))
    assert loaded is not None
    folder = tmp_path_factory.mktemp("big-guide")
    cache = folder / "epg_big.xml"
    cache.write_bytes(data)
    (folder / ("epg_big.xml" + epg._INDEX_SUFFIX)).write_bytes(blob)

    class Client:
        server, username, password, epg_url = "http://192.0.2.1", "", "", ""

    def load():
        g = epg.XmltvGuide(Client(), cache_path=str(cache))
        assert g.load_cached()
        return g
    took["load"], g = _best(load)
    tracemalloc.start()
    loaded = epg.read_index_blob(blob)
    took["guide_mb"] = tracemalloc.get_traced_memory()[0] / 2 ** 20
    tracemalloc.stop()
    del loaded
    small = _xmltv(200, 1)
    tracemalloc.start()
    epg.parse_xmltv(small, 7)
    took["parse_peak_mb"] = tracemalloc.get_traced_memory()[1] / 2 ** 20
    tracemalloc.stop()
    programmes = sum(len(s[0]) for s in by_id.values())
    return {"took": took, "guide": g, "programmes": programmes, "mb": len(data) / 2 ** 20}


def test_the_guide_is_all_there(guide):
    assert guide["programmes"] == CHANNELS * DAYS * 24


@pytest.mark.parametrize("part,budget", [("parse", PARSE_S), ("index", INDEX_WRITE_S),
                                         ("read", INDEX_READ_S), ("load", LOAD_S),
                                         ("parse_peak_mb", PARSE_PEAK_MB),
                                         ("guide_mb", GUIDE_MB)])
def test_the_guide_within_its_budget(guide, part, budget):
    assert guide["took"][part] <= budget, (part, round(guide["took"][part], 3),
                                          round(guide["mb"], 1))


@pytest.fixture(scope="module")
def catalogue():
    from dopeiptv.core import search_all
    src = search_all.Source("a", "A")
    chans = [{"stream_id": i, "name": f"Channel {i}", "category_id": str(i % 40),
              "epg_channel_id": f"ch{i}.tv"} for i in range(ROWS)]
    films = [{"stream_id": 100000 + i, "name": f"Film number {i} ({1950 + i % 70})",
              "category_id": "f"} for i in range(ROWS)]
    series = [{"series_id": 200000 + i, "name": f"Series {i}", "category_id": "s"}
              for i in range(ROWS // 5)]
    took, cat = _best(lambda: search_all.Catalogue(src, chans, films, series))
    return {"src": src, "chans": chans, "films": films, "cat": cat, "took": took}


def test_the_search_across_sources_within_its_budget(guide, catalogue):
    from dopeiptv.core import search_all
    assert catalogue["took"] <= CATALOGUE_S, catalogue["took"]
    now = time.time()
    chans = catalogue["chans"][:CHANNELS]
    took, table = _best(lambda: search_all.GuideTable(catalogue["src"], chans,
                                                      guide["guide"].schedule, now))
    assert len(table) > 0 and took <= GUIDE_TABLE_S, (took, len(table))
    for query in ("news", "film number 12", "channel 1999"):
        took, found = _best(lambda q=query: search_all.search(q, [catalogue["cat"]], [table],
                                                              now=now))
        assert found is not None and found.count() > 0
        assert took <= SEARCH_S, (query, took)


def test_the_palette_s_index_within_its_budget(catalogue):
    from dopeiptv.core import palette_match
    names = [c["name"] for c in catalogue["chans"]] + [f["name"] for f in catalogue["films"]]
    took, index = _best(lambda: palette_match.Index(names))
    assert len(index) == 2 * ROWS and took <= PALETTE_INDEX_S, took
    for query in ("film 12", "chan 4999", "zz"):
        took, _hits = _best(lambda q=query: index.scored(q, 50))
        assert took <= PALETTE_QUERY_S, (query, took)


_LIST_CHILD = r"""
import gc
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw
ROWS = %(rows)d
s = settings()
w = mw.MainWindow(DemoClient(), s, PlaylistStore(s))
w.show()
pump()
wait(1.0)
rows = [{"stream_id": i, "num": i + 1, "name": "Channel %%d" %% i, "stream_icon": "",
         "category_id": "demo", "epg_channel_id": ""} for i in range(ROWS)]


def show(items, text=""):
    w.all_items = items
    w.search.blockSignals(True)
    w.search.setText(text)
    w.search.blockSignals(False)
    w._apply_filter()
    pump(3)
    return w.list_model.rowCount()


def best(fn):
    out = None
    for _ in range(2):
        gc.collect()
        t0 = time.perf_counter()
        n = fn()
        took = time.perf_counter() - t0
        out = took if out is None else min(out, took)
    return out, n


got = {}
got["show"] = best(lambda: show(rows))
got["filter"] = best(lambda: show(rows, "channel 12"))
for order in ("alpha_asc", "alpha_desc"):
    s.setValue("sort_order", order)
    got[order] = best(lambda: show(rows))
done(got)
"""


@pytest.fixture(scope="module")
def big_list():
    pytest.importorskip("PyQt6.QtWidgets")
    return perf.run_child(_LIST_CHILD % {"rows": ROWS})


def test_a_list_of_50_000_within_its_budget(big_list):
    took, n = big_list["show"]
    assert n == ROWS and took <= LIST_SHOW_S, big_list
    took, n = big_list["filter"]
    # Channel 12, 120 to 129, 1200 to 1299 and 12000 to 12999.
    assert n == 1111 and took <= LIST_FILTER_S, big_list
    for order in ("alpha_asc", "alpha_desc"):
        took, n = big_list[order]
        assert n == ROWS and took <= LIST_SORT_S, (order, big_list)
