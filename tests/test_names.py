"""A title whose parts repeat is said once, wherever it comes in.

The window's title read "Jorden runt på 6 steg · Jorden runt på 6 steg ·
Jorden runt på 6 steg · S2 * E3 - Jorden runt på 6 steg - S02E03": a
Continue watching row put "Show · " in front of the episode, the row
played from there was kept again with it, and each round added one. The
names are now kept once (services/resume.py, core/series_view.without_series)
and every title is collapsed where it comes in - the playback path, History
and Continue watching (core/names.collapse_repeats) - for every kind."""
import json

from dopeiptv.core.names import TEXT_MAX, collapse_repeats
from dopeiptv.core.stores import HistoryStore


class _S:
    def __init__(self):
        self.d = {}

    def value(self, k, default=None):
        return self.d.get(k, default)

    def setValue(self, k, v):            # noqa: N802 - QSettings' name
        self.d[k] = v


def test_a_part_said_again_right_after_itself_goes():
    name = "Jorden runt på 6 steg"
    assert collapse_repeats(f"{name} · {name} · {name} · S2 * E3") == f"{name} · S2 * E3"
    assert collapse_repeats("SVT1 · SVT1 - SVT1") == "SVT1"
    assert collapse_repeats("News | news | Live") == "News | Live"          # case aside
    for same in ("Love - Actually - Love", "Film", "", "A · B · C", "Pilot - S01E01"):
        assert collapse_repeats(same) is same                               # nothing repeats
    assert collapse_repeats(None) is None
    long = "A · " * (TEXT_MAX // 3)
    assert collapse_repeats(long) is long                                   # past the bound: as it came
    # Linear: a large repeated title is quick and comes out once.
    big = " · ".join(["Same"] * 500)
    assert collapse_repeats(big) == "Same"


def test_history_keeps_a_name_once_and_mends_one_kept_before():
    s = _S()
    old = [{"name": "Kanal 5 · Kanal 5 · Kanal 5", "_url": "http://x.invalid/1", "_key": 1, "_kind": "live"}]
    s.setValue("history", json.dumps(old))
    h = HistoryStore(s, "history")
    assert h.entries[0]["name"] == "Kanal 5"
    h.add("http://x.invalid/2", "Dune · Dune", None, 2, "movie")
    assert h.entries[0]["name"] == "Dune"
    h.add("http://x.invalid/3", "Semester.mkv", None, 3, "local")
    assert h.entries[0]["name"] == "Semester.mkv"


def test_the_window_title_and_history_say_an_episode_with_its_series_once():
    """The window's title, the overlay, the status line and History: "Show ·
    S2 E3 · Title" - the provider's "Show - S02E03" and a Continue watching
    row's "Show · " taken off first."""
    from dopeiptv.ui.main_window import MainWindow
    from dopeiptv.ui.mw_cast import _CastMixin

    class W:
        _last_playback = None
        series_ctx = None
        _episode_words = MainWindow._episode_words
        _shown_title = MainWindow._shown_title

    w = W()
    ctx = {"series_id": 7, "name": "Jorden runt på 6 steg"}
    w.series_ctx = ctx
    ep = {"id": 3, "season": "2", "episode_num": 3,
          "title": "Jorden runt på 6 steg - S02E03 - Kairo"}
    title = "Jorden runt på 6 steg · Jorden runt på 6 steg · S2 * E3 - Jorden runt på 6 steg - S02E03"
    assert w._shown_title(title, "episode", ep).startswith("Jorden runt på 6 steg · S2 E3 · Kairo")
    assert w._shown_title(title, "episode", ep).count("Jorden runt") == 1
    # Nothing of its own: the series and the episode's number.
    ep2 = dict(ep, title="S2 * E3 - Jorden runt på 6 steg - S02E03")
    assert w._shown_title(title, "episode", ep2) == "Jorden runt på 6 steg · S2 E3"
    # A film, a channel, a local file: as it came, said once.
    assert w._shown_title("Dune · Dune", "movie", {}) == "Dune"
    assert w._shown_title("SVT1", "live", {}) == "SVT1"
    extra = _CastMixin._history_extra(w, "episode", ep, title, ctx)
    assert extra["name"] == "Jorden runt på 6 steg · S2 E3 · Kairo"
