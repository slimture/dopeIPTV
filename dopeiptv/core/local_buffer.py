"""Use only contiguous ranges that mpv can actually seek within its cache."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any


@dataclass(frozen=True)
class BufferWindow:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start

    def clamp(self, target: float) -> float:
        # Leave a small margin for audio/video packet and keyframe rounding.
        margin = min(0.25, self.duration / 4)
        return max(self.start + margin, min(self.end - margin, target))


# How the rewind buffer of a live channel is sized (Settings -> Playback).
# mpv sizes it in bytes, not time: a minute of an HD channel (~8 Mbit/s), so
# the setting's minutes hold for HD and a lighter channel gets more - the
# DVR row shows the time that actually fits.
BYTES_PER_MINUTE = 60 * 1024 * 1024
# In memory up to this; longer only with the buffer on disk.
MEMORY_MAX_MINUTES = 10


def rewind_bytes(minutes: int, on_disk: bool) -> int:
    """demuxer-max-back-bytes for a live channel's rewind buffer."""
    if not on_disk:
        minutes = min(minutes, MEMORY_MAX_MINUTES)
    return max(0, minutes) * BYTES_PER_MINUTE


# mpv's seekable range ends at the last keyframe it holds, so at the live
# edge the playhead runs ahead of it by up to a keyframe interval: 0.8 s with
# a 2 s one on Linux, and IPTV channels use up to about 5 s. Half a second
# lost the window in one poll of eight there - a dropped arrow key, a DVR row
# that blinked away under the pointer.
LIVE_EDGE_SLACK = 5.0


def buffer_window(state: Any, position: float | None) -> BufferWindow | None:
    """Merge overlapping cache ranges, then choose the one under the playhead."""
    if not isinstance(state, dict) or position is None or not math.isfinite(position):
        return None
    raw = state.get("seekable-ranges")
    if not isinstance(raw, list):
        return None
    ranges = []
    for item in raw:
        try:
            start, end = float(item["start"]), float(item["end"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(start) and math.isfinite(end) and end > start:
            ranges.append((start, end))
    merged: list[tuple[float, float]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    for start, end in merged:
        if (start - 0.5 <= position <= end + LIVE_EDGE_SLACK
                and end - start >= 1):
            return BufferWindow(start, end)
    return None
