"""A poster the loader's memory let go is asked for again (#posters):
asked once per page, it stayed blank until the app was started again -
the top of a long list first, as the newest pictures pushed it out."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from collections import OrderedDict  # noqa: E402

from PyQt6.QtGui import QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from dopeiptv.ui.series_cards import ImageSource  # noqa: E402

_app = QApplication.instance() or QApplication([])


class Loader:
    def __init__(self) -> None:
        self.cache: OrderedDict[str, QPixmap] = OrderedDict()
        self.waiting: dict[str, list] = {}
        self.asked: list[str] = []
        self.rest: dict[str, float] = {}

    def cooldown_left(self, url):
        return self.rest.get(url, 0.0)

    def get(self, url, cb):
        self.asked.append(url)
        self.waiting.setdefault(url, []).append(cb)

    def answer(self, url, pm=None):
        pm = pm if pm is not None else QPixmap(4, 4)
        if not pm.isNull():
            self.cache[url] = pm
        for cb in self.waiting.pop(url, []):
            cb(pm)


def test_a_poster_let_go_is_asked_for_again():
    loader, painted = Loader(), []
    src = ImageSource(loader, lambda: painted.append(1))
    assert src.get("u") is None and loader.asked == ["u"]
    assert src.get("u") is None and loader.asked == ["u"]       # on its way: once
    loader.answer("u")
    assert src.get("u") is not None and painted
    loader.cache.pop("u")                                         # the memory let it go
    assert src.get("u") is None
    assert loader.asked == ["u", "u"]                             # asked again
    loader.answer("u")
    assert src.get("u") is not None


def test_a_resting_poster_waits_for_its_rest_then_the_page_paints():
    loader, painted = Loader(), []
    src = ImageSource(loader, lambda: painted.append(1))
    loader.rest["u"] = 0.05
    assert src.get("u") is None and loader.asked == []           # resting: not asked
    assert src._retry is not None and src._retry.isActive()
    loader.rest.clear()
    import time
    deadline = time.monotonic() + 2
    while not painted and time.monotonic() < deadline:
        _app.processEvents()
        time.sleep(0.01)
    assert painted                                                # painted again
    assert src.get("u") is None and loader.asked == ["u"]         # and asked then


def test_a_failed_answer_is_not_asked_in_a_loop():
    loader, painted = Loader(), []
    src = ImageSource(loader, lambda: painted.append(1))
    src.get("u")
    loader.rest["u"] = 20.0                                       # the loader rests it
    loader.answer("u", QPixmap())                                 # nothing came
    for _ in range(5):
        assert src.get("u") is None
    assert loader.asked == ["u"]


def test_tv_mode_uses_the_same_rule():
    from dopeiptv.ui import tv_widgets
    assert issubclass(tv_widgets._ImageSource, ImageSource)


def test_the_loader_says_how_long_a_rest_lasts():
    import time
    from PyQt6.QtCore import QThreadPool
    from dopeiptv.core.workers import LogoLoader
    lo = LogoLoader(QThreadPool(), disk_budget=0)
    assert lo.cooldown_left("https://img.example/a.jpg") == 0.0
    assert lo.cooldown_left("") == 0.0
    lo._mark_dead("https://img.example/a.jpg", 20)
    assert 19 < lo.cooldown_left("https://img.example/a.jpg") <= 20
    lo.dead_hosts["other.example"] = time.monotonic() + 5
    assert 4 < lo.cooldown_left("https://other.example/b.jpg") <= 5
