"""First-run welcome guide, shown when the app opens without a source.

A compact card drawn over the window (a plain child widget, not a modal
dialog, so it feels part of the app), driven by ui/mw_onboarding.py:

  1. Welcome  - a greeting that flashes through languages, the app's
                language (applied at once), one clear "Get started", and two
                quiet links: the demo, and settings brought from another
                computer (the Bring step). Nerd offered quietly under them.
  2. Choose   - "What do you want to watch?": an IPTV provider (Xtream
                Codes, an M3U playlist link or file, a Stalker / Ministra
                portal), a media server (Jellyfin, Emby, Plex), a box at home
                (Enigma2, Tvheadend, HDHomeRun, SAT>IP...), or just one's own
                files and radio (no source: Local files).
  3. Form     - the kind's own fields and heading (ui/dialogs.SourceFields'
                rules: a whole provider link pasted fills the rest, an M3U
                link switches to M3U, a box is found on the network, a media
                server signed in to with ui/media_signin.py).
  4. Connect  - the source tried off the window's thread
                (providers/connect_check.py): what it is doing, then "Done!"
                with what came, or why not in plain words with what to do.
                Back always works and keeps what was typed; nothing is kept
                until it worked.
  5. Bring    - for someone who uses dopeIPTV on another computer already:
                the sync code from there, or a backup file. The window does
                the work (ui/mw_onboarding.py, with Settings' own sync and
                restore) and says how it went.

Esc closes it (the window's one Escape shortcut calls dismiss()); with no
source the window then offers a calm "Add your own source" over its lists.
However it goes (Esc, the demo, just files and radio, a source that came),
what it had under way stops - a try, a media server's sign-in (a Plex PIN
approved later connects nothing), a sync still fetching - and no login typed
stays in it (_leave). The form's Back stops a sign-in too, and keeps what
was typed.
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QLocale, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMenu, QMenuBar, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QStackedWidget, QVBoxLayout, QWidget,
)

from .. import APP_NAME
from ..i18n import LANGUAGES, tr
from . import a11y, theme
from .box_finder import (
    BOX_KINDS, LOGIN_KINDS, MEDIA_KINDS, PIN_KINDS, PLACEHOLDERS, SHORT_NAMES, FindOnNetwork,
    KIND_LABELS,
)
from .dialogs import KindOwnFields, PlaylistDialog, stalker_available
from .media_signin import MediaSignIn
from .theme import P
from .widgets import as_label

#: The pages, in the stack's order.
PAGE_WELCOME, PAGE_CHOOSE, PAGE_FORM, PAGE_WORK, PAGE_BRING = range(5)
#: The connect step begins with the choice of what to watch.
PAGE_CONNECT = PAGE_CHOOSE

#: What the choice offers, and the kinds of each.
IPTV, MEDIA, BOX, FILES = "iptv", "media", "box", "files"


def family_kinds(family: str) -> tuple[str, ...]:
    if family == MEDIA:
        return MEDIA_KINDS
    if family == BOX:
        return BOX_KINDS
    if family == IPTV:
        return ("xtream", "m3u", "stalker") if stalker_available() else ("xtream", "m3u")
    return ()


def family_of(kind: str) -> str:
    for family in (IPTV, MEDIA, BOX):
        if kind in family_kinds(family):
            return family
    return ""


#: The connect step's words for each step it reports (connect_check.STEPS).
STEP_WORDS = {"login": "ms_signing_in", "live": "onb_step_live",
              "vod": "onb_step_vod", "series": "onb_step_series"}


class _Link(QPushButton):
    """A quiet link: a flat button - a screen reader says a button, Space
    and Enter press it - whose words wrap: a long translation goes on a
    second line instead of being cut at the card's edge. Muted, the
    theme's text colour and underlined while pointed at or focused."""

    PAD_Y = 3

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("OnbLink")
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        sp = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        self._hot = False
        self._words = ""
        self.set_plain(text)

    def plain(self) -> str:
        return self._words

    def set_plain(self, text: str) -> None:
        # The words painted as they are; the button's own text with "&"
        # doubled, so a translation's "&" makes no shortcut (widgets.as_label).
        self._words = text
        self.setText(as_label(text))
        self.setAccessibleName(text)
        self.updateGeometry()
        self.update()

    def _font(self) -> QFont:
        f = QFont(self.font())
        f.setPixelSize(12)
        f.setUnderline(self._hot or self.hasFocus())
        return f

    def _flags(self) -> int:
        return int(Qt.AlignmentFlag.AlignCenter) | int(Qt.TextFlag.TextWordWrap)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt's name
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt's name
        fm = QFontMetrics(self._font())
        r = fm.boundingRect(0, 0, max(1, width), 10_000, self._flags(), self._words)
        return r.height() + 2 * self.PAD_Y

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        fm = QFontMetrics(self._font())
        w = fm.horizontalAdvance(self._words) + 4
        return QSize(w, fm.height() + 2 * self.PAD_Y)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        fm = QFontMetrics(self._font())
        return QSize(min(120, self.sizeHint().width()), fm.height() + 2 * self.PAD_Y)

    def enterEvent(self, event) -> None:
        self._hot = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hot = False
        self.update()
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        try:
            p.setFont(self._font())
            p.setPen(QColor(P["text"] if (self._hot or self.hasFocus()) else P["muted"]))
            p.drawText(self.rect().adjusted(0, self.PAD_Y, 0, -self.PAD_Y), self._flags(),
                       self._words)
        finally:
            p.end()


class _Choice(QPushButton):
    """One answer to "What do you want to watch?": its name over what it
    covers, a card as wide as the page. A button for the keyboard and a
    screen reader alike - its name its words, what it covers its
    description - painted in the theme's colours, mirrored right to left."""

    PAD_X, PAD_Y, GAP, ARROW = 14, 11, 3, 18

    def __init__(self, title: str = "", detail: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("OnbChoice")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAutoDefault(False)
        sp = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        self._title, self._detail = "", ""
        self.set_words(title, detail)

    def set_words(self, title: str, detail: str) -> None:
        self._title, self._detail = title, detail
        self.setText(title)
        self.setAccessibleName(title)
        self.setAccessibleDescription(detail)
        self.updateGeometry()
        self.update()

    def words(self) -> tuple[str, str]:
        return self._title, self._detail

    def _fonts(self) -> tuple[QFont, QFont]:
        big = QFont(self.font())
        big.setPixelSize(14)
        big.setWeight(QFont.Weight.Bold)
        small = QFont(self.font())
        small.setPixelSize(12)
        return big, small

    def _text_width(self, width: int) -> int:
        return max(40, width - 2 * self.PAD_X - self.ARROW)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt's name
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt's name
        big, small = self._fonts()
        tw = self._text_width(width)
        flags = int(Qt.TextFlag.TextWordWrap)
        h1 = QFontMetrics(big).boundingRect(0, 0, tw, 10_000, flags, self._title).height()
        h2 = QFontMetrics(small).boundingRect(0, 0, tw, 10_000, flags, self._detail).height() \
            if self._detail else 0
        return 2 * self.PAD_Y + h1 + (self.GAP + h2 if h2 else 0)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        w = max(self.width(), 320)
        return QSize(w, self.heightForWidth(w))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        return QSize(160, self.heightForWidth(max(self.width(), 160)))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.height() != self.heightForWidth(self.width()):
            self.updateGeometry()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        hot = self.underMouse() or self.isDown()
        focus = self.hasFocus()
        box = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(theme.ACCENT if focus else P["border_in"]), 2 if focus else 1))
        p.setBrush(QColor(P["sel"] if hot else P["input"]))
        p.drawRoundedRect(box, 10, 10)
        big, small = self._fonts()
        rtl = self.isRightToLeft()
        tw = self._text_width(self.width())
        x = self.width() - self.PAD_X - tw if rtl else self.PAD_X
        side = Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft
        flags = int(side | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap)
        y = self.PAD_Y
        p.setFont(big)
        p.setPen(QColor(P["text"]))
        r1 = QFontMetrics(big).boundingRect(0, 0, tw, 10_000, flags, self._title)
        p.drawText(QRectF(x, y, tw, r1.height()), flags, self._title)
        if self._detail:
            y += r1.height() + self.GAP
            p.setFont(small)
            p.setPen(QColor(P["muted"]))
            r2 = QFontMetrics(small).boundingRect(0, 0, tw, 10_000, flags, self._detail)
            p.drawText(QRectF(x, y, tw, r2.height()), flags, self._detail)
        # A chevron on the trailing edge: it leads on.
        cx = self.PAD_X if rtl else self.width() - self.PAD_X - 6
        cy = self.height() / 2
        path = QPainterPath()
        d = -1 if rtl else 1
        path.moveTo(cx - 3 * d, cy - 6)
        path.lineTo(cx + 3 * d, cy)
        path.lineTo(cx - 3 * d, cy + 6)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(P["text"] if hot or focus else P["muted2"]), 2))
        p.drawPath(path)
        p.end()


class WelcomeOverlay(QWidget):
    """The first-run guide (see the module): welcome, what to watch, the
    kind's form, connecting, and bringing settings from another computer."""

    #: The Bring step asks the window (ui/mw_onboarding.py): sync with this
    #: code; stop the sync it started; pick a backup to restore; quit, so a
    #: staged backup is put in place as the app starts again.
    sync_asked = pyqtSignal(str)
    sync_cancelled = pyqtSignal()
    restore_asked = pyqtSignal()
    quit_asked = pyqtSignal()
    #: The connect step: stop the try under way (Back, Esc).
    connect_cancelled = pyqtSignal()
    # Decorative greetings that flash on the first page, once round - one per
    # shipped language (Persian and Urdu say it alike: once), so the rotation
    # mirrors how many languages the app speaks.
    _GREETINGS = (
        "Welcome", "Välkommen", "Bienvenido", "Willkommen", "Bienvenue",
        "欢迎", "Добро пожаловать", "ยินดีต้อนรับ", "Bem-vindo", "Benvenuto",
        "Welkom", "Witaj", "Dobrodošli", "Добродошли", "Καλώς ήρθατε",
        "Hoş geldiniz", "Вітаємо", "Selamat datang", "Chào mừng", "स्वागत है",
        "ようこそ", "환영합니다", "Karibu", "مرحبا", "خوش آمدید",
        "ברוכים הבאים",
    )
    #: The card's width and its inner padding; the pages lay out in what is
    #: left between the paddings.
    CARD_W = 440
    PAD = 26
    #: Room kept free above and below the card when it fills the window.
    MARGIN = 16

    def __init__(self, parent: QWidget, *, settings,
                 # (server, user, password, kind, name, mac, signed, extra):
                 # a portal's MAC address for kind "stalker", a media server's
                 # sign-in ({"token", "options"}), a preset's guide address
                 # and options ({"epg_url", "options"}).
                 on_connect: Callable[..., None],
                 on_explore: Callable[[], None],
                 on_language_change: Callable[[str], None],
                 on_demo: Callable[[], None],
                 on_files: Callable[[], None] | None = None,
                 # Asked before a source is tried, settings are brought or
                 # Nerd is switched: False keeps all as it is (a kids
                 # profile without a grown-up's PIN, mw_onboarding).
                 guard: Callable[[], bool] | None = None,
                 # Whether a kids profile is in use: no Nerd offered to it.
                 kids: Callable[[], bool] | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._on_connect = on_connect
        self._on_explore = on_explore
        self._on_language_change = on_language_change
        self._on_demo = on_demo
        self._on_files = on_files or on_explore
        self._guard = guard or (lambda: True)
        self._kids = kids or (lambda: False)
        self._family = IPTV
        self._filling = False
        # The connect step: trying (a try under way), done (a source came).
        self._trying = False
        self._done = False
        self._last_try: tuple = ()
        # A found SAT>IP server's description, a Nerd preset's guide address
        # and lists: for the kind they came with, until the address is
        # typed anew or another kind is picked.
        self._extra: dict = {}
        self._extra_kind = ""

        self.setObjectName("WelcomeOverlay")
        # Its own background painted (a plain QWidget paints none of its
        # style sheet's): the lists behind it - which take no click while it
        # is up - are not seen through it.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Accept keyboard focus so Esc reaches keyPressEvent even before the
        # user clicks a field.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(APP_NAME)
        self.setAccessibleDescription(tr("onb_welcome_sub"))
        self.setStyleSheet(
            f"#WelcomeOverlay {{ background: {P['bg']}; }}"
            "#WelcomeScroll, #WelcomeScrollView { background: transparent; border: none; }"
            f"#WelcomeCard {{ background: {P['pane']};"
            f" border: 1px solid {P['border']}; border-radius: 16px; }}"
            f"#Hero {{ font-size: 23px; font-weight: 800; color: {P['text']}; }}"
            f"#OnbTitle {{ font-size: 16px; font-weight: 700;"
            f" color: {P['text']}; }}"
            f"#OnbSub {{ font-size: 12px; color: {P['muted']}; }}"
            f"#OnbErr {{ font-size: 12px; color: {P['error']}; }}"
            f"#OnbOk {{ font-size: 12px; font-weight: 700; color: {P['ok']}; }}"
            f"#OnbWarn {{ font-size: 12px; color: {P['warn']}; }}"
            # A tinted "tip" band so the paste-a-link hint is the first thing
            # read on the form, clearly set apart from the fields.
            f"#OnbTip {{ font-size: 12px; color: {P['text']};"
            f" background: {P['input']}; border: 1px solid {P['border']};"
            f" border-radius: 8px; padding: 8px 10px; }}"
            # What came over from the other computer, and what did not: over
            # the choice, set apart from the tip by the accent's edge.
            f"#OnbNote {{ font-size: 12px; color: {P['text']};"
            f" background: {P['input']}; border: 1px solid {P['accent']};"
            f" border-radius: 8px; padding: 8px 10px; }}"
            f"#OnbLink {{ font-size: 12px; padding: 3px 0; }}"
            f"#OnbOr {{ font-size: 11px; color: {P['muted2']}; }}"
            f"#OnbBar {{ background: {P['input']}; border: none; border-radius: 3px; }}"
            f"#OnbBar::chunk {{ background: {P['accent']}; border-radius: 3px; }}")

        # Stretches above and below keep the card at its natural (content)
        # height, centred - without them the card stretches to fill the whole
        # overlay, so no amount of inner compression would shrink it.
        outer = self._outer = QVBoxLayout(self)
        outer.addStretch(1)
        self._card = QFrame(objectName="WelcomeCard")
        self._card.setFixedWidth(self.CARD_W)
        self._card.setSizePolicy(QSizePolicy.Policy.Fixed,
                                 QSizePolicy.Policy.Maximum)
        card_l = QVBoxLayout(self._card)
        card_l.setContentsMargins(self.PAD, 18, self.PAD, 18)
        card_l.setSpacing(6)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_welcome_page())   # PAGE_WELCOME
        self._stack.addWidget(self._build_choose_page())    # PAGE_CHOOSE
        self._stack.addWidget(self._build_form_page())      # PAGE_FORM
        self._stack.addWidget(self._build_work_page())      # PAGE_WORK
        self._stack.addWidget(self._build_bring_page())     # PAGE_BRING
        # The pages scroll inside the card when the window is too low for
        # the whole page (a small screen, a long language, the box finder's
        # note): the card never grows past the window and cuts them off.
        self._scroll = QScrollArea(objectName="WelcomeScroll")
        # Tab goes to the page's own controls, and it scrolls to each.
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.viewport().setObjectName("WelcomeScrollView")
        self._scroll.viewport().setAutoFillBackground(False)
        self._scroll.setWidget(self._stack)
        # setWidget paints the page with the window's colour; the card's own
        # shows through. And the page follows the viewport's width, also
        # when the scroll bar takes some of it, instead of being cut off on
        # the right at the width of its widest field.
        self._stack.setAutoFillBackground(False)
        self._stack.setSizePolicy(QSizePolicy.Policy.Ignored,
                                  QSizePolicy.Policy.Fixed)
        card_l.addWidget(self._scroll)
        outer.addWidget(self._card, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(1)
        # The form as its first kind wants it.
        self._fill_kinds(IPTV)
        # A QStackedWidget otherwise reserves the height of its tallest page,
        # so the short welcome page would look as tall as the connect form.
        # Let the card hug whichever page is showing.
        self._stack.currentChanged.connect(self._page_shown)
        self._fit_card(0)

        # Esc is handled by the main window's single Escape shortcut (it calls
        # dismiss() when this overlay is up) - a second shortcut here would
        # just make Escape ambiguous and fire neither.

        self._greet_idx = 0
        self._flash = QTimer(self)
        self._flash.timeout.connect(self._next_greeting)
        self._start_greeting()

        # Enter presses the button that has the keys, as Space does (a
        # button outside a dialog takes Space alone): the guide is used with
        # the keyboard only from its first page to its last.
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(True)

        # While the guide is up the keys stay in it: what is under it cannot
        # be seen, and the window's start (its first list, a place opened)
        # gives its list the focus a moment after the guide shows.
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._focus_moved)

    # -- pages ---------------------------------------------------------------

    @staticmethod
    def _label(text: str, name: str, wrap: bool = True, centred: bool = False) -> QLabel:
        lbl = QLabel(text, objectName=name)
        lbl.setWordWrap(wrap)
        if centred:
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return lbl

    def _build_welcome_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(8)
        self._hero = self._label(self._GREETINGS[0], "Hero", wrap=False, centred=True)
        self._w_sub = self._label(tr("onb_welcome_sub"), "OnbSub", centred=True)

        self._lang_label = QLabel(tr("onb_choose_language"), objectName="OnbSub")
        self._lang_combo = QComboBox()
        for code, name in LANGUAGES.items():
            self._lang_combo.addItem(name, code)
        cur = self._settings.value("language", "en") if self._settings else "en"
        i = self._lang_combo.findData(cur)
        if i >= 0:
            self._lang_combo.setCurrentIndex(i)
        self._lang_combo.currentIndexChanged.connect(self._language_picked)
        self._lang_label.setBuddy(self._lang_combo)

        self._w_next = QPushButton(tr("onb_get_started"), objectName="Primary")
        self._w_next.setMinimumHeight(38)
        self._w_next.clicked.connect(self._get_started)
        # Quiet ways in, under the one button: the demo, and settings from
        # another computer (the Bring step) instead of starting from nothing.
        self._w_demo = _Link(tr("onb_demo_link"))
        self._w_demo.clicked.connect(self._demo)
        self._w_bring = _Link(tr("onb_bring_offer"))
        self._w_bring.clicked.connect(self._open_bring)
        # Nerd, offered quietly: a small link under it all.
        self._w_nerd = QPushButton(objectName="OnbNerd")
        self._w_nerd.setFlat(True)
        self._w_nerd.setCursor(Qt.CursorShape.PointingHandCursor)
        self._w_nerd.setStyleSheet(
            f"QPushButton#OnbNerd {{ background: transparent; border: none;"
            f" color: {P['muted2']}; font-size: 11px; padding: 4px 0; }}"
            f"QPushButton#OnbNerd:hover, QPushButton#OnbNerd:focus {{ color: {P['text']};"
            " text-decoration: underline; }")
        self._w_nerd.clicked.connect(self._toggle_nerd)

        lay.addWidget(self._hero)
        lay.addWidget(self._w_sub)
        lay.addSpacing(4)
        lay.addWidget(self._lang_label)
        lay.addWidget(self._lang_combo)
        lay.addSpacing(6)
        lay.addWidget(self._w_next)
        lay.addSpacing(2)
        lay.addWidget(self._w_demo)
        lay.addWidget(self._w_bring)
        lay.addWidget(self._w_nerd, 0, Qt.AlignmentFlag.AlignHCenter)
        self._sync_nerd_link()
        return page

    def _build_choose_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(8)
        self._ch_title = self._label(tr("onb_choose_title"), "OnbTitle")
        # After a sync that brought settings but no source (sync_no_sources).
        self._c_note = self._label("", "OnbNote")
        self._c_note.setVisible(False)
        self._cards: dict[str, _Choice] = {}
        lay.addWidget(self._ch_title)
        lay.addWidget(self._c_note)
        for family in (IPTV, MEDIA, BOX, FILES):
            card = _Choice()
            card.clicked.connect(lambda _c=False, f=family: self._choose(f))
            self._cards[family] = card
            lay.addWidget(card)
        self._card_words()
        # The demo where Get started leads, too, not only on the first page.
        self._ch_demo = _Link(tr("onb_demo_link"))
        self._ch_demo.clicked.connect(self._demo)
        lay.addWidget(self._ch_demo)
        row = QHBoxLayout()
        self._ch_back = QPushButton(tr("onb_back"))
        self._ch_back.clicked.connect(lambda: self._stack.setCurrentIndex(PAGE_WELCOME))
        row.addWidget(self._ch_back)
        row.addStretch(1)
        lay.addLayout(row)
        return page

    def _card_words(self) -> None:
        for family, (title, detail) in {
                IPTV: ("onb_choose_iptv", "onb_choose_iptv_sub"),
                MEDIA: ("onb_choose_media", "onb_choose_media_sub"),
                BOX: ("onb_choose_box", "onb_choose_box_sub"),
                FILES: ("onb_choose_files", "onb_choose_files_sub")}.items():
            self._cards[family].set_words(tr(title), tr(detail))

    def _build_form_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(7)
        self._c_title = self._label("", "OnbTitle")
        # The kind's tip at the top of the form: paste a whole link, a
        # playlist file, a box found for you.
        self._c_hint = self._label("", "OnbTip")

        form = QFormLayout()
        form.setSpacing(8)
        self._conn_kind = QComboBox()
        # A long kind's name ("Enigma2 box (VU+, Dreambox, Zgemma...)") is
        # cut short rather than widening the form past the card.
        self._conn_kind.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._conn_kind.setMinimumContentsLength(12)
        self._conn_kind.currentIndexChanged.connect(self._update_conn_kind)
        # The IPTV card's kinds to begin with, before each kind keeps its
        # own fields (KindOwnFields, below): what the settings hold from
        # before (an older version's login) stays Xtream's.
        self._filling = True
        for k in family_kinds(IPTV):
            self._conn_kind.addItem(tr(KIND_LABELS.get(k, f"playlist_kind_{k}")), k)
        self._filling = False
        # Empty: never what the settings keep of a source from before (its
        # login - the last source's, app.py writes it there).
        self._name = QLineEdit()
        self._name.setPlaceholderText(tr("playlist_name_hint"))
        self._server = QLineEdit()
        self._server.setPlaceholderText("http://server:port")
        # A single pasted link is recognised as Xtream (fans out into
        # server/username/password) or M3U (a playlist's address or file)
        # and the kind follows; manual entry is untouched. textEdited fires
        # on every user edit (paste too) but NOT on our own setText.
        self._server.textEdited.connect(self._server_typed)
        self._browse = QPushButton(tr("onb_choose_file"))
        self._browse.setAutoDefault(False)
        self._browse.clicked.connect(self._pick_file)
        server_row = self._server_row = QWidget()
        srl = QHBoxLayout(server_row)
        srl.setContentsMargins(0, 0, 0, 0)
        srl.setSpacing(6)
        srl.addWidget(self._server, 1)
        srl.addWidget(self._browse)
        self._user = QLineEdit()
        self._pw = QLineEdit()
        self._pw.setEchoMode(QLineEdit.EchoMode.Password)
        # A Stalker / Ministra portal logs in with the box's MAC address.
        self._mac = QLineEdit()
        self._mac.setPlaceholderText("00:1A:79:12:34:56")
        for field in (self._name, self._server, self._user, self._pw, self._mac):
            field.returnPressed.connect(self._do_connect)
        self._lbl_kind = QLabel(tr("playlist_kind"))
        self._lbl_name = QLabel(tr("playlist_name"))
        self._lbl_server = QLabel(tr("login_server"))
        self._lbl_user = QLabel(tr("login_username"))
        self._lbl_pw = QLabel(tr("login_password"))
        self._lbl_mac = QLabel(tr("playlist_mac"))
        form.addRow(self._lbl_kind, self._conn_kind)
        form.addRow(self._lbl_server, server_row)
        # For a box on the home network and a media server.
        self._finder = FindOnNetwork(page)
        self._finder.picked.connect(self._box_found)
        form.addRow("", self._finder.row)
        # Nerd: devices and servers with known playlist / guide addresses.
        from .source_presets import PresetButton
        self._presets = PresetButton(self._preset_picked, page)
        form.addRow("", self._presets)
        form.addRow(self._lbl_user, self._user)
        form.addRow(self._lbl_pw, self._pw)
        # A media server is signed in to here (ui/media_signin.py).
        self._signin = MediaSignIn(page, self._server, self._user, self._pw)
        # Connect signs in (a name and password, Plex's PIN in the
        # browser): one primary button - Quick Connect stays its own way.
        self._signin.guided()
        self._signin.changed.connect(self._signin_changed)
        self._signin.finished.connect(self._signin_finished)
        self._server.textChanged.connect(self._signin.form_changed)
        self._user.textChanged.connect(self._signin.form_changed)
        # Each kind its own address and login: an Xtream login filled in
        # above is not a Jellyfin's or a box's.
        self._kind_own = KindOwnFields(
            self._conn_kind, {"server": self._server, "user": self._user, "pw": self._pw,
                              "mac": self._mac}, self._signin)
        self._connect_signed = False
        form.addRow("", self._signin)
        form.addRow(self._lbl_mac, self._mac)
        form.addRow(self._lbl_name, self._name)

        self._c_err = self._label("", "OnbErr")
        self._c_err.setTextFormat(Qt.TextFormat.PlainText)
        # Hidden until there is an error, so the empty line doesn't push a
        # gap under the Connect button.
        self._c_err.setVisible(False)
        self._c_connect = QPushButton(tr("btn_connect"), objectName="Primary")
        self._c_connect.setMinimumHeight(36)
        self._c_connect.clicked.connect(self._do_connect)

        lay.addWidget(self._c_title)
        lay.addWidget(self._c_hint)
        lay.addLayout(form)
        lay.addWidget(self._c_err)
        lay.addWidget(self._c_connect)
        lay.addSpacing(2)
        row = QHBoxLayout()
        self._c_back = QPushButton(tr("onb_back"))
        self._c_back.clicked.connect(self._form_back)
        row.addWidget(self._c_back)
        row.addStretch(1)
        lay.addLayout(row)
        return page

    def _build_work_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(8)
        self._k_title = self._label("", "OnbTitle")
        self._k_bar = QProgressBar(objectName="OnbBar")
        self._k_bar.setRange(0, 0)               # busy: no end known
        self._k_bar.setTextVisible(False)
        self._k_bar.setFixedHeight(6)
        self._k_step = self._label("", "OnbSub")
        # What came, or why not: the app's own words (connect_check).
        self._k_text = self._label("", "OnbSub")
        self._k_text.setTextFormat(Qt.TextFormat.PlainText)
        self._k_warn = self._label("", "OnbWarn")
        self._k_warn.setTextFormat(Qt.TextFormat.PlainText)
        self._k_go = a11y.words_later(QPushButton("", objectName="Primary"))
        self._k_go.setMinimumHeight(36)
        self._k_go.clicked.connect(self._work_go)
        row = QHBoxLayout()
        self._k_back = QPushButton(tr("onb_back"))
        self._k_back.clicked.connect(self._work_back)
        row.addWidget(self._k_back)
        row.addStretch(1)
        lay.addWidget(self._k_title)
        lay.addWidget(self._k_bar)
        lay.addWidget(self._k_step)
        lay.addWidget(self._k_text)
        lay.addWidget(self._k_warn)
        lay.addSpacing(4)
        lay.addWidget(self._k_go)
        lay.addLayout(row)
        return page

    def _build_bring_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(7)
        self._b_title = QLabel(tr("onb_bring_title"), objectName="OnbTitle")
        self._b_title.setWordWrap(True)
        self._b_sub = QLabel(tr("onb_bring_sub", page=tr("sync_title")), objectName="OnbSub")
        self._b_sub.setWordWrap(True)
        # Typed or pasted the way Settings > Sync takes it (any case, any
        # separators); checked by the window with core/sync.parse_code.
        self._b_code = QLineEdit()
        self._b_code.setPlaceholderText(tr("sync_code"))
        self._b_code.setAccessibleName(tr("sync_code"))
        self._b_code.returnPressed.connect(self._ask_sync)
        self._b_code.textEdited.connect(self._bring_edited)
        self._b_go = QPushButton(tr("onb_bring_go"), objectName="Primary")
        self._b_go.setMinimumHeight(34)
        self._b_go.clicked.connect(self._ask_sync)
        self._b_wait = QLabel(tr("onb_bring_fetching"), objectName="OnbSub")
        self._b_wait.setWordWrap(True)
        self._b_wait.setVisible(False)
        self._b_err = QLabel("", objectName="OnbErr")
        self._b_err.setTextFormat(Qt.TextFormat.PlainText)     # what the site answered
        self._b_err.setWordWrap(True)
        self._b_err.setVisible(False)
        # What dopeiptv.org gets, in Settings > Sync's words: a caption.
        self._b_private = QLabel(tr("onb_bring_private"), objectName="OnbOr")
        self._b_private.setWordWrap(True)
        self._b_private.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._b_or = QLabel(tr("onb_or"), objectName="OnbOr")
        self._b_or.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._b_restore = QPushButton(tr("onb_restore_backup"))
        self._b_restore.clicked.connect(self._ask_restore)
        self._b_rerr = QLabel("", objectName="OnbErr")
        self._b_rerr.setTextFormat(Qt.TextFormat.PlainText)
        self._b_rerr.setWordWrap(True)
        self._b_rerr.setVisible(False)
        # A backup staged: put in place as the app next starts.
        self._b_ready = QLabel("", objectName="OnbOk")
        self._b_ready.setWordWrap(True)
        self._b_ready.setVisible(False)
        self._b_quit = QPushButton(tr("onb_restore_quit"), objectName="Primary")
        self._b_quit.setMinimumHeight(34)
        self._b_quit.setVisible(False)
        self._b_quit.clicked.connect(self._ask_quit)

        lay.addWidget(self._b_title)
        lay.addWidget(self._b_sub)
        lay.addSpacing(2)
        lay.addWidget(self._b_code)
        lay.addWidget(self._b_go)
        lay.addWidget(self._b_wait)
        lay.addWidget(self._b_err)
        lay.addWidget(self._b_private)
        lay.addSpacing(4)
        lay.addWidget(self._b_or)
        lay.addWidget(self._b_restore)
        lay.addWidget(self._b_rerr)
        lay.addWidget(self._b_ready)
        lay.addWidget(self._b_quit)
        lay.addSpacing(2)
        row = QHBoxLayout()
        self._b_back = QPushButton(tr("onb_back"))
        self._b_back.clicked.connect(self._bring_back)
        row.addWidget(self._b_back)
        row.addStretch(1)
        lay.addLayout(row)
        self._b_busy = False
        self._b_allowed = True
        self._b_staged = False
        return page

    # -- Nerd ------------------------------------------------------------------

    def _nerd_on(self) -> bool:
        return bool(self._settings) and self._settings.value("nerd_enabled", "false") == "true"

    def _toggle_nerd(self) -> None:
        if not self._settings or self._kids() or not self._guard():
            return
        self._settings.setValue("nerd_enabled", "false" if self._nerd_on() else "true")
        from ..core import nerd
        nerd.install(self._settings)
        self._sync_nerd_link()
        self._update_conn_kind()

    def _sync_nerd_link(self) -> None:
        self._w_nerd.setText(tr("onb_nerd_on_now") if self._nerd_on() else tr("onb_nerd_offer"))
        # A kids profile is offered no Nerd (mw_profiles' rule for Settings).
        self._w_nerd.setVisible(not self._kids())

    # -- the choice and the form -----------------------------------------------

    def _get_started(self) -> None:
        self._stack.setCurrentIndex(PAGE_CHOOSE)

    def _choose(self, family: str) -> None:
        """A card picked: its form - or, for files and radio, no source."""
        self._drop_signin()
        if family == FILES:
            self._leave()
            self._on_files()
            return
        self._fill_kinds(family)
        self._hide_error()
        self._stack.setCurrentIndex(PAGE_FORM)

    def _fill_kinds(self, family: str, kind: str | None = None) -> None:
        """The form's kinds: *family*'s, *kind* (else the first) picked. What
        was typed for each kind stays with it (KindOwnFields)."""
        kinds = family_kinds(family)
        if not kinds:
            return
        want = kind if kind in kinds else None
        if family == self._family and self._conn_kind.count() == len(kinds):
            if want is not None:
                self._conn_kind.setCurrentIndex(self._conn_kind.findData(want))
            self._update_conn_kind()
            return
        self._family = family
        self._filling = True
        try:
            self._conn_kind.clear()
            for k in kinds:
                self._conn_kind.addItem(tr(KIND_LABELS.get(k, f"playlist_kind_{k}")), k)
            if want is not None:
                self._conn_kind.setCurrentIndex(self._conn_kind.findData(want))
        finally:
            self._filling = False
        self._update_conn_kind()

    def _set_kind(self, kind: str) -> None:
        """*kind* picked - from another card's kinds too (a link pasted, a
        server found on the network): the form follows."""
        family = family_of(kind)
        if not family:
            return
        if family != self._family:
            self._fill_kinds(family, kind)
            return
        idx = self._conn_kind.findData(kind)
        if idx >= 0 and idx != self._conn_kind.currentIndex():
            self._conn_kind.setCurrentIndex(idx)   # fires _update_conn_kind

    def _heading(self, kind: str) -> str:
        if kind == "xtream":
            return tr("onb_head_xtream")
        if kind == "m3u":
            return tr("onb_head_m3u")
        if kind == "stalker":
            return tr("onb_head_stalker")
        name = SHORT_NAMES.get(kind, kind)
        return tr("onb_head_media", name=name) if kind in MEDIA_KINDS else tr("onb_head_box", name=name)

    def _tip(self, kind: str) -> str:
        if kind == "xtream":
            return tr("onb_tip_xtream")
        if kind == "m3u":
            return tr("onb_tip_m3u")
        if kind in BOX_KINDS:
            return tr("onb_tip_box")
        return ""

    def _update_conn_kind(self) -> None:
        if self._filling:
            return
        kind = self._conn_kind.currentData() or ""
        if kind != self._extra_kind:
            # What a found server or a preset brought was for its own kind.
            self._extra, self._extra_kind = {}, ""
        m3u, stalker = kind == "m3u", kind == "stalker"
        box = kind in BOX_KINDS
        media = kind in MEDIA_KINDS
        self._c_title.setText(self._heading(kind))
        tip = self._tip(kind)
        self._c_hint.setText(tip)
        self._c_hint.setVisible(bool(tip))
        # Only one kind to pick (never here), or the card already said it.
        self._lbl_kind.setVisible(self._conn_kind.count() > 1)
        self._conn_kind.setVisible(self._conn_kind.count() > 1)
        # M3U needs only an address or a file, a portal its address and a
        # MAC address, a box on the home network its address and, if it
        # asks, a login, a media server a sign-in (Plex's in the browser,
        # the address from it).
        self._lbl_server.setText(
            tr("playlist_m3u_url") if m3u
            else tr("playlist_portal_url") if stalker
            else tr("playlist_box_address") if box or media else tr("login_server"))
        self._server.setPlaceholderText(
            "https://example.com/playlist.m3u" if m3u
            else "http://portal.example.com/c/" if stalker
            else PLACEHOLDERS.get(kind, "http://server:port"))
        for w in (self._lbl_server, self._server_row, self._server):
            w.setVisible(kind != "plex")
        self._browse.setVisible(m3u)
        login = (not (m3u or stalker or box or media) or kind in LOGIN_KINDS
                 or kind in ("jellyfin", "emby"))
        pin = kind in PIN_KINDS
        for w in (self._lbl_user, self._user):
            w.setVisible(login and not pin)
        for w in (self._lbl_pw, self._pw):
            w.setVisible(login)
        self._lbl_pw.setText(tr("login_pin") if pin else tr("login_password"))
        hint = tr("playlist_login_optional") if box else ""
        self._user.setPlaceholderText(hint)
        self._pw.setPlaceholderText(tr("playlist_pin_hint") if pin else hint)
        for w in (self._lbl_mac, self._mac):
            w.setVisible(stalker)
        self._finder.row.setVisible(box or media)
        self._finder.wanted = MEDIA_KINDS if media else BOX_KINDS
        if not (box or media):
            self._finder.status.hide()
        self._presets.setVisible(box and self._nerd_on())
        self._signin.set_kind(kind)
        if not self._signin.busy:
            # Another kind stopped the sign-in Connect asked for: a later
            # one (Quick Connect) does not connect by itself.
            self._connect_signed = False
        # A media server signs in, then connects: Connect does both.
        self._c_connect.setVisible(True)
        self._name_fields()
        self._fit_card(self._stack.currentIndex())

    def _name_fields(self) -> None:
        """Each field read by the label beside it - a QLabel of its own,
        which Qt does not tie to the field as a form row's words."""
        for lbl, field in ((self._lbl_kind, self._conn_kind), (self._lbl_name, self._name),
                           (self._lbl_server, self._server), (self._lbl_user, self._user),
                           (self._lbl_pw, self._pw), (self._lbl_mac, self._mac),
                           (self._lang_label, self._lang_combo)):
            field.setAccessibleName(lbl.text())
        code = getattr(self, "_b_code", None)       # the Bring page, built after this one
        if code is not None:
            code.setAccessibleName(tr("sync_code"))
        bar = getattr(self, "_k_bar", None)
        if bar is not None:
            bar.setAccessibleName(tr("ms_signing_in"))

    def _pick_file(self) -> None:
        """An M3U file on this computer, picked: its path in the field."""
        path, _flt = QFileDialog.getOpenFileName(
            self, tr("onb_choose_file"), "", tr("tab_playlists") + " (*.m3u *.m3u8 *.M3U *.M3U8)")
        if path:
            self._server.setText(path)
            self._server.setFocus()

    def _signin_changed(self) -> None:
        self._fit_card(self._stack.currentIndex())

    def _signin_finished(self, ok: bool) -> None:
        """A sign-in Connect asked for has ended: connect if it worked -
        only while the guide is up on its form (closed, or left for another
        page meanwhile, nothing is tried and nothing kept)."""
        pending, self._connect_signed = self._connect_signed, False
        if (pending and ok and self._signin.signed_in() and self.isVisible()
                and self._stack.currentIndex() == PAGE_FORM):
            self._connect_media(str(self._conn_kind.currentData()))
        self._fit_card(self._stack.currentIndex())

    def _drop_signin(self) -> None:
        """A sign-in under way stopped (the form's Back, another card, the
        guide closed): its answer, whenever it comes, connects nothing."""
        self._connect_signed = False
        self._signin.cancel()

    def _form_back(self) -> None:
        """The form's Back: the choice of what to watch - what was typed
        kept, a sign-in under way stopped."""
        self._drop_signin()
        self._stack.setCurrentIndex(PAGE_CHOOSE)

    def _connect_media(self, kind: str) -> None:
        got = self._signin.values()
        name = self._name.text().strip() or got["name"]
        self._hide_error()
        self._start(got["server"], got["username"], "", kind, name, "",
                    {"token": got["token"], "options": got["options"]})

    def _box_found(self, found: dict) -> None:
        self._set_kind(found.get("kind", ""))
        self._server.setText(found.get("server", ""))
        if not self._name.text().strip():
            self._name.setText(str(found.get("name", "")).split(" - ")[0])
        self._extra, self._extra_kind = {}, str(found.get("kind") or "")
        if found.get("kind") == "satip" and found.get("description"):
            self._extra = {"options": {"description": str(found.get("description"))}}
        if found.get("login") or found.get("kind") in ("jellyfin", "emby"):
            (self._pw if found.get("kind") in PIN_KINDS else self._user).setFocus()
        if found.get("kind") in MEDIA_KINDS:
            self._signin.prefer(found)
        self._fit_card(self._stack.currentIndex())

    def _preset_picked(self, src: dict) -> None:
        """Nerd's preset: its kind, address and guide (and a SAT>IP
        server's lists)."""
        self._set_kind(str(src.get("kind") or ""))
        self._server.setText(str(src.get("server") or ""))
        extra: dict = {}
        if src.get("epg_url"):
            extra["epg_url"] = str(src["epg_url"])
        if src.get("m3u"):
            extra["options"] = {"m3u": str(src["m3u"])}
        self._extra, self._extra_kind = extra, str(src.get("kind") or "")
        if not self._name.text().strip():
            self._name.setText(str(src.get("name") or ""))

    def _server_typed(self, _text: str = "") -> None:
        """The address typed or pasted by hand: what a found server or a
        preset brought with it goes - then a whole link is read."""
        self._extra, self._extra_kind = {}, ""
        self._maybe_autodetect_link()

    def _maybe_autodetect_link(self) -> None:
        """Recognise a pasted provider link in the address field and set
        the form up for it: Xtream (split into server/username/password,
        preferred) or M3U (a playlist's address or file). A bare host with no
        link shape is left alone so manual entry keeps working. A portal's,
        a box's or a media server's address is never taken for one."""
        if self._conn_kind.currentData() not in ("xtream", "m3u"):
            return
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
        if self._trying or self._stack.currentIndex() != PAGE_FORM:
            return
        if not self._guard():
            return
        kind = self._conn_kind.currentData()
        server = self._server.text().strip()
        name = self._name.text().strip()
        if kind == "stalker":
            mac = PlaylistDialog.normal_mac(self._mac.text())
            if not (server and mac):
                self._show_error(tr("playlist_portal_required"))
                return
            self._hide_error()
            self._start(server, "", "", kind, name, mac)
            return
        if kind in MEDIA_KINDS:
            if self._signin.signed_in():
                self._connect_media(kind)
            elif self._signin.busy:
                return
            elif kind == "plex":
                # Plex signs in with a PIN in the browser: Connect starts it,
                # and connects once it is done.
                self._signin.start_plex()
                self._connect_signed = True
                self._hide_error()
            elif self._signin.sign_in():
                # Name and password typed: Connect signs in, then connects.
                self._connect_signed = True
                self._hide_error()
            else:
                self._show_error(tr("onb_fill_media"))
            return
        if kind in BOX_KINDS:
            if not server:
                self._show_error(tr("playlist_box_required"))
                return
            self._hide_error()
            user = self._user.text().strip() if kind in LOGIN_KINDS and kind not in PIN_KINDS else ""
            pw = self._pw.text().strip() if kind in LOGIN_KINDS else ""
            self._start(server, user, pw, kind, name)
            return
        if kind == "m3u":
            if not server:
                self._show_error(tr("onb_fill_m3u"))
                return
            user = pw = ""
        else:
            user = self._user.text().strip()
            pw = self._pw.text().strip()
            if not (server and user and pw):
                self._show_error(tr("onb_fill_all"))
                return
        self._hide_error()
        self._start(server, user, pw, kind, name)

    # -- connecting ------------------------------------------------------------

    def _start(self, server: str, user: str, pw: str, kind: str, name: str,
               mac: str = "", signed: dict | None = None) -> None:
        """The source tried: the connect step shows, the window tries it."""
        if self._trying:
            return
        extra = (dict(self._extra) if kind in ("m3u", "satip") and kind == self._extra_kind
                 else {})
        self._last_try = (server, user, pw, kind, name, mac, signed, extra)
        self._trying, self._done = True, False
        from ..core.names import source_name
        shown = name or source_name(server) or SHORT_NAMES.get(kind, "")
        self._k_title.setText(tr("onb_connecting", name=shown))
        self._k_title.setAccessibleName(self._k_title.text())
        self._k_bar.setVisible(True)
        self._k_step.setText(tr("ms_signing_in"))
        self._k_step.setVisible(True)
        self._k_text.setVisible(False)
        self._k_warn.setVisible(False)
        self._k_go.setVisible(False)
        self._k_back.setText(tr("onb_back"))
        self._k_back.setVisible(True)
        self._stack.setCurrentIndex(PAGE_WORK)
        self._k_back.setFocus()
        self._on_connect(server, user, pw, kind, name, mac, signed, extra)

    def connect_step(self, step: str) -> None:
        """The window: what the try is doing now (connect_check.STEPS)."""
        if self._trying and step in STEP_WORDS:
            self._k_step.setText(tr(STEP_WORDS[step]))
            self._k_bar.setAccessibleName(self._k_step.text())

    def connect_done(self, channels: int, movies: int | None = None,
                     series: int | None = None, warning: str = "") -> None:
        """The window: the source worked and is kept - what came."""
        self._trying, self._done = False, True
        loc = QLocale()

        def count(key: str, n: int) -> str:
            return tr(f"{key}_one" if n == 1 else key, n=loc.toString(int(n)))

        parts = [count("count_channels", channels)] if channels else []
        if movies:
            parts.append(count("count_movies", movies))
        if series:
            parts.append(count("count_series", series))
        self._k_title.setText(tr("onb_done_title"))
        self._k_bar.setVisible(False)
        self._k_step.setVisible(False)
        self._k_text.setText(tr("onb_done_counts", what=tr("onb_list_sep").join(parts)))
        self._k_text.setObjectName("OnbOk")
        self._restyle(self._k_text)
        self._k_text.setVisible(True)
        self._k_warn.setText(warning)
        self._k_warn.setVisible(bool(warning))
        self._k_go.setText(tr("onb_start_watching"))
        self._k_go.setVisible(True)
        self._k_back.setVisible(False)
        self._fit_card(PAGE_WORK)
        self._k_go.setFocus()

    def connect_failed(self, text: str) -> None:
        """The window: the source did not work - why, in plain words. Back
        to the form as it was typed, or try again."""
        if not self._trying:
            return
        self._trying, self._done = False, False
        self._k_title.setText(tr("onb_fail_title"))
        self._k_bar.setVisible(False)
        self._k_step.setVisible(False)
        self._k_text.setText(text)
        self._k_text.setObjectName("OnbErr")
        self._restyle(self._k_text)
        self._k_text.setVisible(True)
        self._k_warn.setVisible(False)
        self._k_go.setText(tr("sp_retry"))
        self._k_go.setVisible(True)
        self._k_back.setText(tr("onb_back"))
        self._k_back.setVisible(True)
        self._fit_card(PAGE_WORK)
        self._k_back.setFocus()

    @staticmethod
    def _restyle(w: QWidget) -> None:
        style = w.style()
        if style is not None:
            style.unpolish(w)
            style.polish(w)

    def trying(self) -> bool:
        return self._trying

    def _work_go(self) -> None:
        if self._done:
            self._finish()
            return
        if not self._trying and self._last_try:
            server, user, pw, kind, name, mac, signed, _extra = self._last_try
            self._start(server, user, pw, kind, name, mac, signed)

    def _work_back(self) -> None:
        """Back: the form, as it was typed - a try under way stopped."""
        self._cancel_connect()
        self._stack.setCurrentIndex(PAGE_FORM)

    def _cancel_connect(self) -> None:
        if self._trying:
            self._trying = False
            self.connect_cancelled.emit()

    def _show_error(self, text: str) -> None:
        self._c_err.setText(text)
        self._c_err.setVisible(True)
        self._fit_card(self._stack.currentIndex())
        # On a low window the page scrolls: the words and Connect brought
        # into sight once laid out (they were cut off at the card's foot).
        QTimer.singleShot(0, self._error_in_sight)

    def _error_in_sight(self) -> None:
        if not self._c_err.isVisible():
            return
        self._scroll.ensureWidgetVisible(self._c_connect, 0, 8)
        self._scroll.ensureWidgetVisible(self._c_err, 0, 8)

    def _hide_error(self) -> None:
        self._c_err.setText("")
        self._c_err.setVisible(False)

    # -- the Bring step: what the window says back ----------------------------

    def sync_failed(self, text: str) -> None:
        """The sync did not bring anything: why, and the code to try again -
        the keys back in its field with what was typed chosen (the field was
        off while it fetched, and Enter on Back left the page)."""
        self._set_busy(False)
        self._b_err.setText(text)
        self._b_err.setVisible(True)
        self._fit_card(self._stack.currentIndex())
        if self._b_code.isEnabled() and self.isVisible():
            self._b_code.setFocus(Qt.FocusReason.OtherFocusReason)
            self._b_code.selectAll()

    def sync_no_sources(self, saved: str = "") -> None:
        """Settings and profiles came, no source: the choice of what to
        watch, with a word on why and what to do - and *saved*, how old the
        copy was."""
        self._set_busy(False)
        self._b_code.clear()
        self._b_err.setVisible(False)
        self._c_note.setText(self._no_sources_text() + (f"\n\n{saved}" if saved else ""))
        self._c_note.setVisible(True)
        self._stack.setCurrentIndex(PAGE_CONNECT)
        self._fit_card(PAGE_CONNECT)

    @staticmethod
    def _no_sources_text() -> str:
        # The switch named as Settings > Sync names it.
        return tr("onb_sync_no_sources", what=tr("tab_playlists"), where=tr("sync_nav"))

    def finish(self) -> None:
        """Sources came along: the guide goes, the window loads them."""
        self._set_busy(False)
        self._b_code.clear()
        self._finish()

    def restore_failed(self, text: str) -> None:
        self._b_rerr.setText(text)
        self._b_rerr.setVisible(bool(text))
        self._fit_card(self._stack.currentIndex())

    def restore_staged(self) -> None:
        """A backup waits for the next start: said, with Quit to get there."""
        self._b_rerr.setVisible(False)
        self._b_ready.setText(tr("onb_restore_ready"))
        self._b_ready.setVisible(True)
        self._b_quit.setVisible(True)
        # The backup replaces everything as the app starts: a sync now
        # would be gone with it.
        self._b_staged = True
        self._set_busy(False)

    # -- the Bring step: what it asks -----------------------------------------

    def _open_bring(self) -> None:
        # A modified build may not use dopeiptv.org's sync: said as Settings
        # says it, the code's field off; a backup is this computer's alone.
        from ..core import build_check
        verdict = build_check.settled()
        self._b_allowed = verdict is None or build_check.allowed()
        if not self._b_allowed:
            self._b_err.setText(build_check.notice("sync"))
            self._b_err.setVisible(True)
        elif not self._b_busy:
            self._b_err.setVisible(False)
        self._set_busy(self._b_busy)
        self._stack.setCurrentIndex(PAGE_BRING)
        if self._b_allowed:
            self._b_code.setFocus()

    def _ask_sync(self) -> None:
        if self._b_busy or not self._b_allowed or self._b_staged:
            return
        text = self._b_code.text().strip()
        if not text:
            self._b_code.setFocus()
            return
        if not self._guard():
            return
        self._b_err.setVisible(False)
        self._set_busy(True)
        self.sync_asked.emit(text)

    def _bring_edited(self, _text: str = "") -> None:
        if self._b_err.isVisible() and self._b_allowed:
            self._b_err.setVisible(False)
            self._fit_card(self._stack.currentIndex())

    def _ask_restore(self) -> None:
        if self._b_busy or not self._guard():
            return
        self._b_rerr.setVisible(False)
        self.restore_asked.emit()

    def _ask_quit(self) -> None:
        self.quit_asked.emit()

    def _bring_back(self) -> None:
        """Back to the first page; a sync still fetching is stopped."""
        self._cancel_sync()
        self._stack.setCurrentIndex(PAGE_WELCOME)

    def _cancel_sync(self) -> None:
        if self._b_busy:
            self._set_busy(False)
            self.sync_cancelled.emit()

    def _set_busy(self, on: bool) -> None:
        self._b_busy = on
        ready = not on and self._b_allowed and not self._b_staged
        self._b_code.setEnabled(ready)
        self._b_go.setEnabled(ready)
        self._b_restore.setEnabled(not on)
        self._b_wait.setVisible(on)
        if on:
            self._b_err.setVisible(False)
        self._fit_card(self._stack.currentIndex())

    # -- actions -------------------------------------------------------------

    def _start_greeting(self) -> None:
        """The greeting goes once round the languages - not at all where
        the desktop asks for less motion - and rests on the first."""
        from .notice_widgets import reduced_motion
        self._greet_idx = 0
        self._hero.setText(self._GREETINGS[0])
        if reduced_motion():
            self._flash.stop()
        elif not self._flash.isActive():
            self._flash.start(1300)

    def _next_greeting(self) -> None:
        self._greet_idx = (self._greet_idx + 1) % len(self._GREETINGS)
        self._hero.setText(self._GREETINGS[self._greet_idx])
        if self._greet_idx == 0:
            self._flash.stop()          # once round, then still

    def _language_picked(self) -> None:
        code = self._lang_combo.currentData()
        if not code:
            return
        self._on_language_change(code)
        self._retranslate()

    def dismiss(self) -> None:
        """Esc - the main window's one Escape shortcut: the guide goes. A
        try under way is stopped (nothing of it kept), a sync still fetching
        and a sign-in too; a source that came is kept."""
        if self._done:
            self._finish()
            return
        self._explore()

    def _demo(self) -> None:
        self._leave()
        self._on_demo()

    def _explore(self) -> None:
        self._leave()
        self._on_explore()

    def _finish(self) -> None:
        self._leave()

    def _leave(self) -> None:
        """The guide goes, whichever way (Esc, the demo, just files and
        radio, a source that came): what it had under way stops - a sync
        still fetching, a try, a sign-in, nothing of them kept - and no
        login typed stays in it, a try's that failed neither."""
        self._flash.stop()
        self._cancel_sync()
        self._cancel_connect()
        self._drop_signin()
        self._forget_login()
        self.hide()

    def _forget_login(self) -> None:
        """A source kept: what was typed for it goes from the form - every
        kind's (KindOwnFields), the last try, what came with a found server.
        The guide opened again starts empty, never with its login."""
        self._last_try = ()
        self._extra, self._extra_kind = {}, ""
        self._kind_own.forget()
        with self._signin.filling():
            for field in (self._server, self._user, self._pw, self._mac, self._name):
                field.clear()
        self._signin.forget()

    def _retranslate(self) -> None:
        self.setAccessibleDescription(tr("onb_welcome_sub"))
        self._w_sub.setText(tr("onb_welcome_sub"))
        self._lang_label.setText(tr("onb_choose_language"))
        self._w_next.setText(tr("onb_get_started"))
        self._w_demo.set_plain(tr("onb_demo_link"))
        self._w_bring.set_plain(tr("onb_bring_offer"))
        self._sync_nerd_link()
        self._ch_title.setText(tr("onb_choose_title"))
        self._card_words()
        self._ch_demo.set_plain(tr("onb_demo_link"))
        self._ch_back.setText(tr("onb_back"))
        self._lbl_kind.setText(tr("playlist_kind"))
        self._lbl_name.setText(tr("playlist_name"))
        self._name.setPlaceholderText(tr("playlist_name_hint"))
        self._lbl_user.setText(tr("login_username"))
        self._lbl_mac.setText(tr("playlist_mac"))
        self._browse.setText(tr("onb_choose_file"))
        for i in range(self._conn_kind.count()):
            k = self._conn_kind.itemData(i)
            self._conn_kind.setItemText(i, tr(KIND_LABELS.get(k, f"playlist_kind_{k}")))
        self._finder.retranslate()
        self._signin.retranslate()
        self._presets.setText(tr("nerd_presets"))
        self._c_connect.setText(tr("btn_connect"))
        self._c_back.setText(tr("onb_back"))
        self._k_back.setText(tr("onb_back"))
        self._update_conn_kind()   # re-labels the form for its kind
        self._c_note.setText(self._no_sources_text())
        self._b_title.setText(tr("onb_bring_title"))
        self._b_sub.setText(tr("onb_bring_sub", page=tr("sync_title")))
        self._b_code.setPlaceholderText(tr("sync_code"))
        self._b_go.setText(tr("onb_bring_go"))
        self._b_wait.setText(tr("onb_bring_fetching"))
        self._b_private.setText(tr("onb_bring_private"))
        self._b_or.setText(tr("onb_or"))
        self._b_restore.setText(tr("onb_restore_backup"))
        self._b_ready.setText(tr("onb_restore_ready"))
        self._b_quit.setText(tr("onb_restore_quit"))
        self._b_back.setText(tr("onb_back"))
        if not self._b_allowed:
            from ..core import build_check
            self._b_err.setText(build_check.notice("sync"))
        self._fit_card(self._stack.currentIndex())

    # -- geometry ------------------------------------------------------------

    def _page_shown(self, idx: int) -> None:
        self._fit_card(idx)
        self._page_shown_focus(idx)

    def _page_shown_focus(self, idx: int) -> None:
        """The page's first control takes the keys: Tab and Enter from there."""
        if not self.isVisible():
            return
        first: QWidget | None = {PAGE_WELCOME: self._w_next,
                                 PAGE_CHOOSE: self._cards[IPTV]}.get(idx)
        if idx == PAGE_FORM:
            first = self._server if self._server.isVisibleTo(self._stack.widget(PAGE_FORM)) \
                else self._c_connect
        elif idx == PAGE_WORK:
            first = self._k_go if self._k_go.isVisibleTo(self._stack.widget(PAGE_WORK)) \
                else self._k_back
        elif idx == PAGE_BRING and self._b_code.isEnabled():
            first = self._b_code
        if first is not None:
            first.setFocus(Qt.FocusReason.OtherFocusReason)

    @staticmethod
    def _page_height(page: QWidget, width: int) -> int:
        """The height *page* needs at *width*. Its size hint alone fell
        short wherever text wraps: before the page had been laid out at the
        card's width (the first time the guide shows), a wrapped label
        asked for the height of fewer lines and was cut off - the box
        finder's note in the middle of a sentence."""
        # Asked, not resized: adjustSize() here gave the page its hint's
        # width, wider than the card, and the stack never laid it back.
        lay = page.layout()
        if lay is not None:
            lay.invalidate()
        if lay is not None and lay.hasHeightForWidth():
            return max(1, lay.heightForWidth(width))
        return page.sizeHint().height()

    def _room(self) -> int:
        """The height the pages may take before they scroll: what the
        overlay leaves inside the card; 0 while it is not up yet."""
        if not self.isVisible() or self.height() <= 0:
            return 0
        m = self._outer.contentsMargins()
        card = self._card.layout().contentsMargins()
        return max(120, self.height() - m.top() - m.bottom()
                   - card.top() - card.bottom() - 2 * self.MARGIN)

    def _fit_card(self, idx: int, cover: bool = True) -> None:
        # A QStackedWidget always reserves the height of its tallest page.
        # Pin it to the current page's height so the short welcome page isn't
        # padded out to the size of the connect form.
        cur = self._stack.widget(idx)
        if cur is None:
            return
        # Inside the card's 1 px border too: two pixels more were a line
        # fewer than the page then had (a long note cut off at its end).
        width = self.CARD_W - 2 * self.PAD - 2 * self._card.frameWidth()
        need = self._page_height(cur, width)
        room = self._room()
        if room and need > room:
            # It scrolls: the bar takes its width from the page, which then
            # wraps into more lines.
            width -= self._scroll.verticalScrollBar().sizeHint().width()
            need = self._page_height(cur, width)
        self._stack.setFixedHeight(need)
        self._scroll.setFixedHeight(min(need, room) if room else need)
        self._card.adjustSize()
        if cover and self.isVisible():
            self.cover()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_card(self._stack.currentIndex(), cover=False)

    # -- the keys -------------------------------------------------------------

    def event(self, e) -> bool:  # noqa: D401 - Qt's hook
        """The window's own shortcuts wait while the keys are in the guide:
        a key is the guide's (a field's letter, Tab, Enter), never TV mode's
        T, the palette's Ctrl+K or a channel's digit acting under it. Esc
        stays the window's (it closes the guide: _on_escape), and Quit."""
        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QKeySequence
        if e.type() == QEvent.Type.ShortcutOverride and self.isVisible():
            alt_only = (e.modifiers() & Qt.KeyboardModifier.AltModifier
                        and not e.modifiers() & Qt.KeyboardModifier.ControlModifier)
            # Esc, Quit and the menus' Alt keys stay the window's.
            if (e.key() != Qt.Key.Key_Escape and not alt_only
                    and not e.matches(QKeySequence.StandardKey.Quit)):
                e.accept()
                return True
        return super().event(e)

    def _stops(self) -> list[QWidget]:
        """The page's Tab stops, in the window's focus order."""
        out: list[QWidget] = []
        start = self
        w = start.nextInFocusChain()
        while w is not None and w is not start and len(out) < 400:
            if (self.isAncestorOf(w) and w.isVisible() and w.isEnabled()
                    and w.focusPolicy() & Qt.FocusPolicy.TabFocus
                    and w.focusProxy() is None):
                out.append(w)
            w = w.nextInFocusChain()
        return out

    def focusNextPrevChild(self, next: bool) -> bool:  # noqa: N802 - Qt's name
        """Tab and Shift+Tab go round the page's own controls - never to
        the window's under it, which cannot be seen."""
        stops = self._stops()
        if not stops:
            return super().focusNextPrevChild(next)
        held = QApplication.focusWidget()
        try:
            i = stops.index(held)
        except ValueError:
            i = -1 if next else 0
        target = stops[(i + (1 if next else -1)) % len(stops)]
        target.setFocus(Qt.FocusReason.TabFocusReason if next
                        else Qt.FocusReason.BacktabFocusReason)
        return True

    def _focus_moved(self, _old, new) -> None:
        """The window gave the keys to something under the guide: back to
        the page (a menu, a dialog or another window keeps them)."""
        if new is None or not self.isVisible():
            return
        if new is self or self.isAncestorOf(new) or new.window() is not self.window():
            return
        if isinstance(new, (QMenu, QMenuBar)):
            return
        QTimer.singleShot(0, self._take_keys)

    def _take_keys(self) -> None:
        held = QApplication.focusWidget()
        if not self.isVisible() or held is None or held is self or self.isAncestorOf(held):
            return
        if held.window() is not self.window() or isinstance(held, (QMenu, QMenuBar)):
            return
        self._page_shown_focus(self._stack.currentIndex())

    def reset(self, page: int = PAGE_WELCOME) -> None:
        """Back to *page* (the first, or the choice of what to watch) with
        the greeting flashing again - when the guide is opened again."""
        self._stack.setCurrentIndex(page)
        self._c_err.setText("")
        self._c_err.setVisible(False)
        self._c_note.setVisible(False)
        self._b_err.setVisible(False)
        self._b_rerr.setVisible(False)
        self._set_busy(False)
        self._trying = self._done = False
        self._sync_nerd_link()
        self._start_greeting()

    def cover(self) -> None:
        """Resize to fully cover the parent window's content area, on top."""
        parent = self.parent()
        central = (parent.centralWidget()
                   if hasattr(parent, "centralWidget") else None)
        self.setGeometry(central.geometry() if central else parent.rect())
        # The profile may have changed while it was hidden (a kids one).
        self._sync_nerd_link()
        self.raise_()
        self.show()
        self._fit_card(self._stack.currentIndex(), cover=False)
        # The keys to the page's first control - unless they are in the
        # guide already (a resize of the window covers it again).
        held = QApplication.focusWidget()
        if held is None or not (held is self or self.isAncestorOf(held)):
            self.setFocus()
        if held is None or held is self or not self.isAncestorOf(held):
            self._page_shown_focus(self._stack.currentIndex())
