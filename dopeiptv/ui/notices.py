"""One place for what the app is doing and what it tells you - the state.

Everything is in one corner, the one away from the video (bottom left;
mirrored right to left):

- **Activity** - background work that takes a while: a list or the guide
  loading, a folder read or searched, the network scanned, a sync, an
  update check, a download, a recording saved as MP4. Each job is started
  with a token and ended by its owner (``finish`` / ``fail`` / ``cancel``),
  replaced by a newer job with the same key, or let go by its own safety
  timeout - a job can never stay. It shows only once it has run for
  ``SHOW_AFTER_MS`` (short work never flickers) and, once shown, stays at
  least ``MIN_SHOWN_MS``. One line at the foot of the sidebar, just above
  the source avatar (a ring with a count on the icon rail; on Home, where
  there is no sidebar, at the same corner): an icon for the kind of work,
  the job the person started first - else the oldest - "+N" for the rest,
  a calm accent bar when its progress is known, a gentle spinner when not.
  Jobs of one group merge into one line with a count ("Loading guide · 3
  sources", their progress summed). A click or Enter opens the panel of
  every job - its icon, words, progress and a Cancel where the producer
  can stop it - which follows them live and closes when the last one ends.
- **Cards** - what needs the person: an error, an offer with its action, a
  short note. They stack upward from the bottom-left of the list pane,
  next to the activity line, never over the video or the sidebar: at most
  three, the newest on top, older ones folded into "+N more"; a card with
  an action sorts above the passing ones and stays until answered or
  dismissed, an error stays until dismissed, a passing note goes after
  ``CARD_MS`` (held while the pointer rests on the cards); the same words
  again are one card with a count.

While the video fills the screen, TV mode shows or the picture is popped
out, nothing is drawn: cards wait until you are back, work goes on unseen.
What is about the picture itself (its loading overlay, reconnecting, the
stream banner, a typed channel number) stays over the video, and real
questions stay dialogs.

``NoticeHub`` is the state and its timers; ``Activity`` is a producer's
handle on one job, safe from any thread and usable as a context manager;
ui/notice_widgets.py draws it in P's colours, flat, rounded like the app's
surfaces.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from functools import partial
from typing import Any, Callable

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication

from ..core.log import log

LEVELS = ("info", "warning", "error")
#: The kinds of work an icon is drawn for; anything else is "work".
KINDS = ("work", "list", "guide", "folder", "search", "network", "sync", "update",
         "download", "record")


@dataclass
class Job:
    token: int
    text: str
    key: str | None
    kind: str
    started: float
    mine: bool = False
    done: float | None = None
    total: float | None = None
    cancel: Callable[[], Any] | None = None
    group: str | None = None
    group_text: Callable[[int], str] | None = None
    shown_at: float | None = None

    def progress(self) -> float | None:
        if self.done is None or not self.total:
            return None
        return max(0.0, min(1.0, float(self.done) / float(self.total)))


@dataclass
class Entry:
    """One line of the activity: a job, or a group of them merged."""
    tokens: list[int]
    text: str
    kind: str
    progress: float | None
    mine: bool
    started: float
    cancellable: bool


@dataclass
class Card:
    id: int
    title: str
    body: str
    level: str
    action: Callable[[], Any] | None
    action_label: str | None
    ms: int                         # 0: it stays until answered or dismissed
    key: str | None
    posted: float
    count: int = 1
    left: float = 0.0               # what was left of its time when paused

    @property
    def text(self) -> str:
        return self.title + ("\n" + self.body if self.body else "")

    @property
    def asks(self) -> bool:
        """It waits for an answer: it has an action."""
        return self.action is not None


class NoticeHub(QObject):
    """The window's notices: jobs under tokens, cards in a stack."""

    changed = pyqtSignal()

    #: A job its owner never ended is let go after this (each word from it -
    #: ``update`` - starts the time again).
    JOB_TIMEOUT_MS = 10 * 60_000
    #: A job shows once it ran this long, and stays at least the next.
    SHOW_AFTER_MS = 400
    MIN_SHOWN_MS = 600
    #: A passing card's time when its producer names none, and the least.
    CARD_MS = 6000
    CARD_MIN_MS = 3000
    MAX_SHOWN = 3
    MAX_CARDS = 12
    #: A passing note held this long (seconds) while the video filled the
    #: screen is no news any more when it comes back.
    HELD_STALE_S = 120.0

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._seq = 0
        self._jobs: dict[int, Job] = {}
        self._job_timers: dict[int, QTimer] = {}
        self._on_expire: dict[int, Callable[[], Any] | None] = {}
        self._lingering: dict[int, tuple[Job, float]] = {}
        self._vis = QTimer(self)
        self._vis.setSingleShot(True)
        self._vis.timeout.connect(self._tick)
        self._cards: list[Card] = []
        self._card_timers: dict[int, QTimer] = {}
        self._held = False
        self._paused = False

    # -- jobs -------------------------------------------------------------------

    def start(self, text: str, *, key: str | None = None, kind: str = "work",
              mine: bool = False, timeout_ms: int | None = None,
              on_expire: Callable[[], Any] | None = None,
              cancel: Callable[[], Any] | None = None, group: str | None = None,
              group_text: Callable[[int], str] | None = None,
              done: float | None = None, total: float | None = None) -> int:
        """A job begins; its token is what ends it. *kind* picks its icon,
        *mine* says the person started it (it leads the line), *cancel* lets
        the panel stop it, *group* merges it with others into one line
        (*group_text(n)* its words). A job already running under *key* is
        replaced: its token is dead from now on, and a replacement already
        on screen stays on screen."""
        shown_at = None
        if key is not None:
            old = self.token_for(key)
            if old:
                shown_at = self._jobs[old].shown_at
                self._end(old, emit=False, linger=False)
        self._seq += 1
        token = self._seq
        self._jobs[token] = Job(token, str(text or ""), key,
                                kind if kind in KINDS else "work", time.monotonic(),
                                mine, done, total, cancel, group, group_text, shown_at)
        self._on_expire[token] = on_expire
        t = QTimer(self)
        t.setSingleShot(True)
        t.setInterval(max(1, int(timeout_ms or self.JOB_TIMEOUT_MS)))
        t.timeout.connect(partial(self._expired, token))
        t.start()
        self._job_timers[token] = t
        self._arm()
        self.changed.emit()
        return token

    def update(self, token: int, text: str | None = None, *, done: float | None = None,
               total: float | None = None) -> bool:
        """A word from a running job: its safety time starts again; *text*,
        *done* and *total* (its progress) replace what it said. False for a
        job that is over."""
        job = self._jobs.get(token)
        if job is None:
            return False
        self._job_timers[token].start()
        new = (job.text if text is None else str(text),
               job.done if done is None else done,
               job.total if total is None else total)
        if new != (job.text, job.done, job.total):
            job.text, job.done, job.total = new
            if job.shown_at is not None:
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

    def request_cancel(self, tokens: list[int]) -> bool:
        """The person asked to stop these jobs (the panel's Cancel): each
        producer is told, and the jobs end."""
        stopped = False
        for token in tokens:
            job = self._jobs.get(token)
            if job is None or job.cancel is None:
                continue
            try:
                job.cancel()
            except Exception:
                log.debug("notices: a job's cancel failed", exc_info=True)
            stopped = self._end(token, linger=False) or stopped
        return stopped

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

    def job(self, token: int) -> Job | None:
        return self._jobs.get(token)

    def jobs(self) -> list[Job]:
        """The running jobs, in the order they began."""
        return sorted(self._jobs.values(), key=lambda j: j.token)

    def entries(self) -> list[Entry]:
        """What the activity shows: the jobs that ran long enough (and the
        ended ones still in their least time), a group merged into one,
        the person's own first, then the oldest."""
        shown = [j for j in self._jobs.values() if j.shown_at is not None]
        shown += [j for j, _until in self._lingering.values()]
        shown.sort(key=lambda j: j.token)
        out: list[Entry] = []
        groups: dict[str, list[Job]] = {}
        for j in shown:
            if j.group:
                groups.setdefault(j.group, []).append(j)
        seen: set[str] = set()
        for j in shown:
            members = groups.get(j.group or "", [j]) if j.group else [j]
            if j.group:
                if j.group in seen:
                    continue
                seen.add(j.group)
            out.append(self._entry(members))
        out.sort(key=lambda e: (not e.mine, e.started))
        return out

    def line_text(self) -> str:
        """The line's words: the first entry, "+N" for the rest."""
        es = self.entries()
        if not es:
            return ""
        return es[0].text + (f"  +{len(es) - 1}" if len(es) > 1 else "")

    def _entry(self, jobs: list[Job]) -> Entry:
        live = [j for j in jobs if j.token in self._jobs]
        first = jobs[0]
        if len(jobs) == 1:
            text = first.text
        elif first.group_text is not None:
            text = first.group_text(len(jobs))
        else:
            text = f"{first.text} · {len(jobs)}"
        totals = [(j.done, j.total) for j in jobs]
        progress = None
        if all(d is not None and t for d, t in totals):
            progress = max(0.0, min(1.0, sum(float(d or 0) for d, _t in totals)
                                    / sum(float(t or 0) for _d, t in totals)))
        return Entry([j.token for j in jobs], text, first.kind, progress,
                     any(j.mine for j in jobs), min(j.started for j in jobs),
                     bool(live) and all(j.cancel is not None for j in live))

    def _end(self, token: int, emit: bool = True, linger: bool = True) -> bool:
        job = self._jobs.pop(token, None)
        if job is None:
            return False
        self._on_expire.pop(token, None)
        t = self._job_timers.pop(token, None)
        if t is not None:
            t.stop()
            t.deleteLater()
        if linger and job.shown_at is not None:
            until = job.shown_at + self.MIN_SHOWN_MS / 1000.0
            if until > time.monotonic():
                self._lingering[token] = (job, until)
        self._arm()
        if emit:
            self.changed.emit()
        return True

    def _expired(self, token: int) -> None:
        job = self._jobs.get(token)
        if job is None:
            return
        cb = self._on_expire.get(token)
        log.info("notices: a job its owner did not end was let go (%s)", job.key or job.kind)
        self._end(token, linger=False)
        if cb is not None:
            try:
                cb()
            except Exception:
                log.debug("notices: the job's expiry hook failed", exc_info=True)

    def _arm(self) -> None:
        """The one timer, set to the next moment a job shows or a shown one
        may go - and nothing at all while no job runs."""
        now = time.monotonic()
        nxt = [j.started + self.SHOW_AFTER_MS / 1000.0
               for j in self._jobs.values() if j.shown_at is None]
        nxt += [until for _j, until in self._lingering.values()]
        if not nxt:
            self._vis.stop()
            return
        self._vis.start(max(0, int((min(nxt) - now) * 1000) + 1))

    def _tick(self) -> None:
        now = time.monotonic()
        moved = False
        for j in self._jobs.values():
            if j.shown_at is None and now - j.started >= self.SHOW_AFTER_MS / 1000.0:
                j.shown_at = now
                moved = True
        for token, (_j, until) in list(self._lingering.items()):
            if until <= now:
                del self._lingering[token]
                moved = True
        self._arm()
        if moved:
            self.changed.emit()

    # -- cards ------------------------------------------------------------------

    def post(self, text: str, *, title: str | None = None, level: str = "info",
             action: Callable[[], Any] | None = None, action_label: str | None = None,
             ms: int | None = None, key: str | None = None, answer: bool = False) -> int:
        """A card. *title* in bold over *text* - else the text's first line
        is its title and the rest its line. *action* is what its button (or,
        without *action_label*, a click on the card) does; a card with one,
        or an *answer* wanted, stays until answered or dismissed, an error
        until dismissed, a passing note *ms* (``CARD_MS`` when not given).
        The same *key* - or the same words - again refresh the card there;
        the same words count up."""
        text = str(text or "").strip()
        if title is not None:
            head, body = str(title).strip(), text
        else:
            head, _nl, body = text.partition("\n")
            head, body = head.strip(), body.strip()
        if not head and not body:
            return 0
        if not head:
            head, body = body, ""
        level = level if level in LEVELS else "info"
        stays = answer or action is not None or level == "error"
        stay = 0 if stays else max(self.CARD_MIN_MS, int(ms or self.CARD_MS))
        card = next((c for c in self._cards if (key is not None and c.key == key)
                     or (key is None and c.key is None and c.title == head
                         and c.body == body and c.level == level)), None)
        now = time.monotonic()
        if card is not None:
            self._cards.remove(card)
            same = (card.title, card.body) == (head, body)
            card.count = card.count + 1 if same and key is None else 1
            card.title, card.body, card.level, card.action = head, body, level, action
            card.action_label, card.ms, card.posted = action_label, stay, now
        else:
            self._seq += 1
            card = Card(self._seq, head, body, level, action, action_label, stay, key, now)
        self._cards.append(card)
        while len(self._cards) > self.MAX_CARDS:
            old = next((c for c in self._cards if not c.asks and c.level != "error"),
                       self._cards[0])
            self._drop(old.id)
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

    def shown_cards(self) -> list[Card]:
        """The cards as the stack draws them, from the top: the ones that
        ask for an answer, then the rest - each the newest first."""
        asks = [c for c in self._cards if c.asks]
        rest = [c for c in self._cards if not c.asks]
        return list(reversed(asks)) + list(reversed(rest))

    def texts(self) -> list[str]:
        return [c.text for c in self._cards]

    def _drop(self, card_id: int) -> None:
        self._cards = [c for c in self._cards if c.id != card_id]
        t = self._card_timers.pop(card_id, None)
        if t is not None:
            t.stop()
            t.deleteLater()

    def _time_card(self, card: Card, left: float | None = None) -> None:
        t = self._card_timers.get(card.id)
        if card.ms <= 0 or self._held or self._paused:
            if t is not None:
                t.stop()
            card.left = float(card.ms) if left is None else left
            return
        if t is None:
            t = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(partial(self.dismiss, card.id))
            self._card_timers[card.id] = t
        t.start(int(max(1500.0, card.ms if left is None else left)))

    # -- held, paused --------------------------------------------------------

    @property
    def held(self) -> bool:
        return self._held

    def hold(self, on: bool) -> None:
        """While held no card is drawn and none runs out; back, the passing
        notes that went stale meanwhile are dropped and the rest shown with
        their whole time."""
        on = bool(on)
        if on == self._held:
            return
        self._held = on
        if not on:
            now = time.monotonic()
            for c in list(self._cards):
                if not c.asks and c.level == "info" and now - c.posted > self.HELD_STALE_S:
                    self._drop(c.id)
        for c in self._cards:
            self._time_card(c)
        self.changed.emit()

    def pause(self, on: bool) -> None:
        """The pointer rests on the cards: none runs out until it leaves,
        then each has what was left of its time."""
        on = bool(on)
        if on == self._paused:
            return
        self._paused = on
        for c in self._cards:
            t = self._card_timers.get(c.id)
            if on:
                left = float(t.remainingTime()) if t is not None and t.isActive() else float(c.ms)
                if t is not None:
                    t.stop()
                c.left = left
            else:
                self._time_card(c, c.left or None)


class _NullHub:
    """``hub_of`` on a window without notices (a test's): nothing happens."""

    held = False

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def nothing(*_a: Any, **_k: Any) -> Any:
            return [] if name in ("jobs", "cards", "texts", "entries", "shown_cards") else 0
        return nothing


_NULL = _NullHub()


def hub_of(window: Any) -> Any:
    """The window's notice hub, or a stand-in that does nothing."""
    hub = getattr(window, "notices", None)
    return hub if isinstance(hub, NoticeHub) else _NULL


def app_hub() -> Any:
    """The hub of the app's main window, for a part of the app with no
    window of its own at hand (a form in a dialog)."""
    app = QApplication.instance()
    if app is not None:
        for w in QApplication.topLevelWidgets():
            hub = getattr(w, "notices", None)
            if isinstance(hub, NoticeHub):
                return hub
    return _NULL


class Activity:
    """A producer's handle on one job, safe from any thread: what it says is
    handed to the window's thread in order. Also a context manager - the
    job ends with the block, and an exception makes it a failure::

        job = activity(win, tr("notice_reading_folder", name="Music"), kind="folder")
        job.say(done=40, total=120)       # on a worker: fine
        job.finish()
    """

    def __init__(self, hub: Any, text: str, **kw: Any) -> None:
        self._hub = hub
        self.token = 0
        self._call(self._start, text, kw)

    def _call(self, fn: Callable[..., Any], *args: Any) -> None:
        if isinstance(self._hub, _NullHub):
            return
        from ..core.workers import on_gui_thread
        on_gui_thread(fn, *args)

    def _start(self, text: str, kw: dict) -> None:
        self.token = self._hub.start(text, **kw)

    def say(self, text: str | None = None, *, done: float | None = None,
            total: float | None = None) -> None:
        self._call(lambda: self._hub.update(self.token, text, done=done, total=total))

    def finish(self) -> None:
        self._call(lambda: self._hub.finish(self.token))

    def cancel(self) -> None:
        self._call(lambda: self._hub.cancel(self.token))

    def fail(self, text: str) -> None:
        self._call(lambda: self._hub.fail(self.token, text))

    def __enter__(self) -> "Activity":
        return self

    def __exit__(self, exc_type: Any, exc: Any, _tb: Any) -> bool:
        if exc is None:
            self.finish()
        else:
            self.fail(str(exc) or exc_type.__name__)
        return False


def activity(window: Any, text: str, **kw: Any) -> Activity:
    """A job on *window*'s hub (``NoticeHub.start``'s options), as a handle."""
    return Activity(hub_of(window) if window is not None else app_hub(), text, **kw)
