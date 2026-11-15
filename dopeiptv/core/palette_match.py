"""The command palette's matching: what a typed query finds, best first.

Case and accents do not count ("natt" finds "Nätt-TV", "cafe" "Café"), and
neither does punctuation. Every word typed has to be in the text; one at the
start of a word in it weighs more than one inside a word, and one at the
very start the most. An ``Index`` folds its texts once, so a query over tens
of thousands of channels is a pass of substring tests and nothing more.
"""

from __future__ import annotations

import heapq
import unicodedata
from collections.abc import Iterable, Sequence

# Letters NFKD leaves whole.
_PLAIN = str.maketrans({"ø": "o", "đ": "d", "ł": "l", "ħ": "h", "ı": "i",
                        "æ": "ae", "œ": "oe", "þ": "th", "ð": "d"})


def fold(text: str) -> str:
    """*text* as matching sees it: lower case, no accents, every run of
    anything but letters and digits one space, a space before and after
    (so a word start is " word")."""
    low = unicodedata.normalize("NFKD", (text or "").casefold()).translate(_PLAIN)
    out = []
    space = True
    for ch in low:
        if ch.isalnum():
            out.append(ch)
            space = False
        elif unicodedata.combining(ch):
            continue
        elif not space:
            out.append(" ")
            space = True
    return " " + "".join(out).strip() + " "


def words(query: str) -> list[str]:
    """The words of *query*, folded."""
    return fold(query).split()


def score(terms: Sequence[str], folded: str) -> int:
    """How well *folded* (from ``fold``) matches *terms* (from ``words``):
    0 when a term is missing, else more for terms at word starts, the most
    for one at the very start and for the whole text matched."""
    total = 0
    for t in terms:
        if t not in folded:
            return 0
        if folded.startswith(" " + t):
            total += 4
        elif " " + t in folded:
            total += 2
        else:
            total += 1
        if " " + t + " " in folded:
            total += 1
    if folded == " " + " ".join(terms) + " ":
        total += 4
    return total


class Index:
    """Texts folded once, searched many times. ``search`` gives the
    positions of the best matches in the order the texts were given.

    With *names* (one per text, each a part of its text) a match in the
    name counts twice over and the shorter name goes first: a setting
    found by its name before one found by its explanation."""

    def __init__(self, texts: Iterable[str], names: Iterable[str] | None = None) -> None:
        self._folded = [fold(t) for t in texts]
        self._names = [fold(n) for n in names] if names is not None else None
        if self._names is not None and len(self._names) != len(self._folded):
            raise ValueError("one name per text")

    def __len__(self) -> int:
        return len(self._folded)

    def search(self, query: str, limit: int, terms: Sequence[str] | None = None) -> list[int]:
        """The positions of at most *limit* texts that match *query*: the
        best first, a shorter text before a longer one at the same score,
        then the order given."""
        terms = list(terms) if terms is not None else words(query)
        if not terms or limit <= 0:
            return []
        first = terms[0]
        names = self._names
        hits = []
        for i, f in enumerate(self._folded):
            if first in f:
                s = score(terms, f)
                if s:
                    if names is not None:
                        s += 2 * score(terms, names[i])
                        hits.append((s, -len(names[i]), -i))
                    else:
                        hits.append((s, -len(f), -i))
        best = heapq.nlargest(limit, hits)
        return [-i for _s, _n, i in best]
