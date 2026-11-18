"""The pictures on the website's front page, from the app itself, in every
language.

Each picture is the real window of this release, driven the way a person
would use it; only what it shows is made up - the sources, channels, guide,
films, series, recordings, files and devices - because nothing of a
provider's or a user's may show. Logos, posters, stills and what plays in
the player are drawn by tools/site_art.py in one style; the space channels
play NASA photographs from tools/showcase/ (public domain). No provider and
no network: the made-up source is served on this computer alone and every
other connection is refused; no user data: the settings, caches and home
folder are a throwaway folder. Where the app would ask a device or a
service (the cast devices, OpenSubtitles), the answer is made up there -
never the widgets.

The pictures, in OUT/<lang>/:

* ``hero-1.webp`` .. ``hero-5.webp`` - the whole window (2560 x 1600: the
  1280 x 800 window at scale 2): the TV place with the player playing, the
  same layout in five themes (two light, three dark), a different channel
  in each - for a crossfade on the front page.
* ``movies.webp`` - the whole window: the Movies place, a film picked.
* ``feat-<key>.webp`` - 1600 x 1000, the part of the window that shows one
  feature of the front page's list, zoomed so its text reads at the size
  the site shows it: ``c1`` catch-up (a channel rewound, the timeshift
  timeline), ``mv`` multiview, ``c2`` live TV paused in the rewind buffer,
  ``c3`` the guide following a category, ``c4`` the Recordings place while
  one records, ``c5`` one search across five sources, ``c6`` the stream's
  information over a large player, ``lf`` Local files' library, ``cast`` a
  channel cast to a TV, ``subs`` subtitles found online, ``keys`` the
  command palette, ``mine`` Mine, ``tv`` TV mode with the phone remote
  beside it (its page from core/phone_page.py, rendered by Chromium through
  Playwright when node has it), ``room`` the places folded and Just the list.
* ``spots.json`` - for each feature picture, where the feature is in it
  (centre x, y and size w, h, as fractions of the picture), measured from
  the widgets in that language: the site zooms toward it and rings it.

Every picture keeps its theme in every language (SCENES). Needs a display
with OpenGL (the video surfaces are OpenGL widgets), Xvfb for ``--jobs``,
ffmpeg (what plays becomes videos for mpv) and fonts for every language's
script (Noto's, e.g. fonts-noto-core; else Hindi, Thai and the like fall
back to a bitmap font)::

    xvfb-run -a -s "-screen 0 2560x1600x24" \\
        python tools/site_screenshots.py OUT [lang ...] [--only scene,...] [--jobs N]

Copy OUT/<lang>/ over ``public/screenshots/<lang>/`` on the website branch.
Each picture is made in a process of its own, from a fresh window; with
``--jobs N`` that many languages run at once, each in an Xvfb of its own
(``--work DIR`` keeps the drawn art and the videos in DIR for the next run).
OUT/contact-en.png is a sheet of the English set, its spots marked, to look
over at a glance.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
SHOWCASE = os.path.join(HERE, "showcase")
sys.path.insert(0, os.path.join(HERE, os.pardir))
sys.path.insert(0, HERE)

SIZE = (1280, 800)
SCALE = 2
FEATURE_SIZE = (1600, 1000)
QUALITY = {"hero": 84, "full": 86, "feat": 82}
SCREEN = "-screen 0 2560x1600x24"

#: The NASA photographs (tools/showcase/), played on the Science channels.
NASA = ("orbit", "cosmos", "planet", "launch")

# -- the made-up catalogue ---------------------------------------------------------------

#: (category id, name, [(channel, logo glyph, logo colour, what it plays)]) -
#: the glyphs and pictures are site_art's.
LINEUP = [
    ("1", "News", [("Northwind News", "globe", "#4C8DFF", "studio"),
                   ("Meridian 24", "signal", "#FF6B57", "studio")]),
    ("2", "Science & Nature", [("Orbit", "orbit", "#123C69", "orbit"),
                               ("Cosmos", "sparkle", "#7C5CFF", "cosmos"),
                               ("Planet Watch", "saturn", "#FF9F43", "planet"),
                               ("Launchpad", "rocket", "#2F64B4", "launch"),
                               ("Wild Earth", "leaf", "#1E9E62", "lake"),
                               ("Deep Blue", "fish", "#1FB5C1", "reef"),
                               ("Summit", "mountain", "#6A5ACD", "peaks"),
                               ("Daybreak", "sun", "#F08A24", "forest"),
                               ("Wings", "bird", "#2BA5E0", "woods"),
                               ("Quantum", "atom", "#8E44AD", "cosmos"),
                               ("Lab Live", "flask", "#16A085", "studio")]),
    ("3", "Sport", [("Arena Sport", "ball", "#1B7A4E", "pitch"),
                    ("Court One", "tennis", "#34CFA0", "court")]),
    ("4", "Movies", [("Cinema Noir", "clapper", "#5B2A86", "skyline"),
                     ("Reel Classics", "reel", "#FF854D", "woods")]),
    ("5", "Kids", [("Pixel Kids", "star", "#F5A623", "cartoon")]),
    ("6", "Music", [("Lumen Music", "note", "#E8508C", "stage")]),
]
TITLES = {
    "News": ["Morning Briefing", "World Today", "Market Watch",
             "The Big Interview", "Evening News", "Late Edition"],
    "Science": ["Earth from Orbit", "Night Sky", "Rings of Saturn",
                "The Giant Planets", "Countdown", "Life on the Station"],
    "Nature": ["Wild Lakes", "Mountain Seasons", "Rivers of the North",
               "Night Forest", "Big Skies", "The Long Migration"],
    "Ocean": ["The Coral Garden", "Ocean Giants", "Harbour Seals", "Kelp Forests",
              "Life in the Shallows", "Deep Reef"],
    "Sport": ["Matchday Live", "Goals & Highlights", "Cycling: Mountain Stage",
              "Sports Desk", "Extra Time", "Transfer Talk"],
    "Tennis": ["Tennis: Semi-final", "Court Report", "Classic Matches",
               "Doubles Live", "Baseline", "Tennis: Semi-final"],
    "Movies": ["Lantern Bay", "Glasshouse City", "Northbound",
               "The Quiet Orchard", "Copper Valley", "Echo Ridge"],
    "Kids": ["Robo Pals", "Tiny Planet", "Story Time", "Space Pups",
             "Drawing Club", "Good Night Show"],
    "Music": ["Live Sessions", "Top 20 Countdown", "Acoustic Hour",
              "Studio Stories", "Festival Nights", "Night Mix"],
}

#: What a channel's programmes are, where its category says two things.
GENRE_OF = {"Science & Nature": "Science", "Wild Earth": "Nature", "Deep Blue": "Ocean",
            "Summit": "Nature", "Daybreak": "Nature", "Wings": "Nature",
            "Court One": "Tennis"}

#: Films: (id, title, motif, year, minutes, rating, genre, category, plot).
FILMS = [
    (101, "Lantern Bay", "lighthouse", 2024, 112, 7.4, "Drama", "11",
     "A lighthouse keeper's daughter comes home to the island she left, "
     "the winter the light is to be switched off for good."),
    (102, "Copper Valley", "desert", 2023, 104, 6.9, "Adventure", "12",
     "Two cousins cross the red hills on an old motorbike to find the "
     "town their grandmother drew on every postcard."),
    (103, "The Paper Moon Club", "moon", 2022, 97, 7.1, "Comedy", "13",
     "Four night-shift bakers start a stargazing club on the roof."),
    (104, "Tidewater", "sea", 2024, 118, 7.6, "Drama", "11",
     "A sailing summer, a lost regatta trophy and a family that only "
     "talks when the wind is up."),
    (105, "Northbound", "peaks", 2021, 109, 7.0, "Adventure", "12",
     "A mountain guide takes one last group over the pass."),
    (106, "Glasshouse City", "city", 2023, 101, 6.8, "Thriller", "11",
     "A night in a city that never switches its lights off."),
    (107, "Summer of Kites", "kites", 2022, 92, 7.3, "Family", "14",
     "The windiest summer in years, and a kite festival to win."),
    (108, "The Quiet Orchard", "orchard", 2021, 106, 7.2, "Drama", "11",
     "An apple farm, three sisters and one last harvest."),
    (109, "Salt & Sky", "island", 2024, 99, 6.7, "Romance", "13",
     "Two strangers share a tiny island for a stormy week."),
    (110, "Echo Ridge", "forest", 2023, 103, 6.9, "Mystery", "12",
     "Hikers hear a song in the woods that no one else can."),
    (111, "Moonrise Diner", "diner", 2022, 94, 7.0, "Comedy", "13",
     "The all-night diner at the edge of town has one rule."),
    (112, "Hot Air", "balloons", 2024, 88, 6.6, "Family", "14",
     "A balloon race across the valley, with a stowaway."),
    (113, "Aurora Lines", "aurora", 2023, 115, 7.5, "Drama", "11",
     "A radio operator in the far north picks up a message."),
    (114, "The Long Thaw", "snow", 2021, 98, 6.8, "Family", "14",
     "A town snowed in for a month finds out what it is made of."),
    (115, "Robo Pals", "robots", 2024, 86, 7.1, "Family", "14",
     "Two little robots run away to see the sea."),
    (116, "After the Sun", "sunset", 2022, 107, 7.2, "Romance", "13",
     "One long evening on the coast road, told twice."),
    (117, "Painted Harbour", "harbour", 2020, 101, 7.3, "Drama", "11",
     "A painter rents the last room above the harbour for one summer."),
]
FILM_CATS = [("11", "Drama"), ("12", "Adventure"), ("13", "Comedy & Romance"),
             ("14", "Family")]
#: Series: (id, title, motif, year, rating, genre, plot).
SERIES = [
    (201, "Harbour Street", "harbour", 2023, 7.8, "Drama",
     "The lives of one bright street of houses above a fishing harbour."),
    (202, "The Keepers", "lighthouse", 2022, 7.5, "Mystery",
     "Three keepers, one lighthouse and a logbook with a page missing."),
    (203, "Starfield Academy", "station", 2024, 7.9, "Sci-fi",
     "Cadets on a space station learn more than how to fly."),
    (204, "Snow Valley", "snow", 2021, 7.2, "Family",
     "A ski town's family-run hotel through one long winter."),
    (205, "Little Robots", "robots", 2024, 7.6, "Kids",
     "Two small robots and the very big world around their garden."),
    (206, "Night Shift Diner", "diner", 2023, 7.4, "Comedy",
     "The regulars of a diner that opens when everything else closes."),
]
EPISODE_TITLES = ["Pilot", "High Tide", "The Visitor", "Lights Out", "Small Hours",
                  "Open Water", "Postcards", "The Long Way Round"]
#: The other sources the Search page finds things in, and what each has.
SOURCES = [
    ("playlist", "My playlist", "xtream"),
    ("server", "Home server", "jellyfin"),
    ("box", "Living room box", "enigma2"),
    ("cable", "City Cable", "m3u"),
    ("portal", "Portal Plus", "stalker"),
]
#: What the other sources have (the Search page finds it there): channels
#: (name, logo glyph, colour, what it plays), films and series by id.
ELSEWHERE = {
    "server": {"live": [], "vod": [117, 101, 104], "series": [201, 202]},
    "box": {"live": [("Harbour TV HD", "fish", "#1FB5C1", "reef"),
                     ("Northwind News HD", "globe", "#4C8DFF", "studio")],
            "vod": [], "series": []},
    "cable": {"live": [("Harbour Local", "signal", "#FF854D", "studio"),
                       ("City One", "clapper", "#5B2A86", "skyline")],
              "vod": [], "series": []},
    "portal": {"live": [], "vod": [117, 112], "series": [201]},
}
#: The made-up thing the Search page is asked for.
SEARCH_WORD = "harbour"

#: Devices on the made-up home network.
CAST_DEVICES = ["Living room TV", "Bedroom TV (DLNA)", "Home theatre (Kodi)"]


#: The channels' order (their numbers): the first eight are what the list
#: shows without scrolling, a mix of what plays - each hero picture plays
#: one of them, so the list stands still from one to the next.
ORDER = ("Northwind News", "Meridian 24", "Arena Sport", "Court One", "Wild Earth",
         "Deep Blue", "Pixel Kids", "Lumen Music", "Orbit", "Cosmos", "Planet Watch",
         "Launchpad", "Summit", "Daybreak", "Wings", "Quantum", "Lab Live",
         "Cinema Noir", "Reel Classics")


#: The channels with catch-up (the provider's archive): a few, as on a
#: real list - the news, the football, the launches and a film channel.
ARCHIVE = ("Northwind News", "Arena Sport", "Launchpad", "Cinema Noir")
#: The channels made favourites: a handful, not every other row.
FAVOURITES = ("Northwind News", "Wild Earth", "Orbit", "Cosmos", "Cinema Noir")


def stream_rows() -> list[dict]:
    rows: list[dict] = []
    for cat_id, cat_name, chans in LINEUP:
        for name, glyph, colour, picture in chans:
            sid = ORDER.index(name) + 1
            rows.append({"stream_id": sid, "num": sid, "name": name,
                         "category_id": cat_id, "epg_channel_id": f"ch{sid}",
                         "tv_archive": int(name in ARCHIVE),
                         "tv_archive_duration": 7 if name in ARCHIVE else 0,
                         "stream_type": "live", "_cat": cat_name,
                         "_glyph": glyph, "_colour": colour, "_picture": picture})
    rows.sort(key=lambda r: r["num"])
    return rows


def guide_xml(streams: list[dict]) -> bytes:
    """A guide from four hours ago to ten ahead."""
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    base = now - timedelta(hours=4, minutes=now.minute % 30)
    fmt = "%Y%m%d%H%M%S +0000"
    out = ['<?xml version="1.0" encoding="UTF-8"?><tv>']
    for s in streams:
        out.append(f'<channel id="{s["epg_channel_id"]}">'
                   f'<display-name>{s["name"]}</display-name></channel>')
    for s in streams:
        genre = GENRE_OF.get(s["name"]) or GENRE_OF.get(s["_cat"]) or s["_cat"]
        titles = TITLES[genre]
        i = s["stream_id"] * 5 + s["stream_id"] // 6
        t = base + timedelta(minutes=15 * (i % 2))
        while t < now + timedelta(hours=10):
            title = titles[i % len(titles)].replace("&", "&amp;")
            dur = timedelta(minutes=(30, 60, 45, 90, 60, 30)[i % 6])
            out.append(
                f'<programme channel="{s["epg_channel_id"]}" '
                f'start="{t.strftime(fmt)}" stop="{(t + dur).strftime(fmt)}">'
                f"<title>{title}</title><desc>{title}.</desc></programme>")
            t += dur
            i += 1
    out.append("</tv>")
    return "".join(out).encode()


# -- the art, drawn once per run ------------------------------------------------------------

def make_assets(work: str) -> None:
    """Every logo, poster, backdrop, still and player picture as files in
    *work*/art, and what plays as videos in *work*/video (run in a process of
    its own: it needs a QGuiApplication)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtGui import QGuiApplication

    import site_art as art
    _app = QGuiApplication(sys.argv)
    folder = os.path.join(work, "art")
    os.makedirs(folder, exist_ok=True)

    def put(img, name: str, fmt: str = "png") -> None:
        if not img.save(os.path.join(folder, f"{name}.{fmt}"), fmt.upper(), 92):
            raise RuntimeError(f"could not write {name}")

    for s in stream_rows():
        put(art.logo(s["_glyph"], s["_colour"]), f"logo-{s['stream_id']}")
    for pid, have in ELSEWHERE.items():
        for i, (_name, glyph, colour, _pic) in enumerate(have["live"]):
            put(art.logo(glyph, colour), f"logo-{pid}-{i}")
    for fid, title, motif, *_ in FILMS:
        put(art.poster(title, motif), f"poster-{fid}", "jpg")
        put(art.scenery(motif, 1920, 1080, 1), f"backdrop-{fid}", "jpg")
    for sid, title, motif, *_ in SERIES:
        put(art.poster(title, motif), f"poster-{sid}", "jpg")
        put(art.scenery(motif, 1920, 1080, 0), f"backdrop-{sid}", "jpg")
        for e in range(8):
            put(art.scenery(motif, 640, 360, e + 2), f"still-{sid}-{e + 1}", "jpg")
    for kind in art.PLAYER_SCENES + ("peaks", "forest"):
        put(art.player_scene(kind), f"play-{kind}", "jpg")
    for name in NASA:
        shutil.copy(os.path.join(SHOWCASE, name + ".jpg"),
                    os.path.join(folder, f"play-{name}.jpg"))
    for fid, *_ in FILMS:
        shutil.copy(os.path.join(folder, f"backdrop-{fid}.jpg"),
                    os.path.join(folder, f"play-film{fid}.jpg"))


def make_videos(work: str) -> None:
    """What plays, as videos for mpv: one three-hour video under each
    player picture's name (the player draws the picture of that name), and
    for the smooth-playback picture a real 1080p50 stream with sound, so the
    stream information says what such a channel's would."""
    art_dir, vid = os.path.join(work, "art"), os.path.join(work, "video")
    os.makedirs(vid, exist_ok=True)
    base = os.path.join(vid, "base.mp4")
    if not os.path.exists(base):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=black:s=640x360:r=2", "-t", "10800", "-c:v", "libx264",
                        "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                        base], check=True)
    for name in sorted(os.listdir(art_dir)):
        if name.startswith("play-"):
            dst = os.path.join(vid, name[len("play-"):-len(".jpg")] + ".mp4")
            if not os.path.exists(dst):
                os.link(base, dst)
    hd = os.path.join(vid, "hd-pitch.mp4")
    if not os.path.exists(hd):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=black:s=1920x1080:r=50", "-f", "lavfi", "-i",
                        "anullsrc=r=48000:cl=stereo", "-t", "120", "-c:v", "libx264",
                        "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-shortest", "-movflags", "+faststart", hd], check=True)


# -- one process: a fresh window, one picture -------------------------------------------------

class Stage:
    """One picture's process: the language, where things are, the
    application and the window, the made-up catalogue, and the spot."""

    def __init__(self, lang: str, out: str, work: str, scene: str, tmp: str) -> None:
        self.lang, self.out, self.work, self.scene, self.tmp = lang, out, work, scene, tmp
        self.art = os.path.join(work, "art")
        self.video = os.path.join(work, "video")
        self.app: Any = None
        self.w: Any = None
        self.settings: Any = None
        self.web = ""
        self.streams: list[dict] = []
        self.films: list[dict] = []
        self.series: list[dict] = []
        #: A file's name -> the player picture it shows (recordings, own files).
        self.alias: dict[str, str] = {}
        self.rec_dir = self.own_dir = ""
        self.own_files: dict[int, str] = {}
        self.spot: tuple[float, float, float, float] | None = None


def _isolate(tmp: str) -> None:
    """Settings, caches and the home folder in *tmp*; no connection but to
    this computer."""
    for var, sub in (("HOME", "home"), ("XDG_CONFIG_HOME", "config"),
                     ("XDG_CACHE_HOME", "cache"), ("XDG_DATA_HOME", "data"),
                     ("XDG_STATE_HOME", "state")):
        path = os.path.join(tmp, sub)
        os.makedirs(path, exist_ok=True)
        os.environ[var] = path
    os.environ["DOPEIPTV_VIDEO_THREAD"] = "0"
    for var in ("DOPEIPTV_TMDB_KEY", "http_proxy", "https_proxy", "HTTP_PROXY",
                "HTTPS_PROXY", "ALL_PROXY", "all_proxy"):
        os.environ.pop(var, None)
    import socket
    local = {"127.0.0.1", "::1", "localhost"}
    connect, connect_ex, getaddrinfo = (socket.socket.connect, socket.socket.connect_ex,
                                        socket.getaddrinfo)

    def far(addr) -> bool:
        return isinstance(addr, tuple) and str(addr[0]) not in local

    def guarded_connect(self, addr):
        if far(addr):
            raise OSError("the showcase reaches no network")
        return connect(self, addr)

    def guarded_connect_ex(self, addr):
        if far(addr):
            return 111
        return connect_ex(self, addr)

    def guarded_getaddrinfo(host, *a, **k):
        if host not in local and host is not None:
            raise socket.gaierror("the showcase reaches no network")
        return getaddrinfo(host, *a, **k)

    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = guarded_getaddrinfo  # type: ignore[assignment]


def one_scene(lang: str, out: str, work: str, scene: str) -> int:
    tmp = tempfile.mkdtemp(prefix="dopeiptv-site-")
    try:
        return _one_scene(lang, out, work, scene, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _one_scene(lang: str, out: str, work: str, scene: str, tmp: str) -> int:
    _isolate(tmp)
    os.environ["QT_SCALE_FACTOR"] = str(SCALE)
    from PyQt6.QtCore import QSettings, Qt
    from PyQt6.QtWidgets import QApplication

    from dopeiptv.core.log import configure_logging
    configure_logging()
    app = QApplication(sys.argv)
    # The style the app sets for itself (dopeiptv/app.py): Fusion, under
    # the stylesheet, its buttons without the platform's icons.
    from PyQt6.QtWidgets import QStyleFactory

    from dopeiptv.app import _NoButtonIconsStyle
    fusion = QStyleFactory.create("Fusion")
    app.setStyle(_NoButtonIconsStyle(fusion if fusion is not None else app.style()))
    app.setApplicationName("dopeIPTV")
    app.setOrganizationName("dopeiptv")
    app.setApplicationDisplayName("dopeIPTV")
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                      os.path.join(tmp, "config"))

    from dopeiptv import i18n
    from dopeiptv.ui.qt_i18n import apply_qt_language
    from dopeiptv.ui.theme import apply_theme, build_style

    spec = SCENES[scene]
    st = Stage(lang, out, work, scene, tmp)
    st.app = app
    settings = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
    st.settings = settings
    for key, value in (("language", lang), ("home_enabled", "false"),
                       ("check_updates", "false"), ("mv_info_seen", "true"),
                       ("donate_ask", "false"),
                       ("docked_video_height", 300), ("start_place", "last"),
                       ("theme", spec["theme"]), ("accent", spec.get("accent", "theme")),
                       ("recordings_dir", os.path.join(tmp, "Recordings"))):
        settings.setValue(key, value)
    for key, value in spec.get("settings", {}).items():
        settings.setValue(key, value)
    i18n.set_language(lang)
    apply_qt_language(app)
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft if i18n.is_rtl(lang)
                           else Qt.LayoutDirection.LeftToRight)
    apply_theme(settings)
    app.setStyleSheet(build_style())
    _build_window(st)
    picture = spec["run"](st)
    _save(st, picture, spec["kind"])
    # Out at once: Qt and mpv tearing down at interpreter exit abort now and
    # then, which would report a picture that is written as failed.
    sys.stdout.flush()
    shutil.rmtree(tmp, ignore_errors=True)
    os._exit(0)


def _build_window(st: Stage) -> None:
    """The catalogue, the stand-ins for what would reach the network, the
    window over them - its first list loaded and the guide in."""
    from PyQt6.QtCore import QRectF, Qt
    from PyQt6.QtGui import QColor, QImage, QPainter

    from dopeiptv.core.stores import PlaylistStore
    from dopeiptv.media import video_surface
    from dopeiptv.providers import client as client_mod
    from dopeiptv.providers import epg as epgmod
    from dopeiptv.providers.client import OfflineClient

    art = st.art
    port = _serve(st.work)
    st.web = web = f"http://127.0.0.1:{port}"
    streams = stream_rows()
    for s in streams:
        s["stream_icon"] = f"{web}/art/logo-{s['stream_id']}.png"
    st.streams = streams

    # What plays is a picture: mpv plays its video, so the player is in the
    # state a real channel puts it in; what it draws is the picture itself.
    pictures: dict[str, QImage] = {}

    def picture_of(path: str) -> QImage | None:
        stem = os.path.splitext(os.path.basename(path.split("?")[0]))[0]
        if stem.startswith("hd-"):
            stem = stem[3:]
        stem = st.alias.get(stem, stem)
        if stem not in pictures:
            pictures[stem] = QImage(os.path.join(art, f"play-{stem}.jpg"))
        return pictures[stem]

    def paint_picture(self) -> None:
        try:
            path = str(self.mpv.path) if self.mpv is not None else ""
        except Exception:
            path = ""
        img = picture_of(path) if path else None
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("black"))
        if img is not None and not img.isNull():
            r = self.rect()
            s = img.size().scaled(r.size(), Qt.AspectRatioMode.KeepAspectRatio)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(QRectF((r.width() - s.width()) / 2, (r.height() - s.height()) / 2,
                               s.width(), s.height()), img)
        p.end()

    video_surface._MpvGLWidget.paintGL = paint_picture  # type: ignore[method-assign]

    films = []
    for fid, title, motif, year, minutes, rating, genre, cat, plot in FILMS:
        films.append({"stream_id": fid, "num": fid, "name": title, "title": title,
                      "year": str(year), "rating": f"{rating:.1f}",
                      "stream_icon": f"{web}/art/poster-{fid}.jpg",
                      "category_id": cat, "stream_type": "movie",
                      "container_extension": "mp4", "genre": genre,
                      "added": str(int(time.time() - (fid * 37 % 90) * 86400)),
                      "_motif": motif, "_minutes": minutes, "_plot": plot})
    st.films = films
    series = []
    for sid, title, motif, year, rating, genre, plot in SERIES:
        series.append({"series_id": sid, "num": sid, "name": title, "title": title,
                       "year": str(year), "rating": f"{rating:.1f}",
                       "cover": f"{web}/art/poster-{sid}.jpg",
                       "stream_icon": f"{web}/art/poster-{sid}.jpg",
                       "backdrop_path": [f"{web}/art/backdrop-{sid}.jpg"],
                       "category_id": "21", "genre": genre, "plot": plot,
                       "last_modified": str(int(time.time() - sid % 5 * 86400)),
                       "_motif": motif})
    st.series = series

    def video_of(stem: str) -> str:
        return f"{web}/video/{stem}.mp4"

    class Showcase(OfflineClient):
        """The made-up source: its lists, guide and streams."""

        def __init__(self, label: str = "playlist") -> None:
            super().__init__()
            self.label = label
            self.server = f"http://{label}.example"
            self.own = ELSEWHERE.get(label)

        def _lives(self) -> list[dict]:
            if self.own is None:
                return streams
            return [{"stream_id": 500 + i, "num": i + 1, "name": name, "category_id": "1",
                     "epg_channel_id": "", "stream_type": "live",
                     "stream_icon": f"{web}/art/logo-{self.label}-{i}.png",
                     "_picture": pic}
                    for i, (name, _g, _c, pic) in enumerate(self.own["live"])]

        def live_categories(self) -> list[dict]:
            if self.own is not None:
                return [{"category_id": "1", "category_name": "TV"}] if self.own["live"] else []
            return [{"category_id": c, "category_name": n} for c, n, _ in LINEUP]

        def live_streams(self, category_id=None) -> list[dict]:
            return [{k: v for k, v in s.items() if not k.startswith("_")}
                    for s in self._lives() if category_id in (None, "", s["category_id"])]

        def _films(self) -> list[dict]:
            if self.own is None:
                return [f for f in films if f["stream_id"] != 117]
            return [f for f in films if f["stream_id"] in self.own["vod"]]

        def vod_categories(self) -> list[dict]:
            return [{"category_id": c, "category_name": n} for c, n in FILM_CATS]

        def vod_streams(self, category_id=None) -> list[dict]:
            return [{k: v for k, v in f.items() if not k.startswith("_")}
                    for f in self._films() if category_id in (None, "", f["category_id"])]

        def vod_info(self, vod_id) -> dict:
            f = next((f for f in films if str(f["stream_id"]) == str(vod_id)), None)
            if f is None:
                return {}
            return {"info": {"name": f["name"], "plot": f["_plot"], "genre": f["genre"],
                             "duration_secs": f["_minutes"] * 60,
                             "duration": f"{f['_minutes'] // 60:02d}:{f['_minutes'] % 60:02d}:00",
                             "releasedate": f"{f['year']}-05-14", "rating": f["rating"],
                             "cast": "Mira Holm, Tomas Brandt, Ada Okafor",
                             "director": "Lena Fischer",
                             "movie_image": f["stream_icon"],
                             "backdrop_path": [f"{web}/art/backdrop-{f['stream_id']}.jpg"]},
                    "movie_data": {"stream_id": f["stream_id"], "name": f["name"],
                                   "container_extension": "mp4"}}

        def series_categories(self) -> list[dict]:
            return [{"category_id": "21", "category_name": "Series"}]

        def series_list(self, category_id=None) -> list[dict]:
            mine = series if self.own is None else [
                x for x in series if x["series_id"] in self.own["series"]]
            return [{k: v for k, v in s.items() if not k.startswith("_")}
                    for s in mine if category_id in (None, "", "21")]

        def series_info(self, series_id) -> dict:
            s = next((s for s in series if str(s["series_id"]) == str(series_id)), None)
            if s is None:
                return {}
            eps: dict[str, list] = {}
            for season in (1, 2):
                eps[str(season)] = [{
                    "id": str(s["series_id"] * 100 + season * 10 + e),
                    "episode_num": e, "season": season,
                    "title": EPISODE_TITLES[(e - 1 + season) % len(EPISODE_TITLES)],
                    "container_extension": "mp4",
                    "info": {"duration_secs": 2700 + e * 60,
                             "plot": "Nothing is quite what it seems this week.",
                             "movie_image":
                                 f"{web}/art/still-{s['series_id']}-{(e + season * 3) % 8 + 1}.jpg"}}
                    for e in range(1, 9)]
            return {"info": {"name": s["name"], "plot": s["plot"], "genre": s["genre"],
                             "cover": s["cover"], "rating": s["rating"],
                             "releaseDate": f"{s['year']}-09-01",
                             "backdrop_path": s["backdrop_path"]},
                    "seasons": [{"season_number": n, "name": f"Season {n}"} for n in (1, 2)],
                    "episodes": eps}

        def live_url(self, stream_id, fmt="ts") -> str:
            if self.own is not None:
                s = self._lives()[int(stream_id) - 500]
                return video_of(s["_picture"])
            s = streams[int(stream_id) - 1]
            if st.scene == "feat-c6" and s["_picture"] == "pitch":
                return video_of("hd-pitch")
            return video_of(s["_picture"])

        def vod_url(self, stream_id, ext=None) -> str:
            return video_of(f"film{stream_id}")

        def episode_url(self, episode_id, ext=None) -> str:
            return video_of("film" + str(FILMS[int(episode_id) % len(FILMS)][0]))

        def timeshift_urls(self, stream_id, start_dt, duration_min) -> list[str]:
            return [self.live_url(stream_id)]

    st.Showcase = Showcase
    xml = guide_xml(streams)
    epgmod.XmltvGuide.PARSE_ELSEWHERE = False
    epgmod.XmltvGuide._download = lambda self: xml  # type: ignore[method-assign]
    # Another source's client (the Search page asks each one) is this one.
    client_mod.make_client = lambda pl, *a, **k: Showcase(str(pl.get("id") or ""))

    settings = st.settings
    store = PlaylistStore(settings)
    for pid, name, kind in SOURCES:
        store.add({"id": pid, "name": name, "kind": kind,
                   "server": f"http://{pid}.example", "username": "showcase",
                   "password": "showcase", "epg_url": "", "refresh": "never"})
    store.set_active("playlist")

    _prepare_files(st)
    settings.setValue("local_dirs", json.dumps([st.own_dir]))
    SCENES[st.scene].get("before", lambda _st: None)(st)

    from dopeiptv.ui.main_window import MainWindow
    w = MainWindow(Showcase(), settings, store)
    st.w = w
    w.cover.resolver = None
    w.resize(*SIZE)
    w.move(0, 0)
    w.show()
    _seed(st)
    spin(st, 2)
    w._ensure_xmltv_loaded()
    wait(st, lambda: w.list_model.rowCount() > 0, 10)
    spin(st, 1)


def _serve(folder: str) -> int:
    """The made-up source's streams, logos and posters, served on this
    computer alone - read by the app as a provider's (it asks an archive
    address over HTTP before it plays it, the phone remote shows a logo
    from a web address)."""
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *_a) -> None:
            pass

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address) -> None:
            pass                # a player that read enough and hung up

    server = Server(("127.0.0.1", 0), partial(Quiet, directory=folder))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1]


#: Recordings: (title, channel, days ago, start hour, minutes, picture).
RECORDINGS = [
    ("Rings of Saturn", "Cosmos", 0, 7, 60, "cosmos"),
    ("Matchday Live", "Arena Sport", 0, 6, 105, "pitch"),
    ("The Coral Garden", "Deep Blue", 1, 20, 45, "reef"),
    ("Evening News", "Northwind News", 1, 19, 30, "studio"),
    ("Festival Nights", "Lumen Music", 3, 22, 90, "stage"),
    ("Robo Pals", "Pixel Kids", 4, 17, 25, "cartoon"),
    ("Wild Lakes", "Wild Earth", 5, 21, 50, "lake"),
    ("Glasshouse City", "Cinema Noir", 9, 21, 101, "skyline"),
]
#: Films of one's own (Local files): film ids, and how far two of them got.
OWN_FILMS = [105, 107, 108, 110, 111, 113, 114, 116, 109, 103]
OWN_UNDER_WAY = {113: 0.36, 105: 0.62}


def _sized_copy(src: str, dst: str, size: int) -> None:
    """*src* copied to *dst*, made *size* bytes long without taking the
    room (a sparse file): a recording's or a film's size as listed."""
    shutil.copyfile(src, dst)
    with open(dst, "r+b") as fh:
        fh.truncate(size)


def _prepare_files(st: Stage) -> None:
    """The recordings folder and a folder of films of one's own, made up:
    each a video (the player draws the picture of its name), with
    what the app keeps beside it - a recording's sidecar, the frame and
    length the app found of it."""
    import hashlib
    from datetime import datetime as dt
    base = os.path.join(st.video, "base.mp4")
    from dopeiptv.core.workers import default_image_cache_dir
    thumbs = str(default_image_cache_dir("thumbs"))
    os.makedirs(thumbs, exist_ok=True)
    rec = os.path.join(st.tmp, "Recordings")
    os.makedirs(rec, exist_ok=True)
    st.rec_dir = rec
    by = {s["name"]: s for s in st.streams}
    today = dt.now().replace(minute=0, second=0, microsecond=0)
    for title, channel, ago, hour, minutes, pic in RECORDINGS:
        start = (today - timedelta(days=ago)).replace(hour=hour)
        if start > dt.now():
            start -= timedelta(days=1)
        stem = f"{title} {start:%Y-%m-%d %H.%M}"
        path = os.path.join(rec, stem + ".ts")
        _sized_copy(base, path, minutes * 26_000_000)
        when = start.timestamp() + minutes * 60
        os.utime(path, (when, when))
        with open(path + ".info.json", "w", encoding="utf-8") as fh:
            json.dump({"title": "", "description": "", "icon": by[channel]["stream_icon"],
                       "channel": channel, "start": int(start.timestamp())}, fh)
        key = hashlib.sha1(path.encode("utf-8")).hexdigest()
        shutil.copy(os.path.join(st.art, f"play-{pic}.jpg"), os.path.join(thumbs, key + ".jpg"))
        with open(os.path.join(thumbs, key + ".len"), "w") as fh:
            fh.write(f"{minutes * 60:.1f}")
        st.alias[stem] = pic
    own = os.path.join(st.tmp, "home", "Videos", "Films")
    os.makedirs(own, exist_ok=True)
    st.own_dir = os.path.dirname(own)
    for fid in OWN_FILMS:
        f = next(x for x in FILMS if x[0] == fid)
        stem = f"{f[1]} ({f[3]})"
        path = os.path.join(own, stem + ".mkv")
        _sized_copy(base, path, f[4] * 21_000_000)
        key = hashlib.sha1(path.encode("utf-8")).hexdigest()
        shutil.copy(os.path.join(st.art, f"poster-{fid}.jpg"), os.path.join(thumbs, key + ".jpg"))
        st.alias[stem] = f"film{fid}"
        st.own_files[fid] = path


def _seed(st: Stage) -> None:
    """What a person who has used the app a while has: history, favourites,
    films and episodes under way, Watch later."""
    from dopeiptv.core.stores import FAV_DEFAULT_GROUP
    w = st.w
    now = time.time()
    by = {s["name"]: s for s in st.streams}
    for name in ("Pixel Kids", "Arena Sport", "Wild Earth", "Northwind News",
                 "Cosmos", "Orbit"):
        s = by[name]
        row = {k: v for k, v in s.items() if not k.startswith("_")}
        w.history.add(w.client.live_url(s["stream_id"]), s["name"], s["stream_icon"],
                      s["stream_id"], "live", row)
    for name in FAVOURITES:
        s = by[name]
        w.favs.add(FAV_DEFAULT_GROUP, {k: v for k, v in s.items() if not k.startswith("_")})
    for f, frac in ((st.films[0], 0.42), (st.films[3], 0.7), (st.films[6], 0.25)):
        dur = f["_minutes"] * 60.0
        w.resume.record("vod", f["stream_id"], dur * frac, dur,
                        {k: v for k, v in f.items() if not k.startswith("_")})
    # An episode under way, its series' name and art with it.
    sh = st.series[0]
    ep = {"id": str(sh["series_id"] * 100 + 13), "name": "S1 E3 · The Visitor",
          "title": "The Visitor", "season": 1, "episode_num": 3, "container_extension": "mp4",
          "stream_icon": sh["cover"]}
    w.resume.record("episode", ep["id"], 1300, 2880, ep,
                    {k: sh[k] for k in ("series_id", "name", "cover", "category_id")})
    for fid in (113, 102, 107):
        f = next(x for x in st.films if x["stream_id"] == fid)
        w.watchlist.add_movie_local({k: v for k, v in f.items() if not k.startswith("_")}, None)
    for sid in (203, 206):
        x = next(x for x in st.series if x["series_id"] == sid)
        w.watchlist.add_show_local({k: v for k, v in x.items() if not k.startswith("_")}, None)
    for f in st.films[:2]:
        w.movie_favs.add(FAV_DEFAULT_GROUP, {k: v for k, v in f.items() if not k.startswith("_")})
    # Films of one's own under way (Local files' Continue).
    for fid, frac in OWN_UNDER_WAY.items():
        f = next(x for x in FILMS if x[0] == fid)
        w.resume.record("local", st.own_files[fid], f[4] * 60 * frac, f[4] * 60.0)
    # How the channels were watched these last weeks: the news at this hour
    # on weekdays, the football at weekends, the kids' channel in the
    # afternoon - what "Usually watched now" is read from.
    try:
        from dopeiptv.core import viewing_stats as vs
        log = w._stats_store()
        source = w._stats_source()
        for days in range(1, 22):
            t = now - days * 86400
            for name, hour_off, secs in (("Northwind News", 0, 1500), ("Arena Sport", 2, 2400),
                                         ("Pixel Kids", -4, 1800), ("Wild Earth", 1, 1200)):
                if name == "Northwind News" and days % 7 in (0, 6):
                    continue
                s = by[name]
                log._put(vs.ident(vs.CHANNEL, source, s["stream_id"]), name, vs.CHANNEL,
                         secs, t + hour_off * 3600 + 600)
        log.save(force=True)
    except Exception as e:
        print("viewing log not seeded:", e, file=sys.stderr)


# -- waiting, finding, cropping ---------------------------------------------------------------

def spin(st: Stage, sec: float) -> None:
    end = time.time() + sec
    while time.time() < end:
        st.app.processEvents()
        time.sleep(0.02)


def wait(st: Stage, cond, sec: float = 10.0) -> bool:
    end = time.time() + sec
    while time.time() < end:
        try:
            if cond():
                st.app.processEvents()
                return True
        except Exception:
            pass
        st.app.processEvents()
        time.sleep(0.03)
    return False


def settle(st: Stage, sec: float = 1.0) -> None:
    """Let the window's timers run, the players redrawn meanwhile."""
    from dopeiptv.media import video_surface
    end = time.time() + sec
    while time.time() < end:
        for vid in st.w.findChildren(video_surface._MpvGLWidget):
            vid.update()
        spin(st, 0.2)


def no_focus(st: Stage) -> None:
    from PyQt6.QtWidgets import QApplication
    f = QApplication.focusWidget()
    if f is not None:
        f.clearFocus()


def item(st: Stage, name: str) -> dict:
    return next(x for x in st.w.all_items if x.get("name") == name)


def select(st: Stage, name: str) -> None:
    w = st.w
    for r in range(w.list_model.rowCount()):
        x = w.list_model.item_at(r)
        if x and x.get("name") == name:
            w.listw.setCurrentIndex(w.list_model.index(r, 0))
            return


def pick_category(st: Stage, name: str) -> None:
    from PyQt6.QtCore import Qt
    w = st.w
    for i in range(w.cat_list.count()):
        it = w.cat_list.item(i)
        if it.text().strip() == name or str(it.data(Qt.ItemDataRole.UserRole)) == name:
            w.cat_list.setCurrentRow(i)
            return
    raise LookupError(f"no category {name!r}")


def play(st: Stage, name: str, sec: float = 3.0) -> None:
    select(st, name)
    spin(st, 0.3)
    st.w.play_live_channel(item(st, name))
    spin(st, sec)


def rect_in_window(st: Stage, widget, rect=None):
    """*widget*'s rectangle (or *rect* in its coordinates) in the window's."""
    from PyQt6.QtCore import QPoint, QRect
    r = rect if rect is not None else widget.rect()
    top = widget.mapTo(st.w, QPoint(r.x(), r.y()))
    return QRect(top.x(), top.y(), r.width(), r.height())


def grab(st: Stage):
    from PyQt6.QtWidgets import QApplication
    QApplication.processEvents()
    return st.w.grab().toImage()


def feature(st: Stage, img, crop, spot, bounds: tuple[int, int] = SIZE) -> tuple:
    """A feature picture: *crop* (x, y, w, h in the logical pixels of what
    was grabbed - the window, else a window *bounds* big - made 16:10
    around its centre and kept inside it) of the grabbed *img*, and *spot*
    (a QRect there) as fractions of it."""
    x, y, cw, ch = crop
    if cw / ch > 1.6:
        nh = cw / 1.6
        y -= (nh - ch) / 2
        ch = nh
    else:
        nw = ch * 1.6
        x -= (nw - cw) / 2
        cw = nw
    cw, ch = min(cw, bounds[0]), min(ch, bounds[1])
    if cw / ch > 1.6:
        cw = ch * 1.6
    else:
        ch = cw / 1.6
    x = max(0.0, min(x, bounds[0] - cw))
    y = max(0.0, min(y, bounds[1] - ch))
    st.crop = (x, y, cw, ch)
    if spot is not None:
        sx = (spot.x() + spot.width() / 2 - x) / cw
        sy = (spot.y() + spot.height() / 2 - y) / ch
        st.spot = (round(min(max(sx, 0.0), 1.0), 4), round(min(max(sy, 0.0), 1.0), 4),
                   round(min(spot.width() / cw, 1.0), 4), round(min(spot.height() / ch, 1.0), 4))
    return img, (x, y, cw, ch)


def span(st: Stage, *widgets, top: int | None = None) -> tuple:
    """The crop over *widgets* side by side - whichever side each is on in
    the language's direction - from *top* (else the highest of them) down,
    as tall as 16:10 makes their width."""
    rects = [rect_in_window(st, w) for w in widgets]
    left = min(r.x() for r in rects)
    right = max(r.x() + r.width() for r in rects)
    y = min(r.y() for r in rects) if top is None else top
    return (left, y, right - left, (right - left) / 1.6)


def _save(st: Stage, picture, kind: str) -> None:
    from PyQt6.QtCore import QRect, Qt
    folder = os.path.join(st.out, st.lang)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, st.scene + ".webp")
    if kind == "feat":
        img, (x, y, cw, ch) = picture
        dpr = img.devicePixelRatio() or 1.0
        part = img.copy(QRect(round(x * dpr), round(y * dpr), round(cw * dpr), round(ch * dpr)))
        part.setDevicePixelRatio(1.0)
        img = part.scaled(FEATURE_SIZE[0], FEATURE_SIZE[1],
                          Qt.AspectRatioMode.IgnoreAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
        spot_dir = os.path.join(folder, ".spots")
        os.makedirs(spot_dir, exist_ok=True)
        if st.spot is None:
            raise RuntimeError(f"{st.scene}: no spot")
        x0, y0, w0, h0 = st.spot
        with open(os.path.join(spot_dir, st.scene[len("feat-"):] + ".json"), "w") as fh:
            json.dump({"x": x0, "y": y0, "w": w0, "h": h0}, fh)
    else:
        img = picture
    if not img.save(path, "webp", QUALITY[kind]):
        raise RuntimeError(f"could not write {path}")


# -- the scenes ------------------------------------------------------------------------------

def fit_player(st: Stage) -> None:
    """The docked player's height dragged to fit the picture (16:9)."""
    w = st.w
    handle = getattr(w, "_player_resize", None)
    if handle is None or w.player is None:
        return
    handle._preferred = max(120, round(w.player.video.width() * 9 / 16))
    handle.apply_height()
    spin(st, 0.3)


def tv_place(st: Stage, channel: str) -> None:
    """The TV place, every channel, *channel* playing docked."""
    w = st.w
    w._show_place("tv")
    spin(st, 0.5)
    w.cat_list.setCurrentRow(0)                 # All
    spin(st, 1.5)
    play(st, channel)
    fit_player(st)
    no_focus(st)
    settle(st, 1.5)


def hero(channel: str):
    def run(st: Stage):
        tv_place(st, channel)
        return grab(st)
    return run


def movies(st: Stage):
    """The Movies place as posters, a film picked: the detail column."""
    w = st.w
    w._show_place("films")
    wait(st, lambda: w.list_model.rowCount() > 0, 10)
    spin(st, 1)
    w.cat_list.setCurrentRow(0)          # All
    spin(st, 2)
    select(st, "Tidewater")
    spin(st, 2)
    no_focus(st)
    settle(st, 1)
    return grab(st)


def big_player(st: Stage) -> None:
    """Focus mode: the list folded away, the player given its room, its
    height dragged to the picture's."""
    w = st.w
    w.focus_btn.click()
    spin(st, 0.6)
    fit_player(st)
    spin(st, 0.4)


def player_crop(st: Stage, below: int = 56) -> tuple:
    """The docked player's picture and its control bar."""
    pl = st.w.player
    v = rect_in_window(st, pl.video)
    return (v.x(), v.y(), v.width(), v.height() + below)


def feat_c1(st: Stage):
    """Catch-up: a live channel rewound 28 minutes, the timeline over it."""
    w = st.w
    tv_place(st, "Launchpad")
    big_player(st)
    w._play_timeshift(item(st, "Launchpad"), back_min=28)
    pl = w.player
    wait(st, lambda: getattr(w, "_playing_catchup", False), 10)
    spin(st, 2.5)
    w._update_ts_timeline()
    pl._show_ts_timeline()
    pl._ts_hide_timer.stop()
    no_focus(st)
    settle(st, 1)
    w._update_ts_timeline()
    spin(st, 0.3)
    img = grab(st)
    spot = _slider_handle(st, pl.ts_slider)
    return feature(st, img, player_crop(st), spot)


def _slider_handle(st: Stage, slider):
    """The rectangle of a slider's handle, in the window."""
    from PyQt6.QtCore import QRect
    from PyQt6.QtWidgets import QStyle, QStyleOptionSlider
    opt = QStyleOptionSlider()
    slider.initStyleOption(opt)
    r = slider.style().subControlRect(QStyle.ComplexControl.CC_Slider, opt,
                                      QStyle.SubControl.SC_SliderHandle, slider)
    if not r.isValid() or r.width() <= 0:
        span = max(1, slider.maximum() - slider.minimum())
        frac = (slider.value() - slider.minimum()) / span
        if slider.isRightToLeft():
            frac = 1 - frac
        x = round(frac * slider.width())
        r = QRect(x - 8, 0, 16, slider.height())
    big = r.adjusted(-14, -10, 14, 10)
    return rect_in_window(st, slider, big)


def feat_mv(st: Stage):
    """Multiview: four channels in their window, over the app."""
    from PyQt6.QtCore import QPoint, QRect
    from PyQt6.QtGui import QPainter

    from dopeiptv.media import video_surface
    w = st.w
    tv_place(st, "Wild Earth")
    for n, name in enumerate(("Arena Sport", "Deep Blue", "Cosmos", "Pixel Kids")):
        w._add_channel_to_multiview(item(st, name), n)
        spin(st, 0.5)
    mv = w._multiview_win
    mv.resize(800, 470)
    mv.move(240, 150)
    spin(st, 3)
    cells = list(getattr(mv, "cells", []))
    if cells:
        mv._focus_cell(cells[0])            # the one with the sound
    for cell in cells:
        for vid in cell.findChildren(video_surface._MpvGLWidget):
            vid.update()
    mv._reveal_overlays()
    spin(st, 1)
    base = grab(st)
    top = mv.grab().toImage()
    at = mv.mapToGlobal(QPoint(0, 0)) - w.mapToGlobal(QPoint(0, 0))
    spot = None
    if cells:
        c = cells[0]
        g = c.mapTo(mv, QPoint(0, 0))
        spot = QRect(at.x() + g.x(), at.y() + g.y(), c.width(), c.height())
    mw, mh = mv.width(), mv.height()
    mv.close()
    spin(st, 0.5)
    dpr = base.devicePixelRatio() or 1.0
    p = QPainter(base)
    p.drawImage(QRect(round(at.x()), round(at.y()), round(top.width() / dpr),
                      round(top.height() / dpr)), top)
    p.end()
    return feature(st, base, (at.x() - 20, at.y() - 20, mw + 40, mh + 40), spot)


def feat_c2(st: Stage):
    """Pause live TV: the channel paused in the rewind buffer, 4:12 behind."""
    from dopeiptv.core.local_buffer import BufferWindow
    w = st.w
    tv_place(st, "Court One")
    big_player(st)
    pl = w.player
    held: dict = {}

    def window():
        m = pl.video.mpv
        pos = float(m.playback_time or 0.0)
        if "end" in held:
            return BufferWindow(held["end"] - 1800.0, held["end"])
        return BufferWindow(pos + 10.0 - 1800.0, pos + 10.0)
    # mpv's own buffer as a long live channel's would be: half an hour of it
    # behind the picture, ten seconds read ahead - and then paused, four
    # minutes since.
    pl.local_buffer_window = window  # type: ignore[method-assign]
    spin(st, 1.5)
    pl.pause_btn.click()
    spin(st, 0.8)
    m = pl.video.mpv
    held["end"] = float(m.playback_time or 0.0) + 252.0 + 10.0
    spin(st, 1.2)
    pl._reveal_local_buffer()
    pl._local_buffer_hide_timer.stop()
    no_focus(st)
    settle(st, 0.8)
    img = grab(st)
    spot = rect_in_window(st, pl.local_buffer_time).adjusted(-8, -6, 8, 6)
    return feature(st, img, player_crop(st), spot)


def feat_c6(st: Stage):
    """Smooth playback: the player large, what the stream is said at the top."""
    w = st.w
    tv_place(st, "Wild Earth")
    big_player(st)
    play(st, "Arena Sport", 3.5)
    pl = w.player
    wait(st, lambda: pl.stream_info_badge.isVisible(), 6)
    pl._info_hide.stop()
    no_focus(st)
    settle(st, 0.8)
    img = grab(st)
    spot = rect_in_window(st, pl.stream_info_badge).adjusted(-6, -6, 6, 6)
    return feature(st, img, player_crop(st), spot)


def on_air_cell(st: Stage, grid, channel: str):
    """The guide's card of what *channel* shows now, in the window."""
    from PyQt6.QtCore import QPoint, QRect
    now = time.time()
    for it in grid.scene.items():
        data = it.data(0)
        if not isinstance(data, dict) or not data.get("prog"):
            continue
        ch, prog = data.get("channel") or {}, data["prog"]
        if ch.get("name") != channel:
            continue
        if not (prog.get("start_timestamp", 0) <= now < prog.get("stop_timestamp", 0)):
            continue
        r = grid.view.mapFromScene(it.sceneBoundingRect()).boundingRect()
        vp = grid.view.viewport()
        top = vp.mapTo(st.w, QPoint(r.x(), r.y()))
        vis = QRect(top.x(), top.y(), r.width(), r.height())
        return vis.intersected(rect_in_window(st, vp))
    return None


def feat_c3(st: Stage):
    """The guide on its page, following Science & Nature picked in the sidebar."""
    w = st.w
    tv_place(st, "Cosmos")
    pick_category(st, "Science & Nature")
    spin(st, 1.5)
    select(st, "Cosmos")
    w._open_epg_guide()
    spin(st, 2.5)
    grid = w._guide_grid
    bar = grid.view.horizontalScrollBar()
    # Scrolled back a little, as one does, so what is on now shows its name.
    bar.setValue(max(0, bar.value() - round(grid._x(time.time()) - grid._x(time.time() - 1800))))
    spin(st, 1)
    no_focus(st)
    settle(st, 1)
    img = grab(st)
    page = rect_in_window(st, w._guide_page)
    spot = on_air_cell(st, grid, "Cosmos") or page
    return feature(st, img, span(st, w._guide_page, w._det), spot)


def feat_room(st: Stage):
    """Room for the list: the places folded into rows of icons, Just the list."""
    w = st.w
    tv_place(st, "Northwind News")
    img = grab(st)
    from PyQt6.QtCore import QRect
    box = getattr(w, "_places_box", None)
    spot = rect_in_window(st, box) if box is not None else QRect(10, 60, 220, 110)
    return feature(st, img, span(st, w._side, w._mid), spot)


def dialogs_stay_open(*classes) -> list:
    """A dialog opened with exec() shown and left open instead (the
    picture is taken while it is up); each one opened is collected."""
    opened: list = []
    for cls in classes:
        def shown(self, _opened=opened) -> int:
            self.show()
            _opened.append(self)
            return 0
        cls.exec = shown  # type: ignore[method-assign]
    return opened


def feat_c4(st: Stage):
    """Recordings: what records now and next, the recordings by day; the
    recording's notice at the sidebar's foot."""
    w = st.w
    tv_place(st, "Wild Earth")
    w._record_now(item(st, "Wild Earth"), None)
    spin(st, 1.5)
    now = time.time()
    arena = item(st, "Arena Sport")
    w.rec.add_job(w.client.live_url(arena["stream_id"]), "Matchday Live", now + 2 * 3600,
                  now + 4 * 3600, icon=arena.get("stream_icon"), channel="Arena Sport")
    spin(st, 0.5)
    w._show_place("rec")
    wait(st, lambda: w._place_rec_showing() and w._rp_page.view.model_.rowCount() > 4, 15)
    spin(st, 3)
    no_focus(st)
    settle(st, 1.5)
    img = grab(st)
    line = getattr(w, "_activity_line", None)
    spot = rect_in_window(st, line) if line is not None and line.isVisible() else \
        rect_in_window(st, w._rp_page.now)
    x, _y, wide, _h = span(st, w._side, w._rp_page)
    top = rect_in_window(st, w._rp_page).y()
    return feature(st, img, (x, top, wide, SIZE[1] - top), spot)


def feat_c5(st: Stage):
    """One search across every source: hits from several, each saying where."""
    w = st.w
    from PyQt6.QtTest import QTest
    tv_place(st, "Deep Blue")
    w._open_search_page()
    spin(st, 1)
    page = w._search_page
    QTest.keyClicks(page.field, SEARCH_WORD)
    spin(st, 6)
    no_focus(st)
    settle(st, 1)
    img = grab(st)
    view = page.view
    rows = [view.visualRect(view.model().index(i, 0)) for i in (1, 2)]
    spot = rect_in_window(st, view.viewport(), rows[0].united(rows[1]))
    return feature(st, img, span(st, page, w._det), spot)


def feat_lf(st: Stage):
    """Local files: films of one's own as posters, Continue over them."""
    w = st.w
    tv_place(st, "Wild Earth")
    w._show_place("local")
    spin(st, 2)
    wait(st, lambda: w.list_model.rowCount() > 3, 15)
    spin(st, 3)
    page = w._lp_page
    cards = page.cont.cards()
    if cards:
        w._lp_continue(cards[0])
        spin(st, 3)
        fit_player(st)
    no_focus(st)
    settle(st, 1.5)
    img = grab(st)
    strip = page.cont.strip
    spot = rect_in_window(st, strip.viewport(),
                          strip.visualRect(strip.model().index(0, 0))) if cards else \
        rect_in_window(st, page)
    return feature(st, img, span(st, page, w._det), spot)


class _PlayingTV:
    """What a Chromecast on the home network answers while it plays what it
    was sent - the device manager's answers, made up: playing, at its volume.
    The cast card and its state are the app's own."""

    TAKES_FEED = True

    def __init__(self) -> None:
        self.active: Any = None
        self.state = ""
        self.duration = 0.0
        self.bridge = None
        self.gone = False

    def knows(self, name: str | None) -> bool:
        return name == CAST_DEVICES[0]

    def scan(self) -> list[str]:
        return [CAST_DEVICES[0]]

    def cast(self, name: str, *_a, **_k) -> str:
        from types import SimpleNamespace
        self.active = SimpleNamespace(name=name)
        self.state = "PLAYING/None"
        return name

    def position(self) -> float:
        return 0.0

    def bridged(self) -> bool:
        return False

    def volume(self) -> tuple[float, bool]:
        return 0.6, False

    def set_volume(self, _level: float) -> None:
        pass

    def set_muted(self, _muted: bool) -> None:
        pass

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass

    def seek(self, _seconds: float) -> None:
        pass

    def stop(self) -> None:
        self.active, self.state = None, ""

    def shutdown(self) -> None:
        pass


def feat_cast(st: Stage):
    """Cast to the TV: the channel sent to a TV found on the home network,
    the cast card in the video's place while the TV plays it."""
    from dopeiptv.providers.chromecast import CastDialog
    w = st.w
    tv_place(st, "Arena Sport")
    # The home network's devices, and the TV taking the stream: what the
    # device managers would answer.
    tv = _PlayingTV()
    w.cast.chromecast = tv
    w.cast._current = tv
    w.cast.scan = lambda: list(CAST_DEVICES)
    opened = dialogs_stay_open(CastDialog)
    w.cast_playing()
    spin(st, 2.5)
    dlg = opened[-1]
    dlg.list.setCurrentRow(0)
    dlg._cast()
    spin(st, 2.5)
    dlg.accept()
    spin(st, 2.5)
    no_focus(st)
    settle(st, 1)
    img = grab(st)
    spot = rect_in_window(st, w.cast_bar)
    return feature(st, img, span(st, w._mid, w._det), spot)


#: A dialog's size in its picture: 16:10, as its picture is.
DIALOG_SIZE = (736, 460)


def dialog_picture(st: Stage, dlg, part):
    """A dialog the feature is in, as its own window (the way the help's
    pictures show Settings): at DIALOG_SIZE, or as tall as its content
    needs and as wide again for 16:10. The spot: *part* of it."""
    from PyQt6.QtCore import QPoint, QRect
    from PyQt6.QtWidgets import QWidget
    want_h = max(DIALOG_SIZE[1], QWidget.minimumSizeHint(dlg).height())
    want = (max(DIALOG_SIZE[0], round(want_h * 1.6)), want_h)
    dlg.resize(*want)
    spin(st, 0.8)
    no_focus(st)
    spin(st, 0.3)
    img = dlg.grab().toImage()
    size = QWidget.size(dlg)
    g = part.mapTo(dlg, QPoint(0, 0))
    spot = QRect(g.x(), g.y(), QWidget.width(part), QWidget.height(part))
    return feature(st, img, (0, 0, size.width(), size.height()), spot,
                   bounds=(size.width(), size.height()))


def feat_subs(st: Stage):
    """Subtitles found online for the film that plays."""
    from dopeiptv.providers.opensubtitles import OpenSubtitles
    from dopeiptv.ui.subtitle_search import SubtitleSearchDialog
    w = st.w
    film = next(f for f in st.films if f["stream_id"] == 105)

    def answer(self, method, path, **kw):
        if path != "/subtitles":
            return {}
        rows = []
        for i, (lang, rel, n, hi, trusted) in enumerate((
                ("en", "Northbound.2021.1080p.WEB", 18420, False, True),
                ("en", "Northbound.2021.720p.HDTV", 9310, True, False),
                ("es", "Northbound.2021.1080p.WEB", 7204, False, True),
                ("de", "Northbound.2021.1080p.WEB", 5611, False, False),
                ("fr", "Northbound.2021.WEBRip", 4870, False, True),
                ("sv", "Northbound.2021.1080p.WEB", 2216, False, False),
                ("ar", "Northbound.2021.720p", 1984, False, False),
                ("ja", "Northbound.2021.1080p.WEB", 1530, False, False))):
            rows.append({"attributes": {
                "language": lang, "release": rel, "download_count": n,
                "hearing_impaired": hi, "from_trusted": trusted,
                "machine_translated": False, "ai_translated": False,
                "moviehash_match": i == 0,
                "feature_details": {"title": "Northbound"},
                "files": [{"file_id": 7000 + i, "file_name": rel + ".srt"}]}})
        return {"data": rows}
    OpenSubtitles._call = answer  # type: ignore[method-assign]
    st.settings.setValue("os_api_key", "showcase")
    w._show_place("films")
    wait(st, lambda: w.list_model.rowCount() > 0, 10)
    spin(st, 1)
    w.cat_list.setCurrentRow(0)          # All
    spin(st, 1.5)
    select(st, film["name"])
    spin(st, 0.5)
    w._film_play(item(st, film["name"]))
    spin(st, 3)
    fit_player(st)
    opened = dialogs_stay_open(SubtitleSearchDialog)
    w._open_subtitle_search()
    spin(st, 3)
    dlg = opened[-1]
    return dialog_picture(st, dlg, dlg.tree)


def feat_keys(st: Stage):
    """The command palette over the app: a few letters, channels, actions
    and settings."""
    w = st.w
    tv_place(st, "Cosmos")
    w._open_palette()
    spin(st, 1)
    pal = w._palette_open()
    pal.field.setText(KEYS_WORD)
    spin(st, 2.5)
    settle(st, 0.5)
    img = grab(st)
    box = getattr(pal, "card", None) or getattr(pal, "panel", None) or pal
    r = rect_in_window(st, box)
    spot = rect_in_window(st, pal.field).adjusted(-6, -6, 6, 6)
    return feature(st, img, (r.x() - 60, r.y() - 30, r.width() + 120, r.height() + 60), spot)


#: What is typed into the palette: two letters of the channels' made-up
#: names, so channels match in every language beside its actions and settings.
KEYS_WORD = "or"


def feat_mine(st: Stage):
    """Mine: going on, the favourite channels by habit, Watch later."""
    w = st.w
    tv_place(st, "Northwind News")
    w._show_place("mitt")
    spin(st, 3)
    select(st, "Northwind News")
    spin(st, 1)
    no_focus(st)
    settle(st, 1)
    img = grab(st)
    page = w._mitt_page
    tiles = page.tiles
    spot = rect_in_window(st, tiles.viewport(), tiles.visualRect(tiles.model().index(0, 0)))
    return feature(st, img, span(st, page, w._det), spot)


def feat_tv(st: Stage):
    """TV mode: the ten-foot screen a remote runs."""
    w = st.w
    from PyQt6.QtWidgets import QApplication
    tv_place(st, "Pixel Kids")
    w.toggle_tv_mode()
    spin(st, 2.5)
    for _ in range(2):              # the info bar first, then the page
        w.tv_command("tv_key", "back")
        spin(st, 1.2)
    spin(st, 1)
    settle(st, 1)
    img = grab(st)
    focus = QApplication.focusWidget()
    from PyQt6.QtCore import QRect
    spot = rect_in_window(st, focus) if focus is not None and focus.window() is w else \
        QRect(0, 0, SIZE[0], SIZE[1])
    phone = phone_page(st)
    if phone is not None:
        spot = _put_phone(st, img, phone)
    return feature(st, img, (0, 0, SIZE[0], SIZE[1]), spot)


PHONE_JS = r"""
const { chromium } = require('playwright');
(async () => {
  const [url, out, lang, scheme] = process.argv.slice(2);
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 390, height: 844 },
                                       deviceScaleFactor: 3, locale: lang,
                                       colorScheme: scheme,
                                       isMobile: true, hasTouch: true });
  await page.goto(url, { waitUntil: 'networkidle' });
  await page.waitForTimeout(2500);
  await page.screenshot({ path: out });
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
"""


def phone_page(st: Stage):
    """The phone remote's page as a phone shows it: the app's own server
    (core/phone_remote.py, this computer only) read by Chromium at a
    phone's size. None when node or Playwright is not here."""
    from PyQt6.QtGui import QImage

    from dopeiptv.core.phone_remote import page_url
    w = st.w
    node = shutil.which("node")
    if node is None or not w.phone_remote_start():
        return None
    url = page_url("127.0.0.1", w._phone.port, w._phone.token)
    script = os.path.join(st.tmp, "phone.js")
    with open(script, "w") as fh:
        fh.write(PHONE_JS)
    out = os.path.join(st.tmp, "phone.png")
    from dopeiptv.ui.theme import is_light
    scheme = "light" if is_light(SCENES[st.scene]["theme"]) else "dark"
    proc = subprocess.Popen([node, script, url, out, st.lang, scheme],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    end = time.time() + 60
    while proc.poll() is None and time.time() < end:
        spin(st, 0.1)       # the page's requests are answered on the window's thread
    if proc.poll() is None:
        proc.kill()
        return None
    if proc.returncode != 0 or not os.path.exists(out):
        print("phone page:", (proc.stderr.read() if proc.stderr else "")[-400:], file=sys.stderr)
        return None
    return QImage(out)


def _put_phone(st: Stage, img, phone):
    """The phone's screen in a plain phone's frame, standing at the TV's
    side (the leading side: the end of the line in a right-to-left
    language). Its rectangle in the window's picture."""
    from PyQt6.QtCore import QRect, QRectF, Qt
    from PyQt6.QtGui import QColor, QPainter, QPainterPath
    from dopeiptv.i18n import is_rtl
    h = SIZE[1] * 0.86
    w_ = h * 390 / 844
    bezel = 10.0
    x = SIZE[0] * 0.055 if is_rtl(st.lang) else SIZE[0] - w_ - SIZE[0] * 0.055
    y = (SIZE[1] - h) / 2 + 8
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    outer = QRectF(x - bezel, y - bezel, w_ + 2 * bezel, h + 2 * bezel)
    body = QPainterPath()
    body.addRoundedRect(outer, 46, 46)
    p.fillPath(body, QColor("#0B0B0E"))
    p.setPen(QColor(255, 255, 255, 38))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(body)
    screen = QPainterPath()
    screen.addRoundedRect(QRectF(x, y, w_, h), 38, 38)
    p.setClipPath(screen)
    p.drawImage(QRectF(x, y, w_, h), phone)
    p.end()
    return QRect(round(outer.x()), round(outer.y()), round(outer.width()), round(outer.height()))


SCENES: dict[str, dict] = {
    "hero-1": {"kind": "hero", "theme": "graphite", "accent": "blue",
               "run": hero("Wild Earth")},
    "hero-2": {"kind": "hero", "theme": "latte", "run": hero("Arena Sport")},
    "hero-3": {"kind": "hero", "theme": "ocean", "run": hero("Deep Blue")},
    "hero-4": {"kind": "hero", "theme": "sand", "run": hero("Pixel Kids")},
    "hero-5": {"kind": "hero", "theme": "synthwave", "run": hero("Lumen Music")},
    "movies": {"kind": "full", "theme": "graphite", "accent": "blue", "run": movies},
    "feat-c1": {"kind": "feat", "theme": "midnight", "accent": "blue", "run": feat_c1},
    "feat-mv": {"kind": "feat", "theme": "graphite", "accent": "teal", "run": feat_mv},
    "feat-c2": {"kind": "feat", "theme": "tokyonight", "run": feat_c2,
                "settings": {"live_rewind_minutes": 30}},
    "feat-c3": {"kind": "feat", "theme": "light", "accent": "blue", "run": feat_c3},
    "feat-c6": {"kind": "feat", "theme": "oled", "accent": "green", "run": feat_c6,
                "settings": {"show_stream_info": "true"}},
    "feat-room": {"kind": "feat", "theme": "solarized_light", "run": feat_room,
                  "settings": {"places_folded": "true", "just_the_list": "true"}},
    "feat-c4": {"kind": "feat", "theme": "catppuccin", "run": feat_c4},
    "feat-c5": {"kind": "feat", "theme": "nord", "run": feat_c5},
    "feat-lf": {"kind": "feat", "theme": "latte", "run": feat_lf,
                "settings": {"local_view": "series"}},
    "feat-cast": {"kind": "feat", "theme": "everforest", "run": feat_cast},
    "feat-subs": {"kind": "feat", "theme": "rosepine", "run": feat_subs},
    "feat-keys": {"kind": "feat", "theme": "graphite", "accent": "purple", "run": feat_keys},
    "feat-mine": {"kind": "feat", "theme": "sky", "run": feat_mine},
    "feat-tv": {"kind": "feat", "theme": "kanagawa", "run": feat_tv},
}

#: The feature keys of the front page, in its order.
FEATURES = ("c1", "mv", "c2", "c3", "c4", "c5", "c6", "lf", "cast", "subs", "keys",
            "mine", "tv", "room")


# -- the parent: every language, a process per picture ----------------------------------------

def _contact_sheet(out: str) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QRectF, Qt
    from PyQt6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter
    _app = QGuiApplication(sys.argv)
    folder = os.path.join(out, "en")
    names = [n for n in SCENES if os.path.exists(os.path.join(folder, n + ".webp"))]
    cols, cw, ch, pad = 4, 480, 300, 16
    rows = (len(names) + cols - 1) // cols
    sheet = QImage(cols * (cw + pad) + pad, rows * (ch + 40 + pad) + pad,
                   QImage.Format.Format_RGB32)
    sheet.fill(QColor("#202024"))
    p = QPainter(sheet)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    f = QFont("Inter")
    f.setPixelSize(18)
    p.setFont(f)
    spots = {}
    try:
        with open(os.path.join(folder, "spots.json")) as fh:
            spots = json.load(fh)
    except (OSError, ValueError):
        pass
    for i, name in enumerate(names):
        x = pad + (i % cols) * (cw + pad)
        y = pad + (i // cols) * (ch + 40 + pad)
        img = QImage(os.path.join(folder, name + ".webp"))
        p.drawImage(QRectF(x, y, cw, ch), img)
        sp = spots.get(name[len("feat-"):]) if name.startswith("feat-") else None
        if sp:
            p.setPen(QColor("#FFC94A"))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(x + (sp["x"] - sp["w"] / 2) * cw, y + (sp["y"] - sp["h"] / 2) * ch,
                              sp["w"] * cw, sp["h"] * ch))
        p.setPen(QColor("#ECECF1"))
        p.drawText(QRectF(x, y + ch + 6, cw, 30), Qt.AlignmentFlag.AlignLeft, name)
    p.end()
    sheet.save(os.path.join(out, "contact-en.png"))


def _merge_spots(out: str, lang: str) -> None:
    folder = os.path.join(out, lang)
    os.makedirs(folder, exist_ok=True)
    spot_dir = os.path.join(folder, ".spots")
    path = os.path.join(folder, "spots.json")
    try:
        with open(path) as fh:
            spots = json.load(fh)
    except (OSError, ValueError):
        spots = {}
    if os.path.isdir(spot_dir):
        for name in os.listdir(spot_dir):
            with open(os.path.join(spot_dir, name)) as fh:
                spots[name[:-len(".json")]] = json.load(fh)
        shutil.rmtree(spot_dir, ignore_errors=True)
    ordered = {k: spots[k] for k in FEATURES if k in spots}
    with open(path, "w") as fh:
        json.dump(ordered, fh, indent=1)


_DISPLAYS = threading.Lock()


def _own_display() -> tuple[subprocess.Popen, str]:
    """An Xvfb of its own (a language's, while several run at once)."""
    with _DISPLAYS:
        n = 90
        while os.path.exists(f"/tmp/.X{n}-lock") or os.path.exists(f"/tmp/.X11-unix/X{n}"):
            n += 1
        proc = subprocess.Popen(["Xvfb", f":{n}", *SCREEN.split(), "-nolisten", "tcp"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        end = time.time() + 10
        while not os.path.exists(f"/tmp/.X11-unix/X{n}") and time.time() < end:
            time.sleep(0.1)
    return proc, f":{n}"


def _run_language(lang: str, out: str, work: str, scenes: list[str],
                  own_display: bool) -> list[str]:
    failed = []
    env = dict(os.environ)
    xvfb = None
    if own_display:
        xvfb, env["DISPLAY"] = _own_display()
    for scene in scenes:
        cmd = [sys.executable, os.path.abspath(__file__), "--one", lang, out, work, scene]
        ok = False
        for _attempt in range(2):
            try:
                r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                   text=True, env=env, timeout=300)
            except subprocess.TimeoutExpired:
                print(f"{lang} {scene}: try timed out", file=sys.stderr, flush=True)
                continue
            if r.returncode == 0 and os.path.exists(os.path.join(out, lang, scene + ".webp")):
                ok = True
                break
            tail = "\n".join((r.stderr or "").strip().splitlines()[-6:])
            print(f"{lang} {scene}: try failed\n{tail}", file=sys.stderr, flush=True)
        if not ok:
            failed.append(scene)
    if xvfb is not None:
        xvfb.terminate()
        xvfb.wait(10)
    _merge_spots(out, lang)
    print(lang, "ok" if not failed else "FAILED: " + ", ".join(failed), flush=True)
    return failed


def main(argv: list[str]) -> int:
    if len(argv) >= 5 and argv[0] == "--one":
        return one_scene(argv[1], argv[2], argv[3], argv[4])
    if len(argv) == 2 and argv[0] == "--assets":
        make_assets(argv[1])
        return 0
    if len(argv) == 2 and argv[0] == "--contact":
        _contact_sheet(argv[1])
        return 0
    if not argv:
        print(__doc__)
        return 2
    only: list[str] = []
    jobs = 1
    keep = ""
    rest = []
    it = iter(argv)
    for a in it:
        if a == "--only":
            only = [s for s in next(it).split(",") if s]
        elif a == "--jobs":
            jobs = max(1, int(next(it)))
        elif a == "--work":
            keep = next(it)
        else:
            rest.append(a)
    out, wanted = rest[0], rest[1:]
    unknown = [s for s in only if s not in SCENES]
    if unknown:
        print("unknown scenes: " + ", ".join(unknown), file=sys.stderr)
        return 2
    scenes = only or list(SCENES)
    work = keep or tempfile.mkdtemp(prefix="dopeiptv-site-art-")
    try:
        if not os.path.isdir(os.path.join(work, "art")):
            subprocess.run([sys.executable, os.path.abspath(__file__), "--assets", work],
                           check=True)
        make_videos(work)
        from dopeiptv import i18n
        langs = wanted or ["en", *sorted(c for c in i18n.LANGUAGES if c != "en")]
        failed: dict[str, list[str]] = {}
        if jobs == 1:
            for code in langs:
                bad = _run_language(code, out, work, scenes, False)
                if bad:
                    failed[code] = bad
        else:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(jobs) as pool:
                futures = {}
                for code in langs:
                    futures[code] = pool.submit(_run_language, code, out, work, scenes, True)
                for code, fut in futures.items():
                    bad = fut.result()
                    if bad:
                        failed[code] = bad
        if "en" in langs:
            subprocess.run([sys.executable, os.path.abspath(__file__), "--contact", out])
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
    for code, bad in failed.items():
        print(f"FAILED {code}: {', '.join(bad)}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
