"""Offline category flags, independent of the platform's emoji fonts."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re

from PyQt6.QtGui import QIcon

_FLAGS = Path(__file__).resolve().parents[1] / "assets" / "flags"
_REGIONAL_PAIR = re.compile(r"[\U0001f1e6-\U0001f1ff]{2}")
_PREFIX = re.compile(r"^[\s\[({|:★☆•-]*([A-Z]{2,3})(?=$|[\s\])}|:/_-])")
_ALIASES = {
    "UK": "gb", "USA": "us", "ITA": "it", "GER": "de", "DEU": "de",
    "FRA": "fr", "SPA": "es", "ESP": "es", "POR": "pt", "PRT": "pt",
    "ENG": "gb-eng", "SCO": "gb-sct", "WAL": "gb-wls",
    "ITALIA": "it", "ITALY": "it", "FRANCE": "fr", "GERMANY": "de",
    "DEUTSCHLAND": "de", "SPAIN": "es", "ESPAÑA": "es", "PORTUGAL": "pt",
}


def category_flag(text: str) -> tuple[str | None, str]:
    """Return a bundled flag and display text; never alter the provider data."""
    pair = _REGIONAL_PAIR.search(text)
    if pair:
        code = "".join(chr(ord(c) - 0x1F1E6 + ord("a")) for c in pair.group())
        if (_FLAGS / f"{code}.svg").is_file():
            label = (text[:pair.start()] + text[pair.end():]).strip()
            return code, label or text
    prefix = _PREFIX.match(text)
    token = prefix.group(1) if prefix else text.split(" ", 1)[0].upper()
    code = _ALIASES.get(token, token.lower() if prefix else "")
    if code and (_FLAGS / f"{code}.svg").is_file():
        return code, text
    return None, text


@lru_cache(maxsize=300)
def flag_icon(code: str) -> QIcon:
    return QIcon(str(_FLAGS / f"{code}.svg"))
