"""Paused while the player itself records (no app-kept buffer): mpv stops
reading the channel shortly into the pause, and the recording with it - so
the window says so, once a recording, and where the buffer that records
through a pause is turned on. A recording from the buffer says nothing."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")

from dopeiptv.i18n import tr  # noqa: E402
from dopeiptv.ui.mw_recording import _RecordingMixin  # noqa: E402


class _Host:
    _warn_pause_while_recording = _RecordingMixin._warn_pause_while_recording

    def __init__(self, record: str = "", paused: bool = True) -> None:
        self.mpv = {"stream-record": record}
        self.player = SimpleNamespace(
            _paused=paused, video=SimpleNamespace(mpv=self.mpv))
        self.toasts: list[str] = []

    def _show_toast(self, text: str, duration_ms: int = 0) -> None:
        self.toasts.append(text)


def test_a_pause_during_a_player_recording_is_named_once():
    h = _Host("/rec/svt1.ts")
    h._warn_pause_while_recording()
    assert len(h.toasts) == 1
    assert tr("setting_dvr_kept") in h.toasts[0]
    assert tr("tab_playback") in h.toasts[0]
    h._warn_pause_while_recording()             # paused again, same file
    assert len(h.toasts) == 1
    h.mpv["stream-record"] = "/rec/svt2.ts"     # a new recording
    h._warn_pause_while_recording()
    assert len(h.toasts) == 2


def test_nothing_to_say_without_a_player_recording():
    # Recording from dopeIPTV's own buffer: stream-record is not set, and
    # the recording goes on through the pause.
    h = _Host("")
    h._warn_pause_while_recording()
    assert h.toasts == []


def test_nothing_to_say_while_playing():
    # A recording started while the picture plays: no pause, no warning.
    h = _Host("/rec/svt1.ts", paused=False)
    h._warn_pause_while_recording()
    assert h.toasts == []


def test_no_player_no_warning():
    h = _Host("/rec/svt1.ts")
    h.player = None
    h._warn_pause_while_recording()
    assert h.toasts == []


def test_every_language_names_the_setting_and_the_page():
    import json
    from pathlib import Path

    import dopeiptv
    for f in sorted((Path(dopeiptv.__file__).parent / "locale").glob("*.json")):
        text = json.loads(f.read_text(encoding="utf-8"))["rec_pause_not_recorded"]
        for ph in ("{setting}", "{settings}", "{page}"):
            assert ph in text, (f.stem, ph)
