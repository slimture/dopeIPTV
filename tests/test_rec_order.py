"""Recordings in the order picked (core/rec_view.arrange): the newest or
the oldest first under their days, by name, under their channels, the
longest or the largest first; Unwatched only."""
from __future__ import annotations

from datetime import datetime

from dopeiptv.core import mitt, rec_view

NOW = datetime(2026, 11, 18, 20, 0)          # a Wednesday


def _ts(y, m, d, h=12):
    return str(int(datetime(y, m, d, h).timestamp()))


FILES = [
    {"name": "Kvällsnytt", "_path": "/r/a.ts", "added": _ts(2026, 11, 18, 19), "_channel": "SVT1", "_size": 300},
    {"name": "Sporten", "_path": "/r/b.ts", "added": _ts(2026, 11, 17), "_channel": "TV4", "_size": 900},
    {"name": "Aktuellt", "_path": "/r/c.ts", "added": _ts(2026, 11, 16), "_channel": "SVT2", "_size": 100},
    {"name": "Babel", "_path": "/r/d.ts", "added": _ts(2026, 11, 12), "_channel": "SVT2", "_size": 500},
    {"name": "Dokument", "_path": "/r/e.ts", "added": _ts(2026, 10, 2), "_channel": "", "_size": 700},
    {"name": "okänd", "_path": "/r/f.ts", "added": "0", "_channel": "", "_size": 50},
]


def _names(groups):
    return [[f["name"] for f in rows] for _k, rows in groups]


def test_newest_first_under_their_days_the_undated_last():
    g = rec_view.arrange(FILES, rec_view.NEWEST, NOW, week_start=0)
    assert [k for k, _r in g] == [(mitt.TODAY,), (mitt.YESTERDAY,), (mitt.WEEK,),
                                  (mitt.LAST_WEEK,), (mitt.MONTH, 2026, 10), (mitt.EARLIER,)]
    assert _names(g) == [["Kvällsnytt"], ["Sporten"], ["Aktuellt"], ["Babel"], ["Dokument"], ["okänd"]]


def test_oldest_first_keeps_the_days_the_other_way():
    g = rec_view.arrange(FILES, rec_view.OLDEST, NOW)
    assert [k[0] for k, _r in g][:2] == [mitt.MONTH, mitt.LAST_WEEK]
    assert g[-1][0] == (mitt.EARLIER,)


def test_by_channel_a_to_z_the_newest_first_none_said_last():
    g = rec_view.arrange(FILES, rec_view.CHANNEL, NOW)
    assert [k for k, _r in g] == [("channel", "SVT1"), ("channel", "SVT2"),
                                  ("channel", "TV4"), ("channel", "")]
    assert _names(g)[1] == ["Aktuellt", "Babel"]


def test_name_longest_and_largest_are_one_group():
    by_name = rec_view.arrange(FILES, rec_view.NAME, NOW)
    assert by_name[0][0] is None and _names(by_name)[0][:3] == ["Aktuellt", "Babel", "Dokument"]
    lengths = {"/r/b.ts": 3600.0, "/r/d.ts": 5400.0}
    longest = rec_view.arrange(FILES, rec_view.LONGEST, NOW, length=lambda p: lengths.get(p, 0.0))
    assert _names(longest)[0][:2] == ["Babel", "Sporten"]       # unknown lengths after them
    assert _names(longest)[0][2] == "Kvällsnytt"                 # then the newest first
    largest = rec_view.arrange(FILES, rec_view.LARGEST, NOW)
    assert _names(largest)[0][:2] == ["Sporten", "Dokument"]


def test_unwatched_only_leaves_out_what_was_watched():
    seen = {"/r/a.ts", "/r/c.ts"}
    g = rec_view.arrange(FILES, rec_view.NEWEST, NOW, watched=seen.__contains__, only_unwatched=True)
    names = [n for rows in _names(g) for n in rows]
    assert "Kvällsnytt" not in names and "Aktuellt" not in names and len(names) == 4
    assert (mitt.TODAY,) not in [k for k, _r in g]               # an empty day: no heading


def test_the_setting_read_safely():
    class S(dict):
        def value(self, k, d=None):
            return self.get(k, d)

        def setValue(self, k, v):  # noqa: N802 - Qt's name
            self[k] = v
    assert rec_view.order_of(S()) == rec_view.NEWEST
    assert rec_view.order_of(S(rec_sort="bogus")) == rec_view.NEWEST
    assert rec_view.order_of(S(rec_sort="channel")) == rec_view.CHANNEL
    assert rec_view.unwatched_only(S(rec_unwatched_only="true"))
    assert not rec_view.unwatched_only(S())
