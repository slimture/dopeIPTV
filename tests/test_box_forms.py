"""The add-a-source forms with boxes on the home network: what each kind
asks for, a box found on the network filling the form, options kept."""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from dopeiptv.ui.box_finder import FindOnNetwork  # noqa: E402
from dopeiptv.ui.dialogs import PlaylistDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _shown(w) -> bool:
    return not w.isHidden()


def test_each_box_kind_asks_for_what_it_needs(app):
    d = PlaylistDialog()
    for kind in ("enigma2", "tvheadend", "hdhomerun"):
        assert d.kind.findData(kind) >= 0
    d.kind.setCurrentIndex(d.kind.findData("enigma2"))
    assert _shown(d.finder.row) and _shown(d.user) and not _shown(d.mac)
    assert d.server.placeholderText() == "http://192.168.1.10"
    assert d.user.placeholderText() == "Only if it asks for one"
    d.kind.setCurrentIndex(d.kind.findData("hdhomerun"))
    assert _shown(d.finder.row) and not _shown(d.user) and not _shown(d.pw)
    d.kind.setCurrentIndex(d.kind.findData("xtream"))
    assert not _shown(d.finder.row) and _shown(d.user)
    assert d.user.placeholderText() == ""


def test_a_found_box_fills_the_form(app):
    d = PlaylistDialog()
    d._box_found({"kind": "tvheadend", "server": "http://192.168.1.20:9981",
                  "name": "Tvheadend 4.3 - 192.168.1.20", "login": True})
    assert d.kind.currentData() == "tvheadend"
    assert d.server.text() == "http://192.168.1.20:9981"
    assert d.name.text() == "Tvheadend 4.3"
    v = d.values()
    assert v["kind"] == "tvheadend" and "options" not in v


def test_editing_a_box_keeps_its_options(app):
    pl = {"name": "Vu", "kind": "enigma2", "server": "http://10.0.0.2",
          "options": {"stream_port": "17999"}}
    d = PlaylistDialog(playlist=pl)
    assert d.kind.currentData() == "enigma2"
    assert d.values()["options"] == {"stream_port": "17999"}


def test_finder_offers_what_it_found(app):
    f = FindOnNetwork()
    got = []
    f.picked.connect(got.append)
    f._done([])
    assert not f.status.isHidden() and got == []
    one = {"kind": "enigma2", "server": "http://10.0.0.2", "name": "Vu+ Duo 4K - 10.0.0.2"}
    f._done([one])
    assert got == [one] and f.isEnabled()
