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

# The DVR row shows on activity only: never by itself - not even when the
# buffer first becomes available - and never for a pointer that did not move.
p.local_buffer_window = lambda: BufferWindow(0, 60)
p._local_buffer_ready = False
p._local_buffer_hide_timer.setInterval(40)
p._update_local_buffer(20)
assert p.local_buffer_bar.isHidden(), 'polling must not show the DVR row'
def hover(x):
    QApplication.sendEvent(p.video, QMouseEvent(QEvent.Type.MouseMove,
        QPointF(x, 40), QPointF(p.video.mapToGlobal(QPoint(x, 40))),
        Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier))
hover(40)
assert not p.local_buffer_bar.isHidden(), 'video hover must reveal DVR'
assert p.local_buffer_bar.parent() is p and p.local_buffer_bar.y() > 0, \
    'the DVR row floats over the video'
QTest.qWait(100)
assert p.local_buffer_bar.isHidden(), 'DVR row must hide after idle'
p._update_local_buffer(21)
assert p.local_buffer_bar.isHidden(), 'position polling must not reveal it'
hover(40)
assert p.local_buffer_bar.isHidden(), 'a resting pointer is no hover'
hover(60)
assert not p.local_buffer_bar.isHidden(), 'a moved pointer reveals it'
p.local_buffer_seek.dragging = True
p._hide_local_buffer()
assert not p.local_buffer_bar.isHidden(), 'must stay visible while scrubbing'
p.local_buffer_seek.dragging = False

# A catch-up channel watched live: the DVR row and the timeshift timeline
# take turns in one place - never one above the other - and the switch on
# either picks which; the choice is kept.
p._local_buffer_enabled = True    # a live channel, played with one
p.set_seek_mode('timeline')
p.enter_timeshift(180)
assert p.ts_timeline.isHidden(), 'no timeline first where the DVR row comes'
p._update_local_buffer(22)
assert not p._dvr_switch.isHidden() and not p._ts_switch.isHidden()
assert not p.local_buffer_time.text().startswith('DVR'), \
    'the lit switch names the row'
def one_bar():
    shown = [b for b in (p.local_buffer_bar, p.ts_timeline) if not b.isHidden()]
    assert len(shown) == 1, 'exactly one bar over the video'
    return shown[0]
hover(80)
assert one_bar() is p.local_buffer_bar
dvr_bottom = p.local_buffer_bar.geometry().bottom()
p._dvr_switch.buttons['timeshift'].click()
assert one_bar() is p.ts_timeline
assert abs(p.ts_timeline.geometry().bottom() - dvr_bottom) <= 2, 'same place'
assert h.settings.value('rewind_bar_view') == 'timeshift'
hover(100)
hover(120)
assert one_bar() is p.ts_timeline, 'hover keeps the chosen bar'
p._ts_switch.buttons['dvr'].click()
assert one_bar() is p.local_buffer_bar
assert h.settings.value('rewind_bar_view') == 'dvr'
p._dvr_switch.buttons['timeshift'].click()
p._relative_seek(-10)
assert one_bar() is p.local_buffer_bar, 'a seek in the buffer shows the buffer'
# Fullscreen has the timeline alone: no switch there.
p.set_fullscreen_ui(True)
assert p._ts_switch.isHidden()
p.set_fullscreen_ui(False)
assert not p._ts_switch.isHidden()
# A plain live channel has no timeline: no switch, and the row says DVR.
p.set_seek_mode('live')
p._update_local_buffer(22)
assert p._dvr_switch.isHidden() and p._ts_switch.isHidden()
assert p.local_buffer_time.text().startswith('DVR ')

assert len(i18n.LANGUAGES) == 27
for code, _name in i18n.LANGUAGES:
    i18n.set_language(code)
    p.retranslate_ui()
    assert p.local_buffer_live.text() == i18n.tr('mv_live'), code
    assert p.local_buffer_seek.toolTip() == i18n.tr('tooltip_timeshift'), code
    assert p._ts_switch.buttons['timeshift'].text() == \
        i18n.tr('player_timeshift'), code
    assert p._dvr_switch.buttons['dvr'].toolTip() == i18n.tr('bar_dvr_tip'), code
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
