#!/usr/bin/env python3
"""Generate / refresh a locale template for a new UI language.

Every user-visible string in dopeIPTV goes through ``tr("<key>")`` and the
English source lives in ``dopeiptv/i18n.py``. Additional languages ship as
``dopeiptv/locale/<code>.json`` ({key: "translation"}); i18n.py merges them at
import and English covers any key a locale omits.

Usage::

    # write dopeiptv/locale/tr.template.json with every key + its English text
    python tools/i18n_locale_template.py tr

    # show only the keys still MISSING from an existing locale/tr.json
    python tools/i18n_locale_template.py tr --missing

The workflow: run this to get the full key set with the English reference,
translate the values, save as ``dopeiptv/locale/<code>.json``. A locale joins
the in-app language picker once it covers ~90 % of the keys (see i18n.py).
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dopeiptv.i18n import base_string_keys, english  # noqa: E402

_LOCALE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "dopeiptv", "locale")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    code = sys.argv[1]
    missing_only = "--missing" in sys.argv[2:]

    existing: dict = {}
    path = os.path.join(_LOCALE_DIR, f"{code}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            existing = json.load(fh)

    keys = base_string_keys()
    if missing_only:
        out = {k: english(k) for k in keys if k not in existing}
        print(f"{len(out)} of {len(keys)} keys still missing from {code}.json")
    else:
        # Keep any existing translations, fill the rest with the English
        # reference so the file is complete and easy to translate in place.
        out = {k: existing.get(k) or english(k) for k in keys}

    dest = os.path.join(_LOCALE_DIR, f"{code}.template.json")
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(f"wrote {dest} ({len(out)} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
