"""The tug-of-war watch: drops after real play, three in a minute, told once
per channel - and the live reconnect it sits in behaves exactly as before."""
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication

from dopeiptv.core.contest import ContestWatch
from dopeiptv.ui import main_window as mw
from dopeiptv.ui.main_window import MainWindow


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_three_drops_after_play_within_a_minute():
    c = _Clock()
    w = ContestWatch(clock=c)
    assert not w.drop("ch1", 8.0)
    c.t += 10
    assert not w.drop("ch1", 6.0)
    c.t += 10
    assert w.drop("ch1", 5.0)


def test_told_once_per_channel():
    c = _Clock()
    w = ContestWatch(clock=c)
    for _ in range(3):
        c.t += 5
        w.drop("ch1", 9.0)
    for _ in range(5):
        c.t += 5
        assert not w.drop("ch1", 9.0)
    assert not w.claim("ch1")


def test_drops_before_play_do_not_count():
    # A connection that never got going is a provider problem, not another
    # device taking the account.
    c = _Clock()
    w = ContestWatch(clock=c)
    for _ in range(6):
        c.t += 3
        assert not w.drop("ch1", 0.4)


def test_drops_spread_out_do_not_count():
    c = _Clock()
    w = ContestWatch(clock=c)
    for _ in range(6):
        c.t += 40
        assert not w.drop("ch1", 30.0)


def test_another_channel_starts_afresh():
    c = _Clock()
    w = ContestWatch(clock=c)
    w.drop("ch1", 9.0)
    w.drop("ch1", 9.0)
    assert not w.drop("ch2", 9.0)
    assert not w.drop("ch2", 9.0)
    assert w.drop("ch2", 9.0)


def test_buffer_route_and_drops_share_one_warning():
    c = _Clock()
    w = ContestWatch(clock=c)
    assert w.claim("ch1")
    for _ in range(4):
        c.t += 5
        assert not w.drop("ch1", 9.0)
    assert w.claim("ch2")
    assert not w.claim("ch2")


# -- in the live reconnect -----------------------------------------------------

class _Settings:
    def value(self, key, default=None):
        return {"auto_reconnect_live": "true"}.get(key, default)


def _window(played=9.0, watch=None):
    f = SimpleNamespace()
    f.settings = _Settings()
    f._last_playback = {"kind": "live", "key": "ch1", "url": "http://x/1.ts"}
    f._playing_catchup = False
    f._stream_retries = 0
    f._last_stream_error_ts = 0.0
    f._diag_shown = True
    f.statuses, f.told, f.slow = [], [], []
    f.player = SimpleNamespace(
        isVisible=lambda: True, current_url="http://x/1.ts",
        playback_position=lambda: played,
        set_overlay_info=lambda m: None)
    f._set_status = lambda m, **k: f.statuses.append(m)
    f._arm_slow_reconnect = lambda: f.slow.append(1)
    f._retry_last_stream = lambda: None
    f._tell_contested = lambda: f.told.append(1)
    f._health_drop = f._health_stuck = lambda: None   # stream health only watches
    f._contest = watch or ContestWatch()
    f.MAX_STREAM_RETRIES = MainWindow.MAX_STREAM_RETRIES
    f._auto_reconnect_live = MainWindow._auto_reconnect_live.__get__(f)
    return f


def _drop(f, reason="eof"):
    f.player.current_url = "http://x/1.ts"
    MainWindow._reconnect_live(f, reason)


def test_reconnect_tells_on_the_third_drop_and_still_reconnects(monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(mw.QTimer, "singleShot", lambda ms, fn: None)
    f = _window()
    _drop(f)
    _drop(f)
    assert f.told == []
    _drop(f)
    assert f.told == [1]
    # The reconnect went on exactly as it does without the watch: two quick
    # tries, then the slow timer.
    assert f._stream_retries == MainWindow.MAX_STREAM_RETRIES
    assert f.slow == [1]
    assert f._diag_shown is False


def test_stalls_are_not_counted(monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(mw.QTimer, "singleShot", lambda ms, fn: None)
    f = _window()
    for _ in range(4):
        _drop(f, "stall")
    assert f.told == []


def test_a_broken_watch_never_stops_the_reconnect(monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(mw.QTimer, "singleShot", lambda ms, fn: None)

    class _Broken:
        def drop(self, *a):
            raise RuntimeError("boom")

    f = _window(watch=_Broken())
    _drop(f)
    assert f._stream_retries == 1
    assert f.player.current_url is None
