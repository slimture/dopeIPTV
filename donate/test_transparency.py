"""Privacy, behind the scenes (/transparency/): the words of every language
against the code they show (site/transparency.json, tools/transparency.py).

- every placeholder in the words has something behind it, and every block
  of code, test list and host group the data holds is shown in English;
- each language has the same placeholders as the English, so no language
  leaves out a proof;
- each part's copy button has its question, and its proof is folded;
- every address on the page points at the version it names - a tag or a
  commit, never a branch that moves.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REPO = "https://github.com/slimture/dopeIPTV/blob/"
PLACEHOLDER = re.compile(r"<!--tp:(code|ex|tests|copy|hosts) ([a-z]+)(?: ([a-z]+))?-->")


def _data() -> dict:
    with open(os.path.join(ROOT, "site", "transparency.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _words() -> dict:
    folder = os.path.join(ROOT, "site", "transparency")
    out = {}
    for name in sorted(os.listdir(folder)):
        if name.endswith(".html"):
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                out[name[:-5]] = fh.read()
    return out


def test_every_placeholder_has_its_code_and_every_proof_is_shown():
    data, words = _data(), _words()
    assert "en" in words
    for lang, text in words.items():
        for kind, a, b in PLACEHOLDER.findall(text):
            where = f"{lang}: <!--tp:{kind} {a} {b}-->"
            if kind == "code":
                assert any(blk["id"] == b for blk in data["code"].get(a, [])), where
            elif kind == "ex":
                assert b in data["examples"].get(a, {}), where
            elif kind == "tests":
                assert data["tests"].get(a), where
            elif kind == "hosts":
                assert any(h["kind"] == a and h["group"] == b for h in data["hosts"]), where
    shown = set(PLACEHOLDER.findall(words["en"]))
    for part, blocks in data["code"].items():
        for blk in blocks:
            assert ("code", part, blk["id"]) in shown, f"{part} {blk['id']} is not on the page"
    for part, examples in data["examples"].items():
        for key in examples:
            assert ("ex", part, key) in shown, f"example {part} {key} is not on the page"
    for h in data["hosts"]:
        assert ("hosts", h["kind"], h["group"]) in shown, f"{h['host']} is not on the page"


def test_every_language_shows_every_proof_the_english_does():
    words = _words()
    english = sorted(PLACEHOLDER.findall(words["en"]))
    for lang, text in words.items():
        assert sorted(PLACEHOLDER.findall(text)) == english, lang
        assert re.findall(r'<h2 id="([a-z]+)">', text) == re.findall(r'<h2 id="([a-z]+)">', words["en"]), lang


def test_each_copy_button_has_its_question_and_each_proof_is_folded():
    for lang, text in _words().items():
        parts = re.findall(r"<!--tp:copy ([a-z]+)-->", text)
        assert parts, lang
        for part in parts:
            assert f'<blockquote class="tp-ask" data-for="{part}">' in text, f"{lang}: {part}"
        proofs = text.split('<details class="tp-proof">')[1:]
        assert len(proofs) == len(parts), lang
        for proof in proofs:
            assert "<!--tp:copy " in proof.split("</details>\n")[-2] or "<!--tp:copy " in proof, lang


def test_every_address_names_its_version():
    data = _data()
    ref, site_ref = data["ref"], data["site_ref"]
    assert ref and site_ref and data["version"]
    assert not re.fullmatch(r"main|testing|website|master", ref)
    urls = [p["url"] for blocks in data["code"].values() for blk in blocks for p in blk["parts"]]
    urls += [t["url"] for tests in data["tests"].values() for t in tests]
    urls += [w["url"] for h in data["hosts"] for w in h["where"]]
    for url in urls:
        assert url.startswith(REPO + ref + "/") or url.startswith(REPO + site_ref + "/"), url
    for blocks in data["code"].values():
        for blk in blocks:
            for part in blk["parts"]:
                assert part["code"].strip() and part["start"] <= part["end"], part["path"]
