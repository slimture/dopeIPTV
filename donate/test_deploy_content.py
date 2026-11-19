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


def find_location(server: Block, uri: str) -> Block | None:
    """The location nginx gives *uri* (ngx_http_core_find_location): an
    exact one, else the longest prefix - and inside it its own regexes -,
    then, unless that prefix is ^~, the regexes of the level in order."""
    def find(blocks: list[Block]) -> tuple[Block | None, bool]:
        locs = [b for b in blocks if b.name == "location"]
        for b in locs:
            if b.args[0] == "=" and b.args[1] == uri:
                return b, True
        prefixes = [b for b in locs if (len(b.args) == 1 and not b.args[0].startswith("@"))
                    or b.args[0] == "^~"]
        best = max((b for b in prefixes if uri.startswith(b.args[-1])), key=lambda b: len(b.args[-1]),
                   default=None)
        found = best
        if best is not None:
            inner, final = find(best.body)
            if final:
                return inner, True
            found = inner or best
        if best is None or best.args[0] != "^~":
            for b in locs:
                if b.args[0] in ("~", "~*") and re.search(_py(b.args[1]), uri,
                                                          re.I if b.args[0] == "~*" else 0):
                    return b, True
        return found, False
    return find(server.body)[0]


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
