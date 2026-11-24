# Architecture

dopeIPTV is a desktop IPTV client built with PyQt6 and libmpv that
connects to Xtream Codes providers, M3U playlists, Stalker / Ministra
portals, boxes on the home network and Jellyfin, Emby and Plex servers.
The codebase is grouped into layered subpackages inside the
`dopeiptv/` package - `providers/` (data sources), `core/` (storage,
background work, OS glue), `media/` (playback), `services/`
(window-agnostic application services) and `ui/` (everything the user
sees).

## Module map

Every module under `dopeiptv/`, with its role in short; the module table
in `AGENTS.md` says more about most of them - what each owns, its rules
and its names.

```
dopeiptv.py            Thin launcher (calls dopeiptv.app.main; --server: headless)

dopeiptv/
  __init__.py          Package metadata (APP_NAME, ORG, VERSION)
  app.py               QApplication bootstrap, login flow
  launcher.py          The installed command: app.main, or headless with --server
  headless.py          Server mode without a window (services/home_server.py):
                       QtCore for the settings, no widget, no mpv
  i18n.py              English source strings + set_language / tr()
  locale/              One <code>.json per non-English language (26 files)
  assets/              The Solecraft and TMDB logotypes; flags/, the bundled
                       SVG flags for category names (flag-icons, MIT)
  _*_key.py.example    The shape of the git-ignored keys a release build writes
                       (TMDB, OpenSubtitles, Simkl)

  providers/           Data sources (the network / provider edge)
    __init__.py        The providers layer (no code)
    client.py          XtreamClient, OfflineClient/M3UClient/DemoClient
    stalker.py         StalkerClient - Stalker / Ministra MAC portals
                       (handshake, links, the archive by programme)
    boxes.py           BoxClient - what the boxes at home share: login,
                       catalogue, stable ids, recordings as films, the box's
                       guide as XMLTV
    enigma2.py         Enigma2 receivers over OpenWebif / the Dreambox
                       WebInterface: channels, signal, screenshot, remote keys,
                       recordings, transcoding, tuner state
    autotimer.py       An Enigma2 box's AutoTimer plugin: its rules read, made,
                       changed, run and previewed
    bouquets.py        An Enigma2 box's channel lists through OpenWebif's
                       bouquet editor, and the editor's backup
    tvheadend.py       Tvheadend: channel tags, stream profiles, XMLTV, DVR
                       recordings, digest login
    hdhomerun.py       HDHomeRun tuners and their emulators: discover/lineup,
                       SiliconDust's guide, UDP discovery
    satip.py           SAT>IP servers: SSDP, tuners per system, the server's
                       lists or an M3U, RTSP over TCP/UDP or HTTP
    vdr.py             VDR through restfulapi, its streams from
                       streamdev-server
    dvbviewer.py       DVBViewer Media Server's web API (port 8089) and its
                       streams
    nextpvr.py         NextPVR v5+: the session API, channels, guide,
                       recordings
    mythtv.py          MythTV's Services API: channels, guide, recordings (no
                       live TV over HTTP)
    lan_scan.py        "Find on my network": the broadcasts, SSDP and a bounded
                       port sweep of the home network; what can be cast to
                       there
    mediaserver.py     MediaServerClient - what Jellyfin, Emby and Plex share:
                       a token, one device id per install, films / series /
                       channels as the app's rows, reports back on a thread
    jellyfin.py        JellyfinClient / EmbyClient - sign-in, Quick Connect,
                       libraries, Live TV, UDP 7359 discovery
    plex.py            PlexClient - PIN sign-in, servers, libraries, DVR, GDM
    epg.py             XmltvGuide - XMLTV parser with disk cache + index
    epg_index.py       The guide parser as a process of its own (--epg-index)
    metadata.py        TMDB client, PosterResolver (cover art)
    trakt.py           Trakt.tv sync (watched, watchlist)
    simkl.py           Simkl sync (scrobbles, watched, Plan to watch)
    oauth_loopback.py  Loopback HTTP server for the Trakt OAuth redirect
    diagnostics.py     Plain-language "why won't this stream play?" probe
    opensubtitles.py   OpenSubtitles.com REST client (search, download, hash)
    radio_browser.py   Internet radio from radio-browser.info: servers from
                       its DNS round robin, falling back between them
    chromecast.py      ChromecastManager (devices by address too)
    dlna.py            DLNA / UPnP TVs: SSDP discovery, SOAP control, file share
    casting.py         CastHub - Chromecast, DLNA and Kodi behind one object
    cast_devices.py    Devices to cast to added by their address: what was
                       typed, what is there, the ones kept
    kodi.py            Kodi as a cast target over JSON-RPC (HTTP)
    mdns.py            mDNS discovery in plain Python (finds Kodi)
    cast_bridge.py     Local bridge that re-encodes the audio a Chromecast
                       refuses (E-AC-3) so the stream still casts
    cast_feed.py       A cast's archive or rewind buffer in pieces, read into
                       one converter run: one growing playlist on the TV
    airplay_bridge.py  AirPlay: what goes to an Apple TV as it is, the rest
                       as HLS by ffmpeg on the LAN (token, cleanup)

  core/                Storage, background work, OS integration
    __init__.py        The core layer (no code)
    stores.py          JSON data stores (playlists, favorites, history, ...)
    workers.py         QThreadPool helpers, LogoLoader, byte-bounded caches
    profiles.py        Profiles: what is a profile's own (declared once) and
                       the household's; ProfileSettings, the PIN, kids profiles
    local_buffer.py    Seekable window of a live channel's rewind buffer (DVR)
    dvr_store.py       App-kept rewind buffer: rolling TS segment store
    dvr_ingest.py      Its one provider connection
    dvr_server.py      Its loopback server for mpv (token, live TS)
    dvr_session.py     Ingest + store + server for one live channel
    dvr_pool.py        Every channel's buffer this session, one budget
    dvr_record.py      A recording read from a buffer
    restream.py        Server mode's restream: a channel fetched once into a
                       buffer session and read by every viewer
    hdhr.py            Server mode as an HDHomeRun tuner: discover, lineup,
                       device.xml, the UDP 65001 answer
    server_page.py     Server mode's page in a browser (HTML/CSS/JS in one, a
                       CSP of its own)
    recording.py       RecordingManager - ffmpeg/mpv stream-copy, timers
    rec_mp4.py         Recordings as MP4: the remux (stream copy, faststart),
                       the plausibility check, the .ts kept unless it passed
    rec_view.py        Recordings as their place shows them: now and next,
                       a frame and the length found once, watched marks
    rec_extras.py      Nerd for recordings: the file name template, the command
                       after a recording
    series_rules.py    Series recordings: rules, airings found, the plan, padding;
                       a rule that reminds instead, its reminders' plan
    series_view.py     A series as its page shows it: seasons (Specials last),
                       the next episode, the season it opens on and the one kept
    series_continue.py The Series place's Continue row from what the app keeps
                       (the page's cache, resume points, History): no request
    film_view.py       A film as Movies and the detail column show it: year,
                       length, rating, cast, New, Continue
    night_mode.py      Night mode for the sound: one labelled audio filter
    viewing_stats.py   Your statistics: seconds watched per title and hour, the
                       sums (this computer only, never synced)
    updates.py         Update check: dopeiptv.org first (releases.json,
                       nightly.json), GitHub as fallback; none from a modified
                       build
    selfupdate.py      AppImage in-place update: download, verify, test, swap;
                       every update's signed checksum check
    winupdate.py       The same for a copy the Windows installer put there (the
                       next installer)
    macupdate.py       The same for the Mac app (the .dmg, Solecraft AB's and
                       notarised)
    ed25519.py         Ed25519 signatures in plain Python (the release key)
    update_keys.py     The public half of dopeIPTV's release signing key
    build_check.py     Official, modified or from source: the signed build
                       manifest checked once at start; X-dopeIPTV-Build
    donate_rhythm.py   When the app asks for a tip (the sidebar's heart one
                       week a month), pure logic
    report.py          Error reports and feedback: scrubbed, shown in full,
                       sent only on Send; the answers to feedback
    sync.py            Settings sync: the sync code, what syncs, merging, the
                       round trip to dopeiptv.org (end-to-end encrypted)
    backup.py          Full backup and restore (one JSON file, encrypted with
                       an optional password; a restore over everything is put
                       in place as the app next starts)
    chacha20poly1305.py ChaCha20-Poly1305 (RFC 8439) in plain Python - what
                       sync, Watch together and a backup's password seal with
    together.py        Watch together: the room code, sealed messages, keeping
                       in step, the relay at dopeiptv.org
    discord_rpc.py     Discord status over Discord's local IPC
    privacy.py         Private viewing; Clear recent history by time range
    wakelock.py        Screensaver/suspend inhibitor (DBus + caffeinate)
    gcguard.py         Cyclic garbage collection on the GUI thread only
    contest.py         ContestWatch: another device taking the account's connection
    cast_watch.py      A running cast watched: the receiver's state as one word,
                       the watchdog for a TV stuck buffering (pure)
    connections.py     Connections to the account: the app's own counted, the
                       limit, a warning before one more would go over it
    tuners.py          An Enigma2 box's and a SAT>IP server's tuners on the
                       same count: free, the last free, or cutting something
                       off
    rtsp.py            A SAT>IP source's RTSP transport for mpv and the
                       recorder
    ts_scramble.py     Is a transport stream encrypted? (its first packets)
    ts_analysis.py     Nerd: a simple TR 101 290, read from the rewind buffer
    teletext.py        Teletext in plain Python: TS to pages, a page drawn as
                       cells, the PMT's teletext descriptors
    teletext_subs.py   Teletext and DVB subtitles as subtitle tracks; the page
                       kept per channel
    stream_info.py     Validated stream metadata in a bounded cache; whether
                       the tracks have a real picture
    hls_cap.py         Nerd: the highest quality adaptive (HLS) streams play at
    live_latency.py    Nerd's low latency for live channels from the provider
    nerd.py            Nerd settings into what the player and network do (mpv
                       options, user agent, proxy, IP version, VPN guard)
    nerd_lists.py      Nerd on a source's lists: a picon folder, a channel's
                       guide picked by hand, udpxy
    nerd_check.py      Does a Nerd setting work? Each tried for real
    mirrors.py         Nerd: a source's backup addresses - the next when the
                       one in use is down (never on a refused login)
    conn_test.py       Nerd: test the connection to a source step by step
                       (DNS, TCP, TLS, login, first byte, speed)
    health.py          Nerd's stream health per channel (it only watches)
    channel_prefs.py   Nerd: a channel's own audio/subtitle language,
                       deinterlace, aspect, audio delay (what each sets in mpv)
    channel_numbers.py Nerd: channel numbers of the user's own per source
    catchup.py         Nerd: a source's own catch-up address (Kodi's
                       placeholders, the M3U modes)
    url_rewrite.py     Nerd: rewrite rules for a source's stream addresses
    m3u_ids.py         Stable channel ids for M3U sources, and the one-time
                       move of values kept under ids by place
    home_net.py        A home network in parts (VLANs): Nerd's other networks,
                       who counts as at home, who may use what needs no token
    doh.py             Nerd's own DNS over HTTPS (Cloudflare, Quad9, Google or
                       one's own)
    net_route.py       A source's own way onto the network: its DNS, IP version
                       and pinned certificate
    net_adapter.py     The app's own requests on a source's route (a requests
                       adapter)
    net_bridge.py      The one way out for the programs the app starts on a
                       stream, and the player's for every internet source's
                       stream: a loopback proxy that judges where each
                       connection lands and hands out aliases that name
                       nothing of an address
    cert_pin.py        Trusting one certificate for one source: read,
                       fingerprinted, pinned, a change noted
    http_login.py      A login in a stream's address answered as the server
                       asks (basic or digest)
    exclusive_bind.py  How the app's own servers bind: another program's port
                       stays its own, an idle client is let go
    remote_api.py      The remote API and the shared lists' HTTP server (a
                       token on every request)
    events.py          Nerd: events pushed out - webhooks and MQTT (queue +
                       a thread per receiver; it only watches)
    mqtt.py            A small MQTT 3.1.1 client (standard library only)
    mqtt_control.py    Nerd: Home Assistant's commands over MQTT (topics,
                       parsing, the controls' discovery, TV mode's switch)
    phone_remote.py    The phone remote's server (home network, token in the
                       address, rate limit and lock-out, nothing else)
    phone_page.py      The phone remote's page (HTML/CSS/JS, nothing fetched):
                       the lists, Now playing with seeking, recording, tracks
                       and the sleep timer, and a remote with a touchpad while
                       TV mode is on
    phone_browse.py    The phone remote's lists beyond the channels: built
                       once, paged, opaque ids
    qr.py              QR code encoder (byte mode, level M, versions 1-40)
    ical.py            Reminders and recordings as iCalendar (RFC 5545)
    commands.py        What the app is asked from outside: --play, --channel,
                       dopeiptv:// links
    instance.py        A second start hands its command to the copy running (a
                       local socket of the user's own)
    search_all.py      One search across every source: rows as the lists show
                       them, a guide's week compact, four groups ranked
    every_source.py    One channel list across every source: another source's
                       row (no id of the source in use's), the sections under
                       their headings, another source's guide around now
    mitt.py            Mine's content: entries out of the stores, the filter,
                       favourites by habit, the queue's order, History by day
    places.py          The app's places (Mine, TV, Movies, Series, Guide,
                       Search, Local files, Radio, Recordings): entries, modes,
                       where the app opens, Home's settings retired
    guide_rows.py      The guide's rows read from channel names: a heading
                       "channel", a name's short form
    palette_match.py   The command palette's matching: folded text, word
                       starts, an index for big lists
    local_listing.py   One folder of Local files read in slices, and the order
                       picked for every folder
    local_search.py    Local files' search through a folder and its subfolders,
                       bounded and cancelable
    local_watch.py     What Local files watches on disk, when it reads again,
                       what changed between two listings
    shortcut_keys.py   Keyboard shortcuts: the actions, the app's keys, the
                       user's over them, never two on one key
    video_mode.py      How the picture is drawn: on a thread of its own unless
                       turned off, and why when not
    log.py             Central logging (configure_logging, DOPEIPTV_LOG)
    platform_macos.py  macOS-specific helpers (OpenGL, libmpv, paths)
    platform_windows.py Windows helpers (libmpv discovery, wakelock, shortcut)
    player_exec.py     Locate the external mpv / VLC binary on the host
    desktop_entry.py   The user's .desktop entry + icons (Wayland reads the
                       window icon from there, not from the app)
    xdg.py             Launch system programs from a frozen bundle without
                       our bundled libraries leaking into them
    tv_input.py        TV mode's input as actions: LIRC (lircd's socket), a
                       game controller (SDL2 if pysdl2 is there, else evdev)
    tv_nav.py          TV mode's focus moves (nearest element, grid step)
    ytdlp.py           yt-dlp for YouTube, Twitch and the like: the system's
                       or the app's own copy (only the release the build
                       pinned, its SHA-256 checked; ytdlp_pin.py, written
                       in the build after yt-dlp's signature is checked)
    dest_guard.py      Where the app's own requests may land: checked on
                       the socket (DNS rebinding) and on every redirect
    audiotags.py       Artist/album/title and cover art from music files
                       (header-only, no dependency)
    _alloc.py          Optional allocator preload for long sessions
    _lazy_requests.py  Lazy stand-in for `requests` (faster cold start)

  media/               Playback
    __init__.py        The media layer (no code)
    embedded.py        EmbeddedPlayer: the in-app player
    player_overlays.py What floats over the picture: centre play/pause, hover
                       scrubber, TIMESHIFT pill, Next episode, fullscreen UI
    control_icons.py   The player's control glyphs, drawn
    audio_effects.py   The visualiser, the equaliser and night mode on the core
    sleep_timer.py     The sleep timer's countdown and the number typed, in
                       the picture's corner
    stats_overlay.py   Stats for nerds over the picture (_StatsLabel)
    tracks.py          Audio and subtitle tracks (the one a stream opens
                       with, the menu's entries), a channel's own settings
    popout_mirror.py   The pop-out's picture: the mirror (raster, GL or the
                       picture's own thread), the docked placeholder
    seek_slider.py     The seek bar slider: click, drag, hover time
    video_thread.py    ThreadedVideoSurface (the default): a native window
                       a render thread of its own draws mpv's frames into;
                       the overlays drawn into the picture by mpv
    video_macos.py     macOS's GL context for that thread: an NSOpenGLContext
                       put on its view on the main thread, drawn into and
                       flushed on the render thread (PyObjC, CGL); the
                       picture's own NSView, never the mouse's
    video_surface.py   _MpvGLWidget(QOpenGLWidget), the window-thread surface
                       (fallback and switch); the Linux pop-out's raster mirror
    rewind_bar.py      DVR row + timeshift timeline over the video
    dvr_player.py      The player's side of the app-kept buffer
    loading_overlay.py The loading indicator over the video: spinner, logo or
                       poster, Still loading, Buffering… 45%
    audio_only.py      Whether the stream that plays is sound alone (for the
                       visualiser)
    stream_info.py     The channel-change technical banner, read from mpv's
                       current tracks
    teletext_subs.py   Teletext and DVB subtitles in the player's subtitle menu
    players.py         External player launch (mpv IPC / VLC), libmpv detect
    subtitle_text.py   Stray right-to-left marks out of subtitle files

  services/            Window-agnostic application services
    __init__.py        The services layer (no code)
    coverart.py        CoverArtService - TMDB resolver lifecycle + the
                       ordered list/detail cover-candidate logic
    reminders.py       ReminderStore - programme reminders (persistence)
    resume.py          ResumeStore - per-playlist resume positions
                       (persistence + keep/drop rule)
    mpris.py           MPRIS media session on the D-Bus session bus (Linux)
    media_session.py   The system's media controls: one host, the session
                       for this system (MPRIS / Now Playing / SMTC), the
                       poll, commands queued onto the GUI thread
    media_macos.py     macOS Now Playing (PyObjC's MediaPlayer)
    media_windows.py   Windows' System Media Transport Controls (PyWinRT)
    airplay_macos.py   AirPlay on macOS: AVPlayer + AVKit's route picker
                       in an NSPanel of its own, a CastHub receiver
                       (PyObjC's AVFoundation, AVKit, AppKit)
    notify.py          Desktop notifications (org.freedesktop.Notifications)
    search_sources.py  The search across sources: each source's client, lists
                       (a portal's only as loaded) and guide from disk, once -
                       Every source's channels and guide around now too
    popular.py         Popular right now: TMDB's chart of the week matched
                       against the provider's catalogue (Movies' and Series' tops)
    failover.py        Failover: a live channel that will not play found in
                       another source (never what its lists hide)
    share.py           Every source's channels as one M3U and the guides as one
                       XMLTV (for the remote API and server mode)
    home_server.py     Server mode: the restream, the HDHomeRun API and
                       discovery, the playlist, the guide, the page and its API
    guide_keeper.py    Server mode's guides kept fresh, one source at a time
    together.py        Watch together, the catalogue side: what plays here, the
                       same title found in this source

  ui/                  Everything the user sees
    __init__.py        Lazy MainWindow export (PEP 562)
    main_window.py     MainWindow composition root (QMainWindow + mixins)
    mw_build.py        The window built: _build_ui's parts (menus, sidebar,
                       middle pane, columns, timers, keys) in their old order
    mw_build_detail.py The detail column built: the pane, the cast card, the
                       player, the info under it
    mw_lists.py        The lists: a mode switched, the categories, the middle
                       list, the filter
    app_menu.py        The app's menus, one layout on every platform: File,
                       View, History, Help
    mw_settings.py     Settings dialog mixin: the window, its pages in their
                       groups, what their builders share (SettingsContext),
                       what they store (which row is where:
                       docs/SETTINGS_MAP.md)
    settings_ui.py     Settings building blocks: sidebar shell (groups, a
                       group's pages shown only while it is on), pages,
                       cards, rows, link rows, switches, the playlist delegate
    qt_i18n.py         Qt's own strings (standard buttons, calendars) and
                       QLocale-formatted dates in the app's language
    source_page.py     A source's own page in Settings: Connection, Network,
                       Streams, Guide, Advanced
    source_nerd.py     A source's own Nerd options, without a layout
    source_net.py      A source's own DNS, IP version and trusted certificate,
                       each with a Test
    source_presets.py  Presets in the source dialog (Fritz!Box, Channels DVR,
                       Threadfin, xTeVe, Dispatcharr, ErsatzTV)
    rewrite_rules.py   The rewrite rules' rows in a source's Nerd options
    cert_trust.py      The window that shows a certificate before it is trusted
    nerd_settings.py   Nerd in Settings: Status, and the five pages beside it
                       while it is on
    set_watch.py       Settings' WATCH pages (Playback, Picture and sound,
                       Guide, Multiview, Recording, Casting), each built the
                       first time it is needed
    set_content.py     Settings' CONTENT pages (Sources, Mine, Local files,
                       Posters and info)
    set_services.py    Settings' SERVICES pages (Watch history, Sync,
                       Integrations)
    set_system.py      Settings' SYSTEM pages (General, Appearance, Keyboard
                       shortcuts, Profiles, Parental lock, Advanced)
    about.py           About: the version, the update check, the release notes
                       cut to what is new to the reader
    net_advanced.py    Nerd's Own DNS, Other networks (VLANs) and Low latency
                       cards
    nerd_check_line.py The ✓ / ! / ✗ line under a Nerd setting, its Test and
                       Details
    box_finder.py      Box and media-server kinds in the add-a-source forms;
                       "Find on my network"
    media_signin.py    Signing in to Jellyfin, Emby or Plex in the source
                       dialog and the welcome
    mw_trakt.py        Trakt / watched / watchlist mixin (the sync reads
                       every account signed in: Trakt, Simkl or both)
    mw_simkl.py        Simkl: scrobbles, its Settings card, PIN sign-in
    mw_discord.py      Discord status ("Watching ...") from the player
    mw_sync.py         Settings sync in the window: when a round runs, its page
                       in Settings
    mw_profiles.py     Profiles in the window: a switch with no restart, "Who's
                       watching?", the PIN, kids profiles
    profiles_ui.py     Profiles on screen: the avatar, the picker, the
                       profile's window, Settings > Profiles
    mw_recording.py    Recording mixin (its base: mw_timeshift.py)
    mw_timeshift.py    The provider's archive: catch-up and timeshift
    mw_place_rec.py    The Recordings place where the list was: header, ⋯,
                       recording now and next, the recordings as rows or cards
    rec_place.py       The Recordings place itself, dressed as it paints
    mw_series.py       Series recordings: "Record series…", the rules' check
    autotimer_ui.py    A box's AutoTimer in the window: the rule editor, the
                       preview, the box's rules beside the app's
    mw_portal_archive.py A Stalker portal's archive: by programme, its link
                       asked for off the window's thread
    mw_media.py        Media servers: what plays reported, their resume
                       points and watched marks (media_signin.py: signing in)
    mw_context.py      Right-click context-menu mixin
    mw_favorites.py    Favorites in bulk: multi-row actions, move, drag-and-drop
    mw_detail.py       EPG / detail-panel mixin (a film's plot, cast and
                       Continue)
    mw_search.py       Sidebar category / item search mixin
    mw_sidebar.py      Sidebar chrome (collapse / icon rail / mid-compact)
    mw_shortcuts.py    Keyboard shortcuts: dispatch, the keys in force, live rebind
    mw_nav.py          Nav chrome (focus mode, nav colours/icons, the entry
                       lit, History's Watched filter, the nav's status)
    mw_source_label.py The active source in plain sight: the status line, TV
                       mode's header, the avatar's menu (the label over the
                       categories is kept hidden)
    mw_place_look.py   What the places show: sidebar entries taken out, the
                       fold, rows hidden, Just the list
    mw_pane_follow.py  The middle pane follows what plays
    mw_cast.py         Casting: the cast dialog, the card's controls, archive hand-over
    mw_cast_moment.py  Going back during a live cast: the rewind buffer, else
                       the archive; the strip's timeshift menu, back to live
    mw_cast_watch.py   The cast card's picture, words and state; a stuck cast
                       sent again (twice, then Try again); "On TV" marks
    cast_add_dialogs.py The cast window's Add Kodi… and Add a device by
                       address… windows
    cast_dialog.py     The cast window: the devices found and added, tracks and
                       quality, the TV cast to last first
    cast_card.py       The cast card in the video's place (pure presentation)
    mw_airplay.py      AirPlay (macOS): the receiver added, its own endings
    mw_popout.py       Detached-window ("pop out") player mixin
    mw_multiview.py    Multiview: 2/4/6/9 live streams (own mpv per cell)
    multiview_cell.py  One multiview cell: its own mpv surface, title strip,
                       seek bar, focus, mute, pause
    multiview_window.py The multiview grid's own window
    mw_connections.py  Connections to the account: counted, warned about
    mw_mirrors.py      Nerd: a stream whose server is down, on the next address
    conn_test_dialog.py Nerd: Test connection's window
    mw_box.py          The box from the app: zap, wake, standby, record on the
                       box; an Enigma2 box's info, screenshot, remote and lists
    box_tools.py       An Enigma2 box's own windows: info with the live signal,
                       a screenshot, its remote control
    bouquet_editor.py  An Enigma2 box's channel lists edited from the computer,
                       a backup first
    mw_failover.py     Failover in the window; another source's channel played
                       without switching to it
    mw_health.py       Stream health in the window: the hooks, the channel's
                       window, the list's dot
    mw_stream_info.py  Stream information in the window: kept per channel, the
                       list's technical line
    mw_chanprefs.py    A channel's own settings in the window
    mw_ts_analysis.py  Stream analysis in the window (Nerd)
    ts_analysis_dialog.py Stream analysis's window: summary, tiles, indicators,
                       the PID table
    mw_teletext.py     Teletext in the window, read from the channel's rewind
                       buffer
    teletext_view.py   The teletext viewer: the page drawn, three digits, the
                       colour keys, hold, reveal
    mw_busy.py         The notices at the sidebar's foot (notices.py, notice_widgets.py)
    notices.py         The notices' state: jobs, passing notes, messages and
                       decisions (NoticeHub)
    notice_widgets.py  The notices drawn: the activity line, its panel, the
                       decision card
    mw_updates.py      Update-check badge/status wiring, "Update now"
    mw_reminders.py    Programme-reminder UI (set / list / fire)
    mw_calendar.py     Reminders and recordings as a calendar (file, served)
    mw_report.py       Send feedback, the offer of a report after an error, the
                       answers to feedback and replies
    mw_donate.py       A tip from the app: About's link, the heart one
                       week a month
    mw_privacy.py      Private viewing and Clear recent history in the window
    mw_commands.py     Commands from outside carried out: an address, a channel
                       by name, the next channel
    mw_palette.py      The command palette in the window: its rows and what
                       each does
    command_palette.py The command palette's overlay (Ctrl+K / ⌘K)
    mw_remote.py       The remote API and sharing in the window
    mw_server.py       Server mode in the window: on and off, the window as its
                       host, room made for the app's own use
    mw_events.py       Events pushed out (webhooks, MQTT) and Home Assistant's
                       commands in the window
    mw_phone.py        The phone remote: on/off, its requests on the window's thread
                       (TV mode's through mw_tv.tv_command, as the remote API's
                       and Home Assistant's)
    mw_phone_browse.py The phone remote's lists beyond the channels: films,
                       series, recent, files, radio, recordings
    mw_phone_rec.py    Recording from the phone remote, and the app's questions
                       asked there
    mw_together.py     Watch together in the window: start, join, leave, kept
                       in step
    together_ui.py     Watch together's dialog and the strip under the video
    mw_mpris.py        The system's media controls (media_session host) +
                       media keys
    mw_stats.py        Your statistics: playback time counted, its window
    stats_window.py    The statistics window: tiles, two column charts, top lists
    mw_follow.py       Follow a series: new-episode checks and news
    mw_subtitles.py    Subtitles from OpenSubtitles; remembered per title
    mw_places.py       The places and the way to each (no Home): a place's
                       own page through its hook, else its list under the
                       shared header; the start where the person left off
    mw_mitt.py         Mine's own page in the window: the stores read on a
                       worker, everything played through the window's paths
    mitt_page.py       Mine's page itself: tiles, the queue, the timeline,
                       the folders - painted views on media_cards' PlacePage
    mw_popular.py      Popular right now for Movies and Series, matched to
                       the source on a worker
    mw_guide.py        The Guide page in the middle pane, and its pop-out; the
                       guide's data loaded, kept fresh, cleared
    mw_series_page.py  A series' page in the middle pane: opened from anywhere,
                       played through the window's path, Back to the list as it was
    mw_place_films.py  The Movies place: the list's films as posters or rows,
                       Popular right now and Continue over them; a click picks a
                       film for the detail column, a double click plays it
    mw_place_series.py The Series place where the list was: header, Popular right
                       now, Continue, the category's series - drawn from the list
    mw_search_all.py   The Search page in the middle pane: one search across
                       every source, a hit played or opened through the
                       window's path (another source's channel without a switch)
    mw_every_source.py Every source in the TV place: every source's channels in
                       one list, read off the window's thread, another source's
                       played from it without a switch
    search_page.py     The Search page itself: field, chips, grouped results
    series_page.py     The series page itself: header, season chips, the list
    series_cards.py    Its episode cards: model, painted card, keys, lazy pictures
    series_place.py    The Series place itself: header and filter, the rows, the
                       grid whose cards are filled only as they paint
    media_cards.py     The cards every place shares: pills, poster/wide/list cards,
                       channel rows, the sideways Continue row, the page header
                       and the shared page form (PlacePage)
    tv_place.py        The TV place's rows (painted by channel_list's delegate),
                       Recently watched and the header over the list
    mw_place_tv.py     The TV place in the window (_place_tv_show)
    list_place.py      The form a place over the window's list shares: Back,
                       header, filter, Grid / List, rows, cards as they paint
    list_mirror.py     Such a place kept to the list, its source of truth
    mw_local.py        Local files: the folders, the folder view, the search
    mw_local_library.py The library view: its walk, its cache, its pictures
    mw_queue.py        What is dropped on the window; the music queue, the
                       visualiser and the equaliser
    mw_place_local.py  The Local files place where the list was: Back, header,
                       Continue, the folder's rows or the library's posters
    local_place.py     The Local files place itself, dressed as it paints
    mw_local_watch.py  Changes on disk read again by themselves (#34)
    local_watcher.py   The folders watched on a thread of their own
    mw_radio.py        Radio: radio-browser.info's stations, played as music
    mw_place_radio.py  The Radio place where the list was: Back, header, My
                       stations, the stations as rows or tiles, the countries
    radio_place.py     The Radio place itself, dressed as it paints
    mw_webstreams.py   Open address, YouTube/Twitch through yt-dlp, saved
                       addresses
    mw_tv.py           TV mode: on/off, its keys and inputs, the picture with
                       the info bar, zapping (plays through the window's path)
    tv_mode.py         TV mode's page and screens (Home, Channels, Options)
    tv_screens.py      TV mode's guide, Movies/Series and Search screens
    tv_widgets.py      TV mode's tiles, lists, keyboard and info bar
    mw_onboarding.py   The first-run welcome in the window; its Bring step (a
                       sync code or a backup)
    mw_sort.py         List sort order
    widgets.py         Small standalone widgets
    nav_widgets.py     A nav entry (selected bar, status), the rail's hairline,
                       the source's avatar at the foot, its name label over the
                       categories (kept hidden)
    category_flags.py  Bundled SVG flags for category names (flag-icons, MIT)
    player_resize.py   Height handle under the docked video
    welcome.py         First-run welcome overlay (connect / explore)
    channel_list.py    Virtualized list: model, view, painted delegate
    dialogs.py         Playlist editor, content manager
    feedback_dialogs.py Send feedback, the offer of a report, the answers to
                       feedback
    backup_dialogs.py  A backup put back: picked, its password asked, shown,
                       staged
    epg_grid.py        The guide (EpgGrid): timeline grid, filter, bar -
                       on the Guide page or in its window (EpgGridDialog)
    reminders.py       Reminders manager dialog
    calendar_export.py Export to calendar… (an .ics, the subscription addresses)
    phone_remote.py    Phone remote… dialog: the code (QrView), the address
    shortcuts.py       The Keyboard shortcuts card (its own page under System)
    tmdb_match.py      Manual TMDB match dialog
    subtitle_search.py OpenSubtitles search dialog
    theme.py           THEMES (27, six of them light) x ACCENTS
                       (10 + custom), palette dict P, QSS generator
    app_icon.py        The app's icon, plain or with the heart: drawn,
                       set for every window, written to the icon theme
```

Import direction is one-way: `ui` may use `services`, `media`, `core`
and `providers`; `services` builds on `providers`/`core` but never
imports `ui`; `media` and `providers` may use `core`; `core` depends on
nothing above it.  (The one cross-edge is the player's overlays -
`media.embedded`, `media.loading_overlay`, `media.audio_effects` (the
visualiser) and `media.sleep_timer` (the number typed) - -> `ui.theme`
for their colours, which is why
`ui/__init__` exposes `MainWindow` lazily so touching `ui.theme` never
drags in the window.)

## Data flow

1. **Startup** (`app.py`): creates QApplication, applies theme, runs the
   login loop (PlaylistStore -> the source's client from
   `providers/client.make_client`: `XtreamClient`, `M3UClient`,
   `StalkerClient`, a box's client on `BoxClient` (Enigma2, Tvheadend,
   HDHomeRun, SAT>IP, VDR, DVBViewer, NextPVR, MythTV), a media server's
   (Jellyfin, Emby, Plex) or `DemoClient`, then `authenticate`),
   then builds MainWindow.  A first run goes through the setup wizard
   (`mw_onboarding.py`) instead.

2. **MainWindow** loads categories and channels from the provider via
   `run_async()` workers (every client offers the same calls, so the
   window does not care which kind it has).  Results populate `ChannelListModel`; the
   `ChannelDelegate` custom-paints each row (list mode) or cell (grid).

3. **EPG** is fetched in a background worker into `XmltvGuide`, which
   caches parsed XML to disk (per-playlist, 6h TTL, stale-first).

4. **Playback** goes through either `EmbeddedPlayer` (libmpv OpenGL
   render API, drawn on a thread of its own into a native window - or,
   switched off or as a fallback, inside a QOpenGLWidget on the window's
   thread) or an external player launched by `players.py`.

5. **Rewind / DVR**: by default mpv's own demuxer cache is the rewind
   buffer (in memory, or on disk with `local_buffer.py`).  With
   "dopeIPTV's own buffer" on, `DvrPool` runs a `DvrSession` per live
   channel: `dvr_ingest` holds the one provider connection, `dvr_store`
   keeps rolling TS segments on disk within the space budget, and
   `dvr_server` serves them to mpv over loopback - so pausing never stalls
   the download and LIVE never reconnects.  Only a stream is kept: a
   connection that brings a few kilobytes and ends is a failed try, and an
   answer that is plainly no stream (a web page, JSON, a playlist) is given
   up at once - the pool then plays that channel without the buffer for a
   while.  An address opened by hand (Open address…) never gets the
   buffer: mpv opens it.  `media/dvr_player.py` is the player's side of
   it, `media/rewind_bar.py` the bar over the video.

6. **Recording** uses `RecordingManager`: ffmpeg stream-copy subprocess
   for standalone recordings, mpv's `--stream-record` for in-player
   capture, or `BufferRecorder` (`dvr_record.py`) reading from the app's
   own buffer when that is on - no extra connection, and whole while the
   player is paused. With "Save recordings as MP4" on, a finished .ts is
   remuxed into an MP4 off the window's thread (`rec_mp4.py`) and the .ts
   deleted only once the MP4 is plausibly whole.

## Key patterns

- **QSettings** stores all persistent state (playlists, favorites,
  history, category overrides, parental PIN, theme, recording prefs).
  The JSON stores in `stores.py` wrap QSettings keys.

- **Virtualized list** via QAbstractListModel + QStyledItemDelegate
  handles 10k+ channel lists without lag.

- **Logo loading** is lazy and async: `LogoLoader` fetches images in a
  thread pool and updates the viewport on completion.

- **Theme** is a palette dict `P` plus a `build_style()` function that
  generates the full QSS at startup or on theme change.

## External dependencies

- PyQt6 (widgets, OpenGL, core)
- requests (HTTP)
- python-mpv + libmpv (the embedded player - required in practice; the
  packaged builds bundle libmpv)
- pychromecast (Chromecast; bundled in every packaged build)
- PyObjC's MediaPlayer wrapper on macOS and PyWinRT's Windows.Media packages
  on Windows (the system's media controls; installed and bundled only on
  their own system - the app runs without them); PyObjC's AVFoundation and
  AVKit on macOS for AirPlay, the same way
- ffmpeg CLI (standalone recording, the cast and AirPlay bridges; bundled in
  every packaged build)

Everything else (DLNA, Kodi and mDNS, MPRIS over D-Bus, Discord's IPC,
OpenSubtitles, TMDB, Trakt, Simkl, the Stalker portal protocol, the boxes'
and media servers' APIs, SAT>IP's SSDP, MQTT, the sync and update crypto,
teletext, music tags) is written against the standard library and
`requests`.
