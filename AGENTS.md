# AI Agent Guide

This document helps any AI coding assistant - whichever model or tool it
is (Claude Code, Codex, Gemini CLI, Copilot, Cursor, Aider, a local
model, ...) - and any new human contributor work effectively with the
dopeIPTV codebase.  It is the single source of these instructions:
`CLAUDE.md`, `GEMINI.md` and `.github/copilot-instructions.md` only point
here.  Each module is kept small enough to fit in a 14-16B model's
context window.

**Start here:**

1. This file - what each module owns, the conventions, the quality gate.
2. [`docs/MAINTAINING.md`](docs/MAINTAINING.md) - branches, CI, how a
   release is cut, the website, keys and accounts, open work.
3. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and
   [`docs/DEVELOPER_NOTES.md`](docs/DEVELOPER_NOTES.md) - how the parts
   fit together and why.

**Four rules above all others:** stability first - never break video
playback; Linux is the primary platform and a change for macOS or
Windows must not make Linux worse; one connection - assume the account
allows one connection to the provider (nearly every account does), so
nothing may open a second one by itself: a feature that needs one is off
until the person turns it on knowing what it costs, and asks first when
the limit is full or unknown; run the quality gate yourself before you
say something works.

## Module responsibilities

Modules are grouped into layered subpackages (`providers/`, `core/`,
`media/`, `services/`, `ui/`).  See `docs/ARCHITECTURE.md` for the full
map and the one-way import rule.

| Module | What it owns |
|--------|--------------|
| `app.py` | Entry point, QApplication, login loop |
| `i18n.py` | English source strings, `set_language`, `tr()` (other 26 languages in `locale/*.json`) |
| `providers/client.py` | Xtream Codes API client |
| `providers/stalker.py` | Stalker / Ministra portal client (MAC portals: handshake, create_link, the archive by programme: `archive_link`, `archive_table`) |
| `providers/boxes.py` | `BoxClient`: what boxes on the home network share (login, catalogue, stable ids, recordings as films, the box's guide as XMLTV via `own_guide`) |
| `providers/enigma2.py` | Enigma2 receivers (VU+, Dreambox, Zgemma...) over the classic `/web/` API of OpenWebif and the Dreambox WebInterface; the box itself: tuner signal (`signal`), screenshot (`/grab`), remote keys (`KEYS`, `remote`), its recordings renamed/moved/deleted, transcoding on port 8002 brought to what `/transcoding` accepts (`play_url`), a channel it cannot decrypt (`scrambled`); for the transponder check its tuners, timers and TV (`tuner_state`, off the window's thread) and a channel's transponder (`tuner_key`, no request); its AutoTimer (`autotimer()`), the bouquets' channels (`bouquet_channels`), a service's channel id (`stream_of`) |
| `providers/autotimer.py` | An Enigma2 box's AutoTimer plugin (checked against its source and OpenWebif's `controllers/AT.py`, in the docstring): the rules read (`/autotimer`, attributes it does not know kept), made and changed (`/autotimer/edit` sends only the editor's fields, so the box keeps the rest), on/off (`/autotimer/change`, else the edit), removed, Run now (`/autotimer/parse`), the preview (`/autotimer/simulate`), a box without the plugin said plainly (`Rules.present`); what the box records itself (`records`: its timers and a simulate, at most every 30 min) |
| `providers/tvheadend.py` | Tvheadend servers (channel tags, stream profiles, XMLTV, DVR recordings, digest login) |
| `providers/hdhomerun.py` | HDHomeRun tuners and their emulators (discover/lineup, SiliconDust guide, UDP discovery); a source by its address works on any part of the network - the lineup from the typed address, an emulator's loopback stream address put on it (`_reachable`) |
| `providers/radio_browser.py` | Internet radio from radio-browser.info: its servers from the DNS round robin (`all.api.radio-browser.info`), the next one when one does not answer, stations, countries, languages, genres, search, a click (only when the person turned it on); only `dopeIPTV/<version>` in the user agent |
| `providers/lan_scan.py` | "Find on my network": the HDHomeRun, Jellyfin/Emby (UDP 7359) and Plex (GDM) broadcasts plus a probe of the local /24 and of the other networks Nerd lists (`core/home_net.py`, by unicast) - every address and port swept at once (`open_ports`, `sweep`: a bounded number in flight within the file limit, short timeouts, IPv6 too, cancelable), then only what answered, on its open ports; each kind told by its own answer on its port (VDR's restfulapi 8002 or streamdev 3000, DVBViewer 8089, NextPVR 8866, MythTV 6544 among them); what can be cast to there (`scan_cast`: a Chromecast's eureka_info on 8008, Kodi's JSON-RPC on 8080, a DLNA TV by unicast M-SEARCH or its description paths, `dlna_at`); the Nerd page's Test (`check_networks`) |
| `providers/vdr.py` | VDR through restfulapi (port 8002: channels with their groups and logos, the guide per channel, recordings, timers) with streams from streamdev-server (`streamdev_port`, 3000: `/TS/<channel id>`, recordings by device:inode); streamdev alone: its `channels.m3u`, no guide (`guide_note`); streamdev missing from VDR's plugins said (`live_note`) |
| `providers/dvbviewer.py` | DVBViewer Media Server (Recording Service): its web API's XML (port 8089, basic login) - channel list roots/groups, favourites as groups, EPG per channel, recordings, `timeradd`; streams from its HTTP/UPnP side (`stream_port`, 7522); local times and Delphi day numbers (`local_time`, `ole_day`) |
| `providers/nextpvr.py` | NextPVR v5+ (port 8866): the session API (`session.initiate`, login with md5(":" + md5(PIN) + ":" + salt), `login_digest`), channel.list/groups/listings, recording.list, recording.save with the server's padding; live and recording addresses with the session id, a lost session logged in again once |
| `providers/mythtv.py` | MythTV backends through the Services API (JSON, port 6544): channels and channel groups, GetProgramGuide in half days, GetRecordedList played from `/Content/GetRecording`, AddRecordSchedule; no live TV over HTTP - channels listed for the guide without an address (`no_live`) and `live_note` says why |
| `providers/mediaserver.py` | `MediaServerClient`, what Jellyfin, Emby and Plex share on `BoxClient`: a token (never a password), one device id per install (`device_id`, never in a backup), films/series/episodes/channels as the app's rows with the server's artwork, resume points and watched marks (`_server_pos`, `_played`), the server's Continue watching (`resume_items`), reports and marks back on a thread of its own (`report`, `mark`) |
| `providers/jellyfin.py` | Jellyfin and Emby (one client; Emby's `/emby` prefix and `X-Emby-*` headers): AuthenticateByName, Quick Connect, libraries, Live TV opened by `PlaybackInfo`, its guide as XMLTV, direct file or HLS (`master.m3u8`), `/Sessions/Playing` reports, UDP 7359 discovery |
| `providers/plex.py` | Plex: the PIN sign-in (plex.tv, app.plex.tv in the browser), the account's servers and their best connection, libraries, direct play or the HLS transcoder, the timeline, scrobble; Live TV/DVR when the server has it (`live_note` says why not), GDM discovery |
| `providers/epg.py` | EPG/XMLTV parser, cache and compact index |
| `providers/epg_index.py` | The guide parser as a process of its own (`--epg-index`) |
| `providers/metadata.py` | TMDB client, PosterResolver |
| `providers/trakt.py` | Trakt.tv sync |
| `providers/chromecast.py` | Chromecast support; Chromecasts known by their address (`set_hosts`, pychromecast's `get_chromecast_from_host`, listed after every scan, one mDNS finds at the same address not twice); the cast dialog (its "Add a device by address…", the nothing-found line `none_found_text`) |
| `providers/dlna.py` | DLNA / UPnP TVs (discovery, control, local file share); UPnP 1.1's unicast M-SEARCH to given addresses (`ssdp_unicast`), where renderers keep their description (`DESCRIPTION_PATHS`), renderers known by their address listed after every scan (`set_extra`), a renderer kept in the settings (`Renderer.to_dict`/`from_dict`) |
| `providers/casting.py` | `CastHub`: Chromecast, DLNA and Kodi behind one object (`plays_as_is`, `ended_elsewhere`), and receivers the window adds (`add_receiver`: AirPlay on macOS); the devices added by address and (Nerd) what answers on the other networks, handed to their managers (`add_by_address`, `forget_added`, `is_added`, `stored`), the other networks asked beside the searches |
| `providers/cast_devices.py` | Devices to cast to added by their address (another part of a network split in VLANs): what was typed (`parse_address`: an address, a port, a DLNA description URL), what is there (`detect`: Chromecast, Kodi, DLNA, or the kind chosen), the Chromecasts and TVs kept (`cast_added_devices`, a JSON list; a Kodi goes to `kodi_devices`) |
| `providers/kodi.py` | Kodi as a cast target over JSON-RPC (HTTP): Player.Open with the source's user agent/headers as Kodi's `\|` options (never a loopback address), pause, seek, volume, state polling, a cast ended on Kodi itself; Kodis added by hand with their login (`kodi_devices`, never logged) |
| `providers/mdns.py` | mDNS service discovery in plain Python: one legacy-unicast PTR question, PTR/SRV/A/TXT read back (how Kodi's `_xbmc-jsonrpc-h._tcp` is found) |
| `providers/simkl.py` | Simkl client: AUTH V2's device sign-in (`/oauth2/device`, then `/oauth2/token` polled; a 7-day access token renewed shortly before it runs out or once on a 401 with the sliding 180-day refresh token; sign-out revokes), scrobble start/pause/stop, watched marks, Plan to watch, the account's lists read back (one all-items answer); client id public (`PUBLIC_CLIENT_ID`) or baked at build time (`bundled_client_id`, `_simkl_key.py`), tokens `simkl_access_token`, `simkl_refresh_token`, `simkl_token_expires` |
| `providers/airplay_bridge.py` | AirPlay's plan and bridge, platform-neutral: what goes to an Apple TV as it is (HLS/MP4 it decodes), the rest repackaged by ffmpeg as HLS (fMP4, copy H.264/HEVC and AAC/AC-3/E-AC-3, other sound to AAC, MPEG-2 and other pictures to H.264), served on the LAN under a token; the source's user agent/headers/proxy; the macOS firewall's answer |
| `providers/cast_bridge.py` | Re-encodes audio a Chromecast refuses (E-AC-3) so the stream still casts |
| `providers/cast_feed.py` | A cast's archive or rewind buffer in pieces, read here into one converter run (`Feed`): one growing playlist on the TV, keyframe starts, the buffer's loopback address never handed out |
| `providers/opensubtitles.py` | OpenSubtitles.com client (search, download, file hash) |
| `core/stores.py` | Data persistence layer |
| `core/workers.py` | Thread pool helpers, LogoLoader, image caches; `fetch_file`, one image into the disk cache on the caller's thread (the phone remote's logos: timeout, size cap, image types, the same cooldowns) |
| `core/local_buffer.py` | The seekable window of a live channel's rewind buffer (DVR row) |
| `core/dvr_store.py` | The app-kept rewind buffer's rolling segment store (retention, readers) |
| `core/dvr_ingest.py` | Its one provider connection (reconnects as runs, gives up cleanly) |
| `core/dvr_server.py` | Its loopback server mpv plays from (token, live TS) |
| `core/dvr_session.py` | `DvrSession`: ingest, store and server for one live channel |
| `core/dvr_pool.py` | `DvrPool`: every channel's buffer this session (frozen history, one space budget) |
| `core/dvr_record.py` | `BufferRecorder`: a recording read from a buffer, no connection of its own |
| `core/recording.py` | Recording manager; a finished recording saved as MP4 off the window's thread (`_remux`, the command after it with the final file) |
| `core/rec_mp4.py` | Recordings as MP4: the remux (stream copy, faststart, `.mp4.part` until it passed), what ffmpeg says of a file, when the MP4 is plausibly whole (streams, length, size), the .ts deleted only then (a recording with teletext or subtitles left a .ts on request, `rec_mp4_keep_subs`); the journal of remuxes left for the next start |
| `core/updates.py` | GitHub latest-release check; none from a modified build (`build_check.refuse`), the site asked with `site_headers` (the build's kind) |
| `core/selfupdate.py` | AppImage in-place update (download, checksum, self-check, swap); every update's signed checksum check (`signed_asset`) |
| `core/winupdate.py` / `core/macupdate.py` | The same for an installed Windows copy (the installer) and the Mac app (the .dmg) |
| `core/ed25519.py` | Ed25519 signatures in plain Python (the release key) |
| `core/update_keys.py` | The public half of dopeIPTV's release signing key (`tools/make_update_key.py`, `tools/sign_sums.py`, `tools/build_manifest.py`) |
| `core/build_check.py` | Official, modified or from source: the signed build manifest of a package's own files (`_build_manifest.json` + `.sig` beside the package in the bundle, never in git; `tools/build_manifest.py` makes it in release.yml through the bundle's own `--build-files`), a frozen build's modules read from its PyInstaller archive and its data files from disk, changed / extra code / missing files named (capped), checked once on a thread of its own at start and kept (`start`, `verdict`, `kind`); `allowed`, `refuse`, `notice` and the `X-dopeIPTV-Build` header (`headers`) for dopeiptv.org's services; it only says |
| `core/wakelock.py` | Screensaver inhibitor |
| `core/gcguard.py` | Cyclic garbage collection on the GUI thread only |
| `core/contest.py` | `ContestWatch`: tells another device taking the account's connection from repeated drops |
| `core/connections.py` | Connections to the account (for everyone): what user_info says (`numbers`), which streams count (`is_network`), the app's own (`Use`: player, rewind buffer, recording, multiview cell, cast) against the limit, another device's from the provider's last answer while fresh (`Ledger`), the `Verdict` when one more would go over; it only counts |
| `core/tuners.py` | An Enigma2 box's tuners, on the same count: a service reference's transponder (`transponder`: TSID:ONID:namespace; IPTV and other addresses none, the box's own stream relay the channel it relays), the tuners `/web/about` lists (`e2nim`, `e2frontend`), the running recordings in `/web/timerlist`, what `/web/getcurrent` shows on the TV, the `Verdict` (free / the last free tuner / it would cut off the TV, a stream of the app's own or a recording - and which; a busy transponder is free), each box's state kept a few seconds (`StateCache`); it only says |
| `core/mirrors.py` | Nerd: a source's backup addresses (`backup_servers` in its options, one per line; bad lines refused with their reason), when to try the next (unreachable, TLS, 5xx/52x - never 401/403/404), the one in use per source (`nerd_mirror_<source id>`), `Picker`; its line and Test |
| `core/home_net.py` | A home network split in parts (VLANs, subnets): the other networks Nerd searches (`nerd_other_networks`: a network, a range or an address, one or more per line; home addresses only, a /22 at most per entry, 2048 in all; a bad line refused with its reason, `check_list`), the addresses they come to (`addresses`, `configured` - only while Nerd is on); who counts as at home for the remote API and the phone remote (`is_home_client`: every private range, link-local, IPv6 ULA, this computer's own address - never a public one) |
| `core/conn_test.py` | Nerd: Test connection - DNS, TCP, TLS, login (Xtream's account end and connections), a live channel's first byte and speed against SD/HD/Full HD/4K, step by step with times, on a thread of its own, cancelable; `copy_text` scrubbed by `core/report.py` |
| `core/health.py` | Nerd's stream health per channel (source + stream id): starts, failures, time to picture, drops, last error (scrubbed), last played; one settings value, 500 channels at most; it only watches |
| `core/desktop_entry.py` | The user's .desktop entry and icons (Wayland takes the window icon from there) |
| `core/xdg.py` | Launch system programs from a frozen bundle without our bundled libraries |
| `core/tv_input.py` | TV mode's input as actions ("up", "ok", "back", "ch_up", "red", "digit_5"...): LIRC's key names through lircd's socket, a game controller through SDL2 (pysdl2, optional, never a requirement) or Linux evdev (`/proc/bus/input/devices`, readable pads only), each on a thread of its own and quiet when missing (`InputHub`, `detect`); the same actions and TV mode on/off as the phone remote, the remote API and Home Assistant ask them (`remote_action`, `switch_mode`) |
| `core/tv_nav.py` | TV mode's focus moves: the nearest element an arrow goes to (`pick`, plain rectangles), a cell's neighbour in a grid (`grid_step`) |
| `core/ytdlp.py` | yt-dlp for YouTube, Twitch and the like: which addresses go through it, the system's or the app's own copy (`ytdlp_managed`, the user's data folder), the official release binary downloaded and checked against SHA2-256SUMS, tried, updated at most once a day while used; `resolve` for a libmpv without the ytdl hook; the wrapper a frozen build runs it through. Never in our packages |
| `core/audiotags.py` | Artist/album/title and cover art from music files, header-only |
| `services/mpris.py` | MPRIS media session on the session bus (Linux) |
| `services/media_session.py` | The system's media controls on every desktop: the window's one host (`mpris_snapshot` / `mpris_command`), the session for this system (`create_session`: MPRIS on Linux, Now Playing on macOS, the SMTC on Windows), the poll and the system's commands queued onto the GUI thread (`PolledSession`); a missing bridge logs once at debug; the self-check's `bundle_problem` |
| `services/media_macos.py` | macOS Now Playing through PyObjC: MPNowPlayingInfoCenter (title, programme or series, artwork from the image cache, live or a title's length and position, never kept for suggestions) and MPRemoteCommandCenter (play/pause, next/previous channel or episode, skip and scrub for a title) |
| `services/media_windows.py` | Windows' System Media Transport Controls through PyWinRT: an idle MediaPlayer's controls with its command manager off, the buttons (channel up/down for a channel), the timeline, the thumbnail written from the image cache into a stream in memory |
| `services/airplay_macos.py` | AirPlay to an Apple TV on macOS, as Apple documents it: an AVPlayer with external playback, its AVPlayerLayer and AVKit's route picker (`player` set) in a small NSPanel of their own, made once per cast (never an NSView in a Qt widget; the user clicks Apple's button); the route debounced, never playing on the Mac; AVRouteDetector for the dialog's entry; KVO, notifications and the picker's delegate queued onto the GUI thread, a 1 s tick; each step logged; the CastHub receiver the strip drives; the self-check's `bundle_problem` |
| `services/notify.py` | Desktop notifications over D-Bus (Linux) |
| `services/popular.py` | Popular right now: TMDB's weekly chart matched to the provider's catalogue |
| `core/log.py` | Central logging (`configure_logging`, `DOPEIPTV_LOG`) |
| `core/nerd.py` | Nerd settings into what the player and network do (mpv options, user agent/headers/proxy per source, IP version, VPN guard); nothing while Nerd is off |
| `core/nerd_lists.py` | Nerd on a source's lists: local picon folder, a channel's guide chosen by hand, udpxy for multicast |
| `core/channel_numbers.py` | Nerd: channel numbers of the user's own per source (`nerd_chno_<source id>`), numbering in list order, a number's channel; they win over the source's `num` |
| `core/channel_prefs.py` | Nerd: settings of a channel's own (`nerd_channel_prefs`, by source + stream id): audio/subtitle language, deinterlace, aspect, audio delay (the old `nerd_adelay/` keys move in on read); what each sets in mpv; applied only while Nerd is on |
| `core/shortcut_keys.py` | Keyboard shortcuts: the rebindable actions and the app's keys, the user's over them always (`keyboard_shortcuts`, portable text), never two actions on one key, the keys the app keeps, the old editor's keys carried over |
| `core/palette_match.py` | The command palette's matching: folded text (case, accents, punctuation left out), word starts weigh more, an `Index` folded once for tens of thousands of channels |
| `core/doh.py` | Nerd's own DNS: names looked up over HTTPS (RFC 8484 wire format, or the JSON API), the presets by IP (Cloudflare, Quad9, Google), answers kept for their TTL |
| `core/net_route.py` | A source's own way onto the network (`Route`): its DNS, IP version (per source: IPv4 / IPv6 only, prefer IPv6) and pinned certificate from its options and the Nerd page; `open_connection` as it says (home names and proxies left to the system); its checks |
| `core/net_adapter.py` | The app's own requests on a source's route: a requests adapter that connects to the address own DNS gives under the request's name (Host, SNI, certificate check unchanged) and checks a pinned certificate by fingerprint; mounted by `make_client`, used by the guide, logos and the rewind buffer |
| `core/net_bridge.py` | The player's and recordings' way onto a route: a loopback proxy (CONNECT for https, so ffmpeg keeps the stream's name for TLS) that looks names up as the route says and checks a pinned certificate before a tunnel opens - that check is the trust for the host, so the player and recorder do not check its chain (`checks_pin`, `recorder_options`); on a route with a pin every other https host checked against the system's authorities before its tunnel (`_check_trust`, a redirect or a segment elsewhere) |
| `core/cert_pin.py` | Trusting one certificate for one source: read without trusting (a DER reader of its own), subject / issuer / expiry / SHA-256, the pin in the source's options, a changed one noted for the window (`PinMismatch`, `mismatch`); its check |
| `core/live_latency.py` | Nerd's low latency for live: smaller buffer, quick probe, HLS from the newest segment - set before a live channel from the provider opens, put back before anything else (never the rewind buffer, catch-up, recordings, films); its check |
| `core/nerd_check.py` | Does a Nerd setting work? Each tried for real (proxy, user agent, IP version, udpxy, picons, mpv folder, shaders, mpv options, VPN), no secrets in what it shows |
| `core/http_login.py` | `get_with_login`: a login in a stream's address answered as the server asks (basic or digest) |
| `core/commands.py` | What the app is asked from outside: `--play`, `--channel`, `dopeiptv://` links; a `file://` address as its path, a Windows drive or network path too (`file_url_path`) |
| `core/instance.py` | A second start hands its command to the copy running (local socket, the user's alone; `$XDG_RUNTIME_DIR` on Linux) |
| `core/privacy.py` | Private viewing (nothing kept until the app closes; `for_log` keeps titles out of the log; a playback that was private stays so to its end for every reporter, `playback_private`) and Clear recent history by time range, for every source (the viewing statistics' time too) |
| `core/night_mode.py` | Night mode for the sound: the one audio filter (`dynaudnorm`, its values explained), put on and taken off the core by its own label; the setting |
| `core/profiles.py` | Profiles, one per person in the household: the split declared in one place (`OWN_KEYS`, `OWN_FAMILIES`, `OWN_PREFIXES`, `OWN_STORES`; the rest is shared), a profile's own keys under `profiles/<id>/` in the same four files, `ProfileSettings` (the app's settings as the profile in use sees them; `raw()` the file itself), `pinned()` (a store's view, kept to its profile past a switch), `ProfileStore` (`profiles_list`: name, colour from the theme's ACCENTS or an emoji, a 4-digit PIN hashed as the parental PIN, kids), the first start (`migrate`: everything copied into `me`, the old keys left for a downgrade), what a kids profile never sees (`adult_category`, `adult_item`) |
| `core/viewing_stats.py` | Your statistics: the log of seconds watched per title and hour (`viewing_stats`, this computer only, never synced; private viewing adds nothing), seeded once from the resume positions; the sums (week/month/year, weekday, hour, top channels/movies/series, streaks) |
| `core/rec_extras.py` | Nerd for recordings: file name template (folders, never outside), the command after a recording (no shell), their checks |
| `core/catchup.py` | Nerd: a source's own catch-up address - Kodi's placeholders and the M3U modes (default, append, shift, flussonic), tried before the app's own; its checks |
| `core/series_rules.py` | Series recordings: rules (exact title or a word, one channel or any; record or remind), the guide looked through for their airings, the plan (padding, nothing twice, one copy of a programme on two channels, a cancelled one not again), the reminders' plan (at the programme's start, none twice, a removed one not again, moved with its programme), their store; recording padding; what the box records itself left to it (`plan`'s *box*, `BOX`, `left_to_box`, the store's `forget`) |
| `core/url_rewrite.py` | Nerd: a source's rewrite rules for its stream addresses (find -> replace, plain or regex, in order; bad ones refused, skipped in playback), applied by `make_client` to every address it hands out; the preview line and its Test |
| `core/ts_scramble.py` | Is a transport stream encrypted? Its first packets' transport_scrambling_control bits (`verdict`) |
| `core/ts_analysis.py` | Nerd: a simple TR 101 290 (`Analyzer`): sync, PAT/PMT (PIDs, stream types named), the SDT's service, bitrate per PID over a sliding window, continuity errors (a discontinuity respected, a duplicate allowed once), PCR interval and jitter against arrival (an estimate), PAT/PMT/PTS repetition, scrambled PIDs, null share; priorities 1 and 2 counted by their TR names, in the stream's own time; bounded; `Runner` reads a live channel's rewind buffer on a thread of its own, its work capped - never a second connection |
| `core/teletext.py` | Teletext in plain Python: TS -> PES (EN 300 472, data_identifier 0x10-0x1F) -> packets, Hamming 8/4 and 24/18, odd parity, pages per magazine (serial or parallel, erase, subpages), Fastext links (X/27/0), the national option subsets and the G0 designations of X/28/0 and M/29/0, a page as 40 x 25 cells (`render`: colours, mosaics, double height, flash, conceal, boxes); the PMT's teletext and DVB subtitle descriptors; `StoreFeed`, the rewind buffer read on a thread of its own (never a connection of its own) |
| `core/teletext_subs.py` | Teletext and DVB subtitles as subtitle tracks: a track's menu entries by language and page (`choices`), a language's page (`pick`), mpv's `teletext-page` on an old and a new mpv (`mpv_page`, `default_page`), the page kept per channel for everyone (`teletext_pages`, `PageStore`) |
| `core/hls_cap.py` | Nerd: the highest quality adaptive (HLS) streams play at - `hls-bitrate`, and the variant (picture and sound together) the player switches to at file-loaded for a size cap (a track, or on a newer mpv the variant's edition) |
| `core/remote_api.py` | The remote API and shared lists' small HTTP server (token on every request, this computer only unless chosen - then every private range, never a public address: `client_ok`; the calendar at `/calendar.ics` with remote control on; TV mode on/off/toggle and its keys at `/api/tv`, `/api/tv_key`) |
| `core/events.py` | Nerd: events pushed out - webhooks (POST JSON, token header, retries) and MQTT (event, retained state, last will, Home Assistant discovery); a queue and a thread per receiver; private viewing sends what happened, not what; the recordings' statuses as events; its checks |
| `core/mqtt.py` | A small MQTT 3.1.1 client in the standard library (CONNECT with login and will, PUBLISH QoS 0, SUBSCRIBE, PINGREQ, TLS; `take()` reads what the broker sent without waiting) |
| `core/mqtt_control.py` | Nerd: Home Assistant's commands over MQTT - the topics (`<prefix>/command/+`), what a message asks (a retained or unknown one ignored, logged at debug), the controls' discovery (buttons, volume, favourites list, text, TV mode's switch) and taking them away; TV mode's keys as `tv_key` |
| `core/phone_remote.py` | The phone remote's server: the home network (any private range, `client_ok`; never a public address), the token as the first part of every address, a token bucket and a lock-out per client, only the page and what it asks (state with `tv_mode`, channels, commands, TV mode on/off and its keys, a cached raster logo, the calendar); the touchpad's gestures read here (`gesture`: a swipe an arrow, a tap OK, a long press Back) |
| `core/phone_page.py` | The phone remote's page: HTML/CSS/JS in one, nothing from elsewhere, the app's dark and light palettes (#rrggbb only), its words, a CSP of its own script and style hashes; the TV mode button in its header, and while TV mode is on a remote in place of the lists (d-pad and OK, Back, Options, the colour keys with TV mode's labels, volume and channel rockers, mute, number keys folded out, or a touchpad - the choice in its localStorage); there, while something plays, the whole Now playing card a button back to the picture (`tv_key` "picture") |
| `core/qr.py` | A QR code encoder in plain Python: byte mode, levels L/M/Q/H (M by default), versions 1-40, the best mask by penalty |
| `core/ical.py` | Reminders and scheduled recordings as iCalendar (RFC 5545): stable UIDs, DTSTAMP, UTC times, escaping, folding at 75 octets, CRLF |
| `core/supporter.py` | The supporter key: signed by dopeiptv.org, checked offline, nothing about the person in it |
| `core/backup.py` | Full backup and restore: every setting a person would miss in one JSON file (logins included, caches, window layout and machine state left out); a restore over everything waits for the next start (`stage` / `apply_pending`) |
| `core/sync.py` | Settings sync, for everyone: the sync code, slot and key (HKDF), what syncs (explicit allow/deny lists), stamping, merging (last writer per entry, tombstones), the round trip to dopeiptv.org (nothing about the person: no key, no account; never from a modified build), applying what came back |
| `core/chacha20poly1305.py` | ChaCha20-Poly1305 (RFC 8439) in plain Python, many blocks at once - what sync encrypts with |
| `core/together.py` | Watch together: the room code (8 characters, no look-alikes), its room id and key (PBKDF2, then HKDF), messages sealed and padded (ChaCha20-Poly1305), the relay's clock (shortest round trip), the room (who is in, what plays: the newest state wins), keeping in step (`Corrector`: nothing within a second, a hold when a little ahead, a seek beyond; `LocalWatch`: a seek made here), the link to dopeiptv.org/together/ (a long poll and a sender, its own threads; never from a modified build) |
| `core/discord_rpc.py` | Discord status over Discord's local IPC (Unix socket / named pipe `discord-ipc-N`): frames, where Discord listens, the activity, `Presence` on its own thread (reconnects quietly); `DISCORD_APP_ID` (empty: not offered) |
| `core/report.py` | Error reports and feedback: scrubbed of anything identifying, shown in full, sent only on Send (never from a modified build: copied instead); the report's `Build:` line; the native crash file |
| `services/share.py` | dopeIPTV as a server: every source's channels as one M3U, the downloaded guides as one XMLTV (streamed) |
| `services/failover.py` | Failover: a live channel that will not play found in another source (guide id, else name); never what that source's lists leave out (`Shown`: its hidden categories, locked ones while locked, channels hidden by hand) nor, for a kids profile, one for adults |
| `services/together.py` | Watch together, the catalogue side: what plays here as a `Media`, the same title (TMDB id, else title and year; an episode by its season and number; a channel as failover matches it), what the room plays found in this source (`locate`, off the window's thread) |
| `core/platform_macos.py` | macOS-specific helpers |
| `core/platform_windows.py` | Windows-specific helpers |
| `media/embedded.py` | Embedded mpv player widget (night mode's switch in the options menu, `_sync_night` on each play; the equaliser's labelled filter) |
| `media/players.py` | External player launch, libmpv detection; whether libmpv's FFmpeg decodes teletext (`teletext_decoder`: libzvbi) |
| `media/teletext_subs.py` | Teletext and DVB subtitles in the player (`TeletextSubsMixin`): their subtitle menu entries ("Swedish - teletext 199"), the page set before the track, the PMT's pages from the rewind buffer or a local .ts, the channel's page and the preferred language's, a libmpv without libzvbi said so (never an error); the options menu's Teletext… |
| `media/dvr_player.py` | The player's side of the app-kept buffer (`DvrPlayerMixin`) |
| `media/rewind_bar.py` | The DVR row and timeshift timeline over the video, and their switch (`RewindBarMixin`) |
| `media/stream_info.py` | Channel-change technical banner, reading the current mpv track |
| `core/stream_info.py` | Validated stream metadata and bounded per-source/channel cache |
| `media/seek_slider.py` | The seek bar slider (`_SeekSlider`): click, drag, hover time |
| `media/video_surface.py` | The video surface mpv draws into (`_MpvGLWidget`) and the Linux pop-out's raster mirror |
| `media/subtitle_text.py` | Stray right-to-left marks out of subtitle files before mpv reads them |
| `ui/main_window.py` | MainWindow composition root (+ `mw_*` mixins) |
| `ui/app_menu.py` | The app's menus per platform: macOS's menu bar (app menu by roles, File, View, History, Help), elsewhere the one menu in the same groups |
| `ui/channel_list.py` | List model, view, delegate painting |
| `ui/dialogs.py` | Dialog windows; `SourceFields` (a source's connection fields and what they mean, no layout) and the Add source quick form (`PlaylistDialog`) |
| `ui/settings_ui.py` | Settings building blocks: sidebar shell (group headings, a group's pages shown only while it is on), search view (each page's group over it), pages, a page below a page (`open_sub`: a source's own, in place of Sources), sections (`card`), rows (with a mark before the control: a source's own or shared), link rows to a page elsewhere, switches, steppers, chips |
| `ui/qt_i18n.py` | Qt's own strings and QLocale dates in the app's language |
| `ui/home_widgets.py` | Home's featured strip, shelves and cards (pure presentation) |
| `ui/tmdb_match.py` | Manual TMDB match dialog |
| `ui/subtitle_search.py` | OpenSubtitles search dialog |
| `ui/widgets.py` | Small standalone widgets |
| `ui/nav_widgets.py` | The sidebar's `NavButton` (leading bar when selected, a dot / pill / muted count painted over it), `SideRule`, the hairline between its groups, and `SourceAvatar` at its foot (the active source's initials on the accent, `source_initials`) |
| `ui/box_finder.py` | Box and media-server kinds in the add-a-source forms and the "Find on my network" button (the form's own kinds first; pressed again it stops; with nothing found, why a network split in VLANs hides a box and what fixes it, `nothing_found_text`) |
| `ui/media_signin.py` | Signing in to a media server in the source dialog and the welcome: Jellyfin/Emby name and password (sent once, never kept) or Quick Connect, Plex's PIN in the browser and the server picked; the network off the window's thread |
| `ui/mw_media.py` | Media servers in the window: what plays reported (start, every 10 s, pause, stop), the newer of the server's and this computer's resume points, the server's titles under Continue watching (`_continue_rows`), a watched mark sent back, why there is no live TV; it only watches, nothing in private viewing |
| `ui/nerd_settings.py` | Nerd in Settings: Status (switch, what is in Nerd, how it works) and, while it is on, the five pages beside it under NERD - Network, Boxes and lists, Player, Remote and automation, Diagnostics |
| `ui/source_page.py` | A source's own page in Settings (under Sources): header (initials, name, kind and account, Test connection), tabs Connection / Network (Nerd) / Streams / Guide / Advanced, each row marked own for this source or shared (and where), Nerd rows only while it is on, a change stored a moment later when it can be, the latest connection test |
| `ui/source_nerd.py` | A source's own Nerd options: their controls and what they mean, without a layout (its page puts them on its tabs) |
| `ui/net_advanced.py` | Nerd's Own DNS card and Other networks (VLANs) card (Network page: `nerd_other_networks`, its line as typed, Search now) and Low latency card (Player page), each with its check line |
| `ui/source_net.py` | A source's own DNS, IP version and trusted certificate in its Nerd options, each with a Test |
| `ui/cert_trust.py` | The window that shows a certificate before it is trusted, and the offer to review a pinned source's changed one (`offer_review`) |
| `ui/rewrite_rules.py` | The rewrite rules' rows in a source's Nerd options (find, replace, regex switch, up, remove) |
| `ui/shortcuts.py` | The Keyboard shortcuts card (its own page under SYSTEM, for everyone): each key a button that records the next combination, ✕ / ↺ / reset all, who lost a key and why one is refused said plainly |
| `ui/nerd_check_line.py` | The ✓ / ! / ✗ line under a Nerd setting, its Test button and Details (the app's problem window) |
| `ui/command_palette.py` | The command palette's overlay (Ctrl+K / ⌘K): the field, the three groups, a row's chip, text and keys, Up/Down/Enter/Tab/Esc |
| `ui/mw_palette.py` | The command palette in the window: the menu's and the rebindable actions, every Settings row (`SettingsShell.index`, Nerd's while it is on), the active source's channels and the guide's programmes (folded on a worker), what a row does |
| `ui/conn_test_dialog.py` | Test connection's window: each step's mark, result and time as it goes (polled, never waited on), SD/HD/Full HD/4K lit by the speed, Copy (scrubbed), Stop, Test again |
| `ui/source_presets.py` | Presets in the source dialog (Fritz!Box, Channels DVR, Threadfin, xTeVe, Dispatcharr, ErsatzTV) |
| `ui/mw_box.py` | The box from the app: zap, wake, standby, record on the box from the guide; a channel listed without a play address (MythTV's) said why (`_box_no_live`); Nerd's copy address and guide channel; an Enigma2 box's info, screenshot and remote, its recordings managed, its signal in the stream stats (asked only while they show), the play address a transcoding box gives (`_live_play_url`), a channel it cannot decrypt said so (player, stream health, recordings) |
| `ui/box_tools.py` | An Enigma2 box's own windows: its info with the live signal, a screenshot of its TV with Save, its remote control (keys sent in the order pressed) |
| `ui/mw_commands.py` | Commands from outside carried out: play an address or file, a channel by name, come forward, the next or previous channel from the one playing |
| `ui/mw_privacy.py` | Private viewing and Clear recent history in the window: app-menu entries and shortcuts, the sidebar marker |
| `ui/mw_series.py` | Series recordings in the window: "Record series…" (Record, Remind me, or on an Enigma2 box with AutoTimer the box's own rule), the rules' look through the guide on the pool when it loads and with its 20-minute check (and what the box records, asked there too), the jobs added, what the box records left to it and said once, a reminder rule's reminders set |
| `ui/autotimer_ui.py` | A box's AutoTimer in the window: the rule editor (`AutoTimerDialog`: words and match, channels from the box's bouquets, time span, days, padding, record or zap, folder, duplicates), the preview (`AutoTimerPreview`), and the box's rules in the series card beside the app's, each row marked who records it (`BoxRules`, `marked`) |
| `ui/mw_profiles.py` | Profiles in the window: the profile's stores built pinned to it (`_build_source_stores`, `_build_profile_accounts`), `switch_profile` (what plays stops as Stop does, everything of the profile read again, Home, lists, sidebar, guide and theme shown again - no restart), the PIN asked, "Who's watching?" at start (and always for a profile with a PIN; nothing else until it is given), a kids profile's limits (no locked or adult category or folder, never a PIN question, Settings only with a grown-up's PIN), the avatar's menu, the profile for the phone remote, the remote API and Home Assistant (`_profile_state`, `_profile_switch_remote`) |
| `ui/profiles_ui.py` | Profiles on screen: the avatar (initials on its colour, or its emoji), the picker over the window (big tiles, arrows, Enter), TV mode's own picker screen, the profile's window (name, avatar, PIN, kids), the PIN question, Settings > Profiles (`ProfilesPage`) |
| `ui/mw_stats.py` | Your statistics in the window: the time the docked player plays counted into the log (hooks beside health/events; not while paused, capped per tick), saved every few minutes and on close; "Your statistics…" in the History menu |
| `ui/stats_window.py` | The statistics window: tiles, the period switch, two one-series column charts (P's accent, a hairline baseline, the peak's value, tooltips), the top lists, its empty state |
| `ui/mw_failover.py` | Failover in the window: once per channel, the other source's options while it plays, a note |
| `ui/mw_connections.py` | Connections to the account in the window: the app's own counted (player, rewind buffers, recordings over their own connection, multiview cells), the warning before a multiview cell, a recording while watching or the buffer next to a recording would go over the limit, with go ahead anyway; a box's tuners in the same question (a zap from any way in, a multiview cell, a cast, a recording over its own connection that would cut off a recording or the box's TV; the box asked off the window's thread, never in the way), the channel list's ring (`tuner_mark`) and Nerd > Diagnostics' In use now (`_in_use_lines`) |
| `ui/mw_mirrors.py` | Nerd's backup addresses in the window: a live stream whose server is down asked of the next address (off the window's thread), the channel started again from it |
| `ui/mw_chanprefs.py` | A channel's own settings in the window: the player told which channel it plays (`set_channel`), the channel menu's "Channel settings…" (a dot when it has its own), its dialog |
| `ui/mw_health.py` | Stream health in the window: the playback path's hooks (start, picture, drop, fail), the channel menu's "Stream health…", the list's dot |
| `ui/mw_ts_analysis.py` | Stream analysis in the window: "Stream analysis…" in the player's options menu and the playing channel's menu while Nerd is on, the link from Nerd > Diagnostics, what plays read from the player's rewind buffer (a `Runner` while the window is open, a new one when the channel changes), why there is nothing to read (HLS, the buffer off, not live) |
| `ui/ts_analysis_dialog.py` | Stream analysis's window: summary (OK / warnings / errors), tiles (bitrate, null packets, PCR interval, PCR jitter's sparkline), the indicators with counts, last seen and what each means, the PID table (type, bitrate bar, CC errors, scrambled), Copy (scrubbed), Reset counters; polled once a second, never modal |
| `ui/mw_stream_info.py` | Stream information in the window: what the player observed remembered per source and channel, the list's technical line (off by default, never in private viewing) |
| `ui/mw_portal_archive.py` | A Stalker portal's archive in the window: its link asked for off the window's thread (`StalkerClient.archive_link`), played from the moment asked, a go-back carried on to the next programme |
| `ui/mw_remote.py` | The remote API and sharing in the window: the server as Nerd says, each request's work on the window's thread |
| `ui/mw_events.py` | Events pushed out in the window: the playback path's and the recordings' hooks (start, pause, stop, channel changed, recording started/ended), handed to `core/events.py`; it only watches - and, with MQTT's commands on, carries out what Home Assistant asks on the window's thread (the volume, the favourites list and TV mode's switch kept up to date) |
| `ui/mw_phone.py` | The phone remote in the window: on and off (kept on for the next start), a new address, each request's work on the window's thread, only the channels the lists show; TV mode and its keys through `tv_command`; a channel's logo (a web address or a Nerd picon, no other file) fetched into the image cache once on the server's thread, a failed one not again this session |
| `ui/phone_remote.py` | The Phone remote… dialog (View): turn on, the code (`QrView`, whole device pixels on a white plate) and the address, New address, Stop |
| `ui/mw_calendar.py` | The calendar in the window: every source's reminders and the recordings to come as events, the addresses the remote API and the phone remote serve it at |
| `ui/calendar_export.py` | Export to calendar… (File, and the Reminders window): save an .ics, the subscription addresses with Copy |
| `ui/mw_report.py` | Send feedback, and the offer of a report after an error or a crash (queued; while a video plays it waits in the menu); answers to feedback (their window, the menu entry's count, when they are asked) |
| `ui/mw_together.py` | Watch together in the window: start, join and leave (never while private viewing is on), the playback path's hooks (start, pause, stop), what the room plays found here and started where the room is (no resume question), kept in step once a second through the player's play, pause and seek; a live channel only tuned |
| `ui/together_ui.py` | Watch together's dialog (start a room or join one by its code, the code to share, who is in) and the calm strip under the video |
| `ui/mw_simkl.py` | Simkl in the window: scrobbles of what plays, its card on Settings > Watch history (only with a client id), the PIN sign-in, its watchlist and history window; marks and the watched sync go to every account through `mw_trakt`'s `_watch_accounts` |
| `ui/mw_discord.py` | Discord status in the window: the player's hooks (start, pause, stop), nothing in private viewing, names or only "Watching TV", the card in Settings, Integrations (only with an application id); it only watches |
| `ui/mw_sync.py` | Settings sync in the window: when a round runs (start, after a change, every half hour, Sync now; never in a stream's first seconds), what came back put in place, its page in Settings (Sync between computers) |
| `ui/mw_home.py` | The Home page: which shelves, what goes in them (drawn by `home_widgets.py`) |
| `ui/mw_local.py` | Local files: folders, library view, music queue |
| `ui/mw_radio.py` | Radio, the place under Local files: radio-browser.info's lists (my stations, most voted/listened, search, countries, languages, genres), a station played as music (visualiser, its ICY title), favourite stations (`radio_favorites`, no source's), the click setting's card on Integrations |
| `ui/mw_webstreams.py` | YouTube, Twitch and the like through yt-dlp: Open address… (Ctrl+L), mpv's ytdl hook or the address resolved by the app, the offer to download yt-dlp, its card on Playback, saved addresses (`web_favorites`, LIVE badge) |
| `ui/mw_tv.py` | TV mode in the window: on and off (View menu, Ctrl+Shift+T, `--tv`, `tv_mode_start`), its page in the central stack full screen, the one key filter while it is on (every key without Ctrl/Alt/Meta, `QT_KEYS`), the pad's and LIRC's actions (keys for a dialog), the picture through the docked fullscreen with the info bar, zapping, the red key's favorite, Back over it back to the page with the stream going on (Stop stops); `tv_command`, the one way in for the phone remote, the remote API and Home Assistant (on/off/toggle, a key as a pad's; while off only the player's own controls); it plays through the window's own path |
| `ui/tv_mode.py` | TV mode's page (`TvPage`: tabs, Now playing in a row above them while a stream goes on, safe margins, colour keys, the arrows' moves, Back) and its screens' base, Home (continue watching, favorites, live now), Channels (categories, channels with now/next), Options (sound, subtitles, sleep, start in TV mode, leave) |
| `ui/tv_screens.py` | TV mode's bigger screens: the guide (`TvGuide`, the app's `EpgGrid` in large), Movies/Series (posters, a series' episodes), Search (keyboard on screen, results in shelves) |
| `ui/mw_teletext.py` | Teletext in the window: Teletext… (View menu, the player's options menu, T while a live channel plays), the pages read from the channel's rewind buffer (`StoreFeed`; without the buffer the viewer says it is needed), a few channels' pages kept for the session |
| `ui/teletext_view.py` | The teletext viewer: `TeletextPage` (40 x 25 cells scaled to the window, teletext palette, mosaics) and `TeletextDialog` (three digits, P+/P-, subpages, colour keys, index, hold, reveal; its keys kept from the window's single-key shortcuts) |
| `ui/tv_widgets.py` | TV mode's pieces drawn for the sofa: tiles with the focus ring, shelves, big lists and the poster grid (`ChannelListModel`), the keyboard, the colour legend, the info bar over the picture |
| `ui/mw_multiview.py` | Multiview: 2/4/6/9 live streams, an mpv per cell |
| `ui/mw_donate.py` | Donating from the app: the sidebar heart, About's link, Home's one card after real use |
| `ui/epg_grid.py` | The guide (`EpgGrid`): timeline grid, filter, its bar; the same widget on the Guide page or in its window (`EpgGridDialog`); timers only while on screen; its zoom (the time scale fitted to the room in steady steps, or a span picked with − / +, remembered for the page and the window apart) |
| `ui/mw_guide.py` | The Guide page in the middle pane (a place in the nav): what it covers, kept for the next visit, Esc back, Pop out to its window |
| `ui/epg_search.py` | Programme search across the whole guide |
| `ui/welcome.py` | First-run welcome overlay |
| `ui/category_flags.py` | Bundled SVG flags for category names (flag emoji, "SE \| …" prefixes) |
| `ui/player_resize.py` | The handle under the docked video that sets its height |
| `ui/theme.py` | Theming engine; `SUPPORTER_THEMES`, the two a supporter key opens |
| `ui/app_icon.py` | The app's own icon - plain, or the supporter's while the key and its switch say so: drawn at every size, the window icon, on Linux the user's icon theme |

## Working with individual modules

`ui/main_window.py` is a thin composition root: the real behaviour lives
in the `mw_*` mixins beside it, each mixed into `MainWindow`. Each has its
row in the table above; in short:

- Settings and accounts: `mw_settings`, `mw_trakt` (Trakt, and the watched
  sync over every account), `mw_simkl` (Simkl), `mw_discord` (Discord
  status), `mw_sync` (settings sync between a person's computers),
  `mw_profiles` (profiles: one per person, switching, the picker, kids).
- Places and lists: `mw_home` (Home), `mw_local` (local files), `mw_radio`
  (internet radio), `mw_webstreams` (YouTube, Twitch through yt-dlp),
  `mw_guide` (the Guide page and its pop-out window), `mw_detail`,
  `mw_search` (sidebar search), `mw_sidebar` (panel collapse / icon rail),
  `mw_nav` (focus mode, nav colours/icons, the MINE group, History's
  Watched filter, the nav's status), `mw_favorites` (several favorites at
  once, move between folders, drag-and-drop), `mw_context`, `mw_sort`,
  `mw_follow` (followed series, new-episode news), `mw_reminders`,
  `mw_calendar` (reminders and recordings as a calendar), `mw_stats`
  (your viewing statistics), `mw_donate` (the sidebar heart, About's
  link, Home's card), `mw_onboarding`, `mw_busy` (loading indicator),
  `mw_updates` (update badge), `mw_report` (feedback, error reports and
  the answers to them).
- Watching: `mw_recording`, `mw_series` (series recording rules),
  `mw_multiview` (multiview), `mw_popout` (detached-window player),
  `mw_subtitles` (OpenSubtitles, subtitles remembered per title),
  `mw_teletext` (the teletext viewer), `mw_tv` (TV mode, the 10-foot
  layout run from a remote), `mw_together` (watch together: a room, its
  strip, keeping in step), `mw_media` (media servers: playback reported,
  their resume points and watched marks), `mw_privacy` (private viewing,
  clear recent history), `mw_mpris` (the system's media controls through
  `services/media_session.py` - MPRIS, Now Playing, the SMTC - and the
  media keys).
- Casting: `mw_cast` (Chromecast, DLNA and Kodi: the cast dialog, the
  strip, handing over from the archive, going back during a live cast in
  the rewind buffer while it holds the moment - kept recording beside the
  cast only with Settings > Casting's `cast_keep_buffer` on, a second
  connection, asked first over a full or unknown limit; never to a DLNA TV
  or a Kodi - else in the archive, zapping while casting on the same
  receiver, "Add a device by address…"), `mw_airplay` (AirPlay to an Apple
  TV on macOS: the receiver added, its own endings, its window from the
  strip).
- Sources, boxes and the network: `mw_box` (the box from the app),
  `mw_portal_archive` (a Stalker portal's archive, by programme),
  `mw_failover` (failover to another source), `mw_connections`
  (connections to the account, the warning before one more, a box's
  tuners), `mw_mirrors` (a source's backup addresses for a stream whose
  server is down), `mw_health` (stream health per channel),
  `mw_ts_analysis` (Nerd's stream analysis, TR 101 290), `mw_stream_info`
  (stream details per channel), `mw_chanprefs` (a channel's own settings).
- Remote control and automation: `mw_shortcuts` (keyboard shortcuts),
  `mw_palette` (the command palette), `mw_commands` (commands from
  outside), `mw_remote` (the remote API and sharing), `mw_phone` (the
  phone remote), `mw_events` (events pushed out: webhooks and MQTT, Home
  Assistant's commands).

What stays in `main_window.py` is the `__init__`/`_build_ui` composition
and the playback path (start/stop, seek, fullscreen, reconnect) - keep
playback code there.

When making changes:

- **UI changes**: find the method by name across `ui/main_window.py` and
  the `mw_*` mixins (e.g. `open_settings` lives in `mw_settings.py`).
  A mixin method resolves on `MainWindow` via MRO, so a pure move between
  them never changes behaviour.
- **Playback**: `media/embedded.py` for the in-app player (its DVR row
  and timeshift timeline in `media/rewind_bar.py`),
  `media/players.py` for external players.
- **Theme/style**: `ui/theme.py` contains `THEMES`, `ACCENTS`, palette
  dict `P`, and `build_style()`.
- **Data/API**: `providers/client.py` for HTTP calls, `core/stores.py`
  for persistence, `providers/epg.py` for programme data.
- **Recording**: `core/recording.py` is self-contained; the
  `mw_recording` mixin calls its public API.

## Conventions

- Python 3.11+, `from __future__ import annotations` in every file
- PyQt6 (not PyQt5) - all Qt imports use the PyQt6 namespace
- No comments unless the "why" is non-obvious
- Type hints on public method signatures
- UI strings, comments, docstrings, and commits in English
- `P` dict from `ui/theme.py` for all colors (never hardcode hex)
- `ACCENT` string from `ui/theme.py` for the user's chosen accent color

## Common tasks

**Add a new dialog**: create a class in `ui/dialogs.py`, import it where
it is used, wire it to a menu action.

**Add a new setting**: add the QSettings key read/write in the relevant
method in `ui/mw_settings.py` (usually `open_settings`): a `card.row()` or
`card.toggle()` on the page it belongs to, and the write-back in the
`if d.exec():` block at the end. Rows are found by the Settings search
automatically. A number takes a `Stepper` (not a bare `QSpinBox`), a
time offset a `DurationSpin` and a size a `SizeSpin`, each in a `Stepper`;
a set of on/off choices of one kind takes `Chip`s in a `ChipGroups` rather
than a column of switches. Settings apply as they change (`commit()` in
`open_settings`, debounced, plus once on Close): write the new key there
with `put()`, which stores only what changed. A section is a borderless
surface with hairlines between rows, and controls are filled, not
outlined - don't add borders back, and don't drop the surface either: with
neither, the rows ran together.

**Where a setting goes - decide it before you write it.** Settings are
grouped by what a person is doing: WATCH (Playback, Picture and sound,
Guide, Multiview, Recording, Casting), CONTENT (Sources, Home, Local
files, Posters and info), ACCOUNTS AND INTEGRATIONS (watch history -
Trakt, Simkl -, Sync, Integrations), SYSTEM (General, Appearance, Keyboard
shortcuts, Profiles, Parental lock, Advanced) and NERD (Status, Network, Boxes and lists, Player, Remote
and automation, Diagnostics). Every row there is today, page by page and
section by section with the key it stores, is in
[`docs/SETTINGS_MAP.md`](docs/SETTINGS_MAP.md) - look there first, and add
the new row to it (`tests/test_settings_map.py` checks every page is in one
group, every row on one page, and that the map names every page).

- Put it where someone would look for it, not where the code that reads
  it lives.
- One setting, one place: another page that needs it links to it, never a
  second copy.
- A feature with a few settings gets a section on the page it belongs to,
  not a page of its own; a new page only for a service of its own.
- A source's own option goes in that source's page (`ui/source_page.py`,
  on the tab it belongs to, with an `Origin` mark), marked as its own,
  with the shared one shown as shared.
- A profile's own setting (`core/profiles.py` declares which) stays on the
  page it belongs to, marked "This profile's" (`own()` in `open_settings`,
  shown with two profiles or more); a new one is added to the declared
  split, or it is the household's.
- A Nerd-only option goes under NERD, and the everyday page it belongs
  beside gets one link row to it ("4 Nerd settings for ... -> Nerd >
  Player"): `nerd_link()` in `open_settings`, shown only while Nerd is on.
- If no page fits, stop and ask rather than add a page or a stray row.

**Add a theme**: a palette in `THEMES` (`ui/theme.py`) with every key the
others have, `"light": True` for a light one and `"accent"` for its own
accent colour, plus `theme_<key>` in `i18n.py` and every `locale/*.json`.
`tests/test_theme.py` checks the keys and that the text stays readable.

**Add a new data store**: add a class to `core/stores.py` following the
pattern of `FavoriteStore` or `HistoryStore`.

**Change the channel list appearance**: modify `ChannelDelegate` paint
methods in `ui/channel_list.py`.

## Quality gate

CI (`.github/workflows/ci.yml`) runs on every push/PR - reproduce it
locally before committing:

```bash
ruff check dopeiptv tests     # lint (pyflakes + bugbear + comprehensions)
mypy                          # types - scoped to the logic modules today
QT_QPA_PLATFORM=offscreen pytest -q
```

- Prefer `print()` never; log through the shared logger:
  `from ..core.log import log` then `log.info/warning/error/debug(...)`.
  `DOPEIPTV_LOG=debug` shows debug traces; `DOPEIPTV_LOG_FILE=/path` tees
  to a rotating file.
- `mypy` type-checks the pure-logic layer — `providers/` (client, epg,
  epg_index, metadata, diagnostics, oauth_loopback, opensubtitles, dlna, cast_feed, stalker, boxes, enigma2, autotimer, tvheadend, hdhomerun, vdr, dvbviewer, nextpvr, mythtv, lan_scan, cast_devices, radio_browser, airplay_bridge), mediaserver, jellyfin, plex), kodi, mdns, simkl), lan_scan), `core/` (stores, local_buffer, dvr_store/ingest/server/session/pool/record, updates, log, player_exec, wakelock, selfupdate, winupdate, macupdate, ed25519, update_keys, build_check, gcguard, contest, connections, tuners, mirrors, conn_test, health, home_net, nerd, nerd_lists, channel_numbers, shortcut_keys, palette_match, channel_prefs, http_login, commands, instance, nerd_check, privacy, rec_extras, series_rules, night_mode, rec_mp4, viewing_stats, profiles, catchup, url_rewrite, hls_cap, ts_scramble, teletext, teletext_subs, ts_analysis, remote_api, events, mqtt, mqtt_control, phone_remote, phone_page, qr, ical, report, supporter, sync, chacha20poly1305, discord_rpc, together, backup, doh, cert_pin, net_route, net_adapter, net_bridge, live_latency, ytdlp, tv_input, tv_nav) and `services/` (failover, share, resume,
  coverart, reminders, mpris, media_session, media_macos, media_windows, notify, popular) and `media/subtitle_text.py`; see `[tool.mypy] files` in `pyproject.toml`. The Qt UI is deliberately out of
  scope (the `mw_*` mixins reference shared `self.<attr>` across classes,
  which mypy reads in isolation as false `attr-defined` errors). Grow the list
  as more non-UI modules gain type hints.
- Bump the version in **both** `dopeiptv/__init__.py` and `pyproject.toml`
  on a release, and add a `<release>` at the top of
  `packaging/io.github.slimture.dopeIPTV.metainfo.xml` (software catalogs
  read the version there) - `tests/test_version.py` fails the build if any
  of them drift.
- **Before deleting, moving or renaming a file under `packaging/`, `docs/`
  or any other committed asset, grep for it INCLUDING hidden directories.**
  Plain `grep -r` and ripgrep both skip `.github/`, so a reference from a
  workflow step is invisible to the obvious check - which is exactly how
  `packaging/io.github.slimture.dopeIPTV.png` was removed while the
  Windows job still built its `.ico` from it, and the release build went
  red. Use `grep -r --include=... . .github` explicitly, or `rg --hidden`.
  `tests/test_packaging_icons.py` now fails on any repo path a workflow
  names that does not exist, so this cannot reach CI again - but the habit
  is cheaper than the test.
- **Branches: app work goes on `testing`, releases go `testing` → `main`,
  the website lives on `website`. No other branches, not even for a CI
  experiment.** A scratch branch pushed to GitHub runs workflows, leaves
  commits and artifacts behind, and confuses the history - and it happened,
  while probing a Windows runner for issue #19. To exercise the Windows
  release job without tagging, run `release.yml` by hand (`workflow_dispatch`)
  on `testing`; the "Attach to release" steps only fire on a tag.
- **A test may only import what CI installs**, which is `requirements.txt`
  plus ruff, mypy and pytest - see `.github/workflows/ci.yml`. A green run
  locally proves nothing about a module that happens to be on this machine
  and not on the runner: PyYAML was imported by one test, passed here, and
  failed CI with `ModuleNotFoundError`. Prefer reading a file as text over
  adding a dependency to the whole suite for one assertion.
