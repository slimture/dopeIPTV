"""A guide that is old - or running out - is fetched again, an app left
open included; one that cannot be bettered is not fetched over and over."""
from __future__ import annotations

import time
from array import array

from dopeiptv.providers.epg import XmltvGuide


def _guide(ends_in: float, age: float | None, tmp_path) -> XmltvGuide:
    g = XmltvGuide(client=None, cache_path=str(tmp_path / "g.xml.gz"))
    now = time.time()
    g._by_id = {f"c{i}": (array("q", [int(now - 3600)]),
                          array("q", [int(now + ends_in + i)]), ("x",), ("",))
                for i in range(5)}
    g._note_coverage()
    if age is not None:
        g.cache_path.write_bytes(b"x")
        t = now - age
        import os
        os.utime(g.cache_path, (t, t))
    return g


def test_a_fresh_guide_with_a_day_ahead_is_left_alone(tmp_path):
    assert not _guide(24 * 3600, age=600, tmp_path=tmp_path).stale()


def test_an_old_download_is_due(tmp_path):
    assert _guide(24 * 3600, age=7 * 3600, tmp_path=tmp_path).stale()


def test_a_guide_running_out_is_due_however_new(tmp_path):
    g = _guide(3600, age=600, tmp_path=tmp_path)
    assert g.runs_out_soon() and g.stale()
    # Yesterday's guide, kept by an app left open overnight.
    assert _guide(-10 * 3600, age=600, tmp_path=tmp_path).stale()


def test_one_try_an_hour_for_a_guide_that_always_runs_out(tmp_path):
    g = _guide(3600, age=600, tmp_path=tmp_path)
    g._last_try = time.monotonic()
    assert not g.stale()
    g._last_try = time.monotonic() - 2 * XmltvGuide.REFRESH_RETRY_S
    assert g.stale()


def test_a_running_out_cache_is_downloaded_again_at_start(tmp_path, monkeypatch):
    g = _guide(1800, age=600, tmp_path=tmp_path)
    loaded = {"n": 0}

    def from_disk():
        g._loaded = True
        g._note_coverage()
        return True

    monkeypatch.setattr(g, "_load_from_disk", from_disk)
    monkeypatch.setattr(g, "_download", lambda: loaded.__setitem__("n", 1) or b"<tv/>")
    monkeypatch.setattr(g, "_parse_and_index", lambda data, keep=False: None)
    g._loaded = False
    assert g.ensure_loaded()
    assert loaded["n"] == 1
