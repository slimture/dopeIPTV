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


def test_grid_navigation_selection_and_progress():
    """The interactive layer: arrow-key cell navigation lands on the on-air
    block first, moves along rows and across rows keeping the time slot,
    the description panel fills from the XMLTV desc, the on-air programme
    gets a progress fill, and the day-jump helpers don't blow up."""
    app = _app()
    import time as _time

    from PyQt6.QtCore import QSettings

    from dopeiptv.providers.client import DemoClient
    from dopeiptv.ui.epg_grid import EpgGridDialog
    from dopeiptv.ui.main_window import MainWindow

    settings = QSettings("dopeiptv-test", "epg-grid-nav")
    settings.clear()
    w = MainWindow(DemoClient(), settings)
    now = _time.time()

    def fake_programmes(ch, a, b, _now=now):
        if ch.get("stream_id") != 1:
            return []
        return [
            {"title": "Earlier", "description": "",
             "start_timestamp": _now - 5400, "stop_timestamp": _now - 1800},
            {"title": "On Air", "description": "A described programme.",
             "start_timestamp": _now - 1800, "stop_timestamp": _now + 1800},
            {"title": "Later", "description": "",
             "start_timestamp": _now + 1800, "stop_timestamp": _now + 5400},
        ]

    w.xmltv.programmes_in = fake_programmes
    chans = [
        {"name": "One", "stream_id": 1, "num": 1, "epg_channel_id": "one.se"},
        {"name": "Two", "stream_id": 2, "num": 2},   # no EPG -> filler block
    ]
    d = EpgGridDialog(w, chans)
    d.resize(1200, 600)
    d.show()
    app.processEvents()

    assert len(d._rows) == 2
    assert len(d._rows[0][1]) == 3        # three programme blocks
    assert len(d._rows[1][1]) == 1        # the no-EPG filler

    # First arrow press selects the on-air block.
    d._nav(1, 0)
    assert d._selected["prog"]["title"] == "On Air"
    assert d.desc.isVisible() and "described" in d.desc.text()

    # Right moves to the next programme; its empty desc hides the panel.
    d._nav(1, 0)
    assert d._selected["prog"]["title"] == "Later"
    assert not d.desc.isVisible()

    # Down lands on the no-EPG row's filler; up returns to a programme.
    d._nav(0, 1)
    assert d._selected["channel"]["name"] == "Two"
    assert d._selected["prog"] is None
    d._nav(0, -1)
    assert d._selected["channel"]["name"] == "One"

    # The on-air programme carries exactly one progress fill.
    assert len(d._progress) == 1
    d._tick()                              # live refresh must not raise
    assert len(d._progress) == 1

    # Day jumps and prime-time jump are safe no-crash scrolls.
    d._scroll_hours(24)
    d._scroll_hours(-24)
    d._scroll_tonight()

    d.deleteLater()
    w.deleteLater()
    app.processEvents()
