"""Keyboard-shortcuts editor: rebind the app's shortcuts to your own keys.

Every rebindable action (defined by ``MainWindow.SHORTCUT_ACTIONS``) gets a row
with a ``QKeySequenceEdit``; the defaults are shown until the user changes them.
Saving writes each sequence to settings under ``shortcut/<id>`` and calls
``window.apply_shortcuts()`` so the live QShortcuts rebind immediately - no
restart needed. Escape and Delete are reserved and not listed.
"""

from __future__ import annotations

from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QDialog, QGridLayout, QHBoxLayout, QKeySequenceEdit, QLabel, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from ..i18n import tr


class ShortcutsDialog(QDialog):
    """Edit and persist the app's keyboard shortcuts."""

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.setWindowTitle(tr("sc_title"))
        self.resize(460, 560)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        hint = QLabel(tr("sc_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        lay.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        grid = QGridLayout(body)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(0, 1)

        self._edits: dict[str, tuple[QKeySequenceEdit, str]] = {}
        for row, (sid, default, label_key) in enumerate(
                window.SHORTCUT_ACTIONS):
            grid.addWidget(QLabel(tr(label_key)), row, 0)
            edit = QKeySequenceEdit()
            edit.setKeySequence(
                QKeySequence(window.shortcut_sequence(sid, default)))
            # Keep it to a single chord - a shortcut, not a macro.
            edit.setMaximumSequenceLength(1)
            grid.addWidget(edit, row, 1)
            self._edits[sid] = (edit, default)

        scroll.setWidget(body)
        lay.addWidget(scroll, 1)

        btns = QHBoxLayout()
        reset = QPushButton(tr("sc_reset"))
        reset.clicked.connect(self._reset_defaults)
        btns.addWidget(reset)
        btns.addStretch(1)
        cancel = QPushButton(tr("common_cancel"))
        cancel.clicked.connect(self.reject)
        save = QPushButton(tr("sc_save"), objectName="Primary")
        save.clicked.connect(self._save)
        save.setDefault(True)
        btns.addWidget(cancel)
        btns.addWidget(save)
        lay.addLayout(btns)

    def _reset_defaults(self) -> None:
        for _sid, (edit, default) in self._edits.items():
            edit.setKeySequence(QKeySequence(default))

    def _save(self) -> None:
        for sid, (edit, default) in self._edits.items():
            self.window.save_shortcut(sid, edit.keySequence().toString(),
                                      default)
        self.window.apply_shortcuts()
        self.accept()
