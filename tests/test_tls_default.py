"""Every internet stream's certificate is checked by default; one that does
not check out is asked about once, per source, and the answer is the
source's own option - taken back on its page, held back by sync and named
by a restore (core/nerd.checks_certificate, the window's _tls_offer)."""
from __future__ import annotations

from types import SimpleNamespace

from dopeiptv.core import backup, nerd
from dopeiptv.i18n import tr

_app = None


def test_a_certificate_that_does_not_check_out_is_said_so(monkeypatch):
    from dopeiptv.providers import diagnostics
    from requests.exceptions import SSLError

    def refuse(*_a, **_k):
        raise SSLError("certificate verify failed")
    monkeypatch.setattr(diagnostics.requests, "get", refuse)
    assert diagnostics._probe_url("https://p.example/live/u/p/1.ts") == tr("diag_certificate")


class _Store:
    def __init__(self, pl):
        self.pl = pl

    def active(self):
        return self.pl

    def get(self, pid):
        return self.pl if pid == self.pl["id"] else None

    def update(self, pid, **kw):
        self.pl = dict(self.pl, **kw)


def _window(pl):
    from dopeiptv.ui.main_window import MainWindow
    told, retried = [], []
    w = SimpleNamespace(playlist_store=_Store(pl), _diag_shown=True, _stream_retries=3)
    w._TLS_NO_SOURCE = MainWindow._TLS_NO_SOURCE
    w._show_action_toast = lambda text, ms, go, label=None, decide=False, key=None: told.append(
        (text, go, label, decide, key))
    w._retry_last_stream = lambda: retried.append(True)
    w._tls_offer = MainWindow._tls_offer.__get__(w)
    return w, told, retried


def test_asked_once_and_the_answer_is_the_sources_own():
    pl = {"id": "p1", "name": "Pirat", "server": "https://p.example", "options": {"proxy": ""}}
    w, told, retried = _window(pl)
    w._tls_offer({"kind": "live", "url": "https://p.example/live/u/p/1.ts"})
    (text, go, label, decide, key), = told
    assert "Pirat" in text and label == tr("tls_offer_go") and decide and key == "tls-offer-p1"
    assert not nerd.tls_unchecked(w.playlist_store.pl["options"])      # nothing without the answer
    go()
    assert w.playlist_store.pl["options"] == {"proxy": "", nerd.TLS_UNCHECKED: "true"}
    assert nerd.tls_unchecked(nerd.source())
    assert retried == [True] and w._diag_shown is False and w._stream_retries == 0
    told.clear()
    w._tls_offer({"kind": "live"})                                      # asked once
    assert told == []
    nerd.set_source(None)


def test_what_is_no_sources_is_never_asked_about():
    pl = {"id": "p1", "name": "Pirat", "options": {}}
    w, told, _r = _window(pl)
    for kind in ("radio", "web", "local", "recording"):
        w._tls_offer({"kind": kind})
    w._tls_offer(None)
    assert told == []


def test_the_answer_is_a_setting_sync_holds_back_and_a_restore_names():
    from dopeiptv.core import sync
    from dopeiptv.ui import sync_review
    assert "tls_unchecked" in backup.RISKY_SOURCE and sync_review.labels_known()
    taken, waits = sync.hold_back({"name": "N", "options": {"tls_unchecked": "true"}}, {"name": "N"})
    assert waits == {"tls_unchecked": "true"} and "tls_unchecked" not in taken.get("options", {})


def test_the_source_page_switch_takes_it_back():
    from PyQt6.QtWidgets import QApplication
    global _app
    _app = QApplication.instance() or QApplication([])
    from dopeiptv.ui.dialogs import SourceFields
    from dopeiptv.ui.settings_ui import ToggleSwitch
    f = SimpleNamespace()
    f.values = SourceFields.values.__get__(f)
    for name in ("name", "server", "epg_url", "pw", "mac"):
        setattr(f, name, SimpleNamespace(text=lambda: ""))
    f.kind = SimpleNamespace(currentData=lambda: "xtream")
    f.refresh = SimpleNamespace(currentData=lambda: 0)
    f._username = lambda: ""
    f.normal_mac = lambda _m: ""
    f.nerd_panel = None
    f._had_token = False
    f._options = {nerd.TLS_UNCHECKED: "true", "proxy": "http://x"}
    f.tls_box = ToggleSwitch(True)
    assert f.values()["options"][nerd.TLS_UNCHECKED] == "true"
    f.tls_box.setChecked(False)
    assert f.values()["options"] == {"proxy": "http://x"}
