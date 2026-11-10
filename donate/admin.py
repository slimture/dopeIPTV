"""admin.py - the data behind the admin page (/admin/), served by donate.py.

nginx asks for a password in front of /admin/ and passes the signed-in user
on as X-Admin-User; donate.py refuses any /admin/ request without it, so a
missing auth_basic line cannot open the page to everyone.

    GET /admin/            the page (admin/index.html, admin.css, admin.js)
    GET /admin/data?days=N the numbers for the last N days (7, 30, 90, 365;
                           0 for everything kept)

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
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(HERE)                  # /usr/local/www/iptv: stats/, downloads.json
STATIC = {
    "/admin/": ("index.html", "text/html; charset=utf-8"),
    "/admin/admin.css": ("admin.css", "text/css; charset=utf-8"),
    "/admin/admin.js": ("admin.js", "application/javascript; charset=utf-8"),
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


def donations(key: str, api_version: str = "") -> dict:
    """Succeeded donations through the dialog: [{t, amount, currency, how,
    who}], newest first; cached for five minutes. {"error"} if Stripe could
    not be asked (the page says so instead of showing zero)."""
    with _LOCK:
        if _CACHE["data"] is not None and time.time() - _CACHE["at"] < 300:
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


def data(days: int, stats_dir: str, downloads_json: str, key: str, api_version: str = "") -> dict:
    """The numbers for the last *days* days (0: all of them): totals and
    the same span before it, a series per day - per week for a year, per
    month for longer - the top lists, the donations, and every calendar
    year side by side."""
    days = days if days in RANGES else 30
    stats = _load(os.path.join(stats_dir, "stats.json"), {"days": {}})
    per_day = stats.get("days") or {}
    today = datetime.now(timezone.utc).date()
    if days:
        first = today - timedelta(days=days - 1)
    else:
        first = date.fromisoformat(min(per_day)) if per_day else today
    span = (today - first).days + 1
    step = "day" if span <= 120 else "week" if span <= 550 else "month"

    sums = {k: {} for k in ("pages", "refs", "langs", "os", "devices", "downloads", "files")}
    totals = {"views": 0, "visitors": 0, "downloads": 0}
    buckets: dict = {}
    for i in range(span):
        d = first + timedelta(days=i)
        row = per_day.get(d.isoformat()) or {}
        dl = sum((row.get("downloads") or {}).values())
        b = buckets.setdefault(_bucket(d, step).isoformat(), {"views": 0, "visitors": 0, "downloads": 0})
        b["views"] += row.get("views", 0)
        b["visitors"] += row.get("visitors", 0)
        b["downloads"] += dl
        totals["views"] += row.get("views", 0)
        totals["visitors"] += row.get("visitors", 0)
        totals["downloads"] += dl
        for k, total in sums.items():
            _sum_into(total, row.get(k))
    series = [dict(date=k, **v) for k, v in sorted(buckets.items())]

    before = None
    if days:
        before = {"views": 0, "visitors": 0, "downloads": 0}
        for i in range(1, days + 1):
            row = per_day.get((first - timedelta(days=i)).isoformat()) or {}
            before["views"] += row.get("views", 0)
            before["visitors"] += row.get("visitors", 0)
            before["downloads"] += sum((row.get("downloads") or {}).values())

    don = donations(key, api_version)
    since = datetime.combine(first, datetime.min.time(), timezone.utc).timestamp()
    in_range = [r for r in don.get("list", []) if r["t"] >= since]
    by_cur: dict = {}
    for r in in_range:
        by_cur[r["currency"]] = by_cur.get(r["currency"], 0) + r["amount"]
    all_cur: dict = {}
    for r in don.get("list", []):
        all_cur[r["currency"]] = all_cur.get(r["currency"], 0) + r["amount"]

    years: dict = {}
    for d, row in per_day.items():
        y = years.setdefault(d[:4], {"views": 0, "visitors": 0, "downloads": 0, "donations": 0, "sums": {}})
        y["views"] += row.get("views", 0)
        y["visitors"] += row.get("visitors", 0)
        y["downloads"] += sum((row.get("downloads") or {}).values())
    for r in don.get("list", []):
        y = years.setdefault(str(datetime.fromtimestamp(r["t"], timezone.utc).year),
                             {"views": 0, "visitors": 0, "downloads": 0, "donations": 0, "sums": {}})
        y["donations"] += 1
        y["sums"][r["currency"]] = y["sums"].get(r["currency"], 0) + r["amount"]

    dl_all = _load(downloads_json, {})
    return {
        "days": days,
        "first": first.isoformat(),
        "step": step,
        "updated": stats.get("updated"),
        "today": {k: (per_day.get(today.isoformat()) or {}).get(k, 0) for k in ("views", "visitors")},
        "totals": totals,
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
