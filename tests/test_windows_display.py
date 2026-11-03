"""Portable flags and docked-player sizing, without a provider or GPU."""

from __future__ import annotations

import pytest

from PyQt6.QtCore import QPointF, QSettings, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import (
    QApplication, QLabel, QListWidget, QListWidgetItem, QStyleOptionViewItem,
    QVBoxLayout, QWidget,
)

from dopeiptv.media.embedded import EmbeddedPlayer
from dopeiptv.ui.category_flags import category_flag, flag_icon
from dopeiptv.ui.channel_list import CategoryColorDelegate
from dopeiptv.ui.player_resize import PlayerResizeHandle


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("label,code", [
    ("IT | Sport", "it"), ("[IT] Cinema", "it"), ("IT", "it"),
    ("UK: News", "gb"), ("USA Movies", "us"), ("DE - TV", "de"),
    ("FR|Cinema", "fr"), ("🇮🇹 Italia", "it"), ("🇯🇵 Japan", "jp"),
    ("ITV", None), ("Sports", None), ("4K UHD", None),
])
def test_category_country_detection(label, code):
    assert category_flag(label)[0] == code


def test_flag_delegate_keeps_provider_text_and_paints_svg(app):
    view = QListWidget()
    item = QListWidgetItem("🇮🇹 IT | Cinema", view)
    option = QStyleOptionViewItem()
    delegate = CategoryColorDelegate(view)
    delegate.initStyleOption(option, view.model().index(0, 0))
    assert (option.decorationSize.width(), option.decorationSize.height()) == (16, 12)
    assert option.text == "IT | Cinema"
    assert item.text() == "🇮🇹 IT | Cinema"
    image = flag_icon("it").pixmap(24, 18).toImage()
    assert not image.isNull()
    assert image.pixelColor(3, 9).green() > image.pixelColor(3, 9).red()
    assert image.pixelColor(20, 9).red() > image.pixelColor(20, 9).green()


def test_resize_drag_persists_and_clamps_without_losing_aspect(app, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    parent = QWidget()
    layout = QVBoxLayout(parent)
    player = QWidget()
    player.VIDEO_BOX_HEIGHT = 260
    player._fs_ui = player._popout_mode = False
    player.video = QWidget(player)
    player.bar = QLabel("Controls", player)
    pl = QVBoxLayout(player)
    pl.addWidget(player.video)
    pl.addWidget(player.bar)
    player._lock_video_box = lambda: EmbeddedPlayer._lock_video_box(player)
    layout.addWidget(player)
    info = QWidget()
    info.setMinimumHeight(96)
    grip = PlayerResizeHandle(player, info, settings, parent)
    layout.addWidget(grip)
    layout.addWidget(info, 1)
    parent.resize(600, 800)
    parent.show()
    app.processEvents()
    grip.apply_height()
    before = player._docked_video_height

    def mouse(kind, y, button, buttons):
        return QMouseEvent(kind, QPointF(10, 5), QPointF(10, y),
                           button, buttons, Qt.KeyboardModifier.NoModifier)

    grip.mousePressEvent(mouse(QMouseEvent.Type.MouseButtonPress, 300,
                              Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
    grip.mouseMoveEvent(mouse(QMouseEvent.Type.MouseMove, 400,
                             Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
    grip.mouseReleaseEvent(mouse(QMouseEvent.Type.MouseButtonRelease, 400,
                                Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
    assert player._docked_video_height == before + 100
    assert int(settings.value("docked_video_height")) == before + 100
    parent.resize(600, 430)
    app.processEvents()
    grip.apply_height()
    assert player._docked_video_height < before + 100
    assert info.height() >= info.minimumHeight()
    assert int(settings.value("docked_video_height")) == before + 100
    parent.close()


def test_video_options_keep_original_aspect():
    class Mpv(dict):
        pass

    class Settings:
        def value(self, key, default=None):
            return default

    from types import SimpleNamespace
    mpv = Mpv()
    player = SimpleNamespace(video=SimpleNamespace(mpv=mpv), _settings=Settings())
    EmbeddedPlayer.apply_default_options(player)
    assert mpv["keepaspect"] is True
    assert mpv["video-aspect-override"] == "-1"
