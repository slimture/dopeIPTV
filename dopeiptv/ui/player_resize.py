"""Resize the docked video without moving its OpenGL surface between widgets."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from .theme import P


class PlayerResizeHandle(QWidget):
    MIN_VIDEO_HEIGHT = 120

    def __init__(self, player, info, settings, parent) -> None:
        super().__init__(parent)
        self.player = player
        self.info = info
        self.settings = settings
        self._drag = None
        try:
            self._preferred = max(self.MIN_VIDEO_HEIGHT, min(2160, int(
                settings.value("docked_video_height", player.VIDEO_BOX_HEIGHT))))
        except (TypeError, ValueError):
            self._preferred = player.VIDEO_BOX_HEIGHT
        self.setFixedHeight(10)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        parent.installEventFilter(self)
        player.installEventFilter(self)
        self._update = QTimer(self)
        self._update.setSingleShot(True)
        self._update.timeout.connect(self.apply_height)
        self._update.start(0)

    def eventFilter(self, obj, event) -> bool:
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show,
                            QEvent.Type.Hide, QEvent.Type.ParentChange):
            self._update.start(0)
        return False

    def apply_height(self) -> None:
        p = self.player
        docked = (p.parentWidget() is self.parentWidget()
                  and not p._fs_ui and not p._popout_mode and not p.isHidden())
        self.setVisible(docked)
        if not docked:
            return
        layout = self.parentWidget().layout()
        margins = layout.contentsMargins()
        available = self.parentWidget().height() - margins.top() - margins.bottom()
        visible = [layout.itemAt(i).widget() for i in range(layout.count())]
        visible = [w for w in visible if w is not None and not w.isHidden()]
        available -= max(0, len(visible) - 1) * layout.spacing()
        for widget in visible:
            if widget is not p:
                available -= (self.info.minimumHeight() if widget is self.info
                              else widget.height())
        bar = (p.bar.sizeHint().height() + p.layout().spacing()
               if p.bar.isVisible() and p.bar.parentWidget() is p else 0)
        height = max(self.MIN_VIDEO_HEIGHT, min(self._preferred, available - bar))
        if (getattr(p, "_docked_video_height", None) != height
                or p.minimumHeight() != self.MIN_VIDEO_HEIGHT + bar
                or p.maximumHeight() != height + bar):
            p._docked_video_height = height
            p._lock_video_box()
            p.updateGeometry()

    def restore_after_dock(self) -> None:
        self._drag = None
        layout = self.parentWidget().layout()
        index = layout.indexOf(self.player)
        if index >= 0:
            layout.setStretch(index, 0)
        layout.invalidate()
        self.apply_height()
        self.player.updateGeometry()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = (event.globalPosition().y(),
                          getattr(self.player, "_docked_video_height", self._preferred))
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._preferred = max(self.MIN_VIDEO_HEIGHT, min(2160, int(
                self._drag[1] + event.globalPosition().y() - self._drag[0])))
            self.apply_height()
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drag is not None:
            self._drag = None
            self._preferred = self.player._docked_video_height
            self.settings.setValue("docked_video_height", self._preferred)
            event.accept()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setPen(QPen(QColor(P["muted3"]), 2))
        y = self.height() // 2
        x = self.width() // 2
        painter.drawLine(x - 18, y, x + 18, y)
