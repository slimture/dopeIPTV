"""A card keeps the picture it shows while a new one is on its way: TMDB's
poster taking over from the provider's blinked to an empty box and back to
the same artwork (media_cards.card_picture)."""
from __future__ import annotations

import pytest


def test_a_new_picture_on_its_way_never_leaves_the_card_empty():
    pytest.importorskip("PyQt6.QtWidgets")
    from dopeiptv.ui.media_cards import Card, card_picture
    images = {"https://panel/poster.jpg": "provider", "https://series/poster.jpg": "series"}
    card = Card(key="m1", title="The Bridge", image="https://panel/poster.jpg")
    assert card_picture(images, card) == ("provider", True)

    card.image = "https://image.tmdb.org/t/p/w500/abc.jpg"     # TMDB answered
    assert card_picture(images, card) == ("provider", True)     # still the one it had
    images[card.image] = "tmdb"
    assert card_picture(images, card) == ("tmdb", True)          # then the new one

    images.pop("https://panel/poster.jpg")
    card.image = "https://image.tmdb.org/t/p/w500/other.jpg"
    assert card_picture(images, card) == ("tmdb", True)


def test_a_card_with_nothing_shown_yet_falls_back_as_before():
    pytest.importorskip("PyQt6.QtWidgets")
    from dopeiptv.ui.media_cards import Card, card_picture
    images = {"https://series/poster.jpg": "series"}
    card = Card(key="e1", title="E1", image="https://still/e1.jpg",
                fallback="https://series/poster.jpg")
    assert card_picture(images, card) == ("series", False)
    assert card_picture({}, Card(key="x", title="X")) == (None, False)
    assert Card(key="a", title="A") == Card(key="a", title="A", shown="elsewhere")
