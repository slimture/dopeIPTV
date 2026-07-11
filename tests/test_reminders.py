"""Unit tests for the EPG ReminderStore."""

from dopeiptv.services.reminders import ReminderStore


class FakeSettings:
    def __init__(self, values=None):
        self._v = dict(values or {})

    def value(self, key, default=None):
        return self._v.get(key, default)

    def setValue(self, key, val):
        self._v[key] = val


def _ch(sid, name="Ch"):
    return {"name": name, "stream_id": sid, "num": sid,
            "epg_channel_id": f"c{sid}"}


def test_add_has_remove():
    r = ReminderStore(FakeSettings(), "p")
    r.add(_ch(5), "News", 1000)
    assert r.has(5, 1000)
    assert not r.has(6, 1000)
    r.remove(5, 1000)
    assert not r.has(5, 1000)


def test_add_dedupes_same_channel_and_start():
    r = ReminderStore(FakeSettings(), "p")
    r.add(_ch(5), "News", 1000)
    r.add(_ch(5), "News (updated)", 1000)
    assert len(r.all()) == 1
    assert r.all()[0]["title"] == "News (updated)"


def test_due_fires_within_grace_and_removes():
    r = ReminderStore(FakeSettings(), "p")
    r.add(_ch(5), "News", 1000)
    r.add(_ch(6), "Film", 5000)          # still upcoming
    due = r.due(1200)                     # 200s after start, within grace
    assert [d["title"] for d in due] == ["News"]
    assert not r.has(5, 1000)             # fired -> gone
    assert r.has(6, 5000)                 # upcoming -> kept


def test_due_drops_missed_silently():
    r = ReminderStore(FakeSettings(), "p")
    r.add(_ch(5), "News", 1000)
    assert r.due(1000 + 10_000) == []     # long past grace
    assert not r.has(5, 1000)


def test_persists_across_instances():
    s = FakeSettings()
    ReminderStore(s, "p").add(_ch(5), "News", 1000)
    assert ReminderStore(s, "p").has(5, 1000)


def test_playlist_scoped_key():
    assert ReminderStore(FakeSettings(), "abc")._key == "epg_reminders_abc"
    assert ReminderStore(FakeSettings(), None)._key == "epg_reminders"
