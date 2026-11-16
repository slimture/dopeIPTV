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


def test_the_home_servers_ask_for_what_they_need(app):
    from dopeiptv.i18n import tr
    d = PlaylistDialog()
    for kind, port in (("vdr", 8002), ("dvbviewer", 8089), ("nextpvr", 8866), ("mythtv", 6544)):
        assert d.kind.findData(kind) >= 0, kind
        d.kind.setCurrentIndex(d.kind.findData(kind))
        # The address shows the port the kind listens on.
        assert d.server.placeholderText().endswith(f":{port}"), kind
        assert _shown(d.finder.row) and _shown(d.pw) and not _shown(d.mac)
    d.kind.setCurrentIndex(d.kind.findData("dvbviewer"))
    assert _shown(d.user) and d._pw_lbl.text() == tr("login_password")
    assert d.user.placeholderText() == tr("playlist_login_optional")
    # NextPVR logs in with a PIN alone.
    d.kind.setCurrentIndex(d.kind.findData("nextpvr"))
    assert not _shown(d.user) and _shown(d.pw) and d._pw_lbl.text() == tr("login_pin")
    assert d.pw.placeholderText() == tr("playlist_pin_hint")
    d.user.setText("left over")
    d.pw.setText("1234")
    d.server.setText("10.0.0.5")
    v = d.values()
    assert (v["kind"], v["username"], v["password"]) == ("nextpvr", "", "1234")
    d.kind.setCurrentIndex(d.kind.findData("mythtv"))
    assert _shown(d.user) and d._pw_lbl.text() == tr("login_password")


def test_a_found_home_server_fills_the_form(app):
    d = PlaylistDialog()
    d._box_found({"kind": "vdr", "server": "http://192.168.1.50:8002", "name": "VDR 2.6.4 - 192.168.1.50"})
    assert d.kind.currentData() == "vdr" and d.server.text() == "http://192.168.1.50:8002"
    assert d.name.text() == "VDR 2.6.4" and d.problem() is None
    d = PlaylistDialog()
    d._box_found({"kind": "nextpvr", "server": "http://192.168.1.52:8866", "name": "NextPVR - 192.168.1.52",
                  "login": True})
    assert d.kind.currentData() == "nextpvr" and d.name.text() == "NextPVR"


def test_a_satip_server_asks_for_its_address_and_maybe_a_list(app):
    from dopeiptv.i18n import tr
    d = PlaylistDialog()
    d.kind.setCurrentIndex(d.kind.findData("satip"))
    assert _shown(d.finder.row) and not _shown(d.user) and not _shown(d.pw) and not _shown(d.mac)
    assert _shown(d.satip_m3u) and d._satip_lbl.text() == tr("satip_m3u")
    d.server.setText("10.0.0.9")
    assert d.problem() is None and "options" not in d.values()
    d.satip_m3u.setText("/home/me/Astra 19.2E.m3u")
    assert d.values()["options"] == {"m3u": "/home/me/Astra 19.2E.m3u"}
    # Found on the network: where its description is, kept for its address only.
    d._box_found({"kind": "satip", "server": "http://10.0.0.9", "name": "Octopus NET - 10.0.0.9",
                  "description": "http://10.0.0.9:8080/desc.xml"})
    assert d.name.text() == "Octopus NET"
    assert d.values()["options"]["description"] == "http://10.0.0.9:8080/desc.xml"
    d.server.setText("10.0.0.10")
    assert "description" not in d.values()["options"]
    d.kind.setCurrentIndex(d.kind.findData("enigma2"))
    assert not _shown(d.satip_m3u)


def test_the_fritzbox_cable_preset_is_a_satip_source(app):
    from dopeiptv.providers.satip import split_lists
    from dopeiptv.ui.source_presets import PRESETS, fill
    p = next(p for p in PRESETS if p.kind == "satip")
    src = fill(p, "")
    assert src["kind"] == "satip" and src["server"] == "http://fritz.box"
    assert split_lists(src["m3u"]) == [f"http://fritz.box/dvb/m3u/{n}.m3u" for n in ("tvhd", "tvsd", "radio")]
    d = PlaylistDialog()
    d._preset_picked(src)
    v = d.values()
    assert (v["kind"], v["server"], v["options"]["m3u"]) == ("satip", "http://fritz.box", src["m3u"])
