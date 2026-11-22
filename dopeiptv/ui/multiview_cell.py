"""One cell of the multiview grid (``_MultiviewCell``): a bare mpv video
surface of its own with its position number, an auto-hiding title strip
and seek bar, a small spinner while its stream starts, click-to-focus and
its own mute, pause and seek; the round close button the grid's window
puts over a cell (``_CloseButton``) and the pause / play glyphs it draws
(``_glyph``). The grid is ui/multiview_window.py, the window's side
ui/mw_multiview.py.
"""
from __future__ import annotations

import time
from datetime import datetime
from functools import partial

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

from ..core import stream_address
from ..core.log import log
from ..i18n import tr
from ..media.loading_overlay import LoadingOverlay, quiet
from ..media.seek_slider import _SeekSlider
from ..media.video_thread import make_video_surface, swap_for_fallback


def _fmt(secs: float) -> str:
    secs = int(max(0, secs))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _as_int(v) -> int:
    # Provider flags come as ints, numeric strings or junk; the main window
    # reads them defensively and so must a cell, or adding a channel whose
    # tv_archive_duration is "7.0" raised instead of playing it.
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


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
        # The source (id) the stream is from: whose account's connection
        # it takes (core/connections.py).
        self.source_id = ""
        # Nerd's stream options were set on this cell's mpv (nerd.apply_stream).
        self._nerd_applied = False
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
        # Drawn on a thread of its own like the docked player's, unless that
        # is off or cannot start (media/video_thread.py).
        self.video = make_video_surface(self)
        self.video.setMinimumSize(0, 0)
        # Hover must reveal the overlays (title, controls, the window's close
        # button) without a pressed button - same as the docked player. Without
        # tracking, mouse-move only fires mid-drag and the overlays effectively
        # never appear.
        self.video.setMouseTracking(True)
        lay.addWidget(self.video)
        self._wire_video(self.video)

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
        self._title.setTextFormat(Qt.TextFormat.PlainText)     # channel and programme
        self._title.setStyleSheet(
            "background:rgba(0,0,0,150); color:#ECECF1; padding:3px 8px;"
            "font-size:12px; font-weight:600;")
        self._title.hide()
        # Which playlist/account this cell streams from - a muted sub-line
        # under the title (cells can come from different accounts).
        self.playlist_name = ""
        self._pl = QLabel("", self.video)
        self._pl.setTextFormat(Qt.TextFormat.PlainText)
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
        # A small spinner while the cell's stream starts (it lets clicks
        # through too), after a moment so a quick start never flashes it;
        # the state poll ends it once mpv plays. The cell's number shows
        # inside its ring on hover.
        self._loading = LoadingOverlay(self.video, compact=True)
        self._loading_on = False
        self._loading_timer = QTimer(self)
        self._loading_timer.setSingleShot(True)
        self._loading_timer.setInterval(300)
        self._loading_timer.timeout.connect(self._loading_due)
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

    def _wire_video(self, v) -> None:
        v.video_mouse_press.connect(self._on_press)
        v.video_mouse_move.connect(self._on_move)
        v.video_mouse_release.connect(self._on_release)
        v.video_dbl_click.connect(self.maximize_requested)
        v.playback_error.connect(self._on_error)
        fallback = getattr(v, "fallback", None)
        if fallback is not None:
            fallback.connect(self._video_fallback)

    def _video_fallback(self, _reason: str) -> None:
        """This cell's picture could not be drawn on its own thread: the
        window-thread surface takes its place, its labels and controls with
        it, and a stream that was starting starts again."""
        if getattr(self.video, "threaded", False) is not True:
            return
        self.video = swap_for_fallback(self.video, self._wire_video)
        if self.url:
            QTimer.singleShot(0, partial(self._replay, self.url))

    def _replay(self, url: str) -> None:
        if self.url == url:
            self.play(url, self.title, item=self._item, client=self._client,
                      guide=self._guide, playlist=self.playlist_name)

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
        if not self._loading.isHidden():
            self._loading.place(r)
        self._layout_controls()

    # -- playback ------------------------------------------------------------

    def play(self, url: str, title: str, item: dict | None = None,
             client=None, guide=None, playlist: str = "",
             start: float = 0.0) -> bool:
        # The last look, as the docked player's (core/stream_address.py):
        # what is no stream, whoever asks, is never handed to mpv.
        why = stream_address.refusal(url or "", remote=False)
        if why:
            log.warning("multiview: an address that is no stream (%s) is not opened", why)
            return False
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
        # and a client that can build archive URLs - not a portal, whose
        # archive is asked for by programme (the main player's alone).
        days = _as_int((item or {}).get("tv_archive_duration"))
        self._ts_days = days
        self._ts_capable = bool(
            item and _as_int((item or {}).get("tv_archive")) and days > 0
            and (item or {}).get("stream_id") is not None
            and client is not None and hasattr(client, "timeshift_urls")
            and not getattr(client, "archive_by_programme", False))
        self._live_start = None    # fresh rolling window for the new stream
        self._live_edge = 0.0
        self._win_span = 0.0
        self._title.setText(title or "")
        self._title.adjustSize()
        self._empty.hide()
        return self._mpv_play(url, start)

    def _mpv_play(self, url: str, start: float = 0.0) -> bool:
        """Point this cell's mpv at *url* without disturbing the cell's channel
        / live-url / timeshift bookkeeping (used both for the initial play and
        for archive-segment / go-live swaps). *start* is an offset in seconds
        to begin at - a film or episode handed over from the docked player
        continues where it was, instead of restarting from zero."""
        from ..core import nerd
        if nerd.blocks_stream(url, (nerd.source() or {}).get("_kind")):
            log.info("multiview: not started - no VPN up (Nerd's VPN guard)")
            return False
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
            # Start offset for the next file only - always assigned, so a
            # handover position can't leak into a later load (a timeshift
            # segment or a go-live swap through this same mpv).
            m["start"] = str(float(start)) if start and start > 1 else "none"
            # Nerd: the source's user agent, headers, proxy and route (own
            # DNS, IP version, a trusted certificate), as the docked player
            # takes them - nothing while Nerd is off and never was. For
            # everyone: an internet source's stream through the app's bridge
            # and its certificate checked, or the cell plays nothing.
            try:
                self._nerd_applied = nerd.apply_stream(
                    m, nerd.source(), self._nerd_applied, url=url)
            except nerd.CertificateUnchecked as e:
                log.warning("multiview: a stream whose certificate cannot be checked here is "
                            "not opened (%s)", e)
                try:
                    m.command("stop")
                except Exception:
                    pass
                self._loading_end()
                self._title.setText(tr("player_tls_unverifiable"))
                self._title.setVisible(True)
                self._title.adjustSize()
                return False
            except Exception as e:
                log.debug("multiview: Nerd's stream options not set: %s", e)
            # A SAT>IP source's RTSP transport, for this stream alone.
            from ..media.embedded import load_with_options, rtsp_file_options
            opts = rtsp_file_options(url)
            if opts:
                load_with_options(m, url, opts)
            else:
                m.play(url)
        except Exception as e:
            log.warning("multiview cell play failed: %s: %s",
                        type(e).__name__, e)
            self._loading_end()
            return False
        self._loading_begin()
        return True

    # -- the spinner while a stream starts (never in the stream's way) -------

    @quiet
    def _loading_begin(self) -> None:
        self._loading_on = True
        self._loading_timer.start()

    @quiet
    def _loading_end(self) -> None:
        self._loading_on = False
        self._loading_timer.stop()
        if not self._loading.isHidden():
            self._loading.hide()

    @quiet
    def _loading_due(self) -> None:
        if self._loading_on and self.url is not None and self._loading.isHidden():
            self._loading.place(self.video.rect())
            self._loading.show()
            self._loading.raise_()
            if self._num.isVisible():
                self._num.raise_()

    @quiet
    def _loading_check(self, m, paused: bool) -> None:
        """The state poll, while the stream starts: it plays (mpv is no
        longer idle), so the spinner goes; paused, the pause mark has the
        middle."""
        try:
            playing = (not paused and not m.core_idle
                       and m.time_pos is not None)
        except Exception:
            playing = False
        if playing:
            self._loading_end()
        elif paused:
            if not self._loading.isHidden():
                self._loading.hide()
        elif not self._loading_timer.isActive():
            self._loading_due()

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
        self._loading_end()
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
        self._check_scrambled()

    def _check_scrambled(self) -> None:
        """A box's channel that will not play: one the box cannot decrypt is
        said so in the cell (asked off the window's thread, of the box's
        own streams only)."""
        check = getattr(self._client, "scrambled", None)
        url = self._live_url
        if not callable(check) or not url:
            return
        from PyQt6.QtCore import QThreadPool

        from ..core.workers import run_async
        self._scramble_url = url
        run_async(QThreadPool.globalInstance(), check, self._scramble_said, None, url)

    def _scramble_said(self, found) -> None:
        from PyQt6 import sip
        if sip.isdeleted(self) or found is not True:
            return
        if self._live_url != getattr(self, "_scramble_url", None) or self._ts_seg_start is not None:
            return
        name = (self._item or {}).get("name") or ""
        self._title.setText(tr("mv_cell_scrambled", title=name))
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

    def resume_pos(self) -> float:
        """Where finite content (a film / an episode) is right now, so a
        reload can pick it up there. 0 for live and catch-up cells, whose
        position is a point in a stream rather than in a file."""
        m = self.video.mpv
        if m is None or self.url is None or self._ts_capable:
            return 0.0
        try:
            if not (m.duration and float(m.duration) > 1):
                return 0.0
            return max(0.0, float(m.playback_time or 0.0))
        except Exception:
            return 0.0

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
        if getattr(self, "_loading_on", False):
            self._loading_check(m, paused)
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
        self._loading_end()
        if self.video.mpv is not None:
            try:
                self.video.mpv.command("stop")
            except Exception:
                pass
        self.video.set_blank(True)
        self.url = None
        self.title = ""
        self.source_id = ""
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

    def _grid(self):
        """The multiview window this cell sits in, when there is one (a cell
        built standalone in a test has none)."""
        win = self.window()
        return win if hasattr(win, "resize_edges_at") else None

    def _on_press(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self.context_requested.emit(
                self, event.globalPosition().toPoint())
            return
        if event.button() == Qt.MouseButton.LeftButton:
            # On an edge of a frameless window the press resizes it - not a
            # focus change, since grabbing the frame shouldn't move the audio.
            gpos = event.globalPosition().toPoint()
            grid = self._grid()
            edges = grid.resize_edges_at(gpos) if grid is not None else None
            if edges:
                grid.start_resize(edges, gpos)
                return
            # Focus only (audio). Mute lives on the right-click menu, so a
            # left-click - e.g. to grab and drag the frameless window - never
            # mutes by accident.
            self.focus_requested.emit(self)
            if self._window_frameless():
                self._drag_from = event.position().toPoint()

    def _on_move(self, event) -> None:
        # Reveals the overlays (and restores the cursor), so the edge shape
        # below is applied after it, not before.
        self.hovered.emit(self)
        gpos = event.globalPosition().toPoint()
        grid = self._grid()
        if grid is not None and grid.resizing is not None:
            if event.buttons() & Qt.MouseButton.LeftButton:
                grid.drag_resize(gpos)
                return
            grid.resizing = None
        d = self._drag_from
        if d is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            if (event.position().toPoint() - d).manhattanLength() > 6:
                self._drag_from = None
                handle = self.window().windowHandle()
                if handle is not None:
                    handle.startSystemMove()
            return
        if grid is not None:
            grid.apply_edge_cursor(self, grid.resize_edges_at(gpos))

    def _on_release(self, event) -> None:
        self._drag_from = None
        grid = self._grid()
        if grid is not None:
            grid.resizing = None
