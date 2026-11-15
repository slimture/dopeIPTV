"""TV mode's focus moves: which element an arrow goes to.

TV mode (ui/tv_mode.py) is run with the four arrows alone, so every
screen's elements - tiles, lists, the tabs above them - must be reachable
from one another, and nothing may hold the focus for good. Where the
screen has no rule of its own (a list moves its own rows), the arrow goes
to the nearest element that way: the smallest gap along the arrow, an
element in the same row or column first, then the nearest centre. Pure
geometry on rectangles, so it is the same whatever draws them.
"""

from __future__ import annotations

from collections.abc import Sequence

#: (x, y, width, height), in one coordinate system for all of them.
Rect = tuple[float, float, float, float]

DIRECTIONS = ("up", "down", "left", "right")

# How much more an element off the arrow's row (or column) costs than one
# further along it, and what not overlapping it at all costs: the row you
# are in comes first.
_CROSS_WEIGHT = 3.0
_OUT_OF_LINE = 100_000.0


def _centre(r: Rect) -> tuple[float, float]:
    return r[0] + r[2] / 2.0, r[1] + r[3] / 2.0


def _gap(a0: float, a1: float, b0: float, b1: float) -> float:
    """The distance between the spans [a0, a1] and [b0, b1]; 0 when they
    overlap."""
    if b0 > a1:
        return b0 - a1
    if a0 > b1:
        return a0 - b1
    return 0.0


def score(cur: Rect, cand: Rect, direction: str) -> float | None:
    """How far *cand* is from *cur* going *direction* (lower is nearer),
    or None when it is not that way at all."""
    cx, cy = _centre(cur)
    kx, ky = _centre(cand)
    x0, y0, w, h = cur
    a0, b0, aw, bh = cand
    if direction == "right":
        if kx <= cx + 0.5:
            return None
        along = max(0.0, a0 - (x0 + w))
        cross = _gap(y0, y0 + h, b0, b0 + bh)
    elif direction == "left":
        if kx >= cx - 0.5:
            return None
        along = max(0.0, x0 - (a0 + aw))
        cross = _gap(y0, y0 + h, b0, b0 + bh)
    elif direction == "down":
        if ky <= cy + 0.5:
            return None
        along = max(0.0, b0 - (y0 + h))
        cross = _gap(x0, x0 + w, a0, a0 + aw)
    elif direction == "up":
        if ky >= cy - 0.5:
            return None
        along = max(0.0, y0 - (b0 + bh))
        cross = _gap(x0, x0 + w, a0, a0 + aw)
    else:
        return None
    # The centres' distance only breaks ties (between tiles of one row
    # below, say: the one under the focus wins).
    tie = ((kx - cx) ** 2 + (ky - cy) ** 2) ** 0.5 / 1000.0
    # One in line with the focus (its row, its column) before any other.
    beam = 0.0 if cross == 0 else _OUT_OF_LINE
    return beam + along + cross * _CROSS_WEIGHT + tie


def pick(cur: Rect, cands: Sequence[Rect], direction: str) -> int | None:
    """The index in *cands* an arrow *direction* goes to from *cur*, or
    None when nothing is that way."""
    best: tuple[float, int] | None = None
    for i, cand in enumerate(cands):
        if cand == cur:
            continue
        s = score(cur, cand, direction)
        if s is not None and (best is None or s < best[0]):
            best = (s, i)
    return None if best is None else best[1]


def grid_step(index: int, count: int, columns: int,
              direction: str) -> int | None:
    """The index an arrow goes to in a grid of *count* cells laid out
    *columns* to a row, or None at its edge (the arrow then leaves it)."""
    if count <= 0 or not 0 <= index < count:
        return None
    columns = max(1, columns)
    col = index % columns
    if direction == "right":
        return index + 1 if col < columns - 1 and index + 1 < count else None
    if direction == "left":
        return index - 1 if col > 0 else None
    if direction == "down":
        if index + columns < count:
            return index + columns
        # Into a last row that is not full: its last cell.
        last_row = (count - 1) // columns
        return count - 1 if index // columns < last_row else None
    if direction == "up":
        return index - columns if index - columns >= 0 else None
    return None
