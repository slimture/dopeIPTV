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

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLineEdit, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWidget,
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


def build(page: SettingsPage, settings: Any, parent: QWidget,
          boxes: list[dict] | None = None,
          edit: Callable[[str], None] | None = None,
          add: Callable[[], None] | None = None,
          pages: dict[str, SettingsPage] | None = None,
          show_pages: Callable[[bool], None] | None = None,
          source: Callable[[], dict | None] | None = None,
          channels: Callable[[], list[dict]] | None = None,
          rec_dir: Callable[[], str] | None = None,
          playlists: Callable[[], tuple[list[dict], str | None]] | None = None) -> Callable[[Callable[[str, Any], None]], None]:
    """*page* is Nerd's own: the switch, how it works, stats, diagnostics.
    *pages* ("net", "boxes", "lists", "player", "rec") take the rest, shown by
    *show_pages* while Nerd is on; without them it all goes on *page*.
    *boxes*: the box sources, each a click (*edit*, by id) from its options.
    *source*: the active source, which the network checks ask; *channels*:
    fetches its channel list, off the window's thread (the picon check
    matches against it)."""
    n = nerd.Nerd(settings)
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

    # -- channels: guide -------------------------------------------------------------------
    c = at("lists").card(tr("nerd_card_guide"))
    c.note(tr("nerd_epg_multi"))
    c.note(tr("nerd_epg_map_note"))

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
        put("nerd_rec_template", template.text().strip())
        put("nerd_rec_post", post.text().strip())
        put("nerd_failover", "true" if fail_on.isChecked() else "false")
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

    def flip(on: bool) -> None:
        for card in page.cards:
            card.refresh()
        if show_pages is not None:
            show_pages(on)
        if on:
            start_checks()
    switch.toggled.connect(flip)
    if show_pages is not None:
        show_pages(n.on)
    if n.on:
        start_checks()

    shown = values()

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


def _how_block(items: list[tuple[str, str]]) -> QWidget:
    """How Nerd works, as one block: a short lead-in on the left of each
    line, what it means beside it - no hairlines between, one thing to read."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QGridLayout, QLabel

    from .theme import P
    box = QWidget()
    grid = QGridLayout(box)
    grid.setContentsMargins(0, 4, 0, 4)
    grid.setHorizontalSpacing(18)
    grid.setVerticalSpacing(10)
    for i, (title, body) in enumerate(items):
        head = QLabel(tr(title))
        head.setStyleSheet(f"color:{P['text']}; font-weight:600;")
        text = QLabel(tr(body))
        text.setWordWrap(True)
        text.setStyleSheet(f"color:{P['text2']};")
        grid.addWidget(head, i, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(text, i, 1)
    grid.setColumnStretch(1, 1)
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
    if n.on and n.flag("nerd_log_file"):
        lines.append("Log file: on (rotating, in the app's log folder)")
    return "\n".join(lines)
