"""libmpv makes no core under a numeric locale but "C": mpv_create() says
"Non-C locale detected" and hands back NULL, and python-mpv passes that
NULL on to mpv_set_option_string - a segfault (crash report a8f41ba99eed:
Nerd's check of the mpv options, made on a worker before the player had a
core, on a system in Swedish). Every core is made after
media.players.numeric_c, and the start puts "C" back on the window's thread
right after QApplication has set the locale from the environment."""
from __future__ import annotations

import ast
import locale
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from dopeiptv.media import players
from dopeiptv.media import video_surface as vs

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "dopeiptv"


@pytest.fixture
def kept_locale():
    was = locale.setlocale(locale.LC_NUMERIC)
    yield
    locale.setlocale(locale.LC_NUMERIC, was)


def _usable(name: str) -> bool:
    was = locale.setlocale(locale.LC_NUMERIC)
    try:
        locale.setlocale(locale.LC_NUMERIC, name)
        return locale.setlocale(locale.LC_NUMERIC) != "C"
    except locale.Error:
        return False
    finally:
        locale.setlocale(locale.LC_NUMERIC, was)


def _comma_locale(tmp: pathlib.Path) -> tuple[str, dict[str, str]] | None:
    """A locale with a decimal comma, as the person's was: one the system
    has, else one built into *tmp* (localedef), else None."""
    names = ("sv_SE.UTF-8", "sv_SE.utf8", "de_DE.UTF-8", "de_DE.utf8", "fr_FR.UTF-8",
             "fr_FR.utf8", "nl_NL.UTF-8", "ru_RU.UTF-8")
    for name in names:
        if _usable(name):
            return name, {}
    if shutil.which("localedef") and os.path.isdir("/usr/share/i18n/locales"):
        out = tmp / "locales"
        out.mkdir()
        made = subprocess.run(["localedef", "-i", "sv_SE", "-f", "UTF-8", str(out / "sv_SE.UTF-8")],
                              capture_output=True, timeout=120)
        if made.returncode in (0, 1) and (out / "sv_SE.UTF-8").exists():
            return "sv_SE.UTF-8", {"LOCPATH": str(out)}
    return None


def test_numeric_c_puts_c_back(kept_locale):
    other = next((n for n in ("C.UTF-8", "C.utf8", "sv_SE.UTF-8", "de_DE.UTF-8") if _usable(n)), None)
    if other is None:
        pytest.skip("no locale but C on this system")
    locale.setlocale(locale.LC_NUMERIC, other)
    assert players.numeric_c() is True
    assert locale.setlocale(locale.LC_NUMERIC) == "C"
    assert players.numeric_c() is True             # already "C": nothing written


def test_no_core_where_c_cannot_be_set(monkeypatch):
    made = []

    class FakeMPV:
        def __init__(self, **opts):
            made.append(opts)

    monkeypatch.setattr(players, "_libmpv", type("M", (), {"MPV": FakeMPV}))
    monkeypatch.setattr(players, "numeric_c", lambda: False)
    assert players.throwaway_mpv() is None
    monkeypatch.setattr(vs, "_libmpv", type("M", (), {"MPV": FakeMPV}))
    monkeypatch.setattr(vs, "numeric_c", lambda: False)
    with pytest.raises(RuntimeError):
        vs.build_core(None)
    assert made == []                              # never mpv_create under it


def test_numeric_c_says_no_when_the_locale_refuses(monkeypatch):
    def refuse(*_a):
        raise locale.Error("unsupported locale setting")
    monkeypatch.setattr(locale, "setlocale", refuse)
    assert players.numeric_c() is False


def _calls(fn: ast.AST) -> list[str]:
    """The names *fn* calls, in the order they stand in its source."""
    calls = sorted((n for n in ast.walk(fn) if isinstance(n, ast.Call)),
                   key=lambda n: (n.lineno, n.col_offset))
    return [n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "") for n in calls]


def test_every_core_is_made_after_numeric_c():
    """Only two places make an mpv core, and each asks numeric_c first."""
    makers = []
    for path in PKG.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                direct = [n for n in ast.walk(fn)
                          if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                          and n.func.attr == "MPV"]
                if direct:
                    makers.append((path.relative_to(PKG).as_posix(), fn.name))
    assert sorted(set(makers)) == [("media/players.py", "throwaway_mpv"),
                                   ("media/video_surface.py", "_create_core")]
    tree = ast.parse((PKG / "media/players.py").read_text(encoding="utf-8"))
    fn = next(f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and f.name == "throwaway_mpv")
    assert _calls(fn).index("numeric_c") < _calls(fn).index("MPV")
    tree = ast.parse((PKG / "media/video_surface.py").read_text(encoding="utf-8"))
    fn = next(f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and f.name == "build_core")
    assert _calls(fn).index("numeric_c") < _calls(fn).index("_create_core")
    callers = [p.relative_to(PKG).as_posix() for p in PKG.rglob("*.py")
               if "_create_core(" in p.read_text(encoding="utf-8")]
    assert callers == ["media/video_surface.py"]


def test_the_start_puts_c_back_right_after_qapplication():
    src = (PKG / "app.py").read_text(encoding="utf-8")
    made = src.index("app = QApplication(")
    assert src.index("numeric_c()", made) - made < 600


_REAL = r"""
import faulthandler, locale, sys
faulthandler.enable()
from dopeiptv.media import players
if players._libmpv is None:
    print("NO_LIBMPV"); sys.exit(0)
locale.setlocale(locale.LC_NUMERIC, sys.argv[1])
assert locale.localeconv()["decimal_point"] == ","
core = players.throwaway_mpv()
assert core is not None
assert locale.setlocale(locale.LC_NUMERIC) == "C"
core["deband"] = "yes"
core.terminate()
print("CORE_OK")
"""


def test_a_real_core_under_a_decimal_comma(tmp_path):
    """The crash itself: a core made while LC_NUMERIC is a Swedish one.
    Before numeric_c this ended in a segfault (exit -11)."""
    pytest.importorskip("mpv")
    if players._libmpv is None:
        pytest.skip("libmpv not available")
    found = _comma_locale(tmp_path)
    if found is None:
        pytest.skip("no locale with a decimal comma here")
    name, env_extra = found
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(ROOT), **env_extra)
    out = subprocess.run([sys.executable, "-c", _REAL, name], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and "CORE_OK" in out.stdout, (out.returncode, out.stdout, out.stderr[-2000:])
