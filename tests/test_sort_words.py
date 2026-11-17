"""The sort button names the order the list is in; the menu's first entry
says what every category follows (mw_sort)."""
from __future__ import annotations

from dopeiptv.i18n import tr
from dopeiptv.ui.mw_sort import ORDER_WORDS, _SortMixin, order_words


class _Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def contains(self, key):
        return key in self


class _Win(_SortMixin):
    def __init__(self, mode="vod", **settings):
        self.mode = mode
        self._current_cat = "7"
        self.settings = _Settings(settings)


def test_the_first_entry_names_the_order_every_category_follows():
    w = _Win(sort_order="alpha_asc")
    opts = w._sort_options()
    assert opts[0] == ("global", tr("sort_as_all", order=tr("sort_az")))
    assert [k for k, _ in opts[1:]] == ["default", "alpha_asc", "alpha_desc", "recent"]
    assert dict(opts)["default"] == tr("sort_provider")           # not "Default"
    assert w._sort_button_words() == tr("sort_az")                  # following: its order
    w.settings[w._sort_setting_key()] = "recent"
    assert w._sort_button_words() == tr("option_sort_recent")       # its own


def test_nothing_chosen_is_the_providers_order_and_series_newest_updated():
    assert _Win()._sort_options()[0][1] == tr("sort_as_all", order=tr("sort_provider"))
    series = _Win(mode="series")
    assert series._global_sort_order() == "recent"
    assert series._sort_options()[0][1] == tr("sort_as_all", order=tr("place_sort_updated"))
    assert _Win(sort_order="bogus")._global_sort_order() == "default"
    assert order_words("recent", updated=True) == tr("place_sort_updated")
    assert set(ORDER_WORDS) == {"default", "alpha_asc", "alpha_desc", "recent"}


def test_swedish_says_a_to_o():
    from dopeiptv import i18n
    old = i18n.current_language()
    try:
        i18n.set_language("sv")
        assert tr("sort_az") == "A→Ö" and tr("sort_provider") == "Leverantörens ordning"
    finally:
        i18n.set_language(old)
