"""The first-run guide never left half-done, never under TV mode, never over
what plays - as a second review found it, each finding held here:

- TV mode and the guide never stack: "Start in TV mode" waits at a start the
  guide comes to - also behind "Who's watching?", with a PIN or without -
  TV mode is not turned on while the guide waits, and the guide shown while
  TV mode is on leaves it first (TV mode's own "Who's watching?" the
  window's then). Every key - Enter, Esc - is the guide's; TV mode works
  once the guide is gone;
- a media server's sign-in under way stops with the guide - Esc, the form's
  Back - and its answer, whenever it comes (a Plex PIN approved minutes
  later), connects and keeps nothing;
- what a command plays (a file opened with the app, a link, --play) is seen:
  the guide goes first as its Esc closes it, a link's question first, and
  one waiting behind "Who's watching?" does not come back over it; a PIN
  still holds everything, a kids profile's limits still hold and are said;
- a login typed for a try that failed is not kept once the guide goes;
- the window closed while the Bring step still fetches leaves sync off, its
  code not kept;
- a source a backup or a sync brought whose first login failed: the strip
  says it could not be reached - with Try again and Settings > Sources -
  never "no source yet".

The window runs as a child of its own on real profile settings
(core/profiles.py); nothing reaches the network beyond 127.0.0.1."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytest.importorskip("PyQt6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget  # noqa: E402

from dopeiptv import i18n  # noqa: E402
from dopeiptv.ui.welcome import IPTV, MEDIA, PAGE_CHOOSE, PAGE_FORM, WelcomeOverlay  # noqa: E402

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def app():
    i18n.set_language("en")
    return QApplication.instance() or QApplication([])


def _overlay():
    win = QMainWindow()
    win.setCentralWidget(QWidget())
    s = QSettings("dopeIPTV-test", "welcome-exits")
    s.clear()
    got: dict = {"connect": [], "demo": 0, "files": 0, "explore": 0}
    ov = WelcomeOverlay(
        win, settings=s, on_connect=lambda *a: got["connect"].append(a),
        on_explore=lambda: got.__setitem__("explore", got["explore"] + 1),
        on_language_change=lambda c: None,
        on_demo=lambda: got.__setitem__("demo", got["demo"] + 1),
        on_files=lambda: got.__setitem__("files", got["files"] + 1))
    win.show()
    ov.cover()
    got["_win"] = win
    return ov, got


def _form(ov, family, kind):
    ov._stack.setCurrentIndex(PAGE_CHOOSE)
    ov._cards[family].click()
    ov._set_kind(kind)
    assert ov._stack.currentIndex() == PAGE_FORM


def _failed_try(ov):
    _form(ov, IPTV, "xtream")
    ov._server.setText("http://line.example.com:8080")
    ov._user.setText("anna")
    ov._pw.setText("s3cret pw")
    ov._name.setText("Mine")
    ov._c_connect.click()
    assert ov._last_try
    ov.connect_failed("no")


@pytest.mark.parametrize("way", ["esc", "demo", "files"])
def test_a_failed_trys_login_goes_with_the_guide(app, way):
    ov, got = _overlay()
    _failed_try(ov)
    if way == "esc":
        ov.dismiss()
    elif way == "demo":
        ov._k_back.click()
        ov._c_back.click()
        ov._ch_demo.click()
    else:
        ov._k_back.click()
        ov._c_back.click()
        ov._cards["files"].click()
    assert not ov.isVisible()
    assert ov._last_try == () and ov._extra == {}
    for field in (ov._server, ov._user, ov._pw, ov._mac, ov._name):
        assert field.text() == "", field
    # Nor kept for its kind, to come back when it is picked again.
    ov.reset(PAGE_CHOOSE)
    _form(ov, IPTV, "m3u")
    ov._set_kind("xtream")
    assert ov._user.text() == "" and ov._pw.text() == ""
    assert got["connect"] and got[{"esc": "explore", "demo": "demo", "files": "files"}[way]] == 1
    got["_win"].close()


def test_the_forms_back_keeps_what_was_typed_and_stops_the_sign_in(app):
    ov, got = _overlay()
    _form(ov, MEDIA, "jellyfin")
    si = ov._signin
    asked: list = []
    si._run = lambda fn, done, *args: asked.append((si._gen, done))
    ov._server.setText("http://127.0.0.1:9")
    ov._user.setText("anna")
    ov._pw.setText("pw")
    ov._c_connect.click()
    assert si.busy and ov._connect_signed and asked
    ov._c_back.click()
    assert ov._stack.currentIndex() == PAGE_CHOOSE
    assert not si.busy and not ov._connect_signed and si.sign_btn.isEnabled()
    assert (ov._server.text(), ov._user.text(), ov._pw.text()) == ("http://127.0.0.1:9", "anna", "pw")
    # The sign-in's answer comes after all: dropped - not signed in, nothing tried.
    gen, done = asked[-1]
    done((gen, {"token": "tok", "server": "http://127.0.0.1:9", "user": "anna", "user_id": "u"}, ""))
    assert not si.signed_in() and got["connect"] == []
    got["_win"].close()


# -- the whole window --------------------------------------------------------------------------

_CHILD = r"""
import faulthandler, json, os, sys, tempfile, threading, time
faulthandler.enable()
faulthandler.dump_traceback_later(200, exit=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings, Qt
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
boxes = []
from dopeiptv.ui import message_box
for name in ("error", "warning", "information"):
    setattr(message_box, name, lambda *a, _n=name, **k: boxes.append((_n, a[2:3])))
message_box.MessageDialog.exec = lambda self: boxes.append(("exec", self.text())) or 0

from dopeiptv import i18n
from dopeiptv.core import profiles as _prof
from dopeiptv.core import sync
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import OfflineClient, XtreamClient
from dopeiptv.ui import main_window as mw, profiles_ui, theme
from dopeiptv.ui.welcome import MEDIA, PAGE_CHOOSE, PAGE_FORM, PAGE_WELCOME
from tests.test_welcome_paths import Served, xtream_panel

SCENARIO = sys.argv[1]
i18n.set_language("en")
mw.launch_player = lambda *a, **k: None
tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "s.ini")
s = _prof.ProfileSettings(path, QSettings.Format.IniFormat)
_prof.start(s, [], "Me")
s.setValue("check_updates", "false")
pins = {"given": None}
profiles_ui.ask_pin = lambda *a, **k: pins["given"]
ps0 = _prof.ProfileStore(s)
me = ps0.profiles()[0]
if SCENARIO in ("tv_start", "tv_start_pin", "cmd_picker", "cmd_pin", "tv_picker_then_guide"):
    ps0.add("Other")
    ps0.set_ask_at_start(SCENARIO != "tv_picker_then_guide")
if SCENARIO in ("tv_start", "tv_start_pin"):
    s.setValue("tv_mode_start", "true")
if SCENARIO in ("tv_start_pin", "cmd_pin", "cmd_kids"):
    ps0.set_pin(me.id, "4321")
if SCENARIO == "cmd_kids":
    kid = ps0.add("Kid", kids=True)
if SCENARIO == "close_sync":
    gate = threading.Event()

    class SlowRemote:
        # dopeiptv.org's sync, slow to answer: the Bring step still fetches.
        def __init__(self, slot, endpoint=None, timeout=20.0, auth=""):
            pass

        @classmethod
        def of(cls, raw, endpoint=None, timeout=20.0):
            return cls(sync.derive(raw)[0])

        def get(self):
            gate.wait(20)
            raise sync.SyncError("net")

        def put(self, version_seen, blob):
            gate.wait(20)
            raise sync.SyncError("net")

        def delete(self):
            return True
    sync.Remote = SlowRemote
theme.apply_theme(s)
app.setStyleSheet(theme.build_style())
store = PlaylistStore(s)
w = mw.MainWindow(OfflineClient(), s, store)
played = []
if w.player is not None:
    def _play(url, title, *a, **k):
        played.append(url)
        return True
    w.player.play = _play
toasts = []
_toast = w._show_toast
w._show_toast = lambda text, ms=0: (toasts.append((text, guide_up())), _toast(text, ms))
w.resize(1100, 760)
w.show()
w.activateWindow()
QTest.qWaitForWindowActive(w, 3000)


def idle(secs):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        app.processEvents()
        w.pool.waitForDone(5)
        time.sleep(0.005)


def settle(cond, secs=15.0, what=""):
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


def key(k, mod=Qt.KeyboardModifier.NoModifier):
    QTest.keyClick(QApplication.focusWidget() or w, k, mod)
    app.processEvents()


def guide_up():
    ov = w._welcome
    return ov is not None and ov.isVisible()


def in_guide():
    f = QApplication.focusWidget()
    return f is not None and (f is w._welcome or w._welcome.isAncestorOf(f))


def tv_refused():
    try:
        w.tv_command("tv", "on")
    except ValueError:
        return not w._tv_on
    return False


idle(0.6)               # "Start in TV mode" is asked 300 ms in: it has been

if SCENARIO in ("tv_start", "tv_start_pin"):
    # No source, two profiles asked at start, "Start in TV mode": the guide
    # app.py shows waits behind the window's own "Who's watching?".
    w.show_welcome()
    idle(0.8)
    assert not w._tv_on, "TV mode under the waiting guide"
    assert w._profile_picker is not None and not guide_up()
    assert w._welcome_waits == PAGE_WELCOME
    w.enter_tv_mode()
    assert not w._tv_on and tv_refused()
    pins["given"] = "4321"
    assert w._profile_pick(me.id)
    settle(lambda: guide_up() and in_guide(), what="the guide with the keys")
    idle(0.5)
    assert not w._tv_on and guide_up()
    ov = w._welcome
    ov._w_next.setFocus()
    key(Qt.Key.Key_Return)
    settle(lambda: ov._stack.currentIndex() == PAGE_CHOOSE, what="Enter: the choice")
    assert not w._tv_on and tv_refused()
    key(Qt.Key.Key_Escape)
    settle(lambda: not guide_up(), what="Esc: the guide gone")
    w.enter_tv_mode()
    assert w._tv_on                                  # TV mode, once the guide is gone
    w.exit_tv_mode()

elif SCENARIO == "tv_then_guide":
    w.show_welcome()
    settle(lambda: guide_up(), what="the guide")
    assert not w._tv_on
    key(Qt.Key.Key_Escape)
    settle(lambda: not guide_up(), what="the guide gone")
    w.enter_tv_mode()
    assert w._tv_on
    idle(0.3)
    # The guide asked for while TV mode is on: TV mode goes first.
    w.show_welcome(PAGE_CHOOSE)
    settle(lambda: not w._tv_on and guide_up() and in_guide(), what="the guide, TV mode left")
    ov = w._welcome
    assert ov._stack.currentIndex() == PAGE_CHOOSE
    ov._cards["iptv"].setFocus()
    key(Qt.Key.Key_Return)
    settle(lambda: ov._stack.currentIndex() == PAGE_FORM, what="Enter on the card: its form")
    key(Qt.Key.Key_Escape)
    settle(lambda: not guide_up(), what="Esc: the guide gone")
    assert not w._tv_on

elif SCENARIO == "tv_picker_then_guide":
    # TV mode's own "Who's watching?" up as the guide is asked for: the
    # window's picker in its place, the guide after it.
    w.enter_tv_mode()
    assert w._tv_on
    w.show_profile_picker()
    assert w._tv_page.current == "profiles"
    w.show_welcome()
    settle(lambda: not w._tv_on, what="TV mode left")
    assert w._profile_picker is not None and not guide_up()
    assert w._welcome_waits == PAGE_WELCOME and tv_refused()
    assert w._profile_pick(me.id)
    settle(lambda: guide_up() and in_guide(), what="the guide after the pick")
    assert not w._tv_on

elif SCENARIO in ("signin_esc", "plex_esc"):
    from dopeiptv.core import xdg
    opened = []
    xdg.open_url = lambda url, *a, **k: opened.append(url) or True
    w.show_welcome()
    settle(lambda: guide_up(), what="the guide")
    ov = w._welcome
    tried = []
    ov._on_connect = lambda *a: tried.append(a)
    si = ov._signin
    asked = []
    si._run = lambda fn, done, *args: asked.append((si._gen, done))
    ov._get_started()
    ov._choose(MEDIA)
    if SCENARIO == "signin_esc":
        ov._set_kind("jellyfin")
        ov._server.setText("http://127.0.0.1:9")
        ov._user.setText("anna")
        ov._pw.setText("pw")
    else:
        ov._set_kind("plex")
    ov._c_connect.click()
    assert si.busy and ov._connect_signed and asked
    if SCENARIO == "plex_esc":
        gen, done = asked[-1]
        done((gen, {"id": 7, "code": "abcd"}, ""))   # the PIN made: the browser opened
        assert opened and si._timer.isActive()
        pin_gen = si._gen
    key(Qt.Key.Key_Escape)
    settle(lambda: not guide_up(), what="Esc: the guide gone")
    assert not si.busy and not ov._connect_signed and not si._timer.isActive()
    # Nothing typed stays in the guide closed.
    assert (ov._server.text(), ov._user.text(), ov._pw.text()) == ("", "", "")
    assert ov._last_try == ()
    # The answer comes after all - the password right, the PIN approved in
    # the browser minutes later: nothing is signed in, tried or kept.
    if SCENARIO == "signin_esc":
        gen, done = asked[-1]
        done((gen, {"token": "tok", "server": "http://127.0.0.1:9", "user": "anna",
                    "user_id": "u"}, ""))
    else:
        si._pin_polled((pin_gen, "plex-token", ""))
        si._poll()
    idle(0.3)
    assert not si.signed_in() and len(asked) == 1
    # And a sign-in that ends now, whichever way, connects nothing either:
    # with the guide closed (as the review found it) ...
    state = {"token": "tok", "server": "http://127.0.0.1:9", "user": "anna", "user_id": "u"}
    si._signed(dict(state))
    idle(0.3)
    assert tried == [] and w._wtry is None and store.playlists() == []
    assert not guide_up() and w._no_own_source()
    # ... nor with it open again on another page than the form.
    settle(lambda: w._source_strip.isVisible(), what="the strip")
    w._source_strip_btn.click()
    settle(lambda: guide_up(), what="the guide again")
    assert ov._stack.currentIndex() == PAGE_CHOOSE
    ov._connect_signed = True
    si._signed(dict(state))
    idle(0.3)
    assert tried == [] and w._wtry is None and store.playlists() == []

elif SCENARIO == "cmd_play":
    film = os.path.join(tmp, "film.mkv")
    with open(film, "wb") as f:
        f.write(b"x" * 100)
    w.show_welcome()
    settle(lambda: guide_up() and in_guide(), what="the guide")
    # A file opened with the app (--play, "Open with dopeIPTV").
    assert w.handle_command({"play": film}) == {"ok": True}
    settle(lambda: not guide_up() and played == [film], what="the film, the guide gone")
    assert w._welcome_waits is None and w._wtry is None
    idle(0.5)                                   # the pane follows the film to Local files
    w._show_place("tv")
    settle(lambda: w._source_strip.isVisible(), what="the strip")
    # A link: asked first, over the guide - Cancel leaves the guide as it was.
    w._source_strip_btn.click()
    settle(lambda: guide_up(), what="the guide again")
    answers = []

    def choice(title, message, options, dont_ask_setting=None):
        answers.append(title)
        return answers_given.pop(0)
    w._choice_dialog = choice
    answers_given = [1, 0]
    link = "http://192.0.2.5/live.ts"
    w.handle_command({"play": link, "link": True})
    idle(0.4)
    assert answers and guide_up() and played == [film]
    w.handle_command({"play": link, "link": True})
    settle(lambda: not guide_up() and played == [film, link], what="Play: the guide gone")
    assert len(answers) == 2

elif SCENARIO in ("cmd_picker", "cmd_pin"):
    film = os.path.join(tmp, "film.mkv")
    with open(film, "wb") as f:
        f.write(b"x" * 100)
    assert w._profile_picker is not None
    w.show_welcome()
    idle(0.2)
    assert not guide_up() and w._welcome_waits == PAGE_WELCOME
    got = w.handle_command({"play": film})
    idle(0.5)
    if SCENARIO == "cmd_pin":
        # Who is watching is still to be said: nothing played, the guide
        # still waits for the PIN, and comes after it.
        assert got == {"ok": False, "locked": True} and played == []
        assert w._welcome_waits == PAGE_WELCOME
        pins["given"] = "4321"
        assert w._profile_pick(me.id)
        settle(lambda: guide_up() and in_guide(), what="the guide after the PIN")
    else:
        assert played == [film] and w._welcome_waits is None
        assert w._profile_pick(me.id)
        idle(0.6)
        assert not guide_up()                        # not back over what plays

elif SCENARIO == "cmd_kids":
    film = os.path.join(tmp, "film.mkv")
    with open(film, "wb") as f:
        f.write(b"x" * 100)
    w._profile_close_picker()
    w._profile_must_pick = False
    idle(0.2)
    assert w.switch_profile(kid.id) and w._profile_kids()
    w.show_welcome()
    settle(lambda: guide_up(), what="the guide")
    toasts.clear()
    w.handle_command({"play": film})
    idle(0.4)
    # A kids profile plays no file: nothing played - and said, the guide
    # out of the way so the words are seen.
    assert played == [] and not guide_up()
    assert (tr("profile_kids_not_here"), False) in toasts, toasts

elif SCENARIO == "close_sync":
    w.show_welcome()
    settle(lambda: guide_up(), what="the guide")
    ov = w._welcome
    ov._open_bring()
    ov._b_code.setText(sync.new_code())
    ov._ask_sync()
    settle(lambda: w._wsync_pending and w._sync_busy, what="the sync fetching")
    assert w._sync_on() and sync.code_of(s) is not None
    state_file = w._sync_path()
    real_exit = os._exit
    os._exit = lambda code=0: None              # the window's own hard exit: carried on here
    w.close()
    os._exit = real_exit
    gate.set()
    # Nothing came: sync stays off, as it was - its code not kept.
    assert sync.code_of(s) is None and not w._sync_on()
    s.sync()
    with open(path, encoding="utf-8") as f:
        assert sync.K_CODE not in f.read()
    assert not os.path.exists(state_file)

elif SCENARIO == "unreached":
    # A backup's playlist taken in at the first start; the server turns its
    # login down.
    srv = Served(xtream_panel(pw="another pw"))
    w.show_welcome()
    settle(lambda: guide_up(), what="the guide")
    before = w._wizard_source_ids()
    store.import_data([{"name": "Home IPTV", "kind": "xtream", "server": srv.base,
                        "username": "anna", "password": "s3cret pw", "refresh": "never"}])
    came = [p for p in store.playlists() if str(p.get("id")) not in before]
    w._wizard_load(came)
    settle(lambda: boxes, 20, "the login turned down")
    assert [b[0] for b in boxes] == ["warning"], boxes          # said, as ever
    boxes.clear()
    idle(0.3)
    assert type(w.client) is OfflineClient and not guide_up()
    settle(lambda: w._source_strip.isVisible(), what="the strip")
    assert w._source_strip_text.text() == tr("onb_strip_unreached", name="Home IPTV")
    assert w._source_strip_btn.text() == tr("sp_retry")
    assert w._source_strip_leave.isVisible()
    assert w._source_strip_leave.text() == w.settings_link_text("playlists")
    # Settings > Sources from it.
    at = []
    w.open_settings_at = lambda page: at.append(page)
    w._source_strip_leave.click()
    assert at == ["playlists"]
    # Try again, the server answering now: the source in use, the strip gone.
    srv.routes.update(xtream_panel())
    w._source_strip_btn.click()
    settle(lambda: isinstance(w.client, XtreamClient), 20, "the source in use")
    settle(lambda: w.list_model.rowCount() == 12, 20, "its channels")
    assert not w._source_strip.isVisible() and w._unreached == ""

assert boxes == [], boxes
print("EXITS_OK " + SCENARIO, flush=True)
os._exit(0)
"""


def _env(tmp_path) -> dict:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", DOPEIPTV_NETWORK="0")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        env[var] = str(tmp_path / var.lower())
    return env


@pytest.mark.parametrize("scenario", [
    "tv_start", "tv_start_pin", "tv_then_guide", "tv_picker_then_guide",
    "signin_esc", "plex_esc",
    "cmd_play", "cmd_picker", "cmd_pin", "cmd_kids",
    "close_sync", "unreached"])
def test_the_guide_in_the_window(tmp_path, scenario):
    out = subprocess.run([sys.executable, "-c", _CHILD, scenario], cwd=_REPO_ROOT,
                         env=_env(tmp_path), capture_output=True, text=True, timeout=240)
    assert f"EXITS_OK {scenario}" in out.stdout, (
        f"exit {out.returncode}\n" + out.stdout[-3000:] + out.stderr[-5000:])
