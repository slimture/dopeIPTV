"""Recording in TV mode: Options' Record records what plays through the
player's record button's own path (from its rewind buffer or stream, no
connection of its own) - also while TV mode's page is in front of the
picture, the stream going on behind it - and says Stop recording with its
minutes while that recording runs; pressed then, it asks before it stops
it. Not to be pressed while nothing live plays. The app's questions in it
are TV mode's own (TvAsk): large, answered with the arrows, OK and Back -
a pad's, LIRC's or the phone's actions too. A REC mark over the picture
while a recording runs, in the theme's colours. The phone's Record in TV
mode takes the same stream."""
from __future__ import annotations

import inspect
import os
import re
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QEvent, QSettings, Qt, QTimer  # noqa: E402
from PyQt6.QtGui import QColor, QKeyEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QWidget  # noqa: E402

from dopeiptv.i18n import tr  # noqa: E402

CH = {"stream_id": 7, "name": "SVT1", "stream_icon": ""}


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _NoDialog:
    """Nothing in TV mode may open the window's own small dialog."""

    def __init__(self, *a, **k):
        raise AssertionError("the window's own dialog opened in TV mode")

    @staticmethod
    def warning(*a, **k):
        raise AssertionError("the window's own warning box opened in TV mode")


class _NoBox:
    """ui/message_box.py in TV mode: any message or question of the
    window's own small kind fails."""

    def __getattr__(self, name):
        raise AssertionError(f"the window's own message box ({name}) opened in TV mode")


class _Player(QWidget):
    """The docked player: not shown while TV mode's page is in front of
    it, its stream going on (current_url)."""

    def __init__(self):
        super().__init__()
        self.current_url = "http://x/live/u/p/7.ts"
        self.records: list[str] = []
        self.can_record = True

    def start_stream_record(self, path):
        self.records.append(path)
        return self.can_record

    def take_buffer_recorder(self):
        return "buffer"


class _Rec:
    def __init__(self):
        self.jobs: list[dict] = []
        self.cancelled: list[str] = []
        self.own_connection: list[tuple] = []
        self.full = False

    def recorder(self):
        return ("ffmpeg", "/usr/bin/ffmpeg")

    def total_cap_exceeded(self):
        return self.full

    def folder_used_bytes(self):
        return 9 * 10**9

    def total_cap_bytes(self):
        return 8 * 10**9

    def directory(self):
        return "/rec"

    def build_path(self, title, folder="", channel=""):
        return f"/rec/{title}.ts"

    def add_inplayer_job(self, title, path, stop_ts, url="", icon=None, recorder=None, channel="",
                         extra=None):
        job = {"id": f"j{len(self.jobs)}", "title": title, "path": path, "stop": stop_ts,
               "url": url, "status": "recording", "inplayer": True, "start": time.time(),
               "recorder": recorder, "channel": channel}
        self.jobs.append(job)
        return job

    def add_job(self, *a, **k):
        self.own_connection.append(a)       # a connection of its own: never by itself

    def cancel(self, job_id):
        self.cancelled.append(job_id)
        for j in self.jobs:
            if j["id"] == job_id:
                j["status"] = "done"

    def active_count(self):
        return sum(1 for j in self.jobs if j["status"] == "recording")


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from dopeiptv.ui import mw_recording
    from dopeiptv.ui.mw_phone_rec import _PhoneRecMixin
    from dopeiptv.ui.mw_recording import _RecordingMixin as R
    from dopeiptv.ui.mw_tv import _TvMixin

    monkeypatch.setattr(mw_recording, "QDialog", _NoDialog)
    monkeypatch.setattr(mw_recording, "message_box", _NoBox())

    class W(_TvMixin, _PhoneRecMixin, QWidget):
        _choice_dialog = R._choice_dialog
        _rec_warn = R._rec_warn
        _recorder_ready = R._recorder_ready
        _within_storage_cap = R._within_storage_cap
        _fmt_gb = R.__dict__["_fmt_gb"]
        _record_now = R._record_now
        _rec_private_ok = R._rec_private_ok
        _record_in_background = R._record_in_background
        _with_conn_warning = R._with_conn_warning
        _one_more_over = R._one_more_over

        def __init__(self):
            QWidget.__init__(self)       # TV mode's question has the window over it
            self.settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
            self.player = _Player()
            self.rec = _Rec()
            self.addresses: list = []
            self.client = type("C", (), {"live_url": lambda _s, sid, fmt="ts": (
                self.addresses.append(sid), f"http://x/live/{sid}.ts")[1]})()
            self._playing_item = dict(CH)
            self._playing_group = "live"
            self._playing_key = "live:7"
            self._playing_catchup = False
            self._last_playback = {"kind": "live", "title": "SVT1", "item": dict(CH)}
            self._tv_on, self._tv_watching, self._tv_page = True, False, None

        def channel_display_name(self, it):
            return it.get("name") or ""

        def playback_mode(self):
            return "embedded"

        def _item_key(self, it):
            return f"live:{it.get('stream_id')}"

        def _rec_inplayer_url(self, it):
            return "http://x/live/7.ts"

        def _rec_player_source(self):
            return {"source": "s1"}

        def _rec_source(self):
            return {"source": "s1"}

        def _warn_pause_while_recording(self):
            pass

    w = W()
    yield w
    # Nothing left on screen: a window left active is no TV mode for the next test.
    for x in (w.player, w._tv_rec_mark, w):
        if x is not None:
            x.hide()
            x.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


def _asking(w, answer=None):
    """TV mode's question answered as *answer* (None: Back), and noted."""
    asked: list[tuple] = []

    def ask(title, message, options, dont_ask_setting=None):
        asked.append((title, message, list(options)))
        return answer(asked) if callable(answer) else answer
    w._tv_ask = ask
    return asked


# -- Options' Record ---------------------------------------------------------------------------

def test_record_is_not_pressed_while_nothing_live_plays(window):
    w = window
    asked = _asking(w)
    w._last_playback = {"kind": "movie", "title": "Sintel", "item": {}}
    assert w._tv_rec_tile() == (tr("rec_record"), tr("tv_rec_live_only"), False)
    w._tv_record()
    assert not w.rec.jobs and not asked and not w.player.records
    # An archive's stream is not what airs now; nor is a stream that stopped.
    w._last_playback = {"kind": "live", "title": "SVT1", "item": dict(CH)}
    w._playing_catchup = True
    assert w._tv_rec_tile()[2] is False
    w._playing_catchup = False
    w.player.current_url = None
    assert w._tv_rec_tile()[2] is False
    w._tv_record()
    assert not w.rec.jobs and not w.rec.own_connection
    # TV mode off: nothing (the player's own button is there).
    w.player.current_url = "http://x/live/u/p/7.ts"
    w._tv_on = False
    w._tv_record()
    assert not w.rec.jobs


def test_record_takes_the_players_own_path_with_the_page_in_front(window):
    w = window
    assert not w.player.isVisible()                 # behind TV mode's page, still playing
    assert w._tv_holds_picture() and w._tv_rec_item() == CH
    assert w._tv_rec_tile() == (tr("rec_record"), "SVT1", True)
    w._tv_record()
    [job] = w.rec.jobs
    # The player's record button's path: until stopped, from its rewind
    # buffer (or stream) - no address asked, no connection of its own.
    assert job["inplayer"] and job["recorder"] == "buffer" and job["stop"] is None
    assert w.player.records == ["/rec/SVT1.ts"]
    assert w.rec.own_connection == [] and w.addresses == []
    # While it runs: Stop recording with its minutes, and what it records.
    job["start"] = time.time() - 12 * 60 - 5
    assert w._tv_rec_tile() == (f"{tr('rec_stop_recording')} · {tr('opt_minutes', n=12)}", "SVT1",
                                True)


def test_stop_asks_first_and_only_a_yes_stops(window):
    w = window
    w._tv_record()
    jid = w.rec.jobs[0]["id"]
    asked = _asking(w)                             # Back: no answer
    w._tv_record()
    assert asked == [(tr("phone_rec_stop_ask", title="SVT1"), "",
                      [(tr("rec_stop_recording"), "danger"), (tr("common_cancel"), "normal")])]
    assert w.rec.cancelled == [] and w.rec.jobs[0]["status"] == "recording"
    _asking(w, 1)                                  # Cancel
    w._tv_record()
    assert w.rec.cancelled == []
    _asking(w, 0)                                  # Stop recording: as the record menu stops it
    w._tv_record()
    assert w.rec.cancelled == [jid] and w._tv_rec_job() is None
    assert w._tv_rec_tile() == (tr("rec_record"), "SVT1", True)


def test_the_record_paths_questions_are_tv_modes_own(window):
    w = window
    # The player cannot record its own stream: the window's question, in
    # TV mode's style - Back starts nothing, never a connection of its own.
    w.player.can_record = False
    asked = _asking(w)
    w._tv_record()
    assert [a[0] for a in asked] == [tr("rec_switch_title")]
    assert [o[0] for o in asked[0][2]] == [tr("rec_switch_and_record"), tr("rec_record_background"),
                                           tr("common_cancel")]
    assert not w.rec.jobs and not w.rec.own_connection
    # What stops a recording from starting is said there too, with OK.
    w.player.can_record = True
    w.rec.full = True
    asked = _asking(w)
    w._tv_record()
    assert asked == [(tr("rec_cap_title"), tr("rec_cap_reached", used="9.0 GB", cap="8.0 GB"),
                      [(tr("common_ok"), "primary")])]
    assert not w.rec.jobs


def test_the_phones_record_in_tv_mode_takes_the_same_stream(window):
    w = window
    assert w._phone_rec({}) == {"ok": True}
    assert w.rec.jobs[0]["inplayer"] and not w.rec.own_connection and w.addresses == []
    # Out of TV mode a player not shown is no stream to record from.
    w.rec.jobs.clear()
    w._tv_on = False
    assert w._phone_rec_item() is None and w._phone_rec({}) == {"ok": False}
    assert not w.rec.jobs and not w.rec.own_connection


def test_the_options_screen_offers_record_beside_sound_and_subtitles(window, app):
    from dopeiptv.ui.tv_mode import OptionsScreen

    w = window
    page = QWidget()
    page.window = w                                # as TvPage keeps it
    page.screens = {}
    scr = OptionsScreen(page)
    page.screens["settings"] = scr
    w._tv_page = page
    assert list(scr.tiles)[:4] == ["audio", "subtitles", "record", "sleep"]
    tile = scr.tiles["record"]
    w._last_playback = {"kind": "movie", "title": "Sintel", "item": {}}
    scr.refresh_rec()
    assert not tile.isEnabled() and tile.sub == tr("tv_rec_live_only")
    assert tile not in scr.focusables()
    w._last_playback = {"kind": "live", "title": "SVT1", "item": dict(CH)}
    scr.refresh_rec()
    assert tile.isEnabled() and tile.title == tr("rec_record") and tile.sub == "SVT1"
    assert tile in scr.focusables()
    # OK on it: the record path, once the press is done (a phone's press
    # gets its answer before any question).
    pressed: list[int] = []
    w._tv_record = lambda: pressed.append(1)
    tile.click()
    assert pressed == []
    app.processEvents()
    assert pressed == [1]
    del w._tv_record
    # A recording that starts or stops is said on the tile at once.
    w._tv_record()
    assert tile.title.startswith(tr("rec_stop_recording")) and tile.sub == "SVT1"
    w.rec.cancel(w.rec.jobs[0]["id"])
    w._tv_rec_changed()
    assert tile.title == tr("rec_record")
    scr.deleteLater()
    page.deleteLater()


# -- the REC mark over the picture -------------------------------------------------------------

def test_the_rec_mark_shows_over_the_picture_while_a_recording_runs(window, app):
    from dopeiptv.ui.theme import P
    from dopeiptv.ui.tv_widgets import TvRecMark, px

    w = window
    p = w.player
    p.resize(1280, 720)
    p.show()
    w._tv_watching = True                           # the picture, full screen
    w._tv_rec_mark_sync()
    assert w._tv_rec_mark is None                   # nothing records: nothing made
    w._tv_record()
    w._tv_rec_changed()
    mark = w._tv_rec_mark
    assert isinstance(mark, TvRecMark) and mark.parentWidget() is p and not mark.isHidden()
    assert (mark.x(), mark.y()) == (int(1280 * 0.05), int(720 * 0.05))
    assert mark.TEXT == "REC"
    # A red dot in the theme's recording colour.
    img = mark.grab().toImage()
    dot = img.pixelColor(px(22) + px(20) // 2, mark.height() // 2)
    want = QColor(P["rec"])
    assert max(abs(dot.red() - want.red()), abs(dot.green() - want.green()),
               abs(dot.blue() - want.blue())) <= 8, (dot.name(), want.name())
    # Placed again as the picture changes size.
    p.resize(1920, 1080)
    app.processEvents()
    assert (mark.x(), mark.y()) == (int(1920 * 0.05), int(1080 * 0.05))
    # Gone with the picture (TV mode's page in front), back with it.
    w._tv_watching = False
    w._tv_rec_mark_sync()
    assert mark.isHidden()
    w._tv_watching = True
    w._tv_rec_mark_sync()
    assert not mark.isHidden()
    # Gone when the recording ends, and when TV mode does.
    w.rec.cancel(w.rec.jobs[0]["id"])
    w._tv_rec_changed()
    assert mark.isHidden()
    w._tv_record()
    w._tv_rec_changed()
    assert not mark.isHidden()
    w._tv_on = False
    w._tv_rec_mark_sync()
    assert mark.isHidden()


def test_tv_modes_pieces_take_their_colours_from_the_theme():
    from dopeiptv.ui.tv_widgets import TvAsk, TvRecMark
    for cls in (TvRecMark, TvAsk):
        assert not re.search(r"#[0-9A-Fa-f]{3,8}\b", inspect.getsource(cls)), cls.__name__


# -- TV mode's question ------------------------------------------------------------------------

def test_tv_modes_question_is_answered_with_the_arrows_ok_and_back(app):
    from dopeiptv.ui.tv_widgets import TvAsk

    d = TvAsk(None, "Stop recording", "Stop recording SVT1?",
              [("Stop recording", "danger"), ("Cancel", "normal")], dont=True)
    d.show()
    app.processEvents()
    assert [t.title for t in d.choices] == ["Stop recording", "Cancel"]
    assert d.choices[0].ink == "rec" and d.choices[1].ink == ""
    assert d.focus_at() is d.choices[0]             # no primary: the first
    d.tv_action("down")
    assert d.focus_at() is d.choices[1]
    d.tv_action("right")
    assert d.focus_at() is d.dont
    d.tv_action("down")
    assert d.focus_at() is d.dont                   # the last stays the last
    d.tv_action("ok")                               # "Don't show this again" on - still asked
    assert d.dont_on and d.dont.sub == tr("option_on") and d.isVisible()
    d.tv_action("ok")
    assert not d.dont_on and d.dont.sub == tr("option_off")
    d.tv_action("up")
    d.tv_action("left")
    assert d.focus_at() is d.choices[0]
    # A keyboard's arrow on the tile itself moves the same way.
    QApplication.sendEvent(d.focus_at(), QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down,
                                                   Qt.KeyboardModifier.NoModifier))
    assert d.focus_at() is d.choices[1]
    d.tv_action("ok")
    assert d.chosen == 1 and d.result() == QDialog.DialogCode.Accepted
    d.deleteLater()

    # The app's primary choice has the focus first (one more connection:
    # Cancel), so OK alone never opens what the question is about.
    d = TvAsk(None, "Connections", "One more?", [("Anyway", "normal"), ("Cancel", "primary")])
    d.show()
    app.processEvents()
    assert d.focus_at() is d.choices[1]
    d.tv_action("ok")
    assert d.chosen == 1
    d.deleteLater()

    for back in ("action", Qt.Key.Key_Escape, Qt.Key.Key_Back):
        d = TvAsk(None, "T", "M", [("A", "primary")])
        d.show()
        app.processEvents()
        if back == "action":
            d.tv_action("back")
        else:
            QApplication.sendEvent(d.focus_at(), QKeyEvent(QEvent.Type.KeyPress, back,
                                                           Qt.KeyboardModifier.NoModifier))
        assert d.chosen is None and d.result() == QDialog.DialogCode.Rejected, back
        d.deleteLater()


def test_a_pads_or_the_phones_actions_answer_tv_modes_question(window, app):
    from dopeiptv.ui.tv_widgets import TvAsk

    w = window

    def press():
        for action in ("down", "down", "ok", "up", "ok"):
            w._tv_input(action)

    QTimer.singleShot(0, press)
    # Never a test left waiting: any question still up is closed.
    QTimer.singleShot(4000, lambda: [x.reject() for x in QApplication.topLevelWidgets()
                                     if isinstance(x, TvAsk)])
    idx = w._tv_ask("Recording", "Record SVT1?", [("Record", "primary"), ("Cancel", "normal")],
                    "some_dont_ask_key")
    assert idx == 1
    assert w.settings.value("some_dont_ask_key") == "true"
    # Back: no answer, nothing stored.
    QTimer.singleShot(0, lambda: w._tv_input("back"))
    QTimer.singleShot(4000, lambda: [x.reject() for x in QApplication.topLevelWidgets()
                                     if isinstance(x, TvAsk)])
    assert w._tv_ask("T", "M", [("A", "primary")], "other_key") is None
    assert w.settings.value("other_key") is None
