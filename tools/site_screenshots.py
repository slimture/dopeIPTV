"""The pictures on the website's front page, from the app itself, in every
language.

Four pictures, the app at its best: the channel list and the player, the
programme guide, multiview, and a live channel rewound. The playlist is made
up (tools/showcase/: channel names, a day's guide, logos drawn here) and the
picture in the players is a still from tools/showcase/ - NASA photographs,
in the public domain - so nothing of a provider's or the user's shows.

Needs a display with OpenGL (the video surfaces are OpenGL widgets) and
ffmpeg (each still becomes a short video for mpv to play)::

    xvfb-run -a -s "-screen 0 2560x1600x24" \\
        python tools/site_screenshots.py OUT [lang ...]

OUT gets ``<lang>/{main,guide,multiview,timeshift}.webp`` - copy it over
``public/screenshots/`` on the website branch. Each language runs in a
process of its own, so every picture starts from a fresh window.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SHOWCASE = os.path.join(HERE, "showcase")
sys.path.insert(0, os.path.join(HERE, os.pardir))

SIZE = (1280, 800)
SCALE = "2"
QUALITY = 86

#: (category id, name, [(channel, logo colour, still or None)])
LINEUP = [
    ("1", "News", [("Northwind News", "#2f6fde", None),
                   ("Meridian 24", "#c0392b", None)]),
    ("2", "Science", [("Orbit", "#2c3e50", "orbit"),
                      ("Cosmos", "#6c5ce7", "cosmos"),
                      ("Planet Watch", "#e17055", "planet"),
                      ("Launchpad", "#0984e3", "launch")]),
    ("3", "Sport", [("Arena Sport 1", "#16a085", None),
                    ("Arena Sport 2", "#1abc9c", None)]),
    ("4", "Movies", [("Cinema Noir", "#8e44ad", None),
                     ("Reel Classics", "#d35400", None)]),
    ("5", "Kids", [("Pixel Kids", "#f39c12", None)]),
    ("6", "Music", [("Lumen Music", "#e84393", None)]),
]
TITLES = {
    "News": ["Morning Briefing", "World Today", "Market Watch",
             "The Big Interview", "Evening News", "Late Edition"],
    "Science": ["Earth from Orbit", "Night Sky", "Rings of Saturn",
                "The Giant Planets", "Countdown", "Life on the Station"],
    "Sport": ["Matchday Live", "Goals & Highlights", "Tennis: Semi-final",
              "Cycling: Mountain Stage", "Sports Desk", "Extra Time"],
    "Movies": ["The Last Lighthouse", "City of Glass", "Northern Road",
               "A Quiet Harbour", "Paper Moons", "The Long Winter"],
    "Kids": ["Robo Friends", "Tiny Planet", "Story Time", "Space Pups",
             "Drawing Club", "Good Night Show"],
    "Music": ["Live Sessions", "Top 20 Countdown", "Acoustic Hour",
              "Studio Stories", "Festival Nights", "Night Mix"],
}
SHOTS = ("main", "guide", "multiview", "timeshift")
STILLS = ("orbit", "cosmos", "planet", "launch")
#: Where the parent leaves the videos, so each language need not make them.
VIDEO_DIR_ENV = "DOPEIPTV_SHOWCASE_VIDEOS"


def make_videos(folder: str) -> dict[str, str]:
    """Each still as a ten-minute video (made once per folder)."""
    out = {}
    for name in STILLS:
        dst = os.path.join(folder, name + ".mp4")
        if not os.path.exists(dst):
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1",
                            "-framerate", "5", "-i",
                            os.path.join(SHOWCASE, name + ".jpg"), "-vf",
                            "scale=640:360,format=yuv420p", "-t", "600",
                            "-c:v", "libx264", "-preset", "ultrafast",
                            "-tune", "stillimage", dst], check=True)
        out[name] = dst
    return out


def guide_xml(streams: list[dict]) -> bytes:
    """A guide from four hours ago to ten ahead, in half-hour steps."""
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    base = now - timedelta(hours=4, minutes=now.minute % 30)
    fmt = "%Y%m%d%H%M%S +0000"
    out = ['<?xml version="1.0" encoding="UTF-8"?><tv>']
    for s in streams:
        out.append(f'<channel id="{s["epg_channel_id"]}">'
                   f'<display-name>{s["name"]}</display-name></channel>')
    for s in streams:
        titles = TITLES[s["_cat"]]
        i = s["stream_id"]
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


def one_language(lang: str, out: str) -> int:
    os.environ.setdefault("QT_SCALE_FACTOR", SCALE)
    from PyQt6.QtCore import QRectF, QSettings, Qt
    from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath
    from PyQt6.QtWidgets import QApplication, QDialog

    app = QApplication(sys.argv)
    tmp = tempfile.mkdtemp(prefix="dopeiptv-site-")

    from dopeiptv import i18n
    from dopeiptv.media import video_surface
    from dopeiptv.providers import epg as epgmod
    from dopeiptv.providers.client import OfflineClient
    from dopeiptv.ui.qt_i18n import apply_qt_language
    from dopeiptv.ui.theme import apply_theme, build_style

    def spin(sec: float) -> None:
        end = time.time() + sec
        while time.time() < end:
            app.processEvents()
            time.sleep(0.02)

    def logo(name: str, colour: str) -> str:
        path = os.path.join(tmp, name.replace(" ", "_") + ".png")
        img = QImage(256, 256, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(colour))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(8, 8, 240, 240), 56, 56)
        f = QFont("Inter")
        f.setPixelSize(100)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("white"))
        words = name.split()
        mark = (words[0][0] + (words[1][0] if len(words) > 1 else "")).upper()
        p.drawText(QRectF(0, 0, 256, 256), Qt.AlignmentFlag.AlignCenter, mark)
        p.end()
        img.save(path)
        return path

    # Each still as a short video: mpv plays it, so the player is in the
    # state a real channel puts it in; what it draws is the still itself.
    stills = {name: QImage(os.path.join(SHOWCASE, name + ".jpg"))
              for name in STILLS}
    videos = make_videos(os.environ.get(VIDEO_DIR_ENV) or tmp)

    def paint_still(self) -> None:
        try:
            path = str(self.mpv.path) if self.mpv is not None else ""
        except Exception:
            path = ""
        img = stills.get(os.path.splitext(os.path.basename(path))[0])
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("black"))
        if img is not None and not img.isNull():
            r = self.rect()
            s = img.size().scaled(r.size(), Qt.AspectRatioMode.KeepAspectRatio)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(QRectF((r.width() - s.width()) / 2,
                               (r.height() - s.height()) / 2,
                               s.width(), s.height()), img)
        p.end()

    video_surface._MpvGLWidget.paintGL = paint_still  # type: ignore[method-assign]

    streams: list[dict] = []
    for cat_id, cat_name, chans in LINEUP:
        for name, colour, still in chans:
            sid = len(streams) + 1
            streams.append({
                "stream_id": sid, "num": sid, "name": name,
                "stream_icon": logo(name, colour), "category_id": cat_id,
                "epg_channel_id": f"ch{sid}", "tv_archive": 1,
                "tv_archive_duration": 7, "stream_type": "live",
                "_cat": cat_name, "_still": still})

    class Showcase(OfflineClient):
        def live_categories(self) -> list[dict]:
            return [{"category_id": c, "category_name": n}
                    for c, n, _ in LINEUP]

        def live_streams(self, category_id=None) -> list[dict]:
            return [dict(s) for s in streams
                    if category_id in (None, "", s["category_id"])]

        def live_url(self, stream_id, fmt="ts") -> str:
            s = streams[int(stream_id) - 1]
            return videos.get(s["_still"] or "orbit", "")

    xml = guide_xml(streams)
    epgmod.XmltvGuide.PARSE_ELSEWHERE = False
    epgmod.XmltvGuide._download = lambda self: xml  # type: ignore[method-assign]

    settings = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
    for key, value in (("language", lang), ("home_enabled", "false"),
                       ("check_updates", "false"), ("mv_info_seen", "true"),
                       ("donate_heart_until", time.time() + 10 * 365 * 86400),
                       ("docked_video_height", 300)):
        settings.setValue(key, value)
    i18n.set_language(lang)
    apply_qt_language(app)
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft if i18n.is_rtl(lang)
                           else Qt.LayoutDirection.LeftToRight)
    apply_theme(settings)
    app.setStyleSheet(build_style())

    from dopeiptv.ui.main_window import MainWindow
    w = MainWindow(Showcase(), settings)
    w.resize(*SIZE)
    w.move(0, 0)
    w.show()
    spin(2)
    w.switch_mode("live")
    spin(1.5)
    w.cat_list.setCurrentRow(0)            # All
    spin(1.5)
    w._ensure_xmltv_loaded()
    spin(1.5)

    def item(name: str) -> dict:
        return next(x for x in w.all_items if x.get("name") == name)

    def select(name: str) -> None:
        for r in range(w.list_model.rowCount()):
            x = w.list_model.item_at(r)
            if x and x.get("name") == name:
                w.listw.setCurrentIndex(w.list_model.index(r, 0))
                return

    def save(img: QImage, name: str) -> None:
        folder = os.path.join(out, lang)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name + ".webp")
        if not img.save(path, "webp", QUALITY):
            raise RuntimeError(f"could not write {path}")

    def settle(sec: float = 1.0) -> None:
        for _ in range(int(sec / 0.2)):
            if w.player is not None:
                w.player.video.update()
            spin(0.2)

    # 1. The channel list, the guide's now and next, and the player.
    select("Orbit")
    spin(0.5)
    w.play_live_channel(item("Orbit"))
    spin(3)
    if QApplication.focusWidget() is not None:
        QApplication.focusWidget().clearFocus()
    settle(1.5)
    save(w.grab().toImage(), "main")

    # 2. The programme guide.
    def grab_guide(dialog: QDialog) -> int:
        dialog.resize(*SIZE)
        dialog.move(0, 0)
        dialog.show()
        spin(2.5)
        save(dialog.grab().toImage(), "guide")
        return 0

    real_exec = QDialog.exec
    QDialog.exec = grab_guide  # type: ignore[method-assign,assignment]
    w._open_epg_guide()
    QDialog.exec = real_exec  # type: ignore[method-assign]

    # 3. Multiview: four channels in their own window, over the app.
    for n, name in enumerate(("Orbit", "Cosmos", "Planet Watch", "Launchpad")):
        w._add_channel_to_multiview(item(name), n)
        spin(0.5)
    mv = w._multiview_win
    mv.resize(800, 470)
    mv.move(0, 0)
    spin(3)
    cells = list(getattr(mv, "cells", []))
    if cells:
        mv._focus_cell(cells[0])        # the one with the sound
    for cell in cells:
        for vid in cell.findChildren(video_surface._MpvGLWidget):
            vid.update()
    mv._reveal_overlays()
    spin(1)
    base = w.grab().toImage()
    top = mv.grab().toImage()
    mv.close()
    spin(1)
    dpr = base.devicePixelRatio()
    p = QPainter(base)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.fillRect(QRectF(0, 0, SIZE[0], SIZE[1]), QColor(0, 0, 0, 90))
    tw, th = top.width() / dpr, top.height() / dpr
    x, y = (SIZE[0] - tw) / 2 + 60, (SIZE[1] - th) / 2 + 20
    for k in range(18, 0, -2):          # a soft shadow under the window
        sp = QPainterPath()
        sp.addRoundedRect(QRectF(x - k, y - k + 10, tw + 2 * k, th + 2 * k),
                          14 + k, 14 + k)
        p.fillPath(sp, QColor(0, 0, 0, 10))
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(x, y, tw, th), 12, 12)
    p.setClipPath(clip)
    p.drawImage(QRectF(x, y, tw, th), top)
    p.setClipping(False)
    p.setPen(QColor(255, 255, 255, 40))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(clip)
    p.end()
    save(base, "multiview")

    # 4. A live channel rewound: the player with the room the list had,
    # and the timeline over the picture.
    side, mid, det = w._root.sizes()
    w._set_focus_mode(True)
    w._root.setSizes([side, 0, mid + det])
    handle = getattr(w, "_player_resize", None)
    if handle is not None:
        handle._preferred = 1000            # as tall as the pane allows
        handle.apply_height()
    select("Launchpad")
    spin(1)
    w.play_live_channel(item("Launchpad"))
    spin(3)
    pl = w.player
    pl._seek_mode = "timeline"
    pl.enter_timeshift(90)
    settle(1.5)
    pl._show_ts_timeline()
    pl.update_timeshift_position(28, "Countdown")
    # The player's own clock reports the stream it is really on - live -
    # and would put the marker back there before the picture is taken.
    pl.update_timeshift_position = lambda *a, **k: None  # type: ignore[method-assign]
    spin(0.5)
    save(w.grab().toImage(), "timeshift")
    # Out at once: Qt and mpv tearing down at interpreter exit abort now and
    # then, which would report a language whose pictures are all written as
    # failed.
    sys.stdout.flush()
    os._exit(0)


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] == "--one":
        return one_language(argv[1], argv[2])
    if not argv:
        print(__doc__)
        return 2
    out, wanted = argv[0], argv[1:]
    os.environ[VIDEO_DIR_ENV] = tempfile.mkdtemp(prefix="dopeiptv-videos-")
    make_videos(os.environ[VIDEO_DIR_ENV])
    from dopeiptv import i18n
    langs = wanted or ["en", *sorted(c for c in i18n.LANGUAGES if c != "en")]
    failed = []
    for code in langs:
        r = subprocess.run([sys.executable, os.path.abspath(__file__),
                            "--one", code, out])
        print(code, "ok" if r.returncode == 0 else "FAILED", flush=True)
        if r.returncode != 0:
            failed.append(code)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
