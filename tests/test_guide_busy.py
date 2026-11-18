""""Loading programme guide…" has one owner and a token per load
(ui/mw_busy.py, MainWindow._load_guide), shown as a job in the notices'
corner (ui/notices.py): every way a load ends - done, from the disk cache,
failed, raised, never answering - ends it; a late word or end of an older
load (a source switched away from, a refresh replaced) neither keeps it nor
ends a newer one's; a list loaded meanwhile does not end it; a load a lookup
started on its own gets an owner. It stayed on screen until a category
change hid it."""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

import dopeiptv.ui.main_window as mw
from dopeiptv.i18n import tr
from dopeiptv.providers.epg import XmltvGuide
from dopeiptv.ui.mw_busy import _BusyMixin
from dopeiptv.ui.notices import NoticeHub

_app = QApplication.instance() or QApplication([])
# A guide with somewhere to load from: one with none is never loaded.
GUIDE_URL = "http://guide.test/xmltv.xml"


class _Window(_BusyMixin, QWidget):
    """The window's notices and guide load, nothing else."""
    _ensure_xmltv_loaded = mw.MainWindow._ensure_xmltv_loaded
    _load_guide = mw.MainWindow._load_guide
    pool = None

    def __init__(self, guide=None):
        super().__init__()
        self.notices = NoticeHub(self)
        self.refreshed = 0
        self.list_model = type("M", (), {})()
        self.list_model.refresh_all = self._refresh
        self.list_model.rowCount = lambda: 1
        self.xmltv = guide if guide is not None else self._guide()

    def _refresh(self):
        self.refreshed += 1

    @staticmethod
    def _guide():
        g = XmltvGuide(client=None, custom_url=GUIDE_URL)
        g.ensure_loaded = lambda force=False: True
        return g

    def _guide_progress(self, token):
        # The signal, delivered at once (on the window's thread it is queued).
        return lambda value: self._on_epg_progress(token, value)

    @property
    def notes(self) -> list[str]:
        # Everything said: passing words on the line and messages alike.
        return self.notices.said()

    def shown(self) -> bool:
        return bool(self.notices.jobs())

    def words(self) -> str:
        jobs = self.notices.jobs()
        return jobs[-1].text if jobs else ""


def _jobs(monkeypatch):
    jobs: list = []
    monkeypatch.setattr(mw, "run_async", lambda _pool, fn, done, err=None:
                        jobs.append((fn, done, err)))
    return jobs


# -- the mixin's own rules ---------------------------------------------------

def test_progress_shows_it_and_the_end_ends_it():
    w = _Window()
    t = w._guide_busy_begin()
    assert not w.shown()                      # nothing said yet
    w._on_epg_progress(t, 10)
    assert w.shown() and w.words() == tr("status_loading_programme_guide")
    assert w._guide_busy_end(t)
    assert not w.shown() and w.words() == "" and w.notes == []


def test_a_load_from_the_disk_cache_never_shows_it():
    w = _Window()
    t = w._guide_busy_begin()
    assert w._guide_busy_end(t, "never said")
    assert not w.shown() and w.notes == []    # nothing showed, nothing said


def test_a_failure_ends_it_with_its_note():
    w = _Window()
    t = w._guide_busy_begin()
    w._on_epg_progress(t, -1)
    w._guide_busy_end(t, "could not")
    assert not w.shown() and w.notes == ["could not"]


def test_an_older_loads_late_word_and_end_change_nothing():
    w = _Window()
    old = w._guide_busy_begin()
    w._on_epg_progress(old, 40)
    new = w._guide_busy_begin()               # a source switch, a refresh
    assert not w.shown()                      # the old one's is not the new one's
    w._on_epg_progress(old, 50)               # its late word: nothing
    assert not w.shown()
    w._on_epg_progress(new, 5)
    assert w.shown()
    assert not w._guide_busy_end(old)         # its end: the new one goes on
    assert w.shown()
    assert w._guide_busy_end(new)
    assert not w.shown()
    w._on_epg_progress(new, 99)               # and after its own end: nothing
    assert not w.shown()


def test_a_list_loaded_meanwhile_does_not_end_it_nor_it_the_list():
    w = _Window()
    t = w._guide_busy_begin()
    w._on_epg_progress(t, 10)
    w._show_busy(tr("status_loading_channels"))
    assert w.words() == tr("status_loading_channels")
    w._hide_busy()                            # the list is in (a category change)
    assert w.shown() and w.words() == tr("status_loading_programme_guide")
    w._show_busy(tr("status_loading_channels"))
    w._guide_busy_end(t)                      # the guide is in: the list still loads
    assert w.shown() and w.words() == tr("status_loading_channels")
    w._hide_busy()
    assert not w.shown()


def test_it_is_named_after_what_the_person_did():
    w = _Window()
    t = w._guide_busy_begin(tr("status_refreshing_playlist"))
    w._on_epg_progress(t, 1)
    assert w.words() == tr("status_refreshing_playlist")
    w._guide_busy_end(t)
    t = w._guide_busy_begin()                 # the next load: its own words
    w._on_epg_progress(t, 1)
    assert w.words() == tr("status_loading_programme_guide")


def test_a_load_that_never_answers_is_let_go_with_a_calm_note():
    w = _Window()
    w.GUIDE_QUIET_MS = 30
    t = w._guide_busy_begin()
    w._on_epg_progress(t, 100)                # downloaded; the parse says nothing
    deadline = time.monotonic() + 3
    while w.shown() and time.monotonic() < deadline:
        _app.processEvents()
        time.sleep(0.005)
    assert not w.shown()
    assert w.notes == [tr("status_guide_still_loading")]
    w._on_epg_progress(t, 100)                # it ended: its word is old now
    assert not w.shown()
    assert not w._guide_busy_end(t, "late")   # and so is its end
    assert w.notes == [tr("status_guide_still_loading")]


def test_progress_keeps_a_slow_load_alive():
    w = _Window()
    w.GUIDE_QUIET_MS = 200
    t = w._guide_busy_begin()
    end = time.monotonic() + 0.5
    while time.monotonic() < end:             # reports every 50 ms: never quiet
        w._on_epg_progress(t, -1)
        _app.processEvents()
        time.sleep(0.05)
    assert w.shown() and w.notes == []
    w._guide_busy_end(t)
    assert not w.shown()


# -- the window's load -------------------------------------------------------

def test_each_way_a_load_ends_ends_the_strip(monkeypatch):
    jobs = _jobs(monkeypatch)
    for answer, note in ((True, []), (False, [tr("detail_epg_failed")])):
        w = _Window()
        w._load_guide()
        fn, done, _err = jobs.pop()
        w.xmltv.progress_cb(30)               # the download reports
        assert w.shown()
        w.xmltv.ensure_loaded = lambda force=False, a=answer: a
        done(fn())
        assert not w.shown(), answer
        assert w.notes == note
        assert w._guide_loading is None
        assert w.refreshed == (1 if answer else 0)
    w = _Window()                             # the load raised
    w._load_guide()
    fn, done, err = jobs.pop()
    w.xmltv.progress_cb(30)
    err("download exploded")
    assert not w.shown() and w._guide_loading is None
    assert w.notes == [tr("detail_epg_failed")]


def test_a_source_switch_mid_load(monkeypatch):
    jobs = _jobs(monkeypatch)
    w = _Window()
    first = w.xmltv
    w._load_guide()
    fn1, done1, _ = jobs.pop()
    first.progress_cb(20)
    assert w.shown()
    second = w.xmltv = _Window._guide()       # switch_playlist -> refresh_playlist
    w._load_guide(label=tr("status_refreshing_playlist"))
    fn2, done2, _ = jobs.pop()
    first.progress_cb(60)                     # the old source's download goes on
    assert not w.shown()
    second.progress_cb(5)
    assert w.shown() and w.words() == tr("status_refreshing_playlist")
    done1(fn1())                              # the old one ends: not the new one's
    assert w.shown() and w._guide_loading is second
    assert w.refreshed == 0                   # nor its list
    done2(fn2())
    assert not w.shown() and w._guide_loading is None and w.refreshed == 1


def test_a_second_load_of_the_same_guide_keeps_it_loading(monkeypatch):
    jobs = _jobs(monkeypatch)
    w = _Window()
    w._load_guide()
    fn1, done1, _ = jobs.pop()
    w._load_guide(force=True)                 # Refresh guide now meanwhile
    fn2, done2, _ = jobs.pop()
    w.xmltv.progress_cb(10)
    done1(fn1())
    assert w.shown() and w._guide_loading is w.xmltv
    done2(fn2())
    assert not w.shown() and w._guide_loading is None


def test_a_lookups_own_load_is_owned_by_the_window(monkeypatch):
    """A lookup on a worker (the detail panel, a recording's past
    programmes) found no guide and loads it itself: its first word makes the
    window own a load beside it, whose end ends the strip."""
    jobs = _jobs(monkeypatch)
    w = _Window(guide=XmltvGuide(client=None, custom_url=GUIDE_URL))
    g = w.xmltv
    g.PARSE_ELSEWHERE = False
    g.progress_cb = w._guide_progress(0)      # as the window makes every guide
    seen = []

    def download():
        for pct in (10, 50, 100):
            g.progress_cb(pct)
            seen.append(w.shown())
        return b'<tv><channel id="c"><display-name>C</display-name></channel></tv>'

    g._download = download
    g.listings_for({"epg_channel_id": "c"})   # the lookup's load
    assert seen == [False, True, True]        # owned from its first word on
    assert len(jobs) == 1 and w._guide_loading is g
    fn, done, _ = jobs.pop()
    done(fn())                                # loaded already: it ends at once
    assert not w.shown() and w._guide_loading is None


def test_tv_mode_never_loads_the_guide_on_the_windows_thread():
    from dopeiptv.ui.tv_mode import now_next

    class _Guide:
        loaded = False

        def is_loaded(self):
            return self.loaded

        def listings_for(self, it, limit=8):
            assert self.loaded, "a load on the window's thread"
            now = time.time()
            return [{"title": "News", "start_timestamp": now - 60,
                     "stop_timestamp": now + 60}]

    class _W:
        asked = 0

        def __init__(self):
            self.xmltv = _Guide()

        def _ensure_xmltv_loaded(self):
            self.asked += 1

    w = _W()
    assert now_next(w, {"name": "C"}) == ("", "", None)
    assert w.asked == 1                       # the window loads it, off this thread
    w.xmltv.loaded = True
    on, _next, frac = now_next(w, {"name": "C"})
    assert "News" in on and frac is not None and w.asked == 1


def test_no_provider_no_guide_load_and_no_failure():
    # The first start (no provider): nothing to load the guide from, so no
    # "Loading programme guide", no job and no "could not load".
    from dopeiptv.providers.client import OfflineClient
    jobs = []
    w = _Window(guide=XmltvGuide(OfflineClient()))
    mw_run = mw.run_async
    try:
        mw.run_async = lambda _pool, fn, done, err=None: jobs.append(fn)
        w._load_guide()
    finally:
        mw.run_async = mw_run
    assert jobs == [] and not w.shown() and w.notes == []
