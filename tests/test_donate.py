"""Donating from the app: the rhythm it asks in (core/donate_rhythm.py) -
one week a month once the app is used for real, its colour the first day
only, the answers that put it away - and the window keeping the sidebar's
heart to it (ui/mw_donate.py; Home and its card are gone), with a clock of
the test's own."""
from __future__ import annotations

import os
from datetime import datetime

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from dopeiptv.core import donate_rhythm as rhythm  # noqa: E402
from dopeiptv.core import privacy, profiles, supporter, viewing_stats  # noqa: E402
from dopeiptv.core.donate_rhythm import BRIGHT, DAY, MUTED, OFF  # noqa: E402
from dopeiptv.ui import mw_donate, theme  # noqa: E402
from dopeiptv.ui.mw_donate import _DonateMixin  # noqa: E402
from dopeiptv.ui.widgets import HeartButton  # noqa: E402

app = QApplication.instance() or QApplication([])
HOUR = 3600


def at(y: int, m: int, d: int, h: int = 12) -> float:
    """A local time, as the rhythm's months are."""
    return datetime(y, m, d, h).timestamp()


def lots() -> float:
    return 6 * HOUR


class S(dict):
    """Settings as the rhythm sees them."""

    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's name
        self[key] = value

    def remove(self, key):
        self.pop(key, None)


@pytest.fixture(autouse=True)
def _clean():
    privacy.set_private(False)
    profiles.activate("")
    filled = not theme.P
    if filled:
        theme.P.update(theme.THEMES["graphite"])
    yield
    privacy.set_private(False)
    profiles.activate("")
    if filled:
        theme.P.clear()


# -- the rhythm itself ------------------------------------------------------------

def test_a_new_user_is_first_asked_after_two_weeks_of_use():
    s = S()
    t0 = at(2026, 3, 10)
    rhythm.migrate(s, t0)
    assert s[rhythm.K_FIRST] == t0
    assert rhythm.step(s, t0, lots) == OFF
    assert rhythm.step(s, t0 + 13 * DAY, lots) == OFF
    assert rhythm.K_PERIOD not in s
    t = t0 + 14 * DAY
    assert rhythm.step(s, t, lots) == BRIGHT
    assert s[rhythm.K_PERIOD] == t


def test_only_five_hours_in_thirty_days_is_real_use():
    s = S()
    rhythm.migrate(s, at(2026, 1, 1))
    t = at(2026, 3, 3)
    assert rhythm.step(s, t, lambda: 5 * HOUR - 1) == OFF
    assert rhythm.K_PERIOD not in s
    assert rhythm.step(s, t + HOUR, lambda: 5 * HOUR) == BRIGHT


def test_a_period_is_a_week_and_the_next_one_comes_next_month():
    s = S()
    rhythm.migrate(s, at(2026, 1, 1))
    start = at(2026, 3, 2, 9)
    assert rhythm.step(s, start, lots) == BRIGHT
    assert rhythm.step(s, start + DAY - 1, lots) == BRIGHT
    assert rhythm.step(s, start + DAY, lots) == MUTED          # colour the first day only
    assert rhythm.step(s, start + 7 * DAY - 1, lots) == MUTED
    # Left alone: gone by itself after the week, not again this month.
    assert rhythm.step(s, start + 7 * DAY, lots) == OFF
    assert rhythm.step(s, at(2026, 3, 30), lots) == OFF
    assert s[rhythm.K_PERIOD] == start
    # The first start in April begins April's.
    april = at(2026, 4, 1, 8)
    assert rhythm.step(s, april, lots) == BRIGHT
    assert s[rhythm.K_PERIOD] == april


def test_a_period_begun_late_in_a_month_does_not_run_into_the_next():
    s = S()
    rhythm.migrate(s, at(2026, 1, 1))
    start = at(2026, 1, 28)
    assert rhythm.step(s, start, lots) == BRIGHT
    assert rhythm.step(s, at(2026, 2, 3), lots) == MUTED       # January's, still
    assert rhythm.step(s, at(2026, 2, 5), lots) == OFF
    assert rhythm.step(s, at(2026, 2, 18, 11), lots) == OFF    # three weeks after its start
    assert rhythm.step(s, at(2026, 2, 18, 13), lots) == BRIGHT


def test_nothing_begins_while_it_could_not_be_seen():
    s = S()
    rhythm.migrate(s, at(2026, 1, 1))
    t = at(2026, 3, 3)
    assert rhythm.step(s, t, lots, can_begin=False) == OFF
    assert rhythm.K_PERIOD not in s
    assert rhythm.step(s, t + HOUR, lots) == BRIGHT
    # One running goes on counting while it is not drawn.
    assert rhythm.step(s, t + 2 * DAY, lots, can_begin=False) == MUTED


def test_the_version_before_the_rhythm_is_carried_over():
    now = at(2026, 11, 16)
    s = S(donate_thanked="true", donate_heart_until=str(now + 5 * DAY),
          donate_card_until="0", donate_card_shown=str(now - DAY),
          donate_watch_min="900")
    rhythm.migrate(s, now)
    # "I've already donated" was for good: a year from now.
    assert float(s[rhythm.K_UNTIL]) == now + rhythm.DONATED_S
    assert not any(k in s for k in rhythm.OLD_KEYS)
    # Watched before: not taken for a new user.
    assert float(s[rhythm.K_FIRST]) <= now - rhythm.FIRST_AFTER_DAYS * DAY
    s2 = S(donate_card_until=str(now + 40 * DAY))
    rhythm.migrate(s2, now, earliest=now - 200 * DAY)
    assert float(s2[rhythm.K_UNTIL]) == now + 40 * DAY          # the card's three months go on
    assert float(s2[rhythm.K_FIRST]) == now - 200 * DAY        # the first thing ever watched
    first = s2[rhythm.K_FIRST]
    rhythm.migrate(s2, now + DAY)                              # once
    assert s2[rhythm.K_FIRST] == first


# -- the window ------------------------------------------------------------------

T0 = at(2026, 3, 2, 9)          # the first run


class _Win(QObject, _DonateMixin):
    def __init__(self, tmp_path, now: float = T0, settings=None) -> None:
        super().__init__()
        self.clock = now
        self.settings = settings if settings is not None else QSettings(
            str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
        self._stats_log = viewing_stats.ViewingLog(self.settings)
        self._host = QWidget()                     # never shown: no windows
        self._donate_btn = HeartButton(self._host, size=12)
        self.opened: list[str] = []
        self._init_donate()

    def _donate_time(self) -> float:
        return self.clock

    def open_donate(self, ref: str) -> None:
        self.opened.append(ref)

    def watch(self, hours: float) -> None:
        self._stats_log.add("live:s:1", "News", "live", hours * HOUR, end=self.clock)

    def later(self, secs: float) -> None:
        self.clock += secs
        self._donate_minute()

    def heart(self) -> bool:
        return not self._donate_btn.isHidden()


def _asking(tmp_path) -> _Win:
    """Two weeks after the first run, six hours watched: a period begins."""
    w = _Win(tmp_path)
    w.clock = T0 + 15 * DAY
    w.watch(6)
    w._donate_minute()
    return w


def test_the_window_asks_two_weeks_after_the_first_run(tmp_path):
    w = _Win(tmp_path)
    w.watch(10)
    w._donate_minute()
    assert not w.heart()                       # a new user: not yet
    w.later(13 * DAY)
    assert not w.heart()
    w.later(DAY + HOUR)
    assert w.heart() and w._donate_ink() == theme.HEART


def test_the_window_waits_for_five_hours_watched(tmp_path):
    w = _Win(tmp_path)
    w.later(15 * DAY)
    assert not w.heart()                       # two weeks on, but nothing watched
    w.watch(4)
    w.later(HOUR)
    assert not w.heart()
    w.watch(1)
    w.later(HOUR)
    assert w.heart()


def test_private_viewing_adds_nothing_to_real_use(tmp_path):
    w = _Win(tmp_path)
    w.clock = T0 + 15 * DAY
    privacy.set_private(True)
    w.watch(20)
    privacy.set_private(False)
    w._donate_minute()
    assert not w.heart()


def test_muted_after_the_first_day_and_gone_after_the_week(tmp_path):
    w = _asking(tmp_path)
    assert w.heart() and w._donate_btn._rest == theme.HEART
    w.later(DAY)
    assert w.heart() and w._donate_btn._rest == theme.P["muted"]
    w.later(6 * DAY)
    assert not w.heart()                       # ignored: gone by itself
    w.watch(6)
    w.later(20 * DAY)                          # April
    assert w.heart() and w._donate_btn._rest == theme.HEART


def test_not_right_now_is_three_months(tmp_path):
    w = _asking(tmp_path)
    w.hide_donate_heart()
    assert not w.heart()
    assert float(w.settings.value(rhythm.K_UNTIL)) == w.clock + rhythm.LATER_S
    for _ in range(3):                         # nothing in the months that follow
        w.watch(6)
        w.later(29 * DAY)
        assert not w.heart()
    w.watch(6)
    w.later(5 * DAY)                           # past the three months
    assert w.heart()


def test_ive_donated_is_a_year(tmp_path):
    w = _asking(tmp_path)
    w.donated()
    assert not w.heart()
    assert float(w.settings.value(rhythm.K_UNTIL)) == w.clock + rhythm.DONATED_S
    for _ in range(12):
        w.watch(6)
        w.later(30 * DAY)
        assert not w.heart()
    w.watch(6)
    w.later(6 * DAY)
    assert w.heart()


def test_donate_opens_the_site_and_ends_the_week(tmp_path, monkeypatch):
    w = _asking(tmp_path)
    w.heart_clicked("app-heart")
    assert w.opened == ["app-heart"] and not w.heart()
    w.later(DAY)
    assert not w.heart()                       # not again this month
    w.watch(6)
    w.later(29 * DAY)
    assert w.heart()                           # next month, as ever
    opened: list[str] = []
    monkeypatch.setattr(mw_donate, "open_url", opened.append)
    _DonateMixin.open_donate(w, "app-home")
    assert opened == ["https://dopeiptv.org/support/?ref=app-home"]


def test_a_supporter_is_never_asked(tmp_path, monkeypatch):
    seed, pub = supporter.new_seed()
    monkeypatch.setattr(supporter, "PUBLIC_KEY_HEX", pub.hex())
    monkeypatch.setattr(supporter, "_seen", supporter.OrderedDict())
    w = _Win(tmp_path)
    w.settings.setValue("supporter_key", supporter.make(seed))
    for _ in range(4):
        w.watch(6)
        w.later(20 * DAY)
        assert not w.heart()
    assert w.settings.value(rhythm.K_PERIOD) is None
    w.settings.remove("supporter_key")
    w._donate_minute()                          # the plain icon again


def test_switched_off_it_never_asks(tmp_path):
    w = _asking(tmp_path)
    w.settings.setValue(rhythm.K_ASK, "false")
    w._donate_minute()
    assert not w.heart()
    w.watch(6)
    w.later(30 * DAY)
    assert not w.heart()
    w.settings.setValue(rhythm.K_ASK, "true")
    w.watch(6)
    w._donate_minute()
    assert w.heart()


@pytest.mark.parametrize("where", ["fullscreen", "popout", "tv", "private", "kids"])
def test_never_drawn_where_it_would_disturb(tmp_path, where):
    w = _asking(tmp_path)
    assert w.heart()

    class _Pop:
        def isFullScreen(self):  # noqa: N802 - Qt's name
            return True

    def on(yes: bool) -> None:
        if where == "fullscreen":
            w._player_fs = yes
        elif where == "popout":
            w._popout_win = _Pop() if yes else None
        elif where == "tv":
            w._tv_on = yes
        elif where == "private":
            privacy.set_private(yes)
        else:
            w._profile_kids = (lambda: True) if yes else (lambda: False)

    on(True)
    w.later(60)
    assert not w.heart()
    w.later(2 * DAY)                           # the week's days still count
    on(False)
    w.later(60)
    assert w.heart() and w._donate_btn._rest == theme.P["muted"]
    w.later(5 * DAY)
    assert not w.heart()


def test_only_the_heart_asks_no_card_anywhere(tmp_path):
    """Home's card went with Home: the heart alone asks, and its answers
    put it away."""
    w = _asking(tmp_path)
    assert w.heart() and not hasattr(w, "_donate_card")
    w.hide_donate_heart()                       # the heart's right-click
    w.later(60)
    assert not w.heart()


def test_the_keys_are_the_households(tmp_path):
    """Every key on the file's top level, none under a profile; real use is
    the whole household's watching."""
    path = str(tmp_path / "p.ini")
    s = profiles.ProfileSettings(path, QSettings.Format.IniFormat)
    store = profiles.ProfileStore(s)
    store.add("Anna", pid="anna")
    store.add("Kim", pid="kim")
    profiles.activate("anna")
    other = viewing_stats.ViewingLog(profiles.pinned(s, "kim"))
    other.add("live:s:2", "Sport", "live", 3 * HOUR, end=T0 + 15 * DAY)
    other.save()
    w = _Win(tmp_path, settings=s)
    w.profiles = store
    w.clock = T0 + 15 * DAY
    w.watch(3)                                  # Anna's three and Kim's three
    w._donate_minute()
    assert w.heart()
    w.hide_donate_heart()
    raw = profiles.raw(s)
    keys = set(raw.allKeys())
    for key in (rhythm.K_UNTIL, rhythm.K_PERIOD, rhythm.K_FIRST):
        assert key in keys and not profiles.is_own(key)
    assert not any(k.startswith("profiles/") and "donate" in k for k in keys)
    profiles.activate("kim")                    # the other profile: put away too
    w._stats_log = None
    w.later(60)
    assert not w.heart()
