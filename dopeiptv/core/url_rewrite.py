"""Nerd: a source's own rewrite rules for its stream addresses.

Each rule is *find* -> *replace*, and the rules apply in order to every
address the source hands out to play - live channels, films, episodes,
catch-up - so what is played, recorded, cast or shared is the rewritten
one. A rule is plain text (every occurrence is replaced) or a regular
expression (Python's ``re``: ``\\1`` or ``\\g<name>`` for a group). What
they are for: http:// -> https://, another host or port, a query parameter
of the server's (?profile=matroska -> ?profile=pass).

A rule that cannot work - nothing to find, a regular expression that does
not compile, a group the expression does not have - is refused in the
source dialog and skipped in playback, never an error there. With Nerd off,
or a source without rules, an address is returned exactly as it came.

A regular expression runs on every address the source plays, on the
window's thread, and Python's ``re`` cannot be stopped once it runs: one
that backtracks without end - ``(\\w+\\s?)+$`` took 46 s on an ordinary
address - freezes the whole app. So a regular expression is only taken when
it is safe by construction, read from the standard library's own parse of
it (_parts), not from its text:

- a repeat (``*``, ``+``, ``?``, ``{m,n}``) only of one character, class or
  ``.`` - never of a group or an alternation, so nothing repeats inside a
  repeat;
- alternatives (``a|b``) of plain text only;
- no backreference, lookahead, lookbehind, conditional or atomic group;
- what it costs on the longest address it is run on (_steps): repeats that
  can each end in many places, one after another (``\\w*\\w*X``, ``.*.*X``,
  ``a?a?a?aaa``), multiply - over the budget, refused;
- and, as a last check, timed on long made-up addresses (_too_slow).

One that begins with a repeat without an end - ``(.*)\\.ts$``, ``(\\d+)\\.ts``
- is run without the tries ``re.sub`` makes that must fail (_sub_runs):
once along a long address instead of once per character.

Inline flags (``(?i)`` and the others) are allowed. A pattern is at most
MAX_PATTERN characters; a regular expression is not run on an address longer
than MAX_SUBJECT (the address goes on as it is), and a rule that would make
an address longer than MAX_ADDRESS is skipped. A rule stored before these
checks, or brought in by an imported playlist or a restored backup, that
does not pass them stays stored, does nothing in playback, and the source
dialog says why.

The rules live in the source's options under ``rewrite``, as JSON text (an
export keeps only plain values).
"""

from __future__ import annotations

import json
import re
import time
import warnings
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from . import nerd
from .log import log
from .nerd_check import FAIL, INFO, OK, WARN, Check, redact_url, scrub

KEY = "rewrite"
# More is not a rule set any more but a program; a pattern that long is
# no address's.
MAX_RULES = 20
MAX_PATTERN = 300
#: A regular expression's replacement: each match may add it once.
MAX_REPLACE = 2000
#: A regular expression is not run on a longer address (signed CDN
#: addresses run to 2 KB; Xtream ones are 100 characters).
MAX_SUBJECT = 4096
#: A rule that would make a longer address is skipped.
MAX_ADDRESS = 16384
#: What a regular expression may cost on an address of MAX_SUBJECT
#: characters, in _steps' units (a character looked at, a place tried; one
#: is a nanosecond or less). Most rules cost under 50 million, and
#: ``(.*)\\.(ts|mkv|mp4|avi|m3u8)$`` - unanchored, about the costliest
#: people write - 436 million (0.2 s on 4 KB made to be its worst);
#: ``.*a{150}X`` 2.6 billion, ``\\w*\\w*X`` 10^11.
MAX_STEPS = 500_000_000
#: The timed last check: a regular expression slower than this on one
#: made-up address of up to 512 characters is refused.
SLOW_MS = 50.0
_PROBE_LENGTHS = (32, 128, 512)
# Query keys whose values a rule's text may carry and a line never shows.
_SECRET_IN_TEXT = re.compile(
    r"(?i)\b(username|password|pass|user|token|key|apikey|api_key|mac|auth|sid|secret)=([^&\s]+)")
EMPTY = "nothing to find"

_REPEAT_ONE = ("a repeat (*, +, ?, {m,n}) may only follow one character, class or . - "
               "not a group or alternatives like (…)+, which can hang the app")
_ALT_TEXT = "alternatives (a|b) may only be plain text"
_COSTLY = ("parts that can each match in many ways, one after another (like .*, \\w+ "
           "or x?), can hang the app on a long address - make them narrower "
           "([^/]* instead of .*) or start the pattern with ^")
_NO_PARSER = "regular expressions cannot be checked on this system"
_REFUSED_PARTS = {
    "GROUPREF": "a backreference (\\1 in what to find) is not allowed",
    "GROUPREF_IGNORE": "a backreference (\\1 in what to find) is not allowed",
    "GROUPREF_UNI_IGNORE": "a backreference (\\1 in what to find) is not allowed",
    "GROUPREF_LOC_IGNORE": "a backreference (\\1 in what to find) is not allowed",
    "GROUPREF_EXISTS": "a conditional group (?(1)…) is not allowed",
    "ASSERT": "a lookahead or lookbehind - (?=…), (?<=…) - is not allowed",
    "ASSERT_NOT": "a lookahead or lookbehind - (?!…), (?<!…) - is not allowed",
    "ATOMIC_GROUP": "an atomic group (?>…) is not allowed",
}


def _load_parser() -> Any:
    """The standard library's parser of regular expressions: re._parser
    (3.11 on), sre_parse before it. None: no regular expression is taken.
    By the import statement, never importlib.import_module: this module is
    first imported on a worker thread (a source's client), and on Python
    3.12.3 that call could meet another thread's first import of importlib
    half done - NameError: name 'sys' is not defined, and no recording."""
    try:
        from re import _parser  # type: ignore[attr-defined]
        return _parser
    except ImportError:
        pass
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            import sre_parse
        return sre_parse
    except ImportError:
        return None


_SRE: Any = _load_parser()


@dataclass(frozen=True)
class Rule:
    find: str
    replace: str = ""
    regex: bool = False


def parse(value: Any) -> list[Rule]:
    """The rules a source's options hold: JSON text as stored (a list of
    {"find", "replace", "regex"}), or such a list itself. Anything else, or
    an entry without a find, is no rule."""
    data = value
    if isinstance(value, str):
        if not value.strip():
            return []
        try:
            data = json.loads(value)
        except (ValueError, RecursionError):
            # RecursionError: "[[[[..." nested past Python's limit (a backup
            # or another computer's sync carries the options).
            return []
    if not isinstance(data, list):
        return []
    out: list[Rule] = []
    for row in data[:MAX_RULES]:
        if not isinstance(row, dict):
            continue
        find = row.get("find")
        if not isinstance(find, str) or not find or not _text(find):
            continue
        replace = row.get("replace")
        replace = replace if isinstance(replace, str) else ""
        if not _text(replace):
            continue
        out.append(Rule(find, replace, bool(row.get("regex"))))
    return out


def _text(value: str) -> bool:
    """Whether *value* is text an address can hold: JSON's "\\ud800" is a
    lone surrogate no address encodes, and an address with one made from a
    rule raised wherever it was written out (tests/test_fuzz_readers.py)."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def dump(rules: list[Rule]) -> str:
    """*rules* as a source's options keep them ("" for none)."""
    rows = [{"find": r.find, "replace": r.replace, **({"regex": True} if r.regex else {})}
            for r in rules]
    return json.dumps(rows, ensure_ascii=False) if rows else ""


@lru_cache(maxsize=256)
def _compiled(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


# -- what a regular expression is made of ------------------------------------------

_ASCII_ALL = (1 << 128) - 1


def _bits(chars: str) -> int:
    out = 0
    for ch in chars:
        out |= 1 << ord(ch)
    return out


_DIGIT = _bits("0123456789")
_LETTERS = _bits("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
_WORD = _DIGIT | _LETTERS | _bits("_")
_SPACE_UNICODE = _bits("".join(chr(c) for c in range(128) if chr(c).isspace()))
_SPACE_ASCII = _bits(" \t\n\r\f\v")


@dataclass(frozen=True)
class _Chars:
    """The characters a part can match: its ASCII ones as bits, and whether
    it may match any beyond ASCII (*wide*). Beyond ASCII it is never sure
    which: two wide sets are taken to share some. *exact*: the ASCII bits
    are exactly right (not a guess on the large side), so the set can be
    turned round ([^…])."""

    ascii: int = 0
    wide: bool = False
    exact: bool = True

    def overlaps(self, other: _Chars) -> bool:
        return bool(self.ascii & other.ascii) or (self.wide and other.wide)

    def __or__(self, other: _Chars) -> _Chars:
        return _Chars(self.ascii | other.ascii, self.wide or other.wide,
                      self.exact and other.exact)


_NOTHING = _Chars()
_EVERYTHING = _Chars(_ASCII_ALL, True, False)


def _literal(code: int, flags: int) -> _Chars:
    fold = bool(flags & re.IGNORECASE)
    unicode = not flags & re.ASCII
    if code >= 128:
        # Matched without regard to case, it can be an ASCII letter (K, the
        # Kelvin sign, is k).
        return _Chars(_LETTERS if fold else 0, True, not fold)
    ch = chr(code)
    bits = 1 << code
    if fold and ch.isalpha():
        bits |= 1 << ord(ch.swapcase())
        return _Chars(bits, unicode, True)
    return _Chars(bits)


def _range(lo: int, hi: int, flags: int) -> _Chars:
    out = _NOTHING
    for code in range(max(lo, 0), min(hi, 127) + 1):
        out = out | _literal(code, flags)
    if hi >= 128:
        out = out | _literal(max(lo, 128), flags)
    return out


def _category(name: str, flags: int) -> _Chars:
    unicode = not flags & re.ASCII
    space = _SPACE_UNICODE if unicode else _SPACE_ASCII
    known = {"CATEGORY_DIGIT": _Chars(_DIGIT, unicode),
             "CATEGORY_NOT_DIGIT": _Chars(_ASCII_ALL & ~_DIGIT, True),
             "CATEGORY_WORD": _Chars(_WORD, unicode),
             "CATEGORY_NOT_WORD": _Chars(_ASCII_ALL & ~_WORD, True),
             "CATEGORY_SPACE": _Chars(space, unicode),
             "CATEGORY_NOT_SPACE": _Chars(_ASCII_ALL & ~space, True)}
    return known.get(name, _EVERYTHING)


def _atom(name: str, av: Any, flags: int) -> _Chars:
    """The characters one LITERAL / NOT_LITERAL / ANY / IN matches - on the
    large side where they cannot be told exactly."""
    if name == "LITERAL":
        return _literal(av, flags)
    if name == "NOT_LITERAL":
        # Without regard to case it also refuses the other case: fewer.
        return _Chars(_ASCII_ALL & ~(1 << av) if av < 128 else _ASCII_ALL, True,
                      not flags & re.IGNORECASE)
    if name == "ANY":
        return _Chars(_ASCII_ALL if flags & re.DOTALL else _ASCII_ALL & ~_bits("\n"), True)
    negate, out = False, _NOTHING
    for op, arg in av:
        kind = str(op)
        if kind == "NEGATE":
            negate = True
        elif kind == "LITERAL":
            out = out | _literal(arg, flags)
        elif kind == "RANGE":
            out = out | _range(arg[0], arg[1], flags)
        elif kind == "CATEGORY":
            out = out | _category(str(arg), flags)
        else:
            out = out | _EVERYTHING
    if negate:
        exact = out.exact and not flags & re.IGNORECASE
        return _Chars(_ASCII_ALL & ~out.ascii if exact else _ASCII_ALL, True, False)
    return out


_CATEGORY_TEXT = {"CATEGORY_DIGIT": r"\d", "CATEGORY_NOT_DIGIT": r"\D",
                  "CATEGORY_WORD": r"\w", "CATEGORY_NOT_WORD": r"\W",
                  "CATEGORY_SPACE": r"\s", "CATEGORY_NOT_SPACE": r"\S"}


def _code(code: int) -> str:
    return f"\\U{code:08x}"


def _atom_text(name: str, av: Any) -> str:
    """One LITERAL / NOT_LITERAL / ANY / IN written as a pattern again (its
    flags apart); "" for anything this does not know."""
    if name == "LITERAL":
        return _code(av)
    if name == "NOT_LITERAL":
        return f"[^{_code(av)}]"
    if name == "ANY":
        return "."
    out = ""
    for op, arg in av:
        kind = str(op)
        if kind == "NEGATE" and not out:
            out = "^"
        elif kind == "LITERAL":
            out += _code(arg)
        elif kind == "RANGE":
            out += f"{_code(arg[0])}-{_code(arg[1])}"
        elif kind == "CATEGORY" and str(arg) in _CATEGORY_TEXT:
            out += _CATEGORY_TEXT[str(arg)]
        else:
            return ""
    return f"[{out}]" if out not in ("", "^") else ""


@dataclass(frozen=True)
class _Part:
    """One part of a pattern in the order it matches, groups taken apart:
    "at" (^, $, \\b... - matches no character), "one" (a character), "rep"
    (a character repeated *lo* to *hi* times) or "alt" (one of *alts*)."""

    kind: str
    chars: _Chars = _NOTHING
    lo: int = 1
    hi: int = 1
    lit: str = ""                   # "one": the character, a plain one
    alts: tuple[str, ...] = ()
    fold: bool = False              # matched without regard to case
    possessive: bool = False
    start: bool = False             # "at": only at the start of the address
    atom: str = ""                  # "one", "rep": its character as a pattern
    flags: int = 0                  # the flags it is matched with


class _Refused(Exception):
    pass


_ATOMS = frozenset({"LITERAL", "NOT_LITERAL", "ANY", "IN"})
_REPEATS = frozenset({"MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"})


def _parts(items: Any, flags: int, out: list[_Part]) -> None:
    """*items* (a parsed pattern) as _Part's into *out*; raises _Refused
    with the reason for anything not allowed."""
    for op, av in items:
        name = str(op)
        fold = bool(flags & re.IGNORECASE)
        if name in _ATOMS:
            out.append(_Part("one", _atom(name, av, flags), fold=fold,
                             lit=chr(av) if name == "LITERAL" else "",
                             atom=_atom_text(name, av), flags=flags))
        elif name == "AT":
            at = str(av)
            out.append(_Part("at", lo=0, hi=0, start=at == "AT_BEGINNING_STRING"
                             or (at == "AT_BEGINNING" and not flags & re.MULTILINE)))
        elif name == "SUBPATTERN":
            _group, add, drop, sub = av
            _parts(sub, (flags | add) & ~drop, out)
        elif name == "BRANCH":
            alts: list[str] = []
            for alt in av[1]:
                if any(str(o) != "LITERAL" for o, _a in alt):
                    raise _Refused(_ALT_TEXT)
                alts.append("".join(chr(a) for _o, a in alt))
            chars = _NOTHING
            for text in alts:
                if text:
                    chars = chars | _literal(ord(text[0]), flags)
            out.append(_Part("alt", chars, min(map(len, alts)), max(map(len, alts)),
                             alts=tuple(alts), fold=fold))
        elif name in _REPEATS:
            lo, hi, item = av
            item = list(item)
            if len(item) != 1 or str(item[0][0]) not in _ATOMS:
                raise _Refused(_REPEAT_ONE)
            iop, iav = item[0]
            out.append(_Part("rep", _atom(str(iop), iav, flags), int(lo), int(hi), fold=fold,
                             lit=chr(iav) if str(iop) == "LITERAL" else "",
                             possessive=name == "POSSESSIVE_REPEAT",
                             atom=_atom_text(str(iop), iav), flags=flags))
        else:
            raise _Refused(_REFUSED_PARTS.get(name, f"{name.lower()} is not allowed"))


def _compared(text: str, fold: bool) -> str | None:
    """*text* as two of its characters compare: as it is, or in lower case
    when matched without regard to case. None when that cannot be told
    (beyond ASCII, case has more than two forms: k, K and the Kelvin sign)."""
    if not fold:
        return text
    return text.lower() if text.isascii() else None


def _period(s: str) -> int:
    """The shortest shift that maps *s* onto itself: two places it is
    found at are at least this far apart."""
    if not s:
        return 1
    fail = [0] * len(s)
    k = 0
    for i in range(1, len(s)):
        while k and s[i] != s[k]:
            k = fail[k - 1]
        if s[i] == s[k]:
            k += 1
        fail[i] = k
    return len(s) - fail[-1]


def _alt_ways(part: _Part) -> int:
    """In how many ways *part* (alternatives) can match at one place: one
    when no alternative begins another; else as many as there are."""
    texts = [_compared(t, part.fold) for t in part.alts]
    for i, a in enumerate(texts):
        for j, b in enumerate(texts):
            if a is None or (i != j and b is not None and b.startswith(a)):
                return len(texts)
    return 1


def _follow(parts: list[_Part], j: int) -> tuple[_Chars, int, str, list[_Part], int]:
    """What can come right after part *j*: the characters the next part
    can begin with (and the ones after it while a part may match nothing),
    how many parts that looks at; and the plain text that must follow (in
    the parts right after *j*, as _compared; "" when there is none or it
    cannot be told), its parts, and the index of the part after it."""
    chars, looked = _NOTHING, 0
    for q in parts[j + 1:]:
        looked += 1
        if q.kind == "at":
            continue
        chars = chars | q.chars
        if q.lo > 0:
            break
    text, fold, run, end = "", False, [], j + 1
    for q in parts[j + 1:]:
        if q.kind != "at" and (q.kind != "one" or not q.lit):
            break
        end += 1
        if q.kind == "one":
            text += q.lit
            fold = fold or q.fold
            run.append(q)
    return chars, looked, _compared(text, fold) or "", run, end


def _steps(parts: list[_Part], n: int = MAX_SUBJECT, cap: int = MAX_STEPS) -> int:
    """How many steps a backtracking match of *parts* can take on an address
    of *n* characters, at most (stops counting past *cap*).

    A repeat whose end what follows fixes - [^/]+ before /, \\d+ before .ts
    - ends in one place; one that what follows does not fix - .* before /,
    \\w* before \\w - can end in up to as many places as it can repeat
    (where plain text must follow: as many as that text fits in the
    address), and for each of them all that comes after is tried again.
    Alternatives where none begins another match in one way. Once what is
    left can match nothing, the first try that gets there is the match. A
    pattern that begins with ^ is tried at the start only, any other at
    every place of the address."""
    total, paths = 0, 1
    # (the part right after the plain text a loose repeat must be followed
    # by, the tries that reached that repeat, the text's parts)
    spread: tuple[int, int, list[_Part]] | None = None
    free = [True] * (len(parts) + 1)
    for j in range(len(parts) - 1, -1, -1):
        free[j] = free[j + 1] and parts[j].kind != "at" and parts[j].lo == 0
    for j, p in enumerate(parts):
        if free[j]:
            total += sum(min(q.hi, n) + 1 for q in parts[j:])
            break
        if p.kind in ("at", "one"):
            total += paths
        elif p.kind == "alt":
            total += paths * (sum(map(len, p.alts)) + len(p.alts))
            paths *= _alt_ways(p)
        else:
            top = min(p.hi, n)
            span = max(top - min(p.lo, n), 0) + 1
            chars, looked, text, run, end = _follow(parts, j)
            loose = span > 1 and not p.possessive and p.chars.overlaps(chars)
            if free[j + 1]:
                # Every try that gets past it is the match.
                total += paths * (min(p.lo, n) + 1) + top
            elif (not loose and spread is not None and spread[0] == j
                  and any(not q.chars.overlaps(p.chars) for q in spread[2])):
                # Right after plain text with a character it does not
                # repeat: the places it is tried from are apart, and so are
                # the characters each try runs over - n in all.
                total += spread[1] * n * (2 + looked) + paths * 2
            else:
                total += paths * (top + span * (1 + looked + (len(text) if loose else 0)))
            if loose:
                # (Text matched without regard to case is not told apart.)
                spread = (end, paths, run) if run and not any(q.fold for q in run) else None
                paths *= min(span, n // _period(text) + 1) if text else span
        if total > cap:
            return total
    anchored = bool(parts) and parts[0].kind == "at" and parts[0].start
    return (total if anchored else total * (n + 1)) + n + 1


def _probe_texts(parts: list[_Part], length: int) -> Iterator[str]:
    """Made-up addresses of *length* characters that make a pattern of
    *parts* work hardest: long runs of the characters it looks for, and of
    its own plain text."""
    chars = dict.fromkeys("a0 /._-=?&:%é")
    runs: list[str] = []
    text = ""
    for p in parts + [_Part("at")]:
        if p.kind in ("one", "rep") and p.lit:
            chars[p.lit] = None
            text += p.lit
            continue
        if text:
            runs.append(text)
            text = ""
        for alt in p.alts:
            chars.update(dict.fromkeys(alt))
            runs.append(alt)
    for ch in list(chars)[:24]:
        yield ch * length
        yield ch * (length - 1) + "\x00"
    for run in runs[:8]:
        if run:
            yield (run * (length // len(run) + 1))[:length]
    yield ("ab " * length)[:length]
    yield "http://h.example/" + "x" * max(length - 17, 0)


def _clock() -> float:
    """This thread's processor time: a thread that holds the interpreter
    meanwhile is not counted (the wall clock where there is none)."""
    try:
        return time.thread_time()
    except OSError:
        return time.perf_counter()


def _too_slow(find: str, replace: str, parts: list[_Part]) -> str:
    """The last check: the rule timed, as playback runs it, on made-up
    addresses; slower than SLOW_MS on one, timed again, is refused."""
    spent = 0.0
    for length in _PROBE_LENGTHS:
        for text in _probe_texts(parts, length):
            took = 0.0
            for _try in range(2):
                t0 = _clock()
                _regex_sub(find, replace, text)
                took = _clock() - t0
                if took * 1000 <= SLOW_MS:
                    break
            spent += took
            if took * 1000 > SLOW_MS or spent * 1000 > 6 * SLOW_MS:
                return (f"too slow: {max(took, spent) * 1000:.0f} ms on an address of "
                        f"{length} characters")
    return ""


def _shape(pattern: str) -> tuple[list[_Part], str]:
    """The parts of *pattern* and why it is refused ("" when it is not)."""
    if _SRE is None:
        return [], _NO_PARSER
    try:
        tree = _SRE.parse(pattern)
        state = getattr(tree, "state", None) or getattr(tree, "pattern", None)
        parts: list[_Part] = []
        _parts(tree, int(getattr(state, "flags", 0)), parts)
    except _Refused as e:
        return [], str(e)
    except Exception as e:          # a parser of another version
        log.info("nerd: a rewrite pattern not read (%s)", e)
        return [], _NO_PARSER
    if _steps(parts) > MAX_STEPS:
        return parts, _COSTLY
    return parts, ""


@lru_cache(maxsize=512)
def _verdict(find: str, replace: str, regex: bool) -> str:
    if not find:
        return EMPTY
    if not regex:
        return ""
    if len(find) > MAX_PATTERN:
        return f"longer than {MAX_PATTERN} characters"
    if len(replace) > MAX_REPLACE:
        return f"the replacement is longer than {MAX_REPLACE} characters"
    try:
        pat = _compiled(find)
    except re.error as e:
        return f"{e.msg} (at position {e.pos})" if e.pos is not None else str(e.msg)
    except (TypeError, ValueError, RecursionError, OverflowError) as e:
        return str(e) or type(e).__name__
    parts, why = _shape(find)
    if why:
        return why
    try:
        # The replacement is read before anything is matched: a group the
        # expression does not have is caught here, not while a channel plays.
        pat.sub(replace, "")
    except (re.error, IndexError) as e:
        return f"in the replacement: {getattr(e, 'msg', None) or e}"
    try:
        return _too_slow(find, replace, parts)
    except Exception as e:
        return str(e) or type(e).__name__


def problem(rule: Rule) -> str:
    """Why *rule* cannot work ("" when it can): in words for the line, the
    regular expression's own error where it has one, or what about it could
    hang the app."""
    return _verdict(rule.find, rule.replace, rule.regex)


def problems(rules: list[Rule]) -> list[tuple[int, str]]:
    """(rule number from 1, what is wrong) of each rule that cannot work."""
    return [(i + 1, p) for i, r in enumerate(rules) if (p := problem(r))]


@lru_cache(maxsize=256)
def _runs(pattern: str) -> re.Pattern[str] | None:
    """For a pattern that begins with a repeat of one character class C
    without an end (``(.*)\\.ts$``, ``(\\d+)\\.ts``, ``[^/]+$``) and never
    matches nothing: a pattern for a stretch of C. None for any other."""
    parts, why = _shape(pattern)
    if why or not parts:
        return None
    lead = parts[0]
    if lead.kind != "rep" or lead.hi < MAX_SUBJECT or not lead.atom:
        return None
    if not any(p.kind != "at" and p.lo > 0 for p in parts):
        return None
    try:
        return re.compile(f"(?:{lead.atom})*", lead.flags & (re.IGNORECASE | re.ASCII | re.DOTALL))
    except (re.error, ValueError):
        return None


def _sub_runs(pat: re.Pattern[str], run: re.Pattern[str], template: str, s: str) -> str:
    """``pat.sub(template, s)`` for a pattern _runs() gives *run* for,
    without the tries that must fail. sub() tries a match at every place;
    where a try from the start of a stretch of C failed, the repeat had
    tried every end in the stretch with the rest of the pattern, and a try
    from further in the stretch has only some of those ends - so it fails
    too, and the next place worth a try is where the stretch ends. On a
    long address that is the difference between once and once per
    character (``(.*)\\.ts$`` took 70 ms on 4 KB). The matches, and so the
    result, are sub()'s own: tried in its order, never empty."""
    out: list[str] = []
    done = i = 0
    while i < len(s):
        m = pat.match(s, i)
        if m is None:
            stretch = run.match(s, i)
            i = max(stretch.end() if stretch is not None else i, i + 1)
            continue
        out += (s[done:i], m.expand(template))
        done = i = m.end()
    out.append(s[done:])
    return "".join(out)


def _one(url: str, rule: Rule) -> str | None:
    """*url* through *rule* (None: the address would get too long)."""
    if not rule.regex:
        found = url.count(rule.find)
        if found and len(url) + found * (len(rule.replace) - len(rule.find)) > MAX_ADDRESS:
            return None
        return url.replace(rule.find, rule.replace)
    if len(url) > MAX_SUBJECT:
        return url
    out = _regex_sub(rule.find, rule.replace, url)
    return out if len(out) <= MAX_ADDRESS else None


def _regex_sub(find: str, replace: str, s: str) -> str:
    """``re.sub(find, replace, s)``, the way it runs in playback."""
    pat, run = _compiled(find), _runs(find)
    return pat.sub(replace, s) if run is None else _sub_runs(pat, run, replace, s)


def apply(url: str, rules: list[Rule]) -> str:
    """*url* through *rules*, in order; a rule that cannot work is skipped,
    and so is one that would make the address longer than MAX_ADDRESS. A
    regular expression is not run on an address longer than MAX_SUBJECT.
    An address no rule changes comes back as the same string, and one the
    rules would leave empty comes back as it was."""
    if not url or not rules:
        return url
    out = url
    for rule in rules:
        if problem(rule):
            continue
        try:
            new = _one(out, rule)
        except Exception as e:      # never an error in playback
            log.info("nerd: rewrite rule %r skipped: %s", rule.find, e)
            continue
        if new is None:
            log.info("nerd: rewrite rule %r skipped: the address would pass %d characters",
                     rule.find, MAX_ADDRESS)
            continue
        out = new
    if not out.strip():
        log.info("nerd: rewrite rules left nothing of an address; kept as it was")
        return url
    return url if out == url else out


def rules_of(source: dict | None) -> list[Rule]:
    """The rules of a source (core.nerd.source()'s form: its options)."""
    return parse((source or {}).get(KEY))


def rewrite(url: str, source: dict | None) -> str:
    """What the app plays instead of *url*, an address of *source*: the
    source's rules applied while Nerd is on - *url* itself otherwise."""
    if not url or not source or not source.get(KEY) or not nerd.current().on:
        return url
    rules = rules_of(source)
    return apply(url, rules) if rules else url


# -- the line under the rules ----------------------------------------------------

def shown(url: str, secrets: list[str] | None = None) -> str:
    """*url* without its logins, for a line on screen."""
    return scrub(redact_url(url), list(secrets or []))


def _wrappable(text: str) -> str:
    # A zero-width space after each / ? & lets a long address wrap.
    return re.sub(r"(?<![:/])([/?&])(?!/)", "\\1\u200b", text)


def _rule_line(i: int, rule: Rule, secrets: list[str]) -> str:
    def masked(text: str) -> str:
        return scrub(_SECRET_IN_TEXT.sub(r"\1=•••", text), secrets)
    kind = "regex" if rule.regex else "text"
    return f"Rule {i} ({kind}): {masked(rule.find)}  →  {masked(rule.replace) or '(nothing)'}"


def check(rules: list[Rule], sample: str, channel: str = "", example: bool = False,
          secrets: list[str] | None = None) -> Check:
    """What *sample* (a channel's address of the source; *example*: one
    made up) becomes with *rules* - or which rule cannot work, and why."""
    secrets = list(secrets or [])
    if not rules:
        return Check(INFO, "nerd_ck_rw_none")
    lines = [_rule_line(i + 1, r, secrets) for i, r in enumerate(rules)]
    bad = problems(rules)
    if bad:
        n, why = bad[0]
        lines += ["", *(f"Rule {k}: {w}" for k, w in bad)]
        if why == EMPTY:
            return Check(FAIL, "nerd_ck_rw_empty", {"n": n}, "\n".join(lines))
        return Check(FAIL, "nerd_ck_rw_bad", {"n": n, "error": why}, "\n".join(lines))
    after = apply(sample, rules)
    before_s, after_s = shown(sample, secrets), shown(after, secrets)
    name = channel or "?"
    lines += ["", f"Channel: {channel or '-'}{' (example)' if example else ''}",
              f"Before: {before_s}", f"After: {after_s}"]
    detail = "\n".join(lines)
    if after == sample:
        return Check(WARN, "nerd_ck_rw_same", {"url": _wrappable(before_s)}, detail)
    if "://" not in after:
        return Check(WARN, "nerd_ck_rw_not_address", {"url": _wrappable(after_s)}, detail)
    params = {"before": _wrappable(before_s), "after": _wrappable(after_s)}
    if example:
        return Check(OK, "nerd_ck_rw_would_example", params, detail)
    return Check(OK, "nerd_ck_rw_would", dict(params, channel=name), detail)


# What the archive check's words become for a rewritten address.
_KEYS = {"nerd_ck_cu_ok": "nerd_ck_rw_ok", "nerd_ck_cu_not_stream": "nerd_ck_rw_not_stream",
         "nerd_ck_cu_missing": "nerd_ck_rw_missing"}


def check_address(url: str, original: str, user_agent: str = "",
                  headers: dict[str, str] | None = None, proxy: str = "",
                  secrets: list[str] | None = None, channel: str = "",
                  timeout: float = 15.0) -> Check:
    """Test: ask the rewritten *url* (of a real channel, *original* before
    the rules) the way the app's probe does, and say whether a stream
    comes back."""
    from .catchup import check_archive
    secrets = list(secrets or [])
    r = check_archive(url, user_agent, headers, proxy, timeout=timeout, secrets=secrets,
                      channel=channel)
    detail = "\n".join([f"Before: {shown(original, secrets)}",
                        f"After: {shown(url, secrets)}", r.detail])
    return Check(r.state, _KEYS.get(r.key, r.key), dict(r.params), detail)
