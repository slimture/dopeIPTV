"""Boxes on the home network in the add-a-source forms: what each kind is
called and asks for, and the "Find on my network" button that fills the form
in from what answers (providers/lan_scan.py)."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QThreadPool, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QWidget

from ..core.workers import run_async
from ..i18n import tr
from . import theme
from .theme import P

BOX_KINDS = ("enigma2", "tvheadend", "hdhomerun")
KIND_LABELS = {"enigma2": "playlist_kind_enigma2", "tvheadend": "playlist_kind_tvheadend",
               "hdhomerun": "playlist_kind_hdhomerun"}
SHORT_NAMES = {"enigma2": "Enigma2", "tvheadend": "Tvheadend", "hdhomerun": "HDHomeRun"}
PLACEHOLDERS = {"enigma2": "http://192.168.1.10", "tvheadend": "http://192.168.1.20:9981",
                "hdhomerun": "http://192.168.1.30"}
# Kinds that log in at all (an HDHomeRun never asks).
LOGIN_KINDS = ("enigma2", "tvheadend")


def add_box_kinds(combo) -> None:
    for kind in BOX_KINDS:
        combo.addItem(tr(KIND_LABELS[kind]), kind)


def retranslate_box_kinds(combo) -> None:
    for kind in BOX_KINDS:
        i = combo.findData(kind)
        if i >= 0:
            combo.setItemText(i, tr(KIND_LABELS[kind]))


class FindOnNetwork(QPushButton):
    """Looks for boxes on the local network off the window's thread, then
    hands over the one picked ({"kind", "server", "name", "login"?}). One
    found is taken at once; several are offered in a menu; none says so in
    *status*, a line the form places under the address."""

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

    def retranslate(self) -> None:
        if not self._busy:
            self.setText(tr("playlist_find_on_network"))

    def _start(self) -> None:
        if self._busy:
            return
        from ..providers import lan_scan
        # Not disabled while it searches: a focused button that is, hands the
        # focus to the next field (the user name), as if asked to type there.
        self._busy = True
        self.setText(tr("playlist_searching_network"))
        self.status.hide()
        run_async(QThreadPool.globalInstance(), lan_scan.scan, self._done, self._failed)

    def _reset(self) -> None:
        self._busy = False
        self.setText(tr("playlist_find_on_network"))

    def _failed(self, _err=None) -> None:
        self._reset()
        self._say(tr("playlist_nothing_found"))

    def _say(self, text: str) -> None:
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def _done(self, found) -> None:
        self._reset()
        found = list(found or [])
        if not found:
            self._say(tr("playlist_nothing_found"))
            return
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
