"""Series recordings: every upcoming airing of a programme, recorded.

A rule is a title - the exact title, or a word the title contains - on one
channel or on any channel of the source. The window looks through the guide
with ``find_airings`` when the guide is (re)loaded and every so often, and
``plan`` turns what was found into recordings to schedule: the next
``DAYS`` days, the padding from Settings > Recording, nothing on a channel
already being recorded then, nothing a rule scheduled before (a recording
the user cancelled stays cancelled), and one recording when the same
programme airs at the same time on two channels.

Pure logic and the store the rules live in; nothing here starts a recorder.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from itertools import pairwise
from typing import Any, Callable, Iterable, Protocol

#: How far ahead the guide is looked through.
DAYS = 7
#: Upcoming recordings one rule keeps scheduled at a time - a keyword on
#: every channel could otherwise fill the list with hundreds.
MAX_PER_RULE = 25
#: An airing that starts sooner than this is left alone: it is on already,
#: or about to be, and a recording that would start at once opens a
#: connection of its own without asking.
LEAD_S = 60
#: How long a scheduled airing is remembered after its programme ended.
TAKEN_KEEP_S = 2 * 86400

PAD_BEFORE_KEY = "rec_pad_before_min"
PAD_AFTER_KEY = "rec_pad_after_min"
PAD_BEFORE_MAX = 30
PAD_AFTER_MAX = 120

EXACT = "title"
WORD = "word"
THIS = "channel"
ANY = "any"

# Channel fields kept in a rule, enough to find the channel's guide and
# stream again without the whole line-up.
_CH_FIELDS = ("stream_id", "name", "epg_channel_id", "stream_icon", "num")
_JOB_LIVE = ("scheduled", "recording")


class Settings(Protocol):
    def value(self, key: str, default: Any = None) -> Any: ...

    def setValue(self, key: str, value: Any) -> None: ...  # noqa: N802 - Qt's name


def norm(text: object) -> str:
    """A title as it is compared: case and spacing do not count."""
    return " ".join(str(text or "").split()).casefold()


def _minutes(settings: Settings, key: str, most: int) -> int:
    try:
        n = int(float(settings.value(key, 0) or 0))
    except (TypeError, ValueError):
        return 0
    return max(0, min(most, n))


def padding(settings: Settings) -> tuple[int, int]:
    """(minutes before, minutes after) a programme recorded from the guide,
    as Settings > Recording has them; 0 and 0 unless the user set them."""
    return (_minutes(settings, PAD_BEFORE_KEY, PAD_BEFORE_MAX),
            _minutes(settings, PAD_AFTER_KEY, PAD_AFTER_MAX))


def padded(start: float, stop: float, before_min: int, after_min: int,
           now: float) -> tuple[float, float]:
    """The recording's window for a programme from *start* to *stop*: the
    padding on either side, and never starting before *now*."""
    return max(now, start - before_min * 60), stop + after_min * 60


def stream_key(channel: dict | None) -> str:
    """A channel's id in its source ("" without one)."""
    sid = (channel or {}).get("stream_id")
    return "" if sid is None or sid == "" else str(sid)


def make_rule(text: str, kind: str = EXACT,
              channel: dict | None = None) -> dict | None:
    """A new rule: *text* as an exact title or (WORD) a word in the title,
    on *channel* only or, without one, on any channel. None for no text."""
    text = " ".join(str(text or "").split())
    if not text or kind not in (EXACT, WORD):
        return None
    rule: dict[str, Any] = {"id": uuid.uuid4().hex[:10], "kind": kind,
                            "text": text, "scope": ANY,
                            "created": int(time.time())}
    if channel is not None and stream_key(channel):
        rule["scope"] = THIS
        rule["channel"] = {k: channel[k] for k in _CH_FIELDS
                           if channel.get(k) not in (None, "")}
    return rule


def rule_key(rule: dict) -> str:
    """The channel a rule is for ("" for any channel)."""
    if rule.get("scope") != THIS:
        return ""
    return stream_key(rule.get("channel"))


def matches(rule: dict, title: str) -> bool:
    t, want = norm(title), norm(rule.get("text"))
    if not want:
        return False
    return t == want if rule.get("kind") == EXACT else want in t


def _valid(rule: object) -> bool:
    return (isinstance(rule, dict) and bool(rule.get("id"))
            and rule.get("kind") in (EXACT, WORD) and bool(norm(rule.get("text")))
            and (rule.get("scope") == ANY
                 or (rule.get("scope") == THIS and bool(rule_key(rule)))))


class Matcher:
    """The ids of the rules a title matches, worked out once per title: a
    guide repeats the same titles day after day and channel after channel."""

    def __init__(self, rules: Iterable[dict]) -> None:
        self._rules = [(str(r["id"]), r.get("kind"), norm(r.get("text")))
                       for r in rules]
        self._seen: dict[str, tuple[str, ...]] = {}

    def __call__(self, title: str) -> tuple[str, ...]:
        hit = self._seen.get(title)
        if hit is None:
            t = norm(title)
            hit = tuple(rid for rid, kind, want in self._rules
                        if want and (t == want if kind == EXACT else want in t))
            self._seen[title] = hit
        return hit


@dataclass
class Airing:
    """A programme a rule asks for: on which channel, when."""
    rule: str
    channel: dict
    key: str
    title: str
    start: float
    stop: float
    url: str = ""


@dataclass
class Plan:
    """An airing to schedule, and the recording's window (padding in)."""
    airing: Airing
    start: float
    stop: float


#: The guide's entries for a channel overlapping a window; the last
#: argument tells the titles worth an entry (XmltvGuide.programmes_in).
Programmes = Callable[[dict, float, float, Callable[[str], Any]],
                      Iterable[dict]]


def rule_channels(rules: Iterable[dict]) -> list[dict]:
    """The channels the one-channel rules are for, as the rules keep them."""
    out, seen = [], set()
    for r in rules:
        key = rule_key(r)
        if key and key not in seen:
            seen.add(key)
            out.append(dict(r.get("channel") or {}))
    return out


def find_airings(rules: list[dict], channels: Iterable[dict],
                 programmes: Programmes, now: float, days: int = DAYS,
                 per_rule: int = 2 * MAX_PER_RULE,
                 shift: float = 0.0) -> list[Airing]:
    """Every airing in the next *days* days that a rule asks for, soonest
    first, at most *per_rule* for one rule. A channel listed twice in the
    line-up counts once; an airing two rules want goes to the first.
    *shift*: seconds the guide's times are off the clock (the guide offset
    in Settings) - the airings come back in clock time."""
    rules = [r for r in rules if _valid(r)]
    if not rules:
        return []
    match = Matcher(rules)
    lo, hi = now + LEAD_S, now + days * 86400
    out: list[Airing] = []
    seen: set[tuple[str, float]] = set()
    for ch in channels:
        key = stream_key(ch)
        if not key:
            continue
        here = {str(r["id"]) for r in rules
                if r.get("scope") == ANY or rule_key(r) == key}
        if not here:
            continue
        for p in programmes(ch, lo - shift, hi - shift, match):
            try:
                start = float(p["start_timestamp"]) + shift
                stop = float(p["stop_timestamp"]) + shift
            except (KeyError, TypeError, ValueError):
                continue
            if start < lo or start >= hi or stop <= start or (key, start) in seen:
                continue
            title = str(p.get("title") or "")
            rid = next((r for r in match(title) if r in here), None)
            if rid is not None:
                seen.add((key, start))
                out.append(Airing(rid, ch, key, title, start, stop))
    out.sort(key=lambda a: (a.start, a.key))
    kept: list[Airing] = []
    count: dict[str, int] = {}
    for a in out:
        if count.get(a.rule, 0) < per_rule:
            count[a.rule] = count.get(a.rule, 0) + 1
            kept.append(a)
    return kept


@dataclass
class _Window:
    """A recording already there - or one a rule made before - as the
    planner sees it: its channel, its programme's times, its title."""
    key: str
    url: str
    start: float
    stop: float
    rec_start: float
    rec_stop: float
    title: str
    fixed: bool = True
    plan: Plan | None = None


def _num(v: Any, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _job_window(job: dict) -> _Window:
    rec_start = _num(job.get("start"), 0.0)
    rec_stop = _num(job.get("stop"), float("inf"))
    return _Window(str(job.get("stream_key") or ""), str(job.get("url") or ""),
                   _num(job.get("prog_start"), rec_start),
                   _num(job.get("prog_stop"), rec_stop),
                   rec_start, rec_stop, norm(job.get("title")))


def _taken_window(t: dict) -> _Window:
    start, stop = _num(t.get("start"), 0.0), _num(t.get("stop"), 0.0)
    return _Window(str(t.get("key") or ""), "", start, stop, start, stop,
                   str(t.get("title") or ""))


def _same_channel(a: Airing, w: _Window) -> bool:
    return bool((a.key and w.key and a.key == w.key)
                or (a.url and w.url and a.url == w.url))


def _mostly_overlap(a: Airing, w: _Window) -> bool:
    """Whether *w* covers at least half of the shorter of the two."""
    shared = min(a.stop, w.stop) - max(a.start, w.start)
    if shared <= 0:
        return a.start == w.start
    return shared * 2 >= min(a.stop - a.start, w.stop - w.start)


def _covered(a: Airing, windows: Iterable[_Window]) -> bool:
    """Recorded already: the same channel at much the same time, or the
    same programme at the same time on another channel."""
    title = norm(a.title)
    for w in windows:
        if not _mostly_overlap(a, w):
            continue
        if _same_channel(a, w) or (title and w.title == title):
            return True
    return False


def _meet(windows: list[_Window]) -> None:
    """Back-to-back recordings on one channel do not overlap for their
    padding (that took a second connection for the minutes they shared):
    two new ones meet halfway between their programmes, a new one next to
    one already there ends where it starts, or starts where it ends."""
    for ch in {w.key or w.url for w in windows}:
        row = sorted((w for w in windows if (w.key or w.url) == ch),
                     key=lambda w: w.start)
        for a, b in pairwise(row):
            if a.stop > b.start or a.rec_stop <= b.rec_start:
                continue                   # not one after the other
            if a.fixed and b.fixed:
                continue
            if not a.fixed and not b.fixed:
                mid = (a.stop + b.start) / 2
                a.rec_stop = max(a.stop, min(a.rec_stop, mid))
                b.rec_start = min(b.start, max(b.rec_start, mid))
            elif b.fixed:
                a.rec_stop = max(a.stop, min(a.rec_stop, b.rec_start))
            else:
                b.rec_start = min(b.start, max(b.rec_start, a.rec_stop))
    for w in windows:
        if w.plan is not None:
            w.plan.start, w.plan.stop = w.rec_start, w.rec_stop


def plan(airings: Iterable[Airing], jobs: Iterable[dict],
         taken: Iterable[dict], now: float, before_min: int = 0,
         after_min: int = 0, per_rule: int = MAX_PER_RULE) -> list[Plan]:
    """The airings to schedule now, soonest first, each with its padded
    window. Left out: what is recorded or scheduled already (*jobs*, the
    recorder's list), what a rule scheduled before (*taken*, from the
    store - so a cancelled one is not brought back), a programme airing at
    the same time on two channels after the first, and a rule's airings
    past *per_rule* upcoming recordings."""
    jobs = list(jobs)
    recs = [_job_window(j) for j in jobs
            if isinstance(j, dict) and j.get("status") in _JOB_LIVE]
    earlier = [_taken_window(t) for t in taken if isinstance(t, dict)]
    count: dict[str, int] = {}
    for j in jobs:
        if (isinstance(j, dict) and j.get("rule") and j.get("status") == "scheduled"
                and _num(j.get("start"), 0.0) > now):
            count[str(j["rule"])] = count.get(str(j["rule"]), 0) + 1
    out: list[Plan] = []
    for a in sorted(airings, key=lambda a: (a.start, a.key)):
        if a.start < now + LEAD_S or count.get(a.rule, 0) >= per_rule:
            continue
        if _covered(a, recs) or _covered(a, earlier):
            continue
        s, e = padded(a.start, a.stop, before_min, after_min, now)
        p = Plan(a, s, e)
        out.append(p)
        count[a.rule] = count.get(a.rule, 0) + 1
        recs.append(_Window(a.key, a.url, a.start, a.stop, s, e,
                            norm(a.title), fixed=False, plan=p))
    # Only the recordings themselves: what a rule scheduled before is a
    # job here already, or was cancelled and takes no connection. A job
    # known by its address alone is on the channel that address is.
    by_url = {w.url: w.key for w in recs if w.key and w.url}
    for w in recs:
        if not w.key and w.url in by_url:
            w.key = by_url[w.url]
    _meet([w for w in recs if w.rec_stop > now])
    return out


def retimed(airings: Iterable[Airing], jobs: Iterable[dict],
            now: float) -> list[tuple[dict, Airing]]:
    """Recordings a rule scheduled whose programme the guide now has at
    another time (the same rule, the same channel, mostly the same time):
    each with the airing it should follow. Started ones are left be."""
    airings = list(airings)
    out: list[tuple[dict, Airing]] = []
    for j in jobs:
        if (not isinstance(j, dict) or not j.get("rule")
                or j.get("status") != "scheduled"
                or _num(j.get("start"), 0.0) <= now):
            continue
        w = _job_window(j)
        for a in airings:
            if (a.rule == str(j["rule"]) and _same_channel(a, w)
                    and _mostly_overlap(a, w)):
                if (a.start, a.stop) != (w.start, w.stop):
                    out.append((j, a))
                break
    return out


class SeriesRuleStore:
    """A source's series rules, and the airings they scheduled (kept a
    while so a cancelled recording is not scheduled again). One JSON value
    in the app's settings, per source like the reminders."""

    def __init__(self, settings: Settings,
                 playlist_id: str | None = None) -> None:
        self._settings = settings
        self._key = (f"series_rules_{playlist_id}" if playlist_id
                     else "series_rules")
        try:
            data = json.loads(settings.value(self._key, "") or "{}")
        except (TypeError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        rules = data.get("rules")
        taken = data.get("taken")
        self._rules: list[dict] = [r for r in (rules if isinstance(rules, list) else [])
                                   if _valid(r)]
        self._taken: list[dict] = [t for t in (taken if isinstance(taken, list) else [])
                                   if isinstance(t, dict) and t.get("key")]

    def rules(self) -> list[dict]:
        return [dict(r) for r in self._rules]

    def get(self, rule_id: str) -> dict | None:
        return next((dict(r) for r in self._rules if r["id"] == rule_id), None)

    def add(self, rule: dict) -> dict | None:
        """Keep *rule*; the one already kept when it asks for the same."""
        if not _valid(rule):
            return None
        for r in self._rules:
            if (r.get("kind"), norm(r.get("text")), rule_key(r)) == (
                    rule.get("kind"), norm(rule.get("text")), rule_key(rule)):
                return dict(r)
        self._rules.append(dict(rule))
        self._save()
        return dict(rule)

    def remove(self, rule_id: str) -> bool:
        """Drop a rule and what it scheduled from the memory: a rule made
        again for the same title schedules its airings anew."""
        before = len(self._rules)
        self._rules = [r for r in self._rules if r["id"] != rule_id]
        if len(self._rules) == before:
            return False
        self._taken = [t for t in self._taken if t.get("rule") != rule_id]
        self._save()
        return True

    def taken(self) -> list[dict]:
        return [dict(t) for t in self._taken]

    def remember(self, plans: Iterable[Plan], now: float | None = None) -> None:
        """What was scheduled, so it is not scheduled again; what ended long
        ago is forgotten."""
        now = time.time() if now is None else now
        keep = [t for t in self._taken
                if _num(t.get("stop"), 0.0) > now - TAKEN_KEEP_S]
        for p in plans:
            a = p.airing
            keep.append({"key": a.key, "start": a.start, "stop": a.stop,
                         "title": norm(a.title), "rule": a.rule})
        if keep != self._taken:
            self._taken = keep
            self._save()

    def _save(self) -> None:
        self._settings.setValue(self._key, json.dumps(
            {"rules": self._rules, "taken": self._taken}))
