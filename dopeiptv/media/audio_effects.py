"""What the player does to the sound, and the picture music gets.

The visualiser: an mpv lavfi-complex graph that draws the audio (bars,
spectrum, wave, scope or freqs, the last two in the theme's accent) where
a music stream has no picture - the window reads and keeps the choice
(``vis_state``, ``vis_choose``). The ten-band equaliser, a labelled filter
first on the audio chain. Night mode (core/night_mode.py): its filter put
on and taken off the core playing, a core that refused it not asked again
until it is switched, and a stream's sound that did not start through it
brought back without it. Each touches mpv only when what it wants changed.

Mixed into EmbeddedPlayer; it reads the player's attributes.
"""
from __future__ import annotations

from ..core import night_mode
from ..core.log import log
from ..ui.theme import P


def _hex_rgb(colour: str) -> tuple[int, int, int] | None:
    """A palette colour ("#rrggbb") as its three channels, else None."""
    value = str(colour or "").lstrip("#")
    if len(value) != 6:
        return None
    try:
        n = int(value, 16)
    except ValueError:
        return None
    return (n >> 16) & 255, (n >> 8) & 255, n & 255


class AudioEffectsMixin:
    # -- music: visualiser + equaliser ---------------------------------------

    # Ten ISO bands, the shape every graphic equaliser uses.
    EQ_BANDS = (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
    EQ_PRESETS = {
        "flat":   (0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        "bass":   (6, 5, 4, 2, 0, 0, 0, 0, 0, 0),
        "treble": (0, 0, 0, 0, 0, 1, 3, 4, 5, 6),
        "vocal":  (-2, -1, 0, 2, 4, 4, 3, 1, 0, -1),
        "rock":   (4, 3, 1, -1, -2, 0, 2, 3, 4, 4),
    }
    VIS_STYLES = ("bars", "spectrum", "wave", "scope", "freqs")

    def _vis_filter(self, style: str, w: int = 640, h: int = 360) -> str:
        """An mpv lavfi-complex graph that turns the audio into a picture -
        what a music player shows instead of a black rectangle.

        The filters that draw on a transparent canvas (wave, scope, freqs)
        end in an opaque format: mpv shows what is transparent over a
        checkerboard. Scope and freqs draw in the theme's accent.

        The drawing only sees audio that has a timestamp (aselect; the
        sound itself goes out untouched). Put up on a stream already
        playing, mpv seeks to where it is, and a seekable HLS then hands
        on a first frame without one: showcqt turned that into a picture
        dated thousands of years ahead, and the clock stuck on it."""
        accent = _hex_rgb(P.get("accent", ""))
        if style == "spectrum":
            draw = (f"showspectrum=s={w}x{h}:slide=scroll:mode=combined:"
                    "color=intensity:scale=cbrt:legend=0")
        elif style == "wave":
            draw = (f"showwaves=s={w}x{h}:mode=cline:rate=30:colors=white,"
                    "format=rgb24")
        elif style == "scope":
            # A stereo vectorscope; the accent is how much each line adds,
            # so where they cross it runs to white. Fades leave a short
            # trail.
            tint = (":rc={}:gc={}:bc={}".format(*accent) if accent else "")
            draw = (f"avectorscope=s={w}x{h}:r=30:draw=line:scale=sqrt:"
                    f"zoom=1.3{tint}:rf=15:gf=10:bf=6,format=rgb24")
        elif style == "freqs":
            hi = _hex_rgb(P.get("accent_hi", "")) or accent
            colours = ("|".join("0x{:02X}{:02X}{:02X}".format(*c)
                                for c in (accent, hi))
                       if accent and hi else "white|white")
            draw = (f"showfreqs=s={w}x{h}:r=30:mode=bar:ascale=cbrt:"
                    f"fscale=log:win_size=2048:averaging=2:colors={colours},"
                    "format=rgb24")
        else:
            draw = (f"showcqt=s={w}x{h}:fps=30:count=2:bar_g=4:sono_g=4:"
                    "axis=0:sono_h=0")
        return f"[aid1]asplit=2[ao][a];[a]aselect=not(isnan(pts)),{draw}[vo]"

    # Filled in by the window: reading/writing the user's choices lives
    # there, the drawing lives here.
    open_equaliser = None
    vis_state = None          # () -> (on, style)
    vis_choose = None         # (on, style) -> None

    def _vis_on_get(self) -> bool:
        try:
            return bool(self.vis_state and self.vis_state()[0])
        except Exception:
            return False

    def _vis_style_get(self) -> str:
        try:
            return (self.vis_state and self.vis_state()[1]) or "bars"
        except Exception:
            return "bars"

    def _vis_pick(self, style: str | None) -> None:
        if self.vis_choose:
            self.vis_choose(style is not None, style or self._vis_style_get())

    def set_visualiser(self, on: bool, style: str = "bars", *,
                       playing: bool = False) -> None:
        """Show/hide the audio visualiser. A file with real video never gets
        one; mpv builds the graph from the audio track itself.

        The filter graph is only ever touched when it actually has to
        change: rewriting lavfi-complex on every ordinary video play is a
        needless poke at mpv's pipeline, and the video path must stay
        exactly as it was before any of this existed.

        *playing*: put up on the stream playing now (one mpv found to be
        sound alone, or a style picked from the menu), not before the next
        one loads. mpv then starts the sound and the drawing again
        together, and by default holds the sound back until it reaches the
        first picture's time - on a live stream whose timestamps went back
        in what was already read (an ad break's discontinuity) it never
        did, and sound and picture stood still. So for this file alone
        (a file-local option: the next file has the user's again) the
        sound starts where it is."""
        m = self.video.mpv
        if m is None:
            return
        want = (bool(on), style if on else "")
        if getattr(self, "_vis_now", (False, "")) == want:
            return
        try:
            if on:
                if playing:
                    try:
                        # m[...] is options/<name>; this is the file's own.
                        m.file_local["initial-audio-sync"] = False
                    except Exception as e:
                        log.debug("visualiser: initial-audio-sync: %s", e)
                m["lavfi-complex"] = self._vis_filter(style)
            else:
                m["lavfi-complex"] = ""
            self._vis_now = want
        except Exception as e:
            log.debug("visualiser %s failed: %s", "on" if on else "off", e)

    EQ_LABEL = "dopeiptv-eq"

    def set_equaliser(self, gains, enabled: bool = True) -> None:
        """Apply a ten-band equaliser (gains in dB, -12..+12). Disabled or
        all-flat means no filter at all, so the untouched path stays
        bit-exact. Its own labelled entry on the audio chain, first in it -
        night mode's, after it, stays as it is."""
        m = self.video.mpv
        if m is None:
            return
        try:
            gains = [max(-12.0, min(12.0, float(g))) for g in gains][:10]
            gains += [0.0] * (10 - len(gains))
        except (TypeError, ValueError):
            return
        try:
            want = tuple(gains) if enabled else ()
            if getattr(self, "_eq_now", None) == want:
                return                 # already applied: leave mpv alone
            self._eq_now = want
            if not enabled or not any(abs(g) > 0.05 for g in gains):
                night_mode.remove(m, self.EQ_LABEL)
                return
            parts = [f"equalizer=f={f}:width_type=o:width=1:g={g:.1f}"
                     for f, g in zip(self.EQ_BANDS, gains, strict=True)]
            m.command("af", "pre",
                      f"@{self.EQ_LABEL}:lavfi=[" + ",".join(parts) + "]")
        except Exception as e:
            log.debug("equaliser failed: %s", e)

    # -- night mode (the sound) ------------------------------------------------

    def night_mode_on(self) -> bool:
        return self._night_on

    def set_night_mode(self, on: bool) -> None:
        """Night mode on or off, onto the core at once while the stream
        plays on, and kept for every stream after (core/night_mode.py).
        Only the audio chain changes. Storing the choice is the caller's
        (toggle_night_mode stores it). Every way of switching it - the
        options menu, the window's key, Settings - comes here, and
        night_mode_changed tells the window, which says so once."""
        self._night_on = bool(on)
        self._night_refused = False     # switched by hand: asked again
        self._sync_night()
        self.night_mode_changed.emit(self._night_on)

    def toggle_night_mode(self) -> bool:
        """The options menu's entry and the window's key: flipped,
        remembered and applied. The new state."""
        on = not self._night_on
        night_mode.store(self._settings, on)
        self.set_night_mode(on)
        return on

    def night_mode_failed(self) -> bool:
        """Night mode is on, and the core in use refused its filter: the
        stream plays without it."""
        m = self.video.mpv
        core = self._night_core() if self._night_core is not None else None
        return bool(self._night_on and self._night_refused and m is not None and core is m)

    def _night_from_menu(self, _checked: bool = False) -> None:
        self.toggle_night_mode()

    def _sync_night(self) -> None:
        """The core as night mode wants it. Written only when that differs
        from what this core was given: a play() with nothing changed writes
        nothing, and a core created after the choice (its GL surface builds
        it lazily) gets it on its first play(). A core that refused it is
        not asked again by every play() - only once night mode is switched
        again, or a new core is made. Never raises."""
        m = self.video.mpv
        if m is None:
            return
        core = self._night_core() if self._night_core is not None else None
        mine = core is m
        given = self._night_now if mine else False
        if given == self._night_on or (mine and self._night_refused):
            return
        took = night_mode.apply(m, self._night_on)
        import weakref
        try:
            self._night_core = weakref.ref(m)
        except TypeError:
            self._night_core = None
            return
        if took:
            self._night_now, self._night_refused = self._night_on, False
            log.info("night mode %s", "on" if self._night_on else "off")
        elif self._night_on:
            night_mode.remove(m)            # nothing half-made left on it
            self._night_now, self._night_refused = False, True
            log.warning("night mode: the player did not take its audio filter - "
                        "the sound plays as it is")
        else:
            log.warning("night mode: the audio filter did not come off - "
                        "tried again with the next stream")

    def _night_on_event(self, name: str, _value=None) -> None:
        """(the window's thread) A stream's playback-restart with night mode
        on: did its sound start through the filter? A core given it before
        any stream (its first, or after a rebuild) only finds out then that
        its ffmpeg cannot build it, and mpv drops the sound. Then it comes
        off this core, the sound comes back (core/night_mode.rescue_sound)
        and the window says so. A stream that plays its sound - nearly
        every one - costs a property read."""
        if name != "playback-restart" or not self._night_on or not self._night_now:
            return
        m = self.video.mpv
        core = self._night_core() if self._night_core is not None else None
        if m is None or core is not m or self.current_url is None:
            return
        if night_mode.sound_plays(m) is not False:
            return
        if not night_mode.rescue_sound(m):
            return
        self._night_now, self._night_refused = False, True
        log.warning("night mode: the stream's sound did not start through its "
                    "filter - it plays without it")
        self.night_mode_refused.emit()
