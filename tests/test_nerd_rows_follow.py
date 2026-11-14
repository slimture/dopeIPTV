"""Nerd's pages: a row shown only for one choice follows that choice.

Four rows hid and showed by a lambda that read the page's card variable
when it ran - by then the last card built, not their own - so choosing in
the combo or switch did nothing to them: the custom user agent, the proxy
for the player, the VPN interface and home-network rows, and the highest
bitrate under the HLS quality."""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication, QComboBox, QDialog, QLineEdit, QSpinBox  # noqa: E402

from dopeiptv.core import nerd  # noqa: E402
from dopeiptv.i18n import tr  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def page(app, tmp_path, monkeypatch):
    from dopeiptv.ui import nerd_settings
    from tests.test_nerd_ui import _shell
    monkeypatch.setattr(nerd_settings, "throwaway_mpv", lambda: None)
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s.setValue("nerd_enabled", "true")
    shell, pg = _shell()
    nerd_settings.build(pg, s, QDialog())
    shell.show()
    app.processEvents()
    yield pg
    shell.close()
    nerd.install(None)


def _hidden(w, page) -> bool:
    while w is not None and w is not page:
        if w.isHidden():
            return True
        w = w.parentWidget()
    return False


def test_the_highest_bitrate_follows_the_quality(app, page):
    q = next(c for c in page.findChildren(QComboBox) if c.findData("lowest") >= 0)
    rate = next(sp for sp in page.findChildren(QSpinBox) if sp.suffix() == " kbit/s")
    assert not _hidden(rate, page)
    q.setCurrentIndex(q.findData("lowest"))
    app.processEvents()
    assert _hidden(rate, page)
    q.setCurrentIndex(q.findData("720"))
    app.processEvents()
    assert not _hidden(rate, page)


def test_the_custom_user_agent_follows_its_choice(app, page):
    ua = next(c for c in page.findChildren(QComboBox) if c.findData("custom") >= 0)
    custom = next(e for e in page.findChildren(QLineEdit) if e.placeholderText() == "MyPlayer/1.0")
    assert _hidden(custom, page)
    ua.setCurrentIndex(ua.findData("custom"))
    app.processEvents()
    assert not _hidden(custom, page)
    ua.setCurrentIndex(0)
    app.processEvents()
    assert _hidden(custom, page)


def test_the_vpn_rows_follow_the_guard(app, page):
    from dopeiptv.ui.settings_ui import ToggleSwitch
    iface = next(e for e in page.findChildren(QLineEdit)
                 if e.placeholderText() == tr("nerd_vpn_iface_auto"))
    guard = next(t for t in page.findChildren(ToggleSwitch)
                 if t.accessibleName() == tr("nerd_vpn_guard"))
    assert _hidden(iface, page)
    guard.setChecked(True)
    app.processEvents()
    assert not _hidden(iface, page)
    guard.setChecked(False)
    app.processEvents()
    assert _hidden(iface, page)


def test_the_proxy_for_the_player_follows_the_proxy(app, page):
    from dopeiptv.ui.settings_ui import ToggleSwitch
    proxy_player = next(t for t in page.findChildren(ToggleSwitch)
                        if t.accessibleName() == tr("nerd_proxy_player"))
    proxy = next(e for e in page.findChildren(QLineEdit)
                 if e.placeholderText() == "http://192.168.1.1:3128")
    assert _hidden(proxy_player, page)
    proxy.setText("http://127.0.0.1:3128")
    app.processEvents()
    assert not _hidden(proxy_player, page)
    proxy.setText("")
    app.processEvents()
    assert _hidden(proxy_player, page)
