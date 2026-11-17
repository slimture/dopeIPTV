"""What the app is doing and what it tells you - the state.

All of it lives at the foot of the sidebar (bottom left, mirrored right to
left), in the sidebar's own layout: nothing lies over the list, its
categories or the video.

- **Activity** - background work that takes a while: a list or the guide
  loading, a folder read or searched, the network scanned, a sync, an
  update check, a download, a recording saved as MP4. Each job is started
  with a token and ended by its owner (``finish`` / ``fail`` / ``cancel``),
  replaced by a newer job with the same key, or let go by its own safety
  timeout - a job can never stay. It shows only once it has run for
  ``SHOW_AFTER_MS`` (short work never flickers) and, once shown, stays at
  least ``MIN_SHOWN_MS``. One line just above the source avatar: an icon
  for the kind of work, the job the person started first - else the oldest
  - "+N" for the rest, an accent bar when its progress is known, a gentle
  spinner when not. Jobs of one group merge into one line ("Loading guide ·
  3 sources", their progress summed).
- **Notes** - passing words ("EPG cache cleared", "Copied"): the line's own
  words for ``NOTE_MS``, then gone - no box.
- **Messages** - an error, or a word that needs no decision: only a count
  on the line ("1" on a dot); the panel (a click or Enter on the line)
  lists them under the work, the newest first, each with its action if it
  has one and a dismiss, the read ones faded, Clear all. Stream errors are
  said on the video and never here.
- **Decisions** - what really needs the person now (the server's port
  offer): one small card of the sidebar's width right above the line - an
  icon, its words in two lines at most, one action, a dismiss - and any
  more waiting behind it as a count, until answered or dismissed.

While the video fills the screen, TV mode shows or the picture is popped
out, nothing is drawn and no note is kept; messages and decisions wait.
What is about the picture itself (its loading overlay, reconnecting, the
stream banner, a typed channel number with the player up) stays over the
video, and real questions stay dialogs.

``NoticeHub`` is the state and its timers; ``Activity`` is a producer's
handle on one job, safe from any thread and usable as a context manager;
ui/notice_widgets.py draws it.
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
class Message:
    """A message in the panel - or, when it asks for a decision, the card
    over the activity line."""
    id: int
    title: str
    body: str
    level: str
    action: Callable[[], Any] | None
    action_label: str | None
    key: str | None
    posted: float
    decide: bool = False
    count: int = 1
    read: bool = False

    @property
    def text(self) -> str:
        return self.title + ("\n" + self.body if self.body else "")


class NoticeHub(QObject):
    """The window's notices: jobs under tokens, a passing note, messages
    and decisions."""

    changed = pyqtSignal()

    #: A job its owner never ended is let go after this (each word from it -
    #: ``update`` - starts the time again).
    JOB_TIMEOUT_MS = 10 * 60_000
    #: A job shows once it ran this long, and stays at least the next.
    SHOW_AFTER_MS = 400
    MIN_SHOWN_MS = 600
    #: How long a passing note is the line's words when its producer names
    #: no time - and the least and the most it may name.
    NOTE_MS = 4000
    NOTE_MIN_MS = 2500
    NOTE_MAX_MS = 6000
    #: Messages kept (the oldest read ones go first), and how many words of
    #: this session are kept for ``said``.
    MAX_MESSAGES = 30
    SAID = 50

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
        self._messages: list[Message] = []
        self._note: tuple[str, str | None] | None = None
        self._note_timer = QTimer(self)
        self._note_timer.setSingleShot(True)
        self._note_timer.timeout.connect(self._note_over)
        self._said: list[str] = []
        self._held = False

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

    def fail(self, token: int, text: str, **said: Any) -> bool:
        """The job failed: it ends and *text* is said as an error message -
        only for a job still running (a replaced one's failure is no news)."""
        if not self._end(token):
            return False
        said.setdefault("level", "error")
        self.post(text, **said)
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

    # -- notes, messages, decisions --------------------------------------------

    def post(self, text: str, *, title: str | None = None, level: str = "info",
             action: Callable[[], Any] | None = None, action_label: str | None = None,
             ms: int | None = None, key: str | None = None, decide: bool = False,
             answer: bool = False) -> int:
        """Something said. A passing note (info or a warning, nothing to do)
        is the line's words for *ms* - none kept. An error, or anything with
        an *action*, is a message for the panel, counted on the line until
        read. One that asks for a decision now (*decide*, or *answer*) is the
        card over the line. *title* in bold over *text* - else the text's
        first line is its title. The same *key* or the same words again
        refresh the one there; the same words count up. Returns its id (0
        for a note)."""
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
        decide = decide or answer
        if action is None and not decide and level != "error":
            self.note(head + (" " + body if body else ""), ms=ms, key=key)
            return 0
        self._say(head + (" - " + body if body else ""))
        msg = next((m for m in self._messages if (key is not None and m.key == key)
                    or (key is None and m.key is None and m.title == head
                        and m.body == body and m.level == level)), None)
        now = time.monotonic()
        if msg is not None:
            self._messages.remove(msg)
            same = (msg.title, msg.body) == (head, body)
            msg.count = msg.count + 1 if same and key is None else 1
            msg.title, msg.body, msg.level, msg.action = head, body, level, action
            msg.action_label, msg.decide, msg.posted, msg.read = action_label, decide, now, False
        else:
            self._seq += 1
            msg = Message(self._seq, head, body, level, action, action_label, key, now, decide)
        self._messages.append(msg)
        while len(self._messages) > self.MAX_MESSAGES:
            old = next((m for m in self._messages if m.read and not m.decide),
                       next((m for m in self._messages if not m.decide), self._messages[0]))
            self._messages.remove(old)
        self.changed.emit()
        return msg.id

    def note(self, text: str, *, ms: int | None = None, key: str | None = None) -> None:
        """Passing words: the line says them for *ms*, then they are gone. A
        note while the video fills the screen is no news later: dropped."""
        text = str(text or "").strip()
        if not text:
            return
        self._say(text)
        if self._held:
            return
        self._note = (text, key)
        stay = int(ms) if ms else self.NOTE_MS
        self._note_timer.start(max(self.NOTE_MIN_MS, min(self.NOTE_MAX_MS, stay)))
        self.changed.emit()

    def note_text(self) -> str:
        return self._note[0] if self._note is not None else ""

    def _note_over(self) -> None:
        if self._note is not None:
            self._note = None
            self.changed.emit()

    def dismiss(self, msg_id: int) -> bool:
        msg = next((m for m in self._messages if m.id == msg_id), None)
        if msg is None:
            return False
        self._messages.remove(msg)
        self.changed.emit()
        return True

    def dismiss_key(self, key: str) -> bool:
        """The message - or the note - said under *key* goes."""
        if self._note is not None and self._note[1] == key:
            self._note_timer.stop()
            self._note = None
            self.changed.emit()
            return True
        msg = next((m for m in self._messages if m.key == key), None)
        return self.dismiss(msg.id) if msg is not None else False

    def activate(self, msg_id: int) -> bool:
        """A message's action: the message goes, then the action runs -
        after this event, since what it opens may run an event loop."""
        msg = next((m for m in self._messages if m.id == msg_id), None)
        if msg is None:
            return False
        act = msg.action
        self.dismiss(msg_id)
        if act is not None:
            QTimer.singleShot(0, act)
        return True

    def messages(self) -> list[Message]:
        """The panel's messages, the newest first (decisions too)."""
        return list(reversed(self._messages))

    def decisions(self) -> list[Message]:
        """What waits for a decision, the oldest first: the card shows the
        first, the rest wait behind it."""
        return [m for m in self._messages if m.decide]

    def unread(self) -> int:
        """The count on the line: messages not read yet (a decision has its
        own card)."""
        return sum(1 for m in self._messages if not m.read and not m.decide)

    def unread_level(self) -> str:
        """The worst level among them - the count's colour."""
        levels = {m.level for m in self._messages if not m.read and not m.decide}
        return "error" if "error" in levels else "warning" if "warning" in levels else "info"

    def mark_read(self) -> None:
        if any(not m.read for m in self._messages if not m.decide):
            for m in self._messages:
                if not m.decide:
                    m.read = True
            self.changed.emit()

    def clear(self) -> None:
        """Clear all: every message goes - a decision still waits for its."""
        kept = [m for m in self._messages if m.decide]
        if len(kept) != len(self._messages):
            self._messages = kept
            self.changed.emit()

    def texts(self) -> list[str]:
        """The messages' words, the oldest first."""
        return [m.text for m in self._messages]

    def said(self) -> list[str]:
        """Everything said this session (notes and messages), the oldest
        first, at most ``SAID``."""
        return list(self._said)

    def _say(self, words: str) -> None:
        self._said.append(words)
        del self._said[:-self.SAID]

    # -- held ----------------------------------------------------------------

    @property
    def held(self) -> bool:
        return self._held

    def hold(self, on: bool) -> None:
        """While held nothing is drawn: a note in the middle of its time
        goes, messages and decisions wait."""
        on = bool(on)
        if on == self._held:
            return
        self._held = on
        if on and self._note is not None:
            self._note_timer.stop()
            self._note = None
        self.changed.emit()


class _NullHub:
    """``hub_of`` on a window without notices (a test's): nothing happens."""

    held = False

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def nothing(*_a: Any, **_k: Any) -> Any:
            if name in ("note_text", "line_text"):
                return ""
            return [] if name in ("jobs", "messages", "decisions", "texts", "entries",
                                  "said") else 0
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
