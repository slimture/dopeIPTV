"""AutoTimer on an Enigma2 box: the rules of the box's own AutoTimer plugin
read, made, changed, turned on and off and deleted, "Run now", and what the
rules would record - so dopeIPTV's own series rules (core/series_rules.py)
leave to the box what the box records itself, even while this computer is
off.

Checked against the plugin's source as it is - enigma2-plugins
autotimer/src, OpenPLi's and oe-alliance's (OpenATV, OpenViX):
AutoTimerResource.py, AutoTimerConfiguration.py (buildConfig, parseEntry),
AutoTimer.py (parseTimer) - and OpenWebif's controllers/AT.py, which serves
``/autotimer`` and hands the calls below to the plugin:

- ``/autotimer``: the rules as buildConfig writes them for the web
  interface. ``<autotimer version>``, ``<defaults>``, each ``<timer name
  match enabled="yes|no" id>`` with ``from``/``to`` (the time span, HH:MM),
  ``offset`` (minutes before[,after], "5.0" from Python 3 boxes),
  ``location``, ``justplay``, ``searchType`` (exact, partial, start, end,
  description, favoritedesc - partial when left out), ``searchCase``
  (written only when sensitive), ``avoidDuplicateDescription`` (0-3) and
  whatever else the box has; its channels and bouquets together as
  ``<e2service>``, ``<include|exclude where>`` (the days as dayofweek 0-6
  from Monday, or "weekday" / "weekend"), ``<e2tag>``, ``<afterevent>``.
  Without the plugin OpenWebif answers an e2simplexmlresult "AutoTimer
  Plugin not found" and serves none of the calls below; the old web
  interface answers 404.
- ``/autotimer/edit``: a rule changed by its id, or a new one without.
  Only what is sent changes, so what the editor here does not show - tags,
  after event, filters, counters, VPS, SeriesPlugin, attributes this file
  does not know - stays on the box untouched. ``match``, ``name``,
  ``services``, ``bouquets``, the filters and the tags are unquoted once
  more after the query is read, so they are sent quoted (an IPTV entry's
  ``%3a`` would turn into a colon otherwise). oe-alliance's sets
  ``always_zap`` to 0 unless it is sent and OpenPLi's takes any text for
  on: it is sent only when on.
- ``/autotimer/remove?id=``.
- There is no ``/autotimer/change_status``. oe-alliance's plugin has
  ``/autotimer/change`` (id, name, enabled), which OpenWebif serves where
  the plugin has it; elsewhere a rule is turned on or off through
  ``/autotimer/edit`` with its id and ``enabled``.
- ``/autotimer/parse``: Run now - the box sets its timers. OpenPLi's writes
  ``<ignore />`` every 50 s while it works.
- ``/autotimer/simulate``: what the rules would add, each an
  ``<e2simulatedtimer>`` with the service, title, begin and end (the
  padding in) and the rule's name. oe-alliance's takes an ``id`` for one
  rule and lists what it skips too (``<e2state>Skip``); OpenPLi's answers
  for every rule. A programme the box has a timer for is skipped, so what
  the box will record is the simulate and its timer list
  (``/web/timerlist``) together.

All of it is network: called off the window's thread.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any
from urllib.parse import quote
from xml.etree import ElementTree as ET

from ..core.log import log
from ..i18n import LocalizedError
from .boxes import child_text, clean_text

if TYPE_CHECKING:
    from .enigma2 import Enigma2Client

EXACT, PARTIAL, START, DESCRIPTION = "exact", "partial", "start", "description"
#: The search types the editor offers; a rule with another keeps its own.
SEARCH_TYPES = (EXACT, PARTIAL, START, DESCRIPTION)
#: avoidDuplicateDescription: no, on the same channel, on any channel, on
#: any channel and among the recordings.
DUPLICATES = (0, 1, 2, 3)
DAYS = ("0", "1", "2", "3", "4", "5", "6")
_WEEKDAY, _WEEKEND = ("0", "1", "2", "3", "4"), ("5", "6")
#: How long what the box would record is trusted: a simulate makes the box
#: look through its whole guide.
RECORDS_SECS = 30 * 60
_TIMEOUT = (6.0, 30.0)
_SLOW = (6.0, 300.0)
_ENDED = "3"


@dataclass
class Rule:
    """One AutoTimer rule as the box lists it. *attrs* is every attribute
    the box wrote, those this file does not know among them; *names* the
    channel and bouquet names the box gave its references."""
    id: int | None = None
    name: str = ""
    match: str = ""
    enabled: bool = True
    search_type: str = PARTIAL
    case: bool = False
    services: list[str] = field(default_factory=list)
    bouquets: list[str] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)
    timespan: tuple[str, str] | None = None
    days: list[str] = field(default_factory=list)
    offset: tuple[int, int] | None = None
    justplay: bool = False
    location: str = ""
    avoid_dup: int = 0
    always_zap: bool = False
    tags: list[str] = field(default_factory=list)
    include: dict[str, list[str]] = field(default_factory=dict)
    attrs: dict[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.name or self.match

    def day_set(self) -> set[str]:
        """The days it records on, "weekday" and "weekend" spelled out;
        empty for every day."""
        out: set[str] = set()
        for d in self.days:
            out.update(_WEEKDAY if d == "weekday" else _WEEKEND if d == "weekend"
                       else (d,) if d in DAYS else ())
        return out

    def copy(self, **changes: Any) -> Rule:
        """A copy of its own (lists and dicts too), with *changes*."""
        own: dict[str, Any] = {
            "services": list(self.services), "bouquets": list(self.bouquets),
            "names": dict(self.names), "days": list(self.days), "tags": list(self.tags),
            "include": {k: list(v) for k, v in self.include.items()},
            "attrs": dict(self.attrs)}
        own.update(changes)
        return replace(self, **own)


@dataclass
class Rules:
    """What ``/autotimer`` said: whether the box has the plugin, its rules,
    and what it said when it has none."""
    present: bool
    rules: list[Rule] = field(default_factory=list)
    said: str = ""


def _is_bouquet(ref: str) -> bool:
    from .enigma2 import is_bouquet
    return is_bouquet(ref)


def _int(text: str) -> int:
    """A whole number from what the box wrote ("5", "5.0", "1700000000")."""
    try:
        return max(0, int(round(float(text))))
    except (TypeError, ValueError, OverflowError):
        return 0


def _hhmm(text: str) -> str:
    parts = (text or "").strip().split(":")
    try:
        h, m = int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return ""
    return f"{h:02d}:{m:02d}" if 0 <= h < 24 and 0 <= m < 60 else ""


def parse_rule(el: ET.Element) -> Rule:
    """A ``<timer>`` as the box wrote it."""
    a = dict(el.attrib)
    rid = a.get("id", "")
    r = Rule(id=int(rid) if re.fullmatch(r"-?\d{1,18}", rid) else None,
             name=a.get("name", ""), match=a.get("match", ""),
             enabled=a.get("enabled", "yes") != "no",
             search_type=a.get("searchType") or PARTIAL,
             case=a.get("searchCase") == "sensitive",
             justplay=a.get("justplay", "0") not in ("", "0"),
             location=a.get("location", ""),
             avoid_dup=_int(a.get("avoidDuplicateDescription", "0")),
             always_zap=a.get("always_zap", "0") not in ("", "0"),
             attrs=a)
    span = (_hhmm(a.get("from", "")), _hhmm(a.get("to", "")))
    r.timespan = span if all(span) else None
    if a.get("offset"):
        parts = a["offset"].split(",")
        before = _int(parts[0])
        r.offset = (before, _int(parts[1]) if len(parts) > 1 else before)
    for s in el.findall("e2service"):
        ref = child_text(s, "e2servicereference")
        if not ref:
            continue
        (r.bouquets if _is_bouquet(ref) else r.services).append(ref)
        r.names[ref] = child_text(s, "e2servicename")
    # The plugin's own file names them so, where a box lists it as is.
    r.services += [t for t in ((x.text or "").strip() for x in el.findall("serviceref")) if t]
    r.bouquets += [t for t in ((x.text or "").strip() for x in el.findall("bouquet")) if t]
    for inc in el.findall("include"):
        where, text = inc.get("where", ""), (inc.text or "").strip()
        if where and text:
            r.include.setdefault(where, []).append(text)
    r.days = list(r.include.get("dayofweek", []))
    r.tags = [t for t in (clean_text(x.text) for x in el.findall("e2tag")) if t]
    if not r.tags:
        r.tags = [t for t in (clean_text(x.text) for x in el.findall("tag")) if t]
    return r


def parse_rules(root: ET.Element) -> Rules:
    """``/autotimer``'s answer: the rules, or that the box has no plugin
    (OpenWebif's "AutoTimer Plugin not found", or a page that is not the
    plugin's)."""
    if root.tag == "e2simplexmlresult":
        return Rules(False, said=child_text(root, "e2statetext"))
    if root.tag != "autotimer":
        return Rules(False, said=root.tag)
    return Rules(True, [parse_rule(t) for t in root.findall("timer")])


def _q(text: str) -> str:
    """A value the plugin unquotes a second time, quoted for it."""
    return quote(text, safe="")


def edit_params(rule: Rule, before: Rule | None = None) -> dict[str, Any]:
    """The ``/autotimer/edit`` query for *rule*: every field the editor
    shows, and nothing else - the box keeps what is not sent. *before* is
    the rule as read: the days are sent only when they changed (a box's
    "weekend" stays "weekend")."""
    p: dict[str, Any] = {}
    if rule.id is not None:
        p["id"] = str(rule.id)
    p["match"] = _q(rule.match)
    p["name"] = _q(rule.name or rule.match)
    p["enabled"] = "yes" if rule.enabled else "no"
    p["searchType"] = rule.search_type or PARTIAL
    p["searchCase"] = "sensitive" if rule.case else "insensitive"
    p["justplay"] = "1" if rule.justplay else "0"
    if rule.always_zap and not rule.justplay:
        p["always_zap"] = "1"
    p["services"] = ",".join(_q(s) for s in rule.services)
    p["bouquets"] = ",".join(_q(b) for b in rule.bouquets)
    span = rule.timespan
    p["timespanFrom"], p["timespanTo"] = span if span and all(span) else ("", "")
    p["offset"] = f"{rule.offset[0]},{rule.offset[1]}" if rule.offset else ""
    p["location"] = rule.location or ""
    p["avoidDuplicateDescription"] = str(rule.avoid_dup if rule.avoid_dup in DUPLICATES else 0)
    if before is None or rule.day_set() != before.day_set():
        # The days are one of the includes, and the plugin reads the others
        # back from what it has when only these come: they come too, as the
        # box listed them.
        p["dayofweek"] = sorted(rule.day_set()) or [""]
        for where in ("title", "shortdescription", "description"):
            p[where] = [_q(x) for x in rule.include.get(where, [])] or [""]
    return p


def _said(root: ET.Element) -> tuple[bool, str]:
    if root.tag != "e2simplexmlresult":
        raise LocalizedError("err_unexpected_response")
    return (child_text(root, "e2state").lower() == "true",
            child_text(root, "e2statetext", True))


def parse_simulate(root: ET.Element) -> list[dict]:
    """``/autotimer/simulate``'s timers: what the box would add, without
    those it says it skips."""
    if root.tag == "e2simplexmlresult":
        ok, text = _said(root)
        if not ok:
            raise LocalizedError(text or "err_unexpected_response")
        return []
    if root.tag != "e2autotimersimulate":
        raise LocalizedError("err_unexpected_response")
    out = []
    for t in root.iter("e2simulatedtimer"):
        if child_text(t, "e2state").lower() == "skip":
            continue
        start, stop = _int(child_text(t, "e2timebegin")), _int(child_text(t, "e2timeend"))
        if not stop > start > 0:
            continue
        out.append({"sref": child_text(t, "e2servicereference"),
                    "channel": child_text(t, "e2servicename"),
                    "title": child_text(t, "e2name"), "start": start, "stop": stop,
                    "rule": child_text(t, "e2autotimername")})
    out.sort(key=lambda x: (x["start"], x["channel"]))
    return out


def parse_timers(root: ET.Element, now: float) -> list[dict]:
    """The box's own recordings to come (``/web/timerlist``): not a zap,
    not turned off, not over."""
    out = []
    for t in root.iter("e2timer"):
        start, stop = _int(child_text(t, "e2timebegin")), _int(child_text(t, "e2timeend"))
        if (child_text(t, "e2justplay") == "1" or child_text(t, "e2disabled") == "1"
                or child_text(t, "e2state") == _ENDED or stop <= max(start, now)):
            continue
        out.append({"sref": child_text(t, "e2servicereference"),
                    "channel": child_text(t, "e2servicename"),
                    "title": child_text(t, "e2name"), "start": start, "stop": stop,
                    "rule": ""})
    return out


def _status(err: BaseException) -> int:
    return int(getattr(getattr(err, "response", None), "status_code", 0) or 0)


class AutoTimer:
    """The AutoTimer of one box, through its client's requests (its login,
    basic or digest). *present*: None until asked, then whether the box has
    the plugin."""

    def __init__(self, client: Enigma2Client) -> None:
        self.client = client
        self.present: bool | None = None
        self.said = ""
        self._records: tuple[float, list[dict]] | None = None

    def _xml(self, path: str, params: dict | None = None,
             timeout: tuple[float, float] = _TIMEOUT) -> ET.Element:
        try:
            return self.client.get_xml(path, params, timeout)
        except ET.ParseError:
            raise LocalizedError("err_unexpected_response") from None

    def rules(self) -> Rules:
        """The box's rules, or Rules(False) when it has no AutoTimer - said
        plainly, never an error."""
        try:
            got = parse_rules(self.client.get_xml("/autotimer", None, _TIMEOUT))
        except ET.ParseError:
            got = Rules(False, said="not XML")
        except Exception as e:
            if _status(e) != 404:
                raise
            got = Rules(False, said="404")
        self.present, self.said = got.present, got.said
        if not got.present:
            log.info("enigma2: no AutoTimer on the box (%s)", got.said or "-")
        return got

    def _changed(self) -> None:
        self._records = None

    def save(self, rule: Rule, before: Rule | None = None) -> tuple[bool, str]:
        """/autotimer/edit: *rule* made (no id) or changed. (ok, what the
        box said)."""
        ok, text = _said(self._xml("/autotimer/edit", edit_params(rule, before)))
        self._changed()
        log.info("enigma2: AutoTimer %s %s", "saved" if ok else "not saved:",
                 "" if ok else text)
        return ok, text

    def set_enabled(self, rule: Rule, on: bool) -> tuple[bool, str]:
        """A rule turned on or off: /autotimer/change where the box has it,
        else /autotimer/edit with only the id, the match, the name and the
        switch."""
        if rule.id is None:
            return False, ""
        flag = "yes" if on else "no"
        try:
            ok, text = _said(self._xml("/autotimer/change", {"id": str(rule.id), "enabled": flag}))
        except Exception as e:
            if _status(e) != 404:
                raise
            params = {"id": str(rule.id), "match": _q(rule.match),
                      "name": _q(rule.name or rule.match), "enabled": flag}
            if rule.always_zap and not rule.justplay:
                params["always_zap"] = "1"
            ok, text = _said(self._xml("/autotimer/edit", params))
        self._changed()
        return ok, text

    def remove(self, rule: Rule) -> tuple[bool, str]:
        if rule.id is None:
            return False, ""
        ok, text = _said(self._xml("/autotimer/remove", {"id": str(rule.id)}))
        self._changed()
        return ok, text

    def run(self) -> tuple[bool, str]:
        """/autotimer/parse: the box looks through its guide now and sets
        the timers its rules ask for."""
        ok, text = _said(self._xml("/autotimer/parse", None, _SLOW))
        self._changed()
        return ok, text

    def simulate(self, rule: Rule | None = None) -> list[dict]:
        """What the rules (or *rule* alone) would add now; nothing is set."""
        params = {"id": str(rule.id)} if rule is not None and rule.id is not None else None
        found = parse_simulate(self._xml("/autotimer/simulate", params, _SLOW))
        if rule is not None:
            # A box whose simulate takes no id answers for every rule.
            found = [x for x in found if not x["rule"] or x["rule"] == rule.label]
        return found

    def records(self, now: float | None = None) -> list[dict]:
        """What the box records itself from now on: its timers, and what its
        enabled rules would add (simulated at most every RECORDS_SECS). Each
        with "key", the channel's id in the box's list ("" when not in it)."""
        now = time.time() if now is None else now
        timers = parse_timers(self._xml("/web/timerlist"), now)
        cached = self._records
        if cached is None or now - cached[0] > RECORDS_SECS:
            sim: list[dict] = []
            got = self.rules()
            if got.present and any(r.enabled for r in got.rules):
                sim = self.simulate()
            cached = self._records = (now, sim)
        out = []
        for x in timers + [s for s in cached[1] if s["stop"] > now]:
            sid = self.client.stream_of(x["sref"])
            out.append(dict(x, key="" if sid is None else str(sid)))
        return out
