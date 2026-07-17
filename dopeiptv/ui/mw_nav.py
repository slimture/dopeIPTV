"""Navigation-chrome mixin for MainWindow.

Focus mode, the per-nav-item accent-colour picker, the painted nav icons (glyph
-> centred pixmap), the collapsible Library group, and the category solo toggle.
Pure UI moved out of main_window.py; every method operates on MainWindow widgets
(self.nav_btns, self.cat_list, ...) through the mixin, so behaviour is identical.
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import (
    QColor, QFont, QFontMetricsF, QIcon, QPainter, QPen, QPixmap,
)
from PyQt6.QtWidgets import QColorDialog, QMenu

from ..i18n import tr
from .theme import P


class _NavMixin:
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

    def _glyph_pixmap(self, glyph: str, s: int, color: str) -> QPixmap:
        """A transparent SxS pixmap with GLYPH painted in COLOR, centred by the
        glyph's actual ink bounds (not the font's ascent/descent box - emoji
        carry uneven top/bottom/side bearing that otherwise shoves them low and
        to the left). U+FE0E requests the glyph's monochrome text presentation
        so it takes the pen colour rather than a bright emoji."""
        dpr = self.devicePixelRatioF() or 1.0
        pm = QPixmap(round(s * dpr), round(s * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        pr = QPainter(pm)
        pr.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        g = glyph + "︎"
        px = s * 0.82
        f = QFont()
        f.setPixelSize(round(px))
        fm = QFontMetricsF(f)
        br = fm.tightBoundingRect(g)
        # Some emoji (🔖, 🕘, ...) carry ink wider/taller than the text box, so
        # at the nominal size their glyph overflows the SxS pixmap and clips at
        # its edges. Shrink the font until the ink fits inside the box (with a
        # hair of padding). Plain text glyphs (★, ✓) already fit, so this only
        # touches the ones that would clip.
        biggest = max(br.width(), br.height())
        limit = s * 0.94
        if biggest > limit:
            f.setPixelSize(max(1, round(px * limit / biggest)))
            fm = QFontMetricsF(f)
            br = fm.tightBoundingRect(g)
        pr.setFont(f)
        pr.setPen(QColor(color))
        x = (s - br.width()) / 2.0 - br.x()
        y = (s - br.height()) / 2.0 - br.y()
        pr.drawText(QPointF(x, y), g)
        pr.end()
        return pm

    def _nav_icon(self, glyph: str, s: int) -> QIcon:
        """An icon of size S painted from GLYPH in the theme's muted tone (the
        normal/Off state) and white (the checked/On state), so it always
        matches the nav label."""
        icon = QIcon()
        icon.addPixmap(self._glyph_pixmap(glyph, s, P["text2"]),
                       QIcon.Mode.Normal, QIcon.State.Off)
        icon.addPixmap(self._glyph_pixmap(glyph, s, "#FFFFFF"),
                       QIcon.Mode.Normal, QIcon.State.On)
        return icon

    def _apply_cat_search_icon(self) -> None:
        """(Re)paint the category-search 🔍 as a dead-centred icon in the theme's
        muted tone. A plain QToolButton text emoji sits shoved to the top-left of
        its hover square; an ink-centred icon fixes that and re-tints on theme
        change."""
        if not hasattr(self, "cat_search_btn"):
            return
        self.cat_search_btn.setIcon(
            QIcon(self._glyph_pixmap("🔍", 14, P["text2"])))
        self.cat_search_btn.setIconSize(QSize(14, 14))

    def _apply_nav_icons(self) -> None:
        """(Re)build every nav button's icon in the current theme tones. Called
        at construction and whenever the theme/accent changes. Browse icons are
        a couple of notches larger than Library ones - the same hierarchy as
        the label sizes (NavBtn[primary] in the theme)."""
        if not hasattr(self, "nav_btns"):
            return
        for key, b in self.nav_btns.items():
            s = 22 if key in ("live", "vod", "series") else 19
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
            if kind == "gear":
                # The bare glyph draws small (its ink fills well under the box);
                # scale it up so it reads at the same size as the two vector
                # icons beside it.
                pm = self._fill_glyph_pixmap("⚙", 18, P["text2"], 0.94)
            else:
                pm = self._action_pixmap(kind, 18, P["text2"])
            btn.setIcon(QIcon(pm))
            btn.setIconSize(QSize(18, 18))
        # The playlist stack icon follows the theme too (same square icon in
        # both sidebar states; _update_playlist_btn repaints it).
        if hasattr(self, "_playlist_btn"):
            self._update_playlist_btn()
        # Hide-the-list (focus mode) toggle: as a raw text glyph the ⤢ sat
        # shoved off-centre and clipped at the compact 28 px width - paint it
        # as a centred, ink-measured icon like the rest.
        if hasattr(self, "focus_btn"):
            self.focus_btn.setText("")
            self.focus_btn.setIcon(
                QIcon(self._glyph_pixmap("⤢", 16, P["text2"])))
            self.focus_btn.setIconSize(QSize(16, 16))

    def _fill_glyph_pixmap(self, glyph: str, s: int, color: str,
                           frac: float) -> QPixmap:
        """Like _glyph_pixmap but sizes the glyph so its ink fills FRAC of the
        SxS box (the plain helper only shrinks oversized glyphs, never grows a
        small one - which left the gear tiny next to the vector icons)."""
        dpr = self.devicePixelRatioF() or 1.0
        pm = QPixmap(round(s * dpr), round(s * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        pr = QPainter(pm)
        pr.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        g = glyph + "︎"
        f = QFont()
        f.setPixelSize(s)
        fm = QFontMetricsF(f)
        br = fm.tightBoundingRect(g)
        biggest = max(br.width(), br.height()) or 1.0
        f.setPixelSize(max(1, round(s * frac * s / biggest)))
        fm = QFontMetricsF(f)
        br = fm.tightBoundingRect(g)
        pr.setFont(f)
        pr.setPen(QColor(color))
        x = (s - br.width()) / 2.0 - br.x()
        y = (s - br.height()) / 2.0 - br.y()
        pr.drawText(QPointF(x, y), g)
        pr.end()
        return pm

    def _action_pixmap(self, kind: str, s: int, color: str) -> QPixmap:
        """Hand-drawn monochrome icon for the sidebar action buttons.
        'grid'  -> four rounded tiles (multiview);
        'stack' -> two stacked cards (switch playlist, on the rail);
        'guide' -> a framed programme grid: header row, channel column and a
                   few programme cells (EPG)."""
        dpr = self.devicePixelRatioF() or 1.0
        pm = QPixmap(round(s * dpr), round(s * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        pr = QPainter(pm)
        pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if kind == "stack":
            # Two offset cards = "several playlists, pick one". The back card
            # is dimmed so the front reads as the active list.
            pr.setPen(Qt.PenStyle.NoPen)
            back = QColor(color)
            back.setAlphaF(0.45)
            pr.setBrush(back)
            pr.drawRoundedRect(QRectF(s * 0.26, s * 0.08, s * 0.64, s * 0.56),
                               s * 0.10, s * 0.10)
            pr.setBrush(QColor(color))
            pr.drawRoundedRect(QRectF(s * 0.10, s * 0.32, s * 0.64, s * 0.56),
                               s * 0.10, s * 0.10)
        elif kind == "grid":
            pr.setPen(Qt.PenStyle.NoPen)
            pr.setBrush(QColor(color))
            m = s * 0.09
            gap = s * 0.13
            cell = (s - 2 * m - gap) / 2.0
            rad = cell * 0.26
            for i in (0, 1):
                for j in (0, 1):
                    pr.drawRoundedRect(
                        QRectF(m + j * (cell + gap), m + i * (cell + gap),
                               cell, cell), rad, rad)
        else:   # guide
            pen = QPen(QColor(color))
            pen.setWidthF(max(1.2, s * 0.085))
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pr.setPen(pen)
            pr.setBrush(Qt.BrushStyle.NoBrush)
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
