"""admin.py - the data behind the admin page (/admin/), served by donate.py.

nginx lets only a signed-in browser through to /admin/ (auth_request) and
donate.py checks the signed session cookie again itself, so a missing nginx
line cannot open the page to everyone - and a header sent straight to the
service's socket (X-Admin-User) proves nothing once the first admin exists.

    GET /admin/            the page (admin/index.html, admin.css, admin.js)
    GET /admin/data?days=N the numbers for the last N days (7, 30, 90, 365;
                           0 for everything kept)
    GET /admin/visits?day=YYYY-MM-DD&span=day|week|month|year
                                       the visits of that day, week, month or year,
                                       and the app's own updates among them
                                       (type "update" - never a visit)
    GET /admin/visitor?id=...          every visit kept of one visitor - of
                                       one day: nothing kept ties two days
                                       of a visitor together
    GET /admin/find?by=country&value=SE&days=30   the kept visits one row of
                                       the overview's lists counted

Visitors come from stats_collect.py's STATS_DIR/stats.json, without what
the visits judged likely robots added (kept apart there; how many different
people and robots in the days' sketches, kept in memory beside the month
keys - stats_collect.sketches_now); the all-time
download counts from the site's downloads.json; tips and monthly support
from Stripe itself - the succeeded payments on dopeIPTV's own account, found
with Stripe's search and kept for five minutes. Donations taken before the
move to that account (on Solecraft AB's) are not in it.
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
    "/admin/analysis.js": ("analysis.js", "application/javascript; charset=utf-8"),
    "/admin/account.js": ("account.js", "application/javascript; charset=utf-8"),
    "/admin/builds.js": ("builds.js", "application/javascript; charset=utf-8"),
    "/admin/feedback.js": ("feedback.js", "application/javascript; charset=utf-8"),
    "/admin/login": ("login.html", "text/html; charset=utf-8"),
    "/admin/login.js": ("login.js", "application/javascript; charset=utf-8"),
    "/admin/passkey.js": ("passkey.js", "application/javascript; charset=utf-8"),
    "/admin/logtools.js": ("logtools.js", "application/javascript; charset=utf-8"),
    "/admin/sw.js": ("sw.js", "application/javascript; charset=utf-8"),
    "/admin/app.webmanifest": ("app.webmanifest", "application/manifest+json"),
}


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


# -- tips and monthly support, from Stripe -----------------------------------------------------

_CACHE: dict = {"at": 0.0, "data": None}
_LOCK = threading.Lock()


def _stripe_search(key: str, query: str, api_version: str) -> list:
    out, page = [], None
    for _ in range(5):                       # up to 500 payments
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
    """Succeeded payments on dopeIPTV's own Stripe account - tips (the tip
    dialog) and monthly support (a Payment Link): [{id, t, amount, currency,
    how, who}], newest first; cached for *max_age* seconds (the page: five
    minutes; the watcher in donate.py: one). {"error"} if Stripe could not be
    asked (the page says so instead of showing zero). The account is
    dopeIPTV's alone, so every succeeded payment on it is support."""
    with _LOCK:
        if _CACHE["data"] is not None and time.time() - _CACHE["at"] < max_age:
            return _CACHE["data"]
    if not key:
        return {"error": "not configured", "list": []}
    rows = []
    try:
        for pi in _stripe_search(key, "status:'succeeded'", api_version):
            monthly = bool(pi.get("invoice")) or "subscription" in str(pi.get("description") or "").lower()
            rows.append({
                "id": str(pi.get("id") or ""),
                "t": int(pi.get("created") or 0),
                "amount": int(pi.get("amount_received") or pi.get("amount") or 0),
                "currency": (pi.get("currency") or "").upper(),
                "how": "Monthly support" if monthly else "Tip",
                "who": pi.get("receipt_email") or "",
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


def _week(day: str) -> str:
    d = date.fromisoformat(day)
    return (d - timedelta(days=d.weekday())).isoformat()


def _frozen_part(f: dict, days: list, per_day: dict):
    """Different visitors on *days* of a month whose hashes are gone, from
    its counts (stats_collect.freeze_months): the month's when the days hold
    all of it, else its whole weeks' and the rest's per day (visitors over a
    few days of a week are the days' summed). None as _union says."""
    want = set(days)
    have = set(f.get("days") or [])
    if any((per_day.get(d) or {}).get("visitors") and d not in have for d in want):
        return None
    if have <= want:
        return int(f.get("unique") or 0)
    n, covered = 0, set()
    for w, count in (f.get("weeks") or {}).items():
        wdays = {d for d in have if _week(d) == w}
        if wdays and wdays <= want:
            n += int(count or 0)
            covered |= wdays
    return n + sum((per_day.get(d) or {}).get("visitors", 0) for d in want - covered)


def _sketches() -> dict:
    """The days' sketches as the statistics keep them: beside the month
    keys in memory, never on a disk (stats_collect.sketches_file)."""
    try:
        import stats_collect
    except ImportError:
        return {}
    now = getattr(stats_collect, "sketches_now", None)
    return now() if callable(now) else {}


def _trouble() -> list:
    """What the statistics could not do as the privacy policy says: a file
    that should be gone and is not, swap that keeps what it is given
    (stats_collect.trouble) - the page says it at the top."""
    try:
        import stats_collect
    except ImportError:
        return []
    asked = getattr(stats_collect, "trouble", None)
    try:
        return asked() if callable(asked) else []
    except Exception as e:  # noqa: BLE001 - the page all the same, and the reason said
        return [{"what": "check", "path": "", "why": type(e).__name__}]


class _Fields:
    """The days' sketches (stats_collect.py, sketch.py), each read once."""

    def __init__(self, sketches: dict) -> None:
        self.sketches = sketches if isinstance(sketches, dict) else {}
        self._got: dict = {}

    def __contains__(self, day: str) -> bool:
        return isinstance(self.sketches.get(day), dict)

    def judged(self, day: str) -> bool:
        return day in self and "p" in self.sketches[day]

    def get(self, day: str, kind: str) -> int:
        import sketch
        if (day, kind) not in self._got:
            entry = self.sketches.get(day) or {}
            if kind == "n" and "n" not in entry:
                kind_read = "u"             # a day counted before visits were judged
            else:
                kind_read = kind
            self._got[(day, kind)] = sketch.decode(entry.get(kind_read))
        return self._got[(day, kind)]

    def count(self, days, kind: str) -> int:
        import sketch
        return sketch.count(sketch.union(self.get(d, kind) for d in days if d in self))


def _union(sketches, days: list, per_day: dict, frozen: dict | None = None):
    """Different visitors over these days, the likely robots left out - a
    month at a time, since a visitor's mark is its month's own (a key per
    month) - or None where a day had visitors and no sketch of them is kept:
    the page then shows the visits summed instead. A month whose sketches
    have gone is counted from its numbers (*frozen*)."""
    fields = sketches if isinstance(sketches, _Fields) else _Fields(sketches)
    frozen = frozen or {}
    by_month: dict = {}
    for d in days:
        by_month.setdefault(d[:7], []).append(d)
    total = 0
    for m, ds in by_month.items():
        if m in frozen:
            part = _frozen_part(frozen[m], ds, per_day)
            if part is None:
                return None
            total += part
            continue
        if any((per_day.get(d) or {}).get("visitors") and d not in fields for d in ds):
            return None
        total += fields.count(ds, "n")
    return total


def _judged(sketches, days: list, per_day: dict, frozen: dict | None = None) -> dict:
    """Over these days: the different visitors who showed they were people,
    those judged likely robots (and nothing else), and from which day visits
    were judged - a day counted before that was not. A month whose sketches
    are gone counts with its numbers when the days hold all of it, else as
    not judged."""
    fields = sketches if isinstance(sketches, _Fields) else _Fields(sketches)
    frozen = frozen or {}
    want = set(days)
    judged = [d for d in days if fields.judged(d)]
    n_people = fields.count(judged, "p")
    n_robots = max(0, fields.count(judged, "u") - fields.count(judged, "n"))
    for f in frozen.values():
        fj = set(f.get("judged") or [])
        if fj and fj <= want:
            judged += sorted(fj)
            n_people += int(f.get("people") or 0)
            n_robots += int(f.get("robots") or 0)
    judged_set = set(judged)
    left_out = [(per_day.get(d) or {}).get("robots") or {} for d in days]
    return {"people": n_people, "robots": n_robots,
            "views": sum(rb.get("views", 0) for rb in left_out),
            "downloads": sum(sum(((rb.get("lists") or {}).get("downloads") or {}).values()) for rb in left_out),
            "since": min(judged) if judged else None,
            "all": all(d in judged_set for d in days if (per_day.get(d) or {}).get("visitors"))}


SUMMED = ("views", "visitors", "requests", "visits", "engaged", "bounces", "returning", "timed")


def _timed(row: dict) -> int:
    """Visits whose time is known. Days counted before that was kept took
    every visit as timed, and are read so."""
    return row["timed"] if "timed" in row else row.get("visits", 0)
LISTS = ("pages", "refs", "ref_pages", "channels", "sources", "langs", "os", "os_versions", "devices", "countries",
         "browsers", "browser_versions", "screens", "downloads", "files", "events",
         "updates", "update_files", "update_from", "update_own")


def _updates(row: dict) -> int:
    """The updates the app fetched for itself that day (stats_collect.py
    _app_update) - never among the downloads."""
    return sum((row.get("updates") or {}).values())


def data(days: int, stats_dir: str, downloads_json: str, key: str, api_version: str = "") -> dict:
    """The numbers for the last *days* days (0: all of them): totals and
    the same span before it, a series per day - per week for a year, per
    month for longer - the top lists, the donations, and every calendar
    year side by side. Unique visitors are counted across the days. Likely
    robots are left out of every count, and told apart in "judged"."""
    days = days if days in RANGES else 30
    stats = _load(os.path.join(stats_dir, "stats.json"), {"days": {}})
    fields = _Fields(_sketches())
    frozen = _load(os.path.join(stats_dir, "uniques-months.json"), {})
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
        totals["updates"] = totals.get("updates", 0) + _updates(row)
        for k, total in sums.items():
            _sum_into(total, row.get(k))
    if step != "day":                           # a week's or month's different visitors
        for key_b, ds in bucket_days.items():
            u = _union(fields, ds, per_day, frozen)
            if u is not None:
                buckets[key_b]["visitors"] = u
    series = [dict(date=k, **v) for k, v in sorted(buckets.items())]
    totals["unique"] = _union(fields, range_days, per_day, frozen)

    before = None
    if days:
        before = dict.fromkeys(SUMMED, 0)
        before["downloads"] = before["updates"] = 0
        prev_days = [(first - timedelta(days=i)).isoformat() for i in range(1, days + 1)]
        for iso in prev_days:
            row = per_day.get(iso) or {}
            for k in SUMMED:
                before[k] += _timed(row) if k == "timed" else row.get(k, 0)
            before["downloads"] += sum((row.get("downloads") or {}).values())
            before["updates"] += _updates(row)
        before["unique"] = _union(fields, prev_days, per_day, frozen)

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
        y["unique"] = _union(fields, y.pop("days"), per_day, frozen)

    dl_all = _load(downloads_json, {})
    return {
        "by_channel": by_channel(stats_dir, first.isoformat(), today.isoformat()),
        "days": days,
        "first": first.isoformat(),
        "step": step,
        "updated": stats.get("updated"),
        "geo": bool(stats.get("geo")),
        "trouble": _trouble(),
        "today": {k: (per_day.get(today.isoformat()) or {}).get(k, 0) for k in ("views", "visitors")},
        "totals": totals,
        "judged": _judged(fields, range_days, per_day, frozen),
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
    the last two weeks and, before them, the days' own counts of them."""
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
    # A day whose visits are gone: its counts per way in (stats_collect.funnel_of).
    kept = set(visit_days(stats_dir))
    stats = _load(os.path.join(stats_dir, "stats.json"), {"days": {}})
    for day, row in (stats.get("days") or {}).items():
        if not (first <= day <= last) or day in kept or not isinstance(row, dict):
            continue
        for channel, f in (row.get("funnel") or {}).items():
            if not (isinstance(f, list) and len(f) == 6 and all(isinstance(n, int) for n in f)):
                continue
            r = rows.setdefault(channel or "Direct or unknown",
                                {"visits": 0, "more": 0, "time": 0, "timed": 0, "downloaded": 0, "donate": 0})
            for k, n in zip(("visits", "more", "time", "timed", "downloaded", "donate"), f, strict=True):
                r[k] += max(0, n)
    return sorted(([k, v] for k, v in rows.items()), key=lambda kv: -kv[1]["visits"])


# -- the visitor log ----------------------------------------------------------------

def _visits_file(stats_dir: str, day: str, folder: str = "visits") -> list:
    try:
        with open(os.path.join(stats_dir, folder, day + ".json"), encoding="utf-8") as fh:
            got = json.load(fh)
        return got if isinstance(got, list) else []
    except (OSError, ValueError):
        return []


def visit_days(stats_dir: str, folder: str = "visits") -> list:
    try:
        names = os.listdir(os.path.join(stats_dir, folder))
    except OSError:
        return []
    return sorted((n[:10] for n in names if n.endswith(".json") and len(n) == 15), reverse=True)


def _update_rows(stats_dir: str, day: str) -> list:
    """The app's own updates of a day (stats_collect.py: STATS_DIR/updates),
    as rows of the visitor log: type "update", their time as a start."""
    return [dict(u, day=day, type="update", start=u.get("t") or 0)
            for u in _visits_file(stats_dir, day, "updates") if isinstance(u, dict) and u.get("file")]


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


def download_counts(stats_dir: str, downloads_json: str) -> dict:
    """Downloads today, yesterday, this week (from Monday), this month and
    this year - likely robots left out, as everywhere else - with the day
    each period starts on; and the site's own counter over all time, which
    began before the statistics and counts robots too."""
    per_day = _load(os.path.join(stats_dir, "stats.json"), {"days": {}}).get("days") or {}
    today = datetime.now().astimezone().date()

    def since(first: date) -> int:
        n, d = 0, first
        while d <= today:
            row = per_day.get(d.isoformat())
            if row:
                n += sum((_net(row).get("downloads") or {}).values())
            d += timedelta(days=1)
        return n
    yday = today - timedelta(days=1)
    out = {"since": min(per_day) if per_day else today.isoformat()}
    out["today"] = {"n": since(today), "day": today.isoformat()}
    row = per_day.get(yday.isoformat())
    out["yesterday"] = {"n": sum((_net(row).get("downloads") or {}).values()) if row else 0, "day": yday.isoformat()}
    for span in ("week", "month", "year"):
        first = _span_start(today, span)
        out[span] = {"n": since(first), "day": first.isoformat()}
    dl_all = _load(downloads_json, {})
    out["all"] = int(sum(v for v in dl_all.values() if isinstance(v, (int, float)))) if isinstance(dl_all, dict) else 0
    return out


LIVE_WINDOW = 300          # seconds: a visitor seen this recently is "on the site now"


def live(stats_dir: str, downloads_json: str) -> dict:
    """What the admin page asks every few seconds: the downloads by period,
    today's visitors and page views, the visitors seen in the last five
    minutes and the newest downloads of today - likely robots left out."""
    now = time.time()
    today = datetime.now().astimezone().date()
    raw = _visits_file(stats_dir, today.isoformat())
    # Changes whenever today's visits do (a new one, a page, a download, the
    # seconds a leave adds, a visit judged anew): the page then fetches the
    # view it shows again, and not otherwise.
    rev = "{}.{}.{}.{}.{}.{}".format(len(raw), sum(len(v.get("pages") or ()) for v in raw),
                                      sum(len(v.get("downloads") or ()) for v in raw),
                                      sum(v.get("time") or 0 for v in raw), max((v.get("end", 0) for v in raw), default=0),
                                      sum(1 for v in raw if v.get("kind") == "robot"))
    vs = [v for d in (today - timedelta(days=1), today) for v in _visits_file(stats_dir, d.isoformat())
          if v.get("kind") != "robot"]
    # Who is here now is in the collector's memory, to the second; the
    # visits kept on disk say when only to the ten minutes.
    try:
        import stats_collect
        here_n = stats_collect.here_now(LIVE_WINDOW, now)
    except Exception:  # noqa: BLE001 - the kept visits instead
        here_n = None
    if here_n is None:
        here_n = len({v.get("id") for v in vs if (v.get("pages") or v.get("downloads"))
                      and now - v.get("end", 0) <= LIVE_WINDOW})
    recent = sorted(([d[0], d[1], v.get("country", "")] for v in vs for d in v.get("downloads") or ()
                     if isinstance(d, list) and len(d) > 1), reverse=True)[:5]
    stats = _load(os.path.join(stats_dir, "stats.json"), {"days": {}})
    row = _net((stats.get("days") or {}).get(today.isoformat()) or {})
    own = sum((row.get("update_own") or {}).values())
    rev += f".{_updates(row)}.{own}"            # an update makes no visit
    st = _load(os.path.join(stats_dir, "state.json"), {})
    upd = sorted((r for r in (st.get("recent_updates") or []) if isinstance(r, list) and len(r) >= 5),
                 key=lambda r: r[0] or 0, reverse=True)[:5]
    return {"at": int(now), "rev": rev, "updated": stats.get("updated"), "window": LIVE_WINDOW, "here": here_n,
            "today": {"visitors": row.get("visitors", 0), "views": row.get("views", 0), "updates": _updates(row),
                      "updates_own": own},
            "recent": recent, "updates_recent": upd, "dl": download_counts(stats_dir, downloads_json)}


def visits(stats_dir: str, day: str, span: str = "day") -> dict:
    """The visits of one day, week, month or year, newest first, one by one
    for the last STATS_VISITS_DAYS days - none is kept as a row before that
    (only the days' counts). "periods" are the starts of the spans that
    have any."""
    span = span if span in SPANS else "day"
    try:
        at = date.fromisoformat(day)
    except ValueError:
        at = datetime.now().astimezone().date()
    detailed = visit_days(stats_dir)
    kept = set(detailed)
    updated = set(visit_days(stats_dir, "updates"))
    periods = sorted({_span_start(date.fromisoformat(d), span).isoformat() for d in kept | updated},
                     reverse=True)
    start = _span_start(at, span)
    end = _span_end(start, span)
    lst: list = []
    d = start
    while d <= end:
        iso = d.isoformat()
        if iso in kept:
            lst += [dict(v, day=iso) for v in _visits_file(stats_dir, iso) if v.get("pages") or v.get("downloads")]
        if iso in updated:
            lst += _update_rows(stats_dir, iso)
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
        return (screen_of(*size.split("\u00d7"))[1] if size.count("\u00d7") == 1 else size) == value
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


# -- builds: the release and the test builds, for the Builds tab -----------------

GITHUB_RELEASES = "https://api.github.com/repos/slimture/dopeIPTV/releases?per_page=30"
NIGHTLY_TAG = re.compile(r"^nightly-\d+(?:\.\d+){3}$")
_BUILDS: dict = {"at": 0.0, "list": None, "error": ""}
_BUILDS_LOCK = threading.Lock()


def _build_asset(a: dict) -> dict | None:
    name = str(a.get("name") or "").rsplit("/", 1)[-1]
    if not name or name.startswith(".") or (a.get("state") or "uploaded") != "uploaded":
        return None
    if name.startswith("SHA256SUMS"):              # the signed list, for the app
        return None
    sha = str(a.get("sha256") or a.get("digest") or "")
    return {"name": name, "size": int(a.get("size") or 0),
            "sha256": sha.split(":", 1)[1] if sha.startswith("sha256:") else sha,
            # Through /dl.php like the site's own links: a test build's file is
            # sent on to GitHub there, and a tester's download is counted.
            "url": "/dl.php?f=" + urllib.parse.quote(name)}


def _test_builds(keep: int) -> list:
    req = urllib.request.Request(GITHUB_RELEASES, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "dopeiptv.org admin"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read() or b"[]")
    rows = [r for r in body if isinstance(r, dict) and r.get("prerelease") and not r.get("draft")
            and NIGHTLY_TAG.match(str(r.get("tag_name") or ""))]
    # The highest version first: created_at is the date of the commit a
    # release was made from, the same for two builds of one commit.
    rows.sort(key=lambda r: tuple(int(x) for x in str(r["tag_name"])[len("nightly-"):].split(".")),
              reverse=True)
    out = []
    for r in rows[:keep]:
        notes = str(r.get("body") or "")
        commit = re.search(r"\bat `?([0-9a-f]{7,40})`?", notes)
        out.append({"version": str(r["tag_name"])[len("nightly-"):],
                    "published_at": r.get("published_at") or r.get("created_at"),
                    "html_url": str(r.get("html_url") or ""),
                    "commit": commit.group(1) if commit else "",
                    "changes": [ln[2:].strip() for ln in notes.splitlines() if ln.startswith("- ")][:40],
                    "assets": [x for x in map(_build_asset, r.get("assets") or []) if x]})
    return out


def builds(site_dir: str, max_age: int = 120, keep: int = 10) -> dict:
    """The release the site offers (public/releases.json) and the newest
    *keep* test builds on GitHub, each file with a link through /dl.php;
    which test build the app's Test builds channel offers (nightly.json).
    GitHub is asked at most every *max_age* seconds; when it cannot be, the
    last list it gave stays, with the error beside it."""
    rel = _load(os.path.join(site_dir, "public", "releases.json"), {})
    release = None
    if isinstance(rel, dict) and rel.get("version"):
        release = {"version": str(rel["version"]), "published_at": rel.get("published_at"),
                   "html_url": str(rel.get("html_url") or ""),
                   "assets": [x for x in map(_build_asset, rel.get("assets") or []) if x]}
    offered = _load(os.path.join(site_dir, "public", "nightly.json"), {})
    with _BUILDS_LOCK:
        fresh = _BUILDS["list"] is not None and time.time() - _BUILDS["at"] < max_age
    if not fresh:
        try:
            got = _test_builds(keep)
            with _BUILDS_LOCK:
                _BUILDS.update(at=time.time(), list=got, error="")
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError, TypeError) as e:
            msg = getattr(e, "reason", None) or str(e)
            with _BUILDS_LOCK:
                _BUILDS.update(at=time.time(), error=f"GitHub: {msg}"[:200])
    with _BUILDS_LOCK:
        tests, error, at = _BUILDS["list"] or [], _BUILDS["error"], _BUILDS["at"]
    return {"release": release, "tests": tests, "error": error, "checked": int(at),
            "offered": str(offered.get("version") or "") if isinstance(offered, dict) else ""}
