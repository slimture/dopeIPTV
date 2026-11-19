# Security Policy

## Supported versions

Only the **latest release** receives security fixes — the app has a built-in
update check, and upgrading is a download away.

| Version | Supported |
| ------- | --------- |
| latest release | ✅ |
| older releases | ❌ |

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Report privately via GitHub's built-in flow: the repository's
**Security tab → "Report a vulnerability"**. You'll get an acknowledgement
as soon as the report is seen (normally within a few days), and credit in
the release notes of the fix unless you prefer otherwise.

If the Security tab is unavailable to you, contact the maintainer
[@slimture](https://github.com/slimture) through GitHub.

## Scope notes

Useful context for researchers:

- **Servers the user configures**: their IPTV provider (Xtream Codes, an
  M3U playlist or a Stalker / Ministra portal) and its logos, XMLTV guide
  addresses, the receivers and TV servers at home (Enigma2, Tvheadend,
  HDHomeRun, SAT>IP, VDR, DVBViewer, NextPVR, MythTV), Jellyfin, Emby and
  Plex servers, and - in Nerd, off by default - a proxy, backup addresses,
  udpxy, webhooks and an MQTT broker.
- **dopeiptv.org**, the project's own site (`core/updates.py`,
  `core/report.py`, `core/sync.py`, `core/together.py`):
  - the update check (`releases.json`, `nightly.json`; once a day, every
    six hours on the test-build channel, unless turned off) and the
    download of an update, which the AppImage, the Windows installer's copy
    and the Mac app install themselves, each checked against a checksum
    list signed with the project's release key (`core/selfupdate.py`); the
    user agent names the app's version and system, and the site counts
    update checks from its log;
  - feedback and error reports, only when the person presses Send, and the
    answers to them (`/feedback/report`, `/feedback/replies`);
  - settings sync when turned on (`/sync/`: ciphertext only, under a random
    slot, end-to-end encrypted with a key made from the sync code);
  - Watch together when a room is used (`/together/`: a relay of sealed
    messages);
  - the Trakt sign-in's redirect (`/trakt/callback`), handed on to the
    app's loopback listener.

  Every request to the site says whether the build is official, modified
  or from source (`X-dopeIPTV-Build`, `core/build_check.py`); a modified
  build uses none of these services.
- **GitHub**: the update check and its download when the site does not
  answer, test builds, and the official yt-dlp release the app downloads
  when asked (checked against its published checksums, `core/ytdlp.py`);
  yt-dlp then talks to the site of the address opened (YouTube, Twitch and
  the like).
- **Other fixed services**: TMDB (metadata, artwork and the weekly popular
  chart); radio-browser.info's servers while Radio is used (a station's
  click is reported only when turned on); the iTunes Search API for album
  art of music that has none (on by default, Settings → Local files);
  SiliconDust's `api.hdhomerun.com` for an HDHomeRun's guide; plex.tv for
  the Plex sign-in and the account's servers; and, only when the user
  turns them on, Trakt, Simkl, OpenSubtitles.com and DNS over HTTPS
  (Nerd's own DNS: Cloudflare, Quad9, Google or the user's own resolver).
  Discord is told what plays over its local IPC socket only, when turned
  on.
- **On the local network** it discovers devices: Chromecasts and Kodi
  (mDNS), DLNA/UPnP TVs and SAT>IP servers (SSDP), HDHomeRun tuners (UDP
  broadcast), Jellyfin/Emby (UDP 7359) and Plex (GDM); "Find on my
  network" also probes the ports these servers use on the local /24 (and
  on the networks listed under Nerd › Network › Other networks).
- **Servers the app runs itself** (`core/exclusive_bind.py`):
  - on 127.0.0.1 only: the app's own rewind buffer for the embedded player
    (random token, `core/dvr_server.py`); a loopback proxy that carries the
    player's and recorder's connections over a source's own DNS or pinned
    certificate (Nerd, `core/net_bridge.py`); the Trakt sign-in's redirect
    listener, briefly, on port 41794 (`providers/oauth_loopback.py`);
  - on the LAN while a cast needs it, each under a random path and stopped
    with the cast: a local file served to a DLNA TV (`providers/dlna.py`),
    the converter that makes a stream - or a channel's archive or rewind
    buffer - one a Chromecast can play (`providers/cast_bridge.py`), and on
    macOS the HLS bridge for an Apple TV (`providers/airplay_bridge.py`);
  - off until turned on: the phone remote (home network only - private
    ranges, link-local and IPv6 ULA, never a public address; a long random
    token in every address, rate limit and lock-out, `core/phone_remote.py`);
    the remote API and the shared M3U/XMLTV (Nerd; a token on every request,
    this computer only unless chosen, then the home network,
    `core/remote_api.py`, `services/share.py`); server mode (Nerd, or
    `dopeiptv --server`: HTTP for home addresses with a token on all but
    the HDHomeRun tuner's endpoints, which with the UDP 65001 discovery
    answer only this computer's own networks and Nerd's other networks,
    `services/home_server.py`, `core/hdhr.py`);
  - a local socket of the user's own for a second start's command
    (`core/instance.py`).
- Credentials (provider logins, the sync code, tokens) are stored
  **locally** in Qt's QSettings store, unencrypted — the standard threat
  model of a desktop app — and masked in logs; error reports are scrubbed
  of addresses, logins and names before the person sees them
  (`core/report.py`). There is no telemetry: what plays goes only to what
  the person turns on (Trakt, Simkl, their own media server, Discord, a
  Watch together room's sealed messages), and dopeiptv.org gets nothing
  beyond the requests above. (The website counts downloads and update
  checks from its own web server's log.)
- Playback is delegated to **libmpv/FFmpeg**; vulnerabilities in stream
  demuxing/decoding should be reported upstream, though we will gladly ship
  bundled-library updates that pick up their fixes.
