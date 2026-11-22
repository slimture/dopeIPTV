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
  logged.
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
| From a source on the internet: an `smb://` address (on Windows a sign-in there with the person's account), raw `tcp://` or `tls://` to any service, ftp, WebDAV, a gateway - or an address on this computer or a link-local one (the printer's page, a local engine, a cloud's metadata service) | `core/stream_address.py`: a source whose own address is on the internet (`on_internet`, its backup addresses too) hands out only the media protocols (`MEDIA`), never a host on this computer or link-local in any spelling (`host_refusal`: 127.1, 2130706433, `::ffff:127.0.0.1`, localhost, this computer's name, 0.0.0.0, 169.254/16, fe80::/10); a private address of the home network stays. A home network's source and a playlist file as before; what the person's rewrite rules or udpxy make is theirs |
| From a source on the internet: a logo or poster on this computer or a link-local host, so the app itself sends requests there | `providers/client._pictures_kept`: such a picture is taken out of that source's lists and pages by the same rule (`stream_address.picture_refusal`) |
| A public name whose DNS answer is this computer or a link-local host (DNS rebinding), or a redirect from the internet to this computer, a link-local host or the home network - for the app's own requests (guides, pictures, the APIs, the rewind buffer, a cast's address) | `core/dest_guard.py`: every request through `core/_lazy_requests.py` and `core/net_adapter.py` is checked on its socket after it connects and before a byte is sent (`check_peer`: this computer only for a name that says so, link-local only for a link-local or home name, a cloud's metadata service only by its address); every redirect requests follows, and each one the cast follows by hand, judged by its target as written and as it resolves (`redirect_refusal`: http(s) only, never from the internet inward, the same host again allowed) |
| A line read as an option of an external mpv or VLC, or of the recorder (`--log-file=`, `--include=`) | The address after `--`, and nothing but a stream's address or a file's path handed over (`media/players.py`, `core/recording.py`) |
| ffmpeg reading `concat:`, `subfile:`, `unix:` or a local file behind an address | `core/player_exec.ffmpeg_protocols`: `-protocol_whitelist` with a stream's protocols wherever ffmpeg or ffprobe is started for a stream |
| A picture that names a file: `/dev/zero`, or `\\host\share\x.png` (on Windows a login at that host) | `core/picture_files.py`: a file is read as a picture only inside the app's own picture folders, only a plain file, at most 64 MB |
| An SVG logo that makes Qt open a file or read a stylesheet | `core/svg_safe.screened`: the picture written out again without what it names |
| An answer that never ends, or JSON nested past what Python reads | `core/_lazy_requests.py`: `RESPONSE_MAX` (32 MiB), a source's lists `LIST_MAX` (512 MiB), then `ResponseTooLarge`; too deep is no JSON |
| A guide with a DTD or entities, an endless download, a gzip bomb | `providers/epg.py`: `refuse_entities`, `read_capped`, `gunzip_gently` (`GUIDE_DOWNLOAD_MAX`, `GUIDE_UNPACKED_MAX`: 2 GiB); the index is built in a process of its own (`providers/epg_index.py`) |
| A name shown as markup | `Qt.TextFormat.PlainText` and `ui/widgets.plain_text`, the text over the picture too |
| A title spliced into the command after a recording, or into its file name | `core/rec_extras.py`: no shell, placeholders filled per argument, a batch file on Windows given text only (`through_cmd`); a name without control characters or device names, a folder never outside the recordings folder (`core/recording.py`) |

**What is left.**

- The provider sees what it serves, private viewing or not.
- The ceilings are far above any real answer, so a hostile source can still
  make the app read and work up to them.
- A list chooses where its pictures come from: [below](#a-list-chooses-where-its-pictures-come-from).
- The player and the recorder look names up themselves: [below](#the-player-and-the-recorder-look-names-up-themselves).
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
| A large or looping mDNS answer | `providers/mdns.py`: `MAX_PACKET`, `MAX_PACKETS`, `_MAX_JUMPS` |
| Taking a known TV's name to get the next cast | `providers/casting.py`: a device known by its kind, its own id and its address (`ident`); one answering to the name as another is not cast to until it is picked, its address in sight |
| A box's XML with a DOCTYPE | `providers/boxes.parse_xml` drops it; a SAT>IP description with an entity is refused (`providers/satip.parse_description`) |
| A stream address in a box's list | The same `core/stream_address.py` rules as a provider's |
| Guessing a token of the app's servers | 192 bits from `secrets` for the phone remote, the remote API and server mode, compared with `hmac.compare_digest`; a token bucket per address and a lock-out after wrong tries (`core/phone_remote.RateLimiter`, server mode the same) |
| Taking the phone remote or the remote API down from address after address, each with a fresh bucket | A budget for every address together beside each one's own (`RateLimiter`'s *total_rate*: 60 requests a second, 360 at once; a list's pictures 120 and 900), 429 once it is spent; lock-outs stay per address, and this computer is neither shut out nor counted (`this_computer`) |
| Sending the player, through the remote API, to a service on this computer or to a link-local host (a router's page, a cloud's metadata service) | `core/remote_api.play_refusal`, on the server's thread: an address that names this computer - loopback in any spelling, localhost, 0.0.0.0, an IPv4 address inside IPv6, an empty host - or a link-local or metadata host, or whose name resolves to one now, is refused with the reason (400); AceStream's engine at `127.0.0.1:6878/ace/` and the home network stay, and a script on this computer, which reaches them itself, plays as before (`remote_api.asker`) |
| Opening connections by the thousand, or holding them open | `core/exclusive_bind.py`: 256 at once, 64 per address (`ConnectionLimitMixin`), a client that says nothing let go (`IdleTimeoutMixin`) |
| Reaching the app's servers from the internet | `core/home_net.is_home_client`: loopback, the private ranges, link-local and IPv6 ULA, never a public address |
| Making a playlist's addresses name a host of its choosing, so the token goes there | `home_net.host_for_addresses` (`core/remote_api.py`, `services/home_server.py`) |
| A cast's own server (a file for a DLNA TV, the converter, the AirPlay bridge) | A 144-bit token in the path, compared in constant time, one stream, stopped with the cast (`providers/dlna.py`, `providers/cast_bridge.py`, `providers/airplay_bridge.py`) |

**What is left.**

- Anything on the network can announce itself under a familiar name. The
  app shows each device's address; it cannot prove what a device is.
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
| A request to the phone remote, the remote API, server mode, the rewind buffer or a cast | A token in every address that the page cannot know; the phone remote never counts a web page's guesses and refuses its changes (`cross_site`: `Sec-Fetch-Site`, `Origin`) |
| Someone on the home network who reads the phone remote's traffic | Its key never crosses the network: it is after the `#` of the code's address (`/r/#key`, which no browser sends) and kept on the phone; every request is signed with it - HMAC-SHA256 over the method, the address, the time, a nonce and the body (`core/phone_remote.signature`, the page's own SHA-256 since `crypto.subtle` needs https) - within a minute of this computer's clock, each nonce once (`SIGN_WINDOW_MS`, `NONCES_MAX`): one seen on the way can be neither changed nor sent again. A picture's address is signed until a quarter of an hour on (`PICTURE_TTL_MS`). An address of before, with the key in its path, makes a new key at once (`leaked`, `on_leaked`). The calendar app, which cannot sign, gets a key of its own that opens the calendar alone (`calendar_token`) |
| Someone on the home network who saw a converted cast's address | The converter serves only the receiver it was started for (`providers/cast_bridge.receiver_addresses`, 403 for any other address), a new token each run; the Apple TV's bridge, which is not told the Apple TV's address (Apple's menu picks it), serves this computer and the first other address that asks for the run's stream alone - the Apple TV, within seconds of being told -, 403 for any other (`providers/airplay_bridge.admits`) |
| Server mode's tuner endpoints, which take no token | Refused for a web page (`cross_site`) and under a name that is not a home's (`services/home_server.tuner_host_ok`, `home_net.HOME_SUFFIXES`): a rebound name is the page's own public one |
| The loopback proxy | A key made for each run as the proxy's login, 407 without it, and a page's `GET /...` refused (`core/net_bridge.py`) |
| Script in the pages the app serves | A Content-Security-Policy of their own script and style hashes (`core/phone_page.py`, `core/server_page.py`) |
| A sign-in's redirect made up by the page | Trakt's `state` checked and PKCE (`providers/trakt.py`); the listener on 127.0.0.1, its port never shared (`providers/oauth_loopback.py`) |
| The app opening an address a service's answer chose | `core/xdg.open_url`: https only, and server mode's page on this computer (`tests/test_security_rules.py`) |

**What is left.**

- A link to a channel plays without a question: [below](#a-link-to-a-channel-plays-without-a-question).
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
| Using the loopback proxy to go out as the person, through their route | A key made for each run (`core/net_bridge.py`) |
| Reading the rewind buffer's stream | A 144-bit token, loopback only (`core/dvr_server.py`) |
| Catching the Trakt sign-in's redirect | Its port never shared (`core/exclusive_bind.ExclusiveBindMixin`, `SO_EXCLUSIVEADDRUSE` on Windows), and the code it carries useless without the PKCE verifier that stays in the app (`providers/trakt.py`) |
| A socket of its own where Discord or lircd would be | `core/discord_rpc.py`: a socket this user owns only; `core/tv_input.lirc_socket_ok`: lircd's when root or this user owns it, or in a folder nobody else can write to |
| Reading a media server's token off the session bus | `services/media_session.art_for_bus`: no address with a login on the bus |
| A file planted in the cache | The guide's index is plain data with no code path - a header, texts and arrays' bytes checked against it (`providers/epg.read_index_blob`); no pickle anywhere in the app (`tests/test_security_rules.py`); pictures from disk only in the app's folders (`core/picture_files.py`) |

**What is left.**

- A program running as the person reads what the person can: [below](#logins-are-in-the-settings-files-not-a-keyring).
- Another account can see the arguments of the programs the app starts: [below](#programs-started-by-the-app-show-their-arguments).
- The cache and the logs keep the system's folder modes: [below](#the-cache-and-the-logs-keep-the-folder-modes-the-system-gives).
- Any program on the session bus can drive the app: [below](#any-program-on-the-session-bus-can-drive-the-app).
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
| The player's and recorder's https streams from the internet | The certificate checked against certifi's authorities (`nerd.checks_certificate`), unless the person let that source play unchecked when asked |
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

**A backup** holds every setting a person would miss, logins included -
playlists' passwords, Trakt's and OpenSubtitles' sign-ins, the sync code -
and every profile with its PIN as kept (`core/backup.py`). Without a
password they are plain text in it; with one the file is encrypted
([below](#a-backups-password)). The app says so where the file is made, and
writes it readable only by its owner where the system has such a thing.
Left out: the remote API's, the phone remote's and server mode's tokens,
the media servers' device id, where the windows sit, caches. Whoever has a
file without a password has those logins.

**A backup someone else made** is outside input. Before it is put back,
the window names what would run something, let others in or send the
connections elsewhere (`backup.risky`, `risky_sources`,
`ui/backup_dialogs.RestoreBackupDialog`): the command after a recording
in full, mpv's options and folder, a proxy, headers, udpxy, webhooks, an
MQTT broker and a DNS server (`RISKY`); the remote API, sharing, MQTT's
commands and server mode switched on, and adult channels through it
(`RISKY_ON`); a sync code to join; and each playlist's proxy, headers,
rewrite, udpxy, catch-up address, backup addresses, own DNS and trusted
certificate (`RISKY_SOURCE`). A restore over everything waits for the
next start (`stage`, `apply_pending`). A colour from it is used only as
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
may give the backup a password where it is made (Settings > General >
Backup, a field and its repeat). Without one the file is exactly what a
backup always was, and a plain or an old file restores as before.

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
playlist cannot sign. What holds them is
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
address (`TUNER_RATE`), and the person is told what that means.

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
own under a token, the provider's staying here. A Chromecast is Google's
device running Google's receiver.

*Why:* a DLNA TV, a Kodi and an Apple TV are made to fetch a stream
themselves, and a cast through the converter costs the computer's
processor and starts later. The TV is one the person chose to cast to;
the app says which device a name stands for (`casting.where`).

### Every internet stream's certificate is checked (since 1.6.11)

The player and the recorder check the certificate of every https stream on
the internet against certifi's authorities, as the app's own requests
always did, whether Nerd is on or not (`nerd.checks_certificate`,
`nerd._apply_tls` before every stream - the docked player and each
multiview cell -, `nerd.strict_tls_recorder` for a recorder that reads the
address itself). Someone on the network path who answers with a
certificate of their own gets nothing - the login in an Xtream address
least of all. A server at home is checked as mpv's own default does (0.37
nothing, a newer mpv everything) - its certificate is often its own -
unless Nerd's strict check asks for it too (Nerd > Network, "Check every
stream's certificate", `nerd_tls_strict`). A host whose certificate the
source pins is the bridge's to check (below); a `tls-verify` the person
typed into mpv's options or mpv.conf wins (`Nerd.typed_tls`).

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
proxy of Nerd's for the player the stream goes through that proxy, not the
bridge (`nerd.player_route`); and a cast cannot carry it - the app asks
first (`ui/mw_cast._cast_pin_ok`).

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
operator's list names. A source of the home network's, or a playlist file,
names what it likes. A public name that resolves to a home address is not
caught for the player (mpv looks names up itself). The app's own requests
are caught for this computer and link-local hosts whatever the name
(`dest_guard`), and since 1.6.11 a name a source on the internet named for
its pictures or its guide (`dest_guard.note_outside`, from
`client._pictures_kept` and `M3UClient._guide_ok`) never reaches a home
address either, checked on the socket after it connects: the source's own
server names (`note_own`, from `make_client`: its server, list, guide,
portal and backup addresses - split-horizon DNS for one's own server is
common) and Plex's `*.plex.direct` (a home address by design) excepted. At
most `OUTSIDE_MAX` names are kept; a list that names more makes every
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

### The player and the recorder look names up themselves

The app's own requests are checked where they land (`core/dest_guard.py`).
What mpv and FFmpeg open themselves - a stream, an HLS playlist's segments,
a subtitle - is judged by its address as written
(`core/stream_address.host_refusal`): a public name whose DNS answer is
127.0.0.1, a redirect there inside the stream, or an HLS playlist whose
segments are there, is theirs to follow. FFmpeg reads only a stream's
protocols there (`ffmpeg_protocols`), so what such a request can reach is
a web service on this computer or the home network, not a file.

*Why:* the libraries resolve and connect inside themselves; the app cannot
see the socket. Routing every stream through the app's own proxy would put
a copy of each stream through Python.

### A link to a channel plays without a question

A web page can open `dopeiptv://channel/<name>`: the window comes forward
and plays that channel of the person's own source, as a typed number
would - only a channel the lists show, never one of a hidden or locked
category (`ui/mw_commands._play_channel_named`), and through the same
questions as a zap (a recording in the way, `_guard_stream_switch`).

*Why:* such a link names a channel of the person's own and can only change
what plays; a link that names an address is asked about first.

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
carries the login. Since 1.6.11:

* **A recording over its own connection** is read by the app itself - the
  rewind buffer's way in, with its reconnects, its route and its check of
  where it lands (`core/dvr_ingest.Ingest`) - and handed to ffmpeg or mpv
  on their standard input (`core/recording.Feed`, `piped`): the recorder's
  command line has `-i pipe:0` and `-protocol_whitelist pipe`, no address,
  and ffmpeg may open no network protocol at all.
* **An external player** gets an http(s) address in a one-entry playlist
  file of the user's own (0600, a name nobody can guess, in
  `$XDG_RUNTIME_DIR` on Linux, deleted a minute later:
  `players.handoff_file`).
* **Nerd's bridge** is given to a recorder in its environment, which only
  the same user can read (`net_bridge.recorder_env`), never as an argument.

*What stays:* an HLS playlist the recorder must fetch itself, segment by
segment - an address ending in .m3u8, or one the app found to be a
playlist -, and a stream whose certificate the app cannot check while the
player would play it unchecked (a server at home, a source let play
unchecked), go to the recorder the old way, as an argument
(`Feed.classic`); so do a cast's converter (`providers/cast_bridge.py`)
and server mode's copy of an HLS channel (`core/restream.ffmpeg_opener`).
The docked player and multiview are libmpv inside the app, not a program
of their own.

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

A profile's PIN is 4 to 8 digits and the parental lock's at least 4
characters (`core/profiles.valid_pin`, `ParentalControl.SHORTEST`): four
digits are 10 000 PINs. They are kept by PBKDF2 with 600 000 rounds and a
salt each, and after two wrong tries each further one waits, 5 s growing to
a minute (`core/pins.py`). A copy of the settings file - a backup - is slow
to try, not hard, and whoever can change the file can take the PIN out of
it.

*Why:* a PIN keeps a family member out, not someone with the computer
(`core/pins.py` says so); a remote types digits, so a longer PIN is the
person's choice, not a rule.

### A kids profile holds only with a grown-up's PIN

A kids profile plays only what its lists show - never a file on this
computer, a recording, an address opened by hand, or a row of a category
the source does not list - however it is asked (`_start_playback`'s one
door, `ui/mw_profiles._kids_may_play`), and is left for a grown-up's
profile and opens Settings only with a grown-up's PIN. A new one is made
only once a grown-up's PIN exists; one made before 1.6.9, in a household
with no grown-up's PIN, is left freely so nobody is locked in, until a
grown-up sets one. A provider that files a channel for adults under an
ordinary category, unmarked, is not seen.

*Why:* the app knows a category by its name and a row by what the
provider says of it.

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
computer, and an address cannot tell a home from an office.

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
  `test_net_bridge_key.py`, `test_exclusive_bind.py`,
  `test_phone_remote.py`, `test_server_mode.py`, `test_dvr_server.py`,
  `test_settings_files.py`, `test_pins.py`, `test_rec_extras.py`,
  `test_recording_safety.py`, `test_privacy.py`,
  `test_privacy_playback.py`, `test_privacy_logs.py`, `test_log_redaction.py`,
  `test_report.py`, `test_feedback_fit.py`, `test_remote.py`,
  `test_update_channel.py`, `test_welcome_sync.py`,
  `test_sync.py`, `test_sync_edges.py`, `test_together.py`,
  `test_backup.py`, `test_backup_lock.py`, `test_build_check.py`, `test_selfupdate.py`,
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
