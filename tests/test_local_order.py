"""Local files in the order picked for every folder (core/local_listing
order_rows): folders first, by name; the files by name, the last changed,
the oldest, the largest first, or by type; headings keep their parts."""
from __future__ import annotations

from dopeiptv.core import local_listing as ll


def _f(name, added, size, ext=".mkv"):
    return {"name": name, "_kind": "local", "_path": f"/m/{name}{ext}", "_key": f"/m/{name}{ext}",
            "added": str(added), "_size": size}


def _d(name):
    return {"name": name, "_kind": "localdir", "_path": f"/m/{name}", "_key": f"/m/{name}"}


ROWS = [_d("Serier"), _d("anime"), _f("Babel", 300, 5), _f("alien", 100, 9, ".mp4"),
        _f("Cars", 200, 1, ".avi"), _f("old", 0, 3)]


def _names(rows):
    return [r["name"] for r in rows]


def test_folders_first_by_name_whatever_the_order():
    for order in ll.ORDERS:
        out = ll.order_rows(ROWS, order)
        assert {r["_kind"] for r in out[:2]} == {"localdir"}, order
    assert _names(ll.order_rows(ROWS, ll.NAME))[:2] == ["anime", "Serier"]
    assert _names(ll.order_rows(ROWS, ll.NAME_DESC))[:2] == ["Serier", "anime"]


def test_each_order_of_the_files():
    files = lambda order: _names(ll.order_rows(ROWS, order))[2:]   # noqa: E731
    assert files(ll.NAME) == ["alien", "Babel", "Cars", "old"]
    assert files(ll.NAME_DESC) == ["old", "Cars", "Babel", "alien"]
    assert files(ll.MODIFIED) == ["Babel", "Cars", "alien", "old"]
    assert files(ll.OLDEST) == ["alien", "Cars", "Babel", "old"]      # no time: last
    assert files(ll.LARGEST) == ["alien", "Babel", "old", "Cars"]
    assert files(ll.TYPE) == ["Cars", "Babel", "old", "alien"]        # .avi .mkv .mkv .mp4


def test_headings_keep_their_parts_and_a_bad_order_is_by_name():
    rows = [{"_header": "Films"}, _f("b", 2, 1), _f("a", 1, 2),
            {"_header": "Home videos"}, _f("y", 9, 1), _f("x", 8, 9)]
    out = ll.order_rows(rows, ll.LARGEST)
    assert [r.get("_header") or r["name"] for r in out] == ["Films", "a", "b", "Home videos", "x", "y"]
    assert _names(ll.order_rows(ROWS, "bogus"))[2] == "alien"


def test_the_setting_read_safely():
    class S(dict):
        def value(self, k, d=None):
            return self.get(k, d)
    assert ll.order_of(S()) == ll.NAME
    assert ll.order_of(S(local_sort="largest")) == ll.LARGEST
    assert ll.order_of(S(local_sort="nonsense")) == ll.NAME
