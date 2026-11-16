"""Server mode keeps the guides it hands out fresh: a served source's guide
older than six hours is downloaded again, one try an hour, never the one
the window keeps itself, and an answer that is no guide leaves the copy
there is (services/guide_keeper.py, XmltvGuide.refresh_file)."""
from __future__ import annotations

import gzip
import os
import threading
import time

from dopeiptv.providers.epg import XmltvGuide
from dopeiptv.services.guide_keeper import GuideKeeper

GUIDE = (b'<?xml version="1.0" encoding="UTF-8"?>\n<tv><channel id="a"><display-name>A'
         b'</display-name></channel></tv>\n')


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _keeper(tmp_path, sources, kept=(), clock=None):
    return GuideKeeper(lambda: sources, lambda pl: object(), kept=lambda: set(kept),
                       guide_path=lambda pid: str(tmp_path / f"epg_{pid}.xml"),
                       max_age=6 * 3600.0, clock=clock or Clock())


def _age(path, seconds: float) -> None:
    path.write_bytes(gzip.compress(GUIDE))
    t = time.time() - seconds
    os.utime(path, (t, t))


def test_a_missing_or_old_guide_is_due_a_fresh_one_is_not(tmp_path):
    k = _keeper(tmp_path, [])
    assert k.due("s1")                                     # none on disk
    _age(tmp_path / "epg_s1.xml", 600)
    assert not k.due("s1")
    _age(tmp_path / "epg_s1.xml", 7 * 3600)
    assert k.due("s1")


def test_one_try_an_hour_per_source(tmp_path, monkeypatch):
    clock = Clock()
    calls = []

    def down(self):
        calls.append(self.cache_path.name)
        raise RuntimeError("no answer")
    monkeypatch.setattr(XmltvGuide, "_download", down)
    k = _keeper(tmp_path, [{"id": "s1", "name": "A"}], clock=clock)
    assert k.check() == 0 and calls == ["epg_s1.xml"]
    clock.t += 1800
    assert k.check() == 0 and len(calls) == 1              # within the hour
    clock.t += 1900
    k.check()
    assert len(calls) == 2


def test_the_window_s_own_source_and_local_files_are_left_alone(tmp_path, monkeypatch):
    got = []

    def down(self):
        got.append(self.cache_path.name)
        return GUIDE
    monkeypatch.setattr(XmltvGuide, "_download", down)
    sources = [{"id": "active"}, {"id": "other"}, {"id": "files", "kind": "local"}]
    k = _keeper(tmp_path, sources, kept={"active"})
    assert k.check() == 1
    assert got == ["epg_other.xml"]
    kept = (tmp_path / "epg_other.xml").read_bytes()
    assert kept[:2] == b"\x1f\x8b" and gzip.decompress(kept) == GUIDE


def test_an_answer_that_is_no_guide_keeps_the_copy_there_is(tmp_path, monkeypatch):
    path = tmp_path / "epg_s1.xml"
    _age(path, 7 * 3600)
    before = path.read_bytes()
    monkeypatch.setattr(XmltvGuide, "_download",
                        lambda self: b"<html><body>Account expired</body></html>")
    k = _keeper(tmp_path, [{"id": "s1"}])
    assert k.check() == 0
    assert path.read_bytes() == before


def test_a_gzipped_answer_is_kept_as_it_came(tmp_path, monkeypatch):
    packed = gzip.compress(GUIDE)
    monkeypatch.setattr(XmltvGuide, "_download", lambda self: packed)
    k = _keeper(tmp_path, [{"id": "s1"}])
    assert k.check() == 1
    assert (tmp_path / "epg_s1.xml").read_bytes() == packed


def test_the_source_s_own_guide_address_is_used(tmp_path, monkeypatch):
    seen = []

    def down(self):
        seen.append(self.custom_url)
        return GUIDE
    monkeypatch.setattr(XmltvGuide, "_download", down)
    k = _keeper(tmp_path, [{"id": "s1", "epg_url": "http://guide.example/x.xml"}])
    k.check()
    assert seen == ["http://guide.example/x.xml"]


def test_stopping_stops_the_looking(tmp_path, monkeypatch):
    monkeypatch.setattr(GuideKeeper, "FIRST_S", 0.0)
    monkeypatch.setattr(GuideKeeper, "EVERY_S", 0.05)
    looked = threading.Event()
    k = _keeper(tmp_path, [])
    monkeypatch.setattr(k, "check", lambda stop=None: looked.set() or 0)
    k.start()
    assert looked.wait(2.0)
    thread = k._thread
    k.stop()
    assert thread is not None
    thread.join(2.0)
    assert not thread.is_alive()


def test_the_server_keeps_its_guides_and_the_window_its_own():
    from dopeiptv.services.home_server import HomeServer
    from dopeiptv.ui.mw_server import _WindowHost

    class Host:
        def sources(self):
            return []

        def pool(self):
            return None

        def app_session(self, pid, sid):
            return None

    host = _WindowHost(win=None, bridge=None)
    assert host.guides_kept() == set()
    host.active = "abc"
    assert host.guides_kept() == {"abc"}
    from dopeiptv.services.home_server import Config
    srv = HomeServer(Host(), Config())
    assert isinstance(srv.guides, GuideKeeper)
