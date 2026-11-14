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

The rules live in the source's options under ``rewrite``, as JSON text (an
export keeps only plain values).
"""

from __future__ import annotations

import json
import re
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
MAX_PATTERN = 500
# Query keys whose values a rule's text may carry and a line never shows.
_SECRET_IN_TEXT = re.compile(
    r"(?i)\b(username|password|pass|user|token|key|apikey|api_key|mac|auth|sid|secret)=([^&\s]+)")
# A repeated group that itself repeats - (a+)+, (.*)*, (x|y+){2,} - can take
# minutes on one address (the window waits on it): refused, as a pattern
# that cannot be checked any better before it runs.
_NESTED = re.compile(r"\((?:[^()\\]|\\.)*[+*}]\)\s*[+*{]")
EMPTY = "nothing to find"


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
        except ValueError:
            return []
    if not isinstance(data, list):
        return []
    out: list[Rule] = []
    for row in data[:MAX_RULES]:
        if not isinstance(row, dict):
            continue
        find = row.get("find")
        if not isinstance(find, str) or not find:
            continue
        replace = row.get("replace")
        out.append(Rule(find, replace if isinstance(replace, str) else "",
                        bool(row.get("regex"))))
    return out


def dump(rules: list[Rule]) -> str:
    """*rules* as a source's options keep them ("" for none)."""
    rows = [{"find": r.find, "replace": r.replace, **({"regex": True} if r.regex else {})}
            for r in rules]
    return json.dumps(rows, ensure_ascii=False) if rows else ""


@lru_cache(maxsize=256)
def _compiled(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


def problem(rule: Rule) -> str:
    """Why *rule* cannot work ("" when it can): in words for the line, the
    regular expression's own error where it has one."""
    if not rule.find:
        return EMPTY
    if not rule.regex:
        return ""
    if len(rule.find) > MAX_PATTERN:
        return f"longer than {MAX_PATTERN} characters"
    if _NESTED.search(rule.find):
        return "a repeated group that repeats again, like (a+)+, can hang the app"
    try:
        pat = _compiled(rule.find)
    except re.error as e:
        return f"{e.msg} (at position {e.pos})" if e.pos is not None else str(e.msg)
    except (TypeError, ValueError, RecursionError, OverflowError) as e:
        return str(e) or type(e).__name__
    try:
        # The replacement is read before anything is matched: a group the
        # expression does not have is caught here, not while a channel plays.
        pat.sub(rule.replace, "")
    except (re.error, IndexError) as e:
        return f"in the replacement: {getattr(e, 'msg', None) or e}"
    return ""


def problems(rules: list[Rule]) -> list[tuple[int, str]]:
    """(rule number from 1, what is wrong) of each rule that cannot work."""
    return [(i + 1, p) for i, r in enumerate(rules) if (p := problem(r))]


def _one(url: str, rule: Rule) -> str:
    if not rule.regex:
        return url.replace(rule.find, rule.replace)
    return _compiled(rule.find).sub(rule.replace, url)


def apply(url: str, rules: list[Rule]) -> str:
    """*url* through *rules*, in order; a rule that cannot work is skipped.
    An address no rule changes comes back as the same string, and one the
    rules would leave empty comes back as it was."""
    if not url or not rules:
        return url
    out = url
    for rule in rules:
        if problem(rule):
            continue
        try:
            out = _one(out, rule)
        except Exception as e:      # never an error in playback
            log.info("nerd: rewrite rule %r skipped: %s", rule.find, e)
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
