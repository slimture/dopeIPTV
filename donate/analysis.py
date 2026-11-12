"""analysis.py - the visits behind the admin page's Analysis tab, a row each.

The tab does its own sums in the browser (admin/analysis.js): any
breakdown, split, measure and filter is a click away without asking the
server again. This only reads the visits kept in detail of a span
(stats_collect.py: STATS_VISITS_DAYS) and writes each as one flat row -
likely robots included and marked, so the tab can leave them out or not.

    GET /admin/rows?days=N   {"fields": [...], "rows": [[...], ...]}
    POST /admin/pick         {"keys": [[day, start, id], ...]}: those visits
                             in full, for the visitor log
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta

FIELDS = ("day", "start", "hour", "wd", "id", "country", "channel", "site", "source", "os", "osv",
          "browser", "bv", "device", "screen", "lang", "blang", "pages", "paths", "time",
          "dl", "files", "donate", "donated", "ret", "kind", "acts")


def _days(stats_dir: str, first: str) -> list:
    try:
        names = os.listdir(os.path.join(stats_dir, "visits"))
    except OSError:
        return []
    return sorted(n[:10] for n in names if n.endswith(".json") and len(n) == 15 and n[:10] >= first)


def _visits(stats_dir: str, day: str) -> list:
    try:
        with open(os.path.join(stats_dir, "visits", day + ".json"), encoding="utf-8") as fh:
            got = json.load(fh)
        return got if isinstance(got, list) else []
    except (OSError, ValueError):
        return []


def row(v: dict, day: str) -> list:
    """One visit as FIELDS says - in the terms the overview's lists use."""
    from stats_collect import _family, platform_of, screen_of
    when = datetime.fromtimestamp(v.get("start") or 0).astimezone()
    lang = v.get("lang") or ""
    blang = (v.get("blang") or "").split("-")[0].lower()
    size = v.get("screen") or ""
    downloads = v.get("downloads") or []
    events = {e[1] for e in v.get("events") or []}
    paths = []
    for p in v.get("pages") or []:
        if p[1] not in paths:
            paths.append(p[1])
    browser = v.get("browser") or "Other"
    return [day, v.get("start") or 0, when.hour, when.weekday(), v.get("id") or "",
            v.get("country") or "unknown",
            v.get("channel") or ("Straight to a download" if downloads and not v.get("pages") else "Direct or unknown"),
            v.get("ref") or "", v.get("source") or "",
            _family(v.get("os") or ""), v.get("os") or "Other",
            browser, f"{browser} {v.get('bv') or ''}".strip(),
            v.get("device") or "", screen_of(*size.split("×"))[1] if size.count("×") == 1 else "",
            lang if re.fullmatch(r"[a-z]{2,3}", lang) else "en",
            blang if re.fullmatch(r"[a-z]{2,3}", blang) else "",
            len(v.get("pages") or []), paths, v.get("time") or 0,
            sorted({platform_of(d[1]) for d in downloads}), [d[1] for d in downloads][:5],
            int("donate" in events), int("donated" in events), v.get("returning") or 0,
            v.get("kind") or "unknown", [a[1:] for a in v.get("acts") or []]]


def rows(stats_dir: str, days: int) -> dict:
    """The kept visits of the last *days* days (0: all kept), as rows."""
    today = datetime.now().astimezone().date()
    first = (today - timedelta(days=days - 1)).isoformat() if days > 0 else ""
    out = []
    for day in _days(stats_dir, first):
        out += [row(v, day) for v in _visits(stats_dir, day) if v.get("pages") or v.get("downloads")]
    return {"fields": list(FIELDS), "rows": out, "first": first, "last": today.isoformat()}


PICK_MAX = 1000             # the visitor log draws no more at once


def pick(stats_dir: str, keys: list) -> dict:
    """The visits *keys* names ([day, start, id] each, as rows gave them),
    in full and newest first - a group of the Analysis tab, one by one."""
    want: dict = {}
    for k in keys[:PICK_MAX] if isinstance(keys, list) else ():
        if (isinstance(k, list) and len(k) == 3 and isinstance(k[0], str)
                and re.fullmatch(r"\d{4}-\d\d-\d\d", k[0]) and isinstance(k[1], int)):
            want.setdefault(k[0], set()).add((k[1], str(k[2])))
    out = []
    for day, which in want.items():
        out += [dict(v, day=day) for v in _visits(stats_dir, day)
                if (v.get("start"), v.get("id")) in which]
    out.sort(key=lambda v: -v.get("start", 0))
    return {"visits": out}


def help_questions(site: str) -> dict:
    """The help's questions in English by their ids (render.jq: section-topic),
    so the tab names the one opened rather than its id."""
    try:
        with open(os.path.join(site, "site", "help.json"), encoding="utf-8") as fh:
            sections = json.load(fh)
        with open(os.path.join(site, "site", "lang", "en.json"), encoding="utf-8") as fh:
            en = json.load(fh)
    except (OSError, ValueError):
        return {}
    out = {}
    for sec, topics in sections.items():
        for tp in topics:
            q = en.get(f"help_{sec}_{tp}_q")
            if isinstance(q, str):
                out[f"{sec}-{tp}"] = q
    return out
