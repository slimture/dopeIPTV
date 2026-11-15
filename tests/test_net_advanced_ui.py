"""Nerd's advanced network options in the interface: own DNS and low
latency on the Nerd pages, a source's own DNS, IP version and trusted
certificate in its options - stored only where they differ - and the
window that shows a certificate before it is trusted."""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtGui import QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QLineEdit  # noqa: E402

from dopeiptv.core import cert_pin, nerd  # noqa: E402
from dopeiptv.i18n import tr  # noqa: E402

FP = "c0ffee" + "ab" * 29


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s.setValue("nerd_enabled", "true")
    nerd.install(s)
    yield s
    nerd.install(None)


def _pages(settings):
    from dopeiptv.ui import nerd_settings
    from dopeiptv.ui.settings_ui import SettingsPage
    blank = lambda k, sz, c: QPixmap(sz, sz)  # noqa: E731
    page = SettingsPage("nerd", "Nerd", "")
    pages = {}
    for key in ("net", "boxes", "lists", "player", "rec", "serve"):
        sp = SettingsPage(f"nerd_{key}", key.title(), "")
        sp.icon_kind, sp.icon_base = "nerd", blank
        pages[key] = sp
    commit = nerd_settings.build(page, settings, QDialog(), pages=pages, open_page=lambda k: None)
    return commit, pages


def test_own_dns_and_low_latency_are_stored_as_the_page_says(app, settings):
    from PyQt6.QtWidgets import QComboBox

    from dopeiptv.ui.settings_ui import ToggleSwitch
    commit, pages = _pages(settings)
    stored: dict = {}
    commit(lambda k, v: stored.__setitem__(k, v))
    assert not any(k.startswith(("nerd_dns", "nerd_low")) for k in stored)     # untouched: nothing
    box = next(c for c in pages["net"].findChildren(QComboBox) if c.findData("quad9") >= 0)
    url = next(e for e in pages["net"].findChildren(QLineEdit) if e.accessibleName() == tr("nerd_dns_url"))
    pages["net"].show()
    assert not url.isVisibleTo(pages["net"])            # only for a custom address
    box.setCurrentIndex(box.findData("custom"))
    assert url.isVisibleTo(pages["net"])
    url.setText("https://dns.example/dns-query")
    fallback = next(s for s in pages["net"].findChildren(ToggleSwitch) if s.accessibleName() == tr("nerd_dns_fallback"))
    fallback.setChecked(True)
    latency = next(s for s in pages["player"].findChildren(ToggleSwitch)
                   if s.accessibleName() == tr("nerd_low_latency"))
    latency.setChecked(True)
    commit(lambda k, v: stored.__setitem__(k, v))
    assert stored["nerd_dns"] == "custom" and stored["nerd_dns_url"] == "https://dns.example/dns-query"
    assert stored["nerd_dns_fallback"] == "true" and stored["nerd_low_latency"] == "true"
    pages["net"].hide()


def test_a_dns_address_that_is_none_is_said_at_once(app, settings):
    from PyQt6.QtWidgets import QComboBox

    from dopeiptv.ui.nerd_check_line import CheckLine
    _commit, pages = _pages(settings)
    box = next(c for c in pages["net"].findChildren(QComboBox) if c.findData("quad9") >= 0)
    url = next(e for e in pages["net"].findChildren(QLineEdit) if e.accessibleName() == tr("nerd_dns_url"))
    line = next(ln for ln in pages["net"].findChildren(CheckLine) if ln._title == tr("nerd_ck_title_dns"))
    assert line.result.key == "nerd_ck_dns_off"
    box.setCurrentIndex(box.findData("custom"))
    url.setText("dns.example")
    line.show_preview()
    assert line.result.state == "fail" and line.result.key == "nerd_ck_dns_bad_url"
    box.setCurrentIndex(box.findData("cloudflare"))
    line.show_preview()
    assert line.result is None                          # a Test to press


def _panel(kind="xtream", options=None, server="https://nas.example:8443"):
    from dopeiptv.ui.source_nerd import SourceNerdPanel
    form = {"kind": kind, "server": server}
    return SourceNerdPanel(kind, options or {}, source=lambda: dict(form)), form


def test_a_sources_network_only_where_it_differs(app, settings):
    p, _form = _panel(options={"keep_me": "x", "dns": "google", "ip_family": "ipv6",
                               **cert_pin.pin_options("nas.example", 8443, FP)})
    net = p.net
    assert net.dns.currentData() == "google" and net.family.currentData() == "ipv6"
    assert p.values() == {"keep_me": "x", "dns": "google", "ip_family": "ipv6",
                          "cert_pin": FP, "cert_pin_for": "nas.example:8443"}
    net.dns.setCurrentIndex(net.dns.findData(""))
    net.family.setCurrentIndex(net.family.findData("prefer6"))
    net.forget()
    assert p.values() == {"keep_me": "x", "ip_family": "prefer6"}
    net.dns.setCurrentIndex(net.dns.findData("custom"))
    net.dns_url.setText("https://dns.example/q")
    assert p.values()["dns"] == "custom" and p.values()["dns_url"] == "https://dns.example/q"
    net.dns.setCurrentIndex(net.dns.findData("off"))
    assert p.values()["dns"] == "off" and "dns_url" not in p.values()


def test_trusting_is_offered_only_for_an_https_address(app, settings):
    p, form = _panel(server="http://10.0.0.2:9981")
    p.toggle.setChecked(True)
    assert not p.net.trust_btn.isEnabled() and p.net.pin_label.text() == tr("cert_plain")
    p.net.cert_line.show_preview()
    assert p.net.cert_line.result.key == "nerd_ck_pin_plain"
    form["server"] = "https://nas.example:8443"
    p.source_changed()
    assert p.net.trust_btn.isEnabled() and p.net.pin_label.text() == tr("cert_none")


def test_a_pin_for_another_address_is_pointed_out(app, settings):
    p, _form = _panel(options=cert_pin.pin_options("old.example", 443, FP))
    p.net.cert_line.show_preview()
    assert p.net.cert_line.result.key == "nerd_ck_pin_other"
    assert "old.example:443" in p.net.pin_label.text()


def test_trusting_a_certificate_pins_exactly_it(app, settings, monkeypatch):
    from dopeiptv.ui import source_net
    p, _form = _panel()
    info = cert_pin.CertInfo(der=b"x", sha256=FP, subject={"CN": "nas.example"}, issuer={"CN": "nas.example"})
    shown = {}

    class Dlg:
        def __init__(self, parent, host, port, info, pinned=None):
            shown.update(host=host, port=port, pinned=pinned)

        def exec(self):
            return True
    monkeypatch.setattr(source_net, "CertTrustDialog", Dlg)
    p.net._cert_came("nas.example", 8443, info)
    assert shown == {"host": "nas.example", "port": 8443, "pinned": None}
    assert cert_pin.pin_of(p.values()) == ("nas.example", 8443, FP)
    assert p.net.forget_btn.isVisibleTo(p.net) or not p.net.isVisible()


def test_the_trust_window_shows_the_certificate(app):
    from datetime import datetime, timezone

    from dopeiptv.ui import cert_trust
    from dopeiptv.ui.cert_trust import CertTrustDialog
    info = cert_pin.CertInfo(der=b"x", sha256=FP, subject={"CN": "nas.example", "O": "Home"},
                             issuer={"CN": "nas.example", "O": "Home"},
                             not_before=datetime(2026, 1, 1, tzinfo=timezone.utc),
                             not_after=datetime(2027, 1, 1, tzinfo=timezone.utc), names=["nas.example"])
    d = CertTrustDialog(None, "nas.example", 8443, info)
    assert d.subject.text() == "CN=nas.example, O=Home"
    assert d.fingerprint.text().replace("\n", ":") == cert_pin.pretty(FP)
    assert d.pin() == cert_pin.pin_options("nas.example", 8443, FP)
    changed = CertTrustDialog(None, "nas.example", 8443, info, pinned="00" * 32)
    texts = [w.text() for w in changed.findChildren(type(changed.subject))]
    assert tr("cert_changed_head") in texts and cert_trust.two_lines("00" * 32) in texts


def test_a_changed_certificate_is_offered_for_review_only_when_met(app, monkeypatch):
    from dopeiptv.ui import cert_trust
    asked = []
    monkeypatch.setattr(cert_trust._Review, "ask", lambda self, h, p: asked.append((h, p)))
    pl = {"id": "s1", "name": "NAS", "kind": "tvheadend", "options": cert_pin.pin_options("nas.example", 9982, FP)}
    win = QDialog()
    cert_pin.clear_mismatch("nas.example", 9982)
    assert cert_trust.offer_review(win, None, pl) is False
    assert cert_trust.offer_review(win, None, {"id": "s2", "options": {}}) is False
    cert_pin.record_mismatch("nas.example", 9982, "11" * 32)
    assert cert_trust.offer_review(win, None, pl) is True and asked == [("nas.example", 9982)]
    cert_pin.clear_mismatch("nas.example", 9982)


def test_review_trusts_the_new_one_and_connects_again(app, monkeypatch):
    from dopeiptv.ui import cert_trust

    class Store:
        def __init__(self):
            self.updated = {}

        def update(self, pid, **fields):
            self.updated[pid] = fields

    class Dlg:
        def __init__(self, *a, **kw):
            self.host, self.port, self.info = a[1], a[2], a[3]

        def exec(self):
            return True

        def pin(self):
            return cert_pin.pin_options(self.host, self.port, self.info.sha256)
    monkeypatch.setattr(cert_trust, "CertTrustDialog", Dlg)
    store, retried = Store(), []
    pl = {"id": "s1", "name": "NAS", "kind": "tvheadend",
          "options": {"profile": "pass", **cert_pin.pin_options("nas.example", 9982, FP)}}
    win = QDialog()
    cert_pin.record_mismatch("nas.example", 9982, "22" * 32)
    review = cert_trust._Review(win, store, pl, retried.append)
    review._show("nas.example", 9982, cert_pin.CertInfo(der=b"y", sha256="22" * 32))
    assert store.updated["s1"]["options"] == {"profile": "pass",
                                              **cert_pin.pin_options("nas.example", 9982, "22" * 32)}
    assert retried == ["s1"] and cert_pin.mismatch("nas.example", 9982) is None
