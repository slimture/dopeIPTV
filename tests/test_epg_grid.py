"""EPG guide grid regressions.

Covers: channels without EPG data are clickable/playable; arrow-key cell
navigation, description panel and progress fills; and the rebuild when the
guide finishes loading after the dialog opened.

Runs in a subprocess like the multiview test: the dialog needs a real
MainWindow (whose embedded player owns an offscreen QOpenGLWidget), and
those abort at Qt's interpreter-exit teardown when several accumulate in
one process - it also destabilises later Qt tests in the suite. We assert
on the child's success marker and ignore its exit status.
"""
import os
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QPointF, QSettings
from PyQt6.QtWidgets import QApplication

from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.epg_grid import EpgGridDialog
from dopeiptv.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])
settings = QSettings("dopeiptv-test", "epg-grid-child")
settings.clear()
w = MainWindow(DemoClient(), settings)
now = time.time()

# ---- 1. A channel without EPG is clickable and playable -------------------
chans = [
    {"name": "With EPG", "stream_id": 1, "num": 1, "epg_channel_id": "x.se"},
    {"name": "No EPG", "stream_id": 2, "num": 2},
]
d = EpgGridDialog(w, chans)
d.resize(1200, 600)
d.show()
app.processEvents()

# The dialog opens scrolled to "now" (wall-clock dependent), which shifts
# the pinned channel column in scene coords. Real clicks go through
# mapToScene so the app is fine - but this test clicks raw scene points,
# so pin the view at origin first to make them deterministic.
d.view.horizontalScrollBar().setValue(0)
d.view.verticalScrollBar().setValue(0)
app.processEvents()

d._select_at(QPointF(50, d.HEADER_H + d.ROW_H + 10))     # name cell, row 1
assert d._selected and d._selected["channel"]["name"] == "No EPG"
assert d._selected["prog"] is None
assert d.play_btn.isEnabled()
d._select_at(QPointF(d.CH_COL_W + 300, d.HEADER_H + d.ROW_H + 10))
assert d._selected["channel"]["name"] == "No EPG"

tuned = []
w.tune_from_guide = lambda ch: tuned.append(ch["name"])
d._play_selected()
assert tuned == ["No EPG"], tuned
d.deleteLater(); app.processEvents()

# ---- 2. Navigation, description panel, progress, day jumps ----------------
def fake_programmes(ch, a, b):
    if ch.get("stream_id") != 1:
        return []
    return [
        {"title": "Earlier", "description": "",
         "start_timestamp": now - 5400, "stop_timestamp": now - 1800},
        {"title": "On Air", "description": "A described programme.",
         "start_timestamp": now - 1800, "stop_timestamp": now + 1800},
        {"title": "Later", "description": "",
         "start_timestamp": now + 1800, "stop_timestamp": now + 5400},
    ]

w.xmltv.programmes_in = fake_programmes
d = EpgGridDialog(w, chans)
d.resize(1200, 600)
d.show()
app.processEvents()

assert len(d._rows) == 2
assert len(d._rows[0][1]) == 3          # three programme blocks
assert len(d._rows[1][1]) == 1          # the no-EPG filler

d._nav(1, 0)                            # first press -> the on-air block
assert d._selected["prog"]["title"] == "On Air"
assert d.desc.isVisible() and "described" in d.desc.text()
d._nav(1, 0)                            # right -> next programme
assert d._selected["prog"]["title"] == "Later"
assert not d.desc.isVisible()
d._nav(0, 1)                            # down -> the no-EPG row's filler
assert d._selected["channel"]["name"] == "No EPG"
assert d._selected["prog"] is None
d._nav(0, -1)                           # up -> back to a programme
assert d._selected["channel"]["name"] == "With EPG"

# Exactly one card is marked on-air ("now" state), and the live tick
# (which rolls the highlight forward) must not raise.
now_cards = [rb for _c, blocks in d._rows for rb in blocks
             if rb.get("_state") == "now"]
assert len(now_cards) == 1, len(now_cards)
d._tick()

d._scroll_hours(24); d._scroll_hours(-24); d._scroll_tonight()
d.deleteLater(); app.processEvents()

# ---- 3. Rebuild when the EPG finishes loading after open ------------------
state = {"loaded": False}
w.xmltv.is_loaded = lambda: state["loaded"]
w.xmltv.programmes_in = lambda ch, a, b: ([] if not state["loaded"] else [
    {"title": "P", "description": "",
     "start_timestamp": now - 600, "stop_timestamp": now + 600}])

d = EpgGridDialog(w, [{"name": "One", "stream_id": 1, "num": 1,
                       "epg_channel_id": "one.se"}])
app.processEvents()
assert d._epg_poll.isActive()                        # polling while unloaded
assert d._rows[0][1][0]["data"]["prog"] is None      # loading band
state["loaded"] = True
d._maybe_reload_epg()
assert not d._epg_poll.isActive()
assert d._rows[0][1][0]["data"]["prog"]["title"] == "P"
d.deleteLater(); app.processEvents()

# ---- 4. Filter finds a channel beyond the MAX_CHANNELS display cap --------
w.xmltv.programmes_in = lambda ch, a, b: []
many = [{"name": f"Filler {i}", "stream_id": 1000 + i, "num": i}
        for i in range(EpgGridDialog.MAX_CHANNELS + 50)]
many.append({"name": "V Sport Premium SE", "stream_id": 42, "num": 999})
d = EpgGridDialog(w, many)
app.processEvents()
# Unfiltered: capped to MAX_CHANNELS, target (last) not shown.
assert len(d._rows) == EpgGridDialog.MAX_CHANNELS
assert not any(c.get("name") == "V Sport Premium SE"
               for c, _b in d._rows)
# Filtering searches the FULL list, so the target now appears.
d.filter.setText("v sport")
app.processEvents()
assert any(c.get("name") == "V Sport Premium SE" for c, _b in d._rows)
d.deleteLater(); app.processEvents()

print("EPG_GRID_OK")
"""


def test_epg_grid_interactions():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    # One retry: the child needs an offscreen OpenGL context (the MainWindow
    # embeds a QOpenGLWidget), and creating one can fail spuriously when the
    # machine is under heavy load - the child then dies before printing its
    # marker without any assertion having failed. A genuine regression fails
    # both attempts identically, so the retry can't mask one.
    for _attempt in (1, 2):
        proc = subprocess.run(
            [sys.executable, "-c", _CHILD], capture_output=True, text=True,
            env=env, cwd=_REPO_ROOT, timeout=180)
        if "EPG_GRID_OK" in proc.stdout:
            return
    assert "EPG_GRID_OK" in proc.stdout, (
        f"EPG grid checks failed\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr[-2000:]!r}")
