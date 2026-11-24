"""The first-run guide in the whole window, as a first start shows it
(app.py: no source yet, OfflineClient, show_welcome) - each path a run of
its own, driven as a person would:

- an Xtream panel with the keyboard alone: Enter on Get started and on the
  card, the panel's link pasted, Enter to connect and to start watching -
  its channels in the list, the source kept, one login only;
- the language picked on the first page (Swedish, at once) and a
  playlist's link: its channels in the list;
- the demo: its channels on All, its guide, its films under Movies, the
  strip that says it is the demo, nothing of it in the settings - and left
  for a source of one's own through that strip: nothing of it behind;
- just my files and radio: Local files, not asked again, the strip on TV;
- Arabic and Hebrew in a small window: every page mirrored, inside the
  card, every control named - and the notices' bell at the sidebar's foot
  once the guide goes;
- the notices while the guide is up: a note, a message and a decision
  posted draw nothing over it and take no keys from it, the guide's own
  errors stay in it, the panel does not open over it, Esc is the guide's
  (or the open panel's first, never both); and after every way out of the
  guide - connect, the demo, a backup's or a sync's sources, skipped - the
  bell at the sidebar's foot, its panel and ✕, the decision that waited, a
  job on the line folding back to the bell, Tab from the source to
  Settings to the bell. No message box anywhere.

The servers are the tests' own on 127.0.0.1 (tests/test_welcome_paths.py,
which drives every kind and every failure through the guide alone)."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytest.importorskip("PyQt6.QtWidgets")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import faulthandler, os, sys, tempfile, time
faulthandler.enable()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings, Qt
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QAbstractButton, QApplication, QLabel, QLineEdit, QWidget
app = QApplication.instance() or QApplication([])
# Every message box the app would show, kept - there must be none.
boxes = []
from dopeiptv.ui import message_box
for name in ("error", "warning", "information"):
    setattr(message_box, name, lambda *a, _n=name, **k: boxes.append((_n, a[2:3])))
message_box.MessageDialog.exec = lambda self: boxes.append(("exec", self.text())) or 0

from dopeiptv import i18n
from dopeiptv.core import privacy
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import M3UClient, OfflineClient, XtreamClient
from dopeiptv.providers.demo import DemoClient
from dopeiptv.ui import a11y, main_window as mw, theme
from dopeiptv.ui.welcome import BOX, FILES, IPTV, MEDIA, PAGE_BRING, PAGE_CHOOSE, PAGE_FORM, \
    PAGE_WELCOME, PAGE_WORK
from tests.test_welcome_paths import Served, playlist, xtream_panel

SCENARIO = sys.argv[1]
i18n.set_language("en")
from dopeiptv.ui import mw_busy
toasts = []
_toast = mw_busy._BusyMixin._show_toast
def _kept_toast(self, text, duration_ms=0):
    toasts.append((text, self._welcome is not None and self._welcome.isVisible()))
    return _toast(self, text, duration_ms)
mw_busy._BusyMixin._show_toast = _kept_toast
mw.launch_player = lambda *a, **k: None                       # never a real player
path = os.path.join(tempfile.mkdtemp(), "s.ini")
s = QSettings(path, QSettings.Format.IniFormat)
s.setValue("check_updates", "false")
theme.apply_theme(s)
app.setStyleSheet(theme.build_style())
store = PlaylistStore(s)
w = mw.MainWindow(OfflineClient(), s, store)
if w.player is not None:
    w.player.play = lambda *a, **k: True
w.resize(1100, 760)
w.show()
w.activateWindow()
QTest.qWaitForWindowActive(w, 3000)
w.show_welcome()                         # as app.py does on a first start


def settle(cond, secs=15.0, what=""):
    end = time.monotonic() + secs
    while not cond() and time.monotonic() < end:
        app.processEvents()
        w.pool.waitForDone(5)
        time.sleep(0.005)
    app.processEvents()
    assert cond(), "timed out: " + what


def idle(secs):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def key(k, mod=Qt.KeyboardModifier.NoModifier):
    QTest.keyClick(QApplication.focusWidget(), k, mod)
    app.processEvents()


def paste(text):
    QApplication.clipboard().setText(text)
    key(Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)


def written():
    # Everything the settings file holds, as text.
    s.sync()
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def list_names():
    return [w.list_model.item_at(i).get("name") for i in range(w.list_model.rowCount())]


ov = w._welcome
hub = w.notices
settle(lambda: ov.isVisible() and ov._stack.currentIndex() == PAGE_WELCOME, what="the guide")
assert hub.held                          # the notices wait while it covers the window


def nothing_over_the_guide(what):
    # What the notices draw on the window itself (the stand-ins, the panel)
    # is hidden; the foot's own are under the guide, which covers them.
    for name in ("_activity_float", "_decision_float", "_notice_panel"):
        assert getattr(w, name).isHidden(), (what, name)
    for name in ("_decision_card", "_activity_line"):
        assert getattr(w, name).isHidden(), (what, name)
    # The bell stays in the foot, under the guide (raised over the window's
    # content), never over it.
    assert w.centralWidget().isAncestorOf(w._notice_bell) and ov.isVisible()
    assert ov.parentWidget() is w and w.children().index(ov) > w.children().index(w.centralWidget())


def foot_after(what):
    # Out of the guide: the bell in the sidebar's foot, its panel and ✕, a
    # job on the line folding back to the bell, Tab from the source to
    # Settings to the bell.
    bell, line, panel = w._notice_bell, w._activity_line, w._notice_panel
    settle(lambda: not ov.isVisible() and not hub.held, what=what + ": the notices back")
    settle(lambda: bell.isVisible(), what=what + ": the bell")
    assert w._side_foot.isAncestorOf(bell), what
    # (No avatar with no source of one's own - the demo, files and radio,
    # skipped: Settings then the bell.)
    avatar = w._playlist_btn
    assert avatar.isVisible() == (store.active() is not None), what
    if avatar.isVisible():
        avatar.setFocus(Qt.FocusReason.TabFocusReason)
        app.processEvents()
        key(Qt.Key.Key_Tab)
    else:
        w._settings_btn.setFocus(Qt.FocusReason.TabFocusReason)
        app.processEvents()
    assert QApplication.focusWidget() is w._settings_btn, (what, a11y.describe(QApplication.focusWidget()))
    key(Qt.Key.Key_Tab)
    assert QApplication.focusWidget() is bell, (what, a11y.describe(QApplication.focusWidget()))
    QTest.mouseClick(bell, Qt.MouseButton.LeftButton)
    settle(lambda: panel.isVisible(), what=what + ": the panel")
    QTest.mouseClick(panel.close_btn, Qt.MouseButton.LeftButton)
    settle(lambda: panel.isHidden(), what=what + ": its close button")
    tok = hub.start("Loading the list", kind="list")
    settle(lambda: line.isVisible(), what=what + ": the job on the line")
    hub.finish(tok)
    settle(lambda: line.isHidden() and bell.isVisible(), 20, what + ": folded to the bell")
assert ov.geometry() == w.rect() or ov.geometry().contains(w.centralWidget().geometry()), ov.geometry()


def connect_keyboard_only(link, rows):
    # Get started and the IPTV card with Enter; the link pasted; Enter to
    # connect and to start watching.
    settle(lambda: QApplication.focusWidget() is ov._w_next, what="Get started has the keys")
    key(Qt.Key.Key_Return)
    settle(lambda: ov._stack.currentIndex() == PAGE_CHOOSE, what="what to watch")
    assert QApplication.focusWidget() is ov._cards[IPTV]
    # The start's own place shown under the guide (100 ms in - on a slow
    # computer as Get started is pressed) leaves the keys with the guide.
    w._show_place("tv")
    assert QApplication.focusWidget() is ov._cards[IPTV], a11y.describe(QApplication.focusWidget())
    key(Qt.Key.Key_Return)
    settle(lambda: ov._stack.currentIndex() == PAGE_FORM, what="the form")
    assert QApplication.focusWidget() is ov._server, a11y.describe(QApplication.focusWidget())
    # Tab goes round the form's own controls, each named - never to the
    # window's under it - and Shift+Tab back.
    stops = []
    for _ in range(40):
        key(Qt.Key.Key_Tab)
        fw = QApplication.focusWidget()
        assert fw is not None and ov.isAncestorOf(fw), fw and a11y.describe(fw)
        assert fw.isVisible() and a11y.spoken_name(fw), a11y.describe(fw)
        stops.append(fw)
        if fw is ov._server:
            break
    assert stops[-1] is ov._server, [a11y.spoken_name(x) for x in stops]
    for x in (ov._user, ov._pw, ov._c_connect, ov._c_back):
        assert x in stops, (a11y.describe(x), [a11y.spoken_name(y) for y in stops])
    key(Qt.Key.Key_Backtab)
    assert QApplication.focusWidget() is stops[-2], a11y.describe(QApplication.focusWidget())
    key(Qt.Key.Key_Tab)
    assert QApplication.focusWidget() is ov._server
    paste(link)
    key(Qt.Key.Key_Return)
    settle(lambda: ov._done, 20, "connected")
    assert QApplication.focusWidget() is ov._k_go
    key(Qt.Key.Key_Return)
    settle(lambda: not ov.isVisible(), what="the guide gone")
    settle(lambda: w.list_model.rowCount() == rows, 20, f"{rows} rows")


if SCENARIO == "xtream_keys":
    srv = Served(xtream_panel())
    link = f"{srv.base}/get.php?username=anna&password=s3cret%20pw&type=m3u_plus&output=ts"
    connect_keyboard_only(link, 12)
    assert isinstance(w.client, XtreamClient), type(w.client)
    # The login the link held went into the fields, was kept with the source
    # and left the guide with Done: nothing of it stays in a hidden field.
    assert ov._user.text() == "" and ov._pw.text() == "" and ov._server.text() == ""
    assert not ov._last_try
    assert tr("onb_done_title") == ov._k_title.text()
    words = ov._k_text.text()
    assert "12" in words and "7" in words and "3" in words, words
    pls = store.playlists()
    assert len(pls) == 1 and store.active()["kind"] == "xtream" and store.active()["username"] == "anna"
    assert store.active()["password"] == "s3cret pw"          # the link's, decoded
    assert list_names()[:2] == ["Ch 1", "Ch 2"], list_names()[:3]
    # One login: the window took the client that logged in at Connect.
    logins = [q for p, q in srv.seen if p == "/player_api.php" and "action" not in q]
    assert len(logins) == 1, logins
    assert not w._source_strip.isVisible()
    assert store.active()["name"] in w.windowTitle(), w.windowTitle()
    # Back from nowhere: Esc on the window does not bring the guide back.
    key(Qt.Key.Key_Escape)
    assert not ov.isVisible()
    foot_after("connected")

elif SCENARIO == "m3u_sv":
    combo = ov._lang_combo
    english = ov._w_next.text()
    combo.setCurrentIndex(combo.findData("sv"))
    app.processEvents()
    assert i18n.current_language() == "sv"
    assert ov._w_next.text() == tr("onb_get_started") != english, ov._w_next.text()
    assert ov._w_demo.plain() == tr("onb_demo_link")
    srv = Served({"/lists/tv.m3u8": (200, {"Content-Type": "audio/mpegurl"}, playlist(5))})
    QTest.mouseClick(ov._w_next, Qt.MouseButton.LeftButton)
    settle(lambda: ov._stack.currentIndex() == PAGE_CHOOSE)
    assert ov._cards[IPTV].words()[0] == tr("onb_choose_iptv")
    QTest.mouseClick(ov._cards[IPTV], Qt.MouseButton.LeftButton)
    settle(lambda: ov._stack.currentIndex() == PAGE_FORM)
    ov._server.setFocus()
    paste(f"{srv.base}/lists/tv.m3u8?token=abc")
    assert ov._conn_kind.currentData() == "m3u", ov._conn_kind.currentData()
    assert ov._c_title.text() == ov._heading("m3u")
    QTest.mouseClick(ov._c_connect, Qt.MouseButton.LeftButton)
    settle(lambda: ov._done, 20, "connected")
    assert tr("count_channels", n="5") in ov._k_text.text(), ov._k_text.text()
    assert ov._k_go.text() == tr("onb_start_watching")
    QTest.mouseClick(ov._k_go, Qt.MouseButton.LeftButton)
    settle(lambda: not ov.isVisible())
    assert isinstance(w.client, M3UClient)
    settle(lambda: w.list_model.rowCount() > 0, 20, "the playlist's rows")
    assert all(n.startswith("Channel ") for n in list_names()), list_names()
    assert len(w.client.live_streams(None)) == 5
    assert store.active()["server"] == f"{srv.base}/lists/tv.m3u8?token=abc"

elif SCENARIO == "demo":
    QTest.mouseClick(ov._w_demo, Qt.MouseButton.LeftButton)
    settle(lambda: not ov.isVisible(), what="the guide gone")
    foot_after("the demo")
    assert isinstance(w.client, DemoClient) and w.windowTitle().startswith(tr("demo_title"))
    assert privacy.keeping_nothing()
    settle(lambda: w.list_model.rowCount() == 6, what="every channel on All")
    assert list_names() == [c["name"] for c in w.client.live_streams(None)], list_names()
    settle(lambda: w._source_strip.isVisible(), what="the strip")
    assert w._source_strip_text.text() == tr("onb_strip_demo")
    assert w._source_strip_btn.text() == tr("onb_add_own_source")
    # No "Recently added": nothing of it was added by anyone.
    cats = [w.cat_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(w.cat_list.count())]
    assert "__recent__" not in cats and None in cats, cats
    # Its guide, made here: every channel has programmes now.
    settle(lambda: w.xmltv.is_loaded(), what="the demo's guide")
    for ch in w.client.live_streams(None):
        starts, stops, titles, _d = w.xmltv.schedule(ch)
        now = time.time()
        assert any(a <= now < b for a, b in zip(starts, stops, strict=True)), ch["name"]
    assert w._show_place("guide") is not False
    settle(lambda: w._guide_page_showing() and len(w._guide_grid.channels) == 6, what="the guide page")
    w._show_place("tv")
    # Its films under Movies, with drawn posters.
    w._show_place("films")
    settle(lambda: w.mode == "vod" and sorted(list_names()) == ["Big Buck Bunny", "Sintel",
                                                                "Tears of Steel"],
           what="the films")
    assert all(w.list_model.item_at(i).get("stream_icon", "").startswith("drawn:")
               for i in range(w.list_model.rowCount()))
    assert w._source_strip.isVisible()
    # A channel played: kept nowhere - not in History, not in the settings.
    w._show_place("tv")
    settle(lambda: w.list_model.rowCount() == 6)
    sintel = next(c for c in w.client.live_streams(None) if c["name"] == "Sintel 24/7")
    w.play_live_channel(sintel)
    settle(lambda: (w._last_playback or {}).get("key") == sintel["stream_id"], what="playing")
    assert privacy.playback_private("live", sintel["stream_id"])
    idle(0.3)
    assert w.history.items() == []
    shown = written()
    for name in ("Sintel", "Akamai", "Big Buck Bunny", "demo-live", "drawn:"):
        assert name not in shown, name
    # Left for a source of one's own through the strip: the guide on what
    # to watch, the panel typed in.
    srv = Served(xtream_panel())
    QTest.mouseClick(w._source_strip_btn, Qt.MouseButton.LeftButton)
    settle(lambda: ov.isVisible() and ov._stack.currentIndex() == PAGE_CHOOSE, what="the choice")
    QTest.mouseClick(ov._cards[IPTV], Qt.MouseButton.LeftButton)
    settle(lambda: ov._stack.currentIndex() == PAGE_FORM)
    ov._set_kind("xtream")
    for field, text in ((ov._server, srv.base), (ov._user, "anna"), (ov._pw, "s3cret pw")):
        field.setFocus()
        field.selectAll()
        QTest.keyClicks(field, text)
    QTest.mouseClick(ov._c_connect, Qt.MouseButton.LeftButton)
    settle(lambda: ov._done, 20, "connected")
    QTest.mouseClick(ov._k_go, Qt.MouseButton.LeftButton)
    settle(lambda: not ov.isVisible())
    settle(lambda: isinstance(w.client, XtreamClient) and w.list_model.rowCount() == 12, 20,
           "the panel's channels")
    assert not privacy.keeping_nothing() and w._demo_kept is None
    assert not privacy.playback_private("live", 1)
    assert not w._source_strip.isVisible()
    assert tr("demo_title") not in w.windowTitle()
    assert w.history.items() == []
    assert w._last_playback is None and not (w.player is not None and w.player.current_url)
    shown = written()
    for name in ("Sintel", "Akamai", "Big Buck Bunny", "demo-live", "drawn:"):
        assert name not in shown, name
    # What plays now is kept as ever.
    ch1 = w.client.live_streams(None)[0]
    w.play_live_channel(ch1)
    settle(lambda: (w._last_playback or {}).get("key") == ch1["stream_id"], what="playing")
    settle(lambda: [e.get("name") for e in w.history.items()] == ["Ch 1"], what="History")

elif SCENARIO == "files":
    QTest.mouseClick(ov._w_next, Qt.MouseButton.LeftButton)
    settle(lambda: ov._stack.currentIndex() == PAGE_CHOOSE)
    QTest.mouseClick(ov._cards[FILES], Qt.MouseButton.LeftButton)
    settle(lambda: not ov.isVisible())
    settle(lambda: w._place == "local" and w.mode == "local", what="Local files")
    assert s.value("welcome_files_only") == "true"
    assert not w._source_strip.isVisible()            # its own place: no strip
    assert store.playlists() == []
    w._show_place("tv")
    settle(lambda: w._source_strip.isVisible(), what="the strip on TV")
    assert w._source_strip_text.text() == tr("onb_strip_none")
    QTest.mouseClick(w._source_strip_btn, Qt.MouseButton.LeftButton)
    settle(lambda: ov.isVisible() and ov._stack.currentIndex() == PAGE_CHOOSE)
    settle(lambda: hub.held)
    ov.dismiss()
    foot_after("files and radio")

elif SCENARIO == "bring":
    # A sync's or a backup's sources landed (mw_onboarding._wizard_load):
    # the guide goes and the app uses one.
    srv = Served(xtream_panel())
    pl = store.add({"name": "Brought", "kind": "xtream", "server": srv.base,
                    "username": "anna", "password": "s3cret pw"})
    w._wizard_load([pl])
    settle(lambda: not ov.isVisible())
    settle(lambda: isinstance(w.client, XtreamClient) and w.list_model.rowCount() == 12, 20,
           "the brought source's channels")
    foot_after("brought")

elif SCENARIO == "notices":
    settle(lambda: QApplication.focusWidget() is ov._w_next, what="Get started has the keys")
    w._show_action_toast("Port 3673 is taken", 15000, lambda: None, decide=True)
    w._error("boom")
    hub.note("Copied")
    w._show_toast("A passing word")
    idle(0.6)
    nothing_over_the_guide("posted")
    assert QApplication.focusWidget() is ov._w_next, a11y.describe(QApplication.focusWidget())
    assert len(hub.decisions()) == 1 and hub.note_text() == ""
    # Nothing opens the panel over it: the menu's way in, the bell's.
    w._notices_open_history()
    w._notices_open_panel()
    idle(0.2)
    assert w._notice_panel.isHidden()
    w._set_sidebar_collapsed(True)              # the rail: its stand-ins are the window's
    idle(0.3)
    nothing_over_the_guide("the rail")
    w._set_sidebar_collapsed(False)
    idle(0.2)
    # The guide's own errors stay in it, as words of its own.
    said = len(hub.messages())
    key(Qt.Key.Key_Return)
    settle(lambda: ov._stack.currentIndex() == PAGE_CHOOSE)
    key(Qt.Key.Key_Return)
    settle(lambda: ov._stack.currentIndex() == PAGE_FORM)
    ov._server.clear()
    ov._user.clear()
    ov._pw.clear()
    ov._server.setFocus()
    key(Qt.Key.Key_Return)
    assert ov._c_err.isVisible() and ov._c_err.text() == tr("onb_fill_all")
    closed = Served({})
    port = closed.server_address[1]
    closed.shutdown()
    closed.server_close()
    for field, text in ((ov._server, f"http://127.0.0.1:{port}"), (ov._user, "anna"),
                        (ov._pw, "pw")):
        field.setText(text)
    QTest.mouseClick(ov._c_connect, Qt.MouseButton.LeftButton)
    settle(lambda: ov._stack.currentIndex() == PAGE_WORK and not ov._trying, 20, "the try")
    assert ov._k_text.isVisible() and ov._k_title.text() == tr("onb_fail_title")
    assert len(hub.messages()) == said, [m.text for m in hub.messages()]
    nothing_over_the_guide("a failed try")
    assert ov.isAncestorOf(QApplication.focusWidget())
    # Esc: the guide's - it goes, and the notices come back with it.
    key(Qt.Key.Key_Escape)
    settle(lambda: not ov.isVisible(), what="Esc")
    settle(lambda: w._decision_card.isVisible(), what="the decision that waited")
    assert w._decision_card.text() == "Port 3673 is taken"
    assert w._notice_bell.badge() >= 1
    foot_after("skipped")
    # The panel open, then the guide: the panel goes, and one Esc closes
    # the guide alone.
    QTest.mouseClick(w._notice_bell, Qt.MouseButton.LeftButton)
    settle(lambda: w._notice_panel.isVisible())
    w.show_welcome(PAGE_CHOOSE)
    settle(lambda: ov.isVisible() and w._notice_panel.isHidden(), what="the panel gone")
    assert ov.isAncestorOf(QApplication.focusWidget())
    key(Qt.Key.Key_Escape)
    settle(lambda: not ov.isVisible())
    assert w._notice_panel.isHidden()
    # The panel open by itself: Esc closes it, and nothing else.
    QTest.mouseClick(w._notice_bell, Qt.MouseButton.LeftButton)
    settle(lambda: w._notice_panel.isVisible())
    full = w.isFullScreen()
    key(Qt.Key.Key_Escape)
    settle(lambda: w._notice_panel.isHidden(), what="Esc on the panel")
    assert not ov.isVisible() and w.isFullScreen() == full
    # What was said while the guide was up went through the notices, none
    # as a popup of its own.
    assert ("A passing word", True) in toasts, toasts

elif SCENARIO in ("small_ar", "small_he"):
    lang = SCENARIO.split("_")[1]
    w.resize(640, 480)
    settle(lambda: w.width() <= 1100)
    combo = ov._lang_combo
    combo.setCurrentIndex(combo.findData(lang))
    app.processEvents()
    idle(0.2)
    assert i18n.current_language() == lang
    assert QApplication.layoutDirection() == Qt.LayoutDirection.RightToLeft
    assert ov.isRightToLeft()
    assert ov._w_next.text() == tr("onb_get_started")

    def inside(page_idx, what):
        ov._stack.setCurrentIndex(page_idx)
        idle(0.15)
        card = ov._card.geometry()
        assert ov.rect().contains(card), (what, card, ov.rect())
        page = ov._stack.widget(page_idx)
        width = ov._scroll.viewport().width()
        for x in page.findChildren(QWidget):
            if not x.isVisibleTo(page) or x.width() <= 0:
                continue
            g = x.geometry().translated(x.parentWidget().mapTo(page, x.parentWidget().rect().topLeft())) \
                if x.parentWidget() is not page else x.geometry()
            assert g.left() >= -1 and g.right() <= width + 1, (what, a11y.describe(x), g, width)
        bad = [a11y.describe(x) for x in a11y.unnamed(page)]
        assert not bad, (what, bad)

    inside(PAGE_WELCOME, "welcome")
    inside(PAGE_CHOOSE, "choose")
    for family in (IPTV, MEDIA, BOX):
        ov._choose(family)
        inside(PAGE_FORM, "form " + family)
        back = ov._c_back
        page = ov._stack.widget(PAGE_FORM)
        # Mirrored: Back sits on the right.
        assert back.mapTo(page, back.rect().center()).x() > page.width() // 2, family
    ov._choose(IPTV)
    ov._start("http://127.0.0.1:9", "u", "p", "xtream", "")
    w._wizard_connect_cancel()
    ov.connect_failed(tr("onb_err_unreachable"))
    inside(PAGE_WORK, "work, failed")
    ov._work_back()
    inside(PAGE_BRING, "bring")
    # The cards' words in the language, the card no wider than the window.
    assert ov._cards[IPTV].words()[0] == tr("onb_choose_iptv")
    assert ov._card.width() <= w.width()
    # The bell waits under the guide, and is at the foot once it goes -
    # mirrored: before Settings, as the avatar is after it.
    nothing_over_the_guide("a small window")
    ov.dismiss()
    foot_after("right to left")
    bell, gear = w._notice_bell, w._settings_btn
    at = lambda x: x.mapTo(w, x.rect().topLeft())
    if w._sidebar_collapsed:                 # the icon rail: the bell under Settings
        assert at(bell).x() == at(gear).x() and at(bell).y() > at(gear).y(), (at(bell), at(gear))
    else:
        assert at(bell).x() < at(gear).x(), (at(bell), at(gear))
    # And with room: the sidebar open, the bell before Settings, mirrored.
    w.resize(1100, 760)
    w._set_sidebar_collapsed(False)
    settle(lambda: not w._sidebar_collapsed and bell.isVisible())
    idle(0.2)
    assert at(bell).x() < at(gear).x() and abs(at(bell).y() - at(gear).y()) <= 1, (at(bell), at(gear))

assert not boxes, boxes

print("WELCOME_WINDOW_OK", SCENARIO, flush=True)
os._exit(0)
"""


def _env(tmp_path) -> dict:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        env[var] = str(tmp_path / var.lower())
    return env


@pytest.mark.parametrize("scenario", ["xtream_keys", "m3u_sv", "demo", "files", "bring",
                                      "notices", "small_ar", "small_he"])
def test_the_guide_in_the_window(tmp_path, scenario):
    out = subprocess.run([sys.executable, "-c", _CHILD, scenario], cwd=_REPO_ROOT,
                         env=_env(tmp_path), capture_output=True, text=True, timeout=240)
    assert f"WELCOME_WINDOW_OK {scenario}" in out.stdout, (
        f"exit {out.returncode}\n" + out.stdout[-3000:] + out.stderr[-5000:])
