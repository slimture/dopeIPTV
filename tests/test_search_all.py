"""One search across every source (core/search_all.py; the sources' side
services/search_sources.py): matching as the command palette matches -
case, accents and punctuation aside, word starts first, the exact title
first - the four groups ranked and capped, each hit naming its source; what
a source's lists leave out (Shown) and what a kids profile never sees left
out before anything is matched; a portal's lists only as far as they were
loaded (paged_lists), never paged through; a guide's week from now on,
compact, read from disk without a download, one guide at a time; a query
given up once a newer one is typed; and no stream asked for until a hit is
played."""
from __future__ import annotations

import threading
import time
from array import array

from dopeiptv.core import search_all as sa
from dopeiptv.core.stores import CategoryOverrides, ChannelOverrides
from dopeiptv.services import search_sources as ss
from dopeiptv.services.failover import Shown

NOW = 1_800_000_000
A = sa.Source("a", "Pirat")
B = sa.Source("b", "Nordic", "m3u")


def _cat(source=A, channels=(), movies=(), series=(), **kw) -> sa.Catalogue:
    return sa.Catalogue(source, list(channels), list(movies), list(series), **kw)


def _titles(res, group):
    return [h.title for h in res.hits[group]]


# -- matching and ranking --------------------------------------------------------------

def test_case_accents_and_word_starts_and_the_exact_title_first():
    movies = [{"stream_id": i, "name": n} for i, n in enumerate(
        ["Star Trek: Discovery", "Startrek fans", "Star Trek", "The Lone Star Trek",
         "Stärk trekking", "Café Star TREK 2"])]
    res = sa.search("star trek", [_cat(movies=movies)], now=NOW)
    got = _titles(res, "movies")
    assert got[0] == "Star Trek"                       # the exact title
    assert got[1] == "Star Trek: Discovery"            # at the very start, shorter
    # Both words at word starts, the shorter first; inside a word last.
    assert got[2:5] == ["Stärk trekking", "Café Star TREK 2", "The Lone Star Trek"]
    assert got[5] == "Startrek fans"
    assert res.totals["movies"] == 6
    assert sa.search("STAR trék", [_cat(movies=movies)], now=NOW).hits["movies"][0].title == "Star Trek"
    # Every word has to be there; one letter finds nothing at all.
    assert _titles(sa.search("star wars", [_cat(movies=movies)], now=NOW), "movies") == []
    assert sa.search("s", [_cat(movies=movies)], now=NOW).count() == 0


def test_groups_across_sources_name_their_source_and_the_one_in_use_wins_a_tie():
    a = _cat(A, channels=[{"stream_id": 1, "name": "Vegas TV", "category_id": "7"}],
             categories={"live": {"7": "Sport"}})
    b = _cat(B, channels=[{"stream_id": 9, "name": "Vegas TV", "category_id": "x"}],
             series=[{"series_id": 3, "name": "Vegas", "year": "2012"}])
    res = sa.search("vegas", [b, a], now=NOW, active="a")
    chans = res.hits["channels"]
    assert [h.source.name for h in chans] == ["Pirat", "Nordic"]     # in use first
    assert chans[0].category == "Sport" and chans[1].category == ""
    assert res.hits["series"][0].source is B and res.hits["series"][0].row["series_id"] == 3
    assert res.count() == 3
    # Without one in use, the sources' order.
    assert [h.source.name for h in sa.search("vegas", [b, a], now=NOW).hits["channels"]] == \
        ["Nordic", "Pirat"]


def test_each_group_is_capped_and_says_how_many_there_were():
    chans = [{"stream_id": i, "name": f"News {i}"} for i in range(50)]
    res = sa.search("news", [_cat(channels=chans)], now=NOW, cap=10)
    assert len(res.hits["channels"]) == 10 and res.totals["channels"] == 50


def test_the_users_names_are_matched_and_shown():
    rows = [{"stream_id": 5, "name": "SE| TV4 HD"}]
    cat = _cat(channels=rows, renamed={"live": {"5": "Fyran"}})
    assert _titles(sa.search("fyran", [cat], now=NOW), "channels") == ["Fyran"]
    assert sa.search("tv4", [cat], now=NOW).count() == 0


# -- what is never shown ----------------------------------------------------------------

def _overrides(tmp_path):
    from PyQt6.QtCore import QSettings
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    cats = CategoryOverrides(s, "category_overrides_b")
    chans = ChannelOverrides(s, "channel_overrides_b")
    cats.update("live", "hid", hidden=True)
    cats.update("live", "lock", locked=True)
    cats.update("vod", "vlock", locked=True)
    cats.update("live", "ren", name="XXX Adults")
    chans.update("live", "3", hidden=True)
    chans.update("live", "4", name="My own")
    return cats, chans


def test_shown_leaves_out_hidden_locked_and_hidden_by_hand(tmp_path):
    cats, chans = _overrides(tmp_path)
    rows = [{"stream_id": 1, "name": "A", "category_id": "hid"},
            {"stream_id": 2, "name": "B", "category_id": "lock"},
            {"stream_id": 3, "name": "C", "category_id": "ok"},
            {"stream_id": 4, "name": "D", "category_id": "ok"}]
    locked = Shown.of(cats, chans, True, "live")
    assert [r["stream_id"] for r in sa.visible(rows, locked.keep)] == [4]
    assert locked.titles == {"4": "My own"}
    opened = Shown.of(cats, chans, False, "live")
    assert [r["stream_id"] for r in sa.visible(rows, opened.keep)] == [2, 4]
    # Per kind: the films' own locks, not the channels'.
    films = Shown.of(cats, chans, True, "vod")
    assert films.keep({"stream_id": 3, "category_id": "vlock"}) is False
    assert films.keep({"stream_id": 3, "category_id": "hid"}) is True


def test_a_kids_profile_sees_nothing_for_adults(tmp_path):
    cats, chans = _overrides(tmp_path)
    shown = Shown.of(cats, chans, True, "live")
    provider = [{"category_id": "ren", "category_name": "Family"},
                {"category_id": "k", "category_name": "Kids"},
                {"category_id": "a18", "category_name": "Adult 18+"}]
    adult = sa.adult_ids(provider, shown.names)
    assert adult == {"ren", "a18"}                 # by the user's name too
    rows = [{"stream_id": 10, "name": "Toons", "category_id": "k"},
            {"stream_id": 11, "name": "Late", "category_id": "a18"},
            {"stream_id": 12, "name": "Renamed", "category_id": "ren"},
            {"stream_id": 13, "name": "Flagged", "category_id": "k", "is_adult": 1},
            {"stream_id": 14, "name": "Nowhere"}]
    # As a kids profile's lists have them: a row of no category is none of
    # its (what a provider leaves unlabelled is not taken to be for children).
    assert [r["stream_id"] for r in sa.visible(rows, shown.keep, kids=True, adult=adult)] == [10]
    # Categories that could not be read: none of the rows.
    assert [r["stream_id"] for r in sa.visible(rows, None, kids=True, adult=None)] == []
    assert len(sa.visible(rows, None, kids=False, adult=None)) == 5
    # A category the source does not list; only what a grown-up allows.
    more = rows + [{"stream_id": 15, "name": "Elsewhere", "category_id": "zz"}]
    known = frozenset(c["category_id"] for c in provider)
    assert [r["stream_id"] for r in sa.visible(more, None, True, adult, known)] == [10]
    assert sa.visible(more, None, True, adult, known, allowed=frozenset()) == []
    assert [r["stream_id"] for r in sa.visible(more, None, True, adult, known,
                                               allowed=frozenset({"k"}))] == [10]
    assert [r["stream_id"] for r in sa.visible(more, None, False, adult, known,
                                               allowed=frozenset())] == [10, 11, 12, 13, 14, 15]


class _Client:
    """A source's client, counting what it is asked."""
    paged_lists = False

    def __init__(self, live=(), vod=(), series=(), cats=None):
        self.asked: list[str] = []
        self._live, self._vod, self._series = list(live), list(vod), list(series)
        self._cats = cats or []

    def live_streams(self, cat=None):
        self.asked.append("live")
        return self._live

    def vod_streams(self, cat=None):
        self.asked.append("vod")
        return self._vod

    def series_list(self, cat=None):
        self.asked.append("series")
        return self._series

    def live_categories(self):
        return self._cats

    def vod_categories(self):
        return []

    def series_categories(self):
        return []

    def live_url(self, sid, fmt="ts"):
        self.asked.append(f"url:{sid}")
        return f"http://b/live/{sid}.{fmt}"


def _plan(client=None, kids=False, shown=None, key=("k",), source=B, guide=None):
    return ss.Plan(source, {"id": source.id, "name": source.name, "kind": source.kind},
                   shown or {}, kids, client=client, guide=guide, key=key)


def test_a_catalogue_is_read_once_and_filtered_before_matching(tmp_path):
    cats, chans = _overrides(tmp_path)
    shown = {m: Shown.of(cats, chans, True, m) for m in ("live", "vod", "series")}
    client = _Client(live=[{"stream_id": 1, "name": "Star 1", "category_id": "hid"},
                           {"stream_id": 4, "name": "Star 4", "category_id": "ok"},
                           {"name": "no id"}],
                     vod=[{"stream_id": 7, "name": "Star film", "category_id": "vlock"}],
                     cats=[{"category_id": "ok", "category_name": "Sport"}])
    made = []
    store = ss.Sources(make_client=lambda pl: made.append(pl["id"]) or client,
                       guide_path=lambda pid: str(tmp_path / "none.xml"))
    plan = _plan(shown=shown)
    cat = store.catalogue(plan)
    assert [r["stream_id"] for r in cat.channels] == [4] and cat.movies == []
    assert cat.category("channels", cat.channels[0]) == "Sport"
    assert cat.titles["channels"] == ["My own"]
    assert store.catalogue(plan) is cat and made == ["b"]
    assert client.asked.count("live") == 1                 # once a session
    # Another key (a profile, the lock opened): the same rows filtered anew -
    # nothing asked of the source again (its list cache lasts minutes, so
    # asking would download its whole catalogue once more).
    other = store.catalogue(_plan(key=("other",)))          # nothing left out
    assert made == ["b"] and client.asked.count("live") == 1
    assert sorted(r["stream_id"] for r in other.channels) == [1, 4]
    # Its lists refreshed: asked again.
    store.drop("b")
    store.catalogue(plan)
    assert client.asked.count("live") == 2
    # Nothing looked up opens a stream.
    assert not any(a.startswith("url:") for a in client.asked)


class _Portal(_Client):
    """A portal: pages of lists - never asked for, only what it has."""
    paged_lists = True

    def __init__(self, whole):
        super().__init__()
        self.whole = whole

    def loaded(self, kind):
        self.asked.append(f"loaded:{kind}")
        rows = [{"stream_id": 1, "name": "Star Portal"}] if kind == "live" else []
        return rows, [], self.whole

    def live_streams(self, cat=None):
        raise AssertionError("a portal's lists were paged through")

    vod_streams = series_list = live_streams


def test_a_portal_is_searched_only_as_far_as_its_lists_were_loaded(tmp_path):
    portal = _Portal(whole=False)
    store = ss.Sources(make_client=lambda pl: portal,
                       guide_path=lambda pid: str(tmp_path / "none.xml"))
    cat = store.catalogue(_plan())
    assert cat.partial == sa.PAGED and portal.asked == ["loaded:live", "loaded:vod",
                                                        "loaded:series"]
    res = sa.search("star", [cat], now=NOW)
    assert _titles(res, "channels") == ["Star Portal"]
    assert res.partial == [(B, sa.PAGED)]
    whole = ss.Sources(make_client=lambda pl: _Portal(whole=True),
                       guide_path=lambda pid: str(tmp_path / "none.xml"))
    assert whole.catalogue(_plan()).partial == ""


def test_the_stalker_client_reads_only_its_cached_lists(tmp_path):
    from dopeiptv.providers.stalker import StalkerClient
    c = StalkerClient("http://portal.invalid/c/", "00:1A:79:00:00:01",
                      cache_path=str(tmp_path / "lists_p.json"))
    assert c.paged_lists is True
    c._call = lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked the portal"))
    assert c.loaded("live") == ([], [], False)
    c._list_cache[("itv_genres",)] = (time.time(), [{"category_id": "1"}, {"category_id": "2"}])
    c._list_cache[("itv_genre", "1")] = (time.time(), [{"stream_id": 5, "name": "A"}])
    rows, cats, whole = c.loaded("live")
    assert [r["stream_id"] for r in rows] == [5] and len(cats) == 2 and not whole
    c._list_cache[("itv_genre", "2")] = (time.time(), [{"stream_id": 6, "name": "B"},
                                                       {"stream_id": 5, "name": "A"}])
    rows, _cats, whole = c.loaded("live")
    assert sorted(r["stream_id"] for r in rows) == [5, 6] and whole
    c._list_cache[("vod", "list", "*")] = (time.time(), [{"stream_id": 8, "name": "F"},
                                                         {"series_id": 9, "name": "S"}])
    assert [r["stream_id"] for r in c.loaded("vod")[0]] == [8]
    assert [r["series_id"] for r in c.loaded("series")[0]] == [9] and c.loaded("vod")[2]


def test_a_source_that_does_not_answer_is_said_to_be_searched_in_part(tmp_path):
    class Down(_Client):
        def live_streams(self, cat=None):
            raise OSError("no route")

    store = ss.Sources(make_client=lambda pl: Down(),
                       guide_path=lambda pid: str(tmp_path / "none.xml"))
    cat = store.catalogue(_plan())
    assert cat.partial == sa.FAILED and cat.channels == []
    broken = ss.Sources(make_client=lambda pl: (_ for _ in ()).throw(ValueError("bad")),
                        guide_path=lambda pid: str(tmp_path / "none.xml"))
    assert broken.catalogue(_plan()).partial == sa.FAILED


# -- the guide ----------------------------------------------------------------------------

def _sched(*progs):
    progs = sorted(progs)
    return (array("q", [int(p[0]) for p in progs]), array("q", [int(p[1]) for p in progs]),
            tuple(p[2] for p in progs), tuple(p[3] for p in progs))


H = 3600
SVT = _sched((NOW - 2 * H, NOW - H, "Star Trek", "old"),            # over
             (NOW - 600, NOW + 1200, "Star Trek", "on now"),
             (NOW + H, NOW + 2 * H, "News", "with Star Trek news"),
             (NOW + 3 * H, NOW + 4 * H, "Star Trek", "later today"),
             (NOW + 8 * 86400, NOW + 8 * 86400 + H, "Star Trek", "too far"))


def test_a_weeks_programmes_from_now_title_first_then_the_description():
    chans = [{"stream_id": 1, "name": "SVT1 HD", "epg_channel_id": "svt"},
             {"stream_id": 2, "name": "SVT1", "epg_channel_id": "svt"}]
    table = sa.GuideTable(A, chans, lambda ch: SVT, NOW)
    assert len(table) == 3                    # the shared schedule once, a week ahead
    res = sa.search("star trek", [_cat()], [table], now=NOW)
    hits = res.hits["programmes"]
    assert [h.programme["start_timestamp"] for h in hits] == [NOW - 600, NOW + 3 * H, NOW + H]
    assert hits[0].on_air(NOW) and not hits[0].later(NOW) and hits[1].later(NOW)
    assert hits[-1].title == "News"            # only its description matched: last
    assert all(h.row is chans[0] and h.category == "SVT1 HD" for h in hits)
    # Later, the one on the air is over and goes.
    later = sa.search("star trek", [_cat()], [table], now=NOW + 1800)
    assert [h.programme["start_timestamp"] for h in later.hits["programmes"]] == \
        [NOW + 3 * H, NOW + H]


def test_the_same_programme_on_two_guide_ids_shows_once():
    twin = _sched((NOW + H, NOW + 2 * H, "Vegas", ""))
    other = _sched((NOW + H, NOW + 2 * H, "Vegas", ""))
    chans = [{"stream_id": 1, "name": "A", "epg_channel_id": "x"},
             {"stream_id": 2, "name": "B", "epg_channel_id": "y"}]
    table = sa.GuideTable(A, chans, lambda ch: twin if ch["epg_channel_id"] == "x" else other, NOW)
    res = sa.search("vegas", [], [table], now=NOW)
    assert len(res.hits["programmes"]) == 1 and res.totals["programmes"] == 1


def test_a_huge_guide_keeps_the_soonest_and_says_so():
    progs = [(NOW + i * 60, NOW + i * 60 + 60, f"Show {i % 7}", "") for i in range(500)]
    table = sa.GuideTable(A, [{"stream_id": 1, "name": "X"}], lambda ch: _sched(*progs),
                          NOW, limit=100)
    assert len(table) == 100 and table.cut
    assert max(table.starts) == NOW + 99 * 60
    res = sa.search("show", [], [table], now=NOW)
    assert res.partial == [(A, sa.GUIDE_CUT)]


class _Guide:
    """A guide on disk: read with load_cached, never downloaded."""
    opened: list[str] = []
    busy = 0
    most = 0

    def __init__(self, path):
        self.path = path
        _Guide.opened.append(path)

    def load_cached(self):
        _Guide.busy += 1
        _Guide.most = max(_Guide.most, _Guide.busy)
        time.sleep(0.02)
        _Guide.busy -= 1
        return True

    def schedule(self, ch):
        return SVT

    def ensure_loaded(self, force=False):
        raise AssertionError("a guide was downloaded")


def test_guides_are_read_from_disk_one_at_a_time_and_kept(tmp_path):
    for pid in ("b", "c"):
        (tmp_path / f"epg_{pid}.xml").write_bytes(b"x")
    store = ss.Sources(make_client=lambda pl: _Client(
        live=[{"stream_id": 1, "name": "SVT1", "epg_channel_id": "svt"}]),
        guide_path=lambda pid: str(tmp_path / f"epg_{pid}.xml"), open_guide=_Guide)
    plans = [_plan(), _plan(source=sa.Source("c", "Third"))]
    threads = [threading.Thread(target=lambda p=p: store.guide(p, store.catalogue(p), NOW))
               for p in plans]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert _Guide.most == 1, "two guides were in memory at once"
    assert len(_Guide.opened) == 2
    table = store.guide_in(plans[0], NOW)
    assert table is not None and len(table) == 3 and store.done(plans[0], NOW)
    assert store.guide(plans[0], store.catalogue(plans[0]), NOW) is table
    assert len(_Guide.opened) == 2                         # kept, not read again
    # No guide on this computer: none, remembered - nothing to read again.
    none = _plan(source=sa.Source("d", "No guide"))
    assert store.guide(none, store.catalogue(none), NOW) is None
    assert store.done(none, NOW) and store.guide_in(none, NOW) is None


def test_the_guide_in_use_is_read_from_memory():
    class Mem:
        loaded = False

        def is_loaded(self):
            return self.loaded

        def schedule(self, ch):
            return SVT

    mem = Mem()
    store = ss.Sources(make_client=lambda pl: _Client(), guide_path=lambda pid: "/nonexistent")
    client = _Client(live=[{"stream_id": 1, "name": "SVT1"}])
    plan = _plan(client=client, guide=mem, source=A)
    assert store.guide(plan, store.catalogue(plan), NOW) is None and store.done(plan, NOW)
    mem.loaded = True                       # loaded since: looked at again
    assert not store.done(plan, NOW)
    assert len(store.guide(plan, store.catalogue(plan), NOW)) == 3


def _disk_store(tmp_path, on_read=None):
    """Sources with a guide on disk for b and c, counting each read; *on_read*
    runs while a guide is being read (on its worker)."""
    reads = []

    class Disk:
        def __init__(self, path):
            reads.append(path)

        def load_cached(self):
            if on_read is not None:
                on_read()
            return True

        def schedule(self, ch):
            return SVT

    for pid in ("b", "c"):
        (tmp_path / f"epg_{pid}.xml").write_bytes(b"x")
    store = ss.Sources(make_client=lambda pl: _Client(
        live=[{"stream_id": 1, "name": "SVT1", "epg_channel_id": "svt"}]),
        guide_path=lambda pid: str(tmp_path / f"epg_{pid}.xml"), open_guide=Disk)
    return store, reads


def test_guide_weeks_are_let_go_and_read_again_when_next_asked_for(tmp_path):
    store, reads = _disk_store(tmp_path)

    class Mem:
        def is_loaded(self):
            return True

        def schedule(self, ch):
            return SVT

    other = _plan()
    here = _plan(client=_Client(live=[{"stream_id": 1, "name": "SVT1"}]), guide=Mem(),
                 source=A)
    for p in (other, here):
        assert store.guide(p, store.catalogue(p), NOW) is not None
    assert store.release_guides() == 2              # every week, the one in use's too
    assert store.guide_in(other, NOW) is None and not store.done(other, NOW)
    assert store.guide_in(here, NOW) is None and not store.done(here, NOW)
    assert store.ready(other) is not None           # the lists stay: only the weeks go
    assert len(store.guide(other, store.catalogue(other), NOW)) == 3
    assert len(reads) == 2                          # read again from disk


def test_a_week_read_while_the_weeks_are_let_go_is_not_kept(tmp_path):
    holder = {}
    store, reads = _disk_store(tmp_path, on_read=lambda: holder["store"].release_guides())
    holder["store"] = store
    plan = _plan()
    table = store.guide(plan, store.catalogue(plan), NOW)
    assert table is not None and len(table) == 3     # the worker still has it
    assert store.guide_in(plan, NOW) is None and not store.done(plan, NOW)


def test_what_was_read_of_a_source_removed_or_changed_goes_at_once(tmp_path):
    store, _reads = _disk_store(tmp_path)
    b, c = _plan(), _plan(source=sa.Source("c", "Third"))
    for p in (b, c):
        store.guide(p, store.catalogue(p), NOW)
    sig_c = ss.signature(c.playlist)
    store.keep({"c": sig_c})                         # b removed
    assert store.ready(b) is None and store.guide_in(b, NOW) is None
    assert store.ready(c) is not None and store.guide_in(c, NOW) is not None
    store.keep({"c": ss.signature(dict(c.playlist, server="http://new"))})   # c edited
    assert store.ready(c) is None and store.guide_in(c, NOW) is None


def test_a_weeks_descriptions_are_kept_once_as_they_are_matched():
    long = "Star Trek " + "x" * 400
    sched = _sched((NOW + H, NOW + 2 * H, "A", long),
                   (NOW + 3 * H, NOW + 4 * H, "B", long + "y"),
                   (NOW + 5 * H, NOW + 6 * H, "C", "STAR  trek"),
                   (NOW + 7 * H, NOW + 8 * H, "D", "Star Trek"))
    table = sa.GuideTable(A, [{"stream_id": 1, "name": "X"}], lambda ch: sched, NOW)
    assert len(table.fdescs) == 2                    # the same text once, the same cut once
    res = sa.search("trek", [], [table], now=NOW)
    assert sorted(h.title for h in res.hits["programmes"]) == ["A", "B", "C", "D"]


def test_the_xmltv_guide_loads_its_cache_without_a_download(tmp_path, monkeypatch):
    from dopeiptv.providers.epg import XmltvGuide
    g = XmltvGuide(None, cache_path=str(tmp_path / "epg_x.xml"))
    monkeypatch.setattr(g, "_download", lambda: (_ for _ in ()).throw(AssertionError("download")))
    assert g.load_cached() is False and g.schedule({"epg_channel_id": "c1"}) is None
    (tmp_path / "epg_x.xml").write_bytes(
        b'<tv><programme channel="c1" start="20991231200000 +0000" stop="20991231210000 +0000">'
        b"<title>Far</title></programme></tv>")
    g.PARSE_ELSEWHERE = False
    assert g.load_cached() is True
    assert g.schedule({"epg_channel_id": "c1"})[2] == ("Far",)


# -- a newer query ----------------------------------------------------------------------

def test_a_query_gives_up_once_a_newer_one_is_typed():
    latest = sa.Latest()
    gen = latest.bump()
    cats = [_cat(channels=[{"stream_id": i, "name": "Star"}]) for i in range(3)]
    assert sa.search("star", cats, now=NOW, latest=latest, gen=gen) is not None
    latest.bump()
    assert sa.search("star", cats, now=NOW, latest=latest, gen=gen) is None


def test_tens_of_thousands_of_channels_are_quick():
    chans = [{"stream_id": i, "name": f"Channel {i} {'Sport' if i % 7 else 'Vegas'} HD"}
             for i in range(40_000)]
    films = [{"stream_id": i, "name": f"Film {i} Star"} for i in range(30_000)]
    cats = [_cat(A, channels=chans[:20_000], movies=films),
            _cat(B, channels=chans[20_000:])]
    start = time.perf_counter()
    for q in ("vegas", "star", "channel 1999", "zzz"):
        sa.search(q, cats, now=NOW)
    assert (time.perf_counter() - start) / 4 < 0.5


def test_a_hit_from_another_source_asks_for_its_address_only_to_play():
    client = _Client()
    row = {"stream_id": 7, "name": "X"}
    assert client.asked == []
    assert ss.play_address(client, row) == "http://b/live/7.ts"
    assert client.asked == ["url:7"]

    class Box(_Client):
        def play_url(self, sid, purpose):
            return f"http://box/transcoded/{sid}?{purpose}"

    assert ss.play_address(Box(), row) == "http://box/transcoded/7?play"
    assert ss.play_address(client, {"name": "no id"}) == ""


def test_which_sources_are_searched():
    pls = [{"id": "a", "kind": "xtream"}, {"id": "l", "kind": "local"},
           {"id": "off", "disabled": True}, {"name": "no id"}, {"id": "b", "kind": "m3u"}]
    assert [p["id"] for p in ss.searchable(pls)] == ["a", "b"]
    assert ss.signature({"server": "x"}) != ss.signature({"server": "y"})
    assert ss.source_of({"id": "b", "server": "http://s"}).name == "http://s"
