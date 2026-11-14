"""The Nerd page in Settings: the network, the VPN guard, the player (mpv)
all the way down, and diagnostics. Shown only once Nerd is turned on
(General, or seven taps on the version in About); core/nerd.py turns what is
stored here into what the player and the network do.

``build`` fills the page and returns ``commit(put)``, which open_settings
calls with its own put() like every other page's controls.
"""

from __future__ import annotations

import platform
import sys
from typing import Any, Callable

from PyQt6.QtGui import QFont, QGuiApplication
from PyQt6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from ..core import nerd
from ..i18n import tr
from .settings_ui import Chip, ChipGroups, SettingsPage, Stepper

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


def build(page: SettingsPage, settings: Any, parent: QWidget,
          boxes: list[dict] | None = None,
          edit: Callable[[str], None] | None = None,
          add: Callable[[], None] | None = None) -> Callable[[Callable[[str, Any], None]], None]:
    """*boxes*: the box sources, each listed with its options a click
    away (*edit* opens a source by id)."""
    n = nerd.Nerd(settings)

    def val(key: str, default: str = "") -> str:
        return n.get(key, default)

    # -- the switch: everything below it is there only while it is on -------
    c = page.card()
    switch = c.toggle(tr("nerd_switch"), n.on, tr("nerd_show_hint"))
    teaser = c.note(tr("nerd_teaser"))
    teaser_row = teaser.parentWidget()
    gated_from = len(page.cards)

    # -- the overlay ------------------------------------------------------------
    c = page.card(tr("nerd_card_view"))
    overlay = c.toggle(tr("nerd_overlay"), n.flag("nerd_overlay_more", True), tr("nerd_overlay_hint"))
    c.note(tr("nerd_intro"))

    # -- the boxes and servers ------------------------------------------------------
    if boxes is not None:
        c = page.card(tr("nerd_card_boxes"))
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

    # -- network ------------------------------------------------------------------
    c = page.card(tr("nerd_card_network"))
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
    c.row(tr("nerd_headers"), headers, tr("nerd_headers_hint"), stretch_control=True, search="HTTP header")
    proxy = QLineEdit(val("nerd_proxy"))
    proxy.setPlaceholderText("http://192.168.1.1:3128")
    c.row(tr("nerd_proxy"), proxy, tr("nerd_proxy_hint"), search="proxy HTTP")
    proxy_player = c.toggle(tr("nerd_proxy_player"), n.flag("nerd_proxy_player", True))
    ip_box = combo([("auto", "nerd_ip_auto"), ("ipv4", "nerd_ip_v4"), ("ipv6", "nerd_ip_v6")],
                   val("nerd_ip_family", "auto"))
    c.row(tr("nerd_ip_family"), ip_box, tr("nerd_ip_family_hint"), search="IPv4 IPv6")
    timeout = Stepper(lo=5, hi=180, value=n.num("nerd_net_timeout", 30) or 30, width=130)
    c.row(tr("nerd_timeout"), timeout, tr("nerd_timeout_hint"))
    recon = Stepper(lo=1, hi=60, value=n.num("nerd_reconnect_max", 5) or 5, width=130)
    c.row(tr("nerd_reconnect"), recon, tr("nerd_reconnect_hint"))

    # -- the VPN guard ------------------------------------------------------------
    c = page.card(tr("nerd_card_vpn"))
    guard = c.toggle(tr("nerd_vpn_guard"), n.flag("nerd_vpn_guard"), tr("nerd_vpn_guard_hint"))
    iface = QLineEdit(val("nerd_vpn_iface"))
    iface.setPlaceholderText(tr("nerd_vpn_iface_auto"))
    c.row(tr("nerd_vpn_iface"), iface, tr("nerd_vpn_iface_hint"), search="WireGuard wg0 tun0 VPN")
    vpn_lan = c.toggle(tr("nerd_vpn_lan"), n.flag("nerd_vpn_lan"))
    status = QLabel()
    status.setObjectName("SettingsHint")
    check = QPushButton(tr("nerd_vpn_check"))

    def show_vpn() -> None:
        # Seen to happen: "Checking…" first, then the answer with the time,
        # so a check that finds the same as before still says it looked.
        from PyQt6.QtCore import QTime, QTimer
        status.setText(tr("nerd_vpn_checking"))
        check.setEnabled(False)

        def answer() -> None:
            up = nerd.vpn_interfaces()
            at = QTime.currentTime().toString("HH:mm:ss")
            status.setText((tr("nerd_vpn_up", names=", ".join(up)) if up else tr("nerd_vpn_none"))
                           + f"  ({at})")
            check.setEnabled(True)
        QTimer.singleShot(250, answer)
    check.clicked.connect(show_vpn)
    row = QHBoxLayout()
    row.addWidget(status, 1)
    row.addWidget(check)
    c.layout_row(row, "VPN")
    if n.on:
        show_vpn()

    # -- channels and guide -----------------------------------------------------------
    c = page.card(tr("nerd_card_lists"))
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
    c.row(tr("nerd_picon_dir"), picon_row, tr("nerd_picon_dir_hint"), stretch_control=True,
          search="picon logo SNP")
    picon_over = c.toggle(tr("nerd_picon_override"), n.flag("nerd_picon_override"))
    udpxy = QLineEdit(val("nerd_udpxy"))
    udpxy.setPlaceholderText("http://192.168.1.1:4022")
    c.row("udpxy", udpxy, tr("nerd_udpxy_hint"), search="multicast udp rtp IPTV")
    adelay = c.toggle(tr("nerd_adelay"), n.flag("nerd_adelay_per_channel", True), tr("nerd_adelay_hint"))
    c.note(tr("nerd_epg_multi"))

    # -- the player -----------------------------------------------------------------
    c = page.card(tr("nerd_card_player"))
    spdif_on = set(val("nerd_spdif").split(","))
    spdif = [Chip(label, key in spdif_on) for key, label in SPDIF_LABELS]
    c.row(tr("nerd_spdif"), ChipGroups([("", spdif)]), tr("nerd_spdif_hint"), stretch_control=True,
          search="passthrough S/PDIF HDMI receiver AC3 DTS TrueHD")
    sync = combo([("", "nerd_sync_audio"), ("display-resample", "nerd_sync_resample"),
                  ("display-vdrop", "nerd_sync_vdrop")], val("nerd_video_sync"))
    c.row(tr("nerd_sync"), sync, tr("nerd_sync_hint"), search="video-sync display-resample")
    interp = c.toggle(tr("nerd_interpolation"), n.flag("nerd_interpolation"), tr("nerd_interpolation_hint"))
    probe = combo([("instant", "nerd_probe_instant"), ("", "nerd_probe_fast"),
                   ("thorough", "nerd_probe_thorough")], val("nerd_probe"))
    c.row(tr("nerd_probe"), probe, tr("nerd_probe_hint"))
    cache = combo([(str(mb), tr("nerd_cache_auto") if not mb else f"{mb} MB") for mb in CACHE_CHOICES],
                  str(n.num("nerd_demux_max_mb", 0)))
    c.row(tr("nerd_cache"), cache, tr("nerd_cache_hint"), search="demuxer-max-bytes")

    c = page.card(tr("nerd_card_mpv"))
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
    c.row(tr("nerd_mpv_dir"), dir_row, tr("nerd_mpv_dir_hint"), stretch_control=True,
          search="mpv.conf scripts lua")
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
    c.row(tr("nerd_shaders"), shader_col, tr("nerd_shaders_hint"), stretch_control=True,
          search="glsl FSRCNNX Anime4K upscaling")
    raw = text_box(val("nerd_mpv_options"), "deband=yes\nscale=ewa_lanczossharp\nvolume-max=150", 6)
    c.row(tr("nerd_mpv_options"), raw, tr("nerd_mpv_options_hint"), stretch_control=True,
          search="mpv.conf options")
    c.note(tr("nerd_restart_note"))

    # -- diagnostics --------------------------------------------------------------
    c = page.card(tr("nerd_card_diag"))
    level = combo([("info", "nerd_log_info"), ("debug", "nerd_log_debug")], val("nerd_log_level", "info"))
    c.row(tr("nerd_log_level"), level, tr("nerd_log_level_hint"), search="log debug")
    copy_btn = QPushButton(tr("nerd_copy_diag"))
    copied = QLabel("")
    copied.setObjectName("SettingsHint")

    def copy_diag() -> None:
        QGuiApplication.clipboard().setText(diagnostics(settings))
        copied.setText(tr("nerd_copied"))
    copy_btn.clicked.connect(copy_diag)
    c.layout_row(_hbox(copy_btn, copied), "diagnostics")

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
        put("nerd_overlay_more", "true" if overlay.isChecked() else "false")
        put("nerd_picon_dir", picon_dir.text().strip())
        put("nerd_picon_override", "true" if picon_over.isChecked() else "false")
        put("nerd_udpxy", udpxy.text().strip())
        put("nerd_adelay_per_channel", "true" if adelay.isChecked() else "false")
        return out

    # Off: the switch and what Nerd holds, nothing else. On: all of it.
    first = page.cards[gated_from - 1]
    first.shown_if(teaser_row, lambda: not switch.isChecked())
    for card in page.cards[gated_from:]:
        for row, _text in card.rows:
            prev = card._shown_if.get(id(row))
            card.shown_if(row, lambda prev=prev: switch.isChecked() and (prev is None or prev()))

    def flip(_on: bool) -> None:
        for card in page.cards:
            card.refresh()
    switch.toggled.connect(flip)

    shown = values()

    def commit(put: Callable[[str, Any], None]) -> None:
        """Only what was changed is written: a key never stored that still
        says what the page showed stays unstored (an untouched window
        changes nothing)."""
        for key, value in values().items():
            if settings.value(key) is None and value == shown[key]:
                continue
            put(key, value)

    return commit


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
            "nerd_vpn_lan", "nerd_proxy_player"}
    for key in sorted(safe):
        v = n.get(key)
        if v:
            lines.append(f"  {key} = {v}")
    for key in ("nerd_user_agent", "nerd_referer", "nerd_headers", "nerd_proxy", "nerd_mpv_options",
                "nerd_mpv_config_dir", "nerd_shaders", "nerd_vpn_iface"):
        if n.get(key):
            lines.append(f"  {key} = (set)")
    up = nerd.vpn_interfaces()
    lines.append(f"VPN interfaces up: {', '.join(up) if up else 'none'}")
    return "\n".join(lines)
