"""Teletext and DVB subtitles as subtitle tracks.

mpv reads both from a transport stream: DVB subtitles (``dvb_subtitle``,
bitmaps) with FFmpeg's own decoder, teletext (``dvb_teletext``) only when
its FFmpeg was built with libzvbi - then as bitmaps of the page set in its
``teletext-page`` option, which can change while the track plays. One
teletext track carries every page of the channel: a subtitle page per
language (199 for Swedish, 333 for Finnish...), named by the PMT's teletext
descriptor. mpv shows the languages of a track ("swe,swe,fin") but not the
pages, so the pages come from the PMT when the app's own rewind buffer has
the stream (core/teletext.py reads it); without it a page is guessed from
the language on an mpv older than 0.38, whose ``teletext-page`` cannot say
"any subtitle page" (0 since 0.38) and defaults to page 100, the index.

Here: what the subtitle menu offers for such a track, which teletext page
a language plays, and the page chosen per channel (``teletext_pages``,
for everyone - not one of Nerd's channel settings, which apply only while
Nerd is on).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Protocol

from . import channel_prefs
from .teletext import (TYPE_HOH, TYPE_SUBTITLE, Stream, is_decimal, page_from_text,
                       page_text)

SETTING = "teletext_pages"
CAP = 500
TELETEXT, DVBSUB = "teletext", "dvbsub"
#: Where subtitles usually are by the language's custom - only for an mpv
#: older than 0.38 without the PMT's pages (it cannot ask for "any").
CUSTOM_PAGE = {"swe": 199, "fin": 333, "nor": 777, "dan": 399, "ger": 150,
               "ita": 777, "pol": 777, "dut": 888, "eng": 888, "fre": 888,
               "spa": 888, "por": 888, "cze": 888, "hun": 888}
DEFAULT_PAGE = 888


class Readable(Protocol):
    def value(self, key: str, default: Any = None) -> Any: ...


class Settings(Readable, Protocol):
    def setValue(self, key: str, value: Any) -> None: ...  # noqa: N802 - Qt's name


@dataclass(frozen=True)
class Choice:
    """One entry of the subtitle menu for a teletext or DVB subtitle track."""
    track: Any                  # mpv's track id
    kind: str                   # TELETEXT or DVBSUB
    lang: str = ""              # as the stream tags it ("swe")
    page: int | None = None     # the teletext page (0x199) when known
    hoh: bool = False           # for the hard of hearing
    pid: int | None = None

    def same_page(self, other: "Choice") -> bool:
        return self.track == other.track and self.page == other.page


def track_kind(track: dict) -> str:
    codec = str(track.get("codec") or "").lower()
    if codec == "dvb_teletext":
        return TELETEXT
    if codec == "dvb_subtitle":
        return DVBSUB
    return ""


def track_langs(track: dict) -> list[str]:
    """A track's languages, each once ("swe,swe,fin" -> swe, fin)."""
    out: list[str] = []
    for part in str(track.get("lang") or "").split(","):
        code = channel_prefs.lang(part)
        if code and code not in ("und", "unk", "mis", "zxx") and code not in out:
            out.append(code)
    return out


def _pid(track: dict) -> int | None:
    try:
        pid = int(track.get("src-id") or 0)
    except (TypeError, ValueError, OverflowError):     # an infinite one too
        return None
    return pid if 0 < pid < 0x1FFF else None


def choices(track: dict, streams: dict[int, Stream] | None = None) -> list[Choice]:
    """What the menu offers for *track* (an entry of mpv's track-list):
    a teletext track's subtitle pages by the PMT (*streams*), else one entry
    per language with no page known; a DVB subtitle track as itself."""
    kind = track_kind(track)
    if not kind:
        return []
    tid, pid = track.get("id"), _pid(track)
    stream = streams.get(pid) if streams and pid is not None else None
    if kind == DVBSUB:
        langs = track_langs(track)
        hoh = bool(track.get("hearing-impaired"))
        if stream is not None and stream.subtitling:
            hoh = hoh or any(0x20 <= e.kind <= 0x24 for e in stream.subtitling)
        return [Choice(tid, DVBSUB, langs[0] if langs else "", None, hoh, pid)]
    out: list[Choice] = []
    if stream is not None:
        for e in stream.teletext:
            if e.kind in (TYPE_SUBTITLE, TYPE_HOH) and is_decimal(e.page):
                c = Choice(tid, TELETEXT, channel_prefs.lang(e.lang), e.page,
                           e.kind == TYPE_HOH, pid)
                if not any(x.same_page(c) for x in out):
                    out.append(c)
        if out:
            return out
    langs = track_langs(track) or [""]
    return [Choice(tid, TELETEXT, code, None, False, pid) for code in langs]


def all_choices(tracks: Iterable[dict], streams: dict[int, Stream] | None = None) -> list[Choice]:
    out: list[Choice] = []
    for t in tracks:
        if t.get("type") == "sub":
            out += choices(t, streams)
    return out


def _matches(code: str, wanted: str) -> bool:
    return bool(code) and code in channel_prefs.spellings(wanted)


def pick(options: Iterable[Choice], langs: Iterable[str]) -> Choice | None:
    """The teletext entry for the first of *langs* that has one: a page for
    everyone before one for the hard of hearing."""
    opts = [c for c in options if c.kind == TELETEXT]
    for wanted in langs:
        if not channel_prefs.lang(wanted):
            continue
        hits = [c for c in opts if _matches(c.lang, wanted)]
        hits.sort(key=lambda c: (c.hoh, c.page is None))
        if hits:
            return hits[0]
    return None


def mpv_version(text: Any) -> tuple[int, int]:
    """(0, 37) for "mpv 0.37.0" or "mpv v0.38.0-dev"; (0, 0) unknown."""
    m = re.search(r"(\d+)\.(\d+)", str(text or ""))
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def any_page_supported(version: tuple[int, int]) -> bool:
    """mpv 0.38 and later read ``teletext-page`` 0 as "any subtitle page"."""
    return version >= (0, 38)


def mpv_page(choice: Choice | None, version: tuple[int, int]) -> int:
    """The value of mpv's ``teletext-page`` for *choice*: its page as a
    number (199), else any subtitle page (0) where mpv knows it, else the
    page the language's channels usually use."""
    if choice is not None and choice.page is not None:
        return int(page_text(choice.page))
    if any_page_supported(version):
        return 0
    lang = choice.lang if choice is not None else ""
    for code, page in CUSTOM_PAGE.items():
        if _matches(lang, code):
            return page
    return DEFAULT_PAGE


def default_page(langs: Iterable[str], version: tuple[int, int]) -> int:
    """``teletext-page`` before a stream opens: the subtitle page of the
    first preferred language by custom on an old mpv (never its default 100,
    the index page drawn over the picture), any subtitle page on a new one."""
    if any_page_supported(version):
        return 0
    for code in langs:
        if channel_prefs.lang(code):
            return mpv_page(Choice(None, TELETEXT, code), version)
    return DEFAULT_PAGE


# -- the page chosen per channel ---------------------------------------------------

class PageStore:
    """The teletext subtitles each channel plays, by the channel key of
    core/channel_prefs (source and stream id): its page ("p": "199"), else
    its language ("l": "swe") when no page was known, and its PID ("s")."""

    def __init__(self, settings: Settings | None, key: str = SETTING, cap: int = CAP) -> None:
        self.settings = settings
        self.key = key
        self.cap = cap

    def _all(self) -> dict[str, dict[str, Any]]:
        if self.settings is None:
            return {}
        raw = self.settings.value(self.key, "")
        try:
            data = json.loads(raw) if isinstance(raw, str) and raw else {}
        except (ValueError, RecursionError):        # a backup's, nested past Python's limit
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, ckey: str | None) -> tuple[int | None, str, int | None] | None:
        """(page, language, pid) kept for *ckey*, or None."""
        if not ckey:
            return None
        entry = self._all().get(ckey)
        if not isinstance(entry, dict):
            return None
        page = page_from_text(str(entry.get("p") or ""))
        lang = channel_prefs.lang(entry.get("l"))
        if page is None and not lang:
            return None
        pid = entry.get("s")
        return page, lang, pid if isinstance(pid, int) and 0 < pid < 0x1FFF else None

    def put(self, ckey: str | None, choice: Choice) -> None:
        if not ckey or self.settings is None or choice.kind != TELETEXT:
            return
        entry: dict[str, Any] = {}
        if choice.page is not None and is_decimal(choice.page):
            entry["p"] = page_text(choice.page)
        elif channel_prefs.lang(choice.lang):
            entry["l"] = channel_prefs.lang(choice.lang)
        else:
            return
        if choice.pid is not None:
            entry["s"] = choice.pid
        data = self._all()
        data.pop(ckey, None)
        data[ckey] = entry                      # last changed last: the cap keeps it
        while len(data) > self.cap:
            data.pop(next(iter(data)))
        self.settings.setValue(self.key, json.dumps(data, separators=(",", ":")))

    def forget(self, ckey: str | None) -> None:
        if not ckey or self.settings is None:
            return
        data = self._all()
        if data.pop(ckey, None) is not None:
            self.settings.setValue(self.key, json.dumps(data, separators=(",", ":"))
                                   if data else "")

    def find(self, ckey: str | None, options: Iterable[Choice]) -> Choice | None:
        """The entry among *options* this channel played last: its page on
        the same PID, else on any teletext track, else - the pages not known -
        a teletext track playing that page; a language kept, its entry."""
        kept = self.get(ckey)
        if kept is None:
            return None
        page, lang, pid = kept
        opts = [c for c in options if c.kind == TELETEXT]
        opts.sort(key=lambda c: pid is not None and c.pid != pid)
        if page is None:
            return pick(opts, [lang])
        for c in opts:
            if c.page == page:
                return c
        for c in opts:
            if c.page is None:
                return Choice(c.track, TELETEXT, c.lang, page, False, c.pid)
        return None
