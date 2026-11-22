#!/usr/bin/env python3
"""stats_collect.py - the site's visitor statistics, never an address kept.

nginx hands each line - a page view, the page's /hit, a click on a
download - to the donate service over a socket of its own (stats_inbox.py,
the content server's nginx file), and the service's own thread counts them
every minute (donate.py), as does the admin page when it is opened. The
lines wait in its memory alone until then: no log with a visitor's address
is written, so none turns over, lingers or ends up in a backup. Counts per
day go to STATS_DIR/stats.json.

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
page's visitor log, as at_rest writes it: country, browser and its major
version, system, device and the kind of screen (a phone, a laptop - never
its size), the page's language and the browser's (without its country),
how the visitor came (the site that linked by the name its owner
registered, our own links' tag), the pages and how long each was looked
at, downloads, the donate dialog, what was done on the page (stats_events)
- and on how many earlier days the visitor came. Its start is kept to the
half hour and what happened in it to ten seconds from there. Its visitor
is named for that day alone (day_id): nothing kept ties a visit to the
visitor's visits of another day. Beside it the visitor's mark is sealed
(seal: random to anyone without the month's key), which only this service
can open while the key is in its memory - so that a visit can go on after a
restart. A payment is never on a visit, only in the day's count: the
visit's time, country and browser beside the payment service's record of
who paid would tie the two together. A visit ends after 30 minutes without
a page. After STATS_VISITS_DAYS days (7, and never more: VISITS_DAYS_MAX)
a day's visits are deleted, every one: no row of a visit is kept past them,
in detail or made anonymous. Every run then looks for what should be gone
(left_behind) and deletes it again; what cannot be deleted is said in the
log and on the admin page until it is gone (TROUBLE, trouble).
What stays of them for good is counts: the day's totals and lists, and per
way in (search, social...) how many visits read more than one page, how
long they looked, downloaded, opened the support dialog (funnel_of, the
day's "funnel"). The day's lists of who came (countries, languages,
browser and system versions, sites, tags) then keep no name counted fewer
than K_DAY (5) times that day - the rest is "other" - and nothing adds
those days up again under a name (fold_day): a name is kept only from a day
when five different visitors brought it. Those counts per day are kept for
good. (Until November 2026 the visits went on as anonymous rows and the
month kept its own list of the rarer names; both go at the first run -
_old_rows_off.)

Each update the app fetched for itself goes to STATS_DIR/updates/<day>.json
for the same log: the hour it came in, the file, the version it came from,
the system and processor the app named, the country, and whether from your
own network - no address, never the second. Never a visit, never counted
as one; after UPDATES_DAYS (30) the day's file goes (the counts per day
stay).

A visitor is a keyed hash (HMAC-SHA256) of address and browser, made in
memory: the key is the month's own (MonthKeys) and in this service's
memory - never in a file on a disk (STATS_KEYS_DIR may name a folder on a
file system kept in memory, tmpfs, so a restart of the service keeps them;
a restart of the computer does not), a new one each month, forgotten two
months on. What the system swaps out of memory reaches a disk only as the
swap keeps it: the server's swap is encrypted with a key of its own that a
restart forgets (the server's runbook), and swap_state asks the system - swap that
keeps what it is given is said on the admin page and in the log. So a visitor is the same visitor
within a month, and is told returning from the month before at the most.
How many different visitors came over several days is kept in each day's
sketches (sketch.py: every visitor, those not robots, the people) - two
bits a visitor in a field of 131 072, no mark and no list. A visitor sets
the same two bits on every day of its month, so two days' sketches side by
side could tell that someone came on both: they are kept only where the
month's key is - beside it in STATS_KEYS_DIR, a file system in memory, or in
this service's memory alone - and never on a disk (sketches_file), for this
month and the one before; then the month is counted once more (different
visitors in the month and each of its weeks, people and robots) into
uniques-months.json, numbers alone, and its sketches are deleted
(freeze_months). Whoever could read them could open the sealed marks with
the key beside them anyway; nobody with only the disks, a backup or a copy
of STATS_DIR can. A restart that loses the keys loses the sketches with
them: the admin page then sums the days' visitors for those days.
Unique visitors over a span longer than a month are the months' summed. A
restart without its keys starts new ones: a visitor before and after it
counts twice that month. Robots that say so, scripts and empty browser
names are left out everywhere.

Your own visits are left out three ways: a browser that has opened the
admin page carries a cookie nginx does not log (the content server's nginx file); an
address that is not a public one (your own network, 192.168.x and the like)
never counts; and STATS_IGNORE lists more - addresses, networks or host
names (a dynamic-DNS name is looked up on every run), e.g.
STATS_IGNORE=203.0.113.5, 2001:db8:1::/48, home.example.net

Where the lines come from:

  STATS_SOCKET  unix:/var/run/iptv_donate/stats.sock: nginx's syslog
                output, the iptv_stats and iptv_dl formats in
                the content server's nginx file, a datagram a line
  STATS_DIR     <site>/stats, beside public/ - outside the web root
  GEO_DB        STATS_DIR/dbip-country-lite.mmdb
  STATS_LOG, DL_LOG  the log files of before; until they are deleted
                (the server's runbook) what is left of them is read once, from where
                the last run stopped - and, since they hold addresses, while one
                of them (or a copy newsyslog made) is on the disk the log and the
                admin page say so, louder when nginx still writes it (address_logs)

Anyone can send the page's signal, as often as the web server lets them,
with any browser name and any page to have come from. So nothing a line
says can stop a run (a line that cannot be read is passed over, and said
as a number), take its time (every pattern has an end, a day's marks are
looked up, not gone through) or grow what is kept without end: a browser
name is read to UA_MAX, a version is a few digits, a day keeps at most
MAX_DAY_VISITS visits (STATS_MAX_DAY_VISITS in the settings file; 50 000 -
past it a new visit is its page views only, the day's "capped"), a visit
at most MAX_VISIT_PAGES pages, a day's lists their largest entries, the
lines waiting in memory stats_inbox.MAX_BYTES, and an old log is read
READ_MAX at a time. Everything written is the service's alone
to read (files 0600, folders 0700).

Settings come from the same file as donate.py's (DONATE_CONFIG). Standard
library only.
"""
from __future__ import annotations

import contextlib
import errno
import fcntl
import glob
import gzip
import hashlib
import hmac
import io
import ipaddress
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from donate import CONF  # the shared settings file
import sketch  # how many different visitors, without a mark of any one
import stats_events  # what a page tells beside the view: help, parts read, links
import stats_inbox  # the lines, straight from nginx into memory

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
GEO_MAX = 256 << 20       # the database is a few megabytes; nothing larger is taken for it
KEEP_PER_DAY = 40         # a day's longest lists (sites, files): the top 40, the rest summed
VISITS_DIR = os.path.join(STATS_DIR, "visits")
#: The anonymous visits of before (until November 2026): read once into the
#: days' counts and deleted (_old_rows_off).
ANON_DIR = os.path.join(STATS_DIR, "visits-anon")
UPDATES_DIR = os.path.join(STATS_DIR, "updates")
#: A day's visits in detail are kept this many days at most, whatever
#: STATS_VISITS_DAYS says: the privacy policy promises 7, so a larger value
#: is cut to it (said at the start, donate.start_statistics).
VISITS_DAYS_MAX = 7
try:
    VISITS_DAYS = min(VISITS_DAYS_MAX, max(1, int(CONF.get("STATS_VISITS_DAYS", "7"))))
except ValueError:
    VISITS_DAYS = VISITS_DAYS_MAX
#: The app's updates one by one, each at its hour, for the visitor log; the
#: counts per day stay.
UPDATES_DAYS = 30
VISIT_GAP = 30 * 60       # a visit ends after half an hour without a page
#: A kept visit's start, to the half hour; what happened in it, to ten
#: seconds from that start (at_rest). Exact only in memory, for the run.
COARSE = 1800
STEP = 10
#: A folder on a file system kept in memory (tmpfs) where the month keys may
#: wait through a restart of the service; "" keeps them in its memory alone.
KEYS_DIR = CONF.get("STATS_KEYS_DIR", "")
#: Where nginx hands the statistics' lines (stats_inbox, donate.py).
SOCKET = CONF.get("STATS_SOCKET", "unix:/var/run/iptv_donate/stats.sock")
#: A day's lists kept for good tell no name fewer than this many visitors
#: brought that day, once its visits are gone (fold_day): the rest is
#: "other", and nothing adds days up again under a name.
K_DAY = 5

# Anyone can send the page's signal, with any browser name and any address
# to have come from, as often as the web server lets them. None of it may
# grow what is kept without end or take the run's time:
#: A browser's name as read: the longest real ones are half of this. The
#: web server cuts it at the same place (the content server's nginx file), so a line
#: fits one datagram.
UA_MAX = 512
#: A site's name is at most 253 characters.
HOST_MAX = 255
#: A day's lists that the world keeps short (languages, systems, browser
#: versions): never this long from people, so made-up ones cannot grow them.
KEEP_WIDE = 200
#: The visits kept of one day. Past it a new visit is counted as its page
#: views only (the day's "capped") - a flood of made-up visitors fills
#: neither the disk nor the memory of whoever reads the day's file.
try:
    MAX_DAY_VISITS = max(1000, int(CONF.get("STATS_MAX_DAY_VISITS", "50000")))
except ValueError:
    MAX_DAY_VISITS = 50000
#: How much of a log one run reads; the rest waits for the next.
READ_MAX = 32 << 20
#: One visit's pages, support dialogs opened and downloads: nobody's visit
#: has as many, and one that goes on being sent stops growing there.
MAX_VISIT_PAGES = 1000
MAX_VISIT_EVENTS = 200
#: What a page sends for the tip dialog opened (or a way to support picked
#: on the support page) and for a tip paid, kept under the names the
#: statistics have always had. A page cached from before November 2026
#: sends those names itself.
SUPPORT_EVENTS = {"support": "donate", "supported": "donated"}
MAX_VISIT_DOWNLOADS = 200

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


_NO_URL = urlsplit("")


def _split(url: str):
    """urlsplit, for an address a visitor made up: one it cannot read
    ("http://[", a host name that is none) is no address at all - never an
    error that stops the run and, with it, the statistics."""
    try:
        return urlsplit(url)
    except ValueError:
        return _NO_URL


def referrer_host(ref: str) -> str:
    """The site that linked here: a web address's host, or an Android app's
    name (android-app://com.google.android.googlequicksearchbox/)."""
    if not ref:
        return ""
    parts = _split(ref)
    try:
        host = (parts.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    except ValueError:
        return ""
    return host[:HOST_MAX] if parts.scheme in ("http", "https", "android-app") else ""


def referrer_page(ref: str, host: str) -> str:
    """The page that linked here, when the linking site sent more than its
    own address (many send only that - Google, Facebook and X always):
    host and path, without the query or fragment, which can carry what
    points to a person (a search, a session, a click id). "" when there is
    no page, and for a search engine, whose page says nothing more."""
    if not host or SEARCH.search(host):
        return ""
    parts = _split(ref)
    if parts.scheme not in ("http", "https"):
        return ""
    path = re.sub(r"/+", "/", parts.path or "").rstrip("/")
    return (host + path)[:200] if path else ""


#: Country domains whose sites are named a level further down (example.co.uk).
_SECOND = {"co", "com", "org", "net", "ac", "gov", "edu", "ne", "or", "go", "gv", "nic", "ltd", "plc"}


def site_of(host: str) -> str:
    """The site that linked here, as kept: a search engine, social site or
    code site everyone knows as it names itself, any other by the name its
    owner registered (forum.example.net -> example.net) - so a name made
    for one visitor alone (alice.example.net) points to nobody."""
    if not host or SEARCH.search(host) or SOCIAL.search(host) or CODE.search(host):
        return host
    try:
        ipaddress.ip_address(host.strip("[]"))
        return "an address"
    except ValueError:
        pass
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if len(labels[-1]) == 2 and labels[-2] in _SECOND:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


#: Our own links' ?ref= (MAINTAINING.md: app-about, github-issues, release...).
OWN_SOURCES = re.compile(r"(?:app|github|release|windows|readme)(?:-[a-z0-9]{1,20}){0,2}")


def source_of(src: str) -> str:
    """A link's ?ref= or ?utm_source=, as kept: one of our own links', or a
    site everyone knows; any other is "other" - a value written into one
    person's link would otherwise name them."""
    if not src:
        return ""
    if OWN_SOURCES.fullmatch(src) or SEARCH.search(src) or SOCIAL.search(src) or CODE.search(src):
        return src
    return "other"


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


# A version is a few digits: a made-up one of thousands is never a name to
# count a day's browsers under (each would be a list entry of its own), and
# every part has an end, so no browser name takes the pattern long to read.
SAFARI = r"Version/(\d{1,4})[\d.]{0,24} (?:Mobile/\S{1,40} )?Safari/"
BROWSERS = [(name, re.compile(rx)) for name, rx in (
    ("Edge", r"Edg(?:e|A|iOS)?/(\d{1,4})"),
    ("Opera", r"(?:OPR|Opera|OPT)/(\d{1,4})"),
    ("Samsung Internet", r"SamsungBrowser/(\d{1,4})"),
    ("Yandex", r"YaBrowser/(\d{1,4})"),
    ("Vivaldi", r"Vivaldi/(\d{1,4})"),
    ("Firefox", r"(?:Firefox|FxiOS)/(\d{1,4})"),
    ("Chrome", r"(?:Chrome|CriOS)/(\d{1,4})"),
    ("Safari", SAFARI),
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
    m = re.search(r"(?:iPhone|CPU) OS (\d{1,3})", ua)
    if m:
        major = m.group(1)
        # From iOS 26 Safari says 18 for every newer system, and its own
        # version - the system's - is the one to go by.
        s = re.search(SAFARI, ua)
        if major == "18" and s and int(s.group(1)) >= 26:
            major = s.group(1)
        return ("iPadOS " if "iPad" in ua else "iOS ") + major
    m = re.search(r"Android (\d{1,3})", ua)
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


def _whole_lines(data: bytes, more: bool) -> int:
    """How much of *data* is whole lines. A full piece (*more* follows it)
    without one end of line is nothing a web server wrote: passed over, so
    the run can never stand still at it."""
    cut = data.rfind(b"\n") + 1
    return len(data) if not cut and more else cut


def _read_new(path: str, state: dict) -> list:
    """Lines added to *path* since the last run, following a rotation into
    <path>.0; *state* ({"ino", "off"}) is updated in place. At most READ_MAX
    of a file a run - a log grown huge (a flood, or runs that failed for a
    day) is read in pieces, never all into memory; the rest waits."""
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
                    data = fh.read(READ_MAX)
                if off + len(data) < old.st_size:
                    # More of the old file than one run reads: the rest of
                    # it next time, before the new file.
                    cut = _whole_lines(data, True)
                    lines += data[:cut].decode("utf-8", "replace").splitlines()
                    state["off"] = off + cut
                    return lines
                lines += data.decode("utf-8", "replace").splitlines()
        except OSError:
            pass
        off = 0
    if ino == st.st_ino and st.st_size < off:      # truncated in place
        off = 0
    with open(path, "rb") as fh:
        fh.seek(off)
        data = fh.read(READ_MAX)
    # Only whole lines: a half-written last line waits for the next run.
    cut = _whole_lines(data, len(data) == READ_MAX)
    lines += data[:cut].decode("utf-8", "replace").splitlines()
    state["ino"], state["off"] = st.st_ino, off + cut
    return lines


def browser(ua: str) -> bool:
    """A person's browser: every one says "Mozilla/"; robots that say it
    too name themselves, and scripts (curl, wget, python...) do not say it."""
    return ua.startswith("Mozilla/") and not BOTS.search(ua)


def _month(day: str) -> str:
    return day[:7]


def _month_before(month: str) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    return f"{year - 1}-12" if mon == 1 else f"{year}-{mon - 1:02d}"


#: The months a key is kept for: this one and the one before (a visitor is
#: told returning from there, and the visits in detail reach back into it).
KEPT_MONTHS = 2


def _months_back(month: str, n: int) -> list:
    out = [month]
    for _ in range(n - 1):
        out.append(_month_before(out[-1]))
    return out


def in_memory(path: str) -> bool:
    """Whether *path* is on a file system kept in memory (tmpfs): what is
    written there is gone at the next boot and in no backup of a disk. Asked
    of df, which a jail answers for its own folders too."""
    try:
        out = subprocess.run(["df", "-T", path], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    words = " ".join(out.splitlines()[1:]).split()
    return len(words) > 1 and words[1] in ("tmpfs", "ramfs", "mfs")


class MonthKeys:
    """The keys every mark of a visitor is made with: one per month, random,
    in this service's memory alone - never in a file on a disk, so never in
    a backup or a copy of one, and gone when the service stops. With
    STATS_KEYS_DIR naming a folder on a file system kept in memory (tmpfs)
    they wait there through a restart too; anywhere else that folder is not
    used. A month's key is kept for KEPT_MONTHS months, then forgotten -
    and with it any way from what is kept back to an address. A line of an
    older month gets a key for this run alone."""

    def __init__(self, today: date, folder: str = "", keys: dict | None = None) -> None:
        self.folder = folder if folder and os.path.isdir(folder) and in_memory(folder) else ""
        got = keys if keys is not None else self._read()
        self.keys: dict = {m: bytes.fromhex(k) for m, k in got.items()
                           if isinstance(k, str) and re.fullmatch(r"[0-9a-f]{64}", k)}
        self._passing: dict = {}
        self.roll(today)

    def _read(self) -> dict:
        if not self.folder:
            return {}
        return _load(os.path.join(self.folder, "keys.json"), {})

    def _write(self) -> None:
        if not self.folder:
            return
        with contextlib.suppress(OSError):
            _save(os.path.join(self.folder, "keys.json"), {m: k.hex() for m, k in self.keys.items()})

    def roll(self, today: date) -> None:
        """Today's months kept, the older keys forgotten."""
        self.month = today.strftime("%Y-%m")
        self.kept = tuple(_months_back(self.month, KEPT_MONTHS))
        gone = [m for m in self.keys if m not in self.kept]
        for m in gone:
            del self.keys[m]
        if gone:
            self._write()

    def key(self, month: str) -> bytes:
        if month in self.keys:
            return self.keys[month]
        if month in self.kept:
            self.keys[month] = secrets.token_bytes(32)
            self._write()
            return self.keys[month]
        return self._passing.setdefault(month, secrets.token_bytes(32))

    def known(self, month: str) -> bytes | None:
        """The month's key if one is kept - never one made for the asking."""
        return self.keys.get(month)

    def kid(self, month: str) -> str:
        """A name for the month's key: which kept marks it can open."""
        return hmac.new(self.key(month), b"id", hashlib.sha256).hexdigest()[:8]


_KEYS: list = []

#: The days' sketches when no folder in memory holds them: in this
#: service's memory alone (sketches_file). "full": the folder could not take
#: them once - from then on they are here alone (_keep_sketches).
_SKETCHES: dict = {}
_SKETCHES_HERE = {"full": False}


def sketches_file() -> str:
    """Where the days' sketches wait through a restart: beside the month
    keys, in STATS_KEYS_DIR on a file system kept in memory (MonthKeys) -
    or "" for this service's memory alone. Never STATS_DIR, a disk: a
    visitor sets the same two bits on every day of its month, so two days'
    sketches side by side could tell that someone came on both. They are
    kept only where the key they are made with is."""
    folder = keys_now().folder
    if not folder or _SKETCHES_HERE["full"]:
        return ""
    return os.path.join(folder, "sketches.json")


def _keep_sketches(path: str, sketches: dict) -> None:
    """The sketches written beside the keys - or, the folder in memory too
    full for them, held in this service's memory from then on (said once,
    and never put on a disk instead)."""
    try:
        _save(path, sketches)
    except OSError as e:
        for left in glob.glob(os.path.join(os.path.dirname(path), ".tmp-*")):
            with contextlib.suppress(OSError):
                os.unlink(left)
        _SKETCHES.clear()
        _SKETCHES.update(sketches)
        _SKETCHES_HERE["full"] = True
        print(f"[stats] the sketches could not be kept beside the keys ({e.strerror or e}) - "
              "they are kept in this service's memory alone from now on", flush=True)


def sketches_now() -> dict:
    """The days' sketches as they are kept now - for the admin page."""
    with _RUN:
        path = sketches_file()
        return _load(path, {}) if path else dict(_SKETCHES)


def _sketches_off_disk(sketches: dict) -> None:
    """Once: the sketches of before, kept in STATS_DIR on the disk, taken
    where they are kept now - and that file deleted."""
    old = os.path.join(STATS_DIR, "sketches.json")
    if not os.path.exists(old):
        return
    for day, entry in _load(old, {}).items():
        if isinstance(entry, dict):
            sketches.setdefault(day, entry)
    _gone(old)


def keys_now(today: date | None = None) -> MonthKeys:
    """This service's keys, made at its first run and kept in its memory."""
    today = today or datetime.now().astimezone().date()
    if not _KEYS:
        _KEYS.append(MonthKeys(today, KEYS_DIR))
    else:
        _KEYS[0].roll(today)
    return _KEYS[0]


def _keys(state: dict, day: str) -> MonthKeys:
    keys = state.get("_keys")
    if not isinstance(keys, MonthKeys):
        keys = state["_keys"] = MonthKeys(date.fromisoformat(day))
    return keys


def _hash(state: dict, day: str, *parts: str) -> str:
    """A visitor, a download or an update as a mark: HMAC-SHA256 of *parts*
    with the key of *day*'s month (state["_keys"], in memory alone), cut to
    twelve characters. Never written down as it is: a visit kept carries a
    name of its own for its day (day_id) and the mark sealed (seal)."""
    key = _keys(state, day).key(_month(day))
    return hmac.new(key, "|".join(parts).encode(), hashlib.sha256).hexdigest()[:12]


def day_id(state: dict, day: str, vid: str) -> str:
    """The visitor as a kept visit names it: the same for its visits of one
    day, another on every other day - nothing kept ties two days together."""
    key = _keys(state, day).key(_month(day))
    return hmac.new(key, f"day|{day}|{vid}".encode(), hashlib.sha256).hexdigest()[:12]


def seal(state: dict, day: str, vid: str) -> tuple:
    """The visitor's mark, sealed with its month's key for the visit kept:
    ("w", "e") - random to anyone without the key (a new nonce each time,
    so two visits of one visitor look nothing alike), the mark again to
    this service while the key is kept (unseal). "e" names the key."""
    keys = _keys(state, day)
    month = _month(day)
    if not re.fullmatch(r"[0-9a-f]{12}", vid or ""):
        vid = hashlib.sha256((vid or "").encode()).hexdigest()[:12]     # a mark of long before
    nonce = secrets.token_bytes(6)
    pad = hmac.new(keys.key(month), b"w|" + nonce, hashlib.sha256).digest()[:6]
    body = bytes(a ^ b for a, b in zip(bytes.fromhex(vid), pad, strict=True))
    return (nonce + body).hex(), keys.kid(month)


def unseal(keys: MonthKeys, day: str, w, e) -> str:
    """The mark a visit kept was sealed with, or "" once its key is gone."""
    month = _month(day)
    key = keys.known(month)
    if not key or not isinstance(w, str) or not re.fullmatch(r"[0-9a-f]{24}", w) or keys.kid(month) != e:
        return ""
    raw = bytes.fromhex(w)
    pad = hmac.new(key, b"w|" + raw[:6], hashlib.sha256).digest()[:6]
    return bytes(a ^ b for a, b in zip(raw[6:], pad, strict=True)).hex()


def _first(state: dict, kind: str, day: str, marks: list, mark: str) -> bool:
    """Whether *mark* is new among a day's *marks*, and then adds it. Looked
    up in a set kept beside the list for this run (state["_seen"], never
    saved): a day of a hundred thousand marks costs a line no more than a
    day of ten - going through the list for each line did, and a flood of
    made-up visitors took the run's whole time."""
    sets = state.setdefault("_seen", {})
    known = sets.get((kind, day))
    if known is None:
        known = sets[(kind, day)] = set(marks)
    if mark in known:
        return False
    known.add(mark)
    marks.append(mark)
    return True


def _hash_before(state: dict, day: str, *parts: str) -> str:
    """The same mark under the month before's key: how this visitor was
    known then, to tell a return from there."""
    first = date.fromisoformat(day).replace(day=1)
    return _hash(state, (first - timedelta(days=1)).isoformat(), *parts)


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


def _to_hour(t: int) -> int:
    """A moment as its hour: an update kept is never the second it came."""
    return int(t) - int(t) % 3600 if t else 0


def _update_row(t: int, name: str, from_version: str, country: str, own: bool, ua: str = "") -> dict:
    """An update as the visitor log shows it (admin.py visits)."""
    m = APP_SYSTEM.search(ua or "")
    return {"t": _to_hour(t), "file": name[:120], "from": from_version[:20], "os": platform_of(name),
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
        h = _hash(state, day, "update", ip, name)
        if not _first(state, "dl", day, state.setdefault("_seen_dl", {}).setdefault(day, []), h):
            return
    country = (db.country(ip) if db is not None and ip and ip != "-" else "") or ""
    recent = state.setdefault("recent_updates", [])
    recent.append([_to_hour(t), name[:120], m.group(1)[:20], country, 1 if own else 0])
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
    try:
        date.fromisoformat(day)                 # the web server's own date: a day there is
    except ValueError:
        return
    name = name.rsplit("/", 1)[-1]
    ip = m.group(7) or ""
    ua = m.group(6)[:UA_MAX]
    if APP_UA.match(ua):
        return _app_update(stats, day, name, ua, ip, state, own=not counted(ip),
                           t=_dl_epoch(line), db=db, visits=visits)
    if not counted(ip) or not browser(ua):
        return
    if ip and ip != "-":
        h = _hash(state, day, ip, ua, name)
        if not _first(state, "dl", day, state.setdefault("_seen_dl", {}).setdefault(day, []), h):
            return
    d = _day(stats, day)
    _bump(d["downloads"], platform_of(name))
    _bump(d.setdefault("files", {}), name[:120])
    if visits is not None and ip and ip != "-":
        t = _dl_epoch(line)
        vid = _hash(state, day, ip, ua)
        visit = visits.open_visit(day, vid, t)
        if visit is None:
            if visits.full(day):                # counted above; no visit of its own
                return
            visit = _new_visit(visits, day, vid, t, ip, ua, db, _hash_before(state, day, ip, ua))
        if len(visit["downloads"]) < MAX_VISIT_DOWNLOADS:
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
                packed = resp.read(GEO_MAX + 1)
            # Never more than a country database can be, packed or unpacked:
            # whatever answers in its place cannot fill the memory or the disk.
            data = gzip.GzipFile(fileobj=io.BytesIO(packed)).read(GEO_MAX + 1) if len(packed) <= GEO_MAX else b""
            if not data or len(data) > GEO_MAX:
                raise ValueError("not the size of a country database")
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(GEO_DB), prefix=".tmp-")
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            from mmdb import Reader
            Reader(tmp)                         # a whole, readable file before it replaces the old
            os.chmod(tmp, 0o600)
            os.replace(tmp, GEO_DB)
            print(f"[stats] country database from {url}", flush=True)
            return
        except (OSError, ValueError, EOFError) as e:
            print(f"[stats] {url}: {e}", flush=True)
            with contextlib.suppress(OSError, UnboundLocalError):
                os.unlink(tmp)


# -- one run -----------------------------------------------------------------------

def _private_dir(path: str) -> None:
    """*path* is there, and the service's alone (0700): what is kept in it
    is nobody else's to read on this machine. One made wider before is
    narrowed; one that cannot be (another owner's) is left as it is."""
    os.makedirs(path, mode=0o700, exist_ok=True)
    with contextlib.suppress(OSError):
        if os.stat(path).st_mode & 0o077:
            os.chmod(path, 0o700)


@contextlib.contextmanager
def _locked():
    """One run at a time - cron's and the admin page's - or both would count
    the same new lines."""
    _private_dir(STATS_DIR)
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


#: What waits between runs, in this service's memory alone: the lines from
#: nginx (INBOX), today's and yesterday's visits as they happened (_LIVE),
#: who has been seen today and which downloads were counted (marks, never
#: written down).
INBOX = stats_inbox.Inbox()
_LIVE: dict = {}
_SEEN: dict = {}
_SEEN_DL: dict = {}
_SERVING: list = []
#: One run at a time in this process - the collector's thread and the admin
#: page's (and _locked() for another process).
_RUN = threading.RLock()


def _plain_os(system: str) -> str:
    """A system as a kept visit says it: its version, never a Linux's name."""
    return "Linux" if (system or "").startswith("Linux") else system or "Other"


def _base_lang(tag: str) -> str:
    """A browser's language without its country: "sv" for "sv-SE"."""
    m = re.match(r"[A-Za-z]{2,3}(?![A-Za-z])", tag or "")
    return m.group(0).lower() if m else ""


def _screen_kind(screen: str) -> str:
    if (screen or "").count("×") == 1:
        return screen_of(*screen.split("×"))[1]
    return screen or ""


def at_rest(v: dict, day: str, state: dict) -> dict:
    """A visit as it is written down. Its visitor named for this day alone
    (day_id: no two days of one visitor alike) beside the mark sealed
    (seal: nothing to anyone without the month's key, which is in memory
    alone), its start to the half hour and what happened in it to ten
    seconds from there, a screen by its kind, the browser's language without
    its country, a link's site and tag as site_of and source_of keep them -
    and nothing else: no address, no page that linked, no exact time, no
    mark of the device."""
    vid = v.get("id") or ""
    if "did" not in v:
        v["did"] = day_id(state, day, vid) if vid and not v.get("_lost") else vid
    if "w" not in v and vid and not v.get("_lost"):
        v["w"], v["e"] = seal(state, day, vid)
    try:
        start = int(v.get("start") or 0)
    except (TypeError, ValueError):
        start = 0
    s0 = start - start % COARSE

    def t(x) -> int:
        try:
            x = int(x)
        except (TypeError, ValueError):
            return s0
        return s0 + max(0, int(round((x - start) / STEP)) * STEP)

    def secs(x) -> int:
        try:
            return max(0, int(round(int(x) / STEP)) * STEP)
        except (TypeError, ValueError):
            return 0
    out = {"id": v["did"], "start": s0, "end": t(v.get("end", start)),
           "browser": v.get("browser") or "Other", "bv": str(v.get("bv") or "")[:4],
           "os": _plain_os(v.get("os") or ""), "device": v.get("device") or "",
           "country": v.get("country") or "",
           "returning": _small(v.get("returning"), 5),
           "pages": [[t(p[0]), p[1], secs(p[2])] for p in v.get("pages") or ()
                     if isinstance(p, list) and len(p) >= 3],
           "time": secs(v.get("time") or 0),
           "downloads": [[t(d[0]), d[1]] for d in v.get("downloads") or () if isinstance(d, list) and len(d) >= 2],
           "events": [[t(e[0]), *e[1:]] for e in v.get("events") or () if isinstance(e, list) and e]}
    if v.get("acts"):
        out["acts"] = [[t(a[0]), *a[1:]] for a in v["acts"] if isinstance(a, list) and a]
    for k in ("channel", "lang", "hl", "acted", "kind", "why", "w", "e"):
        if k in v:
            out[k] = v[k]
    if "channel" in v:
        out["ref"] = site_of(v.get("ref") or "")[:80]
        out["source"] = source_of(v.get("source") or "")
        out["blang"] = _base_lang(v.get("blang") or "")
        out["screen"] = _screen_kind(v.get("screen") or "")
    return out


def _small(n, most: int) -> int:
    try:
        return min(max(int(n or 0), 0), most)
    except (TypeError, ValueError):
        return 0


def _restore(v: dict, day: str, keys: MonthKeys) -> None:
    """A visit read back from its file: its visitor's mark unsealed while
    the month's key is kept - the visit then goes on, and counts, as the
    same visitor's - else it stays its day's name alone (_lost)."""
    vid = unseal(keys, day, v.get("w"), v.get("e"))
    v["did"] = v.get("id") or ""
    if vid:
        v["id"] = vid
    else:
        v["_lost"] = True


class Visits:
    """The visits of the days this run touches. Today's and yesterday's stay
    in memory between runs, as they happened (_LIVE); any other day is read
    from its file as it was kept (at_rest). How many different visitors a
    day had is its sketches' (sketch.py): "u" every visitor with a page,
    "n" those not judged robots, "p" those who showed they were people."""

    def __init__(self, sketches: dict, state: dict | None = None) -> None:
        self.days: dict = {}
        self.upd_days: dict = {}
        self.sketches = sketches
        self.state = state if state is not None else {}
        self._fields: dict = {}
        self._by_id: dict = {}          # day -> {visitor: its visits, in the day's order}

    def day(self, day: str) -> list:
        if day not in self.days:
            if day in _LIVE:
                got = _LIVE[day]
            else:
                got = [v for v in _load_list(os.path.join(VISITS_DIR, day + ".json")) if isinstance(v, dict)]
                keys = _keys(self.state, day)
                for v in got:
                    _restore(v, day, keys)
            self.days[day] = got
            by: dict = {}
            for v in got:
                by.setdefault(v.get("id"), []).append(v)
            self._by_id[day] = by
        return self.days[day]

    def add(self, day: str, v: dict) -> None:
        """A new visit at the end of its day (and under its visitor)."""
        self.day(day).append(v)
        self._by_id[day].setdefault(v["id"], []).append(v)

    def open_visit(self, day: str, vid: str, t: int):
        # The visitor's own visits of the day, newest first - found by the
        # visitor, not by going through every visit of the day for each line.
        self.day(day)
        for v in reversed(self._by_id[day].get(vid, ())):
            if t - v["end"] <= VISIT_GAP and t >= v["start"] - 60:
                return v
        return None

    def full(self, day: str) -> bool:
        """Whether the day keeps as many visits as a day may (MAX_DAY_VISITS)."""
        return len(self.day(day)) >= MAX_DAY_VISITS

    def field(self, day: str, kind: str) -> int:
        if (day, kind) not in self._fields:
            self._fields[(day, kind)] = sketch.decode((self.sketches.get(day) or {}).get(kind))
        return self._fields[(day, kind)]

    def put_field(self, day: str, kind: str, field: int) -> None:
        self._fields[(day, kind)] = field
        entry = self.sketches.setdefault(day, {})
        entry[kind] = sketch.encode(field)

    def first_today(self, day: str, vid: str) -> bool:
        """Whether this is the visitor's first page of the day - then it is
        counted in the day's sketch. In memory for the day; after a restart
        the sketch tells it, while the month's key is the same."""
        seen = _SEEN.setdefault(day, set())
        if vid in seen:
            return False
        seen.add(vid)
        u = self.field(day, "u")
        if sketch.has(u, vid):
            return False
        self.put_field(day, "u", sketch.add(u, vid))
        return True

    def before(self, day: str, vid: str, earlier: str = "") -> int:
        """On how many earlier days this visitor came: in this month by its
        mark, in the month before by *earlier* (its mark under that month's
        key) - each day's sketch asked; before that it cannot be told, those
        days are counts alone."""
        n = 0
        month, prev = _month(day), _month_before(_month(day))
        for d in self.sketches:
            if d >= day:
                continue
            mark = vid if _month(d) == month else earlier if _month(d) == prev else ""
            if mark and sketch.has(self.field(d, "u"), mark):
                n += 1
        return n

    def updates(self, day: str) -> list:
        """The app's own updates of a day, for the visitor log - kept apart
        from the visits, so nothing that counts visits ever sees them."""
        if day not in self.upd_days:
            self.upd_days[day] = _load_list(os.path.join(UPDATES_DIR, day + ".json"))
        return self.upd_days[day]

    def save(self, today: date | None = None) -> None:
        """Every day touched written as it is kept (at_rest); today's and
        yesterday's kept on in memory as they are, the rest let go."""
        if self.days:
            _private_dir(VISITS_DIR)
            for day, lst in self.days.items():
                _save(os.path.join(VISITS_DIR, day + ".json"), [at_rest(v, day, self.state) for v in lst])
        if self.upd_days:
            _private_dir(UPDATES_DIR)
            for day, lst in self.upd_days.items():
                _save(os.path.join(UPDATES_DIR, day + ".json"), lst)
        today = today or datetime.now().astimezone().date()
        near = {today.isoformat(), (today - timedelta(days=1)).isoformat()}
        _LIVE.clear()
        _LIVE.update({d: lst for d, lst in self.days.items() if d in near})
        for gone in [d for d in _SEEN if d not in near]:
            del _SEEN[gone]


def here_now(window: int, now: float | None = None) -> int | None:
    """Different visitors seen in the last *window* seconds, from the visits
    in memory - None where this process does not collect (a script)."""
    if not _SERVING:
        return None
    now = time.time() if now is None else now
    with _RUN:
        ids = {v.get("id") for lst in _LIVE.values() for v in lst
               if v.get("kind") != "robot" and (v.get("pages") or v.get("downloads"))
               and now - (v.get("end") or 0) <= window}
    return len(ids)


def _new_visit(visits: Visits, day: str, vid: str, t: int, ip: str, ua: str, db,
               earlier: str = "") -> dict:
    name, version = browser_of(ua)
    v = {"id": vid, "start": t, "end": t, "browser": name, "bv": version,
         "os": os_version_of(ua), "device": device_of(ua),
         "country": (db.country(ip) if db is not None else "") or "",
         "returning": visits.before(day, vid, earlier), "pages": [], "time": 0,
         "downloads": [], "events": []}
    visits.add(day, v)
    return v


def _page_line(stats: dict, state: dict, r: dict, db, visits: Visits) -> None:
    ua, ip = (r.get("a") or "")[:UA_MAX], r.get("ip") or ""
    if not counted(ip):
        return
    day = (r.get("t") or "")[:10]
    if len(day) != 10:
        return
    url = _split(r.get("u") or "")
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
    vid = _hash(state, day, ip, ua)
    d = _day(stats, day)
    ev = q.get("ev", "view")
    ev = SUPPORT_EVENTS.get(ev, ev)
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
        # A payment is counted per day only, never on a visit: the visit's
        # time, country and browser beside Stripe's own record of who paid
        # would tie the two together. Opening the dialog is the visit's.
        if visit is not None and ev == "donate" and len(visit["events"]) < MAX_VISIT_EVENTS:
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
    site, source = site_of(host)[:80], source_of(src)
    if ref_page:
        ref_page = (site + ref_page[len(host):])[:200]   # the page under its site's registered name
    if visit is None and visits.full(day):
        # The day keeps as many visits as a day may: this one is its page
        # view above and nothing more (said once a run, in collect).
        d["capped"] = d.get("capped", 0) + 1
        return
    if visit is None or "channel" not in visit:  # a new visit, or one that began with a download
        visit = visit or _new_visit(visits, day, vid, t, ip, ua, db, _hash_before(state, day, ip, ua))
        visit.update(channel=channel_of(host, src), ref=site, source=source,
                     lang=q.get("l", "")[:5], blang=_base_lang(q.get("bl", "")))
        if ref_page:
            visit["ref_page"] = ref_page        # in memory, for robot_share; never kept
        visit["screen"] = screen_of(q.get("w", ""), q.get("h", ""))[1]
        _bump(d.setdefault("visit_channels", {}), visit["channel"])
        if visit["screen"]:
            _bump(d.setdefault("screens", {}), visit["screen"])
    if q.get("hl") == "1":
        visit["hl"] = True
    if len(visit["pages"]) < MAX_VISIT_PAGES:
        visit["pages"].append([t, page, 0])
    visit["end"] = max(visit["end"], t)

    if not visits.first_today(day, vid):
        return
    d["visitors"] += 1                          # the visitor's first page today
    if visit["returning"]:
        d["returning"] = d.get("returning", 0) + 1
    _bump(d["channels"], channel_of(host, src))
    if site:
        _bump(d["refs"], site)
    if source:
        _bump(d["sources"], source)
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
        if v.get("screen"):
            add("screens", _screen_kind(v["screen"]))
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
        add("sources", first.get("source") or "")
        add("langs", lang if re.fullmatch(r"[a-z]{2,3}", lang) else "en")
        add("os", _os_family(first.get("os") or ""))
        add("os_versions", first.get("os") or "")
        add("devices", first.get("device") or "")
        add("browsers", first.get("browser") or "")
        add("browser_versions", f"{first.get('browser') or ''} {first.get('bv') or ''}".strip())
        add("countries", first.get("country") or "unknown")
    return rb, sorted(people), sorted(robots)


def _visit_totals(stats: dict, visits: Visits, newest: dict) -> None:
    """A touched day's visits judged, then its visits, bounces (one page,
    under ten seconds) and time looked at, without the robots - counted again
    as visits grow. Time and bounces only from the visits whose time is known
    (timed): a page that never said how long it was looked at - closed
    without a word, as phones do, or its message blocked - is not a visit of
    no seconds. What the robots added is kept apart in "robots"; who was a
    person and who not goes into the day's sketches ("n", "p")."""
    for day, lst in visits.days.items():
        for v in lst:
            v["kind"], v["why"] = judge(v, newest)
        rb, people, robots = robot_share(lst)
        # Only visitors this run knows by their mark: one whose month's key
        # went (a restart) is in the day's sketches already, as it was.
        known = {v["id"] for v in lst if v.get("pages") and not v.get("_lost")}
        not_robots = known - set(robots)
        visits.put_field(day, "n", visits.field(day, "n") | sketch.of(not_robots))
        visits.put_field(day, "p", visits.field(day, "p") | sketch.of(p for p in people if p in known))
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


def _lines(path: str, state: dict, key: str) -> list:
    """What is left of a log file of before (STATS_LOG, DL_LOG): nginx hands
    the lines to this service now, but until the old files are deleted
    (the server's runbook) their last lines are counted too - once, from where the
    last run stopped."""
    return _read_new(path, state.setdefault("logs", {}).setdefault(key, {}))


#: Lines dropped uncounted because swap kept what it was given (swap_state).
SWAP_HELD = {"lines": 0, "said": False}


def _swap_holds() -> bool:
    """Swap that keeps what it is given ("plain"): the run stops before a
    month key is made or read, or a line - each an address - is looked at;
    the lines waiting are dropped, uncounted, and said as a number. Swap
    that could not be asked ("unknown") is warned about on the admin page,
    as before - a jail that cannot ask its host would otherwise count
    nothing ever."""
    return swap_state()["state"] == "plain"


def collect(with_fetch: bool = False, lines: list | None = None) -> None:
    """One run: the lines waiting in memory (INBOX, or *lines*: [(tag, line)])
    counted, the touched days' visits judged and written as they are kept,
    and what has aged made counts alone (_forget_old). Not while swap keeps
    what it is given (_swap_holds): then nothing is counted."""
    stats_path = os.path.join(STATS_DIR, "stats.json")
    state_path = os.path.join(STATS_DIR, "state.json")
    if _swap_holds():
        if lines is None:
            lines, _dropped = INBOX.take()
        SWAP_HELD["lines"] += len(lines)
        del lines
        if not SWAP_HELD["said"]:
            SWAP_HELD["said"] = True
            print("[stats] swap keeps what it is given - nothing is counted until it is "
                  "encrypted (the server's runbook); the lines that come are dropped", flush=True)
        return
    SWAP_HELD["said"] = False
    with _RUN, _locked():
        today = datetime.now().astimezone().date()
        IGNORED[:] = _ignored_nets()
        stats = _load(stats_path, {"days": {}})
        state = _load(state_path, {"logs": {}})
        # The day-salted hashes of long before, the key kept for 25 months
        # after them and the marks of the downloads counted: gone.
        for gone in ("salts", "seen", "secret", "seen_dl"):
            state.pop(gone, None)
        state["_keys"] = keys_now(today)
        state["_seen_dl"] = _SEEN_DL
        # The sketches where the key is (sketches_file), never on a disk.
        sketches_path = sketches_file()
        sketches = _load(sketches_path, {}) if sketches_path else _SKETCHES
        _sketches_off_disk(sketches)
        _migrate(state, sketches)
        _old_rows_off(stats)
        visits = Visits(sketches, state)
        if with_fetch:
            fetch_geo(state)
        db = geo()
        if not os.path.isdir(UPDATES_DIR):
            _seed_updates(state, visits)
        dropped = 0
        if lines is None:
            lines, dropped = INBOX.take()
        page_lines = _lines(STATS_LOG, state, "stats") + [ln for tag, ln in lines if tag == "iptv_stats"]
        dl_lines = _lines(DL_LOG, state, "downloads") + [ln for tag, ln in lines if tag == "iptv_dl"]

        # A line is whatever a visitor's browser - or anyone's script - sent.
        # One that cannot be read is passed over and said as a number: it
        # must never stop the run, or one request stops the statistics.
        passed: dict = {}
        for line in page_lines:
            try:
                r = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if isinstance(r, dict):
                try:
                    _page_line(stats, state, r, db, visits)
                except Exception as e:  # noqa: BLE001 - whatever it is, the next line is read
                    _bump(passed, type(e).__name__)
        for line in dl_lines:
            try:
                _download(stats, line, state, visits, db)
            except Exception as e:  # noqa: BLE001 - as above
                _bump(passed, type(e).__name__)
        del lines, page_lines, dl_lines         # the addresses: out of memory with the run
        if passed:
            # Only how many and what kind: the line itself holds an address.
            print(f"[stats] {sum(passed.values())} line(s) could not be read and were passed over ("
                  + ", ".join(f"{k} {v}" for k, v in sorted(passed.items())) + ")", flush=True)
        if dropped:
            print(f"[stats] {dropped} line(s) came while {stats_inbox.MAX_BYTES >> 20} MB of them "
                  "waited already and were dropped", flush=True)
        for day, lst in visits.days.items():
            if len(lst) >= MAX_DAY_VISITS:
                print(f"[stats] {day}: {len(lst)} visits kept, as many as a day may (STATS_MAX_DAY_VISITS) - "
                      f"the day's later ones are counted as page views only", flush=True)
        # Days kept from before visits were judged are judged once, too.
        for f in glob.glob(os.path.join(VISITS_DIR, "*.json")):
            day = os.path.basename(f)[:10]
            if "p" not in (sketches.get(day) or {}):
                visits.day(day)
        newest = state.setdefault("newest", {})
        learn_newest(newest, visits)
        _visit_totals(stats, visits, newest)

        for old in sorted(_SEEN_DL)[:-2]:       # a download's day can only still get lines for a day
            _SEEN_DL.pop(old, None)
        frozen_path = os.path.join(STATS_DIR, "uniques-months.json")
        frozen = _load(frozen_path, {})
        if freeze_months(sketches, frozen, today):
            _save(frozen_path, frozen)
        # A day's list of sites or files stays short, so the years stay small -
        # a flood of made-up referrers cannot grow the file without end.
        for d in stats["days"].values():
            # The page that linked is never kept: its path can name a person
            # (a profile, a shared link) where its site does not. Days kept
            # before this lose theirs.
            d.pop("ref_pages", None)
            (d.get("robots") or {}).get("lists", {}).pop("ref_pages", None)
            for k in ("refs", "files", "sources", "update_files", "update_from"):
                _keep_top(d, k, KEEP_PER_DAY)
            # Languages, systems and browser versions are few in the world;
            # only made-up ones could make these long.
            for k in ("langs", "os_versions", "browser_versions"):
                _keep_top(d, k, KEEP_WIDE)
        stats["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        stats["geo"] = db is not None
        visits.save(today)
        _drop_payments(state)
        _updates_to_the_hour(state)
        _forget_old_updates(state)
        _forget_old(stats)
        sweep_left(today)
        _say_address_logs()
        if sketches_path:
            _keep_sketches(sketches_path, sketches)
        _save(stats_path, stats)
        for k in [k for k in state if k.startswith("_")]:
            state.pop(k)                        # the keys and marks: memory's alone
        _save(state_path, state)


def _migrate(state: dict, sketches: dict) -> None:
    """Once, from how the statistics were kept before: the month keys' file
    deleted unread (from now on a key is in memory alone), the lists of
    visitors' marks (uniques.json, kinds.json) made the days' sketches and
    deleted, and every visit in detail written as it is kept now (at_rest:
    a day's name for its visitor, its mark sealed, its times coarse)."""
    if state.get("kept_since") == 2:
        return
    _gone(os.path.join(STATS_DIR, "visitor-keys.json"))
    uniques_path = os.path.join(STATS_DIR, "uniques.json")
    kinds_path = os.path.join(STATS_DIR, "kinds.json")
    uniques, kinds = _load(uniques_path, {}), _load(kinds_path, {})
    if uniques or kinds:
        for day in {*uniques, *kinds}:
            marks = [m for m in uniques.get(day) or () if isinstance(m, str)]
            entry = sketches.setdefault(day, {})
            entry["u"] = sketch.encode(sketch.decode(entry.get("u")) | sketch.of(marks))
            k = kinds.get(day)
            if isinstance(k, dict):
                robots = set(k.get("r") or ())
                entry["n"] = sketch.encode(sketch.of(m for m in marks if m not in robots))
                entry["p"] = sketch.encode(sketch.of(m for m in k.get("p") or () if isinstance(m, str)))
    for path in (uniques_path, kinds_path):
        _gone(path)
    for f in glob.glob(os.path.join(VISITS_DIR, "*.json")):
        day = os.path.basename(f)[:10]
        try:
            date.fromisoformat(day)
        except ValueError:
            continue
        kept = []
        for v in _load_list(f):
            if not isinstance(v, dict):
                continue
            if "w" not in v:
                v = dict(v)
                v.pop("did", None)
            kept.append(at_rest(v, day, state))
        _save(f, kept)
    state["kept_since"] = 2


def _keep_top(d: dict, k: str, most: int) -> None:
    """A day's list *k* kept to its *most* largest, the rest summed as
    "other"."""
    lst = d.get(k) or {}
    if len(lst) > most:
        top = sorted(lst.items(), key=lambda kv: -kv[1])
        d[k] = dict(top[:most - 1])
        d[k]["other"] = d[k].get("other", 0) + sum(v for _, v in top[most - 1:])


def _forget_old_updates(state: dict, now: float | None = None) -> None:
    """The newest updates the admin page's live line shows (state.json's
    recent_updates) are kept as long as the updates themselves: one older
    than UPDATES_DAYS goes, however few came after it."""
    cut = (time.time() if now is None else now) - UPDATES_DAYS * 86400
    recent = state.get("recent_updates")
    if isinstance(recent, list):
        state["recent_updates"] = [r for r in recent if not (
            isinstance(r, list) and r and isinstance(r[0], (int, float)) and 0 < r[0] < cut)]


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


#: A day's visits per way in, kept for good once they are gone: visits,
#: those that read more than one page, seconds looked at (of those timed),
#: timed, downloaded, opened the support dialog (funnel_of).
FUNNEL = ("visits", "more", "time", "timed", "downloaded", "donate")


def _funnel_add(out: dict, channel: str, pages: int, secs: int, timed: bool,
                downloaded: bool, donate: bool) -> None:
    row = out.setdefault(channel or "", [0] * len(FUNNEL))
    row[0] += 1
    row[1] += pages > 1
    row[2] += max(0, int(secs or 0)) if timed else 0
    row[3] += bool(timed)
    row[4] += bool(downloaded)
    row[5] += bool(donate)


def funnel_of(visits: list) -> dict:
    """A day's visits as counts per way in ({channel: FUNNEL}) - what the
    admin page's "by way in" reads of a day whose visits are gone. Likely
    robots left out, as everywhere."""
    out: dict = {}
    for v in visits:
        if not isinstance(v, dict) or not v.get("pages") or v.get("kind") == "robot":
            continue
        secs = v.get("time") or 0
        _funnel_add(out, v.get("channel") or "", len(v["pages"]), secs, secs > 0,
                    bool(v.get("downloads")),
                    any(isinstance(e, list) and e[1:2] == ["donate"] for e in v.get("events") or ()))
    return out


def _old_rows_off(stats: dict) -> None:
    """Once, from how visits were kept before (until November 2026): the
    anonymous visits of STATS_DIR/visits-anon made their days' counts
    (funnel_of) and deleted, folder and all; the month's own list of the
    rarer names - its days counted them in "other" already - dropped."""
    days = stats.setdefault("days", {})
    stats.pop("rest", None)
    for f in sorted(glob.glob(os.path.join(ANON_DIR, "*.json"))):
        month = _load(f, {})
        rows = month.get("visits") if isinstance(month, dict) else None
        counted: dict = {}
        for a in rows if isinstance(rows, list) else ():
            if not isinstance(a, dict) or not a.get("pages") or a.get("kind") == "robot":
                continue
            day = str(a.get("day") or "")[:10]
            if day not in days or "funnel" in days[day]:
                continue
            _funnel_add(counted.setdefault(day, {}), a.get("channel") or "", int(a.get("pages") or 0),
                        int(a.get("time") or 0), bool(a.get("timed", (a.get("time") or 0) > 0)),
                        bool(a.get("downloaded")), bool(a.get("donate")))
        for day, funnel in counted.items():
            days[day]["funnel"] = funnel
        _gone(f)
    _gone_folder(ANON_DIR)


def _drop_payments(state: dict) -> None:
    """Once: a payment marked on a visit before payments were counted per
    day only, taken off every visit kept."""
    if state.get("payments_off_visits"):
        return
    for f in glob.glob(os.path.join(VISITS_DIR, "*.json")):
        lst = _load_list(f)
        changed = False
        for v in lst:
            evs = v.get("events") if isinstance(v, dict) else None
            if isinstance(evs, list) and any(isinstance(e, list) and e[1:2] == ["donated"] for e in evs):
                v["events"] = [e for e in evs if not (isinstance(e, list) and e[1:2] == ["donated"])]
                changed = True
        if changed:
            _save(f, lst)
    state["payments_off_visits"] = 1


def _updates_to_the_hour(state: dict) -> None:
    """Once: the updates kept from before at the second they came, put at
    their hour (the newer ones are kept so from the start)."""
    if state.get("updates_hourly"):
        return
    for f in glob.glob(os.path.join(UPDATES_DIR, "*.json")):
        rows = _load_list(f)
        if any(isinstance(u, dict) and u.get("t") and u["t"] % 3600 for u in rows):
            for u in rows:
                if isinstance(u, dict) and u.get("t"):
                    u["t"] = _to_hour(u["t"])
            _save(f, rows)
    for r in state.get("recent_updates") or []:
        if isinstance(r, list) and r and isinstance(r[0], int):
            r[0] = _to_hour(r[0])
    state["updates_hourly"] = 1


def week_of(day: str) -> str:
    """The Monday a day's week starts on (the admin page's weeks)."""
    d = date.fromisoformat(day)
    return (d - timedelta(days=d.weekday())).isoformat()


def _net_field(entry: dict) -> int:
    """A day's visitors without those judged robots: its "n", or its "u"
    for a day counted before visits were judged."""
    return sketch.decode(entry.get("n") if "n" in entry else entry.get("u"))


def freeze_months(sketches: dict, frozen: dict, today: date) -> int:
    """The months before the one before this: counted into *frozen* -
    {"days", "unique", "weeks", "judged", "people", "robots"}, as the admin
    page counts them from the sketches - and their sketches deleted. A month
    counted before keeps its numbers. Returns how many months went."""
    keep_from = _month_before(today.strftime("%Y-%m"))
    months = sorted({d[:7] for d in sketches if d[:7] < keep_from})
    for m in months:
        days = sorted(d for d in sketches if d[:7] == m)
        if m not in frozen:
            entries = {d: sketches[d] if isinstance(sketches[d], dict) else {} for d in days}
            weeks: dict = {}
            for d in days:
                weeks[week_of(d)] = weeks.get(week_of(d), 0) | _net_field(entries[d])
            judged = [d for d in days if "p" in entries[d]]
            seen = sketch.union(_net_field(entries[d]) for d in days)
            all_judged = sketch.union(sketch.decode(entries[d].get("u")) for d in judged)
            net_judged = sketch.union(_net_field(entries[d]) for d in judged)
            frozen[m] = {"days": [d for d in days if "u" in entries[d]], "unique": sketch.count(seen),
                         "weeks": {w: sketch.count(f) for w, f in sorted(weeks.items())},
                         "judged": judged,
                         "people": sketch.count(sketch.union(sketch.decode(entries[d].get("p")) for d in judged)),
                         "robots": max(0, sketch.count(all_judged) - sketch.count(net_judged))}
        for d in days:
            sketches.pop(d, None)
    return len(months)


#: The lists of a day that tell who visited (countries, languages, versions,
#: sites, tags): kept with no name fewer than K_DAY once the day's visits are
#: gone - the rest is "other" (fold_day).
FOLD = ("countries", "langs", "os_versions", "browser_versions", "refs", "sources")


def fold_day(stats: dict, day: str) -> None:
    """A day's lists in FOLD, kept for good: the likely robots' part taken
    out first (as the admin page takes it), then a name counted fewer than
    K_DAY times - fewer than five different visitors brought it that day -
    said as "other". It is never added up with other days under its name:
    one person on five days is not five people. A quiet day's few visitors
    are then no longer a country, a language and a browser version side by
    side. Once."""
    d = stats["days"].get(day)
    if not isinstance(d, dict) or d.get("folded"):
        return
    robot_lists = (d.get("robots") or {}).get("lists") or {}
    for k in FOLD:
        if k not in d and k not in robot_lists:
            continue
        lst = dict(d.get(k) or {})
        for name, n in (robot_lists.pop(k, None) or {}).items():
            if lst.get(name, 0) > n:
                lst[name] -= n
            else:
                lst.pop(name, None)
        kept: dict = {}
        moved = 0
        for name, n in lst.items():
            if name == "other" or n >= K_DAY:
                kept[name] = n
            else:
                moved += n
        if moved:
            kept["other"] = kept.get("other", 0) + moved
        d[k] = kept
    d["folded"] = 1


def _forget_old(stats: dict | None = None) -> None:
    """Visits of STATS_VISITS_DAYS days ago and older: their day's counts
    per way in kept (funnel_of), then the day's file deleted - no row of a
    visit stays. Today and the 6 days before it are the 7 kept, so a
    visit is gone 7 days after its day began, at the first run then. The
    day's lists of who came are folded with it (fold_day)."""
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=VISITS_DAYS)).isoformat()
    for f in sorted(glob.glob(os.path.join(VISITS_DIR, "*.json"))):
        day = os.path.basename(f)[:10]
        if day > cutoff:
            continue
        if stats is not None and day in (stats.get("days") or {}):
            stats["days"][day]["funnel"] = funnel_of(_load_list(f))
        _gone(f)
    if stats is not None:
        for day in sorted(stats.get("days") or {}):
            if day <= cutoff:
                fold_day(stats, day)
    # The app's updates of then: counted in stats.json already, so they go.
    gone_updates = (datetime.now(timezone.utc).date() - timedelta(days=UPDATES_DAYS)).isoformat()
    for f in glob.glob(os.path.join(UPDATES_DIR, "*.json")):
        if os.path.basename(f)[:10] <= gone_updates:
            _gone(f)


#: What should be gone but could not be deleted: {path: why}. Tried again
#: on every run (_gone, sweep_left), said in the log once per path and on
#: the admin page (trouble) until it is gone.
TROUBLE: dict = {}


def _gone(path: str) -> bool:
    """*path* deleted - or, where that fails, noted in TROUBLE, said once
    in the log and tried again on the next run (sweep_left). A file that is
    not there is gone."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as e:
        if path not in TROUBLE:
            print(f"[stats] could not delete {path} ({e.strerror or type(e).__name__}) - "
                  "tried again on every run until it is gone", flush=True)
        TROUBLE[path] = e.strerror or type(e).__name__
        return False
    TROUBLE.pop(path, None)
    return True


def _gone_folder(path: str) -> None:
    """An emptied folder of before removed; one that still holds a file
    stays until that file is gone (the file itself is in TROUBLE)."""
    with contextlib.suppress(FileNotFoundError):
        try:
            os.rmdir(path)
        except OSError as e:
            if e.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                _gone(path)


def left_behind(today: date | None = None) -> list:
    """Every file that should no longer exist and still does: the keys,
    marks and sketches kept on the disk before (visitor-keys.json,
    uniques.json, kinds.json, sketches.json), the anonymous visits of before
    (visits-anon), a day's visits past VISITS_DAYS and an update past
    UPDATES_DAYS. Asked after every run, never assumed."""
    today = today or datetime.now(timezone.utc).date()
    out = [p for p in (os.path.join(STATS_DIR, n) for n in
                       ("visitor-keys.json", "uniques.json", "kinds.json", "sketches.json"))
           if os.path.lexists(p)]
    out += sorted(glob.glob(os.path.join(ANON_DIR, "*")))
    cutoff = (today - timedelta(days=VISITS_DAYS)).isoformat()
    out += [f for f in sorted(glob.glob(os.path.join(VISITS_DIR, "*.json")))
            if _is_day(os.path.basename(f)[:10]) and os.path.basename(f)[:10] <= cutoff]
    updates = (today - timedelta(days=UPDATES_DAYS)).isoformat()
    out += [f for f in sorted(glob.glob(os.path.join(UPDATES_DIR, "*.json")))
            if _is_day(os.path.basename(f)[:10]) and os.path.basename(f)[:10] <= updates]
    return out


def _is_day(text: str) -> bool:
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def sweep_left(today: date | None = None) -> list:
    """What left_behind finds deleted once more; what still stays is
    returned (and in TROUBLE). What is no longer there is forgotten."""
    for path in list(TROUBLE):
        if not os.path.lexists(path):
            TROUBLE.pop(path, None)
    stays = [p for p in left_behind(today) if not _gone(p)]
    _gone_folder(ANON_DIR)
    return stays


#: How long swap_state's answer is kept: swap is set up when a server
#: starts, not while it runs.
SWAP_TTL = 3600
_SWAP: dict = {}


def swap_listed(text: str) -> list:
    """The devices in a swap listing - Linux's /proc/swaps or FreeBSD's
    swapctl -l: a heading, then a device's path first on each line."""
    return [line.split()[0] for line in text.splitlines()[1:]
            if line.split() and line.split()[0].startswith("/")]


def _swap_devices() -> list | None:
    """The swap devices in use, as the system names them - None where it
    cannot be asked. Linux's /proc/swaps, else FreeBSD's swapctl -l (a jail
    reads it too)."""
    try:
        with open("/proc/swaps", encoding="utf-8") as f:
            return swap_listed(f.read())
    except OSError:
        pass
    try:
        out = subprocess.run(["swapctl", "-l"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return swap_listed(out.stdout)


def _linux_encrypted(device: str, sys_root: str = "/sys") -> bool:
    """Whether a Linux swap device keeps nothing readable after a restart:
    zram with no device it writes back to, a dm-crypt mapping (its uuid
    starts CRYPT-), or a mapping (LVM, RAID) whose every part is one - as
    the app's core/memory_safe.encrypted_block. A swap file: not known, so
    not counted."""
    return _block_encrypted(os.path.basename(os.path.realpath(device)), sys_root)


def _block_encrypted(name: str, sys_root: str, depth: int = 0) -> bool:
    if depth > 8 or not name or "/" in name or name.startswith("."):
        return False
    block = os.path.join(sys_root, "class", "block", name)

    def read(rel: str) -> str:
        try:
            with open(os.path.join(block, rel), encoding="ascii", errors="replace") as f:
                return f.read(4096).strip()
        except OSError:
            return ""
    if name.startswith("zram"):
        # No backing_dev at all: a kernel without zram writeback, nothing of
        # it reaches a disk. One there that cannot be read is not counted.
        backing = os.path.join(block, "backing_dev")
        if not os.path.lexists(backing):
            return True
        try:
            with open(backing, encoding="ascii", errors="replace") as f:
                return f.read(4096).strip() in ("", "none")
        except OSError:
            return False
    if read("dm/uuid").startswith("CRYPT-"):
        return True
    try:
        parts = sorted(os.listdir(os.path.join(block, "slaves")))
    except OSError:
        return False
    return bool(parts) and all(_block_encrypted(p, sys_root, depth + 1) for p in parts)


def swap_state(now: float | None = None) -> dict:
    """Whether what this service keeps in memory - the month keys, the
    sketches, a folder in memory (STATS_KEYS_DIR) - could reach a disk
    through the system's swap: {"state": ..., "plain": [devices]}.
    "none": no swap. "encrypted": every swap device keeps nothing readable
    once the computer restarts (FreeBSD's .eli with a one-time key, Linux's
    dm-crypt or zram). "plain": a swap device that keeps what it is given -
    said on the admin page and in the log (the server's runbook: encrypted swap).
    "unknown": the system could not be asked."""
    now = time.time() if now is None else now
    if _SWAP and now - _SWAP.get("at", 0) < SWAP_TTL:
        return dict(_SWAP["answer"])
    devices = _swap_devices()
    if devices is None:
        answer: dict = {"state": "unknown", "plain": []}
    elif not devices:
        answer = {"state": "none", "plain": []}
    else:
        plain = [d for d in devices if not (d.endswith(".eli") or _linux_encrypted(d))]
        answer = {"state": "plain" if plain else "encrypted", "plain": plain}
    _SWAP.clear()
    _SWAP.update(at=now, answer=answer)
    return dict(answer)


#: A log of before written this recently (seconds): nginx still writes it.
STILL_WRITTEN = 3600
_SAID_LOGS: set = set()


def address_logs(now: float | None = None) -> list:
    """The logs of before that held each visitor's address (STATS_LOG,
    DL_LOG: nginx's own, from before the socket) and the copies newsyslog
    turned them over into, still on this machine - none should be (the
    privacy policy). Each "written lately" when it changed within
    STILL_WRITTEN - its access_log line is still in nginx's file - else
    "holds addresses"; "could not be asked" when the file cannot be looked
    at. They are nginx's files: deleting them is the server's runbook's,
    not this service's. Asked after every run, never assumed."""
    now = time.time() if now is None else now
    out = []
    for base in (STATS_LOG, DL_LOG):
        if not base:
            continue
        for p in [base] + sorted(set(glob.glob(glob.escape(base) + ".*")) - {base}):
            try:
                st = os.stat(p)
            except FileNotFoundError:
                continue
            except OSError:
                out.append({"what": "log", "path": p, "why": "could not be asked"})
                continue
            if os.path.isdir(p):
                continue
            why = "written lately" if now - st.st_mtime < STILL_WRITTEN else "holds addresses"
            out.append({"what": "log", "path": p, "why": why})
    return out


def _say_address_logs() -> None:
    """Each log of before still there said in the service's log - once for
    each path and what is wrong with it."""
    for t in address_logs():
        if (t["path"], t["why"]) not in _SAID_LOGS:
            _SAID_LOGS.add((t["path"], t["why"]))
            print(f"[stats] {t['path']}: a log of before that holds visitors' addresses is still "
                  f"there ({t['why']}) - the server's runbook says how it goes", flush=True)


def trouble() -> list:
    """What the admin page warns about: each file that should be gone and is
    not, with why - the logs of before with addresses among them
    (address_logs) - and swap that keeps what it is given, or that could
    not be asked (nothing is taken on trust)."""
    out = [{"what": "delete", "path": p, "why": why} for p, why in sorted(TROUBLE.items())]
    out += address_logs()
    swap = swap_state()
    if swap["state"] == "plain":
        out.append({"what": "swap", "path": ", ".join(swap["plain"]), "why": "not encrypted"})
    elif swap["state"] == "unknown":
        out.append({"what": "swap", "path": "", "why": "could not be asked"})
    return out


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
    os.chmod(tmp, 0o600)                        # the service's alone, like the folder
    os.replace(tmp, path)


if __name__ == "__main__":
    os.umask(0o077)             # whatever a run makes is the service's alone to read
    if "--once" not in sys.argv[1:]:
        # The lines come to donate.py's memory now and its own thread counts
        # them; a run here would know none of them (the server's runbook: no cron line).
        print("[stats] counted by the donate service itself now - nothing to do here "
              "(--once runs one count in this process, for a test)", flush=True)
        raise SystemExit(0)
    collect(with_fetch=True)
