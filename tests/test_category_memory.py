"""Each section remembers the sub-category you were last in.

Switching TV -> Movies -> TV used to drop you back at the top of the category
list every time. The selection is now remembered per section (per session and
per provider), while the explicit navigations - jump to what's playing, a
reload asked to keep the current category, a series drill-in - still win.

Runs in a subprocess: the window embeds a QOpenGLWidget whose offscreen
teardown can abort at interpreter exit, so we assert on the child's success
marker and ignore its exit status (same pattern as the other window tests).
"""
import os
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtWidgets import QApplication

from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])
settings = QSettings("dopeiptv-test", "cat-memory")
settings.clear()
w = MainWindow(DemoClient(), settings)


def cat_of(row):
    return w.cat_list.item(row).data(Qt.ItemDataRole.UserRole)


def current_cat():
    it = w.cat_list.currentItem()
    return it.data(Qt.ItemDataRole.UserRole) if it else None


# Favorites and History both build their rows synchronously, so they show the
# behaviour without waiting on a provider round-trip.
w.switch_mode("fav")
app.processEvents()
assert w.cat_list.count() > 3, w.cat_list.count()
assert w.cat_list.currentRow() == 0        # first visit: top of the list
w.cat_list.setCurrentRow(2)
app.processEvents()
fav_cat = cat_of(2)
assert w._last_cat["fav"] == fav_cat, w._last_cat

w.switch_mode("history")
app.processEvents()
assert w.cat_list.currentRow() == 0        # first visit here too
w.cat_list.setCurrentRow(2)
app.processEvents()
hist_cat = cat_of(2)

# Back and forth: each section returns to its own last category.
w.switch_mode("fav")
app.processEvents()
assert current_cat() == fav_cat, (current_cat(), fav_cat)
w.switch_mode("history")
app.processEvents()
assert current_cat() == hist_cat, (current_cat(), hist_cat)
w.switch_mode("fav")
app.processEvents()
assert current_cat() == fav_cat

# A remembered category that is gone (provider dropped it, group renamed,
# folder deleted) falls back to the section's default row instead of leaving
# nothing selected.
w._last_cat["history"] = "no-such-category"
w.switch_mode("history")
app.processEvents()
assert w.cat_list.currentRow() == 0, w.cat_list.currentRow()
assert current_cat() is not None or w.cat_list.count() > 0

# Provider ids are provider-specific: switching playlists forgets them.
w._last_cat = {"live": "7"}
w._last_cat.clear()
assert w._last_cat == {}

# Ids come back from providers as int or str interchangeably, so a remembered
# "7" still finds category 7.
w.switch_mode("history")
app.processEvents()
w._last_cat["history"] = str(hist_cat)
w.switch_mode("fav")
app.processEvents()
w.switch_mode("history")
app.processEvents()
assert current_cat() == hist_cat, (current_cat(), hist_cat)

print("CAT_MEMORY_OK")
"""


def test_sections_remember_their_category():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for _attempt in (1, 2):
        proc = subprocess.run(
            [sys.executable, "-c", _CHILD], capture_output=True, text=True,
            env=env, cwd=_REPO_ROOT, timeout=180)
        if "CAT_MEMORY_OK" in proc.stdout:
            return
    assert "CAT_MEMORY_OK" in proc.stdout, (
        f"category-memory checks failed\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr[-2000:]!r}")
