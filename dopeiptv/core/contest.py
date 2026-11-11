"""Telling a tug of war over the account from the drops it causes."""
from __future__ import annotations

import time
from typing import Callable


class ContestWatch:
    """The provider cuts a live stream that was playing, again and again
    within a minute: another device or app on the account is taking the
    connection, and each side takes it back in turn. A connection that
    never got going is no evidence of that, so only drops after real play
    count. Each channel is told once, by whichever route sees it first -
    these drops, or the rewind buffer's own watch (``claim``).

    It only watches; how the player reconnects does not depend on it."""

    DROPS = 3
    WINDOW = 60.0
    MIN_PLAYED = 2.0

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._key: object = None
        self._drops: list[float] = []
        self._told = False

    def _follow(self, key: object) -> None:
        if key != self._key:
            self._key, self._drops, self._told = key, [], False

    def drop(self, key: object, played: float) -> bool:
        """*key*'s stream dropped after *played* seconds; True the one time
        the drops add up to a contest."""
        self._follow(key)
        if self._told or played < self.MIN_PLAYED:
            return False
        now = self._clock()
        self._drops = [t for t in self._drops if now - t <= self.WINDOW]
        self._drops.append(now)
        if len(self._drops) < self.DROPS:
            return False
        self._told = True
        return True

    def claim(self, key: object) -> bool:
        """Another route saw the contest on *key*; True if it is the first
        to, so the warning is shown once."""
        self._follow(key)
        if self._told:
            return False
        self._told = True
        return True
