"""Building the window (MainWindow mixin): the parts of main_window.py's
``_build_ui``, one method each, which it calls in the order they were always
made - that order is the focus chain, the stacking of overlapping siblings
and the order signals' handlers run in. The menus (``_build_menus``), the
central stack and its splitter (``_build_center``), the sidebar
(``_build_sidebar_shell``, ``_build_places_nav``, ``_build_category_column``,
``_build_sidebar_foot``), the middle pane (``_build_middle_pane``,
``_build_list_controls``, ``_build_list_view``, ``_build_status_row``), the
three columns in the splitter (``_build_columns``), the timers and the
playback state set beside them (``_build_timers``) and the fixed keys
(``_build_shortcuts``); the detail column is ui/mw_build_detail.py, whose
``_BuildDetailMixin`` is this one's base. Moved out of main_window.py as they
were; a part's locals that a later part uses are handed on.
"""

from __future__ import annotations

from functools import partial

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QAction, QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView, QBoxLayout, QButtonGroup, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMenu, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from ..i18n import tr
from . import a11y
from .app_menu import MENUS, lay_out, open_website
from .channel_list import (
    CategoryColorDelegate, ChannelDelegate, ChannelListModel, ChannelListView,
    FolderDropList,
)
from .mw_build_detail import _BuildDetailMixin
from .nav_widgets import NavButton, PlacesFold, PlacesLayout, SideRule, SourceAvatar, SourceLabel
from .theme import P
from .widgets import HeartButton, WhileShown, _SidebarLogo


class _BuildMixin(_BuildDetailMixin):
    """MainWindow mixin: ``_build_ui``'s parts, the detail column's in its base."""
    def _build_menus(self) -> None:
        def action(text: str, slot) -> QAction:
            act = QAction(text, self)
            act.triggered.connect(slot)
            return act
        settings_action = action(tr("btn_settings") + "…", self.open_settings)
        refresh_action = action(tr("menu_refresh_playlist"), self.refresh_playlist)
        open_video_action = action(tr("menu_open_video"), self.open_local_video)
        open_video_action.setShortcut("Ctrl+O")
        open_address_action = action(tr("menu_open_address"), self.open_address)
        open_address_action.setShortcut("Ctrl+L")
        reminders_action = action(tr("reminders_menu"), self._open_reminders)
        stats_action = action(tr("stats_menu") + "…", self.open_stats)
        multiview_action = action(tr("menu_multiview"), self._show_multiview)
        cast_action = action(tr("ctx_cast_to_chromecast"), self.cast_playing)
        together_action = action(tr("together_title") + "…", self.open_together)
        phone_action = action(tr("phone_menu"), self.open_phone_remote)
        tv_action = action(tr("tv_mode"), self.toggle_tv_mode)
        # Checked while TV mode is on (mw_tv): on macOS the menu bar stays
        # in reach over it, and it says so in the window's own terms.
        tv_action.setCheckable(True)
        teletext_action = action(tr("ttx_menu"), self.open_teletext)
        self._teletext_action = teletext_action
        calendar_action = action(tr("cal_menu"), self.open_calendar_export)
        private_action, clear_action = self._build_privacy_actions()
        self._donate_action = action(tr("donate_support") + "…", lambda: self.open_donate("app-menu"))
        report_actions = self._build_report_actions()
        self._feedback_action = report_actions["feedback"]
        website_action = action(tr("menu_website"), open_website)
        about_action = action(tr("menu_about"), self.show_about)
        quit_action = action(tr("menu_quit"), self.close)
        # File, View, History and Help on every platform (app_menu); on
        # macOS About, Settings, Quit and Leave a tip go into the
        # application menu by their roles. Kept by name: the command
        # palette lists them.
        self._menu_actions = {
            "about": about_action, "settings": settings_action,
            "open_video": open_video_action, "open_address": open_address_action,
            "refresh": refresh_action, "reminders": reminders_action, "calendar": calendar_action,
            "multiview": multiview_action, "cast": cast_action,
            "together": together_action, "phone": phone_action,
            "tv_mode": tv_action, "teletext": teletext_action,
            "private": private_action, "clear": clear_action,
            "stats": stats_action,
            **report_actions, "website": website_action,
            "donate": self._donate_action,
            "quit": quit_action}
        menus = lay_out(self.menuBar(), self._menu_actions)
        self._answers_menu = menus["help"]
        # Only while something is playing - there is nothing to send
        # otherwise, and a dead entry is worse than none.
        menus["view"].aboutToShow.connect(
            lambda: cast_action.setEnabled(self.can_cast_playing()))
        # Teletext is a live channel's.
        menus["view"].aboutToShow.connect(self._ttx_menu_state)
        # Kept for live language switching (see retranslate_ui).
        self._i18n_actions = {
            settings_action: lambda: tr("btn_settings") + "…",
            refresh_action: lambda: tr("menu_refresh_playlist"),
            open_video_action: lambda: tr("menu_open_video"),
            teletext_action: lambda: tr("ttx_menu"),
            open_address_action: lambda: tr("menu_open_address"),
            reminders_action: lambda: tr("reminders_menu"),
            multiview_action: lambda: tr("menu_multiview"),
            cast_action: lambda: tr("ctx_cast_to_chromecast"),
            together_action: lambda: tr("together_title") + "…",
            phone_action: lambda: tr("phone_menu"),
            tv_action: lambda: tr("tv_mode"),
            calendar_action: lambda: tr("cal_menu"),
            about_action: lambda: tr("menu_about"),
            quit_action: lambda: tr("menu_quit"),
            self._donate_action: lambda: tr("donate_support") + "…",
            self._feedback_action: lambda: tr("feedback_menu") + "…",
            self._report_wait_action: lambda: tr("report_title") + "…",
            report_actions["answers"]: self._answers_menu_text,
            website_action: lambda: tr("menu_website"),
            private_action: lambda: tr("private_menu"),
            clear_action: lambda: tr("clear_menu") + "…",
            stats_action: lambda: tr("stats_menu") + "…",
        }
        for key, title in MENUS:
            if key in menus:
                self._i18n_actions[menus[key].menuAction()] = partial(tr, title)

    def _build_center(self) -> QSplitter:
        root = QSplitter(Qt.Orientation.Horizontal)
        root.setHandleWidth(6)
        # The central area is a stack: page 0 is the three-column window;
        # TV mode adds its page (mw_tv).
        self._center_stack = QStackedWidget()
        self._center_stack.addWidget(root)
        self.setCentralWidget(self._center_stack)
        return root

    def _build_sidebar_shell(self) -> tuple[QWidget, QVBoxLayout]:
        # Sidebar. Its content lives inside a scroll area so that on a short
        # screen (small laptops) the places and the categories stay reachable
        # by scrolling instead of being clipped off; the foot (the activity
        # line, a decision card, the avatar and Settings) is a fixed area of
        # its own under it, always on screen. On a tall screen the scroll bar
        # never appears.
        # The scroll area sits INSIDE `side` (not in its place), so the
        # collapse-to-rail and splitter logic, which drive `side` and the button
        # widgets directly, are untouched.
        side = QWidget(objectName="Sidebar")
        _side_outer = QVBoxLayout(side)
        _side_outer.setContentsMargins(0, 0, 0, 0)
        _side_outer.setSpacing(0)
        self._side_scroll = QScrollArea()
        self._side_scroll.setWidgetResizable(True)
        self._side_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._side_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._side_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # Transparent so the themed #Sidebar background (on `side`) shows
        # through. IMPORTANT: scope these to the widgets themselves with ID
        # selectors - a bare `background: transparent` cascades to child
        # widgets and wipes their own backgrounds (it silently blanked the
        # Guide/Settings action buttons' fill).
        self._side_scroll.setObjectName("SideScroll")
        a11y.unfocused(self._side_scroll)       # Tab goes to its entries
        self._side_scroll.setStyleSheet(
            "QScrollArea#SideScroll { background: transparent; border: 0; }")
        self._side_scroll.viewport().setObjectName("SideViewport")
        self._side_scroll.viewport().setStyleSheet(
            "QWidget#SideViewport { background: transparent; }")
        _side_outer.addWidget(self._side_scroll)
        _side_content = QWidget(objectName="SideContent")
        _side_content.setStyleSheet(
            "QWidget#SideContent { background: transparent; }")
        self._side_scroll.setWidget(_side_content)
        sl = QVBoxLayout(_side_content)
        sl.setContentsMargins(12, 16, 12, 4)
        # The foot, under the part that scrolls: that part gives up the room
        # the foot takes (a decision card coming, mw_busy) and takes it back.
        self._side_foot = QWidget(objectName="SideFoot")
        self._side_foot.setStyleSheet("QWidget#SideFoot { background: transparent; }")
        self._side_foot_lay = QVBoxLayout(self._side_foot)
        self._side_foot_lay.setContentsMargins(12, 6, 12, 12)
        self._side_foot_lay.setSpacing(6)
        _side_outer.addWidget(self._side_foot)
        # Tight vertical rhythm so the TV..History nav stack stays compact.
        sl.setSpacing(2)

        # Small themed logo at the top of the sidebar (recolours with theme).
        self._sidebar_logo = _SidebarLogo()
        self._sidebar_logo.setMinimumWidth(0)
        self._sidebar_logo.setSizePolicy(QSizePolicy.Policy.Ignored,
                                         QSizePolicy.Policy.Fixed)
        self._sidebar_logo.setToolTip(tr("tooltip_jump_playing"))
        self._sidebar_logo.clicked.connect(self._jump_to_now_playing)
        # The logo draws a small update badge in its top-right corner when a
        # newer release is out; clicking that corner opens About.
        self._sidebar_logo.update_clicked.connect(self.show_about)
        # The logo alone in its row, centred. The donate heart and the private
        # marker lie over it (mw_donate, mw_privacy) -
        # in no layout, so nothing beside the logo moves.
        self._logo_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._logo_row.setContentsMargins(0, 0, 0, 0)
        self._logo_row.setSpacing(0)
        self._logo_row.addStretch(1)
        self._logo_row.addWidget(self._sidebar_logo)
        sl.addLayout(self._logo_row)
        sl.addSpacing(10)
        return side, sl

    def _build_places_nav(self, sl: QVBoxLayout) -> None:
        # Vector-icon kinds for the nav entries (drawn in _action_pixmap), all
        # in one outline style. Each must be visually distinct: watch-later is
        # a bookmark, recordings a record ring, history a clock, the guide
        # programme bars on a timeline. Drawn, not emoji: every OS renders its
        # own emoji font differently.
        self._rail_glyphs = {
            "mitt": "star", "search": "search", "live": "tv", "guide": "guide", "vod": "movie",
            "series": "series", "rec": "rec", "local": "folder_o", "radio": "radio",
        }
        self._nav_texts: dict[str, str] = {}
        self.nav_btns: dict[str, NavButton] = {}
        # Browse-mode availability per active provider (see
        # _refresh_mode_availability): hides TV/Movies/Series a provider has no
        # content for. Empty/unknown = shown, so nothing hides until we KNOW.
        self._avail_gen = 0
        self._mode_avail: dict[str, bool] = {}
        # History's filter (All / Watched), kept while the app runs: the
        # History entry comes back to the one last chosen.
        self._history_filter = "all"

        def _make_nav(key: str, text: str, into, primary: bool = False) -> None:
            # The Guide is a place too: its page in the middle pane.
            b = NavButton(key, text)
            b.setToolTip(text)
            b.setAccessibleName(text)
            # Places (TV, Guide, Movies, ...) are the primary tier: a couple
            # of notches larger than the rows under MINE (see the
            # NavBtn[primary] theme rule; _apply_nav_icons paints their icons
            # larger to match). A fixed-size icon so every label starts at the
            # same x; _apply_nav_icons paints it, muted, in the entry's
            # selected ink when checked.
            b.setProperty("primary", "true" if primary else "false")
            b.navigate.connect(self._on_nav_clicked)
            # Right-click to give this entry a custom colour.
            b.menu_requested.connect(self._nav_color_menu)
            self._nav_texts[key] = text
            into.addWidget(b)
            self.nav_btns[key] = b
            self._apply_nav_color(key)

        # The places - the app's structure (mw_places, core/places.py): Mine,
        # TV, Movies, Series, the Guide and Search, a hairline, then Local
        # files, Radio and Recordings. Mine holds what were Favorites, Watch
        # later and History; there is no Home. _on_nav_clicked sends each
        # entry to its place. They are a block of their own (PlacesLayout):
        # a column as ever, or folded a compact row of icons at the top that
        # gives the categories the room (the chevron under them; Settings >
        # Appearance), and an entry a person never uses can be taken out
        # (mw_place_look).
        self._places_box = QWidget(objectName="PlacesBox")
        self._places_box.setStyleSheet("QWidget#PlacesBox { background: transparent; }")
        # Preferred, not Fixed: a Fixed height caps the folded block's rows
        # at one (a layout's height-for-width is held to the size hint).
        self._places_box.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._places_lay = PlacesLayout(self._places_box, spacing=sl.spacing())
        sl.addWidget(self._places_box)
        upper = (("mitt", tr("nav_mine")), ("live", tr("nav_tv")),
                 ("vod", tr("nav_movies")), ("series", tr("nav_series")),
                 ("guide", tr("nav_guide")), ("search", tr("nav_search")))
        lower = (("local", tr("nav_local")), ("radio", tr("nav_radio")),
                 ("rec", tr("nav_recordings")))
        for key, text in upper:
            _make_nav(key, text, self._places_lay, primary=True)
        # Between the two groups, on the rail too: a hairline and its gap.
        self._places_rule = SideRule()
        self._places_lay.addWidget(self._places_rule)
        for key, text in lower:
            _make_nav(key, text, self._places_lay, primary=True)
        self._places_fold_btn = PlacesFold()
        self._places_fold_btn.clicked.connect(self._toggle_places_fold)
        self._places_lay.addWidget(self._places_fold_btn)
        # Rail-only filler. Expanded, the CATEGORY LIST is the layout's stretch
        # item and soaks up the spare height - but on the icon rail it's
        # hidden, and a QVBoxLayout with no stretch item spreads its surplus
        # between the remaining rows instead, which blew the icons apart
        # (browse tight, library gaps ~10x). Shown only on the rail, this
        # invisible expanding filler takes over the category list's job so the
        # icons stay packed at the top and the actions stay at the bottom.
        self._rail_filler = QWidget()
        self._rail_filler.setSizePolicy(QSizePolicy.Policy.Ignored,
                                        QSizePolicy.Policy.Expanding)
        self._rail_filler.hide()
        sl.addWidget(self._rail_filler, 1)
        self._apply_nav_icons()
        from ..core import places as _places
        self.nav_btns[_places.nav_of_mode(self.mode)].setChecked(True)
        # The profile's own: the entries taken out, the block folded or not.
        self._place_look_init()

    def _build_category_column(self, sl: QVBoxLayout) -> None:
        # The source the lists show, over its categories: its name, its kind
        # in a chip with two sources or more, the source menu on a click
        # (mw_source_label). Hidden on the rail, where the avatar says it.
        sl.addSpacing(8)
        self._source_label = SourceLabel()
        self._source_label.clicked.connect(self._show_source_menu)
        self._source_label.hide()
        sl.addWidget(self._source_label)
        # "Categories" header with a small "solo" toggle on the right that
        # collapses the list to just the active category. Kept in a zero-margin
        # row so it takes exactly the label's height - the nav-button spacing
        # above stays untouched.
        cat_hdr = QHBoxLayout()
        cat_hdr.setContentsMargins(0, 0, 0, 0)
        cat_hdr.setSpacing(4)
        self._cat_section_label = QLabel(
            tr("sidebar_categories"), objectName="SectionLabel")
        self._cat_section_label.setMinimumWidth(0)
        cat_hdr.addWidget(self._cat_section_label)
        cat_hdr.addStretch()
        # A disclosure-style toggle: a Qt-drawn arrow (not a font glyph, so it
        # can't render as a box/ring) - down = list expanded, right = collapsed
        # to just the active category. Reads like a collapsible section header.
        self.cat_solo_btn = QToolButton(objectName="SectionToggle")
        self.cat_solo_btn.setCheckable(True)
        self.cat_solo_btn.setArrowType(Qt.ArrowType.DownArrow)
        self.cat_solo_btn.setAutoRaise(True)
        self.cat_solo_btn.setFixedSize(22, 18)
        self.cat_solo_btn.setToolTip(tr("tooltip_solo_category"))
        self.cat_solo_btn.setAccessibleName(tr("tooltip_solo_category"))
        # A search toggle that reveals the category search box only when you
        # want it, so it doesn't take a permanent row in the sidebar.
        self.cat_search_btn = QToolButton(objectName="SectionToggle")
        self.cat_search_btn.setCheckable(True)
        self.cat_search_btn.setAutoRaise(True)
        self.cat_search_btn.setFixedSize(22, 18)
        self._apply_cat_search_icon()   # ink-centred 🔍 (not a top-left glyph)
        self.cat_search_btn.toggled.connect(self._toggle_cat_search)
        cat_hdr.addWidget(self.cat_search_btn)
        self.cat_solo_btn.toggled.connect(self._on_cat_solo_toggle)
        cat_hdr.addWidget(self.cat_solo_btn)
        sl.addLayout(cat_hdr)
        # Search that spans category names AND their channels: type "germany"
        # or "bbc" and the matching categories float up (ranked by how many of
        # their channels match), each previewing a few hits. Double-click to
        # enter that category. Hidden until the 🔍 toggle is clicked. Its
        # words, and the toggle's tooltip, follow the section
        # (_sync_cat_search_hint).
        self.cat_search = QLineEdit(objectName="CatSearch")
        self.cat_search.setClearButtonEnabled(True)
        self._sync_cat_search_hint()
        self.cat_search.textChanged.connect(self._on_cat_search)
        self.cat_search.hide()
        sl.addWidget(self.cat_search)
        self._cat_search_timer = QTimer(self)
        self._cat_search_timer.setSingleShot(True)
        self._cat_search_timer.setInterval(220)
        self._cat_search_timer.timeout.connect(self._run_category_search)
        self._search_index_cache: dict = {}
        self.cat_list = FolderDropList(objectName="CatList")
        self.cat_list.setAccessibleName(tr("tv_categories"))
        a11y.show_current(self.cat_list)        # Tab in: the row the keyboard is on, shown
        self.cat_list.can_drop = self._fav_drop_allowed
        self.cat_list.on_drop = self._fav_drop_on_folder
        self.cat_list.setItemDelegate(CategoryColorDelegate(self.cat_list))
        self.cat_list.setMinimumWidth(0)
        # Pixel-granular scrolling like the channel list - the default
        # jump-a-whole-row mode reads as laggy on a trackpad, especially with
        # hundreds of provider categories.
        self.cat_list.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        # Give the list a real minimum height so, on a short sidebar, the OUTER
        # scroll area shows a scrollbar (keeping the bottom actions - Guide,
        # Settings - reachable) instead of the QVBoxLayout compressing the
        # fixed buttons below their size hint and clipping their text. On a tall
        # sidebar this minimum is never the binding constraint, so nothing
        # changes there.
        self.cat_list.setMinimumHeight(80)
        self.cat_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.cat_list.currentItemChanged.connect(self._category_changed)
        # A category clicked while the Guide shows: over TV the guide follows
        # it, else that category's list; double-clicked: its guide.
        self.cat_list.itemClicked.connect(self._guide_category_clicked)
        self.cat_list.itemDoubleClicked.connect(self._guide_category_double_clicked)
        self.cat_list.itemClicked.connect(self._sp_category_clicked)
        self.cat_list.itemClicked.connect(self._search_category_clicked)
        self.cat_list.entered.connect(self._category_entered)     # Enter: the keyboard's click
        self.cat_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.cat_list.customContextMenuRequested.connect(self._cat_menu)
        sl.addWidget(self.cat_list, 1)

    def _build_sidebar_foot(self, sl: QVBoxLayout) -> None:
        # Contextual "Sync now" - only shown in the Trakt-backed lists
        # (Watched, Watch Later, Favorites -> Trakt) so the user can pull
        # fresh data without digging into Settings. Sits above the action row.
        self._sync_now_btn = QPushButton(tr("btn_sync_now"))
        self._sync_now_btn.clicked.connect(self._sidebar_sync_now)
        # "Ignored" horizontal policy + no text-based minimum, like the nav
        # buttons: otherwise this button's label width pinned the sidebar wider
        # than the icon rail, so it couldn't collapse while a Trakt-backed list
        # (Watched / Trakt favourites) had the button showing.
        self._sync_now_btn.setSizePolicy(QSizePolicy.Policy.Ignored,
                                         QSizePolicy.Policy.Fixed)
        self._sync_now_btn.setMinimumWidth(0)
        # Deliberately loud (red, bold) so it's obvious when it appears -
        # it only shows in the Trakt-backed lists, so it shouldn't blend
        # in with the neutral sidebar buttons.
        self._sync_now_btn.setStyleSheet(
            "QPushButton{background:#e5354b; color:#ffffff; font-weight:700;"
            " border:none; border-radius:6px; padding:8px;}"
            "QPushButton:hover{background:#c8283b;}")
        self._sync_now_btn.hide()
        sl.addWidget(self._sync_now_btn)

        # The foot, under a hairline: the active source as an avatar (its
        # initials on the accent; a click opens the source switcher) and
        # Settings, icon buttons side by side - one above the other on the
        # rail (_apply_sidebar_chrome). Multiview is in the menu bar's View
        # group and the channel menu's "Add to multiview". (The guide is a
        # nav entry; reload lives in "Refresh playlist" and the per-playlist
        # auto-refresh setting - a sidebar button for it was a mis-click.)
        self._playlist_btn = SourceAvatar()
        self._playlist_btn.clicked.connect(self._show_playlist_menu)
        self._settings_btn = QPushButton(objectName="SideIcon")
        self._settings_btn.setFixedSize(SourceAvatar.SIZE, SourceAvatar.SIZE)
        self._settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._settings_btn.setToolTip(tr("btn_settings"))
        self._settings_btn.setAccessibleName(tr("btn_settings"))
        self._settings_btn.clicked.connect(self.open_settings)
        # A small red heart on the logo's lower corner, its words in the
        # tooltip (mw_donate) - the same place on every system.
        self._donate_btn = donate_btn = HeartButton(objectName="DonateHeart", size=12)
        donate_btn.setFixedSize(22, 22)
        donate_btn.setToolTip(tr("donate_support"))
        donate_btn.setAccessibleName(tr("donate_support"))
        donate_btn.clicked.connect(lambda: self.heart_clicked("app-heart"))
        # Right-click: hide it for three months (mw_donate).
        donate_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        donate_btn.customContextMenuRequested.connect(self._donate_heart_menu)
        self._bottom_rule = SideRule()
        self._side_foot_lay.addWidget(self._bottom_rule)
        self._actions_box = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._actions_box.setContentsMargins(0, 2, 0, 0)
        self._actions_box.setSpacing(10)
        self._actions_box.addWidget(self._playlist_btn)
        self._actions_box.addWidget(self._settings_btn)
        self._side_foot_lay.addLayout(self._actions_box)
        # Centred as a pair (a per-button alignment spread them apart).
        self._side_foot_lay.setAlignment(self._actions_box, Qt.AlignmentFlag.AlignHCenter)
        self._update_playlist_btn()
        self._logo_row.addStretch(1)
        self._build_private_pill()
        self._build_heart_badge()
        self._fit_logo(False)
        self._donate_heart_refresh()         # hidden for three months, maybe
        self._apply_action_icons()
        # The nav's quiet status: a recording running, new episodes of a
        # followed series, what waits under Watch later.
        self.rec.jobs_changed.connect(self._refresh_nav_status)
        self._refresh_nav_status()

    def _build_middle_pane(self) -> tuple[QWidget, QVBoxLayout, QVBoxLayout]:
        # Middle column: a stack of its pages - the list (page 0) and the
        # Guide (added the first time it opens, ui/mw_guide.py). The pane
        # itself stays the splitter's column, so focus mode, fullscreen and
        # the width logic that show, hide and measure self._mid cover both.
        mid = QWidget(objectName="MiddlePane")
        _mid_l = QVBoxLayout(mid)
        _mid_l.setContentsMargins(0, 0, 0, 0)
        _mid_l.setSpacing(0)
        self._mid_stack = QStackedWidget()
        _mid_l.addWidget(self._mid_stack)
        self._list_page = QWidget(objectName="ListPage")
        self._mid_stack.addWidget(self._list_page)
        lp = QVBoxLayout(self._list_page)
        # No horizontal margins on the middle pane. Qt's IconMode reserves
        # ~16 px at the end of each row for its internal wrap check, and any
        # extra inset on either side compounds with that - even 6-8 px of
        # margin was enough to push the last column onto the next row,
        # leaving a huge gap on the right (see channel_list._justify_grid).
        lp.setContentsMargins(0, 14, 0, 10)
        lp.setSpacing(10)
        # The list and its bar, or in their place a place of the unified
        # look (the Series place, ui/mw_place_series.py) - the status row
        # under both.
        self._list_stack = QStackedWidget()
        lp.addWidget(self._list_stack, 1)
        self._list_body = QWidget(objectName="ListBody")
        self._list_stack.addWidget(self._list_body)
        ml = QVBoxLayout(self._list_body)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(10)
        # The place's header over its list: its name, its count, Mine's tabs
        # (mw_places, media_cards.PageHeader) - the form every place shares.
        self._build_place_header(ml)
        # No loading strip over the list any more: it pushed the list down
        # and up as work came and went. What the app is doing is said in the
        # notices' corner (mw_busy, ui/notices.py).
        return mid, lp, ml

    def _build_list_controls(self, ml: QVBoxLayout) -> None:
        self.search = QLineEdit(objectName="Search")
        self.search.setPlaceholderText(tr("list_filter_placeholder"))
        self.search.setAccessibleName(tr("list_filter_placeholder"))     # _sync_search_hint's
        self.search.setClearButtonEnabled(True)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._play_preview)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_filter)
        self.search.textChanged.connect(lambda _t: self._search_timer.start(220))
        ml.addWidget(self.search)

        ctl = QHBoxLayout()
        ctl.setSpacing(6)
        self.size_box = self._combo(
            [("compact", tr("option_compact")), ("medium", tr("option_medium")),
             ("large", tr("option_large")), ("xlarge", tr("option_xlarge"))],
            self.settings.value("view_density", "medium"))
        self.size_box.setObjectName("InlineCombo")
        self.size_box.setAccessibleName(tr("label_size"))
        self.size_box.currentIndexChanged.connect(self._inline_view_changed)
        self.sort_box = self._combo(self._sort_options(), "global")
        self.sort_box.setObjectName("InlineCombo")
        self.sort_box.setAccessibleName(tr("label_sort"))
        self.sort_box.setToolTip(tr("sort_scope_tip"))
        self.sort_box.currentIndexChanged.connect(self._inline_view_changed)
        self.grid_btn = QPushButton(tr("btn_grid"), objectName="InlineToggle")
        self.grid_btn.setIcon(self._nav_icon("gridview", 14))
        self.grid_btn.setIconSize(QSize(14, 14))
        self.grid_btn.setCheckable(True)
        self.grid_btn.setChecked(
            self.settings.value("view_grid", "false") == "true")
        self.grid_btn.toggled.connect(self._inline_view_changed)
        # Compact stand-ins for the Size/Sort combos, mirroring how the grid
        # toggle shrinks to a glyph: tool buttons that open the same choices as
        # a popup menu (fully readable at any pane width, no clipped text).
        # Shown only when the pane is narrow - see _apply_mid_compact.
        self._size_menu_btn = QToolButton(objectName="InlineToggle")
        self._size_menu_btn.setIcon(
            QIcon(self._action_pixmap("sizepick", 15, P["text2"])))
        self._size_menu_btn.setIconSize(QSize(15, 15))
        self._size_menu_btn.setToolTip(tr("label_size"))
        self._size_menu_btn.setAccessibleName(tr("label_size"))
        self._size_menu_btn.setFixedWidth(30)
        self._size_menu_btn.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        self._sort_menu_btn = QToolButton(objectName="InlineToggle")
        self._sort_menu_btn.setIcon(
            QIcon(self._action_pixmap("sort", 15, P["text2"])))
        self._sort_menu_btn.setIconSize(QSize(15, 15))
        self._sort_menu_btn.setToolTip(tr("label_sort"))
        self._sort_menu_btn.setAccessibleName(tr("label_sort"))
        self._sort_menu_btn.setFixedWidth(30)
        self._sort_menu_btn.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        for _btn, _box in ((self._size_menu_btn, self.size_box),
                           (self._sort_menu_btn, self.sort_box)):
            _m = QMenu(_btn)
            _btn.setMenu(_m)
            _m.aboutToShow.connect(
                lambda m=_m, box=_box: self._fill_combo_menu(m, box))
        self._size_menu_btn.hide()
        self._sort_menu_btn.hide()
        self._size_label = QLabel(tr("label_size"))
        self._sort_label = QLabel(tr("label_sort"))
        # Toggle for the left category column - handy for clean screenshots
        # (hide category names) or just more room for the list.
        self.side_btn = QPushButton("", objectName="InlineToggle")
        self.side_btn.setIcon(self._nav_icon("bars", 15))
        self.side_btn.setIconSize(QSize(15, 15))
        self.side_btn.setCheckable(True)
        self.side_btn.setChecked(True)
        self.side_btn.setToolTip(tr("tooltip_toggle_sidebar"))
        self.side_btn.setAccessibleName(a11y.without_key(tr("tooltip_toggle_sidebar")))
        self.side_btn.setFixedWidth(34)
        self.side_btn.toggled.connect(self._on_side_toggle)
        # Focus mode: hide this whole list column to give the player the room,
        # reopened via the arrow strip on the detail pane's edge.
        # Vector icon (not a text glyph): ⤢ as raw text sat off-centre,
        # clipped, and rendered differently per OS. _apply_action_icons
        # re-tints it on theme change; this initial paint covers construction
        # (which happens after the first icon pass).
        self.focus_btn = QPushButton("", objectName="InlineToggle")
        self.focus_btn.setIcon(
            QIcon(self._action_pixmap("focus", 16, P["text2"])))
        self.focus_btn.setIconSize(QSize(16, 16))
        self.focus_btn.setToolTip(tr("tooltip_hide_list"))
        self.focus_btn.setAccessibleName(a11y.without_key(tr("tooltip_hide_list")))
        self.focus_btn.setFixedWidth(34)
        self.focus_btn.clicked.connect(lambda: self._set_focus_mode(True))
        ctl.addWidget(self.side_btn)
        ctl.addWidget(self.focus_btn)
        # The size and the sort each in a box of their own: the TV place's
        # header has its own sort and Compact / Airy, and hides these boxes
        # (mw_place_tv) whatever the narrow pane shows of them inside.
        self._size_ctl = QWidget()
        self._sort_ctl = QWidget()
        for box, widgets in ((self._size_ctl, (self._size_label, self.size_box,
                                               self._size_menu_btn)),
                             (self._sort_ctl, (self._sort_label, self.sort_box,
                                               self._sort_menu_btn))):
            box_lay = QHBoxLayout(box)
            box_lay.setContentsMargins(0, 0, 0, 0)
            box_lay.setSpacing(6)
            for widget in widgets:
                box_lay.addWidget(widget)
            ctl.addWidget(box)
        ctl.addStretch()
        # Folder/library switch for the Local files section - lives up here
        # with the other view controls (it sat as a fake row in the category
        # column first, which read as a folder, not a control).
        self.local_view_btn = QPushButton(objectName="InlineToggle")
        self.local_view_btn.setCheckable(True)
        self.local_view_btn.setText(tr("local_view_folders"))   # _sync_local_view_btn's
        self.local_view_btn.hide()
        self.local_view_btn.clicked.connect(self._local_toggle_view)
        # Rescan beside it: the folder or the library read from the disk
        # again (mw_local; F5 too). Its tooltip names the key in force.
        self.local_rescan_btn = QPushButton("", objectName="InlineToggle")
        self.local_rescan_btn.setIcon(
            QIcon(self._action_pixmap("refresh", 15, P["text2"])))
        self.local_rescan_btn.setIconSize(QSize(15, 15))
        self.local_rescan_btn.setFixedWidth(34)
        self.local_rescan_btn.setToolTip(tr("local_rescan_tip"))
        self.local_rescan_btn.setAccessibleName(tr("local_rescan"))
        self.local_rescan_btn.hide()
        self.local_rescan_btn.clicked.connect(self._local_rescan)
        ctl.addWidget(self.local_rescan_btn)
        ctl.addWidget(self.local_view_btn)
        ctl.addWidget(self.grid_btn)
        ml.addLayout(ctl)

    def _build_list_view(self, ml: QVBoxLayout) -> None:
        # History's two filters: everything played, or what is marked as
        # watched (the "watched" mode, which has no nav entry of its own).
        self._history_chips = QWidget()
        hc = QHBoxLayout(self._history_chips)
        hc.setContentsMargins(0, 0, 0, 0)
        hc.setSpacing(6)
        self._history_chip_group = QButtonGroup(self._history_chips)
        self._history_chip_group.setExclusive(True)
        self._history_chip_btns: dict[str, QPushButton] = {}
        for i, (key, text) in enumerate((("all", tr("history_chip_all")),
                                         ("watched",
                                          tr("history_chip_watched")))):
            chip = QPushButton(text, objectName="FilterChip")
            chip.setCheckable(True)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            self._history_chip_group.addButton(chip, i)
            self._history_chip_btns[key] = chip
            hc.addWidget(chip)
        hc.addStretch()
        self._history_chip_btns["all"].setChecked(True)
        self._history_chip_group.idClicked.connect(self._on_history_chip)
        self._history_chips.hide()
        ml.addWidget(self._history_chips)

        self.back_btn = QPushButton("<-  " + tr("btn_back_to_series"))
        self.back_btn.hide()
        self.back_btn.clicked.connect(self._leave_series)
        ml.addWidget(self.back_btn)

        self.clear_history_btn = QPushButton(tr("msg_clear_history_title"))
        self.clear_history_btn.hide()
        self.clear_history_btn.clicked.connect(self._clear_history)
        ml.addWidget(self.clear_history_btn)

        self.listw = ChannelListView(objectName="Channels")
        a11y.show_current(self.listw)
        self.list_model = ChannelListModel()
        self.listw.setModel(self.list_model)
        # Catch-all: whenever the model is repopulated with rows (from any of
        # the many set_items call sites), retire the "Loading channels..." hint
        # and any busy overlay. Some paths - the startup load when the window is
        # inactive, jumping Home->TV - don't run through _apply_filter, so the
        # hint could otherwise linger under a fully populated list.
        self.list_model.modelReset.connect(self._on_list_populated)
        # The Movies place shows the list's films as cards (mw_place_films).
        self.list_model.modelReset.connect(self._pf_model_reset)
        self.delegate = ChannelDelegate(
            self, self.settings.value("view_density", "medium"))
        self.listw.setItemDelegate(self.delegate)
        # Each row read as it is painted (a screen reader asks the model).
        self.list_model.describe = self.delegate.words_for
        self.listw.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.listw.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.listw.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.listw.setUniformItemSizes(True)
        self.listw.setMouseTracking(True)
        self.listw.selectionModel().currentChanged.connect(
            self._on_current_changed)
        self.listw.doubleClicked.connect(lambda _idx: self.play())
        self.listw.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.listw.customContextMenuRequested.connect(self._context_menu)
        ml.addWidget(self.listw, 1)

        # NB: no bottom "Loading channels..." label here any more. It kept
        # lingering after whichever async load path got discarded (startup on
        # Home, playing from Home, rapid mode hops) - and the busy strip plus
        # the centred spinner overlay already say "loading" while the list is
        # empty, so the label only ever added a way to get stuck.

    def _build_status_row(self, lp: QVBoxLayout) -> None:
        # No source of one's own yet (explore, the demo): a calm strip that
        # says so under the middle pane's pages - every place's, in the
        # layout, never over a row (mw_onboarding).
        self._build_source_strip(self._mid_stack.parentWidget().layout())
        self.count_lbl = QLabel("")
        self.count_lbl.setTextFormat(Qt.TextFormat.PlainText)   # a channel's, a device's name
        self.count_lbl.setStyleSheet(f"color:{P['muted3']}; font-size:11px;")
        status_row = QHBoxLayout()
        status_row.addWidget(self.count_lbl, 1)
        # Subtle "Update available" link in the status row - shown only when a
        # newer release is out, clicking opens About. Lives here (not as an
        # overlay toast) so it's quiet and always available while it lasts.
        self.update_status_btn = a11y.words_later(QPushButton(""))     # the update's own words
        self.update_status_btn.setFlat(True)
        self.update_status_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_status_btn.setStyleSheet(
            f"color:{P['accent']}; font-size:11px; font-weight:600;"
            "border:none; background:transparent; padding:0 4px;")
        self.update_status_btn.clicked.connect(self.show_about)
        self.update_status_btn.hide()
        status_row.addWidget(self.update_status_btn)
        # The once-only "add to the application menu" offer, as a link the
        # user can take or leave - a dialog at startup covered the window.
        self.desktop_entry_btn = QPushButton(tr("desktop_entry_title"))
        self.desktop_entry_btn.setFlat(True)
        self.desktop_entry_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.desktop_entry_btn.setStyleSheet(
            f"color:{P['accent']}; font-size:11px; font-weight:600;"
            "border:none; background:transparent; padding:0 4px;")
        self.desktop_entry_btn.clicked.connect(self._ask_desktop_entry)
        self.desktop_entry_btn.hide()
        status_row.addWidget(self.desktop_entry_btn)
        self.rec_indicator = QPushButton("● REC")
        self.rec_indicator.setFlat(True)
        self.rec_indicator.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rec_indicator.setStyleSheet(
            f"color:{P['rec']}; font-weight:700; font-size:11px;"
            "border:none; background:transparent; padding:0 4px;")
        self.rec_indicator.clicked.connect(self._rec_indicator_menu)
        self.rec_indicator.hide()
        status_row.addWidget(self.rec_indicator)
        lp.addLayout(status_row)

    def _build_columns(self, root: QSplitter, side: QWidget, mid: QWidget, det: QWidget) -> None:
        root.addWidget(side)
        root.addWidget(mid)
        root.addWidget(det)
        root.setSizes([220, 560, 380])
        root.setCollapsible(0, False)
        root.setCollapsible(1, False)
        root.setCollapsible(2, False)
        root.setStretchFactor(0, 0)
        root.setStretchFactor(1, 1)
        root.setStretchFactor(2, 0)
        # Save the panel layout every time the user drags a divider, so it
        # persists even if closeEvent doesn't run (Ctrl+C, force quit, sudden
        # kill). The window geometry is saved via moveEvent/resizeEvent below.
        root.splitterMoved.connect(self._schedule_save_layout)
        # Dragging the side divider inward past a threshold collapses the
        # sidebar to the icon rail. Pinning the rail's max width (in
        # _apply_sidebar_collapsed) is what makes this stick mid-drag - a hard
        # width constraint the splitter honours, unlike a setSizes() call.
        root.splitterMoved.connect(self._maybe_collapse_on_drag)
        # The middle pane's width also changes with divider drags, not just
        # window resizes, so keep its control strip's compact mode in sync.
        root.splitterMoved.connect(self._update_mid_compact)
        # When collapsed the rail's width is pinned (so it can't stretch), which
        # freezes its divider handle - so watch the handle for a rightward drag
        # to re-expand it without reaching for the ☰ button.
        self._side_handle = root.handle(1)
        if self._side_handle is not None:
            self._side_handle.installEventFilter(self)
        # Floor wide enough that the docked player's full control row (transport
        # + options + pop-out + fullscreen + mute + volume) always fits, so
        # dragging the divider in never crushes the buttons or drops the slider.
        # The pane's 20px side margins mean the player is 40px narrower than the
        # pane, so the floor is the ~340px bar plus those margins.
        det.setMinimumWidth(380)
        # Keep the content list from being squeezed away: dragging the sidebar
        # divider far right used to swallow the whole middle column (leaving
        # sidebar + player and no list, which just looks broken). A floor plus
        # non-collapsible keeps it always present.
        mid.setMinimumWidth(240)
        self._side, self._mid, self._det = side, mid, det
        self._root = root
        # A base minimum window size: below this the three columns can't hold
        # the docked player plus its info without overlapping (the info would
        # creep up into the video). Width = rail + middle + detail floors;
        # height fits the docked player (which scales with screen width) plus
        # its control bar, the pane chrome and a little info.
        # Height fits the docked player (fixed box + control bar) plus the
        # info column's small floor and chrome. Overlap is impossible at ANY
        # size - everything under the video lives in the info scroll column -
        # so this floor is about usability, not correctness.
        # player(box_h+~52) + pane margins(40) + spacing(12) + info floor(96)
        # + menu bar/chrome ≈ box_h + 240.
        box_h = getattr(self.player, "VIDEO_BOX_HEIGHT", 260) if self.player else 0
        min_h = (min(box_h, 180) + 240) if self.player else 320
        self._base_min = QSize(700, min_h)
        self.setMinimumSize(self._base_min)
        # The notices' corner (mw_busy, ui/notices.py), a free child of the
        # window itself, not the splitter: a QSplitter treats every child
        # widget as a pane and overrides its geometry.
        self._init_notices()

    def _build_timers(self) -> None:
        self.tick = QTimer(self)
        self.tick.timeout.connect(self._refresh_progress)
        self.tick.start(60_000)

        # Keep the timeshift live-timeline marker current - only while the
        # timeline is on screen: it does nothing else, and running always
        # it woke the idle app once a second (docs/PERFORMANCE.md).
        self._ts_segment_start = None
        self._ts_timeline_timer = QTimer(self)
        self._ts_timeline_timer.setInterval(1000)
        self._ts_timeline_timer.timeout.connect(self._update_ts_timeline)
        timeline = getattr(getattr(self, "player", None), "ts_timeline", None)
        if timeline is not None:
            WhileShown(timeline, self._ts_timeline_timer, self._update_ts_timeline)

        # Poll EPG reminders so one fires close to its programme's start.
        self._reminder_timer = QTimer(self)
        self._reminder_timer.timeout.connect(self._check_reminders)
        self._reminder_timer.start(30_000)

        # Periodically remember the playback position of movies/episodes so
        # they can be resumed later even if the app is closed abruptly.
        self._resume_timer = QTimer(self)
        self._resume_timer.timeout.connect(self._save_resume_position)
        self._resume_timer.start(12_000)
        # High-water playback progress (percent) of the current title. Kept
        # up to date by the resume timer because mpv stops reporting a
        # position once the file has ended - without this, a title watched
        # to the very end would read as 0% at auto-mark time.
        self._playback_max_pct = 0.0
        self._current_epg = None
        self._player_fs = False
        # macOS: mini-player "maximize" goes fullscreen through the mirror
        # pop-out (a frameless window covers the screen instantly, no native
        # fullscreen Space animation - the reason the pop-out felt smoother).
        self._fs_via_popout = False
        self._fs_exiting = False

    def _build_shortcuts(self) -> None:
        # Escape and Delete are structural/context-sensitive, so they stay
        # fixed; everything else is user-rebindable (see _install_shortcuts).
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self,
                  activated=self._on_escape)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self,
                  activated=self._delete_pressed)
        self._install_shortcuts()
