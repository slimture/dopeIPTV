#!/usr/bin/env python3
"""What the visitor statistics keep (stats_collect.py, sketch.py,
stats_inbox.py, analysis.py, admin.py):

- no address is ever written: nginx's lines wait in memory, and nothing the
  run writes holds one;
- the month keys are in memory alone - never a file on a disk;
- a visit kept names its visitor for its day alone, its mark sealed, its
  times coarse, a screen by its kind, a site by its registered name;
- how many different visitors came is kept in sketches, never as marks;
- a whole month of anonymous visits is k-anonymous by different people,
  told apart by their sealed marks - a key gone counts as one person;
- a quiet day's lists keep no name fewer than five once a month old;
- a tip is counted per day only, never on a visit, and a payment marked on
  a visit kept from before is taken off once.

Plain Python: ``python3 donate/test_stats_privacy.py`` runs them all
(pytest finds them too). Everything in a temporary directory.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
IP = "81.2.69.160"                       # a public address


def _module():
    root = tempfile.mkdtemp()
    stats = os.path.join(root, "stats")
    os.makedirs(stats)
    conf = os.path.join(root, "donate.conf")
    with open(conf, "w") as fh:
        fh.write(f"STATS_DIR={stats}\nSTATS_LOG={root}/stats.log\nDL_LOG={root}/dl.log\n"
                 f"GEO_DB={root}/none.mmdb\n")
    for name in ("stats.log", "dl.log"):
        open(os.path.join(root, name), "w").close()
    os.environ["DONATE_CONFIG"] = conf
    os.environ["STATS_DIR"] = stats
    for name in ("donate", "stats_collect", "analysis"):
        sys.modules.pop(name, None)
    import stats_collect
    assert stats_collect.STATS_DIR == stats
    return stats_collect, stats


def _hit(sc, stats, state, visits, ev, t):
    r = {"ip": IP, "t": t, "m": "GET", "u": f"/hit?p=/&ev={ev}&l=en", "s": 204, "a": UA}
    sc._page_line(stats, state, r, None, visits)


def test_a_payment_is_counted_per_day_and_never_on_the_visit():
    sc, _dir = _module()
    stats, state = {"days": {}}, {}
    visits = sc.Visits({}, state)
    _hit(sc, stats, state, visits, "view", "2026-11-19T10:00:00+00:00")
    _hit(sc, stats, state, visits, "donate", "2026-11-19T10:00:30+00:00")
    _hit(sc, stats, state, visits, "donated", "2026-11-19T10:02:00+00:00")
    day = stats["days"]["2026-11-19"]
    assert day["events"] == {"donate": 1, "donated": 1}
    (visit,) = visits.day("2026-11-19")
    assert [e[1] for e in visit["events"]] == ["donate"]
    assert "donated" not in json.dumps(visit)
    anon = sc.anonymous(visit, "2026-11-19")
    assert anon["donate"] is True and "donated" not in anon

    import analysis
    assert "donated" not in analysis.FIELDS
    assert len(analysis.row(visit, "2026-11-19")) == len(analysis.FIELDS)


def test_a_payment_kept_on_a_visit_from_before_is_taken_off_once():
    sc, stats = _module()
    os.makedirs(sc.VISITS_DIR)
    os.makedirs(sc.ANON_DIR)
    day = os.path.join(sc.VISITS_DIR, "2026-10-01.json")
    with open(day, "w") as fh:
        json.dump([{"id": "a", "events": [[1, "donate"], [2, "donated"]], "pages": [[1, "/", 0]]},
                   {"id": "b", "events": [], "pages": [[1, "/", 0]]}], fh)
    month = os.path.join(sc.ANON_DIR, "2025-09.json")
    with open(month, "w") as fh:
        json.dump({"days": ["2025-09-01"], "visits": [{"day": "2025-09-01", "donate": True, "donated": True}]}, fh)
    state: dict = {}
    sc._drop_payments(state)
    with open(day) as fh:
        assert [v["events"] for v in json.load(fh)] == [[[1, "donate"]], []]
    with open(month) as fh:
        assert json.load(fh)["visits"] == [{"day": "2025-09-01", "donate": True}]
    assert state["payments_off_visits"] == 1
    os.unlink(day)
    sc._drop_payments(state)                         # once: not looked for again
    assert not os.path.exists(day)


def _today():
    from datetime import datetime
    return datetime.now().astimezone().replace(microsecond=0)


def _walk_text(folder: str) -> str:
    out = []
    for where, _dirs, files in os.walk(folder):
        for f in files:
            with open(os.path.join(where, f), "rb") as fh:
                out.append(fh.read().decode("utf-8", "replace"))
    return "\n".join(out)


def test_a_visitor_is_a_keyed_hash_with_keys_in_memory_alone():
    import hashlib
    import hmac
    from datetime import date
    sc, stats = _module()
    keys = sc.MonthKeys(date(2026, 11, 19), keys={"2026-11": "1" * 64, "2026-10": "2" * 64,
                                                  "2026-09": "3" * 64, "2026-08": "4" * 64})
    assert sorted(keys.keys) == ["2026-09", "2026-10", "2026-11"]       # older keys forgotten
    assert not keys.folder, "no folder on a disk is ever used for them"
    state = {"_keys": keys}
    vid = sc._hash(state, "2026-11-19", IP, UA)
    assert vid == hmac.new(bytes.fromhex("1" * 64), f"{IP}|{UA}".encode(), hashlib.sha256).hexdigest()[:12]
    assert sc._hash(state, "2026-11-02", IP, UA) == vid                  # the same within a month
    assert sc._hash(state, "2026-10-30", IP, UA) != vid                  # another month, another mark
    assert sc._hash_before(state, "2026-11-19", IP, UA) == sc._hash(state, "2026-10-01", IP, UA)
    # A line of a month whose key is gone gets one for this run alone.
    old = sc._hash(state, "2026-07-14", IP, UA)
    assert "2026-07" not in keys.keys and sc._hash(state, "2026-07-20", IP, UA) == old
    assert sc._month_before("2026-01") == "2025-12"
    # A folder on a disk is not a place for them; one in memory (tmpfs) is.
    on_disk = os.path.join(stats, "keys")
    os.makedirs(on_disk)
    assert not sc.MonthKeys(date(2026, 11, 19), on_disk).folder
    if os.path.isdir("/dev/shm") and sc.in_memory("/dev/shm"):
        mem = tempfile.mkdtemp(dir="/dev/shm")
        k1 = sc.MonthKeys(date(2026, 11, 19), mem)
        assert k1.folder == mem
        first = k1.key("2026-11")
        assert sc.MonthKeys(date(2026, 11, 19), mem).key("2026-11") == first    # through a restart
        import shutil
        shutil.rmtree(mem)


def test_returning_is_told_from_this_month_and_the_one_before_only():
    from datetime import date
    sc, _dir = _module()
    import sketch
    state = {"_keys": sc.MonthKeys(date(2026, 11, 19))}
    october = sc._hash(state, "2026-10-28", IP, UA)
    november = sc._hash(state, "2026-11-19", IP, UA)
    sketches = {"2026-09-03": {"u": sketch.encode(sketch.of([october]))},
                "2026-10-28": {"u": sketch.encode(sketch.of([october]))},
                "2026-11-02": {"u": sketch.encode(sketch.of([november]))}}
    visits = sc.Visits(sketches, state)
    assert visits.before("2026-11-19", november, sc._hash_before(state, "2026-11-19", IP, UA)) == 2
    assert visits.before("2026-11-19", november) == 1


def test_no_address_and_no_key_is_ever_written():
    """The lines wait in memory (the inbox), the run counts them, and nothing
    on disk holds the address, a key, or a mark of the visitor - only a
    day's name for it and the mark sealed."""
    import stat
    sc, stats = _module()
    with open(os.path.join(stats, "state.json"), "w") as fh:
        json.dump({"logs": {}, "secret": "ab" * 32, "seen_dl": {"2026-11-19": ["abc"]}}, fh)
    now = _today()
    page = json.dumps({"ip": IP, "t": now.isoformat(timespec="seconds"), "m": "GET",
                       "u": "/hit?p=/&ev=view&l=en&bl=sv-SE&w=1728&h=1117&r=https%3A%2F%2Falice.example.net%2Fp",
                       "s": 204, "a": UA, "l": "en"})
    stamp = now.strftime("%d/%b/%Y:%H:%M:%S %z")
    dl = (f'10.0.0.5 - - [{stamp}] "GET /dl.php?f=dopeIPTV-1.6.8-x86_64.AppImage HTTP/1.0" 302 0 '
          f'"https://dopeiptv.org/" "{UA}" "{IP}"')
    sc.INBOX.put("iptv_stats", page)
    sc.INBOX.put("iptv_dl", dl)
    sc.collect()
    assert len(sc.INBOX) == 0
    everything = _walk_text(stats)
    assert IP not in everything and "81.2.69" not in everything
    assert "alice" not in everything, "a name made for one visitor's link"
    for gone in ("visitor-keys.json", "uniques.json", "kinds.json"):
        assert not os.path.exists(os.path.join(stats, gone))
    with open(os.path.join(stats, "state.json")) as fh:
        state = json.load(fh)
    assert not {"secret", "seen_dl"} & set(state) and not [k for k in state if k.startswith("_")]
    (visit,) = sc._load_list(os.path.join(sc.VISITS_DIR, now.date().isoformat() + ".json"))
    keys = sc.keys_now()
    vid = sc._hash({"_keys": keys}, now.date().isoformat(), IP, UA)
    assert vid not in everything, "the visitor's mark is never written as it is"
    assert visit["id"] == sc.day_id({"_keys": keys}, now.date().isoformat(), vid)
    assert sc.unseal(keys, now.date().isoformat(), visit["w"], visit["e"]) == vid
    assert visit["start"] % sc.COARSE == 0 and visit["screen"] == "Large screen" and visit["blang"] == "sv"
    assert visit["ref"] == "example.net" and "ref_page" not in visit
    assert visit["downloads"][0][1] == "dopeIPTV-1.6.8-x86_64.AppImage"
    assert stat.S_IMODE(os.stat(os.path.join(stats, "sketches.json")).st_mode) == 0o600
    # A key gone (the service started again without it): the mark cannot be opened.
    from datetime import date
    assert sc.unseal(sc.MonthKeys(date.today()), now.date().isoformat(), visit["w"], visit["e"]) == ""


def test_a_visit_kept_names_its_visitor_for_its_day_alone():
    from datetime import date
    sc, _dir = _module()
    state = {"_keys": sc.MonthKeys(date(2026, 11, 19))}
    vid = sc._hash(state, "2026-11-19", IP, UA)
    one = sc.at_rest({"id": vid, "start": 1763550123, "end": 1763550203, "pages": [[1763550123, "/", 47]],
                      "time": 47, "channel": "Search", "ref": "forum.example.net", "source": "alice-2026",
                      "screen": "390×844", "blang": "sv-SE", "ref_page": "forum.example.net/t/1",
                      "os": "Linux (Arch)"}, "2026-11-19", state)
    two = sc.at_rest({"id": vid, "start": 1763636523, "end": 1763636523, "pages": []}, "2026-11-20", state)
    again = sc.at_rest({"id": vid, "start": 1763550900, "end": 1763550900, "pages": []}, "2026-11-19", state)
    assert one["id"] == again["id"] != two["id"], "the same name within a day, another the next"
    assert one["w"] != again["w"], "a new nonce each time: two visits' seals look nothing alike"
    # The start to the ten minutes; what came after it, to ten seconds from there.
    assert one["start"] == 1763550123 - 1763550123 % 600 and one["end"] - one["start"] == 80
    assert one["pages"] == [[one["start"], "/", 50]] and one["time"] == 50
    assert one["source"] == "other" and one["ref"] == "example.net" and one["screen"] == "Phone"
    assert one["blang"] == "sv" and one["os"] == "Linux" and "ref_page" not in one
    assert sc.at_rest(one, "2026-11-19", state)["start"] == one["start"], "kept as it was, again"
    assert sc.source_of("app-about") == "app-about" and sc.source_of("github-issues") == "github-issues"
    assert sc.source_of("chatgpt.com") == "chatgpt.com" and sc.source_of("newsletter-bob") == "other"
    assert sc.site_of("old.reddit.com") == "old.reddit.com" and sc.site_of("a.b.example.co.uk") == "example.co.uk"
    assert sc.site_of("203.0.113.9") == "an address"


def test_a_visit_goes_on_after_a_restart_with_its_keys_and_not_without():
    sc, stats = _module()
    now = _today()
    line = lambda ev, t: json.dumps({"ip": IP, "t": t.isoformat(timespec="seconds"), "m": "GET",  # noqa: E731
                                     "u": f"/hit?p=/&ev={ev}&l=en", "s": 204, "a": UA, "l": "en"})
    sc.INBOX.put("iptv_stats", line("view", now))
    sc.collect()
    sc._LIVE.clear()                            # as after a restart that kept the keys
    sc._SEEN.clear()
    sc.INBOX.put("iptv_stats", line("view", now.replace(second=0) if now.second else now))
    sc.collect()
    day = sc._load(os.path.join(stats, "stats.json"), {})["days"][now.date().isoformat()]
    assert day["visitors"] == 1, "the same visitor: its mark unsealed again"
    (visit,) = sc._load_list(os.path.join(sc.VISITS_DIR, now.date().isoformat() + ".json"))
    assert len(visit["pages"]) == 2


def test_a_months_sketches_go_and_its_counts_stay():
    from datetime import date
    sc, _dir = _module()
    import admin
    import sketch

    def entry(u, p=None, r=()):
        e = {"u": sketch.encode(sketch.of(u))}
        if p is not None:
            e["n"] = sketch.encode(sketch.of(m for m in u if m not in r))
            e["p"] = sketch.encode(sketch.of(p))
        return e
    hx = {c: hashlib_hex(c) for c in "abcdrxy"}
    sketches = {"2026-09-01": entry([hx["a"], hx["b"]], [hx["a"]]),
                "2026-09-02": entry([hx["a"], hx["c"], hx["r"]], [hx["c"]], [hx["r"]]),
                "2026-09-08": entry([hx["d"]], []),
                "2026-10-05": entry([hx["x"]], [hx["x"]]), "2026-11-02": entry([hx["y"]])}
    frozen: dict = {}
    assert sc.freeze_months(sketches, frozen, date(2026, 11, 19)) == 1
    assert sorted(sketches) == ["2026-10-05", "2026-11-02"]
    sep = frozen["2026-09"]
    assert sep["unique"] == 4 and sep["people"] == 2 and sep["robots"] == 1
    assert sep["weeks"] == {"2026-08-31": 3, "2026-09-07": 1}
    assert sc.freeze_months(sketches, frozen, date(2026, 11, 20)) == 0
    per_day = {"2026-09-01": {"visitors": 2}, "2026-09-02": {"visitors": 2},
               "2026-09-08": {"visitors": 1}, "2026-10-05": {"visitors": 1}}
    sept = ["2026-09-%02d" % i for i in range(1, 31)]
    assert admin._union(sketches, sept, per_day, frozen) == 4
    assert admin._union(sketches, sept + ["2026-10-05"], per_day, frozen) == 5
    assert admin._union(sketches, ["2026-09-%02d" % i for i in range(1, 7)], per_day, frozen) == 3
    assert admin._union(sketches, ["2026-09-02", "2026-09-08"], per_day, frozen) == 2 + 1
    j = admin._judged(sketches, sept, per_day, frozen)
    assert j["people"] == 2 and j["robots"] == 1 and j["all"]
    # This month's, from the sketches themselves.
    live = {"2026-11-01": entry([hx["a"], hx["b"], hx["r"]], [hx["a"]], [hx["r"]]),
            "2026-11-02": entry([hx["a"], hx["c"]], [hx["a"], hx["c"]])}
    pd = {"2026-11-01": {"visitors": 3}, "2026-11-02": {"visitors": 2}}
    assert admin._union(live, ["2026-11-01", "2026-11-02"], pd) == 3          # a, b, c
    j = admin._judged(live, ["2026-11-01", "2026-11-02"], pd)
    assert j["people"] == 2 and j["robots"] == 1


def hashlib_hex(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()[:12]


def test_a_sketch_counts_closely_and_reads_nothing_it_should_not():
    import sketch
    marks = [hashlib_hex(str(i)) for i in range(3000)]
    field = sketch.of(marks)
    assert abs(sketch.count(field) - 3000) < 30
    assert all(sketch.has(field, m) for m in marks[:50])
    assert sum(sketch.has(field, hashlib_hex("x" + str(i))) for i in range(2000)) < 10
    assert sketch.decode(sketch.encode(field)) == field
    for bad in ("", "x", None, 5, "A" * 100000, sketch.encode(field)[:-4]):
        assert sketch.decode(bad) == 0
    import base64
    import zlib
    bomb = base64.b64encode(zlib.compress(b"\0" * (sketch.BITS // 8 * 4))).decode()
    assert sketch.decode(bomb) == 0, "never more than a field unpacked"


def test_the_old_keys_and_lists_are_made_sketches_once_and_deleted():
    sc, stats = _module()
    with open(os.path.join(stats, "visitor-keys.json"), "w") as fh:
        json.dump({"2026-11": "1" * 64}, fh)
    a, b, r = hashlib_hex("a"), hashlib_hex("b"), hashlib_hex("r")
    with open(os.path.join(stats, "uniques.json"), "w") as fh:
        json.dump({"2026-11-18": [a, b, r]}, fh)
    with open(os.path.join(stats, "kinds.json"), "w") as fh:
        json.dump({"2026-11-18": {"p": [a], "r": [r]}}, fh)
    os.makedirs(sc.VISITS_DIR)
    with open(os.path.join(sc.VISITS_DIR, "2026-11-18.json"), "w") as fh:
        json.dump([{"id": a, "start": 1763460007, "end": 1763460057, "pages": [[1763460007, "/", 50]],
                    "time": 50, "channel": "Direct or unknown", "ref": "", "source": "", "lang": "en",
                    "blang": "en-GB", "screen": "1920×1080", "os": "Linux (Ubuntu)", "browser": "Firefox",
                    "bv": "140", "device": "Computer", "country": "SE", "returning": 0,
                    "downloads": [], "events": []}], fh)
    state: dict = {"_keys": sc.keys_now()}
    sc._migrate(state, os.path.join(stats, "sketches.json"))
    for gone in ("visitor-keys.json", "uniques.json", "kinds.json"):
        assert not os.path.exists(os.path.join(stats, gone))
    import admin
    sk = sc._load(os.path.join(stats, "sketches.json"), {})
    assert admin._union(sk, ["2026-11-18"], {"2026-11-18": {"visitors": 3}}) == 2
    assert admin._judged(sk, ["2026-11-18"], {"2026-11-18": {"visitors": 3}})["robots"] == 1
    (v,) = sc._load_list(os.path.join(sc.VISITS_DIR, "2026-11-18.json"))
    assert v["id"] != a and a not in json.dumps(v) and v["screen"] == "Large screen" and v["blang"] == "en"
    assert sc.unseal(state["_keys"], "2026-11-18", v["w"], v["e"]) == a
    assert state["kept_since"] == 2
    sc._migrate(state, os.path.join(stats, "sketches.json"))              # once
    assert sc._load_list(os.path.join(sc.VISITS_DIR, "2026-11-18.json")) == [v]


def test_a_quiet_days_lists_keep_no_name_fewer_than_five_once_a_month_old():
    sc, _dir = _module()
    import admin
    stats = {"days": {
        "2026-09-01": {"countries": {"SE": 7, "IS": 1}, "langs": {"sv": 6, "is": 1}, "views": 8,
                       "robots": {"lists": {"countries": {"SE": 1}, "pages": {"/": 1}}}},
        "2026-09-02": {"countries": {"SE": 6, "IS": 2}, "langs": {"sv": 8}, "views": 8},
        "2026-09-03": {"countries": {"NO": 3}, "views": 3}}}
    for d in list(stats["days"]):
        sc.fold_day(stats, d)
        sc.fold_day(stats, d)                   # once
    d1 = stats["days"]["2026-09-01"]
    assert d1["countries"] == {"SE": 6, "other": 1} and d1["langs"] == {"sv": 6, "other": 1}
    assert "countries" not in d1["robots"]["lists"] and d1["robots"]["lists"]["pages"] == {"/": 1}
    assert stats["days"]["2026-09-03"]["countries"] == {"other": 3}
    rest = stats["rest"]["2026-09"]
    assert rest["countries"] == {"IS": 3, "NO": 3} and rest["langs"] == {"is": 1}
    sc._close_rest(stats, "2026-11")
    assert rest["countries"] == {"other": 6} and rest["langs"] == {"other": 1} and rest["k"] == 5
    # A month whole in the span: its own names are shown again, out of "other".
    stats2 = {"days": {"2026-09-01": {"countries": {"SE": 9, "other": 6}}},
              "rest": {"2026-09": {"countries": {"IS": 6}}}}
    sums = {"countries": {"SE": 9, "other": 6}}
    admin._add_rest(sums, stats2, ["2026-09-01"])
    assert sums["countries"] == {"SE": 9, "IS": 6}
    part = {"countries": {"SE": 9, "other": 6}}
    admin._add_rest(part, {"days": {"2026-09-01": {}, "2026-09-02": {}}, "rest": stats2["rest"]}, ["2026-09-01"])
    assert part["countries"] == {"SE": 9, "other": 6}, "a month only in part stays as it is"


def test_the_socket_hands_nginxs_lines_to_memory():
    import socket
    import stats_inbox
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "stats.sock")
    inbox = stats_inbox.Inbox(most=4096)
    rec = stats_inbox.Receiver(path, inbox)
    rec.start()
    try:
        out = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        out.sendto(b'<190>Nov 20 12:00:00 iptv_stats: {"ip":"81.2.69.160"}', path)
        out.sendto(b'<190>Nov  5 01:02:03 host iptv_dl: 10.0.0.5 - - [x] "GET /dl.php"', path)
        out.sendto(b"nothing nginx sent", path)
        out.sendto(b"<190>Nov 20 12:00:00 other_tag: x", path)
        import time
        for _ in range(100):
            if inbox.passed >= 2 and len(inbox) >= 2:
                break
            time.sleep(0.02)
        lines, dropped = inbox.take()
        assert lines == [("iptv_stats", '{"ip":"81.2.69.160"}'), ("iptv_dl", '10.0.0.5 - - [x] "GET /dl.php"')]
        assert dropped == 0 and len(inbox) == 0 and inbox.passed == 2
        import stat
        assert stat.S_IMODE(os.stat(path).st_mode) & 0o007 == 0, "never every user's"
        assert inbox.put("iptv_stats", "x" * 4000) and not inbox.put("iptv_stats", "x" * 200)
        assert inbox.take()[1] == 1, "a flood past the bound is dropped and counted"
    finally:
        rec.close()
    assert not os.path.exists(path)


def test_an_update_is_kept_at_its_hour_and_for_thirty_days():
    sc, _dir = _module()
    row = sc._update_row(1763582671, "dopeIPTV-1.6.8-x86_64.AppImage", "1.6.7", "SE", False,
                         "dopeIPTV/1.6.7 (Linux; x86_64)")
    assert row["t"] == 1763582671 - 1763582671 % 3600
    os.makedirs(sc.UPDATES_DIR)
    day = os.path.join(sc.UPDATES_DIR, "2026-11-19.json")
    with open(day, "w") as fh:
        json.dump([{"t": 1763582671, "file": "x"}], fh)
    state = {"recent_updates": [[1763582671, "x", "1.6.7", "SE", 0]]}
    sc._updates_to_the_hour(state)
    with open(day) as fh:
        assert json.load(fh)[0]["t"] % 3600 == 0
    assert state["recent_updates"][0][0] % 3600 == 0 and state["updates_hourly"] == 1
    assert sc.UPDATES_DAYS == 30


def test_a_whole_month_of_visits_is_k_anonymous():
    sc, _dir = _module()
    base = {"country": "SE", "browser": "Firefox", "os": "Linux", "device": "desktop", "lang": "sv",
            "channel": "", "site": "", "pages": 2, "time": 40, "timed": True, "returning": False,
            "downloaded": False, "donate": False, "kind": ""}
    rows = [dict(base, day="2026-09-%02d" % (1 + i % 3)) for i in range(15)]
    lone = dict(base, day="2026-09-17", country="IS", browser="Lynx", site="example.org",
                pages=37, time=1234, donate=True)
    rows.append(lone)
    out = sc.k_anonymous(rows)
    key = lambda a: tuple(a.get(f) for f in sc._QI)  # noqa: E731
    counts: dict = {}
    for a in out:
        counts[key(a)] = counts.get(key(a), 0) + 1
    assert all(n >= sc.K_ANON for n in counts.values())
    assert not any(a.get("site") == "example.org" or a.get("browser") == "Lynx" for a in out)
    assert all(a["pages"] in (1, 2, 3, 5, 6) and a["time"] in sc._TIMES for a in out)
    # Fewer than K_ANON visits in a month: none kept one by one.
    assert sc.k_anonymous(rows[:3]) == []
    # The month is made so once it is whole, and only once.
    os.makedirs(sc.ANON_DIR, exist_ok=True)
    path = os.path.join(sc.ANON_DIR, "2026-09.json")
    with open(path, "w") as fh:
        json.dump({"days": ["2026-09-01"], "visits": rows}, fh)
    sc._close_months()
    with open(path) as fh:
        month = json.load(fh)
    assert month["k"] == sc.K_ANON and len(month["visits"]) == len(out)
    sc._close_months()


def test_k_anonymity_counts_different_people_not_visits():
    """One person who came five times is one of the five, not five: a
    combination kept is shared by five different visitors, told apart by
    their sealed marks - which are never kept. A mark whose key is gone
    counts as one person at the most."""
    from datetime import date
    sc, _dir = _module()
    keys = sc.MonthKeys(date(2026, 10, 19))
    state = {"_keys": keys}
    base = {"day": "2026-09-01", "country": "SE", "browser": "Firefox", "os": "Linux",
            "device": "desktop", "lang": "sv", "channel": "", "site": "", "pages": 2, "time": 40,
            "timed": True, "returning": False, "downloaded": False, "donate": False, "kind": ""}

    def sealed(vid):
        w, e = sc.seal(state, "2026-09-01", vid)
        return dict(base, w=w, e=e)
    one = hashlib_hex("p1")
    assert sc.k_anonymous([sealed(one) for _ in range(6)], keys=keys) == [], "six visits of one person"
    five = [sealed(hashlib_hex(f"p{i}")) for i in range(5)]
    out = sc.k_anonymous(five, keys=keys)
    assert len(out) == 5 and not any(k in a for a in out for k in ("w", "e", "who"))
    gone = sc.MonthKeys(date(2026, 10, 19))             # the service started again without them
    assert sc.k_anonymous(five, keys=gone) == [], "a key gone: one person at the most"
    # Kept before marks were sealed: "who" told people apart, as then.
    assert len(sc.k_anonymous([dict(base, who=f"p{i}") for i in range(5)], keys=keys)) == 5
    # One person on one device whose address changed five times: five
    # visitor marks, one device - one person.
    dev = hashlib_hex("one phone")

    def moved(i):
        w, e = sc.seal(state, "2026-09-01", hashlib_hex(f"addr{i}"))
        return dict(base, w=w, e=e, f=sc.seal(state, "2026-09-01", dev)[0])
    assert sc.k_anonymous([moved(i) for i in range(5)], keys=keys) == [], "one device, five addresses"
    devs = [dict(sealed(hashlib_hex(f"p{i}")), f=sc.seal(state, "2026-09-01", hashlib_hex(f"d{i}"))[0])
            for i in range(5)]
    out = sc.k_anonymous(devs, keys=keys)
    assert len(out) == 5 and not any("f" in a for a in out), "five people, five devices: kept, marks gone"
    a = sc.anonymous({"id": one, "w": "ab" * 12, "e": "cd" * 4, "pages": ["/"], "browser": "Firefox"},
                     "2026-09-01")
    assert a["w"] == "ab" * 12 and "id" not in a and one not in json.dumps(a)


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"{len(tests)} passed")


def test_the_page_that_linked_is_never_kept():
    """Its path can name a person (a profile, a shared link) where its site
    does not: only the site is counted, and days kept before lose theirs."""
    sc, _dir = _module()
    import inspect
    src = inspect.getsource(sc)
    assert '_bump(d.setdefault("ref_pages"' not in src and 'add("ref_pages"' not in src
    assert "ref_pages" not in sc.FOLD
    assert 'd.pop("ref_pages", None)' in src and 'rest.pop("ref_pages", None)' in src
