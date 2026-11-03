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
        if start - 0.5 <= position <= end + 0.5 and end - start >= 1:
            return BufferWindow(start, end)
    return None
