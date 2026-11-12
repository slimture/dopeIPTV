"""admin.py - the data behind the admin page (/admin/), served by donate.py.

nginx asks for a password in front of /admin/ and passes the signed-in user
on as X-Admin-User; donate.py refuses any /admin/ request without it, so a
missing auth_basic line cannot open the page to everyone.

    GET /admin/            the page (admin/index.html, admin.css, admin.js)
    GET /admin/data?days=N the numbers for the last N days (7, 30, 90, 365;
                           0 for everything kept)
    GET /admin/visits?day=YYYY-MM-DD&span=day|week|month|year
                                       the visits of that day, week, month or year
    GET /admin/visitor?id=...          every visit kept of one visitor
    GET /admin/find?by=country&value=SE&days=30   the kept visits one row of
                                       the overview's lists counted

Visitors come from stats_collect.py's STATS_DIR/stats.json, without what
the visits judged likely robots added (kept apart there, and which visitors
were which in kinds.json); the all-time
download counts from the site's downloads.json; donations from Stripe
itself - the PaymentIntents the donate dialog made (metadata source), found
with Stripe's search and kept for five minutes. Donations through the old
Payment Link page carry no such mark and are not in it.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(HERE)                  # /usr/local/www/iptv: stats/, downloads.json
STATIC = {
    "/admin/": ("index.html", "text/html; charset=utf-8"),
    "/admin/admin.css": ("admin.css", "text/css; charset=utf-8"),
    "/admin/admin.js": ("admin.js", "application/javascript; charset=utf-8"),
    "/admin/sw.js": ("sw.js", "application/javascript; charset=utf-8"),
    "/admin/app.webmanifest": ("app.webmanifest", "application/manifest+json"),
}
SOURCES = ("iptv.dope.rs donate dialog", "iptv.dope.rs donate dialog (wallet)")


def static(path: str):
    """(bytes, content type) for one of the page's own files, or None."""
    hit = STATIC.get(path)
    if not hit:
        return None
    with open(os.path.join(HERE, "admin", hit[0]), "rb") as fh:
        return fh.read(), hit[1]


def _load(path: str, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _sum_into(total: dict, part: dict) -> None:
    for k, v in (part or {}).items():
        if isinstance(v, (int, float)):
            total[k] = total.get(k, 0) + v


def _top(d: dict, n: int | None = None) -> list:
    """Largest first; all of them unless *n* says otherwise - the page shows
    the first dozen and the rest on request, so no country or browser is
    left out without a word."""
    rows = [[k, v] for k, v in sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))]
    return rows if n is None else rows[:n]


# -- donations, from Stripe -----------------------------------------------------

_CACHE: dict = {"at": 0.0, "data": None}
_LOCK = threading.Lock()


def _stripe_search(key: str, query: str, api_version: str) -> list:
    out, page = [], None
    for _ in range(5):                       # up to 500 donations of each kind
        params = {"query": query, "limit": "100"}
        if page:
            params["page"] = page
        req = urllib.request.Request(
            "https://api.stripe.com/v1/payment_intents/search?" + urllib.parse.urlencode(params),
            headers={"Authorization": "Bearer " + key})
        if api_version:
            req.add_header("Stripe-Version", api_version)
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read() or b"{}")
        out += body.get("data") or []
        if not body.get("has_more") or not body.get("next_page"):
            break
        page = body["next_page"]
    return out


def donations(key: str, api_version: str = "", max_age: int = 300) -> dict:
    """Succeeded donations through the dialog: [{id, t, amount, currency,
    how, who}], newest first; cached for *max_age* seconds (the page: five
    minutes; the donation watcher in donate.py: one). {"error"} if Stripe
    could not be asked (the page says so instead of showing zero)."""
    with _LOCK:
        if _CACHE["data"] is not None and time.time() - _CACHE["at"] < max_age:
            return _CACHE["data"]
    if not key:
        return {"error": "not configured", "list": []}
    rows = []
    try:
        for src in SOURCES:
            q = f"status:'succeeded' AND metadata['source']:'{src}'"
            for pi in _stripe_search(key, q, api_version):
                meta = pi.get("metadata") or {}
                rows.append({
                    "id": str(pi.get("id") or ""),
                    "t": int(pi.get("created") or 0),
                    "amount": int(pi.get("amount_received") or pi.get("amount") or 0),
                    "currency": (pi.get("currency") or "").upper(),
                    "how": "Apple Pay / Google Pay" if src.endswith("(wallet)") else "Card form",
                    "who": meta.get("name") or pi.get("receipt_email") or "",
                })
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        msg = getattr(e, "reason", None) or str(e)
        return {"error": f"Stripe: {msg}"[:200], "list": []}
    rows.sort(key=lambda r: -r["t"])
    data = {"list": rows}
    with _LOCK:
        _CACHE.update(at=time.time(), data=data)
    return data


# -- the page's numbers -----------------------------------------------------------

RANGES = (7, 30, 90, 365, 0)        # days; 0 = everything kept



def _bucket(d: date, step: str) -> date:
    if step == "week":
        return d - timedelta(days=d.weekday())
    if step == "month":
        return d.replace(day=1)
    return d


def _net(row: dict) -> dict:
    """A day's counts without what the visits judged likely robots added
    (stats_collect.py keeps that apart in "robots"; nothing is deleted)."""
    rb = row.get("robots")
    if not rb:
        return row
    out = dict(row)
    for k in ("views", "visitors", "returning"):
        out[k] = max(0, row.get(k, 0) - rb.get(k, 0))
    for k, part in (rb.get("lists") or {}).items():
        left = dict(row.get(k) or {})
        for name, n in part.items():
            if left.get(name, 0) > n:
                left[name] -= n
            else:
                left.pop(name, None)
        out[k] = left
    return out


def _union(uniques: dict, days: list, per_day: dict, kinds: dict):
    """Different visitors over these days, the likely robots left out, or
    None where a day had visitors whose hashes are gone (kept 25 months) -
    the page then shows the visits summed instead."""
    if any((per_day.get(d) or {}).get("visitors") and d not in uniques for d in days):
        return None
    seen: set = set()
    for d in days:
        robots = set((kinds.get(d) or {}).get("r", ()))
        seen.update(x for x in uniques.get(d, ()) if x not in robots)
    return len(seen)


def _judged(uniques: dict, kinds: dict, days: list, per_day: dict) -> dict:
    """Over these days: the different visitors who showed they were people,
    those judged likely robots (and nothing else), and from which day visits
    were judged - a day counted before that was not."""
    judged = [d for d in days if d in kinds]
    people: set = set()
    robots: set = set()
    others: set = set()
    for d in judged:
        r = set(kinds[d].get("r", ()))
        people.update(kinds[d].get("p", ()))
        robots.update(r)
        others.update(x for x in uniques.get(d, ()) if x not in r)
    left_out = [(per_day.get(d) or {}).get("robots") or {} for d in days]
    return {"people": len(people), "robots": len(robots - others),
            "views": sum(rb.get("views", 0) for rb in left_out),
            "downloads": sum(sum(((rb.get("lists") or {}).get("downloads") or {}).values()) for rb in left_out),
            "since": min(judged) if judged else None,
            "all": all(d in kinds for d in days if (per_day.get(d) or {}).get("visitors"))}


SUMMED = ("views", "visitors", "requests", "visits", "engaged", "bounces", "returning", "timed")


def _timed(row: dict) -> int:
    """Visits whose time is known. Days counted before that was kept took
    every visit as timed, and are read so."""
    return row["timed"] if "timed" in row else row.get("visits", 0)
LISTS = ("pages", "refs", "channels", "sources", "langs", "os", "os_versions", "devices", "countries",
         "browsers", "browser_versions", "screens", "downloads", "files", "events")


def data(days: int, stats_dir: str, downloads_json: str, key: str, api_version: str = "") -> dict:
    """The numbers for the last *days* days (0: all of them): totals and
    the same span before it, a series per day - per week for a year, per
    month for longer - the top lists, the donations, and every calendar
    year side by side. Unique visitors are counted across the days. Likely
    robots are left out of every count, and told apart in "judged"."""
    days = days if days in RANGES else 30
    stats = _load(os.path.join(stats_dir, "stats.json"), {"days": {}})
    uniques = _load(os.path.join(stats_dir, "uniques.json"), {})
    kinds = _load(os.path.join(stats_dir, "kinds.json"), {})
    per_day = {d: _net(row) for d, row in (stats.get("days") or {}).items()}
    today = datetime.now().astimezone().date()  # the server's day, as the logs' dates
    if days:
        first = today - timedelta(days=days - 1)
    else:
        first = date.fromisoformat(min(per_day)) if per_day else today
    span = (today - first).days + 1
    step = "day" if span <= 120 else "week" if span <= 550 else "month"

    sums: dict = {k: {} for k in LISTS}
    totals = dict.fromkeys(SUMMED, 0)
    buckets: dict = {}
    bucket_days: dict = {}
    range_days = []
    for i in range(span):
        d = first + timedelta(days=i)
        iso = d.isoformat()
        range_days.append(iso)
        row = per_day.get(iso) or {}
        dl = sum((row.get("downloads") or {}).values())
        key_b = _bucket(d, step).isoformat()
        b = buckets.setdefault(key_b, {"views": 0, "visitors": 0, "downloads": 0})
        bucket_days.setdefault(key_b, []).append(iso)
        b["views"] += row.get("views", 0)
        b["visitors"] += row.get("visitors", 0)
        b["downloads"] += dl
        for k in SUMMED:
            totals[k] += _timed(row) if k == "timed" else row.get(k, 0)
        totals["downloads"] = totals.get("downloads", 0) + dl
        for k, total in sums.items():
            _sum_into(total, row.get(k))
    if step != "day":                           # a week's or month's different visitors
        for key_b, ds in bucket_days.items():
            u = _union(uniques, ds, per_day, kinds)
            if u is not None:
                buckets[key_b]["visitors"] = u
    series = [dict(date=k, **v) for k, v in sorted(buckets.items())]
    totals["unique"] = _union(uniques, range_days, per_day, kinds)

    before = None
    if days:
        before = dict.fromkeys(SUMMED, 0)
        before["downloads"] = 0
        prev_days = [(first - timedelta(days=i)).isoformat() for i in range(1, days + 1)]
        for iso in prev_days:
            row = per_day.get(iso) or {}
            for k in SUMMED:
                before[k] += _timed(row) if k == "timed" else row.get(k, 0)
            before["downloads"] += sum((row.get("downloads") or {}).values())
        before["unique"] = _union(uniques, prev_days, per_day, kinds)

    don = donations(key, api_version)
    since = datetime.combine(first, datetime.min.time()).astimezone().timestamp()
    in_range = [r for r in don.get("list", []) if r["t"] >= since]
    by_cur: dict = {}
    for r in in_range:
        by_cur[r["currency"]] = by_cur.get(r["currency"], 0) + r["amount"]
    all_cur: dict = {}
    for r in don.get("list", []):
        all_cur[r["currency"]] = all_cur.get(r["currency"], 0) + r["amount"]

    years: dict = {}

    def year_row(y: str) -> dict:
        return years.setdefault(y, {"views": 0, "visitors": 0, "downloads": 0, "donations": 0,
                                    "sums": {}, "days": []})
    for d, row in per_day.items():
        y = year_row(d[:4])
        y["views"] += row.get("views", 0)
        y["visitors"] += row.get("visitors", 0)
        y["downloads"] += sum((row.get("downloads") or {}).values())
        y["days"].append(d)
    for r in don.get("list", []):
        y = year_row(str(datetime.fromtimestamp(r["t"]).astimezone().year))
        y["donations"] += 1
        y["sums"][r["currency"]] = y["sums"].get(r["currency"], 0) + r["amount"]
    for y in years.values():
        y["unique"] = _union(uniques, y.pop("days"), per_day, kinds)

    dl_all = _load(downloads_json, {})
    return {
        "by_channel": by_channel(stats_dir, first.isoformat(), today.isoformat()),
        "days": days,
        "first": first.isoformat(),
        "step": step,
        "updated": stats.get("updated"),
        "geo": bool(stats.get("geo")),
        "today": {k: (per_day.get(today.isoformat()) or {}).get(k, 0) for k in ("views", "visitors")},
        "totals": totals,
        "judged": _judged(uniques, kinds, range_days, per_day),
        "before": before,
        "series": series,
        "top": {k: _top(v) for k, v in sums.items()},
        "years": [dict(year=k, **v) for k, v in sorted(years.items(), reverse=True)],
        "downloads_all_time": int(sum(v for v in dl_all.values() if isinstance(v, (int, float))))
                              if isinstance(dl_all, dict) else 0,
        "donations": {
            "error": don.get("error"),
            "count": len(in_range),
            "sums": by_cur,
            "count_all": len(don.get("list", [])),
            "sums_all": all_cur,
            "recent": don.get("list", [])[:25],
        },
    }


# -- visits by how they came -------------------------------------------------------

def by_channel(stats_dir: str, first: str, last: str) -> list:
    """Per way in (search, social...): visits, and how many of them read more
    than one page, downloaded, opened the donate dialog - from the visits of
    the last weeks and the anonymous ones before them."""
    rows: dict = {}

    def add(channel: str, pages: int, secs: int, downloaded: bool, donate: bool, timed: bool) -> None:
        r = rows.setdefault(channel or "Direct or unknown",
                            {"visits": 0, "more": 0, "time": 0, "timed": 0, "downloaded": 0, "donate": 0})
        r["visits"] += 1
        r["more"] += pages > 1
        r["time"] += secs if timed else 0
        r["timed"] += timed
        r["downloaded"] += downloaded
        r["donate"] += donate
    for day in visit_days(stats_dir):
        if first <= day <= last:
            for v in _visits_file(stats_dir, day):
                if v.get("pages") and v.get("kind") != "robot":
                    add(v.get("channel", ""), len(v["pages"]), v.get("time", 0), bool(v.get("downloads")),
                        any(e[1] == "donate" for e in v.get("events") or []), v.get("time", 0) > 0)
    try:
        months = sorted(n[:7] for n in os.listdir(os.path.join(stats_dir, "visits-anon")) if n.endswith(".json"))
    except OSError:
        months = []
    for m in months:
        if not (first[:7] <= m <= last[:7]):
            continue
        month = _load(os.path.join(stats_dir, "visits-anon", m + ".json"), {})
        for a in month.get("visits") or []:
            if first <= a.get("day", "") <= last and a.get("pages") and a.get("kind") != "robot":
                add(a.get("channel", ""), a["pages"], a.get("time", 0), a.get("downloaded", False),
                    a.get("donate", False), a.get("timed", a.get("time", 0) > 0))
    return sorted(([k, v] for k, v in rows.items()), key=lambda kv: -kv[1]["visits"])


# -- the visitor log ----------------------------------------------------------------

def _visits_file(stats_dir: str, day: str) -> list:
    try:
        with open(os.path.join(stats_dir, "visits", day + ".json"), encoding="utf-8") as fh:
            got = json.load(fh)
        return got if isinstance(got, list) else []
    except (OSError, ValueError):
        return []


def visit_days(stats_dir: str) -> list:
    try:
        names = os.listdir(os.path.join(stats_dir, "visits"))
    except OSError:
        return []
    return sorted((n[:10] for n in names if n.endswith(".json") and len(n) == 15), reverse=True)


SPANS = ("day", "week", "month", "year")


def _span_start(d: date, span: str) -> date:
    if span == "week":
        return d - timedelta(days=d.weekday())
    if span == "month":
        return d.replace(day=1)
    if span == "year":
        return d.replace(month=1, day=1)
    return d


def _span_end(start: date, span: str) -> date:
    if span == "week":
        return start + timedelta(days=6)
    if span == "month":
        return (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    if span == "year":
        return start.replace(month=12, day=31)
    return start


def _anon_by_day(stats_dir: str) -> dict:
    """The anonymous visits (STATS_DIR/visits-anon/<month>.json), by day."""
    by_day: dict = {}
    try:
        names = sorted(n for n in os.listdir(os.path.join(stats_dir, "visits-anon")) if n.endswith(".json"))
    except OSError:
        return by_day
    for name in names:
        month = _load(os.path.join(stats_dir, "visits-anon", name), {})
        for d in month.get("days") or []:
            by_day.setdefault(d, [])
        for a in month.get("visits") or []:
            if a.get("day"):
                by_day.setdefault(a["day"], []).append(dict(a, anon=True))
    return by_day


def visits(stats_dir: str, day: str, span: str = "day") -> dict:
    """The visits of one day, week, month or year, newest first: one by one
    for the last STATS_VISITS_DAYS days, and before that as they were made
    anonymous (no time of day, no visitor; marked anon). "periods" are the
    starts of the spans that have any."""
    span = span if span in SPANS else "day"
    try:
        at = date.fromisoformat(day)
    except ValueError:
        at = datetime.now().astimezone().date()
    detailed = visit_days(stats_dir)
    kept = set(detailed)
    anon = _anon_by_day(stats_dir)
    periods = sorted({_span_start(date.fromisoformat(d), span).isoformat() for d in kept | set(anon)},
                     reverse=True)
    start = _span_start(at, span)
    end = _span_end(start, span)
    lst: list = []
    d = start
    while d <= end:
        iso = d.isoformat()
        if iso in kept:
            lst += [dict(v, day=iso) for v in _visits_file(stats_dir, iso) if v.get("pages") or v.get("downloads")]
        else:
            lst += [a for a in anon.get(iso, []) if a.get("pages") or a.get("downloaded")]
        d += timedelta(days=1)
    lst.sort(key=lambda v: (v.get("day", ""), v.get("start", 0)), reverse=True)
    return {"span": span, "day": start.isoformat(), "end": end.isoformat(), "periods": periods,
            "days": detailed, "visits": lst}


def visitor(stats_dir: str, vid: str) -> dict:
    """Every visit kept of one visitor, newest first."""
    vid = "".join(c for c in vid if c in "0123456789abcdef")[:12]
    out = []
    for day in visit_days(stats_dir):
        out += [dict(v, day=day) for v in _visits_file(stats_dir, day) if v.get("id") == vid]
    out.sort(key=lambda v: -v.get("start", 0))
    return {"id": vid, "visits": out}


# -- the visitor log by one row of the overview --------------------------------------

# Each of the overview's lists, and what of a visit it counted. A visitor's
# country, browser and the rest were counted on a page they opened, so only
# visits with a page match those; downloads match by what was downloaded.
FIND_BY = ("country", "channel", "ref", "source", "page", "lang", "os_version", "browser",
           "browser_version", "device", "screen", "download", "file")
_PLAIN = {"ref": "ref", "source": "source", "os_version": "os", "browser": "browser", "device": "device"}


def _counted_under(v: dict, by: str, value: str) -> bool:
    from stats_collect import platform_of, screen_of
    if by == "download":
        return any(platform_of(d[1]) == value for d in v.get("downloads") or ())
    if by == "file":
        return any(d[1] == value for d in v.get("downloads") or ())
    if not v.get("pages"):
        return False
    if by == "page":
        return any(p[1] == value for p in v["pages"])
    if by == "country":
        return (v.get("country") or "unknown") == value
    if by == "channel":
        return (v.get("channel") or "Direct or unknown") == value
    if by == "lang":
        lang = v.get("lang") or ""
        return (lang if re.fullmatch(r"[a-z]{2,3}", lang) else "en") == value
    if by == "browser_version":
        return f"{v.get('browser') or ''} {v.get('bv') or ''}".strip() == value
    if by == "screen":
        size = v.get("screen") or ""
        return size.count("\u00d7") == 1 and screen_of(*size.split("\u00d7"))[1] == value
    return (v.get(_PLAIN[by]) or "") == value


def find(stats_dir: str, by: str, value: str, days: int) -> dict:
    """The kept visits of the last *days* days (0: all kept), newest first,
    that the overview's list *by* counted under *value* - likely robots
    included and marked, as in the day's log."""
    out: list = []
    if by in FIND_BY and value:
        today = datetime.now().astimezone().date()
        first = (today - timedelta(days=days - 1)).isoformat() if days > 0 else ""
        for day in visit_days(stats_dir):
            if day < first:
                continue
            out += [dict(v, day=day) for v in _visits_file(stats_dir, day)
                    if (v.get("pages") or v.get("downloads")) and _counted_under(v, by, value)]
        out.sort(key=lambda v: -v.get("start", 0))
    return {"by": by, "value": value, "visits": out}
