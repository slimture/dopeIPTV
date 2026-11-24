"""A small fuzzer of the suite's own, in the standard library alone (CI
installs no hypothesis or atheris): tests/test_fuzz_*.py throw random and
mutated input at everything the app reads from outside (AGENTS.md rule 5)
and hold each reader to what it promises - only the exceptions it, or the
callers that catch them, document; never a hang, nor time that grows faster
than the input; never output out of proportion; never a RecursionError.

Reproducible: input *i* of a target is made from ``random.Random(f"{name}:
{seed}:{i}")`` alone, so a finding names its target, seed and number, and
the same input comes back on every machine, however many ran before it.

``DOPEIPTV_FUZZ_SECONDS``  seconds per target (default ``BUDGET``: the
                           whole suite adds some 20 s to CI); run it with
                           minutes per target locally to find things
``DOPEIPTV_FUZZ_SEED``     another series of inputs (default 0)

What it makes: ``mutate_bytes`` / ``mutate_text`` (flips, truncation,
duplication, splicing, a token from the target's own vocabulary, odd
numbers, deep nesting, control and right-to-left marks, NUL, lone
surrogates), ``odd_encoding`` (UTF-16 and -32 with and without a BOM,
UTF-7, Latin-1, a BOM before UTF-8), ``mutate_json`` (values of the wrong
type, nulls, huge or odd numbers, lists of non-objects, nesting deeper than
Python reads), ``mutate_xml`` (elements dropped, repeated, nested, odd text
and attributes, declared encodings, DOCTYPEs) and ``mutate_ts`` (packets
with odd PIDs, adaptation fields, counters, sections). ``fuzz`` runs a
target on them, ``linear`` measures how its time grows, ``FakeNet`` answers
every request ``requests`` would send.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import random
import signal
import threading
import time
import traceback
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlsplit

#: Seconds per target on CI.
BUDGET = 0.25
#: An input that takes longer than this has hung (each is small).
HANG_S = 3.0
#: Inputs every target runs, however slow the machine: what CI always tries.
MIN_RUNS = 40
#: The most a mutation makes: the time an input takes is the reader's, not
#: the fuzzer's.
MAX_MADE = 1 << 20


def budget(default: float = BUDGET) -> float:
    raw = os.environ.get("DOPEIPTV_FUZZ_SECONDS", "").strip()
    try:
        return max(0.0, float(raw)) if raw else default
    except ValueError:
        return default


def seed() -> int:
    raw = os.environ.get("DOPEIPTV_FUZZ_SEED", "").strip()
    return int(raw) if raw.isdecimal() else 0


def rng_for(name: str, i: int, s: int | None = None) -> random.Random:
    return random.Random(f"{name}:{seed() if s is None else s}:{i}")


# -- a time limit per input ----------------------------------------------------------

class Hang(BaseException):
    """An input ran past its time. A BaseException, so that no reader's own
    ``except Exception`` takes it for a failure of its own."""


@contextlib.contextmanager
def time_limit(seconds: float) -> Iterator[None]:
    """Raise Hang in the code under test after *seconds* (SIGALRM, on the
    main thread of a Unix process - also inside a regular expression, which
    checks for signals as it runs). Elsewhere nothing interrupts it: the
    time is measured after."""
    usable = (hasattr(signal, "setitimer") and threading.current_thread() is threading.main_thread())
    if not usable or seconds <= 0:
        yield
        return

    def fire(_sig: int, _frame: Any) -> None:
        raise Hang(f"no answer within {seconds:.1f} s")

    old = signal.signal(signal.SIGALRM, fire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


# -- running a target ----------------------------------------------------------------

class Finding(AssertionError):
    """What the fuzzer found, with the input that shows it."""


@dataclass
class Outcome:
    kind: str                    # "raised", "hang", "check"
    what: str                    # exception type and where, or the check's words
    detail: str = ""

    def same(self, other: Outcome | None) -> bool:
        return other is not None and other.kind == self.kind and other.what == self.what


def _where(e: BaseException) -> str:
    """The exception's type and the last frame in the app's own code."""
    frames = traceback.extract_tb(e.__traceback__)
    own = [f for f in frames if f"{os.sep}dopeiptv{os.sep}" in f.filename] or frames
    if not own:
        return type(e).__name__
    f = own[-1]
    return f"{type(e).__name__} at {os.path.basename(f.filename)}:{f.lineno} ({f.name})"


def attempt(target: Callable[[Any], Any], data: Any, allowed: tuple[type[BaseException], ...] = (),
            check: Callable[[Any, Any], str | None] | None = None,
            hang: float = HANG_S) -> tuple[Outcome | None, float]:
    """One input: (what went wrong or None, seconds it took)."""
    t0 = time.perf_counter()
    try:
        with time_limit(hang):
            out = target(data)
    except Hang as e:
        return Outcome("hang", "no answer", str(e)), time.perf_counter() - t0
    except RecursionError as e:
        return Outcome("raised", _where(e), "a RecursionError"), time.perf_counter() - t0
    except MemoryError as e:
        return Outcome("raised", _where(e), "a MemoryError"), time.perf_counter() - t0
    except allowed:
        return None, time.perf_counter() - t0
    except Exception as e:  # noqa: BLE001 - what the fuzzer is for
        return Outcome("raised", _where(e), f"{type(e).__name__}: {str(e)[:300]}"), time.perf_counter() - t0
    took = time.perf_counter() - t0
    if took > hang:
        return Outcome("hang", "no answer", f"{took:.1f} s"), took
    if check is not None:
        said = check(data, out)
        if said:
            return Outcome("check", said), took
    return None, took


def shrink(data: Any, fails: Callable[[Any], bool], seconds: float = 3.0) -> Any:
    """A smaller input that still fails the same way: chunks of bytes or
    text taken out (delta debugging), list items and object keys dropped,
    strings in them shortened - for at most *seconds*."""
    end = time.monotonic() + seconds

    def left() -> bool:
        return time.monotonic() < end

    def seq(s: Any) -> Any:
        n = max(1, len(s) // 2)
        while n >= 1 and left():
            i, changed = 0, False
            while i < len(s) and left():
                cand = s[:i] + s[i + n:]
                if len(cand) < len(s) and fails(cand):
                    s, changed = cand, True
                else:
                    i += n
            if not changed:
                n //= 2
        return s

    def tree(obj: Any, rebuild: Callable[[Any], Any]) -> Any:
        if isinstance(obj, list):
            i = 0
            while i < len(obj) and left():
                cand = obj[:i] + obj[i + 1:]
                if fails(rebuild(cand)):
                    obj = cand
                else:
                    i += 1
            for i in range(len(obj)):
                if not left():
                    break
                obj[i] = tree(obj[i], lambda v, i=i, o=obj: rebuild(o[:i] + [v] + o[i + 1:]))
        elif isinstance(obj, dict):
            for k in list(obj):
                if not left():
                    break
                cand = {kk: v for kk, v in obj.items() if kk != k}
                if fails(rebuild(cand)):
                    obj = cand
            for k in list(obj):
                if not left():
                    break
                obj[k] = tree(obj[k], lambda v, k=k, o=obj: rebuild({**o, k: v}))
        elif isinstance(obj, (str, bytes)) and len(obj) > 1:
            obj = seq(obj) if fails(rebuild(obj)) else obj
        return obj

    if isinstance(data, (bytes, str)):
        return seq(data)
    if isinstance(data, (list, dict)):
        import copy
        return tree(copy.deepcopy(data), lambda v: v)
    return data


def shown(data: Any, most: int = 1500) -> str:
    try:
        text = repr(data)
    except ValueError:          # an int past the digits Python writes
        text = f"<{type(data).__name__} that cannot be written>"
    return text if len(text) <= most else text[:most] + f"... ({len(text)} characters)"


def fuzz(name: str, target: Callable[[Any], Any], make: Callable[[random.Random], Any], *,
         allowed: tuple[type[BaseException], ...] = (), check: Callable[[Any, Any], str | None] | None = None,
         seconds: float | None = None, min_runs: int = MIN_RUNS, hang: float = HANG_S,
         seeds: Sequence[Any] = (), explain: Callable[[Any], str] | None = None) -> int:
    """Run *target* on *seeds* and then on inputs from *make* (input i from
    rng_for(name, i)) for *seconds* (budget()), at least *min_runs* of them.
    Raises Finding - with the input made small - at the first that raises
    what is not *allowed*, hangs, or fails *check(input, output)* (its words,
    or None); *explain(input)* adds what the input led to (the answers a
    fake server gave). Returns how many inputs ran."""
    seconds = budget() if seconds is None else seconds
    s = seed()

    def report(data: Any, outcome: Outcome, label: str) -> None:
        def fails(d: Any) -> bool:
            got, _t = attempt(target, d, allowed, check, hang)
            return outcome.same(got)
        small = shrink(data, fails) if outcome.kind != "hang" else data
        again, _t = attempt(target, small, allowed, check, hang)
        more = "\n  " + explain(small) if explain is not None else ""
        raise Finding(f"{name}: {label} - {outcome.kind}: {outcome.what}\n"
                      f"  {(again or outcome).detail}\n  input: {shown(small)}{more}")

    for k, data in enumerate(seeds):
        outcome, _t = attempt(target, data, allowed, check, hang)
        if outcome is not None:
            report(data, outcome, f"seed {k}")
    end = time.monotonic() + seconds
    i = 0
    while i < min_runs or time.monotonic() < end:
        data = make(rng_for(name, i, s))
        outcome, _t = attempt(target, data, allowed, check, hang)
        if outcome is not None:
            report(data, outcome, f"input {i} (DOPEIPTV_FUZZ_SEED={s})")
        i += 1
    return i


def linear(run: Callable[[Any], Any], make: Callable[[int], Any], n: int, *,
           factor: int = 8, most: float = 3.0, floor: float = 0.02) -> None:
    """Fail when *run* on make(factor * n) takes more than *most* times
    factor as long as on make(n) - time that grows faster than its input
    (quadratic is factor times more). The best of three runs each; a big
    run under *floor* seconds is too quick to judge."""
    def best(data: Any) -> float:
        times = []
        for _ in range(3):
            t0 = time.perf_counter()
            with time_limit(60.0):
                run(data)
            times.append(time.perf_counter() - t0)
        return min(times)

    small, big = make(n), make(factor * n)
    t_small, t_big = best(small), best(big)
    if t_big < floor:
        return
    ratio = t_big / max(t_small, 1e-6)
    if ratio > most * factor:
        raise Finding(f"{factor}x the input took {ratio:.0f}x the time "
                      f"({t_small * 1000:.1f} ms -> {t_big * 1000:.1f} ms): it grows faster than the input")


# -- what goes in --------------------------------------------------------------------

#: Text that has tripped readers before: control characters, marks that turn
#: the text around, a NUL, lone surrogates, numbers no reader expects,
#: markup, addresses that lead home.
ODD_TEXT = (
    "", " ", "\x00", "\t", "\r", "\n", "\r\n", "\x0b", "\x0c", "\x1b[31m", "\x7f", "\x85", "\x86", "\x8a",
    "\u2028", "\u2029", "\u200b", "\u200e", "\u200f", "\u202a", "\u202e", "\u2066", "\u2069", "\ufeff",
    "\ufffd", "\ufffe", "\uffff", "\U0010ffff", "\ud800", "\udfff", "\udc80", "e\u0301\u0301\u0301",
    "é", "ö", "日本", "𝟗", "٣", "²", "½", "Ⅻ", "%", "%00", "%zz", "%%", "%s", "{}", "{0}", "{x}", "${x}",
    "\\", "\\\\", '"', "'", "`", "<", ">", "&", "&amp;", "&#0;", "&#x110000;", "&bogus;", "]]>", "<!--",
    "-->", "<![CDATA[", "<?xml", "<!DOCTYPE a [<!ENTITY e \"x\">]>", "&e;", "0", "-0", "1", "-1", "00",
    "007", "0.5", "1e999", "-1e999", "1e-999", "NaN", "nan", "inf", "Infinity", "-Infinity", "0x10",
    "2147483647", "2147483648", "4294967296", "9223372036854775807", "9223372036854775808",
    "18446744073709551616", "99999999999999999999", "1" * 400, "9" * 5000, "true", "false", "null",
    "None", "[]", "{}", "[{}]", ",", ";", ":", "=", "?", "#", "/", "//", "://", "@", ":@", "user:pw@",
    "http://", "https://", "file:///etc/passwd", "http://127.0.0.1/", "http://[::1]/", "http://0/",
    "http://169.254.169.254/latest", "http://2130706433/", "http://0x7f.1/", "http://localhost:6878/",
    "rtsp://", "udp://@239.0.0.1:1234", "ytdl://x", "lavfi:", "-i", "--", "C:\\x", "\\\\server\\share",
    "../../x", "..", ".", "~", "*", "a" * 2000, " " * 300, "\t" * 300, "<" * 300, "&" * 300,
)

#: Values a JSON answer has where an object or a list was meant.
def odd_value(rng: random.Random) -> Any:
    k = rng.randrange(20)
    if k == 0:
        return None
    if k == 1:
        return rng.choice((True, False))
    if k == 2:
        return rng.choice((0, -1, 1, 2 ** 31, 2 ** 32, 2 ** 63, -2 ** 63, 2 ** 64, 10 ** 30, 10 ** 300))
    if k == 3:
        return rng.choice((0.5, -0.0, 1e308, -1e308, 5e-324, float("inf"), float("-inf"), float("nan")))
    if k == 4:
        return []
    if k == 5:
        return {}
    if k == 6:
        return [rng.choice(ODD_TEXT), rng.randrange(-5, 5), None]
    if k == 7:
        return {rng.choice(ODD_TEXT): rng.choice(ODD_TEXT)}
    if k == 8:
        return Raw(deep_json(rng))
    if k == 9:
        return Raw(rng.choice(("1" * 5000, "-" + "9" * 4400, "1e999", "-1e999", "1E400", "NaN",
                               "Infinity", '"\\ud800"', '"\\udfff\\ud800"', '"\\u0000"')))
    return rng.choice(ODD_TEXT)


def deep_json(rng: random.Random) -> str:
    depth = rng.choice((50, 200, 999, 1100, 3000, 20000))
    kind = rng.randrange(3)
    if kind == 0:
        return "[" * depth + "]" * depth
    if kind == 1:
        return '{"a":' * depth + "1" + "}" * depth
    return '[{"x":' * depth + "0" + "}]" * depth


class Raw:
    """JSON text put in as it is (nesting json.dumps cannot write)."""

    def __init__(self, text: str) -> None:
        self.text = text


def to_json(obj: Any) -> str:
    """json.dumps that writes Raw as it is, NaN and Infinity as Python does."""
    raws: list[str] = []

    def mark(o: Any) -> Any:
        if isinstance(o, Raw):
            raws.append(o.text)
            return f"@@RAW{len(raws) - 1}@@"
        if isinstance(o, dict):
            return {(k if isinstance(k, str) else str(k)): mark(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [mark(v) for v in o]
        return o

    try:
        text = json.dumps(mark(obj))
    except RecursionError:
        # Nested deeper than json.dumps writes (Python 3.12's json reads
        # such documents): as text, nesting deeper than any answer is.
        return "[" * 5000 + "]" * 5000
    for i, raw in enumerate(raws):
        text = text.replace(f'"@@RAW{i}@@"', raw, 1)
    return text


def _nodes(obj: Any, path: tuple = ()) -> Iterator[tuple[tuple, Any]]:
    stack = [(path, obj)]
    while stack:
        p, o = stack.pop()
        yield p, o
        if isinstance(o, dict):
            stack.extend((p + (k,), v) for k, v in o.items())
        elif isinstance(o, list):
            stack.extend((p + (i,), v) for i, v in enumerate(o))


def _set(obj: Any, path: tuple, value: Any) -> Any:
    if not path:
        return value
    holder = obj
    for k in path[:-1]:
        holder = holder[k]
    holder[path[-1]] = value
    return obj


def mutate_json(rng: random.Random, obj: Any, n: int | None = None) -> Any:
    """A copy of *obj* with 1 to *n* things wrong: a value of another type,
    a key gone or added, a list of objects with other things in it, an
    object where a list was and the other way round, items repeated, odd
    numbers and strings, nesting deeper than Python reads (Raw)."""
    import copy
    obj = copy.deepcopy(obj)
    for _ in range(n or rng.randint(1, 4)):
        nodes = list(_nodes(obj))
        path, node = rng.choice(nodes)
        op = rng.randrange(10)
        if op <= 2:
            obj = _set(obj, path, odd_value(rng))
        elif op == 3 and isinstance(node, dict) and node:
            node.pop(rng.choice(list(node)))
        elif op == 4 and isinstance(node, dict):
            node[rng.choice(list(node) or ["x"]) if rng.random() < 0.5 else rng.choice(ODD_TEXT)] = odd_value(rng)
        elif op == 5 and isinstance(node, list):
            node.insert(rng.randrange(len(node) + 1), odd_value(rng))
        elif op == 6 and isinstance(node, list) and node and size_of(obj, MAX_MADE) < MAX_MADE // 50:
            item = node[rng.randrange(len(node))]
            node.extend(copy.deepcopy(item) for _ in range(rng.choice((1, 2, 10, 200))))
        elif op == 7:
            if isinstance(node, list):
                obj = _set(obj, path, {str(i): v for i, v in enumerate(node)})
            elif isinstance(node, dict):
                obj = _set(obj, path, list(node.values()))
            else:
                obj = _set(obj, path, [node])
        elif op == 8 and isinstance(node, str):
            obj = _set(obj, path, mutate_text(rng, node, 1))
        elif op == 9 and isinstance(node, (int, float)) and not isinstance(node, bool):
            obj = _set(obj, path, rng.choice((str(node), -node, node * 10 ** 9, None, "")))
        else:
            obj = _set(obj, path, odd_value(rng))
    return obj


def json_value(rng: random.Random, obj: Any) -> Any:
    """What a reader gets from a mutated *obj* once it is parsed - as an
    answer's json() gives it (nesting deeper than Python reads never gets
    that far: json() says it is no JSON)."""
    from dopeiptv.core.safe_json import deeper_than
    for _ in range(5):
        try:
            got = json.loads(to_json(mutate_json(rng, obj)))
        except (ValueError, RecursionError):
            continue
        # json() refuses a document nested deeper than any answer is
        # (core/safe_json) - from Python 3.12 json itself reads far deeper.
        if not deeper_than(got):
            return got
    return obj


def json_body(rng: random.Random, obj: Any) -> bytes:
    """A JSON answer made from *obj*: mutated as a tree, now and then as
    bytes too."""
    text = to_json(mutate_json(rng, obj) if rng.random() < 0.9 else obj)
    data = text.encode("utf-8", "surrogatepass")
    if rng.random() < 0.15:
        data = mutate_bytes(rng, data, 2)
    return data


def _token(rng: random.Random, tokens: Sequence[str | bytes]) -> str:
    t = rng.choice(tokens) if tokens and rng.random() < 0.6 else rng.choice(ODD_TEXT)
    return t.decode("latin-1") if isinstance(t, bytes) else t


def mutate_text(rng: random.Random, text: str, n: int | None = None,
                tokens: Sequence[str | bytes] = (), others: Sequence[str] = ()) -> str:
    """*text* with 1 to *n* mutations, as text: characters flipped, put in,
    taken out; a run repeated (once, or thousands of times); cut short;
    spliced with one of *others*; a token of the target's own or an odd one
    put in."""
    s = text
    for _ in range(n or rng.randint(1, 5)):
        op = rng.randrange(11)
        i = rng.randrange(len(s) + 1)
        if op == 0 and s:
            j = min(len(s) - 1, i)
            s = s[:j] + chr(ord(s[j]) ^ (1 << rng.randrange(8))) + s[j + 1:]
        elif op == 1:
            s = s[:i] + _token(rng, tokens) + s[i:]
        elif op == 2 and s:
            j = rng.randrange(i, len(s) + 1)
            s = s[:i] + s[j:]
        elif op == 3 and s:
            j = min(len(s), i + rng.randint(1, 64))
            s = s[:j] + s[i:j] * rng.choice((1, 2, 7, 100, 2000)) + s[j:]
        elif op == 4:
            s = s[:i]
        elif op == 5 and others:
            other = rng.choice(others)
            s = s[:i] + other[rng.randrange(len(other) + 1):]
        elif op == 6:
            s = s[:i] + chr(rng.choice((rng.randrange(32), rng.randrange(0x80, 0xA0), rng.randrange(0xD800, 0xE000),
                                        rng.randrange(0x110000)))) + s[i:]
        elif op == 7:
            s = s[:i] + _token(rng, tokens) * rng.choice((10, 300, 3000)) + s[i:]
        elif op == 8 and s:
            j = min(len(s), i + rng.randint(1, 16))
            s = s[:i] + "".join(rng.sample(s[i:j], j - i)) + s[j:]
        elif op == 9:
            s = s[:i] + rng.choice(("\ud800", "\udfff", "\x00", "\u202e", "\ufeff")) + s[i:]
        else:
            s = s[:i] + str(rng.choice((-1, 0, 2 ** 31, 2 ** 63, 10 ** 30))) + s[i:]
        s = s[:MAX_MADE]
    return s


def decoded(text: str) -> str:
    """*text* as text read from bytes can be: no lone surrogates (they
    become U+FFFD). JSON's escapes are what give a reader those."""
    return text.encode("utf-8", "surrogatepass").decode("utf-8", "replace")


def mutate_bytes(rng: random.Random, data: bytes, n: int | None = None,
                 tokens: Sequence[str | bytes] = (), others: Sequence[bytes] = ()) -> bytes:
    """*data* with 1 to *n* mutations: bits flipped, bytes set, put in and
    taken out, a run repeated, cut short, spliced with one of *others*, a
    token put in (once or many times), interesting bytes (0x00, 0x7f, 0x80,
    0xff, a lone UTF-8 continuation, a BOM)."""
    b = bytearray(data)
    for _ in range(n or rng.randint(1, 6)):
        op = rng.randrange(11)
        i = rng.randrange(len(b) + 1)
        if op == 0 and b:
            j = min(len(b) - 1, i)
            b[j] ^= 1 << rng.randrange(8)
        elif op == 1 and b:
            b[min(len(b) - 1, i)] = rng.choice((0, 0x7F, 0x80, 0xFF, 0x20, 0x0A, rng.randrange(256)))
        elif op == 2:
            b[i:i] = bytes(rng.randrange(256) for _ in range(rng.randint(1, 8)))
        elif op == 3 and b:
            del b[i:rng.randrange(i, len(b) + 1)]
        elif op == 4 and b:
            j = min(len(b), i + rng.randint(1, 64))
            b[j:j] = b[i:j] * rng.choice((1, 2, 7, 100, 1000))
        elif op == 5:
            del b[i:]
        elif op == 6 and others:
            other = rng.choice(others)
            b[i:] = other[rng.randrange(len(other) + 1):]
        elif op == 7:
            t = _token(rng, tokens)
            b[i:i] = t.encode("utf-8", "surrogatepass") * rng.choice((1, 1, 1, 10, 500))
        elif op == 8:
            b[i:i] = rng.choice((b"\x00", b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf", b"\x80", b"\xc3",
                                 b"\xed\xa0\x80", b"\xf4\x90\x80\x80", b"\r", b"\n" * 50))
        elif op == 9 and b:
            j = min(len(b), i + rng.randint(1, 16))
            piece = bytearray(b[i:j])
            rng.shuffle(piece)
            b[i:j] = piece
        else:
            b[i:i] = str(rng.choice((-1, 0, 2 ** 31, 2 ** 63, 10 ** 30))).encode()
        del b[MAX_MADE:]
    return bytes(b)


ENCODINGS = ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "utf-7",
             "latin-1", "cp1252", "ascii")


def odd_encoding(rng: random.Random, text: str) -> bytes:
    """*text* in an encoding a reader may not expect: UTF-16 or -32 with or
    without a BOM, UTF-7, Latin-1, UTF-8 behind a BOM - what cannot be
    written so is replaced."""
    enc = rng.choice(ENCODINGS)
    return text.encode(enc, "replace") if enc not in ("utf-8",) else text.encode("utf-8", "surrogatepass")


# -- XML -----------------------------------------------------------------------------

#: What a document may say its encoding is: the right one, another, a
#: codec that is no text encoding, one that escapes or nobody has.
XML_ENCODINGS = ("UTF-8", "utf-8", "UTF-16", "utf-16le", "ISO-8859-1", "windows-1252", "ascii", "utf-7",
                 "UTF-32", "cp037", "unicode_escape", "raw_unicode_escape", "idna", "punycode",
                 "rot13", "base64", "hex", "zlib", "bogus", "mbcs", "utf-8-sig", "latin-1", "")


def mutate_tree(rng: random.Random, root: ET.Element, vocab: Sequence[str] = (), n: int | None = None) -> dict:
    """*root* mutated in place, as elements: one dropped, repeated,
    renamed, moved; odd text, a long text, odd attributes. Returns what is
    put in the text afterwards by its marker (nesting ElementTree cannot
    write, entities, CDATA)."""
    raws: dict[str, str] = {}
    for _ in range(n or rng.randint(1, 5)):
        els = list(root.iter())
        el = rng.choice(els)
        op = rng.randrange(11)
        if op == 0 and len(el):
            el.remove(rng.choice(list(el)))
        elif op == 1 and len(el) and len(els) < 2000:
            child = rng.choice(list(el))
            for _ in range(rng.choice((1, 3, 50, 500))):
                el.append(_copy(child))
        elif op == 2:
            el.text = rng.choice(ODD_TEXT) if rng.random() < 0.7 else mutate_text(rng, el.text or "x", 2)
        elif op == 3:
            el.tag = rng.choice(vocab) if vocab and rng.random() < 0.7 else rng.choice(("x", "a:b", "_", "tv"))
        elif op == 4:
            key = rng.choice(list(el.attrib) or ["id"]) if rng.random() < 0.6 else rng.choice(("id", "x", "a"))
            el.set(key, rng.choice(ODD_TEXT))
        elif op == 5 and el.attrib:
            del el.attrib[rng.choice(list(el.attrib))]
        elif op == 6:
            mark = f"RAW{len(raws)}RAW"
            tag = rng.choice(vocab) if vocab else "x"
            depth = rng.choice((10, 100, 1100, 5000))
            raws[mark] = f"<{tag}>" * depth + "</" + tag + ">" + f"</{tag}>" * (depth - 1)
            el.append(ET.Element(mark))
        elif op == 7:
            mark = f"RAW{len(raws)}RAW"
            raws[mark] = rng.choice(("&amp;&lt;&gt;&quot;", "&#0;", "&#x1F;", "&#65;&#x42;", "&bogus;",
                                     "<![CDATA[ x < y ]]>", "<!-- c -->", "<?pi x?>", "&#xD800;",
                                     "&#99999999;", "<![CDATA[" + "]" * 50 + "]]>"))
            el.append(ET.Element(mark))
        elif op == 8 and len(el.text or "") < 1000:
            el.text = (el.text or "x") * rng.choice((100, 5000))
        elif op == 9 and len(els) > 2:
            other = rng.choice(els[1:])
            if other is not el and el not in list(other.iter()):
                for parent in els:
                    if other in list(parent):
                        parent.remove(other)
                        el.append(other)
                        break
        else:
            el.append(ET.Element(rng.choice(vocab) if vocab else "x", {rng.choice(ODD_TEXT) or "k": "v"}
                                 if rng.random() < 0.1 else {}))
    return raws


def _copy(el: ET.Element) -> ET.Element:
    import copy
    return copy.deepcopy(el)


def xml_text(root: ET.Element, raws: dict[str, str]) -> str:
    try:
        text = ET.tostring(root, encoding="unicode")
    except (ValueError, TypeError):
        text = "<x/>"
    for mark, raw in raws.items():
        text = text.replace(f"<{mark} />", raw, 1)
    return text


def mutate_xml(rng: random.Random, data: bytes | str, vocab: Sequence[str] = (), n: int | None = None) -> bytes:
    """A document made from *data* (well formed): mutated as a tree, given
    a declaration with an encoding - the one it is written in, another, or
    none that exists - now and then a DOCTYPE or a comment before it, and
    sometimes mutated as bytes after."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return mutate_bytes(rng, data if isinstance(data, bytes) else data.encode(), n)
    raws = mutate_tree(rng, root, vocab, n)
    text = xml_text(root, raws)[:MAX_MADE]
    head = ""
    k = rng.randrange(8)
    if k == 0:
        head = '<!DOCTYPE tv SYSTEM "xmltv.dtd">'
    elif k == 1:
        head = rng.choice(('<!DOCTYPE x [<!ENTITY a "aaaa">]>', '<!DOCTYPE x [<!ELEMENT x ANY>]>',
                           '<!DOCTYPE x [ <!ATTLIST x a CDATA "]>"> ]>', "<!-- a comment -->" * 3,
                           "<?pi stuff?>", "\ufeff", "  \n"))
    enc = rng.choice(XML_ENCODINGS)
    decl = f'<?xml version="1.0" encoding="{enc}"?>' if enc else ('<?xml version="1.0"?>' if rng.random() < 0.5 else "")
    whole = decl + head + text
    real = enc if rng.random() < 0.7 else rng.choice(ENCODINGS)
    try:
        out = whole.encode(real or "utf-8")
    except (LookupError, UnicodeError, TypeError):
        out = whole.encode("utf-8", "surrogatepass")
    if rng.random() < 0.2:
        out = mutate_bytes(rng, out, 2, tokens=("<", ">", "</", "&", "]]>", '"', "="))
    return out


# -- transport streams ---------------------------------------------------------------

TS = 188


def mutate_ts(rng: random.Random, data: bytes, pids: Sequence[int] = (), n: int | None = None) -> bytes:
    """A transport stream made from *data*, packet-aware: a packet's PID
    (the tables', *pids*, any), its start flag, adaptation field control and
    length, continuity counter, scrambling bits or payload changed - a
    section of any table id and length in it; packets dropped, repeated,
    reordered; bytes between packets (sync lost); cut short."""
    pkts = [bytearray(data[i:i + TS]) for i in range(0, len(data) - TS + 1, TS)] or [bytearray(b"\x47" + bytes(187))]
    for _ in range(n or rng.randint(1, 8)):
        op = rng.randrange(12)
        k = rng.randrange(len(pkts))
        p = pkts[k]
        if op == 0:
            pid = rng.choice((0, 1, 0x10, 0x11, 0x12, 0x14, 0x1FFF, rng.randrange(0x2000), *pids))
            p[1] = (p[1] & 0xE0) | (pid >> 8)
            p[2] = pid & 0xFF
        elif op == 1:
            p[1] ^= 0x40
        elif op == 2:
            p[3] = (p[3] & 0xCF) | (rng.randrange(4) << 4)
        elif op == 3:
            p[4] = rng.choice((0, 1, 182, 183, 184, 200, 255, rng.randrange(256)))
        elif op == 4:
            p[3] = (p[3] & 0xF0) | rng.randrange(16)
        elif op == 5:
            p[3] = (p[3] & 0x3F) | (rng.randrange(4) << 6)
        elif op == 6:
            tid = rng.choice((0x00, 0x02, 0x42, 0x46, 0x4E, 0x70, 0xFF, rng.randrange(256)))
            length = rng.choice((0, 1, 5, 9, 180, 1021, 0xFFF, rng.randrange(0x1000)))
            sec = bytes([0, tid, 0xB0 | (length >> 8), length & 0xFF]) + bytes(
                rng.randrange(256) for _ in range(rng.randint(0, 40)))
            p[1] |= 0x40
            p[3] = (p[3] & 0xCF) | 0x10
            p[4:4 + len(sec)] = sec
            del p[TS:]
        elif op == 7:
            del pkts[k]
            if not pkts:
                pkts.append(bytearray(b"\x47" + bytes(187)))
        elif op == 8:
            pkts[k:k] = [bytearray(p) for _ in range(rng.choice((1, 5, 50)))]
        elif op == 9 and len(pkts) > 1:
            j = rng.randrange(len(pkts))
            pkts[k], pkts[j] = pkts[j], pkts[k]
        elif op == 10:
            for _ in range(rng.randint(1, 4)):
                j = rng.randrange(4, TS)
                p[j] = rng.randrange(256)
        else:
            p[:] = bytes([0x47]) + bytes(rng.randrange(256) for _ in range(TS - 1))
    out = b"".join(bytes(p) for p in pkts)
    if rng.random() < 0.15:
        i = rng.randrange(len(out) + 1)
        out = out[:i] + bytes(rng.randrange(256) for _ in range(rng.randint(1, 300))) + out[i:]
    if rng.random() < 0.1:
        out = out[:rng.randrange(len(out) + 1)]
    return out


# -- answers to requests -------------------------------------------------------------

@dataclass
class Asked:
    method: str
    url: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: bytes


@dataclass
class FakeNet:
    """Every request ``requests`` sends answered here, never by the network:
    ``answer(asked)`` gives body bytes, or (status, body) or (status,
    headers, body). The app's own adapter still runs (its ceiling, its
    checks): only the transport underneath is this. A context manager."""

    answer: Callable[[Asked], Any]
    calls: list[Asked] = field(default_factory=list)
    _saved: Any = None
    _env: dict = field(default_factory=dict)

    def _send(self, adapter: Any, request: Any, stream: bool = False, timeout: Any = None,
              verify: Any = True, cert: Any = None, proxies: Any = None) -> Any:
        from urllib3.response import HTTPResponse
        u = urlsplit(request.url)
        body = request.body or b""
        asked = Asked(request.method, request.url, u.path, dict(parse_qsl(u.query, keep_blank_values=True)),
                      dict(request.headers), body if isinstance(body, bytes) else str(body).encode())
        if len(self.calls) < 10000:
            self.calls.append(asked)
        got = self.answer(asked)
        status, headers = 200, {}
        if isinstance(got, tuple):
            if len(got) == 3:
                status, headers, got = got
            else:
                status, got = got
        data = got if isinstance(got, bytes) else str(got).encode("utf-8", "surrogatepass")
        headers = {"Content-Length": str(len(data)), **headers}
        raw = HTTPResponse(body=io.BytesIO(data), headers=headers, status=status, preload_content=False,
                           decode_content=False, reason="OK" if status < 400 else "Error")
        return adapter.build_response(request, raw)

    def __enter__(self) -> FakeNet:
        from requests.adapters import HTTPAdapter
        self._saved = HTTPAdapter.send
        # No proxy of this machine's in the way (CI has none).
        self._env = {k: os.environ.pop(k) for k in list(os.environ)
                     if k.lower() in ("http_proxy", "https_proxy", "all_proxy")}
        net = self

        def send(adapter: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
            return net._send(adapter, request, *args, **kwargs)
        HTTPAdapter.send = send  # type: ignore[method-assign]
        return self

    def __exit__(self, *exc: Any) -> None:
        from requests.adapters import HTTPAdapter
        HTTPAdapter.send = self._saved  # type: ignore[method-assign]
        os.environ.update(self._env)


def size_of(obj: Any, most: int = 10 ** 8) -> int:
    """About how much *obj* holds: its strings' and bytes' lengths and one
    for every other thing in it, walked without recursion, counted to
    *most*."""
    total = 0
    stack = [obj]
    seen: set[int] = set()
    while stack and total < most:
        o = stack.pop()
        if isinstance(o, (str, bytes, bytearray)):
            total += len(o) + 1
            continue
        if id(o) in seen:
            continue
        seen.add(id(o))
        total += 1
        if isinstance(o, dict):
            stack.extend(o.keys())
            stack.extend(o.values())
        elif isinstance(o, (list, tuple, set, frozenset)):
            stack.extend(o)
        elif hasattr(o, "__dict__") and not isinstance(o, type):
            stack.extend(vars(o).values())
    return total


def bounded(factor: int = 50, extra: int = 100_000) -> Callable[[Any, Any], str | None]:
    """A check: the output holds no more than *factor* times the input,
    plus *extra* - nothing that grows without bound from a small input."""
    def check(data: Any, out: Any) -> str | None:
        n = size_of(data)
        got = size_of(out, factor * n + extra + 1)
        if got > factor * n + extra:
            return f"an output of {got} from an input of {n}"
        return None
    return check
