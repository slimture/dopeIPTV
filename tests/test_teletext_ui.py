"""Teletext on screen (offscreen): the viewer's keys - three digits, P+/P-,
subpages, colour links, index, hold, reveal, its keys kept from the
window's single-key shortcuts - and its word when the rewind buffer is
off; the player's subtitle menu naming teletext and DVB tracks by language
and page, picking a page, keeping it for the channel, the preferred
language choosing one, and a libmpv without libzvbi said so, never an
error."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QEvent, QSettings, Qt, pyqtSignal  # noqa: E402
from PyQt6.QtGui import QKeyEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMenu, QWidget  # noqa: E402

from dopeiptv.core import teletext as ttx  # noqa: E402
from dopeiptv.core import teletext_subs as ts  # noqa: E402
from dopeiptv.i18n import tr  # noqa: E402
from dopeiptv.media import teletext_subs as mts  # noqa: E402
from dopeiptv.ui.teletext_view import TeletextDialog  # noqa: E402
from tests import ttx_stream as g  # noqa: E402

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings():
    tmp = tempfile.mkdtemp()
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
    return QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)


def _decoder() -> ttx.Decoder:
    dec = ttx.Decoder()
    packets = g.demo_page() + [g.filler(1)]
    for page, text in ((0x101, "News"), (0x400, "Weather"), (0x150, "Other")):
        packets += g.page_packets(page, {2: text}) + [g.filler(page >> 8)]
    for sub in (1, 2, 3):
        packets += g.page_packets(0x200, {2: f"Sport {sub}"}, sub=sub) + [g.filler(2)]
    for p in packets:
        dec.feed_packet(p)
    return dec


class Source:
    def __init__(self, state="ok", decoder=None):
        self.state, self.decoder = state, decoder
        self.closed = self.settings = 0

    def teletext_state(self):
        return self.state, self.decoder, "SVT1"

    def teletext_closed(self):
        self.closed += 1

    def teletext_open_settings(self):
        self.settings += 1


def _key(w, key, text=""):
    ev = QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, text)
    QApplication.sendEvent(w, ev)


def _row(d: TeletextDialog, y: int) -> str:
    return ttx.text_of(d.view.cells(), y)


def test_the_viewers_keys(app):
    src = Source(decoder=_decoder())
    d = TeletextDialog(None, src)
    d.show()
    app.processEvents()
    assert d.page == 0x100 and "dopeIPTV Text" in _row(d, 1)
    # Three digits go to a page; one or two only show in the header.
    _key(d, Qt.Key.Key_2, "2")
    assert _row(d, 0).startswith("P2--") and d.page == 0x100
    _key(d, Qt.Key.Key_0, "0")
    _key(d, Qt.Key.Key_0, "0")
    assert d.page == 0x200 and _row(d, 0).startswith("P200")
    assert _row(d, 2).strip() == "Sport 3" and d.sub_lbl.text() == "3/3"
    # Subpages: left and right.
    _key(d, Qt.Key.Key_Left)
    assert _row(d, 2).strip() == "Sport 2" and d.sub_lbl.text() == "2/3"
    _key(d, Qt.Key.Key_Right)
    _key(d, Qt.Key.Key_Right)
    assert _row(d, 2).strip() == "Sport 1"
    # P+ / P-: the next page there is.
    _key(d, Qt.Key.Key_Down)
    assert d.page == 0x150
    _key(d, Qt.Key.Key_PageUp)
    assert d.page == 0x200
    _key(d, Qt.Key.Key_Up)
    assert d.page == 0x400
    # A digit that cannot start a page, and Backspace.
    _key(d, Qt.Key.Key_9, "9")
    _key(d, Qt.Key.Key_1, "1")
    _key(d, Qt.Key.Key_Backspace)
    assert d.typed == "" and d.page == 0x400
    # The index, then its colour links: red 101, green 200, yellow 400.
    _key(d, Qt.Key.Key_I, "i")
    assert d.page == 0x100
    assert [b.text() for b in d.colour_btns] == ["101", "200", "400", "100"]
    _key(d, Qt.Key.Key_R, "r")
    assert d.page == 0x101 and _row(d, 2).strip() == "News"
    _key(d, Qt.Key.Key_Home)
    _key(d, Qt.Key.Key_Y, "y")
    assert d.page == 0x400
    # Without links: red and green step, blue is the index.
    _key(d, Qt.Key.Key_B, "b")
    assert d.page == 0x100
    # Reveal and hold.
    assert not any(c.conceal is False and c.ch == "S" and c.fg == 2 for c in d.view.cells()[17])
    _key(d, Qt.Key.Key_Question, "?")
    assert d.view.reveal and d.reveal_btn.isChecked()
    _key(d, Qt.Key.Key_H, "h")
    assert d.hold and d.status.text() == tr("ttx_held")
    _key(d, Qt.Key.Key_H, "h")
    assert not d.hold
    # Its keys are its own: the window's R (record), I (stats) wait.
    for key, text in ((Qt.Key.Key_R, "r"), (Qt.Key.Key_I, "i"), (Qt.Key.Key_5, "5"),
                      (Qt.Key.Key_T, "t")):
        ev = QKeyEvent(QEvent.Type.ShortcutOverride, key, Qt.KeyboardModifier.NoModifier, text)
        QApplication.sendEvent(d, ev)
        assert ev.isAccepted(), text
    ctrl = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_K,
                     Qt.KeyboardModifier.ControlModifier, "k")
    QApplication.sendEvent(d, ctrl)
    assert not ctrl.isAccepted()                    # Ctrl+K: the window's
    # T closes it, and the window hears of it.
    _key(d, Qt.Key.Key_T, "t")
    app.processEvents()
    assert not d.isVisible() and src.closed == 1


def test_a_page_not_received_yet_and_the_buffer_off(app):
    src = Source(decoder=_decoder())
    d = TeletextDialog(None, src, start=0x777)
    d.show()
    app.processEvents()
    assert d.status.text() == tr("ttx_waiting", page="777")
    src.state, src.decoder = "no_buffer", None
    d.refresh()
    assert d.stack.currentIndex() == 1 and d.note.text() == tr("ttx_needs_buffer")
    assert not d.settings_btn.isHidden()
    d.settings_btn.click()
    assert src.settings == 1
    src.state = "not_live"
    d.refresh()
    assert d.note.text() == tr("ttx_not_live") and d.settings_btn.isHidden()
    src.state, src.decoder = "no_teletext", ttx.Decoder()
    d.refresh()
    assert d.stack.currentIndex() == 0 and d.status.text() == tr("ttx_none")
    d.close()


# -- the player's side, on a stand-in for EmbeddedPlayer ---------------------------------

class FakeMpv(dict):
    def __init__(self, tracks, version="mpv 0.37.0"):
        super().__init__({"teletext-page": 100, "sub-visibility": True})
        self.tracks = tracks
        self.sid = next((t["id"] for t in tracks if t.get("type") == "sub"
                         and t.get("selected")), False)
        self.mpv_version = version
        self.sets: list[tuple] = []

    @property
    def track_list(self):
        """As mpv has it: the subtitle track selected is the one sid names."""
        return [dict(t, selected=t.get("id") == self.sid) if t.get("type") == "sub" else t
                for t in self.tracks]

    def __setitem__(self, key, value):
        self.sets.append((key, value))
        if key == "sid":
            self.sid = value
        super().__setitem__(key, value)


class Player(mts.TeletextSubsMixin, QWidget):
    track_selected = pyqtSignal(str, object)
    _ttx_streams_ready = pyqtSignal(int, object)

    def __init__(self, mpv, settings, live=True):
        super().__init__()
        self.video = SimpleNamespace(mpv=mpv)
        self._settings = settings
        self._chan_key = "src:7"
        self._chan_own = {}
        self.current_url = "http://tv.invalid/7.ts"
        self._dvr = None
        self.overlay: list[str] = []
        self._ttx_streams_ready.connect(self._ttx_streams_got)
        self._ttx_reset(live)

    def _set_mpv(self, prop, value):
        self.video.mpv[prop] = value

    def set_overlay_info(self, text):
        self.overlay.append(text)


TRACKS = [{"id": 1, "type": "video", "selected": True},
          {"id": 1, "type": "sub", "codec": "dvb_teletext", "lang": "swe,swe,fin",
           "src-id": 0x102},
          {"id": 2, "type": "sub", "codec": "dvb_subtitle", "lang": "fin", "src-id": 0x103},
          {"id": 3, "type": "sub", "codec": "subrip", "lang": "eng"}]
PMT = {0x102: ttx.Stream(0x102, 6, "teletext", teletext=[
    ttx.TeletextEntry("swe", ttx.TYPE_INITIAL, 0x100),
    ttx.TeletextEntry("swe", ttx.TYPE_SUBTITLE, 0x199),
    ttx.TeletextEntry("fin", ttx.TYPE_SUBTITLE, 0x333)])}


@pytest.fixture
def libzvbi(monkeypatch):
    """The decoder there (True), missing (False) or not known (None)."""
    state = {"value": True}
    from dopeiptv.media import players
    monkeypatch.setattr(players, "teletext_decoder", lambda: state["value"])
    monkeypatch.setattr(mts, "_broken", [False])
    return state


def _entries(p: Player) -> list[tuple[str, bool, bool]]:
    menu = QMenu()
    for t in p.video.mpv.track_list:
        if t.get("type") == "sub" and p._ttx_is_track(t):
            p._ttx_add_entries(menu, t, shown=True)
    p._ttx_menu_tail(menu)
    return [(a.text(), a.isEnabled(), a.isChecked()) for a in menu.actions()]


def test_menu_labels_by_language_and_page(app, settings, libzvbi):
    m = FakeMpv(TRACKS)
    p = Player(m, settings)
    p._ttx_poll(m)
    names = [e[0] for e in _entries(p)]
    # The PMT not known yet: each language, no page; DVB as itself.
    assert names[:3] == [tr("sub_ttx", lang=tr("lang_swe")), tr("sub_ttx", lang=tr("lang_fin")),
                         tr("sub_dvb", lang=tr("lang_fin"))]
    assert names[-1] == tr("sub_ttx_page_pick")
    p._ttx_streams_got(p._ttx_token, PMT)
    names = [e[0] for e in _entries(p)]
    assert "Swedish - teletext 199" in [mts.choice_label(c) for c in ts.choices(TRACKS[1], PMT)]
    assert names[:3] == [tr("sub_ttx_page", lang=tr("lang_swe"), page="199"),
                         tr("sub_ttx_page", lang=tr("lang_fin"), page="333"),
                         tr("sub_dvb", lang=tr("lang_fin"))]
    hoh = ts.Choice(1, ts.TELETEXT, "ger", 0x150, True)
    assert mts.choice_label(hoh) == f"{tr('sub_ttx_page', lang=tr('lang_ger'), page='150')} ({tr('sub_hoh')})"
    assert mts.language_label("") == tr("sub_lang_unknown")
    assert mts.language_label("sv") == tr("lang_swe")


def test_a_page_picked_plays_and_is_kept_for_the_channel(app, settings, libzvbi):
    m = FakeMpv(TRACKS)
    p = Player(m, settings)
    p._ttx_poll(m)
    p._ttx_streams_got(p._ttx_token, PMT)
    fin = ts.choices(TRACKS[1], PMT)[1]
    picked = []
    p.track_selected.connect(lambda prop, tid: picked.append((prop, tid)))
    p._ttx_pick(fin)
    assert m["teletext-page"] == 333 and m.sid == 1 and m["sub-visibility"] is True
    # The page is set before the track: it changes while the track plays.
    keys = [k for k, _v in m.sets]
    assert keys.index("teletext-page") < keys.index("sid")
    assert picked == [("sid", 1)]
    assert ts.PageStore(settings).get("src:7") == (0x333, "", 0x102)
    for text, _enabled, checked in _entries(p):
        assert checked == (text == tr("sub_ttx_page", lang=tr("lang_fin"), page="333"))
    # The channel again: its page, before the PMT is read.
    m2 = FakeMpv(TRACKS)
    p2 = Player(m2, settings)
    p2._ttx_poll(m2)
    assert m2["teletext-page"] == 333 and m2.sid == 1
    # Subtitles off by hand: the page goes.
    p2._ttx_forget()
    assert ts.PageStore(settings).get("src:7") is None


def test_the_preferred_language_picks_its_teletext_page(app, settings, libzvbi):
    settings.setValue("sub_mode", "lang")
    settings.setValue("sub_lang", "fin")
    m = FakeMpv(TRACKS)
    p = Player(m, settings)
    p._ttx_poll(m)
    assert m.sid == 1 and m["teletext-page"] == 333       # by custom, mpv 0.37
    m["teletext-page"] = 0
    p._ttx_streams_got(p._ttx_token, PMT)
    assert m["teletext-page"] == 333                       # the PMT's page
    assert ts.PageStore(settings).get("src:7") is None     # chosen, not kept
    # Not on a stream that is no live channel, and not over a DVB track in it.
    m2 = FakeMpv(TRACKS)
    Player(m2, settings, live=False)._ttx_poll(m2)
    assert m2.sid is False
    tracks = [dict(t) for t in TRACKS]
    tracks[2]["selected"] = True
    m3 = FakeMpv(tracks)
    Player(m3, settings)._ttx_poll(m3)
    assert m3.sid == 2 and "teletext-page" not in dict(m3.sets)
    # mpv 0.38 and later: any subtitle page until the PMT says which.
    m4 = FakeMpv(TRACKS, version="mpv 0.39.0")
    Player(m4, settings)._ttx_poll(m4)
    assert m4["teletext-page"] == 0 and m4.sid == 1


def test_before_a_stream_opens_the_page_is_never_the_index(app, settings, libzvbi):
    settings.setValue("sub_mode", "lang")
    settings.setValue("sub_lang", "swe")
    m = FakeMpv([])
    p = Player(m, settings)
    sets = {}
    p._ttx_before_load(sets.__setitem__)
    assert sets == {"teletext-page": 199}
    p._ttx_version = (0, 38)
    p._ttx_before_load(sets.__setitem__)
    assert sets == {"teletext-page": 0}


def test_without_libzvbi_teletext_is_unavailable_never_an_error(app, settings, libzvbi):
    libzvbi["value"] = False
    settings.setValue("sub_mode", "lang")
    settings.setValue("sub_lang", "swe")
    m = FakeMpv(TRACKS)
    p = Player(m, settings)
    p._ttx_poll(m)
    assert m.sid is False                                  # nothing chosen
    p._ttx_streams_got(p._ttx_token, PMT)
    entries = _entries(p)
    ttx_rows = [e for e in entries if tr("sub_ttx_unavailable") in e[0]]
    assert len(ttx_rows) == 2 and not any(e[1] for e in ttx_rows)
    assert entries[-1] == (tr("sub_ttx_no_libzvbi_short"), False, False)
    dvb = [e for e in entries if e[0] == tr("sub_dvb", lang=tr("lang_fin"))]
    assert dvb and dvb[0][1]                                # DVB still offered
    assert mts.unavailable_reason() == tr("sub_ttx_no_libzvbi")


def test_a_track_mpv_lets_go_of_says_libzvbi_is_missing_once(app, settings, libzvbi):
    libzvbi["value"] = None                                 # cannot tell beforehand
    m = FakeMpv(TRACKS)
    p = Player(m, settings)
    p._ttx_poll(m)
    p._ttx_pick(ts.choices(TRACKS[1])[0])
    assert m.sid == 1
    m.sid = False                                           # mpv dropped it
    p._ttx_check()
    assert p.overlay == [tr("sub_ttx_no_libzvbi_short")]
    assert mts.unavailable_reason() and p._ttx_current is None
    assert all(not e[1] for e in _entries(p) if tr("sub_ttx_unavailable") in e[0])


# -- the real player's menu ----------------------------------------------------------------

_CHILD = r"""
import os, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QMenu
app = QApplication.instance() or QApplication([])
tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
s = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
from dopeiptv.i18n import tr
from dopeiptv.media import players, teletext_subs
players.teletext_decoder = lambda: True
from dopeiptv.media.embedded import EmbeddedPlayer
p = EmbeddedPlayer(settings=s)


class FakeMpv(dict):
    pause = False
    sid = False
    mpv_version = "mpv 0.37.0"
    track_list = [{"type": "video", "id": 1, "selected": True},
                  {"type": "sub", "id": 1, "codec": "dvb_teletext", "lang": "swe,fin",
                   "src-id": 258},
                  {"type": "sub", "id": 2, "codec": "dvb_subtitle", "lang": "dan",
                   "src-id": 259},
                  {"type": "sub", "id": 3, "lang": "eng", "title": "SDH"}]

    def command(self, *args):
        pass

    def play(self, url):
        pass

    def __getattr__(self, n):
        return None


m = p.video.mpv = FakeMpv()
opened = []
p.open_teletext = lambda: opened.append(1)
p.teletext_key = lambda: "T"
assert p.play("http://x/live/1.ts", "SVT1", local_buffer=True)
assert m["teletext-page"] != 100
p._poll_position()
mm = QMenu()
p.populate_options_menu(mm)
subs = [a for a in mm.actions() if a.text() == tr("opt_subtitles")][0].menu()
names = [a.text() for a in subs.actions() if a.text()]
assert tr("sub_ttx", lang=tr("lang_swe")) in names, names
assert tr("sub_ttx", lang=tr("lang_fin")) in names, names
assert tr("sub_dvb", lang=tr("lang_dan")) in names, names
assert "eng SDH" in names and tr("sub_ttx_page_pick") in names, names
ttx = [a for a in mm.actions() if a.text().startswith(tr("ttx_menu"))]
assert ttx and ttx[0].text() == tr("ttx_menu") + "\tT"
ttx[0].trigger()
assert opened == [1]
# Picked: its page set and the track chosen, kept for no channel (none armed).
[a for a in subs.actions() if a.text() == tr("sub_ttx", lang=tr("lang_fin"))][0].trigger()
assert m["teletext-page"] == 333 and m["sid"] == 1
print("OK")
"""


def test_the_real_players_subtitle_menu_names_teletext_and_dvb_tracks():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0 and "libmpv" in (r.stderr + r.stdout).lower() \
            and "Traceback" not in r.stderr:
        pytest.skip("no libmpv")
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr[-3000:] + r.stdout[-2000:]
