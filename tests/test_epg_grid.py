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
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication, QGraphicsPixmapItem

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
    # Kept well inside the board's own window. It opens at the current half
    # hour minus thirty minutes, so a programme placed an hour and a half back
    # sits on that edge - and whether it lands inside depends on the seconds
    # on the clock when the test runs. It passed here and failed on CI at
    # 07:30 for no other reason.
    return [
        {"title": "Earlier", "description": "",
         "start_timestamp": now - 1500, "stop_timestamp": now - 900},
        {"title": "On Air", "description": "A described programme.",
         "start_timestamp": now - 900, "stop_timestamp": now + 900},
        {"title": "Later", "description": "",
         "start_timestamp": now + 900, "stop_timestamp": now + 3600},
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
# The window's own startup guide load runs async and, when it concludes as
# FAILED (no network on CI), sets xmltv._failed - which _epg_ready() treats
# as "concluded", so the dialog would skip the loading-band state we're
# testing. Wait for that load to conclude, then pin both flags under stubs.
deadline = time.time() + 20
while time.time() < deadline and not (w.xmltv.is_loaded()
                                      or getattr(w.xmltv, "_failed", False)):
    app.processEvents(); time.sleep(0.02)
state = {"loaded": False}
w.xmltv.is_loaded = lambda: state["loaded"]
w.xmltv._failed = False
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

# ---- 5. A logo arriving after the board scrolled lands in its row ---------
# Logos load async. The channel column is pinned by translating its group by
# the scroll offset, so a logo added later must be positioned in GROUP
# coordinates - positioning it in scene coordinates parked it hundreds of px
# left of the visible column, which is why logos only appeared the second
# time the guide was opened (cached -> callback ran during the build).
pending_logos = []
class _StubLogos:
    def get(self, url, cb):
        pending_logos.append(cb)
w.logos = _StubLogos()
w.xmltv.programmes_in = lambda ch, a, b: []
d = EpgGridDialog(w, [{"name": "Logo", "stream_id": 7, "num": 1,
                       "stream_icon": "http://example.invalid/logo.png"}])
d.resize(900, 400)
d.show()
app.processEvents()
d.view.horizontalScrollBar().setValue(600)     # pin the column away from x=0
app.processEvents()
assert pending_logos, "no logo was requested"
pm = QPixmap(38, 24); pm.fill(QColor("#ff0000"))
pending_logos[0](pm)
logo_items = [i for i in d.scene.items() if isinstance(i, QGraphicsPixmapItem)]
assert len(logo_items) == 1, logo_items
lx = logo_items[0].sceneBoundingRect().x()
col_left = d.view.mapToScene(0, 0).x()
assert col_left > 100, col_left                # the view really did scroll
assert col_left <= lx <= col_left + d.CH_COL_W, (lx, col_left)
d.deleteLater(); app.processEvents()

# ---- 6. Only the card is clickable - the title is paint on top of it ------
w.logos = None
w.xmltv.programmes_in = lambda ch, a, b: [
    {"title": "Clickable Title", "description": "",
     "start_timestamp": now - 1800, "stop_timestamp": now + 3600}]
d = EpgGridDialog(w, [{"name": "One", "stream_id": 1, "num": 1}])
d.resize(1200, 600)
d.show()
d.view.horizontalScrollBar().setValue(0)
d.view.verticalScrollBar().setValue(0)
app.processEvents()
rb = d._rows[0][1][0]
label_rect = rb["label"].sceneBoundingRect()
assert rb["label"].data(0) is None             # carries no click payload
item, data = d._hit(QPointF(label_rect.x() + 4, label_rect.center().y()))
assert item is rb["item"], item                # the card, not the text item
assert data["prog"]["title"] == "Clickable Title"
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
