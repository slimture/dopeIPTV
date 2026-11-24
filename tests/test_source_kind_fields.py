"""Each kind of source keeps its own address and login.

Changing a source's Type in Settings left the Xtream address, user name and
password in the fields of every kind picked after it - a Jellyfin, a
Tvheadend, a box found on the network - and the page stored the source at
once as the new kind with that login. Now another kind shows its own fields
(empty for one not filled in), the kind picked back brings back what it
had, and nothing is stored until the new kind's own are there."""
from __future__ import annotations

import tempfile

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from dopeiptv.providers import mediaserver  # noqa: E402
from dopeiptv.ui.dialogs import PlaylistDialog  # noqa: E402
from dopeiptv.ui.source_page import SourcePage  # noqa: E402
from dopeiptv.ui.welcome import WelcomeOverlay  # noqa: E402

XTREAM = {"id": "xt1", "name": "Pirat", "kind": "xtream", "server": "http://xt.invalid:8080",
          "username": "xtuser", "password": "xtpw", "epg_url": "", "refresh": "never"}
JELLY = {"id": "jf1", "name": "Jellyfin test", "kind": "jellyfin",
         "server": "http://jelly.invalid:8096", "username": "anna", "password": "",
         "token": "tok", "options": {"user_id": "u1", "server_id": "s1"}}


@pytest.fixture(scope="module")
def app():
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _device():
    mediaserver.install_device_store(None)
    yield


class _Store:
    def __init__(self, *items: dict) -> None:
        self.items = [dict(p) for p in items]
        self.active_id = self.items[0]["id"]
        self.updates: list[tuple[str, dict]] = []

    def get(self, pid):
        return next((p for p in self.items if p["id"] == pid), None)

    def update(self, pid, **fields):
        self.updates.append((pid, fields))
        self.get(pid).update(fields)


def _page(store, pid):
    return SourcePage(store.get(pid), store=store, nerd_on=False)


def _pick(combo, kind):
    combo.setCurrentIndex(combo.findData(kind))


def _login(form):
    return form.server.text(), form.user.text(), form.pw.text()


def test_another_kind_never_shows_or_stores_the_xtream_login(app):
    store = _Store(XTREAM)
    page = _page(store, "xt1")
    for kind in ("tvheadend", "m3u", "stalker", "jellyfin", "enigma2", "hdhomerun"):
        _pick(page.kind, kind)
        assert _login(page) == ("", "", ""), kind
        assert page.mac.text() == ""
        # Not stored: the new kind has no address of its own yet.
        assert page.commit() is False
        assert store.get("xt1")["kind"] == "xtream"
    _pick(page.kind, "xtream")
    assert _login(page) == ("http://xt.invalid:8080", "xtuser", "xtpw")
    assert page.commit() is True
    page.leave()
    assert store.updates == []
    assert store.get("xt1") == XTREAM


def _warnings(monkeypatch):
    said = []
    from dopeiptv.ui import source_page
    monkeypatch.setattr(source_page.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: said.append(a[1:])))
    return said


def test_looking_through_the_types_is_no_change_to_warn_about(app, monkeypatch):
    # Settings closed, or the page left, after only picking types: no "This
    # change was not saved", no red line, the source as it was.
    said = _warnings(monkeypatch)
    store = _Store(XTREAM)
    page = _page(store, "xt1")
    for kind in ("tvheadend", "jellyfin", "stalker", "enigma2"):
        _pick(page.kind, kind)
        assert page.commit() is False
        assert page.problem_label.isHidden(), kind
    page.go_back()
    page.leave()
    assert said == []
    assert store.updates == []
    assert store.get("xt1") == XTREAM


def test_a_type_begun_and_left_unfinished_is_still_said(app, monkeypatch):
    said = _warnings(monkeypatch)
    store = _Store(XTREAM)
    page = _page(store, "xt1")
    _pick(page.kind, "stalker")
    page.server.setText("http://portal.invalid/c/")      # no MAC address yet
    assert page.commit() is False
    assert not page.problem_label.isHidden()
    page.leave()
    assert len(said) == 1 and "not saved" in said[0][1]
    assert store.get("xt1")["kind"] == "xtream"


def test_a_kind_filled_in_is_stored_and_the_old_one_comes_back(app):
    store = _Store(XTREAM)
    page = _page(store, "xt1")
    _pick(page.kind, "m3u")
    page.server.setText("http://lists.invalid/mine.m3u")
    assert page.commit() is True
    stored = store.get("xt1")
    assert (stored["kind"], stored["server"], stored["username"], stored["password"]) == (
        "m3u", "http://lists.invalid/mine.m3u", "", "")
    # Back to Xtream: its own login is still there, and stored again.
    _pick(page.kind, "xtream")
    assert page.commit() is True
    assert store.get("xt1")["kind"] == "xtream"
    assert _login(page) == ("http://xt.invalid:8080", "xtuser", "xtpw")
    _pick(page.kind, "m3u")
    assert page.server.text() == "http://lists.invalid/mine.m3u"
    page.leave()


def test_a_media_server_keeps_its_sign_in_across_a_kind_tried_and_dropped(app):
    store = _Store(XTREAM, JELLY)
    page = _page(store, "jf1")
    assert page.signin.signed_in()
    _pick(page.kind, "xtream")
    assert _login(page) == ("", "", "")
    assert page.commit() is False
    _pick(page.kind, "jellyfin")
    assert _login(page) == ("http://jelly.invalid:8096", "anna", "")
    assert page.signin.signed_in()
    assert page.commit() is True
    page.leave()
    assert store.updates == [] and store.get("jf1") == JELLY


def test_a_box_found_on_the_network_gets_no_login_from_another_kind(app):
    store = _Store(XTREAM)
    page = _page(store, "xt1")
    page._box_found({"kind": "enigma2", "server": "http://192.168.1.20", "name": "Vu+ Duo"})
    assert page.kind.currentData() == "enigma2"
    assert _login(page) == ("http://192.168.1.20", "", "")
    page.leave()


def test_the_add_form_keeps_what_each_kind_was_given(app):
    dlg = PlaylistDialog()
    dlg.server.setText("http://xt.invalid:8080")
    dlg.user.setText("xtuser")
    dlg.pw.setText("xtpw")
    _pick(dlg.kind, "m3u")
    assert _login(dlg) == ("", "", "")
    dlg.server.setText("http://lists.invalid/mine.m3u")
    _pick(dlg.kind, "xtream")
    assert _login(dlg) == ("http://xt.invalid:8080", "xtuser", "xtpw")
    _pick(dlg.kind, "m3u")
    assert dlg.server.text() == "http://lists.invalid/mine.m3u"
    # A provider link pasted where an M3U address goes: Xtream, its parts.
    dlg.server.setText("http://other.invalid:8000/get.php?username=bob&password=s3&type=m3u_plus")
    dlg._maybe_autodetect_link()
    assert dlg.kind.currentData() == "xtream"
    assert _login(dlg) == ("http://other.invalid:8000", "bob", "s3")


def test_the_welcome_keeps_an_xtream_login_to_xtream(app, tmp_path):
    s = QSettings(str(tmp_path / "w.ini"), QSettings.Format.IniFormat)
    for k, v in (("server", "http://xt.invalid:8080"), ("username", "xtuser"), ("password", "xtpw")):
        s.setValue(k, v)
    parent = QWidget()
    w = WelcomeOverlay(parent, settings=s, on_connect=lambda *a: None, on_explore=lambda: None,
                       on_language_change=lambda c: None, on_demo=lambda: None)
    assert (w._server.text(), w._user.text()) == ("http://xt.invalid:8080", "xtuser")
    w._set_kind("jellyfin")             # the media server card's kind
    assert w._conn_kind.currentData() == "jellyfin"
    assert (w._server.text(), w._user.text(), w._pw.text()) == ("", "", "")
    w._set_kind("xtream")
    assert (w._server.text(), w._user.text(), w._pw.text()) == (
        "http://xt.invalid:8080", "xtuser", "xtpw")
