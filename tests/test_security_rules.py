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


def _modules() -> list[tuple[str, ast.Module]]:
    """(file, its tree) of every module in the package."""
    out = []
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG).as_posix()
        out.append((rel, ast.parse(path.read_text(encoding="utf-8"), rel)))
    return out


def _dotted(f: ast.AST) -> str:
    if isinstance(f, ast.Attribute):
        inner = _dotted(f.value)
        return f"{inner}.{f.attr}" if inner else f.attr
    return f.id if isinstance(f, ast.Name) else ""


def _calls_in(tree: ast.AST, fn: ast.AST | None = None):
    """Every call in *tree*: (its dotted name, the function it is in, the call)."""
    for child in ast.iter_child_nodes(tree):
        inner = child if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
        if isinstance(child, ast.Call):
            yield _dotted(child.func), inner, child
        yield from _calls_in(child, inner)


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


# -- rule 5: XML from outside, without a DTD of its own -------------------------------

#: What parses XML. Every one reads somebody else's document.
XML_PARSERS = {"fromstring", "XML", "iterparse", "XMLPullParser", "XMLParser", "ParserCreate",
               "parseString", "fromstringlist"}
#: Where one may be called: (module, function) -> the check that runs first
#: in that function ("" for the check itself), and why it is the place.
XML_ALLOWED = {
    ("core/safe_xml.py", "fromstring"): ("refuse_dtd", "the shared check's own parse"),
    ("core/safe_xml.py", "__init__"): ("", "the check itself: expat reading the prolog"),
    ("providers/boxes.py", "parse_xml"): ("refuse_dtd", "a box's answer, its DTD dropped first"),
    ("providers/epg.py", "parse_xmltv"): ("refuse_entities", "a guide, checked as it is parsed"),
    ("services/share.py", "lineup_guide"): ("Prolog", "a cached guide, checked as it streams"),
    ("core/svg_safe.py", "_written_again"): ("", "an SVG written out again from what expat "
                                                 "read; its own EntityDeclHandler refuses an "
                                                 "entity that names another or is long"),
}
#: Where "<!ENTITY" or "<!DOCTYPE" may be looked for in the bytes, and why.
ENTITY_BYTES_OK = {
    "core/safe_xml.py": "drop_dtd finds where a box's DOCTYPE starts; refuse_dtd decides",
    "core/svg_safe.py": "a fast path taken only for a plain ASCII-compatible encoding; "
                        "anything else is read by expat",
}


def test_every_xml_parse_of_outside_data_takes_the_safe_check():
    """Rule 5: XML is parsed only after core/safe_xml.py's check (no DTD of
    its own) - through safe_xml.fromstring, boxes.parse_xml or the places
    listed above, each calling the check in the same function."""
    found = []
    for rel, tree in _modules():
        for name, fn, call in _calls_in(tree):
            if name.rsplit(".", 1)[-1] not in XML_PARSERS or name.startswith("safe_xml."):
                continue
            where = (rel, getattr(fn, "name", ""))
            if where not in XML_ALLOWED:
                found.append(f"{rel}:{call.lineno} {name}() in {where[1] or 'the module'}")
                continue
            guard = XML_ALLOWED[where][0]
            if guard and not any(n.rsplit(".", 1)[-1] == guard for n, _f, _c in _calls_in(fn)):
                found.append(f"{rel}:{call.lineno} {name}() without {guard}() first")
    assert not found, "XML parsed without core/safe_xml.py's check:\n" + "\n".join(found)


def test_a_source_s_id_names_a_file_only_through_file_part():
    """Rule 5: an id comes from backups and sync too; a file's name made of
    one takes core/source_files.file_part, never the id as it came."""
    ids = {"pid", "playlist_id", "source_id", "sid"}
    exts = (".json", ".xml", ".pkl", ".gz", ".m3u", ".db", ".d")
    found = []
    for rel, tree in _modules():
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            parts = node.values
            for i, v in enumerate(parts):
                if not isinstance(v, ast.FormattedValue):
                    continue
                inner = v.value.values[0] if isinstance(v.value, ast.BoolOp) else v.value
                if not (isinstance(inner, ast.Name) and inner.id in ids):
                    continue
                after = parts[i + 1] if i + 1 < len(parts) else None
                if isinstance(after, ast.Constant) and str(after.value).startswith(exts):
                    found.append(f"{rel}:{node.lineno}")
    assert not found, found


def test_no_module_decides_on_entities_from_the_bytes():
    """Rule 5: a look at the bytes for <!ENTITY or <!DOCTYPE misses UTF-16
    and a declared UTF-7 - the check is expat's (core/safe_xml.py)."""
    found = []
    for rel, tree in _modules():
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, (bytes, str)):
                v = node.value if isinstance(node.value, str) else node.value.decode("latin-1")
                if v in ("<!ENTITY", "<!DOCTYPE") and rel not in ENTITY_BYTES_OK:
                    found.append(f"{rel}:{node.lineno}")
    assert not found, found
