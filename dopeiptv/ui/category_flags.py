"""Offline category flags, independent of the platform's emoji fonts."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re
import unicodedata

from PyQt6.QtCore import QLocale
from PyQt6.QtGui import QIcon

_FLAGS = Path(__file__).resolve().parents[1] / "assets" / "flags"
_REGIONAL_PAIR = re.compile(r"[\U0001f1e6-\U0001f1ff]{2}")
# A country code counts only where a separator follows it ("SE | Sport",
# "DE: News", "[UK]"): followed by a word, "AR North Africa" and "TV Shows"
# read as Argentina and Tuvalu.
_PREFIX = re.compile(r"^[\s\[({|:★☆•-]*([A-Z]{2,3})(?=\s*(?:$|[\])}|:/_-]))")
# Codes IPTV providers use for something else more often than for the
# country: Arabic, Africa, Latino, South Africa, TV.
_AMBIGUOUS = {"AR", "AF", "LA", "SA", "TV"}
_ALIASES = {
    "UK": "gb", "USA": "us", "ITA": "it", "GER": "de", "DEU": "de",
    "FRA": "fr", "SPA": "es", "ESP": "es", "POR": "pt", "PRT": "pt",
    "ENG": "gb-eng", "SCO": "gb-sct", "WAL": "gb-wls",
}
# Country names as providers write them that are not Qt's own (English and
# native names come from Qt's locale data, see _country_names).
_NAME_ALIASES = {
    "uk": "gb", "great britain": "gb", "britain": "gb",
    "england": "gb-eng", "scotland": "gb-sct", "wales": "gb-wls",
    "usa": "us", "united states of america": "us", "america": "us",
    "holland": "nl", "the netherlands": "nl",
    "czech republic": "cz", "turkey": "tr", "turkiye": "tr",
    "south korea": "kr", "korea": "kr", "north korea": "kp",
    "russia": "ru", "russian federation": "ru", "iran": "ir",
    "syria": "sy", "vietnam": "vn", "viet nam": "vn", "laos": "la",
    "bosnia": "ba", "bosnia herzegovina": "ba", "macedonia": "mk",
    "ivory coast": "ci", "uae": "ae", "emirates": "ae",
    "dr congo": "cd", "drc": "cd", "cape verde": "cv", "swaziland": "sz",
    "burma": "mm", "vatican": "va", "palestine": "ps",
    "hong kong": "hk", "macau": "mo", "macao": "mo",
}
# Separators between the parts of a category name ("VIP | SWEDEN",
# "Sweden - Sport", "Sweden (HD)").
_SEGMENTS = re.compile(r"\s*[|:/•★☆\[\]()_+]+\s*|\s+[-–—]\s+")
_WORD = re.compile(r"[^\W\d_]+(?:['’.][^\W\d_]+)*")


def _key(text: str) -> str:
    """Words only, case- and accent-insensitive: 'ESPAÑA' and 'España'
    and 'espana' are one key, 'Bosnia & Herzegovina' 'bosnia herzegovina'."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", text)
                    if not unicodedata.combining(c))
    return " ".join(_WORD.findall(plain.casefold()))


@lru_cache(maxsize=1)
def _country_names() -> dict[str, str]:
    """Every country name Qt knows, in English and in the country's own
    languages ("Sweden", "Sverige"; "Switzerland", "Schweiz", "Suisse"),
    mapped to the flag file it names. Built once, from QtCore's locale data -
    a few milliseconds."""
    names: dict[str, str] = {}
    seen: set = set()
    T = QLocale.Country
    for attr in dir(T):
        if attr.startswith("_"):
            continue
        territory = getattr(T, attr)
        if territory in seen:
            continue
        seen.add(territory)
        code = (QLocale.territoryToCode(territory) or "").lower()
        if len(code) != 2 or not (_FLAGS / f"{code}.svg").is_file():
            continue
        spelled = [QLocale.territoryToString(territory)]
        spelled += [loc.nativeTerritoryName() for loc in QLocale.matchingLocales(
            QLocale.Language.AnyLanguage, QLocale.Script.AnyScript, territory)]
        for name in spelled:
            key = _key(name or "")
            # A one- or two-letter Latin "name" would read codes and words as
            # countries (a dialect's "SE" or "TV"); "中国" is a name.
            if len(key) > 2 or (key and not key.isascii()):
                names.setdefault(key, code)
    for name, code in _NAME_ALIASES.items():
        if (_FLAGS / f"{code}.svg").is_file():
            names[_key(name)] = code
    return names


def _name_flag(text: str) -> str | None:
    """A country named in the category: a part of it on its own ("Sweden",
    "VIP | SWEDEN"), or the words it starts or ends with ("SWEDEN HD",
    "Sport Sweden"). Not a country somewhere in the middle of a title."""
    names = _country_names()
    for segment in _SEGMENTS.split(text):
        words = _key(segment).split()
        for n in range(min(4, len(words)), 0, -1):
            for part in (words[:n], words[-n:]):
                code = names.get(" ".join(part))
                if code:
                    return code
    return None


@lru_cache(maxsize=4096)
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
    if token not in _AMBIGUOUS:
        code = _ALIASES.get(token, token.lower() if prefix else "")
        if code and (_FLAGS / f"{code}.svg").is_file():
            return code, text
    # Many providers name a category after the country alone ("Sweden",
    # "SVERIGE", "Deutschland") - no code, no emoji - and those had no flag.
    code = _name_flag(text)
    if code:
        return code, text
    return None, text


@lru_cache(maxsize=300)
def flag_icon(code: str) -> QIcon:
    return QIcon(str(_FLAGS / f"{code}.svg"))
