"""Your viewing statistics: the time counted per title and hour, on this
computer only.

The log (core/viewing_stats.py): time cut at the clock's hours, kept and
read back, nothing while private viewing is on, cleared with Clear recent
history (for its range and kinds), old days let go, seeded once from the
resume positions; the sums - this week, month and year, by weekday (the
locale's first day first) and hour, the top channels, movies and series
(one name across sources), the longest streak and the one going now. The
window's hooks count only what plays and not while paused, and an episode
as its series. The statistics window: its empty state, its figures, the
period switch scoping charts and lists alike. Never synced.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta

import pytest

from dopeiptv.core import privacy
from dopeiptv.core import viewing_stats as vs


class _Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's name
        self[key] = value

    def allKeys(self):  # noqa: N802 - Qt's name
        return list(self)


def _at(y, mo, d, h=20, mi=0):
    return datetime(y, mo, d, h, mi).timestamp()


@pytest.fixture(autouse=True)
def _not_private():
    privacy.set_private(False)
    yield
    privacy.set_private(False)


def test_time_is_cut_at_the_clocks_hours():
    got = vs.split_hours(_at(2026, 11, 15, 20, 50), _at(2026, 11, 15, 22, 10))
    assert got == [("2026111520", 600.0), ("2026111521", 3600.0), ("2026111522", 600.0)]
    assert vs.split_hours(_at(2026, 11, 15, 20, 10), _at(2026, 11, 15, 20, 40)) == [
        ("2026111520", 1800.0)]
    assert vs.split_hours(5.0, 5.0) == []


def test_the_log_keeps_what_was_watched_and_reads_it_back():
    s = _Settings()
    log = vs.ViewingLog(s)
    assert log.fresh and log.empty()
    end = _at(2026, 11, 15, 21, 30)
    log.add("live:p:1", "SVT1 HD", vs.CHANNEL, 3600, end)
    log.add("live:p:1", "SVT1 HD", vs.CHANNEL, 60, end + 60)
    log.save()
    raw = json.loads(s[vs.KEY])
    assert raw["t"]["live:p:1"]["h"] == {"2026111520": 1800.0, "2026111521": 1860.0}
    back = vs.ViewingLog(s)
    assert not back.fresh and not back.empty()
    rows = sorted(back.rows())
    assert [(r.day, r.hour, r.secs) for r in rows] == [
        (date(2026, 11, 15), 20, 1800.0), (date(2026, 11, 15), 21, 1860.0)]
    assert back.since == pytest.approx(end - 3600, abs=1)


def test_private_viewing_adds_nothing():
    log = vs.ViewingLog(_Settings())
    privacy.set_private(True)
    log.add("movie:p:9", "Secret", vs.FILM, 5400, _at(2026, 11, 15))
    assert log.empty()
    privacy.set_private(False)
    log.add("movie:p:9", "Fine", vs.FILM, 5400, _at(2026, 11, 15))
    assert not log.empty()


def test_clearing_takes_the_range_and_kinds_asked_for():
    log = vs.ViewingLog(_Settings())
    log.add("live:p:1", "One", vs.CHANNEL, 3600, _at(2026, 11, 15, 21))
    log.add("movie:p:2", "Film", vs.FILM, 3600, _at(2026, 11, 15, 21))
    log.add("live:p:1", "One", vs.CHANNEL, 3600, _at(2026, 11, 10, 21))
    # The last hour, channels only: today's channel time from 20:00 on goes.
    assert log.clear(_at(2026, 11, 15, 20, 30), {vs.CHANNEL}) == 1
    left = {(r.kind, r.day.day, r.hour) for r in log.rows()}
    assert left == {(vs.FILM, 15, 20), (vs.CHANNEL, 10, 20)}
    assert log.clear(None) == 2 and log.empty() and log.since == 0.0


def test_old_days_go_and_titles_past_the_limit(monkeypatch):
    log = vs.ViewingLog(_Settings())
    now = time.time()
    log.add("live:p:old", "Old", vs.CHANNEL, 600, now - (vs.RETAIN_DAYS + 5) * 86400)
    log.add("live:p:new", "New", vs.CHANNEL, 600, now)
    log.prune(now)
    assert set(log.titles) == {"live:p:new"}
    monkeypatch.setattr(vs, "MAX_TITLES", 3)
    for i in range(5):
        log.add(f"movie:p:{i}", f"F{i}", vs.FILM, 600, now - (10 - i) * 3600)
    log.prune(now)
    assert set(log.titles) == {"live:p:new", "movie:p:4", "movie:p:3"}


def _rows(entries):
    log = vs.ViewingLog(_Settings())
    for tid, name, kind, secs, when in entries:
        log.add(tid, name, kind, secs, when)
    return list(log.rows())


def test_the_sums_of_the_week_month_and_year():
    # Sunday 15 November 2026.
    today = date(2026, 11, 15)
    rows = _rows([
        ("live:a:1", "SVT1 HD", vs.CHANNEL, 2 * 3600, _at(2026, 11, 15, 22)),   # Sunday
        ("live:b:7", "svt1  hd", vs.CHANNEL, 3600, _at(2026, 11, 9, 21)),       # Monday, another source
        ("movie:a:3", "Dune", vs.FILM, 9000, _at(2026, 11, 3, 23)),
        ("series:a:5", "Bron", vs.SERIES, 3000, _at(2026, 10, 20, 21)),
        ("live:a:2", "News 24", vs.CHANNEL, 1800, _at(2025, 12, 31, 12)),
        ("other::x", "home.mkv", vs.OTHER, 600, _at(2026, 11, 15, 10)),
    ])
    s = vs.summarize(rows, today, vs.WEEK, week_start=0)
    assert s.totals[vs.WEEK] == 3 * 3600 + 600              # Monday 9th through Sunday 15th
    assert s.totals[vs.MONTH] == 3 * 3600 + 600 + 9000
    assert s.totals[vs.YEAR] == 3 * 3600 + 600 + 9000 + 3000
    assert s.totals[vs.ALL] == s.totals[vs.YEAR] + 1800
    # A week from Sunday: the 9th was last week.
    sun = vs.summarize(rows, today, vs.WEEK, week_start=6)
    assert sun.totals[vs.WEEK] == 2 * 3600 + 600
    assert s.by_weekday[6] == 2 * 3600 + 600 and s.by_weekday[0] == 3600
    assert sum(s.by_hour) == s.totals[vs.WEEK]
    assert s.by_hour[20] == 2 * 3600 and s.by_hour[21] == 3600 and s.by_hour[9] == 600
    # One name across sources; files are not a channel, movie or series.
    assert s.top[vs.CHANNEL] == [("SVT1 HD", 3 * 3600)]
    assert s.top[vs.FILM] == [] and s.top[vs.SERIES] == []
    year = vs.summarize(rows, today, vs.YEAR)
    assert year.top[vs.FILM] == [("Dune", 9000)] and year.top[vs.SERIES] == [("Bron", 3000)]
    assert [n for n, _s in vs.summarize(rows, today, vs.ALL).top[vs.CHANNEL]] == ["SVT1 HD", "News 24"]
    assert s.first == date(2025, 12, 31)


def test_the_longest_streak_and_the_one_going_now():
    today = date(2026, 11, 15)
    days = [date(2026, 10, 1) + timedelta(days=i) for i in range(5)]          # 5 in a row
    days += [date(2026, 11, 12), date(2026, 11, 13), date(2026, 11, 14)]      # 3, to yesterday
    rows = _rows([("live:a:1", "One", vs.CHANNEL, 1200,
                   datetime(d.year, d.month, d.day, 21).timestamp()) for d in days])
    s = vs.summarize(rows, today)
    assert (s.longest, s.longest_end, s.current, s.days) == (5, date(2026, 10, 5), 3, 8)
    # A day with less than a minute does not keep a streak going.
    rows += _rows([("live:a:1", "One", vs.CHANNEL, 30, _at(2026, 11, 15, 21))])
    assert vs.summarize(rows, today).current == 3
    # Two days without: none going now.
    assert vs.summarize(rows, date(2026, 11, 17)).current == 0
    assert vs.summarize([], today).empty


def test_the_periods_start_where_the_calendar_says():
    sun = date(2026, 11, 15)
    assert vs.period_start(vs.WEEK, sun, 0) == date(2026, 11, 9)
    assert vs.period_start(vs.WEEK, sun, 6) == sun
    assert vs.period_start(vs.MONTH, sun) == date(2026, 11, 1)
    assert vs.period_start(vs.YEAR, sun) == date(2026, 1, 1)
    assert vs.period_start(vs.ALL, sun) is None


def test_a_new_log_starts_from_the_resume_positions_once():
    data = {"vod:12": {"pos": 3000, "dur": 7000, "ts": int(_at(2026, 11, 1, 22)),
                       "item": {"name": "Dune"}},
            "episode:77": {"pos": 1500, "dur": 2700, "ts": int(_at(2026, 11, 2, 21)),
                           "item": {"name": "S1 E2"}, "ctx": {"series_id": 5, "name": "Bron"}},
            "rec:/x.ts": {"pos": 100, "dur": 200, "ts": 1}, "junk": 5}
    seed = vs.resume_seed(data, "p")
    assert {(t[0], t[1], t[2], t[3]) for t in seed} == {
        ("movie:p:12", "Dune", vs.FILM, 3000.0), ("series:p:5", "Bron", vs.SERIES, 1500.0)}
    log = vs.ViewingLog(_Settings())
    assert log.seed(seed) == 2 and not log.fresh
    assert log.seed(seed) == 0                                      # once
    year = vs.summarize(list(log.rows()), date(2026, 11, 15), vs.YEAR)
    assert year.top[vs.FILM] == [("Dune", 3000.0)] and year.top[vs.SERIES] == [("Bron", 1500.0)]


def test_clear_recent_history_clears_it_too():
    s = _Settings()
    log = vs.ViewingLog(s)
    now = time.time()
    log.add("live:p:1", "One", vs.CHANNEL, 600, now)
    log.add("movie:p:2", "Film", vs.FILM, 600, now)
    log.add("movie:p:2", "Film", vs.FILM, 600, now - 3 * 86400)
    log.save()
    from dopeiptv.core.stores import HistoryStore
    privacy.clear_recent(s, s, HistoryStore(s, "history"), 3600, channels=False,
                         titles=True, positions=False, viewing=log)
    assert {r.kind for r in log.rows() if r.day == date.today()} <= {vs.CHANNEL}
    assert any(r.kind == vs.FILM for r in log.rows())                # three days ago stays
    # Without the window's log: read from the settings, cleared there.
    privacy.clear_recent(s, s, HistoryStore(s, "history"), 0)
    assert vs.ViewingLog(s).empty()


def test_it_stays_on_this_computer():
    from dopeiptv.core import backup, sync
    assert not sync.syncable(vs.KEY)
    assert not backup.left_out(vs.KEY)          # a backup is the user's own file


# -- the window's hooks ---------------------------------------------------------------

@pytest.fixture
def counter(monkeypatch):
    pytest.importorskip("PyQt6.QtWidgets")
    from types import SimpleNamespace

    from PyQt6.QtCore import QObject
    from PyQt6.QtWidgets import QApplication

    from dopeiptv.ui import mw_stats
    app = QApplication.instance() or QApplication([])   # kept while the test runs
    clock = [1000.0]
    monkeypatch.setattr(mw_stats.time, "monotonic", lambda: clock[0])

    class W(mw_stats._StatsMixin, QObject):
        def __init__(self):
            super().__init__()
            self.settings = _Settings()
            self.player = SimpleNamespace(current_url="http://x/1.ts")
            self.playlist_store = SimpleNamespace(active_id="p")
            self.series_ctx = None
            self._last_playback = None
    w = W()
    yield w, clock
    assert app is not None
    if w._stats_timer is not None:
        w._stats_timer.stop()


def test_what_plays_is_counted_and_a_pause_is_not(counter):
    w, clock = counter
    w._stats_begin(1, "SVT1 HD", {"stream_id": 1}, "live", False)
    assert w._stats_timer.isActive()
    clock[0] += 60
    w._stats_tick()
    w._stats_paused_changed(True)
    clock[0] += 600                                   # paused ten minutes
    w._stats_tick()
    w._stats_paused_changed(False)
    clock[0] += 30
    w._stats_flush()
    total = sum(r.secs for r in w._stats_store().rows())
    assert total == pytest.approx(90)
    clock[0] += 3600                                  # asleep: one tick's worth at most
    w._stats_tick()
    assert sum(r.secs for r in w._stats_store().rows()) == pytest.approx(90 + w.TICK_CAP_S)
    tid = next(iter(w._stats_store().titles))
    assert tid == "live:p:1" and w._stats_store().titles[tid]["n"] == "SVT1 HD"


def test_an_episode_counts_as_its_series_and_a_stop_ends_it(counter):
    w, clock = counter
    w._last_playback = {"series_ctx": {"series_id": 5, "name": "Bron"}}
    w._stats_begin(77, "S1 E2 - Pilot", {}, "episode", False)
    clock[0] += 60
    w._stats_end()
    assert w._stats_now is None and not w._stats_timer.isActive()
    store = w._stats_store()
    assert list(store.titles) == ["series:p:5"] and store.titles["series:p:5"]["n"] == "Bron"
    assert vs.KEY in w.settings                       # saved as it ended
    clock[0] += 60
    w._stats_tick()                                   # nothing plays: nothing more
    assert sum(r.secs for r in store.rows()) == pytest.approx(60)


def test_a_stream_gone_from_the_player_stops_the_count(counter):
    w, clock = counter
    w._stats_begin("m1", "Dune", {}, "movie", False)
    clock[0] += 60
    w.player.current_url = None                       # stopped elsewhere
    w._stats_tick()
    assert w._stats_now is None
    assert sum(r.secs for r in w._stats_store().rows()) == 0


# -- the window ---------------------------------------------------------------------

@pytest.fixture
def qapp():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_the_window_with_nothing_watched_says_so(qapp):
    from PyQt6.QtWidgets import QLabel

    from dopeiptv.i18n import tr
    from dopeiptv.ui.stats_window import ColumnChart, StatsWindow
    win = StatsWindow(None, vs.ViewingLog(_Settings()), _Settings())
    assert win.findChild(QLabel, "StatsEmpty").text() == tr("stats_empty")
    assert win.findChildren(ColumnChart) == []


def test_the_window_shows_the_sums_and_the_period_scopes_them(qapp):
    from dopeiptv.ui.stats_window import PERIOD_KEY, StatsWindow, _Elided, fmt_time
    today = date.today()
    noon = datetime(today.year, today.month, today.day, 12).timestamp()
    log = vs.ViewingLog(_Settings())
    log.add("live:p:1", "SVT1 HD", vs.CHANNEL, 2 * 3600 + 600, noon)
    log.add("movie:p:2", "Dune", vs.FILM, 3600, noon - 400 * 86400 + 86400 * 40)
    s = _Settings()
    win = StatsWindow(None, log, s, today=today)
    assert win.tiles[vs.WEEK].text() == fmt_time(2 * 3600 + 600) == "2 h 10 min"
    assert win._period == vs.MONTH and win.period_buttons[vs.MONTH].isChecked()
    assert sum(win.hours_chart.values()) == pytest.approx(2 * 3600 + 600)
    assert win.hours_chart.values()[11] == pytest.approx(3600)
    assert "SVT1 HD" in [w.full() for w in win.findChildren(_Elided)]
    win.period_buttons[vs.ALL].click()
    assert s[PERIOD_KEY] == vs.ALL and sum(win.hours_chart.values()) == pytest.approx(3 * 3600 + 600)
    assert "Dune" in [w.full() for w in win.findChildren(_Elided)]
    assert win.days_chart.accessibleDescription().count(":") == 7
    # Opened again: the period last shown.
    assert StatsWindow(None, log, s, today=today)._period == vs.ALL


def test_times_read_as_people_say_them():
    pytest.importorskip("PyQt6.QtWidgets")
    from dopeiptv.i18n import set_language
    from dopeiptv.ui.stats_window import fmt_days, fmt_time
    set_language("en")
    assert fmt_time(0) == "0 min" and fmt_time(20) == "under a minute"
    assert fmt_time(45 * 60) == "45 min" and fmt_time(3 * 3600) == "3 h"
    assert fmt_time(3 * 3600 + 20 * 60) == "3 h 20 min" and fmt_time(140 * 3600 + 59) == "140 h"
    assert fmt_days(1) == "1 day" and fmt_days(12) == "12 days"


def test_it_is_in_the_history_menu():
    pytest.importorskip("PyQt6.QtWidgets")
    from dopeiptv.ui import app_menu
    history = {g: names for g, _where, names in app_menu.GROUPS}["history"]
    assert history[0] == "stats"
