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

## WATCH

**Playback** (`playback`)
- Behaviour: `autoplay_preview`, `autoplay_next_episode`, `nextup_lead_secs`,
  `auto_reconnect_live`, `hide_upcoming_prompt` (inverted), `night_mode`
- Stream and buffer: `stream_format`, `cache_secs`; link → Nerd › Player
  (channel start, buffer memory, HLS, low latency)
- YouTube, Twitch and similar sites: `ytdlp_managed`; which yt-dlp is used,
  Download / Update now (`ytdlp_checked`: the last look for a newer one)
- Rewind live TV: `live_rewind_minutes`, `live_rewind_disk`,
  `live_rewind_kept`, `live_rewind_space_gb`; where the buffer is (Open folder)
- (only without the embedded player) why playback opens in mpv

**Picture & sound** (`audio`)
- Audio & subtitles: `audio_lang`, `sub_mode`, `sub_lang`, `sub_lang2`
- How subtitles look: `sub_scale`, `sub_color`, `sub_background`
- Video: `aspect_mode`, `video_deinterlace`, `video_sharpen`, `video_tonemapping`
- Decoding: `hwdec_mode`; link → Nerd › Player (passthrough, video sync,
  interpolation, audio delay per channel, mpv)

**Guide** (`guide`)
- (untitled): `epg_upcoming_count`
- Time offsets: `replay_delay_min`, `epg_delay_min`
- Guide data: Refresh guide, Clear guide cache

**Multiview** (`multiview`)
- Window: `mv_frameless` (inverted), `mv_on_top`, `mv_remember_geo`, `mv_cells`
- Behavior: `mv_stop_docked`, `mv_new_unmuted`, `mv_conflict_mode`
- Controls: `mv_autohide_secs`, `mv_seek_step_min`, show the tips again (`mv_info_seen`)

**Recording** (`recording`)
- Folder: `recordings_dir`, `rec_save_mp4`; link → Nerd › Remote and
  automation (file names, what runs after)
- Limits: `rec_max_value` + `rec_max_unit`, `rec_total_value` + `rec_total_unit`
- Padding: `rec_pad_before_min`, `rec_pad_after_min`
- Series recordings: the rules (`series_rules_<source id>`), add a word, delete;
  on an Enigma2 box with AutoTimer also the box's own rules (kept on the box,
  no key here) - new, edit, on/off, preview, run now - each row saying who
  records it
- (untitled): which recorder is used, how to record

**Casting** (`casting`) - a page of one row for now; AirPlay's rows join it,
and Chromecast, DLNA and Kodi rows belong here
- Chromecast: `cast_pause_gb`

## CONTENT

**Sources** (`playlists`)
- (untitled): the sources (`playlists`, `active_playlist`): add (the quick
  form - presets, Find on my network, a media server's sign-in - then on to
  the new source's page), open its page (its ›, a double click, Edit),
  remove, use, drag to reorder; a search finds a source by name, kind or
  host
- Selected playlist: its account (expiry, connections), Refresh
- (untitled): link → Nerd › Network (identity, proxy, connection, own DNS,
  VPN guard); link → Nerd › Boxes and lists (boxes, picons, udpxy, failover,
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
  media server), `username` + `password`, a media server's sign-in (`token`,
  options `user_id`, `server_id`, `connections`), `mac` (Stalker)
- Network (Nerd, the whole tab): options `user_agent`, `referer`, `headers`,
  `proxy` (shared: Nerd › Network), `dns` + `dns_url`, `ip_family`,
  `backup_servers`, `cert_pin` + `cert_pin_for` (shared: the system's); the
  latest connection test this session (Copy)
- Streams: stream format → Playback (shared: `stream_format`); Nerd:
  `udpxy` (shared: Nerd › Boxes and lists), `catchup_mode`,
  `catchup_template`, `rewrite`; each kind's: `stream_port`, `transcode`,
  `transcode_more`, `stream_login`, `iptv_direct` (Enigma2), `profile`
  (Tvheadend), `transcode` (HDHomeRun), `ms_quality` (media servers)
- Guide: `epg_url`; Nerd: `epg_shift` (on top of Guide's shared
  `epg_delay_min`), `picons` (Enigma2), `cloud_guide` (HDHomeRun); picons →
  Nerd › Boxes and lists (shared: `nerd_picon_dir`); channel numbers
  (`nerd_chno_<source id>`) → Nerd › Boxes and lists; a channel's guide by hand
- Advanced: `refresh`; Nerd: `radio`, `recordings` (Enigma2, Tvheadend),
  `show_drm`, `hd_group` (HDHomeRun)

**Home** (`home`)
- (untitled): `home_enabled`, `home_start`
- Sections on Home: `home_sh_resume`, `home_sh_new_eps`, `home_sh_fav`,
  `home_sh_history`, `home_sh_watchlist`, `home_sh_hero`, `home_sh_pop_movies`,
  `home_sh_pop_series`, `home_sh_movies`, `home_sh_series`, `home_sh_channels`
- Followed series: `follow_notify`; the followed series, stop following

**Local files** (`local`)
- Folders: `local_dirs`
- Music: `music_art_online`

**Posters & info** (`metadata`)
- (untitled): `metadata_source`, `tmdb_api_key` (Test)

## ACCOUNTS AND INTEGRATIONS

**Watch history** (`history`)
- Trakt: account (connect in the browser, disconnect), watchlist and history
- Simkl (only in a build with its client id): account (PIN sign-in), lists
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
- Privacy: private viewing (until the app closes, not stored), clear recent history
- Updates: `check_updates`, `update_channel`
- Backup: back up, restore
- System: `desktop_entry` (Linux), `mpris` (the system's media controls)
- TV mode: Open TV mode, `tv_mode_start`, `tv_inputs` (game controllers and
  LIRC), the remotes and controllers found
- Support dopeIPTV: feedback and its answers, `supporter_key` (its window),
  `supporter_icon`, the sidebar heart (`donate_heart_until`)

**Appearance** (`appearance`)
- Theme: `theme`, `accent` (+ `accent_custom`), `theme_follow_system`,
  `theme_light`, `corner_style`, `ui_scale`
- Lists: `view_density`, `sort_order`

**Keyboard shortcuts** (`shortcuts`)
- Keyboard shortcuts: `keyboard_shortcuts` (every rebindable action's key)

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
- Webhooks: `nerd_hooks`, `nerd_hooks_urls`, `nerd_hooks_header`
- MQTT: `nerd_mqtt`, `nerd_mqtt_host`, `nerd_mqtt_port`, `nerd_mqtt_tls`,
  `nerd_mqtt_user` + `nerd_mqtt_pass`, `nerd_mqtt_client`, `nerd_mqtt_prefix`,
  `nerd_mqtt_commands`, `nerd_mqtt_ha`
- Recording file names: `nerd_rec_template`
- Recording folder: a check of the folder set under Recording
- When a recording is done: `nerd_rec_post`

**Diagnostics** (`nerd_diag`)
- (untitled): `nerd_log_level`, `nerd_log_file`, the log folder, the
  diagnostics to copy, Test connection, Stream analysis (opens its window
  over the player; Settings closes)
- Stats for nerds: `nerd_overlay_more`
- Stream health: `nerd_health`; the worst channels, clear
- In use now (read-only, no key): the account's connections and each box
  source's tuners, what each is used for
