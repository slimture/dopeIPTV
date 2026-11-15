"""Nerd's advanced network options on its pages: own DNS (DNS over HTTPS,
core/doh.py and core/net_route.py) and the other networks to search (a home
network split in VLANs, core/home_net.py) on the Network page, low latency
for live (core/live_latency.py) on the Player page - each with its line
saying whether it works. A source's own choice of DNS, IP version and trusted
certificate is in its own options (ui/source_net.py).

Each function adds its card and returns what the card holds, as the
settings it is stored as (ui/nerd_settings.py writes them with the rest).
"""

from __future__ import annotations

from typing import Any, Callable

from PyQt6.QtWidgets import QLineEdit

from ..core import doh, home_net, live_latency, nerd_check, net_route
from ..i18n import tr
from .nerd_check_line import CheckLine
from .settings_ui import SettingsPage

#: (value, label) of the own DNS choices on the Nerd page.
DNS_CHOICES = [("", "nerd_dns_off"), ("cloudflare", "Cloudflare"), ("quad9", "Quad9"),
               ("google", "Google"), ("custom", "nerd_dns_custom")]
KEYS = ("nerd_dns", "nerd_dns_url", "nerd_dns_fallback", live_latency.KEY, home_net.KEY)
EXAMPLE_HOST = "example.com"


def probe_host(source: dict | None) -> str:
    """The host a DNS check asks: the active source's, else an example."""
    url = nerd_check.probe_url(source)
    where = net_route.url_host(url) if url else None
    return where[1] if where else EXAMPLE_HOST


def dns_card(page: SettingsPage, n: Any, lines: list[CheckLine],
             source: Callable[[], dict | None], proxy: Callable[[], str]) -> Callable[[], dict[str, str]]:
    """Own DNS for every source (a source's own options can say otherwise).
    *source*: the active source (the check asks its name); *proxy*: the
    Nerd page's proxy as typed (with one, the proxy looks names up)."""
    from .nerd_settings import combo
    c = page.card(tr("nerd_card_dns"))
    c.note(tr("nerd_dns_intro"))
    box = combo(DNS_CHOICES, n.get("nerd_dns"))
    url = QLineEdit(n.get("nerd_dns_url"))
    url.setPlaceholderText("https://dns.example/dns-query")
    url.setAccessibleName(tr("nerd_dns_url"))

    def servers() -> tuple[str, ...]:
        return doh.servers_for(str(box.currentData() or ""), url.text())

    def preview() -> nerd_check.Check | None:
        if not box.currentData():
            return nerd_check.Check(nerd_check.INFO, "nerd_ck_dns_off")
        if box.currentData() == "custom" and not servers():
            return nerd_check.Check(nerd_check.FAIL, "nerd_ck_dns_bad_url")
        return None

    def job() -> Any:
        found = servers()
        if not found:
            return None
        name = doh.label(str(box.currentData()), url.text())
        host, proxied = probe_host(source()), bool(proxy().strip())
        return lambda: net_route.check_dns(found, name, host, proxied)
    line = CheckLine(job, tr("nerd_ck_title_dns"), test=True, preview=preview)
    lines.append(line)
    c.row(tr("nerd_dns"), box, tr("nerd_dns_hint"), search="DNS over HTTPS DoH resolver privacy Cloudflare Quad9 Google",
          below=line)
    url_row = c.row(tr("nerd_dns_url"), url, tr("nerd_dns_url_hint"), stretch_control=True,
                    search="DoH DNS address URL server")
    c.shown_if(url_row, lambda: box.currentData() == "custom")
    fallback = c.toggle(tr("nerd_dns_fallback"), n.flag("nerd_dns_fallback"), tr("nerd_dns_fallback_hint"))
    c.shown_if(c.rows[-1][0], lambda: bool(box.currentData()))
    box.currentIndexChanged.connect(lambda _i, c=c: c.refresh())
    box.currentIndexChanged.connect(line.changed)
    url.textChanged.connect(line.changed)
    c.note(tr("nerd_dns_where"))
    c.note(tr("nerd_applies_dns"))

    def values() -> dict[str, str]:
        return {"nerd_dns": str(box.currentData() or ""), "nerd_dns_url": url.text().strip(),
                "nerd_dns_fallback": "true" if fallback.isChecked() else "false"}
    return values


def latency_card(page: SettingsPage, n: Any, lines: list[CheckLine],
                 make_mpv: Callable[[], Any] | None) -> Callable[[], dict[str, str]]:
    """The low-latency switch for live channels, and whether this player
    takes its options (each set on a throwaway core)."""
    from .settings_ui import ToggleSwitch
    c = page.card(tr("nerd_card_latency"))
    switch = ToggleSwitch(n.flag(live_latency.KEY))
    switch.setAccessibleName(tr("nerd_low_latency"))

    def job() -> Any:
        return (lambda: live_latency.check(make_mpv)) if switch.isChecked() else None
    line = CheckLine(job, tr("nerd_ck_title_latency"))
    lines.append(line)
    switch.toggled.connect(line.changed)
    c.row(tr("nerd_low_latency"), switch, tr("nerd_low_latency_hint"),
          search="low latency live delay buffer live edge fast start HLS", below=line)
    c.note(tr("nerd_low_latency_not"))
    c.note(tr("nerd_applies_stream"))

    def values() -> dict[str, str]:
        return {live_latency.KEY: "true" if switch.isChecked() else "false"}
    return values


def networks_card(page: SettingsPage, n: Any, lines: list[CheckLine]) -> Callable[[], dict[str, str]]:
    """The other networks to search: a home network split in VLANs, where a
    search sent to everyone stays on its own part. Its line says what the
    list comes to (a bad line and why) as it is typed; Test searches it."""
    from .nerd_settings import text_box
    c = page.card(tr("nerd_card_lan"))
    c.note(tr("nerd_lan_intro"))
    box = text_box(n.get(home_net.KEY), "192.168.20.0/24\n192.168.30.10-192.168.30.60\n10.0.40.5", 3)
    box.setAccessibleName(tr("nerd_lan_nets"))

    def preview() -> nerd_check.Check | None:
        return home_net.check_list(box.toPlainText())

    def job() -> Any:
        text = box.toPlainText()
        if not home_net.addresses(text):
            return None
        from ..providers import lan_scan
        return lambda: lan_scan.check_networks(text)
    line = CheckLine(job, tr("nerd_ck_title_lan"), test=True, preview=preview,
                     test_text=tr("nerd_lan_search"))
    lines.append(line)
    box.textChanged.connect(line.changed)
    c.row(tr("nerd_lan_nets"), box, tr("nerd_lan_nets_hint", prefix=home_net.MAX_PREFIX,
                                         max=home_net.MAX_TOTAL),
          stretch_control=True, below=line,
          search="VLAN subnet other network IoT guest router mDNS Bonjour SSDP Chromecast DLNA Kodi")
    c.note(tr("nerd_lan_router"))
    c.note(tr("nerd_applies_lan"))

    def values() -> dict[str, str]:
        return {home_net.KEY: box.toPlainText().strip()}
    return values
