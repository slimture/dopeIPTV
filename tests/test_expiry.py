"""A source's account running out, said before it does (core/expiry.py,
ui/mw_source_label._expiry_check): from the login's own answer, within a
week of the end or after it, once a day per source, never to a kids
profile, and never in the way of a login."""
from __future__ import annotations

import datetime as dt
import json
import os
from types import SimpleNamespace

import pytest

from dopeiptv.core import expiry

DAY = 86400.0


def _at(y, m, d, h=12):
    return dt.datetime(y, m, d, h).timestamp()


NOW = _at(2026, 11, 24)


@pytest.mark.parametrize("raw", [None, "", "0", 0, "null", "None", "unlimited", -5, "-5",
                                 True, "1e999", "nan", 4102444800 * 1000, "9" * 400, {}, []])
def test_no_end_is_said_of_an_answer_that_names_none(raw):
    assert expiry.expires_at({"exp_date": raw}) is None


def test_an_end_reads_as_a_number_or_its_text():
    assert expiry.expires_at({"exp_date": "1795000000"}) == 1795000000.0
    assert expiry.expires_at({"exp_date": 1795000000}) == 1795000000.0
    assert expiry.expires_at(None) is None and expiry.expires_at("x") is None


def test_days_are_the_calendars():
    assert expiry.days_left(_at(2026, 11, 24, 23), _at(2026, 11, 24, 1)) == 0
    assert expiry.days_left(_at(2026, 11, 25, 0), _at(2026, 11, 24, 23)) == 1
    assert expiry.days_left(_at(2026, 11, 23), NOW) == -1


def test_what_is_said_and_when():
    assert expiry.to_tell(None, NOW, None) is None
    assert expiry.to_tell(_at(2026, 12, 9), NOW, None) is None            # 15 days off
    assert expiry.to_tell(_at(2026, 12, 2), NOW, None) is None            # 8 days off
    assert expiry.to_tell(_at(2026, 12, 1), NOW, None) == "soon"          # a week
    assert expiry.to_tell(_at(2026, 11, 25), NOW, None) == "soon"
    assert expiry.to_tell(_at(2026, 11, 24, 23), NOW, None) == "today"
    assert expiry.to_tell(_at(2026, 11, 20), NOW, None) == "past"
    # Once a day: told today, nothing more until tomorrow.
    assert expiry.to_tell(_at(2026, 11, 25), NOW, "2026-11-24") is None
    assert expiry.to_tell(_at(2026, 11, 25), NOW, "2026-11-23") == "soon"


def test_the_days_told_survive_anything_kept():
    for raw in (None, "", "x", "[1,2]", '"text"', "{", json.dumps({"a": 1})):
        assert expiry.read_told(raw) == {}
    told = expiry.read_told(expiry.write_told({}, "src1", NOW))
    assert told == {"src1": "2026-11-24"}
    many = {f"s{i}": "2026-01-01" for i in range(300)}
    kept = expiry.read_told(expiry.write_told(many, "new", NOW))
    assert len(kept) == 256 and kept["new"] == "2026-11-24" and "s0" not in kept


# -- the window -------------------------------------------------------------------------

@pytest.fixture
def window(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from dopeiptv.ui import notices
    from dopeiptv.ui.mw_source_label import _SourceLabelMixin
    posted: list = []

    class Hub:
        def post(self, text, **kw):
            posted.append((text, kw))
            return 1

    monkeypatch.setattr(notices, "hub_of", lambda _w: Hub())
    w = SimpleNamespace(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat),
                        kids=False, opened=[])
    w._profile_kids = lambda: w.kids
    w.open_settings_at = lambda page: w.opened.append(page)
    w.check = lambda pl, answer: _SourceLabelMixin._expiry_check(w, pl, answer)
    w.posted = posted
    return w


def _answer(ends):
    return {"user_info": {"auth": 1, "exp_date": str(int(ends))}}


def test_a_login_whose_account_ends_this_week_says_it_once_a_day(window, monkeypatch):
    monkeypatch.setattr(expiry, "today", lambda now: "2026-11-24")
    soon = dt.datetime.now().timestamp() + 3 * DAY
    pl = {"id": "a1b2c3d4", "name": "Pirat"}
    window.check(pl, _answer(soon))
    (text, kw), = window.posted
    assert "Pirat" in text and kw["level"] == "warning" and kw["key"] == "expiry:a1b2c3d4"
    kw["action"]()
    assert window.opened == ["sources"]
    window.check(pl, _answer(soon))                 # the same day: nothing more
    assert len(window.posted) == 1
    window.check({"id": "other", "name": "Other"}, _answer(soon))   # another source: its own
    assert len(window.posted) == 2


def test_nothing_for_a_far_end_none_a_kids_profile_or_an_odd_answer(window):
    pl = {"id": "a1b2c3d4", "name": "Pirat"}
    window.check(pl, _answer(dt.datetime.now().timestamp() + 30 * DAY))
    window.check(pl, {"user_info": {"auth": 1, "exp_date": None}})
    window.check(pl, None)
    window.check(pl, "garbage")
    window.check(None, _answer(dt.datetime.now().timestamp() + DAY))
    window.kids = True
    window.check(pl, _answer(dt.datetime.now().timestamp() + DAY))
    assert window.posted == []


def test_a_failure_inside_is_never_a_logins(window):
    window.settings = None                          # value() raises inside
    window.check({"id": "x", "name": "X"}, _answer(dt.datetime.now().timestamp() + DAY))
    assert window.posted == []


def test_both_logins_say_it():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    app = (root / "dopeiptv" / "app.py").read_text(encoding="utf-8")
    assert "w._expiry_check(pl, auth_ok[0])" in app
    main = (root / "dopeiptv" / "ui" / "main_window.py").read_text(encoding="utf-8")
    # switch_playlist's login and the welcome's connect step both end in
    # _use_source, which says it.
    assert "self._expiry_check(pl, auth)" in main and "self._use_source(pid, candidate, _auth" in main


def test_its_words_are_in_every_language():
    from dopeiptv import i18n
    for key in ("expiry_soon", "expiry_today", "expiry_past", "expiry_open"):
        assert set(i18n._STRINGS[key]) == set(i18n.LANGUAGES), key
        for lang, text in i18n._STRINGS[key].items():
            if key != "expiry_open":
                assert "{name}" in text, (key, lang)
            if key in ("expiry_soon", "expiry_past"):
                assert "{date}" in text, (key, lang)
