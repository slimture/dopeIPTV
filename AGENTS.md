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

**Three rules above all others:** stability first - never break video
playback; Linux is the primary platform and a change for macOS or
Windows must not make Linux worse; run the quality gate yourself before
you say something works.

## Module responsibilities

Modules are grouped into layered subpackages (`providers/`, `core/`,
`media/`, `services/`, `ui/`).  See `docs/ARCHITECTURE.md` for the full
map and the one-way import rule.

| Module | What it owns |
|--------|--------------|
| `app.py` | Entry point, QApplication, login loop |
| `i18n.py` | English source strings, `set_language`, `tr()` (other 26 languages in `locale/*.json`) |
| `providers/client.py` | Xtream Codes API client |
| `providers/stalker.py` | Stalker / Ministra portal client (MAC portals: handshake, create_link) |
| `providers/boxes.py` | `BoxClient`: what boxes on the home network share (login, catalogue, stable ids, recordings as films, the box's guide as XMLTV via `own_guide`) |
| `providers/enigma2.py` | Enigma2 receivers (VU+, Dreambox, Zgemma...) over the classic `/web/` API of OpenWebif and the Dreambox WebInterface |
| `providers/tvheadend.py` | Tvheadend servers (channel tags, stream profiles, XMLTV, DVR recordings, digest login) |
| `providers/hdhomerun.py` | HDHomeRun tuners and their emulators (discover/lineup, SiliconDust guide, UDP discovery) |
| `providers/lan_scan.py` | "Find on my network": HDHomeRun broadcast plus a probe of the local /24 |
| `providers/epg.py` | EPG/XMLTV parser, cache and compact index |
| `providers/epg_index.py` | The guide parser as a process of its own (`--epg-index`) |
| `providers/metadata.py` | TMDB client, PosterResolver |
| `providers/trakt.py` | Trakt.tv sync |
| `providers/chromecast.py` | Chromecast support |
| `providers/dlna.py` | DLNA / UPnP TVs (discovery, control, local file share) |
| `providers/casting.py` | `CastHub`: Chromecast and DLNA behind one object |
| `providers/cast_bridge.py` | Re-encodes audio a Chromecast refuses (E-AC-3) so the stream still casts |
| `providers/opensubtitles.py` | OpenSubtitles.com client (search, download, file hash) |
| `core/stores.py` | Data persistence layer |
| `core/workers.py` | Thread pool helpers, LogoLoader, image caches |
| `core/local_buffer.py` | The seekable window of a live channel's rewind buffer (DVR row) |
| `core/dvr_store.py` | The app-kept rewind buffer's rolling segment store (retention, readers) |
| `core/dvr_ingest.py` | Its one provider connection (reconnects as runs, gives up cleanly) |
| `core/dvr_server.py` | Its loopback server mpv plays from (token, live TS) |
| `core/dvr_session.py` | `DvrSession`: ingest, store and server for one live channel |
| `core/dvr_pool.py` | `DvrPool`: every channel's buffer this session (frozen history, one space budget) |
| `core/dvr_record.py` | `BufferRecorder`: a recording read from a buffer, no connection of its own |
| `core/recording.py` | Recording manager |
| `core/updates.py` | GitHub latest-release check |
| `core/selfupdate.py` | AppImage in-place update (download, checksum, self-check, swap); every update's signed checksum check (`signed_asset`) |
| `core/winupdate.py` / `core/macupdate.py` | The same for an installed Windows copy (the installer) and the Mac app (the .dmg) |
| `core/ed25519.py` | Ed25519 signatures in plain Python (the release key) |
| `core/update_keys.py` | The public half of dopeIPTV's release signing key (`tools/make_update_key.py`, `tools/sign_sums.py`) |
| `core/wakelock.py` | Screensaver inhibitor |
| `core/gcguard.py` | Cyclic garbage collection on the GUI thread only |
| `core/contest.py` | `ContestWatch`: tells another device taking the account's connection from repeated drops |
| `core/desktop_entry.py` | The user's .desktop entry and icons (Wayland takes the window icon from there) |
| `core/xdg.py` | Launch system programs from a frozen bundle without our bundled libraries |
| `core/audiotags.py` | Artist/album/title and cover art from music files, header-only |
| `services/mpris.py` | MPRIS media session on the session bus (Linux) |
| `services/notify.py` | Desktop notifications over D-Bus (Linux) |
| `services/popular.py` | Popular right now: TMDB's weekly chart matched to the provider's catalogue |
| `core/log.py` | Central logging (`configure_logging`, `DOPEIPTV_LOG`) |
| `core/nerd.py` | Nerd settings into what the player and network do (mpv options, user agent/headers/proxy per source, IP version, VPN guard); nothing while Nerd is off |
| `core/nerd_lists.py` | Nerd on a source's lists: local picon folder, a channel's guide chosen by hand, udpxy for multicast |
| `core/http_login.py` | `get_with_login`: a login in a stream's address answered as the server asks (basic or digest) |
| `core/commands.py` | What the app is asked from outside: `--play`, `--channel`, `dopeiptv://` links |
| `core/instance.py` | A second start hands its command to the copy running (local socket) |
| `core/platform_macos.py` | macOS-specific helpers |
| `core/platform_windows.py` | Windows-specific helpers |
| `media/embedded.py` | Embedded mpv player widget |
| `media/players.py` | External player launch, libmpv detection |
| `media/dvr_player.py` | The player's side of the app-kept buffer (`DvrPlayerMixin`) |
| `media/rewind_bar.py` | The DVR row and timeshift timeline over the video, and their switch (`RewindBarMixin`) |
| `media/seek_slider.py` | The seek bar slider (`_SeekSlider`): click, drag, hover time |
| `media/video_surface.py` | The video surface mpv draws into (`_MpvGLWidget`) and the Linux pop-out's raster mirror |
| `media/subtitle_text.py` | Stray right-to-left marks out of subtitle files before mpv reads them |
| `ui/main_window.py` | MainWindow composition root (+ `mw_*` mixins) |
| `ui/channel_list.py` | List model, view, delegate painting |
| `ui/dialogs.py` | Dialog windows |
| `ui/settings_ui.py` | Settings building blocks: sidebar shell, search view, pages, sections (`card`), rows, switches, steppers, chips |
| `ui/qt_i18n.py` | Qt's own strings and QLocale dates in the app's language |
| `ui/home_widgets.py` | Home's featured strip, shelves and cards (pure presentation) |
| `ui/tmdb_match.py` | Manual TMDB match dialog |
| `ui/subtitle_search.py` | OpenSubtitles search dialog |
| `ui/widgets.py` | Small standalone widgets |
| `ui/box_finder.py` | Box kinds in the add-a-source forms and the "Find on my network" button |
| `ui/nerd_settings.py` | The Nerd page in Settings (shown only once Nerd is turned on) and the diagnostics text |
| `ui/source_nerd.py` | A source's own Nerd options in the Add/Edit source dialog |
| `ui/source_presets.py` | Presets in the source dialog (Fritz!Box, Channels DVR, Threadfin, xTeVe, Dispatcharr, ErsatzTV) |
| `ui/mw_box.py` | The box from the app: zap, wake, standby, record on the box from the guide; Nerd's copy address and guide channel |
| `ui/mw_commands.py` | Commands from outside carried out: play an address or file, a channel by name, come forward |
| `ui/mw_home.py` | The Home page: which shelves, what goes in them (drawn by `home_widgets.py`) |
| `ui/mw_local.py` | Local files: folders, library view, music queue |
| `ui/mw_multiview.py` | Multiview: 2/4/6/9 live streams, an mpv per cell |
| `ui/mw_donate.py` | Donating from the app: the sidebar heart, About's link, Home's one card after real use |
| `ui/epg_grid.py` | The guide's timeline grid |
| `ui/epg_search.py` | Programme search across the whole guide |
| `ui/welcome.py` | First-run welcome overlay |
| `ui/category_flags.py` | Bundled SVG flags for category names (flag emoji, "SE \| …" prefixes) |
| `ui/player_resize.py` | The handle under the docked video that sets its height |
| `ui/theme.py` | Theming engine |

## Working with individual modules

`ui/main_window.py` is a thin composition root: the real behaviour lives
in the `mw_*` mixins beside it, each mixed into `MainWindow`:
`mw_settings`, `mw_trakt`, `mw_recording`, `mw_context`, `mw_favorites`
(several favorites at once, move between folders, drag-and-drop), `mw_home`
(Home), `mw_local` (local files), `mw_multiview` (multiview), `mw_detail`,
`mw_search` (sidebar search), `mw_sidebar` (panel collapse / icon rail),
`mw_nav` (focus mode, nav colours/icons, Library group), `mw_shortcuts`
(keyboard shortcuts), `mw_busy` (loading indicator), `mw_updates`
(update badge), `mw_reminders`, `mw_sort`, `mw_onboarding`,
`mw_mpris` (desktop media controls, media keys), `mw_follow` (followed
series, new-episode news), `mw_subtitles` (OpenSubtitles, subtitles
remembered per title), `mw_cast` (Chromecast and DLNA: the cast
dialog, the strip, handing over from the archive), `mw_donate` (the
sidebar heart, About's link, Home's card) and `mw_popout`
(detached-window player). What stays in `main_window.py` is
the `__init__`/`_build_ui` composition and the playback path (start/stop,
seek, fullscreen, reconnect) - keep playback code there.
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
  epg_index, metadata, diagnostics, oauth_loopback, opensubtitles, dlna, stalker, boxes, enigma2, tvheadend, hdhomerun, lan_scan), `core/` (stores, local_buffer, dvr_store/ingest/server/session/pool/record, updates, log,
  player_exec, wakelock, selfupdate, winupdate, macupdate, ed25519, update_keys, gcguard, contest, nerd, nerd_lists, http_login, commands, instance) and `services/` (resume,
  coverart, reminders, mpris, notify, popular) and `media/subtitle_text.py`; see `[tool.mypy] files` in `pyproject.toml`. The Qt UI is deliberately out of
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
