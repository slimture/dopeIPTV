"""Robustness locks: properties the app relies on but nothing else enforces.

Each test here guards an invariant that, when broken, fails silently or
crashes only in the field (a missing {placeholder} in one language, a store
choking on corrupt QSettings JSON, a UI string that formats badly in Thai).
They are cheap to run and turn those field crashes into red tests.
"""
import ast
import json
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


# -- i18n ---------------------------------------------------------------------

def _i18n_entries():
    """(key, {lang: text}) pairs parsed straight from i18n.py's literal dict,
    so the test sees exactly what ships (no import-order surprises)."""
    tree = ast.parse((_REPO_ROOT / "dopeiptv" / "i18n.py").read_text())
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for k, v in zip(node.keys, node.values, strict=False):
            if (isinstance(k, ast.Constant) and isinstance(k.value, str)
                    and isinstance(v, ast.Dict)):
                langs = {}
                for lk, lv in zip(v.keys, v.values, strict=False):
                    if not (isinstance(lk, ast.Constant)
                            and isinstance(lv, ast.Constant)
                            and isinstance(lv.value, str)):
                        langs = None
                        break
                    langs[lk.value] = lv.value
                if langs and "en" in langs and len(langs) > 1:
                    out.append((k.value, langs))
    return out


def test_i18n_placeholders_match_across_languages():
    """Every translation of a key must use exactly the English placeholders.
    A language missing {n} (or inventing {m}) renders a broken string - or
    nothing - only for users of that language, which no other test sees."""
    bad = []
    for key, langs in _i18n_entries():
        ref = set(re.findall(r"\{(\w+)\}", langs["en"]))
        for lang, text in langs.items():
            if set(re.findall(r"\{(\w+)\}", text)) != ref:
                bad.append((key, lang, sorted(ref)))
    assert not bad, f"placeholder mismatches: {bad}"


def test_tr_never_raises():
    """tr() must degrade, not crash the UI thread: unknown keys are marked,
    missing/extra format kwargs fall back to the raw string - in every
    language."""
    from dopeiptv.i18n import LANGUAGES, current_language, set_language, tr
    saved = current_language()
    try:
        for code in LANGUAGES:
            set_language(code)
            assert tr("definitely_not_a_key") == "?definitely_not_a_key"
            # Missing kwarg: falls back to the unformatted text, no raise.
            assert isinstance(tr("mv_cell"), str)
            # Proper kwarg formats.
            assert "3" in tr("mv_cell", n=3)
            # Extra kwargs are ignored.
            assert isinstance(tr("btn_settings", bogus=1), str)
    finally:
        set_language(saved)


# -- stores vs corrupt/junk persisted data ------------------------------------

class _FakeSettings:
    """QSettings stand-in: dict-backed, accepts any JSON-ish garbage."""

    def __init__(self, initial=None):
        self.data = dict(initial or {})

    def value(self, key, default=None):
        return self.data.get(key, default)

    def setValue(self, key, value):
        self.data[key] = value

    def sync(self):
        pass


CORRUPT = ["{oops", "42", '"just a string"', '[{"no": "fields"}]',
           '[null, 17, "x"]', "", None]


@pytest.mark.parametrize("blob", CORRUPT)
def test_history_store_survives_corrupt_settings(blob):
    from dopeiptv.core.stores import HistoryStore
    s = _FakeSettings({"history": blob})
    h = HistoryStore(s, "history")
    # Every public op works on whatever survived.
    h.add("http://x/live/u/p/1.ts", "One", None, 1, "live")
    assert any(e.get("_key") == 1 for e in h.entries)
    h.remove(1, "live")
    h.clear_kind({"live"})
    h.clear()
    assert h.entries == []


@pytest.mark.parametrize("blob", CORRUPT)
def test_favorite_store_survives_corrupt_settings(blob):
    from dopeiptv.core.stores import FavoriteStore
    s = _FakeSettings({"favorites": blob})
    f = FavoriteStore(s, "favorites")
    f.add("Default", {"stream_id": 5, "name": "Chan"})
    assert f.is_favorite(5)
    # Junk items without the id key are tolerated by lookups and removal.
    f.groups.setdefault("Default", []).append({"name": "no-id"})
    assert f.groups_for(5) == ["Default"]
    f.remove(5)
    assert not f.is_favorite(5)


def test_favorite_store_junk_entries_do_not_break_lookups():
    from dopeiptv.core.stores import FavoriteStore
    s = _FakeSettings(
        {"favorites": json.dumps({"G": [None, 3, {"stream_id": 9}]})})
    f = FavoriteStore(s, "favorites")
    # A None/int row would crash .get() lookups if the store iterated naively.
    try:
        f.is_favorite(9)
    except AttributeError:
        pytest.fail("junk favorite rows crash lookups")


@pytest.mark.parametrize("blob", CORRUPT)
def test_resume_store_survives_corrupt_settings(blob):
    from dopeiptv.services.resume import ResumeStore
    s = _FakeSettings({"resume_positions_p": blob})
    r = ResumeStore(s, "p")
    r.record("vod", 7, pos=600, dur=3600, item={"stream_id": 7, "name": "M"})
    assert r.saved_position(7, "movie") == 600
    assert r.continue_watching()
    r.clear("vod", 7)
    assert r.saved_position(7, "movie") == 0.0


def test_history_healing_is_surgical():
    """The live->movie/episode healing must only retag entries whose URL
    proves them mis-filed - real live channels keep their kind."""
    from dopeiptv.core.stores import HistoryStore
    rows = [
        {"name": "F", "_url": "http://x/movie/u/p/1.mkv", "_key": 1,
         "_kind": "live"},
        {"name": "E", "_url": "http://x/series/u/p/2.mkv", "_key": 2,
         "_kind": "live"},
        {"name": "C", "_url": "http://x/live/u/p/3.ts", "_key": 3,
         "_kind": "live"},
        {"name": "M", "_url": "http://x/movie/u/p/4.mkv", "_key": 4,
         "_kind": "movie"},
    ]
    s = _FakeSettings({"history": json.dumps(rows)})
    h = HistoryStore(s, "history")
    kinds = {e["name"]: e["_kind"] for e in h.entries}
    assert kinds == {"F": "movie", "E": "episode", "C": "live", "M": "movie"}


# -- the whole UI builds in every language ------------------------------------

def test_ui_builds_in_all_languages():
    """Construct the main window once, then cycle through every language:
    retranslate the chrome and build the full settings dialog (all tabs, all
    strings). Catches the class of bug that only crashes in one language."""
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication, QDialog

    from dopeiptv.i18n import LANGUAGES, set_language
    from dopeiptv.providers.client import DemoClient
    from dopeiptv.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    settings = QSettings("dopeiptv-test", "robustness-i18n")
    settings.clear()
    w = MainWindow(DemoClient(), settings)
    orig = QDialog.exec
    QDialog.exec = lambda self: 0   # build fully, never block
    try:
        for code in LANGUAGES:
            set_language(code)
            w.retranslate_ui()
            w.open_settings()
            app.processEvents()
    finally:
        QDialog.exec = orig
        set_language("en")
        w.retranslate_ui()
