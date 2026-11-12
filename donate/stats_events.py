"""stats_events.py - what a page tells /hit beside the view itself.

public/events.js sends these, each once a page, and only that it happened
and what - never who; nothing is kept in the browser. stats_collect.py
keeps them on the visit they belong to ("acts": [time, event, what, ...]),
so the admin page's Analysis tab can count them like everything else.

    q     a question of the help opened by hand        what: its id
    s     the help searched, once typing stopped       what: the words, hits
    sec   a part of the front page scrolled into view   what: its id
    how   the install steps opened from a download card what: the system
    lang  the language switched                         what: from>to
    out   a link to another site followed               what: its host

Anything else, or anything that does not look like the above, is dropped.
"""
from __future__ import annotations

import re

_FORMS = {
    "q": re.compile(r"[a-z0-9][a-z0-9-]{0,59}"),
    "sec": re.compile(r"[a-z0-9][a-z0-9-]{0,29}"),
    "how": re.compile(r"linux|macos|windows"),
    "lang": re.compile(r"[a-z]{2,3}>[a-z]{2,3}"),
    "out": re.compile(r"[a-z0-9][a-z0-9.-]{1,79}"),
}
EVENTS = (*_FORMS, "s")
MAX_PER_VISIT = 200         # a visit's own list stays small whatever a page sends


def clean(ev: str, what: str | None, hits: str | None) -> list | None:
    """[event, what] - [event, words, hits] for a search - or None."""
    if ev not in EVENTS:
        return None
    what = (what or "").strip()
    if ev == "s":
        # Words typed into the help's search: lower case, one space, letters,
        # digits and a little punctuation, forty characters at most.
        words = re.sub(r"[^\w .+#'-]", "", re.sub(r"\s+", " ", what.lower()))[:40].strip()
        if len(words) < 2:
            return None
        try:
            n = max(0, min(int(hits or ""), 999))
        except ValueError:
            n = -1
        return [ev, words, n]
    what = what.lower()[:80]
    return [ev, what] if _FORMS[ev].fullmatch(what) else None


def add(visit: dict, t: int, item: list) -> None:
    """*item* on *visit*, at time *t*: once each, a search's last words only
    when the same words came again."""
    acts = visit.setdefault("acts", [])
    if len(acts) >= MAX_PER_VISIT:
        return
    if any(a[1:3] == item[:2] for a in acts):
        return
    acts.append([t, *item])
