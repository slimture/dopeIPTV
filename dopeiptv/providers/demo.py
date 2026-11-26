"""The demo: a source to try the app with, no account needed.

A few channels - two live test streams around the clock, the Blender
Foundation's open films each on a loop, Apple's test pattern - a guide for
them, and the films themselves under Movies. Everything but the streams is
made here: the channels' logos and the films' posters are drawn by the app
(core/drawn_art.py), the guide is generated for the days around now, the
same for everyone (``guide``), and handed over as a box hands over its own
(``own_guide``, providers/epg.XmltvGuide). Nothing is fetched for it; the
streams' addresses are in one table (providers/demo_streams.py).

The window keeps nothing of it either (ui/main_window.start_demo): its
stores live in memory, and what it plays is kept nowhere - leaving the demo
for a source of one's own leaves nothing of it behind.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from html import escape
from typing import Any, Callable

from ..core import drawn_art
from ..i18n import tr
from . import demo_streams
from .client import OfflineClient

#: The live channels: (stream id, name, its logo's letters, accent, category,
#: stream, what its guide shows). Their guide ids are "demo.<stream>".
CHANNELS: tuple[tuple[int, str, str, str, str, str, str], ...] = (
    (1, "Unified Live", "UL", "teal", "live", "unified", "live"),
    (2, "Unified Live 2", "U2", "blue", "live", "scte35", "live"),
    (3, "Big Buck Bunny 24/7", "BB", "green", "loop", "bbb", "bbb"),
    (4, "Sintel 24/7", "SI", "orange", "loop", "sintel", "sintel"),
    (5, "Tears of Steel 24/7", "TS", "indigo", "loop", "tos", "tos"),
    (6, "Apple Test Card", "AP", "pink", "test", "bipbop", "test"),
)

#: The films: (stream id, title, year, minutes, accent, stream, its plot's key).
FILMS: tuple[tuple[int, str, int, int, str, str, str], ...] = (
    (101, "Big Buck Bunny", 2008, 10, "green", "bbb", "demo_plot_bbb"),
    (102, "Sintel", 2010, 15, "orange", "sintel", "demo_plot_sintel"),
    (103, "Tears of Steel", 2012, 12, "indigo", "tos", "demo_plot_tos"),
)

#: Each film's own length in seconds: its loop's blocks in the guide.
FILM_SECONDS = {"bbb": 596, "sintel": 888, "tos": 734}

#: The categories, in the order they are listed.
LIVE_CATEGORIES = (("demo-live", "demo_cat_live"), ("demo-loop", "demo_cat_loop"),
                   ("demo-test", "demo_cat_test"))
VOD_CATEGORY = ("demo-films", "demo_cat_films")

#: How far the guide reaches around now.
GUIDE_BEFORE_S = 6 * 3600
GUIDE_AFTER_S = 3 * 24 * 3600


def _film(key: str) -> tuple[int, str, int, int, str, str, str] | None:
    return next((f for f in FILMS if f[5] == key), None)


def poster(title: str, year: int, colour: str) -> str:
    """A film's poster as the app draws it."""
    return drawn_art.address("poster", title, colour, f"{year} · Blender Foundation")


def logo(letters: str, colour: str) -> str:
    return drawn_art.address("logo", letters, colour)


def _programmes(what: str, start: int, stop: int) -> list[tuple[int, int, str, str]]:
    """What channel kind *what* shows from *start* to *stop* (whole hours):
    (start, stop, title, description), back to back."""
    out: list[tuple[int, int, str, str]] = []
    film = _film(what)
    if film is not None:
        # A film on a loop, block after block of its own length: it plays
        # again and again (the window loops the file, DemoClient.loops).
        title, year = film[1], film[2]
        desc = tr("demo_prog_loop_desc", title=title, year=year) + " " + tr("demo_credit")
        step = FILM_SECONDS.get(what) or film[3] * 60
        for t in range(start, stop, step):
            out.append((t, t + step, title, desc))
        return out
    if what == "test":
        title, desc = tr("demo_prog_test"), tr("demo_prog_test_desc")
    else:
        title, desc = tr("demo_prog_live"), tr("demo_prog_live_desc")
    for t in range(start, stop, 3600):
        out.append((t, t + 3600, title, desc))
    return out


def _stamp(t: int) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y%m%d%H%M%S +0000")


def guide(now: float | None = None) -> bytes:
    """The demo's guide as XMLTV: every channel from a few hours ago to a few
    days ahead, in whole hours - the same for the same hour, whoever asks."""
    now = time.time() if now is None else now
    hour = int(now) // 3600 * 3600
    start, stop = hour - GUIDE_BEFORE_S, hour + GUIDE_AFTER_S
    parts = ['<?xml version="1.0" encoding="UTF-8"?>\n<tv generator-info-name="dopeIPTV demo">']
    for _sid, name, _l, _c, _cat, key, _what in CHANNELS:
        parts.append(f'<channel id="demo.{key}"><display-name>{escape(name)}</display-name></channel>')
    for _sid, _name, _l, _c, _cat, key, what in CHANNELS:
        for a, b, title, desc in _programmes(what, start, stop):
            parts.append(
                f'<programme start="{_stamp(a)}" stop="{_stamp(b)}" channel="demo.{key}">'
                f'<title>{escape(title)}</title><desc>{escape(desc)}</desc></programme>')
    parts.append("</tv>\n")
    return "\n".join(parts).encode("utf-8")


class DemoClient(OfflineClient):
    """The demo as a source: its channels, its guide and its films, with the
    interface of every client (OfflineClient's empty answers for the rest)."""

    LOG_NAME = "demo"
    #: No "Recently added" among its categories (ui/mw_lists.py): nothing
    #: of it was added by anyone.
    lists_recent = False

    def __init__(self) -> None:
        super().__init__()
        self._live = {sid: demo_streams.STREAMS[key] for sid, *_rest, key, _w in CHANNELS}
        self._films = {f[0]: demo_streams.STREAMS[f[5]] for f in FILMS}

    # -- the channels -------------------------------------------------------------

    def live_categories(self) -> list[dict]:
        return [{"category_id": cid, "category_name": tr(key)} for cid, key in LIVE_CATEGORIES]

    def live_streams(self, category_id: str | None = None) -> list[dict]:
        rows = []
        for sid, name, letters, colour, cat, key, _what in CHANNELS:
            cid = f"demo-{cat}"
            if category_id is not None and str(category_id) != cid:
                continue
            rows.append({"stream_id": sid, "num": sid, "name": name,
                         "stream_type": "live", "stream_icon": logo(letters, colour),
                         "category_id": cid, "epg_channel_id": f"demo.{key}",
                         "tv_archive": 0, "tv_archive_duration": 0})
        return rows

    def live_url(self, stream_id: int | str, fmt: str = "ts") -> str:
        return self._live.get(_int(stream_id), "")

    def loops(self, stream_id: Any) -> bool:
        """Whether channel *stream_id* is a film on a loop: the window has
        the player loop the file (main_window._loop_arm)."""
        sid = _int(stream_id)
        return any(c[0] == sid and _film(c[6]) is not None for c in CHANNELS)

    def own_guide(self, progress_cb: Callable[[int], None] | None = None) -> bytes:
        """The guide, made here (XmltvGuide asks it as a box's own guide)."""
        return guide()

    # -- the films ------------------------------------------------------------------

    def vod_categories(self) -> list[dict]:
        return [{"category_id": VOD_CATEGORY[0], "category_name": tr(VOD_CATEGORY[1])}]

    def vod_streams(self, category_id: str | None = None) -> list[dict]:
        if category_id is not None and str(category_id) != VOD_CATEGORY[0]:
            return []
        return [{"stream_id": sid, "num": i + 1, "name": title, "stream_type": "movie",
                 "stream_icon": poster(title, year, colour), "category_id": VOD_CATEGORY[0],
                 "year": str(year), "runtime": minutes, "container_extension": "m3u8"}
                for i, (sid, title, year, minutes, colour, _key, _plot) in enumerate(FILMS)]

    def vod_info(self, vod_id: int | str) -> dict:
        film = next((f for f in FILMS if f[0] == _int(vod_id)), None)
        if film is None:
            return {}
        sid, title, year, minutes, colour, _key, plot = film
        info: dict[str, Any] = {
            "name": title, "plot": tr(plot) + "\n\n" + tr("demo_credit"),
            "releasedate": str(year), "duration": f"00:{minutes:02d}:00",
            "duration_secs": minutes * 60, "cover_big": poster(title, year, colour)}
        return {"info": info, "movie_data": {"stream_id": sid, "name": title,
                                             "container_extension": "m3u8"}}

    def vod_url(self, stream_id: int | str, ext: str | None = None) -> str:
        return self._films.get(_int(stream_id), "")


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1
