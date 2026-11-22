"""The connection settings sync brought and did not put in place, for the
person to look at (core/sync.py: hold_back, State.held, held_changes).

A playlist's proxy, extra headers, address rewrite, udpxy, catch-up
address, backup addresses, own DNS server and trusted certificate change
where its streams go, so sync never takes them by itself. This window shows
each as it is here and as the synced copy has it - words, never markup: a
playlist's name and every value are whatever another computer, or whoever
has the sync code, wrote - with Apply and Keep mine. It only shows and
asks; ui/mw_sync.py opens it (from the notices' card and from Settings >
Sync) and carries the answer out.
"""

from __future__ import annotations

import re

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout,
    QWidget,
)

from ..core.backup import RISKY_SOURCE
from ..i18n import tr
from .theme import P

#: The app's own words for each setting: its row on a source's page.
_LABELS = {"proxy": "nerd_proxy", "headers": "nerd_headers", "rewrite": "nerd_src_rewrite",
           "udpxy": "nerd_card_udpxy", "catchup_template": "nerd_src_catchup",
           "backup_servers": "nerd_src_mirrors", "dns_url": "nerd_card_dns",
           "cert_pin": "cert_row", "tls_unchecked": "src_tls_unchecked"}
# A place to break after each of these, and inside a long run without one:
# an address is one word, and would set the window's width.
_BREAK_AFTER = re.compile(r"([/?&=,;:|])(?=\S)")
_LONG_RUN = re.compile(r"(\S{24})(?=\S)")
_ZWSP = "​"


def label(setting: str) -> str:
    """A setting (its core/backup.RISKY_SOURCE name) in the app's words."""
    key = _LABELS.get(setting)
    return tr(key) if key else setting


def labels_known() -> bool:
    """Whether every setting that can wait has words here."""
    return all(key in _LABELS for key in RISKY_SOURCE)


def wrappable(text: str) -> str:
    """*text* with places to break a line where it has none of its own."""
    return _LONG_RUN.sub(r"\1" + _ZWSP, _BREAK_AFTER.sub(r"\1" + _ZWSP, text))


def _words(text: str, style: str, wrap: bool = True) -> QLabel:
    """A label that shows *text* as words - never read as markup."""
    lbl = QLabel(text)
    lbl.setTextFormat(Qt.TextFormat.PlainText)
    lbl.setWordWrap(wrap)
    lbl.setStyleSheet(style)
    return lbl


class SyncReviewDialog(QDialog):
    """*changes*: core/sync.held_changes() - per playlist, each setting as
    it is here and as the synced copy has it. ``choice`` afterwards:
    "apply", "keep", or "" for closed without an answer (they wait on)."""

    WIDTH = 600

    def __init__(self, parent: QWidget | None, changes: list) -> None:
        super().__init__(parent)
        self.choice = ""
        self.setWindowTitle(tr("sync_review_title"))
        self.setModal(True)
        self.setMinimumWidth(self.WIDTH)
        col = QVBoxLayout(self)
        col.setContentsMargins(24, 22, 24, 20)
        col.setSpacing(10)
        head = QLabel(tr("sync_review_title"))
        head.setStyleSheet(f"font-size:16px; font-weight:700; color:{P['text']};")
        col.addWidget(head)
        intro = QLabel(tr("sync_review_intro"))
        intro.setWordWrap(True)
        intro.setStyleSheet(f"font-size:13px; color:{P['text2']};")
        col.addWidget(intro)

        body = QWidget()
        rows = QVBoxLayout(body)
        rows.setContentsMargins(0, 0, 8, 0)
        rows.setSpacing(6)
        #: Every label that shows a playlist's name or a value.
        self.shown: list[QLabel] = []
        for change in changes:
            name = _words(change.name, f"font-size:14px; font-weight:700; color:{P['text']};")
            rows.addSpacing(6)
            rows.addWidget(name)
            self.shown.append(name)
            for setting, here, there in change.rows:
                rows.addWidget(_words(label(setting),
                                      f"font-size:13px; font-weight:600; color:{P['text']};"))
                grid = QGridLayout()
                grid.setContentsMargins(12, 0, 0, 4)
                grid.setHorizontalSpacing(12)
                grid.setVerticalSpacing(4)
                grid.setColumnStretch(1, 1)
                for i, (caption, value) in enumerate(((tr("sync_review_here"), here),
                                                      (tr("sync_review_new"), there))):
                    cap = _words(caption, f"font-size:12px; color:{P['muted']};", wrap=False)
                    cap.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeading)
                    val = _words(wrappable(value) if value else tr("sync_review_none"),
                                 f"font-size:13px; color:{P['text'] if value else P['muted']};")
                    val.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeading)
                    val.setAccessibleName(f"{label(setting)}, {caption}")
                    grid.addWidget(cap, i, 0)
                    grid.addWidget(val, i, 1)
                    self.shown.append(val)
                rows.addLayout(grid)
        rows.addStretch(1)
        # Many playlists, or long rules: they scroll, the answer stays on
        # the screen.
        box = QScrollArea()
        box.setWidget(body)
        box.setWidgetResizable(True)
        box.setFrameShape(QScrollArea.Shape.NoFrame)
        box.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        box.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget"
                          " { background:transparent; }")
        col.addWidget(box, 1)

        warn = QLabel(tr("sync_review_warn"))
        warn.setWordWrap(True)
        warn.setStyleSheet(f"font-size:12px; color:{P.get('warn', P['muted'])};")
        col.addWidget(warn)
        col.addSpacing(6)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.keep = QPushButton(tr("sync_held_keep"))
        self.apply = QPushButton(tr("common_apply"), objectName="Primary")
        self.keep.clicked.connect(lambda _c=False: self._answer("keep"))
        self.apply.clicked.connect(lambda _c=False: self._answer("apply"))
        buttons.addWidget(self.keep)
        buttons.addWidget(self.apply)
        col.addLayout(buttons)
        # Taking them is a deliberate click, never Enter's.
        self.apply.setAutoDefault(False)
        self.keep.setDefault(True)
        self.keep.setFocus()
        # A dialog does not ask its wrapped lines how tall they are at its
        # width: told here, or they were cut.
        inner = self.WIDTH - 48
        for lbl in (intro, warn):
            lbl.setMinimumHeight(lbl.heightForWidth(inner))
        body.setFixedWidth(inner)
        box.setFixedHeight(min(360, body.sizeHint().height() + 4))
        body.setMinimumWidth(0)
        body.setMaximumWidth(16777215)

    def _answer(self, choice: str) -> None:
        self.choice = choice
        self.accept()
