"""Numbers from outside (core/num.py): "1e999", "Infinity", NaN, "²" (which
isdigit() takes and int() refuses) and a number of 4300 digits or more
each raised where a provider's, a guide's, a settings file's number was
read - a list, a guide, a calendar or a test gone with it. They are no
number now, wherever they come in."""
from __future__ import annotations

import pytest

from dopeiptv.core import num

BAD = ("1e999", "-1e999", "Infinity", "-inf", "nan", "NaN", "²", "²²²", "9" * 5000,
       "1" * 4400, float("inf"), float("nan"), 10 ** 400, True, None, "", "  ", "abc",
       [1], {"a": 1})


def test_the_helpers_say_no_number_and_never_raise():
    for v in BAD:
        assert num.number(v) is None, v
        assert num.whole(v) is None, v
        assert num.number(v, 7.0) == 7.0 and num.whole(v, 7) == 7, v
    assert num.number(" 3.5 ") == 3.5 and num.whole("3.9") == 3 and num.whole(-2.5) == -2
    assert num.number(12) == 12.0 and num.number("1e3") == 1000.0
    assert num.number("5", lo=6) is None and num.number("5", hi=4) is None
    assert num.number("5", lo=5, hi=5) == 5.0
    assert num.digits("120") == 120 and num.digits("١٢٠") == 120       # any script's digits
    for v in ("²", "1.5", "-1", "", None, "9" * 19, " 1", "1e3"):
        assert num.digits(v) is None, v


def test_what_the_app_reads_from_outside_takes_them(monkeypatch):
    from dopeiptv.core import (catchup, cert_pin, conn_test, connections, film_view, hls_cap,
                               ical, mitt, nerd, restream, series_rules, series_view, teletext,
                               updates)
    from dopeiptv.services import popular

    class S(dict):
        def value(self, key, default=None):
            return self.get(key, default)

    for v in BAD:
        assert connections.numbers({"max_connections": v, "active_cons": v}) == (None, None)
        assert film_view.added_at({"added": v}) == 0
        assert film_view.rating_of({"rating": v, "rating_5based": v}) is None
        assert isinstance(film_view.minutes_of({"runtime": v}, dur_secs=v), int)
        assert series_view.minutes_of({"info": {"duration_secs": v}}) == 0
        assert series_view.rating_of(v) is None
        assert mitt.length_of({"duration_secs": v, "duration": v, "runtime": v}) == 0.0
        assert nerd.Nerd(S(x=v)).num("x", 5) == 5
        assert ical.reminder_events([{"start": v, "stream_id": 1}]) == []
        assert ical.recording_events([{"status": "scheduled", "start": v}]) == []
        assert series_rules._start(v) is None
        assert series_rules._minutes(S(k=v), "k", 60) == 0
        assert hls_cap.cap_of("", v).bits == 0
        conn_test.account_lines({"exp_date": v})
    assert series_view.season_of("S" + "9" * 5000) is None
    assert series_view.episode_title({"title": "²", "episode_num": 2}) == "²"
    assert mitt.length_of({"duration": "9" * 5000 + ":00:00"}) == 0.0
    assert mitt.length_of({"duration": "²"}) == 0.0 and mitt.length_of({"duration": "45"}) == 2700.0
    assert ical.reminder_events([{"start": 2e11, "stop": "1e999", "stream_id": 1}])[0].stop is None
    assert not catchup._known("duration", "²") and catchup._known("duration", "60")
    assert teletext.page_from_text("²²²") is None and teletext.page_from_text("199") == 0x199
    assert cert_pin.pin_of({cert_pin.KEY: "ab" * 32, cert_pin.KEY_FOR: "box.example:²"}) is None
    assert updates._parse("v1.²." + "9" * 5000) == (1, 0, 999999999999999999)
    assert not popular._is_year("²²²²") and popular._is_year("2020")
    monkeypatch.setattr(restream.privacy, "playback_private", lambda kind, key: False)
    assert restream._private("²") is False and restream._private("9" * 5000) is False


@pytest.mark.parametrize("text", ["45 min", "01:02:03", "2700", "45:10"])
def test_lengths_that_read_before_still_read(text):
    assert series_view_minutes(text) > 0


def series_view_minutes(text: str) -> int:
    from dopeiptv.core import series_view
    return series_view.minutes_of({"info": {"duration": text}})
