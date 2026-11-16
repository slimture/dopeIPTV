"""Every Stop the person gives goes through the player's Stop button.

The window hangs what a stop must also end on that button's clicked signal
- the recording that rides the player's stream, the Trakt and Simkl
scrobbles, the wake lock, the pop-out (main_window.py) - so a Stop that
called player.stop() alone left the screen kept awake and the scrobble
"watching". The sleep timer did exactly that: the stream stopped at the
time asked, and the screen never went to sleep.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from dopeiptv.i18n import tr  # noqa: E402


class _Button:
    def __init__(self) -> None:
        self.clicks = 0

    def click(self) -> None:
        self.clicks += 1


class _Quiet:
    def stop(self) -> None:
        pass

    def hide(self) -> None:
        pass

    def emit(self, *_a) -> None:
        pass


class _Player:
    def __init__(self) -> None:
        self.current_url = "http://example.invalid/live/1.ts"
        self._paused = False
        self._fs_ui = False
        self.stop_btn = _Button()
        self.stopped_directly = 0
        self.double_clicked = _Quiet()
        self.extra_options: list = []

    def stop(self) -> None:
        self.stopped_directly += 1

    def toggle_pause(self) -> None:
        pass

    def populate_options_menu(self, _menu, extras: bool = True) -> None:
        pass


_APP: list[QApplication] = []


def _app() -> QApplication:
    app = QApplication.instance() or QApplication([])
    _APP.append(app)            # kept: a collected QApplication takes Qt down
    return app


def _trigger(menu, text: str) -> None:
    act = next(a for a in menu.actions() if a.text() == text)
    act.trigger()


def test_the_sleep_timer_stops_through_the_stop_button():
    from dopeiptv.media.embedded import EmbeddedPlayer
    p = _Player()
    fake = SimpleNamespace(_sleep_tick=_Quiet(), _sleep_badge_timer=_Quiet(),
                           sleep_badge=_Quiet(), set_overlay_info=lambda _t: None,
                           stop=p.stop, stop_btn=p.stop_btn)
    EmbeddedPlayer._on_sleep_elapsed(fake)
    assert p.stop_btn.clicks == 1
    assert p.stopped_directly == 0


def test_the_docked_right_click_stop_is_the_stop_button(monkeypatch):
    _app()
    from dopeiptv.ui import mw_multiview

    shown = []
    monkeypatch.setattr(mw_multiview, "popup_menu_over_video",
                        lambda menu, _pos: shown.append(menu))

    class Win(QWidget, mw_multiview._MultiviewMixin):
        def _send_docked_to_multiview(self) -> None:
            pass

    win = Win()
    win.player = _Player()
    win._docked_context_menu(None)
    _trigger(shown[0], tr("btn_stop"))
    assert win.player.stop_btn.clicks == 1
    assert win.player.stopped_directly == 0


def test_the_popout_right_click_stop_is_the_stop_button(monkeypatch):
    _app()
    from dopeiptv.ui import mw_popout

    shown = []
    monkeypatch.setattr(mw_popout, "popup_menu_over_video",
                        lambda menu, _pos: shown.append(menu))

    class Win(QWidget, mw_popout._PopoutMixin):
        pass

    win = Win()
    win._popout_win = QWidget()
    win.settings = SimpleNamespace(value=lambda _k, d=None: d)
    win.player = _Player()
    win._popout_context_menu(None)
    _trigger(shown[0], tr("btn_stop"))
    assert win.player.stop_btn.clicks == 1
    assert win.player.stopped_directly == 0


def test_a_channel_the_box_cannot_decrypt_stops_through_the_stop_button():
    from dopeiptv.ui.mw_box import _BoxMixin

    errors = []
    win = SimpleNamespace(_playing_key=("live", 7), _box_scrambled_key=("live", 7),
                          player=_Player(), _show_stream_error=errors.append)
    _BoxMixin._box_scrambled_end(win)
    assert win.player.stop_btn.clicks == 1
    assert win.player.stopped_directly == 0
    assert errors == [tr("diag_scrambled")]
