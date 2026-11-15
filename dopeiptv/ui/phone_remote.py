"""The phone remote's dialog (View > Phone remote…): off until it is turned
on here; then the code a phone's camera opens, the address beside it, a
new address on request and Stop. The window does the work (mw_phone)."""

from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QGuiApplication, QPainter
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from ..core import qr
from ..i18n import tr
from .theme import P

#: Light modules around the code, as the standard asks for.
QUIET = 4


def fit_height(dialog: QDialog) -> None:
    """As tall as its words at its width: wrapped labels are not counted
    by a plain size hint, and the rows ran into each other."""
    lay = dialog.layout()
    if lay is None:
        return
    lay.invalidate()
    lay.activate()
    w = max(dialog.width(), dialog.minimumWidth())
    h = lay.totalHeightForWidth(w) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
    h = max(h, lay.totalMinimumSize().height())
    dialog.setMinimumHeight(h)
    dialog.resize(w, h)


class QrView(QWidget):
    """A QR code on a white plate, each module a whole number of device
    pixels (no smoothing, no half pixels: a camera reads it at a glance)."""

    def __init__(self, parent: QWidget | None = None, side: int = 232) -> None:
        super().__init__(parent)
        self._code: qr.QrCode | None = None
        self._side = side
        self.setMinimumSize(side, side)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:      # noqa: N802 - Qt hook
        return QSize(self._side, self._side)

    def set_text(self, text: str) -> None:
        self._code = qr.encode(text, "M") if text else None
        self.setAccessibleName(text)
        self.update()

    @property
    def code(self) -> qr.QrCode | None:
        return self._code

    def module_px(self) -> int:
        """Device pixels per module at the widget's size now."""
        code = self._code
        if code is None:
            return 0
        n = code.size + 2 * QUIET
        side = int(min(self.width(), self.height()) * self.devicePixelRatioF())
        return max(1, side // n)

    def paintEvent(self, event) -> None:          # noqa: N802 - Qt hook
        code = self._code
        if code is None:
            return
        dpr = self.devicePixelRatioF()
        n = code.size + 2 * QUIET
        scale = self.module_px()
        side = scale * n / dpr
        x0 = round((self.width() - side) / 2 * dpr) / dpr
        y0 = round((self.height() - side) / 2 * dpr) / dpr
        unit = scale / dpr
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.fillRect(QRectF(x0, y0, side, side), QColor(Qt.GlobalColor.white))
        dark = QColor(Qt.GlobalColor.black)
        for y, row in enumerate(code.modules):
            x = 0
            while x < code.size:
                if not row[x]:
                    x += 1
                    continue
                run = x
                while run < code.size and row[run]:
                    run += 1
                p.fillRect(QRectF(x0 + (x + QUIET) * unit, y0 + (y + QUIET) * unit,
                                  (run - x) * unit, unit), dark)
                x = run
        p.end()


class PhoneRemoteDialog(QDialog):
    """Turn the phone remote on, show its code and address, make a new
    address, stop it. *window*: the MainWindow (mw_phone's methods)."""

    WIDTH = 460

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.setWindowTitle(tr("phone_title"))
        self.setMinimumWidth(self.WIDTH)
        col = QVBoxLayout(self)
        col.setContentsMargins(24, 22, 24, 20)
        col.setSpacing(10)
        head = QLabel(tr("phone_title"))
        head.setStyleSheet(f"font-size:16px; font-weight:700; color:{P['text']};")
        col.addWidget(head)
        self.intro = QLabel(tr("phone_intro") + "\n\n" + tr("phone_same_network"))
        self.intro.setWordWrap(True)
        self.intro.setStyleSheet(f"font-size:13px; color:{P['text2']};")
        col.addWidget(self.intro)

        self.scan = QLabel(tr("phone_scan"))
        self.scan.setWordWrap(True)
        self.scan.setStyleSheet(f"font-size:13px; color:{P['text2']};")
        col.addWidget(self.scan)
        self.code = QrView(self)
        col.addWidget(self.code, 0, Qt.AlignmentFlag.AlignHCenter)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.address = QLineEdit()
        self.address.setReadOnly(True)
        self.address.setAccessibleName(tr("phone_address"))
        self.copy = QPushButton(tr("problem_copy"))
        self.copy.clicked.connect(self._copy)
        row.addWidget(self.address, 1)
        row.addWidget(self.copy)
        self.address_row = QWidget()
        self.address_row.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        col.addWidget(self.address_row)
        self.note = QLabel(tr("phone_who"))
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        col.addWidget(self.note)
        self.warn = QLabel()
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet(f"font-size:12px; color:{P.get('warn', P['muted'])};")
        col.addWidget(self.warn)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setStyleSheet(f"font-size:12px; color:{P['error']};")
        col.addWidget(self.error)

        buttons = QHBoxLayout()
        self.on_btn = QPushButton(tr("phone_turn_on"), objectName="Primary")
        self.on_btn.clicked.connect(self._turn_on)
        self.new_btn = QPushButton(tr("phone_new_address"))
        self.new_btn.setToolTip(tr("phone_new_address_tip"))
        self.new_btn.clicked.connect(self._new_address)
        self.stop_btn = QPushButton(tr("phone_stop"))
        self.stop_btn.clicked.connect(self._stop)
        close = QPushButton(tr("common_close"))
        close.clicked.connect(self.accept)
        for b in (self.on_btn, self.new_btn, self.stop_btn):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(close)
        col.addLayout(buttons)
        self._copied = QTimer(self)
        self._copied.setSingleShot(True)
        self._copied.timeout.connect(self._copy_reset)
        self._refresh()

    # -- what it shows ------------------------------------------------------------------
    def _refresh(self) -> None:
        on = self.window.phone_remote_running()
        url = self.window.phone_remote_url() if on else ""
        self.code.set_text(url)
        self.address.setText(url)
        self.address.setCursorPosition(0)
        for w in (self.scan, self.code, self.address_row, self.note, self.new_btn, self.stop_btn):
            w.setVisible(on)
        self.intro.setVisible(not on)
        self.on_btn.setVisible(not on)
        local = on and "//127.0.0.1:" in url
        self.warn.setText(tr("phone_no_network") if local else "")
        self.warn.setVisible(local)
        err = "" if on else str(getattr(self.window, "_phone_error", "") or "")
        self.error.setText(tr("phone_cannot_start", error=err) if err else "")
        self.error.setVisible(bool(err))
        if self.isVisible():
            fit_height(self)

    def showEvent(self, event) -> None:         # noqa: N802 - Qt hook
        super().showEvent(event)
        fit_height(self)

    def _turn_on(self) -> None:
        self.window.phone_remote_start()
        self._refresh()

    def _new_address(self) -> None:
        self.window.phone_remote_new_token()
        self._refresh()

    def _stop(self) -> None:
        self.window.phone_remote_stop()
        self._refresh()

    def _copy(self) -> None:
        cb = QGuiApplication.clipboard()
        if cb is not None and self.address.text():
            cb.setText(self.address.text())
            self.copy.setText(tr("problem_copied"))
            self._copied.start(1500)

    def _copy_reset(self) -> None:
        self.copy.setText(tr("problem_copy"))
