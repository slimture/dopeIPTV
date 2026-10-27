# AI Agent Guide

This document helps AI coding assistants (Aider, Cursor, Copilot, etc.)
work effectively with the dopeIPTV codebase.  Each module is kept small
enough to fit in a 14-16B model's context window.

## Module responsibilities

Modules are grouped into layered subpackages (`providers/`, `core/`,
`media/`, `services/`, `ui/`).  See `docs/ARCHITECTURE.md` for the full
map and the one-way import rule.

| Module | What it owns |
|--------|--------------|
| `app.py` | Entry point, QApplication, login loop |
| `i18n.py` | English source strings, `set_language`, `tr()` (other 26 languages in `locale/*.json`) |
| `providers/client.py` | Xtream Codes API client |
| `providers/epg.py` | EPG/XMLTV parser, cache and compact index |
| `providers/epg_index.py` | The guide parser as a process of its own (`--epg-index`) |
| `providers/metadata.py` | TMDB client, PosterResolver |
| `providers/trakt.py` | Trakt.tv sync |
| `providers/chromecast.py` | Chromecast support |
| `providers/dlna.py` | DLNA / UPnP TVs (discovery, control, local file share) |
| `providers/casting.py` | `CastHub`: Chromecast and DLNA behind one object |
| `providers/opensubtitles.py` | OpenSubtitles.com client (search, download, file hash) |
| `core/stores.py` | Data persistence layer |
| `core/workers.py` | Thread pool helpers, LogoLoader, image caches |
| `core/recording.py` | Recording manager |
| `core/updates.py` | GitHub latest-release check |
| `core/selfupdate.py` | AppImage in-place update (download, checksum, self-check, swap) |
| `core/wakelock.py` | Screensaver inhibitor |
| `core/gcguard.py` | Cyclic garbage collection on the GUI thread only |
| `services/mpris.py` | MPRIS media session on the session bus (Linux) |
| `services/notify.py` | Desktop notifications over D-Bus (Linux) |
| `services/popular.py` | Popular right now: TMDB's weekly chart matched to the provider's catalogue |
| `core/log.py` | Central logging (`configure_logging`, `DOPEIPTV_LOG`) |
| `core/platform_macos.py` | macOS-specific helpers |
| `core/platform_windows.py` | Windows-specific helpers |
| `media/embedded.py` | Embedded mpv player widget |
| `media/players.py` | External player launch, libmpv detection |
| `media/subtitle_text.py` | Stray right-to-left marks out of subtitle files before mpv reads them |
| `ui/main_window.py` | MainWindow composition root (+ `mw_*` mixins) |
| `ui/channel_list.py` | List model, view, delegate painting |
| `ui/dialogs.py` | Dialog windows |
| `ui/settings_ui.py` | Settings building blocks: sidebar shell, pages, cards, rows, switches |
| `ui/qt_i18n.py` | Qt's own strings and QLocale dates in the app's language |
| `ui/tmdb_match.py` | Manual TMDB match dialog |
| `ui/subtitle_search.py` | OpenSubtitles search dialog |
| `ui/widgets.py` | Small standalone widgets |
| `ui/theme.py` | Theming engine |

## Working with individual modules

`ui/main_window.py` is a thin composition root: the real behaviour lives
in the `mw_*` mixins beside it, each mixed into `MainWindow`:
`mw_settings`, `mw_trakt`, `mw_recording`, `mw_context`, `mw_favorites`
(several favorites at once, move between folders, drag-and-drop), `mw_detail`,
`mw_search` (sidebar search), `mw_sidebar` (panel collapse / icon rail),
`mw_nav` (focus mode, nav colours/icons, Library group), `mw_shortcuts`
(keyboard shortcuts), `mw_busy` (loading indicator), `mw_updates`
(update badge), `mw_reminders`, `mw_sort`, `mw_onboarding`,
`mw_mpris` (desktop media controls, media keys), `mw_follow` (followed
series, new-episode news), `mw_subtitles` (OpenSubtitles, subtitles
remembered per title) and `mw_popout` (detached-window player). What stays in `main_window.py` is
the `__init__`/`_build_ui` composition and the playback path (start/stop,
seek, fullscreen, reconnect) - keep playback code there.
When making changes:

- **UI changes**: find the method by name across `ui/main_window.py` and
  the `mw_*` mixins (e.g. `open_settings` lives in `mw_settings.py`).
  A mixin method resolves on `MainWindow` via MRO, so a pure move between
  them never changes behaviour.
- **Playback**: `media/embedded.py` for the in-app player,
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
automatically.

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
  epg_index, metadata, diagnostics, oauth_loopback, opensubtitles, dlna), `core/` (stores, updates, log,
  player_exec, wakelock, selfupdate, gcguard) and `services/` (resume,
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
