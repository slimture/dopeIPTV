"""A source's own network in its Nerd options (the Network tab of its page
in Settings, with ui/source_nerd.py's rows): its own DNS (DNS over HTTPS),
its own IP version, and the certificate trusted for it - each with a line
and a Test (core/net_route.py, core/cert_pin.py). Only what differs from
the Nerd page is stored, under KEYS in the source's options.
"""

from __future__ import annotations

from typing import Any, Callable

from PyQt6 import sip
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QWidget,
)

from ..core import cert_pin, doh, nerd, nerd_check, net_route
from ..i18n import tr
from .cert_trust import CertFetch, CertTrustDialog
from .nerd_check_line import CheckLine
from .theme import P
from .widgets import plain_text

KEYS = ("dns", "dns_url", "ip_family", cert_pin.KEY, cert_pin.KEY_FOR)
DNS_CHOICES = [("", "nerd_src_inherit"), ("off", "nerd_dns_off"), ("cloudflare", "Cloudflare"),
               ("quad9", "Quad9"), ("google", "Google"), ("custom", "nerd_dns_custom")]
FAMILY_CHOICES = [("", "nerd_src_inherit"), ("ipv4", "nerd_ip_v4"), ("ipv6", "nerd_ip_v6"),
                  ("prefer6", "nerd_ip_prefer6")]


def _combo(items: list[tuple[str, str]], current: str) -> QComboBox:
    box = QComboBox()
    for value, label in items:
        box.addItem(tr(label) if label.startswith("nerd_") else label, value)
    box.setCurrentIndex(max(0, box.findData(current)))
    return box


class SourceNetOptions(QWidget):
    """*source()*: the source as the page has it now (kind, address,
    login) - what the Tests ask. Holds the controls, never shown itself:
    the page places them."""

    #: A certificate was trusted or forgotten (no control says so).
    pinned = pyqtSignal()

    def __init__(self, options: dict, source: Callable[[], dict], parent: QWidget | None = None,
                 proxy: Callable[[], str] | None = None) -> None:
        super().__init__(parent)
        o = dict(options or {})
        self._source = source
        self._proxy = proxy
        self._pin = cert_pin.pin_of(o)
        self.hide()
        # Own DNS.
        self.dns = _combo(DNS_CHOICES, str(o.get("dns") or ""))
        self.dns.setToolTip(tr("nerd_dns_hint"))
        self.dns_url = QLineEdit(str(o.get("dns_url") or ""))
        self.dns_url.setPlaceholderText("https://dns.example/dns-query")
        self.dns_url.setAccessibleName(tr("nerd_dns_url"))
        self.dns_line = CheckLine(self._dns_job, tr("nerd_ck_title_dns"), test=True, preview=self._dns_preview)
        # IP version.
        self.family = _combo(FAMILY_CHOICES, str(o.get("ip_family") or ""))
        self.family.setToolTip(tr("nerd_src_ip_hint"))
        self.family_line = CheckLine(self._family_job, tr("nerd_ck_title_ip"), test=True,
                                     preview=self._family_preview)
        # The certificate trusted for it.
        self.trust_btn = QPushButton(tr("cert_trust_action"))
        self.trust_btn.setToolTip(tr("cert_trust_hint"))
        self.forget_btn = QPushButton(tr("cert_forget"))
        self.trust_btn.clicked.connect(self.trust)
        self.forget_btn.clicked.connect(self.forget)
        self.cert_buttons = buttons = QWidget()
        h = QHBoxLayout(buttons)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        h.addWidget(self.trust_btn)
        h.addWidget(self.forget_btn)
        h.addStretch(1)
        self.pin_label = QLabel("")
        self.pin_label.setWordWrap(True)
        self.pin_label.setStyleSheet(f"color:{P['text2']}; font-size:12px;")
        self.cert_line = CheckLine(self._cert_job, tr("nerd_ck_title_pin"), test=True, preview=self._cert_preview)
        self._fetch = CertFetch(self, self._cert_came, self._cert_failed)
        self.dns.currentIndexChanged.connect(self._shown)
        for sig in (self.dns.currentIndexChanged, self.dns_url.textChanged):
            sig.connect(self.dns_line.changed)
        self.family.currentIndexChanged.connect(self.family_line.changed)
        self._shown()

    # -- what it holds ---------------------------------------------------------

    def values(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        dns = str(self.dns.currentData() or "")
        if dns:
            out["dns"] = dns
        if dns == "custom" and self.dns_url.text().strip():
            out["dns_url"] = self.dns_url.text().strip()
        if self.family.currentData():
            out["ip_family"] = str(self.family.currentData())
        if self._pin is not None:
            out.update(cert_pin.pin_options(*self._pin))
        return out

    def route(self, with_pin: bool = True) -> net_route.Route:
        """The route these options make, under the Nerd page now."""
        opts = self.values()
        if not with_pin:
            opts = {k: v for k, v in opts.items() if k not in (cert_pin.KEY, cert_pin.KEY_FOR)}
        return net_route.route_for(opts)

    def _server(self) -> str:
        try:
            server = str((self._source() or {}).get("server") or "").strip()
        except Exception:
            server = ""
        return server if "://" in server or not server else "http://" + server

    def _https(self) -> tuple[str, int] | None:
        return cert_pin.host_port(self._server())

    def source_changed(self, *_a: Any) -> None:
        """The dialog's address changed: what the lines said is about the
        old one."""
        self._shown()
        for line in (self.dns_line, self.family_line, self.cert_line):
            line.changed()

    def _shown(self, *_a: Any) -> None:
        self.dns_url.setVisible(self.dns.currentData() == "custom")
        https = self._https()
        self.trust_btn.setEnabled(https is not None)
        self.forget_btn.setVisible(self._pin is not None)
        if self._pin is not None:
            host, port, fp = self._pin
            self.pin_label.setText(tr("cert_pinned", server=f"{host}:{port}", fp=cert_pin.pretty(fp)[:23] + "…"))
        else:
            self.pin_label.setText(tr("cert_none") if https else tr("cert_plain"))

    # -- own DNS ------------------------------------------------------------------

    def _dns_now(self) -> tuple[tuple[str, ...], str]:
        """The servers this source asks (its own choice, else the Nerd
        page's) and what they are called."""
        n = nerd.current()
        own = str(self.dns.currentData() or "")
        if own == "off":
            return (), ""
        choice, url = (own, self.dns_url.text()) if own else (n.get("nerd_dns"), n.get("nerd_dns_url"))
        return doh.servers_for(choice, url), doh.label(choice, url)

    def _dns_preview(self) -> nerd_check.Check | None:
        own = str(self.dns.currentData() or "")
        if own == "custom" and not doh.valid_url(self.dns_url.text()):
            return nerd_check.Check(nerd_check.FAIL, "nerd_ck_dns_bad_url")
        servers, name = self._dns_now()
        if not servers:
            return nerd_check.Check(nerd_check.INFO, "nerd_ck_dns_off")
        if not own:
            return nerd_check.Check(nerd_check.INFO, "nerd_ck_dns_inherit", {"name": name})
        return None

    def _dns_job(self) -> Callable[[], Any] | None:
        servers, name = self._dns_now()
        if not servers:
            return None
        where = net_route.url_host(self._server())
        host = where[1] if where else "example.com"
        proxied = bool(self._proxy() if self._proxy is not None else nerd.current().proxy({}))
        return lambda: net_route.check_dns(servers, name, host, proxied)

    # -- IP version -------------------------------------------------------------------

    def _family_preview(self) -> nerd_check.Check | None:
        if not self.family.currentData():
            return nerd_check.Check(nerd_check.INFO, "nerd_ck_ip_inherit")
        return None

    def _family_job(self) -> Callable[[], Any] | None:
        route, url = self.route(with_pin=False), self._server()
        if not route.family or not url:
            return None
        return lambda: net_route.check_family(route, url)

    # -- the certificate ----------------------------------------------------------------

    def _cert_preview(self) -> nerd_check.Check | None:
        https = self._https()
        if https is None:
            return nerd_check.Check(nerd_check.INFO, "nerd_ck_pin_plain")
        if self._pin is not None and (self._pin[0], self._pin[1]) != https:
            return nerd_check.Check(nerd_check.WARN, "nerd_ck_pin_other",
                                    {"server": f"{self._pin[0]}:{self._pin[1]}"})
        return None

    def _cert_job(self) -> Callable[[], Any] | None:
        url = self._server()
        if self._https() is None:
            return None
        route, pin = self.route(with_pin=False), self._pin
        return lambda: cert_pin.check(url, pin, route)

    def trust(self) -> None:
        """Read the certificate the server shows (off the window's thread)
        and show it before anything is trusted."""
        https = self._https()
        if https is None:
            return
        self.trust_btn.setEnabled(False)
        self.trust_btn.setText(tr("cert_reading"))
        self._fetch.start(https[0], https[1], self.route(with_pin=False))

    def _reset_button(self) -> None:
        self.trust_btn.setText(tr("cert_trust_action"))
        self.trust_btn.setEnabled(self._https() is not None)

    def _cert_came(self, host: str, port: int, info: Any) -> None:
        if sip.isdeleted(self):
            return
        self._reset_button()
        before = self._pin[2] if self._pin and (self._pin[0], self._pin[1]) == (host, port) else None
        dlg = CertTrustDialog(self.window(), host, port, info, before)
        if dlg.exec():
            self._pin = (host, port, info.sha256)
            cert_pin.clear_mismatch(host, port)
            self._shown()
            self.cert_line.changed()
            self.pinned.emit()

    def _cert_failed(self, message: str) -> None:
        if sip.isdeleted(self):
            return
        self._reset_button()
        QMessageBox.warning(self.window(), tr("nerd_ck_title_pin"),
                            plain_text(tr("cert_fetch_failed", error=message)))

    def forget(self) -> None:
        self._pin = None
        self._shown()
        self.cert_line.changed()
        self.pinned.emit()

    def has_pin(self) -> bool:
        return self._pin is not None
