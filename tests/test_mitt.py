"""Mine ("Mitt", core/mitt.py and ui/mitt_page.py): the sections out of the
stores' rows, the filter and the search, the favourite channels ordered by
the habits of this hour and weekday, the queue (its order kept, a title
added since at its end, moves, its count and time), History's timeline by
day across midnight and the week - and the page: sections hidden while
empty, the chips one Tab stop, Esc emptying the search, the queue's
buttons, keys and drag, a tile's heart, the timeline's headings stepped
over, mirrored right to left."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dopeiptv.core import mitt  # noqa: E402

# A Monday evening.
NOW = datetime(2026, 11, 16, 21, 10)


def _hour(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H")


def _hist(name, kind, key, when, **kw):
    return {"name": name, "_kind": kind, "_key": key, "_url": f"http://x/{key}",
            "_watched_at": when.isoformat(), **kw}


# -- the stores' rows as entries ---------------------------------------------------------

def test_history_rows_as_entries_and_merged_across_sources():
    a = mitt.history_entries([
        _hist("Kanal Ett", "live", 1, NOW - timedelta(minutes=5), stream_id=1),
        _hist("Northern Lights", "movie", 30, NOW - timedelta(days=1)),
        _hist("Kustlinjen · S2 E5", "episode", 501, NOW - timedelta(hours=2),
              _series_ctx={"series_id": 9, "name": "Kustlinjen"}),
        _hist("Old episode", "episode", 502, NOW - timedelta(days=3)),     # no series: a film
        _hist("Radio Klassisk", "radio", "radio:u1", NOW - timedelta(hours=3)),
        _hist("Semester.mp4", "local", "/v/s.mp4", NOW - timedelta(hours=4)),
        "junk", {"name": "no key", "_kind": "live"},
    ], "pa")
    kinds = [(e.title, e.kind, e.source) for e in a]
    assert ("Kanal Ett", mitt.CHANNEL, "pa") in kinds
    assert ("Old episode", mitt.FILM, "pa") in kinds
    assert ("Radio Klassisk", mitt.RADIO, "") in kinds          # belongs to no source
    assert ("Semester.mp4", mitt.LOCAL, "") in kinds
    assert len(a) == 6
    b = mitt.history_entries([
        _hist("Kanal Ett", "live", 1, NOW - timedelta(minutes=1), stream_id=1),
        _hist("Radio Klassisk", "radio", "radio:u1", NOW - timedelta(minutes=2)),
    ], "pb")
    both = mitt.merged(a, b)
    # Another source's channel is its own title; a station is one whatever
    # source it was played under - the newest kept.
    assert [e.title for e in both[:3]] == ["Kanal Ett", "Radio Klassisk", "Kanal Ett"]
    assert [e.source for e in both[:3]] == ["pb", "", "pa"]
    assert sum(1 for e in both if e.kind == mitt.RADIO) == 1
    assert all(both[i].when >= both[i + 1].when for i in range(len(both) - 1))


def test_continue_new_episodes_and_favourites():
    rows = [{"_kind": "vod", "stream_id": 30, "name": "Film", "_resume_pos": 600,
             "_resume_dur": 3600, "_ts": 5},
            {"_kind": "episode", "id": "501", "name": "Show · E5", "_resume_pos": 100,
             "_resume_dur": 200, "_ts": 9},
            {"_kind": "local", "_key": "/v/a.mkv", "name": "a", "_resume_pos": 90,
             "_resume_dur": 900, "_ts": 7},
            {"_kind": "rec", "_key": "x"}]
    cont = mitt.continue_entries(rows, "pa")
    assert [(e.kind, e.ident) for e in cont] == [(mitt.FILM, "30"), (mitt.EPISODE, "501"),
                                                 (mitt.LOCAL, "/v/a.mkv")]
    assert cont[0].progress == pytest.approx(600 / 3600)
    assert cont[2].source == ""
    new = mitt.new_entries([{"snap": {"series_id": 9, "name": "Kustlinjen"},
                             "new": [{"id": "1", "added": 100}, {"id": "2", "added": 300}]},
                            {"snap": {"name": "no id"}, "new": [{"id": "3"}]}], "pa")
    assert len(new) == 1 and new[0].when == 300 and len(new[0].row["_new"]) == 2
    groups = {"all": [{"stream_id": 1}, {"stream_id": 2}],
              "Sport": [{"stream_id": 3}, {"stream_id": 1}],
              "Barn": [{"stream_id": 4}], "Vuxen": [{"stream_id": 5}]}
    assert [r["stream_id"] for r in mitt.fav_rows(groups, "stream_id", {"Vuxen"})] == \
        [1, 2, 4, 3]                                # the default bucket, then by name


# -- the filter and the search ---------------------------------------------------------------

def test_chips_and_search():
    es = [mitt.Entry(mitt.CHANNEL, "1", "Kanal Ett", {}),
          mitt.Entry(mitt.FILM, "2", "Stjärnfall", {}),
          mitt.Entry(mitt.EPISODE, "3", "Kustlinjen", {}),
          mitt.Entry(mitt.SERIES, "4", "Fjällstationen", {}),
          mitt.Entry(mitt.RADIO, "radio:5", "Radio Klassisk", {}),
          mitt.Entry(mitt.LOCAL, "/v", "Semester 2025", {}),
          mitt.Entry(mitt.WEB, "http://y", "A stream", {})]

    def shown(chip, query=""):
        return [e.title for e in es if mitt.matches(e, chip, mitt.terms_of(query))]
    assert len(shown(mitt.ALL)) == 7
    assert shown(mitt.SERIES) == ["Kustlinjen", "Fjällstationen"]    # episodes are series'
    assert shown(mitt.CHANNEL) == ["Kanal Ett"]
    assert shown(mitt.LOCAL) == ["Semester 2025"]
    assert shown(mitt.ALL, "stjarn") == ["Stjärnfall"]              # accents aside
    assert shown(mitt.ALL, "FJÄLL station") == ["Fjällstationen"]
    assert shown(mitt.FILM, "kanal") == []
    # What is on now is matched too, once the window put it in.
    es[0].extra, es[0].text = "Kvällsnytt", ""
    assert shown(mitt.ALL, "kvallsnytt") == ["Kanal Ett"]


# -- habits ---------------------------------------------------------------------------------

def test_favourites_ordered_by_what_is_watched_at_this_hour_and_weekday():
    chans = [mitt.Entry(mitt.CHANNEL, str(i), f"C{i}", {}, "pa") for i in range(1, 6)]
    radio = mitt.Entry(mitt.RADIO, "radio:u9", "Radio", {})
    titles = {
        # C3: Monday evenings, three weeks running - the habit.
        "live:pa:3": {"k": "live", "h": {_hour(NOW - timedelta(days=7 * w)): 3000
                                         for w in range(1, 4)}},
        # C5: once, last Monday at 21 - watched there only one day.
        "live:pa:5": {"k": "live", "h": {_hour(NOW - timedelta(days=7)): 3500}},
        # C2: mornings only - nothing at this time.
        "live:pa:2": {"k": "live", "h": {_hour(NOW.replace(hour=8) - timedelta(days=d)): 3600
                                         for d in range(1, 10)}},
        # C4: weekday evenings but not Mondays, a fair amount.
        "live:pa:4": {"k": "live", "h": {_hour(NOW - timedelta(days=d)): 1800
                                         for d in (1, 2, 3, 8, 9)}},
        # The station, under two sources' names.
        "other:pa:radio:u9": {"k": "other", "h": {_hour(NOW - timedelta(days=7)): 1200}},
        "other:pb:radio:u9": {"k": "other", "h": {_hour(NOW - timedelta(days=14)): 1200}},
        "live:pb:3": {"k": "live", "h": {_hour(NOW - timedelta(days=7)): 9000}},  # another source
    }
    hours = mitt.habit_hours(chans + [radio], titles)
    assert set(hours) == {chans[2].key, chans[4].key, chans[1].key, chans[3].key, radio.key}
    assert len(hours[radio.key]) == 2                  # both sources' hours summed
    scores = {k: mitt.habit_score(h, NOW) for k, h in hours.items()}
    assert scores[chans[1].key] == (0.0, 0)            # mornings: none now
    ordered = [e.title for e in mitt.by_habit(chans, scores)]
    assert ordered[:3] == ["C3", "C5", "C4"] or ordered[:3] == ["C3", "C4", "C5"]
    assert ordered[-2:] == ["C1", "C2"]                # no habit: their own order kept
    marked = mitt.habitual(chans + [radio], scores)
    assert chans[2].key in marked                      # three Mondays
    assert chans[4].key not in marked                  # one day only
    # An hour either side counts half; older weeks fade.
    near = mitt.habit_score({_hour(NOW - timedelta(days=7, hours=1)): 1000}, NOW)[0]
    exact = mitt.habit_score({_hour(NOW - timedelta(days=7)): 1000}, NOW)[0]
    old = mitt.habit_score({_hour(NOW - timedelta(days=49)): 1000}, NOW)[0]
    assert near == pytest.approx(exact / 2) and old < exact / 2
    assert mitt.habit_score({_hour(NOW - timedelta(days=70)): 9999}, NOW) == (0.0, 0)


# -- the queue --------------------------------------------------------------------------------

def test_queue_order_kept_new_titles_at_the_end_and_moves():
    films = [{"stream_id": i, "name": f"F{i}"} for i in (1, 2, 3, 4, 5)]   # as added
    items = [(mitt.queue_ident(r, mitt.FILM), r) for r in films]
    # Nothing kept yet: in the order they were added, the first first.
    assert [r["name"] for _i, r in mitt.queue_order(items, [])] == ["F1", "F2", "F3", "F4", "F5"]
    order = ["vod:3", "vod:1", "vod:gone"]
    got = [i for i, _r in mitt.queue_order(items, order)]
    assert got == ["vod:3", "vod:1", "vod:2", "vod:4", "vod:5"]
    # A title added since goes to the end.
    items.append((mitt.queue_ident({"stream_id": 6}, mitt.FILM), {"stream_id": 6}))
    assert [i for i, _r in mitt.queue_order(items, got)][-1] == "vod:6"
    assert mitt.moved(got, "vod:2", -1) == ["vod:3", "vod:2", "vod:1", "vod:4", "vod:5"]
    assert mitt.moved(got, "vod:3", -1) == got
    assert mitt.moved(got, "vod:5", 9) == got
    assert mitt.moved_to(got, ["vod:5"], 0) == ["vod:5", "vod:3", "vod:1", "vod:2", "vod:4"]
    assert mitt.moved_to(got, ["vod:3"], 5) == ["vod:1", "vod:2", "vod:4", "vod:5", "vod:3"]
    assert mitt.moved_to(got, ["vod:1", "vod:4"], 4) == ["vod:3", "vod:2", "vod:1", "vod:4",
                                                         "vod:5"]
    assert mitt.queue_ident({"series_id": 7}, mitt.SERIES) == "series:7"
    assert mitt.queue_ident({"_tmdb_id": 99}, mitt.FILM) == "vod:tmdb:99"
    # Kept as JSON, bounded, junk read as none.
    raw = mitt.dump_order([f"vod:{i}" for i in range(900)] + ["vod:1"])
    assert len(mitt.parse_order(raw)) == mitt.ORDER_MAX
    assert mitt.parse_order("{bad") == [] and mitt.parse_order(None) == []
    assert mitt.parse_order(json.dumps(["a", 3, None, "a"])) == ["a", "3"]


def test_queue_summary_counts_and_time():
    def e(kind, row, pos=0.0, dur=0.0):
        return mitt.Entry(kind, "x", "t", row, pos=pos, dur=dur)
    s = mitt.queue_summary([e(mitt.FILM, {"duration_secs": 6720}),
                            e(mitt.FILM, {"duration": "01:44:00"}),
                            e(mitt.SERIES, {"episode_run_time": 58}),
                            e(mitt.FILM, {}, pos=1200, dur=4440)])          # 52 min left
    assert (s.films, s.series, s.unknown) == (3, 1, 0)
    assert s.seconds == 6720 + 104 * 60 + 58 * 60 + (4440 - 1200)
    s = mitt.queue_summary([e(mitt.FILM, {"duration": "52:00"}), e(mitt.SERIES, {})])
    assert s.seconds == 52 * 60 and s.unknown == 1
    assert mitt.length_of({"duration": "95"}) == 95 * 60
    assert mitt.length_of({"duration": "soon"}) == 0


# -- the timeline -------------------------------------------------------------------------------

def test_timeline_by_day_across_midnight_and_the_week():
    monday = datetime(2026, 11, 16, 0, 5)              # just past midnight, a Monday
    ts = {
        "now": monday.replace(minute=0) - timedelta(minutes=1),   # 23:59 Sunday: yesterday
        "today": monday.replace(minute=1),
        "sat": monday - timedelta(days=2),
        "last_mon": monday - timedelta(days=7),
        "old": datetime(2026, 9, 3, 20, 0),
    }
    es = [mitt.Entry(mitt.FILM, k, k, {}, when=v.timestamp()) for k, v in ts.items()]
    es.append(mitt.Entry(mitt.FILM, "untimed", "untimed", {}))
    es.sort(key=lambda e: e.when, reverse=True)
    groups = mitt.timeline(es, monday, week_start=0)
    by = {g: [e.ident for e in rows] for g, rows in groups}
    assert by[(mitt.TODAY,)] == ["today"]
    assert by[(mitt.YESTERDAY,)] == ["now"]
    # The week began this Monday: Saturday and last Monday were last week.
    assert by[(mitt.LAST_WEEK,)] == ["sat", "last_mon"]
    assert (mitt.WEEK,) not in by
    assert by[(mitt.MONTH, 2026, 9)] == ["old"]
    assert groups[-1][0] == (mitt.EARLIER,)
    # A week that begins on Sunday: Saturday is still last week; with it
    # beginning on Saturday, earlier this week.
    assert mitt.day_group(ts["sat"].timestamp(), monday, week_start=6) == (mitt.LAST_WEEK,)
    assert mitt.day_group(ts["sat"].timestamp(), monday, week_start=5) == (mitt.WEEK,)
    friday = datetime(2026, 11, 20, 22, 0)
    assert mitt.day_group(datetime(2026, 11, 18, 9).timestamp(), friday) == (mitt.WEEK,)
    assert mitt.day_group(datetime(2026, 11, 13, 9).timestamp(), friday) == (mitt.LAST_WEEK,)
    assert mitt.day_group(datetime(2026, 11, 21, 1).timestamp(), friday) == (mitt.TODAY,)


def test_history_actions_and_what_plays():
    ch = mitt.Entry(mitt.CHANNEL, "1", "C", {})
    film = mitt.Entry(mitt.FILM, "2", "F", {})
    assert mitt.action_of(ch, 99) == mitt.PLAY
    assert mitt.action_of(film, 120) == mitt.CONTINUE
    assert mitt.action_of(film, 0) == mitt.AGAIN
    keys = mitt.playing_keys("episode", "501", "pa", series_id=9)
    assert mitt.entry_key("pa", mitt.EPISODE, "501") in keys
    assert mitt.entry_key("pa", mitt.SERIES, 9) in keys
    keys = mitt.playing_keys("live", "failover:pb:7", "pa")
    assert keys == {mitt.entry_key("pb", mitt.CHANNEL, "7")}
    keys = mitt.playing_keys("radio", "radio:u1", "pa")
    assert mitt.entry_key("", mitt.RADIO, "radio:u1") in keys
    assert mitt.playing_keys(None, None, "pa") == set()


# -- the favourites' folders across every source ----------------------------------------------

def _stores():
    """Pirat (in use) and Nordic: a folder "Sport" in both, of channels and films."""
    return [
        mitt.FavStore("pa", "chan", {"all": [{"stream_id": 1, "name": "Kanal Ett"}],
                                     "Sport": [{"stream_id": 3, "name": "Sportkanalen"},
                                               {"stream_id": 4, "name": "Golf"}]},
                      colours={"Sport": {"bgcolor": "#2FBF71", "color": ""}}),
        mitt.FavStore("pa", "movie", {"Sport": [{"stream_id": 40, "name": "Matchen"}]}),
        mitt.FavStore("pa", "series", {}),
        mitt.FavStore("pb", "chan", {"all": [{"stream_id": 1, "name": "Nordic One"}],
                                     "Sport": [{"stream_id": 77, "name": "Nordic Sport"},
                                               {"stream_id": 77, "name": "twice"}],
                                     "Vuxen": [{"stream_id": 79, "name": "Late"}]},
                      locked=frozenset({"Vuxen"}),
                      colours={"Sport": {"bgcolor": "#FF5C5C"}}),
        mitt.FavStore("pb", "movie", {"Sport": [{"stream_id": 900, "name": "Nordic Film"},
                                                {"name": "no id"}]}),
        mitt.FavStore("", "radio", {"Sport": [{"stationuuid": "u1", "name": "Radio Sport"}]}),
    ]


def test_two_sources_folders_are_one_by_name_each_title_its_own_sources():
    loose, named = mitt.fav_folders(_stores())
    assert list(named) == ["Sport", "Vuxen"]                    # one "Sport", by name
    sport = named["Sport"]
    # The channels first, then the films, the stations; the source in use's
    # first in each; a title once per store.
    assert [(e.kind, e.source, e.ident) for e in sport.entries] == [
        (mitt.CHANNEL, "pa", "3"), (mitt.CHANNEL, "pa", "4"), (mitt.CHANNEL, "pb", "77"),
        (mitt.FILM, "pa", "40"), (mitt.FILM, "pb", "900"), (mitt.RADIO, "", "radio:u1")]
    assert len({e.key for e in sport.entries}) == len(sport.entries)
    assert sport.counts == {"chan": 3, "movie": 2, "radio": 1}
    assert sorted(sport.places) == [("", "radio"), ("pa", "chan"), ("pa", "movie"),
                                    ("pb", "chan"), ("pb", "movie")]
    e = sport.entries[2]
    assert e.row["_section"] == "chan" and e.row["_group"] == "Sport" and e.title == "Nordic Sport"
    assert sport.entries[-1].row["_kind"] == "radio" and sport.entries[-1].row["_key"] == "radio:u1"
    # Its colour the first store's that gives one (the source in use's); a
    # folder locked in one source is locked.
    assert sport.colour == {"bgcolor": "#2FBF71"} and not sport.locked
    assert named["Vuxen"].locked
    # The favourites in no folder: every source's, the same id of two
    # sources two titles.
    assert [(e.source, e.ident) for e in loose.entries] == [("pa", "1"), ("pb", "1")]
    assert loose.name == "all" and ("pb", "chan") in loose.places


def test_a_title_left_out_is_neither_listed_nor_counted_and_a_broken_test_shows_nothing():
    def keep(e):
        if e.ident == "4":
            raise RuntimeError("a test that broke")
        return e.source != "pb"
    _loose, named = mitt.fav_folders(_stores(), keep)
    sport = named["Sport"]
    assert [(e.source, e.ident) for e in sport.entries] == [("pa", "3"), ("pa", "40"),
                                                           ("", "radio:u1")]
    assert sport.counts == {"chan": 1, "movie": 1, "radio": 1}
    assert "Vuxen" in named and named["Vuxen"].entries == []     # the folder stays, empty
    assert mitt.fav_folders([]) == (mitt.Folder("all"), {})


def _idents(entries):
    return [e.ident for e in entries]


def test_a_title_dragged_lands_where_it_was_dropped_whatever_source_is_around_it():
    _loose, named = mitt.fav_folders(_stores())
    sport = named["Sport"].entries
    keys = [e.key for e in sport]
    # Nordic's channel before Pirat's first, then Nordic's film before Pirat's.
    got = mitt.dropped(sport, keys, [keys[2]], 0)
    assert _idents(got) == ["77", "3", "4", "40", "900", "radio:u1"]
    got = mitt.dropped(got, [e.key for e in got], [keys[4]], 3)
    assert _idents(got) == ["77", "3", "4", "900", "40", "radio:u1"]
    # Golf dragged up among its own source's, as before.
    assert _idents(mitt.dropped(sport, keys, [keys[1]], 0))[:3] == ["4", "3", "77"]
    # Among another kind's: nothing changes - the folder lists each kind together.
    assert mitt.dropped(sport, keys, [keys[0]], 4) == sport
    assert mitt.dropped(sport, keys, [keys[5]], 0) == sport
    assert mitt.dropped(sport, keys, [], 0) == sport
    # A chip or a search shows some: those not shown keep their places.
    got = mitt.dropped(sport, [keys[3], keys[4]], [keys[4]], 0)
    assert _idents(got) == ["3", "4", "77", "900", "40", "radio:u1"]
    # What is kept: the kinds two sources hold, in their new order; none
    # for a folder whose every kind is one source's.
    assert mitt.folder_order(got) == [keys[0], keys[1], keys[2], keys[4], keys[3]]
    assert mitt.folder_order([e for e in sport if e.source != "pb"]) == []


def test_the_order_kept_holds_through_a_rebuild_and_each_store_keeps_its_own():
    stores = _stores()
    _l, named = mitt.fav_folders(stores)
    keys = [e.key for e in named["Sport"].entries]
    # Nordic's channel dragged between Pirat's two: kept, and read back.
    got = mitt.dropped(named["Sport"].entries, keys, [keys[2]], 1)
    kept = mitt.parse_folder_orders(mitt.dump_folder_orders({"Sport": mitt.folder_order(got)}))
    loose, again = mitt.fav_folders(stores, orders=kept)
    assert _idents(again["Sport"].entries) == ["3", "77", "4", "40", "900", "radio:u1"]
    assert _idents(loose.entries) == ["1", "1"] and _idents(again["Vuxen"].entries) == ["79"]
    # A title added since in Nordic: at the end of its kind; one the order
    # does not name in the middle of its store (a lock opened): after the
    # one before it there; one in front of the first it names: before it.
    pb = {"Sport": [{"stream_id": 76, "name": "front"}, {"stream_id": 77, "name": "Nordic Sport"},
                    {"stream_id": 81, "name": "shown again"}, {"stream_id": 78, "name": "Nordic Golf"}]}
    kept = {"Sport": [keys[0], keys[2], "pb|live:78", keys[1]]}
    _l, again = mitt.fav_folders(stores[:3] + [mitt.FavStore("pb", "chan", pb)] + stores[4:],
                                 orders=kept)
    assert _idents(again["Sport"].entries)[:6] == ["3", "76", "77", "81", "78", "4"]
    pb["Sport"].append({"stream_id": 99, "name": "added since"})
    _l, again = mitt.fav_folders(stores[:3] + [mitt.FavStore("pb", "chan", pb)] + stores[4:],
                                 orders=kept)
    assert _idents(again["Sport"].entries)[:7] == ["3", "76", "77", "81", "78", "4", "99"]
    # A store reordered elsewhere (the sidebar) is followed as it is.
    pa = {"Sport": [{"stream_id": 4, "name": "Golf"}, {"stream_id": 3, "name": "Sportkanalen"}]}
    _l, again = mitt.fav_folders([mitt.FavStore("pa", "chan", pa)] + stores[1:],
                                 orders={"Sport": mitt.folder_order(got)})
    chans = [e for e in again["Sport"].entries if e.kind == mitt.CHANNEL]
    assert [e.ident for e in chans if e.source == "pa"] == ["4", "3"]
    assert sorted(_idents(chans)) == ["3", "4", "77"]


def test_the_folders_orders_are_read_back_bounded_and_kept_tidy():
    for junk in ("", "[1, 2]", "{", "null", '{"a": "b"}', '{"a": [1, {"x": 2}]}', b"\xff\xfe",
                 7, None, '{"a": ' + "[" * 5000 + "]" * 5000 + "}",
                 '{"a": ["' + "x" * mitt.FOLDER_ORDER_TEXT_MAX + '"]}'):
        assert mitt.parse_folder_orders(junk) == {}, junk[:40] if isinstance(junk, str) else junk
    assert mitt.parse_folder_orders('{"a": ["k", 3, "k", "j"], "b": []}') == {"a": ["k", "j"]}
    many = {f"f{i}": [f"k{i}"] for i in range(100)}
    got = mitt.parse_folder_orders(mitt.dump_folder_orders(many))
    assert list(got) == [f"f{i}" for i in range(100 - mitt.FOLDER_ORDERS_MAX, 100)]
    long = mitt.parse_folder_orders(mitt.dump_folder_orders({"a": [f"k{i}" for i in range(2000)]}))
    assert len(long["a"]) == mitt.ORDER_MAX
    # The last arranged last (so the last to go); none kept for an empty one.
    orders = {"a": ["1"], "b": ["2"]}
    assert list(mitt.order_kept(orders, "a", ["3"])) == ["b", "a"]
    assert mitt.order_kept(orders, "a", []) == {"b": ["2"]}
    # A title out of one folder, or out of every one.
    orders = {"a": ["1", "2"], "b": ["1", "3"], "c": ["1"]}
    assert mitt.order_without(orders, ["1"], "a") == {"a": ["2"], "b": ["1", "3"], "c": ["1"]}
    assert mitt.order_without(orders, ["1"]) == {"a": ["2"], "b": ["3"]}
    # Renamed: its order with it; onto a folder with an order of its own, that one.
    assert mitt.order_renamed(orders, "a", "z") == {"b": ["1", "3"], "c": ["1"], "z": ["1", "2"]}
    assert mitt.order_renamed(orders, "a", "b") == {"b": ["1", "3"], "c": ["1"]}
    assert mitt.order_renamed(orders, "q", "z") == orders


# -- the page ---------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    from PyQt6.QtWidgets import QApplication
    a = QApplication.instance() or QApplication([])
    from dopeiptv.ui import theme as th
    th.apply_theme(theme="graphite")
    return a


def _spin(app, secs=0.15):
    end = time.time() + secs
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def _page(app, size=(760, 900)):
    from dopeiptv.ui.mitt_page import MittPage
    page = MittPage(None, None)
    page.resize(*size)
    page.show()
    _spin(app)
    return page


def _cards(n, prefix="k", **kw):
    from dopeiptv.ui.media_cards import Card
    return [Card(key=f"{prefix}{i}", title=f"Title {i}", line="Movie", data=i, **kw)
            for i in range(n)]


def test_sections_hidden_while_empty_and_the_empty_word(app):
    from dopeiptv.ui.media_cards import ChannelRow
    page = _page(app)
    try:
        for name in ("continue", "live", "favorites", "queue", "new", "history"):
            assert not page.sections[name].isVisibleTo(page)
        page.set_section("queue", _cards(3), "3 movies · 5 h")
        page.set_section("live", [ChannelRow(key="c1", name="Kanal Ett")], "", "Show all (40)")
        page.set_section("continue", _cards(2, "c"))
        _spin(app)
        assert page.sections["queue"].isVisibleTo(page)
        assert page.heads["queue"].note.text() == "3 movies · 5 h"
        assert page.heads["live"].button.isVisibleTo(page)
        assert page.rows["continue"].isVisibleTo(page)
        assert not page.sections["history"].isVisibleTo(page)
        assert page.first_view() is page.rows["continue"].strip
        page.set_section("queue", [])
        assert not page.sections["queue"].isVisibleTo(page)
        page.set_empty("Nothing here matches the filter.")
        assert page.empty.isVisibleTo(page)
        page.grab()
    finally:
        page.close()


def test_chips_one_tab_stop_arrows_pick_and_esc_empties_the_search(app):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    page = _page(app)
    try:
        said = []
        page.filter_changed.connect(lambda: said.append(page.chip()))
        policies = [b.focusPolicy() for b in page.chips.buttons]
        assert policies.count(Qt.FocusPolicy.StrongFocus) == 1
        b0 = page.chips.buttons[0]
        b0.setFocus()
        QTest.keyClick(b0, Qt.Key.Key_Right)
        assert page.chip() == "channel" and said[-1] == "channel"
        assert page.chips.buttons[1].hasFocus() or page.chips.buttons[1].focusPolicy() == \
            Qt.FocusPolicy.StrongFocus
        page.chips.buttons[3].click()
        assert page.chip() == "series"
        page.field.setText("abc")
        _spin(app, 0.3)                                  # debounced
        assert said[-1] == "series"
        assert page.escape() is True and page.query() == ""
        assert page.escape() is False                    # empty: the window goes back
        page.set_tab("folders")
        assert page.tab() == "folders" and page.folders.isVisibleTo(page)
        assert not page.overview.isVisibleTo(page)
    finally:
        page.close()


def test_queue_buttons_keys_and_drag(app):
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtTest import QTest
    from dopeiptv.ui.mitt_page import queue_parts
    page = _page(app)
    try:
        page.set_section("queue", _cards(5))
        _spin(app)
        q = page.queue
        nudged, moved, removed = [], [], []
        q.nudged.connect(lambda c, d: nudged.append((c.key, d)))
        q.moved_to.connect(lambda c, i: moved.append((c.key, i)))
        q.removed.connect(lambda c: removed.append(c.key))
        activated = []
        page.activated.connect(lambda s, c: activated.append((s, c.key)))
        r = q.visualRect(q.model_.index(2, 0))
        parts = queue_parts(r, False)
        QTest.mouseClick(q.viewport(), Qt.MouseButton.LeftButton, pos=parts["up"].center())
        QTest.mouseClick(q.viewport(), Qt.MouseButton.LeftButton, pos=parts["down"].center())
        QTest.mouseClick(q.viewport(), Qt.MouseButton.LeftButton, pos=parts["remove"].center())
        assert nudged == [("k2", -1), ("k2", 1)] and removed == ["k2"]
        assert activated == []                          # a button is no play
        # The first row has no Move up: a click there plays it.
        r0 = q.visualRect(q.model_.index(0, 0))
        assert q.button_at(queue_parts(r0, False)["up"].center()) is None
        QTest.mouseClick(q.viewport(), Qt.MouseButton.LeftButton, pos=parts["text"].center())
        assert activated == [("queue", "k2")]
        q.setFocus()
        q.setCurrentIndex(q.model_.index(1, 0))
        QTest.keyClick(q, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
        QTest.keyClick(q, Qt.Key.Key_Delete)
        assert nudged[-1] == ("k1", 1) and removed[-1] == "k1"
        # A row dragged below the fourth.
        start = q.visualRect(q.model_.index(0, 0)).center()
        end = q.visualRect(q.model_.index(3, 0)).bottomLeft() + QPoint(80, -4)
        QTest.mousePress(q.viewport(), Qt.MouseButton.LeftButton, pos=start)
        for i in range(1, 9):
            QTest.mouseMove(q.viewport(), start + (end - start) * (i / 8))
        QTest.mouseRelease(q.viewport(), Qt.MouseButton.LeftButton, pos=end)
        assert moved == [("k0", 4)], moved
        assert activated == [("queue", "k2")]           # a drag is no play either
    finally:
        page.close()


def test_a_tiles_heart_and_its_keys(app):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from dopeiptv.ui.media_cards import ChannelRow
    from dopeiptv.ui.mitt_page import TILE_H, tile_parts
    from dopeiptv.ui import media_cards as mc
    page = _page(app)
    try:
        page.set_section("live", [ChannelRow(key=f"c{i}", name=f"C{i}") for i in range(6)])
        _spin(app, 0.3)
        t = page.tiles
        hearts, played = [], []
        page.heart.connect(lambda r: hearts.append(r.key))
        page.activated.connect(lambda s, r: played.append((s, r.key)))
        idx = t.model_.index(1, 0)
        slot = t.visualRect(idx)
        col = mc.left_column(slot.x(), t.metrics, t.viewport().width(), False)
        tile = mc.card_in_slot(slot, t.metrics, col)
        tile.setHeight(TILE_H)
        QTest.mouseClick(t.viewport(), Qt.MouseButton.LeftButton,
                         pos=tile_parts(tile, False)["heart"].center())
        assert hearts == ["c1"] and played == []
        QTest.mouseClick(t.viewport(), Qt.MouseButton.LeftButton,
                         pos=tile_parts(tile, False)["text"].center())
        assert played == [("live", "c1")]
        t.off = {"c1"}
        t.habit = {"c0"}
        t.grab()                                        # dimmed and marked: paints
        t.setFocus()
        t.setCurrentIndex(t.model_.index(3, 0))
        QTest.keyClick(t, Qt.Key.Key_Delete)
        assert hearts[-1] == "c3"
    finally:
        page.close()


def test_timeline_headings_are_stepped_over(app):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from dopeiptv.ui.media_cards import Card
    from dopeiptv.ui.mitt_page import Heading
    page = _page(app)
    try:
        rows = [Heading(key="h1", title="Today"), Card(key="a", title="A"),
                Card(key="b", title="B"), Heading(key="h2", title="Yesterday"),
                Card(key="c", title="C")]
        page.set_section("history", rows, "", "Show more")
        _spin(app)
        tl = page.timeline
        assert page.history_more.isVisibleTo(page)
        assert tl.group_ends(1) == (True, False) and tl.group_ends(2) == (False, True)
        assert tl.group_ends(4) == (True, True)
        tl.setFocus()
        _spin(app)
        assert tl.current_item().key == "a"             # never on a heading
        QTest.keyClick(tl, Qt.Key.Key_Down)
        QTest.keyClick(tl, Qt.Key.Key_Down)
        assert tl.current_item().key == "c"
        QTest.keyClick(tl, Qt.Key.Key_Up)
        assert tl.current_item().key == "b"
        played = []
        page.activated.connect(lambda s, c: played.append(c.key))
        QTest.keyClick(tl, Qt.Key.Key_Return)
        assert played == ["b"]
        tl.grab()
    finally:
        page.close()


def test_the_parts_are_mirrored_right_to_left(app):
    from PyQt6.QtCore import QRect
    from dopeiptv.ui import mitt_page as mp
    r = QRect(0, 0, 600, 64)
    ltr, rtl = mp.queue_parts(r, False), mp.queue_parts(r, True)
    assert ltr["pos"].left() < ltr["remove"].left()
    assert rtl["pos"].left() > rtl["remove"].left()
    assert rtl["picture"].right() == r.right() - (ltr["picture"].left() - r.left())
    t = QRect(0, 0, 300, mp.TILE_H)
    assert mp.tile_parts(t, False)["heart"].left() > mp.tile_parts(t, False)["logo"].left()
    assert mp.tile_parts(t, True)["heart"].left() < mp.tile_parts(t, True)["logo"].left()
    tl, tr_ = mp.timeline_parts(r, 80, 60, False), mp.timeline_parts(r, 80, 60, True)
    assert tl["when"].left() < tl["action"].left() and tr_["when"].left() > tr_["action"].left()
    assert mp.drop_index(0, 64, 5) == 0 and mp.drop_index(40, 64, 5) == 1
    assert mp.drop_index(10_000, 64, 5) == 5
    # A folder's kinds together: a drag offered its own kind's places alone.
    kinds = ["chan", "chan", "chan", "movie", "movie", "radio"]
    assert mp.drop_span(kinds, [2], 6) == (0, 3)
    assert mp.drop_span(kinds, [3], 6) == (3, 5) and mp.drop_span(kinds, [5], 6) == (5, 6)
    assert mp.drop_span(kinds, [0, 3], 6) is None and mp.drop_span(kinds, [], 6) is None
    assert mp.drop_span([], [0], 4) == (0, 4)


def test_the_page_right_to_left(app):
    from PyQt6.QtCore import Qt
    from dopeiptv.ui.media_cards import ChannelRow
    page = _page(app)
    try:
        page.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        page.set_section("live", [ChannelRow(key=f"c{i}", name=f"C{i}") for i in range(4)])
        page.set_section("queue", _cards(3))
        _spin(app, 0.3)
        t = page.tiles
        a = t.visualRect(t.model_.index(0, 0))
        b = t.visualRect(t.model_.index(1, 0))
        assert a.left() > b.left()                      # the first tile at the right
        x0 = page.chips.buttons[0].geometry().x()
        x1 = page.chips.buttons[1].geometry().x()
        assert x0 > x1                                  # the chips from the right too
        page.grab()
    finally:
        page.close()


def test_the_queue_order_is_a_profiles_own_backed_up_and_not_synced(tmp_path):
    from PyQt6.QtCore import QSettings
    from dopeiptv.core import backup, profiles, sync
    assert profiles.is_own(mitt.ORDER_KEY)
    assert not sync.syncable(mitt.ORDER_KEY)
    assert not sync.syncable(f"profiles/kim/{mitt.ORDER_KEY}")
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s.setValue(f"profiles/me/{mitt.ORDER_KEY}", mitt.dump_order(["vod:1", "series:2"]))
    doc = backup.collect({"main": s}, "1.6.2")
    kept = doc["stores"]["main"][f"profiles/me/{mitt.ORDER_KEY}"]
    assert mitt.parse_order(kept) == ["vod:1", "series:2"]


def test_a_folders_order_is_a_profiles_own_backed_up_and_not_synced(tmp_path):
    from PyQt6.QtCore import QSettings
    from dopeiptv.core import backup, profiles, sync
    assert profiles.is_own(mitt.FOLDER_ORDER_KEY)
    assert not sync.syncable(mitt.FOLDER_ORDER_KEY)
    assert not sync.syncable(f"profiles/kim/{mitt.FOLDER_ORDER_KEY}")
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s.setValue(f"profiles/me/{mitt.FOLDER_ORDER_KEY}",
               mitt.dump_folder_orders({"Sport": ["pb|live:77", "pa|live:3"]}))
    doc = backup.collect({"main": s}, "1.6.2")
    kept = doc["stores"]["main"][f"profiles/me/{mitt.FOLDER_ORDER_KEY}"]
    assert mitt.parse_folder_orders(kept) == {"Sport": ["pb|live:77", "pa|live:3"]}
