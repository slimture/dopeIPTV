#!/usr/bin/env python3
"""stats_collect.py - the site's visitor statistics, from nginx's own logs.

No script in the page, no cookie, nothing sent anywhere: nginx already logs
every request, and this reads the new part of that log every quarter hour
(cron, see DEPLOY.md "Statistics") and keeps counts per day in
STATS_DIR/stats.json, which the admin page (donate.py, /admin/) shows.

What is counted, per day:
  views      pages viewed (/ and /help/), robots left out
  visitors   different visitors: a hash of address + browser, salted with a
             random value that is thrown away when the day is over - so a
             visitor is counted once a day and cannot be followed from one
             day to the next, and no address is ever written down
  pages, refs (sites that linked here), langs (the language the page was
  shown in), os, devices - the last three once per visitor
  downloads  per platform, and files per file, from the /dl.php log the
             site already keeps: a click sent on to the file (302), from a
             real browser - robots, scripts, download checkers and empty
             browser names left out - and, where the log names the visitor
             (its last field, the iptv_dl format), once per visitor per file
             per day, with the same salted hash as the visitors

Two logs, read from where the last run stopped (rotation by newsyslog is
followed into <log>.0):

  STATS_LOG   /var/log/nginx/iptv.dope.rs-stats.log      (JSON lines, the
              iptv_stats format in deploy/content-iptv.dope.rs.conf)
  DL_LOG      /var/log/nginx/iptv.dope.rs-downloads.log  (combined)
  STATS_DIR   <site>/stats, beside public/ - outside the web root

Once, on a fresh start (no state.json yet), `--backfill` also reads the
download log's older, rotated files (<log>.0, <log>.1.bz2 ...), so the
downloads per day go back as far as the server kept them.

Settings come from the same file as donate.py's (DONATE_CONFIG). Standard
library only.
"""
from __future__ import annotations

import bz2
import glob
import gzip
import hashlib
import json
import lzma
import os
import re
import secrets
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from donate import CONF  # the shared settings file

SITE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # /usr/local/www/iptv
STATS_DIR = CONF.get("STATS_DIR", os.path.join(SITE, "stats"))
STATS_LOG = CONF.get("STATS_LOG", "/var/log/nginx/iptv.dope.rs-stats.log")
DL_LOG = CONF.get("DL_LOG", "/var/log/nginx/iptv.dope.rs-downloads.log")
SITE_HOST = CONF.get("SITE_HOST", "iptv.dope.rs")
KEEP_DAYS = 3660          # ten years of days, for the admin page's years
KEEP_PER_DAY = 40         # a day's longest lists (sites, files): the top 40, the rest summed

PAGES = {"/": "/", "/help/": "/help/"}
BOTS = re.compile(r"bot|crawl|spider|slurp|curl|wget|python|httpclient|go-http|java/|okhttp|"
                  r"facebookexternalhit|embedly|preview|monitor|uptime|headless|scrapy|"
                  r"lighthouse|pingdom|feedfetcher|validator|scanner|semrush|ahrefs|"
                  r"bingpreview|whatsapp|telegram|discord|slack|skype", re.IGNORECASE)
# Combined, plus the visitor's address as a last field in the iptv_dl format
# (older lines have none: their first field is the TLS proxy).
DL_LINE = re.compile(r'\[(\d{2})/(\w{3})/(\d{4}):[^\]]*\] "GET /dl\.php\?f=([^ "&]+)[^"]*" (\d{3}) '
                     r'\S+ "[^"]*" "([^"]*)"(?: "([^"]*)")?')
MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def os_of(ua: str) -> str:
    if "Windows" in ua:
        return "Windows"
    if "iPhone" in ua or "iPad" in ua or "iPod" in ua:
        return "iOS"
    if "Android" in ua:
        return "Android"
    if "CrOS" in ua:
        return "ChromeOS"
    if "Macintosh" in ua or "Mac OS X" in ua:
        return "macOS"
    if "Linux" in ua or "X11" in ua:
        return "Linux"
    return "Other"


def device_of(ua: str) -> str:
    if "iPad" in ua or ("Android" in ua and "Mobile" not in ua):
        return "Tablet"
    if "Mobi" in ua or "iPhone" in ua:
        return "Mobile"
    return "Computer"


def platform_of(name: str) -> str:
    low = name.lower()
    if low.endswith((".appimage", ".deb", ".flatpak", ".rpm")):
        return "Linux"
    if low.endswith(".dmg"):
        return "macOS"
    if low.endswith((".zip", ".exe", ".msi")):
        return "Windows"
    if low.endswith((".whl", ".tar.gz")):
        return "Python"
    return "Other"


def _bump(d: dict, key: str, n: int = 1) -> None:
    d[key] = d.get(key, 0) + n


def _read_new(path: str, state: dict) -> list:
    """Lines added to *path* since the last run, following a rotation into
    <path>.0; *state* ({"ino", "off"}) is updated in place."""
    lines: list = []
    try:
        st = os.stat(path)
    except OSError:
        return lines
    ino, off = state.get("ino"), state.get("off", 0)
    if ino is not None and ino != st.st_ino:
        try:
            old = os.stat(path + ".0")
            if old.st_ino == ino:
                with open(path + ".0", "rb") as fh:
                    fh.seek(off)
                    lines += fh.read().decode("utf-8", "replace").splitlines()
        except OSError:
            pass
        off = 0
    if ino == st.st_ino and st.st_size < off:      # truncated in place
        off = 0
    with open(path, "rb") as fh:
        fh.seek(off)
        data = fh.read()
    # Only whole lines: a half-written last line waits for the next run.
    cut = data.rfind(b"\n") + 1
    lines += data[:cut].decode("utf-8", "replace").splitlines()
    state["ino"], state["off"] = st.st_ino, off + cut
    return lines


def browser(ua: str) -> bool:
    """A person's browser: every one says "Mozilla/"; robots that say it
    too name themselves, and scripts (curl, wget, python...) do not say it."""
    return ua.startswith("Mozilla/") and not BOTS.search(ua)


def _download(stats: dict, line: str, state: dict) -> None:
    m = DL_LINE.search(line)
    if not m or m.group(5) != "302" or not browser(m.group(6)):
        return
    dd, mon, yyyy, name = m.group(1), m.group(2), m.group(3), m.group(4)
    if mon not in MONTHS:
        return
    day = f"{yyyy}-{MONTHS[mon]:02d}-{dd}"
    name = name.rsplit("/", 1)[-1]
    ip = m.group(7) or ""
    if ip and ip != "-":
        salt = state["salts"].setdefault(day, secrets.token_hex(16))
        h = hashlib.sha256(f"{salt}|{ip}|{m.group(6)}|{name}".encode()).hexdigest()[:20]
        seen = state.setdefault("seen_dl", {}).setdefault(day, [])
        if h in seen:
            return
        seen.append(h)
    d = _day(stats, day)
    _bump(d["downloads"], platform_of(name))
    _bump(d.setdefault("files", {}), name[:120])


def _rotated(path: str) -> list:
    """<path>.0, <path>.1.bz2 ... oldest first."""
    found = []
    for f in glob.glob(glob.escape(path) + ".[0-9]*"):
        m = re.fullmatch(re.escape(path) + r"\.(\d+)(\.(?:bz2|gz|xz))?", f)
        if m:
            found.append((int(m.group(1)), f))
    return [f for _, f in sorted(found, reverse=True)]


def _open_any(path: str):
    if path.endswith(".bz2"):
        return bz2.open(path, "rt", encoding="utf-8", errors="replace")
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    if path.endswith(".xz"):
        return lzma.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


def _day(stats: dict, day: str) -> dict:
    return stats["days"].setdefault(day, {
        "views": 0, "visitors": 0, "pages": {}, "refs": {}, "langs": {},
        "os": {}, "devices": {}, "downloads": {}, "files": {}})


def collect(backfill: bool = False) -> None:
    os.makedirs(STATS_DIR, mode=0o750, exist_ok=True)
    stats_path = os.path.join(STATS_DIR, "stats.json")
    state_path = os.path.join(STATS_DIR, "state.json")
    if backfill and os.path.exists(state_path):
        sys.exit(f"--backfill is for a fresh start: {state_path} exists, and the old logs "
                 "would be counted twice. Remove stats.json and state.json first.")
    stats = _load(stats_path, {"days": {}})
    state = _load(state_path, {"logs": {}, "salts": {}, "seen": {}})
    salts, seen = state.setdefault("salts", {}), state.setdefault("seen", {})

    for line in _read_new(STATS_LOG, state["logs"].setdefault("stats", {})):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        ua = r.get("a") or ""
        if r.get("m") != "GET" or str(r.get("s")) not in ("200", "304") or not browser(ua):
            continue
        page = PAGES.get(urlsplit(r.get("u") or "").path)
        if page is None:
            continue
        day = (r.get("t") or "")[:10]
        if len(day) != 10:
            continue
        d = _day(stats, day)
        d["views"] += 1
        _bump(d["pages"], page)
        ref = urlsplit(r.get("r") or "").hostname or ""
        ref = ref.removeprefix("www.")
        if ref and ref != SITE_HOST:
            _bump(d["refs"], ref)
        salt = salts.setdefault(day, secrets.token_hex(16))
        h = hashlib.sha256(f"{salt}|{r.get('ip') or ''}|{ua}".encode()).hexdigest()[:20]
        today = seen.setdefault(day, [])
        if h not in today:
            today.append(h)
            d["visitors"] += 1
            _bump(d["langs"], r.get("l") or "en")
            _bump(d["os"], os_of(ua))
            _bump(d["devices"], device_of(ua))

    if backfill:
        for old in _rotated(DL_LOG):
            with _open_any(old) as fh:
                for line in fh:
                    _download(stats, line, state)
            print(f"[stats] read {old}", flush=True)
    for line in _read_new(DL_LOG, state["logs"].setdefault("downloads", {})):
        _download(stats, line, state)

    # The salts and the day's hashes only live while that day can still get
    # lines (today and yesterday, for a late rotation); then they are gone,
    # and with them any way to tie one day's visitors to another's.
    seen_dl = state.setdefault("seen_dl", {})
    days = sorted(set(salts) | set(seen) | set(seen_dl))
    for old in days[:-2]:
        salts.pop(old, None)
        seen.pop(old, None)
        seen_dl.pop(old, None)
    for old in sorted(stats["days"])[:-KEEP_DAYS]:
        stats["days"].pop(old, None)
    # A day's list of sites or files stays short, so ten years stay small -
    # a flood of made-up referrers cannot grow the file without end.
    for d in stats["days"].values():
        for k in ("refs", "files"):
            lst = d.get(k) or {}
            if len(lst) > KEEP_PER_DAY:
                top = sorted(lst.items(), key=lambda kv: -kv[1])
                d[k] = dict(top[:KEEP_PER_DAY - 1])
                d[k]["other"] = d[k].get("other", 0) + sum(v for _, v in top[KEEP_PER_DAY - 1:])
    stats["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _save(stats_path, stats)
    _save(state_path, state)


def _load(path: str, default: dict) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            got = json.load(fh)
        return got if isinstance(got, dict) else default
    except (OSError, ValueError):
        return default


def _save(path: str, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))
    os.chmod(tmp, 0o640)
    os.replace(tmp, path)


if __name__ == "__main__":
    collect(backfill="--backfill" in sys.argv[1:])
