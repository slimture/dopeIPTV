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

    def on_connect(server, user, pw, kind, name):
        captured.update(server=server, user=user, pw=pw, kind=kind, name=name)

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


def test_pasted_xtream_link_splits_into_fields():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    _app, _win, ov, captured = _overlay()
    ov._stack.setCurrentIndex(1)
    # Simulate pasting a full Xtream link into the server field; the
    # editingFinished handler fans it out into server/username/password.
    ov._server.setText(
        "http://prov.tv:8080/get.php?username=joe&password=secret&type=m3u_plus")
    ov._maybe_split_xtream_link()
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
    ov._server.setText("http://server:80")
    ov._user.setText("me")
    ov._maybe_split_xtream_link()
    assert ov._server.text() == "http://server:80"
    assert ov._user.text() == "me"


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
