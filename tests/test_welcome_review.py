"""The first-run guide, the demo and "no source yet" as a review found them,
each finding held here so it cannot come back:

- a name made from an address never carries the login written into it, a
  playlist file is named by its file (core/names.source_name, the guide,
  the quick form, a list of sources imported);
- what a found server or a preset brought goes with its kind and with an
  address typed by hand; no login of an older release's keys put in the
  form, none left in it once a source is kept;
- a mistyped sync code keeps the keys in its field; a media server's form
  has one primary button, says what is missing, Plex's Connect opens its
  sign-in; at the smallest window an error and Connect are brought into
  sight; "Use Quick Connect" whole in Swedish, German and Arabic;
- the links are buttons, the greeting goes round once - not at all with
  less motion - and names each language once; the demo is offered on the
  choice of what to watch too;
- a kids profile is offered no Nerd and the guide's guard holds Connect,
  the Bring step and Nerd;
- the reviewed words: one term, the singular, each language's list
  separator, every one of them translated;
- the demo's films on a loop: their guide's blocks the films' own lengths,
  only those channels looped (the player's side: tests/test_play_ops.py);
- the demo's playbacks named nowhere in the log, and nothing of it left
  once it ends.

And in the whole window on real profile settings (core/profiles.py), each
a run of its own:

- "Who's watching?" first: the guide waits hidden behind the picker - no
  key, no Esc of it - and comes back once a profile is picked, also when
  it was up before the picker, and after a sync that brought profiles but
  no source;
- a kids profile: the strip's Add your own source, Connect, the Bring step
  ask a grown-up's PIN, Nerd is not offered;
- TV mode, the command palette, Search everywhere are not opened over the
  guide (nor TV mode asked from the phone or Home Assistant);
- a source added in Settings while there is none of one's own (exploring,
  the demo) is used at once;
- the demo: one's own History and resume points stay (and what one's own
  files add meanwhile), the demo's are kept nowhere; its films on a loop
  are looped, others not; Leave the demo and Just my files and radio leave
  it for no source; Esc keeps the demo and its strip's words; the readers
  kept per source never take the demo for a stored source; where the app
  opens and the TV place's density are not written by it; the strip's ✕ and
  its narrow form.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("PyQt6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QSettings, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QPushButton, QWidget  # noqa: E402

from dopeiptv import i18n  # noqa: E402
from dopeiptv.core.names import source_name  # noqa: E402
from dopeiptv.i18n import tr  # noqa: E402
from dopeiptv.ui.welcome import (  # noqa: E402
    BOX, IPTV, MEDIA, PAGE_BRING, PAGE_CHOOSE, PAGE_FORM, PAGE_WELCOME, PAGE_WORK,
    WelcomeOverlay, _Link,
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOCALES = os.path.join(_REPO_ROOT, "dopeiptv", "locale")


@pytest.fixture(scope="module")
def app():
    i18n.set_language("en")
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _english():
    yield
    i18n.set_language("en")


def _overlay(settings=None, **kw):
    from PyQt6.QtWidgets import QMainWindow
    win = QMainWindow()
    win.setCentralWidget(QWidget())
    s = settings
    if s is None:
        s = QSettings("dopeIPTV-test", "welcome-review")
        s.clear()
    got: dict = {"connect": [], "files": 0, "demo": 0, "explore": 0}
    ov = WelcomeOverlay(
        win, settings=s, on_connect=lambda *a: got["connect"].append(a),
        on_explore=lambda: got.__setitem__("explore", got["explore"] + 1),
        on_language_change=lambda c: i18n.set_language(c),
        on_demo=lambda: got.__setitem__("demo", got["demo"] + 1),
        on_files=lambda: got.__setitem__("files", got["files"] + 1), **kw)
    got["_win"] = win
    return ov, got


def _form(ov, family, kind=None):
    ov._stack.setCurrentIndex(PAGE_CHOOSE)
    ov._cards[family].click()
    if kind:
        ov._set_kind(kind)
    assert ov._stack.currentIndex() == PAGE_FORM


def _up(app, ov, got, size=(1100, 760)):
    got["_win"].resize(*size)
    got["_win"].show()
    ov.cover()
    for _ in range(4):
        app.processEvents()


# -- a name from an address ---------------------------------------------------------------

LOGINS = ("bob", "hunter2secret")


@pytest.mark.parametrize("address, name", [
    ("http://bob:hunter2secret@127.0.0.1:35693/list.m3u", "127.0.0.1:35693"),
    ("https://bob:hunter2secret@line.example.com/get.php?username=bob&password=hunter2secret",
     "line.example.com"),
    ("bob:hunter2secret@tv.example.org:8080", "tv.example.org:8080"),
    ("http://bob@[2001:db8::5]:8000/x", "[2001:db8::5]:8000"),
    ("http://line.example.com/get.php?username=bob&password=hunter2secret", "line.example.com"),
    ("/home/bob/Lists/my tv.m3u8", "my tv"),
    ("C:\\Lists\\my tv.m3u8", "my tv"),
    ("\\\\nas\\share\\Family.m3u", "Family"),
    ("file:///home/me/tv%20x.m3u", "tv x"),
    ("", ""),
    ("http://", ""),
])
def test_a_name_from_an_address_never_carries_its_login(address, name):
    got = source_name(address)
    assert got == name
    assert not any(word in got for word in LOGINS)


def test_every_way_a_source_is_named_by_itself_leaves_its_login_out(app, tmp_path):
    from dopeiptv.core.stores import PlaylistStore
    from dopeiptv.ui.dialogs import PlaylistDialog
    link = "http://bob:hunter2secret@127.0.0.1:8080/get.php?username=bob&password=hunter2secret"
    # The quick form, nothing typed for the name.
    dlg = PlaylistDialog(None, prefill={"kind": "m3u", "server": link})
    assert dlg.values()["name"] == "127.0.0.1:8080"
    dlg.deleteLater()
    # A list of sources imported (PlaylistStore.import_data).
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    store = PlaylistStore(s)
    store.import_data([{"kind": "m3u", "server": link}])
    assert [p["name"] for p in store.playlists()] == ["127.0.0.1:8080"]
    # The guide's connect step says it by its host.
    ov, got = _overlay()
    _form(ov, IPTV, "m3u")
    ov._server.setText(link)
    ov._c_connect.click()
    assert ov._stack.currentIndex() == PAGE_WORK
    title = ov._k_title.text()
    assert "127.0.0.1:8080" in title and not any(w in title for w in LOGINS), title
    # A file picked from this computer: its own name.
    ov._k_back.click()
    from PyQt6.QtWidgets import QFileDialog
    real = QFileDialog.getOpenFileName
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(tmp_path / "Family TV.m3u"), ""))
    try:
        ov._pick_file()
    finally:
        QFileDialog.getOpenFileName = real
    ov._c_connect.click()
    assert ov._k_title.text() == tr("onb_connecting", name="Family TV")


# -- the form ----------------------------------------------------------------------------

def test_what_a_found_server_brought_goes_with_its_kind_and_a_typed_address(app):
    from PyQt6.QtTest import QTest
    ov, got = _overlay()
    _up(app, ov, got)
    _form(ov, BOX, "satip")
    found = {"kind": "satip", "server": "http://192.168.1.9:8875", "name": "Octopus",
             "description": "http://192.168.1.9:8875/desc.xml"}
    ov._box_found(found)
    assert ov._extra and ov._extra_kind == "satip"
    ov._c_connect.click()
    assert got["connect"][-1][-1] == {"options": {"description": found["description"]}}
    ov._k_back.click()
    # Another kind picked: what came with the SAT>IP server goes.
    ov._set_kind("enigma2")
    assert ov._extra == {} and ov._extra_kind == ""
    ov._set_kind("satip")
    ov._c_connect.click()
    assert got["connect"][-1][-1] == {}, got["connect"][-1]
    ov._k_back.click()
    # Found again, then the address typed by hand: it goes too.
    ov._box_found(found)
    assert ov._extra
    ov._server.setFocus()
    QTest.keyClicks(ov._server, "1")
    assert ov._extra == {} and ov._extra_kind == ""
    got["_win"].close()


def test_no_login_of_an_older_release_is_put_in_the_form(app):
    s = QSettings("dopeIPTV-test", "welcome-review-legacy")
    s.clear()
    for key, value in (("server", "http://old.example.com:8080"), ("username", "anna"),
                       ("password", "s3cret"), ("playlist_name", "Old one")):
        s.setValue(key, value)
    ov, _got = _overlay(s)
    for field in (ov._server, ov._user, ov._pw, ov._name):
        assert field.text() == ""
    s.clear()


def test_no_login_stays_in_the_guide_once_a_source_is_kept(app):
    ov, got = _overlay()
    _form(ov, IPTV, "xtream")
    ov._server.setText("http://line.example.com:8080")
    ov._user.setText("anna")
    ov._pw.setText("s3cret pw")
    ov._name.setText("Mine")
    ov._c_connect.click()
    assert got["connect"] and ov._last_try
    ov.connect_done(12, 7, 3)
    ov._k_go.click()                                  # Start watching
    assert not ov.isVisible()
    for field in (ov._server, ov._user, ov._pw, ov._mac, ov._name):
        assert field.text() == "", field
    assert ov._last_try == () and ov._extra == {}
    # Nor kept for its kind, to come back when the kind is picked again.
    ov.reset(PAGE_CHOOSE)
    _form(ov, IPTV, "m3u")
    ov._set_kind("xtream")
    assert ov._user.text() == "" and ov._pw.text() == "" and ov._server.text() == ""


def test_a_mistyped_sync_code_keeps_the_keys_in_its_field(app):
    ov, got = _overlay()
    _up(app, ov, got)
    asked: list = []
    ov.sync_asked.connect(asked.append)
    ov._w_bring.clicked.emit()
    assert ov._stack.currentIndex() == PAGE_BRING
    ov._b_code.setText("abcd-efgh")
    ov._b_go.click()
    assert asked == ["abcd-efgh"] and not ov._b_code.isEnabled()
    ov.sync_failed(tr("sync_code_bad"))
    app.processEvents()
    assert QApplication.focusWidget() is ov._b_code
    assert ov._b_code.selectedText() == "abcd-efgh"
    # Enter now asks again - it never lands on Back and leaves the page.
    from PyQt6.QtTest import QTest
    QTest.keyClick(ov._b_code, Qt.Key.Key_Return)
    assert ov._stack.currentIndex() == PAGE_BRING and asked == ["abcd-efgh"] * 2
    got["_win"].close()


def _primaries(ov, page):
    return [b for b in ov._stack.widget(page).findChildren(QPushButton)
            if b.objectName() == "Primary" and b.isVisibleTo(ov._stack.widget(page))]


def test_a_media_servers_form_has_one_primary_and_says_what_is_missing(app):
    ov, got = _overlay()
    _up(app, ov, got)
    for kind in ("jellyfin", "emby", "plex"):
        _form(ov, MEDIA, kind)
        assert _primaries(ov, PAGE_FORM) == [ov._c_connect], kind
    _form(ov, MEDIA, "jellyfin")
    ov._c_connect.click()
    assert ov._c_err.isVisible() and ov._c_err.text() == tr("onb_fill_media")
    assert got["connect"] == []
    # Plex: Connect is its sign-in in the browser.
    _form(ov, MEDIA, "plex")
    started: list = []
    ov._signin.start_plex = lambda: started.append(1)
    ov._c_connect.click()
    assert started == [1] and not ov._c_err.isVisible()
    got["_win"].close()


@pytest.mark.parametrize("code", ["sv", "de", "ar"])
def test_use_quick_connect_is_whole_in_long_languages(app, code):
    i18n.set_language(code)
    ov, got = _overlay()
    ov._retranslate()
    _up(app, ov, got)
    _form(ov, MEDIA, "jellyfin")
    for _ in range(4):
        app.processEvents()
    qc = ov._signin.qc_btn
    assert qc.isVisible() and qc.width() + 1 >= qc.sizeHint().width(), (qc.text(), qc.width())
    got["_win"].close()


def test_at_the_smallest_window_an_error_and_connect_are_in_sight(app):
    ov, got = _overlay()
    _up(app, ov, got, (700, 420))
    for kind in ("xtream", "enigma2"):
        _form(ov, IPTV if kind == "xtream" else BOX, kind)
        ov._scroll.verticalScrollBar().setValue(0)
        ov._c_connect.click()
        for _ in range(6):
            app.processEvents()
        vp = ov._scroll.viewport()
        assert ov._scroll.verticalScrollBar().maximum() > 0, kind     # it scrolls here
        for w in (ov._c_err, ov._c_connect):
            top = w.mapTo(vp, QPoint(0, 0)).y()
            assert top >= 0 and top + w.height() <= vp.height(), (kind, w.objectName(), top)
    got["_win"].close()


# -- the first page ---------------------------------------------------------------------

def test_the_links_are_buttons_and_the_greeting_goes_round_once(app, monkeypatch):
    ov, got = _overlay()
    _up(app, ov, got)
    for link in (ov._w_demo, ov._w_bring, ov._ch_demo):
        assert isinstance(link, _Link) and isinstance(link, QPushButton)
        assert link.focusPolicy() == Qt.FocusPolicy.StrongFocus and link.accessibleName()
    # "&" in a translation is no shortcut's mark.
    ov._w_demo.set_plain("Films & more")
    assert ov._w_demo.plain() == "Films & more" and ov._w_demo.text() == "Films && more"
    greetings = ov._GREETINGS
    assert len(set(greetings)) == len(greetings)
    from dopeiptv.ui import notice_widgets
    monkeypatch.setattr(notice_widgets, "reduced_motion", lambda: False)
    ov.reset()
    assert ov._flash.isActive() and ov._hero.text() == greetings[0]
    for _ in range(len(greetings)):
        ov._next_greeting()
    assert not ov._flash.isActive() and ov._hero.text() == greetings[0]
    monkeypatch.setattr(notice_widgets, "reduced_motion", lambda: True)
    ov.reset()
    assert not ov._flash.isActive() and ov._hero.text() == greetings[0]
    got["_win"].close()


def test_the_demo_is_offered_on_the_choice_too(app):
    ov, got = _overlay()
    _up(app, ov, got)
    ov._w_next.click()
    page = ov._stack.widget(PAGE_CHOOSE)
    assert ov._ch_demo.isVisibleTo(page) and ov._ch_demo.plain() == tr("onb_demo_link")
    ov._ch_demo.click()
    assert got["demo"] == 1 and not ov.isVisible()
    got["_win"].close()


def test_a_kids_profile_is_offered_no_nerd_and_the_guard_holds(app):
    s = QSettings("dopeIPTV-test", "welcome-review-kids")
    s.clear()
    allow = {"on": False}
    asked: list = []
    ov, got = _overlay(s, guard=lambda: asked.append(1) or allow["on"], kids=lambda: True)
    _up(app, ov, got)
    assert not ov._w_nerd.isVisibleTo(ov._stack.widget(PAGE_WELCOME))
    ov._toggle_nerd()
    assert s.value("nerd_enabled", "false") == "false" and asked == []   # not even asked
    _form(ov, IPTV, "m3u")
    ov._server.setText("http://127.0.0.1:9/list.m3u")
    ov._c_connect.click()
    assert got["connect"] == [] and ov._stack.currentIndex() == PAGE_FORM and asked == [1]
    synced: list = []
    restores: list = []
    ov.sync_asked.connect(synced.append)
    ov.restore_asked.connect(lambda: restores.append(1))
    ov._open_bring()
    ov._b_code.setText("abcd")
    ov._ask_sync()
    ov._ask_restore()
    assert synced == [] and restores == [] and not ov._b_busy
    # The grown-up's PIN given: on it goes.
    allow["on"] = True
    ov._ask_sync()
    assert synced == ["abcd"]
    got["_win"].close()
    s.clear()


def test_the_strip_folds_its_buttons_under_its_words_on_a_narrow_pane(app):
    from PyQt6.QtWidgets import QBoxLayout

    from dopeiptv.ui.mw_onboarding import _SourceStrip
    strip = _SourceStrip()
    strip.say(True)
    strip.resize(400, 120)
    strip.show()
    for _ in range(3):
        app.processEvents()
    assert strip._box.direction() == QBoxLayout.Direction.TopToBottom
    lines = strip.text.heightForWidth(strip.text.width()) / strip.text.fontMetrics().lineSpacing()
    assert lines <= 3.2, lines                       # five lines it took beside its buttons
    assert strip.leave.isVisibleTo(strip) and strip.away.accessibleName() == tr("common_close")
    strip.resize(900, 60)
    app.processEvents()
    assert strip._box.direction() == QBoxLayout.Direction.LeftToRight
    strip.say(False)
    assert not strip.leave.isVisibleTo(strip) and strip.text.text() == tr("onb_strip_none")
    strip.deleteLater()


# -- words ---------------------------------------------------------------------------------

def _locales():
    out = {}
    for name in sorted(os.listdir(_LOCALES)):
        if name.endswith(".json"):
            with open(os.path.join(_LOCALES, name), encoding="utf-8") as f:
                out[name[:-5]] = json.load(f)
    return out


NEW_KEYS = ("onb_leave_demo", "onb_fill_media", "onb_list_sep", "count_channels_one",
            "count_movies_one", "count_series_one")
CHANGED = ("onb_head_xtream", "playlist_kind_m3u", "playlist_m3u_url", "demo_cat_films",
           "demo_cat_loop", "onb_strip_demo")


def test_the_reviewed_words():
    from dopeiptv.i18n import _STRINGS
    en = {k: v["en"] for k, v in _STRINGS.items()}
    # One term: a provider's source, never "No provider yet" beside it.
    assert "welcome_add_hint" not in en
    assert "user name" in en["onb_head_xtream"] and "provider" in en["onb_head_xtream"]
    for key in ("login_username", "onb_fill_all", "playlist_required_fields", "err_wrong_login",
                "err_box_login_needed", "onb_fill_media"):
        assert "username" not in en[key].lower(), key
    assert "file" in en["playlist_kind_m3u"] and "file" in en["playlist_m3u_url"]
    assert en["demo_cat_films"] == "Freely licensed films" and "Open movies" not in en.values()
    assert " - " not in en["onb_bring_private"]
    locales = _locales()
    assert len(locales) == 26
    for code, d in locales.items():
        assert "welcome_add_hint" not in d, code
        for key in NEW_KEYS + CHANGED:
            assert d.get(key), (code, key)
        assert " - " not in d["onb_bring_private"], code
        for key in ("count_channels_one", "count_movies_one", "count_series_one"):
            assert "{n}" in d[key], (code, key)
        # Translated, not English left in place (the separator and the
        # counts said as a label may be the same in a language).
        for key in CHANGED + ("onb_leave_demo", "onb_fill_media"):
            assert d[key] != en[key], (code, key)
    sv = locales["sv"]
    assert "drakungen" in sv["demo_plot_sintel"] and "draktungen" not in sv["demo_plot_sintel"]
    assert sv["onb_nerd_offer"].startswith("🤓 Nerd?") and "inställningar" in sv["onb_nerd_offer"]
    assert sv["onb_nerd_on_now"].startswith("🤓 Nerd")
    assert "Demokanaler" in sv["demo_title"] and "Demo-kanaler" not in sv["demo_title"]
    assert locales["ja"]["onb_list_sep"] == "、" and locales["ar"]["onb_list_sep"] == "، "


def test_one_of_a_kind_is_said_in_the_singular_with_the_languages_separator(app):
    ov, _got = _overlay()
    ov._trying = True
    ov.connect_done(1, 1, 1)
    assert ov._k_text.text() == tr("onb_done_counts", what="1 channel, 1 movie, 1 series")
    i18n.set_language("ja")
    ov._trying = True
    ov.connect_done(2, 1, 0)
    want = tr("onb_list_sep").join([tr("count_channels", n="2"), tr("count_movies_one", n="1")])
    assert ov._k_text.text() == tr("onb_done_counts", what=want) and "、" in want


# -- the demo --------------------------------------------------------------------------------

def test_the_demos_films_on_a_loop_are_blocks_of_their_own_length():
    import re
    from datetime import datetime

    from dopeiptv.providers import demo
    xml = demo.guide(now=1_700_000_000).decode()
    client = demo.DemoClient()
    for sid, _name, _l, _c, cat, key, what in demo.CHANNELS:
        assert client.loops(sid) == (cat == "loop"), sid
        blocks = re.findall(rf'<programme start="(\d{{14}}) \+0000" stop="(\d{{14}}) \+0000" '
                            rf'channel="demo\.{key}">', xml)
        assert blocks, key
        lengths = {int((datetime.strptime(b, "%Y%m%d%H%M%S") - datetime.strptime(a, "%Y%m%d%H%M%S"))
                       .total_seconds()) for a, b in blocks}
        assert lengths == ({demo.FILM_SECONDS[what]} if cat == "loop" else {3600}), (key, lengths)
    assert not client.loops("nonsense") and not client.loops(101)


def test_a_demo_playback_is_named_nowhere_in_the_log_and_nothing_of_it_stays():
    from dopeiptv.core import privacy
    try:
        privacy.keep_nothing(True, {"local", "radio"})
        privacy.playback_started("live", 3)
        assert privacy.for_log("Big Buck Bunny 24/7") == "<private>"
        assert privacy.url_for_log("https://example.com/bbb.mp4") == "<private>"
        # One's own file played meanwhile is one's own, as ever.
        privacy.playback_started("local", "/m/film.mkv")
        assert privacy.for_log("film.mkv") == "film.mkv"
        privacy.playback_started("live", 3)
        privacy.keep_nothing(False)
        assert privacy._passing_playback is None
        assert not privacy.playback_private("live", 3)
        assert privacy.for_log("Big Buck Bunny 24/7") == "Big Buck Bunny 24/7"
    finally:
        privacy.keep_nothing(False)
        privacy.playback_stopped()


# -- the whole window, on real profile settings ---------------------------------------------

_CHILD = r"""
import faulthandler, os, sys, tempfile, time
faulthandler.enable()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings, Qt
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox
app = QApplication.instance() or QApplication([])
boxes = []
for name in ("critical", "warning", "information", "question"):
    setattr(QMessageBox, name, staticmethod(lambda *a, _n=name, **k: boxes.append((_n, a[2:3]))
                                            or QMessageBox.StandardButton.Ok))
QMessageBox.exec = lambda self: boxes.append(("exec", self.text())) or 0

from dopeiptv import APP_NAME, i18n
from dopeiptv.core import privacy
from dopeiptv.core import profiles as _prof
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import OfflineClient, XtreamClient
from dopeiptv.providers.demo import DemoClient
from dopeiptv.ui import main_window as mw, profiles_ui, theme
from dopeiptv.ui.welcome import FILES, PAGE_BRING, PAGE_CHOOSE, PAGE_CONNECT, PAGE_FORM, PAGE_WELCOME
from tests.test_welcome_paths import Served, xtream_panel

SCENARIO = sys.argv[1]
i18n.set_language("en")
mw.launch_player = lambda *a, **k: None
path = os.path.join(tempfile.mkdtemp(), "s.ini")
s = _prof.ProfileSettings(path, QSettings.Format.IniFormat)
_prof.start(s, [], "Me")
s.setValue("check_updates", "false")
pins = {"given": None}
asked_pins = []
def _ask_pin(*a, **k):
    asked_pins.append(a[1:2])
    return pins["given"]
profiles_ui.ask_pin = _ask_pin
ps0 = _prof.ProfileStore(s)
me = ps0.profiles()[0]
if SCENARIO in ("picker_first", "picker_over", "sync_profiles"):
    ps0.add("Kid", kids=True)
    ps0.set_pin(me.id, "4321")
    ps0.set_ask_at_start(True)
elif SCENARIO == "kids":
    ps0.set_pin(me.id, "4321")
    kid = ps0.add("Kid", kids=True)
theme.apply_theme(s)
app.setStyleSheet(theme.build_style())
store = PlaylistStore(s)
w = mw.MainWindow(OfflineClient(), s, store)
played = []
if w.player is not None:
    def _play(url, title, *a, **k):
        played.append((url, w.player._loop_armed, k.get("local_buffer")))
        w.player._loop_armed = False          # play() takes it, whatever it opens
        return True
    w.player.play = _play
w.resize(1100, 760)
w.show()
w.activateWindow()
QTest.qWaitForWindowActive(w, 3000)
_t_up = time.monotonic()
while time.monotonic() - _t_up < 0.6:        # the window's start: its first place
    app.processEvents()
    time.sleep(0.005)


def settle(cond, secs=15.0, what=""):
    # Holds once it held over a turn of the event loop more: the window's
    # start (its first place, 100 ms in) moves the keys a moment later.
    end = time.monotonic() + secs
    while time.monotonic() < end:
        app.processEvents()
        w.pool.waitForDone(5)
        if cond():
            app.processEvents()
            if cond():
                return
        time.sleep(0.005)
    assert cond(), "timed out: " + what


def idle(secs):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def key(k, mod=Qt.KeyboardModifier.NoModifier, to=None):
    QTest.keyClick(to or QApplication.focusWidget() or w, k, mod)
    app.processEvents()


def written():
    s.sync()
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def guide_up():
    ov = w._welcome
    return ov is not None and ov.isVisible()


def in_guide():
    f = QApplication.focusWidget()
    return f is not None and (f is w._welcome or w._welcome.isAncestorOf(f))


if SCENARIO == "picker_first":
    # At start: "Who's watching?" (a PIN on the profile used last) - the
    # guide app.py asks for waits behind it.
    assert w._profile_picker is not None and w._profile_must_pick
    w.show_welcome()
    idle(0.3)
    assert not guide_up() and w._welcome_waits == PAGE_WELCOME
    for k in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_T):
        key(k)
        idle(0.1)
        assert not guide_up() and not w._tv_on
    assert w._welcome_waits == PAGE_WELCOME and w._profile_picker is not None
    pins["given"] = "4321"
    assert w._profile_pick(me.id)
    settle(lambda: guide_up(), what="the guide after the pick")
    settle(lambda: in_guide(), what="the keys in the guide")
    assert w._welcome._stack.currentIndex() == PAGE_WELCOME and w._welcome_waits is None

elif SCENARIO == "picker_over":
    w._profile_close_picker()
    w._profile_must_pick = False
    idle(0.2)
    w.show_welcome()
    settle(lambda: guide_up() and in_guide(), what="the guide")
    ov = w._welcome
    ov._w_next.click()
    idle(0.1)
    assert ov._stack.currentIndex() == PAGE_CHOOSE
    w._maybe_pick_profile_at_start()
    idle(0.2)
    assert w._profile_picker is not None and not guide_up() and w._welcome_waits is True
    key(Qt.Key.Key_Escape)
    idle(0.2)
    assert not guide_up() and w._welcome_waits is True            # Esc was not the guide's
    # Asked again meanwhile ("Add your own source"): still after the pick.
    w.show_welcome(PAGE_CHOOSE)
    assert not guide_up()
    pins["given"] = "4321"
    assert w._profile_pick(me.id)
    settle(lambda: guide_up() and in_guide(), what="the guide back")
    assert ov._stack.currentIndex() == PAGE_CHOOSE                 # as it was left

elif SCENARIO == "sync_profiles":
    w._profile_close_picker()
    w._profile_must_pick = False
    idle(0.2)
    w.show_welcome()
    settle(lambda: guide_up(), what="the guide")
    ov = w._welcome
    ov._open_bring()
    ov._set_busy(True)
    w._wsync_before = frozenset()
    # A sync that brought settings and profiles, no source.
    w._wizard_sync_landed()
    idle(0.2)
    assert w._profile_picker is not None and not guide_up()
    pins["given"] = "4321"
    assert w._profile_pick(me.id)
    settle(lambda: guide_up(), what="the guide back")
    assert ov._stack.currentIndex() == PAGE_CONNECT and ov._c_note.isVisible()

elif SCENARIO == "kids":
    w._profile_close_picker()
    w._profile_must_pick = False
    idle(0.2)
    assert w.switch_profile(kid.id) and w._profile_kids()
    w.show_welcome()
    settle(lambda: guide_up(), what="the guide")
    ov = w._welcome
    assert not ov._w_nerd.isVisibleTo(ov._stack.widget(PAGE_WELCOME))
    ov._toggle_nerd()
    assert s.value("nerd_enabled", "false") == "false" and asked_pins == []
    # Connect, with no grown-up's PIN given: nothing tried.
    srv = Served(xtream_panel())
    ov._w_next.click()
    ov._cards["iptv"].click()
    ov._server.setText(srv.base)
    ov._user.setText("anna")
    ov._pw.setText("s3cret pw")
    ov._c_connect.click()
    idle(0.3)
    assert asked_pins and w._wtry is None and ov._stack.currentIndex() == PAGE_FORM
    assert not srv.seen
    # The Bring step: nothing joined.
    n = len(asked_pins)
    ov._open_bring()
    ov._b_code.setText("abcd-efgh")
    ov._ask_sync()
    ov._ask_restore()
    assert len(asked_pins) == n + 2 and not ov._b_busy
    # The strip's Add your own source: the guide does not open.
    key(Qt.Key.Key_Escape)
    settle(lambda: not guide_up(), what="the guide gone")
    settle(lambda: w._source_strip.isVisible(), what="the strip")
    w._source_strip_btn.click()
    idle(0.2)
    assert not guide_up() and len(asked_pins) == n + 3
    # The grown-up's PIN: the guide, and Connect goes ahead.
    pins["given"] = "4321"
    w._source_strip_btn.click()
    settle(lambda: guide_up(), what="the guide with the PIN")
    ov._cards["iptv"].click()
    ov._server.setText(srv.base)
    ov._user.setText("anna")
    ov._pw.setText("s3cret pw")
    ov._c_connect.click()
    settle(lambda: ov._done, 20, "connected with the PIN")

elif SCENARIO == "over_guide":
    w.show_welcome()
    settle(lambda: guide_up() and in_guide(), what="the guide")
    w.enter_tv_mode()
    assert not w._tv_on
    for value in ("on", "toggle"):
        try:
            w.tv_command("tv", value)
        except ValueError:
            pass
        else:
            raise AssertionError("TV mode asked over the guide: " + value)
    assert not w._tv_on
    w.tv_command("tv", "off")                    # off is always fine
    w._open_palette()
    assert getattr(w, "_palette", None) is None
    M = Qt.KeyboardModifier
    for k, mod in ((Qt.Key.Key_K, M.ControlModifier),
                   (Qt.Key.Key_F, M.ControlModifier | M.ShiftModifier),
                   (Qt.Key.Key_T, M.ControlModifier | M.ShiftModifier),
                   (Qt.Key.Key_T, M.NoModifier)):
        key(k, mod)
        idle(0.3)
        assert guide_up() and in_guide() and not w._tv_on, (k, mod)
        assert getattr(w, "_palette", None) is None, (k, mod)
        assert getattr(w, "_place", "") != "search", (k, mod)

elif SCENARIO in ("settings_explore", "settings_demo"):
    from PyQt6.QtWidgets import QDialog as _D
    from dopeiptv.ui import dialogs
    from dopeiptv.ui.mw_settings import _SettingsSources
    srv = Served(xtream_panel(channels=5))
    row = {"name": "Mine", "kind": "xtream", "server": srv.base, "username": "anna",
           "password": "s3cret pw", "refresh": "never"}
    dialogs.PlaylistDialog.exec = lambda self: 1
    dialogs.PlaylistDialog.values = lambda self: dict(row)
    w.show_welcome()
    settle(lambda: guide_up(), what="the guide")
    if SCENARIO == "settings_demo":
        w._welcome._w_demo.clicked.emit()
        settle(lambda: isinstance(w.client, DemoClient) and w.list_model.rowCount() >= 6,
               what="the demo")
    else:
        key(Qt.Key.Key_Escape)
        settle(lambda: not guide_up(), what="exploring")
    assert w._no_own_source()

    class Shell:
        def current_key(self):
            return "playlists"
    srcs = _SettingsSources(w, _D(w), Shell(), {}, lambda: False)
    srcs.edit = lambda pid, under=None: None
    srcs.add()
    pid = store.playlists()[0]["id"]
    settle(lambda: isinstance(w.client, XtreamClient), 20, "the source in use")
    settle(lambda: w.list_model.rowCount() == 5, 20, "its channels")
    assert store.active_id == pid and not w._no_own_source() and w._demo_kept is None
    assert not privacy.keeping_nothing() and not w._source_strip.isVisible()
    # A second one added now is not switched to by itself.
    row = dict(row, name="Other")
    srcs.add()
    idle(0.5)
    assert store.active_id == pid

elif SCENARIO == "demo":
    w.show_welcome()
    settle(lambda: guide_up(), what="the guide")
    ov = w._welcome
    # One's own file watched before, and where it got to.
    w.history.add("/m/film.mkv", "film.mkv", None, "/m/film.mkv", "local")
    w.resume.record("local", "/m/film.mkv", 300.0, 3000.0)
    before_place = s.value("last_place", "")
    before_density = s.value("tv_density", "")
    ov._w_demo.clicked.emit()
    settle(lambda: isinstance(w.client, DemoClient) and w.list_model.rowCount() >= 6,
           what="the demo")
    assert [e.get("name") for e in w.history.items()] == ["film.mkv"]
    assert [(k, p) for k, p, _d, _t in w.resume.positions("local")] == [("/m/film.mkv", 300.0)]
    # The readers kept per source take the demo for no stored source.
    assert w._own_source_id() is None and w._health_source_id() == "" and w._chno_pid() is None
    # Its films on a loop are looped; another channel and a film are not.
    chs = {c["stream_id"]: c for c in w.client.live_streams(None)}
    w.play_live_channel(chs[3])
    idle(0.3)
    assert played and played[-1][1] is True, played          # loop_next armed
    w.play_live_channel(chs[1])
    idle(0.3)
    assert played[-1][1] is False, played
    films = w.client.vod_streams(None)
    w._show_place("films")
    idle(0.3)
    w._film_play(films[0])
    idle(0.3)
    assert played[-1][1] is False, played
    # Nothing of the demo kept: not in History, not where the app opens.
    w._place_tv_density("airy")
    w._show_place("tv")
    idle(0.3)
    assert [e.get("name") for e in w.history.items()] == ["film.mkv"]
    assert s.value("last_place", "") == before_place
    assert s.value("tv_density", "") == before_density
    text = written()
    assert "Big Buck" not in text and "Akamai" not in text
    # One's own file played meanwhile is kept as ever.
    assert not privacy.playback_private("local", "/m/other.mkv")
    w.history.add("/m/other.mkv", "other.mkv", None, "/m/other.mkv", "local")
    w.resume.record("local", "/m/other.mkv", 400.0, 3000.0)
    # The strip says it is the demo; Esc from the guide keeps the demo and
    # its words ("No provider yet" was said here).
    settle(lambda: w._source_strip.isVisible(), what="the strip")
    assert w._source_strip_text.text() == tr("onb_strip_demo")
    assert w._source_strip_leave.isVisible()
    w._source_strip_btn.click()
    settle(lambda: guide_up(), what="the guide from the strip")
    assert ov._stack.currentIndex() == PAGE_CHOOSE
    key(Qt.Key.Key_Escape)
    settle(lambda: not guide_up(), what="the guide gone")
    assert isinstance(w.client, DemoClient) and w._source_strip_text.text() == tr("onb_strip_demo")
    # Put away for the session: gone while the demo lasts.
    w._source_strip.away.click()
    idle(0.1)
    assert not w._source_strip.isVisible() and w._strip_away == "demo"
    w._show_place("films")
    w._show_place("tv")
    idle(0.2)
    assert not w._source_strip.isVisible()
    # Leave the demo: no source at all, nothing of it behind - and the
    # strip back, for that.
    w._source_strip_leave.click()
    settle(lambda: isinstance(w.client, OfflineClient) and not isinstance(w.client, DemoClient),
           what="the demo left")
    assert w._demo_kept is None and not privacy.keeping_nothing()
    assert w.windowTitle() == APP_NAME and w.list_model.rowCount() == 0
    settle(lambda: w._source_strip.isVisible()
           and w._source_strip_text.text() == tr("onb_strip_none"), what="no source's words")
    assert not w._source_strip_leave.isVisible()
    assert [e.get("name") for e in w.history.items()] == ["other.mkv", "film.mkv"]
    assert sorted((k, p) for k, p, _d, _t in w.resume.positions("local")) == [
        ("/m/film.mkv", 300.0), ("/m/other.mkv", 400.0)]
    assert "other.mkv" in written()

elif SCENARIO == "demo_files":
    w.show_welcome()
    settle(lambda: guide_up(), what="the guide")
    w._welcome._w_demo.clicked.emit()
    settle(lambda: isinstance(w.client, DemoClient), what="the demo")
    w._source_strip_btn.click()
    settle(lambda: guide_up(), what="the guide from the strip")
    w._welcome._cards[FILES].click()
    settle(lambda: not isinstance(w.client, DemoClient), what="the demo left")
    assert w._demo_kept is None and not privacy.keeping_nothing() and w.mode == "local"
    assert s.value("welcome_files_only") == "true" and w.windowTitle() == APP_NAME

assert boxes == [], boxes
print("REVIEW_OK " + SCENARIO, flush=True)
os._exit(0)
"""


def _env(tmp_path) -> dict:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        env[var] = str(tmp_path / var.lower())
    return env


@pytest.mark.parametrize("scenario", ["picker_first", "picker_over", "sync_profiles", "kids",
                                      "over_guide", "settings_explore", "settings_demo", "demo",
                                      "demo_files"])
def test_the_review_in_the_window(tmp_path, scenario):
    out = subprocess.run([sys.executable, "-c", _CHILD, scenario], cwd=_REPO_ROOT,
                         env=_env(tmp_path), capture_output=True, text=True, timeout=240)
    assert f"REVIEW_OK {scenario}" in out.stdout, (
        f"exit {out.returncode}\n" + out.stdout[-3000:] + out.stderr[-5000:])
