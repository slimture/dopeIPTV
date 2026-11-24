"""Every source, its logic (core/every_source.py) and the sources' side
(services/search_sources.py's ``channels`` and ``now_table``): offered only
with two sources of one's own or more; a source's channels as its own lists
show them - none hidden, of a hidden category or of a locked one while the
lock is shut, renamed rows by their own names, for a kids profile nothing
for adults and only what a grown-up allows - read once a session, its live
lists alone (never its films or series), a portal's only as loaded (never
paged through), a source that does not answer said so; another source's
row carries no id of the source in use's and plays under the failover key;
the sections under their headings in the sources' order, filtered, a grid
without headings; another source's guide around now from its copy on disk
- never downloaded, never the window's - one guide at a time, kept."""
from __future__ import annotations

import threading
import time
from array import array

from dopeiptv.core import every_source as es
from dopeiptv.core import search_all as sa
from dopeiptv.core.stores import CategoryOverrides, ChannelOverrides
from dopeiptv.services import search_sources as ss
from dopeiptv.services.failover import Shown

NOW = 1_800_000_000
H = 3600
A = sa.Source("a", "Pirat")
B = sa.Source("b", "Nordic", "m3u")


class _Client:
    """A source's client, counting what it is asked."""
    paged_lists = False

    def __init__(self, live=(), cats=None):
        self.asked: list[str] = []
        self._live = [dict(r) for r in live]
        self._cats = cats or []

    def live_streams(self, cat=None):
        self.asked.append("live")
        return self._live

    def live_categories(self):
        self.asked.append("live_categories")
        return self._cats

    def vod_streams(self, cat=None):
        self.asked.append("vod")
        return []

    def vod_categories(self):
        self.asked.append("vod_categories")
        return []

    def series_list(self, cat=None):
        self.asked.append("series")
        return []

    def series_categories(self):
        self.asked.append("series_categories")
        return []

    def live_url(self, sid, fmt="ts"):
        self.asked.append(f"url:{sid}")
        return f"http://b/live/{sid}.{fmt}"


def _plan(shown=None, kids=False, allowed=None, key=("k",), source=B, client=None):
    return ss.Plan(source, {"id": source.id, "name": source.name, "kind": source.kind},
                   shown or {}, kids, allowed or {}, client=client, key=key)


def _overrides(tmp_path):
    from PyQt6.QtCore import QSettings
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    cats = CategoryOverrides(s, "category_overrides_b")
    chans = ChannelOverrides(s, "channel_overrides_b")
    cats.update("live", "hid", hidden=True)
    cats.update("live", "lock", locked=True)
    cats.update("live", "ren", name="XXX Adults")
    chans.update("live", "3", hidden=True)
    chans.update("live", "4", name="My own")
    return cats, chans


ROWS = [{"stream_id": 1, "name": "Hidden cat", "category_id": "hid"},
        {"stream_id": 2, "name": "Locked cat", "category_id": "lock"},
        {"stream_id": 3, "name": "Hidden by hand", "category_id": "ok"},
        {"stream_id": 4, "name": "Renamed", "category_id": "ok", "epg_channel_id": "e4"},
        {"stream_id": 5, "name": "Kids show", "category_id": "kids"},
        {"stream_id": 6, "name": "Adult renamed", "category_id": "ren"},
        {"stream_id": 7, "name": "Flagged", "category_id": "kids", "is_adult": 1},
        {"stream_id": 8, "name": "Elsewhere", "category_id": "nowhere"},
        {"name": "no id", "category_id": "ok"}]
CATS = [{"category_id": c, "category_name": n} for c, n in
        (("hid", "Hidden"), ("lock", "Locked"), ("ok", "News"), ("kids", "Kids"),
         ("ren", "Family"))]


def _ids(rows):
    return [r.get("stream_id") for r in rows]


# -- offered ---------------------------------------------------------------------------

def test_it_is_offered_only_with_two_sources_of_ones_own():
    a, b = {"id": "a", "kind": "xtream"}, {"id": "b", "kind": "m3u"}
    assert not es.offered([])
    assert not es.offered([a])
    assert es.offered([a, b])
    assert not es.offered([a, {"id": "l", "kind": "local"}])        # files of one's own
    assert not es.offered([a, {"id": "off", "disabled": True}])    # one turned off
    assert not es.offered([a, {"name": "no id"}, None])


# -- a source's channels ---------------------------------------------------------------

def test_a_sources_channels_are_read_once_its_live_lists_alone_as_its_lists_show_them(tmp_path):
    cats, chans = _overrides(tmp_path)
    client = _Client(ROWS, CATS)
    made = []
    store = ss.Sources(make_client=lambda pl: made.append(pl["id"]) or client,
                       guide_path=lambda pid: str(tmp_path / "none.xml"))
    shut = _plan({"live": Shown.of(cats, chans, True, "live")})
    cat = store.channels(shut)
    # Hidden category, locked one (the lock shut), hidden by hand, no id: out.
    assert _ids(cat.channels) == [4, 5, 6, 7, 8]
    assert cat.titles["channels"][0] == "My own"           # the user's own name
    assert store.channels(shut) is cat and store.channels_ready(shut) is cat
    # Its live lists alone - never its films or series - and once.
    assert client.asked == ["live", "live_categories"] and made == ["b"]
    # The lock opened (another key): the same rows filtered anew, nothing
    # asked of the source again.
    opened = store.channels(_plan({"live": Shown.of(cats, chans, False, "live")}, key=("open",)))
    assert _ids(opened.channels) == [2, 4, 5, 6, 7, 8]
    assert client.asked == ["live", "live_categories"]
    # A full catalogue (the Search page's) reads only what is missing - and
    # stands in for the channels from then on.
    full = store.catalogue(_plan({"live": Shown.of(cats, chans, True, "live")}, key=("full",)))
    assert client.asked.count("live") == 1 and "vod" in client.asked
    assert store.channels_ready(_plan(key=("full",))) is full
    # Nothing read opens a stream.
    assert not any(a.startswith("url:") for a in client.asked)


def test_a_kids_profile_sees_nothing_for_adults_and_only_what_a_grown_up_allows(tmp_path):
    cats, chans = _overrides(tmp_path)
    store = ss.Sources(make_client=lambda pl: _Client(ROWS, CATS),
                       guide_path=lambda pid: str(tmp_path / "none.xml"))
    shown = {"live": Shown.of(cats, chans, True, "live")}
    kids = store.channels(_plan(shown, kids=True, key=("kids",)))
    # No category named for adults (by the user's name too), none flagged,
    # none of a category the source does not list.
    assert _ids(kids.channels) == [4, 5]
    allowed = store.channels(_plan(shown, kids=True, allowed={"live": frozenset({"kids"})},
                                   key=("allowed",)))
    assert _ids(allowed.channels) == [5]
    nothing = store.channels(_plan(shown, kids=True, allowed={"live": frozenset()},
                                   key=("nothing",)))
    assert nothing.channels == []


class _Portal(_Client):
    paged_lists = True

    def loaded(self, kind):
        self.asked.append(f"loaded:{kind}")
        return [{"stream_id": 1, "name": "Portal One", "category_id": "1"}], [], False

    def live_streams(self, cat=None):
        raise AssertionError("a portal's lists were paged through")


def test_a_portal_only_as_far_as_its_lists_were_loaded_and_a_source_down_said_so(tmp_path):
    portal = _Portal()
    store = ss.Sources(make_client=lambda pl: portal,
                       guide_path=lambda pid: str(tmp_path / "none.xml"))
    cat = store.channels(_plan())
    assert portal.asked == ["loaded:live"] and cat.partial == sa.PAGED
    assert _ids(cat.channels) == [1]

    class Down(_Client):
        def live_streams(self, cat=None):
            raise OSError("no route")

    down = ss.Sources(make_client=lambda pl: Down(),
                      guide_path=lambda pid: str(tmp_path / "none.xml"))
    assert down.channels(_plan()).partial == sa.FAILED
    broken = ss.Sources(make_client=lambda pl: (_ for _ in ()).throw(ValueError("bad")),
                        guide_path=lambda pid: str(tmp_path / "none.xml"))
    gone = broken.channels(_plan())
    assert gone.partial == sa.FAILED and gone.channels == []


def test_what_was_read_of_a_source_changed_or_removed_goes(tmp_path):
    store = ss.Sources(make_client=lambda pl: _Client(ROWS[3:5], CATS),
                       guide_path=lambda pid: str(tmp_path / "none.xml"))
    plan = _plan()
    store.channels(plan)
    store.keep({})                                     # removed
    assert store.channels_ready(plan) is None
    store.channels(plan)
    store.drop("b")                                    # its lists refreshed
    assert store.channels_ready(plan) is None


# -- rows and sections ----------------------------------------------------------------

def test_another_sources_row_carries_no_id_of_the_source_in_uses_and_plays_as_failover():
    row = {"stream_id": 8, "name": "Vegas", "stream_icon": "http://logo/8.png",
           "epg_channel_id": "v", "category_id": "n", "num": 3, "tv_archive": 1,
           "direct_source": "http://b/secret/8.ts"}
    shown = es.wrap(B, row, "Vegas Nordic")
    assert "stream_id" not in shown and "series_id" not in shown and "id" not in shown
    assert "direct_source" not in shown and "tv_archive" not in shown
    assert shown["name"] == "Vegas Nordic" and shown["stream_icon"] == "http://logo/8.png"
    assert shown["_key"] == es.play_key("b", 8) == "failover:b:8"
    assert es.is_other(shown) and not es.is_other(row) and not es.is_other(None)


def test_sections_under_their_headings_in_the_sources_order():
    mine = sa.Catalogue(A, [{"stream_id": 1, "name": "SVT1"}, {"stream_id": 2, "name": "TV4"}])
    other = sa.Catalogue(B, [{"stream_id": 8, "name": "Vegas"}, {"stream_id": 9, "name": "SVT2"},
                             {"name": "no id"}], renamed={"live": {"9": "SVT 2 HD"}})
    down = sa.Catalogue(sa.Source("c", "Down"), partial=sa.FAILED)
    empty = sa.Catalogue(sa.Source("d", "Films only"))
    secs = [es.section_of(mine, True), es.section_of(other, False), es.section_of(down, False),
            es.section_of(empty, False)]
    assert secs[0].rows[0] is mine.channels[0]              # the source in use's own rows
    assert [r["name"] for r in secs[1].rows] == ["Vegas", "SVT 2 HD"]
    assert secs[1].originals["failover:b:9"] is other.channels[1]

    def words(sec, n):
        return f"{sec.source.name} {n}"

    rows, blocks, total = es.arrange(secs, lambda s, r: r, words)
    heads = [r["_header"] for r in rows if r.get("_header")]
    assert heads == ["Pirat 2", "Nordic 2", "Down 0"]       # none for a source with none
    assert all(r.get("_tv_head") for r in rows if r.get("_header"))
    assert total == 4 and blocks == {"a": (0, 3), "b": (3, 3), "c": (6, 1)}
    # The filter: a source with nothing that matches is left out, heading too.
    rows, blocks, total = es.arrange(
        secs, lambda s, r: [x for x in r if "svt" in x["name"].lower()], words, filtered=True)
    assert [r.get("_header") or r["name"] for r in rows] == ["Pirat 1", "SVT1", "Nordic 1",
                                                             "SVT 2 HD"]
    assert total == 2 and set(blocks) == {"a", "b"}
    # A grid of tiles: no headings.
    rows, _b, total = es.arrange(secs, lambda s, r: r, words, headings=False)
    assert not any(r.get("_header") for r in rows) and total == len(rows) == 4


# -- another source's guide around now ----------------------------------------------------

def _sched(*progs):
    return (array("q", [p[0] for p in progs]), array("q", [p[1] for p in progs]),
            tuple(p[2] for p in progs), tuple("desc" for _ in progs))


SCHED = _sched((NOW - 2 * H, NOW - H, "Over"), (NOW - 600, NOW + 1200, "On now"),
               (NOW + 1200, NOW + 3000, "Next"), (NOW + 40 * H, NOW + 41 * H, "Far"))


def test_a_table_around_now_by_the_rows_keys():
    chans = [{"stream_id": 8, "epg_channel_id": "v"}, {"stream_id": 9, "epg_channel_id": "v"},
             {"stream_id": 10, "epg_channel_id": "none"}]
    table = es.NowTable(chans, lambda ch: es.play_key("b", ch["stream_id"]),
                        lambda ch: SCHED if ch.get("epg_channel_id") == "v" else None, NOW)
    assert len(table) == 2 and table.is_loaded() and not table.cut
    row = es.wrap(B, chans[0], "Vegas")
    starts, stops, titles, descs = table.schedule(row)
    assert list(titles) == ["On now", "Next"] and len(descs) == 2     # no description kept
    assert table.schedule(es.wrap(B, chans[1], "")) is table.schedule(row)   # one guide id: shared
    assert table.schedule(es.wrap(B, chans[2], "")) is None
    assert table.schedule({"stream_id": 8, "name": "Vegas"}) is None  # no row of the source in use
    from dopeiptv.ui.tv_place import guide_now_next
    nn = guide_now_next(table, row, now=NOW)
    assert nn.title == "On now" and nn.next_title == "Next"
    got = table.listings(row, NOW)
    assert [e["title"] for e in got] == ["On now", "Next"] and all(e["_plain"] for e in got)
    # At most so many airings: the channels past them get none, said.
    def own(ch):            # a schedule of its own per channel: two airings each
        return _sched((NOW - 600, NOW + 1200, "On now"), (NOW + 1200, NOW + 3000, "Next"))

    small = es.NowTable(chans, lambda ch: es.play_key("b", ch["stream_id"]), own, NOW, limit=3)
    assert small.cut and len(small) == 1


class _Guide:
    """A guide on disk: read with load_cached, never downloaded."""
    opened: list[str] = []
    busy = 0
    most = 0

    def __init__(self, path):
        _Guide.opened.append(path)

    def load_cached(self):
        _Guide.busy += 1
        _Guide.most = max(_Guide.most, _Guide.busy)
        time.sleep(0.02)
        _Guide.busy -= 1
        return True

    def schedule(self, ch):
        return SCHED if ch.get("epg_channel_id") == "v" else None

    def ensure_loaded(self, force=False):
        raise AssertionError("a guide was downloaded")

    load = refresh = ensure_loaded


def test_another_sources_guide_is_read_from_disk_one_at_a_time_and_kept(tmp_path):
    for pid in ("b", "c"):
        (tmp_path / f"epg_{pid}.xml").write_bytes(b"x")
    store = ss.Sources(make_client=lambda pl: _Client([{"stream_id": 8, "name": "Vegas",
                                                        "epg_channel_id": "v"}]),
                       guide_path=lambda pid: str(tmp_path / f"epg_{pid}.xml"), open_guide=_Guide)
    plans = [_plan(), _plan(source=sa.Source("c", "Third"))]
    threads = [threading.Thread(target=lambda p=p: store.now_table(p, store.channels(p), NOW))
               for p in plans]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert _Guide.most == 1, "two guides were in memory at once"
    assert len(_Guide.opened) == 2
    table = store.now_in(plans[0], NOW)
    assert table is not None and len(table) == 1 and store.now_looked_for(plans[0], NOW)
    assert table.schedule({"_key": "failover:b:8"}) is not None
    assert store.now_table(plans[0], store.channels(plans[0]), NOW) is table
    assert len(_Guide.opened) == 2                         # kept, not read again
    # Read again once it has been a while.
    assert store.now_in(plans[0], NOW + ss.GUIDE_FRESH_SECS) is None
    # No guide on this computer: none, and nothing downloaded for it.
    none = _plan(source=sa.Source("d", "No guide"))
    assert store.now_table(none, store.channels(none), NOW) is None
    assert store.now_looked_for(none, NOW) and len(_Guide.opened) == 2


def test_the_real_guide_is_read_from_its_copy_on_disk_without_a_download(tmp_path, monkeypatch):
    from dopeiptv.providers.epg import XmltvGuide
    monkeypatch.setattr(XmltvGuide, "PARSE_ELSEWHERE", False)
    monkeypatch.setattr(XmltvGuide, "_download",
                        lambda self: (_ for _ in ()).throw(AssertionError("a download")))
    path = tmp_path / "epg_b.xml"
    stamp = time.strftime("%Y%m%d%H%M%S +0000", time.gmtime(time.time() - 600))
    end = time.strftime("%Y%m%d%H%M%S +0000", time.gmtime(time.time() + 1800))
    path.write_bytes(f'<tv><programme channel="v" start="{stamp}" stop="{end}">'
                     f"<title>Vegas Live</title></programme></tv>".encode())
    store = ss.Sources(make_client=lambda pl: _Client([{"stream_id": 8, "name": "Vegas",
                                                        "epg_channel_id": "v"}]),
                       guide_path=lambda pid: str(path))
    plan = _plan()
    table = store.now_table(plan, store.channels(plan))
    assert table is not None
    from dopeiptv.ui.tv_place import guide_now_next
    nn = guide_now_next(table, {"_key": "failover:b:8"})
    assert nn is not None and nn.title == "Vegas Live"


def test_tens_of_thousands_of_channels_are_laid_out_quickly():
    chans = [{"stream_id": i, "name": f"Channel {i}", "category_id": "1"} for i in range(30_000)]
    cats = [sa.Catalogue(A, chans[:15_000]), sa.Catalogue(B, chans[15_000:])]
    start = time.perf_counter()
    secs = [es.section_of(cats[0], True), es.section_of(cats[1], False)]
    rows, _b, total = es.arrange(secs, lambda s, r: [x for x in r if "1" in x["name"]],
                                 lambda s, n: s.source.name, filtered=True)
    assert total == len(rows) - 2 and time.perf_counter() - start < 1.0


# -- the row picked's programmes with their words; another source's own shift -------------

def test_a_rows_programmes_with_their_words_cut_short():
    long = "x" * (es.TEXT_MAX + 500)
    sched = (array("q", [NOW - 600, NOW + 1200, NOW + 3000]),
             array("q", [NOW + 1200, NOW + 3000, NOW + 4000]),
             ("On now", "Next", "Later"), ("About tonight.", long, ""))
    got = es.listings_of(sched, NOW)
    assert [(e["title"], e["description"][:14]) for e in got] == [
        ("On now", "About tonight."), ("Next", "x" * 14), ("Later", "")]
    assert len(got[1]["description"]) == es.TEXT_MAX and all(e["_plain"] for e in got)
    assert es.listings_of(sched, NOW, limit=1)[0]["title"] == "On now"
    assert es.listings_of(None, NOW) == [] and es.listings_of(sched, NOW + 9 * H) == []
    # The list's own table keeps none of them.
    table = es.NowTable([{"stream_id": 8}], lambda ch: es.play_key("b", 8), lambda ch: sched, NOW)
    assert [e["description"] for e in table.listings({"_key": "failover:b:8"}, NOW)] \
        == ["", "", ""]


class _WordsGuide(_Guide):
    rows: list = []

    def schedule(self, ch):
        _WordsGuide.rows.append(dict(ch))
        return SCHED if ch.get("epg_channel_id") == "v" else None


def test_only_the_row_picked_has_its_words_read_from_disk_and_a_few_kept(tmp_path):
    (tmp_path / "epg_b.xml").write_bytes(b"x")
    _Guide.opened.clear()
    _WordsGuide.rows.clear()
    store = ss.Sources(make_client=lambda pl: _Client(),
                       guide_path=lambda pid: str(tmp_path / f"epg_{pid}.xml"),
                       open_guide=_WordsGuide)
    row = {"stream_id": 8, "name": "Vegas", "epg_channel_id": "v"}
    key = es.play_key("b", 8)
    assert store.texts_in("b", key, 0, NOW) is None        # not read: none
    got = store.texts("b", row, key, 0, NOW)
    assert [(e["title"], e["description"]) for e in got] == [
        ("On now", "desc"), ("Next", "desc"), ("Far", "desc")]
    assert _WordsGuide.rows == [row]                         # that row's alone was looked up
    assert store.texts_in("b", key, 0, NOW + 60) == got
    assert store.texts("b", row, key, 0, NOW + 60) is got and len(_Guide.opened) == 1   # kept
    # Its guide an hour behind (its own shift): what is on in its time -
    # "On now" began ten minutes before NOW and is on still an hour later
    # by that guide's clock.
    late = store.texts("b", row, key, 60, NOW + H)
    assert [e["title"] for e in late][:1] == ["On now"] and len(_Guide.opened) == 2
    assert store.texts_in("b", key, 0, NOW + H) is None      # another offset: read anew
    # The last few rows picked kept, the oldest let go; read again after a while.
    for i in range(es.TEXTS_KEPT):
        store.texts("b", {"stream_id": 100 + i}, es.play_key("b", 100 + i), 0, NOW)
    assert store.texts_in("b", key, 60, NOW + H) is None
    assert store.texts_in("b", es.play_key("b", 100), 0, NOW + ss.GUIDE_FRESH_SECS) is None
    # No guide on this computer: nothing, and nothing downloaded.
    assert store.texts("c", row, es.play_key("c", 8), 0, NOW) == []
    # A source let go: what was read of it goes.
    store.keep({})
    assert store.texts_in("b", es.play_key("b", 100), 0, NOW) is None


def test_the_guide_around_now_keeps_what_is_on_in_a_shifted_sources_own_time(tmp_path):
    (tmp_path / "epg_b.xml").write_bytes(b"x")
    store = ss.Sources(make_client=lambda pl: _Client([{"stream_id": 8, "name": "Vegas",
                                                        "epg_channel_id": "v"}]),
                       guide_path=lambda pid: str(tmp_path / f"epg_{pid}.xml"), open_guide=_Guide)
    plan = _plan()
    cat = store.channels(plan)
    # "Over" ran from two hours to one hour ago: on now in a guide that
    # runs two hours behind.
    plain = store.now_table(plan, cat, NOW)
    assert "Over" not in plain.schedule({"_key": "failover:b:8"})[2]
    assert not store.now_looked_for(plan, NOW, behind=2 * H)     # another shift: not yet
    behind = store.now_table(plan, cat, NOW, behind=2 * H)
    assert behind is not plain and "Over" in behind.schedule({"_key": "failover:b:8"})[2]
    assert store.now_looked_for(plan, NOW, behind=2 * H)
