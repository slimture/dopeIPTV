"""A PIN as it is kept, and as it is tried: the profiles' PINs
(core/profiles.py) and the parental lock's (core/stores.ParentalControl).

Kept: never the PIN - what PBKDF2-HMAC-SHA256 makes of it with a random
salt of its own, in ROUNDS rounds, as ``pbkdf2-sha256$<rounds>$<hex>``
beside that salt. Up to 1.6.8 a PIN was kept as one SHA-256 of salt + PIN;
such a value is still checked the old way and written anew on the next
right entry (``outdated``), so nobody's PIN stops working. A version that
old cannot check a value kept this way.

What that is worth, plainly: four digits are 10,000 PINs. The rounds make
a copy of the settings file - a backup - slow to try, not hard; and whoever
can change the file can take the PIN's lines out of it. A PIN keeps a
family member out, not someone with the computer.

Tried: ``Tries`` counts wrong PINs per PIN, kept in the settings so a
restart does not start the count over. The first FREE slips cost nothing;
from then on each wrong try makes the next wait a little longer - 5 s, 10,
20, 40, then a minute at most - and the right PIN, or a quarter of an hour
without a wrong one, forgets them. Never a lock: the longest wait is
WAITS' last, whatever the clock says.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable
from typing import Any

SCHEME = "pbkdf2-sha256"
#: PBKDF2's rounds for a PIN set from now on (some tenths of a second).
ROUNDS = 600_000
#: More rounds than any version wrote: a value saying so is not one of ours
#: (and would hold the window for minutes).
_MAX_ROUNDS = 5_000_000


def new_salt() -> str:
    return secrets.token_hex(16)


def _key(salt: str, pin: str, rounds: int) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt.encode("utf-8"),
                               rounds).hex()


def hashed(salt: str, pin: str, rounds: int | None = None) -> str:
    """What is kept of *pin* with *salt* (in ROUNDS rounds, or *rounds*)."""
    rounds = ROUNDS if rounds is None else rounds
    return f"{SCHEME}${rounds}${_key(salt, pin, rounds)}"


def make(pin: str) -> tuple[str, str]:
    """(salt, what is kept) for a PIN set now, its salt new."""
    salt = new_salt()
    return salt, hashed(salt, pin)


def _old(salt: str, pin: str) -> str:
    """As a PIN was kept up to 1.6.8."""
    return hashlib.sha256((salt + pin).encode()).hexdigest()


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def check(stored: Any, salt: Any, pin: Any) -> bool:
    """Whether *pin* is the PIN kept as *stored* with *salt* - in either
    way of keeping one."""
    stored, salt, pin = str(stored or ""), str(salt or ""), str(pin or "")
    if not stored:
        return False
    if stored.startswith(SCHEME + "$"):
        try:
            _scheme, text, want = stored.split("$")
            rounds = int(text)
        except ValueError:
            return False
        if not 1 <= rounds <= _MAX_ROUNDS or not want:
            return False
        return _same(_key(salt, pin, rounds), want)
    return _same(_old(salt, pin), stored)


def outdated(stored: Any) -> bool:
    """Whether *stored* is kept the old way, or in fewer rounds than a PIN
    set now: written anew on the next right entry."""
    stored = str(stored or "")
    if not stored:
        return False
    if not stored.startswith(SCHEME + "$"):
        return True
    try:
        return int(stored.split("$")[1]) < ROUNDS
    except (IndexError, ValueError):
        return False


class Tries:
    """Wrong PINs, counted per PIN (*name*: "parental", "profile/<id>")."""

    KEY = "pin_tries"
    #: Wrong tries that cost nothing: a slip of the finger.
    FREE = 2
    #: The wait after the third wrong try, the fourth, ... (s); the last
    #: from then on.
    WAITS = (5.0, 10.0, 20.0, 40.0, 60.0)
    #: Without a wrong try for this long, they are forgotten.
    FORGET_S = 15 * 60.0

    def __init__(self, settings: Any = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.settings = settings
        self.clock = clock
        self._state: dict[str, list[float]] = self._load()

    def _load(self) -> dict[str, list[float]]:
        if self.settings is None:
            return {}
        try:
            raw = json.loads(self.settings.value(self.KEY, "") or "{}")
        except (TypeError, ValueError):
            return {}
        out: dict[str, list[float]] = {}
        for name, entry in (raw.items() if isinstance(raw, dict) else ()):
            try:
                out[str(name)] = [float(int(entry[0])), float(entry[1])]
            except (TypeError, ValueError, IndexError, KeyError):
                continue
        return out

    def _save(self) -> None:
        now = self.clock()
        self._state = {k: v for k, v in self._state.items()
                       if v[0] > 0 and now - v[1] < self.FORGET_S}
        if self.settings is None:
            return
        if self._state:
            self.settings.setValue(self.KEY, json.dumps(
                {k: [int(v[0]), round(v[1], 1)] for k, v in self._state.items()}))
        else:
            self.settings.remove(self.KEY)

    def _entry(self, name: str) -> list[float] | None:
        entry = self._state.get(name)
        if entry is None:
            return None
        now = self.clock()
        if now - entry[1] >= self.FORGET_S:
            del self._state[name]
            self._save()
            return None
        if entry[1] > now:
            # The clock was set back since: counted from now, or the wait
            # would stand until the clock caught up - a lock.
            entry[1] = now
            self._save()
        return entry

    def _wait_after(self, count: float) -> float:
        over = int(count) - self.FREE
        if over <= 0:
            return 0.0
        return self.WAITS[min(over, len(self.WAITS)) - 1]

    def count(self, name: str) -> int:
        entry = self._entry(name)
        return int(entry[0]) if entry is not None else 0

    def wait(self, *names: str) -> float:
        """Seconds until a PIN may be tried where any of *names*' is taken
        (the longest of theirs); 0 when at once."""
        longest = 0.0
        for name in names:
            entry = self._entry(name)
            if entry is None:
                continue
            left = self._wait_after(entry[0]) - (self.clock() - entry[1])
            longest = max(longest, left)
        return max(0.0, longest)

    def wrong(self, *names: str) -> float:
        """A wrong PIN was tried where each of *names*' would have been
        taken: one more for each. The wait that follows."""
        now = self.clock()
        for name in names:
            entry = self._entry(name)
            self._state[name] = [(entry[0] if entry is not None else 0.0) + 1.0, now]
        self._save()
        return self.wait(*names)

    def right(self, name: str) -> None:
        """*name*'s PIN was given: its wrong tries are forgotten (only its
        own - knowing one PIN is no way to more tries at another)."""
        if self._state.pop(name, None) is not None:
            self._save()
