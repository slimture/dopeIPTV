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


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"{len(tests)} passed")
