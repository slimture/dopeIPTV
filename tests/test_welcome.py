"""The first-run guide's card (ui/welcome.py) on its own: the welcome page's
one clear way on, the choice of what to watch, each kind's form and heading,
every shape of a provider's link, back and forth keeping what was typed,
one try per Connect, what the connect step says, a low window, a screen
reader's words, right to left.

These build the plain overlay (no MainWindow), in process; the window's
side - the tries against servers of the tests' own - is in
tests/test_welcome_paths.py, the whole window in tests/test_welcome_window.py.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSettings, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow, QWidget  # noqa: E402

from dopeiptv import i18n  # noqa: E402
from dopeiptv.i18n import tr  # noqa: E402
from dopeiptv.ui import a11y  # noqa: E402
from dopeiptv.ui.welcome import (  # noqa: E402
    BOX, FILES, IPTV, MEDIA, PAGE_CHOOSE, PAGE_FORM, PAGE_WELCOME, PAGE_WORK, WelcomeOverlay,
)


@pytest.fixture(scope="module")
def app():
    i18n.set_language("en")
    return QApplication.instance() or QApplication([])


def _overlay(settings=None):
    win = QMainWindow()
    s = settings
    if s is None:
        s = QSettings("dopeIPTV-test", "welcome")
        s.clear()
    got: dict = {"connect": [], "files": 0, "demo": 0, "explore": 0}
    ov = WelcomeOverlay(
        win, settings=s, on_connect=lambda *a: got["connect"].append(a),
        on_explore=lambda: got.__setitem__("explore", got["explore"] + 1),
        on_language_change=lambda c: None,
        on_demo=lambda: got.__setitem__("demo", got["demo"] + 1),
        on_files=lambda: got.__setitem__("files", got["files"] + 1))
    got["_win"] = win          # kept: Qt deletes the overlay with its window
    return ov, got


def _form(ov, family):
    ov._stack.setCurrentIndex(PAGE_CHOOSE)
    ov._cards[family].click()
    assert ov._stack.currentIndex() == PAGE_FORM


def _shown(ov, w) -> bool:
    return w.isVisibleTo(ov._stack.widget(ov._stack.currentIndex()))


# -- the welcome page and the choice --------------------------------------------------

def test_the_welcome_page_has_one_clear_way_on_and_two_quiet_links(app):
    ov, got = _overlay()
    page = ov._stack.widget(PAGE_WELCOME)
    assert ov._w_next.text() == tr("onb_get_started") and ov._w_next.objectName() == "Primary"
    # The only button on the page beside the Nerd link: the rest are links -
    # flat buttons of their own (a screen reader's button, a Tab stop).
    from PyQt6.QtWidgets import QPushButton

    from dopeiptv.ui.welcome import _Link
    buttons = [b for b in page.findChildren(QPushButton) if b.isVisibleTo(page)]
    links = {b for b in buttons if isinstance(b, _Link)}
    assert set(buttons) - links == {ov._w_next, ov._w_nerd}
    assert links == {ov._w_demo, ov._w_bring}
    assert all(b.isFlat() and b.focusPolicy() == Qt.FocusPolicy.StrongFocus for b in links)
    assert ov._w_demo.plain() == tr("onb_demo_link") and ov._w_bring.plain() == tr("onb_bring_offer")
    ov._w_next.click()
    assert ov._stack.currentIndex() == PAGE_CHOOSE
    ov._ch_back.click()
    assert ov._stack.currentIndex() == PAGE_WELCOME
    ov._w_demo.clicked.emit()
    assert got["demo"] == 1 and not ov.isVisible()


def test_what_to_watch_offers_four_answers_each_its_own_form(app):
    ov, got = _overlay()
    words = {f: c.words() for f, c in ov._cards.items()}
    assert words[IPTV] == (tr("onb_choose_iptv"), tr("onb_choose_iptv_sub"))
    assert "Plex" in words[MEDIA][1] and "HDHomeRun" in words[BOX][1] and "SAT>IP" in words[BOX][1]
    for family, heading, kinds in (
            (IPTV, tr("onb_head_xtream"), {"xtream", "m3u"}),
            (MEDIA, tr("onb_head_media", name="Jellyfin"), {"jellyfin", "emby", "plex"}),
            (BOX, tr("onb_head_box", name="Enigma2"), {"enigma2", "tvheadend", "hdhomerun", "satip"})):
        _form(ov, family)
        assert ov._c_title.text() == heading
        offered = {ov._conn_kind.itemData(i) for i in range(ov._conn_kind.count())}
        assert kinds <= offered and not offered - kinds - {"stalker", "vdr", "dvbviewer", "nextpvr",
                                                             "mythtv"}
        ov._c_back.click()
        assert ov._stack.currentIndex() == PAGE_CHOOSE
    # The heading follows the kind picked.
    _form(ov, IPTV)
    ov._set_kind("m3u")
    assert ov._c_title.text() == tr("onb_head_m3u") and _shown(ov, ov._browse)
    assert not _shown(ov, ov._user) and not _shown(ov, ov._pw)
    ov._cards[FILES].click()
    assert got["files"] == 1 and not ov.isVisible() and got["connect"] == []


def test_the_xtream_form_shows_only_its_own_rows(app):
    ov, _ = _overlay()
    _form(ov, IPTV)
    assert ov._conn_kind.currentData() == "xtream"
    for w in (ov._server, ov._user, ov._pw, ov._c_hint, ov._name):
        assert _shown(ov, w)
    for w in (ov._mac, ov._signin, ov._finder.row, ov._browse, ov._presets):
        assert not _shown(ov, w)


# -- the form: what Connect hands on -----------------------------------------------

def test_connect_hands_on_the_typed_source_and_its_name(app):
    ov, got = _overlay()
    _form(ov, IPTV)
    ov._name.setText("Hemma")
    ov._server.setText("http://s:80")
    ov._user.setText("u")
    ov._pw.setText("p")
    ov._c_connect.click()
    assert got["connect"] == [("http://s:80", "u", "p", "xtream", "Hemma", "", None, {})]
    assert ov._stack.currentIndex() == PAGE_WORK and ov.trying()
    # Connect again while it tries: nothing more (one connection).
    ov._do_connect()
    ov._c_connect.click()
    assert len(got["connect"]) == 1


def test_a_missing_field_is_said_on_the_form(app):
    ov, got = _overlay()
    _form(ov, IPTV)
    ov._server.setText("http://s:80")
    ov._c_connect.click()
    assert got["connect"] == [] and ov._c_err.text() == tr("onb_fill_all")
    assert ov._stack.currentIndex() == PAGE_FORM
    ov._set_kind("m3u")
    ov._server.clear()
    ov._c_connect.click()
    assert got["connect"] == [] and ov._c_err.text() == tr("onb_fill_m3u")


def test_m3u_needs_only_its_address(app):
    ov, got = _overlay()
    _form(ov, IPTV)
    ov._set_kind("m3u")
    ov._name.setText("Sport")
    ov._server.setText("http://x/p.m3u")
    ov._c_connect.click()
    assert got["connect"][0][:5] == ("http://x/p.m3u", "", "", "m3u", "Sport")


def test_a_portal_takes_its_address_and_a_mac(app):
    from dopeiptv.ui.dialogs import stalker_available
    if not stalker_available():
        pytest.skip("no Stalker client in this build")
    ov, got = _overlay()
    _form(ov, IPTV)
    ov._set_kind("stalker")
    assert ov._c_title.text() == tr("onb_head_stalker")
    assert _shown(ov, ov._mac) and not _shown(ov, ov._user)
    ov._server.setText("http://portal.example.com/c/")
    ov._mac.setText("00-1a-79-12-34-5")          # one digit short
    ov._c_connect.click()
    assert got["connect"] == [] and _shown(ov, ov._c_err)
    ov._mac.setText("00-1a-79-12-34-56")
    ov._c_connect.click()
    assert got["connect"][0][:6] == ("http://portal.example.com/c/", "", "", "stalker", "",
                                     "00:1A:79:12:34:56")
    # A portal's address pasted in is never taken for an Xtream or M3U link.
    ov._work_back()
    ov._server.clear()
    ov._server.insert("http://portal.example.com/get.php?username=a&password=b")
    assert ov._conn_kind.currentData() == "stalker"


LINKS = [
    ("http://prov.tv:8080/get.php?username=joe&password=secret&type=m3u_plus&output=ts",
     "http://prov.tv:8080"),
    ("http://prov.tv:8080/player_api.php?username=joe&password=secret", "http://prov.tv:8080"),
    ("http://prov.tv:8080/live/joe/secret/1.ts", "http://prov.tv:8080"),
    ("http://prov.tv/get.php?username=joe&password=secret&type=m3u_plus", "http://prov.tv"),
    ("http://prov.tv:8080/live/joe/secret/1.ts/", "http://prov.tv:8080"),
    ("http://prov.tv:8080/", None),
    ("   https://prov.tv/player_api.php?username=joe&password=secret   ", "https://prov.tv"),
    ("https://prov.tv:8443/joe/secret/77", "https://prov.tv:8443"),
    ("http://prov.tv/iptv/get.php?username=joe&password=secret", "http://prov.tv/iptv"),
]


@pytest.mark.parametrize("link,server", LINKS)
def test_every_shape_of_an_xtream_link_fills_the_form(app, link, server):
    ov, got = _overlay()
    _form(ov, IPTV)
    # insert() emits textEdited as a paste does: the fields fill at once.
    ov._server.clear()
    ov._server.insert(link)
    if server is None:
        # A server alone, with or without its slash: left as typed.
        assert ov._user.text() == "" and ov._server.text() == link.strip()
        return
    assert ov._conn_kind.currentData() == "xtream"
    assert (ov._server.text(), ov._user.text(), ov._pw.text()) == (server, "joe", "secret")
    ov._c_connect.click()
    assert got["connect"][0][:4] == (server, "joe", "secret", "xtream")


def test_a_plain_host_is_left_as_typed(app):
    ov, _ = _overlay()
    _form(ov, IPTV)
    ov._user.setText("me")
    ov._server.clear()
    ov._server.insert("http://server:80")
    assert ov._server.text() == "http://server:80" and ov._user.text() == "me"
    assert ov._conn_kind.currentData() == "xtream"


@pytest.mark.parametrize("link", [
    "https://host.tv/playlist.m3u8", "http://host.tv/list.m3u?token=abc",
    "http://host.tv:9191/get.php?type=m3u", "/home/anna/Lists/tv.m3u",
    "file:///home/anna/My%20Lists/tv.m3u8", "C:\\Lists\\tv.m3u"])
def test_a_playlist_pasted_where_the_xtream_server_goes_switches_to_m3u(app, link):
    ov, got = _overlay()
    _form(ov, IPTV)
    ov._server.clear()
    ov._server.insert(link)
    assert ov._conn_kind.currentData() == "m3u"
    assert ov._server.text() == link and ov._c_title.text() == tr("onb_head_m3u")
    ov._c_connect.click()
    assert got["connect"][0][:4] == (link, "", "", "m3u")


def test_back_and_forth_keep_what_was_typed_for_each_kind(app):
    ov, _ = _overlay()
    _form(ov, IPTV)
    ov._server.setText("http://xt.invalid:8080")
    ov._user.setText("anna")
    ov._pw.setText("pw")
    ov._name.setText("Hemma")
    ov._c_back.click()
    ov._cards[MEDIA].click()
    assert ov._server.text() == "" and ov._conn_kind.currentData() == "jellyfin"
    ov._server.setText("http://10.0.0.5:8096")
    ov._c_back.click()
    ov._ch_back.click()                               # all the way to the first page
    ov._w_next.click()
    ov._cards[IPTV].click()
    assert (ov._server.text(), ov._user.text(), ov._pw.text(), ov._name.text()) == (
        "http://xt.invalid:8080", "anna", "pw", "Hemma")
    ov._c_back.click()
    ov._cards[MEDIA].click()
    assert ov._server.text() == "http://10.0.0.5:8096"


# -- the connect step -----------------------------------------------------------------

def test_the_connect_step_says_what_came_or_why_not(app):
    ov, got = _overlay()
    ov.show()
    _form(ov, IPTV)
    ov._server.setText("http://s:80")
    ov._user.setText("u")
    ov._pw.setText("p")
    ov._c_connect.click()
    page = ov._stack.widget(PAGE_WORK)
    assert ov._k_title.text() == tr("onb_connecting", name="s:80")
    assert ov._k_bar.isVisibleTo(page) and not ov._k_go.isVisibleTo(page)
    ov.connect_step("vod")
    assert ov._k_step.text() == tr("onb_step_vod")
    # Why not, in plain words; Back to the form as typed, or try again.
    ov.connect_failed(tr("onb_err_login"))
    assert ov._k_title.text() == tr("onb_fail_title") and ov._k_text.text() == tr("onb_err_login")
    assert ov._k_go.text() == tr("sp_retry") and ov._k_back.isVisibleTo(page)
    ov._k_go.click()
    assert len(got["connect"]) == 2 and ov.trying()
    ov._k_back.click()                                 # while it tries: stopped, the form back
    assert ov._stack.currentIndex() == PAGE_FORM and not ov.trying()
    assert (ov._server.text(), ov._user.text(), ov._pw.text()) == ("http://s:80", "u", "p")
    ov._c_connect.click()
    ov.connect_done(1234, 56, 0, tr("diag_conn_limit", active=1, maxc=1))
    assert ov._k_title.text() == tr("onb_done_title")
    from PyQt6.QtCore import QLocale
    assert tr("count_channels", n=QLocale().toString(1234)) in ov._k_text.text()
    assert tr("count_movies", n="56") in ov._k_text.text()
    assert "series" not in ov._k_text.text()           # none came: not said
    assert ov._k_warn.isVisibleTo(page) and not ov._k_back.isVisibleTo(page)
    ov._k_go.click()                                   # Start watching
    assert not ov.isVisible()


def test_esc_while_it_tries_drops_the_try(app):
    ov, got = _overlay()
    stopped = []
    ov.connect_cancelled.connect(lambda: stopped.append(1))
    _form(ov, IPTV)
    ov._set_kind("m3u")
    ov._server.setText("http://x/p.m3u")
    ov._c_connect.click()
    ov.dismiss()
    assert stopped == [1] and got["explore"] == 1 and not ov.isVisible()
    ov.connect_failed("late")                          # a late answer changes nothing
    assert not ov.trying()


# -- how it looks: a low window, a screen reader, right to left ----------------------

def _pages(ov):
    yield PAGE_WELCOME
    yield PAGE_CHOOSE
    for family in (IPTV, MEDIA, BOX):
        _form(ov, family)
        yield PAGE_FORM
    ov._set_kind("enigma2")
    ov._server.setText("http://192.168.1.10")
    ov._c_connect.click()
    yield PAGE_WORK
    ov.connect_failed(tr("onb_err_unreachable"))
    yield PAGE_WORK


def test_every_page_fits_a_low_window_and_scrolls(app):
    """The first time the guide showed, a wrapped label got the height of
    fewer lines: on a low screen the box finder's note was cut off. Every
    line shows, the card stays inside the window, never wider than it."""
    from dopeiptv.ui import box_finder
    ov, got = _overlay()
    win = got["_win"]
    win.setCentralWidget(QWidget())
    win.resize(1100, 520)
    win.show()
    ov.cover()
    for idx in _pages(ov):
        if idx == PAGE_FORM and ov._conn_kind.currentData() in box_finder.BOX_KINDS:
            ov._finder.status.setText(box_finder.nothing_found_text())
            ov._finder.status.show()
        ov._stack.setCurrentIndex(idx)
        ov._fit_card(idx)
        for _ in range(4):
            app.processEvents()
        page = ov._stack.currentWidget()
        for lbl in page.findChildren(QLabel):
            if lbl.isVisible() and lbl.wordWrap() and lbl.text():
                assert lbl.height() >= lbl.heightForWidth(lbl.width()), lbl.text()[:30]
        card = ov._card.geometry()
        assert card.top() >= 0 and card.bottom() < ov.height(), idx
        assert page.width() <= ov._scroll.viewport().width()
    win.close()


def test_every_control_has_words_for_a_screen_reader(app):
    ov, got = _overlay()
    got["_win"].resize(1100, 800)
    got["_win"].show()
    ov.cover()
    for idx in _pages(ov):
        ov._stack.setCurrentIndex(idx)
        app.processEvents()
        missing = list(a11y.unnamed(ov._stack.currentWidget()))
        assert not missing, (idx, [type(w).__name__ for w in missing])
    got["_win"].close()


@pytest.mark.parametrize("lang", ["ar", "sv"])
def test_another_language_and_right_to_left(app, lang):
    i18n.set_language(lang)
    try:
        app.setLayoutDirection(Qt.LayoutDirection.RightToLeft if i18n.is_rtl()
                               else Qt.LayoutDirection.LeftToRight)
        ov, got = _overlay()
        got["_win"].resize(1100, 800)
        got["_win"].show()
        ov.cover()
        assert ov._w_next.text() == tr("onb_get_started") != "Get started"
        card = ov._cards[IPTV]
        assert card.words()[0] == tr("onb_choose_iptv") and card.isRightToLeft() == (lang == "ar")
        assert card.heightForWidth(160) > card.heightForWidth(1000)     # its words wrap
        img = card.grab()
        assert not img.isNull()
        _form(ov, BOX)
        assert ov._c_title.text() == tr("onb_head_box", name="Enigma2")
        got["_win"].close()
    finally:
        i18n.set_language("en")
        app.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
