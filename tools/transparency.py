"""The code behind /transparency/ ("Privacy, behind the scenes"): what the
page shows of the app's and the site's own code, taken from that code.

    python3 tools/transparency.py --app PATH --ref v1.6.7 [--out site/transparency.json]

PATH is a checkout of the app at --ref (a tag; a commit while the page is
being written). The site's own code - donate/, deploy/ and the few other
files CODE names - is this checkout's, at its commit (--site-ref, HEAD by
default), which must be on GitHub: every excerpt links to its lines there.
Nothing on the page is copied by hand:

  code      each block the page shows: the named functions, classes and
            assignments of a Python file (found with ast, so a moved
            function is still found), the blocks and directives of an
            nginx file named by their first words (a map, a log_format,
            every limit_req_zone, a server's location - read as nginx reads
            it, so a moved block is still found), or the lines from a
            pattern in any other file - each with the comments right above
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
# in an nginx file, "nginx": what to take by its first words (nginx_find);
# or a pattern from a line to a line for any other file.
_PROXY = "deploy/proxy-dopeiptv.org.conf"          # the TLS-front proxy, dopeiptv.org
_OLD_PROXY = "deploy/proxy-iptv.dope.rs.conf"      # ...and the old name, sent on
_CONTENT = "deploy/content-iptv.dope.rs.conf"      # the content server behind it
_CSP = "deploy/csp.snippet.txt"                    # the CSP's row in the proxy's nginx.conf
_MAIN = "server[server_name dopeiptv.org]"         # the proxy's server of the site itself
CODE: dict[str, list[dict]] = {
    "reports": [
        {"id": "scrub", "repo": "app", "path": "dopeiptv/core/report.py",
         "names": ["_IPV4", "_IPV6", "_MAC", "_EMAIL", "_SUPPORTER", "_KEYVAL", "_AUTH_LINE",
                   "_BEARER", "_HOST_PATH", "_hide_value", "_not_an_address", "scrub",
                   "personal_values"],
         "with": [{"repo": "app", "path": "dopeiptv/core/log.py", "names": ["URL_IN_TEXT"]}]},
        {"id": "send", "repo": "app", "path": "dopeiptv/core/report.py",
         "names": ["ENDPOINT", "_over_https", "_post", "send_status"],
         "with": [{"repo": "app", "path": "dopeiptv/core/build_check.py", "names": ["secure"]}]},
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
         "names": ["format_code", "new_code", "hkdf", "derive", "seal", "unseal"]},
        {"id": "aead", "repo": "app", "path": "dopeiptv/core/chacha20poly1305.py",
         "names": ["poly1305", "_pad16", "_tag", "encrypt", "decrypt"]},
        {"id": "stored", "repo": "app", "path": "dopeiptv/core/sync.py", "names": ["K_CODE", "code_of"],
         "with": [{"repo": "app", "path": "dopeiptv/core/settings_files.py",
                   "names": ["keep_private", "protect"]}]},
        {"id": "counter", "repo": "app", "path": "dopeiptv/core/sync.py",
         "names": ["_META", "_COUNTER", "counter", "_counted", "round_trip"]},
        {"id": "send", "repo": "app", "path": "dopeiptv/core/sync.py",
         "names": ["ENDPOINT", "Remote.__init__", "Remote._call"],
         "with": [{"repo": "app", "path": "dopeiptv/core/build_check.py", "names": ["secure"]}]},
        {"id": "server", "repo": "site", "path": "donate/sync.py",
         "names": ["AUTH", "Refused", "_seal", "_check", "get", "put", "delete"],
         "with": [{"repo": "site", "path": "donate/donate.py", "names": ["Handler._sync"]}]},
        {"id": "nolog", "repo": "site", "path": "deploy/proxy-dopeiptv.org.conf",
         "from": r"^\s*location \^~ /sync/", "to": r"^\s*\}"},
    ],
    "together": [
        {"id": "key", "repo": "app", "path": "dopeiptv/core/together.py",
         "names": ["ALPHABET", "CODE_LEN", "OLD_LEN", "STRETCH", "OLD_STRETCH", "_PAD", "new_code",
                   "derive", "seal", "open_frame"]},
        {"id": "replay", "repo": "app", "path": "dopeiptv/core/together.py",
         "from": r"^    def _make\(", "to": r"^        return msg$",
         "with": [{"repo": "app", "path": "dopeiptv/core/together.py",
                   "from": r"^    def receive\(", "to": r"self\._last_n\[msg\.sender\] = msg\.n"}]},
        {"id": "server", "repo": "site", "path": "donate/together.py", "names": ["Relay.post"],
         "with": [{"repo": "site", "path": "donate/donate.py", "names": ["Handler._together"]}]},
        {"id": "nolog", "repo": "site", "path": "deploy/proxy-dopeiptv.org.conf",
         "from": r"^\s*location \^~ /together/", "to": r"^\s*\}"},
    ],
    "private": [
        {"id": "switch", "repo": "app", "path": "dopeiptv/core/privacy.py",
         "names": ["set_private", "private", "playback_started", "playback_stopped",
                   "playback_private", "private_key", "for_log"]},
        {"id": "stores", "repo": "app", "path": "dopeiptv/core/stores.py", "names": ["HistoryStore.add"],
         "with": [{"repo": "app", "path": "dopeiptv/services/resume.py", "names": ["ResumeStore.record"]}]},
        {"id": "tellers", "repo": "app", "path": "dopeiptv/ui/mw_trakt.py",
         "names": ["_TraktMixin._watch_accounts_start"]},
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
        {"id": "site", "repo": "site", "path": "sync-releases.sh",
         "from": r"^    # ---- The signed checksum list", "to": r"^    fi$",
         "with": [{"repo": "site", "path": "donate/release_sums.py", "names": ["why_not"]}]},
    ],
    "website": [
        {"id": "log", "repo": "site", "path": "deploy/content-iptv.dope.rs.conf",
         "from": r"^log_format iptv_stats", "to": r"';\s*$"},
        {"id": "visitor", "repo": "site", "path": "donate/stats_collect.py",
         "names": ["MonthKeys", "_hash", "_hash_before", "_new_visit"]},
        {"id": "kept", "repo": "site", "path": "donate/stats_collect.py",
         "from": r"^KEEP_PER_DAY = ", "to": r"^UPDATES_DAYS = ",
         "with": [{"repo": "site", "path": "donate/stats_collect.py",
                   "from": r"^    if ev in \(\"donate\", \"donated\"\):", "to": r"^        return$"}]},
        {"id": "anon", "repo": "site", "path": "donate/stats_collect.py",
         "names": ["anonymous", "freeze_months", "K_ANON", "_QI", "_LESS", "k_anonymous"]},
    ],
    "webservers": [
        {"id": "path", "repo": "site", "path": _PROXY, "nginx": [_MAIN + " > location /"],
         "with": [{"repo": "site", "path": _CONTENT, "nginx": ["server > listen", "server > include"]},
                  {"repo": "site", "path": _CONTENT, "nginx": ["server > location = /feedback/report"]},
                  {"repo": "site", "path": "deploy/iptv_donate",
                   "from": r"^: \$\{iptv_donate_enable", "to": r"^: \$\{iptv_donate_log"},
                  {"repo": "site", "path": "donate/donate.py", "names": ["main"]}]},
        {"id": "nolog", "repo": "site", "path": _PROXY, "nginx": ["server > access_log off", "server > error_log"],
         "with": [{"repo": "site", "path": _OLD_PROXY, "nginx": ["server > access_log off", "server > error_log"]},
                  {"repo": "site", "path": _CONTENT, "nginx": ["server > access_log off", "server > error_log"]}]},
        {"id": "logs", "repo": "site", "path": _CONTENT,
         "nginx": ["map $http_referer", "map $request_method", "map $http_user_agent", "map $request_uri",
                   "map $request", "log_format", "map $cookie_iptv_nostats",
                   "server > location = /", "server > location = /help/", "server > location = /dl.php",
                   "server > location @iptv_dl", "server > location = /hit", "server > location @iptv_hit"]},
        {"id": "kept", "repo": "site", "path": "DEPLOY.md",
         "from": r"^/var/log/nginx/iptv\.dope\.rs-downloads\.log", "to": r"^/var/log/iptv-sync\.log",
         "with": [{"repo": "site", "path": "DEPLOY.md", "from": r"^echo '/var/log/nginx/iptv\.dope\.rs-stats\.log",
                   "to": r">> /usr/local/etc/newsyslog\.conf\.d/"},
                  {"repo": "site", "path": "DEPLOY.md", "from": r"^grep -q iptv-donate\.log",
                   "to": r"^newsyslog -nv .*iptv-donate"}]},
        {"id": "limits", "repo": "site", "path": _CONTENT,
         "nginx": ["limit_req_zone", "map $cookie_iptv_admin", "map $http_sec_fetch_mode|$http_accept",
                   "server > client_max_body_size", "server > client_body_buffer_size"],
         "with": [{"repo": "site", "path": _PROXY,
                   "nginx": ["map $request_method", "limit_conn_zone", _MAIN + " > client_max_body_size",
                             _MAIN + " > client_body_buffer_size", _MAIN + " > location ~*"]}]},
        {"id": "headers", "repo": "site", "path": _PROXY,
         "nginx": ["map $uri", _MAIN + " > add_header", _MAIN + " > proxy_hide_header", _MAIN + " > server_tokens"],
         "with": [{"repo": "site", "path": _CSP, "from": r"^Add this line", "to": r"object/base/form locked down"},
                  {"repo": "site", "path": _OLD_PROXY, "nginx": ["server[server_name iptv.dope.rs] > add_header"]},
                  {"repo": "site", "path": _CONTENT,
                   "nginx": ["map $iptv_lang_arg $iptv_set_cookie", r"server > location ~ /\.(?!well-known/)",
                             "server > location = /.well-known/security.txt"]},
                  {"repo": "site", "path": "public/.well-known/security.txt",
                   "from": r"^Contact:", "to": r"^Policy:"}]},
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
# (site/transparency/<lang>.html, part 8), and quoted IP addresses besides -
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


# -- nginx's files ----------------------------------------------------------------

def nginx_tree(text: str, path: str = "") -> dict:
    """An nginx file as nginx reads it (ngx_conf_read_token): a directive is
    its words up to ";", a block its words up to "{" and what it holds up to
    its "}"; a "#" where a word would start begins a comment to the end of
    the line (inside a word it is the word's), a quoted word may hold any of
    them, a backslash keeps the next character in its word. Each node: its
    words (quotes off, nothing else changed), the lines it starts and ends
    on, and its children (None for a directive)."""
    root: dict = {"words": [], "start": 1, "end": 1, "kids": []}
    stack, words, first = [root], [], 0
    i, line, n = 0, 1, len(text)
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
        elif c in " \t\r":
            i += 1
        elif c == "#":
            while i < n and text[i] != "\n":
                i += 1
        elif c in ";{":
            if not words:
                raise SystemExit(f"{path}: line {line}: {c} with nothing before it")
            node = {"words": words, "start": first, "end": line, "kids": None if c == ";" else []}
            stack[-1]["kids"].append(node)
            if c == "{":
                stack.append(node)
            words = []
            i += 1
        elif c == "}":
            if words or len(stack) == 1:
                raise SystemExit(f"{path}: line {line}: a }} out of place")
            stack.pop()["end"] = line
            i += 1
        else:
            if not words:
                first = line
            j = i + 1
            if c in "\"'":
                while j < n and text[j] != c:
                    j += 2 if text[j] == "\\" else 1
                if j >= n:
                    raise SystemExit(f"{path}: line {line}: a quote not closed")
                words.append(text[i + 1:j])
                line += text.count("\n", i, j)
                j += 1
            else:
                j = i
                while j < n and text[j] not in " \t\r\n;{}":
                    j += 2 if text[j] == "\\" else 1
                words.append(text[i:j])
            i = j
    if words or len(stack) != 1:
        raise SystemExit(f"{path}: a block or a directive not closed")
    root["end"] = line
    return root


_HOLDS = re.compile(r"(.*\S)\[([a-z_]+(?: [^\]]*)?)\]")


def nginx_find(tree: dict, selector: str) -> list[dict]:
    """The nodes *selector* names: steps from the file's top level, " > "
    between them, each the first words of a directive or a block - every
    one that starts so ("limit_req_zone": every zone; "location = /": that
    location, not "location = /help/") - and a block's step perhaps with a
    directive it must hold, in brackets ("server[server_name dopeiptv.org]":
    the server of that name alone)."""
    found = [tree]
    for step in selector.split(" > "):
        holds: list[str] = []
        m = _HOLDS.fullmatch(step)
        if m:
            step, holds = m.group(1), m.group(2).split()
        want = step.split()
        found = [k for node in found for k in node["kids"] or []
                 if k["words"][:len(want)] == want
                 and (not holds or any(d["kids"] is None and d["words"] == holds for d in k["kids"] or []))]
    return found


def excerpt(root: str, ref: str, block: dict) -> list[dict]:
    path = block["path"]
    with open(os.path.join(root, path), encoding="utf-8") as fh:
        text = fh.read()
    lines = text.splitlines()
    if "names" in block:
        spans = [(_comments_above(lines, a), b)
                 for a, b in _spans(ast.parse(text), block["names"], path)]
        spans = _merge(spans, lines)
    elif "nginx" in block:
        tree, spans = nginx_tree(text, path), []
        for selector in block["nginx"]:
            nodes = nginx_find(tree, selector)
            if not nodes:
                raise SystemExit(f"{path}: nothing is {selector!r}")
            spans += [(_comments_above(lines, node["start"]), node["end"]) for node in nodes]
        spans = _merge(spans, lines)
    else:
        start = next((i for i, ln in enumerate(lines, 1) if re.search(block["from"], ln)), None)
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
member = together.new_member()
here = together.Room(code, member, "Kalle", together.Clock())
media = together.Media(kind="episode", name="The Bridge S02E03", title="the bridge",
                       year=2011, season=2, episode=3)
msg = here.publish(media, True, 1880.0)
frame = together.seal(key, room, msg)
print(json.dumps({"code": code, "inside": json.dumps(msg, ensure_ascii=False),
                  "request": "POST " + together.ENDPOINT + room,
                  "member": together.MEMBER_HEADER + ": " + member,
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
state = {"_keys": s.MonthKeys(None, date(2026, 11, 19), {"2026-11": "0" * 64})}
ip, ua = "203.0.113.7", "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0"
line = {"ip": ip, "t": "2026-11-19T21:04:31+01:00", "m": "GET", "u": "/hit?p=help&ev=view",
        "s": 204, "r": "https://duckduckgo.com/", "a": ua, "l": "sv"}
class Geo:
    def country(self, addr): return "SE"
class Visits:
    def __init__(self): self.d = {}
    def before(self, day, vid, earlier=""): return 0
    def day(self, day): return self.d.setdefault(day, [])
    def add(self, day, v): self.day(day).append(v)
vid = s._hash(state, "2026-11-19", ip, ua)
visit = s._new_visit(Visits(), "2026-11-19", vid, 1763582671, ip, ua, Geo())
visit.update({"pages": [["help", 1763582671, 48]], "time": 48, "lang": "sv", "channel": "search",
              "ref": "duckduckgo.com"})
print(json.dumps({"log": json.dumps(line), "vid": vid,
                  "visit": json.dumps(visit, ensure_ascii=False),
                  "anon": json.dumps(s.anonymous(visit, "2026-11-19"), ensure_ascii=False)}))
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
    blob = json.loads(syn["body"])["blob"]

    def pretty(text: str) -> str:
        return json.dumps(json.loads(text), ensure_ascii=False, indent=1)
    # Each as the page shows it (site/transparency/<lang>.html: <!--tp:ex PART KEY-->).
    return {
        "reports": {"before": rep["before"], "after": rep["after"]},
        "sync": {"inside": syn["inside"],
                 "request": syn["request"] + "\n" + json.dumps({"version_seen": 0, "blob": shorten(blob)})},
        "together": {"inside": tog["inside"],
                     "request": tog["request"] + "\n" + tog["member"] + "\n\n" + shorten(tog["body"])},
        "private": {"lines": pri["off"] + "\n" + pri["on"]},
        "updates": {"lines": upd["agent"] + "\n" + upd["build"]},
        "website": {"log": web["log"], "visit": pretty(web["visit"]), "anon": pretty(web["anon"])},
        "webservers": {"page": proxy_headers(site, "/"), "admin": proxy_headers(site, "/admin/login")},
    }


def proxy_headers(site: str, uri: str) -> str:
    """The headers the front proxy's file adds to an answer for *uri*: its
    site's server's add_header lines in their order, each variable as
    nginx works it out - a map's value for that address (the string itself
    first, then the regexes in order, else the default), the CSP from its
    row in csp.snippet.txt (the proxy's nginx.conf holds it) - and none
    whose value comes out empty: nginx sends no such header."""
    with open(os.path.join(site, _PROXY), encoding="utf-8") as fh:
        tree = nginx_tree(fh.read(), _PROXY)
    with open(os.path.join(site, _CSP), encoding="utf-8") as fh:
        csp = re.search(r'^\s*"dopeiptv\.org" "([^"]*)";', fh.read(), re.M).group(1)
    maps = {n["words"][2]: n for n in tree["kids"] if n["words"][0] == "map" and n["kids"] is not None}
    given = {"$uri": uri}

    def value(var: str) -> str:
        if var == "$csp_policy":
            return csp
        node = maps[var]
        subject = given[node["words"][1]]
        rows = [(k["words"][0], k["words"][1] if len(k["words"]) > 1 else "") for k in node["kids"]]
        for key, out in rows:
            if key == subject and key != "default" and not key.startswith("~"):
                return out
        for key, out in rows:
            if key.startswith("~"):
                pattern = re.sub(r"\(\?<(?=[A-Za-z_])", "(?P<", key[2:] if key.startswith("~*") else key[1:])
                if re.search(pattern, subject, re.I if key.startswith("~*") else 0):
                    return out
        return dict(rows).get("default", "")

    (server,) = nginx_find(tree, _MAIN)
    out = []
    for d in server["kids"]:
        if d["kids"] is None and d["words"][0] == "add_header":
            said = re.sub(r"\$[a-z_]+", lambda m: value(m.group(0)), d["words"][2])
            if said:
                out.append(f"{d['words'][1]}: {said}")
    return "\n".join(out)


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
    if git(site, "status", "--porcelain", "--", "donate", "deploy", *shown):
        raise SystemExit("donate/, deploy/ or a file the page shows has changes not committed: "
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
