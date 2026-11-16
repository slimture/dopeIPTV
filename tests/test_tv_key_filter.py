"""TV mode's key filter and the pop-out's: Qt's own QWidget.window().

TV mode's page and Home keep the main window in an attribute called
``window``, which shadows QWidget.window(). A key with Ctrl (Ctrl+Shift+T,
to leave TV mode) going up the parents from the page reached it and raised
TypeError in the filter - logged as a crash, with an error report offered.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_APP = None


def _app():
    from PyQt6.QtWidgets import QApplication
    global _APP
    _APP = QApplication.instance() or QApplication([])   # kept alive
    return _APP


def test_the_key_filter_survives_a_widget_that_keeps_a_window_attribute():
    from PyQt6.QtWidgets import QWidget

    from dopeiptv.ui import mw_tv
    _app()

    class Page(QWidget):
        def __init__(self, main):
            super().__init__()
            self.window = main                     # as TvPage and Home do

    class Win(QWidget, mw_tv._TvMixin):
        pass

    win = Win()
    page = Page(win)
    page.setParent(win)
    assert win._tv_takes(page) is True             # it raised TypeError
    assert win._tv_takes(Page(win)) is False       # another window's
