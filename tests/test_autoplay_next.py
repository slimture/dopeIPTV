"""Autoplay-next-episode logic (MainWindow._on_player_finished).

Exercises the pure decision logic with a fake window so we can assert what
gets played without a real player or provider. Mirrors how the app wires the
embedded player's finished() signal to episode autoplay.
"""
from types import SimpleNamespace

import pytest

from dopeiptv.ui.main_window import MainWindow


class _FakeSettings:
    def __init__(self, values):
        self._v = values

    def value(self, key, default=None):
        return self._v.get(key, default)


EPISODES = [
    {"id": 1, "container_extension": "mp4", "name": "S1 E1"},
    {"id": 2, "container_extension": "mp4", "name": "S1 E2"},
    {"id": 3, "container_extension": "mp4", "name": "S1 E3"},
]


def _make(autoplay="true", mode="embedded", last=None):
    f = SimpleNamespace()
    f.settings = _FakeSettings({"autoplay_next_episode": autoplay})
    f._last_playback = last
    f.series_ctx = {"series_id": 7}
    f.played = []
    f.marked = []
    f._save_resume_position = lambda: None
    f._maybe_auto_mark_watched = (
        lambda: f.marked.append(f._last_playback.get("key")))
    f.playback_mode = lambda: mode
    f._item_key = lambda e: e.get("id")
    f.client = SimpleNamespace(
        episode_url=lambda i, ext: f"http://ep/{i}.{ext}")

    def start(url, title, icon, key, kind, record=True, item=None):
        f.played.append((title, key, kind))

    f._start_playback = start
    for name in ("_autoplay_next_episode", "_next_episode_item",
                 "_has_next_episode", "_advance_to_next_episode"):
        setattr(f, name, getattr(MainWindow, name).__get__(f))
    return f


def _last(idx):
    return {"kind": "episode", "key": EPISODES[idx]["id"],
            "item": EPISODES[idx], "series_ctx": {"series_id": 7},
            "ep_queue": EPISODES, "ep_index": idx}


def test_autoplays_next_episode_and_marks_finished():
    f = _make(last=_last(0))
    MainWindow._on_player_finished(f)
    assert f.played == [("S1 E2", 2, "episode")]
    assert 1 in f.marked


def test_advances_through_the_season():
    f = _make(last=_last(1))
    MainWindow._on_player_finished(f)
    assert f.played == [("S1 E3", 3, "episode")]


def test_last_episode_marks_but_does_not_autoplay():
    f = _make(last=_last(2))
    MainWindow._on_player_finished(f)
    assert f.played == []
    assert 3 in f.marked


def test_setting_off_disables_autoplay():
    f = _make(autoplay="false", last=_last(0))
    MainWindow._on_player_finished(f)
    assert f.played == []
    assert 1 in f.marked          # still marked as watched


def test_movie_finish_ignored_by_autoplay():
    f = _make(last={"kind": "movie", "key": 99})
    MainWindow._on_player_finished(f)
    assert f.played == []
    assert f.marked == []


def test_external_mode_does_not_autoplay_in_app():
    f = _make(mode="external", last=_last(0))
    MainWindow._on_player_finished(f)
    assert f.played == []
    assert 1 in f.marked


def test_queue_override_carried_forward():
    f = _make(last=_last(0))
    MainWindow._on_player_finished(f)
    assert f._ep_queue_override is EPISODES
    assert f._ep_index_override == 1


@pytest.mark.parametrize("last", [None, {}, {"kind": "rec"}])
def test_no_op_when_nothing_playable(last):
    f = _make(last=last)
    MainWindow._on_player_finished(f)
    assert f.played == []


def test_live_finish_reconnects_instead_of_autoplaying():
    # A live stream reaching EOF means the connection dropped, not that a
    # title ended - it must reconnect, not autoplay an episode.
    f = _make(last={"kind": "live", "key": 5})
    f._reconnect_live = lambda reason: f.played.append(("__reconnect__", reason))
    MainWindow._on_player_finished(f)
    assert f.played == [("__reconnect__", "eof")]


def test_manual_next_button_skips_without_waiting():
    # The player's 'next episode' button advances even mid-episode, and does
    # NOT force a watched mark (that's left to _start_playback's threshold).
    f = _make(last=_last(0))
    MainWindow._play_next_episode(f)
    assert f.played == [("S1 E2", 2, "episode")]
    assert f.marked == []


def test_manual_next_on_last_episode_is_noop():
    f = _make(last=_last(2))
    MainWindow._play_next_episode(f)
    assert f.played == []


def test_has_next_episode_reflects_position():
    f = _make(last=_last(0))
    assert f._has_next_episode() is True
    f = _make(last=_last(2))
    assert f._has_next_episode() is False


def test_a_history_episode_replays_from_its_stored_url():
    """A History row replays AS an episode - it carries the series context
    so it resumes and lands in the series list - but it has no provider
    episode id, because History stores the URL it was played from rather
    than the catalogue entry. Building a URL from the missing id gave
    /series/user/pass/None.mp4: not empty, so the "no url" guard waved it
    through and playback failed on a nonsense path."""
    f = SimpleNamespace()
    f.series_ctx = None
    f.played = []
    f._item_key = lambda e: e.get("_key")
    f.client = SimpleNamespace(
        episode_url=lambda i, ext: f"http://srv/series/u/p/{i}.{ext or 'mp4'}")

    def start(url, title, icon, key, kind, record=True, item=None):
        f.played.append((url, kind))

    f._start_playback = start
    f._play_continue_episode = MainWindow._play_continue_episode.__get__(f)

    row = {"_kind": "episode", "_key": "ep:88", "name": "Show · S1 E2",
           "_url": "http://srv/series/u/p/88.mkv",
           "_series_ctx": {"series_id": 7, "name": "Show"}}
    f._play_continue_episode(row)

    assert f.played == [("http://srv/series/u/p/88.mkv", "episode")]
    assert f.series_ctx is None          # the context is restored after


def test_a_continue_watching_episode_still_builds_its_provider_url():
    """The normal Continue-watching row does have an id, and must keep
    using it - the stored URL can be stale."""
    f = SimpleNamespace()
    f.series_ctx = None
    f.played = []
    f._item_key = lambda e: e.get("id")
    f.client = SimpleNamespace(
        episode_url=lambda i, ext: f"http://srv/series/u/p/{i}.{ext or 'mp4'}")

    def start(url, title, icon, key, kind, record=True, item=None):
        f.played.append((url, kind))

    f._start_playback = start
    f._play_continue_episode = MainWindow._play_continue_episode.__get__(f)

    f._play_continue_episode({"id": 42, "container_extension": "mp4",
                              "_url": "http://stale/old.mp4",
                              "_series_ctx": {"series_id": 7}})
    assert f.played == [("http://srv/series/u/p/42.mp4", "episode")]


def test_a_subtitle_choice_carries_to_the_next_episode():
    """Picking Swedish subtitles on episode 1 means you want them on
    episode 2. The choice was filed under the episode alone, so autoplay
    moved on and it was gone."""
    store = {}

    class _S:
        @staticmethod
        def value(k, d=None):
            return store.get(k, d)

        @staticmethod
        def setValue(k, v):
            store[k] = v

    f = SimpleNamespace()
    f.settings = _S()
    f.series_ctx = {"series_id": 7}
    f._RESUMABLE = MainWindow._RESUMABLE
    f._TRACK_PREFS_MAX = MainWindow._TRACK_PREFS_MAX
    for n in ("_track_prefs", "_on_track_selected", "_series_pref_keys"):
        setattr(f, n, getattr(MainWindow, n).__get__(f))

    # Watching episode 1, the user picks a subtitle track.
    f._last_playback = {"kind": "episode", "key": 101,
                        "series_ctx": {"series_id": 7}}
    f._on_track_selected("sid", 3)

    prefs = f._track_prefs()
    assert prefs["episode:101"]["sid"] == 3      # this episode
    assert prefs["series:7"]["sid"] == 3         # and the show

    # Episode 2 has its own key and no entry of its own - the series one
    # is what carries the choice over.
    assert "episode:102" not in prefs
    f._last_playback = {"kind": "episode", "key": 102,
                        "series_ctx": {"series_id": 7}}
    assert f._series_pref_keys("episode") == ["series:7"]
    assert prefs[f._series_pref_keys("episode")[0]]["sid"] == 3

    # A different show is unaffected.
    f._last_playback = {"kind": "episode", "key": 900,
                        "series_ctx": {"series_id": 99}}
    assert f._series_pref_keys("episode") == ["series:99"]
    assert "series:99" not in prefs

    # A movie has no series to file under, and must not grow a bogus key.
    f._last_playback = {"kind": "movie", "key": 5, "series_ctx": None}
    f.series_ctx = None
    assert f._series_pref_keys("movie") == []
    f._on_track_selected("aid", 2)
    assert f._track_prefs()["movie:5"]["aid"] == 2


def test_the_macos_icon_leaves_apples_margin_and_renders_full_size():
    """Two things the Dock exposed: the tile was drawn edge to edge, so it
    stood a quarter larger than every neighbour (Apple's is 824 of 1024),
    and QIcon.pixmap() never scales UP, so asking for 1024 handed back the
    256 px image the icon had actually been drawn at.

    Run in a child: a QApplication built in-process fights the other tests
    (same reasoning as tests/test_embedded.py)."""
    import os
    import subprocess
    import sys

    child = r"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication
from dopeiptv.app import MACOS_ICON_INSET, make_app_icon

app = QApplication([])

# Full-bleed everywhere else: the tile touches the canvas edge.
plain = make_app_icon().pixmap(256, 256).toImage()
assert plain.pixelColor(2, 128).alpha() > 0, "non-mac icon must be full bleed"

# macOS: a transparent margin of roughly a tenth on each side, drawn at
# the size actually asked for.
mac = make_app_icon(inset=MACOS_ICON_INSET, sizes=(1024,)).pixmap(1024, 1024).toImage()
assert mac.width() == 1024, "the icon must be drawn AT 1024, not upscaled"
assert mac.pixelColor(2, 512).alpha() == 0, "no ink at the very edge"
cols = [x for x in range(1024) if mac.pixelColor(x, 512).alpha() > 0]
tile = (cols[-1] - cols[0] + 1) / 1024
assert 0.78 <= tile <= 0.83, "tile is %.1f%% of canvas, want ~80.5%%" % (tile * 100)

# The RUNTIME icon matters as much as the .icns: on macOS setWindowIcon
# overrides the bundle icon in the Dock the moment the app starts, so a
# full-bleed one here undoes the margin a second after launch. Check the
# call main() actually makes, per platform.
import re, inspect, dopeiptv.app as A
src = inspect.getsource(A.main)
assert "MACOS_ICON_INSET if _mac else 0.0" in src, \
    "the runtime icon must carry Apple's margin on macOS"
assert re.search(r"sizes=\(\(512", src), \
    "the runtime icon must be drawn big enough for a retina Dock"
print("ICON_OK")
"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.run([sys.executable, "-c", child], capture_output=True,
                          text=True, cwd=root, timeout=180,
                          env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
    assert "ICON_OK" in proc.stdout, (
        f"icon check failed\nstdout={proc.stdout!r}\n"
        f"stderr={proc.stderr[-1500:]!r}")


def test_a_series_row_is_never_played_as_an_episode():
    """Toggling grid view inside a series can leave the list showing the
    series while series_ctx is still set. A double-click then took that
    row for an episode, built ".../series/u/p/None.mp4" from an id it does
    not have, STOPPED whatever was playing and showed an error. The row
    decides what it is, not the flag."""
    f = SimpleNamespace()
    f.mode = "series"
    f.series_ctx = {"series_id": 7}        # stale: the list shows series
    f.entered = []
    f.played = []
    f._enter_series = lambda it: f.entered.append(it.get("series_id"))
    f._request_unlock = lambda: True
    f.client = SimpleNamespace(
        episode_url=lambda i, ext: f"http://srv/series/u/p/{i}.{ext or 'mp4'}")

    def start(*a, **k):
        f.played.append(a[0])

    f._start_playback = start
    f.play_item = MainWindow.play_item.__get__(f)
    f._stream_for = MainWindow._stream_for.__get__(f)

    # The series row: series_id, no episode id.
    f.play_item({"series_id": 7, "name": "The Sopranos"})
    assert f.entered == [7], "a series row must drill in"
    assert f.played == [], "and must never start playback"

    # _stream_for is guarded too, so no other caller can build that URL.
    url, _ = f._stream_for({"series_id": 7, "name": "The Sopranos"})
    assert url is None or "None." not in str(url), \
        f"built a bogus episode URL: {url!r}"


def test_a_real_episode_still_plays_from_inside_a_series():
    """The guard keys off the row having no episode id - a real episode
    has one, and must be unaffected."""
    f = SimpleNamespace()
    f.mode = "series"
    f.series_ctx = {"series_id": 7}
    f.entered = []
    f.client = SimpleNamespace(
        episode_url=lambda i, ext: f"http://srv/series/u/p/{i}.{ext or 'mp4'}")
    f._enter_series = lambda it: f.entered.append(it)

    f._stream_for = MainWindow._stream_for.__get__(f)
    url, title = f._stream_for(
        {"id": 4242, "container_extension": "mkv", "name": "S2 E10"})
    assert url == "http://srv/series/u/p/4242.mkv"
    assert title == "S2 E10"
    assert f.entered == []


def test_toggling_grid_inside_a_series_keeps_the_episodes():
    """Turn grid on inside a series, turn it off: the episode list must
    still be there. Pins the documented path - a view toggle re-filters
    what is already loaded and must never rebuild the category, which
    would put the series list back on screen while series_ctx still said
    'inside a series' (and a double-click there then tried to play an
    episode that does not exist)."""
    class _Box:
        def __init__(self, d): self._d = d
        def currentData(self): return self._d
        def findData(self, k): return 0
        def setCurrentIndex(self, i): pass
        def blockSignals(self, b): pass

    class _Btn(_Box):
        def __init__(self, on): self.on = on
        def isChecked(self): return self.on
        def setChecked(self, v): self.on = v

    class _Model:
        def __init__(self): self.items, self.kind = [], None
        def set_items(self, items, kind):
            self.items, self.kind = list(items), kind

    episodes = [{"id": i, "name": f"S1 E{i}"} for i in range(1, 6)]
    store = {}
    f = SimpleNamespace()
    f.settings = SimpleNamespace(
        value=lambda k, d=None: store.get(k, d),
        setValue=lambda k, v: store.__setitem__(k, v))
    f.mode = "series"
    f.series_ctx = {"series_id": 7}
    f.all_items = list(episodes)
    f._current_cat = "cat-1"
    f.list_model = _Model()
    f.size_box, f.sort_box = _Box("medium"), _Box("global")
    f.grid_btn = _Btn(False)
    f.delegate = SimpleNamespace(
        set_density=lambda d: None, set_grid=lambda g: None,
        grid_size=lambda: SimpleNamespace(height=lambda: 200), row_h=40)
    f.listw = SimpleNamespace(
        setVerticalScrollMode=lambda m: None,
        verticalScrollBar=lambda: SimpleNamespace(
            setSingleStep=lambda s: None),
        setViewMode=lambda m: None, setFlow=lambda x: None,
        setWrapping=lambda b: None, set_grid_cell=lambda c: None,
        setGridSize=lambda s: None, setResizeMode=lambda m: None)
    f.search = SimpleNamespace(text=lambda: "")
    f.LABELS = {"episode": "episodes"}
    f.rebuilt = []
    f._load_items = lambda cat: f.rebuilt.append(cat)
    f._channel_hidden = lambda it, kind: False
    f._sorted = lambda x: x
    f._hide_busy = lambda: None
    f._set_status = lambda *a, **k: None
    f._pending_jump_key = None
    for n in ("_apply_view_settings", "_apply_filter", "_content_kind",
              "_is_combined_view", "_apply_list_layout", "_grid_on",
              "_inline_view_changed", "_sort_setting_key",
              "_current_sort_raw", "_sync_sort_box"):
        setattr(f, n, getattr(MainWindow, n).__get__(f))

    f._apply_filter()
    assert f.list_model.kind == "episode" and len(f.list_model.items) == 5

    f.grid_btn.on = True
    f._inline_view_changed()
    assert f.list_model.kind == "episode", "grid on dropped out of the series"
    assert len(f.list_model.items) == 5

    f.grid_btn.on = False
    f._inline_view_changed()
    assert f.list_model.kind == "episode", "grid off dropped out of the series"
    assert len(f.list_model.items) == 5
    assert f.series_ctx, "the drill state must survive a view toggle"
    assert f.rebuilt == [], \
        f"a view toggle must not rebuild the category: {f.rebuilt}"
