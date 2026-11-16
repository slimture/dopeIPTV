"""Welcome/onboarding overlay: the connect step passes a playlist name
through, and the Trakt step shows a clear 'connected' confirmation.

These build a plain QWidget overlay (no MainWindow / OpenGL), so they run
in-process without the subprocess dance the player tests need.
"""
import pytest


def _overlay(qkind="xtream"):
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication, QMainWindow

    from dopeiptv.ui.welcome import WelcomeOverlay

    app = QApplication.instance() or QApplication([])
    win = QMainWindow()
    s = QSettings("dopeIPTV-test", "welcome")
    s.clear()
    captured = {}

    def on_connect(server, user, pw, kind, name, mac=None):
        captured.update(server=server, user=user, pw=pw, kind=kind, name=name)
        if mac is not None:
            captured["mac"] = mac

    ov = WelcomeOverlay(
        win, settings=s, on_connect=on_connect, on_explore=lambda: None,
        on_connect_trakt=lambda: None, on_language_change=lambda c: None,
        on_demo=lambda: None)
    return app, win, ov, captured


def test_connect_passes_playlist_name():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    _app, _win, ov, captured = _overlay()
    ov._stack.setCurrentIndex(1)
    ov._name.setText("Hemma")
    ov._server.setText("http://s:80")
    ov._user.setText("u")
    ov._pw.setText("p")
    ov._do_connect()
    assert captured == {"server": "http://s:80", "user": "u", "pw": "p",
                        "kind": "xtream", "name": "Hemma"}
    # Missing name is allowed (the mixin fills a default), so an empty name
    # still submits.
    assert ov._stack.currentIndex() == 2   # advanced to the Trakt step


def test_m3u_only_needs_url_and_optional_name():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    _app, _win, ov, captured = _overlay()
    ov._stack.setCurrentIndex(1)
    ov._conn_kind.setCurrentIndex(ov._conn_kind.findData("m3u"))
    ov._name.setText("Sport")
    ov._server.setText("http://x/p.m3u")
    ov._do_connect()
    assert captured["kind"] == "m3u"
    assert captured["name"] == "Sport"
    assert captured["user"] == "" and captured["pw"] == ""


def test_stalker_portal_takes_url_and_mac():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    from dopeiptv.ui.dialogs import stalker_available
    if not stalker_available():
        pytest.skip("no Stalker client in this build")
    _app, _win, ov, captured = _overlay()
    ov._stack.setCurrentIndex(1)
    ov._conn_kind.setCurrentIndex(ov._conn_kind.findData("stalker"))
    # Only the portal's address and a MAC address: no username or password.
    assert ov._mac.isVisibleTo(ov) and not ov._user.isVisibleTo(ov)
    ov._server.setText("http://portal.example.com/c/")
    ov._mac.setText("00-1a-79-12-34-5")          # one digit short
    ov._do_connect()
    assert captured == {} and ov._c_err.isVisibleTo(ov)
    assert ov._stack.currentIndex() == 1
    ov._mac.setText("00-1a-79-12-34-56")
    ov._do_connect()
    assert captured == {"server": "http://portal.example.com/c/", "user": "", "pw": "",
                        "kind": "stalker", "name": "", "mac": "00:1A:79:12:34:56"}
    assert ov._stack.currentIndex() == 2
    # A portal's address pasted in is never taken for an Xtream or M3U link.
    ov._server.clear()
    ov._server.insert("http://portal.example.com/get.php?username=a&password=b")
    assert ov._conn_kind.currentData() == "stalker"


def test_pasted_xtream_link_splits_into_fields():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    _app, _win, ov, captured = _overlay()
    ov._stack.setCurrentIndex(1)
    # Simulate pasting a full Xtream link: insert() emits textEdited just like
    # a real paste, so the fields must fill immediately - no focus change.
    ov._server.clear()
    ov._server.insert(
        "http://prov.tv:8080/get.php?username=joe&password=secret&type=m3u_plus")
    assert ov._conn_kind.currentData() == "xtream"   # preferred mode selected
    assert ov._server.text() == "http://prov.tv:8080"
    assert ov._user.text() == "joe"
    assert ov._pw.text() == "secret"
    # And a normal Connect then submits the split-out credentials.
    ov._do_connect()
    assert captured["server"] == "http://prov.tv:8080"
    assert captured["user"] == "joe"
    assert captured["pw"] == "secret"


def test_plain_host_is_left_untouched():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    _app, _win, ov, _captured = _overlay()
    ov._stack.setCurrentIndex(1)
    # A manually typed host with no credentials must not be rewritten, so
    # server/username/password entry keeps working the old way.
    ov._user.setText("me")
    ov._server.clear()
    ov._server.insert("http://server:80")   # partial host, no credentials
    assert ov._server.text() == "http://server:80"
    assert ov._user.text() == "me"


def test_pasted_m3u_link_switches_mode():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    _app, _win, ov, _captured = _overlay()
    ov._stack.setCurrentIndex(1)
    # A plain playlist URL flips the mode to M3U (no credentials to fill).
    ov._server.clear()
    ov._server.insert("https://host.tv/playlist.m3u8")
    assert ov._conn_kind.currentData() == "m3u"
    assert ov._server.text() == "https://host.tv/playlist.m3u8"


def test_trakt_connected_shows_confirmation():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    _app, _win, ov, _captured = _overlay()
    ov._stack.setCurrentIndex(2)
    assert not ov._trakt_connected
    ov.set_trakt_connected(True)
    assert ov._trakt_connected
    assert "✓" in ov._t_ok.text()          # green ✓ confirmation text set
    # The label was made visible and the finish button reads as done.
    assert ov._t_ok.isVisibleTo(ov._stack.widget(2))
    from dopeiptv.i18n import tr
    assert ov._t_finish.text() == tr("onb_finish_done")


def test_the_connect_step_fits_a_low_window_the_first_time():
    """The first time the wizard showed (before a page had been laid out at
    the card's width) a wrapped label got the height of fewer lines: on a
    low screen the box finder's note was cut mid-sentence and the card ran
    past the window. Now every line shows, the card stays inside the
    window and the page scrolls, never wider than the card."""
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    from PyQt6.QtWidgets import QLabel, QWidget

    from dopeiptv.ui import box_finder
    app, win, ov, _ = _overlay()
    win.setCentralWidget(QWidget())
    win.resize(1100, 600)
    win.show()
    ov.cover()
    app.processEvents()
    ov._toggle_nerd()
    ov._stack.setCurrentIndex(1)
    ov._conn_kind.setCurrentIndex(ov._conn_kind.findData("enigma2"))
    ov._finder.status.setText(box_finder.nothing_found_text())
    ov._finder.status.show()
    ov._fit_card(1)
    for _ in range(5):
        app.processEvents()
    page = ov._stack.currentWidget()
    for lbl in page.findChildren(QLabel):
        if lbl.isVisible() and lbl.wordWrap() and lbl.text():
            assert lbl.height() >= lbl.heightForWidth(lbl.width()), lbl.text()[:30]
    card = ov._card.geometry()
    assert card.top() >= 0 and card.bottom() < ov.height()
    assert ov._stack.height() > ov._scroll.height()          # it scrolls
    assert page.width() <= ov._scroll.viewport().width()     # nothing cut on the right
    win.close()


def test_the_xtream_form_shows_only_its_own_rows_from_the_start():
    """Before a kind or a language was picked, the connect step showed the
    MAC address, the media server sign-in and Find on my network for
    Xtream too."""
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    _app, _win, ov, _ = _overlay()
    page = ov._stack.widget(1)
    assert ov._conn_kind.currentData() == "xtream"
    for w in (ov._server, ov._user, ov._pw):
        assert w.isVisibleTo(page)
    for w in (ov._mac, ov._signin, ov._finder.row):
        assert not w.isVisibleTo(page)
