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
        fh.write(f"STATS_DIR={stats}\n")
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


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"{len(tests)} passed")
