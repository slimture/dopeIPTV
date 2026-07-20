"""Internationalization module for dopeIPTV.

Provides a ``tr(key, **kwargs)`` function that returns the translated string
for the currently active language, with optional format substitutions.

Usage::

    from .i18n import tr, set_language, LANGUAGES

    set_language("sv")
    print(tr("status_loading_channels"))   # "Laddar kanaler…"
    print(tr("status_playing", title="CNN"))  # "Spelar: CNN"

No external dependencies beyond the Python standard library.

--------------------------------------------------------------------------
Contributor guide (how translation works in this codebase)
--------------------------------------------------------------------------
* Every user-visible string in the UI goes through ``tr("some_key")``. So to
  find everything that is translated, grep the code for ``tr(``. Any bare
  string literal passed to a Qt setter (setText / setWindowTitle / addAction /
  setPlaceholderText / setToolTip / addTab / QLabel(...) / QPushButton(...))
  is a bug unless it is a brand/technical token (e.g. "mpv", "VLC", "PiP",
  "dopeIPTV", "http://server:port").
* To ADD A STRING: add a key to ``_STRINGS`` below as ``{"en": "…"}`` (English
  only — English is the source of truth), then call ``tr("your_key")`` (with
  ``{placeholders}`` filled via keyword args, e.g.
  ``tr("status_playing", title=name)``). Add the same key + its translation to
  each ``dopeiptv/locale/<code>.json`` for the other languages (the template
  tool lists what's missing).
* To ADD A LANGUAGE: create ``dopeiptv/locale/<code>.json`` ({key: "text"}) and
  add its code + native name to ``_NATIVE_NAMES`` at the bottom. It joins the
  picker automatically once the file covers most keys. Run ``tests`` - the suite
  checks core languages are complete, placeholders line up, and every
  ``tr("…")`` used in the code has a matching key.
* Dialogs/menus that are rebuilt each time they open pick up the current
  language automatically; persistent chrome is refreshed live by
  ``MainWindow.retranslate_ui`` / ``EmbeddedPlayer.retranslate_ui``.
"""

from __future__ import annotations

_current_language: str = "en"

# English is the ONLY language that lives in code (below). Every other
# language — Swedish, Spanish, … — ships as dopeiptv/locale/<code>.json and is
# merged in + registered here at import (see _load_locale_files / _NATIVE_NAMES
# at the bottom). So this dict starts English-only; the rest are added at load.
LANGUAGES: dict[str, str] = {
    "en": "English",
}

# ---------------------------------------------------------------------------
# Every UI string keyed by a descriptive snake_case identifier. English is the
# source of truth and lives here as ``{"en": "…"}``; translations come from the
# locale JSONs and are merged onto each entry at import. Keys that contain
# ``{…}`` placeholders use Python str.format_map().
# ---------------------------------------------------------------------------

_STRINGS: dict[str, dict[str, str]] = {

    # ── Navigation / sidebar ──────────────────────────────────────────────

    "nav_home": {"en": "Home"},
    "home_featured": {"en": "Featured"},
    "home_resume": {"en": "Continue watching"},
    "home_fav_now": {"en": "On your favorites now"},
    "err_series_open": {"en": "Couldn't open this series (the provider refused the request)."},
    "home_fav_media": {"en": "Your favorites"},
    "home_recent": {"en": "Recently viewed"},
    "home_new_movies": {"en": "Recently added movies"},
    "home_new_series": {"en": "Recently added series"},
    "home_new_channels": {"en": "Recently added channels"},
    "set_home_show": {"en": "Show the Home section"},
    "set_home_start": {"en": "Open Home at startup"},
    "set_home_shelves": {"en": "Sections on Home"},

    "nav_tv": {"en": "TV"},
    "nav_movies": {"en": "Movies"},
    "nav_series": {"en": "Series"},
    "nav_favorites": {"en": "Favorites"},
    "nav_watchlist": {"en": "Watch Later"},
    "nav_watched": {"en": "Watched"},
    "watched_local": {"en": "Local"},
    "watched_trakt": {"en": "Trakt"},
    "watched_trakt_only_note": {"en": "Watched on another device - not available from this provider."},
    "fav_channels": {"en": "Channels"},
    "fav_movies": {"en": "Movies"},
    "fav_series": {"en": "Series"},
    "nav_recordings": {"en": "Recordings"},
    "nav_history": {"en": "History"},
    "sidebar_categories": {"en": "CATEGORIES"},

    "sidebar_library": {"en": "LIBRARY"},

    # ── Settings dialog tabs ──────────────────────────────────────────────

    "settings_title": {"en": "Settings"},
    "tab_playback": {"en": "Playback"},
    "tab_interface": {"en": "Interface"},
    "tab_playlists": {"en": "Playlists"},
    "account_loading": {"en": "Loading account…"},
    "account_unavailable": {"en": "Account info not available for this playlist."},
    "account_status": {"en": "Status"},
    "account_expiry": {"en": "Expires"},
    "account_connections": {"en": "Connections"},
    "account_days_left": {"en": "{days} days left"},
    "account_expired": {"en": "expired"},
    "account_unlimited": {"en": "Unlimited"},
    "account_trial": {"en": "Trial"},
    "tab_parental": {"en": "Parental"},
    "tab_metadata": {"en": "Metadata"},
    "tab_recording": {"en": "Recording"},
    "tab_trakt": {"en": "Trakt"},

    # ── Settings labels (Playback) ────────────────────────────────────────

    "setting_autoplay_preview": {"en": "Auto-play preview on selection"},
    "setting_autoplay_next": {"en": "Auto-play next episode"},
    "setting_auto_reconnect": {"en": "Auto-reconnect live streams"},
    "setting_stream_format": {"en": "Live stream format"},
    "setting_audio_lang": {"en": "Preferred audio language"},
    "setting_subtitles": {"en": "Subtitles"},
    "setting_sub_lang": {"en": "Preferred subtitle language"},
    "setting_sub_lang_fallback": {"en": "Fallback subtitle language"},
    "setting_aspect_ratio": {"en": "Aspect ratio"},
    "setting_network_buffer": {"en": "Network buffer"},
    "setting_replay_delay": {"en": "Replay delay"},
    "setting_epg_delay": {"en": "EPG delay"},
    "setting_epg_cache": {"en": "EPG cache"},
    "btn_refresh_epg": {"en": "Refresh guide now"},
    "btn_clear_epg": {"en": "Clear EPG cache"},
    "epg_cache_cleared": {"en": "EPG cache cleared — reloading…"},

    # ── Settings labels (Interface) ───────────────────────────────────────

    "setting_list_size": {"en": "List size"},
    "setting_upcoming_count": {"en": "Upcoming programmes shown"},
    "setting_sort_by": {"en": "Sort lists by"},
    "setting_theme": {"en": "Theme"},
    "setting_accent_color": {"en": "Accent color"},
    "setting_language": {"en": "Language"},
    "setting_language_restart_hint": {
        "en": "The interface switches right away — restart dopeIPTV if some"
              " texts still show the previous language."},

    # ── Combo box option labels ───────────────────────────────────────────

    "option_compact": {"en": "Compact"},
    "option_medium": {"en": "Medium"},
    "option_large": {"en": "Large"},
    "option_xlarge": {"en": "Extra large (TV)"},
    "option_sort_default": {"en": "Default (provider order)"},
    "option_sort_az": {"en": "Name A -> Z"},
    "option_sort_za": {"en": "Name Z -> A"},
    "option_sort_recent": {"en": "Recently added"},
    "option_sub_off": {"en": "Off"},
    "option_sub_auto": {"en": "On (player default)"},
    "option_sub_lang": {"en": "On - preferred language"},
    "option_sub_forced": {"en": "On - forced subtitles only"},
    "option_aspect_auto": {"en": "Auto"},
    "option_aspect_stretch": {"en": "Stretch to window"},
    "option_yes": {"en": "Yes"},
    "option_no": {"en": "No"},
    "option_lang_auto": {"en": "Auto / provider default"},

    # ── Buttons / actions ─────────────────────────────────────────────────

    "btn_play": {"en": "Play"},
    "btn_stop": {"en": "Stop"},
    "btn_pause": {"en": "Pause"},
    "btn_settings": {"en": "Settings"},
    "btn_refresh": {"en": "Refresh"},
    "btn_epg_guide": {"en": "EPG Guide"},
    "epg_filter_channels": {"en": "Filter channels…"},
    "epg_select_channel": {"en": "Select a channel"},
    "epg_jump_now": {"en": "Now"},
    "epg_jump_playing": {"en": "Playing"},
    "epg_play_channel": {"en": "Play channel"},
    "epg_no_programme": {"en": "No current programme data"},
    "epg_now_prefix": {"en": "Now"},
    "epg_day_back": {"en": "1 day back"},
    "epg_day_fwd": {"en": "1 day forward"},
    "epg_tonight": {"en": "Tonight"},
    "epg_record_hint": {"en": "Right-click an upcoming or currently-airing programme to record it (multi-select works too)."},
    "epg_channels_count": {"en": "{n} channels"},
    "epg_channels_first": {"en": "(first {n})"},
    "btn_add": {"en": "Add…"},
    "btn_edit": {"en": "Edit…"},
    "btn_remove": {"en": "Remove"},
    "btn_export": {"en": "Export…"},
    "btn_import": {"en": "Import…"},
    "btn_close": {"en": "Close"},
    "btn_ok": {"en": "Ok"},
    "btn_cancel": {"en": "Cancel"},
    "btn_save": {"en": "Save"},
    "btn_search": {"en": "Search"},
    "btn_connect": {"en": "Connect"},
    "welcome_title": {"en": "Welcome to dopeIPTV"},
    "welcome_subtitle": {"en": "Connect your IPTV provider to load your channels, movies and series — or just look around first."},
    "welcome_connect": {"en": "Connect your provider"},
    "welcome_explore": {"en": "Continue without account"},
    "onb_try_demo": {"en": "🎬 Try demo channels"},
    "demo_notice": {"en": "Demo mode: a few free public test streams so you can try the app. They're third-party services, so playback isn't guaranteed. Add your own provider any time for the full experience."},
    "trakt_wizard_intro": {"en": "One-time setup: create a free Trakt API app, set its Redirect URI to exactly {url}, and paste its Client ID and Secret below. After that, Connect just opens your browser and you click Yes."},
    "demo_title": {"en": "dopeIPTV — Demo channels"},
    "onb_choose_language": {"en": "Choose your language"},
    "onb_next": {"en": "Next"},
    "onb_back": {"en": "Back"},
    "onb_skip": {"en": "Skip for now"},
    "onb_finish": {"en": "Finish"},
    "onb_features_title": {"en": "What's inside dopeIPTV"},
    "onb_feat_1": {"en": "Live TV, Movies & Series — fast and searchable"},
    "onb_feat_2": {"en": "Full EPG guide with now/next and catch-up"},
    "onb_feat_3": {"en": "Built-in video player — no external app needed"},
    "onb_feat_4": {"en": "Recording, Chromecast, Trakt sync, themes & more"},
    "onb_trakt_title": {"en": "Connect Trakt (optional)"},
    "onb_trakt_desc": {"en": "Sync your watched history and watchlist with Trakt.tv. This is completely optional — you can also do it later in Settings."},
    "onb_trakt_connect": {"en": "Sign in with Trakt"},
    "onb_trakt_connected": {"en": "✓ Trakt connected — your watch history will sync."},
    "onb_trakt_reconnect": {"en": "Reconnect Trakt"},
    "onb_finish_done": {"en": "Done — start watching"},
    "onb_limited_notice": {"en": "You're exploring without a provider, so the app is limited. Add one any time with the “+ Add provider” button in the middle."},
    "sort_global": {"en": "Global default"},
    "sort_scope_hint": {"en": "Sort order for this category. Pick “Global default” to follow the app-wide setting."},
    "tooltip_toggle_sidebar": {"en": "Collapse the sidebar to icons (Ctrl+B)"},
    "nav_set_color": {"en": "Set color…"},
    "nav_set_text_color": {"en": "Set text color…"},
    "nav_set_bg_color": {"en": "Set background color…"},
    "nav_reset_color": {"en": "Reset color"},
    "tooltip_jump_playing": {"en": "Go to what's playing now"},
    "toast_nothing_playing": {"en": "Nothing is playing right now"},
    "tooltip_hide_list": {"en": "Hide the list — focus the player (Ctrl+Shift+M)"},
    "tooltip_show_list": {"en": "Show the list again"},
    "tooltip_solo_category": {"en": "Collapse categories — show only the current one"},
    "tooltip_toggle_library": {"en": "Show or hide your library"},
    "rec_today": {"en": "Today"},
    "rec_yesterday": {"en": "Yesterday"},
    "rec_this_week": {"en": "This week"},
    "rec_earlier": {"en": "Earlier"},
    "fav_empty_all": {"en": "No favorites yet — right-click any channel, movie or series to add one."},
    "onb_fill_all": {"en": "Please fill in server, username and password."},
    "onb_xtream_link_hint": {"en": "Have an Xtream or M3U link? Paste it into the Server field below and we'll fill in the rest — or enter the details by hand."},
    "onb_add_provider": {"en": "+ Add provider"},
    "welcome_add_hint": {"en": "No provider yet — add one any time in Settings."},
    "btn_use": {"en": "Use"},
    "btn_watch": {"en": "Watch"},
    "btn_play_channel": {"en": "Play channel"},
    "btn_back_to_series": {"en": "Back to series"},
    "btn_clear_history": {"en": "Clear history"},
    "btn_grid": {"en": "Grid"},

    # ── Tooltips ───────────────────────────────────────────────────────────

    "tooltip_previous_channel": {"en": "Previous channel"},
    "tooltip_next_channel": {"en": "Next channel"},
    "tooltip_next_episode": {"en": "Next episode"},
    "tooltip_fullscreen": {"en": "Fullscreen"},
    "tooltip_exit_fullscreen": {"en": "Exit fullscreen"},
    "tooltip_mute_unmute": {"en": "Mute / unmute"},
    "tooltip_popout": {"en": "Pop out to a separate window"},
    "tooltip_popout_exit": {"en": "Return the player to the main window"},
    "popout_title": {"en": "dopeIPTV — Player"},
    "popout_placeholder": {"en": "▶  Playing in a separate window\nClick to bring it back"},
    "tooltip_stop_playback": {"en": "Stop playback"},
    "tooltip_pause_resume": {"en": "Pause / resume"},
    "tooltip_volume": {"en": "Volume"},
    "tooltip_record": {"en": "Record"},
    "tooltip_timeshift": {"en": "Timeshift / catch-up"},
    "tooltip_audio_subs_aspect": {"en": "Audio / subtitles / aspect / buffer"},
    "tooltip_reload_channels_epg": {"en": "Reload channels and EPG from server"},
    "tooltip_play_in_mpv": {"en": "Play in mpv"},
    "tooltip_back_10s": {"en": "Back 10 seconds"},
    "tooltip_forward_30s": {"en": "Forward 30 seconds"},

    # ── Status messages ───────────────────────────────────────────────────

    "status_loading_channels": {"en": "Loading channels…"},
    "status_loading_categories": {"en": "Loading categories…"},
    "status_loading_content": {"en": "Loading content…"},
    "status_loading_movies": {"en": "Loading movies…"},
    "status_loading_series": {"en": "Loading series…"},
    "status_loading_recent": {"en": "Loading recently added…"},
    "status_loading_episodes": {"en": "Loading episodes…"},
    "status_refreshing_playlist": {"en": "Refreshing playlist…"},
    "status_connecting": {"en": "Connecting to {name}…"},
    "status_loading_programme_guide": {"en": "Loading programme guide…"},
    "status_loading_programme_guide_pct": {"en": "Loading programme guide... {pct}%"},
    "status_playing": {"en": "Playing: {title}"},
    "chan_entry": {"en": "Channel: {num}"},
    "chan_not_found": {"en": "No channel #{num}"},
    "status_no_favorites": {"en": "No favorites yet - right-click a channel in TV to add one."},
    "status_no_history": {"en": "No watch history yet."},
    "status_reconnecting": {"en": "Reconnecting…"},
    "status_stream_dropped": {"en": "Live stream dropped — double-click to reconnect"},
    "update_status": {"en": "Update available ({version})"},
    "setting_deinterlace": {"en": "Deinterlace"},
    "setting_sharpen": {"en": "Sharpen"},
    "setting_tonemapping": {"en": "HDR tone-mapping"},
    "setting_hwdec": {"en": "Hardware decoding"},
    "setting_hwdec_hint": {"en": "Software (CPU) decoding is the default and handles even 4K fine. Turn on hardware decoding to offload the GPU; if video then goes black or glitches (some drivers, e.g. nvidia-open, with subtitles), switch it back off."},
    "option_hwdec_safe": {"en": "On - safe"},
    "option_hwdec_direct": {"en": "On - direct (zero-copy)"},
    "option_hwdec_off": {"en": "Off - software (CPU, recommended)"},
    "option_off": {"en": "Off"},
    "option_low": {"en": "Low"},
    "option_high": {"en": "High"},
    "option_tonemap_auto": {"en": "Auto"},
    "option_tonemap_clip": {"en": "Clip"},
    "option_on": {"en": "On"},
    "opt_video": {"en": "Video"},
    "sec_playback": {"en": "Playback"},
    "sec_audio_subs": {"en": "Audio & subtitles"},
    "sec_video": {"en": "Video"},
    "sec_network": {"en": "Network & timing"},
    "sec_guide": {"en": "Guide"},
    "epg_search_title": {"en": "Search the guide"},
    "epg_search_btn": {"en": "Search"},
    "epg_search_placeholder": {"en": "Search programmes (e.g. Formula 1)"},
    "epg_search_hint": {"en": "Type at least 2 characters to search this week's guide."},
    "epg_searching": {"en": "Searching…"},
    "epg_search_count": {"en": "{n} matches"},
    "epg_search_none": {"en": "No matches this week."},
    "status_stream_error": {"en": "Stream error: {msg}"},
    "status_checking_stream": {"en": "Stream failed — checking why…"},
    "diag_account_status": {"en": "Your account is {status} — contact your provider"},
    "diag_expired": {"en": "Your subscription has expired — renew it with your provider"},
    "diag_conn_limit": {"en": "All {active}/{maxc} connections are in use — close the stream on your other device or app"},
    "diag_timeout": {"en": "The provider didn't respond (timeout) — its server is likely down or overloaded"},
    "diag_unreachable": {"en": "Can't reach the provider — its server is down or there's a network problem"},
    "diag_forbidden": {"en": "The provider refused the stream (HTTP {code}) — account blocked, or too many connections"},
    "diag_not_found": {"en": "The provider doesn't have this stream (HTTP 404) — try refreshing the playlist"},
    "set_upcoming_prompt": {"en": "Ask to remind/record when a channel isn't live yet"},
    "upcoming_title": {"en": "Upcoming broadcast"},
    "upcoming_body": {"en": "“{channel}” isn't live yet. Set a reminder, or record it when it starts?"},
    "upcoming_remind": {"en": "Remind me when it starts"},
    "upcoming_record_stop": {"en": "Record until I stop it"},
    "upcoming_schedule": {"en": "Schedule a recording (choose time)…"},
    "upcoming_no_epg": {"en": "No schedule info for this channel to set a reminder from."},
    "diag_not_started": {"en": "This stream hasn't started yet — it looks like an upcoming event. Try again once it's live."},
    "diag_blocked": {"en": "The provider blocked this stream (HTTP {code}) — usually anti-VPN/re-streaming, a connection limit, or a region/device block. Turn off any VPN and check with your provider."},
    "diag_http_error": {"en": "The provider returned an error (HTTP {code}) — its server is having trouble"},
    "diag_http_ok_no_play": {"en": "The provider is serving the stream but it wouldn't play — likely an unsupported format for this channel"},
    "diag_generic": {"en": "The stream couldn't be reached — the provider may be down"},
    "status_player_not_found": {"en": "Player not found"},
    "status_player_not_found_msg": {"en": "{name} was not found. Install it and try again."},
    "embedded_gl_failed": {"en": "In-app video isn't available on this system's graphics (often a virtual machine without GPU acceleration). You can still open channels in an external player."},

    # ── Search ────────────────────────────────────────────────────────────

    "search_placeholder": {"en": "Search channels, movies or series…"},
    "search_filter_channels": {"en": "Filter channels…"},

    # ── Detail panel / metadata labels ────────────────────────────────────

    "detail_genre": {"en": "Genre"},
    "detail_director": {"en": "Director"},
    "detail_released": {"en": "Released"},
    "detail_duration": {"en": "Duration"},
    "detail_rating": {"en": "Rating"},
    "detail_cast": {"en": "Cast"},
    "detail_no_info": {"en": "No further information available."},
    "detail_loading_info": {"en": "Loading information…"},
    "detail_select_something": {"en": "Select something from the list"},
    "detail_select_channel": {"en": "Select a channel"},

    # ── Recording ─────────────────────────────────────────────────────────

    "setting_check_updates": {"en": "Check for updates on startup"},
    "setting_force_x11": {"en": "Run via X11 backend (needs restart)"},
    "setting_force_x11_hint": {"en": "Wayland only: run under XWayland so the pop-out player window can stay always-on-top. May look slightly softer on fractional HiDPI scaling."},
    "ext_play_title": {"en": "Open external player?"},
    "ext_play_body": {"en": "Something is playing in the mini player. Opening an external player pulls a second stream from the provider, which many accounts don't allow. Stop the mini player first?"},
    "ext_play_stop_open": {"en": "Stop and open externally"},
    "ext_play_keep_open": {"en": "Open anyway (2nd connection)"},
    "rec_record_programme": {"en": "Record this programme"},
    "rec_record": {"en": "Record"},
    "rec_stop_recording": {"en": "Stop recording"},
    "rec_all_recordings": {"en": "All recordings"},
    "rec_active_scheduled": {"en": "Active & scheduled"},
    "rec_upcoming": {"en": "Upcoming"},
    "rec_status_recording": {"en": "Recording"},
    "rec_status_scheduled": {"en": "Scheduled"},
    "rec_status_done": {"en": "Done"},
    "rec_status_failed": {"en": "Failed"},
    "rec_status_cancelled": {"en": "Cancelled"},
    "rec_record_now_until_stopped": {"en": "Record now - until stopped"},
    "rec_record_now_duration": {"en": "Record now - {duration}"},
    "rec_schedule_recording": {"en": "Schedule recording…"},
    "rec_open_recordings": {"en": "Open Recordings"},
    "rec_recording_n_streams": {"en": "Recording {n} stream(s)…"},

    # ── Cast popup ────────────────────────────────────────────────────────

    "cast_other_titles": {"en": "other titles in your playlist"},
    "cast_looking_up": {"en": "Looking up filmography…"},
    "cast_searching_playlist": {"en": "Searching your playlist…"},
    "cast_no_matches": {"en": "No other titles from this playlist matched."},
    "cast_titles_found": {"en": "{count} title(s) found in your playlist"},
    "cast_double_click": {"en": "double-click to open"},
    "cast_find_other_titles": {"en": "Find other titles with {name} in your playlist"},
    "cast_looking_up_member": {"en": "Looking up cast member…"},

    # ── About / menu ──────────────────────────────────────────────────────

    "about_desc": {"en": "An elegant IPTV client for Xtream Codes and M3U playlists - with EPG, embedded mpv/VLC playback, favorites, recordings and Trakt sync."},
    "about_website": {"en": "Website"},
    "about_github": {"en": "GitHub"},
    "about_all_releases": {"en": "All releases"},
    "about_checking": {"en": "Checking for updates…"},
    "about_up_to_date": {"en": "You're on the latest version."},
    "about_update_available": {"en": "A new version is available: {version}"},
    "about_check_failed": {"en": "Couldn't check for updates right now."},
    "about_download": {"en": "Download the update"},
    "about_check_again": {"en": "Check again"},
    "about_check_updates": {"en": "Check for updates"},
    "about_tmdb_credit": {"en": "Movie and TV metadata and artwork provided by TMDB. This product uses the TMDB API but is not endorsed or certified by TMDB."},
    "menu_about": {"en": "About dopeIPTV"},
    "menu_quit": {"en": "Quit"},
    "menu_refresh_playlist": {"en": "Refresh playlist"},
    "menu_multiview": {"en": "Multiview"},
    "menu_playlists": {"en": "Playlists"},
    "dont_show_again": {"en": "Don't show this again"},
    "mv_info_title": {"en": "About multiview"},
    "mv_info_body": {"en": "Each window is a separate stream, so each uses one connection to your provider. To watch several at once your account needs enough simultaneous connections — e.g. 4 for a full grid. Windows beyond your account's limit will be refused by the provider."},

    # ── Labels for item counts ────────────────────────────────────────────

    "label_channels": {"en": "channels"},
    "label_movies": {"en": "movies"},
    "label_series": {"en": "series"},
    "label_episodes": {"en": "episodes"},
    "label_favorites": {"en": "favorites"},
    "label_history_items": {"en": "history items"},
    "label_recordings": {"en": "recordings"},
    "label_all": {"en": "All"},
    "label_size": {"en": "Size"},
    "label_sort": {"en": "Sort"},
    "label_default": {"en": "Default"},
    "label_recent": {"en": "Recent"},

    # ── Login dialog ──────────────────────────────────────────────────────

    "login_title": {"en": "Connect to an Xtream server"},
    "login_subtitle": {"en": "Sign in with your Xtream Codes credentials."},
    "login_server": {"en": "Server"},
    "login_username": {"en": "Username"},
    "login_password": {"en": "Password"},

    # ── Playlist dialog ───────────────────────────────────────────────────

    "playlist_add_title": {"en": "Add playlist"},
    "playlist_edit_title": {"en": "Edit playlist"},
    "playlist_kind": {"en": "Type"},
    "playlist_kind_xtream": {"en": "Xtream Codes (server + login)"},
    "playlist_kind_m3u": {"en": "M3U playlist (URL)"},
    "playlist_m3u_url": {"en": "M3U URL"},
    "playlist_name": {"en": "Name"},
    "playlist_name_hint": {"en": "e.g. Home, Sports (optional)"},
    "playlist_custom_epg_url": {"en": "Custom TV guide URL"},
    "playlist_auto_refresh": {"en": "Auto-refresh"},
    "playlist_required_fields": {"en": "Server, username and password are required."},

    # ── EPG guide ─────────────────────────────────────────────────────────

    "epg_now": {"en": "Now"},
    "epg_upcoming": {"en": "Upcoming"},
    "epg_earlier_today": {"en": "Earlier today"},
    "epg_no_current_data": {"en": "No current programme data"},
    "epg_no_guide_available": {"en": "No programme guide available for this channel."},
    "epg_could_not_load": {"en": "Could not load the programme guide."},

    # ── Context menu ──────────────────────────────────────────────────────

    "ctx_play_in_mpv": {"en": "Play in mpv"},
    "mv_add": {"en": "Add to multiview"},
    "mv_mute": {"en": "Mute"},
    "mv_unmute": {"en": "Unmute"},
    "mv_move": {"en": "Move / swap with"},
    "mv_close": {"en": "Close multiview"},
    "mv_remove_cell": {"en": "Remove from this window"},
    "mv_cell": {"en": "Window {n}"},
    "mv_live": {"en": "LIVE"},
    "mv_pause": {"en": "Pause / play"},
    "mv_conflict_title": {"en": "Multiview is running"},
    "mv_conflict_body": {"en": "Multiview is still streaming and holds connections to your provider. Close multiview and play here? If your account has enough simultaneous connections you can keep both."},
    "mv_keep": {"en": "Keep multiview"},
    "tab_multiview": {"en": "Multiview"},
    "mv_sec_window": {"en": "WINDOW"},
    "mv_sec_behavior": {"en": "BEHAVIOR"},
    "mv_sec_controls": {"en": "CONTROLS"},
    "mv_set_titlebar": {"en": "Show title bar"},
    "mv_set_on_top": {"en": "Always on top"},
    "mv_set_remember_geo": {"en": "Remember window size and position"},
    "mv_set_stop_docked": {"en": "Stop the main player when sending to multiview"},
    "mv_set_cells": {"en": "Number of windows"},
    "mv_set_new_unmuted": {"en": "Newly added channel takes audio focus"},
    "mv_set_conflict": {"en": "When playing here while multiview runs"},
    "mv_conflict_ask": {"en": "Ask every time"},
    "mv_set_autohide": {"en": "Auto-hide controls after (seconds)"},
    "mv_set_seek_step": {"en": "Arrow-key timeshift step (minutes)"},
    "mv_set_reset_info": {"en": "Show the multiview info notice again"},
    "mv_title": {"en": "dopeIPTV — Multiview"},
    "mv_empty_cell": {"en": "Right-click a channel → Add to multiview"},
    "mv_cell_error": {"en": "{title} — stream failed"},
    "ctx_open_externally": {"en": "Open externally"},
    "ctx_cast_to_chromecast": {"en": "Cast to Chromecast…"},
    "ctx_add_to_favorites": {"en": "Add to favorites"},
    "ctx_add_to_folder": {"en": "Add to folder"},
    "ctx_rename_folder": {"en": "Rename folder…"},
    "ctx_remove_folder": {"en": "Remove folder “{group}”"},
    "prompt_folder_name": {"en": "Folder name:"},
    "ctx_new_group": {"en": "New group…"},
    "ctx_remove_from_favorites": {"en": "Remove from favorites"},
    "ctx_rename_channel": {"en": "Rename channel…"},
    "ctx_rename": {"en": "Rename…"},
    "ctx_hide_channel": {"en": "Hide channel"},
    "ctx_hide": {"en": "Hide"},
    "ctx_copy_stream_url": {"en": "Copy stream URL"},
    "ctx_remove_from_history": {"en": "Remove selected from history"},
    "ctx_manage_categories": {"en": "Manage categories…"},
    "ctx_delete": {"en": "Delete"},
    "ctx_new_folder": {"en": "New folder…"},
    "ctx_move_to": {"en": "Move to"},

    # ── Category management dialog ────────────────────────────────────────

    "cat_manage_title": {"en": "Manage categories"},
    "cat_rename": {"en": "Rename…"},
    "cat_hide": {"en": "Hide"},
    "cat_unhide": {"en": "Unhide"},
    "cat_lock": {"en": "Lock"},
    "cat_unlock": {"en": "Unlock"},

    # ── Parental control ──────────────────────────────────────────────────

    "parental_enter_pin": {"en": "Enter PIN:"},
    "parental_wrong_pin": {"en": "Wrong PIN."},
    "parental_no_pin_set": {"en": "No PIN set."},
    "parental_set_change_pin": {"en": "Set / change PIN…"},
    "parental_remove_pin": {"en": "Remove PIN"},
    "parental_lock_now": {"en": "Lock now"},
    "parental_control": {"en": "Parental control"},

    # ── Confirmation / message dialogs ────────────────────────────────────

    "confirm_clear_history": {"en": "Remove all watch history?"},
    "confirm_delete_recording": {"en": "Delete {what} from disk?"},
    "confirm_remove_playlist": {"en": "Remove this playlist? Its favorites and history are kept until you re-add and clear them."},
    "confirm_restore_channels": {"en": "Undo all channel renames and hides for this section and go back to the provider's original list?"},

    # ── Options menu (embedded player) ────────────────────────────────────

    "opt_audio_track": {"en": "Audio track"},
    "opt_subtitles": {"en": "Subtitles"},
    "opt_audio_delay": {"en": "Audio delay"},
    "opt_aspect_ratio": {"en": "Aspect ratio"},
    "opt_network_buffer": {"en": "Network buffer"},
    "opt_sleep_timer": {"en": "Sleep timer"},
    "opt_minutes": {"en": "{n} min"},
    "opt_sleep_custom": {"en": "Custom…"},
    "sleep_prompt": {"en": "Stop playback after (minutes):"},
    "sleep_set": {"en": "Sleep timer: stopping in {n} min"},
    "sleep_cancelled": {"en": "Sleep timer cancelled"},
    "sleep_stopping": {"en": "Sleep timer: stopping playback"},
    "opt_stats_for_nerds": {"en": "Stats for nerds"},

    # ── Timeshift / catch-up ──────────────────────────────────────────────

    "epg_play_this_programme": {"en": "Play this programme (catch-up)"},
    "ts_play_from_start": {"en": "Play from start (catch-up)"},
    "ts_go_live": {"en": "Go Live"},
    "ts_watch_from_start": {"en": "Watch '{title}' from the start"},
    "ts_browse_past": {"en": "Browse past programmes (EPG)…"},
    "ts_go_back_30m": {"en": "Go back 30 minutes"},
    "ts_go_back_1h": {"en": "Go back 1 hour"},
    "ts_go_back_2h": {"en": "Go back 2 hours"},
    "ts_go_back_6h": {"en": "Go back 6 hours"},
    "ts_go_back_12h": {"en": "Go back 12 hours"},
    "ts_go_back_1d": {"en": "Go back 1 day"},
    "ts_go_back_2d": {"en": "Go back 2 days"},
    "ts_go_back_3d": {"en": "Go back 3 days"},
    "ts_go_back_5d": {"en": "Go back 5 days"},
    "ts_go_back_7d": {"en": "Go back 7 days"},

    # ── Metadata tab ──────────────────────────────────────────────────────

    "meta_artwork_source": {"en": "Artwork source"},
    "meta_playlist_artwork": {"en": "Playlist (provider artwork)"},
    "meta_tmdb_artwork": {"en": "TMDB (fetch posters by title)"},
    "meta_tmdb_api_key": {"en": "TMDB API key"},

    # ── About dialog ──────────────────────────────────────────────────────

    "about_description": {"en": "An elegant IPTV client for Xtream Codes with EPG, embedded playback, favorites and history."},
    "about_playback_via": {"en": "Playback via mpv (embedded/window) or VLC."},

    # ── Refresh options ───────────────────────────────────────────────────

    "refresh_never": {"en": "Never"},
    "refresh_at_startup": {"en": "At startup"},
    "refresh_every_2h": {"en": "Every 2 hours"},
    "refresh_every_6h": {"en": "Every 6 hours"},
    "refresh_every_12h": {"en": "Every 12 hours"},
    "refresh_daily": {"en": "Daily"},
    "refresh_weekly": {"en": "Weekly"},

    # ── Misc / various ────────────────────────────────────────────────────

    "misc_movie": {"en": "Movie"},
    "misc_series_singular": {"en": "Series"},
    "misc_episode": {"en": "Episode"},
    "misc_recordings_saved_in": {"en": "Recordings are saved in:"},
    "misc_choose_folder": {"en": "Choose folder…"},
    "misc_no_audio_tracks": {"en": "(no audio tracks)"},
    "misc_error": {"en": "Error: {msg}"},
    "misc_view_on_imdb": {"en": "View on IMDb"},
    "misc_loading": {"en": "Loading…"},
    "misc_connected_to_trakt": {"en": "Connected to Trakt."},
    "misc_not_connected": {"en": "Not connected."},
    "misc_connect_to_trakt": {"en": "Connect to Trakt…"},
    "misc_disconnect": {"en": "Disconnect"},
    "misc_watchlist_history": {"en": "Watchlist / History…"},
    "misc_connect_first": {"en": "Connect to Trakt first."},
    "misc_stop_recording_at": {"en": "Stop a recording when the file reaches"},
    "misc_theme_applies_immediately": {"en": "Theme and accent apply immediately."},
    "misc_language_restart": {"en": "The menus update now. Restart dopeIPTV to translate every part of the app."},
    "popout_always_on_top": {"en": "Always on top"},
    "popout_autohide_controls": {"en": "Auto-hide controls"},
    "popout_hide_titlebar": {"en": "Hide title bar"},
    "popout_show_titlebar": {"en": "Show title bar"},
    "popout_wayland_hint": {"en": "Always on top: right-click the title bar (Wayland)"},
    "resume_title": {"en": "Resume playback"},
    "resume_prompt": {"en": "You stopped watching at {time}."},
    "resume_continue": {"en": "Resume from {time}"},
    "resume_restart": {"en": "Start from the beginning"},
    "theme_graphite": {"en": "Graphite (default)"},
    "theme_midnight": {"en": "Midnight (blue)"},
    "theme_oled": {"en": "OLED (pure black)"},
    "theme_nord": {"en": "Nord"},
    "theme_dracula": {"en": "Dracula"},
    "theme_gruvbox": {"en": "Gruvbox (dark)"},
    "theme_solarized": {"en": "Solarized (dark)"},
    "theme_catppuccin": {"en": "Catppuccin (mocha)"},
    "theme_light": {"en": "Light"},
    "accent_blue": {"en": "Blue"},
    "accent_purple": {"en": "Purple"},
    "accent_teal": {"en": "Teal"},
    "accent_green": {"en": "Green"},
    "accent_orange": {"en": "Orange"},
    "accent_pink": {"en": "Pink"},
    "accent_red": {"en": "Red"},
    "lang_swe": {"en": "Swedish"},
    "lang_eng": {"en": "English"},
    "lang_nor": {"en": "Norwegian"},
    "lang_dan": {"en": "Danish"},
    "lang_fin": {"en": "Finnish"},
    "lang_ger": {"en": "German"},
    "lang_fre": {"en": "French"},
    "lang_spa": {"en": "Spanish"},
    "lang_ita": {"en": "Italian"},
    "lang_por": {"en": "Portuguese"},
    "lang_pol": {"en": "Polish"},
    "lang_ara": {"en": "Arabic"},
    "lang_tur": {"en": "Turkish"},
    "misc_no_playlists_export": {"en": "No playlists to export."},
    "misc_exported_n_playlists": {"en": "Exported {count} playlist(s) to:\n{path}"},
    "misc_imported_n_playlists": {"en": "Imported {count} playlist(s)."},
    "misc_recording_stopped": {"en": "Recording stopped: {title} ({reason})"},
    "misc_for_linux": {"en": "for Linux"},

    # ══════════════════════════════════════════════════════════════════════
    # To add a new UI string: add a key below with a line per language, then
    # call tr("your_key") in the code. To add a whole new *language*: add its
    # code to LANGUAGES above and a matching entry to every key here.
    # ══════════════════════════════════════════════════════════════════════

    # ── Player options menu (the ⚙ button) ───────────────────────────────
    "opt_off": {"en": "Off"},
    "opt_no_audio_tracks": {"en": "(no audio tracks)"},
    "opt_delay_default": {"en": "0 s (default)"},
    "opt_aspect_auto": {"en": "Auto"},
    "opt_aspect_stretch": {"en": "Stretch to window"},

    # ── Common buttons reused in several dialogs ──────────────────────────
    "common_close": {"en": "Close"},
    "common_cancel": {"en": "Cancel"},
    "common_dismiss": {"en": "Dismiss"},
    "reminder_add": {"en": "Remind me when it starts"},
    "reminder_remove": {"en": "Remove reminder"},
    "reminder_set": {"en": "Reminder set for {title}"},
    "reminder_now_title": {"en": "Programme starting"},
    "reminder_now_body": {"en": "{title} is starting on {channel}."},
    "reminder_watch_now": {"en": "Watch now"},
    "common_loading": {"en": "Loading…"},
    "rec_switch_title": {"en": "Record another channel?"},
    "rec_switch_body": {"en": "“{playing}” is playing. Recording “{target}” instead needs a second connection to your provider - many accounts allow only one at a time."},
    "rec_switch_and_record": {"en": "Switch to it and record"},
    "rec_record_background": {"en": "Record in the background (needs a second connection)"},

    # ── Chromecast dialog ─────────────────────────────────────────────────
    "cast_title": {"en": "Cast to Chromecast"},
    "cast_scanning": {"en": "Scanning for Chromecast devices…"},
    "cast_rescan": {"en": "Rescan"},
    "cast_cast": {"en": "Cast"},
    "cast_stop": {"en": "Stop casting"},
    "cast_devices_found": {"en": "{n} device(s) found."},
    "cast_none_found": {"en": "No Chromecast devices found on this network."},
    "cast_scan_failed": {"en": "Scan failed: {msg}"},
    "cast_starting": {"en": "Starting cast to {name}…"},
    "cast_casting_to": {"en": "Casting to {name}."},
    "cast_failed": {"en": "Cast failed: {msg}"},
    "cast_stopped": {"en": "Casting stopped."},
    "cast_stop_failed": {"en": "Stop failed: {msg}"},

    # ── Playlist dialog extras ────────────────────────────────────────────
    "playlist_msg_title": {"en": "Playlist"},
    "playlist_name_placeholder": {"en": "e.g. My provider"},
    "playlist_epg_placeholder": {"en": "optional - overrides the provider's xmltv.php"},

    # ── Recording scheduling messages (EPG guide) ─────────────────────────
    "rec_msg_title": {"en": "Record"},
    "rec_scheduled_status": {"en": "Scheduled {n} recording(s) - see Recordings → Upcoming"},
    "rec_skipped_warning": {"en": "{n} programme(s) could not be scheduled: missing channel stream id."},

    # ── Content manager dialog ────────────────────────────────────────────
    "cm_title": {"en": "Manage categories"},
    "cm_hint": {"en": "Hidden categories disappear from the sidebar and their channels are left out of 'All'. Locked categories need the parental PIN to open."},
    "cm_rename": {"en": "Rename…"},
    "cm_hide": {"en": "Hide"},
    "cm_unhide": {"en": "Unhide"},
    "cm_lock": {"en": "Lock"},
    "cm_unlock": {"en": "Unlock"},
    "cm_flag_hidden": {"en": "hidden"},
    "cm_flag_locked": {"en": "locked"},
    "cm_rename_title": {"en": "Rename category"},
    "cm_new_name": {"en": "New name:"},
    "pin_new_prompt": {"en": "New PIN:"},
    "pin_choose_prompt": {"en": "No PIN is set yet. Choose a PIN to protect locked content:"},
    "set_cat_icon_title": {"en": "Set category icon"},
    "set_cat_icon_prompt": {"en": "Enter an emoji or short text (leave blank to remove):"},
    "size_value_placeholder": {"en": "e.g. 75"},

    # ── Channel / category context menus (extras) ────────────────────────
    "ctx_reset_color": {"en": "Reset color"},
    "ctx_reset_channel": {"en": "Reset this channel's customizations"},
    "ctx_restore_defaults": {"en": "Restore default channels…"},
    "settings_image_cache_label": {"en": "Image cache on disk: {size}"},
    "settings_image_cache_clear": {"en": "Clear image cache"},
    "settings_image_cache_hint": {"en": "Cached posters and channel logos on disk. Cleared covers reload from the network on next scroll."},
    "ctx_mark_watched": {"en": "Mark as watched (local)"},
    "ctx_mark_watched_trakt": {"en": "Mark as watched + Trakt"},
    "ctx_unmark_watched": {"en": "Unmark as watched (local)"},
    "ctx_unmark_watched_trakt": {"en": "Unmark as watched + Trakt"},
    "ctx_watchlist_add": {"en": "Add to Watch Later (local)"},
    "ctx_watchlist_add_trakt": {"en": "Add to Watch Later + Trakt"},
    "ctx_watchlist_remove": {"en": "Remove from Watch Later (local)"},
    "ctx_watchlist_remove_trakt": {"en": "Remove from Watch Later + Trakt"},
    "ctx_match_tmdb": {"en": "Match on TMDB…"},
    "tmdb_match_title": {"en": "Find on TMDB"},
    "tmdb_match_hint": {"en": "Search TMDB and pick the right poster/metadata. Your choice is remembered and overrides the automatic match."},
    "tmdb_search_placeholder": {"en": "Title"},
    "tmdb_year_placeholder": {"en": "Year"},
    "tmdb_search_btn": {"en": "Search"},
    "tmdb_enter_title": {"en": "Enter a title to search."},
    "tmdb_searching": {"en": "Searching…"},
    "tmdb_search_failed": {"en": "Search failed: {msg}"},
    "tmdb_no_results": {"en": "No matches found."},
    "tmdb_n_matches": {"en": "{n} matches"},
    "tmdb_use_this": {"en": "Use this"},
    "tmdb_clear_override": {"en": "Clear override"},
    "ctx_remove_group": {"en": "Remove group \"{group}\""},
    "ctx_unlock_group": {"en": "Unlock group (remove protection)"},
    "ctx_lock_group": {"en": "Lock group (parental control)"},
    "ctx_rename_category": {"en": "Rename category…"},
    "ctx_set_icon": {"en": "Set icon…"},
    "ctx_set_color": {"en": "Set color"},
    "ctx_set_bg_color": {"en": "Set background"},
    "color_default": {"en": "Default"},
    "ctx_hide_category": {"en": "Hide category"},
    "ctx_unlock_category": {"en": "Unlock category (remove protection)"},
    "ctx_lock_category": {"en": "Lock category (parental control)"},
    "ctx_play_in_vlc": {"en": "Play in VLC"},
    "cat_all": {"en": "All"},
    "cat_continue": {"en": "Continue watching"},
    "cat_recent": {"en": "Recently added"},
    "ctx_continue_remove": {"en": "Remove from Continue watching"},

    # ── Durations (reused by record + timeshift menus) ────────────────────
    "dur_30min": {"en": "30 min"},
    "dur_1h": {"en": "1 hour"},
    "dur_2h": {"en": "2 hours"},
    "dur_4h": {"en": "4 hours"},
    "dur_6h": {"en": "6 hours"},
    "dur_12h": {"en": "12 hours"},
    "dur_1d": {"en": "1 day"},
    "dur_2d": {"en": "2 days"},
    "dur_3d": {"en": "3 days"},
    "dur_5d": {"en": "5 days"},
    "dur_7d": {"en": "7 days"},

    # ── Recording menu / recordings context menu (extras) ─────────────────
    "rec_size_limit_session": {"en": "Size limit (this session)"},
    "rec_stop_named_since": {"en": "Stop recording: {title} (since {since})"},
    "rec_edit_times": {"en": "Edit start/stop time…"},
    "rec_cancel_scheduled": {"en": "Cancel scheduled recording"},
    "rec_remove_from_list": {"en": "Remove selected from list"},
    "rec_clear_finished": {"en": "Clear all finished from list"},
    "rec_move_n": {"en": "Move {n} recordings to"},
    "rec_move_root": {"en": "(Recordings folder)"},
    "rec_delete_n": {"en": "Delete {n} recordings"},
    "rec_delete_all": {"en": "Delete all recordings here…"},
    "rec_n_recordings": {"en": "{n} recordings"},
    "rec_change_folder": {"en": "Change recordings folder…"},
    "rec_saved_in": {"en": "Recordings are saved in:"},
    "rec_custom_size_title": {"en": "Custom size limit"},
    "rec_stop_recording_at": {"en": "Stop recording at"},

    # ── Timeshift menu (extras) ───────────────────────────────────────────
    "ts_go_back": {"en": "Go back {t}"},
    "ts_watch_from_start_named": {"en": "Watch '{title}' from the start"},
    "ts_archive_depth": {"en": "Archive depth: {n} day(s)"},
    "ts_catchup_title": {"en": "Catch-up - {name}"},
    "ts_loading_past": {"en": "Loading past programmes from the guide…"},

    # ── Common ────────────────────────────────────────────────────────────
    "common_watch": {"en": "Watch"},
    "btn_test": {"en": "Test"},
    "btn_choose_folder": {"en": "Choose folder…"},
    "misc_series": {"en": "Series"},

    # ── Form field labels ─────────────────────────────────────────────────
    "field_start": {"en": "Start"},
    "field_stop": {"en": "Stop"},
    "field_save_in": {"en": "Save in"},
    "field_title": {"en": "Title"},
    "field_client_id": {"en": "Client ID"},
    "field_client_secret": {"en": "Client Secret"},

    # ── Settings extras ───────────────────────────────────────────────────
    "settings_export_tip": {"en": "Export all playlists to a JSON file"},
    "settings_import_tip": {"en": "Import playlists from a JSON file"},
    "setting_artwork_source": {"en": "Artwork source"},
    "setting_tmdb_key": {"en": "TMDB API key"},
    "tmdb_key_placeholder": {"en": "TMDB API key (v3 auth)"},
    "meta_src_builtin": {"en": "TMDB (built-in, recommended)"},
    "meta_src_own": {"en": "TMDB (my own key)"},
    "meta_src_provider": {"en": "Provider artwork"},
    "tmdb_enter_key": {"en": "Enter an API key first."},
    "tmdb_checking": {"en": "Checking…"},
    "tmdb_key_works": {"en": "Key works."},
    "tmdb_key_failed": {"en": "Key check failed: {msg}"},
    "pin_set_change": {"en": "Set / change PIN…"},
    "pin_remove": {"en": "Remove PIN"},
    "pin_lock_now": {"en": "Lock now"},
    "pin_none_set": {"en": "No PIN set."},
    "pl_mgmt_unavailable": {"en": "Playlist management unavailable"},

    # ── Message boxes ─────────────────────────────────────────────────────
    "msg_could_not_connect": {"en": "Could not connect to {name}: {msg}"},
    "msg_connect_trakt_first": {"en": "Connect to Trakt first."},
    "msg_cast_needs_package": {"en": "Casting needs the pychromecast package:\n\n  pip install pychromecast"},
    "msg_restore_defaults_body": {"en": "Undo all channel renames and hides for this section and go back to the provider's original list?"},
    "msg_parental_title": {"en": "Parental control"},
    "msg_wrong_pin": {"en": "Wrong PIN."},
    "msg_rec_file_not_ready": {"en": "The recording file hasn't been created yet - try again in a few seconds."},
    "msg_rec_needs_ffmpeg": {"en": "Recording needs ffmpeg (recommended) or mpv on the PATH.\n\nInstall ffmpeg, e.g.:  sudo apt install ffmpeg"},
    "msg_stop_time_future": {"en": "The stop time must be in the future and after the start time."},
    "msg_edit_time_title": {"en": "Edit recording time"},
    "msg_stop_after_start": {"en": "The stop time must be after the start time."},
    "msg_delete_rec_title": {"en": "Delete recording"},
    "msg_delete_rec_body": {"en": "Delete {what} from disk?"},
    "msg_clear_history_title": {"en": "Clear history"},
    "msg_clear_history_body": {"en": "Remove all watch history?"},
    "msg_rename_rec_title": {"en": "Rename recording"},
    "msg_move_rec_title": {"en": "Move recording"},
    "msg_new_folder_title": {"en": "New folder"},
    "msg_folder_name": {"en": "Folder name:"},
    "msg_trakt_enter_creds": {"en": "Enter a Client ID and Client Secret first."},
    "msg_remove_playlist_title": {"en": "Remove playlist"},
    "msg_remove_playlist_body": {"en": "Remove this playlist? Its favorites and history are kept until you re-add and clear them."},

    # ── Trakt dialogs ─────────────────────────────────────────────────────
    "trakt_connect_title": {"en": "Connect to Trakt"},
    "trakt_browser_opening": {"en": "Opening your browser… approve dopeIPTV on the Trakt page that appears, then come back here. Already signed in to Trakt on the web? Just click 'Yes'."},
    "trakt_finishing": {"en": "Approved - finishing sign-in…"},
    "trakt_timed_out": {"en": "Timed out waiting for approval. Close and try again."},
    "trakt_denied": {"en": "Sign-in was declined in the browser."},
    "trakt_port_busy": {"en": "Couldn't open the local sign-in port ({port}). Close whatever is using it and try again."},
    "trakt_use_code_instead": {"en": "Use a code instead"},
    "trakt_requesting_code": {"en": "Requesting a device code…"},
    "trakt_code_expired": {"en": "Code expired - try again."},
    "trakt_connected_excl": {"en": "Connected to Trakt!"},
    "trakt_login_failed": {"en": "Trakt login failed: {msg}"},
    "trakt_enter_code": {"en": "Go to <b>{url}</b> and enter this code:<br><br><span style='font-size:20px; font-weight:700;'>{code}</span>"},
    "trakt_could_not_start": {"en": "Could not start Trakt login: {msg}"},
    "trakt_watchlist_title": {"en": "Trakt Watchlist & History"},
    "trakt_tab_watchlist": {"en": "Watchlist"},
    "trakt_load_failed": {"en": "Could not load Trakt data: {msg}"},
    "trakt_create_app": {"en": "Create a free Trakt app…"},
    "trakt_client_id_ph": {"en": "Client ID (from the app you created)"},
    "trakt_client_secret_ph": {"en": "Client Secret"},
    "trakt_connect_btn": {"en": "Connect to Trakt…"},
    "trakt_connect_browser": {"en": "Connect via browser"},
    "trakt_save_creds": {"en": "Save Client ID & Secret"},
    "trakt_creds_saved": {"en": "Saved. Now click 'Connect via browser' above to sign in."},
    "trakt_connect_browser_hint": {"en": "The easy way: uses dopeIPTV's built-in Trakt app. Your browser opens Trakt, you click 'Yes', and you're signed in - no codes."},
    "trakt_creds_hint": {"en": "Advanced: use your own Trakt API app. Paste its Client ID and Secret, Save them, then use 'Connect via browser' above (Trakt always confirms sign-in in the browser)."},
    "trakt_disconnect": {"en": "Disconnect"},
    "trakt_watchlist_btn": {"en": "Watchlist / History…"},
    "mark_needs_tmdb": {"en": "TMDB metadata hasn't resolved for this title yet — try again in a few seconds, or use 'Match on TMDB…' to pick one manually."},
    "trakt_sync_now": {"en": "Sync now"},
    "trakt_syncing": {"en": "Syncing watched history…"},
    "btn_sync_now": {"en": "Sync now (Trakt)"},
    "trakt_sync_never": {"en": "Watched history: not synced yet."},
    "trakt_sync_status": {"en": "Synced {when} — {movies} movies, {episodes} episodes."},
    "trakt_sync_hint": {"en": "Marks movies and episodes you've watched on any device (mobile, browser, other players) with a check-badge in the list. Auto-syncs on startup at most once an hour."},
    "trakt_connected": {"en": "Connected to Trakt."},
    "trakt_not_connected": {"en": "Not connected."},

    # ── Cast/actor filmography panel ──────────────────────────────────────
    "actor_other_titles": {"en": "{name} — other titles in your playlist"},
    "actor_lookup_filmography": {"en": "Looking up filmography…"},
    "actor_searching_playlist": {"en": "Searching your playlist…"},
    "actor_no_matches": {"en": "No other titles from this playlist matched."},
    "actor_matches_found": {"en": "{n} title(s) found in your playlist (double-click to open):"},
    "actor_not_found_tmdb": {"en": "Couldn't find {name} on TMDB."},
    "actor_lookup_member": {"en": "Looking up cast member…"},
    "ph_no_limit": {"en": "no limit"},
    "rec_total_label": {"en": "Total recordings folder limit"},
    "rec_cap_title": {"en": "Storage limit reached"},
    "rec_cap_reached": {"en": "Can't start a new recording: the recordings folder is at your limit ({used} of {cap}). Delete some recordings or raise the limit in Settings."},

    # ── Settings: reset-to-defaults ───────────────────────────────────────
    "settings_reset_090": {"en": "A major update reset your app preferences to the new defaults. Your playlists, favorites, history, reminders, recordings and Trakt account were kept."},
    "settings_reset_all": {"en": "Reset all settings…"},
    "settings_reset_confirm_1": {"en": "This will erase every dopeIPTV preference on this computer: your playlists, favorites, history, theme, resume positions, PIN, Trakt/TMDB keys and the panel layout.\n\nContinue?"},
    "settings_reset_confirm_2": {"en": "Are you really sure? This can't be undone."},
    "settings_reset_done": {"en": "All settings have been reset. dopeIPTV will now close - start it again to set up your first playlist."},

    "cat_search_placeholder": {"en": "Search categories & channels…"},
    "cat_search_items": {"en": "Search this list…"},
    "cat_search_none": {"en": "No matching categories"},

    "sec_timeshift": {"en": "Timeshift"},
    "reminders_menu": {"en": "Reminders…"},
    "reminders_title": {"en": "Reminders"},
    "reminders_empty": {"en": "No reminders set"},
    "reminders_remove": {"en": "Remove"},
    "reminders_remove_n": {"en": "Remove {n}"},
    "reminder_starts_in": {"en": "starts in {t}"},
    "reminder_starting": {"en": "starting now"},
    "reminder_watch_named": {"en": "Watch {title}"},
    "reminder_multi_body": {"en": "{n} programmes are starting now"},
    "rec_edit_info": {"en": "Edit info…"},
    "rec_info_title": {"en": "Recording info"},
    "rec_info_name": {"en": "Title"},
    "rec_info_desc": {"en": "Description"},
    "sec_maintenance": {"en": "Maintenance"},
    "win_shortcut_btn": {"en": "Create shortcut"},
    "win_shortcut_hint": {"en": "Add dopeIPTV to the Start menu and desktop"},
    "win_shortcut_done": {"en": "Shortcut created"},
    "win_shortcut_fail": {"en": "Couldn't create shortcut"},
    "ts_reset_channel": {"en": "Reset timeshift for this channel"},
    "ts_reset_done_one": {"en": "Timeshift reset for this channel"},
    "ts_reset_broken": {"en": "Reset timeshift channels"},
    "ts_reset_done": {"en": "Timeshift channels reset"},
    "ts_reset_hint": {"en": "Show catch-up again on channels the app learned don't serve it. They're re-tested automatically after a while, too."},

    "ts_archive_unavailable": {"en": "Catch-up isn't available for this channel - check with your provider"},
    "ts_shorter_archive": {"en": "Archive is shorter than listed - trying the deepest available…"},
    "ts_checking": {"en": "Checking catch-up…"},
    "ts_check_failed": {"en": "Couldn't reach the catch-up archive - check the connection and try again"},

    # ── Keyboard shortcuts editor ─────────────────────────────────────────
    "sc_title": {"en": "Keyboard shortcuts"},
    "sc_open": {"en": "Keyboard shortcuts…"},
    "sc_hint": {"en": "Click a field and press the new key combination. Escape and Delete stay reserved."},
    "sc_reset": {"en": "Reset to defaults"},
    "sc_save": {"en": "Save"},
    "sc_next_channel": {"en": "Next channel"},
    "sc_prev_channel": {"en": "Previous channel"},
    "sc_last_channel": {"en": "Last channel"},
    "sc_play_pause": {"en": "Play / Pause"},
    "sc_fullscreen": {"en": "Fullscreen"},
    "sc_mute": {"en": "Mute"},
    "sc_popout": {"en": "Pop out player"},
    "sc_record": {"en": "Record"},
    "sc_stats": {"en": "Playback stats"},
    "sc_epg_guide": {"en": "TV guide"},
    "sc_epg_search": {"en": "Search guide"},
    "sc_reminders": {"en": "Reminders"},
    "sc_sidebar": {"en": "Toggle sidebar"},
    "sc_focus_mode": {"en": "Focus mode"},
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def set_language(code: str) -> None:
    """Set the active language.  Falls back to ``"en"`` for unknown codes."""
    global _current_language
    _current_language = code if code in LANGUAGES else "en"


def current_language() -> str:
    """Return the current language code (e.g. ``"en"``, ``"sv"``)."""
    return _current_language


def tr(key: str, **kwargs: object) -> str:
    """Return the translated string for *key* in the current language.

    Any ``{name}`` placeholders in the string are substituted with the
    corresponding keyword arguments via ``str.format_map()``.

    If the key is missing entirely, the key itself is returned (prefixed
    with ``?``) so missing translations are easy to spot during development.
    If the key exists but has no translation for the active language, the
    English string is used as a fallback.
    """
    entry = _STRINGS.get(key)
    if entry is None:
        return f"?{key}"
    text = entry.get(_current_language) or entry.get("en", f"?{key}")
    if kwargs:
        try:
            return text.format_map(kwargs)
        except (KeyError, IndexError):
            return text
    return text


# --------------------------------------------------------------------------
# Locales
#
# English is the only language inline (above). EVERY other language — including
# the ones that used to live in code (Swedish, Spanish, German, French, Chinese,
# Russian, Thai) — ships as dopeiptv/locale/<code>.json ({key: "translation"}),
# so the English source stays readable and each language is one reviewable file
# a native speaker can correct in isolation. Each file is merged into _STRINGS
# at import; any key it doesn't cover falls back to English (see tr()). A code
# joins the language PICKER only once its file translates most of the base
# strings, so a half-finished locale never ships a half-English UI.
# --------------------------------------------------------------------------

import json as _json
import os as _os
import sys as _sys

#: Right-to-left languages (Arabic, Persian, Hebrew, Urdu). They ship as add-on
#: locales; the UI mirrors its layout direction for these (see is_rtl / apply in
#: the window).
RTL_LANGUAGES = frozenset({"ar", "fa", "he", "ur"})

#: Native names for every non-English language, in picker order: English is
#: seeded separately (always first), then the rest are ordered alphabetically
#: by their English language name (shown in the trailing comment on each line),
#: which is deterministic and easy to keep sorted when a language is added.
#: A code is offered in the picker once its locale file covers
#: _LOCALE_READY_RATIO of the base keys.
_NATIVE_NAMES = {
    "ar": "العربية",           # Arabic
    "zh": "中文",               # Chinese
    "hr": "Hrvatski",          # Croatian
    "nl": "Nederlands",        # Dutch
    "fr": "Français",          # French
    "de": "Deutsch",           # German
    "el": "Ελληνικά",          # Greek
    "he": "עברית",             # Hebrew
    "hi": "हिन्दी",              # Hindi
    "id": "Bahasa Indonesia",  # Indonesian
    "it": "Italiano",          # Italian
    "ja": "日本語",             # Japanese
    "ko": "한국어",             # Korean
    "fa": "فارسی",             # Persian
    "pl": "Polski",            # Polish
    "pt": "Português (BR)",    # Portuguese
    "ru": "Русский",           # Russian
    "sr": "Srpski",            # Serbian
    "es": "Español",           # Spanish
    "sw": "Kiswahili",         # Swahili
    "sv": "Svenska",           # Swedish
    "th": "ไทย",                # Thai
    "tr": "Türkçe",            # Turkish
    "uk": "Українська",        # Ukrainian
    "ur": "اردو",              # Urdu
    "vi": "Tiếng Việt",        # Vietnamese
}

#: A locale must translate at least this fraction of the base keys before it
#: is offered in the picker (English fallback covers the rest).
_LOCALE_READY_RATIO = 0.9


def _locale_dir() -> str:
    """Directory holding the add-on locale JSONs.

    Running from source (or a wheel) it sits right next to this module. In a
    frozen build (PyInstaller .app / .exe) the pure-Python modules live inside
    an archive, so ``__file__`` can point at a virtual path with no ``locale/``
    beside it; there the data files are unpacked under the bundle root
    (``sys._MEIPASS``), and on a macOS .app the code lives in
    ``Contents/Frameworks`` while data may sit in ``Contents/Resources``. Try
    every layout so the languages load no matter how the app was packaged - a
    missing locale dir silently collapses the picker to English-only."""
    here = _os.path.dirname(_os.path.abspath(__file__))
    candidates = [_os.path.join(here, "locale")]
    base = getattr(_sys, "_MEIPASS", None)
    if base:
        candidates.append(_os.path.join(base, "dopeiptv", "locale"))
        candidates.append(_os.path.join(base, "locale"))
    if f"{_os.sep}Contents{_os.sep}Frameworks" in here:
        res = here.replace(
            f"{_os.sep}Contents{_os.sep}Frameworks",
            f"{_os.sep}Contents{_os.sep}Resources")
        candidates.append(_os.path.join(res, "locale"))
    for cand in candidates:
        if _os.path.isdir(cand):
            return cand
    return candidates[0]


def _load_locale_files() -> None:
    """Merge every dopeiptv/locale/<code>.json into _STRINGS, then register each
    language in LANGUAGES — in _NATIVE_NAMES order — once its file covers
    _LOCALE_READY_RATIO of the base keys. English is always present; malformed
    files are skipped, never fatal."""
    base_total = len(_STRINGS)
    coverage: dict[str, int] = {}
    try:
        files = sorted(_os.listdir(_locale_dir()))
    except OSError:
        files = []
    for fn in files:
        if not fn.endswith(".json") or fn.startswith("_"):
            continue
        code = fn[:-5]
        try:
            with open(_os.path.join(_locale_dir(), fn), encoding="utf-8") as fh:
                data = _json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        translated = 0
        for key, value in data.items():
            entry = _STRINGS.get(key)
            if entry is None or not isinstance(value, str) or not value.strip():
                continue
            entry[code] = value
            translated += 1
        coverage[code] = translated
    if not base_total:
        return
    for code, name in _NATIVE_NAMES.items():
        if code not in LANGUAGES and (
                coverage.get(code, 0) / base_total >= _LOCALE_READY_RATIO):
            LANGUAGES[code] = name


def is_rtl(code: str | None = None) -> bool:
    """Whether *code* (default: the active language) reads right-to-left."""
    return (code or _current_language) in RTL_LANGUAGES


def base_string_keys() -> list[str]:
    """Every translatable key, for the locale-template tool and tests."""
    return list(_STRINGS.keys())


def english(key: str) -> str:
    """The English source string for *key* (the translator's reference)."""
    return _STRINGS.get(key, {}).get("en", "")


_load_locale_files()
