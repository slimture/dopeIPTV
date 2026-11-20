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
- No telemetry and no accounts. The app talks to the sources the user adds
  plus TMDB, dopeiptv.org (the update check; and, only when used, feedback,
  settings sync and Watch together - small services of the website's own),
  GitHub as the update fallback, and - when turned on - Trakt, Simkl,
  OpenSubtitles and the rest `SECURITY.md` lists, with the servers the app
  runs itself on the home network.

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
| `docs/` | Architecture, developer notes, features, translating, the threat model (`SECURITY.md`) and the security reviews, this file |

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
  Linux, and - a job of its own - the audit of the Python packages the
  builds ship ("What the build downloads" below). (The packaging checks -
  versions, icons, paths workflows name - are tests in the suite.)
- **`nightly.yml`** - every push to `testing`: builds every package the way
  a release does (it calls `release.yml` with `nightly_version`) and
  publishes them as a **pre-release** `nightly-<version>.<run>`. Users on
  Settings → General → Update channel → Test builds get these. Only the
  newest few are kept; pre-releases never count as "latest", so the
  website and the Releases channel never see them.
- **`release.yml`** - on a `v*` tag: builds and attaches every package -
  sdist + wheel, AppImage + .deb (x86_64 and aarch64), macOS .dmg (arm64
  and x86_64), the Windows installer and .zip (x64 and arm64), Flatpak -
  plus `SHA256SUMS`.
  Each job uploads its own files, so one failing format never blocks the
  others. The **experimental leg** may fail without failing the run: the
  Intel Mac. The Flatpak is not one any more: it is built, installed and
  started in its own runtime (`--self-check`, then 15 s on a virtual
  screen), and a release whose Flatpak fails is red (#36 shipped a Flatpak
  that crashed at every start while the leg was allowed to fail). A flaky
  download (GitHub, conda-forge, micromamba) is the usual reason a leg goes
  red - re-run the failed jobs before looking for a bug.

Every workflow is read-only unless a job says otherwise (`permissions:`),
names each action by its commit (the version in a comment beside it), and
leaves no token in its clone (`persist-credentials: false`). The update key
is handed to one step in each of `release.yml` (the `checksums` job) and
`nightly.yml` (`publish`): jobs that build and install nothing, where the
step runs `python3 -I -S tools/sign_sums.py` and nothing else. After a
release or test build is published, an `attest` job records where its files
were made (GitHub's artifact attestations: `gh attestation verify <file>
--repo slimture/dopeIPTV`); it may fail without failing the run.

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
   Then the security review (AGENTS.md, rule 13): on the `website` branch,
   `python3 tools/review_kit.py --app <checkout of testing> --ref testing
   --out review-kit` makes every question of the transparency page with
   its code, and the questions about the rest of the app, in one file
   (`review-kit/ALL.txt`). Give it to at least one independent AI
   assistant, fix or answer every finding, and add the round - who asked,
   what came back, what was done - to `docs/SECURITY_REVIEWS.md`.
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
| GitHub Actions secret `DOPEIPTV_TMDB_KEY` | written into `dopeiptv/_tmdb_key.py` (git-ignored) by every release/nightly build | builds have no bundled TMDB key: posters and Popular right now on Movies and Series need the user's own key |
| GitHub Actions secret `DOPEIPTV_OPENSUBTITLES_KEY` | bundled the same way for subtitle search | users must enter their own free OpenSubtitles key |
| GitHub Actions secret `SIMKL_CLIENT_ID` | the client id of dopeIPTV's app at simkl.com/settings/developer (no secret is used: the PIN sign-in needs none), written into `dopeiptv/_simkl_key.py` (git-ignored) by every release build ("Bake built-in Simkl client id"); a client id public in the code, `PUBLIC_CLIENT_ID` (`providers/simkl.py`), wins over the file | nothing while `PUBLIC_CLIENT_ID` is set; with it empty too, Simkl is not offered in Settings |
| Discord application | its application id is `DISCORD_APP_ID` in `dopeiptv/core/discord_rpc.py` (public, not a secret: discord.com/developers → Applications, named "dopeIPTV", since Discord shows "Watching <its name>"); a Rich Presence art asset named `dopeiptv` gives the status its picture | the Discord switch is not offered |
| Trakt app registration | its client id is `DEFAULT_CLIENT_ID` in `providers/trakt.py`; the redirect goes through the website (see its README) | Trakt sign-in fails: there is no Settings row for a client id of one's own - only a build with `DEFAULT_CLIENT_ID` empty asks for one on Connect (`_trakt_creds_dialog` in `ui/mw_trakt.py`) |
| The web server | runs the site and its cron sync (website branch: `README.md`; how the server is set up: the private repository `slimture/dopeiptv-server`) | the site stops updating; the app is unaffected |
| GitHub repository settings | releases, Actions, the Security tab for private reports | - |
| Stripe (dopeIPTV's own account, under Solecraft AB) - the tip dialog and a Payment Link | a tip (any amount, once) in the site's tip dialog, paid through `donate/donate.py` (or a Payment Link of its own, `SUPPORT_TIP_URL`, while `SUPPORT_TIPS` is not on), and monthly support (a fixed price) as a Payment Link returning to `https://dopeiptv.org/support/?paid={CHECKOUT_SESSION_ID}`; the settings are the web server's, in `/usr/local/etc/iptv-site.conf` (`SUPPORT_TIPS`, `SUPPORT_MONTHLY_URL`, `SUPPORT_SPONSORS_URL`, ...), read by `sync-releases.sh` (website branch, `DEPLOY.md` "Support"). `.github/FUNDING.yml` (the Sponsor button, shown with Settings → Features → Sponsorships ticked), the README's "Support dopeIPTV" and the app's heart point at `dopeiptv.org/support/` | the site invites to no payment (the support page says "coming soon"); the app's heart still opens the support page |
| Stripe key for the supporter key | a restricted key, read-only (Checkout Sessions: Read, PaymentIntents: Read), on the web server in `/usr/local/etc/iptv-donate.key`, read by `donate/donate.py` | the thank-you after paying shows no supporter key |
| The admin page's sign-in | each admin's passkey, or a name and password (and an authenticator code if turned on); the admins in `/usr/local/www/iptv/stats/admin-users.json` on the web server, a new one by an invite from another (website branch, `DEPLOY.md` "Statistics and the admin page") | `/admin/` cannot be opened; the site is unaffected |
| Apple Developer Program (Solecraft AB, team `6QR76TBT4J`) | the macOS builds' signature and notarisation - see "macOS signing" below | the .dmg is signed ad hoc and users allow the first start under Privacy & Security |
| GitHub Actions secrets `MACOS_CERT_P12`, `MACOS_CERT_PASSWORD` | the Developer ID Application certificate with its private key (base64 of the exported .p12) and the export password | as above |
| GitHub Actions secrets `APPLE_API_KEY_P8`, `APPLE_API_KEY_ID`, `APPLE_API_ISSUER_ID` | an App Store Connect API team key (Developer access) for `notarytool` | a release fails at notarising - the certificate alone signs but is not enough |
| GitHub Actions secret `DOPEIPTV_UPDATE_KEY` | dopeIPTV's release signing key (Ed25519, base64): release.yml's `checksums` job and nightly.yml sign each release's `SHA256SUMS` with it (`tools/sign_sums.py`) - only there, never in a build job; its public half is in `dopeiptv/core/update_keys.py` (`UPDATE_KEYS`). Made once with `tools/make_update_key.py`; the only backup is the owner's password manager - see "The release key" below | a release fails at signing; a test build goes out unsigned and the app will not install it |
| GitHub Actions secret `DOPEIPTV_BUILD_KEY` | the build manifest's own key (Ed25519, base64): every build job signs its list of the app's files with it (`tools/build_manifest.py`, "Official builds" below); its public half is in `BUILD_KEYS` in `dopeiptv/core/update_keys.py`. It signs nothing else - an update needs the update key | every build calls itself modified (its online services off), and a tag's build stops (`--require-key`) |

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
- **Who lets a signature happen (since 1.6.9).** release.yml's `checksums`
  job runs in the environment `release-signing`, nightly.yml's `publish` in
  `nightly-signing`. Set them up once, in the repository's Settings >
  Environments: `release-signing` with yourself as a required reviewer and
  deployment branches and tags limited to `main` and `v*`; `nightly-signing`
  limited to `testing`. Put `DOPEIPTV_UPDATE_KEY` in each environment's own
  secrets and delete the repository-wide one. Then no workflow on another
  branch can read the key, and no release is signed until you approve the
  job in the Actions tab - a changed workflow or a stolen token alone signs
  nothing. Until then the jobs run as before (an environment that does not
  exist is made without rules, and the repository-wide secret still
  reaches it).
- **The build jobs' own key (required since 1.6.9).** Every build job
  signs its build manifest ("Official builds" below) after it has run
  PyInstaller, pip's packages and other people's tools, so those jobs never
  get the update key: they get `DOPEIPTV_BUILD_KEY`, which signs file lists
  and nothing else. Set it up once, in this order:
  1. `python3 tools/make_update_key.py` on your own computer, as for the
     update key; keep the private half in the password manager.
  2. Put its PUBLIC half in `BUILD_KEYS` in `dopeiptv/core/update_keys.py`,
     commit and push `testing` first.
  3. Then add the private half as the Actions secret `DOPEIPTV_BUILD_KEY`.
  Until both are done every nightly calls itself modified and a tag's build
  stops at the manifest step. In the other order (secret first) every build
  stops with "the secret's public half is not one the app carries". If the
  build key leaks, a modified build could call itself official - it could
  not sign an update; replace it the same way and drop the old public half.
- **If the update key leaks:** delete the secret, make a new key, add its
  public half beside the old one and release at once - signed with the OLD
  key, so the copies out there take it - then sign with the new key only and
  drop the old public half one release later. There is no way to make a copy
  already installed distrust the old key before it updates: until then it is
  protected by where updates come from (dopeiptv.org and GitHub over https),
  not by the signature. A second, offline key in `UPDATE_KEYS` from now on
  (never in GitHub, only in the password manager) would let you sign that
  release even after deleting the leaked one's secret.

### What the build downloads

Nothing a build runs or ships is "whatever is newest that day":

- **Binaries fetched by address** - the libmpv build for Windows
  (zhongfly/mpv-winbuild, with FFmpeg linked in), the AppImage runtime and
  appimagetool, micromamba for the Intel Mac build - are listed in
  `packaging/build-inputs.json` with their SHA-256, and fetched only through
  `tools/build_inputs.py fetch <name> <file>`, which stops on any other
  bytes. An input may list several sources, tried in order: upstream's own
  file first, then a release of ours that shipped the same bytes (upstream
  keeps a libmpv build 30 days and replaces the runtime's "continuous" file
  now and then; our releases stay). The same file names the commit each
  source tree cloned by tag must be (`verify-git`: the Vulkan loader, mpv
  for the Intel Mac build).
  *To take a newer libmpv:* pick a release of zhongfly/mpv-winbuild
  (`tools/pick_mpv_asset.py` names the baseline archive), put its two
  archives' addresses and SHA-256 and the DLLs' SHA-256 in the two
  `libmpv-windows-*` entries as their first source, and remove the second
  source until a release of ours has shipped that DLL - then add that
  release's Windows zips as the second source (within 30 days, or the
  Windows build stops once upstream removes the archive). *A newer runtime:*
  the same, with a release's AppImages as the second source.
- **Python packages** - `packaging/requirements-release.txt` names the exact
  version of everything pip installs for the AppImage, .deb, Windows and
  macOS builds and the SHA-256 of every file pip may take
  (`requirements-pip.txt` the same for pip itself, installed first;
  `requirements-audit.txt` for the audit job). They are made from the `.in`
  files beside them with the `uv pip compile` command in each one's first
  lines - never edited by hand. `release.yml` installs with
  `-c packaging/requirements-release.txt`: pip then refuses a file whose hash
  is not listed, and a package that is not in the lock at all. The Flatpak
  has its own pinned wheels in its manifest.
- **The audit** (`ci.yml`, job `audit`; `tools/audit_deps.py`) asks PyPI's
  advisory database about exactly those versions - every platform's, and the
  Flatpak's wheels. A known vulnerability fails the job: move the lock (or
  the manifest) to the fixed version, or accept it for a while in
  `packaging/audit-allowlist.txt` with a reason and a date after which it
  fails again. An audit that did not finish fails too (exit 2): the
  advisory service not reached after three tries a minute apart, or a
  package pip-audit left unchecked - run the job again once it answers.
  `release.yml` runs the same audit; its `checksums` job (the signing) runs
  only once it passed.
- **The yt-dlp the app may download** for web streams is named in the
  build, not at the user's: `release.yml`'s first job, `ytdlp-pin`, runs
  `tools/ytdlp_pin.py --or-none`, which takes the newest yt-dlp release,
  checks its `SHA2-256SUMS.sig` with `gpgv` against yt-dlp's own key
  (`packaging/yt-dlp-signing-key.asc`, fingerprint in
  `core/ytdlp_pin.py`'s `KEY`) and writes that release's tag and the
  SHA-256 of each file into `dopeiptv/core/ytdlp_pin.py`, which every build
  job downloads in. The app installs only that release, only those bytes,
  and runs only a copy it marked itself. A new yt-dlp reaches the users
  with the next release or nightly. If the check fails the build pins
  nothing (a warning in the job; the app then downloads no yt-dlp and uses
  the system's). Should yt-dlp change its key: check the new one by its
  own announcement, replace the `.asc` and `KEY` in one commit.
- **Still as the package manager gives them** (their own signatures or
  checksums, not pinned here): apt's packages on Linux (libmpv and FFmpeg in
  the AppImage and .deb are Ubuntu's, with Ubuntu's security updates at
  build time), Homebrew's mpv and ffmpeg (Apple Silicon), conda-forge's
  libraries (Intel Mac), NSIS from Chocolatey on the ARM runner, the Flatpak
  runtime and SDK from Flathub, and the container image of the Flatpak job.

### Official builds (the build manifest)

Every package a release or test build makes carries a list of the app's own
files - each one's SHA-256, and the version - signed with the same release
key (`dopeiptv/core/build_check.py`). The app reads it at start, on a
thread of its own, and is then one of three:

- **official** - a frozen build (AppImage, .deb, Windows, macOS) or the
  Flatpak whose list is signed by a key in `update_keys.py`, made for this
  version, with every file as listed and no code beside it. A nightly is
  signed too, so it is official.
- **modified** - a frozen build with no list, or one that does not check
  out. The report header names the files: `Build: modified
  (custom_skin.py, … 4 files)`. dopeiptv.org's services are off there and
  each place says so in one line: Watch together, settings sync, the update
  check and self-update, and feedback and error reports (their window offers
  to copy the text instead - reports go to whoever made the build). About
  says "Not an official build". Everything local works as ever.
- **source** - a checkout or a pip install with no list (a git checkout
  ignores a list left in it). Every service works; About says "Running from
  source" and the report header `Build: from source (git <sha>)`.

Every request to dopeiptv.org from those services carries the header
`X-dopeIPTV-Build: official|modified|source` (the site refuses
`modified`).

**How the list is made.** `release.yml` runs `tools/build_manifest.py`
in each build job right after PyInstaller (before the AppImage, .deb,
installer and zip are made, and on macOS before the app is signed, the list
in `Contents/Resources` linked from `Contents/Frameworks`). The tool asks the
bundle itself what it holds (`dopeiptv --build-files OUT`: its data files on
disk, and its modules as its PyInstaller archive stores them - a bundle has
no .py files), signs that with `DOPEIPTV_BUILD_KEY`, writes
`_build_manifest.json` and `.sig` beside the package inside the bundle, and
asks the bundle again: it must now say official, or the job fails. The
macOS job asks the signed app on the mounted .dmg once more. The Flatpak
installs the package with pip, so its job lists the tree's .py files and the
data pyproject.toml ships (`tools/build_manifest.py tree dopeiptv`) before
flatpak-builder; without a python3 in that container it carries no list and
says "from source". Without the key (a fork) nothing is signed and the
build is modified; a tag without the key fails at this step
(`--require-key`). Both files are git-ignored: never commit them.

To try it locally: build with PyInstaller, then
`DOPEIPTV_BUILD_KEY=<a test seed> python3 tools/build_manifest.py frozen
dist/dopeiptv/dopeiptv` - with a key of your own the tool refuses unless its
public half is in `BUILD_KEYS`, so put a test key there in a scratch
copy only (`tests/test_build_check.py` does the round trip with a test key).

**The honest limit.** This stops the builds that do not bother: a changed
or added file, a list copied from an official build, a bundle rebuilt from
a fork. A fork that takes the check out of the code, or sends "official"
itself, cannot be stopped by anything in the app - the code is open. And a
pip install of a fork is "source", like any checkout.

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
preview, SEO). How the servers are set up - the runbook, the nginx files,
the tests that check them - is kept out of the public repository, in the
private `slimture/dopeiptv-server`. In short:

- Static pages rendered by `sync-releases.sh` + `site/render.jq` from the
  latest GitHub release, every 15 minutes by cron on the server. It also
  mirrors the release files and counts downloads from nginx's log of
  `/dl.php`.
- Texts: `site/lang/<code>.json` (27 languages; English is the fallback),
  the help page's topics in `site/help.json`, templates in
  `site/templates/`. A new string needs its key in every language file.
- **One look everywhere** (its README, "One look everywhere"): the header,
  the menu, the footer, the pages and the dialogs share one set of colours,
  icons, controls and words, and the page you are on is marked one way; a
  change to one is carried to the others in the same commit.
- The help shows the settings page an answer is about as the app draws
  it, scrolled to the row it names, in each of the 27 languages:
  `public/help-shots/<lang>/<id>.webp`, listed in `site/i18n.json`
  (`help_shots`); which page and row for each answer is `SHOTS` in
  `tools/help_screenshots.py`. When the settings change, take them
  again with `QT_QPA_PLATFORM=offscreen python tools/help_screenshots.py
  OUT` (this repository) and copy `OUT/` over `public/help-shots/`.
- The front page's pictures of the app are
  `public/screenshots/<lang>/<name>.webp` - five hero pictures in five
  themes, Movies, and a picture per feature with `spots.json` saying where
  in it the feature is - taken by `tools/site_screenshots.py` (this
  repository) from the real window over a made-up catalogue, its art drawn
  by `tools/site_art.py` and public-domain stills from `tools/showcase/`;
  it needs OpenGL, ffmpeg and Noto's fonts:
  `xvfb-run -a -s "-screen 0 2560x1600x24" python tools/site_screenshots.py
  OUT --jobs 3` (about 50 minutes for every language), then copy `OUT/<lang>/`
  over `public/screenshots/<lang>/`. Take them again when the main window
  changes.
- **The server's jq is 1.8.** In `render.jq`, every `EXPR as $x | ...`
  must start right after `|`, `(`, `[`, `;`, `then` or `else` - jq 1.8
  binds `a and b as $x` differently than 1.7.
- Strict Content-Security-Policy: no inline scripts or styles, nothing
  from other origins but Stripe's script and frames for the tip dialog
  (`deploy/csp.snippet.txt`). The download section and the FAQ work
  without JavaScript.
- The one non-static piece is `donate/donate.py`, a standard-library
  service behind `/donate/` that makes the Stripe payments for the tip
  dialog (card form, Apple Pay, Google Pay on the page itself), in the
  payer's own currency, and hands out supporter keys. For the app it
  serves feedback and its answers (`/feedback/`), settings sync (`/sync/`)
  and Watch together (`/together/`). And `/admin/`, for its admins only
  (a passkey, or a password): visitors, downloads, and tips and monthly
  support by day, week, month and year, the visits counted from nginx's
  own log of page views by `donate/stats_collect.py` (cron) - no script or
  cookie on the site, no address kept.
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

- **Stalker / Ministra portals are experimental**: the portal's archive
  plays by programme (`archive_link`, `archive_table`,
  `ui/mw_portal_archive.py`), checked against a fake portal only - it needs
  a real portal with archive to confirm.
- **dopeIPTV's own rewind buffer is experimental** (Settings → Playback).
  When it has had a release or two without trouble, drop the label.
- **Flatpak** is built but not on Flathub (`packaging/flatpak/FLATHUB.md`
  has what a submission needs). Its CI container comes with the SDK
  installed; `/var/lib/flatpak` is a volume of its own so a newer SDK can
  be installed over it (in the image's own layer that fails with "Invalid
  cross-device link"). The build's own import line runs in the SDK, which
  carries more than the runtime - only the installed Flatpak's
  `--self-check` proves a library is there.
- **AirPlay** is macOS only (AVFoundation and AVKit; tvOS broke the API
  other senders use). It plays on a real Apple TV through the bridge
  (checked from a checkout, November 2026); the packaged app's direct way
  (the provider's http HLS, allowed by NSAllowsArbitraryLoadsForMedia) is
  still to be seen on a real Mac - `docs/DEVELOPER_NOTES.md` says how it
  works. Since 1.6.5 a live channel plays live on an Apple TV, with no
  going back (`_cast_can_go_back` in `ui/mw_cast.py`): each step back was
  a new stream the Apple TV started over, slow and out of step with its
  sound.
- Windows code signing (see above).
- **A home network split in VLANs** (approved, November 2026): searches
  sent to everyone at once - mDNS, SSDP, the HDHomeRun, Jellyfin and Plex
  broadcasts - stay on their own part unless the router passes them on, so
  Nerd › Network › Other networks (`core/home_net.py`) lists the other
  parts and "Find on my network" and the cast window ask each address
  there by unicast (`providers/lan_scan.py`); the cast window adds a
  Chromecast, a DLNA TV or a Kodi by its address
  (`providers/cast_devices.py`, kept in `cast_added_devices`, listed on
  Settings › Casting); the remote API and the phone remote let in every
  private range, never a public address. Tried against fake devices on the
  loopback only: a real network with two VLANs and a router between them
  is still to be seen - a Chromecast (8008/8009) and a TV answering
  unicast M-SEARCH there, a cast's converter or file share reached from
  the other part (it is served at this computer's address on its default
  route), and the phone remote from a phone on the guest network.
- On the website, a few Serbian strings are in Cyrillic while the rest of
  the Serbian page is Latin.
- **Profiles**: built - one per person in the household, as Netflix and
  Plex have them (`core/profiles.py`, `ui/mw_profiles.py`,
  `ui/profiles_ui.py`; Settings > System > Profiles). What is a profile's
  own is declared in `core/profiles.py` (favorites of every kind, history
  and resume points, Watch later, watched marks, followed series,
  reminders, statistics, Trakt and Simkl, what the places show, theme and
  accent, audio and subtitle languages, the kids level); everything else is
  the household's. The first start copies everything into the first
  profile (`me`) and leaves the old keys: **drop the old keys in the
  release after the next one** (a downgrade then no longer finds them -
  say so in the notes). A profile's PIN stays on the computer it was set
  on: settings sync carries profiles without it, and never takes one out.
  Not yet: a "recently searched" list of its own (no search keeps one
  today), Home Assistant's
  profile as a list in its discovery (a `profile` command and state field
  for now), and a cast going on through a switch (the TV keeps playing).

### Planned: Enigma2 for those with boxes at home

After the account-connection count (the Nerd network tools) is in, so it
builds on that count instead of a second one:

- **Transponder check.** Built, on the connection count
  (`core/tuners.py`, `ui/mw_connections.py`): channels on one transponder
  share the TSID/ONID/namespace of their service reference. With the
  box's tuner count (`/web/about`), what it records (`/web/timerlist`,
  running recordings only) and what it shows on the TV (`/web/getcurrent`),
  a zap, a multiview cell, a cast or a recording over its own connection
  that would cut off a recording or the TV is asked about first, as the
  connection limit is (phone remote and Home Assistant included); a ring
  in the channel list marks such channels, and Nerd > Diagnostics shows
  what is in use. The box is asked off the window's thread and kept a few
  seconds (every 20 s while it is the source); one that does not answer
  says nothing. Still to see on a real box: FBC tuners (each demodulator
  counts as a tuner), mixed tuner types (a DVB-T channel cannot use a
  DVB-S tuner - all are counted as one pool), and whether a box with no
  free tuner cuts the TV or refuses the stream.
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

- **Bouquet editor**: built, through OpenWebif's own bouquet editor
  (`providers/bouquets.py`, `ui/bouquet_editor.py`; "Edit the box's
  channel lists…" in the box's menu, beside its info, screenshot and
  remote; not for a kids profile). TV and radio bouquets made, renamed,
  moved and deleted; a bouquet's channels dragged into order, renamed,
  removed, markers and spacers, channels added from every channel the box
  knows, a satellite's or a provider's (searched). Each change goes to the
  box at once (the box writes and reloads the lists itself), one after
  another in the order made; a refused one stops the rest and the lists
  are read back. The editor's backup (a tar in the box's /tmp) is taken
  before the first change of a window, copied here to save, and "Undo"
  puts it back; the source reads its channels again once the window is
  gone. Checked against OpenWebif's `controllers/BQE.py`,
  `BouquetEditor.py`, `models/services.py` and the editor's page
  (`public/js/bqe.min.js`), against a fake box. Needs a real box: a backup
  and a restore on a large lamedb (time, and the upload's file opened
  without truncating), a move in a bouquet with hidden (flag 512) entries
  (the editor's positions leave them out), a renamed IPTV entry, names with
  non-ASCII letters, and whether the box's own channel list on the TV
  follows at once. An older OpenWebif and the Dreambox WebInterface have
  no `/bouqueteditor` and are told so; the WebInterface's own
  WebBouquetEditor plugin is not used.
- **AutoTimer**: built - the box's own rules read, made, changed, turned
  on and off and deleted beside dopeIPTV's series rules (Settings >
  Recording > Series recordings, each row "Recorded by the box" or
  "Recorded by dopeIPTV"), Run now and a rule's preview; "Record series…"
  on a box's programme offers the rule on the box. What the box records
  (its timers and a simulate) the app's rules leave to it and say so.
  Checked against the plugin's source (OpenPLi's and oe-alliance's) and
  OpenWebif's `controllers/AT.py`: there is no `change_status` - on/off is
  `/autotimer/change` (oe-alliance) or `/autotimer/edit`. Needs a real box:
  the edit's quoting of an IPTV entry and of non-ASCII words, a simulate's
  time on a full guide, and OpenPLi's simulate (no id: every rule).
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

### Server mode: built (November 2026), to be seen with real devices

dopeIPTV as the home's server as well as its player - what Threadfin,
xTeVe and Dispatcharr are for, in the same program
(`services/home_server.py`; Settings > Nerd > Remote and automation >
Server for the home, off by default; `dopeiptv --server` without a window,
`dopeiptv/headless.py`).

- **Without a window**: `dopeiptv --server` (`--port N`,
  `--no-discovery`) imports no widget, nothing that draws and no mpv - only
  QtCore for the settings, plain threads for the rest; the household's
  sources, settings and guides (never a profile's own) - each served
  source's guide downloaded again once it is six hours old, one try an
  hour (`services/guide_keeper.py`; in the window, the sources other than
  the active one, whose guide the window keeps) -, the usual log; Ctrl+C / SIGTERM stop it cleanly. The page in a browser shows
  the command to start it with the computer. Run it or the window's
  server, not both: two processes cannot count each other's connections.
- **Restream** (`core/restream.py`): `/stream/<source id>/<stream id>`
  and the tuner's `/auto/v<number>` fetch a channel here, once, into a
  rewind buffer session (`core/dvr_*`), and every viewer reads its store
  from a few seconds behind live; one more than 20 s behind is dropped,
  never slowing the others. The last viewer gone, the connection stays
  10 s, at once given to another channel that needs the tuner. In the
  window the pool is the player's own: the player and a device watching
  one channel share its one connection, whoever was first (the player's
  `live_hook`, `DvrPool.adopt` / `acquire`). An HLS channel is copied
  into TS by ffmpeg (one connection, ffmpeg's).
- **One connection**: the tuners are the account's limit when it says
  (else one, or what the person sets - its hint says what it costs); a
  request beyond them gets 503 (`X-HDHomeRun-Error: 805 All Tuners In
  Use`), never a connection more. The app's own player, recordings,
  multiview cells, casts and tests come first: before one opens, the
  server lets go of its channels that leave no room
  (`ui/mw_server.py` `_server_make_room`, `RecordingManager.before_spawn`,
  mw_connections' checks). An Enigma2 box is asked before a channel that
  would cut off its recording or its TV (`core/tuners.py`).
- **HDHomeRun tuner** (`core/hdhr.py`): `discover.json`, `lineup.json`,
  `lineup_status.json`, `device.xml`, `lineup.post`, and the answer to
  libhdhomerun's discover broadcast on UDP 65001 (device id with its
  checksum, made once per install, kept out of backups and sync). The
  guide at `/xmltv.xml` carries the lineup's numbers as its channel ids
  (`services/share.lineup_guide`).
- **Who and what**: home addresses only; a token on everything but the
  tuner's endpoints (Plex cannot send one - the settings say so), which
  with the UDP discovery answer only this computer's own networks and
  Nerd's other networks - not every private range (`home_net.LocalNetworks`:
  a VPN's other customers, carrier NAT and Tailscale's 100.64/10 get 403, and
  the log names each refused address once with the way in); the sources and
  categories chosen, never a hidden or locked one nor a channel hidden by
  hand, and none for adults unless `nerd_server_adult` is on; nothing a
  device watches enters history,
  statistics, Trakt or Simkl.

To be seen with real devices (tested against a fake provider, fake HLS
and the loopback only):

- **Plex**: that it finds the tuner by the UDP discovery and adds it
  (model HDTC-2US, no DeviceAuth), maps the XMLTV guide by the numbers,
  and how it behaves on a 503 and when it changes channel - whether it
  closes the old stream before asking for the new one at one tuner.
- **Jellyfin and Emby**: adding it as an HDHomeRun tuner (they read
  `discover.json` and `lineup.json`; `TunerCount` keeps them off the old
  TCP control protocol) and mapping the guide.
- **Channels DVR**, VLC and Kodi on the playlist, a TV's IPTV app.
- A Raspberry Pi or NAS without a desktop: the headless start with no
  libGL or X libraries installed, and systemd's stop.
- Not yet: running series rules and scheduled
  recordings without the window, SSDP announcing (only the HDHomeRun UDP
  discovery), and a Docker image.

### Ideas to talk through before anything is built

Not decided: each needs a word with the maintainer first.

**Reception and the stream itself**

- **SAT>IP client**: built - see "SAT>IP servers" below. Not built: a
  channel scan for a server that offers no list (today the person gives an
  M3U).
- **Teletext and DVB subtitles**: built. Both in the subtitle menu by
  language and page ("Swedish - teletext 199"; the pages from the PMT, read
  from the rewind buffer or a local .ts), mpv's `teletext-page` set before
  the track and changed while it plays, the page kept per channel
  (`teletext_pages`), the preferred subtitle language picking a teletext
  page (`core/teletext_subs.py`, `media/teletext_subs.py`). The viewer
  (Teletext…, T) decodes the rewind buffer in plain Python
  (`core/teletext.py`, `ui/teletext_view.py`); without the buffer it says
  so - never a second connection. Recordings keep them only as .ts:
  "Keep teletext and subtitles" leaves such a recording a .ts when MP4 is
  on. Teletext subtitles need libmpv's FFmpeg built with libzvbi
  (`media/players.teletext_decoder`; else shown unavailable, and a track mpv
  drops says so once): Ubuntu/Debian's has it (AppImage, .deb), Homebrew's
  (the Mac app) does not, Windows' (zhongfly/mpv-winbuild) and the
  Flatpak's (ffmpeg-full) are not checked. mpv before 0.38 has no "any
  subtitle page" and defaults to page 100: without the PMT a page is
  guessed from the language. Checked with generated streams only (pages,
  subpages, Fastext, national subsets; mpv 0.37 showing 199 and switching
  to 333 live); a real broadcast should confirm DVB subtitles, X/26
  accents and Greek/Arabic/Hebrew pages (not drawn by the viewer).
- **TS analysis in Nerd**: in - `core/ts_analysis.py` with its window
  (`ui/ts_analysis_dialog.py`, `ui/mw_ts_analysis.py`): "Stream analysis…"
  in the player's options menu and the playing channel's menu while Nerd
  is on, linked from Nerd > Diagnostics. PIDs and the PMT (types named),
  the SDT's service, bitrate per PID, continuity errors, PCR interval and
  jitter, priorities 1 and 2 of TR 101 290 by their names - read from the
  app's own rewind buffer only (no second connection; HLS and the buffer
  turned off are said plainly). Checked against streams made in the tests;
  still to be seen on real streams: the PCR jitter against arrival (an
  estimate - its 250 ms limit is a guess at what shows as stutter), an
  MPTS from a box, and the CPU cap on a slow machine at high bitrates.
- **Recordings compared between sources**: the same programme on two
  sources side by side (picture size, bitrate, drops), and the better one
  chosen - built on failover's matching.

**More home backends**: built - VDR, DVBViewer Media Server, NextPVR and
MythTV are source kinds beside Enigma2, Tvheadend, HDHomeRun, Jellyfin,
Emby and Plex, each on `BoxClient` (`providers/vdr.py`, `dvbviewer.py`,
`nextpvr.py`, `mythtv.py`): the Add source form with their ports, their own
options on the source's page, Find on my network on their ports, the
guide as XMLTV, recordings under Movies › Recordings on the box, and
"Record on the box" from the guide (restfulapi's timers, DVBViewer's
`timeradd`, NextPVR's `recording.save`, MythTV's `AddRecordSchedule`).
Each module's docstring says what was checked against the project's
documentation and what was not; every test runs against a fake server.
Still to be seen on real servers:

- **VDR**: restfulapi's `inode` (with or without `.rec`) and streamdev
  playing a recording by it; how streamdev's `channels.m3u` writes a group;
  a timer's answer; a big channel list's guide (one question per channel).
- **DVBViewer**: the stream port and paths (`upnp/channelstream/<nr>.ts`,
  `upnp/recordings/<id>.ts` on 7522 - they differ between DMS versions,
  hence the option), the favourites answer's shape, the times as the
  server's local time, a timer's `dor`/minutes on a real DMS.
- **NextPVR**: that a stream takes any `client=`, `recording.save` with
  only these fields, `setting.list`'s padding names, NextPVR 4's fields.
- **MythTV**: `ChannelGroupID` on versions before v31 (a group then lists
  every channel), v34's API authorisation, AddRecordSchedule matching the
  guide's programme on a real backend. Live TV is not offered: MythTV gives
  it only over the Myth protocol; its channels are listed for the guide and
  recording, and say why they do not play.

**SAT>IP servers**: built - a source kind (`providers/satip.py`) for
Digital Devices' Octopus NET, Telestar's Digibit, Kathrein's EXIP,
minisatip, a Fritz!Box Cable and Tvheadend's SAT>IP server. Found by SSDP
(multicast, and sent to Nerd's other networks and to a typed address), its
description's `X_SATIPCAP` as tuners per system, channels from the
server's `X_SATIPM3U`, a Fritz!Box's three lists or an M3U the person gives
(the source's page, Connection; a Nerd preset fills a Fritz!Box Cable in),
ids from the tuning, RTSP over TCP by default with UDP or HTTP per source
(`core/rtsp.py`: mpv's `rtsp-transport` for that file alone, ffmpeg's
`-rtsp_transport` for a recording), no guide but the source's own XMLTV
(said plainly). Each stream counts as one of the server's tuners of its
system (`tuner_verdict`, `core/tuners.py`'s `counted`), the person may give
fewer; before a multiview cell, a cast or a recording would find none free
- or be the second when the server says no number - the window asks
(`ui/mw_connections.py`). Every test runs against fakes on the loopback
(SSDP answer, device.xml, M3U, RTSP OPTIONS). Still to be seen on a real
server:

- **Descriptions**: the paths tried when no search answers (minisatip
  `:8080/desc.xml`, Fritz!Box `:49000/satipdesc.xml`, Tvheadend
  `:9981/satip_server/desc.xml`, Octopus NET `/octoserve/octonet.xml`),
  and what each writes in `X_SATIPCAP` (a combined tuner listed under two
  systems: the source's Tuners then caps them together).
- **Lists**: the Fritz!Box's `/dvb/m3u/` lists and their `avm=1` query on a
  current FRITZ!OS; Octopus NET's and Tvheadend's `X_SATIPM3U` answers.
- **Playback**: RTSP over TCP (interleaved) on each server - the Fritz!Box
  in particular; mpv taking `rtsp-transport` as a per-file option on the
  mpv versions in the packages; which servers stream the same query over
  HTTP, and on which port (8875 here unless the source says; minisatip's is
  8080); a cast of a SAT>IP channel (a TV cannot play RTSP - HTTP is what a
  TV can fetch); ffmpeg recording over RTSP with `-rtsp_transport`.
- **Tuners**: whether a server shares a tuner between two streams of one
  transponder (counted as two here), and what other devices take from it
  (not known to the app - RTSP DESCRIBE on `rtsp://server/` lists the
  server's sessions on some servers and could tell it).

**Other kinds of streams** (yt-dlp's sites and radio-browser.info are in:
`core/ytdlp.py`, `ui/mw_webstreams.py`, `providers/radio_browser.py`,
`ui/mw_radio.py`)

- **yt-dlp's subtitles and chapters** where the app resolves the address
  itself (a libmpv without the ytdl hook - the Flatpak's): today one
  stream with picture and sound, no separate audio, no subtitles.

**On the TV, from the sofa**

- **A 10-foot mode**: in - TV mode (`ui/mw_tv.py`, `ui/tv_mode.py`), run
  with keys (a remote that acts as a keyboard), a game controller (SDL2
  through pysdl2 when it is installed - an optional `pip install pysdl2
  pysdl2-dll`, never in requirements.txt - else evdev) and LIRC (lircd's
  socket). Still open: native HDMI-CEC (libcec's cec-client, or a kernel
  CEC adapter's keymap, turns the TV remote into keys today), a channel
  list over the picture, the on-screen keyboard beyond Latin letters.
- **dopeIPTV as an HDHomeRun tuner**: built with server mode (above).
- **Android TV and Fire TV**: PyQt6 does not get there in practice; an
  app of its own would be a separate project. Until then the shared
  playlist and guide, the remote API and the HDHomeRun emulation let apps
  on the TV use dopeIPTV as their source.

### For the help, when this batch is done

Written once, when everything in the batch is in (GitHub's CHANGELOG,
FEATURES and README; the website's help in every language):

- **Devices on another part of the network (VLANs).** A search does not
  cross from one VLAN to another unless the router passes it on: an mDNS
  reflector (Avahi's reflector; "Multicast DNS" on UniFi) for Chromecasts
  and Kodi, an SSDP relay or UPnP proxy for DLNA TVs, an IGMP proxy for
  multicast TV - and the router must let traffic through between them.
  Without that: add the device by its address in the cast window, or a
  box by its address in Add source, or list the other networks under Nerd
  › Network › Other networks. The README's Troubleshooting has the short
  version.

- **An Apple TV from Linux or Windows.** AirPlay is macOS only (Apple's
  frameworks; tvOS 18 shut out other senders' video). Elsewhere: cast to a
  Chromecast, a DLNA TV or Kodi - or let an IPTV app on the Apple TV (VLC,
  iPlayTV and the like) read dopeIPTV's shared playlist and guide, and
  Plex or Jellyfin find it as an HDHomeRun tuner (server mode).

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
