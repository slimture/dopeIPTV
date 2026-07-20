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


def test_every_key_has_english_source_and_no_stray_langs():
    """English is the inline source, so every key must carry an "en" string.
    Add-on locales may cover only part of the keys (they fall back to English by
    design), so we don't require every language on every key - but no entry may
    carry a language code we don't declare (a typo'd locale filename, say)."""
    known = {"en"} | set(i18n._NATIVE_NAMES)
    for key, translations in i18n._STRINGS.items():
        assert translations.get("en"), f"{key} has no English source"
        stray = set(translations) - known
        assert not stray, f"{key} has undeclared language(s) {stray}"


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


def test_i18n_status_tool_reports_all_locales_healthy():
    # The contributor-facing health tool must stay runnable and, with the
    # shipped locales, report a clean bill (exit 0 = no incomplete/stray/
    # placeholder issues). This doubles as a guard that every locale is sound.
    import importlib.util
    import os

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "tools", "i18n_status.py")
    spec = importlib.util.spec_from_file_location("i18n_status", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._table(i18n.base_string_keys()) == 0


def test_locale_dir_found_under_frozen_bundle_root(tmp_path, monkeypatch):
    """In a PyInstaller build the modules live in an archive, so __file__ has
    no locale/ beside it; the files are unpacked under sys._MEIPASS instead.
    _locale_dir must fall back there or the picker collapses to English-only
    (the 1.0.0 packaging regression this guards against)."""
    import os

    # A bundle root that holds dopeiptv/locale/, and a bogus module path with
    # nothing beside it (mimics __file__ pointing into the frozen archive).
    (tmp_path / "dopeiptv" / "locale").mkdir(parents=True)
    (tmp_path / "dopeiptv" / "locale" / "sv.json").write_text("{}")
    monkeypatch.setattr(i18n, "__file__", str(tmp_path / "nope" / "i18n.py"))
    monkeypatch.setattr(i18n._sys, "_MEIPASS", str(tmp_path), raising=False)

    assert i18n._locale_dir() == os.path.join(
        str(tmp_path), "dopeiptv", "locale")
