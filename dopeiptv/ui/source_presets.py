"""Presets in the source dialog (with Nerd on): devices and servers that hand
out an M3U playlist and an XMLTV guide at known addresses - pick one, give
its host, and both are filled in."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QInputDialog, QMenu, QPushButton, QWidget

from ..i18n import tr
from . import theme
from .theme import P


@dataclass(frozen=True)
class Preset:
    label: str
    host: str          # the usual host ("" - ask)
    m3u: str           # "{host}" is replaced
    epg: str = ""


PRESETS = (
    Preset("Fritz!Box TV (HD)", "fritz.box", "http://{host}/dvb/m3u/tvhd.m3u"),
    Preset("Fritz!Box TV (SD)", "fritz.box", "http://{host}/dvb/m3u/tvsd.m3u"),
    Preset("Fritz!Box Radio", "fritz.box", "http://{host}/dvb/m3u/radio.m3u"),
    Preset("Channels DVR", "", "http://{host}:8089/devices/ANY/channels.m3u",
           "http://{host}:8089/devices/ANY/guide/xmltv"),
    Preset("Threadfin", "", "http://{host}:34400/m3u/threadfin.m3u",
           "http://{host}:34400/xmltv/threadfin.xml"),
    Preset("xTeVe", "", "http://{host}:34400/m3u/xteve.m3u", "http://{host}:34400/xmltv/xteve.xml"),
    Preset("Dispatcharr", "", "http://{host}:9191/output/m3u", "http://{host}:9191/output/epg"),
    Preset("ErsatzTV", "", "http://{host}:8409/iptv/channels.m3u", "http://{host}:8409/iptv/xmltv.xml"),
)


def fill(preset: Preset, host: str) -> dict:
    """The source a preset gives for *host*: kind, address, guide, name."""
    h = (host or preset.host).strip().removeprefix("http://").removeprefix("https://").strip("/")
    return {"kind": "m3u", "server": preset.m3u.format(host=h),
            "epg_url": preset.epg.format(host=h) if preset.epg else "", "name": preset.label}


class PresetButton(QPushButton):
    """A quiet link: "Fill in from a preset…", a menu of PRESETS, then the
    host (with the usual one already typed)."""

    def __init__(self, on_pick, parent: QWidget | None = None) -> None:
        super().__init__(tr("nerd_presets"), parent)
        self.setObjectName("PresetLink")
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            f"QPushButton#PresetLink {{ background: transparent; border: none; color: {theme.ACCENT};"
            f" font-weight: 600; padding: 2px 0; text-align: left; }}"
            f"QPushButton#PresetLink:hover {{ text-decoration: underline; }}"
            f"QPushButton#PresetLink:disabled {{ color: {P['muted2']}; }}")
        self._on_pick = on_pick
        self.clicked.connect(self._menu)

    def _menu(self) -> None:
        m = QMenu(self)
        for p in PRESETS:
            m.addAction(p.label, lambda _c=False, p=p: self._pick(p))
        m.popup(self.mapToGlobal(self.rect().bottomLeft()))

    def _pick(self, p: Preset) -> None:
        host, ok = QInputDialog.getText(self, p.label, tr("nerd_preset_host", name=p.label),
                                        text=p.host or "192.168.1.")
        if ok and host.strip():
            self._on_pick(fill(p, host))
