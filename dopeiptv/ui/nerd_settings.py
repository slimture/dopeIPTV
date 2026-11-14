"""Nerd in Settings: its own page (the switch, how it works, stats,
diagnostics) and, while it is on, four more under NERD - the network, boxes
and servers, channels and guide, the player. Each setting that can be tried
has a line under it saying whether it works (ui/nerd_check_line.py,
core/nerd_check.py); each card says when its settings take effect.
core/nerd.py turns what is stored here into what the player and the network
do.

``build`` fills the page and returns ``commit(put)``, which open_settings
calls with its own put() like every other page's controls.
"""

from __future__ import annotations

import os
import platform
import sys
from typing import Any, Callable

from PyQt6 import sip
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from ..core import nerd, nerd_check
from ..core.log import log, log_dir
from ..i18n import tr
from ..media.players import throwaway_mpv
from .nerd_check_line import CheckLine
from .settings_ui import Chip, ChipGroups, SettingsPage, Stepper, ToggleSwitch

UA_CHOICES = [("", "nerd_ua_default"), ("vlc", "VLC"), ("kodi", "Kodi"), ("tivimate", "TiviMate"),
              ("smarters", "IPTV Smarters"), ("ffmpeg", "FFmpeg"), ("mag", "MAG box"),
              ("enigma2", "Enigma2"), ("chrome", "Chrome"), ("custom", "nerd_ua_custom")]
SPDIF_LABELS = [("ac3", "AC3"), ("eac3", "E-AC3"), ("dts", "DTS"), ("dts-hd", "DTS-HD"),
                ("truehd", "TrueHD")]
CACHE_CHOICES = [0, 64, 128, 256, 512, 1024, 2048]


def combo(items: list[tuple[str, str]], current: str) -> QComboBox:
    box = QComboBox()
    for value, label in items:
        box.addItem(tr(label) if label.startswith("nerd_") else label, value)
    i = box.findData(current)
    box.setCurrentIndex(i if i >= 0 else 0)
    box.setMaxVisibleItems(14)
    return box


def text_box(value: str, placeholder: str, lines: int = 4) -> QPlainTextEdit:
    """A few lines of typed text (headers, mpv options): monospaced, as a
    config file reads."""
    ed = QPlainTextEdit(value)
    ed.setPlaceholderText(placeholder)
    mono = QFont("monospace")
    mono.setStyleHint(QFont.StyleHint.TypeWriter)
    ed.setFont(mono)
    ed.setTabChangesFocus(True)
    ed.setFixedHeight(ed.fontMetrics().lineSpacing() * lines + 18)
    ed.setObjectName("NerdText")
    return ed


BOX_NAMES = {"enigma2": "Enigma2", "tvheadend": "Tvheadend", "hdhomerun": "HDHomeRun"}

#: Nerd's pages in the order its list shows them, and the settings on each
#: (what "2 changed" counts; boxes are counted as boxes).
PARTS = ("net", "boxes", "lists", "player", "rec", "serve")
PART_KEYS = {
    "net": ("nerd_user_agent", "nerd_referer", "nerd_headers", "nerd_proxy", "nerd_proxy_player",
            "nerd_ip_family", "nerd_net_timeout", "nerd_reconnect_max", "nerd_vpn_guard",
            "nerd_vpn_iface", "nerd_vpn_lan"),
    "lists": ("nerd_picon_dir", "nerd_picon_override", "nerd_udpxy", "nerd_failover", "nerd_health",
              "nerd_numbers_keep"),
    "player": ("nerd_spdif", "nerd_video_sync", "nerd_interpolation", "nerd_probe", "nerd_demux_max_mb",
               "nerd_mpv_config_dir", "nerd_shaders", "nerd_mpv_options", "nerd_adelay_per_channel",
               "nerd_hls_quality", "nerd_hls_max_kbps"),
    "rec": ("nerd_rec_template", "nerd_rec_post"),
    "serve": ("nerd_remote", "nerd_share", "nerd_remote_lan", "nerd_remote_port", "nerd_hooks",
              "nerd_hooks_urls", "nerd_mqtt", "nerd_mqtt_host", "nerd_mqtt_port", "nerd_mqtt_tls",
              "nerd_mqtt_client", "nerd_mqtt_prefix", "nerd_mqtt_ha"),
}


class _Defaults(nerd.Nerd):
    """Nerd's settings, noting the default each was read with: what an
    untouched page shows, so the list can say what was changed."""

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self.defaults: dict[str, str] = {}

    def get(self, key: str, default: str = "") -> str:
        self.defaults.setdefault(key, default)
        return super().get(key, default)


class _PartRow(QFrame):
    """One of Nerd's pages in its list: icon, name, what it is for, what is
    changed on it; a click (or Enter) opens it."""

    def __init__(self, page: SettingsPage, opened: Callable[[], None]) -> None:
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QLabel

        from .settings_ui import page_icon
        from .theme import P
        super().__init__()
        self.setObjectName("NerdPart")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(page.title)
        self._opened = opened
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 8, 10, 8)
        h.setSpacing(12)
        icon = QLabel()
        if page.icon_kind and page.icon_base is not None:
            icon.setPixmap(page_icon(page.icon_kind, page.icon_base, 20, tint=P.get("nerd")).pixmap(20, 20))
        icon.setFixedWidth(22)
        h.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(1)
        name = QLabel(page.title)
        name.setObjectName("NerdPartTitle")
        about = QLabel(page.subtitle)
        about.setObjectName("NerdPartAbout")
        about.setWordWrap(True)
        text.addWidget(name)
        text.addWidget(about)
        h.addLayout(text, 1)
        self.summary = QLabel("")
        self.summary.setObjectName("NerdPartSummary")
        h.addWidget(self.summary, 0, Qt.AlignmentFlag.AlignVCenter)
        arrow = QLabel("‹" if self.layoutDirection() == Qt.LayoutDirection.RightToLeft else "›")
        arrow.setObjectName("NerdPartArrow")
        h.addWidget(arrow, 0, Qt.AlignmentFlag.AlignVCenter)
        for w in (icon, name, about, self.summary, arrow):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_summary(self, text: str, lit: bool) -> None:
        self.summary.setText(text)
        self.summary.setProperty("lit", lit)
        self.summary.style().unpolish(self.summary)
        self.summary.style().polish(self.summary)

    def mouseReleaseEvent(self, event) -> None:       # noqa: N802 - Qt hook
        if self.rect().contains(event.position().toPoint()):
            self._opened()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:           # noqa: N802
        from PyQt6.QtCore import Qt
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._opened()
            return
        super().keyPressEvent(event)


def build(page: SettingsPage, settings: Any, parent: QWidget,
          boxes: list[dict] | None = None,
          edit: Callable[[str], None] | None = None,
          add: Callable[[], None] | None = None,
          pages: dict[str, SettingsPage] | None = None,
          show_pages: Callable[[bool], None] | None = None,
          source: Callable[[], dict | None] | None = None,
          channels: Callable[[], list[dict]] | None = None,
          rec_dir: Callable[[], str] | None = None,
          playlists: Callable[[], tuple[list[dict], str | None]] | None = None,
          remote: Callable[[dict], dict] | None = None,
          open_page: Callable[[str], None] | None = None,
          health_store: Callable[[], Any] | None = None,
          numbers: Callable[[str, bool, Callable[[int], None]], None] | None = None,
          ) -> Callable[[Callable[[str, Any], None]], None]:
    """*page* is Nerd's own: the switch, how it works, stats, diagnostics.
    *pages* ("net", "boxes", "lists", "player", "rec") take the rest, shown by
    *show_pages* while Nerd is on; without them it all goes on *page*.
    *boxes*: the box sources, each a click (*edit*, by id) from its options.
    *source*: the active source, which the network checks ask; *channels*:
    fetches its channel list, off the window's thread (the picon check
    matches against it). *remote*: puts the remote API and sharing as the
    page says now and returns how the server stands (its Test).
    *open_page*: shows a page by key (the list of Nerd's parts, their way
    back). *health_store*: the window's stream health (core/health.py),
    whose worst channels the page lists and clears; without it, the one
    stored in *settings*. *numbers(action, keep, done)*: the active
    source's channel numbers - "count", "all" (number them in list order)
    or "clear"; *done* gets how many have one now (-1: the list could not
    be read)."""
    n = _Defaults(settings)
    sub = pages or {}

    def at(key: str) -> SettingsPage:
        return sub.get(key, page)

    def val(key: str, default: str = "") -> str:
        return n.get(key, default)

    lines: list[CheckLine] = []

    def check(job: Callable[[], Any], title: str, test: bool = False,
              test_text: str = "") -> CheckLine:
        line = CheckLine(job, title, test=test, test_text=test_text)
        lines.append(line)
        return line

    def src() -> dict | None:
        return source() if source is not None else None

    # -- Nerd's own page: the switch, then how it works -------------------------
    c = page.card()
    switch = c.toggle(tr("nerd_switch"), n.on, tr("nerd_show_hint"))
    teaser = c.note(tr("nerd_teaser"))
    teaser_row = teaser.parentWidget()
    gated_from = len(page.cards)

    # -- what is in Nerd: its pages as a list that folds out under the switch ---
    def go(key: str) -> None:
        if open_page is not None:
            open_page(key)
    parts_card = page.card(tr("nerd_card_parts")) if pages else None
    part_rows: list[tuple[str, _PartRow]] = []
    for key in PARTS:
        sp = sub.get(key)
        if sp is None or parts_card is None:
            continue
        row = _PartRow(sp, lambda k=sp.key: go(k))
        parts_card.widget(row, "")
        part_rows.append((key, row))
        sp.set_crumb(tr("nerd_page"), lambda: go(page.key))

    c = page.card(tr("nerd_card_how"))
    how = [("nerd_how_saved_t", "nerd_how_saved"), ("nerd_how_alone_t", "nerd_how_alone"),
           ("nerd_how_checks_t", "nerd_how_checks")]
    if pages:
        how.append(("nerd_how_pages_t", "nerd_how_pages"))
    c.widget(_how_block(how), " ".join(tr(t) + " " + tr(b) for t, b in how))

    c = page.card(tr("nerd_card_view"))
    overlay = c.toggle(tr("nerd_overlay"), n.flag("nerd_overlay_more", True), tr("nerd_overlay_hint"))
    c.note(tr("nerd_intro"))

    # -- network: who the app says it is -------------------------------------------
    c = at("net").card(tr("nerd_card_identity"))
    c.note(tr("nerd_identity_intro"))
    ua_stored = val("nerd_user_agent")
    ua_key = ua_stored if ua_stored in nerd.UA_PRESETS or not ua_stored else "custom"
    ua_box = combo(UA_CHOICES, ua_key)
    c.row(tr("nerd_user_agent"), ua_box, tr("nerd_user_agent_hint"), search="user-agent UA")
    ua_custom = QLineEdit(ua_stored if ua_key == "custom" else "")
    ua_custom.setPlaceholderText("MyPlayer/1.0")
    ua_row = c.row(tr("nerd_ua_custom"), ua_custom)
    c.shown_if(ua_row, lambda: ua_box.currentData() == "custom")
    ua_box.currentIndexChanged.connect(lambda _i: c.refresh())
    referer = QLineEdit(val("nerd_referer"))
    referer.setPlaceholderText("https://portal.example.com/")
    c.row("Referer", referer, tr("nerd_referer_hint"))
    headers = text_box(val("nerd_headers"), "X-Forwarded-For: 203.0.113.7\nOrigin: https://example.com", 3)

    def ua_now() -> str:
        key = ua_box.currentData()
        if key == "custom":
            return ua_custom.text().strip() or nerd.DEFAULT_UA
        return nerd.UA_PRESETS.get(key, "") or nerd.DEFAULT_UA

    def hdrs_now() -> dict[str, str]:
        out = nerd.parse_headers(headers.toPlainText())
        if referer.text().strip():
            out["Referer"] = referer.text().strip()
        return out

    def proxy_now() -> str:
        return proxy.text().strip()

    def identity_job() -> Any:
        s = src()
        url = nerd_check.probe_url(s)
        ua, h, px = ua_now(), hdrs_now(), proxy_now()
        if url is None:
            return lambda: nerd_check.Check(nerd_check.INFO, "nerd_ck_no_source")
        return lambda: nerd_check.check_request(url, ua, h, px, secrets=nerd_check.source_secrets(s))
    id_line = check(identity_job, tr("nerd_ck_title_identity"), test=True,
                    test_text=tr("nerd_ck_test_source"))
    c.row(tr("nerd_headers"), headers, tr("nerd_headers_hint"), stretch_control=True,
          search="HTTP header", below=id_line)
    c.note(tr("nerd_applies_stream"))

    # -- network: the proxy ------------------------------------------------------------
    c = at("net").card(tr("nerd_card_proxy"))
    proxy = QLineEdit(val("nerd_proxy"))
    proxy.setPlaceholderText("http://192.168.1.1:3128")

    def proxy_job() -> Any:
        px = proxy_now()
        if not px:
            return None
        s = src()
        url = nerd_check.probe_url(s)
        ua, h = ua_now(), hdrs_now()
        if url is None:
            return lambda: nerd_check.check_proxy_listens(px)
        return lambda: nerd_check.check_request(url, ua, h, px, about_proxy=True,
                                                secrets=nerd_check.source_secrets(s))
    px_line = check(proxy_job, tr("nerd_ck_title_proxy"), test=True)
    c.row(tr("nerd_proxy"), proxy, tr("nerd_proxy_hint"), search="proxy HTTP", below=px_line)
    proxy_player = c.toggle(tr("nerd_proxy_player"), n.flag("nerd_proxy_player", True))
    pp_row = c.rows[-1][0]
    c.shown_if(pp_row, lambda: bool(proxy_now()))
    proxy.textChanged.connect(lambda _t: c.refresh())
    c.note(tr("nerd_applies_stream"))

    # -- network: the connection ---------------------------------------------------------
    c = at("net").card(tr("nerd_card_conn"))
    ip_box = combo([("auto", "nerd_ip_auto"), ("ipv4", "nerd_ip_v4"), ("ipv6", "nerd_ip_v6")],
                   val("nerd_ip_family", "auto"))

    def ip_job() -> Any:
        url = nerd_check.probe_url(src())
        fam = ip_box.currentData()
        if url is None:
            return lambda: nerd_check.Check(nerd_check.INFO, "nerd_ck_no_source")
        return lambda: nerd_check.check_ip_family(url, fam)
    ip_line = check(ip_job, tr("nerd_ck_title_ip"), test=True)
    c.row(tr("nerd_ip_family"), ip_box, tr("nerd_ip_family_hint"), search="IPv4 IPv6", below=ip_line)
    timeout = Stepper(lo=5, hi=180, value=n.num("nerd_net_timeout", 30) or 30, width=130)
    c.row(tr("nerd_timeout"), timeout, tr("nerd_timeout_hint"))
    recon = Stepper(lo=1, hi=60, value=n.num("nerd_reconnect_max", 5) or 5, width=130)
    c.row(tr("nerd_reconnect"), recon, tr("nerd_reconnect_hint"))
    c.note(tr("nerd_applies_conn"))

    # -- network: the VPN guard ------------------------------------------------------------
    c = at("net").card(tr("nerd_card_vpn"))
    guard = c.toggle(tr("nerd_vpn_guard"), n.flag("nerd_vpn_guard"), tr("nerd_vpn_guard_hint"))
    iface = QLineEdit(val("nerd_vpn_iface"))
    iface.setPlaceholderText(tr("nerd_vpn_iface_auto"))
    iface_row = c.row(tr("nerd_vpn_iface"), iface, tr("nerd_vpn_iface_hint"), search="WireGuard wg0 tun0 VPN")
    vpn_lan = c.toggle(tr("nerd_vpn_lan"), n.flag("nerd_vpn_lan"))
    lan_row = c.rows[-1][0]
    for r in (iface_row, lan_row):
        c.shown_if(r, lambda: guard.isChecked())
    guard.toggled.connect(lambda _on: c.refresh())

    def vpn_job() -> Any:
        name = iface.text().strip()
        return lambda: nerd_check.check_vpn(name)
    vpn_line = check(vpn_job, tr("nerd_ck_title_vpn"), test=True, test_text=tr("nerd_vpn_check"))
    c.widget(vpn_line, "VPN WireGuard")
    vpn_line.auto = True
    c.note(tr("nerd_applies_vpn"))

    # -- boxes and servers ---------------------------------------------------------------
    if boxes is not None:
        c = at("boxes").card(tr("nerd_card_boxes"))
        c.note(tr("nerd_boxes_intro"))
        for pl in boxes:
            btn = QPushButton(tr("nerd_box_options"))
            btn.clicked.connect(lambda _c=False, pid=pl.get("id"): edit and pid and edit(pid))
            c.row(str(pl.get("name") or pl.get("server") or ""), btn,
                  f"{BOX_NAMES.get(pl.get('kind') or '', '')} · {pl.get('server') or ''}",
                  search="box Enigma2 Tvheadend HDHomeRun")
        if not boxes:
            c.note(tr("nerd_no_boxes"))
        if add is not None:
            add_btn = QPushButton(tr("nerd_add_box"))
            add_btn.clicked.connect(lambda _c=False: add())
            c.layout_row(_hbox(add_btn), "box Enigma2 Tvheadend HDHomeRun add")

    # -- channels: picons ------------------------------------------------------------------
    c = at("lists").card(tr("nerd_card_picons"))
    picon_dir = QLineEdit(val("nerd_picon_dir"))
    picon_dir.setPlaceholderText("~/picons")
    pick_picons = QPushButton(tr("nerd_choose"))

    def choose_picons() -> None:
        d = QFileDialog.getExistingDirectory(parent, tr("nerd_picon_dir"), picon_dir.text() or "")
        if d:
            picon_dir.setText(d)
            picon_dir.editingFinished.emit()
    pick_picons.clicked.connect(choose_picons)
    picon_row = QHBoxLayout()
    picon_row.setSpacing(8)
    picon_row.addWidget(picon_dir, 1)
    picon_row.addWidget(pick_picons)

    def picon_job() -> Any:
        folder = picon_dir.text().strip()
        if not folder:
            return None
        fetch = channels

        def run() -> Any:
            try:
                rows = list(fetch() or []) if fetch is not None else []
            except Exception as e:      # no list now: count the picons alone
                log.info("nerd check: no channel list for picons: %s", e)
                rows = []
            return nerd_check.check_picons(folder, rows)
        return run
    pi_line = check(picon_job, tr("nerd_ck_title_picons"))
    picon_dir.textChanged.connect(pi_line.changed)
    c.row(tr("nerd_picon_dir"), picon_row, tr("nerd_picon_dir_hint"), stretch_control=True,
          search="picon logo SNP", below=pi_line)
    picon_over = c.toggle(tr("nerd_picon_override"), n.flag("nerd_picon_override"))
    c.note(tr("nerd_applies_reload"))

    # -- channels: multicast ---------------------------------------------------------------
    c = at("lists").card(tr("nerd_card_udpxy"))
    udpxy = QLineEdit(val("nerd_udpxy"))
    udpxy.setPlaceholderText("http://192.168.1.1:4022")

    def udpxy_job() -> Any:
        url = udpxy.text().strip()
        return (lambda: nerd_check.check_udpxy(url)) if url else None
    ux_line = check(udpxy_job, tr("nerd_ck_title_udpxy"), test=True)
    c.row("udpxy", udpxy, tr("nerd_udpxy_hint"), search="multicast udp rtp IPTV", below=ux_line)
    c.note(tr("nerd_applies_reload"))

    # -- channels: failover ----------------------------------------------------------------
    from ..services import failover
    c = at("lists").card(tr("nerd_card_failover"))
    fail_on = ToggleSwitch(n.flag("nerd_failover"))
    fail_on.setAccessibleName(tr("nerd_failover"))

    def failover_job() -> Any:
        pls, active = playlists() if playlists is not None else ([], None)
        return lambda: failover.check_sources(pls, active)
    fo_line = check(failover_job, tr("nerd_ck_title_failover"))
    c.row(tr("nerd_failover"), fail_on, tr("nerd_failover_hint"),
          search="failover backup another source fallback", below=fo_line)
    c.note(tr("nerd_applies_stream"))

    # -- channels: stream health -----------------------------------------------------------
    from ..core import health
    own_store = health.StreamHealth(settings) if health_store is None else None

    def store_now() -> Any:
        return health_store() if health_store is not None else own_store
    c = at("lists").card(tr("nerd_card_health"))
    health_on = ToggleSwitch(n.flag(health.SWITCH))
    health_on.setAccessibleName(tr("nerd_health"))
    c.row(tr("nerd_health"), health_on, tr("nerd_health_hint"),
          search="stream health statistics start time failures drops reconnects")
    worst = HealthList()
    c.widget(worst, "worst channels slowest start failures")
    clear_health = QPushButton(tr("nerd_health_clear"))

    def show_worst() -> None:
        rows = store_now().worst(10)
        worst.fill(rows)
        clear_health.setEnabled(bool(rows))

    def forget_health() -> None:
        store_now().clear()
        show_worst()
    clear_health.clicked.connect(lambda _c=False: forget_health())
    c.layout_row(_hbox(clear_health), "clear stream health")
    c.note(tr("nerd_applies_stream"))
    show_worst()
    at("lists").installEventFilter(_OnShow(at("lists"), show_worst))

    # -- channels: guide -------------------------------------------------------------------
    c = at("lists").card(tr("nerd_card_guide"))
    c.note(tr("nerd_epg_multi"))
    c.note(tr("nerd_epg_map_note"))

    # -- channels: numbers of the user's own ------------------------------------------------
    c = at("lists").card(tr("nerd_card_chno"))
    c.note(tr("nerd_chno_intro"))
    chno_keep = c.toggle(tr("nerd_chno_keep"), n.flag("nerd_numbers_keep", True), tr("nerd_chno_keep_hint"))
    chno_all = QPushButton(tr("nerd_chno_all_btn"))
    chno_clear = QPushButton(tr("nerd_chno_clear_btn"), objectName="Danger")
    # How many the active source has, under the button that changes it.
    chno_state = QLabel("")
    chno_state.setObjectName("SettingsHint")
    chno_state.setWordWrap(True)
    chno_state.setStyleSheet("font-size:12px;")
    c.row(tr("nerd_chno_all"), chno_all, tr("nerd_chno_all_hint"),
          search="channel number numbers LCN zap renumber order", below=chno_state)
    c.row(tr("nerd_chno_clear"), chno_clear, tr("nerd_chno_clear_hint"),
          search="channel number numbers reset")

    def chno_source() -> str:
        s = src() or {}
        return str(s.get("name") or s.get("server") or "") if s.get("id") else ""

    def chno_said(count: int) -> None:
        if sip.isdeleted(chno_state):
            return
        name = chno_source()
        for b in (chno_all, chno_clear):
            b.setEnabled(bool(name) and numbers is not None)
        if not name:
            chno_state.setText(tr("nerd_chno_no_source"))
        elif count < 0:
            chno_state.setText(tr("nerd_chno_failed"))
        elif count == 0:
            chno_state.setText(tr("nerd_chno_none", source=name))
        else:
            chno_state.setText(tr("nerd_chno_count", source=name, n=count))

    def chno_number_all() -> None:
        if numbers is None:
            return
        chno_all.setEnabled(False)
        chno_state.setText(tr("nerd_chno_working"))
        numbers("all", chno_keep.isChecked(), chno_said)

    def chno_clear_all() -> None:
        from .widgets import confirm
        if numbers is None or not confirm(parent, tr("nerd_chno_clear"),
                                          tr("nerd_chno_clear_ask", source=chno_source()),
                                          default_yes=False):
            return
        numbers("clear", False, chno_said)
    chno_all.clicked.connect(lambda _c=False: chno_number_all())
    chno_clear.clicked.connect(lambda _c=False: chno_clear_all())
    if numbers is not None:
        numbers("count", False, chno_said)
    else:
        chno_said(0)

    # -- the player: picture and sound -------------------------------------------------------
    c = at("player").card(tr("nerd_card_player"))
    spdif_on = set(val("nerd_spdif").split(","))
    spdif = [Chip(label, key in spdif_on) for key, label in SPDIF_LABELS]
    c.row(tr("nerd_spdif"), ChipGroups([("", spdif)]), tr("nerd_spdif_hint"), stretch_control=True,
          search="passthrough S/PDIF HDMI receiver AC3 DTS TrueHD")
    sync = combo([("", "nerd_sync_audio"), ("display-resample", "nerd_sync_resample"),
                  ("display-vdrop", "nerd_sync_vdrop")], val("nerd_video_sync"))
    c.row(tr("nerd_sync"), sync, tr("nerd_sync_hint"), search="video-sync display-resample")

    def picture_pairs() -> list[tuple[str, str]]:
        out = []
        codecs = [k for (k, _l), chip in zip(SPDIF_LABELS, spdif, strict=True) if chip.isChecked()]
        if codecs:
            out.append(("audio-spdif", ",".join(codecs)))
        if sync.currentData():
            out.append(("video-sync", sync.currentData()))
        if interp.isChecked():
            out += [("interpolation", "yes"), ("tscale", "oversample")]
        return out

    def picture_job() -> Any:
        pairs = picture_pairs()
        return (lambda: nerd_check.check_mpv_options("", extra=pairs, make_mpv=throwaway_mpv)) if pairs else None
    pic_line = check(picture_job, tr("nerd_ck_title_mpv"))
    interp = ToggleSwitch(n.flag("nerd_interpolation"))
    interp.setAccessibleName(tr("nerd_interpolation"))
    c.row(tr("nerd_interpolation"), interp, tr("nerd_interpolation_hint"), below=pic_line)
    for chip in spdif:
        chip.toggled.connect(pic_line.changed)
    sync.currentIndexChanged.connect(pic_line.changed)
    interp.toggled.connect(pic_line.changed)
    c.note(tr("nerd_applies_now"))

    # -- the player: channel start and buffer --------------------------------------------------
    c = at("player").card(tr("nerd_card_start"))
    probe = combo([("instant", "nerd_probe_instant"), ("", "nerd_probe_fast"),
                   ("thorough", "nerd_probe_thorough")], val("nerd_probe"))
    c.row(tr("nerd_probe"), probe, tr("nerd_probe_hint"))
    cache = combo([(str(mb), tr("nerd_cache_auto") if not mb else f"{mb} MB") for mb in CACHE_CHOICES],
                  str(n.num("nerd_demux_max_mb", 0)))
    c.row(tr("nerd_cache"), cache, tr("nerd_cache_hint"), search="demuxer-max-bytes")
    adelay = c.toggle(tr("nerd_adelay"), n.flag("nerd_adelay_per_channel", True), tr("nerd_adelay_hint"))
    c.note(tr("nerd_applies_stream"))

    # -- the player: adaptive streams (HLS) ------------------------------------------------------
    from ..core import hls_cap
    c = at("player").card(tr("nerd_card_hls"))
    hls_q = combo([("", "nerd_hls_best"), ("1080", "1080p"), ("720", "720p"), ("480", "480p"),
                   ("lowest", "nerd_hls_lowest")], val("nerd_hls_quality"))
    hls_spin = QSpinBox()
    hls_spin.setRange(0, 200000)
    hls_spin.setSingleStep(500)
    hls_spin.setSuffix(" kbit/s")
    hls_spin.setSpecialValueText(tr("nerd_hls_no_limit"))
    hls_spin.setValue(max(0, n.num("nerd_hls_max_kbps", 0)))
    hls_spin.setAccessibleName(tr("nerd_hls_max_rate"))
    hls_rate = Stepper(hls_spin, width=170)

    def hls_job() -> Any:
        cap = hls_cap.cap_of(hls_q.currentData(), hls_spin.value())
        return (lambda: hls_cap.check(cap, make_mpv=throwaway_mpv)) if cap.active else None
    hls_line = check(hls_job, tr("nerd_ck_title_hls"))
    c.row(tr("nerd_hls_quality"), hls_q, tr("nerd_hls_quality_hint"),
          search="HLS adaptive quality resolution 1080p 720p 480p variant hls-bitrate", below=hls_line)
    rate_row = c.row(tr("nerd_hls_max_rate"), hls_rate, tr("nerd_hls_max_rate_hint"),
                     search="HLS bitrate kbit hls-bitrate bandwidth")
    c.shown_if(rate_row, lambda: hls_q.currentData() != "lowest")
    hls_q.currentIndexChanged.connect(lambda _i: c.refresh())
    hls_q.currentIndexChanged.connect(hls_line.changed)
    hls_spin.valueChanged.connect(hls_line.changed)
    c.note(tr("nerd_applies_stream"))

    # -- the player: mpv itself ----------------------------------------------------------------
    c = at("player").card(tr("nerd_card_mpv"))
    conf_dir = QLineEdit(val("nerd_mpv_config_dir"))
    conf_dir.setPlaceholderText("~/.config/mpv")
    pick_dir = QPushButton(tr("nerd_choose"))

    def choose_dir() -> None:
        d = QFileDialog.getExistingDirectory(parent, tr("nerd_mpv_dir"), conf_dir.text() or "")
        if d:
            conf_dir.setText(d)
            conf_dir.editingFinished.emit()
    pick_dir.clicked.connect(choose_dir)
    dir_row = QHBoxLayout()
    dir_row.setSpacing(8)
    dir_row.addWidget(conf_dir, 1)
    dir_row.addWidget(pick_dir)

    def dir_job() -> Any:
        folder = conf_dir.text().strip()
        return (lambda: nerd_check.check_mpv_dir(folder)) if folder else None
    dir_line = check(dir_job, tr("nerd_ck_title_mpvdir"))
    conf_dir.textChanged.connect(dir_line.changed)
    c.row(tr("nerd_mpv_dir"), dir_row, tr("nerd_mpv_dir_hint"), stretch_control=True,
          search="mpv.conf scripts lua", below=dir_line)
    shaders = text_box(val("nerd_shaders"), "~/.config/mpv/shaders/FSRCNNX_x2_8-0-4-1.glsl", 2)
    add_shader = QPushButton(tr("nerd_add_shader"))

    def pick_shader() -> None:
        files, _f = QFileDialog.getOpenFileNames(parent, tr("nerd_shaders"), "",
                                                 "GLSL (*.glsl *.hook);;*")
        if files:
            have = shaders.toPlainText().strip()
            shaders.setPlainText("\n".join(x for x in [have, *files] if x))
    add_shader.clicked.connect(pick_shader)
    shader_col = QVBoxLayout()
    shader_col.setSpacing(6)
    shader_col.addWidget(shaders)
    shader_col.addLayout(_hbox(add_shader))

    def shader_job() -> Any:
        text = shaders.toPlainText()
        return (lambda: nerd_check.check_shaders(text)) if text.strip() else None
    sh_line = check(shader_job, tr("nerd_ck_title_shaders"))
    shaders.textChanged.connect(sh_line.changed)
    c.row(tr("nerd_shaders"), shader_col, tr("nerd_shaders_hint"), stretch_control=True,
          search="glsl FSRCNNX Anime4K upscaling", below=sh_line)
    raw = text_box(val("nerd_mpv_options"), "deband=yes\nscale=ewa_lanczossharp\nvolume-max=150", 6)

    def raw_job() -> Any:
        text = raw.toPlainText()
        return (lambda: nerd_check.check_mpv_options(text, make_mpv=throwaway_mpv)) if text.strip() else None
    raw_line = check(raw_job, tr("nerd_ck_title_mpv"))
    raw.textChanged.connect(raw_line.changed)
    c.row(tr("nerd_mpv_options"), raw, tr("nerd_mpv_options_hint"), stretch_control=True,
          search="mpv.conf options", below=raw_line)
    c.note(tr("nerd_applies_mpv"))

    # -- recordings: names, the folder, what follows ------------------------------------------
    from ..core import rec_extras
    c = at("rec").card(tr("nerd_card_rec_names"))
    template = QLineEdit(val("nerd_rec_template"))
    template.setPlaceholderText(rec_extras.DEFAULT_TEMPLATE)

    def template_job() -> Any:
        text = template.text()
        return lambda: rec_extras.check_template(text)
    tpl_line = check(template_job, tr("nerd_ck_title_tpl"))
    template.textChanged.connect(tpl_line.changed)
    c.row(tr("nerd_rec_template"), template, tr("nerd_rec_template_hint"), stretch_control=True,
          search="file name template folder recording", below=tpl_line)
    c.note(tr("nerd_rec_fields"))

    c = at("rec").card(tr("nerd_card_rec_folder"))

    def folder_job() -> Any:
        folder = rec_dir() if rec_dir is not None else ""
        return lambda: rec_extras.check_folder(folder)
    folder_line = check(folder_job, tr("nerd_ck_title_recdir"))
    c.widget(folder_line, "recording folder network share NAS free space")
    c.note(tr("nerd_rec_folder_note"))

    c = at("rec").card(tr("nerd_card_rec_after"))
    post = QLineEdit(val("nerd_rec_post"))
    post.setPlaceholderText('comskip "{path}"')

    def post_job() -> Any:
        cmd = post.text().strip()
        return (lambda: rec_extras.check_post(cmd)) if cmd else None
    post_line = check(post_job, tr("nerd_ck_title_post"), test=True,
                      test_text=tr("nerd_ck_test_post"))
    c.row(tr("nerd_rec_post"), post, tr("nerd_rec_post_hint"), stretch_control=True,
          search="command script comskip post-processing after recording", below=post_line)
    c.note(tr("nerd_applies_rec"))

    # -- remote control and sharing --------------------------------------------------------
    from ..core import remote_api
    c = at("serve").card(tr("nerd_card_remote"))
    remote_on = ToggleSwitch(n.flag("nerd_remote"))
    remote_on.setAccessibleName(tr("nerd_remote"))
    c.row(tr("nerd_remote"), remote_on, tr("nerd_remote_hint"),
          search="remote API HTTP REST Home Assistant curl script control")
    # A word joiner after each "/": a path is never broken after its slash.
    c.note(tr("nerd_remote_calls").replace("/", "/\u2060"))

    c = at("serve").card(tr("nerd_card_share"))
    share_on = ToggleSwitch(n.flag("nerd_share"))
    share_on.setAccessibleName(tr("nerd_share"))
    c.row(tr("nerd_share"), share_on, tr("nerd_share_hint"),
          search="server share M3U XMLTV Kodi TiviMate VLC playlist guide")
    copy_m3u = QPushButton(tr("nerd_share_copy_m3u"))
    copy_epg = QPushButton(tr("nerd_share_copy_epg"))
    c.row(tr("nerd_share_addresses"), _fitted(copy_m3u, copy_epg), tr("nerd_share_addresses_hint"),
          search="address URL copy playlist guide")

    c = at("serve").card(tr("nerd_card_access"))
    who = combo([("local", "nerd_remote_local"), ("lan", "nerd_remote_home")],
                "lan" if n.flag("nerd_remote_lan") else "local")
    c.row(tr("nerd_remote_who"), who, tr("nerd_remote_who_hint"), search="LAN network localhost listen")
    port = Stepper(lo=1024, hi=65535, value=n.num("nerd_remote_port", remote_api.DEFAULT_PORT), width=150)
    c.row(tr("nerd_remote_port"), port, tr("nerd_remote_port_hint"), search="port")
    token = QLineEdit(val("nerd_remote_token"))
    token.setReadOnly(True)
    token.setEchoMode(QLineEdit.EchoMode.Password)
    token.setAccessibleName(tr("nerd_remote_token"))
    copy_tok = QPushButton(tr("problem_copy"))
    new_tok = QPushButton(tr("nerd_remote_new_token"))
    drop_tok = QPushButton(tr("nerd_remote_drop_token"))
    drop_tok.setToolTip(tr("nerd_remote_drop_token_tip"))

    def wanted_remote() -> dict:
        return {"remote": switch.isChecked() and remote_on.isChecked(),
                "share": switch.isChecked() and share_on.isChecked(),
                "port": port.value(), "host": "0.0.0.0" if who.currentData() == "lan" else "127.0.0.1",
                "token": token.text().strip()}

    def ensure_token(*_a) -> None:
        if (remote_on.isChecked() or share_on.isChecked()) and not token.text().strip():
            token.setText(remote_api.new_token())
    remote_on.toggled.connect(ensure_token)
    share_on.toggled.connect(ensure_token)

    def address(path: str) -> str:
        ips = remote_api.lan_addresses() if who.currentData() == "lan" else []
        ensure_token()
        return f"http://{(ips or ['127.0.0.1'])[0]}:{port.value()}{path}?token={token.text().strip()}"

    def copy(btn: QPushButton, text: str) -> None:
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(text)
        label = btn.text()
        btn.setText("✓ " + tr("problem_copied"))
        QTimer.singleShot(1500, lambda: btn.setText(label) if not sip.isdeleted(btn) else None)
    copy_m3u.clicked.connect(lambda _c=False: copy(copy_m3u, address("/playlist.m3u")))
    copy_epg.clicked.connect(lambda _c=False: copy(copy_epg, address("/guide.xml.gz")))
    copy_tok.clicked.connect(lambda _c=False: (ensure_token(), copy(copy_tok, token.text().strip())))
    new_tok.clicked.connect(lambda _c=False: token.setText(remote_api.new_token()))

    def drop_token() -> None:
        """Nobody gets in without a token: both off; turning one on again
        makes a new token."""
        remote_on.setChecked(False)
        share_on.setChecked(False)
        token.setText("")
    drop_tok.clicked.connect(lambda _c=False: drop_token())

    def remote_job() -> Any:
        if remote is None:
            return None
        ensure_token()
        st = remote(wanted_remote())
        args = (st["host"], st["port"], st["token"], st["remote"], st["share"], st["running"], st["error"])
        return lambda: remote_api.check_server(*args)
    remote_line = check(remote_job, tr("nerd_ck_title_remote"), test=True, test_text=tr("nerd_ck_test_remote"))
    for sig in (remote_on.toggled, share_on.toggled, who.currentIndexChanged, port.valueChanged, token.textChanged):
        sig.connect(remote_line.changed)
    tok_row = QHBoxLayout()
    tok_row.setSpacing(8)
    tok_row.addWidget(token, 1)
    tok_row.addWidget(copy_tok)
    tok_row.addWidget(new_tok)
    tok_row.addWidget(drop_tok)
    c.row(tr("nerd_remote_token"), tok_row, tr("nerd_remote_token_hint"), stretch_control=True,
          search="token password secret key", below=remote_line)
    c.note(tr("nerd_applies_remote"))

    # -- events pushed out: webhooks and MQTT ------------------------------------------------
    hooks = _events_cards(at("serve"), n, lines)

    # -- diagnostics (Nerd's own page) --------------------------------------------------------
    c = page.card(tr("nerd_card_diag"))
    level = combo([("info", "nerd_log_info"), ("debug", "nerd_log_debug")], val("nerd_log_level", "info"))
    c.row(tr("nerd_log_level"), level, tr("nerd_log_level_hint"), search="log debug")
    log_file = c.toggle(tr("nerd_log_file"), n.flag("nerd_log_file"), tr("nerd_log_file_hint"))
    open_logs = QPushButton(tr("nerd_open_logs"))

    def open_log_folder() -> None:
        from ..core.xdg import open_url
        os.makedirs(log_dir(), exist_ok=True)
        from pathlib import Path
        open_url(Path(log_dir()).as_uri())
    open_logs.clicked.connect(open_log_folder)
    home = os.path.expanduser("~")
    shown_dir = "~" + log_dir()[len(home):] if log_dir().startswith(home) else log_dir()
    c.row(tr("nerd_log_folder"), open_logs, shown_dir, search="log logs folder")
    info_btn = QPushButton(tr("nerd_show_diag"))

    def show_diag() -> None:
        from .dialogs import ProblemDialog
        ProblemDialog(parent, tr("nerd_diag_title"), tr("nerd_diag_summary"),
                      diagnostics(settings), state="info").exec()
    info_btn.clicked.connect(show_diag)
    c.row(tr("nerd_diag_row"), info_btn, tr("nerd_diag_hint"), search="diagnostics bug report copy")

    def values() -> dict[str, str]:
        out: dict[str, str] = {}

        def put(key: str, value: Any) -> None:
            out[key] = str(value)
        put("nerd_enabled", "true" if switch.isChecked() else "false")
        ua = ua_box.currentData()
        put("nerd_user_agent", ua_custom.text().strip() if ua == "custom" else ua)
        put("nerd_referer", referer.text().strip())
        put("nerd_headers", headers.toPlainText().strip())
        put("nerd_proxy", proxy.text().strip())
        put("nerd_proxy_player", "true" if proxy_player.isChecked() else "false")
        put("nerd_ip_family", ip_box.currentData())
        put("nerd_net_timeout", str(timeout.value()))
        put("nerd_reconnect_max", str(recon.value()))
        put("nerd_vpn_guard", "true" if guard.isChecked() else "false")
        put("nerd_vpn_iface", iface.text().strip())
        put("nerd_vpn_lan", "true" if vpn_lan.isChecked() else "false")
        put("nerd_spdif", ",".join(k for (k, _l), chip in zip(SPDIF_LABELS, spdif, strict=True) if chip.isChecked()))
        put("nerd_video_sync", sync.currentData())
        put("nerd_interpolation", "true" if interp.isChecked() else "false")
        put("nerd_probe", probe.currentData())
        put("nerd_demux_max_mb", cache.currentData())
        put("nerd_mpv_config_dir", conf_dir.text().strip())
        put("nerd_shaders", shaders.toPlainText().strip())
        put("nerd_mpv_options", raw.toPlainText().strip())
        put("nerd_log_level", level.currentData())
        put("nerd_log_file", "true" if log_file.isChecked() else "false")
        put("nerd_overlay_more", "true" if overlay.isChecked() else "false")
        put("nerd_picon_dir", picon_dir.text().strip())
        put("nerd_picon_override", "true" if picon_over.isChecked() else "false")
        put("nerd_udpxy", udpxy.text().strip())
        put("nerd_adelay_per_channel", "true" if adelay.isChecked() else "false")
        put("nerd_hls_quality", hls_q.currentData())
        put("nerd_hls_max_kbps", str(hls_spin.value()))
        put("nerd_rec_template", template.text().strip())
        put("nerd_rec_post", post.text().strip())
        put("nerd_failover", "true" if fail_on.isChecked() else "false")
        put("nerd_health", "true" if health_on.isChecked() else "false")
        put("nerd_numbers_keep", "true" if chno_keep.isChecked() else "false")
        put("nerd_remote", "true" if remote_on.isChecked() else "false")
        put("nerd_share", "true" if share_on.isChecked() else "false")
        put("nerd_remote_lan", "true" if who.currentData() == "lan" else "false")
        put("nerd_remote_port", port.value())
        put("nerd_remote_token", token.text().strip())
        out.update(hooks())
        return out

    # Off: the switch and what Nerd holds, nothing else. On: all of it - on
    # their own pages where there are pages, else gated here.
    first = page.cards[gated_from - 1]
    first.shown_if(teaser_row, lambda: not switch.isChecked())
    for card in page.cards[gated_from:]:
        for row, _text in card.rows:
            prev = card._shown_if.get(id(row))
            card.shown_if(row, lambda prev=prev: switch.isChecked() and (prev is None or prev()))

    def start_checks() -> None:
        for line in lines:
            if line.test_btn is None or getattr(line, "auto", False):
                line.start()

    def part_summary(key: str) -> tuple[str, bool]:
        if key == "boxes":
            return (tr("nerd_part_boxes", n=len(boxes)), True) if boxes else (tr("nerd_part_no_boxes"), False)
        if key == "serve" and (remote_on.isChecked() or share_on.isChecked()):
            return tr("nerd_part_on", port=port.value()), True
        now = values()
        changed = sum(1 for k in PART_KEYS.get(key, ()) if now.get(k, "") != n.defaults.get(k, ""))
        return (tr("nerd_part_changed", n=changed), True) if changed else (tr("nerd_part_untouched"), False)

    def refresh_parts() -> None:
        for key, row in part_rows:
            row.set_summary(*part_summary(key))
    if part_rows:
        page.installEventFilter(_OnShow(page, refresh_parts))

    def unfold(card: QWidget) -> None:
        """The list opens downwards rather than appearing all at once."""
        from PyQt6.QtCore import QEasingCurve, QPropertyAnimation
        end = card.sizeHint().height()
        anim = QPropertyAnimation(card, b"maximumHeight", card)
        anim.setDuration(220)
        anim.setStartValue(0)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda: card.setMaximumHeight(16777215) if not sip.isdeleted(card) else None)
        anim.start()

    def flip(on: bool) -> None:
        for card in page.cards:
            card.refresh()
        if show_pages is not None:
            show_pages(on)
        if on:
            start_checks()
            refresh_parts()
            if parts_card is not None and parts_card.isVisibleTo(page):
                unfold(parts_card)
    switch.toggled.connect(flip)
    if show_pages is not None:
        show_pages(n.on)
    if n.on:
        start_checks()

    shown = values()
    refresh_parts()

    def commit(put: Callable[[str, Any], None]) -> None:
        """Only what was changed is written: a key never stored that still
        says what the page showed stays unstored (an untouched window
        changes nothing)."""
        for key, value in values().items():
            if settings.value(key) is None and value == shown[key]:
                continue
            put(key, value)

    commit.lines = lines  # type: ignore[attr-defined]
    return commit


def _events_cards(page: SettingsPage, n: nerd.Nerd, lines: list[CheckLine]) -> Callable[[], dict[str, str]]:
    """Events pushed out (core/events.py): webhooks and MQTT, each with a
    Test that sends a test event and says whether it arrived. Returns what
    the cards hold, as the settings they are stored as."""
    from ..core import events
    from ..core.nerd_check import FAIL, INFO, WARN, Check

    def flag(sw: ToggleSwitch) -> str:
        return "true" if sw.isChecked() else "false"

    # -- webhooks ------------------------------------------------------------------
    c = page.card(tr("nerd_card_hooks"))
    hooks_on = ToggleSwitch(n.flag("nerd_hooks"))
    hooks_on.setAccessibleName(tr("nerd_hooks"))
    c.row(tr("nerd_hooks"), hooks_on, tr("nerd_hooks_hint"),
          search="webhook webhooks events push POST JSON Home Assistant Node-RED automation")
    c.note(tr("nerd_events_what"))
    urls = text_box(n.get("nerd_hooks_urls"), "http://homeassistant.local:8123/api/webhook/…", 3)
    urls.setAccessibleName(tr("nerd_hooks_urls"))
    header = QLineEdit(n.get("nerd_hooks_header"))
    header.setEchoMode(QLineEdit.EchoMode.Password)
    header.setPlaceholderText("Authorization: Bearer …")
    header.setAccessibleName(tr("nerd_hooks_header"))

    def hooks_preview() -> Check | None:
        if not hooks_on.isChecked():
            return Check(INFO, "nerd_ck_events_off")
        good, bad = events.hook_urls(urls.toPlainText())
        if bad:
            return Check(WARN, "nerd_ck_hooks_bad", {"n": bad[0]})
        return None if good else Check(FAIL, "nerd_ck_hooks_none")

    def hooks_job() -> Any:
        good = events.hook_urls(urls.toPlainText())[0]
        if not hooks_on.isChecked() or not good:
            return None
        head = events.hook_header(header.text())
        return lambda: events.check_hooks(good, head)
    hooks_line = CheckLine(hooks_job, tr("nerd_card_hooks"), test=True, preview=hooks_preview)
    lines.append(hooks_line)
    shown = [c.row(tr("nerd_hooks_urls"), urls, tr("nerd_hooks_urls_hint"), stretch_control=True,
                   search="webhook address URL"),
             c.row(tr("nerd_hooks_header"), header, tr("nerd_hooks_header_hint"), stretch_control=True,
                   search="webhook header token Authorization Bearer secret", below=hooks_line)]
    for r in shown:
        c.shown_if(r, hooks_on.isChecked)
    hooks_on.toggled.connect(lambda _on, c=c: c.refresh())
    for sig in (hooks_on.toggled, urls.textChanged, header.textChanged):
        sig.connect(hooks_line.changed)

    # -- MQTT -----------------------------------------------------------------------
    c = page.card(tr("nerd_card_mqtt"))
    mqtt_on = ToggleSwitch(n.flag("nerd_mqtt"))
    mqtt_on.setAccessibleName(tr("nerd_mqtt"))
    c.row(tr("nerd_mqtt"), mqtt_on, tr("nerd_mqtt_hint"),
          search="MQTT broker Mosquitto Home Assistant Node-RED publish topic events")
    host = QLineEdit(n.get("nerd_mqtt_host"))
    host.setPlaceholderText("homeassistant.local")
    host.setAccessibleName(tr("nerd_mqtt_host"))
    port = Stepper(lo=1, hi=65535, value=n.num("nerd_mqtt_port", 1883), width=150)
    tls = ToggleSwitch(n.flag("nerd_mqtt_tls"))
    tls.setAccessibleName(tr("nerd_mqtt_tls"))

    def tls_port(on: bool) -> None:
        if on and port.value() == 1883:
            port.setValue(8883)
        elif not on and port.value() == 8883:
            port.setValue(1883)
    tls.toggled.connect(tls_port)
    user = QLineEdit(n.get("nerd_mqtt_user"))
    user.setPlaceholderText(tr("login_username"))
    user.setAccessibleName(tr("login_username"))
    password = QLineEdit(n.get("nerd_mqtt_pass"))
    password.setEchoMode(QLineEdit.EchoMode.Password)
    password.setPlaceholderText(tr("login_password"))
    password.setAccessibleName(tr("login_password"))
    login = QHBoxLayout()
    login.setSpacing(8)
    login.addWidget(user, 1)
    login.addWidget(password, 1)
    client = QLineEdit(n.get("nerd_mqtt_client"))
    client.setPlaceholderText(events.default_client_id())
    client.setAccessibleName(tr("nerd_mqtt_client"))
    prefix = QLineEdit(n.get("nerd_mqtt_prefix"))
    prefix.setPlaceholderText(events.DEFAULT_PREFIX)
    prefix.setAccessibleName(tr("nerd_mqtt_prefix"))
    ha = ToggleSwitch(n.flag("nerd_mqtt_ha"))
    ha.setAccessibleName(tr("nerd_mqtt_ha"))

    def mqtt_now() -> events.Config:
        return events.Config(mqtt_host=host.text().strip(), mqtt_port=port.value(), mqtt_tls=tls.isChecked(),
                             mqtt_user=user.text().strip(), mqtt_pass=password.text(),
                             client_id=client.text().strip() or events.default_client_id(),
                             prefix=events.clean_prefix(prefix.text()), ha=ha.isChecked())

    def mqtt_preview() -> Check | None:
        if not mqtt_on.isChecked():
            return Check(INFO, "nerd_ck_events_off")
        return None if host.text().strip() else Check(FAIL, "nerd_ck_mqtt_nohost")

    def mqtt_job() -> Any:
        if not mqtt_on.isChecked() or not host.text().strip():
            return None
        cfg = mqtt_now()
        return lambda: events.check_mqtt(cfg)
    mqtt_line = CheckLine(mqtt_job, tr("nerd_card_mqtt"), test=True, preview=mqtt_preview)
    lines.append(mqtt_line)
    shown = [c.row(tr("nerd_mqtt_host"), host, search="MQTT broker host address server"),
             c.row(tr("nerd_remote_port"), port, tr("nerd_mqtt_port_hint"), search="MQTT port 1883 8883"),
             c.row(tr("nerd_mqtt_tls"), tls, search="TLS SSL encrypted 8883"),
             c.row(tr("nerd_mqtt_login"), login, tr("nerd_mqtt_login_hint"), stretch_control=True,
                   search="MQTT user name password login"),
             c.row(tr("nerd_mqtt_client"), client, search="MQTT client id"),
             c.row(tr("nerd_mqtt_prefix"), prefix, tr("nerd_mqtt_prefix_hint"), search="MQTT topic prefix"),
             c.row(tr("nerd_mqtt_ha"), ha, tr("nerd_mqtt_ha_hint"),
                   search="Home Assistant discovery sensor device", below=mqtt_line)]
    for r in shown:
        c.shown_if(r, mqtt_on.isChecked)
    mqtt_on.toggled.connect(lambda _on, c=c: c.refresh())
    for sig in (mqtt_on.toggled, host.textChanged, port.valueChanged, tls.toggled, user.textChanged,
                password.textChanged, client.textChanged, prefix.textChanged, ha.toggled):
        sig.connect(mqtt_line.changed)
    c.note(tr("nerd_applies_hooks"))

    def values() -> dict[str, str]:
        return {"nerd_hooks": flag(hooks_on), "nerd_hooks_urls": urls.toPlainText().strip(),
                "nerd_hooks_header": header.text().strip(), "nerd_mqtt": flag(mqtt_on),
                "nerd_mqtt_host": host.text().strip(), "nerd_mqtt_port": str(port.value()),
                "nerd_mqtt_tls": flag(tls), "nerd_mqtt_user": user.text().strip(),
                "nerd_mqtt_pass": password.text(), "nerd_mqtt_client": client.text().strip(),
                "nerd_mqtt_prefix": prefix.text().strip(), "nerd_mqtt_ha": flag(ha)}
    return values


class _OnShow(QWidget):
    """Runs *fn* each time *target* is shown (the list's counts, fresh)."""

    def __init__(self, target: QWidget, fn: Callable[[], None]) -> None:
        super().__init__(target)
        self.hide()
        self._fn = fn

    def eventFilter(self, obj, event) -> bool:        # noqa: N802 - Qt hook
        from PyQt6.QtCore import QEvent
        if event.type() == QEvent.Type.Show:
            self._fn()
        return False


class HealthList(QWidget):
    """Stream health's worst channels: a dot in the colour of how each is
    doing, its name, its numbers - or a line saying nothing is recorded."""

    def __init__(self) -> None:
        super().__init__()
        self._col = QVBoxLayout(self)
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(8)
        self.rows: list[tuple[str, str]] = []     # (name, numbers) shown

    def fill(self, worst: list[tuple[str, dict]]) -> None:
        import html

        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QLabel

        from ..core import health
        from .dialogs import health_secs
        from .theme import P
        while self._col.count():
            item = self._col.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.deleteLater()
        self.rows = []
        if not worst:
            empty = QLabel(tr("nerd_health_empty"))
            empty.setWordWrap(True)
            empty.setStyleSheet(f"color:{P['muted']}; font-size:12px;")
            self._col.addWidget(empty)
            return
        head = QLabel(tr("nerd_health_worst"))
        head.setStyleSheet(f"color:{P['text']}; font-weight:650;")
        self._col.addWidget(head)
        for key, e in worst:
            name = str(e.get("name") or key)
            starts, fails, drops = health.counts(e)
            numbers = tr("nerd_health_line", fails=fails, starts=starts,
                         avg=health_secs(health.average(e)), drops=drops)
            dot = P.get(health.grade(e) or "muted", P["muted"])
            line = QLabel(f"<span style='color:{dot}'>●</span>&nbsp;"
                          f"<span style='color:{P['text']}; font-weight:600'>{html.escape(name)}</span><br>"
                          f"<span style='color:{P['text2']}; font-size:12px'>{html.escape(numbers)}</span>")
            line.setTextFormat(Qt.TextFormat.RichText)
            line.setWordWrap(True)
            self._col.addWidget(line)
            self.rows.append((name, numbers))


def _how_block(items: list[tuple[str, str]]) -> QWidget:
    """How Nerd works, as small tiles two by two: a mark in Nerd's colour,
    a few words, what they mean - read at a glance, not as a wall of text.
    The tile about the checks shows the marks themselves."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor
    from PyQt6.QtWidgets import QGridLayout, QLabel

    from .settings_ui import page_icon
    from .theme import P
    nerd_col = QColor(P.get("nerd", P["accent"]))
    tint = f"rgba({nerd_col.red()},{nerd_col.green()},{nerd_col.blue()},0.09)"
    edge = f"rgba({nerd_col.red()},{nerd_col.green()},{nerd_col.blue()},0.22)"
    marks = {"nerd_how_saved_t": "trakt", "nerd_how_alone_t": "knobs", "nerd_how_pages_t": "channels"}
    box = QWidget()
    grid = QGridLayout(box)
    grid.setContentsMargins(0, 4, 0, 4)
    grid.setHorizontalSpacing(10)
    grid.setVerticalSpacing(10)
    for i, (title, body) in enumerate(items):
        tile = QFrame()
        tile.setObjectName("NerdHowTile")
        tile.setStyleSheet(f"QFrame#NerdHowTile {{ background:{tint}; border:1px solid {edge}; border-radius:12px; }}"
                           " QFrame#NerdHowTile QLabel { background: transparent; border: none; }")
        col = QVBoxLayout(tile)
        col.setContentsMargins(14, 12, 14, 12)
        col.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(8)
        if title == "nerd_how_checks_t":
            mark = QLabel(f"<span style='color:{P.get('ok', P['text'])}'>✓</span>&nbsp;"
                          f"<span style='color:{P.get('warn', P['text'])}'>!</span>&nbsp;"
                          f"<span style='color:{P['error']}'>✕</span>")
            mark.setTextFormat(Qt.TextFormat.RichText)
            mark.setStyleSheet("font-size:15px; font-weight:800;")
        else:
            mark = QLabel()
            mark.setPixmap(page_icon(marks.get(title, "nerd"), lambda k, sz, c: _blank(sz), 18,
                                     tint=nerd_col.name()).pixmap(18, 18))
        name = QLabel(tr(title))
        name.setStyleSheet(f"color:{P['text']}; font-weight:650;")
        head.addWidget(mark)
        head.addWidget(name, 1)
        text = QLabel(tr(body))
        text.setWordWrap(True)
        text.setStyleSheet(f"color:{P['text2']}; font-size:12px;")
        col.addLayout(head)
        col.addWidget(text)
        col.addStretch(1)
        grid.addWidget(tile, i // 2, i % 2)
    grid.setColumnStretch(0, 1)
    grid.setColumnStretch(1, 1)
    return box


def _blank(size: int):
    from PyQt6.QtGui import QPixmap
    pm = QPixmap(size, size)
    pm.fill(Qt_transparent())
    return pm


def Qt_transparent():  # noqa: N802 - a colour, named as Qt names it
    from PyQt6.QtCore import Qt
    return Qt.GlobalColor.transparent


def _fitted(*widgets: QWidget) -> QWidget:
    """Buttons side by side at the width they need: a row's control column
    is as wide as a dropdown, and two buttons squeezed into it cut their
    words off."""
    box = QWidget()
    box.setLayout(_hbox(*widgets))
    box.layout().setContentsMargins(0, 0, 0, 0)
    box.setFixedWidth(box.sizeHint().width())
    return box


def _hbox(*widgets: QWidget) -> QHBoxLayout:
    h = QHBoxLayout()
    h.setSpacing(8)
    for w in widgets:
        h.addWidget(w)
    h.addStretch(1)
    return h


def diagnostics(settings: Any) -> str:
    """What a bug report needs, without a single secret: versions, the
    system, which Nerd options are set (their names, and values only where
    they cannot carry a login)."""
    from .. import __version__
    lines = [f"dopeIPTV {__version__}", f"Python {platform.python_version()} on {platform.platform()}",
             f"Frozen: {bool(getattr(sys, 'frozen', False))}"]
    try:
        from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
        lines.append(f"Qt {QT_VERSION_STR}, PyQt {PYQT_VERSION_STR}")
    except ImportError:
        pass
    try:
        import mpv
        lines.append(f"libmpv: {getattr(mpv, '__version__', '?')} (python-mpv)")
    except Exception as e:
        lines.append(f"libmpv: not loaded ({type(e).__name__})")
    n = nerd.Nerd(settings)
    lines.append(f"Nerd: {'on' if n.on else 'off'}")
    safe = {"nerd_ip_family", "nerd_net_timeout", "nerd_reconnect_max", "nerd_spdif", "nerd_video_sync",
            "nerd_interpolation", "nerd_probe", "nerd_demux_max_mb", "nerd_log_level", "nerd_vpn_guard",
            "nerd_vpn_lan", "nerd_proxy_player", "nerd_failover", "nerd_health", "nerd_remote", "nerd_share",
            "nerd_remote_lan", "nerd_remote_port", "nerd_hls_quality", "nerd_hls_max_kbps",
            "nerd_hooks", "nerd_mqtt", "nerd_mqtt_port", "nerd_mqtt_tls", "nerd_mqtt_ha"}
    for key in sorted(safe):
        v = n.get(key)
        if v:
            lines.append(f"  {key} = {v}")
    for key in ("nerd_user_agent", "nerd_referer", "nerd_headers", "nerd_proxy", "nerd_mpv_options",
                "nerd_mpv_config_dir", "nerd_shaders", "nerd_vpn_iface", "nerd_hooks_urls",
                "nerd_hooks_header", "nerd_mqtt_host", "nerd_mqtt_user", "nerd_mqtt_pass"):
        if n.get(key):
            lines.append(f"  {key} = (set)")
    up = nerd.vpn_interfaces()
    lines.append(f"VPN interfaces up: {', '.join(up) if up else 'none'}")
    if n.on and n.flag("nerd_log_file"):
        lines.append("Log file: on (rotating, in the app's log folder)")
    return "\n".join(lines)
