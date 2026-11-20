#!/usr/bin/env python3
"""The statistics' run (stats_collect.py) against what anyone can send it.

The page's signal (/hit) and the download link are open to everyone, and
every part of a log line but its time is the sender's own words: the
address asked for, the page said to have linked here, the browser's name.
None of it may stop the run, take its time, or grow what is kept without
end:

- an address Python's urlsplit refuses ("http://[") is no address, not an
  error - one such request used to stop every run for as long as its line
  was in the log, two days;
- a line that fails for any other reason is passed over and said as a
  number, never with its text;
- a browser name of any length is read in no time, and its version is a
  few digits - never a list entry of its own per made-up number;
- a day keeps at most STATS_MAX_DAY_VISITS visits, a visit at most so many
  pages, and the day's lists stay short;
- a log grown huge is read in pieces;
- what the run writes is the service's alone to read (0600, 0700).

Plain Python: ``python3 donate/test_stats_robust.py`` runs them all (pytest
finds them too). Everything in a temporary directory.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import time
from datetime import datetime, timedelta
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/142.0.0.0 Safari/537.36")
IP = "81.2.69.160"                       # a public address
NOW = datetime.now().astimezone().replace(microsecond=0)
TODAY = NOW.date().isoformat()


def _module(extra: str = ""):
    root = tempfile.mkdtemp()
    stats = os.path.join(root, "stats")
    conf = os.path.join(root, "donate.conf")
    with open(conf, "w") as fh:
        fh.write(f"STATS_DIR={stats}\nSTATS_LOG={root}/stats.log\nDL_LOG={root}/dl.log\n"
                 f"GEO_DB={root}/none.mmdb\n{extra}")
    for name in ("stats.log", "dl.log"):
        open(os.path.join(root, name), "w").close()
    os.environ["DONATE_CONFIG"] = conf
    os.environ["STATS_DIR"] = stats
    os.environ.pop("STATS_MAX_DAY_VISITS", None)
    for name in ("donate", "stats_collect", "analysis"):
        sys.modules.pop(name, None)
    import stats_collect
    assert stats_collect.STATS_DIR == stats
    return stats_collect, root


def _line(u: str, ua: str = UA, ip: str = IP, status: int = 204, at: datetime | None = None, method: str = "POST") -> str:
    """A line as nginx's iptv_stats format writes it."""
    return json.dumps({"ip": ip, "t": (at or NOW).isoformat(timespec="seconds"), "m": method, "u": u,
                       "s": status, "a": ua, "l": "en"}, ensure_ascii=False)


def _view(ref: str = "", ua: str = UA, ip: str = IP, at: datetime | None = None, more: str = "") -> str:
    return _line(f"/hit?p=%2F&ev=view&r={quote(ref, safe='')}&l=en&bl=en-GB&w=1920&h=1080{more}", ua, ip, at=at)


def _write(root: str, name: str, lines: list) -> None:
    with open(os.path.join(root, name), "a", encoding="utf-8") as fh:
        fh.write("".join(line + "\n" for line in lines))


def _run(sc) -> str:
    """One run; what it said."""
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        sc.collect()
    return said.getvalue()


def _json(root: str, *path: str):
    with open(os.path.join(root, "stats", *path), encoding="utf-8") as fh:
        return json.load(fh)


def test_an_address_that_cannot_be_read_never_stops_the_run():
    sc, root = _module()
    _write(root, "stats.log", [
        _view(),
        # Each of these made urlsplit raise - and the run with it, every time.
        _view("http://["),
        _line("//[/..", method="GET", status=200),
        _view("http://exa＃mple.org/page"),
        _line("/hit?p=%2F&ev=view&r=http%3A%2F%2F%5B%3A%3A1&l=en"),
        _view("https://forum.example.net/thread/12", ua=CHROME, ip="81.2.69.161"),
    ])
    said = _run(sc)
    assert "Traceback" not in said
    day = _json(root, "stats.json")["days"][TODAY]
    # Every view is counted; one whose linking page cannot be read came from nowhere.
    assert day["views"] == 5 and day["visitors"] == 2
    assert day["refs"] == {"example.net": 1}       # the site by its registered name
    # The run got to the end of the log: the next one has nothing to read.
    state = _json(root, "state.json")
    assert state["logs"]["stats"]["off"] == os.path.getsize(os.path.join(root, "stats.log"))
    _run(sc)
    assert _json(root, "stats.json")["days"][TODAY]["views"] == 5
    assert sc.referrer_host("http://[") == "" and sc.referrer_page("http://[", "x") == ""


def test_a_line_that_fails_is_passed_over_and_only_counted():
    sc, root = _module()
    odd = json.dumps({"ip": IP, "t": NOW.isoformat(timespec="seconds"), "m": "GET",
                      "u": "/hit?p=%2F&ev=view", "s": 204, "a": 12345, "l": "en"})
    _write(root, "stats.log", [_view(), odd, "{not json", "[1, 2]", _view(ua=CHROME, ip="81.2.69.161")])
    _write(root, "dl.log", ["nothing a web server wrote", ""])
    said = _run(sc)
    assert "1 line(s) could not be read and were passed over" in said
    assert IP not in said and "12345" not in said          # never the line itself
    assert _json(root, "stats.json")["days"][TODAY]["views"] == 2
    # What the run keeps beside the state for itself is never saved with it.
    assert not [k for k in _json(root, "state.json") if k.startswith("_")]


def test_a_browser_name_of_any_length_is_read_at_once_and_its_version_is_short():
    sc, _root = _module()
    long_ua = "Mozilla/5.0 (Linux; Android " + "9" * 3000 + ") Version/" + "1" * 8000 + " Chrome/" + "7" * 3000
    started = time.perf_counter()
    for _ in range(20):
        name, version = sc.browser_of(long_ua[:sc.UA_MAX])
        sc.os_version_of(long_ua[:sc.UA_MAX])
        sc.channel_of(sc.referrer_host("https://" + "google." * 1200 + "!/"))
    assert time.perf_counter() - started < 2.0
    assert sc.browser_of("x Chrome/" + "7" * 3000) == ("Chrome", "7777")
    assert sc.os_version_of("Mozilla/5.0 (Linux; Android " + "9" * 3000 + ")") == "Android 999"
    assert sc.os_version_of("Mozilla/5.0 (iPhone; CPU iPhone OS " + "8" * 500 + " like Mac OS X)") == "iOS 888"
    assert len(sc.referrer_host("https://" + "a" * 5000 + ".example/")) <= sc.HOST_MAX
    # Real ones read as ever.
    assert sc.browser_of(CHROME) == ("Chrome", "142")
    assert sc.browser_of("Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 "
                         "(KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1") == ("Safari", "18")
    assert sc.os_version_of("Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) Version/17.4 Mobile/15E148 Safari/604.1") \
        == "iPadOS 17"


def test_made_up_browsers_and_languages_cannot_grow_a_days_lists():
    sc, root = _module()
    lines = []
    for i in range(sc.KEEP_WIDE + 150):
        ua = f"Mozilla/5.0 (Linux; Android {i % 1000}) AppleWebKit/537.36 Chrome/{1000 + i}.0 Mobile Safari/537.36"
        lang = "".join(chr(97 + (i // 26 ** k) % 26) for k in range(3))
        lines.append(_line(f"/hit?p=%2F&ev=view&l={lang}", ua, f"81.2.{i // 250}.{i % 250 + 1}"))
    _write(root, "stats.log", lines)
    _run(sc)
    day = _json(root, "stats.json")["days"][TODAY]
    assert day["visitors"] == sc.KEEP_WIDE + 150
    for name in ("browser_versions", "os_versions", "langs"):
        assert len(day[name]) <= sc.KEEP_WIDE, name
        assert sum(day[name].values()) == sc.KEEP_WIDE + 150, name     # none lost: the rest is "other"
        assert day[name]["other"] >= 150, name


def test_a_day_keeps_no_more_visits_than_a_day_may():
    sc, root = _module("STATS_MAX_DAY_VISITS=1000\n")
    assert sc.MAX_DAY_VISITS == 1000
    lines = [_view(ua=f"{UA} made-up/{i}", ip=f"81.2.{i // 250}.{i % 250 + 1}") for i in range(1300)]
    _write(root, "stats.log", lines)
    said = _run(sc)
    assert "as many as a day may" in said
    day = _json(root, "stats.json")["days"][TODAY]
    assert len(_json(root, "visits", TODAY + ".json")) == 1000
    import sketch
    assert abs(sketch.count(sketch.decode(sc.sketches_now()[TODAY]["u"])) - 1000) <= 5
    assert day["views"] == 1300 and day["visitors"] == 1000 and day["capped"] == 300
    # A visitor already there goes on being counted; a download past the
    # limit is the day's, with no visit of its own.
    _write(root, "stats.log", [_line("/hit?p=%2Fhelp%2F&ev=view&l=en", f"{UA} made-up/5", "81.2.0.6")])
    stamp = NOW.strftime("%d/%b/%Y:%H:%M:%S %z")
    _write(root, "dl.log", [f'10.0.1.3 - - [{stamp}] "GET /dl.php?f=dopeIPTV-1.6.8-x86_64.AppImage HTTP/1.0" 302 0 '
                            f'"" "{CHROME}" "81.2.99.99"'])
    _run(sc)
    day = _json(root, "stats.json")["days"][TODAY]
    visits = _json(root, "visits", TODAY + ".json")
    assert len(visits) == 1000 and day["downloads"] == {"Linux": 1}
    assert max(len(v["pages"]) for v in visits) == 2


def test_one_visit_stops_growing_however_much_is_sent_for_it():
    sc, root = _module()
    sc.MAX_VISIT_PAGES, sc.MAX_VISIT_EVENTS, sc.MAX_VISIT_DOWNLOADS = 5, 3, 2
    lines = [_view(at=NOW + timedelta(seconds=i)) for i in range(12)]
    lines += [_line("/hit?p=%2F&ev=donate", at=NOW + timedelta(seconds=20 + i)) for i in range(8)]
    _write(root, "stats.log", lines)
    stamp = NOW.strftime("%d/%b/%Y:%H:%M:%S %z")
    _write(root, "dl.log", [f'10.0.1.3 - - [{stamp}] "GET /dl.php?f=dopeIPTV-1.6.8.{i}-x86_64.AppImage HTTP/1.0" 302 0 '
                            f'"" "{UA}" "{IP}"' for i in range(6)])
    _run(sc)
    (visit,) = _json(root, "visits", TODAY + ".json")
    assert (len(visit["pages"]), len(visit["events"]), len(visit["downloads"])) == (5, 3, 2)
    day = _json(root, "stats.json")["days"][TODAY]
    assert day["views"] == 12 and day["events"] == {"donate": 8} and sum(day["downloads"].values()) == 6


def test_a_mark_seen_before_is_found_without_the_list_and_the_list_stays_the_same():
    sc, _root = _module()
    state: dict = {}
    marks = ["a", "b"]
    assert sc._first(state, "dl", "2026-11-19", marks, "a") is False
    assert sc._first(state, "dl", "2026-11-19", marks, "c") is True
    assert sc._first(state, "dl", "2026-11-19", marks, "c") is False
    assert marks == ["a", "b", "c"]
    other: list = []
    assert sc._first(state, "dl", "2026-11-20", other, "a") is True and other == ["a"]
    # A visitor's visits are found by the visitor: the newest that fits, as
    # going through the whole day found it.
    visits = sc.Visits({})
    visits.days["d"], visits._by_id["d"] = [], {}
    first = {"id": "v", "start": 1000, "end": 1300}
    later = {"id": "v", "start": 9000, "end": 9100}
    for v in ({"id": "w", "start": 1000, "end": 1000}, first, later):
        visits.add("d", v)
    assert visits.open_visit("d", "v", 9200) is later
    assert visits.open_visit("d", "v", 1500) is first        # a download's line, read after the pages'
    assert visits.open_visit("d", "v", 9100 + sc.VISIT_GAP + 1) is None
    assert visits.open_visit("d", "nobody", 1000) is None


def test_a_log_grown_huge_is_read_in_pieces_and_all_of_it():
    sc, root = _module()
    sc.READ_MAX = 4096
    lines = [_view(ua=f"{UA} piece/{i}", ip=f"81.2.70.{i % 250 + 1}") for i in range(120)]
    _write(root, "stats.log", lines)
    size = os.path.getsize(os.path.join(root, "stats.log"))
    assert size > 5 * sc.READ_MAX
    runs = 0
    while True:
        _run(sc)
        runs += 1
        if _json(root, "state.json")["logs"]["stats"]["off"] >= size:
            break
        assert runs < 50
    assert runs > 3
    assert _json(root, "stats.json")["days"][TODAY]["views"] == 120
    # Turned over (newsyslog) with more left in the old file than one run
    # reads: the rest of it first, then the new file.
    _write(root, "stats.log", [_view(ua=f"{UA} old/{i}", ip=f"81.2.71.{i % 250 + 1}") for i in range(60)])
    os.rename(os.path.join(root, "stats.log"), os.path.join(root, "stats.log.0"))
    _write(root, "stats.log", [_view(ua=f"{UA} new/{i}", ip=f"81.2.72.{i % 250 + 1}") for i in range(10)])
    for _ in range(40):
        _run(sc)
    assert _json(root, "stats.json")["days"][TODAY]["views"] == 190
    # A piece with no end of line in it is nothing nginx wrote: passed over.
    assert sc._whole_lines(b"x" * 10, True) == 10 and sc._whole_lines(b"x" * 10, False) == 0
    assert sc._whole_lines(b"ab\ncd", True) == 3


def test_what_the_run_writes_is_the_services_alone_to_read():
    sc, root = _module()
    stats = os.path.join(root, "stats")
    os.makedirs(os.path.join(stats, "visits"), mode=0o755)
    os.chmod(stats, 0o750)                       # as an earlier version made it
    os.chmod(os.path.join(stats, "visits"), 0o750)
    _write(root, "stats.log", [_view()])
    _run(sc)

    def mode(*path: str) -> int:
        return stat.S_IMODE(os.stat(os.path.join(stats, *path)).st_mode)
    assert mode() == 0o700 and mode("visits") == 0o700
    for name in ("stats.json", "state.json", os.path.join("visits", TODAY + ".json")):
        assert mode(name) == 0o600, name
    assert not os.path.exists(os.path.join(stats, "sketches.json")), "the sketches: never on the disk"
    # No key, no list of visitors' marks: nothing on disk leads back to one.
    for gone in ("visitor-keys.json", "uniques.json", "kinds.json"):
        assert not os.path.exists(os.path.join(stats, gone)), gone


def test_an_update_leaves_the_live_line_when_the_updates_themselves_go():
    sc, _root = _module()
    now = time.time()
    state = {"recent_updates": [[int(now - 40 * 86400), "a.AppImage", "1.6.6", "SE", 0],
                                [int(now - 3 * 86400), "b.AppImage", "1.6.7", "SE", 0],
                                [0, "undated", "1.6.5", "", 0]]}
    sc._forget_old_updates(state, now)
    assert [r[1] for r in state["recent_updates"]] == ["b.AppImage", "undated"]


def test_a_downloads_day_is_the_web_servers_own_whatever_a_name_says():
    # Authorization's user name is written before the time: one made up to
    # look like a date moves nothing.
    sc, root = _module()
    stamp = NOW.strftime("%d/%b/%Y:%H:%M:%S %z")
    _write(root, "dl.log", [f'10.0.1.3 - [31/Feb/2020 [{stamp}] "GET /dl.php?f=dopeIPTV-1.6.8-x86_64.AppImage HTTP/1.0" '
                            f'302 0 "" "{CHROME}" "{IP}"'])
    _run(sc)
    assert list(_json(root, "stats.json")["days"]) == [TODAY]


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"{len(tests)} passed")
