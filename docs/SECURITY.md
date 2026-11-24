# Threat model

What dopeIPTV protects, who could attack it, what stops them and what is
honestly left. The rules every change follows are in
[`AGENTS.md`](../AGENTS.md#security-and-privacy-rules); every review round
and what came of it is in [`SECURITY_REVIEWS.md`](SECURITY_REVIEWS.md); how
to report a problem is in [`SECURITY.md`](../SECURITY.md). The transparency
page, <https://dopeiptv.org/transparency/>, shows the code behind what the
app sends out.

This describes the code it is committed with. Each claim names the module
or function that makes it true. Where a sentence here and the code
disagree, the code is what runs and the sentence is a bug: report it.

## What is protected

- **Logins and tokens.** A source's login - an Xtream user name and
  password, an M3U address with a token in it, a portal's MAC, a box's or
  a media server's login or token -, Trakt's and Simkl's sign-ins,
  OpenSubtitles' login, the sync code, a Watch together room's code, the
  tokens of the app's own servers. They stay in the
  settings files (`core/settings_files.py`), and out of the log
  (`core/log.py`: each value the app holds masked wherever it appears -
  as written, percent-encoded in either case of hex, JSON-escaped, and a
  user name with its password as HTTP Basic's base64, padded or not, or any
  base64 that reads as text holding one (`encoded_forms`, `basic_forms`,
  `base64_masked`) -, one of three characters or fewer as a word of its
  own, so a short password is masked without every "abc" in the log; a line
  that could not be masked is withheld, never written as it was), error
  reports (`core/report.py`, the same forms) and any address that is
  logged. A download is kept by its source's id, its stream id and its
  kind - never by its address, which for Xtream holds the login - and
  nothing it writes beside its file names one
  ([below](#downloaded-films-and-episodes-are-files-on-the-disk)).
- **What a person watches.** History, resume points, watched marks and
  the viewing statistics stay on the computer (`core/viewing_stats.py`
  is never synced), and dopeiptv.org never learns them: sync carries watch
  history only when it is turned on, and only encrypted. What plays goes only to
  what the person turned on - Trakt, Simkl, their own media server,
  Discord, a Watch together room. With private viewing, a playback that
  began private keeps and sends nothing to its end, whatever is switched
  meanwhile (`core/privacy.py`, `playback_private`, `for_log`) - and writes
  no media of it to a disk: a rewind buffer, a cast's converted stream, a
  fetched subtitle only in memory that swap cannot carry to a disk
  (`core/memory_safe.py`), or not at all; a recording only once asked
  ([below](#private-viewing-writes-nothing-to-a-disk)).
- **The household's settings and its sync copy.** On disk the settings
  files are this user's alone. The sync copy on dopeiptv.org is encrypted
  on the computer with a key the site never sees (`core/sync.py`). A
  backup shows what it holds before anything of it is put back
  (`core/backup.py`, `ui/backup_dialogs.py`), and with a password it is
  encrypted ([A backup's password](#a-backups-password)).
- **The computer itself.** Nothing from outside runs code here: no stream
  address, playlist line, picture, guide, link or answer from the network
  becomes a program, an option, a file read or a file written that the
  person did not ask for.
- **The home network.** The app's servers for it - the phone remote, the
  remote API, server mode - are off until turned on, and a cast's run only
  while it does. They answer home addresses only, take a long token on
  every request but server mode's tuner's, and do not let a web page or a
  stranger use them as a way in or out.
- **The update path.** Only what dopeIPTV's release key signed is
  installed, only a signed file that names the release offered - an
  older release's file under a newer tag is refused, since the
  signature covers the file and not the tag - and only a version strictly
  newer than the one running (`core/selfupdate.py`, `signed_asset`,
  `names_version`, `refuse_older`; the one exception
  [below](#a-test-build-left-for-releases)).

Outside the model: a program that runs as the person (it can read what
they can), an administrator of the computer, someone at the unlocked
computer, and the provider's knowledge of what it serves - a stream
cannot be hidden from the server it comes from.

## Who could attack

### A provider or a playlist's author

**What they control.** Everything a source sends: the names of channels,
titles and categories, stream addresses, picture addresses, the guide
(XMLTV) and its size, the API's JSON, a portal's answers, redirects and
status codes, and the bytes of the streams themselves. A box or a media
server added as a source is in the same place, and so is whoever can
change a playlist file on its web server.

| They could try | What stops it |
|---|---|
| A channel address that is no stream: `av://lavfi` (a filter graph can write a file of the sender's choice), `edl://`, `memory://`, `gopher://` to a port of this computer, `fd`, a line that starts with `-` | `core/stream_address.py`: an address from the network plays only over a network media protocol (`NETWORK`), never one of `NEVER`, the `lavfi` device, an option or a control character; checked where a client hands its addresses out (`providers/client.py`, `providers/stalker.py`) and once more in the docked player and each multiview cell (`media/embedded.py`, `ui/multiview_cell.py`) |
| From a source on the internet: an `smb://` address (on Windows a sign-in there with the person's account), raw `tcp://` or `tls://` to any service, ftp, WebDAV, a gateway - or an address on this computer or a link-local one (the printer's page, a local engine, a cloud's metadata service) | `core/stream_address.py`: a source whose own address is on the internet (`on_internet`, its backup addresses too) hands out only the media protocols (`MEDIA`), never a host on this computer or link-local in any spelling (`host_refusal`: 127.1, 2130706433, `::ffff:127.0.0.1`, localhost, this computer's name, 0.0.0.0, 169.254/16, fe80::/10, and no host at all - `http://:8080/`, `http:///x`, which FFmpeg opens here: `empty_host`); a private address of the home network stays. An address with no host is no address from any source, but a socket listening here (`udp://@:1234`, `_LISTEN`) from a playlist file or the home network A home network's source and a playlist file as before; what the person's rewrite rules or udpxy make is theirs |
| From a source on the internet: a logo or poster on this computer or a link-local host, so the app itself sends requests there | `providers/client._pictures_kept`: such a picture is taken out of that source's lists and pages by the same rule (`stream_address.picture_refusal`) |
| A public name whose DNS answer is this computer or a link-local host (DNS rebinding), or a redirect from the internet to this computer, a link-local host or the home network - for the app's own requests (guides, pictures, the APIs, the rewind buffer, a cast's address) | `core/dest_guard.py`: every request through `core/_lazy_requests.py` and `core/net_adapter.py` is checked on its socket after it connects and before a byte is sent (`check_peer`: this computer only for a name that says so, link-local only for a link-local or home name, a cloud's metadata service only by its address); every redirect requests follows, and each one the cast follows by hand, judged by its target as written and as it resolves (`redirect_refusal`: http(s) only, never from the internet inward, the same host again allowed); a request through a proxy - whose connection is the proxy's - has its name looked up here first and judged the same way before anything is sent (`proxied_refusal`, `DestinationRefused`): best effort, the proxy's own lookup is the proxy's |
| An HLS playlist whose segments, keys or redirects send the player or a program the app starts (FFmpeg, mpv) to this computer's services, a link-local or metadata host or a device at home - by an address, or by a public name that resolves there | `core/net_bridge.py`: the in-app player (the docked one and each multiview cell) for every http(s) stream of a source on the internet, Nerd on or off (`player_proxy`), and every program the app starts on such a stream - the recorder, a cast's converter and its probe, the pieces of a cast's archive, AirPlay's converter, server mode's HLS copy (`hand_over`) - go out only through the app's bridge, which judges every connection it opens - each segment, key and redirect hop, CONNECT or plain http - by its host as written and then on its socket, before a byte is sent (`Policy`, `written`, `landed`): a source on the internet's never this computer, link-local, a metadata service or the home network, but the stream's own server (`entry`: AceStream's engine, udpxy at home, what `home_addresses` lets in), the person's own names and `.plex.direct`; a home source's by `dest_guard`'s rule. A refusal is a 502, said once per host and reason ([below](#what-the-in-app-player-opens-itself)) |
| A line read as an option of an external mpv or VLC, or of the recorder (`--log-file=`, `--include=`) | The address after `--`, and nothing but a stream's address or a file's path handed over (`media/players.py`, `core/recording.py`) |
| ffmpeg reading `concat:`, `subfile:`, `unix:` or a local file behind an address | `core/player_exec.ffmpeg_protocols`: `-protocol_whitelist` with a stream's protocols wherever ffmpeg or ffprobe is started for a stream |
| A picture that names a file: `/dev/zero`, or `\\host\share\x.png` (on Windows a login at that host) | `core/picture_files.py`: a file is read as a picture only inside the app's own picture folders, only a plain file, at most 64 MB |
| An SVG logo that makes Qt open a file or read a stylesheet | `core/svg_safe.screened`: the picture written out again without what it names |
| An answer that never ends, or JSON nested past what Python reads | `core/_lazy_requests.py`: `RESPONSE_MAX` (32 MiB), a source's lists `LIST_MAX` (512 MiB), then `ResponseTooLarge`; too deep is no JSON |
| A guide with a DTD or entities, an endless download, a gzip bomb | `providers/epg.py`: `refuse_entities`, `read_capped`, `gunzip_gently` (`GUIDE_DOWNLOAD_MAX`, `GUIDE_UNPACKED_MAX`: 2 GiB); the index is built in a process of its own (`providers/epg_index.py`) |
| A name shown as markup | `Qt.TextFormat.PlainText` and `ui/widgets.plain_text`, the text over the picture too; a message or a question in `ui/message_box.py`, plain text by itself |
| A title spliced into the command after a recording, or into its file name | `core/rec_extras.py`: no shell, placeholders filled per argument, a batch file on Windows given text only (`through_cmd`); a name without control characters or device names, a folder never outside the recordings folder (`core/recording.py`) |
| A film's or an episode's title made into a download's file name (`../../.bashrc`, `CON`, a NUL), a link left on the way, a page or an error message instead of the video, an answer longer than it said, a download that fills the disk | `core/downloads.py`: the name through recording's `safe_filename`, every folder on the way checked as `_stays_inside` does (no link that leads out of the downloads folder), the `.part` and the sidecar opened without following a link (`O_NOFOLLOW`); the first bytes refused as the rewind buffer refuses a page (`dvr_ingest.not_a_stream`, a `text/html` answer too); Content-Length matched; the downloads' space limit and recording's `DISK_FLOOR` of free space looked at before and while it writes; renamed from `.part` only once whole |
| A download's address or an HLS film's segments sending the app to this computer, a link-local host or the home network | The bytes through `core/dvr_ingest.open_for` (the app's guarded request path, `core/dest_guard.py`); an HLS film through `core/restream.ffmpeg_opener` - ffmpeg reads an alias through the bridge, whose `Policy` judges every segment, key and redirect; no address, login or token in ffmpeg's arguments |
| A sidecar in the downloads folder that names a poster elsewhere, or is not one | `core/downloads.read_sidecar`: at most 64 KB, JSON of a kind and source this app writes, only the fields a sidecar keeps (`clean_meta`), its numbers through `core/num.py` in range or left out, a poster by its own name beside it - never a path, a hidden file or one going up; the folder read without following a link, bounded in depth and number |

**What is left.**

- The provider sees what it serves, private viewing or not.
- The ceilings are far above any real answer, so a hostile source can still
  make the app read and work up to them.
- A list chooses where its pictures come from: [below](#a-list-chooses-where-its-pictures-come-from).
- What a TV that fetches a stream itself, a source at home's stream and a web page through yt-dlp open: [below](#what-the-in-app-player-opens-itself).
- A provider on plain http: [below](#a-provider-on-plain-http).
- A stream's own bytes are decoded by FFmpeg and libmpv: [below](#bugs-in-the-media-libraries-are-theirs).

### A device on the home network

**What they control.** What they answer to the app's searches (SSDP,
mDNS, the HDHomeRun, Jellyfin/Emby and Plex broadcasts, "Find on my
network"'s probes), what they say about themselves as a place to cast to
(a Chromecast, a Kodi, a DLNA description), a SAT>IP server's
description and lists, and - when one of the app's servers is on - the
requests they send it.

| They could try | What stops it |
|---|---|
| A device description that sends the app's requests, and the stream's address with them, somewhere else | `providers/dlna.py`: the control address on the description's own host (`same_host`), no redirect followed, sizes capped, no DTD or entity (`_parse_xml`); Kodi's and the probes' requests follow no redirect (`providers/kodi.py`, `providers/lan_scan.py`) |
| Reading a Kodi's login on its way: HTTP's basic login is the password in the clear | `providers/kodi.py`: a login over plain http goes to a Kodi only once the person said so for that Kodi, asked before anything is sent - in the cast window, and on the phone or in TV mode when a cast is run from there (`asks_first`, `KodiPlainLogin`, `ui/mw_cast._kodi_plain_ask`); "Use HTTPS" asks that Kodi over https first, its certificate checked, and keeps it so once it answers (`use_https`); the answer is kept with that Kodi (`plain_ok`), never logged |
| A large or looping mDNS answer | `providers/mdns.py`: `MAX_PACKET`, `MAX_PACKETS`, `_MAX_JUMPS` |
| Taking a known TV's name to get the next cast | `providers/casting.py`: a device known by its kind, its own id and its address (`ident`); one answering to the name as another is not cast to until it is picked, its address in sight |
| A box's XML with a DOCTYPE | `providers/boxes.parse_xml` drops it; a SAT>IP description with an entity is refused (`providers/satip.parse_description`) |
| A stream address in a box's list | The same `core/stream_address.py` rules as a provider's |
| Guessing a token of the app's servers | 192 bits from `secrets` for the phone remote, the remote API and server mode, compared with `hmac.compare_digest` in one place for all three (`core/home_gate.token_ok`); a token bucket per address and a lock-out after wrong tries, never this computer's nor a web page's (`core/home_gate.RateLimiter`, `wrong_token`) |
| Taking the phone remote or the remote API down from address after address, each with a fresh bucket | A budget for every address together beside each one's own (`RateLimiter`'s *total_rate*: 60 requests a second, 360 at once; a list's pictures 120 and 900), 429 once it is spent; lock-outs stay per address, and this computer is neither shut out nor counted (`this_computer`) |
| Sending the player, through the remote API, to a service on this computer or to a link-local host (a router's page, a cloud's metadata service) | `core/remote_api.play_refusal`, on the server's thread: an address that names this computer - loopback in any spelling, localhost, 0.0.0.0, an IPv4 address inside IPv6, an empty host - or a link-local or metadata host, or whose name resolves to one now, is refused with the reason (400); AceStream's engine at `127.0.0.1:6878/ace/` and the home network stay, and a script on this computer, which reaches them itself, plays as before (`remote_api.asker`) |
| Opening connections by the thousand, or holding them open | `core/exclusive_bind.py`: 256 at once, 64 per address (`ConnectionLimitMixin`), a client that says nothing let go (`IdleTimeoutMixin`) |
| Reaching the app's servers from the internet | `core/home_net.is_home_client`: loopback, the private ranges, link-local and IPv6 ULA, never a public address |
| Making a playlist's addresses name a host of its choosing, so the token goes there | `home_net.host_for_addresses` (`core/remote_api.py`, `services/home_server.py`) |
| A cast's own server (a file for a DLNA TV, the converter, the AirPlay bridge) | A 144-bit token in the path, compared in constant time, one stream, stopped with the cast (`providers/dlna.py`, `providers/cast_bridge.py`, `providers/airplay_bridge.py`) |
| Learning what the household records from server mode's page | Its `api/status` - where `dopeiptv --server` lists what it records, waits for and records next (`services/server_recorder.status`) - needs the page's own token (`K_ADMIN`), never the playlist's; a recording the window makes of what plays privately is not named there (its claim's `private`); nothing there can start, stop or change a recording |

**What is left.**

- Anything on the network can announce itself under a familiar name. The
  app shows each device's address; it cannot prove what a device is.
- A Kodi the person let send its login over plain http ("Send anyway")
  gets it in the clear on every request, as HTTP's basic login is; Kodi's
  own HTTPS needs a certificate the system trusts, which Kodi does not
  make for itself.
- A Jellyfin or Emby server found on the network is sent the name and
  password typed into its sign-in (once, never kept,
  `ui/media_signin.py`): a fake one gets them. Plex signs in at plex.tv.
- The app's servers speak plain http: [below](#the-home-networks-servers-speak-plain-http).
- Server mode's tuner endpoints take no token: [below](#the-tuner-endpoints-of-server-mode-take-no-token).
- "Home" is any private address: [below](#home-means-any-private-address).

### A web page in the person's browser

**What they control.** The links the page opens (`dopeiptv://`), what
the browser sends to addresses of this computer and the home network on
the page's behalf (pictures, fetches, forms), and the DNS answers for the
page's own name - which can be made to point at this computer (DNS
rebinding).

| They could try | What stops it |
|---|---|
| A `dopeiptv://` link with a quote in it that adds `--server`, `--play` or Qt's `-platformpluginpath` | `core/commands.own_arguments`: nothing after a link is read, by the app or by Qt (`app.py` hands Qt the program's name alone) |
| A link that plays an address | Asked first in the window, with the server and the whole address shown (`ui/mw_commands._ask_link_play`), one question at a time; a stream only, never a file (`commands.stream_url_ok`) |
| A link that tunes a channel (`dopeiptv://channel/<name>`) | Since 1.6.11 asked first too, once the channel is found - its name in plain text, Cancel the default, one question at a time (`ui/mw_commands._ask_link_channel`); only a channel the lists show, never one of a hidden or locked category (`_play_channel_named`), and through the same questions as a zap. The person's own `--channel`, the remote API and Home Assistant are no links and play as asked |
| A request to the phone remote, the remote API, server mode, the rewind buffer or a cast | A token in every address that the page cannot know; the phone remote never counts a web page's guesses and refuses its changes (`cross_site`: `Sec-Fetch-Site`, `Origin`) |
| Someone on the home network who reads the phone remote's traffic | Its key never crosses the network: it is after the `#` of the code's address (`/r/#key`, which no browser sends) and kept on the phone; every request is signed with it - HMAC-SHA256 over the method, the address, the time, a nonce and the body (`core/phone_remote.signature`, the page's own SHA-256 since `crypto.subtle` needs https) - within a minute of this computer's clock, each nonce once (`SIGN_WINDOW_MS`, `NONCES_MAX`): one seen on the way can be neither changed nor sent again. A picture's address is signed until a quarter of an hour on (`PICTURE_TTL_MS`). An address of before, with the key in its path, makes a new key at once (`leaked`, `on_leaked`). The calendar app, which cannot sign, gets a key of its own that opens the calendar alone (`calendar_token`) |
| Someone on the home network who saw a converted cast's address | The converter serves only the receiver it was started for (`providers/cast_bridge.receiver_addresses`, 403 for any other address), a new token each run; the Apple TV's bridge, which is not told the Apple TV's address (Apple's menu picks it), serves this computer and the first other address that asks for the run's stream alone - the Apple TV, within seconds of being told -, 403 for any other (`providers/airplay_bridge.admits`) |
| Server mode's tuner endpoints, which take no token | Refused for a web page (`cross_site`) and under a name that is not a home's (`services/home_server.tuner_host_ok`, `home_net.HOME_SUFFIXES`): a rebound name is the page's own public one |
| The loopback proxy | A key made for each run as the proxy's login, 407 without it, and a page's `GET /...` refused (`core/net_bridge.py`) |
| Script in the pages the app serves | A Content-Security-Policy of their own script and style hashes (`core/phone_page.py`, `core/server_page.py`) |
| A sign-in's redirect made up by the page | Trakt's `state` checked and PKCE (`providers/trakt.py`); the listener on 127.0.0.1, its port never shared (`providers/oauth_loopback.py`) |
| The app opening an address a service's answer chose | `core/xdg.open_url`: https only, and server mode's page on this computer (`tests/test_security_rules.py`) |

**What is left.**

- A page can bring the window forward (`dopeiptv://open`) and open the
  link's question.

### Another user or program on the same computer

**What they control.** Another account on a shared computer controls its
own files and programs. A program running as the person controls what
the person can: their files, their session bus, the ports on 127.0.0.1.

| They could try | What stops it |
|---|---|
| Reading the settings files (every login, the sync code, the tokens) from another account | `core/settings_files.protect`: each file 0600 and the app's folder 0700, set at start before anything writes them, and only ever narrowed; on Windows the registry and the user's own profile. Where the drive keeps no permissions (FAT, exFAT, some shares) the app starts all the same, logs the file by name and says once where it is (`not_private`, `ui/mw_settings._tell_settings_not_private`) |
| Handing the running app a command | `core/instance.py`: a socket in `$XDG_RUNTIME_DIR` or a 0700 folder of the user's own, never the shared /tmp, nor a folder another account owns; a folder that could not be made, read or narrowed to the user alone is never listened in - the app starts without the second start's handoff, said once (`_folder_refusal`); on Windows a random word in the pipe's name (`_pipe_key`) |
| Using the loopback proxy to go out as the person, through their route | A key made for each run (`core/net_bridge.py`), and its guard: from it nothing reaches what the stream's source may not (`Policy`) |
| Reading a provider's address - an Xtream login, a token - Nerd's proxy with its login or a header that holds a token from the command line of a program the app starts (`ps`, `/proc/<pid>/cmdline`) | `core/recording.py`: never an address in a recorder's arguments - the app reads the stream and hands it over on its input (`Feed`), or the recorder reads an alias through the bridge that names nothing of it (`net_bridge.Bridge.alias`: a `.stream.invalid` host under a 192-bit token, the path's shape alone), or - another address with a login, `rtsp://user:pw@` - a 0600 file (ffmpeg's concat list, mpv's playlist). A cast's converter and ffprobe, the readers of a cast's archive and rewind buffer (the buffer's token too), AirPlay's converter and probe and server mode's HLS copy read an alias the same way (`net_bridge.hand_over`); a proxy is the bridge's to go through, a header that may hold a token the bridge's to add - only the user agent and the headers that open nothing are arguments (`net_bridge.split_headers`; `tests/test_security_rules.py` fails on a `-http_proxy`); the bridge's key in the program's environment alone (`net_bridge.child_env`), which only the same user reads. An external player gets a 0600 playlist file, or is not started (`players.handoff_file`) |
| Reading the rewind buffer's stream | A 144-bit token, loopback only (`core/dvr_server.py`) |
| Catching the Trakt sign-in's redirect | Its port never shared (`core/exclusive_bind.ExclusiveBindMixin`, `SO_EXCLUSIVEADDRUSE` on Windows), and the code it carries useless without the PKCE verifier that stays in the app (`providers/trakt.py`) |
| A socket of its own where Discord or lircd would be | `core/discord_rpc.py`: a socket this user owns only; `core/tv_input.lirc_socket_ok`: lircd's when root or this user owns it, or in a folder nobody else can write to |
| Reading a media server's token off the session bus | `services/media_session.art_for_bus`: no address with a login on the bus |
| A file planted in the cache | The guide's index is plain data with no code path - a header, texts and arrays' bytes checked against it (`providers/epg.read_index_blob`); no pickle anywhere in the app (`tests/test_security_rules.py`); pictures from disk only in the app's folders (`core/picture_files.py`) |
| Stopping a scheduled recording, or keeping the server from one, with a planted claim | `core/rec_claims.py`: the recordings' claims live in a folder of the app's own beside the settings (`recording-claims`), 0700 and this user's - one another account owns, or that cannot be narrowed, is refused, and then the server records nothing; every file in it 0600, opened without following a link (`O_NOFOLLOW`), at most 16 KiB, read as from outside (`parse`: a damaged one is nobody's); a claim is named by a hash of its job's id, so no id names a path, and it carries no stream address - a login never goes into one |

**What is left.**

- A program running as the person reads what the person can: [below](#logins-are-in-the-settings-files-not-a-keyring).
- An address that is not http(s) and holds a login the app does not see as one, and the page yt-dlp is given, are still arguments: [below](#programs-started-by-the-app-show-their-arguments).
- The cache and the logs keep the system's folder modes: [below](#the-cache-and-the-logs-keep-the-folder-modes-the-system-gives).
- Any program on the session bus can drive the app: [below](#any-program-on-the-session-bus-can-drive-the-app).
- Any program running as the person can write the recordings' claims, and
  server mode opens a recording's connection by itself: [below](#server-mode-records-unattended).
- An administrator of the computer can read and change everything.

### Someone on the network path

**What they control.** The traffic between the app and a provider, a
service or a device at home: an open Wi-Fi, a router, an internet
provider.

| They could try | What stops it |
|---|---|
| Changing what dopeiptv.org sends | https to dopeiptv.org itself only (`core/build_check.secure`), no redirect followed; an update installs only what the signed list names (`selfupdate.signed_asset`), and its download follows redirects only over https (`selfupdate._get_https`) |
| Reading or changing a third party's answers (TMDB, Trakt, Simkl, OpenSubtitles, Plex.tv, radio-browser.info, GitHub...) | https with the certificate checked; `verify=False` is nowhere - `core/cert_pin.py` reads a certificate unchecked only to show it before it is pinned, and a pinned server is checked by its fingerprint instead (`core/net_adapter.py`) |
| The app's own requests to an https provider (its lists, guide, logos, the rewind buffer's connection, a recording's own connection) | The certificate checked against the system's authorities, or Nerd's pin for that one server (`core/net_adapter.py`) |
| The player's and recorder's https streams from the internet | The certificate checked against certifi's authorities (`nerd.checks_certificate`), unless the person let that source play unchecked when asked - a player whose core will not take the check plays nothing and says so (`nerd.CertificateUnchecked`); a program reading an alias has the bridge make the TLS connection, with that same check (`net_bridge.Alias.trust`) |
| Changing DNS answers | Nerd's own DNS over HTTPS, the presets reached by IP (`core/doh.py`) |
| Watching while a VPN has dropped | Nerd's VPN guard starts nothing while it is down (`core/nerd.py`, `vpn_blocks`) |
| Reading the MQTT broker's traffic | TLS on 8883, the broker's certificate checked (`core/mqtt.py`) |

**What is left.**

- A provider on plain http: [below](#a-provider-on-plain-http).
- A source the person let play without its certificate checked, and a
  server at home checked only as mpv's own default does: [below](#every-internet-streams-certificate-is-checked-since-1611).
- The app's servers speak plain http on the home network: [below](#the-home-networks-servers-speak-plain-http).
- Searches for devices (SSDP, mDNS, broadcasts) are plain by their nature,
  and a webhook goes over whatever address the person typed.

### dopeiptv.org itself

The site serves the app the update feed and downloads, feedback and its
answers, settings sync, the Watch together relay and the Trakt sign-in's
redirect. Its code is on the `website` branch
(`donate/sync.py`, `donate/together.py`, `donate/feedback.py`,
`donate/release_sums.py`). A modified build uses none of these services
(`build_check.allowed`, `refuse`), and the site refuses one that says it
is.

**What it could do.**

- See the address each request comes from and when, the app's version and
  system (`core/updates.user_agent`) and the build's kind
  (`X-dopeIPTV-Build`, `core/build_check.py`).
- Offer no update, or a late one.
- Hand back an earlier sync copy, keep one back or lose it
  ([below](#the-sync-server-can-hold-a-copy-back)); drop or delay Watch
  together messages, and see who is in a room
  ([below](#the-watch-together-relay-sees-who-is-in-a-room)).
- Read the feedback and error reports the person sends - they are for it -
  exactly as the person saw them before Send: the window scrubs its boxes
  with the person's own values on Send and shows anything more it hid
  before it sends (`ui/feedback_dialogs._ShownIsSent`, `core/report.py`).
- See the Trakt sign-in's one-time code pass through `/trakt/callback`.

**What it could not do.**

- Read a sync copy or a room's messages: the keys come from codes it never
  sees (`core/sync.py`, `core/together.py`: HKDF, ChaCha20-Poly1305).
- Change a copy or a message unnoticed: the tag refuses it; in a room every
  message is signed by its member's own key (`core/ed25519.py`) and an
  unsigned one is dropped, so no member speaks as another; an earlier copy is
  told to a computer that has
  seen a later one (`counter`, `RoundResult.older`), and a copy last saved
  more than a month ago is not taken by a computer that joins until the
  person says so (`saved_at`, `STALE_DAYS`); nor make an old room
  message new: a member since 1.6.11 is taken only from its first message
  that names this computer's id for this stay in the room, whatever time
  the relay says - after a restart too (`Room.receive`, `PROTO`); an older
  member's message out of its time is not acted on.
- Install anything: an update must be on a checksum list signed with a key
  that is never on the web server (`core/update_keys.py`,
  `selfupdate.signed_asset`), and the site mirrors a release's files only
  once that signature holds (`donate/release_sums.py`). An older signed
  release is refused too, under its own tag or a newer one
  (`selfupdate.names_version`).
- Tell who someone is from what the app sends: no account, name, e-mail
  or id the app makes up and keeps. A sync slot and its
  credential come from the sync code (`auth_of`); feedback's answers are
  asked for by the hash of a random code kept on the computer
  (`report.new_code`).
- Use the Trakt code: without the PKCE verifier, which stays in the app,
  it cannot be exchanged for a token.

**What is left.** What the site keeps, and for how long, is its word:
[below](#what-dopeiptvorg-keeps-is-its-word). A site taken over together
with the release key could sign anything.

### A supply-chain attacker

**What they control.** A Python package, a binary the build downloads, a
GitHub Action, a build runner, GitHub itself - or the release key.

| They could try | What stops it |
|---|---|
| A new version of a package on PyPI | `packaging/requirements-release.txt`: every version and the SHA-256 of every file pip may take; `release.yml` installs with `-c` and pip refuses the rest |
| A known vulnerability in a shipped package | CI's audit job and the release's (`tools/audit_deps.py`; the release is not signed until it passed - `release.yml`, `needs.audit.result`); an audit that did not finish fails too (exit 2: no answer after three tries, a package left unchecked); an exception only in `packaging/audit-allowlist.txt`, with a reason and a date |
| A changed binary at a download address (libmpv for Windows, the AppImage runtime and tool, micromamba) or a moved git tag | `packaging/build-inputs.json`: each file's SHA-256 and each tree's commit, fetched only through `tools/build_inputs.py` |
| A changed action, or a token left in a clone | Actions named by commit, workflows read-only unless a job says otherwise, `persist-credentials: false` (`.github/workflows/`) |
| A download swapped on GitHub or on the site | The signed `SHA256SUMS` (`tools/sign_sums.py`) checked by the app and by the site; GitHub's artifact attestations: `gh attestation verify <file> --repo slimture/dopeIPTV` |
| A changed or added file in a package | The signed build manifest (`core/build_check.py`, `tools/build_manifest.py`): such a build calls itself modified and uses no service of the site |
| A Mac app that is not ours as the next update | `core/macupdate.py`: Solecraft AB's Team ID and Apple's notarisation checked before anything is replaced |
| A new dependency slipped in | AGENTS.md rule 10: a reason in its commit and a pinned version |
| A changed yt-dlp download, or its checksum list swapped beside it | `tools/ytdlp_pin.py` in `release.yml`'s `ytdlp-pin` job: the release's `SHA2-256SUMS` checked with `gpgv` against yt-dlp's own key (`packaging/yt-dlp-signing-key.asc`, its fingerprint in `core/ytdlp_pin.py`), the SHA-256s written into the app's signed release; the app installs only those bytes and runs only a copy it marked itself (`core/ytdlp.py`: `install`, `own_copy`) |
| The update key taken from a build job that ran a changed package or tool | The build jobs never get it: they sign their manifest with `DOPEIPTV_BUILD_KEY`, which signs nothing else (`tools/build_manifest.py`, `BUILD_KEYS`) |

**What is left.**

- [Builds are not yet reproducible](#builds-are-not-yet-reproducible).
- [The Windows and Linux packages carry no code signature](#the-windows-and-linux-packages-carry-no-code-signature).
- [The build key signs in jobs that run other people's code](#the-build-key-signs-in-jobs-that-run-other-peoples-code).
- [Test builds carry every push](#test-builds-carry-every-push).
- [yt-dlp is trusted by its own signature](#yt-dlp-is-trusted-by-its-own-signature).
- Still as package managers give them, with their own signatures and not
  pinned here: apt's packages (the AppImage's and the .deb's libmpv and
  FFmpeg),
  Homebrew's and conda-forge's for the Macs, NSIS from Chocolatey, the
  Flatpak's runtime and SDK from Flathub (`docs/MAINTAINING.md`, "What the
  build downloads").

### Someone with a backup or the sync code

**A backup** holds every setting a person would miss and every profile
with its PIN as kept (`core/backup.py`). Its logins - playlists'
passwords, tokens and box MACs, a login in their addresses, proxy and
headers, Trakt's, Simkl's and OpenSubtitles' sign-ins, the sync code, a
Kodi's password, MQTT's and the webhooks' - go in only with a password,
and then the file is encrypted ([below](#a-backups-password)). Without
one they are left out (`backup.without_logins`, `LOGIN_SETTINGS`,
`LOGIN_FIELDS`): the file says so, and putting it back keeps the logins of
the computer it is put back on - a playlist that is there too keeps its
own (`_logins_from_here`). The honest limit: a login written into an
address's path (`/live/user/pass/`, a playlist's own form) cannot be told
from a folder and stays; a source's server holds none. The file is
written readable only by its owner where the system has such a thing.
Left out either way: the remote API's, the phone remote's and server
mode's tokens, the media servers' device id, where the windows sit,
caches.

**A backup someone else made** is outside input. Before it is put back,
the window names what would run something, let others in or send the
connections elsewhere (`backup.risky`, `risky_sources`,
`ui/backup_dialogs.RestoreBackupDialog`): the command after a recording
in full, mpv's options and folder, a proxy, headers, udpxy, webhooks, an
MQTT broker and a DNS server (`RISKY`); the remote API, sharing, MQTT's
commands and server mode switched on, and adult channels through it
(`RISKY_ON`); a sync code to join; and each playlist's proxy, headers,
rewrite, udpxy, catch-up address, backup addresses, own DNS and trusted
certificate (`RISKY_SOURCE`). Since 1.6.11 none of these is brought
unless the person ticks it - one box each, none ticked, over everything or
as playlists beside the ones here (`backup.risky_choices`, `without`): a
setting not ticked stays as it is on this computer, with what is one
setting with it (server mode's reach with its switch, a sync's own with its
code, a watch history's whole sign-in with its token - `_WITH`;
`Backup.keep`, kept in a staged restore), and a playlist not ticked comes
without those options and their companions. A restore over everything
waits for the next start (`stage`, `apply_pending`). A colour from it is used only as
`#rrggbb`.

**The sync code** reads and writes the household's copy: favorites,
settings from the allow-list (how the app looks and plays - never Nerd's,
never this computer's, never a secret: `sync.SETTINGS_ALLOW`,
`SETTINGS_DENY`), and, where the household syncs them, playlists with
their logins and watch history. It cannot put a playlist's proxy, headers,
rewrite, udpxy, catch-up address, backup addresses, DNS or trusted
certificate in place by itself: those wait for the person's Apply
(`hold_back`, `ui/sync_review.py`). It never carries a PIN and never takes
a profile out. A code that has leaked is replaced by stopping sync on every
computer, deleting the synced copy when asked, and starting again on one.

**A room code** lets its holder into the room: the display names, what
plays, and play, pause and seek for everyone, within bounds
(`MEMBERS_MAX`, `VER_JUMP`, never past a title's end). **A room's id**
without the code opens nothing since 1.6.11: every request to the relay
carries the room's credential (`together.auth_of`, `AUTH_HEADER`: HKDF of
the stretched code under a label of its own), of which the relay keeps
only a SHA-256; the first request binds the room, and from then on a
request without it - a message, a seat, reading the sealed messages,
leaving - is turned down, compared in constant time (`donate/together.py`,
`Relay._admit`). The relay also holds a room to `MAX_MEMBERS` seats,
`MAX_PER_MINUTE` messages a member, `ROOMS_PER_ADDR` rooms an address has
made (an IPv6 address by its /64), 2 KB a message and a day's life. **The phone
remote's address** is the remote for whoever is on the home network with
it; New address shuts out every phone that had the old one. **Server
mode's playlist** carries its token, which watches but changes nothing: [below](#server-modes-playlist-token-lets-a-device-watch-all-it-serves).

## A backup's password

The design behind a backup's optional password, with its test vectors
(AGENTS.md rule 9; its line on the transparency page belongs to the
`website` branch's `tools/transparency.py`). A person
may give the backup a password where it is made (Settings > Privacy and
data > Backup, a field and its repeat). Without one the file leaves every login
out (`without_logins`, above), and a plain or an old file restores as
before.

**The file** (`core/backup.py`: `seal`, `lock`, `for_file`) is one JSON
object, written as every backup is (`backup.write`: whole or not at all,
0600):

```json
{"dopeiptv_backup": "encrypted", "v": 1, "kdf": "pbkdf2-sha256",
 "cipher": "chacha20-poly1305", "rounds": 600000,
 "salt": "<16 bytes, base64>", "nonce": "<12 bytes, base64>",
 "data": "<the ciphertext and its 16-byte tag, base64>"}
```

- The key: PBKDF2-HMAC-SHA256 of the password - NFC-normalised, UTF-8 - with
  the salt, in `rounds` rounds, 32 bytes (`backup._key`).
- The data: ChaCha20-Poly1305 (RFC 8439, `core/chacha20poly1305.py`) of the
  backup's own bytes - exactly the file the same backup is without a
  password (`backup.encode`) - under that key and the nonce.
- The associated data: every field but `data` as canonical JSON - keys
  sorted, `,` and `:` without spaces, ASCII (`backup._aad`). A changed
  header fails the tag as a changed byte does.
- The salt and the nonce: 16 and 12 bytes from `secrets`, new for every
  file. A new salt is a new key, so a key seals one message and its nonce
  never repeats.

**Reading one** (`backup.read_any`, `locked`, `Locked.unlock`). Before any
work: `v` 1 (a higher one is said to be from a newer dopeIPTV), exactly
these fields, the kdf and the cipher by name, `rounds` an integer from
100 000 to 5 000 000 (`MIN_ROUNDS`, `MAX_ROUNDS`), salt and nonce of their
exact sizes, every base64 field strict (the alphabet, the padding, the
canonical spelling), the data longer than its tag, the whole file within
the 64 MiB a backup is read up to. Only then is the key made and the tag
checked. A wrong password and a changed file are one error - "Wrong
password, or the file was changed" - never which, never a traceback, and
asked again. What opens is read as a plain backup is and goes on to the
restore window unchanged. Put back over everything, it waits for the next
start where every restore waits (`stage`, `apply_pending`): beside the
settings files, 0600 in their 0700 folder, removed as it is put in place -
as protected as the settings files that then hold the same logins.

**Why these.** Only what is here (rule 9): ChaCha20-Poly1305 is the
cipher sync and Watch together use, and PBKDF2-HMAC-SHA256 in 600 000
rounds is how a PIN and a room code are stretched (`core/pins.py`,
`core/together.py`) - no new primitive, no new dependency. A memory-hard
function (scrypt, Argon2) would slow a graphics card more, but Python's
scrypt depends on the OpenSSL a build links, and Argon2 is not in the
standard library: a backup made by one copy must open in every other. The
rounds are in the file, so a later version can raise them and still open
an older file. Measured on the development machine: the 600 000 rounds
0.15 s; ChaCha20-Poly1305 in plain Python about 0.08 s a MB either way
(4 MB 0.3 s, 16 MB 1.3 s). Both run on a thread of their own while a small
window says so, the window's thread going on
(`ui/backup_dialogs.run_working`, `UnlockBackupDialog`).

**What it protects.** A backup file that is lost, left on a USB stick or in
a cloud folder, or shared by mistake: without the password it shows
nothing of what it holds, and a changed one is refused, never put back.

**What it does not.**

- A weak password against an offline guesser. Whoever has the file can try
  passwords as fast as their hardware computes PBKDF2: the rounds make each
  guess slower, a graphics card still tries thousands a second. A long
  passphrase is the protection; the app does not judge the password.
- Malware, or another program running as the person: it reads the settings
  files, where every login is in plain text anyway
  ([below](#logins-are-in-the-settings-files-not-a-keyring)), or the
  password as it is typed.
- The password in memory: Python cannot wipe a string. The field is
  emptied once the backup is made or the password sent, and the thread
  lets go of it when it ends; a copy can stay in the process's memory until
  it is reused. Never in the log, a report or an error's text.
- The file's size and when it was written. The header says only how it is
  locked: no version, no date, no names.
- A lost password: nothing recovers it, and the app says so where it is
  given.

**Test vectors** (`tests/test_backup_lock.py`): PBKDF2-HMAC-SHA256 against
RFC 7914's own, and the file's: the password "correct horse battery
staple", salt 00..0f, nonce a0..ab, 100 000 rounds and the plain text
`{"dopeiptv_backup": 1, "stores": {}}` as `backup.encode` writes it give the
key `49d49c25…175d2a1e` and exactly the file in the test - the same bytes
pyca/cryptography's ChaCha20Poly1305 gives (checked once when it was made).

**An older app** (1.6.8 and before) does not know the layout: it says the
file is not a dopeIPTV backup or is damaged, and changes nothing.

## Honest limits

What the app does not protect against, each with the reason it stands.
Some are decisions; some are open work, said so.

### The home network's servers speak plain http

The phone remote, server mode, the remote API on the home network and a
cast's servers speak http, not https. The phone remote's key never crosses
the network - its requests are signed, each once (`core/phone_remote.py`) -
so someone who only listens can neither use nor replay them; but what they
carry does cross it: whoever reads the home network's traffic (an open
Wi-Fi, a device that has taken over the router) sees what plays, the lists
the phone asks for and the calendar's own key. Someone who can change that
traffic - not only read it - can also change the page itself on its way to
the phone, and a changed page can read the key the phone keeps: they can
then drive the remote (play, stop, record, the volume) until New address,
though never reach a login, a file or anything beyond what the remote does.
Server mode's and the remote API's tokens are in their addresses, so such a
reader can use them - and the remote API's shared playlist hands each
stream on as a redirect to the source's own address, an Xtream login in it
(`core/remote_api._redirect_stream`), so its token reaches those logins
too; server mode restreams instead and hands out none. A converted cast serves only its receiver's address -
or, where that is not known, the first that asks for its own address -,
which another device on the network could take.

*Why:* a certificate for a home address could only be one the app makes
itself, which every phone's browser would warn about and ask the person to
accept - and a changed page would come with a warning just as the real one
does, so it would protect nothing; a trusted one needs a public name per
computer and a service handing out certificates (Plex runs one), which puts
every computer in the public certificate logs. A TV, Kodi or VLC given a
playlist cannot sign, and neither can Home Assistant's plain REST calls,
which is what the remote API is for. What holds them is
the home network, a 192-bit token, the lock-out and New address.

### Server mode's playlist token lets a device watch all it serves

Every TV, Kodi or VLC given server mode's playlist holds its token, which
opens the playlist, the guide and every stream served
(`services/home_server.py`, `_authorized`). Its page and its `api/` take a
token of their own (`nerd_server_admin_token`), which only this computer's
Settings opens the page with and which is never in a backup: a device with
the playlist cannot change what is served.

*Why:* a playlist of its own per device is not built; the playlist token
is for watching only.

### The tuner without its token, when the person lets a device in

Since 1.6.9 the HDHomeRun API (`discover.json`, `lineup.json`,
`/auto/v<n>`) needs the token, in the tuner's own address
(`/t/<token>/discover.json`, and every stream address of its lineup under
it): Jellyfin, Emby and Channels take such an address. The UDP discovery
on 65001 says nothing to a device not let in. Plex asks a tuner by its
address alone, so Settings > Nerd > Remote and automation can let devices
in without the token (`nerd_server_open`): only the addresses listed
(`nerd_server_open_hosts`, home addresses or this computer's), or - what
1.6.0 to 1.6.8 did, and what a server set up then keeps until it is
changed - every device on this computer's own networks. A device let in
watches within the channels at once and at 10 requests a second per
address (`TUNER_RATE`), and the person is told what that means: the
server's Settings card says what the tuner is open to now, in plain words,
for each choice (`home_server.exposure`). Since 1.6.11 a server that kept
the open tuner from before 1.6.9 unasked keeps working and is told so
once, while it runs, on a decision card (`open_untold`,
`ui/mw_server._server_tell_open`): "Close it" lets in, by their addresses
alone, only the devices that used the tuner without its token this run -
or, when none did, nobody, with a message that says where a device is let
in (`closed_mode`, `note_open_use`); dismissed, it is kept and not said
again (`nerd_server_open_told`). `dopeiptv --server` says it in the log at
every start (`headless.open_note`).

*Why:* a device let in by its address is trusted by its address; on the
home network another device could take that address. Never a web page
(`cross_site`), never under a name that is not a home's (`tuner_host_ok`).

### A cast hands the TV the stream address

A DLNA TV, a Kodi and an Apple TV fetch the stream themselves, and so does
a Chromecast on a direct cast: they are handed the stream's own address -
for an Xtream channel the login is in it - and a Kodi the source's user
agent and headers too (`providers/kodi.py`). Only when the app converts the
stream for the TV - a sound or picture it cannot play, a chosen track,
subtitle or quality, the archive or the rewind buffer in pieces
(`providers/cast_bridge.py`, `providers/cast_feed.py`,
`providers/airplay_bridge.py`) - does the TV get an address of the app's
own under a token, the provider's staying here, and what the converter
fetches goes through the app's guard
([below](#what-the-in-app-player-opens-itself)); what a TV fetches itself
is the TV's. A Chromecast is Google's device running Google's receiver.

*Why:* a DLNA TV, a Kodi and an Apple TV are made to fetch a stream
themselves, and a cast through the converter costs the computer's
processor and starts later. Sending every cast through the computer
instead would keep the computer awake and in the path for the whole cast,
which makes a cast less dependable than the TV fetching the stream itself.
To a Chromecast or an Apple TV the person can have one cast go through
the app: choosing a quality, a sound track or a subtitle in the cast window
converts it. The TV is one
the person chose to cast to; the app says which device a name stands for
(`casting.where`).

### Every internet stream's certificate is checked (since 1.6.11)

The player and the recorder check the certificate of every https stream on
the internet against certifi's authorities, as the app's own requests
always did, whether Nerd is on or not (`nerd.checks_certificate`,
`nerd._apply_tls` before every stream - the docked player and each
multiview cell -, `nerd.strict_tls_recorder` for mpv as a recorder; a
recorder reading an alias has the bridge make the TLS connection to the
stream's server itself, checked the same way - `net_bridge.Alias.trust`:
`CHECKED` with the authorities the player is given, `PINNED` by the pin,
`UNCHECKED` only where the player and the recorder would not check either,
the trust the recorder had when it read the address itself). Someone on
the network path who answers with a certificate of their own gets nothing - the login in an Xtream address
least of all. A server at home is checked as mpv's own default does (0.37
nothing, a newer mpv everything) - its certificate is often its own -
unless Nerd's strict check asks for it too (Nerd > Network, "Check every
stream's certificate", `nerd_tls_strict`). A host whose certificate the
source pins is the bridge's to check (below); a `tls-verify` the person
typed into mpv's options or mpv.conf wins (`Nerd.typed_tls`).

**A player that cannot check it.** Where the certificate is to be checked
and the player's core does not take the check - `tls-verify` or
`tls-ca-file` refused, or `tls-verify` not on when read back - the stream
is not opened at all, the one before it stops, and the video says why
("This player cannot check this stream's certificate, so it was not
opened"): `nerd.CertificateUnchecked`, `EmbeddedPlayer.refusal`, a
multiview cell's title. Such a stream is never handed to an external
player instead, as play()'s other failures are (`main_window._start_playback`).

**One that does not check out.** The stream plays nothing, its diagnosis
says why (`diagnostics._probe_url`: "the provider's certificate could not
be checked"), and the window asks once, on the decision card, whether that
source may play without the check (`MainWindow._tls_offer`). Only that
answer stores the source's own option `tls_unchecked`; its page has the
switch to take it back (Advanced, "Play without checking the certificate").
It is a connection setting like a proxy: sync never takes it unasked and a
restored backup names it first (`backup.RISKY_SOURCE`).

*What stays:* a person who answers "Play without checking" accepts the risk
for that source; a cast's receiver checks as it does itself; an external
player checks as its own defaults do; and a stream on plain http has no
certificate to check ([below](#a-provider-on-plain-http)).

**What Nerd's trusted certificate does.** For one source and one server
(host and port), the person sees the certificate first and pins exactly
that one by its SHA-256 (`core/cert_pin.py`, `ui/cert_trust.py`). The
app's own requests to it accept that certificate and no other
(`core/net_adapter.py`). The player and the recorder go through the
loopback bridge, which checks the fingerprint before every tunnel to that
server and again every ten minutes, and refuses the tunnel when it differs
(`core/net_bridge.py`, `PIN_RECHECK`); on such a route every other https
host - a redirect, an HLS segment elsewhere - is checked against the
system's authorities before its tunnel opens. A changed certificate is
noted and offered for review (`cert_pin.PinMismatch`,
`cert_trust.offer_review`). Its limits: it holds only while Nerd is on
(`net_route.route_for`); it covers https, not rtmp, rtsp or udp; with a
proxy of Nerd's for the player the stream goes through that proxy and the
guard's bridge, not the route's - the pin is not checked there
(`nerd.player_route`); and a cast cannot carry it - the app asks first
(`ui/mw_cast._cast_pin_ok`).

### A provider on plain http

A provider whose address is http sends its lists and streams in the clear,
and an Xtream login travels in every address (`player_api.php?username=…`,
`/live/<user>/<pass>/…`). Test connection says so ("Plain http - nothing is
encrypted", `core/conn_test.py`), and since 1.6.11 so does the address
field itself while an Xtream server or a portal on the internet is typed
at http:// or with no scheme (`SourceFields.plain_http`,
`playlist_http_note`) - a note, nothing refused.

*Why:* it is the provider's choice; the app can only use what it offers.

### A list chooses where its pictures come from

The picture addresses in a list are fetched as they are (`core/workers.py`).
Since 1.6.9 a source on the internet names no device of the home network
either - a private or unique local address, a name without a dot or with a
home ending - for its streams, its pictures or its guide
(`core/stream_address.HOME`, `providers/client._pictures_kept`), unless the
person let that source in (its own option `home_addresses`: a list of
their own, on the internet, of their boxes). The one shape still taken is a
multicast group through a relay at home (udpxy: `/udp/<group>`), which an
operator's list names - since 1.6.11 only as udpxy has it: a private
address written as one, its port, `/udp/` or `/rtp/`, a multicast group and
its port, and nothing after it, no query, login or escape
(`stream_address._RELAY`), so a list cannot reach another page of the
device that answers there. A source of the home network's, or a playlist file,
names what it likes. A public name that resolves to a home address is not
caught for the player (mpv looks names up itself). The app's own requests
are caught for this computer and link-local hosts whatever the name
(`dest_guard`), and since 1.6.11 a name a source on the internet named for
its pictures or its guide (`dest_guard.note_outside`, from
`client._pictures_kept` and `M3UClient._guide_ok`) never reaches a home
address either, checked on the socket after it connects: the source's own
server names (`note_own`, from `make_client`: its server, list, guide,
portal and backup addresses - split-horizon DNS for one's own server is
common) and Plex's `*.plex.direct` (a home address by design) excepted.
Whoever runs a source's own name's DNS - its provider - can point it home
as well, and the app's requests to that name (its lists, and the pictures
and guide it names there) then reach a device at home: a GET of its
choosing whose answer it never sees, never this computer or a link-local
host. That is not narrowed: a server at home by its public name looks the
same in every way the app could tell - its port, its paths, the moment its
answer changes (a laptop coming home flips it too, and the rewind buffer's
connection is one of these requests). At most `OUTSIDE_MAX` names are kept; a list that names more makes every
public name count as an outsider's until the app closes (`dest_guard._FULL`)
- it fails closed rather than let an early name go and reach home.

*Why:* a box's picons and a media server's artwork are at home addresses,
and a playlist on a server at home is common - those are home sources.

### TMDB learns what is looked up

With the built-in key - the default - a film's or a series' title and year
go to The Movie Database for its poster and details, and with them this
computer's address, as to any service; never a login or the provider's
address (`providers/metadata.py`). Nothing is asked about what plays
privately (`privacy.title_private`). **Provider artwork, TMDB off**
(Settings → Posters & info: `services/coverart.OFF`) asks TMDB nothing at
all: no title search, no ids for Trakt or Simkl, no chart of the week, no
picture of the provider's moved onto image.tmdb.org. A picture the
provider's own list names on TMDB's server is still loaded from there -
the provider chose where it is.

*Why:* posters and details are what most people want from a list of
films; turning them off is one choice away, said where it is chosen.

### What the in-app player opens itself

(Before 1.6.11: "The player and the recorder look names up themselves".)

The app's own requests are checked where they land (`core/dest_guard.py`).
FFmpeg and mpv fetch an HLS playlist's segments, its keys and the redirects
they meet themselves, so the app hands every stream it does not read itself
to the app's bridge (`core/net_bridge.py`):

* **The in-app player** - the docked player and each multiview cell, libmpv
  inside the app - for every http(s) stream of a source on the internet,
  Nerd on or off: its `http-proxy` is the bridge (`net_bridge.player_proxy`,
  set in `nerd.apply_stream` - an option of the core in the app's own
  process, no program's argument), put back to none for the next stream
  that needs none. On a Nerd route (own DNS, IP version, a pinned
  certificate) the route's own bridge, which guards the same way. A radio
  station is judged as what it is, no source's.
* **Every program the app starts on a stream** - the recorder, a cast's
  converter and ffprobe, the readers of a cast's archive and rewind buffer,
  AirPlay's converter and probe, server mode's HLS copy - reads an alias
  through it (`net_bridge.hand_over`); with no bridge it is not started.

Every connection FFmpeg or mpv makes there - each segment, key and redirect
hop - is judged by its host as written and on its socket before a byte is
sent (`Policy`), so a public name whose DNS answer is 127.0.0.1, a redirect
there, or a playlist whose segments or key are on this computer, a
link-local host or - for a source on the internet - the home network, gets
a 502 and nothing is sent. Nerd's proxy, where it takes streams, is
chained behind the bridge (an http proxy: `upstream`; a socks one FFmpeg
never went through, nor does the bridge), and without one the environment's
`http_proxy` as FFmpeg itself takes it (`system_proxy`): the way out stays
the person's. A connection the client has left is closed at once, so a
stalled provider's is not held against the account's one connection
(`pipe`). With no bridge to be had the player plays as before and says so
once in the log - never no playback.

*What is left:*

- **A source of the home network, a playlist file, this computer's own
  servers.** Their streams in the player go out as before, judged by the
  address as written (`core/stream_address.host_refusal`); a home source is
  the person's own network, and its programs' aliases are judged by
  `dest_guard`'s rule (a name an internet source named never at home).
  The rewind buffer's own server and AceStream's engine on this computer
  are not bridged at all.
- **A TV that fetches the stream itself.** A Chromecast on a direct cast, a
  DLNA TV, a Kodi and an Apple TV given the original address open what it
  names on their own; that is outside the app
  ([A cast hands the TV the stream address](#a-cast-hands-the-tv-the-stream-address)).
- **A web page through yt-dlp.** The person's own web streams (YouTube,
  Twitch and the like) are left out (`apply_stream`'s *guard*): yt-dlp is
  a program of its own that opens the page and resolves it itself, which
  the bridge does not see, and an address the person typed is no
  provider's; what it resolves to is played as it always was.
- **Through Nerd's proxy** only the address as written is judged - the
  proxy connects. On such a stream the player goes through the guard's
  bridge, not a route's, so a pinned certificate is not checked there.
- **An external player** is the person's own program.
- **What it costs.** Every such stream is copied through the app's process,
  and a plain http request through the bridge is a connection of its own
  (`Connection: close`, so each is looked up for its own host): an HLS
  segment over plain http opens a new connection where it might have
  reused one.
- **A public name of the stream's own server** that the home's DNS answers
  with a home address is let in (split-horizon DNS for one's own server),
  and so are the person's own names and `.plex.direct` (`Policy.entry`,
  `dest_guard.note_own`).

*Why:* the libraries resolve and connect inside themselves; the app cannot
see their sockets, only what they ask a proxy for - so the proxy is the
app's, and every stream from the internet goes through it, at the cost of
a copy of each through the app's process.

### Server mode records unattended

`dopeiptv --server` records what is scheduled while no window runs
(`services/server_recorder.py`): at a recording's time it opens a
connection to the provider by itself - the one the person scheduled, with
the source's own route, user agent and certificate check as the window's
recording has them. It counts it with the channels it streams to devices
and, while fresh, what the provider last said of other devices, and never
goes over the account's limit: the recording waits, said in the log, and
fails at its stop time when no room came (`NO_ROOM`); a channel nobody
watches gives way, a device watching never does. What it cannot see is a
window on another computer, or another app on the account the provider did
not count lately: a recording it opens then can be one too many, as one the
window opens beside another device's. It sends nothing anywhere new: the
provider's own address and the guide's, as the window would.

Each job is recorded by one recorder (`core/rec_claims.py`): on one
computer the claim is the system's lock, held by the recorder's process and
gone with it, so two never record a job and a crashed one's is taken over.
Two computers that share a settings folder over the network may not share
the lock; a claim of another computer stands as long as its recorder
touches it (`STALE_S`, 90 s). Two recorders that come to one job within the
same moment through a share that keeps no locks could both take it; a
recorder that loses the share for longer than that has its claim taken
over while it still records. Any program running as the person can write
the claims - stop a recording, or keep the server from one - as it can
change the settings.

*Why:* the claim needs no server of its own and works wherever the
settings do.

### Downloaded films and episodes are files on the disk

A film or an episode downloaded to watch without the network
(`core/downloads.py`) is a file in the downloads folder - by default one
of the app's own in the person's videos folder - with a sidecar beside it
(`<file>.dl.json`) and its poster as a picture file. The sidecar holds
what offline mode needs and nothing else: the kind, the source's id and
the name the person gave the source, the stream id and the playback key,
the title, year, plot, length and category, the poster's file name, and
for an episode its series (id, title, plot, category, poster's file name),
season, number and title. It never holds an address, a login, a token or
an account; a text that came with an address in it is kept without the
address (`clean_meta`), and a source's name that holds a login - one an
older release made of an address with "user:password@" in it, which the
start now renames to the host and port (`PlaylistStore.rename_login_names`)
- is left out whole. The queue of what is still to download is in the
settings file (0600) - the poster's address too, never the stream's: that
is asked of the source's client when a download starts.

The files take the folder modes the system gives, as recordings do: where
the folder lets another account in, it can see what was downloaded, by
its file names alone. Nothing downloads while viewing privately (a running
download waits), the downloads are never synced and never in a backup, and
Delete download removes the file, its sidecar and its poster.

*Why:* a download is a file the person keeps, like a recording, and the
person chooses its folder. An HLS film cannot be carried on where it was
cut: a pause or a cut starts it again.

### Offline mode keeps the app off the internet - not the system

While offline mode is on (`core/offline.py`) the app's own requests, the
connections the bridge opens for the programs it starts, webhooks, MQTT,
Test connection and Nerd's checks go only to this computer, a link-local
host, the home network or a multicast group: judged by the host as
written in any spelling and again on the socket, before a byte is sent; a
public name is refused without being looked up, unless the person gave it
(a source's own server), which is then judged by every address it
resolves to (`tests/test_security_rules.py` holds every way out, and every
program the app starts). What a program opens itself is judged before it
starts: a web page before yt-dlp is looked for or run, an external mpv or
VLC by the address it is handed, a stream's address with every address
written inside it (mpv's ytdl://, lavf://, edl://, FFmpeg's concat:), and
a playlist or a stream's manifest on this computer - known by its name or
its first bytes, read at most 1 MiB - by the addresses it names; one that
names another list on this computer, is longer than that or spells an
address in XML's character references is refused, as it cannot be judged.
The sidecar
now keeps a category's own name too (a kids profile's lists judge it
offline), and a series' known episodes are kept in the app's cache - id,
season, number and title - for a series' page offline, never while
viewing privately.

Not covered: a home name (one without a dot, or with a home ending) is
looked up by the system's resolver, which may ask a DNS server outside;
what the system, the desktop or another program sends is theirs; a
Chromecast or a TV cast to fetches what it plays itself; a stream at home
whose own playlist names the internet (an HLS list a server at home hands
out) is fetched by the player as it names it; and detection is the
system's word (Qt's QNetworkInformation) - offline mode by itself starts
only once the system says there is no network (its "limited" or "No
Internet" alone, which a DNS blocker or a VPN can make it say with the
internet working, is no reason: a login that fails for want of a network
is), so in the seconds before it the app may still try, and it never
stops what plays: while something plays from the internet it waits, and
that stream's connections go on until it ends. Offline mode turned on by
hand stops it at once.

### Logins are in the settings files, not a keyring

Every login and token is in Qt's settings, as plain text, in files only
this user can read (`core/settings_files.py`: 0600 in a 0700 folder on
Linux and macOS; on Windows the registry and the user's own profile). A
program that runs as the person can read them, and so can anyone with the
person's account or a copy of the disk. Where a drive keeps no permissions
(FAT, some shares) the window asks before it goes on - close, the default,
or go on with that file, remembered by its hash - and server mode without a
window does not start until `--settings-not-private` says so once.

*Why:* a program running as the person can, on most desktops, also ask an
unlocked keyring; no keyring library is a dependency today, and keyrings
differ on every desktop and are missing on some. Kept as it is: the
maintainer's decision.

### Programs started by the app show their arguments

On Linux, by default, every account on the computer can read another's
program arguments (`ps`, `/proc/<pid>/cmdline`), and an Xtream address
carries the login. No program the app starts on a stream - a recorder, a
cast's converter, an external player - is given a provider's address, Nerd's
proxy or a header that may hold a token as an argument:

* **A recording over its own connection** is read by the app itself - the
  rewind buffer's way in, with its reconnects, its route and its check of
  where it lands (`core/dvr_ingest.Ingest`) - and handed to ffmpeg or mpv
  on their standard input (`core/recording.Feed`, `piped`): the recorder's
  command line has `-i pipe:0` and `-protocol_whitelist pipe`, no address,
  and ffmpeg may open no network protocol at all.
* **What `dopeiptv --server` records** (`services/server_recorder.py`) is
  recorded as the window records it: an http(s) channel is read by the
  app into a buffer session of the server's own (`SharedRecording`) and
  written by it - no program at all, no address anywhere but the app's
  memory - and every other address goes through `RecordingManager` as
  below.
* **An HLS playlist**, which the recorder must fetch itself segment by
  segment, and a stream whose certificate the app cannot check while the
  player would play it unchecked (`Feed.classic`), are read through the
  app's bridge under an alias (`net_bridge.Bridge.alias`): an address such
  as `http://<192-bit token>.stream.invalid/~/~/~/~.m3u8`, which names
  neither the host, the folders, the login nor the query, resolves nowhere
  and works through the bridge alone. A request to it is mapped back by
  position, so a playlist's relative addresses resolve as against the real
  one (`real_target`); the bridge sends the source's login and makes the
  TLS connection with the stream's own check. ffmpeg reads only what that
  needs (`player_exec.BRIDGED_PROTOCOLS`); the alias is forgotten as the
  recording ends; with no bridge there is no recording (`NO_BRIDGE`).
* **A cast's converter and ffprobe** (`providers/cast_bridge.py`), the
  **readers of a cast's archive and rewind buffer** (`providers/cast_feed.py`
  - the buffer's own token in its address kept out too), **the Apple TV's
  converter and probe** (`providers/airplay_bridge.py`) and **server mode's
  copy of an HLS channel** (`core/restream.ffmpeg_opener`) read an alias the
  same way (`net_bridge.hand_over`): the options are still chosen by the
  real address (live or an archive window), ffmpeg reads
  `BRIDGED_PROTOCOLS` alone, the alias is forgotten as the run ends, and
  with no bridge nothing is started - the cast says so
  (`cast_err_no_bridge`), server mode's channel is not copied
  (`restream.NO_PRIVATE_WAY`).
* **The bridge's address and key** are in the program's environment,
  which only the same user can read (`net_bridge.child_env`), never in its
  arguments.
* **Nerd's proxy** is the bridge's to go through (`stream_proxy`,
  `upstream`), never `-http_proxy`; **Nerd's headers** are the bridge's to
  add to every request it reads - one to the alias and any plain http one -
  and only those that open nothing (`net_bridge.SAFE_HEADERS`: Referer,
  Origin, Accept... - a value with a login in it never) are ffmpeg's
  `-headers` (`split_headers`). `tests/test_security_rules.py` fails on a
  `-http_proxy` or an unsplit `-headers` anywhere in the app.
* **Every other network address but a multicast group's** - rtsp, rtmp,
  srt and the rest, with a login the app sees (`player_exec.carries_login`)
  or not, since a token may stand in an address's path or under a query
  word of its own (`player_exec.handed_privately`) - is put in a 0600 file:
  the recorder's concat list (its input's options in it from ffmpeg 5) or
  mpv playlist (`recording.concat_list`, `player_exec.private_file`), and a
  cast's converter's, ffprobe's and the Apple TV's converter's concat list
  (`player_exec.private_list`, read as `ffmpeg_protocols` says, gone with
  the run, `net_bridge.Handoff.listed`). With none to be made nothing is
  started (`NO_PRIVATE_FILE`, `cast_err_no_bridge`). A multicast group
  (udp, rtp: an address and a port) with no login stays an argument, and so
  do two more: a SAT>IP tuning, which holds the tuning and nothing else,
  and, for the recorder, an address an ffmpeg older than 5 would read
  without its options.
* **An external player** gets an http(s) address, and every other network
  address but a multicast group's, in a one-entry playlist file of the
  user's own (0600, a name nobody can guess, in `$XDG_RUNTIME_DIR` on
  Linux, deleted a minute later: `players.handoff_file`) - the title too,
  as that file's own line, never `--force-media-title` or `--meta-title`;
  with none to be made it is not started, and the person is told
  (`status_player_no_handoff`).

*What stays:* a header that may hold a token does not reach an https host
other than the stream's own server - a segment on a CDN of its own, which
the bridge only tunnels - from a program the app starts: it would have to
be an argument (FFmpeg's `-/opt <file>` reads only its own options' values
from a file, not an input's, so there is no private way to hand it over);
the log says once which headers were left out of such hops, by name. The
in-app player, which takes no arguments, still sends them everywhere it
did. yt-dlp is given the page it opens. A SAT>IP tuning, a multicast
group, and for the recorder an address an ffmpeg older than 5 would read
without its options, go as an argument (above). A local file's title stands
beside its path, which is there anyway. An alias's token is in the program's arguments: it is
of no use without the bridge's key. A playlist that names an absolute path
whose folder is itself "~" is read under the real folder at that place
(`real_target`'s one difference from the real address). The docked player and multiview are
libmpv inside the app, not a program of their own.

### The cache and the logs keep the folder modes the system gives

Only the settings files are narrowed. The cache - pictures, guides, the
rewind buffer kept on disk, a cast's working files - and the log files are
where the system keeps a user's cache and state, with the modes it gives
them (`core/dvr_store.py`, `core/log.log_dir`). Where the home folder lets
another account in, it can read them. The log is scrubbed of logins and
tokens (`core/log.py`), keeps a private playback's titles out
(`privacy.for_log`), and a private channel's rewind buffer is deleted when
it is left. A channel private from its start keeps its buffer only in
memory out of a disk's reach, or has none
([below](#private-viewing-writes-nothing-to-a-disk)). What runs as the
same user can still read a buffer while it is being written, as it can
read the screen.

*Why:* on most current systems a home folder is closed to other accounts.
Open work.

### Private viewing writes nothing to a disk

What the app makes of a private playback - the rewind buffer, a cast's
converted stream (its HLS segments and the spool a pause is kept in, the
Chromecast's and the Apple TV's converter alike), a subtitle fetched for
it - goes only to a folder in memory that swap cannot carry to a disk, or
is not made at all (`core/memory_safe.py`):

- **In memory, for real.** Linux's `$XDG_RUNTIME_DIR` when it is this
  user's own (0700, theirs) and a tmpfs by the mount table
  (`private_base`), and a folder there made and checked itself - not a
  link, this user's own, 0700, in memory where it really is (`own_folder`,
  `stays_in_memory`; a link under the runtime folder to a disk, or a disk
  mounted there, is found).
- **Out of swap's reach.** A tmpfs's pages can be swapped out, so it
  counts only when what swap keeps cannot be read from a disk afterwards
  (`swap_safe`): no swap, every swap area zram without a backing device (one
  whose backing cannot be read does not count) or
  dm-crypt - directly, or under LVM or another mapping whose every part is
  one - a swap file on such a device; or the tmpfs mounted `noswap`, or
  ramfs. Swap on a plain partition, a swap file on a plain disk, a system
  that cannot be asked: not memory, said once in the log.
- **Else nothing.** A private channel then plays with no rewind buffer of
  the app's (`dvr_pool.NoPrivateBuffer`: no connection opened for the try,
  the player opens the provider's address), mpv's own rewind stays in its
  memory even where Settings put it on the disk (`cache-on-disk`), a cast
  that needs converting is refused with the reason (`cast_err_private_memory`
  - the converter of the stream before goes first), a subtitle reaches mpv
  as bytes in its address (`hex://`), no file. A copy of a subtitle without
  stray right-to-left marks is not made. A subtitle search opened while
  what plays is private asks nothing as it opens: the words in its box -
  the title playing, filled in - go to OpenSubtitles only when the person
  presses Search, and on each search after (`ui/subtitle_search.py`,
  `subs_private_ask`): what the person asks for right then goes (rule 3).
  Otherwise it searches at once, as it always did.
- **Turned on midway.** Private viewing turned on while a channel plays
  from a buffer on the disk - the app's or mpv's - opens it again, in
  memory or with none, and the disk buffer goes (`private_reopen_needed`,
  `DvrPool.on_disk_private`). A cast's converter does not wait for the
  window: each run - a cast, a seek, a recast, the watchdog's reconnect,
  each a run of its own - is watched once a second by a thread of its own,
  and one whose work is on the disk stops within a second of private
  viewing beginning, its files gone - what it wrote in that second was on
  the disk for that second (`cast_bridge._watch`, `airplay_bridge._watch`,
  `private_stopped`). The window then sends the cast again under the same
  rule (`_cast_private_on`); a send still on its way is looked at again
  as soon as it is done (`_cast_send`'s `_private_check`). A disk buffer a
  recording or a cast reads goes on: what it writes is written for them,
  and a second connection is not opened beside it. As the last of them
  lets go, the channel is opened again as when private viewing is turned
  on - nothing more is written there for the private playback alone
  (`dvr_pool.on_private_disk`, the player's `private_on_disk`).
- **Within its room.** A private cast's work - a channel's segments, a
  film's, the archive's or the rewind buffer's pieces, the spool a pause
  is kept in - is measured once a second while it runs, and past its room
  (`private_cap`: Settings' cap, `PRIVATE_CAP`, half of what is free in
  memory) the converter stops and the cast says so (`folder_bytes`): a
  budget looked at once a second, not a ceiling no byte can pass - a
  second's worth of a stream more at the most, never one on the disk.
- **A recording asks.** A recording of what plays, or one set from the
  guide, while viewing privately is a file kept in Recordings with its
  name: asked first, Cancel the default - on the phone and in TV mode as
  their own question (`_rec_private_ok`).
- **A crash.** What a run that ended without tidying left in memory is
  swept at the next start (`memory_safe.sweep`: entries named by their
  process, only one that has ended); a reboot empties it anyway.

macOS and Windows have no such folder for the app: there a private channel
has no rewind buffer and a private cast is not converted. Their swap is
encrypted by the system (macOS always; Windows with BitLocker), but the
app does not count on it.

*What is left.* The player's own memory - mpv's demuxer cache, the decoded
picture - is the process's memory, which the system may swap like any
other; that is protected only as the system's swap is. A hibernation image
is the same.

### Any program on the session bus can drive the app

On Linux the app's media session (MPRIS, `services/mpris.py`) answers
every program on the person's session bus: it can pause, play, seek, skip
channels and quit the app (`Quit`, as the window's close). `OpenUri` does
nothing, and no address with a login is said on the bus.

*Why:* the session bus is the person's own; the desktop's media controls,
the media keys and KDE Connect need these. It can be turned off under
Settings > General.

### A PIN can be short

A profile's PIN is 4 to 12 digits (since 1.6.11; it was 4 to 8) and the
parental lock's at least 4 characters - 12 digits or a passphrase too
(`core/profiles.valid_pin`, `PIN_LONGEST`, `ParentalControl.SHORTEST`);
where either is set the app says six digits or more are much harder to
guess. Four digits are 10 000 PINs. They are kept by PBKDF2 with 600 000 rounds and a
salt each, and after two wrong tries each further one waits, 5 s growing to
a minute (`core/pins.py`). A copy of the settings file - a backup - is slow
to try, not hard, and whoever can change the file can take the PIN out of
it.

*Why:* a PIN keeps a family member out, not someone with the computer
(`core/pins.py` says so); a remote types digits, so a longer PIN is the
person's choice, not a rule - and an old PIN of four goes on working.

### A kids profile holds only with a grown-up's PIN

A kids profile plays only what its lists show - never a file on this
computer, a recording, an address opened by hand, or a row of a category
the source does not list - however it is asked (`_start_playback`'s one
door, `ui/mw_profiles._kids_may_play`), and is left for a grown-up's
profile and opens Settings only with a grown-up's PIN. A new one is made
only once a grown-up's PIN exists; one made before 1.6.9, in a household
with no grown-up's PIN, is left freely so nobody is locked in, until a
grown-up sets one. A provider that files a channel for adults under an
ordinary category, unmarked, is not seen - unless the kids profile sees
**only what a grown-up allows** (since 1.6.11, off by default, its window
behind a grown-up's PIN as the rest: `core/profiles.only_allowed`,
`allowed_for`, `ui/profiles_ui.AllowedPicker`): then only the categories a
grown-up ticked for each source are shown or played, and nothing else -
no other category, no radio. Rows and categories go through one rule
(`ui/mw_profiles.KidsFilter`, its `category_ok`): the lists, the category
column and its search, the favourites, Mine, History and Continue, the
guide, the phone remote's lists and sections, the remote API, Home
Assistant, multiview, the command palette and TV mode; the search across
sources takes each source's own (`services/search_sources.Plan.allowed`,
`core/search_all.visible`, which since 1.6.11 also leaves out what a kids
profile's lists leave out - a row of no category, or of one the source
does not list). A channel of another source - failover's, the Search
page's - is not played for a kids profile at all (the door,
`_kids_may_play`, has no row of the source in use to judge it by), and
server mode is the household's, never a profile's.

*Why:* without the mode the app knows a category by its name and a row by
what the provider says of it; with it, by a grown-up's choice.

### A backup's password is as strong as the password

A backup without a password holds every login as plain text; it is made
only after a warning that says so, Cancel the default
(`ui/mw_settings._backup_save`). With one - at least `PASSWORD_MIN` (10)
characters - a copy of the file can be tried offline as fast as PBKDF2's
600 000 rounds allow: a common password falls
([A backup's password](#a-backups-password)).

*Why:* a memory-hard function is not in the standard library, and the app
does not judge a password beyond its length. A password is the person's
choice, not the default: one forgotten is a backup that cannot be put back
(the maintainer's decision).

### The sync code is the whole key

One code gives its slot, the slot's credential and the key: whoever has it
reads and writes what syncs. Since 1.6.9, Settings > Sync makes a new code
(`_sync_new_code`): the copy under the old one is deleted at the site, and
the other computers join again with the new one.

*Why:* nothing else ties the computers together - no account. A code that
may have been seen is replaced, not guarded.

### Home means any private address

The phone remote, and the remote API and server mode on the home network,
let in any private, link-local or ULA address - every part of a network
split in VLANs (`home_net.is_home_client`). On a shared network - an
office, a hotel, a guest Wi-Fi - that is everyone on it. The token still
holds.

*Why:* a home split in VLANs has its phones on another part than its
computer, and an address cannot tell a home from an office. Narrowing the
phone remote and the remote API to this computer's own networks (as server
mode's tuner is: `home_net.LocalNetworks`, the one place that takes no
token) would shut out every phone and Home Assistant on another part of the
network until the person listed it under Nerd, a feature the remote is not.
Every request still needs the token or the phone's signature, a few wrong
tries lock the address out, and a request from a public address is refused
before anything is read.

### The Watch together relay sees who is in a room

The relay sees the room id, a random id per member, the sizes and times of
the sealed messages and the addresses they come from - so who is in a room
with whom, and when. It keeps rooms in memory only and logs nothing
(`donate/together.py`; the site's privacy policy). Whoever has the code is
in the room: a code is 10 characters (about 50 bits, 600 000 PBKDF2 rounds
before the relay could test a guess - from the room id or from the hash of
the room's credential alike); 1.6.7's codes of 8 and 1.6.8's unsigned
members are refused.

What the credential leaves:

- A room only apps before 1.6.11 use is not bound: its id is all it takes
  to post or read there, as before. Once a newer member binds it, the
  older ones already seated keep their seat while they keep asking; an
  older one who comes later is turned down - their app says the room is
  full - and the others are told once that someone on an older app was
  left out (`Polled.older`).
- Whoever learns a room's id while the relay does not keep the room -
  before its first request, or once it was forgotten - could bind it
  first. The members are then turned down, are told so
  (`together_refused`), and start a new room.
- An older member's messages are judged by time alone (`JOIN_SLACK_MS`):
  after a restart, a relay that says the wrong time could hand an old
  message of theirs again.

*Why:* a relay has to know where to pass a message on; it cannot check
what it cannot open, and it cannot tell an older app from someone without
the code.

### The sync server sees when, and how much

The site sees a slot's id, the sealed copy's size, when it comes and the
address it comes from. It cannot open the copy: the key never leaves the
computers (`core/sync.py`).

*Why:* a copy has to be put somewhere both computers can reach. Padding it
to a few sizes would hide little of how often a computer syncs.

### The sync server can hold a copy back

The sealed counter tells a computer that has seen a later copy. A computer
that has never synced - one that joins, or joins again after a restore -
has seen no counter. Since 1.6.11 every copy also carries, sealed, when it
was put there (`core/sync.saved_at`; an unchanged copy is put back once a
week, `REFRESH_DAYS`, so the time says when a computer last synced), and a
joining computer handed one last saved more than 30 days ago is asked
before anything is merged, put back or applied: "This copy was last saved
N days ago - another computer may have a newer one. Use it anyway?", No
the default, sync then left off (`STALE_DAYS`, `round_trip`'s *stale_ok*,
`ui/mw_sync._sync_ask_stale`; the welcome's Bring step the same). The join
says how old the copy it took was.

*What stays:* a copy handed back from within the month is taken by a
joining computer without a question, and the server can keep a copy back
or lose it altogether. An app up to 1.6.10 does not stamp its uploads, so
a household whose only changes come from such an app is judged by its
entries' own times. A slot that only an app up to 1.6.8 has written is not
bound to its credential until a newer app writes it.

*Why:* inherent to a server that stores; written on the transparency page.

### A test build left for Releases

An update is offered and installed only when it is strictly newer than
the copy running, on either channel and whoever offers it
(`core/updates.offered`, `selfupdate.refuse_older` for every installer).
The one exception: a person who switched from Test builds to Releases
while a test build runs (1.4.0.12) is offered the release that build
carries the number of (1.4.0), which sorts below it, so the channel means
releases again. It holds only for that very build and only once the
window saw the switch (`updates.leave_nightly`, `ui/mw_updates.channel_seen`);
a test build on Releases that was never switched there waits for the next
release.

*Why:* without it a person leaving Test builds would stay on one until the
next release; the release is signed as every one is, and the step is the
person's choice.

### Builds are not yet reproducible

Nobody can rebuild a release and get the same bytes. What vouches for a
download instead is the signed checksum list, the signed build manifest
and GitHub's attestations of where each file was made.

*Why:* not done yet.

### The Windows and Linux packages carry no code signature

The Windows installer and zip are unsigned (SmartScreen asks), and the
AppImage, the .deb and the Flatpak carry no signature of their own. The
Mac builds are signed with Solecraft AB's Developer ID, and releases are
notarised by Apple (test builds are not). The app's own update checks the
signed checksum list on every system.

*Why:* Windows code signing is open work (`docs/MAINTAINING.md`).

### The build key signs in jobs that run other people's code

Every build job signs its build manifest with the build key after it has
run PyInstaller, pip's packages and other people's tools. Whoever takes
that key can make a modified build call itself official - not sign an
update: the update key is used only by the checksum step
(`release.yml`, `tools/sign_sums.py`). A leaked update key cannot be
distrusted by a copy already installed before it updates
(`docs/MAINTAINING.md`, "The release key", has the steps for both).

*Why:* a build has to be signed where it is made.

### A modified build can be made to look official

The build manifest stops the builds that do not bother. A fork that takes
the check out of the code, or sends "official" itself, cannot be stopped
by anything in the app (`core/build_check.py`).

*Why:* the code is open.

### Test builds carry every push

A test build is made and signed from every push to `testing` and offered
to everyone on Settings > General > Update channel > Test builds
(`.github/workflows/nightly.yml`). Releases never take one
(`updates.is_nightly`).

*Why:* that is what the test channel is for; it is off unless chosen.

### yt-dlp is trusted by its own signature

The yt-dlp the app may download is the release the build pinned: its
`SHA2-256SUMS` signed by yt-dlp's key, checked when the build is made, its
SHA-256s in the app's signed release (`core/ytdlp_pin.py`). A release of
yt-dlp's that is itself bad - its key or its build taken over - is not
seen, and it runs as the person. A new yt-dlp comes only with the next
release of the app. The copy is checked against that SHA-256 when it is
installed and then known by its mark - the SHA-256, its size and time - in
the user's own 0700 folder. Since 1.6.11 its bytes are hashed against the
mark right before every run the app starts (`ytdlp.own_copy`, `_may_run`:
finding it for a page, `resolve`, `version_of`), so a file swapped under
the same size and time is not run either, and the log says so. mpv's ytdl
hook runs it by itself - for a page, and as a fallback for a stream that
would not open -, so on Linux and macOS mpv is handed a small wrapper that
hashes the copy with the system's sha256sum, shasum or openssl before each
of those runs and refuses it on a mismatch (`hook_path`, `_hasher`).

*What stays:* on Windows, and where the system has none of those
programs, mpv is handed the copy itself, hashed before each page the app
opens but not before mpv's own fallback runs. Whoever could change the
file between the hash and the run - milliseconds - could as well change
the app itself.

*Why:* an old yt-dlp is the usual reason a stream stops opening. The app's
copy can be turned off (`ytdlp_managed`): then only the system's is used.

### Bugs in the media libraries are theirs

A stream's bytes, a subtitle and a picture are decoded by FFmpeg, libmpv
and Qt. A bug there is reported upstream; the app picks up their fixes
with the libraries it ships. The AppImage and the .deb carry Ubuntu's
libmpv and FFmpeg as they were at build time (22.04's for x86_64, 24.04's
for ARM64), the Flatpak a libmpv built on its runtime's FFmpeg, the
Windows builds a pinned mpv build, the Macs Homebrew's and conda-forge's
(`.github/workflows/release.yml`, `packaging/flatpak/`).

*Why:* a player cannot avoid a decoder.

### A hostile source can make the app work up to its ceilings

The ceilings (`RESPONSE_MAX` 32 MiB, a source's lists `LIST_MAX` 512 MiB, a
guide 2 GiB unpacked, a picture `PICTURE_ALLOC_MIB`) are far above any
real answer, so a source that sends that much makes the app read, unpack
and index that much - memory, disk and time - before it is refused. The
guide's index is built in a process of its own, so a guide cannot take the
window with it.

*Why:* a large provider's real lists and guides come close to tens of
megabytes; a ceiling a real one could reach would break playback.

### What dopeiptv.org keeps is its word

What the site keeps, and for how long, is what its code on the `website`
branch does and its privacy policy says (<https://dopeiptv.org/privacy/>):
the app cannot check it from outside.

*Why:* the code is open and the transparency page shows it; the app sends
nothing that says who asks, so what is kept is little.

### The reviews so far are by AI assistants

Every round in `docs/SECURITY_REVIEWS.md` so far is an independent AI
assistant's, given the review kit's questions and code. No review by a
person outside the project is recorded there yet.

*Why:* none has been made yet. Anyone's findings are welcome as a report
([`SECURITY.md`](../SECURITY.md)).

## How it is kept

- **The rules.** The security and privacy rules in
  [`AGENTS.md`](../AGENTS.md#security-and-privacy-rules) hold for every
  change, however small; a change that cannot follow one stops and asks.
  Each rule names where it is already done right. The module table there
  says what each module owns.
- **The tests.** `tests/test_security_rules.py` holds the rules that read
  the whole code base, each exception listed beside it with its reason.
  Beside it each rule has tests that fail when it is broken - among them
  `test_stream_address.py`, `test_player_command.py`,
  `test_picture_safety.py`, `test_response_cap.py`,
  `test_epg_index_safe.py`, `test_epg_limits.py`, `test_commands.py`,
  `test_commands_outside.py`, `test_local_servers.py`,
  `test_net_bridge_key.py`, `test_stream_guard.py`, `test_player_guard.py`,
  `test_exclusive_bind.py`,
  `test_phone_remote.py`, `test_server_mode.py`, `test_dvr_server.py`,
  `test_settings_files.py`, `test_pins.py`, `test_rec_extras.py`,
  `test_recording_safety.py`, `test_downloads.py`, `test_downloads_hls.py`,
  `test_downloads_window.py`, `test_privacy.py`,
  `test_privacy_playback.py`, `test_privacy_logs.py`, `test_log_redaction.py`,
  `test_report.py`, `test_feedback_fit.py`, `test_remote.py`,
  `test_update_channel.py`, `test_welcome_sync.py`,
  `test_sync.py`, `test_sync_edges.py`, `test_together.py`,
  `test_backup.py`, `test_backup_lock.py`, `test_restore_choices.py`,
  `test_kodi_plain_login.py`, `test_kids_allowed.py`, `test_dest_guard.py`,
  `test_build_check.py`, `test_selfupdate.py`,
  `test_winupdate.py`, `test_macupdate.py` and `test_ytdlp.py`. CI runs
  them on every push, with the audit of the shipped packages.
- **The review kit before every release** (AGENTS.md rule 13): on the
  `website` branch, `tools/review_kit.py --app <checkout> --ref testing`
  gives every question of the transparency page with its code, and the
  questions about the rest of the app, in one `ALL.txt`. At least one
  independent AI assistant reads it; every finding is fixed or answered.
- **The record.** Every round - who looked, what they found, how severe,
  what was done - is in [`SECURITY_REVIEWS.md`](SECURITY_REVIEWS.md),
  nothing left out because it was awkward.
- **The transparency page** shows the code it speaks of, taken from the
  code (`tools/transparency.py` on the `website` branch, which stops on a
  host the app names that the page does not explain), and never claims
  more than the code does (AGENTS.md rule 11).
- **Reports from outside** are welcome: [`SECURITY.md`](../SECURITY.md).
