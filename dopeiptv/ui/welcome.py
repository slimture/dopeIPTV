"""First-run onboarding, shown when the app opens without a provider.

A compact multi-step overlay drawn on top of the main content (a plain
child widget, not a modal dialog, so it feels part of the app):

  1. Welcome - a greeting that flashes through languages, plus a language
               picker for the whole app.
  2. Connect - Xtream Codes server / username / password (or an M3U URL),
               with a short tour of what the app does right below it. A whole
               Xtream link pasted into the server field auto-fills the three
               credential fields.
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
    QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from ..i18n import LANGUAGES, tr
from .theme import P


class WelcomeOverlay(QWidget):
    """First-run onboarding overlay: a multi-step welcome/connect/Trakt wizard drawn over the main content."""
    # Decorative greetings that flash on the first page - one per shipped
    # language, so the rotation mirrors how many languages the app speaks.
    _GREETINGS = (
        "Welcome", "Välkommen", "Bienvenido", "Willkommen", "Bienvenue",
        "欢迎", "Добро пожаловать", "ยินดีต้อนรับ", "Bem-vindo", "Benvenuto",
        "Welkom", "Witaj", "Dobrodošli", "Добродошли", "Καλώς ήρθατε",
        "Hoş geldiniz", "Вітаємо", "Selamat datang", "Chào mừng", "स्वागत है",
        "ようこそ", "환영합니다", "Karibu", "مرحبا", "خوش آمدید",
        "ברוכים הבאים", "خوش آمدید",
    )

    def __init__(self, parent: QWidget, *, settings,
                 on_connect: Callable[[str, str, str, str, str], None],
                 on_explore: Callable[[], None],
                 on_connect_trakt: Callable[[], None],
                 on_language_change: Callable[[str], None],
                 on_demo: Callable[[], None]) -> None:
        super().__init__(parent)
        self._settings = settings
        self._on_connect = on_connect
        self._on_explore = on_explore
        self._on_connect_trakt = on_connect_trakt
        self._on_language_change = on_language_change
        self._on_demo = on_demo

        self.setObjectName("WelcomeOverlay")
        # Accept keyboard focus so Esc reaches keyPressEvent even before the
        # user clicks a field.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet(
            f"#WelcomeOverlay {{ background: {P['bg']}; }}"
            f"#WelcomeCard {{ background: {P['pane']};"
            f" border: 1px solid {P['border']}; border-radius: 16px; }}"
            f"#Hero {{ font-size: 23px; font-weight: 800; color: {P['text']}; }}"
            f"#OnbTitle {{ font-size: 15px; font-weight: 700;"
            f" color: {P['text']}; }}"
            f"#OnbSub {{ font-size: 12px; color: {P['muted']}; }}"
            f"#OnbFeat {{ font-size: 12px; color: {P['muted']}; }}"
            f"#OnbErr {{ font-size: 12px; color: {P['error']}; }}"
            f"#OnbOk {{ font-size: 12px; font-weight: 700;"
            f" color: {P.get('ok', '#3fb950')}; }}"
            # A tinted "tip" band so the paste-a-link hint is the first thing
            # read on the connect step, clearly set apart from the fields.
            f"#OnbTip {{ font-size: 12px; color: {P['text']};"
            f" background: {P['input']}; border: 1px solid {P['border']};"
            f" border-radius: 8px; padding: 8px 10px; }}")

        # Stretches above and below keep the card at its natural (content)
        # height, centred - without them the card stretches to fill the whole
        # overlay, so no amount of inner compression would shrink it.
        outer = QVBoxLayout(self)
        outer.addStretch(1)
        self._card = QFrame(objectName="WelcomeCard")
        self._card.setFixedWidth(430)
        self._card.setSizePolicy(QSizePolicy.Policy.Fixed,
                                 QSizePolicy.Policy.Maximum)
        card_l = QVBoxLayout(self._card)
        card_l.setContentsMargins(26, 16, 26, 16)
        card_l.setSpacing(6)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_welcome_page())   # 0
        self._stack.addWidget(self._build_connect_page())   # 1
        self._stack.addWidget(self._build_trakt_page())     # 2
        card_l.addWidget(self._stack)
        outer.addWidget(self._card, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(1)
        # A QStackedWidget otherwise reserves the height of its tallest page,
        # so the short welcome page would look as tall as the connect form.
        # Let the card hug whichever page is showing.
        self._stack.currentChanged.connect(self._fit_card)
        self._fit_card(0)

        # Esc is handled by the main window's single Escape shortcut (it calls
        # dismiss() when this overlay is up) - a second shortcut here would
        # just make Escape ambiguous and fire neither.

        self._trakt_connected = False
        self._greet_idx = 0
        self._flash = QTimer(self)
        self._flash.timeout.connect(self._next_greeting)
        self._flash.start(1300)

    # -- pages ---------------------------------------------------------------

    def _build_welcome_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(7)
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
        self._w_next.setMinimumHeight(34)
        self._w_next.clicked.connect(lambda: self._stack.setCurrentIndex(1))
        self._w_demo = QPushButton(tr("onb_try_demo"))
        self._w_demo.clicked.connect(self._demo)
        self._w_skip = QPushButton(tr("welcome_explore"))
        self._w_skip.clicked.connect(self._explore)

        lay.addWidget(self._hero)
        lay.addWidget(self._w_sub)
        lay.addSpacing(2)
        lay.addWidget(self._lang_label)
        lay.addWidget(self._lang_combo)
        lay.addSpacing(2)
        lay.addWidget(self._w_next)
        lay.addWidget(self._w_demo)
        lay.addWidget(self._w_skip)
        return page

    def _build_connect_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(6)
        self._c_title = QLabel(tr("login_subtitle"), objectName="OnbTitle")
        self._c_title.setWordWrap(True)
        # Prominent tip at the very top of the step: you can paste a whole
        # provider link, or fill the fields in by hand. Shown in both modes.
        self._c_hint = QLabel(tr("onb_xtream_link_hint"), objectName="OnbTip")
        self._c_hint.setWordWrap(True)

        form = QFormLayout()
        s = self._settings
        self._conn_kind = QComboBox()
        self._conn_kind.addItem(tr("playlist_kind_xtream"), "xtream")
        self._conn_kind.addItem(tr("playlist_kind_m3u"), "m3u")
        self._conn_kind.currentIndexChanged.connect(self._update_conn_kind)
        self._name = QLineEdit(s.value("playlist_name", "") if s else "")
        self._name.setPlaceholderText(tr("playlist_name_hint"))
        self._server = QLineEdit(s.value("server", "") if s else "")
        self._server.setPlaceholderText("http://server:port")
        # A single pasted link is recognised as Xtream (fans out into
        # server/username/password) or M3U (a plain playlist URL) and the mode
        # dropdown follows automatically; manual entry is untouched.
        # textEdited fires on every user edit (incl. paste) but NOT on our own
        # setText, so the fields fill the instant the link is pasted - no need
        # to tab away first - while a half-typed URL simply doesn't match yet.
        self._server.textEdited.connect(lambda _t: self._maybe_autodetect_link())
        self._user = QLineEdit(s.value("username", "") if s else "")
        self._pw = QLineEdit(s.value("password", "") if s else "")
        self._pw.setEchoMode(QLineEdit.EchoMode.Password)
        self._lbl_kind = QLabel(tr("playlist_kind"))
        self._lbl_name = QLabel(tr("playlist_name"))
        self._lbl_server = QLabel(tr("login_server"))
        self._lbl_user = QLabel(tr("login_username"))
        self._lbl_pw = QLabel(tr("login_password"))
        form.addRow(self._lbl_kind, self._conn_kind)
        form.addRow(self._lbl_name, self._name)
        form.addRow(self._lbl_server, self._server)
        form.addRow(self._lbl_user, self._user)
        form.addRow(self._lbl_pw, self._pw)

        self._c_err = QLabel("", objectName="OnbErr")
        self._c_err.setWordWrap(True)
        # Hidden until there is an error, so the empty line doesn't push a
        # gap between the Connect button and the feature tour below.
        self._c_err.setVisible(False)
        self._c_connect = QPushButton(tr("btn_connect"), objectName="Primary")
        self._c_connect.setMinimumHeight(34)
        self._c_connect.clicked.connect(self._do_connect)

        # Short tour of what the app does, right under the login form.
        self._f_title = QLabel(tr("onb_features_title"), objectName="OnbSub")
        self._feats = [QLabel("•  " + tr(k), objectName="OnbFeat")
                       for k in ("onb_feat_1", "onb_feat_2",
                                 "onb_feat_3", "onb_feat_4")]

        lay.addWidget(self._c_title)
        lay.addWidget(self._c_hint)
        lay.addLayout(form)
        # Connect sits directly under the Password field; the error line
        # (hidden until needed) lives just below the button.
        lay.addWidget(self._c_connect)
        lay.addWidget(self._c_err)
        lay.addSpacing(8)
        # Keep the "What's inside" heading tight against its bullet list.
        feats_box = QVBoxLayout()
        feats_box.setSpacing(1)
        feats_box.setContentsMargins(0, 0, 0, 0)
        feats_box.addWidget(self._f_title)
        for f in self._feats:
            f.setWordWrap(True)
            feats_box.addWidget(f)
        lay.addLayout(feats_box)
        lay.addSpacing(2)
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
        lay.setSpacing(7)
        self._t_title = QLabel(tr("onb_trakt_title"), objectName="OnbTitle")
        self._t_title.setWordWrap(True)
        self._t_desc = QLabel(tr("onb_trakt_desc"), objectName="OnbSub")
        self._t_desc.setWordWrap(True)
        self._t_connect = QPushButton(tr("onb_trakt_connect"))
        self._t_connect.setMinimumHeight(30)
        self._t_connect.clicked.connect(self._on_connect_trakt)
        # A green confirmation that appears once Trakt is linked, so the user
        # sees the step succeeded instead of wondering if anything happened.
        self._t_ok = QLabel("", objectName="OnbOk")
        self._t_ok.setWordWrap(True)
        self._t_ok.setVisible(False)
        self._t_finish = QPushButton(tr("onb_finish"), objectName="Primary")
        self._t_finish.setMinimumHeight(34)
        self._t_finish.clicked.connect(self._finish)

        lay.addWidget(self._t_title)
        lay.addWidget(self._t_desc)
        lay.addSpacing(2)
        lay.addWidget(self._t_connect)
        lay.addWidget(self._t_ok)
        lay.addSpacing(2)
        lay.addWidget(self._t_finish)
        return page

    def set_trakt_connected(self, connected: bool) -> None:
        """Reflect a successful Trakt link: swap the connect button for a green
        confirmation and nudge the finish button so the step reads as done."""
        if not connected:
            return
        self._trakt_connected = True
        self._t_ok.setText(tr("onb_trakt_connected"))
        self._t_ok.setVisible(True)
        self._t_connect.setText(tr("onb_trakt_reconnect"))
        self._t_finish.setText(tr("onb_finish_done"))
        self._fit_card(self._stack.currentIndex())

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

    def _update_conn_kind(self) -> None:
        m3u = self._conn_kind.currentData() == "m3u"
        self._lbl_server.setText(
            tr("playlist_m3u_url") if m3u else tr("login_server"))
        self._server.setPlaceholderText(
            "https://example.com/playlist.m3u" if m3u else "http://server:port")
        for w in (self._lbl_user, self._user, self._lbl_pw, self._pw):
            w.setVisible(not m3u)
        self._fit_card(self._stack.currentIndex())

    def _set_kind(self, kind: str) -> None:
        idx = self._conn_kind.findData(kind)
        if idx >= 0 and idx != self._conn_kind.currentIndex():
            self._conn_kind.setCurrentIndex(idx)   # fires _update_conn_kind

    def _maybe_autodetect_link(self) -> None:
        """Recognise a pasted provider link in the server field and configure
        the form for it: Xtream (split into server/username/password, the
        preferred mode) or M3U (a plain playlist URL). A bare host with no link
        shape is left alone so manual entry keeps working."""
        from ..providers.client import detect_provider_link
        detected = detect_provider_link(self._server.text())
        if not detected:
            return
        kind, server, user, pw = detected
        self._set_kind(kind)
        self._server.setText(server)
        if kind == "xtream":
            self._user.setText(user)
            self._pw.setText(pw)

    def _do_connect(self) -> None:
        kind = self._conn_kind.currentData()
        server = self._server.text().strip()
        name = self._name.text().strip()
        if kind == "m3u":
            ok = bool(server)
            user = pw = ""
        else:
            user = self._user.text().strip()
            pw = self._pw.text().strip()
            ok = bool(server and user and pw)
        if not ok:
            self._c_err.setText(tr("onb_fill_all"))
            self._c_err.setVisible(True)
            self._fit_card(self._stack.currentIndex())
            return
        self._c_err.setText("")
        self._c_err.setVisible(False)
        self._on_connect(server, user, pw, kind, name)
        self._stack.setCurrentIndex(2)   # continue to the optional Trakt step

    def dismiss(self) -> None:
        """Close the wizard the same as 'Continue without account' - called by
        the main window's Escape shortcut."""
        self._explore()

    def _demo(self) -> None:
        self._flash.stop()
        self.hide()
        self._on_demo()

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
        self._w_demo.setText(tr("onb_try_demo"))
        self._w_skip.setText(tr("welcome_explore"))
        self._c_title.setText(tr("login_subtitle"))
        self._lbl_kind.setText(tr("playlist_kind"))
        self._lbl_name.setText(tr("playlist_name"))
        self._name.setPlaceholderText(tr("playlist_name_hint"))
        self._lbl_user.setText(tr("login_username"))
        self._lbl_pw.setText(tr("login_password"))
        self._c_hint.setText(tr("onb_xtream_link_hint"))
        self._update_conn_kind()   # re-labels the server row for the mode
        self._c_connect.setText(tr("btn_connect"))
        self._f_title.setText(tr("onb_features_title"))
        for lbl, key in zip(self._feats, ("onb_feat_1", "onb_feat_2",
                                          "onb_feat_3", "onb_feat_4"),
                            strict=False):
            lbl.setText("•  " + tr(key))
        self._c_back.setText(tr("onb_back"))
        self._c_skip.setText(tr("onb_skip"))
        self._t_title.setText(tr("onb_trakt_title"))
        self._t_desc.setText(tr("onb_trakt_desc"))
        connected = self._trakt_connected
        self._t_connect.setText(
            tr("onb_trakt_reconnect") if connected else tr("onb_trakt_connect"))
        self._t_ok.setText(tr("onb_trakt_connected"))
        self._t_finish.setText(
            tr("onb_finish_done") if connected else tr("onb_finish"))

    # -- geometry ------------------------------------------------------------

    def _fit_card(self, idx: int) -> None:
        # A QStackedWidget always reserves the height of its tallest page.
        # Pin it to the current page's height so the short welcome page isn't
        # padded out to the size of the connect form.
        cur = self._stack.widget(idx)
        cur.adjustSize()
        self._stack.setFixedHeight(cur.sizeHint().height())
        self._card.adjustSize()
        if self.isVisible():
            self.cover()

    def reset(self) -> None:
        """Return to the first page and resume the greeting flash (used when
        the wizard is reopened from the '+ Add provider' button)."""
        self._stack.setCurrentIndex(0)
        self._c_err.setText("")
        self._c_err.setVisible(False)
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
        self.setFocus()   # so Esc works without clicking first
