"""Nerd: settings of a channel's own.

A live channel - its source's id and its stream's id, the way stream health
keys it - may keep its own preferred audio language, subtitle language (or
none at all), deinterlacing, aspect ratio and audio delay. All of them are
one JSON value in the app's settings (``nerd_channel_prefs``); a choice left
at "Default" is not stored, so a channel without any of its own is not in it.

While Nerd is on they win over the app's settings each time that channel
starts playing, its catch-up too; the player puts the app's own back on
every start, so nothing is left behind for the next channel. With Nerd off
they are kept and not applied.

The audio delay used to be kept per stream on its own (``nerd_adelay/`` and
a checksum of the player's key for the stream, no source in it). Such a
value is moved in here the first time its channel is read.
"""

from __future__ import annotations

import json
import math
import re
import zlib
from typing import Any, Iterable, Protocol

from . import nerd

STORE_KEY = "nerd_channel_prefs"
LEGACY_PREFIX = "nerd_adelay/"
#: The Nerd switch that keeps the player's audio delay per channel.
DELAY_SWITCH = "nerd_adelay_per_channel"
CAP = 2000
#: Seconds either way; more is no lip sync.
MAX_DELAY = 10.0

FIELDS = ("alang", "slang", "deint", "aspect", "adelay")
SUB_OFF = "off"
DEINT = ("on", "off")
#: "auto" is the stream's own shape, where the app's setting asks for another.
ASPECTS = ("auto", "16:9", "4:3", "21:9", "2.35:1", "stretch")

#: The languages offered, by the ISO 639-2 code the app's own language
#: settings use, with the other ways a stream may tag them: the two-letter
#: code first, then the other three-letter one where there are two.
LANGUAGES: dict[str, tuple[str, ...]] = {
    "swe": ("sv",), "eng": ("en",), "nor": ("no", "nob", "nno"), "dan": ("da",),
    "fin": ("fi",), "ger": ("de", "deu"), "fre": ("fr", "fra"), "spa": ("es",),
    "ita": ("it",), "por": ("pt",), "pol": ("pl",), "ara": ("ar",), "tur": ("tr",),
    "dut": ("nl", "nld"), "rus": ("ru",), "ukr": ("uk",), "gre": ("el", "ell"),
    "heb": ("he",), "per": ("fa", "fas"), "urd": ("ur",), "hin": ("hi",),
    "ben": ("bn",), "pan": ("pa",), "tam": ("ta",), "chi": ("zh", "zho"),
    "jpn": ("ja",), "kor": ("ko",), "tha": ("th",), "vie": ("vi",), "ind": ("id",),
    "may": ("ms", "msa"), "swa": ("sw",), "hrv": ("hr",), "srp": ("sr",),
    "bos": ("bs",), "slv": ("sl",), "mac": ("mk", "mkd"), "alb": ("sq", "sqi"),
    "rum": ("ro", "ron"), "hun": ("hu",), "cze": ("cs", "ces"), "slo": ("sk", "slk"),
    "bul": ("bg",), "est": ("et",), "lav": ("lv",), "lit": ("lt",),
    "ice": ("is", "isl"), "kur": ("ku",),
}

_LANG = re.compile(r"[a-z]{2,3}(-[a-z0-9]{2,8})?")


class Readable(Protocol):
    def value(self, key: str, default: Any = None) -> Any: ...


class Settings(Readable, Protocol):
    def setValue(self, key: str, value: Any) -> None: ...  # noqa: N802 - Qt's name


def channel_key(source: object, stream: object) -> str:
    """A channel wherever it is listed: its source's id and its stream's id
    (the same key stream health uses)."""
    return f"{source or ''}:{stream}"


def legacy_key(stream: str) -> str:
    """Where the audio delay of *stream* (the player's key for it, else its
    address) was kept before it moved in here."""
    return f"{LEGACY_PREFIX}{zlib.crc32((stream or '').encode('utf-8')):08x}"


def enabled(n: nerd.Nerd | None = None) -> bool:
    """A channel's own settings apply: Nerd is on."""
    return (n or nerd.current()).on


def delay_enabled(n: nerd.Nerd | None = None) -> bool:
    """Nerd on, and its switch that keeps the audio delay per channel."""
    n = n or nerd.current()
    return n.on and n.flag(DELAY_SWITCH, True)


# -- what a value may be ------------------------------------------------------

def lang(value: Any) -> str:
    """*value* as a language code (lower case), else ""."""
    code = str(value or "").strip().lower()
    return code if _LANG.fullmatch(code) else ""


def spellings(code: str) -> list[str]:
    """Every way a stream may tag the language *code*, *code* first."""
    code = lang(code)
    if not code:
        return []
    out = [code, *LANGUAGES.get(code, ())]
    for main, other in LANGUAGES.items():
        if code in other:
            out += [main, *other]
    return _unique(out)


def delay(value: Any) -> float:
    """*value* as an audio delay in seconds: within MAX_DELAY, to the
    millisecond; 0.0 for anything else."""
    if isinstance(value, bool):
        return 0.0
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(v):
        return 0.0
    v = round(max(-MAX_DELAY, min(MAX_DELAY, v)), 3)
    return 0.0 if abs(v) < 0.001 else v


def clean(prefs: Any) -> dict[str, Any]:
    """Only what a channel may keep, as it is kept; "Default" is not kept."""
    if not isinstance(prefs, dict):
        return {}
    out: dict[str, Any] = {}
    alang = lang(prefs.get("alang"))
    if alang:
        out["alang"] = alang
    slang = str(prefs.get("slang") or "").strip().lower()
    if slang == SUB_OFF or lang(slang):
        out["slang"] = slang
    deint = str(prefs.get("deint") or "").strip().lower()
    if deint in DEINT:
        out["deint"] = deint
    aspect = str(prefs.get("aspect") or "").strip().lower()
    if aspect in ASPECTS:
        out["aspect"] = aspect
    adelay = delay(prefs.get("adelay"))
    if adelay:
        out["adelay"] = adelay
    return out


def parse(raw: Any) -> dict[str, dict[str, Any]]:
    """The stored value back as channels; anything else as none."""
    if not isinstance(raw, (str, bytes)) or not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, prefs in data.items():
        kept = clean(prefs)
        if kept and str(key):
            out[str(key)] = kept
    return out


def _unique(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        v = str(v or "").strip().lower()
        if v and v not in out:
            out.append(v)
    return out


# -- the store ----------------------------------------------------------------

class ChannelPrefs:
    """Every channel's own settings: one settings value, read as it is now
    each time (the window and the player each hold one)."""

    def __init__(self, settings: Settings | None, key: str = STORE_KEY, cap: int = CAP) -> None:
        self.settings = settings
        self.key = key
        self.cap = cap
        self._parsed: tuple[str, dict[str, dict[str, Any]]] = ("", {})

    def entries(self) -> dict[str, dict[str, Any]]:
        """{channel: its settings}, shared: copy before changing."""
        raw = self.settings.value(self.key, "") if self.settings is not None else ""
        raw = raw if isinstance(raw, str) else ""
        if raw != self._parsed[0]:
            self._parsed = (raw, parse(raw))
        return self._parsed[1]

    def get(self, ckey: str | None, legacy: str | None = None) -> dict[str, Any]:
        """*ckey*'s own settings (a copy). *legacy*: where its audio delay
        was kept before - moved in now, if it is still there."""
        if not ckey:
            return {}
        prefs = dict(self.entries().get(ckey) or {})
        if legacy and self.settings is not None:
            old = self.settings.value(legacy, None)
            if old is not None:
                self._remove(legacy)
                moved = delay(old)
                if moved and "adelay" not in prefs:
                    prefs["adelay"] = moved
                    self._write(ckey, prefs)
        return prefs

    def has_own(self, ckey: str | None, legacy: str | None = None) -> bool:
        return bool(self.get(ckey, legacy))

    def put(self, ckey: str, prefs: dict[str, Any]) -> dict[str, Any]:
        """*ckey*'s settings from now on (none left: it has none of its own).
        Returns what is kept."""
        kept = clean(prefs)
        self._write(ckey, kept)
        return kept

    def set_delay(self, ckey: str, seconds: Any) -> dict[str, Any]:
        """The audio delay chosen in the player, kept for *ckey* (0: none)."""
        prefs = dict(self.entries().get(ckey) or {})
        prefs["adelay"] = delay(seconds)
        return self.put(ckey, prefs)

    def reset(self, ckey: str, legacy: str | None = None) -> None:
        """*ckey* back to the app's settings; a delay kept the old way goes too."""
        if legacy:
            self._remove(legacy)
        self._write(ckey, {})

    def _write(self, ckey: str, prefs: dict[str, Any]) -> None:
        if not ckey:
            return
        entries = dict(self.entries())
        entries.pop(ckey, None)
        if prefs:
            entries[ckey] = prefs          # last changed last: the cap keeps it
        if len(entries) > self.cap:
            entries = dict(list(entries.items())[len(entries) - max(0, self.cap):])
        if self.settings is None:
            return
        if entries:
            raw = json.dumps(entries, separators=(",", ":"), ensure_ascii=False)
            self.settings.setValue(self.key, raw)
        else:
            raw = ""
            self._remove(self.key)
        self._parsed = (raw, entries)

    def _remove(self, key: str) -> None:
        if self.settings is None:
            return
        remove = getattr(self.settings, "remove", None)
        if callable(remove):
            remove(key)
        else:
            self.settings.setValue(key, "")


# -- what it asks of mpv ------------------------------------------------------
#
# The player sets the app's defaults on every start (apply_default_options);
# a channel's own settings only change the value each one gets, so with none
# of its own every property is set exactly as before.

def audio_langs(own: dict[str, Any], default: str) -> str:
    """mpv's ``alang``: the channel's language first, each way it may be
    tagged, then the app's own as the fallback - or the app's alone."""
    code = lang(own.get("alang"))
    if not code:
        return default
    return ",".join(_unique([*spellings(code), *str(default or "").split(",")]))


def deinterlace(own: dict[str, Any], default: bool) -> bool:
    """mpv's ``deinterlace``."""
    mode = own.get("deint")
    return mode == "on" if mode in DEINT else default


def aspect_mode(own: dict[str, Any], default: str) -> str:
    """The aspect setting in force: the channel's, else the app's
    (``aspect_mode``: "auto", a ratio or "stretch")."""
    mode = own.get("aspect")
    return mode if mode in ASPECTS else default


def aspect_props(mode: str) -> list[tuple[str, Any]]:
    """mpv's ``keepaspect`` and ``video-aspect-override`` for *mode*, as the
    player sets them for the app's own setting."""
    if mode == "stretch":
        return [("keepaspect", False)]
    return [("keepaspect", True), ("video-aspect-override", mode if mode != "auto" else "-1")]


def subtitle_props(own: dict[str, Any], live: bool, also: Iterable[str] = ()) -> list[tuple[str, Any]]:
    """What the channel's own subtitle choice sets once the app's defaults
    are on: nothing without one. "off" is no track on a live channel and a
    hidden one on its catch-up (which opens like a film: a track is chosen,
    hidden, so showing one later costs no refresh). A language goes first in
    ``slang``, *also* (the app's own) after it; live it shows at once, on a
    catch-up once the tracks are known (``subtitle_rule``)."""
    slang = own.get("slang")
    if not slang:
        return []
    if slang == SUB_OFF:
        return [("sid", "no")] if live else [("sub-visibility", False)]
    langs = ",".join(_unique([*spellings(slang), *also]))
    if live:
        return [("subs-fallback", "default"), ("sid", "auto"), ("slang", langs),
                ("sub-visibility", True)]
    return [("sid", "auto"), ("slang", langs), ("subs-fallback", "yes"),
            ("sub-visibility", False)]


def subtitle_rule(own: dict[str, Any], live: bool) -> tuple[str, list[str]] | None:
    """The catch-up's rule once its tracks are known: show the chosen track
    when it is in the channel's language. None live, or without a language."""
    slang = own.get("slang")
    if live or not slang or slang == SUB_OFF:
        return None
    return ("lang", spellings(slang))


def own_subtitles(own: dict[str, Any]) -> bool:
    """The channel decides its subtitles itself."""
    return bool(own.get("slang"))
