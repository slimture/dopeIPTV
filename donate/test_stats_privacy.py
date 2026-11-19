#!/usr/bin/env python3
"""What the visitor statistics keep about a payment (stats_collect.py,
analysis.py): a tip is counted per day only, never on a visit - the visit's
time, country and browser beside the payment service's record of who paid
would tie the two together - and a payment marked on a visit kept from
before is taken off once, in detail and anonymous alike.

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


def _hit(sc, stats, uniques, state, visits, ev, t):
    r = {"ip": IP, "t": t, "m": "GET", "u": f"/hit?p=/&ev={ev}&l=en", "s": 204, "a": UA}
    sc._page_line(stats, uniques, state, r, None, visits)


def test_a_payment_is_counted_per_day_and_never_on_the_visit():
    sc, _dir = _module()
    stats, uniques, state = {"days": {}}, {}, {}
    visits = sc.Visits(uniques)
    _hit(sc, stats, uniques, state, visits, "view", "2026-11-19T10:00:00+00:00")
    _hit(sc, stats, uniques, state, visits, "donate", "2026-11-19T10:00:30+00:00")
    _hit(sc, stats, uniques, state, visits, "donated", "2026-11-19T10:02:00+00:00")
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


def test_a_visitor_is_a_keyed_hash_with_a_key_of_the_month():
    import hashlib
    import hmac
    from datetime import date
    sc, _dir = _module()
    keys = sc.MonthKeys(None, date(2026, 11, 19), {"2026-11": "1" * 64, "2026-10": "2" * 64,
                                                   "2026-09": "3" * 64, "2025-11": "4" * 64})
    assert sorted(keys.keys) == ["2026-10", "2026-11"] and keys.changed   # older keys gone
    state = {"_keys": keys}
    vid = sc._hash(state, "2026-11-19", IP, UA)
    assert vid == hmac.new(bytes.fromhex("1" * 64), f"{IP}|{UA}".encode(), hashlib.sha256).hexdigest()[:12]
    assert sc._hash(state, "2026-11-02", IP, UA) == vid                  # the same within a month
    assert sc._hash(state, "2026-10-30", IP, UA) != vid                  # another month, another mark
    assert sc._hash_before(state, "2026-11-19", IP, UA) == sc._hash(state, "2026-10-01", IP, UA)
    # A line of a month whose key is gone gets one for this run alone.
    old = sc._hash(state, "2026-08-14", IP, UA)
    assert "2026-08" not in keys.keys and sc._hash(state, "2026-08-20", IP, UA) == old
    # January's month before is December.
    assert sc._month_before("2026-01") == "2025-12"


def test_returning_is_told_from_this_month_and_the_one_before_only():
    from datetime import date
    sc, _dir = _module()
    state = {"_keys": sc.MonthKeys(None, date(2026, 11, 19))}
    october = sc._hash(state, "2026-10-28", IP, UA)
    november = sc._hash(state, "2026-11-19", IP, UA)
    uniques = {"2026-09-03": [october], "2026-10-28": [october], "2026-11-02": [november]}
    visits = sc.Visits(uniques)
    assert visits.before("2026-11-19", november, sc._hash_before(state, "2026-11-19", IP, UA)) == 2
    assert visits.before("2026-11-19", november) == 1


def test_a_run_forgets_the_old_key_and_keeps_the_months_apart():
    import stat
    from datetime import datetime
    sc, stats = _module()
    with open(os.path.join(stats, "state.json"), "w") as fh:
        json.dump({"logs": {}, "secret": "ab" * 32}, fh)
    today = datetime.now().astimezone()
    line = {"ip": IP, "t": today.isoformat(timespec="seconds"), "m": "GET",
            "u": "/hit?p=/&ev=view&l=en", "s": 204, "r": "", "a": UA, "l": "en"}
    with open(sc.STATS_LOG, "a") as fh:
        fh.write(json.dumps(line) + "\n")
    sc.collect()
    with open(os.path.join(stats, "state.json")) as fh:
        state = json.load(fh)
    assert "secret" not in state and "_keys" not in state
    path = os.path.join(stats, "visitor-keys.json")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    with open(path) as fh:
        keys = json.load(fh)
    month = today.strftime("%Y-%m")
    assert list(keys) == [month]
    import hashlib
    import hmac
    (visit,) = sc._load_list(os.path.join(sc.VISITS_DIR, today.date().isoformat() + ".json"))
    assert visit["id"] == hmac.new(bytes.fromhex(keys[month]), f"{IP}|{UA}".encode(),
                                   hashlib.sha256).hexdigest()[:12]
    assert IP not in json.dumps(visit)


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



def test_a_months_hashes_go_with_its_key_and_its_counts_stay():
    from datetime import date
    sc, _dir = _module()
    import admin
    uniques = {"2026-09-01": ["a", "b"], "2026-09-02": ["a", "c", "r"], "2026-09-08": ["d"],
               "2026-10-05": ["x"], "2026-11-02": ["y"]}
    kinds = {"2026-09-01": {"p": ["a"], "r": []}, "2026-09-02": {"p": ["c"], "r": ["r"]},
             "2026-09-08": {"p": [], "r": []}, "2026-10-05": {"p": ["x"], "r": []}}
    frozen: dict = {}
    assert sc.freeze_months(uniques, kinds, frozen, date(2026, 11, 19)) == 1
    assert sorted(uniques) == ["2026-10-05", "2026-11-02"] and "2026-09-01" not in kinds
    sep = frozen["2026-09"]
    assert sep["unique"] == 4 and sep["people"] == 2 and sep["robots"] == 1
    assert sep["weeks"] == {"2026-08-31": 3, "2026-09-07": 1}
    assert not any(f'"{h}"' in json.dumps(frozen) for h in "abcdrxy"), "no hash is kept"
    # Counted once: a second run leaves the numbers as they are.
    assert sc.freeze_months(uniques, kinds, frozen, date(2026, 11, 20)) == 0
    # The admin page: a whole month from its count, a whole week from its
    # own, a few days of a week as the days' visitors summed.
    per_day = {"2026-09-01": {"visitors": 2}, "2026-09-02": {"visitors": 2},
               "2026-09-08": {"visitors": 1}, "2026-10-05": {"visitors": 1}}
    sept = ["2026-09-%02d" % i for i in range(1, 31)]
    assert admin._union(uniques, sept, per_day, kinds, frozen) == 4
    assert admin._union(uniques, sept + ["2026-10-05"], per_day, kinds, frozen) == 5
    assert admin._union(uniques, ["2026-09-%02d" % i for i in range(1, 7)], per_day, kinds, frozen) == 3
    assert admin._union(uniques, ["2026-09-02", "2026-09-08"], per_day, kinds, frozen) == 2 + 1
    j = admin._judged(uniques, kinds, sept, per_day, frozen)
    assert j["people"] == 2 and j["robots"] == 1 and j["all"]


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


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"{len(tests)} passed")
