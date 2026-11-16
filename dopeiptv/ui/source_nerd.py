"""Nerd options of one source, on its page in Settings (ui/source_page.py
lays them out on its tabs): its own user agent, headers and proxy (else
the Nerd page's), rewrite rules for its stream addresses
(core/url_rewrite.py, ui/rewrite_rules.py), its backup addresses
(core/mirrors.py), its catch-up address (core/catchup.py), and what each
kind of box can be told - Enigma2's stream port and its box's transcoding
(port 8002), Tvheadend's stream profile, HDHomeRun's transcoding, VDR's
streamdev port, DVBViewer's stream port, each kind's switches. Only there
while Nerd is on. Only what differs from the default is stored."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlsplit

from PyQt6.QtWidgets import QComboBox, QLineEdit, QSpinBox, QWidget

from ..core import catchup, mirrors, nerd, nerd_check, url_rewrite
from ..core.log import log
from ..i18n import tr
from .nerd_check_line import CheckLine
from .nerd_settings import UA_CHOICES, text_box
from .rewrite_rules import RulesEditor
from .settings_ui import ToggleSwitch

TVH_PROFILES = ["", "pass", "matroska", "htsp", "audio", "webtv-h264-aac-mpegts",
                "webtv-h264-aac-matroska", "webtv-vp8-vorbis-webm"]
HDHR_TRANSCODE = ["", "heavy", "mobile", "internet720", "internet540", "internet480",
                  "internet360", "internet240"]
# An Enigma2 box's hardware transcoding (port 8002): off, or a step.
E2_TRANSCODE = ["", "low", "medium", "high", "full"]
# What a backup address of each kind looks like.
MIRROR_HINTS = {"xtream": "http://backup.example.com:8080",
                "m3u": "https://backup.example.com/playlist.m3u",
                "stalker": "http://backup.example.com/c/"}
# A media server's films and episodes: the file itself, or its HLS at a
# bitrate (Mbit/s).
MS_QUALITY = ["", "40", "20", "8", "4", "2"]
MEDIA_KINDS = ("jellyfin", "emby", "plex")
# (mode, label) of the catch-up modes; a name of a product stays as it is.
CATCHUP_MODES = [("auto", "nerd_cu_auto"), ("default", "nerd_cu_default"),
                 ("append", "nerd_cu_append"), ("shift", "nerd_cu_shift"),
                 ("flussonic", "Flussonic")]
# The live address a catch-up template is shown on before a channel of the
# source is known (Test finds one).
EXAMPLE_LIVE = "http://example.com/channel/index.m3u8"
# (key, label, default) of each kind's switches.
SWITCHES = {
    "enigma2": [("stream_login", "nerd_src_stream_login", True), ("iptv_direct", "nerd_src_iptv_direct", True),
                ("radio", "nerd_src_radio", True), ("picons", "nerd_src_picons", True),
                ("recordings", "nerd_src_recordings", True),
                ("transcode_more", "nerd_src_transcode_more", False)],
    "tvheadend": [("recordings", "nerd_src_recordings", True)],
    "hdhomerun": [("show_drm", "nerd_src_show_drm", False), ("cloud_guide", "nerd_src_cloud_guide", True),
                  ("hd_group", "nerd_src_hd_group", False)],
    "vdr": [("radio", "nerd_src_radio_channels", True), ("recordings", "nerd_src_recordings", True)],
    "dvbviewer": [("favourites", "nerd_src_fav_groups", True), ("recordings", "nerd_src_recordings", True)],
    "nextpvr": [("radio", "nerd_src_radio_channels", True), ("recordings", "nerd_src_recordings", True)],
    "mythtv": [("recordings", "nerd_src_recordings", True)],
}
# Ports a kind streams from, with their defaults: VDR's streamdev-server,
# the DVBViewer Media Server's HTTP/UPnP side.
STREAMDEV_PORT = 3000
DVB_STREAM_PORT = 7522


def _flag(options: dict, key: str, default: bool) -> bool:
    v = options.get(key)
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip():
        return v.strip().lower() in ("1", "true", "yes", "on")
    return default


class SourceNerdPanel(QWidget):
    """The source's own Nerd options: their controls and what they mean,
    without a layout - the source's page puts each on its tab. Never shown
    itself (it only holds them)."""

    def __init__(self, kind: str, options: dict, parent: QWidget | None = None,
                 source: Callable[[], dict] | None = None) -> None:
        """*source*: the source as the dialog has it now (kind, address,
        login, these options) - what the catch-up Test asks."""
        super().__init__(parent)
        self._options = dict(options or {})
        self._source = source
        self._kind = ""
        # A channel of the source, once Test has found one: the catch-up
        # line shows its address from then on (until the source changes).
        self._cu_channel: dict | None = None
        self._cu_gen = 0
        self.hide()
        # The source's own DNS, IP version and trusted certificate
        # (ui/source_net.py).
        from .source_net import SourceNetOptions
        self.net = SourceNetOptions(self._options, self._source_now, self, proxy=self._proxy_now)
        o = self._options
        self.ua = QComboBox()
        self.ua.addItem(tr("nerd_src_inherit"), "")
        for value, label in UA_CHOICES[1:]:
            self.ua.addItem(tr(label) if label.startswith("nerd_") else label, value)
        stored = str(o.get("user_agent") or "")
        key = stored if (stored in nerd.UA_PRESETS or not stored) else "custom"
        self.ua.setCurrentIndex(max(0, self.ua.findData(key)))
        self.ua_custom = QLineEdit(stored if key == "custom" else "")
        self.ua_custom.setPlaceholderText("MyPlayer/1.0")
        self.referer = QLineEdit(str(o.get("referer") or ""))
        self.referer.setPlaceholderText(tr("nerd_src_inherit"))
        self.headers = text_box(str(o.get("headers") or ""), "X-Token: …", 2)
        self.proxy = QComboBox()
        for value, label in (("", "nerd_src_inherit"), ("direct", "nerd_src_direct"),
                             ("custom", "nerd_ua_custom")):
            self.proxy.addItem(tr(label), value)
        px = str(o.get("proxy") or "")
        self.proxy.setCurrentIndex(max(0, self.proxy.findData(
            px if px in ("", "direct") else "custom")))
        self.proxy_custom = QLineEdit(px if px not in ("", "direct") else "")
        self.proxy_custom.setPlaceholderText("http://10.8.0.1:3128")
        self.udpxy = QLineEdit(str(o.get("udpxy") or ""))
        self.udpxy.setPlaceholderText(tr("nerd_src_inherit"))
        self.epg_shift = QSpinBox()
        self.epg_shift.setRange(-720, 720)
        self.epg_shift.setSingleStep(15)
        self.epg_shift.setSuffix(" min")
        try:
            self.epg_shift.setValue(int(float(o.get("epg_shift") or 0)))
        except (TypeError, ValueError):
            self.epg_shift.setValue(0)
        # Catch-up: for the kinds whose channels have an archive.
        self.cu_mode = QComboBox()
        for value, label in CATCHUP_MODES:
            self.cu_mode.addItem(tr(label) if label.startswith("nerd_") else label, value)
        self.cu_mode.setCurrentIndex(max(0, self.cu_mode.findData(
            catchup.normal_mode(o.get("catchup_mode")))))
        self.cu_template = QLineEdit(str(o.get("catchup_template") or ""))
        self.cu_template.setPlaceholderText("?utc={utc}&lutc={lutc}")
        tip = tr("nerd_src_catchup_hint") + "\n" + tr("nerd_src_catchup_fields")
        self.cu_mode.setToolTip(tip)
        self.cu_template.setToolTip(tip)
        self.cu_line = CheckLine(self._cu_job, tr("nerd_ck_title_catchup"), test=True,
                                 preview=self._cu_preview)
        self.cu_template.textChanged.connect(self.cu_line.changed)
        self.cu_mode.currentIndexChanged.connect(self.cu_line.changed)
        self.cu_mode.currentIndexChanged.connect(self._cu_mode_shown)
        self._cu_mode_shown()
        # Each kind's own.
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        try:
            self.port.setValue(int(o.get("stream_port") or 8001))
        except (TypeError, ValueError):
            self.port.setValue(8001)
        self.port_row_label = tr("nerd_src_stream_port")
        self.streamdev_port = self._port_box(o.get("streamdev_port"), STREAMDEV_PORT)
        self.dvb_port = self._port_box(o.get("stream_port"), DVB_STREAM_PORT)
        self.profile = QComboBox()
        self.profile.setEditable(True)
        for p in TVH_PROFILES:
            self.profile.addItem(p or tr("nerd_src_server_default"), p)
        self._set_editable(self.profile, str(o.get("profile") or ""))
        self.transcode = QComboBox()
        for t in HDHR_TRANSCODE:
            self.transcode.addItem(t or tr("nerd_src_no_transcode"), t)
        self.transcode.setCurrentIndex(max(0, self.transcode.findData(str(o.get("transcode") or ""))))
        self.e2_transcode = QComboBox()
        for t in E2_TRANSCODE:
            self.e2_transcode.addItem(self._e2_step_label(t), t)
        self.e2_transcode.setCurrentIndex(max(0, self.e2_transcode.findData(str(o.get("transcode") or ""))))
        self.e2_transcode.setToolTip(tr("nerd_src_e2_transcode_hint"))
        self.ms_quality = QComboBox()
        for q in MS_QUALITY:
            self.ms_quality.addItem(tr("ms_quality_mbps", n=q) if q else tr("ms_quality_original"), q)
        self.ms_quality.setCurrentIndex(max(0, self.ms_quality.findData(str(o.get("ms_quality") or ""))))
        # Address rewrite: every kind's addresses (after the kind's own
        # widgets, which the example address reads).
        self._rw_channel: dict | None = None
        self._rw_gen = 0
        self.rewrite = RulesEditor(o.get(url_rewrite.KEY) or "")
        self.rewrite.setToolTip(tr("nerd_src_rewrite_hint"))
        self.rw_line = CheckLine(self._rw_job, tr("nerd_ck_title_rewrite"), test=True,
                                 preview=self._rw_preview)
        self.rewrite.edited.connect(self.rw_line.changed)
        # Backup addresses: for the kinds whose clients take them.
        self.mirrors_box = text_box(str(o.get(mirrors.KEY) or ""), MIRROR_HINTS["xtream"], 3)
        self.mirrors_box.setToolTip(tr("nerd_src_mirrors_hint"))
        self.mr_line = CheckLine(self._mr_job, tr("nerd_ck_title_mirrors"), test=True,
                                 preview=self._mr_preview, test_text=tr("nerd_mr_test"))
        self.mirrors_box.textChanged.connect(self.mr_line.changed)
        # Each kind's switches, made once: the kind's own are the ones read.
        self.kind_switches: dict[str, dict[str, ToggleSwitch]] = {}
        for k, rows in SWITCHES.items():
            self.kind_switches[k] = {}
            for key, label, default in rows:
                box = ToggleSwitch(_flag(o, key, default))
                box.setAccessibleName(tr(label))
                self.kind_switches[k][key] = box
        self.switches: dict[str, ToggleSwitch] = {}
        self._kind = ""
        self.ua.currentIndexChanged.connect(self._shown)
        self.proxy.currentIndexChanged.connect(self._shown)
        self.set_kind(kind)
        self._shown()

    @staticmethod
    def _port_box(stored: Any, default: int) -> QSpinBox:
        box = QSpinBox()
        box.setRange(1, 65535)
        try:
            box.setValue(int(stored or default))
        except (TypeError, ValueError):
            box.setValue(default)
        return box

    @staticmethod
    def _e2_step_label(step: str) -> str:
        """An Enigma2 transcoding step as the list shows it: its name, what
        it asks the box for."""
        from ..providers.enigma2 import TRANSCODE_STEPS
        if step not in TRANSCODE_STEPS:
            return tr("nerd_src_no_transcode")
        rate, width, height = TRANSCODE_STEPS[step]
        return f"{tr('e2_tc_' + step)} · {rate / 1e6:g} Mbit/s · {width}×{height}"

    @staticmethod
    def _set_editable(box: QComboBox, value: str) -> None:
        i = box.findData(value)
        if i >= 0:
            box.setCurrentIndex(i)
        else:
            box.setEditText(value)

    def _shown(self, *_a) -> None:
        self.ua_custom.setVisible(self.ua.currentData() == "custom")
        self.proxy_custom.setVisible(self.proxy.currentData() == "custom")

    def set_kind(self, kind: str) -> None:
        """The source's kind: which of its switches count, and what its
        backup and catch-up lines say (which rows show is the page's)."""
        if kind == self._kind:
            return
        self._kind = kind
        self._rw_source_changed()
        self.switches = self.kind_switches.get(kind, {})
        if kind in mirrors.KINDS:
            self.mirrors_box.setPlaceholderText(MIRROR_HINTS.get(kind, MIRROR_HINTS["xtream"]))
            self.mr_line.show_preview()
        if kind in catchup.KINDS:
            self.source_changed()
            self.cu_line.show_preview()

    def values(self) -> dict[str, Any]:
        """The source's options: what was there, with these set (and a
        setting back at its default left out)."""
        out = {k: v for k, v in self._options.items()
               if k not in ("user_agent", "referer", "headers", "proxy", "stream_port",
                            "streamdev_port",
                            "profile", "transcode", "udpxy", "epg_shift", "ms_quality",
                            "catchup_template", "catchup_mode", url_rewrite.KEY,
                            mirrors.KEY)
               and k not in {key for sw in SWITCHES.values() for key, _l, _d in sw}}
        ua = self.ua.currentData()
        ua = self.ua_custom.text().strip() if ua == "custom" else ua
        if ua:
            out["user_agent"] = ua
        if self.referer.text().strip():
            out["referer"] = self.referer.text().strip()
        if self.headers.toPlainText().strip():
            out["headers"] = self.headers.toPlainText().strip()
        px = self.proxy.currentData()
        px = self.proxy_custom.text().strip() if px == "custom" else px
        if px:
            out["proxy"] = px
        if self.udpxy.text().strip():
            out["udpxy"] = self.udpxy.text().strip()
        if self.epg_shift.value():
            out["epg_shift"] = str(self.epg_shift.value())
        rules = self.rewrite.stored()
        if rules:
            out[url_rewrite.KEY] = rules
        if self._kind in mirrors.KINDS and self.mirrors_box.toPlainText().strip():
            out[mirrors.KEY] = self.mirrors_box.toPlainText().strip()
        if self._kind in catchup.KINDS:
            if self.cu_template.text().strip():
                out["catchup_template"] = self.cu_template.text().strip()
            if self.cu_mode.currentData() != "auto":
                out["catchup_mode"] = self.cu_mode.currentData()
        if self._kind == "enigma2" and self.port.value() != 8001:
            out["stream_port"] = str(self.port.value())
        if self._kind == "vdr" and self.streamdev_port.value() != STREAMDEV_PORT:
            out["streamdev_port"] = str(self.streamdev_port.value())
        if self._kind == "dvbviewer" and self.dvb_port.value() != DVB_STREAM_PORT:
            out["stream_port"] = str(self.dvb_port.value())
        if self._kind == "tvheadend":
            prof = self.profile.currentData() if self.profile.currentText() == self.profile.itemText(
                self.profile.currentIndex()) else self.profile.currentText().strip()
            if prof:
                out["profile"] = str(prof)
        if self._kind == "hdhomerun" and self.transcode.currentData():
            out["transcode"] = self.transcode.currentData()
        if self._kind == "enigma2" and self.e2_transcode.currentData():
            out["transcode"] = self.e2_transcode.currentData()
        if self._kind in MEDIA_KINDS and self.ms_quality.currentData():
            out["ms_quality"] = self.ms_quality.currentData()
        for key, _label, default in SWITCHES.get(self._kind, []):
            on = self.switches[key].isChecked()
            if on != default:
                out[key] = on
        return self._with_net(out)

    # -- the source's own network (ui/source_net.py) -------------------------------

    def _with_net(self, out: dict[str, Any]) -> dict[str, Any]:
        from .source_net import KEYS
        for key in KEYS:
            out.pop(key, None)
        out.update(self.net.values())
        return out

    # -- the connection test --------------------------------------------------------

    def test_source(self) -> dict:
        """The source as it would be saved now: what Test connection tries."""
        src = dict(self._source_now(), options=self.values())
        src["kind"] = src.get("kind") or self._kind
        return src

    # -- backup addresses -------------------------------------------------------------

    def _mr_now(self) -> tuple[str, str, str]:
        """(kind, the source's own address, the one in use) as the form has
        them."""
        s = self._source_now()
        kind = str(s.get("kind") or self._kind)
        in_use = mirrors.remembered(nerd.current().s, str(s.get("id") or ""))
        return kind, str(s.get("server") or ""), in_use

    def _mr_preview(self) -> nerd_check.Check:
        kind, primary, in_use = self._mr_now()
        return mirrors.check_list(self.mirrors_box.toPlainText(), kind, primary, in_use)

    def mirrors_ok(self) -> nerd_check.Check | None:
        """None when every line can be an address; else what the line says
        about the first that cannot (the dialog refuses to close on it)."""
        if self._kind not in mirrors.KINDS:
            return None
        shown = self._mr_preview()
        return shown if shown.state == nerd_check.FAIL else None

    def _mr_job(self) -> Callable[[], nerd_check.Check] | None:
        """Test: the source's own address and each backup one, asked as the
        client asks them first, with the source's user agent, headers and
        proxy."""
        shown = self._mr_preview()
        if shown.state == nerd_check.FAIL:
            return lambda: shown
        kind, primary, in_use = self._mr_now()
        if not primary.strip():
            return None
        backups = [a for a in mirrors.parse(self.mirrors_box.toPlainText(), kind)
                   if not mirrors.same(a, primary)]
        if not backups:
            return None
        addrs = [mirrors.normalize(primary)] + backups
        src = dict(self._source_now(), kind=kind)
        opts = dict(self.values(), _kind=kind)
        n = nerd.current()
        ua, hdrs = n.user_agent(opts), n.headers(opts)
        px = n.proxy(opts, primary)
        secrets = catchup.secrets_of(src)

        def run() -> nerd_check.Check:
            return mirrors.check_each(addrs, kind, src, in_use, ua, hdrs, px, secrets)
        return run

    def _proxy_now(self) -> str:
        """The proxy this source's requests take (its own, else the Nerd
        page's): with one, the proxy looks names up."""
        px = self.proxy.currentData()
        px = self.proxy_custom.text().strip() if px == "custom" else px
        return nerd.current().proxy({"proxy": px or "", "_kind": self._kind})

    # -- catch-up ----------------------------------------------------------------

    def _cu_mode_shown(self, *_a) -> None:
        # shift and flussonic make the address from the channel's own.
        self.cu_template.setEnabled(self.cu_mode.currentData() not in ("shift", "flussonic"))

    def _source_now(self) -> dict:
        try:
            return dict(self._source() or {}) if self._source is not None else {}
        except Exception:
            return {}

    def source_changed(self, *_a) -> None:
        """The dialog's kind, address or login changed: a channel found for
        the old one no longer stands for this source."""
        self._cu_channel = None
        self._cu_gen += 1
        self.cu_line.changed()
        self._rw_source_changed()
        self.net.source_changed()

    def _cu_example(self) -> dict:
        """A made-up channel of this source, until Test has found a real one:
        an Xtream account's addresses all have one shape."""
        s = self._source_now()
        if str(s.get("kind") or self._kind) != "xtream":
            return {"live_url": EXAMPLE_LIVE, "_example": True}
        server = str(s.get("server") or "").strip().rstrip("/") or "http://example.com:8080"
        if "://" not in server:
            server = "http://" + server
        ext = "m3u8" if nerd.current().get("stream_format", "ts") == "m3u8" else "ts"
        user, pw = s.get("username") or "user", s.get("password") or "pass"
        return {"live_url": f"{server}/live/{user}/{pw}/1.{ext}", "_example": True}

    def _cu_preview(self) -> nerd_check.Check:
        ch = self._cu_channel or self._cu_example()
        return catchup.check_template(
            self.cu_template.text(), str(self.cu_mode.currentData() or "auto"), ch["live_url"],
            channel=str(ch.get("name") or ""), example=bool(ch.get("_example")),
            catchup_id=str(ch.get("catchup_id") or ""),
            channel_source=str(ch.get("catchup_source") or ""),
            secrets=catchup.secrets_of(self._source_now()))

    def _cu_job(self) -> Callable[[], nerd_check.Check] | None:
        """Test: a programme two hours ago on a channel of this source, asked
        of its archive with the source's user agent, headers and proxy."""
        template, mode = self.cu_template.text(), str(self.cu_mode.currentData() or "auto")
        if not template.strip() and mode == "auto":
            return None
        shown = self._cu_preview()
        if shown.state == nerd_check.FAIL:
            return lambda: shown
        src = dict(self._source_now(), options=self.values())
        src["kind"] = src.get("kind") or self._kind
        opts = dict(src["options"], _kind=src["kind"])
        n = nerd.current()
        ua, hdrs, secrets = n.user_agent(opts), n.headers(opts), catchup.secrets_of(src)
        fmt = str(n.get("stream_format", "ts") or "ts")
        known, gen = self._cu_channel, self._cu_gen

        def run() -> nerd_check.Check:
            ch = known or sample_channel(src, fmt)
            if ch is None:
                return nerd_check.Check(nerd_check.FAIL, "nerd_ck_cu_no_channel")
            if gen == self._cu_gen:         # still the source it was found for
                self._cu_channel = ch
            now = datetime.now(timezone.utc)
            start, end = catchup.sample_programme(now)
            cid, csrc = str(ch.get("catchup_id") or ""), str(ch.get("catchup_source") or "")
            url = catchup.archive_url(ch["live_url"], start, end, template, mode, now, cid, csrc)
            if url and catchup.whole_address(template, mode, csrc):
                url = url_rewrite.rewrite(url, opts)        # as the window plays it
            if url is None:
                return catchup.check_template(template, mode, ch["live_url"], str(ch.get("name") or ""),
                                              catchup_id=cid, channel_source=csrc, secrets=secrets,
                                              now=now)
            return catchup.check_archive(url, ua, hdrs, n.proxy(opts, url), secrets=secrets,
                                         channel=str(ch.get("name") or ""))
        return run if self._may_read(src) else _not_read

    def _may_read(self, src: dict) -> bool:
        """A Test that reads a stream of *src*: asked first by the window
        when that is one connection too many (ui/mw_connections.py)."""
        from .mw_connections import may_read_stream
        return may_read_stream(self, str(src.get("id") or ""))

    # -- address rewrite ----------------------------------------------------------

    def _rw_source_changed(self) -> None:
        """Another source: a channel Test found no longer stands for it."""
        self._rw_channel = None
        self._rw_gen += 1
        self.rw_line.changed()

    def _rw_example(self) -> dict:
        """A made-up channel address of this source's kind, until Test has
        found a real one."""
        s = self._source_now()
        kind = str(s.get("kind") or self._kind)
        if kind == "xtream":
            return self._cu_example()
        server = str(s.get("server") or "").strip().rstrip("/")
        if server and "://" not in server:
            server = "http://" + server
        try:
            parts = urlsplit(server)
            host, port = parts.hostname or "", parts.port
        except ValueError:
            host, port = "", None
        if not host:
            return {"live_url": EXAMPLE_LIVE, "_example": True}
        if ":" in host:
            host = f"[{host}]"
        user, pw = str(s.get("username") or ""), str(s.get("password") or "")
        login = f"{user}:{pw}@" if user and kind in ("enigma2", "tvheadend", "vdr", "dvbviewer") else ""
        origin = f"{parts.scheme}://{host}{f':{port}' if port else ''}"
        if kind == "enigma2":
            url = f"http://{login}{host}:{self.port.value()}/1:0:19:283D:3FB:1:C00000:0:0:0:"
        elif kind == "tvheadend":
            box = self.profile
            prof = str((box.currentData() if box.currentText() == box.itemText(box.currentIndex())
                        else box.currentText().strip()) or "")
            url = (origin.replace("://", "://" + login, 1) + "/stream/channel/0b1c2d3e4f"
                   + (f"?profile={prof}" if prof else ""))
        elif kind == "hdhomerun":
            url = f"http://{host}:5004/auto/v5.1"
        elif kind == "vdr":
            url = f"http://{login}{host}:{self.streamdev_port.value()}/TS/S19.2E-1-1019-10301"
        elif kind == "dvbviewer":
            url = f"http://{login}{host}:{self.dvb_port.value()}/upnp/channelstream/1.ts"
        elif kind == "nextpvr":
            url = origin + "/live?channeloid=7179&client=dopeIPTV&sid=…"
        elif kind == "satip":
            url = (f"rtsp://{host}:554/?src=1&freq=11494&pol=h&ro=0.35&msys=dvbs2&mtype=8psk"
                   "&plts=on&sr=22000&fec=23&pids=0,5100,5101,5102,5104")
        else:
            url = origin + "/channel/1.ts"
        return {"live_url": url, "_example": True}

    def _rw_preview(self) -> nerd_check.Check:
        ch = self._rw_channel or self._rw_example()
        return url_rewrite.check(self.rewrite.rules(), ch["live_url"],
                                 channel=str(ch.get("name") or ""),
                                 example=bool(ch.get("_example")),
                                 secrets=catchup.secrets_of(self._source_now()))

    def rules_ok(self) -> nerd_check.Check | None:
        """None when every rule can work; else what the line says about the
        first that cannot (the dialog refuses to close on it)."""
        shown = self._rw_preview()
        return shown if shown.state == nerd_check.FAIL else None

    def _rw_job(self) -> Callable[[], nerd_check.Check] | None:
        """Test: a channel of this source (its address as the source gives
        it), through the rules, asked with the source's user agent, headers
        and proxy - does a stream come back?"""
        rules = self.rewrite.rules()
        if not rules:
            return None
        shown = self._rw_preview()
        if shown.state == nerd_check.FAIL:
            return lambda: shown
        own = {k: v for k, v in self.values().items() if k != url_rewrite.KEY}
        src = dict(self._source_now(), options=own)
        src["kind"] = src.get("kind") or self._kind
        opts = dict(own, _kind=src["kind"])
        n = nerd.current()
        ua, hdrs, secrets = n.user_agent(opts), n.headers(opts), catchup.secrets_of(src)
        fmt = str(n.get("stream_format", "ts") or "ts")
        known, gen = self._rw_channel, self._rw_gen

        def run() -> nerd_check.Check:
            ch = known or sample_channel(src, fmt)
            if ch is None:
                return nerd_check.Check(nerd_check.FAIL, "nerd_ck_cu_no_channel")
            if gen == self._rw_gen:         # still the source it was found for
                self._rw_channel = ch
            name = str(ch.get("name") or "")
            after = url_rewrite.apply(ch["live_url"], rules)
            if after == ch["live_url"] or "://" not in after:
                return url_rewrite.check(rules, ch["live_url"], channel=name, secrets=secrets)
            return url_rewrite.check_address(after, ch["live_url"], ua, hdrs, n.proxy(opts, after),
                                             secrets=secrets, channel=name)
        return run if self._may_read(src) else _not_read


def _not_read() -> nerd_check.Check:
    """A Test's stream not read: one more connection was not wanted."""
    return nerd_check.Check(nerd_check.INFO, "ct_skip_conn")


def sample_channel(source: dict, fmt: str = "ts") -> dict | None:
    """(worker thread) A channel of *source* to try its catch-up on: one
    with an archive, else the first - its name and live address. None when
    the source's list cannot be had."""
    from ..providers.client import make_client
    try:
        client = make_client(source)
        rows = [r for r in (client.live_streams(None) or []) if r.get("stream_id") is not None]
    except Exception as e:
        log.info("nerd: no channel list for the catch-up test: %s", e)
        return None

    def archived(row: dict) -> bool:
        try:
            return int(row.get("tv_archive") or 0) > 0
        except (TypeError, ValueError):
            return False
    row = next((r for r in rows if archived(r)), rows[0] if rows else None)
    if row is None:
        return None
    try:
        live = client.live_url(row["stream_id"], fmt) or ""
    except Exception as e:
        log.info("nerd: no address for the catch-up test's channel: %s", e)
        return None
    if not live:
        return None
    return {"name": str(row.get("name") or ""), "live_url": live,
            "catchup_id": str(row.get("catchup_id") or ""),
            "catchup_source": str(row.get("catchup_source") or "")}
