"""Night mode for the sound: loud adverts, bangs and scenes brought down
towards the level of speech, so the volume can stay where it is late at
night. Never louder than without it, and plain speech - the news - is left
as it is.

One audio filter on the player's core, through mpv's ``lavfi`` wrapper:
three of ffmpeg's compressors (``acompressor``) one after the other. Each
only ever turns the sound down (no makeup gain) and has a hard knee
(``knee=1``): below its threshold it does nothing at all. The thresholds
are plain numbers, so no ffmpeg has to read "dB":

- the level: ``threshold=0.133`` (-17.5 dBFS) on the sound's slow RMS,
  ``ratio=4``, ``attack=2000``, ``release=9000`` (the longest acompressor
  takes: it comes down over a second or two and back over several, so it
  never rides between words or speakers) - an advert break, a loud scene,
  a channel mastered hot, brought down steadily;
- the scene: ``threshold=0.25`` (-12 dBFS), ``ratio=8``, ``attack=50``,
  ``release=1000`` - an explosion, a shout;
- the bang: ``threshold=0.35`` (-9 dBFS), ``ratio=20``, ``attack=5``,
  ``release=400`` - a shot or a door caught as it starts, let go before the
  next words.

Speech at broadcast level (-23 LUFS) stays under all three. Measured with
speech-like test signals and ITU-R BS.1770 loudness, on a real core
(tests/test_night_mode.py holds it to the rules): news at -24 LUFS, two
speakers, changes by 0.0 LU and
its gain does not move, the pauses between words neither; a channel
mastered hot (-16 LUFS) comes down about 5 LU, steadily (within 2.2 dB for
one speaker); bangs over quiet dialogue end up 8 dB less loud than
without, an advert 11 LU louder than the speech around it about 4 dB
nearer it, and every signal tried is as loud as before or quieter.

Nothing is lifted. The 1.6.0 leveller (``dynaudnorm``, which takes each
slice of sound towards a peak target) and the first 1.6.1 test build (a
quicker one after a compressor) raised what was quiet: they made
news-like speech 4.4 and 7.9 LU louder and rode its gain by 5 to 7 dB -
"louder, and it wobbles". A leveller that lifts can not leave plain
speech alone and never be louder. ``dynaudnorm`` also holds the sound back
by its whole window (its frames times their number: 3.5 s for 1.6.0's),
which a live stream pays at every start and every switch; these
compressors hold back nothing.

Only options ``acompressor`` has had since ffmpeg 3.0 (2016) are given -
the oldest libmpv the app runs with is built on a newer one - and the
rest are ffmpeg's own: one gain for all channels (the stereo or surround
image does not shift). A graph the core's ffmpeg refuses would cost a
stream its sound, so the player checks that the filter took (has_filter)
and that the sound came up through it. It is put on the core with a label
of its own (``@dopeiptv-night``) and taken off by that label: nothing else
on the audio chain - the music equaliser, an ``af`` from Nerd's mpv
options - is touched, and the picture never is. mpv keeps the filter from
stream to stream on that core, so it holds through channel changes, the
rewind buffer and the pop-out; and it is applied on the fly, the stream
keeps playing.
"""

from __future__ import annotations

from typing import Any, Protocol

#: The setting (QSettings key), "true" / "false".
KEY = "night_mode"
#: The label the filter carries on the core.
LABEL = "dopeiptv-night"
#: The filter graph handed to mpv's lavfi wrapper.
GRAPH = ("acompressor=threshold=0.133:ratio=4:attack=2000:release=9000:knee=1,"
         "acompressor=threshold=0.25:ratio=8:attack=50:release=1000:knee=1,"
         "acompressor=threshold=0.35:ratio=20:attack=5:release=400:knee=1")


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
