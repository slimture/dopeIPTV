"""EPG guide grid: channels without any EPG data must still be playable.

The grid's hit-testing keys off data(0) payloads, which only programme
blocks used to carry - a channel with no guide rows had no clickable
surface at all (its row is empty and its name cell was inert), so it
simply could not be played from the guide.
"""
import sys

import pytest


def _app():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv[:1])


def test_no_epg_channel_is_clickable_and_playable():
    app = _app()
    from PyQt6.QtCore import QPointF, QSettings

    from dopeiptv.providers.client import DemoClient
    from dopeiptv.ui.epg_grid import EpgGridDialog
    from dopeiptv.ui.main_window import MainWindow

    settings = QSettings("dopeiptv-test", "epg-grid")
    settings.clear()
    w = MainWindow(DemoClient(), settings)
    chans = [
        {"name": "With EPG", "stream_id": 1, "num": 1,
         "epg_channel_id": "x.se"},
        {"name": "No EPG", "stream_id": 2, "num": 2},
    ]
    d = EpgGridDialog(w, chans)
    d.resize(1200, 600)
    d.show()
    app.processEvents()

    # The name cell in the pinned channel column selects the channel...
    d._select_at(QPointF(50, d.HEADER_H + d.ROW_H + 10))
    assert d._selected and d._selected["channel"]["name"] == "No EPG"
    assert d._selected["prog"] is None
    assert d.play_btn.isEnabled()

    # ...and so does the filler block spanning its empty timeline.
    d._select_at(QPointF(d.CH_COL_W + 300, d.HEADER_H + d.ROW_H + 10))
    assert d._selected["channel"]["name"] == "No EPG"

    # Play tunes the channel live.
    tuned = []
    w.tune_from_guide = lambda ch: tuned.append(ch["name"])
    d._play_selected()
    assert tuned == ["No EPG"]
    d.deleteLater()
    w.deleteLater()
    app.processEvents()
