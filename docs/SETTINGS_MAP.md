# Settings map

Where every row of the Settings window is: group → page (`page key`) →
section → the QSettings key it stores. One setting, one place; a page that
needs a setting kept elsewhere has a link row to it ("→ Nerd › Player").
The rules for placing a new one are in [`AGENTS.md`](../AGENTS.md), "Where
a setting goes"; `tests/test_settings_map.py` checks that every page is in
one group, every row on one page, that the search finds them, and that
this file names every page.

The pages are built in `ui/mw_settings.py` (`open_settings`), Nerd's in
`ui/nerd_settings.py` (with `ui/net_advanced.py`), the shortcuts in
`ui/shortcuts.py`, Simkl in `ui/mw_simkl.py`, sync in `ui/mw_sync.py`,
Discord in `ui/mw_discord.py`. A row without a key is an action or a
status. "(inverted)" is stored the other way round from what the row says.
A source's own options are on its own page, under Sources (below).
"(this profile's)" is a profile's own (`core/profiles.py`): kept under
`profiles/<id>/` and marked "This profile's" on the row while the household
has two profiles or more.

## WATCH

**Playback** (`playback`)
- Behaviour: `show_stream_info`, `autoplay_preview`, `autoplay_next_episode`, `nextup_lead_secs`,
  `auto_reconnect_live`, `hide_upcoming_prompt` (inverted), `night_mode`
- Stream and buffer: `stream_format`, `cache_secs`; link → Nerd › Player
  (channel start, buffer memory, HLS, low latency)
- YouTube, Twitch and similar sites: `ytdlp_managed`; which yt-dlp is used,
  Download / Update now (`ytdlp_checked`: the last look for a newer one)
- Rewind live TV: `live_rewind_minutes`, `live_rewind_disk`,
  `live_rewind_kept`, `live_rewind_space_gb`; where the buffer is (Open folder)
- (only without the embedded player) why playback opens in mpv

**Picture & sound** (`audio`)
- Audio & subtitles: `audio_lang`, `sub_mode`, `sub_lang`, `sub_lang2` (this profile's; the language picks a teletext subtitle page too; the page chosen per channel in the player's menu is `teletext_pages`, no row)
- How subtitles look: `sub_scale`, `sub_color`, `sub_background`
- Video: `aspect_mode`, `video_deinterlace`, `video_sharpen`, `video_tonemapping`
- Decoding: `hwdec_mode`; `video_own_thread` (the picture drawn on a thread
  of its own; off falls back to the window's thread, also
  `DOPEIPTV_VIDEO_THREAD=0`); link → Nerd › Player (passthrough, video sync,
  interpolation, audio delay per channel, mpv)

**Guide** (`guide`)
- (untitled): `epg_upcoming_count`, `guide_now_line`
- Time offsets: `replay_delay_min`, `epg_delay_min`
- Guide data: Refresh guide, Clear guide cache

**Multiview** (`multiview`)
- Window: `mv_frameless` (inverted), `mv_on_top`, `mv_remember_geo`, `mv_cells`
- Behavior: `mv_stop_docked`, `mv_new_unmuted`, `mv_conflict_mode`
- Controls: `mv_autohide_secs`, `mv_seek_step_min`, show the tips again (`mv_info_seen`)

**Recording** (`recording`)
- Folder: `recordings_dir`, `rec_save_mp4`, `rec_mp4_keep_subs` (only while saving as MP4); link → Nerd › Remote and
  automation (file names, what runs after)
- Limits: `rec_max_value` + `rec_max_unit`, `rec_total_value` + `rec_total_unit`
- Padding: `rec_pad_before_min`, `rec_pad_after_min`
- Series recordings: the rules (`series_rules_<source id>`), add a word, delete;
  on an Enigma2 box with AutoTimer also the box's own rules (kept on the box,
  no key here) - new, edit, on/off, preview, run now - each row saying who
  records it
- (untitled): which recorder is used, how to record

**Casting** (`casting`) - AirPlay's rows join it, and Chromecast, DLNA and
Kodi rows belong here
- Chromecast: `cast_pause_gb`
- (untitled): keep the rewind buffer while casting (`cast_keep_buffer`, off
  by default - one more connection to the provider; asked first over a full
  or unknown limit)
- Devices added by address: the Chromecasts and TVs added in the cast
  window ("Add a device by address…", `cast_added_devices`) and the Kodis
  added by hand (`kodi_devices`), each with Remove (adding is in the cast
  window, where the device is tried); link → Nerd › Network (other networks)

## CONTENT

**Sources** (`playlists`)
- (untitled): the sources (`playlists`, `active_playlist`): add (the quick
  form - presets, Find on my network, a media server's sign-in - then on to
  the new source's page), open its page (its ›, a double click, Edit),
  remove, use, drag to reorder; a search finds a source by name, kind or
  host
- Selected playlist: its account (expiry, connections), Refresh
- (untitled): link → Nerd › Network (identity, proxy, connection, own DNS,
  VPN guard, other networks); link → Nerd › Boxes and lists (boxes, picons, udpxy, failover,
  channel numbers)

**A source's own page** - not in the sidebar: opened in place of Sources
(or of Nerd › Boxes and lists, from a box's Options), with a way back, and
from the source menu's "Settings for this source…". Built in
`ui/source_page.py` from the fields of `ui/dialogs.py` (`SourceFields`) and
the Nerd options of `ui/source_nerd.py` and `ui/source_net.py`. Stored in
the source's entry in `playlists`, its own options under `options`; a
change a moment after it is made, the source in use connected again once
the page is left. A row with a shared setting says "Own for this source"
or "Shared" and where the shared one lives. Nerd's rows only while it is on.
- Header: initials, name, kind and account (expiry, connections); Test
  connection (Nerd)
- Connection: `name`, `kind`, `server` (Find on my network for a box or
  media server; a bare host gets the kind's port - VDR 8002, DVBViewer 8089,
  NextPVR 8866, MythTV 6544), `username` + `password` (NextPVR: `password`
  alone, named PIN), a media server's sign-in (`token`, options `user_id`,
  `server_id`, `connections`), `mac` (Stalker); a SAT>IP server's channel
  lists, option `m3u` (empty: the server's own), and where Find on my
  network found its description, option `description` (not shown)
- Network (Nerd, the whole tab): options `user_agent`, `referer`, `headers`,
  `proxy` (shared: Nerd › Network), `dns` + `dns_url`, `ip_family`,
  `backup_servers`, `cert_pin` + `cert_pin_for` (shared: the system's); the
  latest connection test this session (Copy)
- Streams: stream format → Playback (shared: `stream_format`); Nerd:
  `udpxy` (shared: Nerd › Boxes and lists), `catchup_mode`,
  `catchup_template`, `rewrite`; each kind's: `stream_port`, `transcode`,
  `transcode_more`, `stream_login`, `iptv_direct` (Enigma2), `profile`
  (Tvheadend), `transcode` (HDHomeRun), `streamdev_port` (VDR),
  `stream_port` (DVBViewer), `ms_quality` (media servers); a SAT>IP
  server's, for everyone: `transport` (RTSP over TCP - the default, not
  stored -, UDP or HTTP), `http_port` (with HTTP), `tuners` (0: as the
  server says)
- Guide: `epg_url`; Nerd: `epg_shift` (on top of Guide's shared
  `epg_delay_min`), `picons` (Enigma2), `cloud_guide` (HDHomeRun); picons →
  Nerd › Boxes and lists (shared: `nerd_picon_dir`); channel numbers
  (`nerd_chno_<source id>`) → Nerd › Boxes and lists; a channel's guide by hand
- Advanced: `refresh`; Nerd: `radio`, `recordings` (Enigma2, Tvheadend),
  `show_drm`, `hd_group` (HDHomeRun), `radio`, `recordings` (VDR, NextPVR),
  `favourites`, `recordings` (DVBViewer), `recordings` (MythTV)

**Mine** (`mine`) - the place that holds favourites, Watch later and
history (there is no Home since 1.6.2: its settings were carried over once
by `core/places.retire_home` - "Open Home at startup" became `start_place`
= mitt, a popular shelf switched off stays off - and taken away)
- Followed series: `follow_notify`; the followed series, stop following

**Local files** (`local`)
- Folders: `local_dirs`
- Music: `music_art_online`

**Posters & info** (`metadata`)
- (untitled): `metadata_source`, `tmdb_api_key` (Test)
- Popular right now (TMDB's chart, what the source has), at the top of:
  `popular_movies` (Movies), `popular_series` (Series)

## SERVICES

**Watch history** (`history`)
- Trakt: account (this profile's: `trakt_access_token` and its kin; connect
  in the browser, disconnect), watchlist and history
- Simkl (only in a build with its client id): account (this profile's:
  `simkl_access_token`; PIN sign-in), lists
- Watched history: last sync, Sync now - one store, Trakt's and Simkl's

**Sync between computers** (`sync`) - "Sync" in the sidebar, for everyone
- (untitled): start or join, what syncs (`sync_cats`), the code, Sync now,
  stop
- Encrypted, and nothing about you: what dopeiptv.org gets, in plain words

**Integrations** (`integrations`)
- OpenSubtitles.com: `os_api_key` (Test), `os_username` + `os_password`
- Discord (only in a build with its application id): `discord_presence`,
  `discord_presence_detail`
- radio-browser.info: `radio_report_clicks`

## SYSTEM

**General** (`general`)
- Language: `language`
- Start: `start_place` (where you left off - each profile's own
  `last_place` / `last_list_place`, no row - or always on Mine)
- Privacy: private viewing (until the app closes, not stored), clear recent history
- Updates: `check_updates`, `update_channel`
- Backup: back up, restore
- System: `desktop_entry` (Linux), `mpris` (the system's media controls)
- TV mode: Open TV mode, `tv_mode_start`, `tv_inputs` (game controllers and
  LIRC), the remotes and controllers found
- Support dopeIPTV: feedback and its answers, `supporter_key` (its window),
  `supporter_icon`, ask for a donation now and then (`donate_ask`; the
  household's - its rhythm and snooze are `donate_until`, `donate_period`,
  `donate_first_seen`, not settings)
- (no heading) About dopeIPTV with the version, a link row that opens About
  over Settings (no key; found as "about", "version", "update")

**Appearance** (`appearance`)
- Theme: `theme`, `accent` (+ `accent_custom`), `theme_follow_system`,
  `theme_light` (these four this profile's), `corner_style`, `ui_scale`
- Lists: `view_density`, `sort_order`

**Keyboard shortcuts** (`shortcuts`)
- Keyboard shortcuts: `keyboard_shortcuts` (every rebindable action's key)

**Profiles** (`profiles`) - one per person in the household, a service of
its own (`ui/profiles_ui.py`, `ui/mw_profiles.py`)
- Profiles: the household's (`profiles_list`: name, avatar colour or emoji,
  PIN, kids), each one's Edit (its window) and Remove (asked first, never
  the one in use); add a profile (up to 8)
- At start: `profile_ask_start` (ask who's watching; off: the profile used
  last, `profile_active` - one with a PIN asks anyway)
- What a profile keeps: its own and the household's, in words (no key)

**Parental lock** (`parental`)
- PIN: `parental_pin_hash` + `parental_salt` (set, remove), lock now

**Advanced** (`advanced`)
- Maintenance: reset broken .ts streams, Start-menu shortcut (Windows);
  link → Nerd › Diagnostics (log detail, log file, stats for nerds, stream health)
- Caches: the image cache, the local files' caches
- System (Linux): `force_x11`
- Reset: reset all settings

## NERD

Status is always there (it holds the switch); the other five pages only
while Nerd is on, and so are the link rows that lead to them.

**Status** (`nerd`)
- (untitled): `nerd_enabled`
- What's in Nerd: its pages, what is changed on each
- How Nerd works

**Network** (`nerd_net`)
- Who the app says it is: `nerd_user_agent`, `nerd_referer`, `nerd_headers`
- Proxy: `nerd_proxy`, `nerd_proxy_player`
- Connection: `nerd_ip_family`, `nerd_net_timeout`, `nerd_reconnect_max`
- Own DNS: `nerd_dns`, `nerd_dns_url`, `nerd_dns_fallback`
- VPN guard: `nerd_vpn_guard`, `nerd_vpn_iface`, `nerd_vpn_lan`
- Other networks (VLANs): `nerd_other_networks` (the parts of a split home
  network that "Find on my network" and the cast window ask by unicast);
  Search now

**Boxes and lists** (`nerd_boxes`)
- Boxes and servers: each box source's options (its dialog), add a box
- Channel logos (picons): `nerd_picon_dir`, `nerd_picon_override`
- Multicast (udpxy): `nerd_udpxy`
- Failover: `nerd_failover`
- Guide: how to give a source several guides, a channel its guide by hand
- Channel numbers: `nerd_numbers_keep`; number all, clear (`nerd_chno_<source id>`)

**Player** (`nerd_player`)
- Picture and sound: `nerd_spdif`, `nerd_video_sync`, `nerd_interpolation`
- Channel start and buffer: `nerd_probe`, `nerd_demux_max_mb`, `nerd_adelay_per_channel`
- Low latency: `nerd_low_latency`
- Adaptive streams (HLS): `nerd_hls_quality`, `nerd_hls_max_kbps`
- mpv: `nerd_mpv_config_dir`, `nerd_shaders`, `nerd_mpv_options`

**Remote and automation** (`nerd_serve`)
- Remote control: `nerd_remote`
- Share with other players: `nerd_share`; copy the playlist and guide addresses
- Who can reach it: `nerd_remote_lan`, `nerd_remote_port`, `nerd_remote_token`
- Server for the home (server mode, `services/home_server.py`): `nerd_server`,
  `nerd_server_tuners` (0: the account's limit), `nerd_server_port`,
  `nerd_server_sources` (the sources as chips; the categories in each are
  chosen on the server's page in a browser, which also changes the port and
  the tuners); open the page, copy the tuner's and the playlist's
  addresses, how it stands. Not shown: `nerd_server_token`,
  `nerd_server_device_id` (made once, never in a backup)
- Webhooks: `nerd_hooks`, `nerd_hooks_urls`, `nerd_hooks_header`
- MQTT: `nerd_mqtt`, `nerd_mqtt_host`, `nerd_mqtt_port`, `nerd_mqtt_tls`,
  `nerd_mqtt_user` + `nerd_mqtt_pass`, `nerd_mqtt_client`, `nerd_mqtt_prefix`,
  `nerd_mqtt_commands`, `nerd_mqtt_ha`
- Recording file names: `nerd_rec_template`
- Recording folder: a check of the folder set under Recording
- When a recording is done: `nerd_rec_post`

**Diagnostics** (`nerd_diag`)
- (untitled): `nerd_log_level`, `nerd_log_file`, the log folder, the
  diagnostics to copy, how the video is drawn (own thread, or the window's
  and why; no key), Test connection, Stream analysis (opens its window
  over the player; Settings closes)
- Stats for nerds: `nerd_overlay_more`
- Stream health: `nerd_health`; the worst channels, clear
- In use now (read-only, no key): the account's connections and each box
  source's tuners, what each is used for
