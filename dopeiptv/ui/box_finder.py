"""Boxes on the home network and media servers in the add-a-source forms:
what each kind is called and asks for, and the "Find on my network" button
that fills the form in from what answers (providers/lan_scan.py) - the
other networks Nerd lists too (a home network split in VLANs). Pressed
again while it searches, it stops; with nothing found, it says why a split
network may hide a box and what helps."""

from __future__ import annotations

import threading

from PyQt6 import sip
from PyQt6.QtCore import Qt, QThreadPool, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QWidget

from ..core.workers import run_async
from ..i18n import tr
from . import theme
from .theme import P

BOX_KINDS = ("enigma2", "tvheadend", "hdhomerun", "vdr", "dvbviewer", "nextpvr", "mythtv", "satip")
# Media servers (ui/media_signin.py signs in to them).
MEDIA_KINDS = ("jellyfin", "emby", "plex")
KIND_LABELS = {"enigma2": "playlist_kind_enigma2", "tvheadend": "playlist_kind_tvheadend",
               "hdhomerun": "playlist_kind_hdhomerun", "vdr": "playlist_kind_vdr",
               "dvbviewer": "playlist_kind_dvbviewer", "nextpvr": "playlist_kind_nextpvr",
               "mythtv": "playlist_kind_mythtv", "satip": "playlist_kind_satip",
               "jellyfin": "playlist_kind_jellyfin",
               "emby": "playlist_kind_emby", "plex": "playlist_kind_plex"}
SHORT_NAMES = {"enigma2": "Enigma2", "tvheadend": "Tvheadend", "hdhomerun": "HDHomeRun",
               "vdr": "VDR", "dvbviewer": "DVBViewer", "nextpvr": "NextPVR", "mythtv": "MythTV",
               "satip": "SAT>IP", "jellyfin": "Jellyfin", "emby": "Emby", "plex": "Plex"}
# Each with the port its kind listens on (a bare host gets it too; a SAT>IP
# server is found at its address, its RTSP port is 554).
PLACEHOLDERS = {"enigma2": "http://192.168.1.10", "tvheadend": "http://192.168.1.20:9981",
                "hdhomerun": "http://192.168.1.30", "vdr": "http://192.168.1.50:8002",
                "dvbviewer": "http://192.168.1.51:8089", "nextpvr": "http://192.168.1.52:8866",
                "mythtv": "http://192.168.1.53:6544", "satip": "192.168.178.1",
                "jellyfin": "http://192.168.1.40:8096",
                "emby": "http://192.168.1.41:8096"}
# Kinds that log in at all (an HDHomeRun never asks).
LOGIN_KINDS = ("enigma2", "tvheadend", "vdr", "dvbviewer", "nextpvr", "mythtv")
# Kinds whose login is a PIN alone (the password field, named so).
PIN_KINDS = ("nextpvr",)


def add_box_kinds(combo) -> None:
    for kind in BOX_KINDS:
        combo.addItem(tr(KIND_LABELS[kind]), kind)


def add_media_kinds(combo) -> None:
    for kind in MEDIA_KINDS:
        combo.addItem(tr(KIND_LABELS[kind]), kind)


def retranslate_box_kinds(combo) -> None:
    for kind in BOX_KINDS + MEDIA_KINDS:
        i = combo.findData(kind)
        if i >= 0:
            combo.setItemText(i, tr(KIND_LABELS[kind]))


def nothing_found_text() -> str:
    """What "Find on my network" says when nothing answered: type the
    address, and why a home network split in parts (VLANs) may hide a box -
    with what fixes it on the router."""
    return tr("playlist_nothing_found") + " " + tr("vlan_nothing_found")


class FindOnNetwork(QPushButton):
    """Looks for boxes and servers on the local network off the window's
    thread, then hands over the one picked ({"kind", "server", "name",
    "login"?, "id"?}). One found is taken at once; several are offered in a
    menu, those of the kinds in *wanted* first (the form's: boxes or media
    servers); none says so in *status*, a line the form places under the
    address."""

    picked = pyqtSignal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(tr("playlist_find_on_network"), parent)
        self.setObjectName("FindOnNetwork")
        # A quiet link under the address, not a second field beside it:
        # the address keeps its whole width.
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            f"QPushButton#FindOnNetwork {{ background: transparent; border: none;"
            f" color: {theme.ACCENT}; font-weight: 600; padding: 2px 0; text-align: left; }}"
            f"QPushButton#FindOnNetwork:hover {{ text-decoration: underline; }}"
            f"QPushButton#FindOnNetwork:disabled {{ color: {P['muted2']}; }}")
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        self.status.hide()
        # The row a form places under the address: the link, then what it
        # found (or did not).
        self.row = QWidget()
        lay = QHBoxLayout(self.row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(self)
        lay.addWidget(self.status, 1)
        self.clicked.connect(self._start)
        self._busy = False
        self._cancel = threading.Event()
        # A form closed while it searches: the search stops at its next step.
        self.destroyed.connect(self._cancel.set)
        self.wanted: tuple[str, ...] = ()

    def retranslate(self) -> None:
        if not self._busy:
            self.setText(tr("playlist_find_on_network"))

    def _start(self) -> None:
        if self._busy:
            # Pressed again: stop (what was found so far still comes back).
            self._cancel.set()
            self.setText(tr("playlist_search_stopping"))
            return
        from ..providers import lan_scan
        # Not disabled while it searches: a focused button that is, hands the
        # focus to the next field (the user name), as if asked to type there.
        self._busy = True
        self._cancel = threading.Event()
        self.destroyed.connect(self._cancel.set)
        self.setText(tr("playlist_searching_network"))
        self.setToolTip(tr("playlist_search_stop_tip"))
        self.status.hide()
        run_async(QThreadPool.globalInstance(), lan_scan.scan, self._done, self._failed,
                  cancel=self._cancel)

    def _reset(self) -> None:
        self._busy = False
        self.setText(tr("playlist_find_on_network"))
        self.setToolTip("")

    def _failed(self, _err=None) -> None:
        if sip.isdeleted(self):       # its form went while it searched
            return
        self._reset()
        self._say(nothing_found_text())

    def _say(self, text: str) -> None:
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def _done(self, found) -> None:
        if sip.isdeleted(self):
            return
        self._reset()
        found = list(found or [])
        if not found:
            self._say(nothing_found_text())
            return
        found.sort(key=lambda d: d.get("kind") not in self.wanted)
        if len(found) == 1:
            self.picked.emit(found[0])
            return
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        title = menu.addAction(tr("playlist_found_title"))
        title.setEnabled(False)
        for d in found:
            act = menu.addAction(f"{d['name']}  ·  {SHORT_NAMES.get(d['kind'], d['kind'])}")
            act.setToolTip(d["server"])
            act.triggered.connect(lambda _c=False, d=d: self.picked.emit(d))
        menu.popup(self.mapToGlobal(self.rect().bottomLeft()))
