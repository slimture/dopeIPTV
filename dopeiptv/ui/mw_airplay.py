"""MainWindow mixin: AirPlay to an Apple TV, on macOS.

The receiver (services/airplay_macos.py) joins the cast hub as one more
entry in the cast dialog; from there it is a cast like any other - the
local player stops, the cast strip shows what the Apple TV plays and
drives it. What only AirPlay does comes back here: the cast ending by
itself (the Apple TV gone, nothing picked in Apple's menu, a stream it
would not play) and the Apple TV not reaching this Mac. Linux and Windows
add nothing.
"""
from __future__ import annotations

import sys

from ..core.log import log
from ..i18n import tr


class _AirPlayMixin:
    """MainWindow mixin: AirPlay as a cast receiver (macOS only)."""

    _airplay = None

    def _init_airplay(self) -> None:
        """Add AirPlay to the cast hub where this Mac can AirPlay; a build
        without AVFoundation or AVKit says so once in the debug log."""
        if sys.platform != "darwin":
            return
        try:
            from ..services import airplay_macos
            if not airplay_macos.supported():
                log.debug("airplay: off (PyObjC's AVFoundation or AVKit is "
                          "not installed)")
                return
            rx = airplay_macos.AirPlayReceiver(self)
        except Exception as e:
            log.debug("airplay: off (%s: %s)", type(e).__name__, e)
            return
        rx.context = self._airplay_context
        rx.ended.connect(self._airplay_ended)
        rx.problem.connect(self._airplay_problem)
        self.cast.add_receiver(rx)
        self._airplay = rx

    def _airplay_context(self) -> dict:
        return getattr(self, "_cast_ctx", None) or {}

    def _casting_airplay(self) -> bool:
        is_airplay = getattr(getattr(self, "cast", None), "is_airplay", None)
        return bool(callable(is_airplay)
                    and is_airplay(getattr(self, "_cast_device", None)))

    def _airplay_ended(self, why: str) -> None:
        """The Apple TV stopped playing by itself: the strip comes down,
        the point a film reached is kept, and why is said."""
        if not self._casting_airplay():
            return
        self._save_cast_position()
        self.show_cast_strip(None)
        if why:
            self._set_status(tr(why))

    def _airplay_problem(self, why: str) -> None:
        if self._casting_airplay() and why:
            self._error(tr(why))
