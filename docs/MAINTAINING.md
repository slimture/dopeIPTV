# Maintaining dopeIPTV

Everything someone needs to take the project over - a person or an AI
assistant of any kind. Nothing here depends on a particular tool: the
whole process is git, GitHub Actions, a POSIX shell, Python and `jq`.

Read this first, then [`AGENTS.md`](../AGENTS.md) (conventions and the
module map in one page) and [`ARCHITECTURE.md`](ARCHITECTURE.md).

## What the project is

- A desktop IPTV **player** (not a provider) in Python: PyQt6 for the
  interface, libmpv for playback (through `python-mpv`), ffmpeg for
  recording. GPL-3.0-or-later.
- **Linux is the primary target**; macOS and Windows are supported from
  the same code. A change for one platform must never make Linux worse.
- **Stability comes first, and video playback must never break.** The
  embedded mpv/OpenGL path (`media/embedded.py`, `media/video_surface.py`)
  is the heart of the app - change it only with a clear reason and a test.
- No telemetry, no accounts, no server side. The app talks to the user's
  provider plus GitHub, TMDB and - when turned on - Trakt and OpenSubtitles.

## Where things live

| Branch | What | Who pushes |
|---|---|---|
| `testing` | All app work. Every push runs CI and builds test builds. | everyone, via PR or directly |
| `main` | Released code only: `testing` merged at each release. | `tools/release.sh` |
| `website` | The site at <https://dopeiptv.org> (static, rendered by `jq`); its old address, iptv.dope.rs, sends every request on there. | website changes |

**No other branches on GitHub**, not even for an experiment: a pushed
branch runs workflows and leaves artifacts behind. To try the release
build without tagging, run `release.yml` by hand (`workflow_dispatch`) on
`testing` - the "Attach to release" steps only run on a tag.

| Path | What |
|---|---|
| `dopeiptv/` | The app (layers: `providers/ → core/ → media/ → services/ → ui/`) |
| `dopeiptv/i18n.py`, `dopeiptv/locale/*.json` | English source strings; the other 26 languages |
| `tests/` | pytest suite (headless, `QT_QPA_PLATFORM=offscreen`) |
| `tools/` | `release.sh`, translation status, diagnostics probes |
| `packaging/` | AppStream metainfo, desktop file, icons, Flatpak manifest, macOS helpers |
| `*.spec` | PyInstaller specs (Linux, macOS, Windows) |
| `.github/workflows/` | `ci.yml`, `nightly.yml`, `release.yml` |
| `docs/` | Architecture, developer notes, features, translating, this file |

## Every day

```bash
git clone https://github.com/slimture/dopeIPTV && cd dopeIPTV
git checkout testing
python3 -m pip install -r requirements.txt ruff mypy pytest
# libmpv from the system: apt install libmpv2 / dnf install mpv-libs / brew install mpv
python3 dopeiptv.py                       # demo channels need no account
```

The quality gate - CI runs exactly this, so run it before every push:

```bash
ruff check dopeiptv tests
mypy
QT_QPA_PLATFORM=offscreen python3 -m pytest -q
python3 dopeiptv.py --self-check          # libmpv loads, the GL player builds
```

A test may only import what CI installs (`requirements.txt` + ruff, mypy,
pytest). Logs: `DOPEIPTV_LOG=debug DOPEIPTV_LOG_FILE=/tmp/dope.log`.

## The workflows

- **`ci.yml`** - every push and pull request: ruff, mypy and pytest on
  Linux, plus an advisory dependency audit. (The packaging checks -
  versions, icons, paths workflows name - are tests in the suite.)
- **`nightly.yml`** - every push to `testing`: builds every package the way
  a release does (it calls `release.yml` with `nightly_version`) and
  publishes them as a **pre-release** `nightly-<version>.<run>`. Users on
  Settings → General → Update channel → Test builds get these. Only the
  newest few are kept; pre-releases never count as "latest", so the
  website and the Releases channel never see them.
- **`release.yml`** - on a `v*` tag: builds and attaches every package -
  sdist + wheel, AppImage + .deb (x86_64 and aarch64), macOS .dmg (arm64
  and x86_64), Windows .zip (x64 and arm64), Flatpak - plus `SHA256SUMS`.
  Each job uploads its own files, so one failing format never blocks the
  others. **Experimental legs** may fail without failing the run: the
  Flatpak and the Intel Mac. A flaky download (GitHub, conda-forge,
  micromamba) is the usual reason a leg goes red - re-run the failed jobs
  before looking for a bug.

Platform notes that are easy to forget:

- macOS: the Apple Silicon build uses Homebrew's mpv on `macos-15`
  (so it needs macOS 15+); the Intel build uses conda-forge's mpv and
  ffmpeg on `macos-15-intel` (PyQt6's universal wheel needs macOS 14+).
  GitHub keeps the Intel runner until about August 2027, and Apple has
  said macOS 26 is the last for Intel - plan to drop the Intel build then.
- macOS builds are signed with Solecraft AB's Developer ID and notarised
  ("macOS signing" below). Windows builds are unsigned (SmartScreen "Run
  anyway"); the help page and README explain it.
- Windows comes two ways: an installer (NSIS, zlib licence -
  `packaging/windows/dopeiptv.nsi`), per user in
  `%LOCALAPPDATA%\Programs\dopeIPTV` without administrator rights, with a
  Start menu entry and an entry under Settings -> Apps - and the portable
  zip. The release job installs it, installs it again over itself the way
  the app's update does (`/S /relaunch`), self-checks and removes it.
- The system's media controls off Linux come through PyObjC (macOS, Now
  Playing) and PyWinRT (Windows, the SMTC): `requirements.txt` installs
  them with platform markers, the specs name their modules, and
  `--self-check` fails a bundle without them. CI cannot press a media key:
  a change in `services/media_macos.py` or `media_windows.py` is tried on
  a real Mac or PC (Control Center, the lock screen, the volume flyout, the
  keys) before it ships.
- AirPlay (macOS) comes through PyObjC's AVFoundation and AVKit in the same
  way, and `--self-check` fails a Mac bundle without them. A change in
  `services/airplay_macos.py` is tried with a real Apple TV: the AirPlay
  window and Apple's menu in it, a channel and a film, pause, seek, a zap
  while it casts (the Apple TV stays picked, the window stays), the
  firewall's question (what only a real Mac shows: DEVELOPER_NOTES.md,
  AirPlay).

## Cutting a release

1. On `testing`, set the new version in **all five places** -
   `tests/test_version.py` fails the build if they drift:
   - `dopeiptv/__init__.py` (`__version__`)
   - `pyproject.toml` (`version`)
   - `packaging/io.github.slimture.dopeIPTV.metainfo.xml` - a new
     `<release version="x.y.z" date="YYYY-MM-DD">` at the top (software
     catalogs read the version there)
   - `CHANGELOG.md` - a `## [x.y.z]` section (developer detail)
   - `RELEASE_NOTES.md` - the new version's notes under `## dopeIPTV x.y.z`,
     replacing the last version's (for users; it becomes the GitHub release
     text and the website's "What's new"). One version only: every version
     before it is in `CHANGELOG.md`, and on its own release page. Each
     paragraph and list item on one line: GitHub shows a release's every
     line break (`tests/test_tidy_release_notes.py` checks it)
2. Push `testing`; wait for CI to be green (and ideally try the test build).
3. Run **`tools/release.sh x.y.z`**. It never trusts a local branch: it
   fetches, merges `origin/testing` into `main`, checks that all five
   places agree, pushes `main`, and tags `vx.y.z`. Safe to re-run.
4. Watch `release.yml`. Re-run a failed leg if it was a download hiccup.
   Its last job signs the checksum list and tidies the release notes
   (`tools/tidy_release_notes.py`): the new release keeps only its own
   section of `RELEASE_NOTES.md`, the one before it is folded. Every
   nightly run does the same, so the releases page stays short and the
   downloads near the top; nothing is lost - each tag's
   `RELEASE_NOTES.md` and `CHANGELOG.md` have it all.
5. The website picks up the new release by itself within 15 minutes.

Write release notes for people who use the app: what they can now do and
where to find it, in plain words. The changelog can be technical.

## Keys, accounts and secrets

Only names here - the values are with the owner.

| What | Where it is used | Without it |
|---|---|---|
| GitHub Actions secret `DOPEIPTV_TMDB_KEY` | written into `dopeiptv/_tmdb_key.py` (git-ignored) by every release/nightly build | builds have no bundled TMDB key: posters need the user's own key, Home's popular shelves stay empty |
| GitHub Actions secret `DOPEIPTV_OPENSUBTITLES_KEY` | bundled the same way for subtitle search | users must enter their own free OpenSubtitles key |
| GitHub Actions secret `SIMKL_CLIENT_ID` | the client id of dopeIPTV's app at simkl.com/settings/developer (no secret is used: the PIN sign-in needs none), written into `dopeiptv/_simkl_key.py` (git-ignored) by every release build ("Bake built-in Simkl client id") | Simkl is not offered in Settings |
| Discord application | its application id is `DISCORD_APP_ID` in `dopeiptv/core/discord_rpc.py` (public, not a secret: discord.com/developers → Applications, named "dopeIPTV", since Discord shows "Watching <its name>"); a Rich Presence art asset named `dopeiptv` gives the status its picture | the Discord switch is not offered |
| Trakt app registration | its client id is `DEFAULT_CLIENT_ID` in `providers/trakt.py`; the redirect goes through the website (see its README) | users can enter their own client id in Settings → Trakt |
| The web server | runs the site and its cron sync (website branch: `README.md`, `DEPLOY.md`) | the site stops updating; the app is unaffected |
| GitHub repository settings | releases, Actions, the Security tab for private reports | - |
| Stripe Payment Link (donations) | `$DONATE` at the top of `site/render.jq` on the `website` branch - where the site's donate links point without JavaScript. `.github/FUNDING.yml` (the Sponsor button, shown with Settings → Features → Sponsorships ticked) and the README's "Support dopeIPTV" point at `dopeiptv.org/#donate`, which opens the dialog | no donate card on the site |
| Stripe keys for the donate dialog | a restricted key (PaymentIntents: Write) and the publishable key, on the web server in `/usr/local/etc/iptv-donate.key` / `.conf`, read by `donate/donate.py` (website branch, `DEPLOY.md` "Donations"); Apple Pay needs the domain added in Stripe | the dialog sends visitors to the Payment Link's own page instead |
| The admin page's password | `/usr/local/etc/nginx/iptv-admin.htpasswd` on the web server (website branch, `DEPLOY.md` "Statistics and the admin page") | `/admin/` cannot be opened; the site is unaffected |
| Apple Developer Program (Solecraft AB, team `6QR76TBT4J`) | the macOS builds' signature and notarisation - see "macOS signing" below | the .dmg is signed ad hoc and users allow the first start under Privacy & Security |
| GitHub Actions secrets `MACOS_CERT_P12`, `MACOS_CERT_PASSWORD` | the Developer ID Application certificate with its private key (base64 of the exported .p12) and the export password | as above |
| GitHub Actions secrets `APPLE_API_KEY_P8`, `APPLE_API_KEY_ID`, `APPLE_API_ISSUER_ID` | an App Store Connect API team key (Developer access) for `notarytool` | a release fails at notarising - the certificate alone signs but is not enough |
| GitHub Actions secret `DOPEIPTV_UPDATE_KEY` | dopeIPTV's release signing key (Ed25519, base64): release.yml's `checksums` job and nightly.yml sign each release's `SHA256SUMS` with it (`tools/sign_sums.py`); its public half is in `dopeiptv/core/update_keys.py`. Made once with `tools/make_update_key.py`; the only backup is the owner's password manager - see "The release key" below | a release fails at signing; a test build goes out unsigned and the app will not install it |

A source checkout works without any of these. For local testing, set
`DOPEIPTV_TMDB_KEY` / `DOPEIPTV_OPENSUBTITLES_KEY` / `DOPEIPTV_SIMKL_CLIENT_ID` in
the environment.

### The release key

From 1.4.5 the app installs an update - on Linux, Windows and macOS alike -
only when the file is on the release's `SHA256SUMS`, signed with
dopeIPTV's own key (`dopeiptv/core/selfupdate.py`, `signed_asset`). The
site sends the list and its signature with the release (`releases.json`,
`nightly.json`; `sync-releases.sh` moves the site to a release only once it
is signed); otherwise they come from GitHub. A site or mirror taken over,
or a checksum changed beside the file, gets nothing installed.

- The private key is made once on the owner's own computer
  (`python3 tools/make_update_key.py`) and kept in two places only: the
  Actions secret `DOPEIPTV_UPDATE_KEY` and the owner's password manager. It
  is never sent to anyone, never committed, never on the web server.
- Lose it, and the copies out there accept no update from inside the app
  until a new version is installed by hand. To change keys: add the new
  public half to `UPDATE_KEYS` beside the old one, release, and only once
  that release is widespread sign with the new key and drop the old one.
- `tools/sign_sums.py` refuses a secret whose public half the app does not
  carry, so a wrong secret stops the build instead of shipping a release
  no app will install.

### macOS signing

The `macos-dmg` job in `release.yml` signs every piece of code in the
.app with the Developer ID (hardened runtime, the entitlements in
`packaging/macos-entitlements.plist`), signs the .dmg, sends it to Apple
with `notarytool` and staples the ticket to it; the smoke test then asks
Gatekeeper whether it takes the app as notarised. Nightlies are signed the
same way but not sent to Apple - one per push filled Apple's queue while a
new account's first review took days - so a nightly opens after Open
Anyway, and only releases (a tag, or *Release* run by hand) are notarised.
Without the secrets (a fork) the job builds the ad hoc signed
.dmg it always did; with the certificate but no API key a release fails
rather than ship something half done.

- **The certificate** is valid five years (from November 2026). Before it
  runs out, make a new *Developer ID Application* certificate at
  developer.apple.com from a certificate request made in Keychain Access,
  export it with its key as .p12 (File → Export Items), and replace
  `MACOS_CERT_P12` (`base64 -i file.p12 | pbcopy`) and
  `MACOS_CERT_PASSWORD`. Apps already signed keep opening: the signature
  carries a timestamp.
- **The API key** does not expire; revoke it in App Store Connect → Users
  and Access → Integrations if it leaks and make a new one (Developer
  access), then replace the three `APPLE_API_*` secrets. The .p8 file can
  be downloaded only once.
- **If Apple refuses a build**, the job prints `notarytool log`: it names
  each file and what is missing (usually a binary that was not signed, or
  signed without the hardened runtime).
- **A published release's .dmg files can be rebuilt** after a change to
  the build alone (1.4.4 got its signed ones this way): run *Release* by
  hand on `testing` (Actions → Release → Run workflow) with
  `replace_macos_dmg` set to the tag. Only the macOS job runs; it refuses
  when `dopeiptv/` or `requirements.txt` differ from the tag, and replaces
  the two .dmg files on that release and nothing else.

## The website

The `website` branch has its own `README.md` (architecture, local
preview, SEO) and `DEPLOY.md` (the server). In short:

- Static pages rendered by `sync-releases.sh` + `site/render.jq` from the
  latest GitHub release, every 15 minutes by cron on the server. It also
  mirrors the release files and counts downloads from nginx's log of
  `/dl.php`.
- Texts: `site/lang/<code>.json` (27 languages; English is the fallback),
  the help page's topics in `site/help.json`, templates in
  `site/templates/`. A new string needs its key in every language file.
- The help shows six settings pages as the app draws them, in each of the
  27 languages: `public/help-shots/<lang>/<id>.webp`, listed in
  `site/i18n.json` (`help_shots`). When the settings change, take them
  again with `QT_QPA_PLATFORM=offscreen python tools/help_screenshots.py
  OUT` (this repository) and copy `OUT/` over `public/help-shots/`.
- The front page's four pictures of the app are
  `public/screenshots/<lang>/<name>.webp`, taken by
  `tools/site_screenshots.py` (this repository) with a made-up playlist and
  public-domain stills from `tools/showcase/`; it needs OpenGL and ffmpeg:
  `xvfb-run -a -s "-screen 0 2560x1600x24" python tools/site_screenshots.py
  OUT`, then copy `OUT/` over `public/screenshots/`. Take them again when
  the main window changes.
- **The server's jq is 1.8.** In `render.jq`, every `EXPR as $x | ...`
  must start right after `|`, `(`, `[`, `;`, `then` or `else` - jq 1.8
  binds `a and b as $x` differently than 1.7.
- Strict Content-Security-Policy: no inline scripts or styles, nothing
  from other origins but Stripe's script and frames for the donate dialog
  (`deploy/csp.snippet.txt`). The download section and the FAQ work
  without JavaScript.
- The one non-static piece is `donate/donate.py`, a standard-library
  service behind `/donate/` that creates Stripe payment sessions for the
  donate dialog (card form, Apple Pay, Google Pay on the page itself), in
  the donor's own currency. If it is down, the dialog falls back to
  Stripe's own page. It also serves `/admin/` behind nginx's password:
  visitors, downloads and donations by day, week, month and year, counted
  from nginx's own log of page views by `donate/stats_collect.py` (cron) -
  no script or cookie on the site, no address kept.
- The look is deliberately quiet: black or white, greys and the app
  icon's blue (`#4C8DFF`); San Francisco on Apple devices, self-hosted
  Inter elsewhere. On a phone the downloads fold away behind "Send the
  link", since the app is for computers.
- **A link to the site from anywhere we control carries `?ref=`**: the
  app (`app-about`, `app-update`), the README (`github`), the Sponsor
  button (`github-sponsor`), the issue form (`github-issues`), release
  notes (`release`), the Windows readme (`windows-readme`). A link opened
  from a desktop app or GitHub's sidebar carries no referrer, so without it
  those visits count as "direct". The statistics file them under the app
  or GitHub; the page drops the parameter from the address once counted.
  A new link gets its own short `ref`.
- Render every language locally before pushing (the README's "Local
  preview"), then look at the pages in a browser. Deploying is a manual
  step on the server (copy `public/` and `site/`, run the sync once).

## A copy outside GitHub

GitHub is where the project lives. A read-only copy is kept on the
owner's own Forgejo/Gitea at `git.bastion.nu` as a **pull mirror**: that
server fetches every branch and tag from GitHub on a schedule, so nothing
on the GitHub side has to know about it and no key is stored anywhere.
(Set up there with *New migration → GitHub → "This repository will be a
mirror"*, interval e.g. 1 hour. Release files are not part of git, so
they stay on GitHub and the website.)

If GitHub ever became unusable: turn the mirror into an ordinary
repository in its settings, point clones at it
(`git remote set-url origin <new url>`), and rebuild the release
pipeline there - `release.yml` is GitHub Actions, and Forgejo Actions
reads much the same syntax. The website's sync would then need the new
release source in `sync-releases.sh`.

## Translations

The app and the site are both in 27 languages. English is the source; the
other languages started as machine translation and native-speaker fixes
are welcome (`docs/TRANSLATING.md`, `python3 tools/i18n_status.py`). The
tests check that placeholders line up and that every `tr("…")` key exists.

## Open work and known limits

- **Stalker / Ministra portals are experimental**: no catch-up from a
  portal yet (it needs a portal with archive to test against).
- **dopeIPTV's own rewind buffer is experimental** (Settings → Playback).
  When it has had a release or two without trouble, drop the label.
- **Flatpak** is built but not on Flathub (`packaging/flatpak/FLATHUB.md`
  has what a submission needs); its build leg is experimental.
- **AirPlay** is macOS only (AVFoundation and AVKit; tvOS broke the API
  other senders use). It plays on a real Apple TV through the bridge
  (checked from a checkout, November 2026); the packaged app's direct way
  (the provider's http HLS, allowed by NSAllowsArbitraryLoadsForMedia) is
  still to be seen on a real Mac - `docs/DEVELOPER_NOTES.md` says how it
  works.
- Windows code signing (see above).
- On the website, a few Serbian strings are in Cyrillic while the rest of
  the Serbian page is Latin.

### Planned: Enigma2 for those with boxes at home

After the account-connection count (the Nerd network tools) is in, so it
builds on that count instead of a second one:

- **Transponder check.** Channels on the same transponder share the
  TSID/ONID/namespace of their service reference. With the box's tuner
  count (`/web/about`), what it records (`/web/timerlist` - recordings
  only) and what the box itself shows on the TV right now
  (`/web/getcurrent`, the service being watched), warn before a zap or a
  stream that would cut off a recording or someone watching, and mark the
  channels that can be seen without one.
- **Signal values.** Built: SNR, AGC and BER (`/web/signal`) in the stream
  stats (`I`, asked every 2 s only while they show) and the box's info
  window. OpenWebif reports the tuner behind what the box itself shows,
  not necessarily the one a stream comes from: said under the values.
- **Transcoding via port 8002.** Built, in the source's own (Nerd) options:
  off / Low / Medium / High / Full HD, brought to the bitrate limits and
  sizes `/transcoding` reports (read, never set); the player uses it, cast
  and multiview only with "Transcoded when cast or in multiview too";
  recordings over their own connection keep the box's own stream. The
  query follows OpenWebif's own (`&` on OpenPLi, SatDreamGR, Open Vision;
  `?` between options elsewhere).
- **Encrypted channels said plainly.** Built: `core/ts_scramble.py` reads
  the first packets (box streams only) - in the failure diagnosis, a
  frozen channel's last reconnect, a multiview cell, a box recording (its
  file, else its stream), and stream health; failover then tries another
  source.

Then the rest of what an Enigma2 box can do from a computer:

- **Bouquet editor** (as Dreamboxedit does): bouquets and their channels
  reordered, renamed, added and removed, markers, written back to the box
  and the box told to reload them.
- **AutoTimer**: the box's own rules read, made and changed (OpenWebif's
  AutoTimer plugin), beside dopeIPTV's series rules - and said plainly
  which of the two records what.
- **The box's recordings managed**: built - rename, move to another folder,
  delete (with a confirm) from the recording's menu (`/web/movierename`,
  `/web/moviemove`, `/web/moviedelete`, folders from `/web/getlocations`).
- **A screenshot from the box**: built - OpenWebif serves it at `/grab`
  (not `/web/grab`): picture, OSD or both, with Save.
- **The box's remote control in the app** (`/web/remotecontrol`): built -
  numbers, arrows, OK, exit, menu, info, EPG, colour keys, volume, mute,
  channel up/down, TV, radio, text, audio, power (OpenWebif's key codes).

Checked against OpenWebif's source; the Dreambox WebInterface's answers
for movierename/moviemove, `/grab` and `/web/signal` are not verified, and
transcoding (the 8002 query and its units) needs a real box with an
encoder.

### Planned: server mode, after the Enigma2 work

dopeIPTV as the home's server as well as its player - what Threadfin,
xTeVe and Dispatcharr are for, in the same program. Four parts, built
together:

- **Without a window.** A headless start (`--server`) for a NAS, a
  Raspberry Pi, Docker or a systemd service: starts with the computer,
  runs series rules, reminders' recordings and the guide's refresh day
  and night. Linux first (AppImage, .deb, a container image); the same
  code on macOS and Windows.
- **Streams through dopeIPTV.** Today sharing sends a player on to the
  source's own address. Restreaming instead fetches it here, so a
  source's user agent, headers, own DNS, trusted certificate, proxy,
  rewrite rules, backup addresses and failover apply to every device on
  the network, and viewers of the same channel share one connection to
  the account (counted in `core/connections.py`). Redirect stays as an
  option.
- **An HDHomeRun tuner** (`discover.json`, `lineup.json`, `lineup_status.json`,
  SSDP/UDP discovery), so Plex, Jellyfin, Emby and Channels DVR find
  dopeIPTV by themselves; as many tuners as the account's connections.
- **Settings in a browser**: sources, status (connections, tuners,
  recordings, stream health), recordings and series rules - behind a
  token like the remote API, this computer only unless chosen.

### Ideas to talk through before anything is built

Not decided: each needs a word with the maintainer first.

**Reception and the stream itself**

- **SAT>IP client**: minisatip, Digital Devices, Telestar, and Enigma2 or
  Tvheadend serving SAT>IP. RTSP with the tuning in the address, the
  server's channel list where it has one (an M3U), else a scan.
- **Teletext and DVB subtitles** through ffmpeg/libzvbi: teletext pages
  (page 199 and the like in the Nordics) as subtitles, and a teletext
  viewer. Recordings keep them only as .ts (MP4 cannot carry them).
- **TS analysis in Nerd**: PIDs and the PMT, bitrate per PID, continuity
  errors, PCR jitter - a simple TR 101 290 for the stream that plays.
- **Recordings compared between sources**: the same programme on two
  sources side by side (picture size, bitrate, drops), and the better one
  chosen - built on failover's matching.

**More home backends** (Enigma2, Tvheadend, HDHomeRun, Jellyfin, Emby and
Plex are in)

- **VDR** through streamdev or the restfulapi plugin.
- **DVBViewer Media Server** (big on Windows, above all in Germany).
- **NextPVR** and **MythTV**.

**Other kinds of streams** (yt-dlp's sites and radio-browser.info are in:
`core/ytdlp.py`, `ui/mw_webstreams.py`, `providers/radio_browser.py`,
`ui/mw_radio.py`)

- **yt-dlp's subtitles and chapters** where the app resolves the address
  itself (a libmpv without the ytdl hook - the Flatpak's): today one
  stream with picture and sound, no separate audio, no subtitles.

**On the TV, from the sofa**

- **A 10-foot mode**: a large interface run entirely with a remote. The
  app has nothing for HDMI-CEC, a gamepad or LIRC today; a CEC remote works
  only where the system turns it into keys. TV mode on the list from
  "build everything" is the same thing.
- **dopeIPTV as an HDHomeRun tuner**: part of server mode (above).
- **Android TV and Fire TV**: PyQt6 does not get there in practice; an
  app of its own would be a separate project. Until then the shared
  playlist and guide, the remote API and the HDHomeRun emulation let apps
  on the TV use dopeIPTV as their source.

### For the help, when this batch is done

Written once, when everything in the batch is in (GitHub's CHANGELOG,
FEATURES and README; the website's help in every language):

- **An Apple TV from Linux or Windows.** AirPlay is macOS only (Apple's
  frameworks; tvOS 18 shut out other senders' video). Elsewhere: cast to a
  Chromecast, a DLNA TV or Kodi - or let an IPTV app on the Apple TV (VLC,
  iPlayTV and the like) read dopeIPTV's shared playlist and guide, and
  Plex or Jellyfin find it as an HDHomeRun tuner once that is in.

### Every feature works with the others

A rule for everything new, and the reason for a review once a batch is in:

- **One way in.** Whatever starts a stream - a zap in the app, the phone
  remote, Home Assistant commands, Watch together, multiview, the rewind
  buffer, recordings, casting (Chromecast, DLNA, Kodi, AirPlay), failover -
  goes through the same path, so its checks (VPN guard, connection limit,
  transponder check) apply everywhere without being rebuilt per feature.
- **One count of connections and tuners**: an Xtream account's
  connections and a box's tuners and transponders in the same place,
  counting the player, each multiview cell, the rewind buffer, recordings
  and casts.
- **One way to say a problem**: an encrypted channel, a dead address or a
  limit reached reads the same in the player, recordings, multiview,
  stream health and casting.
- **A combined review after each batch**, with tests for the pairs that
  matter (box + recording + multiview, phone remote + transponder check,
  Watch together + failover, casting + the rewind buffer) - kept together
  in `tests/test_feature_pairs.py`.

## How changes are written

- Commits and comments in English; the message says what changed for the
  user and why, not only what the diff does.
- Small, focused commits on `testing`; a test with every fix that can be
  tested headless.
- A pure move of code between modules or mixins must not change
  behaviour - and tests that patch module globals must follow the code to
  its new module.
- Before deleting or renaming a committed file, search for it including
  hidden directories (`rg --hidden`, or `grep -r … . .github`): workflows
  reference files a plain search skips. `tests/test_packaging_icons.py`
  catches missing workflow paths.

## If you are an AI assistant

Read `AGENTS.md` - it is written for any assistant, not one in particular.
Run the quality gate yourself before saying something works, do not push
anything but `testing` (and `website` for the site) without the owner's
go-ahead, and never tag, publish a release, change repository settings or
post on GitHub on your own.
