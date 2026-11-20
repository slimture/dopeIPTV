#!/usr/bin/env python3
"""What the content server's two logs keep of the address asked for
(deploy/content-iptv.dope.rs.conf), the lines worked out from the file as
nginx writes them - its maps, its log formats, $arg_ as nginx reads it:

- a page view (/, /help/) its path, never its query - ?fbclid=, ?utm_...,
  whatever a link carried;
- /hit the fields the page's signal sends (public/nav.js), which are the
  fields stats_collect.py reads, and nothing else - the three lists the
  same, so a field added to one and not the others fails here;
- a download the file's name, not the rest of the link;
- and the statistics read a line of before (the whole address) and one of
  now alike, while the old lines age out of the log.

Plain Python: ``python3 donate/test_log_fields.py`` runs them all (pytest
finds them too). Everything in a temporary directory.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from urllib.parse import parse_qs, quote

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from test_deploy_content import conf, map_value  # noqa: E402

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/142.0.0.0 Safari/537.36")
NOW = datetime.now().astimezone().replace(microsecond=0)


# -- nginx, as the file says --------------------------------------------------------

def _arg(query: str, name: str) -> str:
    """$arg_<name> as nginx gives it (ngx_http_arg): the first name=value of
    the query whose name is *name* in any case, the value as sent."""
    for part in query.split("&"):
        key, eq, value = part.partition("=")
        if eq and key.lower() == name.lower():
            return value
    return ""


def logged_uri(top, request_uri: str) -> str:
    """The "u" of a page view's or /hit's line: $iptv_log_uri."""
    kept = map_value(top, "$iptv_log_uri_kept", request_uri)
    query = request_uri.partition("?")[2]
    kept = re.sub(r"\$arg_(\w+)", lambda m: _arg(query, m.group(1)), kept)
    return map_value(top, "$iptv_log_uri", kept)


def _render(top, name: str, values: dict) -> str:
    """A line of log_format *name* with these variables' values, escaped as
    nginx escapes them (escape=json, or the default's \\xXX for a quote)."""
    fmt = next(f for f in top.all("log_format") if f.args[0] == name)
    args = fmt.args[1:]
    as_json = args[0] == "escape=json"
    text = "".join(args[1:] if as_json else args)

    def put(m: re.Match) -> str:
        v = values[m.group(1)]
        return json.dumps(v)[1:-1] if as_json else v.replace("\\", "\\x5C").replace('"', "\\x22")
    return re.sub(r"\$(\w+)", put, text)


def stats_line(top, request_uri: str, ua: str = UA, ip: str = "81.2.69.160", status: int = 204,
               method: str = "POST", ref: str = "") -> str:
    return _render(top, "iptv_stats", {
        "http_x_real_ip": ip, "time_iso8601": NOW.isoformat(timespec="seconds"),
        "iptv_log_method": map_value(top, "$iptv_log_method", method),
        "iptv_log_uri": logged_uri(top, request_uri), "status": str(status),
        "iptv_log_ua": map_value(top, "$iptv_log_ua", ua), "iptv_lang": "en"})


def dl_line(top, request_uri: str, ua: str = UA, ip: str = "81.2.69.160", status: int = 302,
            method: str = "GET", ref: str = "https://dopeiptv.org/") -> str:
    query = request_uri.partition("?")[2]
    return _render(top, "iptv_dl", {
        "remote_addr": "10.0.0.5", "time_local": NOW.strftime("%d/%b/%Y:%H:%M:%S %z"),
        "iptv_log_method": map_value(top, "$iptv_log_method", method),
        "iptv_dl_file": map_value(top, "$iptv_dl_file", _arg(query, "f")),
        "server_protocol": "HTTP/1.0", "status": str(status), "body_bytes_sent": "154",
        "iptv_log_ua": map_value(top, "$iptv_log_ua", ua), "http_x_real_ip": ip})


# -- what each side says ------------------------------------------------------------

def _hit_fields_in_the_file(top) -> list:
    block = next(b for b in top.all("map") if b.args[1] == "$iptv_log_uri_kept")
    value = next(b.args[0] for b in block.body if b.name.startswith("~^/hit"))
    fields = re.findall(r"[?&]([a-z]+)=\$arg_([a-z]+)", value)
    assert all(a == b for a, b in fields), fields          # each as sent, under its own name
    assert value == "/hit?" + "&".join(f"{a}=$arg_{a}" for a, _b in fields)
    return [a for a, _b in fields]


def _hit_fields_the_page_sends() -> set:
    with open(os.path.join(ROOT, "public", "nav.js"), encoding="utf-8") as fh:
        js = fh.read()
    block = js[js.index("var iptvHit"):]
    block = re.sub(r"(?m)^\s*//[^\n]*$|/\*.*?\*/", "", block[:block.index("})();")], flags=re.S)
    return set(re.findall(r'[?&"]([a-z]+)=', block))


def _hit_fields_the_statistics_read() -> set:
    with open(os.path.join(HERE, "stats_collect.py"), encoding="utf-8") as fh:
        src = fh.read()
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_page_line")
    return set(re.findall(r'\bq\.get\("([a-z]+)"', ast.get_source_segment(src, fn)))


def test_a_page_view_keeps_its_path_and_never_its_query():
    top = conf()
    for asked, kept in (("/", "/"), ("/help/", "/help/"), ("/?fbclid=IwAR0abc", "/"),
                        ("/help/?lang=sv&utm_source=news&utm_campaign=x", "/help/"),
                        ("/?", "/"), ("/?ref=github", "/")):
        assert logged_uri(top, asked) == kept, asked
    line = json.loads(stats_line(top, "/?fbclid=IwAR0abc&gclid=x", method="GET", status=200))
    assert line["u"] == "/" and "fbclid" not in json.dumps(line)


def test_hit_keeps_what_the_page_sends_and_the_statistics_read_and_nothing_else():
    top = conf()
    in_file = _hit_fields_in_the_file(top)
    assert set(in_file) == _hit_fields_the_page_sends() == _hit_fields_the_statistics_read()
    assert len(in_file) == len(set(in_file)) == 12 and in_file[-1] == "r"     # the longest last
    sent = ("/hit?p=%2Fhelp%2F&ev=view&r=" + quote("https://example.com/forum/thread", safe="")
            + "&s=github&l=sv&bl=sv-SE&w=1920&h=1080&fbclid=IwAR0abc&utm_medium=x&EV=other&p=again")
    kept = logged_uri(top, sent)
    q = parse_qs(kept.partition("?")[2])
    assert {k: v[0] for k, v in q.items()} == {"p": "/help/", "ev": "view", "s": "github", "l": "sv",
                                               "bl": "sv-SE", "w": "1920", "h": "1080",
                                               "r": "https://example.com/forum/thread"}
    assert "fbclid" not in kept and "utm_medium" not in kept and "again" not in kept
    assert logged_uri(top, "/hit") == "/hit?p=&ev=&t=&x=&n=&s=&l=&bl=&w=&h=&hl=&r="
    # A page that linked here of any length is cut, never the fields before it.
    long = logged_uri(top, "/hit?p=%2F&ev=view&l=en&w=1&h=2&r=" + "x" * 5000)
    assert len(long) == 1024 and parse_qs(long.partition("?")[2])["l"] == ["en"]
    # Another address is a page's: its path.
    assert logged_uri(top, "/hitx?p=1") == "/hitx" and logged_uri(top, "/hit/?p=1") == "/hit/"


def test_a_download_keeps_the_files_name_and_nothing_else_of_the_link():
    top = conf()
    line = dl_line(top, "/dl.php?f=files/dopeIPTV-1.6.8-x86_64.AppImage&utm_source=news&fbclid=abc")
    request = line.split('"')[1]
    assert request == "GET /dl.php?f=dopeIPTV-1.6.8-x86_64.AppImage HTTP/1.0"
    assert "utm_source" not in line and "fbclid" not in line
    # One that is no file's name: none, and its answer is a 404.
    assert dl_line(top, "/dl.php?f=.secret", status=404).split('"')[1] == "GET /dl.php?f= HTTP/1.0"


def _collector(root: str):
    stats = os.path.join(root, "stats")
    cfg = os.path.join(root, "donate.conf")
    with open(cfg, "w") as fh:
        fh.write(f"STATS_DIR={stats}\nSTATS_LOG={root}/stats.log\nDL_LOG={root}/dl.log\n"
                 f"GEO_DB={root}/none.mmdb\n")
    os.environ["DONATE_CONFIG"] = cfg
    os.environ["STATS_DIR"] = stats
    os.environ.pop("STATS_MAX_DAY_VISITS", None)
    for name in ("donate", "stats_collect", "analysis"):
        sys.modules.pop(name, None)
    import stats_collect
    assert stats_collect.STATS_DIR == stats
    return stats_collect


def _counted(stats_lines: list, dl_lines: list) -> tuple:
    root = tempfile.mkdtemp()
    for name, lines in (("stats.log", stats_lines), ("dl.log", dl_lines)):
        with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
            fh.write("".join(line + "\n" for line in lines))
    sc = _collector(root)
    with contextlib.redirect_stdout(io.StringIO()):
        sc.collect()
    with open(os.path.join(root, "stats", "stats.json"), encoding="utf-8") as fh:
        days = json.load(fh)["days"]
    visits = []
    folder = os.path.join(root, "stats", "visits")
    for f in sorted(os.listdir(folder)):
        with open(os.path.join(folder, f), encoding="utf-8") as fh:
            # A visit's names for its visitor are its run's own (a key each):
            # the rest is the same.
            visits += [{k: v for k, v in visit.items() if k not in ("id", "w", "e", "f")} for visit in json.load(fh)]
    return days, visits


def test_the_statistics_read_a_line_of_before_and_one_of_now_alike():
    top = conf()
    ref = quote("https://forum.example.net/thread/12", safe="")
    a, b = "81.2.69.160", "81.2.69.161"
    asked = [  # (address asked for, method, status, browser, visitor)
        ("/?fbclid=IwAR0abc&utm_source=news", "GET", 200, UA, a),
        ("/hit?p=%2F&ev=view&r=" + ref + "&s=news&l=en&bl=en-GB&w=1920&h=1080&fbclid=IwAR0abc", "POST", 204, UA, a),
        ("/hit?p=%2F&ev=act&utm_campaign=x", "POST", 204, UA, a),
        ("/hit?p=%2F&ev=help&x=" + quote("How do I add a playlist?") + "&n=3", "POST", 204, UA, a),
        ("/hit?p=%2F&ev=donate", "POST", 204, UA, a),
        ("/hit?p=%2F&ev=leave&t=48&fbclid=IwAR0abc", "POST", 204, UA, a),
        ("/help/?lang=sv", "GET", 200, CHROME, b),
        ("/hit?p=%2Fhelp%2F&ev=view&r=&s=&l=sv&bl=sv-SE&w=390&h=844", "POST", 204, CHROME, b),
        ("/hit?p=%2Fhelp%2F&ev=leave&t=12", "POST", 204, CHROME, b),
    ]
    files = [("/dl.php?f=dopeIPTV-1.6.8-x86_64.AppImage&utm_source=news", UA, a, 302),
             ("/dl.php?f=dopeIPTV-1.6.8-x64-setup.exe", CHROME, b, 302),
             ("/dl.php?f=dopeIPTV-1.6.8-x86_64.AppImage", "dopeIPTV/1.6.7 (Linux; x86_64)", a, 302)]

    def before(u, method, status, ua, ip):          # the iptv_stats format of before: $request_uri cut
        return json.dumps({"ip": ip, "t": NOW.isoformat(timespec="seconds"), "m": method, "u": u[:2048],
                           "s": status, "r": "", "a": ua, "l": "en"})

    def dl_before(u, ua, ip, status):                # ... and iptv_dl's: the whole request line
        return (f'10.0.0.5 - - [{NOW.strftime("%d/%b/%Y:%H:%M:%S %z")}] "GET {u} HTTP/1.0" {status} 154 '
                f'"https://dopeiptv.org/" "{ua}" "{ip}"')
    old = _counted([before(*x) for x in asked], [dl_before(*f) for f in files])
    new = _counted([stats_line(top, u, ua, ip, status, method) for u, method, status, ua, ip in asked],
                   [dl_line(top, u, ua, ip, status) for u, ua, ip, status in files])
    days, visits = new
    assert (days, visits) == old
    # ...and they counted what was there to count.
    day = days[NOW.date().isoformat()]
    assert day["views"] == 2 and day["visitors"] == 2 and day["requests"] == 2
    assert day["refs"] == {"example.net": 1} and day["sources"] == {"other": 1}
    assert day["files"] == {"dopeIPTV-1.6.8-x86_64.AppImage": 1, "dopeIPTV-1.6.8-x64-setup.exe": 1}
    assert sum(day["update_files"].values()) == 1
    assert sorted(v["time"] for v in visits) == [10, 50]         # to ten seconds, as kept
    # And a line of now has nothing of what the links carried.
    for u, method, status, ua, ip in asked:
        assert "fbclid" not in stats_line(top, u, ua, ip, status, method)
        assert "utm_" not in stats_line(top, u, ua, ip, status, method)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
