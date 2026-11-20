# Security policy

dopeIPTV is meant to be open, private and hard to attack. What it protects,
who could attack it, what stops them and what is honestly left is its
threat model, [`docs/SECURITY.md`](docs/SECURITY.md); every review round
and what came of it is in
[`docs/SECURITY_REVIEWS.md`](docs/SECURITY_REVIEWS.md). The code behind
what the app sends out is shown at <https://dopeiptv.org/transparency/>.

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Report it privately through GitHub:
**<https://github.com/slimture/dopeIPTV/security/advisories/new>** (the
repository's Security tab → "Report a vulnerability"). The same address is
in <https://dopeiptv.org/.well-known/security.txt>. If the form is not
available to you, contact the maintainer
[@slimture](https://github.com/slimture) through GitHub and say only that
you have something to report privately.

What helps most:

- the version (Help → About; on a Mac the dopeIPTV menu) and the
  package - AppImage, .deb, Flatpak, the Windows installer or zip, the Mac
  app, or a checkout at a commit;
- the system it ran on;
- the steps, and what an attacker gains: what they must control first
  (a provider, a device at home, a web page, a program on the computer...)
  and what they get;
- a log if it shows the problem (`DOPEIPTV_LOG=debug
  DOPEIPTV_LOG_FILE=/path`): logins and tokens are taken out of it, but
  read it before you send it.

English or Swedish, either is fine.

## What happens then

dopeIPTV is maintained by one person, so these are goals, not promises of
a team:

- **an acknowledgement within a week;**
- **for a high or critical problem**, as
  [`docs/SECURITY_REVIEWS.md`](docs/SECURITY_REVIEWS.md) defines them,
  **a fix or a plan within 30 days**, and a release as soon as the fix is
  ready;
- for the rest, a fix in a coming release, or an answer saying why it
  stays as it is - a limit then goes into the threat model.

You are kept informed while it is worked on. Please keep the problem
private until a release with the fix is out, or until we agree otherwise.
Then it is published as a GitHub security advisory, named in the release
notes, and recorded in `docs/SECURITY_REVIEWS.md` - with credit to you,
unless you prefer otherwise.

## Supported versions

Only the **latest release** receives security fixes: the app checks for
updates itself, and the AppImage, the installed Windows app and the Mac app
update in place. A fix reaches the test builds (Settings → General →
Update channel → Test builds) first, as it is made.

| Version | Supported |
| ------- | --------- |
| latest release | ✅ |
| test builds of the next release | ✅ (fixes land here first) |
| older releases | ❌ |

## Scope

**In scope:**

- the app, in every package the project releases (AppImage, .deb, Flatpak,
  the Mac .dmg, the Windows installer and zip, the Python package) and from
  a checkout of this repository;
- the way it is built and updated: the workflows, the pinned build inputs,
  the signed checksum list and build manifest, the update itself;
- dopeiptv.org's services: the update feed and downloads, feedback and its
  answers (`/feedback/`), settings sync (`/sync/`), the Watch together
  relay (`/together/`), the Trakt sign-in's redirect (`/trakt/callback`),
  supporter keys (`/donate/`), and the site itself.

**Out of scope:**

- providers' own servers and streams, and the boxes and media servers
  people add - report those to whoever runs them;
- third-party services: TMDB, Trakt, Simkl, OpenSubtitles, Plex.tv,
  radio-browser.info, GitHub, Stripe, Google's Chromecast, Apple's AirPlay
  and the rest listed below;
- bugs in libmpv, FFmpeg, Qt or Python themselves: report them upstream -
  we gladly ship the bundled libraries' updates that fix them;
- the limits the threat model already names ("Honest limits"), unless you
  show one is worse than it says;
- what needs the person's own account on the computer, an administrator,
  or the unlocked computer;
- reports from an automated scanner without a shown way to use them.

## Safe harbour

Research done in good faith within these lines is welcome, and we will not
take or support legal action against it:

- use your own installs, accounts, sources and devices;
- never touch data that is not yours: no one else's sync copy, room,
  feedback or account - if you reach one by accident, stop, keep nothing,
  and tell us;
- never disrupt a service: no load or denial-of-service tests against
  dopeiptv.org, no spam through its feedback, no guessing at others' sync
  slots or rooms;
- no social engineering of people, and nothing against Stripe, GitHub or
  any other company's systems;
- give us the time above before anything is made public.

If you are not sure whether something is within these lines, ask first
through the private report.

## What the app connects to

Useful context for researchers; the threat model says what protects each.

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
  - settings sync when turned on (`/sync/`: ciphertext only, under a slot
    made from the sync code, end-to-end encrypted with a key made from the
    same code; once bound, a slot is read, written or deleted only with a
    credential from the code too);
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
    certificate (Nerd, a key made for each run, `core/net_bridge.py`); the
    Trakt sign-in's redirect listener, briefly, on port 41794
    (`providers/oauth_loopback.py`);
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
- **Credentials** (provider logins, the sync code, tokens) are stored
  **locally** in Qt's settings, unencrypted, in files only the user can
  read (0600 in a 0700 folder on Linux and macOS,
  `core/settings_files.py`), and kept out of logs; error reports are
  scrubbed of addresses, logins and names before the person sees them
  (`core/report.py`). There is no telemetry: what plays goes only to what
  the person turns on (Trakt, Simkl, their own media server, Discord, a
  Watch together room's sealed messages), and dopeiptv.org gets nothing
  beyond the requests above. (The website counts downloads and update
  checks from its own web server's log.)
- Playback is delegated to **libmpv/FFmpeg**; vulnerabilities in stream
  demuxing/decoding should be reported upstream, though we will gladly ship
  bundled-library updates that pick up their fixes.
