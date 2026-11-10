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
| `website` | The site at <https://iptv.dope.rs> (static, rendered by `jq`). | website changes |

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
- Nothing is code-signed: macOS builds are not notarized (users allow the
  first start under Privacy & Security), Windows builds are unsigned
  (SmartScreen "Run anyway"). Both cost money every year; the help page
  and README explain the workaround.

## Cutting a release

1. On `testing`, set the new version in **all five places** -
   `tests/test_version.py` fails the build if they drift:
   - `dopeiptv/__init__.py` (`__version__`)
   - `pyproject.toml` (`version`)
   - `packaging/io.github.slimture.dopeIPTV.metainfo.xml` - a new
     `<release version="x.y.z" date="YYYY-MM-DD">` at the top (software
     catalogs read the version there)
   - `CHANGELOG.md` - a `## [x.y.z]` section (developer detail)
   - `RELEASE_NOTES.md` - a `## dopeIPTV x.y.z` section at the top (for
     users; it becomes the GitHub release text and the website's "What's
     new")
2. Push `testing`; wait for CI to be green (and ideally try the test build).
3. Run **`tools/release.sh x.y.z`**. It never trusts a local branch: it
   fetches, merges `origin/testing` into `main`, checks that all five
   places agree, pushes `main`, and tags `vx.y.z`. Safe to re-run.
4. Watch `release.yml`. Re-run a failed leg if it was a download hiccup.
5. The website picks up the new release by itself within 15 minutes.

Write release notes for people who use the app: what they can now do and
where to find it, in plain words. The changelog can be technical.

## Keys, accounts and secrets

Only names here - the values are with the owner.

| What | Where it is used | Without it |
|---|---|---|
| GitHub Actions secret `DOPEIPTV_TMDB_KEY` | written into `dopeiptv/_tmdb_key.py` (git-ignored) by every release/nightly build | builds have no bundled TMDB key: posters need the user's own key, Home's popular shelves stay empty |
| GitHub Actions secret `DOPEIPTV_OPENSUBTITLES_KEY` | bundled the same way for subtitle search | users must enter their own free OpenSubtitles key |
| Trakt app registration | its client id is `DEFAULT_CLIENT_ID` in `providers/trakt.py`; the redirect goes through the website (see its README) | users can enter their own client id in Settings → Trakt |
| The web server | runs the site and its cron sync (website branch: `README.md`, `DEPLOY.md`) | the site stops updating; the app is unaffected |
| GitHub repository settings | releases, Actions, the Security tab for private reports | - |
| Stripe Payment Link (donations) | `$DONATE` at the top of `site/render.jq` on the `website` branch - where the site's donate links point without JavaScript. `.github/FUNDING.yml` (the Sponsor button, shown with Settings → Features → Sponsorships ticked) and the README's "Support dopeIPTV" point at `iptv.dope.rs/#donate`, which opens the dialog | no donate card on the site |
| Stripe keys for the donate dialog | a restricted key (PaymentIntents: Write) and the publishable key, on the web server in `/usr/local/etc/iptv-donate.key` / `.conf`, read by `donate/donate.py` (website branch, `DEPLOY.md` "Donations"); Apple Pay needs the domain added in Stripe | the dialog sends visitors to the Payment Link's own page instead |
| The admin page's password | `/usr/local/etc/nginx/iptv-admin.htpasswd` on the web server (website branch, `DEPLOY.md` "Statistics and the admin page") | `/admin/` cannot be opened; the site is unaffected |

A source checkout works without any of these. For local testing, set
`DOPEIPTV_TMDB_KEY` / `DOPEIPTV_OPENSUBTITLES_KEY` in the environment.

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
- **AirPlay** was tried and parked (tvOS broke the API third-party senders
  use) - `docs/DEVELOPER_NOTES.md` says how to re-check.
- macOS notarization and Windows code signing (see above).
- On the website, a few Serbian strings are in Cyrillic while the rest of
  the Serbian page is Latin.

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
