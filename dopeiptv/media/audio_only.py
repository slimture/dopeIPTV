"""Sound with no picture: whether the stream play() opened is audio alone.

An iHeart music channel in an M3U is an address like any TV channel's - no
file extension says it is music - so whether it has a picture is known only
once mpv has opened it and listed its tracks. That is read here: on mpv's
file-loaded event for the file play() opened, and on later changes of its
track list (a stream that turns out to have a picture after all). Both
arrive on mpv's event thread and reach the window's thread through a
signal; nothing polls and nothing is probed. It only reads and says so
(``audio_only_changed``); the window decides what to draw."""
from __future__ import annotations

import weakref

from ..core import stream_info
from ..core.log import log


class AudioOnlyMixin:
    def _ao_init(self) -> None:
        self._ao_path: str | None = None
        self._ao_loaded = False
        self._ao_now = False
        self._ao_event.connect(self._ao_on_event)

    def audio_only(self) -> bool:
        """Whether what plays now is sound without a picture (cover art is
        no picture)."""
        return self._ao_now and self.current_url is not None

    def _ao_reset(self, path: str | None = None) -> None:
        self._ao_path = path or None
        self._ao_loaded = False
        self._ao_now = False

    def _ao_arm(self, core, path: str) -> None:
        """Expect *path* on *core*, which play() is about to open. Hooks
        each core once; the callbacks only hand on what mpv said."""
        self._ao_reset(path)
        ref = getattr(self, "_ao_core", None)
        if ref is not None and ref() is core:
            return
        me, core_ref = weakref.ref(self), weakref.ref(core)

        def loaded(_event) -> None:                 # mpv's event thread
            player, mpv_core = me(), core_ref()
            if player is not None and mpv_core is not None:
                try:
                    player._ao_event.emit("loaded", str(mpv_core.path or ""))
                except (RuntimeError, AttributeError):
                    pass

        def tracks(_name, _value) -> None:          # mpv's event thread
            player = me()
            if player is not None:
                try:
                    player._ao_event.emit("tracks", "")
                except (RuntimeError, AttributeError):
                    pass

        try:
            core.event_callback("file-loaded")(loaded)
        except Exception as e:
            log.debug("audio-only streams not followed: %s", e)
            return
        try:
            core.observe_property("track-list", tracks)
        except Exception as e:
            log.debug("audio-only: track changes not followed: %s", e)
        self._ao_core = core_ref

    def _ao_on_event(self, kind: str, path: str) -> None:
        """(the window's thread) mpv opened a file or changed its tracks:
        said once each time the answer changes, and only for the file
        play() opened - never an earlier stream's late event, never before
        that file's own file-loaded."""
        if kind == "loaded":
            if not self._ao_path or path != self._ao_path:
                return
            self._ao_loaded = True
        elif not self._ao_loaded:
            return
        m = self.video.mpv
        if m is None or self.current_url is None:
            return
        try:
            if str(m.path or "") != self._ao_path:
                return
            tracks = m.track_list
        except Exception as e:
            log.debug("audio-only: tracks not read: %s", e)
            return
        if stream_info.audio_only(tracks):
            now = True
        elif stream_info.has_picture(tracks):
            now = False
        else:
            # No sound and no picture: between files (the rewind buffer
            # opened again, the next stream on its way), nothing to say.
            return
        if now == self._ao_now:
            return
        self._ao_now = now
        log.info("player: %s", "sound alone, no picture" if now
                 else "a picture after all")
        try:
            self.audio_only_changed.emit(now)
        except RuntimeError:
            pass
