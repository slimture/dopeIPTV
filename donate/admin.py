"""admin.py - the data behind the admin page (/admin/), served by donate.py.

nginx asks for a password in front of /admin/ and passes the signed-in user
on as X-Admin-User; donate.py refuses any /admin/ request without it, so a
missing auth_basic line cannot open the page to everyone.

    GET /admin/            the page (admin/index.html, admin.css, admin.js)
    GET /admin/data?days=N the numbers for the last N days (7, 30, 90, 365;
                           0 for everything kept)
    GET /admin/visits?day=YYYY-MM-DD   that day's visits, one by one
    GET /admin/visitor?id=...          every visit kept of one visitor

Visitors come from stats_collect.py's STATS_DIR/stats.json; the all-time
download counts from the site's downloads.json; donations from Stripe
itself - the PaymentIntents the donate dialog made (metadata source), found
with Stripe's search and kept for five minutes. Donations through the old
Payment Link page carry no such mark and are not in it.
"""
from __future__ import annotations

import json
import os
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


def _top(d: dict, n: int = 8) -> list:
    return [[k, v] for k, v in sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


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


def _union(uniques: dict, days: list, per_day: dict):
    """Different visitors over these days, or None where a day had visitors
    whose hashes are gone (kept 25 months) - the page then shows the visits
    summed instead."""
    if any((per_day.get(d) or {}).get("visitors") and d not in uniques for d in days):
        return None
    seen: set = set()
    for d in days:
        seen.update(uniques.get(d, ()))
    return len(seen)


SUMMED = ("views", "visitors", "requests", "visits", "engaged", "bounces", "returning")
LISTS = ("pages", "refs", "channels", "sources", "langs", "os", "os_versions", "devices", "countries",
         "browsers", "browser_versions", "screens", "downloads", "files", "events")


def data(days: int, stats_dir: str, downloads_json: str, key: str, api_version: str = "") -> dict:
    """The numbers for the last *days* days (0: all of them): totals and
    the same span before it, a series per day - per week for a year, per
    month for longer - the top lists, the donations, and every calendar
    year side by side. Unique visitors are counted across the days."""
    days = days if days in RANGES else 30
    stats = _load(os.path.join(stats_dir, "stats.json"), {"days": {}})
    uniques = _load(os.path.join(stats_dir, "uniques.json"), {})
    per_day = stats.get("days") or {}
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
            totals[k] += row.get(k, 0)
        totals["downloads"] = totals.get("downloads", 0) + dl
        for k, total in sums.items():
            _sum_into(total, row.get(k))
    if step != "day":                           # a week's or month's different visitors
        for key_b, ds in bucket_days.items():
            u = _union(uniques, ds, per_day)
            if u is not None:
                buckets[key_b]["visitors"] = u
    series = [dict(date=k, **v) for k, v in sorted(buckets.items())]
    totals["unique"] = _union(uniques, range_days, per_day)

    before = None
    if days:
        before = dict.fromkeys(SUMMED, 0)
        before["downloads"] = 0
        prev_days = [(first - timedelta(days=i)).isoformat() for i in range(1, days + 1)]
        for iso in prev_days:
            row = per_day.get(iso) or {}
            for k in SUMMED:
                before[k] += row.get(k, 0)
            before["downloads"] += sum((row.get("downloads") or {}).values())
        before["unique"] = _union(uniques, prev_days, per_day)

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
        y["unique"] = _union(uniques, y.pop("days"), per_day)

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
        "before": before,
        "series": series,
        "top": {k: _top(v, 12) for k, v in sums.items()},
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

    def add(channel: str, pages: int, secs: int, downloaded: bool, donate: bool) -> None:
        r = rows.setdefault(channel or "Direct or unknown",
                            {"visits": 0, "more": 0, "time": 0, "downloaded": 0, "donate": 0})
        r["visits"] += 1
        r["more"] += pages > 1
        r["time"] += secs
        r["downloaded"] += downloaded
        r["donate"] += donate
    for day in visit_days(stats_dir):
        if first <= day <= last:
            for v in _visits_file(stats_dir, day):
                if v.get("pages"):
                    add(v.get("channel", ""), len(v["pages"]), v.get("time", 0), bool(v.get("downloads")),
                        any(e[1] == "donate" for e in v.get("events") or []))
    try:
        months = sorted(n[:7] for n in os.listdir(os.path.join(stats_dir, "visits-anon")) if n.endswith(".json"))
    except OSError:
        months = []
    for m in months:
        if not (first[:7] <= m <= last[:7]):
            continue
        month = _load(os.path.join(stats_dir, "visits-anon", m + ".json"), {})
        for a in month.get("visits") or []:
            if first <= a.get("day", "") <= last and a.get("pages"):
                add(a.get("channel", ""), a["pages"], a.get("time", 0), a.get("downloaded", False),
                    a.get("donate", False))
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


def visits(stats_dir: str, day: str) -> dict:
    """One day's visits, newest first."""
    try:
        date.fromisoformat(day)
    except ValueError:
        day = datetime.now().astimezone().date().isoformat()
    lst = [v for v in _visits_file(stats_dir, day) if v.get("pages") or v.get("downloads")]
    lst.sort(key=lambda v: -v.get("start", 0))
    return {"day": day, "days": visit_days(stats_dir), "visits": lst}


def visitor(stats_dir: str, vid: str) -> dict:
    """Every visit kept of one visitor, newest first."""
    vid = "".join(c for c in vid if c in "0123456789abcdef")[:12]
    out = []
    for day in visit_days(stats_dir):
        out += [dict(v, day=day) for v in _visits_file(stats_dir, day) if v.get("id") == vid]
    out.sort(key=lambda v: -v.get("start", 0))
    return {"id": vid, "visits": out}
