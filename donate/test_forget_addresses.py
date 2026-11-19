#!/usr/bin/env python3
"""The service's request limits (donate.py _allowed) hold an address in
memory only as long as its limit needs it: ten minutes, a day for the daily
ones, and a minute more at the most - not until thousands of others came.

Plain Python: ``python3 donate/test_forget_addresses.py`` runs them all
(pytest finds them too). Everything in a temporary directory.
"""

from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _donate():
    root = tempfile.mkdtemp()
    stats = os.path.join(root, "stats")
    os.makedirs(stats)
    conf = os.path.join(root, "donate.conf")
    with open(conf, "w") as fh:
        fh.write(f"STATS_DIR={stats}\nLISTEN=127.0.0.1:0\n")
    os.environ["DONATE_CONFIG"] = conf
    os.environ["STATS_DIR"] = stats
    sys.modules.pop("donate", None)
    import donate
    donate._SEEN.clear()
    donate._SPAN.clear()
    return donate


def test_an_address_goes_once_its_window_has_passed():
    d = _donate()
    assert d._allowed("sync:198.51.100.7", 5)
    assert d._allowed("sync-new:198.51.100.7", 5, d.DAY)
    now = d.time.monotonic()
    with d._LOCK:
        assert d._forget_quiet(now) == 0               # both still needed
        assert d._forget_quiet(now + d.WINDOW + 1) == 1  # the ten-minute one went
        assert "sync:198.51.100.7" not in d._SEEN and "sync-new:198.51.100.7" in d._SEEN
        assert d._forget_quiet(now + d.DAY + 1) == 1     # the daily one a day later
        assert not d._SEEN and not d._SPAN


def test_a_request_sweeps_when_a_minute_has_gone():
    d = _donate()
    d._SEEN["tg:203.0.113.9"] = [d.time.monotonic() - d.WINDOW - 5]
    d._SWEPT[0] = d.time.monotonic() - d.FORGET_EVERY - 1
    assert d._allowed("tg:192.0.2.1", 10)
    assert "tg:203.0.113.9" not in d._SEEN and "tg:192.0.2.1" in d._SEEN


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"{len(tests)} passed")
