"""The seek bar's hover tip (media/seek_slider.py): the time under the
pointer just below the bar - on macOS a small window of its own, since the
picture there can be a native view over the window's widgets and a label
among them went behind the timeline; elsewhere a label in the window."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QMouseEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

from dopeiptv.media import seek_slider  # noqa: E402


def _hover(own_window: bool, monkeypatch):
    monkeypatch.setattr(seek_slider, "TIP_WINDOW", own_window)
    app = QApplication.instance() or QApplication([])
    win = QWidget()
    win.resize(600, 300)
    lay = QVBoxLayout(win)
    bar = seek_slider._SeekSlider()
    bar.setRange(0, 1000)
    bar.set_time_provider(lambda f: f"21:{int(f * 59):02d}")
    lay.addStretch(1)
    lay.addWidget(bar)
    win.show()
    app.processEvents()
    x = bar.width() // 2
    ev = QMouseEvent(QEvent.Type.MouseMove, QPointF(x, 5), QPointF(bar.mapToGlobal(QPoint(x, 5))),
                     Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    bar.mouseMoveEvent(ev)
    app.processEvents()
    tip = bar._hover_tip
    base = bar.mapTo(win, QPoint(x, bar.height()))
    want = QPoint(max(4, min(base.x() - tip.width() // 2, win.width() - tip.width() - 4)),
                  min(base.y() + 6, win.height() - tip.height() - 4))
    return app, win, bar, tip, want


def test_on_macos_the_tip_is_a_window_of_its_own_over_everything(monkeypatch):
    app, win, bar, tip, want = _hover(True, monkeypatch)
    assert tip.isWindow() and tip.isVisible() and tip.text().startswith("21:")
    assert tip.windowFlags() & Qt.WindowType.ToolTip
    assert tip.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert tip.pos() == win.mapToGlobal(want)
    bar.leaveEvent(QEvent(QEvent.Type.Leave))
    assert not tip.isVisible()
    win.close()


def test_elsewhere_the_tip_is_a_label_in_the_window_as_before(monkeypatch):
    app, win, bar, tip, want = _hover(False, monkeypatch)
    assert not tip.isWindow() and tip.parent() is win and tip.isVisible()
    assert tip.pos() == want
    win.close()
