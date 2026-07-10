"""First-run onboarding, shown when the app opens without a provider.

A compact multi-step overlay drawn on top of the main content (a plain
child widget, not a modal dialog, so it feels part of the app):

  1. Welcome - a greeting that flashes through languages, plus a language
               picker for the whole app.
  2. Connect - Xtream Codes server / username / password, with a short
               tour of what the app does right below it.
  3. Trakt   - optional Trakt.tv sync.

At any point the user can "Skip for now" and explore the empty app; a
pulsing "+ Add provider" button in the middle pane brings the wizard back
until a provider is configured.
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from ..i18n import LANGUAGES, tr
from .theme import P


class WelcomeOverlay(QWidget):
    # Decorative greetings that flash on the first page.
    _GREETINGS = (
        "Welcome", "Bienvenue", "Bienvenido", "Willkommen", "Benvenuto",
        "Bem-vindo", "Välkommen", "Witaj", "欢迎", "ようこそ",
        "Добро пожаловать", "환영합니다", "ยินดีต้อนรับ", "مرحبا", "Hoş geldiniz",
    )

    def __init__(self, parent: QWidget, *, settings,
                 on_connect: Callable[[str, str, str], None],
                 on_explore: Callable[[], None],
                 on_connect_trakt: Callable[[], None],
                 on_language_change: Callable[[str], None]) -> None:
        super().__init__(parent)
        self._settings = settings
        self._on_connect = on_connect
        self._on_explore = on_explore
        self._on_connect_trakt = on_connect_trakt
        self._on_language_change = on_language_change

        self.setObjectName("WelcomeOverlay")
        self.setStyleSheet(
            f"#WelcomeOverlay {{ background: {P['bg']}; }}"
            f"#WelcomeCard {{ background: {P['pane']};"
            f" border: 1px solid {P['border']}; border-radius: 16px; }}"
            f"#Hero {{ font-size: 30px; font-weight: 800; color: {P['text']}; }}"
            f"#OnbTitle {{ font-size: 18px; font-weight: 700;"
            f" color: {P['text']}; }}"
            f"#OnbSub {{ font-size: 13px; color: {P['muted']}; }}"
            f"#OnbFeat {{ font-size: 13px; color: {P['muted']}; }}"
            f"#OnbErr {{ font-size: 12px; color: {P['error']}; }}")

        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._card = QFrame(objectName="WelcomeCard")
        self._card.setFixedWidth(460)
        card_l = QVBoxLayout(self._card)
        card_l.setContentsMargins(34, 26, 34, 24)
        card_l.setSpacing(12)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_welcome_page())   # 0
        self._stack.addWidget(self._build_connect_page())   # 1
        self._stack.addWidget(self._build_trakt_page())     # 2
        card_l.addWidget(self._stack)
        outer.addWidget(self._card)

        self._greet_idx = 0
        self._flash = QTimer(self)
        self._flash.timeout.connect(self._next_greeting)
        self._flash.start(1300)

    # -- pages ---------------------------------------------------------------

    def _build_welcome_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(12)
        self._hero = QLabel(self._GREETINGS[0], objectName="Hero")
        self._hero.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._w_sub = QLabel(tr("welcome_subtitle"), objectName="OnbSub")
        self._w_sub.setWordWrap(True)
        self._w_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._lang_label = QLabel(tr("onb_choose_language"), objectName="OnbSub")
        self._lang_combo = QComboBox()
        for code, name in LANGUAGES.items():
            self._lang_combo.addItem(name, code)
        cur = self._settings.value("language", "en") if self._settings else "en"
        i = self._lang_combo.findData(cur)
        if i >= 0:
            self._lang_combo.setCurrentIndex(i)
        self._lang_combo.currentIndexChanged.connect(self._language_picked)

        self._w_next = QPushButton(tr("onb_next"), objectName="Primary")
        self._w_next.setMinimumHeight(38)
        self._w_next.clicked.connect(lambda: self._stack.setCurrentIndex(1))
        self._w_skip = QPushButton(tr("welcome_explore"))
        self._w_skip.clicked.connect(self._explore)

        lay.addWidget(self._hero)
        lay.addWidget(self._w_sub)
        lay.addSpacing(4)
        lay.addWidget(self._lang_label)
        lay.addWidget(self._lang_combo)
        lay.addSpacing(4)
        lay.addWidget(self._w_next)
        lay.addWidget(self._w_skip)
        return page

    def _build_connect_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(10)
        self._c_title = QLabel(tr("login_subtitle"), objectName="OnbTitle")
        self._c_title.setWordWrap(True)

        form = QFormLayout()
        s = self._settings
        self._server = QLineEdit(s.value("server", "") if s else "")
        self._server.setPlaceholderText("http://server:port")
        self._user = QLineEdit(s.value("username", "") if s else "")
        self._pw = QLineEdit(s.value("password", "") if s else "")
        self._pw.setEchoMode(QLineEdit.EchoMode.Password)
        self._lbl_server = QLabel(tr("login_server"))
        self._lbl_user = QLabel(tr("login_username"))
        self._lbl_pw = QLabel(tr("login_password"))
        form.addRow(self._lbl_server, self._server)
        form.addRow(self._lbl_user, self._user)
        form.addRow(self._lbl_pw, self._pw)

        self._c_err = QLabel("", objectName="OnbErr")
        self._c_err.setWordWrap(True)
        self._c_connect = QPushButton(tr("btn_connect"), objectName="Primary")
        self._c_connect.setMinimumHeight(38)
        self._c_connect.clicked.connect(self._do_connect)

        # Short tour of what the app does, right under the login form.
        self._f_title = QLabel(tr("onb_features_title"), objectName="OnbSub")
        self._feats = [QLabel("•  " + tr(k), objectName="OnbFeat")
                       for k in ("onb_feat_1", "onb_feat_2",
                                 "onb_feat_3", "onb_feat_4")]

        lay.addWidget(self._c_title)
        lay.addLayout(form)
        lay.addWidget(self._c_err)
        lay.addWidget(self._c_connect)
        lay.addSpacing(6)
        lay.addWidget(self._f_title)
        for f in self._feats:
            f.setWordWrap(True)
            lay.addWidget(f)
        lay.addSpacing(4)
        row = QHBoxLayout()
        self._c_back = QPushButton(tr("onb_back"))
        self._c_back.clicked.connect(lambda: self._stack.setCurrentIndex(0))
        self._c_skip = QPushButton(tr("onb_skip"))
        self._c_skip.clicked.connect(self._explore)
        row.addWidget(self._c_back)
        row.addStretch(1)
        row.addWidget(self._c_skip)
        lay.addLayout(row)
        return page

    def _build_trakt_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(12)
        self._t_title = QLabel(tr("onb_trakt_title"), objectName="OnbTitle")
        self._t_title.setWordWrap(True)
        self._t_desc = QLabel(tr("onb_trakt_desc"), objectName="OnbSub")
        self._t_desc.setWordWrap(True)
        self._t_connect = QPushButton(tr("onb_trakt_connect"))
        self._t_connect.setMinimumHeight(34)
        self._t_connect.clicked.connect(self._on_connect_trakt)
        self._t_finish = QPushButton(tr("onb_finish"), objectName="Primary")
        self._t_finish.setMinimumHeight(38)
        self._t_finish.clicked.connect(self._finish)

        lay.addWidget(self._t_title)
        lay.addWidget(self._t_desc)
        lay.addSpacing(6)
        lay.addWidget(self._t_connect)
        lay.addSpacing(4)
        lay.addWidget(self._t_finish)
        return page

    # -- actions -------------------------------------------------------------

    def _next_greeting(self) -> None:
        self._greet_idx = (self._greet_idx + 1) % len(self._GREETINGS)
        self._hero.setText(self._GREETINGS[self._greet_idx])

    def _language_picked(self) -> None:
        code = self._lang_combo.currentData()
        if not code:
            return
        self._on_language_change(code)
        self._retranslate()

    def _do_connect(self) -> None:
        server = self._server.text().strip()
        user = self._user.text().strip()
        pw = self._pw.text().strip()
        if not (server and user and pw):
            self._c_err.setText(tr("onb_fill_all"))
            return
        self._c_err.setText("")
        self._on_connect(server, user, pw)
        self._stack.setCurrentIndex(2)   # continue to the optional Trakt step

    def _explore(self) -> None:
        self._flash.stop()
        self.hide()
        self._on_explore()

    def _finish(self) -> None:
        self._flash.stop()
        self.hide()

    def _retranslate(self) -> None:
        self._w_sub.setText(tr("welcome_subtitle"))
        self._lang_label.setText(tr("onb_choose_language"))
        self._w_next.setText(tr("onb_next"))
        self._w_skip.setText(tr("welcome_explore"))
        self._c_title.setText(tr("login_subtitle"))
        self._lbl_server.setText(tr("login_server"))
        self._lbl_user.setText(tr("login_username"))
        self._lbl_pw.setText(tr("login_password"))
        self._c_connect.setText(tr("btn_connect"))
        self._f_title.setText(tr("onb_features_title"))
        for lbl, key in zip(self._feats, ("onb_feat_1", "onb_feat_2",
                                          "onb_feat_3", "onb_feat_4")):
            lbl.setText("•  " + tr(key))
        self._c_back.setText(tr("onb_back"))
        self._c_skip.setText(tr("onb_skip"))
        self._t_title.setText(tr("onb_trakt_title"))
        self._t_desc.setText(tr("onb_trakt_desc"))
        self._t_connect.setText(tr("onb_trakt_connect"))
        self._t_finish.setText(tr("onb_finish"))

    # -- geometry ------------------------------------------------------------

    def reset(self) -> None:
        """Return to the first page and resume the greeting flash (used when
        the wizard is reopened from the '+ Add provider' button)."""
        self._stack.setCurrentIndex(0)
        self._c_err.setText("")
        if not self._flash.isActive():
            self._flash.start(1300)

    def cover(self) -> None:
        """Resize to fully cover the parent window's content area, on top."""
        parent = self.parent()
        central = (parent.centralWidget()
                   if hasattr(parent, "centralWidget") else None)
        self.setGeometry(central.geometry() if central else parent.rect())
        self.raise_()
        self.show()
