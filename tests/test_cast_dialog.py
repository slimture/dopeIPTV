"""The cast dialog has to be buildable, not just correct.

Every other cast test borrows methods onto a stub or drives the manager
directly, so the dialog's own widget tree was never constructed once - and a
call to a method the widget does not have (setWordWrap on a checkbox) is not
a logic error any of them could see. It raised on the first line of
__init__'s layout code, so the dialog could not be opened at all while every
test stayed green.

So build the real thing here, offscreen, both ways it is opened: fresh from a
row, and on top of a cast that is already running.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module", autouse=True)
def _qt_app():
    from PyQt6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


class _Cast:
    """A manager that answers without a network."""

    def scan(self):
        return ["Living room", "Bedroom"]


def _window():
    from PyQt6.QtCore import QSettings, QThreadPool
    from PyQt6.QtWidgets import QWidget

    w = QWidget()                       # QDialog needs a real parent
    w.settings = QSettings("dopeiptv-test", "cast-dialog")
    w.settings.clear()
    w.pool = QThreadPool()
    w.cast = _Cast()
    w._cast_device = "Living room"
    return w


def _dialog(burn=True, **kw):
    """Build the dialog. *burn* stands in for the machine's ffmpeg: whether
    this one was built with libass decides whether a text subtitle can be
    offered at all, and the CI runner's build and a developer's differ."""
    import dopeiptv.providers.chromecast as cc
    win = _window()
    kw.setdefault("probe", False)       # never open the stream from a test
    real = cc.can_burn_subtitles
    cc.can_burn_subtitles = lambda exe=None: burn
    try:
        dlg = cc.CastDialog(win, "http://p/live/u/pw/1.m3u8", "SVT1", **kw)
    finally:
        cc.can_burn_subtitles = real
    return win, dlg


def test_dialog_builds_from_a_row():
    win, dlg = _dialog()
    assert dlg.windowTitle()
    # No device picked yet, so there is nothing to ask the question about -
    # and an unfilled "{name} is an older Chromecast" is worse than nothing.
    assert dlg.older_box.isHidden() is True
    assert dlg.quality_note.isHidden() is True
    win.deleteLater()


def test_dialog_builds_on_a_running_cast():
    """Managing mode skips discovery and lists the remembered devices."""
    from dopeiptv.providers.chromecast import CastDialog
    win, dlg = _dialog(managing=True)
    assert dlg.list.count() == 0        # nothing remembered yet
    win.settings.setValue("cast_devices", "Bedroom\nLiving room")
    dlg2 = CastDialog(win, "http://p/live/u/pw/1.m3u8", "SVT1",
                      probe=False, managing=True)
    assert dlg2.list.count() == 2
    # It opens on the device that is playing, not on the first in the list.
    assert dlg2.list.currentItem().text() == "Living room"
    win.deleteLater()


def test_the_picture_question_is_remembered_per_device():
    """It is a per-device ceiling: an old receiver in one room says nothing
    about the Google TV in the other."""
    win = _window()
    win.settings.setValue("cast_devices", "Living room\nBedroom")
    from dopeiptv.providers.chromecast import CastDialog
    dlg = CastDialog(win, "http://p/live/u/pw/1.m3u8", "SVT1",
                     probe=False, managing=True)
    assert dlg.quality() == "original"
    assert dlg.older_box.isChecked() is False
    # It names the device it is about - the dialog is opened once per thing
    # you cast, so a bare "Older Chromecast" in it reads as a question about
    # this broadcast rather than a standing property of the receiver.
    assert "Living room" in dlg.older_box.text()
    # And the note saying which receivers it is for shows with the question,
    # not after it is answered.
    assert dlg.quality_note.isHidden() is False

    dlg.older_box.setChecked(True)              # Living room
    assert dlg.quality() == "older"
    assert win.settings.value("cast_quality_Living room") == "older"

    dlg.list.setCurrentRow(1)                   # Bedroom, untouched
    assert dlg.older_box.isChecked() is False
    assert dlg.quality() == "original"

    dlg.list.setCurrentRow(0)                   # and back
    assert dlg.older_box.isChecked() is True
    assert dlg.quality() == "older"
    win.deleteLater()


def test_tracks_fill_the_boxes_without_a_probe():
    """What the player already knows is handed straight in - a second look at
    the stream costs a provider connection."""
    tracks = {"audio": [{"index": 0, "lang": "swe", "codec": "aac",
                         "default": True},
                        {"index": 1, "lang": "eng", "codec": "ac3"}],
              "subtitle": [{"index": 0, "lang": "swe", "codec": "subrip"}],
              "duration": 3600.0, "height": 1080, "fps": 50.0}
    win, dlg = _dialog(tracks=tracks, managing=True)
    assert dlg.audio_box.count() == 3          # default + two
    assert dlg.subs_box.count() == 2           # off + one
    assert dlg.audio_box.isEnabled() is True
    assert dlg.subs_box.isEnabled() is True
    assert dlg.height == 1080 and dlg.fps == 50.0
    # Default choice means a native cast: nothing is converted, so the note
    # about converting stays out of the way.
    assert dlg._chosen() == (None, None)
    assert dlg.track_note.isVisible() is False
    win.deleteLater()


def test_an_ffmpeg_without_libass_offers_no_subtitle_it_cannot_send():
    """A text subtitle reaches a Chromecast only by being drawn into the
    picture, which needs libass. Offering the choice anyway took the picture
    away and said "No such filter" in the log - so where the choice would
    have been, say why there is none."""
    tracks = {"audio": [{"index": 0, "lang": "swe", "codec": "aac"}],
              "subtitle": [{"index": 0, "lang": "swe", "codec": "subrip"}]}
    win, dlg = _dialog(burn=False, tracks=tracks, managing=True)
    assert dlg.subs_box.isEnabled() is False
    assert dlg.subs_box.count() == 1
    assert "libass" in dlg.subs_box.itemText(0)
    win.deleteLater()

    # A picture-based subtitle is drawn with overlay, which every build has.
    tracks["subtitle"] = [{"index": 0, "lang": "swe", "codec": "dvb_subtitle"}]
    win, dlg = _dialog(burn=False, tracks=tracks, managing=True)
    assert dlg.subs_box.isEnabled() is True
    assert dlg.subs_box.count() == 2           # off + the one
    win.deleteLater()


def test_a_single_audio_track_is_no_choice():
    tracks = {"audio": [{"index": 0, "lang": "swe", "codec": "aac"}],
              "subtitle": []}
    win, dlg = _dialog(tracks=tracks, managing=True)
    assert dlg.audio_box.isEnabled() is False
    assert dlg.subs_box.isEnabled() is False
    win.deleteLater()
