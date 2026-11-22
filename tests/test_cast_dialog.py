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
    import dopeiptv.ui.cast_dialog as cc
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
    from dopeiptv.ui.cast_dialog import CastDialog
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
    from dopeiptv.ui.cast_dialog import CastDialog
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


def test_the_tv_used_last_is_on_top_picked_and_named_on_the_button():
    """One click opens the window, one more (or Enter) casts to the TV used
    last - for a channel and for a film each, a TV that is gone just not
    offered first."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtWidgets import QApplication

    import dopeiptv.ui.cast_dialog as cc
    win = _window()
    win.settings.setValue("cast_devices", "Living room\nBedroom\nKitchen")
    cc.remember_device(win.settings, True, "Bedroom")
    cc.remember_device(win.settings, False, "Kitchen")
    win._cast_device = None
    live = cc.CastDialog(win, "http://p/live/u/pw/1.m3u8", "SVT1", probe=False,
                         managing=True, live=True)
    assert [live.list.item(i).text() for i in range(live.list.count())][0] == "Bedroom"
    assert live.list.currentItem().text() == "Bedroom"
    assert live.cast_btn.text() == cc.tr("cast_cast_to", name="Bedroom")
    assert live.cast_btn.isDefault()
    film = cc.CastDialog(win, "http://p/movie/u/pw/2.mkv", "Sintel", probe=False,
                         managing=True)
    assert film.list.currentItem().text() == "Kitchen"
    # Enter on the list goes to the default button: the cast to that TV.
    went = []
    film._cast = lambda: went.append(film.list.currentItem().text())
    film.cast_btn.clicked.disconnect()
    film.cast_btn.clicked.connect(film._cast)
    film.show()
    film.list.setFocus()
    QApplication.sendEvent(film.list, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return,
                                                Qt.KeyboardModifier.NoModifier))
    assert went == ["Kitchen"]
    film.hide()
    # A TV that is gone is just not first; another picked is named instead.
    cc.remember_device(win.settings, True, "Attic")
    gone = cc.CastDialog(win, "http://p/live/u/pw/1.m3u8", "SVT1", probe=False,
                         managing=True, live=True)
    assert gone.list.item(0).text() == "Living room"
    gone.list.setCurrentRow(2)
    assert gone.cast_btn.text() == cc.tr("cast_cast_to", name="Kitchen")
    win.deleteLater()


def test_what_is_kept_of_the_tv_used_last():
    from PyQt6.QtCore import QSettings

    import dopeiptv.ui.cast_dialog as cc
    s = QSettings("dopeiptv-test", "cast-last")
    s.clear()
    assert cc.last_device(s, True) == "" and cc.last_device(s, False) == ""
    cc.remember_device(s, False, "Kodi (Kodi)")
    # A channel never cast yet goes to the TV used last at all.
    assert cc.last_device(s, True) == "Kodi (Kodi)"
    cc.remember_device(s, True, "Living room")
    assert cc.last_device(s, True) == "Living room" and cc.last_device(s, False) == "Kodi (Kodi)"
    s.setValue(cc.LAST_DEVICE_KEY, "not json")
    assert cc.last_device(s, True) == ""
    assert cc.last_first(["A", "B", "C"], "C") == ["C", "A", "B"]
    assert cc.last_first(["A", "B"], "Z") == ["A", "B"]
    assert cc.cast_button_text(None) == cc.tr("cast_cast")
    assert "…" in cc.cast_button_text("x" * 60)
    s.clear()


def test_cast_button_keeps_its_whole_words():
    """The button names the device picked ("Cast to AirPlay (Apple TV)"):
    the window grows to it, never cuts it to its middle. A minimum width
    of the window's own had kept its layout's out, so the button was
    squeezed to less than its words."""
    from PyQt6.QtWidgets import QApplication, QListWidgetItem
    win, dlg = _dialog(managing=True)
    dlg.show()
    item = QListWidgetItem("AirPlay (Apple TV)")
    dlg.list.addItem(item)
    dlg.list.setCurrentItem(item)
    for _ in range(5):
        QApplication.processEvents()
    dlg.layout().activate()
    assert dlg.cast_btn.text().endswith("AirPlay (Apple TV)")
    assert dlg.minimumSizeHint().width() >= dlg.cast_btn.sizeHint().width()
    assert dlg.minimumWidth() >= dlg.layout().minimumSize().width()
    assert dlg.list.minimumWidth() >= 300      # still starts as wide as before
    dlg.close()
    win.deleteLater()
