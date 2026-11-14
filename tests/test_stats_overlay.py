"""Stats for nerds over the picture: as large as the picture allows, in two
columns when one does not fit, smaller type only when even that is too
tall, a compact view a click away - and values as people write them."""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from dopeiptv.media.embedded import _StatsLabel, aspect_name, on_off  # noqa: E402

STREAM = [("Video", "h264 / 1920×1080 / 50.0 fps"), ("Audio", "aac / 48000 Hz / 2ch / swe"),
          ("HW dec", "software"), ("A/V sync", "0.000 s"), ("Dropped", "38"),
          ("Timing", "0 mistimed / 0 delayed"), ("FPS", "50.0"), ("Bitrate", "4253 kbps"),
          ("Cache", "10.0 s"), ("Net", "543 KB/s"), ("Format", "mpegts"), ("Protocol", "http")]
NERD = [("Colour", "bt.709 / bt.709 / bt.1886"), ("Pixels", "yuv420p · 1920×1080 · 16:9"),
        ("Deinterlace", "off"), ("Audio out", "aac · floatp"), ("Passthrough", "off"),
        ("Container", "mpegts · demuxer lavf"), ("Source", "xtream"), ("User agent", "dopeIPTV/1.0"),
        ("Proxy", "off")]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def label(app):
    host = QWidget()
    host.resize(2000, 1200)
    lbl = _StatsLabel(host)
    yield lbl
    host.deleteLater()


def test_fullscreen_gets_large_type_in_one_column(label):
    label.lay_out([STREAM, NERD], 1904, 1064)          # a 1080p screen
    assert (label.columns, label.font_px) == (1, 15)
    assert label.height() <= 1064 and label.width() <= 1904


def test_a_small_picture_gets_two_columns_before_smaller_type(label):
    # One column at the smallest comfortable type is taller than this picture
    # (the report: the box covered the whole video): two columns instead.
    label.lay_out([STREAM, NERD], 1100, 280)
    assert (label.columns, label.font_px) == (2, 10)
    assert label.height() <= 280 and label.width() <= 1100
    # The type grows with the picture.
    sizes = []
    for h in (300, 500, 700, 900, 1060):
        label.lay_out([STREAM, NERD], 1900, h)
        sizes.append(label.font_px)
    assert sizes == sorted(sizes) and sizes[-1] > sizes[0]


@pytest.mark.parametrize("w, h", [(1904, 1064), (1264, 700), (900, 500), (640, 360), (480, 270)])
def test_it_never_outgrows_the_picture_it_can_fit_in(label, w, h):
    label.lay_out([STREAM, NERD], w, h)
    if label.font_px > 9:
        assert label.height() <= h and label.width() <= w, (w, h, label.size())


def test_a_click_shows_the_few_lines_most_looked_at(label):
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    seen = []
    label.toggled.connect(lambda: seen.append(label.compact))
    label.lay_out([STREAM, NERD], 1904, 1064)
    assert "Colour" in label.text() and "Protocol" in label.text()
    full = label.height()
    pos = QPointF(5, 5)
    label.mousePressEvent(QMouseEvent(QMouseEvent.Type.MouseButtonPress, pos, pos, Qt.MouseButton.LeftButton,
                                      Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    label.lay_out([STREAM, NERD], 1904, 1064)
    assert seen == [True] and "Colour" not in label.text() and "Protocol" not in label.text()
    assert all(k in label.text() for k in _StatsLabel.COMPACT) and label.height() < full


def test_values_as_people_write_them():
    assert aspect_name(1.7777777777777777) == "16:9"
    assert aspect_name("1.3333333") == "4:3"
    assert aspect_name(2.3333) == "21:9"
    assert aspect_name(1.66) == "1.66:1"
    assert aspect_name("—") == "—"
    assert (on_off(True), on_off(False), on_off("yes"), on_off("no"), on_off("auto")) == ("on", "off", "on", "off", "auto")


def test_names_and_values_are_escaped(label):
    label.lay_out([[("User agent", "<b>x</b> & y")]], 1000, 600)
    assert "&lt;b&gt;x&lt;/b&gt; &amp; y" in label.text()
