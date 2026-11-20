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

A newer mpv (0.42 here; 0.37 still had the tracks) opens only the variant
it chose and lists them all as editions instead - "Bitrate: 3.000 Mbps
(h264 1280x720 ...)", the announced bitrate in the edition's metadata
(``variant_bitrate``). There the switch is to the edition, for this stream
only (``file-local-options/edition``): mpv changes variant in place, at the
position it plays, picture and sound together.

A variant is measured by its lines as a 16:9 picture would have them: a
1920x800 film is 1080p, not 800p. ``hls-bitrate`` only ever decides between
HLS variants (a track of any other stream has no variant bitrate, so all
tie; a film's editions have none either), and the switch is only made for a
stream with two variants or more: every other stream plays as it did.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from .log import log
from .num import whole

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
    bits = max(0, whole(kbps or 0, 0, hi=10_000_000)) * 1000
    if q == "lowest":
        return Cap(lowest=True)
    return Cap(height=HEIGHTS.get(q, 0), bits=bits)


def mpv_options(cap: Cap, picker: bool = True) -> list[tuple[str, str]]:
    """What goes on the mpv core for *cap* (nothing for Best). *picker*:
    the core has the player's variant switch (the docked player has it). A
    core without one (a multiview cell) would stay on the smallest variant
    for good with a picture size cap, so it gets only the bitrate cap, if
    one is set - and Lowest, which asks for the smallest."""
    if cap.lowest or (cap.picks and picker):
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


def _tallest_within(video: list[dict], cap: Cap) -> dict:
    """The tallest of *video* (variants of known size) *cap* allows - the
    smallest there is when none fits."""
    fits = [t for t in video if lines(t) <= cap.height + _SLACK
            and (not cap.bits or _int(t["hls-bitrate"]) <= cap.bits)]
    if fits:
        return max(fits, key=lambda t: (lines(t), _int(t["hls-bitrate"])))
    return min(video, key=lambda t: (lines(t), _int(t["hls-bitrate"])))


def _best_sound(audio: list[dict], cap: Cap) -> dict:
    """Sound only (radio): no picture to cap, the best announced within the
    bitrate - the smallest when none is."""
    ok = [t for t in audio if not cap.bits or _int(t["hls-bitrate"]) <= cap.bits]
    return (max(ok, key=lambda t: _int(t["hls-bitrate"])) if ok
            else min(audio, key=lambda t: _int(t["hls-bitrate"])))


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
        best = _best_sound(audio, cap)
        return {} if best.get("selected") else {"aid": int(best["id"])}
    best = _tallest_within(video, cap)
    out: dict[str, int] = {}
    if not best.get("selected"):
        out["vid"] = int(best["id"])
    aud = _same_variant_audio(tracks, best)
    if aud is not None:
        out["aid"] = int(aud["id"])
    return out


# "1280x720" in an edition's title, as mpv describes a picture.
_SIZE = re.compile(r"(?<![\dx])(\d{2,5})x(\d{2,5})(?![\dx])")


def _edition_variants(editions: list[dict]) -> list[dict]:
    """A newer mpv's variants, one per edition, as tracks would have them
    (id, hls-bitrate, demux-w/h from the title: 0 when it says none); []
    unless every edition is one (a film's editions announce no bitrate)."""
    out: list[dict] = []
    for e in editions:
        meta = e.get("metadata") if isinstance(e, dict) else None
        bits = _int(meta.get("variant_bitrate")) if isinstance(meta, dict) else 0
        if bits <= 0 or e.get("id") is None:
            return []
        size = _SIZE.search(str(e.get("title") or ""))
        out.append({"id": _int(e["id"]), "hls-bitrate": bits,
                    "demux-w": int(size.group(1)) if size else 0,
                    "demux-h": int(size.group(2)) if size else 0})
    return out


def pick_edition(editions: list[dict] | None, current: Any, tracks: list[dict] | None,
                 cap: Cap) -> int | None:
    """The edition to switch to on a newer mpv, which lists an HLS stream's
    variants as editions and only the one it opened in the track list: the
    tallest *cap* allows, as pick() chooses among tracks (the best sound
    within the bitrate for a stream of sound only). None when nothing is to
    change: not variants, fewer than two, sizes not known (it stays on the
    smallest, inside the cap), or the right one plays already."""
    if not cap.picks:
        return None
    variants = _edition_variants([e for e in editions or [] if isinstance(e, dict)])
    if len(variants) < 2:
        return None
    sized = [v for v in variants if lines(v)]
    if len(sized) >= 2:
        best = _tallest_within(sized, cap)
    elif not sized and not _variants(list(tracks or []), "video") \
            and _variants(list(tracks or []), "audio"):
        best = _best_sound(variants, cap)
    else:
        return None
    if current is not None and best["id"] == _int(current):
        return None
    return int(best["id"])


def _one_variant_shown(tracks: list[dict]) -> bool:
    """An HLS stream of which the track list shows one variant - all a newer
    mpv shows (the others are editions). Only then are editions asked for:
    nothing more is read from the core for any other stream."""
    video, audio = _variants(tracks, "video"), _variants(tracks, "audio")
    return bool(video or audio) and len(video) <= 1 and len(audio) <= 1


def _apply_edition(mpv: Any, tracks: list[dict], cap: Cap) -> dict[str, int]:
    editions = getattr(mpv, "edition_list", None)
    if not editions:
        return {}
    ed = pick_edition(editions, getattr(mpv, "current_edition", None), tracks, cap)
    if ed is None:
        return {}
    # For this stream only: the next one opens as mpv chooses.
    mpv.command("set", "file-local-options/edition", str(ed))
    v = next((x for x in _edition_variants(editions) if x["id"] == ed), {})
    log.info("nerd: adaptive stream at %s (cap %s, edition %d)",
             f"{v.get('demux-w')}x{v.get('demux-h')} {_int(v.get('hls-bitrate')) // 1000} kbit/s",
             describe(cap), ed)
    return {"edition": ed}


def apply_pick(mpv: Any, cap: Cap) -> dict[str, int]:
    """(mpv's event thread, at file-loaded) Switch *mpv* to the variant
    *cap* picks - by track, or by edition on a newer mpv. Never raises: a
    stream stays on what it opened with."""
    try:
        tracks = mpv.track_list or []
        chosen = pick(tracks, cap)
        for key, tid in chosen.items():
            mpv[key] = tid
        if not chosen and _one_variant_shown(tracks):
            return _apply_edition(mpv, tracks, cap)
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
