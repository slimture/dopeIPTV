"""Watch together's widgets (pure presentation; ui/mw_together.py does the
work): the dialog that starts or joins a room and shows its code, and the
calm strip under the video that says who is in.

Both draw what the window's together_view() says, and refresh when told."""

from __future__ import annotations

from PyQt6 import sip
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from ..core import together
from ..i18n import tr
from .theme import P

#: The member's status, as the others read it next to the name.
_STATUS_KEY = {"miss": "together_member_miss", "load": "together_member_load",
               "idle": "together_member_idle"}
_CONN_KEY = {"connecting": "together_conn_connecting", "net": "together_conn_net",
             "busy": "together_conn_busy"}


def _plain(label: QLabel) -> QLabel:
    """Names and titles come from other computers: never read as markup."""
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def member_lines(view: dict) -> list[str]:
    """One line per person in the room, this computer's first."""
    lines = [tr("together_you", name=view.get("me") or "")]
    for name, status in view.get("members") or []:
        key = _STATUS_KEY.get(status)
        lines.append(tr(key, name=name) if key else name)
    return lines


def note_text(view: dict) -> str:
    """The line under the names: what this computer should know."""
    notes = []
    conn = _CONN_KEY.get(view.get("conn") or "")
    if conn:
        notes.append(tr(conn))
    if view.get("note"):
        notes.append(view["note"])
    if view.get("live"):
        notes.append(tr("together_live_note"))
    if not notes and not view.get("members") and view.get("conn") == "ok":
        notes.append(tr("together_alone_join") if view.get("joined") else tr("together_alone"))
    return "\n".join(notes)


class TogetherDialog(QDialog):
    """Start a room or join one by its code; in a room, its code to share,
    who is in, and the way out. Not modal: it can stay open beside the
    video."""

    def __init__(self, win) -> None:
        super().__init__(win if isinstance(win, QWidget) else None)
        self.win = win
        self.setWindowTitle(tr("together_title"))
        self.setMinimumWidth(480)
        col = QVBoxLayout(self)
        col.setContentsMargins(24, 22, 24, 20)
        col.setSpacing(10)
        head = QLabel(tr("together_title"))
        head.setStyleSheet(f"font-size:16px; font-weight:700; color:{P['text']};")
        col.addWidget(head)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_out())
        self.pages.addWidget(self._build_in())
        col.addWidget(self.pages)
        privacy_note = _plain(QLabel(tr("together_privacy")))
        privacy_note.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        col.addWidget(privacy_note)
        buttons = QHBoxLayout()
        self.leave_btn = QPushButton(tr("together_leave"))
        self.leave_btn.clicked.connect(self._leave)
        buttons.addWidget(self.leave_btn)
        buttons.addStretch(1)
        close = QPushButton(tr("common_close"))
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        col.addLayout(buttons)
        self.refresh()

    # -- out of a room
    def _build_out(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)
        intro = _plain(QLabel(tr("together_intro")))
        intro.setStyleSheet(f"font-size:13px; color:{P['text2']};")
        col.addWidget(intro)
        self.private_lbl = _plain(QLabel(tr("together_private")))
        self.private_lbl.setStyleSheet(f"font-size:12px; color:{P['error']};")
        col.addWidget(self.private_lbl)
        name_row = QHBoxLayout()
        name_lbl = QLabel(tr("together_name"))
        name_row.addWidget(name_lbl)
        # The name typed last this session, else the profile's (mw_profiles).
        room_name = getattr(self.win, "_profile_room_name", lambda: "")
        self.name = QLineEdit(self.win._together_name or room_name() or "")
        self.name.setMaxLength(together.NAME_MAX)
        self.name.setPlaceholderText(tr("together_name_hint"))
        self.name.setAccessibleName(tr("together_name"))
        name_row.addWidget(self.name, 1)
        col.addLayout(name_row)
        start_row = QHBoxLayout()
        self.start_btn = QPushButton(tr("together_start"), objectName="Primary")
        self.start_btn.clicked.connect(self._start)
        start_row.addWidget(self.start_btn)
        hint = _plain(QLabel(tr("together_start_hint")))
        hint.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        start_row.addWidget(hint, 1)
        col.addLayout(start_row)
        col.addSpacing(6)
        join_lbl = QLabel(tr("together_join_label"))
        join_lbl.setStyleSheet(f"font-size:13px; color:{P['text2']};")
        col.addWidget(join_lbl)
        join_row = QHBoxLayout()
        self.code = QLineEdit()
        self.code.setPlaceholderText(together.format_code("K7PQM2XD"))
        self.code.setMaxLength(16)
        self.code.setAccessibleName(tr("together_code"))
        self.code.returnPressed.connect(self._join)
        join_row.addWidget(self.code, 1)
        self.join_btn = QPushButton(tr("together_join"))
        self.join_btn.clicked.connect(self._join)
        join_row.addWidget(self.join_btn)
        col.addLayout(join_row)
        self.error = _plain(QLabel(""))
        self.error.setStyleSheet(f"font-size:12px; color:{P['error']};")
        self.error.hide()
        col.addWidget(self.error)
        self.name.textChanged.connect(self._sync_buttons)
        self.code.textChanged.connect(self._sync_buttons)
        return page

    # -- in a room
    def _build_in(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        cap = QLabel(tr("together_code"))
        cap.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        col.addWidget(cap)
        code_row = QHBoxLayout()
        self.code_lbl = QLabel("")
        self.code_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.code_lbl.setStyleSheet(
            f"font-size:24px; font-weight:700; letter-spacing:3px; color:{P['text']};")
        code_row.addWidget(self.code_lbl)
        code_row.addStretch(1)
        self.copy_btn = QPushButton(tr("problem_copy"))
        self.copy_btn.clicked.connect(self._copy)
        code_row.addWidget(self.copy_btn)
        col.addLayout(code_row)
        share = _plain(QLabel(tr("together_share_hint", most=together.MAX_MEMBERS)))
        share.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        col.addWidget(share)
        col.addSpacing(6)
        who = QLabel(tr("together_in_room"))
        who.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        col.addWidget(who)
        self.members = _plain(QLabel(""))
        self.members.setStyleSheet(f"font-size:13px; color:{P['text']};")
        col.addWidget(self.members)
        self.playing = _plain(QLabel(""))
        self.playing.setStyleSheet(f"font-size:13px; color:{P['text2']};")
        col.addWidget(self.playing)
        self.note = _plain(QLabel(""))
        self.note.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        col.addWidget(self.note)
        return page

    # -- drawing
    def refresh(self) -> None:
        if sip.isdeleted(self):
            return
        view = self.win.together_view()
        inside = bool(view.get("in"))
        self.pages.setCurrentIndex(1 if inside else 0)
        for i in range(self.pages.count()):
            # Only the page shown counts for the height: the other one's
            # lines left a gap in the middle of this one.
            shown = i == self.pages.currentIndex()
            policy = QSizePolicy.Policy.Preferred if shown else QSizePolicy.Policy.Ignored
            self.pages.widget(i).setSizePolicy(policy, policy)
        self.leave_btn.setVisible(inside)
        if inside:
            self.code_lbl.setText(view.get("code") or "")
            self.members.setText("\n".join(member_lines(view)))
            title = view.get("title") or ""
            self.playing.setText(tr("together_now", title=title) if title else "")
            self.playing.setVisible(bool(title))
            text = note_text(view)
            self.note.setText(text)
            self.note.setVisible(bool(text))
        else:
            self.private_lbl.setVisible(bool(view.get("private")))
            self._sync_buttons()
        if self.isVisible():
            self._fit()

    def _fit(self) -> None:
        """As tall as its words at its width: wrapped lines are not in a
        plain size hint, and the intro and the names ran out of the box."""
        lay = self.layout()
        if lay is None:
            return
        lay.invalidate()
        lay.activate()
        w = max(self.width(), self.minimumWidth())
        h = lay.totalHeightForWidth(w) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
        h = max(h, lay.totalSizeHint().height())
        self.setMinimumHeight(h)
        self.resize(w, h)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fit()

    def _sync_buttons(self, *_a) -> None:
        private = bool(self.win.together_view().get("private"))
        named = bool(together.clean_name(self.name.text()))
        self.start_btn.setEnabled(named and not private)
        self.join_btn.setEnabled(named and not private and bool(self.code.text().strip()))
        self.name.setEnabled(not private)
        self.code.setEnabled(not private)

    def show_error(self, text: str) -> None:
        self.error.setText(text or "")
        self.error.setVisible(bool(text))
        if self.isVisible():
            self._fit()

    # -- what the buttons do
    def _start(self) -> None:
        self.show_error(self.win.together_start(self.name.text()) or "")
        self.refresh()

    def _join(self) -> None:
        if not self.join_btn.isEnabled():
            return
        self.show_error(self.win.together_join(self.code.text(), self.name.text()) or "")
        self.refresh()

    def _leave(self) -> None:
        self.win.together_leave()
        self.refresh()

    def _copy(self) -> None:
        code = self.win.together_view().get("code") or ""
        if not code:
            return
        QApplication.clipboard().setText(code)
        self.copy_btn.setText(tr("problem_copied"))
        QTimer.singleShot(1500, self._copied_done)

    def _copied_done(self) -> None:
        if not sip.isdeleted(self.copy_btn):
            self.copy_btn.setText(tr("problem_copy"))


class TogetherStrip(QWidget):
    """Under the video while in a room: a dot, "Watching together", the
    code, who is in - and a line for what this computer should know (a
    live channel cannot be in exact step, a title not in this source)."""

    def __init__(self, win, parent: QWidget | None = None) -> None:
        super().__init__(parent, objectName="TogetherStrip")
        self.win = win
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"QWidget#TogetherStrip {{ background:{P['sel']}; border-radius:10px; }}")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        col = QVBoxLayout(self)
        col.setContentsMargins(12, 8, 8, 8)
        col.setSpacing(3)
        row = QHBoxLayout()
        row.setSpacing(8)
        dot = QLabel("●")
        dot.setStyleSheet(f"color:{P['accent']}; font-size:11px; background:transparent;")
        row.addWidget(dot)
        self.title = QLabel(tr("together_strip"))
        self.title.setStyleSheet(
            f"font-size:12px; font-weight:600; color:{P['text']}; background:transparent;")
        row.addWidget(self.title)
        self.code = QLabel("")
        self.code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.code.setStyleSheet(
            f"font-size:12px; color:{P['muted']}; letter-spacing:1px; background:transparent;")
        row.addWidget(self.code)
        row.addStretch(1)
        style = (f"QPushButton {{ background:transparent; border:none; color:{P['text2']};"
                 " padding:2px 8px; font-size:12px; font-weight:600; }"
                 f"QPushButton:hover {{ background:{P['hover']}; border-radius:6px; }}")
        self.copy_btn = QPushButton(tr("problem_copy"))
        self.copy_btn.setStyleSheet(style)
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.clicked.connect(self._copy)
        row.addWidget(self.copy_btn)
        self.more_btn = QPushButton("…")
        self.more_btn.setStyleSheet(style)
        self.more_btn.setToolTip(tr("together_title"))
        self.more_btn.setAccessibleName(tr("together_title"))
        self.more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_btn.clicked.connect(self._open)
        row.addWidget(self.more_btn)
        self.leave_btn = QPushButton(tr("together_leave"))
        self.leave_btn.setStyleSheet(style)
        self.leave_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.leave_btn.clicked.connect(self._leave)
        row.addWidget(self.leave_btn)
        col.addLayout(row)
        self.names = _plain(QLabel(""))
        self.names.setStyleSheet(f"font-size:12px; color:{P['text2']}; background:transparent;")
        col.addWidget(self.names)
        self.note = _plain(QLabel(""))
        self.note.setStyleSheet(f"font-size:11px; color:{P['muted']}; background:transparent;")
        col.addWidget(self.note)
        self.hide()

    def refresh(self) -> None:
        if sip.isdeleted(self):
            return
        view = self.win.together_view()
        if not view.get("in"):
            self.hide()
            return
        self.code.setText(view.get("code") or "")
        self.names.setText(" · ".join(member_lines(view)))
        text = note_text(view)
        self.note.setText(text)
        self.note.setVisible(bool(text))
        self.show()

    def retranslate(self) -> None:
        self.title.setText(tr("together_strip"))
        self.copy_btn.setText(tr("problem_copy"))
        self.leave_btn.setText(tr("together_leave"))
        self.more_btn.setToolTip(tr("together_title"))
        self.refresh()

    def _copy(self) -> None:
        code = self.win.together_view().get("code") or ""
        if code:
            QApplication.clipboard().setText(code)
            self.copy_btn.setText(tr("problem_copied"))
            QTimer.singleShot(1500, self._copied_done)

    def _copied_done(self) -> None:
        if not sip.isdeleted(self.copy_btn):
            self.copy_btn.setText(tr("problem_copy"))

    def _open(self) -> None:
        self.win.open_together()

    def _leave(self) -> None:
        self.win.together_leave()
