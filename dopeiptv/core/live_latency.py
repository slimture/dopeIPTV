"""Low latency for live (Nerd): one switch for live channels the player
takes straight from the provider - a smaller buffer, a faster start, closer
to the live edge. Never for the rewind buffer (mpv then plays the app's own
copy), catch-up and timeshift, recordings or films: the options are set
before such a live stream opens and put back before the next stream that is
not one.

They are mpv's and ffmpeg's well-known ones:

    cache-secs=2, demuxer-readahead-secs=2   read 2 s ahead, not the buffer Settings sets
    cache-pause-wait=0.5                      after a stall, go on with half a second
    demuxer-lavf-analyzeduration=0.5          look at half a second (and 500 kB)
    demuxer-lavf-probesize=500000               of the stream before showing it
    demuxer-lavf-o: fflags=+nobuffer          no buffering while the stream is probed
                    live_start_index=-1       an HLS stream starts at its newest segment
    video-latency-hacks=yes                   a frame or two sooner on screen

What they replaced goes back as Nerd's own options say it now (the Channel
start choice, typed mpv options), else as the core had it before Nerd
(core/nerd.py remembers that); cache-secs needs nothing, the player sets it
for every stream.
"""

from __future__ import annotations

import weakref
from typing import Any, Callable

from . import nerd
from .log import log

KEY = "nerd_low_latency"
OPTIONS: list[tuple[str, str]] = [
    ("cache-secs", "2"), ("demuxer-readahead-secs", "2"), ("cache-pause-wait", "0.5"),
    ("demuxer-lavf-analyzeduration", "0.5"), ("demuxer-lavf-probesize", "500000"),
    ("video-latency-hacks", "yes"),
]
LAVF = (("fflags", "+nobuffer"), ("live_start_index", "-1"))
#: Set by the player for every stream before these (play()): never put back here.
PER_STREAM = frozenset({"cache-secs"})


def wanted(n: Any = None) -> bool:
    n = nerd.current() if n is None else n
    return bool(n.on and n.flag(KEY))


def _pairs(base: Any) -> dict[str, str]:
    if isinstance(base, dict):
        return {str(k): str(v) for k, v in base.items()}
    out: dict[str, str] = {}
    for item in str(base or "").split(","):
        k, sep, v = item.partition("=")
        if k.strip() and sep:
            out[k.strip()] = v.strip()
    return out


def lavf_value(base: Any = None) -> str:
    """demuxer-lavf-o with the low-latency keys added to *base* (what is set
    there already - a dict as mpv reads it back, or key=value,...): flags
    joined, a live_start_index of the user's own kept."""
    items = _pairs(base)
    flags = items.get("fflags", "")
    if "nobuffer" not in flags:
        items["fflags"] = f"{flags}+nobuffer" if flags else "+nobuffer"
    items.setdefault("live_start_index", "-1")
    return ",".join(f"{k}={v}" for k, v in items.items())


def options(base_lavf: Any = None) -> list[tuple[str, str]]:
    return [*OPTIONS, ("demuxer-lavf-o", lavf_value(base_lavf))]


_applied: dict[int, list[str]] = {}


def _forget(key: int) -> None:
    _applied.pop(key, None)


def put_back(mpv: Any, core: dict[str, str] | None = None) -> list[str]:
    """What the last live stream's options replaced, back on *mpv*."""
    keys = _applied.pop(id(mpv), None)
    if not keys:
        return []
    if core is None:
        core = dict(nerd.current().core_options())
    saved = nerd.remembered(mpv)
    back: list[str] = []
    for key in keys:
        if key in PER_STREAM:
            continue
        value: Any = core[key] if key in core else saved.pop(key, None)
        if value is None:
            continue
        try:
            mpv[key] = value
        except Exception:
            try:
                mpv[key] = nerd.UNSET_MEANS[key]
            except Exception as e:
                log.info("low latency: %s not put back: %s", key, e)
                continue
        back.append(key)
    return back


def apply(mpv: Any, live: bool) -> list[str]:
    """Before a stream opens, after the player's own per-stream options:
    with the switch on and *live* (a channel from the provider), the
    low-latency options; otherwise the last ones go back. The keys set."""
    n = nerd.current()
    want = live and wanted(n)
    if not want and id(mpv) not in _applied:
        return []
    core = dict(n.core_options())
    put_back(mpv, core)
    if not want:
        return []
    try:
        base = core.get("demuxer-lavf-o") or mpv["demuxer-lavf-o"]
    except Exception:
        base = None
    pairs = options(base)
    nerd.remember(mpv, [k for k, _v in pairs if k not in PER_STREAM])
    took = nerd.apply_options(mpv, pairs)
    if took:
        if id(mpv) not in _applied:
            try:
                weakref.finalize(mpv, _forget, id(mpv))
            except TypeError:
                pass
        _applied[id(mpv)] = took
        log.info("nerd: low latency for this live channel (%s)", ", ".join(took))
    return took


def check(make_mpv: Callable[[], Any] | None) -> Any:
    """Each option set on a throwaway mpv core: does this player take them."""
    from .nerd_check import OK, Check, check_mpv_options
    result = check_mpv_options("", extra=options(), make_mpv=make_mpv)
    if result.state == OK:
        return Check(OK, "nerd_ck_latency_ok", {"n": len(options())}, result.detail)
    return result
