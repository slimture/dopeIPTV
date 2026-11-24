"""The multiview grid's own window (``_MultiviewWindow``): 2, 4, 6 or 9
cells (ui/multiview_cell.py) in a top-level window of its own, frameless
and on top as Settings say, dragged and resized by its cells, a cell's
menu, the focused cell's keys; closing it hands control back to the main
window so every cell's mpv is let go cleanly (ui/mw_multiview.py).
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication, QGridLayout, QMenu, QWidget

from ..i18n import tr
from ..media.video_thread import WindowChanges
from .widgets import (
    drag_frameless_resize, frameless_resize_edges, popup_menu_over_video,
    resize_edge_cursor, start_frameless_resize)
from .multiview_cell import _CloseButton, _MultiviewCell


class _MultiviewWindow(WindowChanges, QWidget):
    """Top-level 2x2 grid. Flags (frameless / always-on-top) come from the
    owner's settings; closing hands control back so every cell's mpv is torn
    down cleanly."""

    MIN_W = 480      # a grid of cells needs more room than a single video
    MIN_H = 300

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
        # Frameless windows have no grips: the cells' edges are the handles
        # (see resize_edges_at). `resizing` is only set where the window
        # manager won't take the drag (macOS).
        self.resizing: dict | None = None
        self._edge_cursor_on = False
        # Floating close button (frameless has no title-bar X). Auto-hides with
        # the other overlays. Parented to the TOP-RIGHT cell's video surface,
        # not the window: a plain child of the window renders *behind* the
        # sibling QOpenGLWidgets (so it was invisible); a child of the GL widget
        # composites on top, the same trick the cell's title/border use.
        self._close_host = self.cells[cols - 1].video   # top-right cell
        self._close_btn = _CloseButton(self._close_host)
        self._close_btn.setToolTip(tr("mv_close"))
        self._close_btn.setAccessibleName(tr("mv_close"))      # a drawn ✕
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

    # -- resizing a frameless grid ------------------------------------------

    def resize_edges_at(self, gpos) -> "Qt.Edge":
        """Which window edges the pointer can grab. The grid is title-bar-less
        by default, so - exactly like the pop-out - the video's own edges are
        the resize handles. Cells sit flush against the window edges, so
        whichever cell the pointer is over answers for the edge it touches."""
        return frameless_resize_edges(self, gpos)

    def start_resize(self, edges, gpos) -> None:
        self.resizing = start_frameless_resize(self, edges, gpos)

    def drag_resize(self, gpos) -> None:
        drag_frameless_resize(self, self.resizing, gpos,
                              self.MIN_W, self.MIN_H)

    def apply_edge_cursor(self, cell, edges) -> None:
        """Resize cursor while hovering an edge, so the grab band can be found
        without a visible frame."""
        if edges:
            cell.video.setCursor(resize_edge_cursor(edges))
            self._edge_cursor_on = True
        elif self._edge_cursor_on:
            for c in self.cells:
                c.video.unsetCursor()
            self._edge_cursor_on = False

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
                   playlist: str = "", start: float = 0.0, source: str = "") -> None:
        if cell is not None and 0 <= cell < len(self.cells):
            target = self.cells[cell]
        else:
            target = next((c for c in self.cells if c.url is None),
                          self._focused or self.cells[0])
        target.play(url, title, item, client, guide, playlist, start)
        target.source_id = source
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
        cell), then re-apply audio focus. Each stream is reloaded, so a film
        carries its position across (live keeps starting at the live edge)."""
        sa = (a._live_url, a.title, a._item, a._client, a._guide,
              a.playlist_name, a.resume_pos())
        sb = (b._live_url, b.title, b._item, b._client, b._guide,
              b.playlist_name, b.resume_pos())
        src_a, src_b = a.source_id, b.source_id
        a.play(*sb) if sb[0] else a.clear()
        b.play(*sa) if sa[0] else b.clear()
        a.source_id = src_b if sb[0] else ""
        b.source_id = src_a if sa[0] else ""
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
        link = getattr(self._owner, "add_settings_link", None)
        if callable(link):
            link(m, "multiview")      # its size, frame, sound and controls
        m.addAction(tr("mv_close"), self.close)
        popup_menu_over_video(m, pos)

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
        # shapes are per-widget, so this only blanks it over multiview. A
        # pointer resting on a resize edge keeps its handle shape instead.
        if self._edge_cursor_on:
            return
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
        self._owner._close_multiview(hand_back=True)
