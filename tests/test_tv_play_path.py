"""TV mode plays in its own picture: the player's page is shown before the
first stream opens (its video surface makes the mpv core when it is shown),
and a refused play never opens an external mpv window behind the app."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dopeiptv.ui.mw_tv import _TvMixin  # noqa: E402

_APP = None
_SRC = (Path(__file__).resolve().parent.parent / "dopeiptv/ui/main_window.py"
        ).read_text(encoding="utf-8")


class _Shown:
    def __init__(self, log=None, name=""):
        self.shown = 0
        self.mpv = None
        self._log, self._name = log, name

    def show(self):
        self.shown += 1
        if self._log is not None:
            self._log.append(self._name)


class _Player(_Shown):
    def __init__(self, log=None):
        super().__init__(log, "player")
        self.video = _Shown()


class _Stack:
    def __init__(self, index, log=None):
        self.index = index
        self._log = log

    def currentIndex(self):  # noqa: N802 - Qt's name
        return self.index

    def setCurrentIndex(self, i):  # noqa: N802 - Qt's name
        self.index = i
        if self._log is not None:
            self._log.append(f"stack {i}")

    def updatesEnabled(self):  # noqa: N802 - Qt's name
        return True


class _Win(_TvMixin):
    def __init__(self, on, index=3):
        self.log: list[str] = []
        self._tv_on = on
        self._player_fs = False
        self.player = _Player(self.log)
        self._center_stack = _Stack(index, self.log)

    def _enter_docked_fullscreen(self):
        self._player_fs = True
        self.log.append("fullscreen")


def test_tv_mode_shows_the_player_before_its_first_stream():
    w = _Win(True)
    w._tv_before_play()
    assert w._center_stack.index == 0 and w.player.shown and w.player.video.shown
    # In the picture's own layout first: the window's lists never come up
    # behind TV mode for the moment the player is shown.
    assert w.log == ["fullscreen", "stack 0", "player"], w.log


def test_nothing_changes_outside_tv_mode_or_once_the_core_is_made():
    w = _Win(False)
    w._tv_before_play()
    assert w._center_stack.index == 3 and not w.player.shown
    w = _Win(True)
    w.player.video.mpv = object()
    w._tv_before_play()
    assert w._center_stack.index == 3 and not w.player.shown


def test_the_playback_path_asks_before_play_and_never_opens_mpv_in_tv_mode():
    asked = _SRC.index('getattr(self, "_tv_before_play", None)')
    plays = _SRC.index("if self.player.play(url, title, start=resume_at,")
    assert asked < plays
    tv = _SRC.index('elif getattr(self, "_tv_on", False):', plays)
    external = _SRC.index('launch_player("mpv", url, title, self)', plays)
    assert tv < external
    assert 'tr("tv_play_refused")' in _SRC[tv:external]


class _NowWin:
    playing = True
    title = "SVT1 HD · Rapport"

    def __init__(self):
        self.pictures = 0

    def _tv_has_picture(self):
        return self.playing

    def _tv_now_title(self):
        return self.title

    def _tv_picture(self):
        self.pictures += 1


def _app():
    from PyQt6.QtWidgets import QApplication
    global _APP
    _APP = QApplication.instance() or QApplication([])   # kept alive
    return _APP


def _page(win, monkeypatch=None, height=None):
    from dopeiptv.ui import tv_mode, tv_widgets
    _app()
    if monkeypatch is not None and height is not None:
        # As TvPage does with the screen it opens on, for this one's height.
        monkeypatch.setattr(tv_mode, "set_unit", lambda _h: tv_widgets.set_unit(height))
    return tv_mode.TvPage(win)


def _screen(page):
    """A screen with one tile, as Home under its tab."""
    from dopeiptv.ui.tv_mode import TvScreen
    from dopeiptv.ui.tv_widgets import TvTile

    class One(TvScreen):
        key = "home"

        def __init__(self, page):
            super().__init__(page)
            self.tile = TvTile("A", parent=self)

        def focusables(self):
            return [self.tile]

    scr = One(page)
    page.screens["home"] = scr
    page.stack.addWidget(scr)
    page.show_screen("home", focus=False)
    return scr


def test_the_page_shows_what_plays_and_ok_on_it_is_the_picture():
    from dopeiptv.i18n import tr

    win = _NowWin()
    page = _page(win)
    page.refresh_now()
    assert page.now not in page.tabs and not page.now.isHidden()
    assert page.now in page.focusables()
    # The hint says how to get back to the picture, and how TV mode is left.
    assert "SVT1 HD" in page.now.title
    assert page.hint.text() == f"{tr('tv_hint_now')} · {tr('tv_hint_leave')}"
    page.picture_back = True                    # a look from the picture: Back returns
    page.refresh_now()
    assert page.hint.text() == tr("tv_hint_playing")
    page.now.click()
    assert win.pictures == 1
    win.playing = False
    page.picture_back = False
    page.refresh_now()
    assert page.now.isHidden() and page.now not in page.focusables()
    assert page.hint.text() == f"{tr('tv_hint')} · {tr('tv_hint_leave')}"


def test_now_playing_moves_up_from_the_tabs_and_back_down():
    win = _NowWin()
    page = _page(win)
    scr = _screen(page)
    page.resize(1280, 720)
    page.show()
    _APP.processEvents()
    page.refresh_now()
    _APP.processEvents()
    home = page.tab("home")
    page.set_focus(home)
    page.move("up")
    assert page.focus_widget() is page.now
    for d in ("left", "right", "up"):
        page.move(d)
        assert page.focus_widget() is page.now, d
    page.move("down")
    assert page.focus_widget() is home
    page.set_focus(page.tab("vod"))
    page.move("up")
    assert page.focus_widget() is page.now
    # OK and Back on it: the picture.
    page.handle("ok")
    assert win.pictures == 1
    page.handle("back")
    assert win.pictures == 2
    # Up out of the screen still lands on its tab, never on Now playing.
    page.set_focus(scr.tile)
    page.move("up")
    assert page.focus_widget() is home
    # Hidden with the focus on it: the focus goes to the screen's tab.
    page.set_focus(page.now)
    win.playing = False
    page.refresh_now()
    assert page.focus_widget() is home and page.now.isHidden()
    page.move("up")
    assert page.focus_widget() is home          # nothing above the tabs now


def test_now_playing_and_the_tabs_fit_the_screen(monkeypatch):
    from PyQt6.QtCore import QPoint, QRect

    from dopeiptv.ui import tv_widgets
    long_title = "SVT1 HD · " + "A very long programme title that goes on " * 6
    unit = tv_widgets._unit[0]
    try:
        for w, h in ((1280, 720), (1366, 768), (1440, 900), (1920, 1080), (2560, 1600)):
            win = _NowWin()
            win.title = long_title
            page = _page(win, monkeypatch, h)
            page.resize(w, h)
            page.show()
            _APP.processEvents()
            page.refresh_now()
            _APP.processEvents()
            assert page.minimumSizeHint().width() <= w, (w, h, page.minimumSizeHint())
            mx = int(w * page.SAFE)
            inside = QRect(mx, 0, w - 2 * mx, h)
            now = page.now.geometry()
            assert inside.contains(now), (w, h, now)
            tabs = [QRect(t.mapTo(page, QPoint(0, 0)), t.size()) for t in page.tabs]
            assert all(inside.contains(r) for r in tabs), (w, h, tabs)
            assert now.bottom() < min(r.top() for r in tabs)      # a row of its own
            # A short title: as wide as its words, not the page.
            win.title = "SVT1"
            page.refresh_now()
            _APP.processEvents()
            assert page.now.width() < w // 4, (w, page.now.width())
            assert inside.contains(page.now.geometry())
            page.close()
            page.deleteLater()
    finally:
        tv_widgets._unit[0] = unit


class _Timer:
    def __init__(self):
        self.active = True

    def stop(self):
        self.active = False


class _Watching(_TvMixin):
    """Over the picture, a live channel: what Back and Stop do."""

    def __init__(self):
        from PyQt6.QtWidgets import QWidget
        _app()
        self._tv_on, self._tv_watching = True, True
        self.player = _Player()
        self.player.current_url = "http://x/1.ts"
        self._last_playback = {"kind": "live"}
        self._chan_buffer, self._chan_timer = "", _Timer()
        self._tv_info = QWidget()
        self.done = []

    def _chan_entry_show(self, text, ms):
        self.done.append(("entry", text))

    def _tv_show_info(self):
        self._tv_info.show()

    def _tv_browse(self, screen=None):
        self.done.append("page")
        self._tv_watching = False

    def _tv_stop(self):
        self.done.append("stop")
        self.player.current_url = None


def test_back_over_the_picture_goes_back_a_step_and_the_stream_goes_on():
    w = _Watching()
    w._chan_buffer = "12"                       # a number being typed: cleared, nothing else
    w._tv_info.show()
    w._tv_action("back")
    assert w._chan_buffer == "" and not w._chan_timer.active and w.done == [("entry", "")]
    assert w._tv_watching and w._tv_info.isVisible()
    w._tv_action("back")                        # the info bar: hidden, nothing else
    assert not w._tv_info.isVisible() and w._tv_watching and w.done == [("entry", "")]
    w._tv_action("back")                        # then the page, the stream behind it
    assert w.done[-1] == "page" and not w._tv_watching
    assert w.player.current_url == "http://x/1.ts"


def test_stop_over_the_picture_stops():
    w = _Watching()
    w._tv_info.show()
    w._tv_action("stop")
    assert w.done == ["stop"] and w.player.current_url is None
