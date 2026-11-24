"""A category for adults stays out of a kids profile however it is renamed.

The kids test read only the name the lists show, the one given in the app,
and a kids profile could give it one: "XXX Adults 18+" renamed "Fun
cartoons" from its menu or Manage categories… and its channels were in
every list. The provider's own name counts as well now, and Manage
categories… lists for a kids profile only what its lists show."""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QListWidget  # noqa: E402

from dopeiptv.core import profiles  # noqa: E402
from dopeiptv.core import search_all as sa  # noqa: E402
from dopeiptv.core.stores import CategoryOverrides, ParentalControl  # noqa: E402
from dopeiptv.services import failover  # noqa: E402
from dopeiptv.ui import message_box  # noqa: E402
from dopeiptv.ui.dialogs import ContentManagerDialog  # noqa: E402
from dopeiptv.ui.mw_context import _ContextMenuMixin  # noqa: E402
from dopeiptv.ui.mw_profiles import KidsFilter, adult_ids  # noqa: E402


class Settings:
    def __init__(self) -> None:
        self.d: dict = {}

    def value(self, key, default=None):
        return self.d.get(key, default)

    def setValue(self, key, value) -> None:  # noqa: N802
        self.d[key] = value

    def remove(self, key) -> None:
        self.d.pop(key, None)


CATS = [{"category_id": "1", "category_name": "News"},
        {"category_id": "7", "category_name": "XXX Adults 18+"},
        {"category_id": "8", "category_name": "Cartoons"}]
ADULT = {"stream_id": 700, "name": "Adult One", "category_id": "7"}


class Client:
    def live_categories(self):
        return CATS

    def live_streams(self, _cat=None):
        return [ADULT]


@pytest.fixture()
def app():
    return QApplication.instance() or QApplication([])


def test_a_renamed_category_is_still_for_adults():
    assert profiles.adult_category("Fun cartoons", "XXX Adults 18+")
    assert profiles.adult_category("XXX", "Cartoons")
    assert not profiles.adult_category("Fun cartoons", "Cartoons")
    assert not profiles.adult_category(None, None)
    o = CategoryOverrides(Settings(), "c")
    o.update("live", "7", name="Fun cartoons")
    assert adult_ids(CATS, o, "live") == frozenset({"7"})
    assert sa.adult_ids(CATS, {"7": "Fun cartoons"}) == frozenset({"7"})
    assert failover.kids_rows(Client(), [ADULT], {"7": "Fun cartoons"}) == []


class KidsWindow(_ContextMenuMixin):
    def __init__(self, settings) -> None:
        self.client = Client()
        self.mode = "live"
        self.overrides = CategoryOverrides(settings, "category_overrides_p1")
        self.parental = ParentalControl(settings)
        self._raw_categories = CATS
        self.known: dict = {}
        self.kids = True

    def _profile_kids(self) -> bool:
        return self.kids

    def _kids_known(self) -> dict:
        return self.known

    def _kids_fetch_cats(self) -> None:
        self.known["live"] = (self.client, adult_ids(CATS, self.overrides, "live"))


def test_a_rename_in_manage_categories_does_not_open_it(app, monkeypatch):
    settings = Settings()
    win = KidsWindow(settings)
    win._kids_fetch_cats()
    assert not KidsFilter(win, "live")(ADULT)
    dlg = ContentManagerDialog(None, "live", CATS, win.overrides)
    dlg.window = win
    lw = dlg.findChildren(QListWidget)[0]
    lw.setCurrentRow(next(i for i in range(lw.count()) if "XXX" in lw.item(i).text()))
    monkeypatch.setattr(message_box, "get_text", lambda *a, **k: ("Fun cartoons", True))
    dlg._rename()
    assert win.overrides.display_name("live", "7", "") == "Fun cartoons"
    win._kids_fetch_cats()
    assert not KidsFilter(win, "live")(ADULT)


def test_manage_categories_lists_for_a_kids_profile_only_what_it_sees():
    settings = Settings()
    win = KidsWindow(settings)
    win.overrides.update("live", "7", name="Fun cartoons")
    win.overrides.update("live", "8", locked=True)
    assert [c["category_id"] for c in win._managed_categories()] == ["1"]
    win.kids = False
    assert [c["category_id"] for c in win._managed_categories()] == ["1", "7", "8"]
