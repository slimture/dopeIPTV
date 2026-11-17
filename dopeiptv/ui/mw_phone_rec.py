"""Recording from the phone remote, and the app's questions asked there
(core/phone_page.py shows them, ui/mw_phone.py hands them in).

Recording what plays is the player's record button's own path
(_record_now, until stopped): from the rewind buffer - or the player's
stream - of the channel that plays, no connection of its own, so no
connection question either; the page asks "Record …?" first, unless that
was turned off (``phone_rec_ask``, the household's, one key for every
phone). Stopping always asks on the page. The channel's next programme is
recorded through the scheduled-recording path (_record_programme: the
recording padding, its source, the same job list), or reminded
(_add_reminder). A held recording - one that would open one connection
too many when it came due - goes ahead only with the person's yes
(_rec_go_ahead).

The one-connection rule holds as on the computer: while a phone request
runs, every question the app would ask (_choice_dialog: a recording next
to what plays, one connection more than the account allows or a limit
not known, a box's tuners, a SAT>IP tuner) is the phone's - the same
words and choices - and the request starts nothing (as Cancel would).
The page asks the person and sends the same request again with the
answer; only an explicit choice goes ahead, and the question is never
left waiting on this screen with nobody at it. A "Don't show this again"
the app offers is the phone's to tick too, the same key. A question that
comes later in such a request (a play waiting for its address,
_links_later) is said in the state for a minute, for the page to ask.
"""

from __future__ import annotations

import hashlib
import time
from contextlib import contextmanager
from functools import partial
from typing import Any

from ..core.log import log
from ..i18n import tr

#: Asking before a recording starts, for every phone (the household's).
REC_ASK_KEY = "phone_rec_ask"
#: Answers carried by one request at most, and how long a later question
#: stands in the state.
ANSWERS_MAX = 4
LATE_S = 60.0


def question_id(title: str, message: str, options: list) -> str:
    """A question as the app words it: the same again, the same id."""
    raw = "\x00".join([title, message] + [str(o[0]) for o in options])
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]


def answers_of(raw: Any) -> list[dict[str, Any]]:
    """The answers a request carries, checked: [{"q", "i", "dont"}]."""
    out: list[dict[str, Any]] = []
    for a in (raw if isinstance(raw, list) else [])[:ANSWERS_MAX]:
        if not isinstance(a, dict):
            break
        try:
            i = int(a.get("i"))
        except (TypeError, ValueError):
            break
        q = str(a.get("q") or "")[:16]
        out.append({"q": q, "i": i, "dont": a.get("dont") is True})
    return out


class PhoneQuestions:
    """The app's questions while a phone request runs: one answered by
    the request is answered (when it is still that question), the first
    one not answered is kept for the phone and None returned - nothing
    starts, as Cancel. What the app would warn about is kept as a note."""

    def __init__(self, settings: Any, answers: list[dict[str, Any]]) -> None:
        self.settings = settings
        self.answers = list(answers)
        self.given: list[dict[str, Any]] = []
        self.asked: dict[str, Any] | None = None
        self.notes: list[str] = []

    def __call__(self, title: str, message: str, options: list,
                 dont_ask_setting: str | None = None) -> int | None:
        qid = question_id(title, message, options)
        if self.answers:
            a = self.answers[0]
            if a["q"] == qid and 0 <= a["i"] < len(options):
                self.answers.pop(0)
                self.given.append(a)
                if a["dont"] and dont_ask_setting and self.settings is not None:
                    self.settings.setValue(dont_ask_setting, "true")
                log.info("phone remote: a question answered on the phone (%d)", a["i"])
                return int(a["i"])
            self.answers = []           # another question now: asked afresh
        if self.asked is None:
            self.asked = {"q": qid, "title": title, "text": message,
                          "choices": [{"label": str(o[0]), "kind": str(o[1])} for o in options],
                          "dont": bool(dont_ask_setting), "answers": list(self.given)}
        return None

    def note(self, text: str) -> None:
        if text and text not in self.notes:
            self.notes.append(text)


class _PhoneRecMixin:
    _phone_late: tuple | None = None

    # -- the questions ----------------------------------------------------------------

    @contextmanager
    def _phone_asking(self, raw_answers: Any):
        q = PhoneQuestions(getattr(self, "settings", None), answers_of(raw_answers))
        before = self.__dict__.get("_phone_ask_hook")
        self._phone_ask_hook = q
        if raw_answers:
            self._phone_late = None     # the one said in the state is answered
        try:
            yield q
        finally:
            if before is None:
                self.__dict__.pop("_phone_ask_hook", None)
            else:
                self._phone_ask_hook = before

    def _phone_keep_asking(self, fn):
        """*fn* run later (a play waiting for its address) still the phone's:
        a question then is said in the state, never left on this screen."""
        q = self.__dict__.get("_phone_ask_hook")
        if q is None:
            return fn
        return partial(self._phone_run_late, list(q.answers), fn)

    def _phone_run_late(self, answers: list, fn, *args) -> Any:
        with self._phone_asking(answers) as q:
            result = fn(*args)
        if q.asked is not None:
            self._phone_late = (time.monotonic(), q.asked)
        for text in q.notes:
            self._phone_note(text)
        return result

    def _phone_late_ask(self) -> dict | None:
        late = self._phone_late
        if late is None:
            return None
        if time.monotonic() - late[0] > LATE_S:
            self._phone_late = None
            return None
        return late[1]

    def _phone_answered(self, q: PhoneQuestions, result: Any) -> Any:
        """A request's answer with what the app asked or warned about in it."""
        out = dict(result) if isinstance(result, dict) else {"ok": bool(result)}
        if q.asked is not None:
            out["ok"] = False
            out["ask"] = q.asked
        if q.notes:
            out["note"] = "\n".join(q.notes)
        return out

    # -- recording --------------------------------------------------------------------

    def _phone_rec_asks(self) -> bool:
        settings = getattr(self, "settings", None)
        return settings is None or str(settings.value(REC_ASK_KEY, "true")) != "false"

    def _phone_rec_state(self) -> dict[str, Any] | None:
        """The recording running now as Now playing shows it - the one of
        what plays first - or None."""
        jobs = [j for j in getattr(getattr(self, "rec", None), "jobs", []) or []
                if j.get("status") == "recording"]
        if not jobs:
            return None
        j = next((k for k in jobs if k.get("inplayer")), jobs[0])
        return {"id": str(j.get("id") or ""), "title": str(j.get("title") or ""),
                "start": int(float(j.get("start") or 0)), "here": bool(j.get("inplayer"))}

    def _phone_rec_item(self) -> dict | None:
        """The live channel the docked player plays now - what the phone's
        Record and TV mode's (mw_tv's Options) record from, the player's
        record button's own test; shown, or behind TV mode's page with its
        stream going on - or None: nothing that would make Record open a
        connection of its own."""
        lp = getattr(self, "_last_playback", None) or {}
        it = getattr(self, "_playing_item", None)
        p = getattr(self, "player", None)
        mode, key_of = getattr(self, "playback_mode", None), getattr(self, "_item_key", None)
        held = getattr(self, "_tv_holds_picture", None)
        if (lp.get("kind") != "live" or not it or it.get("stream_id") is None
                or getattr(self, "_playing_catchup", False) or p is None
                or not getattr(p, "current_url", None)
                or not (p.isVisible() or (callable(held) and held()))
                or not callable(mode) or mode() != "embedded" or not callable(key_of)
                or getattr(self, "_playing_group", None) != "live"
                or getattr(self, "_playing_key", None) != key_of(it)):
            return None
        dead = getattr(self, "_conn_player_dead", None)
        return None if callable(dead) and dead(p) else it

    def _phone_rec(self, params: dict) -> dict[str, Any]:
        """Record what plays, as the player's record button does (until
        stopped): from its rewind buffer or stream, no connection of its
        own. *dont*: the page is not to ask before a recording again."""
        if params.get("dont") is True:
            self.settings.setValue(REC_ASK_KEY, "false")
        it = self._phone_rec_item()
        if it is None:
            return {"ok": False}
        before = {j.get("id") for j in self.rec.jobs}
        self._record_now(it, None)
        new = [j for j in self.rec.jobs if j.get("id") not in before]
        if any(j.get("status") == "recording" for j in new):
            return {"ok": True}
        held = next((j for j in new if j.get("_held")), None)
        if held is not None:
            # One connection too many as it would open its own: it waits,
            # as on the computer - Recordings offers Record anyway.
            return {"ok": False, "note": tr("rec_held", title=str(held.get("title") or ""))}
        return {"ok": False}

    def _phone_rec_stop(self, params: dict) -> dict[str, Any]:
        """A running recording stopped, or a scheduled one cancelled - as
        the record menu and the Recordings list do."""
        jid = str(params.get("id") or "")
        j = next((k for k in self.rec.jobs if str(k.get("id")) == jid), None)
        if j is None or j.get("status") not in ("recording", "scheduled"):
            return {"ok": False}
        self.rec.cancel(jid)
        return {"ok": True}

    def _phone_rec_go(self, params: dict) -> dict[str, Any]:
        """A held recording: Record anyway - asked as the note asks it."""
        jid = str(params.get("id") or "")
        self._rec_go_ahead(jid)
        j = next((k for k in self.rec.jobs if str(k.get("id")) == jid), None)
        return {"ok": bool(j is not None and (j.get("_go_ahead") or j.get("status") == "recording"))}

    def _phone_rec_next(self, params: dict) -> dict[str, Any]:
        """The channel's next programme recorded (the scheduled-recording
        path, with the recording padding) or reminded."""
        item, prog = params.get("item") or {}, params.get("prog") or {}
        start, stop = prog.get("start"), prog.get("stop")
        if not item or start is None or stop is None:
            return {"ok": False}
        title = str(prog.get("title") or "")
        if params.get("what") == "remind":
            rem = getattr(self, "reminders", None)
            if rem is not None and rem.has(item.get("stream_id"), start):
                return {"ok": True}
            self._add_reminder(item, {"title": title, "start_timestamp": start, "stop_timestamp": stop})
            return {"ok": True, "note": tr("reminder_set", title=title or item.get("name") or "")}
        before = {j.get("id") for j in self.rec.jobs}
        self._record_programme(item, {"title": title}, float(start), float(stop))
        new = [j for j in self.rec.jobs if j.get("id") not in before]
        if not new:
            return {"ok": False}
        from .qt_i18n import format_when
        j = new[0]
        if j.get("status") == "scheduled":
            return {"ok": True, "note": tr("rec_scheduled_for", title=j.get("title") or title,
                                           when=format_when(float(j.get("start") or start)))}
        return {"ok": True}

    def _phone_rec_jobs(self) -> list[dict[str, Any]]:
        """The recordings running and to come, for the phone's Recordings."""
        out = []
        for j in getattr(getattr(self, "rec", None), "jobs", []) or []:
            status = j.get("status")
            if status not in ("recording", "scheduled"):
                continue
            out.append({"id": str(j.get("id") or ""), "title": str(j.get("title") or ""),
                        "channel": str(j.get("channel") or ""), "status": status,
                        "start": float(j.get("start") or 0), "held": bool(j.get("_held"))})
        return out
