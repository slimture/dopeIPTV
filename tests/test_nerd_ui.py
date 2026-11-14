"""Nerd in the interface: a page that is not there until chosen, controls
that store what they say, a source's own options stored only where they
differ, the welcome wizard's quiet offer, diagnostics without secrets."""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtGui import QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog  # noqa: E402

from dopeiptv.core import nerd  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    yield s
    nerd.install(None)


def _shell():
    from dopeiptv.ui.settings_ui import SettingsPage, SettingsShell, page_icon
    shell = SettingsShell("Search", "none")
    blank = lambda k, sz, c: QPixmap(sz, sz)  # noqa: E731
    general = shell.add_page(SettingsPage("general", "General", ""), page_icon("gear", blank))
    general.card("x").row("Language")
    page = shell.add_page(SettingsPage("nerd", "Nerd", ""), page_icon("nerd", blank))
    return shell, page


def _shown(shell, key):
    return not shell._items[key].isHidden()


def test_the_nerd_page_is_not_there_until_chosen(app, settings):
    from dopeiptv.ui import nerd_settings
    shell, page = _shell()
    nerd_settings.build(page, settings, QDialog())
    shell.arrange([("System", ["general", "nerd"])])
    shell.set_page_enabled("nerd", False)
    assert not _shown(shell, "nerd") and _shown(shell, "general")
    # Not even a search finds it while it is off.
    shell.search.setText("proxy")
    assert not _shown(shell, "nerd")
    shell.search.setText("")
    shell.set_page_enabled("nerd", True)
    assert _shown(shell, "nerd")
    shell.search.setText("proxy")
    assert _shown(shell, "nerd") and not _shown(shell, "general")


def test_the_page_stores_what_its_controls_say(app, settings):
    from dopeiptv.ui import nerd_settings
    from dopeiptv.ui.settings_ui import Chip
    shell, page = _shell()
    d = QDialog()
    commit = nerd_settings.build(page, settings, d)
    from PyQt6.QtWidgets import QComboBox, QLineEdit, QPlainTextEdit
    combos = page.findChildren(QComboBox)
    ua = next(c for c in combos if c.findData("tivimate") >= 0)
    ua.setCurrentIndex(ua.findData("tivimate"))
    for chip in page.findChildren(Chip):
        chip.setChecked(chip.text() in ("AC3", "TrueHD"))
    proxy = next(e for e in page.findChildren(QLineEdit) if e.placeholderText().startswith("http://192.168.1.1"))
    proxy.setText("http://10.0.0.1:3128")
    raw = next(t for t in page.findChildren(QPlainTextEdit) if "deband" in t.placeholderText())
    raw.setPlainText("deband=yes")
    stored = {}
    commit(lambda k, v: stored.__setitem__(k, v))
    assert stored["nerd_user_agent"] == "tivimate"
    assert stored["nerd_spdif"] == "ac3,truehd"
    assert stored["nerd_proxy"] == "http://10.0.0.1:3128"
    assert stored["nerd_mpv_options"] == "deband=yes"
    # What was left as shown is not written (an untouched page writes nothing).
    assert "nerd_net_timeout" not in stored and "nerd_ip_family" not in stored


def test_a_custom_user_agent_is_kept_as_typed(app, settings):
    from dopeiptv.ui import nerd_settings
    settings.setValue("nerd_user_agent", "MyBox/2.0")
    shell, page = _shell()
    commit = nerd_settings.build(page, settings, QDialog())
    stored = {}
    commit(lambda k, v: stored.__setitem__(k, v))
    assert stored["nerd_user_agent"] == "MyBox/2.0"


def test_an_untouched_page_writes_nothing(app, settings):
    from dopeiptv.ui import nerd_settings
    shell, page = _shell()
    commit = nerd_settings.build(page, settings, QDialog())
    stored = {}
    commit(lambda k, v: stored.__setitem__(k, v))
    assert stored == {}


def test_a_sources_own_options_only_where_they_differ(app):
    from dopeiptv.ui.source_nerd import SourceNerdPanel
    p = SourceNerdPanel("enigma2", {"stream_port": "17999", "keep_me": "x"})
    assert p.port.value() == 17999
    assert p.values() == {"stream_port": "17999", "keep_me": "x"}
    p.switches["picons"].setChecked(False)
    p.ua.setCurrentIndex(p.ua.findData("kodi"))
    p.proxy.setCurrentIndex(p.proxy.findData("direct"))
    assert p.values() == {"stream_port": "17999", "keep_me": "x", "picons": False,
                          "user_agent": "kodi", "proxy": "direct"}
    p.port.setValue(8001)
    assert "stream_port" not in p.values()
    # Another kind: its own rows, the box's left behind.
    p.set_kind("hdhomerun")
    p.transcode.setCurrentIndex(p.transcode.findData("mobile"))
    v = p.values()
    assert v["transcode"] == "mobile" and "picons" not in v and "stream_port" not in v
    p.set_kind("tvheadend")
    p.profile.setEditText("my-profile")
    assert p.values()["profile"] == "my-profile"


def test_the_source_dialog_has_nerd_options_only_with_nerd_on(app, settings):
    from dopeiptv.ui.dialogs import PlaylistDialog
    nerd.install(settings)
    assert PlaylistDialog().nerd_panel is None
    settings.setValue("nerd_enabled", "true")
    nerd.install(settings)
    d = PlaylistDialog(playlist={"name": "Vu", "kind": "enigma2", "server": "http://10.0.0.2",
                                 "options": {"stream_port": "17999"}})
    assert d.nerd_panel is not None and d.nerd_panel.port.value() == 17999
    assert d.values()["options"] == {"stream_port": "17999"}
    d.nerd_panel.port.setValue(8001)
    assert "options" not in d.values()


def _welcome(settings):
    from PyQt6.QtWidgets import QMainWindow

    from dopeiptv.ui.welcome import WelcomeOverlay
    got = {}
    win = QMainWindow()
    got["_win"] = win          # kept: Qt deletes the overlay with its window
    ov = WelcomeOverlay(win, settings=settings,
                        on_connect=lambda *a: got.update(args=a), on_explore=lambda: None,
                        on_connect_trakt=lambda: None, on_language_change=lambda c: None,
                        on_demo=lambda: None)
    return ov, got


def _kinds(ov):
    return [ov._conn_kind.itemData(i) for i in range(ov._conn_kind.count())]


def test_welcome_offers_nerd_quietly_and_boxes_only_then(app, settings):
    ov, got = _welcome(settings)
    assert "enigma2" not in _kinds(ov)
    assert ov._w_nerd.text() == "🤓 Nerd? Show advanced options"
    ov._toggle_nerd()
    assert settings.value("nerd_enabled") == "true" and nerd.current().on
    assert {"enigma2", "tvheadend", "hdhomerun"} <= set(_kinds(ov))
    assert ov._w_nerd.text().startswith("🤓 Nerd on")
    ov._stack.setCurrentIndex(1)
    ov._conn_kind.setCurrentIndex(ov._conn_kind.findData("enigma2"))
    assert not ov._finder.row.isHidden()
    ov._server.setText("http://192.168.1.10")
    ov._do_connect()
    assert got["args"][:4] == ("http://192.168.1.10", "", "", "enigma2")
    # Off again: the box kinds go, and the form is back on a normal kind.
    ov._toggle_nerd()
    assert "enigma2" not in _kinds(ov) and ov._conn_kind.currentData() == "xtream"
    assert settings.value("nerd_enabled") == "false"


def test_diagnostics_never_carry_a_secret(app, settings):
    from dopeiptv.ui.nerd_settings import diagnostics
    settings.setValue("nerd_enabled", "true")
    settings.setValue("nerd_proxy", "http://user:hunter2@10.0.0.1:3128")
    settings.setValue("nerd_headers", "X-Token: s3cr3t-token")
    settings.setValue("nerd_spdif", "ac3")
    text = diagnostics(settings)
    assert "hunter2" not in text and "s3cr3t" not in text and "10.0.0.1" not in text
    assert "nerd_proxy = (set)" in text and "nerd_spdif = ac3" in text and "Nerd: on" in text
