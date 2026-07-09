"""Smoke tests: verify every module imports without error."""

import ast
import importlib
import importlib.util
import pathlib

import pytest

import dopeiptv


MODULES = [
    "dopeiptv",
    "dopeiptv.client",
    "dopeiptv.epg",
    "dopeiptv.stores",
    "dopeiptv.workers",
    "dopeiptv.recording",
    "dopeiptv.theme",
    "dopeiptv.wakelock",
    "dopeiptv.metadata",
    "dopeiptv.trakt",
]

MODULES_NEED_DISPLAY = [
    "dopeiptv.app",
    "dopeiptv.channel_list",
    "dopeiptv.chromecast",
    "dopeiptv.dialogs",
    "dopeiptv.embedded",
    "dopeiptv.ui.main_window",
    "dopeiptv.players",
]


@pytest.mark.parametrize("mod", MODULES)
def test_import(mod):
    importlib.import_module(mod)


@pytest.mark.parametrize("mod", MODULES_NEED_DISPLAY)
def test_import_gui(mod):
    try:
        importlib.import_module(mod)
    except ImportError:
        pytest.skip("display or Qt not available")


def _relative_import_targets():
    """Yield (source_module, target_module) for every relative import in the
    package - including ones nested inside function bodies, which the plain
    import tests above never execute."""
    root = pathlib.Path(dopeiptv.__file__).resolve().parent
    for py in sorted(root.rglob("*.py")):
        rel = py.relative_to(root.parent).with_suffix("")
        modname = ".".join(rel.parts)
        is_pkg = py.name == "__init__.py"
        if is_pkg:
            modname = modname[: -len(".__init__")]
        # Relative imports are resolved against the containing package: the
        # package itself for an __init__, else the module's parent package.
        anchor = modname if is_pkg else (
            modname.rsplit(".", 1)[0] if "." in modname else modname)
        tree = ast.parse(py.read_text(), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                name = "." * node.level + (node.module or "")
                target = importlib.util.resolve_name(name, anchor)
                yield modname, target


def test_relative_imports_resolve():
    """Every `from .x`/`from ..x` in the package must point at a real module -
    at module level AND inside methods. Catches the classic 'moved a file but
    an inline import still says .foo instead of ..foo' bug."""
    # _tmdb_key is git-ignored and only baked into release builds by CI; it is
    # imported inside a try/except, so a missing target there is expected.
    optional = {"dopeiptv._tmdb_key"}
    broken = []
    for src, target in _relative_import_targets():
        if target in optional:
            continue
        try:
            if importlib.util.find_spec(target) is None:
                broken.append(f"{src} -> {target}")
        except ModuleNotFoundError:
            broken.append(f"{src} -> {target}")
    assert not broken, "unresolved relative imports:\n  " + "\n  ".join(broken)
