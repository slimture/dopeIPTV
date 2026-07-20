"""Multiview: watch up to four live channels at once in a 2x2 grid.

A separate top-level window holding four lightweight video cells, each its own
libmpv render surface (`_MpvGLWidget`) - the *docked* embedded player is never
touched. Click a cell to give it audio focus (the others stay muted); right-
click for mute, swap/move, and remove. Channels are added from the main list's
right-click "Add to multiview", capturing whatever playlist is active at that
moment - so four cells can come from four different accounts, sidestepping a
single account's connection limit.

The window mirrors the pop-out player's chrome options: title-bar-less by
default (drag a cell to move it), with right-click "Always on top" and
"Show title bar". Cell titles/numbers auto-hide and reappear on mouse
movement; a seek bar shows position/pause for seekable (timeshift/catch-up)
cells. Space pauses the focused cell; Left/Right seek it.

Heads-up for the user: each cell is a separate stream = a separate connection
to the provider. On a low connection-limit account the extra cells will be
refused (the same 458 / "all connections in use" the diagnosis reports).

Kept out of main_window.py to keep that file lean.
"""
from __future__ import annotations

import time
from datetime import datetime

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialogButtonBox, QGridLayout, QLabel, QMenu,
    QPushButton, QVBoxLayout, QWidget)

from ..core.log import log
from ..i18n import tr
from ..media.embedded import _MpvGLWidget, _SeekSlider
from .widgets import exec_menu_over_video


def _fmt(secs: float) -> str:
    secs = int(max(0, secs))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _glyph(kind: str, s: int, alpha: int = 235) -> QPixmap:
    """White vector control glyphs (pause / play). Drawn, not text: the ⏸/▶
    characters take their emoji presentation on macOS and render as black
    marks that ignore the stylesheet colour - invisible on the dark control
    scrims. Rendered at 3x and tagged with that density, so it stays crisp on
    any screen (the widget's DPR at construction time can be a pre-Retina
    1.0, which left the marks pixelated)."""
    ss = 3.0
    pm = QPixmap(round(s * ss), round(s * ss))
    pm.setDevicePixelRatio(ss)
    pm.fill(Qt.GlobalColor.transparent)
    pr = QPainter(pm)
    pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    col = QColor(255, 255, 255, alpha)
    if kind == "pause":
        pr.setPen(Qt.PenStyle.NoPen)
        pr.setBrush(col)
        bw = s * 0.24
        for x in (s * 0.22, s * 0.56):
            pr.drawRoundedRect(QRectF(x, s * 0.12, bw, s * 0.76),
                               bw * 0.3, bw * 0.3)
    else:   # play
        pr.setPen(Qt.PenStyle.NoPen)
        pr.setBrush(col)
        pr.drawPolygon(QPolygonF([
            QPointF(s * 0.26, s * 0.10), QPointF(s * 0.26, s * 0.90),
            QPointF(s * 0.88, s * 0.50)]))
    pr.end()
    return pm


class _CloseButton(QPushButton):
    """Fully custom-painted round close button. A stylesheet border-radius
    circle rasterises without antialiasing on some platforms (a visibly
    jagged ring), so the scrim disc, ring and ✕ are all drawn here with an
    antialiased painter at the widget's real DPR."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(30, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("background:transparent; border:none;")

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        pr = QPainter(self)
        pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        hov = self.underMouse()
        rf = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        pr.setPen(QPen(QColor(255, 255, 255, 230 if hov else 120), 1.2))
        pr.setBrush(QColor(229, 53, 75, 235) if hov
                    else QColor(20, 20, 24, 200))
        pr.drawEllipse(rf)
        pen = QPen(QColor(255, 255, 255, 235))
        pen.setWidthF(1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pr.setPen(pen)
        m = 10.5
        w, h = float(self.width()), float(self.height())
        pr.drawLine(QPointF(m, m), QPointF(w - m, h - m))
        pr.drawLine(QPointF(w - m, m), QPointF(m, h - m))
        pr.end()


class _MultiviewCell(QWidget):
    """One grid cell: a bare mpv video surface with a position number, an
    auto-hiding title strip and seek bar, click-to-focus, and per-cell
    mute/pause/seek."""

    focus_requested = pyqtSignal(object)
    context_requested = pyqtSignal(object, object)   # (cell, global pos)
    hovered = pyqtSignal(object)
    maximize_requested = pyqtSignal()

    def __init__(self, number: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.number = number
        self.url: str | None = None
        self.title: str = ""
        self._focused = False
        self._muted = True
        self._drag_from = None
        self._overlays_on = False
        # Seek-window bookkeeping. For finite content it's [0, duration]; for a
        # live stream it's a rolling window from when this cell started playing
        # (_live_start) to the live edge (_live_edge), so the scrub bar works
        # within mpv's buffer instead of needing a duration live never has.
        self._win_start = 0.0
        self._win_span = 0.0
        self._live_start: float | None = None
        self._live_edge = 0.0
        # Timeshift (provider archive) state. When the channel is catch-up
        # capable the seek bar becomes an archive timeline: dragging re-requests
        # the stream at a server offset (client.timeshift_urls), same mechanism
        # as the docked player. _ts_seg_start is the wall-clock content start of
        # the loaded archive segment, or None while at the live edge.
        self._item: dict | None = None
        self._client = None
        self._live_url: str | None = None
        self._ts_capable = False
        self._ts_days = 0
        self._ts_seg_start: float | None = None
        self._ts_candidates: list[str] = []
        self._ts_cand_idx = 0
        # EPG guide for programme-boundary ticks on the timeline (and the
        # programme name at the playhead), refreshed at most once a minute.
        self._guide = None
        self._progs: list = []
        self._progs_at = 0.0
        # Chosen audio/subtitle tracks (None = mpv's default), re-applied on
        # every stream (re)load - a timeshift jump resets track selection.
        self._want_aid: int | None = None
        self._want_sid: int | str | None = None
        self.setStyleSheet("background:#000000;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.video = _MpvGLWidget(self)
        self.video.setMinimumSize(0, 0)
        # Hover must reveal the overlays (title, controls, the window's close
        # button) without a pressed button - same as the docked player. Without
        # tracking, mouse-move only fires mid-drag and the overlays effectively
        # never appear.
        self.video.setMouseTracking(True)
        lay.addWidget(self.video)
        self.video.video_mouse_press.connect(self._on_press)
        self.video.video_mouse_move.connect(self._on_move)
        self.video.video_mouse_release.connect(self._on_release)
        self.video.video_dbl_click.connect(self.maximize_requested)
        self.video.playback_error.connect(self._on_error)

        # Active-cell marker: a full-rect border painted as a child ON TOP of
        # the GL surface (a stylesheet border on the cell itself is covered by
        # the video, so it only ever flickered into view when a cell was
        # empty). Transparent centre + transparent-for-mouse so it never eats
        # a click meant for the video.
        self._border = QLabel("", self.video)
        self._border.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._border.hide()

        self._title = QLabel("", self.video)
        self._title.setStyleSheet(
            "background:rgba(0,0,0,150); color:#ECECF1; padding:3px 8px;"
            "font-size:12px; font-weight:600;")
        self._title.hide()
        # Which playlist/account this cell streams from - a muted sub-line
        # under the title (cells can come from different accounts).
        self.playlist_name = ""
        self._pl = QLabel("", self.video)
        self._pl.setStyleSheet(
            "background:rgba(0,0,0,120); color:#9A9AA5; padding:1px 8px;"
            "font-size:10px;")
        self._pl.hide()
        self._empty = QLabel(tr("mv_empty_cell"), self.video)
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet("color:#7A7A85; font-size:12px;")
        self._num = QLabel(str(number), self.video)
        self._num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._num.setStyleSheet(
            "background:transparent; color:rgba(255,255,255,140);"
            "font-size:56px; font-weight:800;")
        self._num.hide()
        self._pause = QLabel("", self.video)
        self._pause.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pause.setStyleSheet("background:transparent;")
        self._pause.setPixmap(_glyph("pause", 44, alpha=200))
        self._pause.hide()
        # None of these read-only overlays should intercept mouse events - the
        # video underneath must keep getting click-to-focus, drag and
        # double-click. (The seek slider is deliberately left interactive.)
        for w in (self._title, self._pl, self._empty, self._num, self._pause):
            w.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # Bottom controls, as DIRECT children of the GL widget - the one
        # arrangement macOS reliably composites over the video (a nested
        # container bar did not paint there): pause/play, the docked player's
        # _SeekSlider (programme-boundary ticks + hover tooltips naming the
        # programme + click-to-jump), an offset/position label, and a LIVE
        # pill that jumps back to the live edge - the same look and gestures
        # as the docked timeline.
        self._icon_pause = QIcon(_glyph("pause", 13))
        self._icon_play = QIcon(_glyph("play", 13))
        self._pause_btn = QPushButton("", self.video)
        self._pause_btn.setIcon(self._icon_pause)
        self._pause_btn.setIconSize(QSize(13, 13))
        self._pause_btn.setToolTip(tr("mv_pause"))
        self._pause_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pause_btn.setStyleSheet(
            "QPushButton { background:rgba(0,0,0,170); border:none;"
            " border-radius:4px; }"
            "QPushButton:hover { background:rgba(255,92,92,110); }")
        self._pause_btn.clicked.connect(self.toggle_pause)
        self._pause_btn.hide()
        self._seek = _SeekSlider(self.video)
        self._seek.setRange(0, 1000)
        self._seek.setMouseTracking(True)   # hover names the programme
        self._seek.seek_requested.connect(self._on_seek_value)
        self._seek.hide()
        self._time = QLabel("", self.video)
        self._time.setStyleSheet(
            "background:rgba(0,0,0,170); color:#ECECF1; padding:1px 6px;"
            "font-size:11px; font-weight:600; border-radius:4px;")
        self._time.hide()
        self._live_btn = QPushButton("", self.video)
        self._live_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._live_btn.clicked.connect(self._go_live)
        self._live_btn.hide()
        self._apply_live_style(True)
        self.set_focused(False)

    def _apply_live_style(self, live: bool) -> None:
        """Red solid '● LIVE' pill at the live edge; a bordered '⏭ LIVE'
        (click to go live) while behind - mirroring the docked timeline."""
        if live == getattr(self, "_live_style", None):
            return
        self._live_style = live
        word = tr("mv_live")
        if live:
            self._live_btn.setText("● " + word)
            self._live_btn.setStyleSheet(
                "QPushButton { background:#FF5C5C; color:#fff; border:none;"
                " border-radius:10px; padding:2px 10px; font-size:11px;"
                " font-weight:700; }")
        else:
            self._live_btn.setText("⏭ " + word)
            self._live_btn.setStyleSheet(
                "QPushButton { background:rgba(0,0,0,170); color:#FF5C5C;"
                " border:1px solid #FF5C5C; border-radius:10px;"
                " padding:2px 10px; font-size:11px; font-weight:700; }"
                "QPushButton:hover { background:rgba(255,92,92,60); }")

    def _sync_pause_btn(self) -> None:
        try:
            paused = bool(self.video.mpv.pause) if self.video.mpv else False
        except Exception:
            paused = False
        self._pause_btn.setIcon(self._icon_play if paused
                                else self._icon_pause)

    def _layout_controls(self) -> None:
        """Manual bottom-row layout: pause | slider | -offset | LIVE. Runs on
        resize and on every state poll (the offset label's width changes)."""
        r = self.video.rect()
        bh = 22
        y = r.height() - bh - 8
        x = 6
        self._pause_btn.setGeometry(x, y, 26, bh)
        x += 32
        right = r.width() - 6
        if self._ts_capable:
            lw = max(56, self._live_btn.sizeHint().width())
            self._live_btn.setGeometry(right - lw, y, lw, bh)
            right -= lw + 6
        if not self._time.isHidden():
            self._time.adjustSize()
            tw = self._time.width()
            self._time.setGeometry(right - tw, y, tw, bh)
            right -= tw + 6
        self._seek.setGeometry(x, y + (bh - 16) // 2, max(40, right - x), 16)

    def _raise_controls(self) -> None:
        for w in (self._seek, self._time, self._pause_btn, self._live_btn):
            w.raise_()

    # -- overlays ------------------------------------------------------------

    def show_overlays(self, on: bool) -> None:
        # Title + big position number auto-hide; the seek bar's visibility is
        # owned by refresh_state so it can stay put for the focused/seekable
        # cell instead of vanishing with the 2 s auto-hide.
        self._overlays_on = on
        self._num.setVisible(on)
        self._title.setVisible(on and bool(self.title))
        self._pl.setVisible(on and bool(self._pl.text()))
        # The focus border (red = audible cell) fades with the overlays too.
        self._border.setVisible(on)
        if on:
            self._border.raise_()
            self._num.raise_()
            self._title.raise_()
            self._pl.raise_()
            self._raise_controls()
        else:
            # Controls auto-hide with the rest of the overlays (refresh_state
            # re-shows them on the next reveal).
            for w in (self._pause_btn, self._seek, self._time, self._live_btn):
                w.hide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        r = self.video.rect()
        self._border.setGeometry(r)
        self._title.adjustSize()
        self._title.move(0, 0)
        self._pl.adjustSize()
        self._pl.move(0, self._title.height())
        self._empty.setGeometry(r)
        self._num.setGeometry(r)
        self._pause.setGeometry(r)
        self._layout_controls()

    # -- playback ------------------------------------------------------------

    def play(self, url: str, title: str, item: dict | None = None,
             client=None, guide=None, playlist: str = "") -> bool:
        self.url = url
        self.title = title
        self._live_url = url
        self._item = item
        self._client = client
        self._guide = guide
        self.playlist_name = playlist or ""
        self._pl.setText(self.playlist_name)
        self._pl.adjustSize()
        self._progs = []
        self._progs_at = 0.0
        self._ts_seg_start = None    # start at the live edge
        self._ts_candidates = []
        self._ts_cand_idx = 0
        # A new channel: its track ids have nothing to do with the previous
        # one's, so drop any remembered audio/subtitle choice.
        self._want_aid = None
        self._want_sid = None
        # Catch-up capable? Needs the provider flag, a real depth, a stream id
        # and a client that can build archive URLs.
        days = int((item or {}).get("tv_archive_duration") or 0) if item else 0
        self._ts_days = days
        self._ts_capable = bool(
            item and int((item or {}).get("tv_archive") or 0) and days > 0
            and (item or {}).get("stream_id") is not None
            and client is not None and hasattr(client, "timeshift_urls"))
        self._live_start = None    # fresh rolling window for the new stream
        self._live_edge = 0.0
        self._win_span = 0.0
        self._title.setText(title or "")
        self._title.adjustSize()
        self._empty.hide()
        return self._mpv_play(url)

    def _mpv_play(self, url: str) -> bool:
        """Point this cell's mpv at *url* without disturbing the cell's channel
        / live-url / timeshift bookkeeping (used both for the initial play and
        for archive-segment / go-live swaps)."""
        if self.video.mpv is None:
            self.video.show()
            QApplication.instance().processEvents()
        m = self.video.mpv
        if m is None:
            return False
        try:
            m["force-media-title"] = self.title or ""
            m["cache"] = "yes"
            # Back-buffer so a live stream stays scrubbable within its cache.
            # The cell's own isolated mpv - never the docked player's settings.
            # Sized per channel: catch-up channels keep the deep buffer for
            # archive scrubbing; plain live channels get a quarter of it
            # (still minutes of rewind for the session scrubber) - with up to
            # nine cells the difference is over a gigabyte of ceiling.
            m["cache-secs"] = 600
            back_mb = 200 if self._ts_capable else 48
            m["demuxer-max-back-bytes"] = back_mb * 1024 * 1024
            m["mute"] = self._muted
            # Re-assert the user's chosen tracks: a (re)load - notably every
            # timeshift jump - resets mpv's selection to its defaults.
            if self._want_aid is not None:
                m["aid"] = self._want_aid
            if self._want_sid is not None:
                m["sid"] = self._want_sid
                if self._want_sid != "no":
                    m["sub-visibility"] = True
            m.play(url)
            return True
        except Exception as e:
            log.warning("multiview cell play failed: %s: %s",
                        type(e).__name__, e)
            return False

    # -- timeshift (provider archive) ---------------------------------------

    def _ts_depth_min(self) -> int:
        return max(1, self._ts_days) * 24 * 60

    def _go_live(self) -> None:
        self._ts_seg_start = None
        self._ts_candidates = []
        self._ts_cand_idx = 0
        if self._live_url:
            self._mpv_play(self._live_url)

    def _go_timeshift(self, back_min: float) -> None:
        """Load the provider archive starting *back_min* before now. Candidate
        URL schemes are tried in order (walked on error in _on_error)."""
        if not self._ts_capable or self._client is None:
            return
        sid = (self._item or {}).get("stream_id")
        now = time.time()
        # Keep a small margin inside the oldest edge (providers drop the last
        # few minutes, so a request at the exact limit fails).
        back_min = min(back_min, self._ts_depth_min() - 2)
        if back_min < 1:
            self._go_live()
            return
        start = now - back_min * 60
        duration_min = max(1, int(back_min) + 1)
        try:
            urls = self._client.timeshift_urls(
                sid, datetime.fromtimestamp(start), duration_min)
        except Exception as e:
            log.warning("multiview timeshift url build failed: %s", e)
            return
        if not urls:
            return
        self._ts_candidates = list(urls)
        self._ts_cand_idx = 0
        self._ts_seg_start = start
        self._mpv_play(self._ts_candidates[0])

    def _on_error(self, _msg: str) -> None:
        # Mid-timeshift: walk to the next candidate URL scheme, then fall back
        # to live if none serve the archive.
        if self._ts_seg_start is not None:
            if self._ts_cand_idx + 1 < len(self._ts_candidates):
                self._ts_cand_idx += 1
                self._mpv_play(self._ts_candidates[self._ts_cand_idx])
                return
            self._go_live()
            return
        self._title.setText(tr("mv_cell_error", title=self.title or ""))
        self.title = self._title.text()
        self._title.setVisible(True)
        self._title.adjustSize()

    def set_focused(self, on: bool) -> None:
        self._focused = on
        # Draw the marker on top of the video (see _border). Red = the active
        # (unmuted) cell; a dim frame otherwise so every cell reads as a tile.
        # Visibility follows the overlay auto-hide (see show_overlays).
        self._border.setStyleSheet(
            "background:transparent; border:3px solid #e5354b;" if on
            else "background:transparent; border:2px solid #303038;")
        self._border.setVisible(self._overlays_on)
        if self._overlays_on:
            self._border.raise_()

    def set_muted(self, muted: bool) -> None:
        self._muted = muted
        if self.video.mpv is not None and self.url is not None:
            try:
                self.video.mpv["mute"] = muted
            except Exception:
                pass

    def is_muted(self) -> bool:
        return self._muted

    def tracks(self) -> tuple[list, list]:
        """(audio, subtitle) track dicts from this cell's mpv, empty when
        nothing is loaded - so the menus only appear when there is a choice."""
        m = self.video.mpv
        if m is None or self.url is None:
            return [], []
        try:
            tl = list(m.track_list or [])
        except Exception:
            return [], []
        auds = [t for t in tl if t.get("type") == "audio"]
        subs = [t for t in tl if t.get("type") == "sub"]
        return auds, subs

    @staticmethod
    def track_label(t: dict) -> str:
        lang = t.get("lang") or ""
        title = t.get("title") or ""
        if lang and title:
            return f"{lang} — {title}"
        return lang or title or f"#{t.get('id')}"

    def set_audio(self, tid) -> None:
        m = self.video.mpv
        if m is None:
            return
        try:
            self._want_aid = int(tid)
            m["aid"] = self._want_aid
        except Exception as e:
            log.warning("multiview set_audio(%r) failed: %s", tid, e)

    def set_sub(self, tid) -> None:
        """Select subtitle track *tid*, or turn subtitles off when None.
        The choice is remembered so a timeshift jump (which reloads the
        stream and resets mpv's track selection) keeps the same subtitle."""
        m = self.video.mpv
        if m is None:
            return
        try:
            if tid is None:
                self._want_sid = "no"
                m["sid"] = "no"
            else:
                self._want_sid = int(tid)
                m["sid"] = self._want_sid
                m["sub-visibility"] = True
        except Exception as e:
            log.warning("multiview set_sub(%r) failed: %s", tid, e)

    def toggle_pause(self) -> None:
        m = self.video.mpv
        if m is None or self.url is None:
            return
        try:
            m.pause = not m.pause
        except Exception:
            pass

    def seek(self, secs: int) -> None:
        m = self.video.mpv
        if m is None or self.url is None:
            return
        if self._ts_capable:
            # Step the archive timeline (negative secs = further back). The
            # step size comes from Settings (mv_seek_step_min).
            step = getattr(self, "_ts_step_sec", 300)
            cur_off = self._ts_offset_now()
            new_off_min = (cur_off + (step if secs < 0 else -step)) / 60.0
            if new_off_min < 0.5:
                self._go_live()
            else:
                self._go_timeshift(new_off_min)
            return
        try:
            m.command("seek", secs, "relative")
        except Exception:
            pass

    def _ts_offset_now(self) -> float:
        """Seconds behind live for the currently loaded archive segment
        (0 at the live edge)."""
        if self._ts_seg_start is None:
            return 0.0
        try:
            tp = float(self.video.mpv.time_pos or 0)
        except Exception:
            tp = 0.0
        return max(0.0, time.time() - (self._ts_seg_start + tp))

    def _on_seek_value(self, val: int) -> None:
        """Slider released (or clicked): jump. On a timeshift cell the bar is
        the archive timeline (right edge = live, left = oldest archive)."""
        if self._ts_capable:
            depth = self._ts_depth_min()
            back_min = depth * (1.0 - val / 1000.0)
            if back_min < 2:
                self._go_live()
            else:
                self._go_timeshift(back_min)
            return
        m = self.video.mpv
        if m is None or self.url is None or self._win_span < 1:
            return
        target = self._win_start + val / 1000.0 * self._win_span
        try:
            m.command("seek", target, "absolute")
        except Exception:
            pass

    def _update_segments(self, depth_sec: float) -> str:
        """Feed the timeline programme-boundary ticks (and hover names) from
        the owner's EPG guide, and return the programme title under the
        playhead. The guide is re-queried at most once a minute."""
        if self._guide is None or self._item is None:
            return ""
        now = time.time()
        if now - self._progs_at > 60:
            self._progs_at = now
            try:
                self._progs = list(self._guide.programmes_in(
                    self._item, now - depth_sec, now))
            except Exception:
                self._progs = []
        win_start = now - depth_sec
        segs = []
        for p in self._progs:
            try:
                a = max(0.0, (p["start_timestamp"] - win_start) / depth_sec)
                b = min(1.0, (p["stop_timestamp"] - win_start) / depth_sec)
                tlabel = "%s–%s" % (
                    time.strftime("%H:%M",
                                  time.localtime(p["start_timestamp"])),
                    time.strftime("%H:%M",
                                  time.localtime(p["stop_timestamp"])))
            except Exception:
                continue
            segs.append((a, b, p.get("title") or "", tlabel))
        self._seek.set_segments(segs)
        content_time = now - self._ts_offset_now()
        for p in self._progs:
            try:
                if p["start_timestamp"] <= content_time < p["stop_timestamp"]:
                    return p.get("title") or ""
            except Exception:
                continue
        return ""

    def refresh_state(self) -> None:
        """Poll mpv for position/duration/pause and update the seek bar + pause
        glyph. Called on a timer by the window; robust to a not-yet-ready mpv.

        Finite content (catch-up / VOD / a DVR window) uses an absolute
        [0, duration] bar. A live stream has no duration, so it gets a rolling
        window from when the cell started playing to the live edge - scrubbing
        that seeks back through mpv's buffer (the same idea as timeshift, within
        what's been buffered this session)."""
        m = self.video.mpv
        if m is None or self.url is None:
            return
        try:
            paused = bool(m.pause)
        except Exception:
            paused = False
        self._pause.setVisible(paused)
        if paused:
            self._pause.raise_()
        self._sync_pause_btn()
        # Controls (pause + timeline + offset + LIVE) follow the overlay
        # auto-hide: revealed on hover, gone 2 s after the mouse rests - same
        # rhythm as the docked player's floating bars.
        show = self._overlays_on
        self._pause_btn.setVisible(show)
        if show:
            self._title.setVisible(bool(self.title))
        if self._ts_capable:
            # Archive timeline: the bar spans [now - depth, now]; the knob sits
            # at the current offset from live. Red ● LIVE pill at the edge, a
            # clickable ⏭ LIVE plus "-h:mm:ss" while behind - and programme
            # ticks/names from the EPG, like the docked timeline.
            depth_sec = self._ts_depth_min() * 60.0
            offset = self._ts_offset_now()
            live = offset < 30
            self._seek.setVisible(show)
            self._live_btn.setVisible(show)
            self._time.setVisible(show and not live)
            if show:
                self._apply_live_style(live)
                if not self._seek.dragging:
                    val = int((depth_sec - min(offset, depth_sec))
                              / depth_sec * 1000)
                    self._seek.blockSignals(True)
                    self._seek.setValue(val)
                    self._seek.blockSignals(False)
                if not live:
                    self._time.setText(f"-{_fmt(offset)}")
                prog = self._update_segments(depth_sec)
                base = self.title or ""
                self._title.setText(f"{base}  ·  {prog}" if prog else base)
                self._title.adjustSize()
                self._layout_controls()
                self._raise_controls()
            return
        self._live_btn.hide()
        try:
            pos = float(m.time_pos or 0)
        except Exception:
            self._seek.hide()
            self._time.hide()
            return
        try:
            dur = float(m.duration or 0)
        except Exception:
            dur = 0.0
        try:
            seekable = bool(m.seekable)
        except Exception:
            seekable = False
        if seekable and dur > 1:
            start, span, playhead = 0.0, dur, pos
        else:
            if self._live_start is None:
                self._live_start = pos
            self._live_start = min(self._live_start, pos)
            self._live_edge = max(self._live_edge, pos)
            start = self._live_start
            span = self._live_edge - self._live_start
            playhead = pos
        self._win_start = start
        self._win_span = span
        # The slider only makes sense once there's a scrubbable span; the
        # pause button shows with the overlays regardless.
        self._seek.setVisible(show and span > 2)
        self._time.setVisible(show and span > 2)
        if show and span > 2 and not self._seek.dragging:
            self._seek.blockSignals(True)
            self._seek.setValue(int((playhead - start) / span * 1000))
            self._seek.blockSignals(False)
            self._time.setText(f"{_fmt(playhead - start)} / {_fmt(span)}")
        if show:
            self._layout_controls()
            self._raise_controls()

    def clear(self) -> None:
        if self.video.mpv is not None:
            try:
                self.video.mpv.command("stop")
            except Exception:
                pass
        self.video.set_blank(True)
        self.url = None
        self.title = ""
        self._muted = True
        self._live_start = None
        self._live_edge = 0.0
        self._win_span = 0.0
        self._live_url = None
        self._item = None
        self._client = None
        self._ts_capable = False
        self._ts_seg_start = None
        self._ts_candidates = []
        self._guide = None
        self._progs = []
        self._progs_at = 0.0
        self._want_aid = None
        self._want_sid = None
        self.playlist_name = ""
        self._pl.setText("")
        self._pl.hide()
        self._seek.set_segments([])
        self._title.hide()
        self._pause.hide()
        self._pause_btn.hide()
        self._seek.hide()
        self._time.hide()
        self._live_btn.hide()
        self._empty.show()

    def shutdown(self) -> None:
        try:
            self.video.shutdown()
        except Exception:
            pass

    # -- mouse ---------------------------------------------------------------

    def _window_frameless(self) -> bool:
        return bool(self.window().windowFlags()
                    & Qt.WindowType.FramelessWindowHint)

    def _on_press(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self.context_requested.emit(
                self, event.globalPosition().toPoint())
            return
        if event.button() == Qt.MouseButton.LeftButton:
            # Focus only (audio). Mute lives on the right-click menu, so a
            # left-click - e.g. to grab and drag the frameless window - never
            # mutes by accident.
            self.focus_requested.emit(self)
            if self._window_frameless():
                self._drag_from = event.position().toPoint()

    def _on_move(self, event) -> None:
        self.hovered.emit(self)
        d = self._drag_from
        if d is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            if (event.position().toPoint() - d).manhattanLength() > 6:
                self._drag_from = None
                handle = self.window().windowHandle()
                if handle is not None:
                    handle.startSystemMove()

    def _on_release(self, event) -> None:
        self._drag_from = None


class _MultiviewWindow(QWidget):
    """Top-level 2x2 grid. Flags (frameless / always-on-top) come from the
    owner's settings; closing hands control back so every cell's mpv is torn
    down cleanly."""

    def __init__(self, owner) -> None:
        super().__init__()
        self._owner = owner
        self.setWindowTitle(tr("mv_title"))
        self.setObjectName("MvWin")
        self.setStyleSheet("#MvWin { background:#000000; }")
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        # Grid size from Settings: 2 (1x2), 4 (2x2), 6 (2x3) or 9 (3x3)
        # windows. Applied when the multiview window is (re)created.
        try:
            count = int(owner.settings.value("mv_cells", 4))
        except (TypeError, ValueError):
            count = 4
        if count not in (2, 4, 6, 9):
            count = 4
        cols = {2: 2, 4: 2, 6: 3, 9: 3}[count]
        self.cells: list[_MultiviewCell] = []
        for i in range(count):
            c = _MultiviewCell(i + 1, self)
            c.focus_requested.connect(self._focus_cell)
            c.context_requested.connect(self._cell_context_menu)
            c.hovered.connect(lambda _c: self._reveal_overlays())
            c.maximize_requested.connect(self._toggle_max)
            c.video.video_key_press.connect(
                lambda ev, cell=c: self._cell_key(cell, ev))
            grid.addWidget(c, i // cols, i % cols)
            self.cells.append(c)
        self._focused: _MultiviewCell | None = None
        self._cursor_hidden = False
        # Floating close button (frameless has no title-bar X). Auto-hides with
        # the other overlays. Parented to the TOP-RIGHT cell's video surface,
        # not the window: a plain child of the window renders *behind* the
        # sibling QOpenGLWidgets (so it was invisible); a child of the GL widget
        # composites on top, the same trick the cell's title/border use.
        self._close_host = self.cells[cols - 1].video   # top-right cell
        self._close_btn = _CloseButton(self._close_host)
        self._close_btn.setToolTip(tr("mv_close"))
        self._close_btn.clicked.connect(self.close)
        self._close_btn.hide()
        self._overlay_timer = QTimer(self)
        self._overlay_timer.setSingleShot(True)
        self._overlay_timer.timeout.connect(self._hide_overlays)
        # Poll cell state (position / pause) for the seek bars.
        self._state_timer = QTimer(self)
        self._state_timer.setInterval(500)
        self._state_timer.timeout.connect(self._refresh_states)
        self._state_timer.start()
        self.apply_settings()
        self.setWindowFlags(self._flags())
        self._focus_cell(self.cells[0])

    def apply_settings(self) -> None:
        """Pull the tunable knobs (Settings → Multiview) into the live window:
        overlay/cursor auto-hide delay and the arrow-key timeshift step."""
        s = self._owner.settings
        try:
            secs = float(s.value("mv_autohide_secs", 2))
        except (TypeError, ValueError):
            secs = 2.0
        self._overlay_timer.setInterval(max(500, int(secs * 1000)))
        try:
            step_min = float(s.value("mv_seek_step_min", 5))
        except (TypeError, ValueError):
            step_min = 5.0
        for c in self.cells:
            c._ts_step_sec = max(60, int(step_min * 60))

    # -- window flags --------------------------------------------------------

    def _flags(self) -> "Qt.WindowType":
        flags = Qt.WindowType.Window
        s = self._owner.settings
        wayland = "wayland" in QApplication.platformName().lower()
        if s.value("mv_on_top", "false") == "true" and not wayland:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        if s.value("mv_frameless", "true") == "true":
            flags |= Qt.WindowType.FramelessWindowHint
        return flags

    def _apply_flags(self) -> None:
        geo = self.geometry()
        self.setWindowFlags(self._flags())
        self.setGeometry(geo)
        self.show()
        self.raise_()

    def _set_on_top(self, on: bool) -> None:
        self._owner.settings.setValue("mv_on_top", "true" if on else "false")
        self._apply_flags()

    def _set_frameless(self, hidden: bool) -> None:
        self._owner.settings.setValue(
            "mv_frameless", "true" if hidden else "false")
        self._apply_flags()

    # -- streams / focus -----------------------------------------------------

    def add_stream(self, url: str, title: str, cell: int | None = None,
                   item: dict | None = None, client=None, guide=None,
                   playlist: str = "") -> None:
        if cell is not None and 0 <= cell < len(self.cells):
            target = self.cells[cell]
        else:
            target = next((c for c in self.cells if c.url is None),
                          self._focused or self.cells[0])
        target.play(url, title, item, client, guide, playlist)
        # Settings: a newly added channel can either take audio focus (the
        # default) or start muted, leaving the current audio alone.
        unmute = (self._owner.settings.value("mv_new_unmuted", "true")
                  == "true")
        if (unmute or self._focused is None or self._focused.url is None
                or self._focused is target):
            self._focus_cell(target)
        else:
            target.set_muted(True)
        self._reveal_overlays()

    def _focus_cell(self, cell: "_MultiviewCell") -> None:
        for c in self.cells:
            c.set_focused(c is cell)
            c.set_muted(c is not cell)
        self._focused = cell

    def _swap_cells(self, a: "_MultiviewCell", b: "_MultiviewCell") -> None:
        """Exchange the two cells' videos (swap places / move into an empty
        cell), then re-apply audio focus."""
        sa = (a._live_url, a.title, a._item, a._client, a._guide,
              a.playlist_name)
        sb = (b._live_url, b.title, b._item, b._client, b._guide,
              b.playlist_name)
        a.play(*sb) if sb[0] else a.clear()
        b.play(*sa) if sa[0] else b.clear()
        self._focus_cell(self._focused if self._focused in self.cells else a)

    def _cell_context_menu(self, cell: "_MultiviewCell", pos) -> None:
        m = QMenu(self)
        if cell.url is not None:
            m.addAction(tr("mv_unmute") if cell.is_muted() else tr("mv_mute"),
                        lambda: cell.set_muted(not cell.is_muted()))
            move = m.addMenu(tr("mv_move"))
            for other in self.cells:
                if other is cell:
                    continue
                move.addAction(
                    tr("mv_cell", n=other.number),
                    lambda _c=False, o=other: self._swap_cells(cell, o))
            # Audio / subtitle track choice, only when the stream actually
            # offers alternatives (single-track streams keep the menu clean).
            auds, subs = cell.tracks()
            if len(auds) > 1:
                am = m.addMenu(tr("opt_audio_track"))
                for t in auds:
                    act = am.addAction(cell.track_label(t))
                    act.setCheckable(True)
                    act.setChecked(bool(t.get("selected")))
                    act.triggered.connect(
                        lambda _c=False, tid=t.get("id"), c=cell:
                        c.set_audio(tid))
            if subs:
                sm = m.addMenu(tr("opt_subtitles"))
                any_sel = any(t.get("selected") for t in subs)
                off = sm.addAction(tr("opt_off"))
                off.setCheckable(True)
                off.setChecked(not any_sel)
                off.triggered.connect(
                    lambda _c=False, c=cell: c.set_sub(None))
                for t in subs:
                    act = sm.addAction(cell.track_label(t))
                    act.setCheckable(True)
                    act.setChecked(bool(t.get("selected")))
                    act.triggered.connect(
                        lambda _c=False, tid=t.get("id"), c=cell:
                        c.set_sub(tid))
            m.addAction(tr("mv_remove_cell"), cell.clear)
            m.addSeparator()
        if "wayland" not in QApplication.platformName().lower():
            act = m.addAction(tr("popout_always_on_top"))
            act.setCheckable(True)
            act.setChecked(bool(self.windowFlags()
                                & Qt.WindowType.WindowStaysOnTopHint))
            act.toggled.connect(self._set_on_top)
        frameless = bool(self.windowFlags()
                         & Qt.WindowType.FramelessWindowHint)
        bar = m.addAction(tr("popout_show_titlebar") if frameless
                          else tr("popout_hide_titlebar"))
        bar.triggered.connect(lambda: self._set_frameless(not frameless))
        m.addSeparator()
        m.addAction(tr("mv_close"), self.close)
        exec_menu_over_video(m, pos)

    # -- overlays + polling + keyboard --------------------------------------

    def _toggle_max(self) -> None:
        """Double-click a cell to maximize the whole multiview window (and
        double-click again to restore it)."""
        if self.isMaximized() or self.isFullScreen():
            self._show_cursor()
            self.showNormal()
        else:
            self.showMaximized()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_close_btn()

    def enterEvent(self, event) -> None:
        # Pointer entering the window reveals the overlays (incl. the close
        # button) right away, before any move over a cell fires.
        self._reveal_overlays()
        super().enterEvent(event)

    def _place_close_btn(self) -> None:
        # Top-right corner of the top-right cell = top-right of the window.
        v = self._close_host
        self._close_btn.move(v.width() - self._close_btn.width() - 10, 10)
        self._close_btn.raise_()

    def _show_cursor(self) -> None:
        if self._cursor_hidden:
            self._cursor_hidden = False
            self.unsetCursor()
            for c in self.cells:
                c.video.unsetCursor()

    def _reveal_overlays(self) -> None:
        self._show_cursor()
        for c in self.cells:
            c.show_overlays(True)
        self._place_close_btn()
        self._close_btn.show()
        self._refresh_states()   # so a hovered cell's seek bar appears at once
        self._overlay_timer.start()

    def _hide_overlays(self) -> None:
        # Don't yank the controls away mid-interaction: while a scrub is in
        # progress or the pointer is resting on a control (the controls are
        # siblings, so hovering them doesn't re-arm the cell-hover timer),
        # postpone and check again.
        for c in self.cells:
            if (c._seek.dragging or c._seek.underMouse()
                    or c._pause_btn.underMouse() or c._live_btn.underMouse()):
                self._overlay_timer.start()
                return
        if self._close_btn.underMouse():
            self._overlay_timer.start()
            return
        for c in self.cells:
            c.show_overlays(False)
        self._close_btn.hide()
        # The pointer rests over the video whatever the window state - hide it
        # along with the overlays (any movement brings both back). Cursor
        # shapes are per-widget, so this only blanks it over multiview.
        if not self._cursor_hidden:
            self._cursor_hidden = True
            self.setCursor(Qt.CursorShape.BlankCursor)
            for c in self.cells:
                c.video.setCursor(Qt.CursorShape.BlankCursor)

    def _refresh_states(self) -> None:
        for c in self.cells:
            c.refresh_state()

    def _cell_key(self, cell: "_MultiviewCell", event) -> None:
        if event.key() == Qt.Key.Key_Left:
            cell.seek(-10)
        elif event.key() == Qt.Key.Key_Right:
            cell.seek(10)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space and self._focused is not None:
            self._focused.toggle_pause()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            if self.isMaximized() or self.isFullScreen():
                self._show_cursor()
                self.showNormal()   # step out of maximize first
            else:
                self.close()        # no title-bar X when frameless
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        event.ignore()
        self._owner._close_multiview()


class _MultiviewMixin:
    """MainWindow mixin: the multiview grid of independent live streams."""
    def _add_channel_to_multiview(self, it, cell: int | None = None) -> None:
        """Build the live stream URL for a channel item (using the currently
        active playlist's client) and drop it into multiview cell *cell*
        (0..3), or the first free cell when None."""
        sid = it.get("stream_id")
        if sid is None:
            return
        fmt = self.settings.value("stream_format", "ts")
        try:
            url = self.client.live_url(sid, fmt)
        except Exception:
            url = it.get("_url")
        # Watching in multiview counts as watching - record it in History
        # just like docked playback does.
        try:
            self.history.add(
                url, it.get("name") or it.get("title") or "",
                it.get("stream_icon"), self._item_key(it), "live",
                extra={"stream_id": sid, "num": it.get("num"),
                       "tv_archive": it.get("tv_archive"),
                       "tv_archive_duration": it.get("tv_archive_duration")})
        except Exception:
            pass   # hosts without a history store (tests) just skip it
        self.add_to_multiview(
            url, it.get("name") or it.get("title") or "", cell,
            item=it, client=self.client, guide=getattr(self, "xmltv", None),
            playlist=self._active_playlist_name())

    def _active_playlist_name(self) -> str:
        store = getattr(self, "playlist_store", None)
        pl = store.active() if store else None
        return (pl or {}).get("name") or ""

    def _ensure_multiview_window(self):
        """Create the multiview window if needed, then bring it to the front.
        raise_ + activateWindow is the reliable cross-platform way back to it -
        on macOS separate app windows don't cycle with Cmd+Tab."""
        self._maybe_show_multiview_info()
        if self._multiview_win is None:
            self._multiview_win = _MultiviewWindow(self)
            geo = (self.settings.value("mv_geometry")
                   if self.settings.value("mv_remember_geo", "true") == "true"
                   else None)
            if not (geo is not None
                    and self._multiview_win.restoreGeometry(geo)):
                self._multiview_win.resize(960, 560)
        w = self._multiview_win
        w.show()
        w.raise_()
        w.activateWindow()
        return w

    def _show_multiview(self) -> None:
        """Sidebar ▦ button. Opens multiview - and, pressed again while the
        grid is open but IDLE (no streams), closes it, so the button works
        as a toggle for an empty window instead of just re-raising it. A
        grid with running streams is only brought to the front: a toggle
        there would let one stray click drop every connection."""
        w = self._multiview_win
        if (w is not None and w.isVisible()
                and not any(c.url for c in w.cells)):
            self._close_multiview()
            return
        self._ensure_multiview_window()

    def _docked_context_menu(self, global_pos) -> None:
        """Right-click on the docked video: a complete menu - pause/stop,
        fullscreen, the full audio/subtitle/aspect/... options (same as the
        options button and the menu bar), and send-to-multiview."""
        p = getattr(self, "player", None)
        if p is None or not getattr(p, "current_url", None):
            return
        menu = QMenu(self)
        paused = getattr(p, "_paused", False)
        menu.addAction(tr("btn_play") if paused else tr("btn_pause"),
                       p.toggle_pause)
        menu.addAction(tr("btn_stop"), p.stop)
        # In fullscreen the same toggle leaves it, so label it accordingly.
        menu.addAction(
            tr("tooltip_exit_fullscreen") if getattr(p, "_fs_ui", False)
            else tr("tooltip_fullscreen"),
            lambda: p.double_clicked.emit())
        menu.addSeparator()
        p.populate_options_menu(menu)
        menu.addSeparator()
        menu.addAction(tr("mv_add"), self._send_docked_to_multiview)
        exec_menu_over_video(menu, global_pos)

    def _send_docked_to_multiview(self) -> None:
        p = getattr(self, "player", None)
        if p is None or not getattr(p, "current_url", None):
            return
        url = p.current_url
        it = getattr(self, "_playing_item", None)
        title = ((it or {}).get("name") or (it or {}).get("title")
                 or getattr(self, "_base_title", "") or "")
        self.add_to_multiview(url, title, item=it,
                              client=getattr(self, "client", None),
                              guide=getattr(self, "xmltv", None),
                              playlist=self._active_playlist_name())

    def add_to_multiview(self, url: str, title: str,
                         cell: int | None = None, item: dict | None = None,
                         client=None, guide=None, playlist: str = "") -> None:
        if not url:
            return
        # Free the docked player's connection: otherwise a single-connection
        # account refuses the multiview cell (the same channel then only plays
        # docked), and you'd have two streams and two audio tracks at once.
        # Optional (Settings): an account with spare connections can keep the
        # docked player running instead.
        if self.settings.value("mv_stop_docked", "true") == "true":
            self._stop_docked_for_multiview()
        self._ensure_multiview_window().add_stream(
            url, title, cell, item=item, client=client, guide=guide,
            playlist=playlist)

    def _maybe_show_multiview_info(self) -> None:
        """One-time notice that multiview needs enough provider connections,
        with a 'don't show again' opt-out. A themed QDialog (not QMessageBox,
        whose checkbox rendered invisible against the dark message box)."""
        if self.settings.value("mv_info_seen", "false") == "true":
            return
        from PyQt6.QtWidgets import QDialog
        d = QDialog(self)
        d.setWindowTitle(tr("mv_info_title"))
        lay = QVBoxLayout(d)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(14)
        d.setStyleSheet("QDialog { background:#1b1b20; }")
        msg = QLabel(tr("mv_info_body"))
        msg.setWordWrap(True)
        msg.setMinimumWidth(420)
        msg.setStyleSheet("color:#ECECF1; font-size:13px;")
        lay.addWidget(msg)
        cb = QCheckBox(tr("dont_show_again"))
        # Force a fully self-drawn indicator: on macOS the native check box
        # renders as an invisible same-colour square against the dark dialog.
        # An explicit bordered box that fills with the accent when checked is
        # visible regardless of platform/native styling.
        cb.setStyleSheet(
            "QCheckBox { color:#ECECF1; font-size:13px; spacing:8px; }"
            "QCheckBox::indicator { width:18px; height:18px;"
            " border:1px solid #6A6A75; border-radius:3px; background:#2A2A32; }"
            "QCheckBox::indicator:checked {"
            " background:#e5354b; border:1px solid #e5354b; }")
        lay.addWidget(cb)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(d.accept)
        lay.addWidget(bb)
        d.exec()
        if cb.isChecked():
            self.settings.setValue("mv_info_seen", "true")

    def _maybe_close_multiview_for_playback(self) -> None:
        """Starting playback in the main window while multiview streams run:
        offer to close multiview so its provider connections are freed (on a
        tight connection limit the new stream would otherwise be refused).
        Keeping both is allowed - an account with spare simultaneous
        connections can - and that answer sticks for this multiview window,
        so zapping doesn't re-ask on every channel."""
        win = self._multiview_win
        if win is None or not any(c.url for c in win.cells):
            return
        # Settings can pre-answer the question: always close multiview
        # silently, or always keep it (no dialog either way).
        mode = str(self.settings.value("mv_conflict_mode", "ask"))
        if mode == "keep":
            return
        if mode == "close":
            self._close_multiview()
            return
        if getattr(win, "_conflict_kept", False):
            return
        from PyQt6.QtWidgets import QDialog
        d = QDialog(self)
        d.setWindowTitle(tr("mv_conflict_title"))
        d.setStyleSheet("QDialog { background:#1b1b20; }")
        lay = QVBoxLayout(d)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(14)
        msg = QLabel(tr("mv_conflict_body"))
        msg.setWordWrap(True)
        msg.setMinimumWidth(400)
        msg.setStyleSheet("color:#ECECF1; font-size:13px;")
        lay.addWidget(msg)
        bb = QDialogButtonBox()
        bb.addButton(tr("mv_close"), QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton(tr("mv_keep"), QDialogButtonBox.ButtonRole.RejectRole)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        lay.addWidget(bb)
        d.exec()
        if d.result() == QDialog.DialogCode.Accepted:
            self._close_multiview()
        else:
            win._conflict_kept = True

    def _stop_docked_for_multiview(self) -> None:
        p = getattr(self, "player", None)
        if p is None or not getattr(p, "current_url", None):
            return
        try:
            self.rec.finish_all_inplayer("multiview")
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass
        try:
            self.wake.release()
        except Exception:
            pass
        self._apply_play_icon()

    def _apply_multiview_settings(self) -> None:
        """Push freshly saved Settings → Multiview values into a live window
        (auto-hide delay, seek step, window flags). A changed grid size needs
        a rebuild - close the window so the next open uses the new layout."""
        win = self._multiview_win
        if win is None:
            return
        try:
            count = int(self.settings.value("mv_cells", 4))
        except (TypeError, ValueError):
            count = 4
        if count in (2, 4, 6, 9) and count != len(win.cells):
            self._close_multiview()
            return
        win.apply_settings()
        win._apply_flags()

    def _close_multiview(self) -> None:
        win = self._multiview_win
        if win is None:
            return
        if self.settings.value("mv_remember_geo", "true") == "true":
            self.settings.setValue("mv_geometry", win.saveGeometry())
        self._multiview_win = None
        for c in win.cells:
            c.shutdown()
        win.deleteLater()

    def _close_multiview_if_active(self) -> None:
        if self._multiview_win is not None:
            self._close_multiview()
