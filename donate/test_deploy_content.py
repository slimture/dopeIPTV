"""The content server's nginx file (deploy/content-iptv.dope.rs.conf), read
as nginx reads it - nginx itself is not here. A small reader of its syntax
(blocks, directives, quotes, comments) and of how a request finds its
location and a map its value, then what the file promises: nothing of a
request in its logs but what the statistics read, each limit where it says
it is - and one that applies -, a zone's key never changed under its name
(a reload keeps the old configuration then, DEPLOY.md), bodies kept in
memory up to what the service takes, nothing served that was never meant
to be."""
from __future__ import annotations

import os
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONF = os.path.join(ROOT, "deploy", "content-iptv.dope.rs.conf")


# -- nginx's syntax ---------------------------------------------------------------

class Block:
    """A directive: its name, its arguments, its block (None for a plain
    one, ending in ;) and its line."""

    def __init__(self, name: str, args: list[str], body: list[Block] | None, line: int):
        self.name, self.args, self.body, self.line = name, args, body, line

    def all(self, name: str) -> list[Block]:
        return [b for b in self.body or [] if b.name == name]

    def one(self, name: str) -> Block:
        found = self.all(name)
        assert len(found) == 1, (name, self.line, len(found))
        return found[0]

    def values(self, name: str) -> list[str]:
        return [" ".join(b.args) for b in self.all(name)]

    def __repr__(self) -> str:
        return f"<{self.name} {' '.join(self.args)} @{self.line}>"


def _unescape(raw: str) -> str:
    """A word as nginx copies it: \\" \\' \\\\ lose their backslash, \\t \\r \\n
    are the characters; any other backslash stays (a regex's \\.)."""
    return re.sub(r"\\([\"'\\trn])",
                  lambda m: {"t": "\t", "r": "\r", "n": "\n"}.get(m.group(1), m.group(1)), raw)


def _tokens(text: str):
    """(token, line, quoted) as nginx's ngx_conf_read_token reads them: a #
    that starts a word starts a comment, one inside a word is the word's."""
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
        elif c in "{};":
            yield c, line, False
            i += 1
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            assert j < n, f"a quote not closed, line {line}"
            yield _unescape(text[i + 1:j]), line, True
            line += text.count("\n", i, j)
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in " \t\r\n;{}":
                j += 2 if text[j] == "\\" else 1
            yield _unescape(text[i:j]), line, False
            i = j


def parse(text: str) -> Block:
    stack = [Block("", [], [], 0)]
    words: list[str] = []
    start = 0
    for tok, line, quoted in _tokens(text):
        if not quoted and tok == ";":
            assert words, f"an empty directive, line {line}"
            stack[-1].body.append(Block(words[0], words[1:], None, start))
            words = []
        elif not quoted and tok == "{":
            assert words, f"a block without a name, line {line}"
            b = Block(words[0], words[1:], [], start)
            stack[-1].body.append(b)
            stack.append(b)
            words = []
        elif not quoted and tok == "}":
            assert not words and len(stack) > 1, f"a }} out of place, line {line}"
            stack.pop()
        else:
            if not words:
                start = line
            words.append(tok)
    assert not words and len(stack) == 1, "a block or a directive not closed"
    return stack[0]


def conf(path: str = CONF) -> Block:
    with open(path, encoding="utf-8") as fh:
        return parse(fh.read())


def _py(regex: str) -> str:
    """A PCRE pattern as Python's re writes it ((?<name>...) for a name)."""
    return re.sub(r"\(\?<(?=[A-Za-z_])", "(?P<", regex)


def locate(server: Block, uri: str) -> list[Block]:
    """The locations nginx gives *uri*, the outer first, the one that
    answers last (ngx_http_core_find_location): an exact one, else the
    longest prefix - and inside it its own -, then, unless that prefix is
    ^~, the regexes of the level in order."""
    def find(blocks: list[Block]) -> tuple[list[Block], bool]:
        locs = [b for b in blocks if b.name == "location"]
        for b in locs:
            if b.args[0] == "=" and b.args[1] == uri:
                return [b], True
        prefixes = [b for b in locs if (len(b.args) == 1 and not b.args[0].startswith("@"))
                    or b.args[0] == "^~"]
        best = max((b for b in prefixes if uri.startswith(b.args[-1])), key=lambda b: len(b.args[-1]),
                   default=None)
        found: list[Block] = []
        if best is not None:
            inner, final = find(best.body)
            if final:
                return [best] + inner, True
            found = [best] + inner
        if best is None or best.args[0] != "^~":
            for b in locs:
                if b.args[0] in ("~", "~*") and re.search(_py(b.args[1]), uri,
                                                          re.I if b.args[0] == "~*" else 0):
                    return [b] + find(b.body)[0], True
        return found, False
    return find(server.body)[0]


def find_location(server: Block, uri: str) -> Block | None:
    """The location that answers *uri*."""
    path = locate(server, uri)
    return path[-1] if path else None


def map_value(top: Block, var: str, value: str) -> str:
    """What `map <source> $var` makes of *value*: a plain string first,
    then the regexes in order, else the default - named captures put in."""
    block = next(b for b in top.all("map") if b.args[1] == var)
    entries = [(b.name, b.args[0] if b.args else "") for b in block.body]
    for key, out in entries:
        if not key.startswith("~") and key != "default" and key == value:
            return out
    for key, out in entries:
        if key.startswith("~"):
            m = re.search(_py(key[2:] if key.startswith("~*") else key[1:]), value,
                          re.I if key.startswith("~*") else 0)
            if m:
                return re.sub(r"\$(\w+)", lambda v: m.groupdict().get(v.group(1), v.group(0)), out)
    return dict(entries).get("default", "")


def _server(top: Block) -> Block:
    servers = [b for b in top.all("server") if "dopeiptv.org" in b.values("server_name")[0].split()]
    assert len(servers) == 1
    return servers[0]


def _zones(top: Block) -> dict:
    out = {}
    for b in top.all("limit_req_zone") + top.all("limit_conn_zone"):
        name = next(a.split("=", 1)[1].split(":")[0] for a in b.args if a.startswith("zone="))
        out[name] = b.args[0]
    return out


def test_the_file_reads_as_nginx_reads_it():
    top = conf()
    srv = _server(top)
    assert srv.values("listen") == ["8081"]
    # Every location is a block, every directive ended - and the reader is
    # sure of itself on the file as it was.
    before = subprocess.run(["git", "-C", ROOT, "show", "e75c922:deploy/content-iptv.dope.rs.conf"],
                            capture_output=True, text=True)
    if before.returncode == 0:
        old = parse(before.stdout)
        assert find_location(_server(old), "/help/").args == ["=", "/help/"]


def test_no_zone_changes_its_key_under_its_name():
    # nginx -t passes, the reload fails and the old configuration runs on
    # (DEPLOY.md, "A reload that did nothing"): a new key takes a new name.
    before = subprocess.run(["git", "-C", ROOT, "show", "e75c922:deploy/content-iptv.dope.rs.conf"],
                            capture_output=True, text=True)
    if before.returncode:
        import pytest
        pytest.skip("no git history here")
    old, new = _zones(parse(before.stdout)), _zones(conf())
    for name, key in old.items():
        assert new.get(name, key) == key, name
    for b in parse(before.stdout).all("limit_req_zone"):
        same = [z for z in conf().all("limit_req_zone") if z.args == b.args]
        assert same, b.args


def test_nothing_of_a_request_reaches_the_error_log():
    # A refused request (a rate limit, a broken one) would write its line -
    # a sync slot, a room, an invite - into nginx's error log, which the
    # privacy policy says is never done: only what stops the server.
    srv = _server(conf())
    assert srv.values("error_log") == ["/var/log/nginx/error.log crit"]
    assert srv.values("access_log") == ["off"]


def test_a_name_with_a_dot_first_is_never_served_but_well_known():
    srv = _server(conf())
    dot = find_location(srv, "/.gitignore")
    assert dot.args == ["~", r"/\.(?!well-known/)"] and dot.values("return") == ["404"]
    for uri in ("/files/.gitignore", "/.git/config", "/.env", "/help-shots/sv/.x.webp", "/.x.css",
                "/.well-known", "/fonts/.DS_Store"):
        assert find_location(srv, uri) is dot, uri
    regexes = [b for b in srv.all("location") if b.args[0] in ("~", "~*")]
    assert regexes[0] is dot                       # before any other regex can answer
    txt = find_location(srv, "/.well-known/security.txt")
    assert txt.args == ["=", "/.well-known/security.txt"] and txt.values("default_type") == ["text/plain"]
    assert txt.one("types").body == []             # text/plain, whatever its ending says
    apple = "/.well-known/apple-developer-merchantid-domain-association"
    assert find_location(srv, apple).args == ["=", apple]
    # Everything else as before.
    for uri, where in (("/", "= /"), ("/assets/style.0123456789.css", r"~* \.(css|js)$"),
                       ("/files/dopeIPTV-1.6.8-x86_64.AppImage", "/files/"),
                       ("/help-shots/sv/set-sync.webp", r"~* \.(svg|png|ico|webp|webmanifest|woff2)$"),
                       ("/x.php", r"~ \.php$"), ("/privacy/", "~ ^/(?<iptv_legal>privacy|terms|payments|support|about)/$")):
        assert " ".join(find_location(srv, uri).args) == where, uri


def test_security_txt_says_where_to_report_and_has_not_expired():
    import datetime as dt
    with open(os.path.join(ROOT, "public", ".well-known", "security.txt"), encoding="utf-8") as fh:
        fields = dict(line.split(": ", 1) for line in fh.read().splitlines() if line and not line.startswith("#"))
    assert fields["Contact"] == "https://github.com/slimture/dopeIPTV/security/advisories/new"
    assert fields["Canonical"] == "https://dopeiptv.org/.well-known/security.txt"
    assert fields["Policy"] == "https://github.com/slimture/dopeIPTV/blob/main/SECURITY.md"
    assert fields["Preferred-Languages"] == "en, sv"
    expires = dt.datetime.fromisoformat(fields["Expires"].replace("Z", "+00:00"))
    now = dt.datetime.now(dt.timezone.utc)
    # Renewed once a year, never more than a year and a bit ahead (RFC 9116).
    assert now < expires < now + dt.timedelta(days=400), "security.txt's Expires: a year from today"


def _size(value: str) -> int:
    m = re.fullmatch(r"(\d+)([kKmM]?)", value)
    return int(m.group(1)) * {"": 1, "k": 1024, "m": 1 << 20}[m.group(2).lower()]


def _effective(srv: Block, uri: str, name: str) -> str:
    """*name*'s value for *uri*: its location's own, else the location's
    around it, else the server's (nginx's inheritance)."""
    for b in reversed([srv] + locate(srv, uri)):
        if b.values(name):
            return b.values(name)[-1]
    return ""


def test_a_body_is_held_in_memory_and_nothing_taken_today_is_refused():
    import sys
    sys.path.insert(0, HERE)
    import sync
    import together
    src = open(os.path.join(HERE, "donate.py"), encoding="utf-8").read()

    def takes(method: str) -> int:
        return int(re.search(r"def %s\(self.*?self\._body\((\d+)" % method, src, re.S).group(1))
    admin = max(int(n) for n in re.search(r"self\._body\((\d+) if path == \"/admin/pick\" else (\d+)\)",
                                          src).groups())
    # What the service takes on each route, at the most: nginx never refuses
    # it, and holds it in memory (a buffer as large as what it lets in).
    most = {"/feedback/report": takes("_feedback"), "/feedback/replies": takes("_feedback_replies"),
            "/sync/" + "ab" * 32: sync.MAX_BODY, "/together/" + "ab" * 32: together.MAX_MESSAGE,
            "/admin/pick": admin, "/admin/account/passkey": admin, "/admin/auth/password": takes("_sign_in"),
            "/donate/session": 1024, "/support/key": takes("_key")}
    assert most["/feedback/report"] == 200000 and most["/admin/pick"] == 65536
    srv = _server(conf())
    assert _size(srv.values("client_max_body_size")[0]) == 64 * 1024
    for uri, body in most.items():
        allowed = _size(_effective(srv, uri, "client_max_body_size"))
        buffer = _size(_effective(srv, uri, "client_body_buffer_size"))
        assert allowed >= body, (uri, allowed, body)
        assert buffer >= allowed, (uri, buffer, allowed)
    # Where a route had a limit of its own, it is the same.
    assert _effective(srv, "/sync/x", "client_max_body_size") == "3m"
    assert _effective(srv, "/feedback/report", "client_max_body_size") == "256k"
    assert _effective(srv, "/together/x", "client_max_body_size") == "4k"


def _locations(blocks: list[Block]):
    for b in blocks:
        if b.name == "location":
            yield b
            yield from _locations(b.body)


def _named(srv: Block, name: str) -> Block:
    return next(b for b in srv.all("location") if b.args == [name])


def test_every_limit_is_checked_before_the_answer():
    # A location's return runs in nginx's rewrite phase, before the phase
    # limit_req is checked in: a limit beside a return never applied - which
    # /hit's had not, since it was written. The answer comes from a named
    # location that try_files hands on to, after the limit.
    srv = _server(conf())
    for loc in _locations(srv.body):
        if loc.all("limit_req"):
            assert not loc.all("return") and not any(i.all("return") for i in loc.all("if")), loc
    for uri, zone, named, log in (("/dl.php", "iptv_dl", "@iptv_dl", "iptv.dope.rs-downloads.log iptv_dl"),
                                  ("/hit", "iptv_hit", "@iptv_hit", "iptv.dope.rs-stats.log iptv_stats")):
        loc = find_location(srv, uri)
        assert loc.args == ["=", uri] and loc.values("limit_req") == [f"zone={zone} burst=60 nodelay"]
        assert loc.values("limit_req_status") == ["429"]
        tried = loc.values("try_files")[0].split()
        assert tried[-1] == named and not os.path.exists(os.path.join(ROOT, "public", tried[0].lstrip("/")))
        answer = _named(srv, named)
        assert answer.values("access_log") == [f"/var/log/nginx/{log} if=$iptv_count"]
        assert not loc.all("access_log")           # a refused one is not written down
    assert _zones(conf())["iptv_dl"] == "$http_x_real_ip"
    assert [b.args for b in conf().all("limit_req_zone") if "zone=iptv_dl:1m" in b.args] == [
        ["$http_x_real_ip", "zone=iptv_dl:1m", "rate=120r/m"]]
    # Its answers as before: the file, a test build on GitHub, else Not found.
    dl = _named(srv, "@iptv_dl")
    assert dl.values("return") == ["302 /files/$iptv_dl_file"]
    assert [i.values("return") for i in dl.all("if")] == [
        ['404 Not found'], ["302 https://github.com/slimture/dopeIPTV/releases/download/$iptv_dl_nightly/$iptv_dl_file"],
        ['404 Not found']]
    assert _named(srv, "@iptv_hit").values("return") == ["204"]


def test_a_download_name_is_at_most_120_characters():
    top = conf()
    name = "dopeIPTV-1.6.8-x86_64.AppImage"
    assert map_value(top, "$iptv_dl_file", name) == name
    assert map_value(top, "$iptv_dl_file", "a/b/" + name) == name
    assert map_value(top, "$iptv_dl_file", ".hidden") == ""
    longest = "dopeIPTV-1.6.8.90-" + "x" * 102
    assert len(longest) == 120 and map_value(top, "$iptv_dl_file", longest) == longest
    assert map_value(top, "$iptv_dl_file", longest + "x") == ""
    assert map_value(top, "$iptv_dl_nightly", map_value(top, "$iptv_dl_file", longest)) == "nightly-1.6.8.90"
    assert map_value(top, "$iptv_dl_nightly", map_value(top, "$iptv_dl_file", longest + "x")) == ""


def test_a_log_line_holds_only_so_much_of_what_was_sent():
    top = conf()
    # Each value of the sender's own cut short (a header can be 8k); what
    # the site sets or nginx knows is written as it is.
    cuts = {"$iptv_log_ua": 1024, "$iptv_log_uri": 2048, "$iptv_log_request": 2048,
            "$iptv_log_method": 16, "$iptv_ref": 2048}
    ours = {"$http_x_real_ip", "$time_iso8601", "$time_local", "$status", "$body_bytes_sent",
            "$iptv_lang", "$remote_addr"}
    for fmt in top.all("log_format"):
        used = set(re.findall(r"\$\w+", "".join(fmt.args[1:])))
        assert used <= set(cuts) | ours, (fmt.args[0], used - set(cuts) - ours)
    assert {f.args[0] for f in top.all("log_format")} == {"iptv_stats", "iptv_dl"}
    for var, most in cuts.items():
        long = "Mozilla/5.0 " + "x" * 9000
        assert map_value(top, var, long) == long[:most], var
        assert map_value(top, var, "GET") == "GET", var
        assert map_value(top, var, "") == "", var
    # The page that linked: still without its query or fragment.
    assert map_value(top, "$iptv_ref", "https://example.com/a/b?q=x#f") == "https://example.com/a/b"
    # The download log as sync-releases.sh and stats_collect.py read it:
    # the request line second between quotes, the browser sixth, the
    # address last - and no user name the request could have sent.
    dl = "".join(next(f for f in top.all("log_format") if f.args[0] == "iptv_dl").args[1:])
    assert dl.split('"')[1] == "$iptv_log_request" and dl.split('"')[5] == "$iptv_log_ua"
    assert dl.startswith("$remote_addr - - [$time_local] ") and dl.endswith('"$http_x_real_ip"')


def test_the_language_cookie_goes_over_https_and_only_from_the_site():
    top = conf()
    assert map_value(top, "$iptv_set_cookie", "") == ""
    assert map_value(top, "$iptv_set_cookie", "sv") == \
        "lang=$iptv_lang_arg; Max-Age=31536000; path=/; Secure; SameSite=Lax"
    # Every page that sets it sets it from that map, and only that.
    for loc in _locations(_server(top).body):
        for value in loc.values("add_header"):
            if value.startswith("Set-Cookie"):
                assert value.split()[1] == "$iptv_set_cookie", loc


def test_the_admin_pages_are_counted_by_address_whatever_cookie_comes():
    top = conf()
    srv = _server(top)
    # A browser with the session cookie is not counted by iptv_admin - nor
    # was one with a made-up cookie. Every request is by its address.
    assert map_value(top, "$iptv_admin_limit", "") == "$http_x_real_ip"
    assert map_value(top, "$iptv_admin_limit", "made-up") == ""
    assert _zones(top)["iptv_admin_addr"] == "$http_x_real_ip"
    assert [b.args for b in top.all("limit_req_zone") if "zone=iptv_admin_addr:1m" in b.args] == [
        ["$http_x_real_ip", "zone=iptv_admin_addr:1m", "rate=600r/m"]]
    for uri in ("/admin/", "/admin/stats", "/admin/login", "/admin/sw.js"):
        path = locate(srv, uri)
        assert path[0].args == ["^~", "/admin/"], uri
        limits = next(b.values("limit_req") for b in reversed(path) if b.values("limit_req"))
        assert limits == ["zone=iptv_admin burst=30 nodelay", "zone=iptv_admin_addr burst=300 nodelay"], uri
    # Signing in as before: its own zone, for everyone.
    assert find_location(srv, "/admin/auth/password").values("limit_req") == ["zone=iptv_signin burst=10 nodelay"]


def test_the_port_can_be_kept_to_the_proxy_without_this_file_naming_it():
    # The proxy's address is the server's own: a file of the owner's beside
    # nginx.conf, which a new copy of this one keeps. A pattern (nginx lets
    # one match nothing), so until it is made nothing changes.
    import fnmatch
    srv = _server(conf())
    pattern = srv.values("include")
    assert pattern == ["iptv-only-from-proxy.con[f]"]
    assert fnmatch.fnmatch("iptv-only-from-proxy.conf", pattern[0])
    assert not fnmatch.fnmatch("iptv-only-from-proxy.conf.bak", pattern[0])
    assert srv.body.index(srv.one("include")) < srv.body.index(next(b for b in srv.body if b.name == "location"))
    deploy = open(os.path.join(ROOT, "DEPLOY.md"), encoding="utf-8").read()
    assert "/usr/local/etc/nginx/iptv-only-from-proxy.conf" in deploy and "deny all;" in deploy


def test_the_services_log_is_turned_over_and_reopened():
    # newsyslog sends a SIGHUP to daemon(8)'s pid file; only with -H does
    # daemon reopen its log on one rather than take it amiss.
    rc = open(os.path.join(ROOT, "deploy", "iptv_donate"), encoding="utf-8").read()
    args = re.search(r'command_args="([^"]+)"', rc).group(1).split()
    assert "-H" in args and "-o" in args and args[args.index("-P") + 1] == "${pidfile}"
    pidfile = re.search(r"^pidfile=(.+)$", rc, re.M).group(1).replace("${piddir}", "/var/run/${name}") \
        .replace("${name}", "iptv_donate")
    deploy = open(os.path.join(ROOT, "DEPLOY.md"), encoding="utf-8").read()
    line = re.search(r"'(/var/log/iptv-donate\.log\s+iptv:iptv\s+600\s[^']*)'", deploy).group(1)
    fields = line.split()
    # logfilename owner:group mode count size when flags pid_file: a SIGHUP
    # (newsyslog's own, no signal named) to daemon's pid file.
    assert fields[1:7] == ["iptv:iptv", "600", "5", "1024", "*", "J"] and len(fields) == 8
    assert fields[7] == pidfile == "/var/run/iptv_donate/iptv_donate.pid"
    # The line goes in only after the rc file with -H and a restart.
    rc_in = deploy.index("install -m 555 deploy/iptv_donate")
    assert rc_in < deploy.index("service iptv_donate restart", rc_in) < deploy.index(line)
    assert "install -d -o iptv -g iptv -m 700 /usr/local/www/iptv/stats" in deploy
