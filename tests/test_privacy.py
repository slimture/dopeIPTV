"""Private viewing and Clear recent history: what is kept while private
viewing is on (nothing), and what each time range clears - for every
source, Recently viewed and where playback stopped."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta

import pytest

pytest.importorskip("PyQt6.QtCore")

from PyQt6.QtCore import QSettings  # noqa: E402

from dopeiptv.core import privacy  # noqa: E402
from dopeiptv.core.stores import HistoryStore  # noqa: E402
from dopeiptv.services.resume import ResumeStore  # noqa: E402

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def settings(tmp_path):
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    yield s
    privacy.set_private(False)


def _ago(**kw) -> str:
    return (datetime.now() - timedelta(**kw)).isoformat()


def _history(settings, key="history_a"):
    rows = [
        {"name": "SVT1 HD", "_key": 1, "_kind": "live", "_url": "u1", "_watched_at": _ago(minutes=30)},
        {"name": "Film", "_key": 2, "_kind": "movie", "_url": "u2", "_watched_at": _ago(hours=2)},
        {"name": "TV4", "_key": 3, "_kind": "live", "_url": "u3", "_watched_at": _ago(hours=20)},
        {"name": "Avsnitt", "_key": 4, "_kind": "episode", "_url": "u4", "_watched_at": _ago(days=4)},
        {"name": "Gammal", "_key": 5, "_kind": "live", "_url": "u5", "_watched_at": _ago(days=9)},
        {"name": "Utan tid", "_key": 6, "_kind": "movie", "_url": "u6"},
    ]
    settings.setValue(key, json.dumps(rows))
    return HistoryStore(settings, key)


@pytest.mark.parametrize("rid, left", [
    ("1h", ["Film", "TV4", "Avsnitt", "Gammal", "Utan tid"]),
    ("3h", ["TV4", "Avsnitt", "Gammal", "Utan tid"]),
    ("24h", ["Avsnitt", "Gammal", "Utan tid"]),
    ("5d", ["Gammal", "Utan tid"]),
    ("all", []),
])
def test_each_time_range_clears_what_was_watched_in_it(settings, rid, left):
    h = _history(settings)
    seconds = dict(privacy.RANGES)[rid]
    privacy.clear_recent(settings, settings, h, seconds)
    assert [e["name"] for e in h.items()] == left
    # Stored as well, not only in memory.
    assert [e["name"] for e in HistoryStore(settings, "history_a").items()] == left


def test_channels_or_titles_alone(settings):
    h = _history(settings)
    counts = privacy.clear_recent(settings, settings, h, 0, channels=True, titles=False,
                                  positions=False)
    assert counts["channels"] == 3
    assert [e["_kind"] for e in h.items()] == ["movie", "episode", "movie"]
    h = _history(settings)
    privacy.clear_recent(settings, settings, h, 24 * 3600, channels=False, titles=True,
                         positions=False)
    assert [e["name"] for e in h.items()] == ["SVT1 HD", "TV4", "Avsnitt", "Gammal", "Utan tid"]


def test_every_source_is_cleared(settings):
    """Each source keeps its own Recently viewed; clearing reaches all of
    them, the one on screen through its own store."""
    active = _history(settings, "history_a")
    _history(settings, "history_b")
    _history(settings, "history")
    privacy.clear_recent(settings, settings, active, 3 * 3600)
    for key in ("history_a", "history_b", "history"):
        assert [e["name"] for e in HistoryStore(settings, key).items()][0] == "TV4", key
    assert active.items()[0]["name"] == "TV4"


def test_where_playback_stopped_is_cleared_by_time(settings):
    now = int(time.time())
    for pid in ("a", "b"):
        settings.setValue(f"resume_positions_{pid}", json.dumps({
            "vod:1": {"pos": 600, "dur": 5400, "ts": now - 600},
            "vod:2": {"pos": 900, "dur": 5400, "ts": now - 3 * 86400},
        }))
    gone = privacy.clear_resume(settings, privacy.cutoff(3600))
    assert gone == 2
    for pid in ("a", "b"):
        left = json.loads(settings.value(f"resume_positions_{pid}"))
        assert list(left) == ["vod:2"]
    assert privacy.clear_resume(settings, None) == 2
    assert json.loads(settings.value("resume_positions_a")) == {}


def test_private_viewing_keeps_nothing(settings):
    h = HistoryStore(settings, "history_a")
    r = ResumeStore(settings, "a")
    r.record("vod", 7, 600, 5400, item={"name": "Före"})
    privacy.set_private(True)
    h.add("http://h/1.ts", "SVT1 HD", None, 1, "live")
    r.record("vod", 8, 1200, 5400, item={"name": "Hemlig"})
    r.record("vod", 7, 5300, 5400)          # finished in private: not even dropped
    assert h.items() == []
    assert set(json.loads(settings.value("resume_positions_a"))) == {"vod:7"}
    privacy.set_private(False)
    h.add("http://h/1.ts", "SVT1 HD", None, 1, "live")
    r.record("vod", 8, 1200, 5400, item={"name": "Synlig"})
    assert [e["name"] for e in h.items()] == ["SVT1 HD"]
    assert set(json.loads(settings.value("resume_positions_a"))) == {"vod:7", "vod:8"}


def test_the_clear_window(settings):
    from PyQt6.QtWidgets import QApplication

    from dopeiptv.ui.dialogs import ClearHistoryDialog
    app = QApplication.instance() or QApplication([])   # kept: a dropped one is destroyed
    d = ClearHistoryDialog(None, "channels")
    assert d.channels.isChecked() and not d.titles.isChecked() and not d.positions.isChecked()
    assert [d.range.itemData(i) for i in range(d.range.count())] == [s for _r, s in privacy.RANGES]
    d.channels.setChecked(False)
    assert not d.go.isEnabled()                      # nothing to clear: no Clear
    d.titles.setChecked(True)
    d.range.setCurrentIndex(2)
    assert d.choice() == {"seconds": 24 * 3600, "channels": False, "titles": True,
                          "positions": False}
    assert app is not None


def test_what_is_played_privately_never_reaches_the_log(monkeypatch):
    """The log is kept on disk and goes with a report: while private
    viewing is on, a line about playback says <private>, not the title."""
    import logging

    from dopeiptv.core.log import log
    from dopeiptv.media import players
    lines: list[str] = []

    class Catch(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())
    catch = Catch(logging.INFO)
    level = log.level
    log.addHandler(catch)
    log.setLevel(logging.INFO)
    monkeypatch.setattr(players, "find_player_executable", lambda _p: "/usr/bin/mpv")
    monkeypatch.setattr(players.subprocess, "Popen", lambda *a, **k: None)
    try:
        privacy.set_private(True)
        players.launch_player("mpv", "http://h/live/1.ts", "A Secret Film")
        assert privacy.for_log("A Secret Film") == "<private>"
        privacy.set_private(False)
        players.launch_player("mpv", "http://h/live/2.ts", "An Open Film")
        assert privacy.for_log("An Open Film") == "An Open Film"
    finally:
        privacy.set_private(False)
        log.removeHandler(catch)
        log.setLevel(level)
    said = "\n".join(lines)
    assert "Secret" not in said and "launching EXTERNAL mpv for <private>" in said, said
    assert "An Open Film" in said


_CHILD = r"""
import os, sys, json, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from datetime import datetime
from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QApplication
from dopeiptv.core import privacy
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui import dialogs

app = QApplication.instance() or QApplication([])
# The window keeps positions in a settings file of its own (user scope):
# here a folder of the test's, never the real one of whoever runs this.
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
w = MainWindow(DemoClient(), s, PlaylistStore(s))
assert w._resume_settings.fileName().startswith(tempfile.gettempdir()), w._resume_settings.fileName()
w.show()
toasts = []
w._show_toast = lambda text, ms=0: toasts.append(text)
assert w._private_pill.isHidden() and not w._private_action.isChecked()
assert w._private_action.shortcut() == QKeySequence("Ctrl+Shift+N")
assert w._clear_action.shortcut() == QKeySequence("Ctrl+Shift+Delete")
# On from the menu: the marker shows, history keeps nothing.
from dopeiptv.i18n import tr
w.resize(1400, 900)
w._set_sidebar_collapsed(False)
for _ in range(10):
    app.processEvents()
logo = w._sidebar_logo
before = (logo.geometry(), w.nav_btns["guide"].geometry(), w._donate_btn.geometry())
w._private_action.trigger()
for _ in range(5):
    app.processEvents()
assert privacy.private() and not w._private_pill.isHidden()
# It lies over the logo's lower edge, centred, in no layout: nothing moves.
pill = w._private_pill
assert (logo.geometry(), w.nav_btns["guide"].geometry(), w._donate_btn.geometry()) == before
assert w._logo_row.indexOf(pill) == -1 and pill.parentWidget() is logo.parentWidget()
assert abs(pill.geometry().center().x() - logo.geometry().center().x()) <= 1, (pill.geometry(), logo.geometry())
mark_bottom = logo.y() + (logo.height() + logo.LOGO_H) // 2
assert abs(pill.geometry().center().y() - mark_bottom) <= 1, (pill.geometry(), mark_bottom)
assert pill.geometry().right() < w._donate_btn.geometry().left() or w._donate_btn.isHidden() \
    or pill.geometry().bottom() < w._donate_btn.geometry().top() or pill.geometry().top() > w._donate_btn.geometry().bottom()
assert 0 <= pill.x() and pill.geometry().right() < logo.parentWidget().width()
# On the icon rail too: inside the rail, still centred on the logo, on its
# top edge (the heart is under it there).
w._set_sidebar_collapsed(True)
for _ in range(10):
    app.processEvents()
assert abs(pill.geometry().center().x() - logo.geometry().center().x()) <= 1
assert abs(pill.geometry().center().y() - (logo.y() + (logo.height() - logo.LOGO_H) // 2)) <= 1
assert pill.geometry().right() < logo.parentWidget().width() + 2, (pill.geometry(), logo.parentWidget().width())
w._set_sidebar_collapsed(False)
for _ in range(10):
    app.processEvents()
assert abs(pill.geometry().center().x() - logo.geometry().center().x()) <= 1
w.history.add("http://h/1.ts", "SVT1 HD", None, 1, "live")
assert w.history.items() == []
# Off from the marker.
w._private_pill.click()
assert not privacy.private() and w._private_pill.isHidden() and not w._private_action.isChecked()
# (Other notes may come and go meanwhile - an update check, a guide.)
assert tr("private_on_toast") in toasts and tr("private_off_toast") in toasts, toasts
# Clear recent: what the window chose goes, and it says how much.
w.history.add("http://h/1.ts", "SVT1 HD", None, 1, "live")
w.history.add("http://h/2.mp4", "Film", None, 2, "movie")
dialogs.ClearHistoryDialog.exec = lambda self: 1
dialogs.ClearHistoryDialog.choice = lambda self: {"seconds": 3600, "channels": True,
                                                   "titles": False, "positions": True}
counts = w.open_clear_history()
assert counts["channels"] == 1, counts
assert [e["name"] for e in w.history.items()] == ["Film"]
assert tr("clear_done", n=1) in toasts, toasts
print("PRIVACY_OK")
"""


def test_the_window_turns_private_viewing_on_and_off_and_clears():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=120)
    assert "PRIVACY_OK" in out.stdout, out.stdout + out.stderr[-3000:]
