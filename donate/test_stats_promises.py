#!/usr/bin/env python3
"""The statistics' promises kept by the code, not by habit (stats_collect.py,
admin.py, donate.py):

- a day's visits are kept 14 days at the most, whatever STATS_VISITS_DAYS
  says;
- what should be gone is looked for after every run and deleted again; what
  cannot be deleted is said once in the log and on the admin page until it
  is gone;
- swap is asked of the system: swap that keeps what it is given is said on
  the admin page.

Plain Python: ``python3 donate/test_stats_promises.py`` runs them all
(pytest finds them too). Everything in a temporary directory.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _module(extra: str = ""):
    root = tempfile.mkdtemp()
    stats = os.path.join(root, "stats")
    os.makedirs(stats)
    conf = os.path.join(root, "donate.conf")
    with open(conf, "w") as fh:
        fh.write(f"STATS_DIR={stats}\nSTATS_LOG={root}/stats.log\nDL_LOG={root}/dl.log\n"
                 f"GEO_DB={root}/none.mmdb\n{extra}")
    os.environ["DONATE_CONFIG"] = conf
    os.environ["STATS_DIR"] = stats
    for name in ("donate", "stats_collect", "analysis", "admin"):
        sys.modules.pop(name, None)
    import stats_collect
    assert stats_collect.STATS_DIR == stats
    return stats_collect, stats


def test_visits_are_never_kept_past_fourteen_days():
    sc, _dir = _module("STATS_VISITS_DAYS=60\n")
    assert sc.VISITS_DAYS_MAX == 14 and sc.VISITS_DAYS == 14
    sc, _dir = _module("STATS_VISITS_DAYS=7\n")
    assert sc.VISITS_DAYS == 7, "shorter is allowed"
    sc, _dir = _module("STATS_VISITS_DAYS=0\n")
    assert sc.VISITS_DAYS == 1
    sc, _dir = _module("STATS_VISITS_DAYS=forever\n")
    assert sc.VISITS_DAYS == 14
    # The admin page and the start line take the cut value, not the file's.
    src = open(os.path.join(HERE, "donate.py"), encoding="utf-8").read()
    assert 'body["visits_days"] = int(getattr(stats_collect, "VISITS_DAYS", 14))' in src
    assert 'CONF.get("STATS_VISITS_DAYS", "14") or 14' not in src
    assert "visits kept {stats_collect.VISITS_DAYS} days" in src


def _old_and_new(sc):
    today = date.today()
    old = (today - timedelta(days=sc.VISITS_DAYS + 3)).isoformat()
    recent = (today - timedelta(days=1)).isoformat()
    long_ago = (today - timedelta(days=sc.UPDATES_DAYS + 3)).isoformat()
    os.makedirs(sc.VISITS_DIR, exist_ok=True)
    os.makedirs(sc.UPDATES_DIR, exist_ok=True)
    os.makedirs(sc.ANON_DIR, exist_ok=True)
    paths = {
        "keys": os.path.join(sc.STATS_DIR, "visitor-keys.json"),
        "uniques": os.path.join(sc.STATS_DIR, "uniques.json"),
        "kinds": os.path.join(sc.STATS_DIR, "kinds.json"),
        "sketches": os.path.join(sc.STATS_DIR, "sketches.json"),
        "anon": os.path.join(sc.ANON_DIR, "2026-09.json"),
        "old_visits": os.path.join(sc.VISITS_DIR, old + ".json"),
        "old_update": os.path.join(sc.UPDATES_DIR, long_ago + ".json"),
    }
    keep = {
        "visits": os.path.join(sc.VISITS_DIR, recent + ".json"),
        "update": os.path.join(sc.UPDATES_DIR, recent + ".json"),
        "stats": os.path.join(sc.STATS_DIR, "stats.json"),
    }
    for p in (*paths.values(), *keep.values()):
        with open(p, "w") as fh:
            json.dump({}, fh)
    return paths, keep


def test_what_should_be_gone_is_looked_for_and_deleted_again():
    sc, _dir = _module()
    paths, keep = _old_and_new(sc)
    assert sorted(sc.left_behind()) == sorted(paths.values())
    assert sc.sweep_left() == []
    for p in paths.values():
        assert not os.path.exists(p), p
    for p in keep.values():
        assert os.path.exists(p), p
    assert not os.path.exists(sc.ANON_DIR), "the emptied folder of before goes too"
    assert sc.left_behind() == [] and sc.TROUBLE == {}
    # Every run ends with it, after the days have aged out.
    import inspect
    src = inspect.getsource(sc.collect)
    assert src.index("_forget_old(stats)") < src.index("sweep_left(today)") < src.index("_save(stats_path")


def test_a_file_that_cannot_be_deleted_is_said_and_tried_again():
    sc, _dir = _module()
    os.makedirs(sc.VISITS_DIR)
    old = (date.today() - timedelta(days=sc.VISITS_DAYS + 1)).isoformat()
    stuck = os.path.join(sc.VISITS_DIR, old + ".json")
    os.makedirs(stuck)                          # a folder: unlink cannot take it
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        assert sc.sweep_left() == [stuck]
        assert sc.sweep_left() == [stuck]
    assert said.getvalue().count("could not delete") == 1, "said once, tried every run"
    assert stuck in said.getvalue()
    assert stuck in sc.TROUBLE
    sc._SWAP.clear()
    sc._SWAP.update(at=1e18, answer={"state": "none", "plain": []})
    (t,) = sc.trouble()
    assert t["what"] == "delete" and t["path"] == stuck and t["why"]
    # The admin page says it at the top.
    import admin
    assert admin._trouble() == [t]
    js = open(os.path.join(HERE, "admin", "admin.js"), encoding="utf-8").read()
    html = open(os.path.join(HERE, "admin", "index.html"), encoding="utf-8").read()
    assert 'id="stats-warn" role="alert"' in html and "statsTrouble();" in js
    adm = open(os.path.join(HERE, "admin.py"), encoding="utf-8").read()
    assert '"trouble": _trouble(),' in adm
    # Gone at last: forgotten.
    os.rmdir(stuck)
    assert sc.sweep_left() == [] and sc.TROUBLE == {} and sc.trouble() == []


def test_every_deletion_of_the_statistics_goes_through_the_check():
    # No delete of what should be gone is silent: every one is _gone's.
    sc, _dir = _module()
    import inspect
    for fn in (sc._sketches_off_disk, sc._migrate, sc._old_rows_off, sc._forget_old):
        src = inspect.getsource(fn)
        assert "os.unlink" not in src and "os.rmdir" not in src, fn.__name__
        assert "_gone" in src, fn.__name__


def test_swap_is_asked_of_the_system():
    sc, _dir = _module()
    proc = ("Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"
            "/dev/dm-1                               partition\t8388604\t\t0\t\t-2\n"
            "/dev/zram0                              partition\t4194300\t\t0\t\t100\n")
    assert sc.swap_listed(proc) == ["/dev/dm-1", "/dev/zram0"]
    bsd = "Device:       1024-blocks     Used:\n/dev/ada0p3.eli   2097152         0\n"
    assert sc.swap_listed(bsd) == ["/dev/ada0p3.eli"]
    assert sc.swap_listed("Device:       1024-blocks     Used:\n") == []

    def asked(devices):
        sc._SWAP.clear()
        sc._swap_devices = lambda: devices
        return sc.swap_state()

    assert asked([]) == {"state": "none", "plain": []}
    assert asked(["/dev/ada0p3.eli", "/dev/mirror/swap.eli"]) == {"state": "encrypted", "plain": []}
    assert asked(["/dev/ada0p3.eli", "/dev/ada1p3"]) == {"state": "plain", "plain": ["/dev/ada1p3"]}
    assert asked(None) == {"state": "unknown", "plain": []}
    assert sc._linux_encrypted("/dev/zram0")
    assert not sc._linux_encrypted("/dev/sda2")
    # Plain swap is said on the admin page, once an hour asked again.
    asked(["/dev/ada1p3"])
    assert sc.trouble() == [{"what": "swap", "path": "/dev/ada1p3", "why": "not encrypted"}]
    sc._swap_devices = lambda: []
    assert sc.swap_state()["state"] == "plain", "kept for the hour"
    assert sc.swap_state(now=sc._SWAP["at"] + sc.SWAP_TTL + 1)["state"] == "none"
    src = open(os.path.join(HERE, "donate.py"), encoding="utf-8").read()
    assert "swap = stats_collect.swap_state()" in src and "SWAP NOT ENCRYPTED" in src


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"{len(tests)} passed")
