"""Series recordings, from the guide to the recorder's jobs: what the window
(ui/mw_series.py) and ``dopeiptv --server`` (services/server_recorder.py)
both do with a source's rules - no Qt here.

``find`` looks through the guide for what the rules ask for, each airing
with its address (a portal's is asked for when the recording starts:
``links_later``), and asks a box that records by itself what it will
record (``box_records``). ``schedule`` turns what was found into the
recorder's jobs: a recording the box now records itself taken off the list,
one whose programme the guide moved moved with it, and the rest planned by
core/series_rules.plan - the padding, nothing twice, one copy of a
programme on two channels, no more at once than the account allows - each
added as a job that keeps its rule, its channel and its programme's times.
The rules' memory of what they scheduled is kept up to date with it.

Reminders are a profile's own and the window's: not here.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import series_rules
from .log import log
from .privacy import for_log


class Airings(list):
    """What find found, and what the box records itself (``box``: key,
    start, stop, title, source of each) - empty for any other source."""
    box: list[dict] = []


def links_later(client: Any) -> bool:
    """Whether a recording from *client* asks for its address when it
    starts, not when it is scheduled: a portal's links (and a box's, whose
    list may not be in yet) are slow to get, and a portal's last seconds."""
    return bool(getattr(client, "slow_links", False))


def box_records(client: Any, rules: list[dict], found: list, now: float,
                source: str) -> list[dict]:
    """(off the window's thread) What a box with AutoTimer records itself,
    when a recording rule found something: its timers and its rules'
    simulate."""
    autotimer = getattr(client, "autotimer", None)
    record = series_rules.of_action(rules, series_rules.RECORD)
    if not callable(autotimer) or not any(a.rule in record for a in found):
        return []
    try:
        got = autotimer().records(now)
    except Exception as e:
        log.info("[series] the box's own recordings not read: %s", e)
        return []
    return [dict(x, source=source) for x in got]


def find(client: Any, guide: Any, rules: list[dict], keep: Callable[[dict], bool] | None,
         now: float, shift: float, source: str = "") -> Airings:
    """(off the window's thread) The airings the rules ask for, each with
    its address. The whole line-up only when a rule is for any channel
    (*keep*: which of its channels may be looked at); a one-channel rule
    finds its channel as the rule keeps it. A client whose links are slow to
    get and short-lived (a portal's create_link lasts seconds) is not asked
    for any here: such a recording asks for its address when it starts
    (RecordingManager.LATER)."""
    chans = series_rules.rule_channels(rules)
    if keep is not None:
        chans = [c for c in client.live_streams(None) or [] if keep(c)] + chans
    found = Airings(series_rules.find_airings(rules, chans, guide.programmes_in, now,
                                              shift=shift, source=source))
    found.box = box_records(client, rules, found, now, source)
    if links_later(client):
        return found
    # A reminder needs no address.
    remind = series_rules.of_action(rules, series_rules.REMIND)
    for a in found:
        if a.rule in remind:
            continue
        try:
            a.url = client.live_url(a.channel.get("stream_id"), "ts") or ""
        except Exception as e:
            log.info("[series] no address for %s: %s",
                     for_log(a.channel.get("name")), e)
            a.url = ""
    return found


#: Why schedule() added nothing it had planned (Scheduled.blocked).
NO_RECORDER = "recorder"
FOLDER_FULL = "cap"


@dataclass
class Scheduled:
    """What schedule() did."""
    #: The recordings added, and those moved with their programme.
    added: list[series_rules.Plan] = field(default_factory=list)
    moved: list[series_rules.Plan] = field(default_factory=list)
    #: Airings the box records itself (left out), and the jobs a rule had
    #: scheduled that the box took on since (taken off the list).
    boxed: list[series_rules.Airing] = field(default_factory=list)
    to_box: list[dict] = field(default_factory=list)
    #: Airings left out, with why: series_rules.AT_ONCE or CONNECTIONS.
    left_out: list[tuple[series_rules.Airing, str]] = field(default_factory=list)
    #: Planned but not added: NO_RECORDER, FOLDER_FULL ("" when added).
    blocked: str = ""
    planned: int = 0


def schedule(found: list, rec: Any, store: Any, settings: Any, *, later: bool,
             max_connections: int | None, name_of: Callable[[dict], str],
             extra_of: Callable[[dict], dict], now: float | None = None) -> Scheduled:
    """Schedule with recorder *rec* (core/recording.RecordingManager) what
    the recording rules of *store* (series_rules.SeriesRuleStore) found and
    is not recorded yet. *later*: the source's addresses are asked for when
    a recording starts; *max_connections*: the account's limit, when known;
    *name_of*: a channel's name as the lists show it; *extra_of*: what a job
    keeps of its channel's source (RecordingManager.add_job's *extra*)."""
    now = time.time() if now is None else now
    out = Scheduled()
    box = list(getattr(found, "box", None) or [])
    # Recordings a rule scheduled that the box records itself now (its
    # AutoTimer took them on later): off the list, and out of the rules'
    # memory - should the box stop, they are scheduled here again.
    out.to_box = series_rules.left_to_box(rec.jobs, box, now)
    for j in out.to_box:
        rec.cancel(j["id"])
        rec.remove_job(j["id"])
    if out.to_box:
        store.forget(out.to_box)
        log.info("[series] %d recording(s) left to the box, which records them itself",
                 len(out.to_box))
    rules = store.rules()
    ids = series_rules.of_action(rules, series_rules.RECORD)
    # A rule deleted meanwhile asks for nothing; a channel without an
    # address cannot be recorded (one whose address is asked for when the
    # recording starts has none yet).
    found = [a for a in found if a.rule in ids and (a.url or later)]
    before, after = series_rules.padding(settings)
    for job, a in series_rules.retimed(found, rec.jobs, now):
        start, stop = series_rules.padded(a.start, a.stop, before, after, now)
        log.info("[series] %s moved in the guide - recording it at its new time",
                 for_log(a.title))
        rec.update_job_times(job["id"], start, stop,
                             extra={"prog_start": a.start, "prog_stop": a.stop})
        out.moved.append(series_rules.Plan(a, start, stop))
    # A keyword on any channel records so many at one time, and no rule
    # more at one time than the account has connections.
    wide = {r["id"] for r in rules if r.get("scope") == series_rules.ANY
            and r.get("kind") == series_rules.WORD}
    left_out: list = []
    plans = series_rules.plan(
        found, rec.jobs, series_rules.taken_for(store.taken(), ids, legacy=True),
        now, before, after, wide=wide, connections=max_connections, left_out=left_out,
        box=box)
    out.boxed = [a for a, why in left_out if why == series_rules.BOX]
    out.left_out = [(a, why) for a, why in left_out if why != series_rules.BOX]
    out.planned = len(plans)
    if out.left_out:
        at_once = sum(1 for _a, why in out.left_out if why == series_rules.AT_ONCE)
        log.info("[series] %d airing(s) not scheduled: %d past %d at one time "
                 "for a keyword rule, %d past the account's %s connection(s)",
                 len(out.left_out), at_once, series_rules.MAX_AT_ONCE,
                 len(out.left_out) - at_once, max_connections or "?")
    if plans and not rec.recorder()[1] and getattr(rec, "open_shared", None) is None:
        log.info("[series] %d airing(s) to record, but neither ffmpeg nor "
                 "mpv is there", len(plans))
        out.blocked = NO_RECORDER
    elif plans and rec.total_cap_exceeded():
        log.info("[series] %d airing(s) to record, but the recordings "
                 "folder is at its size limit", len(plans))
        out.blocked = FOLDER_FULL
    if not plans or out.blocked:
        store.remember(out.moved, now)
        return out
    for p in plans:
        a = p.airing
        extra = {"rule": a.rule, "stream_key": a.key,
                 "prog_start": a.start, "prog_stop": a.stop}
        extra.update(extra_of(a.channel))
        rec.add_job(
            "" if later else a.url, a.title, p.start, p.stop,
            icon=a.channel.get("stream_icon"), channel=name_of(a.channel),
            extra=extra)
    out.added = list(plans)
    store.remember(out.moved + plans, now)
    log.info("[series] %d recording(s) scheduled", len(plans))
    return out
