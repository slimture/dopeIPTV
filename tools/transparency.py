"""The code behind /transparency/ ("Privacy, behind the scenes"): what the
page shows of the app's and the site's own code, taken from that code.

    python3 tools/transparency.py --app PATH --ref v1.6.7 [--out site/transparency.json]

PATH is a checkout of the app at --ref (a tag; a commit while the page is
being written). The site's own code - donate/ and the few other files
CODE names - is this checkout's, at its commit (--site-ref, HEAD by
default), which must be on GitHub: every excerpt links to its lines there.
Nothing on the page is copied by hand:

  code      each block the page shows: the named functions, classes and
            assignments of a Python file (found with ast, so a moved
            function is still found), or the lines from a pattern in any
            other file - each with the comments right above
            it, the text, its first and last line and the address of those
            lines on GitHub at the pinned version
  examples  the page's before and after, made by running that code here:
            the report cleaned, the sync copy sealed, a message for the
            relay sealed, the update check's headers, a visit as the
            statistics keep it, the headers the front proxy's file adds to
            an answer
  tests     the app's own tests the page names, with their lines
  hosts     every host name the app's code names, each with where - and
            whether the page explains it (HOSTS below): a host the code
            names that HOSTS does not know stops the run, so a new one is
            never left off the page

The page's words are the languages' own (site/transparency/<lang>.html);
the code and the examples are the same in all of them. Run again for a new
version, read the page against it, then commit both.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import time

REPO = "https://github.com/slimture/dopeIPTV"

# What each part of the page shows. A block: (where, path, names) - "app" or
# "site", the file, and the top-level names (or Class.method) to take; or,
# or a pattern from a line to a line for any other file. No server's
# configuration: the page says what the servers keep, not how they are set up.
CODE: dict[str, list[dict]] = {
    "logins": [
        {"id": "stored", "repo": "app", "path": "dopeiptv/core/stores.py", "names": ["PlaylistStore._save"],
         "with": [{"repo": "app", "path": "dopeiptv/core/settings_files.py",
                   "names": ["FILE_MODE", "FOLDER_MODE", "_narrow", "keep_private"]}]},
        {"id": "log", "repo": "app", "path": "dopeiptv/providers/client.py",
         "from": r"^\s+# From here on these two strings are masked", "to": r"^\s+register_secrets\(username, password\)",
         "with": [{"repo": "app", "path": "dopeiptv/core/log.py",
                   "names": ["register_secrets", "redact", "redact_text", "_RedactFilter"]}]},
        {"id": "notprivate", "repo": "app", "path": "dopeiptv/core/settings_files.py",
         "names": ["ACCEPTED_KEY", "accepted_mark", "not_private"],
         "with": [{"repo": "app", "path": "dopeiptv/ui/mw_settings.py",
                   "names": ["_SettingsMixin._tell_settings_not_private",
                             "_SettingsMixin._ask_settings_open"]}]},
    ],
    "watching": [
        {"id": "tmdb", "repo": "app", "path": "dopeiptv/providers/metadata.py",
         "names": ["TmdbClient.fetch_details"]},
    ],
    "reports": [
        {"id": "scrub", "repo": "app", "path": "dopeiptv/core/report.py",
         "names": ["_IPV4", "_IPV6", "_MAC", "_EMAIL", "_SUPPORTER", "_KEYVAL", "_AUTH_LINE",
                   "_BEARER", "_HOST_PATH", "_SAID", "_JWT", "_PREFIXED", "_LONG", "_HOME_DIR",
                   "_PHONE", "_hide_value", "_not_an_address", "scrub", "personal_values"],
         "with": [{"repo": "app", "path": "dopeiptv/core/log.py", "names": ["URL_IN_TEXT"]}]},
        {"id": "send", "repo": "app", "path": "dopeiptv/core/report.py",
         "names": ["ENDPOINT", "_over_https", "_post", "send_status"],
         "with": [{"repo": "app", "path": "dopeiptv/core/build_check.py", "names": ["secure"]}]},
        {"id": "ask", "repo": "app", "path": "dopeiptv/ui/mw_report.py",
         "names": ["_ReportMixin.open_report"]},
        {"id": "answers", "repo": "app", "path": "dopeiptv/core/report.py",
         "names": ["REPLIES", "new_code", "code_hash", "fetch_replies"],
         "with": [{"repo": "app", "path": "dopeiptv/ui/mw_report.py",
                   "names": ["_ReportMixin._answers_reply"]}]},
        {"id": "server", "repo": "site", "path": "donate/feedback.py",
         "names": ["_URL", "_IPV4", "_IPV6", "_EMAIL", "_MAC", "_KEYVAL", "_AUTH_LINE", "_BEARER",
                   "_HOST_PATH", "_hide_value", "_SUPPORTER", "scrub", "accept"]},
        {"id": "limits", "repo": "site", "path": "donate/donate.py",
         "names": ["_forget_quiet", "_forget_loop", "_allowed"]},
    ],
    "sync": [
        {"id": "key", "repo": "app", "path": "dopeiptv/core/sync.py",
         "names": ["format_code", "new_code", "hkdf", "derive", "auth_of", "seal", "unseal"]},
        {"id": "aead", "repo": "app", "path": "dopeiptv/core/chacha20poly1305.py",
         "names": ["poly1305", "_pad16", "_tag", "encrypt", "decrypt"]},
        {"id": "stored", "repo": "app", "path": "dopeiptv/core/sync.py", "names": ["K_CODE", "code_of"],
         "with": [{"repo": "app", "path": "dopeiptv/core/settings_files.py",
                   "names": ["keep_private", "protect"]}]},
        {"id": "counter", "repo": "app", "path": "dopeiptv/core/sync.py",
         "names": ["_META", "_COUNTER", "counter", "_counted", "round_trip"]},
        {"id": "send", "repo": "app", "path": "dopeiptv/core/sync.py",
         "names": ["ENDPOINT", "AUTH_HEADER", "Remote.__init__", "Remote._call"],
         "with": [{"repo": "app", "path": "dopeiptv/core/build_check.py", "names": ["secure"]}]},
        {"id": "server", "repo": "site", "path": "donate/sync.py",
         "names": ["AUTH", "Refused", "_seal", "_check", "get", "put", "delete"],
         "with": [{"repo": "site", "path": "donate/donate.py", "names": ["Handler._sync"]}]},
    ],
    "together": [
        {"id": "key", "repo": "app", "path": "dopeiptv/core/together.py",
         "names": ["ALPHABET", "CODE_LEN", "OLD_LEN", "STRETCH", "_PAD", "new_code", "is_old_code",
                   "derive", "seal", "open_frame"]},
        {"id": "signed", "repo": "app", "path": "dopeiptv/core/together.py",
         "names": ["Identity", "member_of", "new_identity", "_signed", "encode",
                   "_signature_holds", "open_message"]},
        {"id": "replay", "repo": "app", "path": "dopeiptv/core/together.py",
         "from": r"^    def _make\(", "to": r"^        return msg$",
         "with": [{"repo": "app", "path": "dopeiptv/core/together.py",
                   "names": ["Room._timely", "Room._counted", "remembered"]},
                  {"repo": "app", "path": "dopeiptv/core/together.py",
                   "from": r"^    def receive\(", "to": r"^        if not self\._counted\(msg, now\):"}]},
        {"id": "backlog", "repo": "app", "path": "dopeiptv/core/together.py",
         "names": ["Link._poll_loop"]},
        {"id": "server", "repo": "site", "path": "donate/together.py", "names": ["Relay.post"],
         "with": [{"repo": "site", "path": "donate/donate.py", "names": ["Handler._together"]}]},
    ],
    "private": [
        {"id": "switch", "repo": "app", "path": "dopeiptv/core/privacy.py",
         "names": ["set_private", "private", "playback_started", "playback_stopped",
                   "playback_private", "private_key", "for_log"]},
        {"id": "stores", "repo": "app", "path": "dopeiptv/core/stores.py", "names": ["HistoryStore.add"],
         "with": [{"repo": "app", "path": "dopeiptv/services/resume.py", "names": ["ResumeStore.record"]}]},
        {"id": "tellers", "repo": "app", "path": "dopeiptv/ui/mw_trakt.py",
         "names": ["_TraktMixin._watch_accounts_start"]},
        {"id": "memory", "repo": "app", "path": "dopeiptv/core/memory_safe.py",
         "names": ["in_memory", "encrypted_block", "swap_safe", "stays_in_memory", "private_folder"]},
        {"id": "buffer", "repo": "app", "path": "dopeiptv/core/dvr_pool.py",
         "names": ["NoPrivateBuffer", "DvrPool.open"]},
        {"id": "reopen", "repo": "app", "path": "dopeiptv/ui/mw_privacy.py",
         "names": ["_PrivacyMixin.set_private_viewing"],
         "with": [{"repo": "app", "path": "dopeiptv/media/dvr_player.py",
                   "names": ["DvrPlayerMixin.private_reopen_needed"]},
                  {"repo": "app", "path": "dopeiptv/core/dvr_pool.py",
                   "names": ["DvrPool.on_disk_private"]}]},
        {"id": "cast", "repo": "app", "path": "dopeiptv/providers/cast_bridge.py",
         "names": ["private_cast_dir", "private_cap", "folder_bytes", "CastBridge.start",
                   "CastBridge._watch"]},
    ],
    "updates": [
        {"id": "headers", "repo": "app", "path": "dopeiptv/core/updates.py",
         "names": ["user_agent", "site_headers"],
         "with": [{"repo": "app", "path": "dopeiptv/core/build_check.py", "names": ["HEADER", "OFFICIAL", "headers"]}]},
        {"id": "signed", "repo": "app", "path": "dopeiptv/core/selfupdate.py",
         "names": ["signed_asset", "_expected_sha256", "download"]},
    ],
    "hosts": [
        {"id": "route", "repo": "app", "path": "dopeiptv/providers/cast_bridge.py", "names": ["lan_address"]},
    ],
    "builds": [
        {"id": "allowed", "repo": "app", "path": "dopeiptv/core/build_check.py", "names": ["kind", "allowed"]},
        {"id": "older", "repo": "app", "path": "dopeiptv/core/selfupdate.py",
         "names": ["refuse_older", "_get_https"],
         "with": [{"repo": "app", "path": "dopeiptv/core/updates.py", "names": ["offered"]}]},
        {"id": "site", "repo": "site", "path": "sync-releases.sh",
         "from": r"^    # ---- The signed checksum list", "to": r"^    fi$",
         "with": [{"repo": "site", "path": "donate/release_sums.py", "names": ["why_not"]}]},
    ],
    "website": [
        {"id": "visitor", "repo": "site", "path": "donate/stats_collect.py",
         "names": ["MonthKeys", "_hash", "day_id", "seal", "unseal", "at_rest", "swap_state"]},
        {"id": "sketch", "repo": "site", "path": "donate/sketch.py",
         "names": ["BITS", "spots", "add", "has", "count"]},
        {"id": "kept", "repo": "site", "path": "donate/stats_collect.py",
         "from": r"^KEEP_PER_DAY = ", "to": r"^UPDATES_DAYS = ",
         "with": [{"repo": "site", "path": "donate/stats_collect.py",
                   "from": r"^    if ev in \(\"donate\", \"donated\"\):", "to": r"^        return$"}]},
        {"id": "anon", "repo": "site", "path": "donate/stats_collect.py",
         "names": ["sketches_file", "freeze_months", "FUNNEL", "funnel_of", "FOLD", "fold_day", "_forget_old",
                   "_gone", "left_behind", "sweep_left"]},
    ],
}

# The app's tests the page names as proof, by function.
TESTS: dict[str, list[tuple[str, str]]] = {
    "reports": [("tests/test_report.py", n) for n in (
        "test_nothing_that_identifies_a_stream_or_a_person_is_left",
        "test_the_window_offers_scrubs_and_sends_only_on_send",
        "test_only_the_short_answers_go_with_feedback",
        "test_a_supporter_key_never_goes_in_a_report",
        "test_asking_for_answers_sends_only_hashes")],
}

# Every host the app's code names - what the page's two searches find
# (site/transparency/<lang>.html, part 4), and quoted IP addresses besides -
# and what it is to the page: "talks" - the
# app connects to it (when, the page says); "link" - only an address shown or
# opened in your browser; "name" - not a place the app goes (an example, a
# site yt-dlp is used for, an XML name). The page's own words explain each
# "talks" and "link" host by its group.
HOSTS: dict[str, tuple[str, str]] = {
    "dopeiptv.org": ("talks", "ours"),
    "api.github.com": ("talks", "github"),
    "github.com": ("talks", "github"),
    "api.themoviedb.org": ("talks", "tmdb"),
    "image.tmdb.org": ("talks", "tmdb"),
    "www.themoviedb.org": ("link", "tmdb"),
    "api.opensubtitles.com": ("talks", "opensubtitles"),
    "www.opensubtitles.com": ("link", "opensubtitles"),
    "api.trakt.tv": ("talks", "trakt"),
    "trakt.tv": ("talks", "trakt"),
    "auth.trakt.tv": ("link", "trakt"),
    "app.trakt.tv": ("link", "trakt"),
    "api.simkl.com": ("talks", "simkl"),
    "simkl.com": ("link", "simkl"),
    "plex.tv": ("talks", "plex"),
    "app.plex.tv": ("link", "plex"),
    "all.api.radio-browser.info": ("talks", "radio"),
    "api.radio-browser.info": ("talks", "radio"),
    "radio-browser.info": ("name", "radio"),
    "api.hdhomerun.com": ("talks", "hdhomerun"),
    "itunes.apple.com": ("talks", "itunes"),
    "test-streams.mux.dev": ("talks", "demo"),
    "devstreaming-cdn.apple.com": ("talks", "demo"),
    "bitdash-a.akamaihd.net": ("talks", "demo"),
    "1.1.1.1": ("talks", "dns"),
    "1.0.0.1": ("talks", "dns"),
    "9.9.9.9": ("talks", "dns"),
    "149.112.112.112": ("talks", "dns"),
    "8.8.8.8": ("talks", "dns"),
    "8.8.4.4": ("talks", "dns"),
    "solecraft.se": ("link", "ours"),
    "www.imdb.com": ("link", "imdb"),
    "mpv.io": ("link", "mpv"),
    "www.twitch.tv": ("name", "web"),
    "twitch.tv": ("name", "web"),
    "youtube.com": ("name", "web"),
    "youtube-nocookie.com": ("name", "web"),
    "kick.com": ("name", "web"),
    "vimeo.com": ("name", "web"),
    "rumble.com": ("name", "web"),
    "dailymotion.com": ("name", "web"),
    "bilibili.com": ("name", "web"),
    "tv4.se": ("name", "example"),
    "schemas.xmlsoap.org": ("name", "xml"),
    "purl.org": ("name", "xml"),
    "homeassistant.local": ("name", "example"),
    "ts.net": ("name", "suffix"),
    "bouquets.tv": ("name", "file"),
}
# Hosts that are made up (RFC 2606 examples and the like): left out.
_EXAMPLE = re.compile(r"(^|\.)(example(\.(com|org|net))?|invalid|test|localhost)$")
_HOST_IN = re.compile(
    r"""https?://(\[[0-9a-fA-F:]+\]|[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)"""
    r"""|["']\.?([a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:org|com|net|info|tv|io|dev|se|app))["']"""
    r"""|["'](?:https?://)?((?:\d{1,3}\.){3}\d{1,3})(?::\d+)?[/"']""")


def git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def _spans(tree: ast.Module, names: list[str], path: str) -> list[tuple[int, int]]:
    """The first and last line of each name: a function or class with its
    decorators and docstring, an assignment, or Class.method."""
    out = []
    for name in names:
        cls, _, attr = name.rpartition(".")
        body = tree.body
        if cls:
            node = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls), None)
            if node is None:
                raise SystemExit(f"{path}: no class {cls}")
            body = node.body
        found = None
        for n in body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == attr:
                found = n
            elif isinstance(n, (ast.Assign, ast.AnnAssign)):
                targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                for t in targets:
                    names = t.elts if isinstance(t, ast.Tuple) else [t]
                    if any(isinstance(e, ast.Name) and e.id == attr for e in names):
                        found = n
            if found is not None:
                break
        if found is None:
            raise SystemExit(f"{path}: no {name}")
        first = min([found.lineno] + [d.lineno for d in getattr(found, "decorator_list", [])])
        out.append((first, found.end_lineno or found.lineno))
    return out


def _merge(spans: list[tuple[int, int]], lines: list[str]) -> list[tuple[int, int]]:
    """Spans in order, joined where only blank lines and comments lie between."""
    out: list[list[int]] = []
    for a, b in sorted(spans):
        if out:
            gap = lines[out[-1][1]:a - 1]
            if all(not ln.strip() or ln.strip().startswith("#") for ln in gap):
                out[-1][1] = max(out[-1][1], b)
                continue
        out.append([a, b])
    return [(a, b) for a, b in out]


def _comments_above(lines: list[str], a: int) -> int:
    """The line a span starts on, with the comment lines right above it."""
    while a > 1 and lines[a - 2].strip().startswith("#"):
        a -= 1
    return a


def excerpt(root: str, ref: str, block: dict) -> list[dict]:
    path = block["path"]
    with open(os.path.join(root, path), encoding="utf-8") as fh:
        text = fh.read()
    lines = text.splitlines()
    if "names" in block:
        spans = [(_comments_above(lines, a), b)
                 for a, b in _spans(ast.parse(text), block["names"], path)]
        spans = _merge(spans, lines)
    else:
        found = [i for i, ln in enumerate(lines, 1) if re.search(block["from"], ln)]
        # The newest round's, where a file says the same in every round (a runbook).
        start = (found[-1] if block.get("last") else found[0]) if found else None
        if start is None:
            raise SystemExit(f"{path}: nothing matches {block['from']!r}")
        end = next((i for i, ln in enumerate(lines[start:], start + 1) if re.search(block["to"], ln)),
                   None)
        if end is None:
            raise SystemExit(f"{path}: no end for {block['from']!r}")
        spans = [(_comments_above(lines, start), end)]
    return [{"path": path, "start": a, "end": b,
             "url": f"{REPO}/blob/{ref}/{path}#L{a}-L{b}",
             "code": "\n".join(lines[a - 1:b])} for a, b in spans]


def code_of(app: str, app_ref: str, site: str, site_ref: str) -> dict:
    out: dict[str, list[dict]] = {}
    for part, blocks in CODE.items():
        out[part] = []
        for block in blocks:
            parts = []
            for b in [block] + block.get("with", []):
                root, ref = (app, app_ref) if b["repo"] == "app" else (site, site_ref)
                parts += [dict(p, repo=b["repo"]) for p in excerpt(root, ref, b)]
            out[part].append({"id": block["id"], "parts": parts})
    return out


def tests_of(app: str, app_ref: str) -> dict:
    out: dict[str, list[dict]] = {}
    for part, names in TESTS.items():
        out[part] = []
        for path, name in names:
            with open(os.path.join(app, path), encoding="utf-8") as fh:
                text = fh.read()
            (a, b), = _spans(ast.parse(text), [name], path)
            out[part].append({"name": name, "path": path, "url": f"{REPO}/blob/{app_ref}/{path}#L{a}-L{b}"})
    return out


def hosts_of(app: str, app_ref: str) -> list[dict]:
    """Every host the app's code names, with its first few places."""
    seen: dict[str, list[tuple[str, int]]] = {}
    for top, _dirs, files in os.walk(os.path.join(app, "dopeiptv")):
        for f in sorted(files):
            if not f.endswith(".py"):
                continue
            full = os.path.join(top, f)
            rel = os.path.relpath(full, app)
            with open(full, encoding="utf-8") as fh:
                for no, line in enumerate(fh, 1):
                    for m in _HOST_IN.finditer(line):
                        host = (m.group(1) or m.group(2) or m.group(3) or "").strip("[]").lower()
                        if not host or _EXAMPLE.search(host) or host.startswith("127.") \
                                or re.fullmatch(r"(10|192\.168|169\.254)(\.\d+)*|0\.0\.0\.0|255(\.255)*", host) \
                                or re.fullmatch(r"\d+\.\d+\.\d+\.\d+", host) and host not in HOSTS:
                            continue
                        seen.setdefault(host, []).append((rel, no))
    unknown = sorted(h for h in seen if h not in HOSTS)
    if unknown:
        raise SystemExit("hosts the page does not explain yet (add them to HOSTS and the page): "
                         + ", ".join(unknown))
    out = []
    for host in sorted(seen, key=lambda h: (HOSTS[h][0] != "talks", HOSTS[h][1], h)):
        kind, group = HOSTS[host]
        places = seen[host]
        out.append({"host": host, "kind": kind, "group": group, "count": len(places),
                    "where": [{"path": p, "line": n, "url": f"{REPO}/blob/{app_ref}/{p}#L{n}"}
                              for p, n in places[:3]]})
    return out


# -- the examples: the code above, run here ---------------------------------------

_REPORT = r'''
import json, sys
from dopeiptv.core import report
sources = [{"name": "My IPTV", "server": "http://line.example-tv.net:8080",
            "username": "kalle77", "password": "secret123"}]
personal = report.personal_values(sources, channels=["SVT1 HD"], titles=["The Bridge S02E03"])
log = """12:04:31 INFO player: opening http://line.example-tv.net:8080/live/kalle77/secret123/4711.ts
12:04:33 WARNING player: SVT1 HD dropped, reconnecting from 192.168.1.20 (aa:bb:cc:dd:ee:ff)
12:05:10 INFO history: The Bridge S02E03 resumed at 31:20
12:05:12 ERROR epg: token=abc123def failed for kalle.anka@example.com in /home/kalle/.config/dopeiptv
12:05:14 INFO source: My IPTV logged in as kalle77"""
print(json.dumps({"before": log, "after": report.scrub(log, personal)}))
'''

# Where a login is kept, and what the log makes of it: the settings folder
# and file as the app leaves them, and a few lines through the log's own
# filter - with Kalle's user name and password registered, as the client
# registers them when it is made.
_LOGINS = r'''
import json, logging, os, stat, tempfile
from dopeiptv.core import log, settings_files
folder = os.path.join(tempfile.mkdtemp(), ".config", "dopeIPTV")
path = os.path.join(folder, "dopeIPTV.conf")
settings_files.keep_private(path)
files = "\n".join(stat.filemode(os.stat(p).st_mode) + "  " + n
                  for p, n in ((folder, "~/.config/dopeIPTV/"), (path, "~/.config/dopeIPTV/dopeIPTV.conf")))
log.register_secrets("kalle77", "secret123")
lines = [("player: opening %s", ("http://line.example-tv.net:8080/live/kalle77/secret123/4711.ts",)),
         ("client: GET %s", ("http://line.example-tv.net:8080/player_api.php?username=kalle77"
                             "&password=secret123&action=get_live_categories",)),
         ("mpv: %s", ("Failed to open http://line.example-tv.net:8080/live/kalle77/secret123/4712.ts",)),
         ("source: logged in as %s", ("kalle77",))]
before, after = [], []
for msg, args in lines:
    rec = logging.LogRecord("dopeiptv", logging.INFO, "", 0, msg, args, None)
    before.append(rec.getMessage())
    log._RedactFilter().filter(rec)
    after.append(rec.getMessage())
print(json.dumps({"files": files, "before": "\n".join(before), "after": "\n".join(after)}))
'''

# What The Movie Database is asked for a poster: the request caught before
# it goes, from the app's own client.
_WATCHING = r'''
import json
from dopeiptv.providers import metadata
asked = {}
class Caught(Exception):
    pass
class Requests:
    def get(self, url, params=None, timeout=None):
        asked.update(url=url, params=dict(params or {}))
        raise Caught()
metadata.requests = Requests()
try:
    metadata.TmdbClient("<the app's key>").fetch_details("The Bridge", "series", 2011)
except Caught:
    pass
print(json.dumps({"tmdb": "GET " + asked["url"] + "\n" + json.dumps(asked["params"], ensure_ascii=False, indent=1)}))
'''

_SYNC = r'''
import base64, json
from dopeiptv.core import sync
code = sync.new_code()
slot, key = sync.derive(sync.parse_code(code))
doc = {"v": 1, "c": {"favorites": {"chan|SVT1 HD": {"t": 1763550000, "v": {"name": "SVT1 HD"}}},
                     "settings": {"theme": {"t": 1763550000, "v": "midnight"}}}}
doc = sync._counted(doc, 3)        # the third upload: its counter, locked with the rest
blob = base64.b64encode(sync.seal(key, doc)).decode("ascii")
print(json.dumps({"code": code, "inside": json.dumps(doc, ensure_ascii=False, indent=1),
                  "request": "PUT " + sync.ENDPOINT + slot,
                  "body": json.dumps({"version_seen": 0, "blob": blob})}))
'''

_TOGETHER = r'''
import base64, json
from dopeiptv.core import together
code = "K7PQM-2XDH9"
room, key = together.derive(code)
me = together.new_identity()
here = together.Room(code, me.id, "Kalle", together.Clock())
media = together.Media(kind="episode", name="The Bridge S02E03", title="the bridge",
                       year=2011, season=2, episode=3)
msg = here.publish(media, True, 1880.0)
frame = together.seal(key, room, msg, me)
inside = together.encode(msg, room, me).decode("utf-8")
print(json.dumps({"code": code, "inside": inside,
                  "request": "POST " + together.ENDPOINT + room,
                  "member": together.MEMBER_HEADER + ": " + me.id,
                  "size": len(frame), "body": base64.b64encode(frame).decode("ascii")}))
'''

_PRIVATE = r'''
import json
from dopeiptv.core import privacy
privacy.set_private(False)
off = "playing: " + str(privacy.for_log("The Bridge S02E03"))
privacy.set_private(True)
on = "playing: " + str(privacy.for_log("The Bridge S02E03"))
print(json.dumps({"off": off, "on": on}))
'''

_UPDATES = r'''
import json
from dopeiptv.core import updates, build_check
print(json.dumps({"agent": "User-Agent: " + updates.user_agent(),
                  "build": build_check.HEADER + ": " + " | ".join(
                      (build_check.OFFICIAL, build_check.SOURCE, build_check.MODIFIED))}))
'''

_SITE = r'''
import json, sys
sys.path.insert(0, "donate")
import stats_collect as s
from datetime import date
s.secrets.token_bytes = lambda n: bytes(range(1, n + 1))      # the seal's nonce, made up too
s.counted = lambda addr: True                                   # a documentation address, counted as a public one
state = {"_keys": s.MonthKeys(date(2026, 11, 19), keys={"2026-11": "0" * 64})}
ip, ua = "203.0.113.7", "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0"
line = {"ip": ip, "t": "2026-11-19T21:04:31+01:00", "m": "POST",
        "u": "/hit?p=%2Fhelp%2F&ev=view&t=&x=&n=&s=&l=sv&bl=sv-SE&w=1920&h=1080&hl=&r=https%3A%2F%2Fduckduckgo.com%2F",
        "s": 204, "a": ua, "l": "sv"}
class Geo:
    def country(self, addr): return "SE"
visits = s.Visits({}, state)
s._page_line({"days": {}}, state, line, Geo(), visits)
leave = dict(line, t="2026-11-19T21:05:19+01:00",
             u="/hit?p=%2Fhelp%2F&ev=leave&t=48&x=&n=&s=&l=sv&bl=&w=&h=&hl=&r=")
s._page_line({"days": {}}, state, leave, Geo(), visits)
(visit,) = visits.day("2026-11-19")
kept = s.at_rest(visit, "2026-11-19", state)
print(json.dumps({"log": json.dumps(line), "visit": json.dumps(kept, ensure_ascii=False),
                  "anon": json.dumps(s.funnel_of([kept]), ensure_ascii=False)}))
'''


def shorten(text: str, keep: int = 44) -> str:
    """A long value as its start and its end - the same in every language."""
    return text if len(text) <= 2 * keep + 1 else text[:keep] + "…" + text[-keep // 2:]


def run(root: str, script: str, env: dict | None = None) -> dict:
    """*script* run with *root* first on the path, as a fresh interpreter."""
    full = {**os.environ, **(env or {})}
    head = f"import sys; sys.path.insert(0, {root!r})\n"
    out = subprocess.run([sys.executable, "-I", "-c", head + script], cwd=root, env=full,
                         check=True, capture_output=True, text=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def examples_of(app: str, site: str) -> dict:
    # A person's computer as the example has it: the home folder and the user
    # name the report's cleaning takes out are Kalle's.
    kalle = {"HOME": "/home/kalle", "USER": "kalle", "LOGNAME": "kalle", "USERNAME": "kalle"}
    rep, syn, tog = run(app, _REPORT, kalle), run(app, _SYNC), run(app, _TOGETHER)
    pri, upd, web = run(app, _PRIVATE), run(app, _UPDATES), run(site, _SITE)
    lgn, wat = run(app, _LOGINS), run(app, _WATCHING)
    blob = json.loads(syn["body"])["blob"]

    def pretty(text: str) -> str:
        return json.dumps(json.loads(text), ensure_ascii=False, indent=1)
    # Each as the page shows it (site/transparency/<lang>.html: <!--tp:ex PART KEY-->).
    return {
        "logins": {"files": lgn["files"], "before": lgn["before"], "after": lgn["after"]},
        "watching": {"tmdb": wat["tmdb"]},
        "reports": {"before": rep["before"], "after": rep["after"]},
        "sync": {"inside": syn["inside"],
                 "request": syn["request"] + "\n" + json.dumps({"version_seen": 0, "blob": shorten(blob)})},
        "together": {"inside": tog["inside"],
                     "request": tog["request"] + "\n" + tog["member"] + "\n\n" + shorten(tog["body"])},
        "private": {"lines": pri["off"] + "\n" + pri["on"]},
        "updates": {"lines": upd["agent"] + "\n" + upd["build"]},
        "website": {"log": web["log"], "visit": pretty(web["visit"]), "anon": pretty(web["anon"])},
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--app", required=True, help="a checkout of the app at --ref")
    ap.add_argument("--ref", required=True, help="the app's version: a tag, or a commit")
    ap.add_argument("--site-ref", default="", help="this checkout's commit on GitHub (HEAD)")
    ap.add_argument("--out", default="site/transparency.json")
    a = ap.parse_args(argv)
    site = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = os.path.abspath(a.app)
    commit = git(app, "rev-parse", "HEAD")
    tagged = git(app, "rev-parse", a.ref + "^{commit}")
    if commit != tagged:
        raise SystemExit(f"{app} is at {commit[:12]}, not at {a.ref} ({tagged[:12]})")
    site_ref = a.site_ref or git(site, "rev-parse", "HEAD")
    shown = sorted({b["path"] for blocks in CODE.values() for blk in blocks
                    for b in [blk] + blk.get("with", []) if b["repo"] == "site"})
    if git(site, "status", "--porcelain", "--", "donate", *shown):
        raise SystemExit("donate/ or a file the page shows has changes not committed: "
                         "the links would not match")
    with open(os.path.join(app, "dopeiptv/__init__.py"), encoding="utf-8") as fh:
        version = re.search(r'__version__ = "([^"]+)"', fh.read())
    data = {"ref": a.ref, "commit": commit, "version": version.group(1) if version else "",
            "site_ref": site_ref, "made": time.strftime("%Y-%m-%d"),
            "code": code_of(app, a.ref, site, site_ref),
            "tests": tests_of(app, a.ref),
            "hosts": hosts_of(app, a.ref),
            "examples": examples_of(app, site)}
    with open(os.path.join(site, a.out), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    n = sum(len(p["parts"]) for blocks in data["code"].values() for p in blocks)
    print(f"{a.out}: {n} excerpts, {len(data['hosts'])} hosts, app {a.ref} ({commit[:12]}), "
          f"site {site_ref[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
