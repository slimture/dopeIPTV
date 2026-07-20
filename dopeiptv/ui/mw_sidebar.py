"""Sidebar chrome mixin for MainWindow.

The left panel's expand/collapse (full <-> icon rail), the auto-collapse on a
narrow window, the compact middle-column controls, and the little combo-menu
helper. Pure UI moved out of main_window.py; every method operates on
MainWindow widgets (self._side, self.nav_btns, self._mid, ...) through the
mixin, so behaviour is identical.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QBoxLayout, QMenu

from ..i18n import tr


class _SidebarMixin:
    """MainWindow mixin: the category sidebar and the library section."""
    def _on_side_toggle(self, checked: bool) -> None:
        """Collapse the left column to a narrow icon rail (or expand it back).
        The rail keeps the TV/Movies/Series nav as icons, so you reclaim the
        width without losing navigation. Remembered across fullscreen."""
        self._sidebar_collapsed = not checked
        if hasattr(self, "_side") and not self._player_fs:
            self._apply_sidebar_collapsed()

    RAIL_W = 60

    def _apply_sidebar_chrome(self, collapsed: bool) -> None:
        """Visual-only rail state: nav glyphs vs labels, and which of the
        expanded-only bits (logo, category area) show. Safe to call mid-drag -
        it touches no width constraints and never calls setSizes()."""
        if not hasattr(self, "nav_btns"):
            return
        for key, b in self.nav_btns.items():
            # Rail: icon only (centred); expanded: icon + label. The icon
            # itself is always set (see _apply_nav_icons).
            b.setText("" if collapsed else self._nav_texts[key])
            self._set_rail(b, collapsed)
        # The logo (= the "now playing" jump) stays on the rail too, just
        # scaled down to fit; the category area and full-text buttons hide.
        self._sidebar_logo.setVisible(True)
        self._sidebar_logo.set_compact(collapsed)
        self._cat_section_label.setVisible(not collapsed)
        self.cat_solo_btn.setVisible(not collapsed)
        self.cat_list.setVisible(not collapsed)
        # Keep the Library header widget itself visible in both modes so its
        # top gap always separates Browse from Library (you can see they're two
        # groups). On the rail only its label + arrow hide - the gap stays. The
        # group's icons are always shown on the rail (honour the collapse only
        # when expanded).
        if hasattr(self, "_lib_header"):
            # Rail: the header keeps only its 8px top margin (label and arrow
            # hide), leaving a clear but modest gap between the Browse and
            # Library groups, while the icon-to-icon spacing inside each group
            # stays identical - the rail filler prevents the old row-spreading,
            # so this gap is fixed and predictable.
            self._lib_header.setVisible(True)
            self._lib_section_label.setVisible(not collapsed)
            self._lib_toggle.setVisible(not collapsed)
            self._library_box.setVisible(
                collapsed or not self._lib_toggle.isChecked())
        # The rail's stand-in stretch item (see construction note).
        if hasattr(self, "_rail_filler"):
            self._rail_filler.setVisible(collapsed)
        # Re-evaluate the 'Sync now' button: it's expanded-only (hidden on the
        # rail), so collapsing/expanding must refresh it.
        if hasattr(self, "_sync_now_btn"):
            self._update_sync_btn()
        # The category-search toggle (and its box) belong to the expanded
        # sidebar only - hide them on the collapsed icon rail, restoring the
        # mode's own visibility (set in _load_categories) when expanded.
        if hasattr(self, "cat_search_btn"):
            self.cat_search_btn.setVisible(
                not collapsed and getattr(self, "_cat_search_supported", False))
            if collapsed:
                self.cat_search.hide()
        # Guide / Settings / Multiview are glyph-icon buttons in both modes
        # (see _apply_action_icons); only the rail pill styling toggles.
        self._set_rail(self._guide_btn, collapsed)
        self._set_rail(self._settings_btn, collapsed)
        if hasattr(self, "_multiview_btn"):
            self._set_rail(self._multiview_btn, collapsed)
        if hasattr(self, "_playlist_btn"):
            # The switcher is the same square stack-icon in both states (the
            # active playlist's name lives in its tooltip and the window
            # title); only the sizing flips - fitted square when expanded,
            # filling the rail when collapsed.
            from PyQt6.QtWidgets import QSizePolicy
            self._playlist_btn.setSizePolicy(
                QSizePolicy.Policy.Ignored if collapsed
                else QSizePolicy.Policy.Maximum,
                QSizePolicy.Policy.Fixed)
            self._update_playlist_btn()
            self._set_rail(self._playlist_btn, collapsed)
        # Side by side when expanded, stacked on the narrow rail.
        if hasattr(self, "_actions_box"):
            self._actions_box.setDirection(
                QBoxLayout.Direction.TopToBottom if collapsed
                else QBoxLayout.Direction.LeftToRight)

    def _apply_sidebar_collapsed(self) -> None:
        collapsed = getattr(self, "_sidebar_collapsed", False)
        if not hasattr(self, "nav_btns"):
            return
        # Remember the expanded width so we can hand it back on expand.
        if not collapsed and self._side.width() > self.RAIL_W:
            self._sidebar_expanded_w = max(self._side.width(), 180)
        elif collapsed and self._side.maximumWidth() > self.RAIL_W:
            self._sidebar_expanded_w = max(self._side.width(), 180)
        self._apply_sidebar_chrome(collapsed)
        # While the user is actively dragging the divider (see eventFilter) we
        # leave the pane UNPINNED and skip all geometry, so a single continuous
        # drag can collapse, re-expand and collapse again without a pinned width
        # freezing it. The final width is pinned on mouse release.
        if getattr(self, "_side_dragging", False):
            return
        # Pin the pane and reflow the sizes so the panel snaps to its final
        # width immediately. Crucially this runs on the drag-release too: after
        # a collapse the rail must snap to RAIL_W right away, otherwise the pane
        # keeps its wider footprint and leaves an empty gap beside the icons
        # until the next click. When expanding, the target is the current
        # (dragged) width, so setSizes is a no-op reflow that keeps it put.
        if collapsed:
            # Pin to exactly the rail width: a hard constraint the splitter
            # honours immediately (so a drag-in snaps cleanly) and which keeps
            # the rail from stretching when the middle column is hidden in
            # focus mode.
            self._side.setMinimumWidth(self.RAIL_W)
            self._side.setMaximumWidth(self.RAIL_W)
            target = self.RAIL_W
        else:
            self._side.setMinimumWidth(0)
            self._side.setMaximumWidth(16777215)
            target = getattr(self, "_sidebar_expanded_w", 220)
        sizes = self._root.sizes()
        if len(sizes) >= 2:
            sizes[1] = max(240, sizes[1] + (sizes[0] - target))
            sizes[0] = target
            self._root.setSizes(sizes)

    @staticmethod
    def _set_rail(btn, on: bool) -> None:
        btn.setProperty("rail", on)
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    def _set_sidebar_collapsed(self, collapsed: bool) -> None:
        """Flip collapsed state and keep the ☰ button in sync, without letting
        its toggle re-run the geometry (we drive that from here / on release)."""
        if getattr(self, "_sidebar_collapsed", False) == collapsed:
            return
        self._sidebar_collapsed = collapsed
        self.side_btn.blockSignals(True)
        self.side_btn.setChecked(not collapsed)   # checked == expanded
        self.side_btn.blockSignals(False)
        self._apply_sidebar_collapsed()

    def _maybe_collapse_on_drag(self, *_a) -> None:
        """Dragging the side divider flips the sidebar between full and the icon
        rail. While the handle is held (see eventFilter) the pane is left
        unpinned, so one continuous drag can collapse, re-expand and collapse
        again - the final width is pinned on release. Two thresholds give a
        hysteresis band so it doesn't flicker right at the boundary."""
        if not hasattr(self, "_side") or not hasattr(self, "side_btn"):
            return
        if getattr(self, "_player_fs", False):
            return
        w = self._side.width()
        if not getattr(self, "_sidebar_collapsed", False):
            # Collapse once pulled to (near) the sidebar's own minimum - keyed
            # off the real minimumSizeHint so it works at any font/DPI.
            floor = self._side.minimumSizeHint().width()
            if w < 150 or w <= floor + 12:
                self._collapse_w = w
                self._auto_collapsed = False   # manual action takes over
                self._set_sidebar_collapsed(True)
        else:
            # Re-expand by dragging back out. Only while the handle is held (a
            # rail pinned at rest can't move) and past a hysteresis gap above
            # where it collapsed, so it won't flip-flop at the threshold.
            if (getattr(self, "_side_dragging", False)
                    and w >= getattr(self, "_collapse_w", 150) + 40):
                self._auto_collapsed = False   # manual action takes over
                self._set_sidebar_collapsed(False)

    def _update_mid_compact(self, *_a) -> None:
        if not hasattr(self, "_mid"):
            return
        # A hidden pane (Home showing) reports a stale width - deciding from
        # it left the strip in the wrong form; _leave_home recomputes later.
        if not self._mid.isVisible():
            return
        w = self._mid.width()
        # Hysteresis: enter compact under 400, leave only above 430, so the
        # strip doesn't flip between icons and captions right at the line.
        if getattr(self, "_mid_compact", None) is True:
            self._apply_mid_compact(w < 430)
        else:
            self._apply_mid_compact(w < 400)

    def _apply_mid_compact(self, compact: bool) -> None:
        """Responsive middle-pane control strip: when the list column is
        narrow, drop the Size/Sort captions, cap the closed combos' width and
        shrink the grid toggle to a glyph, so everything stays visible and
        clickable instead of overflowing the pane. The dropdown POPUP keeps
        its full width, so the choices themselves remain readable."""
        if getattr(self, "_mid_compact", None) == compact:
            return
        self._mid_compact = compact
        self._size_label.setVisible(not compact)
        self._sort_label.setVisible(not compact)
        # Every text control swaps to a glyph form instead of clipping: the
        # combos are REPLACED by the ⊞/⇅ menu buttons (their popup lists the
        # same choices, fully readable at any width), and the grid toggle
        # becomes ▦. 28+28 (toggles) + 30+30 (menus) + 34 (grid) + spacings
        # fits the pane's 240px minimum with room to spare.
        self.size_box.setVisible(not compact)
        self.sort_box.setVisible(not compact)
        self._size_menu_btn.setVisible(compact)
        self._sort_menu_btn.setVisible(compact)
        # One uniform tile size for every compact control (the mixed 28/30/34
        # widths and free heights made the strip look like differently sized
        # boxes).
        for b in (self.side_btn, self.focus_btn, self._size_menu_btn,
                  self._sort_menu_btn, self.grid_btn):
            b.setFixedHeight(28)
        self.side_btn.setFixedWidth(30 if compact else 34)
        self.focus_btn.setFixedWidth(30 if compact else 34)
        self._size_menu_btn.setFixedWidth(30)
        self._sort_menu_btn.setFixedWidth(30)
        self.grid_btn.setText("" if compact else tr("btn_grid"))
        self.grid_btn.setMinimumWidth(30 if compact else 0)
        self.grid_btn.setMaximumWidth(30 if compact else 16777215)
        self.grid_btn.setToolTip(tr("btn_grid"))

    def _fill_combo_menu(self, menu: QMenu, box) -> None:
        """The compact ⊞/⇅ buttons mirror their (hidden) combo as a popup
        menu: same items, current one checked, and picking an item drives the
        combo's index so the normal persist/apply path runs. Rebuilt on every
        open, so it always reflects the current items and language."""
        menu.clear()
        for i in range(box.count()):
            act = menu.addAction(box.itemText(i))
            act.setCheckable(True)
            act.setChecked(i == box.currentIndex())
            act.triggered.connect(
                lambda _c, i=i, box=box: box.setCurrentIndex(i))

    def _sidebar_narrow(self) -> bool:
        """Whether the window is currently too narrow for the expanded sidebar
        plus the middle and detail columns to breathe."""
        expanded = getattr(self, "_sidebar_expanded_w", 200)
        threshold = (expanded + self._mid.minimumWidth()
                     + self._det.minimumWidth() + 40)
        return self.width() < threshold

    def _end_fs_exit(self) -> None:
        """Close out a fullscreen exit: stop muting the auto-collapse and
        resync its edge baseline to the restored geometry without acting on
        it - the user's pre-fullscreen rail/expanded choice (just re-applied)
        stands, and only the next genuine crossing of the width threshold
        auto-adapts again."""
        self._fs_exiting = False
        if hasattr(self, "_det"):
            self._last_narrow = self._sidebar_narrow()

    def _maybe_auto_collapse_sidebar(self) -> None:
        """Collapse the sidebar to the icon rail automatically when the whole
        window gets too narrow for the three columns to breathe, and expand it
        again when there's room - but only if WE auto-collapsed it (a manual
        collapse/expand is left alone). Edge-triggered on the width threshold so
        it never fights a manual toggle within the narrow band. Muted during a
        fullscreen exit (_fs_exiting): the fullscreen-to-normal resize is not a
        real edge, and acting on it re-collapsed a deliberately expanded
        sidebar every time you left fullscreen."""
        if (getattr(self, "_player_fs", False)
                or getattr(self, "_fs_exiting", False)
                or getattr(self, "_side_dragging", False)
                or not hasattr(self, "_det")):
            return
        narrow = self._sidebar_narrow()
        if narrow == getattr(self, "_last_narrow", False):
            return
        self._last_narrow = narrow
        collapsed = getattr(self, "_sidebar_collapsed", False)
        if narrow and not collapsed:
            self._auto_collapsed = True
            self._set_sidebar_collapsed(True)
        elif not narrow and collapsed and getattr(self, "_auto_collapsed", False):
            self._auto_collapsed = False
            self._set_sidebar_collapsed(False)
