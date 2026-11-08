"""A portal's play address is fetched off the window's thread: the window
learns which address the play needs, fetches it in the background and
plays once it is in - and only the newest ask goes on."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")

from dopeiptv.ui import main_window  # noqa: E402
from dopeiptv.ui.main_window import MainWindow  # noqa: E402


class Portal:
    slow_links = True

    def __init__(self) -> None:
        self.links: dict = {}
        self.asked: list = []

    def link_ready(self, method, ident):
        return (method, ident) in self.links

    def live_url(self, sid, fmt="ts"):
        self.asked.append(("live_url", sid))
        self.links[("live_url", sid)] = f"http://cdn.invalid/{sid}"
        return self.links[("live_url", sid)]


def _host(client, pending):
    h = SimpleNamespace(client=client, pool=None, _set_status=lambda *_: None)
    h._links_later = lambda plan, again: MainWindow._links_later(h, plan, again)
    return h


def test_the_address_comes_off_the_window_thread_then_plays(monkeypatch):
    pending: list = []
    monkeypatch.setattr(main_window, "run_async",
                        lambda pool, work, done, fail: pending.append((work, done)))
    portal = Portal()
    h = _host(portal, pending)
    played = []

    def play():
        if h._links_later(lambda: h.client.live_url(7), play):
            return
        played.append(h.client.live_url(7))

    play()
    assert played == [] and portal.asked == [], "nothing asked on the window"
    assert h.client is portal, "the stand-in is gone again"
    work, done = pending.pop()
    done(work())                       # the background fetch, then back
    assert played == ["http://cdn.invalid/7"]
    assert portal.asked == [("live_url", 7)] * 2, "the second is a cache hit"


def test_only_the_newest_ask_plays(monkeypatch):
    pending: list = []
    monkeypatch.setattr(main_window, "run_async",
                        lambda pool, work, done, fail: pending.append((work, done)))
    h = _host(Portal(), pending)
    played = []

    def play(sid):
        if h._links_later(lambda: h.client.live_url(sid), lambda: play(sid)):
            return
        played.append(sid)

    play(1)
    play(2)                            # zapped on before 1 came
    (w1, d1), (w2, d2) = pending
    d1(w1())
    assert played == [], "the first is no longer wanted"
    d2(w2())
    assert played == [2]


def test_an_xtream_client_plays_at_once():
    h = _host(SimpleNamespace(live_url=lambda sid, fmt="ts": "x"), [])
    assert MainWindow._links_later(h, lambda: h.client.live_url(1),
                                   lambda: None) is False
