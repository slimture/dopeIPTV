"""DOPEIPTV_GUI_RENDER=tuned: the window-thread video surface without its
waits on the window's thread. Off, the surface is what it always was: no
option of the tuned timing reaches mpv."""
from __future__ import annotations

from dopeiptv.media import video_surface as vs


class _FakeCore(dict):
    def __setitem__(self, key, value):
        super().__setitem__(key, value)


class _Owner:
    EXTRA_OPTS: dict = {}
    variant_picker = False
    hwdec_pref = None


def _core(monkeypatch, tuned: bool) -> dict:
    monkeypatch.setattr(vs, "_create_core", _FakeCore)
    return vs.build_core(_Owner(), tuned=tuned)


def test_tuned_is_off_unless_asked(monkeypatch):
    monkeypatch.delenv("DOPEIPTV_GUI_RENDER", raising=False)
    assert vs.gui_render_tuned() is False
    monkeypatch.setenv("DOPEIPTV_GUI_RENDER", "Tuned ")
    assert vs.gui_render_tuned() is True
    monkeypatch.setenv("DOPEIPTV_GUI_RENDER", "legacy")
    assert vs.gui_render_tuned() is False


def test_the_core_is_built_as_always_without_tuned(monkeypatch):
    core = _core(monkeypatch, tuned=False)
    assert "video-timing-offset" not in core
    assert core["osd-level"] == 0 and core["keep-open"] == "yes"
    assert core["hwdec"] == "no"


def test_tuned_hands_frames_over_at_their_time(monkeypatch):
    core = _core(monkeypatch, tuned=True)
    assert core["video-timing-offset"] == 0


def test_a_surfaces_own_options_go_last(monkeypatch):
    class Owner(_Owner):
        EXTRA_OPTS = {"ao": "null", "osd-level": 1}
    monkeypatch.setattr(vs, "_create_core", _FakeCore)
    core = vs.build_core(Owner())
    assert core["ao"] == "null" and core["osd-level"] == 1


def test_prepare_gui_render_leaves_the_format_alone_unless_tuned(monkeypatch):
    from PyQt6.QtGui import QSurfaceFormat
    before = QSurfaceFormat.defaultFormat().swapInterval()
    monkeypatch.delenv("DOPEIPTV_GUI_RENDER", raising=False)
    vs.prepare_gui_render()
    assert QSurfaceFormat.defaultFormat().swapInterval() == before
    monkeypatch.setenv("DOPEIPTV_GUI_RENDER", "tuned")
    try:
        vs.prepare_gui_render()
        assert QSurfaceFormat.defaultFormat().swapInterval() == 0
    finally:
        fmt = QSurfaceFormat.defaultFormat()
        fmt.setSwapInterval(before)
        QSurfaceFormat.setDefaultFormat(fmt)
