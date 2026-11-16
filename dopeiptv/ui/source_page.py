"""A source's own page in Settings (CONTENT › Sources › the source): a
header - its initials, name, kind and account, Test connection - over five
tabs: Connection (address, login, what its kind asks), Network, Streams,
Guide and Advanced. A row that has a shared setting says whether this
source has its own or follows the shared one, and where that lives. The
Nerd rows - the whole Network tab - show only while Nerd is on.

A change is stored a moment after it is made, as on every Settings page,
when it can be: a rewrite rule or backup address that cannot work, or a
field the kind needs left empty, is said at the top and not stored. The
source in use connects again once its page is left. The fields and what
they mean are the Add dialog's (SourceFields in ui/dialogs.py), the Nerd
ones ui/source_nerd.py's and ui/source_net.py's; only where each goes is
this module's.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from PyQt6 import sip
from PyQt6.QtCore import QLocale, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PyQt6.QtWidgets import (
    QAbstractButton, QButtonGroup, QCheckBox, QComboBox, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSpinBox, QVBoxLayout, QWidget,
)

from ..core import catchup, channel_numbers, mirrors, nerd
from ..i18n import tr
from .box_finder import MEDIA_KINDS
from .dialogs import SourceFields
from .nav_widgets import source_initials
from .settings_ui import PAGE_MAX_WIDTH, SettingsCard, Stepper, align_controls
from .theme import P, _mix, ink_on

#: Each kind as the page and the list of sources name it.
KIND_NAMES = {"xtream": "Xtream", "m3u": "M3U", "stalker": "Stalker", "enigma2": "Enigma2",
              "tvheadend": "Tvheadend", "hdhomerun": "HDHomeRun", "vdr": "VDR",
              "dvbviewer": "DVBViewer", "nextpvr": "NextPVR", "mythtv": "MythTV",
              "satip": "SAT>IP", "jellyfin": "Jellyfin", "emby": "Emby", "plex": "Plex"}
TABS = ("conn", "net", "streams", "guide", "adv")
#: A kind's switches that are about its streams or its guide; the rest are
#: on Advanced.
STREAM_SWITCHES = {("enigma2", "stream_login"), ("enigma2", "iptv_direct"),
                   ("enigma2", "transcode_more")}
GUIDE_SWITCHES = {("enigma2", "picons"), ("hdhomerun", "cloud_guide")}
#: The latest connection test of each source this session, by its id.
LAST_TEST: dict[str, dict[str, Any]] = {}


def _arrow() -> str:
    return "‹" if QLocale().textDirection() == Qt.LayoutDirection.RightToLeft else "›"


def _where(*parts: str) -> str:
    return f" {_arrow()} ".join(parts)


def _stack(*widgets: QWidget) -> QVBoxLayout:
    v = QVBoxLayout()
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(6)
    for w in widgets:
        v.addWidget(w)
    return v


def _holder(*widgets: QWidget) -> QWidget:
    w = QWidget()
    w.setLayout(_stack(*widgets))
    return w


class _Tab(QAbstractButton):
    """A tab of the page: its name, a small "NERD" after it for a Nerd one,
    a line under it while it is the one open."""

    def __init__(self, text: str, tag: str = "") -> None:
        super().__init__()
        self.setText(text)
        self.setAccessibleName(text)
        self._tag = tag
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def _tag_font(self) -> QFont:
        f = QFont(self.font())
        f.setPixelSize(10)
        f.setWeight(QFont.Weight.Bold)
        return f

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        f = QFont(self.font())
        f.setWeight(QFont.Weight.DemiBold)
        fm = QFontMetrics(f)
        w = fm.horizontalAdvance(self.text())
        if self._tag:
            w += 6 + QFontMetrics(self._tag_font()).horizontalAdvance(self._tag)
        return QSize(w + 8, fm.height() + 20)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt's name
        pr = QPainter(self)
        pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        on = self.isChecked()
        f = QFont(self.font())
        f.setWeight(QFont.Weight.DemiBold)
        pr.setFont(f)
        pr.setPen(QColor(P["text"] if on or self.underMouse() else P["text2"]))
        rtl = self.isRightToLeft()
        r = QRectF(self.rect()).adjusted(4, 0, -4, -2)
        align = Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignRight if rtl
                                                 else Qt.AlignmentFlag.AlignLeft)
        pr.drawText(r, int(align), self.text())
        if self._tag:
            used = QFontMetrics(f).horizontalAdvance(self.text()) + 6
            pr.setFont(self._tag_font())
            pr.setPen(QColor(P.get("nerd", P["accent"])))
            pr.drawText(r.adjusted(0 if rtl else used, 1, -used if rtl else 0, 0), int(align), self._tag)
        if on:
            pr.setPen(Qt.PenStyle.NoPen)
            pr.setBrush(QColor(P["accent"]))
            pr.drawRect(QRectF(0, self.height() - 2, self.width(), 2))
        if self.hasFocus():
            pr.setPen(QColor(P["accent_hi"]))
            pr.setBrush(Qt.BrushStyle.NoBrush)
            pr.drawRoundedRect(QRectF(self.rect()).adjusted(1, 4, -1, -5), 4, 4)
        pr.end()


class _Initials(QWidget):
    """The source's initials on the accent, as at the sidebar's foot."""

    SIZE = 44

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(self.SIZE, self.SIZE)
        self.text = "?"

    def set_name(self, name: str) -> None:
        self.text = source_initials(name)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt's name
        fill = P.get("accent", "#4C8DFF")
        pr = QPainter(self)
        pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pr.setPen(Qt.PenStyle.NoPen)
        pr.setBrush(QColor(fill))
        box = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        pr.drawRoundedRect(box, 12.0, 12.0)
        f = QFont(self.font())
        f.setPixelSize(16)
        f.setWeight(QFont.Weight.Bold)
        pr.setFont(f)
        pr.setPen(QColor(ink_on(fill)))
        pr.drawText(box, int(Qt.AlignmentFlag.AlignCenter), self.text)
        pr.end()


class Origin(QLabel):
    """A row's mark: "Own for this source" in Nerd's colour, or "Shared"
    (or what the source follows) in a quiet grey; nothing when neither."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("SourceOrigin")

    def set(self, text: str, own: bool) -> None:
        """Its text and colour. Before it is in its row an empty text hides
        it and a text leaves it to show with the row: shown with no parent
        it was a window of its own for a moment - on GNOME (Wayland) one
        asking for an activation token it never used, and the pointer spun
        for seconds after Settings opened, once for every "This profile's"
        mark."""
        self.setText(text)
        if self.parentWidget() is not None:
            self.setVisible(bool(text))
        elif not text:
            self.hide()
        ink = P.get("nerd", P["accent"]) if own else P["text2"]
        fill = _mix(ink, P["bg"], 0.16) if own else P.get("btn", P["bg"])
        self.setStyleSheet(f"QLabel#SourceOrigin {{ color:{ink}; background:{fill}; border-radius:9px;"
                           " padding:2px 9px; font-size:11px; font-weight:600; }")


class SourcePage(SourceFields, QScrollArea):
    """*store*: the sources (what a change is stored in); *nerd_on*: whether
    Nerd's rows are there. *crumb*: the page it is under, which *back*
    returns to; *open_page(key)* shows another Settings page (a link row).
    *account(source, done)* looks the account up and calls *done(text)*
    with a line about it ("" for none). *saved(id)* after each change is
    stored, *left(id, changed)* once the page is left."""

    LIVE_MS = 400

    def __init__(self, playlist: dict, *, store: Any, nerd_on: bool, crumb: str = "",
                 back: Callable[[], None] | None = None,
                 open_page: Callable[[str], None] | None = None,
                 account: Callable[[dict, Callable[[str], None]], None] | None = None,
                 saved: Callable[[str], None] | None = None,
                 left: Callable[[str, bool], None] | None = None,
                 settings: Any = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SourcePage")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._store, self._nerd_on, self._settings = store, nerd_on, settings
        self._back, self._open_page, self._saved, self._left_cb = back, open_page, saved, left
        self._building = True
        self._left = False
        self._changed = False
        self._account = ""
        self._build_fields(playlist, True)
        #: The controls down the right of the cards (SettingsCard puts them
        #: here), one width for all.
        self.column: list[QWidget] = []
        self._cards: list[SettingsCard] = []
        self._origins: list[tuple[Origin, QWidget, Callable[[], tuple[str, bool, str]]]] = []
        self._parts: dict[str, Any] = {}
        if nerd_on:
            from .source_nerd import SourceNerdPanel
            self.nerd_panel = SourceNerdPanel(self.kind.currentData(), self._options, self,
                                              source=self._source_now)
            for field in (self.server, self.user, self.pw):
                field.textChanged.connect(self.nerd_panel.source_changed)
        body = QWidget()
        body.setObjectName("SettingsPageBody")
        outer = QHBoxLayout(body)
        outer.setContentsMargins(28, 22, 28, 22)
        col = QWidget()
        col.setMaximumWidth(PAGE_MAX_WIDTH)
        self._col = QVBoxLayout(col)
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(6)
        outer.addStretch(1)
        outer.addWidget(col, 100)
        outer.addStretch(1)
        self.setWidget(body)
        self._header(crumb)
        self._build_tabs()
        self._col.addStretch(1)
        self._build_connection()
        if nerd_on:
            self._build_network()
        self._build_streams()
        self._build_guide()
        self._build_advanced()
        self._live = QTimer(self)
        self._live.setSingleShot(True)
        self._live.setInterval(self.LIVE_MS)
        self._live.timeout.connect(self.commit)
        self.kind.currentIndexChanged.connect(self._update_kind)
        self._update_kind()
        self._watch()
        self._stored = self.values()
        self._building = False
        self._refresh_origins()
        self.show_tab("conn")
        if account is not None:
            account(dict(playlist), self._account_came)

    # -- the header ------------------------------------------------------------------

    def _header(self, crumb: str) -> None:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.crumb = QPushButton(f"{crumb}  {_arrow()}" if crumb else _arrow())
        self.crumb.setObjectName("SettingsCrumb")
        self.crumb.setFlat(True)
        self.crumb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.crumb.clicked.connect(self.go_back)
        self.crumb_name = QLabel("")
        self.crumb_name.setObjectName("SettingsHint")
        row.addWidget(self.crumb)
        row.addWidget(self.crumb_name)
        row.addStretch(1)
        self._col.addLayout(row)
        head = QHBoxLayout()
        head.setContentsMargins(0, 6, 0, 10)
        head.setSpacing(14)
        self.avatar = _Initials()
        head.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(3)
        self.title = QLabel("")
        self.title.setObjectName("SettingsTitle")
        self.title.setWordWrap(True)
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("SettingsHint")
        self.subtitle.setWordWrap(True)
        self.subtitle.setStyleSheet("font-size:12.5px;")
        text.addWidget(self.title)
        text.addWidget(self.subtitle)
        head.addLayout(text, 1)
        self.test_btn: QPushButton | None = None
        if self._nerd_on:
            # The connection to the source, step by step (core/conn_test.py).
            self.test_btn = QPushButton(tr("ct_button"))
            self.test_btn.setToolTip(tr("ct_row_hint"))
            self.test_btn.clicked.connect(self.test_connection)
            head.addWidget(self.test_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self._col.addLayout(head)
        # What keeps the last change from being stored, while it does.
        self.problem_label = QLabel("")
        self.problem_label.setWordWrap(True)
        self.problem_label.setStyleSheet(f"color:{P['error']}; font-size:12.5px;")
        self.problem_label.hide()
        self._col.addWidget(self.problem_label)

    def _update_header(self) -> None:
        name = self.values()["name"]
        self.title.setText(name)
        self.crumb_name.setText(name)
        self.avatar.set_name(name)
        kind = str(self.kind.currentData() or "")
        server = self.server.text().strip()
        parts = [KIND_NAMES.get(kind, kind)]
        if self._account:
            parts.append(self._account)
        elif server and kind not in MEDIA_KINDS:
            parts.append(mirrors.shown(server) if "://" in server else server)
        self.subtitle.setText("  ·  ".join(p for p in parts if p))

    def _account_came(self, text: str) -> None:
        if sip.isdeleted(self):
            return
        self._account = text
        self._update_header()

    # -- the tabs ----------------------------------------------------------------------

    def _build_tabs(self) -> None:
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(22)
        names = {"conn": tr("src_tab_connection"), "net": tr("nerd_src_net"),
                 "streams": tr("src_tab_streams"), "guide": tr("sec_guide"),
                 "adv": tr("set_page_advanced")}
        nerd_tag = QLocale().toUpper(tr("nerd_page"))
        self.tabs: dict[str, _Tab] = {}
        self.bodies: dict[str, QWidget] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for key in TABS:
            tab = _Tab(names[key], nerd_tag if key == "net" else "")
            tab.clicked.connect(lambda _c=False, k=key: self.show_tab(k))
            group.addButton(tab)
            bar.addWidget(tab)
            self.tabs[key] = tab
        bar.addStretch(1)
        self._col.addLayout(bar)
        # In the page first: shown in a bar on no widget yet, a tab was a
        # window of its own for a moment (on GNOME the pointer spun).
        self.tabs["net"].setVisible(self._nerd_on)
        line = QFrame()
        line.setObjectName("SettingsDivider")
        line.setFixedHeight(1)
        self._col.addWidget(line)
        self._col.addSpacing(14)
        for key in TABS:
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(6)
            self.bodies[key] = w
            self._col.addWidget(w)

    def show_tab(self, key: str) -> None:
        if key == "net" and not self._nerd_on:
            key = "conn"
        for k, w in self.bodies.items():
            w.setVisible(k == key)
            self.tabs[k].setChecked(k == key)

    def current_tab(self) -> str:
        return next((k for k, t in self.tabs.items() if t.isChecked()), "conn")

    def _card(self, tab: str, title: str = "", nerd_only: bool = False) -> SettingsCard:
        lay = self.bodies[tab].layout()
        if title:
            head = QLabel(title)
            head.setObjectName("SettingsCardTitle")
            head.setIndent(0)
            if nerd_only:
                head.setStyleSheet(f"color:{P.get('nerd', P['accent'])};")
            lay.addWidget(head)
        c = SettingsCard(self)  # type: ignore[arg-type] - it only needs .column
        if title:
            c.setProperty("_title", head)
        lay.addWidget(c)
        lay.addSpacing(8)
        self._cards.append(c)
        return c

    def _origin(self, row: QWidget, badge: Origin, fn: Callable[[], tuple[str, bool, str]]) -> QWidget:
        self._origins.append((badge, row, fn))
        return row

    def _own_or_shared(self, own: Callable[[], bool], where: str,
                       own_hint: str = "") -> Callable[[], tuple[str, bool, str]]:
        def fn() -> tuple[str, bool, str]:
            if own():
                return tr("src_own"), True, own_hint
            return tr("src_shared"), False, tr("src_follows", where=where)
        return fn

    def _refresh_origins(self) -> None:
        for badge, row, fn in self._origins:
            text, own, hint = fn()
            badge.set(text, own)
            label = getattr(row, "hint_label", None)
            if label is not None:
                label.setText(hint)
                label.setVisible(bool(hint))

    # -- Connection -----------------------------------------------------------------------

    def _build_connection(self) -> None:
        c = self._card("conn")
        c.row(tr("playlist_name"), self.name)
        c.row(tr("playlist_kind"), self.kind)
        self.server_row = c.row(tr("login_server"), self.server)
        c.shown_if(self.server_row, lambda: self._parts.get("server", True))
        finder = c.widget(self.finder.row)
        c.shown_if(finder, lambda: self._parts.get("finder", False))
        c.shown_if(c.row(tr("login_username"), self.user),
                   lambda: self._parts.get("login", True) and self._parts.get("user", True))
        self.pw_row = c.row(tr("login_password"), self.pw)
        c.shown_if(self.pw_row, lambda: self._parts.get("login", True))
        signin = c.widget(self.signin)
        c.shown_if(signin, lambda: self.kind.currentData() in MEDIA_KINDS)
        c.shown_if(c.row(tr("playlist_mac"), self.mac), lambda: self._parts.get("mac", False))
        # A SAT>IP server's channels: the lists given, else its own.
        c.shown_if(c.row(tr("satip_m3u"), self.satip_m3u, tr("satip_m3u_hint"), stretch_control=True,
                         search="SAT>IP M3U channel list"),
                   lambda: self._parts.get("satip", False))

    # -- Network (Nerd) ---------------------------------------------------------------------

    def _build_network(self) -> None:
        p = self.nerd_panel
        net = p.net
        shared = _where(tr("nerd_page"), tr("nerd_pg_net"))
        c = self._card("net")

        def row(label: str, control: Any, own: Callable[[], bool], hint: str = "",
                below: QWidget | None = None, search: str = "") -> QWidget:
            badge = Origin()
            r = c.row(label, control, " ", below=below, badge=badge, search=search)
            return self._origin(r, badge, self._own_or_shared(own, shared, hint))
        row(tr("nerd_user_agent"), _stack(p.ua, p.ua_custom), lambda: bool(p.ua.currentData()))
        row("Referer", p.referer, lambda: bool(p.referer.text().strip()))
        row(tr("nerd_headers"), p.headers, lambda: bool(p.headers.toPlainText().strip()))
        row(tr("nerd_proxy"), _stack(p.proxy, p.proxy_custom), lambda: bool(p.proxy.currentData()))
        row(tr("nerd_dns"), _stack(net.dns, net.dns_url), lambda: bool(net.dns.currentData()),
            below=net.dns_line, search="DNS DoH")
        row(tr("nerd_ip_family"), net.family, lambda: bool(net.family.currentData()),
            below=net.family_line, search="IPv4 IPv6")
        badge = Origin()
        mr = c.row(tr("nerd_src_mirrors"), None, tr("nerd_src_mirrors_hint"), badge=badge,
                   below=_holder(p.mirrors_box, p.mr_line))
        self._origin(mr, badge, lambda: (tr("src_own") if p.mirrors_box.toPlainText().strip() else "",
                                         True, tr("nerd_src_mirrors_hint")))
        c.shown_if(mr, lambda: self.kind.currentData() in mirrors.KINDS)
        badge = Origin()
        cert = c.row(tr("cert_row"), net.cert_buttons, " ", badge=badge,
                     below=_holder(net.pin_label, net.cert_line), search="TLS pin")
        self._origin(cert, badge, lambda: ((tr("src_own"), True, tr("cert_trust_hint")) if net.has_pin()
                                           else (tr("src_system"), False, tr("cert_trust_hint"))))
        # The latest connection test of this source, this session.
        test = self._card("net", tr("src_last_test"))
        self.test_head = test.property("_title")
        self.test_box = QWidget()
        QVBoxLayout(self.test_box).setContentsMargins(0, 0, 0, 0)
        test.widget(self.test_box, "connection test")
        self._show_last_test()

    def _show_last_test(self) -> None:
        """What the latest Test connection found, step by step (its marks,
        results and times, as its window showed them), and Copy."""
        from .conn_test_dialog import StepRow
        box = getattr(self, "test_box", None)
        if box is None:
            return
        lay = box.layout()
        while lay.count():
            item = lay.takeAt(0)
            if item is not None and item.widget() is not None:
                item.widget().deleteLater()
        got = LAST_TEST.get(self._pid)
        if got is None:
            self.test_head.setText(tr("src_last_test"))
            note = QLabel(tr("src_no_test", button=tr("ct_button")))
            note.setObjectName("SettingsHint")
            note.setWordWrap(True)
            lay.addWidget(note)
            return
        when = QLocale().toString(got["at"].time(), QLocale.FormatType.ShortFormat)
        self.test_head.setText(f"{tr('src_last_test')}  ·  {when}")
        for step in got["steps"]:
            r = StepRow(step.name)
            r.show_step(step)
            lay.addWidget(r)
        self.copied = QLabel("")
        self.copied.setObjectName("SettingsHint")
        self.copied.setWordWrap(True)
        copy = QPushButton(tr("problem_copy"))
        copy.clicked.connect(self._copy_test)
        foot = QWidget()
        h = QHBoxLayout(foot)
        h.setContentsMargins(0, 6, 0, 0)
        h.setSpacing(10)
        h.addWidget(copy)
        h.addWidget(self.copied, 1)
        lay.addWidget(foot)

    def _copy_test(self) -> None:
        from PyQt6.QtGui import QGuiApplication

        from ..core import conn_test as ct
        got = LAST_TEST.get(self._pid)
        if got is None:
            return
        QGuiApplication.clipboard().setText(ct.copy_text(got["lines"], got["source"]))
        self.copied.setText("✓ " + tr("ct_copied"))

    def test_source(self) -> dict:
        """The source as it would be stored now: what Test connection tries."""
        if self.nerd_panel is not None:
            src = self.nerd_panel.test_source()
        else:
            src = dict(self._source_now(), options=dict(self._options))
        return src

    def test_connection(self) -> None:
        from ..providers.client import make_client
        from .conn_test_dialog import ConnTestDialog
        src = self.test_source()
        dlg = ConnTestDialog(self.window(), src, nerd.current().s, make_client)
        dlg.exec()
        t = dlg.test
        if sip.isdeleted(self) or t is None or not t.finished or t.cancelled:
            return
        LAST_TEST[self._pid] = {"at": datetime.now(), "steps": t.snapshot(),
                                "lines": dlg.lines(), "source": src}
        self._show_last_test()

    # -- Streams ------------------------------------------------------------------------

    def _build_streams(self) -> None:
        c = self._card("streams")
        badge = Origin()
        badge.set(tr("src_shared"), False)
        c.link(tr("setting_stream_format"), tr("src_follows", where=_where(tr("set_grp_watch"), tr("tab_playback"))),
               tr("tab_playback"), lambda: self._go("playback"), badge=badge)
        # A SAT>IP server's own, for everyone: how its streams travel, its
        # HTTP port when they come over HTTP, how many of its tuners to use.
        sat = lambda: self._parts.get("satip", False)  # noqa: E731
        c.shown_if(c.row(tr("satip_transport"), self.satip_transport, tr("satip_transport_hint"),
                         search="SAT>IP RTSP TCP UDP HTTP"), sat)
        c.shown_if(c.row(tr("satip_http_port"), Stepper(self.satip_http_port), tr("satip_http_port_hint")),
                   lambda: sat() and self.satip_transport.currentData() == "http")
        c.shown_if(c.row(tr("satip_tuners"), Stepper(self.satip_tuners, width=230), tr("satip_tuners_hint"),
                         search="SAT>IP tuners"), sat)
        self.satip_transport.currentIndexChanged.connect(self._refresh_cards)
        if self.nerd_panel is None:
            return
        p = self.nerd_panel
        c = self._card("streams", tr("nerd_page"), nerd_only=True)
        badge = Origin()
        ux = c.row("udpxy", p.udpxy, " ", badge=badge, search="multicast udp rtp")
        self._origin(ux, badge, self._own_or_shared(lambda: bool(p.udpxy.text().strip()),
                                                    _where(tr("nerd_page"), tr("nerd_pg_boxlists"))))
        cu = lambda: self.kind.currentData() in catchup.KINDS  # noqa: E731
        c.shown_if(c.row(tr("nerd_src_catchup_mode"), p.cu_mode, tr("nerd_src_catchup_hint")), cu)
        badge = Origin()
        tpl = c.row(tr("nerd_src_catchup"), p.cu_template, tr("nerd_src_catchup_fields"),
                    below=p.cu_line, badge=badge, search="archive timeshift")
        self._origin(tpl, badge, lambda: (tr("src_own") if p.cu_template.text().strip() else "",
                                          True, tr("nerd_src_catchup_fields")))
        c.shown_if(tpl, cu)
        badge = Origin()
        rw = c.row(tr("nerd_src_rewrite"), None, tr("nerd_src_rewrite_hint"), badge=badge,
                   below=_holder(p.rewrite, p.rw_line), search="rewrite regex")
        self._origin(rw, badge, lambda: (tr("src_own") if p.rewrite.stored() else "", True,
                                         tr("nerd_src_rewrite_hint")))
        kind = self.kind.currentData

        def only(row: QWidget, *kinds: str) -> None:
            c.shown_if(row, lambda: kind() in kinds)
        only(c.row(tr("nerd_src_stream_port"), Stepper(p.port)), "enigma2")
        only(c.row(tr("nerd_src_e2_transcode"), p.e2_transcode, tr("nerd_src_e2_transcode_hint")), "enigma2")
        only(c.row(tr("nerd_src_profile"), p.profile), "tvheadend")
        only(c.row(tr("nerd_src_transcode"), p.transcode), "hdhomerun")
        only(c.row(tr("nerd_src_streamdev_port"), Stepper(p.streamdev_port), tr("nerd_src_streamdev_hint")),
             "vdr")
        only(c.row(tr("nerd_src_stream_port"), Stepper(p.dvb_port), tr("nerd_src_dvb_port_hint")),
             "dvbviewer")
        only(c.row(tr("ms_opt_quality"), p.ms_quality), *MEDIA_KINDS)
        self._switch_rows(c, STREAM_SWITCHES)

    def _switch_rows(self, card: SettingsCard, which: set[tuple[str, str]] | None) -> None:
        """The kinds' switches on *card*: those in *which*, or (None) every
        one not on another tab."""
        if self.nerd_panel is None:
            return
        elsewhere = STREAM_SWITCHES | GUIDE_SWITCHES
        for k, boxes in self.nerd_panel.kind_switches.items():
            for key, box in boxes.items():
                mine = (k, key) in which if which is not None else (k, key) not in elsewhere
                if mine:
                    r = card.row(box.accessibleName(), box)
                    card.shown_if(r, lambda k=k: self.kind.currentData() == k)

    # -- Guide ----------------------------------------------------------------------------

    def _build_guide(self) -> None:
        c = self._card("guide")
        c.row(tr("playlist_custom_epg_url"), self.epg_url,
              tr("nerd_epg_multi") if self._nerd_on else "", stretch_control=True, search="EPG XMLTV")
        if self.nerd_panel is None:
            return
        p = self.nerd_panel
        c = self._card("guide", tr("nerd_page"), nerd_only=True)
        badge = Origin()
        shift = c.row(tr("nerd_src_epg_shift"), Stepper(p.epg_shift, width=150), " ", badge=badge,
                      search="EPG offset time")
        self._origin(shift, badge, self._own_or_shared(lambda: bool(p.epg_shift.value()),
                                                       _where(tr("set_grp_watch"), tr("sec_guide"))))
        self._switch_rows(c, GUIDE_SWITCHES)
        boxes = _where(tr("nerd_page"), tr("nerd_pg_boxlists"))
        badge = Origin()
        badge.set(tr("src_shared"), False)
        c.link(tr("nerd_card_picons"), tr("src_follows", where=boxes), boxes,
               lambda: self._go("nerd_boxes"), badge=badge)
        numbers = len(channel_numbers.stored(self._settings, self._pid)) if self._settings is not None else 0
        name = self.values()["name"]
        badge = Origin()
        # None of its own: the source's numbers, nothing shared to follow.
        badge.set(tr("src_own") if numbers else "", True)
        c.link(tr("nerd_card_chno"),
               tr("nerd_chno_count", source=name, n=numbers) if numbers
               else tr("nerd_chno_none", source=name),
               boxes, lambda: self._go("nerd_boxes"), badge=badge)
        c.note(tr("nerd_epg_map_note"))

    # -- Advanced -------------------------------------------------------------------------

    def _build_advanced(self) -> None:
        c = self._card("adv")
        c.row(tr("playlist_auto_refresh"), self.refresh)
        if self.nerd_panel is not None:
            self._switch_rows(self._card("adv", tr("nerd_page"), nerd_only=True), None)

    # -- the kind ---------------------------------------------------------------------------

    def _update_kind(self, *_a: Any) -> None:
        self._parts = self._kind_fields()
        self.server_row.label.setText(self._parts["server_label"])
        self.pw_row.label.setText(self._parts["pw_label"])
        self._refresh_cards()
        self._update_header()

    def _refresh_cards(self, *_a: Any) -> None:
        """Each row shown or not as the kind (and what it chose) says now."""
        for c in self._cards:
            c.refresh()

    # -- storing ------------------------------------------------------------------------------

    def _watch(self) -> None:
        """Every control's change: its mark at once, stored a moment later."""
        for w in self.findChildren(QLineEdit):
            w.textChanged.connect(self._edited)
        for w in self.findChildren(QComboBox):
            w.currentIndexChanged.connect(self._edited)
            if w.isEditable():
                w.editTextChanged.connect(self._edited)
        for w in self.findChildren(QSpinBox):
            w.valueChanged.connect(self._edited)
        for w in self.findChildren(QCheckBox):
            w.toggled.connect(self._edited)
        for w in self.findChildren(QPlainTextEdit):
            w.textChanged.connect(self._edited)
        self.signin.changed.connect(self._edited)
        self.signin.finished.connect(self._edited)
        if self.nerd_panel is not None:
            self.nerd_panel.rewrite.edited.connect(self._edited)
            self.nerd_panel.net.pinned.connect(self._edited)

    def _edited(self, *_a: Any) -> None:
        if self._building or self._left or sip.isdeleted(self):
            return
        self._refresh_origins()
        self._update_header()
        self._live.start()

    def _show_problem(self, bad: tuple[str, str, str] | None) -> None:
        self.problem_label.setText(f"✗  {bad[1]}" if bad else "")
        self.problem_label.setVisible(bad is not None)

    def _show_part(self, part: str) -> None:
        p = self.nerd_panel
        if part == "rewrite" and p is not None:
            self.show_tab("streams")
            p.rw_line.show_preview()
        elif part == "mirrors" and p is not None:
            self.show_tab("net")
            p.mr_line.show_preview()
        else:
            self.show_tab("conn")

    def commit(self) -> bool:
        """Store what the page says, if it can be stored: True when it is
        (or nothing changed)."""
        self._live.stop()
        bad = self.problem()
        self._show_problem(bad)
        if bad is not None:
            return False
        now = self.values()
        if now == self._stored:
            return True
        out = dict(now)
        # Every option back at its default: none left to keep (the store
        # merges, so the old ones would stay).
        if "options" not in out and (self._store.get(self._pid) or {}).get("options"):
            out["options"] = {}
        self._store.update(self._pid, **out)
        self._stored = now
        self._changed = True
        if self._saved is not None:
            self._saved(self._pid)
        return True

    def go_back(self) -> None:
        """Back to the page it is under - unless the change cannot be
        stored, which is said first (as OK said in the old dialog)."""
        bad = self.problem()
        if bad is not None and self.values() != self._stored:
            self._show_part(bad[2])
            QMessageBox.warning(self.window(), bad[0], bad[1])
            return
        if self._back is not None:
            self._back()

    def leave(self) -> None:
        """The page is going (another page shown, Settings closed): what it
        says is stored, or said not to have been."""
        if self._left:
            return
        stored = self.commit()
        self._left = True
        if not stored and self.values() != self._stored:
            bad = self.problem()
            if bad is not None:
                QMessageBox.warning(self.window(), bad[0], f"{bad[1]}\n\n{tr('src_not_saved')}")
        if self._left_cb is not None:
            self._left_cb(self._pid, self._changed)

    def _go(self, key: str) -> None:
        if self._open_page is not None:
            self._open_page(key)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt's name
        # Measured on show: a size hint is only right once styled.
        if not getattr(self, "_aligned", False):
            self._aligned = True
            align_controls(self.column)
        super().showEvent(event)
