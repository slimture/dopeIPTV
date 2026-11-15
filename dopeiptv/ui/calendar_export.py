"""Export to calendar…: the reminders and scheduled recordings saved as an
.ics file, and - while the remote API or the phone remote runs - the
address a calendar app subscribes to. The window does the work
(mw_calendar)."""

from __future__ import annotations

import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from ..core.log import log
from ..i18n import tr
from .theme import P


def default_path() -> str:
    for sub in ("Documents", ""):
        d = os.path.join(os.path.expanduser("~"), sub) if sub else os.path.expanduser("~")
        if os.path.isdir(d):
            return os.path.join(d, "dopeIPTV.ics")
    return "dopeIPTV.ics"


def save_calendar(path: str, text: str) -> None:
    """*text* as the file, its CRLF line endings kept (OSError when not)."""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


class CalendarDialog(QDialog):
    WIDTH = 520

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.setWindowTitle(tr("cal_title"))
        self.setMinimumWidth(self.WIDTH)
        col = QVBoxLayout(self)
        col.setContentsMargins(24, 22, 24, 20)
        col.setSpacing(10)
        head = QLabel(tr("cal_title"))
        head.setStyleSheet(f"font-size:16px; font-weight:700; color:{P['text']};")
        col.addWidget(head)
        intro = QLabel(tr("cal_intro"))
        intro.setWordWrap(True)
        intro.setStyleSheet(f"font-size:13px; color:{P['text2']};")
        col.addWidget(intro)
        reminders, recordings = window._calendar_counts()
        self.counts = QLabel(tr("cal_counts", reminders=reminders, recordings=recordings))
        self.counts.setStyleSheet(f"font-size:13px; color:{P['text']};")
        col.addWidget(self.counts)
        save_row = QHBoxLayout()
        self.save_btn = QPushButton(tr("cal_save"), objectName="Primary")
        self.save_btn.clicked.connect(self._save)
        save_row.addWidget(self.save_btn)
        self.saved = QLabel()
        self.saved.setTextFormat(Qt.TextFormat.PlainText)
        self.saved.setWordWrap(True)
        self.saved.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        save_row.addWidget(self.saved, 1)
        col.addLayout(save_row)

        sub = QLabel(tr("cal_subscribe"))
        sub.setStyleSheet(f"font-size:14px; font-weight:700; color:{P['text']}; margin-top:8px;")
        col.addWidget(sub)
        self.fields: list[QLineEdit] = []
        addresses = window._calendar_addresses()
        hint = QLabel(tr("cal_subscribe_hint") if addresses else tr("cal_subscribe_off"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        col.addWidget(hint)
        for where, url in addresses:
            label = QLabel(where)
            label.setStyleSheet(f"font-size:12px; color:{P['text2']};")
            col.addWidget(label)
            row = QHBoxLayout()
            row.setSpacing(8)
            field = QLineEdit(url)
            field.setReadOnly(True)
            field.setCursorPosition(0)
            field.setAccessibleName(where)
            copy = QPushButton(tr("problem_copy"))
            copy.clicked.connect(lambda _c=False, f=field, b=copy: self._copy(f, b))
            row.addWidget(field, 1)
            row.addWidget(copy)
            col.addLayout(row)
            self.fields.append(field)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = QPushButton(tr("common_close"))
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        col.addLayout(buttons)

    def showEvent(self, event) -> None:         # noqa: N802 - Qt hook
        super().showEvent(event)
        from .phone_remote import fit_height
        fit_height(self)

    def _save(self) -> None:
        path, _f = QFileDialog.getSaveFileName(self, tr("cal_title"), default_path(),
                                               "iCalendar (*.ics)")
        if not path:
            return
        if not path.lower().endswith(".ics"):
            path += ".ics"
        self.write(path)

    def write(self, path: str) -> bool:
        try:
            save_calendar(path, self.window._calendar_text())
        except OSError as e:
            log.warning("calendar: could not save the file: %s", type(e).__name__)
            self.saved.setText(tr("cal_save_failed", error=str(e)))
            self.saved.setStyleSheet(f"font-size:12px; color:{P['error']};")
            return False
        self.saved.setText(tr("cal_saved", name=os.path.basename(path)))
        self.saved.setStyleSheet(f"font-size:12px; color:{P.get('ok', P['muted'])};")
        return True

    def _copy(self, field: QLineEdit, button: QPushButton) -> None:
        cb = QGuiApplication.clipboard()
        if cb is not None:
            cb.setText(field.text())
            button.setText(tr("problem_copied"))
