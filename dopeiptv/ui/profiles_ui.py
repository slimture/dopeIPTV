"""Profiles on screen (core/profiles.py keeps them): an avatar - initials on
the profile's colour, or its emoji - the "Who's watching?" picker over the
window (big tiles, arrows and Enter), the same picker as a screen of TV mode
in its own large style, the window that makes or edits a profile, the PIN
question, and the Profiles page in Settings.

The window's side (switching, the PIN checks, a kids profile's limits) is
ui/mw_profiles.py."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QKeyEvent, QPainter, QPaintEvent, QPen
from PyQt6.QtWidgets import (
    QAbstractButton, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from ..core import profiles
from ..core.profiles import Profile
from ..i18n import tr
from . import message_box
from .theme import ACCENTS, P, ink_on, rounded


def profile_fill(p: Profile | None) -> str:
    """The profile's colour, from the theme's fixed palette (ACCENTS)."""
    key = p.colour if p is not None else "blue"
    return (ACCENTS.get(key) or ACCENTS["blue"])[1]


def avatar_text(p: Profile | None) -> str:
    if p is None:
        return "?"
    return p.emoji or p.initials()


def paint_avatar(pr: QPainter, box: QRectF, p: Profile | None, radius: float,
                 focus: bool = False) -> None:
    """The avatar in *box*: the profile's colour, its emoji or initials over
    it in the ink that reads on it; a ring in the text colour for the focus."""
    fill = profile_fill(p)
    pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pr.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    pr.setPen(Qt.PenStyle.NoPen)
    pr.setBrush(QColor(fill))
    pr.drawRoundedRect(box, radius, radius)
    if focus:
        ring = max(2.0, box.width() / 30)
        pr.setPen(QPen(QColor(P.get("text", "#ECECF1")), ring))
        pr.setBrush(Qt.BrushStyle.NoBrush)
        pr.drawRoundedRect(box.adjusted(-ring * 1.5, -ring * 1.5, ring * 1.5, ring * 1.5),
                           radius + ring * 1.5, radius + ring * 1.5)
    text = avatar_text(p)
    f = QFont(pr.font())
    f.setPixelSize(max(9, int(box.height() * (0.5 if p is not None and p.emoji else 0.38))))
    f.setWeight(QFont.Weight.Bold)
    pr.setFont(f)
    pr.setPen(QColor(ink_on(fill)))
    pr.drawText(box, int(Qt.AlignmentFlag.AlignCenter), text)


class ProfileAvatar(QWidget):
    """A profile's avatar, painted from the theme each time."""

    def __init__(self, p: Profile | None = None, size: int = 32) -> None:
        super().__init__()
        self.profile = p
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_profile(self, p: Profile | None) -> None:
        self.profile = p
        self.update()

    def paintEvent(self, _e: QPaintEvent) -> None:
        pr = QPainter(self)
        s = float(self.width())
        paint_avatar(pr, QRectF(0.5, 0.5, s - 1, s - 1), self.profile, s * 0.26)
        pr.end()


# -- the PIN ---------------------------------------------------------------------

def ask_pin(parent: QWidget, title: str, text: str,
            longest: int = profiles.PIN_LONGEST) -> str | None:
    """A profile's PIN asked for (up to *longest* characters: a grown-up's
    may be the parental PIN, which can be longer), hidden as it is typed;
    None when cancelled. In the app's own look (ui/message_box.py), which
    empties the field before the window goes: the PIN typed is not kept in
    a hidden window for the session."""
    pin, ok = message_box.get_text(parent, title, text, password=True, placeholder="••••",
                                   max_length=max(4, int(longest)), name=tr("profile_pin"))
    return pin.strip() if ok else None


# -- the picker: "Who's watching?" -------------------------------------------------

class ProfileTile(QAbstractButton):
    """A profile in the picker: a big avatar and its name under it; a lock
    under one with a PIN, "Kids" under a kids profile."""

    def __init__(self, p: Profile, size: int = 132, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.profile = p
        self._size = size
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(p.name)
        self.setFixedSize(size + 36, size + 76)

    def sizeHint(self) -> QSize:
        return self.size()

    def paintEvent(self, _e: QPaintEvent) -> None:
        pr = QPainter(self)
        s = float(self._size)
        x = (self.width() - s) / 2
        box = QRectF(x, 14, s, s)
        hot = self.hasFocus() or self.underMouse()
        paint_avatar(pr, box, self.profile, s * 0.18, focus=self.hasFocus())
        f = QFont(self.font())
        f.setPixelSize(17)
        f.setWeight(QFont.Weight.Bold if hot else QFont.Weight.DemiBold)
        pr.setFont(f)
        pr.setPen(QColor(P["text"] if hot else P["text2"]))
        name_box = QRectF(0, box.bottom() + 10, self.width(), 24)
        fm = pr.fontMetrics()
        pr.drawText(name_box, int(Qt.AlignmentFlag.AlignCenter),
                    fm.elidedText(self.profile.name, Qt.TextElideMode.ElideRight, self.width() - 6))
        tags = []
        if self.profile.kids:
            tags.append(tr("profile_kids_tag"))
        if self.profile.has_pin:
            tags.append(tr("profile_pin_on"))
        if tags:
            f.setPixelSize(12)
            f.setWeight(QFont.Weight.Normal)
            pr.setFont(f)
            pr.setPen(QColor(P["muted"]))
            pr.drawText(QRectF(0, name_box.bottom() + 2, self.width(), 18),
                        int(Qt.AlignmentFlag.AlignCenter), " · ".join(tags))
        pr.end()

    def enterEvent(self, e) -> None:
        super().enterEvent(e)
        self.update()

    def leaveEvent(self, e) -> None:
        super().leaveEvent(e)
        self.update()

    def focusInEvent(self, e) -> None:
        super().focusInEvent(e)
        self.update()

    def focusOutEvent(self, e) -> None:
        super().focusOutEvent(e)
        self.update()


class ProfilePicker(QWidget):
    """"Who's watching?" over the whole window: a tile per profile, Manage
    profiles under them. Arrows move between the tiles, Enter or a click
    picks one; Esc goes on as the profile in use when it may (*can_close*)."""

    picked = pyqtSignal(str)
    manage = pyqtSignal()

    COLUMNS = 4

    def __init__(self, parent: QWidget, items: list[Profile], active: str,
                 can_close: Callable[[], bool]) -> None:
        super().__init__(parent)
        self.setObjectName("ProfilePicker")
        # A plain QWidget paints its style sheet's background only when told.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        self._can_close = can_close
        self.setStyleSheet(rounded(
            f"#ProfilePicker {{ background: {P['bg']}; }}"
            f"#ProfilePickerTitle {{ color: {P['text']}; font-size: 34px; font-weight: 700; }}"
            f"#ProfilePickerHint {{ color: {P['muted']}; font-size: 13px; }}"
            f"QPushButton#ProfileManage {{ background: {P['btn']}; color: {P['text2']};"
            f" border: none; border-radius: 8px; padding: 8px 18px; font-size: 14px; }}"
            f"QPushButton#ProfileManage:hover, QPushButton#ProfileManage:focus"
            f" {{ background: {P['btn_hover']}; color: {P['text']}; }}"))
        outer = QVBoxLayout(self)
        outer.addStretch(2)
        title = QLabel(tr("profile_who"))
        title.setObjectName("ProfilePickerTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(title)
        outer.addSpacing(26)
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)
        self.tiles: list[ProfileTile] = []
        cols = min(self.COLUMNS, max(1, len(items)))
        for i, p in enumerate(items):
            t = ProfileTile(p)
            t.clicked.connect(self._clicked)
            grid.addWidget(t, i // cols, i % cols)
            self.tiles.append(t)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(holder)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addSpacing(24)
        self.manage_btn = QPushButton(tr("profile_manage"))
        self.manage_btn.setObjectName("ProfileManage")
        self.manage_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.manage_btn.clicked.connect(self.manage.emit)
        mrow = QHBoxLayout()
        mrow.addStretch(1)
        mrow.addWidget(self.manage_btn)
        mrow.addStretch(1)
        outer.addLayout(mrow)
        outer.addSpacing(10)
        hint = QLabel(tr("profile_picker_hint"))
        hint.setObjectName("ProfilePickerHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(hint)
        outer.addStretch(3)
        self._cols = cols
        start = next((t for t in self.tiles if t.profile.id == active), None)
        self._first = start or (self.tiles[0] if self.tiles else None)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.fit()
        if self._first is not None:
            self._first.setFocus(Qt.FocusReason.OtherFocusReason)

    def fit(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
        self.raise_()

    def _clicked(self) -> None:
        tile = self.sender()
        if isinstance(tile, ProfileTile):
            self.picked.emit(tile.profile.id)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        k = e.key()
        # The window's focus, whether or not it is the active window yet.
        fw = self.window().focusWidget()
        cur = next((i for i, t in enumerate(self.tiles) if t is fw), None)
        step = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1,
                Qt.Key.Key_Up: -self._cols, Qt.Key.Key_Down: self._cols}.get(Qt.Key(k))
        if step is not None and self.tiles:
            if self.layoutDirection() == Qt.LayoutDirection.RightToLeft and abs(step) == 1:
                step = -step
            if cur is None:
                self.tiles[0].setFocus()
            elif 0 <= cur + step < len(self.tiles):
                self.tiles[cur + step].setFocus()
            elif step == self._cols:
                self.manage_btn.setFocus()
            return
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and cur is not None:
            self.picked.emit(self.tiles[cur].profile.id)
            return
        if k == Qt.Key.Key_Escape:
            if self._can_close():
                self.hide()
                self.deleteLater()
            return
        super().keyPressEvent(e)


# -- making or editing one -----------------------------------------------------------

class _Swatch(QAbstractButton):
    """One colour or emoji to pick for the avatar."""

    def __init__(self, colour: str = "", emoji: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.colour, self.emoji = colour, emoji
        self.setCheckable(True)
        self.setFixedSize(30, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # An emoji is read by its own name ("Avatar fox face"); a colour by
        # the name Settings gives it, in the language in use.
        self.setAccessibleName(f"{tr('profile_avatar')} {emoji}" if emoji
                               else tr(f"accent_{colour}") if colour in ACCENTS
                               else tr("profile_no_emoji"))
        self.toggled.connect(self.update)

    def paintEvent(self, _e: QPaintEvent) -> None:
        pr = QPainter(self)
        pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        box = QRectF(3, 3, 24, 24)
        if self.colour:
            pr.setPen(Qt.PenStyle.NoPen)
            pr.setBrush(QColor((ACCENTS.get(self.colour) or ACCENTS["blue"])[1]))
            pr.drawEllipse(box)
        else:
            pr.setPen(Qt.PenStyle.NoPen)
            pr.setBrush(QColor(P["btn"]))
            pr.drawRoundedRect(box, 6, 6)
            f = QFont(self.font())
            f.setPixelSize(15)
            pr.setFont(f)
            pr.setPen(QColor(P["text2"]))
            pr.drawText(box, int(Qt.AlignmentFlag.AlignCenter), self.emoji or "Aa")
        if self.isChecked() or self.hasFocus():
            pr.setPen(QPen(QColor(P["text"] if self.isChecked() else P["accent"]), 2))
            pr.setBrush(Qt.BrushStyle.NoBrush)
            pr.drawRoundedRect(QRectF(1, 1, 28, 28), 8, 8)
        pr.end()


class ProfileDialog(QDialog):
    """A new profile, or one to edit: its name, the avatar's colour or
    emoji, a PIN (4 to 12 digits, or none), whether it is a kids profile -
    and for one, whether it sees only what a grown-up allows, and what
    (``AllowedPicker``, with *window*: the sources and their categories)."""

    def __init__(self, parent: QWidget, store: profiles.ProfileStore,
                 profile: Profile | None = None, parental: Any = None,
                 window: Any = None) -> None:
        super().__init__(parent)
        self.store, self.profile, self.parental = store, profile, parental
        self.setWindowTitle(tr("profile_edit_title") if profile else tr("profile_add_title"))
        self.setMinimumWidth(440)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        top = QHBoxLayout()
        self.preview = ProfileAvatar(None, 64)
        top.addWidget(self.preview)
        top.addSpacing(8)
        name_col = QVBoxLayout()
        name_col.addWidget(QLabel(tr("profile_name")))
        self.name = QLineEdit(profile.name if profile else "")
        self.name.setMaxLength(profiles.MAX_NAME)
        self.name.setAccessibleName(tr("profile_name"))
        self.name.textChanged.connect(self._preview)
        name_col.addWidget(self.name)
        top.addLayout(name_col, 1)
        lay.addLayout(top)
        lay.addWidget(QLabel(tr("profile_avatar")))
        self.colours: list[_Swatch] = []
        crow = QHBoxLayout()
        crow.setSpacing(4)
        current = profile.colour if profile else self._free_colour()
        for c in profiles.COLOURS:
            sw = _Swatch(colour=c)
            sw.setChecked(c == current)
            sw.clicked.connect(self._pick_colour)
            crow.addWidget(sw)
            self.colours.append(sw)
        crow.addStretch(1)
        lay.addLayout(crow)
        self.emojis: list[_Swatch] = []
        erow = QHBoxLayout()
        erow.setSpacing(4)
        have = profile.emoji if profile else ""
        for e in ("",) + profiles.EMOJI:
            sw = _Swatch(emoji=e)
            sw.setChecked(e == have)
            sw.clicked.connect(self._pick_emoji)
            erow.addWidget(sw)
            self.emojis.append(sw)
        erow.addStretch(1)
        lay.addLayout(erow)
        lay.addSpacing(4)
        from .settings_ui import ToggleSwitch
        krow = QHBoxLayout()
        kcol = QVBoxLayout()
        kcol.addWidget(QLabel(tr("profile_kids")))
        khint = QLabel(tr("profile_kids_hint"))
        khint.setWordWrap(True)
        khint.setStyleSheet(f"color:{P['muted']}; font-size:12px;")
        kcol.addWidget(khint)
        krow.addLayout(kcol, 1)
        self.kids = ToggleSwitch(bool(profile and profile.kids))
        self.kids.setAccessibleName(tr("profile_kids"))
        krow.addWidget(self.kids, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(krow)
        # A kids profile that sees only what a grown-up chose, per source
        # (core/profiles.py ``only_allowed``) - off unless turned on here.
        self.allowed = {sid: {m: list(ids) for m, ids in modes.items()}
                        for sid, modes in (profile.allowed if profile else {}).items()}
        self.only_box = QWidget()
        orow = QHBoxLayout(self.only_box)
        orow.setContentsMargins(18, 0, 0, 0)
        ocol = QVBoxLayout()
        ocol.addWidget(QLabel(tr("profile_only_allowed")))
        ohint = QLabel(tr("profile_only_allowed_hint"))
        ohint.setWordWrap(True)
        ohint.setStyleSheet(f"color:{P['muted']}; font-size:12px;")
        ocol.addWidget(ohint)
        self.choose = QPushButton(tr("profile_allowed_choose"))
        self.choose.clicked.connect(self._choose_allowed)
        self.choose.setVisible(window is not None)
        ocol.addWidget(self.choose, 0, Qt.AlignmentFlag.AlignLeft)
        orow.addLayout(ocol, 1)
        self.only = ToggleSwitch(bool(profile and profile.only_allowed))
        self.only.setAccessibleName(tr("profile_only_allowed"))
        orow.addWidget(self.only, 0, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.only_box)
        self.only_box.setVisible(self.kids.isChecked())
        self.kids.toggled.connect(self.only_box.setVisible)
        self._win = window
        prow = QHBoxLayout()
        pcol = QVBoxLayout()
        pcol.addWidget(QLabel(tr("profile_pin")))
        phint = QLabel(tr("profile_pin_hint"))
        phint.setWordWrap(True)
        phint.setStyleSheet(f"color:{P['muted']}; font-size:12px;")
        pcol.addWidget(phint)
        prow.addLayout(pcol, 1)
        self.pin = QLineEdit()
        self.pin.setEchoMode(QLineEdit.EchoMode.Password)
        self.pin.setMaxLength(profiles.PIN_LONGEST)
        self.pin.setFixedWidth(140)
        self.pin.setAccessibleName(tr("profile_pin"))
        self.pin.setPlaceholderText("••••" if profile and profile.has_pin else tr("profile_pin_none"))
        prow.addWidget(self.pin, 0, Qt.AlignmentFlag.AlignVCenter)
        self.clear_pin = QPushButton(tr("profile_pin_remove"))
        self.clear_pin.setCheckable(True)
        self.clear_pin.setVisible(bool(profile and profile.has_pin))
        prow.addWidget(self.clear_pin, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(prow)
        self.error = QLabel("")
        self.error.setStyleSheet(f"color:{P['error']};")
        self.error.hide()
        lay.addWidget(self.error)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                               | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self._preview()
        self.saved: Profile | None = None

    def _free_colour(self) -> str:
        used = {p.colour for p in self.store.profiles()}
        return next((c for c in profiles.COLOURS if c not in used), profiles.COLOURS[0])

    def _colour(self) -> str:
        return next((s.colour for s in self.colours if s.isChecked()), profiles.COLOURS[0])

    def _emoji(self) -> str:
        return next((s.emoji for s in self.emojis if s.isChecked()), "")

    def _pick_colour(self) -> None:
        me = self.sender()
        for s in self.colours:
            s.setChecked(s is me)
        self._preview()

    def _pick_emoji(self) -> None:
        me = self.sender()
        for s in self.emojis:
            s.setChecked(s is me)
        self._preview()

    def _preview(self) -> None:
        self.preview.set_profile(Profile("_", self.name.text() or "?", self._colour(), self._emoji()))

    def _grown_up_pin(self, typed: str) -> bool:
        """Whether a grown-up's PIN exists once this is saved: the parental
        PIN, or a PIN of a profile that is not a kids one - this one's own
        too, when it is being made a grown-up's (never for a kids profile:
        its own PIN is the child's)."""
        if self.parental is not None and self.parental.has_pin():
            return True
        me = self.profile.id if self.profile else ""
        return any(not p.kids and p.has_pin for p in self.store.profiles() if p.id != me)

    def _fail(self, key: str) -> None:
        self.error.setText(tr(key))
        self.error.show()

    def _choose_allowed(self) -> None:
        """What this kids profile may watch, per source (AllowedPicker):
        kept with the profile once it is saved."""
        dlg = AllowedPicker(self, self._win, self.allowed)
        if dlg.exec():
            self.allowed = dlg.chosen()
            if not self.only.isChecked():
                self.only.setChecked(True)      # chosen: the mode is what it is for

    def _save(self) -> None:
        name = profiles.clean_name(self.name.text())
        pin = self.pin.text().strip()
        if not name:
            self._fail("profile_err_name")
            return
        if self.store.name_taken(name, but=self.profile.id if self.profile else ""):
            self._fail("profile_err_taken")
            return
        if pin and not profiles.valid_pin(pin):
            self._fail("profile_err_pin")
            return
        if self.kids.isChecked() and not self._grown_up_pin(pin):
            # A kids profile is a boundary only with a PIN a grown-up keeps:
            # without one, it leaves for another profile or opens Settings
            # as it likes.
            self._fail("profile_err_kids_pin")
            return
        try:
            if self.profile is None:
                p = self.store.add(name, self._colour(), self._emoji(), self.kids.isChecked())
            else:
                p = self.profile
                self.store.update(p.id, name=name, colour=self._colour(),
                                  emoji=self._emoji(), kids=self.kids.isChecked())
            self.store.update(p.id, only_allowed=self.kids.isChecked() and self.only.isChecked(),
                              allowed=self.allowed)
        except ValueError as e:
            self._fail({"full": "profile_err_full", "taken": "profile_err_taken"}.get(str(e), "profile_err_name"))
            return
        if pin:
            self.store.set_pin(p.id, pin)
        elif self.clear_pin.isChecked():
            self.store.clear_pin(p.id)
        self.saved = self.store.get(p.id)
        self.accept()


def _categories_of(window: Any, pl: dict) -> dict[str, list[dict]]:
    """(worker) Source *pl*'s categories of each catalogue (ALLOW_MODES):
    the window's own client for the source in use, else one made for it -
    the source's own lists, nothing else asked."""
    store = getattr(window, "playlist_store", None)
    if str(pl.get("id")) == str(getattr(store, "active_id", "") or "") \
            and getattr(window, "client", None) is not None:
        client = window.client
    else:
        from ..providers.client import make_client
        client = make_client(pl)
    out: dict[str, list[dict]] = {}
    for mode, fn in (("live", "live_categories"), ("vod", "vod_categories"),
                     ("series", "series_categories")):
        get = getattr(client, fn, None)
        try:
            out[mode] = [c for c in (get() if callable(get) else []) or []
                         if isinstance(c, dict) and c.get("category_id") is not None]
        except Exception as e:
            from ..core.log import log
            log.info("profiles: %s categories not read (%s)", mode, type(e).__name__)
            out[mode] = []
    return out


class AllowedPicker(QDialog):
    """What a kids profile with "Only what a grown-up allows" may watch: a
    source picked, its channel, film and series categories ticked - none
    named for adults is offered. Read off the window's thread; ``chosen()``
    the profile's ``allowed`` as it is then (each source's own, kept as it
    was for one not opened here)."""

    def __init__(self, parent: QWidget, window: Any, allowed: dict) -> None:
        super().__init__(parent)
        from PyQt6.QtWidgets import QComboBox, QListWidget
        self.w = window
        self.picked = {sid: {m: set(ids) for m, ids in modes.items()}
                       for sid, modes in (allowed or {}).items()}
        self.setWindowTitle(tr("profile_allowed_title"))
        self.setMinimumSize(460, 520)
        lay = QVBoxLayout(self)
        hint = QLabel(tr("profile_only_allowed_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{P['muted']}; font-size:12px;")
        lay.addWidget(hint)
        self.source = QComboBox()
        self.source.setAccessibleName(tr("profile_allowed_source"))
        store = getattr(window, "playlist_store", None)
        sources = [p for p in (store.playlists() if store is not None else [])
                   if p.get("id") and p.get("kind") != "local"]
        active = str(getattr(store, "active_id", "") or "")
        sources.sort(key=lambda p: str(p.get("id")) != active)
        for p in sources:
            self.source.addItem(str(p.get("name") or p.get("server") or p.get("id")), p)
        lay.addWidget(self.source)
        self.note = QLabel("")
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color:{P['muted']};")
        lay.addWidget(self.note)
        self.list = QListWidget()
        self.list.setAccessibleName(tr("profile_allowed_title"))
        self.list.itemChanged.connect(self._ticked)
        lay.addWidget(self.list, 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                               | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self._pid = ""
        self._gen = 0
        self.source.currentIndexChanged.connect(lambda _i: self._load())
        self._load()

    def _load(self) -> None:
        pl = self.source.currentData()
        self.list.clear()
        if not isinstance(pl, dict):
            self.note.setText(tr("profile_allowed_none"))
            return
        self._pid = str(pl.get("id"))
        self._gen += 1
        gen = self._gen
        self.note.setText(tr("common_loading"))
        from ..core.workers import run_async
        run_async(self.w.pool, lambda: _categories_of(self.w, pl),
                  lambda got: self._fill(gen, pl, got),
                  lambda msg: self._failed(gen, msg))

    def _failed(self, gen: int, msg: str) -> None:
        if gen == self._gen and not sip.isdeleted(self):
            self.note.setText(tr("profile_allowed_failed", msg=msg))

    def _fill(self, gen: int, pl: dict, got: dict) -> None:
        """The source's categories with the ones ticked that were chosen -
        none for adults, by the name the lists show or the provider's own."""
        if gen != self._gen or sip.isdeleted(self):
            return
        from PyQt6.QtWidgets import QListWidgetItem

        from ..core.stores import CategoryOverrides
        pid = str(pl.get("id"))
        names = CategoryOverrides(self.w.settings, f"category_overrides_{pid}")
        mine = self.picked.get(pid, {})
        self.list.blockSignals(True)
        heads = {"live": "nav_tv", "vod": "nav_movies", "series": "nav_series"}
        offered = 0
        for mode in profiles.ALLOW_MODES:
            cats = got.get(mode) or []
            if not cats:
                continue
            head = QListWidgetItem(tr(heads[mode]))
            font = QFont(head.font())
            font.setBold(True)
            head.setFont(font)
            head.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(head)
            for c in cats:
                cid, own = str(c.get("category_id")), str(c.get("category_name") or "")
                shown = names.display_name(mode, cid, own) or own or cid
                if profiles.adult_category(shown, own):
                    continue
                it = QListWidgetItem(shown)
                it.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                it.setCheckState(Qt.CheckState.Checked if cid in mine.get(mode, set())
                                 else Qt.CheckState.Unchecked)
                it.setData(Qt.ItemDataRole.UserRole, (mode, cid))
                self.list.addItem(it)
                offered += 1
        self.list.blockSignals(False)
        self.note.setText("" if offered else tr("profile_allowed_none"))
        self.note.setVisible(not offered)

    def _ticked(self, it: Any) -> None:
        data = it.data(Qt.ItemDataRole.UserRole)
        if not data or not self._pid:
            return
        mode, cid = data
        ids = self.picked.setdefault(self._pid, {}).setdefault(mode, set())
        if it.checkState() == Qt.CheckState.Checked:
            ids.add(cid)
        else:
            ids.discard(cid)

    def chosen(self) -> dict[str, dict[str, list[str]]]:
        """The profile's ``allowed`` as ticked (core/profiles.clean_allowed)."""
        return profiles.clean_allowed({sid: {m: sorted(ids) for m, ids in modes.items()}
                                       for sid, modes in self.picked.items()})


# -- Settings > Profiles -------------------------------------------------------------

class ProfilesPage:
    """The Profiles page's cards (a page of SYSTEM - a service of its own):
    the profiles, add one, ask who is watching at start, and what a
    profile keeps of its own."""

    def __init__(self, page: Any, window: Any, dialog: QWidget) -> None:
        self.page, self.w, self.d = page, window, dialog
        self.card = page.card(tr("profile_card_list"))
        self.add_btn: QPushButton | None = None
        start = page.card(tr("profile_card_start"))
        from .settings_ui import ToggleSwitch
        self.ask = ToggleSwitch(window.profiles.ask_at_start)
        self.ask.setAccessibleName(tr("profile_ask_start"))
        start.row(tr("profile_ask_start"), self.ask, tr("profile_ask_start_hint"))
        self.ask.toggled.connect(window.profiles.set_ask_at_start)
        what = page.card(tr("profile_card_what"))
        what.row(tr("profile_own_label"), None, tr("profile_own_list"))
        what.row(tr("profile_shared_label"), None, tr("profile_shared_list"))
        self.refresh()

    def refresh(self) -> None:
        """The card made again: a row per profile, then Add a profile."""
        store = self.w.profiles
        store.reload()
        lay = self.card._lay
        for row, _t in self.card.rows:
            line = row.property("_divider")
            for w in (line, row):
                if w is not None:
                    lay.removeWidget(w)
                    w.hide()
                    w.deleteLater()
        self.card.rows = []
        active = profiles.active_id() or store.active_id
        for p in store.profiles():
            self._row(p, p.id == active)
        add_btn = QPushButton(tr("profile_add"))
        add_btn.clicked.connect(self._add)
        add_btn.setEnabled(len(store.profiles()) < profiles.MAX_PROFILES)
        self.card.row(tr("profile_add_label"), add_btn, tr("profile_add_hint"))
        self.add_btn = add_btn
        self.card.refresh()

    def _row(self, p: Profile, active: bool) -> QWidget:
        from .source_page import Origin
        edit = QPushButton(tr("profile_edit"))
        edit.setAccessibleName(f"{tr('profile_edit')} {p.name}")
        edit.clicked.connect(lambda _c=False, pid=p.id: self._edit(pid))
        remove = QPushButton(tr("profile_remove"))
        remove.setAccessibleName(f"{tr('profile_remove')} {p.name}")
        remove.setEnabled(not active and len(self.w.profiles.profiles()) > 1)
        if active:
            remove.setToolTip(tr("profile_remove_active"))
        remove.clicked.connect(lambda _c=False, pid=p.id: self._remove(pid))
        controls = QHBoxLayout()
        controls.setSpacing(6)
        controls.addWidget(edit)
        controls.addWidget(remove)
        tags = [tr("profile_kids_tag") if p.kids else tr("profile_everyone")]
        if p.allow_only:
            tags.append(tr("profile_only_allowed"))
        tags.append(tr("profile_pin_on") if p.has_pin else tr("profile_pin_off"))
        badge = None
        if active:
            badge = Origin()
            badge.set(tr("profile_in_use"), True)
        row = self.card.row(p.name, controls, " · ".join(tags), badge=badge)
        lay = row.layout()
        if lay is not None:
            lay.insertWidget(0, ProfileAvatar(p, 34), 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    def _add(self) -> None:
        dlg = ProfileDialog(self.d, self.w.profiles, parental=getattr(self.w, "parental", None),
                            window=self.w)
        if dlg.exec() and dlg.saved is not None:
            self.w._profiles_changed()
            self.refresh()

    def _edit(self, pid: str) -> None:
        p = self.w.profiles.get(pid)
        if p is None or not self.w._profile_unlock(p, self.d):
            return
        dlg = ProfileDialog(self.d, self.w.profiles, p, parental=getattr(self.w, "parental", None),
                            window=self.w)
        if dlg.exec():
            self.w._profiles_changed()
            self.refresh()

    def _remove(self, pid: str) -> None:
        from .message_box import confirm
        p = self.w.profiles.get(pid)
        if p is None or not self.w._profile_unlock(p, self.d):
            return
        if not confirm(self.d, tr("profile_remove_title"),
                       tr("profile_remove_confirm", name=p.name), default_yes=False, danger=True):
            return
        if self.w._profile_remove(pid):
            self.refresh()
        else:
            message_box.information(self.d, tr("profile_remove_title"), tr("profile_remove_active"))


# -- TV mode's picker -------------------------------------------------------------

class TvProfileTile(QAbstractButton):
    """A profile in TV mode: the avatar large, its name under it, the focus
    ring of TV mode's tiles."""

    def __init__(self, p: Profile, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from .tv_widgets import px
        self.profile = p
        self.payload = ("profile", p.id)
        self._s = px(220)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(p.name)
        self.setFixedSize(self._s + px(48), self._s + px(110))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return self.size()

    def paintEvent(self, _e: QPaintEvent) -> None:
        from .tv_widgets import font, px
        pr = QPainter(self)
        s = float(self._s)
        box = QRectF((self.width() - s) / 2, px(20), s, s)
        paint_avatar(pr, box, self.profile, s * 0.16, focus=self.hasFocus())
        pr.setFont(font(30, bold=self.hasFocus()))
        pr.setPen(QColor(P["text"] if self.hasFocus() else P["text2"]))
        fm = pr.fontMetrics()
        pr.drawText(QRectF(0, box.bottom() + px(18), self.width(), px(44)),
                    int(Qt.AlignmentFlag.AlignCenter),
                    fm.elidedText(self.profile.name, Qt.TextElideMode.ElideRight, self.width()))
        if self.profile.kids or self.profile.has_pin:
            pr.setFont(font(22))
            pr.setPen(QColor(P["muted"]))
            tag = " · ".join(([tr("profile_kids_tag")] if self.profile.kids else [])
                            + ([tr("profile_pin_on")] if self.profile.has_pin else []))
            pr.drawText(QRectF(0, box.bottom() + px(62), self.width(), px(32)),
                        int(Qt.AlignmentFlag.AlignCenter), tag)
        pr.end()

    def focusInEvent(self, e) -> None:
        super().focusInEvent(e)
        self.update()

    def focusOutEvent(self, e) -> None:
        super().focusOutEvent(e)
        self.update()


def tv_profiles_screen(page: Any) -> Any:
    """TV mode's "Who's watching?" screen (TvPage.screen("profiles"))."""
    from .tv_mode import TvScreen

    class ProfilesScreen(TvScreen):
        key = "profiles"

        def __init__(self, page: Any) -> None:
            super().__init__(page)
            from .tv_widgets import px
            lay = QVBoxLayout(self)
            lay.setContentsMargins(0, px(40), 0, 0)
            lay.addWidget(self._title(tr("profile_who")), 0, Qt.AlignmentFlag.AlignHCenter)
            lay.addSpacing(px(40))
            self.row = QHBoxLayout()
            self.row.setSpacing(px(24))
            holder = QHBoxLayout()
            holder.addStretch(1)
            holder.addLayout(self.row)
            holder.addStretch(1)
            lay.addLayout(holder)
            lay.addStretch(1)
            self.tiles: list[TvProfileTile] = []

        def enter(self) -> None:
            for t in self.tiles:
                t.deleteLater()
            self.tiles = []
            store = self.w.profiles
            store.reload()
            for p in store.profiles():
                t = TvProfileTile(p)
                t.clicked.connect(self._clicked)
                self.row.addWidget(t)
                self.tiles.append(t)

        def first_focus(self) -> QWidget | None:
            active = profiles.active_id()
            return next((t for t in self.tiles if t.profile.id == active),
                        self.tiles[0] if self.tiles else None)

        def focusables(self) -> list[QWidget]:
            return list(self.tiles)

        def activate(self, cur: QWidget) -> None:
            if isinstance(cur, TvProfileTile):
                cur.click()

        def _clicked(self) -> None:
            tile = self.sender()
            if isinstance(tile, TvProfileTile):
                self.w._profile_pick(tile.profile.id, tv=True)

    return ProfilesScreen(page)
