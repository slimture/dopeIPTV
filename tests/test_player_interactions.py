"""Exercise pointer resizing, repeated dock cycles, DVR idle and all locales."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


_CHILD = r'''
import os
import tempfile
from unittest.mock import patch
from PyQt6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget
from dopeiptv import i18n
from dopeiptv.core.local_buffer import BufferWindow
from dopeiptv.media.embedded import EmbeddedPlayer
from dopeiptv.ui.mw_popout import _PopoutMixin
from dopeiptv.ui.player_resize import PlayerResizeHandle

app = QApplication([])
tmp = tempfile.TemporaryDirectory()

class Host(QMainWindow, _PopoutMixin):
    def __init__(self):
        super().__init__()
        self.settings = QSettings(os.path.join(tmp.name, 'settings.ini'),
                                  QSettings.Format.IniFormat)
        self._popout_win = self._popout_placeholder = None
        self._player_fs = False
        self._det = QWidget()
        layout = QVBoxLayout(self._det)
        layout.addWidget(QLabel('Header'))
        self.player = EmbeddedPlayer(settings=self.settings)
        layout.addWidget(self.player)
        info = QWidget()
        info.setMinimumHeight(96)
        self._player_resize = PlayerResizeHandle(self.player, info, self.settings, self._det)
        layout.addWidget(self._player_resize)
        layout.addWidget(info, 1)
        self.setCentralWidget(self._det)
        self.resize(900, 850)

    def _exit_player_fullscreen(self):
        pass

def settle():
    for _ in range(10):
        app.processEvents()

def send(widget, kind, global_pos, button, buttons):
    event = QMouseEvent(kind, QPointF(widget.mapFromGlobal(global_pos)),
                        QPointF(global_pos), button, buttons,
                        Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)

h = Host()
h.show()
settle()
p, grip = h.player, h._player_resize

with patch('dopeiptv.ui.mw_popout._use_mirror_popout', return_value=False):
    for cycle in range(3):
        grip._preferred = 180
        grip.apply_height()
        settle()
        index = h._det.layout().indexOf(p)
        h._toggle_popout()
        settle()
        win = h._popout_win
        win.setGeometry(40, 40, 640, 480)
        settle()
        before = win.size()
        corner = win.mapToGlobal(QPoint(win.width()-2, win.height()-2))
        send(p.bar, QEvent.Type.MouseButtonPress, corner,
             Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
        assert win._edge_drag_active
        target = corner + QPoint(100, 80)
        send(p.bar, QEvent.Type.MouseMove, target,
             Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
        send(p.bar, QEvent.Type.MouseButtonRelease, target,
             Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
        settle()
        assert win.width() >= before.width()+95, (before, win.size())
        assert win.height() >= before.height()+75, (before, win.size())
        assert not p._click_timer.isActive(), 'edge resize must not queue pause'
        h._exit_popout()
        settle()
        assert h._det.layout().indexOf(p) == index
        assert h._det.layout().stretch(index) == 0
        assert grip.isVisible()
        before = p.height()
        start = grip.mapToGlobal(QPoint(grip.width()//2, grip.height()//2))
        send(grip, QEvent.Type.MouseButtonPress, start,
             Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
        target = start + QPoint(0, 120)
        send(grip, QEvent.Type.MouseMove, target,
             Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
        send(grip, QEvent.Type.MouseButtonRelease, target,
             Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
        settle()
        assert p.height() >= before+100, (cycle, before, p.height())

# The polling loop must not resurrect an idle-hidden DVR row. Activity does.
p.local_buffer_window = lambda: BufferWindow(0, 60)
p._local_buffer_ready = False
p._local_buffer_hide_timer.setInterval(40)
p._update_local_buffer(20)
assert not p.local_buffer_bar.isHidden()
QTest.qWait(100)
assert p.local_buffer_bar.isHidden(), 'DVR row must hide after idle'
p._update_local_buffer(21)
assert p.local_buffer_bar.isHidden(), 'position polling must not reveal it'
QApplication.sendEvent(p.video, QMouseEvent(QEvent.Type.MouseMove, QPointF(40,40),
    QPointF(p.video.mapToGlobal(QPoint(40,40))), Qt.MouseButton.NoButton,
    Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
assert not p.local_buffer_bar.isHidden(), 'video hover must reveal DVR'
p.local_buffer_seek.dragging = True
p._hide_local_buffer()
assert not p.local_buffer_bar.isHidden(), 'must stay visible while scrubbing'
p.local_buffer_seek.dragging = False

assert len(i18n.LANGUAGES) == 27
for code, _name in i18n.LANGUAGES:
    i18n.set_language(code)
    p.retranslate_ui()
    assert p.local_buffer_live.text() == i18n.tr('mv_live'), code
    assert p.local_buffer_seek.toolTip() == i18n.tr('tooltip_timeshift'), code
i18n.set_language('en')
print('PLAYER_INTERACTIONS_27_LANGUAGES_OK', flush=True)
os._exit(0)
'''


def test_pointer_resize_dock_idle_and_language_switching():
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
        capture_output=True, text=True, timeout=60)
    assert "PLAYER_INTERACTIONS_27_LANGUAGES_OK" in proc.stdout, (
        f"stdout={proc.stdout!r}\nstderr={proc.stderr[-3000:]!r}")
