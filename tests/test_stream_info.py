"""Observed stream details: source FPS, bounded cache, load isolation,
no polling, off by default, and the cache as a viewing trace."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from dopeiptv.core import stream_info


class Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):
        self[key] = value


ON = {"show_stream_info": "true"}


def tracks(width=1920, fps=25):
    return [{"type": "video", "selected": True, "demux-w": width,
             "demux-h": 1080, "demux-fps": fps, "codec": "h264"}]


def test_selected_video_uses_source_fps_without_guessing():
    ts = [{"type": "video", "selected": False, "demux-w": 3840}] + tracks(fps=23.976)
    info = stream_info.observed(ts)
    assert stream_info.summary(info) == "1920×1080 · 23.976 fps · H.264"
    del ts[1]["demux-fps"]
    assert "fps" not in stream_info.summary(stream_info.observed(ts))
    assert stream_info.observed([{"type": "audio", "selected": True}]) == {}


@pytest.mark.parametrize("value", [None, [], {"width": "NaN", "height": -1,
                                             "fps": float("inf"), "codec": "<b>bad</b>"}])
def test_invalid_metadata_is_not_displayed(value):
    assert stream_info.summary(value) == ""


def test_cache_survives_restart_and_is_separate_for_each_provider():
    settings = Settings()
    cache = stream_info.StreamInfoCache(settings)
    assert cache.put("provider-a:1", stream_info.observed(tracks()))
    assert not cache.put("provider-a:1", stream_info.observed(tracks()))
    restored = stream_info.StreamInfoCache(settings)
    assert restored.get("provider-a:1") == "1920×1080 · 25 fps · H.264"
    assert restored.get("provider-b:1") == ""


def test_corrupt_cache_and_size_limit():
    for raw in ("broken", "[]", "null", '{"x": []}'):
        assert not stream_info.StreamInfoCache(Settings({stream_info.KEY: raw})).data
    settings = Settings()
    cache = stream_info.StreamInfoCache(settings)
    for i in range(stream_info.CAP + 1):
        cache.put(str(i), {"width": 1920})
    assert len(json.loads(settings[stream_info.KEY])) == stream_info.CAP
    assert not cache.get("0")


def test_banner_waits_for_its_load_and_updates_without_replaying():
    from PyQt6.QtCore import pyqtSignal
    from PyQt6.QtWidgets import QApplication, QWidget
    from dopeiptv.media.stream_info import StreamInfoMixin

    app = QApplication.instance() or QApplication([])

    class Core:
        path = "old"
        track_list = tracks()

        def event_callback(self, name):
            assert name == "file-loaded"
            return lambda cb: setattr(self, "loaded", cb)

        def observe_property(self, name, cb):
            assert name == "video-params"
            self.reconfig = cb

    class Player(StreamInfoMixin, QWidget):
        _info_loaded = pyqtSignal(str)
        _info_reconfig = pyqtSignal()
        stream_info_changed = pyqtSignal(str, object)

    p = Player()
    p._settings = Settings(ON)
    p._chan_key = "source:1"
    core = Core()
    p.video = SimpleNamespace(mpv=core)
    p._ov_surface = QWidget(p)
    p._ov_surface.resize(640, 360)
    p._init_stream_info({"bg": "black", "text": "white"})
    seen = []
    p.stream_info_changed.connect(lambda key, info: seen.append((key, info)))
    p._arm_stream_info(core, "new")
    core.reconfig("video-params", {})  # nothing loaded yet: nothing read
    assert not seen
    core.loaded(None)  # old load cannot populate the new channel
    assert not seen
    core.path = "new"
    core.loaded(None)
    assert seen[-1][0] == "source:1"
    assert p._info_hide.isActive()
    assert "25 fps" in p.stream_info_badge.text()
    p._info_hide.timeout.emit()
    assert p.stream_info_badge.isHidden()
    core.reconfig("video-params", {})
    assert p.stream_info_badge.isHidden()  # never reappears on a format change
    core.track_list = tracks(width=1280, fps=50)
    core.reconfig("video-params", {"w": 1280})
    assert seen[-1][1]["fps"] == 50
    p._settings["show_stream_info"] = "false"
    core.track_list = tracks(width=3840)
    core.reconfig("video-params", {"w": 3840})
    assert seen[-1][1]["width"] == 1280
    p._settings["show_stream_info"] = "true"
    p._read_stream_info()        # what Settings does when the switch turns on
    assert seen[-1][1]["width"] == 3840
    p._reset_stream_info()
    p._chan_key = "source:2"
    p._arm_stream_info(core, "next")
    core.loaded(None)
    assert seen[-1][0] == "source:1"
    p._reset_stream_info()
    p.close()
    app.processEvents()


def test_new_setting_is_translated_in_every_language():
    from dopeiptv import i18n
    for key in ("setting_stream_info", "setting_stream_info_hint"):
        assert set(i18n._STRINGS[key]) == set(i18n.LANGUAGES)
        assert all(i18n._STRINGS[key].values())


def test_real_mpv_file_loaded_delivers_observed_source_details():
    import time
    from pathlib import Path
    from PyQt6.QtCore import pyqtSignal
    from PyQt6.QtWidgets import QApplication, QWidget
    from dopeiptv.media.stream_info import StreamInfoMixin
    from tests.test_nerd import _libmpv

    app = QApplication.instance() or QApplication([])
    core = _libmpv()

    class Player(StreamInfoMixin, QWidget):
        _info_loaded = pyqtSignal(str)
        _info_reconfig = pyqtSignal()
        stream_info_changed = pyqtSignal(str, object)

    p = Player()
    p._settings = Settings(ON)
    p._chan_key = "synthetic:1"
    p.video = SimpleNamespace(mpv=core)
    p._ov_surface = QWidget(p)
    p._ov_surface.resize(640, 360)
    p._init_stream_info({"bg": "black", "text": "white"})
    seen = []
    p.stream_info_changed.connect(lambda key, info: seen.append(info))
    path = str(Path(__file__).parent / "data" / "tiny.ts")
    try:
        p._arm_stream_info(core, path)
        core.play(path)
        deadline = time.monotonic() + 10
        while not seen and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert seen, "file-loaded must reach the UI from mpv's event thread"
        assert seen[-1].get("width", 0) > 0
        assert seen[-1].get("fps", 0) > 0
    finally:
        p._reset_stream_info()
        core.terminate()
        p.close()
        app.processEvents()


def test_list_cache_respects_toggle_and_private_viewing(monkeypatch):
    from dopeiptv.core import privacy
    from dopeiptv.ui.mw_stream_info import _StreamInfoMixin

    host = SimpleNamespace(settings=Settings(ON),
                           _health_key=lambda key, item: "source:1",
                           _item_key=lambda item: 1)
    cache = stream_info.StreamInfoCache(host.settings)
    cache.put("source:1", stream_info.observed(tracks()))
    host._stream_info_store = lambda: cache
    read = lambda: _StreamInfoMixin.channel_stream_info(host, {"stream_id": 1}, "live")
    assert "1920×1080" in read()
    host.settings["show_stream_info"] = "false"
    assert read() == ""
    host.settings["show_stream_info"] = "true"
    monkeypatch.setattr(privacy, "private", lambda: True)
    assert read() == ""


@pytest.mark.parametrize("density", ["compact", "medium", "large", "xlarge"])
def test_technical_line_stays_below_epg_and_inside_each_row(density, tmp_path):
    from PyQt6.QtCore import QRect, Qt
    from PyQt6.QtGui import QPainter, QPixmap
    from PyQt6.QtWidgets import QApplication, QStyleOptionViewItem, QWidget
    from dopeiptv.ui.channel_list import ChannelDelegate, ChannelListModel
    from dopeiptv.ui.theme import P

    app = QApplication.instance() or QApplication([])
    host = QWidget()
    host.settings = Settings(ON)
    host._playing_key = None
    host._playing_group = None
    host.item_tint = lambda *a: (None, None)
    host.channel_display_name = lambda it: it["name"]
    host.channel_stream_info = lambda *a: "1920×1080 · 50 fps · H.264"
    host.cover = SimpleNamespace(cover_url=lambda *a: "", should_fetch=lambda *a: False)
    host.watched_source = lambda *a: None
    host.is_item_on_watchlist = lambda *a: False
    host.is_favorite_item = lambda *a: False
    host._timeshift_days = lambda *a: 0
    host.xmltv = SimpleNamespace(now_for=lambda *a: ("Programme", 50))
    delegate = ChannelDelegate(host, density)
    model = ChannelListModel()
    model.set_items([{"name": "Test channel", "stream_id": 1}], "live")
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 600, delegate.row_h)
    pix = QPixmap(option.rect.size())
    pix.fill(Qt.GlobalColor.black)

    class Painter(QPainter):
        text_rows = []

        def drawText(self, rect, flags, text):
            self.text_rows.append((QRect(rect), text))
            super().drawText(rect, flags, text)

    painter = Painter(pix)
    try:
        delegate.paint(painter, option, model.index(0, 0))
    finally:
        painter.end()
    technical = next(r for r, t in painter.text_rows if "50 fps" in t)
    programme = next(r for r, t in painter.text_rows if "Programme" in t)
    assert technical.top() >= programme.bottom() + 4
    assert technical.bottom() < delegate.row_h
    assert technical.top() >= 0
    assert pix.save(str(tmp_path / f"stream-info-{density}.png"))
    assert P["text"]
    host.close()
    app.processEvents()


def test_off_by_default_everywhere():
    """Off until switched on: rows keep their height, nothing is shown."""
    from PyQt6.QtWidgets import QApplication, QWidget
    from dopeiptv.ui.channel_list import ChannelDelegate

    assert not stream_info.enabled(Settings())
    app = QApplication.instance() or QApplication([])
    host = QWidget()
    host.settings = Settings()
    plain = ChannelDelegate(host, "medium").row_h
    host.settings = Settings(ON)
    assert ChannelDelegate(host, "medium").row_h > plain
    host.close()
    app.processEvents()


def test_banner_keeps_no_polling_timer():
    from PyQt6.QtCore import QTimer, pyqtSignal
    from PyQt6.QtWidgets import QApplication, QWidget
    from dopeiptv.media.stream_info import StreamInfoMixin

    app = QApplication.instance() or QApplication([])

    class Player(StreamInfoMixin, QWidget):
        _info_loaded = pyqtSignal(str)
        _info_reconfig = pyqtSignal()
        stream_info_changed = pyqtSignal(str, object)

    p = Player()
    p._init_stream_info({"bg": "black", "text": "white"})
    assert [t.isSingleShot() for t in p.findChildren(QTimer)] == [True]  # the 4 s hide
    p.close()
    app.processEvents()


def test_window_side_lives_in_its_own_mixin():
    from dopeiptv.ui import main_window
    from dopeiptv.ui.mw_stream_info import _StreamInfoMixin

    assert issubclass(main_window.MainWindow, _StreamInfoMixin)
    for name in ("_stream_info_store", "_remember_stream_info", "channel_stream_info"):
        assert name not in main_window.MainWindow.__dict__


def test_clear_recent_history_drops_the_cache(tmp_path):
    from PyQt6.QtCore import QSettings
    from dopeiptv.core import privacy
    from dopeiptv.core.stores import HistoryStore

    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    stream_info.StreamInfoCache(s).put("a:1", stream_info.observed(tracks()))
    h = HistoryStore(s, "history_a")
    privacy.clear_recent(s, s, h, 0, channels=False, titles=True, positions=False)
    assert stream_info.StreamInfoCache(s).get("a:1")      # titles only: kept
    privacy.clear_recent(s, s, h, 0, channels=True, titles=False, positions=False)
    assert not stream_info.StreamInfoCache(s).data
    stream_info.StreamInfoCache(s).put("a:1", stream_info.observed(tracks()))
    privacy.clear_recent(s, s, h, 3600, channels=True, titles=False, positions=False)
    assert not stream_info.StreamInfoCache(s).data       # no times kept: any range


def test_backups_leave_the_cache_out():
    from dopeiptv.core import backup
    assert backup.left_out(stream_info.KEY)
