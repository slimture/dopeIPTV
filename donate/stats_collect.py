#!/usr/bin/env python3
"""stats_collect.py - the site's visitor statistics, from nginx's own logs.

Run every quarter hour by cron, and by the admin page (donate.py, /admin/)
whenever it is opened, so the numbers there are never more than a moment
old. Counts per day go to STATS_DIR/stats.json.

What counts as a visitor: a page that opened in a real browser. The page
sends one request, /hit, once it is shown (nav.js; no cookie, no id, nothing
kept in the browser) - robots that fetch pages without running them, link
previews and pages only prerendered never send it. Every page request is
counted too, robots and all ("requests"), so the admin page can show how
much was left out.

Some robots do run pages, in a real browser engine. Each visit is judged,
cautiously - a person must never be counted away:
  person   moved the pointer, touched, scrolled or typed (nav.js says once
           that it happened, nothing more); or, with no robot sign, looked
           5 s or more, read more than one page, downloaded after a page or
           came back on another day
  robot    a browser without a screen (headless Chrome names itself), or
           Chrome, Edge or Firefox more than two years (26 versions) behind
           the newest that people bring - they update themselves, and a
           fixed, never-updated browser name is what scrapers send. Never on
           a system that cannot run the newest (Windows 7 and 8, macOS before
           10.15, Android before 10, iPhones, Chromebooks, TVs), and never
           Safari, which follows the system.
  unknown  everything else: a short visit without a move. Counted.
A visit judged a robot is kept, marked, and kept apart from the counts
(stats.json's "robots" per day, which the admin page leaves out); nothing is
deleted. No address lookups, no fingerprints.

Per day:
  views      pages shown (/hit)
  visitors   different visitors that day; per week, month or any span the
             admin page counts them across days (uniques.json)
  requests   page requests of every kind, robots included
  pages, langs, os, devices, countries - countries from DB-IP's free
             database, looked up here (mmdb.py; fetched once a month)
  channels   how a visitor found the site that day: search, social, code
             sites (GitHub...), other sites, or direct/unknown - from the
             page that linked here; refs are those sites, sources the
             ?ref= / ?utm_source= a link carried
  downloads  per platform and per file, from the /dl.php log: a click sent
             on to the file (302) from a real browser, once per visitor per
             file a day

Each visit, one by one, goes to STATS_DIR/visits/<day>.json for the admin
page's visitor log: when, country, browser and version, system, device and
screen, languages, how the visitor came, the pages and how long each was
looked at, downloads, the donate dialog - and whether the visitor was here
before. A visit ends after 30 minutes without a page. After
STATS_VISITS_DAYS days (400, thirteen months: a year against the year
before) a visit is made anonymous rather than deleted:
it moves to STATS_DIR/visits-anon/<month>.json with its day, country,
browser and system without versions, device, page language, how the
visitor came, the number of pages, the time rounded to ten seconds, and
whether there was a download or the donate dialog - no id, no time of day,
no screen, mixed in the month so their order tells nothing. Those, and the
counts per day, are kept for good.

Each update the app fetched for itself goes to STATS_DIR/updates/<day>.json
for the same log: when, the file, the version it came from, the system and
processor the app named, the country, and whether from your own network -
no address. Never a visit, never counted as one; after STATS_VISITS_DAYS
the day's file goes (the counts per day stay).

A visitor is a hash of address and browser with a secret salt kept on this
server: no address is written down, and the hashes are kept 25 months only
to count unique and returning visitors (a year against the year before);
kinds.json, which of a day's visitors were people and which robots, keeps
the same hashes as long. Robots that say so, scripts and empty browser
names are left out everywhere.

Your own visits are left out three ways: a browser that has opened the
admin page carries a cookie nginx does not log (deploy/content-...conf); an
address that is not a public one (your own network, 192.168.x and the like)
never counts; and STATS_IGNORE lists more - addresses, networks or host
names (a dynamic-DNS name is looked up on every run), e.g.
STATS_IGNORE=203.0.113.5, 2001:db8:1::/48, home.example.net

Logs, read from where the last run stopped (rotation by newsyslog is
followed into <log>.0):

  STATS_LOG   /var/log/nginx/iptv.dope.rs-stats.log      (JSON lines, the
              iptv_stats format in deploy/content-iptv.dope.rs.conf)
  DL_LOG      /var/log/nginx/iptv.dope.rs-downloads.log  (iptv_dl)
  STATS_DIR   <site>/stats, beside public/ - outside the web root
  GEO_DB      STATS_DIR/dbip-country-lite.mmdb

Once, on a fresh start (no state.json yet), `--backfill` also reads the
download log's older, rotated files (<log>.0, <log>.1.bz2 ...), so the
downloads per day go back as far as the server kept them.

Settings come from the same file as donate.py's (DONATE_CONFIG). Standard
library only.
"""
from __future__ import annotations

import bz2
import contextlib
import fcntl
import glob
import gzip
import hashlib
import ipaddress
import json
import lzma
import os
import re
import secrets
import socket
import sys
import tempfile
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from donate import CONF  # the shared settings file
import stats_events  # what a page tells beside the view: help, parts read, links

SITE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # /usr/local/www/iptv
STATS_DIR = CONF.get("STATS_DIR", os.path.join(SITE, "stats"))
STATS_LOG = CONF.get("STATS_LOG", "/var/log/nginx/iptv.dope.rs-stats.log")
DL_LOG = CONF.get("DL_LOG", "/var/log/nginx/iptv.dope.rs-downloads.log")
# The site's own names: a referrer from any of them is no referrer. The old
# iptv.dope.rs only sends visitors on, but a page opened there before the move
# can still report a view.
SITE_HOSTS = {h.strip().lower() for h in CONF.get(
    "SITE_HOST", "dopeiptv.org,www.dopeiptv.org,iptv.dope.rs").split(",") if h.strip()}
GEO_DB = CONF.get("GEO_DB", os.path.join(STATS_DIR, "dbip-country-lite.mmdb"))
GEO_URL = "https://download.db-ip.com/free/dbip-country-lite-{month}.mmdb.gz"
KEEP_UNIQUE_DAYS = 761    # the visitor hashes: 25 months, a year against the year before
KEEP_PER_DAY = 40         # a day's longest lists (sites, files): the top 40, the rest summed
VISITS_DIR = os.path.join(STATS_DIR, "visits")
ANON_DIR = os.path.join(STATS_DIR, "visits-anon")
UPDATES_DIR = os.path.join(STATS_DIR, "updates")
try:
    VISITS_DAYS = max(1, int(CONF.get("STATS_VISITS_DAYS", "400")))
except ValueError:
    VISITS_DAYS = 400
VISIT_GAP = 30 * 60       # a visit ends after half an hour without a page

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


def _ignored_nets() -> list:
    nets = []
    for item in re.split(r"[,\s]+", CONF.get("STATS_IGNORE", "")):
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
            continue
        except ValueError:
            pass
        try:                                    # a host name: what it is now
            for info in socket.getaddrinfo(item, None):
                nets.append(ipaddress.ip_network(info[4][0]))
        except OSError as e:
            print(f"[stats] STATS_IGNORE: {item} not found ({e})", flush=True)
    return nets


IGNORED: list = []


def counted(addr: str) -> bool:
    """A visitor's address that counts: a public one, not in STATS_IGNORE.
    An unknown one (old log lines) counts."""
    if not addr or addr == "-":
        return True
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return True
    if not ip.is_global:
        return False
    return not any(ip.version == n.version and ip in n for n in IGNORED)


# How a visitor found the site, by the site that linked here.
SEARCH = re.compile(r"(^|\.)(google\.[a-z.]+|bing\.com|duckduckgo\.com|yahoo\.[a-z.]+|yandex\.[a-z.]+|"
                    r"baidu\.com|ecosia\.org|startpage\.com|search\.brave\.com|qwant\.com|kagi\.com|"
                    r"naver\.com|seznam\.cz|sogou\.com|ask\.com|aol\.com|perplexity\.ai|"
                    r"chatgpt\.com|googlequicksearchbox)$")
SOCIAL = re.compile(r"(^|\.)(reddit\.com|redd\.it|x\.com|twitter\.com|t\.co|facebook\.com|fb\.com|"
                    r"instagram\.com|youtube\.com|youtu\.be|linkedin\.com|lnkd\.in|"
                    r"news\.ycombinator\.com|t\.me|telegram\.org|discord\.com|discord\.gg|vk\.com|"
                    r"threads\.net|bsky\.app|tiktok\.com|pinterest\.[a-z.]+|quora\.com|lemmy\.[a-z.]+|"
                    r"mastodon\.[a-z.]+|mstdn\.[a-z.]+|org\.telegram\.messenger|com\.reddit\.frontpage)$")
CODE = re.compile(r"(^|\.)(github\.com|gitlab\.com|codeberg\.org|sourceforge\.net|flathub\.org|"
                  r"alternativeto\.net|launchpad\.net|git\.bastion\.nu)$")


def channel_of(host: str, source: str = "") -> str:
    if not host:
        # No referrer - a desktop app, GitHub's sidebar, a chat app, a
        # bookmark. Our own links say where they are with ?ref= (the app's
        # app-about / app-update, the README's github, ...): MAINTAINING.md.
        if source.startswith("app") or source == "windows-readme":
            return "The dopeIPTV app"
        if source.startswith("github") or source == "release":
            return "GitHub and code sites"
        return "Direct or unknown"
    if SEARCH.search(host):
        return "Search"
    if SOCIAL.search(host):
        return "Social and forums"
    if CODE.search(host):
        return "GitHub and code sites"
    return "Other sites"


def referrer_host(ref: str) -> str:
    """The site that linked here: a web address's host, or an Android app's
    name (android-app://com.google.android.googlequicksearchbox/)."""
    if not ref:
        return ""
    parts = urlsplit(ref)
    host = (parts.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    return host if parts.scheme in ("http", "https", "android-app") else ""


def referrer_page(ref: str, host: str) -> str:
    """The page that linked here, when the linking site sent more than its
    own address (many send only that - Google, Facebook and X always):
    host and path, without the query or fragment, which can carry what
    points to a person (a search, a session, a click id). "" when there is
    no page, and for a search engine, whose page says nothing more."""
    if not host or SEARCH.search(host):
        return ""
    parts = urlsplit(ref)
    if parts.scheme not in ("http", "https"):
        return ""
    path = re.sub(r"/+", "/", parts.path or "").rstrip("/")
    return (host + path)[:200] if path else ""


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


BROWSERS = [(name, re.compile(rx)) for name, rx in (
    ("Edge", r"Edg(?:e|A|iOS)?/(\d+)"),
    ("Opera", r"(?:OPR|Opera|OPT)/(\d+)"),
    ("Samsung Internet", r"SamsungBrowser/(\d+)"),
    ("Yandex", r"YaBrowser/(\d+)"),
    ("Vivaldi", r"Vivaldi/(\d+)"),
    ("Firefox", r"(?:Firefox|FxiOS)/(\d+)"),
    ("Chrome", r"(?:Chrome|CriOS)/(\d+)"),
    ("Safari", r"Version/(\d+)[\d.]* (?:Mobile/\S+ )?Safari/"),
)]


def browser_of(ua: str) -> tuple:
    """("Firefox", "132"). Brave and some others say they are Chrome."""
    for name, rx in BROWSERS:
        m = rx.search(ua)
        if m:
            return name, m.group(1)
    return "Other", ""


def os_version_of(ua: str) -> str:
    """The system with what version the browser tells: Windows 10 and 11 say
    the same, and macOS has said 10.15 for years, so those stay vague."""
    m = re.search(r"Windows NT (\d+\.\d+)", ua)
    if m:
        return {"10.0": "Windows 10/11", "6.3": "Windows 8.1", "6.2": "Windows 8",
                "6.1": "Windows 7"}.get(m.group(1), "Windows")
    m = re.search(r"(?:iPhone|CPU) OS (\d+)", ua)
    if m:
        major = m.group(1)
        # From iOS 26 Safari says 18 for every newer system, and its own
        # version - the system's - is the one to go by.
        s = re.search(r"Version/(\d+)[\d.]* (?:Mobile/\S+ )?Safari/", ua)
        if major == "18" and s and int(s.group(1)) >= 26:
            major = s.group(1)
        return ("iPadOS " if "iPad" in ua else "iOS ") + major
    m = re.search(r"Android (\d+)", ua)
    if m:
        return "Android " + m.group(1)
    if "CrOS" in ua:
        return "ChromeOS"
    if "Macintosh" in ua or "Mac OS X" in ua:
        # Browsers say 10.15 for every macOS since; an older one is told.
        m = re.search(r"Mac OS X 10[._](\d+)", ua)
        return f"macOS 10.{m.group(1)}" if m and int(m.group(1)) < 15 else "macOS"
    for distro in ("Ubuntu", "Fedora", "Debian", "Mint", "Arch", "openSUSE", "Manjaro"):
        if distro in ua:
            return "Linux (" + distro + ")"
    if "Linux" in ua or "X11" in ua:
        return "Linux"
    return "Other"


def screen_of(w: str, h: str) -> tuple:
    """("1920×1080", "Large computer screen") from the page's screen.width
    and screen.height - CSS pixels, as the page sees them."""
    try:
        wi, hi = int(w), int(h)
    except ValueError:
        return "", ""
    if not (100 <= wi <= 10000 and 100 <= hi <= 10000):
        return "", ""
    short = min(wi, hi)
    kind = ("Phone" if short < 600 else "Tablet" if short < 900 else
            "Laptop" if wi < 1600 else "Large screen" if wi < 2560 else "Very large screen")
    return f"{wi}×{hi}", kind


# TVs, sticks and game consoles: their browsers are as old as the set's
# software, and an IPTV app's visitors may well read the site on one. Fire
# TV sticks name themselves by model (AFTMM, AFTKA...).
TV = re.compile(r"smart-?tv|tizen|web0s|webos|netcast|hbbtv|bravia|crkey|playstation|xbox|nintendo|"
                r"android tv|googletv|roku|viera|aquos|philipstv|vidaa", re.IGNORECASE)
FIRE_TV = re.compile(r"\bAFT[A-Z]")


def device_of(ua: str) -> str:
    if TV.search(ua) or FIRE_TV.search(ua):
        return "TV"
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


def _hash(state: dict, *parts: str) -> str:
    secret = state.setdefault("secret", secrets.token_hex(32))
    return hashlib.sha256("|".join((secret, *parts)).encode()).hexdigest()[:12]


#: The app updating itself (dopeiptv/core/updates.py user_agent):
#: "dopeIPTV/1.4.4 (Windows; AMD64)" - the version it updates from.
APP_UA = re.compile(r"^dopeIPTV/(\d+(?:\.\d+){1,3})\b")
# A test build's file: a four-part version (dopeIPTV-1.4.5.78-x86_64.AppImage),
# as the content server's $iptv_dl_nightly map tells it.
TEST_BUILD = re.compile(r"^dopeiptv[-_]\d+(?:\.\d+){3}[-_.]", re.I)


def update_channel(name: str) -> str:
    """"test" for a test build's file, "release" otherwise."""
    return "test" if TEST_BUILD.match(name or "") else "release"


# The newest updates, for the admin page's live line (admin.live): when,
# which file, from which version, the country, and whether from your own
# network - no address. The last few only, in state.json.
RECENT_UPDATES = 30


#: "(Windows; AMD64)" in the app's user agent: the system and the processor.
APP_SYSTEM = re.compile(r"\(([^;()]{1,40});\s*([^;()]{0,40})\)")


def _update_row(t: int, name: str, from_version: str, country: str, own: bool, ua: str = "") -> dict:
    """An update as the visitor log shows it (admin.py visits)."""
    m = APP_SYSTEM.search(ua or "")
    return {"t": t, "file": name[:120], "from": from_version[:20], "os": platform_of(name),
            "cpu": (m.group(2).strip()[:20] if m else ""), "country": country, "own": 1 if own else 0}


def _app_update(stats: dict, day: str, name: str, ua: str, ip: str, state: dict,
                own: bool = False, t: int = 0, db=None, visits: Visits | None = None) -> None:
    """An update the app fetched for itself: counted apart from downloads
    (a browser's), by platform, by file (the version it goes to) and by the
    version it comes from - once per address, file and day, and never a
    visit: no page was seen. One from your own network (*own*: an address
    that is not counted) is kept apart, release or test build, so a test
    of the update shows without being counted."""
    m = APP_UA.match(ua)
    if not m:
        return
    if ip and ip != "-":
        h = _hash(state, "update", ip, name)
        seen = state.setdefault("seen_dl", {}).setdefault(day, [])
        if h in seen:
            return
        seen.append(h)
    country = (db.country(ip) if db is not None and ip and ip != "-" else "") or ""
    recent = state.setdefault("recent_updates", [])
    recent.append([t, name[:120], m.group(1)[:20], country, 1 if own else 0])
    del recent[:-RECENT_UPDATES]
    if visits is not None:
        visits.updates(day).append(_update_row(t, name, m.group(1), country, own, ua))
    d = _day(stats, day)
    if own:
        _bump(d.setdefault("update_own", {}), update_channel(name))
        return
    _bump(d.setdefault("updates", {}), platform_of(name))
    _bump(d.setdefault("update_files", {}), name[:120])
    _bump(d.setdefault("update_from", {}), m.group(1)[:20])


def _download(stats: dict, line: str, state: dict, visits: Visits | None = None, db=None) -> None:
    m = DL_LINE.search(line)
    if not m or m.group(5) != "302":
        return
    dd, mon, yyyy, name = m.group(1), m.group(2), m.group(3), m.group(4)
    if mon not in MONTHS:
        return
    day = f"{yyyy}-{MONTHS[mon]:02d}-{dd}"
    name = name.rsplit("/", 1)[-1]
    ip = m.group(7) or ""
    if APP_UA.match(m.group(6)):
        return _app_update(stats, day, name, m.group(6), ip, state, own=not counted(ip),
                           t=_dl_epoch(line), db=db, visits=visits)
    if not counted(ip) or not browser(m.group(6)):
        return
    if ip and ip != "-":
        h = _hash(state, ip, m.group(6), name)
        seen = state.setdefault("seen_dl", {}).setdefault(day, [])
        if h in seen:
            return
        seen.append(h)
    d = _day(stats, day)
    _bump(d["downloads"], platform_of(name))
    _bump(d.setdefault("files", {}), name[:120])
    if visits is not None and ip and ip != "-":
        t = _dl_epoch(line)
        vid = _hash(state, ip, m.group(6))
        visit = visits.open_visit(day, vid, t) or _new_visit(visits, day, vid, t, ip, m.group(6), db)
        visit["downloads"].append([t, name[:120]])
        visit["end"] = max(visit["end"], t)


def _dl_epoch(line: str) -> int:
    m = re.search(r"\[(\d{2}/\w{3}/\d{4}:\d{2}:\d{2}:\d{2} [+-]\d{4})\]", line)
    if not m:
        return 0
    try:
        return int(datetime.strptime(m.group(1), "%d/%b/%Y:%H:%M:%S %z").timestamp())
    except ValueError:
        return 0


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
    d = stats["days"].setdefault(day, {})
    for k in ("views", "visitors", "requests"):
        d.setdefault(k, 0)
    for k in ("pages", "refs", "channels", "sources", "langs", "os", "devices", "countries",
              "downloads", "files"):
        d.setdefault(k, {})
    return d


# -- countries ---------------------------------------------------------------------

_GEO: dict = {"mtime": None, "reader": None}


def geo():
    """The country database, read again when it changes; None without one."""
    try:
        mtime = os.stat(GEO_DB).st_mtime
    except OSError:
        return None
    if _GEO["mtime"] != mtime:
        try:
            from mmdb import Reader
            _GEO.update(mtime=mtime, reader=Reader(GEO_DB))
        except (OSError, ValueError) as e:
            print(f"[stats] {GEO_DB}: {e}", flush=True)
            _GEO.update(mtime=mtime, reader=None)
    return _GEO["reader"]


def fetch_geo(state: dict) -> None:
    """DB-IP's free country database, this month's (or last month's, early
    in a month), when there is none or it is a month old; tried once a day."""
    try:
        if time.time() - os.stat(GEO_DB).st_mtime < 32 * 86400:
            return
    except OSError:
        pass
    today = datetime.now(timezone.utc).date()
    if state.get("geo_tried") == today.isoformat():
        return
    state["geo_tried"] = today.isoformat()
    for month in (today.strftime("%Y-%m"), (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")):
        url = GEO_URL.format(month=month)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "dopeiptv.org-stats"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = gzip.decompress(resp.read())
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(GEO_DB), prefix=".tmp-")
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            from mmdb import Reader
            Reader(tmp)                         # a whole, readable file before it replaces the old
            os.chmod(tmp, 0o640)
            os.replace(tmp, GEO_DB)
            print(f"[stats] country database from {url}", flush=True)
            return
        except (OSError, ValueError, EOFError) as e:
            print(f"[stats] {url}: {e}", flush=True)
            with contextlib.suppress(OSError, UnboundLocalError):
                os.unlink(tmp)


# -- one run -----------------------------------------------------------------------

@contextlib.contextmanager
def _locked():
    """One run at a time - cron's and the admin page's - or both would count
    the same new lines."""
    os.makedirs(STATS_DIR, mode=0o750, exist_ok=True)
    with open(os.path.join(STATS_DIR, ".lock"), "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _epoch(t: str) -> int:
    try:
        return int(datetime.fromisoformat(t).timestamp())
    except ValueError:
        return 0


class Visits:
    """The day files of visits this run touches, kept in memory until saved."""

    def __init__(self, uniques: dict) -> None:
        self.days: dict = {}
        self.upd_days: dict = {}
        self.uniques = uniques
        self._sets: dict = {}

    def day(self, day: str) -> list:
        if day not in self.days:
            got = _load_list(os.path.join(VISITS_DIR, day + ".json"))
            self.days[day] = got
        return self.days[day]

    def open_visit(self, day: str, vid: str, t: int):
        for v in reversed(self.day(day)):
            if v["id"] == vid and t - v["end"] <= VISIT_GAP and t >= v["start"] - 60:
                return v
        return None

    def before(self, day: str, vid: str) -> int:
        """On how many earlier days (of the last 400) this visitor came."""
        n = 0
        for d, lst in self.uniques.items():
            if d >= day:
                continue
            if d not in self._sets:
                self._sets[d] = set(lst)
            if vid in self._sets[d]:
                n += 1
        return n

    def updates(self, day: str) -> list:
        """The app's own updates of a day, for the visitor log - kept apart
        from the visits, so nothing that counts visits ever sees them."""
        if day not in self.upd_days:
            self.upd_days[day] = _load_list(os.path.join(UPDATES_DIR, day + ".json"))
        return self.upd_days[day]

    def save(self) -> None:
        for folder, days in ((VISITS_DIR, self.days), (UPDATES_DIR, self.upd_days)):
            if not days:
                continue
            os.makedirs(folder, mode=0o750, exist_ok=True)
            for day, lst in days.items():
                _save(os.path.join(folder, day + ".json"), lst)


def _new_visit(visits: Visits, day: str, vid: str, t: int, ip: str, ua: str, db) -> dict:
    name, version = browser_of(ua)
    v = {"id": vid, "start": t, "end": t, "browser": name, "bv": version,
         "os": os_version_of(ua), "device": device_of(ua),
         "country": (db.country(ip) if db is not None else "") or "",
         "returning": visits.before(day, vid), "pages": [], "time": 0,
         "downloads": [], "events": []}
    visits.day(day).append(v)
    return v


def _page_line(stats: dict, uniques: dict, state: dict, r: dict, db, visits: Visits) -> None:
    ua, ip = r.get("a") or "", r.get("ip") or ""
    if not counted(ip):
        return
    day = (r.get("t") or "")[:10]
    if len(day) != 10:
        return
    url = urlsplit(r.get("u") or "")
    status = str(r.get("s"))
    if url.path in PAGES:                       # the page itself: every request, robots too
        if r.get("m") == "GET" and status in ("200", "304"):
            _day(stats, day)["requests"] += 1
        return
    if url.path != "/hit" or status != "204" or not browser(ua):
        return
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    page = PAGES.get(q.get("p", ""))
    if page is None:
        return
    t = _epoch(r.get("t") or "")
    vid = _hash(state, ip, ua)
    d = _day(stats, day)
    ev = q.get("ev", "view")
    visit = visits.open_visit(day, vid, t)

    if ev == "leave":                           # how long the page was looked at
        try:
            secs = max(0, min(int(q.get("t", "0")), 3600))
        except ValueError:
            secs = 0
        if visit is not None and secs:
            visit["time"] += secs
            visit["end"] = max(visit["end"], t)
            for pg in reversed(visit["pages"]):
                if pg[1] == page:
                    pg[2] += secs
                    break
        return
    if ev in ("donate", "donated"):
        _bump(d.setdefault("events", {}), ev)
        if visit is not None:
            visit["events"].append([t, ev])
            visit["end"] = max(visit["end"], t)
        return
    if ev == "act":                             # a pointer, touch, wheel or key: someone is there
        if visit is not None:
            visit["acted"] = True
        return
    if ev != "view":
        item = stats_events.clean(ev, q.get("x"), q.get("n"))
        if item and visit is not None:
            stats_events.add(visit, t, item)
            visit["end"] = max(visit["end"], t)
        return

    d["views"] += 1
    _bump(d["pages"], page)
    host = referrer_host(q.get("r", ""))
    if host in SITE_HOSTS:
        host = ""
    ref_page = referrer_page(q.get("r", ""), host)
    src = re.sub(r"[^\w.-]", "", q.get("s", ""))[:40].lower()
    if visit is None or "channel" not in visit:  # a new visit, or one that began with a download
        visit = visit or _new_visit(visits, day, vid, t, ip, ua, db)
        visit.update(channel=channel_of(host, src), ref=host[:80], source=src,
                     lang=q.get("l", "")[:5], blang=q.get("bl", "")[:20])
        if ref_page:
            visit["ref_page"] = ref_page
        visit["screen"], kind = screen_of(q.get("w", ""), q.get("h", ""))
        _bump(d.setdefault("visit_channels", {}), visit["channel"])
        if kind:
            _bump(d.setdefault("screens", {}), kind)
    if q.get("hl") == "1":
        visit["hl"] = True
    visit["pages"].append([t, page, 0])
    visit["end"] = max(visit["end"], t)

    seen = uniques.setdefault(day, [])
    if vid in seen:
        return
    seen.append(vid)                            # the visitor's first page today
    d["visitors"] += 1
    if visit["returning"]:
        d["returning"] = d.get("returning", 0) + 1
    _bump(d["channels"], channel_of(host, src))
    if host:
        _bump(d["refs"], host[:80])
    if ref_page:
        _bump(d.setdefault("ref_pages", {}), ref_page)
    if src:
        _bump(d["sources"], src)
    lang = q.get("l") or r.get("l") or "en"
    _bump(d["langs"], lang if re.fullmatch(r"[a-z]{2,3}", lang) else "en")
    name, version = browser_of(ua)
    _bump(d["os"], os_of(ua))
    _bump(d.setdefault("os_versions", {}), os_version_of(ua))
    _bump(d["devices"], device_of(ua))
    _bump(d.setdefault("browsers", {}), name)
    _bump(d.setdefault("browser_versions", {}), f"{name} {version}".strip())
    if db is not None:
        _bump(d["countries"], visit["country"] or "unknown")


# -- people and robots ---------------------------------------------------------------
# The rules are in the docstring at the top. Chrome and Edge share version
# numbers; both, and Firefox, bring a new one every four weeks.

STALE_BEHIND = 26           # versions behind the newest: two years
FAMILY = {"Chrome": "Chromium", "Edge": "Chromium", "Firefox": "Firefox"}
# A release each (version, day): from it, a version every four weeks plus a
# few for beta and nightly is as new as one can be on a day. A higher one is
# made up and never moves the line - it can only stay where it is.
RELEASES = {"Chromium": (142, date(2025, 10, 28)), "Firefox": (145, date(2025, 11, 11))}
# Every macOS since 10.15 says 10.15. The last version for 10.15 itself, and
# anything newer, is never judged on a Mac: it may be all that Mac can run.
MAC_LAST = {"Chromium": 128, "Firefox": 115}


def _major(v: dict) -> int:
    try:
        return int(v.get("bv") or 0)
    except ValueError:
        return 0


def _ceiling(family: str, day: str) -> int:
    version, released = RELEASES[family]
    try:
        days = (date.fromisoformat(day) - released).days
    except ValueError:
        days = 0
    return version + max(0, days) // 28 + 4


def learn_newest(newest: dict, visits: Visits) -> None:
    """The newest Chrome and Firefox that people bring: from visits that
    moved or stayed a while, never above what can exist on their day."""
    for day, lst in visits.days.items():
        for v in lst:
            family, n = FAMILY.get(v.get("browser") or ""), _major(v)
            if not family or not n or v.get("hl") or not (v.get("acted") or (v.get("time") or 0) >= 5):
                continue
            if n <= _ceiling(family, day) and n > newest.get(family, 0):
                newest[family] = n


def _held_back(v: dict) -> bool:
    """A system that cannot run the newest browser, where an old version is
    a person's last one and says nothing."""
    system = v.get("os") or ""
    if v.get("device") == "TV" or system.startswith(("iOS", "iPadOS", "ChromeOS", "macOS 10.", "Other")):
        return True
    if system.startswith("Windows"):
        return system != "Windows 10/11"
    if system.startswith("Android"):
        m = re.match(r"Android (\d+)", system)
        return not m or int(m.group(1)) < 10
    return False


def stale(v: dict, newest: dict) -> bool:
    family, n = FAMILY.get(v.get("browser") or ""), _major(v)
    if not family or not n or not newest.get(family) or _held_back(v):
        return False
    if v.get("os") == "macOS" and n >= MAC_LAST[family]:
        return False
    return n < newest[family] - STALE_BEHIND


def judge(v: dict, newest: dict) -> tuple:
    """("person" | "unknown" | "robot", why): only a move outweighs a robot's
    signs - robots can stay a while and open two pages too."""
    if v.get("hl"):
        return "robot", "headless"
    if v.get("acted"):
        return "person", "acted"
    if stale(v, newest):
        return "robot", "old"
    if len(v.get("pages") or ()) > 1:
        return "person", "pages"
    if (v.get("time") or 0) >= 5:
        return "person", "time"
    if v.get("pages") and v.get("downloads"):
        return "person", "download"
    if v.get("returning"):
        return "person", "returning"
    return "unknown", ""


def _os_family(system: str) -> str:
    """os_of's name from os_version_of's."""
    first = (system or "Other").split(" ")[0]
    return "iOS" if first == "iPadOS" else first if first in (
        "Windows", "iOS", "Android", "ChromeOS", "macOS", "Linux") else "Other"


def robot_share(lst: list) -> tuple:
    """What a day's visits judged robots added to its counts, as _page_line
    and _download added it - per visit (pages, downloads) and, for a visitor
    who was nothing but a robot that day, per visitor (the lists of the
    visitor's first page) - with the day's visitors who were people and
    those who were robots."""
    rb: dict = {"visitors": 0, "views": 0, "visits": 0, "returning": 0, "lists": {}}

    def add(key: str, name: str) -> None:
        if name:
            _bump(rb["lists"].setdefault(key, {}), name)
    by_id: dict = {}
    for v in lst:
        robot = v.get("kind") == "robot"
        if robot:
            for _, name in v.get("downloads") or ():
                add("downloads", platform_of(name))
                add("files", name)
        if not v.get("pages"):
            continue
        by_id.setdefault(v["id"], []).append(v)
        if not robot:
            continue
        rb["visits"] += 1
        rb["views"] += len(v["pages"])
        for p in v["pages"]:
            add("pages", p[1])
        if "channel" in v:
            add("visit_channels", v["channel"])
        if v.get("screen") and v["screen"].count("×") == 1:
            add("screens", screen_of(*v["screen"].split("×"))[1])
    people, robots = [], []
    for vid, vs in by_id.items():
        if any(v.get("kind") == "person" for v in vs):
            people.append(vid)
        if not all(v.get("kind") == "robot" for v in vs):
            continue
        robots.append(vid)
        first = min(vs, key=lambda v: v.get("start", 0))
        rb["visitors"] += 1
        rb["returning"] += 1 if first.get("returning") else 0
        lang = first.get("lang") or ""
        add("channels", first.get("channel") or "Direct or unknown")
        add("refs", first.get("ref") or "")
        add("ref_pages", first.get("ref_page") or "")
        add("sources", first.get("source") or "")
        add("langs", lang if re.fullmatch(r"[a-z]{2,3}", lang) else "en")
        add("os", _os_family(first.get("os") or ""))
        add("os_versions", first.get("os") or "")
        add("devices", first.get("device") or "")
        add("browsers", first.get("browser") or "")
        add("browser_versions", f"{first.get('browser') or ''} {first.get('bv') or ''}".strip())
        add("countries", first.get("country") or "unknown")
    return rb, sorted(people), sorted(robots)


def _visit_totals(stats: dict, visits: Visits, kinds: dict, newest: dict) -> None:
    """A touched day's visits judged, then its visits, bounces (one page,
    under ten seconds) and time looked at, without the robots - counted again
    as visits grow. Time and bounces only from the visits whose time is known
    (timed): a page that never said how long it was looked at - closed
    without a word, as phones do, or its message blocked - is not a visit of
    no seconds. What the robots added is kept apart in "robots"."""
    for day, lst in visits.days.items():
        for v in lst:
            v["kind"], v["why"] = judge(v, newest)
        rb, people, robots = robot_share(lst)
        kinds[day] = {"p": people, "r": robots}
        if day not in stats["days"]:
            continue
        d = stats["days"][day]
        with_pages = [v for v in lst if v["pages"] and v["kind"] != "robot"]
        d["visits"] = len(with_pages)
        timed = [v for v in with_pages if v["time"] > 0]     # a leave came: seconds were added
        d["timed"] = len(timed)
        d["bounces"] = sum(1 for v in timed if len(v["pages"]) == 1 and v["time"] < 10)
        d["engaged"] = sum(v["time"] for v in timed)
        d["people"] = len(people)
        if rb["visits"] or rb["lists"]:
            d["robots"] = rb
        else:
            d.pop("robots", None)


def collect(backfill: bool = False, with_fetch: bool = False) -> None:
    stats_path = os.path.join(STATS_DIR, "stats.json")
    state_path = os.path.join(STATS_DIR, "state.json")
    uniques_path = os.path.join(STATS_DIR, "uniques.json")
    kinds_path = os.path.join(STATS_DIR, "kinds.json")
    with _locked():
        if backfill and os.path.exists(state_path):
            sys.exit(f"--backfill is for a fresh start: {state_path} exists, and the old logs "
                     "would be counted twice. Remove stats.json and state.json first.")
        IGNORED[:] = _ignored_nets()
        stats = _load(stats_path, {"days": {}})
        state = _load(state_path, {"logs": {}})
        uniques = _load(uniques_path, {})
        kinds = _load(kinds_path, {})
        visits = Visits(uniques)
        for gone in ("salts", "seen"):          # the day-salted hashes of before
            state.pop(gone, None)
        if with_fetch:
            fetch_geo(state)
        db = geo()
        if not os.path.isdir(UPDATES_DIR):
            _seed_updates(state, visits)

        for line in _read_new(STATS_LOG, state.setdefault("logs", {}).setdefault("stats", {})):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict):
                _page_line(stats, uniques, state, r, db, visits)

        if backfill:
            for old in _rotated(DL_LOG):
                with _open_any(old) as fh:
                    for line in fh:
                        _download(stats, line, state)
                print(f"[stats] read {old}", flush=True)
        for line in _read_new(DL_LOG, state["logs"].setdefault("downloads", {})):
            _download(stats, line, state, visits, db)
        # Days kept from before visits were judged are judged once, too.
        for f in glob.glob(os.path.join(VISITS_DIR, "*.json")):
            if os.path.basename(f)[:10] not in kinds:
                visits.day(os.path.basename(f)[:10])
        newest = state.setdefault("newest", {})
        learn_newest(newest, visits)
        _visit_totals(stats, visits, kinds, newest)

        seen_dl = state.setdefault("seen_dl", {})
        for old in sorted(seen_dl)[:-2]:        # a download's day can only still get lines for a day
            seen_dl.pop(old, None)
        for old in sorted(uniques)[:-KEEP_UNIQUE_DAYS]:
            uniques.pop(old, None)
        for old in [d for d in kinds if not uniques or d < min(uniques)]:
            kinds.pop(old, None)
        # A day's list of sites or files stays short, so the years stay small -
        # a flood of made-up referrers cannot grow the file without end.
        for d in stats["days"].values():
            for k in ("refs", "ref_pages", "files", "sources", "update_files", "update_from"):
                lst = d.get(k) or {}
                if len(lst) > KEEP_PER_DAY:
                    top = sorted(lst.items(), key=lambda kv: -kv[1])
                    d[k] = dict(top[:KEEP_PER_DAY - 1])
                    d[k]["other"] = d[k].get("other", 0) + sum(v for _, v in top[KEEP_PER_DAY - 1:])
        stats["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        stats["geo"] = db is not None
        visits.save()
        _anonymize_old()
        _save(uniques_path, uniques)
        _save(kinds_path, kinds)
        _save(stats_path, stats)
        _save(state_path, state)


def _seed_updates(state: dict, visits: Visits) -> None:
    """Once, when the visitor log first shows updates: the newest ones
    already counted (state.json's recent_updates), so today's show too."""
    for r in state.get("recent_updates") or []:
        if not (isinstance(r, list) and len(r) >= 5 and r[0]):
            continue
        day = datetime.fromtimestamp(r[0]).date().isoformat()
        visits.updates(day).append(_update_row(r[0], str(r[1]), str(r[2] or ""), str(r[3] or ""), bool(r[4])))
    for lst in visits.upd_days.values():
        lst.sort(key=lambda u: u.get("t") or 0)


def _family(os_version: str) -> str:
    return os_version.split(" (")[0].split(" ")[0] if os_version else "Other"


def anonymous(v: dict, day: str) -> dict:
    """A visit with nothing left that points to a person."""
    events = {e[1] for e in v.get("events") or []}
    return {"day": day, "country": v.get("country") or "", "browser": v.get("browser") or "Other",
            "os": _family(v.get("os") or ""), "device": v.get("device") or "",
            "lang": v.get("lang") or "", "channel": v.get("channel") or "",
            "site": v.get("ref") or "", "pages": len(v.get("pages") or []),
            "time": round((v.get("time") or 0) / 10) * 10, "timed": bool(v.get("time")),
            "returning": bool(v.get("returning")), "downloaded": bool(v.get("downloads")),
            "donate": "donate" in events, "donated": "donated" in events,
            "kind": v.get("kind") or ""}


def _anonymize_old() -> None:
    """Visits older than STATS_VISITS_DAYS: into the month's anonymous file,
    then the day's file goes. A day already in the month file is never
    added twice, so a run stopped halfway costs nothing."""
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=VISITS_DAYS)).isoformat()
    for f in sorted(glob.glob(os.path.join(VISITS_DIR, "*.json"))):
        day = os.path.basename(f)[:10]
        if day >= cutoff:
            continue
        month_path = os.path.join(ANON_DIR, day[:7] + ".json")
        month = _load(month_path, {"days": [], "visits": []})
        if day not in month["days"]:
            month["visits"] += [anonymous(v, day) for v in _load_list(f) if v.get("pages") or v.get("downloads")]
            month["visits"].sort(key=lambda a: (a["day"], a["country"], a["browser"], a["os"], a["device"],
                                                a["channel"], a["site"], a["pages"], a["time"]))
            month["days"] = sorted(month["days"] + [day])
            os.makedirs(ANON_DIR, mode=0o750, exist_ok=True)
            _save(month_path, month)
        with contextlib.suppress(OSError):
            os.unlink(f)
    # The app's updates of then: counted in stats.json already, so they go.
    for f in glob.glob(os.path.join(UPDATES_DIR, "*.json")):
        if os.path.basename(f)[:10] < cutoff:
            with contextlib.suppress(OSError):
                os.unlink(f)


def collect_if_stale(seconds: int = 20) -> None:
    """For the admin page: a run unless one finished a moment ago."""
    try:
        if time.time() - os.stat(os.path.join(STATS_DIR, "stats.json")).st_mtime < seconds:
            return
    except OSError:
        pass
    collect()


def _load(path: str, default: dict) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            got = json.load(fh)
        return got if isinstance(got, dict) else default
    except (OSError, ValueError):
        return default


def _load_list(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as fh:
            got = json.load(fh)
        return got if isinstance(got, list) else []
    except (OSError, ValueError):
        return []


def _save(path: str, data) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))
    os.chmod(tmp, 0o640)
    os.replace(tmp, path)


if __name__ == "__main__":
    collect(backfill="--backfill" in sys.argv[1:], with_fetch=True)
