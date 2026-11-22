"""A Kodi's login over plain http is asked about once per Kodi, before it
is first sent: in the cast window before the player here lets go of
anything, through the window's _choice_dialog - so a cast run from the
phone or in TV mode is asked there - and "Use HTTPS" tried first, before
anything else goes to that Kodi."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class _Kodi:
    def __init__(self, asks=True):
        self.asks, self.allowed, self.https = asks, [], []
        from dopeiptv.providers.kodi import KodiDevice
        self.dev = KodiDevice("Den", "192.168.1.30", 8080, "kodi", "pw", manual=True)

    def asks_first(self, name):
        return self.asks

    def device(self, name):
        return self.dev

    def allow_plain(self, name):
        self.allowed.append(name)
        self.asks = False

    def use_https(self, name):
        self.https.append(name)
        return name


class _Hub:
    def __init__(self, kodi):
        self.kodi, self.casts, self.added = kodi, [], []

    def scan(self):
        return ["Den (Kodi)"]

    def ident(self, name):
        return ""

    def where(self, name):
        return "192.168.1.30"

    def cast(self, name, url, title, *a, **k):
        # What reached the receiver - after "Use HTTPS" was tried, if asked.
        self.casts.append((name, list(self.kodi.https)))
        return name

    def plays_as_is(self, name):
        return True

    def is_kodi(self, name):
        return True

    def is_dlna(self, name):
        return False

    def is_airplay(self, name):
        return False


def _host(answer):
    from dopeiptv.ui.mw_cast import _CastMixin
    from dopeiptv.ui.mw_recording import _RecordingMixin

    class Host:
        _kodi_plain_ask = _CastMixin._kodi_plain_ask
        _choice_dialog = _RecordingMixin._choice_dialog

        def __init__(self):
            self.cast = _Hub(_Kodi())
            self.asked = []
            self._phone_ask_hook = self._phone

        def _phone(self, title, message, options, dont=None):
            self.asked.append((title, message, [label for label, _k in options],
                               [kind for _l, kind in options]))
            return answer
    return Host()


def test_the_question_names_the_kodi_and_cancel_is_the_default():
    from dopeiptv.i18n import tr
    h = _host(None)                         # closed: no answer
    assert h._kodi_plain_ask("Den (Kodi)") == ""
    title, message, labels, kinds = h.asked[0]
    assert title == tr("kodi_plain_title")
    assert "Den" in message and "192.168.1.30:8080" in message
    assert labels == [tr("kodi_plain_send"), tr("kodi_plain_https"), tr("common_cancel")]
    assert kinds[-1] == "primary"           # Cancel, the default
    assert h.cast.kodi.allowed == []


def test_send_anyway_is_kept_with_that_kodi_and_never_asked_again():
    h = _host(0)
    assert h._kodi_plain_ask("Den (Kodi)") == "send"
    assert h.cast.kodi.allowed == ["Den (Kodi)"]
    assert h._kodi_plain_ask("Den (Kodi)") == "send" and len(h.asked) == 1


def test_use_https_is_the_callers_to_try_and_nothing_is_kept_here():
    h = _host(1)
    assert h._kodi_plain_ask("Den (Kodi)") == "https"
    assert h.cast.kodi.allowed == [] and h.cast.kodi.https == []


def test_a_kodi_without_a_login_or_over_https_is_not_asked():
    h = _host(1)
    h.cast.kodi.asks = False
    assert h._kodi_plain_ask("Den (Kodi)") == "send" and h.asked == []


def test_the_phone_and_tv_mode_get_the_question_through_the_windows_path():
    """While a phone request runs its _phone_ask_hook answers; in TV mode
    _tv_ask does - _kodi_plain_ask asks only through _choice_dialog."""
    import inspect

    from dopeiptv.ui import mw_cast
    src = inspect.getsource(mw_cast._CastMixin._kodi_plain_ask)
    assert "self._choice_dialog(" in src and "QMessageBox" not in src
    h = _host(0)
    h._phone_ask_hook = None
    tv = []
    h._tv_on = True
    h._tv_ask = lambda title, message, options, dont=None: tv.append(title) or 2
    assert h._kodi_plain_ask("Den (Kodi)") == "" and len(tv) == 1


@pytest.fixture
def window():
    from PyQt6.QtCore import QSettings, QThreadPool
    from PyQt6.QtWidgets import QApplication, QWidget
    app = QApplication.instance() or QApplication([])
    win = QWidget()
    win.settings = QSettings("dopeiptv-test", "kodi-plain")
    win.settings.clear()
    win.pool, win._cast_device, win._cast_ctx = QThreadPool(), None, {}
    win.app = app
    win.stopped = []
    win.stop_local_playback_for_cast = lambda: win.stopped.append(True) or False
    yield win
    win.settings.clear()


def _cast_with(window, answer):
    from dopeiptv.ui import cast_dialog
    window.cast = _Hub(_Kodi())
    window._kodi_plain_ask = lambda name: answer
    d = cast_dialog.CastDialog(window, "http://a/1.ts", "News", tracks={"audio": []},
                               probe=False, live=True, managing=True)
    window.settings.setValue("cast_devices", "Den (Kodi)")
    d.list.clear()
    d.list.addItem("Den (Kodi)")
    d.list.setCurrentRow(0)
    d._cast()
    window.pool.waitForDone(3000)
    for _ in range(5):
        window.app.processEvents()
    d.close()
    return window.cast


def test_cancel_in_the_cast_window_sends_nothing_and_stops_nothing(window):
    from dopeiptv.i18n import tr
    hub = _cast_with(window, "")
    assert hub.casts == [] and window.stopped == []
    assert tr("cast_err_kodi_plain", name="Den (Kodi)")


def test_use_https_is_tried_before_the_cast_is_sent(window):
    hub = _cast_with(window, "https")
    assert hub.casts == [("Den (Kodi)", ["Den (Kodi)"])] and window.stopped == [True]


def test_send_anyway_casts_as_before(window):
    hub = _cast_with(window, "send")
    assert hub.casts == [("Den (Kodi)", [])]
