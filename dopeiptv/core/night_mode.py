"""Night mode for the sound: loud adverts and explosions brought down,
quiet dialogue brought up, so the volume can stay where it is late at night.

One audio filter on the player's core, ffmpeg's Dynamic Audio Normalizer
(``dynaudnorm``) through mpv's ``lavfi`` wrapper. It looks at the sound a
quarter of a second at a time, finds the gain that would take each slice's
peak to the target, and smooths those gains over the slices around it with
a Gaussian window (after a minimum filter, so a loud slice lowers the gain
*before* it arrives and nothing clips). The result rides the level the way
a person at the volume knob would, rather than squashing every syllable as
a fast compressor does. The values, and why:

- ``f=250``: quarter-second slices (half the default) - an advert break or a
  sudden loud scene is caught within a couple of seconds;
- ``g=15``: smoothed over 15 slices (about four seconds) - slow enough that
  the level does not pump within a sentence;
- ``p=0.5``: peaks to -6 dBFS, leaving headroom;
- ``m=8``: at most 18 dB of gain, so silence and background hiss are not
  pumped up to full level;
- the rest as ffmpeg has them: one gain for all channels (the stereo or
  surround image does not shift), no DC correction, no compression.

Only options every ffmpeg in use knows are given (newer ones would make an
old libmpv refuse the filter). It is put on the core with a label of its own
(``@dopeiptv-night``) and taken off by that label: nothing else on the
audio chain - the music equaliser, an ``af`` from Nerd's mpv options - is
touched, and the picture never is. mpv keeps the filter from stream to
stream on that core, so it holds through channel changes, the rewind
buffer and the pop-out; and it is applied on the fly - turning it on ramps
the level in, turning it off returns it at once, the stream keeps playing.
"""

from __future__ import annotations

from typing import Any, Protocol

#: The setting (QSettings key), "true" / "false".
KEY = "night_mode"
#: The label the filter carries on the core.
LABEL = "dopeiptv-night"
#: The filter graph handed to mpv's lavfi wrapper.
GRAPH = "dynaudnorm=f=250:g=15:p=0.5:m=8"


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


def apply(mpv: Any, on: bool) -> bool:
    """Put the night filter on the core *mpv*, or take it off - its own
    entry only, by its label (``add`` with a label already there replaces
    that entry in place). True when the core took the change."""
    if not on:
        return remove(mpv)
    try:
        mpv.command("af", "add", entry())
        return True
    except Exception:
        return False
