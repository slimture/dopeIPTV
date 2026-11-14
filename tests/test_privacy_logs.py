"""Private viewing keeps what is watched out of the log too, and nothing new
is kept for a channel; an error report never carries the MQTT broker, the
series rules' words or the recordings' titles."""
from __future__ import annotations

import logging

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from dopeiptv.core import privacy, report  # noqa: E402
from dopeiptv.core.log import log  # noqa: E402


class _S(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's name
        self[key] = value

    def remove(self, key):
        self.pop(key, None)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def lines():
    seen: list[str] = []

    class Catch(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())
    h = Catch()
    level = log.level
    log.setLevel(logging.DEBUG)
    log.addHandler(h)
    privacy.set_private(True)
    yield seen
    privacy.set_private(False)
    log.removeHandler(h)
    log.setLevel(level)


def test_a_recording_not_started_is_not_named(app, lines, tmp_path):
    from PyQt6.QtCore import QSettings

    from dopeiptv.core.recording import RecordingManager
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    rec = RecordingManager(s)
    rec._timer.stop()
    j = {"id": "j", "title": "Secret Show", "status": "scheduled"}
    rec._no_address(j, "the source gave none")
    assert j["status"] == "failed"
    assert any("not started" in m for m in lines)
    assert not [m for m in lines if "Secret Show" in m], lines


def test_a_series_channel_without_an_address_is_not_named(lines):
    from dopeiptv.core import series_rules as sr
    from dopeiptv.ui import mw_series

    class Xtream:
        def live_streams(self, _c=None):
            return []

        def live_url(self, sid, fmt="ts"):
            raise RuntimeError("down")

    class Guide:
        def programmes_in(self, ch, lo, hi, keep):
            return [{"title": "Secret Show", "start_timestamp": lo + 600,
                     "stop_timestamp": lo + 4200}]
    import time
    rule = sr.make_rule("Secret Show", sr.EXACT, {"stream_id": 4, "name": "Hidden Channel"})
    found = mw_series._find(Xtream(), Guide(), [rule], None, time.time(), 0.0)
    assert found and found[0].url == ""
    assert any("no address" in m for m in lines)
    assert not [m for m in lines if "Hidden Channel" in m or "Secret Show" in m], lines


def test_a_portals_archive_lines_say_no_channel_or_time(lines):
    from dopeiptv.providers.stalker import StalkerClient
    c = StalkerClient("http://portal.example", "00:1A:79:00:00:01")
    c.archive_programme = lambda *a, **k: None
    assert c.archive_link(4711, 1_800_000_000.0) is None
    hit = [m for m in lines if "no programme" in m]
    assert hit and "4711" not in hit[0] and "<private>" in hit[0], hit


def test_a_channels_audio_delay_is_not_kept_while_private(lines):
    from dopeiptv.media.embedded import EmbeddedPlayer
    kept: list = []

    class Prefs:
        def set_delay(self, chan, value):
            kept.append((chan, value))
            return {"adelay": value}

    class Player:
        _remember_delay = EmbeddedPlayer._remember_delay
        _delay_chan = "pl1:7"
        _delay_key = None
        _delay_set = False
        _chan_own: dict = {}
        _settings = _S()

        def _prefs_store(self):
            return Prefs()
    p = Player()
    p._remember_delay(0.25)
    assert kept == [] and p._delay_set and dict(p._settings) == {}
    # One without a channel (a film): its own key, not kept either.
    p._delay_chan, p._delay_key = None, "nerd_adelay/abc"
    p._remember_delay(0.5)
    assert dict(p._settings) == {}
    privacy.set_private(False)
    p._remember_delay(0.5)
    assert p._settings == {"nerd_adelay/abc": "0.5"}
    p._delay_chan = "pl1:7"
    p._remember_delay(0.25)
    assert kept == [("pl1:7", 0.25)]


def test_a_report_is_scrubbed_of_the_broker_rules_and_recordings():
    from dopeiptv.core import series_rules as sr
    from dopeiptv.ui.mw_report import _ReportMixin
    s = _S(nerd_mqtt_host="mqtt.myhouse.example", nerd_mqtt_user="annasson")
    for pid, text in (("pl1", "Wallander"), ("pl2", "Morden i Sandhamn")):
        sr.SeriesRuleStore(s, pid).add(sr.make_rule(text))

    class Store:
        def playlists(self):
            return [{"id": "pl1", "name": "Mine"}, {"id": "pl2", "name": "Other"}]

    class Rec:
        jobs = [{"title": "Rapport 19.30"}]

    class W(_ReportMixin):
        settings = s
        playlist_store = Store()
        rec = Rec()
    values = W()._personal_values()
    for v in ("mqtt.myhouse.example", "annasson", "Wallander", "Morden i Sandhamn", "Rapport 19.30"):
        assert v in values, v
    text = ("events: connected to mqtt.myhouse.example as annasson; [series] rule: title "
            "'Wallander'; Morden i Sandhamn moved; record: Rapport 19.30 not started")
    out = report.scrub(text, values)
    for v in ("myhouse", "annasson", "Wallander", "Sandhamn", "Rapport 19.30"):
        assert v not in out, out
