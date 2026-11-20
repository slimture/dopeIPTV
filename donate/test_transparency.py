"""Privacy, behind the scenes (/transparency/): the words of every language
against the code they show (site/transparency.json, tools/transparency.py).

- every placeholder in the words has something behind it, and every block
  of code, test list and host group the data holds is shown in English;
- each language has the same placeholders as the English, so no language
  leaves out a proof;
- each part's copy button has its question, and its proof is folded;
- every address on the page points at the version it names - a tag or a
  commit, never a branch that moves;
- what the page shows of this site's own files is still there to be shown
  (tools/transparency.py's CODE), and none of it is a server's
  configuration: the page says what the servers keep, not how they are
  set up.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REPO = "https://github.com/slimture/dopeIPTV/blob/"
PLACEHOLDER = re.compile(r"<!--tp:(code|ex|tests|copy|hosts) ([a-z]+)(?: ([a-z]+))?-->")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import transparency  # noqa: E402


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


def _site_blocks():
    for part, blocks in transparency.CODE.items():
        for blk in blocks:
            for b in [blk] + blk.get("with", []):
                if b["repo"] == "site":
                    yield part, blk["id"], b


def test_what_the_page_shows_of_the_site_is_still_there():
    # A function renamed or a line reworded in donate/ fails here, not on
    # the next run of the tool.
    seen = 0
    for part, bid, b in _site_blocks():
        try:
            parts = transparency.excerpt(ROOT, "HEAD", b)
        except SystemExit as e:
            raise AssertionError(f"{part} {bid}: {e}") from None
        assert parts and all(p["code"].strip() for p in parts), (part, bid, b["path"])
        seen += 1
    assert seen


def test_the_page_shows_no_servers_configuration():
    # How the servers are set up is not the visitor's privacy and helps no
    # one but an attacker: their files are never shown, a question about
    # them is answered on request.
    for _part, bid, b in _site_blocks():
        assert not b["path"].startswith("deploy/") and b["path"] != "DEPLOY.md", (bid, b["path"])
        assert "nginx" not in b, bid
    for lang, text in _words().items():
        assert "DEPLOY.md" not in text and "/deploy/" not in text, lang


def test_every_log_on_the_server_is_named_on_the_page_and_in_the_policy():
    # Each log the site's newsyslog file turns over (DEPLOY.md), and nginx's
    # error log, in part 7 and the privacy policy of both languages - with
    # what it holds and how long, beside it.
    with open(os.path.join(ROOT, "DEPLOY.md"), encoding="utf-8") as fh:
        deploy = fh.read()
    # The newest round's file, whole: what the server writes now.
    whole = re.findall(r"<<'LOGS'\n(.*?)\nLOGS\n", deploy, re.S)[-1]
    names = {os.path.basename(line.split()[0]) for line in whole.splitlines()} | {"error.log"}
    assert names == {"iptv.dope.rs-dl-counts.log", "iptv-sync.log", "iptv-donate.log", "error.log"}
    words = _words()
    for lang in ("en", "sv"):
        page = words[lang]
        start = page.index('<h2 id="webservers">')
        end = page.find('<h2 ', start + 1)
        part = page[start:end if end > 0 else len(page)]
        with open(os.path.join(ROOT, "site", "legal", lang, "privacy.html"), encoding="utf-8") as fh:
            policy = fh.read()
        for name in names:
            assert f"<code>{name}</code>" in part, (lang, name)
            assert f"<code>{name}</code>" in policy, (lang, name)
        # The try-it list sends nobody to the admin page's sign-in.
        for box in re.findall(r'<pre class="tp-cmd"><code>(.*?)</code></pre>', part, re.S):
            assert "/admin" not in box, lang
