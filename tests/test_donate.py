"""Donating from the app: when Home's card shows, what its buttons do, and
the watching that is counted."""
from __future__ import annotations

import os
import time

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from dopeiptv.ui import mw_donate  # noqa: E402
from dopeiptv.ui.mw_donate import (  # noqa: E402
    CARD_AFTER_MIN, SNOOZE_SECS, THANKS_SECS, _DonateMixin,
)

app = QApplication.instance() or QApplication([])


class _Wake:
    def __init__(self) -> None:
        self.held = False


class _Win(QObject, _DonateMixin):
    def __init__(self, tmp_path) -> None:
        super().__init__()
        self.settings = QSettings(str(tmp_path / "s.ini"),
                                  QSettings.Format.IniFormat)
        self.wake = _Wake()
        self._init_donate()


def test_no_card_before_real_use(tmp_path):
    w = _Win(tmp_path)
    assert w._donate_card() is None
    w.settings.setValue("donate_watch_min", CARD_AFTER_MIN - 1)
    assert w._donate_card() is None
    w.settings.setValue("donate_watch_min", CARD_AFTER_MIN)
    assert w._donate_card() is not None


def test_only_watching_counts(tmp_path):
    w = _Win(tmp_path)
    for _ in range(12):
        w._donate_count()                 # nothing playing
    assert w._donate_watched() == 0
    w.wake.held = True
    for _ in range(12):
        w._donate_count()
    # written five minutes at a time; the rest waits in memory
    assert w._donate_watched() == 10 and w._donate_unsaved == 2


def test_not_now_puts_it_away_for_three_months(tmp_path):
    w = _Win(tmp_path)
    w.settings.setValue("donate_watch_min", CARD_AFTER_MIN)
    card = w._donate_card()
    before = time.time()
    card.later.emit()
    until = float(w.settings.value("donate_card_until"))
    assert before + SNOOZE_SECS - 5 <= until <= time.time() + SNOOZE_SECS
    assert w._donate_card() is None
    w.settings.setValue("donate_card_until", time.time() - 1)
    assert w._donate_card() is not None


def test_donate_opens_the_site_and_waits_a_year(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(mw_donate, "open_url", opened.append)
    w = _Win(tmp_path)
    w.settings.setValue("donate_watch_min", CARD_AFTER_MIN)
    w._donate_card().donate.emit()
    assert opened == ["https://iptv.dope.rs/?ref=app-home#donate"]
    until = float(w.settings.value("donate_card_until"))
    assert until >= time.time() + THANKS_SECS - 5


def test_the_heart_button_says_where_it_comes_from(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(mw_donate, "open_url", opened.append)
    _Win(tmp_path).open_donate("app-heart")
    assert opened == ["https://iptv.dope.rs/?ref=app-heart#donate"]


def test_the_heart_hides_for_three_months_and_comes_back(tmp_path):
    from PyQt6.QtWidgets import QPushButton
    w = _Win(tmp_path)
    w._donate_btn = QPushButton()
    w._donate_btn.show()
    assert w._donate_heart_due()
    before = time.time()
    w.hide_donate_heart()
    until = float(w.settings.value("donate_heart_until"))
    assert before + SNOOZE_SECS - 5 <= until <= time.time() + SNOOZE_SECS
    assert not w._donate_heart_due() and not w._donate_btn.isVisible()
    # three months on, the minute's tick puts it back by itself
    w.settings.setValue("donate_heart_until", time.time() - 1)
    w._donate_count()
    assert w._donate_heart_due() and w._donate_btn.isVisible()


def test_left_alone_the_card_goes_after_a_week(tmp_path):
    w = _Win(tmp_path)
    w.settings.setValue("donate_watch_min", CARD_AFTER_MIN)
    assert w._donate_card() is not None            # first shown: noted
    shown = float(w.settings.value("donate_card_shown"))
    assert shown <= time.time()
    assert w._donate_card() is not None            # still within its week
    w.settings.setValue("donate_card_shown", time.time() - mw_donate.SHOWN_SECS - 1)
    assert w._donate_card() is None                # a week on: gone by itself
    until = float(w.settings.value("donate_card_until"))
    assert until >= time.time() + SNOOZE_SECS - 5   # for three months
    assert w.settings.value("donate_card_shown") is None


def test_said_donated_the_heart_and_card_go_for_good(tmp_path):
    from PyQt6.QtWidgets import QPushButton
    w = _Win(tmp_path)
    w._donate_btn = QPushButton()
    w._donate_btn.show()
    w.settings.setValue("donate_watch_min", CARD_AFTER_MIN)
    w.donated()
    assert not w._donate_btn.isVisible() and w._donate_card() is None
    w.settings.setValue("donate_heart_until", 0)      # no time brings it back
    w._donate_count()
    assert not w._donate_btn.isVisible()
    w.show_donate_heart()                             # only the switch does
    assert w._donate_btn.isVisible()
