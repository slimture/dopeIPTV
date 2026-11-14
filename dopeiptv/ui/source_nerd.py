"""Nerd options of one source, in the Add/Edit source dialog: its own user
agent, headers and proxy (else the Nerd page's), and what each kind of box
can be told - Enigma2's stream port, Tvheadend's stream profile,
HDHomeRun's transcoding. Only there while Nerd is on, and folded until
opened. Only what differs from the default is stored."""

from __future__ import annotations

from typing import Any

from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QLineEdit, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)

from ..core import nerd
from ..i18n import tr
from .nerd_settings import UA_CHOICES, text_box

TVH_PROFILES = ["", "pass", "matroska", "htsp", "audio", "webtv-h264-aac-mpegts",
                "webtv-h264-aac-matroska", "webtv-vp8-vorbis-webm"]
HDHR_TRANSCODE = ["", "heavy", "mobile", "internet720", "internet540", "internet480",
                  "internet360", "internet240"]
# (key, label, default) of each kind's switches.
SWITCHES = {
    "enigma2": [("stream_login", "nerd_src_stream_login", True), ("iptv_direct", "nerd_src_iptv_direct", True),
                ("radio", "nerd_src_radio", True), ("picons", "nerd_src_picons", True),
                ("recordings", "nerd_src_recordings", True)],
    "tvheadend": [("recordings", "nerd_src_recordings", True)],
    "hdhomerun": [("show_drm", "nerd_src_show_drm", False), ("cloud_guide", "nerd_src_cloud_guide", True),
                  ("hd_group", "nerd_src_hd_group", False)],
}


def _flag(options: dict, key: str, default: bool) -> bool:
    v = options.get(key)
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip():
        return v.strip().lower() in ("1", "true", "yes", "on")
    return default


class SourceNerdPanel(QWidget):
    """A fold-out under the source form: "Nerd options"."""

    # The rows every kind has; each kind's own come after.
    COMMON_ROWS = 8

    def __init__(self, kind: str, options: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._options = dict(options or {})
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        self.toggle = QPushButton(tr("nerd_src_title") + "  ▸")
        self.toggle.setCheckable(True)
        self.toggle.setFlat(True)
        self.toggle.setObjectName("NerdFold")
        self.toggle.toggled.connect(self._fold)
        lay.addWidget(self.toggle)
        self.body = QWidget()
        self.form = QFormLayout(self.body)
        self.form.setContentsMargins(0, 4, 0, 0)
        self.form.setSpacing(8)
        lay.addWidget(self.body)
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
        self.form.addRow(tr("nerd_user_agent"), self.ua)
        self.form.addRow("", self.ua_custom)
        self.form.addRow("Referer", self.referer)
        self.form.addRow(tr("nerd_headers"), self.headers)
        self.form.addRow(tr("nerd_proxy"), self.proxy)
        self.form.addRow("", self.proxy_custom)
        self.udpxy = QLineEdit(str(o.get("udpxy") or ""))
        self.udpxy.setPlaceholderText(tr("nerd_src_inherit"))
        self.form.addRow("udpxy", self.udpxy)
        self.epg_shift = QSpinBox()
        self.epg_shift.setRange(-720, 720)
        self.epg_shift.setSingleStep(15)
        self.epg_shift.setSuffix(" min")
        try:
            self.epg_shift.setValue(int(float(o.get("epg_shift") or 0)))
        except (TypeError, ValueError):
            self.epg_shift.setValue(0)
        self.form.addRow(tr("nerd_src_epg_shift"), self.epg_shift)
        # Each kind's own.
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        try:
            self.port.setValue(int(o.get("stream_port") or 8001))
        except (TypeError, ValueError):
            self.port.setValue(8001)
        self.port_row_label = tr("nerd_src_stream_port")
        self.profile = QComboBox()
        self.profile.setEditable(True)
        for p in TVH_PROFILES:
            self.profile.addItem(p or tr("nerd_src_server_default"), p)
        self._set_editable(self.profile, str(o.get("profile") or ""))
        self.transcode = QComboBox()
        for t in HDHR_TRANSCODE:
            self.transcode.addItem(t or tr("nerd_src_no_transcode"), t)
        self.transcode.setCurrentIndex(max(0, self.transcode.findData(str(o.get("transcode") or ""))))
        self.switches: dict[str, QCheckBox] = {}
        self._kind = ""
        self.ua.currentIndexChanged.connect(self._shown)
        self.proxy.currentIndexChanged.connect(self._shown)
        self.set_kind(kind)
        self.toggle.setChecked(False)
        self._fold(False)

    @staticmethod
    def _set_editable(box: QComboBox, value: str) -> None:
        i = box.findData(value)
        if i >= 0:
            box.setCurrentIndex(i)
        else:
            box.setEditText(value)

    def _fold(self, open_: bool) -> None:
        self.body.setVisible(open_)
        self.toggle.setText(tr("nerd_src_title") + ("  ▾" if open_ else "  ▸"))
        self._shown()

    def _shown(self, *_a) -> None:
        for w, show in ((self.ua_custom, self.ua.currentData() == "custom"),
                        (self.proxy_custom, self.proxy.currentData() == "custom")):
            w.setVisible(show)
            label = self.form.labelForField(w)
            if label is not None:
                label.setVisible(show)

    def set_kind(self, kind: str) -> None:
        """The rows the source's kind has (the common ones always)."""
        if kind == self._kind:
            return
        self._kind = kind
        while self.form.rowCount() > self.COMMON_ROWS:
            row = self.form.takeRow(self.COMMON_ROWS)
            for item in (row.labelItem, row.fieldItem):
                if item is not None and item.widget() is not None:
                    item.widget().setParent(None)
        self.switches = {}
        if kind == "enigma2":
            self.form.addRow(tr("nerd_src_stream_port"), self.port)
        elif kind == "tvheadend":
            self.form.addRow(tr("nerd_src_profile"), self.profile)
        elif kind == "hdhomerun":
            self.form.addRow(tr("nerd_src_transcode"), self.transcode)
        for key, label, default in SWITCHES.get(kind, []):
            box = QCheckBox(tr(label))
            box.setChecked(_flag(self._options, key, default))
            self.form.addRow("", box)
            self.switches[key] = box

    def values(self) -> dict[str, Any]:
        """The source's options: what was there, with these set (and a
        setting back at its default left out)."""
        out = {k: v for k, v in self._options.items()
               if k not in ("user_agent", "referer", "headers", "proxy", "stream_port",
                            "profile", "transcode", "udpxy", "epg_shift")
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
        if self._kind == "enigma2" and self.port.value() != 8001:
            out["stream_port"] = str(self.port.value())
        if self._kind == "tvheadend":
            prof = self.profile.currentData() if self.profile.currentText() == self.profile.itemText(
                self.profile.currentIndex()) else self.profile.currentText().strip()
            if prof:
                out["profile"] = str(prof)
        if self._kind == "hdhomerun" and self.transcode.currentData():
            out["transcode"] = self.transcode.currentData()
        for key, _label, default in SWITCHES.get(self._kind, []):
            on = self.switches[key].isChecked()
            if on != default:
                out[key] = on
        return out
