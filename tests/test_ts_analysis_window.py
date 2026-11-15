"""Stream analysis in the window (ui/ts_analysis_dialog.py, ui/mw_ts_analysis.py):
the window fed what the analysis found (summary, indicators, PIDs, tiles,
painted offscreen), each status said plainly, Copy scrubbed, Reset counters;
what plays read from the rewind buffer's store (and nothing without it),
the reader started and stopped with the window and the channel; the
entries only while Nerd is on - in the player's options menu (the real
window, in a child process) and the menu of the channel that plays."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtGui import QGuiApplication  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ts_gen import Gen  # noqa: E402

from dopeiptv.core import nerd  # noqa: E402
from dopeiptv.core import ts_analysis as ta  # noqa: E402
from dopeiptv.i18n import tr  # noqa: E402

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _S(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's name
        self[key] = value


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _nerd_off():
    yield
    nerd.install(None)


def faulty_snapshot(name: str = "Test One") -> ta.Snapshot:
    pkts = Gen(name=name).take(12000)
    video = [k for k, p in enumerate(pkts) if ((p[1] & 0x1F) << 8 | p[2]) == 0x100]
    del pkts[video[2000]]
    a = ta.Analyzer()
    data = b"".join(pkts)
    for i in range(0, len(data), 65536):
        a.feed(data[i:i + 65536])
    return a.snapshot()


def dialog(app, poll, **kw):
    from dopeiptv.ui.ts_analysis_dialog import TsAnalysisDialog
    d = TsAnalysisDialog(None, poll, autostart=False, **kw)
    d.refresh()
    return d


# -- the window ------------------------------------------------------------------------


def test_the_window_shows_what_was_found(app):
    snap = faulty_snapshot()
    d = dialog(app, lambda: ("running", snap, 0.1))
    try:
        assert not d.body.isHidden()
        assert "Continuity_count_error" in d.summary.text()
        assert d.rows["cc"].count.text() == "1"
        assert d.rows["sync_loss"].count.text() == "0"
        assert d.rows["cc"].last.text().startswith(tr("tsa_last", when="")[:2])
        assert len(d.rows) == len(ta.INDICATORS)
        assert {p.pid for p in d.pids.rows()} >= {0x0, 0x100, 0x101, 0x1000, ta.NULL_PID}
        assert d.t_rate.value.text().endswith("Mbit/s")
        assert "%" in d.t_null.value.text()
        assert d.sub.text() == "Test One · dope"
        d.resize(1000, 740)
        d.show()
        app.processEvents()
        assert not d.grab().isNull()            # every part painted offscreen
        text = d.text()
        assert "1.4 Continuity_count_error: 1" in text
        assert "0x0100 (256)" in text and "H.264" in text
    finally:
        d.close()


def test_each_status_said_plainly(app):
    from dopeiptv.ui.ts_analysis_dialog import status_text
    for status in ("idle", "not_live", "hls", "not_buffered", "not_ts"):
        d = dialog(app, lambda status=status: (status, None, 0.0))
        try:
            assert d.body.isHidden()
            assert d.message.text() == tr(f"tsa_state_{status}")
            assert not d.reset_btn.isEnabled()
        finally:
            d.close()
    d = dialog(app, lambda: ("need_buffer", None, 0.0))
    assert tr("setting_dvr_kept") in d.message.text()
    assert tr("set_card_rewind") in d.message.text()
    d.close()
    # A stall keeps what was found on screen and says so.
    snap = faulty_snapshot()
    d = dialog(app, lambda: ("stalled", snap, 7.0))
    assert not d.body.isHidden() and d.message.text() == status_text("stalled", 7.0)
    assert "7" in d.message.text()
    d.close()
    d = dialog(app, lambda: ("waiting", None, 0.0))
    assert d.body.isHidden() and d.summary.text() == tr("tsa_waiting")
    d.close()


def test_copy_is_scrubbed_and_reset_asks_the_reader(app):
    snap = faulty_snapshot(name="http://user:secret@203.0.113.9/live")
    resets = []
    d = dialog(app, lambda: ("running", snap, 0.1), reset=lambda: resets.append(1))
    try:
        d.copy()
        got = QGuiApplication.clipboard().text()
        assert "secret" not in got and "203.0.113.9" not in got
        assert "<address>" in got and "Continuity_count_error" in got
        d.reset_btn.click()
        assert resets == [1]
    finally:
        d.close()
    d = dialog(app, lambda: ("running", snap, 0.1), scrub=lambda text: "scrubbed")
    d.copy()
    assert QGuiApplication.clipboard().text() == "scrubbed"
    d.close()


def test_the_sparkline_keeps_its_history_bounded(app):
    from dopeiptv.ui.ts_analysis_dialog import Sparkline
    s = Sparkline()
    s.set_values([None, 1.0, 2.0] * 100)
    assert len(s.values()) == ta.HISTORY
    s.resize(220, 40)
    assert not s.grab().isNull()


# -- what plays, and the reader ------------------------------------------------------------


class Host:
    """The window's side, without the window: what the mixin reads."""

    def __init__(self, url="", kind="live", session=None, kept="false"):
        from dopeiptv.ui.mw_ts_analysis import _TsAnalysisMixin
        self.__class__ = type("H", (Host, _TsAnalysisMixin), {})
        self.player = SimpleNamespace(current_url=url, _dvr=session)
        self._last_playback = {"kind": kind}
        self._playing_catchup = False
        self.settings = _S(live_rewind_kept=kept)
        self._playing_group = "live"
        self._playing_key = 7

    @staticmethod
    def _item_key(it):
        return it.get("stream_id")

    def _personal_values(self):
        return ["Secret Channel"]


def test_without_the_buffer_the_window_says_why():
    assert Host()._tsa_poll()[0] == "idle"
    assert Host("http://x/live/1.ts", kind="movie")._tsa_poll()[0] == "not_live"
    assert Host("http://x/live/a/b/1.m3u8")._tsa_poll()[0] == "hls"
    assert Host("http://x/live/a/b/1.ts")._tsa_poll()[0] == "need_buffer"
    assert Host("udp://@239.1.1.1:1234", kept="true")._tsa_poll()[0] == "not_buffered"
    h = Host("http://x/live/a/b/1.ts")
    h._playing_catchup = True
    assert h._tsa_poll()[0] == "not_live"
    assert Host()._tsa_scrub("on Secret Channel") == "on <private>"


def test_the_reader_follows_the_channel_and_stops_with_the_window(tmp_path):
    from dopeiptv.core.dvr_store import SegmentStore
    s1 = SegmentStore(tmp_path / "a", keep_seconds=60, max_bytes=1 << 30, min_free=0)
    s2 = SegmentStore(tmp_path / "b", keep_seconds=60, max_bytes=1 << 30, min_free=0)
    g = Gen(bitrate=4e6)
    s1.append(g.seconds(3.0))
    s2.append(Gen(bitrate=4e6).seconds(3.0))
    session = SimpleNamespace(closed=False, store=s1)
    h = Host("http://x/live/a/b/1.ts", session=session, kept="true")
    try:
        assert h._tsa_poll()[0] in ("waiting", "running")
        first = h._tsa_runner
        assert first is not None and first.alive
        deadline = time.monotonic() + 8
        status, snap, _idle = h._tsa_poll()
        while time.monotonic() < deadline and status != "running":
            s1.append(g.seconds(0.1))
            time.sleep(0.1)
            status, snap, _idle = h._tsa_poll()
        assert status == "running" and snap.service() == "Test One"
        # Another channel: another store, another reader.
        session.store = s2
        h._tsa_poll()
        assert h._tsa_runner is not first and h._tsa_store is s2
        first.join(3)
        assert not first.alive
        h._tsa_reset()
        # The channel left for an HLS one: nothing read, no reader.
        h.player._dvr = None
        h.player.current_url = "http://x/live/a/b/2.m3u8"
        second = h._tsa_runner
        assert h._tsa_poll()[0] == "hls" and h._tsa_runner is None
        second.join(3)
        assert not second.alive
        # The window closed: its reader with it.
        h.player._dvr = session
        h.player.current_url = "http://x/live/a/b/1.ts"
        h._tsa_poll()
        third = h._tsa_runner
        h._tsa_closed()
        third.join(3)
        assert h._tsa_runner is None and not third.alive
    finally:
        h._tsa_stop()
        s1.close()
        s2.close()


def test_the_entries_only_while_nerd_is_on(app):
    from PyQt6.QtWidgets import QMenu
    h = Host("http://x/live/a/b/1.ts")
    assert h._tsa_menu_text() == ""
    menu = QMenu()
    h._tsa_channel_menu(menu, {"stream_id": 7})
    assert menu.isEmpty()
    nerd.install(_S(nerd_enabled="true"))
    assert h._tsa_menu_text() == tr("tsa_menu")
    h._tsa_channel_menu(menu, {"stream_id": 8})          # not the one playing
    assert menu.isEmpty()
    h._tsa_channel_menu(menu, {"stream_id": 7})
    assert [a.text() for a in menu.actions()] == [tr("tsa_menu")]


_CHILD = r"""
import os, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QMenu

tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
from dopeiptv.core import nerd
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])
settings = QSettings(os.path.join(tmp, "settings.ini"), QSettings.Format.IniFormat)
w = MainWindow(DemoClient(), settings)
w.show()
app.processEvents()
if w.player is not None:
    label = tr("tsa_menu")

    def entries():
        m = QMenu()
        w.player.populate_options_menu(m)
        return [a.text() for a in m.actions()]
    assert label not in entries(), entries()
    settings.setValue("nerd_enabled", "true")
    nerd.install(settings)
    assert label in entries(), entries()
    w.open_ts_analysis()
    app.processEvents()
    win = w._tsa_window
    assert win is not None and win.isVisible()
    assert win.status == "idle", win.status
    assert win.message.text() == tr("tsa_state_idle")
    win.close()
    app.processEvents()
    assert w._tsa_window is None and w._tsa_runner is None
print("TSA_WINDOW_OK")
"""


def test_in_the_real_window():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=_REPO_ROOT)
    r = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                       capture_output=True, text=True, timeout=180)
    assert "TSA_WINDOW_OK" in r.stdout, r.stdout[-2000:] + r.stderr[-4000:]
