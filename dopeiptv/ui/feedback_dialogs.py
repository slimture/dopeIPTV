"""Feedback and error reports in windows of their own (ui/mw_report.py
opens them; core/report.py scrubs, sends and keeps them): Send feedback
(``FeedbackDialog``), the offer of a report after an error or a crash -
shown in full before anything is sent (``ReportDialog``), and the answers
to feedback with a reply to the latest where it may have one
(``AnswersDialog``; an answer in the app's own words, ``answer_text``,
``answers_html``).
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QStyle, QVBoxLayout, QWidget,
)

from ..i18n import tr
from .theme import P


def answer_text(a: dict) -> str:
    """An admin's answer in the app's own words: its fixed part in this
    language, their own words as written."""
    code, version = str(a.get("code") or ""), str(a.get("version") or "")
    fixed = {"thanks": "fbr_thanks", "known": "fbr_known", "needinfo": "fbr_needinfo"}
    if code == "fixed":
        said = tr("fbr_fixed", version=version) if version else tr("fbr_fixed_next")
    else:
        said = tr(fixed[code]) if code in fixed else ""
    return " ".join(p for p in (said, str(a.get("text") or "").strip()) if p)


def answers_html(tickets: list) -> str:
    """The answered reports, newest first: what the person wrote (kept on
    this computer only), each answer with its day and their replies."""
    import datetime as _dt
    import html as _html

    from ..core import report

    def line(when, text: str, colour: str = "") -> str:
        words = f"<span style='color:{colour}'>{_html.escape(text)}</span>" if colour else _html.escape(text)
        return (f"<p style='margin:4px 0 0'><span style='color:{P['muted']}'>"
                f"{_dt.date.fromtimestamp(int(when or 0)).isoformat()}</span>&nbsp; {words}</p>")
    rows = []
    ordered = sorted(tickets, key=lambda t: -max((a.get("t", 0) for a in t.get("r") or [{}]), default=0))
    for n, t in enumerate(ordered):
        # Their own replies in their own colour, the answers as they were.
        said = "".join(line(e.get("t"), answer_text(e)) if kind == "answer"
                       else line(e.get("t"), tr("fbr_you_replied", text=e["w"]), P["text2"])
                       for kind, e in report.conversation(t))
        rows.append(f"<p style='margin:{12 if n else 0}px 0 0 0; color:{P['text2']}'>"
                    f"{_html.escape(tr('fbr_you', text=str(t.get('w') or '…')))}</p>{said}")
    return "".join(rows)


def _count(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


class AnswersDialog(QDialog):
    """Answers to feedback, in a window of their own: a card for each report
    that asked for one, newest first - the day it went, its first words
    (kept on this computer only), each answer with its day and the person's
    replies between them, the new ones marked and the waiting ones said -
    with Check now, and how it stays anonymous. Under an answer that lets
    them reply, a box and Send reply (*reply(code, text)*); what is typed
    there stays while the cards are drawn again. With no report yet, a way
    to send one."""

    #: The first words are kept to this many characters (mw_report).
    WORDS = 90
    #: The cards' room before they scroll.
    TALLEST = 460

    def __init__(self, parent, tickets: list, check=None, send=None, reply=None) -> None:
        super().__init__(parent)
        self._check, self._send, self._reply = check, send, reply
        #: (code, answer's index) new while this window has been open.
        self._new: set[tuple[str, int]] = set()
        #: What is typed in each card's reply box, and the replies on their way.
        self._drafts: dict[str, str] = {}
        self._sending: set[str] = set()
        self.cards: list[QFrame] = []
        self.reply_boxes: dict[str, Any] = {}
        self.reply_buttons: dict[str, QPushButton] = {}
        self.reply_notes: dict[str, QLabel] = {}
        self.send_btn: QPushButton | None = None
        self.setWindowTitle(tr("fbr_title"))
        self.setMinimumWidth(540)
        col = QVBoxLayout(self)
        col.setContentsMargins(24, 20, 24, 18)
        col.setSpacing(10)
        head = QLabel(tr("fbr_title"))
        head.setStyleSheet(f"font-size:17px; font-weight:700; color:{P['text']};")
        col.addWidget(head)
        self.list = QWidget(objectName="AnswersList")
        self.rows = QVBoxLayout(self.list)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(10)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.list)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea, QWidget#AnswersList { background: transparent; }")
        col.addWidget(self.scroll, 1)
        self.note = QLabel(tr("fb_answers_note"))
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        col.addWidget(self.note)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        buttons.addWidget(self.status, 1)
        self.check_btn = QPushButton(tr("fba_check"))
        self.check_btn.clicked.connect(self._check_clicked)
        self.close_btn = QPushButton(tr("common_close"), objectName="Primary")
        self.close_btn.clicked.connect(self.accept)
        buttons.addWidget(self.check_btn)
        buttons.addWidget(self.close_btn)
        col.addLayout(buttons)
        self.set_tickets(tickets)

    # -- what it shows ------------------------------------------------------------

    def set_tickets(self, tickets: list) -> None:
        """The reports as they are kept now; an answer not shown before
        (past its report's "seen") stays marked new while this is open."""
        import time

        from ..core import report
        for t in tickets:
            for i in range(_count(t.get("seen")), len(t.get("r") or [])):
                self._new.add((str(t.get("c")), i))
        # Typing a reply: the same box has the cursor where it was once the
        # cards are drawn again.
        typing = next(((c, b.textCursor().position()) for c, b in self.reply_boxes.items() if b.hasFocus()), None)
        while self.rows.count():
            item = self.rows.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.hide()
                w.deleteLater()
        self.cards = []
        self.reply_boxes, self.reply_buttons, self.reply_notes = {}, {}, {}
        self.send_btn = None
        ordered = sorted(tickets, key=lambda t: -float(t.get("t") or 0))
        for t in ordered:
            card = self._card(t)
            self.cards.append(card)
            self.rows.addWidget(card)
        if not ordered:
            self.rows.addWidget(self._empty())
        self.rows.addStretch(1)
        if typing is not None and typing[0] in self.reply_boxes:
            box = self.reply_boxes[typing[0]]
            box.setFocus()
            cursor = box.textCursor()
            cursor.setPosition(min(typing[1], len(box.toPlainText())))
            box.setTextCursor(cursor)
        asked = bool(report.open_codes(ordered, time.time()))
        self.check_btn.setVisible(asked)
        if not asked:
            self.status.clear()
        # One button stands out: Send feedback… while there is nothing yet.
        primary = "Primary" if ordered else ""
        if self.close_btn.objectName() != primary:
            self.close_btn.setObjectName(primary)
            self.close_btn.style().unpolish(self.close_btn)
            self.close_btn.style().polish(self.close_btn)
        if self.isVisible():
            self._fit()

    def _card(self, t: dict) -> QFrame:
        import html as _html

        from ..core import report
        from .qt_i18n import format_when
        card = QFrame(objectName="AnswerCard")
        card.setStyleSheet(f"QFrame#AnswerCard {{ background:{P['input']}; border-radius:12px; }}"
                           " QFrame#AnswerCard QLabel { background: transparent; }")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(4)
        code, answers = str(t.get("c")), [a for a in t.get("r") or [] if isinstance(a, dict)]
        top = QHBoxLayout()
        top.setSpacing(8)
        sent = QLabel(tr("fba_sent", date=format_when(float(t.get("t") or 0), "d MMM yyyy")))
        sent.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        top.addWidget(sent, 1)
        card.new = sum((code, i) in self._new for i in range(len(answers)))
        if card.new:
            pill = QLabel(tr("fba_new"))
            pill.setStyleSheet(f"QLabel {{ background:{P['accent']}; color:{P['on_accent']};"
                               f" border-radius:8px; padding:1px 8px; font-size:11px; font-weight:700; }}")
            top.addWidget(pill, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(top)
        words = " ".join(str(t.get("w") or "").split())
        if len(words) >= self.WORDS:
            words = words.rstrip(" .,;:-") + "…"
        you = QLabel(tr("fba_you", text=words or "…"))
        you.setTextFormat(Qt.TextFormat.PlainText)       # "<private>" is words, not a tag
        you.setWordWrap(True)
        you.setStyleSheet(f"font-size:13px; color:{P['text2']};")
        lay.addWidget(you)
        if not answers:
            wait = QLabel(tr("fba_waiting"))
            wait.setStyleSheet(f"font-size:13px; color:{P['muted']}; font-style:italic;")
            lay.addSpacing(2)
            lay.addWidget(wait)

        def words(said: str, colour: str) -> None:
            text = QLabel(said)
            text.setTextFormat(Qt.TextFormat.PlainText)
            text.setWordWrap(True)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            text.setStyleSheet(f"font-size:13px; color:{colour};")
            lay.addWidget(text)
        i = -1
        for kind, a in report.conversation(t):
            lay.addSpacing(8)
            if kind == "reply":
                # Their own reply, as they sent it (kept on this computer only).
                when = QLabel(tr("fba_you_replied", date=format_when(float(a["t"]), "d MMM yyyy")))
                when.setStyleSheet(f"font-size:12px; font-weight:600; color:{P['muted']};")
                lay.addWidget(when)
                words(a["w"], P["text2"])
                continue
            i += 1
            fresh = (code, i) in self._new
            dot = f"<span style='color:{P['accent']}'>●</span>&nbsp; " if fresh else ""
            when = QLabel(dot + _html.escape(tr("fba_answer", date=format_when(float(a.get("t") or 0),
                                                                              "d MMM yyyy"))))
            when.setTextFormat(Qt.TextFormat.RichText)
            when.setStyleSheet(f"font-size:12px; font-weight:600;"
                               f" color:{P['text'] if fresh else P['muted']};")
            lay.addWidget(when)
            said = answer_text(a)
            if said:
                words(said, P["text"])
        if self._reply is not None and report.can_reply(t):
            self._reply_box(lay, code)
        return card

    def _reply_box(self, lay: QVBoxLayout, code: str) -> None:
        """Under the answer that lets them reply: a word on what it is, the
        box (as long as the site takes), Send reply and how it went."""
        from functools import partial

        from PyQt6.QtWidgets import QPlainTextEdit
        lay.addSpacing(10)
        hint = QLabel(tr("fba_reply_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        lay.addWidget(hint)
        box = QPlainTextEdit()
        box.setPlaceholderText(tr("fba_reply_placeholder"))
        box.setAccessibleName(tr("fba_reply_placeholder"))
        box.setTabChangesFocus(True)
        box.setFixedHeight(84)
        box.setStyleSheet(f"QPlainTextEdit {{ background: {P['bg']}; color: {P['text']}; border: none;"
                          f" border-radius: 8px; padding: 6px 8px; selection-background-color: {P['sel']}; }}")
        box.setPlainText(self._drafts.get(code, ""))
        sending = code in self._sending
        box.setReadOnly(sending)
        lay.addWidget(box)
        row = QHBoxLayout()
        row.setSpacing(8)
        note = QLabel(tr("fba_reply_sending") if sending else "")
        note.setWordWrap(True)
        note.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        row.addWidget(note, 1)
        send = QPushButton(tr("fba_reply_send"))
        send.setEnabled(not sending and bool(box.toPlainText().strip()))
        send.clicked.connect(partial(self._reply_clicked, code))
        row.addWidget(send, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(row)
        self.reply_boxes[code], self.reply_buttons[code], self.reply_notes[code] = box, send, note
        box.textChanged.connect(partial(self._draft_changed, code))

    def _draft_changed(self, code: str) -> None:
        """Kept as typed (a card drawn again has it back), never longer than
        the site takes; Send reply only with something in it."""
        from ..core import report
        box, send = self.reply_boxes.get(code), self.reply_buttons.get(code)
        if box is None or send is None:
            return
        text = box.toPlainText()
        if len(text) > report.REPLY_MAX:
            at = min(box.textCursor().position(), report.REPLY_MAX)
            text = text[:report.REPLY_MAX]
            box.blockSignals(True)
            box.setPlainText(text)
            box.blockSignals(False)
            cursor = box.textCursor()
            cursor.setPosition(at)
            box.setTextCursor(cursor)
        self._drafts[code] = text
        send.setEnabled(code not in self._sending and bool(text.strip()))

    def _reply_clicked(self, code: str, *_a) -> None:
        text = self._drafts.get(code, "").strip()
        if text and self._reply is not None and code not in self._sending:
            self._reply(code, text)

    # -- a reply on its way -----------------------------------------------------

    def reply_sending(self, code: str) -> None:
        self._sending.add(code)
        box, send, note = (self.reply_boxes.get(code), self.reply_buttons.get(code),
                           self.reply_notes.get(code))
        if box is not None:
            box.setReadOnly(True)
        if send is not None:
            send.setEnabled(False)
        if note is not None:
            note.setStyleSheet(f"font-size:12px; color:{P['muted']};")
            note.setText(tr("fba_reply_sending"))

    def reply_sent(self, code: str, tickets: list) -> None:
        """It arrived: the box goes (until a newer answer lets them reply),
        their reply shows in its place."""
        self._sending.discard(code)
        self._drafts.pop(code, None)
        self.set_tickets(tickets)

    def reply_failed(self, code: str, why: str, tickets: list | None = None) -> None:
        """Not sent: *why* "wait" or "day" (feedback's own limits), "closed"
        (the site is not waiting for a reply to that answer - the box goes,
        what was typed is kept should a newer answer let them), else it did
        not reach the site; the words stay in the box."""
        self._sending.discard(code)
        if why == "closed":
            if tickets is not None:
                self.set_tickets(tickets)
            self.status.setStyleSheet(f"font-size:12px; color:{P['error']};")
            self.status.setText(tr("fba_reply_closed"))
            return
        box, send, note = (self.reply_boxes.get(code), self.reply_buttons.get(code),
                           self.reply_notes.get(code))
        if box is not None:
            box.setReadOnly(False)
        if send is not None:
            send.setEnabled(bool(self._drafts.get(code, "").strip()))
        if note is not None:
            note.setStyleSheet(f"font-size:12px; color:{P['error']};")
            note.setText(tr({"wait": "fb_wait", "day": "fb_enough"}.get(why, "fba_reply_failed")))

    def _empty(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(12, 28, 12, 20)
        lay.setSpacing(8)
        title = QLabel(tr("fba_empty_title"))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"font-size:15px; font-weight:700; color:{P['text']};")
        text = QLabel(tr("fba_empty", option=tr("fb_answers_opt")))
        text.setWordWrap(True)
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text.setStyleSheet(f"font-size:13px; color:{P['text2']};")
        self.send_btn = QPushButton(tr("feedback_menu") + "…", objectName="Primary")
        self.send_btn.clicked.connect(self._send_clicked)
        lay.addWidget(title)
        lay.addWidget(text)
        lay.addSpacing(6)
        lay.addWidget(self.send_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        return box

    # -- asking -------------------------------------------------------------------

    def show_checking(self) -> None:
        self.check_btn.setEnabled(False)
        self.status.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        self.status.setText(tr("fba_checking"))

    def show_result(self, ok: bool, fresh: int, when: float) -> None:
        from .qt_i18n import format_when
        self.check_btn.setEnabled(True)
        if not ok:
            self.status.setStyleSheet(f"font-size:12px; color:{P['error']};")
            self.status.setText(tr("fba_failed"))
            return
        self.status.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        self.status.setText(tr("fba_checked_new" if fresh else "fba_checked_none",
                               time=format_when(when, "HH:mm")))

    def _check_clicked(self, *_a) -> None:
        if self._check is not None:
            self._check()

    def _send_clicked(self, *_a) -> None:
        if self._send is not None:
            self._send()
        else:
            self.accept()

    # -- size ---------------------------------------------------------------------

    def _fit(self) -> None:
        """As tall as the cards at this width, up to a screenful's share -
        then they scroll."""
        width = max(self.width(), self.minimumWidth())
        self.rows.activate()

        def tall(inner: int) -> int:
            h = self.list.heightForWidth(inner) if self.rows.hasHeightForWidth() else -1
            return h if h > 0 else self.list.sizeHint().height()
        h = tall(width - 48)
        if h + 2 <= self.TALLEST:
            # Exactly as tall as the cards: the scroll area's own size hint
            # (its content at no particular width) is taller and left a gap.
            self.scroll.setFixedHeight(h + 2)
        else:
            # It scrolls, and grows with the window.
            self.scroll.setMinimumHeight(self.TALLEST)
            self.scroll.setMaximumHeight(16_777_215)
        lay = self.layout()
        if lay is not None:
            lay.activate()
            self.resize(width, lay.totalHeightForWidth(width) if lay.hasHeightForWidth()
                        else lay.totalSizeHint().height())

    def showEvent(self, event) -> None:  # noqa: N802 - Qt hook
        super().showEvent(event)
        self._fit()


#: The privacy policy's part on the app - what a report and feedback hold
#: and what dopeiptv.org keeps of them. ?ref=: from the app, for the site's
#: statistics (see mw_updates).
PRIVACY_URL = "https://dopeiptv.org/privacy/?ref=app-feedback#app"


def sent_with_text(picked: bool = False, code: bool = False) -> str:
    """The line under a report that says what goes with its text: the
    version and the system as they are sent, what was picked in the
    window and an answer code's hash when it goes."""
    from ..core import report
    items = report.sent_beside()
    if picked:
        items.append(tr("report_item_picked"))
    if code:
        items.append(tr("report_item_code"))
    return tr("report_sent_with", items=" · ".join(items))


def privacy_link() -> QPushButton:
    """A small link under a report's or feedback's fine print: the privacy
    policy, in the browser."""
    from ..core.xdg import open_url
    btn = QPushButton(tr("fb_privacy_link"))
    btn.setFlat(True)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setToolTip(PRIVACY_URL.split("?")[0])
    btn.setStyleSheet(f"QPushButton {{ border:none; background:transparent; padding:0;"
                      f" color:{P['accent']}; font-size:12px; font-weight:600; }}"
                      f" QPushButton:hover {{ text-decoration:underline; }}")
    btn.clicked.connect(lambda: open_url(PRIVACY_URL))
    return btn


class FeedbackDialog(QDialog):
    """Feedback, asked the way the answers are useful: what it is about,
    for something that does not work where and how often, the person's
    words, and what to attach - each on its own, versions only by default.
    What is sent can be shown in full and changed before Send."""

    TOPICS = (("bug", "fb_t_bug"), ("idea", "fb_t_idea"), ("like", "fb_t_like"), ("question", "fb_t_question"))
    AREAS = (("playback", "fb_a_play"), ("guide", "fb_a_guide"), ("recording", "fb_a_rec"),
             ("lists", "fb_a_lists"), ("settings", "fb_a_settings"), ("cast", "fb_a_cast"),
             ("updates", "fb_a_updates"), ("other", "fb_a_other"))
    OFTEN = (("always", "fb_o_always"), ("sometimes", "fb_o_sometimes"), ("once", "fb_o_once"))

    def __init__(self, parent, details, has_log_file: bool = False, answers: list | None = None,
                 all_answers=None, copy_only: bool = False) -> None:
        super().__init__(parent)
        self._all_answers = all_answers
        self.copy_only = copy_only
        from PyQt6.QtGui import QFont
        from PyQt6.QtWidgets import QButtonGroup, QCheckBox, QComboBox, QGridLayout, QPlainTextEdit, QScrollArea

        from .settings_ui import Chip, _Flow
        self._details = details
        self._edited = False
        self.setWindowTitle(tr("feedback_title"))
        self.setMinimumWidth(600)
        # Everything but Cancel and Send scrolls: the answers, the questions
        # and the preview together are taller than a laptop's screen, and a
        # window as tall as all of it went off the screen with Send under it.
        self._body = QWidget()
        self._body.setObjectName("FeedbackBody")
        col = QVBoxLayout(self._body)
        col.setContentsMargins(24, 20, 24, 8)
        col.setSpacing(8)

        def label(text: str, size: int = 12, colour: str = "", bold: bool = True) -> QLabel:
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"font-size:{size}px; font-weight:{700 if bold else 400};"
                              f" color:{colour or P['muted']};")
            return lbl
        col.addWidget(label(tr("feedback_title"), 17, P["text"]))
        col.addWidget(label(tr("feedback_intro"), 13, P["text2"], bold=False))
        # A modified build: nothing goes to dopeiptv.org, the text can be copied.
        self.not_official: QLabel | None = None
        if copy_only:
            self.not_official = label(tr("build_reports_elsewhere"), 13, P["warn"], bold=False)
            col.addWidget(self.not_official)
        # Answers to earlier feedback (asked for with its code), first.
        self.answers_box: QWidget | None = None
        self.all_btn: QPushButton | None = None
        if answers:
            col.addSpacing(4)
            title_row = QHBoxLayout()
            title_row.addWidget(label(tr("fbr_title")), 1)
            if all_answers is not None:
                # Every report and its answers, in their own window.
                self.all_btn = QPushButton(tr("fba_all") + "…")
                self.all_btn.setFlat(True)
                self.all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                self.all_btn.setStyleSheet(f"QPushButton {{ border:none; background:transparent; padding:0;"
                                           f" color:{P['accent']}; font-size:12px; font-weight:600; }}"
                                           f" QPushButton:hover {{ text-decoration:underline; }}")
                self.all_btn.clicked.connect(self._open_all_answers)
                title_row.addWidget(self.all_btn)
            col.addLayout(title_row)
            inner = QLabel(answers_html(answers))
            inner.setWordWrap(True)
            inner.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            inner.setTextFormat(Qt.TextFormat.RichText)
            inner.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            inner.setStyleSheet(f"font-size:13px; color:{P['text']}; padding:10px 12px;")
            scroll = QScrollArea()
            scroll.setWidget(inner)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            scroll.setStyleSheet(f"QScrollArea {{ background:{P['input']}; border-radius:10px; }}"
                                 f" QScrollArea > QWidget > QWidget {{ background:transparent; }}")
            # As tall as the answers at the window's width, up to a few of them.
            scroll.setFixedHeight(min(180, inner.heightForWidth(552) + 4))
            self.answers_box = scroll
            col.addWidget(scroll)
        col.addSpacing(4)
        col.addWidget(label(tr("fb_topic")))
        chips = _Flow(6, False)
        self._topics = QButtonGroup(self)
        self._topics.setExclusive(True)
        self.topic_chips: dict[str, Chip] = {}
        for key, text in self.TOPICS:
            chip = Chip(tr(text), False)
            self._topics.addButton(chip)
            self.topic_chips[key] = chip
            chips.flow.addWidget(chip)
        col.addWidget(chips)
        self.bug_row = QWidget()
        bug = QGridLayout(self.bug_row)
        bug.setContentsMargins(0, 2, 0, 0)
        bug.setHorizontalSpacing(10)
        self.area = QComboBox()
        for key, text in self.AREAS:
            self.area.addItem(tr(text), key)
        self.often = QComboBox()
        for key, text in self.OFTEN:
            self.often.addItem(tr(text), key)
        bug.addWidget(label(tr("fb_where")), 0, 0)
        bug.addWidget(label(tr("fb_often")), 0, 1)
        bug.addWidget(self.area, 1, 0)
        bug.addWidget(self.often, 1, 1)
        self.bug_row.hide()
        col.addWidget(self.bug_row)
        box_style = (f"QPlainTextEdit {{ background: {P['input']}; color: {P['text']};"
                     f" border: none; border-radius: 10px; padding: 8px 10px;"
                     f" selection-background-color: {P['sel']}; }}")
        self.message = QPlainTextEdit()
        self.message.setPlaceholderText(tr("feedback_placeholder"))
        self.message.setStyleSheet(box_style)
        self.message.setFixedHeight(104)
        col.addWidget(self.message)
        col.addWidget(label(tr("fb_attach")))
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        self.attach = {"versions": QCheckBox(tr("fb_att_versions")), "log": QCheckBox(tr("fb_att_log")),
                       "file": QCheckBox(tr("fb_att_file")), "source": QCheckBox(tr("fb_att_source"))}
        self.attach["versions"].setChecked(True)
        self.attach["file"].setVisible(has_log_file)
        for n, key in enumerate(("versions", "log", "source", "file")):
            grid.addWidget(self.attach[key], n // 2, n % 2)
        col.addLayout(grid)
        self.show_btn = QPushButton(tr("fb_show") + "  \u25be")
        self.show_btn.setFlat(True)
        self.show_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.show_btn.setStyleSheet(f"QPushButton {{ border:none; background:transparent; padding:2px 0;"
                                    f" text-align:left; color:{P['text2']}; font-size:12px; }}"
                                    f" QPushButton:hover {{ color:{P['text']}; }}")
        col.addWidget(self.show_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.preview = QPlainTextEdit()
        mono = QFont("monospace")
        mono.setStyleHint(QFont.StyleHint.TypeWriter)
        self.preview.setFont(mono)
        self.preview.setStyleSheet(box_style)
        self.preview.setFixedHeight(190)
        self.preview.hide()
        col.addWidget(self.preview)
        # An answer in the app: a code kept here, its hash with the report.
        self.answers = QCheckBox(tr("fb_answers_opt"))
        self.answers.setChecked(not copy_only)
        self.answers.setToolTip(tr("fb_answers_note"))
        col.addWidget(self.answers)
        note = label(tr("fb_answers_note") + " " + tr("report_privacy"), 12, bold=False)
        col.addWidget(note)
        # What goes beside the text, said as it changes (the answers' code).
        self.beside = label("", 12, bold=False)
        self.beside.setTextFormat(Qt.TextFormat.PlainText)
        col.addWidget(self.beside)
        self.answers.toggled.connect(lambda *_a: self._say_beside())
        self._say_beside()
        self.answers.setVisible(not copy_only)
        note.setVisible(not copy_only)
        self.beside.setVisible(not copy_only)
        col.addWidget(privacy_link(), 0, Qt.AlignmentFlag.AlignLeft)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._scroll = QScrollArea()
        self._scroll.setObjectName("FeedbackScroll")
        self._scroll.setWidget(self._body)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self._scroll, 1)
        self._button_bar = QWidget()
        buttons = QHBoxLayout(self._button_bar)
        buttons.setContentsMargins(24, 10, 24, 18)
        buttons.addStretch(1)
        cancel = QPushButton(tr("common_cancel"))
        self.send_btn = QPushButton(tr("build_copy_report" if copy_only else "report_send"),
                                    objectName="Primary")
        cancel.clicked.connect(self.reject)
        self.send_btn.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.send_btn)
        outer.addWidget(self._button_bar)
        self._topics.buttonToggled.connect(lambda *_a: self._topic_changed())
        for box in self.attach.values():
            box.toggled.connect(lambda *_a: self._refresh())
        # The log's part that matters follows "Where?".
        self.area.currentIndexChanged.connect(lambda *_a: self._refresh())
        self.message.textChanged.connect(self._ready)
        self.preview.textChanged.connect(self._preview_edited)
        self.show_btn.clicked.connect(self._toggle_preview)
        self._refresh()
        self._ready()
        self.message.setFocus()

    def topic(self) -> str:
        return next((k for k, chip in self.topic_chips.items() if chip.isChecked()), "")

    def _say_beside(self) -> None:
        self.beside.setText(sent_with_text(picked=True, code=self.answers.isChecked()))

    def _open_all_answers(self, *_a) -> None:
        if self._all_answers is not None:
            self._all_answers(self)

    def _topic_changed(self) -> None:
        t = self.topic()
        self.bug_row.setVisible(t == "bug")
        self.message.setPlaceholderText(tr({"bug": "fb_ph_bug", "idea": "fb_ph_idea", "like": "fb_ph_like",
                                            "question": "fb_ph_question"}.get(t, "feedback_placeholder")))
        if t == "bug" and not self._edited:
            self.attach["log"].setChecked(True)
            self.attach["source"].setChecked(True)
        self._refresh()
        self._ready()
        self._fit()

    #: How short the window may be made: the rest scrolls.
    SHORTEST = 360
    #: Its share of the screen's height at most.
    SCREEN_SHARE = 0.9

    def _fit(self) -> None:
        """As tall as what it holds at its width, up to the screen's room:
        the questions and the preview come and go, and wrapped text (the
        topics, the notes) is taller in some languages - a plain size hint
        leaves it overlapping. Past that room it scrolls, Cancel and Send
        always in sight, and the window can be made shorter still."""
        lay = self._body.layout()
        if lay is None:
            return
        lay.invalidate()
        lay.activate()
        w = max(self.width(), self.minimumWidth())
        h = lay.totalHeightForWidth(w) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
        h = max(h, lay.totalSizeHint().height())
        want = h + self._button_bar.sizeHint().height()
        room = self._room()
        cap = int(room.height() * self.SCREEN_SHARE) if room is not None else want
        self.setMinimumHeight(min(want, self.SHORTEST, cap))
        self.resize(w, max(self.minimumHeight(), min(want, cap)))
        if self.isVisible():
            # Once the window manager has the new size and its frame - and
            # again a moment later: macOS shows a window after Qt does, and a
            # move before then can be lost.
            QTimer.singleShot(0, self._keep_on_screen)
            QTimer.singleShot(150, self._keep_on_screen)

    def _room(self):
        """The screen's room for windows (the Dock and menu bar, the panel
        left out), on the screen its parent window is on - the dialog's own
        screen is only known once it is shown."""
        parent = self.parentWidget()
        screen = (parent.window().screen() if parent is not None else None) or self.screen() \
            or QApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None else None

    def _place(self) -> None:
        """Centred over its window and wholly in the screen's room, title
        bar and all - set before the window appears. Qt centres a dialog by
        its parent alone, and a tall one over a window lower on the screen
        went on under the Dock, Send with it."""
        room = self._room()
        if room is None:
            return
        title = self.style().pixelMetric(QStyle.PixelMetric.PM_TitleBarHeight) or 28
        w, h = self.width(), self.height() + title
        parent = self.parentWidget()
        if parent is not None:
            over = parent.window().frameGeometry()
            x, y = over.x() + (over.width() - w) // 2, over.y() + (over.height() - h) // 2
        else:
            x, y = room.x() + (room.width() - w) // 2, room.y() + (room.height() - h) // 2
        x = max(room.left(), min(x, room.right() - w + 1))
        y = max(room.top(), min(y, room.bottom() - h + 1))
        self.move(x, y)

    def _keep_on_screen(self) -> None:
        """Moved up (or in) so none of it is under the Dock or off the
        screen - its title bar first."""
        room = self._room()
        if room is None or not self.isVisible():
            return
        frame = self.frameGeometry()
        x = min(frame.x(), room.right() - frame.width() + 1)
        y = min(frame.y(), room.bottom() - frame.height() + 1)
        x, y = max(x, room.left()), max(y, room.top())
        if (x, y) != (frame.x(), frame.y()):
            self.move(x, y)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fit()
        if not getattr(self, "_placed", False):
            # Before the window appears (Qt sends this event first).
            self._placed = True
            self._place()

    def options(self) -> dict:
        out: dict = {k: box.isChecked() and box.isVisibleTo(self) for k, box in self.attach.items()}
        out["area"] = str(self.area.currentData()) if self.topic() == "bug" else ""
        return out

    def _refresh(self) -> None:
        if self._edited:
            return
        self._setting = True
        self.preview.setPlainText(self._details(self.options()))
        self._setting = False

    def _preview_edited(self) -> None:
        if not getattr(self, "_setting", False):
            self._edited = True

    def _toggle_preview(self) -> None:
        on = not self.preview.isVisible()
        self.preview.setVisible(on)
        self.show_btn.setText(tr("fb_hide" if on else "fb_show") + ("  \u25b4" if on else "  \u25be"))
        self._fit()
        if on:
            # What opened is what they asked to see: scrolled into sight.
            QTimer.singleShot(0, lambda: self._scroll.ensureWidgetVisible(self.preview, 0, 12))

    def _ready(self) -> None:
        self.send_btn.setEnabled(bool(self.topic()) and bool(self.message.toPlainText().strip()))

    def fields(self) -> dict[str, str]:
        out = {"topic": self.topic()}
        if out["topic"] == "bug":
            out["area"] = str(self.area.currentData())
            out["often"] = str(self.often.currentData())
        return out

    def payload(self) -> str:
        parts = [self.message.toPlainText().strip()]
        extra = self.preview.toPlainText().strip()
        if extra:
            parts.append(extra)
        return "\n\n".join(p for p in parts if p)


class ReportDialog(QDialog):
    """An error report or feedback, shown in full before anything is sent:
    what it holds, that nothing in it identifies a stream or a person, and
    a box where any of it can be changed or deleted. Sent only on Send."""

    def __init__(self, parent, kind: str, text: str, intro: str = "", copy_only: bool = False) -> None:
        super().__init__(parent)
        from PyQt6.QtGui import QFont
        from PyQt6.QtWidgets import QCheckBox, QPlainTextEdit
        self.kind = kind
        self.copy_only = copy_only
        feedback = kind == "feedback"
        self.setWindowTitle(tr("feedback_title") if feedback else tr("report_title"))
        self.setMinimumWidth(600)
        col = QVBoxLayout(self)
        col.setContentsMargins(24, 22, 24, 20)
        col.setSpacing(8)
        head = QLabel(tr("feedback_title") if feedback else tr("report_title"))
        head.setStyleSheet(f"font-size:16px; font-weight:700; color:{P['text']};")
        col.addWidget(head)
        about = QLabel(intro or (tr("feedback_intro") if feedback else tr("report_intro")))
        about.setWordWrap(True)
        about.setStyleSheet(f"font-size:13px; color:{P['text2']};")
        col.addWidget(about)
        # A modified build: nothing goes to dopeiptv.org, the text can be copied.
        self.not_official: QLabel | None = None
        if copy_only:
            self.not_official = QLabel(tr("build_reports_elsewhere"))
            self.not_official.setWordWrap(True)
            self.not_official.setStyleSheet(f"font-size:13px; color:{P['warn']};")
            col.addWidget(self.not_official)
        box_style = (f"QPlainTextEdit {{ background: {P['input']}; color: {P['text']};"
                     f" border: none; border-radius: 8px; padding: 6px 8px;"
                     f" selection-background-color: {P['sel']}; }}")
        self.message: QPlainTextEdit | None = None
        if feedback:
            self.message = QPlainTextEdit()
            self.message.setPlaceholderText(tr("feedback_placeholder"))
            self.message.setStyleSheet(box_style)
            self.message.setFixedHeight(110)
            col.addWidget(self.message)
        self.include = QCheckBox(tr("report_include"))
        self.include.setChecked(not feedback)
        col.addWidget(self.include)
        what = QLabel(tr("report_what_is_sent"))
        what.setStyleSheet(f"font-size:12px; font-weight:700; color:{P['muted']};")
        col.addWidget(what)
        self.text = QPlainTextEdit(text)
        mono = QFont("monospace")
        mono.setStyleHint(QFont.StyleHint.TypeWriter)
        self.text.setFont(mono)
        self.text.setStyleSheet(box_style)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.text.setMinimumHeight(self.text.fontMetrics().lineSpacing() * 12 + 18)
        col.addWidget(self.text, 1)
        self.include.toggled.connect(self.text.setVisible)
        self.include.toggled.connect(what.setVisible)
        self.text.setVisible(self.include.isChecked())
        what.setVisible(self.include.isChecked())
        privacy = QLabel(tr("report_privacy"))
        privacy.setWordWrap(True)
        privacy.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        col.addWidget(privacy)
        # What goes beside the text (report.send_status's other fields).
        self.beside: QLabel | None = None
        if not copy_only:
            self.beside = QLabel(sent_with_text())
            self.beside.setTextFormat(Qt.TextFormat.PlainText)
            self.beside.setWordWrap(True)
            self.beside.setStyleSheet(f"font-size:12px; color:{P['muted']};")
            col.addWidget(self.beside)
        col.addWidget(privacy_link(), 0, Qt.AlignmentFlag.AlignLeft)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton(tr("common_cancel"))
        self.send_btn = QPushButton(tr("build_copy_report" if copy_only else "report_send"),
                                    objectName="Primary")
        cancel.clicked.connect(self.reject)
        self.send_btn.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.send_btn)
        col.addLayout(buttons)
        if self.message is not None:
            self.message.setFocus()

    def payload(self) -> str:
        """What is sent: the person's words, then the report as they left it."""
        parts = []
        if self.message is not None and self.message.toPlainText().strip():
            parts.append(self.message.toPlainText().strip())
        if self.include.isChecked() and self.text.toPlainText().strip():
            parts.append(self.text.toPlainText().strip())
        return "\n\n".join(parts)
