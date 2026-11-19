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

from functools import partial

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QCheckBox, QDialogButtonBox, QLabel, QMenu, QMessageBox, QVBoxLayout,
)

from ..core.log import log
from ..i18n import tr
from ..media import players
# The cell and the grid's window live in modules of their own; both stay
# importable from here.
from .multiview_cell import _MultiviewCell, _as_int  # noqa: F401
from .multiview_window import _MultiviewWindow
from .widgets import popup_menu_over_video


class _MultiviewMixin:
    """MainWindow mixin: the multiview grid of independent live streams."""
    def _add_channel_to_multiview(self, it, cell: int | None = None) -> None:
        """Build the live stream URL for a channel item (using the currently
        active playlist's client) and drop it into multiview cell *cell*
        (0..3), or the first free cell when None."""
        sid = it.get("stream_id")
        if sid is None:
            return
        if not getattr(self, "_kids_allows", lambda _it, _m="live": True)(it, "live"):
            return          # a kids profile: nothing for adults (mw_profiles)
        fmt = self.settings.value("stream_format", "ts")

        def address():
            # A box that transcodes says which address (ui/mw_box.py).
            play = getattr(self, "_live_play_url", None)
            return (play(sid, fmt, "multiview") if play is not None
                    else self.client.live_url(sid, fmt))
        # A media server opens the channel to give its address, a portal
        # asks create_link: off the window's thread first, as the player's.
        later = getattr(self, "_links_later", None)
        came = partial(self._mv_address_came, it, cell, getattr(self, "_multiview_win", None))
        if later is not None and later(address, came, key=f"multiview-{sid}-{cell}"):
            return
        try:
            url = address()
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
            playlist=self._active_playlist_name(), source=self._mv_source())

    def _mv_address_came(self, it, cell: int | None, win) -> None:
        """The address a cell waited for is in: added - unless the grid it
        was asked for in has been closed meanwhile."""
        if win is not None and getattr(self, "_multiview_win", None) is not win:
            return
        self._add_channel_to_multiview(it, cell)

    def _multiview_available(self) -> bool:
        """Multiview is made of OpenGL video surfaces, and on a display with
        no OpenGL, building one aborts the process. Say so instead of opening
        the grid. (Without libmpv the cells just stay blank, as before.)"""
        if players.opengl_available():
            return True
        try:
            self._show_toast(tr("embedded_gl_failed"), 5000)
        except Exception:
            pass
        return False

    def _multiview_cell_count(self) -> int:
        """How many cells the grid has, or will have when it opens - so an
        "Add to multiview" menu offers each of them, not a fixed four."""
        mvw = getattr(self, "_multiview_win", None)
        if mvw is not None:
            return len(mvw.cells)
        try:
            count = int(self.settings.value("mv_cells", 4))
        except (TypeError, ValueError):
            count = 4
        return count if count in (2, 4, 6, 9) else 4

    def _mv_source(self, docked: bool = False) -> str:
        """The source (id) a cell's stream is from: the active one's, or -
        *docked* - the one the docked player plays from."""
        find = getattr(self, "_conn_player_source" if docked else "_conn_source", None)
        return find() if find is not None else ""

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
        """The menu's Multiview entry: opens multiview, and chosen again closes it.
        It only raised a grid with streams running, and where the window
        manager refuses to raise a window on request (GNOME on Wayland) the
        grid stayed behind the app - the button seemed to do nothing. A grid
        that is playing asks first, so one stray click cannot drop every
        connection; a minimised one is brought back."""
        if not self._multiview_available():
            return
        w = self._multiview_win
        if w is None or not w.isVisible():
            self._ensure_multiview_window()
            return
        if w.isMinimized():
            w.showNormal()
            self._ensure_multiview_window()
            return
        if any(c.url for c in w.cells) and not self._confirm_close_multiview():
            self._ensure_multiview_window()
            return
        self._close_multiview(hand_back=True)

    def _confirm_close_multiview(self) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setWindowTitle(tr("mv_toggle_title"))
        box.setText(tr("mv_toggle_body"))
        close = box.addButton(tr("mv_close"),
                              QMessageBox.ButtonRole.AcceptRole)
        box.addButton(tr("mv_keep"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(close)
        box.exec()
        return box.clickedButton() is close

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
        # The Stop button: it also ends the recording, the scrobbles and the
        # wake lock (player.stop() alone left the screen kept awake).
        menu.addAction(tr("btn_stop"), p.stop_btn.click)
        # In fullscreen the same toggle leaves it, so label it accordingly.
        menu.addAction(
            tr("tooltip_exit_fullscreen") if getattr(p, "_fs_ui", False)
            else tr("tooltip_fullscreen"),
            lambda: p.double_clicked.emit())
        menu.addSeparator()
        p.populate_options_menu(menu)
        menu.addSeparator()
        # Casting and Watch together come once, with the options above.
        menu.addAction(tr("mv_add"), self._send_docked_to_multiview)
        popup_menu_over_video(menu, global_pos)

    def _send_docked_to_multiview(self) -> None:
        p = getattr(self, "player", None)
        if p is None or not getattr(p, "current_url", None):
            return
        url = p.current_url
        it = getattr(self, "_playing_item", None)
        title = ((it or {}).get("name") or (it or {}).get("title")
                 or getattr(self, "_base_title", "") or "")
        # Hand the playhead over with the stream: a film or episode moved to a
        # cell continues where you were watching instead of starting again.
        # Read BEFORE add_to_multiview, which may stop the docked player to
        # free the provider connection. Only finite content has a meaningful
        # position - live has no duration and belongs at the live edge.
        start = 0.0
        try:
            if p.playback_duration() > 1:
                start = p.playback_position()
        except Exception:
            start = 0.0
        self.add_to_multiview(url, title, item=it,
                              client=getattr(self, "client", None),
                              guide=getattr(self, "xmltv", None),
                              playlist=self._active_playlist_name(),
                              start=start, source=self._mv_source(docked=True))

    def add_to_multiview(self, url: str, title: str,
                         cell: int | None = None, item: dict | None = None,
                         client=None, guide=None, playlist: str = "",
                         start: float = 0.0, source: str = "") -> None:
        if not url or not self._multiview_available():
            return
        # Nerd's VPN guard, said as the docked player says it - before the
        # docked player lets its stream go for a cell that would stay black.
        guard = getattr(self, "_vpn_guard_blocks", None)
        if guard is not None and guard(url):
            return
        # One more connection to the account: said first when the account
        # does not allow it (core/connections.py), and only then.
        ok = getattr(self, "_conn_ok_multiview", None)
        if ok is not None and not ok(cell, url, source):
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
            playlist=playlist, start=start, source=source)
        # Watching only multiview is watching video too: without this the
        # screensaver came on mid-match (the docked player had let go).
        self.wake.acquire(f"Multiview: {title}")

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
        # No wake release here: multiview takes the screen over right away
        # and keeps the lock (see add_to_multiview).
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

    def _close_multiview(self, hand_back: bool = False) -> None:
        """Multiview closed. *hand_back* (the person closed it - its window,
        or the Multiview entry again): the cell they were following goes on
        in the window (`_mv_hand_back_pick`). Never when multiview made way
        for something played in the window, nor while the window plays."""
        win = self._multiview_win
        if win is None:
            return
        if self.settings.value("mv_remember_geo", "true") == "true":
            self.settings.setValue("mv_geometry", win.saveGeometry())
        back = self._mv_hand_back_pick(win) if hand_back else None
        self._multiview_win = None
        for c in win.cells:
            c.shutdown()
        win.deleteLater()
        if not getattr(getattr(self, "player", None), "current_url", None):
            self.wake.release()
        if back is not None:
            # Every cell's connection let go first, so the window's stream
            # is never one more than the account allows.
            self._mv_back = back
            QTimer.singleShot(400, self._mv_hand_back_due)

    def _mv_hand_back_pick(self, win) -> dict | None:
        """What the cell being followed plays, and where: the one heard (the
        focused cell, else the first not muted), else the focused, else the
        first that plays. None while the window's own player plays."""
        if getattr(getattr(self, "player", None), "current_url", None):
            return None
        cells = [c for c in win.cells if c.url]
        if not cells:
            return None
        foc = win._focused if win._focused in cells else None
        heard = [c for c in cells if not c.is_muted()]
        cell = (foc if foc is not None and not foc.is_muted() else None) \
            or (heard[0] if heard else None) or foc or cells[0]
        finite = False
        m = cell.video.mpv
        try:
            finite = bool(m is not None and m.duration and float(m.duration) > 1) \
                and not cell._ts_capable
        except Exception:
            finite = False
        return {"url": getattr(cell, "_live_url", None) or cell.url,
                "title": cell.title or "", "item": dict(cell._item or {}),
                "client": cell._client, "finite": finite,
                "pos": cell.resume_pos() if finite else 0.0}

    def _mv_hand_back_due(self) -> None:
        back, self._mv_back = getattr(self, "_mv_back", None), None
        player = getattr(self, "player", None)
        # The app closing (its window hidden), or something started meanwhile.
        if back is None or not self.isVisible() or player is None \
                or getattr(player, "current_url", None):
            return
        it = back["item"]
        log.info("multiview: closed - the cell followed goes on in the window")
        if not back["finite"]:
            if back["client"] is self.client and it.get("stream_id") is not None:
                self.play_live_channel(it)       # a fresh address, as a zap
            else:
                self._start_playback(back["url"], back["title"] or it.get("name") or "",
                                     it.get("stream_icon"), self._item_key(it) if it else back["url"],
                                     "live", item=it or None)
            return
        kind = self._play_kind_for(it) if (it.get("_kind") or it.get("_ekind")) else "movie"
        key = self._item_key(it) if it else back["url"]
        # Where the cell got to, without the resume question (as Continue).
        self._sp_start_once = (key, back["pos"]) if back["pos"] > 0 else None
        try:
            self._start_playback(back["url"], back["title"], it.get("stream_icon") or it.get("cover"),
                                 key, kind, item=it or None)
        finally:
            self._sp_start_once = None

    def _close_multiview_if_active(self) -> None:
        if self._multiview_win is not None:
            self._close_multiview()
