"""Nerd: the highest quality an adaptive (HLS) stream plays at.

An HLS stream lists several versions of itself - its variants, 1080p at 6
Mbit/s, 720p at 3, ... - and mpv opens them all: each variant is a track of
its own, with the bitrate the playlist announces for it
(``track-list/N/hls-bitrate``) and the picture size FFmpeg probed
(``demux-w``/``demux-h``). By itself mpv takes the highest bitrate. mpv has
no option for a resolution: ``hls-bitrate`` (``min``, ``max`` or a number of
bits per second) is the only thing it selects variants by, and a bitrate is
no stand-in for a resolution - a 720p variant at 6 Mbit/s and a 1080p one
at 4 are both real. So:

    Best       nothing at all: mpv's own choice, as without Nerd
    a bitrate  hls-bitrate=<bits>: the best variant announced at or below it
    Lowest     hls-bitrate=min
    1080p ...  hls-bitrate=min, so a stream opens on its smallest variant and
               never above the cap whatever happens next; once mpv has opened
               it (its file-loaded event) the player switches to the tallest
               variant that fits - its video and the audio of the same
               variant together (with the video alone, mpv went on fetching
               the smallest variant for its sound: two downloads).

A variant is measured by its lines as a 16:9 picture would have them: a
1920x800 film is 1080p, not 800p. ``hls-bitrate`` only ever decides between
HLS variants (a track of any other stream has no variant bitrate, so all
tie), and the switch is only made for a stream with two variants or more:
every other stream plays as it did.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from .log import log

if TYPE_CHECKING:               # core.nerd imports this module, nerd_check imports nerd
    from .nerd_check import Check

#: (stored value, label) of the choices; the empty one is Best.
QUALITIES = ("", "1080", "720", "480", "lowest")
HEIGHTS = {"1080": 1080, "720": 720, "480": 480}
# A 1080 stream coded as 1088 lines still counts as 1080.
_SLACK = 16


@dataclass(frozen=True)
class Cap:
    height: int = 0          # the most lines; 0: any
    bits: int = 0            # the most bits per second announced; 0: any
    lowest: bool = False

    @property
    def active(self) -> bool:
        return self.lowest or self.height > 0 or self.bits > 0

    @property
    def picks(self) -> bool:
        """The player chooses a variant once the stream is open."""
        return self.height > 0 and not self.lowest


def cap_of(quality: str | None, kbps: int | str | None = 0) -> Cap:
    """The cap the Nerd page's two settings ask for (*kbps*: kbit/s)."""
    q = str(quality or "").strip().lower().rstrip("p")
    try:
        bits = max(0, int(float(kbps or 0))) * 1000
    except (TypeError, ValueError):
        bits = 0
    if q == "lowest":
        return Cap(lowest=True)
    return Cap(height=HEIGHTS.get(q, 0), bits=bits)


def mpv_options(cap: Cap) -> list[tuple[str, str]]:
    """What goes on the mpv core for *cap* (nothing for Best)."""
    if cap.lowest or cap.picks:
        return [("hls-bitrate", "min")]
    if cap.bits:
        return [("hls-bitrate", str(cap.bits))]
    return []


def describe(cap: Cap) -> str:
    """*cap* in a few words (English: logs, details)."""
    if cap.lowest:
        return "the lowest quality"
    parts = [f"{cap.height}p"] if cap.height else []
    if cap.bits:
        parts.append(f"{cap.bits // 1000} kbit/s")
    return " and ".join(parts) if parts else "the best quality"


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def lines(track: dict) -> int:
    """A video track's size as the lines of a 16:9 picture as wide (0 when
    not known)."""
    h, w = _int(track.get("demux-h")), _int(track.get("demux-w"))
    return max(h, round(w * 9 / 16)) if h else 0


def _variants(tracks: list[dict], kind: str) -> list[dict]:
    return [t for t in tracks if isinstance(t, dict) and t.get("type") == kind
            and _int(t.get("hls-bitrate")) > 0 and t.get("id") is not None
            and not t.get("albumart") and not t.get("image")]


def _same_variant_audio(tracks: list[dict], video: dict) -> dict | None:
    """The audio track of *video*'s variant to play with it: the language
    and title of the one playing now where that variant has them."""
    prog = video.get("program-id")
    if prog is None:
        return None
    audio = _variants(tracks, "audio")
    mine = [t for t in audio if t.get("program-id") == prog]
    if not mine:
        return None
    now = next((t for t in audio if t.get("selected")), None)
    if now is None or now in mine:
        return None
    for key in ("lang", "title"):
        same = [t for t in mine if now.get(key) and t.get(key) == now.get(key)]
        if same:
            return same[0]
    return mine[0]


def pick(tracks: list[dict] | None, cap: Cap) -> dict[str, int]:
    """The tracks to switch to so a stream plays at the best variant *cap*
    allows: {"vid": id, "aid": id}, only what differs from what plays -
    nothing for a stream of fewer than two variants, or variants whose size
    FFmpeg could not tell (it stays on the smallest, inside the cap)."""
    if not cap.picks:
        return {}
    tracks = list(tracks or [])
    any_video = _variants(tracks, "video")
    video = [t for t in any_video if lines(t)]
    if len(video) < 2:
        audio = _variants(tracks, "audio")
        if any_video or len(audio) < 2:
            return {}
        # Sound only (radio): no picture to cap, the best announced within
        # the bitrate.
        ok = [t for t in audio if not cap.bits or _int(t["hls-bitrate"]) <= cap.bits]
        best = (max(ok, key=lambda t: _int(t["hls-bitrate"])) if ok
                else min(audio, key=lambda t: _int(t["hls-bitrate"])))
        return {} if best.get("selected") else {"aid": int(best["id"])}
    fits = [t for t in video if lines(t) <= cap.height + _SLACK
            and (not cap.bits or _int(t["hls-bitrate"]) <= cap.bits)]
    if fits:
        best = max(fits, key=lambda t: (lines(t), _int(t["hls-bitrate"])))
    else:
        best = min(video, key=lambda t: (lines(t), _int(t["hls-bitrate"])))
    out: dict[str, int] = {}
    if not best.get("selected"):
        out["vid"] = int(best["id"])
    aud = _same_variant_audio(tracks, best)
    if aud is not None:
        out["aid"] = int(aud["id"])
    return out


def apply_pick(mpv: Any, cap: Cap) -> dict[str, int]:
    """(mpv's event thread, at file-loaded) Switch *mpv* to the variant
    *cap* picks. Never raises: a stream stays on what it opened with."""
    try:
        tracks = mpv.track_list or []
        chosen = pick(tracks, cap)
        for key, tid in chosen.items():
            mpv[key] = tid
    except Exception as e:
        log.info("nerd: adaptive stream left on its first variant: %s", e)
        return {}
    if chosen:
        vid = chosen.get("vid")
        t = next((t for t in tracks if t.get("type") == "video" and t.get("id") == vid), None)
        log.info("nerd: adaptive stream at %s (cap %s)",
                 f"{t.get('demux-w')}x{t.get('demux-h')} {_int(t.get('hls-bitrate')) // 1000} kbit/s"
                 if t else "its audio", describe(cap))
    return chosen


# -- the line under the setting ------------------------------------------------------

def check(cap: Cap, make_mpv: Callable[[], Any] | None = None) -> Check:
    """Does this mpv take what *cap* needs (a real, throwaway core) - and
    what the cap does, in a line."""
    from . import nerd_check as nc
    if not cap.active:
        return nc.Check(nc.INFO, "nerd_ck_hls_best")
    pairs = mpv_options(cap)
    tried = nc.check_mpv_options("", extra=pairs, make_mpv=make_mpv)
    options = ", ".join(f"{k}={v}" for k, v in pairs)
    if cap.picks:
        within = f", announced at {cap.bits // 1000} kbit/s or less" if cap.bits else ""
        how = (f"mpv: {options} - a stream opens on its smallest variant; then the player "
               f"switches to the tallest one up to {cap.height} lines{within}, its picture and "
               "its sound together.")
    else:
        how = f"mpv: {options} - mpv picks the variant itself."
    detail = "\n".join([f"Cap: {describe(cap)}", how,
                        "Streams that are not adaptive (HLS) are not affected.", "", tried.detail])
    if tried.state == nc.FAIL:
        return nc.Check(nc.FAIL, "nerd_ck_hls_refused", {}, detail)
    if tried.state == nc.INFO:          # no mpv to try it on
        return nc.Check(nc.INFO, tried.key, dict(tried.params), detail)
    if cap.lowest:
        return nc.Check(nc.OK, "nerd_ck_hls_lowest", {}, detail)
    # The numbers read the same in every language.
    what = " · ".join(([f"{cap.height}p"] if cap.height else [])
                      + ([f"{cap.bits // 1000} kbit/s"] if cap.bits else []))
    return nc.Check(nc.OK, "nerd_ck_hls_cap", {"what": what}, detail)
