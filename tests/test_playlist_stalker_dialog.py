"""Adding a Stalker / Ministra portal: the portal's URL and the box's MAC
address, which is written the way portals expect it."""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from dopeiptv.ui.dialogs import PlaylistDialog  # noqa: E402


def test_a_mac_address_is_written_as_portals_expect_it():
    n = PlaylistDialog.normal_mac
    assert n("00:1a:79:12:34:56") == "00:1A:79:12:34:56"
    assert n("00-1A-79-12-34-56") == "00:1A:79:12:34:56"
    assert n("001a79123456") == "00:1A:79:12:34:56"
    assert n(" 00 1a 79 12 34 56 ") == "00:1A:79:12:34:56"
    assert n("00:1A:79:12:34") is None
    assert n("00:1A:79:12:34:5G") is None
    assert n("") is None


def test_a_portal_asks_for_its_url_and_a_mac_not_a_login(monkeypatch):
    from dopeiptv.ui import dialogs
    app = QApplication.instance() or QApplication([])  # noqa: F841
    monkeypatch.setattr(dialogs, "stalker_available", lambda: True)
    d = PlaylistDialog()
    d.kind.setCurrentIndex(d.kind.findData("stalker"))
    assert d.user.isHidden() and d.pw.isHidden()
    assert not d.mac.isHidden()
    d.server.setText("http://portal.invalid/c/")
    d.mac.setText("00-1a-79-12-34-56")
    v = d.values()
    assert v["kind"] == "stalker" and v["mac"] == "00:1A:79:12:34:56"
    assert v["server"] == "http://portal.invalid/c/"
    d.kind.setCurrentIndex(d.kind.findData("xtream"))
    assert not d.user.isHidden() and d.mac.isHidden()


def test_no_portal_choice_without_the_portal_client(monkeypatch):
    from dopeiptv.ui import dialogs
    app = QApplication.instance() or QApplication([])  # noqa: F841
    monkeypatch.setattr(dialogs, "stalker_available", lambda: False)
    assert PlaylistDialog().kind.findData("stalker") < 0
