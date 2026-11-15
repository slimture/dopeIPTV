"""Teletext and DVB subtitles as subtitle tracks (core/teletext_subs.py):
which entries a track gives the menu, a page picked by language, the value
mpv's teletext-page gets on an old and a new mpv, and the page kept per
channel."""
from __future__ import annotations

from dopeiptv.core import teletext as ttx
from dopeiptv.core import teletext_subs as ts


class Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's name
        self[key] = value


TTX = {"id": 3, "type": "sub", "codec": "dvb_teletext", "lang": "swe,swe,fin", "src-id": 0x102}
DVB = {"id": 4, "type": "sub", "codec": "dvb_subtitle", "lang": "fin", "src-id": 0x103}
SRT = {"id": 5, "type": "sub", "codec": "subrip", "lang": "eng"}


def _streams() -> dict[int, ttx.Stream]:
    s = ttx.Stream(0x102, 6, "teletext", teletext=[
        ttx.TeletextEntry("swe", ttx.TYPE_INITIAL, 0x100),
        ttx.TeletextEntry("swe", ttx.TYPE_SUBTITLE, 0x199),
        ttx.TeletextEntry("swe", ttx.TYPE_HOH, 0x198),
        ttx.TeletextEntry("fin", ttx.TYPE_SUBTITLE, 0x333)])
    d = ttx.Stream(0x103, 6, "dvbsub", subtitling=[ttx.SubtitlingEntry("fin", 0x20, 1, 1)])
    return {0x102: s, 0x103: d}


def test_kinds_and_languages():
    assert ts.track_kind(TTX) == ts.TELETEXT and ts.track_kind(DVB) == ts.DVBSUB
    assert ts.track_kind(SRT) == ""
    assert ts.track_langs(TTX) == ["swe", "fin"]
    assert ts.track_langs({"lang": "und,,eng"}) == ["eng"]


def test_a_teletext_track_offers_its_subtitle_pages_when_the_pmt_is_known():
    got = ts.choices(TTX, _streams())
    assert [(c.lang, c.page, c.hoh) for c in got] == [
        ("swe", 0x199, False), ("swe", 0x198, True), ("fin", 0x333, False)]
    assert all(c.track == 3 and c.pid == 0x102 for c in got)
    # Without the PMT: one entry per language, no page.
    bare = ts.choices(TTX)
    assert [(c.lang, c.page) for c in bare] == [("swe", None), ("fin", None)]
    dvb = ts.choices(DVB, _streams())
    assert dvb == [ts.Choice(4, ts.DVBSUB, "fin", None, True, 0x103)]
    assert ts.choices(SRT) == []
    assert len(ts.all_choices([TTX, DVB, SRT, {"type": "video"}], _streams())) == 4


def test_a_language_picks_its_page_for_everyone_first():
    options = ts.all_choices([TTX, DVB], _streams())
    assert ts.pick(options, ["swe"]).page == 0x199
    assert ts.pick(options, ["sv"]).page == 0x199          # however it is spelt
    assert ts.pick(options, ["eng", "fin"]).page == 0x333
    assert ts.pick(options, ["eng"]) is None
    assert ts.pick(options, ["", "fi"]).page == 0x333


def test_teletext_page_values_for_old_and_new_mpv():
    assert ts.mpv_version("mpv 0.37.0") == (0, 37)
    assert ts.mpv_version("mpv v0.38.0-dev-g1234") == (0, 38) and ts.mpv_version(None) == (0, 0)
    known = ts.Choice(1, ts.TELETEXT, "swe", 0x199)
    assert ts.mpv_page(known, (0, 37)) == 199 and ts.mpv_page(known, (0, 39)) == 199
    unknown = ts.Choice(1, ts.TELETEXT, "fin")
    assert ts.mpv_page(unknown, (0, 38)) == 0               # any subtitle page
    assert ts.mpv_page(unknown, (0, 37)) == 333             # by custom
    assert ts.mpv_page(ts.Choice(1, ts.TELETEXT, "sv"), (0, 34)) == 199
    assert ts.mpv_page(ts.Choice(1, ts.TELETEXT, "xx"), (0, 37)) == ts.DEFAULT_PAGE
    # Before a stream opens: never mpv 0.37's index page 100.
    assert ts.default_page(["swe", "eng"], (0, 37)) == 199
    assert ts.default_page([], (0, 37)) == ts.DEFAULT_PAGE
    assert ts.default_page(["swe"], (0, 40)) == 0


def test_the_page_is_kept_per_channel():
    s = Settings()
    store = ts.PageStore(s, cap=3)
    options = ts.all_choices([TTX], _streams())
    assert store.find("a:1", options) is None
    store.put("a:1", options[2])                             # Finnish 333
    assert store.get("a:1") == (0x333, "", 0x102)
    assert store.find("a:1", options) == options[2]
    # The PMT not known next time: the track plays the page kept.
    found = store.find("a:1", ts.all_choices([TTX]))
    assert found is not None and found.page == 0x333 and found.track == 3
    # Picked with no page known: its language is kept instead.
    store.put("b:2", ts.Choice(3, ts.TELETEXT, "fin", None, False, 0x102))
    assert store.find("b:2", options).page == 0x333
    # DVB subtitles are not kept; forgetting; the cap keeps the newest.
    store.put("c:3", ts.choices(DVB)[0])
    assert store.get("c:3") is None
    store.forget("a:1")
    assert store.get("a:1") is None
    for k in ("d:4", "e:5", "f:6"):
        store.put(k, options[0])
    assert store.get("b:2") is None and store.get("f:6") is not None
    s["teletext_pages"] = "not json"
    assert ts.PageStore(s).get("f:6") is None
