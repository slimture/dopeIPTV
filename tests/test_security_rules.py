"""Rules of AGENTS.md's "Security and privacy rules" that read the whole code
base: each fails when a change breaks it somewhere new. An exception is
listed beside its rule with its reason."""
from __future__ import annotations

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "dopeiptv"


def _calls(name: str):
    """(file, line, call) of every call of *name* (a bare name or an
    attribute) in the package."""
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            called = fn.id if isinstance(fn, ast.Name) else (
                fn.attr if isinstance(fn, ast.Attribute) else "")
            if called == name:
                yield path.relative_to(PKG).as_posix(), node.lineno, node


# -- a kids profile: a category for adults by the provider's own name too ------------

#: Where a category's name is the provider's own already (server mode's
#: lineup and the share read the client's categories, never a name given
#: in the app).
PROVIDER_NAMES_ONLY = {"services/share.py", "services/home_server.py"}


def test_a_category_for_adults_is_judged_by_the_providers_name_too():
    """A name given in the app ("XXX" -> "Fun") never takes a category out
    of a kids profile's limits: every test of a category's name passes the
    provider's own beside it (core/profiles.adult_category's *more*)."""
    found = []
    for rel, line, call in _calls("adult_category"):
        if rel in PROVIDER_NAMES_ONLY:
            continue
        if len(call.args) < 2:
            found.append(f"{rel}:{line}")
    assert not found, "adult_category without the provider's name: " + ", ".join(found)
    assert any(rel == "ui/mw_lists.py" for rel, _l, _c in _calls("adult_category"))


# -- the browser and the file manager: through core/xdg.py alone ---------------------

def test_links_and_folders_are_opened_through_core_xdg_alone():
    """open_url opens https (and server mode's page on this computer) only,
    open_folder a folder: nothing else hands an address to the desktop
    (QDesktopServices.openUrl, webbrowser, xdg-open), and no file address
    is made for open_url (a folder is open_folder's)."""
    found = [f"{rel}:{line}" for rel, line, _c in _calls("openUrl") if rel != "core/xdg.py"]
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG).as_posix()
        text = path.read_text(encoding="utf-8")
        if rel != "core/xdg.py" and ("import webbrowser" in text or '"xdg-open"' in text):
            found.append(rel)
    for rel, line, call in _calls("open_url"):
        if any(isinstance(n, ast.Attribute) and n.attr == "as_uri" for n in ast.walk(call)):
            found.append(f"{rel}:{line} (a file address)")
    assert not found, "opened around core/xdg.py: " + ", ".join(found)
