"""Channel numbers of the user's own (Nerd): kept per source, numbered in
list order, a number's channel found, the user's number winning over the
source's everywhere a list goes - the shared playlist included - and
nothing at all with Nerd off; then the real window, zapping by typed
digits to the right channel."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from dopeiptv.core import channel_numbers as cn
from dopeiptv.core import nerd, nerd_lists
from tests.test_nerd import S

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def nerd_on():
    def turn(**kw):
        s = S(nerd_enabled="true", **kw)
        nerd.install(s)
        return s
    yield turn
    nerd.install(None)


def _rows():
    return [{"stream_id": 401, "name": "TV4 HD", "num": 1},
            {"stream_id": 405, "name": "Kanal 5", "num": 2},
            {"stream_id": 407, "name": "Kanal 7", "num": "3"}]


# -- the store -------------------------------------------------------------------------

def test_a_number_is_kept_per_source_as_json():
    s = S()
    assert cn.set_number(s, "p1", 405, 12) is None
    assert json.loads(s["nerd_chno_p1"]) == {"405": 12}
    assert cn.get(s, "p1", 405) == 12 and cn.get(s, "p1", "405") == 12
    assert cn.get(s, "p2", 405) is None                      # another source has its own
    cn.set_number(s, "p2", 405, 3)
    assert cn.stored(s, "p1") == {"405": 12} and cn.stored(s, "p2") == {"405": 3}
    cn.clear(s, "p1", 405)
    assert cn.get(s, "p1", 405) is None and cn.stored(s, "p1") == {}


def test_a_number_belongs_to_one_channel():
    s = S()
    cn.set_number(s, "p1", 401, 12)
    assert cn.set_number(s, "p1", 405, 12) == "401"           # 401 gave it up
    assert cn.stored(s, "p1") == {"405": 12}
    assert cn.set_number(s, "p1", 405, 12) is None            # its own already


@pytest.mark.parametrize("bad", [0, -1, 100000, "x", "", None, True, 1.5])
def test_only_a_channel_number_is_taken(bad):
    s = S()
    cn.set_number(s, "p1", 405, 7)
    cn.set_number(s, "p1", 405, bad)                          # not a number: cleared
    assert cn.get(s, "p1", 405) is None
    assert cn.valid(bad) is None
    assert cn.valid("012") == 12 and cn.valid(99999) == 99999


def test_a_broken_setting_reads_as_none():
    assert cn.stored(S(nerd_chno_p1="{not json"), "p1") == {}
    assert cn.stored(S(nerd_chno_p1=json.dumps([1, 2])), "p1") == {}
    assert cn.stored(S(nerd_chno_p1=json.dumps({"1": "x", "2": 5})), "p1") == {"2": 5}


def test_clear_all_says_how_many():
    s = S()
    cn.set_number(s, "p1", 1, 5)
    cn.set_number(s, "p1", 2, 6)
    assert cn.clear_all(s, "p1") == 2 and cn.stored(s, "p1") == {}


# -- numbering all in list order ---------------------------------------------------------

def test_every_channel_numbered_in_list_order():
    s = S()
    rows = [{"_header": "Sweden"}, *_rows(), {"name": "no id"}]
    assert cn.number_all(s, "p1", rows, keep=False) == {"401": 1, "405": 2, "407": 3}


def test_numbers_set_by_hand_can_stay_and_the_rest_fill_in():
    s = S()
    cn.set_number(s, "p1", 407, 1)
    assert cn.number_all(s, "p1", _rows(), keep=True) == {"407": 1, "401": 2, "405": 3}
    cn.set_number(s, "p1", 405, 50)
    assert cn.number_all(s, "p1", _rows(), keep=False) == {"401": 1, "405": 2, "407": 3}


# -- a number's channel ---------------------------------------------------------------------

def test_a_number_finds_its_channel():
    rows = _rows()
    assert cn.find(rows, 2, {})["stream_id"] == 405           # the source's number
    assert cn.find(rows, "3", {})["stream_id"] == 407          # written as text by the source
    assert cn.find(rows, 9, {}) is None
    numbers = {"405": 12}
    assert cn.find(rows, 12, numbers)["stream_id"] == 405      # the user's own
    assert cn.find(rows, 2, numbers) is None                   # renumbered: not by its old one
    # A number given by hand is that channel's alone, even when the source
    # gives another channel the same one.
    numbers = {"405": 1}
    assert cn.find(rows, 1, numbers)["stream_id"] == 405
    assert cn.find([rows[0]], 1, numbers) is None              # not on the list: not the other
    assert cn.find(rows, "nope", numbers) is None


# -- in the lists -------------------------------------------------------------------------------

def test_the_users_number_wins_over_the_sources(nerd_on):
    s = nerd_on()
    cn.set_number(s, "p1", 405, 12)
    rows = nerd_lists.decorate(_rows(), None, "p1")
    assert [r["num"] for r in rows] == [1, 12, "3"]
    assert rows[1]["_num_own"] == 2 and "_num_own" not in rows[0]
    assert cn.shown(rows[1], cn.active("p1")) == 12
    # The number goes: the source's comes back, in the same rows (a list is
    # kept and decorated again on every fetch).
    cn.clear(s, "p1", 405)
    nerd_lists.decorate(rows, None, "p1")
    assert rows[1]["num"] == 2 and "_num_own" not in rows[1]
    # A favourite is a snapshot: what it shows is asked, not kept.
    cn.set_number(s, "p1", 405, 30)
    snap = {"stream_id": 405, "name": "Kanal 5", "num": 12, "_num_own": 2}
    assert cn.shown(snap, cn.active("p1")) == 30
    cn.clear(s, "p1", 405)
    assert cn.shown(snap, cn.active("p1")) == 2


def test_nerd_off_shows_everything_as_before():
    s = S(nerd_enabled="false")
    cn.set_number(s, "p1", 405, 12)
    nerd.install(s)
    try:
        fresh = _rows()
        assert nerd_lists.decorate(fresh, None, "p1") == _rows()
        assert cn.active("p1") == {}
        # Rows numbered while Nerd was on get the source's numbers back.
        numbered = cn.apply(_rows(), "p1", {"405": 12})
        assert numbered[1]["num"] == 12
        nerd_lists.decorate(numbered, None, "p1")
        assert numbered == _rows()
        assert cn.stored(s, "p1") == {"405": 12}             # kept for when Nerd is back
    finally:
        nerd.install(None)


def test_the_shared_playlist_carries_the_users_number():
    from dopeiptv.services.share import Share
    from tests.test_sources_side_by_side import Xtream
    xt = Xtream()
    s = S(nerd_enabled="true")
    cn.set_number(s, "chno", 405, 7)
    nerd.install(s)
    try:
        pls = [{"id": "chno", "name": "Main", "kind": "xtream", "server": xt.base,
                "username": "anna", "password": "pw-anna"}]
        sh = Share(lambda: pls, ttl=0)

        def line(text, name):
            return next(ln for ln in text.splitlines() if ln.endswith("," + name))
        text = sh.m3u("http://h", "tok")
        assert 'tvg-chno="7"' in line(text, "Kanal 5")
        assert 'tvg-chno="1"' in line(text, "TV4 HD")
        s["nerd_enabled"] = "false"
        nerd.install(s)
        text = sh.m3u("http://h", "tok")
        assert 'tvg-chno="2"' in line(text, "Kanal 5")
    finally:
        nerd.install(None)
        xt.close()


# -- Settings > Nerd > Channels and guide ------------------------------------------------------

def test_the_settings_card_says_how_many_and_numbers_all(tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QPushButton

    from dopeiptv.i18n import tr
    from dopeiptv.ui import nerd_settings
    from dopeiptv.ui.settings_ui import SettingsPage
    app = QApplication.instance() or QApplication([])
    assert app is not None
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    asked = []

    def numbers(action, keep, done):
        asked.append((action, keep))
        done({"count": 3, "all": 5}.get(action, 0))

    def built(source):
        page, parent = SettingsPage("nerd", "Nerd", ""), QDialog()
        nerd_settings.build(page, s, parent, source=source, numbers=numbers)
        button = next(b for b in page.findChildren(QPushButton) if b.text() == tr("nerd_chno_all_btn"))
        return page, parent, button

    try:
        page, _parent, button = built(lambda: {"id": "a", "name": "Main"})
        assert asked == [("count", False)] and button.isEnabled()
        state = next(lb for lb in page.findChildren(QLabel)
                     if lb.text() == tr("nerd_chno_count", source="Main", n=3))
        button.click()
        assert asked[-1] == ("all", True)
        assert state.text() == tr("nerd_chno_count", source="Main", n=5)
        # No source yet: nothing to number, and it says so.
        page, _parent, button = built(lambda: None)
        assert not button.isEnabled()
        assert any(lb.text() == tr("nerd_chno_no_source") for lb in page.findChildren(QLabel))
    finally:
        nerd.install(None)


# -- the real window -----------------------------------------------------------------------------

_CHILD = r"""
import os, sys, json, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
# Before anything makes a window: never the user's own settings or files.
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtTest import QTest
from dopeiptv.core import channel_numbers, nerd
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import make_client
from dopeiptv.ui.dialogs import ChannelNumberDialog
from dopeiptv.ui.main_window import MainWindow
from tests.test_sources_side_by_side import Xtream

xt = Xtream()
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
pls = [{"id": "a", "name": "Main", "kind": "xtream", "server": xt.base, "username": "anna", "password": "pw-anna"}]
s.setValue("playlists", json.dumps(pls))
s.setValue("active_playlist", "a")
s.setValue("nerd_enabled", "true")
channel_numbers.set_number(s, "a", 405, 12)          # Kanal 5 (the source's 2) is 12
nerd.install(s)
store = PlaylistStore(s)
w = MainWindow(make_client(pls[0]), s, store)
played, toasts = [], []
w._start_playback = lambda url, title, icon, key, kind, record=True, item=None, catchup=False: \
    played.append((url, title, kind))
w._show_toast = lambda text, ms=0: toasts.append(text)
TV4, K5 = xt.base + "/live/anna/pw-anna/401.ts", xt.base + "/live/anna/pw-anna/405.ts"

def wait(cond, n=600):
    for _ in range(n):
        app.processEvents(); w.pool.waitForDone(5)
        if cond():
            return True
        QTest.qWait(5)
    return False

def nums():
    rows = [w.list_model.item_at(i) for i in range(w.list_model.rowCount())]
    return {r["stream_id"]: r.get("num") for r in rows if r and r.get("stream_id") is not None}

def typed(widget, digits, n_before):
    QTest.keyClicks(widget, digits)
    assert w._chan_buffer == digits, w._chan_buffer
    return wait(lambda: len(played) > n_before and not w._chan_buffer)

w.switch_mode("live")
assert wait(lambda: len(nums()) == 2), nums()
assert nums() == {401: 1, 405: 12}, nums()                 # the user's number in the list
w.listw.setFocus()

# Typed in the channel list: 12 is Kanal 5, 1 is TV4 (the source's own).
assert typed(w.listw, "12", 0), played
assert played[-1][0] == K5 and played[-1][2] == "live", played
assert any("12" in t for t in toasts), toasts             # the digits on screen
assert typed(w.listw, "1", 1) and played[-1][0] == TV4, played
# Kanal 5's old number reaches nothing now: said, nothing started.
n = len(played)
QTest.keyClicks(w.listw, "2")
assert wait(lambda: tr("chan_not_found", num=2) in toasts), toasts
assert len(played) == n and not w._chan_buffer, played
# Enter takes the number at once.
QTest.keyClicks(w.listw, "12")
QTest.keyClick(w.listw, Qt.Key.Key_Return)
assert not w._chan_buffer and wait(lambda: len(played) > n) and played[-1][0] == K5

# Over the picture while a live channel plays, whatever the list shows: the
# whole source is looked in (and the key reaches the window from the video).
w.mode = "home"
w._live_playing = lambda: True
assert not w._typing()
video = w.player.video if w.player is not None else w     # no libmpv: the window itself
n = len(played)
assert typed(video, "12", n), played
assert played[-1][0] == K5, played
# Not while nothing live plays and the list is not the channel list.
w._live_playing = lambda: False
QTest.keyClicks(video, "1")
assert not w._chan_buffer
del w._live_playing

# The remote API's {"number": 12}.
n = len(played)
assert w._remote_do("play", {"number": 12}) == {"ok": True}
assert wait(lambda: len(played) > n) and played[-1][0] == K5, played
for bad in ("x", 0, 100000):
    try:
        w._remote_do("play", {"number": bad})
        raise AssertionError(bad)
    except ValueError:
        pass

# The dialog says when another channel has the number it is given.
d = ChannelNumberDialog(w, "TV4 HD", 1, lambda n: (n == 12, "Kanal 5" if n == 12 else ""))
assert d.value() == 1 and d.note.isHidden()
d.stepper.setValue(12)
assert not d.note.isHidden() and "Kanal 5" in d.note.text()
d.deleteLater()

# Settings > Nerd: number all in list order, keeping or not; clear all.
got = []
w.switch_mode("live")
assert wait(lambda: len(nums()) == 2)
w._chno_settings("all", True, got.append)
assert wait(lambda: got) and got[-1] == 2, got
assert channel_numbers.stored(s, "a") == {"405": 12, "401": 1}
w._chno_settings("all", False, got.append)
assert wait(lambda: len(got) == 2) and got[-1] == 2
assert channel_numbers.stored(s, "a") == {"401": 1, "405": 2}
assert nums() == {401: 1, 405: 2}, nums()                  # the list on screen follows
w._chno_settings("clear", False, got.append)
assert got[-1] == 0 and channel_numbers.stored(s, "a") == {}
w._chno_settings("count", False, got.append)
assert got[-1] == 0

# Nerd off: the source's numbers, as before (the user's are kept).
channel_numbers.set_number(s, "a", 405, 12)
s.setValue("nerd_enabled", "false"); nerd.install(s)
w.switch_mode("live")
assert wait(lambda: nums() == {401: 1, 405: 2}), nums()
w.listw.setFocus()
n = len(played)
assert typed(w.listw, "2", n) and played[-1][0] == K5, played
n = len(played)
QTest.keyClicks(w.listw, "12")
assert wait(lambda: tr("chan_not_found", num=12) in toasts), toasts
assert len(played) == n, played
assert channel_numbers.stored(s, "a") == {"405": 12}
xt.close()
print("CHANNEL_NUMBERS_OK")
"""


def test_the_window_zaps_to_a_typed_number():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert "CHANNEL_NUMBERS_OK" in out.stdout, out.stdout + out.stderr[-4000:]
