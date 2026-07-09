"""First-run welcome overlay, shown when the app opens without a provider.

It is a plain child widget drawn on top of the main content (not a modal
dialog) so the first run feels like part of the app. It offers two choices:
connect a provider now, or explore the empty app and add one later. The
window supplies both callbacks.
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from ..i18n import tr
from .theme import P


class WelcomeOverlay(QWidget):
    def __init__(self, parent: QWidget,
                 on_connect: Callable[[], None],
                 on_explore: Callable[[], None]) -> None:
        super().__init__(parent)
        self.setObjectName("WelcomeOverlay")
        self.setStyleSheet(
            f"#WelcomeOverlay {{ background: {P['bg']}; }}"
            f"#WelcomeCard {{ background: {P['pane']};"
            f" border: 1px solid {P['border']}; border-radius: 16px; }}"
            f"#WelcomeTitle {{ font-size: 22px; font-weight: 700;"
            f" color: {P['text']}; }}"
            f"#WelcomeSub {{ font-size: 13px; color: {P['muted']}; }}")

        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card = QFrame(objectName="WelcomeCard")
        card.setFixedWidth(440)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(36, 32, 36, 32)
        cl.setSpacing(14)

        title = QLabel(tr("welcome_title"), objectName="WelcomeTitle")
        title.setWordWrap(True)
        sub = QLabel(tr("welcome_subtitle"), objectName="WelcomeSub")
        sub.setWordWrap(True)

        connect_btn = QPushButton(tr("welcome_connect"), objectName="Primary")
        connect_btn.setMinimumHeight(38)
        connect_btn.clicked.connect(on_connect)
        explore_btn = QPushButton(tr("welcome_explore"))
        explore_btn.setMinimumHeight(34)
        explore_btn.clicked.connect(on_explore)

        cl.addWidget(title)
        cl.addWidget(sub)
        cl.addSpacing(6)
        cl.addWidget(connect_btn)
        cl.addWidget(explore_btn)
        outer.addWidget(card)

    def cover(self) -> None:
        """Resize to fully cover the parent window's content area, on top."""
        parent = self.parent()
        central = (parent.centralWidget()
                   if hasattr(parent, "centralWidget") else None)
        self.setGeometry(central.geometry() if central else parent.rect())
        self.raise_()
        self.show()
