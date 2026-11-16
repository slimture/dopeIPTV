"""Night mode for the sound: loud adverts and explosions brought down,
quiet dialogue brought up, so the volume can stay where it is late at night.

One audio filter on the player's core, through mpv's ``lavfi`` wrapper: a
gentle compressor, then ffmpeg's Dynamic Audio Normalizer. The normalizer
cuts the sound into short slices, finds the gain that would take each
slice's peak to the target, and smooths those gains over the slices around
it with a Gaussian window (after a minimum filter, so a loud slice lowers
the gain *before* it arrives and nothing clips). The compressor in front
evens out what is loud and dense within a slice - an advert's music, the
rumble after an explosion - before the leveller sees it, so that comes out
a little less loud next to speech than the leveller alone leaves it.

The leveller alone, with quarter-second slices smoothed over four seconds
(1.6.0), was measured to do little anyone could hear: a short bang stood
37 dB over the speech around it, as it did without, and a scene had to stay
quiet or loud for several seconds before the level moved at all. These
values bring that bang to 16 dB over the speech, and quiet speech up by
about 18 dB within a second; a dense advert 15 dB louder than the speech
before it ends up less than 4 dB louder. The values, and why:

- ``acompressor=threshold=0.063`` (-24 dBFS, written as a plain number so
  no ffmpeg has to read "dB"): above it the level is compressed,
  ``ratio=3`` - gently; ``attack=5`` ms catches a bang as it starts,
  ``release=200`` ms lets go before the next words. It never raises
  anything (no makeup gain), so it can not clip;
- ``dynaudnorm f=150``: slices of 150 ms - a loud advert or a sudden
  explosion is brought down, and quiet speech up, within a second;
- ``g=5``: smoothed over five slices (three quarters of a second): quick,
  yet not within a syllable;
- ``p=0.7``: peaks to -3 dBFS, headroom kept;
- ``m=10``: at most 20 dB of gain, so silence and background hiss are not
  pumped up to full level;
- the rest as ffmpeg has them: one gain for all channels in both (the
  stereo or surround image does not shift), no DC correction.

Only filters and options every ffmpeg in use knows are given -
``acompressor`` and these four of ``dynaudnorm`` have been in ffmpeg since
3.0 (2016), and the oldest libmpv the app runs with is built on a newer
one. A graph the core's
ffmpeg refuses would cost a stream its sound, so the player checks that the
filter took (has_filter) and that the sound came up through it. It is put
on the core with a label of its own (``@dopeiptv-night``) and taken off by
that label: nothing else on the audio chain - the music equaliser, an
``af`` from Nerd's mpv options - is touched, and the picture never is. mpv
keeps the filter from stream to stream on that core, so it holds through
channel changes, the rewind buffer and the pop-out; and it is applied on
the fly - turning it on ramps the level in, turning it off returns it at
once, the stream keeps playing.
"""

from __future__ import annotations

from typing import Any, Protocol

#: The setting (QSettings key), "true" / "false".
KEY = "night_mode"
#: The label the filter carries on the core.
LABEL = "dopeiptv-night"
#: The filter graph handed to mpv's lavfi wrapper.
GRAPH = ("acompressor=threshold=0.063:ratio=3:attack=5:release=200,"
         "dynaudnorm=f=150:g=5:p=0.7:m=10")


class Readable(Protocol):
    def value(self, key: str, default: Any = None) -> Any: ...


class Writable(Readable, Protocol):
    def setValue(self, key: str, value: Any) -> None: ...  # noqa: N802 - Qt's name


def entry() -> str:
    """The filter as mpv's ``af add`` takes it: label, wrapper, graph."""
    return f"@{LABEL}:lavfi=[{GRAPH}]"


def enabled(settings: Readable | None) -> bool:
    """Whether night mode is on (off unless the user turned it on)."""
    if settings is None:
        return False
    try:
        return str(settings.value(KEY, "false")).lower() == "true"
    except Exception:
        return False


def store(settings: Writable | None, on: bool) -> None:
    """Remember night mode for the next start."""
    if settings is not None:
        settings.setValue(KEY, "true" if on else "false")


def has_filter(mpv: Any) -> bool:
    """Whether the core carries the night filter now (its ``af`` list)."""
    try:
        chain = mpv["af"] or []
    except Exception:
        return False
    return any(isinstance(f, dict) and f.get("label") == LABEL for f in chain)


def remove(mpv: Any, label: str = LABEL) -> bool:
    """Take the audio filter labelled *label* off the core, and nothing
    else; one that is not there is no error. An mpv too old to know
    ``remove`` is asked with ``del``, its older name (0.37 knows only the
    new one)."""
    try:
        try:
            mpv.command("af", "remove", "@" + label)
        except Exception:
            mpv.command("af", "del", "@" + label)
        return True
    except Exception:
        return False


def sound_plays(mpv: Any) -> bool | None:
    """Whether a sound track of the stream on *mpv* plays: None for a
    stream without one, or a core that cannot say. A track whose audio
    chain could not be built is listed but not selected. (Not ``aid``: it
    reads no while the music visualiser's graph plays the track.)"""
    try:
        tracks = [t for t in (mpv.track_list or [])
                  if isinstance(t, dict) and t.get("type") == "audio"]
    except Exception:
        return None
    if not tracks:
        return None
    return any(t.get("selected") for t in tracks)


def rescue_sound(mpv: Any) -> bool:
    """The stream on *mpv* has sound but none plays while the night filter
    is on: a core given the filter before any stream (its first, or after a
    rebuild) only finds out at the stream's start that its ffmpeg cannot
    build it, and mpv then drops the sound and plays the picture silent.
    The first sound track asked for again - with the filter, and when that
    does not bring it, without. True when the filter was what kept it off:
    it is off this core now and the sound plays. False otherwise (nothing
    to do, or not the filter's doing - then it stays on). Never raises."""
    if not has_filter(mpv) or sound_plays(mpv) is not False:
        return False
    try:
        first = next(t.get("id") for t in mpv.track_list
                     if isinstance(t, dict) and t.get("type") == "audio")
        mpv.aid = first
        if sound_plays(mpv):
            return False            # a stream switch's race, not the filter
        if not remove(mpv):
            return False
        mpv.aid = first
        back = bool(sound_plays(mpv))
    except Exception:
        back = False
    if not back:
        apply(mpv, True)            # not the filter's doing: it stays
    return back


def apply(mpv: Any, on: bool) -> bool:
    """Put the night filter on the core *mpv*, or take it off - its own
    entry only, by its label (``add`` with a label already there replaces
    that entry in place). True when the core carries it, or no longer
    does, afterwards: a core that is playing refuses a graph its ffmpeg
    cannot build (the command fails, its audio chain stays as it was), and
    one that took the command without the entry showing is no better.
    Never raises."""
    if not on:
        return remove(mpv) and not has_filter(mpv)
    try:
        mpv.command("af", "add", entry())
    except Exception:
        return False
    return has_filter(mpv)
