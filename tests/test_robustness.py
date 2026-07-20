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


_CORE_LANGUAGES = ("en", "sv", "es", "de", "fr", "zh", "ru", "th")


def test_every_key_covers_every_core_language():
    """Every string must be translated (non-empty) into all eight core
    languages. A missing or blank one silently falls back to English for that
    language's users - the app then looks half-finished in, say, German only,
    which no screenshot in English ever reveals. Add-on locales (locale/*.json)
    are exempt: they fall back by design and are gated by coverage."""
    gaps = []
    for key, langs in _i18n_entries():
        for code in _CORE_LANGUAGES:
            if not str(langs.get(code, "")).strip():
                gaps.append(f"{key} → {code}")
    assert not gaps, (
        f"{len(gaps)} core-language translation gaps:\n"
        + "\n".join(gaps[:50]))


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


def test_locale_files_are_valid():
    """Every add-on locale file (dopeiptv/locale/*.json) must: parse, use only
    real string keys, and keep each English placeholder. A stray key is dead
    weight; a dropped/invented {placeholder} renders a broken string for that
    language only - exactly the kind of defect that ships unnoticed."""
    import json
    from dopeiptv.i18n import base_string_keys, english

    locale_dir = _REPO_ROOT / "dopeiptv" / "locale"
    valid_keys = set(base_string_keys())
    problems: list[str] = []
    for path in sorted(locale_dir.glob("*.json")):
        if path.name.startswith(("_",)) or path.name.endswith(".template.json"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            problems.append(f"{path.name}: invalid JSON ({e})")
            continue
        if not isinstance(data, dict):
            problems.append(f"{path.name}: top level is not an object")
            continue
        for key, value in data.items():
            if key not in valid_keys:
                problems.append(f"{path.name}: unknown key {key!r}")
                continue
            if not isinstance(value, str):
                problems.append(f"{path.name}: {key!r} is not a string")
                continue
            ref = set(re.findall(r"\{(\w+)\}", english(key)))
            got = set(re.findall(r"\{(\w+)\}", value))
            if got != ref:
                problems.append(
                    f"{path.name}: {key!r} placeholders {sorted(got)} "
                    f"!= English {sorted(ref)}")
    assert not problems, "locale file problems:\n" + "\n".join(problems)


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


# -- timeshift probe: network failure is not proof ----------------------------

def test_ts_probe_distinguishes_network_failure_from_dead_archive():
    """A probe that never REACHES the provider (timeout/DNS/TLS) must not
    report the archive as proven dead - that verdict hides the channel's
    timeshift for 14 days. Only a real non-stream response counts."""
    from dopeiptv.ui.mw_recording import _RecordingMixin

    class _Resp:
        def __init__(self, ctype, body):
            self.headers = {"Content-Type": ctype}
            self._body = body

        def iter_content(self, _n):
            yield self._body

        def close(self):
            pass

    class _Sess:
        def __init__(self, mode):
            self.mode = mode

        def get(self, _u, **_kw):
            if self.mode == "timeout":
                raise OSError("timed out")
            if self.mode == "html":
                return _Resp("text/html", b"<html>not found</html>")
            return _Resp("video/mp2t", b"\x47" + b"\x00" * 187)

    class _Stub:
        pass

    stub = _Stub()
    probe = _RecordingMixin._probe_ts_candidates

    stub.client = type("C", (), {})()
    stub.client.session = _Sess("timeout")
    assert probe(stub, ["http://x/a.ts", "http://x/b.ts"]) == (None, False)

    stub.client.session = _Sess("html")
    assert probe(stub, ["http://x/a.ts"]) == (None, True)

    stub.client.session = _Sess("ok")
    assert probe(stub, ["http://x/a.ts"]) == ("http://x/a.ts", False)


# -- the whole UI builds in every language ------------------------------------

_I18N_CHILD = r"""
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
QDialog.exec = lambda self: 0   # build fully, never block
for code in LANGUAGES:
    set_language(code)
    w.retranslate_ui()
    w.open_settings()
    app.processEvents()
# flush: the child can abort in Qt's C++ teardown AFTER this line, and a
# buffered (piped) stdout would lose the marker in that crash.
print("I18N_UI_OK", flush=True)
"""


def test_ui_builds_in_all_languages():
    """Construct the main window once, then cycle through every language:
    retranslate the chrome and build the full settings dialog (all tabs, all
    strings). Catches the class of bug that only crashes in one language.

    Runs in a subprocess (see test_multiview): the MainWindow owns an
    offscreen QOpenGLWidget, and building one in the shared pytest process
    can abort the whole run at Qt teardown (segfaulted CI on c0f430a). We
    assert on the child's success marker and ignore its exit status."""
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    import os
    import subprocess
    import sys
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    # One retry, same as test_epg_grid: the child can be killed by the
    # nondeterministic Qt/GL teardown abort (-11 with EMPTY stderr) before its
    # marker reaches the pipe. A genuine language regression fails both
    # attempts identically, with a traceback in stderr.
    for _attempt in (1, 2):
        proc = subprocess.run(
            [sys.executable, "-c", _I18N_CHILD], capture_output=True,
            text=True, env=env, cwd=_REPO_ROOT, timeout=180)
        if "I18N_UI_OK" in proc.stdout:
            return
    assert "I18N_UI_OK" in proc.stdout, (
        f"language UI build failed\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr[-2000:]!r}")
