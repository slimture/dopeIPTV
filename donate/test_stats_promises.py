#!/usr/bin/env python3
"""The statistics' promises kept by the code, not by habit (stats_collect.py,
admin.py, donate.py):

- a day's visits are kept 7 days at the most, whatever STATS_VISITS_DAYS
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


def test_visits_are_never_kept_past_seven_days():
    sc, _dir = _module("STATS_VISITS_DAYS=60\n")
    assert sc.VISITS_DAYS_MAX == 7 and sc.VISITS_DAYS == 7
    sc, _dir = _module("STATS_VISITS_DAYS=14\n")
    assert sc.VISITS_DAYS == 7, "the two weeks of before are cut to it"
    sc, _dir = _module("STATS_VISITS_DAYS=3\n")
    assert sc.VISITS_DAYS == 3, "shorter is allowed"
    sc, _dir = _module("STATS_VISITS_DAYS=0\n")
    assert sc.VISITS_DAYS == 1
    sc, _dir = _module("STATS_VISITS_DAYS=forever\n")
    assert sc.VISITS_DAYS == 7
    sc, _dir = _module()
    assert sc.VISITS_DAYS == 7, "without the setting"
    # The admin page and the start line take the cut value, not the file's.
    src = open(os.path.join(HERE, "donate.py"), encoding="utf-8").read()
    assert 'body["visits_days"] = int(getattr(stats_collect, "VISITS_DAYS", 7))' in src
    assert 'CONF.get("STATS_VISITS_DAYS", "7") or 7' not in src
    assert '"14"' not in src.split("def start_statistics", 1)[-1].split("\ndef ", 1)[0]
    # The admin page says how long single visits are kept from what it is
    # told, and its own word before that is the same.
    js = open(os.path.join(HERE, "admin", "admin.js"), encoding="utf-8").read()
    assert "data.visits_days) || 7" in js and "data.visits_days) || 14" not in js
    page = open(os.path.join(HERE, "admin", "index.html"), encoding="utf-8").read()
    assert '<span id="kept">7 days</span>' in page
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


def test_a_log_of_before_with_addresses_is_said_until_it_is_gone():
    """The logs nginx wrote before the socket held every visitor's address:
    while one of them, or a copy newsyslog made of it, is on the disk the
    log and the admin page say so - louder while nginx still writes it."""
    import time
    sc, _dir = _module()
    sc._SWAP.clear()
    sc._SWAP.update(at=1e18, answer={"state": "none", "plain": []})
    assert sc.address_logs() == [] and sc.trouble() == []
    old_copy = sc.STATS_LOG + ".0"
    for path in (sc.STATS_LOG, old_copy, sc.DL_LOG + ".1.gz"):
        with open(path, "w") as fh:
            fh.write('{"ip": "203.0.113.7"}\n')
    long_ago = time.time() - 2 * sc.STILL_WRITTEN
    os.utime(old_copy, (long_ago, long_ago))
    os.utime(sc.DL_LOG + ".1.gz", (long_ago, long_ago))
    got = {t["path"]: t["why"] for t in sc.address_logs()}
    assert got == {sc.STATS_LOG: "written lately", old_copy: "holds addresses",
                   sc.DL_LOG + ".1.gz": "holds addresses"}
    assert all(t["what"] == "log" for t in sc.trouble()) and len(sc.trouble()) == 3
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        sc._say_address_logs()
        sc._say_address_logs()
    assert said.getvalue().count("holds visitors' addresses") == 3, "once for each"
    assert "203.0.113.7" not in said.getvalue()
    # Said at the top of the admin page, and asked after every run.
    js = open(os.path.join(HERE, "admin", "admin.js"), encoding="utf-8").read()
    assert 't.what === "log"' in js and "still writes visitors' addresses" in js
    src = open(os.path.join(HERE, "stats_collect.py"), encoding="utf-8").read()
    assert src.index("sweep_left(today)") < src.index("_say_address_logs()") < src.index("_save(stats_path")
    # Gone: nothing said any more.
    for path in (sc.STATS_LOG, old_copy, sc.DL_LOG + ".1.gz"):
        os.unlink(path)
    assert sc.address_logs() == [] and sc.trouble() == []


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
    # No swap for the tests after this one: plain swap now stops a run.
    sc._SWAP.clear()
    sc._SWAP.update(at=1e18, answer={"state": "none", "plain": []})


def test_seven_days_means_seven_dates():
    # Today and the 6 days before it are kept; the 7th day back is gone at
    # the first run that day - a visit is never kept past 7 days.
    sc, _dir = _module()
    os.makedirs(sc.VISITS_DIR)
    today = date.today()
    kept = (today - timedelta(days=sc.VISITS_DAYS - 1)).isoformat()
    gone = (today - timedelta(days=sc.VISITS_DAYS)).isoformat()
    for day in (kept, gone):
        with open(os.path.join(sc.VISITS_DIR, day + ".json"), "w") as fh:
            json.dump([], fh)
    assert sc.left_behind() == [os.path.join(sc.VISITS_DIR, gone + ".json")]
    data = {"days": {kept: {"views": 1}, gone: {"views": 1}}}
    sc._forget_old(data)
    assert os.path.exists(os.path.join(sc.VISITS_DIR, kept + ".json"))
    assert not os.path.exists(os.path.join(sc.VISITS_DIR, gone + ".json"))
    assert data["days"][gone].get("folded") == 1 and "folded" not in data["days"][kept]
    os.makedirs(sc.UPDATES_DIR)
    old_update = (today - timedelta(days=sc.UPDATES_DAYS)).isoformat()
    with open(os.path.join(sc.UPDATES_DIR, old_update + ".json"), "w") as fh:
        json.dump([], fh)
    assert os.path.join(sc.UPDATES_DIR, old_update + ".json") in sc.left_behind()


def test_zram_counts_only_without_a_disk_behind_it(tmp_path):
    sc, _dir = _module()
    blocks = tmp_path / "class" / "block"
    for name, files in {"zram0": {"backing_dev": "none"}, "zram1": {"backing_dev": "/dev/sdb2"},
                        "dm-0": {"dm/uuid": "CRYPT-LUKS2-x"}, "dm-1": {"dm/uuid": "LVM-y"},
                        "sda3": {}}.items():
        for rel, text in files.items():
            f = blocks / name / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text + "\n")
        (blocks / name).mkdir(parents=True, exist_ok=True)
    (blocks / "dm-1" / "slaves" / "dm-0").mkdir(parents=True)
    (blocks / "dm-2" / "slaves" / "sda3").mkdir(parents=True)
    root = str(tmp_path)
    assert sc._linux_encrypted("/dev/zram0", root)
    assert not sc._linux_encrypted("/dev/zram1", root), "writes back to a disk"
    assert sc._linux_encrypted("/dev/dm-0", root)
    assert sc._linux_encrypted("/dev/dm-1", root), "LVM on LUKS"
    assert not sc._linux_encrypted("/dev/dm-2", root), "LVM on a plain partition"
    assert not sc._linux_encrypted("/dev/sda3", root)
    # A kernel without zram writeback has no backing_dev: safe. One there
    # that cannot be read: not counted as safe.
    (blocks / "zram5").mkdir()
    assert sc._linux_encrypted("/dev/zram5", root)
    (blocks / "zram6" / "backing_dev").mkdir(parents=True)     # cannot be read as a file
    assert not sc._linux_encrypted("/dev/zram6", root)


def test_swap_that_cannot_be_asked_is_said_too():
    sc, _dir = _module()
    sc._SWAP.clear()
    sc._swap_devices = lambda: None
    assert sc.trouble() == [{"what": "swap", "path": "", "why": "could not be asked"}]
    js = open(os.path.join(HERE, "admin", "admin.js"), encoding="utf-8").read()
    assert 'swap.why === "could not be asked"' in js


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"{len(tests)} passed")


def test_swap_that_keeps_what_it_is_given_stops_the_count():
    """A hard stop, not a warning: while swap keeps what it is given, the
    run makes or reads no month key and looks at no line - each holds an
    address; the lines waiting are dropped, uncounted, and said."""
    import stats_collect as sc
    sc._SWAP.clear()
    sc._SWAP.update(at=1e18, answer={"state": "plain", "plain": ["/dev/ada0p3"]})
    sc.INBOX.put("iptv_stats", '{"ip": "203.0.113.9", "p": "/"}')
    made = []
    real = sc.keys_now
    sc.keys_now = lambda *a, **k: made.append(1) or real(*a, **k)
    try:
        before = sc.SWAP_HELD["lines"]
        sc.collect()
        assert made == [], "no month key made or read"
        assert len(sc.INBOX) == 0 and sc.SWAP_HELD["lines"] == before + 1
    finally:
        sc.keys_now = real
        sc._SWAP.clear()
        sc._SWAP.update(at=1e18, answer={"state": "none", "plain": []})
