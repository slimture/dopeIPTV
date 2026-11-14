"""A portal's archive link that comes back late does not take over.

The link is asked for off the window's thread; meanwhile the user may have
pressed Stop or played another channel - by the auto-preview too, which
never reaches _start_playback. The answer is checked against the
generation every start and every Stop moves (mw_failover's)."""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from dopeiptv.ui import mw_portal_archive  # noqa: E402
from dopeiptv.ui.mw_failover import _FailoverMixin  # noqa: E402
from dopeiptv.ui.mw_portal_archive import _PortalArchiveMixin  # noqa: E402

GOT = {"url": "http://portal.example/archive/1.ts", "start": 1000.0, "stop": 4600.0,
       "offset": 60.0, "title": "Prog", "id": "1"}


class Portal:
    archive_by_programme = True

    def archive_link(self, sid, start, stop=None):
        return GOT


class Window(_PortalArchiveMixin, _FailoverMixin):
    def __init__(self):
        self.client = Portal()
        self.pool = None
        self.player = None
        self.started: list = []
        self.flashed: list = []

    def _flash_status(self, text, *a, **k):
        self.flashed.append(text)

    def channel_display_name(self, it):
        return it.get("name") or "?"

    def _item_key(self, it):
        return it.get("stream_id")

    def _start_playback(self, url, *a, **k):
        self._failover_leave(url, None)
        self.started.append(url)


@pytest.fixture
def asked(monkeypatch):
    calls: list = []
    monkeypatch.setattr(mw_portal_archive, "run_async",
                        lambda pool, fn, done, fail: calls.append((fn, done, fail)))
    return calls


def _ask(w, asked):
    w._play_portal_archive({"stream_id": 5, "name": "Five"}, 1060.0)
    fn, done, _fail = asked[-1]
    return fn, done


def test_the_link_plays_when_nothing_happened_meanwhile(asked):
    w = Window()
    fn, done = _ask(w, asked)
    done(fn())
    assert w.started == [GOT["url"]]


def test_not_after_stop(asked):
    w = Window()
    fn, done = _ask(w, asked)
    w._failover_stopped()               # _on_player_stopped
    done(fn())
    assert w.started == []


def test_not_after_another_channel_by_the_preview(asked):
    w = Window()
    fn, done = _ask(w, asked)
    # The auto-preview: _failover_leave, never _start_playback (_diag_gen
    # does not move there).
    w._failover_leave("http://portal.example/live/9.ts", 9)
    done(fn())
    assert w.started == []
    # Nor does its failure come back with a note over what plays now.
    fn, done = _ask(w, asked)
    w._failover_leave("http://portal.example/live/9.ts", 9)
    asked[-1][2](RuntimeError("down"))
    assert w.flashed.count(mw_portal_archive.tr("ts_check_failed")) == 0
