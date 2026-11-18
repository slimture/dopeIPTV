"""The site's stylesheet against the pages it styles, without a browser.

Two faults this has caught: the support band's section was renamed from
#donate to #support and three rules went on styling an id no page had (the
band lost its colours on a phone), and "Built on" (#built) was pulled up
over the edge of whatever came before it whenever the band was not there -
a negative margin meant for sitting under the band. So:

- every #id a selector names is an id some page, template or script makes;
- #built has no negative margin of its own, only right after the band.
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _read(*parts: str) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def _css() -> str:
    return re.sub(r"/\*.*?\*/", "", _read("public", "style.css"), flags=re.S)


def _rules(css: str):
    """(selector, declarations) of every rule, inside @media too."""
    out, heads, start = [], [], 0
    for i, ch in enumerate(css):
        if ch == "{":
            heads.append(css[start:i].strip())
            start = i + 1
        elif ch == "}":
            head = heads.pop() if heads else ""
            if not head.startswith("@"):
                out.append((head, css[start:i]))
            start = i + 1
    return out


def _made_ids() -> set:
    texts = []
    for d in (("site", "templates"), ("site", "legal", "en"), ("site", "legal", "sv"), ("public",)):
        folder = os.path.join(ROOT, *d)
        for name in os.listdir(folder):
            if name.endswith((".html", ".js")):
                texts.append(_read(*d, name))
    texts.append(_read("site", "render.jq"))
    ids = set()
    for t in texts:
        ids |= set(re.findall(r'\bid=\\?"([A-Za-z][\w-]*)', t))          # id="x", id=\"x\" in jq
        ids |= set(re.findall(r'\.id\s*=\s*"([A-Za-z][\w-]*)"', t))       # el.id = "x"
        ids |= set(re.findall(r'\bid:\s*"([A-Za-z][\w-]*)"', t))
    return ids


def test_every_id_a_rule_names_is_made_somewhere():
    made = _made_ids()
    named = set()
    for sel, _ in _rules(_css()):
        named |= set(re.findall(r"#([A-Za-z][\w-]*)", sel))
    missing = sorted(named - made)
    assert not missing, f"style.css styles ids no page makes: {missing}"


def test_built_is_pulled_up_only_right_after_the_band():
    for sel, body in _rules(_css()):
        parts = [s.strip() for s in sel.split(",")]
        for s in parts:
            if not re.search(r"#built\b", s) or "#built " in s or s.endswith(("#built::after", "#built::before")):
                continue
            neg = re.search(r"margin(-top)?\s*:\s*-", body)
            if neg:
                assert "#support + #built" in s, (
                    f"'{s}' pulls #built up with a negative margin: only right after "
                    "the band (#support + #built) - without it the row lands on the edge "
                    "of the section before")
