"""The parental lock on what the stores kept: History, Continue watching,
the TV place's Recently watched, the phone's Recent and Mine leave a locked
category's channel or film out while the lock is shut, as the lists do.

History kept no category for a channel or a film, so a channel watched once
with the PIN came back - and played - from all of them with the lock shut.
A row kept without its category (from before) is held back until the
source's catalogue said it, then shown or left out."""
from __future__ import annotations

from datetime import datetime

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from dopeiptv.core import privacy  # noqa: E402
from dopeiptv.core.stores import (  # noqa: E402
    CategoryOverrides, ChannelOverrides, HistoryStore, ParentalControl, catalogue_cats,
    kept_place, lock_hides,
)
from dopeiptv.ui import mw_lists, mw_mitt  # noqa: E402
from dopeiptv.ui.main_window import MainWindow  # noqa: E402
from dopeiptv.ui.mw_cast import _CastMixin  # noqa: E402
from dopeiptv.ui.mw_lists import _ListsMixin  # noqa: E402
from dopeiptv.ui.mw_media import _MediaServerMixin  # noqa: E402
from dopeiptv.ui.mw_phone_browse import _PhoneBrowseMixin  # noqa: E402
from dopeiptv.ui.mw_place_tv import _PlaceTvMixin  # noqa: E402


class Settings:
    """QSettings' few calls, kept in a dict."""

    def __init__(self) -> None:
        self.d: dict = {}

    def value(self, key, default=None):
        return self.d.get(key, default)

    def setValue(self, key, value) -> None:  # noqa: N802
        self.d[key] = value

    def remove(self, key) -> None:
        self.d.pop(key, None)

    def contains(self, key) -> bool:
        return key in self.d

    def allKeys(self):  # noqa: N802
        return list(self.d)


ADULT = {"stream_id": 700, "name": "Adult One", "category_id": "7", "num": 70}
NEWS = {"stream_id": 100, "name": "News One", "category_id": "1", "num": 1}
FILM = {"stream_id": 900, "name": "Adult Film", "category_id": "9"}


class Client:
    def __init__(self) -> None:
        self.asked: list[str] = []

    def live_streams(self, _cat=None):
        self.asked.append("live")
        return [NEWS, ADULT]

    def vod_streams(self, _cat=None):
        self.asked.append("vod")
        return [FILM]


def _run_now(_pool, fn, done, fail=None):
    try:
        got = fn()
    except Exception as e:  # noqa: BLE001 - as the pool hands it to fail
        fail(str(e))
        return
    done(got)


@pytest.fixture(autouse=True)
def _inline_pool(monkeypatch):
    monkeypatch.setattr(mw_lists, "run_async", _run_now)
    privacy.set_private(False)


class Win(_ListsMixin, _PlaceTvMixin, _PhoneBrowseMixin):
    _item_key = staticmethod(MainWindow._item_key)

    def __init__(self, settings: Settings, unlocked: bool = False) -> None:
        self.settings = settings
        self.client = Client()
        self.pool = None
        self.overrides = CategoryOverrides(settings, "category_overrides_p1")
        self.channel_ov = ChannelOverrides(settings, "channel_overrides_p1")
        self.parental = ParentalControl(settings)
        self.parental.session_unlocked = unlocked
        self.history = HistoryStore(settings, "history_p1")
        self.shown_again: list[str] = []

    def _profile_kids(self) -> bool:
        return False

    def _kids_keep(self, _mode):
        return None

    def _held_rows_in(self, reason: str) -> None:
        self.shown_again.append(reason)


def _watched(settings: Settings, legacy: bool = False) -> None:
    """Yesterday, with the PIN: the locked channel and film played, and the
    news channel; History kept them as _start_playback hands them over (or,
    *legacy*, as it did before: without their category)."""
    h = HistoryStore(settings, "history_p1")
    for kind, row in (("live", NEWS), ("live", ADULT), ("movie", FILM)):
        extra = _CastMixin._history_extra(None, kind, row, row["name"])
        if legacy:
            extra = {k: v for k, v in (extra or {}).items() if k != "category_id"} or None
        h.add(f"http://provider.invalid/{row['stream_id']}", row["name"], None,
              row["stream_id"], kind, extra=extra)


@pytest.fixture()
def settings():
    s = Settings()
    o = CategoryOverrides(s, "category_overrides_p1")
    o.update("live", "7", locked=True)
    o.update("vod", "9", locked=True)
    return s


def test_history_keeps_a_channels_and_a_films_category():
    assert _CastMixin._history_extra(None, "live", ADULT, "x")["category_id"] == "7"
    assert _CastMixin._history_extra(None, "movie", FILM, "x") == {"category_id": "9"}


def test_a_kept_rows_place_in_its_catalogue():
    assert kept_place({"_kind": "live", "stream_id": 7, "category_id": 3}) == ("live", "3", "7")
    assert kept_place({"_kind": "movie", "_key": 9}) == ("vod", "", "9")
    assert kept_place({"_kind": "vod", "stream_id": 9, "category_id": "2"}) == ("vod", "2", "9")
    ep = {"_kind": "episode", "id": 5, "_series_ctx": {"series_id": 4, "category_id": 8}}
    assert kept_place(ep) == ("series", "8", "4")
    assert kept_place({"_kind": "local", "_key": "/a.mkv"}) is None
    assert kept_place({"_kind": "radio"}) is None


def test_lock_hides_a_locked_categorys_rows_and_holds_back_the_unknown():
    shut = {"live": frozenset({"7"}), "series": frozenset({"8"})}
    assert lock_hides({"_kind": "live", "stream_id": 1, "category_id": "7"}, shut) is True
    assert lock_hides({"_kind": "live", "stream_id": 1, "category_id": "1"}, shut) is False
    ep = {"_kind": "episode", "_series_ctx": {"series_id": 4, "category_id": 8}}
    assert lock_hides(ep, shut) is True
    # Nothing locked of its kind: shown, whatever it says.
    assert lock_hides({"_kind": "movie", "_key": 9}, shut) is False
    # Its category not said: held back until the catalogue told it.
    legacy = {"_kind": "live", "_key": 700}
    assert lock_hides(legacy, shut) is None
    assert lock_hides(legacy, shut, {"live": {"700": "7"}}) is True
    assert lock_hides(legacy, shut, {"live": {"700": "1"}}) is False
    assert lock_hides(legacy, shut, {"live": {"700": ""}}) is False   # not listed at all


def test_the_catalogue_tells_only_what_was_asked():
    c = Client()
    assert catalogue_cats(c, "live", ["700", "5"]) == {"700": "7", "5": ""}

    class Portal:
        paged_lists = True

        def loaded(self, kind):
            return ([ADULT] if kind == "live" else []), [], False

        def live_streams(self, _cat=None):
            raise AssertionError("a portal is never paged through")
    assert catalogue_cats(Portal(), "live", ["700"]) == {"700": "7"}


def test_locked_ids():
    s = Settings()
    o = CategoryOverrides(s, "c")
    o.update("live", 3, locked=True)
    o.update("live", 4, hidden=True)
    assert o.locked_ids("live") == frozenset({"3"})
    assert o.locked_ids("vod") == frozenset()


def test_recently_watched_leaves_a_locked_channel_out(settings):
    _watched(settings)
    w = Win(settings)
    assert [r["name"] for r in w._place_tv_recent_rows()] == ["News One"]
    assert w.client.asked == []                        # each said its category
    w = Win(settings, unlocked=True)
    assert [r["name"] for r in w._place_tv_recent_rows()] == ["Adult One", "News One"]


def test_a_row_kept_without_its_category_is_held_back_then_told(settings):
    _watched(settings, legacy=True)
    w = Win(settings)
    # The catalogue read (here at once) says which is locked: the news is
    # shown again, the adult channel stays out.
    rows = w._place_tv_recent_rows()
    assert w.client.asked == ["live"] and w.shown_again == ["lock"]
    assert [r["name"] for r in rows] == []              # held back meanwhile
    assert [r["name"] for r in w._place_tv_recent_rows()] == ["News One"]
    assert w.client.asked == ["live"]                   # asked once
    # A list that showed the channel this session already said it: shown at
    # once, only the other one asked about.
    w2 = Win(settings)
    w2._place_tv_learn([NEWS])
    assert [r["name"] for r in w2._place_tv_recent_rows()] == ["News One"]
    assert w2.client.asked == ["live"]


def test_a_catalogue_that_cannot_be_read_holds_them_back_and_is_asked_later(settings, monkeypatch):
    _watched(settings, legacy=True)
    w = Win(settings)
    calls = []

    def down(_cat=None):
        calls.append(1)
        raise OSError("provider down")
    w.client.live_streams = down
    assert w._place_tv_recent_rows() == []
    assert w._place_tv_recent_rows() == [] and len(calls) == 1     # not asked at every look
    now = mw_lists.time.monotonic()
    monkeypatch.setattr(mw_lists.time, "monotonic", lambda: now + mw_lists.LOCK_RETRY_S + 1)
    w.client.live_streams = Client().live_streams
    w._place_tv_recent_rows()
    assert [r["name"] for r in w._place_tv_recent_rows()] == ["News One"]


def test_nothing_locked_reads_nothing_and_changes_nothing():
    s = Settings()
    _watched(s, legacy=True)
    w = Win(s)
    rows = list(w.history.entries)
    assert w._lock_filter(rows) == rows
    assert w.client.asked == []


def test_the_phones_history_leaves_a_locked_title_out(settings):
    _watched(settings)
    w = Win(settings)
    shown = w._pb_history_shown(w.history.items())
    assert [h["name"] for h in shown] == ["News One"]
    w = Win(settings, unlocked=True)
    assert len(w._pb_history_shown(w.history.items())) == 3


def test_the_list_history_leaves_a_locked_title_out(settings):
    _watched(settings)
    w = Win(settings)
    assert [h["name"] for h in w._lock_filter(w.history.items())] == ["News One"]


class ContinueWin(_ListsMixin, _MediaServerMixin):
    def __init__(self, settings: Settings, rows) -> None:
        self.client = Client()
        self.pool = None
        self.overrides = CategoryOverrides(settings, "category_overrides_p1")
        self.parental = ParentalControl(settings)
        self.resume = type("R", (), {"continue_watching": lambda _s: list(rows)})()

    def _ms_client(self):
        return None

    def _profile_kids(self) -> bool:
        return False


def test_continue_watching_leaves_a_locked_film_out(settings):
    rows = [{**FILM, "_kind": "vod", "_ts": 2},
            {"_kind": "vod", "stream_id": 1, "category_id": "1", "_ts": 1}]
    w = ContinueWin(settings, rows)
    assert [r["stream_id"] for r in w._continue_rows()] == [1]
    w.parental.session_unlocked = True
    assert [r["stream_id"] for r in w._continue_rows()] == [900, 1]


def test_mine_leaves_another_sources_locked_rows_out(settings):
    _watched(settings)
    cont =[{**FILM, "_kind": "vod", "_resume_pos": 600.0, "_resume_dur": 5400.0, "_ts": 1}]

    def build(shut):
        src = mw_mitt._Source("p1", "Provider", False, raw={"history_p1": settings.d["history_p1"]},
                              shut=shut)
        snap = mw_mitt._Snap(("me", False, False, "p0", ("p0", "p1")), [src],
                             [], [], [], [], [], [], None, datetime.now())
        src.cont = cont
        return mw_mitt._build(snap)

    w = Win(settings)
    data = build(w._lock_shut())
    assert [e.title for e in data.history] == ["News One"]
    assert data.cont == []
    data = build({})
    assert sorted(e.title for e in data.history) == ["Adult Film", "Adult One", "News One"]
    assert [e.title for e in data.cont] == ["Adult Film"]
    # Kept without its category there: left out while its kind is locked.
    assert mw_mitt._lock_keeps({"_kind": "live", "_key": 5}, {"live": frozenset({"7"})}) is False
    assert mw_mitt._lock_keeps({"_kind": "live", "_key": 5}, {"vod": frozenset({"9"})}) is True


class PhonePlayWin(Win):
    """The phone's play of a row it listed, the playback itself left out."""

    def __init__(self, settings: Settings, unlocked: bool = False, kids: bool = False) -> None:
        super().__init__(settings, unlocked)
        self.kids = kids
        self.played: list = []

    def _profile_kids(self) -> bool:
        return self.kids

    def _kids_keep(self, _mode):                   # the news is not for this kid
        return (lambda it: str(it.get("category_id")) != "1") if self.kids else None

    def _kids_allows(self, it, mode="live") -> bool:
        keep = self._kids_keep(mode)
        return keep is None or keep(it)

    def _profile_refuse_locked(self) -> None:
        pass

    def _phone_asked(self) -> None:
        pass

    def _come_forward(self) -> None:
        pass

    def _pb_play_continue(self, row) -> bool:
        self.played.append(("continue", row["stream_id"]))
        return True

    def _pb_play_history(self, it) -> bool:
        self.played.append(("history", it["_key"]))
        return True


def test_a_phones_row_is_asked_again_when_it_plays(settings, monkeypatch):
    from dopeiptv.core import profiles
    _watched(settings)
    w = PhonePlayWin(settings, unlocked=True)
    adult = next(h for h in w.history.items() if h["_key"] == 700)
    film = {**FILM, "_kind": "vod", "_resume_pos": 60.0}
    refs = w._pb()[1]
    w._pb_era_kept()                               # the phone lists Recent (_pb_snapshot)
    h_ref = refs.put("history", "live:700", adult)
    c_ref = refs.put("continue", "vod:900", film)
    assert w._pb_play("history", adult) == {"ok": True}
    # The lock shut since it was listed: neither plays now.
    w.parental.lock_session()
    for kind, value in (("history", adult), ("continue", film)):
        with pytest.raises(ValueError):
            w._pb_play(kind, value)
    assert w.played == [("history", 700)]
    # A kids profile now: the ids listed for the grown-up are forgotten.
    monkeypatch.setattr(profiles, "active_id", lambda: "kid")
    w.parental.session_unlocked = True
    news = next(h for h in w.history.items() if h["_key"] == 100)
    with pytest.raises(ValueError):
        w._pb_play("history", news)
    assert refs.get(h_ref) is None and refs.get(c_ref) is None
    w.kids = True                                  # and its limits asked again
    with pytest.raises(ValueError):
        w._pb_play("history", news)
    w.kids = False
    assert w._pb_play("history", news) == {"ok": True}


def test_the_sidebar_search_names_a_locked_category_without_its_channels(settings):
    from PyQt6.QtWidgets import QApplication, QListWidget

    from dopeiptv.i18n import set_language, tr
    from dopeiptv.ui.mw_search import _SearchMixin
    app = QApplication.instance() or QApplication([])
    set_language("en")

    class SearchWindow(_SearchMixin):
        def __init__(self, unlocked: bool) -> None:
            self.mode = "live"
            self._raw_categories = [{"category_id": "1", "category_name": "News"},
                                    {"category_id": "7", "category_name": "XXX Adults 18+"}]
            self.overrides = CategoryOverrides(settings, "category_overrides_p1")
            self.parental = ParentalControl(settings)
            self.parental.session_unlocked = unlocked
            self.cat_list = QListWidget()

        def _profile_kids(self) -> bool:
            return False

    idx = {"7": [("Adult One", "adult one"), ("Adult Two", "adult two")],
           "1": [("News One", "news one")]}

    def rows(word, unlocked=False):
        w = SearchWindow(unlocked)
        w._render_cat_search(word, idx)
        return [w.cat_list.item(i).text() for i in range(w.cat_list.count())]

    assert rows("adult one") == [tr("cat_search_none")]      # its channels not searched
    assert rows("xxx") == ["XXX Adults 18+  " + tr("cat_locked_suffix")]
    assert rows("adult") == ["XXX Adults 18+  " + tr("cat_locked_suffix")]
    assert rows("adult", unlocked=True) == ["XXX Adults 18+  ·  Adult One, Adult Two"]
    assert rows("news") == ["News  ·  News One"]
    assert app is not None
