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


def _dialog(**kw):
    """Build the dialog. No libass question any more: a text subtitle is
    handed to the receiver as WebVTT beside the picture, which any ffmpeg
    can write, so every subtitle in the stream can be offered."""
    import dopeiptv.providers.chromecast as cc
    win = _window()
    kw.setdefault("probe", False)       # never open the stream from a test
    dlg = cc.CastDialog(win, "http://p/live/u/pw/1.m3u8", "SVT1", **kw)
    return win, dlg


def test_dialog_builds_from_a_row():
    win, dlg = _dialog()
    assert dlg.windowTitle()
    # No device picked yet, so there is nothing to ask the question about -
    # and an unfilled "{name} is an older Chromecast" is worse than nothing.
    assert dlg.kind_box.isHidden() is True
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
    about the Google TV in the other. Three tiers, named after what the
    device is - nobody knows their receiver's maximum profile level, and
    everybody knows which one they bought."""
    win = _window()
    win.settings.setValue("cast_devices", "Living room\nBedroom")
    from dopeiptv.providers.chromecast import CastDialog
    dlg = CastDialog(win, "http://p/live/u/pw/1.m3u8", "SVT1",
                     probe=False, managing=True)
    assert dlg.quality() == "original"
    assert dlg.kind_box.count() == 3
    # It names the device it is about - the dialog is opened once per thing
    # you cast, so an unattached question reads as being about this cast.
    assert "Living room" in dlg.kind_label.text()
    assert dlg.quality_note.isHidden() is False

    dlg.kind_box.setCurrentIndex(dlg.kind_box.findData("oldest"))
    assert dlg.quality() == "oldest"
    assert win.settings.value("cast_quality_Living room") == "oldest"

    dlg.list.setCurrentRow(1)                   # Bedroom, untouched
    assert dlg.quality() == "original"
    dlg.kind_box.setCurrentIndex(dlg.kind_box.findData("hd"))
    assert dlg.quality() == "hd"

    dlg.list.setCurrentRow(0)                   # and back
    assert dlg.kind_box.currentData() == "oldest"
    assert dlg.quality() == "oldest"

    # A device written down before this was three choices still means what
    # its owner meant: an old receiver.
    win.settings.setValue("cast_quality_Bedroom", "720p30")
    dlg.list.setCurrentRow(1)
    assert dlg.quality() == "oldest"
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


def test_every_subtitle_in_the_stream_can_be_offered():
    """There used to be a libass question here: a text subtitle had to be
    drawn into the picture, and builds without libass could not. It is gone.
    A text subtitle now rides beside the picture as a WebVTT rendition,
    which needs no libass - so the choice is always real."""
    tracks = {"audio": [{"index": 0, "lang": "swe", "codec": "aac"}],
              "subtitle": [{"index": 0, "lang": "swe", "codec": "subrip"},
                           {"index": 1, "lang": "eng", "codec": "dvb_subtitle"}]}
    win, dlg = _dialog(tracks=tracks, managing=True)
    assert dlg.subs_box.isEnabled() is True
    assert dlg.subs_box.count() == 3            # off + both
    win.deleteLater()


def test_a_channel_is_handed_over_with_no_length():
    """What mpv answers for a live playlist is the seekable window - a minute
    or so of buffer - and it arrived here as the length of the thing. The
    manager reads a length as "this has an end", announces it to the
    television as a title, and the receiver draws a name and a progress bar
    over the picture and leaves them there.

    So the row's own kind decides, not a number read off the stream."""
    tracks = {"audio": [{"index": 0, "lang": "swe", "codec": "aac"}],
              "subtitle": [], "duration": 68.0, "height": 1080, "fps": 50.0}
    win, dlg = _dialog(tracks=tracks, managing=True, live=True)
    assert dlg.duration == 0.0
    assert dlg.height == 1080, "the rest of what mpv knows is still worth it"
    win.deleteLater()

    # A film keeps its length: on that the bar is real and can be dragged
    # with the television's own remote.
    win, dlg = _dialog(tracks=dict(tracks, duration=5400.0), managing=True)
    assert dlg.duration == 5400.0
    win.deleteLater()


def test_a_single_audio_track_is_no_choice():
    tracks = {"audio": [{"index": 0, "lang": "swe", "codec": "aac"}],
              "subtitle": []}
    win, dlg = _dialog(tracks=tracks, managing=True)
    assert dlg.audio_box.isEnabled() is False
    assert dlg.subs_box.isEnabled() is False
    win.deleteLater()
