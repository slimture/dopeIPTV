"""Navigation-chrome mixin for MainWindow.

Focus mode, the per-nav-item accent-colour picker, the painted nav icons (glyph
-> centred pixmap), the collapsible Library group, and the category solo toggle.
Pure UI moved out of main_window.py; every method operates on MainWindow widgets
(self.nav_btns, self.cat_list, ...) through the mixin, so behaviour is identical.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import (
    QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
)
from PyQt6.QtWidgets import QColorDialog, QMenu

from ..i18n import tr
from .theme import P


class _NavMixin:
    """MainWindow mixin: the top navigation bar and switching between TV/Movies/Series/... modes."""
    def _set_focus_mode(self, on: bool) -> None:
        """Focus mode hides the whole content list so the player pane gets the
        room. The reopen arrow strip on the detail pane's left edge is the way
        back, so the list can never be lost."""
        self._focus_mode = on
        self._mid.setVisible(not on)
        self._reopen_btn.setVisible(on and not self._player_fs)
        if on:
            self._position_reopen()
            self._reopen_btn.raise_()

    def _position_reopen(self) -> None:
        if not hasattr(self, "_reopen_btn"):
            return
        self._reopen_btn.setGeometry(0, 0, 20, self._det.height())

    # -- custom nav colours --------------------------------------------------

    def _apply_nav_color(self, key: str) -> None:
        """Tint one nav entry with the user's chosen text and/or background
        colours. Empty settings -> back to the theme default."""
        b = self.nav_btns.get(key)
        if b is None:
            return
        fg = self.settings.value(f"nav_color_{key}", "") or ""
        bg = self.settings.value(f"nav_bg_{key}", "") or ""
        if not fg and not bg:
            b.setStyleSheet("")
            return
        idle = []
        if fg:
            idle.append(f"color:{fg};")
        if bg:
            idle.append(f"background:{bg};")
        # When selected, a custom background wins as the fill; otherwise keep
        # the accent selection with the custom text colour.
        checked = (f"background:{bg};color:{fg or '#ffffff'};" if bg
                   else f"color:{fg};")
        b.setStyleSheet(
            "QPushButton#NavBtn{%s}QPushButton#NavBtn:checked{%s}"
            % ("".join(idle), checked))

    def _nav_color_menu(self, key: str, global_pos) -> None:
        m = QMenu(self)
        # TV: quick jump into the programme guide from the nav entry.
        if key == "live":
            m.addAction(tr("btn_epg_guide"), self._open_epg_guide)
        # Provider-backed lists can be reloaded straight from their entry.
        if key in ("live", "vod", "series"):
            m.addAction(tr("menu_refresh_playlist"), self.refresh_playlist)
            m.addSeparator()
        m.addAction(tr("nav_set_text_color"),
                    lambda: self._pick_nav_color(key, "text"))
        m.addAction(tr("nav_set_bg_color"),
                    lambda: self._pick_nav_color(key, "bg"))
        if (self.settings.value(f"nav_color_{key}", "")
                or self.settings.value(f"nav_bg_{key}", "")):
            m.addAction(tr("nav_reset_color"),
                        lambda: self._reset_nav_color(key))
        m.exec(global_pos)

    def _pick_nav_color(self, key: str, which: str) -> None:
        skey = f"nav_bg_{key}" if which == "bg" else f"nav_color_{key}"
        cur = self.settings.value(skey, "") or ""
        # Force Qt's own dialog: the native colour picker can open behind the
        # window or hang on some Linux setups (same as the file dialog).
        col = QColorDialog.getColor(
            QColor(cur) if cur else QColor("#3b5ba5"), self,
            tr("nav_set_bg_color") if which == "bg"
            else tr("nav_set_text_color"),
            QColorDialog.ColorDialogOption.DontUseNativeDialog)
        if col.isValid():
            self.settings.setValue(skey, col.name())
            self._apply_nav_color(key)

    def _reset_nav_color(self, key: str) -> None:
        self.settings.remove(f"nav_color_{key}")
        self.settings.remove(f"nav_bg_{key}")
        self._apply_nav_color(key)

    def _on_cat_solo_toggle(self, checked: bool) -> None:
        """Collapse the category list to just the active category (hide all
        the other rows), so the provider's category names can be tucked away
        for clean screenshots while the list still shows where you are."""
        self._cat_solo = checked
        self.cat_solo_btn.setArrowType(
            Qt.ArrowType.RightArrow if checked else Qt.ArrowType.DownArrow)
        self._apply_cat_solo()

    def _nav_icon(self, kind: str, s: int) -> QIcon:
        """A vector icon of size S in the theme's muted tone (the normal/Off
        state) and white (the checked/On state), so it always matches the nav
        label - and renders identically on every OS."""
        icon = QIcon()
        icon.addPixmap(self._action_pixmap(kind, s, P["text2"]),
                       QIcon.Mode.Normal, QIcon.State.Off)
        icon.addPixmap(self._action_pixmap(kind, s, "#FFFFFF"),
                       QIcon.Mode.Normal, QIcon.State.On)
        return icon

    def _apply_cat_search_icon(self) -> None:
        """(Re)paint the category-search magnifier as a centred vector icon in
        the theme's muted tone (the old 🔍 emoji looked different per OS)."""
        if not hasattr(self, "cat_search_btn"):
            return
        self.cat_search_btn.setIcon(
            QIcon(self._action_pixmap("search", 14, P["text2"])))
        self.cat_search_btn.setIconSize(QSize(14, 14))

    def _apply_nav_icons(self) -> None:
        """(Re)build every nav button's icon in the current theme tones. Called
        at construction and whenever the theme/accent changes. Browse icons are
        a couple of notches larger than Library ones - the same hierarchy as
        the label sizes (NavBtn[primary] in the theme)."""
        if not hasattr(self, "nav_btns"):
            return
        for key, b in self.nav_btns.items():
            s = 22 if key in ("home", "live", "vod", "series") else 19
            b.setIcon(self._nav_icon(self._rail_glyphs[key], s))
            b.setIconSize(QSize(s, s))
        self._apply_cat_search_icon()   # re-tint 🔍 to the new theme's muted tone
        self._apply_action_icons()      # Guide / Settings / Multiview glyphs

    def _apply_action_icons(self) -> None:
        """Paint the three sidebar action buttons in the theme's muted tone, so
        they read as a compact icon row (expanded) or stack (rail) rather than
        three wide text pills. Guide and Multiview are crisp vector icons (a
        programme-grid and a 2x2 tile grid) that render identically on every
        platform; Settings keeps the universally-read gear glyph. Re-tinted on
        theme change."""
        specs = (("_guide_btn", "guide"), ("_settings_btn", "gear"),
                 ("_multiview_btn", "grid"))
        for name, kind in specs:
            btn = getattr(self, name, None)
            if btn is None:
                continue
            btn.setText("")   # icon-only; the name lives in the tooltip
            btn.setIcon(QIcon(self._action_pixmap(kind, 18, P["text2"])))
            btn.setIconSize(QSize(18, 18))
        # The playlist stack icon follows the theme too (same square icon in
        # both sidebar states; _update_playlist_btn repaints it).
        if hasattr(self, "_playlist_btn"):
            self._update_playlist_btn()
        # Middle-column controls: focus (hide list), list toggle, grid view
        # and the compact size/sort pickers - vector icons, re-tinted here.
        if hasattr(self, "focus_btn"):
            self.focus_btn.setText("")
            self.focus_btn.setIcon(
                QIcon(self._action_pixmap("focus", 16, P["text2"])))
            self.focus_btn.setIconSize(QSize(16, 16))
        if hasattr(self, "side_btn"):
            self.side_btn.setText("")
            self.side_btn.setIcon(self._nav_icon("bars", 15))
            self.side_btn.setIconSize(QSize(15, 15))
        if hasattr(self, "grid_btn"):
            self.grid_btn.setIcon(self._nav_icon("gridview", 14))
            self.grid_btn.setIconSize(QSize(14, 14))
        if hasattr(self, "_size_menu_btn"):
            self._size_menu_btn.setText("")
            self._size_menu_btn.setIcon(
                QIcon(self._action_pixmap("sizepick", 15, P["text2"])))
            self._size_menu_btn.setIconSize(QSize(15, 15))
            self._sort_menu_btn.setText("")
            self._sort_menu_btn.setIcon(
                QIcon(self._action_pixmap("sort", 15, P["text2"])))
            self._sort_menu_btn.setIconSize(QSize(15, 15))

    def _action_pixmap(self, kind: str, s: int, color: str) -> QPixmap:
        """Hand-drawn monochrome vector icon, identical on every OS - the app
        used to lean on emoji/symbol glyphs here, which each platform's font
        renders differently (and some render as clipped or colour marks).
        Kinds cover the sidebar nav (tv/movie/series/star/bookmark/check/rec/
        clock), the action row (guide/gear/grid/stack) and the middle-column
        controls (search/focus/bars/gridview/sizepick/sort)."""
        # Cached per (kind, size, color, dpr): layout passes - notably
        # splitter drags, which re-run _update_playlist_btn and friends -
        # request the same few icons over and over, and re-rendering each
        # with QPainter every time showed up as visible drag lag.
        dpr = self.devicePixelRatioF() or 1.0
        cache = getattr(self, "_icon_pm_cache", None)
        if cache is None:
            cache = self._icon_pm_cache = {}
        cache_key = (kind, s, color, round(dpr * 100))
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        pm = QPixmap(round(s * dpr), round(s * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        pr = QPainter(pm)
        pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        col = QColor(color)
        pen = QPen(col)
        pen.setWidthF(max(1.2, s * 0.085))
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)

        def stroke() -> None:
            pr.setPen(pen)
            pr.setBrush(Qt.BrushStyle.NoBrush)

        def fill() -> None:
            pr.setPen(Qt.PenStyle.NoPen)
            pr.setBrush(col)

        if kind == "home":
            # A house: roof triangle over a body with a door notch.
            stroke()
            pr.drawPolyline(QPointF(s * 0.14, s * 0.52), QPointF(s * 0.50, s * 0.16),
                            QPointF(s * 0.86, s * 0.52))
            body = QPainterPath()
            body.moveTo(s * 0.24, s * 0.50)
            body.lineTo(s * 0.24, s * 0.86)
            body.lineTo(s * 0.42, s * 0.86)
            body.lineTo(s * 0.42, s * 0.64)
            body.lineTo(s * 0.58, s * 0.64)
            body.lineTo(s * 0.58, s * 0.86)
            body.lineTo(s * 0.76, s * 0.86)
            body.lineTo(s * 0.76, s * 0.50)
            pr.drawPath(body)
        elif kind == "stack":
            # Two offset cards = "several playlists, pick one". The back card
            # is dimmed so the front reads as the active list.
            pr.setPen(Qt.PenStyle.NoPen)
            back = QColor(col)
            back.setAlphaF(0.45)
            pr.setBrush(back)
            pr.drawRoundedRect(QRectF(s * 0.26, s * 0.08, s * 0.64, s * 0.56),
                               s * 0.10, s * 0.10)
            pr.setBrush(col)
            pr.drawRoundedRect(QRectF(s * 0.10, s * 0.32, s * 0.64, s * 0.56),
                               s * 0.10, s * 0.10)
        elif kind == "grid":
            fill()
            m = s * 0.09
            gap = s * 0.13
            cell = (s - 2 * m - gap) / 2.0
            rad = cell * 0.26
            for i in (0, 1):
                for j in (0, 1):
                    pr.drawRoundedRect(
                        QRectF(m + j * (cell + gap), m + i * (cell + gap),
                               cell, cell), rad, rad)
        elif kind == "tv":
            stroke()
            pr.drawRoundedRect(QRectF(s * 0.12, s * 0.30, s * 0.76, s * 0.52),
                               s * 0.08, s * 0.08)
            pr.drawLine(QPointF(s * 0.36, s * 0.28), QPointF(s * 0.22, s * 0.10))
            pr.drawLine(QPointF(s * 0.64, s * 0.28), QPointF(s * 0.78, s * 0.10))
            pr.drawLine(QPointF(s * 0.34, s * 0.92), QPointF(s * 0.66, s * 0.92))
        elif kind == "movie":
            stroke()
            pr.drawRoundedRect(QRectF(s * 0.12, s * 0.22, s * 0.76, s * 0.58),
                               s * 0.08, s * 0.08)
            pr.drawLine(QPointF(s * 0.12, s * 0.42), QPointF(s * 0.88, s * 0.42))
            for fx in (0.22, 0.44, 0.66):
                pr.drawLine(QPointF(s * fx, s * 0.24),
                            QPointF(s * (fx + 0.10), s * 0.42))
        elif kind == "series":
            back = QColor(col)
            back.setAlphaF(0.45)
            pr.setPen(Qt.PenStyle.NoPen)
            pr.setBrush(back)
            pr.drawRoundedRect(QRectF(s * 0.20, s * 0.10, s * 0.68, s * 0.50),
                               s * 0.08, s * 0.08)
            fill()
            pr.drawRoundedRect(QRectF(s * 0.10, s * 0.32, s * 0.68, s * 0.52),
                               s * 0.08, s * 0.08)
            pr.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_Clear)
            pr.drawPolygon(QPolygonF([
                QPointF(s * 0.36, s * 0.44), QPointF(s * 0.36, s * 0.72),
                QPointF(s * 0.60, s * 0.58)]))
            pr.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_SourceOver)
        elif kind == "star":
            fill()
            cx, cy = s * 0.5, s * 0.54
            ro, ri = s * 0.44, s * 0.18
            pts = []
            for i in range(10):
                r = ro if i % 2 == 0 else ri
                a = -math.pi / 2 + i * math.pi / 5
                pts.append(QPointF(cx + r * math.cos(a),
                                   cy + r * math.sin(a)))
            pr.drawPolygon(QPolygonF(pts))
        elif kind == "bookmark":
            fill()
            pr.drawPolygon(QPolygonF([
                QPointF(s * 0.28, s * 0.10), QPointF(s * 0.72, s * 0.10),
                QPointF(s * 0.72, s * 0.90), QPointF(s * 0.50, s * 0.72),
                QPointF(s * 0.28, s * 0.90)]))
        elif kind == "check":
            pen.setWidthF(max(1.6, s * 0.12))
            stroke()
            pr.drawPolyline(QPolygonF([
                QPointF(s * 0.18, s * 0.55), QPointF(s * 0.42, s * 0.78),
                QPointF(s * 0.84, s * 0.26)]))
        elif kind == "rec":
            stroke()
            pr.drawEllipse(QRectF(s * 0.14, s * 0.14, s * 0.72, s * 0.72))
            fill()
            pr.drawEllipse(QRectF(s * 0.34, s * 0.34, s * 0.32, s * 0.32))
        elif kind == "clock":
            stroke()
            pr.drawEllipse(QRectF(s * 0.12, s * 0.12, s * 0.76, s * 0.76))
            pr.drawLine(QPointF(s * 0.5, s * 0.5), QPointF(s * 0.5, s * 0.27))
            pr.drawLine(QPointF(s * 0.5, s * 0.5), QPointF(s * 0.67, s * 0.60))
        elif kind == "gear":
            fill()
            cx = cy = s * 0.5
            for i in range(8):
                pr.save()
                pr.translate(cx, cy)
                pr.rotate(i * 45.0)
                pr.drawRect(QRectF(-s * 0.065, -s * 0.46, s * 0.13, s * 0.18))
                pr.restore()
            pr.drawEllipse(QRectF(cx - s * 0.30, cy - s * 0.30,
                                  s * 0.60, s * 0.60))
            pr.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_Clear)
            pr.drawEllipse(QRectF(cx - s * 0.13, cy - s * 0.13,
                                  s * 0.26, s * 0.26))
            pr.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_SourceOver)
        elif kind == "search":
            stroke()
            pr.drawEllipse(QRectF(s * 0.14, s * 0.14, s * 0.50, s * 0.50))
            pr.drawLine(QPointF(s * 0.61, s * 0.61), QPointF(s * 0.86, s * 0.86))
        elif kind == "focus":
            stroke()
            pr.drawLine(QPointF(s * 0.34, s * 0.66), QPointF(s * 0.66, s * 0.34))
            fill()
            pr.drawPolygon(QPolygonF([
                QPointF(s * 0.56, s * 0.20), QPointF(s * 0.82, s * 0.18),
                QPointF(s * 0.80, s * 0.44)]))
            pr.drawPolygon(QPolygonF([
                QPointF(s * 0.44, s * 0.80), QPointF(s * 0.18, s * 0.82),
                QPointF(s * 0.20, s * 0.56)]))
        elif kind == "bars":
            fill()
            for fy in (0.20, 0.45, 0.70):
                pr.drawRoundedRect(QRectF(s * 0.14, s * fy, s * 0.72, s * 0.11),
                                   s * 0.05, s * 0.05)
        elif kind == "gridview":
            fill()
            cell = s * 0.20
            gap = s * 0.08
            m0 = (s - 3 * cell - 2 * gap) / 2.0
            for i in range(3):
                for j in range(3):
                    pr.drawRect(QRectF(m0 + j * (cell + gap),
                                       m0 + i * (cell + gap), cell, cell))
        elif kind == "sizepick":
            stroke()
            pr.drawRoundedRect(QRectF(s * 0.14, s * 0.14, s * 0.72, s * 0.72),
                               s * 0.08, s * 0.08)
            pr.drawLine(QPointF(s * 0.5, s * 0.32), QPointF(s * 0.5, s * 0.68))
            pr.drawLine(QPointF(s * 0.32, s * 0.5), QPointF(s * 0.68, s * 0.5))
        elif kind == "sort":
            stroke()
            pr.drawLine(QPointF(s * 0.34, s * 0.24), QPointF(s * 0.34, s * 0.80))
            pr.drawLine(QPointF(s * 0.66, s * 0.20), QPointF(s * 0.66, s * 0.76))
            fill()
            pr.drawPolygon(QPolygonF([
                QPointF(s * 0.22, s * 0.34), QPointF(s * 0.46, s * 0.34),
                QPointF(s * 0.34, s * 0.14)]))
            pr.drawPolygon(QPolygonF([
                QPointF(s * 0.54, s * 0.66), QPointF(s * 0.78, s * 0.66),
                QPointF(s * 0.66, s * 0.86)]))
        else:   # guide
            stroke()
            m = s * 0.13
            inner = s - 2 * m
            pr.drawRoundedRect(QRectF(m, m, inner, inner), s * 0.12, s * 0.12)
            hy = m + inner * 0.33          # under the header row
            pr.drawLine(QPointF(m, hy), QPointF(s - m, hy))
            vx = m + inner * 0.36          # channel column | programmes
            pr.drawLine(QPointF(vx, hy), QPointF(vx, s - m))
            for k in (1, 2):              # programme-cell rows
                ry = hy + (s - m - hy) * k / 3.0
                pr.drawLine(QPointF(vx, ry), QPointF(s - m, ry))
        pr.end()
        # A handful of kinds x sizes x theme colors; the cap only matters if
        # someone cycles themes all day.
        if len(cache) > 128:
            cache.clear()
        cache[cache_key] = pm
        return pm

    def _on_library_toggle(self, collapsed: bool) -> None:
        """Fold the Library group (Favorites..History) away behind its
        disclosure arrow, the same gesture as the Categories header. On the
        collapsed icon rail the group is always shown (its header/arrow are
        hidden there), so the collapse only applies to the expanded sidebar."""
        self._lib_toggle.setArrowType(
            Qt.ArrowType.RightArrow if collapsed else Qt.ArrowType.DownArrow)
        if hasattr(self, "_library_box"):
            rail = getattr(self, "_sidebar_collapsed", False)
            self._library_box.setVisible(rail or not collapsed)
        if self.settings is not None:
            self.settings.setValue("library_collapsed", collapsed)

    def _apply_cat_solo(self) -> None:
        if not hasattr(self, "cat_list"):
            return
        solo = getattr(self, "_cat_solo", False)
        current = self.cat_list.currentRow()
        for i in range(self.cat_list.count()):
            it = self.cat_list.item(i)
            if it is not None:
                it.setHidden(solo and i != current)
