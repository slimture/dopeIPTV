"""Smoke tests for the i18n translation module."""

import pathlib
import re

from dopeiptv import i18n


def test_every_tr_key_used_in_code_is_defined():
    """Guard against typos / forgotten keys: every tr("literal") referenced
    anywhere in the package must have an entry in _STRINGS."""
    pkg = pathlib.Path(i18n.__file__).parent
    used = set()
    for path in pkg.glob("*.py"):
        for m in re.finditer(r'tr\(\s*"([a-z0-9_]+)"', path.read_text()):
            used.add(m.group(1))
    # Illustrative keys in the module docstring, not real usage.
    used -= {"your_key", "some_key"}
    missing = sorted(k for k in used if k not in i18n._STRINGS)
    assert not missing, f"tr() keys used but not defined: {missing}"


def test_all_keys_cover_all_languages():
    langs = set(i18n.LANGUAGES)
    for key, translations in i18n._STRINGS.items():
        assert set(translations) == langs, f"{key} missing {langs - set(translations)}"


def test_placeholders_match_across_languages():
    for key, translations in i18n._STRINGS.items():
        en_ph = set(re.findall(r"\{(\w+)\}", translations["en"]))
        for code, text in translations.items():
            ph = set(re.findall(r"\{(\w+)\}", text))
            assert ph == en_ph, f"{key}[{code}] placeholders {ph} != {en_ph}"


def test_set_and_current_language():
    i18n.set_language("sv")
    assert i18n.current_language() == "sv"
    assert i18n.tr("nav_movies") == "Filmer"
    i18n.set_language("en")
    assert i18n.tr("nav_movies") == "Movies"


def test_format_substitution():
    i18n.set_language("en")
    assert i18n.tr("status_playing", title="CNN") == "Playing: CNN"


def test_unknown_language_falls_back_to_english():
    i18n.set_language("xx")
    assert i18n.tr("nav_tv") == "TV"
    i18n.set_language("en")
