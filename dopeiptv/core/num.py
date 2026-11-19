"""Numbers from outside - a provider's, a guide's, a box's, a server's, a
settings file restored or synced - read one way: finite, in range, and
never an exception.

``int(float(text))`` raises OverflowError for "1e999" or "Infinity" (which
float() takes), ``text.isdigit()`` is true for "²" that int() refuses, and
int() refuses a number of more than 4300 digits: each of those took down
whatever was reading it. These say "no number" instead.
"""

from __future__ import annotations

import math
from typing import Any, overload

#: Text longer than this is no number anyone means.
MAX_TEXT = 64


@overload
def number(value: Any, default: float, *, lo: float | None = ...,
           hi: float | None = ...) -> float: ...


@overload
def number(value: Any, default: None = ..., *, lo: float | None = ...,
           hi: float | None = ...) -> float | None: ...


def number(value: Any, default: float | None = None, *, lo: float | None = None,
           hi: float | None = None) -> float | None:
    """*value* (a number, or text such as "12", " 3.5 ", "1e3") as a finite
    float from *lo* to *hi*; *default* for anything else - None, a bool,
    infinity, NaN, text that is no number or longer than MAX_TEXT, a value
    out of range."""
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        try:
            f = float(value)
        except OverflowError:
            return default
    else:
        text = value.strip() if isinstance(value, str) else str(value).strip()
        if not text or len(text) > MAX_TEXT:
            return default
        try:
            f = float(text)
        except ValueError:
            return default
    if not math.isfinite(f) or (lo is not None and f < lo) or (hi is not None and f > hi):
        return default
    return f


@overload
def whole(value: Any, default: int, *, lo: float | None = ..., hi: float | None = ...) -> int: ...


@overload
def whole(value: Any, default: None = ..., *, lo: float | None = ...,
          hi: float | None = ...) -> int | None: ...


def whole(value: Any, default: int | None = None, *, lo: float | None = None,
          hi: float | None = None) -> int | None:
    """number(), its whole part (toward zero, as int(float(...)) has it)."""
    f = number(value, None, lo=lo, hi=hi)
    return default if f is None else int(f)


def digits(text: Any, most: int = 18) -> int | None:
    """*text* made only of decimal digits (at most *most* of them, any
    script's as int() takes them: "120", "١٢٠") as an int; None for
    anything else - "²" too, which isdigit() takes and int() refuses."""
    s = str(text if text is not None else "")
    if not s or len(s) > most or not s.isdecimal():
        return None
    return int(s)
