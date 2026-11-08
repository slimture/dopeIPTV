"""A Stalker / Ministra portal goes through a playlist export and import
with its MAC address - not turned into an Xtream playlist."""
from __future__ import annotations

from dopeiptv.core.stores import PlaylistStore


class Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - QSettings' name
        self[key] = value


def test_a_portal_keeps_its_mac_through_export_and_import():
    a = PlaylistStore(Settings())
    a.add({"name": "Portal", "kind": "stalker",
           "server": "http://portal.invalid/c/", "mac": "00:1A:79:12:34:56",
           "username": "", "password": ""})
    data = a.export_data()
    row = [r for r in data if r["kind"] == "stalker"][0]
    assert row["mac"] == "00:1A:79:12:34:56"
    b = PlaylistStore(Settings())
    before = len(b.items)
    assert b.import_data(data) == 1
    got = [p for p in b.items if p.get("kind") == "stalker"]
    assert len(b.items) == before + 1 and got
    assert got[0]["mac"] == "00:1A:79:12:34:56"
    assert got[0]["server"] == "http://portal.invalid/c/"
    assert b.import_data(data) == 0, "not added twice"


def test_a_portal_without_a_mac_is_not_imported():
    b = PlaylistStore(Settings())
    n = b.import_data([{"kind": "stalker", "server": "http://p.invalid/c/"}])
    assert n == 0
