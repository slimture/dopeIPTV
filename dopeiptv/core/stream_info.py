"""Technical details observed in the playing stream, never probed separately."""
from __future__ import annotations

import json
import math
from typing import Any

KEY = "channel_stream_info"
CAP = 500


def enabled(settings: Any) -> bool:
    return settings is not None and settings.value("show_stream_info", "false") == "true"


def clean(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key, limit in (("width", 32768), ("height", 32768), ("fps", 1000)):
        try:
            n = float(value.get(key, 0))
            if math.isfinite(n) and 0 < n <= limit:
                out[key] = round(n, 3) if key == "fps" else int(n)
        except (ValueError, TypeError, OverflowError):
            pass
    codec = value.get("codec")
    if isinstance(codec, str) and codec and len(codec) <= 32:
        if all(c.isascii() and (c.isalnum() or c in "._- ") for c in codec):
            out["codec"] = codec
    return out


def observed(tracks: Any) -> dict:
    if not isinstance(tracks, list):
        return {}
    for t in tracks:
        if isinstance(t, dict) and t.get("type") == "video" and t.get("selected"):
            return clean({"width": t.get("demux-w") or t.get("width"),
                          "height": t.get("demux-h") or t.get("height"),
                          "fps": t.get("demux-fps"), "codec": t.get("codec")})
    return {}


def summary(value: Any) -> str:
    v = clean(value)
    parts = []
    if v.get("width") and v.get("height"):
        parts.append(f"{v['width']}×{v['height']}")
    if v.get("fps"):
        parts.append(f"{v['fps']:g} fps")
    if v.get("codec"):
        parts.append({"h264": "H.264", "hevc": "HEVC", "h265": "HEVC",
                      "av1": "AV1", "mpeg2video": "MPEG-2"}.get(
                          v["codec"].lower(), v["codec"]))
    return " · ".join(parts)


class StreamInfoCache:
    def __init__(self, settings: Any) -> None:
        self.settings = settings
        try:
            raw = json.loads(settings.value(KEY, "{}"))
        except (ValueError, TypeError):
            raw = {}
        self.data = {k: clean(v) for k, v in raw.items()
                     if isinstance(k, str) and len(k) <= 256 and clean(v)} \
            if isinstance(raw, dict) else {}
        self.data = dict(list(self.data.items())[-CAP:])

    def get(self, key: str | None) -> str:
        return summary(self.data.get(key or "", {}))

    def put(self, key: str, value: dict) -> bool:
        v = clean(value)
        if not key or len(key) > 256 or not v or self.data.get(key) == v:
            return False
        self.data.pop(key, None)
        self.data[key] = v
        self.data = dict(list(self.data.items())[-CAP:])
        self.settings.setValue(KEY, json.dumps(self.data))
        return True
