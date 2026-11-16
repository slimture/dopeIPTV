"""One place for the app's transient notices.

Two kinds, drawn together in one corner of the window - the bottom of the
list pane on the side away from the sidebar (mirrored right to left), just
above its status line; never over the video, nothing drawn while the video
fills the screen or TV mode shows:

- **Activity** - background work the window does: a list or the guide
  loading, a scan, a download, a sync. Each job is started with a token and
  ended by its owner (``finish`` / ``fail`` / ``cancel``), replaced by a
  newer job with the same key, or let go by its own safety timeout - a job
  can never stay. One compact line shows it: a spinner and the newest job's
  words, "· 2 more" for the rest; all of them on hover and on a click.
- **Cards** - what needs the person: an error, an offer with its action, a
  short note. A stack of small cards, the newest nearest the corner, each
  with a dismiss; gone by itself after its time unless it waits for an
  answer; the same words (or key) again refresh the card already there
  instead of stacking a second. Held while the video fills the screen or TV
  mode shows, then shown (a plain note that went stale meanwhile is dropped).

What is about the picture itself (the loading overlay, reconnecting, the
stream banner, a typed channel number) stays over the video, and real
questions stay modal dialogs.

``NoticeHub`` is the state and its timers, no widgets; ``NoticeArea`` draws
it. Producers reach the window's hub through ``hub_of(window)``, which is a
quiet stand-in on a window without one (a test's).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from functools import partial
from typing import Any, Callable

from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QRegion
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton,
    QToolTip, QVBoxLayout, QWidget,
)

from ..core.log import log
from ..i18n import tr
from .theme import P

LEVELS = ("info", "warning", "error")


@dataclass
class Job:
    token: int
    text: str
    key: str | None
    started: float


@dataclass
class Card:
    id: int
    text: str
    level: str
    action: Callable[[], Any] | None
    action_label: str | None
    ms: int                 # 0: it waits for an answer
    key: str | None
    posted: float


class NoticeHub(QObject):
    """The window's notices: jobs under tokens, cards in a stack."""

    changed = pyqtSignal()

    #: A job its owner never ended is let go after this (each word from it -
    #: ``update`` - starts the time again).
    JOB_TIMEOUT_MS = 10 * 60_000
    #: How long a card stays, by level, when its producer names no time.
    CARD_MS = {"info": 5000, "warning": 8000, "error": 12000}
    MAX_CARDS = 3
    #: A plain note held this long (seconds) while the video filled the
    #: screen is no news any more when it comes back.
    HELD_STALE_S = 120.0

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._seq = 0
        self._jobs: dict[int, Job] = {}
        self._job_timers: dict[int, QTimer] = {}
        self._on_expire: dict[int, Callable[[], Any] | None] = {}
        self._cards: list[Card] = []
        self._card_timers: dict[int, QTimer] = {}
        self._held = False

    # -- jobs -------------------------------------------------------------------

    def start(self, text: str, *, key: str | None = None, timeout_ms: int | None = None,
              on_expire: Callable[[], Any] | None = None) -> int:
        """A job begins; its token is what ends it. A job already running
        under *key* is replaced (ended quietly - its token is dead from now
        on). *on_expire* runs if the safety timeout lets it go."""
        if key is not None:
            old = self.token_for(key)
            if old:
                self._end(old, emit=False)
        self._seq += 1
        token = self._seq
        self._jobs[token] = Job(token, str(text or ""), key, time.monotonic())
        self._on_expire[token] = on_expire
        t = QTimer(self)
        t.setSingleShot(True)
        t.setInterval(max(1, int(timeout_ms or self.JOB_TIMEOUT_MS)))
        t.timeout.connect(partial(self._expired, token))
        t.start()
        self._job_timers[token] = t
        self.changed.emit()
        return token

    def update(self, token: int, text: str | None = None) -> bool:
        """A word from a running job: its safety time starts again, and
        *text* replaces its words. False for a job that is over."""
        job = self._jobs.get(token)
        if job is None:
            return False
        self._job_timers[token].start()
        if text is not None and str(text) != job.text:
            job.text = str(text)
            self.changed.emit()
        return True

    def finish(self, token: int) -> bool:
        """The job is done. False for one that was over already (a late
        answer of a replaced job changes nothing)."""
        return self._end(token)

    def cancel(self, token: int) -> bool:
        return self._end(token)

    def fail(self, token: int, text: str, **card: Any) -> bool:
        """The job failed: it ends and *text* is said as an error card -
        only for a job still running (a replaced one's failure is no news)."""
        if not self._end(token):
            return False
        card.setdefault("level", "error")
        self.post(text, **card)
        return True

    def end_key(self, key: str) -> bool:
        token = self.token_for(key)
        return self._end(token) if token else False

    def token_for(self, key: str) -> int:
        for job in self._jobs.values():
            if job.key == key:
                return job.token
        return 0

    def alive(self, token: int) -> bool:
        return token in self._jobs

    def jobs(self) -> list[Job]:
        """The running jobs, the newest first."""
        return sorted(self._jobs.values(), key=lambda j: j.token, reverse=True)

    def _end(self, token: int, emit: bool = True) -> bool:
        if token not in self._jobs:
            return False
        del self._jobs[token]
        self._on_expire.pop(token, None)
        t = self._job_timers.pop(token, None)
        if t is not None:
            t.stop()
            t.deleteLater()
        if emit:
            self.changed.emit()
        return True

    def _expired(self, token: int) -> None:
        job = self._jobs.get(token)
        if job is None:
            return
        cb = self._on_expire.get(token)
        log.info("notices: a job its owner did not end was let go (%s)", job.key or "-")
        self._end(token)
        if cb is not None:
            try:
                cb()
            except Exception:
                log.debug("notices: the job's expiry hook failed", exc_info=True)

    # -- cards ------------------------------------------------------------------

    def post(self, text: str, *, level: str = "info", action: Callable[[], Any] | None = None,
             action_label: str | None = None, ms: int | None = None, key: str | None = None,
             answer: bool = False) -> int:
        """A card. *action* is what its button (or, without *action_label*,
        a click on the card) does; *ms* how long it stays (by level when
        not given); *answer* keeps it until the person acts or dismisses
        it. The same *key* - or the same words at the same level - again
        refreshes the card already there."""
        text = str(text or "").strip()
        if not text:
            return 0
        level = level if level in LEVELS else "info"
        stay = 0 if answer else int(ms if ms else self.CARD_MS[level])
        card = next((c for c in self._cards if (key is not None and c.key == key)
                     or (key is None and c.key is None and c.text == text
                         and c.level == level)), None)
        if card is not None:
            self._cards.remove(card)
            card.text, card.level, card.action = text, level, action
            card.action_label, card.ms, card.posted = action_label, stay, time.monotonic()
        else:
            self._seq += 1
            card = Card(self._seq, text, level, action, action_label, stay, key,
                        time.monotonic())
        self._cards.append(card)
        while len(self._cards) > self.MAX_CARDS:
            self._drop(self._cards[0].id)
        self._time_card(card)
        self.changed.emit()
        return card.id

    def dismiss(self, card_id: int) -> bool:
        if not any(c.id == card_id for c in self._cards):
            return False
        self._drop(card_id)
        self.changed.emit()
        return True

    def dismiss_key(self, key: str) -> bool:
        card = next((c for c in self._cards if c.key == key), None)
        return self.dismiss(card.id) if card is not None else False

    def activate(self, card_id: int) -> bool:
        """The card's action: the card goes, then the action runs - after
        this event, since what it opens may run an event loop of its own."""
        card = next((c for c in self._cards if c.id == card_id), None)
        if card is None:
            return False
        act = card.action
        self.dismiss(card_id)
        if act is not None:
            QTimer.singleShot(0, act)
        return True

    def cards(self) -> list[Card]:
        """The cards, the oldest first."""
        return list(self._cards)

    def texts(self) -> list[str]:
        return [c.text for c in self._cards]

    def _drop(self, card_id: int) -> None:
        self._cards = [c for c in self._cards if c.id != card_id]
        t = self._card_timers.pop(card_id, None)
        if t is not None:
            t.stop()
            t.deleteLater()

    def _time_card(self, card: Card) -> None:
        t = self._card_timers.get(card.id)
        if card.ms <= 0 or self._held:
            if t is not None:
                t.stop()
            return
        if t is None:
            t = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(partial(self.dismiss, card.id))
            self._card_timers[card.id] = t
        t.start(card.ms)

    # -- held while the video fills the screen ----------------------------------

    @property
    def held(self) -> bool:
        return self._held

    def hold(self, on: bool) -> None:
        """While held no card is drawn and none runs out; back, the plain
        notes that went stale meanwhile are dropped and the rest shown with
        their whole time."""
        on = bool(on)
        if on == self._held:
            return
        self._held = on
        if not on:
            now = time.monotonic()
            for c in list(self._cards):
                if (c.level == "info" and c.action is None and c.ms > 0
                        and now - c.posted > self.HELD_STALE_S):
                    self._drop(c.id)
        for c in self._cards:
            self._time_card(c)
        self.changed.emit()


class _NullHub:
    """``hub_of`` on a window without notices (a test's): nothing happens."""

    held = False

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def nothing(*_a: Any, **_k: Any) -> Any:
            return [] if name in ("jobs", "cards", "texts") else 0
        return nothing


_NULL = _NullHub()


def hub_of(window: Any) -> Any:
    """The window's notice hub, or a stand-in that does nothing."""
    hub = getattr(window, "notices", None)
    return hub if isinstance(hub, NoticeHub) else _NULL


# -- drawing -------------------------------------------------------------------

class ActivityLine(QWidget):
    """The running jobs as one calm line: a spinner, the newest job's
    words and "· N more". Hover or click lists them all."""

    MAX_W = 300
    TICK_MS = 80

    def __init__(self, hub: NoticeHub, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("NoticeActivity")
        self._hub = hub
        self._angle = 0
        self._text = ""
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        # Its own font, used for its size and its painting alike: the app's
        # style sheet would change the widget's font once it is polished.
        f = QFont(self.font())
        f.setPixelSize(11)
        f.setWeight(QFont.Weight.DemiBold)
        self._font = f
        self._spin = QTimer(self)
        self._spin.setInterval(self.TICK_MS)
        self._spin.timeout.connect(self._turn)

    def refresh(self) -> None:
        jobs = self._hub.jobs()
        if not jobs:
            self._text = ""
            self.setToolTip("")
            self.setAccessibleName("")
            return
        more = len(jobs) - 1
        self._text = jobs[0].text + (" · " + tr("notice_more", n=more) if more else "")
        lines = [j.text for j in jobs]
        self.setToolTip("\n".join(lines))
        self.setAccessibleName(tr("notice_working") + ": " + "; ".join(lines))
        self.updateGeometry()
        self.adjustSize()
        self.update()

    def text(self) -> str:
        return self._text

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(self._font)
        h = max(24, fm.height() + 10)
        # Room for the words as drawn (a fractional advance rounds up).
        w = min(self.MAX_W, fm.horizontalAdvance(self._text) + h + 24)
        return QSize(w, h)

    def _turn(self) -> None:
        self._angle = (self._angle + 30) % 360
        self.update()

    def showEvent(self, e) -> None:      # noqa: N802 - Qt hook
        super().showEvent(e)
        self._spin.start()

    def hideEvent(self, e) -> None:      # noqa: N802 - Qt hook
        self._spin.stop()
        super().hideEvent(e)

    def mousePressEvent(self, e) -> None:    # noqa: N802 - Qt hook
        if e.button() == Qt.MouseButton.LeftButton and self.toolTip():
            QToolTip.showText(self.mapToGlobal(QPoint(0, 0)), self.toolTip(), self)
            e.accept()
            return
        super().mousePressEvent(e)

    def paintEvent(self, _e) -> None:    # noqa: N802 - Qt hook
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(P['hover']))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        d = 10.0
        cy = r.height() / 2
        sx = r.width() - 10 - d if rtl else 10.0
        pen = QPen(QColor(P['accent']), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(sx, cy - d / 2, d, d), -self._angle * 16, 270 * 16)
        p.setPen(QColor(P['text2']))
        tx = 10 + d + 8
        text_r = QRect(int(10 if rtl else tx), 0, int(r.width() - tx - 10), int(r.height()))
        p.setFont(self._font)
        fm = QFontMetrics(self._font)
        shown = fm.elidedText(self._text, Qt.TextElideMode.ElideRight, text_r.width())
        align = Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignRight if rtl
                                                 else Qt.AlignmentFlag.AlignLeft)
        p.drawText(text_r, int(align), shown)
        p.end()


class NoticeCard(QFrame):
    """One card: its words, its action, a dismiss. Flat, on the theme's
    raised surface, a thin leading bar in its level's colour."""

    def __init__(self, hub: NoticeHub, card: Card, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("NoticeCard")
        self._hub = hub
        self.card_id = card.id
        self._level = card.level
        self._whole = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 6, 8)
        lay.setSpacing(6)
        self.label = QLabel()
        self.label.setObjectName("NoticeText")
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay.addWidget(self.label, 1)
        self.action_btn = QPushButton()
        self.action_btn.setObjectName("NoticeAction")
        self.action_btn.setFlat(True)
        self.action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action_btn.clicked.connect(self._act)
        lay.addWidget(self.action_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.close_btn = QToolButton()
        self.close_btn.setObjectName("NoticeDismiss")
        self.close_btn.setText("✕")
        self.close_btn.setAutoRaise(True)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setToolTip(tr("notice_dismiss"))
        self.close_btn.setAccessibleName(tr("notice_dismiss"))
        self.close_btn.clicked.connect(self._dismiss)
        lay.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.show_card(card)

    def show_card(self, card: Card) -> None:
        self._level = card.level
        self.label.setText(card.text)
        self.setAccessibleName(card.text)
        self.setAccessibleDescription(tr("notice_" + card.level))
        has = card.action is not None
        self.action_btn.setVisible(has and bool(card.action_label))
        self.action_btn.setText(card.action_label or "")
        self.action_btn.setAccessibleName(card.action_label or "")
        # A note written to be clicked ("click here"): the card is its button.
        self._whole = has and not card.action_label
        if self._whole:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        else:
            self.unsetCursor()
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.restyle()

    def restyle(self) -> None:
        bar = {"error": P['error'], "warning": P['warn']}.get(
            self._level, P['accent'])
        surface = P['btn']
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        side = "right" if rtl else "left"
        self.setStyleSheet(
            f"#NoticeCard {{ background: {surface}; border: none;"
            f" border-{side}: 3px solid {bar}; border-radius: 8px; }}"
            f"#NoticeText {{ color: {P['text']}; font-size: 12px;"
            " background: transparent; }"
            f"#NoticeAction {{ color: {P['accent']}; font-size: 12px;"
            " font-weight: 600; background: transparent; border: none; padding: 2px 6px; }"
            f"#NoticeAction:hover {{ background: {P['btn_hover']};"
            " border-radius: 6px; }"
            f"#NoticeDismiss {{ color: {P['muted']}; background: transparent;"
            " border: none; font-size: 11px; padding: 2px 4px; }"
            f"#NoticeDismiss:hover {{ color: {P['text']}; }}")

    def _act(self) -> None:
        self._hub.activate(self.card_id)

    def _dismiss(self) -> None:
        self._hub.dismiss(self.card_id)

    def mousePressEvent(self, e) -> None:    # noqa: N802 - Qt hook
        if self._whole and e.button() == Qt.MouseButton.LeftButton:
            e.accept()
            self._act()
            return
        super().mousePressEvent(e)

    def keyPressEvent(self, e) -> None:      # noqa: N802 - Qt hook
        if self._whole and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._act()
            return
        if e.key() == Qt.Key.Key_Escape:
            self._dismiss()
            return
        super().keyPressEvent(e)


def _announce(widget: QWidget) -> None:
    """Tell a screen reader a card came (an alert, as a toast's role is)."""
    try:
        from PyQt6.QtGui import QAccessible, QAccessibleEvent
        QAccessible.updateAccessibility(QAccessibleEvent(widget, QAccessible.Event.Alert))
    except Exception:
        log.debug("notices: no accessibility event", exc_info=True)


class NoticeArea(QWidget):
    """The cards and the activity line, stacked in the window's corner. A
    free child of the window (a splitter would lay it out as a pane), as
    large as what it shows and clicks pass around it."""

    MAX_W = 360
    INSET = 12
    GAP = 6

    def __init__(self, hub: NoticeHub, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("NoticeArea")
        self._hub = hub
        self._anchor: QRect | None = None
        self._rtl = False
        self._cards: dict[int, NoticeCard] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(self.GAP)
        self._lay = lay
        self.activity = ActivityLine(hub, self)
        lay.addWidget(self.activity, 0, Qt.AlignmentFlag.AlignTrailing)
        self.activity.hide()
        hub.changed.connect(self.sync)
        self.hide()

    def card_widgets(self) -> list[NoticeCard]:
        """The cards drawn, the oldest first."""
        return [self._cards[c.id] for c in self._hub.cards() if c.id in self._cards]

    def sync(self) -> None:
        """What the hub holds, drawn."""
        hub = self._hub
        alive = {c.id for c in hub.cards()}
        for cid in list(self._cards):
            if cid not in alive:
                w = self._cards.pop(cid)
                self._lay.removeWidget(w)
                w.hide()
                w.deleteLater()
        fresh: list[NoticeCard] = []
        for i, c in enumerate(hub.cards()):
            w = self._cards.get(c.id)
            if w is None:
                w = NoticeCard(hub, c, self)
                self._cards[c.id] = w
                fresh.append(w)
            else:
                w.show_card(c)
            self._lay.removeWidget(w)
            self._lay.insertWidget(i, w)
            w.setVisible(True)
        # Shown first: a hidden widget's new size is not passed on to the
        # layout, and the line kept the width of its words before.
        self.activity.setVisible(bool(hub.jobs()))
        self.activity.refresh()
        self.place()
        if self.isVisible():
            for w in fresh:
                _announce(w)

    def set_anchor(self, rect: QRect | None, rtl: bool = False) -> None:
        """Where the corner is: the bottom of *rect* (window coordinates),
        on its trailing side; None draws nothing (the video fills the
        screen, TV mode)."""
        self._anchor = QRect(rect) if rect is not None else None
        self._rtl = bool(rtl)
        self.place()

    def place(self) -> None:
        r = self._anchor
        hub = self._hub
        show = (r is not None and not hub.held
                and (bool(hub.cards()) or bool(hub.jobs())))
        if not show or r is None:
            self.hide()
            return
        w = max(160, min(self.MAX_W, r.width() - 2 * self.INSET))
        self._lay.invalidate()
        for card in self._cards.values():
            card.setFixedWidth(w)
        self.setFixedWidth(w)
        self._lay.activate()
        h = self._lay.sizeHint().height()
        self.setFixedHeight(min(h, max(40, r.height() - 2 * self.INSET)))
        x = r.left() + self.INSET if self._rtl else r.right() - self.INSET - w + 1
        y = r.bottom() - self.INSET - self.height() + 1
        self.move(x, y)
        self.show()
        self.raise_()
        self._lay.activate()
        self._mask()

    def event(self, e) -> bool:     # noqa: D102 - Qt hook
        done = super().event(e)
        if e.type() in (QEvent.Type.LayoutRequest, QEvent.Type.Resize):
            self._mask()        # its children moved: clicks pass around them again
        return done

    def _mask(self) -> None:
        region = QRegion()
        for child in [*self._cards.values(), self.activity]:
            if child.isVisible():
                region = region.united(QRegion(child.geometry()))
        if region.isEmpty():
            self.clearMask()
        else:
            self.setMask(region)

    def restyle(self) -> None:
        for w in self._cards.values():
            w.restyle()
        self.activity.update()


class Follow(QObject):
    """Calls *callback* once, soon after any watched widget moved, changed
    size, showed or hid - the corner follows the window's panes."""

    _EVENTS = (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show, QEvent.Type.Hide,
               QEvent.Type.LayoutDirectionChange)

    def __init__(self, callback: Callable[[], Any], parent: QObject) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(callback)

    def watch(self, obj: QObject) -> None:
        obj.installEventFilter(self)

    def poke(self) -> None:
        self._timer.start()

    def eventFilter(self, obj, event) -> bool:   # noqa: N802 - Qt hook
        if event.type() in self._EVENTS:
            self._timer.start()
        return False
