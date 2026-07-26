"""Matching a cast member's filmography against the playlist.

The credits come from TMDB as plain titles; they have to be matched against
the provider's own (often dirty) titles. That match used to strip spaces as
well as punctuation, which turned its "provider title starts with the credit"
rule into a substring test with no word boundaries - so a short credit claimed
every longer title beginning with the same letters, and actors showed up in
films they have nothing to do with.

Pure string logic, so it runs in-process against the mixin's own methods.
"""
import pytest

from dopeiptv.ui.mw_detail import _DetailMixin as D


def norm(s):
    return D._normalize_title(s)


def matches(credits, provider_titles):
    """The provider titles that would be listed for a person with *credits*."""
    norm_titles = {n for n in (norm(t) for t in credits) if n}
    long_titles = [t for t in norm_titles if len(t) >= 6]
    return [t for t in provider_titles
            if D._title_matches(D, norm(t), norm_titles, long_titles)]


def test_release_noise_is_stripped_but_words_stay_separate():
    assert norm("Inception (2010) 1080p") == "inception"
    assert norm("EN| Inception MULTI") == "inception"
    assert norm("Inception.2010.1080p.BluRay.x264") == "inception"
    # Words must not be glued together - that is what broke the prefix rule.
    assert norm("American Manhunt") == "american manhunt"
    assert norm("The Wall Street Documentary") == "the wall street documentary"


def test_exact_titles_still_match():
    found = matches(["Inception", "Tillsammans"],
                    ["Inception (2010) 1080p", "EN| Tillsammans", "Alien"])
    assert found == ["Inception (2010) 1080p", "EN| Tillsammans"]


def test_trailing_release_junk_still_matches():
    # Junk the noise regex doesn't know, but that doesn't change WHICH film
    # this is, must still match.
    found = matches(["Inception"],
                    ["Inception REPACK", "Inception Extended Edition",
                     "Inception SWEDISH SUBBED"])
    assert len(found) == 3, found


def test_a_credit_does_not_claim_longer_unrelated_titles():
    # The reported bug: a Swedish actor credited on American documentaries.
    # "America" is 7 characters, so the old rule let it claim every provider
    # title starting with those letters once spaces were stripped.
    found = matches(["America"],
                    ["American Manhunt", "American Documentaries Vol 2",
                     "Americas Deadliest"])
    assert found == [], found
    # Same shape, with a genuine multi-word credit.
    found = matches(["The Wall"],
                    ["The Wall Street Documentary", "The Walking Dead"])
    assert found == [], found


def test_a_sequel_is_not_the_original():
    found = matches(["Frozen"], ["Frozen II", "Frozen 2", "Frozen"])
    assert found == ["Frozen"], found


def test_short_credits_never_match_by_prefix():
    # Under six characters a credit carries too little signal to claim a
    # longer provider title - only an exact match counts.
    found = matches(["Up"], ["Up in the Air", "Uptown Girls", "Up"])
    assert found == ["Up"], found


@pytest.mark.parametrize("title", ["", "   ", "(2019)", "1080p"])
def test_empty_and_noise_only_titles_never_match(title):
    assert matches(["Inception"], [title]) == []
    # ...and a credit that normalises to nothing must not match everything.
    assert matches([title], ["Inception", "Alien"]) == []
