#!/usr/bin/env python3
"""The privacy policy and the transparency page say what the code does
(site/legal/*/privacy.html, site/transparency/*.html against
stats_collect.py and DEPLOY.md's newest round): no sentence of how things
were before, and the numbers the code keeps.

Plain Python: ``python3 donate/test_policy_text.py`` runs them all (pytest
finds them too).
"""

from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

#: Sentences of before - each once true, none any more.
OBSOLETE = {
    "en": [
        "statistics' two server logs",               # the statistics write no log since the socket
        "restart of the service forgets it",         # a tmpfs keeps the key through one
        "exists only in the service's memory – never in a file",
        "or at once when the service starts again",
        "until we clean it out",                     # the error logs are turned over
        "not turned over by themselves",
        "Neither is turned over by itself",
    ],
    "sv": [
        "statistikens två serverloggar",
        "omstart av tjänsten glömmer den",
        "finns bara i tjänstens minne – aldrig i en fil",
        "eller direkt när tjänsten startar om",
        "tills vi rensar bort den",
        "byts inte av sig själva",
        "Ingen av dem byts av sig själv",
    ],
}


def _pages(lang: str) -> dict:
    out = {}
    for name in (("legal", lang, "privacy.html"), ("transparency", f"{lang}.html")):
        with open(os.path.join(ROOT, "site", *name), encoding="utf-8") as fh:
            out["/".join(name)] = fh.read()
    return out


def test_no_sentence_of_before_is_left():
    for lang, sentences in OBSOLETE.items():
        for page, text in _pages(lang).items():
            for words in sentences:
                assert words not in text, (page, words)


def test_the_numbers_are_the_codes():
    with open(os.path.join(HERE, "stats_collect.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert re.search(r"^VISITS_DAYS_MAX = 14$", src, re.M)
    assert re.search(r"^KEPT_MONTHS = 2\b", src, re.M)
    say = {"en": ("14 days", "two months", "a week at the most"),
           "sv": ("14 dagar", "två månader", "högst en vecka")}
    for lang, words in say.items():
        policy = _pages(lang)["legal/" + lang + "/privacy.html"]
        for w in words:
            assert w in policy, (lang, w)
    # A week at the most: DEPLOY.md's newest newsyslog file keeps six days
    # of the content server's error log, the proxy's the same.
    with open(os.path.join(ROOT, "DEPLOY.md"), encoding="utf-8") as fh:
        deploy = fh.read()
    whole = re.findall(r"<<'LOGS'\n(.*?)\nLOGS\n", deploy, re.S)[-1]
    proxy = re.findall(r"<<'PROXYLOGS'\n(.*?)\nPROXYLOGS\n", deploy, re.S)[-1]
    for block in (whole, proxy):
        (line,) = [x for x in block.splitlines() if x.startswith("/var/log/nginx/error.log ")]
        assert line.split()[2:6] == ["600", "6", "*", "@T00"], line
        assert line.split()[-2:] == ["/var/run/nginx.pid", "30"], "nginx reopens it"


def test_the_key_is_said_to_be_in_memory_and_swap_encrypted():
    for lang, words in {"en": ("tmpfs", "swap", "restart of the computer"),
                        "sv": ("tmpfs", "swap", "omstart av datorn")}.items():
        policy = _pages(lang)["legal/" + lang + "/privacy.html"]
        for w in words:
            assert w in policy, (lang, w)
        # The page says it in plain words; how it is done is in its code.
        page = _pages(lang)["transparency/" + lang + ".html"]
        assert {"en": "The key is only in memory", "sv": "Nyckeln finns bara i minnet"}[lang] in page, lang
    with open(os.path.join(ROOT, "site", "transparency.json"), encoding="utf-8") as fh:
        shown = "\n".join(p["code"] for blk in json.load(fh)["code"]["website"] for p in blk["parts"])
    for name in ("def swap_state", "VISITS_DAYS_MAX", "def left_behind", "def sweep_left"):
        assert name in shown, name


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"{len(tests)} passed")
