"""What is popular right now that this provider has: the Home shelves
"Popular movies right now" and "Popular series right now".

A provider's API has no chart and no viewing figures - per title it gives
a name, a cover, when it was added and sometimes a rating. So the chart is
TMDB's list of the week (TmdbClient.trending), fetched once a day, matched
against the provider's own catalogue: by TMDB id where the provider gives
one or the poster lookup already found it, else by title and year. Only
what the provider has is shown, in the chart's order.

Pure apart from fetch(), load() and save(). The Home page matches on a
worker thread.
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from typing import Any, Callable

# A chart this old is fetched again; TMDB's weekly list moves every day.
MAX_AGE_S = 20 * 60 * 60
# One older than this is not shown at all, whether a new one came or not.
SHOW_MAX_AGE_S = 7 * 24 * 60 * 60
# After a failed fetch (offline, TMDB down) the next try waits this long.
RETRY_AFTER_S = 60 * 60
# Pages of 20: enough of the chart that a big catalogue fills a shelf.
PAGES = 4

# Words a provider adds to a name that are not part of the title.
_TECH = frozenset((
    "4k", "uhd", "fhd", "hd", "sd", "480p", "720p", "1080p", "2160p",
    "hdr", "hdr10", "hevc", "x264", "x265", "h264", "h265", "multi",
    "multisub", "vostfr"))
_BRACKETED = re.compile(r"[\[({][^\])}]*[\])}]")
_YEAR_IN_BRACKETS = re.compile(r"[\[(]\s*((?:19|20)\d{2})\s*[\])]")
# A short tag in front of the title: "EN - ", "|SE| ", "NF: ", "4K-".
_TAG = re.compile(r"^[^\w]*\w{1,8}[^\w\s]*\s*[-–|:]\s*")


def norm_title(text: str) -> str:
    """The form two spellings of one title share: lower case, no accents,
    no punctuation ("Spider-Man: No Way Home" and "Spider Man No Way
    Home", "Amélie" and "Amelie", "Fast & Furious" and "Fast and
    Furious")."""
    t = text or ""
    if not t.isascii():
        t = unicodedata.normalize("NFKD", t)
        t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.casefold()
    t = re.sub(r"['’`´]", "", t)
    t = t.replace("&", " and ")
    return " ".join(re.sub(r"[\W_]+", " ", t).split())


def _words(text: str) -> list[str]:
    return [w for w in norm_title(text).split() if w not in _TECH]


_LAST_YEAR = time.localtime().tm_year + 1


def _is_year(word: str) -> bool:
    return len(word) == 4 and word.isdigit() and 1900 <= int(word) <= _LAST_YEAR


def name_keys(name: str) -> list[tuple[str, int, int | None]]:
    """The keys a provider's *name* is looked up by, as (key, rank, year),
    best first. Rank 0 is the whole name; each tag taken off its front
    ("EN - ", "|SE| ") adds one. The year is the one in brackets, else a
    trailing one - which may be part of the title instead ("Wonder Woman
    1984" is from 2020), so the name is kept whole as well."""
    m = _YEAR_IN_BRACKETS.search(name)
    bracket_year = int(m.group(1)) if m else None
    text = _BRACKETED.sub(" ", name)
    words = _words(text)
    tail_year = None
    if len(words) > 1 and _is_year(words[-1]) and (
            bracket_year is None or int(words[-1]) == bracket_year):
        tail_year = int(words[-1])
    out: list[tuple[str, int, int | None]] = []

    def add(ws: list[str], rank: int, year: int | None) -> None:
        key = " ".join(ws)
        if key and all(key != k for k, _r, _y in out):
            out.append((key, rank, year))

    # Normalised once: a tag taken off the front takes its own words off
    # the front of the list.
    drop = 0
    for rank in range(3):
        ws = words[drop:]
        if tail_year is not None and len(ws) > 1:
            add(ws[:-1], rank, tail_year)
            if bracket_year is None:
                add(ws, rank, None)
        else:
            add(ws, rank, bracket_year)
        m = _TAG.match(text)
        if not m:
            break
        drop += len(_words(m.group(0)))
        text = text[m.end():]
    return out


def _provider_id(row: dict) -> int | None:
    for key in ("tmdb", "tmdb_id"):
        v = row.get(key)
        if not v:
            continue
        try:
            tid = int(str(v).strip())
        except ValueError:
            continue
        if tid > 0:
            return tid
    return None


def _row_year(row: dict) -> int | None:
    """The year a provider row gives in a field of its own, if any."""
    for key in ("year", "releaseDate", "release_date", "releasedate"):
        v = row.get(key)
        if v and _is_year(str(v).strip()[:4]):
            return int(str(v).strip()[:4])
    return None


def match(chart: list[dict], catalogue: list[dict],
          known_id: Callable[[str], int | None] | None = None
          ) -> list[list[dict]]:
    """For each title on *chart* that the provider has, in the chart's
    order, the catalogue rows that are it, best first - so the caller can
    take the first one the user may see, and a copy in a hidden category
    gives way to one in a visible category. *known_id* gives the TMDB id
    already found for a provider name, when there is one."""
    by_id: dict[int, list[dict]] = {}
    by_key: dict[str, list[tuple[int, int, int | None, dict]]] = {}
    for pos, row in enumerate(catalogue):
        name = str(row.get("name") or row.get("title") or "")
        if not name:
            continue
        tid = _provider_id(row)
        if tid is None and known_id is not None:
            tid = known_id(name)
        if tid is not None:
            # Matched by its id alone, never by a title it shares with
            # another film.
            by_id.setdefault(tid, []).append(row)
            continue
        own_year = _row_year(row)
        for key, rank, year in name_keys(name):
            by_key.setdefault(key, []).append(
                (rank, pos, own_year or year, row))
    out: list[list[dict]] = []
    for entry in chart:
        tid = entry.get("id")
        rows = list(by_id.get(tid, [])) if isinstance(tid, int) else []
        want = entry.get("year")
        hits: list[tuple[int, bool, int, dict]] = []
        for title in entry.get("titles") or []:
            for rank, pos, year, row in by_key.get(
                    " ".join(_words(title)), []):
                if want is None or year is None or abs(year - want) <= 1:
                    hits.append((rank, year is None, pos, row))
        hits.sort(key=lambda h: h[:3])
        for _rank, _no_year, _pos, row in hits:
            if not any(row is r for r in rows):
                rows.append(row)
        if rows:
            out.append(rows)
    return out


def pick(groups: list[list[dict]], ok: Callable[[dict], bool],
         limit: int) -> list[dict]:
    """The first row of each group that *ok* lets through, no row twice,
    at most *limit* of them."""
    out: list[dict] = []
    for rows in groups:
        if len(out) >= limit:
            break
        for row in rows:
            if ok(row) and not any(row is r for r in out):
                out.append(row)
                break
    return out


# -- the chart, fetched once a day and kept on disk --------------------------

def fetch(client: Any, language: str, now: float | None = None) -> dict:
    """This week's chart of movies and series from *client* (a
    TmdbClient), titled in English and in *language*, the app's."""
    langs = ("en-US",) if language == "en" else ("en-US", language)
    return {"t": time.time() if now is None else now, "lang": language,
            "vod": client.trending("vod", PAGES, langs),
            "series": client.trending("series", PAGES, langs)}


def is_fresh(data: dict | None, language: str, now: float) -> bool:
    if not data:
        return False
    return (data.get("lang") == language
            and now - float(data.get("t") or 0) < MAX_AGE_S)


def usable(data: dict | None, now: float) -> bool:
    if not data:
        return False
    return now - float(data.get("t") or 0) < SHOW_MAX_AGE_S


def load(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(
            data.get("t"), (int, float)):
        return None
    for kind in ("vod", "series"):
        rows = data.get(kind)
        if not isinstance(rows, list):
            return None
        data[kind] = [r for r in rows if isinstance(r, dict)
                      and isinstance(r.get("id"), int)
                      and isinstance(r.get("titles"), list)]
    return data


def save(path: str, data: dict) -> None:
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.part"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
