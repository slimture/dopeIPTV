#!/usr/bin/env python3
"""Report the health of every shipped UI translation.

English is the source language (inline in ``dopeiptv/i18n.py``); every other
language ships as ``dopeiptv/locale/<code>.json``. This prints one row per
locale file — how many of the base keys it covers, how many are still missing,
any keys it carries that no longer exist in the source, whether every
``{placeholder}`` matches the English string, and whether the language is
complete enough to appear in the in-app picker.

Use it to see at a glance which languages a native speaker could improve, and
to sanity-check a locale after editing it.

Usage::

    python tools/i18n_status.py            # table for every locale
    python tools/i18n_status.py de         # detail for one language:
                                           # its missing keys + placeholder
                                           # mismatches, with the English source
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dopeiptv.i18n import (  # noqa: E402
    LANGUAGES, _LOCALE_READY_RATIO, RTL_LANGUAGES, base_string_keys, english,
)

_LOCALE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "dopeiptv", "locale")
_PLACEHOLDER = re.compile(r"\{[a-zA-Z_]+\}")


def _locale_codes() -> list[str]:
    if not os.path.isdir(_LOCALE_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(_LOCALE_DIR)
                  if f.endswith(".json"))


def _load(code: str) -> dict[str, str]:
    with open(os.path.join(_LOCALE_DIR, f"{code}.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _placeholder_mismatches(data: dict[str, str], keys: list[str]) -> list[str]:
    bad = []
    for k in keys:
        if k not in data:
            continue
        if set(_PLACEHOLDER.findall(english(k))) != set(
                _PLACEHOLDER.findall(data[k])):
            bad.append(k)
    return bad


def _detail(code: str, keys: list[str]) -> int:
    try:
        data = _load(code)
    except FileNotFoundError:
        print(f"No locale file for '{code}'.")
        return 1
    keyset = set(keys)
    missing = [k for k in keys if k not in data]
    extra = sorted(set(data) - keyset)
    mism = _placeholder_mismatches(data, keys)
    print(f"# {code}  ({len(data)}/{len(keys)} keys, "
          f"{'RTL' if code in RTL_LANGUAGES else 'LTR'})\n")
    if missing:
        print(f"Missing {len(missing)} key(s) — English source shown:")
        for k in missing:
            print(f"  {k}: {english(k)!r}")
        print()
    if extra:
        print(f"Stray {len(extra)} key(s) no longer in the source: "
              f"{', '.join(extra)}\n")
    if mism:
        print(f"Placeholder mismatch in {len(mism)} key(s) "
              "(the {name} tokens must match English):")
        for k in mism:
            print(f"  {k}\n    en: {english(k)!r}\n    {code}: {data[k]!r}")
        print()
    if not (missing or extra or mism):
        print("All good — full coverage, no stray keys, placeholders match.")
    return 0


def _table(keys: list[str]) -> int:
    ratio_pct = int(_LOCALE_READY_RATIO * 100)
    print(f"{'lang':<6}{'coverage':>12}{'missing':>9}{'stray':>7}"
          f"{'ph✗':>6}{'picker':>8}  status")
    print("-" * 60)
    any_issue = False
    for code in _locale_codes():
        data = _load(code)
        have = sum(1 for k in keys if k in data)
        missing = len(keys) - have
        extra = len(set(data) - set(keys))
        mism = len(_placeholder_mismatches(data, keys))
        pct = 100 * have // len(keys)
        in_picker = "yes" if code in LANGUAGES else "no"
        flags = []
        if missing:
            flags.append("incomplete")
        if extra:
            flags.append("stray-keys")
        if mism:
            flags.append("placeholder")
        if code not in LANGUAGES:
            flags.append(f"<{ratio_pct}%-hidden")
        status = ", ".join(flags) if flags else "ok"
        if flags:
            any_issue = True
        print(f"{code:<6}{f'{have}/{len(keys)} ({pct}%)':>12}{missing:>9}"
              f"{extra:>7}{mism:>6}{in_picker:>8}  {status}")
    print("-" * 60)
    print(f"{len(_locale_codes())} locale files + English source = "
          f"{len(LANGUAGES)} languages in the picker.")
    print("Run `python tools/i18n_status.py <code>` for a language's details.")
    return 1 if any_issue else 0


def main() -> int:
    keys = base_string_keys()
    if len(sys.argv) > 1:
        return _detail(sys.argv[1], keys)
    return _table(keys)


if __name__ == "__main__":
    raise SystemExit(main())
